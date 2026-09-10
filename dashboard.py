"""
Thin web shell around the deterministic CLI pipeline.

    upload a corrected book PDF  ->  run extraction  ->  download
    final_export.zip

The extraction core (qbank/) is untouched: this dashboard only
transports files in and out and shells out to the same functions the
CLI uses (run_book -> build_final_zip). Zero LLM, deterministic —
the web layer adds no logic of its own.

    python dashboard.py            # binds 0.0.0.0:$PORT (default 8000)
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file
from werkzeug.utils import secure_filename

from qbank import config
from qbank import state as state_mod
from qbank import audit as audit_mod
from qbank import review as review_mod
from qbank.export import build_final_zip, gate_final_zip
from qbank.run import run_book
from review_dashboard.gen import gen_crops

HERE = Path(__file__).resolve().parent

PDF_DIR = Path(os.environ.get("QBANK_PDFS_DIR",
                              str(config.REPO_ROOT / "pdfs")))
PDF_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 400 * 1024 * 1024

app = Flask(__name__)

_jobs: dict[str, dict] = {}          # subject -> job record
_jobs_lock = threading.Lock()
_pipeline_running = threading.Lock()  # one book at a time


# --------------------------------------------------------------------- api

class _Tee(io.StringIO):
    """Collect run_book's stdout into the job log."""

    def __init__(self, job: dict):
        super().__init__()
        self._job = job

    def write(self, s):
        for line in str(s).splitlines():
            self._job["log"].append(line)
        return len(s)


def _register_book(subject: str, filename: str, note: str = "") -> None:
    books = config.load_books()
    books[subject] = {
        "subject": subject,
        "path": filename,               # resolved via QBANK_PDFS_DIR / pdfs/
        "page_offset": "auto",
        "note": note or f"uploaded {time.strftime('%Y-%m-%d %H:%M')}",
    }
    tmp = config.BOOKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(books, indent=2))
    tmp.replace(config.BOOKS_FILE)


@app.post("/api/upload")
def api_upload():
    f = request.files.get("pdf")
    if f is None or not f.filename:
        return jsonify(ok=False, error="no file field 'pdf'"), 400
    name = secure_filename(f.filename)
    if not name.lower().endswith(".pdf"):
        return jsonify(ok=False, error="only .pdf files"), 400
    subject = (request.form.get("subject") or "").strip().upper()
    if not subject:
        subject = re.sub(r"[^A-Z0-9]", "", name.split(".")[0].upper())[:8]
    if not re.fullmatch(r"[A-Z0-9]{2,8}", subject):
        return jsonify(ok=False,
                       error="subject must be 2-8 chars [A-Z0-9]"), 400

    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify(ok=False, error="file too large"), 413
    if data[:5] != b"%PDF-":
        return jsonify(ok=False, error="not a PDF (bad header)"), 400
    (PDF_DIR / name).write_bytes(data)
    _register_book(subject, name)
    return jsonify(ok=True, subject=subject, file=name,
                   bytes=len(data), pdf_dir=str(PDF_DIR))


def _drive_url(url: str) -> str:
    """Google Drive share links can't be GET directly — map
    /file/d/<id>/... (and open?id=...) to the export endpoint."""
    m = re.search(r"/file/d/([A-Za-z0-9_-]{10,})", url) \
        or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", url)
    if m and "drive.google.com" in url:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def _save_pdf(data: bytes, name: str, subject: str, note: str):
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify(ok=False, error="file too large"), 413
    if data[:5] != b"%PDF-":
        return jsonify(ok=False,
                       error="not a PDF (bad header) — Drive link sahi "
                             "hai? (file public/shared honi chahiye)"), 400
    (PDF_DIR / name).write_bytes(data)
    _register_book(subject, name, note=note)
    return jsonify(ok=True, subject=subject, file=name, bytes=len(data))


@app.post("/api/fetch")
def api_fetch():
    """Book by link (user workflow): Drive/direct URL -> PDF_DIR."""
    b = request.json or {}
    url = (b.get("url") or "").strip()
    subject = (b.get("subject") or "").strip().upper()
    if not url.startswith(("http://", "https://")):
        return jsonify(ok=False, error="need an http(s) url"), 400
    if not re.fullmatch(r"[A-Z0-9]{2,8}", subject):
        return jsonify(ok=False,
                       error="subject must be 2-8 chars [A-Z0-9]"), 400
    url = _drive_url(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (qbank dashboard)"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            data = r.read(MAX_UPLOAD_BYTES + 1)
    except Exception as exc:                           # noqa: BLE001
        return jsonify(ok=False, error=f"download failed: {exc}"), 502
    raw = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    name = urllib.parse.unquote(raw)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or f"{subject}.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{subject}.pdf"
    return _save_pdf(data, name, subject,
                     note=f"fetched from link {time.strftime('%Y-%m-%d')}")


def _job_runner(subject: str, force: bool):
    job = _jobs[subject]
    with _pipeline_running:
        try:
            entry = config.load_books().get(subject)
            if not entry:
                raise ValueError(f"{subject} not registered — upload first")
            pdf = config.resolve_book_path(entry)
            offset = entry.get("page_offset", "auto")
            with redirect_stdout(_Tee(job)):
                res = run_book(pdf, subject, page_offset=offset,
                               force=force)
                job["log"].append("")
                job["log"].append("=== export ===")
                out = build_final_zip(config.OUTPUT_ROOT)
            job["log"].append("")
            job["log"].append("=== review assets ===")
            try:
                cr = gen_crops(subject, pdf, config.OUTPUT_ROOT)
                job["log"].append(
                    f"review crops: {cr['items']} REVIEW table(s), "
                    f"{cr['pages_rendered']} page(s) rendered")
            except Exception as exc:                 # noqa: BLE001
                job["log"].append(f"crops skipped: {exc}")
            try:
                au = audit_mod.audit_book(config.OUTPUT_ROOT,
                                          subject=subject)
                audit_mod.write_report(config.OUTPUT_ROOT, au)
                kinds = ", ".join(f"{k}={v}"
                                  for k, v in sorted(au["by_kind"].items()))
                job["log"].append(
                    f"audit: {au['rows_scanned']} rows scanned, "
                    f"flags: {kinds or 'none'}")
            except Exception as exc:                 # noqa: BLE001
                job["log"].append(f"audit skipped: {exc}")
            if not out["ok"]:
                job["status"] = "error"
                job["error"] = f"export REFUSED: {out['why']}"
            else:
                job["status"] = "done"
                job["result"] = {
                    "chapters_run": res["chapters_run"],
                    "total_questions": res["total_questions"],
                    "census_failures": res["census_failures"],
                    "zip": out["path"],
                    "receipt": out["receipt"],
                }
        except Exception as exc:                      # noqa: BLE001
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            job["finished"] = time.time()


@app.post("/api/run/<subject>")
def api_run(subject: str):
    subject = subject.upper()
    if _pipeline_running.locked():
        return jsonify(ok=False, error="another book is running"), 409
    force = bool((request.json or {}).get("force", False))
    with _jobs_lock:
        job = {"subject": subject, "status": "running", "log": [],
               "started": time.time(), "finished": None,
               "error": None, "result": None}
        _jobs[subject] = job
    threading.Thread(target=_job_runner, args=(subject, force),
                     daemon=True).start()
    return jsonify(ok=True, subject=subject, force=force)


@app.get("/api/log/<subject>")
def api_log(subject: str):
    job = _jobs.get(subject.upper())
    if not job:
        return Response("", mimetype="text/plain")
    body = "\n".join(job["log"][-400:])
    if job["status"] == "error" and job["error"]:
        body += f"\n\nERROR: {job['error']}"
    return Response(body, mimetype="text/plain")


@app.get("/api/status")
def api_status():
    books = config.load_books()
    state = state_mod.load_state().get("pdf_progress", {})
    rows = []
    for subj, entry in sorted(books.items()):
        try:
            pdf = config.resolve_book_path(entry)
            size = Path(pdf).stat().st_size
            found = Path(pdf).name
        except FileNotFoundError:
            size, found = 0, None
        rows.append({
            "subject": subj,
            "file": found or entry.get("path"),
            "bytes": size,
            "chapters_done": len(state.get(subj, {}).get("chapters_done", [])),
            "job": (_jobs.get(subj, {}).get("status")),
        })
    gate = gate_final_zip(config.OUTPUT_ROOT)
    zp = config.OUTPUT_ROOT / "final_export.zip"
    zinfo, receipt = None, None
    if zp.exists():
        subs = sorted(p.name for p in config.SUBJECTS_DIR.iterdir()
                      if p.is_dir()) if config.SUBJECTS_DIR.is_dir() else []
        zinfo = {"bytes": zp.stat().st_size,
                 "name": ("_".join(subs) + "_final_export.zip") if subs
                         else "final_export.zip",
                 "mtime": time.strftime(
                     "%Y-%m-%d %H:%M:%S",
                     time.localtime(zp.stat().st_mtime))}
        try:
            with zipfile.ZipFile(zp) as z:
                receipt = json.loads(z.read("REVIEW_RECEIPT.json"))
        except Exception:                              # noqa: BLE001
            pass
    return jsonify(books=rows, gate=gate, zip=zinfo, receipt=receipt,
                   running=any(j["status"] == "running"
                               for j in _jobs.values()),
                   jobs={s: {"status": j["status"], "error": j["error"]}
                         for s, j in _jobs.items()})


@app.get("/download")
def download():
    zp = config.OUTPUT_ROOT / "final_export.zip"
    if not zp.exists():
        return jsonify(ok=False, error="no export yet — run a book"), 404
    subs = sorted(p.name for p in config.SUBJECTS_DIR.iterdir()
                  if p.is_dir()) if config.SUBJECTS_DIR.is_dir() else []
    name = ("_".join(subs) + "_final_export.zip") if subs \
        else "final_export.zip"
    return send_file(zp, as_attachment=True, download_name=name)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


# ------------------------------------------------------- review layer

@app.get("/review")
def review_page():
    """The human review dashboard (edit + approve REVIEW tables)."""
    return Response((HERE / "review_dashboard" / "index.html").read_text(),
                    mimetype="text/html")


@app.get("/api/queue")
def api_queue():
    return jsonify(review_mod.review_tables(config.OUTPUT_ROOT))


@app.get("/api/audit")
def api_audit():
    rep = config.OUTPUT_ROOT / "data" / "audit_report.jsonl"
    rows = []
    if rep.exists():
        for line in rep.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return jsonify(rows)


@app.post("/api/decision")
def api_decision():
    b = request.json or {}
    try:
        row = review_mod.record_decision(
            config.OUTPUT_ROOT, b["book"], b["q_id"], b["table_id"],
            b.get("action", "approve"), b.get("note", ""))
    except KeyError as exc:
        return jsonify(ok=False, error=f"missing field {exc}"), 400
    return jsonify(row)


@app.post("/api/edit")
def api_edit():
    b = request.json or {}
    try:
        res = review_mod.apply_table_edit(
            config.OUTPUT_ROOT, b["book"], b["q_id"], b["table_id"],
            b.get("markdown", ""))
    except KeyError as exc:
        return jsonify(ok=False, error=f"missing field {exc}"), 400
    if res.get("ok") and b.get("action"):
        review_mod.record_decision(config.OUTPUT_ROOT, b["book"],
                                   b["q_id"], b["table_id"], b["action"],
                                   "saved via edit")
    return jsonify(res)


@app.get("/zip/<book>")
def zip_for_book(book: str):
    """Review-dashboard zip link: the (review-gated) final export."""
    return download()


@app.get("/crops/<name>")
def crops(name: str):
    if ".." in name:
        return jsonify(ok=False, error="bad name"), 400
    f = config.OUTPUT_ROOT / "crops" / name
    if not f.is_file():
        f = HERE / "review_dashboard" / "crops" / name
    if not f.is_file():
        return jsonify(ok=False, error="no such crop"), 404
    return send_file(f, mimetype="image/png")


# ---------------------------------------------------------------------- ui

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jdon Extract v2</title>
<style>
 :root{--bg:#0d1117;--card:#161b22;--line:#30363d;--tx:#e6edf3;
       --dim:#8b949e;--ok:#3fb950;--err:#f85149;--ac:#58a6ff}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--tx);
      font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
 .wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:22px;margin:0 0 4px}
 .sub{color:var(--dim);margin:0 0 26px;font-size:13px}
 .card{background:var(--card);border:1px solid var(--line);
       border-radius:10px;padding:18px 20px;margin-bottom:18px}
 .card h2{font-size:14px;margin:0 0 12px;color:var(--ac);
          text-transform:uppercase;letter-spacing:.06em}
 table{width:100%;border-collapse:collapse;font-size:14px}
 td,th{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}
 th{color:var(--dim);font-weight:600;font-size:12px}
 button{background:#238636;color:#fff;border:0;border-radius:6px;
        padding:8px 16px;font-size:14px;cursor:pointer}
 button.sec{background:#21262d;border:1px solid var(--line);color:var(--tx)}
 button:disabled{opacity:.5;cursor:not-allowed}
 input[type=text]{background:#0d1117;border:1px solid var(--line);
        color:var(--tx);border-radius:6px;padding:8px 10px;width:120px}
 .badge{display:inline-block;padding:2px 9px;border-radius:20px;
        font-size:12px;font-weight:600}
 .b-ok{background:#12261e;color:var(--ok)}
 .b-run{background:#0c2d6b;color:var(--ac)}
 .b-err{background:#2d1214;color:var(--err)}
 .b-idle{background:#21262d;color:var(--dim)}
 pre{background:#0d1117;border:1px solid var(--line);border-radius:8px;
     padding:12px;height:280px;overflow:auto;font-size:12px;
     white-space:pre-wrap;color:#a5d6ff}
 .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 .hint{color:var(--dim);font-size:12px}
 .big{font-size:15px}
</style></head><body><div class="wrap">
<h1>Jdon Extract <span style="color:var(--ac)">v2</span></h1>
<p class="sub">deterministic text-layer pipeline &middot; zero LLM &middot;
upload corrected ED8 PDF &rarr; extract &rarr; download final_export.zip</p>

<div class="row" style="margin-bottom:18px">
 <button class="sec big" onclick="location='/review'">&#129489;&#8205;&#9878;&#65039;
  Review Dashboard</button>
 <span class="hint">REVIEW-flagged tables edit/approve karo — final zip
  tab tak locked rehta hai</span>
</div>

<div class="card"><h2>1 &middot; Upload book PDF</h2>
 <div class="row">
  <input type="file" id="pdf" accept=".pdf">
  <input type="text" id="subject" placeholder="SUBJ e.g. OBG"
         maxlength="8" style="text-transform:uppercase">
  <button id="up" onclick="upload()">Upload</button>
  <span class="hint" id="upmsg"></span>
 </div>
 <div class="row" style="margin-top:10px">
  <input type="text" id="link" placeholder="ya book ka link (Drive/direct URL)"
         style="width:340px">
  <button class="sec" onclick="fetchLink()">Fetch link</button>
  <span class="hint" id="fmsg"></span>
 </div></div>

<div class="card"><h2>2 &middot; Books &amp; runs</h2>
 <table><thead><tr><th>Subject</th><th>File</th><th>Size</th>
 <th>Chapters done</th><th>Status</th><th></th></tr></thead>
 <tbody id="books"></tbody></table>
 <p class="hint" style="margin:10px 0 0">Re-run ignores resume state
 and rebuilds the book from scratch.</p></div>

<div class="card" id="logcard" style="display:none"><h2>Run log</h2>
 <pre id="log"></pre></div>

<div class="card"><h2>3 &middot; Output</h2>
 <div id="gate" class="big"></div>
 <div id="zip" style="margin-top:8px"></div></div>
</div>
<script>
let runSubj=null;
const $=id=>document.getElementById(id);
const mb=n=>(n/1048576).toFixed(1)+" MB";
document.getElementById('pdf').addEventListener('change',e=>{
 const n=e.target.files[0]?.name||"";
 if(n&&!$('subject').value)
   $('subject').value=n.split(/[._-]/)[0].replace(/[^a-zA-Z0-9]/g,'')
     .toUpperCase().slice(0,8);
});
async function upload(){
 const f=$('pdf').files[0]; if(!f){$('upmsg').textContent="pick a PDF";return}
 const fd=new FormData(); fd.append("pdf",f);
 fd.append("subject",$('subject').value);
 $('up').disabled=true; $('upmsg').textContent="uploading...";
 const r=await fetch("/api/upload",{method:"POST",body:fd}).then(r=>r.json());
 $('up').disabled=false;
 $('upmsg').textContent=r.ok?`registered as ${r.subject} (${mb(r.bytes)})`
                            :("error: "+r.error);
 if(r.ok)refresh();
}
async function fetchLink(){
 const url=$('link').value.trim();
 let subj=$('subject').value;
 if(!subj)subj=prompt("subject code? (2-8 chars, e.g. OBG)")||"";
 if(!url||!subj){$('fmsg').textContent="link + subject dono chahiye";return}
 $('fmsg').textContent="downloading...";
 const r=await fetch("/api/fetch",{method:"POST",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify({url,subject:subj})}).then(r=>r.json());
 $('fmsg').textContent=r.ok?`registered ${r.subject} (${mb(r.bytes)})`
                          :("error: "+r.error);
 if(r.ok)refresh();
}
async function run(s,force){
 const r=await fetch(`/api/run/${s}`,{method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({force})}).then(r=>r.json());
 if(!r.ok){alert(r.error);return}
 runSubj=s; $('logcard').style.display="block"; tick();
}
async function refresh(){
 const st=await fetch("/api/status").then(r=>r.json());
 $('books').innerHTML=st.books.map(b=>{
  const j=b.job;
  const badge=j==="running"?'<span class="badge b-run">RUNNING</span>'
   :j==="error"?'<span class="badge b-err">ERROR</span>'
   :j==="done"?'<span class="badge b-ok">DONE</span>'
   :'<span class="badge b-idle">idle</span>';
  return `<tr><td><b>${b.subject}</b></td>
   <td>${b.file??"<i>missing</i>"}</td><td>${b.bytes?mb(b.bytes):"—"}</td>
   <td>${b.chapters_done}</td><td>${badge}</td>
   <td><button ${st.running?"disabled":""}
     onclick="run('${b.subject}',false)">Run</button>
    <button class="sec" ${st.running?"disabled":""}
     onclick="run('${b.subject}',true)">Re-run</button></td></tr>`;
 }).join("")||'<tr><td colspan=6 class="hint">no books yet</td></tr>';
 const g=st.gate;
 $('gate').innerHTML=g.locked
  ? `<span class="badge b-err">GATE LOCKED</span> <span class="hint">${
      g.why??""}</span>
     <div class="row" style="margin-top:10px">
      <button onclick="location='/review'">&#129489;&#8205;&#9878;&#65039;
       Open Review Dashboard</button>
      <span class="hint">tables approve/edit karo, phir export khud
       unlock ho jayega</span></div>`
  : `<span class="badge b-ok">GATE OPEN</span> <span class="hint">${
      g.chapters} chapter(s) verified on disk</span>`;
 $('zip').innerHTML=st.zip
  ? `<div class="row"><button onclick="location='/download'">
      &#11015; Download ${st.zip.name || 'final_export.zip'}</button>
     <span class="hint">${mb(st.zip.bytes)} &middot; built ${st.zip.mtime}${
      st.receipt?` &middot; ${st.receipt.chapters} chapters &middot; ${
      JSON.stringify(st.receipt.shipped_qa_status_counts)}`:""}</span>
     </div>`
  : '<span class="hint">no export built yet</span>';
 if(runSubj&&st.jobs[runSubj]&&st.jobs[runSubj].status!=="running")tick();
}
async function tick(){
 if(!runSubj)return;
 $('log').textContent=await fetch(`/api/log/${runSubj}`).then(r=>r.text());
 $('log').scrollTop=1e9;
 const st=await fetch("/api/status").then(r=>r.json());
 if(st.jobs[runSubj]?.status==="running")setTimeout(tick,1200);
 else refresh();
}
refresh(); setInterval(()=>{if(!runSubj)refresh()},4000);
</script></body></html>
"""


@app.get("/")
def index():
    return Response(PAGE, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"dashboard on http://0.0.0.0:{port}  "
          f"(pdfs: {PDF_DIR}, output: {config.OUTPUT_ROOT})")
    app.run(host="0.0.0.0", port=port, threaded=True)

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
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file
from werkzeug.utils import secure_filename

from qbank import config
from qbank import state as state_mod
from qbank.export import build_final_zip, gate_final_zip
from qbank.run import run_book

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
        zinfo = {"bytes": zp.stat().st_size,
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
    return send_file(zp, as_attachment=True,
                     download_name="final_export.zip")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True)


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

<div class="card"><h2>1 &middot; Upload book PDF</h2>
 <div class="row">
  <input type="file" id="pdf" accept=".pdf">
  <input type="text" id="subject" placeholder="SUBJ e.g. OBG"
         maxlength="8" style="text-transform:uppercase">
  <button id="up" onclick="upload()">Upload</button>
  <span class="hint" id="upmsg"></span>
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
      g.why??""}</span>`
  : `<span class="badge b-ok">GATE OPEN</span> <span class="hint">${
      g.chapters} chapter(s) verified on disk</span>`;
 $('zip').innerHTML=st.zip
  ? `<div class="row"><button onclick="location='/download'">
      &#11015; Download final_export.zip</button>
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
    app.run(host="0.0.0.0", port=port)

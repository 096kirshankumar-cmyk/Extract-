"""Human review layer (adopted, compact, from the old Jdon review system):

  * append-only decision + edit ledgers on the output volume — refresh,
    redeploy or a new run loses nothing;
  * decisions are fingerprinted: if the row CONTENT changes after a
    decision (new run, model update) the decision goes STALE and the
    flag re-opens — safe direction only;
  * human table edits are written to EVERY copy of the row (questions
    and solutions jsonl) with read-back verification — "saved" is only
    reported when disk matches the submission;
  * the final zip stays locked while any REVIEW table is still
    UNDECIDED or STALE (override: QBANK_FORCE_EXPORT=1).

Deliberately NOT adopted from the old layer (not useful here): image
ownership ops, legacy flag-file union/watchdog, run-lock 409s.
"""
import hashlib
import json
import re
from pathlib import Path

DECISIONS = "review_decisions.jsonl"
EDIT_LEDGER = "human_edit_ledger.jsonl"


def _fp(text: str) -> str:
    return hashlib.md5((text or "").encode()).hexdigest()[:16]


def _read_jsonl(p: Path):
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _append_jsonl(p: Path, row: dict):
    with p.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def decision_key(book: str, q_id: str, table_id: str) -> str:
    return f"{book}|{q_id}|{table_id}"


def load_decisions(out_root: Path) -> dict:
    """key -> latest decision row."""
    out = {}
    for d in _read_jsonl(Path(out_root) / DECISIONS):
        out[d["key"]] = d
    return out


def review_tables(out_root: Path) -> list:
    """All REVIEW-flagged tables with live state:
    pending | decided:<action> | stale:<action>."""
    decs = load_decisions(out_root)
    items, seen = [], set()
    for qf in sorted((Path(out_root) / "split").glob("*/*/questions.jsonl")):
        book = qf.parent.parent.name
        for row in _read_jsonl(qf):
            for t in row.get("tables") or []:
                v = (t.get("validation") or {}).get("table_qa") or {}
                if v.get("status") != "REVIEW":
                    continue
                key = decision_key(book, row["q_id"], t["table_id"])
                if key in seen:
                    continue
                seen.add(key)
                d = decs.get(key)
                if d is None:
                    state = "pending"
                elif d.get("fp") != _fp(t["markdown"]):
                    state = "stale:" + d.get("action", "?")
                else:
                    state = "decided:" + d.get("action", "?")
                items.append({
                    "book": book, "q_id": row["q_id"],
                    "table_id": t["table_id"],
                    "suspects": v.get("suspect_fragments") or [],
                    "markdown": t["markdown"],
                    "pages": t["source_pages"],
                    "cross_page": bool(t.get("merged_continuation")),
                    "state": state,
                })
    return items


def record_decision(out_root: Path, book: str, q_id: str, table_id: str,
                    action: str, note: str = "") -> dict:
    """action: approve | reject (reopen). Fingerprinted to the CURRENT
    markdown so a later content change makes it stale."""
    md = None
    for it in review_tables(out_root):
        if decision_key(book, q_id, table_id) == \
                decision_key(it["book"], it["q_id"], it["table_id"]):
            md = it["markdown"]
            break
    row = {"key": decision_key(book, q_id, table_id), "action": action,
           "fp": _fp(md or ""), "note": note,
           "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")}
    _append_jsonl(Path(out_root) / DECISIONS, row)
    return row


def _md_shape(md: str):
    rows = [l for l in md.splitlines() if l.strip()]
    rows = [r for i, r in enumerate(rows)
            if not (i == 1 and re.fullmatch(r"[\s|:-]+", r))]
    shapes = {len(r.split("|")) for r in rows}
    return rows, shapes


def apply_table_edit(out_root: Path, book: str, q_id: str, table_id: str,
                     new_md: str) -> dict:
    """Replace the table markdown in EVERY copy (questions + solutions)
    with read-back verification. Refuses uneven markdown."""
    new_md = new_md.strip("\n")
    rows, shapes = _md_shape(new_md)
    if len(rows) < 2 or len(shapes) != 1:
        return {"ok": False, "why": "uneven or too-short markdown table"}
    touched = 0
    for nf in ("questions.jsonl", "solutions.jsonl"):
        for qf in sorted((Path(out_root) / "split" / book).glob(f"*/{nf}")):
            lines = qf.read_text().splitlines()
            changed = False
            for i, l in enumerate(lines):
                if not l.strip():
                    continue
                r = json.loads(l)
                if r.get("q_id") != q_id:
                    continue
                hit = False
                for t in r.get("tables") or []:
                    if t.get("table_id") == table_id:
                        t["markdown"] = new_md
                        hit = True
                        touched += 1
                if hit:
                    lines[i] = json.dumps(r, ensure_ascii=False)
                    changed = True
            if changed:
                qf.write_text("\n".join(lines) + "\n")
    # read-back verification — "saved" only when disk matches
    for nf in ("questions.jsonl",):
        for qf in sorted((Path(out_root) / "split" / book).glob(f"*/{nf}")):
            for r in _read_jsonl(qf):
                if r.get("q_id") != q_id:
                    continue
                for t in r.get("tables") or []:
                    if t.get("table_id") == table_id and \
                            t["markdown"] != new_md:
                        return {"ok": False, "why": "read-back mismatch"}
    if touched == 0:
        return {"ok": False, "why": "table not found"}
    _append_jsonl(Path(out_root) / EDIT_LEDGER,
                  {"key": decision_key(book, q_id, table_id),
                   "fp": _fp(new_md), "markdown": new_md,
                   "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")})
    return {"ok": True, "copies": touched}


def pending_count(out_root: Path) -> int:
    """REVIEW tables whose decision is missing or stale."""
    return sum(1 for it in review_tables(out_root)
               if it["state"] == "pending" or it["state"].startswith("stale"))


# ------------------------------------------------- question-level edits

Q_EDIT_FIELDS = ("question_text", "options", "solution_text",
                 "correct_option")


def find_question(out_root: Path, q_id: str) -> dict | None:
    """Any question row by q_id (case-insensitive) across all subjects:
    {book, q, answer, solution} — the shape the dashboard editor
    renders. None when the id is not on disk."""
    want = (q_id or "").strip().upper()
    split = Path(out_root) / "split"
    if not split.is_dir():
        return None
    for sub in sorted(split.iterdir()):
        if not sub.is_dir():
            continue
        for qf in sorted(sub.glob("*/questions.jsonl")):
            for r in _read_jsonl(qf):
                if (r.get("q_id") or "").upper() != want:
                    continue
                ch = qf.parent
                ans = next((x for x in _read_jsonl(ch / "answers.jsonl")
                            if (x.get("q_id") or "").upper() == want), None)
                sol = next((x for x in _read_jsonl(ch / "solutions.jsonl")
                            if (x.get("q_id") or "").upper() == want), None)
                return {"book": sub.name, "q": r, "answer": ans,
                        "solution": sol}
    return None


def apply_question_edit(out_root: Path, book: str, q_id: str,
                        patch: dict, note: str = "") -> dict:
    """Edit question_text / options(text only) / solution_text /
    correct_option of ONE q_id, in EVERY copy, with read-back
    verification (same contract as apply_table_edit). Option images
    and all other fields are preserved untouched. Ledger row kind=
    question_edit."""
    want = (q_id or "").strip().upper()
    patch = {k: v for k, v in (patch or {}).items()
             if k in Q_EDIT_FIELDS}
    if not patch:
        return {"ok": False, "why": "nothing editable in patch"}
    targets = []
    if "question_text" in patch or "options" in patch:
        targets.append(("questions.jsonl",
                        ("question_text", "options")))
    if "correct_option" in patch:
        targets.append(("answers.jsonl", ("correct_option",)))
    if "solution_text" in patch:
        targets.append(("solutions.jsonl", ("solution_text",)))

    def _opt_texts():
        return {str(o.get("id", "")).upper(): str(o.get("text", ""))
                for o in patch.get("options") or []}

    touched = 0
    for nf, fields in targets:
        for qf in sorted((Path(out_root) / "split" / book
                          ).glob(f"*/{nf}")):
            lines = qf.read_text().splitlines()
            changed = False
            for i, l in enumerate(lines):
                if not l.strip():
                    continue
                r = json.loads(l)
                if (r.get("q_id") or "").upper() != want:
                    continue
                for f in fields:
                    if f not in patch:
                        continue
                    if f == "options":
                        texts = _opt_texts()
                        r["options"] = [
                            {**o, "text": texts.get(
                                str(o.get("id", "")).upper(),
                                str(o.get("text", "")))}
                            for o in (r.get("options") or [])]
                    else:
                        r[f] = patch[f]
                lines[i] = json.dumps(r, ensure_ascii=False)
                changed = True
                touched += 1
            if changed:
                qf.write_text("\n".join(lines) + "\n")

    # read-back verification — saved only when disk matches
    for nf, fields in targets:
        for qf in sorted((Path(out_root) / "split" / book
                          ).glob(f"*/{nf}")):
            for r in _read_jsonl(qf):
                if (r.get("q_id") or "").upper() != want:
                    continue
                for f in fields:
                    if f not in patch:
                        continue
                    if f == "options":
                        got = [(str(o.get("id", "")).upper(),
                                str(o.get("text", "")))
                               for o in (r.get("options") or [])]
                        exp = sorted(_opt_texts().items())
                        if sorted(got) != exp:
                            return {"ok": False,
                                    "why": "read-back mismatch"}
                    elif r.get(f) != patch[f]:
                        return {"ok": False, "why": "read-back mismatch"}
    if touched == 0:
        return {"ok": False, "why": "question not found"}
    _append_jsonl(Path(out_root) / EDIT_LEDGER,
                  {"key": decision_key(book, q_id, "question"),
                   "kind": "question_edit",
                   "fields": sorted(patch),
                   "fp": _fp(json.dumps(patch, sort_keys=True,
                                        ensure_ascii=False)),
                   "note": note,
                   "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")})
    return {"ok": True, "copies": touched}

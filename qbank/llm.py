"""Optional Gemini-vision pass over ruled-table regions.

The deterministic geometry pipeline still OWNS table structure
(ruled boxes, columns, row bands, ordering). The model is asked only
to transcribe the cell texts of one rendered table image, and its
output is accepted under a hard fidelity envelope:

  * same row count and same cell count per row, else rejected;
  * per cell, the model text is accepted ONLY when it is identical to
    the deterministic cell after whitespace removal — the model may
    re-space ("fl ow" -> "flow", "Increasedpulmonary" ->
    "Increased pulmonary") but can never change, add or drop a
    character of content ("Tetrology" -> "Tetralogy" is REJECTED).

No API key, API error, bad JSON, or shape mismatch => the
deterministic output is returned unchanged. Responses are cached per
(pdf, page, box) so re-runs are deterministic and free.

Default remains zero-LLM: nothing is called unless GEMINI_API_KEY is
set in the environment (QBANK_LLM_MODEL overrides the model id,
QBANK_LLM_TABLES=0 disables explicitly).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

import pymupdf

API = ("https://generativelanguage.googleapis.com/v1beta/models/"
       "{model}:generateContent")
DEFAULT_MODEL = "gemini-3.5-flash-lite"

PROMPT = """You are a precision transcription engine for a medical
textbook table. The image shows ONE ruled table. Return ONLY valid
JSON, no markdown fences, of the form {"rows": [["cell", "..."], ...]}
— one inner list per table row, one string per visible column, in
reading order, header row first.

Transcribe every cell EXACTLY as printed:
- same words, same word order, same symbols and units (—, <, >, /, %,
  -, digits, abbreviations); empty cell = "";
- reconstruct words the typesetter broke across lines inside a cell
  ("fl" + "ow" = "flow", "Atri" + "al" = "Atrial",
   "abn" + "ormalities" = "abnormalities");
- insert the missing space where two separate words are glued
  ("Increasedpulmonary" = "Increased pulmonary",
   "notdepend" = "not depend", "damage,fetal" = "damage, fetal");
- DO NOT correct spellings, DO NOT normalise terminology, DO NOT
  translate, DO NOT add, remove or reorder any content.
A multi-line cell is ONE string with single spaces between its lines
(after the word reconstruction above)."""


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")) and \
        os.environ.get("QBANK_LLM_TABLES", "1") != "0"


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "").lower()


def _score(t: str, words, pairs) -> int:
    """Plausibility of one spacing of a cell: -1 (disqualified) if any
    alphabetic token is not a word the book contains at least twice.
    Single occurrences are excluded deliberately: the vocabulary is
    built from the raw layer, so a glued artifact printed in exactly
    one table cell ("rheniumThe", "tandemOne") is itself in it with
    count 1 — real words recur. Else the score is the count of
    adjacent word pairs the book prints with that spacing."""
    toks = [x.lower() for x in re.findall(r"[A-Za-z]{3,}", t)]
    for tok in toks:
        if words.get(tok, 0) < 2:
            return -1
    return sum(pairs.get(p, 0) for p in zip(toks, toks[1:]))


def _content(rows) -> str:
    """All cell text concatenated, whitespace removed, lowercased."""
    return re.sub(r"\s+", "", "".join(
        str(c) for r in rows for c in r)).lower()


def _tokruns(toks: list) -> list:
    """Alphabetic runs of a token list mapped onto the shared
    alpha-character stream (punctuation/space free, so both sides of
    a character-identical change are directly comparable). Each run
    also carries (original text, starts its token) for capitalisation
    checks."""
    runs, pos = [], 0
    for t in toks:
        first = True
        for m in re.finditer(r"[A-Za-z]+", t):
            g = m.group(0).lower()
            runs.append((pos, pos + len(g), g, m.group(0), first))
            pos += len(g)
            first = False
    return runs


def _block_ok(cdt: list, cmt: list, words) -> bool:
    """Judge ONE spacing change (character-identical strings, so the
    alphabetic offsets of both sides are comparable). Every model
    token must be justified:

      * established book word (printed >=2x) — always fine;
      * FRAGMENT MERGE: it spans >=2 whole deterministic tokens that
        are all rare (<2x) — the signature of a typesetter's mid-word
        wrap ("interme"+"dius"->"intermedius", "destr"+"oying"->
        "destroying"). "rhenium"+"The"->"rheniumThe" fails: "The" is
        not a fragment;
      * UN-GLUE: it is a >=4-letter piece of ONE rare (glued) det
        token whose sibling pieces are all established words, short
        ones very common ("negligibleor"->"negligible or").
        "calcifications"->"calcificatio ns" fails ("ns" is not a
        word); "Monochorionicity"->"Monochorionic ity" fails."""
    D, M = _tokruns(cdt), _tokruns(cmt)
    if not D or not M:
        return False
    if D == M:
        # no alphabetic change: only punctuation spacing may move, and
        # a space may only be INSERTED after sentence punctuation
        # ("damage,fetal"->"damage, fetal"), never after a symbol
        # (">=2mm"->">= 2mm") and never removed
        dd = re.sub(r"\s+", "", " ".join(cdt))
        mm = re.sub(r"\s+", "", " ".join(cmt))
        if dd != mm:
            return False
        i = j = 0
        sd, sm = " ".join(cdt), " ".join(cmt)
        while i < len(sd) and j < len(sm):
            if sd[i] == sm[j]:
                i += 1
                j += 1
            elif sm[j] == " ":          # model inserted a space
                if i == 0 or sd[i - 1] not in ",.:;!?":
                    return False
                j += 1
            else:                       # model removed a space
                return False
        return True
    for (ms, me, m, _mo, _mf) in M:
        if len(m) == 1:
            # single letters only where the det has the same single
            # letter ("mm"->"m m" is corruption)
            if not any(ds == ms and de == me for ds, de, dm, _o, _f in D
                       if dm == m):
                return False
            continue
        if words.get(m, 0) >= 2:
            continue
        ov = [r for r in D if r[0] < me and r[1] > ms]
        if not ov:
            return False
        if len(ov) >= 2 and ov[0][0] == ms and ov[-1][1] == me \
                and all(words.get(r[2], 0) < 2 for r in ov) \
                and not any(r[4] and r[3][0].isupper() for r in ov[1:]):
            continue                          # fragment merge
        if len(ov) == 1 and ov[0][0] <= ms and ov[0][1] >= me \
                and words.get(ov[0][2], 0) < 2 and len(m) >= 4:
            sib = [t for _s, _e, t, _o, _f in M
                   if t != m and _s < ov[0][1] and _e > ov[0][0]]
            if all(words.get(t, 0) >= 2
                   and (len(t) > 3 or words.get(t, 0) >= 50)
                   for t in sib):
                continue                      # un-glue of a rare blob
        return False
    return True


def _respaced(d: str, lj: str, words) -> str:
    """Best spacing of one cell: per diff block, take the model's
    spacing when _block_ok accepts it, else keep the deterministic
    one. A model glitch in one corner of a cell ("themalleus") can
    never veto its good repairs elsewhere in the same cell."""
    import difflib
    dw = re.findall(r"(\s*)(\S+)", d)
    mw = re.findall(r"(\s*)(\S+)", lj)
    smx = difflib.SequenceMatcher(
        a=[x[1].lower() for x in dw], b=[x[1].lower() for x in mw],
        autojunk=False)
    out = ""
    for op, i1, i2, j1, j2 in smx.get_opcodes():
        if op == "equal":
            seg = "".join(ws + t for ws, t in dw[i1:i2])
        else:
            cdt = [t for _ws, t in dw[i1:i2]]
            cd = "".join(cdt)
            cmt = [t for _ws, t in mw[j1:j2]]
            cm = "".join(cmt)
            if cd == cm and _block_ok(cdt, cmt, words):
                seg = "".join(ws + t for ws, t in mw[j1:j2])
                if seg and dw[i1:i2]:
                    seg = dw[i1][0] + seg[len(mw[j1][0]):]
            else:
                seg = "".join(ws + t for ws, t in dw[i1:i2])
        if out and seg and out[-1].isalnum() and seg[0].isalnum():
            out += " "
        out += seg
    out = out.strip()
    return out if _norm(out) == _norm(d) else d   # character safety net


def merge_llm(det_rows: list, llm_rows: list, vocab=None) -> tuple:
    """Fidelity envelope: same characters (whitespace-insensitive),
    then the deterministic cell is replaced only when the model cell
    is strictly more plausible under the book's own vocabulary —
    a candidate containing a non-word token ("TungstenThe",
    "atriumLeft") is disqualified outright; ties keep deterministic.

    Hybrid structure: when the model's row/cell grid differs from the
    deterministic grid, the model grid is accepted ONLY when the whole
    table's content is character-identical (whitespace removed) and
    every >=3-letter token in it is a word the book prints >=2 times.
    Otherwise the deterministic table is kept untouched."""
    if not isinstance(llm_rows, list) or not llm_rows:
        return det_rows, 0
    words = pairs = None
    if vocab is not None:
        words, pairs = vocab
    det_shape = [len(r) for r in det_rows]
    llm_shape = [len(r) if isinstance(r, list) else -1 for r in llm_rows]
    if det_shape != llm_shape:
        # structure suggestion: content must be character-identical and
        # every alphabetic token must be an established book word
        if words is not None and _content(llm_rows) == _content(det_rows):
            toks = [x.lower() for x in re.findall(
                r"[A-Za-z]{3,}", " ".join(
                    str(c) for r in llm_rows for c in r))]
            if all(words.get(t, 0) >= 1 for t in toks):
                return [[" ".join(str(c).split()) for c in r]
                        for r in llm_rows], 1
        return det_rows, 0
    out, nfix = [], 0
    for drow, lrow in zip(det_rows, llm_rows):
        if not isinstance(lrow, list) or len(lrow) != len(drow):
            return det_rows, 0
        newrow = []
        for d, l in zip(drow, lrow):
            l = str(l)
            lj = " ".join(l.split())
            take = False
            if lj != d and _norm(l) == _norm(d):
                if words is None:
                    take = True
                else:
                    lj = _respaced(d, lj, words)
                    take = lj != d
            if take:
                newrow.append(lj)
                nfix += 1
            else:
                newrow.append(d)
        out.append(newrow)
    return out, nfix


def _post(url: str, payload: dict, key: str) -> dict:
    import time as _time
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": key}, method="POST")
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:   # retry throttling/server errs
            if e.code in (429, 500, 503) and attempt < 3:
                _time.sleep(1.5 * attempt)
                continue
            raise


def _cache_path(cache_dir: Path, book, pg: int, box) -> Path:
    sig = hashlib.sha1(
        f"{getattr(book.doc, 'name', '')}|{pg}|{tuple(round(v,1) for v in box)}"
        .encode()).hexdigest()
    return cache_dir / f"{sig}.json"


def transcriber(cache_dir: Path | None = None, model: str | None = None,
                key: str | None = None):
    """Return llm(book, pg, box) -> rows | None (None = keep det)."""
    key = key or os.environ.get("GEMINI_API_KEY", "")
    model = model or os.environ.get("QBANK_LLM_MODEL", DEFAULT_MODEL)

    def llm(book, pg: int, box):
        cache = None
        if cache_dir is not None:
            cache = _cache_path(cache_dir, book, pg, box)
            if cache.exists():
                try:
                    return json.loads(cache.read_text())
                except Exception:
                    pass
        try:
            pix = book.doc[pg - 1].get_pixmap(
                clip=pymupdf.Rect(*box), matrix=pymupdf.Matrix(3, 3))
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            payload = {
                "contents": [{"parts": [
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                    {"text": PROMPT}]}],
                "generationConfig": {"temperature": 0.0,
                                     "max_output_tokens": 8192},
            }
            rows = None
            for attempt in range(3):
                resp = _post(API.format(model=model), payload, key)
                cand = (resp.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or []
                txt = "".join(pt.get("text", "") for pt in parts).strip()
                if txt:
                    txt = re.sub(r"^```(?:json)?|```$", "", txt)
                    try:
                        rows = json.loads(txt).get("rows")
                    except ValueError:
                        rows = None
                if rows is not None:
                    break
                # RECITATION/SAFETY filters are non-deterministic —
                # the same image usually passes on a retry
        except Exception:
            rows = None
        if cache is not None and rows is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(rows))
        return rows
    return llm


VERIFY_PROMPT = """You are re-checking ONE ruled medical-textbook table.
A previous transcription of this exact image may contain word-fragment
errors. Suspect fragments: {suspects}

Look at the image again, cell by cell, and return ONLY valid JSON
(no markdown fences): {{"rows": [["cell", "..."], ...]}} — one inner
list per table row, one string per visible column, reading order,
header first. Transcribe every cell EXACTLY as printed: same words,
symbols, units; empty cell = "". Join words the typesetter broke
across lines inside a cell; insert the missing space where two words
are glued. DO NOT correct spellings or terminology, DO NOT add,
remove, translate or reorder content. Pay special attention to the
suspect fragments above — decide from the IMAGE whether each is one
word or two."""


def verifier(cache_dir: Path | None = None, model: str | None = None,
             key: str | None = None):
    """Second pass for QA-flagged boxes: verify(book, pg, box,
    suspects) -> rows | None. Same envelope applies downstream, so a
    hallucinated answer can never reach the output."""
    key = key or os.environ.get("GEMINI_API_KEY", "")
    model = model or os.environ.get("QBANK_LLM_MODEL", DEFAULT_MODEL)

    def verify(book, pg: int, box, suspects):
        cache = None
        if cache_dir is not None:
            sig = hashlib.sha1(
                f"V|{getattr(book.doc, 'name', '')}|{pg}|"
                f"{tuple(round(v, 1) for v in box)}".encode()).hexdigest()
            cache = cache_dir / f"{sig}.json"
            if cache.exists():
                try:
                    return json.loads(cache.read_text())
                except Exception:
                    pass
        try:
            pix = book.doc[pg - 1].get_pixmap(
                clip=pymupdf.Rect(*box), matrix=pymupdf.Matrix(3, 3))
            b64 = base64.b64encode(pix.tobytes("png")).decode()
            payload = {
                "contents": [{"parts": [
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                    {"text": VERIFY_PROMPT.format(
                        suspects=", ".join(suspects))}]}],
                "generationConfig": {"temperature": 0.0,
                                     "max_output_tokens": 8192},
            }
            rows = None
            for attempt in range(3):
                resp = _post(API.format(model=model), payload, key)
                cand = (resp.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or []
                txt = "".join(pt.get("text", "") for pt in parts).strip()
                if txt:
                    txt = re.sub(r"^```(?:json)?|```$", "", txt)
                    try:
                        rows = json.loads(txt).get("rows")
                    except ValueError:
                        rows = None
                if rows is not None:
                    break
        except Exception:
            rows = None
        if cache is not None and rows is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(rows))
        return rows
    return verify

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


def merge_llm(det_rows: list, llm_rows: list) -> tuple:
    """Apply the fidelity envelope; return (rows, n_model_repairs)."""
    if not isinstance(llm_rows, list) or len(llm_rows) != len(det_rows):
        return det_rows, 0
    out, nfix = [], 0
    for drow, lrow in zip(det_rows, llm_rows):
        if not isinstance(lrow, list) or len(lrow) != len(drow):
            return det_rows, 0
        newrow = []
        for d, l in zip(drow, lrow):
            l = str(l)
            lj = " ".join(l.split())
            if lj != d and _norm(l) == _norm(d):
                newrow.append(lj)      # same characters, better spacing
                nfix += 1
            else:
                newrow.append(d)
        out.append(newrow)
    return out, nfix


def _post(url: str, payload: dict, key: str) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": key}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


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
            resp = _post(API.format(model=model), {
                "contents": [{"parts": [
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                    {"text": PROMPT}]}],
                "generationConfig": {"temperature": 0.0},
            }, key)
            txt = resp["candidates"][0]["content"]["parts"][0]["text"]
            txt = re.sub(r"^```(?:json)?|```$", "", txt.strip())
            rows = json.loads(txt).get("rows")
        except Exception:
            rows = None
        if cache is not None and rows is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(rows))
        return rows
    return llm

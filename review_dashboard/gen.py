"""Render 120dpi page crops for one book's REVIEW-queue tables.
Run right after the book run while split/ exists.  The queue itself
is served live by server.py from the split outputs — no data files."""
import json
import sys
from pathlib import Path

import fitz

BOOK = sys.argv[1]                      # ENT | RAD | ANA
PDF = sys.argv[2]
ROOT = Path(f"/home/user/repo/qbank_output_{BOOK.lower()}")
HERE = Path("/home/user/repo/review_dashboard")
CROPS = HERE / "crops"
CROPS.mkdir(exist_ok=True)

items = []
for ch in sorted((ROOT / "split" / BOOK).iterdir()):
    for l in (ch / "questions.jsonl").read_text().splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        for t in r.get("tables") or []:
            v = (t.get("validation") or {}).get("table_qa") or {}
            if v.get("status") != "REVIEW":
                continue
            items.append({"table_id": t["table_id"],
                          "pages": t["source_pages"]})

d = fitz.open(PDF)
pages_needed = sorted({p for it in items for p in it["pages"]})
for p in pages_needed:
    out = CROPS / f"{BOOK.lower()}_p{p:04d}.png"
    if not out.exists():
        pix = d[p - 1].get_pixmap(dpi=120)
        pix.save(str(out))
print(BOOK, "REVIEW items:", len(items), "| pages rendered:", len(pages_needed))

"""Extract REVIEW-queue tables from one book's output + render page crops.
Run right after the book run while split/ exists.  Read-only otherwise."""
import json
import sys
from pathlib import Path

import fitz

BOOK = sys.argv[1]                      # ENT | RAD | ANA
PDF = sys.argv[2]
ROOT = Path(f"/home/user/repo/qbank_output_{BOOK.lower()}")
HERE = Path("/home/user/repo/review_dashboard")
CROPS = HERE / "crops"
DATA = HERE / "data"
CROPS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

items = []
for ch in sorted((ROOT / "split" / BOOK).iterdir()):
    for fn in ("questions.jsonl",):
        for l in (ch / fn).read_text().splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            for t in r.get("tables") or []:
                v = (t.get("validation") or {}).get("table_qa") or {}
                if v.get("status") != "REVIEW":
                    continue
                items.append({
                    "q_id": r["q_id"],
                    "table_id": t["table_id"],
                    "suspects": v.get("suspect_fragments") or [],
                    "markdown": t["markdown"],
                    "pages": t["source_pages"],
                    "cross_page": bool(t.get("merged_continuation")),
                })

d = fitz.open(PDF)
pages_needed = sorted({p for it in items for p in it["pages"]})
for p in pages_needed:
    out = CROPS / f"{BOOK.lower()}_p{p:04d}.png"
    if not out.exists():
        pix = d[p - 1].get_pixmap(dpi=120)
        pix.save(str(out))
(DATA / f"{BOOK.lower()}.json").write_text(json.dumps(items, indent=1))
print(BOOK, "REVIEW items:", len(items), "| pages rendered:", len(pages_needed))

"""Render 120dpi page crops for one book's REVIEW-queue tables.
Run right after the book run while split/ exists.  The queue itself
is served live (server.py / dashboard.py) from the split outputs —
no data files.

    python3 review_dashboard/gen.py ENT [pdf-path] [--out OUTPUT_ROOT]

Crops land in <out_root>/crops/ (on the writable volume in prod)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz

from qbank import config


def gen_crops(book: str, pdf: str, out_root: Path,
              crops_dir: Path | None = None) -> dict:
    out_root = Path(out_root)
    crops = Path(crops_dir) if crops_dir else out_root / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    items = []
    split_book = out_root / "split" / book
    for ch in sorted(split_book.iterdir()) if split_book.is_dir() else []:
        qf = ch / "questions.jsonl"
        if not qf.exists():
            continue
        for l in qf.read_text().splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            for t in r.get("tables") or []:
                v = (t.get("validation") or {}).get("table_qa") or {}
                if v.get("status") != "REVIEW":
                    continue
                items.append({"table_id": t["table_id"],
                              "pages": t.get("source_pages") or []})
    pages_needed = sorted({p for it in items for p in it["pages"]})
    rendered = 0
    if pages_needed:
        d = fitz.open(pdf)
        for p in pages_needed:
            out = crops / f"{book.lower()}_p{p:04d}.png"
            if not out.exists():
                pix = d[p - 1].get_pixmap(dpi=120)
                pix.save(str(out))
                rendered += 1
    return {"book": book, "items": len(items),
            "pages_rendered": rendered, "crops_dir": str(crops)}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: gen.py BOOK [pdf] [--out ROOT]", file=sys.stderr)
        return 2
    book = argv[0].upper()
    pdf = None
    out_root = config.OUTPUT_ROOT
    i = 1
    while i < len(argv):
        if argv[i] == "--out":
            out_root = Path(argv[i + 1])
            i += 2
        else:
            pdf = argv[i]
            i += 1
    if pdf is None:
        entry = config.load_books().get(book)
        if not entry:
            print(f"no pdf given and {book} not in books.json",
                  file=sys.stderr)
            return 2
        pdf = config.resolve_book_path(entry)
    res = gen_crops(book, pdf, out_root)
    print(f"{book} REVIEW items: {res['items']} | pages rendered: "
          f"{res['pages_rendered']} -> {res['crops_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

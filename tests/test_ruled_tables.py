"""Ruled-table detection and markdown reconstruction on synthetic PDFs.

The rule under test: a table exists iff the book DREW rules (long
horizontal rules stitched by verticals). Bullet lists, unruled
pseudo-tables and short underlines are never tables.
"""

import pymupdf
import pytest

from qbank.tables import build_box
from qbank.textlayer import Book, _ruled_table_boxes


def _page_with_grid(doc):
    """One page carrying a 3-col x 3-row ruled grid with cell text."""
    p = doc.new_page(width=595, height=842)
    x0, y0, x1, y1 = 72, 400, 372, 520     # 3 rows of 40pt
    cols = [72, 172, 272, 372]
    rows = [400, 440, 480, 520]
    for y in rows:
        p.draw_line((x0, y), (x1, y), width=1)
    for x in cols:
        p.draw_line((x, y0), (x, y1), width=1)
    cells = [["Type", "Function", "Site"],
             ["Kinase", "adds phosphate", "cytosol"],
             ["Lipase", "cleaves ester", "gut"]]
    for r, row in enumerate(cells):
        for c, txt in enumerate(row):
            p.insert_text((cols[c] + 6, rows[r] + 24), txt, fontsize=10)
    return p


def test_ruled_grid_is_detected_as_one_box():
    doc = pymupdf.open()
    _page_with_grid(doc)
    boxes = _ruled_table_boxes(doc[0])
    assert len(boxes) == 1
    bx = boxes[0]
    assert bx[0] == pytest.approx(72, abs=1)
    assert bx[1] == pytest.approx(400, abs=1)
    assert bx[2] == pytest.approx(372, abs=1)
    assert bx[3] == pytest.approx(520, abs=1)


def test_short_underline_is_not_a_table():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "Section heading", fontsize=12)
    p.draw_line((72, 104), (170, 104), width=1)      # 98pt underline
    assert _ruled_table_boxes(p) == []


def test_bullets_without_rules_are_not_a_table():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    for i, t in enumerate(["\u2022 From glucose to pyruvate",
                           "\u2022 Pyruvate to acetyl-CoA",
                           "\u2022 Citric acid cycle"]):
        p.insert_text((72, 200 + i * 16), t, fontsize=10)
    assert _ruled_table_boxes(p) == []


def test_markdown_keeps_visual_order(tmp_path):
    pdf = tmp_path / "grid.pdf"
    doc = pymupdf.open()
    _page_with_grid(doc)
    doc.save(str(pdf))
    doc.close()

    book = Book(str(pdf))
    box = book.page(1).table_boxes[0]
    from collections import Counter
    bt = build_box(book, 1, box, Counter())
    md_rows = ["| " + " | ".join(r) + " |" for r in bt.rows]
    assert md_rows[0] == "| Type | Function | Site |"
    assert md_rows[1] == "| Kinase | adds phosphate | cytosol |"
    assert md_rows[2] == "| Lipase | cleaves ester | gut |"

    # every cell string must appear in the page's visual-order text
    flat = " ".join(l.text for l in sorted(book.page(1).lines,
                                           key=lambda l: (round(l.y0 / 3.0), l.x0)))
    for cell in ("Kinase", "adds phosphate", "cytosol",
                 "cleaves ester", "gut"):
        assert cell in flat
    book.close()

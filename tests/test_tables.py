"""Table reconstruction tests: in-cell line joins, camel repair,
cross-page merging — all decided by layout evidence."""

from collections import Counter

import pymupdf
import pytest

from qbank.tables import ChapterTables, build_box
from qbank.textlayer import Book


def _grid_page(doc, cells, x0=72, y0=300, colw=110, rowh=34, ncols=3):
    """Page with a ruled ncols x len(cells) grid; cells[i] = row texts.
    Row text may be a list of wrapped LINES per cell."""
    p = doc.new_page(width=595, height=842)
    cols = [x0 + i * colw for i in range(ncols + 1)]
    rows = [y0 + i * rowh for i in range(len(cells) + 1)]
    for y in rows:
        p.draw_line((cols[0], y), (cols[-1], y), width=1)
    for x in cols:
        p.draw_line((x, rows[0]), (x, rows[-1]), width=1)
    for r, row in enumerate(cells):
        for c, content in enumerate(row):
            lines = content if isinstance(content, list) else [content]
            for k, ln in enumerate(lines):
                # last wrapped line of a full cell reaches the column
                # edge (flush) to mimic the typesetter
                p.insert_text((cols[c] + 5, rows[r] + 14 + k * 11),
                              ln, fontsize=8)
    return p


def _book_with(tmp_path, pages_cells, colw=110, rowh=34):
    doc = pymupdf.open()
    for cells in pages_cells:
        _grid_page(doc, cells, colw=colw, rowh=rowh)
    pdf = tmp_path / "t.pdf"
    doc.save(str(pdf))
    doc.close()
    return Book(str(pdf))


def test_word_split_glues_but_word_wrap_keeps_space(tmp_path):
    # col1: "medial su" reaches the column edge (flush) + "rface"
    #        -> "medial surface"
    # col0: "middle" ends far from the edge + "ear" -> "middle ear"
    cells = [
        ["Nerve", "Region", "Notes"],
        [["middle", "ear"], ["medial su", "rface"], "x"],
        ["y", "z", "w"],
    ]
    book = _book_with(tmp_path, [cells], colw=50, rowh=40)
    box = book.page(1).table_boxes[0]
    bt = build_box(book, 1, box, Counter())
    col1 = [r[1] for r in bt.rows]
    col0 = [r[0] for r in bt.rows]
    assert "medial surface" in col1
    assert "middle ear" in col0        # wrapped words keep their space
    assert bt.line_joins >= 1
    book.close()


def test_comma_digit_continuation(tmp_path):
    cells = [
        ["Nerve", "Derivation", "Region"],
        ["auricular", ["Cervical plexus C2,C", "3"], "concha"],
    ]
    book = _book_with(tmp_path, [cells], colw=90, rowh=40)
    box = book.page(1).table_boxes[0]
    bt = build_box(book, 1, box, Counter())
    joined = [r[1] for r in bt.rows]
    assert any("C2,C3" in c for c in joined)
    book.close()


def test_camel_space_repair_guarded(tmp_path):
    cells = [
        ["Structure", "Note", "x"],
        ["antihelixSome supply", "pH and IgG stay", "ok"],
    ]
    book = _book_with(tmp_path, [cells])
    box = book.page(1).table_boxes[0]
    bt = build_box(book, 1, box, Counter())
    flat = " ".join(" ".join(r) for r in bt.rows)
    assert "antihelix Some" in flat
    assert "pH and IgG stay" in flat   # never split
    assert bt.camel_fixes >= 1
    book.close()


def test_cross_page_merge_with_header_dedup(tmp_path):
    header = ["Nerve", "Derivation", "Region"]
    page1 = [header, ["Greater auricular", "C2,C3", "concha"],
             ["Lesser occipital", "C2", "medial su"]]
    page2 = [header, ["Auriculotemporal", "V3", "tragus"]]
    book = _book_with(tmp_path, [page1, page2])
    ct = ChapterTables(book, 99, 1, 2)
    b1 = book.page(1).table_boxes[0]
    lt = ct.lookup(1, tuple(b1), Counter())
    assert lt is not None
    assert lt.cross_page
    assert lt.source_pages == [1, 2]
    assert lt.header_deduplicated
    assert lt.markdown.count("| Nerve | Derivation | Region |") == 1
    assert "Auriculotemporal" in lt.markdown
    # second box resolves to the SAME logical table (one record)
    b2 = book.page(2).table_boxes[0]
    assert ct.lookup(2, tuple(b2), Counter()).table_id == lt.table_id
    assert ct.stats()["logical_tables"] == 1
    book.close()


def test_record_schema(tmp_path):
    book = _book_with(tmp_path, [[["A", "B", "C"], ["1", "2", "3"]]])
    ct = ChapterTables(book, 5, 1, 1)
    box = book.page(1).table_boxes[0]
    rec = ct.lookup(1, tuple(box), Counter()).as_record()
    assert rec["type"] == "table"
    assert rec["table_id"] == "005-T01"
    assert rec["source_pages"] == [1]
    assert rec["extraction"] == "ruled_grid_geometry"
    assert rec["validation"]["status"] in ("ok", "warnings")
    book.close()

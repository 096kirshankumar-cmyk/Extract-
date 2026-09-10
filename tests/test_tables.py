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

def test_vocab_fragment_glue_and_token_repair():
    from collections import Counter
    from qbank.tables import _join_decision, _repair_token
    words = Counter({"flow": 3, "of": 50, "increased": 4, "pulmonary": 6,
                     "damage": 2, "fetal": 2, "o": 1, "al": 1})
    pairs = Counter({("increased", "pulmonary"): 2})
    vocab = (words, pairs)

    class L:
        def __init__(s, text, x1=100.0):
            s.text, s.x1 = text, x1

    # wrapped non-word fragments glue even in a non-flush column
    assert _join_decision(L("Increasedpulmonary blood fl"),
                          L("ow (pulmonary plethora)"), 500.0, False,
                          vocab) == "glue"
    assert _join_decision(L("development o"), L("f cancer"), 500.0, False,
                          vocab) == "glue"
    # real word boundary stays a space
    assert _join_decision(L("Ventricular septal"), L("defect"), 500.0,
                          False, vocab) == "space"
    # token-level repairs with book-internal evidence only
    assert _repair_token("damage,fetal", words, pairs) == ("damage, fetal", 1)
    assert _repair_token("Increasedpulmonary", words, pairs) == \
        ("Increased pulmonary", 1)
    assert _repair_token("pulmonary", words, pairs) == ("pulmonary", 0)


def test_vocab_token_stream_repairs():
    from collections import Counter
    from qbank.tables import _repair_tokens
    words = Counter({"of": 50, "cancer": 11, "the": 3000, "probability": 1,
                     "not": 100, "depend": 4, "increased": 27,
                     "pulmonary": 128, "increasedpulmonary": 1,
                     "therefore": 1, "there": 44, "flow": 25})
    out, n = _repair_tokens(["development", "o", "fcancer"], words, Counter())
    assert " ".join(out) == "development of cancer" and n == 1
    out, n = _repair_tokens(["Theprobability", "of"], words, Counter())
    assert " ".join(out) == "The probability of" and n == 1
    out, n = _repair_tokens(["severity", "notdepend"], words, Counter())
    assert " ".join(out) == "severity not depend" and n == 1
    out, n = _repair_tokens(["Increasedpulmonary"], words, Counter())
    assert " ".join(out) == "Increased pulmonary" and n == 1
    # glued function-word suffix splits
    words2 = words | Counter({"artery": 4, "or": 20})
    out, n = _repair_tokens(["carotid", "arteryor"], words2, Counter())
    assert " ".join(out) == "carotid artery or" and n == 1
    # real words are untouched
    out, n = _repair_tokens(["therefore", "flow"], words, Counter())
    assert " ".join(out) == "therefore flow" and n == 0


def test_qa_suspects_evidence_rules():
    from collections import Counter
    from qbank.tables import qa_suspects, long_space_suspects
    words = Counter({"brain": 5, "stem": 5, "brainstem": 6, "dome": 4,
                     "do": 3, "me": 3, "can": 5, "be": 6,
                     "mucoperichondrial": 3, "freer": 1, "incision": 2})
    pairs = Counter({("brain", "stem"): 3, ("do", "me"): 0})
    m = [["brain stem", "do me", "canbe", "Freer incision"]]
    s = qa_suspects(m, words, pairs)
    # spaced pair the book itself prints: legitimate, not a suspect
    assert "brain" not in s and "stem" not in s
    # glued collision the book repeats: still caught
    assert "do" in s and "me" in s
    # closed-class glue (can+be): the audit's false negative class
    assert "canbe" in s
    # proper names never enter via the pair rule
    assert "Freer" not in s and "incision" not in s
    # long established terms are not lost-space suspects
    assert long_space_suspects("Mucoperichondrial flap", words) == []
    assert long_space_suspects("Intracranialintradural mass", words) == \
        ["Intracranialintradural"]
    assert long_space_suspects("anything", None) == []

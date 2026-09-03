"""End-to-end regression on a synthetic two-chapter book.

Exercises the real pipeline path — toc.parse_toc, offset detection,
zones.scan_chapter, parse.build_chapter_records (including ruled-table
extraction and Symbol-font glyph repair), images.claim_chapter_images
and writer.write_chapter_split — without needing the real ED8 PDF.
"""

import json

import pymupdf
import pytest

from qbank import config, run


def _footer(p, pno):
    p.insert_text((290, 800), str(pno), fontsize=10)


def _chapter_body(p, qn_start, n=2, with_image=False):
    """n questions with options; returns nothing."""
    y = 100
    for i in range(n):
        qn = qn_start + i
        p.insert_text((72, y), f"Question {qn}:", fontsize=11)
        y += 18
        p.insert_text((72, y), f"Stem text for question {qn} goes here.",
                      fontsize=10)
        y += 12
        if with_image and i == 0:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 120))
            for yy in range(120):
                for xx in range(0, 120, 4):
                    pix.set_pixel(xx, yy, ((xx * 7 + yy) % 256,
                                           (yy * 5) % 256, (xx + yy) % 256))
            p.insert_image(pymupdf.Rect(380, y + 4, 500, y + 124),
                           pixmap=pix)
            pix = None
        y += 132
        for letter, word in zip("abcd", ("Alpha", "Beta", "Gamma", "Delta")):
            p.insert_text((72, y), f"{letter}) {word} option {qn}",
                          fontsize=10)
            y += 16
        y += 20


def _key_and_solutions(p, keys, sol_texts):
    p.insert_text((72, 80), "Answer Key", fontsize=12)
    p.insert_text((72, 100), "Question No.", fontsize=10)
    p.insert_text((200, 100), "Correct Option", fontsize=10)
    y = 120
    for qn, letter in keys:
        p.insert_text((72, y), f"{qn}", fontsize=10)
        p.insert_text((200, y), letter, fontsize=10)
        y += 16
    p.insert_text((72, y + 30), "Detailed Explanations", fontsize=12)
    y += 60
    for qn, text in sol_texts:
        p.insert_text((72, y), f"Solution to Question {qn}:", fontsize=11)
        y += 18
        p.insert_text((72, y), text, fontsize=10)
        y += 24
    return y


def _grid(p, y0=240):
    cols = [72, 172, 272, 372]
    rows = [y0, y0 + 40, y0 + 80, y0 + 120]
    for yy in rows:
        p.draw_line((72, yy), (372, yy), width=1)
    for xx in cols:
        p.draw_line((xx, y0), (xx, y0 + 120), width=1)
    cells = [["Type", "Function", "Site"],
             ["Kinase", "adds phosphate", "cytosol"],
             ["Lipase", "cleaves ester", "gut"]]
    for r, row in enumerate(cells):
        for c, txt in enumerate(row):
            p.insert_text((cols[c] + 6, rows[r] + 24), txt, fontsize=9)


def _build_book(path):
    doc = pymupdf.open()
    # p1 — contents table
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 80), "Contents", fontsize=16)
    p.insert_text((72, 140), "1 First Chapter 2", fontsize=11)
    p.insert_text((72, 160), "2 Second Chapter 4", fontsize=11)
    _footer(p, 1)

    # p2-3 — chapter 1
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 70), "Chapter 1: First Chapter", fontsize=20)
    _chapter_body(p, 1, with_image=True)
    _footer(p, 2)
    p = doc.new_page(width=595, height=842)
    _key_and_solutions(
        p, [(1, "a"), (2, "b")],
        [(1, "First solution sentence."), (2, "Second solution sentence.")])
    # Symbol-font broken arrow between two Helvetica words (the exact
    # artifact class in the real books). The symbol sits 0.7pt higher
    # so its y0 shares the Helvetica baseline bucket.
    p.insert_text((72, 300), "Glucose", fontsize=10)
    p.insert_text((120, 299.3), "\xb0", fontsize=10, fontname="symb")
    p.insert_text((135, 300), "Pyruvate", fontsize=10)
    _footer(p, 3)

    # p4-5 — chapter 2
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 70), "Chapter 2: Second Chapter", fontsize=20)
    _chapter_body(p, 1, n=1)
    _footer(p, 4)
    p = doc.new_page(width=595, height=842)
    _key_and_solutions(p, [(1, "c")], [(1, "See the summary table:")])
    _grid(p)
    _footer(p, 5)

    doc.save(str(path))
    doc.close()


@pytest.fixture()
def mini_output(tmp_path, monkeypatch):
    """Run the pipeline on the synthetic book into an isolated root."""
    out = tmp_path / "out"
    monkeypatch.setattr(config, "OUTPUT_ROOT", out)
    monkeypatch.setattr(config, "DATA_DIR", out / "data")
    monkeypatch.setattr(config, "ASSETS_DIR", out / "assets" / "questions")
    monkeypatch.setattr(config, "SPLIT_DIR", out / "split")
    monkeypatch.setattr(config, "SUBJECTS_DIR", out / "subjects")
    monkeypatch.setattr(config, "STATE_FILE", out / "state.json")

    pdf = tmp_path / "mini.pdf"
    _build_book(pdf)
    res = run.run_book(str(pdf), "TST", page_offset="auto", force=True,
                       output_root=out)
    return out, res


def _rows(path):
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def test_census_and_counts(mini_output):
    out, res = mini_output
    assert res["census_failures"] == []
    assert res["total_questions"] == 3
    assert res["chapters_run"] == 2


def test_split_files_and_answers(mini_output):
    out, _ = mini_output
    ch1 = out / "split" / "TST" / "TST-001"
    for name in ("questions.jsonl", "answers.jsonl", "solutions.jsonl",
                 "unresolved_qids.jsonl", "orphans.jsonl",
                 "image_manifest.jsonl", "chapter_completeness.json"):
        assert (ch1 / name).exists(), name
    q_rows = _rows(ch1 / "questions.jsonl")
    assert [r["q_no"] for r in q_rows] == [1, 2]
    assert all(r["qa_status"] == "READY" for r in q_rows)
    assert all(r["question_text_prov"] == "TEXT_LAYER" for r in q_rows)
    a_rows = _rows(ch1 / "answers.jsonl")
    assert [(r["q_no"], r["correct_option"]) for r in a_rows] == \
        [(1, "A"), (2, "B")]
    s_rows = _rows(ch1 / "solutions.jsonl")
    assert "First solution sentence." in s_rows[0]["solution_text"]
    # Symbol-font degree repaired to a reaction arrow, audited (the
    # artifact line sits under Solution 2 on the page)
    assert "Glucose \u2192 Pyruvate" in s_rows[1]["solution_text"]
    comp = json.loads((ch1 / "chapter_completeness.json").read_text())
    assert comp["census"]["ok"] is True
    assert comp["glyph_fix_counts"].get("reaction_arrow", 0) >= 1
    assert comp["unresolved_qid_count"] == 0


def test_question_image_claimed(mini_output):
    out, _ = mini_output
    ch1 = out / "split" / "TST" / "TST-001"
    manifest = _rows(ch1 / "image_manifest.jsonl")
    embedded = [m for m in manifest if m["type"] == "QUESTION"]
    assert len(embedded) == 1
    f = out / "assets" / "questions" / "TST" / embedded[0]["file"].split("/")[-1]
    assert f.exists() and f.stat().st_size > config.MIN_IMAGE_BYTES


def test_ruled_table_becomes_markdown_and_render(mini_output):
    out, _ = mini_output
    ch2 = out / "split" / "TST" / "TST-002"
    s_rows = _rows(ch2 / "solutions.jsonl")
    tables = s_rows[0].get("tables") or []
    assert len(tables) == 1
    md = tables[0]["markdown"]
    assert "| Type | Function | Site |" in md
    assert "| Kinase | adds phosphate | cytosol |" in md
    # table prose stays OUT of solution_text (moved to the tables field)
    assert "adds phosphate" not in s_rows[0]["solution_text"]
    manifest = _rows(ch2 / "image_manifest.jsonl")
    renders = [m for m in manifest if m["type"] == "SOLUTION"]
    assert len(renders) == 1
    comp = json.loads((ch2 / "chapter_completeness.json").read_text())
    assert comp["images"]["table_renders"] == 1
    assert comp["census"]["ok"] is True

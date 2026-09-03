"""
Record building: ChapterScan + text layer -> one record per question.

Every field is filled ONLY from the book's own text layer:

  question_text  header line -> first option marker, reflowed verbatim
  options        a) .. d) markers, verbatim
  correct_option the answer-key baseline for this q_no
  solution_text  solution header -> next header, reflowed verbatim

Glyph repair (qbank.glyphs) runs per field with a per-question audit
count. A field whose repair left an unknown glyph keeps the original
character and flags the question (qa_reason: unknown_glyph).
"""

from __future__ import annotations

import re
from collections import Counter

from . import glyphs
from .config import (GRADE_RESOLVED, GRADE_RESOLVED_ANCHORED, PROV_TEXT_LAYER,
                     QA_INCOMPLETE, QA_READY, QA_REVIEW_NEEDED)
from .textlayer import Book, Line, reflow
from .zones import ChapterScan

RE_OPT_MARKER = re.compile(r"^([a-d])\)\s*(.*)$", re.S)
TITLE_MIN_SIZE = 16.0


def _baseline_groups(body: list[Line]):
    """[((page, bucket), [lines sorted by x0])] in reading order.
    The ED8 tables print every CELL as its own text line at a shared
    baseline, so 'several lines on one baseline' IS the table signal;
    prose owns its baseline alone."""
    groups: dict = {}
    for ln in body:
        groups.setdefault((ln.page, round(ln.y0 / 3.0)), []).append(ln)
    return [(k, sorted(groups[k], key=lambda l: l.x0))
            for k in sorted(groups)]


def _table_markdown(book: Book, pg: int, box, counts) -> str:
    """Reconstruct one ruled table from its LINES.

    Cells are grouped into baselines with exactly the parser's/
    verifier's y/3 bucketing and read left-to-right, so the cell text
    concatenation is always a substring of the page's visual-order
    text — no reordering, no guessing. Column anchors are the line
    x0 edges that repeat down the table (columns are left-aligned);
    a rare single-line column simply folds into its left neighbour,
    which affects cosmetics, never content or order."""
    pd = book.page(pg)
    lines = [l for l in pd.lines
             if box[0] - 2 <= l.x0 <= box[2] + 2
             and box[1] - 3 <= (l.y0 + l.y1) / 2 <= box[3] + 3]
    if not lines:
        return ""

    xs = sorted(l.x0 for l in lines)
    edges: list[float] = []
    for x in xs:
        if edges and x - edges[-1] <= 8:
            continue
        edges.append(x)
    anchors = [a for a in edges
               if sum(1 for l in lines if abs(l.x0 - a) <= 8) >= 2]
    if not anchors:
        anchors = [xs[0]]

    def col_of(l):
        c = 0
        for i, a in enumerate(anchors):
            if l.x0 >= a - 4:
                c = i
        return c

    buckets: dict = {}
    for l in lines:
        buckets.setdefault(round(l.y0 / 3.0), []).append(l)
    md_rows = []
    for bkey in sorted(buckets):
        cells = [""] * len(anchors)
        for l in sorted(buckets[bkey], key=lambda l: l.x0):
            c = col_of(l)
            cells[c] = (cells[c] + " " + l.text).strip()
        cells = [glyphs.repair(c, counts) if c else c for c in cells]
        if any(cells):
            md_rows.append("| " + " | ".join(cells) + " |")
    if md_rows:
        md_rows.insert(1, "|" + "---|" * len(anchors))
    return "\n".join(md_rows)


def _extract_tables(book: Book, body: list[Line], counts):
    """Split RULED-table baselines out of a block's body.

    A baseline is table content when its lines' y-center falls inside
    a ruled-table box on its page — the book literally drew the grid,
    so there is no guessing. Unruled column text and bullets stay
    prose (verbatim, visual order). Returns (kept_lines, tables,
    regions=[(page, bbox)])."""
    tables, regions, kept = [], [], []
    md_cache: dict = {}
    for (pg, _bkt), lns in _baseline_groups(body):
        boxes = book.page(pg).table_boxes
        ycen = sum(l.y0 for l in lns) / len(lns)
        inside = next((bx for bx in boxes
                       if bx[1] - 3 <= ycen <= bx[3] + 3), None)
        if inside is None:
            kept.extend(lns)
            continue
        key = (pg, inside)
        if key not in md_cache:
            md_cache[key] = _table_markdown(book, pg, inside, counts)
        md = md_cache[key]
        if not md:
            # ruled region with no readable words (a drawn figure, not
            # a text table) — its lines stay prose; nothing is dropped
            kept.extend(lns)
            continue
        if key not in regions:
            tables.append({"type": "table", "markdown": md})
            regions.append(key)
    return kept, tables, regions


def _lines_between(book: Book, start: tuple, end: tuple,
                   include_start: bool = False) -> list[Line]:
    out = []
    last_pg = min(end[0], book.total_pages)   # end may be the past-EOF sentinel
    for pg in range(start[0], last_pg + 1):
        for ln in book.page(pg).lines:
            if ln.pos > start or (include_start and ln.pos == start):
                if ln.pos < end:
                    out.append(ln)
    return out


def _split_stem_options(lines: list[Line]):
    """(stem_lines, {letter: [seed_str | Line, ...]}, markers) using the
    printed a) b) c) d) markers. The Biochemistry book uses exactly
    'a)'..'d)' for all 582 questions (verified); the scan is defensive
    anyway: the first marker must be a), later markers must arrive in
    order, anything after d) is continuation text of option D.
    markers = [(letter, line_pos)] for the image-claiming geometry."""
    stem, opts, cur, markers = [], {}, None, []
    for ln in lines:
        m = RE_OPT_MARKER.match(ln.text.strip())
        letter = m.group(1) if m else None
        if letter and letter not in opts and (
                (cur is None and letter == "a")
                or (cur is not None and letter > cur)):
            cur = letter
            markers.append((letter, ln.pos))
            rest = m.group(2).strip()
            opts[letter] = [rest] if rest else []
            continue
        (opts[cur] if cur else stem).append(ln)
    return stem, opts, markers


def build_chapter_records(book: Book, scan: ChapterScan,
                          chapter_no: int) -> tuple[dict, dict, dict, Counter, list]:
    """Returns (records, option_markers, table_regions, glyph_audit,
    extra_anomalies).

    records[q_no] = {
        question_text, options {A..D}, correct_option, solution_text,
        tables, source_pages, key_page, q_header_page, s_header_page,
        glyph_fixes (Counter), flags [str],
    }
    option_markers[q_no] = [(letter, pos)] for figure claiming.
    table_regions[(q_no, "Q"|"SOL")] = [(page, bbox)] for clip renders.
    """
    glyph_audit = Counter()
    anomalies = []
    option_markers = {}
    table_regions = {}
    key_map = {r.q_no: r for r in scan.key_rows}
    sol_map = dict(scan.solution_headers)
    qhdr_map = dict(scan.question_headers)
    all_qnos = sorted(set(qhdr_map) | set(key_map) | set(sol_map))

    qblocks = {qn: (s, e) for qn, s, e in scan.question_blocks()}
    sblocks = {qn: (s, e) for qn, s, e in scan.solution_blocks()}

    records = {}
    for qn in all_qnos:
        counts = Counter()
        flags = []
        rec = {
            "question_text": "", "options": {}, "correct_option": "",
            "solution_text": "", "tables": [],
            "source_pages": set(), "key_page": None,
            "q_header_page": None, "s_header_page": None,
            "glyph_fixes": counts, "flags": flags,
        }

        if qn in qblocks:
            s, e = qblocks[qn]
            rec["q_header_page"] = qhdr_map[qn].page
            body = _lines_between(book, s, e)
            body, q_tables, q_regions = _extract_tables(book, body, counts)
            rec["tables"].extend(q_tables)
            if q_regions:
                table_regions[(qn, "Q")] = q_regions
            stem_lines, opts_raw, markers = _split_stem_options(body)
            option_markers[qn] = markers
            stem = glyphs.repair(reflow(stem_lines), counts)
            rec["question_text"] = stem
            for letter in "abcd":
                raw = opts_raw.get(letter)
                if raw is None:
                    continue
                seed, rest = [], []
                for item in raw:
                    (seed if isinstance(item, str) else rest).append(item)
                text = " ".join(seed) if seed else ""
                if rest:
                    tail = glyphs.repair(reflow(rest), counts)
                    text = (text + " " + tail).strip() if text else tail
                rec["options"][letter.upper()] = glyphs.repair(text, counts) \
                    if text else text
            for ln in body:
                rec["source_pages"].add(ln.page)
            rec["source_pages"].add(rec["q_header_page"])

        kr = key_map.get(qn)
        if kr:
            rec["correct_option"] = kr.letter.upper()
            rec["key_page"] = kr.page
            rec["source_pages"].add(kr.page)

        if qn in sblocks:
            s, e = sblocks[qn]
            rec["s_header_page"] = sol_map[qn].page
            sol_lines = _lines_between(book, s, e)
            sol_lines, s_tables, s_regions = _extract_tables(
                book, sol_lines, counts)
            rec["tables"].extend(s_tables)
            if s_regions:
                table_regions[(qn, "SOL")] = s_regions
            rec["solution_text"] = glyphs.repair(reflow(sol_lines), counts)
            for ln in sol_lines:
                rec["source_pages"].add(ln.page)
            rec["source_pages"].add(rec["s_header_page"])

        if counts.get("unknown_glyph"):
            flags.append("unknown_glyph")

        glyph_audit.update(counts)
        rec["source_pages"] = sorted(rec["source_pages"])
        records[qn] = rec

    # cross-zone census anomalies (row-level flags)
    for qn, rec in records.items():
        if qn not in qblocks:
            rec["flags"].append("no_question_header")
        if qn not in key_map:
            rec["flags"].append("no_key_row")
        if qn not in sblocks:
            rec["flags"].append("no_solution_header")
    return records, option_markers, table_regions, glyph_audit, anomalies


def grade_and_status(rec: dict) -> tuple[str, str, list[str]]:
    """(q_id_grade, qa_status, qa_reasons) — deterministic.

    RESOLVED_ANCHORED: question header + key row + solution header all
    printed for this q_no (the normal case; the three printed anchors
    agree by construction of the census).
    RESOLVED: fewer than three printed anchors.
    """
    anchors = sum(bool(rec[k]) for k in
                  ("q_header_page", "key_page", "s_header_page"))
    grade = GRADE_RESOLVED_ANCHORED if anchors == 3 else GRADE_RESOLVED

    missing = []
    if not rec["question_text"].strip():
        missing.append("question_text")
    if len(rec["options"]) < 4 or not all(
            str(rec["options"].get(l, "")).strip() for l in "ABCD"):
        missing.append("options")
    if not rec["correct_option"]:
        missing.append("correct_option")
    if not rec["solution_text"].strip():
        missing.append("solution_text")

    reasons = [f for f in rec["flags"] if f != "unknown_glyph"]
    if "unknown_glyph" in rec["flags"]:
        reasons.append("unknown_glyph")
    if missing:
        return grade, QA_INCOMPLETE, reasons + [f"missing:{m}" for m in missing]
    if reasons:
        return grade, QA_REVIEW_NEEDED, reasons
    return grade, QA_READY, []


def chapter_title(book: Book, file_start: int, scan: ChapterScan,
                  toc_title: str) -> str:
    """Full chapter heading from the chapter's first page (20pt Arial
    Bold in these books); falls back to the TOC row (which the book
    prints truncated with '...')."""
    first_q = scan.question_headers[0][1].pos if scan.question_headers else None
    parts = []
    for ln in book.page(file_start).lines:
        if first_q and ln.pos >= first_q:
            break
        if ln.sizes and max(ln.sizes) >= TITLE_MIN_SIZE:
            parts.append(ln.text.strip())
    if parts:
        return " ".join(parts).strip()
    return toc_title

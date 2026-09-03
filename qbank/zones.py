"""
Chapter zoning.

Every MARROW ED8 chapter has the same printed anatomy:

    chapter title
    Question 1: ... Question N: ...        <- questions zone
    Answer Key                             <- key table zone
      Question No.   Correct Option
      1  d / 2  c / ...                    (two COLUMNS — in raw text the
                                            number and the letter extract
                                            as separate lines, so the key
                                            is parsed by BASELINE
                                            geometry, never by regex)
    Detailed Explanations
    Solution to Question 1: ...            <- solutions zone

Zone borders are (page, y) positions, not whole pages — the Answer Key
frequently starts mid-page under the last question's options, and the
first Solution header can sit on the same page as the last key rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textlayer import Book, Line, word_rows

RE_QUESTION_HDR = re.compile(r"^Question\s+(\d+)\s*:\s*$", re.I)
RE_SOLUTION_HDR = re.compile(r"^Solution\s+to\s+Question\s+(\d+)\s*:\s*$", re.I)
RE_ANSWER_KEY = re.compile(r"^Answer\s*Key\s*$", re.I)
RE_KEY_COL_HDR = re.compile(r"Question\s*No\.?|Correct\s*Option", re.I)
RE_DETAILED = re.compile(r"^Detailed\s+Explanations?\s*$", re.I)


@dataclass
class KeyRow:
    q_no: int
    letter: str            # lowercase a-d as printed
    page: int


@dataclass
class ChapterScan:
    question_headers: list = field(default_factory=list)   # [(qn, Line)]
    key_rows: list = field(default_factory=list)           # [KeyRow]
    solution_headers: list = field(default_factory=list)   # [(qn, Line)]
    q_zone_end: tuple | None = None        # (page, y) of "Answer Key" line
    s_zone_start: tuple | None = None      # (page, y) of first Solution hdr
    chapter_end: tuple | None = None       # (page, +inf) sentinel
    anomalies: list = field(default_factory=list)          # [str]

    # -- intervals ------------------------------------------------------
    def question_blocks(self) -> list[tuple[int, tuple, tuple]]:
        """[(qn, start_pos, end_pos)] — header line -> next header (last:
        up to the Answer Key marker). A block's interval owns every line
        and every figure whose (page, y) falls inside it, including the
        MARROW case where the figure prints ABOVE the stem on the next
        page while the header sits at the bottom of the previous one."""
        blocks = []
        hdrs = self.question_headers
        for i, (qn, ln) in enumerate(hdrs):
            start = ln.pos
            end = hdrs[i + 1][1].pos if i + 1 < len(hdrs) else (
                self.q_zone_end or self.chapter_end)
            blocks.append((qn, start, end))
        return blocks

    def solution_blocks(self) -> list[tuple[int, tuple, tuple]]:
        blocks = []
        hdrs = self.solution_headers
        for i, (qn, ln) in enumerate(hdrs):
            start = ln.pos
            end = hdrs[i + 1][1].pos if i + 1 < len(hdrs) else self.chapter_end
            blocks.append((qn, start, end))
        return blocks


def _key_rows_in_range(book: Book, start: tuple, end: tuple) -> tuple[list[KeyRow], list[str]]:
    """Baseline-geometry parse of the answer-key table between two
    (page, y) positions. A key row is one baseline whose words reduce to
    [number, letter]. Tolerates the column header repeating on
    continuation pages and single-line 'N L' layouts alike."""
    rows: list[KeyRow] = []
    anomalies: list[str] = []
    for pg in range(start[0], end[0] + 1):
        pd = book.page(pg)
        for wr in word_rows(pd):
            y = min(w.y0 for w in wr)
            pos = (pg, y)
            if pos < start or pos >= end:
                continue
            texts = [w.text.strip() for w in wr]
            if len(texts) == 2 and texts[0].isdigit() and \
                    re.fullmatch(r"[a-dA-D]", texts[1]):
                rows.append(KeyRow(int(texts[0]), texts[1].lower(), pg))
            elif len(texts) == 1 and re.fullmatch(r"\d{1,3}\s+[a-dA-D]", texts[0]):
                n, l = texts[0].split()
                rows.append(KeyRow(int(n), l.lower(), pg))
            else:
                joined = " ".join(texts).strip()
                if (RE_KEY_COL_HDR.search(joined)      # repeated column hdr
                        or RE_DETAILED.match(joined)   # zone furniture
                        or RE_ANSWER_KEY.match(joined)):
                    continue
                if joined:
                    anomalies.append(
                        f"answer-key zone line not a key row (p{pg}): "
                        f"{joined[:60]!r}")
    return rows, anomalies


def scan_chapter(book: Book, file_start: int, file_end: int) -> ChapterScan:
    scan = ChapterScan()
    scan.chapter_end = (file_end + 1, 0.0)     # past-the-end sentinel
    lines = book.chapter_lines(file_start, file_end)

    key_line = None
    first_sol = None
    for ln in lines:
        t = ln.text.strip()
        m = RE_QUESTION_HDR.match(t)
        if m and key_line is None:
            scan.question_headers.append((int(m.group(1)), ln))
            continue
        if RE_ANSWER_KEY.match(t) and key_line is None:
            key_line = ln
            scan.q_zone_end = ln.pos
            continue
        m = RE_SOLUTION_HDR.match(t)
        if m and key_line is not None:
            scan.solution_headers.append((int(m.group(1)), ln))
            if first_sol is None:
                first_sol = ln
                scan.s_zone_start = ln.pos

    # anomalies: zone markers
    if key_line is None:
        scan.anomalies.append("no 'Answer Key' marker in chapter")
        scan.q_zone_end = scan.chapter_end
    if not scan.question_headers:
        scan.anomalies.append("no 'Question N:' headers in chapter")
    if not scan.solution_headers:
        scan.anomalies.append("no 'Solution to Question N:' headers in chapter")

    # anomalies: numbering must be exactly 1..N in print order
    qns = [qn for qn, _ in scan.question_headers]
    if qns and qns != list(range(1, len(qns) + 1)):
        scan.anomalies.append(
            f"question headers not contiguous 1..{len(qns)}: {qns}")
    sns = [qn for qn, _ in scan.solution_headers]
    if sns and sns != list(range(1, len(sns) + 1)):
        scan.anomalies.append(
            f"solution headers not contiguous 1..{len(sns)}: {sns}")

    if key_line is not None and first_sol is not None:
        rows, key_anoms = _key_rows_in_range(
            book, scan.q_zone_end, scan.s_zone_start)
        scan.key_rows = rows
        scan.anomalies.extend(key_anoms)
        kq = [r.q_no for r in rows]
        if sorted(kq) != list(range(1, len(kq) + 1)):
            scan.anomalies.append(
                f"answer key not contiguous 1..{len(kq)}: {sorted(kq)}")
        if len(set(kq)) != len(kq):
            dupes = sorted({q for q in kq if kq.count(q) > 1})
            scan.anomalies.append(f"duplicate answer-key rows: {dupes}")

    return scan

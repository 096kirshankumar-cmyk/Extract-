"""
Contents-table parsing and page-offset detection.

The MARROW ED8 books print a contents table on the first pages:

    Chapter    Title                                            Page
      1        Chemistry of Carbohydrates, Amino sugars and...   4

In raw text mode the three columns extract as separate lines, so the
table is parsed GEOMETRICALLY: a TOC row is one visual baseline whose
words are [chapter_no, ...title..., start_page].

`detect_page_offset` proves the file-page -> printed-page offset from
the books' own footer numbers instead of trusting a hand-entered
config value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .textlayer import Book, word_rows

TOC_SCAN_PAGES = 12        # leading pages to scan for the contents table


@dataclass
class Chapter:
    chapter_no: int
    chapter_title: str     # from the TOC row (may carry the book's "...")
    printed_start: int     # printed page number shown in the TOC
    file_start: int = 0    # 1-based PDF file page (filled by caller)
    file_end: int = 0


_INT = re.compile(r"^\d{1,4}$")


def _toc_rows(book: Book) -> list[tuple[int, str, int]]:
    rows = []
    for pg in range(1, min(TOC_SCAN_PAGES, book.total_pages) + 1):
        for wr in word_rows(book.page(pg)):
            if len(wr) < 3:
                continue
            first, last = wr[0].text.strip(), wr[-1].text.strip()
            if not (_INT.fullmatch(first) and _INT.fullmatch(last)):
                continue
            title = " ".join(w.text for w in wr[1:-1]).strip()
            # title words only (guards against pure numeric baselines)
            if not title or title.replace(".", "").isdigit():
                continue
            rows.append((int(first), title, int(last)))
    return rows


def _longest_chapter_run(rows: list[tuple[int, str, int]]) -> list[tuple[int, str, int]]:
    """The TOC is the longest run where chapter numbers step by exactly 1
    and page numbers strictly increase (same defensive idea as the old
    extract_toc_chapters, minus the OCR)."""
    best = []
    cur = []
    for r in rows:
        if cur and (r[0] != cur[-1][0] + 1 or r[2] <= cur[-1][2]):
            if len(cur) > len(best):
                best = cur
            cur = []
        cur.append(r)
    if len(cur) > len(best):
        best = cur
    return best


def parse_toc(book: Book) -> list[Chapter]:
    run = _longest_chapter_run(_toc_rows(book))
    if not run:
        raise ValueError("no contents table found in the first "
                         f"{TOC_SCAN_PAGES} pages")
    if run[0][0] != 1:
        raise ValueError(f"TOC run does not start at chapter 1 (starts {run[0][0]})")
    return [Chapter(no, title, page) for no, title, page in run]


def detect_page_offset(book: Book) -> int:
    """offset = file_page - printed_page, proven from footer numbers.

    A footer is the printed page number standing alone near the bottom
    of the page. We sample the leading pages and take the majority
    offset; a book whose text layer lacks footers raises instead of
    guessing.
    """
    votes: dict[int, int] = {}
    for pg in range(1, min(12, book.total_pages) + 1):
        pd = book.page(pg)
        for l in pd.lines + pd.footers:
            t = l.text.strip()
            if t.isdigit() and l.y1 > pd.height * 0.88:
                off = pg - int(t)
                votes[off] = votes.get(off, 0) + 1
    if not votes:
        raise ValueError("cannot auto-detect page_offset: no footer page "
                         "numbers found — pass --page-offset explicitly")
    return max(votes.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]


def assign_file_ranges(chapters: list[Chapter], offset: int, total_pages: int) -> None:
    prev_printed = None
    for ch in chapters:
        if prev_printed is not None and ch.printed_start <= prev_printed:
            raise ValueError(f"TOC pages not increasing at chapter {ch.chapter_no}")
        prev_printed = ch.printed_start
        ch.file_start = ch.printed_start + offset
        if ch.file_start < 1 or ch.file_start > total_pages:
            raise ValueError(f"chapter {ch.chapter_no} start page "
                             f"{ch.file_start} outside the PDF (1..{total_pages})")
    for i, ch in enumerate(chapters):
        ch.file_end = (chapters[i + 1].file_start - 1 if i + 1 < len(chapters)
                       else total_pages)
        if ch.file_end < ch.file_start:
            raise ValueError(f"chapter {ch.chapter_no} has an empty page range")

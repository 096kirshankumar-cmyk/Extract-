"""
Ruled-table reconstruction: geometry -> cells -> logical tables.

Pipeline (this module replaces "flattened text -> regex -> markdown"):

    ruled box (vector rules)
      -> column edges (vertical rules) and row bands (horizontal rules)
      -> lines assigned to (band, column) cells by bbox geometry
      -> cross-line word reconstruction INSIDE a cell, decided by
         layout evidence only:
           * previous line fills the column's measured fill edge
             (max x1 of the column = the typesetter's text extent)
             AND next line starts lowercase  -> join WITHOUT space
             ("medial su" + "rface" = "medial surface",
              "C2,C" + "3" = "C2,C3",  "Grad" + "e 1" = "Grade 1")
           * trailing hyphen                 -> join, hyphen kept
           * anything else                   -> join WITH a space
             ("middle" + "ear" = "middle ear")
      -> camel-boundary space repair for text-layer lost spaces
         ("antihelixSome" -> "antihelix Some"; guarded so pH, IgG,
         mOsm, B12 are never split)
      -> glyph repair (shared frozen rule table)
      -> cross-page continuation merge (same column geometry on the
         next page + repeated header, or last-box-on-page ->
         first-box-on-next-page flow) into ONE logical table
      -> pipe-markdown + validation

Never a blanket whitespace/regex normalisation: every join is decided
per line pair from coordinates and counted for audit.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import glyphs

# camel boundary: >=2 lowercase, then an uppercase that starts a
# lowercase run. Never splits pH / IgG / mOsm / B12 / VLDL.
_CAMEL = re.compile(r"(?<=[a-z]{2})(?=[A-Z][a-z])")
_LONG_TOKEN = re.compile(r"[A-Za-z]{12,}")
_ALPHA = re.compile(r"[A-Za-z]+")


def build_vocab(book) -> tuple:
    """Book-wide evidence: lowercase word counts + adjacent-word pair
    counts from the raw layer (visual rows). The space repairs below
    only fire when the book itself prints the other form elsewhere —
    document-internal evidence, no external knowledge, no blanket
    whitespace regex."""
    from .textlayer import word_rows
    words: Counter = Counter()
    pairs: Counter = Counter()
    for pg in range(1, book.total_pages + 1):
        for wr in word_rows(book.page(pg)):
            toks = [w.text for w in wr if _ALPHA.fullmatch(w.text)]
            for t in toks:
                words[t.lower()] += 1
            for a, b in zip(toks, toks[1:]):
                pairs[(a.lower(), b.lower())] += 1
    return words, pairs


_FUNC = frozenset({"the", "not", "of", "a", "an", "in", "on", "at", "is",
                   "or", "and", "to", "for", "with", "per", "by"})


def _repair_token(tok: str, words: Counter, pairs: Counter) -> tuple:
    """Publisher misprints inside ONE token, repaired only with
    book-internal evidence:

      "damage,fetal"       -> "damage, fetal"  (comma glued: both parts
                                are printed words, glued form is not)
      "Increasedpulmonary" -> "Increased pulmonary" (two common words,
                                glued form never/rarely printed, spaced
                                phrase printed elsewhere)"""
    m = re.fullmatch(r"([A-Za-z]{3,}),([A-Za-z]{3,})", tok)
    if (m and words.get(m.group(1).lower(), 0) >= 1
            and words.get(m.group(2).lower(), 0) >= 1
            and words.get((m.group(1) + m.group(2)).lower(), 0) == 0):
        return f"{m.group(1)}, {m.group(2)}", 1
    if tok.isalpha() and len(tok) >= 8 and words.get(tok.lower(), 0) == 0:
        for i in range(3, len(tok) - 2):
            h, t = tok[:i], tok[i:]
            if (words.get(h.lower(), 0) >= 2 and words.get(t.lower(), 0) >= 2
                    and pairs.get((h.lower(), t.lower()), 0) >= 1):
                return f"{h} {t}", 1
    return tok, 0


def _repair_tokens(parts: list, words: Counter, pairs: Counter) -> tuple:
    """Token-stream repair for one cell line (see _repair_token), plus:

      "o fcancer" -> "of cancer"  (short non-word fragment whose head
                                   completes a common word)
      "Theprobability" -> "The probability" / "notdepend" -> "not depend"
                   (function prefix + common remainder; glued form
                    never printed as a real word elsewhere)"""
    res, nfix = [], 0
    i = 0
    while i < len(parts):
        tok = parts[i]
        if (i + 1 < len(parts) and tok.isalpha() and 1 <= len(tok) <= 2
                and words.get(tok.lower(), 0) <= 3
                and words.get(parts[i + 1].lower(), 0) <= 1):
            nxt = parts[i + 1]
            hit = None
            for k in range(1, min(3, len(nxt) - 2)):
                if (words.get((tok + nxt[:k]).lower(), 0) >= 2
                        and words.get(nxt[k:].lower(), 0) >= 2):
                    hit = k
                    break
            if hit:
                res.append(tok + nxt[:hit])
                res.append(nxt[hit:])
                nfix += 1
                i += 2
                continue
        core, punct = tok, ""
        while core and core[-1] in ",.;:!?)]":
            punct = core[-1] + punct
            core = core[:-1]
        fixed, nf = _repair_token(core, words, pairs)
        if (nf == 0 and core.isalpha() and len(core) >= 8
                and words.get(core.lower(), 0) <= 1):
            for j in range(3, len(core) - 1):
                h, t2 = core[:j], core[j:]
                thr = 1 if h.lower() in _FUNC else 2
                if ((len(t2) >= 4 and t2[0].islower()
                     and words.get(t2.lower(), 0) >= thr)
                    or (t2.lower() in _FUNC and len(t2) >= 2
                        and words.get(h.lower(), 0) >= 2)):
                    if (words.get(h.lower(), 0) >= 2 or h.lower() in _FUNC):
                        fixed, nf = f"{h} {t2}", 1
                        break
        nfix += nf
        res.append(fixed + punct)
        i += 1
    return res, nfix


# ------------------------------------------------------------------ rules

def _segments(book, pg: int):
    """Long horizontal / vertical rule segments on a page."""
    p = book.doc[pg - 1]
    h, v = [], []
    for it in p.get_drawings():
        for item in it["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                seg = (min(a.x, b.x), min(a.y, b.y),
                       max(a.x, b.x), max(a.y, b.y))
            elif item[0] == "re":
                r = item[1]
                seg = (r.x0, r.y0, r.x1, r.y1)
            else:
                continue
            if seg[3] - seg[1] < 2.5 and seg[2] - seg[0] > 30:
                h.append(seg)
            elif seg[2] - seg[0] < 2.5 and seg[3] - seg[1] > 8:
                v.append(seg)
    return h, v


def grid(book, pg: int, box):
    """(column edges, row rule ys) of one ruled box, from its own rules.

    Empty row list means "no reliable row rules" — the caller falls
    back to one markdown row per baseline (v2.0 behaviour)."""
    h, v = _segments(book, pg)
    tol = 4.0
    cols = sorted({round(s[0], 1) for s in v
                   if s[1] >= box[1] - tol and s[3] <= box[3] + tol
                   and box[0] - tol <= s[0] <= box[2] + tol})
    if len(cols) < 2 or cols[0] > box[0] + 12 or cols[-1] < box[2] - 12:
        cols = [round(box[0], 1), round(box[2], 1)]
    rows = sorted({round(s[1], 1) for s in h
                   if s[0] >= box[0] - tol and s[2] <= box[2] + tol
                   and box[1] - tol <= s[1] <= box[3] + tol})
    if len(rows) < 2 or rows[0] > box[1] + 12 or rows[-1] < box[3] - 12:
        rows = []
    return cols, rows


def _box_lines(book, pg: int, box):
    pd = book.page(pg)
    out = []
    for l in pd.lines:
        ycen = (l.y0 + l.y1) / 2
        if (box[0] - 2 <= l.x0 <= box[2] + 2
                and box[1] - 3 <= ycen <= box[3] + 3):
            out.append(l)
    return out


# ------------------------------------------------------------------ box

@dataclass
class BoxTable:
    page: int
    box: tuple
    cols: list                    # column edge xs
    rows: list                    # cell matrix (list of rows of str)
    header: tuple                 # first row (for continuation tests)
    line_joins: int = 0
    camel_fixes: int = 0
    vocab_fixes: int = 0
    warnings: list = field(default_factory=list)


def _join_decision(prev, nxt, fill_x1, fill_reaches_edge, vocab=None) -> str:
    """'glue' (no space), 'hyphen' (no space, keep '-') or 'space'.

    A mid-word split only happens when the typesetter ran out of room:
    the line must fill the column (fill edge) AND the column's fill
    edge must itself reach near the column's right rule. In a column
    of short lines nothing ever had to split mid-word, so every wrap
    there is a deliberate word boundary."""
    t = prev.text.rstrip()
    n = nxt.text.lstrip()
    if not t or not n:
        return "space"
    if t.endswith("-"):
        return "hyphen"
    if t[-1] in ".;:!?":
        return "space"
    if vocab is not None:
        # wrapped fragment the flush heuristic cannot decide: glue when
        # the book's own vocabulary says the concatenation is a real
        # word at least as common as each part alone, with at least
        # one part too rare to be a deliberate standalone word
        # ("fl"+"ow", "Atri"+"al", "inc"+"reasing", "ductu"+"s").
        words = vocab[0]
        a, b = t.split()[-1], n.split()[0]
        ca, cb = words.get(a.lower(), 0), words.get(b.lower(), 0)
        combo = words.get((a + b).lower(), 0)
        if a.isalpha() and b.isalpha() and combo >= 2:
            if combo >= ca and combo >= cb and min(ca, cb) <= 2:
                return "glue"
            if (len(a) == 1 or len(b) == 1) and min(ca, cb) <= 2:
                return "glue"
    flush = fill_reaches_edge and prev.x1 >= fill_x1 - 2.5
    if not flush:
        return "space"
    if t[-1] == "," and n[0].isdigit():
        return "glue"          # "...," + "3"
    if len(t) >= 2 and t[-2] == "," and n[0].isdigit():
        return "glue"          # "C2,C" + "3"  (comma-list continuation)
    if t[-1].isalnum() and n[0].islower():
        return "glue"          # "su" + "rface", "Grad" + "e 1"
    return "space"


def build_box(book, pg: int, box, counts, vocab=None) -> BoxTable:
    """Cell matrix of ONE ruled box with in-cell line reconstruction."""
    cols, row_ys = grid(book, pg, box)
    lines = _box_lines(book, pg, box)
    ncols = len(cols) - 1
    bt = BoxTable(page=pg, box=tuple(box), cols=list(cols),
                  rows=[], header=())
    if not lines:
        return bt

    def col_of(x0):
        c = 0
        for i in range(ncols):
            if x0 >= cols[i] - 4:
                c = i
        return c

    def band_of(ycen):
        for i in range(len(row_ys) - 1):
            if row_ys[i] - 3 <= ycen <= row_ys[i + 1] + 3:
                return ("r", i)
        # outside row rules or no row rules: per-baseline fallback
        return ("y", round(ycen / 3.0))

    # measured fill edge per column (typesetter's text extent)
    fill = [0.0] * ncols
    for l in lines:
        fill[col_of(l.x0)] = max(fill[col_of(l.x0)], l.x1)
    # a column can only force mid-word splits when its fill edge
    # actually reaches near the right rule (12pt ~ cell padding + slack)
    fill_reaches = [fill[c] >= cols[c + 1] - 12 for c in range(ncols)]

    # per column: visual-order lines -> cells (band groups, joined)
    percol = {c: [] for c in range(ncols)}
    for l in sorted(lines, key=lambda l: (round(l.y0 / 3.0), l.x0)):
        percol[col_of(l.x0)].append(l)

    cells = {}          # (bandkey, col) -> text
    band_order = []     # unique bandkeys in visual order
    for c, lns in percol.items():
        cur_band, cur, prev_line = None, None, None
        for l in lns:
            bk = band_of((l.y0 + l.y1) / 2)
            txt = l.text.strip()
            if not txt:
                continue
            if cur is None or bk != cur_band:
                cur_band, cur, prev_line = bk, txt, l
                cells[(bk, c)] = cur
                if bk not in band_order:
                    band_order.append(bk)
                continue
            how = _join_decision(prev_line, l, fill[c], fill_reaches[c],
                                 vocab)
            if how == "space":
                cur = cur + " " + txt
            else:
                cur = cur + txt
                bt.line_joins += 1
            cells[(bk, c)] = cur
            prev_line = l
    band_order.sort(key=lambda bk: (bk[0] != "r", bk[1]))

    # matrix + camel repair + glyph repair + validation warnings
    matrix = []
    for bk in band_order:
        row = []
        for c in range(ncols):
            t = cells.get((bk, c), "")
            if t:
                fixed, n = _CAMEL.subn(" ", t)
                if n:
                    bt.camel_fixes += n
                    t = fixed
                if vocab is not None:
                    # fixed point: chained glues ("theinfrat...") peel
                    # one repair per pass
                    for _ in range(3):
                        fixed_parts, nf = _repair_tokens(
                            t.split(" "), vocab[0], vocab[1])
                        bt.vocab_fixes += nf
                        t = " ".join(fixed_parts)
                        if not nf:
                            break
                t = glyphs.repair(t, counts)
                for tok in _LONG_TOKEN.findall(t):
                    bt.warnings.append(f"suspect_lost_space:{tok[:20]}")
            row.append(t)
        if any(row):
            matrix.append(row)
    bt.rows = matrix
    bt.header = tuple(matrix[0]) if matrix else ()
    return bt


# ------------------------------------------------------- logical tables

@dataclass
class LogicalTable:
    table_id: str
    markdown: str
    chunks: list                       # [(page, box)] in reading order
    header_deduplicated: bool
    line_joins: int
    camel_fixes: int
    vocab_fixes: int
    cross_page: bool
    warnings: list

    @property
    def source_pages(self) -> list:
        return [pg for pg, _ in self.chunks]

    def as_record(self) -> dict:
        return {
            "type": "table",
            "markdown": self.markdown,
            "table_id": self.table_id,
            "source_pages": self.source_pages,
            "merged_continuation": self.cross_page,
            "header_deduplicated": self.header_deduplicated,
            "extraction": "ruled_grid_geometry",
            "validation": {
                "status": "warnings" if self.warnings else "ok",
                "line_joins": self.line_joins,
                "camel_space_fixes": self.camel_fixes,
                "vocab_space_fixes": self.vocab_fixes,
                "warnings": sorted(set(self.warnings))[:8],
            },
        }


def _markdown(matrix) -> str:
    md = []
    for i, row in enumerate(matrix):
        md.append("| " + " | ".join(row) + " |")
        if i == 0:
            md.append("|" + "---|" * len(row))
    return "\n".join(md)


class ChapterTables:
    """Logical-table registry for one chapter.

    Boxes are chained into logical tables; a block that touches any box
    of a chain gets THAT logical table (one record, one id) plus one
    render region per contributing box."""

    def __init__(self, book, chapter_no: int, first_page: int,
                 last_page: int, vocab=None):
        self.book = book
        self.chapter_no = chapter_no
        self.vocab = vocab
        self.boxes = []                 # ordered [(pg, box)]
        for pg in range(first_page, min(last_page, book.total_pages) + 1):
            for bx in sorted(book.page(pg).table_boxes, key=lambda b: b[1]):
                self.boxes.append((pg, tuple(bx)))
        self._bt_cache: dict = {}
        self._own_counts = Counter()
        self._chain_of: dict = {}       # (pg, box) -> chain index
        self._chains = self._build_chains()
        self._lt_cache: dict = {}
        self._counter = 0

    def _bt(self, pg, box, counts=None) -> BoxTable:
        key = (pg, box)
        if key not in self._bt_cache:
            self._bt_cache[key] = build_box(
                self.book, pg, box,
                counts if counts is not None else self._own_counts,
                self.vocab)
        return self._bt_cache[key]

    def _continues(self, i) -> str | None:
        """Does box i+1 continue box i? -> 'dedup' | 'keep' | None."""
        (pg, box), (npg, nbox) = self.boxes[i], self.boxes[i + 1]
        if npg != pg + 1:
            return None
        bt, nbt = self._bt(pg, box), self._bt(npg, nbox)
        if len(bt.cols) != len(nbt.cols):
            return None
        if any(abs(a - b) > 6 for a, b in zip(bt.cols, nbt.cols)):
            return None
        if bt.header and bt.header == nbt.header:
            return "dedup"                       # repeated header
        # no repeated header: document-flow continuation — previous box
        # is the page's last, next box the next page's first, starting
        # in the top quarter, previous box ending in the bottom 40%.
        ph = self.book.page(npg).height
        last_on_pg = all(p != pg or b[1] <= box[1] for p, b in self.boxes)
        first_on_npg = all(p != npg or b[1] >= nbox[1]
                           for p, b in self.boxes)
        if (last_on_pg and first_on_npg and nbox[1] <= ph * 0.25
                and box[3] >= ph * 0.6):
            return "keep"
        return None

    def _build_chains(self) -> list:
        chains, cur = [], []
        for i, (pg, box) in enumerate(self.boxes):
            if not cur:
                cur = [(pg, box, None)]
                continue
            mode = self._continues(i - 1)
            if mode:
                cur.append((pg, box, mode))
            else:
                chains.append(cur)
                cur = [(pg, box, None)]
        if cur:
            chains.append(cur)
        for ci, ch in enumerate(chains):
            for pg, box, _m in ch:
                self._chain_of[(pg, box)] = ci
        return chains

    def lookup(self, pg: int, box, counts=None) -> LogicalTable | None:
        key = (pg, tuple(box))
        ci = self._chain_of.get(key)
        if ci is None:
            return None
        if ci in self._lt_cache:
            return self._lt_cache[ci]
        self._counter += 1
        tid = f"{self.chapter_no:03d}-T{self._counter:02d}"
        chunks = self._chains[ci]
        matrix, joins, camels, vfix, warns = [], 0, 0, 0, []
        dedup = False
        for k, (cpg, cbox, mode) in enumerate(chunks):
            bt = self._bt(cpg, cbox, counts)
            joins += bt.line_joins
            camels += bt.camel_fixes
            vfix += bt.vocab_fixes
            warns += bt.warnings
            rows = bt.rows
            if k and mode == "dedup" and rows and rows[0] == matrix[0]:
                rows = rows[1:]
                dedup = True
            if k == 0:
                matrix = list(rows)
            else:
                matrix += rows
        lt = LogicalTable(
            table_id=tid, markdown=_markdown(matrix) if matrix else "",
            chunks=[(p, b) for p, b, _ in chunks],
            header_deduplicated=dedup, line_joins=joins,
            camel_fixes=camels, vocab_fixes=vfix,
            cross_page=len(chunks) > 1, warnings=warns)
        self._lt_cache[ci] = lt
        return lt

    def materialised(self) -> list:
        """All logical tables actually referenced so far."""
        return list(self._lt_cache.values())

    def stats(self) -> dict:
        lts = list(self._lt_cache.values())
        return {
            "logical_tables": len(lts),
            "ruled_boxes": len(self.boxes),
            "cross_page_merges": sum(1 for t in lts if t.cross_page),
            "header_dedups": sum(1 for t in lts if t.header_deduplicated),
            "line_joins": sum(t.line_joins for t in lts),
            "camel_space_fixes": sum(t.camel_fixes for t in lts),
            "vocab_space_fixes": sum(t.vocab_fixes for t in lts),
            "tables_with_warnings": sum(1 for t in lts if t.warnings),
        }

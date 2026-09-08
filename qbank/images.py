"""
Figure claiming — the same geometry the text uses.

A figure whose (page, y-center) falls inside a block's interval belongs
to that block (README rule carried over from v1, including the MARROW
case where the figure prints above its stem on the next page while the
header sits at the bottom of the previous one). Inside a question block,
each option owns the interval from its marker to the next marker, so a
figure printed under option C's text is claimed as OPTION C. Solution
blocks claim SOLUTION. Anything in the answer-key zone, above the first
header, or between chapters is an ORPHAN — reported, never silently
dropped.

Embedded images are extracted once per unique byte-content (md5), so a
multi-draw figure appears as several manifest rows sharing one file —
exactly the v1 contract note in FORMAT.md.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

import pymupdf
from PIL import Image

from . import config
from .textlayer import Book
from .zones import ChapterScan


@dataclass
class Claim:
    file: str                  # rel to assets/questions: BIO/BIO-001-009_Q_01.webp
    q_no: int
    kind: str                  # Q | SOL | OPT
    option_letter: str | None
    page: int                  # extraction page (1-based file page)
    xref: int
    shared: bool = False       # same bytes already written for another placement
    table_id: str | None = None  # links a table clip render to its
                                 # structured markdown record
    merged: int = 0            # >0: figure was stored as N adjacent
                               # image placements, stitched into one
                               # clip render of their union bbox


@dataclass
class OrphanImage:
    page: int
    reason: str
    xref: int


@dataclass
class ChapterImageReport:
    claims: list = field(default_factory=list)        # [Claim] reading order
    orphans: list = field(default_factory=list)       # [OrphanImage]
    skipped: list = field(default_factory=list)       # [(page, reason)]
    table_renders: int = 0                            # claims that are table clips
    tables_suppressed: int = 0     # table boxes not rendered: structured


class ImageStore:
    """Byte-dedup store writing webp assets under assets/questions/."""

    def __init__(self, assets_dir, subject: str):
        self.assets_dir = assets_dir
        self.subject = subject
        self._by_hash: dict[str, str] = {}     # md5 -> rel file

    def _to_webp(self, data: bytes) -> bytes | None:
        try:
            img = Image.open(io.BytesIO(data))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=config.WEBP_QUALITY, method=4)
            return buf.getvalue()
        except Exception:
            return None

    def _raw_bytes(self, book: Book, xref: int) -> bytes | None:
        """Original image bytes, with soft mask composed in when present."""
        raw = book.doc.extract_image(xref)
        if not raw or not raw.get("image"):
            return None
        data = raw["image"]
        smask = raw.get("smask") or 0
        if smask:
            try:
                base = Image.open(io.BytesIO(data))
                mask = Image.open(io.BytesIO(
                    book.doc.extract_image(smask)["image"]))
                if base.mode != "RGB":
                    base = base.convert("RGB")
                if mask.size != base.size:
                    mask = mask.resize(base.size)
                base.putalpha(mask.convert("L"))
                out = io.BytesIO()
                base.save(out, "PNG")
                data = out.getvalue()
            except Exception:
                pass
        return data

    def put(self, book: Book, xref: int, rel_name: str) -> tuple[str | None, bool]:
        """Write (or reuse) the asset for xref. Returns (rel_file, shared).
        rel_name is used only for a NEW file; identical bytes reuse the
        first file name (multi-draw contract)."""
        data = self._raw_bytes(book, xref)
        if data is None:
            return None, False
        h = hashlib.md5(data).hexdigest()
        if h in self._by_hash:
            return self._by_hash[h], True
        webp = self._to_webp(data)
        if webp is None:
            return None, False
        pil = Image.open(io.BytesIO(webp))
        if (len(webp) < config.MIN_IMAGE_BYTES
                or min(pil.size) < config.MIN_IMAGE_DIM):
            return None, False
        out = self.assets_dir / rel_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(webp)
        self._by_hash[h] = rel_name
        return rel_name, False

    def put_render(self, book: Book, page_no: int, bbox,
                   rel_name: str) -> str | None:
        """Deterministic clip-render of a page region (used for printed
        tables: the pixels ARE the book, zero transcription risk)."""
        try:
            pix = book.doc[page_no - 1].get_pixmap(
                clip=pymupdf.Rect(*bbox), dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=config.WEBP_QUALITY, method=4)
            webp = buf.getvalue()
        except Exception:
            return None
        if len(webp) < config.MIN_IMAGE_BYTES:
            return None
        out = self.assets_dir / rel_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(webp)
        return rel_name


def _adjacent_groups(imgs, tol: float = 4.0) -> list[list]:
    """Cluster placements whose bboxes touch/overlap.

    The publisher stores some figures as 2-3 interlocking image
    pieces (main panel + side/bottom strips). Each piece on its own
    is a cut fragment — the visual figure is the union, so claiming
    must happen per cluster, never per piece."""
    groups: list[list] = []
    for im in imgs:
        b = im.bbox
        hit = None
        for g in groups:
            if any(b[0] <= o.bbox[2] + tol and b[2] >= o.bbox[0] - tol
                   and b[1] <= o.bbox[3] + tol and b[3] >= o.bbox[1] - tol
                   for o in g):
                hit = g
                break
        if hit is None:
            hit = []
            groups.append(hit)
        hit.append(im)
    return groups


def _union_bbox(imgs):
    return (min(i.bbox[0] for i in imgs), min(i.bbox[1] for i in imgs),
            max(i.bbox[2] for i in imgs), max(i.bbox[3] for i in imgs))


def _option_intervals(qblocks: dict, option_markers: dict) -> dict:
    """{qn: [(letter, start_pos, end_pos)]} — each option owns marker ->
    next marker (option D: marker -> end of the question block)."""
    out = {}
    for qn, (bs, be) in qblocks.items():
        marks = option_markers.get(qn) or []
        ivals = []
        for i, (letter, pos) in enumerate(marks):
            end = marks[i + 1][1] if i + 1 < len(marks) else be
            ivals.append((letter.upper(), pos, end))
        out[qn] = ivals
    return out


def claim_chapter_images(book: Book, scan: ChapterScan, store: ImageStore,
                         subject: str, chapter_id: str,
                         first_page: int, last_page: int,
                         option_markers: dict,
                         table_regions: dict | None = None,
                         structured_table_ids: set | None = None,
                         ) -> ChapterImageReport:
    rep = ChapterImageReport()
    qblocks = {qn: (s, e) for qn, s, e in scan.question_blocks()}
    sblocks = {qn: (s, e) for qn, s, e in scan.solution_blocks()}
    opt_iv = _option_intervals(qblocks, option_markers)
    slots: dict[str, int] = {}

    for pg in range(first_page, last_page + 1):
        pd = book.page(pg)
        for group in _adjacent_groups(pd.images):
            bbox = _union_bbox(group)
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area > config.FULLPAGE_AREA_FRAC * pd.width * pd.height:
                rep.skipped.append((pg, "fullpage_background"))
                continue
            pos = (pg, (bbox[1] + bbox[3]) / 2.0)

            zone = qn = None
            for q, (s, e) in qblocks.items():
                if s <= pos < e:
                    zone, qn = "Q", q
                    break
            if zone is None:
                for q, (s, e) in sblocks.items():
                    if s <= pos < e:
                        zone, qn = "SOL", q
                        break
            if zone is None:
                in_key = (scan.q_zone_end and scan.q_zone_end <= pos
                          and (not scan.s_zone_start
                               or pos < scan.s_zone_start))
                rep.orphans.append(OrphanImage(
                    pg,
                    "image_in_answer_key_zone" if in_key
                    else "image_outside_blocks",
                    group[0].xref))
                continue

            kind, letter = zone, None
            if zone == "Q":
                for l, s, e in opt_iv.get(qn, []):
                    if s <= pos < e:
                        kind, letter = "OPT", l
                        break

            slot_key = (f"{subject}/{chapter_id}-{qn:03d}_"
                        f"{kind}" + (f"_{letter}" if letter else ""))
            slots[slot_key] = slots.get(slot_key, 0) + 1
            rel_name = f"{slot_key}_{slots[slot_key]:02d}.webp"
            if len(group) == 1:
                rel_file, shared = store.put(book, group[0].xref, rel_name)
                merged = 0
            else:
                # one figure stored as interlocking pieces — stitch by
                # rendering the union bbox (pixel-exact, includes any
                # vector overlay the book drew across the seams)
                rel_file = store.put_render(book, pg, bbox, rel_name)
                shared, merged = False, len(group)
            if rel_file is None:
                rep.skipped.append((pg, "too_small_or_undecodable"))
                continue
            rep.claims.append(Claim(
                file=rel_file, q_no=qn, kind=kind, option_letter=letter,
                page=pg, xref=group[0].xref, shared=shared, merged=merged))

    # printed tables: deterministic clip renders, owned by their block.
    # One render per contributing BOX (a cross-page logical table gets
    # one render per page, all tagged with its shared table_id); a box
    # referenced by several blocks renders once. A box whose logical
    # table was SUCCESSFULLY structured (non-empty markdown) is NOT
    # rendered: the structured table is the asset, an image copy would
    # duplicate the same content (decision purely from the pipeline's
    # own table metadata — embedded figures/photos are claimed above
    # by xref and are untouched).
    rendered_boxes = set()
    for (qn, zone), regions in sorted((table_regions or {}).items()):
        for region in regions:
            pg, bbox, table_id = region
            if table_id in (structured_table_ids or set()):
                rep.tables_suppressed += 1
                continue
            if (pg, tuple(bbox)) in rendered_boxes:
                continue
            rendered_boxes.add((pg, tuple(bbox)))
            slot_key = f"{subject}/{chapter_id}-{qn:03d}_{zone}"
            slots[slot_key] = slots.get(slot_key, 0) + 1
            rel_name = f"{slot_key}_{slots[slot_key]:02d}.webp"
            rel_file = store.put_render(book, pg, bbox, rel_name)
            if rel_file is None:
                rep.skipped.append((pg, "table_render_failed"))
                continue
            rep.table_renders += 1
            rep.claims.append(Claim(
                file=rel_file, q_no=qn, kind=zone, option_letter=None,
                page=pg, xref=-1, shared=False, table_id=table_id))
    return rep

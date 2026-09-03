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
                         table_regions: dict | None = None) -> ChapterImageReport:
    rep = ChapterImageReport()
    qblocks = {qn: (s, e) for qn, s, e in scan.question_blocks()}
    sblocks = {qn: (s, e) for qn, s, e in scan.solution_blocks()}
    opt_iv = _option_intervals(qblocks, option_markers)
    slots: dict[str, int] = {}

    for pg in range(first_page, last_page + 1):
        pd = book.page(pg)
        for im in pd.images:
            area = (im.bbox[2] - im.bbox[0]) * (im.bbox[3] - im.bbox[1])
            if area > config.FULLPAGE_AREA_FRAC * pd.width * pd.height:
                rep.skipped.append((pg, "fullpage_background"))
                continue

            zone = qn = None
            for q, (s, e) in qblocks.items():
                if s <= im.pos < e:
                    zone, qn = "Q", q
                    break
            if zone is None:
                for q, (s, e) in sblocks.items():
                    if s <= im.pos < e:
                        zone, qn = "SOL", q
                        break
            if zone is None:
                in_key = (scan.q_zone_end and scan.q_zone_end <= im.pos
                          and (not scan.s_zone_start
                               or im.pos < scan.s_zone_start))
                rep.orphans.append(OrphanImage(
                    pg,
                    "image_in_answer_key_zone" if in_key
                    else "image_outside_blocks",
                    im.xref))
                continue

            kind, letter = zone, None
            if zone == "Q":
                for l, s, e in opt_iv.get(qn, []):
                    if s <= im.pos < e:
                        kind, letter = "OPT", l
                        break

            slot_key = (f"{subject}/{chapter_id}-{qn:03d}_"
                        f"{kind}" + (f"_{letter}" if letter else ""))
            slots[slot_key] = slots.get(slot_key, 0) + 1
            rel_name = f"{slot_key}_{slots[slot_key]:02d}.webp"
            rel_file, shared = store.put(book, im.xref, rel_name)
            if rel_file is None:
                rep.skipped.append((pg, "too_small_or_undecodable"))
                continue
            rep.claims.append(Claim(
                file=rel_file, q_no=qn, kind=kind, option_letter=letter,
                page=pg, xref=im.xref, shared=shared))

    # printed tables: deterministic clip renders, owned by their block
    for (qn, zone), regions in sorted((table_regions or {}).items()):
        for (pg, bbox) in regions:
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
                page=pg, xref=-1, shared=False))
    return rep

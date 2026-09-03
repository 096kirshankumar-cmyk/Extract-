"""
Chapter/book orchestration: scan -> parse -> claim figures -> write.

One chapter is one atomic unit: its seven split files are written only
after its census is computed, and chapter_completeness.json lands last
as the "fully on disk" signal (same rule as v1).
"""

from __future__ import annotations

import time
from collections import Counter

from . import config
from .images import ImageStore, claim_chapter_images
from .parse import build_chapter_records, chapter_title
from .textlayer import Book
from .toc import assign_file_ranges, detect_page_offset, parse_toc
from .writer import (append_jsonl, build_rows, write_chapter_split,
                     write_chapters_json)
from .zones import scan_chapter


def _census_summary(scan, anomalies) -> dict:
    qn = [q for q, _ in scan.question_headers]
    kn = [r.q_no for r in scan.key_rows]
    sn = [s for s, _ in scan.solution_headers]
    n = max(len(qn), len(kn), len(sn))
    contiguous = (qn == list(range(1, len(qn) + 1))
                  and sorted(kn) == list(range(1, len(kn) + 1))
                  and sn == list(range(1, len(sn) + 1)))
    ok = (contiguous and len(qn) == len(kn) == len(sn) == n
          and not anomalies)
    return {
        "question_headers": len(qn), "key_rows": len(kn),
        "solution_headers": len(sn), "expected_n": n,
        "contiguous": contiguous, "ok": ok,
        "anomalies": anomalies,
    }


def _manifest_and_files(claims, subject: str, chapter_no: int):
    """image_files_by_q (row-builder shape), pages_by_file, manifest rows."""
    image_files_by_q: dict = {}
    pages_by_file: dict = {}
    manifest = []
    for c in claims:
        qid = f"{subject}-{chapter_no:03d}-{c.q_no:03d}"
        pages_by_file.setdefault(c.file, c.page)
        entry = image_files_by_q.setdefault(
            c.q_no, {"question": [], "solution": [], "option": {}})
        if c.kind == "Q":
            entry["question"].append(c.file)
            mtype, letter = "QUESTION", None
        elif c.kind == "SOL":
            entry["solution"].append(c.file)
            mtype, letter = "SOLUTION", None
        else:
            entry["option"].setdefault(c.option_letter, []).append(c.file)
            mtype, letter = "OPTION", c.option_letter
        manifest.append({
            "q_id": qid, "type": mtype, "option_letter": letter,
            "file": c.file, "source_pages": [c.page],
            "extraction_page": c.page,
        })
    return image_files_by_q, pages_by_file, manifest


def run_chapter(book: Book, subject: str, ch, store: ImageStore,
                output_root) -> dict:
    chapter_id = f"{subject}-{ch.chapter_no:03d}"
    t0 = time.time()
    scan = scan_chapter(book, ch.file_start, ch.file_end)
    (records, option_markers, table_regions, glyph_audit,
     extra_anoms) = build_chapter_records(book, scan, ch.chapter_no)
    anomalies = list(scan.anomalies) + list(extra_anoms)
    census = _census_summary(scan, anomalies)

    img_rep = claim_chapter_images(
        book, scan, store, subject, chapter_id,
        ch.file_start, ch.file_end, option_markers, table_regions)
    image_files_by_q, pages_by_file, manifest = _manifest_and_files(
        img_rep.claims, subject, ch.chapter_no)

    q_rows, a_rows, s_rows, un_rows = build_rows(
        records, subject, chapter_id, ch.chapter_no,
        image_files_by_q, pages_by_file)

    orphan_rows = [{
        "subject": subject, "chapter_id": chapter_id,
        "source_pages": [o.page], "pass": "IMAGE_CLAIM",
        "reason": o.reason, "fragment": "", "carry_q_no": None,
        "cut_part": None, "last_qn_in_batch": None,
    } for o in img_rep.orphans]

    completeness = write_chapter_split(
        output_root=output_root, subject=subject, chapter_id=chapter_id,
        chapter_no=ch.chapter_no, q_rows=q_rows, a_rows=a_rows,
        s_rows=s_rows, un_rows=un_rows, orphan_rows=orphan_rows,
        manifest_rows=manifest, scan_summary=census,
        glyph_audit=dict(glyph_audit),
        image_report_summary={
            "claimed": len(img_rep.claims),
            "orphans": len(img_rep.orphans),
            "skipped": len(img_rep.skipped),
            "shared_multi_draw": sum(1 for c in img_rep.claims if c.shared),
            "table_renders": img_rep.table_renders,
        })

    # global image-ownership ledger (one row per claim, v1-shaped)
    ledger = [{
        "subject": subject, "chapter_id": chapter_id, "page": c.page,
        "file": c.file,
        "owner": f"{subject}-{ch.chapter_no:03d}-{c.q_no:03d}",
        "slot": (f"option_{c.option_letter.lower()}"
                 if c.kind == "OPT" else
                 {"Q": "question", "SOL": "solution"}[c.kind]),
        "method": "textlayer_geometry",
        "evidence": ("clip render of printed table region"
                     if c.xref == -1 else
                     f"xref {c.xref} bbox center inside block interval"),
        "confidence": "high", "outcome": "claimed",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "obj_id": None, "final_file": c.file,
    } for c in img_rep.claims]
    append_jsonl(config.DATA_DIR / "image_ownership.jsonl", ledger)

    title = chapter_title(book, ch.file_start, scan, ch.chapter_title)
    dt = time.time() - t0
    return {
        "chapter_id": chapter_id, "subject": subject,
        "chapter_no": ch.chapter_no, "chapter_title": title,
        "questions": len(q_rows), "census_ok": census["ok"],
        "glyph_fixes": sum(glyph_audit.values()),
        "images": len(img_rep.claims), "orphans": len(img_rep.orphans),
        "secs": round(dt, 1),
    }


def run_book(pdf_path: str, subject: str, page_offset="auto",
             chapters_filter: set | None = None, force: bool = False,
             output_root=None) -> dict:
    from . import state as state_mod
    output_root = output_root or config.OUTPUT_ROOT
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    book = Book(pdf_path)
    if page_offset == "auto":
        page_offset = detect_page_offset(book)
    book.set_offset(int(page_offset))
    chapters = parse_toc(book)
    assign_file_ranges(chapters, int(page_offset), book.total_pages)
    print(f"[{subject}] {book.total_pages} pages, offset "
          f"{page_offset:+d}, {len(chapters)} chapters from the contents table")

    state = state_mod.load_state()
    prog = state_mod.progress(state, subject)
    store = ImageStore(config.ASSETS_DIR, subject)
    chapters_out = []
    results = []
    for ch in chapters:
        chapter_id = f"{subject}-{ch.chapter_no:03d}"
        chapters_out.append({
            "chapter_id": chapter_id, "subject": subject,
            "chapter_no": ch.chapter_no, "chapter_title": ch.chapter_title,
        })
        if chapters_filter and ch.chapter_no not in chapters_filter:
            continue
        if not force and chapter_id in prog["chapters_done"]:
            print(f"[{subject}] {chapter_id}: already done (resume)")
            continue
        res = run_chapter(book, subject, ch, store, output_root)
        results.append(res)
        for c in chapters_out:
            if c["chapter_id"] == chapter_id:
                c["chapter_title"] = res["chapter_title"]
        flag = "" if res["census_ok"] else "  <<< CENSUS FAILED"
        print(f"[{subject}] {chapter_id}: {res['questions']} questions, "
              f"{res['images']} images, {res['glyph_fixes']} glyph fixes, "
              f"{res['secs']}s{flag}")
        if chapter_id not in prog["chapters_done"]:
            prog["chapters_done"].append(chapter_id)
        state_mod.save_state(state)

    write_chapters_json(config.DATA_DIR / "chapters.json", chapters_out)
    write_chapters_json(config.SUBJECTS_DIR / subject / "chapters.json",
                        [c for c in chapters_out if c["subject"] == subject])
    book.close()

    bad = [r["chapter_id"] for r in results if not r["census_ok"]]
    return {"results": results, "chapters_run": len(results),
            "census_failures": bad,
            "total_questions": sum(r["questions"] for r in results)}

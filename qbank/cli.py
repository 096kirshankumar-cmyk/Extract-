"""
Command line:

    python3 -m qbank run --book BIO [--chapters 1,3-5] [--force]
    python3 -m qbank run --pdf path.pdf --subject BIO [--page-offset auto|N]
    python3 -m qbank export [--dest path.zip]
    python3 -m qbank status

No API keys. No network. Deterministic.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config, state as state_mod
from .export import build_final_zip, gate_final_zip
from .run import run_book


def _parse_chapters(spec: str) -> set[int]:
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def cmd_run(args) -> int:
    if args.book:
        books = config.load_books()
        if args.book not in books:
            print(f"book {args.book!r} not in {config.BOOKS_FILE} "
                  f"(have: {sorted(books)})", file=sys.stderr)
            return 2
        entry = books[args.book]
        subject = args.subject or entry.get("subject", args.book)
        pdf = config.resolve_book_path(entry)
        offset = (args.page_offset if args.page_offset is not None
                  else entry.get("page_offset", "auto"))
    else:
        if not args.pdf or not args.subject:
            print("either --book NAME or (--pdf PATH --subject CODE)",
                  file=sys.stderr)
            return 2
        subject = args.subject
        pdf = args.pdf
        offset = args.page_offset if args.page_offset is not None else "auto"
    if offset != "auto":
        offset = int(offset)

    filt = _parse_chapters(args.chapters) if args.chapters else None
    res = run_book(pdf, subject, page_offset=offset,
                   chapters_filter=filt, force=args.force)
    print(f"\n[{subject}] done: {res['chapters_run']} chapter(s), "
          f"{res['total_questions']} questions, "
          f"census failures: {res['census_failures'] or 'none'}")
    return 1 if res["census_failures"] else 0


def cmd_export(args) -> int:
    res = build_final_zip(config.OUTPUT_ROOT, dest=args.dest)
    if not res["ok"]:
        print(f"REFUSED: {res['why']}", file=sys.stderr)
        return 3
    print(f"final_export.zip -> {res['path']}")
    print(json.dumps(res["receipt"], indent=2))
    return 0


def cmd_status(args) -> int:
    state = state_mod.load_state()
    print(json.dumps(state.get("pdf_progress", {}), indent=2))
    gate = gate_final_zip(config.OUTPUT_ROOT)
    print("export gate:", "OPEN (zip can build)" if not gate["locked"]
          else f"LOCKED — {gate['why']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qbank", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="extract one book")
    p_run.add_argument("--book", help="key in books.json (e.g. BIO)")
    p_run.add_argument("--pdf", help="explicit pdf path")
    p_run.add_argument("--subject", help="subject code (e.g. BIO)")
    p_run.add_argument("--page-offset", default=None,
                       help="'auto' (default) or an integer")
    p_run.add_argument("--chapters", help="subset, e.g. '1,3-5'")
    p_run.add_argument("--force", action="store_true",
                       help="re-extract chapters already marked done")
    p_run.set_defaults(fn=cmd_run)

    p_exp = sub.add_parser("export", help="build final_export.zip")
    p_exp.add_argument("--dest", default=None)
    p_exp.set_defaults(fn=cmd_export)

    p_st = sub.add_parser("status", help="resume state + export gate")
    p_st.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

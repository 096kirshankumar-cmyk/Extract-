"""Paths, constants and the book registry. No logic here."""

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Output root — same env override the old pipeline used (Railway volume).
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_DIR", str(REPO_ROOT / "qbank_output")))
DATA_DIR = OUTPUT_ROOT / "data"
ASSETS_DIR = OUTPUT_ROOT / "assets" / "questions"
SPLIT_DIR = OUTPUT_ROOT / "split"
SUBJECTS_DIR = OUTPUT_ROOT / "subjects"
STATE_FILE = OUTPUT_ROOT / "state.json"

BOOKS_FILE = Path(os.environ.get("QBANK_BOOKS", str(REPO_ROOT / "books.json")))

# ---- image claiming -------------------------------------------------------
MIN_IMAGE_BYTES = 1500      # <1.5 KB webp is virtually always noise (rule
                            # carried over from the old pipeline)
MIN_IMAGE_DIM = 24          # px — icons/bullets are never MCQ figures
FULLPAGE_AREA_FRAC = 0.90   # an image covering >90% of the page is a
                            # background scan, not a figure — never claim
WEBP_QUALITY = 90

# ---- provenance vocabulary ------------------------------------------------
PROV_TEXT_LAYER = "TEXT_LAYER"

# q_id grades — same vocabulary as the old split layer.
GRADE_RESOLVED_ANCHORED = "RESOLVED_ANCHORED"   # all printed anchors agree
GRADE_RESOLVED = "RESOLVED"                     # partial printed anchors
GRADE_PROVISIONAL = "PROVISIONAL"               # should not occur in v2

# qa_status values — same vocabulary as the old review layer.
QA_READY = "READY"
QA_REVIEW_NEEDED = "REVIEW_NEEDED"
QA_INCOMPLETE = "INCOMPLETE"

# unresolved-qid reasons — subset of the old UNRESOLVED_REASONS vocabulary
# that is still meaningful for a deterministic pipeline.
UNRESOLVED_REASONS = frozenset({
    "no_anchor_at_all",
    "answer_q_no_not_in_printed_key",
    "solution_q_no_not_in_printed_header",
    "missing_question_for_solution",
})


def load_books() -> dict:
    """books.json: {"BIO": {"path": ..., "page_offset": "auto"|int, ...}}"""
    if not BOOKS_FILE.exists():
        return {}
    return json.loads(BOOKS_FILE.read_text())


def resolve_book_path(entry: dict) -> str:
    p = Path(entry["path"]).expanduser()
    if p.is_absolute():
        return str(p)
    for base in (REPO_ROOT, REPO_ROOT / "pdfs", Path.cwd(), Path.cwd() / "pdfs"):
        cand = base / p
        if cand.exists():
            return str(cand)
    env_dir = os.environ.get("QBANK_PDFS_DIR")
    if env_dir:
        for cand in (Path(env_dir) / p, Path(env_dir) / p.name):
            if cand.exists():
                return str(cand)
    raise FileNotFoundError(
        f"book pdf not found: {entry['path']} (looked in {REPO_ROOT}, "
        f"{REPO_ROOT/'pdfs'}, cwd, $QBANK_PDFS_DIR)")

# Jdon Extract — deterministic text-layer pipeline (v2)

Extracts question banks from MARROW ED8 PDFs **with a corrected text
layer** into the v1 `final_export.zip` contract (`FORMAT.md`).

v2 replaces the old OCR/Gemini/review-layer pipeline. The text layer of
the corrected books is authoritative, so extraction is now **fully
deterministic — zero LLM calls, zero network, pure CLI**. Nothing is
ever guessed: a row is written only when the printed page proves it,
and the census gate refuses to build the export zip when any chapter's
question headers, answer-key rows and solution headers do not match.

## What v2 does differently

| v1 (deleted)                  | v2 (this tree)                                    |
| ----------------------------- | ------------------------------------------------- |
| Gemini OCR + vision fallback  | none — text layer only (`provenance: TEXT_LAYER`) |
| Flask dashboard, review queue | pure CLI (`python -m qbank`)                      |
| LLM table reconstruction      | ruled-grid detection + verbatim cell reads        |
| heuristic glyph regex         | font-proven sentinels + ordered, audited rules    |
| `REVIEW_NEEDED` by LLM flag   | `REVIEW_NEEDED` only for unresolvable glyphs      |

The output contract is unchanged: `split/{SUBJ}/{SUBJ}-NNN/` with
`questions.jsonl`, `answers.jsonl`, `solutions.jsonl`,
`unresolved_qids.jsonl`, `orphans.jsonl`, `image_manifest.jsonl`,
`chapter_completeness.json`; `assets/questions/{SUBJ}/*.webp`;
`data/chapters.json`, `data/image_ownership.jsonl`;
`subjects/{SUBJ}/chapters.json`; `final_export.zip` +
`REVIEW_RECEIPT.json`. See `FORMAT.md`.

## Quickstart

```bash
pip install -r requirements.txt

# put the book PDFs in pdfs/ (gitignored) or point QBANK_PDFS_DIR at a dir
python -m qbank run --book BIO              # extract (resumable per chapter)
python -m qbank status                      # progress + census summary
python -m qbank export                      # gate + build final_export.zip
```

`books.json` registers books:

```json
{ "BIO": { "path": "pdfs/Biochemistry_ed8_CLEAN_corrected.pdf",
           "page_offset": "auto" } }
```

`page_offset: "auto"` proves the file-page ↔ printed-page offset from
the books' own footer numbers; pass `--page-offset N` to override.

## How extraction works

1. **Contents table** (`qbank/toc.py`) — parsed geometrically from word
   baselines; chapter file ranges come from the TOC + proven offset.
2. **Zones** (`qbank/zones.py`) — each chapter splits at the printed
   `Answer Key` baseline and the first `Solution to Question N:` header.
   Key rows (`15 a`) are paired geometrically from the same baseline.
3. **Blocks** (`qbank/parse.py`) — one `Question N:` header anchors one
   record; stem/options/solution text is reflowed from visual-order
   baselines (line-continuation fragments merge back by y/3 bucket +
   x-order).
4. **Tables** — a table exists **iff the book drew rules**: long
   horizontal rules stitched by verticals form a grid box
   (`textlayer._ruled_table_boxes`). Baselines inside a box move to the
   row's `tables` field as pipe-markdown (columns from repeating line-x0
   anchors, cells read baseline-by-baseline — never reordered) and the
   grid is also rendered pixel-exact at 200 dpi as a WebP
   (`*_SOL_NN.webp`, manifest `xref = -1`). Unruled pseudo-tables and
   bullet lists stay verbatim prose.
5. **Glyphs** (`qbank/glyphs.py`) — the corrected books still carry two
   broken font mappings (Symbol `°`, AdobePiStd `■`). Spans of those
   fonts are sentinelised at extraction time, then repaired by an
   ordered, enumerated rule table covering exactly the 67 artifacts
   catalogued for Biochemistry ED8. Every application is counted per
   question (`glyph_fix_counts` in `chapter_completeness.json`); real
   ArialMT degree signs are never touched; anything unmatched is
   restored verbatim and the row is flagged `REVIEW_NEEDED` — never
   guessed.
6. **Images** (`qbank/images.py`) — embedded figures are claimed by
   (page, y-center) inside the owning block or option interval;
   orphans go to `orphans.jsonl`. Nothing is dropped silently.
7. **Gate** (`qbank/export.py`) — the zip is built only when every
   chapter's census is contiguous and no row is `REVIEW_NEEDED`.

## Verification

`scripts/verify_extraction.py` re-derives an independent visual-order
ground truth from the PDF and checks, for every shipped row: stem,
options, key letter and solution text are exact substrings (up to the
documented glyph repairs), every table's markdown appears verbatim,
and every printed baseline is accounted for by some row (coverage).

```bash
python scripts/verify_extraction.py <book.pdf> BIO qbank_output
# verified 28 chapters, 582 questions, 232 embedded images + 39 table renders
# worst content coverage: 0.9942 — ALL CHECKS PASSED
```

## Tests

```bash
python -m pytest tests/ -q        # 21 tests, no fixtures needed
```

- `test_glyphs.py` — every artifact class of the frozen repair table.
- `test_ruled_tables.py` — grid detection/rejection + markdown order on
  synthetic PDFs.
- `test_mini_book.py` — end-to-end run on a synthetic two-chapter book
  (TOC, offset detection, zones, key pairing, Symbol-font repair,
  image claim, ruled-table markdown + render, split-file contract).

## Environment

`OUTPUT_DIR` overrides the output root (default `qbank_output/`);
`QBANK_PDFS_DIR` adds a PDF search directory; `QBANK_BOOKS` overrides
`books.json`. Python ≥ 3.10, deps: PyMuPDF, Pillow.

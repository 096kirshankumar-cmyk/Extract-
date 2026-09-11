# Final Presentation / Typography Pass — ENT (source-verified)

Content-blind whitespace/layout normalization only. Source PDF is the
authority; no word, number, symbol or punctuation character was added,
removed or replaced anywhere (proved by hash regression below).

## A. Problems found (baseline = pre-pass code, full ENT re-extraction)

QA scan of the baseline output (703 Q + 2812 options + 703 solutions +
35 tables):

| class | hits |
|---|---|
| space before sentence punctuation (`(EAC) .`, `below :`) | **167** |
| word:Word / word,Word / word;Word / paren glue / ordinal glue / notation glue / multi-space / pageNum+`Solution to Question` | 0 (already covered by the earlier `spacing_fix` rules or never produced by this book's text layer) |

Plus one source-verified extraction artifact found during the
source-vs-output audit: ENT-001-013 printed `(otocysts) .` while the PDF
text layer prints `(otocysts).` — reflow was joining a line break before
punctuation with a space.

## B. Problems fixed (new rules in `spacing_fix`, qbank/tables.py)

1. **Space-hug**: `[^\s] +[.,;:?!]` → punctuation hugs the preceding
   word (`(EAC) .` → `(EAC).`, `below :` → `below:`, `nerves :` →
   `nerves:`). 119 fields.
2. **`)` never spaced before punctuation**: `\) *(?=[A-Za-z0-9(])` —
   the old paren rule re-inserted the space before `.`; fixed.
3. **Comma / semicolon / sentence collision** (`membrane.It` →
   `membrane. It`) — mechanism added; this book had 0 live targets.
4. **Bracket inside padding** (`( parasellar )` → `(parasellar)`),
   **prose multi-space collapse** (`the   patient` → `the patient`) —
   mechanisms added; 0 live targets in ENT.

## C. Intentionally NOT changed (source-fidelity decisions)

- **`(C2,C3)` stays `(C2,C3)`** — the source prints it compact and
  never prints `C2, C3`; the GPT-suggested comma-space rule was
  implemented, caught by the source audit, and **reverted**. Table
  cells keep the old evidence-based comma split (`_repair_token`).
- **`1:1000`, `10:30`, `1.3:1`, `5,000`** — digit:ratio/time forms
  guarded by lookarounds; untouched.
- **`HbA1c`, `C2H5OH`, `vitamin B12`, `J.K.Rowling`, `v1.2Beta`,
  `B/L`** — notation/initial guards; untouched.
- **Hyphen ↔ en-dash** — never converted (no source proof per
  instance); the existing digit-range en-dash join in tables is
  evidence-based and unchanged.
- **Prose bullets/headings** — preserved verbatim; no prose→bullet
  conversion anywhere.
- **6 windows** where the SOURCE itself prints the stray space
  (`3 nerves : 49 •`, `otosclerosis : • Stapedial 161 •`, `17:1 .`):
  removed as unambiguous whitespace artifacts — whitespace-only,
  characters unchanged.

## D. Files / functions modified

- `qbank/tables.py` — `spacing_fix()` (space-hug rule, paren-after
  guard, comma/semicolon revert note, docstring).
- `tests/test_tables.py` — new regression assertions.

## E. Regression tests added

`tests/test_tables.py::test_spacing_fix_ordinals_notations_brackets`:
space-hug, `(EAC).`, `below:`, sentence collision, bracket padding,
multi-space collapse + keep-list (`1.3:1`, `1:1000`, `J.K.Rowling`,
`v1.2Beta`, `5,000`, `(C2,C3)`, `B/L`, `e.g.` …). Full suite:
**107 passed, 5 skipped** (incl. the 36 real-book spacing regressions
against the ENT PDF vocab).

## F. Before/after metrics (full pipeline re-run, same PDF)

| metric | baseline | final |
|---|---|---|
| chapters / census failures | 38 / 0 | 38 / 0 |
| question rows | 703 | 703 |
| option rows | 2812 | 2812 |
| unique tables | 35 | 35 |
| answer rows / solution rows | 703 / 703 | 703 / 703 |
| image manifest refs | 324 | 324 |
| asset files (webp) | 282 | 282 |
| manifest hash | equal | equal |
| **fields compared** | **4956** | |
| fields changed | **123** | |
| **non-whitespace changes** | **0** | |
| duplicate/missing assets | 0 / 0 | 0 / 0 |

Post-pass QA scan over the FINAL output (12 typography classes incl.
footer/page-number collisions): **0 remaining hits**.

## G. Source-vs-output proof samples (SOURCE → OLD → NEW)

| source PDF text layer | old output | new output |
|---|---|---|
| `…mandibular nerve). 48 Anatomical…` (48 = page footer) | `nerve) . Anatomical` | `nerve). Anatomical` |
| `…(smiling incision). 115 Note:` | `(smiling incision) . Note:` | `(smiling incision). Note:` |
| `…(otocysts).\nThe cells…` | `(otocysts) . The cells` | `(otocysts). The cells` |
| `…formed by 3 nerves : 49 •…` (source prints stray space + footer) | `3 nerves : •` | `3 nerves: •` |
| `…(areal ratio) is 17:1 . Effective…` | `17:1 . Effective` | `17:1. Effective` |
| `…nerve (C2,C3) supplies…` | `(C2,C3)` | `(C2,C3)` — unchanged, compact form is source-true |

Audit method: every one of the 123 changed fields was diffed
(character-level, whitespace-stripped equality enforced = 0 content
changes) and its changed window searched verbatim in the PDF text
layer (117/123 exact or page-footer-tolerant matches; the remaining 6
manually verified above as source-printed stray spaces around
unambiguous punctuation).

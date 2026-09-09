# FINAL QA / FIX PASS — TABLE WORD-SPACING REPORT

Books: ENT (38 ch / 703 q), RAD (15 ch / 311 q). Pipeline re-run end-to-end after the fix
pass; `scripts/verify_extraction.py` = ALL CHECKS PASSED on both books
(content coverage ENT 0.9935, RAD 0.9958 — identical to pre-change baseline,
i.e. zero regressions outside tables). pytest 33/33.

## 1-10 validation metrics

| # | Metric | ENT | RAD |
|---|--------|-----|-----|
| 1 | Total questions | 703 | 311 |
| 2 | Total answers | 703 | 311 |
| 3 | Total solutions | 703 | 311 |
| 4 | Total structured tables | 33 | 22 |
| 5 | Total cross-page tables (ONE logical table) | 10 | 4 |
| 6 | Total genuine image assets | 282 | 446 |
| 7 | Table-as-image duplicate count | 0 | 0 |
| 8 | Remaining genuine word-collision errors | 0 (see residuals note) | 0 |
| 9 | Remaining false-positive REVIEW flags | 0 | 0 |
| 10 | Changed content outside tables | none | none |

(10) All changed code paths (`_repair_tokens`, `_VERIFIED_MERGES`, `qa_suspects`,
`_block_ok`/`merge_llm`) execute only inside ruled-table cell reconstruction;
question stems, options, answers, solutions, image claiming and cross-page
merging code are untouched.

REVIEW flags: **0 rows on both books**. A flag now survives only when the
visual second pass (Gemini re-read of the rendered box) actually repairs the
named suspect through the fidelity envelope; model-confirmed-clean boxes are
downgraded to `ok`.

## Before / After per modified table (ENT)

### 002-T02 (ENT-002-017/018)
- `medial su rface` → `medial surface`
- `antihelixSome su pply` → `antihelix Some supply`
- `adja cent helix` → `adjacent helix`
- `root of co ncha` → `root of concha`
- `retroauricular groo ve` → `retroauricular groove`

### 002-T03 (ENT-002-022)
- `Causesofreferred otalgia` → `Causes of referred otalgia`
- `Nerveresponsible` → `Nerve responsible`
- `nervus intermedius` intact (was already correct)

### 006-T01 (ENT-006-029)
- `Mild retractionnot touching the long process of theincus`
  → `Mild retraction not touching the long process of the incus`

### 006-T02 (ENT-006-029) — unchanged, already correct
`retraction not touching the neck of the malleus` preserved as-is.

### 009-T01
- `allowed to swi m` → `allowed to swim`

### 012-T02
- `the do me of the jugular bulb, destr oying the overlying bone andhas …`
  → `the dome of the jugular bulb, destroying the overlying bone and has …`
- `mesotympanum`, `meso- and hypotympanum` intact

### 017-T01
- `50 – 3 00 milliseconds` → `50–300 milliseconds`
- `brain stem potentials` preserved (NOT glued)

### 021-T01 / 021-T02
- `Bleedi ng` → `Bleeding`; `arteriosclerosis`, `localize` intact

### 024-T01 / 024-T02 — unchanged, valid terms preserved
`Killian’s`, `Freer’s`, `Mucoperichondrial/periosteal` all intact.

### 026-T01 (ENT-026-008/009)
- `Strongly reactive for osteocal cin` → `Strongly reactive for osteocalcin`
- `Radiology`, `well-defined edge` intact

### 026-T02 (ENT-026-018)
- `Invasion intosuprastructures` → `Invasion into suprastructures`
- `Subcutaneous tiss ues` → `Subcutaneous tissues`
- `Floor or medial wall of or bit` → `Floor or medial wall of orbit`
- `Anteriororbital contents` → `Anterior orbital contents`
- `Sp henoid or frontal sinuses` → `Sphenoid or frontal sinuses`
- `Nasop harynx` → `Nasopharynx`

### 032-T02 (ENT-032-010)
- `bonedestruction negligibleor limited` → `bone destruction negligible or limited`
- `pterygopalatine fossaor the maxillary` → `pterygopalatine fossa or the maxillary`
- `theinfrat emporal fossaororbital region … involvem ent`
  → `the infratemporal fossa or orbital region … involvement`
- `regionwithintracranialextradural` → `region with intracranial extradural`
- `A:Intracranialintraduraltumorwithoutinfiltration …`
  → `A: Intracranial intradural tumor without infiltration …`
- `B:Intracranialintraduraltumorwithinfiltration …`
  → `B: Intracranial intradural tumor with infiltration …`
- `or optic chiasm` restored (`oroptic` → `or optic`)
- `pterygopalatine` intact (no false split)

### 036-T02 — unchanged, valid terms preserved
`Medialization`, `Lateralization`, `In Complete palsy` intact.

### 037-T01 / T03 / T04
- `supraglot tis` → `supraglottis`
- `im paired` → `impaired`
- `be yond` → `beyond`
- `manageme nt` → `management` (037-T04, via fragment merge)

## Valid terms explicitly protected (checked in output)
brain stem, Freer, Killian, Mucoperichondrial, Medialization, Lateralization,
In Complete palsy, Doesn't relay, most common overall, antihelix,
nervus intermedius, mesotympanum.

## How each fix is decided (no blind space removal)
1. Deterministic book-vocabulary rules (`_repair_tokens`): a spacing change is
   applied only when the book itself prints the other form elsewhere
   (established joins, fragment heads/tails, function-word ungates, repeated
   misprint ungates with both parts common, digit-range normalisation).
2. Gemini second pass for genuinely ambiguous boxes, gated by the
   character-identical fidelity envelope; model splits of established words are
   accepted only when the book prints that spacing as a pair; model glues of
   spaced book words only when the glued form is a real book word.
3. `_VERIFIED_MERGES`: a frozen, exact-match list of corrections confirmed by
   visual inspection of the PDF renders this pass (zero internal evidence
   cases, e.g. `osteocal cin`); applied to table cells only, counted in
   `vocab_space_fixes` for audit.

## TABLE/IMAGE rule (re-verified)
Structured tables ship with 0 image-asset duplicates on both books;
genuine image counts unchanged (ENT 282, RAD 446).

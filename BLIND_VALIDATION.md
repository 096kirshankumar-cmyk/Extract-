# BLIND VALIDATION — MARROW ED8 Anatomy (fresh PDF, never used in any test)

**Version tested:** frozen `9da0e0a` (zero pipeline code changes for this run; only a
`books.json` entry `ANA` was registered).
**Input:** `uploads/Anatomy_ed8_blind.pdf` — 1217 pages, no bookmarks, printed TOC,
**uncorrected** text layer (deliberately raw — the ENT/RAD PDFs were "CLEAN_corrected").
**Run:** cold LLM cache, ~45 min → **63 chapters, 1115 questions, census failures: none,
1115/1115 COMPLETE+READY, 269 genuine images, 107 unique tables, 3 tables in REVIEW.**

**Method:** seeded random sample (seed 20260909): 8 tables, 12 questions, 10 suspect
regions; full-output scan using only the book's own raw vocabulary (glued = token absent
from vocab splittable into two vocab words w≥3; split = printed-joined form w≥3 while the
separated pair is rare; punctuation-gap regexes); 13 pages rendered at 210 dpi and compared
to the JSON by eye. **Nothing was fixed; everything below is reported.**

**Correction of my own earlier metric:** the first sampler print said `cross_page=0`.
That read a nonexistent key — the real key is `merged_continuation`:
**15 unique tables (19 rows) ARE cross-page merged.** All cross-page claims below use the
real key and were re-verified visually.

---

## A. Pipeline-INDUCED errors (output differs from a clean print)

| # | Where | Print (render) | Output JSON | Class |
|---|-------|----------------|-------------|-------|
| A1 | 016-T01 (p301) | "Found **everywhere** except clitoris…" | "Found **every where** except…" | false split introduced by the pipeline (raw layer prints "everywhere"; "every where" appears nowhere in the book) |

This is the only pipeline-induced character error found in the whole sampled set.

## B. Printed corruption preserved UNREPAIRED (output == the book's own garbage)

The raw PDF itself is corrupt here (verified in renders and raw layer); the deterministic
rules did not fire because in-book evidence is ≤2 occurrences (conservative by design).

| # | Where | Garbled text preserved in output |
|---|-------|----------------------------------|
| B1 | 017-T05 (p322–323) | "ofuni polar nerve cells", "ofpro prioceptionfrom", "oftouch" (×3 cells), "ofpain", "oblongatatill t he 2nd Cervical Segment", "t he tensor veli palitini" |
| B2 | 017-T02 (p312–314) | "rise tomotor nuclei", "sensory nucleiCN V", "(dorsal horns).The rhombic lip" |
| B3 | 037-T01 (p661–662) | "branches ofinternal thoracic artery", "theacro miothoracicand Superior thoracic arteries" |
| B4 | 032-T01 | "Bilateraljugulodigastricandjugulo-omohyoidnodes" |
| B5 | prose p468 / p1086 / p1198 | "column.These", "muscles,the", "Thick-walled,highly" (printed without the space; output faithful) |

Note B1–B4 are partial: adjacent corruptions WERE repaired by the same rules where
evidence existed ("thor acicarteries"→"thoracic arteries", "andSuperior"→"and Superior",
"2ndto 4thposterior"→"2nd to 4th posterior", "Vermilli on"→"Vermillion",
"areolaTyson' s"→"areola Tyson's").

## C. Structural discrepancy

| # | Where | Finding |
|---|-------|---------|
| C1 | 017-T05 (p322 vs p323) | The book prints **two variant copies of the same table** on consecutive pages (different casing and a reworded Motor-nucleus cell). `_continues` header-dedup treated p323 as a continuation of p322 → merged table contains a **duplicate Mesencephalic row** (both casing variants) and **drops the p323 Motor-nucleus variant row**. This is a false "continuation" merge of related-but-distinct tables — the audit-item-2 failure mode, occurring on a fresh book. |

## D. QA behaviour on the fresh book

- 017-T02, 037-T01 → correctly escalated to REVIEW (real corruption; suspects "tomotor",
  "ofinternal", "miothoracicand").
- 016-T01 → **false-positive** REVIEW (suspects "minora", "sheds" — legitimate words), and
  meanwhile the real induced error (A1) in the same table was not what triggered it.
- No question-level prose region was wrongly auto-corrected anywhere in the sample.

## E. Faithful quirks correctly preserved (NOT errors)

"Fordyce' spot" (print genuinely lacks the s), "C 5" with space (print), "venacava"
(printed variant at p975), "Eccrine(Merocrine)", "palitini" (print's own misspelling,
not spell-corrected ✓).

## F. Clean samples (render-verified, no discrepancy)

- **049-T03** p905→906: genuine cross-page table, merged once, header deduped, all 7 rows
  char-exact incl. "37.5 cm", "2 to 5 cm".
- **011-T01** (p206): "indist inct"/"distin ct"/"appear e mpty" correctly rejoined.
- **008-T01** (p137), **060-T02** (p1161), **046-T01** (p856, incl. printed "Costaland"→
  "Costal and" repair), **001-T02** (p15, "neckDistal"→"neck Distal", "Golgi apparat us"→
  "Golgi apparatus").
- **ANA-002-013** (p22): question + options char-exact; answer key anchor agrees (13→c).
- 016-T01 apart from A1, all printed glued runs ("partof cellsheds offdur ing",
  "Thecell is intactandsecretions", "glandMontgomery") correctly repaired.

---

## Verdict

On a completely fresh, uncorrected 1217-page book the frozen pipeline generalizes:
structure, anchoring, census, figure claiming and cross-page merging all held
(15 true merges, 1 false merge), 6 of 8 sampled tables char-clean, and repairs fired
correctly on dozens of printed corruptions. **Residual discrepancies (unfixed, as
instructed):** 1 pipeline-induced false split (A1), 1 false continuation-merge of variant
tables (C1), 1 false-positive REVIEW (016-T01), and the conservative no-repair class
(B1–B5) where in-book evidence ≤2. None of these were used to tune any rule.

---

# POST-FIX RESULTS (commit ecbfcf1, pushed)

User decision: table spacing/word-split repair Gemini karega (visual), strict
no-word-deletion envelope; deterministic layer evidence-based backstop.

**Changes:**
1. `llm.py PROMPT` (T2): explicit layout-corruption mandate — glued words, mid-word
   breaks — "read like a human, output corrected spacing; NEVER delete/add a word,
   every printed character exactly once; only spaces may change". Cache sig busted.
2. `tables.py` deterministic backstop (all rules document-evidence based):
   - 2-letter FUNC-prefix unglue when survivor w>=5 ("oftouch"->"of touch",
     "tomotor"->"to motor", "ofinternal", "ofpain"); suffix-word guard keeps
     "notable" safe.
   - FUNC+FUNC glue split ("tothe"->"to the").
   - single-letter fragment merge ("t he"->"the") only when join w>=50, outranks
     parts 10x, split spacing printed nowhere.
   - generic peel no longer splits a printed lowercase real word into two common
     words ("everywhere" preserved; "sinusmucosa"-class left to model+QA).
   - `_continues`: repeated header + repeated first row => variant COPY, not
     continuation (p322/323 now 017-T05 + 017-T06, no dup row, no lost row).
   - `_VERIFIED_MERGES` +2 wrap splits with document evidence ("rest less"->
     "restless", "through out"->"throughout"; joins printed, splits never).
3. 5 new regression tests on real ANA evidence (68 total, all pass).

**ANA after (107 tables):** oftouch/ofpain/tomotor/ofinternal/tothe = 0; "t he" = 0;
"every where" = 0 ("everywhere" = 1); variant copies split; 017-T05 row complete,
no word deleted. Residual no-evidence garbles remain (ofuni x2, oblongatatill x2,
theacro, miothoracicand, prioceptionfrom x2) — model didn't visually fix and the
book prints no evidence; 2 model wrong-splits with chars intact ("for ward",
"colour less") = accepted risk of the user-sanctioned spacing policy.
REVIEW escalations 3 -> 16: now mostly the book's own typos/wrap-corruptions
("duoednum", "lu mbar", "cordin") surfaced for human check — conservative, not errors.

**ENT/RAD after:** question/option/solution text hashes IDENTICAL
(646544e850e2 / a516682895c1), verifiers ALL CHECKS PASSED, "rest less"/
"through out"/"tothe"/"t he" gone from tables, REVIEW 2/1 (both genuine residuals:
sinusmucosa, withoutintra). 68/68 tests pass.

---

# FINAL CONTRACT (commit 1b2880f, pushed): "Gemini owns table spacing,
# koi word delete nahi hota"

Per-cell envelope ab simple rule par hai: model cell tabhi deterministic cell
ki jagah leta hai jab **exact same characters** hon (whitespace-insensitive) —
matlab deletion/addition impossible by construction. Spacing arbitration me
model ka un-glue rare glued artifacts (det token printed <=2x, e.g.
"oblongatatill" -> "oblongata till") par pair-evidence ke bina jeet jata hai;
established book words ko todna ("surface" -> "su rface") ya bina evidence ke
glue karna ab bhi reject hota hai (directional guards). Hybrid grid suggestion
ke liye poori table ka char-identical content kaafi hai.

**Final ANA (107 tables):** evidence-backed fixes all retained (oftouch /
ofpain / tomotor / ofinternal / tothe / "t he" / "every where" = 0); variant
copies split (017-T05 + 017-T06). Residuals jinke paas book me zero evidence
hai aur model bhi nahi sudhar paya: ofuni x2, oblongatatill x2, theacro,
miothoracicand, prioceptionfrom x2. Gemini-over-splits (chars intact):
"for ward", "colour less", "T he" x1, "I X" x2. REVIEW queue: 22/107 — sab
genuine book corruption/typos ("duoednum", "lu mbar", "cordin", "embryol
ogical") human check ke liye; koi silent error nahi.

**ENT/RAD:** text hashes IDENTICAL (646544e850e2 / a516682895c1), verifiers
ALL CHECKS PASSED, bad-spacing probes 0; REVIEW 6/1 (genuine residuals).
69/69 tests pass. `_post` ab transient network failures retry karta hai
(book runs ab single fetch-glitch par nahi marte).

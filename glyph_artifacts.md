# Biochemistry_ed8_CLEAN_corrected.pdf — all 67 glyph artifacts

Two broken font mappings survive in the corrected text layer:
- **Symbol font** glyphs extracted as `°` (50 cases) — the font's arrow/dash glyphs have no proper Unicode mapping
- **AdobePiStd font** glyphs extracted as `■` (10 cases) — same problem
- **ArialMT `°`** (7 cases) — these are REAL degree signs (temperatures)

Grouped by what the glyph actually is:

---

## A. REAL degree signs — correct, keep as-is (7)

| # | page | text |
|---|------|------|
| 45 | 306 | "rates double for a 10°C increase in temperature" |
| 46 | 306 | "typically double for a 10°C" |
| 63 | 442 | "90°C) to separate the two strands" (PCR denaturation) |
| 64 | 442 | "anneal to the DNA (typically 50–75°C)" |
| 65 | 442 | "grow at 70–80°C" (thermophilic bacteria) |
| 66 | 448 | "90°C (90-96 degrees Celsius for 3 minutes)" |
| 67 | 449 | "typically at 50-75°C) in order to generate" |

## B. Bond arrow — should be `→` in α1→4 / α(1→6) glycosidic notation (21)

| # | page | text |
|---|------|------|
| 01 | 5 | Q5 option a) "α1 ° 4 glucosidic bond" |
| 02 | 5 | Q5 option b) "α1 ° 6 glucosidic bond" |
| 03 | 5 | Q5 option c) "α1 ° 1 glucosidic bond" |
| 04 | 5 | Q5 option d) "α1 ° 2 glucosidic bond" |
| 06 | 12 | "branching points in the glycogen structure have α1 ° 6" |
| 07 | 13 | solution: "α1 / ° / 4 glucosidic linkage" (line-wrapped) |
| 08 | 13 | solution: "branching by means of α1 ° 6 glucosidic bonds" |
| 12 | 45 | option a) "α(1° 4) linkage; α(1° 6) linkage" (first) |
| 13 | 45 | option a) "α(1° 4) linkage; α(1° 6) linkage" (second) |
| 14 | 45 | option b) "α (1° 6) linkage; α (1° 4) linkage" (first) |
| 15 | 45 | option b) "α (1° 6) linkage; α (1° 4) linkage" (second) |
| 16 | 45 | option c) "α (1° 4) linkage; α (1° 4) linkage" (first) |
| 17 | 45 | option c) "α (1° 4) linkage; α (1° 4) linkage" (second) |
| 18 | 45 | option d) "α (1° 6) linkage; α(1° 6) linkage" (first) |
| 19 | 45 | option d) "α (1° 6) linkage; α(1° 6) linkage" (second) |
| 20 | 51 | "primary glycosidic bond is an α (1° 4) linkage" |
| 21 | 51 | "branch point contains an α (1° 6) linkage" |
| 22 | 52 | "glucose residues from UDP-Glucose, in 1 ° 4" |
| 23 | 53 | "phosphorolytic cleavage of the α1 ° 4 bond" |
| 24 | 53 | "phosphorolytic cleavage of the 1 ° 6 bond" |
| 25 | 56 | "lysosomal enzyme, α (1° 4)-glucosidase" |

## C. Reaction arrow — should be `→` in chemical reactions (17)

| # | page | text |
|---|------|------|
| 09 | 41 | "conversion of fructose-1,6-bisphosphate ° [F6P]" |
| 10 | 41 | "conversion of pyruvate ° [acetyl-CoA]" |
| 11 | 41 | "conversion of 3-phosphoglycerate ° [PGAL]" |
| 26 | 90 | Krebs inputs: "From glucose ° Pyruvate ° [acetyl CoA]" (first) |
| 27 | 90 | Krebs inputs: "From glucose ° Pyruvate °" (second) |
| 28 | 90 | "Fatty acid oxidation ° [acetyl CoA]" |
| 29 | 90 | "Ketogenic amino acid oxidation ° [acetyl CoA]" |
| 30 | 96 | "Pyruvate + NAD+ + CoA ° [Acetyl CoA + NADH + CO2]" (PDH reaction) |
| 31 | 125 | catecholamine synthesis: "Tyrosine ° [Dopa]" |
| 32 | 125 | "Dopa ° [Dopamine]" |
| 33 | 204 | β-oxidation: "(Cn)acyl CoA + FAD + NAD+ + H2O + CoA ° (Cn-2)acyl CoA + FADH2 + NADH + Acetyl CoA" |
| 41 | 271 | heme synthesis: "Succinyl-CoA + glycine ° [ALA]" |
| 42 | 271 | "α-amino-β-ketoadipate + CoA-SH ° [products]" |
| 43 | 306 | ping-pong kinetics: "In the forward reaction, A + A ° [products]" |
| 44 | 306 | "In the reverse reaction, P ° [substrates]" |
| 47 | 308 | "the reaction of E+S ° ES becomes the rate-limiting step" (diffusion-limited enzymes; forward arrow) |
| 48 | 363 | "NADPH + 2O2 ° [products]" |

## D. Direction arrow — should be `→` in 5'→3' / 3'→5' notation (10)

| # | page | text |
|---|------|------|
| 49 | 384 | option a) "3'° 5' Exonuclease" |
| 50 | 384 | option b) "5'° 3' Exonuclease" |
| 51 | 384 | option c) "5'° 3' DNA polymerase" |
| 52 | 384 | option d) "3'° 5' DNA polymerase" |
| 53 | 396 | "Klenow fragment lacks 5' ° 3' exonuclease" |
| 54 | 396 | "with both 3' ° 5' [and ...]" |
| 55 | 396 | "5' and 5' ° 3' [activities]" |
| 56 | 396 | "When the 5' ° 3' exonuclease..." |
| 57 | 399 | "through its 5'° 3' exonuclease activity" |
| 58 | 399 | "3'° 5' exonuclease activity" |

## E. NOT an arrow — actually a dash/hyphen `-` (2)

| # | page | text |
|---|------|------|
| 59 | 414 | mRNA cap: "an unusual 5'° 5' triphosphate linkage" → should be **5'-5'** |
| 62 | 428 | "one of four argonaute proteins (Ago 1° 4)" → should be **Ago1-4** (range dash) |

## F. `■` = Greek delta Δ (6)

| # | page | text |
|---|------|------|
| 34 | 214 | Q: "by the enzyme ■9 desaturase?" → **Δ9 desaturase** |
| 35 | 222 | "converted to oleic acid by the enzyme ■9 desaturase" |
| 36 | 222 | "■9 desaturase is present in the endoplasmic reticulum" |
| 37 | 222 | "■9 desaturase is a mixed-function oxidase" |
| 38 | 222 | "always introduced in the ■9 positions" |
| 39 | 222 | "cannot introduce double bonds beyond ■9" |

## G. `■` = stray/unknown glyph (4) — cannot be resolved deterministically

| # | page | text | likely |
|---|------|------|--------|
| 05 | 5 | Q8 stem: "positive reaction to■ Benedict's test?" (solution on p13 has no glyph: "reaction to Benedict's test") | stray — drop |
| 40 | 264 | Q8 stem: "by-product of the ■reaction catalyzed by cytosolic hydroxymethylbilane synthase" | stray — drop |
| 60 | 426 | "inability of the cell to ■turn off■genes" (first ■) | opening quote " |
| 61 | 426 | "inability of the cell to ■turn off■genes" (second ■) | closing quote " |

---

**Totals:** 7 real degrees (keep) · 48 clear arrows (`→`) · 2 dashes (`-`) · 6 deltas (`Δ`) · 4 stray/quotes

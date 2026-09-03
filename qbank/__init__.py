"""
qbank — deterministic MCQ question-bank extractor (text-layer edition).

Rewritten 2026-09 for CORRECTED text-layer PDFs (MARROW ED8 series).
The previous engine (boundary_phased.py + Gemini crops + OCR fallbacks)
existed because the old PDFs had garbled text layers; that machinery was
the source of hallucinated questions and was REMOVED along with the
review dashboard.

Design rule (unchanged): never guess, never auto-correct. Every field is
either proven from the PDF's own text layer / geometry or flagged. The
one exception is the font-proven glyph repair layer (qbank.glyphs):
Symbol/AdobePi font glyphs are PROVABLY artifacts (the font identity is
the proof), and each replacement is made by an enumerated context rule
with a per-question audit trail.

No LLM, no OCR, no network. Pure deterministic extraction:

    TOC -> chapter page ranges
      -> zones (questions / answer key / solutions) by marker + geometry
        -> blocks (header -> next header) -> verbatim text
          -> figures claimed by the same (page, y) geometry
            -> split/<SUBJ>/<SUBJ>-<NNN>/*.jsonl  (same contract as before)
            -> final_export.zip                   (same tree as FORMAT.md)
"""

__version__ = "2.0.0"

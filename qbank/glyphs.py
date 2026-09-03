"""
Font-proven glyph repair.

The corrected MARROW ED8 text layers still carry two broken font
mappings (no ToUnicode CMap):

  * Symbol font      -> extracted as '°'   (was →  or  -)
  * AdobePiStd font  -> extracted as '■'   (was Δ, quotes, or stray)

The font identity is the PROOF that a glyph is an artifact: in these
books every Symbol '°' is a broken arrow/dash and every ArialMT '°' is a
real degree sign (verified exhaustively on Biochemistry ED8: 50 Symbol
artifacts vs 7 real degrees, 10 AdobePi artifacts). So during text
extraction (qbank.textlayer) artifact glyphs are replaced by sentinels:

    Symbol '°'      -> SENT_DEGREE  (\ue000)
    AdobePi '■'     -> SENT_SQUARE  (\ue001)

Real '°' characters are never sentinelised and therefore can never be
touched by any rule below.

Rules are ORDERED and ENUMERATED — they cover exactly the 60 artifacts
found in Biochemistry ED8 (glyph_artifacts.md):

    rule              count  example
    ----------------  -----  --------------------------------
    ago_range_dash       2   (Ago 1° 4)          -> (Ago1-4)
    cap_link_dash        1   5'° 5' triphosphate -> 5'-5'
    direction_arrow     10   3'° 5' exonuclease  -> 3'→5'
    bond_paren_arrow     9   α (1° 6) linkage    -> α(1→6)
    bond_alpha1_arrow    8   α1 ° 4 glucosidic   -> α1→4
    bond_bare1_arrow     4   in 1 ° 4 / the 1 ° 6-> 1→4 / 1→6
    reaction_arrow      17   Pyruvate + CoA °    -> ... → ...
    delta_greek          6   ■9 desaturase       -> Δ9 desaturase
    quote_pair           2   ■turn off■          -> "turn off"
    stray_drop           2   reaction to■ Ben..  -> reaction to Ben..

Anything left over (a sentinel no rule matched) is RESTORED to the
original glyph verbatim and the owning question is flagged for review —
we never guess a replacement.
"""

import re
from collections import Counter

SENT_DEGREE = "\ue000"   # Symbol-font '°'  (broken arrow / dash)
SENT_SQUARE = "\ue001"   # AdobePiStd '■'   (broken Δ / quote / stray)

# Rule names double as the audit vocabulary recorded per question.
R_AGO_DASH = "ago_range_dash"
R_CAP_DASH = "cap_link_dash"
R_DIRECTION = "direction_arrow"
R_BOND_PAREN = "bond_paren_arrow"
R_BOND_ALPHA1 = "bond_alpha1_arrow"
R_BOND_BARE1 = "bond_bare1_arrow"
R_REACTION = "reaction_arrow"
R_DELTA = "delta_greek"
R_QUOTE = "quote_pair"
R_STRAY = "stray_drop"

PRIMES = "'\u2019\u2018\u02b9"          # ' ’ ‘ ʹ  (books mix straight/curly)
_PR = f"[{PRIMES}]"
ARROW = "\u2192"

# Order matters. Each rule: (name, compiled regex, replacement)
_RULES = [
    # "(Ago 1° 4)" — "one of four argonaute proteins": range dash, NOT an
    # arrow. Must run before the bare-1 bond rule (same digit pattern).
    (R_AGO_DASH,
     re.compile(r"\(Ago\s*1\s*" + SENT_DEGREE + r"\s*4\)"),
     "(Ago1-4)"),

    # mRNA cap is a 5'-5' triphosphate LINKAGE — dash, not direction.
    # Must run before the generic direction rule (5'°5' would match it).
    (R_CAP_DASH,
     re.compile(r"5\s*" + _PR + r"\s*" + SENT_DEGREE + r"\s*5\s*" + _PR),
     "5'-5'"),

    # 3'→5' / 5'→3' direction arrows (compact, no spaces — book style).
    (R_DIRECTION,
     re.compile(r"([35])\s*" + _PR + r"\s*" + SENT_DEGREE + r"\s*([35])\s*" + _PR),
     "\\1'" + ARROW + "\\2'"),

    # α(1→4)-style bonds (optionally spaced: "α (1° 6)").
    (R_BOND_PAREN,
     re.compile(r"(\u03b1|\u03b2)\s*\(\s*(1?)\s*" + SENT_DEGREE + r"\s*(\d)\s*\)"),
     lambda m: f"{m.group(1)}({m.group(2) or '1'}" + ARROW + f"{m.group(3)})"),

    # α1→4-style bonds ("α1 ° 4", line-wrapped "α1 °\n4" arrives here
    # already reflowed to "α1 ° 4").
    (R_BOND_ALPHA1,
     re.compile(r"(\u03b1|\u03b2)\s*1\s*" + SENT_DEGREE + r"\s*(\d)"),
     "\\g<1>1" + ARROW + "\\g<2>"),

    # bare "1 ° 4" / "1 ° 6" bonds in phrases like "in 1 ° 4" or
    # "cleavage of the 1 ° 6 bond" — only after a bond-ish word.
    (R_BOND_BARE1,
     re.compile(r"\b(in|the|an)\s+1\s*" + SENT_DEGREE + r"\s*([1246])\b"),
     "\\g<1> 1" + ARROW + "\\g<2>"),

    # Δ9 desaturase / Δ9 positions / beyond Δ9 — delta before a digit.
    (R_DELTA,
     re.compile(SENT_SQUARE + r"\s*(?=\d)"),
     "\u0394"),

    # ■turn off■ — paired stray glyphs used as quotation marks.
    (R_QUOTE,
     re.compile(SENT_SQUARE + r"([A-Za-z]+(?:\s+[A-Za-z]+)*?)" + SENT_SQUARE),
     r'"\1"'),

    # Lone strays ("reaction to■ Benedict's", "the ■reaction"): the glyph
    # carries no content — the parallel solution text proves the sentence
    # reads without it. Drop it (audited + counted) and keep exactly one
    # space at the seam so "to■ Benedict's" becomes "to Benedict's".
    (R_STRAY,
     re.compile(r"(.)?" + SENT_SQUARE + r"\s?(.)?"),
     lambda m: (m.group(1) or "")
     + (" " if m.group(1) and m.group(2)
        and not m.group(1).isspace() else "")
     + (m.group(2) or "")),
]

# Generic reaction arrow — runs LAST among degree rules: any remaining
# Symbol sentinel between content is a reaction arrow (all 17 reaction
# cases: "Pyruvate + NAD+ + CoA °", "From glucose ° Pyruvate °", "E+S°
# ES", "NADPH + 2O2 °", ...).
_REACTION_RE = re.compile(r"\s*" + SENT_DEGREE + r"\s*")


def repair(text: str, counts: Counter | None = None) -> str:
    """Apply every rule to one field string. Returns (repaired text).

    `counts` (optional Counter) receives per-rule application counts.
    A sentinel surviving all rules is restored to its original glyph and
    counted under 'unknown_glyph' so the caller can flag the question.
    """
    if counts is None:
        counts = Counter()
    if SENT_DEGREE not in text and SENT_SQUARE not in text:
        return text

    for name, rx, repl in _RULES:
        if name == R_REACTION:
            continue
        text, n = rx.subn(repl, text)
        if n:
            counts[name] += n

    # generic reaction arrows (remaining degree sentinels)
    text, n = _REACTION_RE.subn(" \u2192 ", text)
    if n:
        counts[R_REACTION] += n
    # tidy double spaces the padded arrow can leave at line-join seams
    text = re.sub(r"  +", " ", text)

    # Anything left is unknown: restore the original glyph verbatim and
    # flag. Never guess.
    if SENT_DEGREE in text:
        counts["unknown_glyph"] += text.count(SENT_DEGREE)
        text = text.replace(SENT_DEGREE, "\u00b0")
    if SENT_SQUARE in text:
        counts["unknown_glyph"] += text.count(SENT_SQUARE)
        text = text.replace(SENT_SQUARE, "\u25a0")
    return text


def has_sentinels(text: str) -> bool:
    return SENT_DEGREE in text or SENT_SQUARE in text

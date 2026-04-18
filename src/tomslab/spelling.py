"""Typo-tolerant spelling correction for Ask-Tom questions.

Goals:
- "absorbtion" -> "absorption"  (common English misspelling)
- "VPOC", "HVN", "LVN", "MID" etc. pass through untouched (trading jargon)
- "mean reverion" -> "mean reversion" for known multi-word concepts

Strategy:
1. Build a whitelist vocabulary from the ``concepts`` table + a small set
   of curated Bookmap/auction terms. Anything matching (case-insensitive)
   stays verbatim.
2. For every other token that doesn't look like plain English (or clearly
   is but simple) fall back to ``difflib.get_close_matches`` against our
   vocabulary. Accept a match only at edit distance ≈ 1-2.
3. Keep a tiny hard-coded misspelling table for words we see most often
   ("absorbtion", "occured", "seperation") — difflib is good but a cheap
   dictionary win shaves false negatives.

Deliberately conservative: if confidence is low we leave the token alone.
Bad correction is worse than no correction.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from functools import lru_cache


_CURATED_TERMS = {
    # core vocabulary always preserved verbatim
    "VPOC", "DVPOC", "NVPOC", "MC/VPOC", "ON/VPOC", "HVN", "LVN", "VHVN",
    "IB", "IBH", "IBL", "IBC", "IBF", "RTH", "ETH", "VWAP", "FS/VWAP",
    "VP", "VA", "MCOMP", "MC", "COMP", "ON",
    "HOR", "HIR", "LIR", "LOR", "MID",
    "HTF", "LTF", "PoC", "POC",
    "Bookmap", "absorption", "initiative", "responsive", "auction",
    "orderflow", "liquidity", "delta", "iceberg", "rotation",
    "Steidlmayer",
}

# Cheap hand-picked corrections for common misspellings.
_HARDCODED = {
    "absorbtion":   "absorption",
    "absortion":    "absorption",
    "absobtion":    "absorption",
    "reverion":     "reversion",
    "reversiion":   "reversion",
    "reversion":    "reversion",
    "voloume":      "volume",
    "volum":        "volume",
    "profil":       "profile",
    "proflie":      "profile",
    "overnite":     "overnight",
    "overnigh":     "overnight",
    "iceburg":      "iceberg",
    "liquidty":     "liquidity",
    "liquiditty":   "liquidity",
    "balence":      "balance",
    "balnace":      "balance",
    "tendancy":     "tendency",
    "seperation":   "separation",
    "occured":      "occurred",
    "truning":      "turning",
    "breakot":      "breakout",
    "breakoot":     "breakout",
    "devation":     "deviation",
    "rejeciton":    "rejection",
    "rejction":     "rejection",
    "retial":       "retail",
    "structerd":    "structured",
    "structred":    "structured",
}


_TOKEN_RE = re.compile(r"\w+(?:[''`]\w+)*", re.UNICODE)


@lru_cache(maxsize=1)
def _vocab(db_path: str) -> tuple[set[str], list[str]]:
    """Load (lowercase_vocab, original_case_list) from the concepts table.

    Returns the set for O(1) membership checks plus the list (with casing
    preserved) that difflib matches against for multi-word hints.
    """
    lower: set[str] = set()
    cased: list[str] = []
    for term in _CURATED_TERMS:
        lower.add(term.lower())
        cased.append(term)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT name, description FROM concepts"):
            for piece in _extract_word_like(r["name"] or ""):
                lower.add(piece.lower())
                cased.append(piece)
            for piece in _extract_word_like(r["description"] or ""):
                if len(piece) >= 4:
                    lower.add(piece.lower())
        conn.close()
    except Exception:
        pass
    return lower, cased


def _extract_word_like(text: str) -> list[str]:
    if not text:
        return []
    return _TOKEN_RE.findall(text)


# A tiny hand-maintained list of very common English words we should accept
# without questioning, to skip fuzzy matching on obvious stuff. This is far
# from complete — we only need enough to cover common question words.
_COMMON_EN = set("""
the is are was were be been being a an of to and or not for with on at from
by as but if then than so that this these those which what when where why how
who whom whose how's it its you your we our they them their he she his her i
me my us do does did doing done have has had having can could should would
will shall may might about into like more most less least some any few many
much several all both each every other another out up down over under in off
on over before after again here there now then today tomorrow yesterday
always never often sometimes rarely usually mostly just only also even yet
said say says made make makes making take taken takes taking going go went
gone come comes came get gets got getting put puts putting use uses used using
know knew known knowing think thought thinking look looks looked looking
between among within without against toward toward towards across through
explain example question answer show tell find finds findable
tom toms toms's mr mrs dr
""".split())


def correct_query(question: str, db_path: str | None = None) -> tuple[str, list[tuple[str, str]]]:
    """Return ``(corrected_question, [(original, replacement), ...])``.

    The corrections list is empty when nothing changed.  A lookup-style
    cache on ``db_path`` means subsequent calls skip the concepts query.
    """
    if not question or not question.strip():
        return question, []

    vocab_lower: set[str] = set()
    vocab_cased: list[str] = []
    if db_path:
        vocab_lower, vocab_cased = _vocab(db_path)

    tokens = _TOKEN_RE.findall(question)
    if not tokens:
        return question, []

    corrections: list[tuple[str, str]] = []
    replacements: dict[str, str] = {}

    for tok in tokens:
        low = tok.lower()
        # preserve uppercase-heavy acronyms and anything in vocab
        if tok.isupper() and len(tok) <= 6:
            continue
        if low in vocab_lower:
            continue
        if low in _COMMON_EN:
            continue
        # cheap hard-coded win
        hit = _HARDCODED.get(low)
        if hit:
            replacements[tok] = hit
            corrections.append((tok, hit))
            continue
        # fuzzy match against vocab
        if vocab_cased:
            matches = difflib.get_close_matches(low, vocab_lower, n=1, cutoff=0.82)
            if matches:
                best = matches[0]
                if best != low:
                    replacements[tok] = best
                    corrections.append((tok, best))

    if not replacements:
        return question, []

    # Apply replacements preserving surrounding punctuation. Use simple
    # whole-token replacement: split on the same regex and rejoin.
    def _sub(m: "re.Match") -> str:
        t = m.group(0)
        return replacements.get(t, t)

    corrected = _TOKEN_RE.sub(_sub, question)
    return corrected, corrections

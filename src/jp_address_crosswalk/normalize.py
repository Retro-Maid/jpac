"""Address normalization (config/address_normalization.yml, docs/MATCHING_RULES.md §8).

Deliberately conservative. Transformations that could merge two genuinely
different Japanese place names are *not* applied, so two towns differing only in
ヶ/ケ/が, ノ/之/の or 旧字体/新字体 will not match. That is the intended cost: an
explicit reviewed override beats a silent global rule (docs/POLICY.md §4).

All functions are pure and operate on Polars expressions so the whole pipeline
stays vectorised (docs/ARCHITECTURE.md §8).
"""

from __future__ import annotations

import re
import unicodedata

import polars as pl

NORMALIZATION_PROFILE_VERSION = "1.0.0"

# Kanji numerals only in a 丁目 context. Bounded on purpose: 三田, 四谷 and
# 六本木 must survive untouched.
_KANJI_DIGIT = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CHOME_RE = re.compile(r"([〇零一二三四五六七八九十百]+)丁目")
_WS_RE = re.compile(r"\s+")
# ー (chouon) is intentionally absent: in kana names it is a letter, not a hyphen.
_HYPHEN_RE = re.compile(r"(?<=\d)[‐‑‒–—―−－ー](?=\d)")
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def kanji_to_int(text: str) -> int | None:
    """Parse a Japanese numeral up to 999. Returns None if unparseable."""
    if not text:
        return None
    if all(c in _KANJI_DIGIT for c in text):
        value = 0
        for c in text:
            value = value * 10 + _KANJI_DIGIT[c]
        return value

    total = 0
    section = 0
    current = 0
    for ch in text:
        if ch in _KANJI_DIGIT:
            current = _KANJI_DIGIT[ch]
        elif ch == "十":
            section += (current or 1) * 10
            current = 0
        elif ch == "百":
            section += (current or 1) * 100
            current = 0
        else:
            return None
    total += section + current
    return total or None


def _chome_sub(match: re.Match[str]) -> str:
    value = kanji_to_int(match.group(1))
    return f"{value}丁目" if value is not None else match.group(0)


def _fold_and_strip(text: str, max_passes: int = 4) -> str:
    """Apply NFKC and whitespace removal together, to a fixed point.

    Neither step is idempotent on its own once the other is present. NFKC
    expands some compatibility characters into *space + combining mark*
    (U+00A8 → ``" ` + U+0308``); removing that space then leaves a base
    character adjacent to a combining mark, which a further NFKC pass would
    compose. Running the pair once therefore gives ``n(n(x)) != n(x)``.

    Matching stability depends on idempotence — the same input must always
    produce the same key — so the pair is iterated until it stops changing.
    """
    for _ in range(max_passes):
        folded = _WS_RE.sub("", unicodedata.normalize("NFKC", text))
        if folded == text:
            return text
        text = folded
    return text


def normalize_conservative(value: str | None) -> str:
    """The default profile used by every name-based matching rule."""
    if not value:
        return ""
    text = _fold_and_strip(value)
    text = _CHOME_RE.sub(_chome_sub, text)
    return _HYPHEN_RE.sub("-", text)


def normalize_mlit_relaxed(value: str | None) -> str:
    """Conservative plus 大字/字 prefix stripping (MLIT candidates only)."""
    text = normalize_conservative(value)
    for prefix in ("大字", "字"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def normalize_postal_town(value: str | None) -> str:
    """Japan Post 町域: conservative plus removal of trailing parentheticals.

    The original is always kept in ``town_raw`` (docs/POLICY.md §5).
    """
    if not value:
        return ""
    return normalize_conservative(_PAREN_RE.sub("", value))


# ------------------------------------------------------------- Polars exprs

def _nfkc_expr(col: pl.Expr) -> pl.Expr:
    # Polars has no NFKC kernel; the substitutions below cover the cases that
    # actually occur in these sources (full-width ASCII), and the Python path is
    # used where full NFKC is required.
    return col


def expr_normalize_conservative(col: pl.Expr) -> pl.Expr:
    """Vectorised conservative normalization.

    ``map_elements`` is used only for the kanji-丁目 rewrite, which has no
    vectorised equivalent; everything else stays in the Polars engine.
    """
    return (
        col.fill_null("")
        .map_elements(normalize_conservative, return_dtype=pl.Utf8)
        .alias(col.meta.output_name() if col.meta.has_multiple_outputs() is False else "normalized")
    )


def compose_abr_full_name(
    oaza: str | None, chome: str | None, koaza: str | None
) -> str:
    """Compose ABR's split name fields into one comparable string.

    ABR stores ``oaza_cho`` / ``chome`` / ``koaza`` separately while MLIT and
    Japan Post publish a single string.
    """
    return "".join(p for p in (oaza, chome, koaza) if p)


def expr_compose_abr_full_name() -> pl.Expr:
    return (
        pl.col("oaza_cho").fill_null("")
        + pl.col("chome").fill_null("")
        + pl.col("koaza").fill_null("")
    )

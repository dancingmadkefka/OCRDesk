"""Financial-token extraction and normalization (docs/grader-plan.md section 1 & 3).

Pure regex + arithmetic, deterministic, no locale libraries. OCR glyph
confusions (O/0, l/1, E/EUR-look-alikes) are intentionally NOT normalized:
this grader measures OCR fidelity, so a model that emits "O" where the GT has
"0" must be scored as wrong, never silently forgiven.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .ir import CellRef, FinToken

# ---------------------------------------------------------------------------
# Currency markers
# ---------------------------------------------------------------------------

_CURRENCY_CANON = {"€": "EUR", "£": "GBP", "CHF": "CHF", "EUR": "EUR", "GBP": "GBP"}
_CURRENCY_ALT = "|".join(
    re.escape(sym) for sym in sorted(_CURRENCY_CANON, key=len, reverse=True)
)


def _match_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    return _CURRENCY_CANON.get(raw.strip())


# ---------------------------------------------------------------------------
# Amounts: 1,234.56 / 1.234,56 / 1'234.56 / plain 59.99, with optional
# currency prefix or suffix (glued or spaced).
# ---------------------------------------------------------------------------

# Grouped-thousands forms need a decimal part unless a currency marker anchors them
# (see _AMOUNT_CUR_INT_RE): a bare '116.303.292' is an identifier, not 116 million.
# The plain-decimal form must not clip a longer separated run ('116.30' inside
# '116.303.292', '6.6' inside the date '6.6.25').
_NUM_CORE = r"""
    (?<![\d.,'])\d{1,3}(?:,\d{3})+\.\d{2}(?![\d.,'])    # 1,234.56
  | (?<![\d.,'])\d{1,3}(?:\.\d{3})+,\d{2}(?![\d.,'])    # 1.234,56
  | (?<![\d.,'])\d{1,3}(?:'\d{3})+\.\d{2}(?![\d.,'])    # 1'234.56  (Swiss)
  | (?<![\d.,'])\d+[.,]\d{1,2}(?![\d.,'])                # 59.99 / 59,99 / 59.9
"""

#: The prefix/suffix whitespace is grouped *with* its currency alternative so
#: it is only ever consumed when a currency marker actually matches — a bare
#: unconditional `\s*` here would swallow a trailing block-boundary newline
#: even when no suffix currency follows, corrupting VAT-letter adjacency
#: detection (see _match_vat_letter_after).
_AMOUNT_CORE_RE = re.compile(
    rf"(?:(?P<pfx>{_CURRENCY_ALT})\s*)?(?P<num>{_NUM_CORE})(?:\s*(?P<sfx>{_CURRENCY_ALT}))?",
    re.VERBOSE,
)

# A bare integer only counts as an amount when a currency marker anchors it
# (otherwise "6" in "6 Items" or a product code would misfire).
_AMOUNT_CUR_INT_RE = re.compile(
    rf"""
    (?:(?P<pfx2>{_CURRENCY_ALT})\s*(?P<num2>\d{{1,3}}(?:[,.']\d{{3}})*|\d+)(?![\d.,']))
  | (?:(?P<num3>\d{{1,3}}(?:[,.']\d{{3}})*|\d+)(?![\d.,'])\s*(?P<sfx2>{_CURRENCY_ALT}))
    """,
    re.VERBOSE,
)

# Irish till-receipt VAT rate letters. Deliberately a fixed allowlist, not
# `[A-Z]`: "O" (visually a zero) and any other letter must never attach, per
# the "59.99 O" negative case in tests/grader/test_fintoken.py.
_VAT_LETTERS = "ABCDEFGH"


def _parse_amount_to_cents(num_str: str) -> int:
    """Convert a matched amount string to integer cents.

    The final separator followed by exactly 1-2 trailing digits (anchored to
    the end of the string) is the decimal point; every other '.', ',' or '\''
    is a thousands separator and is stripped. This works uniformly across
    every accepted format without needing to know which regex alternative
    matched.
    """
    frac_match = re.search(r"[.,](\d{1,2})$", num_str)
    if frac_match:
        frac = frac_match.group(1).ljust(2, "0")
        int_part = num_str[: frac_match.start()]
    else:
        frac = "00"
        int_part = num_str
    int_part = re.sub(r"[^\d]", "", int_part) or "0"
    return int(int_part) * 100 + int(frac)


def _canonical_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def _apply_sign(text: str, start: int, end: int) -> tuple[int, int, bool]:
    """Widen an amount match to include a leading minus or accounting parentheses.

    A minus counts only when it is not glued to a preceding word or number ('10-20.00' is a
    range, 'Total-EFT' is a word), so '-12.34', 'CHF -12.34' and ': -12.34' are negative and
    '(12.34)' is negative. Returns the widened span and the sign."""
    before = text[start - 1] if start > 0 else ""
    before2 = text[start - 2] if start > 1 else ""
    after = text[end] if end < len(text) else ""
    if before and before in "-\u2212" and (start == 1 or before2 in " \t\n:(\u00a0"):
        return start - 1, end, True
    if before == "(" and after == ")":
        return start - 1, end + 1, True
    return start, end, False


def _match_vat_letter_after(text: str, pos: int) -> tuple[int, int, str, bool] | None:
    """Look for a VAT-rate letter immediately following an amount at `pos`.

    Returns (start, end, letter, attached) or None. `attached` is True for
    both glued ("59.99D") and same-line-spaced ("59.99 D") adjacency, and
    False only when the letter sits across a block/newline boundary
    ("59.99\\nD") — matching the three-way triad in the plan exactly.
    """
    window = text[pos : pos + 24]
    # Non-breaking spaces (models emit `&nbsp;` between amount and letter) are still the same line.
    same_line = re.match(rf"[ \t\u00a0]*([{_VAT_LETTERS}])(?![A-Za-z0-9])", window)
    if same_line:
        return pos + same_line.start(1), pos + same_line.end(1), same_line.group(1), True
    blocked = re.match(rf"[ \t\u00a0]*\n[ \t\n\u00a0]*([{_VAT_LETTERS}])(?![A-Za-z0-9])", window)
    if blocked:
        return pos + blocked.start(1), pos + blocked.end(1), blocked.group(1), False
    return None


# ---------------------------------------------------------------------------
# Dates: dd/mm/yyyy, dd.mm.yyyy (same separator both sides), ISO yyyy-mm-dd.
# ---------------------------------------------------------------------------

_DATE_SEP_RE = re.compile(r"\b(\d{1,2})([./])(\d{1,2})\2(\d{4})\b")
# Two-digit years ('6.6.25', '07/06/18') are dates too; day/month ranges are checked in code.
_DATE_SEP2_RE = re.compile(r"(?<![\d.,'/])(\d{1,2})([./])(\d{1,2})\2(\d{2})(?![\d.,'/])")
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# ---------------------------------------------------------------------------
# Percentages
# ---------------------------------------------------------------------------

_PERCENT_RE = re.compile(r"\b(?P<num>\d+(?:[.,]\d+)?)\s?%")

# ---------------------------------------------------------------------------
# Masked card numbers: 4+ asterisks then exactly 4 trailing digits.
# ---------------------------------------------------------------------------

_MASKED_CARD_RE = re.compile(r"\*{4,}(\d{4})\b")

# ---------------------------------------------------------------------------
# IBAN: broad candidate regex (allows internal spaces), then exact-length
# validation per country so we never accept a look-alike token.
# ---------------------------------------------------------------------------

_IBAN_LENGTHS = {
    "IE": 22, "GB": 22, "CH": 21, "DE": 22, "FR": 27,
    "AT": 20, "IT": 27, "ES": 24, "NL": 18, "LU": 20,
}
_IBAN_COUNTRY_ALT = "|".join(_IBAN_LENGTHS)
_IBAN_CANDIDATE_RE = re.compile(
    rf"\b(?:{_IBAN_COUNTRY_ALT})\d{{2}}(?:[ ]?[A-Z0-9]{{1,4}}){{3,8}}\b"
)

# ---------------------------------------------------------------------------
# Reference ids: "INV-2024-0098" style, labeled runs ("EFT No.: 20111669548",
# "Authorisation Code: 222483"), and bare long digit runs as a backstop.
# ---------------------------------------------------------------------------

_REF_ID_EXPLICIT_RE = re.compile(
    r"\b[A-Z]{2,6}-\d{4}-\d{2,8}\b"          # INV-2024-0098
    r"|\b[A-Z]{2,6}-\d{2,3}(?:\.\d{3}){2,}\b"   # CHE-116.303.292 (Swiss UID)
)
_REF_ID_LABELED_RE = re.compile(
    r"\b(?:EFT\s*No\.?|Ref(?:erence)?\s*No\.?|Auth(?:orisation)?\s*Code)\s*:?\s*(\d{5,})\b",
    re.I,
)
_REF_ID_DIGITS_RE = re.compile(r"\b\d{6,}\b")


class _SpanClaims:
    """Tracks which character spans of the source text are already spoken for."""

    def __init__(self) -> None:
        self._spans: list[tuple[int, int]] = []

    def claim(self, start: int, end: int) -> bool:
        for s, e in self._spans:
            if start < e and end > s:
                return False
        self._spans.append((start, end))
        return True


def extract_tokens(text: str, locale_hint: str | None = None) -> list[FinToken]:
    """Extract every recognizable financial token from `text`, in document order.

    `text` should preserve block-boundary line breaks (as produced by
    canonicalize._block_text_raw) rather than being whitespace-collapsed,
    because VAT-letter attachment must distinguish "on the same line" from
    "across a block boundary". `locale_hint` (a decimal separator, "," or
    ".") is accepted for future tie-breaking; the amount grammar below is
    already unambiguous (grouped-thousands forms require exactly 3 digits
    per group, so digit-count alone disambiguates from a 1-2 digit decimal
    fraction) for every case this grader's fixtures exercise.
    """
    del locale_hint  # reserved; see docstring
    if not text:
        return []

    claims = _SpanClaims()
    found: list[tuple[int, FinToken]] = []

    for m in _IBAN_CANDIDATE_RE.finditer(text):
        compact = re.sub(r"\s+", "", m.group(0)).upper()
        if _IBAN_LENGTHS.get(compact[:2]) != len(compact):
            continue
        if not claims.claim(*m.span()):
            continue
        found.append((m.start(), FinToken(
            type="iban", raw=m.group(0), canonical=compact, cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    for m in _MASKED_CARD_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        last4 = m.group(1)
        found.append((m.start(), FinToken(
            type="masked_card", raw=m.group(0), canonical=f"****{last4}",
            cents=None, currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    # Dates run before amounts: "28.02.2021" must claim its whole span so the
    # amount grammar's plain-decimal alternative can't clip it down to "28.02".
    for m in _DATE_SEP_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        dd, _sep, mm, yyyy = m.group(1), m.group(2), m.group(3), m.group(4)
        canonical = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
        found.append((m.start(), FinToken(
            type="date", raw=m.group(0), canonical=canonical, cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    for m in _DATE_SEP2_RE.finditer(text):
        dd, mm, yy = int(m.group(1)), int(m.group(3)), int(m.group(4))
        if not (1 <= dd <= 31 and 1 <= mm <= 12):
            continue
        if not claims.claim(*m.span()):
            continue
        yyyy = 2000 + yy if yy < 70 else 1900 + yy
        found.append((m.start(), FinToken(
            type="date", raw=m.group(0), canonical=f"{yyyy}-{mm:02d}-{dd:02d}", cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    for m in _DATE_ISO_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        found.append((m.start(), FinToken(
            type="date", raw=m.group(0), canonical=m.group(0), cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    # Percent runs before amounts too: "23.0%" must claim the '%' along with
    # the digits, or the amount grammar clips it down to a bare "23.0".
    for m in _PERCENT_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        num = m.group("num").replace(",", ".")
        found.append((m.start(), FinToken(
            type="percent", raw=m.group(0), canonical=num, cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    # Explicit alphanumeric ids run before amounts so 'CHE-116.303.292' is never an amount.
    for m in _REF_ID_EXPLICIT_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        found.append((m.start(), FinToken(
            type="reference_id", raw=m.group(0), canonical=m.group(0), cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    for m in _AMOUNT_CORE_RE.finditer(text):
        start, end, negative = _apply_sign(text, m.start(), m.end())
        if not claims.claim(start, end):
            continue
        cents = _parse_amount_to_cents(m.group("num"))
        if negative:
            cents = -cents
        currency = _match_currency(m.group("pfx") or m.group("sfx"))
        found.append((start, FinToken(
            type="amount", raw=text[start:end].strip(), canonical=_canonical_cents(cents),
            cents=cents, currency=currency, vat_letter=None, attached=False, cell_ref=None,
        )))
        letter = _match_vat_letter_after(text, m.end())
        if letter is not None:
            l_start, l_end, letter_char, attached = letter
            if claims.claim(l_start, l_end):
                found.append((l_start, FinToken(
                    type="vat_letter", raw=letter_char, canonical=letter_char,
                    cents=cents, currency=currency, vat_letter=letter_char,
                    attached=attached, cell_ref=None,
                )))

    for m in _AMOUNT_CUR_INT_RE.finditer(text):
        start, end, negative = _apply_sign(text, m.start(), m.end())
        if not claims.claim(start, end):
            continue
        num = m.group("num2") or m.group("num3")
        cents = _parse_amount_to_cents(num)
        if negative:
            cents = -cents
        currency = _match_currency(m.group("pfx2") or m.group("sfx2"))
        found.append((start, FinToken(
            type="amount", raw=text[start:end].strip(), canonical=_canonical_cents(cents),
            cents=cents, currency=currency, vat_letter=None, attached=False, cell_ref=None,
        )))

    for m in _REF_ID_LABELED_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        digits = m.group(1)
        found.append((m.start(), FinToken(
            type="reference_id", raw=m.group(0), canonical=digits, cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    for m in _REF_ID_DIGITS_RE.finditer(text):
        if not claims.claim(*m.span()):
            continue
        found.append((m.start(), FinToken(
            type="reference_id", raw=m.group(0), canonical=m.group(0), cents=None,
            currency=None, vat_letter=None, attached=False, cell_ref=None,
        )))

    found.sort(key=lambda pair: pair[0])
    ordered = [tok for _, tok in found]
    return attach_vat_letters(ordered)


def attach_vat_letters(tokens: list[FinToken]) -> list[FinToken]:
    """Merge a standalone `vat_letter` token onto the `amount` token it follows.

    Pure function of token order (no text positions needed): for every
    adjacent pair where an `amount` with `vat_letter is None` is immediately
    followed by a `vat_letter`-type token, the amount is replaced with a copy
    carrying that token's `vat_letter`/`attached`. The standalone token is
    kept in the returned list — FIN_EM scores `vat_letter` as its own
    weighted category (docs/grader-plan.md section 5), separate from
    `amount`.
    """
    result = list(tokens)
    for i in range(len(result) - 1):
        amt = result[i]
        nxt = result[i + 1]
        if amt.type == "amount" and amt.vat_letter is None and nxt.type == "vat_letter":
            result[i] = replace(amt, vat_letter=nxt.vat_letter, attached=nxt.attached)
    return result


def with_cell_ref(tokens: list[FinToken], cell_ref: CellRef) -> list[FinToken]:
    """Return a copy of `tokens` with `cell_ref` stamped on every token.

    Small helper used by tables.py so a single extract_tokens() call's
    output can be attributed to the cell it came from without every caller
    reimplementing the dataclasses.replace loop.
    """
    return [replace(tok, cell_ref=cell_ref) for tok in tokens]

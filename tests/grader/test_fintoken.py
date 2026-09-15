"""Tests for ocrgrade.fintoken (docs/grader-plan.md sections 1, 3, 10.5)."""
from __future__ import annotations

from ocrgrade.fintoken import attach_vat_letters, extract_tokens
from ocrgrade.ir import FinToken


def _amounts(text: str) -> list:
    return [t for t in extract_tokens(text) if t.type == "amount"]


def _vat_letters(text: str) -> list:
    return [t for t in extract_tokens(text) if t.type == "vat_letter"]


# --- VAT-letter triad: required dedicated case ------------------------------


def test_vat_letter_glued_is_attached():
    tokens = extract_tokens("59.99D")
    amount = _amounts("59.99D")[0]
    assert amount.cents == 5999
    assert amount.vat_letter == "D"
    assert amount.attached is True
    letters = _vat_letters("59.99D")
    assert len(letters) == 1
    assert letters[0].vat_letter == "D"
    assert letters[0].attached is True
    assert letters[0].cents == 5999
    assert len(tokens) == 2  # amount + vat_letter, nothing else


def test_vat_letter_space_separated_is_attached():
    amount = _amounts("59.99 D")[0]
    assert amount.cents == 5999
    assert amount.vat_letter == "D"
    assert amount.attached is True


def test_vat_letter_block_separated_is_not_attached():
    amount = _amounts("59.99\nD")[0]
    assert amount.cents == 5999
    assert amount.vat_letter == "D"
    assert amount.attached is False
    letters = _vat_letters("59.99\nD")
    assert letters[0].attached is False


# --- OCR glyph confusion: explicitly NOT normalized -------------------------


def test_ocr_glyph_confusion_letter_not_treated_as_valid_vat_letter():
    """'O' looks like a zero but is not an Irish VAT-rate letter. A naive
    `[A-Z]` wildcard implementation would wrongly attach it; the allowlist
    must reject it while still correctly parsing the amount itself.
    """
    amounts = _amounts("59.99 O")
    assert len(amounts) == 1
    amount = amounts[0]
    assert amount.cents == 5999  # "59.99" alone is an unambiguous, correct amount
    assert amount.vat_letter is None
    assert amount.attached is False
    assert _vat_letters("59.99 O") == []


def test_ocr_glyph_confusions_are_not_normalized():
    """O/0 and l/1 glyph confusions must not be silently fixed anywhere. A
    stray letter where a digit belongs is never guessed at or coerced into
    the right value: "59.9l" (an OCR misread of "59.91") must not parse as
    cents=5991 — the trailing "l" is simply not a digit, so the number
    literal stops at "59.9" (cents=5990), and "O.99" (leading letter, no
    digit at all before the separator) must not parse as an amount.
    """
    amounts = _amounts("59.9l")
    assert len(amounts) == 1
    assert amounts[0].cents == 5990  # NOT 5991 — 'l' is never read as '1'
    assert _amounts("O.99") == []  # NOT parsed as if 'O' were '0'


# --- decimal conventions + Swiss apostrophe thousands: required case -------


def test_us_uk_thousands_and_decimal():
    amount = _amounts("1,234.56")[0]
    assert amount.cents == 123456
    assert amount.canonical == "1234.56"


def test_eu_thousands_and_decimal():
    amount = _amounts("1.234,56")[0]
    assert amount.cents == 123456


def test_swiss_apostrophe_thousands():
    amount = _amounts("1'234.56")[0]
    assert amount.cents == 123456


def test_plain_decimal_no_grouping():
    assert _amounts("59.99")[0].cents == 5999
    assert _amounts("0.75")[0].cents == 75
    assert _amounts("2.00")[0].cents == 200


def test_grouped_thousands_without_fraction():
    amount = _amounts("1,234")[0]
    assert amount.cents == 123400


# --- currency markers --------------------------------------------------------


def test_currency_prefix_glued():
    amount = _amounts("EUR66.71")[0]
    assert amount.cents == 6671
    assert amount.currency == "EUR"


def test_currency_symbol_prefix():
    amount = _amounts("€1,234.56")[0]
    assert amount.currency == "EUR"


def test_currency_symbol_suffix_gbp():
    amount = _amounts("£5.00")[0]
    assert amount.currency == "GBP"


def test_bare_integer_requires_currency_marker():
    assert _amounts("84693") == []  # a product code, not an amount
    assert _amounts("6 Items") == []
    assert _amounts("EUR 100")[0].cents == 10000


def test_word_after_amount_is_not_a_vat_letter():
    """'Vat' is three letters, not a lone allowlisted letter — must not
    misfire the way "48.77 Vat" (a real GT cell) could with a careless
    single-character lookahead.
    """
    amount = _amounts("48.77 Vat")[0]
    assert amount.vat_letter is None
    assert amount.attached is False


# --- dates -------------------------------------------------------------------


def test_date_iso():
    tokens = [t for t in extract_tokens("AXD:2021-02-28") if t.type == "date"]
    assert tokens[0].canonical == "2021-02-28"


def test_date_slash_ddmmyyyy():
    tokens = [t for t in extract_tokens("28/02/2021") if t.type == "date"]
    assert tokens[0].canonical == "2021-02-28"


def test_date_dot_ddmmyyyy_not_clipped_by_amount_grammar():
    """28.02.2021 must be recognized whole as a date, not clipped to the
    amount-shaped prefix "28.02" (dates must be extracted before amounts).
    """
    tokens = extract_tokens("28.02.2021")
    dates = [t for t in tokens if t.type == "date"]
    amounts = [t for t in tokens if t.type == "amount"]
    assert len(dates) == 1
    assert dates[0].canonical == "2021-02-28"
    assert amounts == []


# --- percent -----------------------------------------------------------------


def test_percent_extraction():
    tokens = [t for t in extract_tokens("D 23.0% Net") if t.type == "percent"]
    assert tokens[0].canonical == "23.0"


# --- masked card ---------------------------------------------------------


def test_masked_card_normalizes_star_run():
    tokens = [t for t in extract_tokens("************2840") if t.type == "masked_card"]
    assert tokens[0].canonical == "****2840"


# --- IBAN ----------------------------------------------------------------


def test_iban_ie_with_internal_spaces():
    tokens = [t for t in extract_tokens("IE29 AIBK 9311 5212 3456 78") if t.type == "iban"]
    assert len(tokens) == 1
    assert tokens[0].canonical == "IE29AIBK93115212345678"


def test_iban_wrong_length_is_rejected():
    # GB IBANs are 22 chars; this candidate is too short and must not match.
    assert [t for t in extract_tokens("GB29 NWBK 6016") if t.type == "iban"] == []


# --- reference ids ---------------------------------------------------------


def test_reference_id_explicit_pattern():
    tokens = [t for t in extract_tokens("INV-2024-0098") if t.type == "reference_id"]
    assert tokens[0].raw == "INV-2024-0098"


def test_reference_id_labeled_eft_no():
    tokens = [t for t in extract_tokens("EFT No.: 20111669548") if t.type == "reference_id"]
    assert tokens[0].canonical == "20111669548"


def test_reference_id_bare_long_digit_run():
    tokens = [t for t in extract_tokens("Authorisation Code: 222483") if t.type == "reference_id"]
    assert tokens[0].canonical == "222483"


def test_short_digit_run_is_not_a_reference_id():
    assert extract_tokens("Merchant ID: **26020") == [] or all(
        t.type != "reference_id" for t in extract_tokens("Merchant ID: **26020")
    )


# --- attach_vat_letters as a standalone, position-independent function -----


def test_attach_vat_letters_merges_adjacent_pair():
    amount = FinToken(
        type="amount", raw="59.99", canonical="59.99", cents=5999, currency=None,
        vat_letter=None, attached=False, cell_ref=None,
    )
    letter = FinToken(
        type="vat_letter", raw="D", canonical="D", cents=5999, currency=None,
        vat_letter="D", attached=True, cell_ref=None,
    )
    merged = attach_vat_letters([amount, letter])
    assert merged[0].vat_letter == "D"
    assert merged[0].attached is True
    assert merged[1] is letter  # standalone token kept for FIN_EM(vat_letter)


def test_attach_vat_letters_leaves_non_adjacent_amount_alone():
    amount1 = FinToken(
        type="amount", raw="1.00", canonical="1.00", cents=100, currency=None,
        vat_letter=None, attached=False, cell_ref=None,
    )
    amount2 = FinToken(
        type="amount", raw="2.00", canonical="2.00", cents=200, currency=None,
        vat_letter=None, attached=False, cell_ref=None,
    )
    merged = attach_vat_letters([amount1, amount2])
    assert merged[0].vat_letter is None
    assert merged[1].vat_letter is None


# --- ordering ------------------------------------------------------------


def test_tokens_are_returned_in_document_order():
    tokens = extract_tokens("Total: 59.99 D, ref INV-2024-0098, date 28/02/2021")
    kinds = [t.type for t in tokens]
    assert kinds == ["amount", "vat_letter", "reference_id", "date"]

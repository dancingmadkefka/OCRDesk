"""Tests for ocrgrade.canonicalize (docs/grader-plan.md sections 1, 3, 5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ocrgrade.canonicalize import _detect_truncated, _detect_was_fragment, build_document
from ocrgrade.ir import Sidecar
from ocrgrade.roles import load_role_map

ROLE_MAP = load_role_map(None)

GT_SET_DIR = Path(r"C:\Users\daniel\VSCodeProjects\AIFA GT Set")


def _doc(html: str, sidecar: Sidecar | None = None):
    return build_document(html, ROLE_MAP, sidecar)


# --- required dedicated case: fragment vs full document ----------------------


_FRAGMENT_HTML = (
    '<style>.x{color:red}</style>'
    '<div class="header">Corner Grocer Ltd</div>'
    '<table><tr><td>Milk</td><td>2.00</td></tr></table>'
    '<p>Total: EUR2.00</p>'
)

_FULL_DOCUMENT_HTML = (
    "<!DOCTYPE html><html><head><title>Receipt</title></head><body>"
    + _FRAGMENT_HTML
    + "</body></html>"
)


def test_fragment_detected_as_fragment():
    assert _detect_was_fragment(_FRAGMENT_HTML) is True


def test_full_document_detected_as_not_fragment():
    assert _detect_was_fragment(_FULL_DOCUMENT_HTML) is False


def test_fragment_vs_document_yield_equivalent_body_text_norm():
    frag_doc = _doc(_FRAGMENT_HTML)
    full_doc = _doc(_FULL_DOCUMENT_HTML)
    assert frag_doc.was_fragment is True
    assert full_doc.was_fragment is False
    assert frag_doc.parse_ok is True
    assert full_doc.parse_ok is True
    assert frag_doc.body_text_norm == full_doc.body_text_norm
    # The <title> in <head> must never leak into body text.
    assert "Receipt" not in full_doc.body_text_norm


# --- required dedicated case: truncation detection ---------------------------


def test_truncated_missing_closing_html_tag():
    assert _detect_truncated("<html><body><p>hi</p>") is True


def test_not_truncated_when_html_closed():
    assert _detect_truncated("<html><body><p>hi</p></body></html>") is False


def test_truncated_unclosed_table_row_long_input():
    long_prefix = "<p>" + ("padding " * 40) + "</p>"
    html = long_prefix + "<table><tr><td>cut off here"
    assert len(html) > 200
    assert _detect_truncated(html) is True


def test_not_truncated_short_input_even_if_tags_unbalanced():
    # The >200-char table/tr/td gate must not fire on tiny snippets.
    assert _detect_truncated("<table><tr><td>x") is False


def test_build_document_propagates_truncated_flag():
    doc = _doc("<html><body><p>unterminated")
    assert doc.truncated is True


# --- tag-equivalence folds ----------------------------------------------------


def test_b_and_strong_are_equivalent_in_outer_html():
    doc_b = _doc("<table><tr><td><b>Total</b></td><td>5.00</td></tr></table>")
    doc_strong = _doc("<table><tr><td><strong>Total</strong></td><td>5.00</td></tr></table>")
    assert doc_b.tables[0].outer_html == doc_strong.tables[0].outer_html


def test_i_and_em_are_equivalent_in_outer_html():
    doc_i = _doc("<table><tr><td><i>Note</i></td></tr></table>")
    doc_em = _doc("<table><tr><td><em>Note</em></td></tr></table>")
    assert doc_i.tables[0].outer_html == doc_em.tables[0].outer_html


# --- section_headers: h1-h6 + class-resolved, in document order -------------


def test_section_headers_mix_headings_and_classes_in_document_order():
    html = (
        "<h1>Invoice</h1>"
        '<div class="grey-bg">Customer Details</div>'
        "<p>Body text.</p>"
        "<h2>Summary</h2>"
    )
    doc = _doc(html)
    assert doc.section_headers == ["Invoice", "Customer Details", "Summary"]


def test_section_header_does_not_double_count_nested_match():
    html = '<div class="grey-bg"><h1>Invoice</h1></div>'
    doc = _doc(html)
    assert doc.section_headers == ["Invoice"]


# --- label_value_pairs: all four sources -------------------------------------


def test_table_row_two_column_pair():
    doc = _doc("<table><tr><td>Opening Balance</td><td>100.00</td></tr></table>")
    pairs = [p for p in doc.label_value_pairs if p.source == "table_row"]
    assert len(pairs) == 1
    assert pairs[0].label == "Opening Balance"
    assert pairs[0].value == "100.00"
    assert pairs[0].cell_ref is not None


def test_table_row_label_role_cell_in_wider_table():
    html = (
        '<table><tr><td class="label">Balance</td><td>100.00</td><td>note</td></tr></table>'
    )
    doc = _doc(html)
    pairs = [p for p in doc.label_value_pairs if p.source == "table_row"]
    assert any(p.label == "Balance" and p.value == "100.00" for p in pairs)


def test_table_row_skips_header_row():
    html = (
        "<table><tr><th>Label</th><th>Value</th></tr>"
        "<tr><td>Balance</td><td>100.00</td></tr></table>"
    )
    doc = _doc(html)
    pairs = [p for p in doc.label_value_pairs if p.source == "table_row"]
    assert len(pairs) == 1
    assert pairs[0].label == "Balance"
    assert pairs[0].value == "100.00"


def test_definition_list_pairs():
    doc = _doc("<dl><dt>Terminal</dt><dd>5129</dd></dl>")
    pairs = [p for p in doc.label_value_pairs if p.source == "definition_list"]
    assert pairs[0].label == "Terminal"
    assert pairs[0].value == "5129"


def test_definition_list_multiple_dd_per_dt():
    doc = _doc("<dl><dt>Codes</dt><dd>A1</dd><dd>B2</dd></dl>")
    pairs = [p for p in doc.label_value_pairs if p.source == "definition_list"]
    assert {p.value for p in pairs} == {"A1", "B2"}
    assert all(p.label == "Codes" for p in pairs)


def test_colon_pattern_pair():
    doc = _doc("<p>Card Number: ************5173</p>")
    pairs = [p for p in doc.label_value_pairs if p.source == "colon_pattern"]
    assert pairs[0].label == "Card Number"
    assert pairs[0].value == "************5173"


def test_colon_pattern_does_not_double_fire_on_wrapping_div():
    doc = _doc("<div><p>Merchant ID: 73915</p></div>")
    pairs = [p for p in doc.label_value_pairs if p.source == "colon_pattern"]
    assert len(pairs) == 1


def test_colon_pattern_excludes_table_cells():
    doc = _doc("<table><tr><td>Label: value</td></tr></table>")
    assert all(p.source != "colon_pattern" for p in doc.label_value_pairs)


def test_sibling_heuristic_pair_with_unrelated_classes():
    # Mirrors cases 010/027/006: unrelated class names, no colon, adjacent
    # block siblings where sibling 1 has no trailing digit and sibling 2 is
    # value-shaped.
    html = '<div class="foo-weird">Merchant ID</div><div class="bar-odd">73915</div>'
    doc = _doc(html)
    pairs = [p for p in doc.label_value_pairs if p.source == "sibling_heuristic"]
    assert pairs[0].label == "Merchant ID"
    assert pairs[0].value == "73915"


def test_sibling_heuristic_requires_value_shaped_second_sibling():
    html = "<div>Some label</div><div>Also just words</div>"
    doc = _doc(html)
    assert all(p.source != "sibling_heuristic" for p in doc.label_value_pairs)


# --- line_items ---------------------------------------------------------


def test_line_items_from_col_star_classes():
    html = (
        '<table><tr><th class="col-desc">Desc</th><th class="col-price">Price</th></tr>'
        "<tr><td>Milk</td><td>2.00</td></tr>"
        "<tr><td>Oats</td><td>1.19</td></tr>"
        "<tr><td>Buns</td><td>0.75</td></tr></table>"
    )
    doc = _doc(html)
    assert len(doc.line_items) == 3
    assert doc.line_items[0].fields == {"desc": "Milk", "price": "2.00"}


def test_line_items_from_header_row_text_fallback():
    html = (
        "<table><tr><td>Description</td><td>Amount</td></tr>"
        "<tr><td>Milk</td><td>2.00</td></tr>"
        "<tr><td>Oats</td><td>1.19</td></tr>"
        "<tr><td>Buns</td><td>0.75</td></tr></table>"
    )
    doc = _doc(html)
    assert len(doc.line_items) == 3
    assert doc.line_items[0].fields == {"description": "Milk", "amount": "2.00"}


def test_no_line_items_under_three_data_rows():
    html = (
        '<table><tr><th class="col-desc">Desc</th></tr>'
        "<tr><td>Milk</td></tr><tr><td>Oats</td></tr></table>"
    )
    doc = _doc(html)
    assert doc.line_items == []


# --- fin_tokens: document-wide, cell_ref only inside table cells ------------


def test_fin_tokens_include_cell_ref_inside_tables_and_none_outside():
    html = "<table><tr><td>Milk</td><td>2.00</td></tr></table><p>Ref: 30458812736</p>"
    doc = _doc(html)
    amount = next(t for t in doc.fin_tokens if t.type == "amount")
    ref = next(t for t in doc.fin_tokens if t.type == "reference_id")
    assert amount.cell_ref is not None
    assert amount.cell_ref.row == 0 and amount.cell_ref.col == 1
    assert ref.cell_ref is None


def test_fin_tokens_not_duplicated_between_table_and_outside_pass():
    html = "<table><tr><td>64.95 D</td></tr></table>"
    doc = _doc(html)
    amounts = [t for t in doc.fin_tokens if t.type == "amount"]
    assert len(amounts) == 1


# --- tables via tables.build_table ------------------------------------------


def test_tables_built_in_document_order_with_correct_index():
    html = "<table><tr><td>1</td></tr></table><table><tr><td>2</td></tr></table>"
    doc = _doc(html)
    assert [t.index for t in doc.tables] == [0, 1]
    assert doc.tables[0].cells[0].text_norm == "1"
    assert doc.tables[1].cells[0].text_norm == "2"


# --- parse_ok / parse_error ---------------------------------------------------


def test_parse_ok_true_for_normal_input():
    doc = _doc("<p>hello world</p>")
    assert doc.parse_ok is True
    assert doc.parse_error is None


def test_parse_ok_false_for_empty_body_from_nontrivial_input():
    html = "<script>" + ("x = 1;" * 10) + "</script>"
    assert len(html) > 20
    doc = _doc(html)
    assert doc.parse_ok is False
    assert doc.parse_error is not None


def test_sidecar_decimal_sep_is_threaded_through_without_crashing():
    sidecar = Sidecar(
        schema_version=1, case_id="x", category="receipt", category_secondary=None,
        locale="IE", currency="EUR", decimal_sep=",", vat_letter_scheme=True,
        has_tables=True, required_sections=[], critical_fields=[],
        label_value_pairs=[], line_item_schema=[],
    )
    doc = _doc("<p>59,99</p>", sidecar)
    assert doc.parse_ok is True


# --- optional smoke test against the real GT corpus --------------------------


@pytest.mark.skipif(not GT_SET_DIR.is_dir(), reason="AIFA GT Set not present on this machine")
def test_smoke_real_gt_cases_parse_ok_with_sane_table_counts():
    case_dirs = sorted(p for p in GT_SET_DIR.iterdir() if p.is_dir())
    assert case_dirs, "expected at least one case-* folder"
    checked = 0
    for case_dir in case_dirs:
        html_files = list(case_dir.glob("*.html"))
        if not html_files:
            continue
        html = html_files[0].read_text(encoding="utf-8", errors="replace")
        doc = _doc(html)
        assert doc.parse_ok is True, f"{case_dir.name}: {doc.parse_error}"
        assert 0 <= len(doc.tables) <= 50, f"{case_dir.name}: implausible table count"
        checked += 1
    assert checked == len(case_dirs)

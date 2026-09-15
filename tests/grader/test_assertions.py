"""Tests for ocrgrade.assertions (A1-A10).

Hand-builds IR objects directly (Scope B contract: no import of Scope A's
canonicalize/tables/fintoken modules).
"""
from __future__ import annotations

from ocrgrade.assertions import run_assertions
from ocrgrade.ir import (
    Cell,
    CellRef,
    CriticalField,
    Document,
    FinToken,
    LabelValuePair,
    LineItem,
    Sidecar,
    Table,
)


# --- private test builders ---------------------------------------------------------


def make_cell(row, col, text="", role="other", is_numeric=False, tokens=None, table_index=0) -> Cell:
    return Cell(
        table_index=table_index,
        row=row,
        col=col,
        rowspan=1,
        colspan=1,
        is_span_origin=True,
        text_raw=text,
        text_norm=text,
        role=role,
        is_numeric=is_numeric,
        is_header=False,
        tokens=tokens or [],
    )


def make_table(index, cells, outer_html="<table></table>") -> Table:
    n_rows = max((c.row for c in cells), default=-1) + 1
    n_cols = max((c.col for c in cells), default=-1) + 1
    return Table(index=index, n_rows=n_rows, n_cols=n_cols, cells=cells, outer_html=outer_html)


def make_doc(
    tables=None,
    section_headers=None,
    label_value_pairs=None,
    line_items=None,
    fin_tokens=None,
) -> Document:
    return Document(
        tables=tables or [],
        section_headers=section_headers or [],
        label_value_pairs=label_value_pairs or [],
        line_items=line_items or [],
        fin_tokens=fin_tokens or [],
        body_text_norm="",
        parse_ok=True,
        parse_error=None,
        was_fragment=False,
        truncated=False,
    )


def make_sidecar(
    required_sections=None,
    critical_fields=None,
    case_id="case1",
    category="invoice",
) -> Sidecar:
    return Sidecar(
        schema_version=1,
        case_id=case_id,
        category=category,
        category_secondary=None,
        locale="OTHER",
        currency=None,
        decimal_sep=".",
        vat_letter_scheme=False,
        has_tables=True,
        required_sections=required_sections or [],
        critical_fields=critical_fields or [],
        label_value_pairs=[],
        line_item_schema=[],
    )


def make_fin_token(
    type="amount",
    raw="",
    canonical=None,
    cents=None,
    vat_letter=None,
    attached=False,
) -> FinToken:
    return FinToken(
        type=type,  # type: ignore[arg-type]
        raw=raw,
        canonical=canonical,
        cents=cents,
        currency=None,
        vat_letter=vat_letter,
        attached=attached,
        cell_ref=None,
    )


def get(results, assertion_id):
    return next(r for r in results if r.id == assertion_id)


# --- A1: value_in_aligned_cell -------------------------------------------------------


def test_a1_passes_when_value_at_correct_cell():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "Label"), make_cell(0, 1, "100.00")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "Label"), make_cell(0, 1, "100.00")])])
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 0, 1))]
    )
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.critical is True
    assert result.passed is True


def test_a1_fails_on_wrong_cell():
    # we01: the correct amount is present, but beside a different label (Subtotal instead of Total).
    gt = make_doc(tables=[make_table(0, [
        make_cell(0, 0, "Subtotal"), make_cell(0, 1, "90.00"),
        make_cell(1, 0, "Total"), make_cell(1, 1, "100.00"),
    ])])
    hyp = make_doc(tables=[make_table(0, [
        make_cell(0, 0, "Subtotal"), make_cell(0, 1, "100.00"),
        make_cell(1, 0, "Total"), make_cell(1, 1, "90.00"),
    ])])
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 1, 1))]
    )
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.passed is False
    assert "100.00" in result.detail


def test_a1_accepts_same_row_column_swap_and_split_tables():
    # Row-label alignment: a value beside its own label passes even when columns are swapped
    # or the row landed in a different table, because models split and merge tables freely.
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "Label"), make_cell(0, 1, "100.00")])])
    swapped = make_doc(tables=[make_table(0, [make_cell(0, 0, "100.00"), make_cell(0, 1, "Label")])])
    split = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")]), make_table(1, [make_cell(0, 0, "Label"), make_cell(0, 1, "100.00")])])
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 0, 1))]
    )
    assert get(run_assertions(gt, swapped, sidecar), "A1").passed
    assert get(run_assertions(gt, split, sidecar), "A1").passed


def test_a1_fails_on_wrong_digit():
    # we02: one wrong digit in the total, right cell.
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "100.00")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "199.00")])])
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 0, 1))]
    )
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.passed is False


def test_a1_vacuous_pass_when_no_critical_fields_have_cell_ref():
    gt = make_doc()
    hyp = make_doc()
    sidecar = make_sidecar(critical_fields=[])
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.passed is True


# --- A2: total_in_totals_row -----------------------------------------------------------


def test_a2_passes_when_hyp_row_has_total_and_keyword():
    gt_total_cell = make_cell(2, 1, "150.00", role="total_value")
    gt = make_doc(tables=[make_table(0, [make_cell(2, 0, "Total"), gt_total_cell])])
    hyp = make_doc(tables=[make_table(0, [make_cell(2, 0, "Total"), make_cell(2, 1, "150.00")])])
    sidecar = make_sidecar()
    result = get(run_assertions(gt, hyp, sidecar), "A2")
    assert result.passed is True


def test_a2_passes_when_hyp_row_is_last_numeric_row_without_keyword():
    # "Row2" deliberately avoids every word in _TOTAL_KEYWORD_RE so this
    # exercises the last-numeric-row fallback, not the keyword match.
    gt_total_cell = make_cell(1, 1, "150.00", role="total_value")
    gt = make_doc(tables=[make_table(0, [make_cell(1, 0, "Row2"), gt_total_cell])])
    hyp_table = make_table(
        0,
        [
            make_cell(0, 0, "Item", is_numeric=False),
            make_cell(0, 1, "10.00", is_numeric=True),
            make_cell(1, 0, "Row2", is_numeric=False),
            make_cell(1, 1, "150.00", is_numeric=True),
        ],
    )
    hyp = make_doc(tables=[hyp_table])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A2")
    assert result.passed is True


def test_a2_fails_when_total_value_absent_from_hyp():
    gt_total_cell = make_cell(2, 1, "150.00", role="total_value")
    gt = make_doc(tables=[make_table(0, [make_cell(2, 0, "Total"), gt_total_cell])])
    hyp = make_doc(tables=[make_table(0, [make_cell(2, 0, "Total"), make_cell(2, 1, "999.99")])])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A2")
    assert result.passed is False


def test_a2_vacuous_pass_when_gt_has_no_total_value_cells():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A2")
    assert result.passed is True


# --- A3: no_duplicated_financial_value --------------------------------------------------


def test_a3_passes_when_multiplicity_matches():
    gt = make_doc(fin_tokens=[make_fin_token(cents=1000), make_fin_token(cents=1000)])
    hyp = make_doc(fin_tokens=[make_fin_token(cents=1000), make_fin_token(cents=1000)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A3")
    assert result.passed is True


def test_a3_fails_on_duplicated_total():
    # we07: total value duplicated into a line row.
    gt = make_doc(fin_tokens=[make_fin_token(cents=1000)])
    hyp = make_doc(fin_tokens=[make_fin_token(cents=1000), make_fin_token(cents=1000)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A3")
    assert result.passed is False


def test_a3_tolerates_legitimate_gt_repeats():
    # e.g. case 027: the QR-bill total repeated across slips in GT itself.
    gt = make_doc(fin_tokens=[make_fin_token(cents=1000), make_fin_token(cents=1000)])
    hyp = make_doc(fin_tokens=[make_fin_token(cents=1000)])  # hyp under-reproduces, not over
    result = get(run_assertions(gt, hyp, make_sidecar()), "A3")
    assert result.passed is True


# --- A4: required_sections_present -----------------------------------------------------


def test_a4_passes_when_all_sections_present_case_insensitive():
    hyp = make_doc(section_headers=["INVOICE SUMMARY", "Payment Details"])
    sidecar = make_sidecar(required_sections=["invoice summary", "payment details"])
    result = get(run_assertions(make_doc(), hyp, sidecar), "A4")
    assert result.passed is True


def test_a4_fails_when_section_missing():
    hyp = make_doc(section_headers=["Invoice Summary"])
    sidecar = make_sidecar(required_sections=["Invoice Summary", "Payment Details"])
    result = get(run_assertions(make_doc(), hyp, sidecar), "A4")
    assert result.passed is False
    assert "Payment Details" in result.detail


def test_a4_vacuous_pass_when_no_required_sections():
    result = get(run_assertions(make_doc(), make_doc(), make_sidecar(required_sections=[])), "A4")
    assert result.passed is True


# --- A5: vat_letter_attached -------------------------------------------------------------


def test_a5_passes_when_attached_pattern_reproduced():
    gt = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=True)])
    hyp = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=True)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A5")
    assert result.passed is True


def test_a5_fails_when_vat_letter_detached_in_hyp():
    # we10: VAT letter detached from the price (GT has it attached, hyp
    # reproduces the same cents value but with a space/block separation).
    gt = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=True)])
    hyp = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=False)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A5")
    assert result.passed is False


def test_a5_fails_when_letter_wrong_despite_correct_cents_and_adjacency():
    # The letter carries the VAT rate: "59.99 D" reproduced as "59.99 E"
    # (same cents, same adjacency pattern) is a real content error, not
    # merely an adjacency question, and must fail with both letters named.
    gt = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=True)])
    hyp = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="E", attached=True)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A5")
    assert result.passed is False
    assert "D" in result.detail
    assert "E" in result.detail


def test_a5_fails_when_hyp_missing_the_amount_entirely():
    gt = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=True)])
    hyp = make_doc(fin_tokens=[])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A5")
    assert result.passed is False


def test_a5_vacuous_pass_when_no_attached_gt_vat_tokens():
    gt = make_doc(fin_tokens=[make_fin_token(type="vat_letter", cents=5999, vat_letter="D", attached=False)])
    hyp = make_doc(fin_tokens=[])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A5")
    assert result.passed is True


# --- A6: label_value_grouping_intact (non-critical) --------------------------------------


def test_a6_non_critical_and_passes_on_match():
    gt = make_doc(label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)])
    hyp = make_doc(label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A6")
    assert result.critical is False
    assert result.passed is True


def test_a6_fails_on_label_swap():
    gt = make_doc(
        label_value_pairs=[
            LabelValuePair("Opening balance", "100.00", "colon_pattern", None),
            LabelValuePair("Closing balance", "50.00", "colon_pattern", None),
        ]
    )
    hyp = make_doc(
        label_value_pairs=[
            LabelValuePair("Opening balance", "50.00", "colon_pattern", None),
            LabelValuePair("Closing balance", "100.00", "colon_pattern", None),
        ]
    )
    result = get(run_assertions(gt, hyp, make_sidecar()), "A6")
    assert result.passed is False


# --- A7: table_count_delta (non-critical) -------------------------------------------------


def test_a7_passes_on_equal_counts():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A7")
    assert result.critical is False
    assert result.passed is True


def test_a7_fails_on_unequal_counts():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")]), make_table(1, [make_cell(0, 0, "y")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A7")
    assert result.passed is False


# --- A8: line_item_intact (non-critical) --------------------------------------------------


def test_a8_passes_when_row_intact():
    hyp_table = make_table(0, [make_cell(0, 0, "Widget"), make_cell(0, 1, "19.99")])
    gt = make_doc(
        tables=[make_table(0, [make_cell(0, 0, "Widget"), make_cell(0, 1, "19.99")])],
        line_items=[LineItem(table_index=0, row=0, fields={"desc": "Widget", "price": "19.99"})],
    )
    hyp = make_doc(tables=[hyp_table])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A8")
    assert result.passed is True


def test_a8_fails_when_row_missing():
    gt_table = make_table(
        0,
        [
            make_cell(0, 0, "Widget"), make_cell(0, 1, "19.99"),
            make_cell(1, 0, "Gadget"), make_cell(1, 1, "5.00"),
        ],
    )
    hyp_table = make_table(0, [make_cell(0, 0, "Widget"), make_cell(0, 1, "19.99")])  # Gadget row gone
    gt = make_doc(
        tables=[gt_table],
        line_items=[
            LineItem(table_index=0, row=0, fields={"desc": "Widget", "price": "19.99"}),
            LineItem(table_index=0, row=1, fields={"desc": "Gadget", "price": "5.00"}),
        ],
    )
    hyp = make_doc(tables=[hyp_table])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A8")
    assert result.passed is False
    assert "Gadget" in result.detail


# --- A9: heading_sequence_match (non-critical) --------------------------------------------


def test_a9_passes_on_exact_sequence():
    gt = make_doc(section_headers=["Intro", "Details"])
    hyp = make_doc(section_headers=["Intro", "Details"])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A9")
    assert result.passed is True


def test_a9_fails_on_reordered_sequence():
    gt = make_doc(section_headers=["Intro", "Details"])
    hyp = make_doc(section_headers=["Details", "Intro"])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A9")
    assert result.passed is False


# --- A10: no_hallucinated_amount (non-critical) -------------------------------------------


def test_a10_passes_when_every_hyp_amount_in_gt():
    gt = make_doc(fin_tokens=[make_fin_token(cents=1000), make_fin_token(cents=2000)])
    hyp = make_doc(fin_tokens=[make_fin_token(cents=1000)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A10")
    assert result.passed is True


def test_a10_fails_on_hallucinated_amount():
    gt = make_doc(fin_tokens=[make_fin_token(cents=1000)])
    hyp = make_doc(fin_tokens=[make_fin_token(cents=1000), make_fin_token(cents=9999)])
    result = get(run_assertions(gt, hyp, make_sidecar()), "A10")
    assert result.passed is False
    assert "99.99" in result.detail


# --- baseline / no-tables document (all vacuous passes where applicable) -----------------


def test_no_tables_document_all_critical_assertions_pass_vacuously():
    gt = make_doc(section_headers=["Letter"], label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)])
    hyp = make_doc(section_headers=["Letter"], label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)])
    results = run_assertions(gt, hyp, make_sidecar())
    critical = [r for r in results if r.critical]
    assert all(r.passed for r in critical)


def test_run_assertions_returns_all_ten_ids_in_order():
    results = run_assertions(make_doc(), make_doc(), make_sidecar())
    assert [r.id for r in results] == [f"A{i}" for i in range(1, 11)]
    assert sum(1 for r in results if r.critical) == 5
    assert sum(1 for r in results if not r.critical) == 5

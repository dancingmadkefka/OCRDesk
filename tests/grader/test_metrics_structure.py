"""Tests for ocrgrade.metrics_structure.

Hand-builds IR objects directly (Scope B contract: no import of Scope A's
canonicalize/tables modules).
"""
from __future__ import annotations

import pytest

from ocrgrade.ir import Cell, Document, LabelValuePair, LineItem, Table
from ocrgrade.metrics_structure import structure_metrics


# --- private test builders ---------------------------------------------------------


def make_cell(row, col, text="", role="other", is_numeric=False, table_index=0) -> Cell:
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
        tokens=[],
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
) -> Document:
    return Document(
        tables=tables or [],
        section_headers=section_headers or [],
        label_value_pairs=label_value_pairs or [],
        line_items=line_items or [],
        fin_tokens=[],
        body_text_norm="",
        parse_ok=True,
        parse_error=None,
        was_fragment=False,
        truncated=False,
    )


def make_lv_pair(label, value, source="colon_pattern") -> LabelValuePair:
    return LabelValuePair(label=label, value=value, source=source, cell_ref=None)


# --- cell_content_f1 (via structure_metrics) --------------------------------------------


def _simple_table_doc(text_grid, table_index=0):
    cells = [
        make_cell(r, c, text, table_index=table_index)
        for r, row in enumerate(text_grid)
        for c, text in enumerate(row)
    ]
    return make_table(table_index, cells)


def test_cell_content_f1_perfect_alignment_is_one():
    gt_table = _simple_table_doc([["Total", "100.00"]])
    hyp_table = _simple_table_doc([["Total", "100.00"]])
    gt = make_doc(tables=[gt_table])
    hyp = make_doc(tables=[hyp_table])
    m = structure_metrics(gt, hyp)
    assert m["cell_content_f1"] == pytest.approx(1.0)


def test_cell_content_f1_wrong_cell_value_reduces_score():
    gt_table = _simple_table_doc([["Total", "100.00"]])
    hyp_table = _simple_table_doc([["Total", "999.99"]])
    gt = make_doc(tables=[gt_table])
    hyp = make_doc(tables=[hyp_table])
    m = structure_metrics(gt, hyp)
    assert m["cell_content_f1"] < 1.0


def test_cell_content_f1_missing_hyp_table_scores_zero_for_those_cells():
    gt_table = _simple_table_doc([["Total", "100.00"]])
    gt = make_doc(tables=[gt_table])
    hyp = make_doc(tables=[])  # hyp produced no tables at all
    m = structure_metrics(gt, hyp)
    assert m["cell_content_f1"] == pytest.approx(0.0)


def test_cell_content_f1_ignores_empty_gt_cells():
    gt_table = make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "")])
    hyp_table = make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "whatever")])
    gt = make_doc(tables=[gt_table])
    hyp = make_doc(tables=[hyp_table])
    m = structure_metrics(gt, hyp)
    # only the non-empty "Total" cell counts -> perfect match -> 1.0
    assert m["cell_content_f1"] == pytest.approx(1.0)


# --- label_value_f1 -----------------------------------------------------------------------


def test_label_value_f1_full_match_is_one():
    gt_pairs = [make_lv_pair("Opening balance", "100.00"), make_lv_pair("Closing balance", "50.00")]
    hyp_pairs = [make_lv_pair("Opening balance", "100.00"), make_lv_pair("Closing balance", "50.00")]
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])], label_value_pairs=gt_pairs)
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])], label_value_pairs=hyp_pairs)
    m = structure_metrics(gt, hyp)
    assert m["label_value_f1"] == pytest.approx(1.0)


def test_label_value_f1_swapped_values_fails_to_match():
    # we06: opening/closing balance swapped -- same values present but
    # attached to the wrong labels, so neither GT pair finds a same-labeled
    # match in hyp.
    gt_pairs = [make_lv_pair("Opening balance", "100.00"), make_lv_pair("Closing balance", "50.00")]
    hyp_pairs = [make_lv_pair("Opening balance", "50.00"), make_lv_pair("Closing balance", "100.00")]
    gt = make_doc(label_value_pairs=gt_pairs)
    hyp = make_doc(label_value_pairs=hyp_pairs)
    m = structure_metrics(gt, hyp)
    assert m["label_value_f1"] == pytest.approx(0.0)


def test_label_value_f1_none_when_no_pairs_on_either_side():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    m = structure_metrics(gt, hyp)
    assert m["label_value_f1"] is None


# --- line_item_f1 -------------------------------------------------------------------------


def test_line_item_f1_intact_row_is_one():
    hyp_table = _simple_table_doc([["Widget", "2", "19.99"]])
    gt = make_doc(
        tables=[_simple_table_doc([["Widget", "2", "19.99"]])],
        line_items=[LineItem(table_index=0, row=0, fields={"desc": "Widget", "qty": "2", "price": "19.99"})],
    )
    hyp = make_doc(tables=[hyp_table])
    m = structure_metrics(gt, hyp)
    assert m["line_item_f1"] == pytest.approx(1.0)


def test_line_item_f1_omitted_row_reduces_fraction():
    # we_missing_row / we04: one line item's row is simply absent from hyp.
    gt_table = _simple_table_doc([["Widget", "2", "19.99"], ["Gadget", "1", "5.00"]])
    hyp_table = _simple_table_doc([["Widget", "2", "19.99"]])  # "Gadget" row missing
    gt = make_doc(
        tables=[gt_table],
        line_items=[
            LineItem(table_index=0, row=0, fields={"desc": "Widget", "qty": "2", "price": "19.99"}),
            LineItem(table_index=0, row=1, fields={"desc": "Gadget", "qty": "1", "price": "5.00"}),
        ],
    )
    hyp = make_doc(tables=[hyp_table])
    m = structure_metrics(gt, hyp)
    assert m["line_item_f1"] == pytest.approx(0.5)


def test_line_item_f1_none_when_gt_has_no_line_items():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    m = structure_metrics(gt, hyp)
    assert m["line_item_f1"] is None


# --- heading_sequence_score (indirectly, via the no-tables fallback chain below) --------


def test_heading_sequence_used_as_fallback_when_no_tables_and_no_label_values():
    gt = make_doc(section_headers=["Intro", "Details", "Summary"])
    hyp = make_doc(section_headers=["Intro", "Details", "Summary"])
    m = structure_metrics(gt, hyp)
    assert m["structure_na"] is True
    assert m["heading_sequence_score"] == pytest.approx(1.0)
    assert m["structure_score"] == pytest.approx(1.0)


def test_heading_sequence_score_partial_match_less_than_one():
    gt = make_doc(section_headers=["Intro", "Details", "Summary"])
    hyp = make_doc(section_headers=["Intro", "Summary"])  # "Details" dropped
    m = structure_metrics(gt, hyp)
    assert 0.0 < m["heading_sequence_score"] < 1.0


# --- structure_na fallback chain (plan section 5) -----------------------------------------


def test_no_tables_document_falls_back_to_label_value_f1():
    # we_notable: table-less letter, label-value only. Headings are made to
    # mismatch (so heading_sequence_score < 1.0) precisely to prove the
    # fallback chain picks label_value_f1 first rather than blending in or
    # falling through to the heading score.
    gt_pairs = [make_lv_pair("Date", "2024-01-01")]
    hyp_pairs = [make_lv_pair("Date", "2024-01-01")]
    gt = make_doc(section_headers=["Letter", "Body"], label_value_pairs=gt_pairs)
    hyp = make_doc(section_headers=["Letter"], label_value_pairs=hyp_pairs)
    m = structure_metrics(gt, hyp)
    assert m["structure_na"] is True
    assert m["table_count_gt"] == 0
    assert m["label_value_f1"] == pytest.approx(1.0)
    assert m["heading_sequence_score"] < 1.0
    assert m["structure_score"] == pytest.approx(1.0)  # uses label_value_f1, not heading


def test_no_tables_no_label_values_falls_back_to_heading_sequence():
    gt = make_doc(section_headers=["A", "B"])
    hyp = make_doc(section_headers=["A"])
    m = structure_metrics(gt, hyp)
    assert m["structure_na"] is True
    assert m["label_value_f1"] is None
    assert m["structure_score"] == m["heading_sequence_score"]
    assert m["structure_score"] is not None


def test_no_tables_no_label_values_no_headings_structure_score_is_none():
    gt = make_doc()
    hyp = make_doc()
    m = structure_metrics(gt, hyp)
    assert m["structure_na"] is True
    assert m["label_value_f1"] is None
    assert m["heading_sequence_score"] is None
    assert m["structure_score"] is None


def test_tables_present_never_marks_structure_na():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    m = structure_metrics(gt, hyp)
    assert m["structure_na"] is False
    assert m["structure_score"] is not None


# --- teds/teds_struct pass-through and table counts -------------------------------------


def test_teds_pass_through_and_table_counts():
    outer_html = "<table><tr><td>Hello</td></tr></table>"
    gt_table = make_table(0, [make_cell(0, 0, "Hello")], outer_html)
    hyp_table = make_table(0, [make_cell(0, 0, "Hello")], outer_html)
    gt = make_doc(tables=[gt_table])
    hyp = make_doc(tables=[hyp_table])
    m = structure_metrics(gt, hyp)
    assert m["teds"] == pytest.approx(1.0)
    assert m["teds_struct"] == pytest.approx(1.0)
    assert m["table_count_gt"] == 1
    assert m["table_count_hyp"] == 1


def test_table_count_mismatch_is_reported_not_hidden():
    gt = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")]), make_table(1, [make_cell(0, 0, "y")])])
    hyp = make_doc(tables=[make_table(0, [make_cell(0, 0, "x")])])
    m = structure_metrics(gt, hyp)
    assert m["table_count_gt"] == 2
    assert m["table_count_hyp"] == 1

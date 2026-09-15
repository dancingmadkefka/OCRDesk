"""Tests for ocrgrade.teds_adapter.

Hand-builds `Table`/`Cell` IR objects directly (per the Scope B contract: no
import of Scope A's canonicalize/tables modules).
"""
from __future__ import annotations

import time

import pytest
from table_recognition_metric import TEDS

from ocrgrade import teds_adapter
from ocrgrade.ir import Cell, Table
from ocrgrade.teds_adapter import teds_scores


# --- private test builders (per Scope B instructions: no shared fixtures module) ---


def make_cell(row, col, text="", is_numeric=False, table_index=0) -> Cell:
    return Cell(
        table_index=table_index,
        row=row,
        col=col,
        rowspan=1,
        colspan=1,
        is_span_origin=True,
        text_raw=text,
        text_norm=text,
        role="other",
        is_numeric=is_numeric,
        is_header=False,
        tokens=[],
    )


def make_table(index, cells, outer_html) -> Table:
    n_rows = max((c.row for c in cells), default=-1) + 1
    n_cols = max((c.col for c in cells), default=-1) + 1
    return Table(index=index, n_rows=n_rows, n_cols=n_cols, cells=cells, outer_html=outer_html)


# --- the footgun this module exists to avoid --------------------------------------


def test_full_document_teds_call_silently_scores_zero_on_div_wrapped_table():
    """Documents the exact hazard from docs/grader-plan.md section 5/10#1:
    calling TEDS() directly on a div-wrapped document (never rewrapped)
    scores 0.0 even for an identical table, because `body/table` only
    matches a direct child."""
    outer_html = "<table><tr><td>Hello</td></tr></table>"
    div_wrapped = f"<html><body><div class='wrapper'>{outer_html}</div></body></html>"
    assert TEDS(structure_only=False)(div_wrapped, div_wrapped) == 0.0


def test_div_nested_table_pair_scores_nonzero_through_adapter():
    """The adapter isolates `Table.outer_html` (already just `<table>...</table>`,
    with no div ancestor) and rewraps it directly, so the same table content
    that would silently score 0.0 via a naive full-document TEDS() call
    scores nonzero when routed through `teds_scores`."""
    outer_html = "<table><tr><td>Hello</td><td>World</td></tr></table>"
    gt_table = make_table(0, [make_cell(0, 0, "Hello"), make_cell(0, 1, "World")], outer_html)
    hyp_table = make_table(0, [make_cell(0, 0, "Hello"), make_cell(0, 1, "World")], outer_html)

    result = teds_scores([gt_table], [hyp_table])

    assert result.n_pairs == 1
    assert result.teds is not None
    assert result.teds > 0.0


def test_identical_tables_score_one():
    outer_html = "<table><tr><td>Total</td><td>100.00</td></tr></table>"
    gt_table = make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "100.00")], outer_html)
    hyp_table = make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "100.00")], outer_html)

    result = teds_scores([gt_table], [hyp_table])

    assert result.teds == pytest.approx(1.0)
    assert result.teds_struct == pytest.approx(1.0)
    assert result.per_table == [pytest.approx(1.0)]
    assert result.timed_out is False


# --- N/A vs real-zero edge cases ---------------------------------------------------


def test_no_gt_tables_returns_none_scores():
    result = teds_scores([], [])
    assert result.teds is None
    assert result.teds_struct is None
    assert result.n_pairs == 0
    assert result.per_table == []


def test_no_gt_tables_with_hyp_tables_present_still_returns_none():
    outer_html = "<table><tr><td>X</td></tr></table>"
    hyp_table = make_table(0, [make_cell(0, 0, "X")], outer_html)
    result = teds_scores([], [hyp_table])
    assert result.teds is None
    assert result.teds_struct is None


def test_gt_tables_present_hyp_empty_scores_zero_not_none():
    outer_html = "<table><tr><td>X</td></tr></table>"
    gt_table = make_table(0, [make_cell(0, 0, "X")], outer_html)
    result = teds_scores([gt_table], [])
    assert result.teds == 0.0
    assert result.teds_struct == 0.0
    assert result.n_pairs == 0


# --- guards: cell cap and wall-clock timeout ---------------------------------------


def test_cell_cap_scores_zero_and_flags_timed_out():
    big_cells = [make_cell(r, 0, "x") for r in range(teds_adapter.MAX_CELLS_PER_TABLE + 1)]
    gt_table = make_table(0, big_cells, outer_html="<table></table>")
    hyp_table = make_table(0, [make_cell(0, 0, "x")], outer_html="<table><tr><td>x</td></tr></table>")

    result = teds_scores([gt_table], [hyp_table])

    assert result.per_table == [0.0]
    assert result.timed_out is True
    assert result.n_pairs == 1


def test_wall_clock_guard_scores_zero_and_flags_timed_out(monkeypatch):
    class SlowTEDS:
        def __init__(self, structure_only: bool = False) -> None:
            self.structure_only = structure_only

        def __call__(self, pred: str, gt: str) -> float:
            time.sleep(0.2)
            return 1.0

    monkeypatch.setattr(teds_adapter, "TEDS", SlowTEDS)
    monkeypatch.setattr(teds_adapter, "TIMEOUT_SECONDS", 0.01)

    outer_html = "<table><tr><td>x</td></tr></table>"
    gt_table = make_table(0, [make_cell(0, 0, "x")], outer_html)
    hyp_table = make_table(0, [make_cell(0, 0, "x")], outer_html)

    result = teds_scores([gt_table], [hyp_table])

    assert result.per_table == [0.0]
    assert result.timed_out is True


# --- aggregation ---------------------------------------------------------------------


def test_aggregate_is_cell_count_weighted_mean_of_per_table_scores():
    t0_html = "<table><tr><td>Hello</td></tr></table>"
    gt_t0 = make_table(0, [make_cell(0, 0, "Hello")], t0_html)
    hyp_t0 = make_table(0, [make_cell(0, 0, "Hello")], t0_html)

    gt_t1_cells = [make_cell(r, c, f"g{r}{c}") for r in range(2) for c in range(2)]
    gt_t1_html = "<table>" + "".join(
        "<tr>" + "".join(f"<td>g{r}{c}</td>" for c in range(2)) + "</tr>" for r in range(2)
    ) + "</table>"
    gt_t1 = make_table(1, gt_t1_cells, gt_t1_html)

    hyp_t1_cells = [make_cell(r, c, f"h{r}{c}") for r in range(2) for c in range(2)]
    hyp_t1_html = "<table>" + "".join(
        "<tr>" + "".join(f"<td>h{r}{c}</td>" for c in range(2)) + "</tr>" for r in range(2)
    ) + "</table>"
    hyp_t1 = make_table(1, hyp_t1_cells, hyp_t1_html)

    result = teds_scores([gt_t0, gt_t1], [hyp_t0, hyp_t1])

    assert result.n_pairs == 2
    w0, w1 = 1, 4  # cell counts of gt_t0 and gt_t1
    expected = (result.per_table[0] * w0 + result.per_table[1] * w1) / (w0 + w1)
    assert result.teds == pytest.approx(expected)


def test_pairing_is_by_document_order_up_to_min_length():
    html_a = "<table><tr><td>A</td></tr></table>"
    html_b = "<table><tr><td>B</td></tr></table>"
    gt_tables = [make_table(0, [make_cell(0, 0, "A")], html_a), make_table(1, [make_cell(0, 0, "B")], html_b)]
    hyp_tables = [make_table(0, [make_cell(0, 0, "A")], html_a)]  # only one hyp table

    result = teds_scores(gt_tables, hyp_tables)

    assert result.n_pairs == 1
    assert result.per_table == [pytest.approx(1.0)]

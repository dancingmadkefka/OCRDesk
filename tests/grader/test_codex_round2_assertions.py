"""Codex round-2 review of OCRDesk PR #1: four fixes in assertions.py, metrics_content.py,
and metrics_structure.py's `_line_item_f1`.

1. assertions.py:85 -- critical amounts compare as parsed tokens (cents), never as raw
   substrings, so '12.34' cannot be satisfied by '112.34' or by a sign-flipped '-12.34'.
2. metrics_content.py:92 -- the FIN-EM amount comparison is currency-aware: cents must match,
   and a stated currency must not contradict the other side's (absent currency is not itself
   an error).
3. assertions.py:475 -- A5 matches attached GT VAT-letter tokens against hypothesis tokens as
   a multiset, consuming each hypothesis occurrence once.
4. assertions.py:557 and metrics_structure.py's `_line_item_f1` -- both consume a hypothesis
   row once it is assigned to a GT line item, so two identical GT rows can't both match the
   same single hypothesis row.

Uses the shared `ir_factory` fixture from conftest.py rather than hand-rolling IR builders.
"""
from __future__ import annotations

from ocrgrade.assertions import run_assertions
from ocrgrade.metrics_content import content_metrics
from ocrgrade.metrics_structure import structure_metrics


def get(results, assertion_id):
    return next(r for r in results if r.id == assertion_id)


# --- 1a: A1 critical amounts compare as tokens, not substrings --------------------------


def test_a1_rejects_amount_substring_superset(ir_factory):
    # GT critical value 12.34; the hyp cell holds 112.34, which contains '12.34' as a raw
    # substring. Must fail -- a wrong total can no longer sneak past as an aligned value.
    gt = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="12.34"),
    ])])
    hyp = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="112.34"),
    ])])
    sidecar = ir_factory.sidecar(
        critical_fields=[ir_factory.critical_field(value="12.34", cell_ref=ir_factory.cell_ref(0, 0, 1))]
    )
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.passed is False


def test_a1_rejects_sign_flip_via_substring(ir_factory):
    # GT critical value 12.34; the hyp cell holds -12.34. '12.34' is a raw substring of
    # '-12.34' (drop the sign), which is exactly the direction the old substring check missed --
    # the existing suite only covered GT=-12.34/hyp=12.34, not this reverse case.
    gt = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="12.34"),
    ])])
    hyp = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="-12.34"),
    ])])
    sidecar = ir_factory.sidecar(
        critical_fields=[ir_factory.critical_field(value="12.34", cell_ref=ir_factory.cell_ref(0, 0, 1))]
    )
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.passed is False


def test_a1_accepts_amount_embedded_with_currency_prefix_via_live_parse(ir_factory):
    # Positive control: a cell built without pre-extracted tokens (as every hand-built Cell in
    # this suite is) must still align by cents, parsed fresh from its text -- '12.34' inside
    # 'CHF 12.34' is a real cents match, not the substring shortcut this fix removes.
    gt = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="12.34"),
    ])])
    hyp = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="CHF 12.34"),
    ])])
    sidecar = ir_factory.sidecar(
        critical_fields=[ir_factory.critical_field(value="12.34", cell_ref=ir_factory.cell_ref(0, 0, 1))]
    )
    result = get(run_assertions(gt, hyp, sidecar), "A1")
    assert result.passed is True, result.detail


# --- 1b: A2's row search compares row amount tokens, not substrings ---------------------


def test_a2_rejects_amount_substring_superset(ir_factory):
    gt = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="12.34", role="total_value"),
    ])])
    hyp = ir_factory.document(tables=[ir_factory.table(cells=[
        ir_factory.cell(row=0, col=0, text_raw="Total"),
        ir_factory.cell(row=0, col=1, text_raw="112.34"),
    ])])
    result = get(run_assertions(gt, hyp, ir_factory.sidecar()), "A2")
    assert result.passed is False


# --- 2: FIN-EM amount comparison is currency-aware ----------------------------------------


def test_fin_em_amount_currency_mismatch_is_an_error(ir_factory):
    gt = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="EUR")])
    hyp = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="GBP")])
    m = content_metrics(gt, hyp)
    assert m["fin_em"]["amount"] == 0.0
    assert m["fin_em_amounts"] == 0.0


def test_fin_em_amount_missing_currency_is_not_an_error(ir_factory):
    # Either side may simply omit the symbol -- that alone is not a content error.
    gt = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="EUR")])
    hyp_no_symbol = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency=None)])
    assert content_metrics(gt, hyp_no_symbol)["fin_em_amounts"] == 1.0

    gt_no_symbol = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency=None)])
    hyp = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="EUR")])
    assert content_metrics(gt_no_symbol, hyp)["fin_em_amounts"] == 1.0


def test_fin_em_amount_vacuous_when_gt_has_no_amounts(ir_factory):
    gt = ir_factory.document(fin_tokens=[])
    hyp = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="GBP")])
    m = content_metrics(gt, hyp)
    assert m["fin_em_amounts"] == 1.0
    assert m["fin_em_na"]["amount"] is True


def test_spurious_amounts_consistent_with_fin_em_currency_rule(ir_factory):
    # Same scenario as the currency-mismatch FIN-EM test above: the hyp amount is a distinct
    # financial fact from the GT one (same cents, contradicting currency), so it must also
    # count as spurious -- spurious_amounts and fin_em_amounts must agree on that.
    gt = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="EUR")])
    hyp = ir_factory.document(fin_tokens=[ir_factory.fin_token(cents=1234, currency="GBP")])
    m = content_metrics(gt, hyp)
    assert m["fin_em_amounts"] == 0.0
    assert m["spurious_amounts"] == 1


# --- 3: A5 matches attached VAT-letter tokens as a consumed multiset ---------------------


def test_a5_fails_when_repeated_amount_has_only_one_hyp_vat_occurrence(ir_factory):
    # GT: two attached '59.99 A' occurrences (e.g. two identical priced items on a receipt).
    # hyp: only one. The second GT occurrence must not reuse the first's hyp token.
    gt = ir_factory.document(fin_tokens=[
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
    ])
    hyp = ir_factory.document(fin_tokens=[
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
    ])
    result = get(run_assertions(gt, hyp, ir_factory.sidecar()), "A5")
    assert result.passed is False


def test_a5_passes_when_repeated_amount_has_matching_hyp_occurrences(ir_factory):
    # Sanity check: consuming hyp tokens on match must not break the legitimate case where
    # every repeat really is reproduced.
    gt = ir_factory.document(fin_tokens=[
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
    ])
    hyp = ir_factory.document(fin_tokens=[
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
        ir_factory.fin_token(type="vat_letter", cents=5999, vat_letter="A", attached=True),
    ])
    result = get(run_assertions(gt, hyp, ir_factory.sidecar()), "A5")
    assert result.passed is True


# --- 4: A8 and line_item_f1 consume a hyp row per matched GT item -------------------------


def _repeated_widget_rows(ir_factory, n_rows: int):
    cells = []
    for r in range(n_rows):
        cells.append(ir_factory.cell(row=r, col=0, text_raw="Widget"))
        cells.append(ir_factory.cell(row=r, col=1, text_raw="19.99"))
    return ir_factory.table(cells=cells)


def test_a8_fails_when_repeated_line_item_has_only_one_hyp_row(ir_factory):
    gt = ir_factory.document(
        tables=[_repeated_widget_rows(ir_factory, 2)],
        line_items=[
            ir_factory.line_item(table_index=0, row=0, fields={"desc": "Widget", "price": "19.99"}),
            ir_factory.line_item(table_index=0, row=1, fields={"desc": "Widget", "price": "19.99"}),
        ],
    )
    hyp = ir_factory.document(tables=[_repeated_widget_rows(ir_factory, 1)])
    result = get(run_assertions(gt, hyp, ir_factory.sidecar()), "A8")
    assert result.passed is False


def test_a8_passes_when_repeated_line_item_has_matching_hyp_rows(ir_factory):
    gt = ir_factory.document(
        tables=[_repeated_widget_rows(ir_factory, 2)],
        line_items=[
            ir_factory.line_item(table_index=0, row=0, fields={"desc": "Widget", "price": "19.99"}),
            ir_factory.line_item(table_index=0, row=1, fields={"desc": "Widget", "price": "19.99"}),
        ],
    )
    hyp = ir_factory.document(tables=[_repeated_widget_rows(ir_factory, 2)])
    result = get(run_assertions(gt, hyp, ir_factory.sidecar()), "A8")
    assert result.passed is True


def test_line_item_f1_below_one_when_repeated_row_has_only_one_hyp_occurrence(ir_factory):
    gt = ir_factory.document(
        tables=[_repeated_widget_rows(ir_factory, 2)],
        line_items=[
            ir_factory.line_item(table_index=0, row=0, fields={"desc": "Widget", "price": "19.99"}),
            ir_factory.line_item(table_index=0, row=1, fields={"desc": "Widget", "price": "19.99"}),
        ],
    )
    hyp = ir_factory.document(tables=[_repeated_widget_rows(ir_factory, 1)])
    m = structure_metrics(gt, hyp)
    assert m["line_item_f1"] == 0.5


def test_line_item_f1_is_one_when_repeated_row_has_matching_hyp_occurrences(ir_factory):
    gt = ir_factory.document(
        tables=[_repeated_widget_rows(ir_factory, 2)],
        line_items=[
            ir_factory.line_item(table_index=0, row=0, fields={"desc": "Widget", "price": "19.99"}),
            ir_factory.line_item(table_index=0, row=1, fields={"desc": "Widget", "price": "19.99"}),
        ],
    )
    hyp = ir_factory.document(tables=[_repeated_widget_rows(ir_factory, 2)])
    m = structure_metrics(gt, hyp)
    assert m["line_item_f1"] == 1.0

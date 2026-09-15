"""Tests for ocrgrade.scoring: score_document, rank_key, rollup.

Hand-builds IR objects directly (Scope B contract: no import of Scope A's
canonicalize/tables/fintoken modules).
"""
from __future__ import annotations

import pytest

from ocrgrade.ir import (
    Cell,
    CellRef,
    CriticalField,
    Document,
    FinToken,
    LabelValuePair,
    Sidecar,
    Table,
)
from ocrgrade.scoring import rank_key, rollup, score_document


# --- private test builders ---------------------------------------------------------


def make_cell(
    row, col, text="", role="other", is_numeric=False, is_header=False, tokens=None, table_index=0
) -> Cell:
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
        is_header=is_header,
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
    body_text_norm="",
    parse_ok=True,
    parse_error=None,
    truncated=False,
) -> Document:
    return Document(
        tables=tables or [],
        section_headers=section_headers or [],
        label_value_pairs=label_value_pairs or [],
        line_items=line_items or [],
        fin_tokens=fin_tokens or [],
        body_text_norm=body_text_norm,
        parse_ok=parse_ok,
        parse_error=parse_error,
        was_fragment=False,
        truncated=truncated,
    )


def make_sidecar(
    required_sections=None,
    critical_fields=None,
    case_id="case1",
    category="invoice",
    confirmed=False,
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
        confirmed=confirmed,
    )


def make_fin_token(type="amount", canonical=None, cents=None) -> FinToken:
    return FinToken(
        type=type,  # type: ignore[arg-type]
        raw="",
        canonical=canonical,
        cents=cents,
        currency=None,
        vat_letter=None,
        attached=False,
        cell_ref=None,
    )


def _perfect_match_doc():
    table = make_table(
        0, [make_cell(0, 0, "Total", role="total_value"), make_cell(0, 1, "100.00", is_numeric=True)]
    )
    # Includes a date/id/percent token alongside the amount so a genuine
    # perfect match saturates fin_em_ids_dates_pct too -- otherwise an
    # amount-only fixture caps content_score at 0.75 even when everything
    # present matches, since FIN_EM(type) is 0 (not N/A) for a type absent
    # from GT (see metrics_content's literal empty-set-recall formula).
    fin_tokens = [
        make_fin_token(type="amount", cents=10000, canonical="100.00"),
        make_fin_token(type="date", canonical="2024-01-01"),
        make_fin_token(type="reference_id", canonical="REF1"),
        make_fin_token(type="percent", canonical="19%"),
    ]
    return make_doc(
        tables=[table],
        body_text_norm="Total 100.00 2024-01-01 REF1 19%",
        fin_tokens=fin_tokens,
    )


# --- Tier 0: CATASTROPHIC ------------------------------------------------------------


def test_catastrophic_on_harness_error_status():
    gt = _perfect_match_doc()
    hyp = _perfect_match_doc()
    result = score_document(gt, hyp, make_sidecar(), status="error")
    assert result.tier == "CATASTROPHIC"
    assert result.metrics is None
    assert result.display_score == 0.0
    assert result.assertions == []
    assert result.errors


def test_catastrophic_on_parse_failure():
    gt = _perfect_match_doc()
    hyp = make_doc(parse_ok=False, parse_error="boom")
    result = score_document(gt, hyp, make_sidecar())
    assert result.tier == "CATASTROPHIC"
    assert result.metrics is None
    assert result.display_score == 0.0
    assert "boom" in result.errors[0]


def test_catastrophic_on_truncation():
    gt = _perfect_match_doc()
    hyp = make_doc(truncated=True)
    result = score_document(gt, hyp, make_sidecar())
    assert result.tier == "CATASTROPHIC"
    assert result.metrics is None
    assert result.display_score == 0.0


def test_catastrophic_result_still_reports_table_counts():
    gt = _perfect_match_doc()
    hyp = make_doc(parse_ok=False)
    result = score_document(gt, hyp, make_sidecar())
    assert result.table_count_gt == 1
    assert result.table_count_hyp == 0


# --- REJECT: critical assertion failure ------------------------------------------------


def test_reject_when_critical_field_misaligned():
    gt = make_doc(
        tables=[make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "100.00")])],
        body_text_norm="Total 100.00",
    )
    hyp = make_doc(
        tables=[make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "999.99")])],
        body_text_norm="Total 999.99",
    )
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 0, 1))]
    )
    result = score_document(gt, hyp, sidecar)
    assert result.tier == "REJECT"
    assert result.display_score <= 59.0
    assert any(a.id == "A1" and not a.passed for a in result.assertions)


def test_reject_ceiling_holds_even_when_q_is_very_high():
    # Rich content that matches almost perfectly (content_f1, fin_em, and
    # reading order all near-1.0) with only one cell's digits wrong at the
    # critical field's coordinates: Q ends up high (~0.96), but display_score
    # must still clamp to <= 59 because a critical assertion fails. Guards
    # the literal "REJECT with q~0.99 must show display_score <= 59"
    # acceptance check.
    gt_table = make_table(
        0,
        [
            make_cell(0, 0, "Description", role="header", is_header=True),
            make_cell(0, 1, "Amount", role="header", is_header=True),
            make_cell(1, 0, "Widget"),
            make_cell(1, 1, "50.00", is_numeric=True),
            make_cell(2, 0, "Total", role="total_value"),
            make_cell(2, 1, "100.00", is_numeric=True),
        ],
    )
    hyp_table = make_table(
        0,
        [
            make_cell(0, 0, "Description", role="header", is_header=True),
            make_cell(0, 1, "Amount", role="header", is_header=True),
            make_cell(1, 0, "Widget"),
            make_cell(1, 1, "50.00", is_numeric=True),
            make_cell(2, 0, "Total", role="total_value"),
            make_cell(2, 1, "999.99", is_numeric=True),  # only this digit is wrong
        ],
    )
    fin_tokens = [
        make_fin_token(type="amount", cents=10000, canonical="100.00"),
        make_fin_token(type="amount", cents=5000, canonical="50.00"),
        make_fin_token(type="date", canonical="2024-01-01"),
        make_fin_token(type="reference_id", canonical="REF1"),
        make_fin_token(type="percent", canonical="19%"),
    ]
    body = "Description Amount Widget 50.00 Total 100.00 2024-01-01 REF1 19%"
    gt = make_doc(tables=[gt_table], body_text_norm=body, fin_tokens=fin_tokens)
    # hyp's extracted fin_tokens/body text are unaffected by the one wrong
    # table cell (a deliberately simplified fixture: this isolates "one
    # structural cell misplacement" from "content extraction elsewhere",
    # which is exactly the scenario the acceptance check is about).
    hyp = make_doc(tables=[hyp_table], body_text_norm=body.replace("Total 100.00", "Total 999.99"), fin_tokens=fin_tokens)
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 2, 1))]
    )
    result = score_document(gt, hyp, sidecar)
    assert result.tier == "REJECT"
    assert any(a.id == "A1" and not a.passed for a in result.assertions)
    assert result.metrics["q"] > 0.9  # nearly everything else matches
    assert result.display_score <= 59.0
    assert result.display_score == pytest.approx(min(59.0, 100.0 * result.metrics["q"]))


# --- PASS: everything matches ------------------------------------------------------------


def test_pass_on_perfect_match():
    gt = _perfect_match_doc()
    hyp = _perfect_match_doc()
    sidecar = make_sidecar(
        critical_fields=[CriticalField(role="grand_total", value="100.00", cell_ref=CellRef(0, 0, 1))]
    )
    result = score_document(gt, hyp, sidecar)
    assert result.tier == "PASS"
    assert result.display_score == pytest.approx(100.0 * result.metrics["q"])
    assert result.metrics["q"] == pytest.approx(1.0)
    assert result.display_score == pytest.approx(100.0)


def test_archival_safe_true_on_perfect_pass():
    gt = _perfect_match_doc()
    hyp = _perfect_match_doc()
    result = score_document(gt, hyp, make_sidecar())
    assert result.tier == "PASS"
    assert result.archival_safe is True


def test_archival_safe_false_when_spurious_amount_present():
    gt = _perfect_match_doc()
    hyp_table = make_table(
        0,
        [
            make_cell(0, 0, "Total", role="total_value"),
            make_cell(0, 1, "100.00", is_numeric=True),
            make_cell(1, 0, "Extra", is_numeric=False),
            make_cell(1, 1, "5.00", is_numeric=True),
        ],
    )
    hyp = make_doc(
        tables=[hyp_table],
        body_text_norm="Total 100.00 Extra 5.00",
        fin_tokens=[make_fin_token(cents=10000), make_fin_token(cents=500)],
    )
    result = score_document(gt, hyp, make_sidecar())
    assert result.metrics["spurious_amounts"] == 1
    assert result.archival_safe is False


def test_archival_safe_excludes_teds_struct_clause_when_structure_na():
    # A table-less letter with no amounts at all can still be archival-safe:
    # TEDS_struct is not part of the gate when structure_na is true, and
    # fin_em_amounts is vacuously 1.0 (not 0.0) when GT has no amount
    # tokens, so a pure label-value document isn't barred from
    # archival_safe for that reason either.
    fin_tokens = [make_fin_token(type="date", canonical="2024-01-01")]
    gt = make_doc(
        label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)],
        fin_tokens=fin_tokens,
        body_text_norm="Date 2024-01-01",
    )
    hyp = make_doc(
        label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)],
        fin_tokens=fin_tokens,
        body_text_norm="Date 2024-01-01",
    )
    result = score_document(gt, hyp, make_sidecar())
    assert result.structure_na is True
    assert result.tier == "PASS"
    assert result.metrics["fin_em_na"]["amount"] is True
    assert result.metrics["fin_em_amounts"] == 1.0
    assert result.archival_safe is True


def test_archival_safe_false_when_hyp_hallucinates_amount_gt_has_none():
    # GT has no amounts (fin_em_amounts is vacuously satisfied), but hyp
    # invents one out of nowhere: archival_safe must still fail, via
    # spurious_amounts, not via the now-vacuous amounts clause.
    gt = make_doc(
        label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)],
        fin_tokens=[make_fin_token(type="date", canonical="2024-01-01")],
        body_text_norm="Date 2024-01-01",
    )
    hyp = make_doc(
        label_value_pairs=[LabelValuePair("Date", "2024-01-01", "colon_pattern", None)],
        fin_tokens=[
            make_fin_token(type="date", canonical="2024-01-01"),
            make_fin_token(type="amount", cents=500, canonical="5.00"),  # hallucinated
        ],
        body_text_norm="Date 2024-01-01 5.00",
    )
    result = score_document(gt, hyp, make_sidecar())
    assert result.tier == "PASS"  # nothing critical fires on a pure hallucination
    assert result.metrics["fin_em_na"]["amount"] is True
    assert result.metrics["fin_em_amounts"] == 1.0
    assert result.metrics["spurious_amounts"] == 1
    assert result.archival_safe is False


# --- 74-ceiling: critical pass, structure incomplete --------------------------------------


def test_74_ceiling_when_label_value_f1_below_one():
    gt = make_doc(
        tables=[make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "100.00")])],
        body_text_norm="Total 100.00",
        label_value_pairs=[LabelValuePair("Ref", "X1", "colon_pattern", None)],
    )
    hyp = make_doc(
        tables=[make_table(0, [make_cell(0, 0, "Total"), make_cell(0, 1, "100.00")])],
        body_text_norm="Total 100.00",
        label_value_pairs=[],  # Ref pair dropped -> label_value_f1 < 1.0
    )
    result = score_document(gt, hyp, make_sidecar())
    assert result.tier == "PASS"  # no critical assertion fires
    assert result.metrics["label_value_f1"] < 1.0
    assert result.display_score <= 74.0
    assert result.display_score == pytest.approx(min(74.0, 100.0 * result.metrics["q"]))


# --- category / provisional plumbing -------------------------------------------------------


def test_category_defaults_to_sidecar_category():
    gt = _perfect_match_doc()
    hyp = _perfect_match_doc()
    result = score_document(gt, hyp, make_sidecar(category="receipt"))
    assert result.category == "receipt"


def test_category_argument_overrides_sidecar():
    gt = _perfect_match_doc()
    hyp = _perfect_match_doc()
    result = score_document(gt, hyp, make_sidecar(category="receipt"), category="bank-statement")
    assert result.category == "bank-statement"


def test_provisional_mirrors_sidecar_confirmed():
    gt = _perfect_match_doc()
    hyp = _perfect_match_doc()
    result_unconfirmed = score_document(gt, hyp, make_sidecar(confirmed=False))
    result_confirmed = score_document(gt, hyp, make_sidecar(confirmed=True))
    assert result_unconfirmed.provisional is True
    assert result_confirmed.provisional is False


# --- rollup -------------------------------------------------------------------------------


def _case_result(case_id, category, tier, display_score, q=None, archival_safe=False, status="ok"):
    from ocrgrade.ir import CaseResult

    metrics = None if tier == "CATASTROPHIC" else {"q": q if q is not None else display_score / 100.0}
    return CaseResult(
        case_id=case_id,
        status=status,
        input_form="html",
        tier=tier,
        provisional=True,
        parse_ok=tier != "CATASTROPHIC",
        truncated=False,
        table_count_gt=1,
        table_count_hyp=1,
        structure_na=False,
        reading_order_na=False,
        metrics=metrics,
        assertions=[],
        archival_safe=archival_safe,
        display_score=display_score,
        category=category,
    )


def test_rollup_counts_and_rates():
    results = [
        _case_result("c1", "invoice", "PASS", 100.0, q=1.0, archival_safe=True),
        _case_result("c2", "invoice", "REJECT", 40.0, q=0.6),
        _case_result("c3", "receipt", "CATASTROPHIC", 0.0),
    ]
    summary = rollup(results, run_meta={"run_id": "r1", "model": "m1"})
    assert summary["n_cases"] == 3
    assert summary["n_pass"] == 1
    assert summary["n_reject"] == 1
    assert summary["n_catastrophic"] == 1
    assert summary["catastrophic_rate"] == pytest.approx(1 / 3)
    assert summary["gate_pass_rate"] == pytest.approx(1 / 3)
    assert summary["archival_safe_rate"] == pytest.approx(1 / 3)
    assert summary["run_id"] == "r1"
    assert summary["model"] == "m1"
    assert summary["macro_p"] is None


def test_rollup_macro_q_excludes_catastrophic_cases():
    results = [
        _case_result("c1", "invoice", "PASS", 100.0, q=1.0),
        _case_result("c2", "invoice", "REJECT", 40.0, q=0.6),
        _case_result("c3", "invoice", "CATASTROPHIC", 0.0),
    ]
    summary = rollup(results, run_meta={})
    assert summary["macro_q"] == pytest.approx((1.0 + 0.6) / 2)


def test_rollup_worst_category_q_and_category_macro():
    results = [
        _case_result("c1", "invoice", "PASS", 90.0, q=0.9),
        _case_result("c2", "receipt", "PASS", 50.0, q=0.5),
    ]
    summary = rollup(results, run_meta={})
    assert summary["category_macro"] == {"invoice": pytest.approx(90.0), "receipt": pytest.approx(50.0)}
    assert summary["category_micro"] == summary["category_macro"]
    assert summary["worst_category_q"] == pytest.approx(0.5)
    assert summary["headline_display_score"] == pytest.approx((90.0 + 50.0) / 2)


def test_rollup_empty_results_does_not_crash():
    summary = rollup([], run_meta={})
    assert summary["n_cases"] == 0
    assert summary["catastrophic_rate"] == 0.0
    assert summary["macro_q"] is None
    assert summary["worst_category_q"] is None
    assert summary["headline_display_score"] == 0.0
    assert summary["micro_display_score"] == 0.0


def test_micro_display_score_differs_from_macro_when_category_sizes_differ():
    # invoice: 3 cases at 100.0; receipt: 1 case at 0.0. Macro (mean of
    # category means) treats both categories equally regardless of size;
    # micro (pooled over cases) implicitly weights invoice 3x more heavily
    # because it has 3x the cases -- the two numbers must therefore differ.
    results = [
        _case_result("c1", "invoice", "PASS", 100.0, q=1.0),
        _case_result("c2", "invoice", "PASS", 100.0, q=1.0),
        _case_result("c3", "invoice", "PASS", 100.0, q=1.0),
        _case_result("c4", "receipt", "REJECT", 0.0, q=0.0),
    ]
    summary = rollup(results, run_meta={})
    assert summary["headline_display_score"] == pytest.approx((100.0 + 0.0) / 2)  # macro: 50.0
    assert summary["micro_display_score"] == pytest.approx((100.0 * 3 + 0.0) / 4)  # micro: 75.0
    assert summary["headline_display_score"] != pytest.approx(summary["micro_display_score"])


def test_micro_display_score_excludes_harness_error_status():
    results = [
        _case_result("c1", "invoice", "PASS", 100.0, q=1.0, status="ok"),
        _case_result("c2", "invoice", "CATASTROPHIC", 0.0, status="error"),
    ]
    summary = rollup(results, run_meta={})
    # only c1 counts: c2's harness-level "error" status is excluded, not
    # pooled in at its display_score of 0.
    assert summary["micro_display_score"] == pytest.approx(100.0)


# --- rank_key -----------------------------------------------------------------------------


def test_rank_key_orders_higher_macro_q_first():
    better = {"catastrophic_rate": 0.0, "gate_pass_rate": 0.9, "macro_q": 0.9, "worst_category_q": 0.8, "macro_p": None}
    worse = {"catastrophic_rate": 0.0, "gate_pass_rate": 0.5, "macro_q": 0.5, "worst_category_q": 0.4, "macro_p": None}
    ranked = sorted([worse, better], key=rank_key, reverse=True)
    assert ranked[0] is better


def test_rank_key_defaults_throughput_and_peak_vram_to_zero():
    summary = {"catastrophic_rate": 0.0, "gate_pass_rate": 1.0, "macro_q": 1.0, "worst_category_q": 1.0, "macro_p": None}
    key = rank_key(summary)
    assert key[-2] == 0  # throughput default
    assert key[-1] == 0  # -peak_vram default


def test_rank_key_prefers_lower_catastrophic_rate_first():
    fewer_catastrophic = {"catastrophic_rate": 0.1, "gate_pass_rate": 0.1, "macro_q": 0.1, "worst_category_q": 0.1, "macro_p": None}
    more_catastrophic = {"catastrophic_rate": 0.9, "gate_pass_rate": 0.9, "macro_q": 0.9, "worst_category_q": 0.9, "macro_p": None}
    ranked = sorted([more_catastrophic, fewer_catastrophic], key=rank_key, reverse=True)
    assert ranked[0] is fewer_catastrophic

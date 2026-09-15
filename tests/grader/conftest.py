"""Shared test scaffolding for the ocrgrade grader test suite (Scope C).

Ensures the worktree root is importable (so `import ocrgrade` works regardless of
how pytest is invoked), and provides:

  - small factory fixtures for hand-building `ocrgrade.ir` objects (`Document`,
    `Sidecar`, `CaseResult`, ...) without needing canonicalize/scoring, which do
    not exist while this module is developed in parallel with Scopes A and B;
  - a `fake_pipeline` fixture: tiny stand-ins for the injectable callables
    `cli.py` accepts (`build_document`, `to_html`, `score_document`, `rollup`,
    `load_role_map`, `rank_key`), so `test_cli.py` can run `score`, `rank`,
    `shadow` and `fixtures-check` end to end against a temp corpus with none of
    Scope A/B's real modules present.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from ocrgrade import sidecar as sidecar_module
from ocrgrade.ir import (
    AssertionResult,
    CaseResult,
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


# --- ir factories -------------------------------------------------------------


def make_cell_ref(table_index: int = 0, row: int = 0, col: int = 0) -> CellRef:
    return CellRef(table_index=table_index, row=row, col=col)


def make_fin_token(
    *,
    type: str = "amount",
    raw: str = "5.00",
    canonical: str | None = "5.00",
    cents: int | None = 500,
    currency: str | None = "EUR",
    vat_letter: str | None = None,
    attached: bool = False,
    cell_ref: CellRef | None = None,
) -> FinToken:
    return FinToken(
        type=type,
        raw=raw,
        canonical=canonical,
        cents=cents,
        currency=currency,
        vat_letter=vat_letter,
        attached=attached,
        cell_ref=cell_ref,
    )


def make_cell(
    table_index: int = 0,
    row: int = 0,
    col: int = 0,
    *,
    rowspan: int = 1,
    colspan: int = 1,
    is_span_origin: bool = True,
    text_raw: str = "",
    text_norm: str | None = None,
    role: str = "other",
    is_numeric: bool = False,
    is_header: bool = False,
    tokens: list[FinToken] | None = None,
) -> Cell:
    return Cell(
        table_index=table_index,
        row=row,
        col=col,
        rowspan=rowspan,
        colspan=colspan,
        is_span_origin=is_span_origin,
        text_raw=text_raw,
        text_norm=text_raw if text_norm is None else text_norm,
        role=role,
        is_numeric=is_numeric,
        is_header=is_header,
        tokens=tokens or [],
    )


def make_table(
    index: int = 0,
    *,
    n_rows: int = 1,
    n_cols: int = 1,
    cells: list[Cell] | None = None,
    outer_html: str = "<table></table>",
) -> Table:
    return Table(index=index, n_rows=n_rows, n_cols=n_cols, cells=cells or [], outer_html=outer_html)


def make_label_value_pair(
    label: str = "Total",
    value: str = "5.00",
    *,
    source: str = "table_row",
    cell_ref: CellRef | None = None,
) -> LabelValuePair:
    return LabelValuePair(label=label, value=value, source=source, cell_ref=cell_ref)


def make_line_item(table_index: int = 0, row: int = 0, *, fields: dict[str, str] | None = None) -> LineItem:
    return LineItem(table_index=table_index, row=row, fields=fields or {})


def make_document(
    *,
    tables: list[Table] | None = None,
    section_headers: list[str] | None = None,
    label_value_pairs: list[LabelValuePair] | None = None,
    line_items: list[LineItem] | None = None,
    fin_tokens: list[FinToken] | None = None,
    body_text_norm: str = "",
    parse_ok: bool = True,
    parse_error: str | None = None,
    was_fragment: bool = True,
    truncated: bool = False,
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
        was_fragment=was_fragment,
        truncated=truncated,
    )


def make_critical_field(
    role: str = "grand_total",
    value: str = "5.00",
    *,
    cell_ref: CellRef | None = None,
    expected_multiplicity: int = 1,
) -> CriticalField:
    return CriticalField(role=role, value=value, cell_ref=cell_ref, expected_multiplicity=expected_multiplicity)


def make_sidecar(case_id: str = "case-1", **overrides) -> Sidecar:
    sc = sidecar_module.default_sidecar(case_id)
    for key, value in overrides.items():
        setattr(sc, key, value)
    return sc


def make_assertion(id: str = "A1", *, critical: bool = True, passed: bool = True, detail: str = "") -> AssertionResult:
    return AssertionResult(id=id, critical=critical, passed=passed, detail=detail)


def make_case_result(case_id: str = "case-1", **overrides) -> CaseResult:
    defaults = dict(
        case_id=case_id,
        status="ok",
        input_form="html",
        tier="PASS",
        provisional=False,
        parse_ok=True,
        truncated=False,
        table_count_gt=0,
        table_count_hyp=0,
        structure_na=False,
        reading_order_na=False,
        metrics={},
        assertions=[],
        archival_safe=False,
        display_score=100.0,
        runtime_seconds=None,
        errors=[],
        category="other",
    )
    defaults.update(overrides)
    return CaseResult(**defaults)


@pytest.fixture
def ir_factory():
    """Namespace of the `make_*` helpers above, for tests that prefer
    `ir_factory.document(...)` over importing each function individually.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        cell_ref=make_cell_ref,
        fin_token=make_fin_token,
        cell=make_cell,
        table=make_table,
        label_value_pair=make_label_value_pair,
        line_item=make_line_item,
        document=make_document,
        critical_field=make_critical_field,
        sidecar=make_sidecar,
        assertion=make_assertion,
        case_result=make_case_result,
    )


# --- fake pipeline for end-to-end cli.py tests --------------------------------


def _fake_load_role_map(corpus_dir):
    return {"fake_role_map_for": str(corpus_dir) if corpus_dir else None}


def _fake_build_document(html_text: str, role_map, sc) -> Document:
    return make_document(body_text_norm=html_text or "", parse_ok=True, was_fragment=True)


def _fake_to_html(raw: str, hint: str) -> str:
    return raw


def _fake_score_document(gt: Document, hyp: Document, sc: Sidecar, *, status="ok", input_form="html", runtime_seconds=None, category=None):
    category = category or sc.category
    if status != "ok":
        return make_case_result(
            case_id=sc.case_id,
            status="error",
            input_form=input_form,
            tier="CATASTROPHIC",
            provisional=not sc.confirmed,
            parse_ok=False,
            metrics={},
            assertions=[],
            archival_safe=False,
            display_score=0.0,
            runtime_seconds=runtime_seconds,
            errors=["status != ok"],
            category=category,
        )

    match = gt.body_text_norm == hyp.body_text_norm
    assertions = [make_assertion(id="FAKE1", critical=True, passed=match, detail="fake gt/hyp text equality check")]
    return make_case_result(
        case_id=sc.case_id,
        status="ok",
        input_form=input_form,
        tier="PASS" if match else "REJECT",
        provisional=not sc.confirmed,
        table_count_gt=len(gt.tables),
        table_count_hyp=len(hyp.tables),
        metrics={"q": 1.0 if match else 0.3},
        assertions=assertions,
        archival_safe=match,
        display_score=100.0 if match else 40.0,
        runtime_seconds=runtime_seconds,
        category=category,
    )


def _fake_rollup(results: list[CaseResult], *, run_meta: dict) -> dict:
    n = len(results)
    by_category: dict[str, list[float]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r.display_score)
    category_macro = {cat: sum(vals) / len(vals) for cat, vals in by_category.items()}

    def _rate(pred) -> float:
        return (sum(1 for r in results if pred(r)) / n) if n else 0.0

    return {
        "run_id": run_meta.get("run_id"),
        "model": run_meta.get("model"),
        "quant": run_meta.get("quant"),
        "prompt": run_meta.get("prompt"),
        "n_cases": n,
        "n_ok": sum(1 for r in results if r.status == "ok"),
        "n_catastrophic": sum(1 for r in results if r.tier == "CATASTROPHIC"),
        "n_reject": sum(1 for r in results if r.tier == "REJECT"),
        "n_pass": sum(1 for r in results if r.tier == "PASS"),
        "catastrophic_rate": _rate(lambda r: r.tier == "CATASTROPHIC"),
        "gate_pass_rate": _rate(lambda r: r.tier == "PASS"),
        "archival_safe_rate": _rate(lambda r: r.archival_safe),
        "macro_q": (sum(r.display_score for r in results) / n / 100.0) if n else 0.0,
        "worst_category_q": (min(category_macro.values()) / 100.0) if category_macro else 0.0,
        "macro_p": None,
        "category_macro": category_macro,
        "category_micro": {},
        "headline_display_score": (sum(category_macro.values()) / len(category_macro)) if category_macro else 0.0,
        "config_hash": {"roles_yaml": "fake", "fixtures": "fake"},
    }


def _fake_rank_key(summary: dict) -> tuple:
    return (
        1 - (summary.get("catastrophic_rate") or 0.0),
        summary.get("gate_pass_rate") or 0.0,
        summary.get("macro_q") or 0.0,
    )


@pytest.fixture
def fake_pipeline() -> dict:
    """`cli.main(argv, **fake_pipeline)` runs any subcommand against tiny,
    deterministic stand-ins instead of the real canonicalize/markdown/scoring/
    roles modules.
    """
    return {
        "build_document": _fake_build_document,
        "to_html": _fake_to_html,
        "score_document": _fake_score_document,
        "rollup": _fake_rollup,
        "load_role_map": _fake_load_role_map,
        "rank_key": _fake_rank_key,
    }

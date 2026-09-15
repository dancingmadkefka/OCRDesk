"""Tier logic, Q, DisplayScore, ARCHIVAL_SAFE, ranking key, and rollup.

See docs/grader-plan.md section 5 (formulas) and section 7 (result schemas).
"""
from __future__ import annotations

import statistics

from ocrgrade.assertions import run_assertions
from ocrgrade.ir import CaseResult, Document, Sidecar, Tier
from ocrgrade.metrics_content import content_metrics
from ocrgrade.metrics_structure import structure_metrics


def score_document(
    gt: Document,
    hyp: Document,
    sidecar: Sidecar,
    *,
    status: str = "ok",
    input_form: str = "html",
    runtime_seconds: float | None = None,
    category: str | None = None,
) -> CaseResult:
    case_category = category if category is not None else sidecar.category
    table_count_gt = len(gt.tables)
    table_count_hyp = len(hyp.tables)

    # Tier 0: status error, parse failure, or truncation -> CATASTROPHIC,
    # metrics None, display_score 0. GT is assumed well-formed; these gates
    # are about the hypothesis only.
    tier0_errors: list[str] = []
    if status != "ok":
        tier0_errors.append(f"harness status={status}")
    if not hyp.parse_ok:
        tier0_errors.append(f"parse failure: {hyp.parse_error or 'unknown'}")
    if hyp.truncated:
        tier0_errors.append("truncated output")

    if tier0_errors:
        return CaseResult(
            case_id=sidecar.case_id,
            status=status,
            input_form=input_form,
            tier="CATASTROPHIC",
            provisional=not sidecar.confirmed,
            parse_ok=hyp.parse_ok,
            truncated=hyp.truncated,
            table_count_gt=table_count_gt,
            table_count_hyp=table_count_hyp,
            structure_na=False,
            reading_order_na=False,
            metrics=None,
            assertions=[],
            archival_safe=False,
            display_score=0.0,
            runtime_seconds=runtime_seconds,
            errors=tier0_errors,
            category=case_category,
        )

    content = content_metrics(gt, hyp)
    structure = structure_metrics(gt, hyp)
    assertion_results = run_assertions(gt, hyp, sidecar)

    critical_failed = [a for a in assertion_results if a.critical and not a.passed]
    tier: Tier = "REJECT" if critical_failed else "PASS"

    content_score = content["content_score"]
    reading_order_score = content["reading_order_score"]
    structure_score = structure["structure_score"]

    if structure_score is not None:
        q = 0.45 * content_score + 0.45 * structure_score + 0.10 * reading_order_score
    else:
        # No tables in GT AND label_value_F1 AND heading_sequence_score both
        # n/a: drop the Structure term from Q entirely (plan section 5).
        q = 0.9 * content_score + 0.10 * reading_order_score

    metrics = {**content, **structure, "q": q}

    label_value_f1 = structure["label_value_f1"]
    line_item_f1 = structure["line_item_f1"]
    reduced_structure = (label_value_f1 is not None and label_value_f1 < 1.0) or (
        line_item_f1 is not None and line_item_f1 < 1.0
    )

    if tier == "REJECT":
        display_score = min(59.0, 100.0 * q)
    elif reduced_structure:
        display_score = min(74.0, 100.0 * q)
    else:
        display_score = 100.0 * q

    teds_struct = structure["teds_struct"]
    teds_struct_ok = structure["structure_na"] or (teds_struct is not None and teds_struct >= 0.95)
    archival_safe = (
        tier == "PASS"
        and content["fin_em_amounts"] == 1.0
        and content["cer_financial_tokens"] == 0.0
        and content["spurious_amounts"] == 0
        and teds_struct_ok
    )

    return CaseResult(
        case_id=sidecar.case_id,
        status=status,
        input_form=input_form,
        tier=tier,
        provisional=not sidecar.confirmed,
        parse_ok=hyp.parse_ok,
        truncated=hyp.truncated,
        table_count_gt=table_count_gt,
        table_count_hyp=table_count_hyp,
        structure_na=structure["structure_na"],
        reading_order_na=content["reading_order_na"],
        metrics=metrics,
        assertions=assertion_results,
        archival_safe=archival_safe,
        display_score=display_score,
        runtime_seconds=runtime_seconds,
        errors=[],
        category=case_category,
    )


def rank_key(summary: dict) -> tuple:
    """Ranking key per docs/grader-plan.md section 5:

    `(1 - catastrophic_rate, gate_pass_rate, macro_Q, worst_category_Q,
    macro_P, throughput, -peak_VRAM)`. `throughput`/`peak_vram` are optional
    run_meta extensions absent from the core summary.json schema (section 7)
    and default to 0. `macro_p` is always None (fixed per plan); every
    summary carries the same None there so tuple comparisons never need to
    order two different values at that position.
    """
    return (
        1 - summary.get("catastrophic_rate", 0.0),
        summary.get("gate_pass_rate", 0.0),
        summary.get("macro_q") or 0.0,
        summary.get("worst_category_q") or 0.0,
        summary.get("macro_p"),
        summary.get("throughput") or 0,
        -(summary.get("peak_vram") or 0),
    )


def rollup(results: list[CaseResult], *, run_meta: dict) -> dict:
    """Produce the summary.json fields from docs/grader-plan.md section 7."""
    n_cases = len(results)
    n_ok = sum(1 for c in results if c.status == "ok")
    n_catastrophic = sum(1 for c in results if c.tier == "CATASTROPHIC")
    n_reject = sum(1 for c in results if c.tier == "REJECT")
    n_pass = sum(1 for c in results if c.tier == "PASS")

    catastrophic_rate = n_catastrophic / max(1, n_cases)
    gate_pass_rate = n_pass / max(1, n_cases)
    archival_safe_rate = sum(1 for c in results if c.archival_safe) / max(1, n_cases)

    def _q(c: CaseResult) -> float:
        assert c.metrics is not None
        return c.metrics["q"]

    scored = [c for c in results if c.tier in ("PASS", "REJECT") and c.metrics is not None]
    macro_q = statistics.mean(_q(c) for c in scored) if scored else None

    by_category: dict[str, list[CaseResult]] = {}
    for c in results:
        by_category.setdefault(c.category, []).append(c)

    category_q_means: dict[str, float] = {}
    for cat, cases in by_category.items():
        cat_scored = [c for c in cases if c.tier in ("PASS", "REJECT") and c.metrics is not None]
        if cat_scored:
            category_q_means[cat] = statistics.mean(_q(c) for c in cat_scored)
    worst_category_q = min(category_q_means.values()) if category_q_means else None

    # CategoryMacro_c = mean(DisplayScore) per category, over every case in
    # the category (a catastrophic case's display_score of 0 counts).
    category_macro = {
        cat: statistics.mean(c.display_score for c in cases) for cat, cases in by_category.items()
    }
    # No finer sub-grouping exists to make a micro-average differ from the
    # macro-average at this same per-category granularity, so the two are
    # numerically identical here; they are kept as separate keys since a
    # multi-run leaderboard aggregator (report.py / `rank`) may combine them
    # differently. The macro/micro distinction instead shows up at the
    # headline level below: headline_display_score (macro, unweighted mean
    # of category means) vs micro_display_score (pooled over cases, so a
    # larger category implicitly counts for more).
    category_micro = dict(category_macro)

    headline_display_score = (
        statistics.mean(category_macro.values()) if category_macro else 0.0
    )

    # Case-count-weighted mean of display_score, pooled directly over cases
    # rather than averaged per category first -- mathematically equivalent
    # to weighting each category's macro mean by its own case count.
    # "non-error" excludes harness-level status=="error" (infrastructure
    # failure, not a model-quality signal); a CATASTROPHIC case that ran
    # (status=="ok") but parsed/truncated badly still counts, at its
    # display_score of 0.
    non_error_cases = [c for c in results if c.status != "error"]
    micro_display_score = (
        sum(c.display_score for c in non_error_cases) / len(non_error_cases)
        if non_error_cases
        else 0.0
    )

    return {
        "run_id": run_meta.get("run_id"),
        "model": run_meta.get("model"),
        "quant": run_meta.get("quant"),
        "prompt": run_meta.get("prompt"),
        "n_cases": n_cases,
        "n_ok": n_ok,
        "n_catastrophic": n_catastrophic,
        "n_reject": n_reject,
        "n_pass": n_pass,
        "catastrophic_rate": catastrophic_rate,
        "gate_pass_rate": gate_pass_rate,
        "archival_safe_rate": archival_safe_rate,
        "macro_q": macro_q,
        "worst_category_q": worst_category_q,
        "macro_p": None,
        "category_macro": category_macro,
        "category_micro": category_micro,
        "headline_display_score": headline_display_score,
        "micro_display_score": micro_display_score,
        "config_hash": run_meta.get("config_hash", {}),
    }

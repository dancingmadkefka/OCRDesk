"""Table-isolated TEDS scoring.

`table_recognition_metric.TEDS.__call__` looks for `body/table` as a direct
child of the parsed root and silently returns 0.0 otherwise (verified against
the installed source in `.venv/Lib/site-packages/table_recognition_metric/`).
Every real GT case nests `<table>` inside wrapper `<div>`s, so this module
never hands a full document to `TEDS()`: it pairs GT/hyp tables by
document-order index, rewraps each matched table's `outer_html` in a minimal
`<html><body>...</body></html>` shell on both sides, and scores the pair in
isolation. See docs/grader-plan.md sections 4-5 and risk 1 in section 10.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from table_recognition_metric import TEDS

from ocrgrade.ir import Table

# Risk 7 (section 10): APTED is expensive on large tables. These two guards
# are independent: the cell cap avoids even starting a call we know will be
# slow; the wall-clock guard catches anything that is slow for other reasons
# (pathological nesting, etc). Either one scores the pair 0.0 and marks
# `timed_out`.
MAX_CELLS_PER_TABLE = 2000
TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class TedsResult:
    teds: float | None
    teds_struct: float | None
    per_table: list[float] = field(default_factory=list)
    n_pairs: int = 0
    timed_out: bool = False


def _wrap(outer_html: str) -> str:
    return f"<html><body>{outer_html}</body></html>"


def _call_with_timeout(
    scorer: TEDS, pred_html: str, gt_html: str, timeout: float
) -> tuple[float, bool]:
    """Run `scorer(pred_html, gt_html)` on a worker thread with a wall-clock cap.

    Returns `(score, timed_out)`. There is no way to kill a Python thread from
    the outside, so on timeout the worker is simply abandoned (it is a daemon
    thread, so it cannot block process exit) and the pair scores 0.0.
    """
    outcome: list[float] = []

    def _run() -> None:
        try:
            outcome.append(scorer(pred_html, gt_html))
        except Exception:
            outcome.append(0.0)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return 0.0, True
    return (outcome[0] if outcome else 0.0), False


def teds_scores(gt_tables: list[Table], hyp_tables: list[Table]) -> TedsResult:
    """Pair GT/hyp tables by document-order index and score each pair with TEDS.

    Pairing covers `min(len(gt_tables), len(hyp_tables))` tables; any count
    mismatch beyond that is a structural finding for the non-critical A7
    assertion (`assertions.py`), not something this function penalizes twice.

    Returns `teds=None, teds_struct=None` only when the GT document has no
    tables at all (metric not applicable). A GT with tables and a hypothesis
    with none is a real (zero) score, not an N/A.
    """
    if not gt_tables:
        return TedsResult(teds=None, teds_struct=None, per_table=[], n_pairs=0)

    n_pairs = min(len(gt_tables), len(hyp_tables))
    if n_pairs == 0:
        return TedsResult(teds=0.0, teds_struct=0.0, per_table=[], n_pairs=0)

    per_table: list[float] = []
    weighted_full = 0.0
    weighted_struct = 0.0
    weight_total = 0.0
    any_timed_out = False

    for i in range(n_pairs):
        gt_table = gt_tables[i]
        hyp_table = hyp_tables[i]
        weight = float(max(1, len(gt_table.cells)))

        if len(gt_table.cells) > MAX_CELLS_PER_TABLE or len(hyp_table.cells) > MAX_CELLS_PER_TABLE:
            per_table.append(0.0)
            weight_total += weight
            any_timed_out = True
            continue

        gt_html = _wrap(gt_table.outer_html)
        hyp_html = _wrap(hyp_table.outer_html)

        full_score, full_timed_out = _call_with_timeout(
            TEDS(structure_only=False), hyp_html, gt_html, TIMEOUT_SECONDS
        )
        struct_score, struct_timed_out = _call_with_timeout(
            TEDS(structure_only=True), hyp_html, gt_html, TIMEOUT_SECONDS
        )

        if full_timed_out or struct_timed_out:
            any_timed_out = True
            full_score = 0.0
            struct_score = 0.0

        per_table.append(full_score)
        weighted_full += full_score * weight
        weighted_struct += struct_score * weight
        weight_total += weight

    teds = weighted_full / weight_total if weight_total > 0 else 0.0
    teds_struct = weighted_struct / weight_total if weight_total > 0 else 0.0

    return TedsResult(
        teds=teds,
        teds_struct=teds_struct,
        per_table=per_table,
        n_pairs=n_pairs,
        timed_out=any_timed_out,
    )

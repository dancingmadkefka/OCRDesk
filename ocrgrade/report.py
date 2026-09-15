"""Result writers: `cases/<id>.json`, `summary.json`, `leaderboard.csv`, `report.html`.

See docs/grader-plan.md section 7. Stdlib templating only, no Jinja - `report.html`
shows the leaderboard row and per-case rows; it never re-renders GT/hyp documents.
"""
from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ocrgrade.ir import CaseResult

LEADERBOARD_FIXED_COLUMNS = [
    "model",
    "quant",
    "prompt",
    "n",
    "catastrophic_rate",
    "gate_pass_rate",
    "archival_safe_rate",
    "macro_Q",
    "worst_category_Q",
]


def write_case_json(case_result: CaseResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(case_result), indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_summary_json(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def config_id(summary: dict[str, Any]) -> str:
    """A deterministic identifier of the grader configuration a run was scored under."""
    config_hash = summary.get("config_hash") or {}
    return ";".join(f"{k}={config_hash[k]}" for k in sorted(config_hash)) or "unknown"


def write_leaderboard_csv(summaries: list[dict[str, Any]], path: Path) -> None:
    """One row per summary.json. Category columns are the union across all
    summaries, sorted alphabetically, so `rank` can merge runs that cover
    different category sets without shuffling column order between runs.
    """
    categories: set[str] = set()
    for summary in summaries:
        categories.update((summary.get("category_macro") or {}).keys())
    category_columns = sorted(categories)

    columns = LEADERBOARD_FIXED_COLUMNS + [f"{c}_display_score" for c in category_columns] + ["config"]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for summary in summaries:
            category_macro = summary.get("category_macro") or {}
            row = [
                summary.get("model"),
                summary.get("quant"),
                summary.get("prompt"),
                summary.get("n_cases"),
                summary.get("catastrophic_rate"),
                summary.get("gate_pass_rate"),
                summary.get("archival_safe_rate"),
                summary.get("macro_q"),
                summary.get("worst_category_q"),
            ]
            row.extend(category_macro.get(c, "") for c in category_columns)
            row.append(config_id(summary))
            writer.writerow(row)


def write_report_html(summary: dict[str, Any], cases: list[CaseResult], path: Path) -> None:
    """A single run's leaderboard entry plus one row per case. No document
    rendering: this is a grading report, not a compare viewer.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_report_html(summary, cases), encoding="utf-8")


def _render_report_html(summary: dict[str, Any], cases: list[CaseResult]) -> str:
    esc = html.escape

    category_macro = summary.get("category_macro") or {}
    category_cells = "".join(
        f"<td>{esc(category)}</td><td>{_fmt(score)}</td>" for category, score in sorted(category_macro.items())
    )

    case_rows = []
    for case in sorted(cases, key=lambda c: c.case_id):
        failing = [a.id for a in case.assertions if not a.passed]
        case_rows.append(
            "<tr>"
            f"<td>{esc(case.case_id)}</td>"
            f"<td>{esc(case.category)}</td>"
            f"<td class='tier-{esc(case.tier.lower())}'>{esc(case.tier)}</td>"
            f"<td>{_fmt(case.display_score)}</td>"
            f"<td>{'yes' if case.archival_safe else 'no'}</td>"
            f"<td>{esc(', '.join(failing)) if failing else '-'}</td>"
            f"<td>{'yes' if case.provisional else 'no'}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>ocrgrade report: {esc(str(summary.get('model') or summary.get('run_id') or ''))}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1a1a1a; }}
table {{ border-collapse: collapse; margin-bottom: 32px; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 13px; }}
th {{ background: #f0f0f0; }}
.tier-pass {{ color: #146c2e; font-weight: 600; }}
.tier-reject {{ color: #a15c00; font-weight: 600; }}
.tier-catastrophic {{ color: #a30000; font-weight: 600; }}
h1, h2 {{ font-size: 16px; }}
</style>
<h1>ocrgrade report</h1>
<h2>Leaderboard</h2>
<table>
<tr><th>model</th><th>quant</th><th>prompt</th><th>n</th><th>catastrophic_rate</th>
<th>gate_pass_rate</th><th>archival_safe_rate</th><th>macro_Q</th><th>worst_category_Q</th>
{"".join(f"<th>{esc(c)}</th>" for c in sorted(category_macro))}
</tr>
<tr>
<td>{esc(str(summary.get('model') or ''))}</td>
<td>{esc(str(summary.get('quant') or ''))}</td>
<td>{esc(str(summary.get('prompt') or ''))}</td>
<td>{_fmt(summary.get('n_cases'))}</td>
<td>{_fmt(summary.get('catastrophic_rate'))}</td>
<td>{_fmt(summary.get('gate_pass_rate'))}</td>
<td>{_fmt(summary.get('archival_safe_rate'))}</td>
<td>{_fmt(summary.get('macro_q'))}</td>
<td>{_fmt(summary.get('worst_category_q'))}</td>
{"".join(f"<td>{_fmt(category_macro.get(c))}</td>" for c in sorted(category_macro))}
</tr>
</table>
<h2>Cases</h2>
<table>
<tr><th>case_id</th><th>category</th><th>tier</th><th>display_score</th>
<th>archival_safe</th><th>failing assertions</th><th>provisional</th></tr>
{"".join(case_rows)}
</table>
"""


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return html.escape(str(value))

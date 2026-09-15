"""`python -m ocrgrade <command> [args]` - argparse subcommands wiring A + B + C.

See docs/grader-plan.md section 6. Scope A (canonicalize/roles/markdown) and Scope
B (scoring) do not exist while this module is developed in parallel with them, so
every command accepts its real dependencies as injectable keyword parameters and
otherwise resolves them via a lazy import at call time - each command resolves only
the dependencies it actually needs, so e.g. `rank` never touches canonicalize/
scoring.score_document, and tests can run every subcommand end to end against tiny
stand-ins (see tests/grader/conftest.py's `fake_pipeline` fixture).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from ocrgrade import inputs, report, sidecar
from ocrgrade.ir import CaseResult

VALID_TIERS = {"CATASTROPHIC", "REJECT", "PASS"}


# --- dependency resolution ----------------------------------------------------
# Each resolver does the lazy `from ocrgrade import <module>` described above.
# Kept as separate one-liners (rather than resolved once in `main`) so a command
# that does not need a given module never imports it.


def _resolve_build_document(build_document):
    if build_document is not None:
        return build_document
    from ocrgrade import canonicalize

    return canonicalize.build_document


def _resolve_to_html(to_html):
    if to_html is not None:
        return to_html
    from ocrgrade import markdown

    return markdown.to_html


def _resolve_score_document(score_document):
    if score_document is not None:
        return score_document
    from ocrgrade import scoring

    return scoring.score_document


def _resolve_rollup(rollup):
    if rollup is not None:
        return rollup
    from ocrgrade import scoring

    return scoring.rollup


def _resolve_load_role_map(load_role_map):
    if load_role_map is not None:
        return load_role_map
    from ocrgrade import roles

    return roles.load_role_map


def _resolve_rank_key(rank_key):
    if rank_key is not None:
        return rank_key
    from ocrgrade import scoring

    return scoring.rank_key


# --- argument parsing ---------------------------------------------------------


class _ArgumentParser(argparse.ArgumentParser):
    """argparse's own default exits usage errors with status 2. The grader's exit
    codes reserve 2 for domain errors (corpus not found, bad input file, ...), so
    usage errors are remapped to 1 here.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="ocrgrade")
    sub = parser.add_subparsers(dest="command", required=True)

    p_annotate = sub.add_parser("annotate", help="Derive sidecars for a corpus and write the human review CSV.")
    p_annotate.add_argument("--corpus", required=True, type=Path)
    p_annotate.add_argument("--manifest", type=Path, default=None, help="Optional ocr_manifest.json.")

    p_confirm = sub.add_parser(
        "confirm",
        help="Apply the edited annotations_review.csv back to the sidecars (category, locale, currency, "
             "decimal separator, VAT-letter scheme, confirmed, notes). Critical fields and sections stay as derived.",
    )
    p_confirm.add_argument("--corpus", required=True, type=Path)
    p_confirm.add_argument("--csv", type=Path, default=None, help="Default: <corpus>/annotations_review.csv.")

    p_score = sub.add_parser("score", help="Score one hypothesis source against a corpus.")
    p_score.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="AIFA GT Set-layout corpus root. Required unless --ocrdesk-images-dir is given.",
    )
    src = p_score.add_mutually_exclusive_group(required=True)
    src.add_argument("--aifa-results", type=Path, help="AIFA html_vlm_*.json results file.")
    src.add_argument("--hyp-dir", type=Path, help="Directory of <case-id>.html/.md/.txt hypothesis files.")
    src.add_argument("--ocrdesk-images-dir", type=Path, help="OCRDesk images directory (doubles as the corpus).")
    p_score.add_argument("--model", default=None, help="OCRDesk model name; required with --ocrdesk-images-dir.")
    p_score.add_argument("--out-dir", required=True, type=Path)
    p_score.add_argument("--run-id", default=None, help="Default: YYYYmmdd-HHMMSS_<model-safe>.")

    p_rank = sub.add_parser("rank", help="Merge several summary.json files into one ranked leaderboard.csv.")
    p_rank.add_argument("--summary", action="append", required=True, type=Path)
    p_rank.add_argument("--out", required=True, type=Path)

    p_shadow = sub.add_parser(
        "shadow", help="Like score, plus a gitignored shadow_expected.json (local, real-corpus use only)."
    )
    p_shadow.add_argument("--corpus", required=True, type=Path)
    p_shadow.add_argument("--hyp-dir", required=True, type=Path)
    p_shadow.add_argument("--out-dir", required=True, type=Path)
    p_shadow.add_argument("--run-id", default=None)

    p_fixtures = sub.add_parser(
        "fixtures-check", help="Score every synthetic fixture and check it against its expected.json (CI gate)."
    )
    p_fixtures.add_argument("--fixtures-dir", type=Path, default=Path("tests/grader/fixtures"))

    return parser


def main(
    argv: list[str] | None = None,
    *,
    build_document=None,
    to_html=None,
    score_document=None,
    rollup=None,
    load_role_map=None,
    rank_key=None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "annotate":
        return _cmd_annotate(args, build_document=build_document, load_role_map=load_role_map)
    if args.command == "confirm":
        return _cmd_confirm(args)
    if args.command == "score":
        return _cmd_score(
            args,
            build_document=build_document,
            to_html=to_html,
            score_document=score_document,
            rollup=rollup,
            load_role_map=load_role_map,
        )
    if args.command == "rank":
        return _cmd_rank(args, rank_key=rank_key)
    if args.command == "shadow":
        return _cmd_shadow(
            args,
            build_document=build_document,
            to_html=to_html,
            score_document=score_document,
            rollup=rollup,
            load_role_map=load_role_map,
        )
    if args.command == "fixtures-check":
        return _cmd_fixtures_check(
            args, build_document=build_document, to_html=to_html, score_document=score_document, load_role_map=load_role_map
        )

    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - unreachable, subparsers are required


# --- annotate ------------------------------------------------------------------


def _load_manifest(path: Path | None) -> dict[str, dict]:
    """`ocr_manifest.json` in any of three shapes: `{case_id: {...}}`, `[{"case_id"|"id": ...}]`,
    or AIFA's `{"cases": [{"id": ..., "document_type": ...}]}`. Returns {case_id: entry}."""
    if path is None:
        return {}
    if not path.is_file():
        print(f"warning: manifest file not found, continuing without it: {path}", file=sys.stderr)
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        data = data["cases"]
    if isinstance(data, list):
        out: dict[str, dict] = {}
        for entry in data:
            if isinstance(entry, dict):
                key = entry.get("case_id") or entry.get("id")
                if key:
                    out[str(key)] = entry
        return out
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


def _cmd_annotate(args, *, build_document=None, load_role_map=None) -> int:
    corpus_dir: Path = args.corpus
    if not corpus_dir.is_dir():
        print(f"error: corpus directory not found: {corpus_dir}", file=sys.stderr)
        return 2

    build_document = _resolve_build_document(build_document)
    load_role_map = _resolve_load_role_map(load_role_map)

    case_files_list = inputs.discover_corpus(corpus_dir)
    if not case_files_list:
        print(f"error: no cases discovered under {corpus_dir}", file=sys.stderr)
        return 2

    manifest = _load_manifest(args.manifest)
    role_map = load_role_map(corpus_dir)

    all_sidecars = []
    for case_files in case_files_list:
        existing = sidecar.load(case_files.sidecar_path) if case_files.sidecar_path.is_file() else None
        if existing is not None and existing.confirmed:
            # A human already reviewed this one; annotate must not clobber it.
            all_sidecars.append(existing)
            continue
        gt_html = case_files.gt_path.read_text(encoding="utf-8", errors="replace")
        manifest_entry = manifest.get(case_files.case_id, {})
        derived = sidecar.derive(
            case_files.case_id, gt_html, manifest_entry, build_document=build_document, role_map=role_map
        )
        sidecar.save(derived, case_files.sidecar_path)
        all_sidecars.append(derived)

    documents = {cf.case_id: cf.gt_path.stem for cf in case_files_list}
    sidecar.write_review_csv(all_sidecars, corpus_dir / "annotations_review.csv", documents)
    return 0


def _cmd_confirm(args) -> int:
    """Copy the reviewer's edits from annotations_review.csv into the sidecars, then rewrite the
    CSV from the sidecars so both stay in step. A single invalid value stops the whole run
    before anything is written."""
    corpus_dir: Path = args.corpus
    if not corpus_dir.is_dir():
        print(f"error: corpus directory not found: {corpus_dir}", file=sys.stderr)
        return 2
    csv_path: Path = args.csv or corpus_dir / "annotations_review.csv"
    if not csv_path.is_file():
        print(f"error: review CSV not found: {csv_path}", file=sys.stderr)
        return 2
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = {r.get("case_id", "").strip(): r for r in csv.DictReader(fh)}

    case_files_list = inputs.discover_corpus(corpus_dir)
    loaded: list[tuple[Any, Any, list[str]]] = []
    problems: list[str] = []
    for case_files in case_files_list:
        if not case_files.sidecar_path.is_file():
            problems.append(f"{case_files.case_id}: no sidecar yet, run annotate first")
            continue
        sc = sidecar.load(case_files.sidecar_path)
        row = rows.get(case_files.case_id)
        changed: list[str] = []
        if row is not None:
            try:
                changed = sidecar.apply_review_row(sc, row)
            except ValueError as exc:
                problems.append(f"{case_files.case_id}: {exc}")
                continue
        loaded.append((case_files, sc, changed))
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print("nothing written", file=sys.stderr)
        return 1

    n_changed = 0
    for case_files, sc, changed in loaded:
        if changed:
            sidecar.save(sc, case_files.sidecar_path)
            n_changed += 1
    documents = {cf.case_id: cf.gt_path.stem for cf in case_files_list}
    sidecar.write_review_csv([sc for _, sc, _ in loaded], csv_path, documents)
    n_confirmed = sum(1 for _, sc, _ in loaded if sc.confirmed)
    unknown = sorted(set(rows) - {cf.case_id for cf in case_files_list} - {""})
    print(f"applied {csv_path.name} to {len(loaded)} case(s): {n_changed} changed, {n_confirmed} confirmed")
    if unknown:
        print(f"warning: rows without a case in the corpus were ignored: {', '.join(unknown)}", file=sys.stderr)
    return 0


# --- score / shadow (shared pipeline) ------------------------------------------


@dataclass
class _ScoreSource:
    aifa_results: Path | None = None
    hyp_dir: Path | None = None
    ocrdesk_images_dir: Path | None = None
    model: str | None = None


def _score_one_case(case_files, hyp_record, *, build_document, to_html, score_document, role_map) -> CaseResult:
    gt_html = case_files.gt_path.read_text(encoding="utf-8", errors="replace")
    sc = sidecar.load_or_default(case_files.case_id, case_files)
    gt_doc = build_document(gt_html, role_map, sc)

    if hyp_record is None:
        hyp_record = inputs.HypothesisRecord(
            case_id=case_files.case_id,
            raw="",
            hint="html",
            status="error",
            error="no hypothesis provided for this case",
        )

    if hyp_record.status == "ok":
        hyp_html = to_html(hyp_record.raw, hyp_record.hint)
        hyp_doc = build_document(hyp_html, role_map, sc)
        status = "ok"
    else:
        # Section 5's "empty hypothesis / harness status == error" edge case:
        # score_document still needs a Document, so hand it an empty one and let
        # it decide the CATASTROPHIC tier from `status`.
        hyp_doc = build_document("", role_map, sc)
        status = "error"

    return score_document(
        gt_doc,
        hyp_doc,
        sc,
        status=status,
        input_form=hyp_record.hint,
        runtime_seconds=hyp_record.runtime_seconds,
        category=sc.category,
    )


def _run_score_like(
    *,
    corpus: Path | None,
    source: _ScoreSource,
    out_dir: Path,
    run_id: str | None,
    build_document,
    to_html,
    score_document,
    rollup,
    load_role_map,
) -> tuple[int, Path | None, list[CaseResult], dict | None]:
    ocrdesk = source.ocrdesk_images_dir is not None

    if ocrdesk:
        if not source.model:
            print("error: --model is required with --ocrdesk-images-dir", file=sys.stderr)
            return 1, None, [], None
        images_dir = source.ocrdesk_images_dir
        if not images_dir.is_dir():
            print(f"error: OCRDesk images directory not found: {images_dir}", file=sys.stderr)
            return 2, None, [], None
        case_files_list = inputs.discover_ocrdesk_corpus(images_dir)
        role_map_source = images_dir
    else:
        if corpus is None:
            print("error: --corpus is required unless --ocrdesk-images-dir is given", file=sys.stderr)
            return 1, None, [], None
        if not corpus.is_dir():
            print(f"error: corpus directory not found: {corpus}", file=sys.stderr)
            return 2, None, [], None
        case_files_list = inputs.discover_corpus(corpus)
        role_map_source = corpus

    if not case_files_list:
        print("error: no cases discovered", file=sys.stderr)
        return 2, None, [], None

    case_ids = [cf.case_id for cf in case_files_list]

    try:
        if source.aifa_results is not None:
            if not source.aifa_results.is_file():
                print(f"error: AIFA results file not found: {source.aifa_results}", file=sys.stderr)
                return 2, None, [], None
            hyp_records, run_meta = inputs.load_hypotheses(aifa_results=source.aifa_results, case_ids=case_ids)
        elif source.hyp_dir is not None:
            if not source.hyp_dir.is_dir():
                print(f"error: hypothesis directory not found: {source.hyp_dir}", file=sys.stderr)
                return 2, None, [], None
            hyp_records, run_meta = inputs.load_hypotheses(hyp_dir=source.hyp_dir, case_ids=case_ids)
        else:
            hyp_records, run_meta = inputs.load_hypotheses(
                ocrdesk_images_dir=source.ocrdesk_images_dir, ocrdesk_model=source.model, case_ids=case_ids
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2, None, [], None

    if ocrdesk and source.model:
        run_meta["model"] = source.model

    role_map = load_role_map(role_map_source)

    resolved_run_id = run_id or _default_run_id(run_meta.get("model"))
    run_out_dir = out_dir / resolved_run_id
    cases_out_dir = run_out_dir / "cases"
    cases_out_dir.mkdir(parents=True, exist_ok=True)

    hyp_by_case = {r.case_id: r for r in hyp_records}
    results: list[CaseResult] = []
    had_exception = False

    for case_files in case_files_list:
        hyp_record = hyp_by_case.get(case_files.case_id)
        try:
            result = _score_one_case(
                case_files,
                hyp_record,
                build_document=build_document,
                to_html=to_html,
                score_document=score_document,
                role_map=role_map,
            )
        except Exception as exc:  # noqa: BLE001 - one bad case must not abort the run
            had_exception = True
            print(f"error: case {case_files.case_id} raised {exc!r}", file=sys.stderr)
            continue
        results.append(result)
        report.write_case_json(result, cases_out_dir / f"{result.case_id}.json")

    current_fp = sidecar.annotator_fingerprint()
    stale = []
    for cf in case_files_list:
        if getattr(cf, "sidecar_path", None) is None or not cf.sidecar_path.is_file():
            continue
        sc = sidecar.load(cf.sidecar_path)
        if not sc.confirmed and sc.annotator_fingerprint != current_fp:  # confirmed = human-checked, never stale
            stale.append(cf.case_id)
    if stale:
        shown = ", ".join(stale[:5]) + (", ..." if len(stale) > 5 else "")
        print(
            f"warning: {len(stale)} unconfirmed sidecar(s) were annotated by a different grader build ({shown}); "
            "derived fields may be stale - re-run `ocrgrade annotate` (confirmed sidecars are kept)",
            file=sys.stderr,
        )
    run_meta_full = dict(run_meta)
    run_meta_full["run_id"] = resolved_run_id
    run_meta_full["stale_sidecars"] = len(stale)
    summary = rollup(results, run_meta=run_meta_full)
    report.write_summary_json(summary, run_out_dir / "summary.json")
    report.write_leaderboard_csv([summary], run_out_dir / "leaderboard.csv")
    report.write_report_html(summary, results, run_out_dir / "report.html")

    return (3 if had_exception else 0), run_out_dir, results, summary


def _cmd_score(args, *, build_document=None, to_html=None, score_document=None, rollup=None, load_role_map=None) -> int:
    source = _ScoreSource(
        aifa_results=args.aifa_results,
        hyp_dir=args.hyp_dir,
        ocrdesk_images_dir=args.ocrdesk_images_dir,
        model=args.model,
    )
    exit_code, _run_out_dir, _results, _summary = _run_score_like(
        corpus=args.corpus,
        source=source,
        out_dir=args.out_dir,
        run_id=args.run_id,
        build_document=_resolve_build_document(build_document),
        to_html=_resolve_to_html(to_html),
        score_document=_resolve_score_document(score_document),
        rollup=_resolve_rollup(rollup),
        load_role_map=_resolve_load_role_map(load_role_map),
    )
    return exit_code


def _cmd_shadow(args, *, build_document=None, to_html=None, score_document=None, rollup=None, load_role_map=None) -> int:
    source = _ScoreSource(hyp_dir=args.hyp_dir)
    exit_code, run_out_dir, results, _summary = _run_score_like(
        corpus=args.corpus,
        source=source,
        out_dir=args.out_dir,
        run_id=args.run_id,
        build_document=_resolve_build_document(build_document),
        to_html=_resolve_to_html(to_html),
        score_document=_resolve_score_document(score_document),
        rollup=_resolve_rollup(rollup),
        load_role_map=_resolve_load_role_map(load_role_map),
    )
    if run_out_dir is not None:
        shadow_expected = {
            r.case_id: {
                "tier": r.tier,
                "display_score": r.display_score,
                "failed_assertions": [a.id for a in r.assertions if not a.passed],
            }
            for r in results
        }
        (run_out_dir / "shadow_expected.json").write_text(json.dumps(shadow_expected, indent=2), encoding="utf-8")
    return exit_code


# --- rank ------------------------------------------------------------------


def _cmd_rank(args, *, rank_key=None) -> int:
    rank_key = _resolve_rank_key(rank_key)
    summaries = []
    for path in args.summary:
        if not path.is_file():
            print(f"error: summary file not found: {path}", file=sys.stderr)
            return 2
        summaries.append(json.loads(path.read_text(encoding="utf-8")))
    summaries.sort(key=rank_key, reverse=True)
    report.write_leaderboard_csv(summaries, args.out)
    return 0


# --- fixtures-check --------------------------------------------------------


def _cmd_fixtures_check(args, *, build_document=None, to_html=None, score_document=None, load_role_map=None) -> int:
    build_document = _resolve_build_document(build_document)
    to_html = _resolve_to_html(to_html)
    score_document = _resolve_score_document(score_document)
    load_role_map = _resolve_load_role_map(load_role_map)

    fixtures_dir: Path = args.fixtures_dir
    fixture_dirs = sorted(p for p in fixtures_dir.iterdir() if p.is_dir()) if fixtures_dir.is_dir() else []
    if not fixture_dirs:
        # Only exit codes 0/4 are defined for this command (docs/grader-plan.md
        # section 6): an empty/missing fixtures dir is not a vacuous pass, it is
        # a CI-gate failure - there is nothing to confirm the grader against.
        print(f"error: no fixtures found under {fixtures_dir}", file=sys.stderr)
        return 4

    role_map = load_role_map(None)
    rows: list[tuple[str, bool, str]] = []
    for fixture_dir in fixture_dirs:
        ok, detail = _check_one_fixture(
            fixture_dir,
            build_document=build_document,
            to_html=to_html,
            score_document=score_document,
            role_map=role_map,
        )
        rows.append((fixture_dir.name, ok, detail))

    _print_fixture_table(rows)
    return 0 if all(ok for _, ok, _ in rows) else 4


def _check_one_fixture(fixture_dir: Path, *, build_document, to_html, score_document, role_map) -> tuple[bool, str]:
    gt_path = fixture_dir / "gt.html"
    expected_path = fixture_dir / "expected.json"
    if not gt_path.is_file() or not expected_path.is_file():
        return False, "missing gt.html or expected.json"

    hyp_path = fixture_dir / "hyp.html"
    hyp_hint: inputs.Hint = "html"
    if not hyp_path.is_file():
        hyp_path = fixture_dir / "hyp.md"
        hyp_hint = "markdown"
    if not hyp_path.is_file():
        return False, "missing hyp.html or hyp.md"

    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"invalid expected.json: {exc}"

    sidecar_path = fixture_dir / "sidecar.meta.json"
    sc = sidecar.load(sidecar_path) if sidecar_path.is_file() else sidecar.default_sidecar(fixture_dir.name)

    gt_html = gt_path.read_text(encoding="utf-8")
    hyp_raw = hyp_path.read_text(encoding="utf-8")

    gt_doc = build_document(gt_html, role_map, sc)
    hyp_html = to_html(hyp_raw, hyp_hint)
    hyp_doc = build_document(hyp_html, role_map, sc)

    result = score_document(gt_doc, hyp_doc, sc, status="ok", input_form=hyp_hint, category=sc.category)

    problems: list[str] = []
    expected_tier = expected.get("tier")
    if expected_tier not in VALID_TIERS:
        problems.append(f"expected.json has an invalid tier: {expected_tier!r}")
    elif result.tier != expected_tier:
        problems.append(f"tier={result.tier!r} != expected {expected_tier!r}")

    failed_ids = {a.id for a in result.assertions if not a.passed}
    for aid in expected.get("assertions_failed", []):
        if aid not in failed_ids:
            problems.append(f"expected {aid} to fail, but it passed")

    if "structure_na" in expected and result.structure_na != expected["structure_na"]:
        problems.append(f"structure_na={result.structure_na} != expected {expected['structure_na']}")
    if "min_display_score" in expected and result.display_score < expected["min_display_score"]:
        problems.append(f"display_score {result.display_score} < min {expected['min_display_score']}")
    if "max_display_score" in expected and result.display_score > expected["max_display_score"]:
        problems.append(f"display_score {result.display_score} > max {expected['max_display_score']}")

    return (not problems), ("; ".join(problems) if problems else "ok")


def _print_fixture_table(rows: list[tuple[str, bool, str]]) -> None:
    width = max((len(name) for name, _, _ in rows), default=8)
    for name, ok, detail in rows:
        status = "PASS" if ok else "FAIL"
        print(f"{name.ljust(width)}  {status}  {detail}")


# --- misc -----------------------------------------------------------------


def _model_safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value)


def _default_run_id(model: str | None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_model = _model_safe(model) if model else "unknown-model"
    return f"{timestamp}_{safe_model}"

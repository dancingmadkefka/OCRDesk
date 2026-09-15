"""End-to-end tests for ocrgrade.cli, run entirely against the `fake_pipeline`
stand-ins (see conftest.py) since Scopes A and B do not exist yet. These tests
validate the CLI's own plumbing - argument parsing, file discovery, exit codes,
and the shape of the files it writes - not the real grading formulas.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ocrgrade import cli, sidecar


def _make_corpus(tmp_path: Path, cases: dict[str, str]) -> Path:
    """AIFA GT Set layout: one folder per case_id, holding just a GT html (no
    image, exercising the "synthetic corpus" branch of discover_corpus).
    """
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    for case_id, gt_text in cases.items():
        case_dir = corpus_dir / case_id
        case_dir.mkdir()
        (case_dir / f"{case_id}.html").write_text(gt_text, encoding="utf-8")
    return corpus_dir


def _make_hyp_dir(tmp_path: Path, cases: dict[str, str]) -> Path:
    hyp_dir = tmp_path / "hyp"
    hyp_dir.mkdir()
    for case_id, text in cases.items():
        (hyp_dir / f"{case_id}.html").write_text(text, encoding="utf-8")
    return hyp_dir


def _write_fixture(fixtures_dir: Path, name: str, *, gt: str, hyp: str, expected: dict) -> Path:
    d = fixtures_dir / name
    d.mkdir(parents=True)
    (d / "gt.html").write_text(gt, encoding="utf-8")
    (d / "hyp.html").write_text(hyp, encoding="utf-8")
    (d / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    return d


# --- argparse usage errors (exit 1) ----------------------------------------------


def test_main_with_no_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code == 1


def test_score_with_no_source_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["score", "--corpus", str(tmp_path), "--out-dir", str(tmp_path)])
    assert exc_info.value.code == 1


def test_score_with_two_sources_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "score",
                "--corpus", str(tmp_path),
                "--hyp-dir", str(tmp_path),
                "--aifa-results", str(tmp_path / "x.json"),
                "--out-dir", str(tmp_path),
            ]
        )
    assert exc_info.value.code == 1


def test_annotate_without_corpus_is_a_usage_error():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["annotate"])
    assert exc_info.value.code == 1


# --- score: usage-shaped domain errors (exit 1 / 2) ------------------------------


def test_score_missing_corpus_flag_for_non_ocrdesk_source(tmp_path, fake_pipeline):
    hyp_dir = _make_hyp_dir(tmp_path, {})
    exit_code = cli.main(
        ["score", "--hyp-dir", str(hyp_dir), "--out-dir", str(tmp_path / "out")], **fake_pipeline
    )
    assert exit_code == 1


def test_score_ocrdesk_without_model_flag(tmp_path, fake_pipeline):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    exit_code = cli.main(
        ["score", "--ocrdesk-images-dir", str(images_dir), "--out-dir", str(tmp_path / "out")], **fake_pipeline
    )
    assert exit_code == 1


def test_score_corpus_not_found(tmp_path, fake_pipeline):
    hyp_dir = _make_hyp_dir(tmp_path, {})
    exit_code = cli.main(
        [
            "score",
            "--corpus", str(tmp_path / "does-not-exist"),
            "--hyp-dir", str(hyp_dir),
            "--out-dir", str(tmp_path / "out"),
        ],
        **fake_pipeline,
    )
    assert exit_code == 2


def test_score_no_cases_discovered(tmp_path, fake_pipeline):
    empty_corpus = tmp_path / "empty_corpus"
    empty_corpus.mkdir()
    hyp_dir = _make_hyp_dir(tmp_path, {})
    exit_code = cli.main(
        ["score", "--corpus", str(empty_corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(tmp_path / "out")],
        **fake_pipeline,
    )
    assert exit_code == 2


def test_score_aifa_results_file_not_found(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "x"})
    exit_code = cli.main(
        [
            "score",
            "--corpus", str(corpus),
            "--aifa-results", str(tmp_path / "nope.json"),
            "--out-dir", str(tmp_path / "out"),
        ],
        **fake_pipeline,
    )
    assert exit_code == 2


# --- score: happy paths across all three hypothesis sources ---------------------


def test_score_hyp_dir_happy_path_writes_all_outputs(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "hello world", "case-2": "hello world"})
    hyp_dir = _make_hyp_dir(tmp_path, {"case-1": "hello world", "case-2": "something else"})
    out_dir = tmp_path / "out"

    exit_code = cli.main(
        ["score", "--corpus", str(corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(out_dir), "--run-id", "test-run"],
        **fake_pipeline,
    )

    assert exit_code == 0
    run_dir = out_dir / "test-run"
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "leaderboard.csv").is_file()
    assert (run_dir / "report.html").is_file()

    case1 = json.loads((run_dir / "cases" / "case-1.json").read_text(encoding="utf-8"))
    assert case1["tier"] == "PASS"
    case2 = json.loads((run_dir / "cases" / "case-2.json").read_text(encoding="utf-8"))
    assert case2["tier"] == "REJECT"

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["n_cases"] == 2
    assert summary["n_pass"] == 1
    assert summary["n_reject"] == 1
    assert summary["run_id"] == "test-run"


def test_score_default_run_id_format(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "x"})
    hyp_dir = _make_hyp_dir(tmp_path, {"case-1": "x"})
    out_dir = tmp_path / "out"

    exit_code = cli.main(
        ["score", "--corpus", str(corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(out_dir)], **fake_pipeline
    )

    assert exit_code == 0
    run_dirs = list(out_dir.iterdir())
    assert len(run_dirs) == 1
    assert re.match(r"^\d{8}-\d{6}_" + re.escape(hyp_dir.name) + r"$", run_dirs[0].name)


def test_score_aifa_results_source(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "same text"})
    payload = {
        "model": "m1", "quant": "q1", "prompt": "p1", "sampling_profile": None, "sampling": None,
        "cases": [{"case_id": "case-1", "status": "ok", "hypothesis_html": "same text", "runtime_seconds": 3.2}],
    }
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps(payload), encoding="utf-8")
    out_dir = tmp_path / "out"

    exit_code = cli.main(
        ["score", "--corpus", str(corpus), "--aifa-results", str(results_path), "--out-dir", str(out_dir)],
        **fake_pipeline,
    )

    assert exit_code == 0
    run_dirs = list(out_dir.iterdir())
    summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["model"] == "m1"
    case1 = json.loads((run_dirs[0] / "cases" / "case-1.json").read_text(encoding="utf-8"))
    assert case1["tier"] == "PASS"
    assert case1["runtime_seconds"] == 3.2


def test_score_ocrdesk_source(tmp_path, fake_pipeline):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "invoice_ground_truth.html").write_text("same text", encoding="utf-8")
    results_dir = images_dir / "results" / "invoice"
    results_dir.mkdir(parents=True)
    (results_dir / "my-model.html").write_text("same text", encoding="utf-8")
    out_dir = tmp_path / "out"

    exit_code = cli.main(
        ["score", "--ocrdesk-images-dir", str(images_dir), "--model", "my-model", "--out-dir", str(out_dir)],
        **fake_pipeline,
    )

    assert exit_code == 0
    run_dirs = list(out_dir.iterdir())
    summary = json.loads((run_dirs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["model"] == "my-model"
    assert summary["n_pass"] == 1


# --- score: a single bad case yields exit 3 but does not abort the whole run ----


def test_score_one_case_exception_yields_exit_3_but_scores_the_rest(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "good", "case-2": "boom-trigger"})
    hyp_dir = _make_hyp_dir(tmp_path, {"case-1": "good", "case-2": "boom-trigger"})
    out_dir = tmp_path / "out"

    real_build_document = fake_pipeline["build_document"]

    def _raising_build_document(html_text, role_map, sc):
        if "boom-trigger" in (html_text or ""):
            raise RuntimeError("simulated bug in build_document")
        return real_build_document(html_text, role_map, sc)

    injectables = dict(fake_pipeline)
    injectables["build_document"] = _raising_build_document

    exit_code = cli.main(
        ["score", "--corpus", str(corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(out_dir)], **injectables
    )

    assert exit_code == 3
    run_dir = next(out_dir.iterdir())
    assert (run_dir / "cases" / "case-1.json").is_file()
    assert not (run_dir / "cases" / "case-2.json").is_file()
    # the run must still produce a summary for the cases that did score.
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["n_cases"] == 1


# --- annotate ---------------------------------------------------------------------


def test_annotate_corpus_not_found(tmp_path, fake_pipeline):
    exit_code = cli.main(
        ["annotate", "--corpus", str(tmp_path / "nope")],
        build_document=fake_pipeline["build_document"],
        load_role_map=fake_pipeline["load_role_map"],
    )
    assert exit_code == 2


def test_annotate_no_cases_discovered(tmp_path, fake_pipeline):
    corpus = tmp_path / "empty"
    corpus.mkdir()
    exit_code = cli.main(
        ["annotate", "--corpus", str(corpus)],
        build_document=fake_pipeline["build_document"],
        load_role_map=fake_pipeline["load_role_map"],
    )
    assert exit_code == 2


def test_annotate_happy_path_writes_sidecars_and_review_csv(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "Invoice text here", "case-2": "Receipt text here"})

    exit_code = cli.main(
        ["annotate", "--corpus", str(corpus)],
        build_document=fake_pipeline["build_document"],
        load_role_map=fake_pipeline["load_role_map"],
    )

    assert exit_code == 0
    assert (corpus / "case-1" / "case-1.meta.json").is_file()
    assert (corpus / "case-2" / "case-2.meta.json").is_file()

    review_csv = corpus / "annotations_review.csv"
    assert review_csv.is_file()
    rows = review_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3  # header + 2 cases


def test_annotate_preserves_a_confirmed_sidecar(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "some text"})
    sidecar_path = corpus / "case-1" / "case-1.meta.json"
    confirmed = sidecar.default_sidecar("case-1")
    confirmed.category = "payslip"
    confirmed.confirmed = True
    sidecar.save(confirmed, sidecar_path)

    exit_code = cli.main(
        ["annotate", "--corpus", str(corpus)],
        build_document=fake_pipeline["build_document"],
        load_role_map=fake_pipeline["load_role_map"],
    )

    assert exit_code == 0
    reloaded = sidecar.load(sidecar_path)
    assert reloaded.category == "payslip"
    assert reloaded.confirmed is True


def test_annotate_reads_manifest_document_type(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "nothing keyword worthy"})
    manifest_path = tmp_path / "ocr_manifest.json"
    manifest_path.write_text(json.dumps({"case-1": {"document_type": "payslip"}}), encoding="utf-8")

    exit_code = cli.main(
        ["annotate", "--corpus", str(corpus), "--manifest", str(manifest_path)],
        build_document=fake_pipeline["build_document"],
        load_role_map=fake_pipeline["load_role_map"],
    )

    assert exit_code == 0
    sc = sidecar.load(corpus / "case-1" / "case-1.meta.json")
    assert sc.category == "payslip"


# --- rank ---------------------------------------------------------------------


def test_rank_missing_summary_file(tmp_path, fake_pipeline):
    exit_code = cli.main(
        ["rank", "--summary", str(tmp_path / "nope.json"), "--out", str(tmp_path / "out.csv")],
        rank_key=fake_pipeline["rank_key"],
    )
    assert exit_code == 2


def test_rank_merges_and_sorts_by_rank_key(tmp_path, fake_pipeline):
    worse = {
        "model": "worse-model", "quant": None, "prompt": None, "n_cases": 10,
        "catastrophic_rate": 0.5, "gate_pass_rate": 0.2, "archival_safe_rate": 0.1,
        "macro_q": 0.3, "worst_category_q": 0.1, "category_macro": {"receipt": 40.0},
    }
    better = {
        "model": "better-model", "quant": None, "prompt": None, "n_cases": 10,
        "catastrophic_rate": 0.0, "gate_pass_rate": 0.9, "archival_safe_rate": 0.5,
        "macro_q": 0.9, "worst_category_q": 0.8, "category_macro": {"receipt": 95.0, "invoice": 90.0},
    }
    p1 = tmp_path / "worse.json"
    p2 = tmp_path / "better.json"
    p1.write_text(json.dumps(worse), encoding="utf-8")
    p2.write_text(json.dumps(better), encoding="utf-8")
    out = tmp_path / "leaderboard.csv"

    exit_code = cli.main(
        ["rank", "--summary", str(p1), "--summary", str(p2), "--out", str(out)], rank_key=fake_pipeline["rank_key"]
    )

    assert exit_code == 0
    rows = out.read_text(encoding="utf-8").strip().splitlines()
    header = rows[0].split(",")
    assert header[:9] == [
        "model", "quant", "prompt", "n", "catastrophic_rate", "gate_pass_rate",
        "archival_safe_rate", "macro_Q", "worst_category_Q",
    ]
    assert "invoice_display_score" in header
    assert "receipt_display_score" in header
    assert rows[1].startswith("better-model,")


# --- shadow ---------------------------------------------------------------------


def test_shadow_writes_shadow_expected_json(tmp_path, fake_pipeline):
    corpus = _make_corpus(tmp_path, {"case-1": "same", "case-2": "same"})
    hyp_dir = _make_hyp_dir(tmp_path, {"case-1": "same", "case-2": "different"})
    out_dir = tmp_path / "out"

    exit_code = cli.main(
        ["shadow", "--corpus", str(corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(out_dir), "--run-id", "shadow-run"],
        **fake_pipeline,
    )

    assert exit_code == 0
    shadow_path = out_dir / "shadow-run" / "shadow_expected.json"
    assert shadow_path.is_file()
    data = json.loads(shadow_path.read_text(encoding="utf-8"))
    assert data["case-1"]["tier"] == "PASS"
    assert data["case-1"]["failed_assertions"] == []
    assert data["case-2"]["tier"] == "REJECT"
    assert data["case-2"]["failed_assertions"] == ["FAKE1"]
    # shadow must also have written the normal score outputs alongside it.
    assert (out_dir / "shadow-run" / "summary.json").is_file()


# --- fixtures-check ---------------------------------------------------------------


def test_fixtures_check_all_pass_exits_zero(tmp_path, fake_pipeline):
    fixtures_dir = tmp_path / "fixtures"
    _write_fixture(fixtures_dir, "fx-match", gt="hello", hyp="hello", expected={"tier": "PASS", "assertions_failed": []})
    _write_fixture(
        fixtures_dir, "fx-mismatch", gt="hello", hyp="different", expected={"tier": "REJECT", "assertions_failed": ["FAKE1"]}
    )

    exit_code = cli.main(
        ["fixtures-check", "--fixtures-dir", str(fixtures_dir)],
        build_document=fake_pipeline["build_document"],
        to_html=fake_pipeline["to_html"],
        score_document=fake_pipeline["score_document"],
    )

    assert exit_code == 0


def test_fixtures_check_reports_a_tier_mismatch(tmp_path, fake_pipeline, capsys):
    fixtures_dir = tmp_path / "fixtures"
    _write_fixture(fixtures_dir, "fx-wrong", gt="hello", hyp="hello", expected={"tier": "REJECT", "assertions_failed": []})

    exit_code = cli.main(
        ["fixtures-check", "--fixtures-dir", str(fixtures_dir)],
        build_document=fake_pipeline["build_document"],
        to_html=fake_pipeline["to_html"],
        score_document=fake_pipeline["score_document"],
    )

    assert exit_code == 4
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "fx-wrong" in out


def test_fixtures_check_missing_directory_exits_four(tmp_path, fake_pipeline):
    exit_code = cli.main(
        ["fixtures-check", "--fixtures-dir", str(tmp_path / "nope")],
        build_document=fake_pipeline["build_document"],
        to_html=fake_pipeline["to_html"],
        score_document=fake_pipeline["score_document"],
    )
    assert exit_code == 4


def test_fixtures_check_incomplete_fixture_counts_as_a_mismatch(tmp_path, fake_pipeline):
    fixtures_dir = tmp_path / "fixtures"
    incomplete = fixtures_dir / "fx-incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "gt.html").write_text("hello", encoding="utf-8")
    # no hyp.html, no expected.json

    exit_code = cli.main(
        ["fixtures-check", "--fixtures-dir", str(fixtures_dir)],
        build_document=fake_pipeline["build_document"],
        to_html=fake_pipeline["to_html"],
        score_document=fake_pipeline["score_document"],
    )

    assert exit_code == 4


def test_fixtures_check_uses_committed_sidecar_when_present(tmp_path, fake_pipeline):
    fixtures_dir = tmp_path / "fixtures"
    d = _write_fixture(fixtures_dir, "fx-with-sidecar", gt="hello", hyp="hello", expected={"tier": "PASS", "assertions_failed": []})
    sc = sidecar.default_sidecar("fx-with-sidecar")
    sc.category = "letter"
    sidecar.save(sc, d / "sidecar.meta.json")

    seen_categories = []
    real_score_document = fake_pipeline["score_document"]

    def _spy_score_document(gt, hyp, sc, **kwargs):
        seen_categories.append(sc.category)
        return real_score_document(gt, hyp, sc, **kwargs)

    exit_code = cli.main(
        ["fixtures-check", "--fixtures-dir", str(fixtures_dir)],
        build_document=fake_pipeline["build_document"],
        to_html=fake_pipeline["to_html"],
        score_document=_spy_score_document,
    )

    assert exit_code == 0
    assert seen_categories == ["letter"]

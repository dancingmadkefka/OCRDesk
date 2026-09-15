"""Tests for ocrgrade.inputs: corpus discovery and hypothesis loading."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocrgrade import inputs


# --- discover_corpus (AIFA GT Set layout: one folder per case) ------------------


def test_discover_corpus_image_plus_same_stem_gt(tmp_path):
    case_dir = tmp_path / "case-001"
    case_dir.mkdir()
    (case_dir / "receipt.jpg").write_bytes(b"fake-image-bytes")
    (case_dir / "receipt.html").write_text("<html></html>", encoding="utf-8")

    cases = inputs.discover_corpus(tmp_path)

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "case-001"
    assert case.image_path == case_dir / "receipt.jpg"
    assert case.gt_path == case_dir / "receipt.html"
    assert case.sidecar_path == case_dir / "receipt.meta.json"


def test_discover_corpus_no_image_uses_the_only_html(tmp_path):
    case_dir = tmp_path / "case-002"
    case_dir.mkdir()
    (case_dir / "letter.html").write_text("<html></html>", encoding="utf-8")

    cases = inputs.discover_corpus(tmp_path)

    assert len(cases) == 1
    assert cases[0].image_path is None
    assert cases[0].gt_path == case_dir / "letter.html"
    assert cases[0].sidecar_path == case_dir / "letter.meta.json"


def test_discover_corpus_skips_image_without_matching_gt(tmp_path):
    case_dir = tmp_path / "case-003"
    case_dir.mkdir()
    (case_dir / "photo.jpg").write_bytes(b"x")

    assert inputs.discover_corpus(tmp_path) == []


def test_discover_corpus_skips_folder_with_ambiguous_images(tmp_path):
    case_dir = tmp_path / "case-004"
    case_dir.mkdir()
    (case_dir / "a.jpg").write_bytes(b"x")
    (case_dir / "b.jpg").write_bytes(b"x")
    (case_dir / "a.html").write_text("<html></html>", encoding="utf-8")

    assert inputs.discover_corpus(tmp_path) == []


def test_discover_corpus_skips_folder_with_ambiguous_html_and_no_image(tmp_path):
    case_dir = tmp_path / "case-005"
    case_dir.mkdir()
    (case_dir / "one.html").write_text("<html></html>", encoding="utf-8")
    (case_dir / "two.html").write_text("<html></html>", encoding="utf-8")

    assert inputs.discover_corpus(tmp_path) == []


def test_discover_corpus_ignores_loose_files_at_corpus_root(tmp_path):
    (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")
    case_dir = tmp_path / "case-006"
    case_dir.mkdir()
    (case_dir / "x.html").write_text("<html></html>", encoding="utf-8")

    cases = inputs.discover_corpus(tmp_path)

    assert len(cases) == 1
    assert cases[0].case_id == "case-006"


def test_discover_corpus_is_sorted_by_case_id(tmp_path):
    for name in ("case-b", "case-a", "case-c"):
        d = tmp_path / name
        d.mkdir()
        (d / "x.html").write_text("<html></html>", encoding="utf-8")

    cases = inputs.discover_corpus(tmp_path)

    assert [c.case_id for c in cases] == ["case-a", "case-b", "case-c"]


def test_discover_corpus_missing_directory_returns_empty_list(tmp_path):
    assert inputs.discover_corpus(tmp_path / "does-not-exist") == []


# --- discover_ocrdesk_corpus (flat OCRDesk layout) ------------------------------


def test_discover_ocrdesk_corpus_finds_gt_and_matching_image(tmp_path):
    (tmp_path / "invoice_ground_truth.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "invoice.ocr_ready.jpg").write_bytes(b"x")

    cases = inputs.discover_ocrdesk_corpus(tmp_path)

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "invoice"
    assert case.image_path == tmp_path / "invoice.ocr_ready.jpg"
    assert case.gt_path == tmp_path / "invoice_ground_truth.html"
    assert case.sidecar_path == tmp_path / "invoice.meta.json"


def test_discover_ocrdesk_corpus_gt_without_image_still_found(tmp_path):
    (tmp_path / "letter_ground_truth.html").write_text("<html></html>", encoding="utf-8")

    cases = inputs.discover_ocrdesk_corpus(tmp_path)

    assert len(cases) == 1
    assert cases[0].image_path is None


def test_discover_ocrdesk_corpus_missing_directory_returns_empty_list(tmp_path):
    assert inputs.discover_ocrdesk_corpus(tmp_path / "nope") == []


def test_ocrdesk_result_path_layout():
    assert inputs.ocrdesk_result_path(Path("/images"), "invoice", "gpt-4o") == Path("/images/results/invoice/gpt-4o.html")


# --- load_hypotheses: source validation -----------------------------------------


def test_load_hypotheses_requires_exactly_one_source(tmp_path):
    with pytest.raises(ValueError):
        inputs.load_hypotheses()
    with pytest.raises(ValueError):
        inputs.load_hypotheses(aifa_results=tmp_path / "a.json", hyp_dir=tmp_path)


def test_load_hypotheses_ocrdesk_requires_model(tmp_path):
    with pytest.raises(ValueError):
        inputs.load_hypotheses(ocrdesk_images_dir=tmp_path, case_ids=["x"])


# --- load_hypotheses: AIFA html_vlm_*.json source -------------------------------


def test_load_hypotheses_aifa_results_basic(tmp_path):
    payload = {
        "model": "qwen3-vl-8b",
        "quant": "Q4_K_M",
        "prompt": "ocrdesk_html",
        "sampling_profile": "qwen35_instruct_recommended",
        "sampling": {"temperature": 0.7},
        "cases": [
            {"case_id": "case-1", "status": "ok", "hypothesis_html": "<html>ok</html>", "runtime_seconds": 1.5},
            {"case_id": "case-2", "status": "error", "error": "timeout", "runtime_seconds": 2.0},
            {"case_id": "case-3", "status": "skipped", "error": "no image"},
        ],
    }
    results_path = tmp_path / "html_vlm_x.json"
    results_path.write_text(json.dumps(payload), encoding="utf-8")

    records, run_meta = inputs.load_hypotheses(aifa_results=results_path)

    assert run_meta["model"] == "qwen3-vl-8b"
    assert run_meta["quant"] == "Q4_K_M"
    assert run_meta["prompt"] == "ocrdesk_html"

    by_id = {r.case_id: r for r in records}
    assert by_id["case-1"].status == "ok"
    assert by_id["case-1"].raw == "<html>ok</html>"
    assert by_id["case-1"].hint == "html"
    assert by_id["case-1"].runtime_seconds == 1.5

    assert by_id["case-2"].status == "error"
    assert by_id["case-2"].error == "timeout"
    assert by_id["case-2"].raw == ""

    assert by_id["case-3"].status == "skipped"


def test_load_hypotheses_aifa_results_prefers_hypothesis_raw_over_hypothesis_html(tmp_path):
    payload = {
        "model": "m", "quant": None, "prompt": "p", "sampling_profile": None, "sampling": None,
        "cases": [
            {
                "case_id": "case-1",
                "status": "ok",
                "hypothesis_html": "<html>should not be used</html>",
                "hypothesis_raw": "# Heading\n\n| a | b |\n| - | - |\n| 1 | 2 |\n",
            }
        ],
    }
    results_path = tmp_path / "html_vlm_x.json"
    results_path.write_text(json.dumps(payload), encoding="utf-8")

    records, _ = inputs.load_hypotheses(aifa_results=results_path)

    assert records[0].raw.startswith("# Heading")
    assert records[0].hint == "markdown"


def test_load_hypotheses_aifa_results_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        inputs.load_hypotheses(aifa_results=tmp_path / "nope.json")


# --- load_hypotheses: hypothesis directory --------------------------------------


def test_load_hypotheses_hyp_dir_extensions_and_nested_layout(tmp_path):
    (tmp_path / "case-1.html").write_text("<p>one</p>", encoding="utf-8")
    (tmp_path / "case-2.md").write_text("# two", encoding="utf-8")
    (tmp_path / "case-3.txt").write_text("three", encoding="utf-8")
    nested = tmp_path / "case-4"
    nested.mkdir()
    (nested / "hyp.html").write_text("<p>four</p>", encoding="utf-8")

    records, run_meta = inputs.load_hypotheses(
        hyp_dir=tmp_path, case_ids=["case-1", "case-2", "case-3", "case-4", "case-5"]
    )

    by_id = {r.case_id: r for r in records}
    assert by_id["case-1"].hint == "html" and by_id["case-1"].raw == "<p>one</p>"
    assert by_id["case-2"].hint == "markdown" and by_id["case-2"].raw == "# two"
    assert by_id["case-3"].hint == "plain" and by_id["case-3"].raw == "three"
    assert by_id["case-4"].hint == "html" and by_id["case-4"].raw == "<p>four</p>"
    assert by_id["case-5"].status == "error"
    assert by_id["case-5"].error
    assert run_meta["model"] == tmp_path.name


def test_load_hypotheses_hyp_dir_prefers_html_over_other_extensions(tmp_path):
    (tmp_path / "case-1.html").write_text("<p>html wins</p>", encoding="utf-8")
    (tmp_path / "case-1.md").write_text("# md loses", encoding="utf-8")

    records, _ = inputs.load_hypotheses(hyp_dir=tmp_path, case_ids=["case-1"])

    assert records[0].raw == "<p>html wins</p>"


# --- load_hypotheses: OCRDesk layout ---------------------------------------------


def test_load_hypotheses_ocrdesk_layout(tmp_path):
    results_dir = tmp_path / "results" / "invoice"
    results_dir.mkdir(parents=True)
    (results_dir / "my-model.html").write_text("<p>hyp</p>", encoding="utf-8")

    records, run_meta = inputs.load_hypotheses(
        ocrdesk_images_dir=tmp_path, ocrdesk_model="my-model", case_ids=["invoice", "missing-case"]
    )

    by_id = {r.case_id: r for r in records}
    assert by_id["invoice"].status == "ok"
    assert by_id["invoice"].raw == "<p>hyp</p>"
    assert by_id["invoice"].hint == "html"
    assert by_id["missing-case"].status == "error"
    assert run_meta["model"] == "my-model"


# --- hint detection --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<!DOCTYPE html><html><body><p>hi</p></body></html>", "html"),
        ("<table><tr><td>1</td></tr></table>", "html"),
        ("```html\n<table><tr><td>1</td></tr></table>\n```", "html"),
        ("# Heading\nSome text", "markdown"),
        ("| a | b |\n| - | - |\n| 1 | 2 |\n", "markdown"),
        ("```markdown\n# hi\n```", "markdown"),
        ("Just a plain sentence with no markup at all.", "plain"),
    ],
)
def test_sniff_hint(raw, expected):
    assert inputs._sniff_hint(raw) == expected


@pytest.mark.parametrize(
    "suffix,expected",
    [(".md", "markdown"), (".txt", "plain"), (".html", "html"), (".htm", "html"), ("", "html"), (".json", "html")],
)
def test_hint_for_extension(suffix, expected):
    assert inputs._hint_for_extension(suffix) == expected

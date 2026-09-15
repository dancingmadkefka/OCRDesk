"""Integration-stage tests: behaviour that only shows up once the real modules meet.

Hypotheses never reuse the GT's CSS class conventions, so section checks must fall back to the
hypothesis text, and value comparisons must ignore internal whitespace.
"""
from __future__ import annotations

from pathlib import Path

from ocrgrade import assertions, canonicalize, inputs, metrics_structure, roles, sidecar as sc

GT = """<style>.section-title{font-weight:bold}.num{text-align:right}</style>
<div class="section-title">Shannon Insurance - Policy Renewal</div>
<div class="section-title">Payment details</div>
<table><tr><td>Total Payable</td><td class="num">327.50</td></tr></table>"""

HYP_NO_CLASSES = """<!DOCTYPE html><html><body>
<div class="hdr">Shannon Insurance - Policy Renewal</div>
<div class="hdr">Payment details</div>
<table><tr><td>Total Payable</td><td class="amt">327.50</td></tr></table>
</body></html>"""

HYP_REORDERED = """<div>Payment details</div><div>Shannon Insurance - Policy Renewal</div>
<table><tr><td>Total Payable</td><td>327.50</td></tr></table>"""


def _docs(gt_html: str, hyp_html: str):
    rm = roles.load_role_map(None)
    side = sc.default_sidecar("t")
    return canonicalize.build_document(gt_html, rm, side), canonicalize.build_document(hyp_html, rm, side), side


def test_a4_accepts_required_sections_present_only_in_hypothesis_text():
    gt, hyp, side = _docs(GT, HYP_NO_CLASSES)
    side.required_sections = ["Shannon Insurance - Policy Renewal", "Payment details"]
    assert hyp.section_headers == []  # the model used its own class names
    result = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}
    assert result["A4"].passed, result["A4"].detail
    assert result["A9"].passed, result["A9"].detail


def test_a9_and_heading_score_detect_reordered_sections_from_text():
    gt, hyp, side = _docs(GT, HYP_REORDERED)
    result = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}
    assert not result["A9"].passed
    assert metrics_structure.structure_metrics(gt, hyp)["heading_sequence_score"] < 1.0


def test_label_value_comparison_ignores_internal_whitespace():
    gt, hyp, side = _docs(
        "<table><tr><td>Brezel</td><td>2.50A</td></tr></table>",
        "<table><tr><td>Brezel</td><td>2.50 A</td></tr></table>",
    )
    assert gt.label_value_pairs and hyp.label_value_pairs
    result = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}
    assert result["A6"].passed, result["A6"].detail
    assert metrics_structure.structure_metrics(gt, hyp)["label_value_f1"] == 1.0


def test_discover_corpus_prefers_explicit_gt_html_in_fixture_folders(tmp_path: Path):
    case = tmp_path / "we_x"
    case.mkdir()
    (case / "gt.html").write_text("<p>gt</p>", encoding="utf-8")
    (case / "hyp.html").write_text("<p>hyp</p>", encoding="utf-8")
    found = inputs.discover_corpus(tmp_path)
    assert [c.case_id for c in found] == ["we_x"]
    assert found[0].gt_path.name == "gt.html" and found[0].image_path is None

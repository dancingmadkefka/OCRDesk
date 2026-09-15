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


def test_discover_corpus_uses_the_single_meta_json_when_stem_named_one_is_absent(tmp_path: Path):
    case = tmp_path / "we_y"
    case.mkdir()
    (case / "gt.html").write_text("<p>gt</p>", encoding="utf-8")
    (case / "sidecar.meta.json").write_text("{}", encoding="utf-8")
    found = inputs.discover_corpus(tmp_path)
    assert found[0].sidecar_path.name == "sidecar.meta.json"
    # but an explicit stem-named sidecar always wins
    (case / "gt.meta.json").write_text("{}", encoding="utf-8")
    assert inputs.discover_corpus(tmp_path)[0].sidecar_path.name == "gt.meta.json"


# --- remediation after the real-corpus identity run ---------------------------------


def test_a1_matches_locale_formatted_totals_by_cents():
    from ocrgrade.ir import CellRef, CriticalField

    gt_html = "<table><tr><td>Combined Total</td><td class=\"final-val\">3,637.00</td></tr></table>"
    gt, hyp, side = _docs(gt_html, gt_html)
    side.critical_fields = [CriticalField("grand_total", "3637.00", CellRef(0, 0, 1))]
    result = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}
    assert result["A1"].passed, result["A1"].detail
    # a changed digit still fails
    gt2, hyp2, side2 = _docs(gt_html, gt_html.replace("3,637.00", "3,673.00"))
    side2.critical_fields = side.critical_fields
    assert not {a.id: a for a in assertions.run_assertions(gt2, hyp2, side2)}["A1"].passed


def test_two_digit_year_dates_and_swiss_uid_are_not_amounts():
    from ocrgrade import fintoken

    toks = fintoken.extract_tokens("Datum: 6.6.25 Rechnung CHE-116.303.292 Betrag CHF 1'932.24 Total 60.00")
    by_type = {}
    for t in toks:
        by_type.setdefault(t.type, []).append(t.raw)
    assert by_type.get("date") == ["6.6.25"]
    assert "CHE-116.303.292" in by_type.get("reference_id", [])
    amounts = [t.cents for t in toks if t.type == "amount"]
    assert amounts == [193224, 6000], amounts


def test_grouped_integer_amounts_need_a_currency_marker():
    from ocrgrade import fintoken

    assert [t.type for t in fintoken.extract_tokens("Ref 116.303.292")] != ["amount"]
    toks = fintoken.extract_tokens("Total EUR 1,234 due")
    assert [(t.type, t.cents) for t in toks] == [("amount", 123400)]


def test_a2_ignores_empty_total_cells():
    gt_html = "<table><tr><td class=\"final-val\"></td><td class=\"final-val\">12.00</td></tr><tr><td>Total</td><td>12.00</td></tr></table>"
    gt, hyp, side = _docs(gt_html, gt_html)
    result = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}
    assert result["A2"].passed, result["A2"].detail


def test_manifest_document_type_is_the_category_prior():
    doc, _, _ = _docs("<p>Your tax statement. Bank statement lines follow.</p>", "<p>x</p>")
    assert sc._infer_category(doc, {"document_type": "statement"})[0] == "bank-statement"
    assert sc._infer_category(doc, {"document_type": "payslip"})[0] == "payslip"
    assert sc._infer_category(doc, {})[0] == "bank-statement"  # keywords only without a prior


# --- remediation after grading the first real model output ---------------------------


def test_runaway_output_is_catastrophic_and_never_negative():
    from ocrgrade import scoring

    gt_html = "<table><tr><td>Total Payable</td><td class=\"final-val\">327.50</td></tr></table>" + "<p>" + "invoice line text " * 10 + "</p>"
    gt, hyp, side = _docs(gt_html, "<p>" + "garbage 12.34 " * 400 + "</p>")
    r = scoring.score_document(gt, hyp, side)
    assert r.tier == "CATASTROPHIC" and r.display_score == 0.0
    assert any("runaway" in e or "CER" in e for e in r.errors)


def test_high_cer_but_normal_length_is_catastrophic():
    from ocrgrade import scoring

    gt_html = "<p>" + "alpha beta gamma delta " * 8 + "</p>"
    gt, hyp, side = _docs(gt_html, "<p>" + "zzzz yyyy xxxx wwww " * 8 + "</p>")
    r = scoring.score_document(gt, hyp, side)
    assert r.tier == "CATASTROPHIC" and r.display_score == 0.0


def test_aifa_reader_honours_declared_markdown_form_when_text_looks_plain(tmp_path: Path):
    import json
    from ocrgrade import inputs

    payload = {"model": "m", "quant": None, "prompt": "paddleocr_vl_ocr", "sampling": {},
               "cases": [{"case_id": "case-001", "status": "ok", "hypothesis_html": "<pre>x</pre>",
                          "hypothesis_raw": "Total 12.50\nThanks", "output_form": "markdown", "runtime_seconds": 1.0}]}
    f = tmp_path / "html_vlm_x.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    records, meta = inputs._load_aifa_results(f)
    assert records[0].hint == "markdown"


# --- round 5: A1 on real Qwen/Gemma output --------------------------------------------


def test_last_row_line_item_is_not_promoted_to_total():
    html = """<table>
<tr><td>47366</td><td>PREMIUM PORRIDGE OATS</td><td>1.19 E</td></tr>
<tr><td>84693</td><td>#DRONE CAMERA</td><td>59.99 D</td></tr></table>"""
    gt, _, side = _docs(html, html)
    assert not [c for t in gt.tables for c in t.cells if c.role == "total_value"]
    html2 = "<table><tr><td>Item</td><td>1.19</td></tr><tr><td>Total</td><td>1.19</td></tr></table>"
    gt2, _, _ = _docs(html2, html2)
    assert [c.text_norm for t in gt2.tables for c in t.cells if c.role == "total_value" and c.is_numeric] == ["1.19"]


def test_row_label_prefers_short_label_over_paragraph_and_total_synonyms_match():
    from ocrgrade.ir import CellRef, CriticalField

    gt_html = ("<table><tr><td>Did you know? Register or login today where you can check your balance "
               "and view your bill and make secure payments</td><td>Total Bill amount to be taken from your bank a/c.</td>"
               "<td class=\"final-val\">133.93</td></tr></table>")
    hyp_html = "<table><tr><td>TOTAL DUE</td><td>133.93</td></tr></table>"
    gt, hyp, side = _docs(gt_html, hyp_html)
    assert assertions._row_label(gt.tables[0], 0, 2) == "Total Bill amount to be taken from your bank a/c."
    assert assertions._label_matches("Closing Balance", "Opening Balance") is False
    assert assertions._label_matches("Total", "Subtotal") is False
    assert assertions._label_matches("Net pay", "Gross pay") is False
    assert assertions._label_matches("Total Bill amount to be taken from your bank a/c.", "TOTAL DUE") is True
    side.critical_fields = [CriticalField("grand_total", "133.93", CellRef(0, 0, 2))]
    assert {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"].passed


def test_colspan_title_row_is_not_a_column_header_and_out_of_table_total_is_accepted():
    from ocrgrade.ir import CellRef, CriticalField

    gt_html = ("<table><tr><th colspan=\"2\">Explanation Panels</th></tr>"
               "<tr><td>Combined Total</td><td class=\"final-val\">3,637.00</td></tr></table>")
    hyp_html = "<p>Explanation Panels</p><p>Combined Total: 3,637.00</p>"
    gt, hyp, side = _docs(gt_html, hyp_html)
    assert assertions._col_header(gt.tables[0], 1) == ""
    side.critical_fields = [CriticalField("grand_total", "3637.00", CellRef(0, 1, 1))]
    assert {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"].passed


def test_out_of_table_total_becomes_a_critical_field_and_a1_checks_it():
    gt_html = "<h2>SALE</h2><p>Goods: 66.71</p><p>Total: EUR66.71</p><table><tr><td>6 Items</td><td>66.71</td></tr></table>"
    gt, _, _ = _docs(gt_html, gt_html)
    side = sc.derive("c", gt_html, {})
    fields = [(c.role, c.value, c.cell_ref, c.label) for c in side.critical_fields]
    assert ("grand_total", "66.71", None, "Total") in fields, fields
    ok_hyp = "<p>Total: EUR 66.71</p><p>6 Items 66.71</p>"
    bad_hyp = "<p>Total: EUR 67.71</p><p>6 Items 66.71</p>"
    for html, expect in ((ok_hyp, True), (bad_hyp, False)):
        _, hyp, _ = _docs(gt_html, html)
        assert {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"].passed is expect, html


def test_subtotal_spellings_and_prose_totals():
    assert assertions._label_matches("Sub-total cardholder balances", "Subtotal cardholder balances") is True
    assert assertions._label_matches("Subtotal", "Total") is False
    gt_html = "<table><tr><td>Total-EFT CHF</td><td class=\"final-val\">362.00</td></tr></table>"
    hyp_html = "<p>Payment</p><p>Total-EFT CHF 362.00</p>"
    gt, hyp, side = _docs(gt_html, hyp_html)
    side = sc.derive("c", gt_html, {})
    assert side.critical_fields and side.critical_fields[0].label == "Total-EFT CHF"
    assert {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"].passed
    _, hyp_bad, _ = _docs(gt_html, "<p>Payment</p><p>Total-EFT CHF 326.00</p>")
    assert not {a.id: a for a in assertions.run_assertions(gt, hyp_bad, side)}["A1"].passed


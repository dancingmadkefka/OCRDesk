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
    ok_hyp = "<p>Goods: 66.71</p><p>Total: EUR 66.71</p><p>6 Items 66.71</p>"  # GT prints it three times
    bad_hyp = "<p>Goods: 66.71</p><p>Total: EUR 67.71</p><p>6 Items 66.71</p>"
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


# --- Codex review of PR #1 ------------------------------------------------------------


def test_negative_and_parenthesised_amounts_keep_their_sign():
    from ocrgrade import fintoken

    toks = {t.raw: t for t in fintoken.extract_tokens("Debit -12.34 Credit 12.34 Fee (5.00) Total-EFT CHF 362.00 range 10-20.00")}
    assert toks["-12.34"].cents == -1234 and toks["-12.34"].canonical == "-12.34"
    assert toks["12.34"].cents == 1234
    assert toks["(5.00)"].cents == -500
    assert [t.cents for t in toks.values() if t.raw.endswith("362.00")] == [36200]  # 'Total-EFT' is a word, not a sign
    assert toks["20.00"].cents == 2000  # '10-20.00' is a range
    gt_html = "<table><tr><td>Balance</td><td class=\"final-val\">-12.34</td></tr></table>"
    gt, hyp_ok, side = _docs(gt_html, gt_html)
    side = sc.derive("c", gt_html, {})
    assert side.critical_fields[0].value == "-12.34"
    _, hyp_flipped, _ = _docs(gt_html, gt_html.replace("-12.34", "12.34"))
    assert {a.id: a for a in assertions.run_assertions(gt, hyp_ok, side)}["A1"].passed
    assert not {a.id: a for a in assertions.run_assertions(gt, hyp_flipped, side)}["A1"].passed


def test_repeated_total_must_keep_its_multiplicity():
    gt_html = ("<table><tr><td>Total</td><td class=\"final-val\">60.00</td></tr></table>"
               "<p>Customer copy</p><table><tr><td>Total</td><td class=\"final-val\">60.00</td></tr></table>")
    gt, hyp_full, _ = _docs(gt_html, gt_html)
    side = sc.derive("c", gt_html, {})
    cf = [c for c in side.critical_fields if c.value == "60.00"]
    assert cf and cf[0].expected_multiplicity == 2
    assert {a.id: a for a in assertions.run_assertions(gt, hyp_full, side)}["A1"].passed
    _, hyp_one, _ = _docs(gt_html, "<table><tr><td>Total</td><td>60.00</td></tr></table><p>Customer copy</p>")
    result = {a.id: a for a in assertions.run_assertions(gt, hyp_one, side)}["A1"]
    assert not result.passed and "appears 1x" in result.detail


def test_nested_table_rows_stay_out_of_the_parent_grid():
    html = ("<table><tr><td>Outer A</td><td><table><tr><td>Inner 1</td><td>1.00</td></tr>"
            "<tr><td>Inner 2</td><td>2.00</td></tr></table></td></tr><tr><td>Total</td><td>3.00</td></tr></table>")
    gt, _, _ = _docs(html, html)
    outer = gt.tables[0]
    assert outer.n_rows == 2, outer.n_rows
    assert len(gt.tables) == 2 and gt.tables[1].n_rows == 2


def test_aifa_run_level_output_form_is_carried(tmp_path: Path):
    import json
    from ocrgrade import inputs

    payload = {"model": "m", "output_form": "markdown",
               "cases": [{"case_id": "case-001", "status": "ok", "hypothesis_html": "<pre>x</pre>", "hypothesis_raw": "Total 12.50", "runtime_seconds": 1.0}]}
    f = tmp_path / "html_vlm_y.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    records, meta = inputs._load_aifa_results(f)
    assert meta["output_form"] == "markdown" and records[0].hint == "markdown"


def test_teds_timeout_makes_the_case_catastrophic(monkeypatch):
    from ocrgrade import metrics_structure, scoring, teds_adapter

    gt_html = "<table><tr><td>Total</td><td class=\"final-val\">1.00</td></tr></table>"
    gt, hyp, side = _docs(gt_html, gt_html)
    monkeypatch.setattr(metrics_structure.teds_adapter, "teds_scores",
                        lambda a, b: teds_adapter.TedsResult(teds=0.0, teds_struct=0.0, per_table=[0.0], n_pairs=1, timed_out=True))
    r = scoring.score_document(gt, hyp, side)
    assert r.tier == "CATASTROPHIC" and r.display_score == 0.0 and any("TEDS" in e for e in r.errors)



NESTED_GT = ("<table><tr><td>Summary</td><td><table><tr><td>Net Pay</td><td>1234.56</td></tr></table></td></tr>"
             "<tr><td>Payable by EFT</td><td>1234.56</td></tr></table>")


def test_nested_table_content_belongs_to_the_nested_table_only():
    gt, _, _ = _docs(NESTED_GT, NESTED_GT)
    wrapper = next(c for c in gt.tables[0].cells if c.row == 0 and c.col == 1)
    assert wrapper.text_norm.strip() == ""  # the outer cell owns no text of its own
    assert sum(1 for t in gt.fin_tokens if t.type == "amount" and t.cents == 123456) == 2  # printed twice, counted twice


def test_flattened_nested_tables_keep_a1_when_every_printing_survives():
    side = sc.derive("t", NESTED_GT)
    flat = ("<table><tr><td>Net Pay</td><td>1234.56</td></tr>"
            "<tr><td>Payable by EFT</td><td>1234.56</td></tr></table>")
    gt, hyp, _ = _docs(NESTED_GT, flat)
    a1 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"]
    assert a1.passed, a1.detail
    gt, hyp, _ = _docs(NESTED_GT, "<table><tr><td>Net Pay</td><td>1234.56</td></tr></table>")
    a1 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"]
    assert not a1.passed and "appears 1x in hyp, GT has it 2x" in a1.detail


def test_a1_multiplicity_is_counted_live_not_read_from_the_sidecar():
    from dataclasses import replace

    side = sc.derive("t", NESTED_GT)
    side.critical_fields = [replace(cf, expected_multiplicity=7) for cf in side.critical_fields]  # an older tokenizer's count
    gt, hyp, _ = _docs(NESTED_GT, NESTED_GT)
    a1 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"]
    assert a1.passed, a1.detail


def test_annotate_stamps_the_grader_build_and_score_reports_stale_sidecars(tmp_path: Path, capsys):
    import json
    from ocrgrade import cli

    corpus = tmp_path / "corpus"
    case = corpus / "case-001"
    case.mkdir(parents=True)
    (case / "doc.html").write_text(NESTED_GT, encoding="utf-8")
    (case / "doc.jpg").write_bytes(b"\xff\xd8\xff")
    assert cli.main(["annotate", "--corpus", str(corpus)]) == 0
    meta_path = case / "doc.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["annotator_fingerprint"] == sc.annotator_fingerprint() and len(meta["annotator_fingerprint"]) == 12

    hyp_dir = tmp_path / "hyp"
    hyp_dir.mkdir()
    (hyp_dir / "case-001.html").write_text(NESTED_GT, encoding="utf-8")
    out = tmp_path / "out"
    assert cli.main(["score", "--corpus", str(corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(out), "--run-id", "fresh"]) == 0
    assert json.loads((out / "fresh" / "summary.json").read_text(encoding="utf-8"))["stale_sidecars"] == 0

    meta["annotator_fingerprint"] = "0ld0ld0ld0ld"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["score", "--corpus", str(corpus), "--hyp-dir", str(hyp_dir), "--out-dir", str(out), "--run-id", "stale"]) == 0
    assert json.loads((out / "stale" / "summary.json").read_text(encoding="utf-8"))["stale_sidecars"] == 1
    assert "annotated by a different grader build (case-001)" in capsys.readouterr().err


def test_prose_totals_are_found_after_any_occurrence_of_their_label():
    gt_html = ("<table><tr><td>Total Payments</td><td>1810.50</td></tr><tr><td>Net Pay</td><td>1650.40</td></tr></table>"
               "<table><tr><td>Gross Taxable</td><td>3400.00</td></tr><tr><td>Net Pay</td><td>3210.75</td></tr></table>")
    side = sc.derive("t", gt_html)
    assert {(cf.label, cf.value) for cf in side.critical_fields} >= {("Net Pay", "1650.40"), ("Net Pay", "3210.75")}
    hyp_html = ("<p>Total Payments <span>1810.50</span></p><p>Net Pay <span>1650.40</span></p>"
                "<p>Summary To-date</p><p>Gross Taxable <span>3400.00</span></p><p>Net Pay <span>3210.75</span></p>")
    gt, hyp, _ = _docs(gt_html, hyp_html)
    a1 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"]
    assert a1.passed, a1.detail
    gt, hyp, _ = _docs(gt_html, hyp_html.replace("3210.75", "3120.75"))
    a1 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"]
    assert not a1.passed and "3210.75" in a1.detail


def test_vat_letter_after_non_breaking_spaces_is_still_attached():
    from ocrgrade import fintoken

    toks = {t.type: t for t in fintoken.extract_tokens("219.90\u00a0\u00a0H")}
    assert toks["vat_letter"].attached is True and toks["vat_letter"].vat_letter == "H"
    toks = {t.type: t for t in fintoken.extract_tokens("219.90\nH")}
    assert toks["vat_letter"].attached is False


def test_a2_accepts_gt_totals_kept_as_prose_or_in_a_merged_table():
    rm = roles.load_role_map(None)
    total_cls = next(c for c, r in rm.class_to_role.items() if r == "total_value")
    gt_html = (f"<table><tr><td>PAYE</td><td>90.10</td></tr><tr><td>Net Pay</td><td class='{total_cls}'>1650.40</td></tr></table>"
               "<table><tr><td>Gross Taxable</td><td>3400.00</td></tr></table>")
    gt, _, side = _docs(gt_html, gt_html)
    assert any(c.role == "total_value" for t in gt.tables for c in t.cells), "fixture needs a class-marked total"
    prose = "<p>PAYE <span>90.10</span></p><p>Net Pay <span>1650.40</span></p><p>Gross Taxable <span>3400.00</span></p>"
    merged = ("<table><tr><td>PAYE</td><td>90.10</td></tr><tr><td>Net Pay</td><td>1650.40</td></tr>"
              "<tr><td>Gross Taxable</td><td>3400.00</td></tr></table>")
    for hyp_html in (prose, merged):
        _, hyp, _ = _docs(gt_html, hyp_html)
        a2 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A2"]
        assert a2.passed, a2.detail
    _, hyp, _ = _docs(gt_html, merged.replace("1650.40", "1605.40"))
    a2 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A2"]
    assert not a2.passed and "1650.40" in a2.detail

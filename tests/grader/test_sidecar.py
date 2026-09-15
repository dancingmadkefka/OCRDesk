"""Tests for ocrgrade.sidecar: schema defaults, JSON I/O, `derive`, review CSV."""
from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace

from ocrgrade import sidecar


# --- default_sidecar / sidecar_path_for_gt ------------------------------------


def test_default_sidecar_matches_schema_v1_defaults():
    sc = sidecar.default_sidecar("case-42")
    assert sc.schema_version == 1
    assert sc.case_id == "case-42"
    assert sc.category == "other"
    assert sc.category_secondary is None
    assert sc.locale == "OTHER"
    assert sc.currency is None
    assert sc.decimal_sep == "."
    assert sc.vat_letter_scheme is False
    assert sc.has_tables is False
    assert sc.required_sections == []
    assert sc.critical_fields == []
    assert sc.label_value_pairs == []
    assert sc.line_item_schema == []
    assert sc.confirmed is False
    assert sc.notes == ""


def test_sidecar_path_for_gt_is_next_to_gt_with_same_stem(tmp_path):
    gt_path = tmp_path / "some-case" / "receipt.html"
    assert sidecar.sidecar_path_for_gt(gt_path) == tmp_path / "some-case" / "receipt.meta.json"


# --- save / load round trip ----------------------------------------------------


def test_save_and_load_round_trip(tmp_path, ir_factory):
    sc = ir_factory.sidecar(
        "case-1",
        category="invoice",
        currency="EUR",
        required_sections=["Totals"],
        critical_fields=[
            ir_factory.critical_field("grand_total", "45.99", cell_ref=ir_factory.cell_ref(0, 3, 1), expected_multiplicity=2)
        ],
        label_value_pairs=[{"label": "Total", "value": "45.99", "source": "table_row", "cell_ref": None}],
        confirmed=True,
        notes="reviewed by dan",
    )
    path = tmp_path / "receipt.meta.json"

    sidecar.save(sc, path)
    loaded = sidecar.load(path)

    assert loaded == sc
    # spot-check the on-disk shape too, since CriticalField/CellRef must serialize
    # as plain nested objects, not dataclass reprs.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["critical_fields"][0]["cell_ref"] == {"table_index": 0, "row": 3, "col": 1}


def test_load_tolerates_a_minimal_document(tmp_path):
    path = tmp_path / "minimal.meta.json"
    path.write_text(json.dumps({"case_id": "case-1"}), encoding="utf-8")

    loaded = sidecar.load(path)

    assert loaded.case_id == "case-1"
    assert loaded.category == "other"
    assert loaded.critical_fields == []


# --- load_or_default ------------------------------------------------------------


def test_load_or_default_case_files_object_with_existing_sidecar(tmp_path):
    sidecar_path = tmp_path / "case-1.meta.json"
    sc = sidecar.default_sidecar("case-1")
    sc.category = "invoice"
    sidecar.save(sc, sidecar_path)
    case_files = SimpleNamespace(case_id="case-1", gt_path=tmp_path / "case-1.html", sidecar_path=sidecar_path, image_path=None)

    loaded = sidecar.load_or_default("case-1", case_files)

    assert loaded.category == "invoice"


def test_load_or_default_case_files_object_missing_sidecar_returns_default(tmp_path):
    case_files = SimpleNamespace(
        case_id="case-1", gt_path=tmp_path / "case-1.html", sidecar_path=tmp_path / "case-1.meta.json", image_path=None
    )

    loaded = sidecar.load_or_default("case-1", case_files)

    assert loaded == sidecar.default_sidecar("case-1")


def test_load_or_default_bare_path_finds_meta_json_glob(tmp_path):
    sc = sidecar.default_sidecar("fixture-id")
    sc.category = "letter"
    sidecar.save(sc, tmp_path / "sidecar.meta.json")

    loaded = sidecar.load_or_default("fixture-id", tmp_path)

    assert loaded.category == "letter"


def test_load_or_default_bare_path_missing_returns_default(tmp_path):
    loaded = sidecar.load_or_default("fixture-id", tmp_path)
    assert loaded == sidecar.default_sidecar("fixture-id")


# --- derive: category ------------------------------------------------------------


def test_derive_category_from_gt_text_keyword(ir_factory):
    doc = ir_factory.document(body_text_norm="This is an invoice for services rendered.")

    sc = sidecar.derive("c", "<html></html>", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.category == "invoice"
    assert sc.confirmed is False


def test_derive_category_prefers_manifest_prior_over_gt_keyword(ir_factory):
    doc = ir_factory.document(body_text_norm="Please find your payslip enclosed, net pay below.")

    sc = sidecar.derive(
        "c", "", {"document_type": "invoice"}, build_document=lambda h, r, s: doc, role_map=None
    )

    assert sc.category == "invoice"  # the curated manifest is the prior; keywords only fill gaps


def test_derive_category_falls_back_to_manifest_prior_when_no_keyword_fires(ir_factory):
    doc = ir_factory.document(body_text_norm="Nothing keyword-worthy here at all.")

    sc = sidecar.derive(
        "c", "", {"document_type": "bank_statement"}, build_document=lambda h, r, s: doc, role_map=None
    )

    assert sc.category == "bank-statement"


def test_derive_category_defaults_to_other(ir_factory):
    doc = ir_factory.document(body_text_norm="")

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.category == "other"


# --- derive: locale / currency / decimal_sep / vat_letter_scheme -----------------


def test_derive_locale_from_iban_token(ir_factory):
    token = ir_factory.fin_token(type="iban", raw="IE00 FICT 1234 5678 9012 34", canonical="IE00FICT12345678901234")
    doc = ir_factory.document(fin_tokens=[token])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.locale == "IE"


def test_derive_locale_falls_back_to_manifest_language(ir_factory):
    doc = ir_factory.document()

    sc = sidecar.derive("c", "", {"language": "de"}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.locale == "DE"


def test_derive_locale_defaults_to_other(ir_factory):
    doc = ir_factory.document()

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.locale == "OTHER"


def test_derive_currency_from_first_fin_token(ir_factory):
    tokens = [ir_factory.fin_token(type="amount", currency=None), ir_factory.fin_token(type="amount", currency="EUR")]
    doc = ir_factory.document(fin_tokens=tokens)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.currency == "EUR"


def test_derive_currency_none_when_absent(ir_factory):
    doc = ir_factory.document(fin_tokens=[])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.currency is None


def test_derive_decimal_sep_majority_comma(ir_factory):
    tokens = [
        ir_factory.fin_token(type="amount", raw="5,00"),
        ir_factory.fin_token(type="amount", raw="12,50"),
        ir_factory.fin_token(type="amount", raw="3.00"),
    ]
    doc = ir_factory.document(fin_tokens=tokens)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.decimal_sep == ","


def test_derive_decimal_sep_majority_dot(ir_factory):
    tokens = [ir_factory.fin_token(type="amount", raw="5.00"), ir_factory.fin_token(type="amount", raw="3,00")]
    doc = ir_factory.document(fin_tokens=tokens)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.decimal_sep == "."


def test_derive_decimal_sep_defaults_to_dot_when_no_amounts(ir_factory):
    doc = ir_factory.document(fin_tokens=[])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.decimal_sep == "."


def test_derive_vat_letter_scheme_true_when_any_token_has_a_letter(ir_factory):
    tokens = [ir_factory.fin_token(type="amount", vat_letter=None), ir_factory.fin_token(type="amount", vat_letter="A")]
    doc = ir_factory.document(fin_tokens=tokens)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.vat_letter_scheme is True


def test_derive_vat_letter_scheme_false_when_none_present(ir_factory):
    doc = ir_factory.document(fin_tokens=[ir_factory.fin_token(type="amount", vat_letter=None)])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.vat_letter_scheme is False


# --- derive: required_sections / has_tables --------------------------------------


def test_derive_required_sections_dedups_preserving_order(ir_factory):
    doc = ir_factory.document(section_headers=["Totals", "Details", "Totals"])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.required_sections == ["Totals", "Details"]


def test_derive_has_tables_from_manifest_override(ir_factory):
    doc = ir_factory.document(tables=[])

    sc = sidecar.derive("c", "", {"has_tables": True}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.has_tables is True


def test_derive_has_tables_from_document_when_manifest_silent(ir_factory):
    doc = ir_factory.document(tables=[ir_factory.table()])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.has_tables is True


# --- derive: critical_fields -------------------------------------------------------


def test_derive_critical_fields_grand_total_and_subtotal_with_multiplicity(ir_factory):
    subtotal_cell = ir_factory.cell(
        0, 3, 1, role="total_value", text_raw="Subtotal 10.00", text_norm="subtotal 10.00",
        tokens=[ir_factory.fin_token(type="amount", canonical="10.00")],
    )
    total_cell = ir_factory.cell(
        0, 4, 1, role="total_value", text_raw="Total 10.00", text_norm="total 10.00",
        tokens=[ir_factory.fin_token(type="amount", canonical="10.00")],
    )
    table = ir_factory.table(cells=[subtotal_cell, total_cell])
    fin_tokens = [ir_factory.fin_token(type="amount", canonical="10.00") for _ in range(2)]
    doc = ir_factory.document(tables=[table], fin_tokens=fin_tokens)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    by_role = {cf.role: cf for cf in sc.critical_fields}
    assert by_role["subtotal"].value == "10.00"
    assert by_role["subtotal"].cell_ref.row == 3
    assert by_role["grand_total"].cell_ref.row == 4
    # legitimate repeats (e.g. a QR-bill total echoed elsewhere) raise the
    # expected multiplicity rather than being treated as a duplication error.
    assert by_role["grand_total"].expected_multiplicity == 2


def test_derive_critical_fields_skips_non_span_origin_cells(ir_factory):
    cell = ir_factory.cell(0, 0, 0, role="total_value", is_span_origin=False, text_raw="Total 5.00")
    doc = ir_factory.document(tables=[ir_factory.table(cells=[cell])])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.critical_fields == []


def test_derive_critical_fields_skips_total_cells_without_amount_tokens(ir_factory):
    cell = ir_factory.cell(0, 0, 0, role="total_value", text_raw="Total", text_norm="total due", tokens=[])
    doc = ir_factory.document(tables=[ir_factory.table(cells=[cell])], fin_tokens=[])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.critical_fields == []  # a total cell with no amount token is not a critical field


def test_derive_critical_fields_ignores_non_total_roles(ir_factory):
    cell = ir_factory.cell(0, 0, 0, role="numeric_value", text_raw="10.00")
    doc = ir_factory.document(tables=[ir_factory.table(cells=[cell])])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.critical_fields == []


# --- derive: label_value_pairs / line_item_schema -----------------------------------


def test_derive_label_value_pairs_converted_to_plain_dicts(ir_factory):
    pair = ir_factory.label_value_pair(
        label="Opening Balance", value="500.00", source="table_row", cell_ref=ir_factory.cell_ref(0, 0, 1)
    )
    doc = ir_factory.document(label_value_pairs=[pair])

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.label_value_pairs == [
        {"label": "Opening Balance", "value": "500.00", "source": "table_row", "cell_ref": {"table_index": 0, "row": 0, "col": 1}}
    ]


def test_derive_line_item_schema_picks_largest_qualifying_table(ir_factory):
    small_items = [ir_factory.line_item(0, r, fields={"a": "x"}) for r in range(2)]
    big_items = [ir_factory.line_item(1, r, fields={"item": "x", "price": "1.00"}) for r in range(4)]
    doc = ir_factory.document(line_items=small_items + big_items)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.line_item_schema == ["item", "price"]


def test_derive_line_item_schema_empty_below_three_row_threshold(ir_factory):
    items = [ir_factory.line_item(0, r, fields={"a": "x"}) for r in range(2)]
    doc = ir_factory.document(line_items=items)

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.line_item_schema == []


def test_derive_always_unconfirmed_with_empty_notes(ir_factory):
    doc = ir_factory.document()

    sc = sidecar.derive("c", "", {}, build_document=lambda h, r, s: doc, role_map=None)

    assert sc.confirmed is False
    assert sc.notes == ""


def test_derive_resolves_build_document_and_role_map_lazily_when_omitted(monkeypatch, ir_factory):
    """`derive()` must not require canonicalize/roles to exist unless it actually
    falls through to the lazy-import default (Scope A does not exist yet)."""
    doc = ir_factory.document(body_text_norm="a receipt")
    fake_canonicalize = SimpleNamespace(build_document=lambda h, r, s: doc)
    fake_roles = SimpleNamespace(load_role_map=lambda corpus_dir: "FAKE_ROLE_MAP")
    monkeypatch.setitem(__import__("sys").modules, "ocrgrade.canonicalize", fake_canonicalize)
    monkeypatch.setitem(__import__("sys").modules, "ocrgrade.roles", fake_roles)
    # `from ocrgrade import canonicalize` resolves through the package attribute once the real
    # module was imported by an earlier test, so patch that too (order-independent).
    import ocrgrade as _pkg
    monkeypatch.setattr(_pkg, "canonicalize", fake_canonicalize, raising=False)
    monkeypatch.setattr(_pkg, "roles", fake_roles, raising=False)

    sc = sidecar.derive("c", "<html></html>", {})

    assert sc.category == "receipt"


# --- write_review_csv ---------------------------------------------------------------


def test_write_review_csv_columns_and_row_content(tmp_path, ir_factory):
    sc1 = ir_factory.sidecar(
        "case-1",
        category="invoice",
        category_secondary="bill",
        currency="EUR",
        required_sections=["A", "B"],
        critical_fields=[ir_factory.critical_field()],
        label_value_pairs=[{"label": "x", "value": "y", "source": "table_row", "cell_ref": None}],
        confirmed=True,
        notes="reviewed",
    )
    sc2 = ir_factory.sidecar("case-2")
    path = tmp_path / "annotations_review.csv"

    sidecar.write_review_csv([sc1, sc2], path, {"case-1": "doc-1"})

    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert rows[0] == sidecar.REVIEW_COLUMNS
    cf = sc1.critical_fields[0]
    critical = f"{cf.role} {cf.value}" + (f" '{cf.label}'" if cf.label else "")
    assert rows[1] == ["case-1", "doc-1", "invoice", "bill", "OTHER", "EUR", ".", "False", critical, "A | B",
                       "1", "2", "1", "True", "reviewed"]
    assert rows[2] == ["case-2", "", "other", "", "OTHER", "", ".", "False", "", "", "0", "0", "0", "False", ""]

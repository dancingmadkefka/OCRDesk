"""Tests for ocrgrade.roles (docs/grader-plan.md section 3)."""
from __future__ import annotations

from pathlib import Path

from ocrgrade.roles import RoleContext, load_role_map, looks_numeric, resolve_role


def _ctx(role_map=None, **kw):
    return RoleContext(role_map=role_map or load_role_map(None), **kw)


def test_class_map_total_value():
    ctx = _ctx()
    assert resolve_role("td", ["total-value"], "66.71", ctx) == "total_value"


def test_class_map_numeric_value_dict_shape():
    # numeric_value is written in roles.yaml as {classes: [...], align_expectation: right}
    ctx = _ctx()
    assert resolve_role("td", ["col-amount"], "12.00", ctx) == "numeric_value"


def test_class_map_section_header():
    ctx = _ctx()
    assert resolve_role("div", ["grey-bg"], "Company Name", ctx) == "section_header"


def test_class_priority_total_value_over_label():
    # A cell tagged both 'label' and 'total-value' should resolve to the
    # more specific total_value per _ROLE_ORDER.
    ctx = _ctx()
    assert resolve_role("td", ["label", "total-value"], "Total", ctx) == "total_value"


def test_th_tag_is_header():
    ctx = _ctx()
    assert resolve_role("th", [], "EUR", ctx) == "header"


def test_thead_first_row_context_is_header():
    ctx = _ctx(in_thead_first_row=True)
    assert resolve_role("td", [], "Description", ctx) == "header"


def test_total_keyword_with_numeric_text_is_total_value():
    ctx = _ctx()
    assert resolve_role("p", [], "Total: EUR66.71", ctx) == "total_value"


def test_total_keyword_alone_is_label():
    ctx = _ctx()
    assert resolve_role("td", [], "Total", ctx) == "label"


def test_betrag_keyword_recognized():
    # betrag is the Swiss QR-bill amount label (cases 006, 027 in the plan).
    ctx = _ctx()
    assert resolve_role("td", [], "Betrag", ctx) == "label"
    assert resolve_role("td", [], "Betrag: 42.00", ctx) == "total_value"


def test_numeric_text_without_keyword_is_numeric_value():
    ctx = _ctx()
    assert resolve_role("td", [], "59.99", ctx) == "numeric_value"


def test_plain_text_is_other():
    ctx = _ctx()
    assert resolve_role("td", [], "PLUM TOMATOES", ctx) == "other"


def test_no_classes_argument_does_not_crash():
    ctx = _ctx()
    assert resolve_role("td", None, "hello", ctx) == "other"


# --- looks_numeric -----------------------------------------------------------


def test_looks_numeric_amount():
    assert looks_numeric("59.99") is True


def test_looks_numeric_pure_digits():
    assert looks_numeric("6") is True
    assert looks_numeric("123") is True


def test_looks_numeric_rejects_words():
    assert looks_numeric("PLUM TOMATOES") is False
    assert looks_numeric("") is False


# --- load_role_map: package defaults + corpus override ----------------------


def test_load_role_map_package_defaults():
    role_map = load_role_map(None)
    assert role_map.class_to_role["final-val"] == "total_value"
    assert role_map.class_to_role["num"] == "numeric_value"
    assert role_map.class_to_role["grey-bg"] == "section_header"
    assert role_map.class_to_role["handwritten"] == "handwritten"
    assert role_map.class_to_role["header-cell"] == "header"
    assert role_map.class_to_role["spacer-row"] == "spacer"


def test_load_role_map_corpus_override_wins(tmp_path: Path):
    # A corpus roles.yaml re-mapping a package class name must win.
    (tmp_path / "roles.yaml").write_text(
        "label: [final-val]\n", encoding="utf-8"
    )
    role_map = load_role_map(tmp_path)
    assert role_map.class_to_role["final-val"] == "label"
    # Untouched package classes survive the merge.
    assert role_map.class_to_role["grey-bg"] == "section_header"


def test_load_role_map_missing_corpus_file_is_fine(tmp_path: Path):
    role_map = load_role_map(tmp_path)  # no roles.yaml written here
    assert role_map.class_to_role["final-val"] == "total_value"

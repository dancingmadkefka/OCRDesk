"""Structural validation of tests/grader/fixtures/* - file presence and
expected.json shape only. This does NOT run the grading pipeline: that is
`python -m ocrgrade fixtures-check`'s job, and it only passes once Scopes A and
B are integrated (docs/grader-plan.md Stage 2). See test_cli.py for coverage of
the `fixtures-check` command's own plumbing against fake stand-ins.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

EXPECTED_FIXTURE_IDS = {
    "we01_wrong_cell",
    "we02_wrong_digit",
    "we03_fragment_vs_doc",
    "we04_omission",
    "we05_dom_order",
    "we06_label_swap",
    "we07_duplicate",
    "we09_column_flip",
    "we10_vat_detached",
    "we_notable",
    "we_missing_row",
}

VALID_TIERS = {"CATASTROPHIC", "REJECT", "PASS"}
ALLOWED_EXPECTED_KEYS = {"tier", "assertions_failed", "structure_na", "min_display_score", "max_display_score"}


def _fixture_dirs() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir())


def test_all_eleven_fixtures_are_present():
    found = {p.name for p in _fixture_dirs()}
    assert found == EXPECTED_FIXTURE_IDS


@pytest.mark.parametrize("fixture_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_fixture_has_gt_and_exactly_one_hyp_file(fixture_dir):
    assert (fixture_dir / "gt.html").is_file(), f"{fixture_dir.name} is missing gt.html"
    has_html = (fixture_dir / "hyp.html").is_file()
    has_md = (fixture_dir / "hyp.md").is_file()
    assert has_html or has_md, f"{fixture_dir.name} is missing hyp.html/hyp.md"
    assert not (has_html and has_md), f"{fixture_dir.name} has both hyp.html and hyp.md"


@pytest.mark.parametrize("fixture_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_fixture_expected_json_is_well_formed(fixture_dir):
    expected_path = fixture_dir / "expected.json"
    assert expected_path.is_file(), f"{fixture_dir.name} is missing expected.json"
    data = json.loads(expected_path.read_text(encoding="utf-8"))

    assert "tier" in data, f"{fixture_dir.name}: expected.json missing 'tier'"
    assert data["tier"] in VALID_TIERS, f"{fixture_dir.name}: invalid tier {data['tier']!r}"

    assert "assertions_failed" in data, f"{fixture_dir.name}: expected.json missing 'assertions_failed'"
    assert isinstance(data["assertions_failed"], list)
    assert all(isinstance(a, str) for a in data["assertions_failed"])

    extra_keys = set(data.keys()) - ALLOWED_EXPECTED_KEYS
    assert not extra_keys, f"{fixture_dir.name}: expected.json has unexpected keys {extra_keys}"

    if "structure_na" in data:
        assert isinstance(data["structure_na"], bool)
    if "min_display_score" in data:
        assert isinstance(data["min_display_score"], (int, float))
    if "max_display_score" in data:
        assert isinstance(data["max_display_score"], (int, float))
    if "min_display_score" in data and "max_display_score" in data:
        assert data["min_display_score"] <= data["max_display_score"]


@pytest.mark.parametrize("fixture_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_fixture_sidecar_if_present_is_valid_and_matches_case_id(fixture_dir):
    sidecar_path = fixture_dir / "sidecar.meta.json"
    if not sidecar_path.is_file():
        pytest.skip(f"{fixture_dir.name} has no sidecar.meta.json (uses schema defaults)")
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert data.get("schema_version") == 1
    assert data.get("case_id") == fixture_dir.name
    for field_name in ("category", "locale", "decimal_sep", "required_sections", "critical_fields"):
        assert field_name in data, f"{fixture_dir.name}: sidecar.meta.json missing {field_name!r}"


@pytest.mark.parametrize("fixture_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_fixture_html_files_are_under_sixty_lines(fixture_dir):
    for name in ("gt.html", "hyp.html", "hyp.md"):
        path = fixture_dir / name
        if not path.is_file():
            continue
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        assert n_lines < 60, f"{fixture_dir.name}/{name} has {n_lines} lines (limit 60)"


def test_worked_examples_needing_a_grand_total_ship_a_sidecar_critical_field():
    """These worked examples are meaningless without a GT critical_field to check
    A1 against (a missing sidecar falls back to an empty, vacuously-passing
    default) - guard against silently losing that fixture data.
    """
    needs_critical_field = {
        "we01_wrong_cell",
        "we02_wrong_digit",
        "we06_label_swap",
        "we07_duplicate",
        "we_missing_row",
    }
    for fixture_id in needs_critical_field:
        sidecar_path = FIXTURES_DIR / fixture_id / "sidecar.meta.json"
        assert sidecar_path.is_file(), f"{fixture_id} needs a sidecar.meta.json with critical_fields"
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert data.get("critical_fields"), f"{fixture_id}: sidecar.meta.json has no critical_fields"

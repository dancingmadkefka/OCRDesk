"""Tests for ocrgrade.tables (docs/grader-plan.md sections 1, 3, 5)."""
from __future__ import annotations

from bs4 import BeautifulSoup

from ocrgrade.roles import load_role_map
from ocrgrade.tables import build_table, iter_dom_cells_with_colpos

ROLE_MAP = load_role_map(None)


def _parse_table(html: str):
    soup = BeautifulSoup(html, "html5lib")
    return soup.find("table")


def _grid(table):
    return {(c.row, c.col): c for c in table.cells}


# --- required dedicated case: rowspan + colspan expansion -------------------


def test_rowspan_and_colspan_expansion_produces_correct_grid():
    html = """
    <table>
      <tr><td rowspan="2" colspan="2">A</td><td>B</td></tr>
      <tr><td>C</td></tr>
    </table>
    """
    table_el = _parse_table(html)
    table = build_table(table_el, index=0, role_map=ROLE_MAP)

    assert table.n_rows == 2
    assert table.n_cols == 3
    grid = _grid(table)

    assert grid[(0, 0)].text_norm == "A"
    assert grid[(0, 0)].is_span_origin is True
    assert grid[(0, 1)].text_norm == "A"
    assert grid[(0, 1)].is_span_origin is False
    assert grid[(0, 2)].text_norm == "B"

    assert grid[(1, 0)].text_norm == "A"
    assert grid[(1, 0)].is_span_origin is False
    assert grid[(1, 1)].text_norm == "A"
    assert grid[(1, 1)].is_span_origin is False
    assert grid[(1, 2)].text_norm == "C"

    # Synthesized cells copy the origin's rowspan/colspan metadata.
    assert grid[(1, 1)].rowspan == 2
    assert grid[(1, 1)].colspan == 2


def test_rowspan_only_carries_down_correct_column():
    html = """
    <table>
      <tr><td rowspan="3">A</td><td>1</td></tr>
      <tr><td>2</td></tr>
      <tr><td>3</td></tr>
    </table>
    """
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert table.n_rows == 3
    assert table.n_cols == 2
    for r in range(3):
        assert grid[(r, 0)].text_norm == "A"
    assert grid[(0, 0)].is_span_origin is True
    assert grid[(1, 0)].is_span_origin is False
    assert grid[(2, 0)].is_span_origin is False
    assert [grid[(r, 1)].text_norm for r in range(3)] == ["1", "2", "3"]


def test_colspan_only_same_row():
    html = "<table><tr><td colspan=\"3\">Header</td></tr><tr><td>a</td><td>b</td><td>c</td></tr></table>"
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    assert table.n_cols == 3
    grid = _grid(table)
    assert grid[(0, 0)].text_norm == grid[(0, 1)].text_norm == grid[(0, 2)].text_norm == "Header"
    assert grid[(0, 0)].is_span_origin
    assert not grid[(0, 1)].is_span_origin and not grid[(0, 2)].is_span_origin


def test_multiple_independent_rowspans_do_not_clobber_each_other():
    """Regression case for the ordering bug where a later row's own real
    cell could overwrite an earlier row's not-yet-materialized carry-over.
    """
    html = """
    <table>
      <tr><td rowspan="2">A</td><td rowspan="2">B</td><td>1</td></tr>
      <tr><td>2</td></tr>
    </table>
    """
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert table.n_cols == 3
    assert grid[(1, 0)].text_norm == "A"
    assert grid[(1, 1)].text_norm == "B"
    assert grid[(1, 2)].text_norm == "2"


# --- outer_html exact serialization ------------------------------------------


def test_outer_html_is_exact_table_serialization():
    html = '<table class="x"><tr><td>only</td></tr></table>'
    table_el = _parse_table(html)
    table = build_table(table_el, index=0, role_map=ROLE_MAP)
    assert table.outer_html == str(table_el)
    assert table.outer_html.startswith("<table")
    assert table.outer_html.endswith("</table>")


# --- header detection: <th> and first row of <thead> -------------------------


def test_th_cells_are_header():
    html = "<table><tr><th>Col A</th><th>Col B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert grid[(0, 0)].is_header is True
    assert grid[(0, 1)].is_header is True
    assert grid[(1, 0)].is_header is False


def test_first_thead_row_is_header_even_with_td():
    html = """
    <table>
      <thead><tr><td>Description</td><td>Amount</td></tr></thead>
      <tbody><tr><td>Milk</td><td>2.00</td></tr></tbody>
    </table>
    """
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert grid[(0, 0)].is_header is True
    assert grid[(1, 0)].is_header is False


def test_implicit_tbody_is_inherited_from_html5lib():
    """"Implicit tbody" (docs/grader-plan.md section 1) needs no code here:
    html5lib's own HTML5 tree construction wraps bare <tr>s automatically.
    """
    table_el = _parse_table("<table><tr><td>a</td></tr></table>")
    assert table_el.find("tbody") is not None


# --- last-numeric-row fallback ------------------------------------------------


def test_last_numeric_row_promoted_to_total_value():
    html = """
    <table>
      <tr><td>Milk</td><td>2.00</td></tr>
      <tr><td>Oats</td><td>1.19</td></tr>
      <tr><td>Total</td><td>3.19</td></tr>
    </table>
    """
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert grid[(2, 1)].role == "total_value"
    # Earlier numeric cells (not the last non-empty row) are untouched.
    assert grid[(0, 1)].role == "numeric_value"
    assert grid[(1, 1)].role == "numeric_value"


def test_last_numeric_row_fallback_does_not_override_explicit_class():
    html = """
    <table>
      <tr><td>Milk</td><td class="col-amount">2.00</td></tr>
      <tr><td>Oats</td><td class="col-amount">1.19</td></tr>
      <tr><td>Total</td><td class="col-amount">3.19</td></tr>
    </table>
    """
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    # col-amount is an explicit class match; the heuristic must leave it as
    # numeric_value rather than silently promoting it to total_value.
    assert grid[(2, 1)].role == "numeric_value"


def test_last_numeric_row_fallback_noop_without_numeric_cell():
    html = "<table><tr><td>A</td></tr><tr><td>closing remarks</td></tr></table>"
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert grid[(1, 0)].role != "total_value"


# --- per-cell tokens carry the right cell_ref --------------------------------


def test_cell_tokens_have_correct_cell_ref():
    html = "<table><tr><td>Price</td><td>64.95 D</td></tr></table>"
    table = build_table(_parse_table(html), index=2, role_map=ROLE_MAP)
    grid = _grid(table)
    tokens = grid[(0, 1)].tokens
    assert len(tokens) == 2
    for tok in tokens:
        assert tok.cell_ref.table_index == 2
        assert tok.cell_ref.row == 0
        assert tok.cell_ref.col == 1


def test_synthesized_span_cell_tokens_have_own_cell_ref():
    html = '<table><tr><td rowspan="2">64.95 D</td><td>x</td></tr><tr><td>y</td></tr></table>'
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    origin_tokens = grid[(0, 0)].tokens
    span_tokens = grid[(1, 0)].tokens
    assert len(origin_tokens) == len(span_tokens) == 2
    assert origin_tokens[0].cell_ref.row == 0
    assert span_tokens[0].cell_ref.row == 1
    assert span_tokens[0].cell_ref.col == 0


# --- is_numeric flag agrees with roles.looks_numeric -------------------------


def test_is_numeric_flag_set_for_numeric_cell():
    html = "<table><tr><td>CHERRY PEPPERS</td><td>1.79</td></tr></table>"
    table = build_table(_parse_table(html), index=0, role_map=ROLE_MAP)
    grid = _grid(table)
    assert grid[(0, 0)].is_numeric is False
    assert grid[(0, 1)].is_numeric is True


# --- iter_dom_cells_with_colpos: same grid math, exposed for canonicalize ----


def test_iter_dom_cells_with_colpos_matches_build_table_grid():
    html = '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
    table_el = _parse_table(html)
    positions = iter_dom_cells_with_colpos(table_el)
    coords = {(row, col) for row, col, _el in positions}
    # Only origins are returned (2 real cells in row 0, 1 in row 1).
    assert coords == {(0, 0), (0, 1), (1, 1)}

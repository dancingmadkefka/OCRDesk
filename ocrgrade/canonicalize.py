"""Fragment/document canonicalization: the top-level `build_document` entry
point (docs/grader-plan.md sections 1, 3, and the data-side parts of 5).
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from . import fintoken
from .ir import CellRef, Document, FinToken, LabelValuePair, LineItem, Sidecar, Table
from .roles import RoleContext, RoleMap, looks_numeric, resolve_role
from .tables import build_table, extract_block_text, iter_dom_cells_with_colpos, normalize_text

# ---------------------------------------------------------------------------
# Stage-0 gates: run on the RAW string, before html5lib gets a chance to
# silently "fix" evidence of truncation by auto-closing tags at EOF.
# ---------------------------------------------------------------------------

_DOCTYPE_OR_HTML_RE = re.compile(r"^\s*(<!doctype\s+html[^>]*>\s*)?<html[\s>]", re.I)
_HTML_OPEN_RE = re.compile(r"<html[\s>]", re.I)
_HTML_CLOSE_RE = re.compile(r"</html\s*>", re.I)


def _detect_was_fragment(raw: str) -> bool:
    """False for a full document (optional doctype then a top-level <html>),
    True for a fragment (bare markup, e.g. an inline <style> + <div> body).
    """
    return not bool(_DOCTYPE_OR_HTML_RE.match(raw))


def _detect_truncated(raw: str) -> bool:
    """docs/grader-plan.md section 5: missing </html> after an opened <html>,
    or (for inputs over 200 chars) an unclosed <table>/<tr>/<td> at EOF.
    """
    if _HTML_OPEN_RE.search(raw) and not _HTML_CLOSE_RE.search(raw):
        return True
    if len(raw) > 200:
        for tag in ("table", "tr", "td"):
            opens = len(re.findall(rf"<{tag}[\s>]", raw, re.I))
            closes = len(re.findall(rf"</{tag}\s*>", raw, re.I))
            if opens > closes:
                return True
    return False


# ---------------------------------------------------------------------------
# Tag-equivalence folds
# ---------------------------------------------------------------------------

_TAG_FOLDS = {"b": "strong", "i": "em"}


def _fold_tag_equivalences(soup: BeautifulSoup) -> None:
    """Rename <b>->​<strong> and <i>->​<em> in place.

    Applied to the parsed tree *before* tables.build_table serializes
    outer_html, so a hypothesis's stylistic <b> vs the GT's <strong> never
    costs a spurious APTED rename in Scope B's TEDS comparison. "Implicit
    tbody" needs no code here: html5lib's own tree construction already
    wraps bare <tr>s in a <tbody> (verified against the installed html5lib;
    see tests/grader/test_canonicalize.py), so every consumer already sees
    a uniform structure.
    """
    for old_name, new_name in _TAG_FOLDS.items():
        for el in soup.find_all(old_name):
            el.name = new_name


# ---------------------------------------------------------------------------
# section_headers
# ---------------------------------------------------------------------------

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _section_headers(body: Tag, role_map: RoleMap) -> list[str]:
    headers: list[str] = []
    matched_ids: set[int] = set()
    ctx = RoleContext(role_map=role_map)
    for el in body.find_all(True):
        if any(id(anc) in matched_ids for anc in el.parents):
            continue  # already inside a collected section header
        classes = el.get("class") or []
        text = normalize_text(extract_block_text(el))
        if not text:
            continue
        if el.name in _HEADING_TAGS or resolve_role(el.name, classes, text, ctx) == "section_header":
            headers.append(text)
            matched_ids.add(id(el))
    return headers


# ---------------------------------------------------------------------------
# label_value_pairs
# ---------------------------------------------------------------------------

_COLON_LINE_RE = re.compile(r"^(?P<label>[^:\n]{1,60}):\s*(?P<value>\S.*)$")
_SHORT_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/_.]{0,19}$")
_TRAILING_DIGIT_RE = re.compile(r"\d$")

_EXCLUDED_CONTAINER_TAGS = ("table", "dl")


def _in_excluded_container(el: Tag) -> bool:
    return el.find_parent(_EXCLUDED_CONTAINER_TAGS) is not None


def _is_single_line(raw: str) -> bool:
    return "\n" not in raw.strip("\n")


def _value_shaped(text: str) -> bool:
    if not text:
        return False
    if looks_numeric(text):
        return True
    if any(
        tok.type in ("amount", "date", "percent", "iban", "masked_card", "reference_id")
        for tok in fintoken.extract_tokens(text)
    ):
        return True
    return bool(_SHORT_CODE_RE.fullmatch(text))


def _label_shaped(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith(":") or not bool(_TRAILING_DIGIT_RE.search(stripped))


def _table_row_pairs(table: Table) -> list[LabelValuePair]:
    pairs: list[LabelValuePair] = []
    rows: dict[int, list[Any]] = {}
    for cell in table.cells:
        rows.setdefault(cell.row, []).append(cell)
    for row_idx in sorted(rows):
        row_cells = sorted(rows[row_idx], key=lambda c: c.col)
        if not row_cells or all(c.is_header for c in row_cells):
            continue
        if table.n_cols == 2 and len(row_cells) >= 2:
            label_cell, value_cell = row_cells[0], row_cells[1]
            if label_cell.text_norm and value_cell.text_norm:
                pairs.append(LabelValuePair(
                    label=label_cell.text_norm, value=value_cell.text_norm,
                    source="table_row",
                    cell_ref=CellRef(table.index, row_idx, value_cell.col),
                ))
            continue
        for i in range(len(row_cells) - 1):
            if row_cells[i].role == "label" and row_cells[i + 1].text_norm:
                pairs.append(LabelValuePair(
                    label=row_cells[i].text_norm, value=row_cells[i + 1].text_norm,
                    source="table_row",
                    cell_ref=CellRef(table.index, row_idx, row_cells[i + 1].col),
                ))
    return pairs


def _definition_list_pairs(body: Tag) -> list[LabelValuePair]:
    pairs: list[LabelValuePair] = []
    for dl in body.find_all("dl"):
        current_label: str | None = None
        for child in dl.find_all(["dt", "dd"], recursive=False):
            if child.name == "dt":
                current_label = normalize_text(extract_block_text(child))
            elif child.name == "dd" and current_label:
                value = normalize_text(extract_block_text(child))
                if value:
                    pairs.append(LabelValuePair(
                        label=current_label, value=value,
                        source="definition_list", cell_ref=None,
                    ))
    return pairs


def _colon_pattern_pairs(body: Tag) -> list[LabelValuePair]:
    candidates = body.find_all(["p", "div", "li", "span"])
    raw_matches: list[tuple[Tag, str, str]] = []
    for el in candidates:
        if _in_excluded_container(el):
            continue
        raw = extract_block_text(el)
        if not _is_single_line(raw):
            continue
        norm = normalize_text(raw)
        match = _COLON_LINE_RE.match(norm)
        if not match:
            continue
        label, value = match.group("label").strip(), match.group("value").strip()
        if label and value:
            raw_matches.append((el, label, value))

    matched_ids = {id(el) for el, _, _ in raw_matches}
    pairs: list[LabelValuePair] = []
    for el, label, value in raw_matches:
        if any(id(desc) in matched_ids for desc in el.descendants if getattr(desc, "name", None)):
            continue  # a nested element matched the same line more precisely
        pairs.append(LabelValuePair(label=label, value=value, source="colon_pattern", cell_ref=None))
    return pairs


def _direct_child_tags(el: Tag) -> list[Tag]:
    return [c for c in el.children if isinstance(c, Tag)]


def _sibling_heuristic_pairs(body: Tag) -> list[LabelValuePair]:
    pairs: list[LabelValuePair] = []
    for parent in [body, *body.find_all(True)]:
        if parent.name in _EXCLUDED_CONTAINER_TAGS or _in_excluded_container(parent):
            continue
        kids = _direct_child_tags(parent)
        for i in range(len(kids) - 1):
            sib1, sib2 = kids[i], kids[i + 1]
            raw1, raw2 = extract_block_text(sib1), extract_block_text(sib2)
            if not _is_single_line(raw1) or not _is_single_line(raw2):
                continue
            norm1, norm2 = normalize_text(raw1), normalize_text(raw2)
            if not norm1 or not norm2:
                continue
            if _label_shaped(norm1) and _value_shaped(norm2):
                label = norm1[:-1].strip() if norm1.endswith(":") else norm1
                pairs.append(LabelValuePair(
                    label=label, value=norm2, source="sibling_heuristic", cell_ref=None,
                ))
    return pairs


# ---------------------------------------------------------------------------
# line_items
# ---------------------------------------------------------------------------

_COL_CLASS_RE = re.compile(r"^col-(.+)$")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower())
    return slug.strip("_")


def _row_is_header(table: Table, row: int) -> bool:
    row_cells = [c for c in table.cells if c.row == row and c.is_span_origin]
    return bool(row_cells) and all(c.is_header for c in row_cells)


def _line_item_field_names(table_el: Tag, table: Table) -> tuple[list[str], bool] | None:
    """Returns (field_names, header_row_present) or None if neither a
    col-* class family nor a header row is available to name fields from.
    """
    col_names: dict[int, str] = {}
    for _row, col, cell_el in iter_dom_cells_with_colpos(table_el):
        for cls in cell_el.get("class") or []:
            m = _COL_CLASS_RE.match(cls)
            if m:
                col_names.setdefault(col, m.group(1))
    if col_names:
        names = [col_names.get(c, f"col_{c}") for c in range(table.n_cols)]
        return names, _row_is_header(table, 0)

    # Header-row-text fallback: treat row 0 as the header for naming
    # purposes by position, not only when it is marked <th>/thead — real GT
    # cases style header rows with plain <td> too. Guarded on "no numeric
    # cell in row 0" so an actual all-data table with no header (e.g. a
    # header-less repeating 2-column table) is far less likely to be
    # mistaken for one; a known, accepted heuristic limitation either way.
    row0_cells = [c for c in table.cells if c.row == 0 and c.is_span_origin]
    if row0_cells and table.n_rows >= 2 and not any(c.is_numeric for c in row0_cells):
        names_by_col = {c.col: (_slugify(c.text_norm) or f"col_{c.col}") for c in row0_cells}
        names = [names_by_col.get(c, f"col_{c}") for c in range(table.n_cols)]
        return names, True

    return None


def _build_line_items(table_el: Tag, table: Table) -> list[LineItem]:
    resolved = _line_item_field_names(table_el, table)
    if resolved is None:
        return []
    field_names, has_header_row = resolved
    start_row = 1 if (has_header_row or _row_is_header(table, 0)) else 0

    cells_by_row: dict[int, dict[int, Any]] = {}
    for cell in table.cells:
        if cell.is_span_origin:
            cells_by_row.setdefault(cell.row, {})[cell.col] = cell

    data_rows = [
        r for r in range(start_row, table.n_rows)
        if any(c.text_norm for c in cells_by_row.get(r, {}).values())
    ]
    if len(data_rows) < 3:
        return []

    items: list[LineItem] = []
    for r in data_rows:
        row_cells = cells_by_row.get(r, {})
        fields = {
            name: row_cells[col].text_norm
            for col, name in enumerate(field_names)
            if col in row_cells
        }
        items.append(LineItem(table_index=table.index, row=r, fields=fields))
    return items


# ---------------------------------------------------------------------------
# fin_tokens
# ---------------------------------------------------------------------------


def _table_cell_tokens(tables_list: list[Table]) -> list[FinToken]:
    out: list[FinToken] = []
    for table in tables_list:
        for cell in table.cells:
            if cell.is_span_origin:
                out.extend(cell.tokens)
    return out


# ---------------------------------------------------------------------------
# build_document
# ---------------------------------------------------------------------------


def _empty_document(was_fragment: bool, truncated: bool, parse_error: str | None) -> Document:
    return Document(
        tables=[], section_headers=[], label_value_pairs=[], line_items=[],
        fin_tokens=[], body_text_norm="", parse_ok=False, parse_error=parse_error,
        was_fragment=was_fragment, truncated=truncated,
    )


def build_document(html: str, role_map: RoleMap, sidecar: Sidecar | None = None) -> Document:
    """Parse `html` (fragment or full document) into the shared IR `Document`.

    See docs/grader-plan.md sections 1, 3 and 5 for the exact contract; this
    function's steps are documented inline below in the order they run.
    """
    was_fragment = _detect_was_fragment(html)
    truncated = _detect_truncated(html)
    locale_hint = sidecar.decimal_sep if sidecar is not None else None

    try:
        soup = BeautifulSoup(html, "html5lib")
    except Exception as exc:  # html5lib is fragment-tolerant; defensive only
        return _empty_document(was_fragment, truncated, f"html5lib raised: {exc}")

    _fold_tag_equivalences(soup)

    body = soup.body
    if body is None:
        # Defensive: html5lib always synthesizes a <body>, but never trust
        # a parser unconditionally when the caller's own tier decision
        # (Stage-0 gate) depends on this.
        return _empty_document(was_fragment, truncated, "no <body> in parsed tree")

    body_text_norm = normalize_text(extract_block_text(body))

    if not body_text_norm and len(html) > 20:
        return _empty_document(
            was_fragment, truncated,
            "empty body text extracted from non-trivial input",
        )

    table_els = body.find_all("table")
    tables_list = [
        build_table(table_el, index=i, role_map=role_map, locale_hint=locale_hint)
        for i, table_el in enumerate(table_els)
    ]

    section_headers = _section_headers(body, role_map)

    label_value_pairs: list[LabelValuePair] = []
    for table in tables_list:
        label_value_pairs.extend(_table_row_pairs(table))
    label_value_pairs.extend(_definition_list_pairs(body))
    label_value_pairs.extend(_colon_pattern_pairs(body))
    label_value_pairs.extend(_sibling_heuristic_pairs(body))

    line_items: list[LineItem] = []
    for table_el, table in zip(table_els, tables_list):
        line_items.extend(_build_line_items(table_el, table))

    outside_table_text = extract_block_text(body, extra_skip=frozenset({"table"}))
    fin_tokens = _table_cell_tokens(tables_list) + fintoken.extract_tokens(
        outside_table_text, locale_hint
    )

    return Document(
        tables=tables_list,
        section_headers=section_headers,
        label_value_pairs=label_value_pairs,
        line_items=line_items,
        fin_tokens=fin_tokens,
        body_text_norm=body_text_norm,
        parse_ok=True,
        parse_error=None,
        was_fragment=was_fragment,
        truncated=truncated,
    )

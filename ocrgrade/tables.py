"""Table grid expansion, plus the text-normalization helpers shared with
canonicalize.py (docs/grader-plan.md sections 1 and 5).

`normalize_text` (NFKC fold + typographic-quote/dash fold + whitespace
collapse) and the skip/block tag sets it depends on are copied, with this
attribution, from:
    C:\\Users\\daniel\\VSCodeProjects\\AI Financial Advisor\\backend\\benchmark\\harness\\normalize.py
The traversal itself is reimplemented against an already-parsed
BeautifulSoup/html5lib tree (`extract_block_text`) instead of that file's
stdlib HTMLParser, since canonicalize.py parses with html5lib per the plan.
Both helpers live here (rather than in canonicalize.py) so tables.py, which
sits lower in this package's import graph, can build `Cell.text_raw` /
`Cell.text_norm` without importing canonicalize.py and creating a cycle;
canonicalize.py imports them back from here.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from bs4 import Comment, NavigableString, ProcessingInstruction, Tag

from .fintoken import extract_tokens, with_cell_ref
from .ir import Cell, CellRef, Table
from .roles import RoleContext, RoleMap, looks_numeric, resolve_role

# --- copied from AIFA normalize.py (see module docstring) ------------------

_SKIP_CONTENT_TAGS = {"script", "style", "head", "title", "noscript", "template"}

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "button", "caption",
    "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "label",
    "li", "main", "nav", "ol", "option", "p", "pre", "section", "select",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}

_CHAR_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u2026": "...",
}


def normalize_text(text: str) -> str:
    """NFKC fold, ASCII punctuation folds, collapsed whitespace.

    Case is preserved. Copied verbatim from AIFA's normalize_text (see
    module docstring); collapsing whitespace this way is intentional and
    safe here because it always runs on a *complete* extracted string, never
    on individual streaming tokens (which would lose word boundaries).
    """
    text = unicodedata.normalize("NFKC", text)
    for src, dst in _CHAR_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return " ".join(text.split())

# --- reimplemented against a parsed bs4 tree --------------------------------


def extract_block_text(node: Any, extra_skip: frozenset[str] = frozenset()) -> str:
    """Visible text of `node`'s subtree, script/style/head dropped, with a
    boundary "\\n" emitted on both the open and close of every block tag so
    callers can tell "same line" from "different block" (needed by
    fintoken's VAT-letter adjacency check and by canonicalize's colon/sibling
    heuristics). Not yet NFKC-folded or whitespace-collapsed; pass through
    normalize_text() for that.

    `extra_skip` adds tag names to skip entirely (content and all), e.g.
    canonicalize.py passes {"table"} to build the "outside any table" text
    used for document-wide fin_tokens without double-extracting cell text.
    """
    skip_tags = _SKIP_CONTENT_TAGS | extra_skip
    parts: list[str] = []

    def walk(n: Any) -> None:
        name = getattr(n, "name", None)
        if name is not None:
            if name in skip_tags:
                return
            is_block = name in _BLOCK_TAGS
            if is_block:
                parts.append("\n")
            for child in n.children:
                walk(child)
            if is_block:
                parts.append("\n")
        elif isinstance(n, NavigableString):
            if not isinstance(n, (Comment, ProcessingInstruction)):
                parts.append(str(n))

    walk(node)
    return "".join(parts)


def _header_row_ids(table_el: Tag) -> set[int]:
    """id() of every <tr> that is the first row of a <thead>."""
    ids: set[int] = set()
    for thead in table_el.find_all("thead", recursive=False):
        first_tr = thead.find("tr", recursive=False)
        if first_tr is not None:
            ids.add(id(first_tr))
    return ids


def _cell_classes(cell_el: Tag) -> list[str]:
    classes = cell_el.get("class") or []
    return list(classes)


def _iter_dom_rows(table_el: Tag) -> list[Tag]:
    return table_el.find_all("tr", recursive=True)


def build_table(
    table_el: Tag, index: int, role_map: RoleMap, *, locale_hint: str | None = None
) -> Table:
    """Expand `table_el` into an occupied grid (rowspan/colspan resolved).

    Synthesized cells (the non-top-left slots of a spanning cell) carry
    `is_span_origin=False` and copy the origin's text/role/rowspan/colspan;
    only their own `row`/`col`/token `cell_ref` differ from the origin.

    `locale_hint` is optional and keyword-only so the documented 3-argument
    call in docs/grader-plan.md section 1 still works unchanged; it is
    forwarded to fintoken.extract_tokens for a future decimal-separator
    tie-break (see fintoken.extract_tokens's docstring).
    """
    rows = _iter_dom_rows(table_el)
    header_row_ids = _header_row_ids(table_el)

    grid: dict[tuple[int, int], Cell] = {}
    # Whether an *origin* cell's role came from an explicit roles.yaml class
    # match, keyed by its (row, col). Cell carries no `classes` field, so
    # the last-numeric-row fallback (which must not override a class's
    # decision) needs this recorded separately at build time.
    class_matched: dict[tuple[int, int], bool] = {}
    # column -> [remaining_rows_to_fill, origin cell info] for rowspans that
    # carry down from an earlier row.
    carry: dict[int, list[Any]] = {}

    for row_idx, tr in enumerate(rows):
        # Materialize rowspans carried down from earlier rows *before*
        # placing this row's own cells, so the occupancy check below
        # correctly skips columns a prior row's rowspan already claims.
        for (c, origin_cell, o_rowspan, o_colspan) in carry.pop(row_idx, []):
            grid[(row_idx, c)] = Cell(
                table_index=index, row=row_idx, col=c,
                rowspan=o_rowspan, colspan=o_colspan, is_span_origin=False,
                text_raw=origin_cell.text_raw, text_norm=origin_cell.text_norm,
                role=origin_cell.role, is_numeric=origin_cell.is_numeric,
                is_header=origin_cell.is_header,
                tokens=with_cell_ref(list(origin_cell.tokens), CellRef(index, row_idx, c)),
            )

        in_thead_first_row = id(tr) in header_row_ids
        col_cursor = 0
        dom_cells = tr.find_all(["td", "th"], recursive=False)
        for cell_el in dom_cells:
            while (row_idx, col_cursor) in grid:
                col_cursor += 1
            rowspan = _positive_int(cell_el.get("rowspan"), default=1)
            colspan = _positive_int(cell_el.get("colspan"), default=1)

            tag = cell_el.name
            classes = _cell_classes(cell_el)
            class_matched[(row_idx, col_cursor)] = any(
                cls in role_map.class_to_role for cls in classes
            )
            raw = extract_block_text(cell_el)
            norm = normalize_text(raw)
            is_header = tag == "th" or in_thead_first_row
            role = resolve_role(
                tag, classes, norm,
                RoleContext(role_map=role_map, in_thead_first_row=in_thead_first_row),
            )
            is_numeric = looks_numeric(norm)
            base_tokens = extract_tokens(raw, locale_hint)

            origin_cell = Cell(
                table_index=index, row=row_idx, col=col_cursor,
                rowspan=rowspan, colspan=colspan, is_span_origin=True,
                text_raw=raw, text_norm=norm, role=role,
                is_numeric=is_numeric, is_header=is_header,
                tokens=with_cell_ref(base_tokens, CellRef(index, row_idx, col_cursor)),
            )
            grid[(row_idx, col_cursor)] = origin_cell

            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr == 0 and dc == 0:
                        continue
                    r, c = row_idx + dr, col_cursor + dc
                    if dr == 0:
                        # Same row: fill immediately, no need to carry.
                        grid[(r, c)] = Cell(
                            table_index=index, row=r, col=c,
                            rowspan=rowspan, colspan=colspan, is_span_origin=False,
                            text_raw=raw, text_norm=norm, role=role,
                            is_numeric=is_numeric, is_header=is_header,
                            tokens=with_cell_ref(base_tokens, CellRef(index, r, c)),
                        )
                    else:
                        carry.setdefault(r, []).append((c, origin_cell, rowspan, colspan))

            col_cursor += colspan

    n_rows = len(rows)
    n_cols = (max((c for (_r, c) in grid.keys()), default=-1)) + 1

    _apply_last_numeric_row_fallback(grid, n_rows, n_cols, class_matched, index)

    cells = [grid[key] for key in sorted(grid.keys())]
    outer_html = str(table_el)
    return Table(index=index, n_rows=n_rows, n_cols=n_cols, cells=cells, outer_html=outer_html)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _apply_last_numeric_row_fallback(
    grid: dict[tuple[int, int], Cell],
    n_rows: int,
    n_cols: int,
    class_matched: dict[tuple[int, int], bool],
    table_index: int,
) -> None:
    """Last row with any non-empty cell, if it has a numeric cell, becomes a
    total_value candidate for cells whose role came from the content-
    heuristic backstop rather than an explicit class or keyword match
    (docs/grader-plan.md section 3, "last-numeric-row").
    """
    last_nonempty_row = -1
    for r in range(n_rows):
        if any(grid.get((r, c)) and grid[(r, c)].text_norm for c in range(n_cols)):
            last_nonempty_row = r
    if last_nonempty_row < 0:
        return

    row_cells = [grid.get((last_nonempty_row, c)) for c in range(n_cols)]
    if not any(c is not None and c.is_numeric for c in row_cells):
        return

    for c in range(n_cols):
        cell = grid.get((last_nonempty_row, c))
        if cell is None or not cell.is_span_origin or not cell.is_numeric:
            continue
        if cell.role != "numeric_value":
            continue  # keyword heuristic already produced something more specific
        if class_matched.get((cell.row, cell.col), False):
            continue  # an explicit roles.yaml class already said numeric_value
        promoted = Cell(
            table_index=table_index, row=cell.row, col=cell.col,
            rowspan=cell.rowspan, colspan=cell.colspan, is_span_origin=True,
            text_raw=cell.text_raw, text_norm=cell.text_norm, role="total_value",
            is_numeric=cell.is_numeric, is_header=cell.is_header, tokens=cell.tokens,
        )
        grid[(last_nonempty_row, c)] = promoted
        for r2 in range(cell.row, cell.row + cell.rowspan):
            for c2 in range(cell.col, cell.col + cell.colspan):
                if (r2, c2) == (cell.row, cell.col):
                    continue
                span_copy = grid.get((r2, c2))
                if span_copy is not None and not span_copy.is_span_origin:
                    grid[(r2, c2)] = Cell(
                        table_index=table_index, row=r2, col=c2,
                        rowspan=span_copy.rowspan, colspan=span_copy.colspan,
                        is_span_origin=False, text_raw=span_copy.text_raw,
                        text_norm=span_copy.text_norm, role="total_value",
                        is_numeric=span_copy.is_numeric, is_header=span_copy.is_header,
                        tokens=span_copy.tokens,
                    )


def iter_dom_cells_with_colpos(table_el: Tag) -> list[tuple[int, int, Tag]]:
    """(row, col, dom cell element) for every *origin* cell, grid-expanded
    the same way build_table expands rowspan/colspan. Used by
    canonicalize.py to read `col-*` classes straight off the DOM for
    line-item field naming — that information never reaches the Cell IR,
    which deliberately carries no `classes` field.
    """
    rows = _iter_dom_rows(table_el)
    occupied: set[tuple[int, int]] = set()
    carry: dict[int, list[tuple[int, int]]] = {}
    result: list[tuple[int, int, Tag]] = []

    for row_idx, tr in enumerate(rows):
        for (c, span_rows) in carry.pop(row_idx, []):
            occupied.add((row_idx, c))
            del span_rows
        col_cursor = 0
        for cell_el in tr.find_all(["td", "th"], recursive=False):
            while (row_idx, col_cursor) in occupied:
                col_cursor += 1
            rowspan = _positive_int(cell_el.get("rowspan"), default=1)
            colspan = _positive_int(cell_el.get("colspan"), default=1)
            result.append((row_idx, col_cursor, cell_el))
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr == 0 and dc == 0:
                        continue
                    r, c = row_idx + dr, col_cursor + dc
                    if dr == 0:
                        occupied.add((r, c))
                    else:
                        carry.setdefault(r, []).append((c, rowspan))
            col_cursor += colspan
    return result

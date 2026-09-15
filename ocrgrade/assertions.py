"""The 5 critical (A1-A5) and 5 non-critical (A6-A10) assertions.

Pure functions over already-built `Document`/`Sidecar` objects; see
docs/grader-plan.md section 4 for the exact rule text this implements.
"""
from __future__ import annotations

import difflib

import re
from collections import Counter

from ocrgrade.ir import AssertionResult, Cell, Document, FinToken, LineItem, Sidecar, Table

# Same regex given verbatim in docs/grader-plan.md section 3 ("total keyword"
# content heuristic); A2 reuses it directly rather than depending on Scope
# A's roles.py.
_TOTAL_KEYWORD_RE = re.compile(
    r"\btotal\b|\bgesamt\b|\bsumme\b|\bsaldo\b|\bbetrag\b|\bnet pay\b|\bbalance\b|\bamount due\b",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _squash(s: str) -> str:
    """casefold and drop ALL whitespace: value comparisons must not care about '2.50A' vs '2.50 A'."""
    return "".join(s.split()).casefold()


def _headers_or_text_order(gt_headers: list[str], hyp_headers: list[str], hyp_body_text: str) -> list[str]:
    """Hypothesis heading sequence for comparison against GT.

    Models do not reproduce the GT's class conventions, so when the hypothesis resolved no
    section headers the sequence is derived from where each GT heading text occurs in the
    hypothesis body text (document order); headings absent from the text are dropped.
    """
    if hyp_headers:
        return [_norm(h) for h in hyp_headers]
    body = _squash(hyp_body_text)
    found: list[tuple[int, str]] = []
    for h in gt_headers:
        key = _squash(h)
        pos = body.find(key) if key else -1
        if pos >= 0:
            found.append((pos, _norm(h)))
    return [h for _, h in sorted(found)]


def _values_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a in b or b in a


# --- A1: value_in_aligned_cell -------------------------------------------------


def _hyp_cell_lookup(hyp: Document) -> dict[tuple[int, int, int], Cell]:
    lookup: dict[tuple[int, int, int], Cell] = {}
    for table in hyp.tables:
        for cell in table.cells:
            lookup[(table.index, cell.row, cell.col)] = cell
    return lookup


def _cents_of(value: str) -> int | None:
    """Cents of a critical-field value when it is a single amount, else None."""
    from ocrgrade.fintoken import extract_tokens

    amounts = [t for t in extract_tokens(value) if t.type == "amount" and t.cents is not None]
    return amounts[0].cents if len(amounts) == 1 else None


def _cell_amount_tokens(cell: Cell) -> list[FinToken]:
    """Amount tokens belonging to a cell: its own pre-extracted `tokens` (the production path,
    stamped by tables.py) plus a fresh parse of its text. The fresh parse means a cell built
    without pre-extracted tokens -- any hand-built `Cell`, as in this module's unit tests --
    is covered the same way a production one is, rather than silently matching nothing."""
    from ocrgrade.fintoken import extract_tokens

    pre = [t for t in cell.tokens if t.type == "amount" and t.cents is not None]
    live = [t for t in extract_tokens(cell.text_norm) if t.type == "amount" and t.cents is not None]
    return pre + live


def _value_in_cell(value: str, cell: Cell) -> bool:
    """True when the critical value is present in the cell.

    An amount-valued value (anything `_cents_of` can parse as a single amount) must match one
    of the cell's own amount tokens by cents, or by canonical text -- never by raw substring,
    so a wrong total such as '112.34' or '-12.34' can no longer satisfy a '12.34' critical
    value merely because one string contains the other; sign matters, so '-12.34' does not
    match '12.34' either. Locale formatting is still accepted as the same amount ('3637.00'
    matches '3,637.00', "1'932.24" and '3637,00' alike, since those parse to identical cents --
    that is normalization, not a placement error). A non-amount value (a reference id, a date,
    a free-text label) still matches as whitespace-insensitive text or as a token canonical.
    """
    v = _squash(value)
    if not v:
        return False
    cents = _cents_of(value)
    if cents is not None:
        return any(
            t.cents == cents or (t.canonical and _squash(t.canonical) == v)
            for t in _cell_amount_tokens(cell)
        )
    if v in _squash(cell.text_norm):
        return True
    return any(_squash(t.canonical) == v for t in cell.tokens if t.canonical)


def _row_origin_cells(table: Table, row: int) -> list[Cell]:
    return [c for c in table.cells if c.row == row and c.is_span_origin]


def _row_label(table: Table, row: int, exclude_col: int | None) -> str:
    """Label of a table row: its shortest non-numeric cell (so a marketing paragraph sharing the
    row never becomes the label), truncated to twelve words. When the row carries no label,
    walk up to three rows for the nearest labelled row (statement blocks)."""
    for r in range(row, max(-1, row - 4), -1):
        candidates = [
            c.text_norm.strip() for c in _row_origin_cells(table, r)
            if c.text_norm.strip() and not c.is_numeric and not (r == row and c.col == exclude_col)
        ]
        if candidates:
            best = min(candidates, key=lambda t: (len(t.split()), len(t)))
            return " ".join(best.split()[:12])
    return ""


def _col_header(table: Table, col: int) -> str:
    """Text of the per-column header above `col`, or '' when the table has no real header row
    (a first row that is a single spanning title, or that holds numbers, does not count)."""
    first = [c for c in table.cells if c.row == 0]
    origins = [c for c in first if c.is_span_origin and c.text_norm.strip()]
    if len(origins) < 2 or any(c.is_numeric for c in origins) or any(c.colspan > 1 for c in origins):
        return ""
    for c in first:
        if c.col == col and c.is_span_origin:
            return c.text_norm
    return ""


_LABEL_TOKEN_RE = re.compile(r"[0-9a-z]+")


def _label_tokens(text: str) -> list[str]:
    toks = _LABEL_TOKEN_RE.findall(text.casefold())
    out: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] == "sub" and i + 1 < len(toks) and toks[i + 1] == "total":
            out.append("subtotal")  # 'Sub-total' and 'sub total' mean 'Subtotal'
            i += 2
            continue
        out.append(toks[i])
        i += 1
    return out


_SENSE_QUALIFIERS = frozenset({
    "opening", "closing", "previous", "new", "gross", "net", "sub", "subtotal", "vat", "tax",
    "minimum", "min", "interest", "credit", "debit", "brought", "carried", "forward", "discount",
})


def _label_matches(gt_label: str, hyp_text: str) -> bool:
    """Do two row labels mean the same thing?

    Word-level on purpose: 'Closing Balance' vs 'Opening Balance' and 'Total' vs 'Subtotal'
    are different financial meanings that character-level fuzziness would blur, so the
    sense-changing qualifiers present on either side must agree exactly. Beyond that, every
    GT word must appear in the hypothesis (a word of five or more characters tolerates a small
    OCR slip, ratio >= 0.85); labels longer than four words match on 60% of their words, and two
    labels that both name a total match each other ('Total due' vs 'Total bill amount to be
    taken from your bank a/c')."""
    g_toks = _label_tokens(gt_label)
    if not g_toks:
        return True
    h_toks = _label_tokens(hyp_text)
    if not h_toks:
        return False
    if (_SENSE_QUALIFIERS & set(g_toks)) != (_SENSE_QUALIFIERS & set(h_toks)):
        return False
    if _TOTAL_KEYWORD_RE.search(gt_label) and _TOTAL_KEYWORD_RE.search(hyp_text):
        return True

    def word_ok(g: str) -> bool:
        if g in h_toks:
            return True
        if len(g) >= 5:
            return any(len(h) >= 5 and difflib.SequenceMatcher(None, g, h).ratio() >= 0.85 for h in h_toks)
        return False

    hits = sum(1 for g in g_toks if word_ok(g))
    if len(g_toks) > 4:
        return hits >= 0.6 * len(g_toks)
    return hits == len(g_toks)


def _value_in_labelled_row(value: str, gt_label: str, gt_col_header: str, hyp: Document) -> tuple[int, str]:
    """How many hypothesis rows hold the value beside a label matching the GT row label (and,
    when both sides have column headers, under a matching header)? Returns (count, note)."""
    seen_labels: list[str] = []
    count = 0
    for table in hyp.tables:
        rows = sorted({c.row for c in table.cells})
        for r in rows:
            cells = _row_origin_cells(table, r)
            hits = [c for c in cells if _value_in_cell(value, c)]
            if not hits:
                continue
            row_label = " ".join(c.text_norm for c in cells if c.text_norm.strip() and not c.is_numeric)
            if not _label_matches(gt_label, row_label):
                # a value cell may carry its own label text ("Total 66.71"); check the cell text too
                if not any(_label_matches(gt_label, c.text_norm) for c in hits):
                    seen_labels.append(row_label or "(no label)")
                    continue
            if gt_col_header:
                headers = [_col_header(table, c.col) for c in hits]
                if any(headers) and not any(_label_matches(gt_col_header, h) for h in headers if h):
                    seen_labels.append(f"column {headers}")
                    continue
            count += 1
    if count:
        return count, ""
    return 0, f"value present only in rows labelled {seen_labels[:3]}" if seen_labels else "value not found in any hyp table row"



def _value_near_label_in_text(value: str, gt_label: str, body_text_norm: str) -> bool:
    """Last resort for totals that a model kept as prose ("<p>Net Pay <span>1650.40</span></p>"):
    some occurrence of the label is followed within 80 characters by an amount with the same
    cents as the value. The label is matched on the words at that point of the text (as many as
    the GT label has, plus one, stopping before the first number) so trailing text can neither
    help nor hurt the match."""
    from ocrgrade.fintoken import extract_tokens

    body = body_text_norm.casefold()
    g_toks = _label_tokens(gt_label)
    if not g_toks or not body:
        return False
    cents = _cents_of(value)
    if cents is None:
        return False
    n_raw = len(_LABEL_TOKEN_RE.findall(gt_label.casefold()))
    for m in re.finditer(r"(?<!\w)" + re.escape(g_toks[0]), body):
        pos = m.start()
        window = body[pos: pos + 120]
        words = []
        for w in _LABEL_TOKEN_RE.finditer(window):
            if w.group(0).isdigit() or len(words) > n_raw:
                break
            words.append(w)
        if not words:
            continue
        candidate = " ".join(w.group(0) for w in words)
        if not _label_matches(gt_label, candidate):
            continue
        label_end = pos + words[-1].end()
        following = body_text_norm[label_end: label_end + 80]
        amounts = [t for t in extract_tokens(following) if t.type == "amount" and t.cents is not None]
        if amounts and amounts[0].cents == cents:
            return True
    return False


def _count_value_occurrences(value: str, hyp: Document) -> int:
    """Occurrences of the value in a document: amount tokens (table cells and prose alike) with
    the same cents. A1 applies it to the GT and the hypothesis alike."""
    cents = _cents_of(value)
    if cents is None:
        v = _squash(value)
        return sum(1 for t in hyp.tables for c in t.cells if c.is_span_origin and v and v in _squash(c.text_norm))
    return sum(1 for t in hyp.fin_tokens if t.type == "amount" and t.cents == cents)


def _usable_label(gt_label: str) -> str:
    """A paragraph is not a usable label: a total-ish one degrades to 'total' (any total-ish row
    or pair in the hypothesis then counts), any other to no label at all."""
    if len(_label_tokens(gt_label)) > 8:
        return "total" if _TOTAL_KEYWORD_RE.search(gt_label) else ""
    return gt_label


def _value_beside_label_outside_tables(value: str, gt_label: str, hyp: Document) -> bool:
    """The value sits beside its label outside any table: as a label-value line ('Total: 66.71')
    or as prose ('<p>Net Pay <span>1650.40</span></p>'). Shared by A1 and A2."""
    if not gt_label:
        return False
    from ocrgrade.fintoken import extract_tokens

    cents = _cents_of(value)
    for pair in hyp.label_value_pairs:
        if not _label_matches(gt_label, pair.label):
            continue
        if cents is None:
            if _squash(value) in _squash(pair.value):
                return True
        elif any(t.type == "amount" and t.cents == cents for t in extract_tokens(pair.value)):
            return True  # an amount is matched as a token: 12.34 is not inside 112.34 or -12.34
    return _value_near_label_in_text(value, gt_label, hyp.body_text_norm)


def _check_a1(gt: Document, hyp: Document, sidecar: Sidecar) -> AssertionResult:
    """Critical values must sit in a hypothesis row (or label-value pair) whose label matches the
    GT label in meaning, and under a matching column header when both tables have one. Exact
    grid coordinates are accepted as a fast path; they are not required, because models split
    and merge tables freely. Fields without a cell_ref come from totals outside tables. A value
    the GT repeats must occur at least that often in the hypothesis, so dropping one of two
    printed totals cannot pass the gate. Both sides are counted here with the same counter;
    the sidecar's expected_multiplicity is written for the reviewer and never read by score,
    so a sidecar annotated by an older tokenizer cannot skew the gate."""
    fields = list(sidecar.critical_fields)
    if not fields:
        return AssertionResult("A1", True, True, "no critical fields to check")

    lookup = _hyp_cell_lookup(hyp)
    failures: list[str] = []
    multiplicity_checked: set[str] = set()
    for cf in fields:
        ref = cf.cell_ref
        gt_label, gt_header = cf.label, ""
        aligned = False
        if ref is not None:
            key = (ref.table_index, ref.row, ref.col)
            hyp_cell = lookup.get(key)
            if hyp_cell is not None and _value_in_cell(cf.value, hyp_cell):
                aligned = True
            if ref.table_index < len(gt.tables):
                gt_table = gt.tables[ref.table_index]
                gt_label = gt_label or _row_label(gt_table, ref.row, ref.col)
                gt_header = _col_header(gt_table, ref.col)
        gt_label = _usable_label(gt_label)
        note = ""
        if not aligned:
            count, note = _value_in_labelled_row(cf.value, gt_label, gt_header, hyp)
            aligned = count > 0
        if not aligned:
            aligned = _value_beside_label_outside_tables(cf.value, gt_label, hyp)
        if not aligned and not gt_label:
            # a critical value that carries no label anywhere (a bare 'CHF 32.40' on a terminal receipt)
            # can only be required to be present; the multiplicity check below still applies
            aligned = _count_value_occurrences(cf.value, hyp) > 0
            note = "value not found anywhere in the hypothesis"
        if not aligned:
            failures.append(f"critical field {cf.role}={cf.value!r} (GT label {gt_label!r}): {note or 'not found beside its label'}")
            continue
        if cf.value in multiplicity_checked:
            continue
        multiplicity_checked.add(cf.value)
        expected = _count_value_occurrences(cf.value, gt)
        if expected > 1:
            occurrences = _count_value_occurrences(cf.value, hyp)
            if occurrences < expected:
                failures.append(
                    f"critical field {cf.role}={cf.value!r} appears {occurrences}x in hyp, GT has it {expected}x"
                )
    passed = not failures
    detail = "all critical fields aligned" if passed else "; ".join(failures)
    return AssertionResult("A1", True, passed, detail)


# --- A2: total_in_totals_row ----------------------------------------------------


def _last_numeric_row(table) -> int | None:
    """Last row with >=1 numeric cell and no non-empty rows after it."""
    rows_sorted = sorted({c.row for c in table.cells})
    numeric_rows = [r for r in rows_sorted if any(c.is_numeric for c in table.cells if c.row == r)]
    if not numeric_rows:
        return None
    candidate = numeric_rows[-1]
    trailing_nonempty = any(c.text_norm.strip() for c in table.cells if c.row > candidate)
    return candidate if not trailing_nonempty else None


def _cell_match_values(cell: Cell) -> list[str]:
    values = [cell.text_norm]
    values.extend(tok.canonical for tok in cell.tokens if tok.canonical)
    return [v for v in values if v.strip()]


def _row_has_value(value: str, row_cells: list[Cell], row_text: str) -> bool:
    """Does `value` occur in this hyp row? An amount-valued value must match one of the row's
    cell amount tokens by cents -- never by raw substring, so a GT total of '12.34' cannot be
    satisfied by a row that merely contains '112.34'. A non-amount value still matches as
    whitespace-insensitive substring text."""
    cents = _cents_of(value)
    if cents is not None:
        return any(t.cents == cents for c in row_cells for t in _cell_amount_tokens(c))
    return _norm(value) in _norm(row_text)


def _check_a2(gt: Document, hyp: Document) -> AssertionResult:
    total_cells = [
        c for t in gt.tables for c in t.cells
        if c.role == "total_value" and c.is_span_origin and c.text_norm.strip()
    ]
    if not total_cells:
        return AssertionResult("A2", True, True, "no GT total_value cells to check")

    failures: list[str] = []
    for cell in total_cells:
        target_values = _cell_match_values(cell)
        found = False
        for h_table in hyp.tables:
            last_num_row = _last_numeric_row(h_table)
            rows = sorted({c.row for c in h_table.cells})
            for r in rows:
                row_cells = [c for c in h_table.cells if c.row == r]
                row_text = " ".join(c.text_norm for c in row_cells)
                if not any(_row_has_value(v, row_cells, row_text) for v in target_values):
                    continue
                if _TOTAL_KEYWORD_RE.search(row_text) or r == last_num_row:
                    found = True
                    break
            if found:
                break
        if not found and cell.table_index < len(gt.tables):
            # Models merge tables and keep summaries as prose, so a GT total row that carries no
            # total keyword rarely lands in a hyp *totals* row. Accept it where A1 would: in a hyp
            # row whose label matches the GT row label, or beside that label outside any table.
            gt_table = gt.tables[cell.table_index]
            gt_label = _usable_label(_row_label(gt_table, cell.row, cell.col))
            value = next((t.canonical for t in cell.tokens if t.type == "amount" and t.canonical), cell.text_norm)
            found = (
                _value_in_labelled_row(value, gt_label, _col_header(gt_table, cell.col), hyp)[0] > 0
                or _value_beside_label_outside_tables(value, gt_label, hyp)
            )
        if not found:
            failures.append(
                f"GT total value {cell.text_norm!r} (table {cell.table_index} row {cell.row}) "
                "not found in any hyp totals row nor beside its label"
            )
    passed = not failures
    detail = "all GT totals matched in a hyp totals row" if passed else "; ".join(failures)
    return AssertionResult("A2", True, passed, detail)


# --- A3: no_duplicated_financial_value ------------------------------------------


def _amount_cents_counts(doc: Document) -> Counter:
    counts: Counter = Counter()
    for tok in doc.fin_tokens:
        if tok.type == "amount" and tok.cents is not None:
            counts[tok.cents] += 1
    return counts


def _check_a3(gt: Document, hyp: Document) -> AssertionResult:
    gt_counts = _amount_cents_counts(gt)
    hyp_counts = _amount_cents_counts(hyp)
    failures = [
        f"amount {v / 100:.2f} appears {hyp_counts.get(v, 0)}x in hyp vs {gt_c}x in GT"
        for v, gt_c in gt_counts.items()
        if hyp_counts.get(v, 0) > gt_c
    ]
    passed = not failures
    detail = "no financial value exceeds its GT multiplicity" if passed else "; ".join(failures)
    return AssertionResult("A3", True, passed, detail)


# --- A4: required_sections_present ----------------------------------------------


def _check_a4(sidecar: Sidecar, hyp: Document) -> AssertionResult:
    required = sidecar.required_sections
    if not required:
        return AssertionResult("A4", True, True, "no required sections in sidecar")

    # A hypothesis rarely carries the GT's section classes, so presence is checked against
    # resolved headers first and then against the whole hypothesis text (whitespace-insensitive).
    hyp_headers_sq = [_squash(h) for h in hyp.section_headers]
    body_sq = _squash(hyp.body_text_norm)
    missing = [
        req for req in required
        if not (any(_squash(req) in h for h in hyp_headers_sq) or (_squash(req) and _squash(req) in body_sq))
    ]
    passed = not missing
    detail = "all required sections present" if passed else f"missing sections: {missing}"
    return AssertionResult("A4", True, passed, detail)


# --- A5: vat_letter_attached -----------------------------------------------------


def _check_a5(gt: Document, hyp: Document) -> AssertionResult:
    gt_vat_tokens = [t for t in gt.fin_tokens if t.type == "vat_letter" and t.attached]
    if not gt_vat_tokens:
        return AssertionResult("A5", True, True, "no attached GT VAT-letter tokens to check")

    hyp_vat_by_cents: dict[int, list[FinToken]] = {}
    for t in hyp.fin_tokens:
        if t.type == "vat_letter" and t.cents is not None:
            hyp_vat_by_cents.setdefault(t.cents, []).append(t)

    # Adjacency compares against the GT token's own `attached` flag, not a
    # re-derived adjacency pattern -- the no-space/space/block distinction is
    # collapsed into that boolean upstream, by fintoken.py (Scope A). The
    # letter itself must also match: it carries the VAT rate, so "59.99 D"
    # reproduced as "59.99 E" (same cents, same adjacency) is a real content
    # error, not a mere adjacency question.
    #
    # GT tokens are matched against hyp tokens as a multiset on (cents, letter,
    # attached): each matched hyp token is removed from its cents bucket so it
    # cannot also satisfy a second, repeated GT occurrence. Two GT "59.99 A"
    # tokens with only one attached "A" in the hypothesis therefore leave the
    # second GT token with nothing left to match, and it fails like any other
    # missing occurrence.
    failures: list[str] = []
    for gt_tok in gt_vat_tokens:
        candidates = hyp_vat_by_cents.get(gt_tok.cents, []) if gt_tok.cents is not None else []
        amount = f"{(gt_tok.cents or 0) / 100:.2f}"
        adjacency_matches = [h for h in candidates if h.attached == gt_tok.attached]
        exact = next((h for h in adjacency_matches if h.vat_letter == gt_tok.vat_letter), None)
        if exact is not None:
            candidates.remove(exact)  # consume: a further identical GT occurrence can't reuse it
            continue
        wrong_letter = next((h for h in adjacency_matches if h.vat_letter != gt_tok.vat_letter), None)
        if wrong_letter is not None:
            failures.append(
                f"VAT letter mismatch at amount {amount}: GT letter {gt_tok.vat_letter!r} "
                f"vs hyp letter {wrong_letter.vat_letter!r}"
            )
        else:
            failures.append(
                f"VAT letter {gt_tok.vat_letter!r} attached to amount {amount}: "
                f"no hyp token with attached={gt_tok.attached}"
            )
    passed = not failures
    detail = "all attached VAT letters reproduced" if passed else "; ".join(failures)
    return AssertionResult("A5", True, passed, detail)


# --- A6: label_value_grouping_intact ---------------------------------------------


def _pair_matches_lv(needle, haystack) -> bool:
    needle_value = _norm(needle.value)
    needle_label = _norm(needle.label)
    if not needle_value:
        return False
    return any(
        _values_match(_squash(needle.value), _squash(h.value)) and _norm(h.label) == needle_label
        for h in haystack
    )


def _check_a6(gt: Document, hyp: Document) -> AssertionResult:
    gt_pairs = gt.label_value_pairs
    if not gt_pairs:
        return AssertionResult("A6", False, True, "no GT label-value pairs to check")

    failures = []
    used: set[int] = set()  # each hypothesis pair reproduces one GT pair at most
    for gp in gt_pairs:
        match = next(
            (i for i, hp in enumerate(hyp.label_value_pairs) if i not in used and _pair_matches_lv(gp, [hp])),
            None,
        )
        if match is None:
            failures.append(f"label-value pair {gp.label!r} -> {gp.value!r} not reproduced in hyp")
        else:
            used.add(match)
    passed = not failures
    detail = "all label-value pairs intact" if passed else "; ".join(failures)
    return AssertionResult("A6", False, passed, detail)


# --- A7: table_count_delta -------------------------------------------------------


def _check_a7(gt: Document, hyp: Document) -> AssertionResult:
    gt_n, hyp_n = len(gt.tables), len(hyp.tables)
    passed = gt_n == hyp_n
    return AssertionResult("A7", False, passed, f"gt tables={gt_n}, hyp tables={hyp_n}")


# --- A8: line_item_intact ---------------------------------------------------------


def _line_item_hit(item: LineItem, hyp: Document, claimed_rows: dict[int, set[int]]) -> bool:
    """True when an unclaimed hyp row in `item`'s table holds every field value; that row's
    (table, row) is then added to `claimed_rows` so a repeated GT row cannot also match it.

    Without consuming rows this way, two identical GT line items would both independently find
    (and reuse) the same single hyp row when the hypothesis dropped one of the repeats, hiding
    the omission."""
    if item.table_index >= len(hyp.tables):
        return False
    hyp_table = hyp.tables[item.table_index]
    values = [_norm(v) for v in item.fields.values() if _norm(v)]
    if not values:
        return False
    claimed = claimed_rows.setdefault(item.table_index, set())
    for r in sorted({c.row for c in hyp_table.cells}):
        if r in claimed:
            continue
        row_texts = [_norm(c.text_norm) for c in hyp_table.cells if c.row == r]
        if all(any(v in text for text in row_texts) for v in values):
            claimed.add(r)
            return True
    return False


def _check_a8(gt: Document, hyp: Document) -> AssertionResult:
    items = gt.line_items
    if not items:
        return AssertionResult("A8", False, True, "no GT line items to check")

    claimed_rows: dict[int, set[int]] = {}
    failures = [
        f"line item at table {it.table_index} row {it.row} {it.fields} not intact in hyp"
        for it in items
        if not _line_item_hit(it, hyp, claimed_rows)
    ]
    passed = not failures
    detail = "all line items intact" if passed else "; ".join(failures)
    return AssertionResult("A8", False, passed, detail)


# --- A9: heading_sequence_match ----------------------------------------------------


def _check_a9(gt: Document, hyp: Document) -> AssertionResult:
    if not gt.section_headers:
        return AssertionResult("A9", False, True, "no GT section headers to check")

    gt_norm = [_norm(h) for h in gt.section_headers]
    hyp_norm = _headers_or_text_order(gt.section_headers, hyp.section_headers, hyp.body_text_norm)
    passed = gt_norm == hyp_norm
    detail = (
        "heading sequence matches"
        if passed
        else f"gt headings {gt.section_headers} != hyp headings {hyp.section_headers}"
    )
    return AssertionResult("A9", False, passed, detail)


# --- A10: no_hallucinated_amount -----------------------------------------------------


def _check_a10(gt: Document, hyp: Document) -> AssertionResult:
    gt_cents = {t.cents for t in gt.fin_tokens if t.type == "amount" and t.cents is not None}
    hyp_cents = {t.cents for t in hyp.fin_tokens if t.type == "amount" and t.cents is not None}
    spurious = sorted(hyp_cents - gt_cents)
    passed = not spurious
    detail = (
        "no hallucinated amounts"
        if passed
        else f"hyp amounts not in GT: {[f'{c / 100:.2f}' for c in spurious]}"
    )
    return AssertionResult("A10", False, passed, detail)


def run_assertions(gt: Document, hyp: Document, sidecar: Sidecar) -> list[AssertionResult]:
    return [
        _check_a1(gt, hyp, sidecar),
        _check_a2(gt, hyp),
        _check_a3(gt, hyp),
        _check_a4(sidecar, hyp),
        _check_a5(gt, hyp),
        _check_a6(gt, hyp),
        _check_a7(gt, hyp),
        _check_a8(gt, hyp),
        _check_a9(gt, hyp),
        _check_a10(gt, hyp),
    ]

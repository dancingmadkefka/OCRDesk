"""The 5 critical (A1-A5) and 5 non-critical (A6-A10) assertions.

Pure functions over already-built `Document`/`Sidecar` objects; see
docs/grader-plan.md section 4 for the exact rule text this implements.
"""
from __future__ import annotations

import re
from collections import Counter

from ocrgrade.ir import AssertionResult, Cell, Document, FinToken, LineItem, Sidecar

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


def _value_in_cell(value: str, cell: Cell) -> bool:
    """True when the critical value is present in the cell: as whitespace-insensitive text,
    as a token canonical, or as the same amount in cents (so '3637.00' matches '3,637.00',
    "1'932.24" and '3637,00' alike). Locale formatting is never a placement error."""
    v = _squash(value)
    if not v:
        return False
    if v in _squash(cell.text_norm):
        return True
    if any(_squash(t.canonical) == v for t in cell.tokens if t.canonical):
        return True
    cents = _cents_of(value)
    return cents is not None and any(t.type == "amount" and t.cents == cents for t in cell.tokens)


def _check_a1(gt: Document, hyp: Document, sidecar: Sidecar) -> AssertionResult:
    fields_with_ref = [cf for cf in sidecar.critical_fields if cf.cell_ref is not None]
    if not fields_with_ref:
        return AssertionResult("A1", True, True, "no critical fields with cell_ref to check")

    lookup = _hyp_cell_lookup(hyp)
    failures: list[str] = []
    for cf in fields_with_ref:
        ref = cf.cell_ref
        assert ref is not None
        key = (ref.table_index, ref.row, ref.col)
        hyp_cell = lookup.get(key)
        if hyp_cell is None:
            failures.append(f"critical field {cf.role}={cf.value!r}: no hyp cell at {key}")
        elif not _value_in_cell(cf.value, hyp_cell):
            failures.append(
                f"critical field {cf.role}={cf.value!r}: hyp cell at {key} has {hyp_cell.text_norm!r}"
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
                if not any(_norm(v) in _norm(row_text) for v in target_values):
                    continue
                if _TOTAL_KEYWORD_RE.search(row_text) or r == last_num_row:
                    found = True
                    break
            if found:
                break
        if not found:
            failures.append(
                f"GT total value {cell.text_norm!r} (table {cell.table_index} row {cell.row}) "
                "not found in any hyp totals row"
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
    failures: list[str] = []
    for gt_tok in gt_vat_tokens:
        candidates = hyp_vat_by_cents.get(gt_tok.cents, []) if gt_tok.cents is not None else []
        amount = f"{(gt_tok.cents or 0) / 100:.2f}"
        adjacency_matches = [h for h in candidates if h.attached == gt_tok.attached]
        if any(h.vat_letter == gt_tok.vat_letter for h in adjacency_matches):
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

    failures = [
        f"label-value pair {gp.label!r} -> {gp.value!r} not reproduced in hyp"
        for gp in gt_pairs
        if not _pair_matches_lv(gp, hyp.label_value_pairs)
    ]
    passed = not failures
    detail = "all label-value pairs intact" if passed else "; ".join(failures)
    return AssertionResult("A6", False, passed, detail)


# --- A7: table_count_delta -------------------------------------------------------


def _check_a7(gt: Document, hyp: Document) -> AssertionResult:
    gt_n, hyp_n = len(gt.tables), len(hyp.tables)
    passed = gt_n == hyp_n
    return AssertionResult("A7", False, passed, f"gt tables={gt_n}, hyp tables={hyp_n}")


# --- A8: line_item_intact ---------------------------------------------------------


def _line_item_hit(item: LineItem, hyp: Document) -> bool:
    if item.table_index >= len(hyp.tables):
        return False
    hyp_table = hyp.tables[item.table_index]
    values = [_norm(v) for v in item.fields.values() if _norm(v)]
    if not values:
        return False
    rows = {c.row for c in hyp_table.cells}
    for r in rows:
        row_texts = [_norm(c.text_norm) for c in hyp_table.cells if c.row == r]
        if all(any(v in text for text in row_texts) for v in values):
            return True
    return False


def _check_a8(gt: Document, hyp: Document) -> AssertionResult:
    items = gt.line_items
    if not items:
        return AssertionResult("A8", False, True, "no GT line items to check")

    failures = [
        f"line item at table {it.table_index} row {it.row} {it.fields} not intact in hyp"
        for it in items
        if not _line_item_hit(it, hyp)
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

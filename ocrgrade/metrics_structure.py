"""Structure-layer metrics: grid-aligned cell F1, label-value F1, line-item
F1, heading sequence, and packaged TEDS via `teds_adapter`.

Structural alignment is grid-position alignment after rowspan/colspan
expansion (docs/grader-plan.md section 4): GT table t and hyp table t' are
paired by document-order index up to `min(n)`; a GT cell aligns to the hyp
cell at identical (row, col) in the matched table, or to nothing.
"""
from __future__ import annotations

import difflib
from collections import Counter

from ocrgrade import teds_adapter
from ocrgrade.ir import Cell, Document, LabelValuePair, LineItem, Table

EPS = 1e-9


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


def _cell_token_f1(gt_text: str, hyp_text: str) -> float:
    gt_tokens = gt_text.split()
    hyp_tokens = hyp_text.split()
    if not gt_tokens and not hyp_tokens:
        return 1.0
    gt_counts = Counter(gt_tokens)
    hyp_counts = Counter(hyp_tokens)
    overlap = sum(min(c, hyp_counts.get(tok, 0)) for tok, c in gt_counts.items())
    precision = overlap / max(1, sum(hyp_counts.values()))
    recall = overlap / max(1, sum(gt_counts.values()))
    return (2 * precision * recall) / max(EPS, precision + recall)


def _cell_content_f1(gt_tables: list[Table], hyp_tables: list[Table]) -> float | None:
    """Row-aligned cell content F1: for every non-empty GT row, the best token-F1 of its cell
    text against any hypothesis row (any table). Robust to tables being split or merged; a
    row whose content moved elsewhere still scores by its own text. None when GT has no rows."""
    def rows_of(tables: list[Table]) -> list[str]:
        out: list[str] = []
        for t in tables:
            for r in sorted({c.row for c in t.cells}):
                text = _norm(" ".join(c.text_norm for c in t.cells if c.row == r and c.is_span_origin))
                if text:
                    out.append(text)
        return out

    gt_rows = rows_of(gt_tables)
    if not gt_rows:
        return None
    hyp_rows = rows_of(hyp_tables)
    if not hyp_rows:
        return 0.0
    return sum(max(_cell_token_f1(g, h) for h in hyp_rows) for g in gt_rows) / len(gt_rows)


def _values_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a in b or b in a


def _pair_matches(needle: LabelValuePair, haystack: list[LabelValuePair]) -> bool:
    needle_value = _norm(needle.value)
    needle_label = _norm(needle.label)
    if not needle_value:
        return False
    return any(
        _values_match(_squash(needle.value), _squash(h.value)) and _norm(h.label) == needle_label
        for h in haystack
    )


def _label_value_f1(
    gt_pairs: list[LabelValuePair], hyp_pairs: list[LabelValuePair]
) -> float | None:
    """F1 over GT pairs vs hyp pairs; a GT pair counts when its value appears
    in hyp and the nearest hyp label normalizes equal (docs/grader-plan.md
    section 4, A6)."""
    if not gt_pairs and not hyp_pairs:
        return None
    recall_hits = sum(1 for gp in gt_pairs if _pair_matches(gp, hyp_pairs))
    precision_hits = sum(1 for hp in hyp_pairs if _pair_matches(hp, gt_pairs))
    recall = recall_hits / max(1, len(gt_pairs))
    precision = precision_hits / max(1, len(hyp_pairs))
    return (2 * precision * recall) / max(EPS, precision + recall)




def _line_item_f1(gt: Document, hyp: Document) -> float | None:
    """Fraction of GT line-item tuples reproduced intact in one hyp row.

    Each hyp row is claimed by at most one GT item (consumed on match), so when the GT
    contains two identical line-item rows and the hypothesis retains only one, only the first
    GT item can claim it; the second finds every row already claimed or non-matching and
    counts as a miss instead of reusing the same row a second time.
    """
    gt_items = gt.line_items
    if not gt_items:
        return None
    claimed_rows: dict[int, set[int]] = {}
    hits = 0
    for item in gt_items:
        if item.table_index >= len(hyp.tables):
            continue
        hyp_table = hyp.tables[item.table_index]
        values = [_norm(v) for v in item.fields.values() if _norm(v)]
        if not values:
            continue
        claimed = claimed_rows.setdefault(item.table_index, set())
        for r in sorted({c.row for c in hyp_table.cells}):
            if r in claimed:
                continue
            row_texts = [_norm(c.text_norm) for c in hyp_table.cells if c.row == r]
            if all(any(v in text for text in row_texts) for v in values):
                claimed.add(r)
                hits += 1
                break
    return hits / max(1, len(gt_items))


def _heading_sequence_score(gt_headers: list[str], hyp_headers: list[str], hyp_body_text: str = "") -> float | None:
    if not gt_headers:
        return None
    gt_norm = [_norm(h) for h in gt_headers]
    hyp_norm = _headers_or_text_order(gt_headers, hyp_headers, hyp_body_text)
    return difflib.SequenceMatcher(a=gt_norm, b=hyp_norm).ratio()


def structure_metrics(gt: Document, hyp: Document) -> dict:
    table_count_gt = len(gt.tables)
    table_count_hyp = len(hyp.tables)
    structure_na = table_count_gt == 0

    cc_f1 = _cell_content_f1(gt.tables, hyp.tables)
    lv_f1 = _label_value_f1(gt.label_value_pairs, hyp.label_value_pairs)
    li_f1 = _line_item_f1(gt, hyp)
    hs_score = _heading_sequence_score(gt.section_headers, hyp.section_headers, hyp.body_text_norm)
    teds_result = teds_adapter.teds_scores(gt.tables, hyp.tables)

    if not structure_na:
        # default: Structure = mean(TEDS_struct, cell_content_F1, label_value_F1, line_item_F1)
        # teds_result.teds_struct is guaranteed non-None here since table_count_gt > 0.
        components = [c for c in (teds_result.teds_struct, cc_f1, lv_f1, li_f1) if c is not None]
        structure_score = sum(components) / len(components) if components else None
    elif lv_f1 is not None:
        # no tables in GT: drop TEDS_struct/cell_content_F1/line_item_F1 (never score as 0)
        structure_score = lv_f1
    elif hs_score is not None:
        structure_score = hs_score
    else:
        # scoring.py falls back to Q = 0.9*Content + 0.1*ReadingOrder in this case
        structure_score = None

    return {
        "cell_content_f1": cc_f1,
        "label_value_f1": lv_f1,
        "line_item_f1": li_f1,
        "heading_sequence_score": hs_score,
        "teds": teds_result.teds,
        "teds_struct": teds_result.teds_struct,
        "teds_timed_out": teds_result.timed_out,
        "structure_na": structure_na,
        "structure_score": structure_score,
        "table_count_gt": table_count_gt,
        "table_count_hyp": table_count_hyp,
    }

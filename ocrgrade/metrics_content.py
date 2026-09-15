"""Content-layer metrics: CER/WER, multiset content-F1, reading order, FIN-EM.

See docs/grader-plan.md section 5 for the exact formulas implemented here.
"""
from __future__ import annotations

from collections import Counter

from rapidfuzz.distance import Levenshtein

from ocrgrade.ir import Document, FinToken

EPS = 1e-9

# Maps the fin_em output key (as named in docs/grader-plan.md) to the
# FinTokenType literal it reads from `Document.fin_tokens` (ir.py has no
# "id" type; "id" is FIN-EM's short name for `reference_id`). "masked_card"
# is intentionally not FIN-EM-scored per the plan's enumerated type list.
FIN_EM_TYPE_MAP: dict[str, str] = {
    "amount": "amount",
    "date": "date",
    "id": "reference_id",
    "percent": "percent",
    "iban": "iban",
    "vat_letter": "vat_letter",
}

# weights for FIN_EM_ids_dates_pct = wmean(FIN_EM(date)*2, FIN_EM(id)*2, FIN_EM(percent)*3)
IDS_DATES_PCT_WEIGHTS: dict[str, float] = {"date": 2, "id": 2, "percent": 3}

# Content = wmean(FIN_EM_amounts*5, FIN_EM_ids_dates_pct*3, (1-CER)*1, content_f1*3)
CONTENT_WEIGHTS: dict[str, float] = {
    "fin_em_amounts": 5,
    "fin_em_ids_dates_pct": 3,
    "cer_complement": 1,
    "content_f1": 3,
}


def _words(text: str) -> list[str]:
    return text.split()


def _mergesort_inversions(seq: list[int]) -> int:
    """Count inversions in `seq` via mergesort (no scipy dependency)."""
    if len(seq) <= 1:
        return 0
    mid = len(seq) // 2
    left = seq[:mid]
    right = seq[mid:]
    inversions = _mergesort_inversions(left) + _mergesort_inversions(right)
    merged: list[int] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
            inversions += len(left) - i
    merged.extend(left[i:])
    merged.extend(right[j:])
    seq[:] = merged
    return inversions


def _reading_order(words_gt: list[str], words_hyp: list[str]) -> tuple[float, bool]:
    """Kendall tau-a on first-occurrence ranks of tokens common to both sides."""
    first_rank_gt: dict[str, int] = {}
    for idx, w in enumerate(words_gt):
        first_rank_gt.setdefault(w, idx)
    first_rank_hyp: dict[str, int] = {}
    for idx, w in enumerate(words_hyp):
        first_rank_hyp.setdefault(w, idx)

    common = set(first_rank_gt) & set(first_rank_hyp)
    n = len(common)
    if n < 2:
        return 1.0, True

    ordered_by_gt = sorted(common, key=lambda w: first_rank_gt[w])
    hyp_rank_seq = [first_rank_hyp[w] for w in ordered_by_gt]
    inversions = _mergesort_inversions(hyp_rank_seq)
    tau = 1 - (4 * inversions) / (n * (n - 1))
    return (tau + 1) / 2, False


def _fin_values(tokens: list[FinToken], fin_type: str) -> set:
    if fin_type == "amount":
        return {(t.cents, t.currency) for t in tokens if t.type == fin_type and t.cents is not None}
    return {t.canonical for t in tokens if t.type == fin_type and t.canonical is not None}


def _amounts_compatible(a: tuple[int, str | None], b: tuple[int, str | None]) -> bool:
    """Are two (cents, currency) amounts the same financial fact?

    Cents must agree exactly. Currency must not contradict: a hypothesis that drops the
    currency symbol is not an amount error (either side may be `None`), but one that states a
    different currency -- turning EUR 12.34 into GBP 12.34 -- is, even though the cents match.
    """
    a_cents, a_currency = a
    b_cents, b_currency = b
    if a_cents != b_cents:
        return False
    return a_currency is None or b_currency is None or a_currency == b_currency


def _fin_em(gt_tokens: list[FinToken], hyp_tokens: list[FinToken], fin_type: str) -> tuple[float, bool]:
    """Returns `(score, na)`. A type with zero GT tokens is vacuously true
    ("zero amount errors"): score 1.0, na=True -- not 0.0."""
    gt_set = _fin_values(gt_tokens, fin_type)
    if not gt_set:
        return 1.0, True
    hyp_set = _fin_values(hyp_tokens, fin_type)
    if fin_type == "amount":
        hits = sum(1 for g in gt_set if any(_amounts_compatible(g, h) for h in hyp_set))
    else:
        hits = len(gt_set & hyp_set)
    return hits / len(gt_set), False


def content_metrics(gt: Document, hyp: Document) -> dict:
    chars_gt = gt.body_text_norm
    chars_hyp = hyp.body_text_norm
    cer = Levenshtein.distance(chars_gt, chars_hyp) / max(1, len(chars_gt))

    words_gt = _words(gt.body_text_norm)
    words_hyp = _words(hyp.body_text_norm)
    wer = Levenshtein.distance(words_gt, words_hyp) / max(1, len(words_gt))

    gt_counts = Counter(words_gt)
    hyp_counts = Counter(words_hyp)
    overlap = sum(min(c, hyp_counts.get(tok, 0)) for tok, c in gt_counts.items())
    sum_gt = max(1, sum(gt_counts.values()))
    sum_hyp = max(1, sum(hyp_counts.values()))
    precision = overlap / sum_hyp
    recall = overlap / sum_gt
    content_f1 = (2 * precision * recall) / max(EPS, precision + recall)
    omission_rate = 1 - recall
    hallucination_rate = 1 - precision

    reading_order_score, reading_order_na = _reading_order(words_gt, words_hyp)

    fin_em: dict[str, float] = {}
    fin_em_na: dict[str, bool] = {}
    for key, ftype in FIN_EM_TYPE_MAP.items():
        value, na = _fin_em(gt.fin_tokens, hyp.fin_tokens, ftype)
        fin_em[key] = value
        fin_em_na[key] = na

    fin_em_amounts = fin_em["amount"]
    fin_em_amounts_na = fin_em_na["amount"]

    ids_dates_pct_types = ("date", "id", "percent")
    ids_dates_pct_weight_sum = sum(IDS_DATES_PCT_WEIGHTS.values())
    fin_em_ids_dates_pct = (
        sum(fin_em[k] * w for k, w in IDS_DATES_PCT_WEIGHTS.items()) / ids_dates_pct_weight_sum
    )
    # The whole ids/dates/percent term is n/a for content_score's weighting
    # only when every one of its inputs is individually n/a (GT has no date,
    # id, or percent token at all); a partial mix still yields a meaningful
    # (and, per the vacuous-truth fix above, ungenerous-to-neither-side)
    # weighted mean at its normal weight.
    fin_em_ids_dates_pct_na = all(fin_em_na[t] for t in ids_dates_pct_types)

    # cer_financial_tokens: literal char-level CER over the concatenated
    # canonical values in each document's own order (order-sensitive by
    # design -- unlike FIN-EM, which is a set comparison).
    gt_fin_str = "".join((t.canonical or "") for t in gt.fin_tokens)
    hyp_fin_str = "".join((t.canonical or "") for t in hyp.fin_tokens)
    cer_financial_tokens = Levenshtein.distance(gt_fin_str, hyp_fin_str) / max(1, len(gt_fin_str))

    # Same (cents, currency) key and the same compatibility rule as the FIN-EM amount
    # comparison above, so a currency-corrupted amount ('EUR 12.34' hypothesised as 'GBP
    # 12.34') is flagged as spurious here too, not just missed by fin_em_amounts.
    gt_amounts = _fin_values(gt.fin_tokens, "amount")
    hyp_amounts = _fin_values(hyp.fin_tokens, "amount")
    spurious_amounts = sum(1 for h in hyp_amounts if not any(_amounts_compatible(h, g) for g in gt_amounts))

    # content_score's weighted mean drops n/a terms from its weights instead
    # of crediting them at full weight: a letter with no amounts is scored
    # on ids/dates/CER/content_F1 only (fin_em_amounts' weight-5 term is
    # dropped, not included at a free 1.0), and if every FIN-EM component is
    # n/a, Content = wmean((1-CER)*1, content_F1*3). CER-complement and
    # content_f1 are always defined (body text is never n/a), so this sum is
    # never empty.
    content_terms = [
        (fin_em_amounts, CONTENT_WEIGHTS["fin_em_amounts"], fin_em_amounts_na),
        (fin_em_ids_dates_pct, CONTENT_WEIGHTS["fin_em_ids_dates_pct"], fin_em_ids_dates_pct_na),
        (max(0.0, 1 - cer), CONTENT_WEIGHTS["cer_complement"], False),  # CER_sim = max(0, 1-CER)
        (content_f1, CONTENT_WEIGHTS["content_f1"], False),
    ]
    active_terms = [(value, weight) for value, weight, na in content_terms if not na]
    active_weight_sum = sum(weight for _, weight in active_terms)
    content_score = sum(value * weight for value, weight in active_terms) / active_weight_sum

    return {
        "cer": cer,
        "wer": wer,
        "content_f1": content_f1,
        "omission_rate": omission_rate,
        "hallucination_rate": hallucination_rate,
        "reading_order_score": reading_order_score,
        "reading_order_na": reading_order_na,
        "fin_em": fin_em,
        "fin_em_na": fin_em_na,
        "fin_em_amounts": fin_em_amounts,
        "fin_em_ids_dates_pct": fin_em_ids_dates_pct,
        "cer_financial_tokens": cer_financial_tokens,
        "spurious_amounts": spurious_amounts,
        "content_score": content_score,
    }

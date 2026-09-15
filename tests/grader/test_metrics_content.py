"""Tests for ocrgrade.metrics_content.

Hand-builds `Document`/`FinToken` IR objects directly (Scope B contract: no
import of Scope A's canonicalize/fintoken modules).
"""
from __future__ import annotations

import pytest

from ocrgrade.ir import Document, FinToken
from ocrgrade.metrics_content import content_metrics


# --- private test builders ---------------------------------------------------------


def make_doc(
    body_text_norm: str = "",
    fin_tokens: list[FinToken] | None = None,
) -> Document:
    return Document(
        tables=[],
        section_headers=[],
        label_value_pairs=[],
        line_items=[],
        fin_tokens=fin_tokens or [],
        body_text_norm=body_text_norm,
        parse_ok=True,
        parse_error=None,
        was_fragment=False,
        truncated=False,
    )


def make_fin_token(
    type: str = "amount",
    raw: str = "",
    canonical: str | None = None,
    cents: int | None = None,
    currency: str | None = None,
    vat_letter: str | None = None,
    attached: bool = False,
) -> FinToken:
    return FinToken(
        type=type,  # type: ignore[arg-type]
        raw=raw,
        canonical=canonical,
        cents=cents,
        currency=currency,
        vat_letter=vat_letter,
        attached=attached,
        cell_ref=None,
    )


# --- CER / WER -----------------------------------------------------------------------


def test_cer_wer_identical_text_are_zero():
    gt = make_doc(body_text_norm="hello world")
    hyp = make_doc(body_text_norm="hello world")
    m = content_metrics(gt, hyp)
    assert m["cer"] == 0.0
    assert m["wer"] == 0.0


def test_cer_wer_one_character_substitution():
    gt = make_doc(body_text_norm="abc")
    hyp = make_doc(body_text_norm="abd")
    m = content_metrics(gt, hyp)
    assert m["cer"] == pytest.approx(1 / 3)


def test_wer_one_word_substitution():
    gt = make_doc(body_text_norm="a b c")
    hyp = make_doc(body_text_norm="a b d")
    m = content_metrics(gt, hyp)
    assert m["wer"] == pytest.approx(1 / 3)


def test_cer_denominator_guards_empty_gt_text():
    gt = make_doc(body_text_norm="")
    hyp = make_doc(body_text_norm="anything")
    m = content_metrics(gt, hyp)
    assert m["cer"] == len("anything") / 1  # max(1, 0) denominator


# --- content_f1 / omission / hallucination --------------------------------------------


def test_content_f1_partial_overlap():
    gt = make_doc(body_text_norm="a b c")
    hyp = make_doc(body_text_norm="a b d")
    m = content_metrics(gt, hyp)
    # overlap=2 (a,b), P=2/3, R=2/3, F1=2/3
    assert m["content_f1"] == pytest.approx(2 / 3)
    assert m["omission_rate"] == pytest.approx(1 / 3)
    assert m["hallucination_rate"] == pytest.approx(1 / 3)


def test_content_f1_perfect_match_is_one():
    gt = make_doc(body_text_norm="x y z")
    hyp = make_doc(body_text_norm="x y z")
    m = content_metrics(gt, hyp)
    assert m["content_f1"] == pytest.approx(1.0)
    assert m["omission_rate"] == pytest.approx(0.0)
    assert m["hallucination_rate"] == pytest.approx(0.0)


def test_content_f1_respects_multiset_counts_not_just_set_membership():
    # gt has "a" once, hyp has "a" three times: overlap must clip at gt's count.
    gt = make_doc(body_text_norm="a b")
    hyp = make_doc(body_text_norm="a a a b")
    m = content_metrics(gt, hyp)
    # overlap = min(1,3) + min(1,1) = 2; P=2/4=0.5; R=2/2=1.0
    precision = 0.5
    recall = 1.0
    expected_f1 = (2 * precision * recall) / (precision + recall)
    assert m["content_f1"] == pytest.approx(expected_f1)
    assert m["hallucination_rate"] == pytest.approx(1 - precision)
    assert m["omission_rate"] == pytest.approx(1 - recall)


# --- reading order ---------------------------------------------------------------------


def test_reading_order_identical_order_is_perfect():
    gt = make_doc(body_text_norm="alpha beta gamma delta")
    hyp = make_doc(body_text_norm="alpha beta gamma delta")
    m = content_metrics(gt, hyp)
    assert m["reading_order_score"] == pytest.approx(1.0)
    assert m["reading_order_na"] is False


def test_reading_order_minor_local_swap_stays_close_to_one():
    # we05: DOM-order-only difference should keep tau close to (not
    # necessarily exactly) 1.
    gt = make_doc(body_text_norm="a b c d e")
    hyp = make_doc(body_text_norm="a b d c e")  # adjacent swap of c/d
    m = content_metrics(gt, hyp)
    # hand-computed: 1 inversion out of n=5 -> tau=1-4*1/20=0.8 -> score=0.9
    assert m["reading_order_score"] == pytest.approx(0.9)
    assert m["reading_order_na"] is False


def test_reading_order_column_flip_reduces_score_substantially():
    # we09: a block/column transposition is a much bigger reordering than a
    # local swap and should score markedly lower.
    gt = make_doc(body_text_norm="alpha beta gamma delta")
    hyp = make_doc(body_text_norm="gamma delta alpha beta")
    m = content_metrics(gt, hyp)
    # hand-computed: hyp_rank_seq=[2,3,0,1] over gt order -> 4 inversions,
    # n=4 -> tau=1-16/12=-1/3 -> score=1/3
    assert m["reading_order_score"] == pytest.approx(1 / 3)
    assert m["reading_order_score"] < 0.9


def test_reading_order_na_when_fewer_than_two_common_tokens():
    gt = make_doc(body_text_norm="onlyword")
    hyp = make_doc(body_text_norm="onlyword")
    m = content_metrics(gt, hyp)
    assert m["reading_order_na"] is True
    assert m["reading_order_score"] == 1.0

    gt2 = make_doc(body_text_norm="foo")
    hyp2 = make_doc(body_text_norm="bar")
    m2 = content_metrics(gt2, hyp2)
    assert m2["reading_order_na"] is True
    assert m2["reading_order_score"] == 1.0


# --- FIN-EM ------------------------------------------------------------------------------


def test_fin_em_amounts_uses_cents_set_recall():
    gt = make_doc(fin_tokens=[
        make_fin_token(type="amount", cents=1000),
        make_fin_token(type="amount", cents=2000),
    ])
    hyp = make_doc(fin_tokens=[
        make_fin_token(type="amount", cents=1000),
        make_fin_token(type="amount", cents=3000),
    ])
    m = content_metrics(gt, hyp)
    assert m["fin_em"]["amount"] == pytest.approx(0.5)
    assert m["fin_em_amounts"] == pytest.approx(0.5)


def test_fin_em_non_amount_types_use_canonical_string():
    gt = make_doc(fin_tokens=[
        make_fin_token(type="date", canonical="2024-01-01"),
        make_fin_token(type="reference_id", canonical="REF123"),
        make_fin_token(type="percent", canonical="19%"),
        make_fin_token(type="vat_letter", canonical="D"),
    ])
    hyp = make_doc(fin_tokens=[
        make_fin_token(type="date", canonical="2024-01-01"),
        make_fin_token(type="reference_id", canonical="REF123"),
        make_fin_token(type="vat_letter", canonical="D"),
    ])
    m = content_metrics(gt, hyp)
    assert m["fin_em"]["date"] == pytest.approx(1.0)
    assert m["fin_em"]["id"] == pytest.approx(1.0)
    assert m["fin_em"]["percent"] == pytest.approx(0.0)  # hyp has none
    assert m["fin_em"]["vat_letter"] == pytest.approx(1.0)


def test_fin_em_type_absent_from_gt_is_vacuously_true():
    # Vacuous truth: a type with zero GT tokens has "zero amount errors" by
    # definition, so FIN_EM(type) is 1.0 (not 0.0), and it is flagged n/a.
    gt = make_doc(fin_tokens=[])
    hyp = make_doc(fin_tokens=[make_fin_token(type="iban", canonical="DE123")])
    m = content_metrics(gt, hyp)
    assert m["fin_em"]["iban"] == 1.0
    assert m["fin_em_na"]["iban"] is True


def test_fin_em_na_covers_all_six_types_and_is_false_when_gt_has_tokens():
    gt = make_doc(fin_tokens=[make_fin_token(type="amount", cents=1000)])
    hyp = make_doc(fin_tokens=[make_fin_token(type="amount", cents=1000)])
    m = content_metrics(gt, hyp)
    assert set(m["fin_em_na"].keys()) == {"amount", "date", "id", "percent", "iban", "vat_letter"}
    assert m["fin_em_na"]["amount"] is False
    assert all(m["fin_em_na"][t] for t in ("date", "id", "percent", "iban", "vat_letter"))


def test_fin_em_ids_dates_pct_weighted_mean():
    gt = make_doc(fin_tokens=[
        make_fin_token(type="date", canonical="2024-01-01"),
        make_fin_token(type="date", canonical="2024-02-02"),
        make_fin_token(type="reference_id", canonical="REF1"),
        make_fin_token(type="percent", canonical="19%"),
    ])
    hyp = make_doc(fin_tokens=[
        make_fin_token(type="date", canonical="2024-01-01"),  # 1/2 dates
        make_fin_token(type="reference_id", canonical="REF1"),  # 1/1 ids
        # 0/1 percent
    ])
    m = content_metrics(gt, hyp)
    assert m["fin_em"]["date"] == pytest.approx(0.5)
    assert m["fin_em"]["id"] == pytest.approx(1.0)
    assert m["fin_em"]["percent"] == pytest.approx(0.0)
    expected = (0.5 * 2 + 1.0 * 2 + 0.0 * 3) / (2 + 2 + 3)
    assert m["fin_em_ids_dates_pct"] == pytest.approx(expected)


# --- cer_financial_tokens / spurious_amounts --------------------------------------------


def test_cer_financial_tokens_is_order_sensitive_char_cer():
    gt = make_doc(fin_tokens=[
        make_fin_token(type="amount", canonical="100.00"),
        make_fin_token(type="date", canonical="2024-01-01"),
    ])
    hyp = make_doc(fin_tokens=[
        make_fin_token(type="amount", canonical="100.00"),
        make_fin_token(type="date", canonical="2024-01-02"),
    ])
    m = content_metrics(gt, hyp)
    gt_str = "100.002024-01-01"
    assert m["cer_financial_tokens"] == pytest.approx(1 / len(gt_str))


def test_spurious_amounts_counts_hyp_cents_not_in_gt():
    gt = make_doc(fin_tokens=[make_fin_token(type="amount", cents=1000)])
    hyp = make_doc(fin_tokens=[
        make_fin_token(type="amount", cents=1000),
        make_fin_token(type="amount", cents=9999),
    ])
    m = content_metrics(gt, hyp)
    assert m["spurious_amounts"] == 1


def test_spurious_amounts_zero_when_hyp_subset_of_gt():
    gt = make_doc(fin_tokens=[
        make_fin_token(type="amount", cents=1000),
        make_fin_token(type="amount", cents=2000),
    ])
    hyp = make_doc(fin_tokens=[make_fin_token(type="amount", cents=1000)])
    m = content_metrics(gt, hyp)
    assert m["spurious_amounts"] == 0


# --- content_score composite -------------------------------------------------------------


def test_content_score_matches_weighted_formula():
    gt = make_doc(
        body_text_norm="a b c",
        fin_tokens=[make_fin_token(type="amount", cents=1000)],
    )
    hyp = make_doc(
        body_text_norm="a b c",
        fin_tokens=[make_fin_token(type="amount", cents=1000)],
    )
    m = content_metrics(gt, hyp)
    # cer=0, fin_em_amounts=1 (cents match, not n/a), content_f1=1 (identical
    # text). fin_em_ids_dates_pct is fully n/a here (no date/id/percent token
    # on either side), so its weight-3 term is DROPPED from content_score's
    # weighted mean rather than counted at a value of 1.0 -- the two
    # formulations happen to coincide numerically in this all-perfect
    # fixture; the dedicated weight-dropping test below uses an imperfect
    # fixture where they do not.
    assert all(m["fin_em_na"][t] for t in ("date", "id", "percent"))
    expected = (1.0 * 5 + 1.0 * 1 + 1.0 * 3) / (5 + 1 + 3)  # ids_dates_pct term dropped
    assert expected == pytest.approx(1.0)
    assert m["content_score"] == pytest.approx(expected)


def test_content_score_drops_na_weight_instead_of_crediting_it():
    # GT has no amounts at all (fin_em_amounts n/a -> dropped, not a free
    # 1.0 at weight 5), and the id that IS present is wrong, so the
    # remaining active terms average below 1.0. Dropping the n/a weight must
    # give a strictly LOWER content_score than naively crediting a free 1.0
    # at full weight would have -- proving the drop is not a no-op.
    gt = make_doc(
        body_text_norm="ref REF1 date 2024-01-01",
        fin_tokens=[
            make_fin_token(type="reference_id", canonical="REF1"),
            make_fin_token(type="date", canonical="2024-01-01"),
        ],
    )
    hyp = make_doc(
        body_text_norm="ref REF9 date 2024-01-01",
        fin_tokens=[
            make_fin_token(type="reference_id", canonical="REF9"),  # wrong id
            make_fin_token(type="date", canonical="2024-01-01"),
        ],
    )
    m = content_metrics(gt, hyp)
    assert m["fin_em_na"]["amount"] is True
    assert m["fin_em_amounts"] == 1.0
    # date=1.0 (matches), id=0.0 (REF1 != REF9), percent n/a->1.0:
    # wmean(date*2, id*2, percent*3) = (1*2 + 0*2 + 1*3) / 7
    assert m["fin_em_ids_dates_pct"] == pytest.approx(5 / 7)

    naive_full_credit = (
        1.0 * 5 + m["fin_em_ids_dates_pct"] * 3 + (1 - m["cer"]) * 1 + m["content_f1"] * 3
    ) / 12
    dropped_weight = (
        m["fin_em_ids_dates_pct"] * 3 + (1 - m["cer"]) * 1 + m["content_f1"] * 3
    ) / 7
    assert dropped_weight < naive_full_credit
    assert m["content_score"] == pytest.approx(dropped_weight)


def test_content_score_formula_when_every_fin_em_component_is_na():
    # GT has zero fin_tokens of any kind: both fin_em_amounts and
    # fin_em_ids_dates_pct are fully n/a, so Content collapses to exactly
    # wmean((1-CER)*1, content_F1*3), per the plan's literal fallback.
    gt = make_doc(body_text_norm="a b c")
    hyp = make_doc(body_text_norm="a b d")
    m = content_metrics(gt, hyp)
    assert m["fin_em_na"]["amount"] is True
    assert all(m["fin_em_na"][t] for t in ("date", "id", "percent"))
    expected = ((1 - m["cer"]) * 1 + m["content_f1"] * 3) / (1 + 3)
    assert m["content_score"] == pytest.approx(expected)

"""Codex round 3: outside-table amounts as tokens, tokens in reading order, label-value multisets,
configuration identity in the ranked leaderboard."""
from __future__ import annotations

import json
from pathlib import Path

from ocrgrade import assertions, canonicalize, metrics_structure, roles, sidecar as sc
from ocrgrade.ir import CriticalField, LabelValuePair


def _doc(html: str):
    return canonicalize.build_document(html, roles.load_role_map(None), sc.default_sidecar("t"))


def test_outside_table_amount_is_not_matched_inside_a_longer_number():
    gt = _doc("<p>Amount due: 12.34</p>")
    side = sc.default_sidecar("t")
    side.critical_fields = [CriticalField(role="grand_total", value="12.34", cell_ref=None, label="Amount due")]
    for wrong in ("112.34", "-12.34", "12.345"):
        hyp = _doc(f"<p>Amount due: {wrong}</p>")
        a1 = {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"]
        assert not a1.passed, wrong
    hyp = _doc("<p>Amount due: EUR 12.34</p>")
    assert {a.id: a for a in assertions.run_assertions(gt, hyp, side)}["A1"].passed


def test_financial_tokens_follow_reading_order():
    doc = _doc("<p>Deposit 1.00</p><table><tr><td>Total</td><td>2.00</td></tr></table><p>Balance 3.00</p>")
    assert [t.cents for t in doc.fin_tokens if t.type == "amount"] == [100, 200, 300]
    moved = _doc("<table><tr><td>Total</td><td>2.00</td></tr></table><p>Deposit 1.00</p><p>Balance 3.00</p>")
    assert [t.cents for t in moved.fin_tokens if t.type == "amount"] == [200, 100, 300]


def test_nested_table_tokens_follow_their_parent_once():
    doc = _doc("<table><tr><td>A 1.00</td><td><table><tr><td>B 2.00</td></tr></table></td></tr></table><p>C 3.00</p>")
    assert [t.cents for t in doc.fin_tokens if t.type == "amount"] == [100, 200, 300]


def test_repeated_label_value_pair_needs_two_counterparts():
    pair = LabelValuePair(label="VAT", value="2.50", source="colon_pattern", cell_ref=None)
    assert metrics_structure._label_value_f1([pair, pair], [pair]) < 1.0
    assert metrics_structure._label_value_f1([pair, pair], [pair, pair]) == 1.0
    gt = _doc("<p>VAT: 2.50</p><p>VAT: 2.50</p>")
    one = _doc("<p>VAT: 2.50</p>")
    two = _doc("<p>VAT: 2.50</p><p>VAT: 2.50</p>")
    side = sc.default_sidecar("t")
    assert not {a.id: a for a in assertions.run_assertions(gt, one, side)}["A6"].passed
    assert {a.id: a for a in assertions.run_assertions(gt, two, side)}["A6"].passed


def test_ranked_leaderboard_keeps_the_configuration_and_warns_on_a_mix(tmp_path: Path, capsys):
    from ocrgrade import cli

    base = {"n_cases": 1, "catastrophic_rate": 0.0, "gate_pass_rate": 1.0, "archival_safe_rate": 1.0,
            "macro_q": 1.0, "worst_category_q": 1.0, "category_macro": {"receipt": 90.0}}
    a = dict(base, model="a", config_hash={"roles_yaml": "aaaaaaaaaaaa", "annotator": "111111111111"})
    b = dict(base, model="b", config_hash={"roles_yaml": "aaaaaaaaaaaa", "annotator": "222222222222"})
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    out = tmp_path / "leaderboard.csv"

    assert cli.main(["rank", "--summary", str(pa), "--summary", str(pb), "--out", str(out)]) == 0

    rows = out.read_text(encoding="utf-8").strip().splitlines()
    assert rows[0].split(",")[-1] == "config"
    assert any(r.endswith("annotator=111111111111;roles_yaml=aaaaaaaaaaaa") for r in rows[1:])
    assert "2 different grader configurations" in capsys.readouterr().err

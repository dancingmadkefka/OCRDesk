"""Codex round 2, inputs side: currency around signed amounts, nested tables out of the parent's TEDS
html, one image per stem, config hashes in the summary, sidecar case-id validation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from ocrgrade import canonicalize, fintoken, roles, sidecar as sc


def _amounts(text: str):
    return [t for t in fintoken.extract_tokens(text) if t.type == "amount"]


def test_currency_survives_a_sign_between_marker_and_number():
    for text, cents, currency in (("CHF -12.34", -1234, "CHF"), ("EUR (12.34)", -1234, "EUR"), ("€ -5.00", -500, "EUR")):
        toks = _amounts(text)
        assert len(toks) == 1, text
        assert (toks[0].cents, toks[0].currency, toks[0].raw) == (cents, currency, text), text
    glued = _amounts("XCHF -12.34")
    assert glued[0].cents == -1234 and glued[0].currency is None  # a marker glued to a word is not a currency


def test_parent_teds_html_excludes_nested_tables():
    html = ("<table><tr><td>Outer</td><td><table><tr><td>Inner</td><td>1.00</td></tr></table></td></tr>"
            "<tr><td>Total</td><td>1.00</td></tr></table>")
    rm = roles.load_role_map(None)
    doc = canonicalize.build_document(html, rm, sc.default_sidecar("t"))
    assert len(doc.tables) == 2
    assert doc.tables[0].outer_html.count("<table") == 1 and "Inner" not in doc.tables[0].outer_html
    assert "Outer" in doc.tables[0].outer_html and "Inner" in doc.tables[1].outer_html
    flat = canonicalize.build_document("<table><tr><td>A</td></tr></table>", rm, sc.default_sidecar("t"))
    assert flat.tables[0].outer_html.startswith("<table") and "A" in flat.tables[0].outer_html


def test_list_images_keeps_one_variant_per_stem(tmp_path: Path):
    module_path = Path(__file__).resolve().parents[2] / "backend" / "ocr_core.py"
    spec = importlib.util.spec_from_file_location("ocrdesk_ocr_core", module_path)
    ocr_core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ocr_core)
    for name in ("doc.ocr_ready.jpg", "doc.ocr_ready.png", "other.ocr_ready.webp", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    listed = ocr_core.list_images(tmp_path)
    assert [(stem, path.name) for stem, path in listed] == [("doc", "doc.ocr_ready.png"), ("other", "other.ocr_ready.webp")]


def _mini_corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    case = corpus / "case-001"
    case.mkdir(parents=True)
    gt = "<table><tr><td>Total</td><td>12.50</td></tr></table>"
    (case / "doc.html").write_text(gt, encoding="utf-8")
    (case / "doc.jpg").write_bytes(b"\xff\xd8\xff")
    hyp = tmp_path / "hyp"
    hyp.mkdir()
    (hyp / "case-001.html").write_text(gt, encoding="utf-8")
    return corpus, hyp


def test_summary_records_the_scoring_configuration(tmp_path: Path):
    from ocrgrade import cli

    corpus, hyp = _mini_corpus(tmp_path)
    (corpus / "roles.yaml").write_text("total_value:\n  - my-total\n", encoding="utf-8")
    assert cli.main(["annotate", "--corpus", str(corpus)]) == 0
    out = tmp_path / "out"
    assert cli.main(["score", "--corpus", str(corpus), "--hyp-dir", str(hyp), "--out-dir", str(out), "--run-id", "r"]) == 0
    config_hash = json.loads((out / "r" / "summary.json").read_text(encoding="utf-8"))["config_hash"]
    assert set(config_hash) == {"roles_yaml", "annotator", "corpus_roles_yaml"}
    assert all(len(v) == 12 for v in config_hash.values())
    assert config_hash["annotator"] == sc.annotator_fingerprint()


def test_a_sidecar_copied_from_another_case_is_refused(tmp_path: Path, capsys):
    from ocrgrade import cli

    corpus, hyp = _mini_corpus(tmp_path)
    assert cli.main(["annotate", "--corpus", str(corpus)]) == 0
    meta_path = corpus / "case-001" / "doc.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["case_id"] = "case-999"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    capsys.readouterr()
    out = tmp_path / "out"
    assert cli.main(["score", "--corpus", str(corpus), "--hyp-dir", str(hyp), "--out-dir", str(out), "--run-id", "r"]) == 3
    assert "belongs to case 'case-999', not 'case-001'" in capsys.readouterr().err

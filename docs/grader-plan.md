# ocrgrade — architecture and workflow contract

Status: implementation contract, 2026-09-15. Produced by the architecture stage from the binding design in AIFA's `docs/investigations/ocr-html-grader-design-2026-06-19.md` (sections C–H) and the research request in `docs/spikes/ocr-html-grader-research-request.md`. Implementers follow this file; they do not re-litigate it. Once the grader ships, the enduring parts move to `docs/grader.md` and this file is deleted.

## Scope, assumptions, non-goals

**Scope.** Build `ocrgrade/` (pure Python 3.13, stdlib + the allowed packages) in this repo, implementing design-doc sections C–H: fragment/document canonicalization, role resolution, cell graph, financial-token exact match (FIN-EM), packaged TEDS, five critical assertions, tiered scorer, CLI, reports, synthetic fixtures. No LLM, no network, no GPU.

**Assumptions.** The real 36-case corpus (`C:\Users\daniel\VSCodeProjects\AIFA GT Set`) never enters this public repo. `table_recognition_metric`, `apted`, `lxml`, `html5lib`, `beautifulsoup4`, `rapidfuzz`, `pyyaml`, `pytest` are installed in `.venv` (`.venv\Scripts\python.exe`). The presentation tie-breaker (design E4) is a documented no-op hook only.

**Non-goals.** Playwright/SSIM/pHash, LLM judging, KG/transaction extraction, bootstrap confidence intervals, multi-page documents, React UI integration.

**Architect fill-ins** (gaps the design left open at formula level; cheap to retune later via `shadow`): the Content sub-weights and the 74-ceiling trigger in section 5.

---

## 1. Module map

`ocrgrade/ir.py` is a zero-logic shared contract. Every other module imports it; none modify its shapes during the parallel stage. It exists before Stage 1 starts.

```python
Role = Literal["numeric_value", "total_value", "section_header", "label", "value",
               "handwritten", "monospace", "header", "spacer", "other"]

@dataclass(frozen=True)
class CellRef: table_index: int; row: int; col: int

@dataclass(frozen=True)
class FinToken:
    type: Literal["amount", "date", "percent", "iban", "vat_letter", "masked_card", "reference_id"]
    raw: str; canonical: str | None; cents: int | None; currency: str | None
    vat_letter: str | None; attached: bool; cell_ref: CellRef | None

@dataclass(frozen=True)
class Cell:
    table_index: int; row: int; col: int; rowspan: int; colspan: int
    is_span_origin: bool  # False on cells synthesized by span expansion
    text_raw: str; text_norm: str; role: Role; is_numeric: bool; is_header: bool
    tokens: list[FinToken]

@dataclass(frozen=True)
class Table:
    index: int; n_rows: int; n_cols: int; cells: list[Cell]  # occupied grid, spans expanded
    outer_html: str  # exact serialized <table>…</table>, used by teds_adapter

@dataclass(frozen=True)
class LabelValuePair:
    label: str; value: str
    source: Literal["table_row", "definition_list", "colon_pattern", "sibling_heuristic", "sidecar"]
    cell_ref: CellRef | None

@dataclass(frozen=True)
class LineItem: table_index: int; row: int; fields: dict[str, str]

@dataclass(frozen=True)
class Document:
    tables: list[Table]; section_headers: list[str]
    label_value_pairs: list[LabelValuePair]; line_items: list[LineItem]
    fin_tokens: list[FinToken]; body_text_norm: str
    parse_ok: bool; parse_error: str | None; was_fragment: bool; truncated: bool

@dataclass(frozen=True)
class CriticalField:
    role: str; value: str; cell_ref: CellRef | None; expected_multiplicity: int = 1

@dataclass
class Sidecar:  # mutable: annotate writes it, score reads it
    schema_version: int; case_id: str; category: str; category_secondary: str | None
    locale: str; currency: str | None; decimal_sep: str; vat_letter_scheme: bool
    has_tables: bool; required_sections: list[str]; critical_fields: list[CriticalField]
    label_value_pairs: list[dict]; line_item_schema: list[str]
    confirmed: bool = False; notes: str = ""

@dataclass
class AssertionResult: id: str; critical: bool; passed: bool; detail: str

@dataclass
class CaseResult: ...  # section 7; produced by scoring.py, consumed by report.py
```

| Module | Scope | Responsibility | Public surface |
|---|---|---|---|
| `ir.py` | shared, Stage 0 | dataclasses above | all names above |
| `canonicalize.py` | A | html5lib parse, NFKC fold, whitespace collapse, tag-equivalence fold (`b`≈`strong`), fragment/document unwrap | `build_document(html: str, role_map: RoleMap, sidecar: Sidecar \| None) -> Document` |
| `roles.py` | A | load `roles.yaml` (+ optional `<corpus>/roles.yaml` override), resolve element → Role | `load_role_map(corpus_dir: Path \| None) -> RoleMap`; `resolve_role(tag, classes, text, ctx) -> Role` |
| `tables.py` | A | walk `<table>`, expand rowspan/colspan into an occupied grid | `build_table(table_el, index, role_map) -> Table` |
| `fintoken.py` | A | regex extraction + normalization for amount/date/percent/IBAN/VAT-letter/masked-card/reference-id | `extract_tokens(text: str, locale_hint) -> list[FinToken]`; `attach_vat_letters(tokens) -> list[FinToken]` |
| `markdown.py` | A | deterministic markdown → HTML (pipe tables), fence strip, plain text → `<pre>` | `to_html(raw: str, hint: Literal["html", "markdown", "plain"]) -> str` |
| `teds_adapter.py` | B | per-table isolation + minimal-body rewrap before calling packaged TEDS (section 5) | `teds_scores(gt_tables, hyp_tables) -> TedsResult{teds, teds_struct, per_table: list[float]}` |
| `metrics_content.py` | B | CER/WER (rapidfuzz), multiset content-F1 + omission/hallucination, local Kendall-tau reading order | `content_metrics(gt: Document, hyp: Document) -> dict` |
| `metrics_structure.py` | B | grid-aligned cell-content F1, label-value F1, line-item F1, heading sequence | `structure_metrics(gt, hyp) -> dict` |
| `assertions.py` | B | the 5 critical + 5 recommended non-critical assertions, pure functions | `run_assertions(gt, hyp, sidecar) -> list[AssertionResult]` |
| `scoring.py` | B | tier logic, Q, DisplayScore, ARCHIVAL_SAFE, ranking key, rollup | `score_document(gt, hyp, sidecar) -> CaseResult`; `rank_key(summary) -> tuple` |
| `sidecar.py` | C | load/save `.meta.json`, `annotate` derivation, CSV writer | `load_or_default(case_id, corpus_dir) -> Sidecar`; `derive(case_id, gt_html, manifest_entry) -> Sidecar`; `write_review_csv(sidecars, path)` |
| `inputs.py` | C | three hypothesis sources + AIFA-GT-Set corpus discovery | `discover_corpus(dir) -> list[CaseFiles]`; `load_hypotheses(args) -> Iterator[HypothesisRecord]` |
| `report.py` | C | leaderboard.csv, summary.json, report.html (stdlib templating, no Jinja) | `write_case_json`, `write_leaderboard_csv`, `write_report_html` |
| `cli.py` / `__main__.py` | C | argparse subcommands, wiring A+B+C | `main(argv) -> int` |

---

## 2. Sidecar schema v1 and `annotate`

`<stem>.meta.json` lives next to the GT, never in the repo:

| Key | Type | Default | Derivation in `annotate` |
|---|---|---|---|
| `schema_version` | int | `1` | constant |
| `case_id` | str | — | folder name |
| `category` | enum: receipt, invoice, bank-statement, card-statement, tax, form, letter, bill, payslip, other | `"other"` | keyword regex over GT text + `ocr_manifest.json` `document_type` as prior |
| `category_secondary` | str \| null | `null` | unset |
| `locale` | str | `"OTHER"` | IBAN country code found, else language field from manifest |
| `currency` | str \| null | `null` | first currency symbol/code seen (`€` → EUR, `CHF`, `£` → GBP) |
| `decimal_sep` | `","` \| `"."` | `"."` | majority decimal separator across amount tokens found |
| `vat_letter_scheme` | bool | `false` | true if the VAT-letter regex matches at least one amount |
| `has_tables` | bool | from manifest | manifest `has_tables` |
| `required_sections` | list[str] | `[]` | normalized text of every `role == section_header` cell/element |
| `critical_fields` | list[CriticalField] | `[]` | every `role == total_value` cell → `CriticalField(role="grand_total"/"subtotal", value, cell_ref, expected_multiplicity=count_in_gt(value))` |
| `label_value_pairs` | list[{label, value}] | `[]` | table-adjacent pairs + sibling-heuristic pairs (section 3) |
| `line_item_schema` | list[str] | `[]` | column identities from the `col-*` class family or header-row text, per table with 3+ data rows |
| `confirmed` | bool | `false` | always `false` on generation |
| `notes` | str | `""` | unset |

`annotations_review.csv` columns (one row per case, written at the corpus root): `case_id, category, category_secondary, locale, currency, decimal_sep, vat_letter_scheme, n_critical_fields, n_required_sections, n_label_value_pairs, confirmed, notes`.

`score` runs against unconfirmed sidecars; `provisional: true` propagates to `CaseResult`.

---

## 3. `roles.yaml` v1

372 classes occur across the 36 real cases with a median count of 1; most are per-document layout noise. `roles.yaml` covers only classes that recur with consistent meaning; everything else falls through to content heuristics.

```yaml
total_value:      [final-val, total-value, total-row, payable-amount, obv-amount, receipt-total, total-due-row]
numeric_value:    {classes: [num, col-amount, amount-col, amount-value, col-price, price-col, col-total, value-col, value-cell, col-qty, col-unit, col-tax], align_expectation: right}
section_header:   [grey-bg, section-title, main-title, summary-title, header-bar, company-title, invoice-title, receipt-title, receipt-header, header-title, sub-section-title]
label:            [label, label-col, label-box, field-title, meta-label, slip-label, total-label, obv-label, amount-label, details-label]
value:            [value, meta-value, slip-val, val-col]
handwritten:      [handwritten, handwritten-signature, handwritten-notes, handwritten-v]
monospace:        [mono, monospace, machine-data, code-style, code, machine-codes, ocr-line]
header:           [header-cell]
spacer:           [spacer-row, status-row-spacer]
# alignment hints only (not a Role): text-right, right, right-align, right-aligned, pull-right,
# text-center, center-text, center
```

`col-desc` / `col-date` / `col-price` and friends feed `sidecar.line_item_schema` inference (column identity), not `Role`.

**Content heuristics** (fire when no class matches; always run as a backstop):

| Heuristic | Rule |
|---|---|
| `is_numeric` | matches the amount/percent/date regex, or is pure digits with separators |
| total keyword | `re.search(r'\btotal\b|\bgesamt\b|\bsumme\b|\bsaldo\b|\bbetrag\b|\bnet pay\b|\bbalance\b|\bamount due\b', text, re.I)`; `betrag` is the Swiss QR-bill amount label (cases 006, 027) |
| header | `<th>`, or the first `<tr>` of `<thead>` |
| last-numeric-row | last row in a table with at least one numeric cell and no non-empty rows after it → `total_value` candidate fallback |
| label-value sibling (no classes, no table) | adjacent block siblings where sibling 1 ends with `:` or has no trailing digits, and sibling 2 is value-shaped (numeric/date/short code) → `(label, value)`. Needed because cases 010, 027 and 006 each invent unrelated class names for the same pattern |

---

## 4. Assertions and structural alignment

**Structural alignment.** `table_recognition_metric.TEDS.__call__` returns only a scalar; it does not expose the APTED node mapping. ocrgrade therefore defines structural alignment as **grid-position alignment after rowspan/colspan expansion**: GT table *t* and hypothesis table *t′* are paired by document-order index (0-th GT table ↔ 0-th hyp table, and so on; if counts differ, pair up to `min(n)` and flag the remainder via A7). Cell `(table_index, row, col)` in the GT occupied grid aligns to the cell at identical coordinates in the matched hypothesis table, or to nothing if that slot does not exist.

| ID | Critical | Rule | Derivation from GT |
|---|---|---|---|
| A1 `value_in_aligned_cell` | yes | for each `critical_field` with a `cell_ref`: the hyp cell at the same grid coordinates contains `field.value` as a normalized substring | sidecar `critical_fields` |
| A2 `total_in_totals_row` | yes | hyp has a row containing the GT total value where the row also matches the total-keyword heuristic or is the table's last numeric row | GT `role == total_value` rows |
| A3 `no_duplicated_financial_value` | yes | `count_hyp(v) <= count_gt(v)` for every amount `v` | GT fin-token multiset (tolerates legitimate repeats, e.g. the QR-bill total repeated across slips in case 027) |
| A4 `required_sections_present` | yes | every `required_sections` entry is a case-insensitive substring of some hyp section-header text | sidecar `required_sections` |
| A5 `vat_letter_attached` | yes | every GT `attached=true` VAT-letter token has a hyp token with the same cents value whose `attached` matches GT's own adjacency pattern (no space / space / block) | GT fin-tokens |
| A6 `label_value_grouping_intact` | no | for each GT `(label, value)` pair: value present in hyp and the nearest hyp label normalizes equal to the GT label | sidecar + derived pairs |
| A7 `table_count_delta` | no | `hyp table count == gt table count` | table count |
| A8 `line_item_intact` | no | GT line-item tuple co-occurs intact in one hyp row | sidecar `line_item_schema` rows |
| A9 `heading_sequence_match` | no | ordered section-header lists sequence-match | GT `section_headers` |
| A10 `no_hallucinated_amount` | no | every hyp amount has at least one GT occurrence | GT/hyp fin-token sets |

---

## 5. Scoring: exact formulas and edge cases

**TEDS call contract (verified from the installed source).** `TEDS(structure_only=bool)` is called as `teds(pred_html_str, gt_html_str) -> float`, single strings, and internally does `pred_element.xpath("body/table")[0]`; it silently returns `0.0` if either side has no `<table>` as a **direct child of `<body>`**. Every real GT case nests tables inside wrapper `<div>`s, so `teds_adapter.py` must never feed a full document to `TEDS()`. Per matched table pair it serializes `<html><body>{outer_html}</body></html>` for GT and hyp independently, calls TEDS once per pair, and aggregates with a node-count-weighted mean across tables for the document-level `teds` / `teds_struct`. A colspan/rowspan mismatch costs full rename distance even with identical text; expected, not a bug.

```
CER = Levenshtein(chars_gt, chars_hyp) / max(1, len(chars_gt))          # rapidfuzz
WER = Levenshtein(words_gt, words_hyp) / max(1, len(words_gt))
content_F1 = 2PR / max(eps, P+R);  P = overlap / sum(c_hyp(t));  R = overlap / sum(c_gt(t))
omission_rate = 1 - R;  hallucination_rate = 1 - P

# reading order, local, no scipy: tau-a on first-occurrence rank of common tokens
inversions = mergesort_inversions([rank_hyp(t) for t in common_tokens sorted by rank_gt(t)])
tau = 1 - 4*inversions / (n*(n-1))            # n = |common_tokens|
reading_order_score = (tau+1)/2  if n >= 2  else 1.0 (and reading_order_na = true)

WEIGHTS = {date: 2, id: 2, percent: 3, amount: 5, vat_letter: 5, iban: 5, grand_total: 6}   # design E2
FIN_EM(type) = |gt_set & hyp_set| / max(1, |gt_set|)
FIN_EM_ids_dates_pct = wmean(FIN_EM(date)*2, FIN_EM(id)*2, FIN_EM(percent)*3)

# Content sub-weights: architect fill-in, retune via `shadow`
Content = wmean(FIN_EM_amounts*5, FIN_EM_ids_dates_pct*3, (1-CER)*1, content_F1*3)

Structure = mean(TEDS_struct, cell_content_F1, label_value_F1, line_item_F1)   # design F
Q = 0.45*Content + 0.45*Structure + 0.10*ReadingOrder
```

**Edge cases.**

- **No tables in GT** (cases 010, 013, 019, 025, 027, 032): drop `TEDS_struct`, `cell_content_F1`, `line_item_F1` from the `Structure` mean (never score them as 0). `Structure = label_value_F1`; if that is also n/a, `Structure = heading_sequence_score`; if that is also n/a, `Q = 0.9*Content + 0.1*ReadingOrder` and `structure_na: true` is recorded.
- **Empty hypothesis / harness `status == "error"`** → Tier 0 fail, `CATASTROPHIC`, `DisplayScore = 0`, all metrics `null`.
- **Parse failure** → `len(body_text_norm) < 1` with raw input longer than 20 chars (html5lib is fragment-tolerant and essentially never throws) → `CATASTROPHIC`.
- **Truncation** (Stage-0 gate) → missing `</html>` in an input that opened `<html`, or input longer than 200 chars that ends inside an unclosed `<table>`/`<tr>`/`<td>` → `CATASTROPHIC`. Token-ratio and refusal detectors (design I) are deferred.
- **Markdown / plain-text hypothesis** → converted via `markdown.to_html` before any check runs; `input_form` is recorded on the case result.

```
DisplayScore =
    0                       if Tier 0 fails (CATASTROPHIC)
    min(59, 100*Q)          if any CRITICAL assertion (A1–A5) fails
    min(74, 100*Q)          if all critical pass but label_value_F1 < 1.0 or line_item_F1 < 1.0   # architect fill-in
    100*Q                   otherwise (presentation P is a no-op in this build; the 0.97/0.03 blend activates only with a presentation stage)
```

`ARCHIVAL_SAFE`: design F verbatim (Tier 0 pass, all critical assertions pass, `FIN_EM_amounts == 1.0`, CER on financial tokens == 0, `TEDS_struct >= 0.95`, no spurious amounts), with the `TEDS_struct` clause excluded (not failed) when `structure_na`.

Ranking key and category rollup exactly as design F: `key = (1 - catastrophic_rate, gate_pass_rate, macro_Q, worst_category_Q, macro_P, throughput, -peak_VRAM)` with `macro_P` fixed `None`; `CategoryMacro_c = mean(DisplayScore)` per category; `HEADLINE = mean(CategoryMacro_c)`; micro reported alongside, macro is the decision metric.

---

## 6. CLI spec

`python -m ocrgrade <command> [args]`

| Command | Key args | Exit codes |
|---|---|---|
| `annotate` | `--corpus DIR [--manifest FILE]` | 0 ok · 1 usage · 2 corpus not found |
| `score` | `--corpus DIR (--aifa-results FILE \| --hyp-dir DIR \| --ocrdesk-images-dir DIR --model NAME) --out-dir DIR [--run-id ID]` | 0 all cases scored · 1 usage · 2 no cases discovered / bad input file · 3 at least one case raised an uncaught exception (a bug, distinct from a legitimate CATASTROPHIC tier) |
| `rank` | `--summary FILE` (repeatable) `--out FILE` | 0 · 1 · 2 missing summary file |
| `shadow` | `--corpus "AIFA GT Set" --hyp-dir DIR --out-dir LOCAL_GITIGNORED_DIR` | as `score`; writes `shadow_expected.json` |
| `fixtures-check` | `[--fixtures-dir tests/grader/fixtures]` | 0 all fixtures reproduce the expected tier · 4 at least one mismatch (CI gate) |

Hypothesis sources: AIFA `html_vlm_*.json` (`cases[].case_id`, `status`, `hypothesis_html`; if a `hypothesis_raw` field exists, prefer converting it), a directory `<dir>/<case-id>.html` (also `.md`/`.txt`), or the OCRDesk layout (`<images_dir>/results/<stem>/<model>.html` with GT `<stem>_ground_truth.html`).

---

## 7. Results schemas

`cases/<case-id>.json`: `case_id, status, input_form, tier, provisional, parse_ok, truncated, table_count_gt, table_count_hyp, structure_na, reading_order_na, metrics{cer, wer, content_f1, omission_rate, hallucination_rate, fin_em{amount, date, id, percent, iban}, teds, teds_struct, cell_content_f1, label_value_f1, line_item_f1, heading_sequence_score, reading_order_score, content_score, structure_score, q}, assertions[{id, critical, passed, detail}], archival_safe, display_score, runtime_seconds, errors[]`.

`summary.json`: `run_id, model, quant, prompt, n_cases, n_ok, n_catastrophic, n_reject, n_pass, catastrophic_rate, gate_pass_rate, archival_safe_rate, macro_q, worst_category_q, macro_p (null), category_macro{}, category_micro{}, headline_display_score, config_hash{roles_yaml, fixtures}`.

`leaderboard.csv`: `model, quant, prompt, n, catastrophic_rate, gate_pass_rate, archival_safe_rate, macro_Q, worst_category_Q, <category>_display_score…` (category columns sorted alphabetically for determinism).

---

## 8. Test plan

Unit tests per module under `tests/grader/` mirror the module map. Synthetic fixtures under `tests/grader/fixtures/<id>/{gt.html, hyp.html, sidecar.meta.json, expected.json}`, one per worked example plus the two extra cases:

| id | worked example | expected tier | firing assertion |
|---|---|---|---|
| `we01_wrong_cell` | correct amount, wrong cell | REJECT | A1 |
| `we02_wrong_digit` | one wrong digit in the total | REJECT | A1 |
| `we03_fragment_vs_doc` | same content, different valid CSS, fragment vs document | PASS / ARCHIVAL_SAFE | none |
| `we04_omission` | beautiful output, omitted line item | REJECT | A4 or A8 |
| `we05_dom_order` | different DOM traversal, same grid | PASS | none (tau ≈ 1) |
| `we06_label_swap` | opening/closing balance swapped | REJECT | A1 / A6 |
| `we07_duplicate` | total value duplicated into a line row | REJECT | A3 |
| `we09_column_flip` | multi-column reading-order flip | PASS with reduced Q | reading_order low |
| `we10_vat_detached` | VAT letter detached from the price | REJECT | A5 |
| `we_notable` | table-less letter, label-value only | PASS, `structure_na = true` | none |
| `we_missing_row` | one line-item row omitted from a 6-row table | REJECT | A1 or A8; documents the cascading grid-shift limitation (section 10, item 2) |

`fixtures-check` and a pytest wrapper both run these; CI runs `fixtures-check` and `pytest tests/grader -k "not shadow"` without the private corpus. `shadow` writes a gitignored `shadow_expected.json`; `test_shadow_regression.py` is `skipif(not path.exists())`, so it runs only on the operator's machine.

---

## 9. Implementation stages (workflow contract)

**Stage 0 — IR scaffold.** `ocrgrade/ir.py` verbatim from section 1, plus `ocrgrade/__init__.py`. Acceptance: `python -c "import ocrgrade.ir"` succeeds.

**Stage 1 — three parallel implementer scopes**, each depending only on Stage 0:

| Scope | Owns | Deliverables | Acceptance |
|---|---|---|---|
| **A — data layer** | `canonicalize.py`, `roles.py`, `roles.yaml`, `tables.py`, `fintoken.py`, `markdown.py` and their tests | modules + `tests/grader/test_{canonicalize,roles,tables,fintoken,markdown}.py` | those test files pass; VAT-letter attach/detach and rowspan/colspan expansion each have a dedicated test |
| **B — grading logic** | `teds_adapter.py`, `metrics_content.py`, `metrics_structure.py`, `assertions.py`, `scoring.py` and their tests; hand-constructs `Document`/`Table`/`Cell` fixtures directly against `ir.py`, never waits for Scope A | modules + `tests/grader/test_{teds_adapter,metrics_content,metrics_structure,assertions,scoring}.py` | those test files pass; a dedicated test proves `teds_adapter` rewraps tables before calling `TEDS()` (nonzero score on a div-nested table pair) |
| **C — shell** | `sidecar.py`, `inputs.py`, `report.py`, `cli.py`, `__main__.py`, `tests/grader/conftest.py`, `tests/grader/fixtures/*`, `docs/grader.md`, `requirements-grader.txt`, README and DECISIONS appends | modules, docs, fixtures, `tests/grader/test_{sidecar,inputs,cli,fixtures}.py`; builds against injectable `build_document` / `score_document` callables first | `python -m ocrgrade fixtures-check` exits 0 once integrated |

**Stage 2 — integration.** Wire the real `build_document` / `score_document` into `cli.py`. Acceptance: `python -m ocrgrade score --corpus tests/grader/fixtures --hyp-dir <fixtures hyp dir> --out-dir <scratch>` produces `summary.json` with `n_cases == 11`.

**Stage 3 — verification.** An independent verifier runs the commands below and returns pass/fail with evidence.

**Stage 4 — remediation.** Only on failure: route the failed check to the owning scope by file; stop after two loops and report the blocker.

**Rollback.** Everything is additive: `ocrgrade/`, `tests/grader/`, `docs/grader.md`, `requirements-grader.txt`, small appends to `README.md` / `docs/DECISIONS.md`. No existing OCRDesk code path changes.

---

## 10. Risks for the verifier to probe

1. **TEDS `body/table` footgun.** Any full-document call silently returns 0.0 because every real GT nests tables in wrapper `<div>`s. Verify `teds_adapter.py` never calls `TEDS()` on anything but an isolated, rewrapped table.
2. **Grid-position alignment under row omission.** One missing hyp row shifts every later row's index, producing cascading A1 failures instead of one. Accepted MVP limitation, made explicit by the `we_missing_row` fixture.
3. **Table matching by document order only.** Reordered or merged/split tables misalign. Flagged, not fixed.
4. **`roles.yaml` thin coverage.** Heuristics carry most weight. Verify against `shadow` output before trusting rankings, not only against synthetic fixtures.
5. **VAT-letter adjacency.** Must match the GT's own attached / block-separated pattern; needs the `"59.99D"` / `"59.99 D"` / `"59.99\nD"` triad as explicit unit cases.
6. **Markdown → HTML fidelity.** New code with no upstream to reuse; needs a CRLF-input test.
7. **APTED cost.** The largest real table is ~31 rows × 6 cols; add a per-table node cap and a timeout that yields `CATASTROPHIC` as cheap insurance.
8. **`Levenshtein` package.** Imported by `table_recognition_metric`; present in `.venv`; must be pinned in `requirements-grader.txt` with `apted` and `lxml`.

---

## Verification commands

```powershell
cd "C:\Users\daniel\VSCodeProjects\ocr-benchmark-grader"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-grader.txt
.\.venv\Scripts\python.exe -m pytest tests/ -q                          # full suite incl. existing backend tests
.\.venv\Scripts\python.exe -m pytest tests/grader -q -k "not shadow"    # CI-safe grader subset
.\.venv\Scripts\python.exe -m ocrgrade fixtures-check                    # golden-fixture gate, exit 0 required
```

No linter is configured in this repo, so verification is pytest plus `fixtures-check`. `shadow` against the real corpus is manual and local, never CI:

```powershell
.\.venv\Scripts\python.exe -m ocrgrade shadow --corpus "C:\Users\daniel\VSCodeProjects\AIFA GT Set" --hyp-dir <local-hyp-dir> --out-dir <local-gitignored-dir>
```

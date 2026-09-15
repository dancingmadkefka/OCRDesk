# ocrgrade

`ocrgrade` is a deterministic, structure-aware grader for image-to-HTML OCR/VLM
output. It compares a model's HTML transcription of a document against curated
ground truth (GT) and produces a tiered pass/fail verdict plus a numeric
`display_score`, backed by table structure (TEDS), financial-token exact match,
label/value grouping, and five critical correctness assertions - not a plain
text diff.

It is pure Python 3.13 (stdlib + a short list of packages, see
`requirements-grader.txt`). No LLM calls, no network, no GPU: every check is a
deterministic function of the GT and hypothesis HTML.

The full design rationale (canonicalization rules, the scoring formula, tier
ceilings, assertion definitions) lives in `docs/grader-plan.md` while the
implementation is in progress; once it ships, the enduring parts of that plan
fold into this file.

## How the pieces fit together

```
GT html ──┐                                    ┌── cases/<id>.json
          ├─► canonicalize.build_document ──►   │
hyp raw ──┘         (via roles.yaml)      scoring.score_document
   ▲                                             │
   └── markdown.to_html (html/markdown/plain)    ▼
                                            scoring.rollup ──► summary.json
                                                              leaderboard.csv
                                                              report.html
```

A `Sidecar` (`<case>.meta.json`, see "Sidecar and the annotate workflow" below)
travels alongside the GT and supplies the ground-truth facts that cannot be
recovered from the GT's HTML alone - which category the document is, which
table cells are the totals a wrong answer must not move, which sections must
survive verbatim, and so on.

## Feeding the grader

`ocrgrade score` accepts exactly one hypothesis source:

### 1. AI Financial Advisor's `html_vlm_*.json`

The output of `backend/benchmark/html_vlm_eval.py` in the AI Financial Advisor
repo. Pass it with `--corpus <AIFA GT Set> --aifa-results <path to html_vlm_*.json>`.

Per case, the grader reads `hypothesis_raw` when present (the model's
unprocessed output - may be markdown or plain text, and is run through
`markdown.to_html`), falling back to `hypothesis_html`
(already HTML-wrapped by the harness, so it is used as-is). The format hint is
sniffed from the content; when the sniff says plain text but the case (or run)
declares `output_form: markdown|html`, the declared form wins, because the harness
knows the prompt's contract. A case whose
`status` is not `"ok"` is graded as a harness failure (CATASTROPHIC tier, all
metrics null) rather than an empty transcription error. Two cheap catastrophic
detectors also run on every case: runaway or near-empty output (hypothesis text
longer than 5x or shorter than 0.1x the GT text) and CER above 0.8; both give
`CATASTROPHIC` with the reason in `errors`, so a model that loops never earns a
negative or misleading score. Run-level metadata
(`model`, `quant`, `prompt`, `sampling`) is read from the JSON's top-level
fields and carried into `summary.json` / `leaderboard.csv`.

### 2. A hypothesis directory

`--hyp-dir <dir>`, one file per case: `<case-id>.html`, `<case-id>.md`,
`<case-id>.txt`, or `<dir>/<case-id>/hyp.html`. The extension picks the hint
(`.md` → markdown, `.txt` → plain, otherwise html). A case with no matching
file scores as an error (no hypothesis provided), not a silent skip. The
run's `model` label defaults to the hyp-dir's own folder name - pass
`--run-id` explicitly for a more descriptive one.

### 3. OCRDesk's own output

`--ocrdesk-images-dir <images dir> --model <name>`. OCRDesk's images directory
is flat and already holds GT next to the images (`<stem>_ground_truth.html`)
and per-model candidates (`results/<stem>/<model>.html`, see `backend/ocr_core.py`),
so it doubles as the corpus - `--corpus` is not needed (and is ignored) in this
mode. `case_id` is the image stem.

**Note on `--corpus`:** it is required for sources 1 and 2 (an AIFA GT Set-style
corpus: one folder per case, holding a same-stem image + GT, e.g.
`case-014/hotel-invoice.jpg` + `case-014/hotel-invoice.html`; the image is
optional so a GT-only synthetic corpus - like `tests/grader/fixtures/` - works
too), and not accepted/needed for source 3. This departs slightly from a
literal reading of `docs/grader-plan.md` section 6's argument grouping, which
would make `--corpus` unconditionally required; seeding a corpus flag that
would just have to repeat `--ocrdesk-images-dir`'s own path had no upside.

## Sidecar and the `annotate` workflow

A `Sidecar` (schema v1) captures the facts about one case that a heuristic
cannot safely infer from GT HTML alone - or that scoring should not have to
re-derive on every run. It is stored as `<gt-stem>.meta.json`, next to the GT,
and is **never committed to the repo** (see `.gitignore`) - the real corpus
lives outside this repo, and sidecars for it stay wherever the corpus lives.
The exception is the synthetic fixtures under `tests/grader/fixtures/`, whose
sidecars (`sidecar.meta.json`) are committed as test data.

Workflow:

1. `python -m ocrgrade annotate --corpus <dir> [--manifest ocr_manifest.json]`
   walks the corpus, and for every case that does not already have a
   **confirmed** sidecar, derives one from the GT html (category from the
   manifest's `document_type` when the case has one - the manifest is curated, so
   it is the prior that wins; a keyword regex over the GT text only fills in when
   the manifest says nothing; AIFA's `{"cases": [{"id": ...}]}` manifest shape
   is understood as well as flat lists and dicts; locale from an IBAN country
   code or the manifest's `language`;
   currency/decimal separator/VAT-letter scheme from the GT's financial
   tokens; required sections from `section_header`-role elements; critical
   fields from `total_value`-role cells; label/value pairs and line-item
   column schema from the canonicalized `Document`). Every derived sidecar has
   `confirmed: false`.
2. It writes `annotations_review.csv` at the corpus root, one row per case
   (`case_id, category, category_secondary, locale, currency, decimal_sep,
   vat_letter_scheme, n_critical_fields, n_required_sections,
   n_label_value_pairs, confirmed, notes`) - open it, fix any wrong
   heuristic guesses, and flip `confirmed` to `true` once a case's sidecar is
   trustworthy (edit the JSON directly; the CSV is a review aid, not the
   source of truth).
3. `score` reads whatever sidecar exists (or schema defaults if none), and
   marks the result `provisional: true` whenever `confirmed` is `false` - so a
   leaderboard built before annotation finishes still runs, with everything
   flagged as provisional.

Re-running `annotate` never overwrites a sidecar that is already `confirmed`,
so it is safe to re-run after adding new cases to a corpus.

## Output files

`score` (and `shadow`, which is `score` plus one extra file) writes to
`<out-dir>/<run-id>/` (`run-id` defaults to `YYYYmmdd-HHMMSS_<model-safe>`):

- `cases/<case-id>.json` - one case's full result: tier, `display_score`,
  every metric, every assertion's pass/fail and detail, `archival_safe`,
  `provisional`, and any errors.
- `summary.json` - the run's rollup: pass/catastrophic/gate rates, macro `Q`,
  per-category macro/micro scores, the headline display score.
- `leaderboard.csv` - one row (this run) with the fixed columns plus one
  `<category>_display_score` column per category seen, sorted alphabetically.
- `report.html` - a static, self-contained page: the leaderboard row plus a
  table of every case (id, category, tier, display_score, archival_safe,
  failing assertion ids, provisional). It never re-renders GT/hypothesis
  documents - use OCRDesk's own compare view for that.

`shadow` additionally writes `shadow_expected.json`, mapping `case_id` →
`{tier, display_score, failed_assertions}`, for the operator's own before/after
regression tracking against the real corpus. It is local and gitignored -
never generated in CI.

`rank --summary a.json --summary b.json --out leaderboard.csv` merges any
number of existing `summary.json` files into one leaderboard, sorted by
`scoring.rank_key` (best first): catastrophic rate, then gate-pass rate, then
macro `Q`, worst-category `Q`, macro presentation score (unused in this MVP),
throughput, peak VRAM.

## Exit codes

| Command | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `annotate` | ok | usage | corpus not found / no cases | - | - |
| `score` / `shadow` | all cases scored | usage (missing `--corpus`/`--model` where required, or a bad `argparse` invocation) | no cases discovered, or a results/hyp-dir/images-dir path does not exist | at least one case raised an uncaught exception (a bug - distinct from a legitimate `CATASTROPHIC` tier, which is exit 0) | - |
| `rank` | ok | usage | a `--summary` file does not exist | - | - |
| `fixtures-check` | every fixture reproduced its `expected.json` | - | - | - | at least one fixture mismatched, or the fixtures directory was missing/empty (a CI gate failure, not a vacuous pass) |

## Running the tests

```powershell
cd "C:\Users\daniel\VSCodeProjects\ocr-benchmark-grader"
.\.venv\Scripts\python.exe -m pytest tests/grader -q -k "not shadow"   # CI-safe subset
.\.venv\Scripts\python.exe -m ocrgrade fixtures-check                  # golden-fixture gate
```

`tests/grader/test_sidecar.py`, `test_inputs.py`, `test_cli.py` and
`test_fixtures.py` (Scope C) run standalone, against tiny stand-ins for
`canonicalize`/`markdown`/`scoring`/`roles` (see `tests/grader/conftest.py`'s
`fake_pipeline` fixture) - they do not need those modules to exist or be
correct. `python -m ocrgrade fixtures-check` exercises the real pipeline
end-to-end and only passes once those modules are implemented and wired into
`cli.py` (`docs/grader-plan.md` Stage 2).

`shadow` against the real, non-public corpus is manual, local-only, and never
part of CI:

```powershell
.\.venv\Scripts\python.exe -m ocrgrade shadow --corpus "C:\Users\daniel\VSCodeProjects\AIFA GT Set" --hyp-dir <local-hyp-dir> --out-dir <local-gitignored-dir>
```

## CLI usage

```
python -m ocrgrade annotate --corpus DIR [--manifest ocr_manifest.json]

python -m ocrgrade score --corpus DIR --aifa-results FILE --out-dir DIR [--run-id ID]
python -m ocrgrade score --corpus DIR --hyp-dir DIR --out-dir DIR [--run-id ID]
python -m ocrgrade score --ocrdesk-images-dir DIR --model NAME --out-dir DIR [--run-id ID]

python -m ocrgrade rank --summary FILE [--summary FILE ...] --out FILE

python -m ocrgrade shadow --corpus DIR --hyp-dir DIR --out-dir DIR [--run-id ID]

python -m ocrgrade fixtures-check [--fixtures-dir tests/grader/fixtures]
```

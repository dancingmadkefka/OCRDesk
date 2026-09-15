# Design decisions and reasoning

This document records **why** OCRDesk works the way it does: prompt rules, ground truth conventions, compare UI, and boundaries between “document content” and “app presentation.” It is meant for future maintainers or agents starting a fresh conversation without re-deriving these choices.

---

## What this project is

A **browser-based audit and benchmark workspace** for vision-language OCR, not a CLI wrapper. You compare model HTML output against document photos and human-curated ground truth (GT) across a local corpus of `*.ocr_ready.jpg` images.

**Stack:** FastAPI (`backend/`) serves API + built `frontend/dist`; React workspace for side-by-side review, batch OCR, and inline GT editing.

**Run:** `python run.py` → http://127.0.0.1:8877

---

## Core principle: separate document content from app chrome

The most important architectural decision:

| Layer | Responsibility | Stored in GT / model files? |
|-------|----------------|-----------------------------|
| **Document content** | Text, tables, columns, fonts that appear on the paper | Yes |
| **App presentation** | Grey page background, centered card, box-shadow, “nice browser preview” | No |

Early GTs and model outputs sometimes included grey backgrounds, flex-centered cards, and shadows copied from hand-built HTML previews. That helped screenshots but hurt:

- **Side-by-side compare** (white receipt photo vs grey-framed HTML)
- **Fair scoring** (models fail on CSS they never saw on paper)
- **Downstream apps** (every consumer must strip fake chrome or accept inconsistency)

**Decision:** OCR output and GT describe the **document**. The compare viewer adds presentation at render time only (`frontend/src/iframeMeasure.ts`, `useAutoFrame.ts`, compare pane CSS).

Document-level styling **is** content when it exists on the source (Flo Gas purple headers, hotel invoice overlay layout, form structure). Page-level browser chrome is not.

---

## OCR output format: HTML in files, not markdown

**Decision:** Models return **HTML only**—`<!DOCTYPE html>` … `</html>`, no markdown fences, no preamble.

**Reasoning:**

- Corpus GT is HTML (or markdown converted for display).
- Tables, column alignment, and typography need structure; plain text loses layout.
- Fences (` ```html `) were a recurring model failure; backend and frontend strip them (`strip_fences` / `stripFences`) but the prompt forbids them.

**Inference params:** `max_tokens` and `temperature` were removed from API calls; providers use their defaults. The benchmark should not tune generation knobs unless we explicitly study them.

---

## SYSTEM_PROMPT design (`backend/ocr_core.py`)

The prompt evolved from a minimal CLI version into rules tuned for this corpus. Key choices:

### Transcription fidelity

- Reproduce text exactly: no paraphrase, no invented accents, no “corrected” French/German.
- Thermal and dot-matrix printers often lack é, è, ü, etc. → transcribe **visible ASCII** (`Societe`, `PASSIONNE`, `C.PROF.`).
- `[Signature]` / `[Illegible]` for unreadable ink (not normalized spelling).

**Why:** Scoring “OCR quality” requires measuring transcription errors, not post-hoc language normalization. Invented accents are a common model failure mode on receipts.

### Layout: semantic columns, not fake whitespace

- Tabular content uses `<table>` with separate `<td>` per column.
- Do **not** preserve column alignment with spaces, tabs, or `&nbsp;` chains inside one cell.

**Why:** HTML should encode structure; whitespace alignment is fragile and ungradable.

### Typography tiers (three classes)

1. **Printed proportional** — Arial-style body, labels, form structure.
2. **Printed monospace** — whole thermal receipt / dot-matrix blocks, line items, IDs (not only serial numbers).
3. **Handwritten** — pen/cursive/signatures only (Segoe Print–style), not every typed filled field.

**Why:** The corpus mixes receipts, tax forms, bills, and handwritten annotations. Typography is part of fidelity; monospace-on-receipt was initially under-specified (only IDs), which mis-scored Aldi-style receipts.

### No page chrome in model output

Explicit ban on grey backgrounds, flex centering, box-shadow, and card wrappers in OCR HTML. Minimal white `body` only.

**Why:** See separation principle above. Presentation belongs in the viewer.

---

## Ground truth files

**Location:** Alongside images, e.g. `receipt-2__…_ground_truth.html`.

**Detection:** `find_gt()` — exact filenames first, then loose stem match with `prefix + "_"` guard (so `fine` does not match `fine-screenshot`).

**Editing workflow:**

- **GT exists:** Workspace shows Original + GT only (model results hidden). Edit → Save updates GT directly.
- **No GT:** Dropdown shows model results. Edit → Save writes result file **and** creates GT.

Promote-to-GT UI was removed; saving when no GT already promotes. Backend `promote` API remains but is unused in UI.

**Receipt GT cleanup:** Thermal receipt GTs dropped embedded page chrome (grey bg, card shadow). Text fixes where printer lacked accents (`Societe`, `Invite(s)`, `C.PROF.`).

---

## Compare workspace UI

### Side-by-side panes

- 50/50 flex split; image vs HTML in iframes.
- Full HTML documents keep their `<head>` styles in iframe (`wrapForIframe`); fragments get a minimal shell (`htmlUtils.ts`).
- Scripts stripped; structure and CSS kept.

### Auto-frame presentation (viewer only)

**Problem we iterated on:**

1. Baking chrome into GT → bad for scoring and compare.
2. Removing chrome → receipts looked raw and left-aligned in a wide iframe.
3. Fixed narrow card (720px) → broke hotel invoice (overlay layout) and flo-gas (full-width bill).
4. Filename heuristics (`receipt` vs `bill`) → not sustainable.

**Current approach (generic):**

After iframe load, measure a **content bounding box** (`measureDocumentContentBBox`):

- Walk rendered elements (skip `html`/`body`).
- `width = maxRight - minLeft` so **centered** narrow blocks measure correctly (not skewed by left offset).

Then (`layoutForFrame`):

- Always show **grey surround + white card** for HTML in compare view.
- Iframe width = content width + `FRAME_PADDING` (40px), centered in pane.
- If content ≥ 94% of pane width → full-width card with wrap padding (full-bleed bills, invoices).

**Tuning knobs** (single place, not per-document):

- `FRAME_PADDING` — horizontal breathing room inside the card.
- `FULL_BLEED_RATIO` — when to stop shrinking the iframe.

**Files:** `frontend/src/iframeMeasure.ts`, `frontend/src/useAutoFrame.ts`, `ComparePane.tsx`, compare CSS (`with-auto-frame`).

HTML in GT/model files is **never modified** for framing; only the iframe wrapper changes.

### Image zoom

- Single shared zoom slider; both panes receive the same `zoom` value.
- Images: zoom transform on wrap, `transform-origin: top center`, image centered in pane.
- HTML: zoom on the **iframe** only (not the outer grey frame) so framing measurement stays consistent.
- Zoom resets to 100% when navigating to another image.

### Notifications

Save toasts auto-dismiss after 5 seconds, have a close button, and clear on navigation. They previously required a click on easy-to-miss green toast.

---

## API and routing lessons

### SPA routes vs catch-all

A greedy `GET /{path}` catch-all **shadowed** `PUT /api/results/...` and returned 405. Replaced with explicit SPA routes: `/`, `/settings`, `/workspace/{path}`.

### Restart server

- **Restart server** button → `POST /api/restart` → detached `backend/restart_server.py`.
- Restarts without a visible CMD window; logs go to `logs/server.log`.
- Frontend must be rebuilt (`npm run build`) before restart picks up UI changes.

---

## Grading (future) — implications of these decisions

No automated grader ships yet (`normalizeForCompare` only strips fences and normalizes newlines). When adding one:

| Strategy | Effect of current decisions |
|----------|----------------------------|
| **Text extraction** | Viewer chrome ignored; good. |
| **Structured / per-cell** | Table `<td>` columns matter; good. |
| **Raw HTML string diff** | Poor; avoid. |
| **Typography classes** | Optional fidelity dimension; prompt defines three tiers. |
| **Pixel/render diff** | Viewer must apply **same** presentation CSS to GT and model. |

**Recommendation:** Grade stored files (lean HTML), not iframe presentation. Optionally normalize away viewer-only attributes if ever injected.

---

## Rejected or superseded approaches

| Approach | Why abandoned |
|----------|----------------|
| Presentation chrome inside GT HTML | Pollutes scoring; duplicate with viewer; models won’t reproduce it reliably |
| Narrow card only if stem matches `receipt` / excludes `bill` | Breaks on new filenames; unmaintainable |
| Fixed 720px iframe for all “non-bill” docs | Crushes hotel overlay and wide quittances |
| Inner iframe `body` grey + flex (in `wrapForIframe`) | Didn’t fill iframe height; looked top-left aligned |
| `transform: scale` on whole html-wrap | Made HTML pane look zoomed differently from image pane |
| Promote button separate from Save | Confusing; promote ignored edits; workflow merged into Save |

---

## File map (quick reference)

| Area | Location |
|------|----------|
| OCR prompt & GT I/O | `backend/ocr_core.py` |
| API server | `backend/server.py` |
| Settings / images dir | `backend/config.py`, `settings.json` |
| Iframe wrap / fence strip | `frontend/src/htmlUtils.ts` |
| Content measurement & frame layout | `frontend/src/iframeMeasure.ts`, `useAutoFrame.ts` |
| Compare UI | `frontend/src/components/ComparePane.tsx`, `pages/Workspace.tsx` |
| Hidden server restart | `backend/restart_server.py` |

---

## Grader decisions (2026-09-15)

`ocrgrade/` adds a deterministic grader for OCRDesk/AI Financial Advisor HTML
output alongside the app, documented fully in `docs/grader.md` and
`docs/grader-plan.md`. The decisions below are the ones worth remembering
outside those docs, because they trade away something a more thorough design
would have kept:

- **Row-label alignment, not grid coordinates and not tree alignment.**
  `table_recognition_metric`'s `TEDS` returns a bare score with no node mapping,
  and the first real model outputs showed that exact `(table, row, col)`
  coordinates fail as soon as a model splits or merges a table, even with every
  value present. A critical value therefore counts as correctly placed when it
  sits in a hypothesis row whose label matches the GT row label word for word
  ("Closing Balance" never matches "Opening Balance", "Total" never matches
  "Subtotal"; a word of five or more characters tolerates a small OCR slip) and,
  when both tables have header rows, under a matching column header. Exact
  coordinates remain a fast path. The cell-content metric is row-aligned for the
  same reason. A same-row column swap is accepted on purpose. What is lost: a
  value duplicated into a second row with the same label is caught by A3, not A1.
  Alignment falls back, in order, to a hypothesis label-value pair and to the first
  amount following the label in prose, so a total a model kept as a paragraph still
  counts. Critical fields come from total cells in tables and from totals outside
  tables ('Total: EUR66.71'), each carrying its GT label in the sidecar; a table's last
  row only becomes a total when it names one.
  `tests/grader/fixtures/we_missing_row` shows the consequence: an omitted line
  item no longer cascades into aligned-cell failures; it fires the non-critical
  A8, caps the display score at 74, and keeps the case off the archival-safe list.
- **Two catastrophic detectors run before scoring.** Runaway or near-empty output
  (length ratio above 5x or below 0.1x of the GT text) and CER above 0.8 give
  `CATASTROPHIC` outright. Added after Qwen3.5 2B and PaddleOCR-VL produced
  looping outputs 10-18x the reference length that would otherwise have scored
  as negative numbers.
- **Tiers and score ceilings are a deliberate, coarse gate.** `CATASTROPHIC` /
  `REJECT` / `PASS` plus a `DisplayScore` ceiling (59 when any critical
  assertion fails, 74 when critical assertions pass but label/value or
  line-item grouping is imperfect) exist so one wrong total cannot be
  outscored by fluent prose elsewhere in the document. The exact ceilings and
  the `Content` sub-weights are first-pass numbers, expected to move after
  running `shadow` against the real corpus - they are not load-bearing for
  anything outside this repo yet.
- **No presentation scoring in this MVP.** Matching OCRDesk's own principle
  (see "Core principle: separate document content from app chrome" above),
  `ocrgrade` grades document content only. A presentation tie-breaker is
  named in the design as a future no-op hook; it does nothing here.
- **Fixtures are synthetic, not excerpts of the real corpus.** The 36-case
  ground-truth corpus this grader is built against never enters this public
  repo. Every fixture under `tests/grader/fixtures/` is hand-authored,
  invented merchants and numbers, chosen to exercise one worked-example
  failure mode each (wrong cell, duplicated total, detached VAT letter, ...).
  `fixtures-check` is the CI gate; `shadow` against the real corpus is manual,
  local, and never runs in CI.

## Open questions (not settled)

- **Default right pane when GT exists:** Currently resets to Original image on navigation; user may want GT on the right by default for review.
- **Auto “fit width” on load** for images (separate from manual zoom slider).
- **Grading implementation** and which dimensions (text, structure, typography) matter for the benchmark scorecard.
- **Whether post-receipt / formal quittances** should use document-level grey in GT (on paper) vs viewer-only—today lean GT + viewer frame.

Document decisions here when those are resolved.

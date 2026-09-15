"""Corpus discovery and hypothesis loading for the three supported input sources.

See docs/grader-plan.md sections 1 and 6.

Corpus layouts:
  - AIFA GT Set (and synthetic fixture corpora built the same way): one folder per
    case, `<corpus>/<case-id>/`, holding exactly one image and a same-stem GT
    (`<image-stem>.html`); the image is optional so a corpus can be built with GT
    only (`discover_corpus`).
  - OCRDesk: a single flat images directory holding `<stem>.ocr_ready.<ext>`,
    `<stem>_ground_truth.html`, and `results/<stem>/<model>.html`
    (`discover_ocrdesk_corpus`).

Hypothesis sources (`load_hypotheses`):
  - an AIFA `html_vlm_*.json` results file (`HtmlVlmCaseResult` per case, see
    AI Financial Advisor's backend/benchmark/html_vlm_eval.py);
  - a hypothesis directory, one file per case;
  - the OCRDesk `results/<stem>/<model>.html` layout.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

Hint = Literal["html", "markdown", "plain"]

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
GT_SUFFIXES = (".html", ".htm")
OCRDESK_GT_SUFFIX = "_ground_truth.html"


@dataclass(frozen=True)
class CaseFiles:
    case_id: str
    image_path: Path | None
    gt_path: Path
    sidecar_path: Path


@dataclass(frozen=True)
class HypothesisRecord:
    """One case's raw model output plus enough metadata to convert and score it.

    `status` mirrors the AIFA harness's per-case status: "ok" means `raw` holds
    usable output; "error"/"skipped" means the harness itself failed or skipped
    the case (no model output to grade), which `cli.py` turns into a CATASTROPHIC
    case result rather than attempting to build a Document from `raw`.
    """

    case_id: str
    raw: str
    hint: Hint
    status: Literal["ok", "error", "skipped"] = "ok"
    error: str | None = None
    runtime_seconds: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# --- AIFA GT Set corpus discovery -------------------------------------------


def discover_corpus(corpus_dir: Path) -> list[CaseFiles]:
    """One folder per case. GT = the image's same-stem `.html`, or the only
    `.html` in the folder when there is no image (synthetic fixture corpora).

    Folders that do not resolve to exactly one GT file are skipped, not fatal,
    mirroring AI Financial Advisor's `benchmark.harness.ocr_track.discover_cases`
    tolerance for malformed case folders.
    """
    cases: list[CaseFiles] = []
    if not corpus_dir.is_dir():
        return cases
    for case_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
        images = sorted(p for p in case_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        if len(images) > 1:
            continue  # ambiguous: ocr_track.discover_cases also refuses to guess
        image_path = images[0] if images else None
        gt_path = _find_gt_for_case(case_dir, image_path)
        if gt_path is None:
            continue
        sidecar_path = _sidecar_path_for(gt_path)
        cases.append(
            CaseFiles(case_id=case_dir.name, image_path=image_path, gt_path=gt_path, sidecar_path=sidecar_path)
        )
    return cases


def _find_gt_for_case(case_dir: Path, image_path: Path | None) -> Path | None:
    if image_path is not None:
        for suffix in GT_SUFFIXES:
            candidate = image_path.with_suffix(suffix)
            if candidate.is_file():
                return candidate
        return None
    explicit = case_dir / "gt.html"
    if explicit.is_file():  # synthetic fixture corpora keep gt.html next to hyp.html
        return explicit
    html_files = sorted(p for p in case_dir.iterdir() if p.is_file() and p.suffix.lower() in GT_SUFFIXES)
    return html_files[0] if len(html_files) == 1 else None


def _sidecar_path_for(gt_path: Path) -> Path:
    """<stem>.meta.json next to the GT (what `annotate` writes); when that file does not exist
    and the folder holds exactly one *.meta.json (fixture corpora use sidecar.meta.json), use it."""
    preferred = gt_path.with_name(f"{gt_path.stem}.meta.json")
    if preferred.is_file():
        return preferred
    candidates = sorted(p for p in gt_path.parent.glob("*.meta.json") if p.is_file())
    return candidates[0] if len(candidates) == 1 else preferred


# --- OCRDesk corpus discovery ------------------------------------------------


def discover_ocrdesk_corpus(images_dir: Path) -> list[CaseFiles]:
    """Flat OCRDesk layout: GT is `<stem>_ground_truth.html` at the folder root,
    case_id is the stem, and the (optional) image is `<stem>.ocr_ready.<ext>`.
    """
    cases: list[CaseFiles] = []
    if not images_dir.is_dir():
        return cases
    for gt_path in sorted(images_dir.glob(f"*{OCRDESK_GT_SUFFIX}")):
        stem = gt_path.name[: -len(OCRDESK_GT_SUFFIX)]
        image_path = _find_ocrdesk_image(images_dir, stem)
        # Not _sidecar_path_for(gt_path): Path.stem on "<stem>_ground_truth.html"
        # is "<stem>_ground_truth", not the case's own stem. Build it from `stem`
        # directly so the sidecar is "<stem>.meta.json", matching the AIFA-layout
        # convention of living next to the GT under the case's own identity.
        sidecar_path = gt_path.with_name(f"{stem}.meta.json")
        cases.append(CaseFiles(case_id=stem, image_path=image_path, gt_path=gt_path, sidecar_path=sidecar_path))
    return cases


def _find_ocrdesk_image(images_dir: Path, stem: str) -> Path | None:
    for suffix in sorted(IMAGE_SUFFIXES):
        candidate = images_dir / f"{stem}.ocr_ready{suffix}"
        if candidate.is_file():
            return candidate
    return None


def ocrdesk_result_path(images_dir: Path, case_id: str, model: str) -> Path:
    return images_dir / "results" / case_id / f"{model}.html"


# --- hypothesis loading -------------------------------------------------------


def load_hypotheses(
    *,
    case_ids: Iterable[str] | None = None,
    aifa_results: Path | None = None,
    hyp_dir: Path | None = None,
    ocrdesk_images_dir: Path | None = None,
    ocrdesk_model: str | None = None,
) -> tuple[list[HypothesisRecord], dict[str, Any]]:
    """Load hypotheses from exactly one of the three supported sources.

    Returns `(records, run_meta)`. `run_meta` carries whatever identifying
    metadata the source has available (model/quant/prompt/sampling for the AIFA
    source; best-effort labels for the other two) for `scoring.rollup` /
    `report.write_leaderboard_csv`.

    Raises `ValueError` on an invalid combination of arguments (cli.py maps this
    to an exit-code-2 usage error).
    """
    sources = [aifa_results is not None, hyp_dir is not None, ocrdesk_images_dir is not None]
    if sum(sources) != 1:
        raise ValueError("exactly one of aifa_results, hyp_dir, ocrdesk_images_dir must be given")

    if aifa_results is not None:
        return _load_aifa_results(aifa_results)

    ids = list(case_ids or [])
    if hyp_dir is not None:
        return _load_hyp_dir(hyp_dir, ids)

    assert ocrdesk_images_dir is not None
    if not ocrdesk_model:
        raise ValueError("ocrdesk_model is required when ocrdesk_images_dir is given")
    return _load_ocrdesk(ocrdesk_images_dir, ocrdesk_model, ids)


def _load_aifa_results(path: Path) -> tuple[list[HypothesisRecord], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    run_meta = {
        "model": payload.get("model"),
        "quant": payload.get("quant"),
        "prompt": payload.get("prompt"),
        "sampling_profile": payload.get("sampling_profile"),
        "sampling": payload.get("sampling"),
        "output_form": payload.get("output_form"),
    }
    records: list[HypothesisRecord] = []
    for case in payload.get("cases", []):
        case_id = case.get("case_id")
        status = case.get("status") or "ok"
        runtime_seconds = case.get("runtime_seconds")
        if status != "ok":
            records.append(
                HypothesisRecord(
                    case_id=case_id,
                    raw="",
                    hint="html",
                    status="error" if status == "error" else "skipped",
                    error=case.get("error"),
                    runtime_seconds=runtime_seconds,
                    meta=case,
                )
            )
            continue

        raw_field = case.get("hypothesis_raw")
        if raw_field:
            hint = _sniff_hint(raw_field)
            declared = case.get("output_form") or run_meta.get("output_form")
            if hint == "plain" and declared in ("markdown", "html"):
                hint = declared  # the harness knows the prompt's contract; sniffing only upgrades plain
            records.append(
                HypothesisRecord(
                    case_id=case_id,
                    raw=raw_field,
                    hint=hint,
                    status="ok",
                    runtime_seconds=runtime_seconds,
                    meta=case,
                )
            )
        else:
            records.append(
                HypothesisRecord(
                    case_id=case_id,
                    raw=case.get("hypothesis_html") or "",
                    hint="html",
                    status="ok",
                    runtime_seconds=runtime_seconds,
                    meta=case,
                )
            )
    return records, run_meta


def _load_hyp_dir(hyp_dir: Path, case_ids: list[str]) -> tuple[list[HypothesisRecord], dict[str, Any]]:
    records: list[HypothesisRecord] = []
    for case_id in case_ids:
        candidate = _find_hyp_file(hyp_dir, case_id)
        if candidate is None:
            records.append(
                HypothesisRecord(
                    case_id=case_id,
                    raw="",
                    hint="html",
                    status="error",
                    error=f"no hypothesis file found for case {case_id!r} under {hyp_dir}",
                )
            )
            continue
        raw = candidate.read_text(encoding="utf-8", errors="replace")
        records.append(HypothesisRecord(case_id=case_id, raw=raw, hint=_hint_for_extension(candidate.suffix), status="ok"))
    run_meta = {"model": hyp_dir.name, "quant": None, "prompt": None}
    return records, run_meta


def _find_hyp_file(hyp_dir: Path, case_id: str) -> Path | None:
    for suffix in (".html", ".md", ".txt"):
        candidate = hyp_dir / f"{case_id}{suffix}"
        if candidate.is_file():
            return candidate
    nested = hyp_dir / case_id / "hyp.html"
    if nested.is_file():
        return nested
    return None


def _load_ocrdesk(images_dir: Path, model: str, case_ids: list[str]) -> tuple[list[HypothesisRecord], dict[str, Any]]:
    records: list[HypothesisRecord] = []
    for case_id in case_ids:
        hyp_path = ocrdesk_result_path(images_dir, case_id, model)
        if not hyp_path.is_file():
            records.append(
                HypothesisRecord(
                    case_id=case_id,
                    raw="",
                    hint="html",
                    status="error",
                    error=f"no OCRDesk result for model {model!r} at {hyp_path}",
                )
            )
            continue
        raw = hyp_path.read_text(encoding="utf-8", errors="replace")
        records.append(HypothesisRecord(case_id=case_id, raw=raw, hint="html", status="ok"))
    run_meta = {"model": model, "quant": None, "prompt": None}
    return records, run_meta


# --- hint detection -----------------------------------------------------------


def _hint_for_extension(suffix: str) -> Hint:
    suffix = suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix == ".txt":
        return "plain"
    return "html"


_FENCE_RE = re.compile(r"^```(\w*)\s*\r?\n([\s\S]*?)\r?\n?```\s*$")
_HTML_SNIFF_RE = re.compile(
    r"<!DOCTYPE\s+html|<html[\s>]|<body[\s>]|<table[\s>]|<div[\s>]|<p[\s>]|<h[1-6][\s>]", re.I
)
_MD_TABLE_ROW_RE = re.compile(r"^\s{0,3}\|.*\|\s*$", re.M)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)


def _sniff_hint(raw: str) -> Hint:
    """Classify raw model output that has no filename to key a hint off of (the
    forward-looking AIFA `hypothesis_raw` field). Fence *stripping* is
    `markdown.to_html`'s job, not ours - this only peeks inside a fence to decide
    what the content actually is.
    """
    text = raw.strip()
    fence = _FENCE_RE.match(text)
    tag = fence.group(1).lower() if fence else ""
    body = fence.group(2) if fence else text

    if tag in ("html", "htm"):
        return "html"
    if tag in ("md", "markdown"):
        return "markdown"
    if _HTML_SNIFF_RE.search(body):
        return "html"
    if _MD_TABLE_ROW_RE.search(body) or _MD_HEADING_RE.search(body):
        return "markdown"
    return "plain"

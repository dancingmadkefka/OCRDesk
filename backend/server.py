"""FastAPI server for the OCR benchmark browser app."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import load_settings, public_settings, save_settings
from backend.ocr_core import (
    SYSTEM_PROMPT,
    find_gt,
    image_summary,
    list_images,
    list_results,
    parse_refine_response,
    promote_to_gt,
    read_gt_content,
    result_path,
    run_ocr,
    run_refinement,
    save_result,
    save_ground_truth,
    strip_fences,
)

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="OCR Benchmark")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8877",
        "http://localhost:8877",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict[str, Any]] = {}


def _images_dir() -> Path:
    return Path(load_settings()["images_dir"])


def _resolve_under(base: Path, rel: str) -> Path:
    base_resolved = base.resolve()
    resolved = (base / rel).resolve()
    if base_resolved != resolved and base_resolved not in resolved.parents:
        raise HTTPException(403, "Path traversal denied")
    return resolved


def _image_path_map(images_dir: Path) -> dict[str, Path]:
    return dict(list_images(images_dir))


def _require_image_stem(images_dir: Path, stem: str) -> dict[str, Path]:
    images = _image_path_map(images_dir)
    if stem not in images:
        raise HTTPException(404, "Image not found")
    return images


def _default_model_for_backend(backend: str, settings: dict) -> str | None:
    if backend == "openai":
        return settings.get("default_openai_model", "gpt-4o")
    if backend == "anthropic":
        return settings.get("default_anthropic_model", "claude-sonnet-4-20250514")
    return None


# ── Settings ──────────────────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    images_dir: str | None = None
    lm_studio_url: str | None = None
    lm_studio_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    default_openai_model: str | None = None
    default_anthropic_model: str | None = None
    ocr_delay_seconds: float | None = None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/restart")
def restart_server():
    from backend.restart_server import _win_creationflags

    helper = ROOT / "backend" / "restart_server.py"
    subprocess.Popen(
        [sys.executable, str(helper)],
        cwd=ROOT,
        creationflags=_win_creationflags(hidden=True),
    )
    return {"status": "restarting"}


@app.get("/api/settings")
def get_settings():
    return public_settings(load_settings())


@app.put("/api/settings")
def update_settings(body: SettingsUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    # Don't overwrite keys with empty string unless explicitly clearing
    for key in ("openai_api_key", "anthropic_api_key", "lm_studio_key"):
        if key in updates and updates[key] == "":
            del updates[key]
    data = save_settings(updates)
    return public_settings(data)


@app.get("/api/system-prompt")
def get_system_prompt():
    return {"prompt": SYSTEM_PROMPT}


# ── Dashboard ─────────────────────────────────────────────────────────────────


@app.get("/api/summary")
def get_summary():
    images_dir = _images_dir()
    if not images_dir.is_dir():
        raise HTTPException(400, f"Images directory not found: {images_dir}")
    return image_summary(images_dir)


@app.get("/api/images")
def get_images():
    return get_summary()


@app.get("/api/images/{stem}")
def get_image_detail(stem: str):
    images_dir = _images_dir()
    images = _require_image_stem(images_dir, stem)

    gt = find_gt(images_dir, stem)
    gt_info = None
    if gt:
        raw, rendered = read_gt_content(gt)
        gt_info = {"file": gt.name, "raw": raw, "html": rendered}

    models = list_results(images_dir, stem)
    model_contents = {}
    for m in models:
        p = result_path(images_dir, stem, m)
        model_contents[m] = p.read_text(encoding="utf-8")

    all_stems = [s for s, _ in list_images(images_dir)]
    idx = all_stems.index(stem)

    return {
        "stem": stem,
        "image_file": images[stem].name,
        "has_gt": gt is not None,
        "ground_truth": gt_info,
        "models": models,
        "model_contents": model_contents,
        "prev_stem": all_stems[idx - 1] if idx > 0 else None,
        "next_stem": all_stems[idx + 1] if idx < len(all_stems) - 1 else None,
        "index": idx + 1,
        "total": len(all_stems),
    }


@app.get("/api/files/image/{stem}")
def serve_image(stem: str):
    images_dir = _images_dir()
    images = _require_image_stem(images_dir, stem)
    return FileResponse(images[stem], media_type="image/jpeg")


# ── OCR jobs ──────────────────────────────────────────────────────────────────


class OcrJobRequest(BaseModel):
    backend: str = Field(..., pattern="^(lmstudio|openai|anthropic)$")
    model: str | None = None
    stems: list[str]
    overwrite: bool = False


def _process_one(stem: str, req: OcrJobRequest, settings: dict) -> dict:
    images_dir = Path(settings["images_dir"])
    images = _image_path_map(images_dir)
    if stem not in images:
        return {"stem": stem, "status": "error", "error": "Image not found"}

    from backend.ocr_core import safe_name

    model = req.model or _default_model_for_backend(req.backend, settings)
    check_name = safe_name(model) if model else None

    if not req.overwrite and check_name:
        if result_path(images_dir, stem, check_name).exists():
            return {"stem": stem, "status": "skipped", "reason": "exists", "model": check_name}

    try:
        model_name, output = run_ocr(
            req.backend,
            images[stem],
            images_dir,
            model=model,
            lm_url=settings["lm_studio_url"],
            lm_key=settings["lm_studio_key"],
            openai_key=settings["openai_api_key"],
            anthropic_key=settings["anthropic_api_key"],
        )
        if not req.overwrite and result_path(images_dir, stem, model_name).exists():
            return {
                "stem": stem,
                "status": "skipped",
                "reason": "exists",
                "model": model_name,
                "message": f"Result already exists for {model_name} — enable Overwrite to replace",
            }

        out = save_result(images_dir, stem, model_name, output)
        return {"stem": stem, "status": "ok", "model": model_name, "path": str(out)}
    except Exception as e:
        return {"stem": stem, "status": "error", "error": str(e)}


async def _run_job(job_id: str, req: OcrJobRequest):
    job = _jobs[job_id]
    settings = load_settings()
    delay = float(settings.get("ocr_delay_seconds", 2.0))

    for i, stem in enumerate(req.stems):
        job["current"] = stem
        job["done"] = i
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _process_one, stem, req, settings)
        job["results"].append(result)
        if result["status"] == "ok" and i < len(req.stems) - 1 and req.backend != "manual":
            await asyncio.sleep(delay)

    job["done"] = len(req.stems)
    job["status"] = "completed"
    job["current"] = None


@app.post("/api/ocr/jobs")
async def create_ocr_job(req: OcrJobRequest):
    images_dir = _images_dir()
    if not images_dir.is_dir():
        raise HTTPException(400, f"Images directory not found: {images_dir}")
    if not req.stems:
        raise HTTPException(400, "No images selected")

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "total": len(req.stems),
        "done": 0,
        "current": req.stems[0] if req.stems else None,
        "results": [],
        "backend": req.backend,
        "model": req.model,
        "started_at": time.time(),
    }
    asyncio.create_task(_run_job(job_id, req))
    return {"job_id": job_id}


@app.get("/api/ocr/jobs/{job_id}")
def get_ocr_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@app.get("/api/lmstudio/models")
def list_lmstudio_models():
    settings = load_settings()
    from openai import OpenAI

    try:
        client = OpenAI(base_url=settings["lm_studio_url"], api_key=settings["lm_studio_key"])
        models = client.models.list()
        return {"models": [m.id for m in models.data]}
    except Exception as e:
        raise HTTPException(502, f"Cannot reach LM Studio: {e}") from e


# ── Manual paste & promote ────────────────────────────────────────────────────


class ManualSaveRequest(BaseModel):
    stem: str
    html: str
    overwrite: bool = True


@app.post("/api/ocr/manual")
def save_manual(req: ManualSaveRequest):
    images_dir = _images_dir()
    _require_image_stem(images_dir, req.stem)
    if not req.overwrite and result_path(images_dir, req.stem, "manual").exists():
        raise HTTPException(409, "Manual result already exists")
    out = save_result(images_dir, req.stem, "manual", req.html)
    return {"model": "manual", "path": str(out)}


class PromoteRequest(BaseModel):
    stem: str
    model: str
    html: str | None = None


@app.post("/api/promote")
def promote(req: PromoteRequest):
    images_dir = _images_dir()
    _require_image_stem(images_dir, req.stem)
    try:
        dst = promote_to_gt(images_dir, req.stem, req.model, content=req.html)
        return {"ground_truth": dst.name, "path": str(dst)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


class SaveHtmlRequest(BaseModel):
    html: str


@app.put("/api/results/{stem}/{model}")
def update_result(stem: str, model: str, body: SaveHtmlRequest):
    images_dir = _images_dir()
    _require_image_stem(images_dir, stem)
    if model not in list_results(images_dir, stem):
        raise HTTPException(404, f"Result not found: {model}")
    content = strip_fences(body.html)
    out = save_result(images_dir, stem, model, content)
    return {"model": model, "path": str(out)}


@app.put("/api/ground-truth/{stem}")
def update_ground_truth(stem: str, body: SaveHtmlRequest):
    images_dir = _images_dir()
    _require_image_stem(images_dir, stem)
    content = strip_fences(body.html)
    out = save_ground_truth(images_dir, stem, content)
    return {"ground_truth": out.name, "path": str(out)}


# ── Refinement (self-critique + user feedback) ────────────────────────────────


class FeedbackBBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class FeedbackItem(BaseModel):
    excerpt: str | None = None
    comment: str | None = None
    bbox: FeedbackBBox | None = None


class RefineRequest(BaseModel):
    stem: str
    backend: str = Field(..., pattern="^(lmstudio|openai|anthropic)$")
    model: str | None = None
    prev_html: str
    screenshot_b64: str | None = None  # base64 or data: URL of rendered HTML screenshot
    feedbacks: list[FeedbackItem] | None = None


@app.post("/api/refine")
def refine(req: RefineRequest):
    images_dir = _images_dir()
    images = _require_image_stem(images_dir, req.stem)

    fb_list = None
    if req.feedbacks:
        fb_list = [
            {
                "excerpt": f.excerpt,
                "comment": f.comment,
                "bbox": f.bbox.model_dump() if f.bbox else None,
            }
            for f in req.feedbacks
        ]

    settings = load_settings()
    try:
        model_name, output = run_refinement(
            req.backend,
            images[req.stem],
            req.prev_html,
            model=req.model,
            lm_url=settings.get("lm_studio_url"),
            lm_key=settings.get("lm_studio_key"),
            openai_key=settings.get("openai_api_key"),
            anthropic_key=settings.get("anthropic_api_key"),
            screenshot_b64=req.screenshot_b64,
            feedbacks=fb_list,
        )
    except Exception as e:
        raise HTTPException(502, f"Refinement failed: {e}") from e

    satisfied, cleaned, note, improvements, parse_error = parse_refine_response(output)
    screenshot_kb = (
        round(len(req.screenshot_b64 or "") / 1024) if req.screenshot_b64 else 0
    )
    import logging

    logging.getLogger("uvicorn.error").info(
        "refine stem=%s model=%s satisfied=%s raw_chars=%d output_chars=%d screenshot_kb=%d%s",
        req.stem,
        model_name,
        satisfied,
        len(output or ""),
        len(cleaned or ""),
        screenshot_kb,
        f" error={parse_error!r}" if parse_error else "",
    )

    return {
        "model": model_name,
        "output": cleaned if not satisfied else (note or ""),
        "satisfied": satisfied,
        "note": note if satisfied else None,
        "improvements": improvements,
        "raw": output,
        "error": parse_error,
        "raw_chars": len(output or ""),
        "output_chars": len(cleaned or ""),
    }


# ── Static frontend ───────────────────────────────────────────────────────────

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"


def _spa_index():
    if not FRONTEND_INDEX.exists():
        raise HTTPException(404)
    return FileResponse(FRONTEND_INDEX)


if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def spa_root():
        return _spa_index()

    @app.get("/settings")
    def spa_settings():
        return _spa_index()

    @app.get("/workspace/{_rest:path}")
    def spa_workspace(_rest: str):
        return _spa_index()

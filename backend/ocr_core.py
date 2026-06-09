"""Core OCR benchmark logic — shared by API server."""

from __future__ import annotations

import base64
import html as html_mod
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from anthropic import Anthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from openai import OpenAI

SYSTEM_PROMPT = """\
You are an expert OCR system. Convert the document image into clean, semantically correct HTML.

Output format (strict):
- Return ONLY valid HTML. Start with <!DOCTYPE html> and end with </html>.
- Do NOT wrap output in markdown fences (no ```html). No preamble or explanation.

Transcription:
- Reproduce all visible text, numbers, symbols, and punctuation exactly as written.
- Do not paraphrase, summarize, omit, or expand abbreviations or acronyms.
- Preserve typos, casing, and original wording.
- Transcribe characters as printed — do not add accents, diacritics, or normalized punctuation absent from the source (e.g. Societe not Société, C.PROF. not C.PROF,).
- Thermal and dot-matrix printers often lack extended characters; reproduce visible ASCII only.
- For illegible signatures use [Signature]. For other unreadable text use [Illegible].

Page styling (strict):
- Do NOT add browser presentation chrome: grey page backgrounds, flex centering, box shadows, or card wrappers around the document.
- Use a minimal body (white background, modest padding). CSS should reflect typography and layout on the document, not a styled browser preview.

Layout & structure:
- Use semantic HTML (<h1>–<h6>, <p>, <ul>, <div>) for non-tabular content.
- For receipts, invoices, forms, itemized lists, or any multi-column layout, use <table> with <tr>/<td>.
- Split values into logical columns (<td>) — do NOT preserve column alignment with spaces, tabs, or chains of &nbsp; inside a single cell.
- Use empty cells <td></td> where a column has no value.
- Preserve visual hierarchy (titles, section headers, body, footnotes).
- Include a <style> block in <head> with CSS for borders, padding, alignment, and typography.

Typography (match the source; use CSS classes or equivalent inline styles):
- Printed proportional — labels, headings, body text, form structure:
  font-family: Arial, Helvetica, sans-serif;
- Printed monospace — thermal receipts, dot-matrix printouts, terminal/log output, receipt line items, aligned price columns, transaction IDs, serial numbers, barcodes:
  font-family: Consolas, Monaco, 'Courier New', monospace; font-size: 13px;
  (Apply to the whole receipt/table when machine-printed monospace layout is visible.)
- Handwritten — pen-filled fields, cursive annotations, signatures (not typed or printed fill):
  font-family: 'Segoe Print', 'Bradley Hand', 'Architects Daughter', cursive;
  font-style: italic; font-weight: bold; color: #1a237e;"""

REFINE_SYSTEM_PROMPT = """\
You are an expert OCR reviewer and corrector.

You will be shown:
- The original document image (photograph of the paper).
- (Optionally) a screenshot of a browser rendering of your previous HTML attempt.
- Your previous full HTML output (text).
- Optional structured user feedback: excerpts and/or numbered orange boxes on the render screenshot marking areas the user flagged, with comments explaining what is wrong or missing.

Your job:
- Compare the original image pixel-for-pixel against the previous HTML and its rendered screenshot.
- Check transcription fidelity (exact text, numbers, symbols, no additions/omissions, no normalized accents), layout (correct columns via <table> not &nbsp; spaces), structure (headings, lists, forms), and typography (proportional vs monospace vs handwritten styling as defined in the original rules).
- If the previous HTML is already accurate and complete (no meaningful errors visible), reply with exactly:
  [[SATISFIED]]
  You may add one short sentence of justification after it.
- Otherwise, output a complete corrected HTML document starting with <!DOCTYPE html> and ending with </html>. No markdown fences around the HTML.

Changelog (required for every response — place AFTER the HTML or after [[SATISFIED]], never inside the HTML):
[[IMPROVEMENTS]]
[
  {"area": "where on the page", "change": "what you changed or verified", "reason": "why — cite the image or user feedback"}
]
[[/IMPROVEMENTS]]
- Use valid JSON array inside the delimiters. One object per distinct change or verified area.
- Fields: "area" (short, e.g. "totals row", "header"), "change" (what was wrong/fixed or "verified correct"), "reason" (evidence from the image).
- If satisfied: list what you checked and why it is already correct (same JSON format).
- If you made corrections: list every meaningful fix with its reason.

Rules (same as original OCR):
- Reproduce all visible text exactly; preserve typos, casing, visible ASCII only on thermal/dot-matrix.
- Use semantic HTML + <table> for columns/lists/receipts; never fake alignment with spaces or &nbsp; chains inside one cell.
- Typography: printed proportional (Arial etc.), printed monospace for receipts/ledgers (Consolas etc.), handwritten cursive/italic only for actual pen on paper.
- NO browser preview chrome (no grey backgrounds, no centered cards, no box shadows). Minimal white body.
- Include a <style> block for the document's own typography and borders.

When correcting, fix only what the image + feedback show is wrong; do not "improve" content that is already correct."""

REFINE_USER_SUFFIX = (
    "Review against the image(s). If already correct, reply with [[SATISFIED]] plus the required [[IMPROVEMENTS]] block. "
    "Otherwise output corrected <!DOCTYPE html> ... </html> then the required [[IMPROVEMENTS]] block. "
    "Follow OCR rules. The improvements JSON is mandatory."
)

GT_MARKERS = ("ground_truth", "ground-truth", "groud-truth")


def results_dir(images_dir: Path) -> Path:
    return images_dir / "results"


def list_images(images_dir: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if not images_dir.is_dir():
        return out
    for p in sorted(images_dir.glob("*.ocr_ready.jpg")):
        stem = p.name.replace(".ocr_ready.jpg", "")
        out.append((stem, p))
    return out


def _gt_prefix(stem: str) -> str:
    return stem.rsplit("__", 1)[0] if "__" in stem else stem


def _gt_loose_match(gt_stem: str, image_stem: str) -> bool:
    """Match GT files where the hash suffix was omitted (e.g. aldi-receipt_ocr_groud-truth.md)."""
    prefix = _gt_prefix(image_stem)
    if gt_stem == prefix:
        return True
    # Require '_' after prefix so 'fine' does not match 'fine-screenshot…'
    return gt_stem.startswith(prefix + "_")


def find_gt(images_dir: Path, stem: str) -> Path | None:
    for name in [
        f"{stem}_ground_truth.html",
        f"{stem}_ground_truth.md",
        f"{stem}_ground-truth.html",
        f"{stem}_ground-truth.md",
        f"{stem}_ocr_groud-truth.md",
        f"{stem}_ocr_ground-truth.md",
    ]:
        p = images_dir / name
        if p.exists():
            return p

    for p in images_dir.iterdir():
        if p.is_file() and any(m in p.stem for m in GT_MARKERS):
            if _gt_loose_match(p.stem, stem):
                return p
    return None


def result_path(images_dir: Path, stem: str, model: str) -> Path:
    return results_dir(images_dir) / stem / f"{model}.html"


def _ensure_under(base: Path, path: Path) -> Path:
    base_resolved = base.resolve()
    resolved = path.resolve()
    if base_resolved != resolved and base_resolved not in resolved.parents:
        raise ValueError(f"Path escapes image folder: {path}")
    return resolved


def list_results(images_dir: Path, stem: str) -> list[str]:
    rdir = results_dir(images_dir) / stem
    if not rdir.is_dir():
        return []
    return sorted(
        f.stem for f in rdir.iterdir() if f.suffix.lower() == ".html" and f.stem != "compare"
    )


def encode_b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def mime_of(path: Path) -> str:
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(path.suffix.lower().lstrip("."), "image/jpeg")


def build_feedback_block(feedbacks: list[dict[str, str | dict]] | None) -> str:
    """Format user-provided feedback excerpts + comments for the model."""
    if not feedbacks:
        return ""
    has_boxes = any(isinstance(fb.get("bbox"), dict) for fb in feedbacks)
    lines: list[str] = ["User feedback on previous output (focus on these):"]
    if has_boxes:
        lines.append(
            "Orange numbered boxes on the render screenshot mark flagged areas (box number matches item number below)."
        )
    for i, fb in enumerate(feedbacks, 1):
        excerpt = (fb.get("excerpt") or "").strip() if isinstance(fb.get("excerpt"), str) else ""
        comment = (fb.get("comment") or "").strip() if isinstance(fb.get("comment"), str) else ""
        bbox = fb.get("bbox") if isinstance(fb.get("bbox"), dict) else None
        if bbox:
            lines.append(f"{i}. Flagged region (see box #{i} on render screenshot)")
        elif excerpt:
            lines.append(f"{i}. Selected excerpt: {excerpt!r}")
        if comment:
            lines.append(f"   Comment: {comment}")
        if not excerpt and not comment and not bbox:
            lines.append(f"{i}. (empty note)")
    lines.append("")
    return "\n".join(lines)


def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", s).strip("_")


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:html|HTML)?\s*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def looks_like_html(text: str) -> bool:
    """Heuristic: does this look like OCR HTML output?"""
    s = strip_fences(text).strip()
    if len(s) < 24:
        return False
    return bool(
        re.search(
            r"<!DOCTYPE\s+html|<html[\s>]|<body[\s>]|<table[\s>]|<div[\s>]|<p[\s>]|<h[1-6][\s>]",
            s,
            re.I,
        )
    )


def extract_html_output(text: str) -> str:
    """Pull the HTML document out of a model response (preamble / fences tolerated)."""
    t = strip_fences(text).strip()
    if not t:
        return ""
    if re.search(r"<!DOCTYPE\s+html", t, re.I):
        m = re.search(r"(<!DOCTYPE\s+html[\s\S]*?</html>)", t, re.I)
        if m:
            return m.group(1).strip()
    m = re.search(r"(<html[\s\S]*?</html>)", t, re.I)
    if m:
        return m.group(1).strip()
    return t


def _assistant_text(message: object) -> str:
    """Return the model's final answer only (message.content). Reasoning is discarded."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
            elif hasattr(block, "text") and getattr(block, "text", None):
                parts.append(str(block.text))
        return "\n".join(parts).strip()
    return ""


def _normalize_improvement(item: dict[str, Any]) -> dict[str, str] | None:
    change = str(item.get("change") or item.get("what") or "").strip()
    reason = str(item.get("reason") or item.get("why") or "").strip()
    area = str(item.get("area") or item.get("location") or item.get("section") or "").strip()
    if not change and not reason:
        return None
    return {"area": area, "change": change or "—", "reason": reason or "—"}


def strip_improvements_block(text: str) -> tuple[str, str | None]:
    """Return (text without the block, inner JSON/text or None)."""
    pattern = r"\[\[IMPROVEMENTS\]\]\s*([\s\S]*?)\s*\[\[/IMPROVEMENTS\]\]"
    m = re.search(pattern, text, re.I)
    if not m:
        return text, None
    inner = m.group(1).strip()
    stripped = re.sub(pattern, "", text, flags=re.I).strip()
    return stripped, inner


def parse_improvements_list(inner: str | None) -> list[dict[str, str]]:
    if not inner:
        return []

    # Direct JSON array
    try:
        data = json.loads(inner)
        if isinstance(data, list):
            out: list[dict[str, str]] = []
            for item in data:
                if isinstance(item, dict):
                    norm = _normalize_improvement(item)
                    if norm:
                        out.append(norm)
            if out:
                return out
    except json.JSONDecodeError:
        pass

    # JSON array embedded in extra text
    m = re.search(r"\[[\s\S]*\]", inner)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                out = []
                for item in data:
                    if isinstance(item, dict):
                        norm = _normalize_improvement(item)
                        if norm:
                            out.append(norm)
                if out:
                    return out
        except json.JSONDecodeError:
            pass

    # Line fallback: "- area | change | reason" or "CHANGE: ... REASON: ..."
    out = []
    for line in inner.splitlines():
        s = line.strip().lstrip("-•*").strip()
        if not s:
            continue
        area, change, reason = "", "", ""
        if "|" in s:
            parts = [p.strip() for p in s.split("|")]
            if len(parts) >= 3:
                area, change, reason = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                change, reason = parts[0], parts[1]
            else:
                change = parts[0]
        else:
            cm = re.search(r"change\s*:\s*(.+?)(?:\s+reason\s*:\s*(.+))?$", s, re.I)
            if cm:
                change = cm.group(1).strip()
                reason = (cm.group(2) or "").strip()
            else:
                change = s
        norm = _normalize_improvement({"area": area, "change": change, "reason": reason})
        if norm:
            out.append(norm)
    return out


def parse_refine_response(
    raw: str,
) -> tuple[bool, str, str | None, list[dict[str, str]], str | None]:
    """Return (satisfied, html_output, note, improvements, error_message)."""
    raw = (raw or "").strip()
    if not raw:
        return False, "", None, [], "Model returned an empty response."

    body, imp_inner = strip_improvements_block(raw)
    improvements = parse_improvements_list(imp_inner)

    satisfied = "[[SATISFIED]]" in body or "[[SATISFIED]]" in raw
    if satisfied:
        note = body.replace("[[SATISFIED]]", "").strip() or None
        return True, "", note, improvements, None

    html = extract_html_output(body)
    if looks_like_html(html):
        return False, html, None, improvements, None

    preview = body[:240].replace("\n", " ")
    if len(body) > 240:
        preview += "…"
    return (
        False,
        "",
        None,
        improvements,
        f"Model response did not contain usable HTML ({len(body)} chars). Preview: {preview!r}",
    )


def md_to_html(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if re.match(r"^#{1,6}\s", s):
            lvl = len(re.match(r"^(#+)", s).group(1))
            content = re.sub(r"^#+\s*", "", s)
            out.append(f"<h{lvl}>{html_mod.escape(content)}</h{lvl}>")
        elif s:
            out.append(s if "<" in s else f"<p>{html_mod.escape(s)}</p>")
    return "\n".join(out)


def read_gt_content(gt_path: Path) -> tuple[str, str]:
    """Return (raw_content, rendered_html)."""
    raw = gt_path.read_text(encoding="utf-8")
    if gt_path.suffix.lower() == ".html":
        return raw, raw
    return raw, md_to_html(raw)


def backend_lmstudio(img_path: Path, model: str | None, lm_url: str, lm_key: str) -> tuple[str, str]:
    client = OpenAI(base_url=lm_url, api_key=lm_key)
    if not model:
        models = client.models.list()
        if not models.data:
            raise RuntimeError("No models loaded in LM Studio")
        model = models.data[0].id
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_of(img_path)};base64,{encode_b64(img_path)}",
                        },
                    },
                    {"type": "text", "text": "Reproduce this document as HTML."},
                ],
            },
        ],
    )
    return safe_name(model), strip_fences(resp.choices[0].message.content or "")


def backend_openai(img_path: Path, model: str, api_key: str) -> tuple[str, str]:
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_of(img_path)};base64,{encode_b64(img_path)}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": "Reproduce this document as HTML."},
                ],
            },
        ],
    )
    return safe_name(model), strip_fences(resp.choices[0].message.content or "")


def backend_anthropic(img_path: Path, model: str, api_key: str) -> tuple[str, str]:
    if not HAS_ANTHROPIC:
        raise RuntimeError("anthropic package not installed")
    if not api_key:
        raise RuntimeError("Anthropic API key is not configured")
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_of(img_path),
                            "data": encode_b64(img_path),
                        },
                    },
                    {"type": "text", "text": "Reproduce this document as HTML."},
                ],
            }
        ],
    )
    return safe_name(model), strip_fences(resp.content[0].text)


def _b64_to_data_url(b64: str, mime: str = "image/png") -> str:
    b = b64.strip()
    if b.startswith("data:"):
        return b
    return f"data:{mime};base64,{b}"


def _parse_data_url_or_b64(s: str | None) -> tuple[str, str] | None:
    """Return (mime, b64_data) or None."""
    if not s:
        return None
    s = s.strip()
    if s.startswith("data:"):
        # data:image/png;base64,XXXX
        try:
            header, data = s.split(",", 1)
            mime = header.split(";")[0].split(":", 1)[1]
            return mime, data
        except Exception:
            return "image/png", s.split(",", 1)[-1]
    return "image/png", s


def backend_lmstudio_refine(
    img_path: Path,
    prev_html: str,
    model: str | None,
    lm_url: str,
    lm_key: str,
    screenshot_b64: str | None = None,
    feedbacks: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    client = OpenAI(base_url=lm_url, api_key=lm_key)
    if not model:
        models = client.models.list()
        if not models.data:
            raise RuntimeError("No models loaded in LM Studio")
        model = models.data[0].id

    fb_block = build_feedback_block(feedbacks)
    user_text = (
        "Original document image is the first image. "
        + ("A screenshot of your previous HTML render is the second image. " if screenshot_b64 else "")
        + f"\n\nPrevious HTML attempt:\n{prev_html}\n\n"
        + fb_block
        + REFINE_USER_SUFFIX
    )

    content: list[dict] = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_of(img_path)};base64,{encode_b64(img_path)}",
                "detail": "high",
            },
        }
    ]
    if screenshot_b64:
        mime, data = _parse_data_url_or_b64(screenshot_b64)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}})
    content.append({"type": "text", "text": user_text})

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": REFINE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    out = _assistant_text(resp.choices[0].message)
    return safe_name(model), out


def backend_openai_refine(
    img_path: Path,
    prev_html: str,
    model: str,
    api_key: str,
    screenshot_b64: str | None = None,
    feedbacks: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured")
    client = OpenAI(api_key=api_key)

    fb_block = build_feedback_block(feedbacks)
    user_text = (
        "Original document image is the first image. "
        + ("A screenshot of your previous HTML render is the second image. " if screenshot_b64 else "")
        + f"\n\nPrevious HTML attempt:\n{prev_html}\n\n"
        + fb_block
        + REFINE_USER_SUFFIX
    )

    content: list[dict] = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_of(img_path)};base64,{encode_b64(img_path)}",
                "detail": "high",
            },
        }
    ]
    if screenshot_b64:
        mime, data = _parse_data_url_or_b64(screenshot_b64)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}})
    content.append({"type": "text", "text": user_text})

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": REFINE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    out = _assistant_text(resp.choices[0].message)
    return safe_name(model), out


def backend_anthropic_refine(
    img_path: Path,
    prev_html: str,
    model: str,
    api_key: str,
    screenshot_b64: str | None = None,
    feedbacks: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    if not HAS_ANTHROPIC:
        raise RuntimeError("anthropic package not installed")
    if not api_key:
        raise RuntimeError("Anthropic API key is not configured")
    client = Anthropic(api_key=api_key)

    fb_block = build_feedback_block(feedbacks)
    user_text = (
        "Original document image is the first image. "
        + ("A screenshot of your previous HTML render is the second image. " if screenshot_b64 else "")
        + f"\n\nPrevious HTML attempt:\n{prev_html}\n\n"
        + fb_block
        + REFINE_USER_SUFFIX
    )

    content: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_of(img_path),
                "data": encode_b64(img_path),
            },
        }
    ]
    if screenshot_b64:
        mime, data = _parse_data_url_or_b64(screenshot_b64)
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        )
    content.append({"type": "text", "text": user_text})

    resp = client.messages.create(
        model=model,
        system=REFINE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    # Anthropic may return multiple blocks; prefer last text block with HTML
    text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text" and getattr(b, "text", None)]
    out = "\n".join(text_parts).strip() if text_parts else ""
    return safe_name(model), out


def run_ocr(
    backend: str,
    img_path: Path,
    images_dir: Path,
    *,
    model: str | None = None,
    lm_url: str | None = None,
    lm_key: str | None = None,
    openai_key: str | None = None,
    anthropic_key: str | None = None,
    manual_html: str | None = None,
) -> tuple[str, str]:
    """Run OCR and return (model_name, html_output). Does not save."""
    if backend == "lmstudio":
        model_name, output = backend_lmstudio(
            img_path,
            model,
            lm_url or os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1"),
            lm_key or os.getenv("LM_STUDIO_KEY", "lm-studio"),
        )
    elif backend == "openai":
        model_name, output = backend_openai(
            img_path,
            model or "gpt-4o",
            openai_key or os.getenv("OPENAI_API_KEY", ""),
        )
    elif backend == "anthropic":
        model_name, output = backend_anthropic(
            img_path,
            model or "claude-sonnet-4-20250514",
            anthropic_key or os.getenv("ANTHROPIC_API_KEY", ""),
        )
    elif backend == "manual":
        if not manual_html:
            raise RuntimeError("Manual paste requires HTML content")
        model_name, output = "manual", strip_fences(manual_html)
    else:
        raise RuntimeError(f"Unknown backend: {backend}")

    return model_name, output


def run_refinement(
    backend: str,
    img_path: Path,
    prev_html: str,
    *,
    model: str | None = None,
    lm_url: str | None = None,
    lm_key: str | None = None,
    openai_key: str | None = None,
    anthropic_key: str | None = None,
    screenshot_b64: str | None = None,
    feedbacks: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    """Run a refinement/critique pass. Returns (model_name, output).

    The output is either a full corrected HTML, or contains the [[SATISFIED]] sentinel
    (possibly with a trailing note). Caller decides how to interpret/save.
    """
    prev_html = strip_fences(prev_html)
    if backend == "lmstudio":
        return backend_lmstudio_refine(
            img_path,
            prev_html,
            model,
            lm_url or os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1"),
            lm_key or os.getenv("LM_STUDIO_KEY", "lm-studio"),
            screenshot_b64=screenshot_b64,
            feedbacks=feedbacks,
        )
    elif backend == "openai":
        return backend_openai_refine(
            img_path,
            prev_html,
            model or "gpt-4o",
            openai_key or os.getenv("OPENAI_API_KEY", ""),
            screenshot_b64=screenshot_b64,
            feedbacks=feedbacks,
        )
    elif backend == "anthropic":
        return backend_anthropic_refine(
            img_path,
            prev_html,
            model or "claude-sonnet-4-20250514",
            anthropic_key or os.getenv("ANTHROPIC_API_KEY", ""),
            screenshot_b64=screenshot_b64,
            feedbacks=feedbacks,
        )
    else:
        raise RuntimeError(f"Unknown backend for refinement: {backend}")


def save_result(images_dir: Path, stem: str, model_name: str, html: str) -> Path:
    out = _ensure_under(images_dir, result_path(images_dir, stem, model_name))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def save_ground_truth(images_dir: Path, stem: str, content: str) -> Path:
    """Save ground truth content, updating existing file or creating HTML."""
    gt = find_gt(images_dir, stem)
    dst = _ensure_under(images_dir, gt if gt else images_dir / f"{stem}_ground_truth.html")
    dst.write_text(content, encoding="utf-8")
    return dst


def promote_to_gt(
    images_dir: Path,
    stem: str,
    model_name: str,
    content: str | None = None,
) -> Path:
    dst = _ensure_under(images_dir, images_dir / f"{stem}_ground_truth.html")
    if content is not None:
        dst.write_text(strip_fences(content), encoding="utf-8")
        return dst
    src = _ensure_under(images_dir, result_path(images_dir, stem, safe_name(model_name)))
    if not src.exists():
        raise FileNotFoundError(f"Result not found: {model_name}")
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def image_summary(images_dir: Path) -> dict:
    images = list_images(images_dir)
    gt_count = sum(1 for stem, _ in images if find_gt(images_dir, stem))
    model_hits: dict[str, int] = {}

    rows = []
    for stem, img_path in images:
        gt = find_gt(images_dir, stem)
        models = list_results(images_dir, stem)
        for m in models:
            model_hits[m] = model_hits.get(m, 0) + 1
        rows.append(
            {
                "stem": stem,
                "has_gt": gt is not None,
                "gt_file": gt.name if gt else None,
                "models": models,
                "image_file": img_path.name,
            }
        )

    return {
        "total": len(images),
        "gt_count": gt_count,
        "remaining_gt": len(images) - gt_count,
        "model_coverage": model_hits,
        "images": rows,
    }

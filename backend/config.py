"""Persistent settings for the OCR benchmark app."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_IMAGES_DIR = Path(r"C:\Users\daniel\OneDrive\Pictures\benchmark\final")
DEFAULT_LM_URL = "http://localhost:1234/v1"
DEFAULT_LM_KEY = "lm-studio"

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"


def _defaults() -> dict[str, Any]:
    return {
        "images_dir": str(DEFAULT_IMAGES_DIR),
        "lm_studio_url": os.getenv("LM_STUDIO_URL", DEFAULT_LM_URL),
        "lm_studio_key": os.getenv("LM_STUDIO_KEY", DEFAULT_LM_KEY),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "default_openai_model": "gpt-4o",
        "default_anthropic_model": "claude-sonnet-4-20250514",
        "ocr_delay_seconds": 2.0,
    }


def load_settings() -> dict[str, Any]:
    data = _defaults()
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            data.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    # Env vars override empty stored keys
    if os.getenv("OPENAI_API_KEY"):
        data["openai_api_key"] = os.getenv("OPENAI_API_KEY", "")
    if os.getenv("ANTHROPIC_API_KEY"):
        data["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    return data


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    data = load_settings()
    data.update(updates)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def public_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Return settings safe to expose to the frontend (mask API keys)."""
    def mask(key: str) -> str:
        val = data.get(key, "")
        if not val:
            return ""
        if len(val) <= 8:
            return "••••••••"
        return val[:4] + "•" * (len(val) - 8) + val[-4:]

    return {
        "images_dir": data["images_dir"],
        "lm_studio_url": data["lm_studio_url"],
        "lm_studio_key_set": bool(data.get("lm_studio_key")),
        "openai_api_key_set": bool(data.get("openai_api_key")),
        "anthropic_api_key_set": bool(data.get("anthropic_api_key")),
        "openai_api_key_preview": mask("openai_api_key"),
        "anthropic_api_key_preview": mask("anthropic_api_key"),
        "default_openai_model": data["default_openai_model"],
        "default_anthropic_model": data["default_anthropic_model"],
        "ocr_delay_seconds": data["ocr_delay_seconds"],
    }

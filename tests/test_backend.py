from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import ocr_core
from backend import server


class BackendSafetyTests(unittest.TestCase):
    def test_save_ground_truth_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            with self.assertRaises(ValueError):
                ocr_core.save_ground_truth(images_dir, "..\\outside", "<html></html>")

    def test_promote_requires_existing_image_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            (images_dir / "doc.ocr_ready.jpg").write_bytes(b"fake")

            with patch("backend.server._images_dir", return_value=images_dir):
                with self.assertRaises(HTTPException) as ctx:
                    server.promote(
                        server.PromoteRequest(
                            stem="..\\outside",
                            model="manual",
                            html="<html></html>",
                        )
                    )

            self.assertEqual(ctx.exception.status_code, 404)
            self.assertFalse((images_dir.parent / "outside_ground_truth.html").exists())

    def test_process_one_skips_default_model_before_ocr_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            (images_dir / "doc.ocr_ready.jpg").write_bytes(b"fake")
            result = ocr_core.result_path(images_dir, "doc", "gpt-4o")
            result.parent.mkdir(parents=True)
            result.write_text("<html></html>", encoding="utf-8")

            req = server.OcrJobRequest(backend="openai", stems=["doc"], overwrite=False)
            settings = {
                "images_dir": str(images_dir),
                "default_openai_model": "gpt-4o",
                "default_anthropic_model": "claude-sonnet-4-20250514",
                "lm_studio_url": "http://localhost:1234/v1",
                "lm_studio_key": "lm-studio",
                "openai_api_key": "unused",
                "anthropic_api_key": "unused",
            }

            with patch("backend.server.run_ocr") as run_ocr:
                out = server._process_one("doc", req, settings)

            self.assertEqual(out["status"], "skipped")
            self.assertEqual(out["model"], "gpt-4o")
            run_ocr.assert_not_called()

    def test_update_result_creates_new_refined_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images_dir = Path(tmp)
            (images_dir / "doc.ocr_ready.jpg").write_bytes(b"fake")

            with patch("backend.server._images_dir", return_value=images_dir):
                out = server.update_result(
                    "doc",
                    "model-refined-test",
                    server.SaveHtmlRequest(html="<html><body>ok</body></html>"),
                )

            saved = ocr_core.result_path(images_dir, "doc", "model-refined-test")
            self.assertEqual(out["model"], "model-refined-test")
            self.assertTrue(saved.exists())
            self.assertIn("ok", saved.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import os
import tempfile
import time
import unittest

from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from webapp import app as module


def default_components_for_mode(mode):
    raw = {}

    for field in module.load_ui_fields(mode):
        options = field.get("options", [])
        selected = next(
            (
                option
                for option in options
                if option.get("default")
            ),
            options[0] if options else {"value": ""}
        )
        raw[field["id"]] = selected.get("value", "")

    raw["banner"] = {"enabled": False}
    raw["generation"] = {
        "fixed_seed": True,
        "seed": 20260822
    }

    return raw


class GenerationModeTests(unittest.TestCase):
    def test_text_to_image_is_default(self):
        self.assertEqual(module.DEFAULT_MODE, "text_to_image")
        self.assertFalse(
            module.get_mode_config("text_to_image")["requires_upload"]
        )

    def test_text_to_image_prompt_uses_fictional_people(self):
        raw = default_components_for_mode("text_to_image")

        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )
        prompt = module.build_prompt_from_components(
            components,
            "text_to_image"
        )

        self.assertIn("TASK AND CAMPAIGN GOAL", prompt)
        self.assertIn("completely fictional", prompt)
        self.assertIn("SUCCESS CRITERIA", prompt)
        self.assertNotIn("uploaded person", prompt.lower())

    def test_text_to_image_workflow_is_patched_from_config(self):
        raw = default_components_for_mode("text_to_image")

        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )
        prompt = module.build_prompt_from_components(
            components,
            "text_to_image"
        )
        prompt_package = module.build_prompt_package(
            components,
            "text_to_image"
        )
        workflow = module.patch_workflow(
            module.deep_copy_workflow("text_to_image"),
            "text_to_image",
            prompt,
            components,
            negative_prompt=prompt_package["negative_prompt"]
        )

        self.assertEqual(
            workflow["1"]["inputs"]["unet_name"],
            "qwen_image_2512_fp8_e4m3fn.safetensors"
        )
        self.assertEqual(workflow["4"]["inputs"]["text"], prompt)
        self.assertEqual(
            workflow["5"]["inputs"]["text"],
            prompt_package["negative_prompt"]
        )
        self.assertEqual(workflow["7"]["inputs"]["width"], 1664)
        self.assertEqual(workflow["7"]["inputs"]["height"], 928)
        self.assertEqual(workflow["8"]["inputs"]["seed"], 20260822)

    def test_prompt_package_exposes_framework_and_criteria(self):
        raw = default_components_for_mode("text_to_image")
        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )
        package = module.build_prompt_package(
            components,
            "text_to_image"
        )

        self.assertEqual(
            [section["title"] for section in package["sections"]],
            list(module.TEXT_TO_IMAGE_SECTION_TITLES.values())
        )
        self.assertGreaterEqual(len(package["success_criteria"]), 4)
        self.assertIn("company logo", package["negative_prompt"])
        self.assertIn(
            "North Rhine-Westphalia",
            package["positive_prompt"]
        )

    def test_image_to_image_workflow_remains_available(self):
        fields = module.load_ui_fields("image_to_image")
        raw = {
            field["id"]: field["options"][1]["value"]
            for field in fields
        }
        raw["banner"] = {"enabled": False}

        components = module.normalize_prompt_components(
            raw,
            "image_to_image"
        )
        prompt = module.build_prompt_from_components(
            components,
            "image_to_image"
        )
        workflow = module.patch_workflow(
            module.deep_copy_workflow("image_to_image"),
            "image_to_image",
            prompt,
            components,
            "test.png"
        )

        self.assertIn("Preserve the uploaded person's identity", prompt)
        self.assertEqual(workflow["78"]["inputs"]["image"], "test.png")
        self.assertEqual(workflow["435"]["inputs"]["value"], prompt)

    def test_prompt_chain_uses_a_saved_synthetic_source(self):
        self.assertTrue(
            module.get_mode_config("prompt_chain")["requires_library_source"]
        )

        fields = module.load_ui_fields("prompt_chain")
        raw = {
            field["id"]: field["options"][1]["value"]
            for field in fields
        }
        raw["banner"] = {"enabled": False}
        raw["generation"] = {
            "fixed_seed": True,
            "seed": 20260822
        }
        raw["sourceType"] = "generated_library"
        components = module.normalize_prompt_components(
            raw,
            "prompt_chain"
        )
        prompt = module.build_prompt_from_components(
            components,
            "prompt_chain"
        )
        workflow = module.patch_workflow(
            module.deep_copy_workflow("prompt_chain"),
            "prompt_chain",
            prompt,
            components,
            "synthetic-source.png"
        )

        self.assertIn("supplied synthetic campaign image", prompt)
        self.assertNotIn("uploaded person's identity", prompt)
        self.assertEqual(
            workflow["78"]["inputs"]["image"],
            "synthetic-source.png"
        )
        self.assertEqual(
            module.resolve_mode_model_name("prompt_chain"),
            "qwen_image_edit_2509_fp8_e4m3fn.safetensors"
        )

    def test_prompt_chain_requires_a_saved_library_source(self):
        fields = module.load_ui_fields("prompt_chain")
        raw = {
            field["id"]: field["options"][1]["value"]
            for field in fields
        }
        raw["banner"] = {"enabled": False}

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                module.run(
                    prompt_components=json.dumps(raw),
                    mode="prompt_chain",
                    consent_confirmed=False,
                    library_source_id="",
                    file=None
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("requires a saved library image", raised.exception.detail)

    def test_library_only_contains_explicitly_saved_results(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original_paths = (
                module.GENERATED_DIR,
                module.TEMP_RESULTS_DIR,
                module.LIBRARY_DIR,
                module.LIBRARY_IMAGES_DIR,
                module.LIBRARY_THUMBNAILS_DIR,
                module.LIBRARY_DB_PATH
            )

            try:
                module.GENERATED_DIR = root / "generated"
                module.TEMP_RESULTS_DIR = root / "temporary_results"
                module.LIBRARY_DIR = root / "library"
                module.LIBRARY_IMAGES_DIR = module.LIBRARY_DIR / "images"
                module.LIBRARY_THUMBNAILS_DIR = (
                    module.LIBRARY_DIR /
                    "thumbnails"
                )
                module.LIBRARY_DB_PATH = (
                    module.LIBRARY_DIR /
                    "library.sqlite3"
                )
                module.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                module.TEMP_RESULTS.clear()
                module.init_library_storage()

                image_output = BytesIO()
                Image.new("RGB", (640, 360), (32, 82, 150)).save(
                    image_output,
                    format="PNG"
                )
                image_bytes = image_output.getvalue()

                result = module.register_temporary_result(
                    display_bytes=image_bytes,
                    source_bytes=image_bytes,
                    metadata={
                        "mode": "text_to_image",
                        "parent_id": None,
                        "positive_prompt": "Test prompt",
                        "negative_prompt": "Test negative prompt",
                        "components": {
                            "generation": {
                                "actual_seed": 20260822
                            }
                        }
                    }
                )

                self.assertTrue(result["view_url"].startswith("/api/results/"))
                self.assertNotIn("/static/", result["view_url"])
                self.assertEqual(module.list_library_items(), [])

                item = module.persist_temporary_result(
                    result["result_id"],
                    "Testmotiv"
                )

                self.assertEqual(item["title"], "Testmotiv")
                self.assertEqual(item["seed"], 20260822)
                self.assertEqual(
                    item["model_name"],
                    "qwen_image_2512_fp8_e4m3fn.safetensors"
                )
                self.assertEqual(len(module.list_library_items()), 1)
                self.assertTrue(
                    (module.LIBRARY_IMAGES_DIR / f"{item['id']}_source.png").exists()
                )

                module.delete_library_item(item["id"])
                self.assertEqual(module.list_library_items(), [])

                replacement = module.persist_temporary_result(
                    result["result_id"],
                    "Erneut gespeichert"
                )
                self.assertNotEqual(replacement["id"], item["id"])
                self.assertEqual(len(module.list_library_items()), 1)
            finally:
                (
                    module.GENERATED_DIR,
                    module.TEMP_RESULTS_DIR,
                    module.LIBRARY_DIR,
                    module.LIBRARY_IMAGES_DIR,
                    module.LIBRARY_THUMBNAILS_DIR,
                    module.LIBRARY_DB_PATH
                ) = original_paths
                module.TEMP_RESULTS.clear()

    def test_image_to_image_requires_server_side_consent(self):
        fields = module.load_ui_fields("image_to_image")
        raw = {
            field["id"]: field["options"][1]["value"]
            for field in fields
        }
        raw["banner"] = {"enabled": False}

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                module.run(
                    prompt_components=json.dumps(raw),
                    mode="image_to_image",
                    consent_confirmed=False,
                    library_source_id="",
                    file=None
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("requires confirmation", raised.exception.detail)

    def test_real_upload_result_cannot_enter_synthetic_library(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            original_generated_dir = module.GENERATED_DIR
            original_temp_results_dir = module.TEMP_RESULTS_DIR

            try:
                module.GENERATED_DIR = Path(temporary_directory)
                module.TEMP_RESULTS_DIR = Path(temporary_directory)
                module.TEMP_RESULTS.clear()

                image_output = BytesIO()
                Image.new("RGB", (320, 180), (80, 80, 80)).save(
                    image_output,
                    format="PNG"
                )
                image_bytes = image_output.getvalue()

                result = module.register_temporary_result(
                    display_bytes=image_bytes,
                    source_bytes=image_bytes,
                    metadata={
                        "mode": "image_to_image",
                        "parent_id": None,
                        "positive_prompt": "Test prompt",
                        "negative_prompt": "",
                        "components": {}
                    }
                )

                with self.assertRaises(HTTPException) as raised:
                    module.persist_temporary_result(result["result_id"])

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("fully synthetic", raised.exception.detail)
            finally:
                module.GENERATED_DIR = original_generated_dir
                module.TEMP_RESULTS_DIR = original_temp_results_dir
                module.TEMP_RESULTS.clear()

    def test_expired_temporary_results_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            original_generated_dir = module.GENERATED_DIR
            original_temp_results_dir = module.TEMP_RESULTS_DIR
            original_retention = module.TEMP_RESULT_RETENTION_HOURS

            try:
                module.GENERATED_DIR = Path(temporary_directory)
                module.TEMP_RESULTS_DIR = Path(temporary_directory)
                module.TEMP_RESULT_RETENTION_HOURS = 1
                module.TEMP_RESULTS.clear()

                image_output = BytesIO()
                Image.new("RGB", (160, 90), (25, 70, 130)).save(
                    image_output,
                    format="PNG"
                )
                image_bytes = image_output.getvalue()

                result = module.register_temporary_result(
                    display_bytes=image_bytes,
                    source_bytes=image_bytes,
                    metadata={
                        "mode": "text_to_image",
                        "parent_id": None,
                        "positive_prompt": "Test prompt",
                        "negative_prompt": "",
                        "components": {}
                    }
                )
                entry = module.TEMP_RESULTS[result["result_id"]]
                old_time = time.time() - 2 * 60 * 60

                for key in ("display_path", "source_path"):
                    os.utime(entry[key], (old_time, old_time))

                module.cleanup_temporary_result_files()

                self.assertNotIn(result["result_id"], module.TEMP_RESULTS)
                self.assertFalse(entry["display_path"].exists())
                self.assertFalse(entry["source_path"].exists())
            finally:
                module.GENERATED_DIR = original_generated_dir
                module.TEMP_RESULTS_DIR = original_temp_results_dir
                module.TEMP_RESULT_RETENTION_HOURS = original_retention
                module.TEMP_RESULTS.clear()


if __name__ == "__main__":
    unittest.main()

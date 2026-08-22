import asyncio
import json
import unittest

from fastapi import HTTPException

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
                    file=None
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("requires confirmation", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()

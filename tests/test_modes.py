import asyncio
import json
import os
import tempfile
import time
import unittest

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException, UploadFile
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
    @staticmethod
    def make_test_image(width=640, height=640):
        output = BytesIO()
        Image.new("RGB", (width, height), (32, 82, 150)).save(
            output,
            format="PNG"
        )
        return output.getvalue()

    def test_text_to_image_is_default(self):
        self.assertEqual(module.DEFAULT_MODE, "text_to_image")
        self.assertEqual(
            set(module.MODE_CONFIGS),
            {"text_to_image", "prompt_chain"}
        )

    def test_cloud_provider_disables_seed_and_separate_negative_prompt(self):
        raw = default_components_for_mode("text_to_image")
        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )

        module.apply_provider_generation_settings(
            components,
            "openai",
            "text_to_image"
        )

        generation = components["generation"]
        self.assertEqual(generation["provider"], "openai")
        self.assertEqual(
            generation["model_name"],
            module.OPENAI_IMAGE_MODEL
        )
        self.assertFalse(generation["fixed_seed"])
        self.assertFalse(generation["seed_supported"])
        self.assertFalse(generation["negative_prompt_supported"])
        self.assertIsNone(generation["actual_seed"])

    def test_public_config_never_exposes_cloud_api_key(self):
        fake_key = "unit-test-key-value"

        with patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}):
            with patch.object(
                module,
                "local_provider_available",
                return_value=True
            ):
                response = module.public_config()

        payload_text = response.body.decode("utf-8")
        payload = json.loads(payload_text)
        cloud = next(
            provider
            for provider in payload["providers"]
            if provider["id"] == "openai"
        )

        self.assertTrue(cloud["available"])
        self.assertNotIn(fake_key, payload_text)
        self.assertNotIn("api_key", payload_text.lower())

    def test_cloud_text_to_image_uses_configured_model_size_and_quality(self):
        image_bytes = self.make_test_image()
        encoded = module.base64.b64encode(image_bytes).decode("ascii")
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.headers = {"x-request-id": "request-test"}
        response.json.return_value = {
            "data": [{"b64_json": encoded}]
        }
        components = default_components_for_mode("text_to_image")
        components["aspectRatio"] = "16:9"

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "unit-test-key-value"}
        ):
            with patch.object(
                module.requests,
                "post",
                return_value=response
            ) as post:
                result = module.request_openai_image(
                    prompt="A fictional adult workplace scene.",
                    mode="text_to_image",
                    components=components
                )

        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://api.openai.com/v1/images/generations"
        )
        self.assertEqual(
            request.kwargs["json"]["model"],
            module.OPENAI_IMAGE_MODEL
        )
        self.assertEqual(request.kwargs["json"]["size"], "1664x928")
        self.assertEqual(
            request.kwargs["json"]["quality"],
            module.OPENAI_IMAGE_QUALITY
        )
        self.assertEqual(request.kwargs["json"]["n"], 1)
        self.assertEqual(
            request.kwargs["headers"]["Authorization"],
            "Bearer unit-test-key-value"
        )

        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.format, "PNG")

    def test_cloud_prompt_chain_uses_edit_endpoint_and_source_image(self):
        source_bytes = self.make_test_image(1024, 1024)
        encoded = module.base64.b64encode(source_bytes).decode("ascii")
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {
            "data": [{"b64_json": encoded}]
        }

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "unit-test-key-value"}
        ):
            with patch.object(
                module.requests,
                "post",
                return_value=response
            ) as post:
                module.request_openai_image(
                    prompt="Preserve the subject and refine the composition.",
                    mode="prompt_chain",
                    components={},
                    source_bytes=source_bytes
                )

        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://api.openai.com/v1/images/edits"
        )
        self.assertEqual(request.kwargs["data"]["size"], "1024x1024")
        self.assertEqual(
            request.kwargs["data"]["model"],
            module.OPENAI_IMAGE_MODEL
        )
        self.assertIn("image[]", request.kwargs["files"])
        self.assertEqual(
            request.kwargs["files"]["image[]"][1],
            source_bytes
        )

    def test_cloud_request_requires_server_side_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as raised:
                module.request_openai_image(
                    prompt="A fictional adult workplace scene.",
                    mode="text_to_image",
                    components={"aspectRatio": "1:1"}
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("serverseitig", raised.exception.detail)

    def test_cloud_run_does_not_queue_a_local_workflow(self):
        raw = default_components_for_mode("text_to_image")
        image_bytes = self.make_test_image(1664, 928)

        with tempfile.TemporaryDirectory() as temporary_directory:
            original_generated_dir = module.GENERATED_DIR
            original_temp_results_dir = module.TEMP_RESULTS_DIR

            try:
                module.GENERATED_DIR = Path(temporary_directory) / "generated"
                module.TEMP_RESULTS_DIR = (
                    Path(temporary_directory) /
                    "temporary_results"
                )
                module.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                module.TEMP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                module.TEMP_RESULTS.clear()

                with patch.object(
                    module,
                    "provider_is_configured",
                    return_value=True
                ):
                    with patch.object(
                        module,
                        "request_openai_image",
                        return_value=image_bytes
                    ) as cloud_request:
                        with patch.object(
                            module,
                            "queue_prompt",
                            side_effect=AssertionError(
                                "local workflow must not be queued"
                            )
                        ):
                            response = asyncio.run(
                                module.run(
                                    prompt_components=json.dumps(raw),
                                    mode="text_to_image",
                                    library_source_id="",
                                    image_provider="openai"
                                )
                            )

                payload = json.loads(response.body.decode("utf-8"))
                self.assertEqual(payload["provider"], "openai")
                self.assertIsNone(payload["prompt_id"])
                self.assertIsNone(
                    payload["prompt_components"]["generation"]["actual_seed"]
                )
                self.assertEqual(payload["submitted_negative_prompt"], "")
                self.assertEqual(payload["results"][0]["provider"], "openai")
                cloud_request.assert_called_once()
            finally:
                module.GENERATED_DIR = original_generated_dir
                module.TEMP_RESULTS_DIR = original_temp_results_dir
                module.TEMP_RESULTS.clear()

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

        self.assertIn("VISIBLE CAMPAIGN INTENT", prompt)
        self.assertIn("clearly adult", prompt)
        self.assertIn("adult aged 18 or older", prompt)
        self.assertIn("Do not depict children, minors", prompt)
        self.assertNotIn("SUCCESS CRITERIA", prompt)
        self.assertNotIn("North Rhine-Westphalia", prompt)
        self.assertNotIn("school graduates", prompt)
        self.assertNotIn("uploaded person", prompt.lower())

    def test_text_to_image_excludes_minors_and_ambiguous_background_people(self):
        raw = default_components_for_mode("text_to_image")
        components = module.normalize_prompt_components(raw, "text_to_image")
        package = module.build_prompt_package(components, "text_to_image")

        self.assertIn("age appears ambiguous", package["positive_prompt"])
        self.assertIn("minor", package["negative_prompt"])
        self.assertIn("school pupil", package["negative_prompt"])
        self.assertTrue(
            any(
                "volljährige" in criterion["label"]
                for criterion in package["success_criteria"]
            )
        )

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
            list(module.TEXT_TO_IMAGE_RENDER_SECTION_TITLES.values())
        )
        self.assertEqual(package["render_sections"], package["sections"])
        self.assertEqual(
            package["render_prompt"],
            package["positive_prompt"]
        )
        self.assertGreaterEqual(len(package["success_criteria"]), 4)
        self.assertGreaterEqual(len(package["briefing_sections"]), 4)
        self.assertEqual(len(package["translation_steps"]), 3)
        self.assertIn("company logo", package["negative_prompt"])
        self.assertIn(
            "Schulabgänger:innen in NRW",
            " ".join(
                section["text"]
                for section in package["briefing_sections"]
            )
        )
        self.assertNotIn(
            "North Rhine-Westphalia",
            package["positive_prompt"]
        )

    def test_campaign_strategy_is_translated_into_visible_evidence(self):
        raw = default_components_for_mode("text_to_image")
        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )
        package = module.build_prompt_package(
            components,
            "text_to_image"
        )

        render_prompt = package["render_prompt"]
        self.assertIn("performed under adult guidance", render_prompt)
        self.assertIn("shared attention to the same task", render_prompt)
        self.assertIn("avoid a posed recruiting portrait", render_prompt)
        self.assertNotIn("Human review is required", render_prompt)
        self.assertNotIn("Kampagnenziel", render_prompt)

    def test_subject_controls_have_separate_non_overlapping_responsibilities(self):
        fields = module.load_ui_fields("text_to_image")
        field_ids = {field["id"] for field in fields}

        self.assertTrue({
            "peopleConfiguration",
            "mainSubjectAge",
            "backgroundPolicy",
            "mainOutfit",
            "supportingOutfit"
        }.issubset(field_ids))
        self.assertNotIn("personConcept", field_ids)
        self.assertNotIn("outfit", field_ids)

    def test_default_subject_prompt_distinguishes_age_roles_and_clothing(self):
        raw = default_components_for_mode("text_to_image")
        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )
        package = module.build_prompt_package(
            components,
            "text_to_image"
        )

        prompt = package["render_prompt"]
        negative_prompt = package["negative_prompt"]

        self.assertIn("exactly two", prompt)
        self.assertIn("one mentor aged approximately 35 to 50", prompt)
        self.assertIn("aged approximately 20 to 24", prompt)
        self.assertIn("light-blue overshirt", prompt)
        self.assertIn("No additional people are visible", prompt)
        self.assertIn("clothing colors and garment types", prompt)
        self.assertIn("middle-aged main subject", negative_prompt)
        self.assertIn("matching green shirts", negative_prompt)

    def test_incompatible_people_and_action_combination_is_rejected(self):
        fields = module.load_ui_fields("text_to_image")
        raw = default_components_for_mode("text_to_image")
        people_field = next(
            field
            for field in fields
            if field["id"] == "peopleConfiguration"
        )
        solo_option = next(
            option
            for option in people_field["options"]
            if option.get("configuration_id") == "solo"
        )
        raw["peopleConfiguration"] = solo_option["value"]

        with self.assertRaises(HTTPException) as raised:
            module.normalize_prompt_components(raw, "text_to_image")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn(
            "passt nicht zur Personenkonstellation",
            raised.exception.detail
        )

    def test_every_people_configuration_has_a_compatible_recommendation(self):
        fields = module.load_ui_fields("text_to_image")
        people_field = next(
            field
            for field in fields
            if field["id"] == "peopleConfiguration"
        )
        action_field = next(
            field
            for field in fields
            if field["id"] == "action"
        )

        for configuration in people_field["options"]:
            configuration_id = configuration.get("configuration_id")

            if not configuration_id:
                continue

            recommended = next(
                (
                    option
                    for option in action_field["options"]
                    if configuration_id in option.get("recommended_for", [])
                ),
                None
            )
            self.assertIsNotNone(recommended, configuration_id)
            self.assertIn(
                configuration_id,
                recommended["allowed_configurations"]
            )

            raw = default_components_for_mode("text_to_image")
            raw["peopleConfiguration"] = configuration["value"]
            raw["action"] = recommended["value"]
            components = module.normalize_prompt_components(
                raw,
                "text_to_image"
            )
            package = module.build_prompt_package(
                components,
                "text_to_image"
            )
            self.assertIn(configuration["value"], package["render_prompt"])

    def test_solo_configuration_omits_supporting_outfit(self):
        fields = module.load_ui_fields("text_to_image")
        raw = default_components_for_mode("text_to_image")
        people_field = next(
            field
            for field in fields
            if field["id"] == "peopleConfiguration"
        )
        action_field = next(
            field
            for field in fields
            if field["id"] == "action"
        )
        raw["peopleConfiguration"] = next(
            option["value"]
            for option in people_field["options"]
            if option.get("configuration_id") == "solo"
        )
        raw["action"] = next(
            option["value"]
            for option in action_field["options"]
            if "solo" in option.get("recommended_for", [])
        )

        components = module.normalize_prompt_components(
            raw,
            "text_to_image"
        )
        package = module.build_prompt_package(
            components,
            "text_to_image"
        )

        self.assertEqual(components["supportingOutfit"], "")
        self.assertNotIn(
            "Every supporting person wears",
            package["render_prompt"]
        )

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

        self.assertIn("PRIMARY OPTIMIZATION TASK", prompt)
        self.assertIn("SOURCE AND PRESERVATION", prompt)
        self.assertIn("REQUESTED VISUAL CHANGES", prompt)
        self.assertIn("\nOUTPUT\n", prompt)
        self.assertIn("fully synthetic", prompt)
        self.assertNotIn("uploaded person's identity", prompt.lower())
        self.assertEqual(
            workflow["78"]["inputs"]["image"],
            "synthetic-source.png"
        )
        self.assertEqual(workflow["433:3"]["inputs"]["seed"], 20260822)
        self.assertEqual(workflow["435"]["inputs"]["value"], prompt)
        self.assertEqual(
            module.resolve_mode_model_name("prompt_chain"),
            "qwen_image_edit_2509_fp8_e4m3fn.safetensors"
        )

    def test_prompt_chain_separates_render_prompt_and_evaluation_criteria(self):
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
        components = module.normalize_prompt_components(raw, "prompt_chain")
        package = module.build_prompt_package(components, "prompt_chain")

        self.assertEqual(
            [section["title"] for section in package["sections"]],
            list(module.PROMPT_CHAIN_SECTION_TITLES.values())
        )
        self.assertGreaterEqual(len(package["success_criteria"]), 6)
        self.assertIn("one focused refinement", package["positive_prompt"])
        self.assertIn(
            "Change only explicitly requested elements",
            package["positive_prompt"]
        )
        self.assertIn("adult aged 18 or older", package["positive_prompt"])
        self.assertIn("Do not add children, minors", package["positive_prompt"])
        self.assertNotIn("Definition of done", package["positive_prompt"])
        self.assertEqual(package["briefing_sections"], [])
        self.assertEqual(package["translation_steps"], [])

    def test_prompt_chain_fields_cover_campaign_optimization(self):
        field_ids = {
            field["id"]
            for field in module.load_ui_fields("prompt_chain")
        }

        self.assertEqual(
            field_ids,
            {
                "optimizationGoal",
                "changeStrength",
                "preservationFocus",
                "workActivity",
                "interaction",
                "pose",
                "gaze",
                "expression",
                "roleStyling",
                "framing",
                "campaignSpace",
                "spaceTreatment",
                "visualEffect",
                "correctionFocus"
            }
        )

    def test_prompt_chain_separates_framing_and_campaign_space(self):
        fields = module.load_ui_fields("prompt_chain")
        by_id = {field["id"]: field for field in fields}
        raw = default_components_for_mode("prompt_chain")
        raw["optimizationGoal"] = by_id["optimizationGoal"]["options"][6][
            "value"
        ]
        raw["framing"] = by_id["framing"]["options"][1]["value"]
        raw["campaignSpace"] = by_id["campaignSpace"]["options"][1][
            "value"
        ]
        raw["spaceTreatment"] = by_id["spaceTreatment"]["options"][0][
            "value"
        ]
        raw["banner"] = {
            "enabled": True,
            "text": "Deine Zukunft beginnt hier",
            "subtext": "Duales Studium",
            "position": "right",
            "style": "dark_glass",
            "font": "modern",
            "align": "left",
            "color": "#1457ff"
        }
        raw["generation"] = {
            "fixed_seed": True,
            "seed": 20260822
        }
        components = module.normalize_prompt_components(raw, "prompt_chain")
        prompt = module.build_prompt_from_components(components, "prompt_chain")

        self.assertIn("medium environmental shot", prompt)
        self.assertIn("35 to 40 percent", prompt)
        self.assertIn("right as calm, usable negative space", prompt)
        self.assertIn("existing workplace environment naturally", prompt)
        self.assertIn("selected reserved campaign area", prompt)
        self.assertEqual(components["banner"]["position"], "right")

    def test_prompt_chain_campaign_space_overrides_conflicting_banner_side(self):
        fields = {
            field["id"]: field
            for field in module.load_ui_fields("prompt_chain")
        }
        raw = default_components_for_mode("prompt_chain")
        raw["campaignSpace"] = fields["campaignSpace"]["options"][2][
            "value"
        ]
        raw["banner"] = {
            "enabled": True,
            "text": "Duales Studium",
            "position": "right"
        }

        components = module.normalize_prompt_components(raw, "prompt_chain")

        self.assertEqual(components["banner"]["position"], "left")

    def test_prompt_chain_ignores_area_treatment_without_reserved_space(self):
        fields = {
            field["id"]: field
            for field in module.load_ui_fields("prompt_chain")
        }
        raw = default_components_for_mode("prompt_chain")
        raw["campaignSpace"] = ""
        raw["spaceTreatment"] = fields["spaceTreatment"]["options"][1][
            "value"
        ]

        components = module.normalize_prompt_components(raw, "prompt_chain")

        self.assertEqual(components["spaceTreatment"], "")

    def test_prompt_chain_derives_task_from_change_without_primary_goal(self):
        fields = {
            field["id"]: field
            for field in module.load_ui_fields("prompt_chain")
        }
        raw = default_components_for_mode("prompt_chain")
        raw["framing"] = fields["framing"]["options"][1]["value"]
        components = module.normalize_prompt_components(raw, "prompt_chain")
        package = module.build_prompt_package(components, "prompt_chain")

        self.assertFalse(fields["optimizationGoal"].get("required", False))
        self.assertTrue(package["positive_prompt"])
        self.assertIn(
            "explicitly selected visual changes below as the complete",
            package["positive_prompt"]
        )
        self.assertIn("medium environmental shot", package["positive_prompt"])
        self.assertIn(
            "Die ausdrücklich ausgewählte Bildänderung",
            package["success_criteria"][0]["label"]
        )

    def test_prompt_chain_rejects_a_no_change_request(self):
        raw = default_components_for_mode("prompt_chain")
        components = module.normalize_prompt_components(raw, "prompt_chain")
        package = module.build_prompt_package(components, "prompt_chain")

        self.assertEqual(package["positive_prompt"], "")
        self.assertEqual(package["sections"], [])
        self.assertEqual(package["success_criteria"], [])

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
                    library_source_id=""
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("requires a saved library image", raised.exception.detail)

    def test_logo_settings_are_bounded_and_never_enter_the_model_prompt(self):
        raw = default_components_for_mode("text_to_image")
        raw["logo"] = {
            "enabled": True,
            "x_percent": 170,
            "y_percent": -25,
            "size_percent": 80
        }

        components = module.normalize_prompt_components(raw, "text_to_image")
        prompt = module.build_prompt_from_components(
            components,
            "text_to_image"
        )

        self.assertEqual(components["logo"]["x_percent"], 100)
        self.assertEqual(components["logo"]["y_percent"], 0)
        self.assertEqual(components["logo"]["size_percent"], 30)
        self.assertNotIn("logo_file", prompt)
        self.assertIn("No company logos", prompt)
        self.assertTrue(
            any(
                "PNG-Overlay" in criterion["label"]
                for criterion in module.build_success_criteria(components)
            )
        )

    def test_logo_upload_requires_a_png_when_enabled(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                module.read_logo_upload(
                    None,
                    {
                        "enabled": True
                    }
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("keine PNG", raised.exception.detail)

    def test_logo_upload_accepts_only_sanitized_png(self):
        png_bytes = self.make_test_image(240, 120)
        sanitized = module.sanitize_logo_png_bytes(png_bytes)

        with Image.open(BytesIO(sanitized)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGBA")

        jpeg = BytesIO()
        Image.new("RGB", (120, 60), (220, 40, 40)).save(
            jpeg,
            format="JPEG"
        )

        with self.assertRaises(HTTPException) as raised:
            module.sanitize_logo_png_bytes(jpeg.getvalue())

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("PNG", raised.exception.detail)

    def test_logo_layout_avoids_the_mandatory_ai_label(self):
        new_size, position = module.calculate_logo_layout(
            base_size=(1000, 500),
            logo_size=(200, 100),
            settings={
                "x_percent": 0,
                "y_percent": 0,
                "size_percent": 20
            },
            reserved_box=(35, 18, 300, 90)
        )
        logo_box = (
            position[0],
            position[1],
            position[0] + new_size[0],
            position[1] + new_size[1]
        )

        self.assertFalse(
            module.rectangles_overlap(
                logo_box,
                (35, 18, 300, 90),
                padding=15
            )
        )

    def test_ai_label_and_placement_preview_use_upper_right(self):
        new_size, position = module.calculate_ai_overlay_layout(
            base_size=(1000, 500),
            overlay_size=(300, 80)
        )
        html = module.INDEX_PATH.read_text(encoding="utf-8")

        self.assertEqual(module.AI_OVERLAY_POSITION, "top_right")
        self.assertGreater(position[0], 500)
        self.assertLess(position[1], 100)
        self.assertGreater(new_size[0], 0)
        self.assertIn(
            "Oben rechts bleibt für die verpflichtende KI-Kennzeichnung",
            html
        )

    def test_user_positioned_logo_is_only_added_to_display_image(self):
        raw = default_components_for_mode("text_to_image")
        raw["logo"] = {
            "enabled": True,
            "x_percent": 100,
            "y_percent": 100,
            "size_percent": 20
        }
        source_bytes = self.make_test_image(800, 450)
        logo_output = BytesIO()
        Image.new("RGBA", (200, 100), (230, 35, 45, 255)).save(
            logo_output,
            format="PNG"
        )
        upload = UploadFile(
            file=BytesIO(logo_output.getvalue()),
            filename="test-logo.png"
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            original_generated_dir = module.GENERATED_DIR
            original_temp_results_dir = module.TEMP_RESULTS_DIR

            try:
                module.GENERATED_DIR = Path(temporary_directory) / "generated"
                module.TEMP_RESULTS_DIR = (
                    Path(temporary_directory) /
                    "temporary_results"
                )
                module.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                module.TEMP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                module.TEMP_RESULTS.clear()

                with patch.object(
                    module,
                    "provider_is_configured",
                    return_value=True
                ):
                    with patch.object(
                        module,
                        "request_openai_image",
                        return_value=source_bytes
                    ):
                        with patch.object(
                            module,
                            "render_ai_overlay_on_image",
                            side_effect=lambda image_bytes: image_bytes
                        ):
                            response = asyncio.run(
                                module.run(
                                    prompt_components=json.dumps(raw),
                                    mode="text_to_image",
                                    library_source_id="",
                                    image_provider="openai",
                                    logo_file=upload
                                )
                            )

                payload = json.loads(response.body.decode("utf-8"))
                result_id = payload["results"][0]["result_id"]
                entry = module.TEMP_RESULTS[result_id]
                saved_source = entry["source_path"].read_bytes()
                saved_display = entry["display_path"].read_bytes()

                self.assertEqual(saved_source, source_bytes)
                self.assertNotEqual(saved_display, saved_source)
                self.assertTrue(payload["prompt_components"]["logo"]["applied"])

                with Image.open(BytesIO(saved_display)) as image:
                    self.assertEqual(image.getpixel((700, 390))[:3], (230, 35, 45))
            finally:
                module.GENERATED_DIR = original_generated_dir
                module.TEMP_RESULTS_DIR = original_temp_results_dir
                module.TEMP_RESULTS.clear()

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

    def test_removed_image_to_image_mode_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            module.normalize_mode("image_to_image")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unknown generation mode", raised.exception.detail)

    def test_real_image_upload_ui_is_removed(self):
        html = module.INDEX_PATH.read_text(encoding="utf-8")

        self.assertEqual(html.count('type="file"'), 1)
        self.assertIn('id="logoFile"', html)
        self.assertNotIn('id="sourceImage"', html)
        self.assertNotIn('id="personImage"', html)
        self.assertNotIn("consentAccepted", html)
        self.assertNotIn("Eigenes Bild bearbeiten", html)

    def test_cloud_key_is_never_requested_in_the_browser(self):
        html = module.INDEX_PATH.read_text(encoding="utf-8")

        self.assertIn('id="providerPicker"', html)
        self.assertIn('formData.append("image_provider"', html)
        self.assertNotIn('type="password"', html)
        self.assertNotIn('id="openaiApiKey"', html)
        self.assertNotIn('name="OPENAI_API_KEY"', html)

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

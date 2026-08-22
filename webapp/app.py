from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import json
import os
import uuid
import requests
import time
import threading
import websocket

from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from PIL import Image, ImageDraw, ImageFont


COMFY = "http://127.0.0.1:8188"

BASE_DIR = Path(__file__).resolve().parent
MODEL_CONFIG_PATH = BASE_DIR / "model_config.json"
INDEX_PATH = BASE_DIR / "static" / "index.html"

GENERATED_DIR = BASE_DIR / "static" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

ASSETS_DIR = BASE_DIR / "static" / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

AI_OVERLAY_PATH = ASSETS_DIR / "ai_generated_overlay.png"

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

MODEL_CONFIG = json.loads(
    MODEL_CONFIG_PATH.read_text(encoding="utf-8")
)

DEFAULT_MODE = MODEL_CONFIG.get("default_mode", "text_to_image")
MODE_CONFIGS = MODEL_CONFIG.get("modes", {})

if DEFAULT_MODE not in MODE_CONFIGS:
    raise RuntimeError(
        f"Unknown default mode in {MODEL_CONFIG_PATH.name}: {DEFAULT_MODE}"
    )

WORKFLOW_TEMPLATES = {}

for mode_name, mode_config in MODE_CONFIGS.items():
    workflow_path = BASE_DIR / mode_config["workflow"]

    WORKFLOW_TEMPLATES[mode_name] = json.loads(
        workflow_path.read_text(encoding="utf-8")
    )

DEBUG_PROMPTS = os.getenv(
    "EMPLOYER_BRANDING_DEBUG_PROMPTS",
    "0"
).strip().lower() in {"1", "true", "yes", "on"}

progress_state = {
    "value": 0
}

TEXT_TO_IMAGE_SECTION_TITLES = {
    "creative_direction": "CREATIVE ROLE / DIRECTION",
    "task_goal": "TASK AND CAMPAIGN GOAL",
    "context": "CONTEXT",
    "visual_specification": "VISUAL SPECIFICATION",
    "constraints": "CONSTRAINTS",
    "output_format": "OUTPUT FORMAT",
    "success_criteria": "SUCCESS CRITERIA"
}

OUTPUT_FORMATS = {
    "1:1": "Photorealistic square 1:1 image for a social-media feed.",
    "16:9": "Photorealistic 16:9 image for a careers website.",
    "9:16": "Photorealistic vertical 9:16 image for a social-media story.",
    "4:3": "Photorealistic horizontal 4:3 campaign image.",
    "3:4": "Photorealistic vertical 3:4 campaign image."
}

TEXT_TO_IMAGE_NEGATIVE_PROMPT = (
    "low resolution, low quality, distorted anatomy, malformed hands, "
    "extra fingers, duplicated people, oversaturated colors, waxy skin, "
    "artificial face, blurred details, chaotic composition, identifiable "
    "real person, real employee likeness, generated text, watermark, company "
    "logo, readable brand name, employee identification card, staged stock "
    "photo pose, exaggerated enthusiasm, tokenism, stereotypical depiction, "
    "discriminatory depiction, nudity, sexual content, pornography, violence, "
    "blood, weapons, drugs, hate symbols, extremist symbols"
)


# ---------------------------------------------------------------------------
# Grundfunktionen
# ---------------------------------------------------------------------------

def normalize_mode(mode):
    normalized = str(mode or DEFAULT_MODE).strip()

    if normalized not in MODE_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown generation mode: {normalized}"
        )

    return normalized


def get_mode_config(mode):
    return MODE_CONFIGS[normalize_mode(mode)]


def deep_copy_workflow(mode):
    normalized_mode = normalize_mode(mode)
    return json.loads(json.dumps(WORKFLOW_TEMPLATES[normalized_mode]))


def load_ui_fields(mode):
    mode_config = get_mode_config(mode)
    fields_path = BASE_DIR / mode_config["ui_fields"]

    return json.loads(fields_path.read_text(encoding="utf-8"))


def no_cache_headers():
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    }


# ---------------------------------------------------------------------------
# Prompt-Komponenten prüfen
# ---------------------------------------------------------------------------

def get_allowed_values_by_field(mode):
    fields = load_ui_fields(mode)
    allowed = {}

    for field in fields:
        field_id = field.get("id")
        options = field.get("options", [])

        if not field_id:
            continue

        allowed[field_id] = {
            str(option.get("value", ""))
            for option in options
        }

    return allowed


def normalize_banner_settings(raw_components):
    raw_banner = raw_components.get("banner", {})

    if not isinstance(raw_banner, dict):
        raw_banner = {}

    enabled = bool(raw_banner.get("enabled", False))

    text = str(raw_banner.get("text", "") or "").strip()
    subtext = str(raw_banner.get("subtext", "") or "").strip()

    position = str(raw_banner.get("position", "auto") or "auto").strip()
    style = str(raw_banner.get("style", "dark_glass") or "dark_glass").strip()
    font = str(raw_banner.get("font", "modern") or "modern").strip()
    align = str(raw_banner.get("align", "left") or "left").strip()
    color = str(raw_banner.get("color", "#1457ff") or "#1457ff").strip()

    allowed_positions = {
        "auto",
        "bottom",
        "top",
        "right"
    }

    allowed_styles = {
        "dark_glass",
        "light_panel",
        "brand_bar",
        "gradient_bottom",
        "minimal_shadow"
    }

    allowed_fonts = {
        "modern",
        "bold",
        "editorial",
        "condensed"
    }

    allowed_aligns = {
        "left",
        "center",
        "right"
    }

    if position not in allowed_positions:
        position = "auto"

    if style not in allowed_styles:
        style = "dark_glass"

    if font not in allowed_fonts:
        font = "modern"

    if align not in allowed_aligns:
        align = "left"

    if not color.startswith("#") or len(color) not in (4, 7):
        color = "#1457ff"

    if len(text) > 90:
        text = text[:90].strip()

    if len(subtext) > 140:
        subtext = subtext[:140].strip()

    if enabled and not text:
        raise HTTPException(
            status_code=400,
            detail="Banner ist aktiviert, aber der Bannertext ist leer."
        )

    return {
        "enabled": enabled,
        "text": text,
        "subtext": subtext,
        "position": position,
        "style": style,
        "font": font,
        "align": align,
        "color": color
    }


def normalize_generation_settings(raw_components):
    raw_generation = raw_components.get("generation", {})

    if not isinstance(raw_generation, dict):
        raw_generation = {}

    fixed_seed_value = raw_generation.get("fixed_seed", False)
    fixed_seed = fixed_seed_value is True or str(
        fixed_seed_value
    ).strip().lower() in {"1", "true", "yes", "on"}

    raw_seed = raw_generation.get("seed", 20260822)

    try:
        seed = int(raw_seed)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Seed must be an integer."
        ) from exc

    if seed < 0 or seed > 999_999_999_999_999:
        raise HTTPException(
            status_code=400,
            detail="Seed must be between 0 and 999999999999999."
        )

    return {
        "fixed_seed": fixed_seed,
        "seed": seed
    }


def normalize_prompt_components(raw_components, mode=DEFAULT_MODE):
    """
    Nimmt die vom Browser gesendeten Werte entgegen,
    prüft sie gegen die Felddefinition des gewählten Modus und gibt
    saubere Komponenten zurück.
    """
    if not isinstance(raw_components, dict):
        raise HTTPException(
            status_code=400,
            detail="prompt_components must be a JSON object"
        )

    normalized_mode = normalize_mode(mode)
    allowed_values_by_field = get_allowed_values_by_field(normalized_mode)

    normalized = {}
    invalid_fields = {}

    for field_id, allowed_values in allowed_values_by_field.items():
        value = str(raw_components.get(field_id, "") or "").strip()

        if value and value not in allowed_values:
            invalid_fields[field_id] = value
            continue

        normalized[field_id] = value

    extra_prompt = str(raw_components.get("extraPrompt", "") or "").strip()

    if len(extra_prompt) > 2000:
        raise HTTPException(
            status_code=400,
            detail="extraPrompt is too long"
        )

    normalized["extraPrompt"] = extra_prompt
    normalized["banner"] = normalize_banner_settings(raw_components)
    normalized["generation"] = normalize_generation_settings(raw_components)

    if invalid_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid prompt component values",
                "invalid_fields": invalid_fields
            }
        )

    return normalized


# ---------------------------------------------------------------------------
# Prompt bauen
# ---------------------------------------------------------------------------

def ensure_sentence(value):
    text = str(value or "").strip()

    if text and text[-1] not in ".!?":
        text += "."

    return text


def build_success_criteria(components):
    criteria = [
        {
            "prompt": (
                "The selected employer benefit and campaign goal must be "
                "recognizable without relying on generated text."
            ),
            "label": (
                "Kampagnenziel und Arbeitgebernutzen sind ohne generierten "
                "Text erkennbar."
            )
        },
        {
            "prompt": (
                "The workplace activity, collaboration, and responsibility "
                "must be understandable at first glance."
            ),
            "label": (
                "Tätigkeit, Zusammenarbeit und Verantwortung sind auf den "
                "ersten Blick verständlich."
            )
        },
        {
            "prompt": (
                "The scene must appear credible, respectful, and appropriate "
                "for the defined target group."
            ),
            "label": (
                "Die Szene wirkt glaubwürdig, respektvoll und passend für die "
                "definierte Zielgruppe."
            )
        },
        {
            "prompt": (
                "No identifiable real person, logo, readable brand name, "
                "tokenism, or stereotypical depiction may appear."
            ),
            "label": (
                "Keine identifizierbare reale Person, kein Logo, kein lesbarer "
                "Markenname, kein Tokenismus und keine stereotype Darstellung."
            )
        }
    ]

    if components.get("banner", {}).get("enabled"):
        criteria.append({
            "prompt": (
                "The requested negative space must remain calm, uncluttered, "
                "and suitable for a separately added campaign headline."
            ),
            "label": (
                "Die vorgesehene Freifläche bleibt ruhig und für eine separat "
                "ergänzte Kampagnenheadline nutzbar."
            )
        })

    return criteria


def build_text_to_image_sections(components):
    creative_direction = components.get("creativeDirection", "") or (
        "Use the visual language of professional employer-branding campaign "
        "photography with authentic documentary realism."
    )

    task_parts = [
        components.get("campaignGoal", ""),
        components.get("employerBenefit", "")
    ]
    task_text = " ".join(
        ensure_sentence(part)
        for part in task_parts
        if part
    ) or (
        "Create a completely fictional recruiting image that communicates a "
        "credible employer benefit."
    )

    context_parts = [
        components.get("targetGroup", ""),
        components.get("employerContext", ""),
        components.get("competitorInsight", "")
    ]
    context_text = " ".join(
        ensure_sentence(part)
        for part in context_parts
        if part
    ) or (
        "The image is intended for an early-career employer-branding campaign."
    )

    visual_parts = []

    for field_id in ("personConcept", "workContext", "action"):
        value = components.get(field_id, "")

        if value:
            visual_parts.append(ensure_sentence(value))

    pose = components.get("pose", "")

    if pose:
        visual_parts.append(
            ensure_sentence("The main person is " + pose)
        )

    expression = components.get("expression", "")
    gaze = components.get("gaze", "")
    face_parts = []

    if expression:
        face_parts.append("has " + expression)

    if gaze:
        face_parts.append("is " + gaze)

    if face_parts:
        visual_parts.append(
            "The main person " + "; ".join(face_parts) + "."
        )

    outfit = components.get("outfit", "")

    if outfit:
        visual_parts.append(
            ensure_sentence("The main person is " + outfit)
        )

    visual_style_parts = [
        components.get(field_id, "")
        for field_id in (
            "framing",
            "cameraAngle",
            "lighting",
            "imageEffect"
        )
        if components.get(field_id, "")
    ]

    if visual_style_parts:
        visual_parts.append(
            ensure_sentence(
                "Use " + ", ".join(visual_style_parts)
            )
        )

    extra_prompt = components.get("extraPrompt", "")

    if extra_prompt:
        visual_parts.append(
            ensure_sentence("Additional requested detail: " + extra_prompt)
        )

    visual_text = " ".join(visual_parts) or (
        "Show a credible fictional young adult participating in a concrete "
        "workplace task with natural body language and realistic work objects."
    )

    constraints_text = (
        "Do not imitate or depict any identifiable real person. Do not present "
        "fictional people as real employees or testimonials. No company logos, "
        "readable brand names, employee identification cards, exaggerated "
        "enthusiasm, tokenism, discriminatory content, or stereotypical "
        "depiction. Human review is required before publication."
    )

    aspect_ratio = components.get("aspectRatio", "16:9") or "16:9"
    output_parts = [
        OUTPUT_FORMATS.get(aspect_ratio, OUTPUT_FORMATS["16:9"])
    ]
    banner = components.get("banner", {})

    if banner.get("enabled"):
        banner_position = banner.get("position", "auto")

        if banner_position == "auto":
            banner_position = "bottom"

        output_parts.append(
            "Leave clean negative space on the " + banner_position +
            " side of the image for a separately added campaign headline; "
            "keep this area calm and free of generated typography."
        )

    output_text = " ".join(output_parts)
    success_criteria = build_success_criteria(components)
    success_text = " ".join(
        f"{index}. {criterion['prompt']}"
        for index, criterion in enumerate(success_criteria, start=1)
    )

    section_values = [
        ("creative_direction", ensure_sentence(creative_direction)),
        ("task_goal", task_text),
        ("context", context_text),
        ("visual_specification", visual_text),
        ("constraints", constraints_text),
        ("output_format", output_text),
        ("success_criteria", success_text)
    ]

    return [
        {
            "id": section_id,
            "title": TEXT_TO_IMAGE_SECTION_TITLES[section_id],
            "text": text
        }
        for section_id, text in section_values
    ]


def build_image_to_image_prompt(components):
    person_concept = components.get("personConcept", "")
    work_context = components.get("workContext", "") or components.get(
        "brandTone",
        ""
    )
    image_effect = components.get("imageEffect", "")
    action = components.get("action", "")
    expression = components.get("expression", "")
    outfit = components.get("outfit", "")
    framing = components.get("framing", "")
    pose = components.get("pose", "")
    gaze = components.get("gaze", "")
    camera_angle = components.get("cameraAngle", "")
    lighting = components.get("lighting", "")
    extra_prompt = components.get("extraPrompt", "")
    banner = components.get("banner", {})

    selected_anything = any([
        person_concept,
        work_context,
        image_effect,
        action,
        expression,
        outfit,
        framing,
        pose,
        gaze,
        camera_angle,
        lighting,
        extra_prompt,
        banner.get("enabled", False)
    ])

    if not selected_anything:
        return ""

    blocks = [
        "Preserve the uploaded person's identity, face structure, age, "
        "hairstyle, skin tone, natural body proportions, and recognizable "
        "appearance."
    ]

    if person_concept:
        blocks.append("Main subject: " + person_concept)

    if work_context:
        blocks.append("Work setting and activity: " + work_context)

    if action:
        blocks.append("Collaboration and action: " + action)

    if pose:
        blocks.append("Body position: the main person is " + pose + ".")

    face_parts = []

    if expression:
        face_parts.append("has " + expression)

    if gaze:
        face_parts.append("is " + gaze)

    if face_parts:
        blocks.append(
            "Face and gaze: the main person " + "; ".join(face_parts) + "."
        )

    if outfit:
        blocks.append("Clothing: the main person is " + outfit + ".")

    visual_style_parts = [
        part
        for part in (framing, camera_angle, lighting, image_effect)
        if part
    ]

    if visual_style_parts:
        blocks.append(
            "Visual style and composition: use " +
            ", ".join(visual_style_parts) +
            "."
        )

    if extra_prompt:
        blocks.append(ensure_sentence(extra_prompt))

    return "\n\n".join(blocks).strip()


def build_prompt_package(components, mode=DEFAULT_MODE):
    normalized_mode = normalize_mode(mode)

    if normalized_mode == "text_to_image":
        sections = build_text_to_image_sections(components)
        positive_prompt = "\n\n".join(
            section["title"] + "\n" + section["text"]
            for section in sections
        )

        return {
            "positive_prompt": positive_prompt.strip(),
            "negative_prompt": TEXT_TO_IMAGE_NEGATIVE_PROMPT,
            "sections": sections,
            "success_criteria": build_success_criteria(components)
        }

    positive_prompt = build_image_to_image_prompt(components)

    return {
        "positive_prompt": positive_prompt,
        "negative_prompt": "",
        "sections": [],
        "success_criteria": []
    }


def build_prompt_from_components(components, mode=DEFAULT_MODE):
    """Backward-compatible accessor for the positive model prompt."""
    return build_prompt_package(components, mode)["positive_prompt"]


# ---------------------------------------------------------------------------
# Banner Rendering mit Pillow
# ---------------------------------------------------------------------------

def parse_hex_color(value, fallback=(20, 87, 255)):
    value = str(value or "").strip()

    if not value.startswith("#"):
        return fallback

    value = value[1:]

    try:
        if len(value) == 3:
            r = int(value[0] * 2, 16)
            g = int(value[1] * 2, 16)
            b = int(value[2] * 2, 16)
            return (r, g, b)

        if len(value) == 6:
            r = int(value[0:2], 16)
            g = int(value[2:4], 16)
            b = int(value[4:6], 16)
            return (r, g, b)

    except ValueError:
        return fallback

    return fallback


def font_candidates(font_style, bold=False):
    windows_fonts = Path(r"C:\Windows\Fonts")

    if font_style == "editorial":
        names = [
            "georgiab.ttf",
            "georgia.ttf",
            "timesbd.ttf",
            "times.ttf"
        ]
    elif font_style == "condensed":
        names = [
            "bahnschrift.ttf",
            "arialbd.ttf",
            "arial.ttf"
        ]
    elif font_style == "bold":
        names = [
            "arialbd.ttf",
            "segoeuib.ttf",
            "seguisb.ttf",
            "arial.ttf"
        ]
    else:
        names = [
            "segoeuib.ttf",
            "seguisb.ttf",
            "arialbd.ttf",
            "arial.ttf"
        ]

    if not bold:
        names.extend([
            "segoeui.ttf",
            "arial.ttf"
        ])

    return [
        windows_fonts / name
        for name in names
    ]


def load_font(font_style, size, bold=False):
    for path in font_candidates(font_style, bold=bold):
        if path.exists():
            return ImageFont.truetype(str(path), size=size)

    return ImageFont.load_default()


def text_size(draw, text, font):
    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font
    )

    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text(draw, text, font, max_width):
    words = text.split()

    if not words:
        return []

    lines = []
    current = words[0]

    for word in words[1:]:
        test = current + " " + word
        width, _ = text_size(draw, test, font)

        if width <= max_width:
            current = test
        else:
            lines.append(current)
            current = word

    lines.append(current)

    return lines


def fit_text(
    draw,
    text,
    font_style,
    max_width,
    max_height,
    max_size,
    min_size,
    bold=True
):
    for size in range(max_size, min_size - 1, -2):
        font = load_font(
            font_style,
            size,
            bold=bold
        )

        lines = wrap_text(
            draw,
            text,
            font,
            max_width
        )

        line_heights = [
            text_size(draw, line, font)[1]
            for line in lines
        ]

        total_height = (
            sum(line_heights) +
            max(0, len(lines) - 1) * int(size * 0.28)
        )

        widest = max(
            [text_size(draw, line, font)[0] for line in lines] or [0]
        )

        if widest <= max_width and total_height <= max_height:
            return font, lines, total_height

    font = load_font(
        font_style,
        min_size,
        bold=bold
    )

    lines = wrap_text(
        draw,
        text,
        font,
        max_width
    )

    total_height = len(lines) * min_size

    return font, lines, total_height


def draw_gradient_overlay(image, position, height):
    width, image_height = image.size
    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0)
    )

    pixels = overlay.load()

    if position == "top":
        for y in range(height):
            alpha = int(190 * (1 - y / max(1, height)))

            for x in range(width):
                pixels[x, y] = (0, 0, 0, alpha)
    else:
        start_y = image_height - height

        for y in range(start_y, image_height):
            alpha = int(190 * ((y - start_y) / max(1, height)))

            for x in range(width):
                pixels[x, y] = (0, 0, 0, alpha)

    return Image.alpha_composite(
        image,
        overlay
    )


def draw_text_lines(
    draw,
    lines,
    x,
    y,
    font,
    fill,
    align,
    max_width,
    line_gap,
    shadow=False
):
    current_y = y

    for line in lines:
        line_width, line_height = text_size(
            draw,
            line,
            font
        )

        if align == "center":
            line_x = x + (max_width - line_width) / 2
        elif align == "right":
            line_x = x + max_width - line_width
        else:
            line_x = x

        if shadow:
            draw.text(
                (line_x + 3, current_y + 3),
                line,
                font=font,
                fill=(0, 0, 0, 175)
            )

        draw.text(
            (line_x, current_y),
            line,
            font=font,
            fill=fill
        )

        current_y += line_height + line_gap

    return current_y


def render_banner_on_image(image_bytes, banner):
    image = Image.open(BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size

    draw = ImageDraw.Draw(image)

    position = banner.get("position", "auto")

    if position == "auto":
        position = "bottom"

    style = banner.get("style", "dark_glass")
    font_style = banner.get("font", "modern")
    align = banner.get("align", "left")
    brand_color = parse_hex_color(
        banner.get("color", "#1457ff")
    )

    margin = int(min(width, height) * 0.045)
    radius = int(min(width, height) * 0.035)

    if position == "right":
        box_width = int(width * 0.38)
        box_height = height - 2 * margin
        box_x = width - box_width - margin
        box_y = margin
        inner_pad = int(box_width * 0.10)
        text_max_width = box_width - 2 * inner_pad
        headline_max_height = int(box_height * 0.45)
        max_headline_size = int(width * 0.055)
    else:
        box_width = width - 2 * margin
        box_height = int(height * 0.24)
        box_x = margin
        box_y = margin if position == "top" else height - box_height - margin
        inner_pad = int(box_height * 0.20)
        text_max_width = box_width - 2 * inner_pad
        headline_max_height = int(box_height * 0.48)
        max_headline_size = int(width * 0.070)

    headline = banner.get("text", "")
    subtext = banner.get("subtext", "")

    headline_font, headline_lines, headline_height = fit_text(
        draw=draw,
        text=headline,
        font_style=font_style,
        max_width=text_max_width,
        max_height=headline_max_height,
        max_size=max(44, min(96, max_headline_size)),
        min_size=26,
        bold=True
    )

    sub_font = None
    sub_lines = []
    sub_height = 0

    if subtext:
        sub_font, sub_lines, sub_height = fit_text(
            draw=draw,
            text=subtext,
            font_style="modern",
            max_width=text_max_width,
            max_height=int(box_height * 0.28),
            max_size=max(22, int(max_headline_size * 0.42)),
            min_size=16,
            bold=False
        )

    line_gap = int(headline_font.size * 0.25)
    sub_gap = int(headline_font.size * 0.28)

    total_text_height = headline_height

    if sub_lines:
        total_text_height += sub_gap + sub_height

    if total_text_height + 2 * inner_pad > box_height:
        box_height = total_text_height + 2 * inner_pad

        if position == "bottom":
            box_y = height - box_height - margin

    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0)
    )

    overlay_draw = ImageDraw.Draw(overlay)

    if style == "dark_glass":
        overlay_draw.rounded_rectangle(
            (box_x, box_y, box_x + box_width, box_y + box_height),
            radius=radius,
            fill=(10, 14, 24, 188)
        )

        text_fill = (255, 255, 255, 255)
        sub_fill = (235, 238, 245, 238)
        shadow = False

    elif style == "light_panel":
        overlay_draw.rounded_rectangle(
            (box_x, box_y, box_x + box_width, box_y + box_height),
            radius=radius,
            fill=(255, 255, 255, 224)
        )

        text_fill = (16, 24, 39, 255)
        sub_fill = (55, 65, 81, 245)
        shadow = False

    elif style == "brand_bar":
        overlay_draw.rounded_rectangle(
            (box_x, box_y, box_x + box_width, box_y + box_height),
            radius=radius,
            fill=(*brand_color, 232)
        )

        accent_width = max(
            8,
            int(box_width * 0.012)
        )

        overlay_draw.rounded_rectangle(
            (box_x, box_y, box_x + accent_width, box_y + box_height),
            radius=radius,
            fill=(255, 255, 255, 235)
        )

        text_fill = (255, 255, 255, 255)
        sub_fill = (245, 247, 255, 238)
        shadow = False

    elif style == "gradient_bottom":
        image = draw_gradient_overlay(
            image,
            "top" if position == "top" else "bottom",
            box_height + margin * 2
        )

        overlay = Image.new(
            "RGBA",
            image.size,
            (0, 0, 0, 0)
        )

        text_fill = (255, 255, 255, 255)
        sub_fill = (240, 242, 248, 240)
        shadow = True

    else:
        text_fill = (255, 255, 255, 255)
        sub_fill = (245, 245, 245, 238)
        shadow = True

    image = Image.alpha_composite(
        image,
        overlay
    )

    draw = ImageDraw.Draw(image)

    text_x = box_x + inner_pad
    text_y = box_y + int((box_height - total_text_height) / 2)

    next_y = draw_text_lines(
        draw=draw,
        lines=headline_lines,
        x=text_x,
        y=text_y,
        font=headline_font,
        fill=text_fill,
        align=align,
        max_width=text_max_width,
        line_gap=line_gap,
        shadow=shadow
    )

    if sub_lines and sub_font:
        draw_text_lines(
            draw=draw,
            lines=sub_lines,
            x=text_x,
            y=next_y + sub_gap,
            font=sub_font,
            fill=sub_fill,
            align=align,
            max_width=text_max_width,
            line_gap=int(sub_font.size * 0.25),
            shadow=shadow
        )

    output = BytesIO()

    image.convert("RGB").save(
        output,
        format="PNG",
        optimize=True
    )

    output.seek(0)

    return output.getvalue()


# ---------------------------------------------------------------------------
# AI-Overlay / Kennzeichnung
# ---------------------------------------------------------------------------

def render_ai_overlay_on_image(image_bytes):
    """
    Legt das transparente PNG static/assets/ai_generated_overlay.png
    proportional skaliert und zentriert über das Ergebnisbild.

    Wenn die Datei fehlt, wird das Bild unverändert zurückgegeben.
    """
    if not AI_OVERLAY_PATH.exists():
        print(
            f"WARNUNG: AI-Overlay nicht gefunden: {AI_OVERLAY_PATH}"
        )
        return image_bytes

    base = Image.open(BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.open(AI_OVERLAY_PATH).convert("RGBA")

    base_w, base_h = base.size
    overlay_w, overlay_h = overlay.size

    scale = min(
        base_w / overlay_w,
        base_h / overlay_h
    ) * 0.24

    new_size = (
        max(1, int(overlay_w * scale)),
        max(1, int(overlay_h * scale))
    )

    overlay = overlay.resize(
        new_size,
        Image.LANCZOS
    )

    margin_x = int(base_w * 0.035)
    margin_y = int(base_h * 0.035)

    x = base_w - overlay.width - margin_x
    y = margin_y

    layer = Image.new(
        "RGBA",
        base.size,
        (0, 0, 0, 0)
    )

    layer.paste(
        overlay,
        (x, y),
        overlay
    )

    result = Image.alpha_composite(
        base,
        layer
    )

    output = BytesIO()

    result.convert("RGB").save(
        output,
        format="PNG",
        optimize=True
    )

    output.seek(0)

    return output.getvalue()


def save_generated_image(image_bytes):
    filename = f"campaign_{uuid.uuid4().hex}.png"
    path = GENERATED_DIR / filename
    path.write_bytes(image_bytes)

    return f"/static/generated/{filename}"


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------

def validate_uploaded_image(file_bytes, filename):
    max_bytes = int(MODEL_CONFIG.get("max_upload_bytes", 15_000_000))
    max_pixels = int(MODEL_CONFIG.get("max_upload_pixels", 30_000_000))

    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty"
        )

    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail="The uploaded image exceeds the configured size limit."
        )

    try:
        with Image.open(BytesIO(file_bytes)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is not a valid image."
        ) from exc

    if image_format not in {"JPEG", "PNG", "WEBP"}:
        raise HTTPException(
            status_code=400,
            detail="Only JPEG, PNG, and WebP images are supported."
        )

    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise HTTPException(
            status_code=400,
            detail="The uploaded image dimensions are not supported."
        )

    safe_name = Path(filename or "input.png").name

    return safe_name or "input.png"

def comfy_upload_image(file_bytes, filename):
    files = {
        "image": (filename, file_bytes)
    }

    response = requests.post(
        f"{COMFY}/upload/image",
        files=files,
        timeout=60
    )

    response.raise_for_status()

    return response.json()["name"]


def queue_prompt(workflow):
    payload = {
        "prompt": workflow,
        "client_id": "webapp"
    }

    response = requests.post(
        f"{COMFY}/prompt",
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    return response.json()["prompt_id"]


def wait_for_result(prompt_id):
    while True:
        response = requests.get(
            f"{COMFY}/history/{prompt_id}",
            timeout=30
        )

        response.raise_for_status()

        history = response.json()

        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})

            found_images = []

            for node_output in outputs.values():
                if "images" in node_output:
                    found_images.extend(node_output["images"])

            if found_images:
                return found_images

        time.sleep(0.4)


def patch_text_to_image_workflow(
    workflow,
    prompt,
    negative_prompt,
    components,
    mode_config
):
    nodes = mode_config["nodes"]
    model = mode_config["model"]
    generation = mode_config["generation"]

    model_loader = workflow[nodes["model_loader"]]["inputs"]
    clip_loader = workflow[nodes["clip_loader"]]["inputs"]
    vae_loader = workflow[nodes["vae_loader"]]["inputs"]
    positive = workflow[nodes["positive_prompt"]]["inputs"]
    negative = workflow[nodes["negative_prompt"]]["inputs"]
    latent = workflow[nodes["latent_image"]]["inputs"]
    sampler = workflow[nodes["sampler"]]["inputs"]

    model_loader["unet_name"] = model["unet_name"]
    clip_loader["clip_name"] = model["clip_name"]
    vae_loader["vae_name"] = model["vae_name"]
    positive["text"] = prompt
    negative["text"] = negative_prompt or TEXT_TO_IMAGE_NEGATIVE_PROMPT

    aspect_ratio = components.get(
        "aspectRatio",
        generation.get("default_aspect_ratio", "1:1")
    )
    dimensions = generation["aspect_ratios"].get(aspect_ratio)

    if not dimensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported aspect ratio: {aspect_ratio}"
        )

    latent["width"] = int(dimensions[0])
    latent["height"] = int(dimensions[1])
    generation_settings = components.get("generation", {})

    if generation_settings.get("fixed_seed"):
        sampler["seed"] = int(generation_settings["seed"])
    else:
        sampler["seed"] = uuid.uuid4().int % 1_000_000_000_000_000
    sampler["steps"] = int(generation.get("steps", 50))
    sampler["cfg"] = float(generation.get("cfg", 4.0))
    sampler["sampler_name"] = generation.get("sampler_name", "euler")
    sampler["scheduler"] = generation.get("scheduler", "simple")

    return workflow


def patch_image_to_image_workflow(workflow, image_name, prompt):
    workflow["78"]["inputs"]["image"] = image_name
    workflow["435"]["inputs"]["value"] = prompt
    workflow["433:111"]["inputs"]["prompt"] = ["435", 0]
    workflow["433:3"]["inputs"]["seed"] = (
        uuid.uuid4().int % 1_000_000_000_000_000
    )

    return workflow


def patch_workflow(
    workflow,
    mode,
    prompt,
    components,
    image_name=None,
    negative_prompt=""
):
    normalized_mode = normalize_mode(mode)

    if normalized_mode == "text_to_image":
        return patch_text_to_image_workflow(
            workflow,
            prompt,
            negative_prompt,
            components,
            get_mode_config(normalized_mode)
        )

    if not image_name:
        raise HTTPException(
            status_code=400,
            detail="Image-to-Image mode requires an uploaded image."
        )

    return patch_image_to_image_workflow(
        workflow,
        image_name,
        prompt
    )


def track_progress(prompt_id):
    ws = websocket.WebSocket()
    ws.connect("ws://127.0.0.1:8188/ws?clientId=webapp")

    try:
        while True:
            msg = ws.recv()

            if isinstance(msg, bytes):
                continue

            data = json.loads(msg)
            event_type = data.get("type")

            if event_type in ("execution_progress", "progress"):
                progress_data = data.get("data", {})
                value = progress_data.get("value")
                max_value = progress_data.get("max")

                if (
                    isinstance(value, int)
                    and isinstance(max_value, int)
                    and max_value > 0
                ):
                    progress_state["value"] = int(
                        (value / max_value) * 100
                    )

            if event_type == "executed":
                event_data = data.get("data", {})

                if event_data.get("prompt_id") == prompt_id:
                    progress_state["value"] = 100
                    break

    finally:
        ws.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return FileResponse(
        str(INDEX_PATH),
        headers=no_cache_headers()
    )


@app.get("/api/config")
def public_config():
    modes = []

    for mode_name, mode_config in MODE_CONFIGS.items():
        modes.append({
            "id": mode_name,
            "label": mode_config.get("label", mode_name),
            "description": mode_config.get("description", ""),
            "experimental": bool(mode_config.get("experimental", False)),
            "requires_upload": bool(mode_config.get("requires_upload", False))
        })

    return JSONResponse(
        content={
            "default_mode": DEFAULT_MODE,
            "modes": modes
        },
        headers=no_cache_headers()
    )


@app.get("/api/ui-fields")
def ui_fields(mode: str = Query(DEFAULT_MODE)):
    normalized_mode = normalize_mode(mode)
    fields = load_ui_fields(normalized_mode)

    return JSONResponse(
        content=fields,
        headers=no_cache_headers()
    )


@app.get("/api/progress")
def progress():
    return JSONResponse(
        content=progress_state,
        headers=no_cache_headers()
    )


@app.post("/api/preview-prompt")
async def preview_prompt(payload: dict = Body(...)):
    mode = normalize_mode(payload.get("mode", DEFAULT_MODE))
    raw_components = payload.get("components", payload)

    components = normalize_prompt_components(raw_components, mode)
    prompt_package = build_prompt_package(components, mode)

    return JSONResponse(
        content={
            "mode": mode,
            "prompt": prompt_package["positive_prompt"],
            "positive_prompt": prompt_package["positive_prompt"],
            "negative_prompt": prompt_package["negative_prompt"],
            "sections": prompt_package["sections"],
            "success_criteria": prompt_package["success_criteria"],
            "components": components
        },
        headers=no_cache_headers()
    )


@app.post("/api/run")
async def run(
    prompt_components: str = Form(...),
    mode: str = Form(DEFAULT_MODE),
    consent_confirmed: bool = Form(False),
    file: Optional[UploadFile] = File(None)
):
    normalized_mode = normalize_mode(mode)
    mode_config = get_mode_config(normalized_mode)

    try:
        raw_components = json.loads(prompt_components)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"prompt_components is not valid JSON: {exc}"
        )

    components = normalize_prompt_components(
        raw_components,
        normalized_mode
    )
    prompt_package = build_prompt_package(
        components,
        normalized_mode
    )
    final_prompt = prompt_package["positive_prompt"]
    negative_prompt = prompt_package["negative_prompt"]

    if not final_prompt:
        raise HTTPException(
            status_code=400,
            detail="Prompt is empty"
        )

    if DEBUG_PROMPTS:
        print("\n" + "=" * 100)
        print("GENERATION MODE:", normalized_mode)
        print("PROMPT COMPONENTS:")
        print(json.dumps(components, indent=2, ensure_ascii=False))
        print("FINAL SERVER PROMPT:")
        print(final_prompt)
        print("=" * 100 + "\n")

    stored_image_name = None

    if mode_config.get("requires_upload"):
        if not consent_confirmed:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Image-to-Image mode requires confirmation that the "
                    "image may be processed and altered."
                )
            )

        if file is None:
            raise HTTPException(
                status_code=400,
                detail="Image-to-Image mode requires an uploaded image."
            )

        image_bytes = await file.read()
        safe_filename = validate_uploaded_image(
            image_bytes,
            file.filename
        )
        stored_image_name = comfy_upload_image(
            image_bytes,
            safe_filename
        )

    workflow = deep_copy_workflow(normalized_mode)

    workflow = patch_workflow(
        workflow=workflow,
        mode=normalized_mode,
        prompt=final_prompt,
        components=components,
        image_name=stored_image_name,
        negative_prompt=negative_prompt
    )

    progress_state["value"] = 0

    prompt_id = queue_prompt(workflow)

    threading.Thread(
        target=track_progress,
        args=(prompt_id,),
        daemon=True
    ).start()

    images = wait_for_result(prompt_id)

    view_urls = []

    banner = components.get("banner", {})

    for image in images:
        query = urlencode({
            "filename": image["filename"],
            "subfolder": image.get("subfolder", ""),
            "type": image["type"]
        })

        comfy_view_url = f"{COMFY}/view?{query}"

        response = requests.get(
            comfy_view_url,
            timeout=60
        )

        response.raise_for_status()

        final_image_bytes = response.content

        if banner.get("enabled"):
            final_image_bytes = render_banner_on_image(
                final_image_bytes,
                banner
            )

        final_image_bytes = render_ai_overlay_on_image(
            final_image_bytes
        )

        view_urls.append(
            save_generated_image(final_image_bytes)
        )

    return JSONResponse(
        content={
            "mode": normalized_mode,
            "prompt_id": prompt_id,
            "submitted_prompt": final_prompt,
            "submitted_negative_prompt": negative_prompt,
            "prompt_sections": prompt_package["sections"],
            "success_criteria": prompt_package["success_criteria"],
            "prompt_components": components,
            "view_urls": view_urls
        },
        headers=no_cache_headers()
    )

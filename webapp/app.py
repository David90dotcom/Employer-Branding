from fastapi import FastAPI, HTTPException, Form, Body, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import json
import os
import sqlite3
import uuid
import requests
import time
import threading
import websocket

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageDraw, ImageFont


COMFY = "http://127.0.0.1:8188"

BASE_DIR = Path(__file__).resolve().parent
MODEL_CONFIG_PATH = BASE_DIR / "model_config.json"
INDEX_PATH = BASE_DIR / "static" / "index.html"

GENERATED_DIR = BASE_DIR / "static" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = BASE_DIR / "data"
TEMP_RESULTS_DIR = DATA_DIR / "temporary_results"
LIBRARY_DIR = DATA_DIR / "campaign_library"
LIBRARY_IMAGES_DIR = LIBRARY_DIR / "images"
LIBRARY_THUMBNAILS_DIR = LIBRARY_DIR / "thumbnails"
LIBRARY_DB_PATH = LIBRARY_DIR / "library.sqlite3"

TEMP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

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

TEMP_RESULTS = {}
TEMP_RESULTS_LOCK = threading.Lock()

TEMP_RESULT_RETENTION_HOURS = int(
    MODEL_CONFIG.get("temporary_result_retention_hours", 24)
)

LIBRARY_SAVEABLE_MODES = {
    "text_to_image",
    "prompt_chain"
}

TEXT_TO_IMAGE_BRIEFING_SECTION_TITLES = {
    "campaign_goal": "KAMPAGNENZIEL",
    "target_context": "ZIELGRUPPE UND KONTEXT",
    "employer_benefit": "ARBEITGEBERNUTZEN",
    "competitor_insight": "WETTBEWERBSIMPULS",
    "creative_direction": "GESTALTUNGSRAHMEN"
}

TEXT_TO_IMAGE_RENDER_SECTION_TITLES = {
    "visual_direction": "VISUAL DIRECTION",
    "visible_campaign_intent": "VISIBLE CAMPAIGN INTENT",
    "subjects_workplace": "SUBJECTS AND WORKPLACE",
    "action_appearance": "ACTION AND APPEARANCE",
    "composition_light": "COMPOSITION AND LIGHT",
    "visual_constraints": "VISUAL CONSTRAINTS",
    "output": "OUTPUT"
}

# Backward-compatible name for integrations that already inspect this mapping.
TEXT_TO_IMAGE_SECTION_TITLES = TEXT_TO_IMAGE_RENDER_SECTION_TITLES

PROMPT_CHAIN_SECTION_TITLES = {
    "role_method": "ROLE AND METHOD",
    "primary_task": "PRIMARY OPTIMIZATION TASK",
    "source_preservation": "SOURCE AND PRESERVATION",
    "requested_changes": "REQUESTED VISUAL CHANGES",
    "constraints": "CONSTRAINTS",
    "output_criteria": "OUTPUT"
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
    "discriminatory depiction, child, children, minor, underage person, "
    "teenager, school pupil, school uniform, family with children, youthful "
    "background person, age-ambiguous person, unnecessary bystanders, crowd, "
    "nudity, sexual content, pornography, violence, blood, weapons, drugs, "
    "hate symbols, extremist symbols"
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
# Lokale Kampagnenbibliothek
# ---------------------------------------------------------------------------

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_asset_id(raw_id, label="asset id"):
    try:
        return uuid.UUID(str(raw_id or "")).hex
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label}."
        ) from exc


def library_connection():
    connection = sqlite3.connect(
        str(LIBRARY_DB_PATH),
        timeout=10
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_library_storage():
    LIBRARY_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    with library_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_assets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                parent_id TEXT,
                filename TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                thumbnail_filename TEXT NOT NULL,
                positive_prompt TEXT NOT NULL,
                negative_prompt TEXT NOT NULL,
                components_json TEXT NOT NULL,
                seed INTEGER,
                model_name TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                FOREIGN KEY(parent_id)
                    REFERENCES campaign_assets(id)
                    ON DELETE SET NULL
            )
            """
        )


def cleanup_temporary_result_files():
    if TEMP_RESULT_RETENTION_HOURS <= 0:
        return

    cutoff = time.time() - TEMP_RESULT_RETENTION_HOURS * 60 * 60

    def entry_is_expired(entry):
        try:
            return (
                entry["display_path"].stat().st_mtime < cutoff or
                entry["source_path"].stat().st_mtime < cutoff
            )
        except OSError:
            return True

    with TEMP_RESULTS_LOCK:
        expired_entries = [
            entry
            for entry in TEMP_RESULTS.values()
            if entry_is_expired(entry)
        ]

        for entry in expired_entries:
            TEMP_RESULTS.pop(entry["id"], None)

    for entry in expired_entries:
        for key in ("display_path", "source_path"):
            try:
                entry[key].unlink()
            except FileNotFoundError:
                pass

    for directory, pattern in (
        (TEMP_RESULTS_DIR, "*.png"),
        (GENERATED_DIR, "campaign_*.png")
    ):
        paths = directory.glob(pattern)

        for path in paths:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue


def create_thumbnail(image_bytes, max_size=(420, 260)):
    with Image.open(BytesIO(image_bytes)) as image:
        thumbnail = image.convert("RGB")
        thumbnail.thumbnail(max_size, Image.LANCZOS)

        output = BytesIO()
        thumbnail.save(
            output,
            format="PNG",
            optimize=True
        )
        return output.getvalue()


def register_temporary_result(display_bytes, source_bytes, metadata):
    cleanup_temporary_result_files()
    TEMP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    result_id = uuid.uuid4().hex
    display_path = TEMP_RESULTS_DIR / f"{result_id}_display.png"
    source_path = TEMP_RESULTS_DIR / f"{result_id}_source.png"

    display_path.write_bytes(display_bytes)
    source_path.write_bytes(source_bytes)

    entry = {
        "id": result_id,
        "display_path": display_path,
        "source_path": source_path,
        "created_at": utc_now_iso(),
        "library_id": None,
        **metadata
    }

    with TEMP_RESULTS_LOCK:
        TEMP_RESULTS[result_id] = entry

    return {
        "result_id": result_id,
        "view_url": f"/api/results/{result_id}/image",
        "saved": False,
        "mode": entry.get("mode", DEFAULT_MODE),
        "parent_id": entry.get("parent_id")
    }


def get_temporary_result(raw_result_id):
    result_id = normalize_asset_id(raw_result_id, "result id")

    with TEMP_RESULTS_LOCK:
        entry = TEMP_RESULTS.get(result_id)

    if not entry:
        raise HTTPException(
            status_code=404,
            detail=(
                "Temporary result not found. It may have expired or the "
                "application may have been restarted."
            )
        )

    if not entry["display_path"].exists() or not entry["source_path"].exists():
        raise HTTPException(
            status_code=404,
            detail="Temporary result files are no longer available."
        )

    return entry


def serialize_library_row(row):
    components = json.loads(row["components_json"] or "{}")

    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "saved_at": row["saved_at"],
        "mode": row["mode"],
        "parent_id": row["parent_id"],
        "positive_prompt": row["positive_prompt"],
        "negative_prompt": row["negative_prompt"],
        "components": components,
        "seed": row["seed"],
        "model_name": row["model_name"],
        "width": row["width"],
        "height": row["height"],
        "image_url": f"/api/library/{row['id']}/image",
        "thumbnail_url": f"/api/library/{row['id']}/thumbnail"
    }


def get_library_row(raw_item_id):
    item_id = normalize_asset_id(raw_item_id, "library item id")

    with library_connection() as connection:
        row = connection.execute(
            "SELECT * FROM campaign_assets WHERE id = ?",
            (item_id,)
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Library item not found."
        )

    return row


def list_library_items():
    with library_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM campaign_assets
            ORDER BY saved_at DESC
            """
        ).fetchall()

    return [serialize_library_row(row) for row in rows]


def resolve_mode_model_name(mode):
    mode_config = get_mode_config(mode)
    configured_name = mode_config.get("model", {}).get("unet_name")

    if configured_name:
        return configured_name

    for node in WORKFLOW_TEMPLATES.get(mode, {}).values():
        unet_name = node.get("inputs", {}).get("unet_name")

        if unet_name:
            return unet_name

    return mode_config.get("label", mode)


def persist_temporary_result(raw_result_id, title=""):
    entry = get_temporary_result(raw_result_id)

    if entry.get("mode") not in LIBRARY_SAVEABLE_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only fully synthetic Text-to-Image and Prompt Chain "
                "results can be saved in the campaign library."
            )
        )

    if entry.get("library_id"):
        try:
            return serialize_library_row(
                get_library_row(entry["library_id"])
            )
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

            with TEMP_RESULTS_LOCK:
                entry["library_id"] = None

    cleaned_title = " ".join(str(title or "").split())

    if len(cleaned_title) > 100:
        raise HTTPException(
            status_code=400,
            detail="Library title is too long."
        )

    if not cleaned_title:
        cleaned_title = "Gespeichertes Kampagnenmotiv"

    item_id = uuid.uuid4().hex
    filename = f"{item_id}.png"
    source_filename = f"{item_id}_source.png"
    thumbnail_filename = f"{item_id}_thumb.png"

    display_bytes = entry["display_path"].read_bytes()
    source_bytes = entry["source_path"].read_bytes()

    image_path = LIBRARY_IMAGES_DIR / filename
    source_path = LIBRARY_IMAGES_DIR / source_filename
    thumbnail_path = LIBRARY_THUMBNAILS_DIR / thumbnail_filename

    image_path.write_bytes(display_bytes)
    source_path.write_bytes(source_bytes)
    thumbnail_path.write_bytes(create_thumbnail(display_bytes))

    with Image.open(BytesIO(display_bytes)) as image:
        width, height = image.size

    components = entry.get("components", {})
    generation = components.get("generation", {})
    seed = generation.get("actual_seed")

    if seed is None and generation.get("fixed_seed"):
        seed = generation.get("seed")
    mode = entry.get("mode", DEFAULT_MODE)
    model_name = resolve_mode_model_name(mode)

    with library_connection() as connection:
        connection.execute(
            """
            INSERT INTO campaign_assets (
                id,
                title,
                created_at,
                saved_at,
                mode,
                parent_id,
                filename,
                source_filename,
                thumbnail_filename,
                positive_prompt,
                negative_prompt,
                components_json,
                seed,
                model_name,
                width,
                height
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                cleaned_title,
                entry["created_at"],
                utc_now_iso(),
                mode,
                entry.get("parent_id"),
                filename,
                source_filename,
                thumbnail_filename,
                entry.get("positive_prompt", ""),
                entry.get("negative_prompt", ""),
                json.dumps(components, ensure_ascii=False),
                seed,
                model_name,
                width,
                height
            )
        )

    with TEMP_RESULTS_LOCK:
        entry["library_id"] = item_id

    return serialize_library_row(get_library_row(item_id))


def delete_library_item(raw_item_id):
    row = get_library_row(raw_item_id)

    paths = [
        LIBRARY_IMAGES_DIR / row["filename"],
        LIBRARY_IMAGES_DIR / row["source_filename"],
        LIBRARY_THUMBNAILS_DIR / row["thumbnail_filename"]
    ]

    with library_connection() as connection:
        connection.execute(
            "DELETE FROM campaign_assets WHERE id = ?",
            (row["id"],)
        )

    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def delete_temporary_result(raw_result_id):
    entry = get_temporary_result(raw_result_id)

    for key in ("display_path", "source_path"):
        try:
            entry[key].unlink()
        except FileNotFoundError:
            pass

    with TEMP_RESULTS_LOCK:
        TEMP_RESULTS.pop(entry["id"], None)


init_library_storage()
cleanup_temporary_result_files()


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


def get_selected_ui_options(components, mode):
    """Return the configured option metadata for each selected field value."""
    selected = {}

    for field in load_ui_fields(mode):
        field_id = field.get("id")
        selected_value = str(components.get(field_id, "") or "").strip()

        if not field_id or not selected_value:
            continue

        for option in field.get("options", []):
            if str(option.get("value", "")) == selected_value:
                selected[field_id] = option
                break

    return selected


def selected_option_label(selected_options, field_id):
    option = selected_options.get(field_id, {})
    return str(option.get("label", "") or "").strip()


def build_text_to_image_briefing_sections(
    components,
    selected_options=None
):
    """Build the campaign rationale that remains outside the model prompt."""
    selected_options = selected_options or get_selected_ui_options(
        components,
        "text_to_image"
    )

    campaign_goal = selected_option_label(
        selected_options,
        "campaignGoal"
    )
    target_group = selected_option_label(
        selected_options,
        "targetGroup"
    )
    employer_context = selected_option_label(
        selected_options,
        "employerContext"
    )
    employer_benefit = selected_option_label(
        selected_options,
        "employerBenefit"
    )
    competitor_insight = selected_option_label(
        selected_options,
        "competitorInsight"
    )
    creative_direction = selected_option_label(
        selected_options,
        "creativeDirection"
    )

    section_values = []

    if campaign_goal:
        section_values.append(("campaign_goal", campaign_goal))

    target_context_parts = []

    if target_group:
        target_context_parts.append("Zielgruppe: " + target_group)

    if employer_context:
        target_context_parts.append("Kontext: " + employer_context)

    if target_context_parts:
        section_values.append(
            ("target_context", "; ".join(target_context_parts))
        )

    if employer_benefit:
        section_values.append(("employer_benefit", employer_benefit))

    if competitor_insight:
        section_values.append(("competitor_insight", competitor_insight))

    if creative_direction:
        section_values.append(("creative_direction", creative_direction))

    return [
        {
            "id": section_id,
            "title": TEXT_TO_IMAGE_BRIEFING_SECTION_TITLES[section_id],
            "text": text
        }
        for section_id, text in section_values
    ]


def build_text_to_image_translation_steps(
    components,
    selected_options=None
):
    """Translate abstract campaign choices into observable image evidence."""
    selected_options = selected_options or get_selected_ui_options(
        components,
        "text_to_image"
    )
    steps = []

    for field_id in (
        "campaignGoal",
        "employerBenefit",
        "competitorInsight"
    ):
        option = selected_options.get(field_id, {})
        instruction = str(
            option.get("visual_translation", "") or ""
        ).strip()

        if not instruction:
            continue

        steps.append({
            "field_id": field_id,
            "source": str(option.get("label", "") or "").strip(),
            "instruction": ensure_sentence(instruction)
        })

    return steps


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
                "Every visible person must be a clearly recognizable fictional "
                "adult aged 18 or older; no children, minors, school pupils, "
                "or age-ambiguous background people may appear."
            ),
            "label": (
                "Alle sichtbaren Personen sind eindeutig als volljährige "
                "fiktive Erwachsene erkennbar; Minderjährige oder altersmäßig "
                "uneindeutige Hintergrundpersonen sind ausgeschlossen."
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


def build_text_to_image_render_sections(
    components,
    selected_options=None
):
    """Build only visually actionable instructions for the image model."""
    selected_options = selected_options or get_selected_ui_options(
        components,
        "text_to_image"
    )
    creative_direction = components.get("creativeDirection", "") or (
        "Use the visual language of professional employer-branding campaign "
        "photography with authentic documentary realism."
    )

    translation_steps = build_text_to_image_translation_steps(
        components,
        selected_options
    )
    visible_intent_text = " ".join(
        step["instruction"]
        for step in translation_steps
    ) or (
        "Make the recruiting message visible through an observable workplace "
        "activity rather than generated campaign text."
    )

    subject_parts = []

    for field_id in ("personConcept", "workContext"):
        value = components.get(field_id, "")

        if value:
            subject_parts.append(ensure_sentence(value))

    subjects_text = " ".join(subject_parts) or (
        "Show one clearly adult fictional person participating in a concrete "
        "workplace task in a credible, brand-neutral work environment."
    )

    action_parts = []
    action = components.get("action", "")

    if action:
        action_parts.append(ensure_sentence(action))

    pose = components.get("pose", "")

    if pose:
        action_parts.append(
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
        action_parts.append(
            "The main person " + "; ".join(face_parts) + "."
        )

    outfit = components.get("outfit", "")

    if outfit:
        action_parts.append(
            ensure_sentence("The main person is " + outfit)
        )

    extra_prompt = components.get("extraPrompt", "")

    if extra_prompt:
        action_parts.append(
            ensure_sentence("Additional visible detail: " + extra_prompt)
        )

    action_text = " ".join(action_parts) or (
        "Use natural task-focused body language, plausible hands, and a gaze "
        "that connects the main person to the visible work object."
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
        composition_text = ensure_sentence(
            "Use " + ", ".join(visual_style_parts)
        )
    else:
        composition_text = (
            "Use an eye-level documentary composition, realistic light, "
            "natural proportions, and credible workplace detail."
        )

    constraints_text = (
        "Do not imitate or depict any identifiable real person. Do not present "
        "fictional people as real employees or testimonials. No company logos, "
        "readable brand names, employee identification cards, generated "
        "typography, exaggerated enthusiasm, tokenism, discriminatory content, "
        "or stereotypical depiction. Every visible person must be a clearly "
        "recognizable fictional adult aged 18 or older. Do not depict children, "
        "minors, school pupils, school uniforms, families with children, or "
        "people whose age appears ambiguous. Show only the people required for "
        "the selected workplace task and keep the background free of "
        "unnecessary bystanders."
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

    section_values = [
        ("visual_direction", ensure_sentence(creative_direction)),
        ("visible_campaign_intent", visible_intent_text),
        ("subjects_workplace", subjects_text),
        ("action_appearance", action_text),
        ("composition_light", composition_text),
        ("visual_constraints", constraints_text),
        ("output", output_text)
    ]

    return [
        {
            "id": section_id,
            "title": TEXT_TO_IMAGE_RENDER_SECTION_TITLES[section_id],
            "text": text
        }
        for section_id, text in section_values
    ]


def build_text_to_image_sections(components):
    """Backward-compatible alias for the actual render-prompt sections."""
    return build_text_to_image_render_sections(components)


def build_prompt_chain_success_criteria(components):
    criteria = [
        {
            "prompt": (
                "The primary optimization goal must be clearly visible in "
                "the revised campaign image."
            ),
            "label": (
                "Das primäre Optimierungsziel ist im überarbeiteten Motiv "
                "klar erkennbar."
            )
        },
        {
            "prompt": (
                "Only explicitly requested image elements may change; all "
                "unselected elements should remain visually stable."
            ),
            "label": (
                "Nur ausdrücklich ausgewählte Merkmale wurden verändert; "
                "nicht ausgewählte Merkmale bleiben stabil."
            )
        },
        {
            "prompt": (
                "The original fictional main person must remain recognizable, "
                "natural, and visually consistent with the source image."
            ),
            "label": (
                "Die fiktive Hauptperson bleibt wiedererkennbar, natürlich und "
                "zum Ausgangsbild konsistent."
            )
        },
        {
            "prompt": (
                "Every visible person must remain or become a clearly "
                "recognizable fictional adult aged 18 or older; no children, "
                "minors, or age-ambiguous people may be introduced."
            ),
            "label": (
                "Alle sichtbaren Personen sind eindeutig als volljährige "
                "fiktive Erwachsene erkennbar; die Überarbeitung ergänzt "
                "keine Minderjährigen oder altersmäßig uneindeutigen Personen."
            )
        },
        {
            "prompt": (
                "Work activity, gaze, gestures, hands, objects, and any social "
                "interaction must be physically plausible and causally aligned."
            ),
            "label": (
                "Tätigkeit, Blick, Gesten, Hände, Objekte und Interaktion sind "
                "körperlich plausibel und inhaltlich aufeinander bezogen."
            )
        },
        {
            "prompt": (
                "No company logo, readable brand name, generated campaign text, "
                "real-employee claim, tokenism, or stereotypical depiction may "
                "appear."
            ),
            "label": (
                "Keine Logos, lesbaren Markennamen, generierten Kampagnentexte, "
                "vorgetäuschten Beschäftigtenaussagen, Tokenismen oder "
                "stereotypen Darstellungen."
            )
        },
        {
            "prompt": (
                "The result must support the intended campaign use and remain "
                "subject to human comparison and approval before publication."
            ),
            "label": (
                "Das Ergebnis unterstützt den Kampagnenzweck und wird vor einer "
                "Veröffentlichung menschlich verglichen und freigegeben."
            )
        }
    ]

    interaction = components.get("interaction", "")
    includes_additional_people = (
        interaction and
        "only visible subject" not in interaction
    )

    if includes_additional_people:
        criteria.insert(
            4,
            {
                "prompt": (
                    "Every additional fictional person must have a clear role "
                    "in the same workplace task, must be a clearly recognizable "
                    "adult aged 18 or older, and must not function as visual "
                    "decoration."
                ),
                "label": (
                    "Jede zusätzliche fiktive Person erfüllt eine erkennbare "
                    "Funktion in derselben Arbeitssituation, ist eindeutig "
                    "volljährig und dient nicht nur als Dekoration."
                )
            }
        )

    return criteria


def build_prompt_chain_sections(components):
    optimization_goal = components.get("optimizationGoal", "")

    if not optimization_goal:
        return []

    change_strength = components.get("changeStrength", "")
    preservation_focus = components.get("preservationFocus", "")
    extra_prompt = components.get("extraPrompt", "")
    banner = components.get("banner", {})

    role_text = (
        "Apply the visual judgement of a professional employer-branding "
        "campaign art director. This is stage 2 of a prompt chain: use the "
        "supplied result from stage 1 as the visual source and perform one "
        "focused refinement instead of creating an unrelated new scene."
    )

    task_parts = [ensure_sentence(optimization_goal)]

    if change_strength:
        task_parts.append(ensure_sentence(change_strength))

    task_text = " ".join(task_parts)

    source_parts = [
        (
            "The source image and every person shown in it are fully synthetic. "
            "Preserve the fictional main person's recognizable face structure, "
            "hairstyle, apparent age, skin tone, natural body proportions, and "
            "overall visual continuity."
        )
    ]

    if preservation_focus:
        source_parts.append(ensure_sentence(preservation_focus))

    source_parts.append(
        "Treat the source image as visual material, not as evidence of a real "
        "employee or a real workplace event."
    )
    source_text = " ".join(source_parts)

    requested_fields = [
        ("Workplace activity", components.get("workActivity", "")),
        ("People and interaction", components.get("interaction", "")),
        ("Pose and body action", components.get("pose", "")),
        ("Gaze", components.get("gaze", "")),
        ("Facial expression", components.get("expression", "")),
        ("Clothing and role styling", components.get("roleStyling", "")),
        ("Composition", components.get("composition", "")),
        ("Visual effect", components.get("visualEffect", "")),
        ("Quality correction", components.get("correctionFocus", ""))
    ]
    requested_parts = [
        f"{label}: {ensure_sentence(value)}"
        for label, value in requested_fields
        if value
    ]

    if extra_prompt:
        requested_parts.append(
            "Additional requested visual detail: " +
            ensure_sentence(extra_prompt)
        )

    if requested_parts:
        requested_text = "\n".join(
            f"- {part}"
            for part in requested_parts
        )
    else:
        requested_text = (
            "Apply the primary optimization task without introducing any "
            "additional scene, person, styling, or composition change."
        )

    constraints_text = (
        "Change only explicitly requested elements and preserve all other "
        "image elements as closely as possible. Do not replace the fictional "
        "main person. Do not imitate any identifiable real person. Do not add "
        "company logos, readable brand names, employee identification cards, "
        "generated campaign typography, or unverifiable employment claims. "
        "Every visible person must be a clearly recognizable fictional adult "
        "aged 18 or older. Do not add children, minors, school pupils, families "
        "with children, age-ambiguous people, or unnecessary bystanders. Any "
        "additional adults must contribute naturally to the same task; avoid "
        "tokenism, stereotypes, exaggerated enthusiasm, and staged stock-photo "
        "poses. Keep anatomy, hands, gaze, work objects, lighting, scale, and "
        "spatial relationships physically plausible."
    )

    output_parts = [
        (
            "Return one photorealistic revised employer-branding campaign image "
            "with the same dimensions and aspect ratio as the source image."
        )
    ]

    if banner.get("enabled"):
        banner_position = banner.get("position", "auto")

        if banner_position == "auto":
            banner_position = "bottom"

        output_parts.append(
            "Create calm, uncluttered negative space at the " +
            banner_position +
            " for a separately rendered campaign banner. Do not generate the "
            "banner text inside the image."
        )

    output_text = " ".join(output_parts)

    section_values = [
        ("role_method", role_text),
        ("primary_task", task_text),
        ("source_preservation", source_text),
        ("requested_changes", requested_text),
        ("constraints", constraints_text),
        ("output_criteria", output_text)
    ]

    return [
        {
            "id": section_id,
            "title": PROMPT_CHAIN_SECTION_TITLES[section_id],
            "text": text
        }
        for section_id, text in section_values
    ]


def build_prompt_package(components, mode=DEFAULT_MODE):
    normalized_mode = normalize_mode(mode)

    if normalized_mode == "text_to_image":
        selected_options = get_selected_ui_options(
            components,
            "text_to_image"
        )
        briefing_sections = build_text_to_image_briefing_sections(
            components,
            selected_options
        )
        translation_steps = build_text_to_image_translation_steps(
            components,
            selected_options
        )
        sections = build_text_to_image_render_sections(
            components,
            selected_options
        )
        positive_prompt = "\n\n".join(
            section["title"] + "\n" + section["text"]
            for section in sections
        )

        return {
            "positive_prompt": positive_prompt.strip(),
            "render_prompt": positive_prompt.strip(),
            "negative_prompt": TEXT_TO_IMAGE_NEGATIVE_PROMPT,
            "sections": sections,
            "render_sections": sections,
            "briefing_sections": briefing_sections,
            "translation_steps": translation_steps,
            "success_criteria": build_success_criteria(components)
        }

    sections = build_prompt_chain_sections(components)
    positive_prompt = "\n\n".join(
        section["title"] + "\n" + section["text"]
        for section in sections
    )

    return {
        "positive_prompt": positive_prompt.strip(),
        "render_prompt": positive_prompt.strip(),
        "negative_prompt": "",
        "sections": sections,
        "render_sections": sections,
        "briefing_sections": [],
        "translation_steps": [],
        "success_criteria": (
            build_prompt_chain_success_criteria(components)
            if sections else []
        )
    }


def build_prompt_from_components(components, mode=DEFAULT_MODE):
    """Backward-compatible accessor for the actual model render prompt."""
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


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------

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


def patch_prompt_chain_workflow(workflow, image_name, prompt, components):
    workflow["78"]["inputs"]["image"] = image_name
    workflow["435"]["inputs"]["value"] = prompt
    workflow["433:111"]["inputs"]["prompt"] = ["435", 0]
    generation_settings = components.get("generation", {})

    if generation_settings.get("fixed_seed"):
        workflow["433:3"]["inputs"]["seed"] = int(
            generation_settings["seed"]
        )
    else:
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
            detail="Prompt Chain mode requires a saved source image."
        )

    return patch_prompt_chain_workflow(
        workflow,
        image_name,
        prompt,
        components
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
            "requires_library_source": bool(
                mode_config.get("requires_library_source", False)
            )
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


@app.get("/api/library")
def campaign_library():
    return JSONResponse(
        content={"items": list_library_items()},
        headers=no_cache_headers()
    )


@app.get("/api/library/{item_id}/image")
def campaign_library_image(item_id: str):
    row = get_library_row(item_id)
    path = LIBRARY_IMAGES_DIR / row["filename"]

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Saved image file not found."
        )

    return FileResponse(
        str(path),
        media_type="image/png",
        headers=no_cache_headers()
    )


@app.get("/api/library/{item_id}/thumbnail")
def campaign_library_thumbnail(item_id: str):
    row = get_library_row(item_id)
    path = LIBRARY_THUMBNAILS_DIR / row["thumbnail_filename"]

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Saved thumbnail file not found."
        )

    return FileResponse(
        str(path),
        media_type="image/png",
        headers=no_cache_headers()
    )


@app.post("/api/library/save")
async def save_to_campaign_library(payload: dict = Body(...)):
    item = persist_temporary_result(
        payload.get("result_id"),
        payload.get("title", "")
    )

    return JSONResponse(
        content={"item": item},
        headers=no_cache_headers()
    )


@app.get("/api/results/{result_id}/image")
def temporary_result_image(result_id: str):
    entry = get_temporary_result(result_id)

    return FileResponse(
        str(entry["display_path"]),
        media_type="image/png",
        headers=no_cache_headers()
    )


@app.delete("/api/library/{item_id}")
def remove_from_campaign_library(item_id: str):
    delete_library_item(item_id)

    return JSONResponse(
        content={"deleted": True},
        headers=no_cache_headers()
    )


@app.delete("/api/results/{result_id}")
def discard_temporary_result(result_id: str):
    delete_temporary_result(result_id)

    return JSONResponse(
        content={"deleted": True},
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
            "render_prompt": prompt_package["render_prompt"],
            "negative_prompt": prompt_package["negative_prompt"],
            "sections": prompt_package["sections"],
            "render_sections": prompt_package["render_sections"],
            "briefing_sections": prompt_package["briefing_sections"],
            "translation_steps": prompt_package["translation_steps"],
            "success_criteria": prompt_package["success_criteria"],
            "components": components
        },
        headers=no_cache_headers()
    )


@app.post("/api/run")
async def run(
    prompt_components: str = Form(...),
    mode: str = Form(DEFAULT_MODE),
    library_source_id: str = Form("")
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

    library_source_row = None

    if library_source_id:
        if not mode_config.get("requires_library_source"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "A saved library image can only be used in Prompt Chain "
                    "mode."
                )
            )

        library_source_row = get_library_row(library_source_id)

        if library_source_row["mode"] not in LIBRARY_SAVEABLE_MODES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Prompt Chain mode only accepts fully synthetic library "
                    "images."
                )
            )

    elif mode_config.get("requires_library_source"):
        raise HTTPException(
            status_code=400,
            detail="Prompt Chain mode requires a saved library image."
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

    if mode_config.get("requires_library_source"):
        source_path = (
            LIBRARY_IMAGES_DIR /
            library_source_row["source_filename"]
        )

        if not source_path.exists():
            raise HTTPException(
                status_code=404,
                detail="The saved source image is no longer available."
            )

        image_bytes = source_path.read_bytes()
        safe_filename = source_path.name
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

    if normalized_mode == "text_to_image":
        sampler_node = get_mode_config(normalized_mode)["nodes"]["sampler"]
        actual_seed = workflow[sampler_node]["inputs"]["seed"]
    else:
        actual_seed = workflow["433:3"]["inputs"]["seed"]

    components.setdefault("generation", {})["actual_seed"] = actual_seed

    progress_state["value"] = 0

    prompt_id = queue_prompt(workflow)

    threading.Thread(
        target=track_progress,
        args=(prompt_id,),
        daemon=True
    ).start()

    images = wait_for_result(prompt_id)

    results = []
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

        source_image_bytes = response.content
        display_image_bytes = source_image_bytes

        if banner.get("enabled"):
            display_image_bytes = render_banner_on_image(
                display_image_bytes,
                banner
            )

        display_image_bytes = render_ai_overlay_on_image(
            display_image_bytes
        )

        result = register_temporary_result(
            display_bytes=display_image_bytes,
            source_bytes=source_image_bytes,
            metadata={
                "mode": normalized_mode,
                "parent_id": (
                    library_source_row["id"]
                    if library_source_row is not None
                    else None
                ),
                "positive_prompt": final_prompt,
                "negative_prompt": negative_prompt,
                "components": components
            }
        )
        results.append(result)
        view_urls.append(result["view_url"])

    return JSONResponse(
        content={
            "mode": normalized_mode,
            "prompt_id": prompt_id,
            "submitted_prompt": final_prompt,
            "submitted_render_prompt": final_prompt,
            "submitted_negative_prompt": negative_prompt,
            "prompt_sections": prompt_package["sections"],
            "render_sections": prompt_package["render_sections"],
            "briefing_sections": prompt_package["briefing_sections"],
            "translation_steps": prompt_package["translation_steps"],
            "success_criteria": prompt_package["success_criteria"],
            "prompt_components": components,
            "results": results,
            "view_urls": view_urls
        },
        headers=no_cache_headers()
    )

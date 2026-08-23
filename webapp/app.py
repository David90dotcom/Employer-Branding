from fastapi import (
    FastAPI,
    HTTPException,
    Form,
    Body,
    Query,
    File,
    UploadFile
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import asyncio
import base64
import binascii
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

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


BASE_DIR = Path(__file__).resolve().parent
MODEL_CONFIG_PATH = BASE_DIR / "model_config.json"
INDEX_PATH = BASE_DIR / "static" / "index.html"

COMFY = os.getenv(
    "EMPLOYER_BRANDING_COMFY_URL",
    "http://127.0.0.1:8188"
).strip().rstrip("/")

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images"
OPENAI_IMAGE_MODEL = (
    os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or
    "gpt-image-2"
)
OPENAI_IMAGE_QUALITY = (
    os.getenv("OPENAI_IMAGE_QUALITY", "medium").strip().lower() or
    "medium"
)
OPENAI_IMAGE_TIMEOUT_SECONDS = 180

if OPENAI_IMAGE_QUALITY not in {"low", "medium", "high", "auto"}:
    raise RuntimeError(
        "OPENAI_IMAGE_QUALITY must be low, medium, high, or auto."
    )

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
AI_OVERLAY_POSITION = "top_right"

MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_LOGO_PIXELS = 16_000_000
MIN_LOGO_SIZE_PERCENT = 5
MAX_LOGO_SIZE_PERCENT = 30

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

IMAGE_PROVIDERS = {
    "local": {
        "label": "Lokal · Qwen / ComfyUI",
        "description": (
            "Verarbeitet Render-Prompt und Bilddaten ausschließlich über "
            "die lokal erreichbare ComfyUI-Instanz."
        ),
        "supports_seed": True,
        "supports_negative_prompt": True,
        "is_cloud": False
    },
    "openai": {
        "label": "Cloud · OpenAI Images API",
        "description": (
            "Sendet den Render-Prompt und bei Stufe 2 das synthetische "
            "Ausgangsbild an die OpenAI Images API."
        ),
        "supports_seed": False,
        "supports_negative_prompt": False,
        "is_cloud": True
    }
}

DEFAULT_IMAGE_PROVIDER = os.getenv(
    "EMPLOYER_BRANDING_IMAGE_PROVIDER",
    "local"
).strip().lower()

if DEFAULT_IMAGE_PROVIDER not in IMAGE_PROVIDERS:
    raise RuntimeError(
        "EMPLOYER_BRANDING_IMAGE_PROVIDER must be local or openai."
    )

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


def get_openai_api_key():
    """Read the key only when needed so it never enters public config."""
    return os.getenv("OPENAI_API_KEY", "").strip()


def local_provider_available():
    try:
        response = requests.get(
            f"{COMFY}/system_stats",
            timeout=1.5
        )
        return response.ok
    except requests.RequestException:
        return False


def provider_is_configured(provider):
    normalized = str(provider or "").strip().lower()

    if normalized == "openai":
        return bool(get_openai_api_key())

    if normalized == "local":
        return local_provider_available()

    return False


def normalize_image_provider(provider, require_configured=False):
    raw_provider = (
        provider
        if isinstance(provider, str)
        else DEFAULT_IMAGE_PROVIDER
    )
    normalized = str(raw_provider or DEFAULT_IMAGE_PROVIDER).strip().lower()

    if normalized not in IMAGE_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown image provider: {normalized}"
        )

    if require_configured and not provider_is_configured(normalized):
        if normalized == "openai":
            detail = (
                "Der Cloud-Anbieter ist nicht konfiguriert. Hinterlege "
                "OPENAI_API_KEY serverseitig in der lokalen .env-Datei "
                "oder als Umgebungsvariable und starte die App neu."
            )
        else:
            detail = (
                "ComfyUI ist unter der konfigurierten lokalen Adresse "
                "nicht erreichbar."
            )

        raise HTTPException(status_code=503, detail=detail)

    return normalized


def resolve_provider_model_name(provider, mode=DEFAULT_MODE):
    normalized_provider = normalize_image_provider(provider)

    if normalized_provider == "openai":
        return OPENAI_IMAGE_MODEL

    return resolve_mode_model_name(mode)


def apply_provider_generation_settings(components, provider, mode):
    normalized_provider = normalize_image_provider(provider)
    generation = components.setdefault("generation", {})
    provider_config = IMAGE_PROVIDERS[normalized_provider]

    generation["provider"] = normalized_provider
    generation["model_name"] = resolve_provider_model_name(
        normalized_provider,
        mode
    )
    generation["seed_supported"] = bool(
        provider_config["supports_seed"]
    )
    generation["negative_prompt_supported"] = bool(
        provider_config["supports_negative_prompt"]
    )

    if not provider_config["supports_seed"]:
        generation["fixed_seed"] = False
        generation["actual_seed"] = None

    return components


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
        "parent_id": entry.get("parent_id"),
        "provider": entry.get(
            "provider",
            entry.get("components", {}).get("generation", {}).get(
                "provider",
                "local"
            )
        ),
        "model_name": entry.get(
            "model_name",
            entry.get("components", {}).get("generation", {}).get(
                "model_name"
            )
        )
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
        "provider": components.get("generation", {}).get(
            "provider",
            "local"
        ),
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
    model_name = (
        entry.get("model_name") or
        generation.get("model_name") or
        resolve_mode_model_name(mode)
    )

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
        "left",
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


def normalize_logo_settings(raw_components):
    """Validate positioning data without receiving or storing the PNG."""
    raw_logo = raw_components.get("logo", {})

    if not isinstance(raw_logo, dict):
        raw_logo = {}

    enabled_value = raw_logo.get("enabled", False)
    enabled = enabled_value is True or str(
        enabled_value
    ).strip().lower() in {"1", "true", "yes", "on"}

    def bounded_integer(name, default, minimum, maximum):
        try:
            value = int(float(raw_logo.get(name, default)))
        except (TypeError, ValueError):
            value = default

        return max(minimum, min(maximum, value))

    return {
        "enabled": enabled,
        "x_percent": bounded_integer("x_percent", 0, 0, 100),
        "y_percent": bounded_integer("y_percent", 0, 0, 100),
        "size_percent": bounded_integer(
            "size_percent",
            14,
            MIN_LOGO_SIZE_PERCENT,
            MAX_LOGO_SIZE_PERCENT
        )
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
    normalized["logo"] = normalize_logo_settings(raw_components)
    normalized["generation"] = normalize_generation_settings(raw_components)

    if invalid_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid prompt component values",
                "invalid_fields": invalid_fields
            }
        )

    validate_prompt_component_compatibility(normalized, normalized_mode)

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


def selected_campaign_space_position(components):
    selected_options = get_selected_ui_options(
        components,
        "prompt_chain"
    )
    option = selected_options.get("campaignSpace", {})
    return str(
        option.get("campaign_space_position", "") or ""
    ).strip()


def validate_prompt_component_compatibility(components, mode):
    """Reject combinations that would create contradictory person prompts."""
    if mode == "prompt_chain":
        campaign_space_position = selected_campaign_space_position(
            components
        )

        if not campaign_space_position:
            components["spaceTreatment"] = ""
            return

        banner = components.get("banner", {})

        if banner.get("enabled"):
            # A selected usable area is authoritative. This prevents the
            # generated composition and the deterministic banner placement
            # from requesting opposite sides of the same image.
            banner["position"] = campaign_space_position

        return

    if mode != "text_to_image":
        return

    selected_options = get_selected_ui_options(components, mode)
    configuration = selected_options.get("peopleConfiguration", {})
    action = selected_options.get("action", {})
    configuration_id = str(
        configuration.get("configuration_id", "") or ""
    ).strip()
    allowed_configurations = action.get("allowed_configurations")

    if (
        configuration_id and
        isinstance(allowed_configurations, list) and
        configuration_id not in allowed_configurations
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Die gewählte Arbeitsweise passt nicht zur "
                "Personenkonstellation. Bitte eine der in der Oberfläche "
                "freigegebenen Tätigkeiten auswählen."
            )
        )

    if configuration_id == "solo":
        components["supportingOutfit"] = ""


def build_text_to_image_negative_prompt(
    components,
    selected_options=None
):
    """Add selection-specific exclusions without contradicting other ages."""
    selected_options = selected_options or get_selected_ui_options(
        components,
        "text_to_image"
    )
    additions = []

    for field_id in (
        "peopleConfiguration",
        "mainSubjectAge",
        "backgroundPolicy",
        "mainOutfit",
        "supportingOutfit"
    ):
        negative_value = str(
            selected_options.get(field_id, {}).get("negative_prompt", "") or ""
        ).strip()

        if negative_value:
            additions.append(negative_value)

    if not additions:
        return TEXT_TO_IMAGE_NEGATIVE_PROMPT

    return TEXT_TO_IMAGE_NEGATIVE_PROMPT + ", " + ", ".join(additions)


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


def build_success_criteria(components, selected_options=None):
    selected_options = selected_options or get_selected_ui_options(
        components,
        "text_to_image"
    )
    logo_enabled = components.get("logo", {}).get("enabled", False)
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
                "No identifiable real person, model-generated logo, readable "
                "brand name, tokenism, or stereotypical depiction may appear. "
                "An optional context-supplied logo may only be added as an "
                "exact deterministic "
                "PNG overlay after generation."
                if logo_enabled else
                "No identifiable real person, logo, readable brand name, "
                "tokenism, or stereotypical depiction may appear."
            ),
            "label": (
                "Keine identifizierbare reale Person und kein vom Modell "
                "erzeugtes Logo; ein optionales Logo wird nur "
                "anschließend als exaktes PNG-Overlay ergänzt."
                if logo_enabled else
                "Keine identifizierbare reale Person, kein Logo, kein lesbarer "
                "Markenname, kein Tokenismus und keine stereotype Darstellung."
            )
        }
    ]

    configuration_label = selected_option_label(
        selected_options,
        "peopleConfiguration"
    )
    age_label = selected_option_label(
        selected_options,
        "mainSubjectAge"
    )
    background_label = selected_option_label(
        selected_options,
        "backgroundPolicy"
    )

    if configuration_label or age_label or background_label:
        selected_labels = " · ".join(
            label
            for label in (
                configuration_label,
                age_label,
                background_label
            )
            if label
        )
        criteria.insert(1, {
            "prompt": (
                "The selected foreground configuration, main-subject age "
                "range, and background-person policy must be followed exactly."
            ),
            "label": (
                "Personenzahl, Alterseindruck und Hintergrund entsprechen "
                "der Auswahl: " + selected_labels + "."
            )
        })

    configuration_id = str(
        selected_options.get("peopleConfiguration", {}).get(
            "configuration_id",
            ""
        ) or ""
    )

    if configuration_id and configuration_id != "solo":
        criteria.insert(2, {
            "prompt": (
                "Main and supporting people must remain visually distinct in "
                "role, position, age impression, and clothing."
            ),
            "label": (
                "Haupt- und Begleitpersonen sind durch Rolle, Position, "
                "Alterseindruck und Kleidung eindeutig unterscheidbar."
            )
        })

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

    configuration_option = selected_options.get(
        "peopleConfiguration",
        {}
    )
    configuration_id = str(
        configuration_option.get("configuration_id", "") or ""
    ).strip()

    for field_id in (
        "peopleConfiguration",
        "mainSubjectAge",
        "workContext",
        "backgroundPolicy"
    ):
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

    main_outfit = components.get("mainOutfit", "")

    if main_outfit:
        action_parts.append(
            ensure_sentence(main_outfit)
        )

    supporting_outfit = components.get("supportingOutfit", "")

    if supporting_outfit and configuration_id != "solo":
        action_parts.append(ensure_sentence(supporting_outfit))
        action_parts.append(
            "Keep the clothing colors and garment types of the main and "
            "supporting people visibly distinct."
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
        "people whose age appears ambiguous. Follow the selected foreground "
        "configuration and background-person policy exactly. Do not merge, "
        "duplicate, or interchange the roles, ages, positions, or clothing of "
        "the main and supporting people."
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
    optimization_goal = components.get("optimizationGoal", "")
    logo_enabled = components.get("logo", {}).get("enabled", False)
    logo_criterion = {
        "prompt": (
            "No model-generated company logo, readable brand name, generated "
            "campaign text, real-employee claim, tokenism, or stereotypical "
            "depiction may appear. An optional context-supplied logo may only "
            "be added as an exact deterministic PNG overlay after generation."
            if logo_enabled else
            "No company logo, readable brand name, generated campaign text, "
            "real-employee claim, tokenism, or stereotypical depiction may "
            "appear."
        ),
        "label": (
            "Das Modell erzeugt keine Logos oder Markennamen; ein optionales "
            "Logo wird ausschließlich anschließend als exaktes "
            "PNG-Overlay ergänzt."
            if logo_enabled else
            "Keine Logos, lesbaren Markennamen, generierten Kampagnentexte, "
            "vorgetäuschten Beschäftigtenaussagen, Tokenismen oder "
            "stereotypen Darstellungen."
        )
    }
    criteria = [
        {
            "prompt": (
                "The primary optimization goal must be clearly visible in "
                "the revised campaign image."
                if optimization_goal else
                "The explicitly selected visual refinement must be clearly "
                "visible in the revised campaign image."
            ),
            "label": (
                "Das primäre Optimierungsziel ist im überarbeiteten Motiv "
                "klar erkennbar."
                if optimization_goal else
                "Die ausdrücklich ausgewählte Bildänderung ist im "
                "überarbeiteten Motiv klar erkennbar."
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
        logo_criterion,
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

    campaign_space_position = selected_campaign_space_position(components)

    if campaign_space_position:
        position_label = {
            "left": "links",
            "right": "rechts",
            "top": "oben",
            "bottom": "unten"
        }.get(campaign_space_position, campaign_space_position)
        criteria.insert(
            2,
            {
                "prompt": (
                    "The selected campaign-copy area must have the requested "
                    "size and position, remain calm and low-detail, and stay "
                    "free of faces, hands, essential work objects, and "
                    "generated typography."
                ),
                "label": (
                    "Die reservierte Kampagnenfläche liegt "
                    f"{position_label}, besitzt die gewählte Größe "
                    "und bleibt ruhig, detailarm sowie frei von Gesichtern, "
                    "Händen, zentralen Arbeitsobjekten und generierter Schrift."
                )
            }
        )

    return criteria


def build_prompt_chain_sections(components):
    optimization_goal = components.get("optimizationGoal", "")
    change_strength = components.get("changeStrength", "")
    preservation_focus = components.get("preservationFocus", "")
    extra_prompt = components.get("extraPrompt", "")
    banner = components.get("banner", {})

    requested_fields = [
        ("Workplace activity", components.get("workActivity", "")),
        ("People and interaction", components.get("interaction", "")),
        ("Pose and body action", components.get("pose", "")),
        ("Gaze", components.get("gaze", "")),
        ("Facial expression", components.get("expression", "")),
        ("Clothing and role styling", components.get("roleStyling", "")),
        ("Framing", components.get("framing", "")),
        ("Usable campaign-copy area", components.get("campaignSpace", "")),
        ("Reserved-area treatment", components.get("spaceTreatment", "")),
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

    banner_requests_layout_change = bool(banner.get("enabled"))

    if not (
        optimization_goal or
        requested_parts or
        banner_requests_layout_change
    ):
        return []

    role_text = (
        "Apply the visual judgement of a professional employer-branding "
        "campaign art director. This is stage 2 of a prompt chain: use the "
        "supplied result from stage 1 as the visual source and perform one "
        "focused refinement instead of creating an unrelated new scene."
    )

    task_parts = [
        ensure_sentence(optimization_goal)
        if optimization_goal else
        (
            "Use the explicitly selected visual changes below as the complete "
            "optimization task. Do not introduce an additional campaign "
            "reinterpretation or change any unselected image element."
        )
    ]

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

    if requested_parts:
        requested_text = "\n".join(
            f"- {part}"
            for part in requested_parts
        )
    elif optimization_goal:
        requested_text = (
            "Apply the primary optimization task without introducing any "
            "additional scene, person, styling, or composition change."
        )
    else:
        requested_text = (
            "Prepare only the campaign-layout space requested in OUTPUT and "
            "do not alter any unrelated image element."
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

    campaign_space_position = selected_campaign_space_position(components)

    if banner.get("enabled") and not campaign_space_position:
        banner_position = banner.get("position", "auto")

        if banner_position == "auto":
            banner_position = "bottom"

        output_parts.append(
            "Create calm, uncluttered negative space at the " +
            banner_position +
            " for a separately rendered campaign banner. Do not generate the "
            "banner text inside the image."
        )
    elif banner.get("enabled"):
        output_parts.append(
            "Keep the selected reserved campaign area free of generated "
            "typography so that the campaign banner can be rendered there "
            "deterministically after image generation."
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
            "negative_prompt": build_text_to_image_negative_prompt(
                components,
                selected_options
            ),
            "sections": sections,
            "render_sections": sections,
            "briefing_sections": briefing_sections,
            "translation_steps": translation_steps,
            "success_criteria": build_success_criteria(
                components,
                selected_options
            )
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

    if position in {"left", "right"}:
        box_width = int(width * 0.38)
        box_height = height - 2 * margin
        box_x = (
            margin
            if position == "left"
            else width - box_width - margin
        )
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
# Deterministische PNG-Overlays
# ---------------------------------------------------------------------------

def sanitize_logo_png_bytes(file_bytes):
    """Accept one bounded PNG and remove metadata by re-encoding it."""
    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail="Die ausgewählte Logo-PNG ist leer."
        )

    if len(file_bytes) > MAX_LOGO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Die Logo-PNG darf höchstens 5 MB groß sein."
        )

    try:
        with Image.open(BytesIO(file_bytes)) as image:
            if image.format != "PNG":
                raise HTTPException(
                    status_code=400,
                    detail="Als Firmenlogo ist ausschließlich PNG erlaubt."
                )

            if getattr(image, "n_frames", 1) != 1:
                raise HTTPException(
                    status_code=400,
                    detail="Animierte PNG-Dateien sind nicht erlaubt."
                )

            width, height = image.size

            if width < 1 or height < 1 or width * height > MAX_LOGO_PIXELS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Die Logo-PNG besitzt unzulässige Abmessungen "
                        "oder mehr als 16 Megapixel."
                    )
                )

            sanitized = image.convert("RGBA")
            sanitized.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Die hochgeladene Datei ist keine lesbare PNG-Datei."
        ) from exc

    output = BytesIO()
    sanitized.save(output, format="PNG", optimize=True)
    return output.getvalue()


async def read_logo_upload(logo_file, logo_settings):
    """Read an optional upload only for the current generation request."""
    if not logo_settings.get("enabled"):
        return None

    if logo_file is None or not hasattr(logo_file, "read"):
        raise HTTPException(
            status_code=400,
            detail="Logo-Overlay ist aktiviert, aber keine PNG wurde gewählt."
        )

    try:
        file_bytes = await logo_file.read(MAX_LOGO_UPLOAD_BYTES + 1)
    finally:
        close = getattr(logo_file, "close", None)

        if close is not None:
            result = close()

            if asyncio.iscoroutine(result):
                await result

    return sanitize_logo_png_bytes(file_bytes)


def rectangles_overlap(first, second, padding=0):
    return not (
        first[2] + padding <= second[0] or
        second[2] + padding <= first[0] or
        first[3] + padding <= second[1] or
        second[3] + padding <= first[1]
    )


def calculate_ai_overlay_layout(base_size, overlay_size):
    base_w, base_h = base_size
    overlay_w, overlay_h = overlay_size
    scale = min(
        base_w / overlay_w,
        base_h / overlay_h
    ) * 0.24
    new_size = (
        max(1, int(overlay_w * scale)),
        max(1, int(overlay_h * scale))
    )
    margin_x = int(base_w * 0.035)
    margin_y = int(base_h * 0.035)

    if AI_OVERLAY_POSITION == "top_left":
        position = (margin_x, margin_y)
    else:
        position = (
            base_w - new_size[0] - margin_x,
            margin_y
        )

    return new_size, position


def ai_overlay_reserved_box(base_size):
    if not AI_OVERLAY_PATH.exists():
        return None

    with Image.open(AI_OVERLAY_PATH) as overlay:
        new_size, position = calculate_ai_overlay_layout(
            base_size,
            overlay.size
        )

    return (
        position[0],
        position[1],
        position[0] + new_size[0],
        position[1] + new_size[1]
    )


def calculate_logo_layout(base_size, logo_size, settings, reserved_box=None):
    base_w, base_h = base_size
    logo_w, logo_h = logo_size
    requested_width = base_w * settings["size_percent"] / 100
    maximum_height = base_h * 0.32
    scale = min(
        requested_width / logo_w,
        maximum_height / logo_h
    )
    new_size = (
        max(1, int(logo_w * scale)),
        max(1, int(logo_h * scale))
    )
    margin = max(1, int(min(base_w, base_h) * 0.03))
    available_x = max(0, base_w - 2 * margin - new_size[0])
    available_y = max(0, base_h - 2 * margin - new_size[1])
    requested = (
        margin + round(available_x * settings["x_percent"] / 100),
        margin + round(available_y * settings["y_percent"] / 100)
    )

    def box_at(position):
        return (
            position[0],
            position[1],
            position[0] + new_size[0],
            position[1] + new_size[1]
        )

    if not reserved_box or not rectangles_overlap(
        box_at(requested),
        reserved_box,
        padding=margin
    ):
        return new_size, requested

    candidates = [
        (requested[0], reserved_box[3] + margin),
        (reserved_box[2] + margin, requested[1]),
        (margin + available_x, margin),
        (margin, margin + available_y),
        (margin + available_x, margin + available_y)
    ]
    valid_candidates = []

    for candidate in candidates:
        x = max(margin, min(margin + available_x, candidate[0]))
        y = max(margin, min(margin + available_y, candidate[1]))
        normalized_candidate = (x, y)

        if not rectangles_overlap(
            box_at(normalized_candidate),
            reserved_box,
            padding=margin
        ):
            valid_candidates.append(normalized_candidate)

    if not valid_candidates:
        return new_size, requested

    closest = min(
        valid_candidates,
        key=lambda candidate: (
            (candidate[0] - requested[0]) ** 2 +
            (candidate[1] - requested[1]) ** 2
        )
    )
    return new_size, closest


def render_logo_overlay_on_image(image_bytes, logo_bytes, settings):
    if not logo_bytes or not settings.get("enabled"):
        return image_bytes

    base = Image.open(BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
    new_size, position = calculate_logo_layout(
        base.size,
        logo.size,
        settings,
        reserved_box=ai_overlay_reserved_box(base.size)
    )
    logo = logo.resize(new_size, Image.LANCZOS)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(logo, position, logo)
    result = Image.alpha_composite(base, layer)
    output = BytesIO()
    result.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_ai_overlay_on_image(image_bytes):
    """Place the mandatory transparent AI label in the upper-right corner."""
    if not AI_OVERLAY_PATH.exists():
        print(
            f"WARNUNG: AI-Overlay nicht gefunden: {AI_OVERLAY_PATH}"
        )
        return image_bytes

    base = Image.open(BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.open(AI_OVERLAY_PATH).convert("RGBA")
    new_size, position = calculate_ai_overlay_layout(
        base.size,
        overlay.size
    )
    overlay = overlay.resize(new_size, Image.LANCZOS)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(overlay, position, overlay)
    result = Image.alpha_composite(base, layer)
    output = BytesIO()
    result.convert("RGB").save(
        output,
        format="PNG",
        optimize=True
    )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Cloud Images API
# ---------------------------------------------------------------------------

def openai_size_is_valid(width, height):
    return (
        width > 0 and
        height > 0 and
        width <= 3840 and
        height <= 3840 and
        width % 16 == 0 and
        height % 16 == 0 and
        max(width, height) / min(width, height) <= 3 and
        655_360 <= width * height <= 8_294_400
    )


def resolve_openai_image_size(mode, components, source_bytes=None):
    if mode == "text_to_image":
        generation = get_mode_config(mode).get("generation", {})
        aspect_ratio = components.get(
            "aspectRatio",
            generation.get("default_aspect_ratio", "1:1")
        )
        dimensions = generation.get("aspect_ratios", {}).get(aspect_ratio)

        if dimensions and openai_size_is_valid(
            int(dimensions[0]),
            int(dimensions[1])
        ):
            return f"{int(dimensions[0])}x{int(dimensions[1])}"

        return "auto"

    if source_bytes:
        try:
            with Image.open(BytesIO(source_bytes)) as image:
                width, height = image.size

            if openai_size_is_valid(width, height):
                return f"{width}x{height}"
        except (OSError, ValueError):
            pass

    return "auto"


def normalize_openai_image_bytes(encoded_image):
    if not encoded_image or not isinstance(encoded_image, str):
        raise HTTPException(
            status_code=502,
            detail="Der Cloud-Anbieter hat keine Bilddaten zurückgegeben."
        )

    try:
        decoded = base64.b64decode(encoded_image, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=502,
            detail="Die Cloud-Bildantwort war nicht gültig kodiert."
        ) from exc

    try:
        with Image.open(BytesIO(decoded)) as image:
            normalized = image.convert("RGB")
            output = BytesIO()
            normalized.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Die Cloud-Antwort enthielt keine gültige Bilddatei."
        ) from exc


def raise_openai_api_error(response):
    request_id = str(response.headers.get("x-request-id", "") or "").strip()
    error_code = ""

    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        error_code = str(error.get("code", "") or "").strip()
    except (ValueError, TypeError):
        error_code = ""

    suffix = f" Referenz: {request_id}." if request_id else ""

    if error_code == "moderation_blocked":
        raise HTTPException(
            status_code=400,
            detail=(
                "Die Cloud-Bildanfrage wurde durch eine Sicherheitsprüfung "
                "abgelehnt. Bitte die visuellen Angaben neutraler und "
                f"eindeutiger formulieren.{suffix}"
            )
        )

    if response.status_code in {401, 403}:
        detail = (
            "Der Cloud-Zugriff wurde abgelehnt. Bitte API-Key, Projektzugriff "
            f"und gegebenenfalls die Organisationsverifizierung prüfen.{suffix}"
        )
    elif response.status_code == 429:
        detail = (
            "Das Cloud-Kontingent oder ein API-Limit wurde erreicht. Bitte "
            f"Nutzung und Abrechnung im API-Projekt prüfen.{suffix}"
        )
    elif 500 <= response.status_code < 600:
        detail = (
            "Der Cloud-Bilddienst ist vorübergehend nicht verfügbar. Bitte "
            f"später erneut versuchen.{suffix}"
        )
    else:
        code_suffix = f" ({error_code})" if error_code else ""
        detail = (
            "Die Cloud-Bildanfrage konnte nicht verarbeitet werden"
            f"{code_suffix}.{suffix}"
        )

    raise HTTPException(status_code=502, detail=detail)


def request_openai_image(prompt, mode, components, source_bytes=None):
    api_key = get_openai_api_key()

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY ist serverseitig nicht konfiguriert. "
                "Der Schlüssel darf nicht in die Browseroberfläche eingegeben "
                "oder in das Repository übernommen werden."
            )
        )

    image_size = resolve_openai_image_size(
        mode,
        components,
        source_bytes=source_bytes
    )
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    try:
        if mode == "text_to_image":
            response = requests.post(
                f"{OPENAI_IMAGES_URL}/generations",
                headers={
                    **headers,
                    "Content-Type": "application/json"
                },
                json={
                    "model": OPENAI_IMAGE_MODEL,
                    "prompt": prompt,
                    "n": 1,
                    "size": image_size,
                    "quality": OPENAI_IMAGE_QUALITY
                },
                timeout=OPENAI_IMAGE_TIMEOUT_SECONDS
            )
        else:
            if not source_bytes:
                raise HTTPException(
                    status_code=400,
                    detail="Cloud image editing requires a source image."
                )

            if len(source_bytes) >= 50 * 1024 * 1024:
                raise HTTPException(
                    status_code=400,
                    detail="Das Ausgangsbild überschreitet das 50-MB-Limit."
                )

            response = requests.post(
                f"{OPENAI_IMAGES_URL}/edits",
                headers=headers,
                data={
                    "model": OPENAI_IMAGE_MODEL,
                    "prompt": prompt,
                    "size": image_size,
                    "quality": OPENAI_IMAGE_QUALITY
                },
                files={
                    "image[]": (
                        "synthetic-campaign-source.png",
                        source_bytes,
                        "image/png"
                    )
                },
                timeout=OPENAI_IMAGE_TIMEOUT_SECONDS
            )
    except requests.Timeout as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "Die Cloud-Bildgenerierung hat das Zeitlimit überschritten. "
                "Es wurde kein API-Key protokolliert."
            )
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Der Cloud-Bilddienst ist momentan nicht erreichbar. "
                "Bitte Netzwerkverbindung und API-Konfiguration prüfen."
            )
        ) from exc

    if not response.ok:
        raise_openai_api_error(response)

    try:
        payload = response.json()
        encoded_image = payload["data"][0]["b64_json"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Die Cloud-Bildantwort hatte ein unerwartetes Format."
        ) from exc

    return normalize_openai_image_bytes(encoded_image)


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
    provider_availability = {
        provider_name: provider_is_configured(provider_name)
        for provider_name in IMAGE_PROVIDERS
    }

    for mode_name, mode_config in MODE_CONFIGS.items():
        modes.append({
            "id": mode_name,
            "label": mode_config.get("label", mode_name),
            "description": mode_config.get("description", ""),
            "requires_library_source": bool(
                mode_config.get("requires_library_source", False)
            )
        })

    providers = []

    for provider_name, provider_config in IMAGE_PROVIDERS.items():
        configured = provider_availability[provider_name]

        if configured:
            disabled_reason = ""
        elif provider_name == "openai":
            disabled_reason = (
                "OPENAI_API_KEY fehlt in der serverseitigen Konfiguration."
            )
        else:
            disabled_reason = "ComfyUI ist derzeit nicht erreichbar."

        providers.append({
            "id": provider_name,
            "label": provider_config["label"],
            "description": provider_config["description"],
            "available": configured,
            "configured": configured,
            "disabled_reason": disabled_reason,
            "supports_seed": bool(provider_config["supports_seed"]),
            "supports_negative_prompt": bool(
                provider_config["supports_negative_prompt"]
            ),
            "is_cloud": bool(provider_config["is_cloud"]),
            "model_name": resolve_provider_model_name(
                provider_name,
                DEFAULT_MODE
            )
        })

    effective_default_provider = DEFAULT_IMAGE_PROVIDER

    if not provider_availability[effective_default_provider]:
        available_provider = next(
            (
                provider["id"]
                for provider in providers
                if provider["available"]
            ),
            None
        )

        if available_provider:
            effective_default_provider = available_provider

    return JSONResponse(
        content={
            "default_mode": DEFAULT_MODE,
            "modes": modes,
            "default_provider": effective_default_provider,
            "providers": providers
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
    provider = normalize_image_provider(
        payload.get("provider", DEFAULT_IMAGE_PROVIDER)
    )
    raw_components = payload.get("components", payload)

    components = normalize_prompt_components(raw_components, mode)
    apply_provider_generation_settings(components, provider, mode)
    prompt_package = build_prompt_package(components, mode)

    return JSONResponse(
        content={
            "mode": mode,
            "provider": provider,
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
    library_source_id: str = Form(""),
    image_provider: str = Form(DEFAULT_IMAGE_PROVIDER),
    logo_file: UploadFile = File(None)
):
    normalized_mode = normalize_mode(mode)
    normalized_provider = normalize_image_provider(
        image_provider
    )
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
    apply_provider_generation_settings(
        components,
        normalized_provider,
        normalized_mode
    )
    logo_bytes = await read_logo_upload(
        logo_file,
        components.get("logo", {})
    )
    components.setdefault("logo", {})["applied"] = bool(logo_bytes)

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

    normalize_image_provider(
        normalized_provider,
        require_configured=True
    )

    if DEBUG_PROMPTS:
        print("\n" + "=" * 100)
        print("GENERATION MODE:", normalized_mode)
        print("IMAGE PROVIDER:", normalized_provider)
        print("PROMPT COMPONENTS:")
        print(json.dumps(components, indent=2, ensure_ascii=False))
        print("FINAL SERVER PROMPT:")
        print(final_prompt)
        print("=" * 100 + "\n")

    stored_image_name = None
    source_input_bytes = None

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

        source_input_bytes = source_path.read_bytes()

    progress_state["value"] = 0
    prompt_id = None
    source_images = []

    if normalized_provider == "local":
        if source_input_bytes is not None:
            stored_image_name = comfy_upload_image(
                source_input_bytes,
                source_path.name
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

        prompt_id = queue_prompt(workflow)

        threading.Thread(
            target=track_progress,
            args=(prompt_id,),
            daemon=True
        ).start()

        images = wait_for_result(prompt_id)

        for image in images:
            query = urlencode({
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image["type"]
            })

            response = requests.get(
                f"{COMFY}/view?{query}",
                timeout=60
            )
            response.raise_for_status()
            source_images.append(response.content)
    else:
        components.setdefault("generation", {})["actual_seed"] = None
        progress_state["value"] = 10
        source_images.append(
            await asyncio.to_thread(
                request_openai_image,
                prompt=final_prompt,
                mode=normalized_mode,
                components=components,
                source_bytes=source_input_bytes
            )
        )
        progress_state["value"] = 90

    results = []
    view_urls = []

    banner = components.get("banner", {})
    logo_settings = components.get("logo", {})
    submitted_negative_prompt = (
        negative_prompt
        if IMAGE_PROVIDERS[normalized_provider]["supports_negative_prompt"]
        else ""
    )
    model_name = resolve_provider_model_name(
        normalized_provider,
        normalized_mode
    )

    for source_image_bytes in source_images:
        display_image_bytes = source_image_bytes

        if banner.get("enabled"):
            display_image_bytes = render_banner_on_image(
                display_image_bytes,
                banner
            )

        if logo_bytes:
            display_image_bytes = render_logo_overlay_on_image(
                display_image_bytes,
                logo_bytes,
                logo_settings
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
                "negative_prompt": submitted_negative_prompt,
                "provider": normalized_provider,
                "model_name": model_name,
                "components": components
            }
        )
        results.append(result)
        view_urls.append(result["view_url"])

    progress_state["value"] = 100

    return JSONResponse(
        content={
            "mode": normalized_mode,
            "provider": normalized_provider,
            "model_name": model_name,
            "prompt_id": prompt_id,
            "submitted_prompt": final_prompt,
            "submitted_render_prompt": final_prompt,
            "submitted_negative_prompt": submitted_negative_prompt,
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

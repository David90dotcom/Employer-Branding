from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import json
import uuid
import requests
import time
import threading
import websocket

from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageDraw, ImageFont


COMFY = "http://127.0.0.1:8188"

BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_PATH = BASE_DIR / "workflow_template.json"
UI_FIELDS_PATH = BASE_DIR / "ui_fields.json"
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

WORKFLOW_TEMPLATE = json.loads(
    WORKFLOW_PATH.read_text(encoding="utf-8")
)

progress_state = {
    "value": 0
}


# ---------------------------------------------------------------------------
# Grundfunktionen
# ---------------------------------------------------------------------------

def deep_copy_workflow():
    return json.loads(json.dumps(WORKFLOW_TEMPLATE))


def load_ui_fields():
    return json.loads(UI_FIELDS_PATH.read_text(encoding="utf-8"))


def no_cache_headers():
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    }


# ---------------------------------------------------------------------------
# Prompt-Komponenten prüfen
# ---------------------------------------------------------------------------

def get_allowed_values_by_field():
    fields = load_ui_fields()
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


def normalize_prompt_components(raw_components):
    """
    Nimmt die vom Browser gesendeten Werte entgegen,
    prüft sie gegen ui_fields.json und gibt saubere Komponenten zurück.
    """
    if not isinstance(raw_components, dict):
        raise HTTPException(
            status_code=400,
            detail="prompt_components must be a JSON object"
        )

    allowed_values_by_field = get_allowed_values_by_field()

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

def build_prompt_from_components(components):
    """
    Baut aus den serverseitig geprüften Prompt-Komponenten einen klaren,
    priorisierten Modell-Prompt.

    Struktur:
    1. Identität erhalten
    2. Hauptszene / Körper / Aktion
    3. Gesicht / Blick / Ausdruck
    4. Kleidung
    5. Umgebung / Kamera / Licht / Wirkung
    6. Banner-Freiraum, falls Banner aktiv
    7. Prioritäten am Ende wiederholen
    8. Schutzregeln / negative Constraints
    """
    brand_tone = components.get("brandTone", "")
    work_context = components.get("workContext", "")
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
        brand_tone,
        work_context,
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

    blocks = []

    blocks.append(
        "Preserve the person's identity, face structure, age, hairstyle, "
        "skin tone, natural body proportions, and recognizable appearance."
    )

    main_scene_parts = []

    if pose:
        main_scene_parts.append(pose)

    if action:
        main_scene_parts.append(action)

    if work_context:
        main_scene_parts.append(work_context)

    if main_scene_parts:
        blocks.append(
            "Create a realistic professional employer-branding photo where "
            "the person is " + ", ".join(main_scene_parts) + "."
        )

    if brand_tone:
        blocks.append(
            "Employer-brand image effect: " + brand_tone
        )

    face_parts = []

    if expression:
        face_parts.append(
            f"the person has a {expression}"
        )

    if gaze:
        face_parts.append(
            f"the person is {gaze}"
        )

    if face_parts:
        face_block = (
            "Face and gaze: " +
            "; ".join(face_parts) +
            "."
        )

        if gaze:
            face_block += (
                " The head direction and both eyes must clearly follow "
                "this gaze instruction."
            )

        blocks.append(face_block)

    if outfit:
        blocks.append(
            f"Clothing: the person is {outfit}."
        )

    visual_style_parts = []

    if framing:
        visual_style_parts.append(framing)

    if camera_angle:
        visual_style_parts.append(camera_angle)

    if lighting:
        visual_style_parts.append(lighting)

    if visual_style_parts:
        blocks.append(
            "Visual style and composition: use " +
            ", ".join(visual_style_parts) +
            "."
        )

    if banner.get("enabled"):
        banner_position = banner.get("position", "auto")

        if banner_position == "auto":
            banner_position = "bottom"

        if banner_position == "right":
            blocks.append(
                "Leave clean negative space on the right side of the image, "
                "with calm background space and no generated words, letters, logos, signs, labels, or typography."
            )
        elif banner_position == "top":
            blocks.append(
                "Leave clean negative space at the top of the image, "
                "with calm background space and no generated words, letters, logos, signs, labels, or typography."
            )
        else:
            blocks.append(
                "Leave clean negative space at the bottom of the image, "
                "with calm background space and no generated words, letters, logos, signs, labels, or typography."
            )

    if extra_prompt:
        normalized_extra = extra_prompt.strip()

        if normalized_extra[-1] not in ".!?":
            normalized_extra += "."

        blocks.append(normalized_extra)

    priority_details = []

    if gaze:
        priority_details.append(gaze)

    if expression:
        priority_details.append(expression)

    if action:
        priority_details.append(action)

    if pose:
        priority_details.append(pose)

    if work_context:
        priority_details.append(work_context)

    if outfit:
        priority_details.append(outfit)

    if camera_angle:
        priority_details.append(camera_angle)

    if framing:
        priority_details.append(framing)

    if lighting:
        priority_details.append(lighting)

    if banner.get("enabled"):
        priority_details.append(
            "simple uncluttered background space with no generated text"
        )

    if priority_details:
        blocks.append(
            "Priority details to follow clearly: " +
            "; ".join(priority_details) +
            "."
        )

    blocks.append(
        ""
    )

    return "\n\n".join(block for block in blocks if block).strip()


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


def patch_workflow(workflow, image_name, prompt):
    workflow["78"]["inputs"]["image"] = image_name
    workflow["435"]["inputs"]["value"] = prompt
    workflow["433:111"]["inputs"]["prompt"] = ["435", 0]
    workflow["433:3"]["inputs"]["seed"] = uuid.uuid4().int % 1_000_000_000_000_000

    return workflow


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


@app.get("/api/ui-fields")
def ui_fields():
    fields = load_ui_fields()

    print("UI_FIELDS_PATH:", UI_FIELDS_PATH.resolve())
    print("GELADENE FELDER:", [field.get("id") for field in fields])

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
    raw_components = payload.get("components", payload)

    components = normalize_prompt_components(raw_components)
    prompt = build_prompt_from_components(components)

    return JSONResponse(
        content={
            "prompt": prompt,
            "components": components
        },
        headers=no_cache_headers()
    )


@app.post("/api/run")
async def run(
    prompt_components: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        raw_components = json.loads(prompt_components)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"prompt_components is not valid JSON: {exc}"
        )

    components = normalize_prompt_components(raw_components)
    final_prompt = build_prompt_from_components(components)

    if not final_prompt:
        raise HTTPException(
            status_code=400,
            detail="Prompt is empty"
        )

    print("\n" + "=" * 100)
    print("PROMPT-KOMPONENTEN VOM FRONTEND:")
    print(json.dumps(components, indent=2, ensure_ascii=False))
    print()
    print("FINALER SERVER-PROMPT:")
    print(final_prompt)
    print("=" * 100 + "\n")

    image_bytes = await file.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty"
        )

    stored_image_name = comfy_upload_image(
        image_bytes,
        file.filename
    )

    workflow = deep_copy_workflow()

    workflow = patch_workflow(
        workflow,
        stored_image_name,
        final_prompt
    )

    print("PROMPT IN WORKFLOW NODE 435:")
    print(workflow["435"]["inputs"]["value"])
    print()

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
            "prompt_id": prompt_id,
            "submitted_prompt": final_prompt,
            "prompt_components": components,
            "view_urls": view_urls
        },
        headers=no_cache_headers()
    )
# Employer Branding Prompt Lab

Webanwendung zur KI-gestützten Erstellung und Überarbeitung von Employer-Branding-Motiven. Aus ausgewählten Parametern entsteht ein strukturierter Prompt. Als Bildanbieter kann entweder die OpenAI Images API oder optional eine lokale ComfyUI-Installation verwendet werden.

## Voraussetzungen

### Betrieb über die OpenAI Images API

- Windows 10 oder 11
- Python 3.10 oder 3.11
- OpenAI-API-Schlüssel mit verfügbarem Guthaben

Für diesen Betrieb sind **keine ComfyUI-Installation, keine NVIDIA-GPU und keine lokalen Bildmodelle** erforderlich.

### Optional: lokaler Betrieb über ComfyUI

Nur für die lokale Bildgenerierung werden zusätzlich benötigt:

- aktuelle ComfyUI-Installation
- geeignete NVIDIA-GPU
- folgende Modelldateien:

| Datei | Ordner innerhalb von `ComfyUI/models` |
| --- | --- |
| `qwen_image_2512_fp8_e4m3fn.safetensors` | `diffusion_models` |
| `qwen_image_edit_2509_fp8_e4m3fn.safetensors` | `diffusion_models` |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `text_encoders` |
| `qwen_image_vae.safetensors` | `vae` |
| `Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors` | `loras` |

## Installation

1. Repository herunterladen oder klonen:

   ```powershell
   git clone https://github.com/David90dotcom/Employer-Branding.git
   cd Employer-Branding
   ```

2. Python-Umgebung erstellen und Pakete installieren:

   ```powershell
   py -3.10 -m venv webapp_env
   webapp_env\Scripts\python.exe -m pip install --upgrade pip
   webapp_env\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Konfigurationsdatei anlegen:

   ```powershell
   Copy-Item .env.example .env
   ```

## OpenAI-API einrichten

Die Datei `.env` öffnen und folgende Werte eintragen:

```env
OPENAI_API_KEY=DEIN_OPENAI_API_SCHLUESSEL
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_QUALITY=medium
EMPLOYER_BRANDING_IMAGE_PROVIDER=openai
EMPLOYER_BRANDING_SKIP_COMFYUI=1
```

Der API-Schlüssel bleibt ausschließlich in der lokalen `.env` und darf nicht auf GitHub hochgeladen werden. Die Datei wird durch `.gitignore` ausgeschlossen.

## Optional: ComfyUI einrichten

Für den lokalen Betrieb in `.env` einstellen:

```env
EMPLOYER_BRANDING_IMAGE_PROVIDER=local
EMPLOYER_BRANDING_SKIP_COMFYUI=0
```

Anschließend in `main.py` die Pfade zur eigenen ComfyUI-Installation prüfen:

```python
COMFY_DIR = Path(r"C:\AI\image\ComfyUI")
COMFY_PYTHON = COMFY_DIR / "venv" / "Scripts" / "python.exe"
```

Die Workflows werden automatisch aus dem Ordner `webapp` geladen.

## Start

Im Projektordner ausführen:

```powershell
webapp_env\Scripts\python.exe main.py
```

Im OpenAI-only-Betrieb startet ausschließlich die Webanwendung. Bei lokaler Konfiguration startet der Launcher zusätzlich ComfyUI. Anschließend öffnet sich automatisch:

`http://127.0.0.1:8765`

## Bedienung

1. Bildanbieter und Arbeitsmodus auswählen.
2. gewünschte Kampagnen- und Bildparameter festlegen.
3. Prompt-Vorschau prüfen.
4. Bild erzeugen oder einen gespeicherten synthetischen Entwurf weiterbearbeiten.
5. optional Kampagnentext ergänzen und Ergebnis herunterladen.

Erzeugte Bilder werden zusätzlich unter `webapp/static/generated` gespeichert.

## Beenden

Die Webanwendung verwendet Port `8765`, ComfyUI optional Port `8188`.

OpenAI-only-Betrieb beenden:

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess }
```

Webanwendung und lokale ComfyUI-Instanz beenden:

```powershell
Get-NetTCPConnection -LocalPort 8765,8188 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess }
```

## Wenn etwas nicht startet

- **OpenAI nicht verfügbar:** API-Schlüssel und Einstellungen in `.env` prüfen.
- **API-Anfrage scheitert:** API-Guthaben, Modellname und Internetverbindung prüfen.
- **Port bereits belegt:** laufende Instanz beenden oder den Port in `main.py` ändern.
- **Lokales Modell fehlt:** Dateiname und Modellordner mit der Tabelle vergleichen.
- **ComfyUI-Node fehlt:** ComfyUI aktualisieren und erneut starten.
- **Weitere Fehler:** `webapp_start.log` und bei lokalem Betrieb `comfy_start.log` öffnen.

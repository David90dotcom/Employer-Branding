# Employer Branding Prompt Lab

Lokale Webanwendung zur KI-gestützten Überarbeitung von Employer-Branding-Motiven. Die Anwendung erstellt aus ausgewählten Parametern einen strukturierten Prompt, übergibt Bild und Prompt an ComfyUI und ergänzt auf Wunsch Kampagnentext sowie die Kennzeichnung `AI GENERATED`.

## Voraussetzungen

- Windows 10 oder 11
- Python 3.10 oder 3.11
- aktuelle ComfyUI-Installation mit funktionierender NVIDIA-GPU

Der mitgelieferte Workflow benötigt diese Modelldateien:

| Datei | Ordner innerhalb von `ComfyUI/models` |
| --- | --- |
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

3. In `main.py` diese beiden Pfade prüfen und bei Bedarf anpassen:

   ```python
   COMFY_DIR = Path(r"C:\AI\image\ComfyUI")
   COMFY_PYTHON = COMFY_DIR / "venv" / "Scripts" / "python.exe"
   ```

4. ComfyUI einmal separat starten und prüfen. Der Workflow wird später automatisch aus `webapp/workflow_template.json` geladen und muss nicht manuell importiert werden.

## Start

Im Projektordner ausführen:

```powershell
webapp_env\Scripts\python.exe main.py
```

Der Launcher startet ComfyUI und die Webanwendung. Danach öffnet sich automatisch:

`http://127.0.0.1:8000`

## Bedienung

1. Ausgangsbild hochladen.
2. gewünschte Vorgaben auswählen.
3. optional Kampagnentext aktivieren.
4. Prompt-Vorschau prüfen.
5. Bildgenerierung starten und Ergebnis herunterladen.

Die erzeugten Bilder werden zusätzlich unter `webapp/static/generated` gespeichert.

## Beenden

ComfyUI und Webanwendung verwenden die Ports `8188` und `8000`. Zum Beenden in PowerShell:

```powershell
Get-NetTCPConnection -LocalPort 8000,8188 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess }
```

## Wenn etwas nicht startet

- **Pfad nicht gefunden:** Pfade zu ComfyUI und Python in `main.py` kontrollieren.
- **Port bereits belegt:** laufende Instanz beenden oder Ports in `main.py` ändern.
- **Modell fehlt:** Dateiname und Modellordner mit der Tabelle oben vergleichen.
- **Node fehlt:** ComfyUI aktualisieren und erneut starten.
- **Weitere Fehler:** `comfy_start.log` und `webapp_start.log` im Projektordner öffnen.

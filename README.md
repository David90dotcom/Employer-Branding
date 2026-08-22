# Employer-Branding Image Studio

Lokaler Prototyp zur standardisierten Erstellung von Employer-Branding-Bildmotiven mit ComfyUI.

## Modi

- **Text-to-Image (Standard):** erzeugt vollständig fiktive Personen und Arbeitsszenen ohne Upload eines Personenfotos.
- **Image-to-Image (experimentell):** bearbeitet ein berechtigt hochgeladenes Bild mit dem bisherigen Qwen-Image-Edit-Workflow. Dieser Modus verlangt eine Einwilligungsbestätigung im Browser und prüft diese zusätzlich auf dem Server. Auch Dateityp, Dateigröße und Bildabmessungen werden serverseitig kontrolliert.

Beide Modi verwenden dieselbe serverseitige Prompt-Erzeugung, Ergebnisanzeige, optionale Kampagnen-Banner und den Hinweis `KI-generiert`.

## Prompt Engineering Lab

Der Text-to-Image-Modus übersetzt ein deutschsprachiges Kampagnenbriefing in
einen englischen, modular aufgebauten Gesamtprompt. Die Struktur ist in der
Oberfläche und in der Serverantwort sichtbar:

1. `CREATIVE ROLE / DIRECTION`
2. `TASK AND CAMPAIGN GOAL`
3. `CONTEXT`
4. `VISUAL SPECIFICATION`
5. `CONSTRAINTS`
6. `OUTPUT FORMAT`
7. `SUCCESS CRITERIA`

Die Auswahlfelder decken Kampagnenziel, Zielgruppe, Branchenkontext,
Arbeitgebernutzen, einen Impuls aus dem Wettbewerbsvergleich sowie die
beobachtbare Bildgestaltung ab. Reale Unternehmens- und Markennamen werden
nicht in das Modellprompt übernommen. Verbindliche ethische und markenbezogene
Leitplanken werden zusätzlich als separater Negative Prompt an den
Text-to-Image-Workflow übergeben.

Die Anwendung erzeugt dabei keinen technischen „Multi-Prompt“, sondern einen
strukturierten positiven Gesamtprompt und einen zugehörigen Negative Prompt.
Für kontrollierte Vergleiche kann ein fester Seed verwendet werden. Werden
Promptvarianten verglichen, sollten Modell, Seed, Seitenverhältnis, Schritte,
CFG, Sampler und Scheduler unverändert bleiben.

Die automatisch abgeleiteten Erfolgskriterien dienen als Prüfraster für die
menschliche Bewertung. Sie sind keine automatische Qualitätsgarantie und
ersetzen weder den Faktencheck noch die ethische und markenrechtliche Freigabe.

## Standardmodell für Text-to-Image

Die Konfiguration liegt in `webapp/model_config.json`. Voreingestellt ist:

- Diffusionsmodell: `qwen_image_2512_fp8_e4m3fn.safetensors`
- Textencoder: `qwen_2.5_vl_7b_fp8_scaled.safetensors`
- VAE: `qwen_image_vae.safetensors`
- 50 Schritte, CFG 4, Euler, Simple Scheduler

Die Dateien werden nicht mit dem Repository ausgeliefert. Sie müssen in den dafür vorgesehenen ComfyUI-Modellordnern liegen:

```text
ComfyUI/models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors
ComfyUI/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
ComfyUI/models/vae/qwen_image_vae.safetensors
```

Der Text-to-Image-Workflow orientiert sich am nativen Qwen-Image-2512-Workflow von ComfyUI. Für diese Modellgeneration muss ComfyUI aktuell genug sein, um unter anderem `EmptySD3LatentImage`, `ModelSamplingAuraFlow` und den Qwen-Image-Typ des `CLIPLoader` zu unterstützen.

## Installation unter Windows

1. ComfyUI installieren beziehungsweise aktualisieren und die benötigten Modelle in die oben genannten Ordner legen.
2. Im Projektordner eine virtuelle Umgebung erstellen:

   ```powershell
   py -3.10 -m venv webapp_env
   .\webapp_env\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Falls ComfyUI nicht unter `C:\AI\image\ComfyUI` liegt, die Konstante `COMFY_DIR` am Anfang von `main.py` anpassen.
4. Die Anwendung starten:

   ```powershell
   python main.py
   ```

Die Web-App wird anschließend lokal unter `http://127.0.0.1:8000` geöffnet. ComfyUI läuft lokal unter `http://127.0.0.1:8188`.

## Modellwechsel

Für einen späteren Modellwechsel werden Modellnamen und Text-to-Image-Parameter zentral in `webapp/model_config.json` geändert. Benötigt ein anderes Modell eine andere ComfyUI-Node-Struktur, wird zusätzlich `webapp/workflow_text_to_image.json` ersetzt und die dortigen Node-IDs werden im Abschnitt `nodes` der Konfiguration angepasst.

## Datenschutz und Transparenz

Text-to-Image ist der Standard, damit keine realen Personenfotos für die reguläre Kampagnengenerierung verarbeitet werden. Die Ergebnisse zeigen fiktive KI-Personen und dürfen nicht als echte Beschäftigte oder Testimonials ausgegeben werden. Jedes Ergebnis erhält automatisiert den eingebetteten Hinweis `KI-generiert` und muss vor einer Nutzung fachlich, ethisch sowie markenrechtlich geprüft werden.

Reale Unternehmenslogos werden nicht durch das Modell generiert und sind nicht Bestandteil des Repositorys. Ein berechtigt verwendetes Logo sollte erst nach der Generierung als exakter Overlay ergänzt werden.

Generierte Bilder werden lokal unter `webapp/static/generated/` gespeichert. Der Inhalt dieses Ordners wird von Git ignoriert; nur `.gitkeep` bleibt versioniert.

## Entwicklungszweige

- `archive/image-to-image-v1`: unveränderter Ausgangsstand der ursprünglichen Image-to-Image-Version
- `feature/text-to-image-default`: Entwicklung von Text-to-Image als Standard und Image-to-Image als experimenteller Zusatzmodus

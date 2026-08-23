# Employer-Branding Image Studio

Lokaler Prototyp zur standardisierten Erstellung von Employer-Branding-Bildmotiven mit ComfyUI.

## Modi

- **Stufe 1 - Text-to-Image:** erzeugt vollständig fiktive Personen und
  Arbeitsszenen ohne Upload eines Personenfotos.
- **Stufe 2 - Prompt Chain / Optimierung:** übernimmt ausschließlich ein bewusst
  gespeichertes synthetisches Ergebnis aus Stufe 1 und optimiert es mit einem
  fokussierten zweiten Prompt.

Ein separater Upload- oder Image-to-Image-Modus für reale Personenbilder ist
nicht Bestandteil der aktuellen Anwendung. Beide Stufen verwenden dieselbe
Ergebnisanzeige, optionale Kampagnen-Banner und den Hinweis `KI-generiert`.

## Prompt Engineering Lab

Der Text-to-Image-Modus trennt drei Ebenen, die in der Oberfläche und in der
Serverantwort sichtbar bleiben:

1. **Kampagnenbriefing:** Kampagnenziel, Zielgruppe, Branchenkontext,
   Arbeitgebernutzen, Wettbewerbsimpuls und Gestaltungsrahmen begründen die
   Konzeption. Diese Angaben werden nicht wörtlich an das Bildmodell gesendet.
2. **Visuelle Übersetzung:** Abstrakte Ziele werden in beobachtbare Merkmale
   übertragen, zum Beispiel „Teamarbeit“ in eine gemeinsame Blickachse auf
   dieselbe Aufgabe oder „Verantwortung“ in eine aktiv ausgeführte Handlung.
3. **Render-Prompt:** Nur visuell umsetzbare Angaben zu Person, Arbeitsumgebung,
   Handlung, Körperhaltung, Blick, Kleidung, Komposition, Licht und Format
   werden als englischer Prompt an Qwen gesendet.

Zielgruppeninformationen wie „Schulabgänger:innen in NRW“, die regionale
Einordnung sowie das menschliche Prüfraster bleiben außerhalb des
Render-Prompts. Dadurch wird insbesondere vermieden, dass eine abstrakte
Zielgruppenbezeichnung unbeabsichtigt sehr jung oder minderjährig wirkende
Personen auslöst. Das sichtbare Alter wird stattdessen ausdrücklich über die
Personendarstellung und die Adult-only-Leitplanken gesteuert.

Der tatsächliche Render-Prompt besteht aus:

1. `VISUAL DIRECTION`
2. `VISIBLE CAMPAIGN INTENT`
3. `SUBJECTS AND WORKPLACE`
4. `ACTION AND APPEARANCE`
5. `COMPOSITION AND LIGHT`
6. `VISUAL CONSTRAINTS`
7. `OUTPUT`

Reale Unternehmens- und Markennamen werden nicht in den Render-Prompt
übernommen. Verbindliche visuelle Schutzregeln werden zusätzlich als separater
Negative Prompt an den Text-to-Image-Workflow übergeben. Die automatisch
abgeleiteten Erfolgskriterien bilden ein getrenntes menschliches Prüfraster;
sie werden nicht an Qwen gesendet und ersetzen weder Faktencheck noch ethische
und markenrechtliche Freigabe.

Die Anwendung erzeugt keinen technischen „Multi-Prompt“, sondern einen
strukturierten Render-Prompt und einen zugehörigen Negative Prompt. Für
kontrollierte Vergleiche kann ein fester Seed verwendet werden. Werden
Promptvarianten verglichen, sollten Modell, Seed, Seitenverhältnis, Schritte,
CFG, Sampler und Scheduler unverändert bleiben.

## Lokale Kampagnenbibliothek und Prompt Chain

Neue Ergebnisse sind zunächst temporär und werden nach der konfigurierten
Aufbewahrungsdauer aus einem nicht öffentlich eingebundenen lokalen
Datenbereich entfernt. Erst der ausdrückliche Button
`In Bibliothek speichern` legt ein Motiv dauerhaft in der lokalen
Kampagnenbibliothek ab. Gespeichert werden:

- das gekennzeichnete Vorschaubild,
- ein kleines Vorschaubild für die Bibliotheksansicht,
- eine saubere interne Bearbeitungsquelle ohne eingebrannten Banner oder
  `KI-generiert`-Overlay,
- positiver Prompt und Negative Prompt,
- Modus, Seed, Modellbezeichnung und Bildabmessungen,
- bei verketteten Ergebnissen die Referenz auf das Ausgangsmotiv.

Ein gespeichertes synthetisches Text-to-Image-Ergebnis kann über
`Weiterbearbeiten` als zweite Stufe einer multimodalen Prompt Chain an den
Qwen-Edit-Workflow übergeben werden. Die saubere Bearbeitungsquelle verhindert,
dass der Transparenzhinweis oder ein zuvor eingefügter Banner Teil der nächsten
Bildgenerierung wird.

Die zweite Stufe ist als kontrollierte Kampagnenoptimierung aufgebaut. Sie
verlangt ein primäres Optimierungsziel und gliedert den Prompt in:

1. `ROLE AND METHOD`
2. `PRIMARY OPTIMIZATION TASK`
3. `SOURCE AND PRESERVATION`
4. `REQUESTED VISUAL CHANGES`
5. `CONSTRAINTS`
6. `OUTPUT`

Ausgewählt werden können Änderungsstärke, Erhaltungsfokus, Tätigkeit,
Personenzahl und Interaktion, Pose, Blick, Gesichtsausdruck, Rollenwirkung,
Kampagnenkomposition, Bildwirkung sowie eine konkrete Qualitätskorrektur.
Nicht ausgewählte Merkmale sollen möglichst stabil bleiben. Ein fester Seed
ermöglicht auch in Stufe 2 besser kontrollierbare Promptvergleiche.
Die Definition of Done wird als separates menschliches Prüfraster angezeigt
und nicht in den Optimierungs-Prompt geschrieben.

Die Kampagnenbibliothek liegt ausschließlich unter `webapp/data/` und wird von
Git ignoriert. Sie wird daher weder committed noch nach GitHub hochgeladen.

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

Die Web-App wird anschließend lokal unter `http://127.0.0.1:8765` geöffnet. Ist dieser projektspezifische Standardport bereits belegt, wählt der Launcher automatisch den nächsten freien Port und zeigt die tatsächlich verwendete Adresse an. Eine bereits unter `http://127.0.0.1:8188` laufende ComfyUI-Instanz wird wiederverwendet, statt einen zweiten Prozess zu starten.

## Modellwechsel

Für einen späteren Modellwechsel werden Modellnamen und Text-to-Image-Parameter zentral in `webapp/model_config.json` geändert. Benötigt ein anderes Modell eine andere ComfyUI-Node-Struktur, wird zusätzlich `webapp/workflow_text_to_image.json` ersetzt und die dortigen Node-IDs werden im Abschnitt `nodes` der Konfiguration angepasst.

## Datenschutz und Transparenz

Die Anwendung verarbeitet keine hochgeladenen realen Personenfotos. Die
Ergebnisse zeigen fiktive KI-Personen und dürfen nicht als echte Beschäftigte
oder Testimonials ausgegeben werden. Jedes Ergebnis erhält automatisiert den
eingebetteten Hinweis `KI-generiert` und muss vor einer Nutzung fachlich,
ethisch sowie markenrechtlich geprüft werden.

Alle dargestellten Personen müssen eindeutig als volljährige fiktive
Erwachsene erkennbar sein. Kinder, Minderjährige und altersmäßig uneindeutige
Hintergrundpersonen werden durch Positivprompt, Negative Prompt und
Erfolgskriterien ausdrücklich ausgeschlossen; die menschliche Sichtprüfung
bleibt dennoch verbindlich.

Reale Unternehmenslogos werden nicht durch das Modell generiert und sind nicht Bestandteil des Repositorys. Ein berechtigt verwendetes Logo sollte erst nach der Generierung als exakter Overlay ergänzt werden.

Temporäre Ergebnisse und die bewusst gespeicherte Kampagnenbibliothek liegen
lokal unter `webapp/data/`. Dieser Bereich wird von Git ignoriert und nicht in
das öffentliche Repository übernommen.

## Entwicklungszweige

- `archive/image-to-image-v1`: unveränderter Ausgangsstand der ursprünglichen Image-to-Image-Version
- `feature/text-to-image-default`: Einführung von Text-to-Image als Standard
- `feature/prompt-engineering-lab`: strukturierter Promptbaukasten für Stufe 1
- `feature/prompt-chain-library`: lokale Kampagnenbibliothek und verkettete Optimierungsstufe

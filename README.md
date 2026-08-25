# Employer-Branding Image Studio

Prototyp zur standardisierten Erstellung von Employer-Branding-Bildmotiven.
Als Bildanbieter stehen der lokale ComfyUI-Workflow und optional die OpenAI
Images API zur Verfügung.

## Modi

- **Stufe 1 - Text-to-Image:** erzeugt vollständig fiktive Personen und
  Arbeitsszenen ohne Upload eines Personenfotos.
- **Stufe 2 - Prompt Chain / Optimierung:** übernimmt ausschließlich ein bewusst
  gespeichertes synthetisches Ergebnis aus Stufe 1 und optimiert es mit einem
  fokussierten zweiten Prompt.

Ein separater Upload- oder Image-to-Image-Modus für reale Personenbilder ist
nicht Bestandteil der aktuellen Anwendung. Beide Stufen verwenden dieselbe
Ergebnisanzeige, optionale Kampagnen-Banner, ein optionales PNG-Logo und den
verpflichtenden Hinweis `KI-generiert`.

## Bildanbieter

Die Oberfläche bietet zwei klar getrennte Anbieter:

- **Lokal · Qwen / ComfyUI:** Render-Prompt und synthetische Bilddaten bleiben
  im lokalen Workflow. Ein fester Seed und ein separater Negative Prompt sind
  verfügbar.
- **Cloud · OpenAI Images API:** verwendet standardmäßig `gpt-image-2` für
  Text-to-Image und für die Bearbeitung eines gespeicherten synthetischen
  Ausgangsbildes. Die Cloud-API unterstützt in dieser Einbindung weder einen
  festen Seed noch einen separaten Negative Prompt. Die verbindlichen
  visuellen Leitplanken bleiben deshalb Teil des positiven Render-Prompts.

Der Cloud-Anbieter überträgt den Render-Prompt und in der zweiten Prompt-Stufe
das ausgewählte synthetische Ausgangsbild an die Images API. Die Auswahl wird
in den technischen Daten des Ergebnisses dokumentiert. Ein API-Key wird
ausschließlich serverseitig aus `OPENAI_API_KEY` gelesen und niemals an den
Browser, in das Prompt-Paket, in die lokale Bibliothek oder in die
Serverantwort ausgegeben.

Cloud-Aufrufe können Kosten im zugehörigen API-Projekt verursachen. Für GPT
Image kann außerdem eine Organisationsverifizierung erforderlich sein. Vor dem
ersten Einsatz sollten daher Abrechnung, Projektlimits und API-Zugriff im
eigenen OpenAI-Projekt geprüft werden.

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

### Widerspruchsfreie Personen- und Kleidungssteuerung

Personenzahl, Rollenverteilung und Bildhierarchie werden gemeinsam über die
`Personenkonstellation` festgelegt. Davon getrennt steuern eigene Felder:

- den Alterseindruck ausschließlich der Hauptperson,
- zusätzliche Personen ausschließlich im Hintergrund,
- die Kleidung und Farbpalette der Hauptperson,
- eine sichtbar abweichende Kleidung der Begleitpersonen,
- optional sichtbare Geschlechtspräsentation, Hautton und Haarstruktur getrennt
  für Haupt- und Begleitpersonen.

Die Konstellationen unterscheiden zwischen einzelner Hauptperson,
Hauptperson mit erwachsener Mentorin beziehungsweise erwachsenem Mentor,
gleichrangigem Zweierteam und dreiköpfigem Erwachsenenteam. Tätigkeiten tragen
Kompatibilitätsangaben: Bei einer einzelnen Hauptperson kann beispielsweise
keine „praktische Anleitung“ ausgewählt werden, die zwei Personen voraussetzt.
Die Oberfläche deaktiviert unpassende Tätigkeiten und wählt bei einem Wechsel
eine geeignete Alternative; dieselbe Regel wird zusätzlich serverseitig
geprüft. Bei einer Einzelperson wird das Feld für Begleitkleidung ausgeblendet
und nicht in den Render-Prompt übernommen.

Alters- und Kleidungsoptionen ergänzen passende dynamische Ausschlüsse im
Negative Prompt. Dadurch kann ein gewünschter junger, aber eindeutig
volljähriger Alterseindruck gestärkt werden, ohne eine später bewusst gewählte
ältere Hauptperson durch einen statischen Negative Prompt auszuschließen.

Die optionalen Repräsentationsfelder sind standardmäßig eingeklappt und ohne
Vorgabe. Bewusst gibt es weder einen pauschalen Schalter `Diversität an/aus`
noch einen vermeintlich messbaren Diversitätsgrad. Gesteuert werden nur
beobachtbare Gestaltungsmerkmale pro fiktiver Rolle. Die Promptlogik trennt
diese Merkmale ausdrücklich von Kompetenz, Hierarchie, Persönlichkeit und
Tätigkeit und leitet daraus keine Ethnie, Nationalität, Religion oder soziale
Herkunft ab. Bei einer einzelnen Hauptperson werden Angaben zu
Begleitpersonen ausgeblendet und serverseitig verworfen. Ein zusätzliches
menschliches Prüfkriterium macht stereotype oder tokenistische Zuordnungen
sichtbar; in Stufe 2 gehören die gewählten Merkmale zur zu erhaltenden
Bildkontinuität.

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
- eine saubere interne Bearbeitungsquelle ohne eingebrannten Banner,
  Firmenlogo oder `KI-generiert`-Overlay,
- positiver Prompt und Negative Prompt,
- Modus, Anbieter, Seed-Verfügbarkeit, Modellbezeichnung und Bildabmessungen,
- bei verketteten Ergebnissen die Referenz auf das Ausgangsmotiv.

Ein gespeichertes synthetisches Text-to-Image-Ergebnis kann über
`Weiterbearbeiten` als zweite Stufe einer multimodalen Prompt Chain an den
Qwen-Edit-Workflow übergeben werden. Die saubere Bearbeitungsquelle verhindert,
dass der Transparenzhinweis oder ein zuvor eingefügter Banner Teil der nächsten
Bildgenerierung wird.

Die zweite Stufe ist als kontrollierte Kampagnenoptimierung aufgebaut. Sie
kann optional ein übergeordnetes Optimierungsziel verwenden und gliedert den
Prompt in:

1. `ROLE AND METHOD`
2. `PRIMARY OPTIMIZATION TASK`
3. `SOURCE AND PRESERVATION`
4. `REQUESTED VISUAL CHANGES`
5. `CONSTRAINTS`
6. `OUTPUT`

Bleibt das übergeordnete Ziel leer, leitet die Anwendung den Root Task direkt
aus den ausgewählten Einzeländerungen ab. So kann beispielsweise ausschließlich
der Bildausschnitt oder die nutzbare Textfläche angepasst werden. Mindestens
eine tatsächliche Bildänderung oder ein Freitextauftrag bleibt erforderlich;
die voreingestellte Änderungsstärke allein startet keine Bearbeitung.

Ausgewählt werden können Änderungsstärke, Erhaltungsfokus, Tätigkeit,
Personenzahl und Interaktion, Pose, Blick, Gesichtsausdruck, Rollenwirkung,
Bildausschnitt, nutzbare Kampagnentextfläche, Gestaltung dieser Fläche,
Bildwirkung sowie eine konkrete Qualitätskorrektur. Bildausschnitt und
Textfläche sind bewusst getrennt: So lassen sich beispielsweise ein
Halbkörpermotiv und rechts 35 bis 40 Prozent ruhige Textfläche gleichzeitig
anfordern. Wird dort anschließend ein Banner gesetzt, übernimmt dessen
Position automatisch die ausgewählte Kampagnenfläche.
Nicht ausgewählte Merkmale sollen möglichst stabil bleiben. Ein fester Seed
ermöglicht auch in Stufe 2 besser kontrollierbare Promptvergleiche.
Die Definition of Done wird als separates menschliches Prüfraster angezeigt
und nicht in den Optimierungs-Prompt geschrieben.

## Layout-Studio ohne erneute Bildgenerierung

Kampagnentext und Logo können sowohl während eines Bildlaufs als auch später
auf ein temporäres oder bereits gespeichertes Motiv gesetzt werden. Dafür
verwendet die App stets die saubere interne Bildquelle und ruft weder ComfyUI
noch die Cloud Images API erneut auf. Das vereinfachte Textwerkzeug bietet
Headline, optionale Subheadline, Position, Bannerstil, Schriftstil,
Schriftgröße, Ausrichtung und Akzentfarbe. Ein optionales Logo kann weiterhin
als PNG frei positioniert und skaliert werden. Standardmäßig verkleinert die
automatische Typografie die Schrift bei Bedarf, um einzelne Wörter in einer
eigenen Zeile zu vermeiden. In Headline und Subheadline können Zeilenumbrüche
mit der Eingabetaste bewusst festgelegt werden.

Optional lässt sich ein freies Textfeld aktivieren. Es kann direkt in der
16:9-Vorschau über einen eigenen Griff verschoben und an der rechten unteren
Ecke stufenlos vergrößert werden. Horizontale und vertikale Position, Breite,
Höhe und Schriftgröße können zusätzlich über Prozentregler exakt eingestellt
werden. In diesem Modus bleibt die gewählte Schriftgröße verbindlich; passt der
Text nicht in das Textfeld, fordert die App zum Vergrößern des Feldes, zum
Verkleinern der Schrift oder zum Setzen weiterer manueller Zeilenumbrüche auf,
statt die Schrift unbemerkt zu reduzieren. Die Geometrie dient ausschließlich
dem deterministischen Layout und wird nicht an das Bildmodell gesendet.

`Layout ohne KI anwenden` erzeugt zunächst eine neue temporäre
Gestaltungsvariante. Sie kann kontrolliert betrachtet und anschließend über
den bestehenden Button `In Bibliothek speichern` dauerhaft abgelegt werden.
Das ursprüngliche Bild bleibt dabei unverändert und die Bibliothek kennzeichnet
solche Varianten ausdrücklich als `Layout ohne Bild-KI`.

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

1. Im Projektordner eine virtuelle Umgebung erstellen:

   ```powershell
   py -3.10 -m venv webapp_env
   .\webapp_env\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Für die lokale Generierung ComfyUI installieren beziehungsweise
   aktualisieren und die benötigten Modelle in die oben genannten Ordner
   legen. Falls ComfyUI nicht unter `C:\AI\image\ComfyUI` liegt, die Konstante
   `COMFY_DIR` am Anfang von `main.py` anpassen.
3. Optional den Cloud-Anbieter konfigurieren. Die Beispieldatei wird als
   lokale `.env` kopiert; diese Datei wird von Git ignoriert:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   In `.env` ausschließlich lokal den eigenen Schlüssel eintragen:

   ```dotenv
   OPENAI_API_KEY=hier_den_eigenen_api_key_eintragen
   OPENAI_IMAGE_MODEL=gpt-image-2
   OPENAI_IMAGE_QUALITY=medium
   ```

   Den Schlüssel niemals in die Browseroberfläche, ein Prompt-Feld, einen
   Screenshot, einen Commit oder eine Supportnachricht kopieren. Alternativ
   kann `OPENAI_API_KEY` entsprechend der offiziellen API-Dokumentation als
   System-Umgebungsvariable gesetzt werden.
4. Die Anwendung starten:

   ```powershell
   python main.py
   ```

Die Web-App wird anschließend lokal unter `http://127.0.0.1:8765` geöffnet. Ist dieser projektspezifische Standardport bereits belegt, wählt der Launcher automatisch den nächsten freien Port und zeigt die tatsächlich verwendete Adresse an. Eine bereits unter `http://127.0.0.1:8188` laufende ComfyUI-Instanz wird wiederverwendet, statt einen zweiten Prozess zu starten.

Für einen reinen Cloud-Betrieb kann in `.env` zusätzlich gesetzt werden:

```dotenv
EMPLOYER_BRANDING_IMAGE_PROVIDER=openai
EMPLOYER_BRANDING_SKIP_COMFYUI=1
```

Der Launcher prüft in diesem Fall keinen ComfyUI-Pfad und startet nur die
Web-App. Ohne konfigurierten `OPENAI_API_KEY` bricht der Cloud-only-Start mit
einer klaren Fehlermeldung ab.

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

Reale Unternehmenslogos werden nicht durch das Modell generiert und sind nicht
Bestandteil des Repositorys. Ein Logo kann optional als PNG ausgewählt,
horizontal und vertikal positioniert sowie zwischen 5 und 30 Prozent der
Bildbreite skaliert werden. Die App akzeptiert ausschließlich eine lesbare PNG
bis 5 MB und kodiert sie ohne eingebettete Metadaten neu. Die Datei wird nur im
Arbeitsspeicher des aktuellen Gestaltungs- oder Generierungsaufrufs verarbeitet.
Ob die konkrete Verwendung zulässig ist, hängt vom jeweiligen Kontext ab und
muss von der anwendenden Person selbst geprüft werden.

Banner und Logo werden ausschließlich auf das Anzeige- und Exportbild gelegt.
Die saubere interne Quelle für eine spätere Prompt-Chain-Stufe bleibt ohne
diese Gestaltungselemente. Der verpflichtende Hinweis `KI-generiert` wird
zuletzt oben rechts ergänzt und kann daher nicht vom Logo verdeckt werden. Bei
einer Überschneidung verschiebt die App das Logo in den nächstgelegenen freien
Bereich. Der neutrale Hinweis in der Oberfläche erinnert daran, die zulässige
Nutzung im jeweiligen Studien-, Demonstrations- oder Anwendungskontext selbst
zu prüfen; er stellt keine rechtliche Bewertung dar.

Temporäre Ergebnisse und die bewusst gespeicherte Kampagnenbibliothek liegen
lokal unter `webapp/data/`. Dieser Bereich wird von Git ignoriert und nicht in
das öffentliche Repository übernommen.

Die lokale `.env` enthält gegebenenfalls den Cloud-Schlüssel und wird ebenfalls
von Git ignoriert. Nur die leere `.env.example` gehört in das Repository. Die
Weboberfläche erhält ausschließlich den Status „konfiguriert“ oder „nicht
konfiguriert“, niemals den Schlüsselwert. Bei Auswahl des Cloud-Anbieters weist
die Oberfläche ausdrücklich auf die Datenübertragung hin.

## Entwicklungszweige

- `archive/image-to-image-v1`: unveränderter Ausgangsstand der ursprünglichen Image-to-Image-Version
- `feature/text-to-image-default`: Einführung von Text-to-Image als Standard
- `feature/prompt-engineering-lab`: strukturierter Promptbaukasten für Stufe 1
- `feature/prompt-chain-library`: lokale Kampagnenbibliothek und verkettete Optimierungsstufe

# Wochenaufgaben-Agent Mediamatiker · Setup (Weg 1: monday.com + Claude API)

Dieses Paket generiert jeden Montag automatisch je einen Wochentask für Devin und Amelia
(Bildungsplan-Bezug + JP-Praxisbezug) und schreibt ihn direkt auf die richtigen monday.com-Boards.

**Trigger:** GitHub Actions (kostenlos, zuverlässig, zeitgesteuert) statt einer nativen
monday.com-Automation — monday.com kann zwar zeitgesteuert Items *erstellen*, aber nicht
"rufe eine externe KI auf und schreibe deren Antwort strukturiert zurück" ohne eine
Zwischenstation wie Make/Zapier oder eben ein eigenes Skript. GitHub Actions übernimmt hier
die Rolle dieser Zwischenstation — technisch dasselbe Ergebnis, ohne zusätzliches
Abo-Tool.

---

## 1. Einmalig: Repository anlegen

1. Neues (privates!) GitHub-Repo erstellen, z. B. `jp-lernende-wochentasks`.
2. Diesen ganzen Ordner (`generate_weekly_tasks.py`, `system_prompt.md`, `requirements.txt`,
   `.github/workflows/weekly-tasks.yml`) hineinkopieren und pushen.

## 2. Einmalig: API-Keys besorgen

| Key | Woher | Wofür |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | Task-Inhalte generieren |
| `MONDAY_API_TOKEN` | monday.com → Avatar (unten links) → **Administration** → **API** → *"Diesen Token generieren/kopieren"* (oder pro User: Avatar → Developers → My Access Tokens) | Tasks auf die Boards schreiben |

In GitHub: Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository
secret** → beide Keys unter genau diesen Namen anlegen.

## 3. Einmalig: Spalten-IDs der Boards herausfinden

Die Tasks brauchen die *internen* Spalten-IDs (nicht die sichtbaren Namen). Am einfachsten über
den monday.com API-Playground:

1. Öffne https://monday.com/developers/v2/try-it-yourself (oder API Playground im
   Entwickler-Menü).
2. Führe diese Query aus (Board-ID von Devin als Beispiel):
   ```graphql
   query {
     boards(ids: [18422835984]) {
       columns { id title type }
     }
   }
   ```
3. Notiere dir die `id` der Spalten für **Beschreibung/Notizen** (meist Typ `long_text` oder
   `text`), **Priorität** (Typ `status` oder `dropdown`), **Fälligkeitsdatum** (Typ `date`) und
   ggf. **Tags** (Typ `tags` oder eigene Spalte).
4. Wiederholen für Amelias Board (`18423457412`).
5. In `generate_weekly_tasks.py` den Abschnitt `COLUMN_MAP` mit den echten IDs füllen (aktuell
   stehen dort nur Platzhalter-Beispiele wie `"description": "long_text"`).

> Falls eine Spalte bei euch anders heisst/fehlt (z. B. keine eigene "Tags"-Spalte) — den
> entsprechenden Eintrag in `COLUMN_MAP` einfach weglassen, das Skript überspringt ihn dann.

## 4. Lokal testen (empfohlen, bevor der erste automatische Lauf passiert)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
export MONDAY_API_TOKEN="eyJ..."

# Nur generieren, NICHTS auf monday.com schreiben:
python generate_weekly_tasks.py --dry-run

# Wenn die Ausgabe passt: echten Lauf inkl. Schreiben testen
python generate_weekly_tasks.py
```

Bei `--dry-run` siehst du Titel, Beschreibung, Fälligkeit, Priorität und Tags für beide Personen
im Terminal, bevor irgendetwas auf monday.com landet.

## 5. Manuell über GitHub testen (ohne auf Montag zu warten)

Im Repo → Tab **Actions** → Workflow "Wochenaufgaben Mediamatiker" → **Run workflow** (Button
oben rechts, dank `workflow_dispatch` im Workflow verfügbar) → einmal auf Zuruf laufen lassen
und das Log prüfen.

## 6. Ab dann: läuft automatisch

Jeden Montag ca. 07:30 (siehe Zeitzonen-Hinweis in `weekly-tasks.yml`) läuft der Job automatisch,
generiert die zwei Tasks und legt sie auf den Boards an. Bei Fehlern (z. B. abgelaufener Token)
bekommt ihr eine E-Mail von GitHub Actions an die Repo-Owner-Adresse.

---

## Was noch manuell nachjustiert werden sollte

- **Rotation "von Hand" korrigieren:** Falls Devin oder Amelia mit einem Thema schneller/
  langsamer vorankommen als die Wochen-Schätzung in `system_prompt.md`, dort die
  Wochenbereiche pro Modul (M1–M6) anpassen.
- **Personen-Zuordnung Person→Column für "Zugewiesen an":** Falls die Boards eine echte
  "Person"-Spalte (Typ `people`) haben, in `COLUMN_MAP` ergänzen und im Skript
  `column_values[col_map["people"]] = {"personsAndTeams": [{"id": <monday_user_id>, "kind": "person"}]}`
  hinzufügen (monday-User-ID einmalig per API `users { id name }` nachschlagen).
- **Qualitätskontrolle:** Empfehlung, in den ersten 2–3 Wochen die generierten Tasks vor
  Sichtbarkeit für die Lernenden kurz von Markus gegenlesen zu lassen (z. B. Board-Status
  "Entwurf" statt "To Do" als Default, bis Vertrauen in die Automatik besteht).

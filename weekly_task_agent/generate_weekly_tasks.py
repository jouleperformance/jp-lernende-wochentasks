#!/usr/bin/env python3
"""
Wochenaufgaben-Agent Mediamatiker (Devin & Amelia) - Joule Performance

Ablauf:
1. Liest system_prompt.md (Bildungsplan-Rotation, JP-Kontext, Ausgabeschema)
2. Ruft die Claude API auf -> erhält 2 fertige Tasks als striktes JSON
3. Schreibt jeden Task via monday.com GraphQL API auf das jeweilige Board

Benoetigte Umgebungsvariablen (als GitHub Actions Secrets oder lokal per `export`):
    ANTHROPIC_API_KEY   -> https://console.anthropic.com
    MONDAY_API_TOKEN    -> monday.com > Avatar > Admin > API (oder pro User im Profil)

Board-/Spalten-IDs muessen einmalig unten in COLUMN_MAP eingetragen werden
(siehe README.md, Abschnitt "Spalten-IDs herausfinden").
"""

import json
import os
import sys
from datetime import date, timedelta

import requests
from anthropic import Anthropic

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# 1) Board- und Spalten-Konfiguration -- einmalig pro Board ausfuellen.
#    Spalten-IDs herausfinden: siehe README.md
# ---------------------------------------------------------------------------
COLUMN_MAP = {
    "18422835984": {  # Devin
        "description": "long_text",     # <- durch echte column id ersetzen
        "priority": "status",           # <- durch echte column id ersetzen
        "due_date": "date",             # <- durch echte column id ersetzen
        "tags": "tags",                 # <- durch echte column id ersetzen (falls vorhanden)
    },
    "18423457412": {  # Amelia
        "description": "long_text",
        "priority": "status",
        "due_date": "date",
        "tags": "tags",
    },
}

MONDAY_API_URL = "https://api.monday.com/v2"


def load_system_prompt() -> str:
    with open(os.path.join(SCRIPT_DIR, "system_prompt.md"), encoding="utf-8") as f:
        return f.read()


def call_claude(system_prompt: str, today: date) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Fehler: ANTHROPIC_API_KEY ist nicht gesetzt.")

    client = Anthropic(api_key=api_key)
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    kw = today.isocalendar().week

    user_message = (
        f"Heutiges Datum: {today.isoformat()} (KW {kw}, {today.strftime('%A')}).\n"
        f"Faelligkeitsdatum fuer beide Tasks: {friday.isoformat()} (Freitag dieser Woche).\n"
        "Generiere jetzt die zwei Wochentasks fuer Devin und Amelia gemaess Schema."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    # Falls das Modell trotz Anweisung Codeblock-Fences liefert, entfernen:
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        sys.exit(f"Fehler: Claude-Antwort war kein gueltiges JSON.\n{e}\n\nRohtext:\n{raw_text}")


def create_monday_item(token: str, task: dict) -> str:
    board_id = task["board_id"]
    col_map = COLUMN_MAP.get(board_id, {})

    column_values = {}
    if "description" in col_map:
        column_values[col_map["description"]] = task["description"]
    if "priority" in col_map:
        column_values[col_map["priority"]] = {"label": task["priority"]}
    if "due_date" in col_map:
        column_values[col_map["due_date"]] = {"date": task["due_date"]}
    if "tags" in col_map and task.get("tags"):
        column_values[col_map["tags"]] = ", ".join(task["tags"])

    query = """
    mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
      create_item (
        board_id: $boardId,
        item_name: $itemName,
        column_values: $columnValues
      ) {
        id
      }
    }
    """
    variables = {
        "boardId": board_id,
        "itemName": task["title"],
        "columnValues": json.dumps(column_values),
    }

    resp = requests.post(
        MONDAY_API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"monday.com API-Fehler fuer {task['person']}: {data['errors']}")
    return data["data"]["create_item"]["id"]


def main():
    dry_run = "--dry-run" in sys.argv

    system_prompt = load_system_prompt()
    today = date.today()

    print(f"[1/3] Generiere Tasks via Claude ({MODEL}) fuer {today.isoformat()} ...")
    result = call_claude(system_prompt, today)
    tasks = result.get("tasks", [])
    if len(tasks) != 2:
        print(f"Warnung: erwartet 2 Tasks, erhalten {len(tasks)}.")

    for t in tasks:
        print(f"\n--- {t['person']} ---")
        print(f"Titel: {t['title']}")
        print(f"Faelligkeit: {t['due_date']}  |  Prioritaet: {t['priority']}  |  Tags: {t.get('tags')}")
        print(f"Beschreibung:\n{t['description']}\n")

    if dry_run:
        print("[dry-run] Kein Schreibzugriff auf monday.com ausgefuehrt.")
        return

    token = os.environ.get("MONDAY_API_TOKEN")
    if not token:
        sys.exit("Fehler: MONDAY_API_TOKEN ist nicht gesetzt.")

    print("[2/3] Schreibe Tasks auf monday.com ...")
    for t in tasks:
        item_id = create_monday_item(token, t)
        print(f"  -> {t['person']}: Item {item_id} auf Board {t['board_id']} erstellt.")

    print("[3/3] Fertig.")


if __name__ == "__main__":
    main()

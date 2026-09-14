#!/usr/bin/env python3
"""
Wochenaufgaben-Agent Mediamatiker (Devin & Amelia) - Joule Performance

Ablauf:
1. Liest system_prompt.md (Bildungsplan-Rotation, JP-Kontext, Ausgabeschema)
2. Ruft die Claude API auf -> erhaelt 2 fertige Tasks als striktes JSON
3. Erstellt je ein Item auf monday.com (create_item) mit Prioritaet/HKB/Deadline
4. Postet die volle Aufgabenbeschreibung als Update/Kommentar auf das Item
   (die Boards haben keine Text-Spalte fuer Beschreibungen)

Benoetigte Umgebungsvariablen:
    ANTHROPIC_API_KEY   -> https://console.anthropic.com
    MONDAY_API_TOKEN    -> monday.com > Avatar > Administration > API
"""

import json
import os
import sys
from datetime import date, timedelta

import requests
from anthropic import Anthropic

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-sonnet-4-6"
MONDAY_API_URL = "https://api.monday.com/v2"

# ---------------------------------------------------------------------------
# Spalten-Konfiguration -- identisch auf beiden Boards (per API verifiziert).
# ---------------------------------------------------------------------------
COLUMNS = {
    "priority": "color_mm5e5xe0",
    "status": "status",
    "hkb": "color_mm5egej3",
    "due_date": "date4",
    "ansprechperson": "person",                 # Pflichtfeld auf beiden Boards
    "assignee": "multiple_person_mm5bmx0z",      # Spalte "Devin" bzw. "Amelia"
}

# monday.com User-IDs (via `query { users { id name email } }` ermittelt)
MARKUS_USER_ID = 104611755  # Praxisbildner, Ansprechperson auf beiden Boards
PERSON_IDS = {
    "Devin": 65052525,
    "Amelia": 112589393,
}

# Exakte Labels der Status-Spalten (monday.com akzeptiert NUR diese Texte)
PRIORITY_LABELS = {
    "tief": "Tief",
    "mittel": "Mittel",
    "hoch": "Hoch",
    "kritisch": "Kritisch ⚠️️",
}
HKB_LABELS = {letter: f"HKB {letter.upper()}" for letter in "abcdef"}
DEFAULT_STATUS_ON_CREATE = "In Bearbeitung"


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
        max_tokens=8000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        sys.exit(f"Fehler: Claude-Antwort war kein gueltiges JSON.\n{e}\n\nRohtext:\n{raw_text}")


def normalize_priority(value: str) -> str:
    key = value.strip().lower().replace(" ⚠️️", "").replace("!", "")
    for k, label in PRIORITY_LABELS.items():
        if key.startswith(k):
            return label
    return PRIORITY_LABELS["hoch"]  # sicherer Default


def normalize_hkb(value: str) -> str:
    letter = value.strip().lower().replace("hkb", "").replace("-", "").strip()[:1]
    return HKB_LABELS.get(letter, HKB_LABELS["a"])


def monday_request(token: str, query: str, variables: dict) -> dict:
    resp = requests.post(
        MONDAY_API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"monday.com API-Fehler: {data['errors']}")
    return data["data"]


CREATE_ITEM_QUERY = """
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

CREATE_UPDATE_QUERY = """
mutation ($itemId: ID!, $body: String!) {
  create_update (item_id: $itemId, body: $body) {
    id
  }
}
"""


def create_monday_task(token: str, task: dict) -> tuple[str, str]:
    person_id = PERSON_IDS.get(task["person"])

    column_values = {
        COLUMNS["priority"]: {"label": normalize_priority(task["priority"])},
        COLUMNS["hkb"]: {"label": normalize_hkb(task["hkb"])},
        COLUMNS["status"]: {"label": DEFAULT_STATUS_ON_CREATE},
        COLUMNS["due_date"]: {"date": task["due_date"]},
        COLUMNS["ansprechperson"]: {
            "personsAndTeams": [{"id": MARKUS_USER_ID, "kind": "person"}]
        },
    }
    if person_id:
        column_values[COLUMNS["assignee"]] = {
            "personsAndTeams": [{"id": person_id, "kind": "person"}]
        }

    item_data = monday_request(
        token,
        CREATE_ITEM_QUERY,
        {
            "boardId": task["board_id"],
            "itemName": task["title"],
            "columnValues": json.dumps(column_values),
        },
    )
    item_id = item_data["create_item"]["id"]

    update_body = task["description"]
    if task.get("links"):
        update_body += "\n\nVerlinkungen/Dokumente:\n" + "\n".join(
            f"- {link}" for link in task["links"]
        )

    update_data = monday_request(
        token, CREATE_UPDATE_QUERY, {"itemId": item_id, "body": update_body}
    )
    update_id = update_data["create_update"]["id"]

    return item_id, update_id


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
        print(
            f"Faelligkeit: {t['due_date']}  |  Prioritaet: {normalize_priority(t['priority'])}"
            f"  |  HKB: {normalize_hkb(t['hkb'])}  |  Status: {DEFAULT_STATUS_ON_CREATE}"
        )
        print(f"Beschreibung (wird als Update gepostet):\n{t['description']}")
        if t.get("links"):
            print("Links: " + ", ".join(t["links"]))

    if dry_run:
        print("\n[dry-run] Kein Schreibzugriff auf monday.com ausgefuehrt.")
        return

    token = os.environ.get("MONDAY_API_TOKEN")
    if not token:
        sys.exit("Fehler: MONDAY_API_TOKEN ist nicht gesetzt.")

    print("\n[2/3] Schreibe Tasks auf monday.com ...")
    for t in tasks:
        item_id, update_id = create_monday_task(token, t)
        print(f"  -> {t['person']}: Item {item_id} (Update {update_id}) auf Board {t['board_id']} erstellt.")

    print("[3/3] Fertig.")


if __name__ == "__main__":
    main()

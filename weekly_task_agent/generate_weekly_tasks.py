#!/usr/bin/env python3
"""
Wochenaufgaben-Agent Mediamatiker (Devin & Amelia) - Joule Performance

Ablauf:
1. Liest system_prompt.md (Bildungsplan-Rotation, JP-Kontext, Ausgabeschema)
2. Laedt die zuletzt vergebenen Aufgabentitel von monday.com (Deduplizierung --
   verhindert, dass dieselbe/eine sehr aehnliche Aufgabe zweimal vergeben wird)
3. Ruft die Claude API auf -> erhaelt 2 fertige Tasks als striktes JSON
4. Erstellt je ein Item auf monday.com (create_item) mit Prioritaet/HKB/Deadline
5. Postet die volle Aufgabenbeschreibung als Update/Kommentar auf das Item
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

BOARD_IDS = ["18422835984", "18423457412"]  # Devin, Amelia
RECENT_ITEMS_LIMIT = 10  # pro Board, fuer die Duplikat-Pruefung

# ---------------------------------------------------------------------------
# Spalten-Konfiguration -- identisch auf beiden Boards (per API verifiziert).
# ---------------------------------------------------------------------------
COLUMNS = {
    "priority": "color_mm5e5xe0",
    "status": "status",
    "hkb": "color_mm5egej3",
    "due_date": "date4",
}

PRIORITY_LABELS = {
    "tief": "Tief",
    "mittel": "Mittel",
    "hoch": "Hoch",
    "kritisch": "Kritisch ⚠️",
}
HKB_LABELS = {letter: f"HKB {letter.upper()}" for letter in "abcdef"}
DEFAULT_STATUS_ON_CREATE = "In Bearbeitung"


def load_system_prompt() -> str:
    with open(os.path.join(SCRIPT_DIR, "system_prompt.md"), encoding="utf-8") as f:
        return f.read()


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


RECENT_ITEMS_QUERY = """
query ($boardId: ID!, $limit: Int!) {
  boards (ids: [$boardId]) {
    items_page (limit: $limit) {
      items {
        name
      }
    }
  }
}
"""


def fetch_recent_task_titles(token: str) -> dict[str, list[str]]:
    """Holt die zuletzt vergebenen Aufgabentitel je Board -- Grundlage fuer
    die Duplikat-Vermeidung. Reihenfolge ist neueste-zuerst (monday.com
    Default-Sortierung)."""
    titles_by_board = {}
    for board_id in BOARD_IDS:
        data = monday_request(
            token, RECENT_ITEMS_QUERY, {"boardId": board_id, "limit": RECENT_ITEMS_LIMIT}
        )
        boards = data.get("boards", [])
        items = boards[0]["items_page"]["items"] if boards else []
        titles_by_board[board_id] = [i["name"] for i in items]
    return titles_by_board


def call_claude(system_prompt: str, today: date, recent_titles: dict[str, list[str]]) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Fehler: ANTHROPIC_API_KEY ist nicht gesetzt.")

    client = Anthropic(api_key=api_key)
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    kw = today.isocalendar().week

    all_recent = recent_titles.get("18422835984", []) + recent_titles.get("18423457412", [])
    recent_block = (
        "BEREITS VERGEBENE AUFGABEN DER LETZTEN WOCHEN (nicht wiederholen, auch nicht "
        "thematisch/inhaltlich sehr aehnlich -- waehle ein klar unterschiedliches Thema "
        "innerhalb des passenden Rotationsmoduls):\n"
        + "\n".join(f"- {t}" for t in all_recent)
        if all_recent
        else "BEREITS VERGEBENE AUFGABEN: keine (erster Lauf oder Boards leer)."
    )

    user_message = (
        f"Heutiges Datum: {today.isoformat()} (KW {kw}, {today.strftime('%A')}).\n"
        f"Faelligkeitsdatum fuer beide Tasks: {friday.isoformat()} (Freitag dieser Woche).\n\n"
        f"{recent_block}\n\n"
        "Generiere jetzt die zwei Wochentasks fuer Devin und Amelia gemaess Schema. "
        "Waehle bewusst ein neues Thema, das sich klar von der obigen Liste unterscheidet, "
        "auch wenn beide Themen technisch im selben Rotationsmodul liegen -- variiere "
        "Aufgabentyp, Zielsetzung oder Blickwinkel."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
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
    key = value.strip().lower().replace(" ⚠️", "").replace("!", "")
    for k, label in PRIORITY_LABELS.items():
        if key.startswith(k):
            return label
    return PRIORITY_LABELS["hoch"]  # sicherer Default


def normalize_hkb(value: str) -> str:
    letter = value.strip().lower().replace("hkb", "").replace("-", "").strip()[:1]
    return HKB_LABELS.get(letter, HKB_LABELS["a"])


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
    column_values = {
        COLUMNS["priority"]: {"label": normalize_priority(task["priority"])},
        COLUMNS["hkb"]: {"label": normalize_hkb(task["hkb"])},
        COLUMNS["status"]: {"label": DEFAULT_STATUS_ON_CREATE},
        COLUMNS["due_date"]: {"date": task["due_date"]},
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

    token = os.environ.get("MONDAY_API_TOKEN")
    if not token and not dry_run:
        sys.exit("Fehler: MONDAY_API_TOKEN ist nicht gesetzt.")

    system_prompt = load_system_prompt()
    today = date.today()

    print("[1/4] Lade zuletzt vergebene Aufgabentitel (Duplikat-Pruefung) ...")
    recent_titles = fetch_recent_task_titles(token) if token else {}
    for board_id, titles in recent_titles.items():
        print(f"  Board {board_id}: {len(titles)} bisherige Titel geladen.")

    print(f"[2/4] Generiere Tasks via Claude ({MODEL}) fuer {today.isoformat()} ...")
    result = call_claude(system_prompt, today, recent_titles)
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

    print("\n[3/4] Schreibe Tasks auf monday.com ...")
    for t in tasks:
        item_id, update_id = create_monday_task(token, t)
        print(f"  -> {t['person']}: Item {item_id} (Update {update_id}) auf Board {t['board_id']} erstellt.")

    print("[4/4] Fertig.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Scrape la fréquentation des piscines d'été de Lyon et met à jour data.json / history.json."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

URL = "https://www.piscines-patinoires.lyon.fr/frequentation-piscine.html"
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"
HISTORY_FILE = ROOT / "history.json"

# Correspondance entre un mot-clé identifiant chaque piscine d'été et sa clé dans le JSON.
POOLS = {
    "cntb": "CNTB",
    "vaise": "Vaise",
    "duchère": "Duchère",
    "duchere": "Duchère",
    "mermoz": "Mermoz",
}


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (piscines-scraper)"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def parse_value(raw: str):
    """Convertit une cellule du tableau en int, ou None si la piscine est fermée ("-")."""
    raw = raw.strip()
    if raw == "-" or raw == "":
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def match_pool_key(name: str):
    lowered = name.lower()
    for keyword, key in POOLS.items():
        if keyword in lowered:
            return key
    return None


def parse_pools(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    results = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            name = cells[0].get_text(strip=True)
            key = match_pool_key(name)
            if key is None:
                continue

            capacite = parse_value(cells[1].get_text())
            frequentation = parse_value(cells[2].get_text())
            places_restantes = parse_value(cells[3].get_text())

            results[key] = {
                "nom": name,
                "capacite_max": capacite,
                "frequentation_reelle": frequentation,
                "places_restantes": places_restantes,
                "ouvert": frequentation is not None,
            }

    return results


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main() -> int:
    try:
        html = fetch_html(URL)
    except Exception as exc:  # réseau, timeout, etc.
        print(f"Erreur lors du téléchargement de la page : {exc}", file=sys.stderr)
        return 1

    pools = parse_pools(html)

    expected_keys = set(POOLS.values())
    missing = expected_keys - set(pools.keys())
    if missing:
        print(f"Attention : piscines non trouvées dans la page : {missing}", file=sys.stderr)

    if not pools:
        print("Aucune piscine trouvée, le tableau a peut-être changé de structure.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    data = {
        "derniere_mise_a_jour": now,
        "piscines": pools,
    }
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    history = load_json(HISTORY_FILE, [])
    history.append({"horodatage": now, "piscines": pools})
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"OK - {len(pools)} piscine(s) mise(s) à jour ({now})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

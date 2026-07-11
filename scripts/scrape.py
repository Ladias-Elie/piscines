#!/usr/bin/env python3
"""Scrape la fréquentation des piscines d'été de Lyon et met à jour data.json / history.json."""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

URL = "https://www.piscines-patinoires.lyon.fr/frequentation-piscine.html"
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"
HISTORY_FILE = ROOT / "history.json"

# Les horodatages sont stockés en UTC, mais affichés à l'heure de Lyon.
FUSEAU_LYON = ZoneInfo("Europe/Paris")

# Nombre de jours précédents comparés au jour courant dans la mini-courbe, et
# tolérance de recherche autour de la même heure (le cron tourne toutes les
# 15 min mais peut avoir un peu de retard).
JOURS_HISTORIQUE = 6
TOLERANCE_MINUTES = 25

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


def _point_from_pool(date_iso: str, pool) -> dict:
    if pool is None:
        return {"date": date_iso, "frequentation_reelle": None, "capacite_max": None, "ouvert": None}
    return {
        "date": date_iso,
        "frequentation_reelle": pool.get("frequentation_reelle"),
        "capacite_max": pool.get("capacite_max"),
        "ouvert": pool.get("ouvert"),
    }


def build_journee_actuelle(history: list, pool_key: str, now: datetime) -> list:
    """Pour une piscine, renvoie tous les relevés du jour courant (hors point actuel),
    triés chronologiquement, pour tracer la courbe de fréquentation de la journée."""

    aujourdhui = now.date()
    points = []
    for entry in history:
        try:
            horodatage = datetime.fromisoformat(entry["horodatage"])
        except (KeyError, ValueError, TypeError):
            continue
        if horodatage.date() != aujourdhui:
            continue
        pool = entry.get("piscines", {}).get(pool_key)
        if pool is None:
            continue
        points.append((horodatage, _point_from_pool_heure(horodatage, pool)))

    points.sort(key=lambda item: item[0])
    return [point for _, point in points]


def _point_from_pool_heure(horodatage: datetime, pool: dict) -> dict:
    return {
        "heure": horodatage.astimezone(FUSEAU_LYON).strftime("%H:%M"),
        "frequentation_reelle": pool.get("frequentation_reelle"),
        "capacite_max": pool.get("capacite_max"),
        "ouvert": pool.get("ouvert"),
    }


def build_historique_7j(history: list, pool_key: str, now: datetime) -> list:
    """Pour une piscine, renvoie les valeurs à la même heure sur les JOURS_HISTORIQUE
    jours précédents, plus le point du jour, pour tracer une mini-courbe de comparaison."""

    par_date = {}
    for entry in history:
        try:
            horodatage = datetime.fromisoformat(entry["horodatage"])
        except (KeyError, ValueError, TypeError):
            continue
        par_date.setdefault(horodatage.date(), []).append((horodatage, entry))

    points = []
    for jours_avant in range(JOURS_HISTORIQUE, 0, -1):
        cible = now - timedelta(days=jours_avant)
        meilleur_pool = None
        meilleur_ecart = None
        for horodatage, entry in par_date.get(cible.date(), []):
            ecart = abs((horodatage - cible).total_seconds())
            if ecart <= TOLERANCE_MINUTES * 60 and (meilleur_ecart is None or ecart < meilleur_ecart):
                pool = entry.get("piscines", {}).get(pool_key)
                if pool is not None:
                    meilleur_pool = pool
                    meilleur_ecart = ecart
        points.append(_point_from_pool(cible.date().isoformat(), meilleur_pool))

    return points


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

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")

    history = load_json(HISTORY_FILE, [])

    piscines_avec_historique = {}
    for key, pool in pools.items():
        historique = build_historique_7j(history, key, now_dt)
        historique.append(_point_from_pool(now_dt.date().isoformat(), pool))

        journee = build_journee_actuelle(history, key, now_dt)
        journee.append(_point_from_pool_heure(now_dt, pool))

        piscines_avec_historique[key] = {
            **pool,
            "historique_7j": historique,
            "journee_actuelle": journee,
        }

    data = {
        "derniere_mise_a_jour": now,
        "piscines": piscines_avec_historique,
    }
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # history.json ne garde que les valeurs brutes mesurées, sans la mini-courbe dérivée.
    history.append({"horodatage": now, "piscines": pools})
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"OK - {len(pools)} piscine(s) mise(s) à jour ({now})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

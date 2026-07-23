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

# Fenêtre glissante (en jours) incluse dans la courbe de tendance affichée
# dans la PWA. Garde data.json compact même après plusieurs semaines de collecte.
FENETRE_TENDANCE_JOURS = 5

# Taille des créneaux (en minutes) pour la courbe de fréquentation moyenne.
TAILLE_CRENEAU_MOYENNE = 15

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


def _point_from_pool_heure(horodatage: datetime, pool: dict) -> dict:
    local = horodatage.astimezone(FUSEAU_LYON)
    return {
        "jour": local.date().isoformat(),
        "heure": local.strftime("%H:%M"),
        "frequentation_reelle": pool.get("frequentation_reelle"),
        "capacite_max": pool.get("capacite_max"),
        "ouvert": pool.get("ouvert"),
    }


def build_tendance(history: list, pool_key: str, now: datetime) -> list:
    """Pour une piscine, renvoie tous les relevés des FENETRE_TENDANCE_JOURS derniers
    jours (hors point actuel), triés chronologiquement, pour tracer la courbe de
    fréquentation dans le temps."""

    limite = now - timedelta(days=FENETRE_TENDANCE_JOURS)
    points = []
    for entry in history:
        try:
            horodatage = datetime.fromisoformat(entry["horodatage"])
        except (KeyError, ValueError, TypeError):
            continue
        if horodatage < limite:
            continue
        pool = entry.get("piscines", {}).get(pool_key)
        if pool is None:
            continue
        points.append((horodatage, _point_from_pool_heure(horodatage, pool)))

    points.sort(key=lambda item: item[0])
    return [point for _, point in points]


def _plus_longue_plage_contigue(points: list) -> list:
    """Renvoie la plus longue série de créneaux consécutifs (sans trou d'un
    créneau à l'autre) parmi les créneaux ayant au moins une observation."""

    meilleure: list = []
    courante: list = []
    for point in points:
        if courante and point["creneau"] - courante[-1]["creneau"] != TAILLE_CRENEAU_MOYENNE:
            courante = []
        courante.append(point)
        if len(courante) > len(meilleure):
            meilleure = courante[:]

    return meilleure


def build_moyenne_jour(history: list, pool_key: str) -> list:
    """Pour une piscine, moyenne la fréquentation par créneau de TAILLE_CRENEAU_MOYENNE
    minutes sur tout l'historique disponible (heure de Lyon), pour tracer un profil
    de journée type comparé à la courbe du jour.

    Certains relevés nocturnes isolés montrent une fréquentation résiduelle non
    nulle (le compteur du site ne semble pas toujours revenir à "-" immédiatement
    à la fermeture). Pour ne garder que la vraie plage d'ouverture, on ne conserve
    que la plus longue série de créneaux consécutifs (sans trou) — un relevé
    nocturne isolé, séparé du reste par plusieurs heures sans aucune donnée,
    forme sa propre mini-série et se retrouve naturellement exclu."""

    par_creneau: dict = {}
    for entry in history:
        try:
            horodatage = datetime.fromisoformat(entry["horodatage"])
        except (KeyError, ValueError, TypeError):
            continue
        pool = entry.get("piscines", {}).get(pool_key)
        if not pool or not pool.get("ouvert") or pool.get("frequentation_reelle") is None:
            continue

        local = horodatage.astimezone(FUSEAU_LYON)
        minutes = local.hour * 60 + local.minute
        creneau = (minutes // TAILLE_CRENEAU_MOYENNE) * TAILLE_CRENEAU_MOYENNE
        par_creneau.setdefault(creneau, []).append(pool["frequentation_reelle"])

    if not par_creneau:
        return []

    points_bruts = []
    for creneau in sorted(par_creneau.keys()):
        valeurs = par_creneau[creneau]
        points_bruts.append({
            "creneau": creneau,
            "heure": f"{creneau // 60:02d}:{creneau % 60:02d}",
            "frequentation_moyenne": round(sum(valeurs) / len(valeurs)),
            "nb_jours": len(valeurs),
        })

    plage = _plus_longue_plage_contigue(points_bruts)

    return [
        {"heure": p["heure"], "frequentation_moyenne": p["frequentation_moyenne"], "nb_jours": p["nb_jours"]}
        for p in plage
    ]


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
        tendance = build_tendance(history, key, now_dt)
        tendance.append(_point_from_pool_heure(now_dt, pool))

        moyenne_jour = build_moyenne_jour(history, key)

        piscines_avec_historique[key] = {**pool, "tendance": tendance, "moyenne_jour": moyenne_jour}

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

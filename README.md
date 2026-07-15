# Piscines Lyon — Fréquentation en direct

Suivi en temps réel de la fréquentation des piscines d'été de Lyon (CNTB,
Vaise, Duchère, Mermoz), à partir des données publiées par
[piscines-patinoires.lyon.fr](https://www.piscines-patinoires.lyon.fr/frequentation-piscine.html).

Le projet est composé de trois parties :

- **`scripts/scrape.py`** — scrape la page officielle, extrait capacité
  maximale / fréquentation réelle / places restantes pour chaque piscine
  d'été, et écrit :
  - `data.json` : état actuel (écrasé à chaque exécution)
  - `history.json` : historique complet, une entrée horodatée ajoutée à
    chaque exécution
- **`.github/workflows/scrape.yml`** — exécute le scraper toutes les 15
  minutes via GitHub Actions et commite les JSON s'ils ont changé
- **`index.html`** — PWA en un seul fichier (HTML/CSS/JS vanilla, sans
  dépendance) qui affiche le CNTB en vedette avec le nombre de personnes
  actuel et un graphique de fréquentation dans le temps, et les 3 autres
  piscines en cartes avec le même graphique en plus petit

Une piscine fermée (valeur `-` sur le site) est représentée avec
`"ouvert": false` et des valeurs `null` pour la fréquentation et les places
restantes.

## Activer GitHub Pages

1. Pousser ce dépôt sur GitHub.
2. Aller dans **Settings → Pages**.
3. Dans **Build and deployment**, choisir **Source : Deploy from a branch**.
4. Sélectionner la branche `main` et le dossier `/ (root)`, puis **Save**.
5. Le site sera disponible après quelques minutes à l'adresse
   `https://<utilisateur>.github.io/<depot>/`.

La PWA lit `data.json` à la racine du même dépôt, donc aucune configuration
supplémentaire n'est nécessaire : Pages sert directement `index.html`,
`manifest.json`, `sw.js`, `data.json` et `history.json`.

## Activer le workflow de scraping

1. Le fichier `.github/workflows/scrape.yml` est déjà configuré pour
   tourner toutes les 15 minutes (`cron: "*/15 * * * *"`).
2. GitHub Actions doit avoir le droit d'écrire sur le dépôt : dans
   **Settings → Actions → General → Workflow permissions**, choisir
   **Read and write permissions**, puis **Save**.
3. Le workflow se déclenche automatiquement dès qu'il est présent sur la
   branche par défaut. Il peut aussi être lancé manuellement depuis l'onglet
   **Actions → Scrape fréquentation piscines → Run workflow**
   (`workflow_dispatch`).
4. Chaque exécution met à jour `data.json` et ajoute une entrée à
   `history.json`, puis commite ces fichiers si leur contenu a changé.

## Lancer le scraper en local

```bash
pip install -r requirements.txt
python scripts/scrape.py
```

## Structure de `data.json`

```json
{
  "derniere_mise_a_jour": "2026-07-15T20:13:29+00:00",
  "piscines": {
    "CNTB": {
      "nom": "Centre Nautique T.Bertrand (CNTB)",
      "capacite_max": 2000,
      "frequentation_reelle": 864,
      "places_restantes": 1136,
      "ouvert": true,
      "tendance": [
        { "jour": "2026-07-11", "heure": "14:03", "frequentation_reelle": 340, "capacite_max": 2000, "ouvert": true },
        { "jour": "2026-07-11", "heure": "20:30", "frequentation_reelle": null, "capacite_max": 2000, "ouvert": false },
        { "jour": "2026-07-15", "heure": "20:13", "frequentation_reelle": 864, "capacite_max": 2000, "ouvert": true }
      ]
    },
    "Vaise": {
      "nom": "Piscine de Vaise",
      "capacite_max": 1100,
      "frequentation_reelle": null,
      "places_restantes": null,
      "ouvert": false,
      "tendance": []
    }
  }
}
```

`tendance` contient tous les relevés des 5 derniers jours (fenêtre glissante
définie par `FENETRE_TENDANCE_JOURS` dans `scripts/scrape.py`), triés
chronologiquement, avec un trou (`frequentation_reelle: null`) pour les
créneaux sans mesure ou piscine fermée. C'est ce champ que la PWA utilise
pour tracer le graphique de fréquentation dans le temps (nuage de points
relié, échelle fixe 0-100 % par rapport à un seuil pratique par piscine,
coupures de ligne sur les périodes de fermeture). La fenêtre glissante
garde `data.json` compact même après plusieurs semaines de collecte —
`history.json`, lui, garde tout l'historique brut sans limite.

Les seuils de couleur (vert/orange/rouge) sont calculés en nombre de
personnes réel, pas en pourcentage de la capacité légale de la piscine
(volontairement très supérieure à ce qui « sent » plein) — voir la
constante `SEUILS` dans `index.html`.

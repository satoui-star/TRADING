# environment.md — Mise en place de l'environnement (étape 1 du framework)

Ce document correspond à l'exigence du PDF (Step 1 + Appendice C) : « A new machine
can be provisioned from documentation; the bootstrap script succeeds; secrets are
not stored in the repository. »

## 1. Prérequis

- **Python 3.13+** (testé 3.13 et 3.14).
- Dépendances :

```
pip install -r requirements.txt
```

`requirements.txt` couvre le cœur analytique (pandas, numpy, scipy, plotly,
streamlit) et, pour la capture live uniquement, `ib_async`.

## 2. Modèle de secrets

Il n'y a **aucun secret** dans le dépôt. La connexion IBKR ne nécessite pas de clé
API : seuls l'hôte, le port et le client id sont configurés, en clair, dans
`config.py` (`IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`). L'authentification est portée
par IB Gateway / TWS (login interactif), hors du code. Si un secret devait être
ajouté un jour, il devrait l'être via une variable d'environnement, jamais en dur.

## 3. Smoke test de connectivité (bootstrap)

Gateway/TWS ouvert et connecté (port `4002` par défaut, cf. `config.py`) :

```
python connexion.py
```

Sortie attendue : transitions d'état `DECONNECTE -> CONNEXION -> CONNECTE`,
l'heure serveur, puis `Sain ? True`. En cas d'échec, l'erreur est **structurée**
(machine à états + `ConnectionError`), pas un crash opaque :
- `client id invalide ...` → corriger `IB_CLIENT_ID`.
- `connexion IBKR impossible ...` après N tentatives → Gateway fermé ou mauvais port.

La session applique seule : reconnexion à backoff exponentiel + jitter, heartbeat,
et refus de se déclarer « sain » tant qu'un aller-retour broker n'a pas réussi.

## 4. Deux modes d'exécution

- **Mode A (live)** : Gateway ouvert → `python daily.py` (capture → snapshot → IV →
  QC → forward → validation → manifeste).
- **Mode B (hors-ligne)** : sur un `data/brut_*.csv` existant, sans `ib_async` :

```
python snapshot.py
python calcul_iv.py
python qc.py
python forward.py
python surface.py
python validation.py
python manifest.py
```

## 5. Où sont écrits les artefacts

- `data/` — prix bruts immuables, datés (`brut_*`).
- `data/calcul/` — analytics recalculables (`market_state_*`, `iv_*`, `qc_*`,
  `forward_*`, `surface_*`, `validation_*`, `triage_*`, `manifest_*`, `*.html`).

## 6. Tests

```
python -m pytest tests/ -q
```

Doit afficher tous les tests au vert (unitaires pricing/forward/risque +
régression sur le brut committé).

# Manuel d'exécution — Plateforme de volatilité ESTX50

*Comment installer, lancer et lire la plateforme. La partie IBKR (connexion + capture des prix) est **facultative** : tout le reste fonctionne hors-ligne sur un fichier de prix bruts déjà présent.*

Ce document correspond à l'étape 16 du framework du prof (« Production hardening, documentation, and handover ») : un opérateur qui n'a pas écrit le code doit pouvoir installer, lancer un calcul, lire le rapport QC et savoir où enquêter en cas d'échec.

---

## 0. Démarrage rapide (TL;DR)

Depuis le dossier du projet (`C:\Users\Utilisateur\Desktop\TRADING`) :

**Mode A — avec IBKR (capture live).** IB Gateway ouvert et connecté (port 4002), puis :

```
python daily.py
```

→ enchaîne capture → IV → QC → forward → validation, et archive un instantané daté.

**Mode B — sans IBKR (sur des prix déjà capturés).** Il suffit qu'un fichier `data\brut_ESTX50_*.csv` existe déjà. On saute la capture et on lance la chaîne analytique :

```
python snapshot.py
python calcul_iv.py
python qc.py
python forward.py
python surface.py
python validation.py
python manifest.py
```

Puis, pour visualiser :

```
streamlit run dashboard.py
```

C'est tout. Le détail de chaque étape est ci-dessous.

---

## 1. Prérequis et installation

- **Python 3.13** (testé avec `Python313`).
- Bibliothèques de calcul et de visualisation :

```
pip install pandas numpy scipy plotly streamlit
```

- **Facultatif — uniquement pour la capture live IBKR** :

```
pip install ib_async
```

  Si `ib_async` n'est pas installé, **seuls** `connexion.py` et `donnees.py` (et donc `daily.py`) sont indisponibles. Tout le reste de la plateforme fonctionne.

Aucune clé API à coller dans le code : les paramètres de connexion (hôte, port 4002, client id) sont dans `config.py`.

---

## 2. Carte du projet

```
TRADING/
├─ config.py          ← TOUS les réglages (seuils, versions, chemins). Le point de vérité.
├─ connexion.py       ← session IBKR fiable : machine à états, reconnexion, heartbeat (étape 1)
├─ donnees.py         ← capture des prix bruts depuis IBKR (IBKR uniquement)
│
├─ snapshot.py        ← market-state : prix de référence + drapeaux d'état (étape 5)
├─ pricing.py         ← Black-Scholes, Greeks, inversion d'IV (Brent)
├─ calcul_iv.py       ← inverse toute la chaîne d'options en IV
├─ qc.py              ← filtre qualité (OTM, prix plancher, IV plausible) + codes de rejet
├─ forward.py         ← forward par parité call-put + dividende implicite (étape 6)
├─ surface.py         ← nappe SVI calibrée + no-arbitrage calendaire (étape 9)
├─ risque.py          ← Greeks monétisés, P&L scénarios, VaR/ES (étapes 11-12)
├─ strategies.py      ← 9 templates de positions (straddle, condor, etc.)
├─ validation.py      ← suite de QA + détection d'anomalie + table de triage (étape 14)
├─ manifest.py        ← manifeste de run : code_version + hashes de config (Appendice B)
│
├─ nappe.py           ← ancienne nappe brute (interpolation griddata) — secondaire
├─ historique.py      ← évolution spot / vol ATM / skew au fil des captures
├─ daily.py           ← orchestrateur : capture → snapshot → IV → QC → forward → validation → manifeste
├─ dashboard.py       ← interface Streamlit, 4 onglets (étape 15)
│
├─ positions.csv      ← portefeuille à risquer (lu par risque.py et le dashboard)
├─ requirements.txt   ← dépendances (ib_async = capture live uniquement)
│
├─ tests/             ← pytest : pricing, forward, risque + régression (Partie IV.E)
├─ docs/              ← environment.md, known_limitations.md, release_checklist.md, modules.md
│
└─ data/                          ← couche BRUTE, immuable, horodatée
   ├─ brut_ESTX50_AAAAMMJJ_HHMMSS.csv
   └─ calcul/                      ← couche ANALYTIQUE, recalculable
      ├─ market_state_ESTX50_*.csv
      ├─ iv_ESTX50_*.csv
      ├─ qc_ESTX50_*.csv
      ├─ forward_*.csv  /  forward_diag_*.csv
      ├─ surface_params_*.csv  /  surface_grid_*.csv  /  surface_points_*.csv
      ├─ validation_*.csv  /  triage_*.csv  /  qc_metrics_history_ESTX50.csv
      ├─ manifest_ESTX50_*.json
      └─ *.html (nappes et smiles ouverts dans le navigateur)
```

Principe central (du PDF) : `data/` contient les **prix bruts immuables** ; `data/calcul/` contient des **analytics recalculables**. On ne réécrit jamais un brut, et on peut tout reconstruire à partir de lui.

---

## 3. Les deux modes d'exécution

### Mode A — avec IBKR (capture live) — *facultatif*

1. Lancer **IB Gateway** (ou TWS) en session paper, le laisser se connecter.
2. Vérifier que le port API est **4002** (le réglage dans `config.py`, `IB_PORT`).
3. Lancer la chaîne complète :

```
python daily.py
```

`daily.py` exécute, dans l'ordre : capture des prix bruts → market-state snapshot → inversion IV → contrôle qualité → forward par parité → suite de validation → manifeste de run. Chaque exécution **ajoute** un instantané daté sans rien écraser.

> Pour tester juste la connexion sans rien capturer : `python connexion.py` (affiche l'état de session).

### Mode B — sans IBKR (sur un brut existant) — *le mode principal pour une démo ou une correction*

On part d'un fichier `data\brut_ESTX50_*.csv` déjà capturé (le vôtre, ou un échantillon fourni). On ne touche ni à `connexion.py`, ni à `donnees.py`, ni à `daily.py`. On lance la chaîne analytique **dans cet ordre** (l'ordre compte : chaque module lit la sortie du précédent) :

```
python snapshot.py      (1) market-state : prix de référence + drapeaux d'état
python calcul_iv.py     (2) inverse les prix du dernier brut en IV
python qc.py            (3) filtre la chaîne et pose les codes de rejet
python forward.py       (4) reconstruit le forward par parité
python surface.py       (5) calibre la nappe SVI + diagnostic calendaire
python validation.py    (6) note la fiabilité du run (pass/warn/fail)
python manifest.py      (7) écrit le manifeste de run (lineage)
```

Puis la visualisation (voir §4 et §5).

> **Pourquoi cet ordre ?** `qc.py` lit le dernier `iv_`, `validation.py` lit le dernier `qc_` + le brut correspondant. Si vous lancez `qc` avant `calcul_iv`, il travaillera sur un vieux `iv_`. En cas de doute, relancez la chaîne dans l'ordre : c'est rapide et déterministe.

---

## 4. Le pipeline en détail (que lit / que produit chaque module)

| Commande | Lit | Produit | Sortie console attendue |
|---|---|---|---|
| `python snapshot.py` | dernier `brut_` | `market_state_*.csv` | répartition des drapeaux (ok / stale / spread large / croisé) |
| `python calcul_iv.py` | dernier `brut_` | `iv_*.csv` | « X/Y options inversées », écart médian vs IBKR, date de valorisation |
| `python qc.py` | dernier `iv_` | `qc_*.csv` | nombre de points gardés / rejetés par code de raison |
| `python forward.py` | dernier `brut_` | `forward_*.csv`, `forward_diag_*.csv` | F parité vs carry par maturité, dividende implicite, confiance |
| `python surface.py` | dernier `qc_` (+ brut) | `surface_params/grid/points_*.csv`, `*.html` | RMSE SVI par tranche, violations calendaires, ouvre 2 graphes |
| `python risque.py` | `positions.csv` + dernier `qc_` | (affichage) | Greeks monétisés, grille de P&L scénarios, VaR/ES |
| `python validation.py` | dernier `qc_` (+ brut, + historique) | `validation_*.csv`, `triage_*.csv`, `qc_metrics_history` | compteurs pass/warn/fail + table de triage |
| `python historique.py` | tous les `qc_` | `historique.html` | évolution spot / vol ATM / skew |
| `python manifest.py` | derniers artefacts | `manifest_*.json` | run_id, code_version, hashes de config, partitions, statut |

Notes utiles :

- **Déterminisme** : la date de valorisation (qui sert à calculer la maturité `T`) est lue dans la colonne `capture_ts` du brut, **pas** l'horloge du jour. Rejouer le même brut un autre jour donne donc exactement les mêmes IV. (Sur de très vieux `qc_` sans `capture_ts`, le forward retombe sur le carry, *labellisé* — relancez la chaîne pour repropager `capture_ts`.)
- **`surface.py` ouvre deux onglets de navigateur** (nappe 3D + smiles). Pour l'en empêcher (ex. exécution automatisée) : sous Windows `set SURFACE_AUTO_OPEN=0` avant de lancer, ou `$env:SURFACE_AUTO_OPEN=0` sous PowerShell.

---

## 5. Le dashboard (interface Streamlit)

```
streamlit run dashboard.py
```

Ouvre une page dans le navigateur (par défaut `http://localhost:8501`). Quatre onglets :

1. **Données** — santé du marché (spot, vol ATM, skew), **nappe SVI 3D calibrée**, smiles points-vs-ajustement, forward par parité.
2. **Risque** — Greeks monétisés du portefeuille (`positions.csv`), grille de P&L sous chocs, VaR/ES, simulateur de stratégies.
3. **Daily** — évolution dans le temps (spot, vol ATM, skew) sur toutes les captures.
4. **Validation** — verdict pass/warn/fail, table de triage colorée par sévérité, métriques du run et leur tendance.

Le dashboard ne capture rien : il lit les fichiers déjà présents dans `data/calcul/`. Il fonctionne donc parfaitement **sans IBKR**, du moment qu'une capture existe.

---

## 6. Risque et portefeuille (`positions.csv`)

`risque.py` et l'onglet Risque lisent `positions.csv`, un CSV de 4 colonnes :

```
echeance,strike,type,quantite
20260717,6250,C,-1
20260717,6250,P,-1
```

- `echeance` : date d'expiration au format `AAAAMMJJ`.
- `type` : `C` (call) ou `P` (put).
- `quantite` : nombre de contrats, **négatif si vendu** (ici un short straddle).

Modifiez ce fichier pour risquer un autre portefeuille, puis relancez `python risque.py` (ou rafraîchissez le dashboard). Un fichier `positions_long.csv` est fourni comme second exemple.

---

## 7. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `ModuleNotFoundError: ib_async` | bibliothèque IBKR absente | Normal en mode B. Ne lancez pas `donnees.py`/`daily.py`. Pour la capture live : `pip install ib_async`. |
| `Aucun brut_...` / `Lance d'abord python donnees.py` | pas de fichier de prix dans `data/` | Placez un `brut_ESTX50_*.csv` dans `data/`, ou faites une capture (mode A). |
| Le dashboard dit « Aucune capture disponible » | pas de `qc_` dans `data/calcul/` | Lancez `python calcul_iv.py` puis `python qc.py`. |
| Forward affiché « repli carry » partout | `qc_` ancien, sans `capture_ts` | Relancez `calcul_iv` → `qc` → `forward` pour repropager `capture_ts`. |
| L'onglet Validation montre des anomalies « non évaluables » | historique trop court | Normal : il faut au moins 4 captures pour juger une anomalie. |
| `surface.py` ouvre des onglets non voulus | ouverture auto des graphes | `set SURFACE_AUTO_OPEN=0` avant de lancer. |
| Connexion IBKR qui échoue | Gateway fermé / mauvais port | Ouvrez IB Gateway, vérifiez `IB_PORT=4002` dans `config.py`. |

Règle d'or : en cas de doute sur l'état, relancez la chaîne `calcul_iv → qc → forward → validation`. Elle est déterministe et ne corrompt rien (chaque sortie est un nouveau fichier daté).

---

## 8. Glossaire des artefacts produits

| Fichier | Couche | Contenu |
|---|---|---|
| `brut_*` | brute | prix bid/ask/close par option, spot, `capture_ts` (immuable) |
| `market_state_*` | analytique | prix de référence par option + spread % + drapeaux d'état |
| `iv_*` | analytique | IV inversée + Greeks par option |
| `qc_*` | analytique | chaîne filtrée + `gardee` + code de rejet |
| `forward_*` / `forward_diag_*` | analytique | forward retenu par maturité + diagnostics par candidat |
| `surface_params_*` / `surface_grid_*` / `surface_points_*` | analytique | paramètres SVI, grille reconstruite, points bruts/acceptés/rejetés |
| `validation_*` / `triage_*` | analytique | tous les checks ; et seulement les warn/fail |
| `qc_metrics_history_ESTX50.csv` | analytique | une ligne par run, pour le suivi de tendance et la baseline d'anomalie |
| `manifest_*.json` | analytique | manifeste de run : code_version, hashes de config, partitions entrée/sortie |

---

## 9. Correspondance modules ↔ étapes du framework (pour la soutenance)

| Étape du prof | Module(s) |
|---|---|
| 1 Accès / connexion (machine à états, reconnexion, heartbeat) | `connexion.py`, `docs/environment.md` |
| 2-3 Univers & capture | `donnees.py` |
| 4 Stockage (brut / analytique) | dossiers `data/` et `data/calcul/` |
| 5 Market-state snapshots | `snapshot.py` |
| 6 Forward & carry implicite | `forward.py` |
| 7 Normalisation & QC | `qc.py` |
| 8 Inversion d'IV | `pricing.py`, `calcul_iv.py` |
| 9 Surface | `surface.py` |
| 10 Pricing | `pricing.py` |
| 11-12 Greeks, risque, scénarios | `risque.py` |
| 13 Reconstruction / historique | `daily.py`, `historique.py` |
| 14 Validation & anomalies | `validation.py` |
| 15 Orchestration & dashboard | `daily.py`, `dashboard.py` |
| 16 Documentation / handover | ce manuel, `docs/` (modules, limites, release) |
| Tests (Partie IV.E) | `tests/` (`pytest`) |
| Manifeste / lineage (Appendice B) | `manifest.py` |

Tout est **strategy-agnostic** : la plateforme **mesure et price** le risque ; `strategies.py` ne fait que décrire des positions, il ne décide aucun trade.

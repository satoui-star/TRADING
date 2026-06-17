# modules.md — Référence des modules (API publique + modes de défaillance)

Le PDF (Appendice C) demande « one README per major package explaining public APIs
and failure modes ». Le dépôt étant mono-fichier par module (mono-repo étudiant),
cette référence consolidée en tient lieu. Chaque entrée : rôle, API publique,
entrées/sorties, modes de défaillance.

## config.py
**Rôle** : point de vérité unique des réglages (versions, IBKR, univers, pricing,
QC, snapshot, forward, surface, scénarios/VaR, validation, chemins).
**API** : constantes uniquement. **Défaillance** : aucune (importé partout).

## connexion.py — étape 1
**Rôle** : cycle de vie de la session IBKR (machine à états, reconnexion
backoff+jitter, heartbeat). **API** : `SessionIBKR` (`.connecter()`, `.battre_coeur()`,
`.age_heartbeat()`, `.est_sain()`, `.reconnecter()`, `.fermer()`), et `ouvrir()`
(compat : renvoie l'objet IB connecté). **Défaillance** : `RuntimeError` si
`ib_async` absent ; `ValueError` si client id invalide ; `ConnectionError` structuré
après `IB_MAX_RETRIES`.

## donnees.py — étapes 2-3
**Rôle** : inventaire (échéances/strikes) + capture des prix bruts par lots.
**API** : `if __name__ == "__main__"` ; fonctions `capter_spot`, `inventaire`,
`capter_une_echeance`, `archiver`. **Sortie** : `data/brut_ESTX50_*.csv`.
**Défaillance** : nécessite `ib_async` + Gateway (Mode A uniquement).

## snapshot.py — étape 5
**Rôle** : market-state snapshot — prix de référence par option (mid/close labellisé)
+ spread % + drapeaux (`ok`/`stale_close_only`/`spread_large`/`croise`/`sans_prix`).
**API** : `construire_snapshot(df_brut)` (pur), `etat_option(bid, ask, close)`,
`main()`. **Sortie** : `market_state_*.csv`. **Défaillance** : `FileNotFoundError`
si aucun brut. **Limite** : `quote_age_seconds` = NaN (capture instantanée).

## pricing.py — étapes 8 & 10
**Rôle** : Black-Scholes, Greeks, inversion d'IV (Brent). **API** : `prix_bs`,
`greeks`, `vol_implicite` (renvoie `(sigma, statut)`, jamais de NaN muet).
**Défaillance** : `vol_implicite` renvoie `(nan, raison)` si prix hors bornes /
sous l'intrinsèque. Module pur, sans I/O.

## calcul_iv.py — étape 8 (chaîne)
**Rôle** : inverse toute la chaîne du dernier brut en IV + Greeks. **API** :
`calculer_iv_chaine(df, jour_reference=None)`, `main()`. **Sortie** : `iv_*.csv`.
**Déterminisme** : valorisation = `capture_ts`. **Défaillance** : `FileNotFoundError`
si aucun brut.

## qc.py — étape 7
**Rôle** : filtre les quotes (OTM, prix plancher, IV plausible) avec code de rejet.
**API** : `appliquer_qc(df, spot)`, `main()`. **Sortie** : `qc_*.csv` (colonnes
`gardee`, `raison_rejet`). **Défaillance** : `FileNotFoundError` si aucun `iv_`.

## forward.py — étape 6
**Rôle** : forward par parité call-put + carry implicite + diagnostics.
**API** : `estimer_forward(sous, spot, T, echeance)`, `construire_courbe(df_brut,
spot, ref)`, `zscore_robuste`, `forward_carry`. **Sortie** : `forward_*.csv`,
`forward_diag_*.csv`. **Défaillance** : repli carry **labellisé** si < `FORWARD_MIN_CANDIDATS`.

## surface.py — étape 9
**Rôle** : nappe SVI par tranche + fallback spline, en log-moneyness / variance
totale, interpolation cross-maturité, diagnostic calendaire. **API** :
`preparer_tranches`, `ajuster_tranche`, `diagnostic_calendaire`, `construire_grille`,
`courbe_forward`, `main()`. **Sortie** : `surface_params/grid/points_*.csv`, `*.html`.
**Défaillance** : tranche `< 3` points → `insuffisant` ; SVI raté ou RMSE trop haut
→ spline.

## risque.py — étapes 11-12
**Rôle** : Greeks monétisés, P&L full-repricing vs Taylor, VaR/ES Monte-Carlo.
**API** : `charger_portefeuille`, `greeks_portefeuille(df, spot)`,
`valeur_portefeuille`, `pnl_scenarios`, `pnl_greeks`, `var_es_montecarlo`.
**Entrée** : `positions.csv` + dernier `qc_`. **Limite** : affichage seul (pas de
partition persistée — cf. known_limitations).

## strategies.py
**Rôle** : 9 templates de positions (straddle, strangle, condor, butterfly, risk
reversal, spreads). **API** : fonctions + `CATALOGUE`. Aucune évaluation (pur).

## validation.py — étape 14
**Rôle** : suite de checks nommés (couverture, santé quotes, convergence, stabilité
forward, résidu parité, RMSE, calendrier, Greeks diff-finies, complétude scénarios),
anomalies vs baseline glissante, table de triage. **API** : `lancer_suite(...)`,
`check_*`, `main()`. **Sortie** : `validation_*.csv`, `triage_*.csv`,
`qc_metrics_history_ESTX50.csv`. **Honnêteté** : checks sans données → `non_evaluable`.

## manifest.py — Appendice B
**Rôle** : manifeste de run (run_id, code_version, hashes de config, partitions
entrée/sortie, statut). **API** : `construire_manifeste`, `config_hashes`, `ecrire`,
`main()`. **Sortie** : `manifest_ESTX50_*.json`.

## daily.py — étapes 13/15
**Rôle** : orchestrateur Mode A — capture → snapshot → IV → QC → forward →
validation → manifeste. Chaque étape isolée (exit code clair en cas d'échec).

## dashboard.py — étape 15
**Rôle** : interface Streamlit (Données / Risque / Daily / Validation). Lit les
artefacts de `data/calcul/`, ne capture rien. **Lancement** : `streamlit run dashboard.py`.

## historique.py / nappe.py
**Rôle** : `historique.py` trace l'évolution spot / vol ATM / skew sur toutes les
captures ; `nappe.py` est l'ancienne nappe brute (interpolation `griddata`),
secondaire, conservée pour comparaison.

## tests/
**Rôle** : `test_pricing` (identités, solveur, Greeks vs diff-finies),
`test_forward` (parité, rejet MAD, repli), `test_risk` (réconciliation agrégats,
grille, VaR reproductible), `test_regression` (chaîne complète sur le brut committé).
**Lancement** : `python -m pytest tests/ -q`.

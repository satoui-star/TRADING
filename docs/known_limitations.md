# known_limitations.md — Limites connues et choix de périmètre assumés

Le PDF (Appendice C) exige ce document : « current compromises, unresolved issues,
planned enhancements ». Chaque point ci-dessous est un choix **conscient**, pas un
oubli, et est exposé honnêtement (principe « Never hide a fallback / missing data »,
Partie XIX du PDF).

## Choix de périmètre (volontaires)

1. **Pricing européen uniquement.** Les options OESX sur l'Euro STOXX 50 sont de
   style **européen** : Black-Scholes/Black-76 est exact. Le pricer américain
   (arbre / Bjerksund-Stensland) du Step 10 n'est donc pas implémenté car inutile
   pour ce sous-jacent. À ajouter si on étend à des options sur actions
   individuelles (style américain).

2. **Capture par snapshot, pas par flux d'événements.** `donnees.py` fait une
   capture instantanée par lots (Step 3 « snapshot »), pas un journal append-only
   d'événements horodatés tick par tick. Conséquences assumées :
   - `quote_age_seconds` n'est pas mesurable (un seul `capture_ts` par chaîne) ;
   - le check `continuite_collecteur` reste **non évaluable** (pas de gaps à mesurer).

3. **Pas de bid/ask sur le sous-jacent.** L'indice est capté en `last`/`close`
   sans fourchette. Le check `sante_quote_sous_jacent` (spread du sous-jacent)
   reste donc **non évaluable**. En revanche, la santé des quotes d'**options**
   (mid/close, spread large, croisé) est, elle, évaluée via `snapshot.py`.

4. **Greeks broker non persistés.** On ne capte que l'IV témoin d'IBKR (`iv_ibkr`),
   pas les `modelGreeks` complets → le check `reconciliation_greeks_broker` reste
   **non évaluable**. Nos Greeks sont validés par différences finies (check interne).

5. **`risque.py` est en affichage seul.** Les Greeks agrégés, la grille de P&L et la
   VaR/ES sont calculés et affichés, mais pas persistés en partitions
   `risk_aggregates` / `scenario_results`. Le manifeste le signale (`note_risque`).

6. **Stockage = CSV datés**, pas de base de métadonnées relationnelle ni de store
   colonne partitionné. Le principe brut-immuable / analytics-recalculable du PDF
   (Step 4) est respecté, mais sans migrations de schéma ni partitionnement
   formel par (date, sous-jacent, couche).

7. **Configuration en un seul `config.py`**, pas en fichiers YAML séparés
   (qc.yaml, scenarios.yaml…). Choix mono-repo étudiant : la **structure** du PDF
   est reproduite par sections, et chaque jeu de seuils est **versionné**
   (`QC_VERSION`, `SCENARIO_VERSION`, …) + hashé dans le manifeste.

8. **Replay déterministe mais pas de backfill par plage de dates.** Rejouer la
   chaîne sur un même brut redonne exactement les mêmes IV (date de valorisation =
   `capture_ts`). Il n'y a pas encore de job de replay sur un **intervalle** de
   dates ni de partitions historiques versionnées (Step 13 partiel).

9. **Orchestration légère.** `daily.py` enchaîne les étapes et `dashboard.py`
   fournit l'observabilité, mais il n'y a pas de planificateur live, d'alerting,
   ni de logs à corrélation d'identifiants (Step 15 partiel).

10. **Détection d'anomalie tardive.** Les checks d'anomalie exigent au moins
    `VALID_HISTO_MIN` (=4) runs passés ; avant cela ils sont **non évaluables**.

## Améliorations futures (par ordre de valeur)

- Pricer américain + inversion associée (si extension aux actions).
- Collecteur streaming append-only (event_id, exchange_ts/receipt_ts) → débloque
  `continuite_collecteur` et `quote_age_seconds`.
- Persistance des sorties de `risque.py` (partitions risk/scenario).
- Backfill par plage de dates + partitions historiques versionnées.
- Migration du stockage vers un store partitionné (parquet) si le volume grandit.

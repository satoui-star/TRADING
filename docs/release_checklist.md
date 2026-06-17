# release_checklist.md — Checklist de release (étape 16 / Partie IV.J du PDF)

Le PDF impose : « Every change that can alter economics must have a release
artifact … Require regression results before promoting a release. » Cette checklist
est à dérouler avant toute mise en service d'une version.

## Catégories de changement (PDF Partie XV)

- **Catégorie A — économique** : `pricing.py`, bornes du solveur, paramétrisation
  de surface, grille de scénarios, seuils de QC/forward. → preuve de régression la
  plus forte + revue explicite.
- **Catégorie B — opérationnel** : seuils de validation, politique d'alerte,
  cadence/ordonnancement.
- **Catégorie C — non économique** : logs, docs, renommages.

Tout changement de Catégorie A **doit** s'accompagner d'un incrément de la version
de config concernée dans `config.py` (`QC_VERSION`, `FORWARD_VERSION`,
`SURFACE_VERSION`, `SCENARIO_VERSION`, `SOLVEUR_VERSION`, `SNAPSHOT_VERSION`) et/ou
de `CODE_VERSION`.

## Avant de promouvoir une release

1. **Versions** : incrémenter `CODE_VERSION` et toute `*_VERSION` de config touchée.
2. **Tests unitaires + régression** :
   ```
   python -m pytest tests/ -q        # doit être 100 % vert
   ```
3. **Rejouer un jour calme** (brut liquide) via la chaîne hors-ligne et vérifier :
   - taux de convergence solveur > 97 % ;
   - forward par parité haute confiance sur les maturités liquides ;
   - **0 violation d'arbitrage calendaire** ;
   - RMSE SVI sous le seuil par tranche.
4. **Rejouer un jour stressé** (liquidité éparse / spreads larges) et vérifier que
   les drapeaux de dégradation et les `warn/fail` de validation se déclenchent
   correctement (pas de faux « tout vert »).
5. **Suite de validation** : `python validation.py` → relever pass/warn/fail et la
   sévérité max ; aucun `fail` non justifié.
6. **Manifeste** : `python manifest.py` → vérifier `statut: success`, et **comparer
   les `config_hashes`** au release précédent (un hash qui change = un seuil a
   bougé : doit être intentionnel et documenté).
7. **Documentation** : mettre à jour `known_limitations.md` si le périmètre change,
   et `MANUEL.md` si une commande change.

## Après mise en service

- Archiver le manifeste de run et le rapport de validation du jour.
- Conserver le brut + les analytics datés (lineage / replay).

## Plan de rollback

Les sorties étant des fichiers datés non écrasés, un rollback consiste à revenir à
la version de code précédente (git) et à re-pointer sur les derniers artefacts
sains. Aucun brut n'est jamais modifié, donc aucune perte de données source.

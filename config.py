# config.py — TOUS les réglages du projet, au même endroit (étape 4 + Partie VII du PDF).
#
# Principe central du PDF (« Configuration philosophy ») :
#   « Configurations are economic inputs. They should never live as scattered
#     constants in notebooks or inside implementation files. »
# -> Donc AUCUN seuil économique en dur dans qc.py / surface.py / risque.py /
#    pricing.py / forward.py / donnees.py : tout est ici, traçable et versionné.
#
# Le PDF découpe la config en fichiers (qc.yaml, scenarios.yaml, pricing.yaml...).
# On garde un seul fichier Python (plus simple pour un mono-repo étudiant) mais
# on en reproduit la STRUCTURE par sections + on VERSIONNE chaque jeu de réglages
# (« universe version, QC threshold version, solver version, scenario-grid version »).

from pathlib import Path

# ===========================================================================
# VERSIONS DE CONFIG  (PDF : versionner chaque jeu de réglages séparément)
# À incrémenter dès qu'on change un seuil : ça trace l'impact économique.
# ===========================================================================
UNIVERS_VERSION   = "u1"   # univers / découverte de contrats
SOLVEUR_VERSION   = "s1"   # bornes + tolérances du solveur d'IV
QC_VERSION        = "q1"   # filtres de qualité des quotes
FORWARD_VERSION   = "f1"   # moteur de forward par parité
SURFACE_VERSION   = "v1"   # calibration SVI / fallback
SCENARIO_VERSION  = "sc1"  # grille de chocs + paramètres VaR

# ===========================================================================
# 1. CONNEXION IBKR  (étape 1)
# ===========================================================================
IB_HOST = "127.0.0.1"
IB_PORT = 4002          # 4002 = IB Gateway (paper) ; 7497 = TWS paper
IB_CLIENT_ID = 1

# ===========================================================================
# 2. UNIVERS / SOUS-JACENT  (étape 2)
# ===========================================================================
SYMBOLE = "ESTX50"      # Euro STOXX 50
BOURSE = "EUREX"
DEVISE = "EUR"
CLASSE_OPTIONS = "OESX" # options mensuelles standard (européennes)
MULTIPLICATEUR = "10"

# --- Paramètres de capture (étape 3, ex-donnees.py) ---
NB_ECHEANCES = 6        # nombre d'échéances captées (les N plus proches)
ATTENTE_MAX = 15        # attente max par échéance (s) pour les prix différés
SEUIL_PRET = 0.80       # échéance "prête" quand ce % de contrats a un prix

# --- Sélection de strikes (étape 2/7) ---
BANDE_STRIKES = 0.05    # on garde les strikes à +/- 5 % du spot
PAS_STRIKE = 25         # on ne garde que les strikes multiples de 25 (les cotés)

# ===========================================================================
# 3. HYPOTHÈSES DE MARCHÉ  (forward de repli + pricing)
# ===========================================================================
TAUX_SANS_RISQUE = 0.03   # r
DIVIDENDE = 0.032         # q (rendement dividende Eurostoxx) — sert au carry de repli

# ===========================================================================
# 4. PRICING / SOLVEUR D'IV  (étapes 8 & 10, ex-pricing.py)
#    PDF (Partie V.2) : bornes + tolérance + nb d'itérations explicites.
# ===========================================================================
SIGMA_MIN = 1e-4        # borne basse de vol (≠ 0 strict pour la stabilité numérique)
SIGMA_MAX = 5.0         # borne haute (assez large pour les marchés stressés)
SOLVEUR_XTOL = 1e-8     # tolérance d'arrêt du solveur (sur sigma)
SOLVEUR_MAX_ITER = 100  # garde-fou d'itérations

# ===========================================================================
# 5. CONTRÔLE QUALITÉ DES QUOTES  (étape 7, ex-qc.py)
# ===========================================================================
QC_PRIX_MIN = 0.50      # sous ce prix, option trop illiquide / bruitée -> rejet
QC_IV_MIN = 0.03        # IV plausible mini sur indice (3 %)
QC_IV_MAX = 1.50        # IV plausible maxi sur indice (150 %)

# ===========================================================================
# 6. MOTEUR DE FORWARD PAR PARITÉ  (étape 6, forward.py)
#    PDF Partie V.1 : bande d'éligibilité, rejet MAD, score de confiance, repli.
# ===========================================================================
FORWARD_BANDE = 0.05        # on n'utilise que les paires C/P à +/- 5 % du spot
FORWARD_MIN_CANDIDATS = 3   # sous ce nombre de paires propres -> repli carry
FORWARD_MAX_ZSCORE = 3.5    # rejet d'un candidat si |z robuste (MAD)| dépasse ça
FORWARD_LARGEUR_ATM = 0.03  # largeur (en % du spot) du poids gaussien autour de l'ATM
FORWARD_N_CONF = 6          # nb de candidats propres visé pour une confiance pleine
FORWARD_CONF_TOL = 0.001    # échelle de dispersion (MAD/spot) pour le score de confiance

# ===========================================================================
# 7. SURFACE DE VOLATILITÉ  (étape 9, ex-surface.py)
# ===========================================================================
SURFACE_MIN_POINTS = 5      # sous ce seuil par tranche : pas de SVI, fallback spline
SURFACE_MAX_RMSE_VOL = 0.02 # RMSE (en vol) au-delà duquel on préfère le fallback
SURFACE_N_GRILLE_K = 50     # nb de points en log-moneyness (grille reconstruite)
SURFACE_N_GRILLE_T = 40     # nb de maturités interpolées (surface 3D)
SURFACE_TOL_CALENDRIER = 1e-6  # tolérance pour la monotonie calendaire

# ===========================================================================
# 8. SCÉNARIOS & VaR  (étape 12, ex-risque.py)
#    PDF : « Version the scenario grid. Do not leave it as a mutable cell. »
# ===========================================================================
CHOCS_SPOT = [-0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10]   # en % du spot
CHOCS_VOL = [-0.05, -0.02, 0.0, 0.02, 0.05]                 # en points de vol absolus

VAR_SIGMA_SPOT_JOUR = 0.012   # écart-type des rendements quotidiens du spot (~1.2 %)
VAR_SIGMA_VOL_JOUR = 0.010    # écart-type du mouvement quotidien de l'IV (~1 pt)
VAR_CORREL_SPOT_VOL = -0.7    # corrélation spot/vol (négative : spot baisse -> vol monte)
VAR_N_SIMULATIONS = 10000     # tirages Monte Carlo
VAR_HORIZON_JOURS = 1         # horizon de la VaR (jours)
VAR_QUANTILE = 0.99           # niveau de confiance

# ===========================================================================
# 9. VALIDATION & DÉTECTION D'ANOMALIE  (étape 14)
#    PDF Appendice D : chaque check renvoie statut + sévérité + valeur +
#    version de seuil + code de raison + contexte. Sévérités 1..4 (Partie VI).
# ===========================================================================
VALIDATION_VERSION = "val1"
VALID_COVERAGE_FAIL = 6        # < ce nb de points OTM par maturité  -> fail
VALID_COVERAGE_WARN = 10       # < ce nb                              -> warn
VALID_CONV_WARN = 0.97         # ratio de convergence solveur sous lequel -> warn
VALID_CONV_FAIL = 0.90         # sous lequel                              -> fail
VALID_RMSE_WARN = 0.02         # RMSE vol de surface au-delà -> warn (= SURFACE_MAX_RMSE_VOL)
VALID_RMSE_FAIL = 0.04         # au-delà -> fail
VALID_FWD_STAB_WARN = 0.001    # |F pondéré - F médian| / spot au-delà -> warn
VALID_FWD_STAB_FAIL = 0.005    # au-delà -> fail
VALID_PARITE_WARN = 0.001      # médiane des |résidus de parité| / spot au-delà -> warn
VALID_PARITE_FAIL = 0.005      # au-delà -> fail
VALID_GREEK_FD_WARN = 1e-3     # erreur relative Greeks analytiques vs diff. finies -> warn
VALID_GREEK_FD_FAIL = 1e-2     # au-delà -> fail
VALID_ANOMALIE_Z = 3.5         # |z robuste (MAD)| au-delà duquel un métrique est anormal
VALID_HISTO_MIN = 4            # nb mini de runs passés pour juger une anomalie


# ===========================================================================
# 10. CHEMINS DE STOCKAGE  (étape 4 : brut immuable vs analytics recalculables)
# ===========================================================================
DOSSIER = Path(__file__).parent
RACINE = DOSSIER                  # alias lu par risque.py / dashboard.py
DATA = DOSSIER / "data"           # prix bruts, datés, immuables
CALCUL = DATA / "calcul"          # résultats recalculables
DATA.mkdir(exist_ok=True)
CALCUL.mkdir(exist_ok=True)

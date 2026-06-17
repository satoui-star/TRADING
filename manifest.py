# manifest.py — Manifeste de run (PDF Appendice B + Partie XV « Lineage »).
#
# Le PDF exige qu'un job écrive UN manifeste par exécution, portant : run_id,
# version de CODE, HASHES de configuration par groupe, partitions d'entrée et de
# sortie, et statut. C'est l'épine dorsale de la traçabilité : « Every job must
# emit a manifest with code version and config hashes. »
#
# Ici, le modèle de stockage est « un CSV daté par run » (étape 4). L'analogue
# d'une partition est donc le FICHIER le plus récent de chaque famille, relié à
# la capture via la colonne capture_ts quand elle existe. Le manifeste est des
# MÉTADONNÉES (pas de l'analytics) : il peut donc s'horodater à l'heure courante
# sans casser le déterminisme des calculs.

import hashlib
import json
from datetime import datetime

import pandas as pd

import config


# ===========================================================================
# Hashes de configuration par groupe économique (traçabilité du PDF)
# ===========================================================================
_GROUPES = {
    "univers":   ["SYMBOLE", "BOURSE", "DEVISE", "CLASSE_OPTIONS", "MULTIPLICATEUR",
                  "NB_ECHEANCES", "BANDE_STRIKES", "PAS_STRIKE", "UNIVERS_VERSION"],
    "marche":    ["TAUX_SANS_RISQUE", "DIVIDENDE"],
    "solveur":   ["SIGMA_MIN", "SIGMA_MAX", "SOLVEUR_XTOL", "SOLVEUR_MAX_ITER",
                  "SOLVEUR_VERSION"],
    "qc":        ["QC_PRIX_MIN", "QC_IV_MIN", "QC_IV_MAX", "QC_VERSION"],
    "snapshot":  ["SNAP_SPREAD_LARGE_PCT", "SNAP_STALE_RATIO_WARN",
                  "SNAP_STALE_RATIO_FAIL", "SNAPSHOT_VERSION"],
    "forward":   ["FORWARD_BANDE", "FORWARD_MIN_CANDIDATS", "FORWARD_MAX_ZSCORE",
                  "FORWARD_LARGEUR_ATM", "FORWARD_N_CONF", "FORWARD_CONF_TOL",
                  "FORWARD_VERSION"],
    "surface":   ["SURFACE_MIN_POINTS", "SURFACE_MAX_RMSE_VOL", "SURFACE_N_GRILLE_K",
                  "SURFACE_N_GRILLE_T", "SURFACE_TOL_CALENDRIER", "SURFACE_VERSION"],
    "scenarios": ["CHOCS_SPOT", "CHOCS_VOL", "VAR_SIGMA_SPOT_JOUR", "VAR_SIGMA_VOL_JOUR",
                  "VAR_CORREL_SPOT_VOL", "VAR_N_SIMULATIONS", "VAR_HORIZON_JOURS",
                  "VAR_QUANTILE", "SCENARIO_VERSION"],
    "validation": ["VALID_COVERAGE_FAIL", "VALID_COVERAGE_WARN", "VALID_CONV_WARN",
                   "VALID_CONV_FAIL", "VALID_RMSE_WARN", "VALID_RMSE_FAIL",
                   "VALID_FWD_STAB_WARN", "VALID_FWD_STAB_FAIL", "VALID_PARITE_WARN",
                   "VALID_PARITE_FAIL", "VALID_GREEK_FD_WARN", "VALID_GREEK_FD_FAIL",
                   "VALID_ANOMALIE_Z", "VALID_HISTO_MIN", "VALIDATION_VERSION"],
}


def _hash(attrs):
    """Hash court et STABLE des valeurs de config d'un groupe (sha1 tronqué)."""
    payload = ";".join(f"{a}={getattr(config, a)}" for a in attrs)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def config_hashes():
    """Un hash par groupe économique : change si (et seulement si) un seuil change."""
    return {groupe: _hash(attrs) for groupe, attrs in _GROUPES.items()}


# ===========================================================================
# Localisation des partitions (dernier fichier de chaque famille)
# ===========================================================================
def _capture_ts_du_fichier(chemin):
    """Lit la colonne capture_ts (1re ligne) si elle existe, sinon None."""
    try:
        tete = pd.read_csv(chemin, nrows=1)
        if "capture_ts" in tete.columns and len(tete):
            return str(tete["capture_ts"].iloc[0])
    except Exception:
        pass
    return None


def _dernier(dossier, motif):
    fichiers = sorted(dossier.glob(motif))
    return fichiers[-1] if fichiers else None


def _partition(dossier, motif):
    """Renvoie {fichier, capture_ts} du dernier fichier d'une famille, ou None."""
    f = _dernier(dossier, motif)
    if f is None:
        return None
    return {"fichier": f.name, "capture_ts": _capture_ts_du_fichier(f)}


def construire_manifeste(environnement="local"):
    """Assemble le manifeste du run le plus récent (toutes familles d'artefacts)."""
    sym = config.SYMBOLE
    brut = _partition(config.DATA, f"brut_{sym}_*.csv")
    capture = brut["capture_ts"] if brut else None

    sorties = {
        "market_state":   _partition(config.CALCUL, f"market_state_{sym}_*.csv"),
        "iv_points":      _partition(config.CALCUL, f"iv_{sym}_*.csv"),
        "qc":             _partition(config.CALCUL, f"qc_{sym}_*.csv"),
        "forward_curve":  _partition(config.CALCUL, f"forward_{sym}_*.csv"),
        "surface_params": _partition(config.CALCUL, "surface_params_*.csv"),
        "surface_grid":   _partition(config.CALCUL, "surface_grid_*.csv"),
        "validation":     _partition(config.CALCUL, f"validation_{sym}_*.csv"),
    }
    manquantes = [k for k, v in sorties.items() if v is None]

    return {
        "run_id": f"{capture or 'inconnu'}_run",
        "genere_le": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "environnement": environnement,
        "code_version": config.CODE_VERSION,
        "capture_ts": capture,
        "config_hashes": config_hashes(),
        "partitions_entree": {"raw_market_events": brut,
                              "positions": "positions.csv"},
        "partitions_sortie": sorties,
        "sorties_manquantes": manquantes,
        "note_risque": ("risque.py est affichage seul (pas de partition persistée) "
                        "— voir known_limitations.md"),
        "statut": "success" if not manquantes else "incomplet",
    }


def ecrire(manifeste=None, environnement="local"):
    """Persiste le manifeste en JSON dans data/calcul/ et le renvoie."""
    manifeste = manifeste or construire_manifeste(environnement)
    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    chemin = config.CALCUL / f"manifest_{config.SYMBOLE}_{horo}.json"
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(manifeste, f, indent=2, ensure_ascii=False)
    return chemin, manifeste


def main():
    chemin, m = ecrire()
    print(f"[manifest] run_id        : {m['run_id']}")
    print(f"[manifest] code_version  : {m['code_version']}")
    print(f"[manifest] capture_ts    : {m['capture_ts']}")
    print(f"[manifest] statut        : {m['statut']}")
    if m["sorties_manquantes"]:
        print(f"[manifest] sorties absentes : {', '.join(m['sorties_manquantes'])}")
    print("[manifest] hashes de config :")
    for groupe, h in m["config_hashes"].items():
        print(f"      {groupe:<11} {h}")
    print(f"[manifest] manifeste écrit : {chemin.name}")


if __name__ == "__main__":
    main()

# validation.py — Étape 14 du framework : SUITE DE VALIDATION + DÉTECTION D'ANOMALIE.
#
# Objectif (PDF Step 14) : « Create the automated controls that determine whether
# each daily analytics run is trustworthy. Validation must be treated as a product,
# not as a last-minute dashboard. Produce actionable flags instead of vague
# 'data looked weird' statements. »
#
# Principe d'implémentation (PDF Appendice D + qc/checks.py) :
#   - une LIBRAIRIE de checks NOMMÉS (pas un gros if monolithique) ;
#   - chaque check renvoie un objet structuré : statut, sévérité, valeur mesurée,
#     version du seuil, CODE DE RAISON stable, et contexte pour investiguer ;
#   - un rapport pass/warn/fail + une TABLE DE TRIAGE (warn/fail uniquement) ;
#   - détection d'anomalie contre une BASELINE GLISSANTE (z robuste MAD) ;
#   - tout est rejouable et déterministe (mêmes fonctions que la prod : on appelle
#     surface.preparer_tranches / forward, pas une logique parallèle qui dériverait).
#
# Sévérités (PDF Partie VI) :
#   1 = collecte/stockage à l'arrêt (intervention immédiate)
#   2 = analytics incomplets ou fail dur sur une maturité suivie (même session)
#   3 = dégradation dans les tolérances mais qui empire (à surveiller)
#   4 = informatif / anomalie à faible risque (log et revue ultérieure)
#
# Honnêteté : les checks qui exigent des champs qu'on ne capture pas encore
# (continuité collecteur, santé quote du sous-jacent, Greeks broker) sont marqués
# NON_EVALUABLE avec le champ manquant nommé — jamais cachés (« Never hide a
# fallback / missing data », PDF Partie XIX).

from dataclasses import dataclass, asdict
from datetime import datetime, date
import numpy as np
import pandas as pd

import config
import pricing
import surface as surf
import forward as fwd


# ===========================================================================
# Objet de résultat (PDF : status, severity, measured value, threshold version,
# reason code, context payload)
# ===========================================================================
@dataclass(frozen=True)
class ResultatCheck:
    check: str          # nom du check
    cible: str          # target_key : "global" ou une échéance
    statut: str         # pass / warn / fail / non_evaluable
    severite: int       # 1..4
    valeur: float       # valeur mesurée (NaN si non évaluable)
    seuil: float        # seuil appliqué (NaN si sans objet)
    version_seuil: str  # version du jeu de seuils (traçabilité)
    code_raison: str    # code stable, ex. COVERAGE_FAIBLE
    contexte: str       # de quoi investiguer en un coup d'œil


def _r(check, cible, statut, severite, valeur, seuil, code, contexte):
    return ResultatCheck(check, str(cible), statut, severite,
                         float(valeur) if valeur is not None else float("nan"),
                         float(seuil) if seuil is not None else float("nan"),
                         config.VALIDATION_VERSION, code, contexte)


# ===========================================================================
# Localisation des artefacts de la MÊME capture (déterminisme)
# ===========================================================================
def _capture_ts(df):
    if "capture_ts" in df.columns and len(df):
        return str(df["capture_ts"].iloc[0])
    return ""


def _date_capture(df, repli=None):
    cap = _capture_ts(df)
    if cap:
        try:
            return datetime.strptime(cap.split("_")[0], "%Y%m%d").date()
        except ValueError:
            pass
    return repli or date.today()


def _dernier(prefixe):
    fichiers = sorted(config.CALCUL.glob(f"{prefixe}_{config.SYMBOLE}_*.csv"))
    return fichiers[-1] if fichiers else None


def _fichier_pour_capture(prefixe, capture_ts, dossier=None):
    """Trouve le fichier `prefixe_*` dont la colonne capture_ts == capture_ts.
    À défaut, le plus récent. Renvoie (DataFrame | None)."""
    dossier = dossier or config.CALCUL
    fichiers = sorted(dossier.glob(f"{prefixe}_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        return None
    if capture_ts:
        for f in reversed(fichiers):
            try:
                d = pd.read_csv(f)
            except Exception:
                continue
            if "capture_ts" in d.columns and str(d["capture_ts"].iloc[0]) == capture_ts:
                return d
    try:
        return pd.read_csv(fichiers[-1])
    except Exception:
        return None


# ===========================================================================
# CHECKS — chacun nommé, isolé, avec code de raison et contexte
# ===========================================================================
def check_couverture(qc, ref):
    """Couverture : nombre de points OTM retenus par maturité (calls + puts).
    PDF : 'minimum count of eligible calls and puts per monitored maturity'."""
    res = []
    gardes = qc[qc["gardee"]] if "gardee" in qc.columns else qc
    for ech, sous in gardes.groupby("echeance"):
        n = len(sous)
        nc = int((sous["type"] == "C").sum()) if "type" in sous.columns else -1
        npu = int((sous["type"] == "P").sum()) if "type" in sous.columns else -1
        ctx = f"{n} points ({nc} calls / {npu} puts)"
        if n < config.VALID_COVERAGE_FAIL:
            res.append(_r("couverture", int(ech), "fail", 2, n,
                          config.VALID_COVERAGE_FAIL, "COVERAGE_INSUFFISANTE",
                          ctx + " — surface non fiable sur cette maturité"))
        elif n < config.VALID_COVERAGE_WARN:
            res.append(_r("couverture", int(ech), "warn", 3, n,
                          config.VALID_COVERAGE_WARN, "COVERAGE_FAIBLE", ctx))
        else:
            res.append(_r("couverture", int(ech), "pass", 4, n,
                          config.VALID_COVERAGE_WARN, "COVERAGE_OK", ctx))
    return res


def check_convergence_solveur(brut, spot, ref):
    """Convergence du solveur : fraction des options OTM éligibles inversées.
    Recalculé sur le MÊME chemin de code (pricing.vol_implicite) que la prod."""
    if brut is None:
        return [_r("convergence_solveur", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "brut introuvable pour cette capture")]
    r, q = config.TAUX_SANS_RISQUE, config.DIVIDENDE
    eligible = ok = 0
    for _, g in brut.groupby(["echeance", "strike", "type"]):
        l = g.iloc[0]
        K = float(l["strike"]); typ = l["type"]
        # même règle OTM que le QC : put sous le spot, call au-dessus
        if (typ == "P" and K >= spot) or (typ == "C" and K < spot):
            continue
        mid, _ = fwd._mid(l.get("bid"), l.get("ask"), l.get("close"))
        if mid is None or mid < config.QC_PRIX_MIN:
            continue
        T = fwd._maturite_annees(l["echeance"], ref)
        if T <= 0:
            continue
        eligible += 1
        sigma, statut = pricing.vol_implicite(mid, spot, K, T, r, q,
                                              1 if typ == "C" else -1)
        if np.isfinite(sigma):
            ok += 1
    if eligible == 0:
        return [_r("convergence_solveur", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "aucune option éligible")]
    ratio = ok / eligible
    ctx = f"{ok}/{eligible} inversées"
    if ratio < config.VALID_CONV_FAIL:
        return [_r("convergence_solveur", "global", "fail", 2, ratio,
                   config.VALID_CONV_FAIL, "CONV_EFFONDREE", ctx)]
    if ratio < config.VALID_CONV_WARN:
        return [_r("convergence_solveur", "global", "warn", 3, ratio,
                   config.VALID_CONV_WARN, "CONV_BASSE", ctx)]
    return [_r("convergence_solveur", "global", "pass", 4, ratio,
               config.VALID_CONV_WARN, "CONV_OK", ctx)]


def check_stabilite_forward(courbe_fwd, spot):
    """Stabilité du forward : écart |F pondéré - F médian| rapporté au spot."""
    res = []
    if not courbe_fwd:
        return [_r("stabilite_forward", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "courbe de forward indisponible")]
    for ech, rr in sorted(courbe_fwd.items(), key=lambda kv: kv[1].T):
        if rr.methode != "parite":
            res.append(_r("stabilite_forward", ech, "warn", 3, None, None,
                          "FWD_REPLI_CARRY",
                          f"forward en repli carry (conf {rr.confiance:.2f})"))
            continue
        ecart = abs(rr.forward - rr.forward_median) / spot
        ctx = f"F={rr.forward:.2f} médian={rr.forward_median:.2f} conf={rr.confiance:.2f}"
        if ecart > config.VALID_FWD_STAB_FAIL:
            res.append(_r("stabilite_forward", ech, "fail", 2, ecart,
                          config.VALID_FWD_STAB_FAIL, "FWD_INSTABLE", ctx))
        elif ecart > config.VALID_FWD_STAB_WARN:
            res.append(_r("stabilite_forward", ech, "warn", 3, ecart,
                          config.VALID_FWD_STAB_WARN, "FWD_PEU_STABLE", ctx))
        else:
            res.append(_r("stabilite_forward", ech, "pass", 4, ecart,
                          config.VALID_FWD_STAB_WARN, "FWD_OK", ctx))
    return res


def check_residu_parite(courbe_fwd, spot):
    """Résidu de parité : médiane des |F_i - F retenu| sur les candidats retenus."""
    res = []
    if not courbe_fwd:
        return [_r("residu_parite", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "courbe de forward indisponible")]
    for ech, rr in sorted(courbe_fwd.items(), key=lambda kv: kv[1].T):
        retenus = [c for c in rr.candidats if c.retenu]
        if not retenus:
            res.append(_r("residu_parite", ech, "non_evaluable", 4, None, None,
                          "NON_EVALUABLE_DONNEES", "aucun candidat retenu"))
            continue
        resid = np.median([abs(c.forward_estime - rr.forward) for c in retenus]) / spot
        ctx = f"{len(retenus)} candidats, résidu médian {resid*spot:.2f} pts"
        if resid > config.VALID_PARITE_FAIL:
            res.append(_r("residu_parite", ech, "fail", 2, resid,
                          config.VALID_PARITE_FAIL, "PARITE_HORS_TOL", ctx))
        elif resid > config.VALID_PARITE_WARN:
            res.append(_r("residu_parite", ech, "warn", 3, resid,
                          config.VALID_PARITE_WARN, "PARITE_LARGE", ctx))
        else:
            res.append(_r("residu_parite", ech, "pass", 4, resid,
                          config.VALID_PARITE_WARN, "PARITE_OK", ctx))
    return res


def check_rmse_surface(tranches):
    """Erreur d'ajustement de surface : RMSE (en vol) par maturité."""
    res = []
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    if not cal:
        return [_r("rmse_surface", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "aucune tranche calibrée")]
    for t in sorted(cal, key=lambda x: x["T"]):
        rmse = t.get("rmse_vol", float("nan"))
        ctx = f"{t['methode']} sur {t['n']} pts, RMSE {rmse:.4f}"
        if not np.isfinite(rmse):
            res.append(_r("rmse_surface", int(t["echeance"]), "non_evaluable", 4,
                          None, None, "NON_EVALUABLE_DONNEES", ctx))
        elif rmse > config.VALID_RMSE_FAIL:
            res.append(_r("rmse_surface", int(t["echeance"]), "fail", 2, rmse,
                          config.VALID_RMSE_FAIL, "RMSE_TROP_HAUT", ctx))
        elif rmse > config.VALID_RMSE_WARN:
            res.append(_r("rmse_surface", int(t["echeance"]), "warn", 3, rmse,
                          config.VALID_RMSE_WARN, "RMSE_HAUT", ctx))
        else:
            res.append(_r("rmse_surface", int(t["echeance"]), "pass", 4, rmse,
                          config.VALID_RMSE_WARN, "RMSE_OK", ctx))
    return res


def check_calendrier(tranches):
    """No-arbitrage calendaire : la variance totale ne doit pas reculer avec T."""
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    if len(cal) < 2:
        return [_r("calendrier", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "moins de 2 maturités calibrées")]
    kk = np.linspace(min(t["k"].min() for t in cal),
                     max(t["k"].max() for t in cal), config.SURFACE_N_GRILLE_K)
    diag = surf.diagnostic_calendaire(cal, kk)
    pct = diag["pct"]
    ctx = f"{diag['violations']}/{diag['total']} points en recul de variance"
    if diag["violations"] > 0:
        sev = 2 if pct > 1.0 else 3
        return [_r("calendrier", "global", "fail" if pct > 1.0 else "warn", sev,
                   pct, 0.0, "CAL_VIOLATION", ctx)]
    return [_r("calendrier", "global", "pass", 4, 0.0, 0.0, "CAL_OK", ctx)]


def check_greeks_diff_finies(tranches, spot, ref):
    """Sanité des Greeks : analytiques vs différences finies sur un contrat ATM.
    PDF : 'finite-difference and analytic Greeks agree within tolerance'. C'était
    le test d'acceptation manquant signalé à l'audit."""
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    if not cal:
        return [_r("greeks_diff_finies", "global", "non_evaluable", 4, None, None,
                   "NON_EVALUABLE_DONNEES", "aucune tranche calibrée")]
    # tranche la plus fournie, vol ATM via le SVI calibré
    t = max(cal, key=lambda x: x["n"])
    F, T = t["forward"], t["T"]
    K = float(min(t["strikes_obs"], key=lambda s: abs(s - F)))  # strike ~ ATM
    sigma = float(np.sqrt(max(surf.evaluer_tranche(t, np.array([0.0]))[0], 1e-9) / T))
    r, q = config.TAUX_SANS_RISQUE, config.DIVIDENDE

    g = pricing.greeks(spot, K, T, r, q, sigma, 1)
    h = spot * 1e-4
    p_up = pricing.prix_bs(spot + h, K, T, r, q, sigma, 1)
    p_dn = pricing.prix_bs(spot - h, K, T, r, q, sigma, 1)
    p_0 = pricing.prix_bs(spot, K, T, r, q, sigma, 1)
    delta_fd = (p_up - p_dn) / (2 * h)
    gamma_fd = (p_up - 2 * p_0 + p_dn) / (h * h)
    dv = 0.005
    vega_fd = (pricing.prix_bs(spot, K, T, r, q, sigma + dv, 1)
               - pricing.prix_bs(spot, K, T, r, q, sigma - dv, 1)) / (2 * dv) / 100.0

    def err(a, b):
        return abs(a - b) / (abs(b) + 1e-9)
    e = {"delta": err(g["delta"], delta_fd),
         "gamma": err(g["gamma"], gamma_fd),
         "vega": err(g["vega"], vega_fd)}
    pire = max(e, key=e.get)
    val = e[pire]
    ctx = (f"ATM {int(t['echeance'])} K={K:.0f} σ={sigma:.3f} | "
           f"err δ={e['delta']:.1e} γ={e['gamma']:.1e} ν={e['vega']:.1e}")
    if val > config.VALID_GREEK_FD_FAIL:
        return [_r("greeks_diff_finies", f"ATM_{int(t['echeance'])}", "fail", 2, val,
                   config.VALID_GREEK_FD_FAIL, "GREEK_DERIVE", ctx)]
    if val > config.VALID_GREEK_FD_WARN:
        return [_r("greeks_diff_finies", f"ATM_{int(t['echeance'])}", "warn", 3, val,
                   config.VALID_GREEK_FD_WARN, "GREEK_ECART", ctx)]
    return [_r("greeks_diff_finies", f"ATM_{int(t['echeance'])}", "pass", 4, val,
               config.VALID_GREEK_FD_WARN, "GREEK_OK", ctx)]


def check_completude_scenarios():
    """Complétude des scénarios : la grille de chocs est-elle définie et versionnée ?
    (PDF : 'all configured scenarios executed and stored with no missing results'.)"""
    n = len(config.CHOCS_SPOT) * len(config.CHOCS_VOL)
    ctx = (f"grille {len(config.CHOCS_SPOT)}×{len(config.CHOCS_VOL)} = {n} cellules "
           f"(version {config.SCENARIO_VERSION})")
    if n == 0:
        return [_r("completude_scenarios", "global", "fail", 2, 0, 1,
                   "SCENARIO_GRILLE_VIDE", "aucun choc défini")]
    return [_r("completude_scenarios", "global", "pass", 4, n, n,
               "SCENARIO_GRILLE_OK", ctx)]


def checks_non_evaluables():
    """Checks de l'Appendice D qui exigent des champs qu'on ne capture pas encore.
    On les expose explicitement (jamais cachés) pour que l'opérateur sache quoi câbler."""
    return [
        _r("continuite_collecteur", "global", "non_evaluable", 4, None, None,
           "NON_EVALUABLE_DONNEES",
           "pas de journal d'événements horodaté (Step 3) — gaps non mesurables"),
        _r("sante_quote_sous_jacent", "global", "non_evaluable", 4, None, None,
           "NON_EVALUABLE_DONNEES",
           "spread%/âge du quote sous-jacent non capturés au snapshot (Step 5)"),
        _r("reconciliation_greeks_broker", "global", "non_evaluable", 4, None, None,
           "NON_EVALUABLE_DONNEES",
           "Greeks broker (modelGreeks) non capturés — réconciliation impossible"),
    ]


# ===========================================================================
# Détection d'anomalie contre une baseline glissante (z robuste MAD)
# ===========================================================================
def _z_robuste(x, serie):
    """z = 0.6745 (x - médiane) / MAD sur l'historique (Éq. 24).
    Cas dégénéré : si MAD = 0 (baseline parfaitement stable), toute déviation
    par rapport à la médiane est anormale -> z infini ; sinon z = 0."""
    serie = np.asarray(serie, dtype=float)
    med = np.median(serie)
    mad = np.median(np.abs(serie - med))
    if mad <= 1e-12:
        return 0.0 if abs(x - med) <= 1e-9 else np.inf
    return 0.6745 * (x - med) / mad


def checks_anomalies(metriques, histo):
    """Compare les métriques clés du run à la distribution des runs passés."""
    res = []
    surveilles = [("n_points", "ANOMALIE_COUVERTURE", "nb de points retenus"),
                  ("taux_convergence", "ANOMALIE_CONVERGENCE", "ratio de convergence"),
                  ("rmse_moyen", "ANOMALIE_RMSE", "RMSE surface moyen")]
    for cle, code, libelle in surveilles:
        passe = histo[cle].dropna().tolist() if (histo is not None and cle in histo) else []
        if len(passe) < config.VALID_HISTO_MIN:
            res.append(_r("anomalie_" + cle, "global", "non_evaluable", 4,
                          metriques.get(cle), None, "HISTO_INSUFFISANT",
                          f"{len(passe)} runs passés (min {config.VALID_HISTO_MIN}) — {libelle}"))
            continue
        z = _z_robuste(metriques[cle], passe)
        ctx = f"{libelle}={metriques[cle]:.4g} | z robuste={z:+.2f} vs {len(passe)} runs"
        if abs(z) > config.VALID_ANOMALIE_Z:
            res.append(_r("anomalie_" + cle, "global", "warn", 3, abs(z),
                          config.VALID_ANOMALIE_Z, code, ctx))
        else:
            res.append(_r("anomalie_" + cle, "global", "pass", 4, abs(z),
                          config.VALID_ANOMALIE_Z, "ANOMALIE_RAS", ctx))
    return res


# ===========================================================================
# Orchestration de la suite
# ===========================================================================
def lancer_suite(qc, brut, spot, ref, courbe_fwd, tranches, histo):
    """Exécute tous les checks et renvoie la liste de ResultatCheck + métriques."""
    res = []
    res += check_couverture(qc, ref)
    res += check_convergence_solveur(brut, spot, ref)
    res += check_stabilite_forward(courbe_fwd, spot)
    res += check_residu_parite(courbe_fwd, spot)
    res += check_rmse_surface(tranches)
    res += check_calendrier(tranches)
    res += check_greeks_diff_finies(tranches, spot, ref)
    res += check_completude_scenarios()
    res += checks_non_evaluables()

    # Métriques agrégées du run (pour l'historique + l'anomalie)
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    conv = next((c.valeur for c in res
                 if c.check == "convergence_solveur" and np.isfinite(c.valeur)), float("nan"))
    metriques = {
        "n_points": int((qc["gardee"]).sum()) if "gardee" in qc.columns else len(qc),
        "taux_convergence": conv,
        "rmse_moyen": float(np.nanmean([t.get("rmse_vol", np.nan) for t in cal])) if cal else float("nan"),
        "forward_conf_moyen": float(np.mean([r.confiance for r in courbe_fwd.values()])) if courbe_fwd else float("nan"),
    }
    res += checks_anomalies(metriques, histo)
    return res, metriques


# ===========================================================================
# Entrée principale : lit les artefacts, valide, persiste rapport + triage
# ===========================================================================
def main():
    qc_path = _dernier("qc")
    if qc_path is None:
        print("[validation] aucun fichier qc_. Lance d'abord : python daily.py")
        return
    print(f"[validation] lecture du QC : {qc_path.name}")
    qc = pd.read_csv(qc_path)
    cap = _capture_ts(qc)
    ref = _date_capture(qc)
    spot = float(qc["spot"].iloc[0]) if "spot" in qc.columns else None
    print(f"[validation] capture {cap or '(inconnue)'} | date de valorisation {ref} | spot {spot}")

    # Brut de la MÊME capture (le nom du brut EST le capture_ts)
    brut = None
    if cap:
        bp = config.DATA / f"brut_{config.SYMBOLE}_{cap}.csv"
        if bp.exists():
            brut = pd.read_csv(bp)
    if spot is None and brut is not None:
        spot = float(brut["spot"].iloc[0])

    # Mêmes fonctions que la prod (chemin de code unique) : forward + tranches SVI
    courbe_fwd = surf.courbe_forward(qc, spot, ref) if spot is not None else {}
    tranches, _ = (surf.preparer_tranches(qc, spot, ref=ref, courbe_fwd=courbe_fwd)
                   if spot is not None else ([], None))

    # Historique des métriques (baseline glissante), hors run courant
    histo_path = config.CALCUL / f"qc_metrics_history_{config.SYMBOLE}.csv"
    histo = pd.read_csv(histo_path) if histo_path.exists() else None
    if histo is not None and cap and "capture_ts" in histo.columns:
        histo = histo[histo["capture_ts"].astype(str) != cap]

    res, metriques = lancer_suite(qc, brut, spot, ref, courbe_fwd, tranches, histo)

    rapport = pd.DataFrame([asdict(c) for c in res])
    n_pass = int((rapport["statut"] == "pass").sum())
    n_warn = int((rapport["statut"] == "warn").sum())
    n_fail = int((rapport["statut"] == "fail").sum())
    n_ne = int((rapport["statut"] == "non_evaluable").sum())
    sev_max = int(rapport.loc[rapport["statut"].isin(["warn", "fail"]), "severite"].min()) \
        if (n_warn + n_fail) else 4

    # --- Rapport console (spécifique, pas de bannière vague) ---
    print(f"\n=== Suite de validation (étape 14) — version {config.VALIDATION_VERSION} ===")
    print(f"  {n_pass} pass · {n_warn} warn · {n_fail} fail · {n_ne} non évaluables")
    print(f"  sévérité la plus haute déclenchée : {sev_max} "
          f"(1=critique … 4=info)\n")
    triage = rapport[rapport["statut"].isin(["warn", "fail"])].sort_values("severite")
    if len(triage):
        print("  À investiguer (table de triage) :")
        for _, l in triage.iterrows():
            print(f"   [S{int(l['severite'])}] {l['statut'].upper():4s} {l['check']}"
                  f"({l['cible']}) — {l['code_raison']} : {l['contexte']}")
    else:
        print("  Aucun warn/fail : run jugé fiable par les checks évaluables.")
    if n_ne:
        manquants = rapport[rapport["statut"] == "non_evaluable"]["check"].tolist()
        print(f"\n  Non évaluables (données à câbler) : {', '.join(manquants)}")

    # --- Persistance : rapport complet + table de triage ---
    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    rapport.insert(0, "capture_ts", cap)
    f_rapport = config.CALCUL / f"validation_{config.SYMBOLE}_{horo}.csv"
    f_triage = config.CALCUL / f"triage_{config.SYMBOLE}_{horo}.csv"
    rapport.to_csv(f_rapport, index=False)
    triage.to_csv(f_triage, index=False)
    print(f"\n[validation] rapport complet : {f_rapport.name}")
    print(f"[validation] table de triage : {f_triage.name}")

    # --- Historique des métriques (trend monitoring + baseline anomalie) ---
    ligne = {"capture_ts": cap, "run_ts": horo, **metriques,
             "n_warn": n_warn, "n_fail": n_fail, "severite_max": sev_max}
    if histo_path.exists():
        h = pd.read_csv(histo_path)
        h = h[h["capture_ts"].astype(str) != cap]  # idempotent : remplace si re-run
        h = pd.concat([h, pd.DataFrame([ligne])], ignore_index=True)
    else:
        h = pd.DataFrame([ligne])
    h.to_csv(histo_path, index=False)
    print(f"[validation] historique mis à jour : {histo_path.name} ({len(h)} runs)")

    return sev_max


if __name__ == "__main__":
    main()

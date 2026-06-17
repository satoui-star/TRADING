# surface.py — Étape 9 du framework, version SCRUPULEUSEMENT conforme au PDF.
#
# Le PDF (étape 9 + Partie V.3 « Surface-calibration guidance ») demande :
#   - une représentation PARAMÉTRIQUE : SVI par tranche de maturité (Éq. 20) ;
#   - un FALLBACK non-paramétrique : spline en variance totale, pour les tranches
#     trop pauvres en points ou que SVI cale mal ;
#   - de travailler en LOG-MONEYNESS k = ln(K/F) (Éq. 6) et en VARIANCE TOTALE
#     w = sigma^2 * T (Éq. 7) — jamais en strike brut ni en vol brute ;
#   - de calibrer tranche par tranche, PUIS d'interpoler entre maturités
#     EN VARIANCE (Éq. 22) ;
#   - des DIAGNOSTICS de non-arbitrage : monotonie calendaire (Éq. 21) et
#     pathologies cross-strike (variance négative) ;
#   - de PERSISTER les points bruts/acceptés/rejetés + les paramètres ajustés +
#     la grille reconstruite, et de BORNER les paramètres en loggant les bornes
#     atteintes ;
#   - un tracé comparant les points bruts aux tranches ajustées (revue opérateur).
#
# Ce module ne se connecte à rien : il consomme le dernier qc_*.csv et émet
# paramètres, grille et diagnostics. Le forward est reconstruit par le carry
# F = S*e^((r-q)T) (Éq. 3), faute d'accès aux futures sur le compte paper.

import os
from datetime import datetime, date
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from scipy.optimize import least_squares
from scipy.interpolate import UnivariateSpline

import config
import forward as fwd   # moteur de forward par parité (étape 6)

# --- Réglages de surface : centralisés dans config.py (Partie VII du PDF) ---
MIN_POINTS_PAR_TRANCHE = config.SURFACE_MIN_POINTS   # sous ce seuil : fallback spline
MAX_RMSE_VOL = config.SURFACE_MAX_RMSE_VOL           # RMSE vol au-delà -> fallback
N_GRILLE_K = config.SURFACE_N_GRILLE_K               # nb points log-moneyness (grille)
N_GRILLE_T = config.SURFACE_N_GRILLE_T               # nb maturités interpolées (3D)
TOL_CALENDRIER = config.SURFACE_TOL_CALENDRIER       # tolérance monotonie calendaire


# ===========================================================================
# 1. Utilitaires : fichier, maturité, forward, spot
# ===========================================================================
def _dernier_fichier_qc():
    fichiers = sorted(config.CALCUL.glob(f"qc_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError(
            "Aucun fichier qc_ dans data/calcul/. Lance d'abord : python qc.py"
        )
    return fichiers[-1]


def _date_capture(df, repli=None):
    """Date de valorisation = jour de CAPTURE (déterminisme), lue dans 'capture_ts'.
    Repli explicite sur aujourd'hui si la colonne manque (vieux fichiers)."""
    if "capture_ts" in df.columns and len(df):
        try:
            return datetime.strptime(str(df["capture_ts"].iloc[0]).split("_")[0],
                                     "%Y%m%d").date()
        except (ValueError, TypeError):
            pass
    return repli or date.today()


def _maturite_annees(echeance, ref=None):
    ech = datetime.strptime(str(int(echeance)), "%Y%m%d").date()
    return max((ech - (ref or date.today())).days, 0) / 365.0


def _recuperer_spot(df, fichier_qc):
    """Spot depuis le QC s'il y est, sinon depuis le brut de même horodatage."""
    if "spot" in df.columns and not pd.isna(df["spot"].iloc[0]):
        return float(df["spot"].iloc[0])
    horodatage = "_".join(fichier_qc.stem.split("_")[-2:])
    brut = config.DATA / f"brut_{config.SYMBOLE}_{horodatage}.csv"
    if brut.exists():
        b = pd.read_csv(brut)
        if "spot" in b.columns:
            return float(b["spot"].iloc[0])
    raise ValueError("Spot introuvable (ni dans le qc_, ni dans le brut_).")


def forward(spot, T):
    """Forward de REPLI par le carry : F = S e^{(r-q)T} (Éq. 3 du PDF).
    Utilisé seulement quand la parité n'est pas disponible pour une maturité."""
    return spot * np.exp((config.TAUX_SANS_RISQUE - config.DIVIDENDE) * T)


def courbe_forward(df_qc, spot, ref):
    """
    Construit la courbe de forward par PARITÉ (forward.py) depuis le brut qui
    correspond à la capture du QC. Le nom de fichier du brut EST le capture_ts,
    donc on le retrouve directement. Retourne {echeance:int -> ResultatForward}.
    Vide si le brut est introuvable (vieux QC) -> tout retombera sur le carry.
    """
    if "capture_ts" not in df_qc.columns or not len(df_qc):
        return {}
    cap = str(df_qc["capture_ts"].iloc[0])
    if not cap or cap == "nan":
        return {}
    brut_path = config.DATA / f"brut_{config.SYMBOLE}_{cap}.csv"
    if not brut_path.exists():
        return {}
    try:
        df_brut = pd.read_csv(brut_path)
        resultats, _, _ = fwd.construire_courbe(df_brut, spot, ref)
        return resultats
    except Exception as e:
        print(f"[surface] forward parité indisponible ({e}) -> repli carry.")
        return {}


# ===========================================================================
# 2. SVI : la paramétrisation (Éq. 20) et sa calibration en variance totale
# ===========================================================================
def svi(k, p):
    """Variance totale SVI brute (Éq. 20 du PDF) :
        w(k) = a + b ( rho (k - m) + sqrt((k - m)^2 + s^2) )
    p = (a, b, rho, m, s)."""
    a, b, rho, m, s = p
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + s ** 2))


def _bornes_svi(k, w):
    """Bornes « raisonnables » sur les paramètres (PDF : apply sensible bounds)."""
    wmax = max(float(np.max(w)), 1e-8)
    kmin, kmax = float(np.min(k)), float(np.max(k))
    noms = ["a", "b", "rho", "m", "s"]
    bas = [-2 * wmax, 0.0, -0.999, kmin - 0.05, 1e-4]
    haut = [2 * wmax, 5.0, 0.999, kmax + 0.05, 0.5]
    return noms, bas, haut


def calibrer_svi(k, w, T, sigma_obs):
    """
    Cale un SVI sur (k, w) par moindres carrés EN VARIANCE TOTALE (PDF : fit in
    total variance space). Essaie plusieurs départs de rho (la calibration SVI
    est sensible au point de départ) et garde le meilleur ajustement.

    Retourne un dict avec params, RMSE et erreur max EN VOL, et les bornes
    atteintes — ou None si la calibration échoue.
    """
    noms, bas, haut = _bornes_svi(k, w)
    wmin = float(np.min(w))
    k_at_min = float(k[np.argmin(w)])

    meilleur = None
    for rho0 in (-0.5, -0.1, 0.3):
        x0 = np.clip([max(wmin, 1e-6), 0.01, rho0, k_at_min, 0.05], bas, haut)
        try:
            res = least_squares(
                lambda p: svi(k, p) - w, x0,
                bounds=(bas, haut), method="trf", x_scale="jac", max_nfev=4000,
            )
        except Exception:
            continue
        if meilleur is None or res.cost < meilleur.cost:
            meilleur = res
    if meilleur is None:
        return None

    p = meilleur.x
    # Erreurs exprimées EN VOL (interprétable) : sigma = sqrt(w / T)
    w_fit = np.clip(svi(k, p), 1e-12, None)
    sigma_fit = np.sqrt(w_fit / T)
    rmse = float(np.sqrt(np.mean((sigma_fit - sigma_obs) ** 2)))
    maxerr = float(np.max(np.abs(sigma_fit - sigma_obs)))

    aux_bornes = [noms[i] for i in range(5)
                  if abs(p[i] - bas[i]) < 1e-6 or abs(p[i] - haut[i]) < 1e-6]
    return {
        "methode": "svi",
        "params": dict(zip(noms, [round(float(v), 8) for v in p])),
        "rmse_vol": rmse, "max_err_vol": maxerr,
        "bornes_atteintes": ",".join(aux_bornes) if aux_bornes else "",
        "_p": p,
    }


# ===========================================================================
# 3. Fallback non-paramétrique : spline en variance totale
# ===========================================================================
def calibrer_spline(k, w, T, sigma_obs):
    """Spline lissante en variance totale (fallback du PDF pour tranches pauvres)."""
    idx = np.argsort(k)
    ks, ws, so = k[idx], w[idx], sigma_obs[idx]
    ordre = min(3, len(ks) - 1)
    # Léger lissage : suit les points sans osciller ; ext=3 -> valeur de bord
    # au-delà des données (pas d'extrapolation explosive).
    spl = UnivariateSpline(ks, ws, k=ordre, s=len(ks) * 1e-7, ext=3)
    w_fit = np.clip(spl(ks), 1e-12, None)
    sigma_fit = np.sqrt(w_fit / T)
    rmse = float(np.sqrt(np.mean((sigma_fit - so) ** 2)))
    maxerr = float(np.max(np.abs(sigma_fit - so)))
    return {
        "methode": "spline", "params": {}, "rmse_vol": rmse, "max_err_vol": maxerr,
        "bornes_atteintes": "", "_spline": spl,
    }


def evaluer_tranche(resultat, k_grille):
    """Évalue la variance totale du modèle d'une tranche sur une grille de k."""
    if resultat["methode"] == "svi":
        w = svi(k_grille, resultat["_p"])
    elif resultat["methode"] == "spline":
        w = resultat["_spline"](k_grille)
    else:
        return np.full_like(k_grille, np.nan)
    return np.clip(w, 0.0, None)   # jamais de variance négative (cross-strike)


# ===========================================================================
# 4. Ajustement d'une tranche : SVI si possible, sinon spline
# ===========================================================================
def ajuster_tranche(k, w, T, sigma_obs):
    n = len(k)
    if n < 3:
        return {"methode": "insuffisant", "params": {}, "n": n,
                "rmse_vol": np.nan, "max_err_vol": np.nan,
                "bornes_atteintes": "", "raison": f"seulement {n} point(s)"}

    raison = ""
    resultat = None
    if n >= MIN_POINTS_PAR_TRANCHE:
        svi_fit = calibrer_svi(k, w, T, sigma_obs)
        if svi_fit is None:
            raison = "SVI a échoué -> spline"
        elif svi_fit["rmse_vol"] > MAX_RMSE_VOL:
            raison = (f"RMSE SVI {svi_fit['rmse_vol']:.4f} > {MAX_RMSE_VOL} -> spline")
        else:
            resultat = svi_fit
    else:
        raison = f"{n} points < {MIN_POINTS_PAR_TRANCHE} -> spline"

    if resultat is None:
        resultat = calibrer_spline(k, w, T, sigma_obs)

    resultat["n"] = n
    resultat["raison"] = raison
    return resultat


# ===========================================================================
# 5. Diagnostic de non-arbitrage : monotonie calendaire (Éq. 21)
# ===========================================================================
def diagnostic_calendaire(tranches, k_grille):
    """
    Vérifie que la variance totale ne décroît pas quand la maturité augmente,
    à log-moneyness fixé (Éq. 21 du PDF : ∂w/∂T ≥ 0). Retourne le nombre et le
    pourcentage de violations sur la grille.
    """
    triees = sorted(tranches, key=lambda t: t["T"])
    W = np.array([evaluer_tranche(t, k_grille) for t in triees])  # (n_tranches, n_k)
    if W.shape[0] < 2:
        return {"violations": 0, "total": 0, "pct": 0.0}
    diffs = np.diff(W, axis=0)                      # variation en T, à k fixé
    valides = np.isfinite(diffs)
    violations = int(np.sum((diffs < -TOL_CALENDRIER) & valides))
    total = int(np.sum(valides))
    pct = 100.0 * violations / total if total else 0.0
    return {"violations": violations, "total": total, "pct": pct}


# ===========================================================================
# 6. Reconstruction de la grille + interpolation cross-maturité (Éq. 22)
# ===========================================================================
def construire_grille(tranches, spot):
    """
    Grille (k, T) reconstruite depuis les tranches calibrées. Entre deux
    maturités, on interpole linéairement EN VARIANCE TOTALE (Éq. 22 du PDF :
    w(T) = lambda·w(T1) + (1-lambda)·w(T2)).
    Retourne (k_grille, T_fin, W_fin, sigma_fin) pour la surface 3D, et un
    DataFrame de la grille par tranche calibrée pour la persistance.
    """
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    cal = sorted(cal, key=lambda t: t["T"])
    kmin = min(t["k"].min() for t in cal)
    kmax = max(t["k"].max() for t in cal)
    k_grille = np.linspace(kmin, kmax, N_GRILLE_K)

    # Variance par tranche calibrée sur la grille k
    W_tranches = np.array([evaluer_tranche(t, k_grille) for t in cal])  # (n_cal, n_k)
    T_cal = np.array([t["T"] for t in cal])

    # Grille T fine + interpolation EN VARIANCE à k constant (Éq. 22)
    T_fin = np.linspace(T_cal.min(), T_cal.max(), N_GRILLE_T)
    W_fin = np.empty((N_GRILLE_T, N_GRILLE_K))
    for j in range(N_GRILLE_K):
        W_fin[:, j] = np.interp(T_fin, T_cal, W_tranches[:, j])
    sigma_fin = np.sqrt(np.clip(W_fin, 0, None) / T_fin[:, None])

    # DataFrame de la grille par tranche calibrée (pour le CSV de persistance)
    lignes = []
    for t in cal:
        w_t = evaluer_tranche(t, k_grille)
        F = t["forward"]   # le F effectivement utilisé (parité ou carry)
        for kk, ww in zip(k_grille, w_t):
            lignes.append({
                "echeance": t["echeance"], "T": round(t["T"], 5),
                "log_moneyness": round(float(kk), 5),
                "strike": round(float(F * np.exp(kk)), 2),
                "variance_totale": round(float(ww), 8),
                "vol_implicite": round(float(np.sqrt(max(ww, 0) / t["T"])), 5),
            })
    grille_df = pd.DataFrame(lignes)
    return k_grille, T_fin, W_fin, sigma_fin, grille_df


# ===========================================================================
# 7. Tracés (revue opérateur) : points bruts vs tranches ajustées + surface 3D
# ===========================================================================
def tracer_smiles(tranches, spot, auto_open=True):
    """LE tracé exigé par le PDF : points acceptés vs courbe ajustée, par tranche."""
    couleurs = ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4",
                "#84cc16", "#ec4899"]
    fig = go.Figure()
    for i, t in enumerate(sorted(tranches, key=lambda x: x["T"])):
        c = couleurs[i % len(couleurs)]
        # points acceptés (réels)
        fig.add_trace(go.Scatter(
            x=t["strikes_obs"], y=t["sigma_obs"], mode="markers",
            marker=dict(color=c, size=7, symbol="circle"),
            name=f"{int(t['echeance'])} — points", legendgroup=str(i),
        ))
        # courbe ajustée (modèle), si la tranche a été calibrée
        if t["methode"] in ("svi", "spline"):
            F = t["forward"]   # le F effectivement utilisé (parité ou carry)
            k_fit = np.linspace(t["k"].min(), t["k"].max(), 100)
            w_fit = evaluer_tranche(t, k_fit)
            sig_fit = np.sqrt(np.clip(w_fit, 0, None) / t["T"])
            K_fit = F * np.exp(k_fit)
            fig.add_trace(go.Scatter(
                x=K_fit, y=sig_fit, mode="lines",
                line=dict(color=c, width=2),
                name=f"{int(t['echeance'])} — {t['methode'].upper()}",
                legendgroup=str(i),
            ))
    fig.add_vline(x=spot, line_dash="dash", line_color="grey", annotation_text="spot")
    fig.update_layout(
        title=f"Smiles {config.SYMBOLE} — points réels vs ajustement (SVI / spline)",
        xaxis_title="Strike", yaxis_title="Volatilité implicite",
        template="plotly_dark", height=600,
    )
    chemin = config.CALCUL / "surface_smiles_svi.html"
    fig.write_html(str(chemin), auto_open=auto_open)
    print(f"[surface] smiles (points vs ajustement) : {chemin}")


def tracer_surface_3d(k_grille, T_fin, sigma_fin, spot, auto_open=True):
    """Surface 3D reconstruite, en log-moneyness (représentation du PDF)."""
    fig = go.Figure(data=[go.Surface(
        x=k_grille, y=T_fin, z=sigma_fin * 100,
        colorscale="Viridis", colorbar=dict(title="IV %"),
    )])
    fig.update_layout(
        title=f"Nappe SVI {config.SYMBOLE} — spot {spot:.0f} "
              f"(log-moneyness, interpolation en variance)",
        scene=dict(
            xaxis_title="Log-moneyness k = ln(K/F)",
            yaxis_title="Maturité (années)",
            zaxis_title="Volatilité implicite (%)",
        ),
        template="plotly_dark", height=700,
    )
    chemin = config.CALCUL / "surface_3d_svi.html"
    fig.write_html(str(chemin), auto_open=auto_open)
    print(f"[surface] surface 3D reconstruite : {chemin}")


# ===========================================================================
# 8. Préparation des points + orchestration
# ===========================================================================
def preparer_tranches(df, spot, ref=None, courbe_fwd=None):
    """
    Découpe les points QC en tranches de maturité. Pour chaque échéance, agrège
    par strike (médiane, robuste) et passe en (k = ln(K/F), w = sigma^2 T).
    F vient de la PARITÉ (courbe_fwd) si dispo, sinon du carry (labellisé).
    Retourne (liste de tranches, DataFrame de tous les points pour persistance).
    """
    courbe_fwd = courbe_fwd or {}
    df = df.copy()
    df["T"] = df["echeance"].apply(lambda e: _maturite_annees(e, ref))

    def _forward_de(ech, T):
        """F retenu pour une maturité : parité si calibrée, sinon carry."""
        rr = courbe_fwd.get(int(ech))
        if rr is not None and rr.methode == "parite":
            return float(rr.forward), "parite", float(rr.confiance), float(rr.dividende_implicite)
        return float(forward(spot, T)), "carry", 0.0, config.DIVIDENDE

    # forward par ligne (pour la persistance des points) : map echeance -> F
    f_par_ech = {int(e): _forward_de(e, float(df[df["echeance"] == e]["T"].iloc[0]))[0]
                 for e in df["echeance"].unique()}
    df["forward"] = df["echeance"].apply(lambda e: f_par_ech[int(e)])
    df["log_moneyness"] = np.log(df["strike"] / df["forward"])
    df["variance_totale"] = df["iv_calculee"] ** 2 * df["T"]

    tranches = []
    gardes = df[df["gardee"]]
    for ech, sous in gardes.groupby("echeance"):
        T = float(sous["T"].iloc[0])
        if T <= 0:
            continue
        F, meth, conf, div = _forward_de(ech, T)
        # médiane par strike (robuste), puis tri par k
        ag = (sous.groupby("strike", as_index=False)
                  .agg(iv=("iv_calculee", "median")))
        ag["k"] = np.log(ag["strike"] / F)
        ag["w"] = ag["iv"] ** 2 * T
        ag = ag.sort_values("k")

        fit = ajuster_tranche(ag["k"].values, ag["w"].values, T, ag["iv"].values)
        fit.update({
            "echeance": int(ech), "T": T,
            "k": ag["k"].values, "w": ag["w"].values,
            "strikes_obs": ag["strike"].values, "sigma_obs": ag["iv"].values,
            "forward": F, "forward_methode": meth, "forward_conf": conf,
            "dividende_impl": div,
        })
        tranches.append(fit)
    return tranches, df


def main():
    auto = os.environ.get("SURFACE_AUTO_OPEN", "1") != "0"

    fichier = _dernier_fichier_qc()
    print(f"[surface] lecture des points QC : {fichier.name}")
    df = pd.read_csv(fichier)
    spot = _recuperer_spot(df, fichier)
    ref = _date_capture(df)
    print(f"[surface] spot de référence : {spot:.2f}")
    print(f"[surface] date de valorisation (jour de capture) : {ref}\n")

    # --- Étape 6 : forward par parité (depuis le brut de la même capture) ---
    courbe_fwd = courbe_forward(df, spot, ref)
    if courbe_fwd:
        n_par = sum(1 for r in courbe_fwd.values() if r.methode == "parite")
        print(f"[surface] forward par parité : {n_par}/{len(courbe_fwd)} maturités "
              f"(le reste en repli carry)")
        print(f"{'échéance':>10} {'F parité':>10} {'F carry':>10} {'q impl.':>8} "
              f"{'conf.':>6}  méthode")
        for ech in sorted(courbe_fwd, key=lambda e: courbe_fwd[e].T):
            r = courbe_fwd[ech]
            print(f"{ech:>10} {r.forward:>10.2f} {r.forward_carry:>10.2f} "
                  f"{r.dividende_implicite*100:>7.2f}% {r.confiance:>6.2f}  "
                  f"{r.qualite} ({r.methode})")
        print()
    else:
        print("[surface] forward par parité indisponible (brut introuvable) "
              "-> repli carry pour toutes les maturités.\n")

    tranches, points_df = preparer_tranches(df, spot, ref=ref, courbe_fwd=courbe_fwd)
    if not tranches:
        print("[surface] aucune tranche exploitable (vérifie le QC).")
        return

    # --- Rapport de calibration par tranche ---
    print("=== Calibration par tranche (étape 9 : SVI, fallback spline) ===")
    print(f"{'échéance':>10} {'T':>7} {'pts':>4} {'méthode':>9} "
          f"{'RMSE_vol':>9} {'maxerr':>8}  remarque")
    lignes_params = []
    for t in sorted(tranches, key=lambda x: x["T"]):
        rmse = "" if not np.isfinite(t.get("rmse_vol", np.nan)) else f"{t['rmse_vol']:.4f}"
        mx = "" if not np.isfinite(t.get("max_err_vol", np.nan)) else f"{t['max_err_vol']:.4f}"
        print(f"{t['echeance']:>10} {t['T']:>7.4f} {t['n']:>4} {t['methode']:>9} "
              f"{rmse:>9} {mx:>8}  {t.get('raison', '')}")
        p = t.get("params", {})
        lignes_params.append({
            "echeance": t["echeance"], "T": round(t["T"], 5), "methode": t["methode"],
            "n_points": t["n"], "rmse_vol": t.get("rmse_vol"),
            "max_err_vol": t.get("max_err_vol"),
            "a": p.get("a"), "b": p.get("b"), "rho": p.get("rho"),
            "m": p.get("m"), "s": p.get("s"),
            "forward": round(t.get("forward", np.nan), 2),
            "forward_methode": t.get("forward_methode", ""),
            "forward_conf": round(t.get("forward_conf", 0.0), 3),
            "dividende_impl": round(t.get("dividende_impl", config.DIVIDENDE), 5),
            "bornes_atteintes": t.get("bornes_atteintes", ""),
            "remarque": t.get("raison", ""),
        })

    # --- Diagnostic de non-arbitrage calendaire (Éq. 21) ---
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    kmin = min(t["k"].min() for t in cal)
    kmax = max(t["k"].max() for t in cal)
    k_diag = np.linspace(kmin, kmax, N_GRILLE_K)
    diag = diagnostic_calendaire(cal, k_diag)
    print(f"\n[surface] monotonie calendaire (variance croissante avec T) : "
          f"{diag['violations']}/{diag['total']} points en violation "
          f"({diag['pct']:.1f}%)")
    if diag["violations"] == 0:
        print("[surface]   -> aucune violation : pas d'arbitrage calendaire grossier.")
    else:
        print("[surface]   -> violations détectées : à signaler (souvent dû à des "
              "tranches très courtes bruitées).")

    # --- Reconstruction de la grille (interpolation en variance, Éq. 22) ---
    k_grille, T_fin, W_fin, sigma_fin, grille_df = construire_grille(cal, spot)

    # --- Persistance : points + paramètres + grille (exigence du PDF) ---
    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    cols_points = ["echeance", "strike", "type", "T", "forward", "log_moneyness",
                   "iv_calculee", "variance_totale", "gardee", "raison_rejet"]
    cols_points = [c for c in cols_points if c in points_df.columns]
    f_points = config.CALCUL / f"surface_points_{horo}.csv"
    f_params = config.CALCUL / f"surface_params_{horo}.csv"
    f_grille = config.CALCUL / f"surface_grid_{horo}.csv"
    points_df[cols_points].to_csv(f_points, index=False)
    pd.DataFrame(lignes_params).to_csv(f_params, index=False)
    grille_df.to_csv(f_grille, index=False)
    print(f"\n[surface] points (bruts+acceptés+rejetés) : {f_points.name}")
    print(f"[surface] paramètres par tranche          : {f_params.name}")
    print(f"[surface] grille reconstruite             : {f_grille.name}")

    # --- Tracés ---
    tracer_smiles(tranches, spot, auto_open=auto)
    tracer_surface_3d(k_grille, T_fin, sigma_fin, spot, auto_open=auto)


if __name__ == "__main__":
    main()

# forward.py — Étape 6 du framework : FORWARD PAR PARITÉ CALL-PUT + carry implicite.
#
# C'est l'étape la plus détaillée du PDF (Step 6 + Partie V.1 + Appendice A), et
# la première question de revue du prof : « Explain why the forward curve must be
# built before log-moneyness and surface fitting. » Réponse : une erreur de forward
# contamine TOUT en aval (moneyness, IV, forme de la nappe, deltas, P&L scénario).
#
# Méthode (Éq. 2-5 du PDF) :
#   - pour chaque maturité, on prend les paires call/put proches de la monnaie ;
#   - parité : F_i = K + e^{rT} (C_mid - P_mid)   (le forward, SANS feed dividende) ;
#   - on pondère chaque candidat (proximité ATM + qualité de spread) ;
#   - on rejette les aberrants par z-score ROBUSTE (MAD, Éq. 24) ;
#   - on agrège (moyenne pondérée) -> forward retenu ; on garde aussi la médiane ;
#   - dividende implicite (Éq. 5) : q = r - ln(F/S)/T ;
#   - score de CONFIANCE (densité x cohérence) + label qualité ;
#   - si trop peu de candidats propres -> REPLI carry F = S e^{(r-q)T}, LABELLISÉ.
#
# Le module ne se connecte à rien : il consomme le dernier brut_*.csv (prix bruts
# call ET put par strike), émet la courbe de forward + un bundle de diagnostics.

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional
import numpy as np
import pandas as pd

import config


# ===========================================================================
# Objets typés (PDF Partie XVII : dataclasses pour les objets d'analytics)
# ===========================================================================
@dataclass(frozen=True)
class CandidatForward:
    strike: float
    call_mid: float
    put_mid: float
    forward_estime: float       # F_i = K + e^{rT}(C-P)
    poids: float
    zscore_robuste: float       # z par MAD vs médiane des candidats
    retenu: bool                # False si rejeté comme aberrant


@dataclass(frozen=True)
class ResultatForward:
    echeance: int
    T: float
    spot: float
    forward: float              # forward retenu (pondéré sur les candidats propres)
    forward_median: float       # diagnostic : médiane des candidats propres
    forward_carry: float        # repli théorique S e^{(r-q)T} (toujours calculé)
    dividende_implicite: float  # q = r - ln(F/S)/T
    n_candidats: int            # avant rejet d'outliers
    n_retenus: int              # après rejet
    mad: float                  # dispersion robuste des candidats (en points d'indice)
    confiance: float            # score [0,1]
    qualite: str                # haute / moyenne / faible / repli_carry
    methode: str                # "parite" ou "repli_carry"
    candidats: List[CandidatForward] = field(default_factory=list)


# ===========================================================================
# Utilitaires
# ===========================================================================
def _date_capture(df, repli=None):
    """Date de valorisation = jour de CAPTURE (déterminisme), lue dans 'capture_ts'."""
    if "capture_ts" in df.columns and len(df):
        try:
            return datetime.strptime(str(df["capture_ts"].iloc[0]).split("_")[0],
                                     "%Y%m%d").date()
        except (ValueError, TypeError):
            pass
    return repli or date.today()


def _maturite_annees(echeance, ref):
    ech = datetime.strptime(str(int(echeance)), "%Y%m%d").date()
    return max((ech - ref).days, 0) / 365.0


def _mid(bid, ask, close):
    """Mid (bid+ask)/2 si bid/ask valides, sinon close, sinon None.
    Renvoie (prix, est_mid) — est_mid=False quand on est retombé sur le close."""
    if bid is not None and ask is not None and not pd.isna(bid) and not pd.isna(ask) \
            and bid > 0 and ask > 0:
        return (bid + ask) / 2.0, True
    if close is not None and not pd.isna(close) and close > 0:
        return float(close), False
    return None, False


def forward_carry(spot, T):
    """Forward de repli par le carry : F = S e^{(r-q)T} (Éq. 3 du PDF)."""
    return spot * np.exp((config.TAUX_SANS_RISQUE - config.DIVIDENDE) * T)


def zscore_robuste(valeurs):
    """Z-score robuste par MAD (Éq. 24 du PDF) :
        z_i = 0.6745 (x_i - médiane) / MAD
    Le 0.6745 rend le MAD comparable à un écart-type pour une loi normale.
    Renvoie (z, médiane, MAD). Si MAD = 0 (tous égaux), z = 0 partout."""
    x = np.asarray(valeurs, dtype=float)
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    if mad <= 1e-12:
        return np.zeros_like(x), med, 0.0
    z = 0.6745 * (x - med) / mad
    return z, med, mad


# ===========================================================================
# Cœur : forward d'UNE maturité
# ===========================================================================
def estimer_forward(sous, spot, T, echeance):
    """
    Estime le forward d'une maturité depuis ses paires call/put.
    `sous` = lignes brutes (call ET put) de CETTE échéance.
    Retourne un ResultatForward complet (avec diagnostics par candidat).
    """
    r = config.TAUX_SANS_RISQUE
    carry = float(forward_carry(spot, T))

    # 1) Construire les candidats : un par strike ayant un call ET un put exploitables,
    #    dans la bande d'éligibilité autour de la monnaie.
    bas, haut = spot * (1 - config.FORWARD_BANDE), spot * (1 + config.FORWARD_BANDE)
    bruts = []   # (strike, call_mid, put_mid, F_i, poids)
    for K, g in sous.groupby("strike"):
        K = float(K)
        if not (bas <= K <= haut):
            continue
        lc = g[g["type"] == "C"]
        lp = g[g["type"] == "P"]
        if lc.empty or lp.empty:
            continue
        c_mid, c_estmid = _mid(lc["bid"].iloc[0], lc["ask"].iloc[0], lc["close"].iloc[0])
        p_mid, p_estmid = _mid(lp["bid"].iloc[0], lp["ask"].iloc[0], lp["close"].iloc[0])
        if c_mid is None or p_mid is None:
            continue

        # Parité : F_i = K + e^{rT} (C - P)
        f_i = K + np.exp(r * T) * (c_mid - p_mid)

        # Poids = proximité ATM (gaussienne) x qualité de cotation (spread serré).
        prox = np.exp(-0.5 * ((K - spot) / (spot * config.FORWARD_LARGEUR_ATM)) ** 2)
        # spread relatif (si bid/ask dispo) ; pénalité fixe si on a dû prendre le close.
        sp = []
        for l, est in ((lc, c_estmid), (lp, p_estmid)):
            if est and l["ask"].iloc[0] > 0 and l["bid"].iloc[0] > 0:
                m = (l["bid"].iloc[0] + l["ask"].iloc[0]) / 2.0
                sp.append((l["ask"].iloc[0] - l["bid"].iloc[0]) / m if m > 0 else 0.1)
            else:
                sp.append(0.10)   # close-only : qualité moindre
        spread_rel = float(np.mean(sp))
        poids = prox / (1.0 + spread_rel)

        bruts.append((K, float(c_mid), float(p_mid), float(f_i), float(poids)))

    n_candidats = len(bruts)

    # 2) Pas assez de paires -> repli carry, labellisé (jamais caché).
    if n_candidats < config.FORWARD_MIN_CANDIDATS:
        return ResultatForward(
            echeance=int(echeance), T=T, spot=spot,
            forward=carry, forward_median=carry, forward_carry=carry,
            dividende_implicite=config.DIVIDENDE,
            n_candidats=n_candidats, n_retenus=0, mad=0.0,
            confiance=0.0, qualite="repli_carry", methode="repli_carry",
            candidats=[CandidatForward(K, c, p, f, w, 0.0, False)
                       for (K, c, p, f, w) in bruts],
        )

    # 3) Rejet des aberrants par z robuste (MAD).
    f_arr = np.array([b[3] for b in bruts])
    z, med, mad = zscore_robuste(f_arr)
    retenus_mask = np.abs(z) <= config.FORWARD_MAX_ZSCORE

    candidats = [CandidatForward(K, c, p, f, w, float(zi), bool(m))
                 for (K, c, p, f, w), zi, m in zip(bruts, z, retenus_mask)]

    f_ret = f_arr[retenus_mask]
    w_ret = np.array([b[4] for b in bruts])[retenus_mask]
    n_retenus = int(retenus_mask.sum())

    # Si tout a été rejeté (cas pathologique), repli carry.
    if n_retenus == 0 or w_ret.sum() <= 0:
        return ResultatForward(
            echeance=int(echeance), T=T, spot=spot,
            forward=carry, forward_median=carry, forward_carry=carry,
            dividende_implicite=config.DIVIDENDE,
            n_candidats=n_candidats, n_retenus=0, mad=mad,
            confiance=0.0, qualite="repli_carry", methode="repli_carry",
            candidats=candidats,
        )

    # 4) Agrégation : moyenne PONDÉRÉE (Éq. 4) + médiane (diagnostic).
    f_pondere = float(np.sum(w_ret * f_ret) / np.sum(w_ret))
    f_median = float(np.median(f_ret))

    # 5) Dividende implicite (Éq. 5) : q = r - ln(F/S)/T.
    div_impl = float(r - np.log(f_pondere / spot) / T) if T > 0 else config.DIVIDENDE

    # 6) Score de confiance = densité x cohérence, dans [0,1].
    mad_ret = float(np.median(np.abs(f_ret - np.median(f_ret))))
    densite = min(1.0, n_retenus / config.FORWARD_N_CONF)
    coherence = float(np.exp(-(mad_ret / spot) / config.FORWARD_CONF_TOL))
    confiance = max(0.0, min(1.0, densite * coherence))
    qualite = ("haute" if confiance >= 0.7 else
               "moyenne" if confiance >= 0.4 else "faible")

    return ResultatForward(
        echeance=int(echeance), T=T, spot=spot,
        forward=f_pondere, forward_median=f_median, forward_carry=carry,
        dividende_implicite=div_impl,
        n_candidats=n_candidats, n_retenus=n_retenus, mad=mad_ret,
        confiance=confiance, qualite=qualite, methode="parite",
        candidats=candidats,
    )


# ===========================================================================
# Courbe de forward : toutes les maturités
# ===========================================================================
def construire_courbe(df_brut, spot=None, ref=None):
    """
    Construit la courbe de forward depuis un brut (call+put par strike).
    Retourne (dict {echeance:int -> ResultatForward}, DataFrame courbe, DataFrame diag).
    """
    if spot is None:
        spot = float(df_brut["spot"].iloc[0])
    ref = ref or _date_capture(df_brut)
    capture_ts = str(df_brut["capture_ts"].iloc[0]) if "capture_ts" in df_brut.columns else ""

    resultats = {}
    for ech, sous in df_brut.groupby("echeance"):
        T = _maturite_annees(ech, ref)
        if T <= 0:
            continue
        resultats[int(ech)] = estimer_forward(sous, spot, T, ech)

    # DataFrame courbe (une ligne par maturité) — l'artefact persistable.
    courbe = pd.DataFrame([{
        "echeance": r.echeance, "T": round(r.T, 5), "spot": round(r.spot, 2),
        "forward": round(r.forward, 2), "forward_median": round(r.forward_median, 2),
        "forward_carry": round(r.forward_carry, 2),
        "dividende_implicite": round(r.dividende_implicite, 5),
        "n_candidats": r.n_candidats, "n_retenus": r.n_retenus,
        "mad": round(r.mad, 4), "confiance": round(r.confiance, 3),
        "qualite": r.qualite, "methode": r.methode,
        "capture_ts": capture_ts, "forward_version": config.FORWARD_VERSION,
    } for r in sorted(resultats.values(), key=lambda x: x.T)])

    # DataFrame diagnostics (une ligne par candidat) — le bundle exigé par le PDF.
    lignes = []
    for r in sorted(resultats.values(), key=lambda x: x.T):
        for c in r.candidats:
            lignes.append({
                "echeance": r.echeance, "T": round(r.T, 5), "strike": c.strike,
                "call_mid": round(c.call_mid, 3), "put_mid": round(c.put_mid, 3),
                "forward_i": round(c.forward_estime, 2),
                "residu_vs_retenu": round(c.forward_estime - r.forward, 2),
                "poids": round(c.poids, 4), "zscore_robuste": round(c.zscore_robuste, 3),
                "retenu": c.retenu, "capture_ts": capture_ts,
            })
    diag = pd.DataFrame(lignes)
    return resultats, courbe, diag


# ===========================================================================
# Orchestration / artefact
# ===========================================================================
def _dernier_brut():
    fichiers = sorted(config.DATA.glob(f"brut_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError("Aucun brut_. Lance d'abord : python donnees.py")
    return fichiers[-1]


def main():
    fichier = _dernier_brut()
    print(f"[forward] lecture du brut : {fichier.name}")
    df = pd.read_csv(fichier)
    spot = float(df["spot"].iloc[0])
    ref = _date_capture(df)
    print(f"[forward] spot {spot:.2f} | date de valorisation (capture) : {ref}\n")

    resultats, courbe, diag = construire_courbe(df, spot, ref)

    print("=== Courbe de forward par parité (étape 6) ===")
    print(f"{'échéance':>10} {'T':>7} {'F parité':>10} {'F carry':>10} "
          f"{'q impl.':>8} {'cand.':>6} {'conf.':>6}  qualité")
    for _, l in courbe.iterrows():
        print(f"{int(l['echeance']):>10} {l['T']:>7.4f} {l['forward']:>10.2f} "
              f"{l['forward_carry']:>10.2f} {l['dividende_implicite']*100:>7.2f}% "
              f"{int(l['n_retenus'])}/{int(l['n_candidats']):<3} {l['confiance']:>6.2f}  "
              f"{l['qualite']} ({l['methode']})")

    # Écart parité vs carry : c'est le diagnostic le plus parlant (l'erreur qu'on
    # commettait en se contentant du carry à dividende fixe).
    par = courbe[courbe["methode"] == "parite"]
    if len(par):
        ecart = (par["forward"] - par["forward_carry"]).abs()
        print(f"\n[forward] écart |parité - carry| : médian {ecart.median():.2f} pts, "
              f"max {ecart.max():.2f} pts "
              f"(le carry à q={config.DIVIDENDE:.1%} fixe ne capte pas le vrai dividende)")

    # Persistance : courbe + diagnostics (exigence du PDF : forward = sortie 1re classe).
    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    f_courbe = config.CALCUL / f"forward_{config.SYMBOLE}_{horo}.csv"
    f_diag = config.CALCUL / f"forward_diag_{config.SYMBOLE}_{horo}.csv"
    courbe.to_csv(f_courbe, index=False)
    diag.to_csv(f_diag, index=False)
    print(f"\n[forward] courbe        : {f_courbe.name}")
    print(f"[forward] diagnostics   : {f_diag.name}")


if __name__ == "__main__":
    main()

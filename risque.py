# risque.py — Étapes 11-12 du framework : Greeks agrégés + P&L scénario.
# Lit un portefeuille (positions.csv) + la nappe d'IV (dernier qc_), puis :
#   - étape 11 : calcule les Greeks de chaque ligne et les AGRÈGE (monétisés)
#   - étape 12 : reprice tout le portefeuille sous une grille de chocs spot x vol
#                (FULL REPRICING, la vérité, pas l'approximation Greeks)
# N'invente AUCUNE formule : appelle pricing.py (moteur déjà validé).

from datetime import datetime, date
import numpy as np
import pandas as pd

import config
from pricing import prix_bs, greeks


# --- Chocs de scénario (étape 12) : centralisés et VERSIONNÉS dans config.py.
#     PDF : « Version the scenario grid. Do not leave it as a mutable cell. » ---
CHOCS_SPOT = config.CHOCS_SPOT   # en % du spot
CHOCS_VOL = config.CHOCS_VOL     # en points de vol absolus


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


def _dernier_qc():
    fichiers = sorted(config.CALCUL.glob(f"qc_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError("Lance d'abord la chaîne donnees -> calcul_iv -> qc.")
    return fichiers[-1]


def charger_portefeuille():
    """Lit positions.csv et y rattache l'IV de la nappe + le spot du jour."""
    pos = pd.read_csv(config.RACINE / "positions.csv")

    qc = pd.read_csv(_dernier_qc())
    spot = float(qc["spot"].iloc[0])
    # Déterminisme : date de valorisation = jour de capture du QC (pas today()).
    ref = _date_capture(qc)
    # IV de référence par (échéance, strike) — SANS le type : par parité call-put,
    # le call et le put de même strike/échéance partagent la même IV. On utilise
    # donc l'IV du point QC retenu (l'OTM), valable pour les deux droits.
    nappe = (qc[qc["gardee"]]
             .groupby(["echeance", "strike"], as_index=False)["iv_calculee"]
             .median())

    df = pos.merge(nappe, on=["echeance", "strike"], how="left")
    manquants = df[df["iv_calculee"].isna()]
    if len(manquants):
        print("[risque] ATTENTION, IV introuvable dans la nappe pour :")
        print(manquants[["echeance", "strike", "type"]].to_string(index=False))
        print("[risque] -> ces lignes seront ignorées. Vérifie positions.csv "
              "(strike/échéance présents dans la nappe ?)\n")
        df = df.dropna(subset=["iv_calculee"])

    df["T"] = df["echeance"].apply(lambda e: _maturite_annees(e, ref))
    df["cp"] = np.where(df["type"] == "C", 1, -1)
    return df, spot


def greeks_portefeuille(df, spot):
    """
    Étape 11 : Greeks par ligne + agrégat. Deux familles de colonnes :

      - Greeks de POSITION (Greek x quantité x multiplicateur) :
        delta, gamma, vega, theta_jour. Pratiques pour le pilotage.

      - Greeks CASH / dollar Greeks (Éq. 17-18 du PDF), tous en euros :
          delta_cash      = delta x S x qté x mult   (dollar delta : exposition
                            notionnelle, = € de sous-jacent delta-équivalents)
          gamma_cash      = gamma x S^2 x qté x mult  (dollar gamma, Éq. 17)
          vega_cash       = vega x qté x mult         (dollar vega, Éq. 18)
          theta_cash_jour = theta x qté x mult        (€ par jour)

    Le PDF demande explicitement de stocker brut ET monétisé : on garde les deux.
    Noter que pour le vega et le theta, "Greek de position" et "cash" coïncident
    (pas de facteur spot) ; c'est seulement pour delta et gamma qu'ils diffèrent.
    """
    r, q = config.TAUX_SANS_RISQUE, config.DIVIDENDE
    mult = float(config.MULTIPLICATEUR)
    lignes = []
    for _, p in df.iterrows():
        g = greeks(spot, p["strike"], p["T"], r, q, p["iv_calculee"], int(p["cp"]))
        prix = prix_bs(spot, p["strike"], p["T"], r, q, p["iv_calculee"], int(p["cp"]))
        facteur = p["quantite"] * mult
        lignes.append({
            "echeance": int(p["echeance"]), "strike": p["strike"], "type": p["type"],
            "quantite": p["quantite"], "iv": round(p["iv_calculee"], 4),
            "prix": round(prix, 2),
            "valeur": round(prix * facteur, 2),
            "delta": round(g["delta"] * facteur, 2),
            "gamma": round(g["gamma"] * facteur, 4),
            "vega": round(g["vega"] * facteur, 2),
            "theta_jour": round(g["theta_jour"] * facteur, 2),
            # --- Greeks CASH / dollar Greeks (Éq. 17-18 du PDF) ---
            "delta_cash": round(g["delta"] * spot * facteur, 2),
            "gamma_cash": round(g["gamma"] * spot**2 * facteur, 2),
            "vega_cash": round(g["vega"] * facteur, 2),
            "theta_cash_jour": round(g["theta_jour"] * facteur, 2),
        })
    detail = pd.DataFrame(lignes)
    colonnes_somme = ["valeur", "delta", "gamma", "vega", "theta_jour",
                      "delta_cash", "gamma_cash", "vega_cash", "theta_cash_jour"]
    agregat = detail[colonnes_somme].sum()
    return detail, agregat


def valeur_portefeuille(df, spot, choc_spot, choc_vol):
    """Reprice TOUT le portefeuille sous un choc (spot en %, vol en points)."""
    r, q = config.TAUX_SANS_RISQUE, config.DIVIDENDE
    mult = float(config.MULTIPLICATEUR)
    s = spot * (1 + choc_spot)
    total = 0.0
    for _, p in df.iterrows():
        sigma = max(p["iv_calculee"] + choc_vol, 1e-4)
        prix = prix_bs(s, p["strike"], p["T"], r, q, sigma, int(p["cp"]))
        total += prix * p["quantite"] * mult
    return total


def pnl_scenarios(df, spot, valeur_base):
    """Étape 12 : grille de P&L (full repricing) = valeur(choc) - valeur(base)."""
    table = pd.DataFrame(index=[f"{c:+.0%}" for c in CHOCS_SPOT],
                         columns=[f"{v:+.2f}" for v in CHOCS_VOL], dtype=float)
    for cs in CHOCS_SPOT:
        for cv in CHOCS_VOL:
            v = valeur_portefeuille(df, spot, cs, cv)
            table.loc[f"{cs:+.0%}", f"{cv:+.2f}"] = round(v - valeur_base, 2)
    return table


def pnl_greeks(agregat, spot):
    """
    Approximation Taylor du P&L à partir des Greeks AGRÉGÉS :
        P&L ≈ delta·dS + ½·gamma·dS² + vega·dσ
    (on ignore le theta car les chocs sont instantanés : dt = 0)
    Bonne pour les petits chocs, dérape sur les gros à cause de la convexité.
    """
    table = pd.DataFrame(index=[f"{c:+.0%}" for c in CHOCS_SPOT],
                         columns=[f"{v:+.2f}" for v in CHOCS_VOL], dtype=float)
    delta, gamma, vega = agregat["delta"], agregat["gamma"], agregat["vega"]
    for cs in CHOCS_SPOT:
        dS = spot * cs                                         # mouvement de spot en points
        for cv in CHOCS_VOL:
            pnl = delta * dS + 0.5 * gamma * dS**2 + vega * (cv * 100)
            # vega est exprimé "par point de vol" ; cv est en absolu (0.05 = 5 pts)
            # -> on multiplie par 100 pour aligner les unités
            table.loc[f"{cs:+.0%}", f"{cv:+.2f}"] = round(pnl, 2)
    return table


# --- Paramètres VaR : centralisés dans config.py (scenarios/VaR, versionnés) ---
SIGMA_SPOT_JOUR = config.VAR_SIGMA_SPOT_JOUR   # écart-type des rendements quotidiens
SIGMA_VOL_JOUR = config.VAR_SIGMA_VOL_JOUR     # écart-type du mouvement quotidien d'IV
CORREL_SPOT_VOL = config.VAR_CORREL_SPOT_VOL   # corrélation spot/vol
N_SIMULATIONS = config.VAR_N_SIMULATIONS       # tirages Monte Carlo
HORIZON_JOURS = config.VAR_HORIZON_JOURS       # horizon de la VaR (jours)
QUANTILE = config.VAR_QUANTILE                 # niveau de confiance


def var_es_montecarlo(df, spot, valeur_base, rng=None):
    """
    VaR & ES paramétriques par Monte Carlo + full repricing.
    On tire N couples (rendement_spot, choc_vol) corrélés selon une loi normale,
    on reprice le portefeuille pour chacun, on en déduit la distribution des P&L
    et on extrait le quantile (VaR) et la moyenne au-delà (ES).
    """
    rng = rng or np.random.default_rng(seed=42)  # graine fixe = résultats reproductibles
    # On scale les écarts-types à l'horizon (loi du temps : sigma * sqrt(jours))
    sig_s = SIGMA_SPOT_JOUR * np.sqrt(HORIZON_JOURS)
    sig_v = SIGMA_VOL_JOUR * np.sqrt(HORIZON_JOURS)

    # Tirage de N couples (rendement, choc_vol) corrélés
    moyennes = [0.0, 0.0]
    covariance = [[sig_s**2, CORREL_SPOT_VOL * sig_s * sig_v],
                  [CORREL_SPOT_VOL * sig_s * sig_v, sig_v**2]]
    chocs = rng.multivariate_normal(moyennes, covariance, size=N_SIMULATIONS)

    # Pour chaque tirage, reprice tout le portefeuille (full repricing)
    pnls = np.empty(N_SIMULATIONS)
    for i in range(N_SIMULATIONS):
        v = valeur_portefeuille(df, spot, chocs[i, 0], chocs[i, 1])
        pnls[i] = v - valeur_base

    # Extraction des métriques de risque
    var = -np.quantile(pnls, 1 - QUANTILE)       # signe + : c'est une perte
    queue = pnls[pnls <= -var]                   # les pires (1-Q)% des cas
    es = -queue.mean() if len(queue) else var
    return {
        "var": var, "es": es,
        "moyenne_pnl": pnls.mean(),
        "ecart_type_pnl": pnls.std(),
        "min_pnl": pnls.min(), "max_pnl": pnls.max(),
        "pnls": pnls,
    }


def main():
    df, spot = charger_portefeuille()
    print(f"[risque] spot de référence : {spot}")
    print(f"[risque] {len(df)} positions chargées\n")

    detail, agregat = greeks_portefeuille(df, spot)
    print("=== Détail par position (Greeks monétisés) ===")
    print(detail.to_string(index=False))
    print("\n=== Greeks agrégés du portefeuille ===")
    print(f"  Valeur totale : {agregat['valeur']:>12.2f} EUR")
    print(f"  Delta         : {agregat['delta']:>12.2f}  (€ par point de spot)")
    print(f"  Gamma         : {agregat['gamma']:>12.4f}")
    print(f"  Vega          : {agregat['vega']:>12.2f}  (€ par point de vol)")
    print(f"  Theta/jour    : {agregat['theta_jour']:>12.2f}  (€ par jour)")

    print("\n=== Greeks CASH / monétisés (Éq. 17-18 du PDF) ===")
    print(f"  Delta cash    : {agregat['delta_cash']:>14.2f}  (dollar delta : € de sous-jacent équivalents)")
    print(f"  Gamma cash    : {agregat['gamma_cash']:>14.2f}  (dollar gamma, Éq. 17 : gamma·S²·mult)")
    print(f"  Vega cash     : {agregat['vega_cash']:>14.2f}  (dollar vega, Éq. 18 : € par point de vol)")
    print(f"  Theta cash/j  : {agregat['theta_cash_jour']:>14.2f}  (€ par jour)")

    print("\n=== P&L sous scénarios (FULL REPRICING — la vérité) ===")
    print("    lignes = choc spot | colonnes = choc vol (points)\n")
    table = pnl_scenarios(df, spot, agregat["valeur"])
    print(table.to_string())

    print("\n=== P&L sous scénarios (APPROXIMATION GREEKS — Taylor) ===")
    table_greeks = pnl_greeks(agregat, spot)
    print(table_greeks.to_string())

    print("\n=== ÉCART entre les deux (full - greeks) ===")
    print("    petit au centre, grossit aux extrêmes : c'est la convexité que Taylor rate")
    ecart = (table - table_greeks).round(2)
    print(ecart.to_string())

    # Pire perte de la grille (esprit "worst-case" de l'étape 12)
    pire = table.min().min()
    ou = table.stack().idxmin()
    print(f"\n[risque] pire perte (full repricing) : {pire:.2f} EUR "
          f"(spot {ou[0]}, vol {ou[1]})")

    # --- VaR / ES paramétriques (Monte Carlo + full repricing) ---
    print(f"\n=== VaR & ES paramétriques ({N_SIMULATIONS} simulations, "
          f"horizon {HORIZON_JOURS}j, confiance {QUANTILE:.0%}) ===")
    print(f"    Hypothèses : sigma_spot/jour = {SIGMA_SPOT_JOUR:.1%}, "
          f"sigma_vol/jour = {SIGMA_VOL_JOUR:.3f}, "
          f"corr(spot,vol) = {CORREL_SPOT_VOL}\n")
    metr = var_es_montecarlo(df, spot, agregat["valeur"])
    print(f"  VaR {QUANTILE:.0%}        : {metr['var']:>10.2f} EUR  "
          f"(perte max avec {QUANTILE:.0%} de confiance)")
    print(f"  ES  {QUANTILE:.0%}        : {metr['es']:>10.2f} EUR  "
          f"(perte moyenne dans les {(1-QUANTILE):.0%} pires cas)")
    print(f"  P&L moyen      : {metr['moyenne_pnl']:>10.2f} EUR")
    print(f"  Écart-type P&L : {metr['ecart_type_pnl']:>10.2f} EUR")
    print(f"  Plage simulée  : [{metr['min_pnl']:.2f} ; {metr['max_pnl']:.2f}] EUR")


if __name__ == "__main__":
    main()

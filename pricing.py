# pricing.py — Étapes 8 & 10 du framework : NOTRE moteur de calcul.
# Black-Scholes (prix), les Greeks, et le solveur de volatilité implicite.
# Ce fichier NE SE CONNECTE À RIEN. Il calcule, c'est tout. Il est testable seul.

import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq

import config

SIGMA_MIN, SIGMA_MAX = config.SIGMA_MIN, config.SIGMA_MAX   # bornes du solveur (config.py)


def _d1_d2(S, K, T, r, q, sigma):
    """Les deux termes d1 et d2 de Black-Scholes (Éq. 8-9 du PDF)."""
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return d1, d2


def prix_bs(S, K, T, r, q, sigma, cp):
    """Prix Black-Scholes. cp = +1 pour un call, -1 pour un put (Éq. 10-11)."""
    if T <= 0 or sigma <= 0:
        return max(cp * (S - K), 0.0)          # valeur intrinsèque à l'échéance
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    if cp == 1:
        return S * np.exp(-q*T) * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)
    else:
        return K * np.exp(-r*T) * norm.cdf(-d2) - S * np.exp(-q*T) * norm.cdf(-d1)


def greeks(S, K, T, r, q, sigma, cp):
    """Les sensibilités (Éq. 13-16). vega par point de vol, theta par jour."""
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    pdf = norm.pdf(d1)
    delta = np.exp(-q*T) * (norm.cdf(d1) if cp == 1 else norm.cdf(d1) - 1)
    gamma = np.exp(-q*T) * pdf / (S * sigma * np.sqrt(T))
    vega = S * np.exp(-q*T) * pdf * np.sqrt(T) / 100.0        # pour +1 point de vol
    theta_an = (-S * np.exp(-q*T) * pdf * sigma / (2*np.sqrt(T))
                - cp * r * K * np.exp(-r*T) * norm.cdf(cp*d2)
                + cp * q * S * np.exp(-q*T) * norm.cdf(cp*d1))
    return {"delta": delta, "gamma": gamma,
            "vega": vega, "theta_jour": theta_an / 365.0}


def vol_implicite(prix_marche, S, K, T, r, q, cp):
    """
    Étape 8 : on INVERSE le prix pour trouver la vol. Méthode de Brent (robuste).
    Renvoie (sigma, statut) — jamais un NaN muet : toujours un diagnostic.
    """
    # borne basse : le prix ne peut pas être sous la valeur intrinsèque actualisée
    intrinseque = max(cp * (S*np.exp(-q*T) - K*np.exp(-r*T)), 0.0)
    if prix_marche < intrinseque - 1e-6:
        return np.nan, "rejet: prix < valeur intrinseque"
    f = lambda sig: prix_bs(S, K, T, r, q, sig, cp) - prix_marche
    try:
        if f(SIGMA_MIN) * f(SIGMA_MAX) > 0:
            return np.nan, "rejet: prix hors bornes de vol"
        sigma = brentq(f, SIGMA_MIN, SIGMA_MAX, xtol=config.SOLVEUR_XTOL,
                       maxiter=config.SOLVEUR_MAX_ITER)
        return sigma, "ok"
    except Exception as e:
        return np.nan, f"echec solveur: {e}"


# --- Test direct : on vérifie notre solveur contre l'IV d'IBKR ---
if __name__ == "__main__":
    # On rejoue une ligne de notre capture : call 6025, close=214.5, spot=6229.43
    # échéance 20260619 ; on calcule T en années (~ jours/365)
    from datetime import date
    S = 6229.43
    K = 6225.0          # ATM : strike le plus proche du spot
    T = (date(2026, 6, 19) - date(2026, 6, 15)).days / 365.0
    r, q = config.TAUX_SANS_RISQUE, config.DIVIDENDE
    prix = 49.0         # <-- mets ici le close du call 6225 lu dans ton CSV data/
    cp = 1

    sigma, statut = vol_implicite(prix, S, K, T, r, q, cp)
    print(f"Notre IV calculée : {sigma:.4f}  ({statut})")
    print(f"IV d'IBKR (témoin): 0.2004")
    print(f"Prix re-calculé avec notre IV : {prix_bs(S,K,T,r,q,sigma,cp):.2f} (marché: {prix})")
    g = greeks(S, K, T, r, q, sigma, cp)
    print("Greeks :", {k: round(v, 4) for k, v in g.items()})
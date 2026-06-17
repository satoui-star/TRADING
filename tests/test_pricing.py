# test_pricing.py — Tests UNITAIRES du moteur (étape 8/10 + Partie IV.E du PDF).
# Identités de pricing, cas limites, aller-retour du solveur, Greeks analytiques
# vs différences finies, et états d'erreur structurés (jamais de NaN muet).

import numpy as np
import pytest

import config
from pricing import prix_bs, greeks, vol_implicite, _d1_d2

S, K, T, r, q = 6289.88, 6300.0, 0.25, 0.03, 0.032


def test_parite_call_put():
    """C - P = S e^{-qT} - K e^{-rT} (parité, indépendante de sigma)."""
    sigma = 0.20
    c = prix_bs(S, K, T, r, q, sigma, +1)
    p = prix_bs(S, K, T, r, q, sigma, -1)
    attendu = S * np.exp(-q * T) - K * np.exp(-r * T)
    assert c - p == pytest.approx(attendu, abs=1e-6)


def test_valeur_intrinseque_a_echeance():
    """À T=0, le prix vaut exactement la valeur intrinsèque."""
    assert prix_bs(6400, 6300, 0.0, r, q, 0.2, +1) == pytest.approx(100.0)
    assert prix_bs(6200, 6300, 0.0, r, q, 0.2, +1) == pytest.approx(0.0)
    assert prix_bs(6200, 6300, 0.0, r, q, 0.2, -1) == pytest.approx(100.0)


def test_aller_retour_iv():
    """price(sigma) puis vol_implicite(price) doit retrouver sigma."""
    for sigma_vrai in (0.08, 0.15, 0.30, 0.75):
        for cp in (+1, -1):
            prix = prix_bs(S, K, T, r, q, sigma_vrai, cp)
            sigma, statut = vol_implicite(prix, S, K, T, r, q, cp)
            assert statut == "ok"
            assert sigma == pytest.approx(sigma_vrai, abs=1e-4)


def test_signes_des_greeks():
    """Delta call dans (0, e^{-qT}); gamma et vega strictement positifs."""
    g = greeks(S, K, T, r, q, 0.20, +1)
    assert 0.0 < g["delta"] < np.exp(-q * T)
    assert g["gamma"] > 0.0
    assert g["vega"] > 0.0
    gp = greeks(S, K, T, r, q, 0.20, -1)
    assert -np.exp(-q * T) < gp["delta"] < 0.0     # delta put négatif


def test_greeks_vs_differences_finies():
    """Greeks analytiques == différences finies centrées (mêmes que validation.py)."""
    sigma = 0.22
    g = greeks(S, K, T, r, q, sigma, +1)
    h = S * 1e-4
    p_up = prix_bs(S + h, K, T, r, q, sigma, +1)
    p_dn = prix_bs(S - h, K, T, r, q, sigma, +1)
    p_0 = prix_bs(S, K, T, r, q, sigma, +1)
    delta_fd = (p_up - p_dn) / (2 * h)
    gamma_fd = (p_up - 2 * p_0 + p_dn) / (h * h)
    dv = 0.005
    vega_fd = (prix_bs(S, K, T, r, q, sigma + dv, +1)
               - prix_bs(S, K, T, r, q, sigma - dv, +1)) / (2 * dv) / 100.0
    assert g["delta"] == pytest.approx(delta_fd, rel=1e-4)
    assert g["gamma"] == pytest.approx(gamma_fd, rel=1e-3)
    assert g["vega"] == pytest.approx(vega_fd, rel=1e-3)


def test_solveur_prix_sous_intrinseque():
    """Prix sous la valeur intrinsèque -> échec STRUCTURÉ (pas de NaN muet)."""
    intrinseque = max(+1 * (S * np.exp(-q * T) - K * np.exp(-r * T)), 0.0)
    sigma, statut = vol_implicite(intrinseque - 5.0, S, K, T, r, q, +1)
    assert np.isnan(sigma)
    assert "intrinseque" in statut


def test_solveur_prix_hors_bornes():
    """Prix impossible (au-dessus du spot actualisé) -> échec structuré borné."""
    sigma, statut = vol_implicite(2 * S, S, K, T, r, q, +1)
    assert np.isnan(sigma)
    assert "bornes" in statut


def test_d1_d2_coherents():
    """d2 = d1 - sigma sqrt(T) par construction."""
    d1, d2 = _d1_d2(S, K, T, r, q, 0.2)
    assert d1 - d2 == pytest.approx(0.2 * np.sqrt(T), abs=1e-12)

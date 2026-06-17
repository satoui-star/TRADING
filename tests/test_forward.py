# test_forward.py — Tests UNITAIRES du moteur de forward par parité (étape 6).
# On fabrique une chaîne synthétique dont le forward est CONNU et on vérifie :
#   - reconstruction exacte par parité ;
#   - rejet d'un candidat aberrant par z-score robuste (MAD) ;
#   - repli carry LABELLISÉ quand trop peu de paires.

import numpy as np
import pandas as pd
import pytest

import config
from forward import estimer_forward, zscore_robuste, forward_carry

R = config.TAUX_SANS_RISQUE
SPOT, T, ECH = 100.0, 0.25, 20260717


def _ligne(strike, type_opt, prix):
    """Une ligne brute : mid = prix (bid/ask serrés autour), close = prix."""
    return {"strike": float(strike), "type": type_opt,
            "bid": prix - 0.05, "ask": prix + 0.05, "close": prix}


def _chaine(forwards_par_strike):
    """Construit un df call+put par strike tel que F_i = K + e^{rT}(C-P).
    On fixe P=10 et on en déduit C = P + e^{-rT}(F_i - K)."""
    disc = np.exp(-R * T)
    lignes = []
    for K, F_i in forwards_par_strike.items():
        p = 10.0
        c = p + disc * (F_i - K)
        lignes.append(_ligne(K, "C", c))
        lignes.append(_ligne(K, "P", p))
    return pd.DataFrame(lignes)


def test_reconstruction_parite_exacte():
    """5 strikes propres, léger tilt -> forward retenu ~ 101, méthode parité."""
    fwd = {96: 100.98, 98: 100.99, 100: 101.00, 102: 101.01, 104: 101.02}
    res = estimer_forward(_chaine(fwd), SPOT, T, ECH)
    assert res.methode == "parite"
    assert res.n_retenus == 5
    assert res.forward == pytest.approx(101.0, abs=0.1)
    assert res.confiance > 0.5


def test_rejet_aberrant_mad():
    """Un strike avec F_i=130 doit être rejeté (z robuste > seuil), forward stable."""
    fwd = {96: 100.98, 98: 100.99, 99: 130.0, 100: 101.00, 102: 101.01, 104: 101.02}
    res = estimer_forward(_chaine(fwd), SPOT, T, ECH)
    assert res.methode == "parite"
    rejetes = [c for c in res.candidats if not c.retenu]
    assert any(abs(c.forward_estime - 130.0) < 1.0 for c in rejetes)
    assert res.forward == pytest.approx(101.0, abs=0.5)


def test_repli_carry_si_trop_peu():
    """Moins de FORWARD_MIN_CANDIDATS paires -> repli carry labellisé."""
    fwd = {98: 101.0, 102: 101.0}     # 2 < 3
    res = estimer_forward(_chaine(fwd), SPOT, T, ECH)
    assert res.methode == "repli_carry"
    assert res.qualite == "repli_carry"
    assert res.forward == pytest.approx(forward_carry(SPOT, T))
    assert res.confiance == 0.0


def test_zscore_robuste_degenere():
    """MAD nul (toutes valeurs égales) -> z = 0 partout (pas de division par 0)."""
    z, med, mad = zscore_robuste([5.0, 5.0, 5.0, 5.0])
    assert mad == 0.0
    assert np.all(z == 0.0)
    assert med == 5.0


def test_zscore_robuste_detecte_outlier():
    """Sur une série avec un outlier net, le z de l'outlier dépasse le seuil."""
    z, med, mad = zscore_robuste([10.0, 10.1, 9.9, 10.05, 50.0])
    assert mad > 0
    assert abs(z[-1]) > config.FORWARD_MAX_ZSCORE

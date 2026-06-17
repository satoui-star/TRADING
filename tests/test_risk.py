# test_risk.py — Tests UNITAIRES du risque (étapes 11-12).
#   - les agrégats RÉCONCILIENT exactement avec la somme des lignes (PDF Step 11) ;
#   - la grille de scénarios est complète et la cellule centrale (0,0) vaut 0 ;
#   - la VaR Monte-Carlo est REPRODUCTIBLE (graine fixe) et ES >= VaR.

import numpy as np
import pandas as pd
import pytest

import config
from risque import (greeks_portefeuille, valeur_portefeuille, pnl_scenarios,
                    var_es_montecarlo, CHOCS_SPOT, CHOCS_VOL)

SPOT = 6289.88


def _portefeuille():
    """Short straddle ATM synthétique, déjà enrichi en IV / T / cp."""
    return pd.DataFrame([
        {"echeance": 20260717, "strike": 6250.0, "type": "C", "quantite": -1,
         "iv_calculee": 0.1374, "T": 0.0822, "cp": 1},
        {"echeance": 20260717, "strike": 6250.0, "type": "P", "quantite": -1,
         "iv_calculee": 0.1374, "T": 0.0822, "cp": -1},
    ])


def test_agregats_reconcilient_aux_lignes():
    """Chaque Greek agrégé = somme des Greeks de ligne (réconciliation Step 11)."""
    detail, agregat = greeks_portefeuille(_portefeuille(), SPOT)
    for col in ["valeur", "delta", "gamma", "vega", "theta_jour",
                "delta_cash", "gamma_cash", "vega_cash", "theta_cash_jour"]:
        assert agregat[col] == pytest.approx(detail[col].sum(), abs=1e-6)


def test_grille_scenarios_complete():
    """La grille a la bonne forme et la cellule sans choc (0%, +0.00) vaut 0."""
    df = _portefeuille()
    base = valeur_portefeuille(df, SPOT, 0.0, 0.0)
    table = pnl_scenarios(df, SPOT, base)
    assert table.shape == (len(CHOCS_SPOT), len(CHOCS_VOL))
    assert table.loc["+0%", "+0.00"] == pytest.approx(0.0, abs=1e-6)
    assert table.isna().sum().sum() == 0     # aucune cellule manquante


def test_var_reproductible_et_es_superieure():
    """Même graine -> même VaR ; et ES >= VaR (la queue est au moins aussi sévère)."""
    df = _portefeuille()
    base = valeur_portefeuille(df, SPOT, 0.0, 0.0)
    m1 = var_es_montecarlo(df, SPOT, base)
    m2 = var_es_montecarlo(df, SPOT, base)
    assert m1["var"] == m2["var"]
    assert m1["es"] == m2["es"]
    assert m1["es"] + 1e-9 >= m1["var"]


def test_repricing_choc_spot_monotone_pour_short_gamma():
    """Un short straddle perd quand le spot bouge fort dans un sens ou l'autre."""
    df = _portefeuille()
    base = valeur_portefeuille(df, SPOT, 0.0, 0.0)
    v_up = valeur_portefeuille(df, SPOT, 0.10, 0.0)
    v_dn = valeur_portefeuille(df, SPOT, -0.10, 0.0)
    assert v_up - base < 0
    assert v_dn - base < 0

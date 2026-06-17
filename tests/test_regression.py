# test_regression.py — Test de RÉGRESSION (Partie IV.E + Partie XVI du PDF).
# On rejoue la chaîne analytique (mêmes fonctions que la prod) sur le brut
# committé dans data/ et on vérifie des INVARIANTS stables :
#   - le solveur converge sur la grande majorité des options ;
#   - notre IV colle à celle d'IBKR (témoin) ;
#   - le QC ne garde que des options OTM ;
#   - le forward est reconstruit par parité sur les maturités liquides ;
#   - la surface ne présente pas d'arbitrage calendaire grossier.
# Déterministe : la date de valorisation vient de capture_ts, pas de l'horloge.

from datetime import datetime
import numpy as np
import pandas as pd
import pytest

import config
import calcul_iv
import qc as qc_mod
import forward as fwd
import surface as surf


@pytest.fixture(scope="module")
def brut():
    fichiers = sorted(config.DATA.glob(f"brut_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        pytest.skip("aucun brut_ committé dans data/ pour la régression")
    return pd.read_csv(fichiers[-1])


@pytest.fixture(scope="module")
def ref(brut):
    cap = str(brut["capture_ts"].iloc[0]).split("_")[0]
    return datetime.strptime(cap, "%Y%m%d").date()


def test_solveur_converge_majoritairement(brut, ref):
    df_iv = calcul_iv.calculer_iv_chaine(brut, jour_reference=ref)
    taux = (df_iv["statut"] == "ok").mean()
    assert taux > 0.90, f"taux de convergence trop bas : {taux:.2%}"


def test_iv_proche_ibkr(brut, ref):
    df_iv = calcul_iv.calculer_iv_chaine(brut, jour_reference=ref)
    ecarts = df_iv["ecart_iv"].dropna().abs()
    assert ecarts.median() < 0.02, f"écart médian vs IBKR trop large : {ecarts.median():.4f}"


def test_qc_ne_garde_que_otm(brut, ref):
    df_iv = calcul_iv.calculer_iv_chaine(brut, jour_reference=ref)
    spot = float(brut["spot"].iloc[0])
    df_qc = qc_mod.appliquer_qc(df_iv, spot)
    gardes = df_qc[df_qc["gardee"]]
    assert len(gardes) > 0
    est_otm = ((gardes["strike"] < spot) & (gardes["type"] == "P")) | \
              ((gardes["strike"] >= spot) & (gardes["type"] == "C"))
    assert est_otm.all(), "des options ITM ont été retenues par le QC"


def test_forward_par_parite_sur_maturites_liquides(brut, ref):
    spot = float(brut["spot"].iloc[0])
    resultats, courbe, _ = fwd.construire_courbe(brut, spot, ref)
    assert len(resultats) > 0
    parites = [r for r in resultats.values() if r.methode == "parite"]
    assert len(parites) >= 1
    # sur ce brut, toutes les maturités sont liquides -> parité haute confiance
    assert all(r.confiance > 0.5 for r in parites)


def test_pas_d_arbitrage_calendaire(brut, ref):
    spot = float(brut["spot"].iloc[0])
    df_iv = calcul_iv.calculer_iv_chaine(brut, jour_reference=ref)
    df_qc = qc_mod.appliquer_qc(df_iv, spot)
    df_qc["spot"] = spot
    courbe_fwd = fwd.construire_courbe(brut, spot, ref)[0]
    tranches, _ = surf.preparer_tranches(df_qc, spot, ref=ref, courbe_fwd=courbe_fwd)
    cal = [t for t in tranches if t["methode"] in ("svi", "spline")]
    assert len(cal) >= 2
    kk = np.linspace(min(t["k"].min() for t in cal),
                     max(t["k"].max() for t in cal), config.SURFACE_N_GRILLE_K)
    diag = surf.diagnostic_calendaire(cal, kk)
    assert diag["pct"] < 1.0, f"trop de violations calendaires : {diag['pct']:.1f}%"


def test_determinisme_iv(brut, ref):
    """Rejouer le calcul sur le même brut + même date donne exactement la même IV."""
    a = calcul_iv.calculer_iv_chaine(brut, jour_reference=ref)["iv_calculee"]
    b = calcul_iv.calculer_iv_chaine(brut, jour_reference=ref)["iv_calculee"]
    pd.testing.assert_series_equal(a, b)

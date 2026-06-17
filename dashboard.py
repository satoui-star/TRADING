# dashboard.py — Application Streamlit pour la plateforme de volatilité ESTX50.
# Lance avec : streamlit run dashboard.py
#
# Trois onglets fidèles au cahier des charges du prof :
#   1. Données — l'état du marché
#   2. Risque — le simulateur de positions et la mesure du risque
#   3. Daily — l'historique au fil du temps
#
# Philosophie : chaque écran s'explique tout seul (titres, sous-titres, légendes
# pédagogiques). Tout le calcul est délégué aux modules métier (pricing, qc,
# risque, historique). Ce fichier ne fait QUE de l'orchestration et de la
# présentation. C'est l'étape 15 du framework du prof.

from datetime import datetime, date
import subprocess
import sys

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import griddata

import config
from pricing import prix_bs, greeks
import strategies as strat
import risque as moteur_risque
import surface as surf   # nappe SVI calibrée + forward par parité (étapes 6 & 9)
import validation as valid  # suite de validation + anomalies (étape 14)


# ======================================================================
# CONFIG GLOBALE STREAMLIT — thème sombre type GitHub / terminal pro
# ======================================================================
st.set_page_config(
    page_title="ESTX50 Vol Platform",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Palette : fond très sombre, un seul accent vert pétrole, texte gris clair
COULEUR_FOND = "#0d1117"
COULEUR_ACCENT = "#10b981"
COULEUR_TEXTE = "#e6edf3"
COULEUR_DISCRET = "#8b949e"
COULEUR_HAUSSE = "#10b981"
COULEUR_BAISSE = "#ef4444"

st.markdown(f"""
<style>
.stApp {{ background-color: {COULEUR_FOND}; color: {COULEUR_TEXTE}; }}
body, [class*="css"] {{ font-family: -apple-system, system-ui, "Inter", sans-serif; }}
h1, h2, h3, h4 {{ color: {COULEUR_TEXTE}; font-weight: 600; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 0; border-bottom: 1px solid #21262d; }}
.stTabs [data-baseweb="tab"] {{ padding: 12px 24px; color: {COULEUR_DISCRET}; }}
.stTabs [aria-selected="true"] {{ color: {COULEUR_ACCENT}; border-bottom: 2px solid {COULEUR_ACCENT}; }}
[data-testid="stMetricValue"] {{ font-size: 1.8rem; color: {COULEUR_TEXTE}; }}
[data-testid="stMetricLabel"]  {{ color: {COULEUR_DISCRET}; font-size: 0.85rem; }}
[data-testid="stMetricDelta"]  {{ font-size: 0.8rem; }}
.bandeau-contexte {{
    background: #161b22; border-left: 3px solid {COULEUR_ACCENT};
    padding: 10px 16px; margin-bottom: 24px; border-radius: 4px;
    color: {COULEUR_DISCRET}; font-size: 0.9rem; line-height: 1.5;
}}
.bandeau-contexte b {{ color: {COULEUR_TEXTE}; }}
.legende {{
    color: {COULEUR_DISCRET}; font-size: 0.85rem; line-height: 1.5;
    margin-top: -10px; margin-bottom: 16px; font-style: italic;
}}
.kpi-bloc {{
    background: #161b22; padding: 16px; border-radius: 6px;
    border: 1px solid #21262d;
}}
</style>
""", unsafe_allow_html=True)


# ======================================================================
# HELPERS — chargement et calculs partagés (cachés pour éviter de recalculer)
# ======================================================================
def _maturite(echeance, ref=None):
    ech = datetime.strptime(str(int(echeance)), "%Y%m%d").date()
    return max((ech - (ref or date.today())).days, 0) / 365.0


def _date_capture(df, repli=None):
    """Date de valorisation = jour de CAPTURE (déterminisme), lue dans 'capture_ts'.
    Repli explicite sur 'repli' (ici la date du nom de fichier) ou aujourd'hui."""
    if df is not None and "capture_ts" in df.columns and len(df):
        try:
            return datetime.strptime(str(df["capture_ts"].iloc[0]).split("_")[0],
                                     "%Y%m%d").date()
        except (ValueError, TypeError):
            pass
    return repli or date.today()


@st.cache_data(show_spinner=False)
def charger_dernier_qc():
    fichiers = sorted(config.CALCUL.glob(f"qc_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        return None, None, None, None
    f = fichiers[-1]
    df = pd.read_csv(f)
    spot = _recuperer_spot(df, f)
    nappe = (df[df["gardee"]]
             .groupby(["echeance", "strike"], as_index=False)["iv_calculee"]
             .median())
    timestamp_capture = "_".join(f.stem.split("_")[-2:])  # AAAAMMJJ_HHMMSS
    return df, nappe, spot, timestamp_capture


def _recuperer_spot(df, fichier_qc):
    """Le spot peut être dans le QC, sinon on le lit dans le brut correspondant."""
    if "spot" in df.columns and not pd.isna(df["spot"].iloc[0]):
        return float(df["spot"].iloc[0])
    # Repli : on lit le brut associé (même horodatage)
    horodatage = "_".join(fichier_qc.stem.split("_")[-2:])
    brut = config.DATA / f"brut_{config.SYMBOLE}_{horodatage}.csv"
    if brut.exists():
        df_brut = pd.read_csv(brut)
        if "spot" in df_brut.columns:
            return float(df_brut["spot"].iloc[0])
    # Dernier repli : impossible de trouver le spot
    return None


@st.cache_data(show_spinner=False)
def charger_capture_precedente():
    """Pour calculer les variations entre la dernière capture et celle d'avant."""
    fichiers = sorted(config.CALCUL.glob(f"qc_{config.SYMBOLE}_*.csv"))
    if len(fichiers) < 2:
        return None, None
    f = fichiers[-2]
    df = pd.read_csv(f)
    spot = _recuperer_spot(df, f)
    if spot is None:
        return None, None
    nappe = (df[df["gardee"]]
             .groupby(["echeance", "strike"], as_index=False)["iv_calculee"]
             .median())
    return nappe, spot


def vol_atm_moyenne(nappe, spot):
    """IV moyenne au strike ATM de chaque échéance."""
    vols = []
    for ech, sous in nappe.groupby("echeance"):
        idx = (sous["strike"] - spot).abs().idxmin()
        vols.append(sous.loc[idx, "iv_calculee"])
    return float(np.mean(vols)) if vols else None


def skew_indicateur(nappe, spot):
    """Skew = vol(K=spot-5%) - vol(K=spot+5%), moyenné sur les échéances."""
    skews = []
    for ech, sous in nappe.groupby("echeance"):
        k_bas, k_haut = spot * 0.95, spot * 1.05
        try:
            v_bas = sous.iloc[(sous["strike"] - k_bas).abs().argsort()[:1]]["iv_calculee"].iloc[0]
            v_haut = sous.iloc[(sous["strike"] - k_haut).abs().argsort()[:1]]["iv_calculee"].iloc[0]
            skews.append(v_bas - v_haut)
        except IndexError:
            continue
    return float(np.mean(skews)) if skews else None


def trouver_strike_atm(nappe, spot, echeance):
    candidats = nappe[nappe["echeance"] == echeance]["strike"].values
    if len(candidats) == 0:
        return None
    return float(candidats[np.argmin(np.abs(candidats - spot))])


@st.cache_data(show_spinner=False)
def calculer_validation(ts_capture):
    """Lance la suite de validation (étape 14) sur la dernière capture.
    Mise en cache par horodatage : on ne ré-inverse pas à chaque interaction.
    Renvoie (rapport: list[dict], metriques: dict) — picklable pour le cache."""
    fichiers = sorted(config.CALCUL.glob(f"qc_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        return [], {}
    qc = pd.read_csv(fichiers[-1])
    cap = valid._capture_ts(qc)
    ref = valid._date_capture(qc)
    spot_v = float(qc["spot"].iloc[0]) if "spot" in qc.columns else None

    brut = None
    if cap:
        bp = config.DATA / f"brut_{config.SYMBOLE}_{cap}.csv"
        if bp.exists():
            brut = pd.read_csv(bp)
    if spot_v is None and brut is not None:
        spot_v = float(brut["spot"].iloc[0])
    if spot_v is None:
        return [], {}

    courbe_fwd = surf.courbe_forward(qc, spot_v, ref)
    tranches, _ = surf.preparer_tranches(qc, spot_v, ref=ref, courbe_fwd=courbe_fwd)

    histo_path = config.CALCUL / f"qc_metrics_history_{config.SYMBOLE}.csv"
    histo = pd.read_csv(histo_path) if histo_path.exists() else None
    if histo is not None and cap and "capture_ts" in histo.columns:
        histo = histo[histo["capture_ts"].astype(str) != cap]

    from dataclasses import asdict
    res, metriques = valid.lancer_suite(qc, brut, spot_v, ref, courbe_fwd, tranches, histo)
    return [asdict(c) for c in res], metriques


@st.cache_data(show_spinner=False)
def charger_historique_qc():
    """Historique des métriques QC pour le suivi de tendance (étape 14)."""
    p = config.CALCUL / f"qc_metrics_history_{config.SYMBOLE}.csv"
    return pd.read_csv(p) if p.exists() else None


# ======================================================================
# BANDEAU DE CONTEXTE PERMANENT — qui dit ce que regarde l'utilisateur
# ======================================================================
df_qc, nappe, spot, ts_capture = charger_dernier_qc()
nappe_prec, spot_prec = charger_capture_precedente()

# Déterminisme : date de valorisation = jour de CAPTURE (lu dans capture_ts du QC),
# utilisée partout pour calculer T. Toujours définie, même sans capture.
date_reference = _date_capture(df_qc)

st.markdown("# ESTX50 Volatility Platform")

if ts_capture:
    date_capture = datetime.strptime(ts_capture, "%Y%m%d_%H%M%S")
    # date_reference (jour de capture) est déjà calculée plus haut, déterministe.
    bandeau = (
        f"<b>Sous-jacent</b> : Euro STOXX 50 (indice européen large cap) — "
        f"<b>Source</b> : Interactive Brokers, données différées EUREX — "
        f"<b>Dernière capture</b> : {date_capture.strftime('%d/%m/%Y à %H:%M')} — "
        f"<b>Pipeline</b> : capture des prix bruts → inversion Black-Scholes → "
        f"filtre OTM (parité call-put) → nappe → moteur de risque"
    )
else:
    bandeau = "<b>Aucune donnée captée.</b> Va dans l'onglet Daily et lance une capture."
st.markdown(f"<div class='bandeau-contexte'>{bandeau}</div>", unsafe_allow_html=True)


# ======================================================================
# ONGLETS
# ======================================================================
onglet1, onglet2, onglet3, onglet4 = st.tabs([
    "1. Données — état du marché",
    "2. Risque — positions et P&L",
    "3. Daily — historique",
    "4. Validation — QA & anomalies",
])


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ ONGLET 1 — DONNÉES                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
with onglet1:
    if nappe is None:
        st.warning("Aucune capture disponible. Va dans l'onglet Daily.")
    else:
        # ── Bloc KPI de santé du marché ──
        st.markdown("### Santé du marché")
        st.markdown("<div class='legende'>Indicateurs clés calculés sur la dernière capture, "
                    "avec variation depuis la capture précédente.</div>",
                    unsafe_allow_html=True)

        # Calcul des variations
        vol_atm = vol_atm_moyenne(nappe, spot)
        skew = skew_indicateur(nappe, spot)
        if spot_prec is not None:
            d_spot = spot - spot_prec
            d_spot_pct = d_spot / spot_prec * 100
            vol_prec = vol_atm_moyenne(nappe_prec, spot_prec)
            d_vol = (vol_atm - vol_prec) * 100 if vol_prec else None
            skew_prec = skew_indicateur(nappe_prec, spot_prec)
            d_skew = (skew - skew_prec) * 100 if skew_prec is not None else None
        else:
            d_spot_pct = d_vol = d_skew = None

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("Spot ESTX50", f"{spot:.2f}",
                      delta=f"{d_spot_pct:+.2f}%" if d_spot_pct is not None else None)
        with c2:
            st.metric("Vol ATM moyenne", f"{vol_atm*100:.2f}%",
                      delta=f"{d_vol:+.2f} pts" if d_vol is not None else None,
                      delta_color="inverse")
        with c3:
            st.metric("Skew (vol 95% - 105%)", f"{skew*100:+.2f} pts",
                      delta=f"{d_skew:+.2f} pts" if d_skew is not None else None,
                      delta_color="inverse",
                      help="Mesure la pente du smile. Plus c'est grand, plus le marché "
                           "valorise cher les puts OTM (peur de la baisse).")
        with c4:
            st.metric("Options liquides", f"{len(nappe)}",
                      help="Nombre d'options retenues par le QC (OTM uniquement).")
        with c5:
            n_ech = nappe["echeance"].nunique()
            st.metric("Échéances", f"{n_ech}",
                      help="Nombre de maturités captées.")

        st.divider()

        # ── Nappe 3D ──
        st.markdown("### Nappe de volatilité 3D")
        st.markdown(
            "<div class='legende'>Surface des volatilités implicites en fonction du strike "
            "(axe X) et de la maturité (axe Y). Construite par inversion Black-Scholes des "
            "prix mid-market OTM. La hauteur représente l'IV : un relief élevé côté strikes "
            "bas (gauche) indique une peur de la baisse (skew). La nappe s'aplatit avec la "
            "maturité (structure de terme).</div>",
            unsafe_allow_html=True
        )

        # ── Nappe 3D (surface SVI calibrée) ──
        st.markdown("### Nappe de volatilité 3D")
        st.markdown(
            "<div class='legende'>Surface des volatilités implicites <b>calibrée par SVI</b> "
            "(une tranche par maturité), ajustée sur les points OTM en log-moneyness "
            "k = ln(K/F), avec un forward F reconstruit par <b>parité call-put</b>. "
            "Un relief côté strikes bas (gauche) traduit la peur de la baisse (skew) ; "
            "la nappe s'aplatit avec la maturité.</div>",
            unsafe_allow_html=True
        )

        # Calibration SVI via le MÊME code que surface.py (un seul chemin de code,
        # principe du PDF). Forward de parité depuis le brut de la même capture.
        courbe_fwd = surf.courbe_forward(df_qc, spot, date_reference)
        tranches_svi, _ = surf.preparer_tranches(df_qc, spot, ref=date_reference,
                                                  courbe_fwd=courbe_fwd)
        cal = [t for t in tranches_svi if t["methode"] in ("svi", "spline")]

        diag_cal = {"violations": 0, "total": 0, "pct": 0.0}
        if len(cal) >= 2:
            kk = np.linspace(min(t["k"].min() for t in cal),
                             max(t["k"].max() for t in cal), 50)
            diag_cal = surf.diagnostic_calendaire(cal, kk)

        # nappe_T (points médians + T) : encore utilisé par le tableau QC plus bas
        nappe_T = nappe.copy()
        nappe_T["T"] = nappe_T["echeance"].apply(lambda e: _maturite(e, date_reference))

        col_nappe, col_droite = st.columns([2, 1])

        with col_nappe:
            if cal:
                cal_tries = sorted(cal, key=lambda t: t["T"])
                sx = np.linspace(min(t["strikes_obs"].min() for t in cal_tries),
                                 max(t["strikes_obs"].max() for t in cal_tries), 60)
                T_vals, Z = [], []
                for t in cal_tries:
                    k = np.log(sx / t["forward"])
                    w = surf.evaluer_tranche(t, k)
                    Z.append(np.sqrt(np.clip(w, 0, None) / t["T"]))
                    T_vals.append(t["T"])
                fig3d = go.Figure(data=[go.Surface(
                    x=sx, y=T_vals, z=np.array(Z),
                    colorscale="Viridis", colorbar=dict(title="IV"))])
                fig3d.update_layout(
                    template="plotly_dark", height=500,
                    scene=dict(xaxis_title="Strike", yaxis_title="T (années)",
                               zaxis_title="IV (SVI)", bgcolor=COULEUR_FOND),
                    paper_bgcolor=COULEUR_FOND, margin=dict(l=0, r=0, t=20, b=0),
                )
                st.plotly_chart(fig3d, use_container_width=True)

                meth_fwd = ("parité" if any(t["forward_methode"] == "parite" for t in cal)
                            else "carry (repli)")
                arb = ("aucune violation" if diag_cal["violations"] == 0
                       else f"{diag_cal['violations']}/{diag_cal['total']} violations")
                st.markdown(
                    f"<div class='legende'>Forward : <b>{meth_fwd}</b> &nbsp;·&nbsp; "
                    f"calibration : <b>{sum(1 for t in cal if t['methode']=='svi')} SVI / "
                    f"{sum(1 for t in cal if t['methode']=='spline')} spline</b> &nbsp;·&nbsp; "
                    f"arbitrage calendaire : <b>{arb}</b></div>",
                    unsafe_allow_html=True)
            else:
                st.info("Pas assez de points pour calibrer une surface SVI.")

        with col_droite:
            # ── Densité d'options par strike (l'histogramme du collègue) ──
            st.markdown("#### Densité par strike")
            st.markdown(
                "<div class='legende'>Combien d'options retenues à chaque strike. "
                "Les barres hautes indiquent les strikes les plus liquides.</div>",
                unsafe_allow_html=True
            )
            dens = nappe.groupby("strike").size().reset_index(name="n")
            fig_dens = go.Figure(go.Bar(x=dens["strike"], y=dens["n"],
                                        marker_color=COULEUR_ACCENT))
            fig_dens.add_vline(x=spot, line_dash="dash", line_color="orange",
                               annotation_text="spot")
            fig_dens.update_layout(template="plotly_dark", height=230,
                                   paper_bgcolor=COULEUR_FOND,
                                   plot_bgcolor=COULEUR_FOND,
                                   margin=dict(l=10, r=10, t=10, b=30),
                                   xaxis_title="Strike", yaxis_title="N")
            st.plotly_chart(fig_dens, use_container_width=True)

            # ── Distribution des IV ──
            st.markdown("#### Distribution des IV")
            st.markdown(
                "<div class='legende'>Histogramme de toutes les IV de la nappe. "
                "Montre où se concentre la volatilité du marché.</div>",
                unsafe_allow_html=True
            )
            fig_iv = go.Figure(go.Histogram(x=nappe["iv_calculee"] * 100,
                                            nbinsx=25,
                                            marker_color=COULEUR_ACCENT,
                                            opacity=0.85))
            fig_iv.update_layout(template="plotly_dark", height=230,
                                 paper_bgcolor=COULEUR_FOND,
                                 plot_bgcolor=COULEUR_FOND,
                                 margin=dict(l=10, r=10, t=10, b=30),
                                 xaxis_title="IV (%)", yaxis_title="N")
            st.plotly_chart(fig_iv, use_container_width=True)

        st.divider()

        # ── Smiles : points réels vs ajustement SVI (revue opérateur, PDF étape 9) ──
        with st.expander("Smiles par échéance — points réels vs ajustement SVI"):
            st.markdown(
                "<div class='legende'>Pour chaque maturité : les <b>points OTM retenus</b> "
                "(ronds) et la <b>courbe SVI ajustée</b> (trait). L'écart points/courbe est le "
                "diagnostic de qualité de calibration. Le smile s'aplatit avec la maturité.</div>",
                unsafe_allow_html=True
            )
            couleurs = ["#10b981", "#3b82f6", "#f59e0b", "#ef4444", "#a855f7",
                        "#06b6d4", "#84cc16", "#ec4899"]
            figs = go.Figure()
            for i, t in enumerate(sorted(cal, key=lambda x: x["T"])):
                c = couleurs[i % len(couleurs)]
                figs.add_trace(go.Scatter(
                    x=t["strikes_obs"], y=t["sigma_obs"], mode="markers",
                    marker=dict(color=c, size=7),
                    name=f"{int(t['echeance'])} — points", legendgroup=str(i)))
                k_fit = np.linspace(t["k"].min(), t["k"].max(), 100)
                sig_fit = np.sqrt(np.clip(surf.evaluer_tranche(t, k_fit), 0, None) / t["T"])
                figs.add_trace(go.Scatter(
                    x=t["forward"] * np.exp(k_fit), y=sig_fit, mode="lines",
                    line=dict(color=c, width=2),
                    name=f"{int(t['echeance'])} — {t['methode'].upper()}",
                    legendgroup=str(i)))
            figs.add_vline(x=spot, line_dash="dash", line_color="grey",
                           annotation_text="spot")
            figs.update_layout(template="plotly_dark", height=420,
                               paper_bgcolor=COULEUR_FOND, plot_bgcolor=COULEUR_FOND,
                               xaxis_title="Strike", yaxis_title="IV")
            st.plotly_chart(figs, use_container_width=True)

        # ── Courbe de forward par parité (étape 6) ──
        if courbe_fwd:
            with st.expander("Forward par parité call-put (étape 6)"):
                st.markdown(
                    "<div class='legende'>Forward F reconstruit par parité pour chaque "
                    "maturité, le dividende implicite qui en découle, et un score de "
                    "confiance. Comparé au forward de carry théorique (q fixe).</div>",
                    unsafe_allow_html=True)
                fwd_rows = [{
                    "échéance": e, "T": round(r.T, 4),
                    "F parité": round(r.forward, 2),
                    "F carry": round(r.forward_carry, 2),
                    "écart (pts)": round(r.forward - r.forward_carry, 2),
                    "div. implicite": f"{r.dividende_implicite*100:.2f}%",
                    "candidats": f"{r.n_retenus}/{r.n_candidats}",
                    "confiance": round(r.confiance, 2),
                    "qualité": r.qualite, "méthode": r.methode,
                } for e, r in sorted(courbe_fwd.items(), key=lambda kv: kv[1].T)]
                st.dataframe(pd.DataFrame(fwd_rows), use_container_width=True,
                             hide_index=True)

        with st.expander("Tableau des points retenus par le QC"):
            st.dataframe(nappe_T.sort_values(["echeance", "strike"]),
                         use_container_width=True, hide_index=True)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ ONGLET 2 — RISQUE                                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
with onglet2:
    if nappe is None:
        st.warning("Aucune nappe disponible. Capture des données dans l'onglet Daily.")
        st.stop()

    st.markdown("### Simulateur de position")
    st.markdown(
        "<div class='legende'>Choisis une stratégie standard ou saisis tes positions à la "
        "main. Le moteur récupère l'IV correspondante dans la nappe, calcule les Greeks, "
        "le P&L scénario par revalorisation complète, et estime la VaR/ES par Monte Carlo.</div>",
        unsafe_allow_html=True
    )

    # ── Constructeur de position ──
    col_strat, col_param = st.columns([1, 2])
    with col_strat:
        mode = st.radio("Mode", ["Stratégie type", "Saisie manuelle"], horizontal=True)
        echeance_choisie = st.selectbox(
            "Échéance",
            sorted(nappe["echeance"].unique().tolist()),
            help="La maturité sur laquelle tu prends ta position."
        )
        strike_atm = trouver_strike_atm(nappe, spot, echeance_choisie)
        st.markdown(f"<div class='legende'>Spot {spot:.0f} — strike ATM le plus proche : "
                    f"<b style='color:{COULEUR_ACCENT}'>{strike_atm:.0f}</b></div>",
                    unsafe_allow_html=True)

    descriptions_strat = {
        "Long straddle (acheteur de vol)":  "Pari sur l'amplitude du mouvement, peu importe la direction. Gagne aux extrêmes, perd au centre.",
        "Short straddle (vendeur de vol)":  "L'inverse : pari que le marché va stagner. Petites primes encaissées, mais pertes potentielles importantes aux extrêmes.",
        "Long strangle":                    "Comme un straddle, mais avec des strikes OTM (moins cher). Il faut un mouvement plus fort pour gagner.",
        "Short strangle":                   "Vente de volatilité plus prudente que le short straddle, mais même profil de risque asymétrique.",
        "Iron condor (vendu)":              "Pari range : on encaisse une prime si le marché reste entre deux bornes. Profit et perte bornés.",
        "Long butterfly":                   "Pari pointu sur un niveau précis du spot à l'échéance. Coût faible, gain max si le spot termine pile au strike central.",
        "Risk reversal (haussier)":         "Position directionnelle quasi-gratuite : la prime du put vendu finance le call acheté. Risque de baisse non couvert.",
        "Call spread (haussier)":           "Pari à la hausse avec risque limité : on achète un call et on en revend un plus loin.",
        "Put spread (baissier)":            "Pari à la baisse avec risque limité : on achète un put et on en revend un plus loin.",
    }

    positions = None
    with col_param:
        if mode == "Stratégie type":
            nom_strat = st.selectbox("Type", list(strat.CATALOGUE.keys()))
            st.markdown(f"<div class='legende'>{descriptions_strat[nom_strat]}</div>",
                        unsafe_allow_html=True)
            fn = strat.CATALOGUE[nom_strat]
            kwargs = {"quantite": st.number_input("Quantité (lots)", 1, 100, 1)}
            if "ecart_strikes" in fn.__code__.co_varnames:
                kwargs["ecart_strikes"] = st.slider("Écart strikes", 25, 500, 100, 25)
            if "ecart_interne" in fn.__code__.co_varnames:
                kwargs["ecart_interne"] = st.slider("Écart interne", 25, 200, 50, 25)
                kwargs["ecart_externe"] = st.slider("Écart externe", 50, 500, 150, 25)
            if "largeur_ailes" in fn.__code__.co_varnames:
                kwargs["largeur_ailes"] = st.slider("Largeur ailes", 25, 300, 100, 25)
            if "largeur" in fn.__code__.co_varnames:
                kwargs["largeur"] = st.slider("Largeur spread", 25, 300, 100, 25)
            positions = fn(echeance_choisie, strike_atm, **kwargs)
        else:
            st.markdown(
                "<div class='legende'>Ajoute, modifie ou supprime des lignes. "
                "Quantité positive = long, négative = short.</div>",
                unsafe_allow_html=True
            )
            modele = pd.DataFrame([
                {"echeance": echeance_choisie, "strike": strike_atm, "type": "C", "quantite": 1},
            ])
            positions = st.data_editor(
                modele, num_rows="dynamic", use_container_width=True,
                column_config={"type": st.column_config.SelectboxColumn(options=["C", "P"])}
            )

    # ── Joindre les positions à la nappe ──
    df_pos = positions.merge(nappe, on=["echeance", "strike"], how="left")
    manquants = df_pos["iv_calculee"].isna().sum()
    if manquants:
        st.warning(f"{manquants} position(s) sans IV dans la nappe. "
                   "Vérifie que les strikes existent (consulte la densité dans l'onglet 1).")
        df_pos = df_pos.dropna(subset=["iv_calculee"])
    if len(df_pos) == 0:
        st.error("Aucune position évaluable.")
        st.stop()

    df_pos["T"] = df_pos["echeance"].apply(lambda e: _maturite(e, date_reference))
    df_pos["cp"] = np.where(df_pos["type"] == "C", 1, -1)

    detail, agregat = moteur_risque.greeks_portefeuille(df_pos, spot)

    st.divider()

    # ── Payoff + Greeks côte à côte ──
    col_payoff, col_greeks = st.columns([2, 1])

    with col_payoff:
        st.markdown("### Payoff à l'échéance")
        st.markdown(
            "<div class='legende'>Combien tu gagnes ou perds à l'expiration en fonction du "
            "spot final, après déduction du coût de la position (prime payée ou reçue).</div>",
            unsafe_allow_html=True
        )
        spot_grille = np.linspace(spot * 0.85, spot * 1.15, 200)
        payoff = np.zeros_like(spot_grille)
        for _, p in df_pos.iterrows():
            intrinseque = (np.maximum(spot_grille - p["strike"], 0) if p["type"] == "C"
                           else np.maximum(p["strike"] - spot_grille, 0))
            payoff += intrinseque * p["quantite"] * float(config.MULTIPLICATEUR)
        payoff -= agregat["valeur"]
        couleurs = np.where(payoff >= 0, COULEUR_HAUSSE, COULEUR_BAISSE)
        fig_pay = go.Figure()
        fig_pay.add_trace(go.Scatter(x=spot_grille, y=payoff,
                                     mode="lines", line=dict(color=COULEUR_ACCENT, width=2),
                                     fill="tozeroy",
                                     fillcolor="rgba(16,185,129,0.15)"))
        fig_pay.add_vline(x=spot, line_dash="dash", line_color="orange",
                          annotation_text="spot")
        fig_pay.add_hline(y=0, line_color="#30363d")
        fig_pay.update_layout(template="plotly_dark", height=350,
                              paper_bgcolor=COULEUR_FOND, plot_bgcolor=COULEUR_FOND,
                              xaxis_title="Spot à l'échéance", yaxis_title="P&L (€)",
                              margin=dict(l=10, r=10, t=20, b=40))
        st.plotly_chart(fig_pay, use_container_width=True)

    with col_greeks:
        st.markdown("### Sensibilités (Greeks)")
        st.markdown(
            "<div class='legende'>Comment ta position réagit à de petits mouvements de "
            "chaque facteur. Monétisés (€ par unité de variation).</div>",
            unsafe_allow_html=True
        )
        st.metric("Valeur de la position", f"{agregat['valeur']:+.0f} €",
                  help="Coût total si tu achètes (positif) ou prime encaissée (négatif).")
        st.metric("Delta — sensibilité au spot", f"{agregat['delta']:+.2f}",
                  help="€ gagnés par point de hausse du spot.")
        st.metric("Gamma — convexité", f"{agregat['gamma']:.4f}",
                  help="Variation du delta quand le spot bouge. Positif = tu gagnes quand ça bouge.")
        st.metric("Vega — sensibilité à la vol", f"{agregat['vega']:+.1f}",
                  help="€ gagnés par point d'IV en hausse.")
        st.metric("Theta — érosion temporelle", f"{agregat['theta_jour']:+.1f}",
                  help="€ gagnés (ou perdus) par jour qui passe.")

        with st.expander("Greeks cash (monétisés — format desk, Éq. 17-18 du PDF)"):
            st.markdown(
                "<div class='legende'>Les Greeks « dollar » : la même sensibilité, "
                "mais exprimée en euros à l'échelle de la position — le format qu'un desk "
                "lit en pratique. Pour le delta et le gamma on intègre le niveau du spot "
                "(× S et × S²) ; le vega et le theta sont déjà des montants en euros.</div>",
                unsafe_allow_html=True
            )
            st.metric("Delta cash (dollar delta)", f"{agregat['delta_cash']:+,.0f} €",
                      help="Exposition notionnelle : montant de sous-jacent auquel la position équivaut au 1er ordre.")
            st.metric("Gamma cash (dollar gamma)", f"{agregat['gamma_cash']:+,.0f} €",
                      help="Éq. 17 du PDF : gamma × S² × multiplicateur. La convexité, monétisée.")
            st.metric("Vega cash (dollar vega)", f"{agregat['vega_cash']:+,.0f} €",
                      help="Éq. 18 du PDF : € gagnés par point d'IV en hausse.")
            st.metric("Theta cash / jour", f"{agregat['theta_cash_jour']:+,.0f} €",
                      help="€ gagnés (ou perdus) par jour qui passe.")

    st.divider()

    # ── Grille de chocs ──
    st.markdown("### P&L sous scénarios (revalorisation complète)")
    st.markdown(
        "<div class='legende'>Chaque case montre le P&L si le spot et l'IV bougent selon "
        "les chocs indiqués. <b style='color:" + COULEUR_HAUSSE + "'>Vert</b> = gain, "
        "<b style='color:" + COULEUR_BAISSE + "'>rouge</b> = perte. La position est "
        "revalorisée intégralement par le moteur Black-Scholes (pas une approximation).</div>",
        unsafe_allow_html=True
    )
    table = moteur_risque.pnl_scenarios(df_pos, spot, agregat["valeur"])
    fig_heat = go.Figure(data=go.Heatmap(
        z=table.values, x=table.columns, y=table.index,
        colorscale=[[0, COULEUR_BAISSE], [0.5, "#21262d"], [1, COULEUR_HAUSSE]],
        zmid=0, text=table.values, texttemplate="%{text:.0f}",
        textfont=dict(color="white"),
        colorbar=dict(title="P&L (€)"),
    ))
    fig_heat.update_layout(template="plotly_dark", height=380,
                           paper_bgcolor=COULEUR_FOND, plot_bgcolor=COULEUR_FOND,
                           xaxis_title="Choc de vol (points)",
                           yaxis_title="Choc de spot",
                           margin=dict(l=10, r=10, t=20, b=40))
    st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()

    # ── VaR/ES ──
    st.markdown("### VaR & ES — risque probabiliste")
    col_explication, col_param_var = st.columns([3, 1])
    with col_explication:
        st.markdown(
            "<div class='legende'>10 000 scénarios aléatoires demain (spot et IV tirés selon "
            "une loi normale corrélée). On revalorise la position pour chacun. La <b>VaR</b> "
            "est la perte qu'on dépasse seulement 1% du temps. L'<b>ES</b> est la perte "
            "moyenne dans ce 1% des pires cas.</div>",
            unsafe_allow_html=True
        )
    with col_param_var:
        quantile_var = st.slider("Niveau", 0.90, 0.99, 0.99, 0.01,
                                 label_visibility="collapsed")

    moteur_risque.QUANTILE = quantile_var
    metr = moteur_risque.var_es_montecarlo(df_pos, spot, agregat["valeur"])

    col_kpi, col_dist = st.columns([1, 2])
    with col_kpi:
        st.metric(f"VaR {quantile_var:.0%}", f"{metr['var']:.0f} €",
                  help="Perte maximale avec ce niveau de confiance.")
        st.metric(f"ES {quantile_var:.0%}", f"{metr['es']:.0f} €",
                  help="Perte moyenne dans les pires scénarios.")
        st.metric("P&L moyen attendu", f"{metr['moyenne_pnl']:+.0f} €",
                  help="Espérance du P&L sur 10 000 simulations.")
    with col_dist:
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(x=metr["pnls"], nbinsx=80,
                                        marker_color=COULEUR_ACCENT, opacity=0.85))
        fig_hist.add_vline(x=-metr["var"], line_color="orange", line_dash="dash",
                           annotation_text=f"VaR {quantile_var:.0%}")
        fig_hist.add_vline(x=-metr["es"], line_color=COULEUR_BAISSE, line_dash="dash",
                           annotation_text=f"ES {quantile_var:.0%}")
        fig_hist.update_layout(template="plotly_dark", height=300,
                               paper_bgcolor=COULEUR_FOND, plot_bgcolor=COULEUR_FOND,
                               xaxis_title="P&L simulé (€)", yaxis_title="Fréquence",
                               margin=dict(l=10, r=10, t=20, b=40))
        st.plotly_chart(fig_hist, use_container_width=True)

    with st.expander("Détail des positions (par ligne)"):
        st.dataframe(detail, use_container_width=True, hide_index=True)


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ ONGLET 3 — DAILY                                                      ║
# ╚══════════════════════════════════════════════════════════════════════╝
with onglet3:
    st.markdown("### Capture du marché")
    st.markdown(
        "<div class='legende'>Lance une capture pour ajouter un instantané à l'historique. "
        "Le pipeline complet (capture → IV → QC) s'exécute via IB Gateway et prend 1-2 minutes. "
        "Chaque exécution crée trois fichiers datés, jamais écrasés.</div>",
        unsafe_allow_html=True
    )

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("Capturer maintenant", type="primary", use_container_width=True):
            with st.spinner("Capture en cours via IB Gateway..."):
                r = subprocess.run([sys.executable, str(config.RACINE / "daily.py")],
                                   capture_output=True, text=True,
                                   cwd=str(config.RACINE))
                if r.returncode == 0:
                    st.cache_data.clear()
                    st.success("Capture réussie. Recharge la page pour voir les nouvelles données.")
                else:
                    st.error(f"Échec : {r.stderr[-500:]}")
    with col_info:
        if ts_capture:
            st.info(f"Dernière capture : **{date_capture.strftime('%d/%m/%Y à %H:%M:%S')}** "
                    f"— {len(nappe)} options retenues sur {nappe['echeance'].nunique()} échéances.")

    st.divider()

    st.markdown("### Évolution du marché dans le temps")
    st.markdown(
        "<div class='legende'>Trois métriques au fil des captures. <b>Spot</b> : niveau de "
        "l'indice. <b>Vol ATM</b> : niveau de la volatilité implicite à la monnaie pour "
        "chaque maturité. <b>Skew</b> : pente du smile sur l'échéance courte (un pic indique "
        "un afflux d'achats de protection).</div>",
        unsafe_allow_html=True
    )

    import historique as h
    try:
        hist = h.construire_historique()
        st.markdown(f"<div class='legende'>{hist['timestamp'].nunique()} captures, "
                    f"{hist['echeance'].nunique()} échéances suivies.</div>",
                    unsafe_allow_html=True)

        fig = make_subplots(
            rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
            subplot_titles=("Spot ESTX50",
                            "Volatilité ATM par échéance",
                            "Pente du skew (vol 95% - vol 105%)")
        )
        spots = hist.drop_duplicates("timestamp")[["timestamp", "spot"]]
        fig.add_trace(go.Scatter(x=spots["timestamp"], y=spots["spot"],
                                 mode="lines+markers", name="spot",
                                 line=dict(color=COULEUR_ACCENT)), row=1, col=1)
        for ech, sous in hist.groupby("echeance"):
            sous = sous.sort_values("timestamp")
            fig.add_trace(go.Scatter(x=sous["timestamp"], y=sous["vol_atm"],
                                     mode="lines+markers",
                                     name=f"ATM {int(ech)}"), row=2, col=1)
        ech_courte = hist["echeance"].min()
        s = hist[hist["echeance"] == ech_courte].sort_values("timestamp")
        fig.add_trace(go.Scatter(x=s["timestamp"], y=s["skew_95_105"],
                                 mode="lines+markers", name="skew",
                                 line=dict(color="orange")), row=3, col=1)
        fig.update_layout(template="plotly_dark", height=750, showlegend=True,
                          paper_bgcolor=COULEUR_FOND, plot_bgcolor=COULEUR_FOND,
                          margin=dict(l=10, r=10, t=40, b=40))
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("Tableau complet de l'historique"):
            st.dataframe(hist, use_container_width=True, hide_index=True)
    except FileNotFoundError:
        st.warning("Pas encore d'historique. Lance une première capture.")


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ ONGLET 4 — VALIDATION (étape 14 : QA, anomalies, table de triage)     ║
# ╚══════════════════════════════════════════════════════════════════════╝
with onglet4:
    if df_qc is None:
        st.warning("Aucune capture disponible. Va dans l'onglet Daily.")
    else:
        st.markdown("### Suite de validation — la capture est-elle fiable ?")
        st.markdown(
            "<div class='legende'>Contrôles automatiques de l'étape 14 du framework. "
            "Chaque check renvoie un statut <b>pass / warn / fail</b>, une sévérité "
            "(1 critique → 4 informatif), un code de raison et un contexte pour "
            "investiguer. Les checks dont les données ne sont pas encore captées sont "
            "marqués <b>non évaluables</b> (jamais masqués). Une détection d'anomalie "
            "compare les métriques clés à la tendance des captures passées.</div>",
            unsafe_allow_html=True
        )

        rapport_rows, metriques = calculer_validation(ts_capture)
        if not rapport_rows:
            st.info("Validation indisponible : il manque le brut ou le spot de cette capture.")
        else:
            rapport = pd.DataFrame(rapport_rows)
            n_pass = int((rapport["statut"] == "pass").sum())
            n_warn = int((rapport["statut"] == "warn").sum())
            n_fail = int((rapport["statut"] == "fail").sum())
            n_ne = int((rapport["statut"] == "non_evaluable").sum())
            decl = rapport[rapport["statut"].isin(["warn", "fail"])]
            sev_max = int(decl["severite"].min()) if len(decl) else 4

            # ── Bandeau de verdict ──
            if n_fail:
                verdict, coul = f"{n_fail} FAIL — intervention requise", COULEUR_BAISSE
            elif n_warn:
                verdict, coul = f"{n_warn} warn — à surveiller", "#f59e0b"
            else:
                verdict, coul = "Run jugé fiable par les checks évaluables", COULEUR_ACCENT
            st.markdown(
                f"<div style='padding:10px 14px;border-left:3px solid {coul};"
                f"background:#161b22;border-radius:4px;margin:6px 0 14px;'>"
                f"<b style='color:{coul}'>{verdict}</b> &nbsp;·&nbsp; "
                f"sévérité max déclenchée : <b>{sev_max}</b> (1=critique … 4=info)</div>",
                unsafe_allow_html=True)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Pass", n_pass)
            c2.metric("Warn", n_warn)
            c3.metric("Fail", n_fail)
            c4.metric("Non évaluables", n_ne)

            st.divider()

            # ── Table de triage (warn/fail) colorée par sévérité ──
            st.markdown("#### Table de triage — où regarder")
            triage = decl.sort_values("severite")
            if len(triage):
                aff = triage[["severite", "statut", "check", "cible",
                              "code_raison", "valeur", "seuil", "contexte"]].copy()
                aff.columns = ["Sév.", "Statut", "Check", "Cible",
                               "Code raison", "Valeur", "Seuil", "Contexte"]

                def _couleur(row):
                    c = {"fail": "#3d1418", "warn": "#3a2f12"}.get(row["Statut"], "#0d1117")
                    return [f"background-color:{c}"] * len(row)

                sty = (aff.style
                       .apply(_couleur, axis=1)
                       .format({"Valeur": "{:.4g}", "Seuil": "{:.4g}"}))
                st.dataframe(sty, use_container_width=True, hide_index=True)
            else:
                st.markdown(
                    "<div class='legende'>Aucun warn/fail. Tous les checks évaluables "
                    "sont au vert.</div>", unsafe_allow_html=True)

            # ── Métriques clés du run ──
            if metriques:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Points retenus", f"{metriques.get('n_points', 0)}")
                tc = metriques.get("taux_convergence", float("nan"))
                m2.metric("Convergence solveur",
                          f"{tc*100:.1f}%" if np.isfinite(tc) else "n/a")
                rm = metriques.get("rmse_moyen", float("nan"))
                m3.metric("RMSE surface moyen",
                          f"{rm:.4f}" if np.isfinite(rm) else "n/a")
                fc = metriques.get("forward_conf_moyen", float("nan"))
                m4.metric("Confiance forward moy.",
                          f"{fc:.2f}" if np.isfinite(fc) else "n/a")

            st.divider()

            # ── Tendance des métriques QC (suivi de régression) ──
            st.markdown("#### Tendance des métriques QC")
            st.markdown(
                "<div class='legende'>Évolution capture après capture. Sert de "
                "baseline à la détection d'anomalie : un point qui décroche de la "
                "tendance déclenche un flag.</div>", unsafe_allow_html=True)
            histo_qc = charger_historique_qc()
            if histo_qc is not None and len(histo_qc) >= 2:
                h = histo_qc.copy()
                try:
                    h["t"] = pd.to_datetime(h["capture_ts"], format="%Y%m%d_%H%M%S")
                except (ValueError, TypeError):
                    h["t"] = range(len(h))
                figv = make_subplots(
                    rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                    subplot_titles=("Points retenus", "Convergence solveur",
                                    "RMSE surface moyen"))
                figv.add_trace(go.Scatter(x=h["t"], y=h["n_points"], mode="lines+markers",
                                          line=dict(color=COULEUR_ACCENT)), row=1, col=1)
                if "taux_convergence" in h:
                    figv.add_trace(go.Scatter(x=h["t"], y=h["taux_convergence"]*100,
                                              mode="lines+markers",
                                              line=dict(color="#3b82f6")), row=2, col=1)
                if "rmse_moyen" in h:
                    figv.add_trace(go.Scatter(x=h["t"], y=h["rmse_moyen"],
                                              mode="lines+markers",
                                              line=dict(color="orange")), row=3, col=1)
                figv.update_layout(template="plotly_dark", height=560, showlegend=False,
                                   paper_bgcolor=COULEUR_FOND, plot_bgcolor=COULEUR_FOND,
                                   margin=dict(l=10, r=10, t=40, b=30))
                st.plotly_chart(figv, use_container_width=True)
            else:
                st.markdown(
                    "<div class='legende'>Historique trop court (il faut au moins "
                    f"{config.VALID_HISTO_MIN} captures pour juger une anomalie). "
                    "Les flags d'anomalie restent « non évaluables » d'ici là.</div>",
                    unsafe_allow_html=True)

            # ── Rapport complet ──
            with st.expander("Rapport complet (tous les checks)"):
                full = rapport[["severite", "statut", "check", "cible",
                                "code_raison", "valeur", "seuil",
                                "version_seuil", "contexte"]].copy()
                full.columns = ["Sév.", "Statut", "Check", "Cible", "Code raison",
                                "Valeur", "Seuil", "Version seuil", "Contexte"]
                st.dataframe(full.sort_values(["Sév.", "Statut"]),
                             use_container_width=True, hide_index=True)

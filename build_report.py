# build_report.py — Rapport HTML autonome « terminal quant » (soutenance).
#
# Génère UN fichier .html autoportant (Plotly inliné une fois) à partir des
# DERNIERS artefacts réels de data/calcul/. Aucune donnée fabriquée : tout est lu
# sur disque ou recalculé via les modules existants (forward, surface, risque).
# Le fichier s'ouvre par double-clic, sans serveur ni Internet.
#
# Principes du PDF rendus visibles : déterminisme (date = capture_ts), couches
# brut/recalculable, AUCUN repli caché, lineage (manifeste). Strategy-agnostic :
# on mesure et on price le risque, on ne recommande aucun trade.
#
# Lancement : python build_report.py   (REPORT_AUTO_OPEN=1 pour ouvrir le navigateur)

import os
import json
import traceback
import webbrowser
from datetime import datetime
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from plotly.offline import get_plotlyjs

import config
import surface as surf   # réutilise svi() pour reconstruire les smiles ajustés


# ===========================================================================
# Thème « terminal quant » (centralisé)
# ===========================================================================
THEME = {
    "bg": "#0a0e14", "panel": "#121823", "panel2": "#0d131c", "border": "#1e2733",
    "text": "#e6edf3", "muted": "#8b98a9", "grid": "#1b2430",
    "accent": "#00d4b1", "cyan": "#38bdf8",
    "pass": "#34d399", "warn": "#fbbf24", "fail": "#f87171",
    "font_sans": 'ui-sans-serif, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    "font_mono": 'ui-monospace, "Cascadia Code", "Consolas", Menlo, monospace',
}
PALETTE_MAT = ["#00d4b1", "#38bdf8", "#fbbf24", "#a78bfa",
               "#34d399", "#f472b6", "#fb923c", "#60a5fa"]
SURFACE_SCALE = [[0.0, "#06242b"], [0.25, "#0b6e6b"], [0.55, "#00d4b1"],
                 [0.8, "#67e8f9"], [1.0, "#e6fff8"]]
PNL_SCALE = [[0.0, "#f87171"], [0.5, "#121823"], [1.0, "#34d399"]]

_CACHE = {}


# ===========================================================================
# Helpers
# ===========================================================================
class Manquant(Exception):
    """Artefact absent : déclenche un encart « donnée absente » (jamais un crash)."""


def _latest(pattern, dossier=config.CALCUL):
    fichiers = sorted(dossier.glob(pattern))
    return fichiers[-1] if fichiers else None


def _require(pattern, hint, dossier=config.CALCUL):
    p = _latest(pattern, dossier)
    if p is None:
        raise Manquant(f"{hint} — lancez la chaîne d'analyse (motif {pattern}).")
    return p


def _spot():
    if "spot" in _CACHE:
        return _CACHE["spot"]
    for pat in ("qc_ESTX50_*.csv", "iv_ESTX50_*.csv"):
        p = _latest(pat)
        if p is not None:
            df = pd.read_csv(p, nrows=1)
            if "spot" in df.columns:
                _CACHE["spot"] = float(df["spot"].iloc[0])
                return _CACHE["spot"]
    raise Manquant("spot de référence introuvable")


def _bool(series):
    return series.astype(str).str.lower() == "true"


def style(fig, height=440, three_d=False):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=THEME["panel"], plot_bgcolor=THEME["panel"],
        font=dict(color=THEME["text"], family=THEME["font_sans"], size=12),
        margin=dict(l=64, r=28, t=34, b=52), height=height,
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=THEME["border"], borderwidth=1),
        colorway=PALETTE_MAT, hoverlabel=dict(font_family=THEME["font_mono"]),
    )
    if three_d:
        axis = dict(backgroundcolor=THEME["panel"], gridcolor=THEME["grid"],
                    zerolinecolor=THEME["grid"], color=THEME["muted"])
        fig.update_scenes(xaxis=axis, yaxis=axis, zaxis=axis)
    else:
        fig.update_xaxes(gridcolor=THEME["grid"], zerolinecolor=THEME["grid"],
                         linecolor=THEME["border"], color=THEME["muted"])
        fig.update_yaxes(gridcolor=THEME["grid"], zerolinecolor=THEME["grid"],
                         linecolor=THEME["border"], color=THEME["muted"])
    return fig


def frag(fig):
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"displayModeBar": False, "responsive": True})


def stat(label, value, sub="", tone="accent"):
    return (f'<div class="stat"><div class="stat-val t-{tone}">{escape(str(value))}</div>'
            f'<div class="stat-lab">{escape(label)}</div>'
            f'<div class="stat-sub">{escape(sub)}</div></div>')


# ===========================================================================
# PANELS — chaque builder renvoie le HTML interne d'une carte
# ===========================================================================
def panel_kpis():
    iv = pd.read_csv(_require("iv_ESTX50_*.csv", "IV absente"))
    fwd = pd.read_csv(_require("forward_ESTX50_*.csv", "forward absent"))
    sp = pd.read_csv(_require("surface_params_*.csv", "surface absente"))
    val = pd.read_csv(_require("validation_ESTX50_*.csv", "validation absente"))

    n, n_ok = len(iv), int((iv["statut"] == "ok").sum())
    conv = n_ok / n if n else float("nan")
    med = iv["ecart_iv"].abs().median()
    spot = _spot()
    par = fwd[fwd["methode"] == "parite"]
    conf = par["confiance"].mean() if len(par) else fwd["confiance"].mean()
    cal = sp[sp["methode"].isin(["svi", "spline"])]
    rmse = cal["rmse_vol"].mean()
    vc = val["statut"].value_counts().to_dict()
    cal_row = val[val["check"] == "calendrier"]
    cal_pct = float(cal_row["valeur"].iloc[0]) if len(cal_row) else float("nan")

    conv_tone = "pass" if conv >= config.VALID_CONV_WARN else "warn"
    rmse_tone = "pass" if rmse <= config.VALID_RMSE_WARN else "warn"
    cal_tone = "pass" if (cal_pct == 0) else "warn"
    cells = [
        stat("Spot ESTX50", f"{spot:,.0f}".replace(",", " "), "référence", "cyan"),
        stat("Options inversées", f"{n_ok}/{n}", f"convergence {conv:.1%}", conv_tone),
        stat("Écart médian vs IBKR", f"{med:.4f}", "vol (témoin IBKR)", "accent"),
        stat("Confiance forward", f"{conf:.2f}", "parité call-put", "pass"),
        stat("RMSE SVI moyen", f"{rmse:.4f}", "ajustement de nappe", rmse_tone),
        stat("Arbitrage calendaire", f"{cal_pct:.1f}%", "variance croissante en T", cal_tone),
        stat("Validation", f"{vc.get('pass',0)}·{vc.get('warn',0)}·{vc.get('fail',0)}",
             "pass · warn · fail", "pass" if vc.get("fail", 0) == 0 else "fail"),
    ]
    return '<div class="stat-grid">' + "".join(cells) + "</div>"


def panel_surface():
    g = pd.read_csv(_require("surface_grid_*.csv", "grille de surface absente"))
    piv = g.pivot_table(index="T", columns="log_moneyness", values="vol_implicite")
    x, y, z = piv.columns.values, piv.index.values, piv.values * 100.0
    fig = go.Figure(go.Surface(
        x=x, y=y, z=z, colorscale=SURFACE_SCALE,
        colorbar=dict(title="IV %", outlinecolor=THEME["border"], tickcolor=THEME["muted"]),
        contours={"z": {"show": True, "usecolormap": True, "project_z": True}},
    ))
    fig.update_layout(scene=dict(
        xaxis_title="Log-moneyness k = ln(K/F)",
        yaxis_title="Maturité (années)", zaxis_title="Vol implicite (%)",
        camera=dict(eye=dict(x=1.5, y=-1.5, z=0.9))))
    return frag(style(fig, height=560, three_d=True))


def panel_smiles():
    pts = pd.read_csv(_require("surface_points_*.csv", "points de surface absents"))
    par = pd.read_csv(_require("surface_params_*.csv", "paramètres de surface absents"))
    spot = _spot()
    fig = go.Figure()
    for i, ech in enumerate(sorted(par["echeance"].unique())):
        c = PALETTE_MAT[i % len(PALETTE_MAT)]
        pe = pts[(pts["echeance"] == ech) & _bool(pts["gardee"])]
        if len(pe):
            fig.add_scatter(x=pe["strike"], y=pe["iv_calculee"], mode="markers",
                            marker=dict(color=c, size=7, line=dict(width=0)),
                            name=f"{int(ech)} · points", legendgroup=str(ech))
        row = par[par["echeance"] == ech].iloc[0]
        if row["methode"] == "svi" and pd.notna(row.get("a")) and len(pe):
            k_obs = np.log(pe["strike"].values / row["forward"])
            k = np.linspace(float(k_obs.min()), float(k_obs.max()), 120)
            w = np.clip(surf.svi(k, (row["a"], row["b"], row["rho"], row["m"], row["s"])), 1e-12, None)
            sig = np.sqrt(w / row["T"])
            K = row["forward"] * np.exp(k)
            fig.add_scatter(x=K, y=sig, mode="lines", line=dict(color=c, width=2),
                            name=f"{int(ech)} · SVI", legendgroup=str(ech))
    fig.add_vline(x=spot, line_dash="dash", line_color=THEME["muted"],
                  annotation_text="spot", annotation_font_color=THEME["muted"])
    fig.update_layout(xaxis_title="Strike", yaxis_title="Volatilité implicite")
    return frag(style(fig, height=480))


def panel_forward():
    f = pd.read_csv(_require("forward_ESTX50_*.csv", "forward absent")).sort_values("T")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=f["T"], y=f["dividende_implicite"] * 100, name="Dividende implicite (%)",
                marker_color=THEME["cyan"], opacity=0.28, secondary_y=True)
    fig.add_scatter(x=f["T"], y=f["forward_carry"], name="F carry (repli)", mode="lines",
                    line=dict(color=THEME["muted"], width=1.5, dash="dot"))
    fig.add_scatter(x=f["T"], y=f["forward_median"], name="F médian", mode="lines+markers",
                    line=dict(color=THEME["cyan"], width=1.5, dash="dash"))
    fig.add_scatter(x=f["T"], y=f["forward"], name="F parité", mode="lines+markers",
                    line=dict(color=THEME["accent"], width=2.5),
                    marker=dict(size=9, color=THEME["accent"]))
    fig.update_xaxes(title_text="Maturité (années)")
    fig.update_yaxes(title_text="Forward (points d'indice)", secondary_y=False)
    fig.update_yaxes(title_text="Dividende implicite (%)", secondary_y=True,
                     gridcolor="rgba(0,0,0,0)", color=THEME["muted"])
    return frag(style(fig, height=440))


def panel_parite():
    d = pd.read_csv(_require("forward_diag_ESTX50_*.csv", "diagnostics de parité absents"))
    ret, rej = d[_bool(d["retenu"])], d[~_bool(d["retenu"])]
    fig = go.Figure()
    fig.add_scatter(x=ret["strike"], y=ret["zscore_robuste"], mode="markers", name="retenu",
                    marker=dict(color=THEME["pass"], size=8),
                    customdata=ret["echeance"], hovertemplate="K=%{x}<br>z=%{y:.2f}<br>éch=%{customdata}<extra></extra>")
    if len(rej):
        fig.add_scatter(x=rej["strike"], y=rej["zscore_robuste"], mode="markers", name="rejeté (MAD)",
                        marker=dict(color=THEME["fail"], size=10, symbol="x"),
                        customdata=rej["echeance"], hovertemplate="K=%{x}<br>z=%{y:.2f}<br>éch=%{customdata}<extra></extra>")
    for s in (1, -1):
        fig.add_hline(y=s * config.FORWARD_MAX_ZSCORE, line_dash="dash", line_color=THEME["warn"],
                      annotation_text=f"seuil ±{config.FORWARD_MAX_ZSCORE}",
                      annotation_font_color=THEME["warn"])
    fig.update_layout(xaxis_title="Strike", yaxis_title="z-score robuste (MAD)")
    return frag(style(fig, height=420))


def panel_solveur():
    iv = pd.read_csv(_require("iv_ESTX50_*.csv", "IV absente"))
    ok = iv[(iv["statut"] == "ok") & iv["iv_ibkr"].notna()]
    fig = make_subplots(rows=1, cols=2, column_widths=[0.6, 0.4], horizontal_spacing=0.12,
                        subplot_titles=("Notre IV (Brent) vs IBKR", "Distribution de l'écart"))
    lo = float(min(ok["iv_ibkr"].min(), ok["iv_calculee"].min()))
    hi = float(max(ok["iv_ibkr"].max(), ok["iv_calculee"].max()))
    fig.add_scatter(x=ok["iv_ibkr"], y=ok["iv_calculee"], mode="markers",
                    marker=dict(color=THEME["accent"], size=5, opacity=0.7),
                    name="options", row=1, col=1)
    fig.add_scatter(x=[lo, hi], y=[lo, hi], mode="lines", name="y = x",
                    line=dict(color=THEME["muted"], dash="dash"), row=1, col=1)
    fig.add_histogram(x=ok["ecart_iv"], nbinsx=40, marker_color=THEME["cyan"],
                      opacity=0.85, name="écart", row=1, col=2)
    fig.update_xaxes(title_text="IV IBKR", row=1, col=1)
    fig.update_yaxes(title_text="IV calculée", row=1, col=1)
    fig.update_xaxes(title_text="IV calculée − IBKR", row=1, col=2)
    fig.update_layout(showlegend=False)
    return frag(style(fig, height=420))


def panel_risque():
    import risque
    df, spot = risque.charger_portefeuille()
    detail, agg = risque.greeks_portefeuille(df, spot)
    base = risque.valeur_portefeuille(df, spot, 0.0, 0.0)
    table = risque.pnl_scenarios(df, spot, base)
    metr = risque.var_es_montecarlo(df, spot, base)

    cells = [
        stat("Delta", f"{agg['delta']:,.1f}".replace(",", " "), "€ / point spot", "cyan"),
        stat("Gamma", f"{agg['gamma']:.4f}", "par point", "cyan"),
        stat("Vega", f"{agg['vega']:,.0f}".replace(",", " "), "€ / point vol", "accent"),
        stat("Theta / jour", f"{agg['theta_jour']:,.0f}".replace(",", " "), "€ / jour", "accent"),
        stat("Delta cash", f"{agg['delta_cash']:,.0f}".replace(",", " "), "€ équivalents", "muted"),
        stat("Gamma cash", f"{agg['gamma_cash']:,.0f}".replace(",", " "), "Éq.17 PDF", "muted"),
        stat(f"VaR {config.VAR_QUANTILE:.0%}", f"{metr['var']:,.0f}".replace(",", " "), "perte 1j", "fail"),
        stat(f"ES {config.VAR_QUANTILE:.0%}", f"{metr['es']:,.0f}".replace(",", " "), "queue moyenne", "fail"),
    ]
    greeks_html = '<div class="stat-grid">' + "".join(cells) + "</div>"

    heat = go.Figure(go.Heatmap(
        z=table.values, x=list(table.columns), y=list(table.index),
        colorscale=PNL_SCALE, zmid=0, text=table.values, texttemplate="%{z:.0f}",
        textfont=dict(size=10, family=THEME["font_mono"]),
        colorbar=dict(title="P&L €", outlinecolor=THEME["border"])))
    heat.update_layout(xaxis_title="Choc de vol (points)", yaxis_title="Choc de spot",
                       title=dict(text="P&L sous scénarios — full repricing", font=dict(size=13)))
    style(heat, height=420)

    pnls = metr["pnls"]
    hist = go.Figure(go.Histogram(x=pnls, nbinsx=60, marker_color=THEME["accent"], opacity=0.85))
    hist.add_vline(x=-metr["var"], line_color=THEME["fail"],
                   annotation_text=f"VaR {config.VAR_QUANTILE:.0%}", annotation_font_color=THEME["fail"])
    hist.add_vline(x=-metr["es"], line_color=THEME["warn"],
                   annotation_text="ES", annotation_font_color=THEME["warn"])
    hist.update_layout(xaxis_title="P&L simulé (€)", yaxis_title="Fréquence",
                       title=dict(text=f"Distribution Monte-Carlo ({config.VAR_N_SIMULATIONS} tirages)",
                                  font=dict(size=13)))
    style(hist, height=420)
    return greeks_html + '<div class="row2">' + frag(heat) + frag(hist) + "</div>"


def panel_validation():
    v = pd.read_csv(_require("validation_ESTX50_*.csv", "validation absente"))
    couleur = {"pass": THEME["pass"], "warn": THEME["warn"],
               "fail": THEME["fail"], "non_evaluable": THEME["muted"]}
    vc = v["statut"].value_counts()
    labels = [k for k in ["pass", "warn", "fail", "non_evaluable"] if k in vc.index]
    pie = go.Figure(go.Pie(labels=labels, values=[int(vc[k]) for k in labels], hole=0.62,
                           marker=dict(colors=[couleur[k] for k in labels],
                                       line=dict(color=THEME["panel"], width=2)),
                           textinfo="label+value"))
    pie.update_layout(showlegend=False, title=dict(text="Statut des checks", font=dict(size=13)))
    style(pie, height=360)

    triage = v[v["statut"].isin(["warn", "fail"])].sort_values("severite")
    if len(triage):
        rows = "".join(
            f'<tr><td><span class="pill" style="background:{couleur[r.statut]}22;'
            f'color:{couleur[r.statut]}">S{int(r.severite)} {r.statut.upper()}</span></td>'
            f'<td class="mono">{escape(str(r.check))}</td><td class="mono">{escape(str(r.cible))}</td>'
            f'<td>{escape(str(r.code_raison))}</td><td class="muted">{escape(str(r.contexte))}</td></tr>'
            for r in triage.itertuples())
        triage_html = ('<table class="tbl"><thead><tr><th>Sév.</th><th>Check</th><th>Cible</th>'
                       '<th>Code</th><th>Contexte</th></tr></thead><tbody>' + rows + "</tbody></table>")
    else:
        triage_html = '<p class="ok-msg">✓ Aucun warn/fail — run jugé fiable par les checks évaluables.</p>'

    ne = v[v["statut"] == "non_evaluable"]
    if len(ne):
        items = "".join(f"<li><span class='mono'>{escape(str(r.check))}</span> — "
                        f"<span class='muted'>{escape(str(r.contexte))}</span></li>"
                        for r in ne.itertuples())
        ne_html = ('<details class="ne"><summary>Non évaluables — données à câbler '
                   f"({len(ne)})</summary><ul>{items}</ul></details>")
    else:
        ne_html = ""
    return '<div class="row2">' + frag(pie) + '<div class="triage">' + triage_html + ne_html + "</div></div>"


def panel_historique():
    h = pd.read_csv(_require("qc_metrics_history_ESTX50.csv", "historique de métriques absent"))
    x = list(range(1, len(h) + 1))
    fig = make_subplots(rows=2, cols=2, vertical_spacing=0.18, horizontal_spacing=0.12,
                        subplot_titles=("Taux de convergence", "RMSE moyen",
                                        "Confiance forward", "Points retenus"))
    spec = [("taux_convergence", THEME["pass"], 1, 1), ("rmse_moyen", THEME["warn"], 1, 2),
            ("forward_conf_moyen", THEME["accent"], 2, 1), ("n_points", THEME["cyan"], 2, 2)]
    for col, color, r, c in spec:
        if col in h.columns:
            fig.add_scatter(x=x, y=h[col], mode="lines+markers", line=dict(color=color, width=2),
                            marker=dict(size=7), name=col, row=r, col=c)
    fig.update_layout(showlegend=False)
    fig.update_xaxes(title_text="run #")
    return frag(style(fig, height=460))


def panel_lineage():
    p = _latest("manifest_ESTX50_*.json")
    if p is None:
        raise Manquant("manifeste absent — lancez python manifest.py")
    m = json.loads(p.read_text(encoding="utf-8"))
    head = "".join([
        stat("run_id", m.get("run_id", "—"), "", "cyan"),
        stat("code_version", m.get("code_version", "—"), "", "accent"),
        stat("capture_ts", m.get("capture_ts", "—"), "date de valorisation", "muted"),
        stat("statut", m.get("statut", "—"), "", "pass" if m.get("statut") == "success" else "warn"),
    ])
    hashes = "".join(f"<tr><td class='mono'>{escape(k)}</td><td class='mono muted'>{escape(v)}</td></tr>"
                     for k, v in m.get("config_hashes", {}).items())
    hashes_html = ("<table class='tbl'><thead><tr><th>Groupe de config</th><th>Hash</th></tr></thead>"
                   f"<tbody>{hashes}</tbody></table>")
    note = escape(m.get("note_risque", ""))
    return ('<div class="stat-grid">' + head + "</div>"
            "<p class='muted' style='margin:14px 0 6px'>Hashes de configuration (changent si un seuil "
            "économique bouge — traçabilité) :</p>" + hashes_html
            + (f"<p class='muted' style='margin-top:10px'>{note}</p>" if note else ""))


# ===========================================================================
# Bandeau d'ouverture : verdict en une ligne + schéma de la chaîne (architecture)
# ===========================================================================
def flow_svg():
    """Schéma horizontal de la chaîne, en SVG inline (net, offline, sans dépendance)."""
    P2, ACC, TX, MUT, BD = (THEME["panel2"], THEME["accent"], THEME["text"],
                            THEME["muted"], THEME["border"])
    stages = [("Capture", "2–3"), ("Snapshot", "5"), ("Forward", "6"), ("IV", "8"),
              ("Surface", "9"), ("Risque", "11–12"), ("Validation", "14")]
    bw, bh, gap, x0, y0 = 152, 56, 30, 12, 20
    W = x0 * 2 + len(stages) * bw + (len(stages) - 1) * gap
    parts = [f'<svg viewBox="0 0 {W} 96" width="100%" preserveAspectRatio="xMidYMid meet" '
             'role="img" aria-label="chaîne de traitement">']
    for i, (name, step) in enumerate(stages):
        x = x0 + i * (bw + gap)
        cx = x + bw / 2
        parts.append(f'<rect x="{x}" y="{y0}" width="{bw}" height="{bh}" rx="8" fill="{P2}" '
                     f'stroke="{ACC}" stroke-width="1.2"/>')
        parts.append(f'<text x="{cx}" y="{y0+24}" text-anchor="middle" fill="{TX}" '
                     f'font-family="monospace" font-size="15" font-weight="700">{name}</text>')
        parts.append(f'<text x="{cx}" y="{y0+42}" text-anchor="middle" fill="{MUT}" '
                     f'font-family="sans-serif" font-size="11">Étape {step}</text>')
        if i < len(stages) - 1:
            ax, ax2, ay = x + bw, x + bw + gap, y0 + bh / 2
            parts.append(f'<line x1="{ax+3}" y1="{ay}" x2="{ax2-7}" y2="{ay}" '
                         f'stroke="{ACC}" stroke-width="1.6"/>')
            parts.append(f'<path d="M{ax2-7},{ay-4} L{ax2-1},{ay} L{ax2-7},{ay+4} Z" fill="{ACC}"/>')
    parts.append("</svg>")
    return ('<div class="flow">' + "".join(parts)
            + '<div class="cap">Chaîne déterministe — chaque sortie remonte à une capture datée ; '
            'un manifeste de lineage est écrit à chaque run (Étape 4 / Appendice B).</div></div>')


def build_hero():
    """Verdict factuel (lu sur les artefacts) + schéma de la chaîne."""
    iv = pd.read_csv(_require("iv_ESTX50_*.csv", "IV absente"))
    val = pd.read_csv(_require("validation_ESTX50_*.csv", "validation absente"))
    n, n_ok = len(iv), int((iv["statut"] == "ok").sum())
    vc = val["statut"].value_counts().to_dict()
    cal_row = val[val["check"] == "calendrier"]
    cal_pct = float(cal_row["valeur"].iloc[0]) if len(cal_row) else float("nan")
    cal_txt = ("0 violation d'arbitrage calendaire" if cal_pct == 0
               else f"{cal_pct:.1f}% d'arbitrage calendaire")
    verdict = (f"Plateforme opérationnelle de bout en bout — {n_ok}/{n} options inversées, "
               f"{cal_txt}, validation {vc.get('pass',0)}·{vc.get('warn',0)}·{vc.get('fail',0)} "
               "(pass·warn·fail).")
    return (f'<div class="verdict"><span class="v-tag">VERDICT</span>{escape(verdict)}</div>'
            + flow_svg())


# ===========================================================================
# Déclaration des sections + exécution sûre (une carte ne peut pas tuer le rapport)
# ===========================================================================
PANELS = [
    dict(anchor="synthese", nav="Synthèse", step="", title="Synthèse du run",
         caption="Les indicateurs clés du dernier run, lus sur les artefacts réels.",
         proof="déterminisme : tout vient d'une capture datée (capture_ts).", build=panel_kpis),
    dict(anchor="nappe", nav="Nappe 3D", step="9", title="Nappe de volatilité SVI (3D)",
         caption="Surface reconstruite en log-moneyness × maturité, interpolée en variance totale.",
         proof="une nappe lisse et stable, calibrée tranche par tranche.", build=panel_surface),
    dict(anchor="smiles", nav="Smiles", step="9", title="Smiles par maturité — points vs ajustement",
         caption="Points de marché acceptés (QC) contre la courbe SVI calibrée, par échéance.",
         proof="on garde les points bruts à côté de l'ajustement (debug opérateur).", build=panel_smiles),
    dict(anchor="forward", nav="Forward", step="6", title="Structure par terme du forward",
         caption="Forward par parité call-put vs repli carry vs médiane, et dividende implicite.",
         proof="forward de 1re classe : une erreur ici contamine IV, nappe et risque.", build=panel_forward),
    dict(anchor="parite", nav="Parité", step="6", title="Diagnostic de parité — rejet des aberrants",
         caption="z-score robuste (MAD) de chaque candidat ; au-delà du seuil, le strike est rejeté.",
         proof="sélection robuste : un strike illiquide ne fausse pas le forward.", build=panel_parite),
    dict(anchor="solveur", nav="Solveur", step="8", title="Solveur d'IV vs témoin IBKR",
         caption="Notre IV (inversion de Brent) comparée à l'IV d'IBKR, et distribution de l'écart.",
         proof="le moteur d'inversion est correct (écart médian ~ pts de base).", build=panel_solveur),
    dict(anchor="risque", nav="Risque", step="11-12", title="Risque du portefeuille & scénarios",
         caption="Greeks monétisés, grille de P&L en full repricing, et VaR/ES Monte-Carlo.",
         proof="full repricing = vérité ; VaR reproductible (graine fixe).", build=panel_risque),
    dict(anchor="validation", nav="Validation", step="14", title="Suite de validation & triage",
         caption="Statut pass/warn/fail des checks nommés, table de triage et non-évaluables assumés.",
         proof="QA comme un produit : flags actionnables, rien de caché.", build=panel_validation),
    dict(anchor="tendance", nav="Tendance", step="14", title="Tendance des métriques QC",
         caption="Évolution des métriques clés sur les runs successifs (baseline d'anomalie).",
         proof="suivi de régression : on voit une dérive avant qu'elle ne casse.", build=panel_historique),
    dict(anchor="lineage", nav="Lineage", step="4", title="Lineage & manifeste de run",
         caption="Versions de code et de configuration, partitions d'entrée/sortie du run.",
         proof="reproductibilité : chaque sortie remonte à ses sources.", build=panel_lineage),
]


def render_card(p):
    badge = f'<span class="badge">Étape {p["step"]}</span>' if p["step"] else '<span class="badge alt">KPI</span>'
    try:
        body = p["build"]()
        status = "ok"
    except Manquant as e:
        body = f'<div class="placeholder">Donnée absente — {escape(str(e))}</div>'
        status = "absent"
    except Exception as e:  # une carte qui plante n'arrête pas le rapport
        body = (f'<div class="placeholder err">Erreur de panneau : {escape(str(e))}<br>'
                f'<span class="muted">{escape(traceback.format_exc().splitlines()[-1])}</span></div>')
        status = "erreur"
    proof = (f'<div class="proof">Ce que ça prouve → {escape(p["proof"])}</div>') if p["proof"] else ""
    html = (f'<section class="card" id="{p["anchor"]}"><div class="card-head">{badge}'
            f'<h2>{escape(p["title"])}</h2></div>'
            f'<p class="caption">{escape(p["caption"])}</p>{body}{proof}</section>')
    return html, status


CSS = """
:root{--bg:#0a0e14;--panel:#121823;--panel2:#0d131c;--border:#1e2733;--text:#e6edf3;
--muted:#8b98a9;--accent:#00d4b1;--cyan:#38bdf8;--pass:#34d399;--warn:#fbbf24;--fail:#f87171;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:ui-sans-serif,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5}
.mono{font-family:ui-monospace,"Cascadia Code","Consolas",Menlo,monospace}
a{color:var(--accent);text-decoration:none}
header.top{position:sticky;top:0;z-index:20;background:rgba(10,14,20,.92);
backdrop-filter:blur(8px);border-bottom:1px solid var(--border);padding:14px 28px}
.top-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap;max-width:1280px;margin:0 auto}
.brand{font-weight:700;letter-spacing:.04em;font-size:18px}
.brand .dot{color:var(--accent)}
.chip{font-family:ui-monospace,"Cascadia Code",Consolas,Menlo,monospace;font-size:12px;
color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:3px 9px;background:var(--panel2)}
nav{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
nav a{font-size:12.5px;color:var(--muted);padding:4px 9px;border-radius:6px}
nav a:hover{color:var(--text);background:var(--panel)}
main{max-width:1280px;margin:26px auto;padding:0 28px}
.lede{color:var(--muted);font-size:14px;margin:0 0 22px;border-left:2px solid var(--accent);padding-left:14px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;
padding:20px 22px 16px;margin:0 0 22px;box-shadow:0 1px 0 rgba(255,255,255,.02);position:relative;overflow:hidden}
.card::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;
background:linear-gradient(90deg,var(--accent),transparent 60%)}
.card-head{display:flex;align-items:center;gap:12px;margin-bottom:4px}
.card-head h2{font-size:17px;margin:0;font-weight:650}
.badge{font-family:ui-monospace,Consolas,monospace;font-size:11px;font-weight:700;
color:var(--bg);background:var(--accent);border-radius:5px;padding:3px 8px;white-space:nowrap}
.badge.alt{background:var(--cyan)}
.caption{color:var(--muted);font-size:13.5px;margin:0 0 14px}
.proof{margin-top:12px;font-size:12.5px;color:var(--accent);border-top:1px dashed var(--border);padding-top:10px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.stat-val{font-family:ui-monospace,"Cascadia Code",Consolas,Menlo,monospace;font-size:25px;font-weight:700;letter-spacing:-.02em}
.stat-lab{font-size:12.5px;margin-top:3px}
.stat-sub{font-size:11px;color:var(--muted);margin-top:1px}
.t-accent{color:var(--accent)}.t-cyan{color:var(--cyan)}.t-pass{color:var(--pass)}
.t-warn{color:var(--warn)}.t-fail{color:var(--fail)}.t-muted{color:var(--muted)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:14px}
@media(max-width:900px){.row2{grid-template-columns:1fr}}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
.tbl th{text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--border);padding:7px 8px}
.tbl td{border-bottom:1px solid var(--border);padding:7px 8px;vertical-align:top}
.pill{font-family:ui-monospace,Consolas,monospace;font-size:11px;font-weight:700;border-radius:5px;padding:2px 7px;white-space:nowrap}
.muted{color:var(--muted)}
.ok-msg{color:var(--pass);font-size:14px;background:var(--panel2);border:1px solid var(--border);
border-radius:8px;padding:14px 16px}
.triage{display:flex;flex-direction:column;gap:10px}
.ne summary{cursor:pointer;color:var(--muted);font-size:13px;margin-top:8px}
.ne ul{margin:8px 0 0;padding-left:18px;font-size:12.5px}.ne li{margin:3px 0}
.placeholder{background:var(--panel2);border:1px dashed var(--border);border-radius:8px;
padding:22px;color:var(--warn);font-size:13.5px}
.placeholder.err{color:var(--fail)}
.verdict{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--accent);
border-radius:8px;padding:14px 18px;margin:0 0 16px;font-size:15px}
.v-tag{display:inline-block;font-family:ui-monospace,Consolas,monospace;font-size:11px;font-weight:700;
color:var(--bg);background:var(--accent);border-radius:5px;padding:2px 8px;margin-right:10px;letter-spacing:.04em}
.flow{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin:0 0 24px}
.flow .cap{color:var(--muted);font-size:12px;margin-top:10px}
footer{max-width:1280px;margin:10px auto 40px;padding:0 28px;color:var(--muted);font-size:12px}
"""


def construire_html():
    spot = None
    try:
        spot = _spot()
    except Manquant:
        pass
    # contexte d'en-tête
    cap = code = "—"
    mp = _latest("manifest_ESTX50_*.json")
    if mp is not None:
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
            cap, code = m.get("capture_ts", "—"), m.get("code_version", "—")
        except Exception:
            pass

    cards, statuses = [], []
    for p in PANELS:
        html, st = render_card(p)
        cards.append(html)
        statuses.append((p["anchor"], st))

    try:                                  # bandeau d'ouverture (verdict + schéma)
        hero = build_hero()
    except Exception:
        hero = ""

    nav = "".join(f'<a href="#{p["anchor"]}">{escape(p["nav"])}</a>' for p in PANELS)
    chips = (f'<span class="chip">capture {escape(cap)}</span>'
             f'<span class="chip">{escape(code)}</span>'
             + (f'<span class="chip">spot {spot:,.0f}</span>'.replace(",", " ") if spot else ""))
    gen = datetime.now().strftime("%Y-%m-%d %H:%M")

    header = ('<header class="top"><div class="top-row">'
              '<span class="brand">ESTX50 <span class="dot">●</span> Infrastructure de volatilité</span>'
              f'{chips}<nav>{nav}</nav></div></header>')
    lede = ('<p class="lede">Rapport autoportant du stack de volatilité — données réelles, '
            'recalculées par les mêmes modules que la production. Strategy-agnostic : on mesure '
            'et on price le risque, on ne recommande aucun trade. Chaque panneau renvoie à une '
            'étape du framework.</p>')
    footer = (f'<footer>Généré le {gen} · données : data/calcul/ · '
              'brut immuable / analytics recalculable · déterminisme par capture_ts.</footer>')

    doc = ("<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width, initial-scale=1'>"
           "<title>ESTX50 — Infrastructure de volatilité</title>"
           "<style>" + CSS + "</style>"
           "<script>" + get_plotlyjs() + "</script></head><body>"
           + header + "<main>" + lede + hero + "".join(cards) + "</main>" + footer
           + "</body></html>")
    return doc, statuses


def main():
    doc, statuses = construire_html()
    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    sortie = config.CALCUL / f"rapport_{config.SYMBOLE}_{horo}.html"
    dernier = config.CALCUL / "rapport_dernier.html"
    sortie.write_text(doc, encoding="utf-8")
    dernier.write_text(doc, encoding="utf-8")

    print(f"[rapport] {len(doc)//1024} Ko écrits")
    for anchor, st in statuses:
        marque = {"ok": "[OK]", "absent": "[absent]", "erreur": "[ERREUR]"}.get(st, "?")
        print(f"[rapport]   {marque} {anchor:12s} {st}")
    print(f"[rapport] fichier : {sortie.name}")
    print(f"[rapport] alias   : {dernier.name}")
    if os.environ.get("REPORT_AUTO_OPEN", "0") == "1":
        webbrowser.open(dernier.as_uri())


if __name__ == "__main__":
    main()

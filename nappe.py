# nappe.py — Étape 9 du framework : construction et visualisation de la NAPPE.
# Lit le dernier fichier qc_ (points propres), agrège, interpole sur une grille
# régulière, et produit une surface 3D interactive + les smiles superposés.
# Ne se connecte à rien : pur calcul + visualisation à partir des points QC.

from datetime import datetime, date
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import griddata

import config


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


def preparer_points(df, ref=None):
    """
    Garde les points retenus par le QC, calcule T, et AGRÈGE :
    pour un même (strike, echeance) on prend la MÉDIANE des IV calculées
    (robuste au bruit, comme le préconise le PDF avec les stats robustes).
    """
    pts = df[df["gardee"]].copy()
    pts["T"] = pts["echeance"].apply(lambda e: _maturite_annees(e, ref))
    agrege = (pts.groupby(["echeance", "strike", "T"], as_index=False)
                 ["iv_calculee"].median())
    return agrege


def tracer_nappe(agrege, spot):
    # --- Grille régulière strike x maturité ---
    strikes = np.linspace(agrege["strike"].min(), agrege["strike"].max(), 40)
    maturites = np.linspace(agrege["T"].min(), agrege["T"].max(), 40)
    gx, gy = np.meshgrid(strikes, maturites)

    # Interpolation des IV éparses sur la grille (linéaire, comble les trous)
    gz = griddata(
        points=agrege[["strike", "T"]].values,
        values=agrege["iv_calculee"].values,
        xi=(gx, gy),
        method="linear",
    )

    # --- Figure 1 : surface 3D interactive ---
    surface = go.Figure(data=[go.Surface(
        x=strikes, y=maturites, z=gz,
        colorscale="Viridis",
        colorbar=dict(title="IV"),
    )])
    surface.update_layout(
        title=f"Nappe de volatilité {config.SYMBOLE} — spot {spot:.0f}",
        scene=dict(
            xaxis_title="Strike",
            yaxis_title="Maturité (années)",
            zaxis_title="Volatilité implicite",
        ),
        template="plotly_dark",
        height=700,
    )
    surface.write_html(str(config.CALCUL / "nappe_3d.html"), auto_open=True)
    print(f"[nappe] surface 3D : {config.CALCUL / 'nappe_3d.html'}")

    # --- Figure 2 : smiles superposés (une courbe par échéance) ---
    smiles = go.Figure()
    for ech, sous in agrege.groupby("echeance"):
        sous = sous.sort_values("strike")
        T = sous["T"].iloc[0]
        smiles.add_trace(go.Scatter(
            x=sous["strike"], y=sous["iv_calculee"],
            mode="lines+markers",
            name=f"{int(ech)} (T={T:.3f})",
        ))
    smiles.add_vline(x=spot, line_dash="dash", line_color="grey",
                     annotation_text="spot")
    smiles.update_layout(
        title=f"Smiles par échéance {config.SYMBOLE} — le skew s'aplatit avec la maturité",
        xaxis_title="Strike",
        yaxis_title="Volatilité implicite",
        template="plotly_dark",
        height=600,
    )
    smiles.write_html(str(config.CALCUL / "smiles.html"), auto_open=True)
    print(f"[nappe] smiles superposés : {config.CALCUL / 'smiles.html'}")


def main():
    fichier = _dernier_fichier_qc()
    print(f"[nappe] lecture des points QC : {fichier.name}")
    df = pd.read_csv(fichier)
    spot = float(df["spot"].iloc[0]) if "spot" in df.columns else 6250.0

    agrege = preparer_points(df, ref=_date_capture(df))
    n_ech = agrege["echeance"].nunique()
    print(f"[nappe] {len(agrege)} points agrégés sur {n_ech} échéances")

    tracer_nappe(agrege, spot)


if __name__ == "__main__":
    main()

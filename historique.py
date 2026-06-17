# historique.py — Étape 13 (suite) : lecture et visualisation de l'historique.
# Parcourt tous les fichiers qc_ déjà produits, en extrait pour chaque capture :
#   - le timestamp et le spot
#   - la vol ATM par échéance
#   - la pente du skew (proxy : vol 90% - vol 110% du spot)
# Puis trace l'évolution de ces métriques dans le temps.
# Ne se connecte à rien : lit uniquement les fichiers locaux.

from datetime import datetime, date
import re
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config


def _parse_horodatage(nom_fichier):
    """Extrait la date+heure du nom de fichier qc_ESTX50_AAAAMMJJ_HHMMSS.csv."""
    m = re.search(r"(\d{8})_(\d{6})", nom_fichier)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")


def _maturite_annees(echeance, ref):
    ech = datetime.strptime(str(int(echeance)), "%Y%m%d").date()
    return max((ech - ref).days, 0) / 365.0


def _date_capture(df, repli=None):
    """Date de valorisation = jour de CAPTURE (déterminisme), lue dans 'capture_ts'.
    Repli explicite (ici : le timestamp du nom de fichier) si la colonne manque."""
    if "capture_ts" in df.columns and len(df):
        try:
            return datetime.strptime(str(df["capture_ts"].iloc[0]).split("_")[0],
                                     "%Y%m%d").date()
        except (ValueError, TypeError):
            pass
    return repli or date.today()


def _spot_de_capture(ts_qc):
    """Le 'spot' n'est pas stocké dans les qc_ ; on le relit depuis le brut_ qui
    était le plus récent au moment où ce qc a été produit (même logique que qc.py :
    plus grand timestamp brut <= timestamp qc). Fallback : brut le plus proche."""
    bruts = [(b, _parse_horodatage(b.name))
             for b in sorted(config.DATA.glob(f"brut_{config.SYMBOLE}_*.csv"))]
    bruts = [(b, t) for b, t in bruts if t is not None]
    if not bruts:
        return None
    if ts_qc is not None:
        anterieurs = [(b, t) for b, t in bruts if t <= ts_qc]
        choix = (max(anterieurs, key=lambda bt: bt[1]) if anterieurs
                 else min(bruts, key=lambda bt: abs((bt[1] - ts_qc).total_seconds())))
    else:
        choix = bruts[-1]
    return float(pd.read_csv(choix[0])["spot"].iloc[0])


def metriques_d_une_capture(fichier):
    """Pour un fichier qc_, calcule les métriques de cette capture."""
    df = pd.read_csv(fichier)
    df = df[df["gardee"]]            # seuls les points qui ont survécu au QC
    if df.empty:
        return None

    ts = _parse_horodatage(fichier.name)
    spot = float(df["spot"].iloc[0]) if "spot" in df.columns else _spot_de_capture(ts)
    if spot is None:
        return None
    # Déterminisme : T se calcule au jour de CAPTURE. On le lit dans capture_ts ;
    # à défaut (vieux qc_ sans la colonne) on retombe sur le timestamp du fichier.
    ref = _date_capture(df, repli=(ts.date() if ts else None))

    # Une ligne par échéance : vol ATM + skew (vol 95% strike vs vol 105% strike)
    lignes = []
    for ech, sous in df.groupby("echeance"):
        T = _maturite_annees(ech, ref)
        # vol ATM = vol au strike le plus proche du spot
        idx_atm = (sous["strike"] - spot).abs().idxmin()
        vol_atm = float(sous.loc[idx_atm, "iv_calculee"])

        # skew = vol(strike 95%) - vol(strike 105%) ; mesure simple de la pente
        strike_bas = spot * 0.95
        strike_haut = spot * 1.05
        sous_bas = sous.iloc[(sous["strike"] - strike_bas).abs().argsort()[:1]]
        sous_haut = sous.iloc[(sous["strike"] - strike_haut).abs().argsort()[:1]]
        if len(sous_bas) and len(sous_haut):
            skew = float(sous_bas["iv_calculee"].iloc[0] -
                         sous_haut["iv_calculee"].iloc[0])
        else:
            skew = np.nan

        lignes.append({
            "timestamp": ts, "spot": spot,
            "echeance": int(ech), "T": round(T, 4),
            "vol_atm": round(vol_atm, 4), "skew_95_105": round(skew, 4),
        })
    return lignes


def construire_historique():
    fichiers = sorted(config.CALCUL.glob(f"qc_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError("Aucun fichier qc_ dans data/calcul/.")
    print(f"[historique] {len(fichiers)} captures trouvées.")

    toutes = []
    for f in fichiers:
        lignes = metriques_d_une_capture(f)
        if lignes:
            toutes.extend(lignes)
    hist = pd.DataFrame(toutes).sort_values("timestamp")
    return hist


def tracer(hist):
    # 3 sous-graphiques empilés : spot / vol ATM par échéance / pente du skew
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        subplot_titles=("Spot ESTX50",
                                        "Volatilité ATM par échéance",
                                        "Pente du skew (vol 95% - vol 105%)"),
                        vertical_spacing=0.08)

    # Graphique 1 : spot
    spots = hist.drop_duplicates("timestamp")[["timestamp", "spot"]]
    fig.add_trace(go.Scatter(x=spots["timestamp"], y=spots["spot"],
                             mode="lines+markers", name="spot",
                             line=dict(color="#1D9E75")), row=1, col=1)

    # Graphique 2 : une courbe de vol ATM par échéance
    for ech, sous in hist.groupby("echeance"):
        sous = sous.sort_values("timestamp")
        fig.add_trace(go.Scatter(x=sous["timestamp"], y=sous["vol_atm"],
                                 mode="lines+markers",
                                 name=f"ATM {int(ech)}"), row=2, col=1)

    # Graphique 3 : skew (on prend l'échéance la plus proche pour lisibilité)
    ech_courte = hist["echeance"].min()
    s = hist[hist["echeance"] == ech_courte].sort_values("timestamp")
    fig.add_trace(go.Scatter(x=s["timestamp"], y=s["skew_95_105"],
                             mode="lines+markers",
                             name=f"skew {int(ech_courte)}",
                             line=dict(color="#E45756")), row=3, col=1)

    fig.update_layout(title=f"Historique du marché {config.SYMBOLE}",
                      template="plotly_dark", height=900, showlegend=True)
    sortie = config.CALCUL / "historique.html"
    fig.write_html(str(sortie), auto_open=True)
    print(f"[historique] graphique sauvegardé : {sortie}")


def main():
    hist = construire_historique()
    print(f"[historique] {len(hist)} lignes (captures x échéances).\n")
    # Petit aperçu des dernières captures
    print("Dernières lignes de l'historique :")
    print(hist.tail(10).to_string(index=False))
    tracer(hist)


if __name__ == "__main__":
    main()

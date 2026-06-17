# calcul_iv.py — Étape 8 (industrialisée) : on INVERSE toute la chaîne.
# Lit le dernier fichier brut de data/, calcule NOTRE IV + NOS Greeks pour
# chaque option avec NOTRE moteur (pricing.py), compare à l'IV d'IBKR (témoin),
# et sauvegarde le résultat dans data/calcul/.
#
# Ce fichier ne se connecte PAS à IBKR. Il lit les prix bruts déjà captés
# (séparation des couches : donnees.py capte, ici on calcule).

from datetime import date, datetime
import pandas as pd
import numpy as np

import config
from pricing import vol_implicite, greeks, prix_bs


def _dernier_fichier_brut():
    """Trouve le fichier brut le plus récent dans data/ (le dernier capté)."""
    fichiers = sorted(config.DATA.glob(f"brut_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError(
            "Aucun fichier brut dans data/. Lance d'abord : python donnees.py"
        )
    return fichiers[-1]


def _date_capture(df, repli=None):
    """
    Date de VALORISATION = jour de CAPTURE des prix (déterminisme du PDF :
    rejouer ce calcul un autre jour sur le même brut doit donner le même T,
    donc la même IV). On la lit dans la colonne 'capture_ts' ('AAAAMMJJ_HHMMSS')
    écrite par donnees.py. Repli explicite (jamais muet) si la colonne manque
    (vieux fichiers bruts) : on retombe sur aujourd'hui.
    """
    if "capture_ts" in df.columns and len(df):
        try:
            brut = str(df["capture_ts"].iloc[0]).split("_")[0]
            return datetime.strptime(brut, "%Y%m%d").date()
        except (ValueError, TypeError):
            pass
    return repli or date.today()


def _maturite_annees(echeance_str, jour_reference=None):
    """
    Convertit une échéance 'YYYYMMDD' en fraction d'année T (convention /365).
    jour_reference = date de capture (passée par calculer_iv_chaine), pas today().
    """
    ech = datetime.strptime(str(echeance_str), "%Y%m%d").date()
    ref = jour_reference or date.today()
    jours = (ech - ref).days
    return max(jours, 0) / 365.0


def _prix_a_inverser(ligne):
    """
    Choisit le prix de marché à inverser : le mid (bid+ask)/2 si dispo,
    sinon le close. Renvoie (prix, source) ou (None, raison).
    """
    bid, ask, close = ligne["bid"], ligne["ask"], ligne["close"]
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0, "mid"
    if close is not None and not pd.isna(close) and close > 0:
        return float(close), "close"
    return None, "aucun_prix"


def calculer_iv_chaine(df, jour_reference=None):
    """
    Pour chaque ligne (option) : inverse le prix -> NOTRE IV, calcule NOS Greeks.
    Ajoute des colonnes de diagnostic (étape 8 du framework : jamais de NaN muet).
    """
    r, q = config.TAUX_SANS_RISQUE, config.DIVIDENDE
    # Déterminisme : la date de valorisation est le jour de CAPTURE (lu dans le
    # brut), pas le jour où tourne ce script. Sans ça, rejouer le calcul demain
    # donnerait un T plus court -> une IV différente sur les mêmes prix.
    jour_reference = jour_reference or _date_capture(df)
    resultats = []

    for _, ligne in df.iterrows():
        S = float(ligne["spot"])
        K = float(ligne["strike"])
        cp = 1 if ligne["type"] == "C" else -1
        T = _maturite_annees(ligne["echeance"], jour_reference)

        prix, source_prix = _prix_a_inverser(ligne)
        sortie = {
            "echeance": ligne["echeance"],
            "strike": K,
            "type": ligne["type"],
            "spot": S,                       # propagé pour que iv_ et qc_ portent le spot
            "capture_ts": ligne.get("capture_ts", ""),  # propagé : date de valorisation traçable
            "T_annees": round(T, 4),
            "prix_utilise": prix,
            "source_prix": source_prix,
            "iv_calculee": np.nan,
            "statut": "",
            "iv_ibkr": ligne.get("iv_ibkr", np.nan),
            "ecart_iv": np.nan,
            "delta": np.nan, "gamma": np.nan,
            "vega": np.nan, "theta_jour": np.nan,
        }

        if prix is None or T <= 0:
            sortie["statut"] = "rejet: pas de prix exploitable" if prix is None \
                               else "rejet: echeance passee"
            resultats.append(sortie)
            continue

        sigma, statut = vol_implicite(prix, S, K, T, r, q, cp)
        sortie["statut"] = statut
        if np.isfinite(sigma):
            sortie["iv_calculee"] = round(sigma, 4)
            g = greeks(S, K, T, r, q, sigma, cp)
            sortie["delta"] = round(g["delta"], 4)
            sortie["gamma"] = round(g["gamma"], 6)
            sortie["vega"] = round(g["vega"], 3)
            sortie["theta_jour"] = round(g["theta_jour"], 3)
            # comparaison au témoin IBKR si disponible
            iv_ibkr = ligne.get("iv_ibkr", np.nan)
            if iv_ibkr is not None and not pd.isna(iv_ibkr):
                sortie["ecart_iv"] = round(sigma - float(iv_ibkr), 4)

        resultats.append(sortie)

    return pd.DataFrame(resultats)


def main():
    fichier = _dernier_fichier_brut()
    print(f"[calcul_iv] lecture du brut : {fichier.name}")
    df_brut = pd.read_csv(fichier)

    ref = _date_capture(df_brut)
    print(f"[calcul_iv] date de valorisation (jour de capture) : {ref}")

    df_iv = calculer_iv_chaine(df_brut)

    # Sauvegarde dans la couche "calcul" (recalculable, séparée du brut)
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    sortie = config.CALCUL / f"iv_{config.SYMBOLE}_{horodatage}.csv"
    df_iv.to_csv(sortie, index=False)

    # Petit rapport de qualité (esprit étape 14 : on rend des diagnostics)
    n_total = len(df_iv)
    n_ok = int((df_iv["statut"] == "ok").sum())
    ecarts = df_iv["ecart_iv"].dropna()
    print(f"[calcul_iv] {n_ok}/{n_total} options inversées avec succès")
    if len(ecarts):
        print(f"[calcul_iv] écart médian vs IBKR : {ecarts.abs().median():.4f} "
              f"| écart max : {ecarts.abs().max():.4f}")
    print(f"[calcul_iv] résultat sauvegardé : {sortie.name}\n")

    # Aperçu : les options réussies, triées par strike
    apercu = df_iv[df_iv["statut"] == "ok"].sort_values(["type", "strike"])
    cols = ["type", "strike", "T_annees", "prix_utilise", "source_prix",
            "iv_calculee", "iv_ibkr", "ecart_iv", "delta", "vega"]
    print(apercu[cols].to_string(index=False))


if __name__ == "__main__":
    main()

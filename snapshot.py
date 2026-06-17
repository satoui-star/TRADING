# snapshot.py — Étape 5 du framework : MARKET-STATE SNAPSHOTS.
#
# Le PDF (Step 5 + dictionnaire de données) demande une couche d'état de marché
# DÉTERMINISTE entre le brut et les analytics, qui :
#   - choisit un PRIX DE RÉFÉRENCE par option : mid (bid+ask)/2 quand fiable,
#     sinon repli sur le close — TOUJOURS labellisé (reference_type), jamais caché ;
#   - calcule le spread % et pose des DRAPEAUX d'état (stale / spread large /
#     croisé / sans prix) ;
#   - porte aussi le prix de référence du sous-jacent et son type.
#
# Honnêteté (PDF Partie XIX) : `quote_age_seconds` n'est PAS calculable ici — le
# brut est une capture instantanée unique (un seul capture_ts pour toute la
# chaîne), sans horodatage par tick (cela exigerait le journal d'événements
# streaming de l'étape 3, non implémenté). La colonne existe (schéma du PDF) mais
# vaut NaN, documenté plutôt que fabriqué.
#
# Module PUR : il consomme un brut (DataFrame) et émet un DataFrame d'état. Il ne
# se connecte à rien et ne calcule aucune IV.

from datetime import datetime
import numpy as np
import pandas as pd

import config

SPREAD_LARGE = config.SNAP_SPREAD_LARGE_PCT   # seuil "spread large" (config.py)


def etat_option(bid, ask, close):
    """
    Choisit le prix de référence d'UNE option et son diagnostic d'état.
    Renvoie (reference_price, reference_type, spread_pct, flag).

    Règles (ordre de priorité) :
      - bid/ask valides et non croisés -> mid ; flag "spread_large" si trop large.
      - bid/ask croisés (bid > ask)     -> repli close ; flag "croise".
      - sinon close > 0                 -> close ; flag "stale_close_only".
      - sinon                           -> aucun prix ; flag "sans_prix".
    """
    bid_ok = bid is not None and not pd.isna(bid) and bid > 0
    ask_ok = ask is not None and not pd.isna(ask) and ask > 0
    close_ok = close is not None and not pd.isna(close) and close > 0

    if bid_ok and ask_ok:
        if bid > ask:   # marché croisé : mid non fiable, on retombe sur le close
            if close_ok:
                return float(close), "close", np.nan, "croise"
            return None, "aucun", np.nan, "croise"
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid if mid > 0 else np.nan
        flag = "spread_large" if (np.isfinite(spread_pct) and spread_pct > SPREAD_LARGE) else "ok"
        return float(mid), "mid", float(spread_pct), flag

    if close_ok:
        return float(close), "close", np.nan, "stale_close_only"

    return None, "aucun", np.nan, "sans_prix"


def reference_sous_jacent(spot):
    """Prix de référence du sous-jacent + type. donnees.py capte `last or close`
    sur l'indice sans bid/ask : on labellise honnêtement l'origine."""
    return float(spot), "indice_last_ou_close"


def construire_snapshot(df_brut):
    """
    Construit le market-state snapshot (un par option) depuis un brut.
    PUR et DÉTERMINISTE : tri explicite, aucune horloge, aucune I/O.
    """
    spot = float(df_brut["spot"].iloc[0])
    ref_spot, ref_spot_type = reference_sous_jacent(spot)
    cap = str(df_brut["capture_ts"].iloc[0]) if "capture_ts" in df_brut.columns else ""

    lignes = []
    for _, r in df_brut.iterrows():
        prix, rtype, spread, flag = etat_option(r.get("bid"), r.get("ask"), r.get("close"))
        lignes.append({
            "echeance": int(r["echeance"]),
            "strike": float(r["strike"]),
            "type": r["type"],
            "bid": r.get("bid"), "ask": r.get("ask"), "close": r.get("close"),
            "mid_option_price": prix,
            "reference_type": rtype,
            "spread_pct": round(spread, 5) if np.isfinite(spread) else np.nan,
            "quote_age_seconds": np.nan,          # non capturé (capture instantanée)
            "flag": flag,
            "reference_spot": ref_spot,
            "reference_type_spot": ref_spot_type,
            "capture_ts": cap,
            "snapshot_version": config.SNAPSHOT_VERSION,
        })
    snap = pd.DataFrame(lignes)
    return snap.sort_values(["echeance", "strike", "type"]).reset_index(drop=True)


def resume(snap):
    """Petit résumé d'état pour la console / l'opérateur."""
    n = len(snap)
    par_flag = snap["flag"].value_counts().to_dict()
    return n, par_flag


# ===========================================================================
# Orchestration / artefact
# ===========================================================================
def _dernier_brut():
    fichiers = sorted(config.DATA.glob(f"brut_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError("Aucun brut_. Lance d'abord : python donnees.py")
    return fichiers[-1]


def main():
    fichier = _dernier_brut()
    print(f"[snapshot] lecture du brut : {fichier.name}")
    df = pd.read_csv(fichier)
    snap = construire_snapshot(df)

    n, par_flag = resume(snap)
    spot = float(df["spot"].iloc[0])
    print(f"[snapshot] spot de référence : {spot:.2f} (type : indice_last_ou_close)")
    print(f"[snapshot] {n} états d'option construits. Répartition des drapeaux :")
    for flag, cpt in sorted(par_flag.items(), key=lambda kv: -kv[1]):
        print(f"      {cpt:4d}  {flag}")

    horo = datetime.now().strftime("%Y%m%d_%H%M%S")
    sortie = config.CALCUL / f"market_state_{config.SYMBOLE}_{horo}.csv"
    snap.to_csv(sortie, index=False)
    print(f"[snapshot] market-state sauvegardé : {sortie.name}")


if __name__ == "__main__":
    main()

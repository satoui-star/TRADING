# donnees.py — Étapes 2-3 du framework : inventaire + capture des prix BRUTS.
# VERSION PAR LOTS : on capte UNE ÉCHÉANCE À LA FOIS, avec une attente dédiée,
# au lieu d'un seul sleep global (qui laissait les échéances lointaines vides).
# Ce fichier NE CALCULE RIEN : il capte et il archive.

from datetime import datetime
import pandas as pd
from ib_async import Index, Option

import config
from connexion import ouvrir

# Paramètres de capture : centralisés dans config.py (Partie VII du PDF).
NB_ECHEANCES = config.NB_ECHEANCES   # nombre d'échéances captées (les N plus proches)
ATTENTE_MAX = config.ATTENTE_MAX     # attente max par échéance (s) pour prix différés
SEUIL_PRET = config.SEUIL_PRET       # échéance "prête" quand ce % de contrats a un prix


def capter_spot(ib):
    """Étape 3 : le prix de référence du sous-jacent (donnée brute)."""
    indice = Index(config.SYMBOLE, config.BOURSE, config.DEVISE)
    ib.qualifyContracts(indice)
    tk = ib.reqMktData(indice, "", snapshot=True)
    ib.sleep(3)
    spot = tk.last or tk.close
    print(f"[donnees] spot {config.SYMBOLE} = {spot}")
    return float(spot), indice


def inventaire(ib, indice, spot):
    """Étape 2 : le catalogue (N échéances proches + strikes autour du spot)."""
    chaines = ib.reqSecDefOptParams(indice.symbol, "", indice.secType, indice.conId)
    classe = next(c for c in chaines if c.tradingClass == config.CLASSE_OPTIONS)
    echeances = sorted(classe.expirations)[:NB_ECHEANCES]
    strikes = sorted(
        k for k in classe.strikes
        if spot * (1 - config.BANDE_STRIKES) <= k <= spot * (1 + config.BANDE_STRIKES)
        and k % config.PAS_STRIKE == 0
    )
    print(f"[donnees] {len(echeances)} échéances : {echeances}")
    print(f"[donnees] {len(strikes)} strikes par échéance")
    return echeances, strikes


def _prix_present(tk):
    """Un prix exploitable est arrivé si on a un close, ou un bid/ask positifs."""
    close_ok = tk.close is not None and tk.close == tk.close and tk.close > 0  # != NaN
    bidask_ok = (tk.bid is not None and tk.ask is not None
                 and tk.bid > 0 and tk.ask > 0)
    return close_ok or bidask_ok


def capter_une_echeance(ib, echeance, strikes):
    """
    Étape 3 : capte les prix bruts d'UNE échéance, avec attente active.
    On demande tous les contrats de l'échéance, puis on attend (par paliers de 1 s)
    que la plupart aient reçu un prix, sans dépasser ATTENTE_MAX.
    """
    contrats = [
        Option(config.SYMBOLE, echeance, K, droit, config.BOURSE,
               multiplier=config.MULTIPLICATEUR, currency=config.DEVISE,
               tradingClass=config.CLASSE_OPTIONS)
        for K in strikes for droit in ["C", "P"]
    ]
    contrats = [c for c in ib.qualifyContracts(*contrats) if c.conId]
    tickers = [(c, ib.reqMktData(c, "", snapshot=False)) for c in contrats]

    # Attente active : on s'arrête dès que SEUIL_PRET est atteint, sinon ATTENTE_MAX.
    cible = max(1, int(len(tickers) * SEUIL_PRET))
    for _ in range(ATTENTE_MAX):
        ib.sleep(1)
        prets = sum(1 for _, tk in tickers if _prix_present(tk))
        if prets >= cible:
            break

    lignes = []
    for c, tk in tickers:
        lignes.append({
            "echeance": c.lastTradeDateOrContractMonth,
            "strike": c.strike,
            "type": c.right,
            "bid": tk.bid,
            "ask": tk.ask,
            "close": tk.close,
            "iv_ibkr": tk.modelGreeks.impliedVol if tk.modelGreeks else None,
        })

    # On annule les souscriptions de cette échéance avant de passer à la suivante
    # (libère le quota de lignes de données simultanées d'IBKR).
    for c, _ in tickers:
        ib.cancelMktData(c)

    prets = sum(1 for _, tk in tickers if _prix_present(tk))
    print(f"[donnees]   {echeance} : {prets}/{len(tickers)} prix reçus")
    return lignes


def capter_prix(ib, echeances, strikes):
    """Boucle sur les échéances, une par une (capture par lots)."""
    toutes = []
    for ech in echeances:
        toutes.extend(capter_une_echeance(ib, ech, strikes))
    return pd.DataFrame(toutes)


def archiver(spot, df):
    """Étape 4 : ranger les prix BRUTS, datés, immuables, dans data/."""
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    fichier = config.DATA / f"brut_{config.SYMBOLE}_{horodatage}.csv"
    df["spot"] = spot
    df["capture_ts"] = horodatage
    df.to_csv(fichier, index=False)
    print(f"[donnees] {len(df)} prix bruts archivés dans {fichier.name}")
    return fichier


if __name__ == "__main__":
    ib = ouvrir()
    try:
        spot, indice = capter_spot(ib)
        echeances, strikes = inventaire(ib, indice, spot)
        print("[donnees] capture par lots (une échéance à la fois)...")
        df = capter_prix(ib, echeances, strikes)
        archiver(spot, df)
        print("\nPrix close reçus par échéance :")
        print(df.groupby("echeance")["close"].count().to_string())
    finally:
        ib.disconnect()
        print("[donnees] session fermée.")
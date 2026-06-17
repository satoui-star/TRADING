# strategies.py — Bibliothèque des 6 templates de stratégies optionnelles.
# Chaque fonction prend (echeance, strike_atm) + paramètres spécifiques
# et renvoie un DataFrame de positions au format de risque.py / positions.csv :
# colonnes : echeance, strike, type ('C'/'P'), quantite (positif=long, négatif=short)
#
# Ce module ne fait QUE construire des positions. Il n'évalue rien.
# L'évaluation reste le boulot de risque.py qui appelle pricing.py.

import pandas as pd


def long_straddle(echeance, strike_atm, quantite=1):
    """Achat call + achat put même strike. Long volatilité, neutre directionnel."""
    return pd.DataFrame([
        {"echeance": echeance, "strike": strike_atm, "type": "C", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm, "type": "P", "quantite": quantite},
    ])


def short_straddle(echeance, strike_atm, quantite=1):
    """Inverse : on encaisse la prime, on gagne si le marché stagne."""
    return long_straddle(echeance, strike_atm, -quantite)


def long_strangle(echeance, strike_atm, ecart_strikes=100, quantite=1):
    """Achat call OTM + achat put OTM. Long vol, moins cher qu'un straddle."""
    return pd.DataFrame([
        {"echeance": echeance, "strike": strike_atm + ecart_strikes, "type": "C", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm - ecart_strikes, "type": "P", "quantite": quantite},
    ])


def short_strangle(echeance, strike_atm, ecart_strikes=100, quantite=1):
    """L'inverse. Très utilisé par les vendeurs de prime."""
    return long_strangle(echeance, strike_atm, ecart_strikes, -quantite)


def iron_condor(echeance, strike_atm, ecart_interne=50, ecart_externe=150, quantite=1):
    """
    Iron condor (vendu, le plus courant) : on vend un strangle (ailes internes)
    et on rachète un strangle plus large (protection). Profit limité, perte limitée.
    """
    return pd.DataFrame([
        # vente du strangle interne
        {"echeance": echeance, "strike": strike_atm + ecart_interne, "type": "C", "quantite": -quantite},
        {"echeance": echeance, "strike": strike_atm - ecart_interne, "type": "P", "quantite": -quantite},
        # rachat du strangle externe (protection)
        {"echeance": echeance, "strike": strike_atm + ecart_externe, "type": "C", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm - ecart_externe, "type": "P", "quantite": quantite},
    ])


def long_butterfly(echeance, strike_atm, largeur_ailes=100, quantite=1):
    """
    Long butterfly call : +1 call (K-L) -2 calls K +1 call (K+L).
    Profite si le marché reste exactement autour de K à l'échéance.
    """
    return pd.DataFrame([
        {"echeance": echeance, "strike": strike_atm - largeur_ailes, "type": "C", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm,                  "type": "C", "quantite": -2 * quantite},
        {"echeance": echeance, "strike": strike_atm + largeur_ailes, "type": "C", "quantite": quantite},
    ])


def risk_reversal(echeance, strike_atm, ecart_strikes=100, quantite=1):
    """
    Risk reversal : achat call OTM + vente put OTM. Pari directionnel haussier
    quasi-gratuit (la prime du put compense celle du call). Très utilisé par
    les desks pour parier sur le skew.
    """
    return pd.DataFrame([
        {"echeance": echeance, "strike": strike_atm + ecart_strikes, "type": "C", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm - ecart_strikes, "type": "P", "quantite": -quantite},
    ])


def call_spread(echeance, strike_atm, largeur=100, quantite=1):
    """Achat call K + vente call K+L. Pari haussier à risque limité."""
    return pd.DataFrame([
        {"echeance": echeance, "strike": strike_atm,           "type": "C", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm + largeur, "type": "C", "quantite": -quantite},
    ])


def put_spread(echeance, strike_atm, largeur=100, quantite=1):
    """Achat put K + vente put K-L. Pari baissier à risque limité."""
    return pd.DataFrame([
        {"echeance": echeance, "strike": strike_atm,           "type": "P", "quantite": quantite},
        {"echeance": echeance, "strike": strike_atm - largeur, "type": "P", "quantite": -quantite},
    ])


# Catalogue : nom affiché -> (fonction, paramètres par défaut)
# C'est ce que le dashboard utilise pour construire son menu déroulant.
CATALOGUE = {
    "Long straddle (acheteur de vol)":  long_straddle,
    "Short straddle (vendeur de vol)":  short_straddle,
    "Long strangle":                    long_strangle,
    "Short strangle":                   short_strangle,
    "Iron condor (vendu)":              iron_condor,
    "Long butterfly":                   long_butterfly,
    "Risk reversal (haussier)":         risk_reversal,
    "Call spread (haussier)":           call_spread,
    "Put spread (baissier)":            put_spread,
}

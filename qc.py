# qc.py — Étape 7 du framework : contrôle qualité des quotes.
# On ne garde que les options ECONOMIQUEMENT FIABLES pour la nappe :
#   - règle OTM : puts sous le spot, calls au-dessus (les liquides, riches en valeur temps)
#   - on écarte les prix trop faibles (illiquides) et les IV aberrantes
# Chaque rejet porte un CODE DE RAISON (exigence du PDF : QC auditable, pas de rejet muet).
#
# Ce fichier ne se connecte à rien et ne calcule aucune IV : il TRIE des lignes
# déjà calculées par calcul_iv.py.

from datetime import datetime
import pandas as pd
import numpy as np

import config

# --- Seuils de QC : centralisés dans config.py (Partie VII du PDF) ---
PRIX_MIN = config.QC_PRIX_MIN          # sous ce prix : illiquide / bruité
IV_MIN, IV_MAX = config.QC_IV_MIN, config.QC_IV_MAX   # IV plausible sur indice


def _dernier_fichier_iv():
    fichiers = sorted(config.CALCUL.glob(f"iv_{config.SYMBOLE}_*.csv"))
    if not fichiers:
        raise FileNotFoundError(
            "Aucun fichier iv_ dans data/calcul/. Lance d'abord : python calcul_iv.py"
        )
    return fichiers[-1]


def appliquer_qc(df, spot):
    """
    Marque chaque ligne : gardee=True/False + raison_rejet.
    Règle maîtresse : pour chaque strike on ne garde que l'option OTM.
    """
    lignes = []
    for _, r in df.iterrows():
        K = float(r["strike"])
        type_opt = r["type"]            # 'C' ou 'P'
        gardee = True
        raison = "ok"

        # 1) Sélection OTM : c'est le filtre principal (résout les ITM aberrants)
        #    K < spot  -> on veut le PUT (le call y est ITM profond, instable)
        #    K >= spot -> on veut le CALL (le put y est ITM profond, instable)
        est_otm = (K < spot and type_opt == "P") or (K >= spot and type_opt == "C")
        if not est_otm:
            gardee, raison = False, "rejet_ITM (on garde l'OTM a ce strike)"

        # 2) Le calcul d'IV doit avoir réussi
        elif r["statut"] != "ok" or not np.isfinite(r["iv_calculee"]):
            gardee, raison = False, "rejet_solveur (IV non calculee)"

        # 3) Prix trop faible -> illiquide, IV peu fiable
        elif r["prix_utilise"] is None or float(r["prix_utilise"]) < PRIX_MIN:
            gardee, raison = False, "rejet_prix_trop_faible"

        # 4) IV hors bornes plausibles -> aberration
        elif not (IV_MIN <= float(r["iv_calculee"]) <= IV_MAX):
            gardee, raison = False, "rejet_IV_hors_bornes"

        ligne = dict(r)
        ligne["gardee"] = gardee
        ligne["raison_rejet"] = raison
        lignes.append(ligne)

    return pd.DataFrame(lignes)


def main():
    fichier = _dernier_fichier_iv()
    print(f"[qc] lecture des IV calculées : {fichier.name}")
    df = pd.read_csv(fichier)

    spot = float(df["spot"].iloc[0]) if "spot" in df.columns else None
    # 'spot' n'est pas dans le fichier iv_ ; on le relit depuis le brut correspondant
    if spot is None or pd.isna(spot):
        brut = sorted(config.DATA.glob(f"brut_{config.SYMBOLE}_*.csv"))[-1]
        spot = float(pd.read_csv(brut)["spot"].iloc[0])
    print(f"[qc] spot de référence : {spot}")

    df_qc = appliquer_qc(df, spot)
    df_qc["spot"] = spot   # on stocke le spot ici : risque/historique/dashboard le reliront directement

    # Sauvegarde de la chaîne filtrée (couche calcul)
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    sortie = config.CALCUL / f"qc_{config.SYMBOLE}_{horodatage}.csv"
    df_qc.to_csv(sortie, index=False)

    # Rapport de QC : combien gardé, et pourquoi les rejets (esprit étape 14)
    n = len(df_qc)
    gardees = df_qc[df_qc["gardee"]]
    print(f"[qc] {len(gardees)}/{n} options retenues pour la nappe")
    print("[qc] motifs de rejet :")
    for raison, cpt in df_qc[~df_qc["gardee"]]["raison_rejet"].value_counts().items():
        print(f"      {cpt:3d}  {raison}")
    print(f"[qc] chaîne filtrée sauvegardée : {sortie.name}\n")

    # Aperçu du SMILE propre (les points gardés, triés par strike)
    print("Points retenus (le smile propre) :")
    cols = ["type", "strike", "iv_calculee", "iv_ibkr", "ecart_iv", "delta"]
    print(gardees.sort_values("strike")[cols].to_string(index=False))


if __name__ == "__main__":
    main()

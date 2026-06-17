# connexion.py — Étape 1 du framework : ouvrir une session IBKR fiable.
# Ce fichier ne fait QUE gérer la connexion. Aucun calcul, aucune capture de données.

from ib_async import IB
import config

def ouvrir():
    """Ouvre une connexion à IBKR et la renvoie. Données en différé (3)."""
    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID, timeout=10)
    ib.reqMarketDataType(3)   # 3 = données différées (gratuites)
    print(f"[connexion] OK — connecté : {ib.isConnected()}")
    return ib

# Test direct : python connexion.py
if __name__ == "__main__":
    ib = ouvrir()
    print("Heure serveur :", ib.reqCurrentTime())
    ib.disconnect()
    print("[connexion] fermée proprement.")
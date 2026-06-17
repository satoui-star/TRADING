# connexion.py — Étape 1 du framework : ouvrir une session IBKR FIABLE.
# Ce fichier ne fait QUE gérer la connexion. Aucun calcul, aucune capture.
#
# Le PDF (Step 1 + Partie XII connectivity/session.py) demande une session qui
# possède sa propre logique :
#   - machine à états : DECONNECTE / CONNEXION / CONNECTE / DEGRADE / RECONNEXION ;
#   - reconnexion avec backoff EXPONENTIEL + JITTER ;
#   - heartbeat (aller-retour broker) et âge du heartbeat ;
#   - validation du client id au démarrage (évite les collisions entre services) ;
#   - logs structurés à chaque transition d'état ;
#   - « refuse de se déclarer sain tant qu'un aller-retour n'a pas réussi ».
#
# Compat : la fonction historique `ouvrir()` est conservée (donnees.py l'utilise)
# et renvoie toujours l'objet IB connecté, en données différées (type 3).

import random
import time
from datetime import datetime, timezone

import config

# Import tolérant : sans ib_async (mode hors-ligne), importer ce module ne casse
# pas — seule la connexion live est indisponible, signalée par une erreur claire.
try:
    from ib_async import IB
except Exception:           # pragma: no cover - dépend de l'environnement
    IB = None


class SessionIBKR:
    """Possède le cycle de vie de la connexion IBKR (PDF connectivity/session.py)."""

    DECONNECTE = "DECONNECTE"
    CONNEXION = "CONNEXION"
    CONNECTE = "CONNECTE"
    DEGRADE = "DEGRADE"
    RECONNEXION = "RECONNEXION"

    def __init__(self, host=None, port=None, client_id=None):
        if IB is None:
            raise RuntimeError(
                "ib_async non installé : connexion live indisponible (mode hors-ligne). "
                "Installez-la pour la capture : pip install ib_async"
            )
        self.host = host or config.IB_HOST
        self.port = port or config.IB_PORT
        self.client_id = config.IB_CLIENT_ID if client_id is None else client_id
        self._valider_client_id()
        self.ib = IB()
        self.etat = self.DECONNECTE
        self.dernier_heartbeat = None   # datetime UTC du dernier aller-retour réussi

    # --- garde-fous & journalisation -------------------------------------
    def _valider_client_id(self):
        if not isinstance(self.client_id, int) or self.client_id < 0:
            raise ValueError(
                f"client id invalide ({self.client_id!r}) : un entier >= 0 est "
                "attendu (un id par service, pour éviter les collisions de session)."
            )

    def _log(self, message):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[connexion][{ts}][{self.etat}] {message}")

    def _transition(self, etat, message=""):
        ancien, self.etat = self.etat, etat
        self._log(f"{ancien} -> {etat} {message}".rstrip())

    def _backoff(self, tentative):
        """Backoff exponentiel plafonné + jitter (0..25 %) pour lisser les reprises."""
        base = min(config.IB_BACKOFF_BASE * (2 ** tentative), config.IB_BACKOFF_MAX)
        return base + random.uniform(0.0, base * 0.25)

    # --- cycle de vie -----------------------------------------------------
    def connecter(self):
        """Connecte avec retries/backoff. Ne déclare CONNECTE qu'après un
        aller-retour réussi. Lève ConnectionError après épuisement des tentatives."""
        self._transition(self.CONNEXION,
                         f"vers {self.host}:{self.port} (client {self.client_id})")
        derniere_erreur = None
        for tentative in range(config.IB_MAX_RETRIES):
            try:
                self.ib.connect(self.host, self.port, clientId=self.client_id,
                                timeout=config.IB_TIMEOUT)
                self.ib.reqMarketDataType(3)            # 3 = différé (gratuit)
                if self.battre_coeur():                 # santé = 1 aller-retour OK
                    self._transition(self.CONNECTE, "aller-retour broker OK")
                    return self.ib
                derniere_erreur = "heartbeat initial échoué"
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
            except Exception as e:
                derniere_erreur = e
            if tentative < config.IB_MAX_RETRIES - 1:
                attente = self._backoff(tentative)
                self._transition(
                    self.RECONNEXION,
                    f"échec ({derniere_erreur}); nouvelle tentative dans {attente:.1f}s "
                    f"[{tentative + 1}/{config.IB_MAX_RETRIES}]")
                time.sleep(attente)
        self._transition(self.DECONNECTE,
                         f"abandon après {config.IB_MAX_RETRIES} tentatives")
        raise ConnectionError(
            f"connexion IBKR impossible ({self.host}:{self.port}) : {derniere_erreur}")

    def battre_coeur(self):
        """Aller-retour broker (reqCurrentTime). Met à jour l'âge du heartbeat.
        Renvoie True si OK ; sinon passe en DEGRADE et renvoie False."""
        try:
            self.ib.reqCurrentTime()
            self.dernier_heartbeat = datetime.now(timezone.utc)
            return True
        except Exception as e:
            self._transition(self.DEGRADE, f"heartbeat échoué ({e})")
            return False

    def age_heartbeat(self):
        """Âge (s) du dernier aller-retour réussi, ou None si jamais réussi."""
        if self.dernier_heartbeat is None:
            return None
        return (datetime.now(timezone.utc) - self.dernier_heartbeat).total_seconds()

    def est_sain(self):
        """Sain = CONNECTE, socket ouverte, et heartbeat récent (< tolérance SLO)."""
        age = self.age_heartbeat()
        return (self.etat == self.CONNECTE and self.ib.isConnected()
                and age is not None and age < config.IB_HEARTBEAT_TOL_S)

    def reconnecter(self):
        """Coupe proprement puis relance la séquence de connexion."""
        self._transition(self.RECONNEXION, "reconnexion demandée")
        try:
            self.ib.disconnect()
        except Exception:
            pass
        return self.connecter()

    def fermer(self):
        try:
            self.ib.disconnect()
        finally:
            self._transition(self.DECONNECTE, "session fermée")


def ouvrir():
    """Compat historique : ouvre une session fiable et renvoie l'objet IB connecté
    (données différées). Signature inchangée pour donnees.py."""
    session = SessionIBKR()
    ib = session.connecter()
    print(f"[connexion] OK — connecté : {ib.isConnected()} | client {session.client_id} "
          f"| âge heartbeat {session.age_heartbeat():.1f}s")
    return ib


# Test direct : python connexion.py
if __name__ == "__main__":
    session = SessionIBKR()
    ib = session.connecter()
    print("Heure serveur :", ib.reqCurrentTime())
    print(f"Sain ? {session.est_sain()} | âge heartbeat : {session.age_heartbeat():.1f}s")
    session.fermer()
    print("[connexion] fermée proprement.")

# daily.py — Étape 13 du framework : capture quotidienne (ou intraday).
# Enchaîne donnees -> calcul_iv -> qc en une seule commande, pour alimenter
# l'historique. Chaque exécution produit un instantané daté qui s'ajoute aux
# précédents (rien n'est écrasé : tous les fichiers gardent leur horodatage).
#
# Lancement manuel : python daily.py
# (Plus tard : automatisable via le Planificateur de tâches Windows.)

from datetime import datetime
import sys
import traceback

# On importe le 'main' de chaque module et on les enchaîne.
# Chaque module garde son rôle : donnees capte, calcul_iv calcule, qc filtre.
import donnees
import calcul_iv
import qc
import forward
import validation


def main():
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"[daily] capture du {horodatage}")
    print(f"{'='*60}\n")

    # Étape 1 : capture des prix bruts depuis IBKR
    print("[daily] 1/5 — capture des prix bruts...")
    try:
        donnees.__name__ = "__main__"  # déclenche le bloc if __name__ == "__main__"
        exec(open(donnees.__file__).read(), {"__name__": "__main__",
                                             "__file__": donnees.__file__})
    except SystemExit:
        pass
    except Exception:
        print("[daily] échec à l'étape donnees :")
        traceback.print_exc()
        sys.exit(1)

    # Étape 2 : calcul d'IV + Greeks sur la chaîne
    print("\n[daily] 2/5 — calcul des IV...")
    try:
        calcul_iv.main()
    except Exception:
        print("[daily] échec à l'étape calcul_iv :")
        traceback.print_exc()
        sys.exit(1)

    # Étape 3 : contrôle qualité (filtrage OTM)
    print("\n[daily] 3/5 — contrôle qualité...")
    try:
        qc.main()
    except Exception:
        print("[daily] échec à l'étape qc :")
        traceback.print_exc()
        sys.exit(1)

    # Étape 4 : forward par parité (lit le brut, persiste courbe + diagnostics)
    print("\n[daily] 4/5 — forward par parité...")
    try:
        forward.main()
    except Exception:
        print("[daily] échec à l'étape forward :")
        traceback.print_exc()
        sys.exit(1)

    # Étape 5 : suite de validation (QA, anomalies, table de triage)
    print("\n[daily] 5/5 — suite de validation...")
    try:
        validation.main()
    except Exception:
        print("[daily] échec à l'étape validation :")
        traceback.print_exc()
        sys.exit(1)

    print(f"\n[daily] capture du {horodatage} terminée.")
    print("[daily] fichiers datés et archivés : brut_, iv_, qc_, forward_, validation_.")


if __name__ == "__main__":
    main()

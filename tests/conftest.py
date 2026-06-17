# conftest.py — met la racine du projet sur le sys.path pour que les tests
# puissent importer config / pricing / forward / etc. (les modules sont à la
# racine, les tests dans tests/). Aucun effet de bord ailleurs.
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)

"""
Point d'entree Dagster du workspace -- ne contient AUCUN asset.

C'est le regroupement a la racine : Dagster ne charge que ce fichier
(PYTHONPATH=/workspace), et ce fichier va chercher les definitions de chaque
projet pour n'en presenter qu'une seule interface.

    /workspace/
    ├── definitions.py        <- ce fichier, tu n'as pas a le modifier
    ├── README.md
    ├── diagnostic/
    │   └── definitions.py    <- les assets du projet "diagnostic"
    └── mon-projet/
        └── definitions.py    <- decouvert automatiquement

Creer un projet ne demande donc pas de toucher a ce fichier : un dossier avec
un definitions.py dedans suffit, il apparait dans l'interface au prochain
rechargement du code. C'est voulu -- un fichier central a editer a chaque
nouveau projet finit toujours par etre oublie, et le projet reste invisible
sans qu'on comprenne pourquoi.

Regle de nommage : chaque projet expose une variable "defs" de type
Definitions. Un dossier qui n'a pas de definitions.py est ignore (une
application web sans job Dagster, par exemple) -- c'est parfaitement normal.
"""
import importlib.util
import os
import sys
import traceback

from dagster import Definitions

WORKSPACE = os.path.dirname(os.path.abspath(__file__))

# Les dossiers commencant par "." ou "_" sont ignores : .codelab (marqueurs
# internes), .venv, __pycache__, et par convention "_quelque-chose" pour un
# projet mis de cote sans le supprimer.
def _dossiers_de_projet():
    for nom in sorted(os.listdir(WORKSPACE)):
        if nom.startswith(".") or nom.startswith("_"):
            continue
        chemin = os.path.join(WORKSPACE, nom)
        if os.path.isdir(chemin) and os.path.isfile(os.path.join(chemin, "definitions.py")):
            yield nom, chemin


def _charger(nom, chemin):
    """Importe le definitions.py d'un projet et renvoie son objet defs."""
    fichier = os.path.join(chemin, "definitions.py")
    # Le dossier du projet est ajoute au chemin d'import pour que ses modules
    # voisins ("import checks") se resolvent sans prefixe de package. C'est ce
    # qui permet d'ecrire un projet comme un simple dossier de scripts.
    if chemin not in sys.path:
        sys.path.insert(0, chemin)

    spec = importlib.util.spec_from_file_location(f"projet_{nom}", fichier)
    module = importlib.util.module_from_spec(spec)
    # Enregistre avant execution : sans ca, un module qui s'auto-importe ou
    # qui utilise des dataclasses echoue avec une erreur peu parlante.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    defs = getattr(module, "defs", None)
    if defs is None:
        raise AttributeError(
            f"{fichier} ne definit pas de variable 'defs'. "
            f"Attendu : defs = Definitions(assets=[...])")
    return defs


chargees = []
for nom, chemin in _dossiers_de_projet():
    try:
        chargees.append(_charger(nom, chemin))
        print(f"[workspace] projet '{nom}' charge.", file=sys.stderr)
    except Exception:
        # Volontairement non fatal. Une erreur de syntaxe dans un projet ne
        # doit pas rendre TOUS les projets invisibles dans l'interface : on
        # perdrait aussi les jobs qui marchent, et la panne serait bien plus
        # dure a localiser qu'avec ce message.
        print(f"[workspace] projet '{nom}' IGNORE, il ne se charge pas :", file=sys.stderr)
        traceback.print_exc()

# Definitions.merge fusionne assets, jobs, schedules, sensors et ressources.
# La liste vide est un cas normal : workspace neuf, ou aucun projet n'a encore
# de definitions.py. Dagster demarre alors avec une interface vide plutot que
# de refuser de se charger.
defs = Definitions.merge(*chargees) if chargees else Definitions()

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
Definitions. Ses assets sont ranges sous son nom (groupe et etiquette
codelab/projet, voir _marquer) : rien a declarer pour les distinguer. Un dossier qui n'a pas de definitions.py est ignore (une
application web sans job Dagster, par exemple) -- c'est parfaitement normal.
"""
import importlib.util
import os
import re
import sys
import traceback
import warnings

from dagster import Definitions

WORKSPACE = os.path.dirname(os.path.abspath(__file__))

# Les dossiers commencant par "." ou "_" sont ignores : .codelab (marqueurs
# internes), .venv, __pycache__, et par convention "_quelque-chose" pour un
# projet mis de cote sans le supprimer.
#
# Un projet qui porte un requirements-dagster.txt est ignore lui aussi : il a
# ses propres dependances, et tourne dans son propre environnement Python,
# declare a part par codelab-dagster (voir dagster/isolation.py dans le depot
# CodeLab). Le charger ici aussi l'importerait sans ses paquets -- et en
# double.
ISOLE = "requirements-dagster.txt"

# L'etiquette posee sur chaque asset, avec le nom exact du projet : le
# catalogue d'assets de Dagster filtre dessus (codelab/projet=mon-projet).
ETIQUETTE_PROJET = "codelab/projet"


def _dossiers_de_projet():
    for nom in sorted(os.listdir(WORKSPACE)):
        if nom.startswith(".") or nom.startswith("_"):
            continue
        chemin = os.path.join(WORKSPACE, nom)
        if not (os.path.isdir(chemin) and os.path.isfile(os.path.join(chemin, "definitions.py"))):
            continue
        if os.path.isfile(os.path.join(chemin, ISOLE)):
            print(f"[workspace] projet '{nom}' : charge dans son propre environnement "
                  f"({ISOLE}), pas ici.", file=sys.stderr)
            continue
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


def groupe_du_projet(nom):
    """Le nom de groupe Dagster d'un projet : Dagster n'accepte que
    [A-Za-z0-9_], donc mon-projet devient mon_projet."""
    return re.sub(r"[^A-Za-z0-9_]", "_", nom)


def _marquer(nom, defs):
    """Range chaque asset du projet sous son nom.

    Vecu : « possible d'identifier et separer les taches sur Dagster par
    projet ? ». Tous les projets partagent un seul emplacement de code
    ("definitions") ; seul un group_name pose a la main les distinguait, et
    l'exemple de la documentation (group_name="mon-projet") etait refuse
    par Dagster a cause du tiret.

    Un asset sans groupe (ou dans "default") prend le groupe du projet ; un
    groupe choisi par le projet est garde. Tous recoivent l'etiquette
    ETIQUETTE_PROJET.

    ESSAYE ET ECARTE : un emplacement de code par projet (ce que fait deja
    dagster/isolation.py pour les projets isoles). Separation plus nette,
    mais 200 a 300 Mo par projet, un redemarrage au lieu de *Reload* pour
    voir un nouveau projet, et l'historique des plannings coupe en deux.
    """
    groupe = groupe_du_projet(nom)

    def ranger(spec):
        changements = {"tags": {**spec.tags, ETIQUETTE_PROJET: nom}}
        if spec.group_name in (None, "default"):
            changements["group_name"] = groupe
        return spec.replace_attributes(**changements)

    try:
        # map_asset_specs est marquee « preview » par Dagster : sans danger
        # ici, la version est figee (dagster/Dockerfile) et un echec retombe
        # sur les definitions telles quelles. L'avertissement, repete pour
        # chaque projet, noierait le journal.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return defs.map_asset_specs(func=ranger)
    except Exception:
        # Le rangement est un confort : un projet dont les assets ne s'y
        # pretent pas se charge quand meme, tel quel.
        print(f"[workspace] projet '{nom}' : assets laisses sans etiquette.", file=sys.stderr)
        traceback.print_exc()
        return defs


chargees = []
for nom, chemin in _dossiers_de_projet():
    try:
        chargees.append(_marquer(nom, _charger(nom, chemin)))
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

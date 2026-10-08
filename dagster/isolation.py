"""Les projets Dagster qui ont leurs propres dependances, chacun dans son
environnement.

POURQUOI. Tous les projets se chargent dans le meme processus Python, celui
de /workspace/definitions.py. Deux projets qui veulent deux versions de
pandas ne peuvent pas y cohabiter : le premier importe gagne, et l'autre
tourne sur une version qu'il n'a pas demandee -- ou ne se charge plus du tout.

LA REGLE, DU COTE DU PROJET. Un fichier « requirements-dagster.txt » a cote de
son definitions.py, et c'est tout. Ce script, lance par lancer.sh avant
Dagster :

  1. construit (ou reconstruit, si le fichier a change) un environnement
     virtuel dans <projet>/.dagster-venv, avec les paquets demandes ;
  2. ecrit un workspace.yaml qui declare ce projet comme un emplacement de
     code a part, execute par le Python de cet environnement ;
  3. y garde l'emplacement commun, « definitions », pour tous les autres.

L'agregateur (/workspace/definitions.py) ignore les projets qui portent ce
fichier : ils ne sont pas charges deux fois.

L'environnement voit les paquets de l'image (voir relier_aux_paquets_de_l_image) :
dagster y est donc a la meme version que l'interface, sans etre reinstalle.
Ce que le projet demande passe devant.

CE QUE CA COUTE. Chaque emplacement est un processus Python de plus, pour
l'interface ET pour le daemon : compter 200 a 300 Mo par projet isole.
D'ou l'opt-in : un projet sans ce fichier reste dans le processus commun, ou
il ne coute presque rien. Au-dela d'un projet isole, remonter le mem_limit de
codelab-dagster.

SANS PROJET ISOLE, RIEN NE CHANGE : aucun workspace.yaml, lancer.sh garde
« -m definitions », exactement comme avant.

Sortie : le chemin du workspace.yaml quand il en faut un, rien sinon. Tout
le reste va sur stderr, donc dans « docker logs codelab-dagster ».
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

FICHIER = "requirements-dagster.txt"
VENV = ".dagster-venv"
EMPREINTE = ".codelab-empreinte"
# Une installation qui pend ne doit pas bloquer Dagster indefiniment.
DELAI_PIP = int(os.environ.get("CODELAB_ISOLATION_DELAI", "900"))


def dire(message):
    print(f"[codelab-dagster] {message}", file=sys.stderr, flush=True)


def projets_isoles(workspace):
    """Les projets qui demandent leur propre environnement, par ordre de nom.

    Memes regles de decouverte que l'agregateur : ni « . » ni « _ » en tete,
    un definitions.py dedans.
    """
    try:
        noms = sorted(os.listdir(workspace))
    except OSError:
        return []
    return [nom for nom in noms
            if not nom.startswith((".", "_"))
            and os.path.isfile(os.path.join(workspace, nom, "definitions.py"))
            and os.path.isfile(os.path.join(workspace, nom, FICHIER))]


def empreinte(dossier):
    """Ce qui oblige a reconstruire : la liste des paquets, et le Python
    (une nouvelle image peut en changer -- l'environnement serait casse)."""
    with open(os.path.join(dossier, FICHIER), "rb") as f:
        contenu = f.read()
    return hashlib.sha256(contenu + sys.version.encode()).hexdigest()


def python_du_venv(dossier):
    return os.path.join(dossier, VENV, "bin", "python")


def relier_aux_paquets_de_l_image(venv):
    """Rend visibles, APRES ceux du projet, les paquets de l'interpreteur qui
    lance ce script -- dagster en tete.

    Un fichier .pth plutot que « --system-site-packages » : celui-ci ne
    designe que les paquets du Python de base, et rate ceux d'un Python qui
    serait lui-meme dans un environnement. Le .pth nomme exactement les
    dossiers ou dagster a ete trouve. Ajoutes en fin de chemin d'import, ils
    passent APRES les paquets du projet : une version demandee l'emporte.
    """
    import glob
    import site
    paquets = [p for p in site.getsitepackages() if os.path.isdir(p)]
    for cible in glob.glob(os.path.join(venv, "lib", "python*", "site-packages")):
        with open(os.path.join(cible, "_codelab_image.pth"), "w") as f:
            f.write("\n".join(paquets) + "\n")


def preparer(dossier, executer=subprocess.run):
    """Construit l'environnement du projet s'il n'est pas a jour.
    Renvoie True s'il est utilisable."""
    venv = os.path.join(dossier, VENV)
    note = os.path.join(venv, EMPREINTE)
    attendue = empreinte(dossier)
    try:
        with open(note) as f:
            if f.read().strip() == attendue and os.path.exists(python_du_venv(dossier)):
                return True
    except OSError:
        pass

    nom = os.path.basename(dossier)
    dire(f"projet '{nom}' : construction de son environnement ({FICHIER})...")
    shutil.rmtree(venv, ignore_errors=True)
    # stdout=stderr : la sortie standard de ce script est lue par lancer.sh
    # comme le chemin du workspace.yaml. Les lignes de pip n'ont rien a y
    # faire ; elles vont au journal.
    try:
        executer([sys.executable, "-m", "venv", venv],
                 check=True, timeout=120, stdout=sys.stderr)
        relier_aux_paquets_de_l_image(venv)
        executer([python_du_venv(dossier), "-m", "pip", "install", "--no-cache-dir",
                  "--disable-pip-version-check", "-r", os.path.join(dossier, FICHIER)],
                 check=True, timeout=DELAI_PIP, stdout=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        # L'environnement a moitie construit est retire : sans lui,
        # l'emplacement s'affiche en erreur dans Dagster, ce qui se voit.
        # Un environnement incomplet, lui, chargerait peut-etre -- avec des
        # versions que personne n'a demandees.
        shutil.rmtree(venv, ignore_errors=True)
        dire(f"projet '{nom}' : environnement IMPOSSIBLE a construire ({e}). "
             f"Il apparaitra en erreur dans Dagster ; corrige {FICHIER} puis "
             f"« docker restart codelab-dagster ».")
        return False
    with open(note, "w") as f:
        f.write(attendue + "\n")
    dire(f"projet '{nom}' : environnement pret.")
    return True


def workspace_yaml(workspace, isoles, module="definitions"):
    """Le contenu du workspace.yaml. JSON est du YAML valide : pas besoin de
    pyyaml, et aucun nom de dossier ne peut casser la syntaxe."""
    # L'emplacement commun, declare comme le faisait « -m definitions » :
    # meme nom (« definitions »), donc memes plannings et meme historique.
    emplacements = [{"python_module": module}]
    for nom in isoles:
        dossier = os.path.join(workspace, nom)
        emplacements.append({"python_file": {
            "relative_path": os.path.join(dossier, "definitions.py"),
            "working_directory": dossier,
            "executable_path": python_du_venv(dossier),
            "location_name": nom,
        }})
    return json.dumps({"load_from": emplacements}, indent=2) + "\n"


def main():
    workspace = os.environ.get("CODELAB_WORKSPACE", "/workspace")
    cible = os.path.join(os.environ.get("DAGSTER_HOME", "/opt/dagster/home"), "workspace.yaml")
    module = os.environ.get("CODELAB_DAGSTER_MODULE", "definitions")

    isoles = projets_isoles(workspace)
    if not isoles:
        # Plus de projet isole : le fichier d'une session precedente ne doit
        # pas survivre et continuer a declarer des emplacements disparus.
        try:
            os.remove(cible)
        except OSError:
            pass
        return 0

    for nom in isoles:
        preparer(os.path.join(workspace, nom))
    temporaire = cible + ".tmp"
    with open(temporaire, "w") as f:
        f.write(workspace_yaml(workspace, isoles, module))
    os.replace(temporaire, cible)
    dire(f"{len(isoles)} projet(s) dans leur propre environnement : {', '.join(isoles)}.")
    print(cible)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
CodeLab -- TESTS : LES AUTRES SERVICES
---------------------------------------------------------------------------
ROLE        les tests des autres services : poste de developpement et outil codelab, Dagster et son proxy, Postgres, Redis, Mailpit, le diagnostic et ses sondes.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes, modules et fixtures viennent de
            conftest.py (tests du panneau : test_panneau.py). Un test nouveau va dans la partie de
            son sujet. Aucun test ne lit /var/lib/codelab ni /workspace
            reels.
STRUCTURE   les services de la stack, le diagnostic
"""

from conftest import *  # noqa: F401,F403 -- aides, constantes, modules



# ===========================================================================
# LES SERVICES DE LA STACK
# Dagster, Postgres et pgvector, VS Code, Codex, l'outil codelab et son
# squelette.
# ===========================================================================

# --------------- codelab create / update : deux gestes, rien d'efface ---------------
#
# Vecu : « mon agent Claude ne fonctionne pas de maniere continue sur
# l'app ». Les projets crees avant une mise a jour gardaient un vieux manuel,
# parfois sans CLAUDE.md. create pose tout, update remet un projet existant
# au niveau de la stack -- sans jamais effacer ce que le projet a ecrit.

_SANS_GARDIEN = {"CODELAB_BASES_DEMANDES": "/nulle-part/codelab-bases"}


def _git(dossier, *args):
    import subprocess
    subprocess.run(["git", "-C", str(dossier), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def test_create_sans_gardien_des_bases_cree_quand_meme_le_projet(tmp_path):
    """La base est creee par defaut, mais un Postgres injoignable ne fait pas
    echouer la creation : le projet est la, et update rattrapera la base."""
    modeles = os.path.join(_racine_depot(), "dev", "modeles")
    r, espace = _lancer_codelab(tmp_path, ["create", "carnet"],
                                dict(_SANS_GARDIEN, CODELAB_MODELES=modeles))
    assert r.returncode == 0, r.stdout + r.stderr
    projet = espace / "carnet"
    for nom in ("AGENTS.md", "CLAUDE.md", "README.md", ".gitignore", "codelab_auth.py",
                "codelab-web.js"):
        assert (projet / nom).exists(), nom
    assert "relance « codelab update »" in r.stdout
    # Deux fois de suite : refuse, et dit quoi faire a la place.
    r, _ = _lancer_codelab(tmp_path, ["create", "carnet"], _SANS_GARDIEN)
    assert r.returncode == 1 and "codelab update carnet" in r.stderr


def test_codelab_update_met_a_jour_sans_rien_effacer(tmp_path):
    """Un vieux projet : son AGENTS.md sans bloc, son CLAUDE.md, un module
    modifie a la main, un .gitignore sans .env. Tout ce qu'il a ecrit reste ;
    ce qui manque arrive."""
    modeles = os.path.join(_racine_depot(), "dev", "modeles")
    projet = tmp_path / "ws" / "vieux"
    projet.mkdir(parents=True)
    (projet / "AGENTS.md").write_text("mes regles\n")
    (projet / "CLAUDE.md").write_text("mes consignes\n")
    (projet / ".gitignore").write_text("node_modules/")  # sans fin de ligne
    (projet / "codelab_web.py").write_text("# ma version\n")
    r, _ = _lancer_codelab(tmp_path, ["update", "vieux", "--sans-db"],
                           {"CODELAB_MODELES": modeles})
    assert r.returncode == 0, r.stdout + r.stderr
    agents = (projet / "AGENTS.md").read_text()
    assert agents.startswith("<!-- codelab:debut") and "# Manuel CodeLab" in agents
    assert agents.rstrip().endswith("mes regles")
    assert (projet / "CLAUDE.md").read_text() == "mes consignes\n"
    assert (projet / "codelab_web.py").read_text() == "# ma version\n"
    assert (projet / "codelab_auth.py").read_text() == open(os.path.join(modeles, "codelab_auth.py")).read()
    assert (projet / ".gitignore").read_text().splitlines() == ["node_modules/", ".env", ".dagster-venv/"]
    assert (projet / ".git").exists()
    # Relancee : rien de plus, et pas de fichier temporaire laisse derriere.
    r, _ = _lancer_codelab(tmp_path, ["update", "vieux", "--sans-db"], {"CODELAB_MODELES": modeles})
    assert r.returncode == 0 and ".gitignore" not in r.stdout
    assert agents == (projet / "AGENTS.md").read_text()
    assert not list(projet.glob("*.tmp*"))


def test_codelab_update_remplace_un_module_intact_dans_git(tmp_path):
    """Un module tel que CodeLab l'a pose, versionne et intact : update le
    remplace par la version du jour -- l'ancienne reste dans git. Modifie
    sans etre versionne, il n'est jamais touche."""
    import shutil
    modeles = tmp_path / "modeles"
    shutil.copytree(os.path.join(_racine_depot(), "dev", "modeles"), modeles)
    r, espace = _lancer_codelab(tmp_path, ["create", "atelier", "--sans-db"],
                                {"CODELAB_MODELES": str(modeles)})
    assert r.returncode == 0, r.stdout + r.stderr
    projet = espace / "atelier"
    _git(projet, "add", "-A")
    _git(projet, "commit", "-qm", "depart")
    (modeles / "codelab_auth.py").write_text("# version du jour\n")
    (modeles / "codelab_web.py").write_text("# version du jour\n")
    (projet / "codelab_web.py").write_text("# retouche locale\n")
    r, _ = _lancer_codelab(tmp_path, ["update", "atelier", "--sans-db"],
                           {"CODELAB_MODELES": str(modeles)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (projet / "codelab_auth.py").read_text() == "# version du jour\n"
    assert (projet / "codelab_web.py").read_text() == "# retouche locale\n"
    assert "laisse tel quel" in r.stdout


def test_codelab_update_tous_ne_touche_que_les_projets_codelab(tmp_path):
    """/workspace contient aussi des clones et des essais : --tous ne visite
    que les dossiers qui portent deja le manuel."""
    for nom in ("un", "deux"):
        r, espace = _lancer_codelab(tmp_path, ["create", nom, "--sans-db"])
        assert r.returncode == 0, r.stdout + r.stderr
    (espace / "clone").mkdir()
    (espace / "clone" / "AGENTS.md").write_text("pas CodeLab\n")
    r, _ = _lancer_codelab(tmp_path, ["update", "--tous", "--sans-db"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 projet(s) mis a jour" in r.stdout
    assert (espace / "clone" / "AGENTS.md").read_text() == "pas CodeLab\n"
    assert not (espace / "clone" / "CLAUDE.md").exists()
    assert not list(espace.glob("*/*.tmp*"))


def test_codelab_update_refuse_un_projet_absent_et_sans_dossier_courant(tmp_path):
    r, _ = _lancer_codelab(tmp_path, ["update", "fantome", "--sans-db"])
    assert r.returncode == 1 and "codelab create fantome" in r.stderr
    r, _ = _lancer_codelab(tmp_path, ["update"])
    assert r.returncode == 2 and "dossier d'un projet" in r.stderr


def _commandes_de_l_outil():
    """Les commandes du dispatch de dev/codelab, anciens noms et options d'aide exclus."""
    import re
    source = open(os.path.join(_racine_depot(), "dev", "codelab"), encoding="utf-8").read()
    dispatch = source.split("# ------------------------------ dispatch")[1]
    noms = set()
    for etiquette in re.findall(r"^  ([a-z|\-\"]+)\)", dispatch, re.M):
        noms.update(n for n in etiquette.split("|") if re.fullmatch(r"[a-z][a-z-]*", n))
    return noms


def test_l_aide_liste_chaque_commande_et_le_hub_aussi(tmp_path):
    """Une commande ajoutee a l'outil doit apparaitre dans « codelab help »,
    dans la fenetre Commandes du hub et dans FONCTIONNALITES.md : sinon
    personne ne la trouve. Vecu : « retire codelab aide, ne garde que codelab
    help », puis « garde que la version anglaise de codelab help »."""
    commandes = _commandes_de_l_outil()
    assert {"create", "update", "video", "help", "db", "vscode-bases"} <= commandes
    assert "aide" not in commandes
    aide, _ = _lancer_codelab(tmp_path, ["help"])
    seul, _ = _lancer_codelab(tmp_path, [])
    assert aide.returncode == seul.returncode == 0
    assert seul.stdout == aide.stdout
    assert "creates a complete project" in aide.stdout
    assert "cree un projet complet" not in aide.stdout, "l'aide est en anglais seulement"
    r, _ = _lancer_codelab(tmp_path, ["aide"])
    assert r.returncode == 2, "codelab aide est retiree"
    page = open(os.path.join(_racine_depot(), "app-manager", "app", "dashboard.html"),
                encoding="utf-8").read()
    fenetre = page.split('id="param-aide"')[1].split('<!-- ====')[0]
    assert "codelab aide" not in fenetre
    fonctionnalites = open(os.path.join(_racine_depot(), "FONCTIONNALITES.md"), encoding="utf-8").read()
    for c in commandes:
        assert f"codelab {c}" in aide.stdout, ("help", c)
        assert f"codelab {c}" in fenetre, ("hub", c)
        assert f"`codelab {c}" in fonctionnalites, ("FONCTIONNALITES.md", c)
    # Le detail d'une commande.
    r, _ = _lancer_codelab(tmp_path, ["help", "update"])
    assert "deleting nothing" in r.stdout and "sans rien effacer" not in r.stdout


def test_l_aide_est_un_onglet_des_parametres_reserve_a_l_administrateur():
    """Vecu : « le helper doit etre disponible dans les reglages, un onglet
    dedie pour l'admin seulement ». L'onglet porte data-admin (cache aux
    comptes utilisateurs, comme E-mail et alertes) et paramOnglet le refuse
    a un compte utilisateur ; la page Applications n'a plus sa fenetre."""
    page = _page_panneau("dashboard.html")
    onglets = page.split('id="param-onglets"')[1].split("</div>")[0]
    assert ("""<button data-p="aide" data-agir="paramOnglet" data-args='["aide"]' """
            """data-admin="1">Aide</button>""") in onglets
    assert 'id="param-aide"' in page
    utilisateur = re.search(r"const ONGLETS_UTILISATEUR=\[([^\]]*)\]", page).group(1)
    assert "aide" not in utilisateur
    outils = page.split('<div id="sec-apps"')[1].split('id="ap-outils"')[0]
    assert "Commandes" not in outils and 'id="ov-commandes"' not in page
    # Depuis « Nouvelle application », le lien mene a l'onglet.
    assert 'data-agir="aideDepuisAjout"' in page.split('id="ov-add"')[1][:1500]
    assert "aideDepuisAjout(){ hide('ov-add'); ouvrirAide(); }" in page
    assert "function ouvrirAide(){ showSection('parametres'); paramOnglet('aide'); }" in page


# ---------- le squelette se met a jour sans ecraser le travail ----------
#
# Defaut vecu, et le plus vicieux rencontre jusqu'ici parce qu'il ne produit
# AUCUN message : le squelette /workspace n'est copie qu'au premier
# demarrage, et jamais remplace ensuite. Une correction livree dans l'image
# ne pouvait donc atteindre aucune installation existante. Le projet
# "diagnostic" corrige etait dans l'image, le disque gardait la version
# cassee, et "docker compose pull" n'y changeait rien.
#
# L'entrypoint note desormais l'empreinte de chaque fichier qu'il depose,
# dans .codelab/empreintes. Un fichier dont l'empreinte n'a pas bouge n'a ete
# touche par personne, et lui seul est remplace. Les tests ci-dessous font
# tourner LE VRAI BLOC, extrait du vrai entrypoint : une reecriture du shell
# dans le test ne prouverait que la justesse du test.




def test_un_projet_par_defaut_absent_est_recopie_mais_pas_un_autre(tmp_path):
    """Vecu : « Messages » est devenu un projet a lui. Une installation deja
    amorcee ne l'aurait jamais recu : le squelette ne cree aucun fichier
    absent, pour qu'un projet supprime expres ne revienne pas. Un projet par
    defaut ne se supprime pas : absent, il est recopie entier, et ses
    empreintes notees ; un projet d'exemple ordinaire, lui, reste absent."""
    a = _Atelier(tmp_path)
    for nom in ("messages", "exemple"):
        (a.base / "seed" / nom).mkdir()
        (a.base / "seed" / nom / "app.py").write_text(nom, encoding="utf-8")
    journal = a.demarrer()
    assert (a.base / "ws" / "messages" / "app.py").read_text(encoding="utf-8") == "messages"
    assert not (a.base / "ws" / "exemple").exists()
    assert "projet par defaut messages copie" in journal
    assert " messages/app.py" in a.empreintes.read_text(encoding="utf-8")
    # Present, il n'est plus recopie : un fichier modifie reste a l'utilisateur.
    (a.base / "ws" / "messages" / "app.py").write_text("MON CODE", encoding="utf-8")
    (a.base / "seed" / "messages" / "app.py").write_text("nouveau", encoding="utf-8")
    a.demarrer()
    assert (a.base / "ws" / "messages" / "app.py").read_text(encoding="utf-8") == "MON CODE"




def test_une_ancienne_version_livree_est_remplacee(tmp_path):
    """Le cas qui a motive tout ceci : une version livree, restee intacte
    depuis, recoit la correction de l'image."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("ancienne", encoding="utf-8")
    a.noter("ancienne")
    journal = a.demarrer()
    assert a.sur_disque() == "corrigee", "la correction de l'image n'a pas atteint le disque"
    assert "mis a jour" in journal




def test_sans_empreinte_notee_un_fichier_est_laisse_tel_quel(tmp_path):
    """Depuis le retrait de la liste figee des anciennes versions : un
    fichier dont rien n'est note est tenu pour modifie. Le doute protege le
    travail de l'utilisateur ; une installation mise a jour depuis a note
    ses propres empreintes, et n'est pas concernee."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("ancienne", encoding="utf-8")
    journal = a.demarrer()
    assert a.sur_disque() == "ancienne"
    assert "modifies sur place" in journal




def test_un_fichier_modifie_par_l_utilisateur_n_est_jamais_ecrase(tmp_path):
    """La regle qui protege le travail : une empreinte qui n'est ni la notre
    ni l'une des anciennes, c'est du travail humain."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("MON CODE", encoding="utf-8")
    a.noter("ancienne")
    journal = a.demarrer()
    assert a.sur_disque() == "MON CODE", "le travail de l'utilisateur a ete ecrase"
    assert "modifies sur place" in journal




def test_un_fichier_supprime_expres_ne_ressuscite_pas(tmp_path):
    """Supprimer le projet d'exemple doit tenir. Le voir revenir a chaque
    redemarrage serait insupportable, et c'est la raison d'etre du marqueur."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.noter("ancienne")
    a.demarrer()
    assert a.sur_disque() is None, "un fichier supprime a ete recree"




def test_un_fichier_retire_du_squelette_part_s_il_est_intact(tmp_path):
    """Vecu : les taches VS Code (.vscode/tasks.json) sont parties de
    l'image mais restaient dans /workspace, et appelaient d'anciennes
    commandes. Note par CodeLab, absent de l'image et intact : il part, son
    dossier vide aussi. Modifie : il reste, il est a l'utilisateur."""
    a = _Atelier(tmp_path)
    a.livre.write_text("v1", encoding="utf-8")
    a.cible.write_text("v1", encoding="utf-8")
    ws = tmp_path / "ws"
    (ws / ".vscode").mkdir()
    (ws / ".vscode" / "tasks.json").write_text("taches", encoding="utf-8")
    (ws / "ancien").mkdir()
    (ws / "ancien" / "a-moi.txt").write_text("retouche", encoding="utf-8")
    a.empreintes.write_text(
        _somme_texte("v1") + " diagnostic/checks.py\n"
        + _somme_texte("taches") + " .vscode/tasks.json\n"
        + _somme_texte("original") + " ancien/a-moi.txt\n"
        + _somme_texte("parti") + " ancien/deja-parti.txt\n", encoding="utf-8")
    journal = a.demarrer()
    assert not (ws / ".vscode").exists(), journal
    assert ".vscode/tasks.json retire" in journal
    assert (ws / "ancien" / "a-moi.txt").read_text(encoding="utf-8") == "retouche"
    assert a.sur_disque() == "v1"
    notes = a.empreintes.read_text(encoding="utf-8")
    assert ".vscode/tasks.json" not in notes and "deja-parti" not in notes
    assert "diagnostic/checks.py" in notes




def test_rien_ne_bouge_avant_le_premier_amorcage(tmp_path):
    """Sans marqueur, l'amorcage classique n'a pas encore eu lieu : cette
    passe n'a rien a faire et ne doit surtout pas prendre les devants."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("ancienne", encoding="utf-8")
    a.noter("ancienne")
    a.marqueur.unlink()
    a.demarrer()
    assert a.sur_disque() == "ancienne"




def test_le_mecanisme_s_entretient_seul_sans_liste_figee(tmp_path):
    """LE point de cette refonte.

    Une fois qu'un fichier est passe par ici, son empreinte est notee, et la
    livraison SUIVANTE n'a plus besoin d'aucun catalogue. C'est ce qui permet
    de supprimer le generateur d'empreintes et la corvee de le relancer avant
    chaque commit.
    """
    a = _Atelier(tmp_path)
    a.livre.write_text("v1", encoding="utf-8")
    a.cible.write_text("v1", encoding="utf-8")
    a.demarrer()                                   # premier demarrage : on note
    assert a.note() == _somme_texte("v1"), "l'empreinte n'a pas ete notee"

    a.livre.write_text("v2", encoding="utf-8")     # nouvelle image
    a.demarrer()
    assert a.sur_disque() == "v2", (
        "sans liste figee, la mise a jour n'a pas eu lieu : le mecanisme ne "
        "s'entretient pas tout seul")
    assert a.note() == _somme_texte("v2"), "l'empreinte n'a pas suivi la mise a jour"




def test_un_fichier_approprie_le_reste_aux_livraisons_suivantes(tmp_path):
    """Une fois que l'utilisateur a pris un fichier a son compte, il le garde
    -- y compris apres une nouvelle version de l'image. Le cas contraire
    serait une perte de donnees differee, donc encore plus difficile a
    relier a sa cause."""
    a = _Atelier(tmp_path)
    a.livre.write_text("v1", encoding="utf-8")
    a.cible.write_text("MON CODE", encoding="utf-8")
    a.demarrer()
    assert a.note() is None, "le fichier de l'utilisateur a ete note comme etant le notre"

    a.livre.write_text("v2", encoding="utf-8")
    a.demarrer()
    assert a.sur_disque() == "MON CODE"




def test_une_installation_existante_entre_dans_le_mecanisme_sans_rien_ecraser(tmp_path):
    """Un fichier deja identique a l'image n'a rien a recevoir, mais doit
    quand meme etre note -- sinon il resterait dependant de la liste figee
    pour toujours, et la liste, elle, ne grandit plus."""
    a = _Atelier(tmp_path)
    a.livre.write_text("v1", encoding="utf-8")
    a.cible.write_text("v1", encoding="utf-8")
    journal = a.demarrer()
    assert a.note() == _somme_texte("v1")
    assert "mis a jour" not in journal, "un fichier deja a jour a ete reecrit"




def test_un_lien_a_la_place_du_fichier_de_notes_se_repare(tmp_path):
    """Le fichier de notes vit dans le workspace, inscriptible par le groupe :
    n'importe quelle application peut y poser un lien.

    Deux choses doivent tenir, et la seconde a ete trouvee en mutant. Un :
    ne jamais ecrire a travers le lien. Deux : ne pas se contenter de
    REFUSER -- un refus laisserait le lien en place et arreterait la prise de
    notes pour toujours, si bien qu'un "ln -s" suffirait a desactiver le
    mecanisme. Le lien doit donc etre remplace par un vrai fichier."""
    a = _Atelier(tmp_path)
    victime = tmp_path / "victime"
    victime.write_text("intact", encoding="utf-8")
    a.livre.write_text("v1", encoding="utf-8")
    a.cible.write_text("v1", encoding="utf-8")
    os.symlink(str(victime), str(a.empreintes))
    a.demarrer()
    assert victime.read_text(encoding="utf-8") == "intact", (
        "le lien a ete suivi : ecriture root hors du workspace")
    assert not os.path.islink(str(a.empreintes)), (
        "le lien est reste : la prise de notes est desactivee pour toujours")
    assert a.note() == _somme_texte("v1"), "les notes n'ont pas repris"




def test_un_lien_symbolique_a_la_place_du_fichier_est_refuse(tmp_path):
    """Ce bloc tourne en root et /workspace est inscriptible par les
    applications. Un lien pose a la place d'un fichier du squelette ne doit
    jamais etre suivi : ce serait une ecriture root arbitraire offerte a
    n'importe quelle application du panneau."""
    a = _Atelier(tmp_path)
    victime = tmp_path / "victime"
    victime.write_text("intact", encoding="utf-8")
    a.livre.write_text("corrigee", encoding="utf-8")
    os.symlink(str(victime), str(a.cible))
    a.noter("intact")
    a.demarrer()
    assert victime.read_text(encoding="utf-8") == "intact", (
        "le lien a ete suivi : ecriture hors du workspace, en root")
    assert os.path.islink(str(a.cible)), "le lien a ete remplace"




def test_un_temporaire_pose_d_avance_ne_detourne_pas_l_ecriture(tmp_path):
    """Regression : tant que le fichier temporaire portait un nom
    previsible, une application pouvait poser d'avance un lien a ce nom et
    faire ecrire root dans la cible de son choix. Le nom est desormais tire
    par mktemp, qui cree le fichier sans jamais suivre un lien existant."""
    a = _Atelier(tmp_path)
    victime = tmp_path / "victime"
    victime.write_text("intact", encoding="utf-8")
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("ancienne", encoding="utf-8")
    a.noter("ancienne")
    # Le piege, au nom qu'utilisait l'ancienne version du code.
    os.symlink(str(victime), str(a.ws / "checks.py.codelab-tmp"))
    a.demarrer()
    assert victime.read_text(encoding="utf-8") == "intact", (
        "le temporaire previsible a detourne l'ecriture, en root")
    # La mise a jour legitime doit quand meme avoir eu lieu.
    assert a.sur_disque() == "corrigee"


# ---------------- Dagster : ce qui l'empechait de rester debout ----------
#
# Vecu : « Dagster me met comme erreur 502 Bad Gateway ». Le proxy allait
# bien ; c'est codelab-dagster qui ne repondait plus. Deux causes tenaient
# ensemble, et les deux se verrouillent ici.




def test_les_runs_planifies_passent_par_une_file_d_attente():
    """Depuis qu'un planning declenche le diagnostic chaque heure, les
    runs arrivent tout seuls. Sans coordinateur, Dagster les
    demarre TOUS a la fois, chacun dans son processus -- et sur une machine
    de la taille d'une ZimaBlade, quelques runs qui se chevauchent suffisent
    a emporter le webserver. Ce qu'on voit alors est un 502 qui ne dit rien.
    """
    chemin = _fichier_du_depot("dagster", "dagster.yaml")
    if not chemin:
        pytest.skip("depot absent de cette image")
    texte = open(chemin, encoding="utf-8").read()
    assert "QueuedRunCoordinator" in texte
    assert "max_concurrent_runs: 1" in texte
    # Un run dont le processus a disparu occupait la place de la file pour
    # toujours, et plus rien ne partait.
    assert "run_monitoring:" in texte and "enabled: true" in texte




def test_dagster_yaml_se_met_a_jour_sur_les_installations_existantes():
    """Le fichier n'etait ecrit qu'au premier demarrage : une correction
    livree dans l'image n'atteignait aucune machine deja installee -- la
    file d'attente ci-dessus ne serait arrivee nulle part."""
    chemin = _fichier_du_depot("dagster", "entrypoint.sh")
    if not chemin:
        pytest.skip("depot absent de cette image")
    texte = open(chemin, encoding="utf-8").read()
    bloc = texte.split("--------------------------- dagster.yaml")[1]
    # Remplace sur preuve de contenu, jamais sur une date, et jamais un
    # fichier que l'utilisateur a modifie.
    assert "sha256sum" in bloc
    assert "DAGSTER_YAML_NOTE" in bloc
    assert "laisse tel quel" in bloc
    # Renommage atomique : un arret au mauvais moment ne laisse pas une
    # configuration a moitie ecrite.
    assert 'mv "$tmp" "$DAGSTER_YAML"' in bloc




def test_le_proxy_explique_l_absence_de_dagster_au_lieu_du_502_nu():
    """« 502 Bad Gateway » est exact et inutilisable : il ne dit ni qui ne
    repond pas -- le proxy va tres bien -- ni quoi faire."""
    conf = _fichier_du_depot("dagster", "proxy", "nginx.conf")
    page = _fichier_du_depot("dagster", "proxy", "indisponible.html")
    if not conf or not page:
        pytest.skip("depot absent de cette image")
    texte = open(conf, encoding="utf-8").read()
    assert "error_page 502 503 504 /_indisponible.html;" in texte
    # Servie en interne seulement : sinon l'adresse serait atteignable
    # directement et la page mise en cache par le navigateur.
    bloc = texte.split("location = /_indisponible.html {")[1].split("}")[0]
    assert "internal;" in bloc
    assert "no-store" in bloc
    # Et la page dit quoi taper, plutot que de plaindre l'utilisateur.
    assert "docker logs --tail 50 codelab-dagster" in open(page, encoding="utf-8").read()




def test_la_sonde_des_origines_prend_le_meme_defaut_que_le_panneau(monkeypatch):
    """La sonde reclamait APP_MANAGER_APPS_PORT et declarait l'installation
    en faute des que la variable manquait -- alors que le panneau se rabat
    sur 9002 et que le compose publie ce port. Elle annoncait donc des
    origines confondues sur une installation ou elles etaient separees.

    Une sonde ne dit que ce qu'elle constate : meme defaut que le panneau,
    puis verification sur le port.
    """
    monkeypatch.delenv("APP_MANAGER_APPS_PORT", raising=False)
    assert _port_applications() == PORT_APPS_DEFAUT == 9002
    monkeypatch.setenv("APP_MANAGER_APPS_PORT", "9500")
    assert _port_applications() == 9500

    _ok, _nom, detail = check_origine_applications()
    assert "APP_MANAGER_APPS_PORT absent" not in detail
    assert "9500" in detail




def test_le_port_des_applications_est_ecrit_dans_les_deux_composes():
    """Le panneau prend 9002 par defaut, mais une valeur devinee ne se relit
    pas : les deux composes doivent la poser noir sur blanc, et la meme."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    fichiers = [os.path.join(racine, n)
                for n in ("docker-compose.yml", "docker-compose-casaos.yml")]
    presents = [f for f in fichiers if os.path.exists(f)]
    if not presents:
        pytest.skip("composes absents de cette image")
    assert len(presents) == 2, "un des deux composes manque"
    for chemin in presents:
        texte = open(chemin, encoding="utf-8").read()
        assert f'APP_MANAGER_APPS_PORT: "{PORT_APPS_DEFAUT}"' in texte, chemin
        assert f'- "{PORT_APPS_DEFAUT}:{PORT_APPS_DEFAUT}"' in texte, chemin




def test_le_panneau_ne_repond_pas_sur_le_port_des_applications(deux_origines):
    """Le coeur de la separation : si le panneau repondait sur les deux
    ports, les deux origines se vaudraient et rien ne serait separe."""
    c = deux_origines
    for chemin in ("/", "/login", "/api/apps", "/api/utilisateurs"):
        r = c.get(chemin, **_sur_port(9302))
        assert r.status_code == 404, f"{chemin} repond sur le port des applications"




def test_les_applications_repondent_sur_leur_port(deux_origines, monkeypatch):
    c = deux_origines
    # L'application ne repond pas : on le DECIDE ici, au lieu de parier sur
    # le fait que rien n'ecoute sur le port 9103 de la machine qui lance les
    # tests. Ce pari se perdait des qu'un panneau local tournait a cote --
    # le proxy joignait la vraie application, rendait son 404 a elle, et le
    # test accusait la separation des origines.
    def ne_repond_pas(*a, **kw):
        raise OSError("connexion refusee")
    monkeypatch.setattr(app, "demander_a_l_application", ne_repond_pas)

    r = c.get("/public/", **_sur_port(9302))
    # 502 : la route du proxy est bien atteinte, et c'est la connexion a
    # l'application qui manque. Un 404 voudrait dire que la garde d'origine
    # a refuse la requete avant.
    assert r.status_code == 502, r.data[:200]
    # La sonde de sante reste joignable : c'est le HEALTHCHECK du conteneur.
    assert c.get("/health", **_sur_port(9302)).status_code == 200




def test_une_application_demandee_au_panneau_est_renvoyee_chez_elle(deux_origines):
    """Les favoris et les liens deja partages doivent continuer de marcher."""
    c = deux_origines
    r = c.get("/public/", **_sur_port(9301))
    assert r.status_code == 302
    assert r.headers["Location"] == "http://serveur:9302/public/"
    # Le sous-chemin et la requete suivent, sinon un lien profond casse.
    r = c.get("/public/a/b?x=1", **_sur_port(9301))
    assert r.headers["Location"] == "http://serveur:9302/public/a/b?x=1"




def test_sans_separation_rien_ne_change(deux_espaces, monkeypatch):
    """Le second port peut ne pas s'ouvrir -- non publie par le compose, deja
    pris. Dans ce cas les applications restent servies par le panneau : une
    stack qui protege moins vaut mieux qu'une stack morte."""
    monkeypatch.setitem(app._origines_separees, "actif", False)
    c = deux_espaces
    r = c.get("/public/")
    assert r.status_code != 302 or "9302" not in r.headers.get("Location", "")
    assert c.get("/login").status_code == 200


















# ---------- 34. Dagster dans le hub ----------
#
# Demande : « Ajoute Dagster comme une application a part entiere disponible
# sur le hub, qui redirige vers le port 3000. » Il manquait la ou on le
# cherche : le hub est la page d'accueil de CodeLab, et Dagster s'ouvrait en
# retapant une adresse et un numero de port de memoire.
#
# Ce n'est pas une application declaree pour autant -- personne ne le demarre
# d'ici, il n'a ni dossier ni commande. Il vit donc dans une liste a part,
# peinte dans la meme grille.




def test_dagster_parait_dans_le_hub_et_mene_au_port_3000(hub_dagster):
    hub_dagster.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    d = hub_dagster.get("/api/mes-apps").get_json()

    services = d["services"]
    # Trois services dans le hub : l'orchestrateur, le courrier, et les
    # messages recus.
    assert [s["name"] for s in services] == ["dagster", "mailpit"]
    dagster = services[0]
    # L'adresse est celle du NAVIGATEUR : meme hote, autre port.
    assert dagster["url"].endswith(":3000/"), dagster["url"]
    assert dagster["externe"] is True
    assert dagster["running"] is True
    # Et il ne se melange pas aux applications declarees : ni dans la liste
    # qu'on masque, ni dans le choix « de quelle application veux-tu parler ».
    assert [a["name"] for a in d["apps"]] == ["site"]




def test_dagster_ne_parait_pas_pour_un_compte_utilisateur(hub_dagster):
    """Son interface lance des jobs, donc execute du code sur la machine : le
    proxy le refuse deja a un compte utilisateur. Lui montrer une tuile qui
    le renverrait a la page de connexion serait promettre une porte qui ne
    s'ouvre pas."""
    with hub_dagster.session_transaction() as sess:
        sess["authed"] = True
        sess["role"] = app.ROLE_UTILISATEUR
        sess["utilisateur"] = "marie"
        sess["ouverte"] = int(time.time())
        sess["jeton"] = "j"
    d = hub_dagster.get("/api/mes-apps").get_json()
    # Pas de Dagster pour un compte utilisateur. L'interface de chat, elle,
    # reste : son portier l'ouvre a tout compte connecte.
    assert app.DAGSTER_NOM not in {s["name"] for s in d["services"]}




def test_la_tuile_de_dagster_revient_par_le_panneau():
    """La tuile passe par l'app-manager pour journaliser l'ouverture et garder
    un chemin de retour, sans perdre l'origine propre de Dagster.

    L'expression etait ecrite ICI, dans la tuile. Elle vit maintenant dans
    lienOuverture(), avec les quatre autres endroits qui ouvrent une
    application. Le test suit le deplacement et verifie les DEUX : que la
    tuile applique la regle, et ce que la regle dit. Ne verifier que la tuile
    laisserait vider lienOuverture() sans que rien ne rougisse.
    """
    page = _page_panneau("dashboard.html")
    bloc = page.split("function peindreBento(")[1].split("const tuile = e =>")[0]
    assert "lienOuverture(a)" in bloc
    assert 'target="_blank"' not in bloc
    assert "hubApps.concat(hubServices)" in page

    regle = page.split("const lienOuverture = a =>")[1].split(";")[0]
    # Un service externe par la porte du panneau, une application par
    # l'origine des applications.
    assert "a.externe" in regle
    assert "'/' + encodeURIComponent(a.name) + '/'" in regle
    assert "urlApplication(a.name)" in regle




def test_ouvrir_une_application_passe_partout_par_la_meme_adresse():
    """Cinq endroits ouvrent une application ; un seul doit dire ou.

    L'adresse etait recopiee dans la tuile du hub, dans les trois boutons
    « Ouvrir » de la fiche et dans sa ligne « Adresse ». Quatre disaient la
    meme chose et la cinquieme avait derive : la ligne « Adresse » d'un
    service externe pointait sur son adresse PROPRE (Dagster sur son port),
    court-circuitant la porte qui verifie le droit d'entrer et journalise
    l'ouverture. Un droit manquant n'y donnait plus le refus du panneau mais
    la page d'erreur nue du service.

    Ce test tient la regle de bout en bout : aucun de ces endroits ne
    reconstruit l'adresse dans son coin, et aucun n'ouvre un onglet.
    """
    page = _page_panneau("dashboard.html")

    # Aucune page du panneau n'ouvre plus d'onglet : depuis une application,
    # le ruban de retour pose par le proxy ramene au hub.
    assert 'target="_blank"' not in page

    # Les trois boutons « Ouvrir » de la fiche.
    boutons = re.findall(r'<a class="btn[^"]*" href="\$\{([^}]+)\}"[^>]*>\$\{svgOuvrir\}', page)
    assert len(boutons) == 3, boutons
    assert all(b.strip() == "lienOuverture(a)" for b in boutons), boutons

    # La ligne « Adresse » de la fiche : le TEXTE dit ou le service vit, le
    # LIEN passe par la porte du panneau. Les deux ne se confondent pas.
    assert "$('fa-url').href = lienOuverture(a);" in page
    assert "$('fa-url').textContent = externe ? a.url : '/'+a.name+'/';" in page



def test_les_applications_par_defaut_ont_leur_icone(hub_dagster, tmp_path):
    """« dagster », « diagnostic » et « demo » donneraient le meme « D ».

    Trois carres identiques dans la grille du hub, a distinguer en lisant le
    nom dessous -- c'est-a-dire en cessant de les reconnaitre d'un coup
    d'oeil.

    C'est un DEFAUT et non une reservation : un logo depose dans le projet
    reprend la main. L'ordre est celui-la et pas l'inverse, parce qu'un logo
    pose est un choix explicite, et l'icone livree n'est qu'un repli.
    """
    hub_dagster.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    # Messages porte son icone dans son projet (icon.svg), qu'elle sert aussi
    # a sa propre page : inscrite comme le panneau l'inscrit, elle la montre.
    apps = app.load()
    apps[app.MESSAGERIE_NOM] = {"path": DOSSIER_MESSAGES, "command": "x", "port": 9703}
    app.save(apps)
    for nom in app.APPS_PAR_DEFAUT:
        r = hub_dagster.get("/api/icon/" + nom)
        assert r.status_code == 200, nom
        assert r.mimetype == "image/svg+xml", nom
        assert b"<svg" in r.data and b"<text" not in r.data, (
            f"l'icone de {nom} est retombee sur la lettre par defaut")
    # Deux dessins distincts : une icone commune ne distinguerait rien.
    dessins = {hub_dagster.get("/api/icon/" + n).data for n in app.APPS_PAR_DEFAUT}
    assert len(dessins) == len(app.APPS_PAR_DEFAUT)

    # Un logo depose gagne sur l'icone livree.
    projet = tmp_path / "projet-dagster"
    projet.mkdir()
    (projet / "logo.svg").write_text("<svg><rect id='depose'/></svg>")
    apps = app.load()
    apps["dagster"] = {"path": str(projet), "command": "x", "port": 9702}
    app.save(apps)
    assert b"depose" in hub_dagster.get("/api/icon/dagster").data



# ---------- 25. CodeLab n'heberge pas d'interface de chat -----------------
#
# L'application maison « aichat » a ete retiree, et l'interface Open WebUI qui
# l'avait remplacee l'a ete a son tour : l'outil du developpeur est son
# editeur, et les applications appellent leurs services elles-memes. Deux
# choses doivent rester vraies : l'ancienne application disparait du registre
# sans perte de code, et le panneau cesse de travailler quand l'onglet n'est
# pas regarde.


def test_le_panneau_cesse_de_sonder_quand_l_onglet_n_est_plus_regarde():
    """Quatre minuteries tournaient dans un onglet d'arriere-plan.

    Rafraichissement des applications, messages, metriques, hub : le panneau
    interrogeait la machine toutes les cinq secondes meme sans personne
    devant. Sur une ZimaBlade, cela se voit.
    """
    page = _page_panneau("dashboard.html")
    assert "function surveiller(" in page
    assert "visibilitychange" in page
    assert "document.hidden" in page
    # Plus aucun setInterval pose directement : tous passent par surveiller().
    directs = [l for l in page.splitlines()
               if "setInterval(" in l and "t.fn" not in l and "t.id" not in l]
    assert directs == [], directs
    # Les quatre sondes, plus la definition : cinq occurrences au moins.
    assert page.count("surveiller(") >= 5, "une minuterie a echappe au garde"
    for sonde in ("refresh()", "guetterMessages", "chargerHub()"):
        assert sonde in page


# ---------- 26. Postgres : la sauvegarde et le role du service IA ---------




def test_la_liste_des_bases_a_sauvegarder_ne_passe_pas_par_la_base_supprimee():
    """La sauvegarde disait « 0 base(s) ecrite(s) » et personne ne le lisait.

    La liste des bases etait demandee a « postgres », la base de maintenance
    livree par initdb -- que drop_maintenance_db supprime justement a la fin
    du provisionnement. La requete echouait, la liste revenait vide, la
    boucle ne sauvegardait rien, et le script se terminait sans erreur.
    """
    script = _entrypoint_postgres()
    bloc = script.split("bases=$(psql")[1].split(")\n")[0]
    assert '-d "$CODELAB_INSTANCE_DB"' in bloc, (
        "la liste des bases doit etre demandee a une base qui existe encore")
    assert "-d postgres " not in bloc
    # Et drop_maintenance_db existe bien : c'est ce qui rend l'erreur
    # possible, donc ce qui rend ce test necessaire.
    assert "drop_maintenance_db()" in script


# ---------- 39. pgvector : la base vectorielle est la base ---------------
#
# Le besoin d'une base vectorielle se traite ici par une EXTENSION et non par
# un service de plus. Sur une machine de 4 Go, un conteneur supplementaire se
# paie ; une extension ne coute que sa taille sur disque. Et les vecteurs
# vivent a cote des donnees du projet : meme transaction, meme sauvegarde.




def test_l_image_postgres_embarque_pgvector():
    """L'extension est dans l'image, et sa version suit celle du serveur.

    Le paquet PGDG se nomme postgresql-<majeure>-<extension> : une montee de
    Postgres qui oublierait ce numero installerait l'extension d'une autre
    majeure, ou rien du tout.
    """
    source = _dockerfile_postgres()
    majeures = re.findall(r"^FROM postgres:(\d+)", source, re.M)
    assert majeures, "la version de Postgres ne se lit plus dans le Dockerfile"
    majeure = majeures[0]
    assert f"postgresql-{majeure}-pgvector" in source, (
        f"l'image est basee sur postgres:{majeure} mais n'installe pas "
        f"postgresql-{majeure}-pgvector")
    # Les listes apt ne restent pas dans l'image.
    assert "rm -rf /var/lib/apt/lists/*" in source




def test_chaque_base_de_projet_recoit_l_extension():
    """« Aucune configuration manuelle apres l'installation » vaut aussi ici.

    L'extension est creee a cote du schema du projet, dans la meme boucle :
    une base creee plus tard la recoit au demarrage suivant, sans que
    personne ait a y penser.
    """
    script = _entrypoint_postgres()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in script
    # Dans « public » et non dans le schema du projet : une extension posee
    # dans un schema supprimable disparait avec lui.
    assert "SCHEMA public" in script
    # Et l'echec se lit dans les journaux au demarrage, au lieu de se
    # decouvrir au premier CREATE TABLE.
    assert "pgvector indisponible" in script




def test_l_extension_est_creee_dans_la_meme_boucle_que_le_schema():
    """Sinon une base creee apres coup n'aurait que la moitie de sa mise en place."""
    script = _entrypoint_postgres()
    debut = script.find("for db in $CODELAB_PROJECT_DBS; do")
    assert debut != -1, "la boucle de provisionnement a change de forme"
    fin = script.find("\n  done", debut)
    boucle = script[debut:fin]
    assert "CREATE SCHEMA IF NOT EXISTS" in boucle
    assert "CREATE EXTENSION IF NOT EXISTS vector" in boucle, (
        "l'extension est creee hors de la boucle des bases de projet")


# ---------- 41. les cles et les secrets se posent depuis le panneau -------
#
# CE QUE CETTE SECTION PROTEGE, et pourquoi chaque test existe.
#
# La page remplace trois gestes manuels par un bouton. Elle touche donc aux
# seuls fichiers de la stack qui portent des secrets en clair, et elle les
# montre -- partiellement -- dans un navigateur. Les deux risques sont
# nommes et testes separement :
#
#   DIVULGATION    aucune valeur complete ne doit sortir du processus, ni
#                  par une reponse, ni par une URL, ni par le journal.
#   INCOHERENCE    le panneau ecrit ce que d'autres programmes lisent. Une
#                  variable mal nommee, une empreinte calculee autrement, un
#                  fichier que le compose ne monte plus : rien ne leve
#                  d'erreur, tout se tait, et la cle est simplement refusee.

# --------------------------- ce qui sort de la page ---------------------

# ------------------------ ce qui s'ecrit sur le disque ------------------

# ------------------------------ la cle des agents ------------------------

# ---------- 42. la tuile Dagster : son logo, et son rangement ------------
#
# Trois signalements, une meme tuile.
#
#   « le logo n'est pas net »        -> il etait telecharge, donc matriciel ;
#   « un fond clair, constamment »   -> la plaque suivait le theme ;
#   « pouvoir masquer Dagster »      -> c'etait la seule tuile qu'on ne
#                                       pouvait pas retirer de son hub.




def test_la_plaque_du_logo_est_claire_dans_les_trois_themes():
    """Demande explicitement : « un fond clair CONSTAMMENT ».

    Et ce n'est pas qu'une preference : le mark Dagster porte une forme
    presque noire (#1F1F1F). Sur la plaque grise-bleue d'avant (#556274) elle
    tombait a 2,66:1 et le dessin se refermait sur lui-meme.

    Le seuil est pose sur la LUMINANCE, pas sur la valeur : #556274 comme
    n'importe quel gris moyen echoue, et une future retouche claire passe
    sans qu'on ait a toucher ce test.
    """
    for valeur in _plaques_du_theme():
        assert _luminance(valeur) > 0.6, (
            f"--plaque-logo vaut {valeur} : ce n'est pas un fond clair "
            f"(luminance {_luminance(valeur):.2f})")




def test_la_partie_sombre_du_logo_reste_lisible_sur_la_plaque():
    """Le chiffre qui justifie le changement, verifie plutot qu'affirme."""
    def contraste(a, b):
        la, lb = _luminance(a), _luminance(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    for plaque in _plaques_du_theme():
        assert contraste("#1F1F1F", plaque) >= 10, (
            f"la forme sombre du logo tombe a "
            f"{contraste('#1F1F1F', plaque):.2f}:1 sur {plaque}")




def test_le_logo_dagster_servi_est_le_svg_livre_avec_l_image(hub):
    """LE test du signalement « choisis un logo plus net ».

    Le panneau allait chercher /favicon.ico sur l'instance Dagster et servait
    ce qu'il trouvait, EN PRIORITE sur le SVG livre. Un favicon fait 32 px ;
    la tuile du hub en fait 40 a 48. Le navigateur agrandissait donc une
    image matricielle.

    Le test voisin « le logo est vectoriel et net » lisait la CONSTANTE. Il
    passait donc pendant que la reponse servie, elle, etait matricielle.
    Celui-ci lit la REPONSE.
    """
    hub.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    r = hub.get("/api/icon/" + app.DAGSTER_NOM)
    assert r.status_code == 200
    assert r.mimetype == "image/svg+xml", (
        f"le logo de Dagster est servi en {r.mimetype} : une image "
        f"matricielle agrandie dans la tuile")
    corps = r.get_data(as_text=True)
    assert corps == app.ICONE_DAGSTER
    assert "<svg" in corps and "viewBox" in corps




def test_le_panneau_s_ajoute_a_l_ecran_d_accueil_d_un_iphone(client):
    """Safari > Partager > « Sur l'ecran d'accueil ».

    Sans manifeste ni balises apple-mobile-web-app-*, iOS posait une capture
    de la page en guise d'icone et l'ouvrait dans un onglet Safari ordinaire.
    Tout cela doit repondre SANS session : on ajoute souvent l'icone depuis
    la page de connexion.
    """
    import struct
    for page in ("dashboard.html", "login.html"):
        html = _page_panneau(page)
        for balise in ('rel="manifest" href="/manifest.webmanifest"',
                       'rel="apple-touch-icon" href="/apple-touch-icon.png',
                       'name="apple-mobile-web-app-capable" content="yes"',
                       'name="apple-mobile-web-app-title" content="CodeLab"'):
            assert balise in html, f"{page} : {balise} manque"

    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.mimetype == "application/manifest+json"
    m = json.loads(r.get_data(as_text=True))
    assert m["display"] == "standalone" and m["start_url"] == "/"
    assert m["short_name"] == "CodeLab"

    tailles = {"/apple-touch-icon.png": "180x180", **{i["src"]: i["sizes"] for i in m["icons"]}}
    for adresse, taille in tailles.items():
        r = client.get(adresse)
        assert r.status_code == 200 and r.mimetype == "image/png"
        png = r.get_data()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        largeur, hauteur, _, type_couleur = struct.unpack(">IIBB", png[16:26])
        assert f"{largeur}x{hauteur}" == taille
        # 2 = RVB sans canal alpha. iOS peint en NOIR la transparence d'une
        # apple-touch-icon : l'icone doit etre opaque.
        assert type_couleur == 2, "l'icone de l'ecran d'accueil a un canal alpha"




def test_le_panneau_ne_telecharge_plus_le_logo_de_dagster():
    """Ni appel reseau, ni cache sur disque, ni fichier de type a cote.

    Le cache vivait dans STATE_DIR : le laisser derriere aurait garde un
    fichier que plus rien n'ecrit et que le filet des tests devait encore
    detourner.
    """
    for parti in ("icone_dagster", "ICONE_DAGSTER_CACHE",
                  "ICONES_DAGSTER_CANDIDATES", "_lire_icone_dagster_du_disque"):
        assert not hasattr(app, parti), (
            f"{parti} est encore la : le logo peut encore etre telecharge")
    # « favicon » reste un nom legitime AILLEURS : find_icon() le cherche
    # parmi les logos qu'un projet depose dans son dossier. Ce qu'on
    # interdit, c'est de le chercher CHEZ DAGSTER -- donc les deux mots
    # ensemble, dans la meme ligne ou le meme appel.
    source = _page_panneau("app.py")
    # Les COMMENTAIRES sont hors sujet : celui qui explique le retrait cite
    # forcement les deux mots, et ce test le faisait tomber sur sa propre
    # justification. C'est du code qu'on parle.
    coupables = [ligne for ligne in source.split("\n")
                 if not ligne.strip().startswith("#")
                 and "favicon" in ligne.lower() and "dagster" in ligne.lower()]
    assert not coupables, (
        "app.py va encore chercher un favicon chez Dagster : " + str(coupables))
    assert "DAGSTER_PORT}/favicon" not in source
    assert "ICONE_DAGSTER" in source, "le SVG livre doit rester, lui"


# ------------------------- ranger Dagster ------------------------------




def test_dagster_se_range_hors_du_hub_comme_une_application(hub, monkeypatch):
    """Sa tuile etait la seule qu'on ne pouvait pas ecarter.

    Dagster n'est pas une application declaree : il ne figure pas dans
    load(), et la route de masquage le refusait donc en 404.
    """
    monkeypatch.setattr(app, "origine_dagster", lambda: "http://dagster:3000/")
    c = _admin_du_hub(hub)
    assert app.DAGSTER_NOM in [
        s["name"] for s in c.get("/api/mes-apps").get_json()["services"]]

    assert c.post("/api/mes-apps/" + app.DAGSTER_NOM + "/masquer").status_code == 200

    d = c.get("/api/mes-apps").get_json()
    assert app.DAGSTER_NOM not in [s["name"] for s in d["services"]], (
        "la tuile est restee dans le hub")
    assert [s["name"] for s in d["services_masques"]] == [app.DAGSTER_NOM], (
        "range, il disparait aussi des reglages : impossible de le remettre")




def test_ranger_dagster_ne_l_arrete_pas_et_ne_ferme_rien(hub, monkeypatch):
    """Le point a ne pas se tromper, et il vaut ici plus qu'ailleurs : c'est
    un SERVICE. Croire qu'on l'a eteint en le rangeant ferait prendre un
    rangement pour un arret."""
    monkeypatch.setattr(app, "origine_dagster", lambda: "http://dagster:3000/")
    c = _admin_du_hub(hub)
    c.post("/api/mes-apps/" + app.DAGSTER_NOM + "/masquer")
    # Le portier de Dagster continue d'ouvrir : le proxy ne consulte pas le
    # rangement, et c'est bien ainsi.
    assert c.get("/api/auth-check").status_code == 204




def test_remettre_dagster_le_ramene_dans_le_hub(hub, monkeypatch):
    monkeypatch.setattr(app, "origine_dagster", lambda: "http://dagster:3000/")
    c = _admin_du_hub(hub)
    c.post("/api/mes-apps/" + app.DAGSTER_NOM + "/masquer")
    assert c.delete("/api/mes-apps/" + app.DAGSTER_NOM + "/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert [s["name"] for s in d["services"]] == [app.DAGSTER_NOM, app.MAILPIT_NOM]
    assert d["services_masques"] == []




def test_un_compte_utilisateur_ne_peut_pas_ranger_dagster(hub):
    """Ouvrir ce nom a la route de masquage ne doit rien ouvrir d'autre.

    peut_voir() garde le dernier mot : pour une application par defaut il
    exige l'administrateur. Un compte nomme qui tenterait le coup
    apprendrait, sinon, que ce service existe.
    """
    c = _connexion_marie(hub)
    assert c.post("/api/mes-apps/" + app.DAGSTER_NOM + "/masquer").status_code == 404
    d = c.get("/api/mes-apps").get_json()
    assert d["services"] == [] and d["services_masques"] == []




def test_seuls_les_services_nommes_passent_par_la_route_de_masquage():
    """Une liste fermee, pas « tout ce qui n'est pas une application » :
    sinon la route accepterait n'importe quel nom et masquees.json se
    remplirait d'entrees qui ne designent rien."""
    assert app.SERVICES_MASQUABLES == frozenset({app.DAGSTER_NOM, app.MAILPIT_NOM})




def test_ranger_un_nom_inconnu_reste_un_refus(hub):
    c = _admin_du_hub(hub)
    assert c.post("/api/mes-apps/nexiste-pas/masquer").status_code == 404




# ---------- 43. le serveur VS Code survit aux mises a jour ----------


def test_le_serveur_vscode_survit_a_une_mise_a_jour_d_image():
    """Remote-SSH installe ses extensions DANS le conteneur. Sans montage,
    chaque « compose pull » les effacait toutes, sans rien dire."""
    for chemin in _composes():
        texte = open(chemin, encoding="utf-8").read()
        assert "/home/vscode/.vscode-server" in texte, (
            f"{os.path.basename(chemin)} ne persiste pas le serveur VS Code")




def test_le_dossier_du_serveur_vscode_est_rendu_a_son_utilisateur():
    """Docker cree un montage absent EN ROOT. Sans ce chown, la premiere
    connexion Remote-SSH echoue sur un refus d'ecriture, et le message cote
    VS Code ne nomme ni le dossier ni le proprietaire."""
    source = _entrypoint_dev()
    assert "VSCODE_SERVER_DIR=/home/vscode/.vscode-server" in source
    debut = source.index("VSCODE_SERVER_DIR=/home/vscode/.vscode-server")
    bloc = source[debut:debut + 700]
    assert 'chown -R "$SSH_USER:$SSH_USER" "$VSCODE_SERVER_DIR"' in bloc
    # Pas a chaque demarrage : un chown -R sur des milliers de fichiers
    # d'extensions couterait plusieurs secondes pour ne rien changer.
    assert "stat -c %U" in bloc, (
        "le chown n'est pas conditionne : il tournera a chaque demarrage")




# ---------- 49. le workspace s'ecrit, et OpenClaw se parle en page --------
#
# Deux demandes de Lucas : « vscode doit avoir acces aux dossiers des projets
# dans workspace, pour modifier les config git par exemple », et « discuter
# dans un chat plutot qu'en terminal ».


def test_les_permissions_du_workspace_sont_rattrapees_a_chaque_demarrage():
    """LA PASSE COMPLETE EST PROTEGEE PAR UN MARQUEUR, et c'est voulu : un
    chgrp -R sur un gros workspace coute plusieurs secondes a chaque
    demarrage. Mais elle laissait un trou -- /workspace est partage, et un
    dossier amorce par Dagster ou clone depuis une autre session nait avec
    le groupe de son createur, APRES la premiere passe. Plus rien ne le
    rattrapait : vscode le voyait sans pouvoir y ecrire, et « git config »
    echouait sur un refus de droits.
    """
    source = _entrypoint_dev()
    corps = "\n".join(l for l in source.split("\n")
                      if not l.strip().startswith("#"))
    marqueur = corps.index('if [ ! -f "$PERM_MARKER" ]')
    apres = corps[corps.index("\nfi", marqueur):]

    # HORS DU BLOC DU MARQUEUR : un rattrapage place dedans ne tournerait
    # qu'une fois, ce qui est exactement le defaut qu'on repare.
    assert "-exec chgrp" in apres, (
        "aucun rattrapage de groupe hors du bloc protege par le marqueur")
    assert "-exec chmod g+rwX" in apres, (
        "aucun rattrapage de droits hors du bloc protege par le marqueur")
    # Le setgid, sans quoi ce qui nait dans un dossier rattrape reprend le
    # groupe de son createur et le trou se rouvre au fichier suivant.
    assert "-exec chmod g+s" in apres, "le setgid n'est pas rattrape"

    # CIBLE, et pas massif : find ecarte les entrees deja conformes, donc le
    # cas courant ne coute qu'un parcours. Sans les negations, ce serait un
    # chmod -R a chaque demarrage.
    for negation in ('\\! -group "$CODELAB_GROUP"', "\\! -perm -g+w",
                     "\\! -perm -g+s"):
        assert negation in apres, (
            f"le rattrapage n'ecarte pas ce qui est deja conforme ({negation}) : "
            "il reecrirait tout le workspace a chaque demarrage")






def test_le_sommaire_de_credentials_env_est_en_tete_et_les_blocs_vides_partent(tmp_path):
    """Execute les fonctions de codelab-postgres sur un fichier d'une ancienne
    installation : le sommaire doit arriver EN TETE, en un seul exemplaire,
    et les blocs des autres services rester intacts."""
    import subprocess
    source = open(os.path.join(_racine_depot(), "postgres", "entrypoint.sh"),
                  encoding="utf-8").read()
    fonctions = "".join(re.findall(r"^(?:upsert_block|header_block)\(\) \{.*?\n\}\n",
                                   source, re.S | re.M))
    assert "header_block()" in fonctions
    appel = re.search(r'^header_block "# credentials\.env.*?"\n', source, re.S | re.M).group(0)
    assert 'upsert_block "codelab-dev"' not in source
    fichier = tmp_path / "credentials.env"
    fichier.write_text(
        "# ===== codelab-app-manager =====\nAPP_MANAGER_ADMIN_PASSWORD=x\n"
        "# ===== /codelab-app-manager =====\n"
        "# ===== codelab-header =====\n# ancien\n# ===== /codelab-header =====\n",
        encoding="utf-8")
    script = f'ENV_FILE="{fichier}"\n' + fonctions + appel
    subprocess.run(["sh", "-c", script], check=True, timeout=30)
    texte = fichier.read_text(encoding="utf-8")
    assert texte.startswith("# ===== codelab-header =====\n"), texte[:80]
    assert texte.count("# ===== codelab-header =====") == 1
    assert "# ancien" not in texte
    assert "APP_MANAGER_ADMIN_PASSWORD=x" in texte
    # Le sommaire nomme chaque bloc qui existe encore, et dit s'il va aux apps.
    for bloc in ("codelab-postgres", "codelab-app-manager", "codelab-sso", "codelab-alertes"):
        assert bloc in texte, bloc




def test_les_images_python_partagent_la_meme_base():
    """Docker ne stocke une couche qu'une fois. Une image sur une autre base
    Python en porte une copie a elle -- environ 120 Mo sur une eMMC de 17 Go
    qui a deja deborde. Et la CI teste sur la version que le panneau livre."""
    racine = _racine_depot()
    bases = {}
    for service in ("app-manager", "dev", "dagster"):
        source = open(os.path.join(racine, service, "Dockerfile"), encoding="utf-8").read()
        # La DERNIERE etape : c'est elle qui est livree. Une etape d'avant
        # (le Node recopie dans le panneau) ne laisse que ce qu'on en copie.
        bases[service] = re.findall(r"^FROM (\S+)", source, re.M)[-1]
    assert len(set(bases.values())) == 1, bases
    version = re.search(r"python:(\d+\.\d+)", bases["app-manager"]).group(1)
    ci = open(os.path.join(racine, ".github", "workflows", "build-images.yml"),
              encoding="utf-8").read()
    assert f'python-version: "{version}"' in ci, (
        "la CI ne teste pas sur la version de Python que le panneau livre")




def test_dagster_tient_interface_et_daemon_dans_un_seul_conteneur():
    """Plus de service codelab-dagster-daemon : l'image lance les deux
    programmes, et rien ne donne au daemon plus que ce qu'il avait."""
    racine = _racine_depot()
    for chemin in _composes():
        services = _services_du_compose(chemin)
        assert "codelab-dagster-daemon" not in services, chemin
        assert "codelab-dagster" in services, chemin
        # Toujours derriere le proxy : pas de port publie pour Dagster lui-meme.
        bloc = _bloc_du_service(chemin, "codelab-dagster")
        assert "ports:" not in bloc, chemin
    dockerfile = open(os.path.join(racine, "dagster", "Dockerfile"), encoding="utf-8").read()
    assert 'CMD ["/opt/dagster/lancer.sh"]' in dockerfile
    # lancer.sh passe par l'entrypoint, donc par la bascule sur l'utilisateur
    # dagster : pas d'ENTRYPOINT qui la contournerait.
    assert 'ENTRYPOINT ["/opt/dagster/entrypoint.sh"]' in dockerfile
    entree = open(os.path.join(racine, "dagster", "entrypoint.sh"), encoding="utf-8").read()
    assert 'exec gosu "$CODELAB_USER" "$@"' in entree




def test_lancer_relance_le_daemon_et_s_arrete_proprement(tmp_path):
    """Execute le vrai lancer.sh avec de faux programmes Dagster : un daemon
    qui tombe est relance, SIGTERM arrete les deux, et une interface qui
    meurt seule fait sortir le conteneur avec son code."""
    import signal
    import subprocess
    import time
    source = open(os.path.join(_racine_depot(), "dagster", "lancer.sh"),
                  encoding="utf-8").read().replace("PAUSE_RELANCE=10", "PAUSE_RELANCE=1")
    script = tmp_path / "lancer.sh"
    script.write_text(source, encoding="utf-8")
    journal = tmp_path / "journal"
    binaires = tmp_path / "bin"
    binaires.mkdir()
    (binaires / "dagster-daemon").write_text(
        "#!/bin/sh\n"
        f'echo "daemon start" >> {journal}\n'
        f'n=$(grep -c "daemon start" {journal})\n'
        f"trap 'echo \"daemon term\" >> {journal}; exit 0' TERM\n"
        'if [ "$n" -eq 1 ]; then sleep 0.5; exit 1; fi\n'
        "while :; do sleep 0.1; done\n", encoding="utf-8")
    (binaires / "dagster-webserver").write_text(
        "#!/bin/sh\n"
        f'echo "web start $*" >> {journal}\n'
        f"trap 'echo \"web term\" >> {journal}; exit 0' TERM\n"
        f'if [ -f {tmp_path}/web-meurt ]; then sleep 0.5; exit 3; fi\n'
        "while :; do sleep 0.1; done\n", encoding="utf-8")
    for f in binaires.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{binaires}:/usr/bin:/bin"}

    p = subprocess.Popen(["sh", str(script)], env=env)
    time.sleep(3)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=15) == 0
    lignes = journal.read_text(encoding="utf-8").splitlines()
    assert lignes.count("daemon start") == 2, lignes     # relance apres la chute
    assert "daemon term" in lignes and "web term" in lignes
    assert "web start -h 0.0.0.0 -p 3000 -m definitions" in lignes

    journal.write_text("", encoding="utf-8")
    (tmp_path / "web-meurt").write_text("", encoding="utf-8")
    assert subprocess.run(["sh", str(script)], env=env, timeout=15).returncode == 3




@pytest.mark.parametrize("nom", ["codelab", "dagster", "postgres"])
def test_codelab_db_refuse_les_bases_de_la_stack(nom):
    import subprocess
    outil = os.path.join(_racine_depot(), "dev", "codelab")
    r = subprocess.run(["bash", outil, "db", nom], capture_output=True, text=True,
                       timeout=10, env={"PATH": os.environ["PATH"]})
    assert r.returncode == 2
    assert "base de la stack" in r.stderr




def test_le_compose_casaos_ne_porte_aucune_variable_a_substituer():
    """CasaOS ne substitue pas « ${...} » : il transmet le texte tel quel,
    et un service recevrait la chaine « ${VARIABLE} » au lieu de sa valeur."""
    texte = open(os.path.join(_racine_depot(), "docker-compose-casaos.yml"),
                 encoding="utf-8").read()
    assert "${" not in texte


# ------------------------ Codex sur un compte ChatGPT ------------------------




def test_un_config_toml_neuf_n_a_aucun_fournisseur_et_un_autre_n_est_pas_touche(tmp_path):
    r = _bloc_codex(tmp_path)
    assert r.returncode == 0, r.stderr
    neuf = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "model_provider" not in neuf
    assert 'sandbox_mode = "danger-full-access"' in neuf

    a_soi = 'model = "gpt-5.5"\n\n[mcp_servers.x]\ncommand = "y"\n'
    (tmp_path / "config.toml").write_text(a_soi, encoding="utf-8")
    _bloc_codex(tmp_path)
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == a_soi




def test_le_jeton_chatgpt_n_est_lisible_que_par_vscode(tmp_path):
    """/workspace/.codex est partage avec le groupe codelab (panneau,
    Dagster) : un auth.json en 0660 leur donnerait le compte ChatGPT."""
    jeton = tmp_path / "auth.json"
    jeton.write_text("{}", encoding="utf-8")
    jeton.chmod(0o664)
    assert _bloc_codex(tmp_path).returncode == 0
    assert stat.S_IMODE(jeton.stat().st_mode) == 0o600




def _lier_a_claude(lien, cible):
    """La fonction lier_a_claude de dev/entrypoint.sh, prise telle quelle."""
    import subprocess
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"),
                  encoding="utf-8").read()
    debut = source.index("lier_a_claude() {")
    fonction = source[debut:source.index("\n}\n", debut) + 3]
    return subprocess.run(["bash", "-c", fonction + 'lier_a_claude "$1" "$2"', "_",
                           str(lien), str(cible)],
                          capture_output=True, text=True, timeout=30)


def test_la_connexion_de_claude_code_survit_a_une_mise_a_jour(tmp_path):
    """Vecu : « je n'arrive pas a me connecter. Claude Code bug constamment ».

    Claude Code rangeait sa connexion sous /home/vscode, efface a chaque
    mise a jour de l'image. Elle vit desormais dans /workspace/.claude-code
    (CLAUDE_CONFIG_DIR), ~/.claude et ~/.claude.json n'en sont que des liens,
    et ce qui s'etait ecrit a leur place est recopie, jamais efface."""
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"),
                  encoding="utf-8").read()
    assert 'CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$WORKSPACE_DIR/.claude-code}"' in source
    assert 'echo "export CLAUDE_CONFIG_DIR=$CLAUDE_DIR"' in source
    assert '"$CODEX_PROFILE" "$CLAUDE_PROFILE" "$PROFILE"; do' in source
    assert 'chmod 700 "$CLAUDE_DIR"' in source

    cible = tmp_path / "workspace" / ".claude-code"
    cible.mkdir(parents=True)
    maison = tmp_path / "home"
    maison.mkdir()
    # Avant le lien : un vrai dossier, avec la connexion dedans.
    (maison / ".claude").mkdir()
    (maison / ".claude" / ".credentials.json").write_text("jeton", encoding="utf-8")
    assert _lier_a_claude(maison / ".claude", cible).returncode == 0
    assert (maison / ".claude").is_symlink()
    assert (cible / ".credentials.json").read_text(encoding="utf-8") == "jeton"

    # Un fichier reecrit par renommage par-dessus le lien : recopie, puis relie.
    (maison / ".claude.json").write_text('{"neuf": 1}', encoding="utf-8")
    assert _lier_a_claude(maison / ".claude.json", cible / ".claude.json").returncode == 0
    assert (maison / ".claude.json").is_symlink()
    assert (cible / ".claude.json").read_text(encoding="utf-8") == '{"neuf": 1}'

    # Et au demarrage suivant, rien ne bouge.
    assert _lier_a_claude(maison / ".claude", cible).returncode == 0
    assert (cible / ".credentials.json").exists() and not (cible / ".claude-code").exists()


def test_l_archive_du_workspace_n_emporte_pas_les_jetons_des_agents():
    """Les jetons de Codex et de Claude Code restent sur ce disque."""
    assert {".codex", ".claude-code"} <= app.WORKSPACE_EXCLUS




def test_l_image_dev_n_embarque_pas_de_catalogue_de_modeles():
    racine = _racine_depot()
    dockerfile = open(os.path.join(racine, "dev", "Dockerfile"), encoding="utf-8").read()
    assert "codex-modeles" not in dockerfile
    assert not os.path.exists(os.path.join(racine, "dev", "codex-modeles.json"))




def test_plus_aucune_trace_de_l_ancien_service_ia_dans_le_depot():
    """Demande : plus aucune reference a l'ancien service IA, nulle part.

    Le service, sa passerelle, ses cles et le code qui migrait les
    installations existantes sont partis. Le nettoyage d'un serveur qui les
    portait encore se fait une fois, a la main. Ce test tient la regle sur
    TOUT le depot : un mot qui revient dans un commentaire, une doc ou un
    script le fait echouer. Les motifs sont assembles morceau par morceau,
    sans quoi ce fichier se trouverait lui-meme.
    """
    mot = "l" + "l" + "m"
    motif = re.compile("(?<![a-z])" + mot + "(?![a-z])|lite" + mot + "|open" + "router",
                       re.IGNORECASE)
    racine = _racine_depot()
    trouves = []
    for dossier, sous, fichiers in os.walk(racine):
        sous[:] = [s for s in sous if s not in (".git", "__pycache__", ".pytest_cache",
                                                "node_modules", ".venv")]
        for nom in fichiers:
            chemin = os.path.join(dossier, nom)
            try:
                with open(chemin, encoding="utf-8") as f:
                    for n, ligne in enumerate(f, 1):
                        if motif.search(ligne):
                            trouves.append(f"{os.path.relpath(chemin, racine)}:{n}")
            except (UnicodeDecodeError, OSError):
                continue
    assert not trouves, trouves
    assert not os.path.exists(os.path.join(racine, "l" + "lm"))
    regles = {r.rule for r in app.flask_app.url_map.iter_rules()}
    assert not [r for r in regles if r.startswith("/api/cles")], regles




def test_redis_est_dans_la_stack_sans_port_publie_et_durci():
    """Un Redis publie sur Internet est l'une des portes les plus exploitees
    qui soient : il n'est joignable que depuis le reseau de la stack."""
    for chemin in _composes():
        bloc = _bloc_du_service(chemin, "codelab-redis")
        assert bloc, f"codelab-redis absent de {os.path.basename(chemin)}"
        assert "ports:" not in bloc, chemin
        assert "no-new-privileges:true" in bloc and "- ALL" in bloc, chemin
        assert "/DATA/AppData/codelab/redis:/data" in bloc, chemin
        # La sonde lit le mot de passe elle-meme : aucun "$" dans le compose.
        assert '["CMD", "codelab-redis-sante"]' in bloc, chemin
        valeurs = "".join(l for l in bloc.splitlines(True) if not l.lstrip().startswith("#"))
        assert "$" not in valeurs, chemin
    ci = open(os.path.join(_racine_depot(), ".github", "workflows", "build-images.yml"),
              encoding="utf-8").read()
    assert "codelab-redis redis/Dockerfile redis/" in ci and '"redis/**"' in ci




def test_l_entrypoint_redis_genere_un_mot_de_passe_stable_et_une_config_fermee(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    env_file = config / "credentials.env"
    env_file.write_text("# ===== codelab-postgres =====\nPOSTGRES_PASSWORD=y\n"
                        "# ===== /codelab-postgres =====\n", encoding="utf-8")
    sortie = _lancer_entrypoint_redis(tmp_path).communicate(timeout=30)[0]
    assert f"LANCE redis-server {tmp_path / 'redis.conf'}" in sortie, sortie
    texte = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=y" in texte
    mdp = re.search(r"^REDIS_PASSWORD=([0-9a-f]{64})$", texte, re.M).group(1)
    assert f"REDIS_URL=redis://default:{mdp}@codelab-redis:6379/0" in texte
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    conf = (tmp_path / "redis.conf").read_text(encoding="utf-8")
    assert stat.S_IMODE((tmp_path / "redis.conf").stat().st_mode) == 0o600
    assert f"user default on >{mdp} ~* &* +@all -@admin" in conf
    assert "maxmemory-policy volatile-lru" in conf and "appendonly yes" in conf
    # Le mot de passe ne passe jamais sur la ligne de commande.
    assert mdp not in sortie

    # Second demarrage : meme mot de passe, un seul bloc.
    _lancer_entrypoint_redis(tmp_path).communicate(timeout=30)
    texte = env_file.read_text(encoding="utf-8")
    assert texte.count("# ===== codelab-redis =====") == 1
    assert f"REDIS_PASSWORD={mdp}" in texte




@pytest.mark.skipif(not shutil.which("redis-server"), reason="redis-server absent")
def test_la_config_redis_generee_refuse_l_anonyme_et_l_administration(tmp_path):
    """La configuration, chargee par un vrai redis-server : sans mot de passe
    rien ne passe, et avec, CONFIG reste refusee."""
    import subprocess
    port = _port_libre()
    _lancer_entrypoint_redis(tmp_path, {"CODELAB_REDIS_PORT": str(port)}).communicate(timeout=30)
    (tmp_path / "data").mkdir(exist_ok=True)
    serveur = subprocess.Popen(["redis-server", str(tmp_path / "redis.conf")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        mdp = re.search(r"^REDIS_PASSWORD=(\w+)$",
                        (tmp_path / "config" / "credentials.env").read_text(), re.M).group(1)

        def cli(*args, auth=True):
            env = dict(os.environ, REDISCLI_AUTH=mdp) if auth else {
                k: v for k, v in os.environ.items() if k != "REDISCLI_AUTH"}
            return subprocess.run(["redis-cli", "-p", str(port), *args], env=env,
                                  capture_output=True, text=True, timeout=10).stdout

        for _ in range(50):
            if "PONG" in cli("ping"):
                break
            time.sleep(0.1)
        assert "NOAUTH" in cli("get", "x", auth=False)
        assert cli("set", "app:x", "1").strip() == "OK"
        assert "NOPERM" in cli("config", "set", "dir", "/tmp")
        # Et la sonde de sante du conteneur passe sur cette configuration.
        sante = subprocess.run(
            ["sh", os.path.join(_racine_depot(), "redis", "sante.sh")],
            env=dict(os.environ, CODELAB_REDIS_CONF=str(tmp_path / "redis.conf"),
                     CODELAB_REDIS_PORT=str(port)), timeout=10)
        assert sante.returncode == 0
    finally:
        serveur.kill()
        serveur.wait()




def test_les_ecrivains_de_credentials_env_attendent_le_verrou_partage(tmp_path):
    """Quatre services reecrivent credentials.env a leur demarrage. Sans
    verrou, deux qui demarrent ensemble lisent le meme contenu, et le dernier
    a ecrire efface le bloc de l'autre -- celui de Postgres compris, dont le
    mot de passe ne se regenere pas sans casser la base.

    Le verrou est tenu ici comme le panneau le tient : l'entrypoint Redis doit
    attendre, puis ecrire une fois le verrou rendu."""
    import fcntl
    (tmp_path / "config").mkdir()
    env_file = tmp_path / "config" / "credentials.env"
    env_file.write_text("", encoding="utf-8")
    verrou = os.open(str(env_file) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(verrou, fcntl.LOCK_EX)
    proc = _lancer_entrypoint_redis(tmp_path)
    try:
        time.sleep(0.5)
        assert proc.poll() is None, "l'entrypoint n'a pas attendu le verrou"
        assert "REDIS_PASSWORD" not in env_file.read_text(encoding="utf-8")
    finally:
        os.close(verrou)
    proc.communicate(timeout=30)
    assert "REDIS_PASSWORD=" in env_file.read_text(encoding="utf-8")

    racine = _racine_depot()
    for chemin in (("postgres", "entrypoint.sh"), ("redis", "entrypoint.sh"),
                   ("dagster", "proxy", "entrypoint.sh")):
        source = open(os.path.join(racine, *chemin), encoding="utf-8").read()
        assert 'exec 9>"$ENV_FILE.lock"' in source and "flock 9" in source, chemin
        # Rendu avant de passer la main au serveur, qui le garderait sinon.
        assert source.index("exec 9>&-") < source.rindex("\nexec "), chemin
    assert "fcntl.flock" in inspect.getsource(app.upsert_shared_block)




def test_le_panneau_ecrit_credentials_env_sous_le_verrou(tmp_path, monkeypatch):
    """upsert_shared_block attend le meme verrou que les entrypoints."""
    import fcntl
    import threading
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    verrou = os.open(str(tmp_path / "credentials.env.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    fcntl.flock(verrou, fcntl.LOCK_EX)
    fil = threading.Thread(target=app.upsert_shared_block, args=("essai", [], {"A": "1"}))
    fil.start()
    time.sleep(0.3)
    assert fil.is_alive(), "le panneau a ecrit sans attendre le verrou"
    os.close(verrou)
    fil.join(10)
    assert "A=1" in (tmp_path / "credentials.env").read_text()




def test_une_session_ssh_recoit_redis_et_le_sommaire_le_nomme():
    racine = _racine_depot()
    entree = open(os.path.join(racine, "dev", "entrypoint.sh"), encoding="utf-8").read()
    assert 'export REDISCLI_AUTH=' in entree and 'export REDIS_URL=' in entree
    assert "redis-tools" in open(os.path.join(racine, "dev", "Dockerfile"),
                                 encoding="utf-8").read()
    postgres = open(os.path.join(racine, "postgres", "entrypoint.sh"), encoding="utf-8").read()
    assert "#   codelab-redis        codelab-redis" in postgres


# ---------- l'extension Database Client JDBC dans VS Code ----------




def test_l_extension_postgresql_est_recommandee_sans_ecraser_le_reste(tmp_path):
    import subprocess
    source, bloc = _bloc_extensions_vscode(tmp_path)
    assert "cweijan.vscode-database-client2 cweijan.dbclient-jdbc" in bloc
    ws = tmp_path / "ws"
    (ws / ".vscode").mkdir(parents=True)
    fichier = ws / ".vscode" / "extensions.json"
    # L'ancienne recommandation (extension PostgreSQL) est remplacee.
    fichier.write_text('{"recommendations": ["ms-python.python", "ms-ossdata.vscode-pgsql"], '
                       '"autre": 1}')
    script = (f'set -e\nSSH_USER=personne\nVSCODE_SERVER_DIR="{tmp_path}/vs"\n'
              f'WORKSPACE_DIR="{ws}"\n' + bloc)
    for _ in range(2):   # idempotent : pas de doublon au redemarrage
        subprocess.run(["bash", "-c", script], check=True, timeout=30)
    contenu = json.loads(fichier.read_text())
    assert contenu == {"recommendations": ["ms-python.python", "mtxr.sqltools", "mtxr.sqltools-driver-pg",
                                           "cweijan.vscode-database-client2", "cweijan.dbclient-jdbc"],
                       "autre": 1}
    # Un fichier JSONC (avec commentaires) est laisse tel quel.
    fichier.write_text('// a moi\n{"recommendations": []}')
    subprocess.run(["bash", "-c", script], check=True, timeout=30)
    assert fichier.read_text().startswith("// a moi")




def test_l_extension_postgresql_s_installe_une_fois_sous_le_compte_vscode():
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    assert 'runuser -u "$SSH_USER" -- env HOME="/home/$SSH_USER"' in source
    assert '--install-extension "$ext"' in source
    # Deja installee : rien a telecharger. Echec : un nouvel essai par heure.
    assert 'ls -d "$VSCODE_SERVER_DIR/extensions/$ext_min-"*' in source
    assert "-mmin -60" in source
    for chemin in _composes():
        assert ('CODELAB_VSCODE_EXTENSIONS: "mtxr.sqltools mtxr.sqltools-driver-pg '
                'cweijan.vscode-database-client2 cweijan.dbclient-jdbc"') in open(
            chemin, encoding="utf-8").read(), chemin




def test_chaque_base_apparait_dans_sqltools_sans_rien_creer(tmp_path):
    """SQLTools lit ses connexions dans les reglages : « codelab vscode-bases »
    y declare chaque base de projet (groupe CodeLab), CHACUNE AVEC SON COMPTE
    (~/.codelab-bases, tenu par le gardien des bases), garde les connexions
    ajoutees a la main, et ne touche pas un fichier a commentaires."""
    import subprocess
    bases = tmp_path / "bases"
    bases.write_text("recettes mdpR\ncarnet mdpC\n")
    reglages = tmp_path / "Machine" / "settings.json"
    reglages.parent.mkdir()
    reglages.write_text(json.dumps({"editor.fontSize": 14, "sqltools.connections": [
        {"name": "perso", "driver": "PostgreSQL"},
        {"name": "ancienne", "group": "CodeLab", "driver": "PostgreSQL"}]}))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "CODELAB_VSCODE_REGLAGES": str(reglages), "CODELAB_BASES_FICHIER": str(bases)}
    commande = ["sh", os.path.join(_racine_depot(), "dev", "codelab"), "vscode-bases"]
    subprocess.run(commande, env=env, check=True, timeout=30)
    d = json.loads(reglages.read_text())
    assert d["editor.fontSize"] == 14
    assert [(c["name"], c.get("group")) for c in d["sqltools.connections"]] == [
        ("recettes", "CodeLab"), ("carnet", "CodeLab"), ("perso", None)]
    c = d["sqltools.connections"][1]
    assert c["driver"] == "PostgreSQL" and c["server"] == "codelab-postgres" and c["database"] == "carnet"
    # Le compte de la base, jamais l'administrateur.
    assert c["username"] == "carnet" and c["password"] == "mdpC"
    assert stat.S_IMODE(os.stat(reglages).st_mode) == 0o600
    assert not (tmp_path / ".pgpass").exists(), "le terminal n'a que le compte du projet ouvert"
    # Un fichier avec des commentaires reste tel quel.
    reglages.write_text("// a moi\n{}")
    r = subprocess.run(commande, env=env, timeout=30, capture_output=True, text=True)
    assert r.returncode != 0 and reglages.read_text() == "// a moi\n{}"
    # Le gardien des bases du poste les tient a jour, et apres « codelab db ».
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    assert "codelab vscode-bases --discret" in source and "ecrire_les_bases_du_poste || true" in source


# ------------------- isolation des dependances des projets Dagster -------------------




def test_seuls_les_projets_qui_le_demandent_sont_isoles(tmp_path):
    iso = _isolation()
    _workspace_isole(tmp_path)
    assert iso.projets_isoles(str(tmp_path)) == ["lourd"]
    contenu = json.loads(iso.workspace_yaml(str(tmp_path), ["lourd"]))
    commun, lourd = contenu["load_from"]
    # L'emplacement commun garde la forme de « -m definitions » : meme nom,
    # donc memes plannings et meme historique.
    assert commun == {"python_module": "definitions"}
    assert lourd["python_file"]["location_name"] == "lourd"
    assert lourd["python_file"]["executable_path"] == str(
        tmp_path / "lourd" / ".dagster-venv" / "bin" / "python")
    assert lourd["python_file"]["working_directory"] == str(tmp_path / "lourd")




def test_l_environnement_n_est_reconstruit_que_si_le_fichier_change(tmp_path):
    iso = _isolation()
    _workspace_isole(tmp_path)
    projet = str(tmp_path / "lourd")
    appels = []

    def faux(cmd, **kw):
        appels.append(cmd)
        assert kw.get("stdout") is sys.stderr     # rien sur la sortie lue par lancer.sh
        if "venv" in cmd:
            bin_ = Path(cmd[-1], "bin")
            bin_.mkdir(parents=True)
            (bin_ / "python").write_text("")
            Path(cmd[-1], "lib", "python3.13", "site-packages").mkdir(parents=True)
    assert iso.preparer(projet, executer=faux) is True
    assert len(appels) == 2 and appels[1][-1].endswith("requirements-dagster.txt")
    pth = tmp_path / "lourd" / ".dagster-venv" / "lib" / "python3.13" / "site-packages" / "_codelab_image.pth"
    assert pth.exists()
    # Inchange : rien a refaire.
    assert iso.preparer(projet, executer=faux) is True and len(appels) == 2
    # Modifie : reconstruit.
    (tmp_path / "lourd" / "requirements-dagster.txt").write_text("pandas==2.2.4\n")
    assert iso.preparer(projet, executer=faux) is True and len(appels) == 4




def test_un_environnement_qui_echoue_est_retire(tmp_path):
    import subprocess
    iso = _isolation()
    _workspace_isole(tmp_path)

    def echoue(cmd, **kw):
        if "venv" in cmd:
            Path(cmd[-1], "bin").mkdir(parents=True)
            return
        raise subprocess.CalledProcessError(1, cmd)
    assert iso.preparer(str(tmp_path / "lourd"), executer=echoue) is False
    assert not (tmp_path / "lourd" / ".dagster-venv").exists()




def test_sans_projet_isole_le_workspace_yaml_disparait(tmp_path, monkeypatch, capsys):
    iso = _isolation()
    ws, home = tmp_path / "ws", tmp_path / "home"
    (ws / "commun").mkdir(parents=True)
    (ws / "commun" / "definitions.py").write_text("")
    home.mkdir()
    (home / "workspace.yaml").write_text("ancien")
    monkeypatch.setenv("CODELAB_WORKSPACE", str(ws))
    monkeypatch.setenv("DAGSTER_HOME", str(home))
    assert iso.main() == 0
    assert capsys.readouterr().out == ""
    assert not (home / "workspace.yaml").exists()




def test_lancer_passe_le_workspace_yaml_quand_un_projet_est_isole(tmp_path):
    import subprocess
    source = open(os.path.join(_racine_depot(), "dagster", "lancer.sh"),
                  encoding="utf-8").read().replace("PAUSE_RELANCE=10", "PAUSE_RELANCE=1")
    script = tmp_path / "lancer.sh"
    script.write_text(source, encoding="utf-8")
    yaml_ = tmp_path / "workspace.yaml"
    yaml_.write_text("{}")
    faux_iso = tmp_path / "isolation.py"
    faux_iso.write_text(f"print({str(yaml_)!r})\n")
    journal = tmp_path / "journal"
    binaires = tmp_path / "bin"
    binaires.mkdir()
    # Le daemon tourne jusqu'a SIGTERM ; l'interface s'arrete d'elle-meme, ce
    # qui fait sortir lancer.sh apres avoir arrete le daemon.
    (binaires / "dagster-daemon").write_text(
        f'#!/bin/sh\necho "dagster-daemon $*" >> {journal}\n'
        "trap 'exit 0' TERM\nwhile :; do sleep 0.1; done\n")
    (binaires / "dagster-webserver").write_text(
        f'#!/bin/sh\necho "dagster-webserver $*" >> {journal}\nsleep 0.3\nexit 3\n')
    for f in binaires.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{binaires}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
           "CODELAB_ISOLATION": str(faux_iso)}
    assert subprocess.run(["sh", str(script)], env=env, timeout=30).returncode == 3
    lignes = journal.read_text().splitlines()
    assert f"dagster-webserver -h 0.0.0.0 -p 3000 -w {yaml_}" in lignes
    assert f"dagster-daemon run -w {yaml_}" in lignes
    # Le script ne sort rien (aucun projet isole) : « -m definitions », comme avant.
    faux_iso.write_text("")
    journal.write_text("")
    assert subprocess.run(["sh", str(script)], env=env, timeout=30).returncode == 3
    assert "dagster-webserver -h 0.0.0.0 -p 3000 -m definitions" in journal.read_text().splitlines()
    dockerfile = open(os.path.join(_racine_depot(), "dagster", "Dockerfile")).read()
    assert "COPY dagster/isolation.py /opt/dagster/isolation.py" in dockerfile




def test_l_agregateur_ne_charge_pas_un_projet_isole(tmp_path, monkeypatch):
    """Le definitions.py racine, avec un faux module dagster : un projet isole
    n'est pas importe une seconde fois dans le processus commun."""
    import types
    faux = types.ModuleType("dagster")

    class Definitions:
        def __init__(self, **kw):
            self.kw = kw

        @staticmethod
        def merge(*defs):
            return list(defs)
    faux.Definitions = Definitions
    monkeypatch.setitem(sys.modules, "dagster", faux)
    _workspace_isole(tmp_path)
    (tmp_path / "lourd" / "definitions.py").write_text("raise SystemExit('charge a tort')\n")
    (tmp_path / "commun" / "definitions.py").write_text("defs = 'commun'\n")
    source = open(os.path.join(_racine_depot(), "workspace", "definitions.py"),
                  encoding="utf-8").read()
    agregateur = tmp_path / "definitions.py"
    agregateur.write_text(source)
    spec = importlib.util.spec_from_file_location("agregateur_test", agregateur)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.defs == ["commun"]


def test_l_agregateur_range_chaque_projet_sous_son_nom(tmp_path, monkeypatch):
    """Vecu : « possible d'identifier et separer les taches sur Dagster par
    projet ? ». Chaque asset recoit l'etiquette codelab/projet et, s'il n'a
    pas de groupe a lui, le groupe du projet -- sans tiret, que Dagster
    refuse dans un nom de groupe."""
    import types
    faux = types.ModuleType("dagster")

    class Spec:
        def __init__(self, groupe, tags=None):
            self.group_name, self.tags = groupe, tags or {}

        def replace_attributes(self, **kw):
            return Spec(kw.get("group_name", self.group_name), kw.get("tags", self.tags))

    class Definitions:
        def __init__(self, specs=()):
            self.specs = list(specs)

        def map_asset_specs(self, func):
            return Definitions(func(s) for s in self.specs)

        @staticmethod
        def merge(*defs):
            return [s for d in defs for s in d.specs]
    faux.Definitions = Definitions
    faux.Spec = Spec
    monkeypatch.setitem(sys.modules, "dagster", faux)
    (tmp_path / "mon-projet").mkdir()
    (tmp_path / "mon-projet" / "definitions.py").write_text(
        "from dagster import Definitions, Spec\n"
        "defs = Definitions([Spec(None), Spec('default'), Spec('choisi', {'k': 'v'})])\n")
    source = open(os.path.join(_racine_depot(), "workspace", "definitions.py"),
                  encoding="utf-8").read()
    agregateur = tmp_path / "definitions.py"
    agregateur.write_text(source)
    spec = importlib.util.spec_from_file_location("agregateur_range", agregateur)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert [s.group_name for s in module.defs] == ["mon_projet", "mon_projet", "choisi"]
    assert all(s.tags["codelab/projet"] == "mon-projet" for s in module.defs)
    assert module.defs[2].tags["k"] == "v"


def _images_de_la_ci():
    """La table de la CI : {image: (Dockerfile, debuts de chemin)}."""
    import yaml
    chemin = os.path.join(_racine_depot(), ".github", "workflows", "build-images.yml")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    ci = yaml.safe_load(open(chemin, encoding="utf-8"))
    script = ci["jobs"]["changements"]["steps"][1]["run"]
    table = script.split("<<'FIN'\n", 1)[1].split("\nFIN", 1)[0]
    images = {}
    for ligne in table.splitlines():
        nom, dockerfile, *chemins = ligne.split()
        images[nom] = (dockerfile, chemins)
    return ci, images


def test_la_ci_ne_reconstruit_que_les_images_touchees_et_n_en_oublie_aucune():
    """Vecu : « The job was not started because [...] your spending limit
    needs to be increased ». Sur le plan gratuit, chaque push reconstruisait
    les sept images ; une journee epuisait les 2 000 minutes du mois. Une
    image ne se reconstruit plus que si un fichier qu'elle copie change :
    la table doit donc couvrir CHAQUE COPY de chaque Dockerfile, sinon une
    modification partirait en production sans nouvelle image."""
    ci, images = _images_de_la_ci()
    racine = _racine_depot()
    dockerfiles = {os.path.relpath(os.path.join(d, f), racine)
                   for d, _, fichiers in os.walk(racine) if ".git" not in d
                   for f in fichiers if f == "Dockerfile"}
    assert {df for df, _ in images.values()} == dockerfiles
    for nom, (dockerfile, chemins) in images.items():
        assert any(dockerfile.startswith(c) for c in chemins), (nom, "son Dockerfile")
        texte = open(os.path.join(racine, dockerfile), encoding="utf-8").read()
        for ligne in texte.splitlines():
            mots = ligne.split()
            if not mots or mots[0] not in ("COPY", "ADD") or any(m.startswith("--from") for m in mots):
                continue
            for source in [m for m in mots[1:-1] if not m.startswith("--")]:
                assert any(source.startswith(c) or c.startswith(source.rstrip("/") + "/")
                           for c in chemins), (nom, source)
    # La matrice vient de la table, et rien ne se construit quand rien n'a change.
    build = ci["jobs"]["build"]
    assert build["strategy"]["matrix"] == "${{ fromJSON(needs.changements.outputs.matrice) }}"
    assert build["if"] == "needs.changements.outputs.nombre != '0'"
    # L'arm64 (emule, le plus cher) seulement hors pull request, et reglable.
    assert "vars.CODELAB_PLATEFORMES || 'linux/amd64,linux/arm64'" in build["env"]["PLATEFORMES"]
    assert "github.event_name == 'pull_request' && 'linux/amd64'" in build["env"]["PLATEFORMES"]


def test_la_ci_choisit_ses_images_d_apres_le_diff(tmp_path):
    """Le script de la CI, joue pour de vrai sur un petit depot : un
    changement dans redis/ ne reconstruit que Redis ; le workflow lui-meme,
    un lundi ou un tag, tout ; un commit precedent inconnu, tout."""
    import subprocess
    ci, images = _images_de_la_ci()
    script = ci["jobs"]["changements"]["steps"][1]["run"]
    depot = tmp_path / "depot"
    depot.mkdir()

    def git(*a):
        subprocess.run(["git", "-C", str(depot), *a], check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    git("init", "-q")
    (depot / "README.md").write_text("x")
    git("add", "-A"); git("commit", "-qm", "un")
    avant = subprocess.run(["git", "-C", str(depot), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()

    # Un faux « gh » : il rend la derniere construction reussie de main.
    outils = tmp_path / "outils"
    outils.mkdir()
    (outils / "gh").write_text('#!/bin/sh\nprintf "%s\\n" "$FAUX_REUSSI"\n')
    (outils / "gh").chmod(0o755)
    reussi = {"sha": ""}

    def choisir(evenement, avant_sha, fichier=None):
        if fichier:
            (depot / fichier).parent.mkdir(parents=True, exist_ok=True)
            (depot / fichier).write_text(secrets.token_hex(4))
            git("add", "-A"); git("commit", "-qm", "deux")
        sortie = tmp_path / ("sortie-" + secrets.token_hex(3))
        r = subprocess.run(["bash", "-e", "-c", script], cwd=depot, capture_output=True, text=True,
                           env=dict(os.environ, EVENEMENT=evenement, AVANT=avant_sha,
                                    PATH=f"{outils}:{os.environ['PATH']}",
                                    FAUX_REUSSI=reussi["sha"], DEPOT="x/y",
                                    TYPE_REF="branch", BASE_PR="", GITHUB_OUTPUT=str(sortie),
                                    RUNNER_TEMP=str(tmp_path)))
        assert r.returncode == 0, r.stderr
        lignes = dict(l.split("=", 1) for l in sortie.read_text().splitlines())
        return [i["name"] for i in json.loads(lignes["matrice"])["include"]]

    assert choisir("push", avant, "redis/sante.sh") == ["codelab-redis"]
    tete = subprocess.run(["git", "-C", str(depot), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert choisir("push", tete, "docker-compose.yml") == []
    tete = subprocess.run(["git", "-C", str(depot), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert set(choisir("push", tete, "workspace/messages/app.py")) == {"codelab-dagster"}
    tete = subprocess.run(["git", "-C", str(depot), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert set(choisir("push", tete, ".github/workflows/build-images.yml")) == set(images)
    assert set(choisir("schedule", "")) == set(images)
    assert set(choisir("push", "0" * 40)) == set(images)
    # Vecu : la fusion de Messages a echoue aux tests sur main ; le push
    # suivant ne comparait qu'au commit precedent, et l'image du panneau n'a
    # jamais recu Messages. La base est la derniere construction REUSSIE.
    reussi["sha"] = subprocess.run(["git", "-C", str(depot), "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout.strip()
    choisir("push", reussi["sha"], "app-manager/app/app.py")      # echoue : rien de construit
    tete = subprocess.run(["git", "-C", str(depot), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert set(choisir("push", tete, "redis/sante.sh")) == {"codelab-app-manager", "codelab-redis"}


def test_une_cle_ssh_sans_retour_a_la_ligne_final_est_autorisee(tmp_path):
    """Vecu : « Permission denied (publickey) », avec un
    MacBookAir-Lucas.pub bien present et « AUCUNE cle autorisee » au
    demarrage. Le fichier n'avait pas de retour a la ligne final, et
    « read » abandonnait la derniere ligne. Une cle venue de Windows (fin
    de ligne CR LF) doit aussi passer, sans le CR que sshd prendrait pour
    une partie de la cle."""
    import pwd
    import subprocess
    entree = os.path.join(_racine_depot(), "dev", "entrypoint.sh")
    if not os.path.exists(entree):
        pytest.skip("depot complet absent de cette image")
    texte = open(entree, encoding="utf-8").read()
    morceaux = [l for l in texte.splitlines() if l.startswith("CR=")]
    for nom in ("key_ids", "sync_authorized_keys"):
        debut = texte.index(nom + "() {")
        morceaux.append(texte[debut:texte.index("\n}\n", debut) + 3])
    cles = tmp_path / "authorized_keys.d"
    cles.mkdir()
    (cles / "mac.pub").write_bytes(b"ssh-ed25519 AAAAC3Mac lucas@mac")
    (cles / "pc.pub").write_bytes(b"ssh-ed25519 AAAAC3Pc\r\n")
    (cles / "deux.pub").write_bytes(b"ssh-ed25519 AAAAC3Un a\nssh-ed25519 AAAAC3Deux b")
    r = subprocess.run(["sh", "-c", "\n".join(morceaux) + "\nsync_authorized_keys"],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, SSH_USER=pwd.getpwuid(os.getuid()).pw_name, SSH_DIR=str(tmp_path),
                                AUTHORIZED_KEYS=str(tmp_path / "authorized_keys"),
                                KEYS_D=str(cles), DERIVED_SNAPSHOT=str(tmp_path / ".derived")))
    assert r.returncode == 0, r.stderr
    assert "4 cle(s) autorisee(s)" in r.stdout, r.stdout
    lignes = [l for l in (tmp_path / "authorized_keys").read_bytes().split(b"\n")
              if l and not l.startswith(b"#")]
    assert lignes == [b"ssh-ed25519 AAAAC3Un a", b"ssh-ed25519 AAAAC3Deux b",
                      b"ssh-ed25519 AAAAC3Mac lucas@mac", b"ssh-ed25519 AAAAC3Pc"]


# ===========================================================================
# LE DIAGNOSTIC
# les sondes de checks.py, leur hierarchie, la verification approfondie, la
# documentation qui ne ment pas, la separation sondes et tests.
# ===========================================================================

# ------------------- 5. inscription du projet de diagnostic -------------------
#
# CE QUE CETTE SECTION DISAIT AVANT, ET POURQUOI ELLE DIT L'INVERSE.
#
# L'inscription etait une amorce a jouer UNE FOIS, et la propriete tenue ici
# etait « il ne se reinscrit jamais » : un projet supprime depuis le panneau
# ne devait pas revenir au redemarrage suivant.
#
# Le diagnostic n'est plus un projet parmi d'autres. C'est une application
# PAR DEFAUT : le serveur refuse de la supprimer, de la configurer et de
# l'arreter. Il n'y a donc plus de suppression volontaire a respecter, et la
# garantie qui compte s'est inversee -- sur une installation en service, le
# diagnostic EST LA, inscrit et configure.
#
# Ce n'est pas un confort. Trois chemins menaient a une installation qui n'en
# avait pas, et aucun ne se voyait : le dossier pas encore amorce par dagster
# pendant qu'une autre application etait inscrite, un marqueur pose sans que
# l'inscription reussisse, et toute installation anterieure au diagnostic.




def test_le_diagnostic_est_inscrit_et_configure(tmp_path, monkeypatch):
    """« Deja cree ET configure » : la fiche n'a pas de formulaire.

    Une application par defaut ne se configure pas depuis le panneau. Tout ce
    dont elle a besoin doit donc etre pose a l'inscription -- a commencer par
    la commande de build, sans laquelle le pilote Postgres ne s'installe sur
    ordre de personne.
    """
    racine = _amorcage(tmp_path, monkeypatch)
    assert app.amorcer_projet("diagnostic") == "diagnostic"
    inscrit = app.load()["diagnostic"]
    assert inscrit["path"] == str(racine / "diagnostic")
    assert inscrit["command"] == app.COMMANDE_PAR_DEFAUT
    assert inscrit["build_command"] == app.BUILD_PAR_DEFAUT
    assert inscrit["enabled"] is True
    assert inscrit["titre"] == app.TITRES_PAR_DEFAUT["diagnostic"]
    assert inscrit["description"] == app.DIAGNOSTIC_DESCRIPTION
    assert inscrit["visibility"] == app.VISIBILITE_PRIVEE
    assert app.PORT_MIN <= inscrit["port"] <= app.PORT_MAX




def test_un_diagnostic_efface_revient_au_redemarrage(tmp_path, monkeypatch):
    """L'INVERSE de ce que ce fichier tenait avant, et c'est voulu.

    Le diagnostic ne se supprime pas depuis le panneau -- la route repond
    403. Un registre sans lui n'est donc pas un choix de l'utilisateur : c'est
    un etat casse, et le seul geste utile est de le reparer.
    """
    _amorcage(tmp_path, monkeypatch)
    app.amorcer_projet("diagnostic")
    app.save({})
    assert app.amorcer_projet("diagnostic") == "diagnostic"
    assert "diagnostic" in app.load()




def test_une_installation_deja_en_service_le_recoit_aussi(tmp_path, monkeypatch):
    """Le chemin qui privait une installation entiere de diagnostic.

    L'inscription ne se declenchait que sur un registre VIDE. Une stack qui
    avait declare une application avant que dagster ait depose le projet --
    les deux conteneurs demarrent en parallele -- ne le recevait jamais. Ni
    au redemarrage suivant, ni aucun autre.
    """
    _amorcage(tmp_path, monkeypatch)
    app.save({"mon-site": {"port": 9101}})
    assert app.amorcer_projet("diagnostic") == "diagnostic"
    apres = app.load()
    assert "diagnostic" in apres
    # Et l'application qui etait la n'a pas bouge.
    assert apres["mon-site"]["port"] == 9101




def test_une_inscription_deja_faite_n_est_pas_recreee(tmp_path, monkeypatch):
    """Inscrire n'est pas ecraser : le port attribue ne doit pas changer.

    Une application dont le port change a chaque demarrage du panneau, c'est
    une adresse qu'on ne peut pas noter.
    """
    _amorcage(tmp_path, monkeypatch)
    app.amorcer_projet("diagnostic")
    port = app.load()["diagnostic"]["port"]
    assert app.amorcer_projet("diagnostic") is None
    assert app.load()["diagnostic"]["port"] == port




def test_une_configuration_derivee_est_remise_droite(tmp_path, monkeypatch):
    """Ce que le panneau remet, et ce qu'il laisse.

    Le chemin, la commande et le build ne sont pas configurables pour cette
    application-la : les laisser deriver n'apporte rien et prive du
    diagnostic. Le reste -- ce que l'utilisateur PEUT changer, comme la
    visibilite -- n'est pas touche.
    """
    racine = _amorcage(tmp_path, monkeypatch)
    app.amorcer_projet("diagnostic")
    apps = app.load()
    apps["diagnostic"]["command"] = "sleep 1"
    apps["diagnostic"]["build_command"] = ""
    apps["diagnostic"]["visibility"] = app.VISIBILITE_PUBLIQUE
    app.save(apps)

    app.amorcer_projet("diagnostic")

    remis = app.load()["diagnostic"]
    assert remis["command"] == app.COMMANDE_PAR_DEFAUT
    assert remis["build_command"] == app.BUILD_PAR_DEFAUT
    assert remis["path"] == str(racine / "diagnostic")
    assert remis["visibility"] == app.VISIBILITE_PUBLIQUE




def test_un_projet_pas_encore_amorce_est_retente_au_demarrage_suivant(tmp_path, monkeypatch):
    """app-manager et dagster demarrent en parallele, et c'est dagster qui
    depose le projet dans /workspace : au premier demarrage le dossier peut
    ne pas encore exister. On ne peut pas inscrire un chemin absent -- mais
    renoncer DEFINITIVEMENT, comme avant, privait du projet pour une simple
    question d'ordre de demarrage."""
    racine = _amorcage(tmp_path, monkeypatch, avec_projet=False)
    assert app.amorcer_projet("diagnostic") is None
    assert app.load() == {}

    (racine / "diagnostic").mkdir()
    (racine / "diagnostic" / "app.py").write_text("")
    assert app.amorcer_projet("diagnostic") == "diagnostic"




# ---------- la verification approfondie, en direct ----------

def test_la_verification_annonce_ses_lignes_avant_de_les_jouer():
    """Elle jouait tout avant de repondre : on cliquait, et le navigateur
    restait blanc jusqu'a trois minutes -- le temps de la suite de
    regressions -- sans dire ou il en etait, ni s'il avancait encore."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert "/api/test/<int:indice>" in src, "un test a la fois, sinon rien n'avance"
    # Les lignes sont posees AVANT d'etre jouees, chacune « en attente ».
    assert 'class="st attente"><span>en attente</span>' in src
    # La roue avance quand un test FINIT : pas d'animation qui tourne dans le
    # vide et laisse croire que ca progresse.
    assert "roue(r + 1, liste.length)" in src
    roue = src.split("function roue(fait, total){")[1].split("\n}")[0]
    assert "strokeDashoffset" in roue and "fait / total" in roue




def test_la_verification_se_lance_sur_tout_ou_sur_une_rubrique():
    """« Le proxy ne sert plus cette application » ne demande pas de rejouer
    trois minutes de tests de base : on relance le SUJET qu'on vient de voir
    en echec."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    # Le bouton global n'a pas de sujet, celui d'une rubrique porte sa cle.
    assert 'data-verifier=""' in src, "le bouton global a disparu"
    assert 'data-verifier="{esc(cle)}"' in src, (
        "chaque rubrique doit pouvoir se verifier seule")
    choix = src.split("function choisis(cle, avecSuite){")[1].split("\n}")[0]
    assert "cle === null || t.theme === cle" in choix
    # La suite de regressions reste optionnelle, quelle que soit la rubrique.
    assert "avecSuite || t.theme !== 'code'" in choix




def test_la_verification_se_joue_dans_une_fenetre_posee_des_le_depart():
    """Une fenetre construite en JavaScript au moment du clic n'existe pas
    tant qu'on n'a pas clique : elle ne se teste pas, et elle ne se lit
    pas."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert 'id="voile"' in src and 'aria-modal="true"' in src
    # Elle se ferme de trois facons : la croix, le voile, la touche Echap.
    for geste in ("$('fermer').addEventListener('click', fermer)",
                  "e.target === $('voile')", "e.key === 'Escape'"):
        assert geste in src, geste
    # Mais jamais pendant qu'une verification tourne : fermer ne l'arrete
    # pas, et la rouvrir montrerait une fenetre qui avance toute seule.
    fermer = src.split("function fermer(){")[1].split("\n}")[0]
    assert "if(enCours) return;" in fermer




def test_les_sondes_sont_rangees_par_thematique():
    """Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
    qu'on cherche. Rangees, ce sont cinq questions distinctes."""
    groupes = sondes_par_theme(run_all())
    assert groupes, "aucune rubrique"
    cles = [cle for cle, _, _, _ in groupes]
    assert "autres" not in cles, (
        "une sonde n'a pas de rubrique : ajoute-la a THEME_DES_SONDES")
    # Chaque sonde apparait une fois et une seule.
    rangees = [nom for _, _, _, sondes in groupes for _, nom, _ in sondes]
    attendues = [nom for _, nom, _ in run_all()]
    assert sorted(rangees) == sorted(attendues)
    # Et chaque test approfondi aussi.
    par_theme = indices_par_rubrique()
    assert "autres" not in par_theme, (
        "un test approfondi n'a pas de rubrique : ajoute-le a THEME_DES_TESTS")
    rangs = sorted(i for indices in par_theme.values() for i in indices)
    assert rangs == list(range(len(noms_des_tests())))




def test_un_indice_de_test_hors_liste_ne_casse_pas_la_page():
    """Le rang vient de l'URL : il se borne, il ne se croit pas. Une
    IndexError rendrait une page d'erreur au milieu d'une verification."""
    import app as diag
    client = diag.app.test_client()
    for mauvais in ("99", "-1"):
        r = client.get(f"/api/test/{mauvais}")
        assert r.status_code in (404, 400), mauvais




def test_les_noms_annonces_sont_ceux_des_resultats():
    """La page annonce ses lignes puis les remplit : une ligne qui change
    d'intitule en cours de route n'est plus la meme ligne."""
    noms = checks_noms()
    assert len(noms) == len(TESTS_APPROFONDIS) + 1
    assert noms[-1] == NOM_SUITE_PANNEAU
    # run_test rend le nom de la LISTE, pas celui que la fonction se donne.
    src = inspect.getsource(run_test)
    assert "return etat, nom, detail" in src




def test_la_page_d_etat_range_ses_sondes_en_rubriques():
    """Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
    qu'on cherche. Une carte par sujet, son etat en pastille, et son propre
    bouton : on lit la rubrique qui cloche, et on approfondit celle-la."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert "checks.sondes_par_theme(resultats)" in src, (
        "la page doit prendre le regroupement du module, pas s'en inventer un")
    bloc = src.split("def _rubrique(")[1].split("\n\n\n")[0]
    # Chaque rubrique porte son compte : « 4 sur 7 » se lit sans compter les
    # lignes, et la couleur dit tout de suite si l'on doit s'y arreter.
    assert "passees} sur {total}" in bloc
    assert "checks.pire(resultats).classe" in bloc, (
        "la pastille doit prendre la couleur du pire rang de la rubrique : "
        "une rubrique qui ne porte qu'une alerte est orange, pas rouge")
    # Et son bouton, qui ne vaut que pour elle.
    assert 'data-verifier="{esc(cle)}"' in bloc




def test_la_page_d_etat_partage_un_seul_gabarit():
    """Deux gabarits divergent au premier changement, et le diagnostic se
    mettrait a ressembler a deux applications selon la page ouverte."""
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert src.count("<!doctype") == 1, (
        "un seul gabarit : les autres pages passent par page()")
    # L'ancienne adresse /tests continue de mener a la verification : le lien
    # a circule, il est dans les favoris et dans les journaux.
    assert '@app.get("/tests")' in src
    assert "ouvrir_verification=True" in src




def test_le_diagnostic_suit_le_theme_du_panneau():
    """Il CHARGE le theme, il ne le recopie pas.

    Une palette recopiee dans une deuxieme page est une palette qui
    divergera, et le diagnostic finirait par annoncer une stack saine dans
    des couleurs qui ne sont plus celles de la stack.
    """
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    assert 'href="/theme.css"' in src, "le diagnostic ne charge pas le theme partage"
    assert "codelab-theme" in src, "le choix clair / sombre n'est pas repris"
    # Il arrive par un COOKIE : le localStorage du panneau appartient a une
    # autre origine, et n'est pas lisible d'ici -- il ne doit pas l'etre.
    assert "document.cookie.match" in src, (
        "sans le cookie, le diagnostic retombe sur le reglage du systeme")
    panneau = _page_panneau("dashboard.html")
    assert "function noterThemePartage(t){" in panneau, (
        "le panneau ne partage pas son choix : personne ne pose le cookie")
    bloc = panneau.split("function setTheme(t){")[1].split("\n}")[0]
    assert "noterThemePartage(t)" in bloc
    # Pose aussi au chargement : une installation en place ne doit pas avoir
    # a refaire son choix pour que les applications le voient.
    assert "noterThemePartage(themeChoisi)" in panneau
    # Aucune couleur en dur ne doit revenir dans sa feuille de style.
    css = src[src.index("CSS = "):src.index('"""', src.index("CSS = ") + 10)]
    assert "#" not in css.replace("#{", ""), (
        "une couleur en dur est revenue dans le CSS du diagnostic")




def test_le_theme_traverse_la_separation_des_origines():
    """C'etait LA cause du « tres mauvais rendu » du diagnostic.

    La garde qui separe les deux ports ne laissait passer que le proxy et la
    sonde de sante. Une application hebergee qui demandait "/theme.css" --
    le diagnostic le fait a chaque page -- recevait une page 404 a la place
    de la feuille, et s'affichait alors SANS UN SEUL JETON : fond blanc,
    texte brut, pastilles invisibles. Rien dans son propre CSS n'etait en
    cause, et c'est bien pour cela que le defaut a tenu.

    Les deux routes sont deja publiques sur le port du panneau -- la page de
    connexion en a besoin avant toute session. Les ouvrir ici ne revele donc
    rien de plus : des couleurs, et un fichier de police.
    """
    if app is None:
        pytest.skip("panneau absent")
    ancien = app._origines_separees["actif"]
    ancien_apps = app.APPS_PORT
    app._origines_separees["actif"] = True
    app.APPS_PORT = 9002
    try:
        client = app.flask_app.test_client()
        for chemin, type_attendu in (("/theme.css", "text/css"),
                                     ("/polices/manrope-latin.woff2", "font/woff2")):
            r = client.get(chemin, environ_overrides={"SERVER_PORT": "9002"})
            assert r.status_code == 200, f"{chemin} ne passe pas la garde"
            assert type_attendu in r.headers["Content-Type"], chemin
        # Et le panneau, lui, ne s'affiche toujours pas sur ce port : c'est
        # tout l'interet de la separation, et elle ne doit pas s'ouvrir avec.
        for chemin in ("/", "/api/mes-apps", "/login"):
            r = client.get(chemin, environ_overrides={"SERVER_PORT": "9002"})
            assert r.status_code == 404, f"{chemin} repond sur le port des apps"
    finally:
        app._origines_separees["actif"] = ancien
        app.APPS_PORT = ancien_apps




def test_le_diagnostic_sert_son_theme_lui_meme():
    """Le second chemin, celui qui ne depend d'aucune garde d'origine.

    La page charge le theme par deux liens vers le MEME fichier : celui du
    panneau, a la racine de l'origine des applications, et un lien RELATIF
    servi par cette route-ci. Il faudrait que les deux tombent pour qu'une
    page nue revienne.

    Le fichier n'est pas recopie : il est lu la ou il est. Une palette
    recopiee dans une deuxieme page est une palette qui divergera.
    """
    import app as diag
    if not diag.fichier_du_theme():
        pytest.skip("le theme du panneau n'est pas sur ce disque")
    client = diag.app.test_client()
    r = client.get("/theme.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["Content-Type"]
    # C'est bien la feuille du panneau, avec ses jetons.
    for jeton in (b"--accent", b"--ok", b"--warn", b"--err", b"--surface"):
        assert jeton in r.data, jeton
    # La police que cette feuille reclame, prise a cote d'elle.
    assert client.get("/polices/manrope-latin.woff2").status_code == 200
    # Et rien d'autre : sans liste blanche, ce chemin deviendrait une lecture
    # de fichier arbitraire.
    for mauvais in ("theme.css", "LICENCE-manrope.txt", "manrope-latin.woff2.bak"):
        assert client.get("/polices/" + mauvais).status_code == 404, mauvais




def test_la_page_d_accueil_est_un_cockpit():
    """Elle repond d'abord a « est-ce que tout va bien ? ».

    Un anneau pour la part de sondes vertes, le mot d'etat, les quatre rangs
    comptes, et le bouton qui lance tout. Une liste de seize lignes ne
    repond a cette question qu'apres l'avoir entierement lue.
    """
    import app as diag
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    # Le denominateur de l'anneau ecarte les sondes SANS OBJET : un anneau
    # qui n'arrive jamais au bout parce que trois sondes ne s'appliquent pas
    # ici fait chercher une panne qui n'existe pas.
    bloc = src.split("def _anneau(")[1].split("\n\n\n")[0]
    assert "verts / applicables" in bloc
    assert "len(resultats) - len(sans_objet)" in src

    html = diag.app.test_client().get("/").data.decode()
    for morceau in ('class="cockpit', 'class="anneau', 'class="etat-mot',
                    'class="compteur', 'data-verifier=""', 'class="grille"'):
        assert morceau in html, morceau
    # Les quatre rangs, nommes. Un rang a zero reste affiche : « aucune
    # erreur » est une information.
    for libelle in ("erreurs", "attention", "au vert", "sans objet"):
        assert f"<span>{libelle}</span>" in html, libelle
    # Le theme arrive par les deux chemins.
    assert 'href="/theme.css"' in html and 'href="theme.css"' in html
    # Et la barre du panneau coiffe la page, sans trait vertical a cote de
    # la marque.
    assert 'class="barre"' in html and "CodeLab" in html




def test_la_page_suit_la_forme_de_cleanmymac():
    """« Mettre a jour l'application Diagnostic pour qu'elle reprenne le style
    de CleanMyMac » : une colonne de rubriques a gauche, chacune marquee de
    son pire etat, et une seule vue a la fois au centre.

    Chaque entree de la colonne doit designer une vue qui existe : une
    entree sans vue ne montrerait rien au clic.
    """
    import re
    import app as diag
    html = diag.app.test_client().get("/").data.decode()
    assert 'class="cote verre"' in html and 'class="scene"' in html
    assert 'class="analyser"' in html and 'data-verifier=""' in html
    vues = re.findall(r'data-vue="([a-z]+)"', html)
    assert vues[0] == "accueil" and len(vues) > 1, vues
    for cle in vues:
        assert f'data-vue-de="{cle}"' in html, f"la vue {cle} n'existe pas"
    # Le point de chaque rubrique porte un rang connu, jamais une couleur.
    points = re.findall(r'class="point ([a-z]+)"', html)
    assert len(points) == len(vues) - 1
    assert set(points) <= {"ok", "warn", "ko", "neutre"}, points




def test_le_detail_d_une_sonde_n_est_plus_en_chasse_fixe():
    """Une phrase entiere en monospace se lit deux fois moins vite.

    Et toutes les lignes de cette page en etaient : le detail d'une sonde
    est une PHRASE, pas une valeur. La chasse fixe ne reste que pour ce qui
    est vraiment du code -- un chemin, un port, un extrait de configuration,
    dans une balise <code>.
    """
    src = open(os.path.join(DOSSIER_DIAGNOSTIC, "app.py"),
               encoding="utf-8").read()
    css = src[src.index("CSS = "):src.index('"""', src.index("CSS = ") + 10)]
    detail = css.split(".det{")[1].split("}")[0]
    assert "--mono" not in detail, (
        "le detail des sondes est revenu en chasse fixe")
    assert "--mono" in css.split("code{")[1].split("}")[0], (
        "plus rien n'est en chasse fixe, pas meme un chemin")




# ---------- 27. le projet de diagnostic ne se supprime pas ----------
#
# C'est l'etat des lieux de l'installation : il dit si les services se
# parlent, si le panneau est ferme, si les applications sont isolees. Une
# stack sans lui n'a plus aucun moyen de se controler elle-meme -- et comme
# son inscription n'a lieu qu'UNE fois (marqueur), le supprimer le ferait
# disparaitre pour de bon, pas jusqu'au prochain redemarrage.

def test_le_diagnostic_ne_se_supprime_pas(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "admin", "password": "secret-de-test"})
    app.save({app.DIAGNOSTIC_NOM: {"path": "/w/d", "command": "x", "port": 9100},
              "autre": {"path": "/w/a", "command": "x", "port": 9101}})

    r = c.delete("/api/app/" + app.DIAGNOSTIC_NOM)
    assert r.status_code == 403
    assert app.DIAGNOSTIC_NOM in app.load(), "le diagnostic a ete supprime"

    # Temoin : une autre application se supprime normalement -- sinon ce test
    # passerait au vert parce que la suppression est cassee pour tout le monde.
    assert c.delete("/api/app/autre").status_code == 200
    assert "autre" not in app.load()




def test_le_diagnostic_voit_l_ensemble_du_workspace(monkeypatch):
    """L'observateur est la seule application non isolee, et c'est sa raison
    d'etre : isole, il ne verrait ni /workspace/definitions.py ni les autres
    projets, et rapporterait une stack en panne alors que tout va bien."""
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
    # Verdict fixe : ce test porte sur le branchement, pas sur le
    # noyau de la machine qui lance la suite.
    app._isolement.update(verdict=True, raison="")
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    argv, _ = app.commande_isolee(app.DIAGNOSTIC_NOM, "/workspace/diagnostic", "x", {})
    assert argv[0] == "bash", "le diagnostic est isole : il deviendrait aveugle"
    # Temoin : n'importe quelle autre application, elle, est bien isolee.
    argv, _ = app.commande_isolee("autre", "/workspace/autre", "x", {})
    assert argv[0] == "unshare"




# ---------- 28. la hierarchie des sondes de diagnostic ----------
#
# Releve dans les journaux Dagster du 15 septembre : six sondes en ECHEC sur
# dix-sept, un run rouge, un mail -- et la lecture du detail donnait ceci.
#
#   credentials.env          le secret n'etait pas arrive dans le conteneur
#   Postgres                 consequence directe du precedent
#   panneau ferme            127.0.0.1:9001 depuis codelab-dagster : personne
#   origine des applications 127.0.0.1:9002, meme erreur d'adresse
#   isolation                refus du noyau de l'HOTE, rien a corriger ici
#   espace disque            87 %, 2.1 Go libres -- des jours de marge
#
# Deux vrais problemes, deux mauvaises adresses et deux points a regarder
# quand on passe : tous au meme rang, avec le meme rouge et le meme mail.
# C'est ainsi qu'on cesse de lire ses alertes, et qu'on rate la prochaine
# vraie panne.

def test_chaque_sonde_declare_son_rang(monkeypatch):
    """La table est la hierarchie : une sonde qui n'y figure pas peut faire
    tomber un run sans que personne ne l'ait decide."""
    # Les sondes reseau sont jouees pour de vrai, elles echouent ici, et
    # c'est tres bien : ce qu'on verifie est le NOM de chaque ligne.
    noms = [nom for _etat, nom, _detail in run_all()]
    assert len(noms) == len(set(noms)), "deux sondes portent le meme nom"
    manquantes = [n for n in noms if n not in SEVERITE_MAX]
    assert not manquantes, (
        f"sondes absentes de SEVERITE_MAX : {manquantes} -- ajoute-les, avec "
        f"le rang que tu leur donnes")
    en_trop = [n for n in SEVERITE_MAX if n not in noms]
    assert not en_trop, f"SEVERITE_MAX parle de sondes disparues : {en_trop}"




def test_une_sonde_de_confort_ne_peut_pas_faire_tomber_un_run():
    """Le filet, teste tel quel : meme si une sonde de rang 2 renvoyait un
    echec -- demain, par inadvertance -- elle ne reveille personne."""
    etat, _, _ = _hierarchiser((Etat.ECHEC, "isolation des applications", "x"))
    assert etat is Etat.ALERTE
    # Et le filet ne relache rien de ce qui compte.
    etat, _, _ = _hierarchiser((Etat.ECHEC, "Postgres", "x"))
    assert etat is Etat.ECHEC
    # Une sonde inconnue reste au rang le plus severe : on ne se tait pas sur
    # ce qu'on n'a pas encore classe.
    etat, _, _ = _hierarchiser((Etat.ECHEC, "sonde ajoutee demain", "x"))
    assert etat is Etat.ECHEC




def test_le_verdict_global_est_le_pire_des_rangs():
    assert pire([(Etat.OK, "a", ""), (Etat.ALERTE, "b", "")]) is Etat.ALERTE
    assert pire([(Etat.ALERTE, "a", ""), (Etat.ECHEC, "b", "")]) is Etat.ECHEC
    assert pire([(Etat.OK, "a", ""), (Etat.SANS_OBJET, "b", "")]) is Etat.SANS_OBJET
    assert pire([]) is Etat.OK
    # ECHEC est faux, les trois autres sont vrais : tout le code qui lisait
    # "ok" continue de le lire, avec le bon sens.
    assert not Etat.ECHEC
    assert Etat.ALERTE and Etat.SANS_OBJET and Etat.OK


# ---------- 28 bis. le secret Postgres, la ou l'entrypoint l'a pose ----------
#
# LA panne de ces journaux, et la seule qui demandait un geste. Dans
# codelab-dagster, credentials.env est en 0600 root et Dagster tourne sous
# l'uid 1002 : le fichier est illisible, c'est prevu, et l'entrypoint exporte
# la valeur en DAGSTER_PG_PASSWORD avant d'abandonner root. checks.py ne
# lisait que POSTGRES_PASSWORD : le secret etait dans l'environnement du
# processus, a portee de main, et la sonde repondait "fe_sendauth: no
# password supplied" quatre fois par heure.




def test_le_secret_est_lu_la_ou_l_entrypoint_l_a_pose(config_isolee, monkeypatch):
    monkeypatch.setenv("DAGSTER_PG_PASSWORD", "mot-de-passe-dagster")
    assert mot_de_passe_pg() == ("mot-de-passe-dagster", "DAGSTER_PG_PASSWORD")
    assert pg_settings()["password"] == "mot-de-passe-dagster"

    etat, _nom, detail = check_config()
    assert etat is Etat.OK, detail
    # Et la sonde dit PAR OU il est arrive : "illisible ici" et "personne ne
    # l'a transmis" se depannent de deux facons opposees.
    assert "DAGSTER_PG_PASSWORD" in detail




def test_une_session_ssh_lit_le_secret_de_son_profil(config_isolee, monkeypatch):
    """Meme mecanique dans codelab-dev : l'entrypoint pre-remplit PGPASSWORD
    dans le profil, et le fichier reste illisible sous l'uid 1000."""
    monkeypatch.setenv("PGPASSWORD", "mot-de-passe-ssh")
    assert pg_settings()["password"] == "mot-de-passe-ssh"




def test_le_fichier_reste_prioritaire(config_isolee, monkeypatch):
    """Une valeur perimee dans l'environnement ne doit pas gagner contre le
    fichier partage, qui est la source de verite."""
    config_isolee.write_text("POSTGRES_PASSWORD=celui-du-fichier\n")
    monkeypatch.setenv("DAGSTER_PG_PASSWORD", "un-autre")
    assert pg_settings()["password"] == "celui-du-fichier"
    _etat, _nom, detail = check_config()
    assert "lu dans" in detail




def test_sans_aucun_secret_la_sonde_nomme_les_trois_chemins(config_isolee):
    etat, _nom, detail = check_config()
    assert etat is Etat.ECHEC
    for cle in CLES_MOT_DE_PASSE_PG:
        assert cle in detail, "la sonde ne dit pas ou le secret aurait pu passer"


# ---------- 28 ter. deux paliers pour le disque ----------




def test_un_disque_qui_se_remplit_est_une_alerte(tmp_path, monkeypatch):
    """87 %, 2.1 Go libres : exactement la ligne du journal du 15 septembre.
    Il reste des jours -- cela se traite dans la journee, pas la nuit."""
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path))
    _faux_disque(monkeypatch, 87, 2.1)
    etat, _nom, detail = check_espace_disque()
    assert etat is Etat.ALERTE, detail
    assert "87 %" in detail




def test_un_disque_presque_plein_est_un_echec(tmp_path, monkeypatch):
    """La prochaine ecriture de Postgres echoue, et son message ne parlera
    jamais d'espace disque."""
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path))
    _faux_disque(monkeypatch, 97, 0.4)
    etat, _nom, detail = check_espace_disque()
    assert etat is Etat.ECHEC, detail
    assert SEUIL_DISQUE_CRITIQUE == 95 and SEUIL_LIBRE_CRITIQUE_GO == 1




def test_un_grand_disque_bien_rempli_ne_dit_rien(tmp_path, monkeypatch):
    """89 % de 250 Go laisse 28 Go : des mois de marge, aucune alerte."""
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path))
    _faux_disque(monkeypatch, 89, 28)
    etat, _nom, _detail = check_espace_disque()
    assert etat is Etat.OK




# ---------- 28 quater. ce qui ne se regarde pas d'ici ----------
#
# L'etat du panneau n'est monte que dans codelab-app-manager. Depuis Dagster,
# quatre sondes y trouvaient un panneau vide -- zero application, zero compte,
# rien d'expose -- et affichaient un vert rassurant sur une question qu'elles
# n'avaient jamais pu poser. Un faux OK est pire qu'une case vide : il fait
# cesser de chercher.

def test_les_sondes_du_panneau_ne_repondent_pas_depuis_ailleurs(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path / "jamais-monte"))
    for fonction in (check_applications, check_surface_exposee,
                     check_provenance, check_exposition):
        etat, nom, detail = fonction()
        assert etat is Etat.SANS_OBJET, f"{nom} conclut sans rien avoir lu"
        assert "n'est pas monté dans ce conteneur" in detail




def test_l_etat_du_panneau_monte_les_sondes_repondent(tmp_path, monkeypatch):
    """Temoin : le meme code, avec le dossier sous les yeux, conclut bien --
    sinon le test ci-dessus passerait au vert parce que tout est casse."""
    etat_dir = tmp_path / "etat"
    etat_dir.mkdir()
    monkeypatch.setenv("APP_MANAGER_STATE", str(etat_dir))
    for fonction in (check_applications, check_surface_exposee, check_provenance):
        etat, nom, _detail = fonction()
        assert etat is Etat.OK, nom




# ---------- 28 quinquies. le panneau n'est pas toujours sur 127.0.0.1 ----------

def test_la_sonde_cherche_le_panneau_par_son_nom_de_service(monkeypatch):
    monkeypatch.delenv("CODELAB_PANNEAU_HOTE", raising=False)
    assert _hotes_panneau() == ("127.0.0.1", "codelab-app-manager")
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "autre-hote")
    assert _hotes_panneau() == ("autre-hote",)




def test_un_panneau_ferme_est_un_ok(faux_panneau):
    etat, _nom, detail = check_panneau_ferme()
    assert etat is Etat.OK, detail




def test_une_route_d_administration_ouverte_est_un_echec(faux_panneau):
    """La seule chose qui justifie un run rouge ici : une breche CONSTATEE."""
    faux_panneau["/api/utilisateurs"] = 200
    etat, _nom, detail = check_panneau_ferme()
    assert etat is Etat.ECHEC
    assert "/api/utilisateurs" in detail




def test_un_panneau_injoignable_est_une_alerte(monkeypatch):
    """Une absence de preuve n'est pas une preuve. C'est ce que les journaux
    du 15 septembre montraient : deux ECHEC pour une adresse mal choisie."""
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "127.0.0.1")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        libre = s.getsockname()[1]      # un port ou plus personne n'ecoute
    monkeypatch.setenv("MANAGER_PORT", str(libre))
    monkeypatch.setenv("APP_MANAGER_APPS_PORT", str(libre))
    for fonction in (check_panneau_ferme, check_origine_applications):
        etat, nom, _detail = fonction()
        assert etat is Etat.ALERTE, nom
    # Le panneau, lui, est bien declare injoignable : la panne est dite une
    # fois, par la sonde dont c'est le travail.
    etat, _nom, detail = check_panneau_joignable()
    assert etat is Etat.ECHEC and "docker logs" in detail




def test_le_panneau_joignable_par_son_nom_de_service(faux_panneau):
    etat, _nom, detail = check_panneau_joignable()
    assert etat is Etat.OK, detail




# ---------- 28 sexies. l'isolement refuse n'est plus une panne ----------

def test_isoler_a_zero_est_un_choix_assume(monkeypatch):
    """La sonde promet « pour assumer le choix et faire taire cette sonde :
    APP_MANAGER_ISOLER=0 ». Elle tenait si peu parole qu'elle continuait a
    marquer l'installation en faute une fois la variable posee."""
    monkeypatch.setenv("APP_MANAGER_ISOLER", "0")
    etat, _nom, detail = check_isolation()
    assert etat is Etat.OK
    assert "volontairement" in detail


# ---------- 28 septies. l'asset Dagster, et ce qui fait tomber un run ----------
#
# C'est la decision qui compte : le capteur alerte_mail_echec se declenche sur
# un run EN ECHEC. Ce qui fait echouer le run fait donc partir le mail -- et
# un mail toutes les heures pour un disque a 87 % est un mail qu'on
# apprend a ne plus ouvrir.
#
# definitions.py importe dagster, absent de cette image (et de celle du
# panneau). On le remplace par un module d'emprunt : l'asset n'utilise aucune
# API de Dagster pour decider, seulement les rangs -- c'est precisement ce
# qu'on veut verifier.




def test_une_alerte_ne_fait_pas_echouer_le_run(asset_diagnostic, monkeypatch):
    """LE reglage demande : le disque a 87 % et l'isolement refuse par l'hote
    se journalisent en warning, et le run reste vert."""
    _sondes(monkeypatch, [
        (Etat.OK, "Postgres", "connecte"),
        (Etat.ALERTE, "espace disque", "/workspace : 87 % occupe, 2.1 Go libres"),
        (Etat.ALERTE, "isolation des applications", "le noyau refuse"),
        (Etat.SANS_OBJET, "surface exposée", "pas monté ici"),
    ])
    contexte = _ContexteDeTest()

    resultat = asset_diagnostic.diagnostic_codelab(contexte)

    assert "alertes" in resultat
    # Chaque rang dans le bon canal : c'est ce que lit la page des runs. On
    # vise la ligne de la SONDE ("nom -- detail"), pas le resume final, qui
    # les cite toutes.
    assert contexte.niveaux("espace disque -- ") == ["warning"]
    assert contexte.niveaux("Postgres -- ") == ["info"]
    assert contexte.niveaux("surface exposée -- ") == ["info"]
    assert contexte.metadonnees["critiques"] == 0
    assert contexte.metadonnees["alertes"] == 2
    assert contexte.metadonnees["sans objet"] == 1
    # Et le battement de coeur porte les alertes : la page du panneau les
    # relit sans avoir a rejouer les sondes.
    assert "alertes :" in asset_diagnostic.ecrit["detail"]
    assert asset_diagnostic.ecrit["fermee"] is True




def test_une_sonde_critique_fait_bien_echouer_le_run(asset_diagnostic, monkeypatch):
    """Temoin : sans lui, le test precedent passerait au vert parce que plus
    rien ne fait jamais echouer quoi que ce soit."""
    _sondes(monkeypatch, [
        (Etat.ECHEC, "Postgres", "connexion refusee"),
        (Etat.ALERTE, "espace disque", "87 %"),
    ])
    contexte = _ContexteDeTest()
    with pytest.raises(RuntimeError) as leve:
        asset_diagnostic.diagnostic_codelab(contexte)
    assert "Postgres" in str(leve.value)
    # L'alerte reste citee, mais apres : elle n'est pas la raison de l'echec.
    assert "espace disque" in str(leve.value)
    assert str(leve.value).index("Postgres") < str(leve.value).index("espace disque")
    assert contexte.niveaux("Postgres -- ") == ["error"]




def test_la_base_injoignable_ne_sort_plus_en_trace_nue(asset_diagnostic, monkeypatch):
    """L'exception de psycopg2 remplacait tout : quarante lignes de pile, et
    la cause -- dite deux lignes plus haut par la sonde credentials.env --
    disparaissait dessous."""
    _sondes(monkeypatch, [(Etat.OK, "Postgres", "connecte")])

    class _Refus(Exception):
        pass

    def _refuse(*_a, **_k):
        raise _Refus("no password supplied")

    monkeypatch.setattr(checks, "connect_pg", _refuse)
    contexte = _ContexteDeTest()
    with pytest.raises(RuntimeError) as leve:
        asset_diagnostic.diagnostic_codelab(contexte)

    assert "battement de coeur" in str(leve.value)
    # Chainee, pas perdue : la trace d'origine reste consultable dessous.
    assert isinstance(leve.value.__cause__, _Refus)
    # Et les sondes ont ete journalisees AVANT : c'est la ou se lit la cause.
    assert contexte.niveaux("Postgres -- ") == ["info"]




# ---------- 27. ce qui ne s'applique pas ----------------------------------
#
# Une sonde qui ne concerne pas une installation ne doit pas etre rouge :
# il n'y a rien a reparer. « Sans objet » est un troisieme etat, et il doit se propager
# jusqu'au compte affiche.

def test_une_sonde_sans_objet_ne_compte_ni_au_numerateur_ni_au_denominateur():
    """« 5 sur 11 » sur une installation ou tout va bien inquiete pour rien.

    L'anneau du cockpit et la pastille de chaque rubrique doivent raconter la
    meme chose : six sondes qui ne s'appliquent pas ici ne sont pas six
    sondes en attente.
    """
    import app as diag
    resultats = [
        (Etat.OK, "a", ""),
        (Etat.OK, "b", ""),
        (Etat.SANS_OBJET, "c", ""),
        (Etat.SANS_OBJET, "d", ""),
    ]
    html = diag._rubrique("services", "Services", "", resultats)
    assert "2 sur 2" in html, html
    # Et une rubrique entierement sans objet le dit avec des mots plutot que
    # d'afficher « 0 sur 0 ».
    html = diag._rubrique("services", "Services", "",
                          [(Etat.SANS_OBJET, "c", "")])
    assert "sans objet" in html
    assert "0 sur 0" not in html




def test_le_titre_du_cockpit_compte_les_memes_sondes_que_la_pastille():
    """« 8 vérifications en échec » au-dessus d'une pastille qui en montrait
    sept : le premier reflexe est de chercher la huitieme.

    L'erreur d'ecriture en base est deja portee par la sonde Postgres. La
    compter une seconde fois dans le titre faisait diverger les deux nombres
    des qu'elle survenait -- c'est-a-dire exactement quand on lit la page.
    """
    import app as diag
    source = (Path(diag.__file__).resolve()).read_text(encoding="utf-8")
    bloc = source.split("if critiques or erreur_db:")[1].split("elif alertes:")[0]
    assert "combien = len(critiques) or 1" in bloc
    assert "len(critiques) + (1 if erreur_db else 0)" not in bloc




def test_la_sonde_de_port_garde_un_cache_par_service(monkeypatch, tmp_path):
    """Un cache unique pour deux services rendait la reponse de l'un pour
    l'autre -- et le hub interroge les deux a chaque rafraichissement."""
    m = _charger_app_manager(monkeypatch, tmp_path)
    m._sondes_de_port.clear()
    appels = []

    class FauxSocket:
        def settimeout(self, _):
            pass

        def connect_ex(self, cible):
            appels.append(cible)
            return 0 if cible[1] == 3001 else 1

        def close(self):
            pass

    monkeypatch.setattr(m.socket, "socket", lambda *a, **k: FauxSocket())
    assert m._port_repond("a", 3001) is True
    assert m._port_repond("b", 3000) is False
    # Chacun son entree : deux appels, deux reponses distinctes.
    assert appels == [("a", 3001), ("b", 3000)]
    # Et le cache tient : le second passage ne rouvre pas de connexion.
    assert m._port_repond("a", 3001) is True
    assert len(appels) == 2




def test_les_graphiques_ecartent_les_services_que_le_panneau_ne_mesure_pas():
    """Le panneau n'a pas le pid d'un service externe : il ne mesure rien.

    Une barre a 0.0 % a cote des applications qu'il mesure vraiment se lit
    comme « ce service ne consomme rien ». C'est faux -- on ne sait pas.
    """
    page = _page_panneau("dashboard.html")
    bloc = page.split("function barChart(")[1].split("\n}")[0]
    assert "!a.externe" in bloc, (
        "les graphiques CPU et mémoire doivent ecarter les services externes")


# ---------- 37. la documentation ne ment pas ------------------------------
#
# Le depot tient sa documentation pour une source, pas pour un commentaire :
# les README expliquent POURQUOI chaque chose est ainsi, et quelqu'un les lit
# avant de toucher au code. Une documentation perimee est donc pire qu'une
# documentation absente -- elle fait prendre une decision fausse en toute
# confiance.
#
# Vecu a l'envers : le retrait de l'interface de chat a laisse dans
# un README une section entiere decrivant son portier, son reseau ferme
# et le secret partage -- trois mecanismes qui n'existaient plus. Le code
# etait juste, les tests verts, et la documentation racontait l'installation
# d'avant.




def test_le_readme_racine_liste_les_services_qui_existent():
    """Le tableau des services, confronte au compose.

    Un service ajoute sans sa ligne se decouvre en lisant le compose ; un
    service retire dont la ligne reste envoie chercher un conteneur qui
    n'existe pas.
    """
    texte = _readme_racine().lower()
    services = set(_services_du_compose(_composes()[0]))
    # Les noms tels qu'ils se lisent dans le tableau, sans le prefixe.
    for service in services:
        court = service.replace("codelab-", "")
        assert court in texte, (
            f"le service {service} n'apparait nulle part dans README.md")
    # Et l'inverse : rien qui ne tourne plus.
    for disparu in ("open-webui", "openwebui", "aichat"):
        for ligne in _readme_racine().splitlines():
            if ligne.startswith("|") and disparu in ligne.lower():
                raise AssertionError(
                    f"README.md decrit encore « {disparu} » comme un service")




def test_aucun_readme_ne_decrit_un_composant_retire():
    """Les mecanismes disparus ne doivent plus etre expliques au present.

    On cherche ce qui designe sans ambiguite l'ancienne installation. Les
    passages qui RACONTENT le retrait sont legitimes et restent : c'est la
    difference entre « l'interface vit sur un reseau ferme » et « l'interface
    a ete retiree ».
    """
    racine = _racine_depot()
    if not os.path.exists(os.path.join(racine, "README.md")):
        pytest.skip("depot complet absent de cette image")
    interdits = ("codelab-open-webui", "X-OpenWebUI-User-Id",
                 "CODELAB_OPENWEBUI_KEY", "/api/sso-check")
    fautes = []
    for dossier, _, fichiers in os.walk(racine):
        if ".git" in dossier:
            continue
        for nom in fichiers:
            if not nom.endswith(".md"):
                continue
            chemin = os.path.join(dossier, nom)
            texte = open(chemin, encoding="utf-8", errors="replace").read()
            for motif in interdits:
                if motif in texte:
                    fautes.append(f"{os.path.relpath(chemin, racine)} : {motif}")
    assert not fautes, "documentation perimee :\n  " + "\n  ".join(fautes)




def test_aucun_plafond_ne_descend_sous_le_plancher_de_demarrage():
    """Un plafond trop BAS casse le demarrage, et pas seulement la marche.

    Vecu : le plafond du proxy Dagster est passe a 96 Mo par souci
    d'economie, et Docker a refuse de CREER le conteneur -- « Minimum memory
    limit can not be less than memory reservation limit ». Toute la stack
    s'arretait la, sur 32 Mo economises.

    LA CAUSE, MESUREE. « docker compose config » sur la machine de
    reference montre ce que CasaOS/ZimaOS ajoute en recopiant le compose --
    a CHAQUE service, la meme valeur, absente de nos deux fichiers :

        deploy.resources.reservations.memory: "268435456"   (256 Mio)

    Docker refuse alors de creer un conteneur dont le plafond est sous cette
    reservation. Le proxy, plafonne a 96 Mo, etait le seul en dessous.

    Le plancher vaut donc pour TOUS les services, pas seulement le proxy :
    la reservation s'applique a chaque conteneur, et les autres passaient
    seulement parce qu'ils etaient deja au-dessus. Le jour ou quelqu'un
    economise 100 Mo sur n'importe lequel, c'est la stack entiere qui ne
    demarre plus -- et le message de Docker ne nomme pas le coupable.

    Les tests voisins tiennent le PLAFOND -- qu'aucun service ne prenne plus
    du tiers de la machine. Celui-ci tient le PLANCHER, l'autre facon de se
    tromper, dont rien ne protegeait.
    """
    for chemin in _composes():
        for nom, reglages in _services_du_compose(chemin).items():
            brut = str(reglages.get("mem_limit", reglages.get("deploy_memory")))
            mo = int(brut[:-1]) * 1024 if brut.endswith("g") else int(brut.rstrip("m"))
            assert mo >= RESERVATION_HOTE_MO, (
                f"{os.path.basename(chemin)} : {nom} est plafonne a {mo} Mo, "
                f"sous la reservation de {RESERVATION_HOTE_MO} Mio que l'hote "
                f"ajoute -- Docker refusera de creer ce conteneur")




def test_aucun_plafond_ne_depasse_la_machine_visee():
    """Un plafond au-dessus de la RAM disponible n'est pas une securite large.

    Vecu a l'envers : les valeurs visaient 8 Go et le panneau avait 4 Go de
    plafond. Sur un serveur de 4 Go, ce plafond ne bornait plus rien -- ce
    seul service pouvait emporter la machine, ce que ces lignes existent
    precisement pour empecher.

    La regle : aucun service ne prend a lui seul plus du TIERS de la machine.
    Il en reste alors assez pour que le panneau reponde et qu'on puisse
    arreter le fautif depuis le panneau -- ce qui etait impossible avant que
    ces limites existent.
    """
    maximum = RAM_CIBLE_MO // 3
    def en_mo(v):
        v = str(v)
        return int(v[:-1]) * 1024 if v.endswith("g") else int(v.rstrip("m"))
    for chemin in _composes():
        for nom, reglages in _services_du_compose(chemin).items():
            brut = reglages.get("mem_limit", reglages.get("deploy_memory"))
            assert brut, (chemin, nom)
            assert en_mo(brut) <= maximum, (
                f"{nom} : plafond de {en_mo(brut)} Mo, au-dela du tiers de la "
                f"machine visee ({maximum} Mo pour {RAM_CIBLE_MO} Mo)")




def test_aucun_plafond_processeur_ne_depasse_la_machine_visee():
    """Meme raisonnement pour le processeur.

    Un plafond de 2.0 sur un serveur bi-coeur ne limite rien : il autorise
    un service a prendre les deux coeurs, donc a rendre la machine sourde.
    Aucun ne depasse les trois quarts du total.
    """
    maximum = COEURS_CIBLE * 0.75
    for chemin in _composes():
        for nom, reglages in _services_du_compose(chemin).items():
            brut = reglages.get("cpus", reglages.get("deploy_cpus"))
            assert brut, (chemin, nom)
            part = float(str(brut).strip('"'))
            assert part <= maximum, (
                f"{nom} : {part} cpus, au-dela des trois quarts des "
                f"{COEURS_CIBLE} coeurs vises")




def test_le_compose_annonce_la_meme_cible_que_les_tests():
    """Le commentaire et le test doivent dire le meme chiffre.

    Un commentaire qui annonce 8 Go au-dessus de valeurs calculees pour 4 Go
    ferait remonter les plafonds a la premiere relecture, en toute bonne foi.

    Seul docker-compose.yml est concerne : c'est LUI qu'on lit et qu'on
    modifie. Son jumeau CasaOS ne porte aucun commentaire, pour la raison
    que dit le test suivant -- et les plafonds eux-memes y sont verifies
    chiffre par chiffre par les tests ci-dessus, pas par une phrase.
    """
    chemin = [c for c in _composes()
              if os.path.basename(c) == "docker-compose.yml"][0]
    texte = open(chemin, encoding="utf-8").read()
    assert f"CIBLE : {RAM_CIBLE_MO // 1024} Go et {COEURS_CIBLE} coeurs" in texte, (
        f"docker-compose.yml n'annonce pas la cible "
        f"{RAM_CIBLE_MO // 1024} Go / {COEURS_CIBLE} coeurs")




def test_le_dossier_des_projets_s_ecrit_une_seule_fois():
    """Vecu : « Point de montage /workspace commun a tous les services. Je
    peux changer le path [...], et tous les autres services pointent vers le
    nouveau path ». Le chemin hote vit dans x-codelab-workspace ; les trois
    services qui le montent le reprennent par l'ancre, jamais en dur."""
    import yaml
    for nom in ("docker-compose.yml", "docker-compose-casaos.yml"):
        texte = open(os.path.join(_racine_depot(), nom), encoding="utf-8").read()
        assert texte.count(":/workspace\n") == 1, f"{nom} : le chemin est recopie"
        assert texte.count("      - *workspace\n") == 3, nom
        d = yaml.safe_load(texte)
        attendu = d["x-codelab-workspace"]
        montes = {service for service, c in d["services"].items()
                  if attendu in (c.get("volumes") or [])}
        assert montes == {"codelab-dev", "codelab-dagster", "codelab-app-manager"}, nom




def test_le_compose_casaos_ne_porte_aucun_commentaire():
    """Ce fichier-la se colle dans une zone de texte, il ne se lit pas.

    La meme explication ecrite a deux endroits finit par diverger, et c'est
    la copie qu'on ne lit jamais qui se met a mentir. Ce jumeau ne porte donc
    que les valeurs ; tout ce qu'il y a a dire est dans docker-compose.yml,
    commente ligne a ligne, et dans les README.

    Le test tient la contrainte dans le sens ou elle se perd : on ajoute un
    commentaire par reflexe, on ne l'enleve jamais par hasard.
    """
    chemin = [c for c in _composes()
              if os.path.basename(c) == "docker-compose-casaos.yml"][0]
    fautives = [(n, l) for n, l in enumerate(
        open(chemin, encoding="utf-8").read().split("\n"), 1) if "#" in l]
    assert not fautives, ("docker-compose-casaos.yml porte un commentaire : "
                          + "; ".join(f"ligne {n} : {l.strip()}" for n, l in fautives))




# ---------- 38. les sondes et les tests restent separes ------------------
#
# Ce decoupage a une raison de production, pas de confort : checks.py est
# importe par app.py et par l'asset Dagster, dans des images qui n'embarquent
# pas pytest. Tant que les deux vivaient dans le meme fichier, il fallait un
# faux module pytest pour que les decorateurs de la suite s'evaluent a
# l'import -- et le jour ou ce repli manquait, l'asset de diagnostic
# disparaissait sans bruit des journaux Dagster.

def test_les_sondes_n_importent_pas_pytest():
    """checks.py doit rester lisible par une image sans pytest.

    On lit le fichier plutot que le module : un import reussi ici ne
    prouverait rien, puisque pytest est evidemment present quand cette suite
    tourne.
    """
    source = open(checks.__file__, encoding="utf-8").read()
    for ligne in source.splitlines():
        nue = ligne.strip()
        if nue.startswith("#") or '"' in nue or "'" in nue:
            continue
        assert not nue.startswith(("import pytest", "from pytest")), (
            f"checks.py importe pytest : {nue}")
    # Et aucun decorateur de test n'y subsiste.
    assert "@pytest." not in source
    assert "\ndef test_" not in source, (
        "un test a ete ecrit dans checks.py : il va dans tests/")




def test_les_tests_importent_les_sondes_et_jamais_l_inverse():
    """Le sens de la dependance, verifie dans les deux fichiers.

    checks.py qui importerait la suite ramenerait pytest dans les images de
    production par la bande -- exactement ce que la separation evite.
    """
    sondes = open(checks.__file__, encoding="utf-8").read()
    for motif in (r"^\s*(?:import|from)\s+(?:commun|conftest|test_)",
                  r"\bimportlib\b.*\b(?:commun|conftest)\b"):
        assert not re.search(motif, sondes, re.M), (
            "checks.py importe la suite : pytest reviendrait dans les images de production")
    assert sondes.count('"tests")') == 1, (
        "checks.py reference la suite ailleurs que pour la lancer")
    # Les tests, eux, chargent les sondes : une fois, pour tous, dans conftest.py.
    aides = open(os.path.join(DOSSIER_DIAGNOSTIC, "tests", "conftest.py"), encoding="utf-8").read()
    assert "\nimport checks" in aides




def test_la_ci_et_la_documentation_lancent_la_suite_au_bon_endroit():
    """Un chemin perime ici, et la CI ne teste plus rien en silence.

    C'est deja arrive une fois, autrement : cinq tests se sont ignores
    pendant des semaines parce qu'un chemin ne correspondait plus.
    """
    racine = _racine_depot()
    ci = os.path.join(racine, ".github", "workflows", "build-images.yml")
    if not os.path.exists(ci):
        pytest.skip("depot complet absent de cette image")
    texte = open(ci, encoding="utf-8").read()
    assert "pytest workspace/diagnostic/tests -q" in texte, (
        "la CI ne lance pas la suite au bon chemin")
    assert "pytest workspace/diagnostic/checks.py" not in texte
    assert "test_regressions.py" not in texte, "la CI vise l'ancien fichier de la suite"
    # La documentation donne la meme commande, sans l'ancien chemin.
    for doc in ("AGENTS.md", "README.md", os.path.join("app-manager", "README.md"),
                os.path.join("workspace", "diagnostic", "README.md")):
        contenu = open(os.path.join(racine, doc), encoding="utf-8").read()
        assert "test_regressions.py" not in contenu, f"{doc} cite l'ancien fichier"
        assert "pytest workspace/diagnostic/tests" in contenu, f"{doc} ne dit pas comment lancer la suite"




# -------------------------------- les pentests --------------------------------
#
# Vecu : « Assure-toi qu'aucune faille de securite n'est exploitable. Ajoute
# des pentests aux diagnostics. » Un pentest qui passe toujours ne prouve
# rien : chacun est joue contre le vrai panneau (il doit passer) ET contre un
# serveur troue expres (il doit echouer).

PENTESTS = [(nom, fn) for nom, fn in TESTS_APPROFONDIS if nom.startswith("Pentest")]


def _servir(monkeypatch, application):
    """Un vrai port, pour que les pentests fassent de vraies requetes."""
    import threading
    from werkzeug.serving import make_server
    serveur = make_server("127.0.0.1", 0, application, threaded=True)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    port = str(serveur.server_port)
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "127.0.0.1")
    monkeypatch.setenv("MANAGER_PORT", port)
    monkeypatch.setenv("APP_MANAGER_APPS_PORT", port)
    return serveur


def test_les_pentests_passent_contre_le_vrai_panneau(monkeypatch):
    """Le panneau tel qu'il est livre resiste a chaque attaque."""
    monkeypatch.setattr(app, "_login_attempts", {})
    serveur = _servir(monkeypatch, app.flask_app)
    try:
        for nom, fn in PENTESTS:
            etat, titre, detail = checks._essai(nom, fn)
            assert etat == Etat.OK, f"{nom} : {detail}"
    finally:
        serveur.shutdown()


def test_les_pentests_voient_un_panneau_troue(monkeypatch):
    """Un serveur qui laisse tout passer : chaque pentest doit le dire.

    Il repond 200 partout, sans en-tete de securite, renvoie l'hote recu
    dans sa redirection, laisse lire /etc/passwd, donne un cookie de
    tournage a qui le demande et ne limite jamais les essais."""
    def troue(environ, demarrer):
        hote = environ.get("HTTP_HOST", "")
        demarrer("200 OK", [("Content-Type", "text/plain"),
                            ("Location", f"http://{hote}/"),
                            ("Set-Cookie", "codelab-tournage=ouvert; Path=/")])
        return [b'root:x:0:0:root:/root:/bin/bash\n{"ok": true}']
    serveur = _servir(monkeypatch, troue)
    try:
        for nom, fn in PENTESTS:
            etat, _titre, detail = checks._essai(nom, fn)
            assert etat == Etat.ECHEC, f"{nom} n'a rien vu : {detail}"
    finally:
        serveur.shutdown()


def test_les_pentests_visent_de_vraies_routes():
    """Une route renommee laisserait le pentest frapper a une porte qui
    n'existe plus -- et compter son 404 comme un succes de la garde. Chaque
    route visee existe donc, avec sa methode."""
    carte = app.flask_app.url_map.bind("localhost")
    for methode, chemin in checks.ROUTES_ADMIN_PENTEST:
        carte.match(chemin, method=methode)          # leve NotFound / MethodNotAllowed
    # Et elles sont bien gardees cote administrateur dans le source.
    source = _source_du_panneau()
    for methode, chemin in checks.ROUTES_ADMIN_PENTEST:
        regle, _ = carte.match(chemin, method=methode, return_rule=True)
        vue = app.flask_app.view_functions[regle.endpoint]
        bloc = source[:source.index(f"def {vue.__name__}(")].rsplit("\n\n", 1)[-1]
        assert "@require_admin" in bloc, f"{methode} {chemin} n'est pas réservée à l'administrateur"

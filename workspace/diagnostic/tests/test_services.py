"""
CodeLab -- TESTS : LES SERVICES DE LA STACK
---------------------------------------------------------------------------
ROLE        Dagster, Postgres et pgvector, VS Code, Codex, l'outil codelab
            et son squelette.
CHARGE PAR  pytest : python3 -m pytest workspace/diagnostic/tests -q
            (depuis la racine du depot), la CI, et la page /tests
            du diagnostic.
CONTRAINTES Les aides, constantes et modules viennent de commun.py ; les
            fixtures, et le filet qui detourne TOUS les chemins d'etat
            avant chaque test, de conftest.py. Une aide nouvelle va dans
            commun.py, une fixture dans conftest.py, un test dans le
            fichier de son sujet. Aucun test ne lit /var/lib/codelab ni
            /workspace reels.
"""

from commun import *  # noqa: F401,F403 -- aides, constantes, modules


# ------------- "codelab new --ouvrir" rouvre la fenetre VS Code -----------
#
# La tache VS Code « CodeLab : nouveau projet » cree le projet PUIS demande a
# la fenetre de se rouvrir dessus. Ce qui se teste ici, c'est la partie
# fragile : la fenetre ne repond pas toujours, et surtout elle n'existe pas
# toujours -- une session SSH ordinaire n'en a aucune. Dans tous ces cas le
# projet doit rester cree et la commande sortir sans erreur : ouvrir est un
# confort, pas une etape du travail.
#
# Le vrai "code" du serveur VS Code ne peut pas tourner ici. On le remplace
# par un script qui note ce qu'on lui a demande : ce qu'on verifie, c'est
# l'appel emis, la ou le reste (la socket, la fenetre) appartient a VS Code.




def test_nouveau_projet_avec_ouvrir_demande_la_reouverture(tmp_path):
    chemin, trace = _faux_code(tmp_path)
    r, espace = _lancer_codelab(
        tmp_path, ["new", "facturier", "--ouvrir"],
        {"PATH": chemin + os.pathsep + os.environ["PATH"],
         "VSCODE_IPC_HOOK_CLI": "/tmp/une-socket-vscode.sock"})

    assert r.returncode == 0, r.stdout + r.stderr
    assert (espace / "facturier").is_dir(), "le projet doit exister"
    assert trace.exists(), ("aucune demande d'ouverture : " + r.stdout + r.stderr)
    demande = trace.read_text().strip()
    assert "--reuse-window" in demande, demande
    assert str(espace / "facturier") in demande, demande




def test_sans_ouvrir_la_fenetre_ne_bouge_pas(tmp_path):
    """Le drapeau doit etre la seule chose qui declenche l'ouverture : lancer
    la commande a la main dans un terminal ne doit pas faire sauter la vue."""
    chemin, trace = _faux_code(tmp_path)
    r, espace = _lancer_codelab(
        tmp_path, ["new", "facturier"],
        {"PATH": chemin + os.pathsep + os.environ["PATH"],
         "VSCODE_IPC_HOOK_CLI": "/tmp/une-socket-vscode.sock"})

    assert r.returncode == 0, r.stdout + r.stderr
    assert (espace / "facturier").is_dir()
    assert not trace.exists(), "la fenetre a bouge alors qu'on ne l'a pas demande"




def test_sans_fenetre_vscode_le_projet_est_quand_meme_cree(tmp_path):
    """Session SSH ordinaire : il n'y a aucune fenetre a qui parler. Ce n'est
    pas une erreur, et cela doit se dire."""
    chemin, trace = _faux_code(tmp_path)
    r, espace = _lancer_codelab(
        tmp_path, ["new", "facturier", "--ouvrir"],
        {"PATH": chemin + os.pathsep + os.environ["PATH"]})

    assert r.returncode == 0, r.stdout + r.stderr
    assert (espace / "facturier").is_dir()
    assert not trace.exists(), "rien ne devait etre demande sans socket"
    assert "session SSH simple" in r.stdout, r.stdout




def test_une_fenetre_qui_ne_repond_pas_ne_casse_pas_la_creation(tmp_path):
    """Le cas qui compte : le projet est deja sur le disque quand on tente
    d'ouvrir. Une commande qui sortirait en erreur ici laisserait croire que
    la creation a echoue."""
    chemin, trace = _faux_code(tmp_path, code=1)
    r, espace = _lancer_codelab(
        tmp_path, ["new", "facturier", "--ouvrir"],
        {"PATH": chemin + os.pathsep + os.environ["PATH"],
         "VSCODE_IPC_HOOK_CLI": "/tmp/une-socket-vscode.sock"})

    assert r.returncode == 0, (
        "le projet est cree : un echec d'ouverture ne doit pas faire echouer "
        "la commande\n" + r.stdout + r.stderr)
    assert (espace / "facturier").is_dir()
    assert trace.exists(), "l'ouverture devait avoir ete tentee"
    assert "a la main" in r.stdout, r.stdout




def test_code_est_retrouve_sous_vscode_server_hors_du_path(tmp_path):
    """Selon comment la tache est lancee, "code" n'est pas toujours dans le
    PATH. Le serveur VS Code le pose sous un dossier qui porte l'empreinte de
    sa version -- elle change a chaque mise a jour, donc on cherche le plus
    recent au lieu d'en figer un."""
    maison = tmp_path / "home"
    maison.mkdir(exist_ok=True)
    trace = tmp_path / "appel-code.txt"
    for empreinte, age in (("vieux0000", 100000), ("recent1111", 0)):
        d = maison / ".vscode-server" / "bin" / empreinte / "bin" / "remote-cli"
        d.mkdir(parents=True)
        outil = d / "code"
        outil.write_text("#!/bin/sh\n"
                         'printf "%s %s\\n" ' + '"' + empreinte + '" "$*" >> '
                         + '"' + str(trace) + '"\n' + "exit 0\n")
        outil.chmod(0o755)
        os.utime(outil, (time.time() - age, time.time() - age))

    # PATH volontairement ampute de "code" : c'est tout l'objet du test.
    vide = tmp_path / "_path-sans-code"
    vide.mkdir(exist_ok=True)
    r, espace = _lancer_codelab(
        tmp_path, ["new", "facturier", "--ouvrir"],
        {"PATH": str(vide) + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
         "VSCODE_IPC_HOOK_CLI": "/tmp/une-socket-vscode.sock"})

    assert r.returncode == 0, r.stdout + r.stderr
    assert (espace / "facturier").is_dir()
    assert trace.exists(), ("le code de .vscode-server n'a pas ete trouve : "
                            + r.stdout + r.stderr)
    assert "recent1111" in trace.read_text(), (
        "c'est le plus RECENT qu'il faut prendre : " + trace.read_text())




def test_les_taches_vscode_passent_bien_le_drapeau():
    """Le drapeau peut etre parfait et ne servir a rien si la tache ne le
    passe pas. C'est la jointure qui casse en silence."""
    taches = os.path.join(DOSSIER_DIAGNOSTIC,
                          os.pardir, ".vscode", "tasks.json")
    if not os.path.exists(taches):
        pytest.skip("tasks.json absent de cette image")
    contenu = open(taches, encoding="utf-8").read()
    lignes = [l for l in contenu.splitlines() if '"command"' in l
              and "codelab new" in l]
    assert lignes, "aucune tache de creation de projet trouvee"
    for ligne in lignes:
        assert "--ouvrir" in ligne, (
            "cette tache cree le projet sans rouvrir la fenetre : " + ligne)


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




def test_par_le_domaine_une_application_est_renvoyee_a_son_sous_domaine(deux_origines):
    """Le panneau ouvert par son nom de domaine (relaye par le VPS) envoie
    chaque application a <nom>.<domaine> : rien a declarer."""
    c = deux_origines
    r = c.get("/public/page?x=1", base_url="http://toi.duckdns.org:9301", headers=_PAR_LE_VPS)
    assert r.headers["Location"] == "https://public.toi.duckdns.org/page?x=1"
    # En local (une adresse IP, sans le VPS) : le port des applications.
    r = c.get("/public/", base_url="http://192.168.1.10:9301")
    assert r.headers["Location"] == "http://192.168.1.10:9302/public/"
    # Un nom du reseau domestique n'a pas de sous-domaines.
    r = c.get("/public/", base_url="http://zimablade.local:9301", headers=_PAR_LE_VPS)
    assert r.headers["Location"] == "https://zimablade.local:9302/public/" \
        or r.headers["Location"] == "http://zimablade.local:9302/public/"




def test_le_sous_domaine_sert_toute_l_application(deux_origines, monkeypatch):
    """Sur le port des applications, blog.<domaine> sert « blog » : tout le
    chemin lui appartient, la racine et /theme.css compris."""
    vus = []
    monkeypatch.setattr(app, "_proxy", lambda nom, sous: (vus.append((nom, sous)) or ("ok", 200)))
    c = deux_origines
    for chemin, attendu in (("/", ""), ("/theme.css", "theme.css"), ("/a/b?q=1", "a/b")):
        r = c.get(chemin, base_url="http://public.toi.duckdns.org:9302", headers=_PAR_LE_VPS)
        assert r.status_code == 200 and vus[-1] == ("public", attendu), chemin
    # Un sous-domaine qui ne nomme aucune application : la garde habituelle.
    r = c.get("/", base_url="http://inconnue.toi.duckdns.org:9302", headers=_PAR_LE_VPS)
    assert "Ce n'est pas le panneau" in r.get_data(as_text=True)
    # Sans le VPS devant, pas de sous-domaine.
    vus.clear()
    c.get("/", base_url="http://public.toi.duckdns.org:9302")
    assert vus == []




def test_la_session_vaut_pour_les_sous_domaines(deux_origines):
    """Une application privee sur blog.<domaine> doit reconnaitre la session
    ouverte sur <domaine> : le cookie porte Domain=<domaine>. En local, il
    reste attache a l'hote."""
    with app.flask_app.test_request_context(
            "/", base_url="http://toi.duckdns.org:9301", headers=_PAR_LE_VPS):
        assert app.flask_app.session_interface.get_cookie_domain(app.flask_app) == "toi.duckdns.org"
    with app.flask_app.test_request_context("/", base_url="http://192.168.1.10:9301"):
        assert app.flask_app.session_interface.get_cookie_domain(app.flask_app) is None




def test_l_adresse_publique_s_apprend_par_le_domaine(deux_origines):
    """Plus de champ « Adresse publique » : c'est le domaine par lequel le
    panneau a ete ouvert depuis internet. Ensuite, seule une session peut la
    changer -- pas un visiteur qui imiterait les en-tetes du VPS."""
    c = deux_origines
    app.ecrire_exposition({})
    c.get("/login", base_url="http://toi.duckdns.org:9301", headers=_PAR_LE_VPS)
    assert app.adresse_publique() == "https://toi.duckdns.org"
    c.get("/login", base_url="http://pirate.example.org:9301", headers=_PAR_LE_VPS)
    assert app.adresse_publique() == "https://toi.duckdns.org"


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
    assert [s["name"] for s in services] == ["dagster", "mailpit", "messages"]
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
    assert [s["name"] for s in d["services"]] == [app.DAGSTER_NOM, app.MAILPIT_NOM, app.MESSAGERIE_NOM]
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
    assert app.SERVICES_MASQUABLES == frozenset({app.DAGSTER_NOM, app.MAILPIT_NOM,
                                                app.MESSAGERIE_NOM})




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
    assert "name: codelab-redis" in ci and '"redis/**"' in ci




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

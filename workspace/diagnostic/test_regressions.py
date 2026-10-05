"""
Suite de regressions CodeLab -- les tests, et rien d'autre.

Ce fichier a ete detache de checks.py, qui portait les deux : les sondes que
le diagnostic execute en production, et les tests qui verifient le depot.
Les melanger avait un cout concret, pas seulement esthetique :

  - checks.py est importe par app.py et par l'asset Dagster, dans des images
    qui n'embarquent pas pytest -- il a donc fallu un faux module pytest pour
    que les decorateurs s'evaluent quand meme a l'import. Ce repli n'a plus
    lieu d'etre : les sondes n'ont plus une seule ligne de pytest.
  - 12 000 lignes dont 10 000 de tests rendaient illisible le fichier que
    deux services chargent en production.

CE QUI VIT ICI : les tests, les fixtures, et le filet qui detourne les
chemins d'etat pour qu'aucun test ne touche l'installation.

CE QUI VIT DANS checks.py : les sondes, leur classement, et la facon de les
lancer. Le diagnostic n'a besoin que de ce fichier-la.

Lancer la suite :

    python3 -m pytest workspace/diagnostic/test_regressions.py -q -rs
"""
import ast
import base64
import builtins
import datetime
import enum
import importlib.util
import inspect
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

# checks.py est dans le meme dossier. L'ajout explicite au chemin vaut pour
# les cas ou ce fichier est lance autrement que par pytest depuis la racine.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checks                                                   # noqa: E402
from checks import (                                            # noqa: E402
    CLES_MOT_DE_PASSE_PG, DOSSIER_PANNEAU, Etat,
    NOM_SUITE_PANNEAU, PORT_APPS_DEFAUT, SAUVEGARDE_FRAICHE_H,
    SAUVEGARDE_VIEILLE_H, SEUIL_DISQUE, SEUIL_DISQUE_CRITIQUE,
    SEUIL_LIBRE_CRITIQUE_GO, SEUIL_LIBRE_GO, SEVERITE_MAX,
    TESTS_APPROFONDIS, THEMES, THEME_DES_SONDES, THEME_DES_TESTS,
    _hierarchiser, _hotes_panneau, _port_applications, app,
    check_applications, check_config, check_espace_disque,
    check_exposition, check_isolation, check_origine_applications,
    check_panneau_ferme, check_panneau_joignable, check_provenance,
    check_sauvegardes, check_surface_exposee, indices_par_rubrique,
    mot_de_passe_pg, noms_des_tests, pg_settings, pire, run_all, run_test,
    sondes_par_theme,
)


# ==========================================================================
#                        TESTS DU PANNEAU (app-manager)
# ==========================================================================
#
# La suite de regressions du panneau vit ici, avec l'etat des lieux, pour une
# raison simple : c'est le seul endroit ou elle est lancable des DEUX cotes
# -- par la CI avant qu'une image ne parte, et depuis l'installation qui
# tourne, par la page /tests du diagnostic.
#
#     python -m pytest workspace/diagnostic/test_regressions.py -q
#
# CE QUI REND CELA SANS DANGER, ET QU'IL FAUT LIRE AVANT DE TOUCHER A CE
# FICHIER. Ces tests ecrivent : ils enregistrent des applications, creent des
# comptes, posent des cles d'acces. Tant qu'ils vivaient dans un dossier de
# tests, on pouvait admettre que leurs fixtures ne redirigent pas TOUS les
# chemins -- le fixture "client", par exemple, ne redirigeait pas APPS_FILE.
# Ici, ils peuvent tourner dans une installation reelle, ou ces chemins
# existent : sans precaution, le premier lancement remplacerait tes
# applications et tes comptes par des donnees de test.
#
# D'ou le filet ci-dessous : AVANT chaque test, sans exception et sans que
# personne ait a y penser, tous les chemins d'etat du panneau sont detournes
# vers un dossier jetable, et l'on verifie qu'aucun ne pointe encore vers le
# vrai. Un test qui oublierait de rediriger quelque chose ne peut plus faire
# de degat -- et si le filet lui-meme se cassait, les tests s'arretent au lieu
# de s'executer.

# Tous les chemins que le panneau ecrit. La liste est verifiee : si le
# panneau en gagne un et qu'on oublie de l'ajouter ici, le test qui suit
# echoue au lieu de laisser passer une ecriture reelle.
CHEMINS_ETAT = [
    "STATE_DIR", "APPS_FILE", "LOG_DIR", "UTILISATEURS_FILE", "PASSKEYS_FILE",
    "ACCES_FILE", "PROCESSUS_FILE", "MASQUEES_FILE", "MESSAGES_FILE", "CHILD_HOME", "ALERTES_FILE", "SMTP_FILE",
    "CATEGORIES_FILE", "SURVEILLANCE_FILE", "COFFRE_FILE",
    "EXPOSITION_FILE", "SHARED_CONFIG_DIR",
    "SHARED_ENV_FILE", "LEGACY_ADMIN_PASSWORD_FILE", "LEGACY_SECRET_KEY_FILE",
    "SSO_PRIVATE_FILE", "DB_FILE",
]


@pytest.fixture(autouse=True)
def _bac_a_sable(tmp_path, monkeypatch):
    """Le filet. autouse : il s'applique a TOUS les tests, y compris ceux
    ecrits demain par quelqu'un qui n'aura pas lu ce commentaire."""
    # Noms prefixes : quatre tests creent deja leur propre tmp_path/"etat",
    # et se heurtaient a celui du filet ("File exists"). Le bac du filet ne
    # doit marcher sur les pieds de personne.
    etat = tmp_path / "_filet-etat"
    partage = tmp_path / "_filet-config"
    travail = tmp_path / "_filet-workspace"
    etat.mkdir()
    partage.mkdir()
    travail.mkdir()
    remplacements = {
        "STATE_DIR": str(etat),
        "APPS_FILE": str(etat / "apps.json"),
        "LOG_DIR": str(etat / "logs"),
        "UTILISATEURS_FILE": str(etat / "utilisateurs.json"),
        "PASSKEYS_FILE": str(etat / "passkeys.json"),
        "ACCES_FILE": str(etat / "acces.jsonl"),
        "PROCESSUS_FILE": str(etat / "processus.json"),
        "MASQUEES_FILE": str(etat / "masquees.json"),
        "MESSAGES_FILE": str(etat / "messages.jsonl"),
        "CHILD_HOME": str(etat / "home"),
        "ALERTES_FILE": str(etat / "alertes.json"),
        "SMTP_FILE": str(etat / "smtp.json"),
        "SURVEILLANCE_FILE": str(etat / "surveillance.json"),
        "COFFRE_FILE": str(etat / "reglages.chiffres"),
        "CATEGORIES_FILE": str(etat / "categories.json"),
        "EXPOSITION_FILE": str(etat / "exposition.json"),
        "SHARED_CONFIG_DIR": str(partage),
        "SHARED_ENV_FILE": str(partage / "credentials.env"),
        "LEGACY_ADMIN_PASSWORD_FILE": str(etat / "admin_password"),
        "LEGACY_SECRET_KEY_FILE": str(etat / "flask_secret_key"),
        "SSO_PRIVATE_FILE": str(etat / "codelab_sso_private.key"),
        "DB_FILE": str(etat / "codelab.db"),
    }
    for nom in CHEMINS_ETAT:
        assert hasattr(app, nom), (
            f"{nom} a disparu du panneau : le filet ne couvre plus tout, "
            f"mets CHEMINS_ETAT a jour avant de continuer")
        monkeypatch.setattr(app, nom, remplacements[nom])
    # Ceinture : aucun chemin ne doit plus designer une installation reelle.
    for nom in CHEMINS_ETAT:
        valeur = getattr(app, nom)
        assert str(tmp_path) in valeur, f"{nom} pointe encore vers {valeur}"
    # Le cache du registre garde les valeurs de l'ancien chemin.
    app._apps_cache["signature"] = None
    # isolement_disponible() garde son verdict, et il LANCE UN PROCESS
    # pour le calculer. Deux raisons de le fixer ici : un test qui
    # simule un noyau qui refuse ne doit pas contaminer les suivants,
    # et aucun test ne doit lancer unshare sans le vouloir -- ceux qui
    # remplacent Popen s'y casseraient. Repli par defaut, donc : la
    # valeur sure. Les trois tests qui portent sur la detection
    # elle-meme remettent None, ceux qui veulent la branche isolee
    # posent True.
    app._isolement.update(verdict=False, raison="fixe par le filet")
    # Le second facteur obligatoire de l'administrateur est eteint par
    # defaut ICI SEULEMENT : des centaines de tests se connectent en
    # administrateur avec le mot de passe, et parlent d'autre chose. Les
    # tests de ce facteur le rallument (voir « admin_2fa »).
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "0")
    # Le coffre : une cle neuve par test, et rien en memoire d'un test a
    # l'autre -- sinon un reglage ecrit par l'un se lirait dans le suivant.
    from cryptography.fernet import Fernet
    monkeypatch.setattr(app, "_cle_chiffrement", Fernet.generate_key().decode())
    app._coffre["donnees"] = None
    yield
    app._coffre["donnees"] = None


def test_le_filet_detourne_bien_tous_les_chemins():
    """Le filet se teste lui-meme : si un chemin d'etat lui echappait, ce
    test le dirait avant qu'une ecriture n'atteigne une vraie installation."""
    import inspect
    source = inspect.getsource(app)
    # Tout ce qui ressemble a un chemin construit sous STATE_DIR,
    # SHARED_CONFIG_DIR ou ROOT doit figurer dans CHEMINS_ETAT.
    #
    # ROOT a ete ajoute avec la page des cles : le panneau y ecrit desormais
    # /workspace/.codex et /workspace/.openclaw, et ces deux-la echappaient
    # au motif -- un test qui cree une cle serait alle ecrire dans le vrai
    # /workspace de la machine qui lance la suite.
    trouves = set(re.findall(
        r"^([A-Z][A-Z0-9_]*) = os\.path\.join\((?:STATE_DIR|SHARED_CONFIG_DIR|ROOT)",
        source, re.M))
    oublies = trouves - set(CHEMINS_ETAT)
    assert not oublies, (
        f"chemins d'etat non couverts par le filet : {sorted(oublies)}")


# ------------------------- le client de test et le jeton -------------------
#
# Le panneau exige un jeton CSRF sur toute ecriture d'une session ouverte.
# Dans un navigateur, c'est l'enveloppe posee autour de fetch qui l'ajoute --
# une fois, pour tous les appels. Ici, c'est ce client : sans lui, chacun des
# ~120 appels d'ecriture des tests devrait poser l'en-tete a la main, et le
# jour ou l'un serait oublie on croirait a une regression du panneau.
#
# Il lit le jeton dans la session, exactement comme la page le lit dans le
# HTML servi. Ce qui n'est PAS teste par ce client -- l'absence de jeton, un
# jeton faux -- l'est explicitement, plus bas, section 22.
# ---------- l'isolement se detecte en l'essayant, pas en le cherchant -----
#
# Vecu sur une vraie machine : "unshare -Ur true" repondait "Operation not
# permitted", et le panneau lancait quand meme les applications derriere
# unshare -- donc aucune ne demarrait. La detection cherchait le BINAIRE.
# unshare vient de util-linux : il est toujours present. Ce qui manquait,
# c'etait l'autorisation du noyau, et elle ne se lit pas dans un PATH.
#
# Les deux tests posent un faux unshare en tete de PATH : l'un refuse comme
# le noyau refusait, l'autre accepte. La detection doit les distinguer.

def _faux_unshare(tmp_path, code, message=""):
    dossier = tmp_path / "_faux-bin"
    dossier.mkdir(exist_ok=True)
    outil = dossier / "unshare"
    outil.write_text("#!/bin/sh\n"
                     + (("echo '%s' >&2\n" % message) if message else "")
                     + "exit %d\n" % code)
    outil.chmod(0o755)
    return str(dossier)


def test_un_noyau_qui_refuse_fait_tomber_l_isolement(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    app._isolement.update(verdict=None, raison="")

    assert app.isolement_disponible() is False
    assert "Operation not permitted" in app._isolement["raison"]

    # Et surtout : la commande construite doit etre la commande ORDINAIRE.
    # C'est ce qui fait la difference entre une application qui demarre sans
    # isolement et une application qui ne demarre pas du tout.
    argv, env = app.commande_isolee("mon-projet", "/workspace/mon-projet",
                                    "python app.py")
    assert argv[0] != "unshare", argv
    assert argv == ["bash", "-lc", "python app.py"]
    assert env == {}


def test_un_noyau_qui_accepte_garde_l_isolement(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _faux_unshare(tmp_path, 0)
                       + os.pathsep + os.environ["PATH"])
    app._isolement.update(verdict=None, raison="")

    assert app.isolement_disponible() is True

    argv, env = app.commande_isolee("mon-projet", "/workspace/mon-projet",
                                    "python app.py")
    assert argv[0] == "unshare", argv
    assert env["CODELAB_COMMANDE"] == "python app.py"


def test_la_sonde_du_diagnostic_voit_le_refus_du_noyau(tmp_path, monkeypatch):
    """La sonde se trompait de la meme facon : elle affichait vert sur une
    machine ou aucune application ne pouvait demarrer."""
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("APP_MANAGER_ISOLER", raising=False)

    ok, _nom, detail = check_isolation()
    # ALERTE et non ECHEC : l'uid par application tient toujours, et ce refus
    # vient de l'hote -- aucune commande du conteneur ne le leve. Faire
    # tomber le run de diagnostic la-dessus quatre fois par heure, c'etait
    # apprendre a ne plus lire ses alertes.
    assert ok is Etat.ALERTE
    assert "namespace utilisateur" in detail
    # Ce qui reste vrai doit etre dit aussi : l'uid par application tient
    # toujours. Annoncer "aucune isolation" ferait chercher une panne la ou
    # il n'y en a pas.
    assert "propre uid" in detail


def test_le_refus_du_noyau_nomme_le_verrou_qui_bloque(tmp_path, monkeypatch):
    """Conseiller deux sysctl sans regarder s'ils sont en cause envoyait
    taper des commandes sans effet -- sur une machine ou c'est AppArmor qui
    refuse, elles ne changent rien et l'on recommence indefiniment."""
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("APP_MANAGER_ISOLER", raising=False)

    ouvert = tmp_path / "max_user_namespaces"
    ouvert.write_text("15000\n")
    ferme = tmp_path / "apparmor_restrict_unprivileged_userns"
    ferme.write_text("1\n")
    monkeypatch.setattr(checks, "VERROUS_USERNS", [
        (str(tmp_path / "absent"), "0", "sysctl -w kernel.unprivileged_userns_clone=1"),
        (str(ouvert), "0", "sysctl -w user.max_user_namespaces=15000"),
        (str(ferme), "1", "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0"),
    ])

    _ok, _nom, detail = check_isolation()
    # Le verrou ferme est nomme, avec sa commande.
    assert "apparmor_restrict_unprivileged_userns=1" in detail
    assert "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" in detail
    # Les deux autres ne sont pas en cause : les citer serait envoyer taper
    # des commandes qui ne changent rien.
    assert "max_user_namespaces=15000" not in detail
    assert "unprivileged_userns_clone" not in detail


def test_sans_verrou_ferme_la_sonde_ne_conseille_pas_de_sysctl(tmp_path, monkeypatch):
    """Quand aucun sysctl n'interdit rien, c'est le bac a sable du conteneur
    qui refuse -- et aucun sysctl n'y changera quoi que ce soit."""
    monkeypatch.setenv("PATH", _faux_unshare(
        tmp_path, 1, "unshare: unshare failed: Operation not permitted")
        + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("APP_MANAGER_ISOLER", raising=False)
    ouvert = tmp_path / "max_user_namespaces"
    ouvert.write_text("15000\n")
    monkeypatch.setattr(checks, "VERROUS_USERNS",
                        [(str(ouvert), "0", "sysctl -w user.max_user_namespaces=15000")])

    _ok, _nom, detail = check_isolation()
    assert "seccomp" in detail and "AppArmor" in detail
    assert "sysctl -w" not in detail


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

CHEMINS_OUTIL_CODELAB = [
    "/usr/local/bin/codelab",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "dev", "codelab"),
]


def _outil_codelab():
    for chemin in CHEMINS_OUTIL_CODELAB:
        if os.path.exists(chemin):
            return chemin
    return None


def _lancer_codelab(tmp_path, args, env_sup=None):
    import subprocess
    outil = _outil_codelab()
    if outil is None:
        pytest.skip("l'outil codelab n'est pas la (image sans le conteneur dev)")

    espace = tmp_path / "ws"
    espace.mkdir(exist_ok=True)
    manuel = tmp_path / "AGENTS-source.md"
    manuel.write_text("# Manuel CodeLab\n")

    env = dict(os.environ)
    env.update({"CODELAB_WORKSPACE": str(espace),
                "CODELAB_AGENTS_SOURCE": str(manuel),
                "HOME": str(tmp_path / "home")})
    env.pop("VSCODE_IPC_HOOK_CLI", None)
    env.update(env_sup or {})
    (tmp_path / "home").mkdir(exist_ok=True)

    r = subprocess.run(["sh", outil] + args, capture_output=True, text=True,
                       env=env, timeout=120)
    return r, espace


def _faux_code(tmp_path, code=0):
    """Un faux "code" qui ecrit ce qu'on lui demande dans un fichier."""
    dossier = tmp_path / "_faux-vscode"
    dossier.mkdir(exist_ok=True)
    trace = tmp_path / "appel-code.txt"
    outil = dossier / "code"
    # Ecrit sans %-formatage : le script shell contient lui-meme des "%s"
    # (ceux de printf), et les melanger donnait un TypeError obscur.
    outil.write_text("#!/bin/sh\n"
                     'printf "%s\\n" "$*" >> ' + '"' + str(trace) + '"\n'
                     "exit " + str(code) + "\n")
    outil.chmod(0o755)
    return str(dossier), trace


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
    taches = os.path.join(os.path.dirname(os.path.abspath(__file__)),
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

_DEBUT_RECONCILIATION = "# ------------------- mise a jour des fichiers non modifies"
_FIN_RECONCILIATION = "# ----------------------- abandon des privileges"


def _racine_depot():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, os.pardir)


def _bloc_reconciliation():
    """Le bloc de l'entrypoint, tel quel, avec le seul chemin d'image redirige."""
    entree = os.path.join(_racine_depot(), "dagster", "entrypoint.sh")
    if not os.path.exists(entree):
        pytest.skip("depot complet absent de cette image")
    texte = open(entree, encoding="utf-8").read()
    # Un assert, pas un skip : si les reperes ont bouge, le test doit crier
    # plutot que disparaitre en silence -- c'est exactement ainsi qu'une
    # regression passe inapercue.
    assert _DEBUT_RECONCILIATION in texte, "repere de debut introuvable dans entrypoint.sh"
    assert _FIN_RECONCILIATION in texte, "repere de fin introuvable dans entrypoint.sh"
    bloc = texte[texte.index(_DEBUT_RECONCILIATION):texte.index(_FIN_RECONCILIATION)]
    return bloc.replace('SEED_SUMS=/opt/dagster/workspace.sums', 'SEED_SUMS="$SUMS_TEST"')


def _somme(chemin):
    import hashlib
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _somme_texte(contenu):
    import hashlib
    return hashlib.sha256(contenu.encode()).hexdigest()


class _Atelier:
    """Un faux /workspace, son squelette d'image, et de quoi rejouer le bloc.

    Volontairement un objet et non une fonction : plusieurs tests ont besoin
    de faire tourner DEUX demarrages de suite, ce qui est justement le coeur
    du mecanisme -- ce que le premier note, le second s'en sert.
    """

    def __init__(self, tmp_path):
        self.base = tmp_path
        self.seed = tmp_path / "seed" / "diagnostic"
        self.ws = tmp_path / "ws" / "diagnostic"
        self.seed.mkdir(parents=True)
        self.ws.mkdir(parents=True)
        (tmp_path / "ws" / ".codelab").mkdir()
        self.marqueur = tmp_path / "ws" / ".codelab" / "workspace-v1"
        self.marqueur.write_text("marqueur")
        self.empreintes = tmp_path / "ws" / ".codelab" / "empreintes"
        self.sums = tmp_path / "figees.sums"
        self.sums.write_text("", encoding="utf-8")
        self.cible = self.ws / "checks.py"
        self.livre = self.seed / "checks.py"

    def figer(self, *contenus):
        """Declare des contenus comme "versions livrees autrefois" -- la liste
        d'amorcage des installations anterieures au mecanisme."""
        self.sums.write_text("".join(
            _somme_texte(c) + " diagnostic/checks.py\n" for c in contenus),
            encoding="utf-8")

    def demarrer(self):
        import subprocess
        env = dict(os.environ,
                   WORKSPACE_SEED=str(self.base / "seed"),
                   WORKSPACE_DIR=str(self.base / "ws"),
                   SEED_MARKER=str(self.marqueur),
                   SUMS_TEST=str(self.sums),
                   CODELAB_GROUP="root")
        r = subprocess.run(["sh", "-c", _bloc_reconciliation()],
                           capture_output=True, text=True, timeout=60, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout + r.stderr

    def sur_disque(self):
        return (self.cible.read_text(encoding="utf-8")
                if self.cible.exists() else None)

    def note(self):
        """L'empreinte notee pour checks.py, ou None."""
        if not self.empreintes.exists():
            return None
        for ligne in self.empreintes.read_text(encoding="utf-8").splitlines():
            if ligne.endswith(" diagnostic/checks.py"):
                return ligne.split(" ")[0]
        return None


def test_une_ancienne_version_livree_est_remplacee(tmp_path):
    """Le cas qui a motive tout ceci, sur une installation anterieure au
    mecanisme : rien n'est encore note, c'est la liste figee qui tranche."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("ancienne", encoding="utf-8")
    a.figer("ancienne", "corrigee")
    journal = a.demarrer()
    assert a.sur_disque() == "corrigee", "la correction de l'image n'a pas atteint le disque"
    assert "mis a jour" in journal


def test_un_fichier_modifie_par_l_utilisateur_n_est_jamais_ecrase(tmp_path):
    """La regle qui protege le travail : une empreinte qui n'est ni la notre
    ni l'une des anciennes, c'est du travail humain."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("MON CODE", encoding="utf-8")
    a.figer("ancienne", "corrigee")
    journal = a.demarrer()
    assert a.sur_disque() == "MON CODE", "le travail de l'utilisateur a ete ecrase"
    assert "modifies sur place" in journal


def test_un_fichier_supprime_expres_ne_ressuscite_pas(tmp_path):
    """Supprimer le projet d'exemple doit tenir. Le voir revenir a chaque
    redemarrage serait insupportable, et c'est la raison d'etre du marqueur."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.figer("ancienne", "corrigee")
    a.demarrer()
    assert a.sur_disque() is None, "un fichier supprime a ete recree"


def test_rien_ne_bouge_avant_le_premier_amorcage(tmp_path):
    """Sans marqueur, l'amorcage classique n'a pas encore eu lieu : cette
    passe n'a rien a faire et ne doit surtout pas prendre les devants."""
    a = _Atelier(tmp_path)
    a.livre.write_text("corrigee", encoding="utf-8")
    a.cible.write_text("ancienne", encoding="utf-8")
    a.figer("ancienne", "corrigee")
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
    # La liste figee n'a servi a rien ici, et c'est exactement l'objectif.
    assert a.sums.read_text(encoding="utf-8") == ""


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
    a.figer("intact")
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
    a.figer("ancienne")
    # Le piege, au nom qu'utilisait l'ancienne version du code.
    os.symlink(str(victime), str(a.ws / "checks.py.codelab-tmp"))
    a.demarrer()
    assert victime.read_text(encoding="utf-8") == "intact", (
        "le temporaire previsible a detourne l'ecriture, en root")
    # La mise a jour legitime doit quand meme avoir eu lieu.
    assert a.sur_disque() == "corrigee"


# ------------- ce fichier doit rester importable sans pytest --------------
#
# Regression vecue : "import pytest" en tete de fichier, puis une classe
# definie a partir du module du panneau, ont fait disparaitre le projet
# diagnostic de Dagster. L'image Dagster n'a ni pytest ni flask ni le
# panneau ; definitions.py ignore silencieusement un projet qui ne se charge
# pas, donc l'asset n'etait plus la et rien ne criait.
#
# Le test rejoue exactement cette situation dans un interpreteur neuf : les
# paquets absents de l'image Dagster y sont rendus introuvables, et le
# panneau aussi. Ce qui doit survivre, ce sont les SONDES -- run_all() --,
# c'est-a-dire tout ce que Dagster vient chercher ici.

def test_checks_reste_importable_sans_pytest_ni_panneau():
    import subprocess

    script = """
import importlib.util, os, sys

ABSENTS = {"pytest", "flask", "psutil", "waitress", "webauthn", "qrcode"}


class Bloqueur:
    def find_spec(self, nom, chemin=None, cible=None):
        if nom.split(".")[0] in ABSENTS:
            raise ModuleNotFoundError("No module named " + repr(nom), name=nom)
        return None


sys.meta_path.insert(0, Bloqueur())

# Le panneau n'est pas dans l'image Dagster : aucun des chemins cherches par
# _charger_panneau() ne doit repondre.
_existe = os.path.exists
os.path.exists = lambda c: (
    False if str(c).endswith(os.path.join("app-manager", "app", "app.py"))
    else _existe(c))

spec = importlib.util.spec_from_file_location("checks_sans_pytest", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

os.path.exists = _existe

assert module.app is None, "le panneau aurait du rester introuvable"
assert callable(module.run_all), "les sondes doivent survivre a l'absence de pytest"
print("IMPORT OK")
"""

    # checks.__file__ et non __file__ : c'est l'importabilite des SONDES
    # qu'on verifie, pas celle de cette suite -- qui, elle, a le droit
    # d'exiger pytest.
    r = subprocess.run(
        [sys.executable, "-c", script, os.path.abspath(checks.__file__)],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (
        "checks.py ne s'importe plus sans pytest ni panneau -- c'est la "
        "situation de l'image Dagster, ou le projet diagnostic disparaitrait "
        "sans bruit :\n" + r.stdout + r.stderr)
    assert "IMPORT OK" in r.stdout


# Tout ce bloc suppose que le module du panneau a ete trouve. Dans les
# images Dagster et dev il ne l'est pas -- elles n'embarquent ni le
# panneau ni flask -- et seules les sondes du haut de ce fichier y servent.
if app is not None:
    class ClientAvecJeton(app.flask_app.test_client_class or __import__(
            "flask.testing", fromlist=["FlaskClient"]).FlaskClient):
        def open(self, *a, **kw):
            methode = (kw.get("method") or (a[1] if len(a) > 1 else "GET") or "GET").upper()
            if methode not in ("GET", "HEAD"):
                with self.session_transaction() as sess:
                    jeton = sess.get("jeton")
                if jeton:
                    entetes = dict(kw.get("headers") or {})
                    entetes.setdefault(app.JETON_ENTETE, jeton)
                    kw["headers"] = entetes
            return super().open(*a, **kw)


    app.flask_app.test_client_class = ClientAvecJeton


def _projet(tmp_path, fichiers):
    for nom, contenu in fichiers.items():
        cible = tmp_path / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu)
    return str(tmp_path)


# ------------------ SSO des applications ------------------
#
# L'application ne doit recevoir ni le cookie de session ni la cle privee.
# Le test verifie aussi que l'audience lie l'assertion au projet demande.

def test_assertion_sso_signee_et_limitee_a_l_application(monkeypatch):
    import base64
    import json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    cle = app._generer_cle_sso()
    monkeypatch.setattr(app, "_sso_private_key", cle)
    monkeypatch.setattr(app, "_sso_public_key", app._public_sso_b64(cle))

    app.flask_app.secret_key = "test-sso-secret"
    with app.flask_app.test_request_context("/"):
        app.session["authed"] = True
        app.session["role"] = app.ROLE_UTILISATEUR
        app.session["utilisateur"] = "alice"
        token = app.jeton_sso("wine-manager")

    h, p, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    signature = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    public = base64.urlsafe_b64decode(
        app._sso_public_key + "=" * (-len(app._sso_public_key) % 4)
    )
    Ed25519PublicKey.from_public_bytes(public).verify(
        signature, (h + "." + p).encode("ascii")
    )

    assert payload["sub"] == "alice"
    assert payload["aud"] == "wine-manager"
    assert payload["iss"] == app.SSO_ISSUER
    assert payload["exp"] - payload["iat"] == app.SSO_TOKEN_TTL


def test_secrets_partages_n_expose_pas_les_cles_sso(tmp_path, monkeypatch):
    env = tmp_path / "credentials.env"
    env.write_text(
        "CODELAB_SSO_PRIVATE_KEY=secret\n"
        "CODELAB_SSO_PUBLIC_KEY=public\n"
        "CODELAB_TEST=value\n"
    )
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(env))
    assert app.secrets_partages() == {"CODELAB_TEST": "value"}


# ------------------ 0. cache du registre d'applications ------------------
#
# load() est mis en cache pour eviter une lecture disque par requete
# proxifiee. Le risque du cache est de servir un registre perime : ces deux
# tests tiennent la seule propriete qui compte, "une modification est vue".

def test_le_registre_est_relu_quand_la_base_change(tmp_path, monkeypatch):
    import sqlite3
    app._apps_cache["signature"] = None
    app.save({"un": {"port": 9101}})
    assert list(app.load()) == ["un"]

    # Ecriture exterieure, sans passer par save() : avec sqlite3 ou DB
    # Browser, par exemple. Les declencheurs de la table la signalent.
    cx = sqlite3.connect(app.DB_FILE)
    cx.execute("UPDATE applications SET nom = 'deux', port = 9102")
    cx.commit()
    cx.close()
    assert list(app.load()) == ["deux"]


def test_modifier_le_registre_recu_ne_corrompt_pas_le_cache(tmp_path, monkeypatch):
    """Les appelants modifient ce que load() renvoie avant de le repasser a
    save() : sans copie, ces modifications apparaitraient dans le cache avant
    l'ecriture -- et y resteraient meme si elle echouait."""
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    app.save({"un": {"port": 9101, "enabled": True}})

    registre = app.load()
    registre["un"]["enabled"] = False
    registre["intrus"] = {"port": 9999}

    relu = app.load()
    assert relu["un"]["enabled"] is True
    assert "intrus" not in relu


# ----------------------------- 1. detection -----------------------------

def test_un_projet_vite_est_construit_puis_servi_en_statique(tmp_path):
    """Le bug d'origine : un projet Vite n'a pas de script "start". La
    suggestion proposait quand meme "npm start", l'application etait declaree
    puis echouait au demarrage."""
    chemin = _projet(tmp_path, {
        "package.json": json.dumps({"scripts": {"build": "vite build"},
                                    "devDependencies": {"vite": "^5"}}),
        "package-lock.json": "{}",
    })
    commande, build = app.detect_project(chemin)
    assert commande == "python3 -m http.server $PORT --directory dist"
    assert build == "npm ci && npm run build"


def test_aucune_commande_ne_code_le_port_en_dur(tmp_path):
    """Le port est attribue par l'app-manager et injecte en $PORT. Un numero
    ecrit en dur se desynchronise des que le port change."""
    cas = [
        {"index.html": "<html>"},
        {"manage.py": ""},
        {"package.json": json.dumps({"scripts": {"build": "vite build"},
                                     "devDependencies": {"vite": "^5"}})},
        {"package.json": json.dumps({"scripts": {"build": "next build"},
                                     "dependencies": {"next": "14"}})},
    ]
    for i, fichiers in enumerate(cas):
        dossier = tmp_path / f"cas{i}"
        dossier.mkdir()
        commande, _ = app.detect_project(_projet(dossier, fichiers))
        assert "$PORT" in commande, commande


# --------------------------- 2. bornage des chemins ---------------------------

def test_les_chemins_restent_dans_le_workspace():
    assert app.under_root(os.path.join(app.ROOT, "mon-projet"))
    assert not app.under_root("/etc")
    assert not app.under_root(os.path.join(app.ROOT, "..", "etc"))
    # Un dossier voisin dont le nom commence comme la racine ne doit pas
    # passer pour un enfant : "/workspace-bis" n'est pas dans "/workspace".
    assert not app.under_root(app.ROOT + "-bis")


def test_un_nom_de_projet_ne_peut_pas_porter_de_separateur():
    """Le nom sert a construire des chemins de journaux et des routes."""
    assert app.valid_name("../../etc/passwd") == "etc-passwd"


# --------------------------- 3. authentification ---------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    # Un test qui active le second facteur ecrit dans credentials.env et pose
    # un secret global : sans ces deux lignes il ecrirait le VRAI fichier de
    # la machine, et laisserait la 2FA active pour les tests suivants.
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "_totp_secret", "")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()


def test_les_routes_api_refusent_sans_session(client):
    assert client.get("/api/apps").status_code == 401
    assert client.post("/api/toggle/quelconque").status_code == 401
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 200
    assert client.get("/api/apps").status_code == 200


def test_la_limite_de_tentatives_ne_se_contourne_pas_par_en_tete(client):
    """X-Forwarded-For est pose par le client quand le service est publie
    directement : le faire varier donnait un compteur neuf a chaque essai, ce
    qui annulait la limite."""
    assert not app.trust_proxy(), "le proxy de confiance ne doit pas etre actif par defaut"
    for i in range(app.RATE_LIMIT_MAX):
        assert client.post("/login", json={"password": "faux"},
                           headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 401
    assert client.post("/login", json={"password": "faux"},
                       headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_le_point_d_appui_de_dagster_repond_par_un_code_sans_page(client):
    """nginx interroge cette route avant chaque requete vers Dagster : il lui
    faut un code, pas une redirection. Si elle se mettait a repondre 302
    comme les autres routes protegees, nginx la lirait comme un refus et
    Dagster deviendrait inaccessible meme connecte."""
    r = client.get("/api/auth-check")
    assert r.status_code == 401 and not r.data

    client.post("/login", json={"password": "secret-de-test"})
    r = client.get("/api/auth-check")
    assert r.status_code == 204 and not r.data


def test_le_cookie_de_session_est_samesite_lax(client):
    """Les actions du panneau sont des POST sans corps : sans SameSite, un
    formulaire pose sur un autre site peut les declencher avec le cookie de
    l'utilisateur connecte."""
    r = client.post("/login", json={"password": "secret-de-test"})
    cookie = r.headers.get("Set-Cookie", "")
    assert "SameSite=Lax" in cookie and "HttpOnly" in cookie


# ------------------- 3 bis. visibilite et second facteur -------------------
#
# Deux reglages destines a une exposition hors du reseau local. Ils sont
# inactifs par defaut : ces tests tiennent surtout le fait qu'ils ne changent
# rien tant qu'on ne les active pas, et qu'ils ferment bien une fois actifs.

def test_une_application_privee_exige_la_session(client, monkeypatch, tmp_path):
    """Le controle vit dans le proxy, pas dans l'interface : un lien qui
    circule ne doit pas suffire a ouvrir une application privee."""
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    app.save({"prive": {"path": str(tmp_path), "command": "x", "port": 9101,
                        "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: True)

    r = client.get("/prive/", follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["Location"]

    client.post("/login", json={"password": "secret-de-test"})
    # Connecte, la requete traverse le controle et va jusqu'au proxy ; le
    # port 9101 n'ecoute pas ici, donc 502 -- mais plus de redirection.
    assert client.get("/prive/", follow_redirects=False).status_code != 302


def test_une_application_sans_champ_reste_publique(tmp_path):
    """Les applications declarees avant ce reglage ne doivent pas se fermer
    toutes seules a la mise a jour."""
    assert app.visibilite({"port": 9101}) == app.VISIBILITE_PUBLIQUE
    assert app.visibilite({"visibility": "n'importe quoi"}) == app.VISIBILITE_PUBLIQUE


def test_le_code_a_six_chiffres_suit_la_norme():
    """Vecteur de la RFC 6238 : le secret "12345678901234567890" en base32,
    a l'instant 59, donne 287082. S'il change, aucune application
    d'authentification du marche ne saura plus se synchroniser."""
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert app.totp_code(secret, 59 // app.TOTP_PAS) == "287082"


def test_le_code_tolere_un_intervalle_mais_pas_deux(monkeypatch):
    secret = app.totp_nouveau_secret()
    maintenant = int(time.time()) // app.TOTP_PAS
    assert app.totp_verifie(secret, app.totp_code(secret, maintenant))
    assert app.totp_verifie(secret, app.totp_code(secret, maintenant - 1))
    assert not app.totp_verifie(secret, app.totp_code(secret, maintenant - 4))
    assert not app.totp_verifie(secret, "000000")
    assert not app.totp_verifie(secret, "")
    assert not app.totp_verifie("", "123456")     # secret vide = desactive


def test_sans_secret_la_connexion_se_fait_au_seul_mot_de_passe(client):
    assert not app.totp_actif()
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 200


def test_avec_un_secret_le_mot_de_passe_seul_ne_suffit_plus(client, monkeypatch):
    monkeypatch.setattr(app, "_totp_secret", app.totp_nouveau_secret())
    assert client.post("/login", json={"password": "secret-de-test"}).status_code == 401
    bon = app.totp_code(app._totp_secret, int(time.time()) // app.TOTP_PAS)
    assert client.post("/login", json={"password": "secret-de-test", "code": bon}).status_code == 200


# --------------------- 4. isolation de ce qui est lance ---------------------

def test_le_cookie_du_panneau_ne_part_pas_dans_l_application():
    """Les applications sont servies sur la meme origine que le panneau : le
    navigateur leur envoie le cookie admin, et le proxy le relayait."""
    nom = app.flask_app.config.get("SESSION_COOKIE_NAME") or "session"
    reste = app.strip_session_cookie(f"theme=dark; {nom}=SECRET; {nom}_id=garde-moi")
    assert "SECRET" not in reste
    # Les cookies de l'application passent, y compris ceux au nom voisin.
    assert "theme=dark" in reste and f"{nom}_id=garde-moi" in reste


def test_les_privileges_sont_abandonnes_dans_le_bon_ordre(monkeypatch):
    """credentials.env est monte en 0600 root : un enfant lance en root le
    lisait. setgroups et setgid AVANT setuid -- apres, le processus ne peut
    plus changer ses groupes et garderait ceux de root."""
    ordre = []
    monkeypatch.setattr(app.os, "geteuid", lambda: 0)
    monkeypatch.setattr(app.os, "setgroups", lambda g: ordre.append(("setgroups", tuple(g))))
    monkeypatch.setattr(app.os, "setgid", lambda g: ordre.append(("setgid", g)))
    monkeypatch.setattr(app.os, "setuid", lambda u: ordre.append(("setuid", u)))
    monkeypatch.setattr(app.os, "umask", lambda m: ordre.append(("umask", m)))
    app.drop_privileges()
    assert ordre == [
        ("setgroups", (app.RUN_AS_GID,)),
        ("setgid", app.RUN_AS_GID),
        ("setuid", app.RUN_AS_UID),
        ("umask", 0o002),
    ]
    assert app.RUN_AS_UID != 0 and app.RUN_AS_GID != 0


def test_une_application_sans_limite_memoire_abandonne_quand_meme_ses_privileges(monkeypatch):
    """La limite memoire est optionnelle, l'abandon des privileges ne l'est
    pas : les deux passaient autrefois par le meme preexec_fn conditionnel."""
    ordre = []
    monkeypatch.setattr(app.resource, "setrlimit", lambda *a: ordre.append("rlimit"))
    monkeypatch.setattr(app, "drop_privileges", lambda nom=None: ordre.append("drop"))
    app.child_setup()()
    assert ordre == ["drop"]
    ordre.clear()
    app.child_setup(64)()
    assert ordre == ["rlimit", "drop"]   # la limite avant la bascule


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

def _amorcage(tmp_path, monkeypatch, avec_projet=True):
    etat = tmp_path / "etat"
    etat.mkdir()
    racine = tmp_path / "workspace"
    racine.mkdir()
    if avec_projet:
        (racine / "diagnostic").mkdir()
        (racine / "diagnostic" / "app.py").write_text("")
    monkeypatch.setattr(app, "APPS_FILE", str(etat / "apps.json"))
    monkeypatch.setattr(app, "ROOT", str(racine))
    app._apps_cache["signature"] = None
    return racine


def test_le_diagnostic_est_inscrit_et_configure(tmp_path, monkeypatch):
    """« Deja cree ET configure » : la fiche n'a pas de formulaire.

    Une application par defaut ne se configure pas depuis le panneau. Tout ce
    dont elle a besoin doit donc etre pose a l'inscription -- a commencer par
    la commande de build, sans laquelle le pilote Postgres ne s'installe sur
    ordre de personne.
    """
    racine = _amorcage(tmp_path, monkeypatch)
    assert app.amorcer_diagnostic() == "diagnostic"
    inscrit = app.load()["diagnostic"]
    assert inscrit["path"] == str(racine / "diagnostic")
    assert inscrit["command"] == app.DIAGNOSTIC_COMMANDE
    assert inscrit["build_command"] == app.DIAGNOSTIC_BUILD
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
    app.amorcer_diagnostic()
    app.save({})
    assert app.amorcer_diagnostic() == "diagnostic"
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
    assert app.amorcer_diagnostic() == "diagnostic"
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
    app.amorcer_diagnostic()
    port = app.load()["diagnostic"]["port"]
    assert app.amorcer_diagnostic() is None
    assert app.load()["diagnostic"]["port"] == port


def test_une_configuration_derivee_est_remise_droite(tmp_path, monkeypatch):
    """Ce que le panneau remet, et ce qu'il laisse.

    Le chemin, la commande et le build ne sont pas configurables pour cette
    application-la : les laisser deriver n'apporte rien et prive du
    diagnostic. Le reste -- ce que l'utilisateur PEUT changer, comme la
    visibilite -- n'est pas touche.
    """
    racine = _amorcage(tmp_path, monkeypatch)
    app.amorcer_diagnostic()
    apps = app.load()
    apps["diagnostic"]["command"] = "sleep 1"
    apps["diagnostic"]["build_command"] = ""
    apps["diagnostic"]["visibility"] = app.VISIBILITE_PUBLIQUE
    app.save(apps)

    app.amorcer_diagnostic()

    remis = app.load()["diagnostic"]
    assert remis["command"] == app.DIAGNOSTIC_COMMANDE
    assert remis["build_command"] == app.DIAGNOSTIC_BUILD
    assert remis["path"] == str(racine / "diagnostic")
    assert remis["visibility"] == app.VISIBILITE_PUBLIQUE


def test_un_projet_pas_encore_amorce_est_retente_au_demarrage_suivant(tmp_path, monkeypatch):
    """app-manager et dagster demarrent en parallele, et c'est dagster qui
    depose le projet dans /workspace : au premier demarrage le dossier peut
    ne pas encore exister. On ne peut pas inscrire un chemin absent -- mais
    renoncer DEFINITIVEMENT, comme avant, privait du projet pour une simple
    question d'ordre de demarrage."""
    racine = _amorcage(tmp_path, monkeypatch, avec_projet=False)
    assert app.amorcer_diagnostic() is None
    assert app.load() == {}

    (racine / "diagnostic").mkdir()
    (racine / "diagnostic" / "app.py").write_text("")
    assert app.amorcer_diagnostic() == "diagnostic"


# ---------------- 6. secrets transmis aux applications ----------------
#
# credentials.env est en 0600 root ; les applications tournent sous l'uid
# 1001 et ne peuvent donc pas le lire, alors que c'est la que la
# documentation leur dit de prendre le mot de passe Postgres. Le panneau le
# lit pour elles et le transmet par l'environnement -- sans son propre bloc.

def _credentials(tmp_path, monkeypatch, contenu):
    fichier = tmp_path / "credentials.env"
    fichier.write_text(contenu)
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(fichier))
    return fichier


def test_le_mot_de_passe_du_panneau_n_est_pas_transmis_aux_applications(tmp_path, monkeypatch):
    """Le defaut a empecher : une application est du code arbitraire tournant
    sous un autre uid. Lui donner le mot de passe admin annulerait la
    separation pour lui offrir l'acces au panneau."""
    _credentials(tmp_path, monkeypatch, "\n".join([
        "# ===== codelab-postgres =====",
        "POSTGRES_PASSWORD=mdp-postgres",
        "# ===== /codelab-postgres =====",
        "APP_MANAGER_ADMIN_PASSWORD=mdp-panneau",
        "APP_MANAGER_SESSION_SECRET=cle-de-session",
        "APP_MANAGER_TOTP_SECRET=second-facteur",
    ]))
    partages = app.secrets_partages()
    assert partages["POSTGRES_PASSWORD"] == "mdp-postgres"
    assert not [c for c in partages if c.startswith("APP_MANAGER_")]


def test_le_bloc_d_alertes_du_panneau_n_est_pas_transmis_aux_applications(tmp_path, monkeypatch):
    """Le meme defaut que ci-dessus, par une autre porte.

    La regle "le panneau ne transmet pas son propre bloc" reposait sur le
    prefixe APP_MANAGER_. Le bloc d'alertes n'en porte pas -- il ne le peut
    pas, le capteur Dagster lit ces noms-la dans ce fichier -- et passait
    donc entier dans l'environnement de chaque application : serveur
    d'envoi, identifiant, MOT DE PASSE, et l'adresse de l'administrateur.

    Ce que ca donnait : n'importe quelle application deployee pouvait
    expedier du courrier au nom de CodeLab, depuis l'adresse meme d'ou
    partent les alertes.
    """
    _credentials(tmp_path, monkeypatch, "\n".join([
        "POSTGRES_PASSWORD=mdp-postgres",
        "SMTP_HOST=smtp.example.com",
        "SMTP_PORT=587",
        "SMTP_TLS=starttls",
        "SMTP_USER=panneau@example.com",
        "SMTP_PASSWORD=mot-de-passe-d-application",
        "ALERTE_FROM=panneau@example.com",
        "ALERTE_ADMIN=admin@example.com",
        "API_TOKEN=jeton-metier",
    ]))
    partages = app.secrets_partages()
    assert partages == {"POSTGRES_PASSWORD": "mdp-postgres",
                        "API_TOKEN": "jeton-metier"}
    # Nomme la cle la plus grave separement : si la liste d'exclusion venait a
    # etre reduite un jour, l'echec doit designer ce qui a fuite.
    assert "SMTP_PASSWORD" not in partages


def test_le_panneau_lit_toujours_son_propre_bloc_d_alertes(tmp_path, monkeypatch):
    """Le pendant du test precedent, et la raison d'etre de la separation.

    Retirer ces cles de secrets_partages() ne doit rien retirer au panneau :
    c'est sa configuration d'envoi de repli. Sans cette verification, le
    correctif pourrait couper les alertes sans que rien ne le signale --
    exactement la panne silencieuse qu'elles servent a eviter.
    """
    _credentials(tmp_path, monkeypatch, "\n".join([
        "SMTP_HOST=smtp.example.com",
        "SMTP_USER=panneau@example.com",
        "SMTP_PASSWORD=mot-de-passe-d-application",
    ]))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    origine = app.smtp_origine()
    assert origine["host"] == "smtp.example.com"
    assert origine["password"] == "mot-de-passe-d-application"


def test_la_liste_d_exclusion_suit_les_champs_smtp(tmp_path, monkeypatch):
    """CLES_PANNEAU derive de CHAMPS_SMTP plutot que d'etre recopiee.

    Ajouter un champ d'envoi sans penser a l'exclure rouvrirait la fuite en
    silence. Ce test tient ce lien : c'est la propriete, pas la liste.
    """
    assert set(app.CHAMPS_SMTP.values()) <= app.CLES_PANNEAU


def test_une_cle_reservee_ne_peut_pas_casser_le_lancement(tmp_path, monkeypatch):
    """Une ligne PATH= ajoutee a la main casserait sinon toutes les
    applications d'un coup, sans rien pour l'expliquer."""
    _credentials(tmp_path, monkeypatch,
                 "PATH=/casse-tout\nHOME=/nulle-part\nPORT=1\nAPI_TOKEN=jeton\n")
    partages = app.secrets_partages()
    assert partages == {"API_TOKEN": "jeton"}


def test_la_derniere_occurrence_gagne_et_les_guillemets_sautent(tmp_path, monkeypatch):
    """Chaque service reecrit son bloc en fin de fichier : une valeur laissee
    plus haut est perimee. Et les guillemets, qu'on met par reflexe, donnent
    un mot de passe faux s'ils sont conserves."""
    _credentials(tmp_path, monkeypatch, "\n".join([
        "# commentaire",
        "",
        "ligne malformee sans egal",
        'POSTGRES_PASSWORD="perime"',
        "POSTGRES_PASSWORD='a-jour'",
    ]))
    assert app.secrets_partages() == {"POSTGRES_PASSWORD": "a-jour"}


def test_un_fichier_illisible_ne_empeche_pas_de_lancer(tmp_path, monkeypatch):
    """Volume config non monte : les applications se debrouillent avec leur
    propre .env, comme avant -- le panneau ne doit pas refuser de lancer."""
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "absent.env"))
    assert app.secrets_partages() == {}


# ------------------- 7. alertes par mail -------------------
#
# Une alerte qui part en boucle est pire que pas d'alerte : la boite se
# remplit, et on prend l'habitude de ne plus la lire. Ces tests tiennent la
# seule propriete qui compte vraiment -- un incident, un mail.

@pytest.fixture
def alertes(tmp_path, monkeypatch):
    """Un panneau avec une application declaree et les alertes actives."""
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    app._apps_cache["signature"] = None
    app._alertes_en_cours.clear()
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n")
    app.ecrire_alertes(True, ["moi@example.com"])
    app.save({"site": {"path": "/workspace/site", "command": "python3 app.py",
                       "port": 9101, "enabled": True}})
    envoyes = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None:
                        envoyes.append(sujet))
    return envoyes


def _etat(monkeypatch, tourne, en_boucle):
    monkeypatch.setattr(app, "is_running", lambda n: tourne)
    monkeypatch.setattr(app, "is_crash_looping", lambda n: en_boucle)


def test_une_application_tombee_alerte_une_seule_fois(alertes, monkeypatch):
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    assert alertes == ["[CodeLab] site est tombee"]
    # Le moniteur repasse toutes les 10 secondes : sans memoire de l'incident,
    # c'est un mail toutes les 10 secondes tant que l'application est a terre.
    app.alerte_tick()
    app.alerte_tick()
    assert len(alertes) == 1


def test_le_retour_a_la_normale_est_signale_puis_oublie(alertes, monkeypatch):
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    _etat(monkeypatch, tourne=True, en_boucle=False)
    app.alerte_tick()
    assert alertes == ["[CodeLab] site est tombee", "[CodeLab] site est revenue"]
    app.alerte_tick()
    assert len(alertes) == 2   # l'incident est clos, plus rien a dire


def test_un_plantage_rattrape_par_un_redemarrage_n_alerte_pas(alertes, monkeypatch):
    """Le filet de securite qui fonctionne n'est pas un incident : une
    application relancee avec succes ne doit reveiller personne."""
    _etat(monkeypatch, tourne=False, en_boucle=False)
    app.alerte_tick()
    assert alertes == []


def test_un_arret_volontaire_n_alerte_pas(alertes, monkeypatch):
    """Personne n'a besoin d'un mail pour une action qu'il vient de faire."""
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    apps = app.load()
    apps["site"]["enabled"] = False       # arret depuis le panneau
    app.save(apps)
    app.alerte_tick()
    assert len(alertes) == 1              # ni deuxieme alerte, ni mail de retour
    assert app._alertes_en_cours == set()


def test_l_interrupteur_coupe_vraiment_les_alertes(alertes, monkeypatch):
    app.ecrire_alertes(False, ["moi@example.com"])
    _etat(monkeypatch, tourne=False, en_boucle=True)
    app.alerte_tick()
    assert alertes == []


def test_la_configuration_incomplete_est_dite_champ_par_champ(tmp_path, monkeypatch):
    """"Ca ne marche pas" est inutilisable ; le nom de la cle manquante se
    corrige en dix secondes."""
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    (tmp_path / "credentials.env").write_text("")
    app.ecrire_alertes(True, [])
    _, manquants = app.config_smtp()
    assert manquants == ["serveur d'envoi", "adresse d'expedition",
                         "adresse d'alerte de l'administrateur"]


def test_les_adresses_saisies_sont_nettoyees():
    """Saisie humaine : virgules, espaces, doublons, ligne vide."""
    assert app._adresses("moi@example.com, autre@example.com ,moi@example.com,") == [
        "moi@example.com", "autre@example.com"]
    assert app._adresses(["pas-une-adresse", " ok@example.com "]) == ["ok@example.com"]


# ------------------- 8. deux espaces : admin et utilisateur -------------------
#
# Le controle des droits est fait dans les routes, jamais dans l'interface :
# masquer un bouton ne protege rien, la route reste appelable a la main. Ces
# tests appellent donc les routes directement, comme le ferait quelqu'un qui
# a lu le code de la page.

@pytest.fixture
def deux_espaces(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)   # 200 000 par test, c'est long
    monkeypatch.setattr(app, "is_running", lambda n: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({
        "prive-autorise": {"path": "/w/a", "command": "x", "port": 9101,
                           "enabled": True, "visibility": "privee"},
        "prive-refuse": {"path": "/w/b", "command": "x", "port": 9102,
                         "enabled": True, "visibility": "privee"},
        "public": {"path": "/w/c", "command": "x", "port": 9103,
                   "enabled": True, "visibility": "publique"},
    })
    sel = "aa" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["prive-autorise"], "cree": 0}})
    return app.flask_app.test_client()


def _connecte(client, nom, mdp):
    """Connexion complete d'un compte utilisateur, second facteur compris.

    Ces comptes n'ouvrent jamais de session sur le seul mot de passe : au
    premier acces le serveur renvoie une cle a enregistrer, ensuite il exige
    un code. Les deux cas sont traites ici pour que les tests de droits
    parlent de droits et pas d'authentification.
    """
    r = client.post("/login", json={"nom": nom, "password": mdp})
    assert r.status_code in (200, 401), r.data
    d = r.get_json()

    if d.get("inscription"):
        secret = d["secret"]
        r = client.post("/login/second-facteur",
                        json={"code": app.totp_code(secret, int(time.time()) // app.TOTP_PAS)})
        assert r.status_code == 200, r.data
        return r.get_json()

    if d.get("totp"):
        secret = app.lire_utilisateurs()[nom]["totp"]
        r = client.post("/login", json={"nom": nom, "password": mdp,
                                        "code": app.totp_code(secret, int(time.time()) // app.TOTP_PAS)})
        assert r.status_code == 200, r.data
        return r.get_json()

    assert r.status_code == 200, r.data
    return d


def test_un_utilisateur_ne_peut_rien_administrer(deux_espaces):
    """Le coeur du sujet : un compte utilisateur ne deploie pas, ne configure
    pas, ne cree pas de compte -- meme en appelant les routes a la main."""
    c = deux_espaces
    assert _connecte(c, "marie", "mot-de-passe-long")["role"] == "utilisateur"
    for methode, route in [("get", "/api/apps"), ("post", "/api/add"),
                           ("post", "/api/toggle/public"), ("post", "/api/deploy/public"),
                           ("delete", "/api/app/public"), ("get", "/api/utilisateurs"),
                           ("post", "/api/utilisateurs"), ("get", "/api/alertes"),
                           ("get", "/api/logs/public"),
                           ("get", "/api/apps/public/acces"),
                           ("put", "/api/apps/public/acces"),
                           ("put", "/api/alertes/application/public"),
                           ("get", "/api/vps"), ("put", "/api/vps"),
                           ("get", "/api/browse")]:
        r = getattr(c, methode)(route, json={})
        assert r.status_code == 403, f"{methode.upper()} {route} a repondu {r.status_code}"


def test_un_utilisateur_ne_voit_que_ses_projets(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    noms = [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]
    assert noms == ["prive-autorise"]

    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    noms = [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]
    assert noms == ["prive-autorise", "prive-refuse", "public"]


def test_un_projet_prive_non_autorise_reste_ferme(deux_espaces, monkeypatch):
    """Connaitre l'adresse ne suffit pas : le refus est dans le proxy, pas
    dans la liste affichee."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/prive-refuse/").status_code == 403
    # Et l'application autorisee, elle, est bien servie : on s'arrete juste
    # avant la connexion reelle au port de l'application.
    monkeypatch.setattr(app, "is_running", lambda n: False)
    assert c.get("/prive-autorise/").status_code == 503   # "arretee", pas "refuse"


def test_un_projet_public_reste_ouvert_sans_compte(deux_espaces, monkeypatch):
    """La visibilite publique est ce qui permet de partager un lien : les
    comptes ne doivent pas l'avoir refermee au passage."""
    monkeypatch.setattr(app, "is_running", lambda n: False)
    assert deux_espaces.get("/public/").status_code == 503   # servie, mais arretee


def test_dagster_reste_reserve_a_l_administrateur(deux_espaces):
    """L'interface de Dagster lance des jobs, donc execute du code : y donner
    acces a un compte utilisateur serait lui donner l'administration."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/auth-check").status_code == 401
    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    assert c.get("/api/auth-check").status_code == 204


def test_une_session_ouverte_avant_les_comptes_reste_administratrice(deux_espaces):
    """Mise a jour d'une installation en service : les seules sessions qui
    existaient venaient du panneau d'administration. Les degrader
    deconnecterait l'administrateur de son propre panneau."""
    c = deux_espaces
    with c.session_transaction() as s:
        s["authed"] = True          # session d'avant, sans role enregistre
    assert c.get("/api/apps").status_code == 200


def test_le_nom_du_compte_d_administration_ne_peut_pas_etre_repris(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/utilisateurs", json={"nom": "admin", "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 400
    assert "administration" in r.get_json()["error"]


def test_les_mots_de_passe_sont_derives_et_sales(deux_espaces):
    """Deux comptes avec le meme mot de passe ne doivent pas donner la meme
    empreinte : sinon le fichier revele qui partage un mot de passe."""
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    for nom in ("paul", "jean"):
        assert c.post("/api/utilisateurs",
                      json={"nom": nom, "mot_de_passe": "le-meme-mot-de-passe"}).status_code == 200
    comptes = app.lire_utilisateurs()
    assert comptes["paul"]["hash"] != comptes["jean"]["hash"]
    assert "le-meme-mot-de-passe" not in json.dumps(comptes)
    assert app.verifie_mot_de_passe(comptes["paul"], "le-meme-mot-de-passe")
    assert not app.verifie_mot_de_passe(comptes["paul"], "presque-le-meme")


def test_un_droit_sur_un_projet_inexistant_n_est_pas_enregistre(deux_espaces):
    """Un projet supprime puis recree sous le meme nom rendrait sinon un
    droit qu'on croyait perdu."""
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.put("/api/utilisateurs/marie", json={"projets": ["public", "jamais-declare"]})
    assert r.status_code == 200
    assert app.lire_utilisateurs()["marie"]["projets"] == ["public"]


# ---------- assistant de liaison avec un VPS ----------
#
# Il rend les fichiers de app-manager/vps/ avec les valeurs substituees, et
# dit ce qu'il CONSTATE sur la requete en cours. Il ne se connecte jamais au
# VPS : lui confier une cle SSH avec les droits qui vont avec ferait de ce
# panneau la cible la plus interessante de l'installation.

@pytest.fixture
def vps(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "EXPOSITION_FILE", str(tmp_path / "exposition.json"))
    monkeypatch.delenv("APP_MANAGER_PUBLIC_URL", raising=False)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c


def test_les_valeurs_sont_substituees_dans_les_vrais_modeles(vps):
    """Substituer plutot que reecrire : les modeles sont la source de verite.
    Un assistant qui regenere son propre texte finit par decrire autre chose
    que ce que dit le README, et c'est toujours celui qu'on ne relit pas qui
    se trompe."""
    r = vps.put("/api/vps", json={"domaine": "codelab.chezmoi.fr",
                                  "ip": "203.0.113.10", "reseau": "10.9.0"})
    assert r.status_code == 200, r.data
    d = vps.get("/api/vps").get_json()
    if d["modeles_absents"]:
        pytest.skip("modeles vps absents de cette image")
    tout = "\n".join(f["contenu"] for f in d["fichiers"])
    assert "codelab.chezmoi.fr" in tout
    assert "codelab.exemple.fr" not in tout, "le domaine d'exemple traine encore"
    assert "10.9.0.2" in tout and "10.8.0.2" not in tout
    assert "203.0.113.10" in tout
    # Et chaque fichier dit ou il va : un contenu sans destination oblige a
    # retourner lire le README, ce que l'assistant est cense eviter.
    assert all(f["destination"] for f in d["fichiers"])


def test_chaque_fichier_dit_sur_quelle_machine_il_va(vps):
    """La seule question devant un fichier de configuration est "je le colle
    OU ?". Le cote voyage donc avec la donnee. Le deviner dans la page a
    partir du texte d'un chemin serait une regle de plus a tenir a jour
    ailleurs -- et c'est toujours celle-la qu'on oublie."""
    for cle, entree in app.VPS_FICHIERS.items():
        relatif, destination, cote, role = entree
        assert cote in (app.COTE_VPS, app.COTE_LOCAL), cle
        assert role, cle
    d = vps.get("/api/vps").get_json()
    if d["modeles_absents"]:
        pytest.skip("modeles vps absents de cette image")
    assert all(f["cote"] and f["role"] for f in d["fichiers"])
    # Les deux machines sont representees : un assistant qui n'en montrerait
    # qu'une laisserait le tunnel a moitie pose.
    assert {f["cote"] for f in d["fichiers"]} == {app.COTE_VPS, app.COTE_LOCAL}


def test_le_tunnel_est_propose_avant_nginx(vps):
    """Sans tunnel, nginx n'a personne a joindre. L'ordre des fichiers est
    l'ordre dans lequel on les pose."""
    cles = list(app.VPS_FICHIERS)
    assert cles.index("wireguard_vps") < cles.index("nginx")
    assert cles.index("wireguard_local") < cles.index("nginx")
    d = vps.get("/api/vps").get_json()
    if d["modeles_absents"]:
        pytest.skip("modeles vps absents de cette image")
    rendus = [f["cle"] for f in d["fichiers"]]
    assert rendus.index("wireguard_vps") < rendus.index("nginx")


def test_la_destination_ne_repete_pas_la_machine(vps):
    """La destination est un chemin a coller dans un terminal. Y glisser
    "(sur le VPS)" donnait une commande fausse des qu'on la copiait."""
    for cle, (_, destination, _, _) in app.VPS_FICHIERS.items():
        assert "(" not in destination, cle
        assert destination.startswith("/"), cle


def test_une_adresse_privee_est_refusee(vps):
    """Un VPS joignable depuis internet n'a pas une adresse privee. Saisir
    celle de sa propre machine donnerait une configuration qui ne peut pas
    marcher -- autant le dire tout de suite qu'apres trois copies."""
    r = vps.put("/api/vps", json={"domaine": "x.fr", "ip": "192.168.1.50"})
    assert r.status_code == 400, r.data
    # Fragment sans accent : ce fichier est du code, et le code de ce depot
    # s'ecrit sans accents. Le message, lui, en porte -- il est lu par un humain.
    assert "adresse publique" in r.get_json()["error"]
    assert app.lire_vps()["ip"] == ""


def test_un_reseau_de_tunnel_mal_forme_est_refuse(vps):
    r = vps.put("/api/vps", json={"reseau": "pas-un-reseau"})
    assert r.status_code == 400, r.data


def test_le_diagnostic_ne_dit_que_ce_qu_il_constate(vps):
    """Sans proxy devant, rien n'est annonce : le panneau doit le dire au lieu
    de supposer que le tunnel est en place."""
    d = vps.get("/api/vps").get_json()
    etapes = {e["etape"]: e["ok"] for e in d["diagnostic"]}
    assert etapes["Un interm\u00e9diaire relaie cette requ\u00eate"] is False
    assert etapes["La requ\u00eate arrive en HTTPS"] is False
    # Toute etape non faite doit porter la marche a suivre : un diagnostic qui
    # dit "non" sans dire quoi faire ne sert qu'a inquieter.
    assert all(e["aide"] for e in d["diagnostic"] if not e["ok"])


def test_le_diagnostic_voit_le_proxy_quand_il_est_la(vps):
    d = vps.get("/api/vps", headers={"X-Forwarded-For": "203.0.113.7",
                                     "X-Forwarded-Proto": "https"}).get_json()
    etapes = {e["etape"]: e["ok"] for e in d["diagnostic"]}
    assert etapes["Un interm\u00e9diaire relaie cette requ\u00eate"] is True
    assert etapes["La requ\u00eate arrive en HTTPS"] is True
    # Le proxy n'est pas declare pour autant : constater n'est pas croire.
    assert etapes["Le proxy est d\u00e9clar\u00e9 de confiance"] is False


def test_enregistrer_le_vps_n_efface_pas_les_autres_reglages(vps):
    """exposition.json porte desormais quatre reglages ET le bloc VPS. Le
    reecrire en entier a chaque enregistrement effacerait le reste."""
    vps.put("/api/securite/exposition", json={"adresse_publique": "https://codelab.chezmoi.fr"})
    vps.put("/api/vps", json={"domaine": "codelab.chezmoi.fr", "ip": "203.0.113.10"})
    assert app.adresse_publique() == "https://codelab.chezmoi.fr", (
        "l'adresse publique a ete effacee par l'enregistrement du VPS")
    assert app.lire_vps()["domaine"] == "codelab.chezmoi.fr"


# ---------- changer son propre mot de passe ----------
#
# Cela n'existait pas : le mot de passe d'administration ne se changeait
# qu'en editant credentials.env sur le serveur, donc en s'y connectant en
# SSH. Un secret qu'on ne peut pas changer facilement est un secret qu'on ne
# change jamais -- et celui-la donne l'execution de commandes sur la machine.

@pytest.fixture
def compte_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "ancien-mot-de-passe")
    monkeypatch.setattr(app, "_totp_secret", "")
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app.ecrire_bloc_panneau("ancien-mot-de-passe", "cle-de-session-existante", "")
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "ancien-mot-de-passe"})
    return c


def test_l_admin_ne_change_pas_son_mot_de_passe_depuis_le_panneau(compte_admin):
    """CE QUI A CHANGE, ET POURQUOI. Le panneau reecrivait credentials.env.
    Or ce fichier est LA source : c'est lui qu'on lit pour se depanner quand
    le panneau ne repond plus, lui qu'on copie en changeant de machine, lui
    que le diagnostic controle. Deux sources pour un meme secret, c'est
    decouvrir laquelle fait foi le jour ou elles divergent -- au pire moment.
    """
    r = compte_admin.post("/api/compte/mot-de-passe",
                          json={"ancien": "ancien-mot-de-passe",
                                "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert "credentials.env" in r.get_json()["error"]
    # Rien n'a bouge, ni en memoire ni sur le disque.
    assert app.admin_password() == "ancien-mot-de-passe"
    assert app.read_shared_value("APP_MANAGER_ADMIN_PASSWORD") == "ancien-mot-de-passe"
    assert app.read_shared_value("APP_MANAGER_SESSION_SECRET") == "cle-de-session-existante"


def test_une_session_volee_ne_verrouille_pas_le_compte(deux_espaces):
    """L'ancien mot de passe est exige meme sur une session deja ouverte :
    sans cela, un cookie capture suffirait a prendre la place de quelqu'un
    definitivement."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "pas-le-bon", "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"],
                                    "mot-de-passe-long")


def test_un_mot_de_passe_trop_court_est_refuse(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "mot-de-passe-long", "nouveau": "court"})
    assert r.status_code == 400, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"],
                                    "mot-de-passe-long")


def test_un_compte_nomme_change_le_sien_et_pas_celui_de_l_admin(deux_espaces):
    """Chacun le sien : la route ne prend aucun nom en parametre, elle agit
    sur la session qui appelle."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "mot-de-passe-long", "nouveau": "un-autre-mot-de-passe"})
    assert r.status_code == 200, r.data
    comptes = app.lire_utilisateurs()
    assert app.verifie_mot_de_passe(comptes["marie"], "un-autre-mot-de-passe")
    assert not app.verifie_mot_de_passe(comptes["marie"], "mot-de-passe-long")
    # Le sel a change aussi : deux mots de passe identiques ne doivent pas
    # produire la meme empreinte d'un compte a l'autre.
    assert comptes["marie"]["sel"] != "aa" * 16


def test_un_compte_nomme_doit_donner_son_ancien_mot_de_passe(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "pas-le-bon", "nouveau": "un-autre-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"], "mot-de-passe-long")


# ---------- l'acces a une application, vu depuis l'application ----------
#
# La meme information que dans la fiche d'un compte, prise par l'autre bout.
# Elle n'existait que dans un sens : pour savoir qui ouvrait une application
# il fallait ouvrir les fiches une par une, et pour l'accorder a cinq
# personnes, cinq allers-retours.

@pytest.fixture
def acces(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"facturation": {"path": "/w/f", "command": "x", "port": 9101,
                              "enabled": True, "visibility": "privee"},
              "vitrine": {"path": "/w/v", "command": "x", "port": 9102,
                          "enabled": True, "visibility": "publique"}})
    sel = "cc" * 16
    app.ecrire_utilisateurs({
        "marie": {"sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
                  "projets": ["vitrine"], "cree": 0, "email": "marie@example.com"},
        "paul": {"sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
                 "projets": [], "cree": 0},
    })
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c


def test_l_application_dit_qui_l_ouvre(acces):
    d = acces.get("/api/apps/facturation/acces").get_json()
    assert [c["nom"] for c in d["comptes"]] == ["marie", "paul"]
    assert all(c["acces"] is False for c in d["comptes"])
    assert d["publique"] is False
    # L'adresse aide a distinguer deux homonymes ; elle est deja visible
    # ailleurs dans le panneau pour un administrateur.
    assert d["comptes"][0]["email"] == "marie@example.com"


def test_accorder_l_acces_depuis_l_application_n_efface_pas_les_autres_projets(acces):
    """La propriete qui compte. Envoyer la liste complete des projets d'un
    compte aurait efface en silence ce qu'un autre onglet venait d'accorder :
    on n'ecrit donc QUE cette application dans chaque fiche."""
    r = acces.put("/api/apps/facturation/acces", json={"utilisateurs": ["marie"]})
    assert r.status_code == 200, r.data
    comptes = app.lire_utilisateurs()
    assert comptes["marie"]["projets"] == ["facturation", "vitrine"], (
        "l'acces accorde ailleurs a ete efface")
    assert comptes["paul"]["projets"] == []


def test_retirer_l_acces_ne_retire_que_celui_la(acces):
    acces.put("/api/apps/facturation/acces", json={"utilisateurs": ["marie"]})
    r = acces.put("/api/apps/facturation/acces", json={"utilisateurs": []})
    assert r.status_code == 200, r.data
    assert app.lire_utilisateurs()["marie"]["projets"] == ["vitrine"]


def test_une_application_publique_le_dit(acces):
    """Elle s'ouvre sans compte : laisser croire que cocher quelqu'un y change
    quelque chose serait pire que de ne rien afficher."""
    assert acces.get("/api/apps/vitrine/acces").get_json()["publique"] is True


def test_un_compte_inconnu_est_refuse_sans_rien_ecrire(acces):
    r = acces.put("/api/apps/facturation/acces",
                  json={"utilisateurs": ["marie", "fantome"]})
    assert r.status_code == 400, r.data
    assert "fantome" in r.get_json()["error"]
    assert app.lire_utilisateurs()["marie"]["projets"] == ["vitrine"], (
        "un refus a quand meme modifie les comptes")


def test_une_application_inconnue_repond_404(acces):
    assert acces.get("/api/apps/absente/acces").status_code == 404
    assert acces.put("/api/apps/absente/acces",
                     json={"utilisateurs": []}).status_code == 404


# ---------- 9. second facteur obligatoire pour les comptes utilisateurs ----------
#
# Ces comptes existent pour etre distribues : leur mot de passe circule par un
# canal qu'on ne maitrise pas, et sera reutilise ailleurs. Le point a tenir
# est qu'un mot de passe seul n'ouvre JAMAIS de session -- ni avant
# l'inscription du facteur, ni apres.

def _code_valide(secret):
    return app.totp_code(secret, int(time.time()) // app.TOTP_PAS)


def test_le_mot_de_passe_seul_n_ouvre_aucune_session(deux_espaces):
    """Le premier acces renvoie une cle a enregistrer, pas une session : entre
    les deux, le cookie ne vaut rien."""
    c = deux_espaces
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert r.status_code == 200 and r.get_json()["inscription"] is True

    # La session intermediaire n'ouvre rien du tout.
    assert c.get("/api/mes-apps").status_code == 401
    assert c.get("/prive-autorise/").status_code == 302   # renvoye vers /login
    assert c.get("/", follow_redirects=False).status_code == 302


def test_l_inscription_ouvre_la_session_et_persiste_la_cle(deux_espaces):
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    # Rien n'est enregistre tant que le code n'est pas confirme : une cle mal
    # recopiee ne doit pas enfermer dehors.
    assert not app.lire_utilisateurs()["marie"].get("totp")

    assert c.post("/login/second-facteur",
                  json={"code": _code_valide(secret)}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["totp"] == secret
    assert c.get("/api/mes-apps").status_code == 200


def test_un_code_faux_n_enregistre_rien(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    r = c.post("/login/second-facteur", json={"code": "000000"})
    assert r.status_code == 400
    assert not app.lire_utilisateurs()["marie"].get("totp")
    assert c.get("/api/mes-apps").status_code == 401


def test_une_fois_inscrit_le_code_est_exige_a_chaque_connexion(deux_espaces):
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    c.post("/logout")

    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert r.status_code == 401 and r.get_json()["totp"] is True
    assert c.get("/api/mes-apps").status_code == 401

    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "code": _code_valide(secret)})
    assert r.status_code == 200
    assert c.get("/api/mes-apps").status_code == 200


def test_la_reinitialisation_par_l_admin_refait_passer_par_l_inscription(deux_espaces):
    """Telephone perdu : l'administrateur remet l'etape a zero, sans jamais
    connaitre ni transmettre la cle de quelqu'un d'autre."""
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    c.post("/logout")

    c.post("/login", json={"password": "secret-de-test"})
    assert c.get("/api/utilisateurs").get_json()["utilisateurs"][0]["totp"] is True
    assert c.put("/api/utilisateurs/marie",
                 json={"reinitialiser_totp": True}).status_code == 200
    assert c.get("/api/utilisateurs").get_json()["utilisateurs"][0]["totp"] is False
    c.post("/logout")

    # Et l'ancienne cle ne vaut plus rien : c'est une NOUVELLE inscription.
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "code": _code_valide(secret)})
    assert r.get_json()["inscription"] is True
    assert r.get_json()["secret"] != secret


def test_l_inscription_ne_remplace_pas_un_facteur_deja_en_service(deux_espaces):
    """Deux sessions ouvertes en parallele : la seconde ne doit pas ecraser la
    cle que la premiere vient d'enregistrer, sinon le telephone deja
    configure cesse de fonctionner."""
    c = deux_espaces
    secret = c.post("/login", json={"nom": "marie",
                                    "password": "mot-de-passe-long"}).get_json()["secret"]
    comptes = app.lire_utilisateurs()          # une autre session a fini avant
    comptes["marie"]["totp"] = "AUTRECLEDEJAENREGISTREE"
    app.ecrire_utilisateurs(comptes)

    r = c.post("/login/second-facteur", json={"code": _code_valide(secret)})
    assert r.status_code == 409
    assert app.lire_utilisateurs()["marie"]["totp"] == "AUTRECLEDEJAENREGISTREE"


def test_sans_l_obligation_l_administrateur_entre_au_mot_de_passe(deux_espaces):
    """APP_MANAGER_ADMIN_2FA=0 (pose par le filet) : la marche arriere du
    compose, le jour ou le telephone qui portait le facteur est perdu."""
    c = deux_espaces
    assert not app.totp_actif()
    assert c.post("/login", json={"password": "secret-de-test"}).status_code == 200


# ------------------- 10. presentation des projets -------------------

def test_une_description_collee_depuis_un_readme_est_ramenee_a_une_ligne():
    """Retours a la ligne, espaces multiples, et plus long que ce que la carte
    peut afficher : la liste doit rester une liste."""
    propre = app.description_propre("  Premiere ligne\n\n  et   la suite  " + "z" * 200)
    assert propre.startswith("Premiere ligne et la suite z")
    assert "\n" not in propre and len(propre) == app.DESCRIPTION_MAX
    assert app.description_propre(None) == ""


def test_la_description_est_nettoyee_a_la_declaration(deux_espaces, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", str(tmp_path))
    dossier = tmp_path / "nouveau"
    dossier.mkdir()
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/add", json={"name": "nouveau", "path": str(dossier),
                                 "command": "python3 app.py",
                                 "description": "Deux\nlignes"})
    assert r.status_code == 200, r.data
    assert app.load()["nouveau"]["description"] == "Deux lignes"


def test_la_description_suit_le_projet_jusqu_a_l_espace_utilisateur(deux_espaces):
    apps = app.load()
    apps["prive-autorise"]["description"] = "Le tableau de bord des ventes"
    app.save(apps)

    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    app_vue = c.get("/api/mes-apps").get_json()["apps"][0]
    assert app_vue["description"] == "Le tableau de bord des ventes"
    # Et rien de plus : l'espace utilisateur n'a pas a connaitre le chemin ni
    # la commande de lancement.
    assert "path" not in app_vue and "command" not in app_vue


# ------------------- 11. plafond des flux de journal -------------------
#
# Un flux de journal occupe un thread du serveur tant qu'il est ouvert.
# Mesure faite sur une instance reelle : avec 16 threads, 20 flux simultanes
# rendaient le panneau entierement muet -- healthcheck compris, donc le
# conteneur passait "unhealthy". Le plafond transforme cette panne totale en
# un refus lisible sur le seul flux de trop.

def test_le_plafond_de_flux_protege_le_panneau(monkeypatch):
    monkeypatch.setattr(app, "SSE_MAX_FLUX", 2)
    monkeypatch.setattr(app, "_flux_ouverts", 0)
    assert app._prendre_place_flux() is True
    assert app._prendre_place_flux() is True
    assert app._prendre_place_flux() is False   # le flux de trop est refuse
    app._rendre_place_flux()
    assert app._prendre_place_flux() is True    # une place rendue est reutilisable


def test_une_place_rendue_deux_fois_n_en_cree_pas_une_troisieme(monkeypatch):
    """call_on_close et le generateur peuvent tous deux liberer : le compteur
    ne doit pas passer sous zero, sinon le plafond monterait a chaque
    deconnexion."""
    monkeypatch.setattr(app, "SSE_MAX_FLUX", 1)
    monkeypatch.setattr(app, "_flux_ouverts", 0)
    app._prendre_place_flux()
    app._rendre_place_flux()
    app._rendre_place_flux()
    assert app._prendre_place_flux() is True
    assert app._prendre_place_flux() is False


def test_le_flux_refuse_le_dit_avec_un_code_utilisable(deux_espaces, monkeypatch):
    """503 et un message qui nomme la cause : l'interface s'en sert pour
    expliquer, au lieu de rester sur « Connexion... »."""
    monkeypatch.setattr(app, "SSE_MAX_FLUX", 0)
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.get("/api/logs/public/stream")
    assert r.status_code == 503
    assert "journaux" in r.get_json()["error"]


# ---------- 12. une seule application, un seul hub ----------
#
# CodeLab est une seule page. Le hub est l'accueil de tout le monde ; ce que
# le menu propose en plus depend du role. Ce qui doit rester vrai : la page
# est la meme pour tout le monde, mais elle sait qui la regarde, et surtout
# les ROUTES continuent de decider -- une page bricolee ne donne aucun droit.

def test_la_meme_page_est_servie_aux_deux_roles(deux_espaces):
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    page_utilisateur = c.get("/").get_data(as_text=True)
    assert '"utilisateur"' in page_utilisateur     # role injecte
    assert "sec-hub" in page_utilisateur

    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    page_admin = c.get("/").get_data(as_text=True)
    assert '"admin"' in page_admin
    # Les memes sections sont servies aux deux roles : seul le role injecte
    # change. C'est lui, et les routes, qui decident de ce qui est utilisable.
    for section in ("sec-hub", "sec-overview", "sec-apps", "sec-parametres", "sec-users"):
        assert f'id="{section}"' in page_admin
        assert f'id="{section}"' in page_utilisateur


def test_le_hub_est_l_accueil_des_deux_roles(deux_espaces):
    """L'administrateur atterrit sur le hub, comme tout le monde.

    Il n'y a plus de bascule « Hub / Developpeur » : la page s'ouvre sur le
    lanceur, et la console d'administration s'atteint par le menu.
    """
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    page = c.get("/").get_data(as_text=True)
    # En debut de ligne : l'appel du demarrage, pas un onclick de menu.
    assert "\nshowSection('hub');\n" in page
    assert "mode-toggle" not in page and "setMode" not in page
    # Le pied du menu lateral annoncait une evidence : il n'est plus la.
    assert "Serveur en service" not in page


def test_les_comptes_ont_leur_propre_entree_de_menu(deux_espaces):
    """Gerer qui entre n'est pas un reglage du serveur.

    Les comptes vivaient dans Configuration, entre les categories et les
    alertes. Ils ont leur section, et le menu lateral y mene.
    """
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    page = c.get("/").get_data(as_text=True)
    assert 'id="sec-users"' in page
    assert 'data-sec="users"' in page
    # La liste des comptes se dessine dans la section Utilisateurs, et nulle
    # part ailleurs : la dedoubler ferait diverger deux formulaires qui
    # ecrivent le meme fichier.
    assert page.count('id="us-liste"') == 1
    avant, apres = page.split('id="sec-users"', 1)
    assert 'id="us-liste"' in apres and 'id="us-liste"' not in avant


def test_les_parametres_tiennent_en_un_seul_endroit(deux_espaces):
    """Une seule entree de menu, et des onglets derriere.

    « Parametres » et « Configuration » etaient deux portes pour la meme
    piece : il fallait se demander dans laquelle chercher. Les reglages du
    serveur sont maintenant des onglets, montres au seul administrateur.
    """
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    page = c.get("/").get_data(as_text=True)
    assert 'id="sec-parametres"' in page
    assert "acct-configuration" not in page   # l'entree en double a disparu
    # Les onglets d'administration sont dans la page, mais masques par
    # defaut (CSS) et n'apparaissent que si le role injecte est admin.
    assert '.fiche-onglets button[data-admin]{display:none}' in page
    assert "if(estAdmin) document.querySelectorAll('#param-onglets button[data-admin]')" in page
    for onglet in ("param-compte", "param-securite", "param-serveur",
                   "param-categories", "param-email"):
        assert f'id="{onglet}"' in page
    # Les alertes vivent avec le serveur d'envoi, dans un seul onglet.
    assert 'id="param-alertes"' not in page


def test_aucun_onglet_de_parametres_ne_reste_vide():
    """Un onglet sans rien a regler pour ce compte ne s'affiche pas."""
    page = _page_panneau("dashboard.html")
    assert "function majOngletsParametres()" in page
    assert page.count("majOngletsParametres();") >= 2


def test_l_ancienne_adresse_de_l_espace_ramene_a_la_page_unique(deux_espaces):
    """Elle a pu etre mise en favori : elle ne doit pas tomber en 404."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.get("/espace")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")


def test_le_mode_affiche_ne_donne_aucun_droit(deux_espaces):
    """Le coeur du sujet : la page connait le role pour savoir quoi afficher,
    mais un compte utilisateur qui appellerait les routes d'administration a
    la main -- ou qui modifierait la page -- reste refuse."""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/").status_code == 200          # la page, oui
    assert c.get("/api/apps").status_code == 403  # les droits, non
    assert c.get("/api/utilisateurs").status_code == 403
    # Et le hub, lui, reste servi aux deux roles.
    assert c.get("/api/mes-apps").status_code == 200


# ---------- 12 quinquies. la duree de vie d'une session ----------
#
# Deux limites, et il faut les deux. L'INACTIVITE ferme une session oubliee
# -- un poste public, un portable perdu -- au bout de trois jours ; elle est
# tenue par le cookie, dont Flask repousse la date a chaque requete. Elle ne
# dit rien d'une session ENTRETENUE : un onglet laisse ouvert sur une page
# qui interroge l'API la maintiendrait valable pour toujours. D'ou la limite
# ABSOLUE de trente jours, comptee par le serveur depuis l'ouverture.

def test_les_deux_limites_de_session_sont_posees():
    assert app.SESSION_INACTIVITE == datetime.timedelta(days=3)
    assert app.SESSION_ABSOLUE == datetime.timedelta(days=30)
    # Le cookie porte la limite d'inactivite : c'est lui qui la fait
    # respecter, y compris sur un navigateur qui ne revient jamais.
    assert app.flask_app.config["PERMANENT_SESSION_LIFETIME"] == app.SESSION_INACTIVITE


def test_une_session_ouverte_est_datee(journal):
    """Sans date d'ouverture, la limite absolue n'a rien a compter."""
    c = journal
    c.post("/login", json={"password": "secret-de-test"})
    with c.session_transaction() as sess:
        assert isinstance(sess.get("ouverte"), (int, float))
        assert abs(time.time() - sess["ouverte"]) < 30


def test_une_session_de_plus_de_trente_jours_ne_vaut_plus_rien(journal):
    """Meme entretenue : la date d'ouverture ne se repousse pas."""
    c = journal
    c.post("/login", json={"password": "secret-de-test"})
    assert c.get("/api/apps").status_code == 200
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 31 * 86400
    assert c.get("/api/apps").status_code in (401, 403)
    # Et la session est bien VIDEE, pas seulement refusee une fois.
    with c.session_transaction() as sess:
        assert sess.get("authed") is not True


def test_une_session_de_vingt_neuf_jours_tient_encore(deux_espaces):
    """La limite est une limite, pas une approximation : on ne deconnecte
    pas quelqu'un la veille. (Un compte utilisateur : l'administrateur a la
    sienne, plus courte.)"""
    c = deux_espaces
    _connecte(c, "marie", "mot-de-passe-long")
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 29 * 86400
    assert c.get("/api/mes-apps").status_code == 200


def test_la_session_d_administration_tombe_au_bout_de_douze_heures(journal):
    """L'administrateur ouvre tout, Dagster et Mailpit compris : sa session
    vit une journee de travail, pas un mois."""
    c = journal
    c.post("/login", json={"password": "secret-de-test"})
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 11 * 3600
    assert c.get("/api/apps").status_code == 200
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 13 * 3600
    assert c.get("/api/apps").status_code in (401, 403)


def test_une_session_d_avant_ce_mecanisme_n_est_pas_jetee(journal):
    """Mettre a jour le panneau ne doit deconnecter personne : une session
    sans date est datee de maintenant, pas refusee."""
    c = journal
    c.post("/login", json={"password": "secret-de-test"})
    with c.session_transaction() as sess:
        del sess["ouverte"]
    assert c.get("/api/apps").status_code == 200
    with c.session_transaction() as sess:
        assert isinstance(sess.get("ouverte"), (int, float))


def test_l_expiration_est_journalisee(journal):
    c = journal
    c.post("/login", json={"password": "secret-de-test"})
    with c.session_transaction() as sess:
        sess["ouverte"] = time.time() - 31 * 86400
    c.get("/api/apps")
    genres = [e.get("action") for e in app.lire_acces() if e.get("genre") == "session"]
    assert any("12 heures" in (a or "") for a in genres), genres


# ---------- 12 sexies. un mot laisse depuis le hub ----------
#
# Trois sujets et pas un de plus : une application a laquelle on a acces, le
# hub lui-meme, ou l'idee d'une application qui manque. Un champ libre aurait
# demande a l'administrateur de deviner de quoi on parle -- ce qu'il ne peut
# pas faire depuis un mail de trois lignes.

@pytest.fixture
def messagerie(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "MESSAGES_FILE", str(tmp_path / "messages.jsonl"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app._dernier_message.clear()
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n"
        "ALERTE_ADMIN=chef@example.com\n")
    app.save({"compta": {"path": "/w/a", "command": "x", "port": 9101,
                         "enabled": True, "visibility": "privee"},
              "secret": {"path": "/w/c", "command": "x", "port": 9103,
                         "enabled": True, "visibility": "privee"}})
    sel = "ff" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["compta"], "cree": 0}})
    partis = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None:
                        partis.append((destinataires, sujet, corps)))
    c = app.flask_app.test_client()
    _connecte(c, "marie", "mot-de-passe-long")
    return c, partis


def test_un_message_est_range_puis_envoye_a_l_administrateur(messagerie):
    c, partis = messagerie
    r = c.post("/api/messages", json={"cible": "application", "app": "compta",
                                      "texte": "Le bouton demande deux clics."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is True

    garde = app.lire_messages()
    assert len(garde) == 1
    assert garde[0]["qui"] == "marie" and garde[0]["app"] == "compta"
    assert garde[0]["texte"] == "Le bouton demande deux clics."

    destinataires, sujet, corps = partis[-1]
    assert destinataires == ["chef@example.com"]
    assert "marie" in sujet and "compta" in sujet
    assert "Le bouton demande deux clics." in corps


def test_un_message_survit_a_un_envoi_impossible(messagerie, monkeypatch):
    """ENREGISTRE D'ABORD, ENVOYE ENSUITE. Un SMTP mal configure ne doit pas
    faire perdre le message : personne ne saurait jamais qu'il a ete ecrit."""
    c, _partis = messagerie
    def refuse(*a, **kw):
        raise RuntimeError("serveur injoignable")
    monkeypatch.setattr(app, "envoyer_mail", refuse)
    r = c.post("/api/messages", json={"cible": "hub", "texte": "Le hub est lent."})
    assert r.status_code == 200, r.data
    assert r.get_json()["envoye"] is False
    assert len(app.lire_messages()) == 1


def test_on_n_ecrit_que_sur_une_application_qu_on_peut_ouvrir(messagerie):
    """Sinon ce formulaire dirait l'existence de projets qu'on n'a pas le
    droit de connaitre -- exactement comme la liste des masquees."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "application", "app": "secret",
                                         "texte": "bonjour"}).status_code == 404
    assert c.post("/api/messages", json={"cible": "application", "app": "fantome",
                                         "texte": "bonjour"}).status_code == 404
    assert app.lire_messages() == [] and partis == []


def test_une_idee_d_application_ne_porte_aucun_projet(messagerie):
    """« Une application qui manque » n'en designe aucune : garder un nom
    envoye au passage ferait croire a un message sur un projet existant."""
    c, _ = messagerie
    r = c.post("/api/messages", json={"cible": "idee", "app": "compta",
                                      "texte": "Il manque un suivi des congés."})
    assert r.status_code == 200, r.data
    assert app.lire_messages()[0]["app"] == ""


def test_un_sujet_invente_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "autre chose",
                                         "texte": "bonjour"}).status_code == 400
    assert app.lire_messages() == []


def test_un_message_vide_ou_trop_long_est_refuse(messagerie):
    c, _ = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "   "}).status_code == 400
    trop = "a" * (app.MESSAGE_LONGUEUR_MAX + 1)
    assert c.post("/api/messages", json={"cible": "hub", "texte": trop}).status_code == 400
    assert app.lire_messages() == []


def test_un_deuxieme_message_immediat_est_refuse(messagerie):
    """De quoi corriger une faute de frappe, pas de quoi remplir la boite de
    l'administrateur."""
    c, partis = messagerie
    assert c.post("/api/messages", json={"cible": "hub", "texte": "un"}).status_code == 200
    r = c.post("/api/messages", json={"cible": "hub", "texte": "deux"})
    assert r.status_code == 429, r.data
    assert len(app.lire_messages()) == 1 and len(partis) == 1


def test_seul_l_administrateur_lit_les_messages(messagerie):
    """Ce sont des mots qui lui sont adresses, et ils portent le nom de qui
    les a ecrits."""
    c, _ = messagerie
    c.post("/api/messages", json={"cible": "hub", "texte": "bonjour"})
    assert c.get("/api/messages").status_code in (401, 403)
    admin = app.flask_app.test_client()
    admin.post("/login", json={"password": "secret-de-test"})
    d = admin.get("/api/messages").get_json()
    assert [m["texte"] for m in d["messages"]] == ["bonjour"]


def test_les_messages_partent_aussi_vers_la_base():
    """Le fichier tient sans base et se lit en SSH ; la base garde tout et
    s'interroge en SQL. Le meme couple que pour le journal des acces."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS messages" in src
    assert "_pg_deposer((\"message\"" in src
    # Et le rattrapage les rejoue apres une coupure de la base.
    rattrapage = src.split("def _pg_rattraper(")[1].split("\ndef ")[0]
    assert "_pg_ecrire_messages" in rattrapage


# ---------- 12 quater. masquer une application de SON hub ----------
#
# Ce n'est ni un droit retire, ni une application arretee : le projet
# continue de tourner, les autres comptes le voient, et son adresse reste
# ouverte a qui la connait. C'est du RANGEMENT -- et l'interface doit le dire,
# pour que personne ne croie avoir ferme quelque chose.

@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "MASQUEES_FILE", str(tmp_path / "masquees.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"compta": {"path": "/w/a", "command": "x", "port": 9101,
                         "enabled": True, "visibility": "privee"},
              "vitrine": {"path": "/w/b", "command": "x", "port": 9102,
                          "enabled": True, "visibility": "publique"},
              "secret": {"path": "/w/c", "command": "x", "port": 9103,
                         "enabled": True, "visibility": "privee"}})
    sel = "dd" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["compta"], "cree": 0}})
    return app.flask_app.test_client()


def _connexion_marie(c):
    """Second facteur compris : ces comptes n'ouvrent jamais de session sur
    le seul mot de passe."""
    _connecte(c, "marie", "mot-de-passe-long")
    return c


def test_une_application_masquee_quitte_le_hub_de_ce_compte_seulement(hub):
    c = _connexion_marie(hub)
    assert c.post("/api/mes-apps/compta/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert "compta" not in [a["name"] for a in d["apps"]]
    assert [a["name"] for a in d["masquees"]] == ["compta"]

    # L'administrateur, lui, la voit toujours : masquer n'est pas supprimer.
    admin = app.flask_app.test_client()
    admin.post("/login", json={"password": "secret-de-test"})
    assert "compta" in [a["name"] for a in admin.get("/api/mes-apps").get_json()["apps"]]


def test_masquer_ne_ferme_aucun_acces(hub):
    """Le point a ne pas se tromper : l'application reste ouverte a qui
    connait son adresse. Croire le contraire ferait prendre un rangement
    pour une protection."""
    c = _connexion_marie(hub)
    c.post("/api/mes-apps/compta/masquer")
    # Le proxy ne regarde pas la liste des masquees : il regarde les droits.
    # 502 ici veut dire qu'il a cherche a joindre l'application (elle
    # n'ecoute pas dans un test) -- donc qu'il a laisse passer.
    r = c.get("/compta/", follow_redirects=False)
    assert r.status_code != 403, "masquer ne doit pas fermer l'acces"
    assert r.status_code != 404, "l'application doit rester joignable"


def test_on_ne_masque_que_ce_qu_on_peut_deja_voir(hub):
    """Sinon la liste des masquees revelerait l'existence de projets qu'on
    n'a pas le droit de connaitre."""
    c = _connexion_marie(hub)
    assert c.post("/api/mes-apps/secret/masquer").status_code == 404
    assert c.post("/api/mes-apps/fantome/masquer").status_code == 404
    assert app.lire_masquees() == {}


def test_le_masquage_est_journalise_pour_l_administrateur(hub):
    """Une application qui disparait d'un hub sans que personne n'ait touche
    aux droits doit rester explicable."""
    c = _connexion_marie(hub)
    c.post("/api/mes-apps/compta/masquer")
    c.delete("/api/mes-apps/compta/masquer")
    actions = [(e.get("qui"), e.get("app"), e.get("action"))
               for e in app.lire_acces() if e.get("genre") == "masquage"]
    assert ("marie", "compta", "masque") in actions
    assert ("marie", "compta", "affiche") in actions


def test_reafficher_rend_l_application_au_hub(hub):
    c = _connexion_marie(hub)
    c.post("/api/mes-apps/compta/masquer")
    assert c.delete("/api/mes-apps/compta/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert "compta" in [a["name"] for a in d["apps"]]
    assert d["masquees"] == []
    # Aucune entree vide ne traine derriere : le fichier reste lisible.
    assert app.lire_masquees() == {}


def test_l_administrateur_range_son_hub_comme_les_autres(hub):
    """Il ne figure pas dans le registre des comptes -- d'ou un fichier a
    part -- mais il a les memes yeux et le meme ecran que les autres."""
    c = hub
    c.post("/login", json={"password": "secret-de-test"})
    assert c.post("/api/mes-apps/vitrine/masquer").status_code == 200
    assert app.masquees_du_compte(app.NOM_ADMIN) == ["vitrine"]
    assert "vitrine" not in [a["name"] for a in c.get("/api/mes-apps").get_json()["apps"]]


def test_masquer_demande_une_session(hub):
    anonyme = app.flask_app.test_client()
    assert anonyme.post("/api/mes-apps/vitrine/masquer").status_code in (401, 403)


# ---------- 12 octies. conteneuriser une application ----------
#
# Le panneau PRODUIT les fichiers ; il ne construit aucune image et ne lance
# rien. Il n'a pas de socket Docker, et lui en donner un reviendrait a lui
# offrir la machine entiere.

@pytest.fixture
def conteneur(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    projet = tmp_path / "facturier"
    projet.mkdir()
    (projet / "requirements.txt").write_text("flask\npsycopg[binary]\n")
    (projet / "app.py").write_text("import psycopg, os\nPORT = os.environ['PORT']\n")
    app.save({"facturier": {"path": str(projet), "command": "python3 app.py",
                            "port": 9105, "enabled": False, "max_memory_mb": 256}})
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c, projet


def test_la_pile_se_devine_sur_les_fichiers_pas_sur_la_commande(conteneur):
    """« npm start » peut lancer autre chose que du Node, et un script shell
    peut lancer n'importe quoi. Ce sont les fichiers qui parlent."""
    c, projet = conteneur
    d = c.get("/api/app/facturier/conteneur").get_json()
    assert d["pile"] == "Python"
    assert d["indices"] == ["requirements.txt"]
    assert "postgres" in d["services"]
    noms = [f["nom"] for f in d["fichiers"]]
    assert noms == ["Dockerfile", "docker-compose.yml", ".env.exemple",
                    ".dockerignore", "CONTENEUR.md"]


def test_le_compose_produit_tient_debout(conteneur):
    """Un fichier de configuration produit et jamais relu par une machine
    finit par contenir une faute de frappe.

    PAS DE PyYAML ICI. La regle de ce depot est que la CI installe ce que
    l'image embarque, et l'image du panneau n'a aucune raison d'embarquer un
    analyseur YAML. La structure se verifie donc a la main -- c'est moins
    complet qu'un analyseur, et c'est suffisant pour attraper ce qui casse
    reellement : une cle au mauvais niveau, un volume declare nulle part.
    """
    c, _ = conteneur
    d = c.get("/api/app/facturier/conteneur").get_json()
    compose = next(f for f in d["fichiers"] if f["nom"] == "docker-compose.yml")
    lignes = [l for l in compose["contenu"].splitlines()
              if l.strip() and not l.lstrip().startswith("#")]

    racines = [l.rstrip(":") for l in lignes if not l.startswith(" ")]
    assert racines == ["services", "volumes"], racines
    # Les services sont a deux espaces, leurs reglages plus loin.
    services = [l.strip().rstrip(":") for l in lignes
                if l.startswith("  ") and not l.startswith("   ") and l.rstrip().endswith(":")]
    assert "facturier" in services and "base" in services, services
    # Le volume monte par la base existe au niveau racine : un volume nomme
    # nulle part fait echouer « up » avec un message peu parlant.
    assert "      - base:/var/lib/postgresql/data" in lignes
    assert "  base:" in lignes[lignes.index("volumes:"):]
    # Aucune tabulation : YAML les refuse, et elles ne se voient pas.
    assert "\t" not in compose["contenu"]
    # La limite de memoire suit celle de CodeLab : le conteneur doit se
    # comporter comme l'application se comporte ici.
    assert "          memory: 256M" in lignes

    # Et si un analyseur YAML est la -- c'est le cas en developpement et en
    # CI, pas forcement dans une image -- on ne s'en prive pas.
    try:
        import yaml
    except ImportError:
        return
    charge = yaml.safe_load(compose["contenu"])
    assert set(charge["services"]) == {"facturier", "base"}
    assert charge["services"]["facturier"]["deploy"]["resources"]["limits"]["memory"] \
        == "256M"


def test_l_image_produite_ne_tourne_pas_en_root(conteneur):
    """Un conteneur en root donne root sur ses volumes montes, et rapproche
    d'une evasion tout ce qui tourne dedans."""
    c, _ = conteneur
    d = c.get("/api/app/facturier/conteneur").get_json()
    dockerfile = next(f for f in d["fichiers"] if f["nom"] == "Dockerfile")["contenu"]
    # Une LIGNE « USER app », pas la chaine quelque part : commentee, elle
    # ne bascule rien, et le test passerait quand meme.
    lignes = [l.strip() for l in dockerfile.splitlines()]
    assert "USER app" in lignes, dockerfile
    assert any(l.startswith("RUN useradd") for l in lignes), dockerfile
    # Le port de CodeLab est repris : c'est le contrat, et c'est ce qui casse
    # en premier ailleurs.
    assert "ENV PORT=9105" in dockerfile


def test_le_modele_de_variables_n_emporte_aucun_secret(conteneur):
    """Un modele, pas une copie de credentials.env : il part dans le depot
    du projet, et il doit pouvoir y rester."""
    c, _ = conteneur
    d = c.get("/api/app/facturier/conteneur").get_json()
    modele = next(f for f in d["fichiers"] if f["nom"] == ".env.exemple")["contenu"]
    assert "PORT=9105" in modele
    assert "POSTGRES_PASSWORD=a-changer" in modele
    ignore = next(f for f in d["fichiers"] if f["nom"] == ".dockerignore")["contenu"]
    # Le .env rempli, lui, n'entre ni dans l'image ni dans le depot.
    assert ".env" in ignore and "!.env.exemple" in ignore


def test_la_conteneurisation_n_ecrit_plus_dans_le_projet(conteneur):
    """UNE SEULE SORTIE : l'archive.

    « Deposer dans le projet » ecrivait dans /workspace -- donc dans le
    travail de quelqu'un -- pour un geste dont le but est justement d'emmener
    l'application AILLEURS. Il fallait en plus un second bouton pour les
    fichiers deja presents, et le premier ne devait jamais ecraser. Tout cela
    disparait avec la porte elle-meme.
    """
    c, projet = conteneur
    avant = sorted(p.name for p in projet.iterdir())

    # La route d'ecriture n'existe plus du tout : 405 si Flask reconnait le
    # chemin sans la methode, 404 si le catch-all du panneau le prend avant.
    assert c.post("/api/app/facturier/conteneur", json={}).status_code in (404, 405)

    # Et l'archive, elle, ne touche a rien.
    r = c.get("/api/app/facturier/conteneur.zip")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert sorted(p.name for p in projet.iterdir()) == avant

    page = _page_panneau("dashboard.html")
    assert "ctPoser" not in page, "le bouton de depot est revenu"
    assert 'id="ct-telecharger"' in page


def test_le_panneau_ne_construit_ni_ne_lance_aucune_image():
    """La limite est nette et elle doit le rester : produire des fichiers ne
    demande aucun acces a Docker, et un panneau qui aurait ce socket
    donnerait la machine entiere a qui prend sa session."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    # Ce qu'on cherche, c'est une EXECUTION, pas le mot : le mode d'emploi
    # produit contient « docker compose up », et c'est precisement son role.
    for ligne in src.splitlines():
        nue = ligne.strip()
        if nue.startswith("#") or nue.startswith('"') or nue.startswith("'"):
            continue
        assert "docker.sock" not in nue, nue
        if "docker" in nue:
            assert not any(appel in nue for appel in
                           ("subprocess.run", "subprocess.Popen", "os.system",
                            "check_output")), nue


def test_conteneuriser_est_reserve_a_l_administrateur(conteneur):
    c, _ = conteneur
    anonyme = app.flask_app.test_client()
    assert anonyme.get("/api/app/facturier/conteneur").status_code in (401, 403)
    # L'archive aussi : elle porte le meme contenu, dont un modele de
    # variables et le mode d'emploi de l'installation.
    assert anonyme.get("/api/app/facturier/conteneur.zip").status_code in (401, 403)


# ---------- 12 ter. le logo d'une application ----------
#
# Il vit dans le DOSSIER du projet : un projet emporte son logo quand on le
# copie ailleurs. Et il est servi par /api/icon, donc DANS L'ORIGINE DU
# PANNEAU -- ce qui decide de ce qu'on accepte.

def _png(octets=64):
    """Un vrai PNG minuscule : la sonde lit la signature, pas l'extension."""
    import struct
    import zlib

    def bloc(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    brut = b"".join(b"\x00" + b"\x0e\x7c\x86" * 8 for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n"
            + bloc(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut)) + bloc(b"IEND", b""))


@pytest.fixture
def logo(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    projet = tmp_path / "site"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "x", "port": 9101,
                       "enabled": False}})
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c, projet


def test_un_logo_depose_vit_dans_le_dossier_du_projet(logo):
    """Et non dans un coin d'etat du panneau : le projet emporte son logo
    quand on le copie ailleurs, et un icon.png pose a la main donne
    exactement le meme resultat."""
    c, projet = logo
    r = c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    assert r.status_code == 200, r.data
    assert (projet / "icon.png").exists()
    assert r.get_json()["fichier"] == "icon.png"


def test_un_svg_est_refuse(logo):
    """Une image SVG est un document qui peut porter du script. Servie par
    /api/icon, donc dans l'origine du panneau, elle l'executerait la."""
    c, projet = logo
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    r = c.put("/api/app/site/logo", data=svg, content_type="image/svg+xml")
    assert r.status_code == 400, r.data
    assert "SVG" in r.get_json()["error"]
    assert not list(projet.glob("icon.*"))


def test_le_format_est_lu_dans_les_octets_pas_dans_l_entete(logo):
    """Un envoi annonce ce qu'il veut : c'est la signature du fichier qui
    fait foi. Sans cela, il suffisait d'annoncer image/png."""
    c, projet = logo
    r = c.put("/api/app/site/logo",
              data=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
              content_type="image/png")
    assert r.status_code == 400, r.data
    assert not list(projet.glob("icon.*"))


def test_une_image_trop_lourde_est_refusee(logo):
    c, projet = logo
    r = c.put("/api/app/site/logo",
              data=_png() + b"\x00" * (app.LOGO_MAX_OCTETS + 1),
              content_type="image/png")
    assert r.status_code == 400, r.data
    assert "lourde" in r.get_json()["error"]
    assert not list(projet.glob("icon.*"))


def test_un_seul_logo_a_la_fois(logo):
    """find_icon prend le premier nom de sa liste : un icon.png laisse en
    place survivrait a l'envoi d'un icon.gif et continuerait de s'afficher."""
    c, projet = logo
    c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    r = c.put("/api/app/site/logo", data=b"GIF89a" + b"\x00" * 32,
              content_type="image/gif")
    assert r.status_code == 200, r.data
    assert not (projet / "icon.png").exists()
    assert (projet / "icon.gif").exists()


def test_retirer_le_logo_ne_touche_pas_a_ce_que_le_projet_a_pose(logo):
    """Un favicon.ico depose a la main dans le projet lui appartient : le
    panneau n'efface que ce qu'il a lui-meme ecrit."""
    c, projet = logo
    (projet / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    r = c.delete("/api/app/site/logo")
    assert r.status_code == 200, r.data
    assert not (projet / "icon.png").exists()
    assert (projet / "favicon.ico").exists()


def test_le_depot_d_un_logo_est_journalise(logo):
    """Une image deposee dans le dossier d'un projet est une ecriture : elle
    se retrouve dans le journal comme le reste."""
    c, _ = logo
    c.put("/api/app/site/logo", data=_png(), content_type="image/png")
    evenements = [e for e in app.lire_acces() if e.get("genre") == "logo"]
    assert evenements and evenements[0]["app"] == "site"
    assert evenements[0]["action"] == "depose"


def test_l_icone_servie_ne_peut_pas_executer_de_script(logo):
    """Un SVG pose A LA MAIN dans le projet reste lu par find_icon -- c'est
    voulu, le projet a le droit. La politique de securite le rend inerte."""
    c, projet = logo
    (projet / "icon.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>')
    r = c.get("/api/icon/site")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_seul_l_administrateur_depose_un_logo(logo, monkeypatch):
    c, _ = logo
    anonyme = app.flask_app.test_client()
    assert anonyme.put("/api/app/site/logo", data=_png(),
                       content_type="image/png").status_code in (401, 403)


# ---------- 12 bis. enregistrer la configuration d'une application ----------
#
# Vecu : « Rend possible la sauvegarde de la configuration d'une application.
# Proposer un redemarrage. Les champs qui ne necessitent pas de redemarrage
# peuvent etre appliques directement. »
#
# La route refusait tout net pendant qu'une application tournait : corriger
# une faute dans une description demandait de couper le service. Le refus
# protegeait d'une illusion reelle -- croire qu'une commande modifiee
# s'applique au processus deja lance -- mais il la traitait en interdisant
# tout, alors qu'il suffit de DIRE lequel des champs attend un redemarrage.

@pytest.fixture
def en_marche(tmp_path, monkeypatch):
    """Une application qui tourne, et dont on peut editer la configuration."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"site": {"path": "/w/a", "command": "python3 app.py", "port": 9101,
                       "enabled": True, "description": "avant",
                       "max_memory_mb": 256}})
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c


def _editer(c, **champs):
    """Le formulaire envoie TOUS ses champs, comme la page le fait : une
    requete partielle viderait ce qu'elle omet, et le test mesurerait alors
    cet effacement plutot que ce qu'il croit mesurer."""
    corps = {"path": "/w/a", "command": "python3 app.py", "max_memory_mb": 256}
    corps.update(champs)
    return c.put("/api/app/site", json=corps)


def test_une_application_en_marche_s_enregistre_desormais(en_marche):
    """C'etait la demande : pouvoir enregistrer sans couper le service."""
    r = _editer(en_marche, description="apres")
    assert r.status_code == 200, r.data
    assert app.load()["site"]["description"] == "apres"


def test_ce_qui_se_relit_a_chaque_requete_s_applique_tout_de_suite(en_marche):
    """Description, categorie, visibilite, commande de build : le panneau les
    relit a chaque fois qu'il s'en sert. Rien a redemarrer."""
    r = _editer(en_marche, description="apres", visibility="privee",
                build_command="npm install")
    d = r.get_json()
    assert d["redemarrage_requis"] is False, d
    assert d["champs_en_attente"] == []
    assert app.load()["site"]["visibility"] == "privee"


def test_ce_qui_est_lu_au_lancement_attend_le_redemarrage(en_marche):
    """La commande, le dossier et la limite memoire sont lus quand le
    processus demarre. Les changer ne touche pas celui qui tourne -- et le
    taire laisserait croire le contraire."""
    r = _editer(en_marche, command="python3 autre.py", max_memory_mb=128)
    d = r.get_json()
    assert d["redemarrage_requis"] is True, d
    assert set(d["champs_en_attente"]) == {"command", "max_memory_mb"}
    # Enregistre malgre tout : c'est le prochain demarrage qui la prendra.
    assert app.load()["site"]["command"] == "python3 autre.py"


def test_une_application_arretee_n_a_rien_a_redemarrer(en_marche, monkeypatch):
    """Reclamer un redemarrage a qui ne tourne pas serait un faux message :
    le prochain demarrage prendra la nouvelle configuration tout seul."""
    monkeypatch.setattr(app, "is_running", lambda n: False)
    d = _editer(en_marche, command="python3 autre.py").get_json()
    assert d["redemarrage_requis"] is False
    assert d["running"] is False


def test_reenregistrer_a_l_identique_ne_reclame_pas_de_redemarrage(en_marche):
    """La comparaison porte sur la valeur NETTOYEE : un espace en fin de
    ligne ne doit pas annoncer un redemarrage necessaire."""
    d = _editer(en_marche, command="python3 app.py", max_memory_mb=256).get_json()
    assert d["redemarrage_requis"] is False, d


def test_la_liste_des_champs_qui_attendent_vit_du_cote_serveur():
    """Une copie dans le navigateur aurait diverge a la premiere evolution :
    c'est le serveur qui sait ce qu'il relit, et quand."""
    assert app.CHAMPS_AU_DEMARRAGE == ("path", "command", "max_memory_mb")
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    # La page lit la reponse du serveur, elle ne rejoue pas la regle.
    assert "d.redemarrage_requis" in page
    assert "champs_en_attente" in page
    # Et l'ancien bandeau bloquant a bien disparu.
    assert "stopThenEdit" not in page
    assert "ne peut pas être enregistrée" not in page


# ---------- 13. categories du hub ----------
#
# Une categorie ne donne aucun droit : c'est du rangement. Ce qui doit rester
# vrai, c'est qu'un projet ne porte jamais une categorie qui n'existe pas --
# sinon supprimer une categorie le rendrait invisible dans le hub, range dans
# un tiroir que plus rien n'affiche.

@pytest.fixture
def categorise(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    # Arretee : ces tests-la ne parlent pas du redemarrage, et une
    # application a l'arret repond sans bandeau.
    monkeypatch.setattr(app, "is_running", lambda n: False)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"site": {"path": "/w/a", "command": "x", "port": 9101,
                       "enabled": True, "categorie": "Outils"}})
    app.ecrire_categories(["Outils", "Donnees"])
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c


def test_une_categorie_supprimee_ne_reste_pas_collee_a_un_projet(categorise):
    """Le cas qui rendrait un projet invisible dans le hub.

    Le hub n'affiche que les groupes qu'il connait : un projet qui garderait
    « Outils » apres la disparition d'« Outils » ne serait dans aucun groupe.
    """
    r = categorise.put("/api/categories", json={"categories": ["Donnees"]})
    assert r.status_code == 200, r.data
    assert r.get_json()["declasses"] == 1
    assert app.load()["site"]["categorie"] == ""
    hub = categorise.get("/api/mes-apps").get_json()
    assert hub["apps"][0]["categorie"] == ""
    assert hub["categories"] == ["Donnees"]


def test_un_projet_ne_prend_pas_une_categorie_inventee(categorise):
    """Une categorie arrive par le reseau : elle se verifie comme le reste."""
    r = categorise.post("/api/add", json={
        "name": "neuf", "path": "/w/neuf", "command": "python3 app.py",
        "categorie": "Inventee"})
    assert r.status_code == 200, r.data
    assert app.load()["neuf"]["categorie"] == ""

    # A l'edition aussi : une categorie connue passe, une inconnue est videe.
    assert categorise.put("/api/app/site", json={
        "path": "/w/a", "command": "x", "categorie": "Donnees"}).status_code == 200
    assert app.load()["site"]["categorie"] == "Donnees"
    assert categorise.put("/api/app/site", json={
        "path": "/w/a", "command": "x", "categorie": "Fantome"}).status_code == 200
    assert app.load()["site"]["categorie"] == ""


def test_deux_tiroirs_pour_la_meme_chose_sont_refuses(categorise):
    """« Outils » et « outils » rangeraient les projets a deux endroits."""
    r = categorise.put("/api/categories", json={"categories": ["Outils", "outils", " Outils "]})
    assert r.status_code == 200, r.data
    assert r.get_json()["categories"] == ["Outils"]


def test_un_utilisateur_lit_les_categories_mais_n_en_cree_pas(categorise):
    """Le hub d'un compte utilisateur en a besoin pour se ranger ; le
    rangement lui-meme reste une decision d'administration."""
    sel = "bb" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["site"], "cree": 0}})
    categorise.post("/logout")
    _connecte(categorise, "marie", "mot-de-passe-long")
    assert categorise.get("/api/categories").status_code == 200
    assert categorise.put("/api/categories", json={"categories": ["A moi"]}).status_code == 403
    assert app.lire_categories() == ["Outils", "Donnees"]


# ---------- 14. QR code du second facteur ----------
#
# Recopier une cle de 32 caracteres a la main est le moment ou l'inscription
# echoue. Le QR code supprime cette etape -- mais il porte le secret, donc il
# ne doit jamais voyager par l'adresse, et son absence ne doit rien casser.

def test_le_qr_code_ne_sort_pas_de_la_session(client):
    """Aucun secret dans l'URL : une adresse finit dans l'historique du
    navigateur, dans les journaux d'acces et dans le referer de la page
    suivante. La route ne lit QUE la session signee."""
    pytest.importorskip("qrcode")
    # Sans inscription en attente, rien a montrer.
    assert client.get("/qr/totp.svg").status_code == 404

    client.post("/login", json={"password": "secret-de-test"})
    d = client.post("/api/securite/totp/preparer").get_json()
    assert d["qr"] == "/qr/totp.svg", "aucun secret ne doit apparaitre dans l'adresse"

    r = client.get("/qr/totp.svg")
    assert r.status_code == 200
    assert r.mimetype == "image/svg+xml"
    corps = r.get_data(as_text=True)
    assert corps.startswith("<svg") and "<rect" in corps
    # Le secret est encode dans les modules du QR, pas ecrit dans le SVG.
    assert d["secret"] not in corps


def test_le_qr_code_disparait_avec_l_inscription(client):
    """Une fois le facteur enregistre, la route ne doit plus rien servir."""
    pytest.importorskip("qrcode")
    client.post("/login", json={"password": "secret-de-test"})
    d = client.post("/api/securite/totp/preparer").get_json()
    assert client.get("/qr/totp.svg").status_code == 200
    code = app.totp_code(d["secret"], int(time.time()) // app.TOTP_PAS)
    r = client.post("/api/securite/totp/activer", json={"code": code})
    assert r.status_code == 200, r.data
    assert client.get("/qr/totp.svg").status_code == 404


def test_sans_la_bibliotheque_qr_l_inscription_marche_encore(client, monkeypatch):
    """Le panneau doit rester lancable avec Flask pour seule dependance.

    Sans qrcode, la page retombe sur la cle a saisir : la route repond 404,
    l'image se masque, et l'inscription se termine normalement.
    """
    # La bibliotheque rendue introuvable, pour de vrai : un sys.modules a None
    # fait lever ImportError a l'import, exactement comme si elle manquait.
    monkeypatch.setitem(sys.modules, "qrcode", None)
    assert app.qr_svg("otpauth://totp/CodeLab:admin?secret=AAAA") == ""

    client.post("/login", json={"password": "secret-de-test"})
    d = client.post("/api/securite/totp/preparer").get_json()
    assert d["secret"], "la cle a recopier reste fournie"
    assert client.get("/qr/totp.svg").status_code == 404
    code = app.totp_code(d["secret"], int(time.time()) // app.TOTP_PAS)
    assert client.post("/api/securite/totp/activer", json={"code": code}).status_code == 200


# ---------- 15. adresse mail des comptes et inscription libre ----------
#
# L'adresse relie un compte a quelqu'un de joignable, et c'est elle qui rend
# l'inscription libre defendable. Ce qui doit rester vrai : une adresse n'est
# "verifiee" que si un code envoye dessus est revenu, un compte inscrit
# librement n'ouvre aucun projet et ne se connecte pas avant d'avoir confirme,
# et l'inscription reste fermee sans serveur d'envoi.

@pytest.fixture
def comptes_mail(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({})
    app.ecrire_utilisateurs({})
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n")
    partis = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None:
                        partis.append((destinataires, corps)))
    return app.flask_app.test_client(), partis


def _code_du_dernier_mail(partis):
    """Le code tel que la personne le lit dans son mail."""
    return re.search(r": (\d{6})", partis[-1][1]).group(1)


# ---------- mot de passe oublie ----------
#
# Par l'adresse mail quand il y en a une de VERIFIEE, et par l'administrateur
# sinon. Aucun des deux chemins ne suffit seul : sans adresse, personne ne
# peut prouver a distance qui il est ; et si le seul recours etait
# l'administrateur, il faudrait le deranger a chaque oubli.

def _base_brute():
    """Tout ce que la base du panneau garde sur le disque, octet par octet :
    le fichier, et ce qui attend encore dans son journal d'ecriture (-wal).
    C'est ce que lirait quiconque copie le dossier d'etat."""
    brut = b""
    for suffixe in ("", "-wal"):
        try:
            with open(app.DB_FILE + suffixe, "rb") as f:
                brut += f.read()
        except OSError:
            pass
    return brut.decode("latin-1")


def _compte_avec_adresse(nom="marie", mdp="mot-de-passe-long", verifiee=True,
                         face_id=True):
    """face_id : une cle d'acces enregistree -- la condition, depuis l'option A,
    pour que la reinitialisation par mail soit ouverte."""
    sel = "ee" * 16
    app.ecrire_utilisateurs({nom: {
        "sel": sel, "hash": app.derive_mot_de_passe(mdp, sel),
        "projets": [], "cree": 0,
        "email": f"{nom}@example.com", "email_verifie": verifiee}})
    app.ecrire_passkeys({nom: [{"id": "CLE", "cle_publique": "AAAA", "compteur": 0,
                                "nom": "Face ID", "cree": 0, "dernier": 0}]}
                        if face_id else {})


def test_un_code_de_reinitialisation_part_a_l_adresse_verifiee(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    assert c.post("/mot-de-passe/oubli", json={"nom": "marie"}).status_code == 200
    assert partis and partis[-1][0] == ["marie@example.com"]
    assert "reinitialisation" in partis[-1][1] or "einitialisation" in partis[-1][1]

    code = _code_du_dernier_mail(partis)
    r = c.post("/mot-de-passe/reinitialiser",
               json={"nom": "marie", "code": code, "nouveau": "nouveau-mot-de-passe"})
    assert r.status_code == 200, r.data
    compte = app.lire_utilisateurs()["marie"]
    assert app.verifie_mot_de_passe(compte, "nouveau-mot-de-passe")
    assert not app.verifie_mot_de_passe(compte, "mot-de-passe-long")


def test_le_code_de_reinitialisation_ne_dort_pas_en_clair(comptes_mail):
    """Comme celui de la verification d'adresse : le fichier ne garde qu'une
    empreinte. Sinon le code attendrait, lisible, a cote du nom du compte."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    code = _code_du_dernier_mail(partis)
    assert code not in _base_brute()


def test_le_code_de_reinitialisation_ne_sert_qu_une_fois(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    code = _code_du_dernier_mail(partis)
    assert c.post("/mot-de-passe/reinitialiser",
                  json={"nom": "marie", "code": code,
                        "nouveau": "nouveau-mot-de-passe"}).status_code == 200
    r = c.post("/mot-de-passe/reinitialiser",
               json={"nom": "marie", "code": code, "nouveau": "encore-un-autre"})
    assert r.status_code == 400, r.data
    assert app.verifie_mot_de_passe(app.lire_utilisateurs()["marie"],
                                    "nouveau-mot-de-passe")


def test_un_code_de_verification_ne_vaut_pas_reinitialisation(comptes_mail):
    """Deux pouvoirs tres differents : verifier une adresse rend un compte
    utilisable, reinitialiser un mot de passe le rend ACCESSIBLE. Un seul
    jeton pour les deux melangerait les deux."""
    c, partis = comptes_mail
    _compte_avec_adresse(verifiee=False)
    comptes = app.lire_utilisateurs()
    code_adresse = app.poser_code_email(comptes["marie"])
    app.ecrire_utilisateurs(comptes)
    r = c.post("/mot-de-passe/reinitialiser",
               json={"nom": "marie", "code": code_adresse, "nouveau": "nouveau-mot-de-passe"})
    assert r.status_code == 400, r.data


def test_sans_adresse_verifiee_rien_ne_part(comptes_mail):
    """Et l'utilisateur est renvoye vers l'administrateur : c'est l'autre
    chemin, celui qui ne depend d'aucun mail."""
    c, partis = comptes_mail
    _compte_avec_adresse(verifiee=False)
    assert c.post("/mot-de-passe/oubli", json={"nom": "marie"}).status_code == 200
    assert partis == []


def test_la_reponse_ne_dit_jamais_si_le_compte_existe(comptes_mail):
    """Sinon ce formulaire devient un annuaire : on y tape des noms jusqu'a
    trouver ceux qui existent."""
    c, _partis = comptes_mail
    _compte_avec_adresse()
    connue = c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    inconnue = c.post("/mot-de-passe/oubli", json={"nom": "fantome"})
    assert connue.status_code == inconnue.status_code == 200
    assert connue.get_json() == inconnue.get_json()
    # Et au moment de poser le mot de passe, un compte inconnu rend le meme
    # message qu'un code faux.
    faux = c.post("/mot-de-passe/reinitialiser",
                  json={"nom": "fantome", "code": "123456", "nouveau": "mot-de-passe-long"})
    mauvais = c.post("/mot-de-passe/reinitialiser",
                     json={"nom": "marie", "code": "000000", "nouveau": "mot-de-passe-long"})
    assert faux.get_json()["error"] == mauvais.get_json()["error"] == "Code incorrect."


def test_un_second_envoi_immediat_ne_part_pas(comptes_mail):
    """Sans cela, ce formulaire est un robinet a mails vers l'adresse de
    quelqu'un d'autre."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    assert len(partis) == 1, partis


def test_la_reinitialisation_est_journalisee(comptes_mail, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    c.post("/mot-de-passe/reinitialiser",
           json={"nom": "marie", "code": _code_du_dernier_mail(partis),
                 "nouveau": "nouveau-mot-de-passe"})
    actions = [e.get("action") for e in app.lire_acces()
               if e.get("genre") == "mot-de-passe"]
    assert "code envoye" in actions
    assert "reinitialise par mail" in actions


def test_aucun_mot_de_passe_n_est_ecrit_en_clair(comptes_mail):
    """La regle de fond : ce qui est persiste est une EMPREINTE, jamais le
    mot de passe. Vrai a la creation, vrai au changement, et vrai a la
    reinitialisation -- c'est le chemin le plus recent, donc le plus facile
    a oublier."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    c.post("/mot-de-passe/reinitialiser",
           json={"nom": "marie", "code": _code_du_dernier_mail(partis),
                 "nouveau": "nouveau-mot-de-passe"})
    registre = _base_brute()
    assert "nouveau-mot-de-passe" not in registre
    assert "mot-de-passe-long" not in registre
    compte = app.lire_utilisateurs()["marie"]
    # Empreinte derivee et sel propre a ce compte, refait a chaque changement.
    assert len(compte["sel"]) >= 32 and len(compte["hash"]) >= 32
    assert compte["hash"] == app.derive_mot_de_passe("nouveau-mot-de-passe", compte["sel"])


def test_la_base_ne_recoit_aucune_empreinte_de_mot_de_passe():
    """La base est joignable par les projets deployes. Y deposer des
    empreintes leur offrirait une attaque hors ligne sur les mots de passe
    du panneau : le miroir ne porte que ce qui se lit deja dans l'interface.
    """
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    table = src.split("CREATE TABLE IF NOT EXISTS utilisateurs")[1].split(")\"\"\"")[0]
    for interdit in ("hash", "sel", "mot_de_passe", "password", "totp"):
        assert interdit not in table, f"{interdit} n'a rien a faire dans cette table"
    ecriture = src.split("def _pg_ecrire_utilisateurs(")[1].split("\ndef ")[0]
    assert '"hash"' not in ecriture and '"sel"' not in ecriture


def test_le_code_de_verification_ne_dort_pas_en_clair(comptes_mail):
    """utilisateurs.json ne doit pas contenir le code qu'on vient d'envoyer.

    Il y resterait a cote du nom du compte, lisible par qui lit le fichier --
    exactement ce que la derivation des mots de passe evite par ailleurs.
    """
    c, partis = comptes_mail
    c.post("/login", json={"password": "secret-de-test"})
    c.post("/api/utilisateurs", json={"nom": "marie", "mot_de_passe": "mot-de-passe-long"})
    c.post("/logout")
    _connecte(c, "marie", "mot-de-passe-long")
    r = c.post("/api/mon-compte/email", json={"email": "marie@example.com"})
    assert r.status_code == 200, r.data
    code = _code_du_dernier_mail(partis)
    assert partis[-1][0] == ["marie@example.com"], "le code part a l'adresse declaree"

    assert code not in _base_brute(), "le code ne doit etre range que sous forme d'empreinte"

    assert c.post("/api/mon-compte/email/confirmer",
                  json={"code": "000000" if code != "000000" else "111111"}).status_code == 400
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False
    assert c.post("/api/mon-compte/email/confirmer", json={"code": code}).status_code == 200
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is True


def test_un_code_ne_se_devine_pas_par_essais_successifs(comptes_mail):
    """Six chiffres se devinent en un million d'essais : il en faut une borne."""
    c, partis = comptes_mail
    c.post("/login", json={"password": "secret-de-test"})
    c.post("/api/utilisateurs", json={"nom": "marie", "mot_de_passe": "mot-de-passe-long"})
    c.post("/logout")
    _connecte(c, "marie", "mot-de-passe-long")
    c.post("/api/mon-compte/email", json={"email": "marie@example.com"})
    juste = _code_du_dernier_mail(partis)
    faux = "000000" if juste != "000000" else "111111"

    for _ in range(app.CODE_EMAIL_ESSAIS):
        assert c.post("/api/mon-compte/email/confirmer", json={"code": faux}).status_code == 400
    # Le bon code ne passe plus : le code en cours a ete jete.
    r = c.post("/api/mon-compte/email/confirmer", json={"code": juste})
    assert r.status_code == 400
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False


def test_changer_d_adresse_annule_la_verification(comptes_mail):
    """Sinon il suffirait de remplacer une adresse verifiee par une autre
    pour heriter de son statut sans jamais rien recevoir."""
    c, partis = comptes_mail
    c.post("/login", json={"password": "secret-de-test"})
    c.post("/api/utilisateurs", json={"nom": "marie", "mot_de_passe": "mot-de-passe-long"})
    c.post("/logout")
    _connecte(c, "marie", "mot-de-passe-long")
    c.post("/api/mon-compte/email", json={"email": "marie@example.com"})
    c.post("/api/mon-compte/email/confirmer", json={"code": _code_du_dernier_mail(partis)})
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is True

    c.post("/api/mon-compte/email", json={"email": "autre@example.com"})
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False
    # Cote administrateur aussi : changer l'adresse de quelqu'un ne lui
    # transmet pas le statut de l'ancienne. On repart d'un etat verifie pour
    # que ce soit bien ce chemin-la qui soit mis a l'epreuve.
    comptes = app.lire_utilisateurs()
    comptes["marie"]["email_verifie"] = True
    app.ecrire_utilisateurs(comptes)
    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    c.put("/api/utilisateurs/marie", json={"email": "encore@example.com"})
    assert app.lire_utilisateurs()["marie"]["email_verifie"] is False


def test_il_n_y_a_plus_d_inscription_libre(comptes_mail):
    """Les comptes ne naissent que par invitation de l'administrateur : plus de
    formulaire public, meme avec un serveur d'envoi configure."""
    c, partis = comptes_mail
    r = c.post("/inscription", json={"nom": "paul", "email": "paul@example.com",
                                     "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code >= 300
    r = c.post("/inscription/", json={"nom": "paul", "email": "paul@example.com",
                                      "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 404
    assert "paul" not in app.lire_utilisateurs() and not partis
    assert c.get("/api/inscription").status_code in (401, 404)


# ---------- 16. journal des acces ----------
#
# Deux usages, deux seulement : reconnaitre une intrusion, et savoir si un
# projet sert encore. Ce qui doit rester vrai : une ouverture n'est notee
# qu'apres les controles d'acces (un refus n'est pas une visite), le journal
# ne grossit pas indefiniment, et une ecriture impossible ne casse rien.

@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app._dernier_acces.clear()
    app.save({"prive": {"path": "/w/a", "command": "x", "port": 9101,
                        "enabled": True, "visibility": "privee"},
              "public": {"path": "/w/b", "command": "x", "port": 9102,
                         "enabled": True, "visibility": "publique"}})
    sel = "cc" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": [], "cree": 0, "totp": app.totp_nouveau_secret()}})
    return app.flask_app.test_client()


# ---------- les processus survivent au panneau ----------
#
# Vecu : « Le bouton arreter l'application ne fonctionne pas. »
#
# Les applications sont lancees avec start_new_session=True : elles ont leur
# propre session, donc elles survivent a l'arret du panneau -- mise a jour de
# l'image, "docker restart", plantage. Le dictionnaire procs, lui, vit en
# memoire et ne survivait pas.
#
# Au redemarrage, le panneau ne connaissait donc plus les processus toujours
# en vie : l'interface affichait « Arretee » d'une application qui tournait,
# resume() en relancait une deuxieme qui mourait sur un port occupe, et
# ARRETER NE FAISAIT RIEN -- stop() ne trouvait rien a tuer, notait
# enabled=False et repondait que tout allait bien.

@pytest.fixture
def survivants(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "PROCESSUS_FILE", str(tmp_path / "processus.json"))
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(app, "ISOLER_APPS", False)
    app._apps_cache["signature"] = None
    projet = tmp_path / "projet"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "sleep 120",
                       "port": 9399, "enabled": False}})
    app.procs.clear()
    yield tmp_path
    for nom in list(app.procs):
        try:
            app.stop(nom)
        except Exception:                                         # noqa: BLE001
            pass
    app.procs.clear()


def test_un_processus_lance_est_note_sur_le_disque(survivants):
    """Sans note, rien ne permet de le retrouver apres un redemarrage : le
    panneau est son pere, et un pere mort ne laisse aucune trace."""
    assert app.start("site") is None
    note = app.lire_processus()["site"]
    assert note["pid"] == app.procs["site"].pid
    app.stop("site")
    # Et la note disparait avec lui : une note qui traine ferait adopter un
    # pid reutilise par n'importe quel autre programme.
    assert "site" not in app.lire_processus()


def test_le_panneau_reprend_la_main_sur_ce_qui_tourne_encore(survivants):
    """LE test de cette correction : on simule le redemarrage du panneau en
    vidant procs -- exactement ce que fait un "docker restart" -- et on
    verifie qu'arreter tue reellement le processus."""
    assert app.start("site") is None
    pid = app.procs["site"].pid

    app.procs.clear()                       # le panneau redemarre
    assert app.is_running("site") is False  # etat d'avant la correction
    repris = app.adopter_processus_survivants()
    assert repris and "site" in repris[0]
    assert app.is_running("site") is True

    app.stop("site")
    for _ in range(40):
        if not app._processus_est_le_notre(pid, "site"):
            break
        time.sleep(0.1)
    assert not app._processus_est_le_notre(pid, "site"), (
        "le processus tourne toujours apres un arret demande")


def test_un_pid_qui_n_est_plus_le_notre_n_est_pas_adopte(survivants):
    """Un numero de processus est reutilise par le noyau. Adopter sur le seul
    pid finirait par tuer un programme qui n'a rien demande -- pire que le
    defaut qu'on corrige."""
    app._ecrire_processus({"site": {"pid": os.getpid(), "depuis": 0}})
    app.procs.clear()
    assert app.adopter_processus_survivants() == []
    assert app.is_running("site") is False
    # La note fausse est nettoyee au passage.
    assert app.lire_processus() == {}


def test_la_marque_est_posee_dans_l_environnement_du_processus(survivants):
    """Dans l'environnement, et non dans le titre du processus : le noyau le
    fige a l'exec, l'application ne peut donc pas l'effacer."""
    assert app.start("site") is None
    pid = app.procs["site"].pid
    environ = open(f"/proc/{pid}/environ", "rb").read()
    assert b"CODELAB_APP=site\0" in environ


def test_l_adoption_passe_avant_la_reprise_automatique():
    """Ordre imperatif : resume() relancerait par-dessus une application deja
    en vie, et la nouvelle mourrait sur un port occupe."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    demarrage = src.split('if __name__ == "__main__":')[1]
    # Les commentaires citent les deux noms : on ne regarde que les appels.
    lignes = [l.strip() for l in demarrage.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert lignes.index("adopter_processus_survivants()") < lignes.index("resume()")


# ---------- la carte de chaleur, et les analyses ----------
#
# Vecu : « Ajoute une carte de chaleur pour l'utilisation des applications, et
# la possibilite de filtrer sur un utilisateur. Toutes les statistiques
# doivent etre historisees en base, et ces donnees alimentent les analyses. »
#
# Le point qui compte, et qui ne se voit pas a l'ecran : le fichier du
# journal est PLAFONNE a 1 Mo, donc il oublie. Tant qu'il servait a afficher
# quarante lignes, cela n'avait aucune importance. Des qu'on COMPTE, un total
# calcule sur une fenetre glissante ne veut plus rien dire -- il diminue tout
# seul a mesure que le journal tourne. Les analyses lisent donc la base, le
# fichier restant le repli.

def _semer_ouvertures(monkeypatch, tmp_path, evenements):
    """Verse des evenements dans le journal (table journal de la base)."""
    with app.stockage.transaction(app.DB_FILE) as cx:
        cx.execute("DELETE FROM journal")
        for e in evenements:
            reste = {k: v for k, v in e.items()
                     if k not in ("id", "ts", "genre", "qui", "app", "ip")}
            cx.execute("INSERT INTO journal (id, ts, genre, qui, app, ip, details) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (e["id"], e["ts"], e["genre"], e.get("qui"), e.get("app"),
                        e.get("ip"), json.dumps(reste)))


def test_la_carte_compte_par_jour_et_pas_par_evenement(journal, tmp_path, monkeypatch):
    """Une case par jour : trois ouvertures le meme jour font une case a
    trois, pas trois cases."""
    hier = time.time() - 86400
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(hier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(hier) + 60, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "c", "ts": int(hier) + 120, "genre": "ouverture", "qui": "marie", "app": "public"},
        # Une connexion n'est pas une ouverture : la carte parle d'usage des
        # applications, pas de presence.
        {"id": "d", "ts": int(hier) + 180, "genre": "connexion", "qui": "marie"},
    ])
    jours = app.carte_activite()
    assert len(jours) == 1, jours
    assert jours[0]["ouvertures"] == 3
    # Et combien d'applications distinctes ce jour-la : c'est ce qui
    # distingue « j'ai ouvert dix fois la meme » de « j'ai travaille partout ».
    assert jours[0]["apps"] == 2


def test_la_carte_se_filtre_par_personne_et_par_application(journal, tmp_path, monkeypatch):
    hier = int(time.time() - 86400)
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": hier, "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": hier, "genre": "ouverture", "qui": "jean", "app": "prive"},
        {"id": "c", "ts": hier, "genre": "ouverture", "qui": "marie", "app": "public"},
    ])
    assert app.carte_activite(qui="marie")[0]["ouvertures"] == 2
    assert app.carte_activite(app="prive")[0]["ouvertures"] == 2
    assert app.carte_activite(qui="marie", app="prive")[0]["ouvertures"] == 1
    # Un visiteur anonyme se filtre aussi : "" est une valeur, pas l'absence
    # de filtre.
    assert app.carte_activite(qui="") == []


def test_la_carte_ne_montre_que_l_annee_demandee(journal, tmp_path, monkeypatch):
    """Une annee civile, de janvier a decembre : ce qui est arrive l'an
    dernier n'a rien a faire sur la carte de cette annee, et inversement."""
    import datetime as _dt
    fuseau = app._fuseau()
    cette_annee = app.annee_courante()
    milieu = _dt.datetime(cette_annee, 6, 15, 12, 0, tzinfo=fuseau).timestamp()
    an_dernier = _dt.datetime(cette_annee - 1, 6, 15, 12, 0, tzinfo=fuseau).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(an_dernier), "genre": "ouverture", "qui": "marie", "app": "prive"},
        {"id": "b", "ts": int(milieu), "genre": "ouverture", "qui": "marie", "app": "prive"},
    ])
    jours = app.carte_activite(cette_annee)
    assert [j["jour"] for j in jours] == [f"{cette_annee}-06-15"]
    jours = app.carte_activite(cette_annee - 1)
    assert [j["jour"] for j in jours] == [f"{cette_annee - 1}-06-15"]
    # Sans annee demandee, c'est l'annee en cours.
    assert app.carte_activite() == app.carte_activite(cette_annee)


def test_les_annees_proposees_vont_de_la_premiere_a_aujourd_hui(journal, tmp_path,
                                                                monkeypatch):
    """Le selecteur doit offrir chaque annee entre la premiere utilisation et
    l'annee en cours -- y compris celles ou rien ne s'est passe, sans quoi la
    liste aurait des trous et l'on croirait a une perte de donnees."""
    import datetime as _dt
    fuseau = app._fuseau()
    cette_annee = app.annee_courante()
    vieux = _dt.datetime(cette_annee - 2, 3, 2, 12, 0, tzinfo=fuseau).timestamp()
    _semer_ouvertures(monkeypatch, tmp_path, [
        {"id": "a", "ts": int(vieux), "genre": "ouverture", "qui": "marie", "app": "prive"},
    ])
    assert app.annees_activite() == (cette_annee - 2, cette_annee - 2)
    assert app.annees_proposees(*app.annees_activite()) == [
        cette_annee - 2, cette_annee - 1, cette_annee]
    # Journal vide : l'annee en cours reste proposee, un selecteur vide
    # n'etant pas une reponse.
    assert app.annees_proposees(None, None) == [cette_annee]


def test_la_carte_est_reservee_a_l_administrateur(journal):
    """Elle dit qui a ouvert quoi et quand : c'est le meme secret que le
    journal, elle se garde comme lui."""
    c = journal
    assert c.get("/api/activite/carte").status_code in (401, 403)
    c.post("/login", json={"password": "secret-de-test"})
    r = c.get("/api/activite/carte")
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["source"] in ("postgres", "fichier")
    assert isinstance(d["jours"], list)
    assert d["annee"] in d["annees"], (
        "l'annee affichee doit figurer dans la liste proposee")


def test_une_annee_farfelue_ne_fait_pas_tomber_la_carte(journal):
    """Le parametre arrive par l'URL : il se borne, il ne se croit pas."""
    c = journal
    c.post("/login", json={"password": "secret-de-test"})
    courante = app.annee_courante()
    for valeur in ("0", "-5", "99999", "beaucoup", ""):
        r = c.get("/api/activite/carte?annee=" + valeur)
        assert r.status_code == 200, valeur
        assert r.get_json()["annee"] == courante, valeur
    # Une annee plausible, elle, est respectee.
    r = c.get("/api/activite/carte?annee=" + str(courante - 1))
    assert r.get_json()["annee"] == courante - 1


def test_les_analyses_preferent_la_base_au_fichier_plafonne():
    """Le resume etait calcule sur le fichier : le total d'ouvertures d'un
    projet DIMINUAIT tout seul a mesure que le journal tournait."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src.split("def api_activite(")[1].split("@flask_app")[0]
    assert "pg_resume_acces()" in bloc, "le resume doit venir de la base quand elle repond"
    assert "resume_acces()" in bloc, "et le fichier doit rester le repli"
    # Le comptage par jour se fait DANS la base : ramener un an d'evenements
    # pour les additionner en Python s'ecroulerait le jour ou l'historique
    # compte pour de bon.
    carte = src.split("def pg_carte_activite(")[1].split("\ndef ")[0]
    assert "GROUP BY" in carte and "count(*)" in carte


def test_la_carte_couvre_l_annee_de_janvier_a_decembre():
    """Une fenetre glissante commencait un jour quelconque : les mois se
    decalaient d'un cran chaque jour, et deux cartes prises a deux dates ne
    se comparaient pas."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    assert "new Date(annee,0,1)" in bloc, "la carte doit commencer au 1er janvier"
    assert "new Date(annee,11,31)" in bloc, "et finir au 31 decembre"
    assert "d.fenetre" not in bloc, "la fenetre glissante n'a plus cours"
    # Les jours a venir ne se peignent pas comme des jours a zero : ce serait
    # affirmer une inactivite qui n'a pas encore eu lieu.
    assert "carte-case avenir" in bloc and ".carte-case.avenir{" in page


def test_la_ligne_des_mois_couvre_toute_la_largeur_de_la_carte():
    """Une etiquette par colonne laissait onze mois muets et un mot de dix
    pixels de large : on ne lisait rien."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    assert "grid-column:span" in bloc, (
        "chaque mois doit couvrir ses colonnes, pas une seule")
    # Les MEMES colonnes et le meme ecart pour les deux rangees : sans cela,
    # les mois ne tombent pas en face de leurs semaines. La ligne des mois
    # n'a qu'une rangee, elle ne reprend donc pas les sept lignes.
    assert "${gabarit}" in bloc and "${gabaritMois}" in bloc
    mois = bloc.split("const gabaritMois=")[1].split("\n")[0]
    assert "repeat(${colonnes},${cote}px)" in mois and "gap:${ecart}px" in mois
    assert "grid-template-rows" not in mois


def test_l_annee_de_la_carte_se_choisit():
    """« Et l'an dernier ? » n'avait aucune reponse : la carte ne montrait
    que les douze derniers mois, sans moyen de remonter."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert 'id="carte-annee"' in page
    bloc = page.split("async function chargerCarte(){")[1].split("\n}")[0]
    assert "p.set('annee'" in bloc, "le choix doit partir au serveur"
    # La liste des annees vient du serveur : la page ne connait pas
    # l'historique, et une liste devinee proposerait des annees vides.
    annees = page.split("function peindreAnnees(d){")[1].split("\n}")[0]
    assert "d.annees" in annees and "d.annee" in annees


def test_les_paliers_de_la_carte_sont_relatifs_au_plus_charge():
    """Un seuil absolu afficherait une carte toute pale sur une installation
    calme, et toute sombre sur une installation chargee. Ce qu'on lit dans
    une carte de chaleur, c'est le relief."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function carteNiveau(")[1].split("\n}")[0]
    assert "sommet" in bloc and "/ sommet" in bloc
    # Cinq paliers, comme le calendrier de GitHub.
    for niveau in range(5):
        assert f".carte-case.n{niveau}" in page or niveau == 0


def test_un_acces_refuse_n_est_pas_une_visite(journal):
    """Le journal sert a savoir si un projet sert encore.

    Compter les refus dedans donnerait a une application fermee l'air d'etre
    tres frequentee, ce qui est exactement l'inverse de ce qu'on demande.
    """
    c = journal
    # Sans session, un projet prive redirige vers la connexion.
    assert c.get("/prive/", follow_redirects=False).status_code == 302
    assert [e for e in app.lire_acces() if e.get("genre") == "ouverture"] == []

    # Un projet public, lui, est une vraie visite -- meme sans compte.
    c.get("/public/")
    ouvertures = [e for e in app.lire_acces() if e.get("genre") == "ouverture"]
    assert len(ouvertures) == 1
    assert ouvertures[0]["app"] == "public" and ouvertures[0]["qui"] == ""


def test_une_page_web_ne_fait_pas_cinquante_lignes_de_journal(journal):
    """Une page, c'est des dizaines de requetes. Les compter toutes ne dirait
    plus rien de la frequentation, et remplirait le disque."""
    c = journal
    for _ in range(30):
        c.get("/public/")
    assert len([e for e in app.lire_acces() if e.get("genre") == "ouverture"]) == 1

    # Le regroupement passe : la visite suivante compte pour une nouvelle.
    app._dernier_acces.clear()
    c.get("/public/")
    assert len([e for e in app.lire_acces() if e.get("genre") == "ouverture"]) == 2


def test_les_connexions_et_les_echecs_sont_notes(journal):
    c = journal
    c.post("/login", json={"password": "faux"})
    c.post("/login", json={"password": "secret-de-test"})
    genres = [e["genre"] for e in app.lire_acces()]
    assert genres[:2] == ["connexion", "echec"], genres   # plus recent d'abord
    resume = app.resume_acces()
    assert resume["echecs"] == 1
    assert resume["comptes"]["admin"]["connexions"] == 1


def test_le_journal_a_un_plafond_au_lieu_de_remplir_le_disque(journal, monkeypatch):
    """Un journal sans plafond transforme une curiosite en panne : au-dela,
    les plus anciens evenements partent (Postgres, lui, garde tout)."""
    monkeypatch.setattr(app, "JOURNAL_MAX", 50)
    app._journal_ecrits[0] = 0
    for i in range(400):
        app.journaliser("ouverture", qui=f"compte{i}", app="public", ip="10.0.0.1")
    restants = app.lire_acces(limite=1000)
    assert len(restants) <= 50 + 200          # la purge passe tous les 200
    # Ce sont les plus ANCIENS qui partent.
    assert restants[0]["qui"] == "compte399"


def test_journaliser_ne_fait_jamais_tomber_le_service(journal, monkeypatch):
    """Disque plein ou montage en lecture seule : le proxy doit continuer.

    Faire echouer une connexion pour proteger son journal reviendrait a
    eteindre le service au moment ou on veut justement l'observer.
    """
    monkeypatch.setattr(app, "ACCES_FILE", "/proc/interdit/acces.jsonl")
    app.journaliser("connexion", qui="marie")          # ne leve pas
    assert journal.get("/public/").status_code in (200, 502, 503)
    assert journal.post("/login", json={"password": "secret-de-test"}).status_code == 200


def test_le_journal_est_reserve_a_l_administrateur(journal):
    """Il contient des adresses IP et le detail de qui ouvre quoi."""
    c = journal
    assert c.get("/api/activite").status_code == 401
    _connecte(c, "marie", "mot-de-passe-long")
    assert c.get("/api/activite").status_code == 403


# ---------- 17. adresse publique du serveur ----------
#
# « Publique » veut dire : accessible sans compte. Sur un serveur que
# personne d'autre ne peut joindre, le mot promet une ouverture qui n'existe
# pas -- il ne retire que l'authentification. Tant qu'aucune adresse publique
# n'est declaree, le panneau ne le propose pas, et le serveur le refuse.

@pytest.fixture
def exposition(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "EXPOSITION_FILE", str(tmp_path / "exposition.json"))
    monkeypatch.delenv("APP_MANAGER_PUBLIC_URL", raising=False)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app.save({"deja-public": {"path": "/w/a", "command": "x", "port": 9101,
                              "enabled": True, "visibility": "publique"},
              "prive": {"path": "/w/b", "command": "x", "port": 9102,
                        "enabled": True, "visibility": "privee"}})
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c


def test_sans_adresse_publique_on_ne_peut_pas_ouvrir_une_application(exposition):
    c = exposition
    r = c.post("/api/visibility/prive", json={"visibility": "publique"})
    assert r.status_code == 400
    assert "adresse publique" in r.get_json()["error"]
    assert app.load()["prive"]["visibility"] == "privee"

    # Le chemin qui REFERME n'est jamais bloque : une application deja
    # publique doit toujours pouvoir redevenir privee.
    assert c.post("/api/visibility/deja-public",
                  json={"visibility": "privee"}).status_code == 200
    assert app.load()["deja-public"]["visibility"] == "privee"


def test_une_application_neuve_nait_privee_sur_un_serveur_prive(exposition):
    """Le defaut sur lequel on ne peut pas se tromper : elle s'ouvre en une
    bascule, alors qu'une application ouverte par megarde ne se referme
    qu'apres coup."""
    c = exposition
    r = c.post("/api/add", json={"name": "neuf", "path": "/w/neuf",
                                 "command": "x", "visibility": "publique"})
    assert r.status_code == 200, r.data
    assert app.load()["neuf"]["visibility"] == "privee"


def test_une_fois_l_adresse_declaree_le_partage_redevient_possible(exposition):
    c = exposition
    assert c.put("/api/securite/exposition",
                 json={"adresse_publique": "pas une adresse"}).status_code == 400
    r = c.put("/api/securite/exposition",
              json={"adresse_publique": "https://codelab.example.com/"})
    assert r.status_code == 200, r.data
    # L'adresse est rangee sans sa barre finale : elle sert de prefixe.
    assert app.adresse_publique() == "https://codelab.example.com"
    assert c.get("/api/securite").get_json()["adresse_publique"] == "https://codelab.example.com"
    assert c.post("/api/visibility/prive", json={"visibility": "publique"}).status_code == 200


# ---------- HTTPS et proxy de confiance, regles depuis la page ------------
#
# Ces deux reglages vivaient uniquement dans le compose, en commentaire. Le
# code disait pourquoi : « l'activer depuis une page servie en clair
# deconnecterait sur-le-champ la session qui vient de l'activer, sans moyen
# de revenir en arriere. »
#
# L'objection etait juste. Ce qui la leve n'est pas de l'ignorer, c'est de
# rendre le cas impossible : on n'allume que ce que la requete en cours
# justifie. Ces tests tiennent exactement cette promesse -- et le contraire,
# qui compte autant : ETEINDRE reste possible en toutes circonstances.


def test_https_ne_s_active_pas_depuis_une_page_en_clair(exposition):
    """Le verrou anti-enfermement. Sans lui, un clic depuis http posait un
    cookie Secure que le navigateur cessait d'envoyer : plus de session, et
    plus de page pour revenir en arriere."""
    c = exposition
    r = c.put("/api/securite/exposition", json={"https": True})
    assert r.status_code == 400, r.data
    assert "deconnecterait" in r.get_json()["error"]
    assert app.https_actif() is False
    assert app.flask_app.config["SESSION_COOKIE_SECURE"] is False


def test_https_s_active_depuis_une_page_en_https(exposition):
    c = exposition
    r = c.put("/api/securite/exposition", json={"https": True},
              base_url="https://localhost")
    assert r.status_code == 200, r.data
    assert app.https_actif() is True
    # Le cookie suit tout de suite : c'est l'interet de ne plus figer au
    # demarrage. Sans cette ligne, le reglage serait enregistre et sans effet
    # jusqu'au prochain redemarrage -- une case qui ment.
    assert app.flask_app.config["SESSION_COOKIE_SECURE"] is True


def test_https_s_active_derriere_un_proxy_declare(exposition):
    """Le cas courant : le TLS se termine au proxy, la requete arrive ici en
    clair et n'annonce https que par un en-tete. Sans ce chemin, la case
    serait inatteignable la ou elle sert le plus."""
    c = exposition
    assert c.put("/api/securite/exposition", json={"trust_proxy": True},
                 headers={"X-Forwarded-Proto": "https"}).status_code == 200
    r = c.put("/api/securite/exposition", json={"https": True},
              headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200, r.data
    assert app.https_actif() is True


def test_eteindre_https_reste_possible_depuis_une_page_en_clair(exposition):
    """La marche arriere ne doit dependre d'aucune condition : c'est elle
    qu'on cherche quand tout va mal."""
    c = exposition
    c.put("/api/securite/exposition", json={"https": True},
          base_url="https://localhost")
    assert app.https_actif() is True
    r = c.put("/api/securite/exposition", json={"https": False})
    assert r.status_code == 200, r.data
    assert app.https_actif() is False


def test_le_proxy_ne_se_declare_pas_sans_proxy(exposition):
    """Croire X-Forwarded-For sans proxy devant, c'est laisser n'importe quel
    client s'inventer une adresse a chaque essai -- et annuler la limite de
    tentatives de connexion. La page ne doit pas permettre cette regression."""
    c = exposition
    r = c.put("/api/securite/exposition", json={"trust_proxy": True})
    assert r.status_code == 400, r.data
    assert "X-Forwarded" in r.get_json()["error"]
    assert app.trust_proxy() is False


def test_le_proxy_se_declare_quand_il_est_la(exposition):
    c = exposition
    r = c.put("/api/securite/exposition", json={"trust_proxy": True},
              headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 200, r.data
    # Lu a chaud, sans redemarrage : c'est tout l'objet du changement.
    assert app.trust_proxy() is True


def test_enregistrer_l_adresse_n_efface_pas_les_deux_autres_reglages(exposition):
    """Le fichier portait un seul reglage et etait reecrit en entier. Avec
    trois, enregistrer l'adresse effacait HTTPS et le proxy en silence."""
    c = exposition
    c.put("/api/securite/exposition", json={"https": True},
          base_url="https://localhost")
    c.put("/api/securite/exposition", json={"trust_proxy": True},
          headers={"X-Forwarded-For": "203.0.113.7"})

    r = c.put("/api/securite/exposition",
              json={"adresse_publique": "https://codelab.example.com"})
    assert r.status_code == 200, r.data
    assert app.https_actif() is True, "HTTPS efface par l'enregistrement de l'adresse"
    assert app.trust_proxy() is True, "le proxy efface par l'enregistrement de l'adresse"


def test_le_compose_l_emporte_sur_la_page_pour_https_et_le_proxy(exposition, monkeypatch):
    """Meme regle que pour l'adresse, et pour la meme raison : la page ne doit
    pas laisser modifier ce qu'un redemarrage remettrait. C'est aussi la seule
    marche arriere qui ne passe pas par le panneau."""
    c = exposition
    monkeypatch.setenv("APP_MANAGER_HTTPS", "1")
    monkeypatch.setenv("APP_MANAGER_TRUST_PROXY", "1")
    assert app.https_actif() is True
    assert app.trust_proxy() is True

    etat = c.get("/api/securite").get_json()
    assert etat["https_fige"] is True and etat["trust_proxy_fige"] is True

    assert c.put("/api/securite/exposition",
                 json={"https": False}).status_code == 400
    assert c.put("/api/securite/exposition",
                 json={"trust_proxy": False}).status_code == 400
    assert app.https_actif() is True


# ---------- 12 bis. l'administration reste sur le reseau local ----------
#
# Le raisonnement : un compte nomme est fait pour etre distribue et n'ouvre
# que les projets qu'on lui a autorises. Le compte d'administration, lui,
# permet de declarer une application -- donc d'executer du code sur la
# machine. Les deux n'ont aucune raison d'etre joignables de la meme facon.
#
# Ce que ces tests tiennent, dans l'ordre d'importance :
#
#   1. un compte nomme entre toujours de l'exterieur -- c'est la moitie de
#      la demande, et celle qu'une implementation trop large casserait ;
#   2. on ne peut pas activer ce reglage depuis l'exterieur, sous peine de
#      se retirer l'administration dans la seconde ;
#   3. derriere un proxy non declare, l'administration est REFUSEE et non
#      accordee : toutes les requetes y paraissent locales, et une securite
#      qui ment est pire que pas de securite.

DEHORS = {"REMOTE_ADDR": "203.0.113.7"}      # bloc de documentation, jamais prive
CHEZ_SOI = {"REMOTE_ADDR": "192.168.1.42"}


def _active_le_verrou(client):
    """Active le reglage depuis le reseau local, seule facon permise."""
    r = client.put("/api/securite/exposition", json={"admin_reseau_local": True})
    assert r.status_code == 200, r.data
    assert app.admin_limite_au_reseau_local() is True
    return r


def test_une_adresse_illisible_n_ouvre_pas_l_administration():
    """Le sens du doute. Une adresse qu'on ne sait pas lire ne doit jamais
    valoir "locale" : c'est un controle d'acces, pas un affichage."""
    assert app.adresse_est_locale("192.168.1.42") is True
    assert app.adresse_est_locale("10.0.0.1") is True
    assert app.adresse_est_locale("127.0.0.1") is True
    assert app.adresse_est_locale("::1") is True
    assert app.adresse_est_locale("fd00::1") is True
    # 100.64/10, l'espace partage : un reseau prive type Tailscale est une
    # extension de la maison, pas l'Internet. Python l'EXCLUT de is_private
    # selon sa version -- raison pour laquelle la liste est ecrite a la main.
    assert app.adresse_est_locale("100.100.1.2") is True
    # Une pile double presente parfois une adresse v4 sous forme mappee.
    assert app.adresse_est_locale("::ffff:192.168.1.42") is True
    assert app.adresse_est_locale("8.8.8.8") is False
    # Les plages de DOCUMENTATION : is_private les dit "privees", elles n'ont
    # rien de local. C'est ce piege qui a fait ecrire la liste a la main.
    assert app.adresse_est_locale("203.0.113.7") is False
    assert app.adresse_est_locale("2001:db8::1") is False
    assert app.adresse_est_locale("unknown") is False
    assert app.adresse_est_locale("") is False
    assert app.adresse_est_locale(None) is False


def test_sans_le_reglage_l_admin_entre_de_partout(exposition):
    """L'etat par defaut ne change pas : ce serait une rupture pour qui
    administre deja depuis l'exterieur sans avoir rien demande."""
    assert app.admin_limite_au_reseau_local() is False
    c = app.flask_app.test_client()
    r = c.post("/login", json={"password": "secret-de-test"}, environ_base=DEHORS)
    assert r.status_code == 200, r.data


def test_l_admin_ne_se_connecte_plus_depuis_l_exterieur(exposition):
    _active_le_verrou(exposition)
    c = app.flask_app.test_client()
    r = c.post("/login", json={"password": "secret-de-test"}, environ_base=DEHORS)
    assert r.status_code == 403, r.data
    assert "reseau local" in r.get_json()["error"]
    # Et le mot de passe n'a meme pas ete regarde : aucune session ouverte.
    assert r.get_json().get("ok") is not True


def test_l_admin_se_connecte_toujours_depuis_chez_lui(exposition):
    _active_le_verrou(exposition)
    c = app.flask_app.test_client()
    r = c.post("/login", json={"password": "secret-de-test"}, environ_base=CHEZ_SOI)
    assert r.status_code == 200, r.data


def test_un_compte_nomme_entre_toujours_depuis_l_exterieur(exposition):
    """LA moitie de la demande qu'une implementation trop large casserait :
    le verrou ne vise que l'administration, jamais les comptes nommes."""
    _active_le_verrou(exposition)
    sel = "bb" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": ["prive"], "cree": 0}})

    c = app.flask_app.test_client()
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"},
               environ_base=DEHORS)
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d.get("inscription") is True, "le compte nomme a ete refuse de l'exterieur"
    r = c.post("/login/second-facteur",
               json={"code": app.totp_code(d["secret"], int(time.time()) // app.TOTP_PAS)},
               environ_base=DEHORS)
    assert r.status_code == 200, r.data
    assert r.get_json()["role"] == app.ROLE_UTILISATEUR


def test_une_session_admin_qui_sort_cesse_d_administrer(exposition):
    """Verifie a chaque requete, pas seulement a la connexion : un portable
    ouvert dans le salon puis repris depuis un train -- ou un cookie vole --
    ne doit plus administrer une fois dehors."""
    c = exposition
    _active_le_verrou(c)
    # La meme session, le meme cookie, depuis l'exterieur.
    r = c.get("/api/apps", environ_base=DEHORS)
    assert r.status_code == 403, r.data
    assert "reseau local" in r.get_json()["error"]
    # Et elle fonctionne toujours depuis chez soi.
    assert c.get("/api/apps", environ_base=CHEZ_SOI).status_code == 200


def test_on_n_active_pas_ce_reglage_depuis_l_exterieur(exposition):
    """Meme regle que pour HTTPS, et pour la meme raison : on n'allume pas un
    interrupteur qui couperait la branche sur laquelle on est assis."""
    c = exposition
    r = c.put("/api/securite/exposition", json={"admin_reseau_local": True},
              environ_base=DEHORS)
    assert r.status_code == 400, r.data
    assert "ne vient pas du" in r.get_json()["error"]
    assert app.admin_limite_au_reseau_local() is False


def test_eteindre_depuis_le_reseau_local_reste_toujours_permis(exposition):
    c = exposition
    _active_le_verrou(c)
    r = c.put("/api/securite/exposition", json={"admin_reseau_local": False})
    assert r.status_code == 200, r.data
    assert app.admin_limite_au_reseau_local() is False


def test_un_proxy_non_declare_interdit_d_activer(exposition):
    """Le piege que ce reglage doit absolument eviter : derriere un proxy non
    declare, remote_addr est l'adresse DU PROXY -- privee. Toute requete
    paraitrait locale et le reglage serait affiche actif sans rien proteger."""
    c = exposition
    r = c.put("/api/securite/exposition", json={"admin_reseau_local": True},
              headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 400, r.data
    assert "proxy" in r.get_json()["error"].lower()
    assert app.admin_limite_au_reseau_local() is False


def test_derriere_un_proxy_non_declare_l_administration_est_refusee(exposition):
    """Le choix qui compte : ne pas savoir vaut REFUSER.

    Si un proxy apparait apres coup sans etre declare, l'administration se
    ferme -- elle ne s'ouvre pas a tout le monde. La marche arriere est le
    compose, comme pour les autres reglages.
    """
    c = exposition
    _active_le_verrou(c)
    r = c.get("/api/apps", headers={"X-Forwarded-For": "192.168.1.42"})
    assert r.status_code == 403, r.data
    assert "proxy" in r.get_json()["error"].lower()


def test_un_proxy_declare_rend_l_adresse_reelle_a_nouveau_lisible(exposition):
    """Une fois le proxy declare, c'est X-Forwarded-For qui fait foi -- et le
    verrou redevient utile au lieu d'etre bloquant."""
    c = exposition
    c.put("/api/securite/exposition", json={"trust_proxy": True},
          headers={"X-Forwarded-For": "192.168.1.42"})
    _active_le_verrou(c)
    assert c.get("/api/apps",
                 headers={"X-Forwarded-For": "192.168.1.42"}).status_code == 200
    r = c.get("/api/apps", headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 403, r.data


def test_une_adresse_locale_injectee_par_le_client_ne_passe_pas_le_verrou(exposition):
    """L'attaque : depuis Internet, envoyer soi-meme « X-Forwarded-For:
    192.168.1.42 ». nginx AJOUTE l'adresse reelle a la suite : c'est elle,
    la derniere, qui doit compter."""
    c = exposition
    c.put("/api/securite/exposition", json={"trust_proxy": True},
          headers={"X-Forwarded-For": "192.168.1.42"})
    _active_le_verrou(c)
    r = c.get("/api/apps", headers={"X-Forwarded-For": "192.168.1.42, 203.0.113.7"})
    assert r.status_code == 403, r.data
    assert c.get("/api/apps", headers={
        "X-Forwarded-For": "203.0.113.7, 192.168.1.42"}).status_code == 200


def test_changer_d_adresse_inventee_ne_contourne_pas_la_limite(client, monkeypatch):
    monkeypatch.setattr(app, "trust_proxy", lambda: True)
    app._login_attempts.clear()
    for i in range(app.RATE_LIMIT_MAX):
        client.post("/login", json={"password": "faux"},
                    headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"})
    r = client.post("/login", json={"password": "faux"},
                    headers={"X-Forwarded-For": "10.9.9.200, 203.0.113.7"})
    assert r.status_code == 429


def test_le_compose_peut_rouvrir_l_administration(exposition, monkeypatch):
    """La seule marche arriere qui ne passe pas par le panneau, et celle qui
    compte le jour ou l'on se retrouve enferme dehors."""
    c = exposition
    _active_le_verrou(c)
    monkeypatch.setenv("APP_MANAGER_ADMIN_LAN_ONLY", "0")
    assert app.admin_limite_au_reseau_local() is False
    autre = app.flask_app.test_client()
    assert autre.post("/login", json={"password": "secret-de-test"},
                      environ_base=DEHORS).status_code == 200


def test_le_compose_peut_aussi_l_imposer(exposition, monkeypatch):
    c = exposition
    monkeypatch.setenv("APP_MANAGER_ADMIN_LAN_ONLY", "1")
    assert app.admin_limite_au_reseau_local() is True
    r = c.put("/api/securite/exposition", json={"admin_reseau_local": False})
    assert r.status_code == 400, r.data
    assert "compose" in r.get_json()["error"]


def test_une_cle_d_acces_ne_contourne_pas_le_verrou(exposition):
    """Une cle d'acces vaut mot de passe ET second facteur, mais elle voyage
    avec son porteur et ne dit rien d'ou l'on appelle. Sans verrou sur cette
    route, il suffirait de passer par cette porte-ci."""
    if not app.passkeys_disponibles():
        pytest.skip("webauthn absent de cette image")
    _active_le_verrou(exposition)
    app.ecrire_passkeys({app.NOM_ADMIN: [
        {"id": "cle-de-test", "cle": "", "compteur": 0, "cree": 0, "nom": "test"}]})

    c = app.flask_app.test_client()
    with c.session_transaction() as sess:
        sess["passkey_defi"] = base64.b64encode(b"defi-de-test").decode()
    r = c.post("/login/passkey", json={"credential": {"id": "cle-de-test"}},
               environ_base=DEHORS)
    assert r.status_code == 403, r.data
    assert "reseau local" in r.get_json()["error"]


def test_marteler_le_refus_finit_par_etre_limite(exposition):
    """Un refus doit couter quelque chose a qui le provoque.

    Sinon il est gratuit : on le martele sans jamais etre limite, et comme
    chaque appel ecrit une ligne de journal, la rotation finit par chasser
    l'historique reel -- une facon discrete d'effacer ses traces.
    """
    _active_le_verrou(exposition)
    c = app.flask_app.test_client()
    vus = set()
    for _ in range(app.RATE_LIMIT_MAX + 2):
        vus.add(c.post("/login", json={"password": "peu importe"},
                       environ_base=DEHORS).status_code)
    assert 403 in vus, "le verrou n'a jamais refuse"
    assert 429 in vus, "le refus est gratuit : aucune limite ne s'applique"


def test_la_page_recoit_de_quoi_griser_la_case_et_dire_pourquoi(exposition):
    """Une case grisee sans raison fait cliquer dans le vide. La page a besoin
    des trois : l'etat, si elle peut etre cochee, et d'ou l'on appelle."""
    r = exposition.get("/api/securite", environ_base=DEHORS)
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["admin_reseau_local"] is False
    assert d["peut_activer_admin_reseau_local"] is False
    assert d["client_local"] is False
    assert d["client_ip"] == "203.0.113.7"
    assert d["admin_reseau_local_fige"] is False


def test_l_adresse_du_compose_l_emporte_sur_celle_de_la_page(exposition, monkeypatch):
    """Sinon la page laisserait modifier ce qu'un redemarrage remettrait."""
    c = exposition
    monkeypatch.setenv("APP_MANAGER_PUBLIC_URL", "https://depuis-le-compose.example/")
    assert app.adresse_publique() == "https://depuis-le-compose.example"
    etat = c.get("/api/securite").get_json()
    assert etat["adresse_figee"] is True
    assert c.put("/api/securite/exposition",
                 json={"adresse_publique": "https://autre.example"}).status_code == 400


# ---------- 18. cles d'acces (passkeys) ----------
#
# Le navigateur impose ses conditions : HTTPS, un nom de domaine, pas une
# adresse IP. Ce qui doit rester vrai cote serveur : on ANNONCE ces
# conditions au lieu de laisser le bouton echouer, on ne croit pas un proxy
# qu'on n'a pas declare, et une cle inconnue n'ouvre rien.
#
# La ceremonie WebAuthn elle-meme (signature, attestation) se verifie au
# navigateur, avec un authentificateur virtuel : elle ne tient pas dans un
# test sans navigateur.

@pytest.fixture
def cles(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "PASSKEYS_FILE", str(tmp_path / "passkeys.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "trust_proxy", lambda: False)
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()


def _etat_passkeys(client, **entetes):
    return client.get("/api/passkeys/etat", headers=entetes).get_json()


def test_les_conditions_du_navigateur_sont_annoncees(cles, monkeypatch):
    """Un bouton qui echoue toujours est pire qu'un bouton absent."""
    pytest.importorskip("webauthn")
    # En clair, sur autre chose que localhost : impossible, et on dit pourquoi.
    d = _etat_passkeys(cles, Host="codelab.example.com")
    assert d["possible"] is False and "HTTPS" in d["empechement"]

    # Une adresse IP ne peut pas servir de relying party id -- meme en HTTPS,
    # et c'est bien la regle de l'IP qui doit refuser, pas celle du TLS.
    monkeypatch.setattr(app, "trust_proxy", lambda: True)
    d = _etat_passkeys(cles, Host="192.168.1.20:9001", **{"X-Forwarded-Proto": "https"})
    assert d["possible"] is False
    assert "nom de domaine" in d["empechement"]
    monkeypatch.setattr(app, "trust_proxy", lambda: False)

    # localhost est un contexte securise pour le navigateur : ca marche.
    assert _etat_passkeys(cles, Host="localhost:9001")["possible"] is True


def test_un_proxy_non_declare_n_est_pas_cru_sur_parole(cles, monkeypatch):
    """X-Forwarded-Proto se pose par n'importe quel client.

    Le message change alors de nature : ce n'est pas « mets du TLS », c'est
    « declare ton proxy » -- et c'est la difference entre chercher une heure
    et poser une variable.
    """
    pytest.importorskip("webauthn")
    entetes = {"Host": "codelab.example.com", "X-Forwarded-Proto": "https"}
    d = _etat_passkeys(cles, **entetes)
    assert d["possible"] is False
    assert "Proxy de confiance" in d["empechement"]

    monkeypatch.setattr(app, "trust_proxy", lambda: True)
    assert _etat_passkeys(cles, **entetes)["possible"] is True


def test_une_cle_inconnue_n_ouvre_aucune_session(cles):
    """C'est la signature qui fait foi, jamais le nom annonce par le client."""
    pytest.importorskip("webauthn")
    entetes = {"Host": "localhost:9001"}
    with cles.session_transaction() as s:
        s["passkey_defi"] = base64.b64encode(b"defi").decode()
    r = cles.post("/login/passkey", headers=entetes, json={
        "credential": {"id": "cle-qui-n-existe-pas", "response": {}}})
    assert r.status_code == 401
    with cles.session_transaction() as s:
        assert s.get("authed") is not True
    assert [e["motif"] for e in app.lire_acces() if e["genre"] == "echec"] == \
        ["cle d'acces inconnue"]


def test_les_cles_sont_celles_du_compte_connecte(cles):
    """La liste et la suppression ne parlent jamais d'un autre compte."""
    pytest.importorskip("webauthn")
    app.ecrire_passkeys({
        "admin": [{"id": "AAA", "cle_publique": "x", "compteur": 0, "nom": "Telephone"}],
        "marie": [{"id": "BBB", "cle_publique": "y", "compteur": 0, "nom": "Portable"}],
    })
    assert cles.get("/api/mon-compte/passkeys").status_code == 401
    cles.post("/login", json={"password": "secret-de-test"})
    liste = cles.get("/api/mon-compte/passkeys").get_json()["passkeys"]
    assert [k["id"] for k in liste] == ["AAA"]
    # La cle publique ne sort pas : elle n'apprend rien a l'interface.
    assert "cle_publique" not in liste[0]
    # Celle de quelqu'un d'autre ne se supprime pas depuis ce compte.
    assert cles.delete("/api/mon-compte/passkeys/BBB").status_code == 404
    assert len(app.lire_passkeys()["marie"]) == 1


# ---------- 19. modifier un compte ----------
#
# L'administrateur pouvait remettre un second facteur a zero, mais pas
# retirer les cles d'acces : un compte restait ouvrable par un telephone
# perdu. Et surtout, supprimer un compte laissait ses cles derriere lui.

def test_supprimer_un_compte_emporte_ses_cles_d_acces(cles):
    """Le cas qui rouvrirait la porte.

    Recreer un compte du meme nom lui rendrait les cles de l'ancien --
    l'appareil de la personne partie ouvrirait de nouveau la session.
    """
    sel = "dd" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": [], "cree": 0}})
    app.ecrire_passkeys({"marie": [{"id": "AAA", "cle_publique": "x", "compteur": 0}]})

    cles.post("/login", json={"password": "secret-de-test"})
    assert cles.delete("/api/utilisateurs/marie").status_code == 200
    assert "marie" not in app.lire_passkeys()

    # Le nom redevient libre, et le compte recree part de zero.
    r = cles.post("/api/utilisateurs", json={"nom": "marie",
                                             "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 200, r.data
    assert app.lire_passkeys().get("marie", []) == []


def test_l_administrateur_peut_retirer_les_cles_d_un_compte(cles):
    """Appareil perdu : meme geste que la remise a zero du second facteur."""
    sel = "ee" * 16
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("mot-de-passe-long", sel),
        "projets": [], "cree": 0, "totp": app.totp_nouveau_secret()}})
    app.ecrire_passkeys({"marie": [{"id": "AAA", "cle_publique": "x", "compteur": 0}],
                         "admin": [{"id": "BBB", "cle_publique": "y", "compteur": 0}]})
    cles.post("/login", json={"password": "secret-de-test"})

    # Le compte annonce ce qu'il a, sans le livrer.
    comptes = cles.get("/api/utilisateurs").get_json()["utilisateurs"]
    marie = [c for c in comptes if c["nom"] == "marie"][0]
    assert marie["passkeys"] == 1
    assert "cle_publique" not in json.dumps(comptes)

    assert cles.put("/api/utilisateurs/marie",
                    json={"retirer_passkeys": True}).status_code == 200
    assert "marie" not in app.lire_passkeys()
    # Celles des autres comptes ne bougent pas.
    assert len(app.lire_passkeys()["admin"]) == 1
    # Et le compte, lui, existe toujours : on retire un moyen d'entrer, pas
    # la personne.
    assert "marie" in app.lire_utilisateurs()


# ---------- 20. le serveur d'envoi n'est pas les alertes ----------
#
# Un serveur SMTP parfaitement configure passait pour incomplet tant qu'aucun
# destinataire d'alerte n'etait saisi -- alors qu'il sert aussi les codes de
# verification d'adresse et l'inscription libre, qui n'ont rien a voir avec
# les alertes. Les deux ont chacun leur onglet, et le test d'envoi doit
# pouvoir viser une adresse sans qu'aucune alerte soit reglee.

@pytest.fixture
def envoi(tmp_path, monkeypatch):
    """Un panneau connecte, avec une configuration d'envoi D'ORIGINE posee
    dans credentials.env -- celle qui doit survivre a tout."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
    monkeypatch.setattr(app, "SMTP_FILE", str(tmp_path / "smtp.json"))
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    app._apps_cache["signature"] = None
    app._alertes_en_cours.clear()
    app.ecrire_bloc_alertes({"SMTP_HOST": "origine.example.com",
                             "SMTP_USER": "origine@example.com",
                             "SMTP_PASSWORD": "mot-de-passe-origine"})
    app.ecrire_alertes(False, [])
    app.save({"site": {"path": "/workspace/site", "command": "python3 app.py",
                       "port": 9101, "enabled": True}})
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c


def test_le_serveur_d_envoi_se_declare_pret_sans_alerte_reglee(envoi):
    """Le serveur d'envoi et les alertes sont deux choses : un serveur
    parfaitement configure passerait pour incomplet tant qu'aucune adresse
    d'alerte n'est saisie, alors qu'il sert aussi les codes de verification."""
    etat = envoi.get("/api/alertes").get_json()
    assert etat["smtp_ok"] is True
    assert etat["source"] == "origine"
    assert etat["origine_utilisable"] is True
    assert etat["personnalisee"] is None
    # Ca, en revanche, c'est l'affaire des alertes.
    assert "adresse d'alerte de l'administrateur" in etat["manquants"]


def test_une_configuration_qui_ne_repond_pas_n_est_pas_enregistree(envoi, monkeypatch):
    """Ce qui remplace l'ancien bouton "mail de test" : un test qu'il fallait
    penser a lancer, et dont l'oubli ne se voyait pas. Ici une configuration
    qui ne marche pas n'entre tout simplement pas."""
    monkeypatch.setattr(app, "verifier_smtp",
                        lambda cfg: (False, "SMTPAuthenticationError: 535 refuse"))
    r = envoi.post("/api/alertes", json={
        "actif": False, "admin": ["moi@example.com"],
        "smtp": {"host": "faux.example.com", "user": "x@example.com",
                 "password": "mauvais"}})
    assert r.status_code == 400, r.data
    assert "535 refuse" in r.get_json()["detail"]
    assert app.lire_smtp_personnalise() is None, "une configuration refusee a ete ecrite"
    # Et surtout : celle d'origine sert toujours.
    assert app.smtp_origine()["host"] == "origine.example.com"


def test_une_configuration_qui_repond_devient_la_source(envoi, monkeypatch):
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    r = envoi.post("/api/alertes", json={
        "actif": False, "admin": ["moi@example.com"],
        "smtp": {"host": "perso.example.com", "user": "perso@example.com",
                 "password": "bon"}})
    assert r.status_code == 200, r.data
    assert r.get_json()["source"] == "personnalisee"
    cfg, _ = app.config_smtp()
    assert cfg["host"] == "perso.example.com"
    # L'ORIGINE EST INTACTE. C'est tout l'objet de la separation : se tromper
    # d'un caractere depuis une page ne doit pas supprimer le seul moyen de
    # prevenir qu'une application est tombee.
    assert app.smtp_origine()["host"] == "origine.example.com"
    assert app.smtp_origine()["password"] == "mot-de-passe-origine"


def test_enregistrer_l_adresse_admin_n_efface_pas_la_configuration_d_origine(envoi):
    """Le bloc de credentials.env est REMPLACE en entier par upsert_shared_block.
    Y ecrire la seule adresse d'administration effacerait le serveur d'envoi et
    son mot de passe -- donc le repli que tout ceci construit."""
    r = envoi.post("/api/alertes", json={"actif": True,
                                         "admin": ["chef@example.com"]})
    assert r.status_code == 200, r.data
    origine = app.smtp_origine()
    assert origine["host"] == "origine.example.com", "le serveur d'origine a ete efface"
    assert origine["password"] == "mot-de-passe-origine", "le mot de passe a ete efface"
    assert app.alertes_admin() == ["chef@example.com"]


def test_revenir_a_l_origine_est_toujours_permis(envoi, monkeypatch):
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    envoi.post("/api/alertes", json={"actif": False, "admin": ["moi@example.com"],
                                     "smtp": {"host": "perso.example.com",
                                              "user": "p@example.com",
                                              "password": "bon"}})
    assert app.lire_smtp_personnalise() is not None
    # Serveur vide = retour a l'origine. Sans condition : c'est la marche
    # arriere, et on la cherche justement quand rien ne va.
    r = envoi.post("/api/alertes", json={"actif": False, "admin": ["moi@example.com"],
                                         "smtp": {"host": ""}})
    assert r.status_code == 200, r.data
    assert app.lire_smtp_personnalise() is None
    assert app.config_smtp()[0]["host"] == "origine.example.com"


def test_l_envoi_repli_sur_l_origine_quand_la_personnalisee_tombe(envoi, monkeypatch):
    """Une configuration verifiee le jour de son enregistrement peut cesser de
    marcher : mot de passe revoque, quota, serveur eteint. Ce jour-la, l'alerte
    doit sortir quand meme."""
    monkeypatch.setattr(app, "verifier_smtp", lambda cfg: (True, ""))
    envoi.post("/api/alertes", json={"actif": True, "admin": ["moi@example.com"],
                                     "smtp": {"host": "perso.example.com",
                                              "user": "p@example.com",
                                              "password": "bon"}})
    essais = []

    def _envoi(cfg, sujet, corps, destinataires=None):
        essais.append(cfg["host"])
        if cfg["host"] == "perso.example.com":
            raise OSError("serveur injoignable")
    monkeypatch.setattr(app, "envoyer_mail", _envoi)

    envoye, detail = app.envoyer_avec_repli("sujet", "corps", ["moi@example.com"])
    assert envoye is True, detail
    assert essais == ["perso.example.com", "origine.example.com"], (
        "le repli n'a pas eu lieu dans cet ordre")
    assert detail == "d'origine"


def test_chaque_application_a_ses_destinataires_et_l_admin_recoit_tout(envoi):
    """Une application de facturation ne previent pas les memes personnes
    qu'un site vitrine. Mais l'adresse d'administration s'ajoute TOUJOURS :
    une application dont la liste est vide ne tombe pas en silence."""
    envoi.post("/api/alertes", json={"actif": True, "admin": ["chef@example.com"]})
    r = envoi.put("/api/alertes/application/site",
                  json={"alertes": "equipe@example.com, autre@example.com"})
    assert r.status_code == 200, r.data
    assert r.get_json()["destinataires"] == ["chef@example.com",
                                             "equipe@example.com",
                                             "autre@example.com"]
    # Une application sans liste propre : l'admin reste, et il est seul.
    app.save(dict(app.load(), vitrine={"path": "/w/v", "command": "x",
                                       "port": 9102, "enabled": True}))
    assert app.destinataires_alerte("vitrine") == ["chef@example.com"]


def test_l_adresse_d_alerte_d_une_installation_existante_est_reprise(envoi):
    """Avant les alertes par application, tous les destinataires vivaient dans
    alertes.json. Sans repli sur cette liste, une installation existante aurait
    cesse d'etre prevenue a la mise a jour -- silencieusement."""
    app.ecrire_bloc_alertes({"ALERTE_ADMIN": ""})
    app.ecrire_alertes(True, ["ancien@example.com"])
    assert app.alertes_admin() == ["ancien@example.com"]


def test_activer_les_alertes_sans_adresse_d_administration_est_refuse(envoi):
    r = envoi.post("/api/alertes", json={"actif": True, "admin": []})
    assert r.status_code == 400, r.data
    assert "administrateur" in r.get_json()["error"]


# ---------- 21. miroir Postgres ----------
#
# Le fichier reste la source de verite : il tient sans base et se lit depuis
# une session SSH. Postgres est la memoire longue -- le fichier est plafonne
# a 1 Mo et oublie. Ce qui doit rester vrai : ecrire dans la base n'est
# JAMAIS sur le chemin d'une requete, et le rejeu ne double aucune ligne.

def test_journaliser_ne_depend_jamais_de_la_base(tmp_path, monkeypatch):
    """Une base eteinte, lente, ou une file pleine : la connexion passe quand
    meme. C'est la seule propriete qui compte -- le reste n'est que du
    journal."""
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PG_ACTIF", True)
    # File pleine : chaque depot est perdu, sans exception ni attente.
    pleine = queue.Queue(maxsize=1)
    pleine.put(("acces", {}))
    monkeypatch.setattr(app, "_pg_file", pleine)
    perdus = app._pg_etat["perdus"]

    debut = time.time()
    for _ in range(50):
        app.journaliser("connexion", qui="marie", ip="10.0.0.1")
    assert time.time() - debut < 1.0, "journaliser doit rendre la main tout de suite"
    assert app._pg_etat["perdus"] == perdus + 50
    # Et malgre tout, les 50 evenements sont dans le fichier.
    assert len(app.lire_acces(limite=1000)) == 50


def test_chaque_evenement_porte_un_identifiant(tmp_path, monkeypatch):
    """C'est lui qui rend le rattrapage rejouable.

    Sans identifiant, un rejeu apres une coupure de la base reinsererait les
    memes lignes -- et l'historique compterait double.
    """
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PG_ACTIF", False)
    app.journaliser("connexion", qui="marie")
    app.journaliser("connexion", qui="marie")
    ids = [e["id"] for e in app.lire_acces()]
    assert len(ids) == 2 and len(set(ids)) == 2, "deux evenements, deux identifiants"


def test_le_miroir_se_tait_quand_il_est_debranche(monkeypatch):
    """APP_MANAGER_PG=0, ou psycopg absent : rien ne part, rien ne casse."""
    monkeypatch.setattr(app, "PG_ACTIF", False)
    avant = app._pg_file.qsize()
    app._pg_deposer(("acces", {"id": "x"}))
    assert app._pg_file.qsize() == avant
    assert app.pg_disponible() is False


def test_le_miroir_ecrit_vraiment_dans_postgres(tmp_path, monkeypatch):
    """Contre un vrai serveur, quand il y en a un.

    Ignore sans base joignable : ce test verifie la creation des tables, le
    rejeu idempotent et la trace d'un compte supprime -- des choses qu'un
    faux curseur ne prouverait pas.
    """
    pytest.importorskip("psycopg")
    import psycopg
    dsn = os.environ.get("CODELAB_TEST_PG")
    if not dsn:
        pytest.skip("CODELAB_TEST_PG non defini : pas de serveur de test")
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as e:
        pytest.skip(f"serveur de test injoignable : {e}")

    reglages = psycopg.conninfo.conninfo_to_dict(dsn)
    monkeypatch.setattr(app, "PG_BASE", "codelab_test_" + secrets.token_hex(4))
    monkeypatch.setattr(app, "PG_ACTIF", True)
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "PASSKEYS_FILE", str(tmp_path / "passkeys.json"))
    monkeypatch.setattr(app, "_pg_reglages", lambda: {
        "host": reglages.get("host", "127.0.0.1"), "port": reglages.get("port", "5432"),
        "user": reglages.get("user", "codelab"), "password": reglages.get("password", ""),
        "instance": reglages.get("dbname", "dagster")})

    app.journaliser("connexion", qui="marie", ip="10.0.0.1", role="utilisateur")
    app.journaliser("ouverture", qui="marie", app="site", ip="10.0.0.1")
    app.ecrire_utilisateurs({"marie": {"sel": "aa", "hash": "bb", "projets": ["site"],
                                       "cree": 0, "email": "marie@example.com"}})
    try:
        app._pg_preparer()
        with app._pg_connexion(app.PG_BASE) as cx:
            app._pg_rattraper(cx)
            assert cx.execute("SELECT count(*) FROM acces").fetchone()[0] == 2
            # Rejoue : les memes lignes ne rentrent pas deux fois.
            app._pg_rattraper(cx)
            assert cx.execute("SELECT count(*) FROM acces").fetchone()[0] == 2
            # Aucun secret n'a traverse.
            colonnes = [c[0] for c in cx.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'utilisateurs'").fetchall()]
            assert not ({"hash", "sel", "totp", "cle_publique"} & set(colonnes))
            # Un compte supprime laisse sa trace : le journal le nomme encore.
            app.ecrire_utilisateurs({})
            app._pg_ecrire_utilisateurs(cx)
            reste = cx.execute("SELECT nom, supprime IS NOT NULL FROM utilisateurs").fetchall()
            assert reste == [("marie", True)]
    finally:
        with app._pg_connexion(app._pg_reglages()["instance"]) as cx:
            cx.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                psycopg.sql.Identifier(app.PG_BASE)))


# ---------- 22. jeton CSRF ----------
#
# SameSite=Lax bloque deja un autre SITE. Ce jeton couvre ce qu'il ne couvre
# pas : une requete lancee depuis une AUTRE ORIGINE qui porterait quand meme
# le cookie. Ce qui doit rester vrai :
#
#   - une ecriture sans jeton, sur une session ouverte, est refusee ;
#   - un jeton d'une autre session ne vaut rien ;
#   - les routes de connexion restent atteignables sans jeton (on ne peut pas
#     exiger un jeton d'une session qui n'existe pas encore) ;
#   - le proxy des applications n'est pas concerne ;
#   - une session ouverte porte TOUJOURS un jeton, sinon la garde se
#     contournerait en n'en ayant pas.

def test_une_ecriture_sans_jeton_est_refusee(deux_espaces):
    c = deux_espaces
    r = c.post("/login", json={"password": "secret-de-test"})
    assert r.status_code == 200, r.get_json()

    with c.session_transaction() as sess:
        assert sess.get("jeton"), "une session ouverte doit porter un jeton"

    # Avec le jeton (le client de test le pose, comme fetch dans la page) :
    # l'action passe. C'est le temoin -- sans lui, un 403 ne prouverait que
    # l'existence d'un bug quelconque.
    assert c.post("/api/toggle/public").status_code == 200

    # Le meme cookie de session, jeton vide : c'est exactement la requete
    # qu'une autre origine peut declencher, elle a le cookie mais pas le
    # jeton. Elle doit etre refusee.
    r = c.post("/api/toggle/public", headers={app.JETON_ENTETE: ""})
    assert r.status_code == 403
    assert "jeton" in r.get_json()["error"].lower()


def test_le_jeton_d_une_autre_session_ne_vaut_rien(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/toggle/public",
               headers={app.JETON_ENTETE: secrets.token_urlsafe(32)})
    assert r.status_code == 403


def test_les_routes_de_connexion_restent_ouvertes_sans_jeton(deux_espaces):
    """Sinon plus personne ne peut se connecter : le jeton vit dans la
    session, et la session n'existe pas encore."""
    c = deux_espaces
    r = c.post("/login", json={"password": "mauvais"})
    assert r.status_code == 401           # refuse par le mot de passe...
    assert "jeton" not in r.get_json()["error"].lower()   # ...pas par le jeton
    r = c.post("/login", json={"password": "secret-de-test"})
    assert r.status_code == 200


def test_toutes_les_routes_d_ecriture_sont_couvertes(deux_espaces):
    """La garde est un before_request, pas un decorateur pose route par
    route : ce test le constate sur la table de routage plutot que sur une
    liste ecrite a la main, qui vieillirait mal.

    Une route d'ecriture ajoutee demain sans etre exemptee est protegee
    d'office. Le test echoue seulement si quelqu'un l'AJOUTE aux exemptions.
    """
    ecritures = set()
    for regle in app.flask_app.url_map.iter_rules():
        if app.JETON_METHODES & set(regle.methods or ()):
            ecritures.add(regle.endpoint)
    non_couvertes = ecritures - app.JETON_EXEMPTS
    assert non_couvertes, "aucune route d'ecriture : la table est vide ?"
    # Les exemptions sont celles qu'on a decidees, pas plus.
    assert app.JETON_EXEMPTS == {
        "login_submit", "login_second_facteur",
        "login_passkey_options", "login_passkey",
        # Premiere connexion de l'administrateur : Face ID comme second
        # facteur, apres le mot de passe, sans session encore ouverte.
        "login_inscription_passkey_options", "login_inscription_passkey",
        # Ouverte par la personne invitee, sans session -- ou dans le
        # navigateur de l'administrateur qui essaie son propre lien.
        "invitation_accepter",
        "proxy", "proxy_noslash",
    }


def test_le_get_n_est_jamais_concerne(deux_espaces):
    """Un GET ne change rien. L'exiger la n'apporterait aucune protection et
    casserait la moitie de la page."""
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
    r = c.get("/api/apps", headers={app.JETON_ENTETE: "n'importe quoi"})
    assert r.status_code == 200


# ---------- 23. un uid par application ----------
#
# L'uid partage protegeait les applications DU panneau, pas les unes DES
# autres : meme uid, donc /proc/<pid>/environ d'une application etait lisible
# par sa voisine -- c'est-a-dire le mot de passe Postgres qu'on lui transmet.

def test_l_uid_ne_depend_que_du_nom(monkeypatch):
    """Derive, pas attribue : rien a migrer, et une application qui redemarre
    retrouve ses fichiers. Doit tenir d'un processus a l'autre, donc pas de
    hash() (randomise par PYTHONHASHSEED)."""
    a = app.uid_application("facturier")
    assert a == app.uid_application("facturier")
    assert a != app.uid_application("cahier")
    assert app.UID_APP_BASE <= a < app.UID_APP_BASE + app.UID_APP_PLAGE
    # Jamais root, jamais l'uid du service, jamais l'uid de la session SSH.
    for nom in ("a", "site", "notes", "x" * 32, "projet-2"):
        assert app.uid_application(nom) not in (0, 1000, 1001)


def test_l_uid_survit_a_un_redemarrage_du_panneau():
    """Le vrai risque : un uid different a chaque demarrage rendrait les
    fichiers de l'application illisibles par elle-meme. On relance un
    interpreteur neuf, avec un PYTHONHASHSEED different."""
    import subprocess as sp
    chemin = os.path.join(DOSSIER_PANNEAU, "app", "app.py")
    code = ("import importlib.util,sys;"
            "spec=importlib.util.spec_from_file_location('m', %r);"
            "m=importlib.util.module_from_spec(spec);sys.modules['m']=m;"
            "spec.loader.exec_module(m);print(m.uid_application('facturier'))" % chemin)
    vus = set()
    for graine in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=graine)
        vus.add(sp.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True).stdout.strip())
    assert len(vus) == 1, f"uid instable entre processus : {vus}"


@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_deux_applications_ne_tournent_pas_sous_le_meme_uid(tmp_path):
    """Le test qui compte : on lance vraiment deux process et on regarde sous
    quel uid ils tournent. Verifier uid_application() ne prouverait que
    l'arithmetique -- pas que preexec_fn s'en sert."""
    import subprocess as sp

    def uid_reel(nom):
        r = sp.run(["python3", "-c", "import os;print(os.getuid(), os.getgid())"],
                   capture_output=True, text=True,
                   preexec_fn=app.child_setup(nom=nom))
        return r.stdout.strip()

    a, b = uid_reel("facturier"), uid_reel("cahier")
    assert a != b, f"les deux applications tournent sous le meme uid : {a}"
    # Le groupe, lui, reste commun : c'est lui qui garde /workspace editable
    # depuis une session SSH.
    assert a.split()[1] == b.split()[1] == str(app.RUN_AS_GID)
    # Et aucune ne tourne en root.
    assert a.split()[0] != "0" and b.split()[0] != "0"


@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_ne_lit_pas_le_dossier_personnel_d_une_autre(tmp_path, monkeypatch):
    """~/.npmrc et les jetons qu'un outil y depose appartiennent a une
    application, pas au voisinage."""
    import subprocess as sp
    # Les dossiers temporaires de pytest sont en 0700 root : un uid non
    # privilegie ne peut meme pas les traverser, et TOUT lui serait refuse --
    # le test passerait sans rien prouver. On ouvre la traversee (x) sans
    # ouvrir la lecture du contenu.
    for parent in list(tmp_path.parents)[:3] + [tmp_path]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o011)
        except OSError:
            pass
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    maison_a = app.ensure_child_home("facturier")
    with open(os.path.join(maison_a, "secret"), "w") as f:
        f.write("jeton-de-facturier")
    os.chown(os.path.join(maison_a, "secret"),
             app.uid_application("facturier"), app.RUN_AS_GID)

    # PermissionError precisement, pas "une exception quelconque" : un chemin
    # absent ou un interpreteur qui plante donnerait le meme "refuse" et le
    # test passerait pour la mauvaise raison.
    lecteur = ("import sys\n"
               "try:\n"
               "    open(sys.argv[1]).read()\n"
               "    print('LU')\n"
               "except PermissionError:\n"
               "    print('REFUSE')\n"
               "except Exception as e:\n"
               "    print('AUTRE:' + type(e).__name__)\n")
    cible = os.path.join(maison_a, "secret")

    # Temoin : l'application a qui ce dossier appartient, elle, le lit.
    sien = sp.run(["python3", "-c", lecteur, cible], capture_output=True,
                  text=True, preexec_fn=app.child_setup(nom="facturier"))
    assert sien.stdout.strip() == "LU", sien.stdout + sien.stderr

    autre = sp.run(["python3", "-c", lecteur, cible], capture_output=True,
                   text=True, preexec_fn=app.child_setup(nom="cahier"))
    assert autre.stdout.strip() == "REFUSE", autre.stdout + autre.stderr


def test_le_nom_arrive_bien_jusqu_au_preexec(tmp_path, monkeypatch):
    """uid_application() peut etre parfait et ne servir a rien si start() et
    build() oublient de passer le nom. C'est la jointure qui casse en
    silence : sans ce test, les deux applications repartent sous l'uid
    partage et tous les autres tests restent verts."""
    recu = {}
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app, "STATE_DIR", str(tmp_path))
    app._apps_cache["signature"] = None
    projet = tmp_path / "facturier"
    projet.mkdir()
    app.save({"facturier": {"path": str(projet), "command": "true",
                            "port": 9199, "enabled": False,
                            "build_command": "true"}})

    monkeypatch.setattr(app, "child_setup",
                        lambda max_memory_mb=None, nom=None: recu.setdefault("demarrage", nom))
    def _popen(*a, **kw):
        recu["home"] = (kw.get("env") or {}).get("HOME")
        return type("P", (), {"pid": 1, "poll": lambda s: None})()
    monkeypatch.setattr(app.subprocess, "Popen", _popen)
    app.start("facturier")
    assert recu.get("demarrage") == "facturier"
    # HOME suit l'uid : sinon ~/.npmrc et le cache npm restent en commun, et
    # la separation s'arrete a la porte du dossier personnel.
    assert recu["home"] == app.ensure_child_home("facturier")
    assert recu["home"] != app.ensure_child_home("cahier")

    recu.clear()
    monkeypatch.setattr(app, "child_setup",
                        lambda max_memory_mb=None, nom=None: recu.setdefault("build", nom))
    monkeypatch.setattr(app.subprocess, "run",
                        lambda *a, **kw: type("R", (), {"returncode": 0})())
    app.run_build("facturier")
    assert recu.get("build") == "facturier"


def test_l_enveloppe_de_fetch_regarde_l_origine():
    """Trouve par l'audit de la branche : l'enveloppe posait le jeton sur
    TOUTE ecriture, sans regarder la cible. Aucun appel de la page n'est
    absolu aujourd'hui, donc rien ne fuyait -- mais la protection ne tenait
    que par accident. Un fetch('https://...', {method:'POST'}) ajoute demain
    aurait envoye le jeton de session a un tiers (un en-tete non standard
    declenche un pre-vol CORS, qu'un serveur hostile autorise volontiers).

    Test structurel, et il faut le dire : il constate que la garde est encore
    la, pas qu'elle est correcte. Ce qui prouve qu'elle marche, c'est la
    verification au navigateur contre un vrai serveur tiers -- elle ne tient
    pas dans pytest.
    """
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    debut = page.index("window.fetch = function")
    enveloppe = page[debut:debut + 600]
    assert "memeOrigine(cible)" in enveloppe, (
        "l'enveloppe de fetch n'appelle plus memeOrigine : le jeton peut "
        "partir vers une autre origine")
    # Et la garde compare bien des origines, pas des prefixes de chaine.
    verif = page[page.index("function memeOrigine"):page.index("window.fetch = function")]
    assert "new URL(url, location.href).origin === location.origin" in verif
    assert "return false" in verif, "une cible illisible doit priver du jeton, pas l'accorder"


# ---------- 23 bis. le theme n'existe qu'a un seul endroit ----------
#
# La demande etait "que le style soit facilement modifiable". Ce qui l'en
# empechait n'etait pas le CSS mais la RECOPIE : les memes variables vivaient
# six fois -- trois blocs de theme (clair, sombre automatique, sombre choisi)
# dans chacune des deux pages. Changer un gris demandait six retouches
# identiques, et en oublier une laissait le theme sombre de travers sans que
# rien ne le signale.
#
# Ces deux tests tiennent la propriete, pas la mise en forme : il y a un
# fichier de theme, et les pages n'en redefinissent aucun.

def _page_panneau(nom):
    return open(os.path.join(DOSSIER_PANNEAU, "app", nom), encoding="utf-8").read()


# ---------------- le script des pages doit s'ANALYSER ----------------
#
# Toute l'interface du panneau tient dans une balise <script> de plusieurs
# milliers de lignes, servie telle quelle. Une parenthese en trop et la page
# entiere ne fait plus rien : pas de liste d'applications, pas de bouton, pas
# la moindre erreur visible ailleurs que dans la console du navigateur. Aucun
# test ne le voyait -- ils lisent le fichier comme du TEXTE, et un texte est
# toujours valide.
#
# node est present dans l'image app-manager (il construit les applications) et
# sur les machines de CI. La ou il manque, le test le dit et s'arrete : mieux
# vaut une ligne ignoree, visible, qu'une couverture qu'on croit avoir.

def _scripts_de_la_page(nom):
    """Le contenu de chaque <script> de la page, marqueurs remplaces.

    Le serveur substitue __ROLE__ et compagnie avant d'envoyer : les laisser
    tels quels donnerait une erreur de syntaxe qui n'existe pas en vrai.
    """
    page = _page_panneau(nom)
    scripts = []
    for bloc in re.findall(r"<script>(.*?)</script>", page, re.S):
        bloc = (bloc.replace("__ROLE__", '"admin"')
                    .replace("__UTILISATEUR__", '"moi"'))
        scripts.append(re.sub(r"__[A-Z_]+__", '""', bloc))
    return scripts


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node absent de cette image (present dans app-manager et en CI)")
def test_le_script_des_pages_du_panneau_est_analysable():
    import subprocess
    import tempfile
    for nom in ("dashboard.html", "login.html"):
        scripts = _scripts_de_la_page(nom)
        assert scripts, f"{nom} n'a plus de script : la page ne ferait plus rien"
        for i, script in enumerate(scripts):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as f:
                f.write(script)
                chemin = f.name
            try:
                r = subprocess.run(["node", "--check", chemin],
                                   capture_output=True, text=True, timeout=60)
            finally:
                os.unlink(chemin)
            assert r.returncode == 0, (
                f"{nom}, script #{i} : la page ne s'executerait pas du tout\n"
                + (r.stderr or "")[:800])


# ---------------- les parametres : on replie, on ne deroule plus ----------
#
# Vecu : « Reduis toutes les zones des parametres, je dois pouvoir developper
# pour parametrer, et reduire. » Un onglet deroulait jusqu'a six cartes
# ouvertes -- pour changer une ligne, il fallait traverser tout le reste.

def test_chaque_carte_de_reglages_se_replie():
    """La transformation est faite une fois pour toutes les cartes, et non
    ecrite a la main dans chacune : une carte ajoutee plus tard doit se
    replier sans que personne n'ait a y penser."""
    page = _page_panneau("dashboard.html")
    assert "function plierLesCartes()" in page
    assert ".onglet-p .settings-card:not(.pliable)" in page, (
        "le pliage doit viser toutes les cartes de parametres")
    # Repliee par defaut : c'est tout l'objet de la demande.
    assert ".settings-card.pliable>.card-corps{display:none" in page
    assert ".settings-card.pliable.ouverte>.card-corps{display:block}" in page
    # La pastille d'etat reste lisible carte fermee : c'est ce qu'on vient
    # verifier avant de decider d'ouvrir.
    assert ".settings-card.pliable>.card-top>.pill" in page
    # Et la poignee repond au clavier, pas seulement au pointeur.
    assert "tete.addEventListener('keydown'" in page
    assert "aria-expanded" in page


def test_l_etat_d_une_carte_est_retenu_par_son_titre():
    """Une cle fondee sur la position rouvrirait des cartes fermees des
    qu'on en reordonne une."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("function pliCle(")[1].split("}")[0]
    assert "card-name b" in bloc and "textContent" in bloc
    assert "indexOf" not in bloc and "index" not in bloc


def test_la_deconnexion_est_rouge_aux_deux_endroits():
    """Deux boutons pour la meme action : ils doivent se ressembler. Un
    « Deconnexion » gris dans les parametres et rouge dans le menu se lit
    comme deux choses differentes."""
    page = _page_panneau("dashboard.html")
    declencheurs = page.split('onclick="doLogout()"')[:-1]
    assert len(declencheurs) == 2, "il y a deux boutons de deconnexion"
    for avant in declencheurs:
        balise = avant[avant.rindex("<"):]
        assert "acct-sortir" in balise or "btn-danger-quiet" in balise, (
            "un declencheur de deconnexion sans marque rouge : " + balise)
    assert ".acct-item.acct-sortir{color:var(--err)}" in page


def test_l_apparence_se_choisit_dans_une_liste_a_icones():
    """Trois mots colles dans un interrupteur segmente se lisent comme un
    reglage binaire mal compte. Une ligne par choix, avec son icone."""
    page = _page_panneau("dashboard.html")
    bloc = page.split('class="choix-liste" id="theme-toggle"')[1].split("</div>")[0]
    for choix in ("auto", "light", "dark"):
        assert f'data-t="{choix}"' in bloc
    # Une icone et une coche par ligne : trois choix, six svg.
    assert bloc.count("<svg") == 6, bloc.count("<svg")
    # applyTheme continue de piloter la liste : sans cela le choix actif ne
    # se verrait nulle part.
    assert "#theme-toggle button" in page


def test_une_case_a_cocher_n_est_pas_un_champ_de_saisie():
    """La regle input{width:100%} s'appliquait aussi aux cases : elles
    s'etiraient sur toute la largeur et leur intitule tombait a la ligne en
    dessous, sans lien visible entre les deux."""
    page = _page_panneau("dashboard.html")
    assert "input[type=checkbox],input[type=radio]{width:auto" in page
    assert "label:has(> input[type=checkbox])" in page


def test_les_preferences_d_affichage_ne_sont_plus_une_carte():
    """Le reglage 25/50 vit maintenant au-dessus du tableau qu'il concerne.
    La carte qui restait dans le compte ne parlait plus que d'elle-meme."""
    page = _page_panneau("dashboard.html")
    for trace in ("pref-taille", "prefTaille", "pref-msg", "Préférences d'affichage"):
        assert trace not in page, trace


def test_les_deux_pages_lisent_le_meme_theme():
    """Un seul fichier de variables, lie par les deux pages."""
    theme = _page_panneau("theme.css")
    # Les jetons structurants y sont, et dans les trois etats de theme.
    for cle in ("--accent:", "--bg:", "--ok:", "--err:", "--warn:", "--mono:", "--r:"):
        assert cle in theme, f"{cle} manque au theme"
    assert theme.count("--accent:") == 3, (
        "les trois etats de theme doivent etre tenus : clair, sombre du "
        "systeme, sombre choisi explicitement")
    assert '[data-theme="dark"]' in theme and "prefers-color-scheme" in theme

    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        assert '<link rel="stylesheet" href="/theme.css">' in page, (
            f"{nom} ne lit pas le theme partage")


def test_aucune_page_ne_redefinit_un_jeton_du_theme():
    """La recopie ne doit pas pouvoir revenir en silence.

    C'est ce test qui donne son sens au precedent : sans lui, on peut lier
    theme.css ET reposer un bloc :root dans la page, qui gagnerait par
    l'ordre de cascade. Le fichier partage serait alors mort sans que
    personne le remarque.
    """
    for nom in ("dashboard.html", "login.html"):
        css = _page_panneau(nom).split("</style>")[0]
        for cle in ("--accent:", "--bg:", "--surface:", "--txt:", "--ok:", "--err:"):
            assert cle not in css, (
                f"{nom} redefinit {cle} : le theme partage ne sert plus a rien, "
                "et les deux pages vont diverger")


# ---------- 23 ter. la police ne vient de nulle part ailleurs ----------
#
# Un panneau auto-heberge qui irait chercher sa police chez Google ferait
# fuiter l'adresse IP de chaque visiteur vers un tiers, et s'afficherait mal
# des que la machine est hors ligne -- c'est-a-dire exactement quand on a
# besoin de lui. La regle etait deja ecrite dans le depot ; elle n'etait
# tenue par rien.

def test_aucune_page_ne_va_chercher_une_police_ailleurs():
    """Aucun appel a un hebergeur de polices, dans aucun fichier servi."""
    for nom in ("theme.css", "dashboard.html", "login.html"):
        texte = _page_panneau(nom)
        for hote in ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit",
                     "fonts.bunny.net", "cdn.jsdelivr.net"):
            assert hote not in texte, (
                f"{nom} va chercher une police sur {hote} : le panneau ne doit "
                "dependre d'aucun tiers pour s'afficher")


def test_la_police_est_livree_avec_l_image():
    """Elle est declaree, elle est presente, et sa licence l'accompagne."""
    theme = _page_panneau("theme.css")
    assert "@font-face" in theme, "aucune police declaree"
    # On lit le BLOC, pas le fichier : un commentaire qui parle de swap
    # satisfaisait la verification alors que la declaration avait disparu.
    # Trouve en mutant le fichier -- la mutation survivait.
    debut = theme.index("@font-face")
    bloc = theme[debut:theme.index("}", debut)]
    assert 'src:url("/polices/manrope-latin.woff2")' in bloc, (
        "la police n'est pas servie par le panneau lui-meme")
    assert "font-display:swap" in bloc, (
        "sans swap, le texte reste invisible tant que la police n'est pas la")

    polices = os.path.join(DOSSIER_PANNEAU, "app", "polices")
    fichier = os.path.join(polices, "manrope-latin.woff2")
    assert os.path.isfile(fichier), "le fichier de police manque a l'image"
    with open(fichier, "rb") as f:
        assert f.read(4) == b"wOF2", "ce n'est pas un woff2"
    assert os.path.isfile(os.path.join(polices, "LICENCE-manrope.txt")), (
        "une police redistribuee sans sa licence, c'est une licence violee")


# ---------- 23 quater. les accents ne debordent pas sur les identifiants ----------
#
# Signale par Lucas, et la cause est une campagne precedente : en accentuant
# les textes visibles de l'interface, trois IDENTIFIANTS ont ete accentues au
# passage. Un texte accentue se lit mieux ; un identifiant accentue ne
# correspond plus a rien, et se tait.
#
#   data-activité="..."  dans le balisage, contre [data-activite] dans le
#                        selecteur : le noeud n'etait jamais trouve ;
#   c.dernière           contre le champ derniere renvoye par /api/activite :
#                        undefined, donc ilYA() repondait "jamais".
#
# Resultat : la ligne "derniere connexion" de chaque compte etait morte
# depuis. Aucune erreur, aucune trace -- juste une information absente, ce
# qui est exactement ce qu'aucune relecture ne remarque.

def _script_panneau():
    page = _page_panneau("dashboard.html")
    return page[page.index("</style>"):]


def test_aucun_attribut_data_n_est_accentue():
    """Un nom d'attribut accentue ne correspond a aucun selecteur."""
    page = _page_panneau("dashboard.html")
    fautifs = re.findall(r'data-([A-Za-z0-9_-]*[^\x00-\x7F][A-Za-z0-9_-]*)\s*=', page)
    assert not fautifs, f"attributs data- accentues : {sorted(set(fautifs))}"


def test_chaque_selecteur_data_trouve_son_attribut():
    """Le lien selecteur <-> balisage, tenu dans les deux sens.

    C'est ce test qui aurait attrape le defaut : les deux formes existaient,
    chacune correcte de son cote, mais elles ne se rencontraient jamais.
    """
    page = _page_panneau("dashboard.html")
    cherches = set(re.findall(r'\[data-([A-Za-z0-9_-]+)\]', page))
    poses = set(re.findall(r'data-([A-Za-z0-9_-]+)\s*=', page))
    # dataset.machin pose exactement data-machin : le script y accede par la
    # propriete, et ne pas le compter ferait echouer ce test sur un attribut
    # qui existe bel et bien dans le document.
    for nom in re.findall(r'\.dataset\.([A-Za-z0-9_]+)\s*=', page):
        poses.add(re.sub(r'([A-Z])', lambda m: '-' + m.group(1).lower(), nom))
    orphelins = cherches - poses
    assert not orphelins, (
        f"selecteurs sans attribut correspondant : {sorted(orphelins)} -- "
        "le noeud ne sera jamais trouve")


def test_la_derniere_connexion_lit_le_champ_de_l_api():
    """Le champ s'appelle derniere, sans accent, et c'est /api/activite qui
    le nomme. Le lire accentue donne undefined, et undefined affiche
    "jamais" -- un compte qui vient de se connecter s'annonce alors comme
    ne s'etant jamais connecte."""
    script = _script_panneau()
    assert "c.dernière" not in script, (
        "le champ de l'API s'ecrit derniere, sans accent")
    assert "ilYA(c.derniere)" in script


# ---------- 23 quinquies. le reglage 25/50 est sur le journal ----------
#
# Signale par Lucas : il etait pose sur la liste des COMPTES, qui tient a
# l'ecran, alors que le journal des connexions grandit a chaque visite et
# etait coupe net a 40 lignes, sans que rien ne le dise.

def test_le_journal_des_connexions_se_pagine():
    page = _page_panneau("dashboard.html")
    for cle in ('id="ac-taille"', 'id="ac-pagination"', 'id="ac-prec"',
                'id="ac-suiv"', 'id="ac-compte"'):
        assert cle in page, f"{cle} manque : le journal ne se pagine pas"
    script = _script_panneau()
    assert "function acParPage()" in script and "function acAllerA(" in script
    assert "slice(0,40)" not in script, (
        "la coupe seche a 40 lignes est revenue : ni reglable, ni annoncee")


def test_la_liste_des_comptes_ne_se_pagine_plus():
    """Deux paginations sur la meme page, dont une inutile, se confondent."""
    page = _page_panneau("dashboard.html")
    for cle in ('id="us-taille"', 'id="us-prec"', 'id="us-suiv"'):
        assert cle not in page, f"{cle} est revenu sur la liste des comptes"


# ---------- 23 sexies. la surveillance se declenche toute seule ----------
#
# Le diagnostic n'avait AUCUN planning : l'asset ne tournait que si quelqu'un
# allait cliquer "Materialize" dans Dagster. Une surveillance qu'il faut
# declencher ne previent de rien -- on ne la declenche que quand on soupconne
# deja quelque chose.
#
# La consequence depassait le diagnostic : alerte_mail_echec est un
# run_failure_sensor, il reagit a un run EN ECHEC. Aucun run ne demarrant
# jamais tout seul, aucun ne pouvait echouer, donc aucune alerte ne partait.
# Un systeme d'alerte complet, avec son SMTP et son repli, qui n'attendait
# qu'un clic pour servir.
#
# Tests TEXTUELS, et il faut le dire : l'image du panneau ne contient pas
# dagster, donc importer definitions.py ici echouerait. Ils constatent que le
# planning est declare et branche, pas qu'il se declenche -- ce qui le prouve,
# c'est la page des schedules de Dagster.

def _definitions_diagnostic():
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "definitions.py"), encoding="utf-8").read()


def test_le_diagnostic_a_un_planning_et_il_est_actif():
    src = _definitions_diagnostic()
    assert "ScheduleDefinition(" in src, (
        "aucun planning : l'asset ne tournera que si on le declenche a la main, "
        "et le capteur d'alerte n'aura jamais de run en echec a signaler")
    # Une fois par heure, hors de l'heure pile : toutes les quinze minutes,
    # chaque run chargeait Dagster, le panneau et Postgres 96 fois par jour.
    assert 'cron_schedule="7 * * * *"' in src
    assert "*/15" not in src
    assert "DefaultScheduleStatus.RUNNING" in src, (
        "un planning qu'il faut activer a la main dans l'interface : on ne "
        "s'apercoit de l'oubli qu'en ratant une alerte")


def test_le_planning_est_branche_dans_les_definitions():
    """Declare ne suffit pas : Dagster ne voit que ce qui est dans defs.

    Sans cette verification, un planning parfaitement ecrit mais absent de
    Definitions() passerait le test precedent tout en ne tournant jamais.
    """
    src = _definitions_diagnostic()
    bloc = src[src.index("defs = Definitions("):]
    assert "schedules=[" in bloc, "le planning n'est pas passe a Definitions()"
    assert "jobs=[" in bloc, "le job du planning n'est pas passe a Definitions()"
    assert "sensors=[" in bloc


def test_la_verification_approfondie_reste_a_la_demande():
    """Ce qui AGIT ne se planifie pas.

    run_tests() et la suite du panneau ecrivent, traversent le proxy et
    laissent des traces. Les jouer quatre fois par heure remplirait les
    journaux de traces que personne n'a demandees. Seules les sondes en
    lecture seule tournent toutes les heures.
    """
    src = _definitions_diagnostic()
    assert "run_tests" not in src, (
        "la verification approfondie est entree dans le code planifie")
    assert "lancer_suite_du_panneau" not in src


# ---------- 23 septies. l'accent ne deborde sur AUCUN identifiant ----------
#
# La verification precedente ne couvrait que les attributs data-. Six autres
# identifiants accentues dormaient dans la page, tous nes de la meme campagne
# d'accentuation des textes visibles :
#
#   la classe qui grise une application arretee, accentuee dans le gabarit
#     et sans accent dans le CSS : l'application n'etait plus grisee dans le
#     hub, et restait CLIQUABLE -- le lien ne menant qu'a une page d'erreur ;
#   les deux classes de couleur du bouton demarrer / arreter, accentuees de
#     meme : le bouton de la fiche perdait sa couleur ;
#   une variable declaree sans accent et relue avec, DEUX fois :
#     ReferenceError a l'ouverture d'une fiche, le rendu s'arretait la ;
#   deux champs d'objet accentues contre les noms renvoyes par l'API : les
#     cases d'autorisation ne se cochaient plus, et chaque cle d'acces
#     s'affichait "Ajoutee jamais".
#
# Un texte accentue se lit mieux. Un identifiant accentue ne correspond plus
# a rien -- et selon l'endroit, il se tait ou il leve.

def _script_panneau_html():
    page = _page_panneau("dashboard.html")
    return page, page[page.index("</style>"):]


def test_aucune_classe_css_n_est_accentuee():
    """Une classe accentuee ne correspond a aucune regle : le style saute."""
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        classes = set()
        for m in re.finditer(r'class="([^"]*)"', page):
            classes.update(m.group(1).split())
        fautives = sorted(c for c in classes
                          if re.search(r"[^\x00-\x7F]", c))
        assert not fautives, f"{nom} : classes accentuees {fautives}"


def test_aucune_balise_html_n_est_accentuee():
    """Un nom de BALISE accentue donne un element inconnu, silencieusement.

    Trouve dans l'assistant VPS : <detabils> ecrit avec un accent n'etait
    plus un <details>. Les quatre fichiers de configuration s'affichaient
    donc deroules d'un coup, au lieu d'etre replies -- la page etait noyee,
    et rien n'indiquait pourquoi.
    """
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        fautives = sorted(set(
            m.group(1) for m in re.finditer(
                r"</?([A-Za-z0-9]*[^\x00-\x7F\s/>][A-Za-z0-9]*)[\s/>]", page)))
        assert not fautives, f"{nom} : balises accentuees {fautives}"


def test_aucun_identifiant_javascript_n_est_accentue():
    """Ni une variable, ni un champ d'objet.

    Une variable accentuee lue sans etre declaree LEVE (ReferenceError) et
    arrete le rendu en cours ; un champ accentue vaut undefined et se tait.
    Les deux viennent de la meme erreur, et aucun des deux ne doit passer.
    """
    _, script = _script_panneau_html()
    lus = set()
    # Le premier caractere peut lui-meme porter l'accent (etape) : le motif
    # ne doit donc PAS exiger un caractere ASCII devant. C'est ce que la
    # premiere version supposait, et "etape" lui a echappe.
    for m in re.finditer(r"\$\{\s*([\w$\u00C0-\u024F]*[^\x00-\x7F][\w$\u00C0-\u024F]*)",
                         script):
        lus.add(m.group(1))
    for m in re.finditer(r"\.([\w$\u00C0-\u024F]*[^\x00-\x7F][\w$\u00C0-\u024F]*)\b",
                         script):
        lus.add(m.group(1))
    assert not lus, f"identifiants JavaScript accentues : {sorted(lus)}"


# ---------- 23 octies. demarrer ne se tait plus quand ca echoue ----------
#
# Signale par Lucas : "arreter et pause ne fonctionne pas dans la page
# applications". Mesure au navigateur : le clic partait bien, la requete
# aboutissait, l'API repondait 200 OK -- et l'application restait arretee,
# sans un mot.
#
# api_toggle repondait {"ok": True} sans rien verifier, et start() se taisait
# dans tous ses cas d'echec : dossier disparu, commande introuvable, port
# deja pris, isolement refuse. Il fallait aller lire le journal de
# l'application, en supposant qu'on sache qu'il existe.

def test_demarrer_verifie_que_l_application_vit_encore():
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    assert "def start(name, attendre=True):" in src, (
        "start() ne prend plus le temps de regarder l'application vivre")
    assert "DELAI_DEMARRAGE" in src
    assert "def derniere_ligne_utile(" in src, (
        "sans la derniere ligne du journal, le message n'apprend rien")
    # L'echec remonte a l'appelant, il ne se contente pas d'un print.
    bloc = src[src.index("def start(name, attendre=True):"):src.index("def stop(name):")]
    assert "return f\"Dossier introuvable" in bloc
    # Le message est LU par quelqu'un : il porte ses accents, contrairement
    # au code qui l'entoure.
    assert "s'est arr\u00eat\u00e9e aussit\u00f4t" in bloc


def test_l_api_toggle_rend_compte_de_l_echec():
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src[src.index("def api_toggle("):]
    bloc = bloc[:bloc.index("\n@flask_app", 10)]
    assert "stop(n) if action" in bloc and "else start(n)" in bloc, (
        "l'API ignore ce que stop() ou start() lui rendent")
    assert "409" in bloc, (
        "un echec de demarrage doit se voir dans le code de reponse")
    # Le SENS de l'action voyage avec la reponse : sans lui, la page ne peut
    # que deviner, et son repli est ecrit pour le demarrage.
    assert '"action": action' in bloc
    # Et plus aucune exception ne sort d'ici en page HTML : la page n'y
    # trouverait aucun JSON et retomberait sur ce meme repli.
    assert "except Exception" in bloc and "500" in bloc


def test_le_port_deja_pris_se_dit_avant_de_lancer(tmp_path, monkeypatch):
    """« Address already in use » au fond d'un journal, dans la langue d'un
    autre programme, n'est pas une reponse. Le cas est frequent -- deux
    applications sur le meme port, un survivant d'un arret brutal -- et il se
    reconnait AVANT de lancer quoi que ce soit."""
    import socket as _s
    projet = tmp_path / "projet"
    projet.mkdir()
    prise = _s.socket()
    prise.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    prise.bind(("127.0.0.1", 0))
    prise.listen(1)
    port = prise.getsockname()[1]
    try:
        app.save({"bloquee": {"path": str(projet), "command": "sleep 60",
                              "port": port, "visibility": "privee"}})
        erreur = app.start("bloquee")
        assert erreur and str(port) in erreur, erreur
        assert "occup" in erreur, erreur
        # Rien n'a ete lance : un port pris ne doit pas laisser un processus
        # derriere lui.
        assert not app.is_running("bloquee")
    finally:
        prise.close()
        app.stop("bloquee")

    # Et le port libre ne declenche rien : sinon on refuserait tout.
    assert app.port_occupe_par(port) is None


def test_un_port_libere_a_l_instant_ne_bloque_pas_le_redemarrage():
    """Un port fraichement ferme reste en TIME_WAIT. Sans SO_REUSEADDR sur la
    sonde, on refuserait de redemarrer ce qu'on vient d'arreter -- le geste
    le plus courant du panneau."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src.split("def port_occupe_par(")[1].split("\ndef ")[0]
    assert "SO_REUSEADDR" in bloc
    # Sur 127.0.0.1 : c'est l'adresse que le proxy contacte, et une sonde sur
    # 0.0.0.0 refuserait un demarrage qu'un service sur une autre adresse
    # n'aurait pas empeche.
    assert '"127.0.0.1"' in bloc


def test_un_lancement_impossible_ne_devient_pas_une_erreur_500():
    """Popen leve pour tout ce qui empeche le lancement avant la commande.
    L'exception remontait a Flask : 500, page HTML, aucun message pour
    l'interface -- qui affichait alors son texte par defaut."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src[src.index("def start(name, attendre=True):"):src.index("def port_occupe_par(")]
    assert "except (OSError, ValueError)" in bloc
    assert 'return f"Lancement impossible' in bloc


def test_le_redemarrage_rend_compte_de_son_echec():
    """restart_app() jetait l'erreur de start() : la route repondait « ok »,
    et le bouton laissait une application arretee sans un mot."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src.split("def restart_app(n):")[1].split("\n\n\n")[0]
    assert "return start(n)" in bloc, "l'erreur de start() repart a la poubelle"
    route = src.split("def api_restart(n):")[1].split("\n\n\n")[0]
    assert "erreur = restart_app(n)" in route and "409" in route
    # Le deploiement aussi : un build reussi suivi d'une mise en ligne ratee
    # repondait « ok ».
    deploiement = src.split("def api_deploy(n):")[1].split("\n\n\n")[0]
    assert "erreur = restart_app(n) if is_running(n) else start(n)" in deploiement
    assert "409" in deploiement


def test_aucune_action_ne_se_rabat_sur_une_phrase_vide():
    """« L'application n'a pas demarre » n'apprend rien. Quand le serveur ne
    dit rien du tout, le code HTTP distingue au moins un refus d'une panne."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    aide = page.split("async function echec(r, defaut){")[1].split("\n}")[0]
    assert "r.status" in aide and "401" in aide and "403" in aide
    for action in ("async function tg(n){", "async function restartApp(n){",
                   "async function deployApp(n){", "async function buildApp(n){"):
        bloc = page.split(action)[1].split("\n}")[0]
        assert "await echec(r," in bloc, f"{action} ne rend pas compte de l'echec"


def test_le_bouton_lit_la_reponse():
    """Deux silences valaient mieux qu'un : meme si l'API avait repondu une
    erreur, tg() jetait la reponse sans la regarder."""
    _, script = _script_panneau_html()
    bloc = script[script.index("async function tg(n){"):]
    bloc = bloc[:bloc.index("async function", 10)]
    assert "r.ok" in bloc and "notifier(" in bloc, (
        "le bouton ignore la reponse : l'utilisateur clique dans le vide")


# ---------- 23 nonies. revenir au hub, et voir plus loin ----------

def test_le_ruban_de_retour_refuse_les_cas_risques():
    """Injecter dans la page d'une application ne se fait pas a l'aveugle.

    Quatre refus, et chacun evite de casser quelque chose : un code autre que
    200, autre chose que du HTML, un corps COMPRESSE (les octets ne
    contiennent alors pas "</body>"), et l'absence de </body>.
    """
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src[src.index("def injecter_ruban("):src.index("def _proxy(")]
    for garde in ("status != 200", "text/html", "content-encoding", "rfind"):
        assert garde in bloc, f"le refus sur {garde} a disparu"
    # Content-Length doit suivre, sinon le navigateur tronque la page juste
    # avant le ruban.
    assert "Content-Length" in bloc

    # Et il ne se pose que pour quelqu'un de connecte : une application
    # publique vue par un visiteur anonyme n'a pas a lui annoncer qu'un
    # panneau existe derriere.
    pose = src[src.index("sortants = [(k, v) for k, v in headers.items()"):]
    pose = pose[:pose.index("return Response")]
    assert "if is_authed():" in pose


def test_le_ruban_ramene_au_hub_et_pas_a_la_racine_des_applications(monkeypatch):
    """Le lien du ruban etait « / », et « / » a change de sens.

    Depuis que les applications ont leur propre port, la racine de CE port
    n'est pas le hub : la garde des origines y repond « Ce n'est pas le
    panneau ». Cliquer sur « CodeLab » depuis une application tombait donc
    sur une page d'erreur. Le lien doit sortir de l'origine des applications.
    """
    monkeypatch.setitem(app._origines_separees, "actif", True)
    monkeypatch.setattr(app, "APPS_PORT", 9002)
    monkeypatch.setattr(app, "PANEL_PORT", 9001)
    monkeypatch.setattr(app, "PANEL_URL", "")

    with app.flask_app.test_request_context(
            "/appli/", base_url="http://machine:9002"):
        assert app.lien_panneau() == "http://machine:9001/"
        assert b'href="http://machine:9001/"' in app.ruban_retour()
        # La page d'erreur du proxy (application arretee, acces refuse) sort
        # du meme port et souffrait du meme lien mort.
        assert 'href="http://machine:9001/"' in app._page("T", "M")

    # Sur le port du panneau, le lien relatif reste le bon : il suit le nom
    # d'hote par lequel on est arrive, quel qu'il soit.
    with app.flask_app.test_request_context(
            "/", base_url="http://machine:9001"):
        assert app.lien_panneau() == "/"


def test_le_lien_du_hub_suit_l_adresse_declaree_derriere_un_reverse_proxy():
    """Un second PORT n'est pas joignable de partout : quand le panneau est
    publie sous une adresse a lui, c'est elle qu'il faut ecrire."""
    with app.flask_app.test_request_context("/appli/", base_url="http://machine:9002"):
        ancien_actif = app._origines_separees["actif"]
        app._origines_separees["actif"] = True
        ancien_url, ancien_apps = app.PANEL_URL, app.APPS_PORT
        app.PANEL_URL, app.APPS_PORT = "https://codelab.exemple.fr", 9002
        try:
            assert app.lien_panneau() == "https://codelab.exemple.fr/"
            # Separation inactive : rien ne change, le lien reste relatif.
            app._origines_separees["actif"] = False
            assert app.lien_panneau() == "/"
        finally:
            app._origines_separees["actif"] = ancien_actif
            app.PANEL_URL, app.APPS_PORT = ancien_url, ancien_apps


def test_un_nom_d_hote_hostile_ne_se_recopie_pas_dans_la_page():
    """request.host vient de l'en-tete Host, donc du client. Il finit dans un
    attribut href : un guillemet bien place en sortirait, et deviendrait du
    script dans la page d'une application."""
    for hostile in ('machine" onmouseover="alert(1)', "ma chine", "<script>",
                    "machine/../x", "machine\r\nX: 1", ""):
        assert app.hote_propre(hostile) == "", f"{hostile!r} est passe"
    # Un nom d'hote ordinaire passe, lui : une garde qui refuse tout ne
    # laisserait jamais le lien sortir de l'origine des applications.
    assert app.hote_propre("codelab.local:9002") == "codelab.local"
    assert app.hote_propre("192.168.1.42") == "192.168.1.42"

    # Bout en bout : le lien retombe sur le relatif plutot que d'ecrire une
    # adresse douteuse. (Werkzeug ecarte deja ces Host de son cote -- la
    # garde ci-dessus ne depend pas de lui pour autant.)
    ancien = app._origines_separees["actif"]
    ancien_url, ancien_apps = app.PANEL_URL, app.APPS_PORT
    app._origines_separees["actif"] = True
    app.PANEL_URL, app.APPS_PORT = "", 9002
    try:
        with app.flask_app.test_request_context(
                "/appli/", environ_overrides={"HTTP_HOST": 'x" onmouseover="1',
                                              "SERVER_PORT": "9002"}):
            assert app.lien_panneau() == "/"
            assert b'href="/"' in app.ruban_retour()
        with app.flask_app.test_request_context(
                "/appli/", environ_overrides={"HTTP_HOST": "codelab.local:9002",
                                              "SERVER_PORT": "9002"}):
            assert app.lien_panneau() == f"http://codelab.local:{app.PANEL_PORT}/"
    finally:
        app._origines_separees["actif"] = ancien
        app.PANEL_URL, app.APPS_PORT = ancien_url, ancien_apps


def test_sans_adresse_utilisable_on_ne_redirige_pas_en_rond():
    """Une cible relative renverrait la requete vers elle-meme, et le
    navigateur tournerait en rond jusqu'a abandonner."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src.split("def separer_les_origines():")[1].split("\n\n\n")[0]
    assert "if not origine:" in bloc and "return None" in bloc


def test_le_ruban_est_construit_a_chaque_page():
    """Une constante d'octets ne peut pas porter un lien qui depend de
    l'origine : le ruban doit etre fabrique au moment de l'injection."""
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    assert "RUBAN_RETOUR" not in src, (
        "le ruban est redevenu une constante : son lien ne peut plus suivre "
        "l'origine qui sert la page")
    bloc = src[src.index("def injecter_ruban("):src.index("def _proxy(")]
    assert "ruban_retour()" in bloc


def _textes_visibles(html):
    """Les textes affiches d'une page : ni CSS, ni script, ni commentaire."""
    import html as _html
    corps = html
    for balise in ("style", "script"):
        corps = re.sub(r"<%s[^>]*>.*?</%s>" % (balise, balise), " ", corps, flags=re.S)
    corps = re.sub(r"<!--.*?-->", " ", corps, flags=re.S)
    return [_html.unescape(t).strip()
            for t in re.split(r"<[^>]+>", corps) if t.strip()]


# Les fautes qu'on retrouve toujours : un participe passe ou un verbe prive
# de son accent. La liste est volontairement courte et SURE -- un mot qui
# existe aussi sans accent (« ou », « a », « la ») n'y figure pas, sous peine
# de faire echouer le test sur des phrases correctes -- « autorises » en est
# un bon exemple : « les projets que tu lui autorises » est juste.
MOTS_SANS_ACCENT = [
    "verifie", "verifier", "verification", "verifications", "execute", "executee",
    "executees", "reponse", "reponses", "detail", "details", "etat", "etats",
    "deja", "apres", "etre", "meme", "memes", "tres", "ete", "creee", "creees",
    "creer", "donnee", "donnees", "annee", "arretee", "arretees", "declaree",
    "declarees", "repond", "deconnecte", "desactive", "recu", "securite",
    "systeme", "numero", "acces", "reserve", "derniere", "dernieres",
    "premiere", "supprimee", "enregistree", "demarree", "redemarre", "ajoutee",
    "retiree", "activee", "refusee", "acceptee", "envoyee", "memoire",
    "categorie", "categories", "duree", "presentation", "frequentation",
    "notee", "inseree", "depasser", "ecouter", "repeter", "identite",
    "expediteur", "interessante", "derriere", "dependent", "fermee", "revoque",
    "eteint", "ecrase", "posee", "restee", "oubliee", "prevenir", "previent",
    "reinitialiser", "disparait", "controle", "creent", "sante",
    "gerez", "visibilite", "releve", "decoche", "redemarrer",
    # Deuxieme passe, en relisant TOUS les textes visibles du panneau, de la
    # page de connexion et du diagnostic. Chacun de ces mots a ete trouve en
    # place : « Connexion chiffree », « Heberge chez toi », « Applications
    # centralisees », « Adresse jamais confirmee », « Fixe par
    # APP_MANAGER_HTTPS ».
    #
    # La liste ne retient que des mots dont AUCUNE forme francaise ne
    # s'ecrit sans accent. « marque », « cote », « regle », « nommes »,
    # « constate » en sont volontairement absents : ce sont aussi des mots
    # ou des conjugaisons parfaitement corrects sans accent, et les y mettre
    # ferait echouer le test sur des phrases justes.
    "chiffree", "chiffrees", "confirmee", "confirmees", "frequente",
    "frequentes", "centralisee", "centralisees", "copiee", "copiees",
    "accede", "heberge", "protegerait", "paraitraient", "releves",
    "severite", "deuxieme", "defaut", "reseau", "echec", "echecs",
    "probleme", "problemes", "parametre", "parametres", "requete",
    "requetes", "resultat", "resultats", "necessaire", "periode",
    "integrite", "telecharger", "demarrer", "demarrage", "reussi",
    "reussie", "echoue",
    # « Desactivee », « Desactiver » : trouves sur les pastilles d'etat, que
    # personne ne relit parce qu'elles font deux mots. « active », lui, n'y
    # est pas -- une sonde active s'ecrit sans accent.
    "desactive", "desactivee", "desactivees", "desactiver", "desactives",
]
MOTIF_SANS_ACCENT = re.compile(
    r"\b(" + "|".join(sorted(MOTS_SANS_ACCENT, key=len, reverse=True)) + r")\b", re.I)


def test_le_mot_a_l_administrateur_a_sa_propre_rubrique():
    """Il vivait au bas du hub, replie sous les applications : la ou tout le
    monde passe, mais aussi la ou personne ne vient pour ecrire. Les
    reglages sont l'endroit ou l'on va pour AGIR sur son compte, et un onglet
    se trouve par son nom -- ce qu'une carte au bas d'une liste de tuiles ne
    permet pas."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    # L'onglet existe, et pour TOUT LE MONDE : data-admin le reserverait a
    # l'administrateur, qui est justement celui a qui l'on ecrit.
    onglet = page.split('<button data-p="message"')[1].split(">")[0]
    assert "data-admin" not in onglet, onglet
    assert 'id="param-message"' in page

    # Le formulaire est DANS cet onglet, et plus dans le hub.
    hub = page.split('<div id="sec-hub"')[1].split('<!-- ==================== VUE')[0]
    assert "msg-carte" not in hub, "le formulaire traine encore dans le hub"
    message = page.split('<div class="onglet-p" id="param-message">')[1] \
                  .split('<div class="onglet-p"')[0]
    for champ in ('id="msg-cible"', 'id="msg-app"', 'id="msg-texte"', 'id="msg-envoyer"'):
        assert champ in message, champ


def test_une_carte_seule_dans_son_onglet_ne_se_replie_pas():
    """Le pli sert a passer devant ce qu'on ne vient pas regler. Quand il n'y
    a rien d'autre dans l'onglet, il ne fait que cacher la seule chose de la
    page derriere un clic -- vu sur la rubrique Message, ou le formulaire
    disparaissait entierement."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function plierLesCartes(){")[1].split("\n}")[0]
    assert "closest('.onglet-p')" in bloc
    assert "querySelectorAll('.settings-card').length < 2" in bloc


def test_le_hub_ne_garde_que_sa_grille():
    """UNE SEULE VUE. Le hub offrait deux tailles de tuile, retenues par
    navigateur : une decision de plus a prendre pour une page ou l'on ne
    vient que pour ouvrir une application. L'etat d'une application se lit
    dans « Applications » ; ici, la grille d'icones dit « c'est la », et
    c'est tout ce qu'on lui demande."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert "hubTaille(" not in page, "la bascule de taille est revenue"
    assert "codelab.hub.taille" not in page, (
        "le reglage de taille est revenu dans le stockage du navigateur")
    assert "id=\"hub-taille\"" not in page, "le selecteur de taille est revenu"
    # La grille est posee sans condition : plus rien ne peut la faire varier.
    assert "const classeListe = 'hub-liste compacte';" in page
    # Ce qu'on accepte de perdre en grille : l'etat et la visibilite sous le
    # nom. C'est le prix pour voir trente projets d'un coup.
    assert ".hub-liste.compacte .hub-projet .bas{display:none}" in page \
        or ".hub-liste.compacte .hub-projet .desc,\n.hub-liste.compacte .hub-projet .bas{display:none}" in page
    # Une application arretee reste grisee et non cliquable : sans cela on
    # cliquerait dans le vide.
    assert ".hub-projet.arretee{opacity:.6;pointer-events:none}" in page


def test_la_tuile_en_mode_icone_n_a_ni_fond_ni_cadre():
    """En mode icone, la carte devenait un carre gris autour d'une petite
    image : trente carres gris alignes font une grille de cases, pas un
    bureau. Ce qu'on vient voir, c'est l'icone."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    regle = page.split(".hub-liste.compacte .hub-projet{")[1].split("}")[0]
    assert "background:none" in regle, "le fond de la tuile reste en mode icone"
    assert "border-color:transparent" in regle, "le cadre reste en mode icone"
    # Le survol aussi : sans cela, le fond revient des qu'on approche la
    # souris, et c'est justement la qu'on le voit.
    survol = page.split(".hub-liste.compacte .hub-projet:hover{")[1].split("}")[0]
    assert "background:none" in survol and "border-color:transparent" in survol
    # Il reste quelque chose a survoler : le nom prend l'accent.
    assert ".hub-liste.compacte .hub-projet:hover .nom{color:var(--accent)}" in page


def test_les_trois_cartes_de_la_vue_d_ensemble_font_la_meme_hauteur():
    """« Applications » etait un cadre transparent contenant trois tuiles
    cadrees : elles commencaient plus bas et finissaient plus haut que
    « Sante » et « Ressources », et les trois colonnes ne s'alignaient sur
    rien."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert ".card:has(> .ov-nums){background:transparent" not in page, (
        "la carte des chiffres redevient un cadre transparent, et ses tuiles "
        "ne s'alignent plus sur les deux voisines")
    # La grille etire deja les colonnes ; ce qui manquait, c'est que le
    # contenu remplisse la carte.
    assert ".card{display:flex;flex-direction:column}" in page
    assert ".card>.ov-nums{flex:1" in page
    # Et que le contenu des deux autres se pose au milieu de ce qui reste.
    assert ".card>.healthbar{margin-top:auto}" in page
    assert ".card>.health-legend{margin-bottom:auto}" in page
    assert ".card>.res-row:first-of-type{margin-top:auto}" in page


def test_la_carte_de_chaleur_ne_defile_pas():
    """Une carte de chaleur se lit d'un coup d'oeil -- c'est sa seule raison
    d'etre. La moitie cachee derriere un glissement horizontal ne se lit
    jamais."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    zone = page.split(".carte-zone{")[1].split("}")[0]
    assert "overflow" not in zone, "la barre de defilement est revenue"
    # La taille d'une case se CALCULE depuis la place disponible : ni 11 px
    # en dur (qui debordent d'une carte etroite), ni « 1fr » (dont la hauteur
    # dependrait de la largeur, qui dependrait de la hauteur -- le navigateur
    # tranchait ce cercle par des cases de 54 px).
    bloc = page.split("function peindreCarte(d){")[1].split("\nfunction ")[0]
    assert "$('carte-zone').clientWidth" in bloc, (
        "la carte doit mesurer la place dont elle dispose")
    assert "grid-template-rows:repeat(7,${cote}px)" in bloc, (
        "les lignes suivent la meme taille, sinon les cases ne sont pas carrees")
    # L'ecart entre les cases se resserre avant que la case ne rapetisse :
    # 52 ecarts de 3 px font 156 px, qui debordent a eux seuls d'une carte
    # etroite.
    assert "for(const e of [3,2,1,0])" in bloc, (
        "sans resserrer l'ecart, la grille deborde meme avec des cases minuscules")
    assert "gap:${ecart}px" in bloc
    # Et elle se repeint quand la fenetre change : une carte calculee pour
    # 1400 px deborde a 900.
    assert "addEventListener('resize'" in page and "peindreCarte(derniereCarte)" in page


def test_les_tuiles_du_hub_sont_deux_fois_plus_grandes():
    """A 44 px, on ne distinguait plus une application d'une autre. Une
    grille d'icones sert justement a reconnaitre d'un coup d'oeil."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    icone = page.split(".hub-liste.compacte .hub-projet img{")[1].split("}")[0]
    assert "width:88px" in icone and "height:88px" in icone
    # La colonne laisse respirer l'icone et son nom, sans plus : 148 px pour
    # 88 px d'icone, et 10 px entre deux tuiles. Resserrer davantage ferait
    # se toucher les noms ; relacher rendrait la grille clairsemee.
    grille = page.split(".hub-liste.compacte{")[1].split("}")[0]
    assert "minmax(148px" in grille and "gap:10px" in grille


def test_les_controles_du_navigateur_suivent_le_theme():
    """Une case cochee en bleu Windows et une fleche grise systeme suffisent
    a faire passer la page pour un formulaire pose sur un theme soigne."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert "accent-color:var(--accent)" in page, (
        "sans accent-color, les cases et les boutons radio gardent le bleu du "
        "systeme")
    assert "caret-color:var(--accent)" in page
    bloc = page.split("select{appearance:none")[1].split("}")[0]
    assert "background-image:url(\"data:image/svg+xml" in bloc, (
        "la fleche du navigateur doit etre remplacee par la notre")
    # Et en sombre aussi : une fleche claire sur fond sombre se voit.
    assert ':root[data-theme="dark"] select{background-image' in page
    assert "select option{background:var(--surface)" in page


def test_les_zones_des_parametres_ne_sont_plus_des_boites():
    """Six boites blanches empilees dans un onglet donnaient six rectangles
    qui se disputaient l'attention -- alors qu'aucun n'est un objet."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split(".settings-card{")[1].split("}")[0]
    assert "background:transparent" in bloc and "border:0" in bloc
    assert "box-shadow:none" in bloc
    # Il reste de quoi separer deux reglages : sans filet, ils se collent.
    assert "border-bottom:1px solid var(--line)" in bloc


def test_le_reglage_d_affichage_se_deroule_quand_il_y_a_trop_d_applications():
    """Quinze applications faisaient de ce reglage la moitie de la page, pour
    une liste qu'on ouvre trois fois par an."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert "MASQUAGE_SEUIL_DEROULANT" in page
    bloc = page.split("function peindreMasquees(){")[1].split("\n}")[0]
    assert "<details" in bloc, "la liste longue doit se replier"
    assert "if(toutes.length <= MASQUAGE_SEUIL_DEROULANT)" in bloc, (
        "une liste courte ne gagne rien a etre repliee")
    # Redessinee a chaque case cochee, elle se refermerait sous le doigt.
    assert "ouverte" in bloc and "details" in bloc


def test_les_destinataires_d_alerte_vivent_dans_la_fiche_de_l_application():
    """Ils etaient dans les parametres globaux, en une liste de champs ou il
    fallait retrouver la bonne ligne -- et rien, depuis la fiche d'une
    application, ne disait qui serait prevenu si elle tombait."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert 'id="al-par-app"' not in page, (
        "la liste globale des destinataires est revenue")
    assert "peindreAlertesParApp" not in page
    config = page.split('<div class="onglet" id="onglet-config">')[1]
    config = config.split('id="sec-parametres"')[0]
    assert 'id="e-alertes"' in config, (
        "le champ doit etre dans l'onglet Configuration de l'application")
    # Enregistre par le meme bouton que le reste de la configuration.
    envoi = page.split("async function submitEdit(){")[1].split("\n}")[0]
    assert "/api/alertes/application/" in envoi
    # Et relu a l'ouverture de la fiche, sinon le champ parait toujours vide.
    lecture = page.split("function chargerConfigFiche(){")[1].split("\n}")[0]
    assert "$('e-alertes').value" in lecture
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src.split("def api_apps():")[1].split("\n\n\n")[0]
    assert '"alertes": _adresses(par_app.get(name))' in bloc, (
        "la liste doit arriver avec l'application")


def test_le_masquage_ne_se_regle_que_dans_les_parametres():
    """La croix posee sur chaque tuile transformait le hub en liste a trier,
    et se cliquait par accident. Le reglage vit la ou vivent les reglages."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    assert "hub-masquer" not in page, (
        "le bouton de masquage est revenu sur la tuile du hub")
    # La tuile n'est plus qu'un lien.
    tuile = page.split("const tuile = a =>")[1].split("`;")[0]
    assert "<button" not in tuile, "la tuile du hub porte de nouveau un bouton"
    # Et le reglage est bien une case a cocher, dans les deux sens.
    assert "function basculerMasquage(" in page
    bascule = page.split("async function basculerMasquage(nom, masquer){")[1]
    bascule = bascule.split("\n}")[0]
    assert "masquer ? 'POST' : 'DELETE'" in bascule, (
        "cocher doit masquer, decocher doit reafficher")


def test_les_parametres_listent_toutes_les_applications_a_cocher():
    """N'afficher que les masquees obligeait a en retirer une depuis le hub
    avant de pouvoir la retrouver ici : la liste doit etre complete."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function peindreMasquees(){")[1].split("\n}")[0]
    assert "(hubApps||[]).concat(hubMasquees||[])" in bloc, (
        "la liste des reglages doit reunir les affichees ET les masquees")
    assert 'type="checkbox"' in bloc
    # La case ne porte plus son appel en attribut : elle nomme son action, et
    # l'ecouteur unique la route. Les deux moities sont verifiees, sinon le
    # test passerait sur une case qui ne fait plus rien.
    assert 'data-changer="app-masquer"' in bloc, (
        "la case ne declare plus son action")
    ecoute = page.split("document.addEventListener('change'")[1].split("\n}")[0]
    assert "'app-masquer'" in ecoute and "basculerMasquage(" in ecoute, (
        "personne n'ecoute la case : la cocher ne masquerait plus rien")
    # Une case cochee doit se lire sans lire l'etiquette : la ligne se retire.
    assert "cachee" in bloc


def test_les_projets_autorises_forment_une_liste_lisible():
    """Une rangee de cases nues ne disait ni de quelle application il
    s'agissait, ni lesquelles etaient publiques -- donc lesquelles cocher ne
    changeait rien."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("$('us-f-projets').innerHTML =")[1].split("</label>`;")[0]
    assert "proj-ligne" in bloc
    assert "icone(a.name)" in bloc, "une ligne sans icone ne se reconnait pas"
    assert "badgeVis(a)" in bloc, "le badge public/prive doit rester visible"
    assert 'type="checkbox"' in bloc
    # Le style existe vraiment : une classe sans regle ne dessine rien.
    assert ".proj-ligne img{" in page
    # Et c'est la LIGNE qu'on choisit, pas la case : elle porte la classe
    # commune a toutes les listes a choix du panneau.
    assert "choix-ligne" in bloc
    assert ".choix-ligne:has(>input:checked)" in page, (
        "rien ne distingue plus une ligne retenue d'une ligne ordinaire")


def test_la_fiche_d_un_compte_attend_la_liste_des_applications():
    """Ouverte avant que /api/apps ne reponde, la rubrique « Projets
    autorises » restait vide sans rien dire."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("async function usOuvrir(nom){")[1].split("\n}")[0]
    assert "if(!apps.length)" in bloc and "await refresh()" in bloc
    assert "usPeindreFiche()" in bloc.split("await refresh()")[1], (
        "il faut repeindre une fois la liste arrivee")


def test_la_rubrique_message_ferme_la_marche():
    """On ne vient pas dans les parametres pour ecrire, on y vient pour
    regler : le mot a l'administrateur passe en dernier."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    onglets = page.split('<div class="fiche-onglets" id="param-onglets">')[1]
    onglets = onglets.split("</div>")[0]
    noms = re.findall(r'data-p="([a-z]+)"', onglets)
    assert noms[-1] == "message", f"ordre des onglets : {noms}"
    assert noms[0] == "compte", "le compte reste la premiere rubrique"


def test_le_bandeau_n_a_plus_de_recherche():
    """Le menu et la marque a gauche, le compte a droite : la barre de
    recherche de l'entete est retiree, et rien ne filtre plus en silence le
    hub ou la liste des applications."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    barre = page.split('<div class="topbar">')[1].split('<div class="acct-wrap">')[0]
    groupe = barre.split('<div class="topbar-gauche">')[1]
    assert "side-toggle-btn" in groupe[:400] and "topbar-brand" in groupe[:2000]
    assert barre.count('class="spacer"') == 1
    for trace in ('class="rechercher"', 'id="q"', "onRecherche", "correspond(", ".rechercher{"):
        assert trace not in page, trace


def _phrases_du_script(page):
    """Les chaines du JavaScript qui sont des PHRASES, donc des textes lus.

    Un test qui ne regarde que le HTML rate tout ce que la page ecrit
    elle-meme -- et c'est la que la moitie de l'interface est produite. Le
    filtre est simple : au moins trois mots, et pas d'adresse ni de selecteur
    (ils contiennent presque toujours une barre, un diese ou un signe egal).

    Le signe egal ecarte un artefact du decoupage : entre deux chaines
    voisines d'une meme ligne de code, le texte qui les separe est du CODE,
    et il etait pris pour une phrase -- « + r).textContent = d.detail || »
    en est un.
    """
    phrases = []
    for js in re.findall(r"<script[^>]*>(.*?)</script>", page, re.S):
        # Les COMMENTAIRES d'abord : ils sont ecrits sans accents, c'est la
        # regle, et leurs apostrophes ressemblent a s'y meprendre a des
        # chaines de caracteres.
        js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
        js = re.sub(r"(?m)^\s*//.*$", " ", js)
        for m in re.finditer(r"'((?:[^'\\\n]|\\.){12,200})'"
                             r'|"((?:[^"\\\n]|\\.){12,200})"', js):
            t = next(g for g in m.groups() if g is not None)
            if len(t.split()) < 3:
                continue
            if re.search(r"[/<>{}#=]|\bfunction\b|\bvar\b", t):
                continue
            # MEME ARTEFACT QUE LE SIGNE EGAL, autre forme. Le texte pris
            # entre deux chaines voisines est du code, et une concatenation
            # commence ou finit par « + » : « + (c.requetes ? » en est un.
            # Aucune phrase ne commence ni ne finit par un plus.
            if t.strip().startswith("+") or t.strip().endswith("+"):
                continue
            phrases.append(t.replace("\\'", "'"))
    return phrases


def test_aucun_texte_du_hub_n_a_perdu_ses_accents():
    """Les textes AFFICHES portent leurs accents -- le code qui les entoure,
    lui, n'en porte aucun. C'est la regle de ce depot, et elle se relachait a
    chaque ajout : « Sante », « Visibilite », « Presentation », « Repeter »,
    « Identite » sont tous arrives comme cela.

    Le HTML ET les phrases du script : la moitie de l'interface est ecrite
    par la page elle-meme, et un test qui l'ignore laisse passer « limitee a
    ce que tu leur autorisés » -- vu sur la page d'accueil.
    """
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    fautes = []
    for texte in _textes_visibles(page) + _phrases_du_script(page):
        for mot in MOTIF_SANS_ACCENT.findall(texte):
            fautes.append(f"{mot} — dans « {texte[:70]} »")
    assert not fautes, "textes visibles sans accents :\n" + "\n".join(fautes[:12])


def test_aucun_imperatif_n_est_ecrit_au_participe_passe():
    """« Déclaré-la », « Réglé-le », « modifié-le là-bas » : un participe
    passe la ou le francais demande un imperatif. La faute vient d'une passe
    d'accentuation trop zelee -- on accentue « declare » sans regarder si la
    phrase donne un ordre.

    La regle mecanique qui les attrape toutes : un verbe en -é suivi d'un
    trait d'union et d'un pronom est TOUJOURS faux. « Déclare-la »,
    « règle-le », « modifie-le » : l'imperatif ne prend pas d'accent final.
    """
    motif = re.compile(r"[A-Za-zÀ-ÿ]+é-(?:le|la|les|y|en|moi|toi)\b")
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "app.py"),
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")):
        src = open(chemin, encoding="utf-8").read()
        for faute in motif.findall(src):
            fautes.append(f"{os.path.basename(chemin)} : {faute}")
    assert not fautes, "imperatifs au participe passe :\n" + "\n".join(fautes)


def test_les_phrases_qui_donnent_un_ordre_le_donnent_en_francais():
    """Les memes fautes sans trait d'union, que la regle mecanique
    ci-dessus ne peut pas attraper : « Déclaré un dossier », « Créé un
    compte », « réglé les destinataires ». Une liste, donc -- courte, et
    nourrie de ce qui a ete trouve en place."""
    fautives = ["Déclaré un", "Déclaré l'", "Déclaré d'abord", "Créé un",
                "créé un compte", "réglé les destinataires", "Réglé-le",
                "Activé-la", "Ajouté un", "Supprimé le"]
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "app.py")):
        src = open(chemin, encoding="utf-8").read()
        for faute in fautives:
            if faute in src:
                fautes.append(f"{os.path.basename(chemin)} : « {faute} »")
    assert not fautes, "participe passe au lieu d'un imperatif :\n" + "\n".join(fautes)


# Les verbes a l'infinitif derriere « a » : « rien a taper », « A activer
# ci-dessous », « A poser une fois le TLS en place ». La preposition prend
# l'accent, le verbe avoir non -- et « avoir » ne se suit jamais directement
# d'un infinitif. Les infinitifs en -re sont listes : trop de mots courants
# finissent par ces deux lettres (« encore », « autre », « contre ») pour
# qu'une terminaison suffise.
INFINITIFS_EN_RE = ("faire", "mettre", "prendre", "ecrire", "écrire", "lire",
                    "dire", "suivre", "remettre", "reprendre", "comprendre",
                    "permettre", "croire", "vivre", "rendre", "attendre",
                    "repondre", "répondre", "perdre", "revivre")
MOTIF_A_INFINITIF = re.compile(
    r"(?<![\wÀ-ÿ'])[Aa]\s+(?:[a-zà-ÿ]+(?:er|ir)\b|(?:" +
    "|".join(INFINITIFS_EN_RE) + r")\b)")

# « ne protégé plus rien » : un participe passe la ou il faut un present.
# Sans auxiliaire, « ne ... plus » demande un verbe conjugue.
MOTIF_PARTICIPE_SANS_AUXILIAIRE = re.compile(
    r"\bne\s+[a-zà-ÿ]+é\s+(?:plus|pas|rien|jamais|guere|guère)\b")


def test_les_etiquettes_d_un_seul_mot_portent_leurs_accents():
    """« Desactivee », « Actives » : les pastilles d'etat font UN mot, et le
    controle des phrases ne les voyait pas -- il ne regarde que ce qui
    compte trois mots ou plus. Ce sont pourtant les textes les plus lus de
    la page : on les relit a chaque coup d'oeil.

    On ne cherche que dans ce qui est manifestement une etiquette : entre
    guillemets ou entre balises. Sans cela, totpDesactiver() -- un nom de
    fonction, qui ne s'affiche nulle part -- serait signale.
    """
    motif = re.compile(r"""(?<=['">])(Desactivee?s?|Desactiver|Activee|Activees|"""
                       r"""Arretee|Arretees|Demarree|Reussi|Echoue)(?=['"<])""")
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html")):
        page = open(chemin, encoding="utf-8").read()
        for faute in motif.findall(page):
            fautes.append(f"{os.path.basename(chemin)} : « {faute} »")
    assert not fautes, "etiquettes sans accents :\n" + "\n".join(fautes)


def test_la_preposition_a_garde_son_accent_devant_un_infinitif():
    """« rien a taper », « A activer ci-dessous », « A poser une fois le TLS
    en place » : c'est la preposition, pas le verbe avoir -- qui ne se suit
    d'ailleurs jamais directement d'un infinitif."""
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html")):
        page = open(chemin, encoding="utf-8").read()
        for texte in _textes_visibles(page) + _phrases_du_script(page):
            texte = " ".join(texte.split())
            for faute in MOTIF_A_INFINITIF.findall(texte):
                fautes.append(f"{os.path.basename(chemin)} : « {faute} » "
                              f"dans « {texte[:70]} »")
    assert not fautes, "« a » au lieu de « à » :\n" + "\n".join(fautes[:12])


def test_aucun_participe_passe_ne_tient_lieu_de_verbe_conjugue():
    """« la limite de tentatives ne protégé plus rien » : l'accent de trop,
    pose par une relecture qui accentue sans lire la phrase."""
    fautes = []
    for chemin in (os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   os.path.join(DOSSIER_PANNEAU, "app", "login.html")):
        page = open(chemin, encoding="utf-8").read()
        for texte in _textes_visibles(page) + _phrases_du_script(page):
            texte = " ".join(texte.split())
            for faute in MOTIF_PARTICIPE_SANS_AUXILIAIRE.findall(texte):
                fautes.append(f"{os.path.basename(chemin)} : « {faute} »")
    assert not fautes, "participe passe sans auxiliaire :\n" + "\n".join(fautes)


def test_aucun_texte_de_la_page_de_connexion_n_a_perdu_ses_accents():
    """La page de connexion echappait au controle, et cela se voyait :
    « Applications centralisees », « Heberge chez toi », « Accede a tes
    applications », « Connexion chiffree cote serveur ». C'est pourtant la
    PREMIERE page que voit quelqu'un a qui l'on partage une application."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "login.html"),
                encoding="utf-8").read()
    fautes = []
    for texte in _textes_visibles(page) + _phrases_du_script(page):
        for mot in MOTIF_SANS_ACCENT.findall(texte):
            fautes.append(f"{mot} — dans « {texte[:70]} »")
    assert not fautes, "textes visibles sans accents :\n" + "\n".join(fautes[:12])


def _messages_des_sondes():
    """Ce que les sondes AFFICHENT : leur nom et leur detail.

    Pris dans l'arbre syntaxique plutot qu'en jouant les sondes : le
    resultat ne doit pas dependre de la machine qui lance le test. Une sonde
    qui echoue ici et passe en CI montrerait des textes differents, et le
    controle des accents deviendrait une loterie.
    """
    import ast as _ast
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    messages = []
    for f in _ast.walk(_ast.parse(src)):
        if not isinstance(f, _ast.FunctionDef):
            continue
        if not (f.name.startswith("check_") or f.name.startswith("verif_")
                or f.name == "lancer_suite_du_panneau"):
            continue
        for n in _ast.walk(f):
            if (isinstance(n, _ast.Return) and isinstance(n.value, _ast.Tuple)
                    and len(n.value.elts) == 3):
                for e in n.value.elts[1:]:
                    messages += [x.value for x in _ast.walk(e)
                                 if isinstance(x, _ast.Constant)
                                 and isinstance(x.value, str)]
    return messages


def _textes_du_diagnostic():
    """Tout ce que la page du diagnostic affiche : ses fragments et son
    script. Le CSS est ecarte -- c'est du style, pas du texte."""
    import ast as _ast
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
               encoding="utf-8").read()
    arbre = _ast.parse(src)
    # Les DOCSTRINGS sont du commentaire : elles s'ecrivent sans accents,
    # c'est la regle du depot, et les inclure ferait echouer le test sur
    # chaque explication du fichier.
    ecartes = set()
    for n in _ast.walk(arbre):
        corps = getattr(n, "body", None)
        if (isinstance(corps, list) and corps
                and isinstance(corps[0], _ast.Expr)
                and isinstance(corps[0].value, _ast.Constant)
                and isinstance(corps[0].value.value, str)):
            ecartes.add(id(corps[0].value))
    phrases = []
    for n in arbre.body:
        if (isinstance(n, _ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], _ast.Name)
                and isinstance(n.value, _ast.Constant)
                and isinstance(n.value.value, str)):
            nom = n.targets[0].id
            if nom == "CSS":
                ecartes.add(id(n.value))
            elif nom == "SCRIPT":
                ecartes.add(id(n.value))
                # Le script : seules ses CHAINES sont du texte lu.
                phrases += _phrases_du_script(
                    "<script>" + n.value.value + "</script>")
    morceaux = []
    for n in _ast.walk(arbre):
        if isinstance(n, _ast.Constant) and isinstance(n.value, str):
            if id(n) not in ecartes:
                morceaux.append(n.value)
    textes = []
    for m in morceaux:
        # Un fragment coupe au milieu d'une balise -- « ... data-verifier=" »
        # -- laisserait passer ses ATTRIBUTS pour du texte affiche.
        m = re.sub(r"<[^>]*$", "", m)
        if "<" not in m and len(m.split()) < 2:
            continue      # un mot seul et sans balise : une cle, pas une phrase
        textes += _textes_visibles(m)
    return textes + phrases


def test_aucun_texte_du_diagnostic_n_a_perdu_ses_accents():
    """Meme regle pour l'application diagnostic : c'est une page de CodeLab,
    pas une application invitee."""
    fautes = []
    for texte in _textes_du_diagnostic() + _messages_des_sondes():
        if texte.startswith("{") or "checks." in texte:
            continue                          # une expression Python, pas un texte
        for mot in MOTIF_SANS_ACCENT.findall(texte):
            fautes.append(f"{mot} — dans « {texte[:70]} »")
    assert not fautes, "textes visibles sans accents :\n" + "\n".join(fautes[:12])


# ---------- l'accent ne doit pas deborder sur un NOM DE VARIABLE ----------
#
# La relecture des messages a accentue quatre identifiants au passage :
# len(clés), {détail_hotes}, {numéro} et {après - avant}. Chacun leve
# NameError a l'execution, et aucun ne se voit a la lecture -- dans une
# f-string, « clés » a exactement l'air d'un mot de la phrase.
#
# Deux d'entre eux vivaient sur le chemin NORMAL de check_cles_ssh : sur une
# installation ou authorized_keys est lisible, la page entiere du diagnostic
# tombait. Les deux autres faisaient echouer un test approfondi avec un
# message qui parlait d'autre chose.
#
# Ce controle lit l'arbre syntaxique et cherche les noms CHARGES qui ne sont
# definis nulle part. C'est exactement ce que fait un interpreteur au moment
# d'evaluer la ligne -- sauf qu'ici on le sait avant de livrer.

def _noms_jamais_definis(chemin):
    arbre = ast.parse(open(chemin, encoding="utf-8").read())
    definis = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            definis.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definis.add(n.name)
        elif isinstance(n, ast.arg):
            definis.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                definis.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            definis.add(n.name)
        elif isinstance(n, ast.Global):
            definis.update(n.names)
    # __file__ et compagnie sont poses par l'interpreteur, pas par le code.
    definis.update(["__file__", "__name__", "__doc__"])
    return sorted({n.id for n in ast.walk(arbre)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                   and n.id not in definis and not hasattr(builtins, n.id)})


def test_aucun_accent_n_a_deborde_sur_un_nom_de_variable():
    """Le test qui aurait vu passer len(clés) et {numéro}."""
    fichiers = [os.path.join(os.path.dirname(os.path.abspath(__file__)), nom)
                for nom in ("checks.py", "app.py")]
    if DOSSIER_PANNEAU:
        fichiers.append(os.path.join(DOSSIER_PANNEAU, "app", "app.py"))
    for chemin in fichiers:
        if not os.path.exists(chemin):
            continue
        inconnus = _noms_jamais_definis(chemin)
        assert not inconnus, (
            f"{os.path.basename(chemin)} charge des noms qui n'existent pas : "
            f"{inconnus} -- un accent a deborde sur une variable, et cela leve "
            f"NameError a l'execution")


# ---------- la verification approfondie, en direct ----------

def test_la_verification_annonce_ses_lignes_avant_de_les_jouer():
    """Elle jouait tout avant de repondre : on cliquait, et le navigateur
    restait blanc jusqu'a trois minutes -- le temps de la suite de
    regressions -- sans dire ou il en etait, ni s'il avancait encore."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
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
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
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
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
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


def checks_noms():
    return noms_des_tests()


def test_la_page_d_etat_range_ses_sondes_en_rubriques():
    """Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
    qu'on cherche. Une carte par sujet, son etat en pastille, et son propre
    bouton : on lit la rubrique qui cloche, et on approfondit celle-la."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
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
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
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
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
               encoding="utf-8").read()
    assert 'href="/theme.css"' in src, "le diagnostic ne charge pas le theme partage"
    assert "codelab-theme" in src, "le choix clair / sombre n'est pas repris"
    # Il arrive par un COOKIE : le localStorage du panneau appartient a une
    # autre origine, et n'est pas lisible d'ici -- il ne doit pas l'etre.
    assert "document.cookie.match" in src, (
        "sans le cookie, le diagnostic retombe sur le reglage du systeme")
    panneau = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                   encoding="utf-8").read()
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
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
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


def test_le_detail_d_une_sonde_n_est_plus_en_chasse_fixe():
    """Une phrase entiere en monospace se lit deux fois moins vite.

    Et toutes les lignes de cette page en etaient : le detail d'une sonde
    est une PHRASE, pas une valeur. La chasse fixe ne reste que pour ce qui
    est vraiment du code -- un chemin, un port, un extrait de configuration,
    dans une balise <code>.
    """
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
               encoding="utf-8").read()
    css = src[src.index("CSS = "):src.index('"""', src.index("CSS = ") + 10)]
    detail = css.split(".det{")[1].split("}")[0]
    assert "--mono" not in detail, (
        "le detail des sondes est revenu en chasse fixe")
    assert "--mono" in css.split("code{")[1].split("}")[0], (
        "plus rien n'est en chasse fixe, pas meme un chemin")


# ---------- 25. une donnee ne devient jamais du programme ----------
#
# Le defaut, trouve en auditant la branche et REPRODUIT dans un navigateur :
# le navigateur de dossiers ecrivait
#
#     <div onclick="browse('<chemin>/<nom du dossier>')">
#
# Le libelle etait echappe, le gestionnaire ne l'etait pas. Un dossier nomme
# avec une apostrophe refermait la chaine JavaScript, et la suite s'executait
# dans la page du panneau -- celle qui detient le jeton CSRF et les boutons
# demarrer, arreter, deployer, renommer.
#
# CE QUI REND LA CHOSE SERIEUSE : /workspace est inscriptible depuis une
# session SSH, et par TOUTE application deployee. Chacune tourne sous son
# propre uid, precisement pour ne pas pouvoir toucher au reste ; il lui
# suffisait de creer un dossier et d'attendre un clic de l'administrateur
# pour franchir cette frontiere.
#
# Echapper l'apostrophe n'y aurait rien change : le navigateur decode les
# entites AVANT de lire le JavaScript, et &#39; y redevient une apostrophe.
# La seule parade est de ne pas fabriquer de programme a partir d'une donnee.

def test_aucun_gestionnaire_en_ligne_ne_porte_de_donnee():
    """Un attribut onclick qui interpole une chaine, et le defaut revient.

    La regle se verifie sur le caractere : une apostrophe suivie d'une
    interpolation, c'est une chaine de programme fabriquee a partir d'une
    donnee. Un argument numerique -- (i) sans apostrophes -- reste permis :
    un entier ne peut pas refermer quoi que ce soit.
    """
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    fautes = [l.strip() for l in page.split("\n") if "'${" in l]
    assert not fautes, (
        "une donnee est interpolee dans une chaine JavaScript ecrite en "
        "attribut -- passe par data- et la table ACTIONS :\n"
        + "\n".join(fautes[:5]))
    # Et la table existe, avec son ecouteur unique.
    assert "const ACTIONS = {" in page
    assert "e.target.closest('[data-agir]')" in page, (
        "l'ecouteur delegue a disparu : les boutons generes ne font plus rien")


def test_le_navigateur_de_dossiers_construit_du_dom():
    """Le chemin est capture par une fermeture, jamais ecrit comme du texte.

    C'est ce qui rend la regle tenable : il n'y a plus d'echappement a poser
    correctement, donc plus d'echappement a oublier.
    """
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function peuplerDossiers(")[1].split("\n}")[0]
    assert "n.textContent = libelle" in bloc, (
        "le nom du dossier repasse par du HTML : textContent n'interprete "
        "rien, une chaine de HTML si")
    assert "n.onclick = () => aller(chemin)" in bloc, (
        "le chemin redevient du texte de programme")
    # Les DEUX navigateurs passent par la : celui du formulaire d'ajout et
    # celui du formulaire de modification. Un seul corrige, c'est l'autre qui
    # porte le defaut.
    for fonction in ("async function browse(p){", "async function browseEdit(p){"):
        corps = page.split(fonction)[1].split("\n}")[0]
        assert "peuplerDossiers(" in corps, fonction


def test_le_navigateur_de_dossiers_lit_ce_que_le_serveur_envoie(
        client, tmp_path, monkeypatch):
    """Les deux cotes du meme contrat, verifies ensemble.

    Le navigateur du formulaire de modification lisait x.path et x.name sur
    les entrees de "dirs". Or /api/browse renvoie des NOMS -- des chaines.
    Il affichait donc autant de lignes vides que de dossiers, toutes vers
    « undefined ». Personne ne l'avait vu : aucun test ne regardait ce
    rendu, et le navigateur du formulaire d'ajout, lui, marchait.
    """
    racine = tmp_path / "racine"
    (racine / "un projet").mkdir(parents=True)
    (racine / ".cache").mkdir()
    monkeypatch.setattr(app, "ROOT", str(racine))
    client.post("/login", json={"password": "secret-de-test"})
    d = client.get("/api/browse", query_string={"path": str(racine)}).get_json()
    # Des chaines, et les dossiers caches restent caches.
    assert d["dirs"] == ["un projet"], d["dirs"]
    assert all(isinstance(x, str) for x in d["dirs"])

    # Et la page les consomme comme des chaines.
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    bloc = page.split("function peuplerDossiers(")[1].split("\n}")[0]
    assert "(d.dirs || []).forEach(nom =>" in bloc, (
        "la page relit un objet la ou le serveur envoie un nom")
    assert ".path" not in bloc.split("d.path + '/' + nom")[0].split("forEach")[-1], (
        "une entree de dirs est traitee comme un objet")


# ---------- 26. ce que le navigateur a le droit de faire ----------
#
# Le panneau ne posait aucun en-tete de securite. Ils ne reparent rien : ils
# reduisent ce qu'une faille permet, et c'est exactement pour le jour ou la
# premiere barriere cede qu'on les veut deja en place.

def test_le_panneau_pose_ses_entetes_de_securite(client):
    """Les quatre en-tetes, sur la page du panneau ET sur celle de connexion.

    Celle de connexion surtout : c'est elle qui porte le mot de passe, et
    c'est la seule que voit quelqu'un qui n'a pas encore de session.
    """
    for chemin in ("/login", "/"):
        client.post("/login", json={"password": "secret-de-test"})
        r = client.get(chemin)
        for entete in ("Content-Security-Policy", "X-Frame-Options",
                       "Referrer-Policy", "X-Content-Type-Options"):
            assert entete in r.headers, f"{entete} manque sur {chemin}"
    csp = r.headers["Content-Security-Policy"]
    # Le clickjacking ne se rattrape pas au jeton CSRF : un clic dans une
    # iframe invisible est un VRAI clic, dans une VRAIE session.
    assert "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    # Un script injecte ne doit pouvoir ni charger du code ailleurs, ni
    # renvoyer ce qu'il a lu.
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    # Et la page doit continuer de s'afficher : sa police, son logo en
    # data: et ses styles en ligne sont dans la regle.
    for permis in ("img-src 'self' data:", "font-src 'self'",
                   "style-src 'self' 'unsafe-inline'"):
        assert permis in csp, permis


def test_hsts_n_est_pose_que_derriere_du_tls(client, monkeypatch):
    """HSTS est un ENGAGEMENT : le navigateur refuse ensuite le http pendant
    toute sa duree. Le poser sur une installation servie en clair la rendrait
    injoignable, sans moyen de revenir en arriere."""
    monkeypatch.setattr(app, "https_actif", lambda: False)
    assert "Strict-Transport-Security" not in client.get("/login").headers
    monkeypatch.setattr(app, "https_actif", lambda: True)
    assert "Strict-Transport-Security" in client.get("/login").headers
    # Sans includeSubDomains : les sous-domaines ne nous appartiennent pas.
    assert "includeSubDomains" not in app.HSTS


def test_une_application_hebergee_garde_sa_propre_politique(deux_espaces):
    """La politique du panneau casserait la premiere application qui charge
    une police, une carte ou un script ailleurs -- et ce serait decider a sa
    place. Restent nosniff, et frame-ancestors -- qui ne dit rien de ce que
    l'application charge, seulement de QUI peut l'encadrer (voir
    proteger_l_encadrement)."""
    r = deux_espaces.get("/public/")
    csp = r.headers.get("Content-Security-Policy", "")
    assert csp.startswith("frame-ancestors ") and ";" not in csp, (
        "seul frame-ancestors est impose aux applications : " + csp)
    assert "X-Frame-Options" not in r.headers
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


# ---------- 27. la taille d'une requete ----------
#
# Il n'y en avait aucune. Seize fils, seize corps sans fin, et le service
# tombe sans qu'un seul mot de passe ait ete tente.

def test_un_corps_trop_gros_est_refuse(client):
    client.post("/login", json={"password": "secret-de-test"})
    trop = "x" * (app.CORPS_MAX_PANNEAU + 1)
    r = client.put("/api/categories", data=trop, content_type="application/json")
    assert r.status_code == 413
    # Un message lisible, et en JSON : la page attend du JSON, et la page
    # d'erreur par defaut de Flask lui aurait rendu du HTML.
    assert "maximum" in (r.get_json() or {}).get("error", "")
    # Ce qui passe, passe toujours.
    r = client.put("/api/categories", json={"categories": ["essais"]})
    assert r.status_code == 200


def test_le_plafond_des_applications_est_plus_large_que_celui_du_panneau():
    """Deux usages sans rapport : les API du panneau n'envoient qu'un logo,
    une application hebergee peut recevoir un fichier."""
    assert app.CORPS_MAX_APPLICATION > app.CORPS_MAX_PANNEAU
    # Le filet de Flask vaut le plus large des deux : c'est lui qui coupe un
    # corps annonce sans taille, et il ne doit pas bloquer les applications.
    assert app.flask_app.config["MAX_CONTENT_LENGTH"] == app.CORPS_MAX_APPLICATION
    # La garde decide sur le Content-Length : elle refuse AVANT de lire.
    corps = inspect.getsource(app.borner_le_corps)
    assert "request.content_length" in corps
    assert "get_data" not in corps


# ---------- 28. aucun service ne peut prendre toute la machine ----------
#
# Vecu a l'envers : rien ne bornait rien. Une application partie en boucle
# emportait la machine entiere -- et le panneau avec, donc on ne pouvait meme
# plus l'arreter depuis le panneau. Les journaux, eux, grossissaient jusqu'a
# remplir le disque, et la sonde l'annoncait quand il l'etait deja.

def _composes():
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    fichiers = [os.path.join(racine, n)
                for n in ("docker-compose.yml", "docker-compose-casaos.yml")]
    presents = [f for f in fichiers if os.path.exists(f)]
    if not presents:
        pytest.skip("composes absents de cette image")
    assert len(presents) == 2, "un des deux composes manque"
    return presents


def _services_du_compose(chemin):
    """Les services et leurs reglages, lus sans PyYAML.

    L'image du diagnostic n'embarque pas PyYAML, et l'ajouter pour un test
    ferait porter une dependance a la production. Le fichier est indente de
    facon reguliere : deux espaces par service, quatre par reglage.
    """
    services, courant, dans_services = {}, None, False
    for ligne in open(chemin, encoding="utf-8"):
        # Les sections de premier niveau : seule « services: » nous
        # interesse. Sans ce garde, « codelab-ia: » declare sous
        # « networks: » passait pour un service de plus.
        if ligne[:1] not in (" ", "\n", "#", ""):
            dans_services = ligne.rstrip() == "services:"
            courant = None
            continue
        if not dans_services:
            continue
        if ligne.startswith("  ") and not ligne.startswith("   ") and ligne.rstrip().endswith(":"):
            courant = ligne.strip().rstrip(":")
            services[courant] = {}
        elif courant and ligne.startswith("    ") and not ligne.startswith("     "):
            cle, _, valeur = ligne.strip().partition(":")
            services[courant][cle] = valeur.strip()
        elif courant and ligne.startswith("          ") and not ligne.startswith("           "):
            cle, _, valeur = ligne.strip().partition(":")
            if cle in ("memory", "cpus", "pids"):
                services[courant][f"deploy_{cle}"] = valeur.strip()
    return {n: r for n, r in services.items() if n.startswith("codelab-")}


def _bloc_du_service(chemin, nom):
    """Les lignes brutes d'un service, listes imbriquees comprises."""
    lignes, dedans, dans_services = [], False, False
    for ligne in open(chemin, encoding="utf-8"):
        if ligne[:1] not in (" ", "\n", "#", ""):
            dans_services, dedans = ligne.rstrip() == "services:", False
            continue
        if not dans_services:
            continue
        if ligne.startswith("  ") and not ligne.startswith("   ") and ligne.rstrip().endswith(":"):
            dedans = ligne.strip().rstrip(":") == nom
            continue
        if dedans:
            lignes.append(ligne)
    return "".join(lignes)


def test_chaque_service_est_borne_dans_les_deux_composes():
    """Memoire, processeur, processus, journaux. Les quatre, partout.

    Ce sont des PLAFONDS et non des reservations : ce qu'on empeche, c'est
    qu'UN service prenne tout -- pas de repartir la memoire a l'avance.
    """
    for chemin in _composes():
        services = _services_du_compose(chemin)
        # Les memes services des deux cotes : un compose qui en oublie un
        # est une installation qui ne demarre pas pareil selon la porte
        # d'entree.
        assert len(services) == 7, (chemin, sorted(services))
        for nom, reglages in services.items():
            assert "pids_limit" in reglages or "deploy_pids" in reglages, (
                f"{nom} n'a pas de limite processus dans {os.path.basename(chemin)}")
            assert "logging" in reglages, f"{nom} n'a pas de logging dans {os.path.basename(chemin)}"
            assert ("cpus" in reglages and "mem_limit" in reglages) or "deploy" in reglages, (
                f"{nom} n'a pas de limites de ressources dans {os.path.basename(chemin)}")
        texte = open(chemin, encoding="utf-8").read()
        # La rotation est ce qui empeche un journal de remplir le disque :
        # le driver seul ne borne rien.
        # Un compte par SERVICE, et non un nombre ecrit en dur : un service
        # ajoute demain doit faire echouer ce test tant qu'il n'a pas sa
        # rotation, sans qu'on ait a se souvenir de mettre un chiffre a jour.
        assert texte.count('max-size: "10m"') == len(services), chemin
        assert texte.count('max-file: "3"') == len(services), chemin


def test_le_panneau_a_la_part_la_plus_large():
    """Ce conteneur porte le panneau, les builds npm ET toutes les
    applications deployees, qui sont ses processus fils. Le borner comme un
    service ordinaire, c'est une application tuee au hasard en plein build."""
    def en_mo(v):
        return int(v[:-1]) * 1024 if v.endswith("g") else int(v.rstrip("m"))
    for chemin in _composes():
        services = _services_du_compose(chemin)
        part = {n: en_mo(r.get("mem_limit", r.get("deploy_memory")))
            for n, r in services.items()}
        assert part["codelab-app-manager"] == max(part.values()), chemin
        # Et le relais nginx, qui ne fait que relayer, reste AU PLANCHER --
        # il n'est plus seul dessus depuis qu'une reservation exterieure
        # empeche de descendre plus bas (voir le test du plancher), mais
        # aucun service ne doit passer sous lui.
        assert part["codelab-dagster-proxy"] == min(part.values()), chemin


# ---------- 29. il n'y avait aucune sauvegarde ----------
#
# Rien ne copiait utilisateurs.json, apps.json, le journal des acces ni la
# moindre base. Un disque perdu, et il fallait tout redeclarer de memoire :
# les comptes, les droits par projet, les applications, les categories,
# l'adresse publique -- et les donnees, elles, ne se redeclarent pas.
#
# Le partage des roles n'est pas un choix d'organisation : pg_dump REFUSE de
# sauvegarder un serveur plus recent que lui. Le panneau tourne sur une image
# dont le client Postgres a une version de retard ; codelab-postgres, lui,
# porte le pg_dump de la version exacte du serveur. Chacun sauvegarde ce
# qu'il est SEUL a pouvoir sauvegarder.

@pytest.fixture
def sauvegardes(tmp_path, monkeypatch):
    """Un dossier d'etat a sauvegarder, et un dossier ou ecrire."""
    etat = tmp_path / "etat"
    (etat / "logs").mkdir(parents=True)
    (etat / "utilisateurs.json").write_text('{"marie": {}}')
    (etat / "apps.json").write_text('{"demo": {}}')
    (etat / "acces.jsonl").write_text('{"genre": "connexion"}\n')
    (etat / "logs" / "demo.log").write_text("x" * 5000)
    (etat / "home" / "12302").mkdir(parents=True)
    (etat / "home" / "12302" / "cache").write_text("x" * 5000)
    cible = tmp_path / "sauvegardes"
    cible.mkdir()
    monkeypatch.setattr(app, "STATE_DIR", str(etat))
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(cible))
    return etat, cible


def test_l_archive_de_l_etat_porte_ce_qui_ne_se_redeclare_pas(sauvegardes):
    """Les comptes, les applications, le journal. Pas les journaux
    d'applications : ils se regenerent, et ils sont de loin ce qui pese."""
    _, cible = sauvegardes
    app.save({"demo": {"path": "/w/d", "command": "x", "port": 9101}})
    app.journaliser("connexion", qui="marie", ip="10.0.0.1")
    chemin = app.sauvegarder_l_etat()
    import sqlite3
    import tarfile
    with tarfile.open(chemin) as archive:
        noms = {n.lstrip("./") for n in archive.getnames()}
        # La base y est sous forme d'INSTANTANE coherent, pris par SQLite --
        # jamais le fichier vivant ni son -wal.
        archive.extract("./codelab.db", path=cible / "restauree")
    assert "codelab.db" in noms
    assert not {"codelab.db-wal", "codelab.db-shm"} & noms
    cx = sqlite3.connect(cible / "restauree" / "codelab.db")
    assert [r[0] for r in cx.execute("SELECT nom FROM applications")] == ["demo"]
    assert cx.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
    cx.close()
    # Les journaux d'applications pesent et se regenerent ; le dossier
    # personnel des applications est un cache, ECRIVABLE PAR ELLES -- l'y
    # inclure leur laisserait decider du poids de la sauvegarde.
    for exclu in app.ARCHIVE_EXCLUS:
        assert not [n for n in noms if n.startswith(exclu)], (exclu, sorted(noms))
    # 0600 : l'archive porte les empreintes de mots de passe et le journal
    # des acces. Les applications tournent sous un autre uid, et n'ont aucune
    # raison de pouvoir la lire.
    assert oct(os.stat(chemin).st_mode)[-3:] == "600"
    # Et aucun secret : credentials.env ne vit pas dans le dossier d'etat, et
    # c'est ce qui permet de recopier l'archive ailleurs sans la vider.
    assert not [n for n in noms if "credentials" in n]


def test_une_archive_a_moitie_ecrite_ne_prend_pas_la_place_de_la_bonne(sauvegardes):
    """Un arret au milieu de l'ecriture laisserait sinon une archive tronquee
    portant l'heure la plus recente -- celle qu'on choisirait pour
    restaurer."""
    source = inspect.getsource(app.sauvegarder_l_etat)
    assert ".partielle" in source and "os.replace(" in source
    # Elle est aussi RELUE avant d'etre gardee, comme les dumps Postgres.
    assert "getmembers()" in source
    _, cible = sauvegardes
    app.sauvegarder_l_etat()
    assert not [n for n in os.listdir(cible) if n.endswith(".partielle")]


def test_la_rotation_garde_les_plus_recentes(sauvegardes, monkeypatch):
    _, cible = sauvegardes
    monkeypatch.setattr(app, "SAUVEGARDES_GARDEES", 3)
    for jour in range(6):
        (cible / f"{app.SAUVEGARDE_PREFIXE}2026010{jour}-000000.tar.gz").write_text("x")
    app._rotation_sauvegardes()
    restantes = sorted(os.listdir(cible))
    assert len(restantes) == 3
    # Les trois DERNIERES : le nom porte la date, donc l'ordre alphabetique
    # est l'ordre chronologique -- y compris au passage d'une annee, la ou un
    # tri sur la date de fichier suivrait une copie.
    assert restantes[0].endswith("20260103-000000.tar.gz")


def test_sans_volume_monte_le_panneau_ne_sauvegarde_pas(monkeypatch, tmp_path):
    """Ecrire dans la couche du conteneur donnerait une sauvegarde qui
    disparait au premier « docker compose down » : pire que rien, parce
    qu'on croit en avoir une."""
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(tmp_path / "absent"))
    assert not app.sauvegardes_actives()
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(tmp_path))
    assert app.sauvegardes_actives()
    monkeypatch.setattr(app, "SAUVEGARDE_HEURES", 0)
    assert not app.sauvegardes_actives()


def test_la_sonde_des_sauvegardes_distingue_les_trois_cas(tmp_path):
    """Absente, en retard, morte : trois rangs, trois gestes differents."""
    dossier = tmp_path / "sauvegardes"
    dossier.mkdir()

    # Rien encore : une alerte, pas une panne -- au premier demarrage, c'est
    # l'etat normal pendant quelques secondes.
    etat, _, detail = check_sauvegardes(str(dossier))
    assert etat is Etat.ALERTE and "rien pour" in detail

    def poser(nom, heures):
        f = dossier / nom
        f.write_text("x")
        quand = time.time() - heures * 3600
        os.utime(f, (quand, quand))

    poser("base-codelab-20260101-000000.dump", 2)
    poser("panneau-20260101-000000.tar.gz", 2)
    assert check_sauvegardes(str(dossier))[0] is Etat.OK

    # Un passage manque. C'est la PLUS RECENTE de chaque famille qui compte :
    # en ajouter une vieille a cote d'une fraiche ne change rien, et c'est
    # bien ce qu'on veut -- sinon la rotation elle-meme declencherait
    # l'alerte.
    poser("panneau-20260101-000000.tar.gz", SAUVEGARDE_FRAICHE_H + 1)
    assert check_sauvegardes(str(dossier))[0] is Etat.ALERTE

    # Plus rien depuis trois jours : ce n'est plus un retard, c'est arrete.
    poser("panneau-20260101-000000.tar.gz", SAUVEGARDE_VIEILLE_H + 1)
    etat, _, detail = check_sauvegardes(str(dossier))
    assert etat is Etat.ECHEC and "ne tourne plus" in detail

    # Volume absent : le compose est a completer.
    etat, _, detail = check_sauvegardes(str(tmp_path / "jamais"))
    assert etat is Etat.ALERTE and "docker-compose.yml" in detail


def test_les_deux_services_ecrivent_dans_le_meme_dossier():
    """Les dumps et l'archive de l'etat au meme endroit : on copie un seul
    dossier pour tout emporter."""
    for chemin in _composes():
        services = _services_du_compose(chemin)
        texte = open(chemin, encoding="utf-8").read()
        assert texte.count("/sauvegardes:/sauvegardes") == 2, (
            f"{os.path.basename(chemin)} : les deux services doivent monter "
            f"le volume de sauvegarde")
        assert set(services) >= {"codelab-postgres", "codelab-app-manager"}


def test_la_sauvegarde_des_bases_est_verifiee_avant_d_etre_gardee():
    """Une sauvegarde qu'on decouvre illisible le jour de la restauration ne
    vaut pas mieux que pas de sauvegarde."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("entrypoint postgres absent de cette image")
    script = open(chemin, encoding="utf-8").read()
    assert "pg_dump" in script and "-Fc" in script
    # La relecture complete du fichier, et le retrait s'il ne passe pas.
    bloc = script.split("sauvegarder_une_base()")[1].split("\n}")[0]
    assert "pg_restore --list" in bloc
    assert bloc.count("rm -f \"$fichier\"") == 2, (
        "un dump illisible ou rate doit etre ecarte, jamais garde")
    # Et la rotation, par base.
    assert "SAUVEGARDES_GARDEES" in bloc


def test_la_restauration_est_documentee():
    """Une sauvegarde dont personne ne sait se servir n'est pas une
    sauvegarde. La procedure doit etre ecrite, avec les commandes exactes."""
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "README.md")
    if not os.path.exists(chemin):
        pytest.skip("README absent de cette image")
    texte = open(chemin, encoding="utf-8").read()
    assert "pg_restore" in texte, "la restauration des bases n'est pas ecrite"
    assert "panneau-" in texte and "tar" in texte, (
        "la restauration de l'etat du panneau n'est pas ecrite")
    assert "/sauvegardes" in texte


# ---------- 30. les applications par defaut ----------
#
# Demande : « Dagster doit se comporter comme les autres applications, meme si
# rien n'est configurable pour elle. De la meme maniere, Diagnostic ne doit
# pas etre configurable. »
#
# Deux applications que CodeLab apporte avec lui. Elles se lisent comme les
# autres -- meme tuile, meme ligne, meme fiche -- et c'est le but : ce sont
# des applications, pas des boutons a part. Ce qui change tient en une
# phrase : il n'y a rien a y configurer.

def test_les_applications_par_defaut_ne_se_configurent_pas(hub_dagster):
    """Le refus vient du SERVEUR, pas de la page.

    Masquer un onglet ne protege rien : la route reste appelable a la main,
    et c'est exactement ce que fait qui cherche a contourner.
    """
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    apps = app.load()
    # Le diagnostic est declare comme sur une vraie installation.
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "visibility": "privee"}
    app.save(apps)

    for nom in app.APPS_PAR_DEFAUT:
        refus = [
            ("put", f"/api/app/{nom}", {"path": "/w/x", "command": "x"}),
            ("delete", f"/api/app/{nom}", None),
            ("post", f"/api/app/{nom}/renommer", {"nom": "autre"}),
            ("post", f"/api/visibility/{nom}", {"visibility": "publique"}),
            ("put", f"/api/apps/{nom}/acces", {"utilisateurs": ["marie"]}),
            ("delete", f"/api/app/{nom}/logo", None),
        ]
        for methode, route, corps in refus:
            r = getattr(c, methode)(route, json=corps or {})
            assert r.status_code == 403, f"{methode.upper()} {route} -> {r.status_code}"
            assert "par défaut" in (r.get_json() or {}).get("error", ""), route

    # Et une application ordinaire, elle, se configure toujours.
    r = c.post("/api/visibility/site", json={"visibility": "privee"})
    assert r.status_code == 200, r.data


def test_dagster_figure_parmi_les_applications(hub_dagster):
    """Elle vivait sur le hub et nulle part ailleurs : on la cherchait dans
    « Applications », elle n'y etait pas."""
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    lignes = c.get("/api/apps").get_json()["apps"]
    par_nom = {a["name"]: a for a in lignes}
    assert app.DAGSTER_NOM in par_nom, "Dagster manque dans la liste"
    d = par_nom[app.DAGSTER_NOM]
    assert d["externe"] is True and d["par_defaut"] is True
    # Les memes cles que les autres lignes : la page les lit sans se demander
    # a chaque fois si elle a affaire a un service.
    ordinaire = par_nom["site"]
    assert set(ordinaire) - set(d) == set(), sorted(set(ordinaire) - set(d))
    # Et la liste reste triee : une tuile qui change de place d'un
    # rafraichissement a l'autre se cherche a chaque fois.
    assert [a["name"] for a in lignes] == sorted(par_nom)


def test_une_application_par_defaut_est_reservee_a_l_administrateur(hub_dagster):
    """Pas « privee, donc autorisable a un compte » : reservee.

    Dagster permet de lancer des jobs, donc d'executer du code sur cette
    machine ; le diagnostic nomme les conteneurs, les comptes sans second
    facteur et ce qui est expose. Ni un clic ni une faute de manipulation ne
    doivent pouvoir les accorder.
    """
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "x", "port": 9109,
                                "visibility": "privee"}
    app.save(apps)
    c = hub_dagster
    _connecte(c, "marie", "un-mot-de-passe")
    for nom in app.APPS_PAR_DEFAUT:
        r = c.get(f"/{nom}/")
        assert r.status_code in (302, 403, 404), f"{nom} -> {r.status_code}"
    # Son propre projet, lui, s'ouvre toujours.
    assert c.get("/site/").status_code != 403

    # Et son hub ne lui montre pas de tuile qui ne s'ouvre pas : une porte
    # fermee affichee comme une porte, c'est quelqu'un qui cherche ce qu'il a
    # mal fait. Meme si l'administrateur la lui a accordee par megarde dans
    # sa fiche -- ce que l'API refuse, mais qu'un fichier edite a la main
    # pourrait contenir.
    d = c.get("/api/mes-apps").get_json()
    noms = {a["name"] for a in d["apps"]} | {a["name"] for a in d.get("masquees", [])}
    assert not (noms & set(app.APPS_PAR_DEFAUT)), sorted(noms)
    # Dagster n'apparait pas : son interface execute du code sur la machine.
    # Et rien d'autre n'apparait a sa place : le hub d'un compte utilisateur
    # ne porte aucune tuile de service.
    services = {x["name"] for x in d.get("services", [])}
    assert services == set(), sorted(services)


def test_les_applications_par_defaut_suivent_la_frontiere_de_l_administration(
        hub_dagster, tmp_path, monkeypatch):
    """« Fais la meme chose pour diagnostic. »

    Le diagnostic dit ce qui tourne, ce qui est expose et quels comptes n'ont
    pas de second facteur : c'est une page d'administration par ce qu'elle
    montre. Elle s'arrete donc ou l'administration s'arrete -- meme reglage,
    meme frontiere, y compris au milieu d'une session deja ouverte.
    """
    monkeypatch.setattr(app, "EXPOSITION_FILE", str(tmp_path / "exposition.json"))
    monkeypatch.setattr(app, "admin_limite_au_reseau_local", lambda: True)
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "x", "port": 9109,
                                "visibility": "privee"}
    app.save(apps)
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"},
           environ_overrides={"REMOTE_ADDR": "192.168.1.10"})

    # Depuis le reseau local : la requete atteint le proxy (l'application
    # n'ecoute pas dans un test, d'ou l'erreur de passerelle).
    r = c.get("/diagnostic/", environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
    assert r.status_code != 403, r.data[:160]

    # La MEME session, vue d'ailleurs : refusee.
    r = c.get("/diagnostic/", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code == 403
    assert b"reseau local" in r.data or b"r\xc3\xa9seau local" in r.data


def test_l_administrateur_du_reseau_local_reste_connecte(client, tmp_path, monkeypatch):
    """La question posee, verifiee plutot que lue.

    Toutes les adresses privees ouvrent l'administration et la GARDENT :
    le controle a lieu a chaque requete, il ne doit donc pas refuser une
    session qui n'a pas bouge. Tailscale (100.64/10) compte comme local --
    c'est ainsi qu'on administre depuis l'exterieur sans rien ouvrir.
    """
    monkeypatch.setattr(app, "EXPOSITION_FILE", str(tmp_path / "exposition.json"))
    monkeypatch.setattr(app, "admin_limite_au_reseau_local", lambda: True)
    locales = ("192.168.1.42", "10.8.0.5", "172.20.0.3", "127.0.0.1",
               "100.101.102.103")
    for ip in locales:
        app._login_attempts.clear()
        r = client.post("/login", json={"password": "secret-de-test"},
                        environ_overrides={"REMOTE_ADDR": ip})
        assert r.status_code == 200, f"{ip} : connexion refusee"
        # Et la session TIENT : trois requetes de suite, comme un usage reel.
        for _ in range(3):
            assert client.get("/api/apps",
                              environ_overrides={"REMOTE_ADDR": ip}).status_code == 200, ip
    # De l'exterieur, rien ne s'ouvre.
    for ip in ("2.15.44.7", "203.0.113.9"):
        app._login_attempts.clear()
        assert client.post("/login", json={"password": "secret-de-test"},
                           environ_overrides={"REMOTE_ADDR": ip}).status_code == 403, ip


def test_une_application_par_defaut_ne_s_arrete_pas_a_la_main(hub_dagster):
    """Le cycle de vie aussi est refuse cote SERVEUR, pas seulement masque.

    Le piege qu'on ferme : une application par defaut n'est pas
    configurable. La laisser s'arreter d'un clic, c'est offrir un bouton
    « Arreter » sans le bouton qui rallume -- l'installation se retrouverait
    sans diagnostic, sans rien pour le dire et sans moyen d'y revenir.

    Les gardes existaient deja dans le panneau, mais AUCUN test ne les
    tenait : un refactor pouvait les faire disparaitre en silence.
    """
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "visibility": "privee"}
    app.save(apps)

    for nom in app.APPS_PAR_DEFAUT:
        for route in (f"/api/toggle/{nom}", f"/api/restart/{nom}",
                      f"/api/deploy/{nom}", f"/api/build/{nom}"):
            r = c.post(route, json={})
            assert r.status_code == 403, f"POST {route} -> {r.status_code}"
            # Le message NOMME l'application et l'action : « 403 » seul
            # n'apprend rien a qui lit la reponse.
            message = (r.get_json() or {}).get("error", "")
            assert nom in message, route
            assert "interdite" in message, route

    # Et une application ordinaire n'est pas prise dans le meme filet -- sans
    # quoi un refus rendu pour tout le monde passerait ce test.
    assert app.refus_action_protegee("site", "arrêt/redémarrage") is None


def test_le_panneau_tient_ses_applications_par_defaut_debout(hub_dagster):
    """Le chemin qui mene a l'etat mort, et ce qui en sort.

    start() marque « enabled: False » quand le lancement echoue (limite
    memoire refusee, abandon de privileges impossible). Personne ne l'a
    voulu, et le bouton qui rallumerait est justement celui qu'on interdit
    au test precedent. Sans ce rattrapage au demarrage, l'application par
    defaut restait eteinte pour de bon.
    """
    apps = app.load()
    apps[app.DAGSTER_NOM] = {"path": "/w/dag", "command": "x", "port": 9108,
                             "enabled": False}
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False}
    app.save(apps)

    app.tenir_les_applications_par_defaut()

    apres = app.load()
    for nom in app.APPS_PAR_DEFAUT:
        assert apres[nom]["enabled"] is True, nom
    # La tuile du diagnostic dit ce qu'elle est : sa fiche n'ayant pas de
    # formulaire, cette phrase ne s'ecrit nulle part ailleurs.
    assert apres[app.DIAGNOSTIC_NOM]["description"] == app.DIAGNOSTIC_DESCRIPTION
    # Rien n'est impose a Dagster : ce qu'on repare ici, c'est l'etat mort.
    assert "description" not in apres[app.DAGSTER_NOM]
    # Une application ordinaire arretee le RESTE : un arret volontaire est un
    # choix, et le rattrapage ne vaut que pour les deux qui n'ont pas le choix.
    assert not apres["site"].get("enabled")


def test_la_description_deja_posee_n_est_pas_ecrasee(hub_dagster):
    """Le rattrapage comble un manque, il ne decide pas a la place."""
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False,
                                "description": "Ma phrase à moi"}
    app.save(apps)

    app.tenir_les_applications_par_defaut()

    apres = app.load()
    assert apres[app.DIAGNOSTIC_NOM]["description"] == "Ma phrase à moi"
    assert apres[app.DIAGNOSTIC_NOM]["enabled"] is True


def test_le_demarrage_du_panneau_rallume_ce_qui_ne_se_rallume_pas_a_la_main(
        hub_dagster, monkeypatch):
    """Bout en bout : le rattrapage est BRANCHE, pas seulement ecrit.

    Une fonction correcte que resume() n'appelle pas ne repare rien.
    """
    demarrees = []
    monkeypatch.setattr(app, "start", lambda n, attendre=True: demarrees.append(n))
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False}
    app.save(apps)

    app.resume()

    assert app.DIAGNOSTIC_NOM in demarrees, demarrees
    assert app.load()[app.DIAGNOSTIC_NOM]["enabled"] is True


def test_le_menu_ne_defile_pas_avec_la_page():
    """Il partait avec elle.

    Sur une longue liste d'applications, changer de rubrique demandait de
    remonter d'abord -- le menu etait reste tout en haut.
    """
    page = _page_panneau("dashboard.html")
    debut = page.index(".sidebar{")
    regle = page[debut:page.index("}", debut)]
    assert "position:sticky" in regle, regle
    assert "top:52px" in regle, regle
    # Sa propre hauteur, donc son propre defilement : un menu plus long que
    # l'ecran doit rester entierement atteignable.
    assert "overflow-y:auto" in regle, regle
    # Et le rognage horizontal SURVIT : c'est lui qui decoupe les libelles
    # pendant que la colonne se replie. Un "overflow:auto" pour les deux axes
    # ferait apparaitre une barre de defilement pendant l'animation.
    assert "overflow-x:hidden" in regle, regle
    # Sans cela, le parent etire la colonne et "sticky" n'a plus de prise.
    debut_corps = page.index(".page-body{")
    assert "align-items:flex-start" in page[debut_corps:page.index("}", debut_corps)]


def test_le_moniteur_rallume_une_application_par_defaut_eteinte(hub_dagster, monkeypatch):
    """Le rattrapage ne vaut pas qu'au demarrage du panneau.

    Il n'avait lieu que dans resume(). Une application par defaut eteinte
    entre deux redemarrages -- par l'echec de lancement de start(), le seul
    chemin qui y mene -- le restait jusqu'au suivant. Sur une machine qui ne
    redemarre pas, c'est-a-dire pour de bon.
    """
    monkeypatch.setattr(app, "start", lambda n, attendre=True: demarrees.append(n))
    demarrees = []
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = {"path": "/w/diag", "command": "python3 app.py",
                                "port": 9109, "enabled": False}
    app.save(apps)

    app.monitor_tick()

    assert app.load()[app.DIAGNOSTIC_NOM]["enabled"] is True
    # Et le tour suivant la relance, puisqu'elle est desormais "enabled".
    assert app.DIAGNOSTIC_NOM in demarrees, demarrees


def test_le_moniteur_ne_rallume_pas_une_application_ordinaire_arretee(hub_dagster, monkeypatch):
    """Un arret volontaire est un choix, et il se respecte.

    Le rattrapage ne vaut que pour les deux qui n'ont pas le choix -- celles
    dont le bouton « Arreter » est refuse par le serveur.
    """
    monkeypatch.setattr(app, "start", lambda n, attendre=True: None)
    app.monitor_tick()
    assert not app.load()["site"].get("enabled")


# ---------- 30 bis. le nom se lit, le chemin se tape ----------
#
# Demande : « Le nom de l'application peut etre en majuscule, remplace par des
# minuscules dans le path, et les espaces sont autorises dans le nom,
# remplaces par des tirets dans le path. La validite du nom de l'app doit
# dependre du path resultant. »
#
# Avant, les deux etaient la MEME chaine, et la regle etait un refus sec :
# une majuscule rendait le nom invalide, sans rien dire de plus. On devinait
# la contrainte au lieu de la lire.

def test_le_chemin_se_tire_du_nom():
    """La transformation, cas par cas."""
    for nom, chemin in [
        ("Mon Site", "mon-site"),
        ("SITE", "site"),
        ("Mon  --  Site", "mon-site"),      # ni doubles tirets ni doubles espaces
        ("  Site  ", "site"),               # les bords se taillent
        ("Café", "caf"),                    # l'accent ne passe pas dans l'adresse
        ("site_2", "site_2"),               # le tiret bas reste
        ("---", ""),                        # rien d'utilisable : pas de chemin
        ("", ""),
        (None, ""),
    ]:
        assert app.valid_name(nom) == chemin, nom


def test_le_nom_garde_sa_casse_et_ses_espaces():
    assert app.titre_propre("  Mon   Site  ") == "Mon Site"
    assert app.titre_propre("SITE") == "SITE"
    assert app.titre_propre(None) == ""


def test_une_application_se_declare_sous_son_nom_et_se_sert_sous_son_chemin(hub_dagster, monkeypatch):
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/add", json={"name": "Mon Site", "path": "/w/x",
                                 "command": "python3 app.py"})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["name"] == "mon-site" and d["titre"] == "Mon Site"

    # La cle du registre EST le chemin : c'est elle que porte l'adresse.
    assert "mon-site" in app.load()
    ligne = [a for a in c.get("/api/apps").get_json()["apps"]
             if a["name"] == "mon-site"][0]
    assert ligne["titre"] == "Mon Site"


def test_un_nom_qui_ne_donne_aucun_chemin_est_refuse(hub_dagster, monkeypatch):
    """La validite depend du chemin OBTENU, et le refus le dit.

    Un nom fait de tirets seuls, ou de caracteres accentues seuls, se lit
    tres bien et ne laisse pourtant rien dont faire une adresse. Refuser sans
    montrer ce que le nom donnait dans l'adresse n'apprend rien.
    """
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/add", json={"name": "---", "path": "/w/x",
                                 "command": "python3 app.py"})
    assert r.status_code == 400
    assert "adresse" in r.get_json()["error"]


def test_deux_noms_qui_donnent_le_meme_chemin_se_refusent(hub_dagster, monkeypatch):
    """« Mon Site » et « mon site » se lisent differemment et se servent au
    meme endroit. Le second doit tomber, sur le chemin et non sur le nom."""
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    assert c.post("/api/add", json={"name": "Mon Site", "path": "/w/x",
                                    "command": "x"}).status_code == 200
    r = c.post("/api/add", json={"name": "mon site", "path": "/w/x",
                                "command": "x"})
    assert r.status_code == 400
    assert "/mon-site/" in r.get_json()["error"], r.get_json()


def test_changer_la_seule_casse_ne_deplace_rien(hub_dagster):
    """« site » renomme en « Site » garde le chemin /site/.

    Sans ce cas, le renommage echouait sur « une adresse deja prise » : la
    sienne. Et rien ne doit s'arreter -- il n'y a rien a deplacer.
    """
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    r = c.post("/api/app/site/renommer", json={"nom": "Site"})
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["nom"] == "site" and d["titre"] == "Site"
    assert d["redemarree"] is False
    assert app.load()["site"]["titre"] == "Site"


def test_la_page_affiche_le_nom_et_designe_par_la_cle():
    """Deux chaines, deux usages, et il ne faut pas les echanger.

    Afficher la cle, c'est montrer « mon-site » a qui a tape « Mon Site ».
    Designer par le nom, c'est fabriquer une adresse qui n'existe pas.
    """
    page = _page_panneau("dashboard.html")
    assert "const nomAffiche" in page
    # La tuile du hub et la ligne du tableau LISENT le nom.
    assert '<div class="nom">${esc(nomAffiche(a))}</div>' in page
    assert '<div class="n">${esc(nomAffiche(a))}</div>' in page
    # Et tout ce qui DESIGNE garde la cle.
    assert 'data-cible="${esc(a.name)}"' in page


# ---------- 30 ter. la tuile d'un service ressemble aux autres ----------

def test_aucune_tuile_ne_porte_de_cadre_a_part():
    """Demande, en deux temps.

    D'abord : « L'app Dagster ne doit pas avoir ce contour blanc. Seul le
    logo doit avoir un fond comme les autres applications. » Dagster portait
    un fond ET une bordure au niveau de la TUILE, pour se detacher du hub --
    une vignette cerclee de blanc au milieu de projets qui n'en ont pas.

    Puis, capture a l'appui : « Retire le fond derriere dagster. Je veux
    juste un contour autour du logo qui fait la meme taille que l'icone de
    diagnostic. » Le fond plein pose sous l'image refaisait la meme vignette
    en plus petit. Ce qu'il fallait n'etait pas une plaque mais un CONTOUR.

    Puis : « reduis la taille du logo pour que le rendu soit plus
    esthetique. » Le logo touchait son cadre.

    Enfin : « ajoute un fond derriere dagster quand meme. »

    Le fond revient donc, et ce n'est pas un aller-retour pour rien : ce qui
    le rendait laid les deux premieres fois, ce n'etait pas le fond, c'etait
    son ABSENCE DE MARGE. Sur la tuile, la plaque debordait l'icone et le nom
    tombait en dehors ; sous l'image sans marge, le logo touchait les bords.
    Avec la marge, les trois ensemble donnent une plaque de la taille exacte
    des autres icones et un logo centre -- la forme d'une icone
    d'application.

    La regle vise l'ICONE et pas la tuile : ce qui distingue Dagster n'est pas
    d'etre un service, c'est que son logo est TRANSPARENT la ou les projets
    deposent une image pleine. Habiller la tuile traitait la mauvaise
    difference -- c'etait la vignette de depart.
    """
    page = _page_panneau("dashboard.html")

    # LA TUILE elle-meme n'a rien de particulier : c'est la vignette cerclee
    # de blanc qu'on a retiree, et c'est elle qui ne doit pas revenir.
    debut = page.index(".hub-liste.compacte .hub-projet{")
    tuile = page[debut:page.index("}", debut)]
    assert "background:none" in tuile, tuile
    assert "border-color:transparent" in tuile, tuile
    for regle_de_service in (".hub-liste.compacte .hub-projet.service-stack{",
                             ".hub-projet.service-stack{"):
        assert regle_de_service not in page, (
            f"{regle_de_service} habille la tuile, pas l'icone")

    # L'ICONE d'un service, elle, a son cadre : son logo est transparent, et
    # sans cadre il flotte sans forme au milieu d'icones pleines.
    debut = page.index(".hub-liste.compacte .hub-projet.service-stack img{")
    icone = page[debut:page.index("}", debut)]
    assert "border:1px solid var(--line)" in icone, icone
    # LES TROIS ENSEMBLE, et c'est la MARGE qui fait la difference. Un fond
    # sans marge donne un carre gris derriere un dessin qui touche les bords ;
    # avec la marge, c'est une plaque d'icone et un logo centre.
    # Un jeton A PART, et pas --surface : en theme sombre celui-ci vaut
    # #141b23 sur un fond #0d1218, soit 1,08:1 -- la plaque y etait
    # invisible. Voir --plaque-logo dans theme.css.
    assert "background:var(--plaque-logo)" in icone, icone
    assert "padding:" in icone, icone

    # Les 88 px sont la mesure commune. box-sizing:border-box fait tenir
    # bordure ET marge DEDANS : sans lui, la tuile du service serait plus
    # grande que celle du diagnostic, ce qu'on cherche justement a eviter.
    debut = page.index(".hub-liste.compacte .hub-projet img{")
    commune = page[debut:page.index("}", debut)]
    assert "width:88px;height:88px" in commune, commune
    assert "object-fit:contain" in commune, commune
    # "border:" et non "border" : border-radius est legitime ici, c'est le
    # coin arrondi commun a toutes les icones.
    assert "border:" not in commune, commune
    assert "box-sizing:border-box" in page


# ---------- 31. une page d'erreur n'affiche pas ce qu'on lui envoie ----------

def test_la_page_d_erreur_echappe_ce_qu_elle_affiche(client):
    """Le nom vient de l'URL, et ressortait tel quel.

    « /zz<script>alert(1)</script>/ » sur l'origine des applications rendait
    une page portant la balise. Elle n'y porte pas la session du panneau --
    c'est tout l'interet de la separation des origines -- mais elle permettait
    de se faire passer pour n'importe quelle application hebergee, dans son
    origine, devant l'utilisateur qui vient de cliquer.
    """
    r = client.get("/zz<script>alert(1)</script>/")
    assert b"<script>alert" not in r.data
    assert b"&lt;script&gt;" in r.data
    # La page reste lisible : on echappe, on ne tronque pas.
    assert b"Aucune application" in r.data


def test_la_vue_d_ensemble_ne_double_plus_les_messages():
    """Deux endroits ou lire la meme chose, c'est deux endroits ou croire les
    avoir traites. Les messages ont leur rubrique, avec son compteur."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    ensemble = page.split('<div id="sec-overview"')[1].split('<div id="sec-apps"')[0]
    assert "zone-messages" not in ensemble
    assert "msg-liste" not in ensemble
    # La rubrique, elle, est toujours la.
    assert 'id="sec-messages"' in page and 'id="side-count-messages"' in page


def test_l_onglet_messages_ferme_le_menu():
    """On ne choisit pas d'aller lire ses messages en arrivant : on y va quand
    le compteur le dit. La place suit l'usage."""
    page = open(os.path.join(DOSSIER_PANNEAU, "app", "dashboard.html"),
                encoding="utf-8").read()
    menu = page.split('<nav class="sidebar"')[1].split("</nav>")[0]
    rubriques = re.findall(r'data-sec="([a-z]+)"', menu)
    assert rubriques[-1] == "messages", rubriques
    assert rubriques[0] == "hub", rubriques


def test_le_diagnostic_porte_sa_marque_partout(hub_dagster):
    """Le meme dessin dans les trois endroits ou on le voit.

    La tuile du hub (servie par le panneau), la barre du haut de la page, et
    l'icone de l'onglet. Les deux services ne peuvent pas partager de code --
    le diagnostic tourne dans un autre processus, sans acces au panneau --
    mais ils partagent la FORME, et c'est ce qui doit rester vrai.
    """
    import app as diag
    # Un seul trace dans la page, utilise deux fois.
    assert diag.MARQUE_TRACE in diag._barre(""), "la barre a perdu la marque"
    assert diag.MARQUE_TRACE.replace(" ", "%20") in diag.FAVICON, (
        "l'icone de l'onglet ne suit plus la marque")
    page = diag.app.test_client().get("/").data.decode()
    assert 'rel="icon"' in page

    # Et le panneau sert bien une icone a la tuile, distincte de la lettre.
    hub_dagster.post("/login", json={"password": "secret-de-test"})
    r = hub_dagster.get("/api/icon/diagnostic")
    assert r.status_code == 200 and b"<text" not in r.data
    # Le meme geste : un trace ouvert, pas une lettre ni un cercle.
    assert b"<path" in r.data and b"stroke" in r.data


def test_les_sondes_regardent_au_dela_de_la_stack():
    """Trois familles ajoutees, toutes en LECTURE SEULE.

    Les sondes d'avant s'arretaient a "le service repond". Celles-ci
    regardent ce que la stack porte (les applications une par une), ce
    qu'elle use (le disque, les journaux) et ce qu'elle laisse ouvert.
    """
    assert callable(check_applications)
    assert callable(check_espace_disque)
    assert callable(check_surface_exposee)
    for fn in (check_applications, check_espace_disque, check_surface_exposee):
        ok, nom, detail = fn()
        assert isinstance(ok, Etat) and nom and detail


def test_l_alerte_disque_demande_les_deux_conditions(tmp_path, monkeypatch):
    """Un pourcentage seul se trompe dans les deux sens.

    89 % d'un disque de 250 Go laisse 28 Go -- des mois de marge, et la sonde
    crierait pour rien. C'est le defaut qu'avait la premiere version, vu sur
    la machine de developpement.
    """
    assert SEUIL_DISQUE == 85 and SEUIL_LIBRE_GO == 5
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "checks.py"),
               encoding="utf-8").read()
    # On lit le CORPS de la sonde, pas le fichier : sinon l'assertion se
    # trouve elle-meme -- sa propre chaine est dans le fichier -- et la
    # verification passe quelle que soit la sonde. Defaut vu en mutant : la
    # mutation survivait.
    corps = src[src.index("def check_espace_disque("):]
    corps = corps[:corps.index("\ndef ", 10)]
    assert "libre < SEUIL_LIBRE_GO" in corps, (
        "l'alerte disque ne demande plus les deux conditions")


def test_la_sonde_des_applications_nomme_ce_qui_cloche(tmp_path, monkeypatch):
    """Elle ne dit pas "il y a un probleme" : elle dit lequel, et ou."""
    etat = tmp_path / "etat"
    etat.mkdir()
    (etat / "apps.json").write_text(json.dumps({
        "disparue": {"path": "/n-existe-pas", "command": "python3 a.py",
                     "port": 9201, "enabled": False},
        "muette": {"path": str(tmp_path), "command": "python3 -V",
                   "port": 9204, "enabled": True},
    }))
    monkeypatch.setenv("APP_MANAGER_STATE", str(etat))
    ok, _, detail = check_applications()
    # Rang 2 : une application tombee n'est pas la stack tombee.
    assert ok is Etat.ALERTE
    assert "disparue" in detail and "dossier introuvable" in detail
    assert "muette" in detail and "9204" in detail


# ---------- 24. le dossier personnel ne se detourne pas ----------
#
# Trouve par l'audit de la branche, et c'etait une escalade de privileges
# introduite par la separation des uid elle-meme. CHILD_HOME etait ecrivable
# par le groupe partage : une application pouvait effacer son propre dossier,
# le remplacer par un lien symbolique vers n'importe quel dossier de la
# machine, et attendre. Au redemarrage, le chown pose par root suivait le
# lien -- le dossier des secrets devenait sa propriete, et il ne restait
# qu'a remplacer credentials.env.

@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_ne_detourne_pas_le_chown_de_root(tmp_path, monkeypatch):
    """Le scenario complet, joue tel quel : lien pose par l'application,
    ensure_child_home rappele par root, et la cible qui ne doit PAS changer
    de proprietaire."""
    import subprocess as sp
    for parent in list(tmp_path.parents)[:3] + [tmp_path]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o011)
        except OSError:
            pass
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    convoite = tmp_path / "secrets"
    convoite.mkdir(mode=0o700)
    (convoite / "credentials.env").write_text("POSTGRES_PASSWORD=secret\n")
    avant = os.stat(convoite).st_uid

    maison = app.ensure_child_home("alpha")
    # Le parent n'est pas ecrivable par le groupe : c'est ce qui bloque tout.
    assert os.stat(app.CHILD_HOME).st_mode & 0o020 == 0, \
        "CHILD_HOME est ecrivable par le groupe : une application peut y " \
        "remplacer un dossier par un lien"

    sp.run(["sh", "-c", f"rmdir '{maison}'; ln -s '{convoite}' '{maison}'"],
           capture_output=True, preexec_fn=app.child_setup(nom="alpha"))
    app.ensure_child_home("alpha")
    assert os.stat(convoite).st_uid == avant, \
        "root a suivi un lien pose par l'application et lui a donne la cible"


@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_n_ecrase_pas_le_dossier_d_une_autre(tmp_path, monkeypatch):
    """Meme cause, autre effet : deposer un .profile chez la voisine, que
    "bash -lc" execute sous SON uid -- l'isolation par uid annulee par le
    dossier personnel."""
    import subprocess as sp
    for parent in list(tmp_path.parents)[:3] + [tmp_path]:
        try:
            os.chmod(parent, os.stat(parent).st_mode | 0o011)
        except OSError:
            pass
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    app.ensure_child_home("alpha")
    chez_beta = app.ensure_child_home("beta")

    sp.run(["sh", "-c",
            f"rm -rf '{chez_beta}' && mkdir -m 0777 '{chez_beta}' && "
            f"echo charge > '{chez_beta}/.profile'"],
           capture_output=True, preexec_fn=app.child_setup(nom="alpha"))
    assert not os.path.exists(os.path.join(chez_beta, ".profile"))
    assert os.stat(chez_beta).st_uid == app.uid_application("beta")


@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_entree_hostile_deja_en_place_est_retiree(tmp_path, monkeypatch):
    """Une installation mise a jour peut deja porter un lien pose du temps ou
    c'etait possible. Il ne doit pas etre suivi, mais enleve."""
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    os.makedirs(app.CHILD_HOME, exist_ok=True)
    convoite = tmp_path / "secrets"
    convoite.mkdir()
    avant = os.stat(convoite).st_uid
    os.symlink(str(convoite),
               os.path.join(app.CHILD_HOME, str(app.uid_application("alpha"))))

    maison = app.ensure_child_home("alpha")
    assert not os.path.islink(maison), "le lien est toujours la"
    assert os.path.isdir(maison)
    assert os.stat(convoite).st_uid == avant, "la cible du lien a ete chownee"


def test_l_application_travaille_quand_meme_chez_elle(tmp_path, monkeypatch):
    """Une correction qui rend le dossier personnel inutilisable ne vaut
    rien : c'est la que vit le cache npm, d'un build a l'autre."""
    monkeypatch.setattr(app, "CHILD_HOME", str(tmp_path / "home"))
    maison = app.ensure_child_home("alpha")
    assert os.path.isdir(maison)
    # Le groupe traverse le parent, sinon l'application n'atteint pas son
    # propre dossier.
    assert os.stat(app.CHILD_HOME).st_mode & 0o010, \
        "le groupe ne peut plus traverser CHILD_HOME"
    # Et deux appels de suite ne se marchent pas dessus.
    assert app.ensure_child_home("alpha") == maison


# ---------- 25. une origine a part pour les applications ----------
#
# Tant que les applications etaient servies sous le port du panneau, elles
# vivaient dans SON origine : une XSS dans une application quelconque lisait
# la page du panneau, donc le jeton, donc pilotait la stack. Aucun jeton n'y
# pouvait rien -- le script hostile etait du bon cote de la barriere.
#
# Ce qui doit rester vrai : chaque port ne sert que ce qui lui appartient.

@pytest.fixture
def deux_origines(deux_espaces, monkeypatch):
    monkeypatch.setattr(app, "APPS_PORT", 9302)
    monkeypatch.setattr(app, "APPS_URL", "")
    monkeypatch.setitem(app._origines_separees, "actif", True)
    return deux_espaces


def _sur_port(port):
    """L'adresse de base qui fait arriver la requete sur ce port.

    base_url et pas environ_base : Werkzeug reconstruit SERVER_PORT et
    HTTP_HOST a partir de l'URL, et ecrase ce qu'on aurait pose a la main --
    les premiers tests ecrits ainsi mesuraient le port par defaut sans le
    dire.
    """
    return {"base_url": "http://serveur:%d" % port}


# ---------------- Dagster : ce qui l'empechait de rester debout ----------
#
# Vecu : « Dagster me met comme erreur 502 Bad Gateway ». Le proxy allait
# bien ; c'est codelab-dagster qui ne repondait plus. Deux causes tenaient
# ensemble, et les deux se verrouillent ici.

def _fichier_du_depot(*morceaux):
    """Un fichier du depot, ou None s'il n'est pas dans cette image.

    Les sondes tournent aussi dans le conteneur du diagnostic, qui ne
    contient pas le depot : un test qui suppose le contraire echouerait la
    ou il n'a rien a dire.
    """
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, *morceaux)
    return chemin if os.path.exists(chemin) else None


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
    assert "dagster.yaml.sums" in bloc
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
    monkeypatch.setattr(app.urllib.request, "urlopen", ne_repond_pas)

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


def test_l_adresse_declaree_prend_le_pas_sur_le_port(deux_origines, monkeypatch):
    """Derriere un reverse proxy, un second PORT n'est pas joignable de
    l'exterieur : on declare alors un sous-domaine."""
    monkeypatch.setattr(app, "APPS_URL", "https://apps.exemple.fr")
    c = deux_origines
    r = c.get("/public/", **_sur_port(9301))
    assert r.headers["Location"] == "https://apps.exemple.fr/public/"


# ---------- 26. isolation du systeme de fichiers ----------
#
# L'uid par application separait les process. Il ne separait pas les
# fichiers : /workspace est partage par le groupe codelab -- il le faut,
# sinon le code n'est plus modifiable en SSH -- donc une application lisait
# le .env de sa voisine. Chaque application recoit maintenant sa propre vue,
# dans laquelle /workspace ne contient qu'elle.

def test_la_commande_est_enveloppee_par_defaut(monkeypatch):
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
    # Verdict fixe : ce test porte sur le branchement, pas sur le
    # noyau de la machine qui lance la suite.
    app._isolement.update(verdict=True, raison="")
    argv, env = app.commande_isolee("facturier", "/workspace/facturier", "npm start", {})
    assert argv[0] == "unshare"
    # --user : c'est ce qui evite d'avoir besoin de CAP_SYS_ADMIN, donc de
    # defaire le cap_drop du compose.
    assert "--user" in argv and "--mount" in argv
    # Le chemin et la commande passent par l'environnement : interpoles dans
    # le script, un nom avec une apostrophe le casserait, et un chemin venu
    # d'ailleurs deviendrait une injection.
    assert env["CODELAB_PROJET"] == "/workspace/facturier"
    assert env["CODELAB_COMMANDE"] == "npm start"
    assert "npm start" not in " ".join(argv)


def test_sans_unshare_l_application_demarre_quand_meme(monkeypatch):
    """Une image reconstruite ailleurs, un noyau ou les namespaces
    utilisateur sont coupes : mieux vaut une application qui tourne sans
    isolation qu'une application qui ne tourne pas."""
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    monkeypatch.setattr(app.shutil, "which", lambda n: None)
    argv, env = app.commande_isolee("facturier", "/workspace/facturier", "npm start", {})
    assert argv == ["bash", "-lc", "npm start"]
    assert env == {}


def test_l_isolation_se_coupe_par_application_et_globalement(monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n)
    # Verdict fixe : ce test porte sur le branchement, pas sur le
    # noyau de la machine qui lance la suite.
    app._isolement.update(verdict=True, raison="")
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    apps = {"facturier": {"path": "/w/f", "isolation": False}}
    argv, _ = app.commande_isolee("facturier", "/w/f", "x", apps)
    assert argv[0] == "bash", "le reglage par application n'est pas lu"
    monkeypatch.setattr(app, "ISOLER_APPS", False)
    argv, _ = app.commande_isolee("facturier", "/w/f", "x", {})
    assert argv[0] == "bash", "le reglage global n'est pas lu"


def _ouvrir_traversee(chemins):
    for c in chemins:
        try:
            os.chmod(c, os.stat(c).st_mode | 0o011)
        except OSError:
            pass


@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_une_application_isolee_ne_voit_plus_sa_voisine(tmp_path, monkeypatch):
    """Le test qui compte : on lance vraiment la commande et on regarde ce
    qu'elle voit. Verifier la ligne de commande ne prouverait que la ligne
    de commande."""
    import subprocess as sp
    # Ce test-ci VEUT la vraie mesure : le filet pose un verdict de repli
    # pour que personne ne lance unshare par accident, on le leve ici.
    app._isolement.update(verdict=None, raison="")
    if not app.isolement_disponible():
        pytest.skip("l'isolement ne fonctionne pas sur cette machine : "
                    + (app._isolement["raison"] or "raison inconnue"))
    racine = tmp_path / "ws"
    (racine / "facturier").mkdir(parents=True)
    (racine / "cahier").mkdir()
    (racine / "cahier" / ".env").write_text("SECRET=xyz\n")
    _ouvrir_traversee(list(tmp_path.parents)[:3] + [tmp_path, racine])
    monkeypatch.setattr(app, "ROOT", str(racine))
    monkeypatch.setattr(app, "ISOLER_APPS", True)

    def voit(apps):
        cmd = ("ls %s; [ -r %s/cahier/.env ] && echo VOISINE_LISIBLE || echo VOISINE_INVISIBLE"
               % (racine, racine))
        argv, env_iso = app.commande_isolee("facturier", str(racine / "facturier"), cmd, apps)
        r = sp.run(argv, cwd=str(racine / "facturier"),
                   env=dict(os.environ, HOME="/tmp", **env_iso),
                   capture_output=True, text=True,
                   preexec_fn=app.child_setup(nom="facturier"))
        return r.stdout + r.stderr

    # Temoin : sans isolation la voisine est visible -- sinon ce test
    # passerait au vert pour la mauvaise raison.
    sortie = voit({"facturier": {"path": str(racine / "facturier"), "isolation": False}})
    assert "cahier" in sortie and "VOISINE_LISIBLE" in sortie, sortie

    sortie = voit({"facturier": {"path": str(racine / "facturier")}})
    assert "VOISINE_INVISIBLE" in sortie, sortie
    assert "cahier" not in sortie, "le projet voisin est encore visible : " + sortie
    assert "facturier" in sortie, "l'application ne voit plus son propre projet : " + sortie


@pytest.mark.skipif(os.geteuid() != 0, reason="demande root pour changer d'uid")
def test_ce_qu_une_application_isolee_ecrit_arrive_sur_le_disque(tmp_path, monkeypatch):
    """Une tmpfs posee au mauvais endroit ferait disparaitre le resultat de
    chaque build, en silence -- le pire defaut possible ici."""
    import subprocess as sp
    # Ce test-ci VEUT la vraie mesure : le filet pose un verdict de repli
    # pour que personne ne lance unshare par accident, on le leve ici.
    app._isolement.update(verdict=None, raison="")
    if not app.isolement_disponible():
        pytest.skip("l'isolement ne fonctionne pas sur cette machine : "
                    + (app._isolement["raison"] or "raison inconnue"))
    racine = tmp_path / "ws"
    (racine / "facturier").mkdir(parents=True)
    _ouvrir_traversee(list(tmp_path.parents)[:3] + [tmp_path, racine])
    os.chown(racine / "facturier", app.uid_application("facturier"), app.RUN_AS_GID)
    monkeypatch.setattr(app, "ROOT", str(racine))
    monkeypatch.setattr(app, "ISOLER_APPS", True)
    argv, env_iso = app.commande_isolee(
        "facturier", str(racine / "facturier"),
        "mkdir -p dist && echo resultat > dist/index.html", {})
    sp.run(argv, cwd=str(racine / "facturier"),
           env=dict(os.environ, HOME="/tmp", **env_iso),
           capture_output=True, text=True,
           preexec_fn=app.child_setup(nom="facturier"))
    produit = racine / "facturier" / "dist" / "index.html"
    assert produit.exists(), "le resultat du build a disparu avec le namespace"
    assert produit.read_text().strip() == "resultat"
    # Et il reste modifiable depuis une session SSH : c'est la raison d'etre
    # du groupe partage, l'isolation ne doit pas la casser.
    assert produit.stat().st_mode & 0o020, oct(produit.stat().st_mode)


# ---------- 27. le projet de diagnostic ne se supprime pas ----------
#
# C'est l'etat des lieux de l'installation : il dit si les services se
# parlent, si le panneau est ferme, si les applications sont isolees. Une
# stack sans lui n'a plus aucun moyen de se controler elle-meme -- et comme
# son inscription n'a lieu qu'UNE fois (marqueur), le supprimer le ferait
# disparaitre pour de bon, pas jusqu'au prochain redemarrage.

def test_le_diagnostic_ne_se_supprime_pas(deux_espaces):
    c = deux_espaces
    c.post("/login", json={"password": "secret-de-test"})
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

@pytest.fixture
def config_isolee(tmp_path, monkeypatch):
    """Un credentials.env a nous, et aucune variable heritee de la machine."""
    fichier = tmp_path / "credentials.env"
    fichier.write_text("POSTGRES_USER=codelab\n")
    monkeypatch.setattr(checks, "ENV_FILE", str(fichier))
    monkeypatch.setattr(checks, "PROJET_ENV", str(tmp_path / ".env"))
    for cle in CLES_MOT_DE_PASSE_PG:
        monkeypatch.delenv(cle, raising=False)
    return fichier


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

def _faux_disque(monkeypatch, pourcent, libre_go):
    total = 100 * 1024 ** 3
    libre = int(libre_go * 1024 ** 3)
    # Le pourcentage occupe se calcule sur (total - libre) : on choisit donc
    # le total pour que les deux chiffres soient coherents entre eux.
    total = int(libre / (1 - pourcent / 100))

    class _St:
        f_blocks = total // 4096
        f_bsize = 4096
        f_frsize = 4096
        f_bavail = libre // 4096

    monkeypatch.setattr(os, "statvfs", lambda _chemin: _St())


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


@pytest.fixture
def faux_panneau(monkeypatch):
    """Un panneau minuscule, sur un vrai port : la sonde fait de vraies
    requetes, c'est tout son interet."""
    import http.server
    import threading

    reponses = {"/health": 200}

    class _Poignee(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                          # noqa: N802
            code = reponses.get(self.path, 401)
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    serveur = http.server.HTTPServer(("127.0.0.1", 0), _Poignee)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    monkeypatch.setenv("CODELAB_PANNEAU_HOTE", "127.0.0.1")
    monkeypatch.setenv("MANAGER_PORT", str(serveur.server_address[1]))
    monkeypatch.setenv("APP_MANAGER_APPS_PORT", str(serveur.server_address[1]))
    try:
        yield reponses
    finally:
        serveur.shutdown()
        serveur.server_close()


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

class _JournalDeTest:
    def __init__(self):
        self.lignes = []

    def info(self, message):
        self.lignes.append(("info", message))

    def warning(self, message):
        self.lignes.append(("warning", message))

    def error(self, message):
        self.lignes.append(("error", message))


class _ContexteDeTest:
    def __init__(self):
        self.log = _JournalDeTest()
        self.metadonnees = {}

    def add_output_metadata(self, valeurs):
        self.metadonnees.update(valeurs)

    def niveaux(self, mot):
        return [n for n, m in self.log.lignes if mot in m]


@pytest.fixture
def asset_diagnostic(monkeypatch):
    """definitions.py charge avec un faux dagster, et sa base remplacee."""
    import types
    faux = types.ModuleType("dagster")

    def _decorateur(*_args, **_kwargs):
        return lambda fonction: fonction

    class _Statut:
        RUNNING = "RUNNING"

    class _Metadonnee:
        @staticmethod
        def md(texte):
            return texte

    faux.AssetExecutionContext = object
    faux.RunFailureSensorContext = object
    faux.Definitions = lambda **kw: kw
    faux.ScheduleDefinition = lambda **kw: kw
    faux.define_asset_job = lambda **kw: kw
    faux.DefaultScheduleStatus = _Statut
    faux.DefaultSensorStatus = _Statut
    faux.MetadataValue = _Metadonnee
    faux.asset = _decorateur
    faux.run_failure_sensor = _decorateur
    monkeypatch.setitem(sys.modules, "dagster", faux)

    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "definitions.py")
    spec = importlib.util.spec_from_file_location("codelab_definitions_test", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # La base : ecrite pour de bon nulle part, mais la chaine complete est
    # jouee -- connexion, ecriture, fermeture.
    ecrit = {}

    class _Connexion:
        def close(self):
            ecrit["fermee"] = True

    monkeypatch.setattr(checks, "connect_pg", lambda *a, **k: _Connexion())
    monkeypatch.setattr(checks, "write_heartbeat",
                        lambda _conn, source, detail: ecrit.setdefault("detail", detail) and 0 or 7)
    module.ecrit = ecrit
    return module


def _sondes(monkeypatch, resultats):
    monkeypatch.setattr(checks, "run_all", lambda *a, **k: resultats)


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


# ---------- 29. arreter une application ----------
#
# Signale par Lucas : « je ne peux pas arreter les applications », avec le
# message « L'application n'a pas demarre. (reponse 500) ».
#
# Deux defauts superposes, et le second cachait le premier. api_toggle ne
# disait jamais QUELLE action il avait tentee, et la page retombait donc sur
# un message de repli ecrit pour le demarrage : un arret rate s'annoncait
# « n'a pas demarre », ce qui envoie chercher a l'oppose de la panne. Et
# stop(), qui n'attrapait que ProcessLookupError, laissait filer toute autre
# erreur jusqu'a une page HTTP 500 sans JSON -- le motif reel ne sortait que
# dans les journaux du panneau, que l'interface ne montre pas.

def test_un_arret_qui_echoue_ne_parle_pas_de_demarrage(en_marche, monkeypatch):
    def _refuse(_nom):
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(app, "stop", _refuse)
    r = en_marche.post("/api/toggle/site")

    assert r.status_code == 500
    d = r.get_json()
    assert d is not None, "une page HTML 500 : la page n'y lira aucun motif"
    assert d["action"] == "arret"
    assert "arrêter" in d["error"], d["error"]
    assert "démarr" not in d["error"], "le message envoie chercher a l'oppose"
    # Et le motif reel est dans la reponse, pas seulement dans les journaux.
    assert "PermissionError" in d["error"]


def test_un_arret_refuse_rend_409_et_son_motif(en_marche, monkeypatch):
    monkeypatch.setattr(app, "stop", lambda _n: "Le processus ne s'arrete pas.")
    r = en_marche.post("/api/toggle/site")
    assert r.status_code == 409
    assert r.get_json()["action"] == "arret"
    assert "ne s'arrete pas" in r.get_json()["error"]


def test_un_arret_qui_marche_le_dit_aussi(en_marche, monkeypatch):
    vus = []
    monkeypatch.setattr(app, "stop", lambda n: vus.append(n))
    r = en_marche.post("/api/toggle/site")
    assert r.status_code == 200 and vus == ["site"]
    assert r.get_json() == {"ok": True, "action": "arret", "running": False}


def test_un_processus_qui_survit_n_est_pas_declare_arrete(survivants, monkeypatch):
    """Le cas le plus couteux : stop() repondait « tout va bien » sur un
    processus toujours vivant, le panneau affichait « Arretee », et
    l'application continuait de tenir son port."""
    class _Increvable:
        pid = 424242

        def poll(self):
            return None

    app.save({"site": {"path": str(survivants / "projet"), "command": "sleep 120",
                       "port": 9399, "enabled": True}})
    app.procs["site"] = _Increvable()
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", lambda *_a: None)     # signaux sans effet
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    erreur = app.stop("site")

    assert erreur and "424242" in erreur
    assert app.load()["site"]["enabled"] is True, (
        "le panneau a marque arretee une application toujours vivante")
    assert "site" in app.procs, "la seule trace qui permettait de la retrouver"
    app.procs.clear()


def test_un_signal_refuse_est_dit_et_non_leve(survivants, monkeypatch):
    class _Vivant:
        pid = 4242

        def poll(self):
            return None

    app.procs["site"] = _Vivant()

    def _refuse(*_a):
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", _refuse)
    erreur = app.stop("site")
    assert erreur and "Operation not permitted" in erreur
    app.procs.clear()


def test_un_arret_qui_marche_rend_none(survivants):
    """Temoin du contrat : None veut bien dire « c'est fait »."""
    assert app.start("site") is None
    assert app.stop("site") is None
    assert app.load()["site"]["enabled"] is False
    assert "site" not in app.lire_processus()


def test_le_bouton_ne_se_trompe_plus_de_sens():
    """La page doit dire le bon sens meme sur une reponse sans JSON.

    Le repli d'echec() est ecrit pour le demarrage : un arret rate
    s'annoncait « L'application n'a pas demarre (reponse 500) », ce qui
    envoie chercher exactement a l'oppose de la panne. La page connait
    l'etat qu'elle vient d'afficher -- c'est lui qui choisit la phrase quand
    le serveur ne dit rien du tout.
    """
    _, script = _script_panneau_html()
    bloc = script[script.index("async function tg(n){"):]
    bloc = bloc[:bloc.index("async function", 10)]
    assert "running===true" in bloc, (
        "la page ne regarde pas l'etat qu'elle vient d'afficher")
    assert "arrêtée" in bloc, "aucun message pour un arret qui echoue"
    assert "echec(r," in bloc, (
        "le message du serveur ne gagne plus sur le repli de la page")


# ---------- 30. renommer une application ----------
#
# Demande : « Il doit etre possible de changer de nom d'application en
# admin. » Il fallait jusqu'ici supprimer l'application et la redeclarer --
# donc perdre son journal, ses destinataires d'alerte et les comptes qu'on lui
# avait autorises.
#
# Ce qui rend l'operation delicate : le nom n'est pas une etiquette, c'est une
# CLE. Il designe l'application dans apps.json, dans son adresse, dans le nom
# de son journal, dans les projets autorises de chaque compte, dans ce que
# chacun a masque de son hub -- et il decide de l'uid sous lequel elle tourne.

@pytest.fixture
def a_renommer(tmp_path, monkeypatch):
    """Un panneau avec une application, un compte qui y a droit, un journal."""
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "MASQUEES_FILE", str(tmp_path / "masquees.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    os.makedirs(str(tmp_path / "logs"), exist_ok=True)
    projet = tmp_path / "projet"
    projet.mkdir()
    app._apps_cache["signature"] = None
    app.save({
        "site": {"path": str(projet), "command": "python3 app.py", "port": 9401,
                 "enabled": False, "description": "avant", "alertes": ["moi@example.com"]},
        "autre": {"path": str(projet), "command": "python3 b.py", "port": 9402,
                  "enabled": False},
    })
    app.ecrire_utilisateurs({"marie": {"sel": "x", "hash": "y", "projets": ["site", "autre"]}})
    app.ecrire_masquees({"marie": ["site"]})
    (tmp_path / "logs" / "site.log").write_text("une ligne de journal\n")
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c, tmp_path


def test_renommer_emporte_tout_ce_qui_designe_l_application(a_renommer):
    """La cle change, et avec elle tout ce qui la nommait."""
    c, tmp_path = a_renommer
    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["nom"] == "vitrine"

    apps = app.load()
    assert "vitrine" in apps and "site" not in apps
    # Ce qui accompagnait l'application la suit : sa description, ses
    # destinataires d'alerte, son port.
    assert apps["vitrine"]["description"] == "avant"
    assert apps["vitrine"]["alertes"] == ["moi@example.com"]
    assert apps["vitrine"]["port"] == 9401
    # Et sa place dans la liste : une application qui saute en fin de liste
    # parce qu'on l'a renommee se cherche.
    assert list(apps) == ["vitrine", "autre"]

    # Les droits d'un compte suivent, sinon marie perd l'acces sans que
    # personne ne le lui ait retire.
    assert app.lire_utilisateurs()["marie"]["projets"] == ["vitrine", "autre"]
    # Ce qu'elle avait masque de son hub aussi : sinon l'application
    # reapparait chez elle au premier rechargement.
    assert app.lire_masquees()["marie"] == ["vitrine"]
    # Le journal suit : c'est ce qu'on relit pour comprendre ce qui vient de
    # se passer.
    assert (tmp_path / "logs" / "vitrine.log").exists()
    assert not (tmp_path / "logs" / "site.log").exists()


def test_renommer_refuse_un_nom_deja_pris(a_renommer):
    c, _ = a_renommer
    r = c.post("/api/app/site/renommer", json={"nom": "autre"})
    assert r.status_code == 400
    assert "déjà" in r.get_json()["error"]
    assert "site" in app.load(), "l'application a disparu sur un refus"


def test_renommer_refuse_un_nom_reserve(a_renommer):
    c, _ = a_renommer
    for reserve in ("api", "health", "login"):
        r = c.post("/api/app/site/renommer", json={"nom": reserve})
        assert r.status_code == 400, reserve


def test_le_diagnostic_ne_se_renomme_pas(a_renommer):
    """Meme raison que pour la suppression : le panneau le retrouve par son
    nom, et le marqueur qui evite une seconde inscription porte dessus."""
    c, _ = a_renommer
    apps = app.load()
    apps[app.DIAGNOSTIC_NOM] = dict(apps["site"])
    app.save(apps)
    r = c.post("/api/app/" + app.DIAGNOSTIC_NOM + "/renommer", json={"nom": "controle"})
    assert r.status_code == 403
    assert app.DIAGNOSTIC_NOM in app.load()


def test_renommer_une_application_qui_tourne_l_arrete_et_la_relance(a_renommer, monkeypatch):
    """Le processus porte l'ancien nom dans son environnement (CODELAB_APP) :
    c'est ce qui permet de le reconnaitre apres un redemarrage du panneau. Le
    laisser tourner sous l'ancien nom reviendrait a le perdre de vue."""
    c, _ = a_renommer
    faits = []
    monkeypatch.setattr(app, "is_running", lambda n: n == "site")
    monkeypatch.setattr(app, "stop", lambda n: faits.append(("stop", n)))
    monkeypatch.setattr(app, "start", lambda n: faits.append(("start", n)))
    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 200
    assert faits == [("stop", "site"), ("start", "vitrine")]
    assert r.get_json()["redemarree"] is True


def test_un_arret_impossible_annule_le_renommage(a_renommer, monkeypatch):
    """On ne renomme pas une application qu'on n'a pas su arreter : la cle
    changerait pendant qu'un processus tourne toujours sous l'ancienne."""
    c, _ = a_renommer
    monkeypatch.setattr(app, "is_running", lambda n: n == "site")
    monkeypatch.setattr(app, "stop", lambda n: "le processus ne s'arrête pas")
    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 409
    assert "site" in app.load() and "vitrine" not in app.load()


def test_le_dossier_est_rendu_au_nouvel_identifiant(a_renommer, monkeypatch):
    """L'uid d'une application est DERIVE de son nom : la renommer lui en
    donne un autre, et tout ce qu'elle avait ecrit deviendrait illisible pour
    elle. Un projet qui ne peut plus relire sa propre base apres un simple
    changement de nom serait un piege."""
    c, tmp_path = a_renommer
    donnees = tmp_path / "projet" / "donnees.sqlite"
    donnees.write_text("x")
    ancien, nouveau = app.uid_application("site"), app.uid_application("vitrine")
    assert ancien != nouveau, "les deux noms tombent sur le meme uid, test sans objet"

    vus = []
    monkeypatch.setattr(os, "lstat", lambda chemin: type(
        "S", (), {"st_uid": ancien, "st_gid": 2000})())
    monkeypatch.setattr(os, "lchown", lambda chemin, uid, gid: vus.append((chemin, uid)))
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    r = c.post("/api/app/site/renommer", json={"nom": "vitrine"})
    assert r.status_code == 200
    assert r.get_json()["fichiers_repris"] >= 1
    assert all(uid == nouveau for _chemin, uid in vus), vus
    assert any(chemin.endswith("donnees.sqlite") for chemin, _uid in vus)


def test_la_page_offre_le_renommage(a_renommer):
    """Le champ existe vraiment, et il est fige pour le diagnostic."""
    page = _page_panneau("dashboard.html")
    assert 'id="e-nom"' in page and 'onclick="renommerApp()"' in page
    bloc = page.split("async function renommerApp(){")[1].split("\n}")[0]
    assert "/renommer" in bloc
    # Apres coup, la fiche doit suivre le nouveau nom : sinon elle interroge
    # une application qui n'existe plus.
    assert "ficheNom = d.nom" in bloc
    assert "await refresh()" in bloc


# ---------- 31. suspendre un compte ----------
#
# Demande : « Ajoute la fonctionnalite suspendre l'utilisateur. » Il n'y avait
# que supprimer -- et supprimer efface les projets autorises, l'adresse et les
# cles d'acces, qu'il faut tout ressaisir au retour. Un depart, un doute, un
# appareil perdu : on veut fermer l'acces TOUT DE SUITE et decider plus tard.
#
# Ce qui compte vraiment ici : la suspension doit valoir sur-le-champ. Une
# suspension qui n'agit qu'a la prochaine connexion ne ferme rien -- celui
# dont on vient de retirer l'acces garde sa session ouverte, c'est-a-dire en
# pratique indefiniment.

@pytest.fixture
def deux_comptes(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "PASSKEYS_FILE", str(tmp_path / "passkeys.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "under_root", lambda p: True)
    app._apps_cache["signature"] = None
    app.save({"prive": {"path": "/w/p", "command": "x", "port": 9501,
                        "visibility": "privee"}})
    sel = "abcdef"
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("un-mot-de-passe", sel),
        "projets": ["prive"], "totp": "JBSWY3DPEHPK3PXP"}})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()


def _admin(client):
    client.post("/login", json={"password": "secret-de-test"})
    return client


def test_suspendre_garde_le_compte_et_ses_droits(deux_comptes):
    """C'est toute la difference avec supprimer : la porte se ferme, la piece
    reste en l'etat."""
    c = _admin(deux_comptes)
    r = c.put("/api/utilisateurs/marie", json={"suspendu": True})
    assert r.status_code == 200, r.get_json()
    compte = app.lire_utilisateurs()["marie"]
    assert compte.get("suspendu") is True
    # Rien d'autre n'a bouge.
    assert compte["projets"] == ["prive"]
    assert compte.get("totp")
    # Et la liste le dit, sans qu'il faille ouvrir la fiche.
    vue = c.get("/api/utilisateurs").get_json()["utilisateurs"][0]
    assert vue["suspendu"] is True and vue["projets"] == ["prive"]


def test_reactiver_rend_l_acces_sans_rien_ressaisir(deux_comptes):
    c = _admin(deux_comptes)
    c.put("/api/utilisateurs/marie", json={"suspendu": True})
    c.put("/api/utilisateurs/marie", json={"suspendu": False})
    compte = app.lire_utilisateurs()["marie"]
    assert "suspendu" not in compte
    assert compte["projets"] == ["prive"], "les droits n'ont pas survecu"


def test_un_compte_suspendu_ne_se_connecte_plus(deux_comptes):
    """Le mot de passe est bon : le refus doit donc DIRE pourquoi, sinon on
    cherche une panne la ou il y a une decision."""
    c = _admin(deux_comptes)
    c.put("/api/utilisateurs/marie", json={"suspendu": True})
    c.post("/logout")
    r = c.post("/login", json={"nom": "marie", "password": "un-mot-de-passe"})
    assert r.status_code == 403
    assert "suspendu" in r.get_json()["error"].lower()


def test_la_session_ouverte_d_un_compte_suspendu_tombe(deux_comptes, monkeypatch):
    """LE test de cette fonction. Suspendre pendant qu'une session tourne
    doit la fermer a la requete suivante : sinon la suspension n'est qu'une
    promesse pour la prochaine connexion."""
    c = _admin(deux_comptes)
    # On ouvre une session utilisateur a la main : le second facteur n'a pas
    # sa place dans ce test-ci, il est verifie ailleurs.
    with c.session_transaction() as sess:
        sess["authed"] = True
        sess["role"] = app.ROLE_UTILISATEUR
        sess["utilisateur"] = "marie"
        sess["ouverte"] = int(time.time())
        sess["jeton"] = "j"
    assert c.get("/api/mon-compte").status_code == 200

    comptes = app.lire_utilisateurs()
    comptes["marie"]["suspendu"] = True
    app.ecrire_utilisateurs(comptes)

    # La requete suivante ne doit plus rien autoriser.
    assert c.get("/api/mon-compte").status_code == 401


def test_un_compte_suspendu_n_ouvre_plus_ses_projets(deux_comptes):
    """Les projets restent coches dans sa fiche -- c'est voulu -- mais ils ne
    s'ouvrent plus tant que le compte est suspendu."""
    c = _admin(deux_comptes)
    with app.flask_app.test_request_context("/"):
        from flask import session as s
        s["authed"] = True
        s["role"] = app.ROLE_UTILISATEUR
        s["utilisateur"] = "marie"
        assert app.projets_autorises() == {"prive"}
        comptes = app.lire_utilisateurs()
        comptes["marie"]["suspendu"] = True
        app.ecrire_utilisateurs(comptes)
        assert app.projets_autorises() == set()


def test_la_fenetre_du_compte_offre_les_deux_gestes():
    """Suspendre et supprimer cote a cote, et la difference expliquee : ce
    sont deux gestes qu'on confond tant qu'on ne l'a pas lue."""
    page = _page_panneau("dashboard.html")
    assert 'id="us-f-suspendre"' in page and 'onclick="usSuspendre()"' in page
    assert 'onclick="usSupprimer()"' in page
    bloc = page.split("async function usSuspendre(){")[1].split("\n}")[0]
    assert "suspendu: suspendre" in bloc, "le bouton n'envoie pas l'etat"
    assert "demander(" in bloc, "suspendre sans confirmation se clique par accident"


def test_la_fenetre_du_compte_ne_deroule_plus_tout():
    """Quatre rubriques deroulees, chacune precedee de son paragraphe : la
    fenetre depassait l'ecran et l'on faisait defiler pour trouver
    « Enregistrer »."""
    page = _page_panneau("dashboard.html")
    fenetre = page.split('<div class="ov" id="ov-user">')[1].split("</div></div>")[0]
    assert fenetre.count('class="repli"') >= 3, (
        "les explications sont de nouveau deroulees d'office")
    # Les deux champs d'identite tiennent sur une ligne.
    assert 'class="grid2"' in fenetre


# ---------- 32. emporter les fichiers de conteneurisation ----------
#
# « Sortir cette application de CodeLab » ne savait que DEPOSER les fichiers
# dans le dossier du projet -- c'est-a-dire supposer que la suite se passe
# ici. Or sortir une application, c'est l'emmener ailleurs : sur un poste,
# sur un autre serveur, dans un depot qui n'a rien a voir avec cette machine.
# Il fallait alors recopier quatre fichiers a la main depuis la page.

def test_l_archive_de_conteneurisation_s_emporte(tmp_path, monkeypatch):
    import io as flux
    import zipfile
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "under_root", lambda p: True)
    projet = tmp_path / "projet"
    projet.mkdir()
    (projet / "app.py").write_text("print('bonjour')\n")
    app._apps_cache["signature"] = None
    app.save({"site": {"path": str(projet), "command": "python3 app.py", "port": 9601}})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})

    r = c.get("/api/app/site/conteneur.zip")
    assert r.status_code == 200
    assert r.mimetype == "application/zip"
    assert "site-conteneur.zip" in r.headers["Content-Disposition"]

    archive = zipfile.ZipFile(flux.BytesIO(r.data))
    noms = archive.namelist()
    # UN DOSSIER, pas quatre fichiers en vrac : ouverte sur un bureau, elle
    # ne doit pas repandre un Dockerfile au milieu du reste.
    assert noms and all(n.startswith("site/") for n in noms), noms
    attendus = {f["nom"] for f in app.fichiers_conteneur(
        dict(app.load()["site"], name="site"))["fichiers"]}
    assert {n.split("/", 1)[1] for n in noms} == attendus
    # Et le contenu est bien celui que la page montre.
    for nom in noms:
        assert archive.read(nom).decode("utf-8").strip(), nom

    # Rien n'a ete ecrit dans le projet : telecharger n'est pas deposer.
    assert sorted(p.name for p in projet.iterdir()) == ["app.py"]


def test_le_bouton_de_telechargement_existe():
    page = _page_panneau("dashboard.html")
    assert 'onclick="ctTelecharger()"' in page
    bloc = page.split("function ctTelecharger(){")[1].split("\n}")[0]
    assert "/conteneur.zip" in bloc


def test_toutes_les_coches_du_panneau_choisissent_une_ligne():
    """« Pour tout CodeLab » : plus une seule case nue a viser.

    Les listes (applications du hub, comptes autorises, projets d'un compte)
    comme les reglages isoles (proxy, HTTPS, administration sur le reseau
    local) designent une LIGNE. La case reste dans le document -- elle porte
    l'etat et recoit le clavier -- simplement on ne la vise plus.
    """
    page = _page_panneau("dashboard.html")
    cases = re.findall(r'<label([^>]*)>\s*\n?\s*<input type="checkbox"', page)
    nues = [c for c in cases if "choix-ligne" not in c]
    assert not nues, f"cases a cocher hors d'une ligne a choisir : {nues}"
    # Et le style existe, dans les deux formes : une ligne de liste se teinte,
    # un reglage isole ne prend que la coche et l'accent sur son libelle.
    assert ".choix-ligne:has(>input:checked){background:var(--accent-soft)" in page
    assert ".field>.choix-ligne:has(>input:checked){background:none" in page


# ---------- 33. arreter demande une capability ----------
#
# Signale par Lucas, apres la premiere correction : « Le panneau n'a pas pu
# signaler le processus (pid 11) : [Errno 1] Operation not permitted ». Le
# message etait enfin juste -- c'etait bien un refus du noyau -- mais le
# bouton ne marchait toujours pas.
#
# LA CAUSE. Le panneau tourne en root, avec cap_drop: ALL et cinq capabilities
# rendues. KILL n'y etait pas. Or le noyau n'autorise un signal que si
# l'expediteur a CAP_KILL, ou si son uid est celui de la cible -- et depuis
# que chaque application tourne sous son propre uid, root n'a jamais l'uid
# d'aucune. Toutes les applications etaient donc inarretables, sur toute
# installation durcie.

def test_le_panneau_a_le_droit_d_arreter_ses_applications():
    """La capability manquante, dans les DEUX composes.

    C'est un test de configuration et pas de code, parce que le defaut etait
    la : aucune ligne de Python n'aurait pu arreter quoi que ce soit sans
    elle.
    """
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    fichiers = [os.path.join(racine, n)
                for n in ("docker-compose.yml", "docker-compose-casaos.yml")]
    presents = [f for f in fichiers if os.path.exists(f)]
    if not presents:
        pytest.skip("composes absents de cette image")
    assert len(presents) == 2, "un des deux composes manque"
    for chemin in presents:
        texte = open(chemin, encoding="utf-8").read()
        # Le bloc du service, et lui seul : « codelab-app-manager » parait
        # aussi dans le nom de l'image et dans container_name, et un split
        # naif tomberait au milieu d'une ligne.
        bloc = texte.split("\n  codelab-app-manager:\n")[1]
        bloc = bloc.split("cap_add:")[1].split("\n\n")[0]
        assert "- KILL" in bloc, (
            f"{os.path.basename(chemin)} : sans CAP_KILL, le panneau ne peut "
            f"arreter aucune application -- chacune tourne sous son propre uid")
        # Temoin : les autres capabilities n'ont pas disparu au passage.
        for indispensable in ("- SETUID", "- SETGID"):
            assert indispensable in bloc, chemin


def test_un_refus_du_noyau_repart_sous_l_uid_de_l_application(monkeypatch):
    """Le repli, pour les installations qui tournent encore sur l'ancien
    compose : le signal part d'un enfant qui a pris l'uid de la cible."""
    essais = []

    def _killpg(pgid, sig):
        essais.append(("killpg", pgid, sig))
        if len(essais) == 1:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", _killpg)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # Le fork est simule : on execute le corps de l'enfant dans le test, et
    # l'on verifie qu'il abandonne bien l'uid AVANT de signaler.
    bascules = []
    monkeypatch.setattr(os, "setgroups", lambda g: bascules.append(("groupes", tuple(g))))
    monkeypatch.setattr(os, "setgid", lambda g: bascules.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: bascules.append(("uid", u)))

    sorties = []
    def _fork():
        return 0            # on joue l'enfant
    def _exit(code):
        sorties.append(code)
        raise SystemExit(code)
    monkeypatch.setattr(os, "fork", _fork)
    monkeypatch.setattr(os, "_exit", _exit)

    with pytest.raises(SystemExit):
        app._signaler_groupe(4242, 15, 1234)

    assert sorties == [0], "l'enfant n'a pas signale puis rendu la main"
    assert bascules == [("groupes", (app.RUN_AS_GID,)), ("gid", app.RUN_AS_GID),
                        ("uid", 1234)]
    # Deux tentatives : la directe, refusee, puis celle de l'enfant.
    assert [e[0] for e in essais] == ["killpg", "killpg"]


def test_un_refus_qui_persiste_est_dit_avec_son_remede(survivants, monkeypatch):
    """Quand meme le repli echoue, le message doit nommer la capability : sans
    elle, on cherche du cote de l'application, ou rien n'y peut quoi que ce
    soit."""
    class _Vivant:
        pid = 11

        def poll(self):
            return None

    app.procs["site"] = _Vivant()
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)   # pas root : pas de repli
    def _refus(*_a):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "killpg", _refus)

    erreur = app.stop("site")
    assert erreur and "KILL" in erreur, erreur
    assert "cap_add" in erreur
    assert "11" in erreur
    app.procs.clear()


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

@pytest.fixture
def hub_dagster(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    monkeypatch.setattr(app, "UTILISATEURS_FILE", str(tmp_path / "utilisateurs.json"))
    monkeypatch.setattr(app, "MASQUEES_FILE", str(tmp_path / "masquees.json"))
    monkeypatch.setattr(app, "CATEGORIES_FILE", str(tmp_path / "categories.json"))
    monkeypatch.setattr(app, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(app, "is_running", lambda n: False)
    # Le port de Dagster est sonde pour de vrai : on repond a sa place.
    monkeypatch.setattr(app, "dagster_repond", lambda: True)
    app._apps_cache["signature"] = None
    app.save({"site": {"path": "/w/s", "command": "x", "port": 9701,
                       "visibility": "privee"}})
    sel = "a1b2c3d4"      # le sel se lit en hexadecimal, pas en toutes lettres
    app.ecrire_utilisateurs({"marie": {
        "sel": sel, "hash": app.derive_mot_de_passe("un-mot-de-passe", sel),
        "projets": ["site"], "totp": "JBSWY3DPEHPK3PXP"}})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    return app.flask_app.test_client()


def test_dagster_parait_dans_le_hub_et_mene_au_port_3000(hub_dagster):
    hub_dagster.post("/login", json={"password": "secret-de-test"})
    d = hub_dagster.get("/api/mes-apps").get_json()

    services = d["services"]
    # Deux services dans le hub : l'orchestrateur, et le courrier.
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
    bloc = page.split("const tuile = a =>")[1].split("`;")[0]
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
    hub_dagster.post("/login", json={"password": "secret-de-test"})
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


# ---------- 35. les messages ont leur rubrique ----------
#
# Demande : « Ajoute une rubrique pour les messages. La vue d'ensemble doit
# recenser les messages lies au hub par defaut, et je dois pouvoir filtrer sur
# une autre application. Ajoute la possibilite de supprimer ou retenir les
# messages, mettre un scroll lorsqu'il y en a trop. »
#
# Ils vivaient au bas de la vue d'ensemble, sous les graphiques : on ne les
# trouvait qu'en descendant, tous sujets meles, et rien ne permettait ni d'en
# ecarter un ni d'en garder un sous la main.

@pytest.fixture
def boite(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "MESSAGES_FILE", str(tmp_path / "messages.jsonl"))
    monkeypatch.setattr(app, "ACCES_FILE", str(tmp_path / "acces.jsonl"))
    monkeypatch.setattr(app, "APPS_FILE", str(tmp_path / "apps.json"))
    app._apps_cache["signature"] = None
    app.save({})
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})
    return c, tmp_path


def _message(c, cible, texte, app_nom="", qui="marie", quand=None, retenu=False):
    m = {"id": secrets.token_hex(12), "ts": quand or int(time.time()),
         "qui": qui, "cible": cible, "app": app_nom, "texte": texte}
    if retenu:
        m["retenu"] = True
    app.enregistrer_message(m)
    return m["id"]


def test_retenir_un_message_le_garde_en_tete(boite):
    """On retient ce qu'on veut traiter : le laisser descendre avec le temps,
    c'est le perdre."""
    c, _ = boite
    vieux = _message(c, "hub", "à traiter", quand=int(time.time()) - 3600)
    _message(c, "hub", "tout frais")

    assert c.put("/api/messages/" + vieux, json={"retenu": True}).status_code == 200
    liste = c.get("/api/messages").get_json()["messages"]
    assert liste[0]["id"] == vieux, "le message retenu n'est pas remonte"
    assert liste[0]["retenu"] is True

    # Et relacher le remet a sa place, par sa date.
    assert c.put("/api/messages/" + vieux, json={"retenu": False}).status_code == 200
    liste = c.get("/api/messages").get_json()["messages"]
    assert liste[0]["id"] != vieux
    assert "retenu" not in liste[0]


def test_supprimer_un_message_le_retire_du_fichier(boite):
    c, tmp_path = boite
    garde = _message(c, "hub", "on garde")
    parti = _message(c, "idee", "on jette")

    assert c.delete("/api/messages/" + parti).status_code == 200
    restants = [m["id"] for m in c.get("/api/messages").get_json()["messages"]]
    assert restants == [garde]
    # Dans la base aussi, pas seulement dans la reponse : un message qui
    # revient au redemarrage n'a pas ete supprime.
    ids = [l["id"] for l in app.stockage.lire(app.DB_FILE, "SELECT id FROM messages")]
    assert parti not in ids and garde in ids
    # Et c'est journalise : supprimer un mot qui vous est adresse se trace.
    genres = [e.get("genre") for e in app.lire_acces()]
    assert "message" in genres


def test_les_anciens_messages_sont_importes_puis_supprimables(boite):
    """Les messages d'avant la base (fichier courant ET sa rotation) entrent
    dans la table a l'import, et s'y suppriment comme les autres."""
    c, tmp_path = boite
    ancien = {"id": secrets.token_hex(12), "ts": int(time.time()) - 10000,
              "qui": "marie", "cible": "hub", "app": "", "texte": "d'avant la rotation"}
    with open(app.MESSAGES_FILE + ".1", "w") as f:
        f.write(json.dumps(ancien, ensure_ascii=False) + "\n")
    app.importer_anciens_fichiers()
    recent = _message(c, "hub", "récent")

    assert c.delete("/api/messages/" + ancien["id"]).status_code == 200
    restants = [m["id"] for m in c.get("/api/messages").get_json()["messages"]]
    assert restants == [recent]
    assert not os.path.exists(app.MESSAGES_FILE + ".1")

def test_le_guet_ne_redemande_que_ce_qui_est_arrive_depuis(boite):
    """La page interroge toutes les vingt secondes : lui renvoyer trois cents
    messages pour en decouvrir un serait payer cher une notification."""
    c, _ = boite
    repere = int(time.time()) - 100
    _message(c, "hub", "avant", quand=repere - 10)
    neuf = _message(c, "hub", "après", quand=repere + 10)

    liste = c.get("/api/messages?apres=%d" % repere).get_json()["messages"]
    assert [m["id"] for m in liste] == [neuf]
    # Un « apres » illisible ne fait pas tomber la route : elle rend tout.
    assert len(c.get("/api/messages?apres=demain").get_json()["messages"]) == 2


def test_les_messages_restent_reserves_a_l_administrateur(boite):
    c, _ = boite
    identifiant = _message(c, "hub", "un mot")
    anonyme = app.flask_app.test_client()
    assert anonyme.get("/api/messages").status_code in (401, 403)
    assert anonyme.delete("/api/messages/" + identifiant).status_code in (401, 403)
    assert anonyme.put("/api/messages/" + identifiant,
                       json={"retenu": True}).status_code in (401, 403)


def test_un_message_inconnu_ne_fait_pas_semblant(boite):
    c, _ = boite
    assert c.delete("/api/messages/jamais-vu").status_code == 404
    assert c.put("/api/messages/jamais-vu", json={"retenu": True}).status_code == 404


def test_la_rubrique_des_messages_filtre_et_defile():
    """La page : une rubrique a elle, le hub par defaut, un filtre par
    application, deux gestes et un defilement dans la carte."""
    page = _page_panneau("dashboard.html")
    assert 'id="sec-messages"' in page, "la rubrique n'existe pas"
    assert "showSection('messages')" in page, "rien n'y mene depuis le menu"

    # Le filtre s'ouvre sur le hub, et se complete des applications dont on
    # a effectivement parle.
    bloc = page.split('<select id="msg-filtre"')[1].split("</select>")[0]
    assert 'value="hub"' in bloc and "selected" not in bloc, bloc
    assert "peindreMessages()" in bloc
    remplir = page.split("function msgRemplirFiltre(){")[1].split("\n}")[0]
    assert "optgroup" in remplir and "app:" in remplir

    # Les deux gestes.
    assert "async function msgRetenir(" in page
    assert "async function msgSupprimer(" in page
    assert "demander(" in page.split("async function msgSupprimer(")[1].split("\n}")[0], (
        "supprimer sans confirmation se clique par accident")

    # Le defilement est DANS la carte : la barre de filtres doit rester sous
    # les yeux pendant qu'on descend la liste.
    style = page.split(".msg-boite{")[1].split("}")[0]
    assert "overflow:auto" in style and "max-height" in style


def test_un_message_recu_se_signale_dans_le_coin():
    """« Ajoute des notifications en bas a droite des qu'un message est
    recu. » La page guette, et le dit la ou elle dit tout le reste."""
    page = _page_panneau("dashboard.html")
    bloc = page.split("async function guetterMessages(){")[1].split("\n}")[0]
    assert "apres=" in bloc, "le guet retelecharge toute la boite"
    assert "notifier(" in bloc
    # surveiller() et non setInterval() : les minuteries s'arretent quand
    # l'onglet passe en arriere-plan.
    assert "surveiller(guetterMessages" in page
    # Et les notifications paraissent bien en bas a droite.
    style = page.split(".toasts{")[1].split("}")[0]
    assert "bottom" in style and "right" in style, style



# ---------- 24. regressions app-manager : ports / metriques / logo --------
#
# Ces tests sont volontairement des tests de source et de comportement isole :
# ils ne demarrent ni Docker ni une application utilisateur. Ils garantissent
# que les trois regressions vues en production restent couvertes.

def _racine_app_manager():
    """Le dossier qui contient app.py, quelle que soit la mise en page.

    Vecu a l'envers : cette fonction ne cherchait que app-manager/app.py. Le
    panneau vit dans app-manager/app/app.py depuis longtemps, donc aucun
    candidat ne correspondait, tous ces tests s'ignoraient en silence -- et la
    CI restait verte en ne verifiant plus rien. Les deux dispositions sont
    desormais essayees, et une image sans les sources est le SEUL cas qui
    justifie encore un saut.
    """
    bases = []
    configured = os.environ.get("CODELAB_REPO_ROOT")
    if configured:
        bases.append(Path(configured) / "app-manager")
    ici = Path(__file__).resolve()
    bases.extend((parent / "app-manager" for parent in ici.parents))
    for base in bases:
        for candidate in (base / "app", base):
            if (candidate / "app.py").is_file():
                return candidate
    pytest.skip("sources app-manager absentes de cette image")


def _charger_app_manager(monkeypatch, tmp_path):
    racine = _racine_app_manager()
    monkeypatch.setenv("APP_MANAGER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("APP_MANAGER_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("APP_MANAGER_SHARED_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("CODELAB_TESTS", "1")
    spec = importlib.util.spec_from_file_location(
        "codelab_app_manager_regression", racine / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_un_port_externe_est_detecte_des_l_attribution(monkeypatch, tmp_path):
    """Un port occupe par un autre service ne doit jamais etre propose."""
    m = _charger_app_manager(monkeypatch, tmp_path)

    class Conn:
        status = m.psutil.CONN_LISTEN
        pid = 4242
        laddr = type("Addr", (), {"port": 9102})()

    monkeypatch.setattr(m.psutil, "net_connections", lambda kind=None: [Conn()])
    assert m.next_port({}) == 9101
    assert m.next_port({"demo": {"port": 9101}}) == 9103


def test_un_processus_codelab_survivant_est_repris(monkeypatch, tmp_path):
    """Après redémarrage du panneau, un listener connu est adopté."""
    m = _charger_app_manager(monkeypatch, tmp_path)

    class Conn:
        status = m.psutil.CONN_LISTEN
        pid = 4242
        laddr = type("Addr", (), {"port": 9102})()

    monkeypatch.setattr(m.psutil, "net_connections", lambda kind=None: [Conn()])
    monkeypatch.setattr(m, "load", lambda: {
        "demo": {"port": 9102, "enabled": True}
    })
    monkeypatch.setattr(m, "_processus_est_le_notre", lambda pid, sauf=None: pid == 4242)
    monkeypatch.setattr(m, "_noter_processus", lambda *args: None)
    monkeypatch.setattr(m, "save", lambda apps: None)
    monkeypatch.setattr(m, "ProcessusAdopte", lambda pid, name: ("adopte", pid, name))
    monkeypatch.setattr(m, "is_running", lambda name: True)

    m.procs.clear()
    assert m.port_occupe_par(9102, sauf="demo") is None
    assert m.procs["demo"] == ("adopte", 4242, "demo")


def test_un_port_externe_explique_le_pid(monkeypatch, tmp_path):
    """Le message de panne donne enfin le processus qui bloque le port."""
    m = _charger_app_manager(monkeypatch, tmp_path)

    class Conn:
        status = m.psutil.CONN_LISTEN
        pid = 9876
        laddr = type("Addr", (), {"port": 9102})()

    monkeypatch.setattr(m.psutil, "net_connections", lambda kind=None: [Conn()])
    monkeypatch.setattr(m, "_description_processus",
                        lambda pid: ("node", "node server.js"))
    monkeypatch.setattr(m, "load", lambda: {})

    erreur = m.port_occupe_par(9102)
    assert "9102" in erreur
    assert "9876" in erreur
    assert "node server.js" in erreur


def test_ouverture_des_stats_force_une_premiere_mesure(monkeypatch, tmp_path):
    """La fiche metriques ne doit pas dependre du rafraichissement precedent.

    Ce test s'ignorait en silence tant que la recherche des sources visait
    app-manager/app.py. Il passe par un contexte de requete explicite plutot
    que par une session de test : la route est appelee directement, elle n'a
    pas besoin de cookie, et le panneau pose sa propre interface de session.
    """
    m = _charger_app_manager(monkeypatch, tmp_path)
    mesures = []

    class Proc:
        pid = 4242

    # La route est protegee par require_admin, applique a l'import : on ouvre
    # la porte par les trois predicats qu'il interroge, pas en remplacant le
    # decorateur -- trop tard, il a deja enveloppe la vue.
    monkeypatch.setattr(m, "is_authed", lambda: True)
    monkeypatch.setattr(m, "est_admin", lambda: True)
    monkeypatch.setattr(m, "refus_admin_hors_reseau", lambda: None)
    monkeypatch.setattr(m, "load", lambda: {"demo": {}})
    monkeypatch.setattr(m, "procs", {"demo": Proc()})
    monkeypatch.setattr(m, "is_running", lambda name: True)
    monkeypatch.setattr(m, "proc_stats",
                        lambda pid: {"cpu_percent": 12.5, "memory_mb": 64})
    monkeypatch.setattr(m, "record_metrics",
                        lambda name, cpu, mem: mesures.append((name, cpu, mem)))
    monkeypatch.setattr(m, "get_metrics_history", lambda name: mesures)

    with m.flask_app.test_request_context("/api/metrics/demo"):
        reponse = m.api_metrics("demo")
    # L'ouverture a elle seule doit avoir produit une mesure : sans elle, les
    # deux cartes affichaient « Aucune mesure » jusqu'au prochain sondage.
    assert mesures == [("demo", 12.5, 64)]
    assert reponse.json["points"][-1] == {"t": "demo", "cpu": 12.5, "mem": 64}


def test_le_logo_dagster_est_vectoriel_et_net():
    bloc = app.ICONE_DAGSTER
    assert '<svg' in bloc
    assert 'aria-label="Dagster"' in bloc
    # Les couleurs OFFICIELLES, celles du favicon de dagster-webserver.
    assert "#4F43DD" in bloc and "#352D8E" in bloc
    assert "#4C9AFF" not in bloc
    # Toujours pose sur la plaque blanche : pas de variante sombre.
    assert "prefers-color-scheme" not in bloc
    assert '<path' in bloc

# ---------- 25. CodeLab n'heberge pas d'interface de chat -----------------
#
# L'application maison « aichat » a ete retiree, et l'interface Open WebUI qui
# l'avait remplacee l'a ete a son tour : l'outil du developpeur est son
# editeur, et les applications appellent leurs services elles-memes. Deux
# choses doivent rester vraies : l'ancienne application disparait du registre
# sans perte de code, et le panneau cesse de travailler quand l'onglet n'est
# pas regarde.

def test_l_ancienne_application_aichat_quitte_le_registre(monkeypatch, tmp_path):
    """Le registre est nettoye, le code de l'utilisateur ne l'est pas.

    Supprimer /workspace/aichat serait une perte de donnees silencieuse : le
    dossier appartient a l'utilisateur. Seule l'inscription disparait, parce
    que la tuile pointait vers une application qui ne peut plus rien faire.
    """
    m = _charger_app_manager(monkeypatch, tmp_path)
    arretees = []
    enregistre = {}
    monkeypatch.setattr(m, "load", lambda: dict(enregistre))
    monkeypatch.setattr(m, "save", lambda apps: enregistre.clear() or enregistre.update(apps))
    monkeypatch.setattr(m, "stop", lambda nom: arretees.append(nom))

    enregistre.update({"aichat": {"port": 9100}, "facturier": {"port": 9101}})
    assert m.retirer_aichat() == "aichat"
    assert arretees == ["aichat"]
    assert sorted(enregistre) == ["facturier"]

    # Deuxieme passage : plus rien a faire, et rien d'arrete au passage.
    assert m.retirer_aichat() is None
    assert arretees == ["aichat"]

    source = (_racine_app_manager() / "app.py").read_text(encoding="utf-8")
    assert "shutil.rmtree" not in source.split("def retirer_aichat")[1].split("\ndef ")[0]


def test_le_panneau_cesse_de_sonder_quand_l_onglet_n_est_plus_regarde():
    """Quatre minuteries tournaient dans un onglet d'arriere-plan.

    Rafraichissement des applications, messages, metriques, hub : le panneau
    interrogeait la machine toutes les cinq secondes meme sans personne
    devant. Sur une ZimaBlade, cela se voit.
    """
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
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

def _entrypoint_postgres():
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("entrypoint postgres absent de cette image")
    return open(chemin, encoding="utf-8").read()


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
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    bloc = page.split("function barChart(")[1].split("\n}")[0]
    assert "!a.externe" in bloc, (
        "les graphiques CPU et mémoire doivent ecarter les services externes")


def _reseaux_du_compose(chemin):
    """Les reseaux de chaque service, lus sans PyYAML.

    Deux espaces par service, quatre pour « networks: », six pour chaque
    entree de la liste -- la meme indentation reguliere que partout ailleurs
    dans ces deux fichiers.
    """
    par_service, courant, dans_reseaux, dans_services = {}, None, False, False
    for ligne in open(chemin, encoding="utf-8"):
        if ligne[:1] not in (" ", "\n", "#", ""):
            dans_services = ligne.rstrip() == "services:"
            courant = None
            continue
        if not dans_services:
            continue
        if ligne.startswith("  ") and not ligne.startswith("   ") and ligne.rstrip().endswith(":"):
            courant, dans_reseaux = ligne.strip().rstrip(":"), False
            par_service.setdefault(courant, [])
        elif courant and ligne.startswith("    ") and not ligne.startswith("     "):
            dans_reseaux = ligne.strip() == "networks:"
        elif courant and dans_reseaux and ligne.strip().startswith("- "):
            par_service[courant].append(ligne.strip()[2:].strip())
    return par_service


# ---------- 36. ce que l'agent de developpement ne peut pas atteindre -----
#
# codelab-dev porte un agent qui execute des commandes sans qu'on relise
# chacune. Ce qui l'empeche de sortir de son perimetre ne tient PAS a ses
# consignes -- AGENTS.md est lu par un modele, pas applique par le noyau --
# mais a trois faits, verifies ici :
#
#   1. la session SSH tourne en « vscode », qui n'appartient a aucun groupe
#      d'administration ;
#   2. aucune regle sudoers ne lui est deposee, donc « sudo » reste installe
#      mais hors de portee depuis une session ;
#   3. credentials.env -- mot de passe d'administration du panneau, cle
#      privee SSO, mot de passe SMTP -- est monte en lecture seule et reste
#      en 0600 root, donc illisible par « vscode ».
#
# Ces trois faits tiennent aujourd'hui par ABSENCE : personne n'a ajoute la
# ligne qui les romprait. Un « usermod -aG sudo vscode » glisse un jour pour
# installer un paquet ouvrirait credentials.env a l'agent, sans que rien ne
# le signale. C'est exactement ce qu'un test doit retenir.

def _dockerfile_dev():
    chemin = os.path.join(_racine_depot(), "dev", "Dockerfile")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    return open(chemin, encoding="utf-8").read()


def _entrypoint_dev():
    chemin = os.path.join(_racine_depot(), "dev", "entrypoint.sh")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    return open(chemin, encoding="utf-8").read()


def test_l_utilisateur_des_sessions_n_est_dans_aucun_groupe_d_administration():
    """« vscode » appartient a « codelab », et a rien d'autre.

    Le groupe codelab donne l'ecriture partagee sur /workspace, et c'est tout
    ce qu'il donne. Tout autre groupe secondaire -- sudo, admin, wheel, root,
    docker -- rendrait la machine entiere accessible depuis une session, donc
    a l'agent qui y tourne.
    """
    source = _dockerfile_dev()
    groupes = re.findall(r"usermod\s+-aG\s+([A-Za-z0-9_,-]+)\s+vscode", source)
    assert groupes, "aucun usermod -aG pour vscode : le Dockerfile a change de forme"
    for liste in groupes:
        assert set(liste.split(",")) == {"codelab"}, (
            f"vscode ajoute au(x) groupe(s) {liste} : seul « codelab » est prevu")
    # Et pas par une autre porte : ni creation directe dans un groupe
    # d'administration, ni ajout par gpasswd/adduser.
    for interdit in ("-aG sudo", "-aG wheel", "-aG root", "-aG docker",
                     "gpasswd -a vscode", "adduser vscode sudo"):
        assert interdit not in source, f"« {interdit} » dans dev/Dockerfile"


def test_aucune_regle_sudoers_n_est_deposee_pour_les_sessions():
    """sudo est installe, et reste inutilisable depuis une session.

    L'image embarque sudo -- un environnement de developpement en a l'usage
    quand on entre en root par « docker exec ». Ce qui ne doit jamais
    arriver, c'est qu'une session SSH puisse s'en servir : ni regle dans
    /etc/sudoers.d, ni NOPASSWD, ni mot de passe pose sur le compte.
    """
    for source, nom in ((_dockerfile_dev(), "dev/Dockerfile"),
                        (_entrypoint_dev(), "dev/entrypoint.sh")):
        for interdit in ("NOPASSWD", "sudoers", "chpasswd", "passwd vscode"):
            assert interdit not in source, f"« {interdit} » dans {nom}"


def test_le_fichier_de_secrets_est_en_lecture_seule_pour_codelab_dev():
    """credentials.env n'est pas modifiable depuis le conteneur du shell.

    Trois services le montent en lecture-ecriture parce qu'ils y ecrivent
    leur propre bloc (postgres, app-manager, dagster-proxy). codelab-dev,
    lui, ne fait que lire le mot de passe Postgres : un montage accessible en
    ecriture lui permettrait de reecrire les secrets de la stack.
    """
    for chemin in _composes():
        services = _services_du_compose(chemin)
        assert "codelab-dev" in services, chemin
        texte = open(chemin, encoding="utf-8").read()
        bloc = texte.split("  codelab-dev:", 1)[1].split("\n  codelab-", 1)[0]
        montages = [l.strip()[2:] for l in bloc.splitlines()
                    if l.strip().startswith("- /DATA")]
        config = [m for m in montages if m.endswith(":/var/lib/codelab/config")
                  or m.endswith(":/var/lib/codelab/config:ro")]
        assert config, (chemin, montages)
        for m in config:
            assert m.endswith(":ro"), (
                f"{os.path.basename(chemin)} : codelab-dev monte le dossier de "
                f"secrets en ecriture ({m})")


def test_l_entrypoint_du_shell_ne_relache_pas_les_droits_du_fichier_de_secrets():
    """Personne ne rend credentials.env lisible « pour que ce soit plus simple ».

    Le fichier est ecrit en 0600 root par codelab-postgres. Un chmod ou un
    chown pose ici le rendrait lisible par vscode -- donc par l'agent -- et
    la lecture seule du montage n'y changerait rien.
    """
    source = _entrypoint_dev()
    for ligne in source.splitlines():
        nue = ligne.strip()
        if nue.startswith("#"):
            continue
        if ("chmod" in nue or "chown" in nue or "setfacl" in nue) and "credentials" in nue:
            raise AssertionError(f"dev/entrypoint.sh touche aux droits du fichier "
                                 f"de secrets : {nue}")


def test_le_gitconfig_pose_par_le_shell_ne_porte_ni_identite_ni_secret():
    """Un .gitconfig d'amorce, et rien de plus.

    Deux choses n'ont rien a faire dans un fichier ecrit par l'image :

      - une identite. Personne ici ne sait qui est l'utilisateur, et une
        valeur inventee signerait ses commits d'un faux nom. Sans user.name
        ni user.email, git refuse de commiter avec un message qui dit
        exactement quoi taper -- c'est le bon comportement.
      - un moyen de s'authentifier. Un jeton ou un credential helper pointant
        sur un fichier du workspace serait lisible par tout ce qui tourne en
        « vscode », dont l'agent, et par Dagster et app-manager qui montent le
        meme dossier. Les cles restent sur le poste du developpeur et sont
        pretees le temps d'une session (ssh -A).
    """
    source = _entrypoint_dev()
    debut = source.find('cat > "$GITCONFIG"')
    assert debut != -1, "le .gitconfig d'amorce a disparu de l'entrypoint"
    bloc = source[debut:source.find("GITCFG\nfi", debut)]
    for interdit in ("user.name", "user.email", "[user]", "credential",
                     "helper", "token", "PAT", "password"):
        assert interdit not in bloc, f"« {interdit} » dans le .gitconfig d'amorce"
    # Ecrit une seule fois : un reglage fait a la main depuis une session doit
    # survivre a une mise a jour d'image.
    assert '[ ! -f "$GITCONFIG" ]' in source, (
        "le .gitconfig est reecrit a chaque demarrage : les reglages de "
        "l'utilisateur seraient perdus")


def test_l_entrypoint_du_shell_ne_depose_aucun_jeton_d_acces():
    """Aucun secret d'authentification ecrit dans le workspace.

    Vaut pour git comme pour le reste : ce conteneur ne fabrique pas de
    credentials permanents, parce qu'un agent qui tourne sans personne devant
    les utiliserait sans qu'on l'ait voulu.
    """
    source = _entrypoint_dev()
    for ligne in source.splitlines():
        nue = ligne.strip()
        if nue.startswith("#") or not nue:
            continue
        minuscules = nue.lower()
        if any(motif in minuscules for motif in
               ("git_askpass", "credential.helper", "ghp_", "github_token",
                ".git-credentials")):
            raise AssertionError(f"dev/entrypoint.sh depose un moyen "
                                 f"d'authentification : {nue}")


def test_l_image_dev_ne_porte_que_codex():
    """OpenClaw, acpx et codex-acp sont retires : un gateway, des
    connecteurs de messagerie et une chaine de six etapes pour un chat que
    Codex fait seul, dans le terminal. Ils pesaient des centaines de Mo sur
    une machine dont le disque a deborde."""
    source = _dockerfile_dev()
    assert "npm install -g @openai/codex" in source
    for retire in ("openclaw", "acpx", "codex-acp"):
        assert retire not in source.lower(), f"{retire} revient dans l'image"
    assert "deb.nodesource.com/setup_24.x" in source, (
        "Node vient encore d'apt : trop ancien")
    assert "\n        nodejs \\" not in source, (
        "nodejs est encore installe depuis apt, en conflit avec NodeSource")


def test_la_configuration_des_agents_vit_dans_le_workspace():
    """Elle doit survivre a une recreation du conteneur.

    Vecu a l'envers pour Codex : range sous /home/vscode, son auth.json
    disparaissait a chaque mise a jour d'image et il fallait refaire
    « codex login ».
    """
    source = _entrypoint_dev()
    assert 'CODEX_HOME_DIR="${CODEX_HOME:-$WORKSPACE_DIR/.codex}"' in source


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

def _readme_racine():
    chemin = os.path.join(_racine_depot(), "README.md")
    if not os.path.exists(chemin):
        pytest.skip("depot complet absent de cette image")
    return open(chemin, encoding="utf-8").read()


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


RAM_CIBLE_MO = 4096
COEURS_CIBLE = 2


# 256 Mio : la reservation memoire que CasaOS/ZimaOS ajoute a CHAQUE
# service en recopiant le compose. Mesuree, pas choisie -- voir le test.
RESERVATION_HOTE_MO = 256


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
        "un test a ete ecrit dans checks.py : il va dans test_regressions.py")


def test_les_tests_importent_les_sondes_et_jamais_l_inverse():
    """Le sens de la dependance, verifie dans les deux fichiers.

    checks.py qui importerait la suite ramenerait pytest dans les images de
    production par la bande -- exactement ce que la separation evite.
    """
    sondes = open(checks.__file__, encoding="utf-8").read()
    assert "test_regressions" not in sondes.replace(
        "test_regressions.py", "", 1).replace("test_regressions.py", ""), (
        "checks.py reference la suite ailleurs que pour la lancer")
    suite = open(os.path.abspath(__file__), encoding="utf-8").read()
    assert "import checks" in suite


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
    assert "pytest workspace/diagnostic/test_regressions.py" in texte, (
        "la CI ne lance pas la suite au bon chemin")
    assert "pytest workspace/diagnostic/checks.py" not in texte


# ---------- 39. pgvector : la base vectorielle est la base ---------------
#
# Le besoin d'une base vectorielle se traite ici par une EXTENSION et non par
# un service de plus. Sur une machine de 4 Go, un conteneur supplementaire se
# paie ; une extension ne coute que sa taille sur disque. Et les vecteurs
# vivent a cote des donnees du projet : meme transaction, meme sauvegarde.

def _dockerfile_postgres():
    racine = os.path.dirname(DOSSIER_PANNEAU or "")
    chemin = os.path.join(racine, "postgres", "Dockerfile")
    if not os.path.exists(chemin):
        pytest.skip("Dockerfile postgres absent de cette image")
    return open(chemin, encoding="utf-8").read()


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


# ---------- 40. le nom du projet ne se devine pas ------------------------
#
# Sans "name:" en tete, Compose nomme le projet d'apres le DOSSIER qui
# contient le fichier. Trois chemins donnent trois noms : un clone donne
# « codelab », l'archive ZIP de GitHub donne « codelab-main », et une
# interface qui recopie le fichier dans un dossier a elle -- CasaOS le fait
# -- donne « compose-d4601441b33edb0f ».
#
# Ce nom prefixe les ressources de Compose et, sur CasaOS, donne son nom au
# DOSSIER de l'application. Le fixer, c'est le rendre identique partout.

def test_les_deux_composes_nomment_le_projet():
    """Le nom est ecrit, pas derive du chemin."""
    for chemin in _composes():
        texte = open(chemin, encoding="utf-8").read()
        noms = re.findall(r"^name:\s*(\S+)\s*$", texte, re.M)
        assert noms, (
            f"{os.path.basename(chemin)} ne declare pas « name: » : le nom du "
            f"projet dependra du dossier d'ou l'on demarre")
        assert noms[0] == "codelab", (
            f"{os.path.basename(chemin)} nomme le projet « {noms[0]} »")


def test_les_deux_composes_nomment_le_meme_projet():
    """Deux noms differents feraient deux installations qui s'ignorent.

    Les deux fichiers decrivent la MEME stack : demarrer avec l'un puis avec
    l'autre doit reprendre les memes conteneurs, pas en creer une seconde
    serie a cote.
    """
    noms = set()
    for chemin in _composes():
        texte = open(chemin, encoding="utf-8").read()
        trouve = re.findall(r"^name:\s*(\S+)\s*$", texte, re.M)
        if trouve:
            noms.add(trouve[0])
    assert len(noms) == 1, f"les composes nomment des projets differents : {sorted(noms)}"


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


def _plaques_du_theme():
    """La valeur de --plaque-logo dans chacun des blocs de theme.css.

    Trois blocs : clair, « prefers-color-scheme: dark », et le sombre choisi
    a la main. Les trois doivent etre peints, et un jeton oublie dans l'un
    d'eux ne se voit qu'en basculant le theme a la main -- ce qui n'arrive
    jamais au moment ou l'on ecrit la regle.
    """
    feuille = (_racine_app_manager() / "theme.css").read_text(encoding="utf-8")
    valeurs = re.findall(r"--plaque-logo:\s*(#[0-9a-fA-F]{3,6})\s*;", feuille)
    assert len(valeurs) == 3, (
        f"theme.css definit --plaque-logo {len(valeurs)} fois, trois attendues")
    return valeurs


def _luminance(hexa):
    """La luminance relative WCAG d'une couleur, pour dire « clair » sans
    se fier a l'impression qu'en donne le nom du jeton."""
    h = hexa.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    canaux = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    canaux = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in canaux]
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


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
    hub.post("/login", json={"password": "secret-de-test"})
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
        html = (_racine_app_manager() / page).read_text(encoding="utf-8")
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

    for adresse in ["/apple-touch-icon.png"] + [i["src"] for i in m["icons"]]:
        r = client.get(adresse)
        assert r.status_code == 200 and r.mimetype == "image/png"
        png = r.get_data()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        largeur, hauteur, _, type_couleur = struct.unpack(">IIBB", png[16:26])
        assert (largeur, hauteur) == (180, 180)
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
    source = (_racine_app_manager() / "app.py").read_text(encoding="utf-8")
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

def _admin_du_hub(c):
    c.post("/login", json={"password": "secret-de-test"})
    return c


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


# ------------------------- la liste des reglages -----------------------

def _bloc_liste_rangement():
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    debut = page.index("const lignes = toutes.map(a=>{")
    return page[debut:page.index("}).join('');", debut)]


def test_une_ligne_de_rangement_ne_porte_que_le_nom_et_la_case():
    """« Rends la liste plus legere. »

    Chaque ligne portait l'icone, le nom, la description complete et le mot
    « Affichée » ou « Masquée » : quatre informations pour un reglage qui en
    a une. La description est deja sur la tuile du hub, et l'etat est dans la
    case -- cochee veut dire affichee, c'est tout le propos.
    """
    bloc = _bloc_liste_rangement()
    assert "a.description" not in bloc, (
        "la description est revenue dans la ligne : deux hauteurs de ligne "
        "differentes selon que l'application en a une")
    assert "masquee-etat" not in bloc, (
        "le libelle « Affichée / Masquée » est revenu : il repete la case")
    assert "nomAffiche(a)" in bloc and "app-masquer" in bloc, (
        "une ligne doit toujours porter le nom et la case")


def test_la_liste_du_rangement_est_batie_sur_les_quatre_sources():
    """Sans les services, Dagster se masque par l'API et ne se remet plus.

    La verification porte sur L'EXPRESSION qui construit la liste, et pas sur
    la presence des noms dans la fonction : un mutant qui remplacait
    « .concat(hubServices||[]) » par « .concat([]) » laissait les deux noms
    ailleurs -- dans le commentaire au-dessus et dans le calcul des cochees --
    et passait au travers. C'est ce mutant qui a ecrit ce test-ci.
    """
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    debut = page.index("function peindreMasquees(){")
    fin = page.index("}", page.index("const lignes", debut))
    corps = page[debut:fin]
    depart = corps.index("const toutes =")
    expression = corps[depart:corps.index(";", depart)]
    for source in ("hubApps", "hubMasquees", "hubServices", "hubServicesMasques"):
        assert source in expression, (
            f"la liste du rangement ne part pas de {source} : "
            f"« {expression.strip()} »")


def test_la_carte_du_rangement_ne_dit_plus_l_inverse_de_ce_qu_elle_fait():
    """Elle disait « Coche une application pour la retirer de ton hub ».

    Le code fait l'inverse depuis longtemps -- cochee veut dire AFFICHEE --
    et la phrase etait restee. Sur un reglage dont tout l'interet est le sens
    de la case, c'est la seule phrase qu'il fallait avoir juste.
    """
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    debut = page.index('<b>Applications du hub</b>')
    carte = page[debut:debut + 900]
    assert "Cochée, elle est affichée" in carte
    assert "Coche une application pour la retirer" not in carte
    # Le rangement n'est pas une fermeture : la carte doit continuer de le dire.
    assert "rangement" in carte


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
    le bloc vide « codelab-dev » doit partir, et les blocs des autres
    services rester intacts."""
    import subprocess
    source = open(os.path.join(_racine_depot(), "postgres", "entrypoint.sh"),
                  encoding="utf-8").read()
    fonctions = "".join(re.findall(r"^(?:upsert_block|remove_block|header_block)\(\) \{.*?\n\}\n",
                                   source, re.S | re.M))
    assert "header_block()" in fonctions and "remove_block()" in fonctions
    appel = re.search(r'^header_block "# credentials\.env.*?"\n', source, re.S | re.M).group(0)
    assert 'remove_block "codelab-dev"' in source
    assert 'upsert_block "codelab-dev"' not in source
    fichier = tmp_path / "credentials.env"
    fichier.write_text(
        "# ===== codelab-app-manager =====\nAPP_MANAGER_ADMIN_PASSWORD=x\n"
        "# ===== /codelab-app-manager =====\n"
        "# ===== codelab-header =====\n# ancien\n# ===== /codelab-header =====\n"
        "# ===== codelab-dev =====\n# Pas de mot de passe\n# ===== /codelab-dev =====\n",
        encoding="utf-8")
    script = f'ENV_FILE="{fichier}"\n' + fonctions + appel + 'remove_block "codelab-dev"\n'
    subprocess.run(["sh", "-c", script], check=True, timeout=30)
    texte = fichier.read_text(encoding="utf-8")
    assert texte.startswith("# ===== codelab-header =====\n"), texte[:80]
    assert texte.count("# ===== codelab-header =====") == 1
    assert "# ancien" not in texte
    assert "codelab-dev =====" not in texte
    assert "APP_MANAGER_ADMIN_PASSWORD=x" in texte
    # Le sommaire nomme chaque bloc qui existe encore, et dit s'il va aux apps.
    for bloc in ("codelab-postgres", "codelab-app-manager", "codelab-sso", "codelab-alertes"):
        assert bloc in texte, bloc


def test_le_proxy_dagster_retire_son_ancien_bloc_au_lieu_de_le_reecrire(tmp_path):
    """Le bloc ne portait plus aucune valeur : il part, les autres restent."""
    import subprocess
    source = open(os.path.join(_racine_depot(), "dagster", "proxy", "entrypoint.sh"),
                  encoding="utf-8").read()
    assert 'upsert_block "codelab-dagster-proxy"' not in source
    debut = source.index("CONFIG_DIR=")
    fin = source.index('mv "$ENV_FILE.tmp" "$ENV_FILE"') + len('mv "$ENV_FILE.tmp" "$ENV_FILE"')
    fichier = tmp_path / "credentials.env"
    fichier.write_text(
        "# ===== codelab-postgres =====\nPOSTGRES_PASSWORD=y\n# ===== /codelab-postgres =====\n"
        "# ===== codelab-dagster-proxy =====\n# note\n# ===== /codelab-dagster-proxy =====\n",
        encoding="utf-8")
    subprocess.run(["sh", "-c", source[debut:fin]], check=True, timeout=30,
                   env={"CODELAB_CONFIG_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"})
    texte = fichier.read_text(encoding="utf-8")
    assert "codelab-dagster-proxy" not in texte
    assert "POSTGRES_PASSWORD=y" in texte


def test_les_restes_d_openclaw_partent_sauf_ce_qui_a_ete_retouche(tmp_path):
    """Execute le bloc de l'entrypoint : .openclaw part, les fichiers VS Code
    ecrits pour OpenClaw partent s'ils sont intacts, et un settings.json
    complete a la main reste."""
    import subprocess
    source = _entrypoint_dev()
    debut = source.index('rm -rf "$WORKSPACE_DIR/.openclaw"')
    fin = source.index("\nPYRESTES\n", debut) + len("\nPYRESTES\n")
    (tmp_path / ".openclaw").mkdir()
    (tmp_path / ".openclaw" / "openclaw.json").write_text("{}", encoding="utf-8")
    vs = tmp_path / ".vscode"
    vs.mkdir()
    (vs / "extensions.json").write_text(json.dumps(
        {"recommendations": ["openknot.openclaw-extension"]}), encoding="utf-8")
    retouche = {"remote.autoForwardPorts": True,
                "remote.portsAttributes": {"18789": {"label": "OpenClaw - chat",
                                                     "onAutoForward": "silent"}},
                "editor.fontSize": 14}
    (vs / "settings.json").write_text(json.dumps(retouche), encoding="utf-8")
    subprocess.run(["bash", "-c", source[debut:fin]], check=True, timeout=30,
                   env={"WORKSPACE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    assert not (tmp_path / ".openclaw").exists()
    assert not (vs / "extensions.json").exists()
    assert json.loads((vs / "settings.json").read_text(encoding="utf-8")) == retouche


def test_les_images_python_partagent_la_meme_base():
    """Docker ne stocke une couche qu'une fois. Une image sur une autre base
    Python en porte une copie a elle -- environ 120 Mo sur une eMMC de 17 Go
    qui a deja deborde. Et la CI teste sur la version que le panneau livre."""
    racine = _racine_depot()
    bases = {}
    for service in ("app-manager", "dev", "dagster"):
        source = open(os.path.join(racine, service, "Dockerfile"), encoding="utf-8").read()
        bases[service] = re.search(r"^FROM (\S+)", source, re.M).group(1)
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

def _bloc_codex(dossier):
    """Le bloc Codex de dev/entrypoint.sh, pris tel quel et execute."""
    import subprocess
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"),
                  encoding="utf-8").read()
    debut = source.index('CODEX_CFG="$CODEX_HOME_DIR/config.toml"')
    fin = source.index("# ~/.codex vers CODEX_HOME.")
    bloc = source[debut:fin]
    return subprocess.run(["sh", "-c", bloc],
                          env=dict(os.environ, CODEX_HOME_DIR=str(dossier),
                                   SSH_USER=str(os.getuid())),
                          capture_output=True, text=True, timeout=30)


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


# ---------- Redis : un service partage, ferme, et un verrou sur les secrets --

def _lancer_entrypoint_redis(tmp_path, extra_env=None):
    """Execute redis/entrypoint.sh tel quel, avec un faux entrypoint officiel
    qui se contente d'afficher ce qu'il recoit."""
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    faux = bin_dir / "docker-entrypoint.sh"
    faux.write_text('#!/bin/sh\necho "LANCE $*"\n', encoding="utf-8")
    faux.chmod(0o755)
    (tmp_path / "config").mkdir(exist_ok=True)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin",
           "CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_REDIS_CONF": str(tmp_path / "redis.conf"),
           "CODELAB_REDIS_DIR": str(tmp_path / "data")}
    env.update(extra_env or {})
    return subprocess.Popen(
        ["sh", os.path.join(_racine_depot(), "redis", "entrypoint.sh"),
         "redis-server", str(tmp_path / "redis.conf")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


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


def _port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


# ---------- Mailpit : l'envoi de mails, garde et relaye ----------

def test_mailpit_est_dans_la_stack_ferme_et_derriere_le_proxy():
    for chemin in _composes():
        bloc = _bloc_du_service(chemin, "codelab-mailpit")
        assert bloc, f"codelab-mailpit absent de {os.path.basename(chemin)}"
        assert "ports:" not in bloc, chemin
        assert "no-new-privileges:true" in bloc and "- ALL" in bloc, chemin
        assert 'CODELAB_MAILPIT_RELAIS: "auto"' in bloc, chemin
        proxy = _bloc_du_service(chemin, "codelab-dagster-proxy")
        assert '"8025:8025"' in proxy, chemin
    racine = _racine_depot()
    ci = open(os.path.join(racine, ".github", "workflows", "build-images.yml"),
              encoding="utf-8").read()
    assert "name: codelab-mailpit" in ci and '"mailpit/**"' in ci
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    emplacement = conf[conf.index("location / {"):]
    # L'autorisation du panneau d'abord, puis le mot de passe interne.
    assert "auth_request /_auth;" in emplacement
    assert "include /etc/nginx/codelab/mailpit-auth.conf;" in emplacement
    dockerfile = open(os.path.join(racine, "dagster", "proxy", "Dockerfile"), encoding="utf-8").read()
    assert "echo 'return 503;' > /etc/nginx/codelab/mailpit-auth.conf" in dockerfile


def test_le_proxy_genere_le_mot_de_passe_interne_de_mailpit(tmp_path):
    import base64 as b64
    import subprocess
    (tmp_path / "config").mkdir()
    env_file = tmp_path / "config" / "credentials.env"
    env_file.write_text("# ===== codelab-postgres =====\nPOSTGRES_PASSWORD=y\n"
                        "# ===== /codelab-postgres =====\n", encoding="utf-8")
    include = tmp_path / "ng" / "mailpit-auth.conf"
    env = {"CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_MAILPIT_AUTH_INCLUDE": str(include), "PATH": "/usr/bin:/bin"}
    script = os.path.join(_racine_depot(), "dagster", "proxy", "entrypoint.sh")
    for _ in range(2):
        subprocess.run(["sh", script, "true"], check=True, timeout=30, env=env,
                       capture_output=True)
    texte = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=y" in texte
    assert texte.count("# ===== codelab-proxy =====") == 1
    mdp = re.search(r"^CODELAB_INTERNE_MAILPIT_UI_PASSWORD=([0-9a-f]{64})$", texte, re.M).group(1)
    attendu = b64.b64encode(f"codelab:{mdp}".encode()).decode()
    assert include.read_text() == f'proxy_set_header Authorization "Basic {attendu}";\n'
    assert stat.S_IMODE(include.stat().st_mode) == 0o600


def _lancer_entrypoint_mailpit(tmp_path, blocs, extra_env=None):
    """mailpit/entrypoint.sh tel quel, avec un faux binaire Mailpit qui note son
    environnement et s'arrete : l'entrypoint doit alors sortir avec lui."""
    import subprocess
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "credentials.env").write_text(blocs, encoding="utf-8")
    faux = tmp_path / "mailpit"
    faux.write_text(f'#!/bin/sh\nenv | grep "^MP_" | sort > "{tmp_path}/env.txt"\nexit 0\n',
                    encoding="utf-8")
    faux.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "CODELAB_CONFIG_DIR": str(tmp_path / "config"),
           "CODELAB_MAILPIT_BIN": str(faux), "CODELAB_MAILPIT_INTERVALLE": "1",
           "CODELAB_MAILPIT_ATTENTE": "2"}
    env.update(extra_env or {})
    proc = subprocess.run(["sh", os.path.join(_racine_depot(), "mailpit", "entrypoint.sh")],
                          env=env, capture_output=True, text=True, timeout=30)
    vu = {}
    if (tmp_path / "env.txt").exists():
        for ligne in (tmp_path / "env.txt").read_text().splitlines():
            cle, _, valeur = ligne.partition("=")
            vu[cle] = valeur
    return proc, vu


_UI = "CODELAB_INTERNE_MAILPIT_UI_PASSWORD=" + "a" * 64 + "\n"


def test_mailpit_relaie_par_le_serveur_du_panneau(tmp_path):
    blocs = (_UI + "CODELAB_INTERNE_RELAIS_HOST=smtp.gmail.com\n"
             "CODELAB_INTERNE_RELAIS_PORT=587\nCODELAB_INTERNE_RELAIS_TLS=starttls\n"
             "CODELAB_INTERNE_RELAIS_USER=moi@gmail.com\n"
             "CODELAB_INTERNE_RELAIS_PASSWORD=abcd efgh\n"
             "CODELAB_INTERNE_RELAIS_FROM=moi@gmail.com\n")
    proc, vu = _lancer_entrypoint_mailpit(tmp_path, blocs)
    assert proc.returncode == 0, proc.stderr
    assert vu["MP_UI_AUTH"] == "codelab:" + "a" * 64
    assert vu["MP_SMTP_RELAY_ALL"] == "true"
    assert vu["MP_SMTP_RELAY_HOST"] == "smtp.gmail.com" and vu["MP_SMTP_RELAY_PORT"] == "587"
    assert vu["MP_SMTP_RELAY_STARTTLS"] == "true" and "MP_SMTP_RELAY_TLS" not in vu
    assert vu["MP_SMTP_RELAY_AUTH"] == "plain"
    assert vu["MP_SMTP_RELAY_PASSWORD"] == "abcd efgh"
    assert vu["MP_SMTP_RELAY_OVERRIDE_FROM"] == "moi@gmail.com"
    # Le mot de passe du serveur d'envoi ne sort pas dans les journaux.
    assert "abcd efgh" not in proc.stdout + proc.stderr
    texte = (tmp_path / "config" / "credentials.env").read_text()
    assert "MAIL_HOST=codelab-mailpit\nMAIL_PORT=1025\n" in texte


def test_mailpit_en_ssl_et_en_mode_test(tmp_path):
    blocs = (_UI + "CODELAB_INTERNE_RELAIS_HOST=ssl0.ovh.net\n"
             "CODELAB_INTERNE_RELAIS_PORT=465\nCODELAB_INTERNE_RELAIS_TLS=ssl\n")
    _, vu = _lancer_entrypoint_mailpit(tmp_path, blocs)
    assert vu["MP_SMTP_RELAY_TLS"] == "true" and "MP_SMTP_RELAY_STARTTLS" not in vu
    assert vu["MP_SMTP_RELAY_AUTH"] == "none"
    _, vu = _lancer_entrypoint_mailpit(tmp_path, blocs, {"CODELAB_MAILPIT_RELAIS": "non"})
    assert not [c for c in vu if c.startswith("MP_SMTP_RELAY")], vu
    # Sans serveur d'envoi : tout est garde, rien n'est relaye.
    _, vu = _lancer_entrypoint_mailpit(tmp_path, _UI)
    assert not [c for c in vu if c.startswith("MP_SMTP_RELAY")], vu


def test_mailpit_refuse_de_demarrer_sans_le_mot_de_passe_interne(tmp_path):
    """Sans lui, son API serait ouverte a toutes les applications du reseau."""
    proc, vu = _lancer_entrypoint_mailpit(tmp_path, "")
    assert proc.returncode == 1
    assert vu == {}, "Mailpit a ete lance sans mot de passe"


def test_le_panneau_publie_le_relais_mais_ne_le_transmet_pas(tmp_path, monkeypatch):
    env_file = tmp_path / "credentials.env"
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(env_file))
    monkeypatch.setattr(app, "lire_smtp_personnalise", lambda: app._normalise_smtp({
        "host": "smtp.gmail.com", "port": 587, "tls": "starttls",
        "user": "moi@gmail.com", "password": "secret"}))
    assert app.publier_relais_mail()
    texte = env_file.read_text()
    assert "CODELAB_INTERNE_RELAIS_HOST=smtp.gmail.com" in texte
    assert "CODELAB_INTERNE_RELAIS_PASSWORD=secret" in texte
    assert "CODELAB_INTERNE_RELAIS_FROM=moi@gmail.com" in texte
    with open(env_file, "a") as f:
        f.write("MAIL_HOST=codelab-mailpit\nCODELAB_INTERNE_MAILPIT_UI_PASSWORD=x\n")
    transmis = app.secrets_partages()
    assert transmis.get("MAIL_HOST") == "codelab-mailpit"
    assert not [c for c in transmis if c.startswith("CODELAB_INTERNE_")], transmis
    # Et il suit un enregistrement depuis Parametres > E-mail.
    assert "publier_relais_mail()" in inspect.getsource(app)


# ---------- la tuile Mailpit ----------

def test_mailpit_a_sa_tuile_dans_le_hub_et_mene_au_port_8025(hub_dagster):
    hub_dagster.post("/login", json={"password": "secret-de-test"})
    d = hub_dagster.get("/api/mes-apps").get_json()
    (mailpit,) = [s for s in d["services"] if s["name"] == app.MAILPIT_NOM]
    assert mailpit["url"].endswith(":8025/"), mailpit["url"]
    assert mailpit["externe"] is True and mailpit["par_defaut"] is True
    assert mailpit["titre"] == "Mailpit"
    # La porte du panneau journalise puis redirige vers le proxy.
    r = hub_dagster.get("/mailpit/")
    assert r.status_code == 302 and r.headers["Location"].endswith(":8025/")
    # Son icone, dessinee, distincte des autres.
    icone = hub_dagster.get("/api/icon/mailpit")
    assert icone.mimetype == "image/svg+xml" and b"<text" not in icone.data
    assert icone.data != hub_dagster.get("/api/icon/dagster").data


def test_mailpit_est_reserve_a_l_administrateur(hub_dagster):
    """Son interface montre tous les mails, liens de reinitialisation compris."""
    c = hub_dagster
    _connecte(c, "marie", "un-mot-de-passe")
    assert c.get("/mailpit/").status_code in (302, 403, 404)
    assert c.get("/mailpit/").headers.get("Location", "").endswith(":8025/") is False
    d = c.get("/api/mes-apps").get_json()
    assert not [s for s in d.get("services", []) if s["name"] == app.MAILPIT_NOM]
    assert c.post("/api/mes-apps/mailpit/masquer").status_code == 404


def test_mailpit_se_range_et_ne_se_configure_pas(hub, monkeypatch):
    monkeypatch.setattr(app, "origine_mailpit", lambda: "http://x:8025/")
    c = _admin_du_hub(hub)
    assert c.post("/api/mes-apps/mailpit/masquer").status_code == 200
    d = c.get("/api/mes-apps").get_json()
    assert app.MAILPIT_NOM not in [s["name"] for s in d["services"]]
    assert app.MAILPIT_NOM in [s["name"] for s in d["services_masques"]]
    assert "par défaut" in app.refus_configuration(app.MAILPIT_NOM)
    assert app.refus_action_protegee(app.MAILPIT_NOM, "arrêt") is not None


def test_la_surveillance_de_mailpit_ne_note_que_les_changements(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "_mailpit_suivi", {"debout": None})
    etats = iter([(True, 3.0), (True, 4.0), (False, 5000.0)])
    monkeypatch.setattr(app, "mailpit_sonde", lambda: next(etats))
    for _ in range(3):
        app.mailpit_tick()
    lignes = (tmp_path / "mailpit.log").read_text().splitlines()
    assert len(lignes) == 2, lignes
    assert "Mailpit répond" in lignes[0] and "ne répond pas" in lignes[1]


# ---------- robustesse : double clic, plafonds, surveillance, erreurs ----------

def test_une_seule_action_a_la_fois_par_application(hub, monkeypatch):
    """Un double clic sur « Demarrer » demarrait puis arretait : la seconde
    requete arrivait quand l'application tournait deja. Pendant qu'une action
    tourne, les autres sont refusees en 409."""
    import threading
    c = _admin_du_hub(hub)
    entree, sortie = threading.Event(), threading.Event()

    def build_lent(n):
        entree.set()
        sortie.wait(5)
        return True, "ok"
    monkeypatch.setattr(app, "run_build", build_lent)
    resultat = {}
    # Le premier build tourne dans un fil, avec sa propre session admin.
    c2 = app.flask_app.test_client()
    c2.post("/login", json={"password": "secret-de-test"})
    fil = threading.Thread(target=lambda: resultat.setdefault(
        "premier", c2.post("/api/build/compta").status_code))
    fil.start()
    assert entree.wait(5)
    r = c.post("/api/deploy/compta")
    assert r.status_code == 409 and r.get_json()["en_cours"] is True
    assert c.post("/api/toggle/compta").status_code == 409
    # Une AUTRE application n'est pas bloquee.
    monkeypatch.setattr(app, "stop", lambda n: None)
    assert c.post("/api/toggle/vitrine").status_code == 200
    sortie.set()
    fil.join(5)
    assert resultat["premier"] == 200
    # Le verrou est rendu.
    monkeypatch.setattr(app, "run_build", lambda n: (True, "ok"))
    assert c.post("/api/build/compta").status_code == 200


def test_un_second_clic_sur_demarrer_n_arrete_pas(hub, monkeypatch):
    """La page dit l'action VOULUE : si l'application tourne deja, un
    « demarrage » ne fait rien -- surtout pas un arret."""
    c = _admin_du_hub(hub)
    arrets = []
    monkeypatch.setattr(app, "stop", lambda n: arrets.append(n))
    r = c.post("/api/toggle/compta", json={"action": "demarrage"})
    assert r.status_code == 200 and r.get_json()["deja"] is True
    assert arrets == []
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    assert "actionsEnCours" in page and "body:JSON.stringify({action:" in page


def test_la_surveillance_externe_n_accepte_que_https_et_verifie_l_adresse(client, monkeypatch, tmp_path):
    client.post("/login", json={"password": "secret-de-test"})
    r = client.post("/api/surveillance-externe", json={"url": "http://hc-ping.com/x"})
    assert r.status_code == 400
    appels = []
    monkeypatch.setattr(app, "signaler_vie", lambda url=None: (appels.append(url) or (False, "HTTP 404")))
    r = client.post("/api/surveillance-externe", json={"url": "https://hc-ping.com/abc"})
    assert r.status_code == 400 and appels == ["https://hc-ping.com/abc"]
    assert app.lire_surveillance() == "", "une adresse qui ne repond pas ne s'enregistre pas"
    monkeypatch.setattr(app, "signaler_vie", lambda url=None: (True, "HTTP 200"))
    assert client.post("/api/surveillance-externe",
                       json={"url": "https://hc-ping.com/abc"}).status_code == 200
    assert app.lire_surveillance() == "https://hc-ping.com/abc"
    # Au coffre : chiffree, en 0600, jamais en clair.
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600
    assert b"hc-ping.com" not in _octets_de_l_etat()
    assert client.post("/api/surveillance-externe", json={"url": ""}).status_code == 200
    assert app.lire_surveillance() == ""


def test_le_panneau_compresse_sans_exposer_le_jeton(client):
    client.post("/login", json={"password": "secret-de-test"})
    r = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in r.headers.get("Vary", "")
    import gzip as gz
    assert b"<html" in gz.decompress(r.data).lower()
    # BREACH : une requete qui porte des parametres n'est jamais compressee.
    r = client.get("/?x=1", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in r.headers
    # Sans Accept-Encoding, rien ne change.
    assert "Content-Encoding" not in client.get("/").headers


def test_les_erreurs_du_navigateur_arrivent_dans_les_journaux(client, capsys):
    client.post("/login", json={"password": "secret-de-test"})
    r = client.post("/api/erreur-navigateur", json={
        "message": "x is not defined\n[faux] ligne", "source": "/", "ligne": 12,
        "page": "/#hub"})
    assert r.status_code == 200
    sortie = capsys.readouterr().out
    assert "[navigateur]" in sortie and "x is not defined [faux] ligne" in sortie
    assert "\n[faux]" not in sortie, "un saut de ligne fabriquerait une fausse ligne"
    for _ in range(40):
        r = client.post("/api/erreur-navigateur", json={"message": "boucle"})
    assert r.status_code == 429
    page = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    assert "addEventListener('unhandledrejection'" in page


def test_la_sauvegarde_est_restauree_a_l_essai():
    source = _entrypoint_postgres()
    assert 'verifier_restaurations "$bases"' in source
    bloc = source.split("verifier_restaurations() {")[1].split("\n}\n")[0]
    assert "pg_restore -U" in bloc and "tables_de_la_sauvegarde" in bloc
    assert "ECHEC" in bloc and "$VERIFICATION" in bloc
    # La base jetable n'est jamais elle-meme sauvegardee.
    assert "AND datname <> '$BASE_ESSAI'" in source


def test_le_diagnostic_rougit_si_la_restauration_d_essai_rate(tmp_path):
    for nom in ("base-diagnostic-20990101-000000.dump", "panneau-20990101-000000.tar.gz"):
        (tmp_path / nom).write_text("x")
    (tmp_path / "verification-restauration.txt").write_text(
        "2099-01-01T00:00:00Z ECHEC restauration d'essai ratee pour : diagnostic\n")
    etat, _, detail = checks.check_sauvegardes(str(tmp_path))
    assert etat == checks.Etat.ECHEC and "restauration d'essai" in detail
    (tmp_path / "verification-restauration.txt").write_text(
        "2099-01-01T00:00:00Z OK 2 base(s) restauree(s) sans erreur\n")
    etat, _, detail = checks.check_sauvegardes(str(tmp_path))
    assert etat == checks.Etat.OK and "sans erreur" in detail


def test_le_code_du_workspace_est_archive_sans_ce_qui_se_reconstruit(tmp_path, monkeypatch):
    racine, cible = tmp_path / "ws", tmp_path / "sv"
    (racine / "projet" / "node_modules" / "x").mkdir(parents=True)
    (racine / "projet" / "node_modules" / "x" / "index.js").write_text("lourd")
    (racine / "projet" / "app.py").write_text("print(1)")
    (racine / "projet" / ".env").write_text("CLE=1")
    (racine / "projet" / "gros.bin").write_bytes(b"0" * 100)
    cible.mkdir()
    monkeypatch.setattr(app, "ROOT", str(racine))
    monkeypatch.setattr(app, "SAUVEGARDES_DIR", str(cible))
    monkeypatch.setattr(app, "WORKSPACE_FICHIER_MAX", 50)
    assert app.workspace_a_sauvegarder()
    chemin = app.sauvegarder_le_workspace()
    import tarfile
    with tarfile.open(chemin) as archive:
        noms = {n.lstrip("./") for n in archive.getnames()}
    assert "projet/app.py" in noms and "projet/.env" in noms
    assert not [n for n in noms if "node_modules" in n]
    assert "projet/gros.bin" not in noms
    assert stat.S_IMODE(os.stat(chemin).st_mode) == 0o600
    assert not app.workspace_a_sauvegarder(), "une par semaine, pas a chaque passage"


# ---------- l'extension Database Client JDBC dans VS Code ----------

def _bloc_extensions_vscode(tmp_path):
    source = open(os.path.join(_racine_depot(), "dev", "entrypoint.sh"), encoding="utf-8").read()
    debut = source.index('VSCODE_EXTENSIONS="${CODELAB_VSCODE_EXTENSIONS')
    fin = source.index('if [ -n "$VSCODE_EXTENSIONS" ]; then\n    ( while')
    return source, source[debut:fin]


def test_l_extension_postgresql_est_recommandee_sans_ecraser_le_reste(tmp_path):
    import subprocess
    source, bloc = _bloc_extensions_vscode(tmp_path)
    assert "cweijan.dbclient-jdbc" in bloc
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
    assert contenu == {"recommendations": ["ms-python.python", "cweijan.dbclient-jdbc"],
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
        assert 'CODELAB_VSCODE_EXTENSIONS: "cweijan.dbclient-jdbc"' in open(
            chemin, encoding="utf-8").read(), chemin


# ---------- le coffre : reglages et adresses chiffres, en base et en local ----

def _octets_de_l_etat():
    """Tout ce que le panneau a ecrit sur disque, concatene."""
    tout = b""
    for dossier, _, fichiers in os.walk(app.STATE_DIR):
        for nom in fichiers:
            with open(os.path.join(dossier, nom), "rb") as f:
                tout += f.read()
    if os.path.exists(app.SHARED_ENV_FILE):
        tout += open(app.SHARED_ENV_FILE, "rb").read()
    return tout


def test_les_adresses_des_comptes_ne_sont_jamais_en_clair():
    app.ecrire_utilisateurs({"marie": {"sel": "aa", "hash": "bb", "projets": [],
                                       "email": "marie@exemple.fr"}})
    assert app.lire_utilisateurs()["marie"]["email"] == "marie@exemple.fr"
    assert b"marie@exemple.fr" not in _octets_de_l_etat()
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600


def test_le_serveur_d_envoi_et_son_mot_de_passe_sont_chiffres():
    app.ecrire_smtp_personnalise(app._normalise_smtp({
        "host": "smtp.gmail.com", "port": 587, "user": "moi@gmail.com",
        "password": "motdepasse-tres-secret"}))
    assert app.lire_smtp_personnalise()["password"] == "motdepasse-tres-secret"
    for secret in (b"motdepasse-tres-secret", b"moi@gmail.com"):
        assert secret not in _octets_de_l_etat(), secret
    app.effacer_smtp_personnalise()
    assert app.lire_smtp_personnalise() is None


def test_une_cle_perdue_ne_casse_rien_et_ne_detruit_rien():
    """Cle changee : la valeur illisible n'est ni effacee ni ecrasee, le
    panneau fait comme si elle manquait plutot que de tomber -- et la cle
    retrouvee la rend de nouveau lisible."""
    from cryptography.fernet import Fernet
    app.ecrire_reglage("alertes_admin", ["moi@exemple.fr"])
    bonne = app._cle_chiffrement
    app._cle_chiffrement = Fernet.generate_key().decode()
    app._coffre["donnees"] = None
    assert app.reglage("alertes_admin") is None
    assert app.stockage.lire(app.DB_FILE, "SELECT 1 FROM reglages_chiffres "
                                          "WHERE cle = 'alertes_admin'")
    app._cle_chiffrement = bonne
    app._coffre["donnees"] = None
    assert app.reglage("alertes_admin") == ["moi@exemple.fr"]


def test_les_anciens_fichiers_en_clair_migrent_au_coffre():
    etat = app.STATE_DIR
    json.dump({"host": "smtp.x.fr", "password": "ancien-mdp"}, open(app.SMTP_FILE, "w"))
    json.dump({"actif": True, "destinataires": ["a@x.fr"]}, open(app.ALERTES_FILE, "w"))
    json.dump({"url": "https://hc-ping.com/abc"}, open(app.SURVEILLANCE_FILE, "w"))
    json.dump({"paul": {"sel": "aa", "hash": "bb", "email": "paul@x.fr"}},
              open(app.UTILISATEURS_FILE, "w"))
    app.save({"site": {"path": "/w/s", "command": "x", "port": 9101,
                       "alertes": ["ops@x.fr"]}})
    app.upsert_shared_block("codelab-alertes", [], {"SMTP_HOST": "smtp.origine.fr",
                                                    "ALERTE_ADMIN": "chef@x.fr"})
    app.importer_anciens_fichiers()
    app.migrer_vers_le_coffre()

    for chemin in (app.SMTP_FILE, app.ALERTES_FILE, app.SURVEILLANCE_FILE):
        assert not os.path.exists(chemin), chemin
    assert app.lire_smtp_personnalise()["password"] == "ancien-mdp"
    assert app.lire_alertes()["actif"] is True
    assert app.lire_surveillance() == "https://hc-ping.com/abc"
    assert app.alertes_admin() == ["chef@x.fr"]
    assert app.destinataires_alerte("site") == ["chef@x.fr", "ops@x.fr"]
    assert "alertes" not in app.load()["site"]
    assert app.lire_utilisateurs()["paul"]["email"] == "paul@x.fr"
    env = open(app.SHARED_ENV_FILE).read()
    assert "ALERTE_ADMIN=" not in env and "SMTP_HOST=smtp.origine.fr" in env
    for clair in (b"ancien-mdp", b"chef@x.fr", b"ops@x.fr", b"paul@x.fr", b"hc-ping.com"):
        assert clair not in _octets_de_l_etat(), clair
    assert os.path.isdir(etat)


def test_les_destinataires_suivent_un_renommage_et_partent_avec_l_application():
    app.ecrire_reglage("alertes_par_app", {"vieux": ["a@x.fr"]})
    app._renommer_references("vieux", "neuf")
    assert app.reglage("alertes_par_app") == {"neuf": ["a@x.fr"]}


class _FauxCurseur:
    def __init__(self, base):
        self.base, self.resultat = base, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if sql.startswith("SELECT cle, valeur FROM reglages"):
            self.resultat = list(self.base.items())
        elif sql.startswith("INSERT INTO reglages"):
            self.base[params[0]] = bytes(params[1])
        elif sql.startswith("DELETE FROM reglages"):
            self.base.pop(params[0], None)

    def fetchall(self):
        return self.resultat


class _FausseBase:
    def __init__(self):
        self.lignes = {}

    def cursor(self):
        return _FauxCurseur(self.lignes)


def test_la_base_recoit_les_reglages_chiffres_et_sait_les_rendre():
    """En base : chiffre, jamais en clair. Et si la copie locale disparait
    (dossier d'etat restaure, disque change), le panneau se reprend depuis
    la base."""
    base = _FausseBase()
    app.ecrire_reglage("smtp", {"host": "smtp.x.fr", "password": "secret-en-base"})
    app._pg_synchroniser_reglages(base)
    assert set(base.lignes) == {"smtp"}
    assert b"secret-en-base" not in base.lignes["smtp"]

    with app.stockage.transaction(app.DB_FILE) as cx:     # copie locale perdue
        cx.execute("DELETE FROM reglages_chiffres")
    app._coffre["donnees"] = None
    app._pg_synchroniser_reglages(base)
    assert app.reglage("smtp")["password"] == "secret-en-base"
    app._coffre["donnees"] = None                        # et elle est reecrite
    assert app.reglage("smtp")["password"] == "secret-en-base"
    source = inspect.getsource(app._pg_ecrire_utilisateurs)
    assert 'UPDATE utilisateurs SET email = \'\'' in source, (
        "les adresses recopiees en clair par une version precedente doivent partir")


def test_credentials_env_porte_les_identifiants_par_defaut_et_la_cle():
    app.ecrire_bloc_panneau("mdp", "cle-session", "")
    env = open(app.SHARED_ENV_FILE).read()
    assert "APP_MANAGER_ADMIN_LOGIN=admin" in env
    assert f"APP_MANAGER_CHIFFREMENT_CLE={app._cle_chiffrement}" in env
    # Jamais transmise aux applications : prefixe du panneau.
    assert not [c for c in app.secrets_partages() if "CHIFFREMENT" in c]
    racine = _racine_depot()
    assert "REDIS_USER=default" in open(os.path.join(racine, "redis", "entrypoint.sh")).read()
    assert "CODELAB_INTERNE_MAILPIT_UI_USER=codelab" in open(
        os.path.join(racine, "dagster", "proxy", "entrypoint.sh")).read()


def test_la_cle_de_chiffrement_n_est_jamais_regeneree(monkeypatch):
    cle = app._cle_chiffrement
    app.ecrire_bloc_panneau("mdp", "cle-session", "")
    monkeypatch.setattr(app, "_cle_chiffrement", "")
    app.bootstrap_secrets()
    assert app._cle_chiffrement == cle


def test_l_onglet_d_une_application_prend_son_logo_si_elle_n_en_a_pas():
    page = b"<html><head><title>x</title></head><body>ok</body></html>"
    entetes = [("Content-Type", "text/html"), ("Content-Length", str(len(page)))]
    with app.flask_app.test_request_context("/site/"):
        data, sortants = app.injecter_icone(page, 200, entetes, "mon site")
    assert b'<link rel="icon" href="/api/icon/mon%20site"></head>' in data
    assert ("Content-Length", str(len(data))) in sortants
    # Une application qui declare deja son icone la garde.
    propre = page.replace(b"</head>", b'<link rel="icon" href="/f.png"></head>')
    with app.flask_app.test_request_context("/site/"):
        data, _ = app.injecter_icone(propre, 200, entetes, "site")
    assert data == propre
    # Le panneau garde le logo CodeLab dans ses onglets.
    panneau = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    assert panneau.count('rel="icon"') == 1


def test_une_application_s_installe_comme_une_app_sur_l_ecran_d_accueil():
    """Le proxy pose manifeste, icone et balises iOS dans chaque page --
    sans jamais ecraser ce que l'application declare elle-meme."""
    page = b"<html><head><title>x</title></head><body>ok</body></html>"
    entetes = [("Content-Type", "text/html"), ("Content-Length", str(len(page)))]
    with app.flask_app.test_request_context("/site/"):
        data, sortants = app.injecter_webapp(page, 200, entetes, "mon site",
                                             'Mon <site>')
    tete = data[:data.index(b"</head>")]
    assert b'<link rel="manifest" href="/mon%20site/__codelab/manifest.webmanifest">' in tete
    assert b'<link rel="apple-touch-icon" href="/mon%20site/__codelab/icone.png">' in tete
    assert b'name="apple-mobile-web-app-capable" content="yes"' in tete
    assert b'name="mobile-web-app-capable" content="yes"' in tete
    assert b'content="Mon &lt;site&gt;"' in tete, "le titre doit etre echappe"
    # Le ruban reste visible en mode app : rien ne le masque.
    assert b"display-mode" not in tete
    assert ("Content-Length", str(len(data))) in sortants

    # Ce que l'application declare deja reste a elle.
    propre = page.replace(b"</head>", b'<link rel="manifest" href="/m.json">'
                          b'<link rel="apple-touch-icon" href="/i.png">'
                          b'<meta name="apple-mobile-web-app-title" content="A"></head>')
    with app.flask_app.test_request_context("/site/"):
        data, _ = app.injecter_webapp(propre, 200, entetes, "site", "site")
    assert data.count(b'rel="manifest"') == 1
    assert data.count(b"apple-touch-icon") == 1
    assert data.count(b"apple-mobile-web-app-title") == 1

    # Les memes refus que le ruban : on ne touche ni a une erreur, ni a
    # autre chose que du HTML, ni a un corps compresse.
    with app.flask_app.test_request_context("/site/"):
        assert app.injecter_webapp(page, 404, entetes, "s", "s")[0] == page
        assert app.injecter_webapp(page, 200, [("Content-Type", "application/json")],
                                   "s", "s")[0] == page
        assert app.injecter_webapp(page, 200, entetes + [("Content-Encoding", "gzip")],
                                   "s", "s")[0] == page


def test_le_manifeste_d_une_application_reste_dans_son_projet(client, monkeypatch, tmp_path):
    """Portee /<nom>/ : l'app installee ne deborde pas sur les autres projets.
    Le manifeste et l'icone suivent les controles d'acces du proxy, et
    repondent meme application arretee."""
    import struct
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    projet = tmp_path / "projet"
    projet.mkdir()
    app.save({"site": {"path": str(projet), "command": "x", "port": 9101,
                       "enabled": True, "titre": "Mon site"},
              "prive": {"path": str(projet), "command": "x", "port": 9102,
                        "enabled": True, "visibility": "privee"}})
    monkeypatch.setattr(app, "is_running", lambda n: False)

    r = client.get("/site/__codelab/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    m = json.loads(r.get_data(as_text=True))
    assert m["scope"] == m["start_url"] == "/site/"
    assert m["display"] == "standalone" and m["name"] == "Mon site"

    # Sans logo dans le projet : le logo CodeLab, opaque, en PNG.
    r = client.get("/site/__codelab/icone.png")
    assert r.status_code == 200 and r.data == app.ICONE_ECRAN_ACCUEIL_PNG
    # Un logo PNG depose dans le projet prend la place.
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + b"\x00" * 17
    (projet / "icon.png").write_bytes(png)
    r = client.get("/site/__codelab/icone.png")
    assert r.data == png and r.mimetype == "image/png"
    # Un SVG, qu'iOS ne sait pas afficher, retombe sur le logo CodeLab.
    (projet / "icon.png").unlink()
    (projet / "icon.svg").write_text("<svg/>")
    assert client.get("/site/__codelab/icone.png").data == app.ICONE_ECRAN_ACCUEIL_PNG

    # Application privee : rien sans session, pas meme son nom.
    r = client.get("/prive/__codelab/manifest.webmanifest", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_le_panneau_installe_ouvre_les_applications_sans_en_sortir(client, monkeypatch, tmp_path):
    """Dans le panneau ajoute a l'ecran d'accueil, une application ouverte
    directement vivait sur une autre origine : iOS l'affichait dans son
    navigateur integre (« Done », fleches). Elle s'ouvre maintenant encadree
    dans une page du panneau, qui n'autorise a encadrer QUE l'origine des
    applications."""
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    app.save({"site": {"path": str(tmp_path), "command": "x", "port": 9101,
                       "enabled": True, "titre": "Mon <site>"}})
    monkeypatch.setattr(app, "origines_separees", lambda: True)
    monkeypatch.setattr(app, "origine_applications", lambda: "http://hote:9002")

    # Sans session : rien, pas meme le nom.
    assert client.get("/ouvrir.html?app=site").status_code in (302, 401)

    client.post("/login", json={"password": "secret-de-test"})
    r = client.get("/ouvrir.html?app=site")
    assert r.status_code == 200
    corps = r.get_data(as_text=True)
    assert '<iframe src="http://hote:9002/site/"' in corps
    assert "Mon &lt;site&gt;" in corps and "Mon <site>" not in corps
    # L'onglet porte l'icone de l'application ouverte, pas celle du panneau.
    assert '<link rel="icon" href="/api/icon/site">' in corps
    csp = r.headers["Content-Security-Policy"]
    assert csp.endswith("; frame-src http://hote:9002")
    assert "frame-ancestors 'none'" in csp, "la page elle-meme ne s'encadre pas"
    assert client.get("/ouvrir.html?app=inconnue").status_code == 404

    # Le ruban ramene la FENETRE au hub, pas le cadre.
    with app.flask_app.test_request_context("/site/"):
        assert b'target="_top"' in app.ruban_retour()

    # Le hub n'y passe qu'en mode app.
    html = (_racine_app_manager() / "dashboard.html").read_text(encoding="utf-8")
    assert "display-mode: standalone" in html and "/ouvrir.html?app=" in html


def test_seul_le_panneau_peut_encadrer_une_application(monkeypatch):
    """Clickjacking : aucun autre site ne pose une application dans un cadre.
    Et le panneau installe, qui l'encadre, n'est plus bloque par un
    X-Frame-Options pose d'office par le framework de l'application."""
    monkeypatch.setattr(app, "origines_separees", lambda: True)
    monkeypatch.setattr(app, "PANEL_URL", "https://codelab.exemple.fr")
    with app.flask_app.test_request_context("/site/", base_url="http://10.126.126.1:9002"):
        sortants = app.proteger_l_encadrement(
            [("Content-Type", "text/html"), ("X-Frame-Options", "SAMEORIGIN")])
    assert not [k for k, _ in sortants if k.lower() == "x-frame-options"]
    csp = dict(sortants)["Content-Security-Policy"]
    assert csp == ("frame-ancestors https://codelab.exemple.fr http://10.126.126.1:9001 "
                   "http://localhost:9001 http://127.0.0.1:9001")

    # Une application qui a SA regle frame-ancestors la garde.
    propre = [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")]
    with app.flask_app.test_request_context("/site/"):
        assert app.proteger_l_encadrement(propre) == propre

    # Sans separation des origines, le panneau EST l'origine des applications.
    monkeypatch.setattr(app, "origines_separees", lambda: False)
    with app.flask_app.test_request_context("/site/"):
        assert dict(app.proteger_l_encadrement([]))["Content-Security-Policy"] \
            == "frame-ancestors 'self'"



def test_la_protection_d_encadrement_couvre_toute_reponse_d_application(
        client, monkeypatch, tmp_path):
    """Pages d'erreur et redirections comprises, pas seulement les pages
    relayees depuis l'application."""
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None
    app.save({"site": {"path": str(tmp_path), "command": "x", "port": 9101,
                       "enabled": True}})
    monkeypatch.setattr(app, "is_running", lambda n: False)
    r = client.get("/site/")
    assert r.status_code == 503
    assert "frame-ancestors" in r.headers.get("Content-Security-Policy", "")
    assert "X-Frame-Options" not in r.headers


def test_mailpit_est_habille_aux_couleurs_de_codelab():
    """Mailpit n'est pas modifie : le proxy injecte une feuille de style,
    sert la police du panneau et remplace son logo par le notre."""
    racine = _racine_depot()
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    assert re.search(r"""sub_filter '</head>' '<link rel="stylesheet" href="/_codelab/mailpit\.css\?v=\w+"></head>';""", conf)
    for route in ("location /_codelab/", "location = /mailpit.svg", "location = /favicon.svg"):
        assert route in conf, route
    dockerfile = open(os.path.join(racine, "dagster", "proxy", "Dockerfile"), encoding="utf-8").read()
    assert "COPY dagster/proxy/mailpit-theme/ /usr/share/nginx/codelab/mailpit/" in dockerfile
    assert "manrope-latin.woff2 /usr/share/nginx/codelab/mailpit/manrope.woff2" in dockerfile
    css = open(os.path.join(racine, "dagster", "proxy", "mailpit-theme", "mailpit.css"),
               encoding="utf-8").read()
    # Par les variables de Bootstrap, pas en visant les classes une a une.
    assert "--bs-primary: #0e7c86;" in css and "--bs-primary: #2ab5bf;" in css
    assert 'font-family: "Manrope"' in css and "/_codelab/manrope.woff2" in css
    # Le ruban de retour n'herite pas de l'icone « lien externe » de Mailpit.
    assert "#codelab-retour-hub::after" in css
    # Le meme logo sur la tuile du hub et dans Mailpit.
    logo = open(os.path.join(racine, "dagster", "proxy", "mailpit-theme", "logo.svg"),
                encoding="utf-8").read().strip()
    assert app.ICONE_MAILPIT == logo
    # Un seul fond, le turquoise : l'enveloppe est un trait blanc, sans
    # aplat blanc dessous (comme le logo du diagnostic).
    assert logo.count("<rect") == 2 and 'fill="#fff"' not in logo
    assert 'fill="none" stroke="#fff"' in logo
    # Le logo aussi dans les notifications et sous /favicon.ico, en PNG.
    for route in ("location = /notification.png", "location = /favicon.ico"):
        assert route in conf, route
    png = open(os.path.join(racine, "dagster", "proxy", "mailpit-theme", "logo.png"), "rb").read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # Dans les fenetres (reglages, a propos) : le logo devant le titre, les
    # interrupteurs en liste, des onglets segmentes.
    assert ".modal-title::before" in css and "/_codelab/logo.svg" in css
    assert "#SettingsModal .form-switch" in css
    assert ".modal .nav-tabs" in css


def test_postgres_realigne_le_mot_de_passe_a_chaque_demarrage():
    """credentials.env regenere, donnees gardees : la base doit reprendre le
    mot de passe du fichier, sinon tous les clients sont refuses."""
    racine = _racine_depot()
    script = open(os.path.join(racine, "postgres", "entrypoint.sh"),
                  encoding="utf-8").read()
    fonction = script.split("realigner_mot_de_passe() {", 1)[1].split("\n}\n", 1)[0]
    assert "ALTER ROLE" in fonction and "PASSWORD" in fonction
    # Par l'entree standard, jamais en argument de psql (visible dans ps).
    assert "| psql" in fonction and " -c " not in fonction
    # Apostrophes doublees pour le litteral SQL.
    assert "s/'/''/g" in fonction
    provision = script.split("provision_databases() {", 1)[1].split("\n}\n", 1)[0]
    assert "realigner_mot_de_passe" in provision


def test_seul_un_logo_transparent_a_une_plaque_sur_le_hub():
    """Le logo de Mailpit porte son fond turquoise, comme le diagnostic : pas
    de plaque blanche autour. Celui de Dagster, transparent, garde la sienne."""
    dagster = app._tuile_service(app.DAGSTER_NOM, "", "http://x:3000/", True, 3000)
    mailpit = app._tuile_service(app.MAILPIT_NOM, "", "http://x:8025/", True, 8025)
    assert dagster["plaque"] is True
    assert mailpit["plaque"] is False
    page = _page_panneau("dashboard.html")
    assert "${a.externe && a.plaque ? ' service-stack' : ''}" in page


def test_favicon_ico_est_le_logo_codelab_sur_tous_les_ports():
    """Safari range les icones par nom d'hote, sans le port : /favicon.ico
    rend le logo CodeLab sur le panneau, sur l'origine des applications et
    chez Mailpit -- quoi que Safari retienne pour l'hote, c'est le notre."""
    ancien = app._origines_separees["actif"]
    ancien_apps = app.APPS_PORT
    app._origines_separees["actif"] = True
    app.APPS_PORT = 9002
    try:
        client = app.flask_app.test_client()
        for port in (9001, 9002):
            for chemin in ("/favicon.ico", "/icone-codelab.png"):
                r = client.get(chemin, **_sur_port(port))
                assert r.status_code == 200, (port, chemin)
                assert r.headers["Content-Type"].startswith("image/png")
                assert r.data == app.ICONE_CODELAB_PNG
        assert base64.b64encode(app.ICONE_CODELAB_PNG).decode() in _page_panneau("dashboard.html")
        # Le reste du panneau ne repond toujours pas sur le port des applications.
        assert client.get("/login", **_sur_port(9002)).status_code == 404
        for nom in ("dashboard.html", "login.html"):
            page = _page_panneau(nom)
            assert 'rel="icon" type="image/png" href="data:' not in page, nom
    finally:
        app._origines_separees["actif"] = ancien
        app.APPS_PORT = ancien_apps
    racine = _racine_depot()
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    bloc = conf.split("location = /favicon.ico {", 1)[1].split("}", 1)[0]
    assert "mailpit/codelab.png" in bloc
    png = open(os.path.join(racine, "dagster", "proxy", "mailpit-theme", "codelab.png"), "rb").read()
    assert png == app.ICONE_CODELAB_PNG


def test_le_logo_mailpit_ne_depend_pas_du_cache_de_mailpit():
    """Mailpit sert /mailpit.svg et /favicon.svg « immutable » pour un an :
    l'onglet et la barre passent par une adresse a nous, versionnee."""
    racine = _racine_depot()
    conf = open(os.path.join(racine, "dagster", "proxy", "mailpit.conf"), encoding="utf-8").read()
    css = open(os.path.join(racine, "dagster", "proxy", "mailpit-theme", "mailpit.css"),
               encoding="utf-8").read()
    version = re.search(r'href="/_codelab/logo\.svg\?v=(\w+)"', conf).group(1)
    assert f'''sub_filter 'href="/favicon.svg"' 'href="/_codelab/logo.svg?v={version}"';''' in conf
    assert f'href="/_codelab/mailpit.css?v={version}"' in conf
    assert 'img[src$="mailpit.svg"]' in css and "object-position: -99999px 0;" in css
    assert css.count(f'url("/_codelab/logo.svg?v={version}")') == 2


def test_dagster_garde_un_pilote_postgres_qu_il_sait_ouvrir():
    """SQLAlchemy 2.1 prend psycopg (v3) par defaut pour postgresql://, que
    dagster-postgres n'installe pas : Dagster redemarrait en boucle. L'image
    epingle SQLAlchemy et verifie le pilote AU BUILD."""
    racine = _racine_depot()
    dockerfile = open(os.path.join(racine, "dagster", "Dockerfile"), encoding="utf-8").read()
    assert '"sqlalchemy<2.1"' in dockerfile
    assert "sqlalchemy.create_engine('postgresql://" in dockerfile


# ---------------- invitations, codes par mail, envoi par Mailpit ----------------
#
# Creer un compte, c'est donner une adresse : la personne recoit un lien et
# choisit elle-meme son nom et son mot de passe. Le second facteur n'exige
# plus d'application : un code part par mail. Et les mails du panneau
# passent par Mailpit, comme ceux des applications.


def _jeton_du_lien(partis):
    return re.search(r"/invitation/([A-Za-z0-9_-]+)", partis[-1][1]).group(1)


@pytest.fixture
def invitations(comptes_mail):
    c, partis = comptes_mail
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_ADMIN, utilisateur=app.NOM_ADMIN,
                 ouverte=int(time.time()), jeton="j")
    return c, partis, {app.JETON_ENTETE: "j"}


def test_une_invitation_envoie_un_lien_et_le_rend_a_l_administrateur(invitations):
    c, partis, h = invitations
    r = c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    assert r.status_code == 200, r.data
    d = r.get_json()
    assert d["envoye"] is True and partis[-1][0] == ["paul@example.com"]
    assert d["lien"].endswith("/invitation/" + _jeton_du_lien(partis))
    # Seule l'empreinte est rangee : le jeton ne se relit nulle part.
    assert _jeton_du_lien(partis) not in json.dumps(app.reglage("invitations"))
    assert [i["email"] for i in c.get("/api/invitations").get_json()["invitations"]] \
        == ["paul@example.com"]


def test_accepter_l_invitation_cree_le_compte_verifie_et_ouvre_la_session(invitations):
    c, partis, h = invitations
    c.post("/api/invitations", json={"email": "paul@example.com"}, headers=h)
    jeton = _jeton_du_lien(partis)
    c.post("/logout")
    assert c.get("/api/invitation/" + jeton).get_json()["email"] == "paul@example.com"
    r = c.post("/invitation/" + jeton, json={"nom": "paul", "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 200, r.data
    compte = app.lire_utilisateurs()["paul"]
    assert compte["email"] == "paul@example.com" and compte["email_verifie"] is True
    assert c.get("/api/mes-apps").status_code == 200
    # Le lien ne sert qu'une fois.
    c.post("/logout")
    assert c.post("/invitation/" + jeton, json={"nom": "autre",
                  "mot_de_passe": "mot-de-passe-long"}).status_code == 404


def test_un_lien_d_invitation_invente_ne_cree_rien(invitations):
    c, _, _ = invitations
    c.post("/logout")
    r = c.post("/invitation/" + "x" * 43, json={"nom": "intrus",
                                                "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 404
    assert "intrus" not in app.lire_utilisateurs()


def test_une_invitation_annulee_ne_vaut_plus_rien(invitations):
    c, partis, h = invitations
    ident = c.post("/api/invitations", json={"email": "paul@example.com"},
                   headers=h).get_json()["id"]
    jeton = _jeton_du_lien(partis)
    assert c.delete("/api/invitations/" + ident, headers=h).status_code == 200
    assert c.get("/api/invitation/" + jeton).status_code == 404


def test_un_compte_verifie_recoit_son_code_par_mail_et_entre_avec(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    d = r.get_json()
    assert r.status_code == 401 and d["moyen"] == "email" and d["envoye"] is True
    assert "marie@example.com" not in json.dumps(d), "l'adresse ne doit pas s'afficher en entier"
    assert partis[-1][0] == ["marie@example.com"]
    assert c.get("/api/mes-apps").status_code == 401
    app._login_attempts.clear()
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                               "code": _code_du_dernier_mail(partis)})
    assert r.status_code == 200, r.data
    assert c.get("/api/mes-apps").status_code == 200


def test_le_code_de_connexion_ne_sert_qu_une_fois_et_exige_le_mot_de_passe(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    code = _code_du_dernier_mail(partis)
    app._login_attempts.clear()
    assert c.post("/login", json={"nom": "marie", "password": "faux",
                                  "code": code}).status_code == 401
    assert c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                  "code": code}).status_code == 200
    c.post("/logout")
    app._login_attempts.clear()
    assert c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long",
                                  "code": code}).status_code == 401


def test_les_mails_du_panneau_passent_par_mailpit(monkeypatch):
    remis = []
    monkeypatch.setattr(app, "MAIL_PAR_MAILPIT", True)
    monkeypatch.setattr(app, "_envoyer_par_mailpit", lambda msg: remis.append(msg))
    direct = []
    monkeypatch.setattr(app.smtplib, "SMTP", lambda *a, **k: direct.append(a))
    app.envoyer_mail({"host": "smtp.example.com", "port": 587, "tls": "starttls",
                      "user": "", "password": "", "expediteur": "moi@example.com"},
                     "sujet", "corps", destinataires=["toi@example.com"])
    assert remis and remis[0]["To"] == "toi@example.com" and not direct


def test_mailpit_injoignable_l_envoi_repart_en_direct(monkeypatch):
    def refuse(msg):
        raise ConnectionRefusedError("arrete")
    monkeypatch.setattr(app, "MAIL_PAR_MAILPIT", True)
    monkeypatch.setattr(app, "_envoyer_par_mailpit", refuse)
    envois = []

    class FauxSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): envois.append(msg)

    monkeypatch.setattr(app.smtplib, "SMTP", FauxSMTP)
    app.envoyer_mail({"host": "smtp.example.com", "port": 587, "tls": "starttls",
                      "user": "", "password": "", "expediteur": "moi@example.com"},
                     "sujet", "corps", destinataires=["toi@example.com"])
    assert len(envois) == 1


# ---------- option A : la boite mail seule ne prend pas un compte ----------
#
# Le mail sert deja de second facteur (code de connexion). S'il servait AUSSI a
# reinitialiser le mot de passe, qui tient la boite tiendrait le compte. D'ou :
# reinitialisation par mail reservee aux comptes qui ont Face ID ou une
# application, et code par mail suspendu apres une reinitialisation.

def _reinitialiser_par_mail(c, partis, nouveau="nouveau-mot-de-passe"):
    c.post("/mot-de-passe/oubli", json={"nom": "marie"})
    code = _code_du_dernier_mail(partis)
    app._login_attempts.clear()
    return c.post("/mot-de-passe/reinitialiser",
                  json={"nom": "marie", "code": code, "nouveau": nouveau})


def test_sans_facteur_fort_la_reinitialisation_par_mail_est_fermee(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse(face_id=False)
    assert c.post("/mot-de-passe/oubli", json={"nom": "marie"}).status_code == 200
    assert partis == [], "un code de reinitialisation est parti sans facteur fort"


def test_apres_une_reinitialisation_par_mail_le_code_mail_ne_suffit_plus(comptes_mail):
    """Le scenario de la boite mail volee : mot de passe reinitialise par mail,
    puis tentative d'entrer avec le code... envoye par mail."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    assert _reinitialiser_par_mail(c, partis).status_code == 200
    app._login_attempts.clear()
    nb = len(partis)
    r = c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe",
                               "moyen": "email"})
    assert r.status_code == 403, r.data
    assert len(partis) == nb, "un code de connexion est parti par mail"
    assert c.get("/api/mes-apps").status_code == 401


def test_apres_reinitialisation_aucune_application_ne_s_inscrit_sur_le_mot_de_passe(comptes_mail):
    """Sans ce verrou, le mot de passe reinitialise suffirait a enregistrer
    une application d'authentification -- et a entrer."""
    c, partis = comptes_mail
    _compte_avec_adresse()
    _reinitialiser_par_mail(c, partis)
    app._login_attempts.clear()
    r = c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe"})
    assert r.status_code == 403 and not r.get_json().get("inscription")


def test_un_facteur_fort_rend_le_code_par_mail(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    comptes = app.lire_utilisateurs()
    comptes["marie"]["totp"] = "JBSWY3DPEHPK3PXP"
    app.ecrire_utilisateurs(comptes)
    _reinitialiser_par_mail(c, partis)
    assert app.lire_utilisateurs()["marie"]["code_mail_suspendu"] is True
    app._login_attempts.clear()
    d = c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe"}).get_json()
    assert d["moyens"] == ["totp"]
    code = app.totp_code("JBSWY3DPEHPK3PXP", int(time.time()) // app.TOTP_PAS)
    assert c.post("/login", json={"nom": "marie", "password": "nouveau-mot-de-passe",
                                  "code": code}).status_code == 200
    assert not app.lire_utilisateurs()["marie"].get("code_mail_suspendu")


def test_une_adresse_verifiee_sans_serveur_d_envoi_n_ouvre_pas_l_inscription(comptes_mail, tmp_path):
    """Serveur d'envoi en panne : le compte ne doit pas retomber sur
    l'inscription d'une application, qui n'exige que le mot de passe."""
    c, _ = comptes_mail
    _compte_avec_adresse(face_id=False)
    (tmp_path / "credentials.env").write_text("")
    r = c.post("/login", json={"nom": "marie", "password": "mot-de-passe-long"})
    assert r.status_code == 403 and not r.get_json().get("inscription")


def test_un_compte_suspendu_n_entre_pas_par_face_id(comptes_mail, monkeypatch):
    """Le mot de passe refusait deja un compte suspendu ; la cle d'acces, non."""
    pytest.importorskip("webauthn")
    import webauthn
    c, _ = comptes_mail
    _compte_avec_adresse()
    comptes = app.lire_utilisateurs()
    comptes["marie"]["suspendu"] = True
    app.ecrire_utilisateurs(comptes)
    monkeypatch.setattr(app, "passkey_contexte",
                        lambda: ("codelab.exemple.fr", "https://codelab.exemple.fr", ""))

    class Verifiee:
        new_sign_count = 1
    monkeypatch.setattr(webauthn, "verify_authentication_response", lambda **k: Verifiee())
    with c.session_transaction() as sess:
        sess["passkey_defi"] = base64.b64encode(b"defi").decode()
    r = c.post("/login/passkey", json={"credential": {"id": "CLE"}})
    assert r.status_code == 403, r.data
    assert c.get("/api/mes-apps").status_code == 401


# ---------- un changement de mot de passe ferme les sessions ouvertes ----------

def test_un_nouveau_mot_de_passe_pose_par_l_admin_ferme_la_session_volee(deux_espaces):
    vol = app.flask_app.test_client()
    _connecte(vol, "marie", "mot-de-passe-long")
    assert vol.get("/api/mes-apps").status_code == 200
    time.sleep(1.1)
    comptes = app.lire_utilisateurs()
    app.fermer_les_autres_sessions(comptes["marie"])
    app.ecrire_utilisateurs(comptes)
    assert vol.get("/api/mes-apps").status_code == 401


def test_changer_son_mot_de_passe_garde_sa_session_et_ferme_les_autres(deux_espaces):
    c = deux_espaces
    autre = app.flask_app.test_client()
    _connecte(c, "marie", "mot-de-passe-long")
    _connecte(autre, "marie", "mot-de-passe-long")
    time.sleep(1.1)
    jeton = c.get("/").get_data(as_text=True)
    jeton = re.search(r"const JETON=(\"[^\"]+\")", jeton).group(1).strip('"')
    r = c.post("/api/compte/mot-de-passe",
               json={"ancien": "mot-de-passe-long", "nouveau": "un-tout-nouveau-mot-de-passe"},
               headers={app.JETON_ENTETE: jeton})
    assert r.status_code == 200, r.data
    assert c.get("/api/mes-apps").status_code == 200
    assert autre.get("/api/mes-apps").status_code == 401


def test_une_reinitialisation_par_mail_ferme_les_sessions_ouvertes(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    vol = app.flask_app.test_client()
    with vol.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()) - 10, jeton="j")
    assert vol.get("/api/mes-apps").status_code == 200
    assert _reinitialiser_par_mail(c, partis).status_code == 200
    assert vol.get("/api/mes-apps").status_code == 401


def test_un_cookie_ancien_ne_suffit_pas_a_ajouter_une_cle_ni_changer_d_adresse(comptes_mail):
    """Une session volee ne doit pas pouvoir s'installer durablement : ajouter
    sa propre cle Face ID, ou detourner l'adresse qui recoit les codes."""
    c, _ = comptes_mail
    _compte_avec_adresse()
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()) - app.AUTHENTIFICATION_RECENTE - 60, jeton="j")
    h = {app.JETON_ENTETE: "j"}
    r = c.post("/api/mon-compte/email", json={"email": "pirate@example.com"}, headers=h)
    assert r.status_code == 403 and r.get_json()["reconnexion"] is True
    assert app.lire_utilisateurs()["marie"]["email"] == "marie@example.com"
    if app.passkeys_disponibles():
        r = c.post("/api/mon-compte/passkeys/options", headers=h)
        assert r.status_code in (400, 403)


def test_juste_apres_la_connexion_l_adresse_se_change(comptes_mail):
    c, partis = comptes_mail
    _compte_avec_adresse()
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur="marie",
                 ouverte=int(time.time()), jeton="j")
    r = c.post("/api/mon-compte/email", json={"email": "nouvelle@example.com"},
               headers={app.JETON_ENTETE: "j"})
    assert r.status_code == 200, r.data
    assert partis[-1][0] == ["nouvelle@example.com"]


# ---------- Dagster et Mailpit : a l'administrateur, et a personne d'autre ----------
#
# Dagster execute du code sur la machine ; Mailpit montre tous les mails,
# codes de connexion et liens d'invitation compris. Ni l'un ni l'autre ne
# doit jamais s'ouvrir a un compte utilisateur, par aucun chemin.

def _session_utilisateur(c, nom="marie"):
    with c.session_transaction() as s:
        s.update(authed=True, role=app.ROLE_UTILISATEUR, utilisateur=nom,
                 ouverte=int(time.time()), jeton="j")


def test_le_proxy_de_dagster_et_mailpit_refuse_un_compte_utilisateur(hub_dagster):
    """C'est /api/auth-check que nginx interroge avant CHAQUE requete vers
    Dagster (port 3000) et Mailpit (port 8025)."""
    c = hub_dagster
    assert c.get("/api/auth-check").status_code == 401          # anonyme
    _session_utilisateur(c)
    assert c.get("/api/auth-check").status_code == 401          # utilisateur
    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"})
    assert c.get("/api/auth-check").status_code == 204          # administrateur


def test_aucune_tuile_dagster_ni_mailpit_pour_un_compte_utilisateur(hub_dagster, monkeypatch):
    monkeypatch.setattr(app, "mailpit_repond", lambda: True)
    c = hub_dagster
    _session_utilisateur(c)
    d = c.get("/api/mes-apps").get_json()
    noms = {a["name"] for a in d.get("apps", []) + d.get("services", [])}
    assert not noms & {app.DAGSTER_NOM, app.MAILPIT_NOM}, noms


def test_dagster_et_mailpit_ne_s_accordent_a_aucun_compte(hub_dagster):
    c = hub_dagster
    c.post("/login", json={"password": "secret-de-test"})
    for service in (app.DAGSTER_NOM, app.MAILPIT_NOM):
        r = c.put(f"/api/apps/{service}/acces", json={"utilisateurs": ["marie"]},
                  headers={app.JETON_ENTETE: c.get("/").get_data(as_text=True)
                           .split("const JETON=\"")[1].split("\"")[0]})
        assert r.status_code in (403, 404), (service, r.data)
    assert app.lire_utilisateurs()["marie"]["projets"] == ["site"]


# ---------- le compte d'administration : second facteur obligatoire ----------

@pytest.fixture
def admin_2fa(journal, monkeypatch):
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "1")
    monkeypatch.setattr(app, "_totp_secret", "")
    monkeypatch.setattr(app, "ecrire_bloc_panneau", lambda pw, key, totp: True)
    return journal


def test_le_mot_de_passe_admin_seul_n_ouvre_pas_de_session(admin_2fa):
    c = admin_2fa
    r = c.post("/login", json={"password": "secret-de-test"})
    assert r.status_code == 200 and r.get_json()["inscription"] is True
    assert c.get("/api/apps").status_code == 401
    assert c.get("/api/auth-check").status_code == 401


def test_l_admin_enregistre_son_facteur_puis_doit_le_donner(admin_2fa):
    c = admin_2fa
    secret = c.post("/login", json={"password": "secret-de-test"}).get_json()["secret"]
    code = app.totp_code(secret, int(time.time()) // app.TOTP_PAS)
    assert c.post("/login/second-facteur", json={"code": code}).status_code == 200
    assert app._totp_secret == secret
    assert c.get("/api/apps").status_code == 200
    c.post("/logout")
    app._login_attempts.clear()
    r = c.post("/login", json={"password": "secret-de-test"})
    assert r.status_code == 401 and r.get_json()["totp"] is True


def test_un_admin_avec_face_id_seulement_ne_passe_pas_au_mot_de_passe(admin_2fa):
    """Sinon le mot de passe seul permettrait d'enregistrer une application,
    et de contourner Face ID."""
    app.ecrire_passkeys({app.NOM_ADMIN: [{"id": "A", "cle_publique": "AAAA",
                                          "compteur": 0, "nom": "Face ID"}]})
    c = admin_2fa
    r = c.post("/login", json={"password": "secret-de-test"})
    assert r.status_code == 403 and not r.get_json().get("inscription")
    assert c.get("/api/apps").status_code == 401


def _inscription_face_id(monkeypatch):
    """Un contexte HTTPS, et un navigateur dont la cle est toujours valide."""
    pytest.importorskip("webauthn")
    import webauthn
    monkeypatch.setattr(app, "passkey_contexte",
                        lambda: ("codelab.exemple.fr", "https://codelab.exemple.fr", ""))

    class Verifiee:
        credential_id = b"cle-admin"
        credential_public_key = b"publique"
        sign_count = 0
    monkeypatch.setattr(webauthn, "verify_registration_response", lambda **k: Verifiee())


def test_premiere_connexion_admin_par_face_id_sans_rien_recopier(admin_2fa, monkeypatch):
    """Le mot de passe reconnu, Face ID s'enregistre en un geste et ouvre la
    session : aucune cle ni aucun code a recopier."""
    _inscription_face_id(monkeypatch)
    c = admin_2fa
    d = c.post("/login", json={"password": "secret-de-test"}).get_json()
    assert d["inscription"] is True and d["passkey"] is True
    assert d["uri"].startswith("otpauth://totp/")
    r = c.post("/login/inscription/passkey/options")
    assert r.status_code == 200 and r.get_json()["user"]["name"] == app.NOM_ADMIN
    r = c.post("/login/inscription/passkey", json={"credential": {"id": "x"}, "nom": "Face ID"})
    assert r.status_code == 200, r.data
    assert [k["nom"] for k in app.passkeys_du_compte(app.NOM_ADMIN)] == ["Face ID"]
    assert c.get("/api/apps").status_code == 200
    # Une seule fois : le facteur existe, la porte est refermee.
    c.post("/logout")
    with c.session_transaction() as sess:
        sess["totp_inscription"] = app.NOM_ADMIN
        sess["inscription_depuis"] = int(time.time())
    assert c.post("/login/inscription/passkey/options").status_code == 409


def test_face_id_a_l_inscription_exige_le_mot_de_passe_juste_avant(admin_2fa, monkeypatch):
    """Sans inscription en attente -- ou trop ancienne -- rien ne s'enregistre."""
    _inscription_face_id(monkeypatch)
    c = admin_2fa
    assert c.post("/login/inscription/passkey/options").status_code == 400
    assert c.post("/login/inscription/passkey", json={"credential": {}}).status_code == 400
    c.post("/login", json={"password": "faux-mot-de-passe"})
    assert c.post("/login/inscription/passkey/options").status_code == 400
    app._login_attempts.clear()
    c.post("/login", json={"password": "secret-de-test"})
    with c.session_transaction() as sess:
        sess["inscription_depuis"] = int(time.time()) - app.INSCRIPTION_VALIDITE - 1
    assert c.post("/login/inscription/passkey/options").status_code == 400
    assert not app.passkeys_du_compte(app.NOM_ADMIN)
    assert c.get("/api/apps").status_code == 401


def test_face_id_a_l_inscription_reste_dans_le_reseau_local(admin_2fa, monkeypatch):
    _inscription_face_id(monkeypatch)
    c = admin_2fa
    c.post("/login", json={"password": "secret-de-test"})
    monkeypatch.setattr(app, "refus_admin_hors_reseau", lambda: "Hors du réseau local.")
    assert c.post("/login/inscription/passkey/options").status_code == 403
    assert c.post("/login/inscription/passkey", json={"credential": {}}).status_code == 403
    assert not app.passkeys_du_compte(app.NOM_ADMIN)


def test_la_page_de_connexion_propose_face_id_puis_un_lien_prerempli():
    page = _page_panneau("login.html")
    assert "/login/inscription/passkey/options" in page and "inscrirePasskey()" in page
    # Le lien otpauth:// ouvre l'application de codes avec tout deja rempli.
    assert "$$('ins-lien').href=d.uri" in page
    assert "Saisir la clé à la main" in page


def test_les_parametres_n_ont_plus_de_posture_de_securite():
    page = _page_panneau("dashboard.html")
    for trace in ("Posture de sécurité", "sec-liste", "peindrePosture",
                  "chargerPostureDuCompte", "POSTURE_"):
        assert trace not in page, trace


def test_la_marche_arriere_du_compose_rouvre_le_mot_de_passe(admin_2fa, monkeypatch):
    monkeypatch.setenv("APP_MANAGER_ADMIN_2FA", "0")
    assert admin_2fa.post("/login", json={"password": "secret-de-test"}).status_code == 200


def test_une_connexion_admin_depuis_une_adresse_inconnue_est_signalee(journal, monkeypatch):
    envois = []
    monkeypatch.setattr(app, "alertes_admin", lambda: ["moi@example.com"])
    monkeypatch.setattr(app, "envoyer_avec_repli",
                        lambda sujet, corps, dest: envois.append((sujet, dest)))

    class ThreadImmediat:
        def __init__(self, target, args=(), daemon=None):
            self.t, self.a = target, args
        def start(self):
            self.t(*self.a)
    monkeypatch.setattr(app.threading, "Thread", ThreadImmediat)
    monkeypatch.setattr(app, "trust_proxy", lambda: True)
    c = journal
    c.post("/login", json={"password": "secret-de-test"},
           headers={"X-Forwarded-For": "192.168.1.10"})       # l'installation
    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"},
           headers={"X-Forwarded-For": "192.168.1.10"})       # deja vue
    assert envois == []
    c.post("/logout")
    c.post("/login", json={"password": "secret-de-test"},
           headers={"X-Forwarded-For": "192.168.1.77"})       # nouvelle
    assert len(envois) == 1 and envois[0][1] == ["moi@example.com"]



# ------------------- la base du panneau (SQLite) -------------------
#
# Les donnees vivaient dans une quinzaine de fichiers JSON ; elles vivent dans
# codelab.db, une table par sujet. Ce qui doit rester vrai : l'import des
# anciens fichiers ne perd rien et ne se rejoue pas, les originaux sont gardes
# (sans adresse en clair), le schema est versionne, et le diagnostic lit la
# base sans pouvoir l'ecrire.

def _anciens_fichiers():
    etat = app.STATE_DIR
    ecrire = lambda nom, donnee: open(os.path.join(etat, nom), "w").write(
        json.dumps(donnee, ensure_ascii=False))
    ecrire("apps.json", {"site": {"path": "/w/s", "command": "x", "port": 9101,
                                  "enabled": True, "visibility": "privee"},
                         "blog": {"path": "/w/b", "command": "y", "port": 9102}})
    ecrire("utilisateurs.json", {"marie": {"sel": "aa", "hash": "bb", "projets": ["site"],
                                           "email": "marie@x.fr", "email_verifie": True,
                                           "totp": "SECRET"}})
    ecrire("passkeys.json", {"marie": [{"id": "K1", "cle_publique": "AAAA", "compteur": 3,
                                        "nom": "Face ID", "cree": 1, "dernier": 2}]})
    ecrire("categories.json", ["Outils", "Sites"])
    ecrire("masquees.json", {"marie": ["blog"]})
    ecrire("exposition.json", {"adresse_publique": "https://x.fr"})
    open(os.path.join(etat, "acces.jsonl"), "w").write(
        json.dumps({"id": "e1", "ts": 100, "genre": "connexion", "qui": "marie",
                    "ip": "10.0.0.1", "moyen": "mot de passe"}) + "\n")


def test_l_import_verse_chaque_ancien_fichier_dans_sa_table():
    _anciens_fichiers()
    faits = app.importer_anciens_fichiers()
    assert faits
    apps = app.load()
    assert list(apps) == ["site", "blog"]
    assert apps["site"]["enabled"] is True and apps["site"]["visibility"] == "privee"
    marie = app.lire_utilisateurs()["marie"]
    assert marie["projets"] == ["site"] and marie["totp"] == "SECRET"
    assert marie["email"] == "marie@x.fr" and marie["email_verifie"] is True
    assert app.lire_passkeys()["marie"][0]["compteur"] == 3
    assert app.lire_categories() == ["Outils", "Sites"]
    assert app.lire_masquees() == {"marie": ["blog"]}
    assert app.lire_exposition()["adresse_publique"] == "https://x.fr"
    e = app.lire_acces()[0]
    assert e["qui"] == "marie" and e["moyen"] == "mot de passe"


def test_l_import_garde_les_originaux_sans_adresse_en_clair():
    _anciens_fichiers()
    app.importer_anciens_fichiers()
    etat = app.STATE_DIR
    assert not os.path.exists(os.path.join(etat, "apps.json"))
    assert os.path.exists(os.path.join(etat, "apps.json.migre"))
    garde = open(os.path.join(etat, "utilisateurs.json.migre")).read()
    assert "marie@x.fr" not in garde and "bb" in garde
    assert b"marie@x.fr" not in _octets_de_l_etat()


def test_un_import_rejoue_n_ecrase_rien():
    _anciens_fichiers()
    app.importer_anciens_fichiers()
    app.save({"nouvelle": {"path": "/w/n", "command": "z", "port": 9103}})
    # Un vieux fichier reapparu (restauration partielle) ne remplace pas ce
    # que la base porte deja.
    open(os.path.join(app.STATE_DIR, "apps.json"), "w").write('{"vieille": {}}')
    app.importer_anciens_fichiers()
    assert list(app.load()) == ["nouvelle"]


def test_le_schema_est_versionne_et_la_base_reservee_au_panneau():
    import sqlite3
    app.load()
    cx = sqlite3.connect(app.DB_FILE)
    assert cx.execute("PRAGMA user_version").fetchone()[0] == app.stockage.VERSION
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"applications", "comptes", "acces_applications", "cles_acces", "categories",
            "masquees", "journal", "messages", "processus", "parametres",
            "reglages_chiffres"} <= tables
    cx.close()
    assert stat.S_IMODE(os.stat(app.DB_FILE).st_mode) == 0o600


def test_supprimer_un_compte_retire_aussi_ses_droits():
    app.ecrire_utilisateurs({"marie": {"sel": "a", "hash": "b", "projets": ["site"]},
                             "paul": {"sel": "c", "hash": "d", "projets": ["site"]}})
    comptes = app.lire_utilisateurs()
    comptes.pop("marie")
    app.ecrire_utilisateurs(comptes)
    lignes = app.stockage.lire(app.DB_FILE, "SELECT compte FROM acces_applications")
    assert [l["compte"] for l in lignes] == ["paul"]


def test_le_diagnostic_lit_la_base_en_lecture_seule(monkeypatch):
    monkeypatch.setenv("APP_MANAGER_STATE", app.STATE_DIR)
    app.save({"site": {"path": "/w/s", "command": "x", "port": 9101,
                       "visibility": "publique"}})
    app.ecrire_utilisateurs({"marie": {"sel": "a", "hash": "b", "projets": []}})
    app.ecrire_exposition({"admin_reseau_local": True})
    app.journaliser("connexion", qui="marie", ip="203.0.113.9")
    assert checks._apps_panneau()["site"]["visibility"] == "publique"
    assert checks._comptes_panneau() == {"marie": {}}
    assert checks._exposition_panneau() == {"admin_reseau_local": True}
    assert checks._ips_du_journal() == ["203.0.113.9"]
    # Lecture seule : une ecriture par cette voie est refusee.
    assert checks._requete_panneau("DELETE FROM applications") is None
    assert list(app.load()) == ["site"]



def test_le_panneau_declare_toujours_sa_propre_icone():
    """Safari garde une icone par nom d'hote : celle d'une application ou de
    Mailpit (meme hote, autre port) ne doit pas reprendre l'onglet du
    panneau. Chaque page du panneau declare donc la sienne, versionnee."""
    for nom in ("dashboard.html", "login.html"):
        page = _page_panneau(nom)
        # Une adresse qu'aucun autre service du meme hote ne sert.
        assert 'rel="icon" type="image/png" sizes="128x128" href="/icone-codelab.png?v=4"' in page, nom
        assert 'href="/apple-touch-icon.png?v=4"' in page, nom
        assert "/favicon.png?v=" not in page and "/favicon.ico?v=" not in page, nom
        # Au retour sur l'onglet, les balises sont reposees.
        assert "window.addEventListener('pageshow', reposerIcone);" in page, nom


def test_les_connexions_recentes_se_filtrent_et_restent_courtes():
    page = _page_panneau("dashboard.html")
    assert 'id="ac-genre"' in page and "AC_MAX=200" in page
    # 10 par defaut : la liste ne deborde plus a l'ouverture.
    assert "return 10;" in page



def test_sur_telephone_le_menu_est_ferme_et_s_ouvre_par_dessus():
    """Sur un telephone, la colonne de gauche prenait la moitie de l'ecran."""
    page = _page_panneau("dashboard.html")
    assert "@media (max-width:760px)" in page
    assert ".sidebar.ouvert{transform:none}" in page
    assert "function fermerMenuMobile()" in page
    # Changer de rubrique referme le menu.
    debut = page.index("function showSection(name){")
    assert "fermerMenuMobile();" in page[debut:debut + 800]


def test_sur_telephone_le_hub_est_une_grille_d_icones():
    page = _page_panneau("dashboard.html")
    assert ".hub-liste.compacte{grid-template-columns:repeat(4,minmax(0,1fr))" in page


def test_la_page_de_connexion_ne_montre_pas_credentials_sur_telephone():
    page = _page_panneau("login.html")
    assert "@media (max-width:760px){.pied{display:none}}" in page

"""
Sondes de diagnostic CodeLab -- partagees entre l'application web et l'asset
Dagster, pour que les deux racontent exactement la meme chose.

Uniquement la bibliotheque standard ici. Le pilote Postgres est resolu par
connect_pg(), qui accepte psycopg (v3, installe par la commande de build de
l'application) ou psycopg2 (deja present dans l'image Dagster via
dagster-postgres).
"""
import base64
import importlib.util
import queue
import re
import secrets
import sys

# --------------------------------------------------------------------------
# pytest, quand il est la.
#
# Ce fichier est importe par DEUX mondes qui n'ont pas les memes paquets :
#
#   - pytest, qui collecte la suite de regression du panneau, plus bas ;
#   - Dagster, qui ne vient chercher ici que les SONDES -- et dont l'image ne
#     contient pas pytest, puisqu'elle n'a aucune raison d'embarquer un
#     lanceur de tests pour faire tourner des jobs.
#
# Sans ce repli, "import pytest" en tete de fichier faisait disparaitre le
# projet diagnostic de Dagster : definitions.py ignore un projet qui ne se
# charge pas, et le disait dans ses journaux -- l'asset de diagnostic n'etait
# simplement plus la. Le remplacant ne sert qu'a laisser les decorateurs
# s'evaluer a l'import ; il ne sait pas lancer un test, et le dit s'il est
# sollicite.
try:
    import pytest
except ModuleNotFoundError:  # image Dagster, image dev
    class _MarqueursAbsents:
        def skipif(self, *_args, **_kwargs):
            return lambda fonction: fonction

    class _PytestAbsent:
        mark = _MarqueursAbsents()

        def fixture(self, *args, **kwargs):
            # Accepte les deux ecritures : @fixture et @fixture(autouse=True).
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return lambda fonction: fonction

        def __getattr__(self, nom):
            raise RuntimeError(
                "pytest n'est pas installe dans cette image. Les sondes de "
                "checks.py fonctionnent sans lui ; sa suite de tests, non "
                "(pytest.%s). Elle se lance depuis le panneau ou en CI."
                % nom)

    pytest = _PytestAbsent()

import json
import os
import socket
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

ENV_FILE = os.environ.get("CODELAB_ENV_FILE", "/var/lib/codelab/config/credentials.env")
# Configuration propre au projet : elle s'ajoute a credentials.env et le
# remplace en cas de doublon. Un projet copie emporte donc sa configuration
# avec lui, sans rien devoir ajouter au fichier commun.
PROJET_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
WORKSPACE = os.environ.get("APP_MANAGER_ROOT", "/workspace")
# Dans app-manager comme dans dagster, le volume config est monte au meme
# endroit : les cles SSH sont donc visibles a cote de credentials.env.
SSH_DIR = os.environ.get("CODELAB_SSH_DIR") or os.path.join(os.path.dirname(ENV_FILE), "ssh")
# uid de l'utilisateur SSH du conteneur dev, tel que vu depuis les autres
# conteneurs (le volume est partage, les uid sont les memes).
SSH_UID = int(os.environ.get("CODELAB_SSH_UID", "1000"))

# Une base Postgres par projet, nommee comme le dossier du projet, avec un
# schema "dagster" dedans. Les tables d'un projet ne peuvent donc pas entrer
# en collision avec celles d'un autre, et "DROP DATABASE diagnostic" suffit a
# tout nettoyer sans risquer d'emporter les donnees du voisin.
#
# Il n'y a pas de base fourre-tout : la base "dagster" ne contient que les
# tables d'instance de Dagster (runs, evenements, planifications). La base
# "postgres" livree par initdb, elle, est supprimee au demarrage du serveur --
# aucun service CodeLab ne s'y connecte.
TABLE = "codelab_diagnostic"
# PROJET, DB, SCHEMA et TABLE_QUALIFIEE sont definis plus bas, apres read_env :
# leurs valeurs se lisent dans le .env du projet.
PROJET = os.path.basename(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------ credentials.env ------------------------------

def _lire_fichier(chemin):
    """Lit un fichier KEY=VALUE. Dict vide si absent ou illisible.

    Volontairement tolerant : un fichier manquant ou une ligne malformee ne
    doit pas empecher le projet de demarrer. Les guillemets entourant une
    valeur sont retires -- on les ecrit par reflexe, et les garder donnerait un
    mot de passe faux, avec une erreur trompeuse a l'autre bout.
    """
    valeurs = {}
    try:
        with open(chemin) as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, _, valeur = ligne.partition("=")
                valeur = valeur.strip()
                if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
                    valeur = valeur[1:-1]
                # Derniere occurrence gagnante : dans credentials.env, chaque
                # service reecrit son bloc en fin de fichier, donc une valeur
                # laissee plus haut est forcement perimee.
                valeurs[cle.strip()] = valeur
    except OSError:
        pass
    return valeurs


def read_env(key, env_file=None):
    """Lit une cle de configuration, en trois couches.

    De la plus faible a la plus forte :

      1. l'environnement du conteneur, pose par le docker-compose ;
      2. credentials.env, commun a toute la stack ;
      3. PROJET/.env, propre a ce projet -- il gagne toujours.

    C'est la troisieme couche qui permet de pointer ce projet-ci sur une autre
    base, ou de lui donner son propre jeton d'API, sans toucher au fichier
    partage par tous les services.

    Une valeur vide ("CLE=" dans le fichier) est traitee comme absente : c'est
    la forme que prend un placeholder qu'on a oublie de remplir, et la
    confondre avec une valeur valide donne des erreurs bien plus obscures en
    aval.
    """
    if env_file:
        # Chemin explicite : les tests veulent lire un fichier precis sans que
        # le .env du projet vienne s'y superposer.
        return _lire_fichier(env_file).get(key) or None

    valeur = os.environ.get(key)
    for fichier in (ENV_FILE, PROJET_ENV):
        trouvee = _lire_fichier(fichier).get(key)
        if trouvee:
            valeur = trouvee
    return valeur or None


# Base du projet. CODELAB_DB (dans le .env du projet) l'emporte ; a defaut,
# c'est le nom du dossier -- un projet copie sous un autre nom vise donc sa
# propre base sans qu'on ait rien a editer.
#
# Surtout PAS POSTGRES_DB : cette cle, publiee dans credentials.env, designe
# la base d'instance de Dagster. Un projet qui ecrirait dedans melangerait ses
# tables avec les runs et les evenements.
DB = read_env("CODELAB_DB") or PROJET
# Base d'instance de Dagster. Elle existe toujours, donc elle sert de point
# d'entree pour creer la base du projet : on ne peut pas creer une base
# depuis elle-meme.
DB_INSTANCE = read_env("POSTGRES_DB") or "dagster"
# Nom du schema, identique dans toutes les bases de projet. Le qualifier
# explicitement dans les requetes -- plutot que de se reposer sur le
# search_path -- evite qu'une table homonyme d'un autre schema soit atteinte
# par erreur.
SCHEMA = read_env("CODELAB_SCHEMA") or "dagster"
TABLE_QUALIFIEE = f"{SCHEMA}.{TABLE}"


def pg_settings(env_file=None, dbname=None):
    return {
        "host": read_env("POSTGRES_HOST", env_file) or "codelab-postgres",
        "port": int(read_env("POSTGRES_PORT", env_file) or 5432),
        "dbname": dbname or read_env("CODELAB_DB", env_file) or DB,
        "user": read_env("POSTGRES_USER", env_file) or "codelab",
        "password": read_env("POSTGRES_PASSWORD", env_file),
    }


# --------------------------------- Postgres ---------------------------------

class PiloteAbsent(RuntimeError):
    """Ni psycopg ni psycopg2 : ce n'est ni un probleme de reseau ni de mot de
    passe, et le message doit le dire clairement."""


def pilote_pg():
    try:
        import psycopg
        return psycopg, "psycopg (v3)"
    except ImportError:
        pass
    try:
        import psycopg2
        return psycopg2, "psycopg2"
    except ImportError:
        pass
    raise PiloteAbsent(
        "aucun pilote Postgres dans ce conteneur. Panneau -> menu \"...\" du "
        "projet -> \"Lancer le build\" (commande : "
        "pip install --target vendor \"psycopg[binary]\"), puis redemarrer l'app.")


def _identifiant(nom):
    """Valide un nom de base ou de schema destine a etre interpole en SQL.

    Les identifiants Postgres ne peuvent pas etre passes en parametre lie :
    ils sont forcement concatenes dans la requete. Ici ils viennent d'un .env
    ou d'un nom de dossier, donc de l'utilisateur -- on refuse tout ce qui
    n'est pas un nom simple plutot que de concatener a l'aveugle.
    """
    if not nom or not all(c.isalnum() or c in "_-" for c in nom):
        raise ValueError(
            f"nom Postgres invalide : {nom!r} -- lettres, chiffres, tirets et "
            f"soulignes uniquement (CODELAB_DB / CODELAB_SCHEMA dans le .env).")
    return nom


def ensure_database(env_file=None, dbname=None):
    """Cree la base du projet si elle n'existe pas. Renvoie True si creee.

    On se connecte a la base d'instance de Dagster pour cela : CREATE DATABASE
    ne peut pas s'executer depuis la base qu'on cree, et celle-la existe
    toujours. autocommit est obligatoire, Postgres refusant CREATE DATABASE
    dans une transaction.
    """
    mod, _ = pilote_pg()
    cible = _identifiant(dbname or pg_settings(env_file)["dbname"])
    admin = mod.connect(**pg_settings(env_file, dbname=DB_INSTANCE))
    try:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cible,))
            if cur.fetchone():
                return False
            cur.execute(f'CREATE DATABASE "{cible}"')
    finally:
        admin.close()
    return True


def connect_pg(env_file=None, schema=None):
    """Ouvre une connexion sur la base du projet, positionnee sur son schema.

    La base et le schema sont crees s'ils n'existent pas : un projet
    fraichement copie fonctionne sans preparation manuelle. Le schema est
    verifie a chaque connexion parce que l'operation est instantanee quand il
    est deja la, et que l'alternative -- un script d'initialisation a penser a
    lancer -- est exactement le genre d'etape qu'on oublie. La base, elle,
    n'est creee qu'apres un echec de connexion : le cas normal ne paie donc
    aucune connexion supplementaire.
    """
    mod, _ = pilote_pg()
    reglages = pg_settings(env_file)
    try:
        conn = mod.connect(**reglages)
    except Exception:
        # Base absente (projet neuf ou copie) : on la cree puis on reessaie
        # une fois. Si l'echec venait d'autre chose -- reseau, mot de passe --
        # ensure_database echoue de la meme facon et l'erreur remonte telle
        # quelle, sans etre masquee par une seconde tentative.
        ensure_database(env_file)
        conn = mod.connect(**reglages)
    sch = _identifiant(schema or SCHEMA)
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{sch}"')
            # search_path pour que les requetes non qualifiees (psql
            # interactif, \dt, outils tiers) voient les tables du projet.
            cur.execute(f'SET search_path TO "{sch}", public')
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS " + TABLE_QUALIFIEE + " ("
            "  id     BIGSERIAL PRIMARY KEY,"
            "  source TEXT        NOT NULL,"
            "  detail TEXT,"
            "  vu_le  TIMESTAMPTZ NOT NULL DEFAULT now())")
    conn.commit()


def write_heartbeat(conn, source, detail=""):
    ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO " + TABLE_QUALIFIEE + " (source, detail) VALUES (%s, %s) RETURNING id",
                    (source, detail))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def read_heartbeats(conn, limit=10):
    ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT source, count(*), max(vu_le) FROM " + TABLE_QUALIFIEE
                    + " GROUP BY source ORDER BY source")
        par_source = cur.fetchall()
        cur.execute("SELECT id, source, detail, vu_le FROM " + TABLE_QUALIFIEE
                    + " ORDER BY id DESC LIMIT %s", (limit,))
        recentes = cur.fetchall()
    return par_source, recentes


# ----------------------------------- outils -----------------------------------

def _accessible_par(uid, chemin, bit_user, bit_group, bit_other):
    """Le processus d'uid donne a-t-il ce droit sur ce chemin ?

    On calcule depuis les metadonnees plutot que d'utiliser os.access() : ce
    code tourne en root, pour qui tout est accessible. C'est justement le
    piege qu'on veut detecter."""
    st = os.stat(chemin)
    if st.st_uid == uid:
        return bool(st.st_mode & bit_user)
    if st.st_gid == uid:
        return bool(st.st_mode & bit_group)
    return bool(st.st_mode & bit_other)


def _mode(chemin):
    return stat.filemode(os.stat(chemin).st_mode)


def _taille(chemin):
    """Taille en octets, -1 si meme la metadonnee est hors de portee.

    Lire la taille ne demande que de traverser le dossier, pas d'ouvrir le
    fichier : c'est ce qui reste possible quand on tourne sous un autre uid
    que son proprietaire."""
    try:
        return os.stat(chemin).st_size
    except OSError:
        return -1


# ---------------------------------- sondes ----------------------------------
# Chacune renvoie (ok, titre, detail). Aucune ne leve : une sonde qui echoue
# doit afficher pourquoi, pas faire tomber la page.

def check_config(env_file=None):
    """Volume config monte + secret partage disponible.

    "Disponible" et non "lisible" : dans le conteneur app-manager, les
    applications tournent sous l'uid 1001 alors que credentials.env est en
    0600 root. Le panneau, qui tourne en root, leur transmet donc les valeurs
    par l'environnement. Cote Dagster le fichier est lu directement. Les deux
    cas sont sains, mais ils ne se depannent pas de la meme facon -- la sonde
    dit donc d'ou vient la valeur, pas seulement qu'elle est la.
    """
    path = env_file or ENV_FILE
    if not os.path.exists(path):
        return False, "credentials.env", f"introuvable : {path} (volume config non monte ?)"
    # os.access ne ment pas ici : le seul cas ou ce code tourne en root est
    # celui ou root peut effectivement lire le fichier.
    lisible = os.access(path, os.R_OK)
    pw = read_env("POSTGRES_PASSWORD", env_file)
    if not pw:
        if not lisible:
            return (False, "credentials.env",
                    f"{path} illisible sous l'uid {os.geteuid()}, et POSTGRES_PASSWORD "
                    f"n'a pas ete transmis par le panneau")
        return False, "credentials.env", "lisible, mais POSTGRES_PASSWORD absent"
    origine = path if lisible else (f"transmis par le panneau ({path} est "
                                    f"illisible sous l'uid {os.geteuid()})")
    return True, "credentials.env", f"{origine} -- POSTGRES_PASSWORD lu ({len(pw)} caracteres)"


def check_workspace(workspace=None):
    """Volume /workspace partage entre dev, dagster et app-manager."""
    root = workspace or WORKSPACE
    if not os.path.isdir(root):
        return False, "/workspace", f"{root} n'est pas un dossier (volume non monte ?)"
    defs = os.path.join(root, "definitions.py")
    if not os.path.exists(defs):
        return False, "/workspace", f"{root} monte, mais definitions.py absent -- Dagster n'a rien a charger"
    n = len([x for x in os.listdir(root) if not x.startswith(".")])
    return True, "/workspace", f"{root} -- definitions.py present, {n} entrees visibles"


def check_pilote_pg():
    try:
        _, nom = pilote_pg()
        return True, "Postgres (pilote)", f"{nom} disponible"
    except PiloteAbsent as e:
        return False, "Postgres (pilote)", str(e)


def check_postgres(env_file=None):
    """Reseau + mot de passe partage + base accessible."""
    cfg = pg_settings(env_file)
    try:
        pilote_pg()
    except PiloteAbsent:
        return False, "Postgres", "pilote absent -- voir la sonde precedente"
    try:
        conn = connect_pg(env_file)
    except Exception as e:
        return False, "Postgres", f"{cfg['host']}:{cfg['port']} -- {type(e).__name__}: {e}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            v = cur.fetchone()[0].split(" on ")[0]
        return True, "Postgres", (f"{cfg['host']}:{cfg['port']}/{cfg['dbname']} "
                                  f"schema {SCHEMA} -- {v}")
    except Exception as e:
        return False, "Postgres", f"connecte mais requete refusee -- {e}"
    finally:
        conn.close()


def check_http(nom, url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, nom, f"{url} -- HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, nom, f"{url} -- HTTP {e.code} (service joignable)"
    except urllib.error.URLError as e:
        # Nom resolu mais rien en ecoute : le conteneur tourne, le service
        # qu'il heberge non -- il demarre encore, ou il est tombe. C'est une
        # panne tres differente d'un nom introuvable (conteneur arrete), et
        # les deux se lisent pareil sans cette distinction.
        hote = urllib.parse.urlsplit(url).hostname or nom
        if isinstance(e.reason, ConnectionRefusedError):
            return False, nom, (
                f"{url} -- connexion refusee : le conteneur repond mais rien "
                f"n'ecoute sur ce port. Le service demarre encore, ou il est "
                f"tombe : docker logs --tail 50 {hote}")
        if isinstance(e.reason, socket.gaierror):
            # Meme lecture que dans check_tcp : le DNS de Docker n'inscrit que
            # les conteneurs demarres.
            return False, nom, (
                f"{url} -- nom introuvable ({e.reason}). Conteneur arrete, ou "
                f"hors du reseau codelab : docker ps -a --filter name={hote}")
        return False, nom, f"{url} -- {type(e).__name__}: {e}"
    except Exception as e:
        return False, nom, f"{url} -- {type(e).__name__}: {e}"


def check_tcp(nom, host, port, timeout=4, lire_banniere=False):
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            if lire_banniere:
                s.settimeout(timeout)
                b = s.recv(128).decode("utf-8", "replace").strip()
                return True, nom, f"{host}:{port} -- {b or 'connexion acceptee'}"
            return True, nom, f"{host}:{port} -- connexion acceptee"
    except socket.gaierror as e:
        # Le DNS de Docker ne connait que les conteneurs DEMARRES du reseau :
        # un nom qui ne resout pas designe presque toujours un conteneur arrete.
        return False, nom, (f"{host}:{port} -- nom introuvable ({e}). Conteneur arrete, "
                            f"ou hors du reseau codelab : docker ps -a --filter name={host}")
    except Exception as e:
        return False, nom, f"{host}:{port} -- {type(e).__name__}: {e}"


def check_cles_ssh(ssh_dir=None, uid=None):
    """Droits sur authorized_keys, du point de vue de l'utilisateur SSH.

    sshd lit les cles hote en root, mais ouvre authorized_keys APRES avoir
    pris l'uid de l'utilisateur cible. Un dossier non traversable ou un
    fichier non lisible par lui donne un "Permission denied (publickey)"
    cote client, strictement identique a celui d'une cle absente. C'est
    exactement le genre de panne qu'aucune sonde reseau ne verra."""
    d = ssh_dir or SSH_DIR
    u = SSH_UID if uid is None else uid
    ak = os.path.join(d, "authorized_keys")
    hk = os.path.join(d, "host_keys")

    if not os.path.isdir(d):
        return False, "Cles SSH (droits)", f"{d} absent -- codelab-dev n'a jamais demarre ?"
    if not _accessible_par(u, d, stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH):
        return False, "Cles SSH (droits)", (
            f"{d} est en {_mode(d)} : l'uid {u} ne peut pas le traverser, donc sshd "
            f"n'atteindra jamais authorized_keys. Corriger : chmod 755 {d}")
    if not os.path.exists(ak):
        return False, "Cles SSH (droits)", f"{ak} absent -- aucune cle autorisee"
    if not _accessible_par(u, ak, stat.S_IRUSR, stat.S_IRGRP, stat.S_IROTH):
        st = os.stat(ak)
        return False, "Cles SSH (droits)", (
            f"{ak} est en {_mode(ak)} et appartient a {st.st_uid}:{st.st_gid} : "
            f"illisible par l'uid {u}. Corriger : chown {u}:{u} {ak}")

    try:
        with open(ak) as f:
            cles = [l for l in f.read().splitlines()
                    if l.strip() and not l.strip().startswith("#")]
    except OSError as e:
        # Ne pas pouvoir ouvrir le fichier SOI-MEME n'est pas un defaut : ce
        # code ne tourne pas forcement sous l'uid de sshd. Dans le conteneur
        # app-manager il tourne sous l'uid 1001, et authorized_keys appartient
        # a l'uid 1000 en 0600 -- exactement ce qu'on veut. Les droits ont
        # deja ete verifies au-dessus, depuis les metadonnees, et ils disent
        # que sshd y arrivera. Seul le comptage des cles est perdu ; le cas
        # qui compte vraiment, un fichier vide, se lit encore dans la taille.
        if _taille(ak) == 0:
            return False, "Cles SSH (droits)", (
                f"{ak} est vide -- aucune connexion SSH ne passera")
        return True, "Cles SSH (droits)", (
            f"droits corrects pour l'uid {u} ; contenu non verifiable depuis "
            f"ce conteneur, qui tourne sous l'uid {os.geteuid()} ({e.strerror}) "
            f"-- {_taille(ak)} octets")
    if not cles:
        return False, "Cles SSH (droits)", f"{ak} est vide -- aucune connexion SSH ne passera"

    try:
        n_hotes = len([x for x in os.listdir(hk)
                       if x.endswith("_key")]) if os.path.isdir(hk) else 0
        detail_hotes = f"{n_hotes} cle(s) hote persistee(s)"
    except OSError:
        # Meme raison : le dossier des cles hote appartient a sshd, pas a nous.
        detail_hotes = "cles hote non listables depuis ce conteneur"
    return True, "Cles SSH (droits)", (
        f"{len(cles)} cle(s) autorisee(s), lisible(s) par l'uid {u} ; "
        f"{detail_hotes}")


# ---------------------------------------------------------------- securite
#
# Les sondes ci-dessus disent si la stack MARCHE. Celles-ci disent si elle est
# correctement FERMEE -- ce qu'un controle du code ne peut pas dire, parce que
# cela depend de la configuration reelle : une variable oubliee, un port non
# publie, une garde active en developpement et pas en service.
#
# Elles tournent depuis l'interieur du conteneur du panneau, donc sans jamais
# avoir besoin d'un mot de passe : elles verifient precisement que ce qui
# DEVRAIT demander une session en demande bien une.
#
# Aucune n'ecrit ni ne casse quoi que ce soit : ce sont des lectures, et des
# ecritures qui doivent etre REFUSEES. Si l'une d'elles passe, c'est le
# probleme.

def _port_panneau():
    return int(os.environ.get("MANAGER_PORT") or 9001)


# Le port des applications quand rien ne le dit : c'est le defaut du panneau
# (voir APPS_PORT dans app-manager/app/app.py), et c'est celui que publie
# docker-compose.yml.
PORT_APPS_DEFAUT = 9002


def _port_applications():
    """Le port ou les applications sont servies.

    POURQUOI CE N'EST PLUS "la variable ou rien". Cette sonde lisait
    APP_MANAGER_APPS_PORT et declarait l'installation en faute des qu'elle
    etait absente -- alors que le panneau, lui, se rabat sur 9002 et que le
    compose publie ce port. Elle annoncait donc "les applications sont
    servies dans l'origine du panneau" sur une installation ou les deux
    origines etaient parfaitement separees.

    Une sonde ne doit dire que ce qu'elle CONSTATE. Elle prend donc le meme
    defaut que le panneau, et va verifier sur le port ce qui s'y trouve
    vraiment.
    """
    return int(os.environ.get("APP_MANAGER_APPS_PORT") or PORT_APPS_DEFAUT)


def check_panneau_ferme():
    """Les routes d'administration exigent-elles une session ?

    On les appelle sans rien : la bonne reponse est un refus. Un 200 ici
    voudrait dire que n'importe qui sur le reseau lit la liste des comptes.
    """
    import urllib.error
    import urllib.request
    base = f"http://127.0.0.1:{_port_panneau()}"
    ouvertes = []
    for chemin in ("/api/apps", "/api/utilisateurs", "/api/activite", "/api/securite"):
        try:
            code = urllib.request.urlopen(base + chemin, timeout=4).getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:                                    # noqa: BLE001
            return False, "panneau ferme", f"{chemin} injoignable : {e}"
        if code == 200:
            ouvertes.append(chemin)
    if ouvertes:
        return (False, "panneau ferme",
                "repond 200 sans session : " + ", ".join(ouvertes))
    return True, "panneau ferme", "les routes d'administration exigent une session"


def check_origine_applications():
    """Les applications sont-elles servies dans une autre origine ?

    Tant qu'elles vivent sous le port du panneau, une faille dans l'une
    d'elles donne acces au panneau : meme origine, donc meme page, meme
    jeton. Un port distinct fait du navigateur l'arbitre.
    """
    import urllib.error
    import urllib.request
    port = _port_applications()
    base = f"http://127.0.0.1:{port}"
    try:
        urllib.request.urlopen(base + "/health", timeout=4).getcode()
    except Exception as e:                                        # noqa: BLE001
        return (False, "origine des applications",
                f"rien ne repond sur le port {port} ({e}) -- publie-le dans "
                f"docker-compose.yml, sinon les applications repartent dans "
                f"l'origine du panneau")
    fuites = []
    for chemin in ("/", "/login", "/api/apps"):
        try:
            code = urllib.request.urlopen(base + chemin, timeout=4).getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:                                         # noqa: BLE001
            continue
        if code == 200:
            fuites.append(chemin)
    if fuites:
        return (False, "origine des applications",
                f"le panneau repond sur le port {port} : " + ", ".join(fuites))
    return (True, "origine des applications",
            f"port {port} -- le panneau n'y repond pas, les origines sont bien separees")


def check_exposition():
    """Ce qui doit etre pose quand le panneau sort du reseau local.

    Ces variables ne se devinent pas depuis le code : elles dependent de ce
    qu'on a mis devant. Tant que la stack reste chez soi, leur absence est
    normale -- la sonde le dit plutot que de crier au feu.
    """
    # DEUX SOURCES, ET IL FAUT LES DEUX. Ces reglages se posent desormais
    # depuis la page Exposition du panneau, qui les ecrit dans
    # exposition.json ; la variable d'environnement reste prioritaire quand
    # le compose la fixe. Ne lire que l'environnement, comme le faisait cette
    # sonde, annoncait "il manque APP_MANAGER_HTTPS" sur une installation ou
    # HTTPS etait deja active depuis la page -- la sonde reclamait ce qui
    # etait deja fait.
    etat = os.environ.get("APP_MANAGER_STATE") or "/var/lib/codelab/app-manager"
    try:
        with open(os.path.join(etat, "exposition.json")) as f:
            pose = json.load(f) or {}
    except (OSError, ValueError):
        pose = {}

    def _actif(variable, cle):
        depuis_env = (os.environ.get(variable) or "").strip()
        if depuis_env:
            return depuis_env.lower() in ("1", "true", "yes")
        return bool(pose.get(cle))

    https = _actif("APP_MANAGER_HTTPS", "https")
    proxy = _actif("APP_MANAGER_TRUST_PROXY", "trust_proxy")
    publique = ((os.environ.get("APP_MANAGER_PUBLIC_URL") or "").strip()
                or str(pose.get("adresse_publique") or "").strip())
    if not (https or proxy or publique):
        return (True, "exposition",
                "reseau local : aucune adresse publique declaree, cookie non "
                "marque Secure -- coherent tant que rien n'est devant")
    manques = []
    if not https:
        manques.append("HTTPS (cookie de session non marque Secure)")
    if not proxy:
        manques.append("proxy de confiance (tous les visiteurs partagent une adresse)")
    if not publique:
        manques.append("adresse publique (aucun partage possible)")
    # Pas dans les "manques" : une stack exposee peut parfaitement vouloir
    # que son administration reste joignable de l'exterieur, et la reclamer
    # en rouge serait dicter un choix plutot que signaler un oubli. La sonde
    # dit donc simplement lequel des deux est en vigueur.
    admin_local = _actif("APP_MANAGER_ADMIN_LAN_ONLY", "admin_reseau_local")
    portee = ("administration reservee au reseau local" if admin_local
              else "administration joignable de l'exterieur")
    if manques:
        return (False, "exposition", "expose, mais il manque : "
                + " ; ".join(manques) + " -- a poser dans Parametres > Exposition"
                + " (" + portee + ")")
    return (True, "exposition",
            f"publie sur {publique}, cookie Secure, adresse reelle des visiteurs, {portee}")


def check_provenance():
    """D'ou t'a-t-on reellement atteint ?

    C'est la question a laquelle un pare-feu repond en la fermant. Ici on ne
    la ferme pas -- un conteneur ne peut pas poser de regles sur l'hote sans
    qu'on lui donne le reseau de l'hote et CAP_NET_ADMIN, c'est-a-dire sans
    defaire tout le durcissement -- mais on la POSE, ce qui est deja ce qui
    manquait : personne ne regarde les adresses du journal, et une machine
    qui gagne une interface a laquelle personne ne pense ne previent pas.

    La regle est celle du bon sens : des adresses publiques sont normales sur
    une stack declaree publique, et anormales sur une stack censee rester a
    la maison.
    """
    import ipaddress
    etat = os.environ.get("APP_MANAGER_STATE") or "/var/lib/codelab/app-manager"
    journal = os.path.join(etat, "acces.jsonl")
    if not os.path.exists(journal):
        return True, "provenance des connexions", "aucune connexion enregistree pour l'instant"
    try:
        with open(journal, errors="replace") as f:
            lignes = f.readlines()[-2000:]
    except OSError as e:
        return True, "provenance des connexions", f"journal illisible ici ({e})"

    dehors, dedans = {}, 0
    for ligne in lignes:
        try:
            ip = json.loads(ligne).get("ip") or ""
        except ValueError:
            continue
        if not ip:
            continue
        try:
            adr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        # is_global et non is_private : "prive" englobe aussi les plages de
        # documentation (203.0.113.0/24, 198.51.100.0/24), qui ne sont pas
        # celles d'un reseau domestique. La question posee ici est "cette
        # adresse est-elle routable depuis Internet ?", et c'est exactement
        # ce que is_global repond.
        if adr.is_global:
            dehors[ip] = dehors.get(ip, 0) + 1
        else:
            dedans += 1

    publique = (os.environ.get("APP_MANAGER_PUBLIC_URL") or "").strip()
    if not dehors:
        return (True, "provenance des connexions",
                f"{dedans} connexions, toutes depuis le reseau local")
    resume = ", ".join(f"{ip} ({n}x)" for ip, n in
                       sorted(dehors.items(), key=lambda x: -x[1])[:3])
    if publique:
        return (True, "provenance des connexions",
                f"{dedans} depuis le reseau local, {sum(dehors.values())} depuis "
                f"l'exterieur -- attendu, la stack est publiee sur {publique}")
    return (False, "provenance des connexions",
            f"des connexions viennent de l'EXTERIEUR alors qu'aucune adresse "
            f"publique n'est declaree : {resume}. Verifie ce que ta box "
            f"redirige, et borne les ports au reseau local.")


def _etat_panneau():
    """Le dossier d'etat du panneau, vu depuis ce projet."""
    return os.environ.get("APP_MANAGER_STATE") or "/var/lib/codelab/app-manager"


def _lire_json(chemin, defaut):
    try:
        with open(chemin) as f:
            return json.load(f) or defaut
    except (OSError, ValueError):
        return defaut


def check_applications():
    """Chaque application declaree, de bout en bout.

    Les autres sondes verifient la STACK -- Postgres repond, Dagster repond,
    le proxy sert. Aucune ne regardait les applications elles-memes, alors
    que c'est pour elles que la stack existe. Une application dont le dossier
    a disparu, dont la commande n'existe plus ou qui n'ecoute pas son port
    passait totalement inapercue jusqu'a ce qu'on essaie de l'ouvrir.

    Trois questions par application, dans l'ordre ou elles cassent :

      1. son dossier existe-t-il encore ? (renomme, supprime, volume absent)
      2. le premier mot de sa commande se resout-il ? (python3, node, un
         binaire installe par un build qui n'a pas ete rejoue)
      3. si elle est censee tourner, quelque chose ecoute-t-il son port ?

    Lecture seule : rien n'est demarre, rien n'est arrete.
    """
    apps = _lire_json(os.path.join(_etat_panneau(), "apps.json"), {})
    if not apps:
        return (True, "applications declarees",
                "Aucune application declaree : rien a verifier.")

    soucis, tournent = [], 0
    for nom, a in sorted(apps.items()):
        chemin = a.get("path") or ""
        if not os.path.isdir(chemin):
            soucis.append(f"{nom} : dossier introuvable ({chemin})")
            continue
        commande = (a.get("command") or "").strip()
        premier = commande.split()[0] if commande else ""
        if not premier:
            soucis.append(f"{nom} : aucune commande de lancement")
        elif not (shutil.which(premier) or os.path.isfile(os.path.join(chemin, premier))):
            soucis.append(f"{nom} : commande introuvable ({premier})")
        if a.get("enabled"):
            port = a.get("port")
            if _port_ouvert("127.0.0.1", port):
                tournent += 1
            else:
                soucis.append(f"{nom} : marquee demarree, mais rien n'ecoute sur {port}")

    total = len(apps)
    if soucis:
        return (False, "applications declarees",
                f"{len(soucis)} probleme(s) sur {total} application(s) : "
                + " ; ".join(soucis[:4])
                + (" ..." if len(soucis) > 4 else ""))
    return (True, "applications declarees",
            f"{total} application(s), {tournent} en ligne : dossier present, "
            f"commande resolvable, port a l'ecoute.")


def _port_ouvert(hote, port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    s = socket.socket()
    s.settimeout(1.5)
    try:
        return s.connect_ex((hote, port)) == 0
    finally:
        s.close()


# DEUX conditions, et il faut les deux. Un pourcentage seul se trompe dans
# les deux sens : 89 % d'un disque de 250 Go laisse 28 Go, de quoi tenir des
# mois, et la sonde crierait pour rien ; 70 % d'une carte SD de 16 Go laisse
# 5 Go, et c'est deja court. On alerte quand le disque est a la fois BIEN
# REMPLI et qu'il reste peu de chose en valeur absolue.
SEUIL_DISQUE = 85
SEUIL_LIBRE_GO = 5


def check_espace_disque():
    """Ce qui tue une machine auto-hebergee : pas une panne, un disque plein.

    Et cela ne previent pas. Postgres refuse d'ecrire, les journaux
    s'arretent, les builds echouent avec des messages qui ne parlent pas
    d'espace. La sonde regarde les volumes qui comptent, plus le poids des
    journaux du panneau -- ils grossissent tout seuls, a chaque ligne de
    chaque application.
    """
    lignes, alerte = [], False
    vus = set()
    for chemin in ("/workspace", _etat_panneau(), "/var/lib/codelab/config", "/"):
        if not os.path.isdir(chemin):
            continue
        try:
            st = os.statvfs(chemin)
        except OSError:
            continue
        cle = (st.f_blocks, st.f_bsize)
        if cle in vus:          # meme systeme de fichiers, deja compte
            continue
        vus.add(cle)
        total = st.f_blocks * st.f_frsize
        libre = st.f_bavail * st.f_frsize
        if not total:
            continue
        occupe = round(100 * (total - libre) / total)
        lignes.append(f"{chemin} : {occupe} % occupe, "
                      f"{libre / (1024 ** 3):.1f} Go libres")
        if occupe >= SEUIL_DISQUE and libre < SEUIL_LIBRE_GO * 1024 ** 3:
            alerte = True

    journaux = os.path.join(_etat_panneau(), "logs")
    poids = 0
    if os.path.isdir(journaux):
        for nom in os.listdir(journaux):
            try:
                poids += os.path.getsize(os.path.join(journaux, nom))
            except OSError:
                pass
        lignes.append(f"journaux des applications : {poids / (1024 ** 2):.0f} Mo")

    if not lignes:
        return False, "espace disque", "Aucun volume lisible."
    return (not alerte), "espace disque", " | ".join(lignes)


def check_surface_exposee():
    """Ce qui est REELLEMENT joignable, et par qui.

    Constate plutot que de faire confiance a ce qui est declare : la liste
    des applications publiques vient d'apps.json, la restriction
    d'administration d'exposition.json, et les comptes sans second facteur
    d'utilisateurs.json. Trois fichiers, trois verites qu'on ne rapproche
    jamais a l'oeil.

    Elle ne dit pas "c'est mal" : une application publique sur une machine
    qui n'est pas publiee ne risque rien. Elle dit ce qui est ouvert, pour
    que le choix soit fait en connaissance.
    """
    etat = _etat_panneau()
    apps = _lire_json(os.path.join(etat, "apps.json"), {})
    expo = _lire_json(os.path.join(etat, "exposition.json"), {})
    comptes = _lire_json(os.path.join(etat, "utilisateurs.json"), {})

    publiques = sorted(n for n, a in apps.items()
                       if (a.get("visibility") or "privee") == "publique")
    sans_2fa = sorted(n for n, c in comptes.items()
                      if isinstance(c, dict) and not c.get("totp"))
    admin_local = bool(expo.get("admin_reseau_local"))
    adresse = (expo.get("adresse_publique") or "").strip()

    morceaux = [
        f"administration {'limitee au reseau local' if admin_local else 'joignable de partout'}",
        f"adresse publique {'declaree : ' + adresse if adresse else 'non declaree'}",
        f"{len(publiques)} application(s) publique(s)"
        + (f" ({', '.join(publiques[:3])})" if publiques else ""),
        f"{len(sans_2fa)} compte(s) sans second facteur"
        + (f" ({', '.join(sans_2fa[:3])})" if sans_2fa else ""),
    ]
    # Le seul cas franchement mauvais : une machine publiee ET une
    # administration joignable de partout. Le reste est un etat des lieux.
    mauvais = bool(adresse) and not admin_local
    return (not mauvais), "surface exposee", " | ".join(morceaux)


# Les trois verrous du noyau qui peuvent interdire un namespace utilisateur,
# avec la valeur qui BLOQUE et la commande qui l'ouvre. En constante, et non
# dans le corps de la sonde : un test doit pouvoir les remplacer par des
# fichiers a lui, sans quoi cette lecture ne serait verifiable que sur une
# machine deja en panne.
VERROUS_USERNS = [
    ("/proc/sys/kernel/unprivileged_userns_clone", "0",
     "sysctl -w kernel.unprivileged_userns_clone=1"),
    ("/proc/sys/user/max_user_namespaces", "0",
     "sysctl -w user.max_user_namespaces=15000"),
    ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1",
     "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0"),
]


def _verrou_userns():
    """Lequel des trois verrous du noyau interdit le namespace utilisateur.

    POURQUOI ALLER LE LIRE. La sonde disait "pose ces deux sysctl" sans
    regarder s'ils etaient en cause : sur une machine ou c'est AppArmor qui
    refuse, les deux commandes conseillees ne changent rien, et l'on
    recommence indefiniment. Trois verrous existent, ils ne vivent pas au
    meme endroit, et un seul suffit a tout bloquer.

    Ces fichiers sont lus sur l'HOTE a travers /proc, qui n'est pas
    namespace : ce qu'on lit ici est bien le reglage de la machine.
    """
    coupables = []
    for chemin, valeur_bloquante, remede in VERROUS_USERNS:
        try:
            with open(chemin) as f:
                lu = f.read().strip()
        except OSError:
            # Absent : ce verrou-la n'existe pas sur ce noyau, il n'y est
            # donc pour rien.
            continue
        if lu == valeur_bloquante:
            coupables.append(f"{os.path.basename(chemin)}={lu}, a corriger par : {remede}")
    if coupables:
        return "Sur l'hote : " + " ; ".join(coupables) + "."
    # Aucun des trois n'est ferme : c'est le bac a sable du conteneur
    # (seccomp, AppArmor) qui refuse, et cela ne se corrige pas par sysctl.
    return ("Aucun sysctl du noyau ne l'interdit : c'est le profil seccomp ou "
            "AppArmor du conteneur qui refuse.")


def check_isolation():
    """Une application peut-elle voir les fichiers d'une autre ?

    Cette sonde tourne dans le projet de diagnostic, qui est justement le
    SEUL a ne pas etre isole : c'est l'observateur, il a besoin de voir. Elle
    controle donc les conditions de l'isolement, pas son propre bac.
    """
    import shutil
    import subprocess
    actif = os.environ.get("APP_MANAGER_ISOLER", "1").lower() not in ("0", "false", "no")
    if not actif:
        return (False, "isolation des applications",
                "APP_MANAGER_ISOLER coupe : chaque application voit les "
                "fichiers de toutes les autres")
    if shutil.which("unshare") is None:
        return (False, "isolation des applications",
                "unshare absent de l'image : les applications demarrent, mais "
                "sans etre isolees les unes des autres")

    # On EXECUTE, on ne se contente pas de trouver le binaire. unshare vient
    # de util-linux : il est toujours la. Ce qui manque, sur les machines ou
    # l'isolement echoue, c'est l'autorisation du noyau -- et elle ne se lit
    # pas dans un chemin d'acces.
    try:
        essai = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--mount", "true"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        refus = (essai.stderr or b"").decode("utf-8", "replace").strip()
        ok = essai.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        ok, refus = False, str(e)

    if not ok:
        return (False, "isolation des applications",
                "le noyau refuse de creer un namespace utilisateur (%s). %s "
                "Chaque application garde son propre uid -- elles ne peuvent "
                "pas se relire l'environnement ni se tuer -- mais elles "
                "partagent la vue de /workspace. Pour assumer le choix et "
                "faire taire cette sonde : APP_MANAGER_ISOLER=0"
                % (refus or "raison inconnue", _verrou_userns()))

    return (True, "isolation des applications",
            "chaque application ne voit que son propre projet "
            "(ce diagnostic excepte : il doit voir l'ensemble)")


# ------------------------------------------------ verification approfondie
#
# Les sondes ci-dessus REGARDENT : elles lisent un fichier, ouvrent une
# connexion, comparent une variable. Elles tournent a chaque affichage de la
# page, donc elles doivent rester instantanees et sans effet.
#
# Ce qui suit AGIT : chaque test declenche quelque chose et verifie que la
# stack a reagi comme il faut. C'est plus lent, cela laisse des traces dans
# les journaux, et cela ne se lance donc qu'a la demande -- le bouton
# « Verification approfondie » de la page.
#
# POURQUOI CE N'EST PAS LA SUITE DE TESTS DU PANNEAU. Celle-ci vit dans
# app-manager/tests/ et s'execute a la construction de l'image, sur du code,
# dans des dossiers temporaires. La lancer DANS une installation qui tourne
# la detruirait : ses fixtures ecrivent apps.json, utilisateurs.json et
# passkeys.json sans les rediriger -- sur une machine reelle, ces chemins
# existent, et ce sont tes applications et tes comptes qui seraient
# remplaces. Deux choses differentes, deux endroits differents : la-bas on
# verifie du CODE avant de le livrer, ici on verifie une INSTALLATION qui
# tourne.
#
# Regle absolue ici : aucun test ne modifie l'etat de l'installation. Les
# ecritures se font dans la table du diagnostic ou dans son propre dossier,
# et les actions interdites doivent etre REFUSEES -- si l'une passe, c'est
# le resultat du test.

def _essai(nom, fn):
    """Un test qui ne fait jamais tomber la page : un echec est un
    resultat, une exception aussi."""
    try:
        return fn()
    except Exception as e:                                        # noqa: BLE001
        return False, nom, f"{type(e).__name__}: {e}"


def verif_base_ecrit_et_relit():
    """La chaine complete jusqu'a Postgres : on insere, on relit."""
    conn = connect_pg()
    try:
        numero = write_heartbeat(conn, "verification", "verification approfondie")
        _, recentes = read_heartbeats(conn, limit=20)
        vu = any("verification approfondie" in str(l) for l in recentes)
        if not vu:
            return (False, "base : ecriture puis relecture",
                    f"ligne #{numero} inseree, mais absente de la relecture")
        return (True, "base : ecriture puis relecture",
                f"ligne #{numero} inseree et relue dans la foulee")
    finally:
        conn.close()


def verif_ecriture_refusee_sans_session():
    """Une ecriture sans session doit etre refusee.

    Le test ne casse rien PRECISEMENT parce qu'il doit echouer : si la
    requete passait, c'est elle qui serait le probleme.

    Ce qu'il prouve exactement, et pas davantage : la route d'ecriture exige
    une session. Il ne prouve PAS que le jeton tient -- pour cela il faudrait
    une session valide, que ce diagnostic n'a pas et ne doit pas avoir. Le
    premier jet s'appelait "refusee sans jeton" : il annoncait un controle
    qu'il ne faisait pas.
    """
    import urllib.error
    import urllib.request
    url = f"http://127.0.0.1:{_port_panneau()}/api/visibility/diagnostic"
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        code = urllib.request.urlopen(req, timeout=5).getcode()
    except urllib.error.HTTPError as e:
        code = e.code
    if code in (401, 403):
        quoi = "session" if code == 401 else "jeton"
        return (True, "ecriture refusee sans session",
                f"le panneau repond {code} -- la garde de {quoi} tient")
    return (False, "ecriture refusee sans session",
            f"le panneau repond {code} : une ecriture est passee sans session")


def verif_proxy_sert_cette_application():
    """Bout en bout : le diagnostic se demande lui-meme, a travers le proxy.

    C'est le seul test qui traverse toute la chaine du panneau -- routage,
    resolution du projet, regle de visibilite, reverse proxy -- et il n'a
    besoin de rien d'autre que de cette application, qui tourne forcement
    puisqu'elle execute ce test.

    Ce que dit chaque reponse, et c'est la que le test devient utile :

      302 vers /login  le proxy a resolu le projet ET applique sa visibilite
                       privee. Les deux marchent. C'est le cas NORMAL, le
                       diagnostic etant inscrit en prive.
      200              idem, sur une application publique.
      404              le panneau ne connait pas ce projet.
      502 / 503        il le connait, mais l'application ne repond pas.

    Le premier jet suivait les redirections et comptait le 302 comme un
    echec : il declarait le proxy casse alors qu'il faisait exactement son
    travail.
    """
    import urllib.error
    import urllib.request

    class _SansSuivre(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    port = _port_applications() or _port_panneau()
    url = f"http://127.0.0.1:{port}/diagnostic/health"
    ouvre = urllib.request.build_opener(_SansSuivre).open
    debut = time.time()
    try:
        r = ouvre(url, timeout=8)
        code, entetes = r.getcode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        code, entetes = e.code, dict(e.headers)
    except Exception as e:                                        # noqa: BLE001
        return False, "le proxy sert cette application", f"{url} : {e}"
    ms = int((time.time() - debut) * 1000)

    if code == 200:
        return (True, "le proxy sert cette application",
                f"{url} repond 200 en {ms} ms")
    if code in (301, 302, 303, 307, 308) and "/login" in (entetes.get("Location") or ""):
        return (True, "le proxy sert cette application",
                f"le proxy resout le projet et applique sa visibilite privee "
                f"(redirection vers /login, {ms} ms)")
    if code == 404:
        return (False, "le proxy sert cette application",
                "le panneau ne connait pas d'application « diagnostic » : "
                "elle a ete supprimee du registre, ou renommee")
    if code in (502, 503):
        return (False, "le proxy sert cette application",
                f"le panneau connait le projet mais l'application ne repond "
                f"pas ({code}) -- est-elle demarree ?")
    return False, "le proxy sert cette application", f"{url} repond {code}"


def verif_le_journal_enregistre():
    """Le journal des acces suit-il vraiment ce qui se passe ?

    On compte, on provoque une ouverture (la requete ci-dessus en est une),
    on recompte. Un journal qui n'ecrit plus est invisible autrement : tout
    continue de marcher, on ne s'apercoit de rien, et le jour ou l'on
    cherche qui s'est connecte il n'y a rien a lire.
    """
    import urllib.request
    etat = os.environ.get("APP_MANAGER_STATE") or "/var/lib/codelab/app-manager"
    journal = os.path.join(etat, "acces.jsonl")

    def taille():
        try:
            return os.path.getsize(journal)
        except OSError:
            return -1

    avant = taille()
    if avant < 0:
        return (False, "le journal des acces enregistre",
                f"{journal} introuvable ou illisible depuis ici")
    port = _port_applications() or _port_panneau()
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/diagnostic/health", timeout=8).read(10)
    except Exception:                                             # noqa: BLE001
        pass
    time.sleep(1.0)
    apres = taille()
    if apres > avant:
        return (True, "le journal des acces enregistre",
                f"une ouverture de plus notee ({apres - avant} octets)")
    # Les ouvertures sont regroupees par quart d'heure et par compte : rien
    # de neuf peut vouloir dire "deja note il y a dix minutes", pas "casse".
    return (True, "le journal des acces enregistre",
            f"{journal} lisible ({avant} octets) -- rien de neuf, les "
            f"ouvertures sont regroupees par quart d'heure")


def verif_le_projet_est_ecrivable():
    """Un build ecrit dans le dossier du projet : il faut que ce soit vrai.

    Le fichier est cree puis efface -- rien ne subsiste.
    """
    ici = os.path.dirname(os.path.abspath(__file__))
    temoin = os.path.join(ici, ".verification-ecriture")
    try:
        with open(temoin, "w") as f:
            f.write("temoin\n")
        relu = open(temoin).read().strip()
    finally:
        try:
            os.unlink(temoin)
        except OSError:
            pass
    if relu != "temoin":
        return False, "le dossier du projet est ecrivable", "relecture incorrecte"
    return (True, "le dossier du projet est ecrivable",
            f"{ici} -- ecrit, relu, efface")


# Prefixe "verif_" et non "test_" : ce fichier contient AUSSI la suite de
# tests du panneau, que pytest collecte. Nommees "test_...", ces cinq
# verifications seraient ramassees par pytest et tourneraient a la
# construction de l'image -- sans panneau, sans base, sans installation.
# Elles echoueraient toutes, pour rien.
def lancer_suite_du_panneau(timeout=180):
    """Lance la suite de regressions du panneau, dans un sous-process.

    Un sous-process et pas pytest.main() dans celui-ci : pytest reimporterait
    ce fichier, qui est justement en train de s'executer, et les fixtures
    s'appliqueraient a l'application vivante. Un process a part n'a aucun de
    ces effets et meurt avec son resultat.

    Rien de ce qu'ils ecrivent ne touche l'installation : le filet detourne
    tous les chemins d'etat avant chaque test (voir plus bas). Verifie en
    faisant tourner la suite entiere avec APP_MANAGER_STATE pointant sur de
    vrais fichiers -- aucun n'a bouge, aucun n'est apparu.
    """
    import subprocess
    if app is None:
        return (False, "suite de regressions du panneau",
                "le module du panneau est introuvable depuis ici")
    debut = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", os.path.abspath(__file__),
             "-q", "--no-header", "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(__file__)))
    except FileNotFoundError:
        return (False, "suite de regressions du panneau",
                "pytest n'est pas installe dans cette image")
    except subprocess.TimeoutExpired:
        return (False, "suite de regressions du panneau",
                f"la suite n'a pas fini en {timeout} s")
    secondes = time.time() - debut
    # La derniere ligne non vide porte le compte : "123 passed, 1 skipped...".
    lignes = [l for l in (r.stdout or "").strip().split("\n") if l.strip()]
    resume = lignes[-1] if lignes else (r.stderr or "").strip()[-200:]
    if r.returncode == 0:
        return (True, "suite de regressions du panneau",
                f"{resume} -- en {secondes:.1f} s")
    echecs = [l for l in lignes if l.startswith("FAILED")]
    return (False, "suite de regressions du panneau",
            resume + (" | " + " ; ".join(e[6:80] for e in echecs[:4]) if echecs else ""))


def run_tests():
    """Les tests a la demande, dans l'ordre ou on veut les lire."""
    return [
        _essai("base : ecriture puis relecture", verif_base_ecrit_et_relit),
        _essai("ecriture refusee sans session", verif_ecriture_refusee_sans_session),
        _essai("le proxy sert cette application", verif_proxy_sert_cette_application),
        _essai("le journal des acces enregistre", verif_le_journal_enregistre),
        _essai("le dossier du projet est ecrivable", verif_le_projet_est_ecrivable),
    ]


def run_all(env_file=None, workspace=None, ssh_dir=None):
    """Toutes les sondes, dans l'ordre ou on veut les lire :
    d'abord ce qui doit MARCHER, ensuite ce qui doit etre FERME."""
    host = read_env("POSTGRES_HOST", env_file) or "codelab-postgres"
    port = int(read_env("POSTGRES_PORT", env_file) or 5432)
    return [
        check_config(env_file),
        check_workspace(workspace),
        check_pilote_pg(),
        check_postgres(env_file),
        check_tcp("codelab-postgres (TCP)", host, port),
        check_http("codelab-dagster", "http://codelab-dagster:3000/"),
        check_tcp("codelab-dev (SSH)", "codelab-dev", 22, lire_banniere=True),
        check_cles_ssh(ssh_dir),
        # L'etat des lieux ne s'arrete pas a "ca marche" : il dit aussi si
        # c'est correctement ferme.
        check_panneau_ferme(),
        check_origine_applications(),
        check_exposition(),
        check_isolation(),
        check_provenance(),
        # Au-dela de "la stack repond" : ce qu'elle porte, ce qu'elle use, et
        # ce qu'elle laisse ouvert. Les trois sont en LECTURE SEULE, donc a
        # leur place ici et non dans la verification approfondie.
        check_applications(),
        check_espace_disque(),
        check_surface_exposee(),
    ]


# --------------------------------------------------------------------------
# Le module du panneau, pour la suite de tests ci-dessous.
#
# Deux emplacements possibles, et il faut les deux : dans le conteneur
# l'application est installee sous /opt/codelab, tandis qu'en CI on travaille
# depuis une copie du depot. On cherche donc les deux, dans cet ordre.
CHEMINS_PANNEAU = [
    "/opt/codelab/app-manager/app/app.py",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app-manager", "app", "app.py"),
]


# Rempli par _charger_panneau() : le dossier du service, d'ou les tests
# retrouvent les pages servies et le fichier a relancer dans un sous-process.
DOSSIER_PANNEAU = ""


def _charger_panneau():
    global DOSSIER_PANNEAU
    for chemin in CHEMINS_PANNEAU:
        if os.path.exists(chemin):
            DOSSIER_PANNEAU = os.path.dirname(os.path.dirname(chemin))
            spec = importlib.util.spec_from_file_location(
                "codelab_app_manager", chemin)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


app = _charger_panneau()


# ==========================================================================
#                        TESTS DU PANNEAU (app-manager)
# ==========================================================================
#
# La suite de regressions du panneau vit ici, avec l'etat des lieux, pour une
# raison simple : c'est le seul endroit ou elle est lancable des DEUX cotes
# -- par la CI avant qu'une image ne parte, et depuis l'installation qui
# tourne, par la page /tests du diagnostic.
#
#     python -m pytest workspace/diagnostic/checks.py -q
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
    "ACCES_FILE", "CHILD_HOME", "ALERTES_FILE", "SMTP_FILE", "CATEGORIES_FILE",
    "EXPOSITION_FILE", "DIAGNOSTIC_MARQUEUR", "SHARED_CONFIG_DIR",
    "SHARED_ENV_FILE", "LEGACY_ADMIN_PASSWORD_FILE", "LEGACY_SECRET_KEY_FILE",
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
    etat.mkdir()
    partage.mkdir()
    remplacements = {
        "STATE_DIR": str(etat),
        "APPS_FILE": str(etat / "apps.json"),
        "LOG_DIR": str(etat / "logs"),
        "UTILISATEURS_FILE": str(etat / "utilisateurs.json"),
        "PASSKEYS_FILE": str(etat / "passkeys.json"),
        "ACCES_FILE": str(etat / "acces.jsonl"),
        "CHILD_HOME": str(etat / "home"),
        "ALERTES_FILE": str(etat / "alertes.json"),
        "SMTP_FILE": str(etat / "smtp.json"),
        "CATEGORIES_FILE": str(etat / "categories.json"),
        "EXPOSITION_FILE": str(etat / "exposition.json"),
        "DIAGNOSTIC_MARQUEUR": str(etat / "diagnostic-inscrit"),
        "SHARED_CONFIG_DIR": str(partage),
        "SHARED_ENV_FILE": str(partage / "credentials.env"),
        "LEGACY_ADMIN_PASSWORD_FILE": str(etat / "admin_password"),
        "LEGACY_SECRET_KEY_FILE": str(etat / "flask_secret_key"),
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
    yield


def test_le_filet_detourne_bien_tous_les_chemins():
    """Le filet se teste lui-meme : si un chemin d'etat lui echappait, ce
    test le dirait avant qu'une ecriture n'atteigne une vraie installation."""
    import inspect
    source = inspect.getsource(app)
    # Tout ce qui ressemble a un chemin construit sous STATE_DIR ou
    # SHARED_CONFIG_DIR doit figurer dans CHEMINS_ETAT.
    trouves = set(re.findall(
        r"^([A-Z][A-Z0-9_]*) = os\.path\.join\((?:STATE_DIR|SHARED_CONFIG_DIR)",
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
    assert ok is False
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
    monkeypatch.setattr(sys.modules[__name__], "VERROUS_USERNS", [
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
    monkeypatch.setattr(sys.modules[__name__], "VERROUS_USERNS",
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

    r = subprocess.run(
        [sys.executable, "-c", script, os.path.abspath(__file__)],
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


# ------------------ 0. cache du registre d'applications ------------------
#
# load() est mis en cache pour eviter une lecture disque par requete
# proxifiee. Le risque du cache est de servir un registre perime : ces deux
# tests tiennent la seule propriete qui compte, "une modification est vue".

def test_le_registre_est_relu_quand_le_fichier_change(tmp_path, monkeypatch):
    fichier = tmp_path / "apps.json"
    monkeypatch.setattr(app, "APPS_FILE", str(fichier))
    app._apps_cache["signature"] = None

    app.save({"un": {"port": 9101}})
    assert list(app.load()) == ["un"]

    # Ecriture exterieure, sans passer par save() : le panneau documente
    # d'editer apps.json a la main, et un job Dagster pourrait le faire.
    fichier.write_text(json.dumps({"deux": {"port": 9102}}))
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
# Le projet de diagnostic s'inscrit tout seul au premier demarrage. La
# propriete a tenir n'est pas "il s'inscrit" (visible du premier coup d'oeil)
# mais "il ne se reinscrit jamais" : un projet supprime qui revient au
# redemarrage suivant est exactement le defaut qui rend une installation
# penible, et il ne se voit qu'apres coup.

def _amorcage(tmp_path, monkeypatch, avec_projet=True):
    etat = tmp_path / "etat"
    etat.mkdir()
    racine = tmp_path / "workspace"
    racine.mkdir()
    if avec_projet:
        (racine / "diagnostic").mkdir()
        (racine / "diagnostic" / "app.py").write_text("")
    monkeypatch.setattr(app, "APPS_FILE", str(etat / "apps.json"))
    monkeypatch.setattr(app, "DIAGNOSTIC_MARQUEUR", str(etat / "diagnostic-inscrit"))
    monkeypatch.setattr(app, "ROOT", str(racine))
    app._apps_cache["signature"] = None
    return racine


def test_le_diagnostic_est_inscrit_au_premier_demarrage(tmp_path, monkeypatch):
    racine = _amorcage(tmp_path, monkeypatch)
    assert app.amorcer_diagnostic() == "diagnostic"
    inscrit = app.load()["diagnostic"]
    assert inscrit["path"] == str(racine / "diagnostic")
    assert inscrit["command"] == app.DIAGNOSTIC_COMMANDE
    assert inscrit["build_command"] == app.DIAGNOSTIC_BUILD
    # Pas demarree ici : c'est le thread d'amorcage qui la lance, apres le
    # build qui installe son pilote Postgres.
    assert inscrit["enabled"] is False
    assert inscrit["visibility"] == app.VISIBILITE_PRIVEE
    assert app.PORT_MIN <= inscrit["port"] <= app.PORT_MAX


def test_un_diagnostic_supprime_ne_revient_pas_au_redemarrage(tmp_path, monkeypatch):
    """Le defaut a empecher : supprimer le projet depuis le panneau, puis le
    retrouver au demarrage suivant."""
    _amorcage(tmp_path, monkeypatch)
    app.amorcer_diagnostic()
    app.save({})                       # suppression depuis le panneau
    assert app.amorcer_diagnostic() is None
    assert app.load() == {}


def test_un_panneau_deja_utilise_n_est_pas_touche(tmp_path, monkeypatch):
    """Mise a jour d'une installation existante : le registre a deja des
    applications, on n'y ajoute rien -- l'utilisateur a peut-etre inscrit ce
    projet lui-meme, ou l'a supprime volontairement."""
    _amorcage(tmp_path, monkeypatch)
    app.save({"mon-site": {"port": 9101}})
    assert app.amorcer_diagnostic() is None
    assert list(app.load()) == ["mon-site"]
    # Et la question est tranchee pour de bon, meme si le panneau se vide.
    app.save({})
    assert app.amorcer_diagnostic() is None


def test_un_projet_pas_encore_amorce_est_retente_au_demarrage_suivant(tmp_path, monkeypatch):
    """app-manager et dagster demarrent en parallele, et c'est dagster qui
    depose le projet dans /workspace : au premier demarrage le dossier peut
    ne pas encore exister. Renoncer definitivement ici priverait l'utilisateur
    du projet pour une simple question d'ordre de demarrage."""
    racine = _amorcage(tmp_path, monkeypatch, avec_projet=False)
    assert app.amorcer_diagnostic() is None
    assert not os.path.exists(app.DIAGNOSTIC_MARQUEUR)

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
    assert etapes["Un intermediaire relaie cette requete"] is False
    assert etapes["La requete arrive en HTTPS"] is False
    # Toute etape non faite doit porter la marche a suivre : un diagnostic qui
    # dit "non" sans dire quoi faire ne sert qu'a inquieter.
    assert all(e["aide"] for e in d["diagnostic"] if not e["ok"])


def test_le_diagnostic_voit_le_proxy_quand_il_est_la(vps):
    d = vps.get("/api/vps", headers={"X-Forwarded-For": "203.0.113.7",
                                     "X-Forwarded-Proto": "https"}).get_json()
    etapes = {e["etape"]: e["ok"] for e in d["diagnostic"]}
    assert etapes["Un intermediaire relaie cette requete"] is True
    assert etapes["La requete arrive en HTTPS"] is True
    # Le proxy n'est pas declare pour autant : constater n'est pas croire.
    assert etapes["Le proxy est declare de confiance"] is False


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


def test_l_admin_change_son_mot_de_passe(compte_admin):
    r = compte_admin.post("/api/compte/mot-de-passe",
                          json={"ancien": "ancien-mot-de-passe",
                                "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 200, r.data
    assert app.admin_password() == "un-nouveau-mot-de-passe"
    # Il survit au redemarrage : c'est credentials.env qui fait autorite.
    assert app.read_shared_value("APP_MANAGER_ADMIN_PASSWORD") == "un-nouveau-mot-de-passe"


def test_la_cle_de_session_n_est_pas_remplacee_au_passage(compte_admin):
    """Ecrire une cle differente de celle en place deconnecterait tout le
    monde au redemarrage suivant, sans rapport visible avec le changement de
    mot de passe. Elle est donc relue a sa source, jamais reconstituee."""
    compte_admin.post("/api/compte/mot-de-passe",
                      json={"ancien": "ancien-mot-de-passe",
                            "nouveau": "un-nouveau-mot-de-passe"})
    assert app.read_shared_value("APP_MANAGER_SESSION_SECRET") == "cle-de-session-existante"


def test_une_session_volee_ne_verrouille_pas_le_compte(compte_admin):
    """L'ancien mot de passe est exige meme sur une session deja ouverte :
    sans cela, un cookie capture suffirait a prendre la place de quelqu'un
    definitivement."""
    r = compte_admin.post("/api/compte/mot-de-passe",
                          json={"ancien": "pas-le-bon",
                                "nouveau": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403, r.data
    assert app.admin_password() == "ancien-mot-de-passe"


def test_un_mot_de_passe_trop_court_est_refuse(compte_admin):
    r = compte_admin.post("/api/compte/mot-de-passe",
                          json={"ancien": "ancien-mot-de-passe", "nouveau": "court"})
    assert r.status_code == 400, r.data
    assert app.admin_password() == "ancien-mot-de-passe"


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


def test_l_administrateur_garde_le_choix_de_son_second_facteur(deux_espaces):
    """Le rendre obligatoire pour lui aussi pourrait l'enfermer hors de son
    propre panneau : c'est un reglage, pas une regle."""
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
                   "param-categories", "param-alertes"):
        assert f'id="{onglet}"' in page


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

    brut = open(app.UTILISATEURS_FILE).read()
    assert code not in brut, "le code ne doit etre range que sous forme d'empreinte"

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


def test_un_compte_inscrit_librement_n_ouvre_rien_et_attend_son_code(comptes_mail):
    """Le compte existe, mais il ne se connecte pas et n'autorise aucun projet."""
    c, partis = comptes_mail
    app.save({"prive": {"path": "/w/a", "command": "x", "port": 9101,
                        "enabled": True, "visibility": "privee"}})
    r = c.post("/inscription", json={"nom": "paul", "email": "paul@example.com",
                                     "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 200, r.data
    assert app.lire_utilisateurs()["paul"]["projets"] == []

    # Mot de passe juste, et pourtant pas de session : l'adresse n'a pas
    # encore repondu.
    r = c.post("/login", json={"nom": "paul", "password": "mot-de-passe-long"})
    assert r.status_code == 403
    assert r.get_json().get("attente_email") is True
    assert c.get("/api/mes-apps").status_code == 401

    app._login_attempts.clear()
    assert c.post("/inscription/confirmer",
                  json={"code": _code_du_dernier_mail(partis)}).status_code == 200
    # Le compte devient utilisable : la connexion demande maintenant le
    # second facteur, comme pour tout compte utilisateur.
    r = c.post("/login", json={"nom": "paul", "password": "mot-de-passe-long"})
    assert r.status_code == 200 and r.get_json().get("inscription") is True


def test_sans_serveur_d_envoi_l_inscription_est_fermee(comptes_mail, tmp_path):
    """Sans mail, une adresse declaree ne peut pas etre verifiee : ouvrir la
    creation de comptes reviendrait a offrir un formulaire a remplir en
    boucle."""
    c, _ = comptes_mail
    (tmp_path / "credentials.env").write_text("")
    assert c.get("/api/inscription").get_json()["ouverte"] is False
    r = c.post("/inscription", json={"nom": "paul", "email": "paul@example.com",
                                     "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 403
    assert "paul" not in app.lire_utilisateurs()


def test_un_mail_qui_ne_part_pas_ne_laisse_pas_un_nom_pris(comptes_mail, monkeypatch):
    """Sinon le nom serait pris par quelqu'un qui ne pourra jamais s'en servir."""
    c, _ = comptes_mail
    def refuse(*a, **k):
        raise OSError("relais injoignable")
    monkeypatch.setattr(app, "envoyer_mail", refuse)
    r = c.post("/inscription", json={"nom": "paul", "email": "paul@example.com",
                                     "mot_de_passe": "mot-de-passe-long"})
    assert r.status_code == 502
    assert "paul" not in app.lire_utilisateurs()


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


def test_le_journal_tourne_au_lieu_de_remplir_le_disque(journal, monkeypatch):
    """Un journal sans plafond transforme une curiosite en panne."""
    monkeypatch.setattr(app, "ACCES_MAX_OCTETS", 400)
    for i in range(60):
        app.journaliser("ouverture", qui=f"compte{i}", app="public", ip="10.0.0.1")
    assert os.path.getsize(app.ACCES_FILE) <= 400 + 200   # la ligne en cours
    assert os.path.exists(app.ACCES_FILE + ".1")
    # Rien n'est perdu tant que la rotation n'a pas tourne deux fois : les
    # deux fichiers sont relus ensemble.
    assert len(app.lire_acces(limite=1000)) > 1


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
        "inscription_creer", "inscription_confirmer",
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
    assert 'cron_schedule="*/15 * * * *"' in src
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
    lecture seule tournent toutes les quinze minutes.
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
    assert "s'est arretee aussitot" in bloc


def test_l_api_toggle_rend_compte_de_l_echec():
    src = open(os.path.join(DOSSIER_PANNEAU, "app", "app.py"), encoding="utf-8").read()
    bloc = src[src.index("def api_toggle("):]
    bloc = bloc[:bloc.index("def ", 10)]
    assert "erreur = start(n)" in bloc, "l'API ignore ce que start() lui rend"
    assert "409" in bloc, (
        "un echec de demarrage doit se voir dans le code de reponse")


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
    # Aucune couleur en dur ne doit revenir dans sa feuille de style.
    css = src[src.index("CSS = "):src.index('"""', src.index("CSS = ") + 10)]
    assert "#" not in css.replace("#{", ""), (
        "une couleur en dur est revenue dans le CSS du diagnostic")


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
        assert isinstance(ok, bool) and nom and detail


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
    assert ok is False
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
    """Depuis qu'un planning declenche le diagnostic toutes les quinze
    minutes, les runs arrivent tout seuls. Sans coordinateur, Dagster les
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


def test_les_applications_repondent_sur_leur_port(deux_origines):
    c = deux_origines
    r = c.get("/public/", **_sur_port(9302))
    # Pas 404 : la route du proxy est bien atteinte (l'application n'ecoute
    # pas dans un test, d'ou l'erreur de passerelle).
    assert r.status_code != 404
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

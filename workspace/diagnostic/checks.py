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


def _port_applications():
    return int(os.environ.get("APP_MANAGER_APPS_PORT") or 0)


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
    if not port:
        return (False, "origine des applications",
                "APP_MANAGER_APPS_PORT absent : les applications sont servies "
                "par le panneau, donc dans SON origine")
    base = f"http://127.0.0.1:{port}"
    try:
        urllib.request.urlopen(base + "/health", timeout=4).getcode()
    except Exception as e:                                        # noqa: BLE001
        return (False, "origine des applications",
                f"port {port} ferme ({e}) -- publie-le dans docker-compose.yml")
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
    if manques:
        return (False, "exposition", "expose, mais il manque : "
                + " ; ".join(manques) + " -- a poser dans Parametres > Exposition")
    return True, "exposition", f"publie sur {publique}, cookie Secure, adresse reelle des visiteurs"


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
                "le noyau refuse de creer un namespace utilisateur (%s). Les "
                "applications demarrent, mais sans etre isolees. Sur l'hote : "
                "sysctl -w kernel.unprivileged_userns_clone=1 et "
                "user.max_user_namespaces=15000. Pour assumer le choix et "
                "faire taire cette sonde : APP_MANAGER_ISOLER=0"
                % (refus or "raison inconnue"))

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
    "ACCES_FILE", "CHILD_HOME", "ALERTES_FILE", "CATEGORIES_FILE",
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
    assert "kernel.unprivileged_userns_clone" in detail


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
# L'entrypoint sait maintenant reconnaitre un fichier qui est l'une de nos
# anciennes versions -- son empreinte figure dans dagster/squelette.sums --
# et lui seul est remplace. Les tests ci-dessous font tourner LE VRAI BLOC,
# extrait du vrai entrypoint : une reecriture du shell dans le test ne
# prouverait que la justesse du test.

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


def _scene(tmp_path, sur_disque, livre, amorce=True, connues=()):
    """Monte un faux workspace et joue le bloc dessus.

    sur_disque : contenu de /workspace/diagnostic/checks.py, ou None pour
                 simuler un fichier supprime par l'utilisateur.
    livre      : contenu de la version portee par l'image.
    connues    : contenus a declarer comme "versions deja livrees par
                 CodeLab" -- c'est ce que fait dagster/squelette.sums.
    """
    import hashlib
    import subprocess

    seed = tmp_path / "seed" / "diagnostic"
    ws = tmp_path / "ws" / "diagnostic"
    seed.mkdir(parents=True)
    ws.mkdir(parents=True)
    (tmp_path / "ws" / ".codelab").mkdir()
    (seed / "checks.py").write_text(livre, encoding="utf-8")
    if sur_disque is not None:
        (ws / "checks.py").write_text(sur_disque, encoding="utf-8")
    if amorce:
        (tmp_path / "ws" / ".codelab" / "workspace-v1").write_text("marqueur")

    sommes = tmp_path / "squelette.sums"
    sommes.write_text("".join(
        hashlib.sha256(c.encode()).hexdigest() + " diagnostic/checks.py\n"
        for c in connues), encoding="utf-8")

    env = dict(os.environ,
               WORKSPACE_SEED=str(tmp_path / "seed"),
               WORKSPACE_DIR=str(tmp_path / "ws"),
               SEED_MARKER=str(tmp_path / "ws" / ".codelab" / "workspace-v1"),
               SUMS_TEST=str(sommes),
               CODELAB_GROUP="root")
    r = subprocess.run(["sh", "-c", _bloc_reconciliation()],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    cible = ws / "checks.py"
    return (cible.read_text(encoding="utf-8") if cible.exists() else None,
            r.stdout + r.stderr)


def test_une_ancienne_version_livree_est_remplacee(tmp_path):
    """Le cas qui a motive tout ceci : le disque porte une version que NOUS
    avons livree, elle est donc remplacable sans rien perdre."""
    apres, journal = _scene(tmp_path, sur_disque="ancienne", livre="corrigee",
                            connues=("ancienne", "corrigee"))
    assert apres == "corrigee", "la correction de l'image n'a pas atteint le disque"
    assert "mis a jour" in journal


def test_un_fichier_modifie_par_l_utilisateur_n_est_jamais_ecrase(tmp_path):
    """La regle qui protege le travail : si l'empreinte du disque n'est
    aucune des notres, c'est du travail humain, on n'y touche pas."""
    apres, journal = _scene(tmp_path, sur_disque="MON CODE", livre="corrigee",
                            connues=("ancienne", "corrigee"))
    assert apres == "MON CODE", "le travail de l'utilisateur a ete ecrase"
    assert "modifies sur place" in journal


def test_un_fichier_supprime_expres_ne_ressuscite_pas(tmp_path):
    """Supprimer le projet d'exemple doit tenir. Le voir revenir a chaque
    redemarrage serait insupportable, et c'est la raison d'etre du marqueur."""
    apres, _ = _scene(tmp_path, sur_disque=None, livre="corrigee",
                      connues=("ancienne", "corrigee"))
    assert apres is None, "un fichier supprime a ete recree"


def test_rien_ne_bouge_avant_le_premier_amorcage(tmp_path):
    """Sans marqueur, l'amorcage classique n'a pas encore eu lieu : cette
    passe n'a rien a faire et ne doit surtout pas prendre les devants."""
    apres, _ = _scene(tmp_path, sur_disque="ancienne", livre="corrigee",
                      amorce=False, connues=("ancienne", "corrigee"))
    assert apres == "ancienne"


def test_un_lien_symbolique_a_la_place_du_fichier_est_refuse(tmp_path):
    """Ce bloc tourne en root et /workspace est inscriptible par les
    applications. Un lien pose a la place d'un fichier du squelette ne doit
    jamais etre suivi : ce serait une ecriture root arbitraire offerte a
    n'importe quelle application du panneau."""
    import subprocess

    victime = tmp_path / "victime"
    victime.write_text("intact", encoding="utf-8")

    seed = tmp_path / "seed" / "diagnostic"
    ws = tmp_path / "ws" / "diagnostic"
    seed.mkdir(parents=True)
    ws.mkdir(parents=True)
    (tmp_path / "ws" / ".codelab").mkdir()
    (tmp_path / "ws" / ".codelab" / "workspace-v1").write_text("marqueur")
    (seed / "checks.py").write_text("corrigee", encoding="utf-8")
    os.symlink(str(victime), str(ws / "checks.py"))

    sommes = tmp_path / "squelette.sums"
    sommes.write_text(_somme(str(victime)) + " diagnostic/checks.py\n",
                      encoding="utf-8")

    env = dict(os.environ,
               WORKSPACE_SEED=str(tmp_path / "seed"),
               WORKSPACE_DIR=str(tmp_path / "ws"),
               SEED_MARKER=str(tmp_path / "ws" / ".codelab" / "workspace-v1"),
               SUMS_TEST=str(sommes), CODELAB_GROUP="root")
    r = subprocess.run(["sh", "-c", _bloc_reconciliation()],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr

    assert victime.read_text(encoding="utf-8") == "intact", (
        "le lien a ete suivi : ecriture hors du workspace, en root")
    assert os.path.islink(str(ws / "checks.py")), "le lien a ete remplace"


def test_un_temporaire_pose_d_avance_ne_detourne_pas_l_ecriture(tmp_path):
    """Regression : tant que le fichier temporaire portait un nom
    previsible, une application pouvait poser d'avance un lien a ce nom et
    faire ecrire root dans la cible de son choix. Le nom est desormais tire
    par mktemp, qui cree le fichier sans jamais suivre un lien existant."""
    import subprocess

    victime = tmp_path / "victime"
    victime.write_text("intact", encoding="utf-8")

    seed = tmp_path / "seed" / "diagnostic"
    ws = tmp_path / "ws" / "diagnostic"
    seed.mkdir(parents=True)
    ws.mkdir(parents=True)
    (tmp_path / "ws" / ".codelab").mkdir()
    (tmp_path / "ws" / ".codelab" / "workspace-v1").write_text("marqueur")
    (seed / "checks.py").write_text("corrigee", encoding="utf-8")
    (ws / "checks.py").write_text("ancienne", encoding="utf-8")
    # Le piege, au nom qu'utilisait l'ancienne version du code.
    os.symlink(str(victime), str(ws / "checks.py.codelab-tmp"))

    import hashlib
    sommes = tmp_path / "squelette.sums"
    sommes.write_text(
        hashlib.sha256(b"ancienne").hexdigest() + " diagnostic/checks.py\n",
        encoding="utf-8")

    env = dict(os.environ,
               WORKSPACE_SEED=str(tmp_path / "seed"),
               WORKSPACE_DIR=str(tmp_path / "ws"),
               SEED_MARKER=str(tmp_path / "ws" / ".codelab" / "workspace-v1"),
               SUMS_TEST=str(sommes), CODELAB_GROUP="root")
    r = subprocess.run(["sh", "-c", _bloc_reconciliation()],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr

    assert victime.read_text(encoding="utf-8") == "intact", (
        "le temporaire previsible a detourne l'ecriture, en root")
    # La mise a jour legitime doit quand meme avoir eu lieu.
    assert (ws / "checks.py").read_text(encoding="utf-8") == "corrigee"


def test_le_manifeste_des_empreintes_est_a_jour():
    """Le filet du filet.

    Si quelqu'un modifie un fichier du squelette sans relancer
    dagster/empreintes-squelette.sh, la version courante n'est plus reconnue
    comme etant la notre. Consequence silencieuse : la mise a jour cesse de
    fonctionner pour ce fichier, sans que rien n'echoue. Ce test rend cet
    oubli bruyant.
    """
    racine = _racine_depot()
    manifeste = os.path.join(racine, "dagster", "squelette.sums")
    squelette = os.path.join(racine, "workspace")
    if not (os.path.exists(manifeste) and os.path.isdir(squelette)):
        pytest.skip("depot complet absent de cette image")

    connues = set()
    for ligne in open(manifeste, encoding="utf-8"):
        if ligne.startswith("#") or not ligne.strip():
            continue
        connues.add(ligne.strip())

    manquants = []
    for dossier, sous, fichiers in os.walk(squelette):
        sous[:] = [d for d in sous if d != "__pycache__"]
        for nom in fichiers:
            chemin = os.path.join(dossier, nom)
            relatif = os.path.relpath(chemin, squelette)
            if _somme(chemin) + " " + relatif not in connues:
                manquants.append(relatif)

    assert not manquants, (
        "version courante absente de dagster/squelette.sums pour : "
        + ", ".join(sorted(manquants))
        + " -- relancer ./dagster/empreintes-squelette.sh puis committer.")


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
                        lambda cfg, sujet, corps: envoyes.append(sujet))
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
    assert manquants == ["SMTP_HOST", "SMTP_USER (ou ALERTE_FROM)", "destinataires"]


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
                           ("post", "/api/alertes/test"), ("get", "/api/logs/public"),
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
    # Arretee : la fiche refuse de modifier une application en marche, et ce
    # n'est pas ce que ces tests-la verifient.
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

def test_le_serveur_d_envoi_se_teste_sans_alerte_reglee(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_admin_password", "secret-de-test")
    monkeypatch.setattr(app, "ALERTES_FILE", str(tmp_path / "alertes.json"))
    monkeypatch.setattr(app, "SHARED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SHARED_ENV_FILE", str(tmp_path / "credentials.env"))
    app.flask_app.secret_key = "cle-de-test"
    app.flask_app.config["TESTING"] = True
    app._login_attempts.clear()
    (tmp_path / "credentials.env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_USER=panneau@example.com\n")
    app.ecrire_alertes(False, [])          # serveur pret, aucune alerte reglee
    partis = []
    monkeypatch.setattr(app, "envoyer_mail",
                        lambda cfg, sujet, corps, destinataires=None:
                        partis.append(destinataires))

    c = app.flask_app.test_client()
    c.post("/login", json={"password": "secret-de-test"})

    # Le serveur se declare pret, meme sans destinataire d'alerte.
    etat = c.get("/api/alertes").get_json()
    assert etat["smtp_ok"] is True
    assert etat["manquants_smtp"] == []
    assert "destinataires" in etat["manquants"]   # ca, c'est l'affaire des alertes

    # Un test vers une adresse choisie part quand meme.
    r = c.post("/api/alertes/test", json={"destinataire": "moi@example.com"})
    assert r.status_code == 200, r.data
    assert partis == [["moi@example.com"]]

    # Sans adresse, en revanche, il n'y a personne a qui ecrire.
    r = c.post("/api/alertes/test", json={})
    assert r.status_code == 400
    assert "destinataires" in r.get_json()["error"]
    assert len(partis) == 1


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

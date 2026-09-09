"""
Sondes de diagnostic CodeLab -- partagees entre l'application web et l'asset
Dagster, pour que les deux racontent exactement la meme chose.

Uniquement la bibliotheque standard ici. Le pilote Postgres est resolu par
connect_pg(), qui accepte psycopg (v3, installe par la commande de build de
l'application) ou psycopg2 (deja present dans l'image Dagster via
dagster-postgres).
"""
import os
import socket
import stat
import urllib.error
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
        return False, "Cles SSH (droits)", f"{ak} -- {e}"
    if not cles:
        return False, "Cles SSH (droits)", f"{ak} est vide -- aucune connexion SSH ne passera"

    n_hotes = len([x for x in os.listdir(hk)
                   if x.endswith("_key")]) if os.path.isdir(hk) else 0
    return True, "Cles SSH (droits)", (
        f"{len(cles)} cle(s) autorisee(s), lisible(s) par l'uid {u} ; "
        f"{n_hotes} cle(s) hote persistee(s)")


def run_all(env_file=None, workspace=None, ssh_dir=None):
    """Les huit sondes, dans l'ordre ou on veut les lire."""
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
    ]

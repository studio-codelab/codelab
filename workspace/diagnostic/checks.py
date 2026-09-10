"""
Sondes de diagnostic CodeLab -- partagees entre l'application web et l'asset
Dagster, pour que les deux racontent exactement la meme chose.

Uniquement la bibliotheque standard ici. Le pilote Postgres est resolu par
connect_pg(), qui accepte psycopg (v3, installe par la commande de build de
l'application) ou psycopg2 (deja present dans l'image Dagster via
dagster-postgres).
"""
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
    https = (os.environ.get("APP_MANAGER_HTTPS", "").lower() in ("1", "true", "yes"))
    proxy = (os.environ.get("APP_MANAGER_TRUST_PROXY", "").lower() in ("1", "true", "yes"))
    publique = (os.environ.get("APP_MANAGER_PUBLIC_URL") or "").strip()
    if not (https or proxy or publique):
        return (True, "exposition",
                "reseau local : aucune adresse publique declaree, cookie non "
                "marque Secure -- coherent tant que rien n'est devant")
    manques = []
    if not https:
        manques.append("APP_MANAGER_HTTPS (cookie de session non marque Secure)")
    if not proxy:
        manques.append("APP_MANAGER_TRUST_PROXY (tous les visiteurs partagent une adresse)")
    if not publique:
        manques.append("APP_MANAGER_PUBLIC_URL (aucun partage possible)")
    if manques:
        return False, "exposition", "expose, mais il manque : " + " ; ".join(manques)
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
    actif = os.environ.get("APP_MANAGER_ISOLER", "1").lower() not in ("0", "false", "no")
    outil = shutil.which("unshare")
    if not actif:
        return (False, "isolation des applications",
                "APP_MANAGER_ISOLER coupe : chaque application voit les "
                "fichiers de toutes les autres")
    if not outil:
        return (False, "isolation des applications",
                "unshare absent de l'image : les applications demarrent, mais "
                "sans etre isolees les unes des autres")
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


def test_base_ecrit_et_relit():
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


def test_ecriture_refusee_sans_session():
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


def test_proxy_sert_cette_application():
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


def test_le_journal_enregistre():
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


def test_le_projet_est_ecrivable():
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


def run_tests():
    """Les tests a la demande, dans l'ordre ou on veut les lire."""
    return [
        _essai("base : ecriture puis relecture", test_base_ecrit_et_relit),
        _essai("ecriture refusee sans session", test_ecriture_refusee_sans_session),
        _essai("le proxy sert cette application", test_proxy_sert_cette_application),
        _essai("le journal des acces enregistre", test_le_journal_enregistre),
        _essai("le dossier du projet est ecrivable", test_le_projet_est_ecrivable),
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

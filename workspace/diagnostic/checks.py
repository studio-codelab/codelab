"""
Sondes de diagnostic CodeLab -- partagees entre l'application web et l'asset
Dagster, pour que les deux racontent exactement la meme chose.

Uniquement la bibliotheque standard ici, et c'est vrai au pied de la lettre
depuis que la suite de tests a ete detachee dans test_regressions.py : ce
fichier n'importe plus pytest du tout. Il a longtemps fallu un faux module
pytest pour que les decorateurs de la suite s'evaluent a l'import dans les
images qui ne l'embarquent pas -- l'image Dagster, l'image dev. Ce repli n'a
plus lieu d'etre.

Le pilote Postgres est resolu par connect_pg(), qui accepte psycopg (v3,
installe par la commande de build de l'application) ou psycopg2 (deja present
dans l'image Dagster via dagster-postgres).
"""
import ast
import base64
import importlib.util
import queue
import re
import secrets
import sys


import builtins
import inspect
import datetime
import enum
import json
import os
from pathlib import Path
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


# LE MOT DE PASSE N'ARRIVE PAS PAR LE MEME CHEMIN DANS LES TROIS CONTENEURS,
# et ce n'est pas un accident : credentials.env est en 0600 root, or aucun des
# processus qui en ont besoin ne tourne en root. Chaque entrypoint lit donc le
# fichier AVANT d'abandonner ses privileges, et passe la valeur par
# l'environnement -- sous un nom different selon le service, parce que chacun
# la destinait d'abord a sa propre configuration.
#
# Ne lire que POSTGRES_PASSWORD revenait a ignorer les deux autres chemins.
# Cote Dagster, cela se payait comptant : le secret etait la, dans
# l'environnement du processus, et la sonde repondait "fe_sendauth: no
# password supplied" quatre fois par heure.
CLES_MOT_DE_PASSE_PG = (
    # Le fichier partage, ou l'environnement : le cas du panneau, qui lit
    # credentials.env en root et transmet la valeur aux applications qu'il
    # lance sous un uid sans droit dessus.
    "POSTGRES_PASSWORD",
    # codelab-dagster (interface et daemon) : l'entrypoint exporte la
    # valeur sous ce nom (dagster.yaml ne sait lire qu'une variable
    # d'environnement), puis bascule sur l'uid 1002.
    "DAGSTER_PG_PASSWORD",
    # Session SSH dans codelab-dev : l'entrypoint pre-remplit le profil, pour
    # que "psql" fonctionne sans rien saisir. Meme mecanique, meme raison.
    "PGPASSWORD",
)


def mot_de_passe_pg(env_file=None):
    """(valeur, nom de la variable qui l'a fournie), ou (None, "") s'il manque.

    L'origine n'est pas un detail d'affichage : "le fichier est illisible ici,
    mais l'entrypoint a transmis le secret" et "personne n'a transmis le
    secret" se depannent de deux facons opposees, et se lisaient exactement
    pareil tant que la sonde ne disait que "absent".
    """
    for cle in CLES_MOT_DE_PASSE_PG:
        valeur = read_env(cle, env_file)
        if valeur:
            return valeur, cle
    return None, ""


def pg_settings(env_file=None, dbname=None):
    return {
        "host": read_env("POSTGRES_HOST", env_file) or "codelab-postgres",
        "port": int(read_env("POSTGRES_PORT", env_file) or 5432),
        "dbname": dbname or read_env("CODELAB_DB", env_file) or DB,
        "user": read_env("POSTGRES_USER", env_file) or "codelab",
        "password": mot_de_passe_pg(env_file)[0],
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


# ------------------------------ rangs de gravite ------------------------------
#
# QUATRE RANGS, ET C'EST TOUTE LA HIERARCHIE. Avant, une sonde ne savait dire
# que "oui" ou "non", et tout "non" faisait tomber le run Dagster, donc partir
# un mail. Un disque a 87 %, un namespace utilisateur refuse par le noyau et
# une base de donnees injoignable arrivaient au meme rang -- avec le meme
# rouge, la meme alerte, la meme urgence. Trois alertes sur quatre ne
# demandaient aucun geste immediat : c'est exactement comme cela qu'on cesse
# de lire ses alertes, et qu'on rate la quatrieme.
#
#   ECHEC       la stack ne rend plus son service, ou elle est OUVERTE : le
#               secret partage manque, Postgres ne repond pas, un conteneur
#               est tombe, une route d'administration repond sans session, le
#               disque va refuser la prochaine ecriture. Le run echoue, le
#               capteur envoie le mail. On se leve la nuit pour ca.
#
#   ALERTE      degrade, mais ca tourne : l'isolement des applications n'est
#               pas disponible, le disque se remplit, une application
#               declaree ne repond plus, un reglage d'exposition manque. A
#               regarder dans la journee. Le run reste vert -- une alerte
#               n'est pas une panne, et un run rouge de plus est un mail de
#               moins qu'on lira.
#
#   SANS_OBJET  la sonde ne peut pas repondre D'ICI. L'etat du panneau n'est
#               monte que dans codelab-app-manager : depuis Dagster, les
#               sondes qui le lisent voyaient un panneau vide -- zero
#               application, zero compte, rien d'expose -- et affichaient un
#               vert rassurant sur une question qu'elles n'avaient pas posee.
#               Une absence de reponse n'est ni un succes ni un echec.
#
#   OK          verifie, ici, maintenant.
#
# Le rang de chaque sonde n'est pas fige : c'est ce qu'elle CONSTATE qui le
# decide. "Le panneau est injoignable depuis ce conteneur" est une alerte
# (absence de preuve) ; "le panneau repond 200 sans session" est un echec
# (preuve). Le plafond declare dans SEVERITE_MAX, plus bas, garde la
# hierarchie lisible en un seul endroit et empeche une sonde de confort de
# faire tomber un run.

class Etat(enum.IntEnum):
    """Le rang d'un resultat de sonde, ordonne du PIRE au MEILLEUR.

    Ordonne, donc comparable : le verdict d'une page, c'est le minimum de ses
    lignes, et il s'ecrit min(...) plutot qu'en empilant des booleens.

    ECHEC vaut 0, donc il est faux au sens de Python, et les trois autres sont
    vrais. Ce n'est pas une astuce : c'est ce qui permet a tout le code qui
    lisait "ok" de continuer a le lire comme avant, avec le bon sens --
    "cette sonde n'est pas en echec".
    """

    ECHEC = 0
    ALERTE = 1
    SANS_OBJET = 2
    OK = 3

    @property
    def libelle(self):
        """Ce qui s'affiche dans la colonne « Etat » et se prefixe aux logs.

        Les mots sont ceux qu'on emploie en parlant de la page -- OK,
        ATTENTION, ERREUR -- et non les noms internes des rangs. Un tableau
        de bord se lit a voix haute : « il y a deux attentions » se dit,
        « il y a deux alertes de rang 1 » ne se dit pas.
        """
        return {0: "ERREUR", 1: "ATTENTION", 2: "SANS OBJET", 3: "OK"}[int(self)]

    @property
    def classe(self):
        """Le nom de classe CSS, cote page web."""
        return {0: "ko", 1: "warn", 2: "neutre", 3: "ok"}[int(self)]

    @classmethod
    def depuis(cls, valeur):
        """Normalise un booleen en Etat, un Etat restant lui-meme.

        Les tests de la verification approfondie, eux, n'ont que deux
        reponses a donner -- l'action attendue s'est produite, ou non.
        """
        return valeur if isinstance(valeur, cls) else (cls.OK if valeur else cls.ECHEC)


def pire(resultats):
    """Le rang le plus bas d'une liste de resultats : le verdict global."""
    return min((Etat.depuis(e) for e, _, _ in resultats), default=Etat.OK)


def noms_par_rang(resultats):
    """{Etat: [noms de sondes]} -- de quoi ecrire un resume sans reboucler."""
    groupes = {etat: [] for etat in Etat}
    for etat, nom, _ in resultats:
        groupes[Etat.depuis(etat)].append(nom)
    return groupes


# ---------------------------------- sondes ----------------------------------
# Chacune renvoie (etat, titre, detail). Aucune ne leve : une sonde qui echoue
# doit afficher pourquoi, pas faire tomber la page.

def check_config(env_file=None):
    """Volume config monte + secret partage disponible.

    "Disponible" et non "lisible", et c'est tout le sujet de cette sonde :
    credentials.env est en 0600 root, et aucun des processus qui s'en servent
    ne tourne en root. Dans app-manager, les applications tournent sous l'uid
    1001 et le panneau leur transmet les valeurs par l'environnement ; dans
    Dagster, l'entrypoint lit le fichier en root puis bascule sur l'uid 1002
    en exportant DAGSTER_PG_PASSWORD. Un fichier illisible EST donc l'etat
    normal des deux cotes -- ce qui compte, c'est que le secret soit arrive.
    La sonde dit par ou.
    """
    path = env_file or ENV_FILE
    if not os.path.exists(path):
        return (Etat.ECHEC, "credentials.env",
                f"introuvable : {path} (volume config non monté ?)")
    # os.access ne ment pas ici : le seul cas ou ce code tourne en root est
    # celui ou root peut effectivement lire le fichier.
    lisible = os.access(path, os.R_OK)
    pw, origine = mot_de_passe_pg(env_file)
    if not pw:
        # UN SEUL message pour les deux cas, et il nomme les trois chemins
        # possibles. "lisible, mais POSTGRES_PASSWORD absent" laissait croire
        # que tout se jouait dans ce fichier, alors que deux conteneurs sur
        # trois recoivent le secret par l'environnement.
        etat_fichier = "lisible" if lisible else f"illisible sous l'uid {os.geteuid()}"
        return (Etat.ECHEC, "credentials.env",
                f"aucun mot de passe Postgres : {path} est {etat_fichier} et ne le "
                f"porte pas, et aucune des variables "
                f"{', '.join(CLES_MOT_DE_PASSE_PG)} n'a été transmise par "
                f"l'entrypoint de ce conteneur")
    # L'origine se CONSTATE, elle ne se deduit pas des droits du fichier :
    # dans app-manager le fichier est lisible (le panneau tourne en root) ET
    # la valeur peut venir de l'environnement. Annoncer "lu dans
    # credentials.env" sans regarder ce qu'il contient enverrait corriger un
    # fichier qui n'est pour rien dans ce qui arrive.
    if _lire_fichier(path).get("POSTGRES_PASSWORD") == pw:
        d_ou = f"lu dans {path}"
    elif lisible:
        d_ou = (f"transmis par l'environnement ({origine}) ; {path} est lisible "
                f"mais ne porte pas cette valeur")
    else:
        d_ou = (f"transmis par l'environnement ({origine}) -- normal : {path} est "
                f"illisible sous l'uid {os.geteuid()}")
    return (Etat.OK, "credentials.env",
            f"{d_ou} -- mot de passe Postgres disponible ({len(pw)} caractères)")


def check_workspace(workspace=None):
    """Volume /workspace partage entre dev, dagster et app-manager."""
    root = workspace or WORKSPACE
    if not os.path.isdir(root):
        return Etat.ECHEC, "/workspace", f"{root} n'est pas un dossier (volume non monté ?)"
    defs = os.path.join(root, "definitions.py")
    if not os.path.exists(defs):
        return (Etat.ECHEC, "/workspace",
                f"{root} monté, mais definitions.py absent -- Dagster n'a rien à charger")
    n = len([x for x in os.listdir(root) if not x.startswith(".")])
    return Etat.OK, "/workspace", f"{root} -- definitions.py présent, {n} entrées visibles"


def check_pilote_pg():
    try:
        _, nom = pilote_pg()
        return Etat.OK, "Postgres (pilote)", f"{nom} disponible"
    except PiloteAbsent as e:
        return Etat.ECHEC, "Postgres (pilote)", str(e)


def check_postgres(env_file=None):
    """Reseau + mot de passe partage + base accessible."""
    cfg = pg_settings(env_file)
    try:
        pilote_pg()
    except PiloteAbsent:
        return Etat.ECHEC, "Postgres", "pilote absent -- voir la sonde précédente"
    try:
        conn = connect_pg(env_file)
    except Exception as e:
        # Sans mot de passe, Postgres repond "fe_sendauth: no password
        # supplied" -- exact, et illisible pour qui ne sait pas d'ou le
        # secret est cense venir. On renvoie donc vers la sonde qui le dit,
        # plutot que de laisser chercher du cote du reseau.
        indice = ("" if cfg["password"] else
                  " -- aucun mot de passe disponible dans ce conteneur, "
                  "voir la sonde credentials.env")
        return (Etat.ECHEC, "Postgres",
                f"{cfg['host']}:{cfg['port']} -- {type(e).__name__}: {e}{indice}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            v = cur.fetchone()[0].split(" on ")[0]
        return Etat.OK, "Postgres", (f"{cfg['host']}:{cfg['port']}/{cfg['dbname']} "
                                     f"schema {SCHEMA} -- {v}")
    except Exception as e:
        return Etat.ECHEC, "Postgres", f"connecté mais requête refusée -- {e}"
    finally:
        conn.close()


def check_pgvector(env_file=None):
    """L'extension « vector » est-elle disponible dans la base du projet ?

    Deux questions distinctes, et la nuance compte pour qui doit reparer :

      - le PAQUET est-il dans l'image ? (pg_available_extensions)
      - l'extension est-elle CREEE dans cette base ? (pg_extension)

    Un paquet absent se repare en reconstruisant l'image ; une extension non
    creee, par un CREATE EXTENSION. Repondre « pgvector ne marche pas » aux
    deux enverrait chercher du mauvais cote.

    ALERTE et non ECHEC : une installation qui ne fait pas de recherche
    vectorielle fonctionne parfaitement sans. Ce n'est pas une panne, c'est
    une capacite absente.
    """
    try:
        pilote_pg()
    except PiloteAbsent:
        return (Etat.SANS_OBJET, "pgvector",
                "pilote Postgres absent -- voir la sonde précédente")
    try:
        conn = connect_pg(env_file)
    except Exception:
        return (Etat.SANS_OBJET, "pgvector",
                "base injoignable -- voir la sonde Postgres")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT default_version FROM pg_available_extensions "
                        "WHERE name = 'vector'")
            dispo = cur.fetchone()
            if not dispo:
                return (Etat.ALERTE, "pgvector",
                        "extension absente de l'image : reconstruire "
                        "codelab-postgres, ou s'en passer si aucun projet "
                        "n'en a besoin")
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            creee = cur.fetchone()
        if not creee:
            return (Etat.ALERTE, "pgvector",
                    f"disponible (v{dispo[0]}) mais pas créée dans cette base -- "
                    f"CREATE EXTENSION vector")
        return (Etat.OK, "pgvector", f"extension active, version {creee[0]}")
    except Exception as e:
        return (Etat.ALERTE, "pgvector", f"état indéterminé -- {type(e).__name__}: {e}")
    finally:
        conn.close()


def check_http(nom, url, timeout=4, gravite=Etat.ECHEC):
    """Un service HTTP repond-il ? gravite dit ce que coute son silence.

    Un conteneur qui ne repond plus est une panne franche, pas un reglage a
    revoir : le defaut est donc ECHEC. Le parametre existe pour les services
    dont l'absence degrade sans casser, et il oblige l'appelant a le dire
    explicitement -- ce qui se relit dans run_all().
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return Etat.OK, nom, f"{url} -- HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return Etat.OK, nom, f"{url} -- HTTP {e.code} (service joignable)"
    except urllib.error.URLError as e:
        # Nom resolu mais rien en ecoute : le conteneur tourne, le service
        # qu'il heberge non -- il demarre encore, ou il est tombe. C'est une
        # panne tres differente d'un nom introuvable (conteneur arrete), et
        # les deux se lisent pareil sans cette distinction.
        hote = urllib.parse.urlsplit(url).hostname or nom
        if isinstance(e.reason, ConnectionRefusedError):
            return gravite, nom, (
                f"{url} -- connexion refusée : le conteneur répond mais rien "
                f"n'écoute sur ce port. Le service démarre encore, ou il est "
                f"tombe : docker logs --tail 50 {hote}")
        if isinstance(e.reason, socket.gaierror):
            # Meme lecture que dans check_tcp : le DNS de Docker n'inscrit que
            # les conteneurs demarres.
            return gravite, nom, (
                f"{url} -- nom introuvable ({e.reason}). Conteneur arrêté, ou "
                f"hors du réseau codelab : docker ps -a --filter name={hote}")
        return gravite, nom, f"{url} -- {type(e).__name__}: {e}"
    except Exception as e:
        return gravite, nom, f"{url} -- {type(e).__name__}: {e}"


def check_tcp(nom, host, port, timeout=4, lire_banniere=False, gravite=Etat.ECHEC):
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            if lire_banniere:
                s.settimeout(timeout)
                b = s.recv(128).decode("utf-8", "replace").strip()
                return Etat.OK, nom, f"{host}:{port} -- {b or 'connexion acceptée'}"
            return Etat.OK, nom, f"{host}:{port} -- connexion acceptée"
    except socket.gaierror as e:
        # Le DNS de Docker ne connait que les conteneurs DEMARRES du reseau :
        # un nom qui ne resout pas designe presque toujours un conteneur arrete.
        return gravite, nom, (f"{host}:{port} -- nom introuvable ({e}). Conteneur arrêté, "
                              f"ou hors du réseau codelab : docker ps -a --filter name={host}")
    except Exception as e:
        return gravite, nom, f"{host}:{port} -- {type(e).__name__}: {e}"


def check_cles_ssh(ssh_dir=None, uid=None):
    """Droits sur authorized_keys, du point de vue de l'utilisateur SSH.

    sshd lit les cles hote en root, mais ouvre authorized_keys APRES avoir
    pris l'uid de l'utilisateur cible. Un dossier non traversable ou un
    fichier non lisible par lui donne un "Permission denied (publickey)"
    cote client, strictement identique a celui d'une cle absente. C'est
    exactement le genre de panne qu'aucune sonde reseau ne verra.

    RANG 2, jamais plus : SSH est la porte d'entree de l'humain, pas le
    chemin de donnees de la stack. Dagster, Postgres et le panneau
    fonctionnent identiquement sans aucune cle deposee -- et une installation
    fraiche n'en a justement aucune, c'est l'etat voulu tant qu'on n'a pas
    autorise sa premiere machine. En faire un echec, c'etait teindre en rouge
    une installation neuve et parfaitement saine."""
    d = ssh_dir or SSH_DIR
    u = SSH_UID if uid is None else uid
    ak = os.path.join(d, "authorized_keys")
    hk = os.path.join(d, "host_keys")

    if not os.path.isdir(d):
        return (Etat.ALERTE, "Clés SSH (droits)",
                f"{d} absent -- codelab-dev n'a jamais démarré ?")
    if not _accessible_par(u, d, stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH):
        return Etat.ALERTE, "Clés SSH (droits)", (
            f"{d} est en {_mode(d)} : l'uid {u} ne peut pas le traverser, donc sshd "
            f"n'atteindra jamais authorized_keys. Corriger : chmod 755 {d}")
    if not os.path.exists(ak):
        return (Etat.ALERTE, "Clés SSH (droits)",
                f"{ak} absent -- aucune machine autorisée. C'est l'état d'une "
                f"installation neuve : déposer une clé publique dans "
                f"{os.path.join(d, 'authorized_keys.d')} ouvre l'accès SSH.")
    if not _accessible_par(u, ak, stat.S_IRUSR, stat.S_IRGRP, stat.S_IROTH):
        st = os.stat(ak)
        return Etat.ALERTE, "Clés SSH (droits)", (
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
            return Etat.ALERTE, "Clés SSH (droits)", (
                f"{ak} est vide -- aucune connexion SSH ne passera")
        return Etat.OK, "Clés SSH (droits)", (
            f"droits corrects pour l'uid {u} ; contenu non vérifiable depuis "
            f"ce conteneur, qui tourne sous l'uid {os.geteuid()} ({e.strerror}) "
            f"-- {_taille(ak)} octets")
    if not cles:
        return (Etat.ALERTE, "Clés SSH (droits)",
                f"{ak} est vide -- aucune connexion SSH ne passera")

    try:
        n_hotes = len([x for x in os.listdir(hk)
                       if x.endswith("_key")]) if os.path.isdir(hk) else 0
        detail_hotes = f"{n_hotes} cle(s) hote persistee(s)"
    except OSError:
        # Meme raison : le dossier des cles hote appartient a sshd, pas a nous.
        detail_hotes = "cles hote non listables depuis ce conteneur"
    return Etat.OK, "Clés SSH (droits)", (
        f"{len(cles)} clé(s) autorisée(s), lisible(s) par l'uid {u} ; "
        f"{detail_hotes}")


# ---------------------------------------------------------------- securite
#
# Les sondes ci-dessus disent si la stack MARCHE. Celles-ci disent si elle est
# correctement FERMEE -- ce qu'un controle du code ne peut pas dire, parce que
# cela depend de la configuration reelle : une variable oubliee, un port non
# publie, une garde active en developpement et pas en service.
#
# Elles interrogent le panneau sans jamais presenter d'identifiants : elles
# verifient precisement que ce qui DEVRAIT demander une session en demande
# bien une.
#
# Aucune n'ecrit ni ne casse quoi que ce soit : ce sont des lectures, et des
# ecritures qui doivent etre REFUSEES. Si l'une d'elles passe, c'est le
# probleme.
#
# PREUVE ET ABSENCE DE PREUVE, et c'est ce qui decide de leur rang. Une route
# d'administration qui repond 200 sans session est une breche CONSTATEE :
# echec, on le corrige tout de suite. Un panneau qu'on n'arrive pas a joindre
# depuis ce conteneur-ci ne prouve rien du tout -- c'est une alerte, et la
# sonde de service "codelab-app-manager" dit, elle, si le panneau tourne. Les
# confondre, c'etait faire tomber le run de diagnostic quatre fois par heure
# pour une question restee sans reponse.

def _port_panneau():
    return int(os.environ.get("MANAGER_PORT") or 9001)


# Le panneau n'est pas toujours dans le meme conteneur que ces sondes.
#
# POURQUOI CE N'EST PLUS "127.0.0.1 OU RIEN". Ce fichier est joue depuis DEUX
# endroits : app.py, dans codelab-app-manager -- la, 127.0.0.1 est bien le
# panneau -- et l'asset Dagster, dans codelab-dagster, ou 127.0.0.1 est le
# conteneur Dagster. Personne n'y ecoute sur 9001 : les deux sondes de
# fermeture annoncaient "panneau injoignable" et "rien ne repond sur le port
# 9002" a chaque passage du planning, sur une stack ou les deux ports
# repondaient parfaitement. Deux echecs par run, tous les quarts d'heure,
# pour une adresse mal choisie.
#
# Le nom de service, lui, est resolu par le DNS du reseau codelab depuis
# n'importe quel conteneur de la stack. On garde 127.0.0.1 en premier : quand
# on EST dans le panneau, c'est la reponse la plus directe, et c'est aussi la
# seule qui teste ce que le conteneur expose vraiment.
HOTES_PANNEAU = ("127.0.0.1", "codelab-app-manager")


def _hotes_panneau():
    """Ou chercher le panneau, dans l'ordre. CODELAB_PANNEAU_HOTE tranche."""
    force = (os.environ.get("CODELAB_PANNEAU_HOTE") or "").strip()
    return (force,) if force else HOTES_PANNEAU


def _joindre_panneau(port, chemin="/health", timeout=4):
    """Trouve l'hote ou le panneau repond sur ce port.

    Renvoie (hote, code HTTP) au premier qui repond -- y compris par une
    erreur HTTP, qui prouve qu'un service est bien la -- ou (None, raisons)
    si aucun ne repond, avec ce qu'on a essaye et pourquoi cela a echoue.
    """
    raisons = []
    for hote in _hotes_panneau():
        url = f"http://{hote}:{port}{chemin}"
        try:
            return hote, urllib.request.urlopen(url, timeout=timeout).getcode()
        except urllib.error.HTTPError as e:
            return hote, e.code
        except Exception as e:                                    # noqa: BLE001
            raisons.append(f"{hote}:{port} ({e})")
    return None, " ; ".join(raisons)


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
    port = _port_panneau()
    hote, info = _joindre_panneau(port)
    if hote is None:
        return (Etat.ALERTE, "panneau fermé",
                f"panneau injoignable depuis ce conteneur ({info}) : la sonde ne "
                f"peut pas conclure. Ce n'est pas une brèche constatée, c'est une "
                f"absence de preuve -- la sonde codelab-app-manager dit, elle, si "
                f"le service tourne.")
    base = f"http://{hote}:{port}"
    ouvertes = []
    for chemin in ("/api/apps", "/api/utilisateurs", "/api/activite", "/api/securite"):
        try:
            code = urllib.request.urlopen(base + chemin, timeout=4).getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:                                    # noqa: BLE001
            return Etat.ALERTE, "panneau fermé", f"{base}{chemin} injoignable : {e}"
        if code == 200:
            ouvertes.append(chemin)
    if ouvertes:
        return (Etat.ECHEC, "panneau fermé",
                f"{base} répond 200 sans session : " + ", ".join(ouvertes))
    return (Etat.OK, "panneau fermé",
            f"{base} -- les routes d'administration exigent une session")


def check_origine_applications():
    """Les applications sont-elles servies dans une autre origine ?

    Tant qu'elles vivent sous le port du panneau, une faille dans l'une
    d'elles donne acces au panneau : meme origine, donc meme page, meme
    jeton. Un port distinct fait du navigateur l'arbitre.
    """
    port = _port_applications()
    hote, info = _joindre_panneau(port)
    if hote is None:
        # Rang 2 : rien n'est CONSTATE ici. Ou le port n'est pas publie -- et
        # le panneau sert alors les applications dans sa propre origine, ce
        # qu'il dit lui-meme dans Parametres > Serveur -- ou l'on regarde
        # depuis un conteneur qui n'a pas ce port. Dans les deux cas, il n'y
        # a pas de faille prouvee, donc pas de quoi faire tomber un run.
        return (Etat.ALERTE, "origine des applications",
                f"rien ne répond sur le port {port} ({info}) -- publie-le dans "
                f"docker-compose.yml, sinon les applications repartent dans "
                f"l'origine du panneau")
    base = f"http://{hote}:{port}"
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
        # La, c'est constate : le panneau repond sur l'origine des
        # applications, donc une faille dans l'une d'elles atteint le
        # panneau. Rang 1.
        return (Etat.ECHEC, "origine des applications",
                f"le panneau répond sur {base} : " + ", ".join(fuites))
    return (Etat.OK, "origine des applications",
            f"{base} -- le panneau n'y répond pas, les origines sont bien séparées")


def check_exposition():
    """Ce qui doit etre pose quand le panneau sort du reseau local.

    Ces variables ne se devinent pas depuis le code : elles dependent de ce
    qu'on a mis devant. Tant que la stack reste chez soi, leur absence est
    normale -- la sonde le dit plutot que de crier au feu.
    """
    # DEUX SOURCES, ET IL FAUT LES DEUX. Ces reglages se posent desormais
    # depuis la page Exposition du panneau, qui les ecrit dans
    # la base du panneau ; la variable d'environnement reste prioritaire quand
    # le compose la fixe. Ne lire que l'environnement, comme le faisait cette
    # sonde, annoncait "il manque APP_MANAGER_HTTPS" sur une installation ou
    # HTTPS etait deja active depuis la page -- la sonde reclamait ce qui
    # etait deja fait.
    pose = _exposition_panneau() or {}

    # Ni le fichier ni une variable : on ne regarde pas le bon conteneur.
    # Repondre "reseau local, tout va bien" ici serait inventer une reponse a
    # partir de fichiers qu'on n'a simplement pas sous les yeux.
    if not pose and not _etat_panneau_visible() and not any(
            (os.environ.get(v) or "").strip() for v in
            ("APP_MANAGER_HTTPS", "APP_MANAGER_PUBLIC_URL")):
        return Etat.SANS_OBJET, "exposition", _hors_de_portee()

    # Proxy de confiance et administration locale ne sont plus des reglages :
    # X-Forwarded-For n'est jamais cru, l'administration ne repond qu'au
    # reseau local. Le cookie Secure suit la connexion (https constate) ; la
    # sonde, hors requete, ne voit que l'adresse publique declaree.
    publique = ((os.environ.get("APP_MANAGER_PUBLIC_URL") or "").strip()
                or str(pose.get("adresse_publique") or "").strip())
    if not publique:
        return (Etat.OK, "exposition",
                "réseau local : aucune adresse publique déclarée -- cohérent "
                "tant que rien n'est devant")
    portee = "administration reservee au reseau local"
    manques = []
    if not publique.lower().startswith("https://"):
        manques.append("HTTPS (adresse publique en http://, cookie de session non marque Secure)")
    if manques:
        # Rang 2 : la stack tourne, et ce qui manque est un REGLAGE a poser,
        # pas un service tombe. Le dire en rouge sanglant a chaque quart
        # d'heure n'accelere pas sa pose -- cela apprend a ne plus lire.
        return (Etat.ALERTE, "exposition", "exposé, mais il manque : "
                + " ; ".join(manques) + " -- à poser dans Paramètres > Exposition"
                + " (" + portee + ")")
    return (Etat.OK, "exposition",
            f"publié sur {publique}, cookie Secure, adresse réelle des visiteurs, {portee}")


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
    if not _etat_panneau_visible():
        return Etat.SANS_OBJET, "provenance des connexions", _hors_de_portee()
    ips = _ips_du_journal()
    if ips is None:
        return Etat.SANS_OBJET, "provenance des connexions", "journal illisible ici"
    if not ips:
        return (Etat.OK, "provenance des connexions",
                "aucune connexion enregistrée pour l'instant")

    dehors, dedans = {}, 0
    for ip in ips:
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
        return (Etat.OK, "provenance des connexions",
                f"{dedans} connexions, toutes depuis le réseau local")
    resume = ", ".join(f"{ip} ({n}x)" for ip, n in
                       sorted(dehors.items(), key=lambda x: -x[1])[:3])
    if publique:
        return (Etat.OK, "provenance des connexions",
                f"{dedans} depuis le réseau local, {sum(dehors.values())} depuis "
                f"l'extérieur -- attendu, la stack est publiée sur {publique}")
    # Rang 2, et le mot compte : ces adresses sont a EXPLIQUER, pas une
    # intrusion prouvee. Un tunnel, un VPN de maison ou un proxy monte sans
    # declarer APP_MANAGER_PUBLIC_URL donnent exactement cette trace.
    return (Etat.ALERTE, "provenance des connexions",
            f"des connexions viennent de l'EXTÉRIEUR alors qu'aucune adresse "
            f"publique n'est déclarée : {resume}. Vérifie ce que ta box "
            f"redirige, et borne les ports au réseau local.")


def _etat_panneau():
    """Le dossier d'etat du panneau, vu depuis ce projet."""
    return os.environ.get("APP_MANAGER_STATE") or "/var/lib/codelab/app-manager"


def _etat_panneau_visible():
    """Le dossier d'etat du panneau est-il monte dans CE conteneur ?

    Il ne l'est que dans codelab-app-manager -- c'est son volume, il contient
    les comptes, les cles d'acces et le journal des acces, et rien ne
    justifierait de l'ouvrir au conteneur qui execute le code des jobs.
    Depuis Dagster, les sondes qui le lisent y trouvaient donc un panneau
    vide : zero application, zero compte, rien d'expose -- et affichaient un
    vert rassurant sur une question qu'elles n'avaient jamais pu poser. Un
    faux OK est pire qu'une case vide : il fait cesser de chercher.
    """
    return os.path.isdir(_etat_panneau())


def _hors_de_portee():
    return (f"l'état du panneau ({_etat_panneau()}) n'est pas monté dans ce "
            f"conteneur : cette sonde ne conclut que depuis codelab-app-manager "
            f"(page de diagnostic du panneau)")


# ------------------- la base du panneau, en lecture seule -------------------
#
# Le panneau range ses donnees dans une base SQLite, codelab.db (voir
# app-manager/app/stockage.py). Les sondes la lisent en LECTURE SEULE : un
# diagnostic ne doit jamais pouvoir modifier ce qu'il observe. Les anciens
# fichiers JSON restent le repli, pour une installation pas encore migree.

def _base_panneau():
    return os.path.join(_etat_panneau(), "codelab.db")


def _requete_panneau(requete, parametres=()):
    """Les lignes de la requete, ou None si la base n'est pas lisible ici."""
    import sqlite3
    chemin = _base_panneau()
    if not os.path.exists(chemin):
        return None
    try:
        cx = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True, timeout=5)
        try:
            cx.row_factory = sqlite3.Row
            return cx.execute(requete, parametres).fetchall()
        finally:
            cx.close()
    except sqlite3.Error:
        return None


def _apps_panneau():
    lignes = _requete_panneau("SELECT nom, dossier, commande, port, actif, visibilite "
                              "FROM applications")
    if lignes is None:
        return _lire_json(os.path.join(_etat_panneau(), "apps.json"), {})
    apps = {}
    for l in lignes:
        a = {"path": l["dossier"] or "", "command": l["commande"] or "",
             "port": l["port"], "enabled": bool(l["actif"])}
        if l["visibilite"]:
            a["visibility"] = l["visibilite"]
        apps[l["nom"]] = a
    return apps


def _comptes_panneau():
    lignes = _requete_panneau("SELECT nom, totp FROM comptes")
    if lignes is None:
        return _lire_json(os.path.join(_etat_panneau(), "utilisateurs.json"), {})
    return {l["nom"]: ({"totp": l["totp"]} if l["totp"] else {}) for l in lignes}


def _exposition_panneau():
    lignes = _requete_panneau("SELECT valeur FROM parametres WHERE cle = 'exposition'")
    if lignes is None:
        return _lire_json(os.path.join(_etat_panneau(), "exposition.json"), {})
    try:
        return json.loads(lignes[0]["valeur"]) if lignes else {}
    except ValueError:
        return {}


def _ips_du_journal(nombre=2000):
    """Les adresses des derniers evenements, ou None si le journal est
    illisible ici."""
    lignes = _requete_panneau("SELECT ip FROM journal ORDER BY ts DESC LIMIT ?", (nombre,))
    if lignes is not None:
        return [l["ip"] or "" for l in lignes]
    journal = os.path.join(_etat_panneau(), "acces.jsonl")
    if not os.path.exists(journal):
        return []
    try:
        with open(journal, errors="replace") as f:
            brutes = f.readlines()[-nombre:]
    except OSError:
        return None
    ips = []
    for ligne in brutes:
        try:
            ips.append(json.loads(ligne).get("ip") or "")
        except ValueError:
            continue
    return ips


def _taille_du_journal():
    """Le nombre d'evenements (ou la taille de l'ancien fichier), -1 si
    illisible."""
    lignes = _requete_panneau("SELECT COUNT(*) AS n FROM journal")
    if lignes is not None:
        return lignes[0]["n"]
    try:
        return os.path.getsize(os.path.join(_etat_panneau(), "acces.jsonl"))
    except OSError:
        return -1


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

    RANG 2 : une application tombee n'est pas la stack tombee. Le panneau a
    deja son propre systeme d'alerte pour celles qui ne redemarrent plus, et
    faire echouer le diagnostic de toute l'installation parce qu'un projet
    personnel ne repond plus melange deux choses de portee tres differente.
    """
    if not _etat_panneau_visible():
        return Etat.SANS_OBJET, "applications déclarées", _hors_de_portee()
    apps = _apps_panneau()
    if not apps:
        return (Etat.OK, "applications déclarées",
                "Aucune application déclarée : rien à vérifier.")

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
        return (Etat.ALERTE, "applications déclarées",
                f"{len(soucis)} problème(s) sur {total} application(s) : "
                + " ; ".join(soucis[:4])
                + (" ..." if len(soucis) > 4 else ""))
    return (Etat.OK, "applications déclarées",
            f"{total} application(s), {tournent} en ligne : dossier présent, "
            f"commande résolvable, port à l'écoute.")


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

# ET DEUX PALIERS, parce qu'un disque ne tombe pas en panne : il se remplit.
# "87 %, 2.1 Go libres" laisse des jours pour faire le menage -- c'est une
# alerte, on la traite dans la journee. "96 %, 400 Mo" est autre chose : la
# prochaine ecriture de Postgres echoue, les journaux s'arretent, et les
# messages d'erreur ne parlent jamais d'espace disque. La, c'est un echec, et
# le mail doit partir.
SEUIL_DISQUE_CRITIQUE = 95
SEUIL_LIBRE_CRITIQUE_GO = 1


def check_espace_disque():
    """Ce qui tue une machine auto-hebergee : pas une panne, un disque plein.

    Et cela ne previent pas. Postgres refuse d'ecrire, les journaux
    s'arretent, les builds echouent avec des messages qui ne parlent pas
    d'espace. La sonde regarde les volumes qui comptent, plus le poids des
    journaux du panneau -- ils grossissent tout seuls, a chaque ligne de
    chaque application.
    """
    lignes, alerte, critique = [], False, False
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
        lignes.append(f"{chemin} : {occupe} % occupé, "
                      f"{libre / (1024 ** 3):.1f} Go libres")
        if occupe >= SEUIL_DISQUE and libre < SEUIL_LIBRE_GO * 1024 ** 3:
            alerte = True
        if (occupe >= SEUIL_DISQUE_CRITIQUE
                and libre < SEUIL_LIBRE_CRITIQUE_GO * 1024 ** 3):
            critique = True

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
        return Etat.SANS_OBJET, "espace disque", "Aucun volume lisible."
    if critique:
        return (Etat.ECHEC, "espace disque",
                " | ".join(lignes) + " -- la prochaine écriture peut échouer "
                "(Postgres, journaux, builds), fais de la place maintenant")
    if alerte:
        return (Etat.ALERTE, "espace disque",
                " | ".join(lignes) + f" -- sous {SEUIL_LIBRE_GO} Go libres, "
                f"à traiter dans la journée")
    return Etat.OK, "espace disque", " | ".join(lignes)


# Une sauvegarde de plus de deux jours n'est pas une panne -- la stack rend
# son service -- mais c'est ce qu'on veut savoir AVANT d'en avoir besoin. Le
# jour ou on la cherche, il est trop tard pour decouvrir qu'elle date d'un
# mois.
SAUVEGARDE_FRAICHE_H = 26      # une par jour, plus une marge de decalage
SAUVEGARDE_VIEILLE_H = 72      # au-dela, quelque chose ne tourne plus


def check_sauvegardes(dossier=None):
    """Y a-t-il une sauvegarde, et de quand date-t-elle ?

    Elle ne verifie pas le CONTENU : les deux services qui les ecrivent le
    font deja, chacun au moment ou il ecrit -- pg_restore --list pour les
    bases, une relecture de l'archive pour l'etat du panneau. Un fichier
    illisible est ecarte sur place et n'arrive jamais ici. Ce qui reste a
    surveiller, c'est qu'elles continuent d'arriver.
    """
    dossier = dossier or os.environ.get("CODELAB_SAUVEGARDES_DIR", "/sauvegardes")
    if not os.path.isdir(dossier):
        # Pas de volume monte : sur une installation mise a jour depuis une
        # version qui n'en avait pas, c'est un compose a completer -- pas une
        # panne, et pas non plus quelque chose qu'on peut ignorer.
        return (Etat.ALERTE, "sauvegardes",
                f"{dossier} n'existe pas -- aucune sauvegarde n'est faite. "
                f"Monte le volume /sauvegardes sur codelab-postgres et "
                f"codelab-app-manager (voir docker-compose.yml).")

    familles = {"bases": "base-", "panneau": "panneau-"}
    maintenant = time.time()
    ages, lignes, manquants = {}, [], []
    for titre, prefixe in familles.items():
        fichiers = [os.path.join(dossier, n) for n in os.listdir(dossier)
                    if n.startswith(prefixe)]
        recent = max((f for f in fichiers), key=_date_de_fichier, default="")
        if not recent:
            manquants.append(titre)
            continue
        age = (maintenant - _date_de_fichier(recent)) / 3600
        ages[titre] = age
        lignes.append(f"{titre} : {len(fichiers)} fichier(s), la plus récente "
                      f"il y a {age:.0f} h")

    if manquants:
        return (Etat.ALERTE, "sauvegardes",
                (" | ".join(lignes) + " | " if lignes else "")
                + f"rien pour : {', '.join(manquants)} -- au premier "
                  f"démarrage, la première sauvegarde arrive tout de suite ; "
                  f"sinon, regarde les journaux du service concerné.")
    # La restauration d'essai de codelab-postgres : une sauvegarde qui ne se
    # restaure pas est une panne, meme si elle est fraiche.
    verification = _lire_verification(dossier)
    if verification:
        lignes.append("restauration d'essai : " + verification)
        if " ECHEC " in f" {verification} ":
            return (Etat.ECHEC, "sauvegardes", " | ".join(lignes))
    vieille = max(ages.values())
    if vieille >= SAUVEGARDE_VIEILLE_H:
        return (Etat.ECHEC, "sauvegardes",
                " | ".join(lignes) + f" -- plus rien depuis "
                f"{vieille:.0f} h : la sauvegarde ne tourne plus.")
    if vieille >= SAUVEGARDE_FRAICHE_H:
        return (Etat.ALERTE, "sauvegardes",
                " | ".join(lignes) + " -- un passage a été manqué.")
    return Etat.OK, "sauvegardes", " | ".join(lignes)


def _lire_verification(dossier):
    """La derniere ligne de verification-restauration.txt, ou ""."""
    try:
        with open(os.path.join(dossier, "verification-restauration.txt"),
                  encoding="utf-8", errors="replace") as f:
            return f.readline().strip()[:300]
    except OSError:
        return ""


def _date_de_fichier(chemin):
    try:
        return os.path.getmtime(chemin)
    except OSError:
        return 0.0


def check_surface_exposee():
    """Ce qui est REELLEMENT joignable, et par qui.

    Constate plutot que de faire confiance a ce qui est declare : la liste
    des applications publiques, la restriction d'administration et les
    comptes sans second facteur viennent de trois tables de la base du
    panneau. Trois sources, trois verites qu'on ne rapproche
    jamais a l'oeil.

    Elle ne dit pas "c'est mal" : une application publique sur une machine
    qui n'est pas publiee ne risque rien. Elle dit ce qui est ouvert, pour
    que le choix soit fait en connaissance.
    """
    if not _etat_panneau_visible():
        return Etat.SANS_OBJET, "surface exposée", _hors_de_portee()
    etat = _etat_panneau()
    apps = _apps_panneau()
    expo = _exposition_panneau()
    comptes = _comptes_panneau()

    publiques = sorted(n for n, a in apps.items()
                       if (a.get("visibility") or "privee") == "publique")
    sans_2fa = sorted(n for n, c in comptes.items()
                      if isinstance(c, dict) and not c.get("totp"))
    admin_local = True    # toujours : ce n'est plus un reglage
    adresse = (expo.get("adresse_publique") or "").strip()

    morceaux = [
        f"administration {'limitee au reseau local' if admin_local else 'joignable de partout'}",
        f"adresse publique {'déclarée : ' + adresse if adresse else 'non déclarée'}",
        f"{len(publiques)} application(s) publique(s)"
        + (f" ({', '.join(publiques[:3])})" if publiques else ""),
        f"{len(sans_2fa)} compte(s) sans second facteur"
        + (f" ({', '.join(sans_2fa[:3])})" if sans_2fa else ""),
    ]
    # Le seul cas franchement mauvais : une machine publiee ET une
    # administration joignable de partout. Le reste est un etat des lieux.
    #
    # Rang 2 quand meme : c'est une CONFIGURATION, choisie, qu'un mail toutes
    # les heures ne changera pas. La sonde la nomme et dit ou la
    # revoir ; decider reste a l'administrateur.
    mauvais = bool(adresse) and not admin_local
    if mauvais:
        return (Etat.ALERTE, "surface exposée", " | ".join(morceaux)
                + " -- administration ouverte sur une machine publiée : "
                "Paramètres > Exposition, « administration réservée au réseau local »")
    return Etat.OK, "surface exposée", " | ".join(morceaux)


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

    RANG 2, jamais plus. L'isolement par namespace est une DEUXIEME barriere :
    la premiere, l'uid propre a chaque application, tient toujours quand le
    noyau refuse la seconde. Deux applications ne peuvent alors ni se relire
    l'environnement ni se tuer ; elles partagent la vue de /workspace, qu'un
    utilisateur unique partage de toute facon avec sa session SSH. Faire
    tomber le diagnostic entier la-dessus -- sur un refus qui vient de l'HOTE
    et qu'aucune commande du conteneur ne corrige -- etait la plus bruyante
    des alertes inactionnables.
    """
    import shutil
    import subprocess
    actif = os.environ.get("APP_MANAGER_ISOLER", "1").lower() not in ("0", "false", "no")
    if not actif:
        # Choix EXPLICITE de l'administrateur, et c'est la variable que la
        # branche ci-dessous lui conseille de poser pour faire taire la
        # sonde. Continuer a la marquer en faute apres qu'il l'a posee
        # reviendrait a ne pas tenir parole.
        return (Etat.OK, "isolation des applications",
                "APP_MANAGER_ISOLER=0 : isolement coupé volontairement -- chaque "
                "application garde son uid, mais voit les fichiers des autres")
    if shutil.which("unshare") is None:
        return (Etat.ALERTE, "isolation des applications",
                "unshare absent de l'image : les applications démarrent, mais "
                "sans être isolées les unes des autres")

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
        # Ou l'on regarde compte : chaque conteneur a son propre profil
        # seccomp/AppArmor. Un refus constate depuis Dagster rend l'isolement
        # DOUTEUX cote panneau, il ne le refute pas -- et le dire evite de
        # partir corriger la mauvaise machine.
        ou = ("" if _etat_panneau_visible() else
              " Constaté depuis ce conteneur-ci, qui n'est pas celui qui lance "
              "les applications : la page de diagnostic du panneau tranche.")
        return (Etat.ALERTE, "isolation des applications",
                "le noyau refuse de créer un namespace utilisateur (%s). %s "
                "Chaque application garde son propre uid -- elles ne peuvent "
                "pas se relire l'environnement ni se tuer -- mais elles "
                "partagent la vue de /workspace.%s Pour assumer le choix et "
                "faire taire cette sonde : APP_MANAGER_ISOLER=0"
                % (refus or "raison inconnue", _verrou_userns(), ou))

    return (Etat.OK, "isolation des applications",
            "chaque application ne voit que son propre projet "
            "(ce diagnostic excepté : il doit voir l'ensemble)")


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
    resultat, une exception aussi.

    Ces tests-la n'ont que deux reponses a donner -- l'action attendue s'est
    produite, ou non -- et ils repondent donc en booleens. Etat.depuis() les
    range au meme format que les sondes, pour que la page n'ait qu'une seule
    facon d'afficher un resultat.
    """
    try:
        etat, titre, detail = fn()
        return Etat.depuis(etat), titre, detail
    except Exception as e:                                        # noqa: BLE001
        return Etat.ECHEC, nom, f"{type(e).__name__}: {e}"


def verif_base_ecrit_et_relit():
    """La chaine complete jusqu'a Postgres : on insere, on relit."""
    conn = connect_pg()
    try:
        numero = write_heartbeat(conn, "verification", "verification approfondie")
        _, recentes = read_heartbeats(conn, limit=20)
        vu = any("verification approfondie" in str(l) for l in recentes)
        if not vu:
            return (False, "base : écriture puis relecture",
                    f"ligne #{numero} insérée, mais absente de la relecture")
        return (True, "base : écriture puis relecture",
                f"ligne #{numero} insérée et relue dans la foulée")
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
        return (True, "écriture refusée sans session",
                f"le panneau répond {code} — la garde de {quoi} tient")
    return (False, "écriture refusée sans session",
            f"le panneau répond {code} : une écriture est passée sans session")


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
                f"{url} répond 200 en {ms} ms")
    if code in (301, 302, 303, 307, 308) and "/login" in (entetes.get("Location") or ""):
        return (True, "le proxy sert cette application",
                f"le proxy résout le projet et applique sa visibilité privée "
                f"(redirection vers /login, {ms} ms)")
    if code == 404:
        return (False, "le proxy sert cette application",
                "le panneau ne connait pas d'application « diagnostic » : "
                "elle a été supprimée du registre, ou renommée")
    if code in (502, 503):
        return (False, "le proxy sert cette application",
                f"le panneau connaît le projet mais l'application ne répond "
                f"pas ({code}) — est-elle démarrée ?")
    return False, "le proxy sert cette application", f"{url} répond {code}"


def verif_le_journal_enregistre():
    """Le journal des acces suit-il vraiment ce qui se passe ?

    On compte, on provoque une ouverture (la requete ci-dessus en est une),
    on recompte. Un journal qui n'ecrit plus est invisible autrement : tout
    continue de marcher, on ne s'apercoit de rien, et le jour ou l'on
    cherche qui s'est connecte il n'y a rien a lire.
    """
    import urllib.request
    journal = "le journal du panneau"
    avant = _taille_du_journal()
    if avant < 0:
        return (False, "le journal des accès enregistré",
                f"{journal} introuvable ou illisible depuis ici")
    port = _port_applications() or _port_panneau()
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/diagnostic/health", timeout=8).read(10)
    except Exception:                                             # noqa: BLE001
        pass
    time.sleep(1.0)
    apres = _taille_du_journal()
    if apres > avant:
        return (True, "le journal des accès enregistré",
                f"une ouverture de plus notée ({apres} événements)")
    # Les ouvertures sont regroupees par quart d'heure et par compte : rien
    # de neuf peut vouloir dire "deja note il y a dix minutes", pas "casse".
    return (True, "le journal des accès enregistré",
            f"{journal} lisible ({avant} événements) — rien de neuf, les "
            f"ouvertures sont regroupées par quart d'heure")


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
        return False, "le dossier du projet est écrivable", "relecture incorrecte"
    return (True, "le dossier du projet est écrivable",
            f"{ici} -- écrit, relu, effacé")


# Prefixe "verif_" et non "test_" : ce fichier contient AUSSI la suite de
# tests du panneau, que pytest collecte. Nommees "test_...", ces cinq
# verifications seraient ramassees par pytest et tourneraient a la
# construction de l'image -- sans panneau, sans base, sans installation.
# Elles echoueraient toutes, pour rien.
def lancer_suite_du_panneau(timeout=180):
    """Lance la suite de regressions du panneau, dans un sous-process.

    Un sous-process et pas pytest.main() : les fixtures de la suite
    detournent les chemins d'etat, et les faire jouer dans le process qui
    sert le diagnostic les appliquerait a l'application vivante. Un process a
    part n'a aucun de ces effets et meurt avec son resultat.

    (La suite vit dans test_regressions.py depuis qu'elle a ete detachee
    d'ici. Avant cela, pytest reimportait ce fichier-meme, en train de
    s'executer -- une raison de plus pour le sous-process.)

    Rien de ce qu'ils ecrivent ne touche l'installation : le filet detourne
    tous les chemins d'etat avant chaque test (voir plus bas). Verifie en
    faisant tourner la suite entiere avec APP_MANAGER_STATE pointant sur de
    vrais fichiers -- aucun n'a bouge, aucun n'est apparu.
    """
    import subprocess
    suite = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "test_regressions.py")
    if not os.path.exists(suite):
        # Image sans la suite : ce n'est pas un echec, il n'y a rien a lancer.
        return (Etat.SANS_OBJET, "suite de régressions du panneau",
                "la suite de tests n'est pas presente dans cette image")
    if app is None:
        # Pas un echec : le panneau n'est simplement pas dans cette image.
        return (Etat.SANS_OBJET, "suite de régressions du panneau",
                "le module du panneau est introuvable depuis ici")
    debut = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", suite,
             "-q", "--no-header", "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(__file__)))
    except FileNotFoundError:
        return (Etat.SANS_OBJET, "suite de régressions du panneau",
                "pytest n'est pas installé dans cette image")
    except subprocess.TimeoutExpired:
        return (Etat.ECHEC, "suite de régressions du panneau",
                f"la suite n'a pas fini en {timeout} s")
    secondes = time.time() - debut
    # La derniere ligne non vide porte le compte : "123 passed, 1 skipped...".
    lignes = [l for l in (r.stdout or "").strip().split("\n") if l.strip()]
    resume = lignes[-1] if lignes else (r.stderr or "").strip()[-200:]
    if r.returncode == 0:
        return (Etat.OK, "suite de régressions du panneau",
                f"{resume} -- en {secondes:.1f} s")
    echecs = [l for l in lignes if l.startswith("FAILED")]
    return (Etat.ECHEC, "suite de régressions du panneau",
            resume + (" | " + " ; ".join(e[6:80] for e in echecs[:4]) if echecs else ""))


# --------------------------------------------------------------------------
# LES THEMATIQUES
#
# Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce qu'on
# cherche. Regroupees par sujet, ce sont cinq questions auxquelles on repond
# separement : les services se parlent-ils, la base garde-t-elle ce qu'on lui
# confie, ce qui doit etre ferme l'est-il, que porte le panneau, et reste-t-il
# de la place.
#
# Le regroupement vit ICI et non dans la page : c'est la meme information que
# les sondes elles-memes, et l'asset Dagster comme la page doivent en avoir la
# meme lecture.
THEMES = [
    ("services", "Services",
     "Les conteneurs de la stack, et le chemin entre eux."),
    ("donnees", "Données",
     "Postgres : le pilote, la connexion, et ce qui y est écrit."),
    ("securite", "Sécurité",
     "Ce qui doit être fermé l'est-il, et depuis où peut-on entrer."),
    ("applications", "Applications",
     "Ce que le panneau porte, et dans quel état."),
    ("ressources", "Ressources",
     "Le disque, les journaux, et la place qui reste."),
]

# Quelle sonde appartient a quelle thematique, par le nom qu'elle se donne.
# Une sonde absente de cette table n'est pas perdue : elle tombe dans
# « Autres » (voir sondes_par_theme) et un test signale l'oubli.
THEME_DES_SONDES = {
    "credentials.env": "services",
    "/workspace": "services",
    "codelab-dagster": "services",
    "codelab-dev (SSH)": "services",
    "codelab-postgres (TCP)": "services",
    "codelab-redis (TCP)": "services",
    "codelab-mailpit (SMTP)": "services",
    "codelab-app-manager": "services",
    "Postgres (pilote)": "donnees",
    "Postgres": "donnees",
    "pgvector": "donnees",
    "panneau fermé": "securite",
    "origine des applications": "securite",
    "exposition": "securite",
    "isolation des applications": "securite",
    "provenance des connexions": "securite",
    "surface exposée": "securite",
    "Clés SSH (droits)": "securite",
    "applications déclarées": "applications",
    "espace disque": "ressources",
    "sauvegardes": "ressources",
}


def theme_d_une_sonde(nom):
    return THEME_DES_SONDES.get(nom, "autres")


def sondes_par_theme(resultats=None):
    """Les sondes rangees par thematique, dans l'ordre de THEMES.

    Rend une liste de (cle, titre, description, [(ok, nom, detail)]). Les
    thematiques sans aucune sonde sont ecartees -- une rubrique vide n'est
    pas une rubrique -- et ce qui n'est range nulle part finit dans
    « Autres » plutot que de disparaitre.
    """
    resultats = run_all() if resultats is None else resultats
    par_cle = {}
    for ok, nom, detail in resultats:
        par_cle.setdefault(theme_d_une_sonde(nom), []).append((ok, nom, detail))
    groupes = [(cle, titre, desc, par_cle.get(cle, []))
               for cle, titre, desc in THEMES if par_cle.get(cle)]
    if par_cle.get("autres"):
        groupes.append(("autres", "Autres",
                        "Sondes qui n'ont pas encore de rubrique.",
                        par_cle["autres"]))
    return groupes


# La liste, et non plus une suite d'appels enfouie dans une fonction : la
# page de verification les annonce AVANT de les lancer, et en joue un a la
# fois pour montrer ou elle en est. Un nom affiche puis un resultat au meme
# indice, c'est ce qui permet de remplir le tableau ligne par ligne.
#
# Les intitules sont lus par quelqu'un : ils portent leurs accents.
TESTS_APPROFONDIS = [
    ("Base : écriture puis relecture", verif_base_ecrit_et_relit),
    ("Écriture refusée sans session", verif_ecriture_refusee_sans_session),
    ("Le proxy sert cette application", verif_proxy_sert_cette_application),
    ("Le journal des accès enregistre", verif_le_journal_enregistre),
    ("Le dossier du projet est écrivable", verif_le_projet_est_ecrivable),
]

# La suite de regressions du panneau vient toujours en dernier : c'est la
# plus longue, et on veut voir les tests d'installation d'abord.
NOM_SUITE_PANNEAU = "Suite de régressions du panneau"

# La thematique de chaque test approfondi, par son intitule. Elle sert a
# lancer la verification SUR UN SUJET : celui qu'on vient de voir en echec,
# sans rejouer les trois minutes des autres.
#
# La suite de regressions n'appartient a aucune des cinq : elle verifie du
# code, pas cette installation. Elle a donc sa rubrique, et c'est aussi ce
# qui la garde optionnelle.
THEME_DES_TESTS = {
    "Base : écriture puis relecture": "donnees",
    "Écriture refusée sans session": "securite",
    "Le proxy sert cette application": "services",
    "Le journal des accès enregistre": "securite",
    "Le dossier du projet est écrivable": "ressources",
    NOM_SUITE_PANNEAU: "code",
}


def indices_par_rubrique(avec_suite=True):
    """Le rang de chaque test approfondi, range par thematique.

    Le nom ne commence pas par « tests » : pytest ramasse tout ce qui
    commence ainsi, et cette fonction-ci se serait retrouvee jouee comme un
    test -- elle rend un dictionnaire, pytest le signale, et le bruit finit
    par cacher un vrai avertissement.

    Le RANG et pas le nom : c'est lui que /api/test/<i> attend, et c'est ce
    qui permet de ne jouer qu'un sujet.
    """
    par_cle = {}
    for indice, nom in enumerate(noms_des_tests(avec_suite)):
        par_cle.setdefault(THEME_DES_TESTS.get(nom, "autres"), []).append(indice)
    return par_cle


def noms_des_tests(avec_suite=True):
    noms = [nom for nom, _ in TESTS_APPROFONDIS]
    return noms + [NOM_SUITE_PANNEAU] if avec_suite else noms


def run_test(indice, avec_suite=True):
    """Un seul test, par son rang dans noms_des_tests()."""
    if indice == len(TESTS_APPROFONDIS) and avec_suite:
        return lancer_suite_du_panneau()
    nom, fn = TESTS_APPROFONDIS[indice]
    etat, _nom_interne, detail = _essai(nom, fn)
    # Le nom affiche est celui de la LISTE, pas celui que la fonction se
    # donne : la page annonce ses lignes avant de les remplir, et une ligne
    # qui change d'intitule en cours de route n'est plus la meme ligne.
    return etat, nom, detail


def run_tests():
    """Les tests a la demande, dans l'ordre ou on veut les lire."""
    return [_essai(nom, fn) for nom, fn in TESTS_APPROFONDIS]


def check_panneau_joignable():
    """Le panneau repond-il ? La question de disponibilite, et elle seule.

    Elle etait posee NULLE PART, et c'est ce qui forcait les deux sondes de
    fermeture a repondre a sa place -- donc a echouer pour une raison qui
    n'etait pas la leur. Les autres services ont chacun la leur (Postgres,
    Dagster, dev) ; le panneau n'y avait pas droit.
    """
    port = _port_panneau()
    hote, info = _joindre_panneau(port)
    if hote is None:
        return (Etat.ECHEC, "codelab-app-manager",
                f"aucune réponse ({info}) -- conteneur arrêté ? "
                f"docker logs --tail 50 codelab-app-manager")
    return Etat.OK, "codelab-app-manager", f"http://{hote}:{port}/health -- HTTP {info}"


# --------------------------- LA HIERARCHIE, EN UN SEUL ENDROIT ---------------
#
# Le pire rang que chaque sonde a le DROIT d'atteindre. Se lit de haut en bas
# comme une liste de priorites : ce qui est a ECHEC reveille quelqu'un, ce qui
# est a ALERTE attend demain matin.
#
# C'est une declaration ET un filet. Une sonde qui voudrait passer en echec
# alors qu'elle est declaree de rang 2 est ramenee a l'alerte par
# _hierarchiser() : la hierarchie ne depend donc pas de la vigilance de celui
# qui ecrira la prochaine sonde, et elle se relit ici sans parcourir mille
# lignes de code.
SEVERITE_MAX = {
    # ------------------------------------------------------------- rang 1 --
    # Sans ca, la stack ne rend plus son service. Le run echoue, le capteur
    # envoie le mail.
    "credentials.env": Etat.ECHEC,            # pas de secret : rien ne se connecte
    "/workspace": Etat.ECHEC,                 # Dagster n'a plus de code a charger
    "Postgres (pilote)": Etat.ECHEC,
    "Postgres": Etat.ECHEC,
    "codelab-postgres (TCP)": Etat.ECHEC,
    "codelab-redis (TCP)": Etat.ECHEC,       # les applications qui s'en servent tombent
    "codelab-mailpit (SMTP)": Etat.ALERTE,   # des mails qui ne partent plus, pas une panne
    "codelab-dagster": Etat.ECHEC,            # un conteneur tombe est une panne,
    "codelab-dev (SSH)": Etat.ECHEC,          # pas un reglage a revoir
    "codelab-app-manager": Etat.ECHEC,
    # Rang 1 pour une autre raison : une breche CONSTATEE. Ces deux sondes
    # n'y montent que sur une preuve -- une route d'administration qui repond
    # sans session, le panneau qui repond sur l'origine des applications. Un
    # panneau injoignable, lui, ne prouve rien et reste une alerte.
    "panneau fermé": Etat.ECHEC,
    "origine des applications": Etat.ECHEC,
    # Et la panne qui ne previent pas : un disque plein arrete Postgres avec
    # des messages qui ne parlent jamais d'espace disque.
    "espace disque": Etat.ECHEC,
    # Rang 1 aussi, et c'est assume : une sauvegarde qui a cesse de tourner ne
    # se voit jamais au moment ou elle cesse. Elle se voit le jour ou l'on en
    # a besoin, et ce jour-la il n'y a plus rien a faire.
    "sauvegardes": Etat.ECHEC,
    # ------------------------------------------------------------- rang 2 --
    # Degrade, mais ca tourne. A regarder dans la journee, sans mail de nuit.
    "Clés SSH (droits)": Etat.ALERTE,         # aucune cle = installation neuve
    "isolation des applications": Etat.ALERTE,  # 2e barriere ; l'uid tient
    "exposition": Etat.ALERTE,                # reglages a poser
    "provenance des connexions": Etat.ALERTE,  # a expliquer, pas une intrusion
    "applications déclarées": Etat.ALERTE,    # un projet tombe n'est pas la stack
    "surface exposée": Etat.ALERTE,           # un choix, pas une panne
    # Une CAPACITE absente, pas une panne : une installation qui ne fait
    # aucune recherche vectorielle tourne parfaitement sans l'extension. La
    # mettre au rang 1 enverrait un mail de nuit pour un type que personne
    # n'utilise.
    "pgvector": Etat.ALERTE,
}


def _hierarchiser(resultat):
    """Ramene un resultat au rang maximal declare pour sa sonde."""
    etat, nom, detail = resultat
    # Rappel de l'ordre : ECHEC(0) < ALERTE(1) < SANS_OBJET(2) < OK(3). Le
    # plafond est donc un PLANCHER de valeur, d'ou le max().
    return max(Etat.depuis(etat), SEVERITE_MAX.get(nom, Etat.ECHEC)), nom, detail


def _executer_sonde(nom, fonction):
    """Une exception de sonde devient un resultat lisible, jamais un HTTP 500."""
    try:
        return fonction()
    except Exception as error:
        return (Etat.ECHEC, nom, f"exception inattendue ({type(error).__name__}: {error})")


def run_all(env_file=None, workspace=None, ssh_dir=None):
    """Toutes les sondes, dans l'ordre ou on veut les lire : d'abord ce qui
    doit MARCHER, ensuite ce qui doit etre FERME, enfin l'etat des lieux."""
    host = read_env("POSTGRES_HOST", env_file) or "codelab-postgres"
    port = int(read_env("POSTGRES_PORT", env_file) or 5432)
    resultats = [        # --- ce qui doit marcher -----------------------------------------
        _executer_sonde("check_config", lambda: check_config(env_file)),
        _executer_sonde("check_workspace", lambda: check_workspace(workspace)),
        _executer_sonde("check_pilote_pg", lambda: check_pilote_pg()),
        _executer_sonde("check_postgres", lambda: check_postgres(env_file)),
        _executer_sonde("pgvector", lambda: check_pgvector(env_file)),
        _executer_sonde("check_tcp", lambda: check_tcp("codelab-postgres (TCP)", host, port)),
        _executer_sonde("check_tcp", lambda: check_tcp("codelab-redis (TCP)", "codelab-redis", 6379)),
        _executer_sonde("check_tcp", lambda: check_tcp("codelab-mailpit (SMTP)", "codelab-mailpit", 1025,
                                                        lire_banniere=True, gravite=Etat.ALERTE)),
        _executer_sonde("check_http", lambda: check_http("codelab-dagster", "http://codelab-dagster:3000/")),
        _executer_sonde("check_tcp", lambda: check_tcp("codelab-dev (SSH)", "codelab-dev", 22, lire_banniere=True)),
        _executer_sonde("check_panneau_joignable", lambda: check_panneau_joignable()),
        _executer_sonde("check_cles_ssh", lambda: check_cles_ssh(ssh_dir)),
        # --- ce qui doit etre ferme ---------------------------------------
        # L'etat des lieux ne s'arrete pas a "ca marche" : il dit aussi si
        # c'est correctement ferme.
        _executer_sonde("check_panneau_ferme", lambda: check_panneau_ferme()),
        _executer_sonde("check_origine_applications", lambda: check_origine_applications()),
        _executer_sonde("check_exposition", lambda: check_exposition()),
        _executer_sonde("check_isolation", lambda: check_isolation()),
        _executer_sonde("check_provenance", lambda: check_provenance()),
        # --- ce que la stack porte et ce qu'elle use -----------------------
        # Au-dela de "la stack repond" : ce qu'elle porte, ce qu'elle use, et
        # ce qu'elle laisse ouvert. Les trois sont en LECTURE SEULE, donc a
        # leur place ici et non dans la verification approfondie.
        _executer_sonde("check_applications", lambda: check_applications()),
        _executer_sonde("check_espace_disque", lambda: check_espace_disque()),
        _executer_sonde("check_sauvegardes", lambda: check_sauvegardes()),
        _executer_sonde("check_surface_exposee", lambda: check_surface_exposee()),
    ]
    return [_hierarchiser(r) for r in resultats]


# --------------------------------------------------------------------------
# Audit des dependances des projets
#
# Trivy, en CI, regarde le depot de la stack -- pas les projets de
# /workspace. Une application qui embarque une version vulnerable de Flask
# ou d'express ne se voyait nulle part. Cet audit lit ce que chaque projet
# fige (versions exactes) et le confronte a la base publique OSV
# (osv.dev, qui agrege les avis PyPI, npm, GitHub...).
#
# Seulement la bibliotheque standard : il tourne dans codelab-dagster, qui
# n'a ni pip-audit ni npm. Et seulement des versions EXACTES : « flask>=2 »
# ne dit pas ce qui est installe, l'auditer reviendrait a deviner.
#
# Ce qui est lu, par projet (dossiers de /workspace hors « . » et « _ ») :
#   - requirements*.txt     les lignes « nom==version » ;
#   - vendor/               les paquets installes par « pip install --target » ;
#   - .dagster-venv/        ceux de l'environnement Dagster isole du projet ;
#   - package-lock.json     les versions resolues par npm.
# --------------------------------------------------------------------------

OSV_URL = "https://api.osv.dev/v1/querybatch"
OSV_LOT = 500           # l'API accepte 1000 requetes par appel ; marge.

_EXIGENCE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def _normaliser_pypi(nom):
    return re.sub(r"[-_.]+", "-", nom).lower()


def _paquets_installes(dossier):
    """Les paquets d'un site-packages (ou d'un vendor/), lus dans *.dist-info."""
    paquets = set()
    try:
        entrees = os.listdir(dossier)
    except OSError:
        return paquets
    for entree in entrees:
        if not entree.endswith(".dist-info"):
            continue
        nom = version = None
        try:
            with open(os.path.join(dossier, entree, "METADATA"), encoding="utf-8",
                      errors="replace") as f:
                for ligne in f:
                    if not ligne.strip():
                        break           # fin de l'en-tete
                    if ligne.startswith("Name:"):
                        nom = ligne[5:].strip()
                    elif ligne.startswith("Version:"):
                        version = ligne[8:].strip()
        except OSError:
            continue
        if nom and version:
            paquets.add(("PyPI", _normaliser_pypi(nom), version))
    return paquets


def _paquets_npm(lock):
    """Les versions resolues d'un package-lock.json (formats 1, 2 et 3)."""
    paquets = set()
    for chemin, info in (lock.get("packages") or {}).items():
        if not chemin or not isinstance(info, dict) or info.get("link"):
            continue                    # "" : le projet lui-meme
        nom = info.get("name") or chemin.rsplit("node_modules/", 1)[-1]
        if info.get("version"):
            paquets.add(("npm", nom, info["version"]))
    if not paquets:
        # Format 1 : un arbre "dependencies" imbrique.
        pile = [lock.get("dependencies") or {}]
        while pile:
            for nom, info in pile.pop().items():
                if isinstance(info, dict):
                    if info.get("version"):
                        paquets.add(("npm", nom, info["version"]))
                    pile.append(info.get("dependencies") or {})
    return paquets


def dependances_du_projet(dossier):
    """Ensemble de (ecosysteme, nom, version) fige par un projet."""
    paquets = set()
    try:
        entrees = sorted(os.listdir(dossier))
    except OSError:
        return paquets
    for entree in entrees:
        if entree.startswith("requirements") and entree.endswith(".txt"):
            try:
                with open(os.path.join(dossier, entree), encoding="utf-8",
                          errors="replace") as f:
                    for ligne in f:
                        m = _EXIGENCE.match(ligne)
                        if m:
                            paquets.add(("PyPI", _normaliser_pypi(m.group(1)), m.group(2)))
            except OSError:
                pass
    paquets |= _paquets_installes(os.path.join(dossier, "vendor"))
    for site in sorted(Path(dossier, ".dagster-venv", "lib").glob("python*/site-packages")):
        paquets |= _paquets_installes(str(site))
    try:
        with open(os.path.join(dossier, "package-lock.json"), encoding="utf-8") as f:
            paquets |= _paquets_npm(json.load(f))
    except (OSError, ValueError):
        pass
    return paquets


def dependances_du_workspace(workspace=None):
    """{projet: {(ecosysteme, nom, version), ...}} pour chaque projet."""
    workspace = workspace or WORKSPACE
    projets = {}
    try:
        noms = sorted(os.listdir(workspace))
    except OSError:
        return projets
    for nom in noms:
        chemin = os.path.join(workspace, nom)
        if nom.startswith((".", "_")) or not os.path.isdir(chemin):
            continue
        paquets = dependances_du_projet(chemin)
        if paquets:
            projets[nom] = paquets
    return projets


def interroger_osv(paquets, timeout=30, url=OSV_URL):
    """{(ecosysteme, nom, version): [identifiants d'avis]} pour les paquets
    vulnerables. Leve OSError (ou ValueError) si OSV ne repond pas."""
    paquets = sorted(paquets)
    vulnerables = {}
    for debut in range(0, len(paquets), OSV_LOT):
        lot = paquets[debut:debut + OSV_LOT]
        corps = json.dumps({"queries": [
            {"package": {"ecosystem": eco, "name": nom}, "version": version}
            for eco, nom, version in lot]}).encode()
        requete = urllib.request.Request(url, data=corps, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(requete, timeout=timeout) as r:
            resultats = json.load(r).get("results") or []
        for paquet, resultat in zip(lot, resultats):
            ids = sorted(v.get("id") for v in (resultat or {}).get("vulns") or [] if v.get("id"))
            if ids:
                vulnerables[paquet] = ids
    return vulnerables


def audit_dependances(workspace=None, interroger=None):
    """Confronte les dependances de chaque projet a OSV.

    Renvoie (nombre de paquets audites, {projet: [(eco, nom, version, [ids])]}).
    """
    projets = dependances_du_workspace(workspace)
    tous = set().union(*projets.values()) if projets else set()
    vulnerables = (interroger or interroger_osv)(tous) if tous else {}
    rapport = {}
    for projet, paquets in projets.items():
        touches = [(eco, nom, version, vulnerables[(eco, nom, version)])
                   for eco, nom, version in sorted(paquets) if (eco, nom, version) in vulnerables]
        if touches:
            rapport[projet] = touches
    return len(tous), rapport


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

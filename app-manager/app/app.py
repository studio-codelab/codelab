"""
CodeLab -- LE PANNEAU : authentification, hub, applications, proxy, mails.
---------------------------------------------------------------------------
ROLE        Un seul service Flask, servi par waitress (voir « serveur HTTP ») :
              :9001  le panneau, son API, /login, /health (sonde Docker) ;
              :9002  les applications, sous /<nom>/, sur une ORIGINE A PART
                     (APP_MANAGER_APPS_PORT ; 0 les remet sur 9001).
            Il authentifie (mot de passe, second facteur, cles d'acces),
            declare, construit, lance et surveille les applications de
            /workspace, les sert par reverse proxy, envoie mails et
            notifications, et sauvegarde son etat.
            Il n'ECRIT JAMAIS dans /workspace : un projet est cree par
            l'utilisateur (SSH, VS Code, git), puis declare ici.
CHARGE PAR  le conteneur codelab-app-manager (entrypoint.sh) ; importe par la
            suite de tests (workspace/diagnostic/test_regressions.py), qui
            appelle les fonctions par leur nom : un renommage se reporte dans
            les tests.
CONTRAINTES Les regles numerotees de SECURITE.md, et la carte des dependances
            d'AGENTS.md (racine du depot). En particulier : le controle des
            droits est ici, jamais seulement dans la page ; une route qui
            modifie passe par verifier_jeton() ; une application ne recoit
            jamais le cookie ni les secrets du panneau.
            Chemins par variables d'environnement, alignes sur
            docker-compose.yml : APP_MANAGER_STATE (etat), APP_MANAGER_ROOT
            (/workspace), APP_MANAGER_SHARED_CONFIG (credentials.env, 0600).
STRUCTURE   Sections titrees « # ---- titre ---- », dans cet ordre :
            pages servies, secrets, SSO des applications, double
            authentification, origine des applications, Dagster, connexion
            et appareils, en-tetes et CSP, jeton CSRF, comptes et coffre,
            invitations, cles d'acces, messages, journal et miroir Postgres,
            persistance, privileges et isolation, cycle de vie et
            surveillance, sauvegardes, alertes et envoi, metriques, icones,
            categories, routes (auth, API, pages), notifications Web Push,
            taches et suivi, composition du hub, proxy, serveur HTTP, main.
CHOIX       - Le cadre /ouvrir.html est un fichier a marqueurs, rempli en un
              seul passage (ouvrir_dans_le_panneau) : sa page et son script,
              ecrits en chaines Python, ne se relisaient plus.
            - Un seul fichier : les tests et les sondes du diagnostic
              importent les fonctions de app ; un decoupage doit les
              reexporter (voir A AMELIORER).
            - Les processus des applications survivent a un redemarrage du
              panneau (section « les processus survivent au panneau »).
            - Les applications sur une origine distincte du panneau : un
              script d'application ne lit pas le cookie de session.
            Les pages et scripts servis vivent a cote, lus une fois a
            l'import (_lire_ressource) : dashboard.html, login.html,
            messages.html, ouvrir.html (a marqueurs), mode.js, theme.css,
            environnement-app.js (injecte par le proxy).
A AMELIORER - Fonctions encore longues (60 a 75 lignes de code), a decouper
              avec la meme prudence que login_submit : _proxy,
              _verifier_cle, api_message, _pg_preparer.
            - Decouper en modules (auth, proxy, notifications, cycle de vie)
              avec un app.py qui reexporte les noms : relecture et tests plus
              cibles, sans changer l'API vue par les tests.
            - Generer la table de STRUCTURE ci-dessus depuis les titres de
              section, pour qu'elle ne vieillisse plus.
"""
import base64
import copy
import datetime
import fcntl
import html as _html
import io
import functools
import gzip
import queue
import hashlib
import hmac
import ipaddress
import json
import os
import re
import resource
import secrets
import shutil
import signal
import smtplib
import tarfile
import socket
import ssl
import stat as stat_mod
import struct
import subprocess
import threading
import time
import unicodedata
import zlib
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from email.message import EmailMessage

import psutil
from flask import (Flask, Response, g, has_request_context, jsonify, redirect,
                   request, session, send_file, stream_with_context)

# Le module de stockage vit a cote de ce fichier, et se charge PAR SON
# CHEMIN. app.py est parfois charge lui-meme par son chemin (le diagnostic,
# les tests) ; ajouter son dossier au chemin d'import ferait alors passer ce
# fichier-ci pour le « app » d'un autre projet -- celui du diagnostic.
import importlib.util as _importlib_util
import sys
_spec_stockage = _importlib_util.spec_from_file_location(
    "codelab_stockage", os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockage.py"))
stockage = sys.modules.get("codelab_stockage") or _importlib_util.module_from_spec(_spec_stockage)
if "codelab_stockage" not in sys.modules:
    sys.modules["codelab_stockage"] = stockage
    _spec_stockage.loader.exec_module(stockage)

STATE_DIR = os.environ.get("APP_MANAGER_STATE", "/var/lib/codelab/app-manager")
# LA BASE DU PANNEAU. Applications, comptes, droits, cles d'acces, journal,
# messages et reglages y vivent, chacun dans sa table -- voir stockage.py et
# la section « Donnees » du README. Les anciens fichiers JSON ci-dessous ne
# servent plus qu'a l'import automatique, une fois, au premier demarrage
# apres la mise a jour (voir importer_anciens_fichiers).
DB_FILE = os.path.join(STATE_DIR, "codelab.db")
APPS_FILE = os.path.join(STATE_DIR, "apps.json")
LOG_DIR = os.path.join(STATE_DIR, "logs")
ROOT = os.environ.get("APP_MANAGER_ROOT", "/workspace")
PORT_MIN, PORT_MAX = 9101, 9140
HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade",
       "proxy-authenticate", "proxy-authorization", "te", "trailers"}

SHARED_CONFIG_DIR = os.environ.get("APP_MANAGER_SHARED_CONFIG", "/var/lib/codelab/config")
# Source de verite unique pour tous les secrets CodeLab. Pas de nom en "."
# (fichier cache sous Unix) : les navigateurs de fichiers web n'offrent pas
# tous une option pour les afficher.
SHARED_ENV_FILE = os.path.join(SHARED_CONFIG_DIR, "credentials.env")

# Anciens fichiers dedies, un par secret. Conserves uniquement pour la
# migration (leur valeur est reprise puis le fichier est supprime) et comme
# repli si credentials.env n'est pas accessible en ecriture.
LEGACY_ADMIN_PASSWORD_FILE = os.path.join(STATE_DIR, "admin_password")
LEGACY_SECRET_KEY_FILE = os.path.join(STATE_DIR, "flask_secret_key")
SSO_PRIVATE_FILE = os.path.join(STATE_DIR, "codelab_sso_private.key")

_admin_password = None   # valeur courante, chargee par bootstrap_secrets()
_totp_secret = ""        # vide = double authentification desactivee
TOTP_COMPTE = "admin"    # le nom affiche dans l'application d'authentification

# ------------------------------ pages servies ------------------------------
#
# Les deux pages HTML vivent dans leur propre fichier, a cote de ce module.
# Elles etaient auparavant des chaines Python d'une seule ligne -- 67 Ko pour
# le tableau de bord : illisibles en revue, impossibles a modifier sans
# re-echapper le tout, et signalees par git comme une seule ligne changee a
# chaque retouche de l'interface. Dans un .html elles se relisent, se diffent
# et beneficient de la coloration syntaxique.
#
# Lues une fois a l'import : elles viennent de l'image et ne changent jamais
# en cours d'execution, les relire a chaque requete couterait un acces disque
# pour rien.
def _lire_ressource(nom):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), nom),
              encoding="utf-8") as f:
        return f.read()


DASHBOARD_PAGE = _lire_ressource("dashboard.html")
MESSAGERIE_PAGE = _lire_ressource("messages.html")
LOGIN_PAGE = _lire_ressource("login.html")
# Le cadre d'une application ouverte depuis le hub installe (ouvrir_dans_le_panneau).
OUVRIR_PAGE = _lire_ressource("ouvrir.html")
# Le theme : un seul fichier de variables, partage par les deux pages. Servi
# plutot que recopie dans chacune, pour qu'il n'existe qu'un endroit a changer
# et qu'aucune des deux ne puisse deriver de l'autre.
THEME_CSS = _lire_ressource("theme.css")
MODE_JS = _lire_ressource("mode.js")
# Le script du panneau (dossier panneau/), sorti de dashboard.html pour se
# mettre en cache. Son
# empreinte entre dans l'adresse (/panneau.js?v=...) : un deploiement change
# l'adresse, donc le navigateur ne garde jamais une version perimee.
# Les modules du script, DANS L'ORDRE : assembles en un seul script (une
# seule portee, une seule requete, un seul cache), comme lorsqu'il tenait en
# un fichier. Un module nouveau s'ajoute ici, a sa place.
PANNEAU_MODULES = ("00-socle.js", "01-navigation.js", "02-hub.js", "03-widgets.js",
                   "04-apercu.js", "05-fiche.js", "06-acces.js", "07-comptes.js",
                   "08-reglages.js")
PANNEAU_JS = "\n".join(_lire_ressource(os.path.join("panneau", nom)) for nom in PANNEAU_MODULES)
PANNEAU_JS_VERSION = hashlib.sha256(PANNEAU_JS.encode("utf-8")).hexdigest()[:12]
# Le logo CodeLab : celui de la barre du haut, relu dans la page plutot que
# recopie ici -- un seul dessin, qu'on ne peut pas changer a un endroit et
# oublier a l'autre. C'est aussi l'icone des onglets, servie a une VRAIE
# adresse (/favicon.png, /favicon.ico) : voir favicon_panneau().
ICONE_CODELAB_PNG = base64.b64decode(re.search(
    r'class="topbar-brand"[^>]*>\s*<img src="data:image/png;base64,([A-Za-z0-9+/=]+)"',
    DASHBOARD_PAGE).group(1))

# L'icone de l'ecran d'accueil (iPhone, Android) : le meme logo, pose sur un
# fond blanc OPAQUE de 180 px. iOS remplit de noir la transparence d'une
# apple-touch-icon : le logo du favicon, transparent, y devenait un flacon
# bleu sur un carre noir. Voir ecran_accueil_icone().
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "icone-ecran-accueil.png"), "rb") as _f:
    ICONE_ECRAN_ACCUEIL_PNG = _f.read()

# Les seuls fichiers que /polices/<nom> accepte de servir.
POLICES_SERVIES = {"manrope-latin.woff2", "jakarta-latin.woff2"}


flask_app = Flask(__name__)

# Le panneau s'authentifie par cookie de session, et toutes ses actions
# (demarrer, arreter, builder, deployer) sont des POST sans corps. Sans
# SameSite, n'importe quelle page visitee dans le meme navigateur pouvait donc
# poster un formulaire vers /api/toggle/<app> et piloter la stack a l'insu de
# l'utilisateur -- une CSRF classique, et ici avec execution de la commande de
# build a la cle. "Lax" n'envoie plus le cookie que sur une navigation de
# premier niveau en GET : les GET de l'API sont en lecture seule, les
# ecritures deviennent inatteignables depuis un autre site.
flask_app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # "Secure" interdit au navigateur d'envoyer le cookie sur une connexion en
    # clair. Indispensable des qu'un reverse proxy termine du TLS devant --
    # mais actif seulement sur demande : le poser alors que le panneau est
    # servi en http empecherait purement et simplement de se connecter.
    #
    # Regle au demarrage plutot que depuis l'interface, et c'est delibere :
    # l'activer depuis une page servie en clair deconnecterait sur-le-champ la
    # session qui vient de l'activer, sans moyen de revenir en arriere.
    # Valeur de depart seulement : appliquer_cookie_securise() la reprend
    # des que l'etat d'exposition est lisible, et apres chaque changement.
    SESSION_COOKIE_SECURE=os.environ.get("APP_MANAGER_HTTPS", "").lower()
                          in ("1", "true", "yes"),
    # DEUX LIMITES, ET IL FAUT LES DEUX.
    #
    # Celle-ci est la limite d'INACTIVITE : Flask repousse la date du cookie a
    # chaque requete, donc une session en usage ne tombe jamais, et une
    # session oubliee -- un poste public, un portable perdu -- se ferme d'elle
    # meme au bout de trois jours.
    #
    # L'inactivite seule ne suffit pas : une session entretenue par un onglet
    # laisse ouvert resterait valable indefiniment. La limite ABSOLUE ci-apres
    # la coupe au bout de trente jours, quoi qu'il arrive.
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=3),
)

SESSION_INACTIVITE = flask_app.config["PERMANENT_SESSION_LIFETIME"]
SESSION_ABSOLUE = datetime.timedelta(days=30)
# L'administrateur ouvre tout, Dagster et Mailpit compris : sa session tombe
# au bout de douze heures, quoi qu'il arrive. Une journee de travail, pas un
# mois de cookie a voler.
SESSION_ADMIN_ABSOLUE = datetime.timedelta(hours=12)
procs = {}          # nom -> subprocess.Popen
lock = threading.Lock()
_proc_cache = {}     # pid -> psutil.Process (prime pour cpu_percent delta)
_login_attempts = {}  # ip -> [timestamps des echecs recents]
_restart_history = {}  # nom -> [timestamps des redemarrages auto recents]
_metrics_history = {}  # nom -> deque[(timestamp, cpu_percent, memory_mb)]

MAX_LOG_BYTES = 2 * 1024 * 1024  # 2 Mo -- rotation simple, un seul fichier .1 conserve
METRICS_HISTORY_LEN = 30         # ~2.5 min a 5s/poll, ~30 points suffisent pour une tendance
RESTART_WINDOW = 600             # 10 min
RESTART_MAX_ATTEMPTS = 5         # au-dela, on arrete d'essayer (evite une boucle de crash infinie)


def rotate_log_if_needed(name):
    """Renomme le journal en .1 (ecrasant l'ancien .1 s'il existe) s'il
    depasse MAX_LOG_BYTES. Appele au demarrage d'une app, avant de
    rouvrir le fichier en ecriture -- pas de logique de purge en tache
    de fond, juste un controle a chaque (re)demarrage."""
    path = os.path.join(LOG_DIR, name + ".log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


# --------------------------- bootstrap secrets ---------------------------

def read_shared_value(key):
    """Lit une cle dans credentials.env. Renvoie None si le fichier n'existe
    pas, n'est pas lisible, ou ne contient pas la cle."""
    # Derniere occurrence : chaque bloc est reecrit en fin de fichier, donc
    # une valeur laissee plus haut (edition a la main, ancien format) est
    # forcement la perimee.
    valeur = None
    try:
        with open(SHARED_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    valeur = line[len(key) + 1:].strip() or None
    except OSError:
        pass
    return valeur


def read_legacy_file(path):
    """Lit un ancien fichier mono-secret. Renvoie None s'il est absent ou vide."""
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def upsert_shared_block(name, comment_lines, pairs):
    """Ecrit/met a jour un BLOC entier (commentaires + cles) dans
    credentials.env, partage avec codelab-postgres
    (/DATA/AppData/codelab/config/credentials.env sur le disque de l'hote --
    pas de "." en tete de nom, un navigateur de fichiers web ne propose pas
    toujours d'afficher les fichiers caches). Le bloc est delimite par des
    marqueurs "# ===== <name> =====" / "# ===== /<name> =====" et remplace
    entierement a chaque appel -- pas juste les lignes CLE=valeur, sinon
    les commentaires documentant ce bloc s'accumuleraient en double a
    chaque redemarrage. Les blocs des autres services (ex. codelab-postgres)
    restent intacts quel que soit l'ordre de demarrage : meme logique que
    cote codelab-postgres dans docker-compose.yml."""
    try:
        os.makedirs(SHARED_CONFIG_DIR, exist_ok=True)
        # Le verrou partage avec codelab-postgres, codelab-redis et le proxy
        # Dagster (flock sur le meme fichier, meme noyau). Sans lui, deux
        # services qui demarrent ensemble lisent le meme contenu, et le
        # dernier a ecrire efface le bloc que l'autre venait d'ajouter.
        verrou = os.open(SHARED_ENV_FILE + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as e:
        print(f"[app-manager] credentials.env non ecrit ({e}), repli sur "
              f"les fichiers de {STATE_DIR}.", flush=True)
        return False
    try:
        fcntl.flock(verrou, fcntl.LOCK_EX)
        return _upsert_shared_block_verrouille(name, comment_lines, pairs)
    finally:
        os.close(verrou)


def _upsert_shared_block_verrouille(name, comment_lines, pairs):
    try:
        start, end = f"# ===== {name} =====", f"# ===== /{name} ====="
        existing = []
        if os.path.exists(SHARED_ENV_FILE):
            with open(SHARED_ENV_FILE) as f:
                existing = f.read().splitlines()
        kept, skip = [], False
        for line in existing:
            if line == start:
                skip = True
                continue
            if line == end:
                skip = False
                continue
            if not skip:
                kept.append(line)
        block = [start] + list(comment_lines) + [f"{k}={v}" for k, v in pairs.items()] + [end]
        with open(SHARED_ENV_FILE, "w") as f:
            f.write("\n".join(kept + block) + "\n")
        os.chmod(SHARED_ENV_FILE, 0o600)
        return True
    except OSError as e:
        # Le volume partage n'est peut-etre pas monte (ex. test local sans
        # docker-compose) -- ne bloque jamais le demarrage du service pour
        # ca. L'appelant bascule alors sur les fichiers dedies de STATE_DIR.
        print(f"[app-manager] credentials.env non ecrit ({e}), repli sur "
              f"les fichiers de {STATE_DIR}.", flush=True)
        return False


def ecrire_bloc_panneau(pw, key, totp):
    """Reecrit le bloc du panneau dans credentials.env.

    Un seul endroit ecrit ce bloc : ajouter une cle sans passer par ici la
    ferait disparaitre au redemarrage suivant, quand bootstrap_secrets()
    reecrirait le bloc sans elle.
    """
    commentaires = [
        "# Panneau web de gestion des applications deployees (http://<IP>:9001/).",
        "# APP_MANAGER_ADMIN_LOGIN / _PASSWORD : l'identifiant et le mot de",
        "#   passe du compte d'administration. Dagster et Mailpit s'ouvrent",
        "#   avec la meme session : ils n'ont pas d'identifiant a eux.",
        "# APP_MANAGER_SESSION_SECRET : cle de signature des sessions -- la",
        "#   changer deconnecte tout le monde ; ne jamais la partager.",
        "# APP_MANAGER_CHIFFREMENT_CLE : chiffre les reglages saisis dans le",
        "#   panneau (e-mails, serveur d'envoi, surveillance), en base comme",
        "#   dans la copie locale. La perdre rend ces reglages illisibles :",
        "#   la sauvegarder avec ce fichier, ne jamais la regenerer.",
    ]
    valeurs = {
        "APP_MANAGER_URL": "http://<IP-du-serveur>:9001",
        "APP_MANAGER_ADMIN_LOGIN": NOM_ADMIN,
        "APP_MANAGER_ADMIN_PASSWORD": pw,
        "APP_MANAGER_SESSION_SECRET": key,
    }
    if _cle_chiffrement:
        valeurs["APP_MANAGER_CHIFFREMENT_CLE"] = _cle_chiffrement
    if totp:
        commentaires.append(
            "# APP_MANAGER_TOTP_SECRET : double authentification activee. Vider")
        commentaires.append(
            "#   cette valeur et redemarrer suffit a la desactiver si l'appareil")
        commentaires.append(
            "#   qui porte les codes a ete perdu.")
        valeurs["APP_MANAGER_TOTP_SECRET"] = totp
    return upsert_shared_block("codelab-app-manager", commentaires, valeurs)


def bootstrap_secrets():
    """Charge les secrets du panneau. credentials.env fait autorite ; a
    defaut on reprend les anciens fichiers dedies (migration) ; a defaut on
    genere. Ne leve jamais : un secret illisible est regenere plutot que
    d'empecher le service de demarrer."""
    global _admin_password
    os.makedirs(STATE_DIR, exist_ok=True)

    pw = read_shared_value("APP_MANAGER_ADMIN_PASSWORD")
    key = read_shared_value("APP_MANAGER_SESSION_SECRET")

    if not pw:
        pw = read_legacy_file(LEGACY_ADMIN_PASSWORD_FILE)
        if pw:
            print("[app-manager] mot de passe admin repris de "
                  f"{LEGACY_ADMIN_PASSWORD_FILE} (migration).", flush=True)
        else:
            pw = secrets.token_urlsafe(18)
            print("[app-manager] mot de passe admin genere.", flush=True)

    if not key:
        key = read_legacy_file(LEGACY_SECRET_KEY_FILE) or secrets.token_hex(32)

    _admin_password = pw
    flask_app.secret_key = key

    # La cle du coffre. Lue, jamais regeneree par-dessus une valeur presente :
    # une nouvelle cle rendrait illisible tout ce qui a ete chiffre avec
    # l'ancienne.
    global _cle_chiffrement
    _cle_chiffrement = read_shared_value("APP_MANAGER_CHIFFREMENT_CLE") or ""
    if not _cle_chiffrement:
        try:
            from cryptography.fernet import Fernet
            _cle_chiffrement = Fernet.generate_key().decode()
            print("[app-manager] cle de chiffrement des reglages generee.", flush=True)
        except ImportError:
            print("[app-manager] cryptography absent : reglages non chiffrables.", flush=True)
    _coffre["donnees"] = None

    # Le secret de double authentification, s'il a ete active un jour. Absent =
    # desactivee, et la connexion se fait au seul mot de passe.
    global _totp_secret
    _totp_secret = read_shared_value("APP_MANAGER_TOTP_SECRET") or ""

    written = ecrire_bloc_panneau(pw, key, _totp_secret)

    if written:
        # Les valeurs sont desormais dans credentials.env : les anciens
        # fichiers mono-secret n'ont plus de raison d'exister. Supprimes
        # seulement apres une ecriture reussie, jamais avant.
        for path in (LEGACY_ADMIN_PASSWORD_FILE, LEGACY_SECRET_KEY_FILE):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    print(f"[app-manager] {path} supprime (valeur migree vers "
                          f"{SHARED_ENV_FILE}).", flush=True)
            except OSError:
                pass
    else:
        # credentials.env inaccessible : on retombe sur les fichiers dedies,
        # sinon un redemarrage regenererait un mot de passe different.
        for path, value in ((LEGACY_ADMIN_PASSWORD_FILE, pw),
                            (LEGACY_SECRET_KEY_FILE, key)):
            try:
                with open(path, "w") as f:
                    f.write(value)
                os.chmod(path, 0o600)
            except OSError:
                pass


# --------------------------- SSO des applications ---------------------------
#
# Une application ne reçoit jamais le cookie Flask ni APP_MANAGER_SESSION_SECRET.
# Le proxy lui transmet une assertion courte, signee avec une cle Ed25519 privee
# qui reste dans le control-plane. La cle publique est injectee dans le
# processus : l'application peut donc verifier elle-meme l'identite sans
# recevoir un secret qui permettrait de forger une session CodeLab.
SSO_ISSUER = "codelab"
SSO_TOKEN_TTL = 60
_sso_private_key = None
_sso_public_key = ""


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64json(data):
    return _b64url(json.dumps(data, separators=(",", ":"), sort_keys=True).encode())


def _charger_cle_sso(raw):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ImportError, ValueError, TypeError):
        return None


def _generer_cle_sso():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


def _public_sso_b64(cle):
    from cryptography.hazmat.primitives import serialization
    return _b64url(cle.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw))


def bootstrap_sso():
    """Charge ou cree la cle de signature SSO, sans jamais l'exposer aux apps."""
    global _sso_private_key, _sso_public_key
    from cryptography.hazmat.primitives import serialization

    raw = None
    encoded = read_shared_value("CODELAB_SSO_PRIVATE_KEY")
    if encoded:
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError):
            raw = None

    if raw:
        _sso_private_key = _charger_cle_sso(raw)

    if _sso_private_key is None:
        try:
            with open(SSO_PRIVATE_FILE, "rb") as f:
                raw = f.read()
            _sso_private_key = _charger_cle_sso(raw)
        except (OSError, ValueError, TypeError):
            _sso_private_key = None

    if _sso_private_key is None:
        try:
            _sso_private_key = _generer_cle_sso()
        except ImportError:
            print("[app-manager] cryptography absente : SSO des applications indisponible.",
                  flush=True)
            return False

    raw = _sso_private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    private_b64 = _b64url(raw)
    _sso_public_key = _public_sso_b64(_sso_private_key)

    written = upsert_shared_block(
        "codelab-sso",
        [
            "# Identite signee transmise aux applications par le reverse proxy.",
            "# CODELAB_SSO_PRIVATE_KEY : secret du control-plane, jamais transmis aux apps.",
            "# CODELAB_SSO_PUBLIC_KEY : cle publique permettant aux apps de verifier les assertions.",
        ],
        {
            "CODELAB_SSO_PRIVATE_KEY": private_b64,
            "CODELAB_SSO_PUBLIC_KEY": _sso_public_key,
        })
    if not written:
        try:
            with open(SSO_PRIVATE_FILE, "wb") as f:
                f.write(raw)
            os.chmod(SSO_PRIVATE_FILE, 0o600)
        except OSError as e:
            print(f"[app-manager] cle SSO non persistante ({e}).", flush=True)
    return True


def jeton_sso(nom_app):
    """Construit une assertion JWT-like EdDSA valable 60 secondes pour une app."""
    if _sso_private_key is None or not is_authed():
        return ""

    maintenant = int(time.time())
    utilisateur = utilisateur_courant()
    role = role_courant() or ""
    compte = None if est_admin() else lire_utilisateurs().get(utilisateur)
    email = (compte or {}).get("email", "") if compte else ""

    header = {"alg": "EdDSA", "typ": "JWT"}
    payload = {
        "iss": SSO_ISSUER,
        "sub": utilisateur,
        "preferred_username": utilisateur,
        "role": role,
        "aud": nom_app,
        "iat": maintenant,
        "exp": maintenant + SSO_TOKEN_TTL,
    }
    if email:
        payload["email"] = email
        # Confirmee ou non : une application ne relie un compte local a
        # celui-ci par l'adresse QUE si elle est confirmee -- sinon, qui
        # saisit l'adresse d'un autre dans CodeLab prendrait son compte.
        payload["email_verified"] = bool((compte or {}).get("email_verifie"))

    message = _b64json(header) + "." + _b64json(payload)
    signature = _b64url(_sso_private_key.sign(message.encode("ascii")))
    return message + "." + signature


def admin_password():
    return _admin_password


# ------------------------- double authentification -------------------------
#
# TOTP (RFC 6238) : le code a six chiffres d'une application d'authentification.
# Ecrit ici plutot qu'importe : l'algorithme tient en vingt lignes avec la
# bibliotheque standard, et ce service n'a que trois dependances -- en ajouter
# une pour cela serait disproportionne.
#
# A quoi cela sert : le mot de passe du panneau est un secret unique. S'il
# fuit -- capture sur un reseau, note quelque part, reutilise -- il donne
# l'execution de commandes sur la machine. Le second facteur exige en plus un
# appareil physique, et une fuite du seul mot de passe ne suffit plus.
#
# Desactive par defaut : sans secret enregistre, la connexion se fait comme
# avant. C'est un reglage a activer depuis le panneau le jour ou il est
# expose au-dela du reseau local.
TOTP_PAS = 30          # secondes par code, valeur universelle
TOTP_CHIFFRES = 6
TOTP_TOLERANCE = 1     # +/- un intervalle, pour une horloge legerement decalee


def totp_nouveau_secret():
    """20 octets aleatoires en base32, le format que lisent les applications."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, compteur):
    # Le "=" de remplissage est retire du secret affiche (les applications ne
    # l'aiment pas) : il faut donc le remettre avant de decoder.
    cle = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    empreinte = hmac.new(cle, struct.pack(">Q", compteur), hashlib.sha1).digest()
    debut = empreinte[-1] & 0x0F
    tronque = struct.unpack(">I", empreinte[debut:debut + 4])[0] & 0x7FFFFFFF
    return str(tronque % (10 ** TOTP_CHIFFRES)).zfill(TOTP_CHIFFRES)


def totp_verifie(secret, code):
    code = (code or "").strip().replace(" ", "")
    if not secret or not code.isdigit() or len(code) != TOTP_CHIFFRES:
        return False
    compteur = int(time.time()) // TOTP_PAS
    for ecart in range(-TOTP_TOLERANCE, TOTP_TOLERANCE + 1):
        # compare_digest plutot que "==" : meme raison que pour le mot de passe.
        if secrets.compare_digest(code, totp_code(secret, compteur + ecart)):
            return True
    return False


def totp_actif():
    return bool(_totp_secret)


def totp_uri(secret, compte=None):
    """L'adresse otpauth:// que lisent les applications d'authentification.

    Le nom du compte apparait dans l'application du telephone : avec plusieurs
    comptes CodeLab sur le meme appareil, "CodeLab:admin" et "CodeLab:marie"
    se distinguent, la ou deux entrees "CodeLab" seraient indiscernables.
    """
    compte = compte or TOTP_COMPTE
    return (f"otpauth://totp/CodeLab:{compte}?secret={secret}"
            f"&issuer=CodeLab&algorithm=SHA1&digits={TOTP_CHIFFRES}&period={TOTP_PAS}")


def qr_svg(donnee):
    """L'adresse otpauth en QR code, en SVG. "" si la bibliotheque manque.

    Recopier une cle de 32 caracteres a la main sur un telephone est le
    moment ou l'inscription echoue : une lettre pour une autre, et le code
    genere ne tombera jamais juste. Le QR code supprime cette etape.

    Facultatif volontairement : le panneau doit rester lancable depuis un
    depot fraichement clone avec Flask pour seule dependance. Sans la
    bibliotheque, la page retombe sur la cle a saisir -- ce qui marchait
    hier marche encore.

    Le SVG est peint en noir sur blanc, quel que soit le theme : un lecteur
    de QR code a besoin de ce contraste, et un code clair sur fond sombre
    n'est pas lu par tous les telephones.
    """
    try:
        import qrcode
    except ImportError:
        return ""
    code = qrcode.QRCode(border=2)
    code.add_data(donnee)
    code.make(fit=True)
    grille = code.get_matrix()
    cote = len(grille)

    # Un rectangle par suite horizontale de modules noirs, pas un par module :
    # le SVG passe de plusieurs milliers de balises a quelques centaines.
    rects = []
    for y, ligne in enumerate(grille):
        x = 0
        while x < cote:
            if not ligne[x]:
                x += 1
                continue
            debut = x
            while x < cote and ligne[x]:
                x += 1
            rects.append(f'<rect x="{debut}" y="{y}" width="{x - debut}" height="1"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {cote} {cote}" shape-rendering="crispEdges">'
            f'<rect width="{cote}" height="{cote}" fill="#ffffff"/>'
            f'<g fill="#000000">{"".join(rects)}</g></svg>')


# --------------------------- auth ---------------------------

RATE_LIMIT_WINDOW = 300  # 5 min
RATE_LIMIT_MAX = 5


# X-Forwarded-For n'est croyable que derriere un proxy de confiance qui le
# reecrit. Le panneau est publie directement sur le port 9001 : n'importe quel
# client peut donc poser l'en-tete qu'il veut, et le faire varier a chaque
# essai -- ce qui donnait a chaque tentative de connexion un compteur neuf et
# annulait purement et simplement la limite de 5 essais par 5 minutes.
# Derriere un vrai reverse proxy, cocher la case dans Exposition -- ou poser
# APP_MANAGER_TRUST_PROXY=1 dans le compose, qui l'emporte.
#
# Il n'y a PLUS de constante ici, volontairement : elle etait calculee a
# l'import, donc un reglage change depuis la page n'aurait ete vu par
# personne jusqu'au redemarrage. trust_proxy() est definie plus bas, avec
# l'etat d'exposition qu'elle lit.


def _client_ip():
    """L'adresse du client.

    Derriere un proxy de confiance, c'est la DERNIERE adresse de
    X-Forwarded-For qui fait foi, jamais la premiere. nginx
    ($proxy_add_x_forwarded_for) AJOUTE l'adresse qu'il voit a la fin de ce
    que le client a envoye : la premiere est donc celle que le client a
    choisie. La prendre laissait n'importe qui, depuis Internet, se dire
    « 192.168.1.10 » -- et passer la restriction de l'administration au
    reseau local comme la limite de tentatives.
    """
    if trust_proxy():
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def rate_limited():
    ip = _client_ip()
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= RATE_LIMIT_MAX


def register_failed_attempt():
    ip = _client_ip()
    _login_attempts.setdefault(ip, []).append(time.time())


def detail_pour_admin(message, e):
    """Le message d'une erreur technique, selon qui le lit.

    L'administrateur recoit le detail (le type et le texte de l'exception) :
    c'est lui qui repare. Un compte utilisateur, ou une page de connexion,
    ne recoit que la phrase simple -- les erreurs techniques ne remontent
    qu'a l'administrateur. Le detail part toujours dans le journal du
    conteneur, pour qu'il ne soit pas perdu.
    """
    detail = f"{type(e).__name__}: {e}"
    print(f"[app-manager] {message} ({detail})", flush=True)
    if is_authed() and est_admin():
        return f"{message} ({detail})"
    return message


def avec_essais_restants(message):
    """Le refus dit combien d'essais il reste avant la pause.

    « Identifiants incorrects. » seul laissait taper au hasard jusqu'au
    blocage, puis decouvrir une attente de cinq minutes sans l'avoir vue
    venir. A appeler APRES register_failed_attempt() : le compte inclut
    l'essai qu'on vient de rater. Rien n'est dit tant qu'il en reste plus de
    trois -- un mot de passe mal tape une fois n'appelle pas d'avertissement.
    """
    ip = _client_ip()
    now = time.time()
    faits = len([t for t in _login_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW])
    restants = RATE_LIMIT_MAX - faits
    pause = f"{RATE_LIMIT_WINDOW // 60} minutes"
    if restants <= 0:
        return f"{message} Trop d'essais : patiente {pause} avant de réessayer."
    if restants <= 3:
        return (f"{message} Encore {restants} essai{'s' if restants > 1 else ''} "
                f"avant une pause de {pause}.")
    return message


def is_authed():
    return session.get("authed") is True


def _refus(message, code):
    """Refus coherent entre l'API et les pages : un appel fetch() veut un
    code et un message, un clic dans le navigateur veut la page de
    connexion."""
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), code
    # Une page du panneau demandee sans session : on y revient apres la
    # connexion (login.html ne suit qu'un chemin local, voir SUITE).
    if request.method == "GET" and request.path != "/":
        return redirect("/login?suite=" + urllib.parse.quote(request.path, safe="/"))
    return redirect("/login")


def require_auth(view):
    """Une session, quel que soit son role. Pour ce qu'un utilisateur voit."""
    def wrapped(*a, **kw):
        if not is_authed():
            return _refus("Non authentifie.", 401)
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


# --------------------- une origine a part pour les applications ---------------
#
# Le probleme, tant qu'il n'etait pas resolu : les applications etaient servies
# sous :9001/<nom>/, c'est-a-dire dans l'ORIGINE du panneau. Une faille XSS
# dans une application quelconque -- du code que tu ecris vite, pas du code
# durci -- donnait acces au panneau : lire son DOM, lire le jeton CSRF dans sa
# page, piloter la stack. Aucun jeton ne protege de cela, puisque le script
# hostile est dans la meme origine que ce qu'il attaque.
#
# Les applications ont donc leur propre port, donc leur propre ORIGINE :
# une origine, c'est un schema, un hote ET un port. Ce que cela change :
#
#   - un script d'une application ne lit plus le DOM du panneau ni sa page,
#     donc plus le jeton ;
#   - il peut encore ENVOYER une requete au panneau avec le cookie -- le
#     cookie est porte par l'hote, pas par le port, et SameSite raisonne en
#     "site", ou le port ne compte pas non plus. Mais sans le jeton, cette
#     requete est refusee. C'est ici que le jeton CSRF prend tout son sens :
#     seul, il ne servait a rien contre une application ; avec la separation
#     des origines, il devient ce qui la rend efficace.
#
# Le cookie de session continue par ailleurs d'etre retire avant d'atteindre
# l'application elle-meme (voir strip_session_cookie).
#
# 0 desactive la separation : les applications repassent sous le port du
# panneau, comme avant. C'est aussi ce qui se produit tout seul si le second
# port ne peut pas s'ouvrir -- mieux vaut des applications joignables et un
# avertissement qu'une stack a moitie morte apres une mise a jour.
APPS_PORT = int(os.environ.get("APP_MANAGER_APPS_PORT", "9002") or 0)

# Le port du panneau. Sert a une seule chose, mais elle compte : ecrire, DANS
# une page servie sur le port des applications, un lien qui ramene au hub.
PANEL_PORT = int(os.environ.get("MANAGER_PORT", "9001") or 9001)

# Pose par servir() une fois le second ecouteur reellement ouvert. Tant qu'il
# est faux, rien ne change : ni redirection, ni restriction.
_origines_separees = {"actif": False}


def origines_separees():
    return _origines_separees["actif"]


def requete_d_application():
    """Cette requete est-elle pour une application (le proxy, ou un
    sous-domaine) plutot que pour le panneau ?"""
    return (request.endpoint in ("proxy", "proxy_noslash")
            or bool(getattr(g, "sous_domaine", "")))


# Un nom d'hote ordinaire, et rien d'autre. Ce qui suit n'est pas de la
# prudence gratuite : request.host vient de l'en-tete Host, donc du CLIENT.
# Nous le recopions dans un attribut href (le ruban de retour, les pages
# d'erreur du proxy) et dans un en-tete Location. Un guillemet bien place
# dans un Host sortirait de l'attribut et deviendrait du script dans la page
# d'une application. Un nom d'hote n'a aucune raison de contenir autre chose
# que des lettres, des chiffres, un point ou un tiret : on refuse tout le
# reste au lieu de l'echapper -- il n'existe aucun cas legitime a rattraper.
_HOTE_ORDINAIRE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


def hote_propre(brut):
    """Le nom d'hote d'un en-tete Host, sans son port. Vide s'il est douteux.

    Werkzeug ecarte deja les Host malformes avant nous -- request.host rend
    alors une chaine vide. Ce filtre-ci ne fait donc pas double emploi par
    hasard : il tient tout seul, sans dependre de la version d'une
    bibliotheque ni de la porte d'entree utilisee, pour une regle dont le
    cout est une expression reguliere.
    """
    hote = (brut or "").split(":")[0]
    return hote if _HOTE_ORDINAIRE.match(hote) else ""


def hote_demande():
    """Le nom d'hote de la requete, s'il est propre. Sinon la chaine vide."""
    if not has_request_context():
        return ""
    return hote_propre(request.host)


def origine_applications():
    """Le port des applications, vu depuis le navigateur (en local).

    Chaine vide quand le nom d'hote n'est pas exploitable : l'appelant
    renonce alors a separer plutot que d'ecrire une adresse douteuse.
    """
    hote = hote_demande()
    if not hote:
        return ""
    return f"{request.scheme}://{hote}:{APPS_PORT}"


# UNE APPLICATION, UN SOUS-DOMAINE. Ouvert par un nom de domaine depuis
# internet (relaye par le VPS), le panneau sert chaque application sous son
# propre nom : « blog » sur https://blog.toi.duckdns.org quand le panneau est
# a https://toi.duckdns.org. Rien a declarer : le nom de l'application EST
# son adresse. Chaque sous-domaine est une origine a lui, la separation tient
# comme avec le port. En local -- une adresse IP, un nom sans point ou du
# reseau domestique --, rien ne change : le port des applications, /<nom>/.
_SUFFIXES_LOCAUX = (".local", ".lan", ".home", ".internal", ".localhost", ".arpa")


def sur_le_port_des_apps():
    return (origines_separees() and has_request_context()
            and str(request.environ.get("SERVER_PORT", "")) == str(APPS_PORT))


def schema_vu():
    """http ou https, tel que le navigateur le voit (derriere le VPS, nginx
    l'annonce dans X-Forwarded-Proto)."""
    annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
    return "https" if annonce == "https" or request.scheme == "https" else "http"


def _nom_de_domaine(hote):
    if "." not in hote or hote.endswith(_SUFFIXES_LOCAUX):
        return False
    try:
        ipaddress.ip_address(hote)
        return False
    except ValueError:
        return True


def domaine_des_applications():
    """Le domaine sous lequel chaque application prend son sous-domaine, ou "".

    Seulement pour une requete RELAYEE (le VPS pose X-Forwarded-*) qui arrive
    par un nom de domaine. Sur le port du panneau, c'est son nom d'hote ; sur
    celui des applications, ce nom d'hote moins l'application.
    """
    if not origines_separees() or not has_request_context():
        return ""
    if not (request.headers.get("X-Forwarded-Proto") or request.headers.get("X-Forwarded-For")):
        return ""
    hote = hote_demande().lower()
    if not _nom_de_domaine(hote):
        return ""
    if sur_le_port_des_apps():
        nom, _, reste = hote.partition(".")
        return reste if nom in load() and _nom_de_domaine(reste) else ""
    return hote


def application_du_sous_domaine():
    """Sur le port des applications : l'application que nomme le sous-domaine."""
    if not sur_le_port_des_apps() or not domaine_des_applications():
        return ""
    return hote_demande().lower().partition(".")[0]


def modele_adresse_application():
    """L'adresse d'une application, « {nom} » a remplacer : la page en fait
    les liens de toutes ses tuiles sans redemander au serveur."""
    domaine = domaine_des_applications()
    if domaine:
        return f"{schema_vu()}://{{nom}}.{domaine}/"
    base = origine_applications() if origines_separees() else ""
    return base + "/{nom}/"


def origine_de_l_application(nom):
    """L'origine (schema, hote, port) d'une application, ou "" si c'est
    celle du panneau."""
    adresse = modele_adresse_application().replace("{nom}", nom)
    m = re.match(r"^[a-z]+://[^/]+", adresse)
    return m.group(0) if m else ""


def cadres_permis():
    """La source frame-src des pages du panneau qui encadrent une application."""
    domaine = domaine_des_applications()
    if domaine:
        return f"{schema_vu()}://*.{domaine}"
    return (origine_applications() if origines_separees() else "") or "'self'"


# ------------------------- Dagster, dans le hub -------------------------
#
# Dagster n'est pas une application du panneau : personne ne le declare,
# personne ne le demarre d'ici, il n'a ni dossier ni commande. C'est un
# SERVICE de la stack, servi sur son propre port derriere le proxy qui exige
# la session du panneau.
#
# Il manquait pourtant la ou on le cherche. Le hub est la page d'accueil de
# CodeLab -- « la liste des projets ouvrables » -- et Dagster s'ouvrait en
# retapant une adresse et un numero de port de memoire. Une tuile l'y remet,
# avec les autres, et elle mene au port 3000.
DAGSTER_NOM = "dagster"
DIAGNOSTIC_NOM = "diagnostic"

# LES APPLICATIONS PAR DEFAUT : celles que CodeLab apporte avec lui.
#
# Elles se comportent comme les autres -- meme tuile dans le hub, meme ligne
# dans la liste, meme fiche -- et c'est voulu : ce sont des applications, pas
# des boutons a part. Ce qui les distingue tient en une phrase : IL N'Y A
# RIEN A Y CONFIGURER.
#
# Rien a configurer, donc rien a casser : on ne renomme pas « diagnostic »,
# on ne change pas sa commande de lancement, on ne le supprime pas par megarde
# -- et il ne se rend pas publique, ce qui offrirait a un visiteur anonyme
# l'etat des lieux complet de l'installation.
#
# Deux consequences, tenues cote SERVEUR et pas seulement dans la page :
#
#   1. reservees a l'administrateur. Pas « privees, donc autorisables a un
#      compte » : reservees. Dagster permet de lancer des jobs, donc
#      d'executer du code ici ; le diagnostic nomme les conteneurs, les
#      comptes sans second facteur et ce qui est expose.
#   2. soumises a la meme regle de reseau local que l'administration. Si
#      l'administration est limitee au reseau local, ces deux-la le sont
#      aussi -- meme session, meme frontiere.
APPS_PAR_DEFAUT = (DAGSTER_NOM, DIAGNOSTIC_NOM)
# Mailpit : un service de la stack, comme Dagster, mais qui n'a jamais ete
# declare dans apps.json. Il en a les memes regles -- reserve a
# l'administrateur (son interface montre TOUS les mails envoyes, liens de
# reinitialisation compris), rien a configurer, rien a demarrer ni arreter.
MAILPIT_NOM = "mailpit"
APPLICATIONS_PROTEGEES = {DAGSTER_NOM, DIAGNOSTIC_NOM, MAILPIT_NOM}

# Le nom AFFICHE des deux applications par defaut. Leur cle est le chemin --
# « dagster », « diagnostic » -- et c'est elle qui s'affichait : deux tuiles
# en minuscules au milieu de projets nommes par leur auteur. Elles n'ont pas
# de formulaire ou corriger cela, la valeur est donc posee ici.
# « Messages » : l'application qui recense les messages laisses depuis le hub
# et en fait des projets, des taches et une feuille de route. Elle est servie
# par le panneau lui-meme (/messages) -- ses donnees sont celles du panneau,
# et elle est reservee a l'administrateur, comme Dagster et Mailpit.
MESSAGERIE_NOM = "messages"
# L'ancien nom de l'application, garde pour les rangements deja enregistres.
MESSAGERIE_ANCIEN_NOM = "demandes"
TITRES_PAR_DEFAUT = {DAGSTER_NOM: "Dagster", DIAGNOSTIC_NOM: "Diagnostic",
                     MAILPIT_NOM: "Mailpit", MESSAGERIE_NOM: "Messages"}

# La tuile du diagnostic a besoin d'une phrase, et cette phrase ne peut pas
# s'ecrire a la main : la fiche d'une application par defaut n'a pas de
# formulaire. Elle est donc posee ici, et le panneau la met en place au
# demarrage si elle manque (voir tenir_les_applications_par_defaut).
# Les descriptions des applications par defaut tiennent ENTIERES sur leur
# tuile du hub (DESCRIPTION_VISIBLE dans dashboard.html, environ 45
# caracteres sur deux lignes) : 40 au plus, verifie par les tests.
DIAGNOSTIC_DESCRIPTION = "L'état de santé de CodeLab"
# Les descriptions qu'une version precedente a enregistrees : reconnues, et
# remplacees par la courte (une description reecrite a la main est gardee).
DIAGNOSTIC_DESCRIPTIONS_ANCIENNES = (
    "État de santé de l'installation : les services se parlent-ils, la base "
    "répond-elle, et qu'est-ce qui est ouvert ?",)


def refus_action_protegee(nom, action):
    if nom in APPLICATIONS_PROTEGEES:
        return f"« {nom} » est une application de supervision : l'action « {action} » est interdite."
    return None


def est_app_par_defaut(nom):
    return nom in APPS_PAR_DEFAUT or nom in (MAILPIT_NOM, MESSAGERIE_NOM)


def refus_configuration(nom):
    """Le message de refus quand on tente de configurer une application par
    defaut, ou None si ce n'en est pas une.

    Le controle est ICI, pas seulement dans la page : masquer un bouton ne
    protege rien, la route reste appelable a la main.
    """
    if not est_app_par_defaut(nom):
        return None
    return (f"« {nom} » est une application par défaut de CodeLab : "
            f"elle n'est pas configurable.")


DAGSTER_PORT = int(os.environ.get("APP_MANAGER_DAGSTER_PORT", "3000") or 3000)
# Vu depuis ce conteneur, pour savoir s'il repond. C'est le PROXY qu'on
# interroge et non Dagster : c'est lui qui publie le port, et un Dagster
# debout derriere un proxy tombe ne s'ouvre pas davantage.
DAGSTER_HOTE_INTERNE = os.environ.get("APP_MANAGER_DAGSTER_HOTE") or "codelab-dagster-proxy"
DAGSTER_AMONT = os.environ.get("APP_MANAGER_DAGSTER_AMONT") or "codelab-dagster"


def origine_dagster():
    """L'adresse de Dagster vue DU NAVIGATEUR, pas du conteneur.

    Le meme hote que le panneau, un autre port : c'est ainsi que le compose
    le publie, et le cookie de session traverse -- sa portee ignore le
    numero de port.
    """
    hote = hote_demande()
    if not hote:
        return ""
    return f"{request.scheme}://{hote}:{DAGSTER_PORT}/"


_sondes_de_port = {}


def _port_repond(hote, port):
    """Ce port est-il ouvert ? Reponse mise en cache 10 s.

    Le hub se rafraichit toutes les dix secondes : sans ce cache, chaque
    passage ouvrirait une connexion de plus, pour une reponse qui ne change
    pas d'une seconde a l'autre.
    """
    cle = (hote, port)
    sonde = _sondes_de_port.setdefault(cle, {"quand": 0.0, "ouvert": False})
    maintenant = time.time()
    if maintenant - sonde["quand"] < 10:
        return sonde["ouvert"]
    ouvert = False
    s = socket.socket()
    s.settimeout(1.0)
    try:
        ouvert = s.connect_ex((hote, port)) == 0
    except OSError:
        ouvert = False
    finally:
        s.close()
    sonde.update(quand=maintenant, ouvert=ouvert)
    return ouvert


def dagster_repond():
    return _port_repond(DAGSTER_HOTE_INTERNE, DAGSTER_PORT)


# Mailpit : meme montage que Dagster. Son interface est publiee par le meme
# proxy authentifiant, sur un autre port.
MAILPIT_PORT = int(os.environ.get("APP_MANAGER_MAILPIT_PORT", "8025") or 8025)
MAILPIT_HOTE_INTERNE = os.environ.get("APP_MANAGER_MAILPIT_HOTE") or "codelab-dagster-proxy"
MAILPIT_AMONT = os.environ.get("APP_MANAGER_MAILPIT_AMONT") or "codelab-mailpit"


def origine_mailpit():
    """L'adresse de Mailpit vue du navigateur : meme hote, port 8025."""
    hote = hote_demande()
    if not hote:
        return ""
    return f"{request.scheme}://{hote}:{MAILPIT_PORT}/"


def mailpit_repond():
    return _port_repond(MAILPIT_HOTE_INTERNE, MAILPIT_PORT)


# ------------------- ce que le panneau sait de Dagster -------------------
#
# Dagster tourne dans un AUTRE conteneur : le panneau n'a pas son pid, donc
# ni son processeur ni sa memoire -- et il n'aura jamais rien de plus, sauf a
# lui donner la socket Docker, ce qui reviendrait a lui donner la machine.
#
# Ce qu'il peut mesurer, en revanche, est exactement ce qui compte pour un
# service qu'on ne fait pas tourner soi-meme : REPOND-IL, ET EN COMBIEN DE
# TEMPS. Les deux courbes de sa fiche portent donc cela, et la fiche le dit
# -- une courbe intitulee « CPU » qui montrerait autre chose serait pire que
# pas de courbe du tout.
_dagster_suivi = {"debout": None}


def dagster_sonde():
    """(debout, millisecondes). Interroge l'AMONT, pas le proxy.

    Mesurer le proxy dirait que nginx va bien, ce qu'on sait deja : ce qu'on
    veut savoir, c'est si Dagster derriere lui repond.
    """
    debut = time.time()
    try:
        with urllib.request.urlopen(f"http://{DAGSTER_AMONT}:{DAGSTER_PORT}/",
                                    timeout=5) as r:
            debout = 200 <= r.status < 500
    except Exception:
        return False, (time.time() - debut) * 1000
    return debout, (time.time() - debut) * 1000


def dagster_tick():
    """Un tour de surveillance, appele par le moniteur.

    Les mesures alimentent la meme reserve que les applications du panneau,
    donc la meme fiche et les memes courbes. Le JOURNAL, lui, ne recoit que
    les CHANGEMENTS d'etat : une ligne toutes les dix secondes pour dire que
    tout va bien n'est pas un journal, c'est un mur.
    """
    debout, ms = dagster_sonde()
    record_metrics(DAGSTER_NOM, round(ms, 1), 100.0 if debout else 0.0)
    if _dagster_suivi["debout"] is debout:
        return
    premier = _dagster_suivi["debout"] is None
    _dagster_suivi["debout"] = debout
    if debout:
        journal_du_service(DAGSTER_NOM,
                           f"Dagster répond ({ms:.0f} ms)."
                           if premier else
                           f"Dagster répond de nouveau ({ms:.0f} ms).")
    else:
        journal_du_service(
            DAGSTER_NOM,
            f"Dagster ne répond pas sur http://{DAGSTER_AMONT}:{DAGSTER_PORT}/ "
            f"-- conteneur arrêté ? « docker compose up -d codelab-dagster ».")


_mailpit_suivi = {"debout": None}


def mailpit_sonde():
    """(debout, millisecondes). /readyz est la seule route de Mailpit ouverte
    sans son mot de passe interne -- et c'est exactement la question."""
    debut = time.time()
    try:
        with urllib.request.urlopen(f"http://{MAILPIT_AMONT}:{MAILPIT_PORT}/readyz",
                                    timeout=5) as r:
            debout = r.status == 200
    except Exception:
        return False, (time.time() - debut) * 1000
    return debout, (time.time() - debut) * 1000


def mailpit_tick():
    """Comme dagster_tick : des mesures a chaque tour, une ligne de journal
    seulement quand l'etat change."""
    debout, ms = mailpit_sonde()
    record_metrics(MAILPIT_NOM, round(ms, 1), 100.0 if debout else 0.0)
    if _mailpit_suivi["debout"] is debout:
        return
    premier = _mailpit_suivi["debout"] is None
    _mailpit_suivi["debout"] = debout
    if debout:
        journal_du_service(MAILPIT_NOM,
                           f"Mailpit répond ({ms:.0f} ms)."
                           if premier else
                           f"Mailpit répond de nouveau ({ms:.0f} ms).")
    else:
        journal_du_service(
            MAILPIT_NOM,
            f"Mailpit ne répond pas sur http://{MAILPIT_AMONT}:{MAILPIT_PORT}/ "
            f"-- les mails des applications ne partent plus. "
            f"« docker compose up -d codelab-mailpit ».")


def journal_du_service(nom, ligne):
    """Ecrit dans le journal d'une application que le panneau ne lance pas.

    Le meme fichier que pour une application lancee par le panneau : la fiche
    et son flux en direct n'ont donc rien a savoir de la difference, et le
    journal d'un service se lit exactement comme les autres.
    """
    rotate_log_if_needed(nom)
    quand = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, nom + ".log"), "a") as f:
            f.write(f"[{quand}] {ligne}\n")
    except OSError:
        pass        # journaliser n'est pas le travail : voir journaliser()


# Les services de la stack qu'un compte peut ranger hors de son hub. Une
# liste nommee plutot qu'un « tout ce qui n'est pas une application » : ce
# qui est masquable se decide ici, et se lit d'un coup d'oeil.
SERVICES_MASQUABLES = frozenset({DAGSTER_NOM, MAILPIT_NOM, MESSAGERIE_NOM})

# La phrase de la tuile Dagster, lue par les deux listes ci-dessous. Deux
# copies divergeraient, et la tuile rangee ne se decrirait plus comme celle
# qu'on retrouve en la remettant.
DESCRIPTION_DAGSTER = "Les jobs de données et leurs plannings."
DESCRIPTION_MAILPIT = "Les mails envoyés par tes applications."
DESCRIPTION_MESSAGERIE = "Les messages du hub, rangés en projets."


def services_masques():
    """Les services que CE compte a ranges hors de son hub.

    Meme forme que services_du_hub() -- nom, titre, description -- et rien de
    plus : cette liste ne sert qu'a les reproposer dans les reglages. Elle est
    vide pour un compte qui n'est pas administrateur, comme l'autre : un
    service qu'on ne peut pas voir n'est pas un service qu'on range.
    """
    if not est_admin():
        return []
    masquees = masquees_du_compte()
    return [{"name": nom, "titre": TITRES_PAR_DEFAUT[nom],
             "description": description, "externe": True}
            for nom, description in ((DAGSTER_NOM, DESCRIPTION_DAGSTER),
                                     (MAILPIT_NOM, DESCRIPTION_MAILPIT),
                                     (MESSAGERIE_NOM, DESCRIPTION_MESSAGERIE))
            if nom in masquees]


def services_du_hub(avec_messagerie=True):
    """Les tuiles du hub qui ne sont pas des applications declarees.

    Dagster est RESERVE A L'ADMINISTRATEUR, et ce n'est pas une politesse :
    son interface permet de lancer des jobs, donc d'executer du code sur
    cette machine. Le proxy le refuse deja a un compte utilisateur (voir
    /api/auth-check) -- lui montrer une tuile qui le renverrait a la page de
    connexion serait promettre une porte qui ne s'ouvre pas.
    """
    services = []
    if not est_admin():
        return services
    # RANGE EST RANGE, meme pour un service. Dagster occupait une tuile que
    # rien ne permettait de retirer : sur une installation ou l'on n'ouvre
    # jamais l'orchestrateur, c'etait la seule tuile impossible a ecarter de
    # son propre hub. Le masquage reste ce qu'il est -- du rangement, par
    # compte, sans rien fermer : Dagster tourne, son adresse repond, et
    # l'administrateur le retrouve dans Parametres > Compte.
    #
    # Mailpit suit les memes regles, pour une raison voisine : son interface
    # montre tous les mails envoyes par les applications, liens de
    # reinitialisation de mot de passe compris.
    masquees = masquees_du_compte()
    if DAGSTER_NOM not in masquees:
        adresse = origine_dagster()
        if adresse:
            debout = dagster_repond()
            services.append(_tuile_service(DAGSTER_NOM, DESCRIPTION_DAGSTER,
                                           adresse, debout, DAGSTER_PORT))
    if MAILPIT_NOM not in masquees:
        adresse = origine_mailpit()
        if adresse:
            debout = mailpit_repond()
            services.append(_tuile_service(MAILPIT_NOM, DESCRIPTION_MAILPIT,
                                           adresse, debout, MAILPIT_PORT))
    # Messages : servie par le panneau lui-meme, donc toujours debout. Sa
    # phrase dit ce qui attend, comme le compteur d'une boite de reception.
    if avec_messagerie and MESSAGERIE_NOM not in masquees:
        tuile = _tuile_service(MESSAGERIE_NOM, DESCRIPTION_MESSAGERIE, "/messages", True, PANEL_PORT)
        nouvelles = sum(1 for m in lire_messages(limite=MESSAGES_AFFICHES)
                        if not m.get("lu") and not m.get("archive"))
        if nouvelles:
            tuile["description"] = (f"{nouvelles} nouveau{'x' if nouvelles > 1 else ''} "
                                    f"message{'s' if nouvelles > 1 else ''} à lire.")
        # Une page du panneau, pas un service a son propre port : la tuile
        # l'ouvre directement, sans passer par le proxy.
        tuile["interne"] = True
        tuile["non_lues"] = nouvelles
        services.append(tuile)
    return services


# Les services dont le logo n'a pas de fond propre (voir "plaque" plus bas).
SERVICES_LOGO_TRANSPARENT = frozenset({DAGSTER_NOM})


def _tuile_service(nom, description, adresse, debout, port):
    """Une tuile de service de la stack, avec les cles d'une application."""
    return {
        "name": nom,
        "titre": TITRES_PAR_DEFAUT[nom],
        "description": description,
        "categorie": CATEGORIE_CODELAB,
        "running": debout,
        "listening": debout,
        "visibility": VISIBILITE_PRIVEE,
        # Ce qui la distingue d'une application : elle a sa propre adresse,
        # et le panneau ne la demarre ni ne l'arrete.
        "externe": True,
        # Le fond clair sous le logo, sur la tuile du hub : seulement pour
        # un logo TRANSPARENT (Dagster), qui flotterait sans lui. Celui de
        # Mailpit porte deja son fond turquoise, comme le diagnostic : une
        # plaque blanche autour l'encadrait pour rien.
        "plaque": nom in SERVICES_LOGO_TRANSPARENT,
        "par_defaut": True,
        "url": adresse,
        # Les memes cles que les autres lignes, avec des valeurs vides : la
        # page lit ces champs sans se demander a chaque fois si elle a
        # affaire a un service. Un « undefined » dans une fiche est un defaut
        # d'affichage de plus a prevoir, et il finit toujours par passer.
        "path": "", "command": "", "port": port,
        "port_listeners": _ecouteurs_port(port),
        "failed": False, "crash_looping": False, "has_build": False, "dernier_echec": None,
        "build_command": "", "max_memory_mb": None, "alertes": [],
        "cpu_percent": 0.0, "memory_mb": 0.0,
        "presentation": None, "presentation_masquee": False, "presentation_fichier": "",
    }


def lien_panneau():
    """Le lien qui ramene au hub, ecrit pour l'origine qui sert la page.

    Un simple "/" a suffi tant que tout vivait sur le meme port. Depuis que
    les applications ont leur propre origine, "/" designe la racine du PORT
    DES APPLICATIONS -- et cette racine ne sert rien : la garde des origines
    y repond « Ce n'est pas le panneau ». Le ruban de retour, glisse dans
    chaque page d'application, ramenait donc vers une page d'erreur au lieu
    du hub.

    Sur le port du panneau, ou quand la separation n'a pas pu s'activer, "/"
    reste juste et reste relatif -- donc valable quel que soit le nom d'hote
    par lequel on est arrive.
    """
    if not sur_le_port_des_apps():
        return "/"
    # Sous un sous-domaine (blog.toi.duckdns.org), le panneau est au domaine.
    domaine = domaine_des_applications()
    if domaine:
        return f"{schema_vu()}://{domaine}/"
    hote = hote_demande()
    if not hote:
        return "/"
    return f"{request.scheme}://{hote}:{PANEL_PORT}/"


# ----------------- se connecter avec CodeLab, puis revenir ----------------
#
# Une application (privee, ou publique avec un bouton « Se connecter avec
# CodeLab ») envoie le visiteur a la connexion du panneau. Il doit revenir LA
# OU IL ETAIT, pas sur le hub. Mais la page de connexion ne suit qu'un chemin
# LOCAL (voir SUITE dans login.html) : une adresse quelconque en ferait un
# tremplin vers n'importe quel site. Le retour passe donc par une adresse du
# panneau, /ouvrir.app/<application>/<chemin>, qui ne mene QU'A une
# application declaree, sur l'origine des applications de ce serveur.
#
# Le point dans « ouvrir.app » n'est pas decoratif : un nom d'application ne
# peut pas en contenir (valid_name), cette adresse ne masque donc jamais une
# application.
_CHEMIN_APP = re.compile(r"^[A-Za-z0-9_\-./%~]*$")
# Le meme filtre que SUITE dans login.html, cote serveur.
_SUITE_LOCALE = re.compile(r"^/(?![/\\])[A-Za-z0-9_\-/%.]*$")


def suite_locale(brute):
    """Un chemin du panneau ou revenir apres la connexion, ou "/"."""
    s = str(brute or "")
    return s if _SUITE_LOCALE.match(s) and ".." not in s else "/"


def chemin_dans_app(brut):
    """Un chemin a l'interieur d'une application : jamais de « .. », jamais
    de schema ni d'hote -- au pire, la racine de l'application."""
    c = str(brut or "").lstrip("/")
    if not _CHEMIN_APP.match(c) or ".." in c.split("/") or "\\" in c:
        return ""
    return c


def adresse_application(nom, chemin=""):
    """L'adresse d'une page d'application, vue depuis le navigateur."""
    return (modele_adresse_application().replace("{nom}", urllib.parse.quote(nom, safe=""))
            + urllib.parse.quote(chemin_dans_app(chemin), safe="/%~"))


def suite_vers_application(nom, chemin=""):
    """Le « suite » de la connexion qui ramene dans une application."""
    return ("/ouvrir.app/" + urllib.parse.quote(nom, safe="") + "/"
            + urllib.parse.quote(chemin_dans_app(chemin), safe="/%"))


@flask_app.get("/ouvrir.app/<n>/")
@flask_app.get("/ouvrir.app/<n>/<path:chemin>")
@require_auth
def ouvrir_application_apres_connexion(n, chemin=""):
    if n not in load():
        return Response(_page("Introuvable", "Aucune application \u00ab " + n + " \u00bb."),
                        404, mimetype="text/html")
    # Pas de controle de droits ICI : le proxy les fait a l'arrivee, et dit
    # « Acces refuse » avec le nom de l'application -- une redirection n'a
    # rien a cacher de plus.
    return redirect(adresse_application(n, chemin), 302)


# SE DECONNECTER DE CODELAB DEPUIS UNE APPLICATION, puis y revenir.
#
# Une page qui DEMANDE avant de deconnecter : une deconnexion par simple
# lien serait declenchable par n'importe quel site (une image suffit). Le
# bouton envoie POST /logout avec le jeton de la session, comme le panneau.
@flask_app.get("/deconnexion.html")
def page_deconnexion():
    nom = request.args.get("app", "")
    cible = (adresse_application(nom, request.args.get("chemin", ""))
             if nom in load() else "/")

    def esc(x):
        return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    if is_authed():
        titre = "Se d\u00e9connecter de CodeLab ?"
        texte = ("Tu seras d\u00e9connect\u00e9 du panneau et de toutes les "
                 "applications o\u00f9 CodeLab te reconna\u00eet.")
        boutons = ('<a class="b" href="' + esc(cible) + '">Annuler</a>'
                   '<button class="b p" id="ok" type="button">Se d\u00e9connecter</button>')
        script = ('<script>document.getElementById("ok").onclick=function(){'
                  'this.disabled=true;fetch("/logout",{method:"POST",credentials:"same-origin",'
                  'headers:{"X-CodeLab-Jeton":' + json.dumps(jeton_session()) + '}})'
                  '.finally(function(){location.href=' + json.dumps(cible) + ';});};</script>')
    else:
        titre = "Tu n\u2019es pas connect\u00e9 \u00e0 CodeLab"
        texte = "Il n\u2019y a rien \u00e0 fermer."
        boutons = '<a class="b p" href="' + esc(cible) + '">Revenir</a>'
        script = ""
    page = ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>D\u00e9connexion \u00b7 CodeLab</title>'
            '<link rel="icon" type="image/png" href="/codelab-onglet.png">'
            '<style>body{margin:0;min-height:100vh;display:grid;place-items:center;'
            'background:#f1f2f7;color:#15172a;font:15px/1.5 -apple-system,BlinkMacSystemFont,'
            '"Segoe UI",Roboto,sans-serif}.c{background:#fff;border-radius:22px;padding:28px 30px;'
            'max-width:400px;margin:16px;box-shadow:0 10px 40px rgba(21,23,42,.1)}'
            'h1{font-size:19px;margin:0 0 8px}p{color:#5e6280;margin:0 0 22px}'
            '.r{display:flex;gap:10px;justify-content:flex-end}.b{font:inherit;font-weight:700;'
            'padding:9px 18px;border-radius:11px;border:1px solid #e3e5ee;background:#f5f6fa;'
            'color:#15172a;text-decoration:none;cursor:pointer}.p{background:#3b3fd8;'
            'border-color:#3b3fd8;color:#fff}@media(prefers-color-scheme:dark){body{background:#0b0d18;'
            'color:#eceefb}.c{background:#121527}p{color:#9599b8}.b{background:#181c32;'
            'border-color:#232843;color:#eceefb}.p{background:#6366f1;border-color:#6366f1}}</style>'
            '</head><body><main class="c"><h1>' + titre + '</h1><p>' + texte + '</p>'
            '<div class="r">' + boutons + '</div></main>' + script + '</body></html>')
    r = Response(page, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


@flask_app.before_request
def fermer_les_sessions_suspendues():
    """Suspendre doit valoir TOUT DE SUITE, pas a la prochaine connexion.

    Sans cette garde, un compte suspendu gardait sa session ouverte : celui
    dont on vient de fermer l'acces continuait d'ouvrir ses projets tant
    qu'il ne se deconnectait pas -- c'est-a-dire, en pratique, indefiniment.
    Le registre est relu a chaque requete (il l'est deja pour les projets
    autorises), et une session dont le compte a disparu ou a ete suspendu
    est effacee sur place.

    On efface et on laisse passer : la route dira elle-meme ce qu'elle exige,
    et rediriger ici renverrait la page de connexion vers elle-meme.
    """
    if session.get("authed") is not True or est_admin():
        return None
    nom = utilisateur_courant()
    compte = lire_utilisateurs().get(nom) if nom else None
    if compte is None or compte_suspendu(compte):
        session.clear()
        return None
    # Mot de passe change ou reinitialise, cles Face ID retirees : les
    # sessions ouvertes AVANT tombent. Une session est un cookie signe, que
    # le serveur ne peut pas rappeler -- sans cette date, un cookie vole
    # survivait au changement de mot de passe fait justement pour s'en
    # debarrasser.
    depuis = compte.get("sessions_depuis") or 0
    if (session.get("ouverte") or 0) < depuis:
        session.clear()
    return None


# AUTHENTIFICATION RECENTE. Ajouter une cle Face ID ou changer l'adresse qui
# recoit les codes, c'est installer un acces DURABLE au compte. Un cookie de
# session vole ne doit pas y suffire : ces deux gestes exigent une connexion
# de moins d'un quart d'heure. Le panneau propose Face ID juste apres la
# connexion, donc dans cette fenetre.
AUTHENTIFICATION_RECENTE = 900


def authentification_recente():
    return int(time.time()) - int(session.get("ouverte") or 0) <= AUTHENTIFICATION_RECENTE


def refus_authentification_ancienne():
    if authentification_recente():
        return None
    return jsonify({"error": "Par sécurité, reconnecte-toi avant cette opération "
                             "(connexion de moins de 15 minutes requise).",
                    "reconnexion": True}), 403


def fermer_les_autres_sessions(compte, garder_celle_ci=False, nom=None):
    """Invalide les sessions ouvertes de ce compte (a ecrire ensuite)."""
    maintenant = int(time.time())
    compte["sessions_depuis"] = maintenant
    if garder_celle_ci:
        # Celle qui vient de prouver le mot de passe reste ouverte.
        session["ouverte"] = maintenant
    if nom:
        # Et la liste des appareils le reflete aussitot.
        try:
            garder = session.get("sid") if garder_celle_ci else None
            revoquer_appareils([l["sid"] for l in appareils_du_compte(nom) if l["sid"] != garder])
        except Exception:                                         # noqa: BLE001
            pass


@flask_app.before_request
def expirer_les_sessions_trop_vieilles():
    """La limite absolue : trente jours depuis l'ouverture, point.

    L'inactivite est tenue par le cookie lui-meme (PERMANENT_SESSION_LIFETIME,
    repoussee a chaque requete). Elle ne dit rien d'une session ENTRETENUE :
    un onglet laisse ouvert sur une page qui interroge l'API la maintiendrait
    valable pour toujours. Cette garde-ci coupe au bout de trente jours,
    quoi qu'il arrive -- et c'est le SERVEUR qui compte, dans un cookie signe
    que le navigateur ne peut pas retoucher.

    On efface et on laisse passer, sans rediriger : la requete continue en
    visiteur anonyme, et c'est la route qui dira ce qu'elle exige. Rediriger
    ici renverrait aussi la page de connexion vers elle-meme.
    """
    if session.get("authed") is not True:
        return None
    ouverte = session.get("ouverte")
    if not isinstance(ouverte, (int, float)):
        # Session ouverte avant ce mecanisme : on la date maintenant plutot
        # que de deconnecter tout le monde a la mise a jour.
        session["ouverte"] = int(time.time())
        return None
    limite = (SESSION_ADMIN_ABSOLUE if session.get("role", ROLE_ADMIN) == ROLE_ADMIN
              else SESSION_ABSOLUE)
    if time.time() - ouverte > limite.total_seconds():
        qui = session.get("utilisateur") or ""
        session.clear()
        journaliser("session", qui=qui, action="expiree (" + (
                        "12 heures" if limite == SESSION_ADMIN_ABSOLUE else "30 jours") + ")",
                    ip=_adresse_client())
    return None


# ------------------------- les appareils connectes -------------------------
#
# Une session est un cookie signe : le serveur ne pouvait ni dire ou l'on
# etait connecte, ni couper un appareil perdu sans changer le mot de passe.
# Chaque session recoit desormais un identifiant (session["sid"]) et une
# ligne dans la table appareils ; la retirer deconnecte l'appareil a sa
# requete suivante.
#
# Le registre n'est pas relu a chaque requete -- le proxy en sert des
# dizaines par page. Une deconnexion faite ici est connue tout de suite
# (_sid_revoques) ; la base est relue au plus toutes les minutes par
# session, ce qui suffit apres un redemarrage du panneau.

APPAREIL_CONTROLE = 60
_sid_controles = {}
_sid_revoques = set()
_appareils_verrou = threading.Lock()


def id_public_appareil(sid):
    """Ce que la page voit : jamais le sid lui-meme."""
    return hashlib.sha256(("appareil:" + sid).encode()).hexdigest()[:16]


def _agent_court():
    return (request.headers.get("User-Agent") or "")[:300]


def revoquer_appareils(sids):
    sids = [x for x in sids if x]
    if not sids:
        return 0
    with _appareils_verrou:
        _sid_revoques.update(sids)
        for x in sids:
            _sid_controles.pop(x, None)
    with stockage.transaction(DB_FILE) as cx:
        n = 0
        for x in sids:
            n += cx.execute("DELETE FROM appareils WHERE sid = ?", (x,)).rowcount
    return n


def appareils_du_compte(compte):
    """Les sessions ouvertes de ce compte, la plus recente d'abord. Les
    lignes plus vieilles que la duree maximale d'une session sont oubliees."""
    limite = int(time.time() - SESSION_ABSOLUE.total_seconds())
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM appareils WHERE vue < ?", (limite,))
    return stockage.lire(DB_FILE, "SELECT * FROM appareils WHERE compte = ? ORDER BY vue DESC",
                         (compte,))


@flask_app.before_request
def suivre_l_appareil():
    if session.get("authed") is not True:
        return None
    qui = utilisateur_courant() or NOM_ADMIN
    sid = session.get("sid")
    maintenant = int(time.time())
    if not sid:
        # Premiere requete d'une session (ou session ouverte avant le
        # registre) : elle s'inscrit.
        sid = secrets.token_hex(16)
        session["sid"] = sid
        try:
            with stockage.transaction(DB_FILE) as cx:
                cx.execute("INSERT OR REPLACE INTO appareils (sid, compte, ouverte, vue, ip, agent) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (sid, qui, int(session.get("ouverte") or maintenant), maintenant,
                            _adresse_client() or "", _agent_court()))
        except Exception:                                         # noqa: BLE001
            return None
        _sid_controles[sid] = maintenant
        return None
    if sid in _sid_revoques:
        session.clear()
        return None
    if maintenant - _sid_controles.get(sid, 0) < APPAREIL_CONTROLE:
        return None
    _sid_controles[sid] = maintenant
    try:
        with stockage.transaction(DB_FILE) as cx:
            vu = cx.execute("UPDATE appareils SET vue = ?, ip = ?, agent = ? WHERE sid = ? AND compte = ?",
                            (maintenant, _adresse_client() or "", _agent_court(), sid, qui)).rowcount
    except Exception:                                             # noqa: BLE001
        # Base momentanement illisible : on ne deconnecte personne pour ca.
        return None
    if not vu:
        # La ligne a ete retiree (ou appartient a un autre compte) : cet
        # appareil a ete deconnecte.
        _sid_revoques.add(sid)
        session.clear()
    return None


# Ce que le port des applications accepte de servir. Le proxy et la sonde de
# sante, evidemment -- mais aussi le theme et les polices : une application
# hebergee (le Diagnostic, par exemple) charge "/theme.css" pour heriter de
# l'apparence choisie par l'utilisateur dans le hub. Sans ces deux entrees,
# la feuille repondait 404 sur ce port et la page s'affichait sans un seul
# jeton de couleur. Les deux routes sont deja publiques sur le panneau et ne
# revelent rien : des couleurs et un fichier de police.
ROUTES_APPLICATIONS = {"proxy", "proxy_noslash", "health", "theme_css", "police",
                       "favicon_panneau"}


@flask_app.before_request
def separer_les_origines():
    """Chaque port ne sert que ce qui lui appartient.

    Sans cette garde, ouvrir le second port ne separerait rien : le panneau
    repondrait sur les deux, et les deux origines se vaudraient.
    """
    if not origines_separees():
        return None
    if sur_le_port_des_apps():
        # blog.toi.duckdns.org : tout le chemin appartient a l'application,
        # « / » et « /theme.css » compris.
        nom = application_du_sous_domaine()
        if nom:
            g.sous_domaine = nom
            return _proxy(nom, request.path.lstrip("/"))
        if request.endpoint in ROUTES_APPLICATIONS or request.endpoint is None:
            return None
        # Le panneau ne s'affiche pas ici, et ses API n'y repondent pas : ce
        # port n'est pas de confiance, c'est tout l'interet.
        return Response(_page("Ce n'est pas le panneau",
                              "Cette adresse ne sert que les applications."),
                        404, mimetype="text/html")
    # Les routes /api/* appartiennent au panneau. Elles doivent rester sur
    # son origine, meme si Flask les ferait tomber sur le catch-all des
    # applications (endpoint "proxy"). Sans cette exception, une faute de
    # route ou un appel API depuis le panneau est transforme en redirection
    # vers 9002, ou "api" est ensuite interprete comme un nom d'application.
    if request.path == "/api" or request.path.startswith("/api/"):
        return None

    # Sur le port du panneau : une application demandee ici est renvoyee chez
    # elle. Les favoris et les liens deja partages continuent de marcher.
    if request.endpoint in ("proxy", "proxy_noslash"):
        # Sans adresse utilisable, on ne redirige pas : une cible relative
        # renverrait vers la page elle-meme, et le navigateur tournerait en
        # rond. Le panneau sert alors l'application, comme avant la
        # separation -- degrade, mais joignable.
        if not (domaine_des_applications() or origine_applications()):
            return None
        nom = request.view_args.get("n", "") if request.view_args else ""
        sous = request.view_args.get("sub", "") if request.view_args else ""
        cible = (modele_adresse_application().replace("{nom}", urllib.parse.quote(nom))
                 + sous)
        if request.query_string:
            cible += "?" + request.query_string.decode("latin-1")
        return redirect(cible, 302)
    return None


# --------------------- ce que le navigateur a le droit de faire ---------------
#
# Le panneau n'a jamais pose un seul en-tete de securite sur ses pages. Rien
# n'y obligeait tant qu'il n'y avait pas de faille -- mais c'est precisement
# la qu'ils servent : ils ne reparent rien, ils reduisent ce qu'une faille
# permet. La regle « une donnee ne devient jamais du programme » est la
# premiere barriere ; celle-ci est la seconde, pour le jour ou la premiere
# cede quand meme.
#
# Ce que chacun ferme :
#
#   frame-ancestors / X-Frame-Options   le panneau ne s'encadre plus. Sans
#       eux, un site pouvait le poser dans une iframe invisible sous un
#       bouton anodin et faire cliquer « Supprimer » a l'utilisateur. Le
#       jeton CSRF n'y peut rien : c'est un VRAI clic, dans une VRAIE
#       session.
#   default-src / script-src            un script injecte ne peut plus
#       charger de code depuis ailleurs, ni renvoyer ce qu'il a lu vers un
#       serveur tiers. 'unsafe-inline' reste necessaire : les deux pages
#       portent leur script et leur style en ligne, et les retirer est un
#       autre chantier. Le reste de la regle garde sa valeur malgre lui.
#   base-uri / form-action              une balise <base> injectee
#       detournerait toutes les adresses relatives de la page ; un formulaire
#       reecrit posterait le mot de passe ailleurs.
#   Referrer-Policy                     l'adresse d'une page du panneau ne
#       part plus chez un tiers.
#   nosniff                             un fichier servi comme du texte ne
#       peut plus etre relu comme du script.
#
# LES APPLICATIONS HEBERGEES SONT EXCLUES, sauf nosniff. Leur imposer la
# politique du panneau casserait la premiere qui charge une police, une carte
# ou un script depuis ailleurs -- et ce serait decider a leur place. Le
# panneau protege le panneau.
# Une seule regle leur est imposee, frame-ancestors, qui ne dit rien de ce
# qu'elles chargent : seulement qui peut les encadrer. Voir
# proteger_l_encadrement.
CSP_PANNEAU = ("default-src 'self'; base-uri 'none'; object-src 'none'; "
               "frame-ancestors 'none'; form-action 'self'; "
               "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
               "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")

# Six mois, et sans includeSubDomains. HSTS est un ENGAGEMENT : une fois
# l'en-tete recu, le navigateur refuse le http pendant toute cette duree, et
# rien ne peut le lui faire oublier plus tot. Un an sur une machine
# auto-hebergee ou l'on essaie des choses, c'est long ; les sous-domaines,
# eux, ne nous appartiennent pas forcement. Pose seulement quand le panneau
# SAIT qu'il est servi en TLS -- sinon il se rendrait lui-meme injoignable.
HSTS = "max-age=15552000"


# LA COMPRESSION. La page du panneau pese 300 Ko et repartait telle quelle a
# chaque visite : sans importance sur le reseau local, sensible depuis un
# telephone a l'exterieur. En gzip, elle en fait environ 60.
#
# Ce qui n'est PAS compresse, et pourquoi :
#   - le proxy des applications : leurs reponses sont les leurs, et elles
#     compressent deja si elles le veulent ;
#   - les flux (journaux en direct) : un flux compresse arrive par paquets ;
#   - tout ce qui n'est pas un GET sans parametres. C'est la parade a BREACH,
#     l'attaque qui devine un secret (ici le jeton de la page) a la taille
#     d'une reponse compressee -- elle exige que l'attaquant fasse refleter
#     un texte a lui dans la MEME reponse, donc une requete qu'il compose.
COMPRESSIBLES = {"text/html", "application/json", "text/css",
                 "application/javascript", "text/javascript", "image/svg+xml",
                 "text/plain"}


@flask_app.after_request
def compresser(reponse):
    if (request.method != "GET" or request.query_string
            or requete_d_application()
            or "gzip" not in request.headers.get("Accept-Encoding", "")
            or reponse.status_code != 200 or reponse.direct_passthrough
            or reponse.is_streamed or "Content-Encoding" in reponse.headers
            or reponse.mimetype not in COMPRESSIBLES):
        return reponse
    donnees = reponse.get_data()
    if len(donnees) < 1024:
        return reponse
    reponse.set_data(gzip.compress(donnees, compresslevel=6))
    reponse.headers["Content-Encoding"] = "gzip"
    reponse.headers["Content-Length"] = str(len(reponse.get_data()))
    reponse.vary.add("Accept-Encoding")
    return reponse


# ------------------- une reference pour chaque panne -------------------
#
# « (reponse 500) » ne disait ni quoi faire ni ou chercher. Chaque reponse
# 5xx du panneau porte desormais une REFERENCE courte (X-CodeLab-Ref), que
# la page affiche et que le journal du conteneur ecrit sur la meme ligne
# que la route : « docker logs codelab-app-manager | grep 7F3A2C » retrouve
# la panne. Rien de sensible dans la reference : six caracteres au hasard.

def nouvelle_reference():
    return secrets.token_hex(3).upper()


@flask_app.errorhandler(500)
def erreur_inattendue(e):
    """Une exception non prevue : sa pile part au journal, avec la
    reference ; la page recoit une phrase et la reference, jamais la pile."""
    ref = nouvelle_reference()
    origine = getattr(e, "original_exception", None) or e
    print(f"[codelab] erreur {ref} : {request.method} {request.path}\n"
          + "".join(traceback.format_exception(type(origine), origine, origine.__traceback__)),
          flush=True)
    reponse = jsonify({"error": "Erreur inattendue du panneau.", "ref": ref})
    reponse.status_code = 500
    reponse.headers["X-CodeLab-Ref"] = ref
    return reponse


@flask_app.after_request
def referencer_les_pannes(reponse):
    if (reponse.status_code >= 500 and not requete_d_application()
            and "X-CodeLab-Ref" not in reponse.headers):
        ref = nouvelle_reference()
        reponse.headers["X-CodeLab-Ref"] = ref
        print(f"[codelab] erreur {ref} : {request.method} {request.path} -> "
              f"{reponse.status_code}", flush=True)
    return reponse


@flask_app.after_request
def poser_les_entetes(reponse):
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    if requete_d_application():
        # Toute reponse d'application, page d'erreur et redirection
        # comprises : seul le panneau peut l'encadrer.
        entetes = proteger_l_encadrement(list(reponse.headers.items()))
        reponse.headers.clear()
        for k, v in entetes:
            reponse.headers.add(k, v)
        return reponse
    reponse.headers.setdefault("Content-Security-Policy", CSP_PANNEAU)
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers.setdefault("Referrer-Policy", "no-referrer")
    reponse.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if https_actif():
        reponse.headers.setdefault("Strict-Transport-Security", HSTS)
    return reponse


# ------------------------- la taille d'une requete --------------------------
#
# Il n'y en avait aucune. Un seul POST pouvait donc faire lire au serveur
# autant d'octets qu'il en envoyait, et waitress tient seize fils : seize
# corps sans fin, et le service tombe sans qu'aucun mot de passe ait ete
# tente. Ce n'est pas une faille, c'est une porte laissee ouverte.
#
# DEUX plafonds, parce qu'il y a deux usages qui n'ont rien a voir :
#
#   le panneau       ses propres API. Le plus gros envoi legitime est un logo
#                    de 512 Ko, qui voyage en base64 dans du JSON. 2 Mo
#                    laissent large.
#   les applications ce que le proxy transmet a une application hebergee. On
#                    ne sait pas ce qu'elle recoit -- un fichier deposé, une
#                    archive. Le plafond est donc genereux, et reglable : ce
#                    qu'il borne, c'est la memoire du panneau, qui met le
#                    corps entier en memoire avant de le transmettre.
CORPS_MAX_PANNEAU = 2 * 1024 * 1024
# Une piece jointe (capture, maquette), deposee seule : 10 Mo.
PIECE_MAX = 10 * 1024 * 1024
CORPS_MAX_APPLICATION = int(os.environ.get("APP_MANAGER_CORPS_MAX_MO", "32")) * 1024 * 1024

# Le filet de Flask, pour un corps annonce sans taille (Transfer-Encoding:
# chunked) : la lecture s'arrete d'elle-meme au plafond.
flask_app.config["MAX_CONTENT_LENGTH"] = CORPS_MAX_APPLICATION


@flask_app.before_request
def borner_le_corps():
    """Refuse AVANT de lire : le Content-Length suffit a decider."""
    if requete_d_application():
        return None
    taille = request.content_length
    limite = PIECE_MAX if request.endpoint == "api_piece_deposer" else CORPS_MAX_PANNEAU
    if taille is not None and taille > limite:
        return _trop_gros(limite)
    return None


def _trop_gros(limite):
    message = ("Envoi trop volumineux : %d Mo au maximum."
               % (limite // (1024 * 1024)))
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), 413
    return Response(_page("Envoi trop volumineux", message), 413,
                    mimetype="text/html")


@flask_app.errorhandler(413)
def trop_gros(e):
    """Le meme refus quand c'est Flask qui coupe, et non la garde ci-dessus.

    Sans lui, un depot trop gros rendait la page d'erreur par defaut : du
    HTML la ou la page attend du JSON, donc un message illisible."""
    limite = (CORPS_MAX_APPLICATION if requete_d_application()
              else PIECE_MAX if request.endpoint == "api_piece_deposer"
              else CORPS_MAX_PANNEAU)
    return _trop_gros(limite)


# ------------------------------- jeton CSRF --------------------------------
#
# SameSite=Lax bloque deja l'essentiel : un autre SITE ne peut plus faire
# poster le navigateur vers /api/toggle/<app> avec le cookie de session.
# Ce jeton couvre ce que SameSite ne couvre pas.
#
# Ce qu'il apporte VRAIMENT, et ce qu'il n'apporte pas -- parce que la
# nuance decide de la suite :
#
#   Il protege d'une AUTRE ORIGINE. Un script servi ailleurs (un autre port
#   de cette machine, par exemple) peut declencher une requete vers le
#   panneau avec le cookie, mais la politique d'origine l'empeche de LIRE la
#   reponse d'un GET -- donc d'apprendre le jeton. Sans le jeton, sa requete
#   est refusee.
#
#   Il ne protege PAS d'un script servi sous la MEME origine. Les
#   applications sont servies sous :9001/<nom>/, donc un script hostile qui
#   y tourne lit le jeton comme le panneau le lit. La reponse a ce
#   probleme-la n'est pas un jeton, c'est une origine separee -- un port ou
#   un nom d'hote distinct pour les applications. Le jeton est ce qui rendra
#   cette separation efficace le jour ou elle sera faite : sans lui, changer
#   d'origine n'empecherait pas la requete, seulement sa lecture.
#
# Verifie ici, dans un before_request, et pas route par route : une route
# d'ecriture ajoutee demain est protegee sans que personne y pense. C'est
# l'inverse d'un decorateur qu'on oublie.
JETON_ENTETE = "X-CodeLab-Jeton"

# Les seules routes d'ecriture atteignables SANS session : on ne peut pas
# exiger d'un visiteur un jeton qui vit dans une session qu'il n'a pas
# encore. Elles ont leur propre garde -- mot de passe, code a six chiffres,
# limite de tentatives.
JETON_EXEMPTS = {
    "login_submit", "login_second_facteur",
    "login_passkey_options", "login_passkey",
    "login_inscription_passkey_options", "login_inscription_passkey",
    "login_facteur_passkey_options", "login_facteur_passkey",
    "invitation_accepter",
    # Le proxy transporte les requetes des applications hebergees : leurs
    # formulaires ne connaissent pas le jeton du panneau, et n'ont aucune
    # raison de le connaitre.
    "proxy", "proxy_noslash",
}

JETON_METHODES = {"POST", "PUT", "PATCH", "DELETE"}


def jeton_session():
    """Le jeton de la session courante, cree a la demande.

    Vit dans le cookie de session, donc signe : un client ne peut pas s'en
    fabriquer un, et il disparait avec la session.
    """
    j = session.get("jeton")
    if not j:
        j = secrets.token_urlsafe(32)
        session["jeton"] = j
    return j


@flask_app.before_request
def verifier_jeton():
    if request.method not in JETON_METHODES:
        return None
    if request.endpoint in JETON_EXEMPTS:
        return None
    # Pas de session ouverte : rien a proteger ici, et la route dira
    # elle-meme qu'il faut s'authentifier -- repondre 403 masquerait le vrai
    # motif. Une session ouverte, elle, porte TOUJOURS un jeton : il est pose
    # au moment ou elle s'ouvre (voir ouvrir_session), jamais plus tard. Sans
    # cette garantie, "pas de jeton donc on laisse passer" serait un
    # contournement au lieu d'une exemption.
    if not is_authed():
        return None
    # session.get et pas session["jeton"] : l'invariant "une session ouverte
    # porte un jeton" est vrai, mais s'il cassait un jour, une KeyError
    # rendrait un 500 la ou un 403 est la bonne reponse -- et un 500 sur une
    # ecriture se lit comme une panne du panneau, pas comme un refus.
    attendu = session.get("jeton") or ""
    fourni = request.headers.get(JETON_ENTETE, "")
    if not attendu or not secrets.compare_digest(fourni, attendu):
        return jsonify({"error": "Jeton de sécurité absent ou invalide. "
                                 "Recharge la page."}), 403
    return None


def require_admin(view):
    """Reserve a l'administrateur : declarer, deployer, configurer, gerer les
    comptes. Tout ce qui n'est pas "ouvrir un projet autorise" passe par ici.

    Le controle est fait ICI et non dans l'interface : masquer un bouton ne
    protege rien, la route reste appelable a la main.
    """
    def wrapped(*a, **kw):
        if not is_authed():
            return _refus("Non authentifie.", 401)
        if not est_admin():
            return _refus("Reserve a l'administrateur.", 403)
        # Apres le controle de role, et non avant : le message ne doit rien
        # apprendre a qui n'est deja administrateur. Verifie a CHAQUE
        # requete, pas seulement a la connexion -- une session ouverte dans
        # le salon puis reprise depuis l'exterieur (portable qui se deplace,
        # cookie vole) doit cesser d'administrer en sortant.
        hors = refus_admin_hors_reseau()
        if hors:
            return _refus(hors, 403)
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


# ------------------------------ utilisateurs ------------------------------
#
# Deux espaces, pas deux mots de passe pour la meme personne :
#
#   ADMINISTRATEUR -- le compte du panneau, celui dont le mot de passe est
#     genere au premier demarrage. Il voit tout et peut tout : declarer un
#     projet, le deployer, changer sa visibilite, gerer les comptes,
#     configurer les alertes, ouvrir Dagster.
#   UTILISATEUR -- un compte nomme, cree depuis le panneau, qui n'a acces
#     qu'aux projets qu'on lui a explicitement autorises, et seulement pour
#     les OUVRIR. Ni demarrage, ni arret, ni configuration, ni Dagster --
#     Dagster permet d'executer du code arbitraire, ce qui en fait un droit
#     d'administrateur deguise.
#
# Les mots de passe sont derives, jamais stockes : le fichier vit dans le
# dossier d'etat, a cote de apps.json, et une copie de sauvegarde ne doit pas
# etre une liste de mots de passe.
# ------------------------------- LE COFFRE -------------------------------
#
# Ce qui se regle DEPUIS LE PANNEAU et qui dit quelque chose de quelqu'un --
# les adresses e-mail des comptes et des alertes, le serveur d'envoi et son
# mot de passe, l'adresse de surveillance -- ne vit plus dans des fichiers
# en clair. Chaque valeur est chiffree (Fernet : AES-128-CBC + HMAC-SHA256)
# avec une cle rangee dans credentials.env (APP_MANAGER_CHIFFREMENT_CLE),
# puis :
#
#   - ecrite dans Postgres, table « reglages » de la base du panneau. C'est
#     la qu'elle vit, et elle est sauvegardee avec les autres bases ;
#   - recopiee, chiffree elle aussi, dans la base locale du panneau (table
#     reglages_chiffres de codelab.db). Une alerte doit partir PRECISEMENT
#     quand quelque chose est tombe -- Postgres compris. Le panneau ne depend
#     donc jamais de Postgres pour LIRE ses reglages : il le tient a jour, et
#     s'y reprend si la copie locale est vide.
#
# Sans la cle, ni la base ni la copie ne se lisent, et c'est le but : un dump
# de la base ou une archive de sauvegarde ne livrent aucune adresse. La
# perdre, c'est perdre ces reglages (pas les comptes, pas les applications) :
# elle se sauvegarde avec credentials.env.
#
# Ancien emplacement (un fichier chiffre d'un seul bloc) : lu une fois par
# l'import automatique, puis mis de cote.
COFFRE_FILE = os.path.join(STATE_DIR, "reglages.chiffres")
_cle_chiffrement = ""
_coffre = {"donnees": None}
_coffre_verrou = threading.RLock()


def _fernet():
    from cryptography.fernet import Fernet
    if not _cle_chiffrement:
        raise RuntimeError("cle de chiffrement absente (APP_MANAGER_CHIFFREMENT_CLE)")
    return Fernet(_cle_chiffrement.encode())


def chiffrer(valeur):
    return _fernet().encrypt(json.dumps(valeur, ensure_ascii=False).encode())


def dechiffrer(jeton):
    return json.loads(_fernet().decrypt(bytes(jeton)).decode())


def _coffre_donnees():
    """Le contenu dechiffre, charge une fois. Jamais d'exception.

    Tant que la cle n'est pas chargee, rien n'est mis en cache : une lecture
    trop tot ne doit pas figer un coffre vide qu'une ecriture viendrait
    ensuite poser par-dessus le vrai.
    """
    with _coffre_verrou:
        if _coffre["donnees"] is not None:
            return _coffre["donnees"]
        if not _cle_chiffrement:
            return {}
        donnees = {}
        try:
            lignes = stockage.lire(DB_FILE, "SELECT cle, valeur FROM reglages_chiffres")
        except Exception as e:                                    # noqa: BLE001
            print(f"[app-manager] reglages illisibles ({type(e).__name__}: {e}).",
                  flush=True)
            return {}
        for ligne in lignes:
            try:
                donnees[ligne["cle"]] = dechiffrer(ligne["valeur"])
            except Exception as e:                                # noqa: BLE001
                # Cle changee ou valeur abimee : la ligne reste en base, pour
                # qu'une cle retrouvee la rende lisible ; elle est seulement
                # ignoree ici.
                print(f"[app-manager] reglage « {ligne['cle']} » illisible "
                      f"({type(e).__name__}) : la cle APP_MANAGER_CHIFFREMENT_CLE "
                      f"de credentials.env a-t-elle change ?", flush=True)
        _coffre["donnees"] = donnees
        return _coffre["donnees"]


def _coffre_ecrire(cle, valeur):
    """Ecrit une valeur chiffree en base (None = retire). Leve si l'ecriture
    echoue."""
    with stockage.transaction(DB_FILE) as cx:
        if valeur is None:
            cx.execute("DELETE FROM reglages_chiffres WHERE cle = ?", (cle,))
        else:
            cx.execute("INSERT OR REPLACE INTO reglages_chiffres (cle, valeur) "
                       "VALUES (?, ?)", (cle, chiffrer(valeur)))


def reglage(cle, defaut=None):
    """Une valeur du coffre, dechiffree. Une COPIE : la modifier ne change rien."""
    with _coffre_verrou:
        return copy.deepcopy(_coffre_donnees().get(cle, defaut))


def ecrire_reglage(cle, valeur):
    """Ecrit (None = retire) une valeur. Leve si elle ne s'ecrit pas : c'est
    a l'appelant de dire que rien n'a ete enregistre."""
    with _coffre_verrou:
        if not _cle_chiffrement:
            raise RuntimeError("cle de chiffrement absente (APP_MANAGER_CHIFFREMENT_CLE)")
        donnees = _coffre_donnees()
        try:
            _coffre_ecrire(cle, valeur)
        except Exception as e:                                    # noqa: BLE001
            raise OSError(f"reglage non enregistre : {e}") from e
        if valeur is None:
            donnees.pop(cle, None)
        else:
            donnees[cle] = copy.deepcopy(valeur)
    _pg_deposer(("reglage", cle))


UTILISATEURS_FILE = os.path.join(STATE_DIR, "utilisateurs.json")

# Nom reserve : le compte d'administration n'est pas dans ce fichier, son mot
# de passe vit dans credentials.env. Laisser creer un utilisateur "admin"
# donnerait deux comptes pour un seul nom, et l'un masquerait l'autre.
NOM_ADMIN = "admin"

ROLE_ADMIN = "admin"
ROLE_UTILISATEUR = "utilisateur"

# PBKDF2-HMAC-SHA256. Pas de dependance a ajouter (hashlib est dans la
# bibliotheque standard), et un cout de calcul qui rend une liste de mots de
# passe voles inexploitable en pratique. 600 000 iterations : la valeur que
# recommande l'OWASP pour cet algorithme. L'empreinte porte son nombre
# d'iterations (« pbkdf2-sha256$600000$... ») : le jour ou il monte encore,
# chaque compte est recalcule a sa connexion suivante, sans rien casser. Une
# empreinte sans prefixe date de l'epoque des 200 000.
PBKDF2_ITERATIONS = 600_000
PBKDF2_ANCIEN = 200_000

# Le meme minimum partout ou un mot de passe nait : invitation, inscription,
# reinitialisation, administrateur, changement. Il etait de 8 a la creation
# et de 12 au changement -- le plus faible des deux faisait la regle.
MDP_MIN = 12


CHAMPS_COMPTE = (
    ("sel", "sel", "txt"), ("hash", "empreinte_mdp", "txt"),
    ("email_verifie", "email_verifie", "bool"), ("attente_email", "attente_email", "bool"),
    ("suspendu", "suspendu", "bool"), ("totp", "totp", "txt"), ("cree", "cree", "int"),
    ("sessions_depuis", "sessions_depuis", "int"),
    ("code_mail_suspendu", "code_mail_suspendu", "bool"),
)


def lire_utilisateurs():
    """Les comptes, tables comptes et acces_applications. Jamais d'exception :
    une base illisible ne doit pas empecher l'administrateur de se connecter
    pour la reparer.

    L'adresse e-mail de chaque compte vit dans le COFFRE, chiffree : elle est
    rattachee ici, pour que tout le reste du panneau lise un compte d'un seul
    tenant.
    """
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM comptes ORDER BY nom")
        droits = stockage.lire(DB_FILE, "SELECT compte, application FROM "
                                        "acces_applications ORDER BY rowid")
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] comptes illisibles ({type(e).__name__}: {e}).", flush=True)
        return {}
    projets = {}
    for ligne in droits:
        projets.setdefault(ligne["compte"], []).append(ligne["application"])
    emails = reglage("emails_comptes", {}) or {}
    comptes = {}
    for ligne in lignes:
        compte = stockage.depuis_ligne(ligne, CHAMPS_COMPTE, ligne["options"])
        compte["projets"] = projets.get(ligne["nom"], [])
        if emails.get(ligne["nom"]):
            compte["email"] = emails[ligne["nom"]]
        comptes[ligne["nom"]] = compte
    return comptes


def ecrire_utilisateurs(comptes):
    """Remplace l'ensemble des comptes, en une transaction."""
    # Les adresses partent au coffre, chiffrees ; la table n'en garde
    # aucune. D'abord le coffre : s'il echoue, les comptes ne sont pas
    # touches et rien ne se perd.
    emails = {nom: c["email"] for nom, c in comptes.items()
              if isinstance(c, dict) and c.get("email")}
    ecrire_reglage("emails_comptes", emails or None)
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM acces_applications")
        cx.execute("DELETE FROM comptes")
        for nom, c in comptes.items():
            if not isinstance(c, dict):
                continue
            reste = {k: v for k, v in c.items() if k not in ("email", "projets")}
            ligne, options = stockage.vers_ligne(reste, CHAMPS_COMPTE)
            ligne["nom"] = nom
            stockage.inserer(cx, "comptes", ligne, options)
            # Dans l'ordre donne, sans doublon : l'ordre est celui que
            # l'interface affiche.
            for application in dict.fromkeys(c.get("projets") or []):
                cx.execute("INSERT INTO acces_applications (compte, application) "
                           "VALUES (?, ?)", (nom, application))
    # APRES l'ecriture, jamais avant : le miroir relit les comptes pour les
    # recopier, et prendrait sinon l'etat d'avant la modification.
    _pg_deposer(("utilisateurs", None))


def _pbkdf2(mot_de_passe, sel, iterations):
    return hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(),
                               bytes.fromhex(sel), iterations).hex()


def derive_mot_de_passe(mot_de_passe, sel):
    return f"pbkdf2-sha256${PBKDF2_ITERATIONS}${_pbkdf2(mot_de_passe, sel, PBKDF2_ITERATIONS)}"


def verifie_mot_de_passe(compte, mot_de_passe):
    try:
        attendu = compte["hash"]
        if attendu.startswith("pbkdf2-sha256$"):
            _, iterations, empreinte = attendu.split("$")
            calcule = _pbkdf2(mot_de_passe, compte["sel"], int(iterations))
        else:
            empreinte = attendu
            calcule = _pbkdf2(mot_de_passe, compte["sel"], PBKDF2_ANCIEN)
    except (KeyError, TypeError, ValueError):
        return False
    return secrets.compare_digest(calcule, empreinte)


def empreinte_a_jour(compte):
    return str((compte or {}).get("hash") or "").startswith(f"pbkdf2-sha256${PBKDF2_ITERATIONS}$")


# Le meme calcul qu'un vrai compte, quand le nom n'existe pas : sinon la
# reponse arrivait plus vite pour un nom inconnu, et la duree seule donnait la
# liste des comptes valides.
_COMPTE_FANTOME = {"sel": "00" * 16, "hash": "pbkdf2-sha256$%d$00" % PBKDF2_ITERATIONS}


# ---------------------- adresse mail d'un compte ----------------------
#
# L'adresse relie un compte a quelqu'un de joignable : c'est par elle qu'un
# mot de passe se recupere, et c'est elle qui rend une inscription libre
# defendable -- sans verification, n'importe qui creerait n'importe quoi.
#
# La verification est un code a six chiffres envoye a l'adresse. Volontairement
# le meme geste que le second facteur : la personne connait deja ce parcours.
# Il ne remplace pas le second facteur et n'ouvre aucune session -- il atteste
# seulement que l'adresse existe et qu'elle appartient bien a qui la declare.
EMAIL_MAX = 254                 # la limite de la RFC 5321
CODE_EMAIL_VALIDITE = 900       # 15 minutes : le temps d'aller lire son mail
CODE_EMAIL_ESSAIS = 5           # au-dela, il faut en redemander un autre
CODE_EMAIL_DELAI = 60           # pas plus d'un envoi par minute et par compte


def email_valide(brut):
    """Une adresse plausible, ou "".

    Volontairement permissif : la seule verification qui vaille est d'y
    envoyer un code et d'attendre qu'il revienne. Une expression reguliere
    stricte refuse des adresses parfaitement valides et n'arrete personne.
    """
    adresse = re.sub(r"\s+", "", str(brut or ""))[:EMAIL_MAX]
    return adresse if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", adresse) else ""


def _empreinte_code(code):
    return hashlib.sha256(str(code).encode()).hexdigest()


def poser_code_email(compte):
    """Tire un code a six chiffres, le range, et le renvoie en clair.

    Range sous forme d'empreinte : le code n'a pas a etre lisible dans
    utilisateurs.json, ou il resterait apres coup a cote du nom du compte.
    """
    code = f"{secrets.randbelow(1000000):06d}"
    compte["email_code"] = {"empreinte": _empreinte_code(code),
                            "expire": int(time.time()) + CODE_EMAIL_VALIDITE,
                            "essais": 0,
                            "envoye": int(time.time())}
    return code


def verifier_code_email(compte, code):
    """(ok, message). Consomme un essai, et le code au premier succes."""
    en_cours = compte.get("email_code") or {}
    if not en_cours:
        return False, "Aucun code en attente. Demandes-en un nouveau."
    if int(time.time()) > en_cours.get("expire", 0):
        compte.pop("email_code", None)
        return False, "Ce code a expire. Demandes-en un nouveau."
    if en_cours.get("essais", 0) >= CODE_EMAIL_ESSAIS:
        compte.pop("email_code", None)
        return False, "Trop d'essais. Demandes-en un nouveau."
    en_cours["essais"] = en_cours.get("essais", 0) + 1
    propose = re.sub(r"\D", "", str(code or ""))
    if not (propose and secrets.compare_digest(_empreinte_code(propose),
                                               en_cours.get("empreinte", ""))):
        return False, "Code incorrect."
    compte.pop("email_code", None)
    compte["email_verifie"] = True
    return True, ""


def envoyer_code_email(adresse, nom, code):
    """Envoie le code, ou leve. L'appelant traduit l'echec."""
    cfg, ok = smtp_utilisable()
    if not ok:
        raise RuntimeError("Aucun serveur d'envoi configure.")
    envoyer_mail(cfg, "[CodeLab] verification de ton adresse",
                 f"Code de verification pour le compte « {nom} » : {code}\n\n"
                 f"Il est valable {CODE_EMAIL_VALIDITE // 60} minutes.\n\n"
                 "Si tu n'es pas a l'origine de cette demande, ignore ce "
                 "message : sans ce code, rien ne change.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=[adresse], sensible=True)


# ---------------------- mot de passe oublie ----------------------
#
# Par l'ADRESSE MAIL quand il y en a une de verifiee, et par
# l'administrateur sinon. Les deux chemins existent parce qu'aucun des deux
# ne suffit : sans adresse, personne ne peut prouver a distance qui il est ;
# et si le seul recours etait l'administrateur, il faudrait le deranger pour
# chaque oubli.
#
# UN CODE A PART, et non celui de la verification d'adresse : verifier une
# adresse rend un compte utilisable, reinitialiser un mot de passe le rend
# ACCESSIBLE. Le premier code, consomme, marque l'adresse comme verifiee --
# ce qui n'a aucun sens ici, et surtout melangerait deux pouvoirs tres
# differents dans un seul jeton.
CODE_REINIT_VALIDITE = 900      # 15 minutes, comme la verification d'adresse
CODE_REINIT_ESSAIS = 5


def preparer_code_reinit(compte):
    code = f"{secrets.randbelow(1000000):06d}"
    compte["reinit_code"] = {"empreinte": _empreinte_code(code),
                             "expire": int(time.time()) + CODE_REINIT_VALIDITE,
                             "essais": 0,
                             "envoye": int(time.time())}
    return code


def verifier_code_reinit(compte, code):
    """(ok, message). Consomme un essai, et le code au premier succes."""
    en_cours = compte.get("reinit_code") or {}
    if not en_cours:
        return False, "Aucune réinitialisation en cours. Recommence."
    if int(time.time()) > en_cours.get("expire", 0):
        compte.pop("reinit_code", None)
        return False, "Ce code a expiré. Demandes-en un nouveau."
    if en_cours.get("essais", 0) >= CODE_REINIT_ESSAIS:
        compte.pop("reinit_code", None)
        return False, "Trop d'essais. Demandes-en un nouveau."
    en_cours["essais"] = en_cours.get("essais", 0) + 1
    propose = re.sub(r"\D", "", str(code or ""))
    if not (propose and secrets.compare_digest(_empreinte_code(propose),
                                               en_cours.get("empreinte", ""))):
        return False, "Code incorrect."
    compte.pop("reinit_code", None)
    return True, ""


def envoyer_code_reinit(adresse, nom, code):
    cfg, ok = smtp_utilisable()
    if not ok:
        raise RuntimeError("Aucun serveur d'envoi configure.")
    envoyer_mail(cfg, "[CodeLab] reinitialisation de ton mot de passe",
                 f"Code de reinitialisation pour le compte « {nom} » : {code}\n\n"
                 f"Il est valable {CODE_REINIT_VALIDITE // 60} minutes, et ne sert "
                 f"qu'une fois.\n\n"
                 "Si tu n'es pas a l'origine de cette demande, ignore ce message : "
                 "sans ce code, ton mot de passe ne change pas.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=[adresse], sensible=True)


# ---------------------- second facteur par code envoye ----------------------
#
# LE SECOND FACTEUR SANS APPLICATION A INSTALLER. Scanner un QR code avec une
# application d'authentification, c'etait l'etape ou l'on perdait les gens :
# il faut l'application, comprendre ce qu'elle fait, et la retrouver le jour
# ou l'on change de telephone. Un code a six chiffres recu par mail, au
# moment de se connecter, ne demande rien de tout cela -- et Face ID (une cle
# d'acces) dispense meme du code.
#
# Il reste un VRAI second facteur : le mot de passe est verifie d'abord, et
# le code ne part que vers l'adresse verifiee du compte -- jamais vers une
# destination fournie par la requete.
#
# Ordre de preference : Face ID / Touch ID (rien a saisir), puis le code par
# mail, puis l'application d'authentification. La personne peut toujours
# basculer de l'un a l'autre.
CODE_CONNEXION_VALIDITE = 600   # 10 minutes
CODE_CONNEXION_ESSAIS = 5
MOYEN_TOTP, MOYEN_EMAIL = "totp", "email"
# Face ID / Touch ID en second facteur : apres le mot de passe, la cle
# d'acces de CE compte confirme que c'est bien la personne.
MOYEN_PASSKEY = "passkey"
SECOND_FACTEUR_VALIDITE = 600


def masquer_destination(moyen, valeur):
    """Ce qu'on peut afficher de la destination sans la reveler : la page de
    connexion est publique, et le mot de passe seul ne doit pas suffire a
    lire l'adresse de quelqu'un."""
    valeur = str(valeur or "")
    if moyen == MOYEN_EMAIL and "@" in valeur:
        local, domaine = valeur.split("@", 1)
        return local[:1] + "•••@" + domaine
    return ""


def moyens_second_facteur(compte):
    """Les moyens utilisables pour ce compte, dans l'ordre de preference."""
    moyens = []
    # Apres une reinitialisation du mot de passe PAR MAIL, le mail a deja
    # servi de preuve : le reprendre comme second facteur reviendrait a
    # ouvrir le compte a qui tient la boite. Le code par mail reste suspendu
    # jusqu'a une connexion par l'application ou par Face ID.
    if (compte.get("email") and compte.get("email_verifie")
            and not compte.get("code_mail_suspendu") and smtp_utilisable()[1]):
        moyens.append(MOYEN_EMAIL)
    # Le code par mail d'abord : rien a installer, il arrive la ou l'on
    # regarde deja. L'application d'authentification reste a un clic.
    if compte.get("totp"):
        moyens.append(MOYEN_TOTP)
    return moyens


def facteur_fort(nom, compte):
    """Le compte a-t-il un facteur qui ne passe PAS par la boite mail ?"""
    return bool(compte.get("totp") or passkeys_du_compte(nom))


def poser_code_connexion(compte, moyen):
    code = f"{secrets.randbelow(1000000):06d}"
    compte["code_connexion"] = {"empreinte": _empreinte_code(code),
                                "expire": int(time.time()) + CODE_CONNEXION_VALIDITE,
                                "essais": 0, "moyen": moyen,
                                "envoye": int(time.time())}
    return code


def verifier_code_connexion(compte, code):
    """Consomme un essai, et le code au premier succes. Rend True ou False."""
    en_cours = compte.get("code_connexion") or {}
    if not en_cours:
        return False
    if (int(time.time()) > en_cours.get("expire", 0)
            or en_cours.get("essais", 0) >= CODE_CONNEXION_ESSAIS):
        compte.pop("code_connexion", None)
        return False
    en_cours["essais"] = en_cours.get("essais", 0) + 1
    propose = re.sub(r"\D", "", str(code or ""))
    if propose and secrets.compare_digest(_empreinte_code(propose),
                                          en_cours.get("empreinte", "")):
        compte.pop("code_connexion", None)
        return True
    return False


def envoyer_code_connexion(compte, nom, moyen, code, destinataires=None):
    """Envoie le code par mail, ou leve.

    Le texte pour tous les lecteurs de mail ; une version mise en page, le
    code en grand, pour ceux qui l'affichent.
    """
    validite = CODE_CONNEXION_VALIDITE // 60
    cfg, _ = smtp_utilisable()
    chiffres = "".join(
        f'<span style="display:inline-block;width:40px;height:52px;line-height:52px;'
        f'margin:0 3px;border-radius:10px;background:#f1f3f6;font-size:28px;'
        f'font-weight:700;color:#11151c">{c}</span>' for c in code)
    page = (
        '<div style="background:#eef0f3;padding:32px 12px;font-family:-apple-system,'
        'BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:440px;margin:0 auto;background:#fff;border-radius:20px;'
        'padding:32px 28px;text-align:center;color:#11151c">'
        '<div style="font-size:15px;font-weight:700;letter-spacing:-.01em">CodeLab</div>'
        '<h1 style="font-size:21px;margin:22px 0 6px">Ton code de connexion</h1>'
        f'<p style="margin:0 0 22px;color:#5b6472;font-size:14px">Pour le compte '
        f'<b>{_html.escape(nom)}</b>. Saisis-le sur la page de connexion.</p>'
        f'<div style="white-space:nowrap">{chiffres}</div>'
        f'<p style="margin:22px 0 0;color:#5b6472;font-size:13px">Valable {validite} minutes, '
        'il ne sert qu\'une fois.</p>'
        '<p style="margin:18px 0 0;padding-top:16px;border-top:1px solid #e6e8ec;'
        'color:#8a92a0;font-size:12px">Pas toi ? Ton mot de passe est connu de quelqu\'un '
        "d'autre : change-le, et préviens ton administrateur.</p>"
        '</div></div>')
    envoyer_mail(cfg, f"[CodeLab] {code} est ton code de connexion",
                 f"Code de connexion pour le compte « {nom} » : {code}\n\n"
                 f"Il est valable {validite} minutes, et ne sert qu'une fois.\n\n"
                 "Si tu n'es pas à l'origine de cette connexion, ton mot de passe "
                 "est connu de quelqu'un d'autre : change-le, et préviens ton "
                 "administrateur.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=destinataires or [compte["email"]], sensible=True, html=page)


# ------------------------------ invitations ------------------------------
#
# CREER UN COMPTE, C'EST DONNER UNE ADRESSE MAIL. L'administrateur n'invente
# plus ni nom ni mot de passe a transmettre par un canal qu'il ne maitrise
# pas : la personne recoit un lien, choisit elle-meme son nom et son mot de
# passe, et son adresse est verifiee du meme coup -- c'est en cliquant sur le
# lien recu qu'elle prouve qu'elle la releve.
#
# Le lien porte un jeton de 256 bits. Seule son EMPREINTE est rangee (au
# coffre) : qui lit le coffre ne peut pas s'en servir. Il ne sert qu'une
# fois et expire au bout de sept jours.
INVITATION_VALIDITE = 7 * 86400


def _empreinte_jeton(jeton):
    return hashlib.sha256(str(jeton or "").encode()).hexdigest()


def lire_invitations():
    """Les invitations encore valables, les expirees en moins."""
    maintenant = int(time.time())
    tout = reglage("invitations", {}) or {}
    return {k: v for k, v in tout.items()
            if isinstance(v, dict) and v.get("expire", 0) > maintenant}


def ecrire_invitations(invitations):
    ecrire_reglage("invitations", invitations or None)


def invitation_par_jeton(jeton):
    """(empreinte, invitation) ou (None, None)."""
    if not jeton or len(jeton) > 100:
        return None, None
    empreinte = _empreinte_jeton(jeton)
    invitation = lire_invitations().get(empreinte)
    return (empreinte, invitation) if invitation else (None, None)


def lien_invitation(jeton):
    """L'adresse publique du panneau si elle est connue -- l'administrateur
    invite souvent depuis le reseau local, ou l'adresse qu'il a sous les
    yeux ne s'ouvre pas de l'exterieur."""
    base = adresse_publique() or request.host_url.rstrip("/")
    return f"{base}/invitation/{jeton}"


def envoyer_invitation(adresse, lien):
    """Envoie le lien, ou leve. En direct, jamais par Mailpit : le lien
    ouvre la creation d'un compte."""
    cfg, _ = smtp_utilisable()
    envoyer_mail(cfg, "[CodeLab] ton invitation",
                 "Bonjour,\n\n"
                 "Un compte CodeLab t'attend. Ouvre ce lien pour choisir ton nom "
                 "d'utilisateur et ton mot de passe :\n\n"
                 f"{lien}\n\n"
                 f"Le lien est valable {INVITATION_VALIDITE // 86400} jours et ne sert "
                 "qu'une fois.\n\n"
                 "Si tu ne t'attendais pas à ce message, ignore-le : sans ce lien, "
                 "aucun compte n'est créé.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=[adresse], sensible=True)


def creer_invitation(adresse, projets):
    """Range une invitation neuve et rend (empreinte, jeton). Une invitation
    deja en attente pour la meme adresse est remplacee : son ancien lien ne
    vaut plus rien."""
    invitations = {k: v for k, v in lire_invitations().items()
                   if v.get("email") != adresse}
    jeton = secrets.token_urlsafe(32)
    empreinte = _empreinte_jeton(jeton)
    invitations[empreinte] = {"email": adresse, "projets": projets,
                              "cree": int(time.time()),
                              "expire": int(time.time()) + INVITATION_VALIDITE}
    ecrire_invitations(invitations)
    return empreinte, jeton


# --------------------------- cles d'acces (passkeys) ---------------------------
#
# Une cle d'acces remplace le mot de passe ET le code a six chiffres : le
# telephone (ou l'ordinateur) prouve la possession, et l'empreinte ou le code
# de l'appareil prouve la personne. Rien a retenir, rien a recopier, et rien
# a hameconner -- la cle ne signe que pour le domaine qui l'a enregistree,
# donc un faux site n'en tire rien.
#
# TROIS CONTRAINTES QUE LE NAVIGATEUR IMPOSE, et qu'il faut annoncer plutot
# que subir :
#
#   1. contexte securise. Le navigateur refuse WebAuthn hors HTTPS (sauf sur
#      localhost). Sur http://192.168.1.x:9001, le bouton ne peut pas
#      marcher : le panneau le dit au lieu de l'afficher pour rien ;
#   2. un vrai nom de domaine. Le "rp_id" ne peut pas etre une adresse IP.
#      Il faut donc un nom -- celui par lequel on ouvrira toujours le
#      panneau, puisque les cles sont liees a lui ;
#   3. le meme nom a chaque fois. Une cle enregistree sur codelab.exemple.fr
#      ne fonctionne pas sur 192.168.1.20, et c'est voulu.
#
# La bibliotheque webauthn fait la cryptographie. Ecrire soi-meme la
# verification d'une signature ECDSA et le decodage CBOR d'une attestation,
# c'est exactement le genre de code ou une erreur discrete ne se voit jamais
# -- sauf de celui qui la cherche. Import optionnel, comme le QR code : sans
# elle, les cles d'acces sont simplement indisponibles.
PASSKEYS_FILE = os.path.join(STATE_DIR, "passkeys.json")
PASSKEY_NOM_MAX = 40


def passkeys_disponibles():
    try:
        import webauthn  # noqa: F401
    except ImportError:
        return False
    return True


def _hote_et_schema():
    """(hote sans port, schema, https_annonce_sans_confiance).

    X-Forwarded-Proto n'est croyable que derriere un proxy declare de
    confiance : n'importe quel client peut le poser. On ne s'en sert donc
    que si trust_proxy() est actif -- mais on retient qu'il annoncait HTTPS,
    parce que c'est exactement le cas ou la marche a suivre n'est pas
    « mets du TLS » mais « declare ton proxy ».
    """
    hote = (request.host or "").split(":")[0]
    # Le schema annonce par un proxy suffit ici : la signature d'une cle
    # d'acces porte l'origine que le navigateur a REELLEMENT vue, et ne
    # concordera pas avec un schema invente.
    annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
    if annonce in ("http", "https"):
        return hote, annonce, False
    return hote, request.scheme, False


def passkey_contexte():
    """(rp_id, origine, empechement).

    empechement vaut "" quand tout est reuni. Sinon c'est la phrase a
    afficher : le navigateur, lui, se contenterait d'une erreur illisible.
    """
    hote, schema, https_non_cru = _hote_et_schema()
    origine = f"{schema}://{request.host}"
    if not hote:
        return "", "", "Hote inconnu."
    local = hote in ("localhost", "127.0.0.1", "::1")
    if schema != "https" and not local:
        if https_non_cru:
            return "", "", ("Un proxy annonce HTTPS, mais ce panneau ne le croit pas : "
                            "coche « Proxy de confiance » dans Exposition.")
        return "", "", ("Les clés d'accès exigent une connexion HTTPS : le navigateur "
                        "refuse de les créer en clair. Mets le TLS en place, puis "
                        "reviens ici.")
    # Une adresse IP ne peut pas servir de "relying party id" : la norme
    # exige un nom de domaine. C'est la meme exigence que le certificat.
    if re.fullmatch(r"[0-9.]+|\[[0-9a-fA-F:]+\]", hote) and not local:
        return "", "", ("Les clés d'accès exigent un nom de domaine, pas une adresse IP. "
                        "Ouvre le panneau par son nom (celui du certificat).")
    return hote, origine, ""


def lire_passkeys():
    """Les cles d'acces, par compte : {compte: [cle, ...]}."""
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM cles_acces ORDER BY cree, id")
    except Exception:                                             # noqa: BLE001
        return {}
    tout = {}
    for ligne in lignes:
        cle = {"id": ligne["id"], "cle_publique": ligne["cle_publique"],
               "compteur": ligne["compteur"] or 0}
        for champ in ("nom", "cree", "dernier"):
            if ligne[champ] is not None:
                cle[champ] = ligne[champ]
        tout.setdefault(ligne["compte"], []).append(cle)
    return tout


def ecrire_passkeys(tout):
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM cles_acces")
        for compte, cles in tout.items():
            for k in cles or []:
                cx.execute("INSERT OR REPLACE INTO cles_acces (id, compte, cle_publique, "
                           "compteur, nom, cree, dernier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (k["id"], compte, k.get("cle_publique") or "",
                            int(k.get("compteur") or 0), k.get("nom"),
                            k.get("cree"), k.get("dernier")))
    # Le nombre de cles d'un compte fait partie de ce que le miroir recopie.
    # Depose apres l'ecriture, pour la meme raison qu'au-dessus.
    _pg_deposer(("utilisateurs", None))


def passkeys_du_compte(nom):
    return lire_passkeys().get(nom, [])


def _descripteurs(nom):
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(k["id"]))
            for k in passkeys_du_compte(nom)]


# ------------------------- messages des utilisateurs -------------------------
#
# Un mot laisse depuis le hub : une remarque sur une application, une
# remarque sur le hub lui-meme, ou l'idee d'une application qui manque.
#
# Deux ecritures, comme pour le journal des acces, et pour la meme raison :
# le FICHIER d'abord, qui tient sans base et se lit depuis une session SSH,
# puis la base, qui garde tout et s'interroge en SQL. Un message perdu parce
# que Postgres redemarrait serait un message que personne ne saura jamais
# avoir ete ecrit.
MESSAGES_FILE = os.path.join(STATE_DIR, "messages.jsonl")
MESSAGES_MAX = 5000          # au-dela, les plus anciens non retenus partent
MESSAGE_LONGUEUR_MAX = 2000
# Une minute entre deux messages du meme compte : de quoi corriger une faute
# de frappe, pas de quoi remplir la boite de l'administrateur.
MESSAGE_DELAI = 60
MESSAGE_CIBLES = ("application", "hub", "idee")
# Ce que la page affiche au maximum. Trois cents : au-dela, on ne lit plus
# une boite, on la trie -- et c'est le role de la suppression, pas du
# defilement.
MESSAGES_AFFICHES = 300

_dernier_message = {}


# en_attente : nouveau, rien de decide. a_reflechir : mis de cote pour y
# revenir. accepte / refuse : traite.
STATUTS_MESSAGE = ("en_attente", "a_reflechir", "accepte", "refuse")

CHAMPS_MESSAGE = (
    ("id", "id", "txt"), ("ts", "ts", "int"), ("qui", "qui", "txt"),
    ("cible", "cible", "txt"), ("app", "app", "txt"), ("texte", "texte", "txt"),
    ("statut", "statut", "txt"), ("lu", "lu", "bool"), ("retenu", "retenu", "bool"),
    ("archive", "archive", "bool"),
)


def lire_messages(limite=200):
    """Les plus recents d'abord."""
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM messages ORDER BY ts DESC, rowid DESC "
                                        "LIMIT ?", (int(limite),))
    except Exception:                                             # noqa: BLE001
        return []
    return [stockage.depuis_ligne(l, CHAMPS_MESSAGE, l["options"]) for l in lignes]


def reecrire_messages(messages):
    """Remplace l'ensemble des messages, en une transaction."""
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM messages")
        for m in messages:
            ligne, options = stockage.vers_ligne(m, CHAMPS_MESSAGE)
            stockage.inserer(cx, "messages", ligne, options)
        _oublier_pieces_orphelines(cx)


_INCHANGE = object()


def modifier_message(identifiant, retenu=None, lu=None, statut=None, archive=None, supprimer=False,
                     projet=_INCHANGE):
    """Met à jour l'état d'un message ou le supprime, sous un seul verrou.

    projet : le projet auquel le message est rattache (None le detache).
    """
    with _acces_verrou:
        tous = lire_messages(limite=100000)
        vise = next((m for m in tous if m.get("id") == identifiant), None)
        if vise is None:
            return False
        if supprimer:
            tous = [m for m in tous if m.get("id") != identifiant]
        else:
            if retenu is not None:
                if retenu:
                    vise["retenu"] = True
                else:
                    vise.pop("retenu", None)
            if lu is not None:
                if lu:
                    vise["lu"] = True
                else:
                    vise.pop("lu", None)
            if statut is not None:
                if statut in STATUTS_MESSAGE:
                    vise["statut"] = statut
            if archive is not None:
                if archive:
                    vise["archive"] = True
                else:
                    vise.pop("archive", None)
            if projet is not _INCHANGE:
                if projet:
                    vise["projet"] = projet
                else:
                    vise.pop("projet", None)
        reecrire_messages(tous)
    if supprimer:
        _pg_deposer(("message_supprime", identifiant))
    else:
        _pg_deposer(("message_modifie", vise.copy()))
    return True


def modifier_messages(changements):
    """{id: {cle: valeur}} -- plusieurs messages d'un coup, une ecriture.

    Valeur None : la cle est retiree. Rend le nombre de messages trouves.
    """
    with _acces_verrou:
        tous = lire_messages(limite=100000)
        trouves = 0
        for m in tous:
            ch = changements.get(m.get("id"))
            if ch is None:
                continue
            trouves += 1
            for cle, valeur in ch.items():
                if valeur is None:
                    m.pop(cle, None)
                else:
                    m[cle] = valeur
        if trouves:
            reecrire_messages(tous)
    if trouves:
        _pg_deposer(("messages_modifies", list(changements)))
    return trouves


def marquer_messages_lus():
    """Marque tous les messages comme lus après leur consultation dans le panneau."""
    with _acces_verrou:
        tous = lire_messages(limite=100000)
        changes = 0
        for message in tous:
            if not message.get("lu"):
                message["lu"] = True
                changes += 1
        if changes:
            reecrire_messages(tous)
            _pg_deposer(("messages_lus",))
    return changes


# ------------------------- les pieces jointes -------------------------
#
# Une capture d'ecran, une maquette : ce qu'un message ne sait pas dire en
# mots. Images (PNG, JPEG, WebP, GIF) et PDF, rien d'autre -- reconnus a
# leurs premiers octets, pas au nom ni au type annonce. Pas de SVG : c'est
# du code, qui s'executerait a l'ouverture.
#
# Le fichier se depose d'abord, seul (il depasse le plafond des requetes du
# panneau), puis le message le nomme. Un depot jamais envoye part au bout
# d'un jour.
PIECES_PAR_MESSAGE = 6
PIECES_EN_ATTENTE_MAX = 12
PIECE_ATTENTE_MAX = 86400
PIECE_NOM_MAX = 80
_SIGNATURES_PIECE = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"%PDF-", "application/pdf", "pdf"),
)


def type_de_piece(donnees):
    """(type, extension) reconnus aux premiers octets, ou (None, None)."""
    for signature, type_, ext in _SIGNATURES_PIECE:
        if donnees.startswith(signature):
            return type_, ext
    if donnees[:4] == b"RIFF" and donnees[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None, None


def nom_de_piece(brut, ext):
    """Un nom lisible et inoffensif, qui finit par la bonne extension."""
    nom = urllib.parse.unquote(str(brut or ""))
    nom = re.split(r"[\\/]", nom)[-1]
    nom = "".join(c for c in nom if c.isprintable() and c not in '<>:"|?*').strip(" .")
    base = nom.rsplit(".", 1)[0] if "." in nom else nom
    base = base[:PIECE_NOM_MAX - len(ext) - 1] or "piece"
    return f"{base}.{ext}"


def _oublier_pieces_orphelines(cx):
    """Les pieces d'un message disparu, et les depots jamais envoyes."""
    cx.execute("DELETE FROM pieces WHERE message IS NOT NULL "
               "AND message NOT IN (SELECT id FROM messages)")
    cx.execute("DELETE FROM pieces WHERE message IS NULL AND ts < ?",
               (int(time.time()) - PIECE_ATTENTE_MAX,))


def pieces_en_attente(qui, ids):
    """Les depots de « qui » qui attendent un message, dans l'ordre donne."""
    if not ids:
        return []
    lignes = stockage.lire(DB_FILE, f"SELECT id, nom, type, taille FROM pieces "
                                    f"WHERE message IS NULL AND qui = ? "
                                    f"AND id IN ({','.join('?' * len(ids))})", (qui, *ids))
    par_id = {l["id"]: {"id": l["id"], "nom": l["nom"], "type": l["type"],
                        "taille": l["taille"]} for l in lignes}
    return [par_id[i] for i in ids if i in par_id]


def enregistrer_message(message):
    """Ecrit le message, et n'echoue jamais bruyamment."""
    try:
        with stockage.transaction(DB_FILE) as cx:
            ligne, options = stockage.vers_ligne(message, CHAMPS_MESSAGE)
            stockage.inserer(cx, "messages", ligne, options)
            # Plafond : une boite qui se remplit en boucle ne doit pas remplir
            # le disque. Les plus anciens partent, les retenus restent.
            cx.execute("DELETE FROM messages WHERE rowid IN (SELECT rowid FROM messages "
                       "WHERE COALESCE(retenu, 0) = 0 ORDER BY ts DESC LIMIT -1 OFFSET ?)",
                       (MESSAGES_MAX,))
            # Ses pieces deposees l'attendaient : elles lui appartiennent.
            ids = [p["id"] for p in message.get("pieces") or []]
            if ids:
                cx.execute(f"UPDATE pieces SET message = ? WHERE message IS NULL AND qui = ? "
                           f"AND id IN ({','.join('?' * len(ids))})",
                           (message["id"], message.get("qui") or "", *ids))
            _oublier_pieces_orphelines(cx)
    except Exception:                                             # noqa: BLE001
        return False
    _pg_deposer(("message", message))
    return True


# ------------------------- applications masquees -------------------------
#
# Un compte peut retirer une application de SON hub. Ce n'est ni un droit
# retire, ni une application arretee : le projet continue de tourner, les
# autres comptes le voient, et son adresse reste ouverte a qui la connait --
# c'est du RANGEMENT, pas une protection. La page le dit, pour que personne
# ne croie avoir ferme quelque chose.
#
# Un fichier a part plutot qu'un champ dans le registre des comptes : le
# compte d'administration n'y figure pas, et il a le droit de ranger son hub
# comme les autres.
MASQUEES_FILE = os.path.join(STATE_DIR, "masquees.json")


def lire_masquees():
    """{compte: [applications rangees hors du hub]}."""
    try:
        # Dans l'ordre ou elles ont ete rangees (rowid = ordre d'ecriture).
        lignes = stockage.lire(DB_FILE, "SELECT compte, application FROM masquees "
                                        "ORDER BY compte, rowid")
    except Exception:                                             # noqa: BLE001
        return {}
    d = {}
    for l in lignes:
        d.setdefault(l["compte"], []).append(l["application"])
    return d


def ecrire_masquees(d):
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM masquees")
        for compte, applications in (d or {}).items():
            for application in applications or []:
                if isinstance(application, str):
                    cx.execute("INSERT OR IGNORE INTO masquees (compte, application) "
                               "VALUES (?, ?)", (compte, application))


def masquees_du_compte(qui=None):
    qui = qui or utilisateur_courant() or NOM_ADMIN
    valeurs = lire_masquees().get(qui) or []
    # « Demandes » s'appelle maintenant « Messages » : un rangement deja
    # enregistre sous l'ancien nom vaut pour le nouveau.
    return [MESSAGERIE_NOM if v == MESSAGERIE_ANCIEN_NOM else str(v)
            for v in valeurs if isinstance(v, str)]


# ------------------------- journal des acces -------------------------
#
# Qui s'est connecte, quand, et quelle application il a ouverte. Deux usages,
# et deux seulement : reconnaitre une tentative d'intrusion (des echecs de
# connexion en rafale, une connexion a 4 h du matin), et savoir si un projet
# sert encore a quelqu'un avant de l'arreter.
#
# Un fichier de lignes JSON, ajoutees a la fin. Pas de base : ce sont des
# evenements, jamais modifies, et un fichier texte se lit depuis une session
# SSH le jour ou le panneau ne repond plus. Il est plafonne et tourne comme
# les journaux d'application -- un journal qui remplit le disque transforme
# une curiosite en panne.
ACCES_FILE = os.path.join(STATE_DIR, "acces.jsonl")
JOURNAL_MAX = 50000                 # evenements gardes en local (Postgres garde tout)
_journal_ecrits = [0]
ACCES_LIGNES_LUES = 400             # ce que l'interface affiche au plus
# Une page web, c'est des dizaines de requetes. Une ouverture par personne et
# par application n'est donc notee qu'une fois par quart d'heure : au-dela on
# ne journalise plus une visite, on journalise le HTML.
ACCES_REGROUPEMENT = 900

_dernier_acces = {}
_acces_verrou = threading.Lock()


def _adresse_client():
    """L'adresse du visiteur, selon qu'on est derriere un proxy de confiance."""
    # La meme regle que _client_ip : derriere un proxy de confiance, c'est
    # l'adresse qu'IL a ajoutee (la derniere) qui fait foi -- la premiere est
    # celle que le client a choisie, et le journal noterait un mensonge.
    if trust_proxy():
        derniere = (request.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        if derniere:
            return derniere
    return request.remote_addr or ""


def journaliser(genre, **details):
    """Ajoute un evenement, table journal. N'echoue jamais : journaliser
    n'est pas le travail.

    Une ecriture impossible (disque plein, base verrouillee) ne doit ni
    refuser une connexion ni casser le proxy -- ce serait faire tomber le
    service pour proteger son journal.
    """
    evenement = {"id": secrets.token_hex(12), "ts": int(time.time()), "genre": genre}
    evenement.update(details)
    _pg_deposer(("acces", evenement))
    reste = {k: v for k, v in evenement.items()
             if k not in ("id", "ts", "genre", "qui", "app", "ip")}
    try:
        with stockage.transaction(DB_FILE) as cx:
            cx.execute("INSERT OR IGNORE INTO journal (id, ts, genre, qui, app, ip, details) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (evenement["id"], evenement["ts"], genre,
                        evenement.get("qui"), evenement.get("app"), evenement.get("ip"),
                        json.dumps(reste, ensure_ascii=False)))
            _journal_ecrits[0] += 1
            if _journal_ecrits[0] % 200 == 0:
                # Le plafond, verifie de temps en temps plutot qu'a chaque
                # ligne : les plus anciens evenements partent. Postgres, lui,
                # garde tout.
                cx.execute("DELETE FROM journal WHERE rowid IN (SELECT rowid FROM journal "
                           "ORDER BY ts DESC, rowid DESC LIMIT -1 OFFSET ?)", (JOURNAL_MAX,))
    except Exception:                                             # noqa: BLE001
        pass


def journaliser_ouverture(name):
    """Note qu'une application vient d'etre ouverte, sans noter chaque requete."""
    qui = utilisateur_courant() if is_authed() else ""
    cle = (qui, name)
    maintenant = time.time()
    with _acces_verrou:
        if maintenant - _dernier_acces.get(cle, 0) < ACCES_REGROUPEMENT:
            return
        _dernier_acces[cle] = maintenant
    journaliser("ouverture", qui=qui, app=name, ip=_adresse_client())


# ------------------------- miroir Postgres -------------------------
#
# Le fichier reste la source de verite : il tient sans base, se lit depuis
# une session SSH, et ne fait tomber personne quand le disque se remplit. Il
# est PLAFONNE, donc il oublie -- 1 Mo, environ 8 000 evenements.
#
# Postgres est la memoire longue : la meme chose, sans plafond, interrogeable
# en SQL. Le panneau y ECRIT EN PLUS, jamais A LA PLACE, et jamais sur le
# chemin d'une requete : une base lente ou eteinte ne doit ralentir ni une
# connexion, ni l'ouverture d'une application.
#
# D'ou une file en memoire et un fil dedie. Si la base est absente, la file
# se vide dans le vide et le panneau ne s'en apercoit pas ; quand la base
# revient, le fil rejoue le fichier -- les identifiants d'evenement rendent
# l'operation idempotente.
PG_BASE = os.environ.get("APP_MANAGER_PG_BASE", "codelab")
PG_ACTIF = (os.environ.get("APP_MANAGER_PG", "1") or "").lower() not in ("0", "false", "no")
PG_FILE_MAX = 5000          # au-dela, on jette : la memoire n'est pas un journal
PG_ATTENTE_MIN, PG_ATTENTE_MAX = 5, 300   # secondes entre deux tentatives

_pg_file = queue.Queue(maxsize=PG_FILE_MAX)
_pg_etat = {"pret": False, "erreur": "", "ecrits": 0, "perdus": 0}


def pg_disponible():
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return PG_ACTIF and bool(read_shared_value("POSTGRES_PASSWORD"))


def _pg_reglages():
    return {
        "host": read_shared_value("POSTGRES_HOST") or "codelab-postgres",
        "port": read_shared_value("POSTGRES_PORT") or "5432",
        "user": read_shared_value("POSTGRES_USER") or "codelab",
        "password": read_shared_value("POSTGRES_PASSWORD") or "",
        # Base d'INSTANCE de Dagster : elle sert de point d'entree pour creer
        # la notre. La base "postgres" est supprimee par codelab-postgres.
        "instance": read_shared_value("POSTGRES_DB") or "dagster",
    }


def _pg_connexion(base):
    import psycopg
    r = _pg_reglages()
    return psycopg.connect(host=r["host"], port=r["port"], user=r["user"],
                           password=r["password"], dbname=base,
                           connect_timeout=5, autocommit=True)


def _pg_preparer():
    """Cree la base et les tables si besoin. Leve si la base est injoignable."""
    import psycopg
    r = _pg_reglages()
    # CREATE DATABASE n'accepte pas IF NOT EXISTS : on regarde d'abord.
    with _pg_connexion(r["instance"]) as cx:
        existe = cx.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                            (PG_BASE,)).fetchone()
        if not existe:
            cx.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(
                psycopg.sql.Identifier(PG_BASE)))
    with _pg_connexion(PG_BASE) as cx:
        cx.execute("""
            CREATE TABLE IF NOT EXISTS acces (
              id          TEXT PRIMARY KEY,
              ts          TIMESTAMPTZ NOT NULL,
              genre       TEXT NOT NULL,
              qui         TEXT NOT NULL DEFAULT '',
              application TEXT,
              ip          TEXT,
              role        TEXT,
              moyen       TEXT,
              motif       TEXT,
              action      TEXT
            )""")
        # Le coffre : une valeur chiffree par cle, illisible sans la cle de
        # credentials.env. Cette base est joignable par les projets deployes
        # (meme utilisateur Postgres) : rien n'y est en clair.
        cx.execute("""
            CREATE TABLE IF NOT EXISTS reglages (
              cle    TEXT PRIMARY KEY,
              valeur BYTEA NOT NULL,
              maj    TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        cx.execute("CREATE INDEX IF NOT EXISTS acces_ts ON acces (ts DESC)")
        cx.execute("CREATE INDEX IF NOT EXISTS acces_qui ON acces (qui, ts DESC)")
        cx.execute("CREATE INDEX IF NOT EXISTS acces_app ON acces (application, ts DESC)")
        # Aucun secret ici : ni empreinte de mot de passe, ni sel, ni cle du
        # second facteur, ni cle d'acces. Cette base est joignable par les
        # projets deployes -- elle ne porte que ce qui se lit deja dans le
        # panneau.
        # Les mots laisses depuis le hub. Ils ne contiennent rien de secret --
        # ce que quelqu'un a choisi d'ecrire a son administrateur -- mais ils
        # sont horodates et signes du compte : c'est ce qui permet de
        # repondre, et de voir qu'une meme gene revient.
        cx.execute("""
            CREATE TABLE IF NOT EXISTS messages (
              id          TEXT PRIMARY KEY,
              ts          TIMESTAMPTZ NOT NULL,
              qui         TEXT NOT NULL DEFAULT '',
              cible       TEXT NOT NULL,
              application TEXT,
              texte       TEXT NOT NULL,
              lu          BOOLEAN NOT NULL DEFAULT FALSE
            )""")
        cx.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS lu BOOLEAN NOT NULL DEFAULT FALSE")
        cx.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS statut TEXT NOT NULL DEFAULT 'en_attente'")
        cx.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS archive BOOLEAN NOT NULL DEFAULT FALSE")
        cx.execute("CREATE INDEX IF NOT EXISTS messages_ts ON messages (ts DESC)")
        cx.execute("""
            CREATE TABLE IF NOT EXISTS utilisateurs (
              nom            TEXT PRIMARY KEY,
              email          TEXT NOT NULL DEFAULT '',
              email_verifie  BOOLEAN NOT NULL DEFAULT FALSE,
              attente_email  BOOLEAN NOT NULL DEFAULT FALSE,
              second_facteur BOOLEAN NOT NULL DEFAULT FALSE,
              cles_acces     INTEGER NOT NULL DEFAULT 0,
              projets        TEXT[]  NOT NULL DEFAULT '{}',
              cree           TIMESTAMPTZ,
              supprime       TIMESTAMPTZ,
              maj            TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")


def _pg_deposer(tache):
    """Met une tache dans la file, sans jamais attendre.

    Une file pleine veut dire que la base ne suit pas : on jette, et on le
    compte. Bloquer ici arreterait une connexion pour un journal.
    """
    if not PG_ACTIF:
        return
    try:
        _pg_file.put_nowait(tache)
    except queue.Full:
        _pg_etat["perdus"] += 1


def _pg_ecrire_acces(cx, evenements):
    lignes = [(
        e.get("id") or hashlib.sha256(
            json.dumps(e, sort_keys=True).encode()).hexdigest()[:24],
        datetime.datetime.fromtimestamp(e.get("ts") or 0, datetime.timezone.utc),
        e.get("genre") or "", e.get("qui") or "", e.get("app"), e.get("ip"),
        e.get("role"), e.get("moyen"), e.get("motif"), e.get("action"),
    ) for e in evenements]
    if not lignes:
        return
    with cx.cursor() as cur:
        cur.executemany(
            """INSERT INTO acces (id, ts, genre, qui, application, ip, role,
                                  moyen, motif, action)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO NOTHING""", lignes)
    _pg_etat["ecrits"] += len(lignes)


def _pg_ecrire_messages(cx, messages):
    lignes = [(m.get("id") or secrets.token_hex(12),
               datetime.datetime.fromtimestamp(m.get("ts") or 0, datetime.timezone.utc),
               m.get("qui") or "", m.get("cible") or "", m.get("app"),
               m.get("texte") or "", bool(m.get("lu")),
               m.get("statut") or "en_attente", bool(m.get("archive"))) for m in messages]
    if not lignes:
        return
    with cx.cursor() as cur:
        cur.executemany(
            """INSERT INTO messages (id, ts, qui, cible, application, texte, lu, statut, archive)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO NOTHING""", lignes)


def _pg_ecrire_utilisateurs(cx):
    """Recopie le registre des comptes, et marque les disparus.

    Une ligne n'est jamais supprimee : le journal des acces la designe par
    son nom, et un historique qui perd ses acteurs ne s'interprete plus.
    """
    comptes = lire_utilisateurs()
    cles = lire_passkeys()
    with cx.cursor() as cur:
        for nom, c in comptes.items():
            cur.execute("""
                INSERT INTO utilisateurs (nom, email, email_verifie, attente_email,
                                          second_facteur, cles_acces, projets, cree,
                                          supprime, maj)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,now())
                ON CONFLICT (nom) DO UPDATE SET
                  email=EXCLUDED.email, email_verifie=EXCLUDED.email_verifie,
                  attente_email=EXCLUDED.attente_email,
                  second_facteur=EXCLUDED.second_facteur,
                  cles_acces=EXCLUDED.cles_acces, projets=EXCLUDED.projets,
                  cree=EXCLUDED.cree, supprime=NULL, maj=now()
            """, (nom, "", bool(c.get("email_verifie")),
                  bool(c.get("attente_email")), bool(c.get("totp")),
                  len(cles.get(nom, [])), sorted(c.get("projets", [])),
                  datetime.datetime.fromtimestamp(c.get("cree") or 0,
                                                  datetime.timezone.utc)))
        cur.execute("""UPDATE utilisateurs SET supprime = now(), maj = now()
                        WHERE supprime IS NULL AND NOT (nom = ANY(%s))""",
                    (list(comptes),))
        # Les adresses ne sont plus recopiees ici : elles vivent chiffrees
        # dans « reglages ». Celles qu'une version precedente y avait
        # ecrites en clair sont effacees.
        cur.execute("UPDATE utilisateurs SET email = '' WHERE email <> ''")


def _pg_ecrire_reglage(cx, cle):
    valeur = reglage(cle)
    with cx.cursor() as cur:
        if valeur is None:
            cur.execute("DELETE FROM reglages WHERE cle = %s", (cle,))
        else:
            cur.execute("""INSERT INTO reglages (cle, valeur, maj) VALUES (%s, %s, now())
                           ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur,
                                                           maj = now()""",
                        (cle, chiffrer(valeur)))


def _pg_synchroniser_reglages(cx):
    """La copie locale et la base, remises d'accord.

    Copie locale vide (dossier d'etat restaure, disque remplace) et base
    remplie : la base fait autorite, et la copie est reconstruite depuis elle.
    Sinon, la copie locale est la plus recente -- chaque ecriture y passe
    d'abord -- et la base la rattrape.
    """
    if not _cle_chiffrement:
        return
    with cx.cursor() as cur:
        cur.execute("SELECT cle, valeur FROM reglages")
        lignes = cur.fetchall()
    if not _coffre_donnees() and lignes:
        with _coffre_verrou:
            donnees = _coffre_donnees()
            for cle, valeur in lignes:
                try:
                    donnees[cle] = dechiffrer(valeur)
                    _coffre_ecrire(cle, donnees[cle])
                except Exception as e:                            # noqa: BLE001
                    print(f"[app-manager] reglage « {cle} » illisible en base "
                          f"({type(e).__name__}) : ignore.", flush=True)
        print(f"[app-manager] reglages repris de la base ({len(lignes)}).", flush=True)
        return
    locales = set(_coffre_donnees())
    for cle in locales:
        _pg_ecrire_reglage(cx, cle)
    with cx.cursor() as cur:
        for cle, _ in lignes:
            if cle not in locales:
                cur.execute("DELETE FROM reglages WHERE cle = %s", (cle,))


def _pg_rattraper(cx):
    """Rejoue le journal local en entier : ce qui manque entre, le reste glisse.

    Appele au demarrage et apres chaque reconnexion. Le journal local est
    plafonne a 50 000 evenements -- et ON CONFLICT DO
    NOTHING rend l'operation sans consequence quand tout est deja la.
    """
    _pg_ecrire_acces(cx, lire_acces(limite=100000))
    _pg_ecrire_messages(cx, lire_messages(limite=100000))
    _pg_ecrire_utilisateurs(cx)
    _pg_synchroniser_reglages(cx)


def _pg_boucle():
    """Le fil qui ecrit. Il ne remonte jamais une erreur a l'appelant."""
    attente = PG_ATTENTE_MIN
    while True:
        if not pg_disponible():
            time.sleep(PG_ATTENTE_MIN)
            continue
        try:
            _pg_preparer()
            with _pg_connexion(PG_BASE) as cx:
                _pg_rattraper(cx)
                _pg_etat["pret"] = True
                _pg_etat["erreur"] = ""
                attente = PG_ATTENTE_MIN
                while True:
                    genre, charge = _pg_file.get()
                    if genre == "acces":
                        _pg_ecrire_acces(cx, [charge])
                    elif genre == "message":
                        _pg_ecrire_messages(cx, [charge])
                    elif genre == "message_supprime":
                        with cx.cursor() as cur:
                            cur.execute("DELETE FROM messages WHERE id = %s",
                                        (charge,))
                    elif genre == "message_modifie":
                        with cx.cursor() as cur:
                            cur.execute("DELETE FROM messages WHERE id = %s",
                                        (charge.get("id"),))
                        _pg_ecrire_messages(cx, [charge])
                    elif genre == "messages_lus":
                        with cx.cursor() as cur:
                            cur.execute("UPDATE messages SET lu = TRUE WHERE lu = FALSE")
                    elif genre == "utilisateurs":
                        _pg_ecrire_utilisateurs(cx)
                    elif genre == "reglage":
                        _pg_ecrire_reglage(cx, charge)
        except Exception as e:
            # Base eteinte, mot de passe change, disque plein cote serveur :
            # on note, on attend, on recommence. Le panneau, lui, continue.
            _pg_etat["pret"] = False
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
            time.sleep(attente)
            attente = min(attente * 2, PG_ATTENTE_MAX)


def demarrer_miroir_pg():
    if not PG_ACTIF:
        return
    threading.Thread(target=_pg_boucle, daemon=True).start()


def pg_lire_acces(limite=ACCES_LIGNES_LUES, app=None, qui=None):
    """L'historique long, lu dans Postgres. Leve si la base ne repond pas."""
    conditions, valeurs = [], []
    if app is not None:
        conditions.append("application = %s")
        valeurs.append(app)
    if qui is not None:
        conditions.append("qui = %s")
        valeurs.append(qui)
    ou = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    valeurs.append(int(limite))
    with _pg_connexion(PG_BASE) as cx:
        lignes = cx.execute(
            "SELECT id, ts, genre, qui, application, ip, role, moyen, motif, action"
            " FROM acces" + ou + " ORDER BY ts DESC, id DESC LIMIT %s",
            valeurs).fetchall()
    evenements = []
    for (id_, ts, genre, qui_, app_, ip, role, moyen, motif, action) in lignes:
        e = {"id": id_, "ts": int(ts.timestamp()), "genre": genre, "qui": qui_ or ""}
        for cle, valeur in (("app", app_), ("ip", ip), ("role", role),
                            ("moyen", moyen), ("motif", motif), ("action", action)):
            if valeur is not None:
                e[cle] = valeur
        evenements.append(e)
    return evenements


# ------------------------- les analyses -------------------------
#
# Le journal local est plafonne (50 000 evenements) : il OUBLIE. Tant qu'il servait a afficher les
# quarante dernieres lignes, cela n'avait aucune importance. Des qu'on compte
# -- « combien de fois ce projet a-t-il ete ouvert cette annee », « quels
# jours cette personne travaille » -- la question change : un total calcule
# sur une fenetre glissante ne veut rien dire, et il diminue
# tout seul a mesure que le journal tourne.
#
# Les analyses lisent donc Postgres, qui garde tout. Le fichier reste le
# repli : base eteinte, on repond quand meme, sur ce qu'on a, et la reponse
# dit d'ou elle vient.

# Le fuseau dans lequel une journee commence. Une carte par jour calculee en
# UTC coupe la soiree en deux pour qui vit a l'est de Greenwich : ce qui est
# fait a 23 h a Paris compterait pour le lendemain.
FUSEAU_JOURNAL = os.environ.get("TZ") or "UTC"


def _fuseau():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(FUSEAU_JOURNAL)
    except Exception:                                             # noqa: BLE001
        # Base de fuseaux absente de l'image, nom inconnu : UTC plutot que
        # rien. Une carte decalee d'une heure reste lisible ; une page en
        # erreur, non.
        return datetime.timezone.utc


def _jour(ts):
    return datetime.datetime.fromtimestamp(ts or 0, _fuseau()).date().isoformat()


def annee_courante():
    return datetime.datetime.now(_fuseau()).year


def pg_carte_activite(annee=None, qui=None, app=None):
    """Une ligne par jour de l'annee : ouvertures, applications, et le detail
    par personne et par application (voir detail_du_jour).

    Le comptage se fait DANS la base : ramener une annee d'evenements pour
    les additionner ici marcherait aujourd'hui et s'ecroulerait le jour ou
    l'historique compte pour de bon. Une seule requete, groupee par jour, par
    application et par personne : quelques lignes par jour, d'ou se deduisent
    les totaux et les deux details.

    UNE ANNEE CIVILE, ET PLUS UNE FENETRE GLISSANTE. La fenetre de 366 jours
    repondait a « ces douze derniers mois » et a rien d'autre : impossible de
    revenir sur l'annee passee, et les mois se decalaient d'un cran chaque
    jour -- deux cartes prises a deux dates ne se comparaient pas. Une annee
    de janvier a decembre est fixe, se compare, et se choisit.
    """
    annee = annee_courante() if annee is None else int(annee)
    conditions = ["genre = 'ouverture'",
                  "extract(year from ts AT TIME ZONE %s) = %s"]
    valeurs = [FUSEAU_JOURNAL, annee]
    if qui is not None:
        conditions.append("qui = %s")
        valeurs.append(qui)
    if app is not None:
        conditions.append("application = %s")
        valeurs.append(app)
    valeurs.insert(0, FUSEAU_JOURNAL)
    with _pg_connexion(PG_BASE) as cx:
        lignes = cx.execute(
            "SELECT (ts AT TIME ZONE %s)::date AS jour, COALESCE(application, ''),"
            "       COALESCE(qui, ''), count(*)"
            "  FROM acces WHERE " + " AND ".join(conditions) +
            " GROUP BY 1, 2, 3 ORDER BY 1", valeurs).fetchall()
    par_jour = {}
    for j, application, personne, n in lignes:
        par_jour.setdefault(j.isoformat(), {})[(application, personne)] = int(n)
    return [detail_du_jour(j, v) for j, v in sorted(par_jour.items())]


def detail_du_jour(jour, comptes):
    """La case d'un jour, telle que la carte la survole. comptes associe a
    chaque couple (application, personne) son nombre d'ouvertures.

    Rend le total, le nombre d'applications distinctes, et deux details en
    chiffres, du plus grand au plus petit : par personne et par application.

    Vecu : « Ajoute la possibilite d'avoir du detail pour chaque case »,
    puis « je veux le detail par utilisateur, en chiffre et pas en
    graphique ». La case ne disait que « 12 ouvertures -- 3 applications » :
    qui, et ou, il fallait le chercher dans le journal. Un visiteur anonyme
    (qui vide) garde son nom vide : la page l'ecrit « visiteur anonyme ».
    """
    par_app, par_qui = {}, {}
    for (application, personne), n in comptes.items():
        par_app[application] = par_app.get(application, 0) + n
        par_qui[personne] = par_qui.get(personne, 0) + n

    def classer(table, cle):
        return [{cle: k, "ouvertures": n}
                for k, n in sorted(table.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"jour": jour, "ouvertures": sum(comptes.values()), "apps": len(par_app),
            "par_app": classer(par_app, "app"), "par_qui": classer(par_qui, "qui")}


def carte_activite(annee=None, qui=None, app=None):
    """Le meme comptage, sur le fichier. Repli quand la base ne repond pas."""
    annee = annee_courante() if annee is None else int(annee)
    debut, fin = f"{annee}-01-01", f"{annee}-12-31"
    par_jour = {}
    for e in lire_acces(limite=100000):
        if e.get("genre") != "ouverture":
            continue
        if not debut <= _jour(e.get("ts")) <= fin:
            continue
        if qui is not None and (e.get("qui") or "") != qui:
            continue
        if app is not None and (e.get("app") or "") != app:
            continue
        comptes = par_jour.setdefault(_jour(e.get("ts")), {})
        cle = (e.get("app") or "", e.get("qui") or "")
        comptes[cle] = comptes.get(cle, 0) + 1
    return [detail_du_jour(j, v) for j, v in sorted(par_jour.items())]


def pg_annees_activite():
    """La premiere et la derniere annee ou quelque chose a ete ouvert."""
    with _pg_connexion(PG_BASE) as cx:
        borne = cx.execute(
            "SELECT min(extract(year from ts AT TIME ZONE %s))::int,"
            "       max(extract(year from ts AT TIME ZONE %s))::int"
            "  FROM acces WHERE genre = 'ouverture'",
            [FUSEAU_JOURNAL, FUSEAU_JOURNAL]).fetchone()
    return (borne[0], borne[1]) if borne else (None, None)


def annees_activite():
    """Les memes bornes, sur le fichier."""
    basse = haute = None
    for e in lire_acces(limite=100000):
        if e.get("genre") != "ouverture":
            continue
        a = int(_jour(e.get("ts"))[:4])
        basse = a if basse is None or a < basse else basse
        haute = a if haute is None or a > haute else haute
    return basse, haute


def annees_proposees(basse, haute):
    """La liste offerte au choix : de la premiere utilisation a aujourd'hui.

    L'annee en cours en fait TOUJOURS partie, meme sans une seule ouverture :
    une installation neuve doit pouvoir regarder sa carte, et un selecteur
    vide n'est pas une reponse. Sans bornes connues, il reste cette
    seule annee.
    """
    courante = annee_courante()
    basse = min(basse or courante, courante)
    haute = max(haute or courante, courante)
    return list(range(int(basse), int(haute) + 1))


def pg_resume_acces():
    """Le meme resume que resume_acces(), calcule sur TOUT l'historique."""
    comptes, apps_ = {}, {}
    with _pg_connexion(PG_BASE) as cx:
        for qui, connexions, ouvertures, derniere in cx.execute("""
                SELECT qui,
                       count(*) FILTER (WHERE genre = 'connexion'),
                       count(*) FILTER (WHERE genre = 'ouverture'),
                       max(ts)  FILTER (WHERE genre = 'connexion')
                  FROM acces WHERE genre IN ('connexion','ouverture')
                 GROUP BY qui""").fetchall():
            comptes[qui or ""] = {
                "connexions": int(connexions), "ouvertures": int(ouvertures),
                "derniere": int(derniere.timestamp()) if derniere else 0}
        for application, ouvertures, derniere in cx.execute("""
                SELECT application, count(*), max(ts) FROM acces
                 WHERE genre = 'ouverture' GROUP BY application""").fetchall():
            apps_[application or ""] = {"ouvertures": int(ouvertures),
                                        "derniere": int(derniere.timestamp()) if derniere else 0,
                                        "qui": {}}
        for application, qui, n in cx.execute("""
                SELECT application, qui, count(*) FROM acces
                 WHERE genre = 'ouverture' GROUP BY application, qui""").fetchall():
            apps_.setdefault(application or "", {"ouvertures": 0, "derniere": 0,
                                                 "qui": {}})["qui"][qui or ""] = int(n)
        (echecs,) = cx.execute(
            "SELECT count(*) FROM acces WHERE genre = 'echec'").fetchone()
    return {"comptes": comptes, "apps": apps_, "echecs": int(echecs)}


def lire_acces(limite=ACCES_LIGNES_LUES, app=None, qui=None):
    """Les evenements les plus recents d'abord, table journal."""
    conditions, valeurs = [], []
    if app is not None:
        conditions.append("app = ?")
        valeurs.append(app)
    if qui is not None:
        conditions.append("COALESCE(qui, '') = ?")
        valeurs.append(qui)
    ou = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    try:
        lignes = stockage.lire(DB_FILE, f"SELECT * FROM journal{ou} "
                                        f"ORDER BY ts DESC, rowid DESC LIMIT ?",
                               (*valeurs, int(limite)))
    except Exception:                                             # noqa: BLE001
        return []
    evenements = []
    for l in lignes:
        e = {"id": l["id"], "ts": l["ts"], "genre": l["genre"]}
        for champ in ("qui", "app", "ip"):
            if l[champ] is not None:
                e[champ] = l[champ]
        try:
            e.update(json.loads(l["details"] or "{}"))
        except ValueError:
            pass
        evenements.append(e)
    return evenements


def resume_acces():
    """Par compte et par application : derniere fois, et combien de fois.

    C'est ce qu'on veut savoir en une ligne -- « personne n'a ouvert ce
    projet depuis trois semaines » -- sans derouler le journal entier.
    """
    comptes, apps_, echecs = {}, {}, 0
    for e in lire_acces(limite=100000):
        qui = e.get("qui") or ""
        genre = e.get("genre")
        if genre == "connexion":
            c = comptes.setdefault(qui, {"connexions": 0, "derniere": 0, "ouvertures": 0})
            c["connexions"] += 1
            c["derniere"] = max(c["derniere"], e.get("ts", 0))
        elif genre == "echec":
            echecs += 1
        elif genre == "ouverture":
            c = comptes.setdefault(qui, {"connexions": 0, "derniere": 0, "ouvertures": 0})
            c["ouvertures"] += 1
            a = apps_.setdefault(e.get("app") or "", {"ouvertures": 0, "derniere": 0, "qui": {}})
            a["ouvertures"] += 1
            a["derniere"] = max(a["derniere"], e.get("ts", 0))
            a["qui"][qui] = a["qui"].get(qui, 0) + 1
    return {"comptes": comptes, "apps": apps_, "echecs": echecs}


def nom_utilisateur_valide(brut):
    """Minuscules, chiffres, tiret et souligne. Le nom sert d'identifiant de
    fichier JSON et s'affiche partout : autant le contraindre a l'entree
    plutot que d'echapper a chaque affichage."""
    nom = (brut or "").strip().lower()
    return nom if re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", nom or "") else ""


def role_courant():
    """Le role de la session, ou None si elle n'est pas authentifiee.

    Une session ouverte AVANT l'arrivee des comptes utilisateurs n'a pas de
    role enregistre : elle ne peut venir que du panneau d'administration,
    seule facon de se connecter a l'epoque. On la traite donc comme telle,
    plutot que de deconnecter tout le monde a la mise a jour.
    """
    if session.get("authed") is not True:
        return None
    return session.get("role") or ROLE_ADMIN


def est_admin():
    return role_courant() == ROLE_ADMIN


def utilisateur_courant():
    return session.get("utilisateur") or ""


def compte_suspendu(compte):
    """Un compte suspendu existe encore, mais n'ouvre plus rien.

    POURQUOI PAS « SUPPRIMER ». Un depart, un doute, un appareil perdu : on
    veut fermer l'acces TOUT DE SUITE et decider plus tard. Supprimer efface
    les projets autorises, l'adresse, les cles d'acces -- et il faut tout
    ressaisir au retour. Suspendre ferme la porte en gardant la piece en
    l'etat, et se defait d'un clic.
    """
    return bool((compte or {}).get("suspendu"))


def projets_autorises():
    """Les projets que la session peut ouvrir. None = tous (administrateur)."""
    if est_admin():
        return None
    compte = lire_utilisateurs().get(utilisateur_courant())
    # Suspendu : plus aucun projet, meme ceux qui restent coches dans sa
    # fiche. On ne retire pas ses droits, on ferme la porte -- les retrouver
    # a la reactivation est tout l'interet.
    if not compte or compte_suspendu(compte):
        return set()
    # Une application reservee ne compte jamais, meme cochee par une version
    # plus ancienne du panneau : le droit ecrit dans la fiche ne vaut rien.
    return {p for p in compte.get("projets", []) if not est_app_par_defaut(p)}


def peut_voir(name):
    """La session a-t-elle le droit d'ouvrir cette application ?

    Une application publique est ouverte a tous, y compris a un visiteur non
    connecte -- c'est le sens de "publique", et c'est ce qui permet de
    partager un projet par un simple lien. Le controle par compte ne concerne
    donc que les applications privees.

    Sauf les applications par defaut : elles sont RESERVEES a
    l'administrateur, et pas seulement « privees, donc autorisables ». Ni
    l'interface ni une faute de manipulation ne peuvent les accorder a un
    compte -- api_app_acces_modifier les refuse aussi.
    """
    if est_app_par_defaut(name):
        return est_admin()
    autorises = projets_autorises()
    return autorises is None or name in autorises


# --------------------------- persistance ---------------------------

# Cache d'apps.json, invalide par la signature du fichier (date de
# modification en nanosecondes + taille).
#
# load() est appele partout : a chaque tour du moniteur, a chaque
# rafraichissement du tableau de bord, et surtout DANS LE PROXY -- donc une
# lecture disque et un parsing JSON pour chaque requete servie a chaque
# application, images et appels d'API compris. Un stat() remplace tout cela
# quand le fichier n'a pas bouge.
#
# La signature plutot que la seule date : deux ecritures rapprochees peuvent
# theoriquement partager un horodatage, la taille les departage dans la
# plupart des cas. Et save() pose le cache directement, ce qui rend le
# rechargement inutile apres nos propres ecritures.
_apps_cache = {"signature": None, "apps": {}}


CHAMPS_APPLICATION = (
    ("titre", "titre", "txt"), ("description", "description", "txt"),
    ("path", "dossier", "txt"), ("command", "commande", "txt"),
    ("build_command", "commande_build", "txt"), ("port", "port", "int"),
    ("enabled", "actif", "bool"), ("visibility", "visibilite", "txt"),
    ("categorie", "categorie", "txt"), ("max_memory_mb", "memoire_max_mo", "int"),
)


def load():
    """Les applications declarees, table applications.

    Le cache est indexe par un compteur que chaque ecriture fait avancer :
    load() est appele partout -- a chaque tour du moniteur et, surtout, a
    chaque requete servie par le proxy. Une requete d'une ligne sur la table
    meta remplace la relecture de toute la table quand rien n'a bouge.
    """
    try:
        gen = stockage.generation(DB_FILE, "applications")
        if _apps_cache["signature"] != (DB_FILE, gen):
            lignes = stockage.lire(DB_FILE, "SELECT * FROM applications ORDER BY rowid")
            _apps_cache["apps"] = {
                ligne["nom"]: stockage.depuis_ligne(ligne, CHAMPS_APPLICATION,
                                                    ligne["options"])
                for ligne in lignes}
            _apps_cache["signature"] = (DB_FILE, gen)
    except Exception as e:                                        # noqa: BLE001
        # Base momentanement illisible : on garde ce qu'on avait. Renvoyer
        # un registre vide ici arreterait toutes les applications au tour de
        # moniteur suivant.
        print(f"[app-manager] applications illisibles ({type(e).__name__}: {e}).",
              flush=True)
    # Copie : les appelants modifient librement ce qu'ils recoivent avant de
    # le repasser a save(), et une reference partagee ferait apparaitre ces
    # modifications avant meme l'ecriture -- y compris si elle echoue.
    return {nom: dict(a) if isinstance(a, dict) else a
            for nom, a in _apps_cache["apps"].items()}


def save(apps):
    """Remplace l'ensemble des applications, en une transaction."""
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM applications")
        for nom, a in apps.items():
            if not isinstance(a, dict):
                continue
            ligne, options = stockage.vers_ligne(a, CHAMPS_APPLICATION)
            ligne["nom"] = nom
            stockage.inserer(cx, "applications", ligne, options)
        stockage.avancer_generation(cx, "applications")
    _apps_cache["signature"] = None   # relu au prochain load()


def next_port(apps):
    """Choisit un port libre dans le registre ET sur la machine.

    Avant, un service externe pouvait occuper 9102 sans exister dans
    apps.json : la creation de l'application reussissait, puis son premier
    demarrage echouait. Le conflit doit etre evite au moment de l'attribution,
    pas decouvert apres coup.
    """
    used = {a["port"] for a in apps.values()}
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p in used:
            continue
        if _ecouteurs_port(p):
            continue
        return p
    return None


# ------------------------- abandon des privileges ----------------------------
#
# Le service tourne en root : il en a besoin au demarrage pour poser le groupe
# et le setgid sur /workspace. Mais tout ce qu'il LANCE -- application
# deployee comme commande de build -- n'en a aucun besoin, et l'heritait
# pourtant.
#
# Ce que cela ouvrait : credentials.env est monte dans ce conteneur en
# 0600 root. Un processus enfant lance en root pouvait donc lire le mot de
# passe Postgres, le mot de passe du panneau et la cle de signature des
# sessions. Le vecteur realiste n'est pas l'application elle-meme mais son
# build : "npm ci" execute les scripts postinstall de toutes les dependances
# transitives, et une seule compromise dans la chaine suffit.
#
# Apres bascule sur l'uid 1001, ce fichier redevient illisible pour eux.
RUN_AS_UID = int(os.environ.get("APP_MANAGER_RUN_AS_UID", "1001"))
RUN_AS_GID = int(os.environ.get("APP_MANAGER_RUN_AS_GID", "2000"))

# --- un uid par application -------------------------------------------------
#
# L'uid 1001 partage protegeait les applications DU PANNEAU (credentials.env
# redevient illisible), mais pas les unes DES AUTRES : meme uid, donc chacune
# pouvait tuer les process d'une autre, et surtout lire son
# /proc/<pid>/environ -- c'est-a-dire les variables qu'on lui transmet, mot de
# passe Postgres compris.
#
# Chaque application tourne donc sous son propre uid, derive de son nom.
# Derive et non attribue : aucun etat a tenir a jour, rien a migrer, et le
# meme nom redonne toujours le meme uid -- une application qui redemarre
# retrouve ses fichiers. Deux noms peuvent tomber sur le meme uid ; c'est
# alors exactement la situation d'avant, jamais pire.
#
# CE QUE CELA NE FAIT PAS, et il faut le savoir : /workspace reste partage par
# le groupe codelab, parce que ton code doit rester modifiable depuis une
# session SSH. Une application peut donc toujours LIRE et ECRIRE les fichiers
# d'une autre a travers le groupe. Ce qui change, c'est ce qui n'appartient a
# personne d'autre : les process, leur environnement, et les fichiers qu'une
# application cree en 0600 pour elle-meme.
UID_APP_BASE = int(os.environ.get("APP_MANAGER_UID_BASE", "10000"))
UID_APP_PLAGE = int(os.environ.get("APP_MANAGER_UID_PLAGE", "5000"))


def uid_application(nom):
    """L'uid d'une application, derive de son nom.

    sha256 et pas hash() : hash() est randomise a chaque demarrage du
    processus (PYTHONHASHSEED), donc l'uid changerait a chaque redemarrage du
    panneau et l'application ne retrouverait plus ses fichiers.
    """
    if not nom:
        return RUN_AS_UID
    empreinte = hashlib.sha256(nom.encode("utf-8")).digest()
    return UID_APP_BASE + int.from_bytes(empreinte[:4], "big") % UID_APP_PLAGE


# Dossier personnel des processus enfants. Sans lui, ils heritent de
# HOME=/root -- illisible et surtout non ecrivable une fois l'uid abandonne,
# et "npm ci" echoue alors sur son cache (~/.npm) avant meme d'installer quoi
# que ce soit. Range dans le volume d'etat plutot que dans /tmp : le cache npm
# survit ainsi d'un build a l'autre.
CHILD_HOME = os.path.join(STATE_DIR, "home")


# --------------------- secrets transmis aux applications ---------------------
#
# credentials.env est en 0600 root : c'est ce qu'on veut, il contient le mot de
# passe du panneau et le secret de double authentification. Mais les
# applications lancees par le panneau tournent sous l'uid 1001 (voir
# drop_privileges) et ne peuvent donc pas le lire -- alors que la lecture de ce
# fichier est precisement ce que la documentation leur demande de faire pour
# obtenir le mot de passe Postgres. Resultat concret : "fe_sendauth: no
# password supplied", une erreur qui ne dit rien de sa cause.
#
# Le panneau, lui, le lit (il tourne en root) : il transmet donc les valeurs a
# ses enfants par l'environnement, ce que read_env() consulte de toute facon
# comme couche la plus faible. Le .env du projet continue de gagner, et rien
# n'est transmis a Dagster, qui lit le fichier directement.
#
# Ce qui n'est PAS transmis : le bloc du panneau. Une application est du code
# arbitraire tournant sous un autre uid -- lui donner le mot de passe admin
# reviendrait a annuler cette separation pour lui offrir l'acces au panneau.
PREFIXE_PRIVE = "APP_MANAGER_"

# Cles qui changent la maniere dont le process enfant s'execute, plutot que ce
# qu'il fait. Une ligne "PATH=..." ajoutee a la main dans credentials.env
# casserait sinon toutes les applications d'un coup, sans rien pour l'expliquer.
CLES_RESERVEES = {"PATH", "HOME", "PORT", "PYTHONPATH", "PYTHONHOME",
                  "LD_PRELOAD", "LD_LIBRARY_PATH"}
# Les cles SSO sont gerees par app-manager lui-meme. La cle privee ne doit
# jamais entrer dans l'environnement d'une application ; la publique est
# injectee explicitement par start() pour rendre la verification portable.
CLES_SSO = {"CODELAB_SSO_PRIVATE_KEY", "CODELAB_SSO_PUBLIC_KEY"}
# Ce que les services se passent entre eux par credentials.env sans que les
# applications aient a le voir : le serveur d'envoi que Mailpit relaie (son
# mot de passe compris) et le mot de passe interne entre le proxy et Mailpit.
# Une application qui l'aurait lirait tous les mails de ses voisines.
PREFIXE_INTERNE = "CODELAB_INTERNE_"
# Le compte ADMINISTRATEUR de Postgres (super-utilisateur : il entre dans
# toutes les bases). Il ne part plus vers les applications : chacune recoit
# a la place le compte de SA base, sous les memes noms -- voir
# identifiants_base().
CLES_BASE_ADMIN = {"POSTGRES_USER", "POSTGRES_PASSWORD"}

# Le bloc d'alertes du panneau (serveur d'envoi, identifiant, mot de passe,
# adresse de l'administrateur). Defini plus bas, avec CHAMPS_SMTP dont il
# derive : une seule liste de champs, pas deux a tenir d'accord. Python
# resout ce nom a l'appel, pas a l'import -- et secrets_partages() n'est
# appelee qu'au lancement d'une application.


def secrets_partages():
    """Les valeurs de credentials.env destinees aux applications.

    Meme tolerance de lecture que cote projet (checks.py) : commentaires,
    lignes vides et lignes malformees ignorees, guillemets retires, derniere
    occurrence gagnante -- chaque service reecrit son bloc en fin de fichier,
    donc une valeur laissee plus haut est perimee.

    Relu a chaque demarrage plutot que mis en cache : un mot de passe change
    est ainsi pris en compte en redemarrant l'application, sans redemarrer le
    panneau.

    Trois familles de cles ne sortent pas d'ici : celles prefixees
    APP_MANAGER_ (les secrets du panneau), celles qui changeraient la maniere
    dont le process s'execute (CLES_RESERVEES), et le bloc d'alertes du
    panneau (CLES_PANNEAU).
    """
    valeurs = {}
    try:
        with open(SHARED_ENV_FILE) as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, _, valeur = ligne.partition("=")
                cle, valeur = cle.strip(), valeur.strip()
                if (not cle or cle.startswith(PREFIXE_PRIVE)
                        or cle.startswith(PREFIXE_INTERNE)
                        or cle in CLES_RESERVEES or cle in CLES_PANNEAU
                        or cle in CLES_SSO or cle in CLES_BASE_ADMIN):
                    continue
                if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
                    valeur = valeur[1:-1]
                valeurs[cle] = valeur
    except OSError:
        # Volume config non monte, ou fichier pas encore ecrit : les
        # applications se debrouillent avec leur propre .env, comme avant.
        pass
    return valeurs


# ------------------------ une base, un compte ------------------------
#
# Chaque projet a sa base ET son compte Postgres, du meme nom : seul ce
# compte (et l'administrateur) peut s'y connecter. Une application recoit
# donc POSTGRES_USER / POSTGRES_PASSWORD -- les memes noms qu'avant, rien a
# changer dans son code -- mais ceux de SA base : les autres, et celles de
# la stack (dagster, codelab), lui sont fermees par Postgres lui-meme.
#
# Sa base : CODELAB_DB dans le .env du projet, sinon le nom de son dossier
# (la regle que suivent deja les projets, voir dev/AGENTS.md). Au lancement,
# le panneau la cree si elle manque, avec son compte : un projet copie tourne
# sans preparation, comme quand il avait le compte administrateur.
#
# Le mot de passe du compte n'est rangé nulle part : il se derive du mot de
# passe administrateur et du nom de la base -- meme formule que
# postgres/proteger-base.sql, qui fait le travail cote Postgres.
PROTEGER_SQL = os.environ.get("CODELAB_PROTEGER_SQL",
                              "/usr/local/share/codelab/proteger-base.sql")
BASES_DE_LA_STACK = {"dagster", "codelab", "postgres", "codelab_essai_restauration"}
NOM_BASE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
BASE_ASSUREE_DUREE = 600
_bases_assurees = {}


def mot_de_passe_base(base, cle):
    return hashlib.sha256(f"{cle}:codelab-base:{base}".encode("utf-8")).hexdigest()


def _valeur_env_projet(chemin, cle):
    """La derniere valeur de « cle » dans le .env d'un projet, ou None."""
    valeur = None
    try:
        with open(os.path.join(chemin, ".env"), encoding="utf-8", errors="replace") as f:
            for ligne in f:
                ligne = ligne.strip()
                if ligne.startswith("export "):
                    ligne = ligne[7:].lstrip()
                if ligne.startswith("#") or "=" not in ligne:
                    continue
                k, _, v = ligne.partition("=")
                if k.strip() == cle:
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    valeur = v
    except OSError:
        return None
    return valeur


def base_de_l_app(a):
    """Le nom de la base d'une application, ou None s'il n'en est pas un."""
    chemin = (a or {}).get("path") or ""
    nom = _valeur_env_projet(chemin, "CODELAB_DB") or os.path.basename(chemin.rstrip("/"))
    if not NOM_BASE_RE.match(nom or "") or nom in BASES_DE_LA_STACK:
        return None
    return nom


def assurer_base(base):
    """Cree la base et son compte s'il le faut, et realigne leurs droits.

    Rend True si c'est fait (ou fait il y a peu). Jamais d'exception : une
    base injoignable ne doit pas empecher une application de demarrer --
    elle echouerait a s'y connecter, comme avant.
    """
    if time.time() - _bases_assurees.get(base, 0) < BASE_ASSUREE_DUREE:
        return True
    r = _pg_reglages()
    if not r["password"] or not os.path.exists(PROTEGER_SQL):
        return False
    env = dict(os.environ, PGPASSWORD=r["password"], CODELAB_BASE=base,
               CODELAB_CLE_BASES=r["password"], PGCONNECT_TIMEOUT="5",
               CODELAB_SCHEMA=read_shared_value("CODELAB_PROJECT_SCHEMA") or "dagster")
    try:
        p = subprocess.run(["psql", "-h", r["host"], "-p", str(r["port"]), "-U", r["user"],
                            "-d", r["instance"], "-q", "-f", PROTEGER_SQL],
                           env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[app-manager] base {base} : {type(e).__name__}: {e}", flush=True)
        return False
    if p.returncode != 0:
        print(f"[app-manager] base {base} non preparee : {p.stderr.strip()[:300]}", flush=True)
        return False
    _bases_assurees[base] = time.time()
    return True


def identifiants_base(a):
    """Ce qu'une application recoit pour SA base : son compte, pas celui de tous."""
    base = base_de_l_app(a)
    cle = read_shared_value("POSTGRES_PASSWORD") or ""
    if not base or not cle:
        return {}
    assurer_base(base)
    return {"POSTGRES_USER": base, "POSTGRES_PASSWORD": mot_de_passe_base(base, cle),
            "CODELAB_DB": base}


def ensure_child_home(nom=None):
    """Le dossier personnel des process d'une application.

    Un par uid, pas un pour tout le monde : ~/.npmrc, les jetons qu'un outil y
    depose et le cache npm appartiennent a une application, pas au voisinage.
    Le cache continue de survivre d'un build a l'autre -- c'est la raison
    d'etre de ce dossier -- simplement il ne survit plus d'une application a
    l'autre, ce qui n'a jamais ete voulu.

    Nomme par l'uid et non par le nom de l'application : un uid est un entier,
    donc jamais un chemin qui s'echappe, meme si apps.json a ete edite a la
    main.
    """
    uid = uid_application(nom) if nom else RUN_AS_UID
    dossier = str(uid)
    chemin = os.path.join(CHILD_HOME, dossier)
    os.makedirs(CHILD_HOME, exist_ok=True)

    # Le PARENT n'est pas ecrivable par les applications. C'est la ligne qui
    # compte, et elle a manque : quand il l'etait (2770, groupe codelab), une
    # application pouvait effacer son propre dossier, le remplacer par un lien
    # symbolique vers n'importe quel dossier de la machine, et attendre. Au
    # redemarrage suivant, le chown pose ici par root suivait le lien et
    # donnait la cible a l'application -- le dossier des secrets, par exemple,
    # dont il suffit alors de remplacer le fichier. Elle pouvait aussi ecraser
    # le dossier personnel d'une AUTRE application et y deposer un .profile,
    # que "bash -lc" execute sous l'uid de la voisine.
    #
    # 0751 : root cree et modifie, le groupe traverse seulement. Traverser
    # suffit, puisque chaque application est proprietaire de son propre
    # dossier a l'interieur.
    if os.geteuid() == 0:
        try:
            os.chown(CHILD_HOME, 0, RUN_AS_GID)
            os.chmod(CHILD_HOME, 0o0751)
        except OSError as e:
            print(f"[app-manager] {CHILD_HOME} : droits non poses ({e}).", flush=True)

    # Tout se fait relativement a un descripteur du parent, et le dossier est
    # ouvert en O_NOFOLLOW : meme si une entree hostile subsistait d'une
    # version precedente, elle n'est pas suivie mais retiree.
    try:
        parent = os.open(CHILD_HOME, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as e:
        print(f"[app-manager] {CHILD_HOME} illisible ({e}).", flush=True)
        return chemin
    try:
        try:
            infos = os.lstat(dossier, dir_fd=parent)
        except FileNotFoundError:
            infos = None
        if infos is not None and not stat_mod.S_ISDIR(infos.st_mode):
            # Un lien, un fichier : pas un dossier personnel. On ne le suit
            # pas, on l'enleve.
            print(f"[app-manager] {chemin} n'etait pas un dossier -- retire.", flush=True)
            os.unlink(dossier, dir_fd=parent)
            infos = None
        if infos is None:
            os.mkdir(dossier, 0o700, dir_fd=parent)
        if os.geteuid() == 0:
            fd = os.open(dossier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent)
            try:
                # 2700 et pas 2770 : c'est precisement ce que le groupe
                # partage ne doit PAS ouvrir. Le setgid reste, pour que ce qui
                # y nait garde le groupe codelab.
                os.fchown(fd, uid, RUN_AS_GID)
                os.fchmod(fd, 0o2700)
            finally:
                os.close(fd)
    except OSError as e:
        print(f"[app-manager] {chemin} : droits non poses ({e}).", flush=True)
    finally:
        os.close(parent)
    return chemin


def drop_privileges(nom=None):
    """Bascule le processus courant sur l'utilisateur non privilegie.

    Appelee dans le preexec_fn, donc APRES le fork et AVANT l'exec : elle ne
    touche jamais au service lui-meme. Sans effet si l'on n'est pas root, ce
    qui est le cas quand app.py tourne hors conteneur (tests, mise au point).

    nom : l'application concernee, qui donne son uid. Sans nom, l'uid partage
    d'avant -- c'est le cas des travaux qui n'appartiennent a aucune
    application en particulier.
    """
    if os.geteuid() != 0:
        return
    uid = uid_application(nom) if nom else RUN_AS_UID
    # setgroups avant setuid : une fois l'uid abandonne, le processus n'a plus
    # le droit de modifier ses groupes secondaires, et garderait ceux de root.
    # Le groupe reste commun : c'est lui qui donne l'acces a /workspace, et
    # donc la possibilite de continuer a editer son code en SSH.
    os.setgroups([RUN_AS_GID])
    os.setgid(RUN_AS_GID)
    os.setuid(uid)
    # Repose ici : le umask n'est pas herite du service de maniere fiable a
    # travers toute la chaine, et sans 002 les fichiers produits par un build
    # (dist/, node_modules/) ressortent en lecture seule pour le groupe --
    # donc non modifiables depuis une session SSH.
    os.umask(0o002)


# ------------------- isolation du systeme de fichiers -------------------
#
# L'uid par application separait les process et leur environnement. Il ne
# separait PAS les fichiers : /workspace est partage par le groupe codelab --
# il le faut, sinon ton code n'est plus modifiable depuis une session SSH --
# donc une application pouvait lire le .env de sa voisine.
#
# Chaque application recoit maintenant sa propre vue du systeme de fichiers,
# dans laquelle /workspace ne contient QU'ELLE.
#
# COMMENT, ET POURQUOI CE CHEMIN-LA. Creer un namespace de montage demande
# CAP_SYS_ADMIN -- que le compose retire justement a tous les conteneurs. Mais
# un namespace UTILISATEUR s'ouvre sans aucune capability, meme sous
# no-new-privileges (mesure), et depuis l'interieur on peut alors monter. On
# obtient donc l'isolation sans rendre au conteneur la capability qu'on vient
# de lui retirer -- les deux durcissements ne se contredisent pas.
#
# CE QU'ON Y GAGNE : les projets voisins deviennent invisibles, et /tmp
# n'est plus partage entre applications.
#
# CE QU'ON Y PERD, et il faut le savoir : dans son namespace, l'application se
# voit uid 0. Elle ne gagne aucun pouvoir dehors -- les fichiers des autres
# lui apparaissent comme appartenant a "nobody" -- mais les namespaces
# utilisateur ont un historique de failles d'evasion du noyau. On echange
# "une application lit les fichiers d'une autre" contre "une application
# touche une surface noyau plus large". Sur un serveur ou les projets ne
# communiquent pas entre eux, l'echange est bon.
#
# CE QUE CELA NE FAIT PAS : les process des autres restent visibles dans
# /proc (leur environnement, lui, reste illisible : uid different). Un
# namespace PID le corrigerait, mais il exige de remonter /proc, ce que
# Docker interdit par ses montages masques -- mesure, pas suppose.
ISOLER_APPS = (os.environ.get("APP_MANAGER_ISOLER", "1").lower()
               not in ("0", "false", "no"))

# Le script qui tourne DANS le namespace, avant la commande de l'application.
# Le chemin et la commande arrivent par l'environnement, jamais par
# interpolation : un nom de projet avec une apostrophe casserait le script,
# et un chemin choisi ailleurs deviendrait une injection.
SCRIPT_ISOLEMENT = r"""
set -e
# Un point d'appui a nous. On ne peut pas simplement ecrire dans /mnt : dans
# le namespace utilisateur, tout ce qui appartient au vrai root apparait
# comme appartenant a "nobody", donc en lecture seule. Monter, en revanche,
# ne demande pas d'ecrire dans le dossier -- seulement qu'il existe.
mount -t tmpfs none /mnt
mkdir /mnt/projet
# Le projet est mis de cote AVANT que la tmpfs ne masque la racine du
# workspace : apres, son emplacement d'origine n'existe plus.
mount --bind "$CODELAB_PROJET" /mnt/projet
# La racine du workspace devient vide, puis ne recoit que ce projet, A SON
# CHEMIN D'ORIGINE : les chemins absolus qu'une application garde dans sa
# configuration continuent de fonctionner.
mount -t tmpfs none "$CODELAB_RACINE"
mkdir -p "$CODELAB_PROJET"
mount --bind /mnt/projet "$CODELAB_PROJET"
umount /mnt/projet
# /tmp prive : deux applications ne se marchent plus dessus, et aucune ne
# lit le fichier temporaire d'une autre.
#
# Sauf si le workspace vit SOUS /tmp -- une installation de mise au point, ou
# APP_MANAGER_ROOT pointe ailleurs. La tmpfs masquerait alors le projet qu'on
# vient de monter, et l'application ne demarrerait plus : "cd: can't cd to
# ...". On prefere un /tmp partage a une application qui ne tourne pas.
case "$CODELAB_PROJET" in
  /tmp|/tmp/*) : ;;
  *) mount -t tmpfs none /tmp ;;
esac
cd "$CODELAB_PROJET"
exec bash -lc "$CODELAB_COMMANDE"
"""


# Resultat du test reel, calcule une fois puis garde : lancer un process a
# chaque demarrage d'application couterait une dizaine de millisecondes pour
# une reponse qui ne change pas sans redemarrage du noyau.
_isolement = {"verdict": None, "raison": ""}


def isolement_disponible():
    """L'isolement par namespace utilisateur fonctionne-t-il VRAIMENT ici ?

    La question n'est pas "unshare est-il installe" mais "le noyau
    accepte-t-il". Les deux se separent, et c'est tout l'interet de cette
    fonction : unshare vient de util-linux, donc il est TOUJOURS dans
    l'image -- tandis que la creation d'un namespace utilisateur peut etre
    refusee par la machine hote, sans que l'image n'y soit pour rien :

      - kernel.unprivileged_userns_clone=0 (Debian et derives) ;
      - user.max_user_namespaces=0 ;
      - un profil seccomp ou AppArmor qui filtre l'appel ;
      - un noyau compile sans CONFIG_USER_NS.

    Se contenter de chercher le binaire, comme le faisait cette fonction,
    repondait "disponible" sur ces machines : le panneau lancait alors
    l'application derriere unshare, le noyau refusait, et RIEN NE DEMARRAIT.
    Un repli qui ne se declenche jamais ne protege de rien.

    On execute donc la vraie commande, une fois. C'est la seule reponse qui
    ne se discute pas.
    """
    if _isolement["verdict"] is not None:
        return _isolement["verdict"]

    if shutil.which("unshare") is None:
        _isolement.update(verdict=False, raison="unshare absent de l'image")
        return False

    try:
        essai = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--mount", "true"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        ok = essai.returncode == 0
        raison = "" if ok else (essai.stderr or b"").decode(
            "utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError) as e:
        ok, raison = False, str(e)

    _isolement.update(verdict=ok, raison=raison)
    if not ok:
        # Bruyant, et c'est voulu : tourner sans isolement est une protection
        # en moins. Cela doit se voir dans "docker logs", pas se deviner.
        print("[codelab] ISOLEMENT INDISPONIBLE sur cette machine : %s.\n"
              "[codelab] Les applications demarrent quand meme, mais SANS "
              "etre isolees les unes des autres.\n"
              "[codelab] Pour l'activer : autoriser les namespaces "
              "utilisateur sur l'hote (sysctl kernel.unprivileged_userns_clone=1\n"
              "[codelab] et user.max_user_namespaces>0). Pour ne plus voir ce "
              "message : APP_MANAGER_ISOLER=0." % (raison or "raison inconnue"),
              flush=True)
    return ok


def commande_isolee(nom, chemin, commande, apps=None):
    """La commande a passer a Popen, isolee si c'est possible et voulu.

    Retourne aussi l'environnement a ajouter : le script lit ses deux
    parametres dedans.
    """
    infos = (apps or {}).get(nom) or {}
    # Un reglage par application : le jour ou l'une d'elles a besoin de voir
    # autre chose, la reponse n'est pas "desactive l'isolement partout".
    voulu = ISOLER_APPS and infos.get("isolation", True) is not False
    # Le projet de diagnostic est l'exception, et c'est sa raison d'etre : il
    # fait l'etat des lieux de l'installation. Isole, il ne verrait que
    # lui-meme -- ni /workspace/definitions.py, ni les autres projets -- et il
    # rapporterait une stack en panne alors que tout va bien. L'observateur a
    # besoin de voir.
    #
    # Ecrit ici et pas seulement dans apps.json : une installation deja en
    # place n'a pas le reglage, et se mettrait a jour vers un diagnostic
    # aveugle.
    if nom == DIAGNOSTIC_NOM:
        voulu = False
    if not voulu or not isolement_disponible():
        return ["bash", "-lc", commande], {}
    return (["unshare", "--user", "--map-root-user", "--mount",
             "sh", "-c", SCRIPT_ISOLEMENT],
            {"CODELAB_PROJET": chemin, "CODELAB_COMMANDE": commande,
             "CODELAB_RACINE": ROOT})


def child_setup(max_memory_mb=None, nom=None):
    """preexec_fn commun aux applications et aux builds."""
    def _setup():
        if max_memory_mb:
            # Limite "douce" de memoire virtuelle pour ce process et ses
            # enfants (herite a travers fork/exec). Ne limite pas le CPU :
            # RLIMIT_CPU tue le process une fois un total de secondes CPU
            # cumule atteint, ce qui n'a pas de sens pour un serveur cense
            # tourner indefiniment -- seulement pour un script qui boucle.
            #
            # Pose avant l'abandon des privileges : une limite abaissee ne se
            # releve plus ensuite, l'ordre inverse marcherait aussi mais
            # celui-ci reste vrai si la limite devenait "dure".
            mem = int(max_memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        drop_privileges(nom)
    return _setup


# ------------------------- cycle de vie ----------------------------

def is_running(name):
    p = procs.get(name)
    return p is not None and p.poll() is None


# Combien de temps on regarde l'application vivre avant de la declarer
# demarree. Un processus qui meurt le fait presque toujours tout de suite --
# commande introuvable, port deja pris, dependance absente, isolement refuse.
# Une seconde suffit a les attraper, et n'est pas une attente perceptible
# derriere un clic.
DELAI_DEMARRAGE = 1.0


# ------------------- les processus survivent au panneau -------------------
#
# LE DEFAUT, ET CE QU'IL DONNAIT A VOIR. Les applications sont lancees avec
# start_new_session=True : elles ont leur propre session, donc elles SURVIVENT
# a l'arret du panneau (mise a jour de l'image, "docker restart", plantage).
# Le dictionnaire procs, lui, ne survivait pas : il vit en memoire.
#
# Au redemarrage, le panneau ne connaissait donc plus les processus toujours
# en vie. Consequences en chaine, toutes constatees :
#
#   - l'interface affichait « Arretee » d'une application qui tournait ;
#   - resume() la relancait, et la nouvelle mourait sur « address already in
#     use » -- l'ancienne continuait de repondre ;
#   - et surtout, ARRETER NE FAISAIT RIEN : stop() ne trouvait rien a tuer,
#     mettait enabled=False, et repondait que tout allait bien. On cliquait,
#     le service continuait de tourner.
#
# Le panneau note donc sur disque ce qu'il a lance, et RECONNAIT ses
# processus au demarrage suivant. Le pid seul ne suffit pas : un numero est
# reutilise par le noyau, et tuer le mauvais processus serait pire que le
# defaut qu'on corrige. On verifie donc que le processus porte bien la
# marque que le panneau lui a posee -- son environnement contient
# CODELAB_APP=<nom>.
PROCESSUS_FILE = os.path.join(STATE_DIR, "processus.json")


def _ecrire_processus(d):
    """Remplace la liste des processus lances, en une transaction : c'est
    elle qui sert a se remettre d'un arret brutal."""
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM processus")
        for nom, p in (d or {}).items():
            cx.execute("INSERT INTO processus (nom, pid, depuis) VALUES (?, ?, ?)",
                       (nom, int(p.get("pid") or 0), p.get("depuis")))


def lire_processus():
    try:
        lignes = stockage.lire(DB_FILE, "SELECT nom, pid, depuis FROM processus")
    except Exception:                                             # noqa: BLE001
        return {}
    return {l["nom"]: {"pid": l["pid"], "depuis": l["depuis"]} for l in lignes}


def _noter_processus(nom, pid):
    d = lire_processus()
    d[nom] = {"pid": int(pid), "depuis": int(time.time())}
    _ecrire_processus(d)


def _oublier_processus(nom):
    d = lire_processus()
    if d.pop(nom, None) is not None:
        _ecrire_processus(d)


def _processus_est_le_notre(pid, nom):
    """Ce pid est-il bien l'application <nom> lancee par ce panneau ?

    La marque est dans l'environnement du processus, que le noyau fige a
    l'exec : elle ne peut pas etre reecrite apres coup par le programme
    lui-meme, contrairement a son titre (argv[0]).
    """
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as f:
            environ = f.read()
    except (OSError, ValueError):
        return False
    return b"CODELAB_APP=" + nom.encode() + b"\0" in environ + b"\0"


class ProcessusAdopte:
    """Un processus lance par un panneau precedent, repris en main.

    Il expose juste ce que le reste du code attend d'un Popen : un pid et
    poll(). Rien de plus -- on ne peut pas recuperer le code de sortie d'un
    processus dont on n'est pas le pere, et personne ici n'en a besoin.
    """

    def __init__(self, pid, nom):
        self.pid = int(pid)
        self.nom = nom
        self.returncode = None

    def poll(self):
        return None if _processus_est_le_notre(self.pid, self.nom) else 0

    def wait(self, timeout=None):
        return self.poll()


def adopter_processus_survivants():
    """Reprend la main sur ce qu'un panneau precedent a laisse tourner.

    Appele AVANT resume() : sans cela, resume() relancerait par-dessus une
    application deja en vie, et la nouvelle mourrait sur un port occupe.
    """
    repris, oublies = [], []
    for nom, info in lire_processus().items():
        pid = (info or {}).get("pid")
        if pid and _processus_est_le_notre(pid, nom) and nom in load():
            procs[nom] = ProcessusAdopte(pid, nom)
            repris.append(f"{nom} (pid {pid})")
        else:
            oublies.append(nom)
    for nom in oublies:
        _oublier_processus(nom)
    if repris:
        print("[app-manager] processus repris apres redemarrage : "
              + ", ".join(repris), flush=True)
    return repris


def start(name, attendre=True):
    """Demarre une application. Rend None si tout va bien, sinon POURQUOI.

    Avant, cette fonction se taisait dans tous les cas d'echec : dossier
    disparu, commande introuvable, port deja pris, isolement refuse. Le
    panneau repondait "ok" et l'interface revenait a "Arretee" sans un mot.
    On cliquait, rien ne se passait, et il fallait aller lire le journal de
    l'application pour comprendre -- en supposant qu'on sache qu'il existe.
    """
    apps = load()
    a = apps.get(name)
    if not a or is_running(name):
        return None

    # Le dossier de l'app peut avoir disparu (supprime depuis /workspace,
    # volume non monte, renomme). Sans ce garde-fou, subprocess.Popen leve
    # FileNotFoundError sur cwd -- et comme resume() est appele au demarrage
    # du service, une seule app orpheline empeche app-manager entier de
    # demarrer, en boucle de redemarrage.
    if not os.path.isdir(a["path"]):
        print(f"[app-manager] {name} : dossier introuvable ({a['path']}), "
              f"demarrage ignore.", flush=True)
        apps[name]["enabled"] = False
        save(apps)
        return f"Dossier introuvable : {a['path']}"

    # LE PORT, AVANT DE LANCER QUOI QUE CE SOIT. Sans cette verification, une
    # application dont le port est deja occupe demarre, meurt sur
    # « Address already in use », et le panneau rend la derniere ligne du
    # journal -- un message d'un autre langage, parfois noye dans une trace.
    # Le cas n'a rien de rare : un processus survivant d'avant un redemarrage
    # brutal, deux applications reglees sur le meme port, ou un service de la
    # machine. Le dire en francais, et nommer le coupable quand on le connait.
    occupe = port_occupe_par(a["port"], name)
    if occupe:
        return occupe

    os.makedirs(LOG_DIR, exist_ok=True)
    rotate_log_if_needed(name)
    try:
        out = open(os.path.join(LOG_DIR, name + ".log"), "ab", buffering=0)
    except OSError as e:
        return f"Journal impossible a ouvrir : {e}"
    env = _environnement_de_lancement(name, a)
    argv, env_isolement = commande_isolee(name, a["path"], a["command"], apps)
    env.update(env_isolement)

    # Popen leve pour tout ce qui empeche le lancement AVANT la commande :
    # dossier devenu inaccessible, interpreteur absent, preexec_fn en echec
    # (abandon des privileges impossible, limite memoire refusee). Sans ce
    # filet, l'exception remontait jusqu'a Flask : la route repondait 500 et
    # une page HTML, l'interface n'y trouvait aucun message, et affichait son
    # texte par defaut -- « L'application n'a pas demarre », qui n'apprend
    # rien a personne.
    try:
        with lock:
            procs[name] = subprocess.Popen(
                argv,
                cwd=a["path"], env=env, stdout=out, stderr=out,
                start_new_session=True,
                preexec_fn=child_setup(a.get("max_memory_mb"), name))
    except (OSError, ValueError) as e:
        out.close()
        apps = load()
        if name in apps:
            apps[name]["enabled"] = False
            save(apps)
        return f"Lancement impossible : {e}"
    _noter_processus(name, procs[name].pid)
    apps[name]["enabled"] = True
    save(apps)

    if not attendre:
        return None

    return _surveiller_le_demarrage(name)


def _environnement_de_lancement(name, a):
    """L'environnement d'une application : celui du panneau, plus ses
    secrets a elle, son port, son HOME et de quoi verifier le SSO.

    SECURITE (regle 19 de SECURITE.md) : secrets_partages() ne transmet que
    ce qu'une application a le droit de lire -- jamais le bloc du panneau
    (mot de passe, cle de session, secret de double authentification), voir
    « secrets transmis aux applications ». identifiants_base() ne donne que
    le compte de SA base (« une base, un compte »). Ajouter une variable
    ici, c'est la donner a du code arbitraire : verifier qu'elle n'ouvre
    rien.
    """
    env = dict(os.environ, **secrets_partages(), **identifiants_base(a))
    env.update(PORT=str(a["port"]), PYTHONUNBUFFERED="1",
               HOME=ensure_child_home(name),
               CODELAB_SSO_PUBLIC_KEY=_sso_public_key,
               CODELAB_SSO_ISSUER=SSO_ISSUER,
               CODELAB_SSO_AUDIENCE=name,
               # La marque qui permettra de reconnaitre ce processus apres un
               # redemarrage du panneau. Le noyau fige l'environnement a
               # l'exec : l'application ne peut pas l'effacer.
               CODELAB_APP=name)
    return env


def _surveiller_le_demarrage(name):
    """Regarde l'application vivre DELAI_DEMARRAGE secondes ; rend None si
    elle tient, sinon pourquoi elle est tombee.

    Sans cela, « demarree » veut seulement dire « Popen n'a pas leve
    d'exception » -- ce qui reste vrai d'une commande qui meurt a la ligne
    suivante.
    """
    fin = time.time() + DELAI_DEMARRAGE
    while time.time() < fin:
        if procs[name].poll() is not None:
            code = procs[name].returncode
            procs.pop(name, None)
            _oublier_processus(name)
            apps = load()
            if name in apps:
                apps[name]["enabled"] = False
                # Retenu pour la fiche : « Arretee » seul ne dit pas qu'elle a
                # plante au lancement. Efface au prochain demarrage reussi.
                apps[name]["dernier_echec"] = {"quand": int(time.time()), "code": code}
                save(apps)
            return (f"L'application s'est arrêtée aussitôt (code {code}). "
                    + derniere_ligne_utile(name))
        time.sleep(0.05)
    apps = load()
    if name in apps and apps[name].pop("dernier_echec", None) is not None:
        save(apps)
    return None


def _ecouteurs_port(port):
    """Retourne les PID qui ecoutent reellement sur le port TCP.

    Le simple bind sur 127.0.0.1 ne voit pas proprement tous les cas
    (IPv6, adresse specifique, socket deja publie). psutil donne ici le meme
    niveau d'information que « ss -ltnp », sans dependre d'un binaire
    externe.
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        return []
    pids = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            if int(conn.laddr.port) == port and conn.pid:
                pids.add(int(conn.pid))
    except (psutil.Error, OSError):
        pass
    return sorted(pids)


def _description_processus(pid):
    try:
        p = psutil.Process(pid)
        nom = p.name()
        cmd = " ".join(p.cmdline()[:3])
        return nom, cmd
    except psutil.Error:
        return "processus", ""


def port_occupe_par(port, sauf=None):
    """Le port est-il deja pris ? Rend le message a afficher, ou None.

    En cas de processus CodeLab survivant, on le reprend automatiquement
    lorsqu'il porte encore la marque CODELAB_APP. Un redemarrage brutal du
    panneau ne doit pas transformer une application saine en conflit de port.
    Pour tout autre processus, le demarrage reste bloque et l'interface donne
    le PID pour faciliter le diagnostic.
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None

    ecouteurs = _ecouteurs_port(port)
    if not ecouteurs:
        # Cas de course ou de socket TCP atypique : le bind reste le dernier
        # filet, mais on ne l'utilise plus comme source unique de vérité.
        sonde = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sonde.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sonde.bind(("127.0.0.1", port))
            return None
        except OSError:
            pass
        finally:
            sonde.close()
        return (f"Le port {port} est deja occupe sur la machine. "
                f"Verifie avec « ss -ltnp | grep {port} ».")

    apps = load()
    for pid in ecouteurs:
        # Le cas important : le processus appartient a CETTE application,
        # mais le fichier de pid a ete perdu ou le panneau a redemarre avant
        # d'avoir pu le relire. On peut le reprendre sans le tuer.
        if _processus_est_le_notre(pid, sauf):
            procs[sauf] = ProcessusAdopte(pid, sauf)
            _noter_processus(sauf, pid)
            apps = load()
            if sauf in apps:
                apps[sauf]["enabled"] = True
                save(apps)
            print(f"[app-manager] {sauf} : processus survivant repris "
                  f"(pid {pid}, port {port}).", flush=True)
            return None

        # Si le port appartient a une autre application CodeLab, nommons-la
        # meme si son registre de processus a ete perdu.
        for autre in apps:
            if autre == sauf or str(apps[autre].get("port")) != str(port):
                continue
            if _processus_est_le_notre(pid, autre):
                return (f"Le port {port} est deja occupe par « {autre} » "
                        f"(pid {pid}). Change le port de l'une des deux "
                        f"dans sa configuration.")

        nom_proc, cmd = _description_processus(pid)
        detail = f"PID {pid} ({nom_proc})"
        if cmd:
            detail += f" : {cmd}"
        return (f"Le port {port} est deja occupe sur la machine ({detail}). "
                f"Verifie avec « ss -ltnp | grep {port} ».")

    return None


def derniere_ligne_utile(nom):
    """La derniere ligne non vide du journal, pour dire POURQUOI.

    C'est ce qui transforme "ca ne marche pas" en "python3: can't open file"
    ou "bind: address already in use". Sans elle, le message d'erreur
    n'apprend rien que l'interface ne montrait deja.
    """
    chemin = os.path.join(LOG_DIR, nom + ".log")
    try:
        with open(chemin, "rb") as f:
            # Les dernieres lignes suffisent, et un journal peut etre gros.
            f.seek(0, os.SEEK_END)
            debut = max(0, f.tell() - 4096)
            f.seek(debut)
            lignes = [l.strip() for l in f.read().decode("utf-8", "replace").splitlines()]
    except OSError:
        return "Le journal de l'application est illisible."
    for ligne in reversed(lignes):
        if ligne:
            return ligne[:300]
    return f"Le journal est vide : {chemin}"


def _signaler_groupe(pgid, sig, uid):
    """Envoie un signal au groupe de processus d'une application.

    POURQUOI CE N'EST PAS UN SIMPLE os.killpg. Le panneau tourne en root,
    mais avec cap_drop: ALL et une poignee de capabilities rendues -- et KILL
    n'en faisait pas partie. Or le noyau n'autorise un signal que si
    l'expediteur a CAP_KILL, ou si son uid est celui de la cible. Chaque
    application tournant sous SON PROPRE uid (voir uid_application), root se
    voyait refuser l'arret de toutes :

        Le panneau n'a pas pu signaler le processus (pid 11) :
        [Errno 1] Operation not permitted

    La capability est rendue dans les deux composes. Ce repli sert les
    installations qui tournent encore sur l'ancien : on abandonne l'uid dans
    un ENFANT -- jamais dans le service lui-meme, qui en a besoin -- et le
    signal part alors d'un processus qui a exactement l'uid de sa cible.
    Le panneau garde SETUID, donc cette bascule-la reste permise.

    Leve l'erreur d'origine si le repli echoue aussi : un arret qui ne
    s'arrete pas doit se dire, pas se deviner.
    """
    try:
        os.killpg(pgid, sig)
        return
    except PermissionError as refus:
        if os.geteuid() != 0 or not uid:
            raise
        # fork plutot que seteuid : le service est multi-thread, et changer
        # l'uid du processus entier -- meme une seconde -- ouvrirait une
        # fenetre ou une autre requete s'executerait sous un uid qui n'est
        # pas le sien.
        enfant = os.fork()
        if enfant == 0:                                   # pragma: no cover
            # Ici, uniquement des appels systeme : ce code tourne apres un
            # fork dans un processus multi-thread, ou tout le reste est
            # hasardeux.
            try:
                os.setgroups([RUN_AS_GID])
                os.setgid(RUN_AS_GID)
                os.setuid(uid)
                os.killpg(pgid, sig)
                os._exit(0)
            except OSError:
                os._exit(1)
        _, etat = os.waitpid(enfant, 0)
        if etat != 0:
            raise refus


def stop(name):
    """Arrete une application. Rend None si tout va bien, sinon POURQUOI.

    MEME CONTRAT QUE start(), et il manquait. Cette fonction ne rendait rien
    et n'attrapait que ProcessLookupError : un signal refuse (EPERM), un
    disque plein au moment de reecrire processus.json ou apps.json, et
    l'exception traversait la route jusqu'a une page d'erreur HTTP 500. La
    page, qui n'y trouvait aucun JSON, affichait alors son message par
    defaut -- ecrit pour le demarrage : un arret qui echoue s'annoncait
    « L'application n'a pas demarre. »

    Et le cas le plus couteux ne levait rien du tout : un processus qui
    survit a SIGTERM comme a SIGKILL laissait tout de meme enabled=False et
    une reponse « tout va bien ». Le panneau affichait « Arretee » d'une
    application qui tournait toujours et tenait son port -- exactement le
    defaut que l'adoption des processus avait corrige ailleurs.
    """
    p = procs.get(name)
    erreur = None
    if p and p.poll() is None:
        uid = uid_application(name)
        try:
            _signaler_groupe(os.getpgid(p.pid), signal.SIGTERM, uid)
            for _ in range(30):
                if p.poll() is not None:
                    break
                time.sleep(0.1)
            if p.poll() is None:
                _signaler_groupe(os.getpgid(p.pid), signal.SIGKILL, uid)
                # Laisser au noyau le temps de faire le travail : sans cette
                # seconde attente, on constatait la survie du processus a
                # l'instant meme ou on venait de l'achever.
                for _ in range(20):
                    if p.poll() is not None:
                        break
                    time.sleep(0.05)
        except ProcessLookupError:
            pass        # deja parti entre le poll et le signal : c'est gagne
        except PermissionError as e:
            # Le message nomme la cause : sans elle, on cherche du cote de
            # l'application alors que rien, dans l'application, n'y peut
            # quoi que ce soit.
            erreur = (f"Le panneau n'a pas le droit d'arrêter le processus "
                      f"(pid {getattr(p, 'pid', '?')}) : {e}. Il lui manque la "
                      f"capability KILL : ajoute « - KILL » sous cap_add du "
                      f"service codelab-app-manager dans docker-compose.yml, "
                      f"puis « docker compose up -d codelab-app-manager ».")
        except OSError as e:
            erreur = (f"Le panneau n'a pas pu signaler le processus "
                      f"(pid {getattr(p, 'pid', '?')}) : {e}")
        if erreur is None and p.poll() is None:
            erreur = (f"Le processus (pid {p.pid}) ne s'arrete ni sur SIGTERM "
                      f"ni sur SIGKILL.")
    if erreur:
        # On ne touche NI a procs NI a enabled : marquer "arretee" une
        # application toujours vivante, c'est perdre la seule trace qui
        # permet encore de la retrouver -- et promettre a l'interface un
        # etat qui n'existe pas.
        print(f"[app-manager] {name} : arret impossible -- {erreur}", flush=True)
        return erreur
    procs.pop(name, None)
    _oublier_processus(name)
    _restart_history.pop(name, None)  # arret volontaire : on oublie l'historique de crash
    apps = load()
    if name in apps:
        apps[name]["enabled"] = False
        save(apps)
    return None


def tenir_les_applications_par_defaut():
    """Elles ne s'arretent plus a la main, donc le panneau s'en charge.

    Le piege qu'on ferme ici : une application par defaut n'est PAS
    configurable, et son arret est refuse cote serveur (voir
    refus_action_protegee). Il reste pourtant un chemin qui la marque
    "enabled": False sans que personne l'ait voulu -- l'echec de lancement
    dans start(), quand le systeme refuse la limite memoire ou l'abandon de
    privileges. Une fois a False, plus rien ne la rallumait : le bouton qui
    l'aurait fait est justement celui qu'on interdit. L'installation restait
    sans diagnostic, definitivement, et sans rien pour le dire.

    Deux gestes, a chaque demarrage du panneau :

      - REMETTRE "enabled", pour sortir de cet etat mort ;
      - POSER LA DESCRIPTION si elle manque, parce qu'une tuile sans
        description n'apprend rien et que celle-ci ne s'ecrit pas ailleurs.

    Ni l'un ni l'autre n'ecrase un choix : la description deja presente est
    laissee telle quelle, et "enabled" n'est pas un reglage que l'on choisit
    pour ces deux-la.
    """
    apps = load()
    change = False
    for nom in APPS_PAR_DEFAUT:
        a = apps.get(nom)
        if a is None:
            continue
        if not a.get("enabled"):
            a["enabled"] = True
            change = True
        if not (a.get("titre") or "").strip():
            a["titre"] = TITRES_PAR_DEFAUT[nom]
            change = True
        if nom == DIAGNOSTIC_NOM and ((a.get("description") or "").strip() in ("",) + DIAGNOSTIC_DESCRIPTIONS_ANCIENNES):
            a["description"] = DIAGNOSTIC_DESCRIPTION
            change = True
    if change:
        save(apps)


def resume():
    tenir_les_applications_par_defaut()
    for name, a in load().items():
        if a.get("enabled"):
            # Une app qui refuse de demarrer ne doit jamais empecher le
            # panneau de se lancer : c'est justement depuis le panneau qu'on
            # va la reparer ou la supprimer.
            try:
                start(name, attendre=False)
            except Exception as e:
                print(f"[app-manager] {name} : echec du demarrage auto ({e}), "
                      f"ignoree.", flush=True)


# ------------------------- redemarrage automatique ----------------------------

def monitor_tick():
    """Un tour de surveillance : toute app marquee "enabled" dans
    apps.json mais dont le process est mort (crash, pas un arret
    volontaire -- stop() met "enabled" a False) est redemarree
    automatiquement, avec une limite d'essais pour eviter de s'epuiser
    sur une app qui plante en boucle des le demarrage."""
    # LE RATTRAPAGE NE VAUT PAS QU'AU DEMARRAGE. Une application par defaut
    # eteinte entre deux redemarrages du panneau le restait jusqu'au suivant
    # -- c'est-a-dire, sur une machine qui ne redemarre pas, pour de bon. Le
    # geste est le meme ici, et il ne court-circuite rien : c'est toujours la
    # limite d'essais ci-dessous qui decide de relancer ou non, donc une
    # application qui plante en boucle n'est pas relancee sans fin.
    tenir_les_applications_par_defaut()
    apps = load()
    now = time.time()
    for name, a in apps.items():
        if not a.get("enabled") or is_running(name):
            continue
        hist = [t for t in _restart_history.get(name, []) if now - t < RESTART_WINDOW]
        if len(hist) < RESTART_MAX_ATTEMPTS:
            hist.append(now)
            _restart_history[name] = hist
            print(f"[app-manager] {name} arretee de maniere inattendue, "
                  f"redemarrage automatique ({len(hist)}/{RESTART_MAX_ATTEMPTS})", flush=True)
            start(name, attendre=False)
        else:
            _restart_history[name] = hist


# ------------------------- sonde d'ecoute ----------------------------
#
# "Le process est vivant" et "l'application est joignable" sont deux choses
# differentes : un serveur qui plante dans son thread d'ecoute, ou qui n'a
# jamais reussi a prendre son port, laisse un process bien vivant derriere
# lui. La pastille etait alors verte et la page blanche, sans rien pour
# distinguer les deux avant d'ouvrir l'application.
#
# La sonde ouvre une connexion TCP sur 127.0.0.1:<port>, exactement la cible
# du reverse proxy (voir _proxy) : ce qu'elle mesure est donc bien "le
# panneau saurait-il servir cette application maintenant". Volontairement pas
# de requete HTTP : le port ouvert est le signal cherche, et une reponse
# applicative peut legitimement etre un 404 ou un 405 sans que rien n'aille
# mal.
PROBE_TIMEOUT = 1.5

# Flux de journal en direct (Server-Sent Events). Un battement regulier tient
# la connexion ouverte quand le journal est silencieux ; une duree de vie
# bornee rend le thread au serveur, le navigateur se reconnectant tout seul.
# Un flux silencieux n'ecrit rien, et c'est en ecrivant que le serveur
# s'apercoit qu'un client est parti : le battement sert donc aussi a liberer
# la place d'un onglet ferme, dans ce delai au pire.
SSE_BATTEMENT = 10      # secondes de silence avant un commentaire de maintien
SSE_DUREE_MAX = 600     # 10 minutes, puis reconnexion transparente

# Nombre de threads du serveur HTTP. Le panneau relaie le trafic des
# applications : une application lente retient un thread pendant toute sa
# reponse, et le defaut de waitress (4) suffirait a bloquer le panneau entier
# derriere quelques requetes trainantes.
WSGI_THREADS = int(os.environ.get("APP_MANAGER_THREADS", "16"))

# Silence tolere sur une connexion avant fermeture. Genereux, parce que le
# panneau relaie aussi les applications : une application qui fait du
# long-polling ou son propre flux d'evenements ne doit pas etre coupee par le
# proxy.
WSGI_TIMEOUT = int(os.environ.get("APP_MANAGER_TIMEOUT", "600"))

# Un flux de journal occupe un thread tant qu'il est ouvert. Sans plafond,
# assez d'onglets ouverts sur des journaux consomment tout le pool et le
# panneau ne repond plus du tout -- mesure : avec 16 threads, 20 flux
# simultanes le rendaient muet, healthcheck compris.
#
# La moitie du pool : mesure faite, 8 flux ouverts en meme temps laissent le
# panneau repondre en 5 ms, et laisser l'autre moitie pour les pages et le
# relai des applications suffit largement. Plus bas, on risquerait un refus
# la ou personne n'a rien fait de deraisonnable -- l'interface n'ouvre qu'un
# flux a la fois par onglet.
SSE_MAX_FLUX = max(1, WSGI_THREADS // 2)

_flux_verrou = threading.Lock()
_flux_ouverts = 0


def _prendre_place_flux():
    """Reserve une place de flux, ou None s'il n'y en a plus."""
    global _flux_ouverts
    with _flux_verrou:
        if _flux_ouverts >= SSE_MAX_FLUX:
            return False
        _flux_ouverts += 1
        return True


def _rendre_place_flux():
    global _flux_ouverts
    with _flux_verrou:
        _flux_ouverts = max(0, _flux_ouverts - 1)

# nom -> True (port ouvert) / False (rien n'ecoute). Une app arretee n'y
# figure pas : l'absence de cle veut dire "non concernee", pas "en panne".
_listening = {}


def probe_port(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def probe_tick():
    """Un tour de sonde sur les applications en cours d'execution."""
    apps = load()
    for name in list(_listening):
        if name not in apps or not is_running(name):
            _listening.pop(name, None)
    for name, a in apps.items():
        if is_running(name):
            _listening[name] = probe_port(a["port"])


def is_crash_looping(name):
    hist = [t for t in _restart_history.get(name, []) if time.time() - t < RESTART_WINDOW]
    return len(hist) >= RESTART_MAX_ATTEMPTS


def start_monitor_thread():
    def _loop():
        while True:
            time.sleep(10)
            try:
                monitor_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans le moniteur de redemarrage : {e}", flush=True)
            try:
                serveur_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans la mesure du serveur : {e}", flush=True)
            # Sonde a la suite du redemarrage automatique, dans le meme
            # thread : une app qui vient d'etre relancee n'ecoute pas encore,
            # et la sonder au tour suivant evite un faux "ne repond pas" a
            # chaque redemarrage.
            try:
                probe_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans la sonde d'ecoute : {e}", flush=True)
            # Dagster : service externe, suivi en lecture seule.
            try:
                dagster_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans la surveillance de Dagster : {e}", flush=True)
            try:
                mailpit_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans la surveillance de Mailpit : {e}", flush=True)
            # Les alertes en dernier, et dans le meme thread : un envoi SMTP
            # peut prendre jusqu'a 20 secondes, mais il n'a lieu qu'une fois
            # l'incident deja constate -- le redemarrage automatique a donc
            # deja eu lieu, et rien d'urgent n'attend derriere.
            try:
                alerte_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans les alertes : {e}", flush=True)
            try:
                suivi_push_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans les notifications de suivi : {e}", flush=True)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# --------------------------- sauvegarde de l'etat ---------------------------
#
# Il n'y en avait aucune. Rien ne copiait utilisateurs.json, apps.json ni le
# journal des acces : un disque perdu, et il fallait tout redeclarer de
# memoire -- les comptes, les droits par projet, les applications, les
# categories, l'adresse publique. Postgres, lui, est sauvegarde par son
# propre conteneur (voir postgres/entrypoint.sh) : pg_dump refuse de
# sauvegarder un serveur plus recent que lui, et c'est le seul endroit ou il
# a la bonne version. Chacun sauvegarde ce qu'il est SEUL a pouvoir
# sauvegarder.
#
# Ce qui n'entre pas dans l'archive :
#
#   les journaux d'applications (logs/)  ils se regenerent, et ils pesent
#   credentials.env                      il n'est pas dans ce dossier, et
#                                        c'est voulu : l'archive se recopie,
#                                        se transporte, se depose ailleurs.
#                                        Y mettre le mot de passe du panneau
#                                        et la cle de session reviendrait a
#                                        les diffuser avec elle.
SAUVEGARDES_DIR = os.environ.get("APP_MANAGER_SAUVEGARDES", "/sauvegardes")
SAUVEGARDE_HEURES = int(os.environ.get("APP_MANAGER_SAUVEGARDE_HEURES", "24"))
SAUVEGARDES_GARDEES = int(os.environ.get("APP_MANAGER_SAUVEGARDES_GARDEES", "7"))
SAUVEGARDE_PREFIXE = "panneau-"


def sauvegardes_actives():
    """Le dossier n'existe que si le volume est monte.

    Sans lui, l'archive irait dans la couche du conteneur et disparaitrait au
    premier « docker compose down » -- une sauvegarde qui s'efface avec ce
    qu'elle sauvegarde est pire que rien, parce qu'on croit en avoir une.
    """
    return SAUVEGARDE_HEURES > 0 and os.path.isdir(SAUVEGARDES_DIR)


# Ce que l'archive laisse dehors, et pourquoi :
#
#   logs/   les journaux d'applications. Ils se regenerent, et ils sont de
#           loin ce qui pese le plus.
#   home/   le dossier personnel de chaque application. C'est un cache (npm,
#           pip), pas de l'etat -- et il est ECRIVABLE PAR LES APPLICATIONS.
#           L'y inclure laisserait n'importe laquelle d'entre elles decider
#           du poids de la sauvegarde, et y deposer ce qu'elle veut.
ARCHIVE_EXCLUS = ("logs", "home")


def _filtre_de_l_archive(info):
    """tarfile nomme les membres « ./quelque-chose » quand la racine est
    ajoutee sous le nom « . » : le prefixe se retire ici, sinon la
    comparaison porte sur le point et ne filtre rien."""
    chemin = info.name[2:] if info.name.startswith("./") else info.name
    if chemin.split("/")[0] in ARCHIVE_EXCLUS or chemin.endswith((".log", ".log.1")):
        return None
    # La base vivante ne se copie pas fichier par fichier : une ecriture en
    # cours, ou ce qui attend encore dans le -wal, donnerait une copie
    # incoherente. L'archive en recoit un instantane (voir plus bas).
    if chemin in ("codelab.db", "codelab.db-wal", "codelab.db-shm"):
        return None
    return info


def sauvegarder_l_etat():
    """Une archive du dossier d'etat, verifiee, puis la rotation.

    Ecrite a cote puis renommee : un « docker compose down » au milieu de
    l'ecriture laisserait sinon une archive tronquee portant l'heure la plus
    recente -- celle qu'on choisirait pour restaurer.

    VERIFIEE, comme les dumps Postgres : l'archive est relue entierement
    avant d'etre gardee. Une sauvegarde qu'on decouvre illisible le jour de
    la restauration ne vaut pas mieux que pas de sauvegarde.
    """
    horodatage = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    nom = f"{SAUVEGARDE_PREFIXE}{horodatage}.tar.gz"
    final = os.path.join(SAUVEGARDES_DIR, nom)
    provisoire = final + ".partielle"
    instantane = os.path.join(SAUVEGARDES_DIR, f".codelab-{horodatage}.db")
    try:
        # Un instantane coherent de la base, pris par SQLite lui-meme, meme
        # pendant des ecritures.
        stockage.sauvegarder_vers(DB_FILE, instantane)
        with tarfile.open(provisoire, "w:gz") as archive:
            archive.add(STATE_DIR, arcname=".", filter=_filtre_de_l_archive)
            archive.add(instantane, arcname="./codelab.db")
    finally:
        try:
            os.remove(instantane)
        except OSError:
            pass
    with tarfile.open(provisoire) as archive:
        if not archive.getmembers():
            raise OSError("archive vide")
    os.chmod(provisoire, 0o600)
    os.replace(provisoire, final)
    _rotation_sauvegardes()
    return final


def _rotation_sauvegardes():
    """Les N plus recentes, et rien d'autre.

    Le tri est sur le NOM : il porte la date en AAAAMMJJ-HHMMSS, donc
    l'ordre alphabetique est l'ordre chronologique -- y compris au passage
    d'une annee, la ou un tri sur la date de fichier suivrait une copie.
    """
    try:
        archives = sorted(n for n in os.listdir(SAUVEGARDES_DIR)
                          if n.startswith(SAUVEGARDE_PREFIXE) and n.endswith(".tar.gz"))
    except OSError:
        return
    for vieille in archives[:-SAUVEGARDES_GARDEES] if SAUVEGARDES_GARDEES > 0 else []:
        try:
            os.remove(os.path.join(SAUVEGARDES_DIR, vieille))
        except OSError:
            pass


# ------------------------- LE CODE DE /workspace -------------------------
#
# Les bases et l'etat du panneau etaient sauvegardes ; le CODE des projets ne
# l'etait pas. Tant qu'un projet est pousse sur Git, rien n'est perdu -- mais
# rien ne le verifie, et un projet commence un soir sans « git init » vit
# seulement sur ce disque.
#
# Une archive par semaine, quatre gardees. Sans ce qui se reconstruit
# (dependances, caches, builds) ni les fichiers de plus de 50 Mo (jeux de
# donnees, binaires) : l'archive doit rester petite pour qu'on la copie
# ailleurs sans y penser. Relue avant d'etre gardee, comme les autres.
WORKSPACE_PREFIXE = "workspace-"
WORKSPACE_JOURS = int(os.environ.get("APP_MANAGER_WORKSPACE_JOURS", "7"))
WORKSPACE_GARDEES = 4
WORKSPACE_FICHIER_MAX = 50 * 1024 * 1024
WORKSPACE_EXCLUS = {"node_modules", ".venv", "venv", "__pycache__", ".cache",
                    ".npm", "dist", "build", ".next", ".pytest_cache",
                    ".mypy_cache", ".vscode-server", ".codex"}


def _filtre_workspace(membre):
    parties = membre.name.split("/")
    if any(p in WORKSPACE_EXCLUS for p in parties):
        return None
    if membre.isfile() and membre.size > WORKSPACE_FICHIER_MAX:
        return None
    # Les secrets d'un projet (.env) restent dans l'archive : sans eux, le
    # projet restaure ne demarre pas. L'archive est donc en 0600, comme
    # celle de l'etat du panneau.
    return membre


def workspace_a_sauvegarder():
    """Vrai si la derniere archive a plus de WORKSPACE_JOURS jours."""
    if WORKSPACE_JOURS <= 0 or not os.path.isdir(ROOT):
        return False
    try:
        archives = sorted(n for n in os.listdir(SAUVEGARDES_DIR)
                          if n.startswith(WORKSPACE_PREFIXE) and n.endswith(".tar.gz"))
    except OSError:
        return False
    if not archives:
        return True
    age = time.time() - os.path.getmtime(os.path.join(SAUVEGARDES_DIR, archives[-1]))
    return age >= WORKSPACE_JOURS * 86400


def sauvegarder_le_workspace():
    horodatage = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    final = os.path.join(SAUVEGARDES_DIR, f"{WORKSPACE_PREFIXE}{horodatage}.tar.gz")
    provisoire = final + ".partielle"
    try:
        with tarfile.open(provisoire, "w:gz") as archive:
            archive.add(ROOT, arcname=".", filter=_filtre_workspace)
        with tarfile.open(provisoire) as archive:
            if not archive.getmembers():
                raise OSError("archive vide")
        os.chmod(provisoire, 0o600)
        os.replace(provisoire, final)
    except BaseException:
        try:
            os.remove(provisoire)
        except OSError:
            pass
        raise
    archives = sorted(n for n in os.listdir(SAUVEGARDES_DIR)
                      if n.startswith(WORKSPACE_PREFIXE) and n.endswith(".tar.gz"))
    for vieille in archives[:-WORKSPACE_GARDEES]:
        try:
            os.remove(os.path.join(SAUVEGARDES_DIR, vieille))
        except OSError:
            pass
    return final


# ------------------------ LA SURVEILLANCE EXTERNE ------------------------
#
# Toutes les alertes de la stack partent DE la machine : si elle s'eteint --
# coupure de courant, disque mort, box en panne --, personne ne previent
# personne. Seul un service EXTERIEUR peut constater un silence.
#
# Le principe est celui du « dead man's switch » : le panneau appelle une
# adresse toutes les cinq minutes, et le service exterieur (healthchecks.io,
# gratuit jusqu'a 20 surveillances, ou tout service compatible) envoie un
# mail quand les appels cessent. Aucun port a ouvrir : c'est la machine qui
# appelle.
SURVEILLANCE_FILE = os.path.join(STATE_DIR, "surveillance.json")
SURVEILLANCE_INTERVALLE = 300
_surveillance_etat = {"quand": None, "ok": None, "detail": ""}


def lire_surveillance():
    d = reglage("surveillance", {}) or {}
    url = d.get("url") if isinstance(d, dict) else ""
    return url if isinstance(url, str) and url_surveillance_valide(url) else ""


def url_surveillance_valide(url):
    """https seulement, sans espace, d'une longueur raisonnable. C'est une
    adresse que le panneau appellera tout seul, a intervalles reguliers."""
    if not isinstance(url, str) or len(url) > 300 or any(c.isspace() for c in url):
        return False
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname)


def ecrire_surveillance(url):
    # Au coffre : l'adresse de ping suffit a faire croire le serveur vivant.
    ecrire_reglage("surveillance", {"url": url} if url else None)


def signaler_vie(url=None):
    """Un appel a l'adresse de surveillance. (ok, detail), jamais d'exception."""
    url = url if url is not None else lire_surveillance()
    if not url:
        return None, "aucune adresse"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CodeLab"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok, detail = 200 <= r.status < 300, f"HTTP {r.status}"
    except Exception as e:                                        # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    _surveillance_etat.update(quand=time.time(), ok=ok, detail=detail)
    if not ok:
        print(f"[app-manager] surveillance externe injoignable : {detail}", flush=True)
    return ok, detail


def demarrer_la_surveillance_externe():
    def _boucle():
        while True:
            try:
                signaler_vie()
            except Exception as e:                                # noqa: BLE001
                print(f"[app-manager] surveillance externe : {e}", flush=True)
            time.sleep(SURVEILLANCE_INTERVALLE)
    threading.Thread(target=_boucle, daemon=True).start()


@flask_app.get("/api/surveillance-externe")
@require_admin
def api_surveillance_lire():
    etat = dict(_surveillance_etat)
    return jsonify({"url": lire_surveillance(), "intervalle": SURVEILLANCE_INTERVALLE,
                    "dernier_appel": etat["quand"], "ok": etat["ok"],
                    "detail": etat["detail"]})


@flask_app.post("/api/surveillance-externe")
@require_admin
def api_surveillance_ecrire():
    """Enregistre l'adresse et l'appelle AUSSITOT : une adresse mal copiee
    se voit ici, pas le jour ou la machine tombe sans que rien ne sonne."""
    url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
    if url and not url_surveillance_valide(url):
        return jsonify({"error": "Adresse invalide : elle doit commencer par "
                                 "https:// (celle que donne healthchecks.io)."}), 400
    if url:
        ok, detail = signaler_vie(url)
        if not ok:
            return jsonify({"error": "Cette adresse n'a pas répondu, rien n'a "
                                     "été enregistré.", "detail": detail}), 400
    try:
        ecrire_surveillance(url)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Réglage non enregistré : {e}"}), 500
    if not url:
        _surveillance_etat.update(quand=None, ok=None, detail="")
    journaliser("reglage", qui=utilisateur_courant() or NOM_ADMIN,
                action="surveillance externe " + ("activee" if url else "retiree"),
                ip=_adresse_client())
    return jsonify({"ok": True, "url": url})


# ------------------- les sauvegardes, vues du panneau -------------------
#
# Elles ne sortaient jamais du serveur, et rien ne le disait : un disque qui
# lache emportait le panneau ET ses sauvegardes. Parametres > Securite les
# montre, permet de telecharger la derniere, et ALERTE tant qu'aucune copie
# n'est partie ailleurs depuis une semaine -- sauf si l'administrateur dit
# qu'il copie deja le dossier par ses propres moyens (NAS, rsync, restic).

SAUVEGARDES_SUIVI_FILE = os.path.join(STATE_DIR, "sauvegardes-suivi.json")
SAUVEGARDE_NOM = re.compile(r"^(panneau|workspace)-\d{8}-\d{6}\.tar\.gz$")
SAUVEGARDE_COPIE_JOURS = 7


def lire_suivi_sauvegardes():
    try:
        with open(SAUVEGARDES_SUIVI_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_suivi_sauvegardes(d):
    tmp = SAUVEGARDES_SUIVI_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, SAUVEGARDES_SUIVI_FILE)


def archives_de_sauvegarde():
    """Les archives du dossier, la plus recente d'abord."""
    if not os.path.isdir(SAUVEGARDES_DIR):
        return []
    liste = []
    for nom in os.listdir(SAUVEGARDES_DIR):
        chemin = os.path.join(SAUVEGARDES_DIR, nom)
        if SAUVEGARDE_NOM.match(nom) and os.path.isfile(chemin):
            st = os.stat(chemin)
            liste.append({"nom": nom, "genre": nom.split("-", 1)[0],
                          "taille": st.st_size, "date": int(st.st_mtime)})
    return sorted(liste, key=lambda a: a["date"], reverse=True)


def etat_des_sauvegardes():
    archives = archives_de_sauvegarde()
    suivi = lire_suivi_sauvegardes()
    copie = suivi.get("derniere_copie")
    externe = bool(suivi.get("copie_externe"))
    maintenant = time.time()
    # L'alerte : des archives existent, et aucune n'est sortie du serveur
    # depuis une semaine (ou jamais), sans copie declaree ailleurs.
    alerte = bool(archives) and not externe and (
        not copie or maintenant - copie > SAUVEGARDE_COPIE_JOURS * 86400)
    return {"actives": sauvegardes_actives(), "toutes_les_heures": SAUVEGARDE_HEURES,
            "gardees": SAUVEGARDES_GARDEES, "archives": archives[:12],
            "derniere_copie": copie, "copie_externe": externe,
            "alerte_hors_serveur": alerte, "jours": SAUVEGARDE_COPIE_JOURS}


def resume_sauvegardes():
    """Ce que le widget du hub affiche : la derniere archive, et l'alerte
    tant qu'aucune copie n'est sortie du serveur."""
    try:
        etat = etat_des_sauvegardes()
    except OSError:
        return None
    derniere = etat["archives"][0] if etat["archives"] else None
    return {"actives": etat["actives"], "derniere": derniere,
            "alerte_hors_serveur": etat["alerte_hors_serveur"]}


@flask_app.get("/api/sauvegardes")
@require_admin
def api_sauvegardes():
    return jsonify(etat_des_sauvegardes())


@flask_app.get("/api/sauvegardes/<nom>")
@require_admin
def api_sauvegarde_telecharger(nom):
    """Une archive, en piece jointe. Seuls les noms produits par le panneau
    passent : ni chemin, ni autre fichier du dossier."""
    if not SAUVEGARDE_NOM.match(nom):
        return jsonify({"error": "Sauvegarde introuvable."}), 404
    chemin = os.path.join(SAUVEGARDES_DIR, nom)
    if not os.path.isfile(chemin):
        return jsonify({"error": "Sauvegarde introuvable."}), 404
    suivi = lire_suivi_sauvegardes()
    suivi["derniere_copie"] = int(time.time())
    try:
        ecrire_suivi_sauvegardes(suivi)
    except OSError:
        pass
    journaliser("sauvegarde", qui=utilisateur_courant() or NOM_ADMIN, action="telechargee",
                fichier=nom, ip=_adresse_client())
    return send_file(chemin, mimetype="application/gzip", as_attachment=True,
                     download_name=nom, max_age=0)


@flask_app.put("/api/sauvegardes/copie")
@require_admin
def api_sauvegardes_copie():
    """{externe: bool} -- l'administrateur copie deja le dossier ailleurs."""
    d = request.get_json(force=True, silent=True) or {}
    suivi = lire_suivi_sauvegardes()
    suivi["copie_externe"] = bool(d.get("externe"))
    ecrire_suivi_sauvegardes(suivi)
    return jsonify(etat_des_sauvegardes())


# ------------------------- la version de CodeLab -------------------------
#
# Rien ne disait quelle version tournait : « je ne vois pas les modifs »
# voulait souvent dire « l'image n'a pas ete retiree ». L'image porte son
# commit et sa date (CODELAB_VERSION, CODELAB_VERSION_DATE, poses par la CI
# au build) ; Parametres > Compte les affiche.
#
# AUCUN APPEL SORTANT : le panneau ne demande pas a GitHub s'il existe plus
# recent. C'est un choix -- un serveur qui contacte l'exterieur de lui-meme
# est un risque a prendre sans necessite. La page propose un lien vers les
# nouveautes ; c'est le navigateur de l'administrateur qui l'ouvre, s'il le
# veut.

CODELAB_VERSION = os.environ.get("CODELAB_VERSION", "").strip()
CODELAB_VERSION_DATE = os.environ.get("CODELAB_VERSION_DATE", "").strip()
DEPOT_CODELAB = "studio-codelab/codelab"
SHA_VALIDE = re.compile(r"^[0-9a-f]{40}$")


def etat_de_la_version():
    dev = not SHA_VALIDE.match(CODELAB_VERSION)
    return {"sha": "" if dev else CODELAB_VERSION, "court": "" if dev else CODELAB_VERSION[:7],
            "date": CODELAB_VERSION_DATE, "dev": dev, "depot": DEPOT_CODELAB}


@flask_app.get("/api/version")
@require_admin
def api_version():
    return jsonify(etat_de_la_version())


def demarrer_les_sauvegardes():
    if not sauvegardes_actives():
        if SAUVEGARDE_HEURES <= 0:
            print("[app-manager] sauvegardes desactivees "
                  "(APP_MANAGER_SAUVEGARDE_HEURES=0).", flush=True)
        else:
            print(f"[app-manager] pas de volume de sauvegarde monte sur "
                  f"{SAUVEGARDES_DIR} : l'etat du panneau ne sera pas "
                  f"sauvegarde.", flush=True)
        return

    def _boucle():
        while True:
            try:
                if workspace_a_sauvegarder():
                    chemin = sauvegarder_le_workspace()
                    journaliser("sauvegarde", fichier=os.path.basename(chemin),
                                octets=os.path.getsize(chemin))
            except Exception as e:
                print(f"[app-manager] archive du workspace impossible : {e}", flush=True)
            try:
                chemin = sauvegarder_l_etat()
                journaliser("sauvegarde", fichier=os.path.basename(chemin),
                            octets=os.path.getsize(chemin))
            except Exception as e:
                # Jamais d'exception qui remonte : le fil mourrait, et il n'y
                # aurait plus de sauvegarde du tout -- silencieusement.
                print(f"[app-manager] sauvegarde impossible : {e}", flush=True)
                journaliser("sauvegarde", erreur=str(e))
            time.sleep(SAUVEGARDE_HEURES * 3600)

    threading.Thread(target=_boucle, daemon=True).start()


# ------------------------------ alertes mail ------------------------------
#
# Le panneau surveille deja les applications et les redemarre quand elles
# tombent (voir monitor_tick). Mais il fallait avoir le panneau sous les yeux
# pour le savoir : une application qui s'arrete la nuit reste arretee jusqu'a
# ce qu'on pense a regarder. Un mail transforme cette surveillance passive en
# alerte.
#
# Deux fichiers, pour une seule raison : les secrets ne vont pas au meme
# endroit que le reste.
#
#   credentials.env, bloc "codelab-alertes" : le serveur SMTP et son mot de
#     passe. C'est le fichier en 0600, et c'est DEJA le bloc que lit le
#     capteur d'alerte de Dagster -- une seule configuration SMTP pour toute
#     la stack, pas deux a tenir a jour.
#   alertes.json, dans le dossier d'etat : l'interrupteur et les
#     destinataires. Une adresse de destination n'est pas un secret, et la
#     garder hors du fichier de secrets evite de le reecrire pour un
#     changement anodin.
ALERTES_FILE = os.path.join(STATE_DIR, "alertes.json")
BLOC_ALERTES = "codelab-alertes"

# Nombre de lignes de journal jointes au mail. Assez pour reconnaitre une
# trace d'exception, pas assez pour rendre le mail illisible sur telephone.
ALERTE_LOG_LIGNES = 25


def lire_alertes():
    """L'interrupteur et l'ancienne liste globale, lus dans le coffre. Jamais
    d'exception : le moniteur doit tourner quoi qu'il arrive."""
    d = reglage("alertes", {}) or {}
    if not isinstance(d, dict):
        d = {}
    return {
        "actif": bool(d.get("actif")),
        "destinataires": [a for a in d.get("destinataires", []) if isinstance(a, str) and a.strip()],
    }


def ecrire_alertes(actif, destinataires):
    ecrire_reglage("alertes", {"actif": bool(actif),
                               "destinataires": list(destinataires)})


def ecrire_bloc_alertes(valeurs):
    """Met a jour le bloc SMTP de credentials.env, SANS perdre le reste.

    Le piege, et il est serieux : upsert_shared_block REMPLACE le bloc en
    entier. Lui passer la seule cle qu'on veut changer effacerait toutes les
    autres -- dont le serveur d'envoi et son mot de passe, c'est-a-dire la
    configuration d'origine qui sert de repli. On relit donc les valeurs
    presentes et l'on n'ecrase que celles fournies.

    Meme bloc que celui documente pour le capteur Dagster : configurer les
    alertes depuis le panneau configure donc aussi celles de Dagster.
    """
    commentaires = [
        "# Serveur d'envoi des alertes CodeLab, partage par le panneau",
        "# (application tombee) et par le capteur Dagster (run en echec).",
        "# C'est la configuration D'ORIGINE : le panneau ne l'ecrase jamais,",
        "#   il la garde en repli si celle saisie dans l'interface echoue.",
        "# Modifiable depuis le panneau : Parametres > E-mail.",
        "# SMTP_TLS : starttls (defaut, port 587) | ssl (port 465) | none.",
        "# SMTP_USER et SMTP_PASSWORD sont optionnels : un relais interne peut",
        "#   ne pas demander d'authentification.",
        "# ALERTE_ADMIN : l'adresse qui recoit TOUTES les alertes, posee ici a",
        "#   l'installation. Celle reglee dans le panneau l'emporte : elle est",
        "#   en base, chiffree, et n'est jamais recopiee dans ce fichier.",
    ]
    cles = list(CHAMPS_SMTP.values()) + ["ALERTE_ADMIN"]
    fusion = {}
    for cle in cles:
        valeur = valeurs.get(cle) if cle in valeurs else read_shared_value(cle)
        if str(valeur or "").strip():
            fusion[cle] = valeur
    return upsert_shared_block(BLOC_ALERTES, commentaires, fusion)


# --------------------- deux configurations d'envoi ---------------------
#
# POURQUOI DEUX. credentials.env porte la configuration d'ORIGINE : celle
# posee a l'installation, par le compose ou a la main. Elle a une qualite que
# rien d'autre n'a -- elle a toujours marche, et personne ne l'a touchee
# depuis une page web. C'est donc elle qu'on garde en repli.
#
# Une configuration saisie depuis le panneau vit a cote, dans smtp.json, et
# n'ECRASE JAMAIS la premiere. Sans cette separation, se tromper d'un
# caractere dans un nom de serveur depuis une page supprimait le seul moyen
# de prevenir qu'une application est tombee -- et l'on ne s'en apercevait
# qu'au premier incident, c'est-a-dire au pire moment.
#
# Ce qui en decoule :
#
#   a l'ENREGISTREMENT, une configuration personnalisee doit prouver qu'elle
#     fonctionne : on se connecte reellement au serveur, on chiffre, on
#     s'authentifie. Si cela echoue, rien n'est enregistre et le message du
#     serveur est remonte tel quel. C'est ce qui remplace l'ancien bouton
#     "envoyer un mail de test" -- un test qu'il fallait penser a lancer, et
#     dont l'oubli ne se voyait pas ;
#   a l'ENVOI, si la personnalisee echoue malgre tout (mot de passe revoque,
#     serveur eteint, quota), on repart aussitot sur celle d'origine. Une
#     alerte qui ne sort pas ajoute une panne a celle qu'elle signale.
# Vingt secondes : au-dela, ce n'est plus une lenteur mais une panne, et ni
# le moniteur ni une page ne doivent rester suspendus a un serveur muet.
SMTP_DELAI = 20

SMTP_FILE = os.path.join(STATE_DIR, "smtp.json")

# Les champs d'une configuration d'envoi, et la variable de credentials.env
# qui porte chacun. Une seule liste : ajouter un champ ici le fait exister
# partout, plutot que dans trois fonctions a tenir d'accord.
CHAMPS_SMTP = {
    "host": "SMTP_HOST",
    "port": "SMTP_PORT",
    "tls": "SMTP_TLS",
    "user": "SMTP_USER",
    "password": "SMTP_PASSWORD",
    "expediteur": "ALERTE_FROM",
}

# Les cles que le panneau ecrit POUR LUI-MEME dans credentials.env. Elles y
# vivent parce que le capteur Dagster les lit dans ce fichier, pas parce
# qu'une application aurait a les connaitre.
#
# Elles sont donc retirees de secrets_partages() : une application est du
# code arbitraire tournant sous un autre uid, et lui remettre SMTP_PASSWORD
# lui donnerait de quoi expedier du courrier au nom de CodeLab -- une adresse
# de confiance, celle-la meme d'ou partent les alertes. ALERTE_ADMIN n'est
# pas un secret, mais c'est l'adresse de l'administrateur : elle n'a rien a
# faire dans l'environnement d'une application non plus.
#
# La regle existait deja pour le prefixe APP_MANAGER_ ; ce bloc lui avait
# echappe, faute de porter ce prefixe. Le renommer n'etait pas possible : le
# capteur Dagster et la documentation lisent ces noms-la.
CLES_PANNEAU = set(CHAMPS_SMTP.values()) | {"ALERTE_ADMIN"}


def _port_smtp(brut, defaut=587):
    try:
        port = int(str(brut or "").strip())
    except (TypeError, ValueError):
        return defaut
    return port if 1 <= port <= 65535 else defaut


def _normalise_smtp(brut):
    """Met une configuration en forme, quelle que soit sa provenance."""
    brut = brut or {}
    tls = str(brut.get("tls") or "starttls").strip().lower()
    if tls not in ("starttls", "ssl", "none"):
        tls = "starttls"
    cfg = {
        "host": str(brut.get("host") or "").strip(),
        "port": _port_smtp(brut.get("port")),
        "tls": tls,
        "user": str(brut.get("user") or "").strip(),
        "password": str(brut.get("password") or ""),
        "expediteur": str(brut.get("expediteur") or "").strip(),
    }
    # Gmail et la plupart des fournisseurs refusent d'expedier au nom d'une
    # autre adresse que celle du compte : l'expediteur suit donc l'identifiant,
    # sauf mention explicite.
    if not cfg["expediteur"]:
        cfg["expediteur"] = cfg["user"]
    return cfg


def smtp_origine():
    """La configuration de credentials.env. Celle qui sert de repli."""
    return _normalise_smtp({
        cle: read_shared_value(env) for cle, env in CHAMPS_SMTP.items()
    })


def lire_smtp_personnalise():
    """La configuration saisie depuis le panneau, ou None.

    Jamais d'exception : un fichier illisible doit faire retomber sur la
    configuration d'origine, pas empecher le panneau de demarrer.
    """
    d = reglage("smtp")
    if not isinstance(d, dict) or not str(d.get("host") or "").strip():
        return None
    return _normalise_smtp(d)


def ecrire_smtp_personnalise(cfg):
    # Au coffre, chiffre : il porte le mot de passe du serveur d'envoi.
    ecrire_reglage("smtp", cfg)


def effacer_smtp_personnalise():
    """Revenir a la configuration d'origine. Toujours permis -- c'est la
    marche arriere, et elle ne doit dependre d'aucune condition."""
    try:
        ecrire_reglage("smtp", None)
    except (OSError, RuntimeError) as e:
        print(f"[app-manager] configuration d'envoi non effacee : {e}", flush=True)


BLOC_RELAIS_MAIL = "codelab-relais-mail"


def migrer_vers_le_coffre():
    """Range au coffre ce que les versions precedentes gardaient en clair.

    Chaque source n'est supprimee qu'APRES une ecriture reussie au coffre :
    une migration qui echoue laisse tout en place, et reessaie au prochain
    demarrage.
    """
    if not _cle_chiffrement:
        return

    def _json(chemin):
        try:
            with open(chemin) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    for chemin, cle in ((SMTP_FILE, "smtp"), (ALERTES_FILE, "alertes"),
                        (SURVEILLANCE_FILE, "surveillance")):
        if not os.path.exists(chemin):
            continue
        valeur = _json(chemin)
        try:
            if isinstance(valeur, dict) and reglage(cle) is None:
                ecrire_reglage(cle, valeur)
            os.remove(chemin)
            print(f"[app-manager] {os.path.basename(chemin)} range au coffre "
                  f"(chiffre) puis supprime.", flush=True)
        except (OSError, RuntimeError) as e:
            print(f"[app-manager] {chemin} non migre : {e}", flush=True)

    # L'adresse d'administration, que le panneau ecrivait dans credentials.env.
    admin = _adresses(read_shared_value("ALERTE_ADMIN") or "")
    if admin:
        try:
            if not reglage("alertes_admin"):
                ecrire_reglage("alertes_admin", admin)
            ecrire_bloc_alertes({"ALERTE_ADMIN": ""})
            print("[app-manager] adresse d'alerte rangee au coffre.", flush=True)
        except (OSError, RuntimeError) as e:
            print(f"[app-manager] adresse d'alerte non migree : {e}", flush=True)

    # Les destinataires propres a chaque application, gardes dans apps.json.
    apps = load()
    anciens = {n: _adresses(a.get("alertes")) for n, a in apps.items()
               if isinstance(a, dict) and "alertes" in a}
    if anciens:
        try:
            par_app = reglage("alertes_par_app", {}) or {}
            for n, adresses in anciens.items():
                if adresses and n not in par_app:
                    par_app[n] = adresses
            ecrire_reglage("alertes_par_app", par_app or None)
            for n in anciens:
                apps[n].pop("alertes", None)
            save(apps)
            print("[app-manager] destinataires d'alerte ranges au coffre.", flush=True)
        except (OSError, RuntimeError) as e:
            print(f"[app-manager] destinataires d'alerte non migres : {e}", flush=True)
    # Ce qui vient d'etre retire en clair ne doit pas survivre dans le
    # journal d'ecriture de la base.
    stockage.vider_le_journal(DB_FILE)


# ------------------ import des anciens fichiers (une fois) ------------------
#
# Avant la base, chaque donnee avait son fichier. Au premier demarrage apres
# la mise a jour, chacun est lu, verse dans sa table, puis RENOMME en
# « .migre » -- jamais supprime : c'est la sauvegarde de l'etat d'avant, et le
# moyen de revenir en arriere. Une table deja remplie n'est pas ecrasee : un
# import joue deux fois ne fait rien de plus.

def _lire_fichier_json(chemin, defaut):
    try:
        with open(chemin) as f:
            d = json.load(f)
        return d if isinstance(d, type(defaut)) else defaut
    except (OSError, ValueError):
        return defaut


def _lire_fichier_lignes(chemin):
    lignes = []
    for c in (chemin + ".1", chemin):
        try:
            with open(c, errors="replace") as f:
                for ligne in f:
                    try:
                        e = json.loads(ligne)
                    except ValueError:
                        continue
                    if isinstance(e, dict):
                        lignes.append(e)
        except OSError:
            continue
    return lignes


def _mettre_de_cote(*chemins):
    for chemin in chemins:
        for c in (chemin, chemin + ".1"):
            if os.path.exists(c):
                try:
                    os.replace(c, c + ".migre")
                except OSError:
                    pass


def _table_vide(table):
    return not stockage.lire(DB_FILE, f"SELECT 1 FROM {table} LIMIT 1")


def _verser_si_vide(fichier, table, defaut, verser, libelle, faits):
    """Un ancien fichier JSON verse dans sa table, si elle est encore vide.

    Une table deja remplie gagne toujours : le fichier n'est alors qu'un
    reste d'une installation deja migree. Dans les deux cas il est mis de
    cote (« .migre »), jamais supprime.
    """
    if not os.path.exists(fichier):
        return
    if _table_vide(table):
        verser(_lire_fichier_json(fichier, defaut))
        if libelle:
            faits.append(libelle)
    _mettre_de_cote(fichier)


def _importer_coffre(faits):
    """L'ancien coffre chiffre, cle par cle : une cle deja dans la base gagne."""
    if not (os.path.exists(COFFRE_FILE) and _cle_chiffrement):
        return
    try:
        with open(COFFRE_FILE, "rb") as f:
            ancien = dechiffrer(f.read())
        with _coffre_verrou:
            presentes = set(_coffre_donnees())
            for cle, valeur in (ancien or {}).items():
                if cle not in presentes:
                    _coffre_ecrire(cle, valeur)
            _coffre["donnees"] = None
        _mettre_de_cote(COFFRE_FILE)
        faits.append("reglages chiffres")
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] import du coffre impossible ({type(e).__name__}) : "
              f"le fichier reste en place.", flush=True)


def _importer_comptes(faits):
    """Les comptes, et leurs adresses gardees a part par une version
    intermediaire. Rend False si l'ecriture echoue : la suite de l'import
    attend alors le prochain demarrage."""
    if not os.path.exists(UTILISATEURS_FILE):
        return True
    if _table_vide("comptes"):
        comptes = _lire_fichier_json(UTILISATEURS_FILE, {})
        emails = reglage("emails_comptes", {}) or {}
        for nom, c in comptes.items():
            if isinstance(c, dict) and emails.get(nom) and not c.get("email"):
                c["email"] = emails[nom]
        try:
            ecrire_utilisateurs({n: c for n, c in comptes.items() if isinstance(c, dict)})
            faits.append("comptes")
        except (OSError, RuntimeError) as e:
            print(f"[app-manager] import des comptes impossible : {e}", flush=True)
            return False
    # La copie gardee ne doit pas garder ce que la base chiffre : une tres
    # ancienne installation avait les adresses en clair dans ce fichier
    # (regle 16 de SECURITE.md : aucune adresse en clair sur le disque).
    try:
        brut = _lire_fichier_json(UTILISATEURS_FILE, {})
        for c in brut.values():
            if isinstance(c, dict):
                c.pop("email", None)
        with open(UTILISATEURS_FILE, "w") as f:
            json.dump(brut, f, indent=2)
        os.chmod(UTILISATEURS_FILE, 0o600)
    except OSError:
        pass
    _mettre_de_cote(UTILISATEURS_FILE)
    return True


def _importer_journal(faits):
    """Le journal des acces, ligne par ligne (fichier courant et sa rotation
    « .1 ») ; INSERT OR IGNORE sur l'identifiant : un import repris apres une
    coupure ne double rien."""
    if not (os.path.exists(ACCES_FILE) or os.path.exists(ACCES_FILE + ".1")):
        return
    evenements = _lire_fichier_lignes(ACCES_FILE)
    with stockage.transaction(DB_FILE) as cx:
        for e in evenements:
            reste = {k: v for k, v in e.items()
                     if k not in ("id", "ts", "genre", "qui", "app", "ip")}
            cx.execute("INSERT OR IGNORE INTO journal (id, ts, genre, qui, app, ip, "
                       "details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (e.get("id") or secrets.token_hex(12), int(e.get("ts") or 0),
                        e.get("genre") or "", e.get("qui"), e.get("app"), e.get("ip"),
                        json.dumps(reste, ensure_ascii=False)))
    _mettre_de_cote(ACCES_FILE)
    faits.append(f"journal ({len(evenements)} evenements)")


def importer_anciens_fichiers():
    """Verse les anciens fichiers JSON dans la base. Rend la liste importee.

    RETROCOMPATIBILITE : les installations d'avant codelab.db gardaient leur
    etat en fichiers JSON. L'import tourne a chaque demarrage et ne fait rien
    quand il n'y a plus de fichier. Il pourra partir quand plus aucune
    installation ne demarre d'une version anterieure a la base.
    L'ORDRE COMPTE : le coffre d'abord (les adresses des comptes y sont), et
    un echec sur les comptes arrete la suite jusqu'au prochain demarrage.
    """
    faits = []
    _importer_coffre(faits)
    _verser_si_vide(APPS_FILE, "applications", {}, save, "applications", faits)
    if not _importer_comptes(faits):
        return faits
    _verser_si_vide(PASSKEYS_FILE, "cles_acces", {}, ecrire_passkeys, "cles d'acces", faits)
    _verser_si_vide(CATEGORIES_FILE, "categories", [],
                    lambda brut: ecrire_categories([c for c in map(categorie_propre, brut) if c]),
                    "categories", faits)
    _verser_si_vide(MASQUEES_FILE, "masquees", {}, ecrire_masquees, "rangements du hub", faits)

    if os.path.exists(EXPOSITION_FILE):
        if lire_parametre("exposition") is None:
            ecrire_exposition(_lire_fichier_json(EXPOSITION_FILE, {}))
            faits.append("exposition")
        _mettre_de_cote(EXPOSITION_FILE)

    _verser_si_vide(PROCESSUS_FILE, "processus", {}, _ecrire_processus, None, faits)
    _importer_journal(faits)

    if os.path.exists(MESSAGES_FILE) or os.path.exists(MESSAGES_FILE + ".1"):
        if _table_vide("messages"):
            reecrire_messages(_lire_fichier_lignes(MESSAGES_FILE))
            faits.append("messages")
        _mettre_de_cote(MESSAGES_FILE)

    stockage.vider_le_journal(DB_FILE)
    if faits:
        print("[app-manager] anciens fichiers importes dans codelab.db : "
              + ", ".join(faits) + ". Les originaux sont gardes en « .migre ».",
              flush=True)
    return faits


def publier_relais_mail():
    """Publie pour codelab-mailpit le serveur d'envoi a relayer.

    Celui que le panneau utilise lui-meme : la configuration saisie dans
    Parametres > E-mail si elle existe, celle d'origine sinon. Mailpit ne
    peut pas lire smtp.json, qui vit dans le dossier d'etat du panneau ; il
    lit ce bloc, et se relance tout seul quand il change. Une seule
    configuration d'envoi pour toute la stack, donc.

    Cles prefixees CODELAB_INTERNE_ : jamais transmises aux applications.
    Elles envoient par Mailpit, sans jamais voir ce mot de passe.
    """
    cfg = lire_smtp_personnalise() or smtp_origine()
    valeurs = {}
    if cfg.get("host"):
        valeurs = {
            "CODELAB_INTERNE_RELAIS_HOST": cfg["host"],
            "CODELAB_INTERNE_RELAIS_PORT": str(cfg["port"]),
            "CODELAB_INTERNE_RELAIS_TLS": cfg["tls"],
            "CODELAB_INTERNE_RELAIS_USER": cfg["user"],
            "CODELAB_INTERNE_RELAIS_PASSWORD": cfg["password"],
            "CODELAB_INTERNE_RELAIS_FROM": cfg["expediteur"],
        }
    commentaires = [
        "# Le serveur d'envoi que codelab-mailpit relaie : celui de",
        "#   Parametres > E-mail, recopie ici par le panneau. Ne pas editer :",
        "#   le panneau reecrit ce bloc a chaque enregistrement.",
        "# Jamais transmis aux applications (prefixe CODELAB_INTERNE_).",
    ]
    return upsert_shared_block(BLOC_RELAIS_MAIL, commentaires,
                               {k: v for k, v in valeurs.items() if v})


def smtp_incomplet(cfg):
    """Ce qui manque a cette configuration pour pouvoir envoyer."""
    manques = []
    if not cfg.get("host"):
        manques.append("serveur d'envoi")
    if not cfg.get("expediteur"):
        manques.append("adresse d'expedition")
    return manques


def verifier_smtp(cfg):
    """Se connecte VRAIMENT au serveur, et renvoie (ok, message).

    Connexion, chiffrement, authentification -- tout sauf l'envoi. C'est
    volontaire : une verification qui expedie un message oblige a choisir un
    destinataire, donc a deranger quelqu'un a chaque enregistrement, et finit
    par etre contournee. Tout ce qui peut echouer a l'envoi echoue deja ici,
    a l'exception du refus d'un destinataire precis.
    """
    manques = smtp_incomplet(cfg)
    if manques:
        return False, "Configuration incomplete : " + ", ".join(manques) + "."
    try:
        if cfg["tls"] == "ssl":
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                                  context=ssl.create_default_context(),
                                  timeout=SMTP_DELAI) as s:
                if cfg["user"] and cfg["password"]:
                    s.login(cfg["user"], cfg["password"])
            return True, ""
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_DELAI) as s:
            s.ehlo()
            if cfg["tls"] != "none":
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if cfg["user"] and cfg["password"]:
                s.login(cfg["user"], cfg["password"])
        return True, ""
    except Exception as e:
        # Le message du serveur est la seule chose qui aide vraiment ici
        # ("authentification refusee", "relais interdit", "nom inconnu") : on
        # le remonte tel quel plutot que de le resumer en "echec".
        return False, f"{type(e).__name__}: {e}"


def _adresses(brutes):
    """Nettoie une liste d'adresses saisies.

    Pas de validation stricte : un format valide n'est pas une adresse qui
    existe, et rien ici ne peut trancher cela. On retire les doublons et ce
    qui n'a manifestement pas la forme d'une adresse.
    """
    if isinstance(brutes, str):
        brutes = re.split(r"[,;\s]+", brutes)
    vues, propres = set(), []
    for a in brutes or []:
        a = (a or "").strip()
        if a and "@" in a and a not in vues:
            vues.add(a)
            propres.append(a)
    return propres


def alertes_admin():
    """L'adresse qui recoit TOUTES les alertes, quelle que soit l'application.

    Elle vit dans le bloc de credentials.env, a cote du serveur d'envoi : une
    installation peut donc la poser des le depart, et c'est le seul
    destinataire garanti le jour ou une application n'en declare aucun.

    Repli sur l'ancienne liste globale de alertes.json : avant les alertes
    par application, tous les destinataires etaient la. Sans ce repli, une
    installation existante aurait cesse d'etre prevenue a la mise a jour --
    silencieusement, et l'on ne s'en serait apercu qu'au premier incident.
    """
    # Celle reglee dans le panneau (au coffre), sinon celle posee a
    # l'installation dans credentials.env, sinon l'ancienne liste globale.
    return (_adresses(reglage("alertes_admin") or [])
            or _adresses(read_shared_value("ALERTE_ADMIN") or "")
            or _adresses(lire_alertes()["destinataires"]))


def config_smtp():
    """La configuration qui sera REELLEMENT utilisee, et ce qui lui manque.

    La personnalisee quand il y en a une, celle d'origine sinon. Renvoie
    (cfg, manquants) : cfg est utilisable si manquants est vide.
    """
    cfg = dict(lire_smtp_personnalise() or smtp_origine())
    cfg["destinataires"] = alertes_admin()
    manquants = smtp_incomplet(cfg)
    if not cfg["destinataires"]:
        manquants.append("adresse d'alerte de l'administrateur")
    return cfg, manquants


def smtp_utilisable():
    """(cfg, ok) pour un envoi a une adresse choisie.

    Distinct de config_smtp() : les alertes veulent en plus savoir a qui
    ecrire, alors qu'un code de verification part vers une adresse donnee.
    Sans cette distinction, un serveur d'envoi parfaitement configure
    passerait pour incomplet tant qu'aucune alerte n'est reglee.
    """
    cfg, _ = config_smtp()
    return cfg, not smtp_incomplet(cfg)


def envoi_mail_configure():
    """Un serveur d'envoi est-il regle ? Sans lui, aucun compte : un compte
    se connecte avec un code envoye par e-mail, et une invitation EST un
    e-mail. Creer un compte qui ne pourrait pas recevoir son code, ce
    serait le laisser entrer sans second facteur -- ou ne jamais entrer."""
    try:
        return smtp_utilisable()[1]
    except Exception:                                             # noqa: BLE001
        return False


SMTP_REQUIS = ("Règle d'abord un serveur d'envoi (Paramètres › E-mail) : sans lui, "
               "aucun compte ne peut recevoir son invitation ni ses codes de connexion.")


def _refus_sans_smtp():
    """409 si aucun serveur d'envoi n'est regle, None sinon."""
    if envoi_mail_configure():
        return None
    return jsonify({"error": SMTP_REQUIS, "smtp_requis": True}), 409


# LES MAILS DU PANNEAU PASSENT PAR MAILPIT, comme ceux des applications.
#
# Avant, le panneau parlait directement au serveur d'envoi : ses mails
# (alertes, codes, invitations) ne laissaient aucune trace, alors que ceux
# des applications se relisaient dans Mailpit. Desormais tout passe par le
# meme service : chaque mail se relit dans l'interface de Mailpit, le mode
# test vaut pour tout le monde, et le plafond d'envoi aussi. Mailpit relaie
# par le serveur de Parametres > E-mail, que le panneau lui publie deja
# (publier_relais_mail).
#
# Mailpit injoignable (service arrete, installation sans lui) : repli sur
# l'envoi direct, pour qu'une alerte ne tombe pas avec le service qui
# devait la porter.
#
# SAUF LES MAILS DE SECURITE. Un code de verification, de connexion ou de
# reinitialisation, un lien d'invitation, l'alerte d'une connexion
# administrateur inconnue : ce sont des secrets, ou des signaux qu'un
# intrus aurait interet a lire et a effacer. Garder leur copie dans Mailpit,
# que tout administrateur ouvre depuis le hub, ce serait laisser trainer la
# cle a cote de la porte. Ils partent en DIRECT par le serveur d'envoi, et
# ne se relisent nulle part dans CodeLab (sensible=True).
MAILPIT_SMTP_HOTE = os.environ.get("APP_MANAGER_MAILPIT_SMTP_HOTE") or MAILPIT_AMONT
MAILPIT_SMTP_PORT = int(os.environ.get("APP_MANAGER_MAILPIT_SMTP_PORT", "1025") or 1025)
MAIL_PAR_MAILPIT = (os.environ.get("APP_MANAGER_MAIL_PAR_MAILPIT", "1").strip().lower()
                    not in ("0", "non", "false", "no"))
# Mailpit remplace l'expediteur par celui du compte d'envoi ; il en faut
# quand meme un quand aucun serveur n'est regle (mail garde, pas relaye).
EXPEDITEUR_DEFAUT = "CodeLab <codelab@localhost>"


def _envoyer_par_mailpit(msg):
    """Remet le message a Mailpit, ou leve OSError / SMTPException."""
    with smtplib.SMTP(MAILPIT_SMTP_HOTE, MAILPIT_SMTP_PORT, timeout=10) as s:
        s.send_message(msg)


def envoyer_mail(cfg, sujet, corps, destinataires=None, sensible=False, html=None):
    """Envoie, ou leve. Par Mailpit d'abord, en direct sinon.

    sensible=True (mails de securite) : jamais par Mailpit, toujours en direct.
    html : une version mise en page, en plus du texte.
    """
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = cfg.get("expediteur") or EXPEDITEUR_DEFAUT
    msg["To"] = ", ".join(destinataires if destinataires is not None
                          else cfg["destinataires"])
    msg.set_content(corps)
    if html:
        msg.add_alternative(html, subtype="html")

    if sensible:
        if not cfg.get("host"):
            raise RuntimeError("Aucun serveur d'envoi configuré : un mail de sécurité "
                               "ne passe pas par Mailpit.")
    elif MAIL_PAR_MAILPIT:
        try:
            _envoyer_par_mailpit(msg)
            return
        except (OSError, smtplib.SMTPException) as e:
            if not cfg.get("host"):
                raise RuntimeError(f"Mailpit injoignable ({type(e).__name__}: {e}) "
                                   "et aucun serveur d'envoi configuré.") from e
            print(f"[app-manager] Mailpit injoignable ({type(e).__name__}: {e}) : "
                  "envoi direct.", flush=True)
    elif not cfg.get("host"):
        raise RuntimeError("Aucun serveur d'envoi configuré.")

    def _login(s):
        if cfg["user"] and cfg["password"]:
            s.login(cfg["user"], cfg["password"])

    if cfg["tls"] == "ssl":
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"],
                              context=ssl.create_default_context(), timeout=20) as s:
            _login(s)
            s.send_message(msg)
    elif cfg["tls"] == "none":
        # Relais interne sans chiffrement : les identifiants passeraient en
        # clair, a ne faire que sur un reseau de confiance.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            _login(s)
            s.send_message(msg)
    else:
        # STARTTLS : on ouvre en clair puis on chiffre AVANT de s'authentifier.
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            _login(s)
            s.send_message(msg)


def destinataires_alerte(name, apps=None):
    """A qui adresser l'alerte de cette application.

    Chaque application declare ses propres destinataires -- une application
    de facturation ne previent pas les memes personnes qu'un site vitrine,
    et une liste unique obligeait a prevenir tout le monde ou personne.
    L'adresse d'administration s'y ajoute TOUJOURS : c'est le filet, celui
    qui garantit qu'une application dont on a oublie de remplir la liste ne
    tombe pas en silence.
    """
    apps = apps if apps is not None else load()
    propres = _adresses((reglage("alertes_par_app", {}) or {}).get(name))
    vus, tout = set(), []
    for adresse in alertes_admin() + propres:
        if adresse not in vus:
            vus.add(adresse)
            tout.append(adresse)
    return tout


def envoyer_avec_repli(sujet, corps, destinataires, sensible=False):
    """Envoie, en repassant sur la configuration d'origine si besoin.

    La configuration personnalisee a ete verifiee le jour ou elle a ete
    enregistree, mais un mot de passe se revoque, un quota se remplit, un
    serveur s'eteint. Ce jour-la, celle de credentials.env -- qui n'a jamais
    bouge -- reprend le relais plutot que de laisser l'alerte a terre.

    Renvoie (envoye, detail).
    """
    personnalisee = lire_smtp_personnalise()
    tentatives = []
    if personnalisee:
        tentatives.append(("personnalisee", personnalisee))
    origine = smtp_origine()
    if not smtp_incomplet(origine) and origine != personnalisee:
        tentatives.append(("d'origine", origine))
    if not tentatives:
        return False, "aucune configuration d'envoi utilisable"

    echecs = []
    for nom, cfg in tentatives:
        if smtp_incomplet(cfg):
            continue
        try:
            envoyer_mail(cfg, sujet, corps, destinataires=destinataires, sensible=sensible)
            if echecs:
                print(f"[app-manager] envoi repli sur la configuration {nom} "
                      f"apres : {' ; '.join(echecs)}", flush=True)
            return True, nom
        except Exception as e:
            echecs.append(f"{nom} -> {type(e).__name__}: {e}")
    return False, " ; ".join(echecs)


def alerter(sujet, corps, name=None, apps=None):
    """Envoi best-effort depuis le moniteur.

    Ne leve jamais et ne bloque jamais le moniteur : une alerte qui ne part
    pas ne doit pas ajouter une panne a celle qu'elle signale. Renvoie True
    si le mail est parti.
    """
    if not lire_alertes()["actif"]:
        return False
    cibles = destinataires_alerte(name, apps) if name else alertes_admin()
    if not cibles:
        print("[app-manager] alerte non envoyee : aucun destinataire "
              "(ni adresse d'administration, ni adresse propre a "
              f"l'application {name or '?'})", flush=True)
        return False
    envoye, detail = envoyer_avec_repli(sujet, corps, cibles)
    if envoye:
        print(f"[app-manager] alerte envoyee a {len(cibles)} destinataire(s) "
              f"par la configuration {detail} : {sujet}", flush=True)
    else:
        print(f"[app-manager] alerte non envoyee ({detail})", flush=True)
    return envoye


def fin_du_journal(name, lignes=ALERTE_LOG_LIGNES):
    """Les dernieres lignes du journal d'une application.

    Lues depuis la fin : un journal de 2 Mo ne doit pas etre charge en
    memoire pour en extraire vingt lignes.
    """
    chemin = os.path.join(LOG_DIR, name + ".log")
    try:
        taille = os.path.getsize(chemin)
        with open(chemin, "rb") as f:
            f.seek(max(0, taille - 8192))
            texte = f.read().decode("utf-8", "replace")
    except OSError:
        return "(journal illisible)"
    fin = texte.splitlines()[-lignes:]
    return "\n".join(fin) or "(journal vide)"


def corps_alerte_chute(name, a):
    return "\n".join([
        f"L'application « {name} » ne répond plus.",
        "",
        f"Dossier   : {a.get('path', '?')}",
        f"Commande  : {a.get('command', '?')}",
        f"Port      : {a.get('port', '?')}",
        f"État      : arrêtée après {RESTART_MAX_ATTEMPTS} tentatives de "
        f"redémarrage en {RESTART_WINDOW // 60} minutes",
        "",
        f"Panneau   : {read_shared_value('APP_MANAGER_URL') or 'http://<IP-du-serveur>:9001'}/",
        "",
        f"Fin du journal ({ALERTE_LOG_LIGNES} dernières lignes)",
        "-" * 46,
        fin_du_journal(name),
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])


def corps_alerte_retour(name):
    return "\n".join([
        f"L'application « {name} » répond de nouveau.",
        "",
        f"Panneau : {read_shared_value('APP_MANAGER_URL') or 'http://<IP-du-serveur>:9001'}/",
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])


# Applications pour lesquelles une alerte de chute a deja ete envoyee. Sans
# cette memoire, le moniteur reexpedierait le meme mail toutes les dix
# secondes tant que l'application reste a terre -- une boite pleine, et une
# alerte qu'on finit par ignorer. Une entree disparait quand l'application
# repart (mail de retour) ou quand elle est arretee volontairement.
_alertes_en_cours = set()


def alerte_tick():
    """Compare l'etat des applications a celui du tour precedent, et envoie
    un mail sur les deux transitions qui comptent : tombee, puis revenue.

    Volontairement fonde sur is_crash_looping() et non sur "le process est
    mort" : une application qui plante et redemarre toute seule dans la
    seconde n'est pas un incident, c'est le filet de securite qui fonctionne.
    L'incident commence quand le panneau a epuise ses tentatives.
    """
    apps = load()
    for name in list(_alertes_en_cours):
        if name not in apps or not apps[name].get("enabled"):
            # Supprimee ou arretee a la main : l'incident est clos, sans mail
            # de retour -- personne n'a besoin d'etre prevenu d'une action
            # qu'il vient de faire lui-meme.
            _alertes_en_cours.discard(name)
        elif is_running(name) and not is_crash_looping(name):
            _alertes_en_cours.discard(name)
            alerter(f"[CodeLab] {name} est revenue", corps_alerte_retour(name),
                    name=name, apps=apps)
            notifier_compte(NOM_ADMIN, f"{titre_de(name, apps.get(name))} est revenue",
                            "L'application répond de nouveau.",
                            url="/applications/" + urllib.parse.quote(name), etiquette="app-" + name)

    for name, a in apps.items():
        if (a.get("enabled") and not is_running(name) and is_crash_looping(name)
                and name not in _alertes_en_cours):
            _alertes_en_cours.add(name)
            alerter(f"[CodeLab] {name} est tombee", corps_alerte_chute(name, a),
                    name=name, apps=apps)
            notifier_compte(NOM_ADMIN, f"{titre_de(name, a)} est tombée",
                            f"Arrêtée après {RESTART_MAX_ATTEMPTS} tentatives de redémarrage.",
                            url="/applications/" + urllib.parse.quote(name),
                            etiquette="app-" + name, urgence="high")


# ------------------------------- build ---------------------------------

def run_build(name):
    apps = load()
    a = apps.get(name)
    if not a:
        return False, "Application inconnue."
    cmd = (a.get("build_command") or "").strip()
    if not cmd:
        return False, "Aucune commande de build definie pour cette application."
    os.makedirs(LOG_DIR, exist_ok=True)
    env = dict(os.environ, **secrets_partages(), **identifiants_base(a),
               HOME=ensure_child_home(name))
    logf = os.path.join(LOG_DIR, name + ".log")
    with open(logf, "ab") as out:
        out.write(f"\n$ {cmd}\n".encode())
        try:
            # Meme abandon de privileges que pour l'application : c'est le
            # build qui execute le plus de code tiers (scripts postinstall).
            argv, env_isolement = commande_isolee(name, a["path"], cmd, apps)
            env.update(env_isolement)
            # Le build est le moment ou le plus de code tiers s'execute
            # (scripts postinstall des dependances) : c'est celui qui a le
            # plus besoin d'etre enferme.
            r = subprocess.run(argv, cwd=a["path"], env=env,
                                stdout=out, stderr=out, timeout=600,
                                preexec_fn=child_setup(nom=name))
            ok = r.returncode == 0
            msg = None if ok else f"Le build a echoue (code {r.returncode}) -- voir le journal."
        except subprocess.TimeoutExpired:
            out.write(b"\n[build] delai depasse (10 min), arrete.\n")
            ok, msg = False, "Le build a depasse le delai de 10 minutes."
    return ok, msg


# ------------------------- historique de metriques ----------------------------

def record_metrics(name, cpu_percent, memory_mb):
    dq = _metrics_history.setdefault(name, deque(maxlen=METRICS_HISTORY_LEN))
    dq.append((time.time(), cpu_percent, memory_mb))


def get_metrics_history(name):
    return list(_metrics_history.get(name, []))


# ------------------------- le pouls du serveur ----------------------------
#
# Le widget « Serveur » du hub montre la machine elle-meme, pas une
# application : processeur, memoire, disque, et la courbe des dix dernieres
# minutes. La mesure se prend dans le moniteur (un point toutes les dix
# secondes), pas a la demande : deux onglets ouverts ne doublent pas la
# frequence, et la courbe est deja la quand on arrive sur le hub.
SERVEUR_HISTORIQUE_LEN = 60      # 10 min a un point toutes les 10 s
_serveur_historique = deque(maxlen=SERVEUR_HISTORIQUE_LEN)


def serveur_tick():
    """Un point de mesure : (horodatage, processeur %, memoire %)."""
    # cpu_percent(None) mesure depuis l'appel precedent : le premier appel
    # rend 0.0 et ne sert que d'amorce.
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    _serveur_historique.append((time.time(), round(cpu, 1), round(mem, 1)))


def etat_serveur():
    """Ce que montre le widget « Serveur ». Rien de secret : des pourcentages."""
    mem = psutil.virtual_memory()
    try:
        disque = psutil.disk_usage(ROOT if os.path.isdir(ROOT) else "/")
        disque = {"pct": round(disque.percent, 1), "utilise": disque.used, "total": disque.total}
    except OSError:
        disque = None
    hist = list(_serveur_historique)
    try:
        allume = int(time.time() - psutil.boot_time())
    except Exception:                                             # noqa: BLE001
        allume = None
    return {
        "cpu": hist[-1][1] if hist else round(psutil.cpu_percent(interval=None), 1),
        "coeurs": psutil.cpu_count() or 1,
        "memoire": {"pct": round(mem.percent, 1), "utilise": mem.total - mem.available,
                    "total": mem.total},
        "disque": disque,
        "allume_depuis": allume,
        "historique": [{"t": int(t), "cpu": c, "mem": m} for t, c, m in hist],
    }


ACTIVITE_JOURS = 14


def activite_recente(jours=ACTIVITE_JOURS):
    """Les ouvertures des derniers jours, jour par jour, et la periode d'avant.

    Pour le widget « Activité » : la courbe dit le rythme, le total se
    compare a la meme duree juste avant (la tendance), et les applications
    les plus ouvertes disent ou va le temps.
    """
    aujourd_hui = datetime.datetime.now(_fuseau()).date()
    debut = aujourd_hui - datetime.timedelta(days=jours - 1)
    debut_avant = debut - datetime.timedelta(days=jours)
    par_jour, par_app, avant = {}, {}, 0
    lignes = None
    if pg_disponible():
        try:
            with _pg_connexion(PG_BASE) as cx:
                lignes = [(j.isoformat(), a or "", int(n)) for j, a, n in cx.execute(
                    "SELECT (ts AT TIME ZONE %s)::date AS jour, application, count(*)"
                    "  FROM acces WHERE genre = 'ouverture'"
                    "   AND (ts AT TIME ZONE %s)::date >= %s"
                    " GROUP BY 1, 2", [FUSEAU_JOURNAL, FUSEAU_JOURNAL, debut_avant]).fetchall()]
        except Exception as e:                                    # noqa: BLE001
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
            lignes = None
    if lignes is None:
        borne = debut_avant.isoformat()
        compte = {}
        for e in lire_acces(limite=100000):
            if e.get("genre") != "ouverture":
                continue
            j = _jour(e.get("ts"))
            if j < borne:
                continue
            cle = (j, e.get("app") or "")
            compte[cle] = compte.get(cle, 0) + 1
        lignes = [(j, a, n) for (j, a), n in compte.items()]
    debut_iso = debut.isoformat()
    for j, a, n in lignes:
        if j < debut_iso:
            avant += n
            continue
        par_jour[j] = par_jour.get(j, 0) + n
        par_app[a] = par_app.get(a, 0) + n
    serie = [{"jour": (debut + datetime.timedelta(days=i)).isoformat(),
              "ouvertures": par_jour.get((debut + datetime.timedelta(days=i)).isoformat(), 0)}
             for i in range(jours)]
    apps = load()
    top = sorted(((n, a) for a, n in par_app.items() if a), reverse=True)[:4]
    return {"jours": serie, "total": sum(par_jour.values()), "precedent": avant,
            "apps": [{"name": a, "titre": titre_de(a, apps[a]) if a in apps else a,
                      "ouvertures": n} for n, a in top]}


# ------------------------- metriques ----------------------------

def proc_stats(pid):
    """CPU% et memoire (Mo) cumules sur le process et tous ses enfants.
    cpu_percent() ne donne une valeur exploitable qu'a partir du 2e appel
    sur un meme objet psutil.Process (le 1er sert d'amorce) -- d'ou le
    cache _proc_cache, reutilise entre deux appels a /api/apps."""
    try:
        top = psutil.Process(pid)
    except psutil.Error:
        return {"cpu_percent": 0.0, "memory_mb": 0.0}

    try:
        pids = [top.pid] + [c.pid for c in top.children(recursive=True)]
    except psutil.Error:
        pids = [top.pid]

    cpu_total, mem_total = 0.0, 0
    for cpid in pids:
        pr = _proc_cache.get(cpid)
        if pr is None:
            try:
                pr = psutil.Process(cpid)
                pr.cpu_percent(interval=None)  # amorce
                _proc_cache[cpid] = pr
                continue  # premiere mesure ignoree, pas encore fiable
            except psutil.Error:
                continue
        try:
            cpu_total += pr.cpu_percent(interval=None)
            mem_total += pr.memory_info().rss
        except psutil.Error:
            _proc_cache.pop(cpid, None)

    return {"cpu_percent": round(cpu_total, 1), "memory_mb": round(mem_total / (1024 * 1024), 1)}


# ------------------------- auto-detection de commande ----------------------------
#
# Detecter la commande de LANCEMENT ne suffisait pas : pour un projet front,
# ce qu'il faut deviner c'est le couple build + service. La suggestion rendait
# "npm start" pour un projet Vite, script qui n'existe pas dans un projet Vite
# standard -- l'app etait declaree, puis echouait au demarrage.
#
# Une detection renvoie donc les deux commandes, et pour un front elle
# propose de servir le dossier produit par le build avec le http.server de
# Python plutot que le serveur de developpement du framework : le build a
# besoin de node, le service n'en a pas besoin, et un "vite dev" deploye est
# un serveur de developpement expose en continu.

# Frameworks front dont le build produit un dossier statique, et nom de ce
# dossier. La cle est cherchee dans dependencies + devDependencies.
STATIC_BUILDERS = {
    "vite": "dist",
    "astro": "dist",
    "@11ty/eleventy": "_site",
    "parcel": "dist",
    "react-scripts": "build",
    "@angular/cli": "dist",
}


def detect_project(path):
    """Suggere (commande de lancement, commande de build) pour un dossier.

    Les deux peuvent etre vides : une detection ratee ne doit rien imposer.
    """

    def exists(*parts):
        return os.path.exists(os.path.join(path, *parts))

    def read(*parts):
        try:
            with open(os.path.join(path, *parts), errors="ignore") as f:
                return f.read()
        except OSError:
            return ""

    if exists("package.json"):
        try:
            pkg = json.loads(read("package.json"))
        except Exception:
            pkg = {}
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        deps = {}
        for key in ("dependencies", "devDependencies"):
            if isinstance(pkg.get(key), dict):
                deps.update(pkg[key])

        # "npm ci" plutot que "npm install" quand le lockfile est la : c'est
        # l'installation reproductible, et elle est plus rapide.
        install = "npm ci" if exists("package-lock.json") else "npm install"
        build = f"{install} && npm run build" if "build" in scripts else install

        # Next.js a son propre serveur : il se build, puis se lance -- pas de
        # dossier statique a servir (sauf export, non detectable d'ici).
        if "next" in deps:
            return "npx next start --port $PORT", build

        for dep, outdir in STATIC_BUILDERS.items():
            if dep in deps and "build" in scripts:
                return f"python3 -m http.server $PORT --directory {outdir}", build

        if "start" in scripts:
            return "npm start", build
        if pkg.get("main"):
            return f"node {pkg['main']}", build
        return "npm start", build

    if exists("manage.py"):
        return "python3 manage.py runserver 0.0.0.0:$PORT", _python_install(path)

    if exists("app.py"):
        return "python3 app.py", _python_install(path)

    if exists("main.py"):
        return "python3 main.py", _python_install(path)

    if exists("Procfile"):
        for line in read("Procfile").splitlines():
            if line.strip().startswith("web:"):
                return line.split(":", 1)[1].strip(), ""

    if exists("index.html"):
        return "python3 -m http.server $PORT", ""

    return "", ""


def _python_install(path):
    if os.path.exists(os.path.join(path, "requirements.txt")):
        return "pip install -r requirements.txt"
    return ""


# ------------------------- icones ----------------------------

ICON_CANDIDATES = ["icon.png", "icon.svg", "favicon.png", "favicon.ico", "logo.png", "logo.svg"]
PALETTE = ["#316dca", "#8957e5", "#bf3989", "#cf222e", "#bc4c00", "#9a6700", "#1a7f37", "#0969da"]


def find_icon(path):
    for name in ICON_CANDIDATES:
        p = os.path.join(path, name)
        if os.path.isfile(p):
            return p
    return None


# ------------------------- le logo d'une application -------------------------
#
# Il vit dans le DOSSIER du projet, sous le nom icon.<ext>, et non dans un
# coin d'etat du panneau : un projet emporte ainsi son logo quand on le copie
# ailleurs, et celui qui a depose un icon.png a la main voit exactement le
# meme resultat.
#
# PAS DE SVG A L'ENVOI, et c'est deliberé. Une image SVG est un document qui
# peut porter du script ; servie par /api/icon, donc dans l'origine du
# panneau, elle y executerait ce script. Les formats acceptes sont des
# images matricielles, reconnues a leur signature -- pas a leur extension,
# qu'on peut ecrire n'importe comment.
LOGO_FORMATS = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"GIF87a": ("gif", "image/gif"),
    b"GIF89a": ("gif", "image/gif"),
}
LOGO_MAX_OCTETS = 512 * 1024      # 512 Ko : une icone, pas une photo
# Les noms qu'on peut ECRIRE (et donc remplacer). Les autres candidats de
# ICON_CANDIDATES restent lus, jamais ecrases : un favicon.ico depose a la
# main appartient au projet.
LOGO_NOMS_ECRITS = ("icon.png", "icon.jpg", "icon.gif", "icon.webp")


def _format_du_logo(donnees):
    """Le format REEL, lu dans les premiers octets. Rend (ext, type) ou None.

    WebP demande douze octets : "RIFF", quatre octets de taille, puis "WEBP".
    """
    for signature, (ext, mime) in LOGO_FORMATS.items():
        if donnees.startswith(signature):
            return ext, mime
    if donnees[:4] == b"RIFF" and donnees[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def default_icon_svg(name):
    color = PALETTE[sum(map(ord, name or "?")) % len(PALETTE)]
    letter = (name[:1] or "?").upper()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="16" fill="{color}"/>'
        f'<text x="32" y="43" font-family="system-ui,sans-serif" font-size="26" '
        f'font-weight="600" fill="#fff" text-anchor="middle">{letter}</text></svg>'
    )




# Longueur d'un nom d'application. Le nom s'affiche sous une icone, dans une
# tuile large de 148 px : au-dela, il est coupe et ne se lit plus.
NOM_MAX = 60


def titre_de(nom, a=None):
    """Le nom a afficher : celui qui a ete saisi, ou la cle a defaut.

    Les applications inscrites avant que le nom et le chemin se separent
    n'ont pas de titre. La cle EST leur nom, et elle le reste : rien a
    migrer, et une inscription ancienne s'affiche exactement comme avant.
    """
    return ((a or {}).get("titre") or "").strip() or nom


def titre_propre(brut):
    """Le nom TEL QU'IL S'AFFICHE : casse et espaces conserves.

    Une seule normalisation, sur les espaces : les doubler ou les mettre en
    tete ne veut rien dire, et deux noms qui ne different que par la se
    liraient comme un seul.
    """
    return re.sub(r"\s+", " ", str(brut or "")).strip()[:NOM_MAX]


def valid_name(raw):
    """Le CHEMIN tire du nom : ce qui apparait dans l'adresse.

    Le nom et le chemin ne sont plus la meme chaine. « Mon Site » s'affiche
    ainsi et se sert sur /mon-site/. Deux raisons de les separer :

      - une adresse est sensible a la casse et ne prend pas d'espace, alors
        qu'un nom d'application est fait pour etre lu ;
      - la regle d'avant refusait tout net une majuscule ("Site" -> nom
        invalide, sans rien dire de plus), ce qui obligeait a deviner la
        contrainte au lieu de la subir.

    C'est donc le chemin OBTENU qui decide de la validite : si la
    transformation ne laisse rien -- un nom fait de "---" ou d'ideogrammes
    que l'adresse ne sait pas porter -- il n'y a pas de chemin, donc pas
    d'application.
    """
    value = str(raw or "").strip().lower()
    # LES ACCENTS SE RETIRENT, ILS NE SE REMPLACENT PAS. « API Météo » donnait
    # « /api-m-t-o/ » : chaque lettre accentuee devenait un tiret, et
    # l'adresse ne se lisait plus. On garde la lettre de base (é -> e), et
    # les ligatures se deplient (œ -> oe) ; ce que l'ASCII ne sait pas
    # porter du tout reste traite comme avant, plus bas.
    for lig, rempl in (("œ", "oe"), ("æ", "ae"), ("ß", "ss")):
        value = value.replace(lig, rempl)
    value = "".join(c for c in unicodedata.normalize("NFKD", value)
                    if not unicodedata.combining(c))
    # Les espaces deviennent des tirets, explicitement. Le remplacement
    # general ci-dessous le ferait aussi, mais c'est la regle annoncee a
    # l'utilisateur : elle doit se lire dans le code.
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9_-]", "-", value)
    # Pas deux tirets de suite : "Mon  --  Site" ne donne pas "mon----site".
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")[:NOM_MAX]


# Longueur d'une description de projet. Assez pour une phrase qui dit a quoi
# sert l'application, trop court pour une documentation -- l'espace
# utilisateur doit rester une liste qu'on parcourt d'un coup d'oeil.
DESCRIPTION_MAX = 140


def description_propre(brute):
    """Une ligne, sans retour a la ligne ni balise possible.

    Le texte est rendu echappe cote page, mais le nettoyer ici evite qu'une
    description sur trois lignes deforme la liste.
    """
    texte = re.sub(r"\s+", " ", str(brute or "")).strip()
    return texte[:DESCRIPTION_MAX]


# ------------------------------ categories ------------------------------
#
# Une categorie est un simple intitule libre ("Outils", "Sites", "Donnees")
# qui regroupe les projets dans le hub. Elle ne donne aucun droit et ne
# change rien au deploiement : c'est du rangement, et rien d'autre.
#
# La liste vit dans un fichier a part plutot que dans apps.json : une
# categorie existe avant qu'un projet la porte (on la cree pour ranger
# ensuite), et elle survit a la suppression du dernier projet qui l'utilisait.
# Un champ libre par projet aurait produit "Outils", "outils" et "Outil ".
CATEGORIES_FILE = os.path.join(STATE_DIR, "categories.json")
CATEGORIE_MAX = 30      # un intitule, pas une phrase
CATEGORIES_MAX = 20     # au-dela, ce n'est plus un rangement mais une liste


# LA CATEGORIE « CodeLab ». Les applications de la stack, reservees a
# l'administrateur -- Dagster, Mailpit, Messages, le Diagnostic -- se rangent
# d'office ensemble, en tete de liste. Elle ne se renomme pas, ne se supprime
# pas, et ne se donne pas a un projet : ce qu'elle annonce (« reserve a
# l'administrateur, fourni par CodeLab ») doit rester vrai.
CATEGORIE_CODELAB = "CodeLab"


def est_categorie_codelab(nom):
    return categorie_propre(nom).lower() == CATEGORIE_CODELAB.lower()


def categorie_de(nom, a=None):
    """La categorie affichee d'une application : CodeLab pour celles de la
    stack, sinon celle qu'on lui a donnee (si elle existe encore)."""
    if est_app_par_defaut(nom):
        return CATEGORIE_CODELAB
    return categorie_valide((a or {}).get("categorie"))


def categories_affichees():
    """CodeLab d'abord, puis les categories de l'administrateur."""
    return [CATEGORIE_CODELAB] + lire_categories()


def categorie_propre(brute):
    """Un intitule sur une ligne, borne en longueur."""
    return re.sub(r"\s+", " ", str(brute or "")).strip()[:CATEGORIE_MAX]


def lire_categories():
    """La liste des categories, dans l'ordre voulu par l'administrateur.

    L'ordre est celui de l'affichage dans le hub : il se regle en rangeant
    la liste, pas par un tri alphabetique impose.
    """
    try:
        lignes = stockage.lire(DB_FILE, "SELECT nom FROM categories ORDER BY position")
    except Exception:                                             # noqa: BLE001
        return []
    return [c for c in (categorie_propre(l["nom"]) for l in lignes)
            if c and not est_categorie_codelab(c)][:CATEGORIES_MAX]


def ecrire_categories(liste):
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM categories")
        for position, nom in enumerate(liste):
            cx.execute("INSERT OR IGNORE INTO categories (nom, position) VALUES (?, ?)",
                       (nom, position))


def categorie_valide(brute, connues=None):
    """La categorie d'un projet, ou "" si elle n'existe pas (ou plus).

    Un projet ne porte jamais une categorie inconnue : sinon supprimer une
    categorie laisserait des projets ranges dans un tiroir invisible.
    """
    voulue = categorie_propre(brute)
    if not voulue:
        return ""
    return voulue if voulue in (lire_categories() if connues is None else connues) else ""


# ------------------------- adresse publique du serveur -------------------------
#
# « Publique » veut dire : accessible sans compte. Tant que ce serveur n'est
# joignable que depuis le salon, cela ne partage rien -- le mot promet une
# ouverture qui n'existe pas. Tant qu'aucune adresse publique n'est connue,
# le panneau ne propose donc pas de rendre une application publique.
#
# RIEN A SAISIR. L'adresse publique est le nom de domaine par lequel le
# panneau a ete ouvert depuis internet, relaye par le VPS (voir
# noter_l_adresse_publique). APP_MANAGER_PUBLIC_URL, dans le compose,
# l'emporte : c'est la marche arriere qui ne depend pas du panneau.
EXPOSITION_FILE = os.path.join(STATE_DIR, "exposition.json")


def _vrai(valeur):
    return str(valeur or "").strip().lower() in ("1", "true", "yes")


def lire_parametre(cle, defaut=None):
    """Un reglage en clair, table parametres (JSON)."""
    try:
        ligne = stockage.lire(DB_FILE, "SELECT valeur FROM parametres WHERE cle = ?", (cle,))
        return json.loads(ligne[0]["valeur"]) if ligne else defaut
    except Exception:                                             # noqa: BLE001
        return defaut


def ecrire_parametre(cle, valeur):
    with stockage.transaction(DB_FILE) as cx:
        if valeur is None:
            cx.execute("DELETE FROM parametres WHERE cle = ?", (cle,))
        else:
            cx.execute("INSERT OR REPLACE INTO parametres (cle, valeur) VALUES (?, ?)",
                       (cle, json.dumps(valeur, ensure_ascii=False)))


def lire_exposition():
    d = lire_parametre("exposition", {})
    return d if isinstance(d, dict) else {}


def ecrire_exposition(reglages):
    ecrire_parametre("exposition", reglages)


@flask_app.before_request
def noter_l_adresse_publique():
    """Retient le domaine par lequel le panneau est ouvert depuis internet.

    La premiere fois pour n'importe quelle requete relayee ; ensuite, seule
    une session ouverte peut la changer : un visiteur du reseau local qui
    imiterait les en-tetes du VPS ne detourne pas les liens d'invitation.
    """
    if sur_le_port_des_apps():
        return None
    domaine = domaine_des_applications()
    if not domaine:
        return None
    vue = f"{schema_vu()}://{domaine}"
    connue = str(lire_exposition().get("adresse_publique") or "")
    if vue == connue or (connue and not is_authed()):
        return None
    try:
        ecrire_exposition({**lire_exposition(), "adresse_publique": vue})
    except Exception:                                             # noqa: BLE001
        pass
    return None


def adresse_publique():
    """L'adresse publique du serveur, ou "".

    La variable d'environnement l'emporte ; sinon, la derniere adresse
    constatee (noter_l_adresse_publique).
    """
    depuis_env = (os.environ.get("APP_MANAGER_PUBLIC_URL") or "").strip()
    if depuis_env:
        return depuis_env.rstrip("/")
    return str(lire_exposition().get("adresse_publique") or "").strip().rstrip("/")


def https_actif():
    """La requete en cours arrive-t-elle en HTTPS ?

    PLUS DE REGLAGE : le cookie de session devient « Secure » et HSTS part
    des que le navigateur parle en https -- directement, ou derriere un
    proxy qui l'annonce (X-Forwarded-Proto). Croire cet en-tete ici ne
    donne rien a un menteur : un cookie Secure pose sur une connexion en
    clair est ignore par le navigateur, et HSTS aussi. APP_MANAGER_HTTPS=1
    le force (jamais l'inverse).
    """
    if _vrai(os.environ.get("APP_MANAGER_HTTPS")):
        return True
    if not has_request_context():
        return False
    return request.is_secure or (
        (request.headers.get("X-Forwarded-Proto") or "").lower() == "https")


def trust_proxy():
    """Croit-on X-Forwarded-For pour l'adresse du visiteur ? JAMAIS.

    C'etait un reglage. Il ne l'est plus : n'importe quel client peut poser
    cet en-tete, et s'y fier ouvrait la porte a qui se dirait « 192.168.1.10 ».
    L'adresse du visiteur est celle de la connexion, point. Derriere un
    proxy, tous les visiteurs partagent donc son adresse -- c'est plus
    strict (la limite de tentatives les compte ensemble), jamais moins.
    """
    return False


# ------------- l'administration reste sur le reseau local -------------
#
# Le raisonnement : un compte utilisateur est fait pour etre distribue et
# n'ouvre que les projets qu'on lui a autorises. Le compte d'administration,
# lui, permet de declarer une application, donc d'executer du code sur la
# machine. Les deux n'ont aucune raison d'etre joignables de la meme facon.
#
# Ce reglage separe les deux : les comptes nommes continuent d'entrer de
# n'importe ou, l'administration ne repond plus que depuis le reseau local.
# Un mot de passe d'administration qui fuit ne suffit alors plus -- il faut
# aussi etre dans la maison.
#
# CE QUE "LOCAL" VEUT DIRE ICI, et pourquoi la liste est ecrite a la main.
#
# Le reflexe est d'appeler ipaddress.ip_address(...).is_private. C'est FAUX
# pour cet usage, et le test l'a montre avant que cela ne parte :
#
#   is_private repond "non joignable globalement", pas "reseau local". Les
#     plages de DOCUMENTATION en font partie -- 203.0.113.0/24 et 2001:db8::/32
#     sont "privees" pour Python. Elles n'ont rien de local ;
#   et 100.64.0.0/10, l'espace partage ou vivent les adresses Tailscale, en
#     est EXCLU selon la version de Python. Le sens de la fonction change donc
#     d'un interpreteur a l'autre, ce qu'un controle d'acces ne peut pas se
#     permettre.
#
# La liste ci-dessous dit donc exactement ce qu'on entend par "chez soi" :
# les trois plages RFC 1918, la boucle locale, le lien-local, l'espace
# partage (un reseau prive type Tailscale est une extension de la maison,
# pas l'Internet), et leurs equivalents IPv6.
RESEAUX_LOCAUX = tuple(ipaddress.ip_network(c) for c in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # RFC 1918
    "127.0.0.0/8",                                      # boucle locale
    "169.254.0.0/16",                                   # lien-local
    "100.64.0.0/10",                                    # espace partage / Tailscale
    "::1/128",                                          # boucle locale IPv6
    "fc00::/7",                                         # adresses uniques locales
    "fe80::/10",                                        # lien-local IPv6
))


def adresse_est_locale(brute):
    """Cette adresse appartient-elle au reseau local ?

    Tout ce qui n'est pas analysable est traite comme NON local. C'est le
    sens qu'il faut : ceci est un controle d'acces, et une adresse qu'on ne
    sait pas lire ne doit jamais ouvrir l'administration.
    """
    try:
        adresse = ipaddress.ip_address(str(brute or "").strip())
    except ValueError:
        return False
    # ::ffff:192.168.1.10 -- une pile double presente parfois les adresses
    # IPv4 sous cette forme. Sans ce repli, une machine du salon serait vue
    # comme etrangere, et l'administration se fermerait sans raison visible.
    mappee = getattr(adresse, "ipv4_mapped", None)
    if mappee is not None:
        adresse = mappee
    return any(adresse in reseau for reseau in RESEAUX_LOCAUX
               if reseau.version == adresse.version)


def proxy_non_declare():
    """Un intermediaire parle-t-il sans qu'on l'ait declare ?

    Le piege que ce reglage doit absolument eviter : derriere un reverse
    proxy non declare, request.remote_addr est l'adresse DU PROXY. Elle est
    privee, donc toute requete -- y compris venue du bout du monde --
    paraitrait locale, et la restriction serait affichee comme active tout
    en ne protegeant rien. Une securite qui ment est pire que pas de
    securite : on cesse de se mefier.
    """
    if trust_proxy():
        return False
    return bool(request.headers.get("X-Forwarded-For")
                or request.headers.get("X-Forwarded-Proto"))


def admin_limite_au_reseau_local():
    """TOUJOURS : l'administration ne repond qu'au reseau local, en connexion
    directe. Ce n'est plus un reglage -- la securite la plus stricte n'est
    pas une option."""
    return True


def client_est_local():
    """La requete en cours vient-elle du reseau local ?

    Repond NON si un proxy non declare s'interpose : on ne sait alors pas
    qui appelle, et ne pas savoir vaut refuser.
    """
    if proxy_non_declare():
        return False
    return adresse_est_locale(_client_ip())


def refus_admin_hors_reseau():
    """Le message de refus, ou None si l'acces est permis.

    Une seule fonction, appelee aux trois endroits ou une session
    d'administration peut naitre ou servir : les deux routes de connexion et
    require_admin. Les separer aurait fini par en laisser une derriere.
    """
    if not admin_limite_au_reseau_local() or client_est_local():
        return None
    if proxy_non_declare():
        # Distinguer les deux causes : "je suis pourtant chez moi" est
        # exactement le moment ou l'on a besoin de savoir que c'est le proxy
        # qui brouille la piste, et non son adresse.
        return ("L'administration ne répond qu'au réseau local, en connexion directe : "
                "cette requête passe par un intermédiaire (proxy, VPS). Connecte-toi "
                "depuis chez toi, sur l'adresse locale du panneau.")
    return ("L'administration est limitee au reseau local. Cette requete vient "
            "de " + _client_ip() + ".")


def appliquer_cookie_securise():
    """Le cookie suit la requete (voir SessionSelonLaConnexion) : plus rien
    a aligner. Gardee pour les appels existants."""
    return None


from flask.sessions import SecureCookieSessionInterface  # noqa: E402


class SessionSelonLaConnexion(SecureCookieSessionInterface):
    """« Secure » des que la requete arrive en HTTPS, sans reglage.

    Et valable pour les sous-domaines quand le panneau est ouvert par son
    nom de domaine : une application privee (blog.toi.duckdns.org) doit
    reconnaitre la session ouverte sur toi.duckdns.org. Le cookie reste
    HttpOnly, et le proxy le retire avant de relayer : l'application ne le
    voit jamais -- exactement comme avec le port des applications, ou le
    navigateur l'envoyait deja (un cookie ne tient pas compte du port).
    """

    def get_cookie_secure(self, app):
        return https_actif()

    def get_cookie_domain(self, app):
        return domaine_des_applications() or None


flask_app.session_interface = SessionSelonLaConnexion()


# ------------------------- visibilite d'une application -------------------------
#
# Le reverse proxy sert les applications SANS authentification : c'est ce qui
# permet de partager un projet par un simple lien. Tant que le panneau vit sur
# un reseau de confiance, cela va de soi ; le jour ou le port est publie, cela
# revient a offrir chaque projet a tout internet.
#
# Deux etats, pas trois : "publique" (le comportement historique, et le defaut
# pour une application deja declaree qui n'a pas le champ) et "privee", qui
# exige la meme session que le panneau. Arreter une application reste le
# moyen de la retirer completement -- inutile d'un troisieme etat pour cela.
VISIBILITE_PUBLIQUE = "publique"
VISIBILITE_PRIVEE = "privee"
VISIBILITES = (VISIBILITE_PUBLIQUE, VISIBILITE_PRIVEE)


def visibilite(a):
    v = (a or {}).get("visibility")
    return v if v in VISIBILITES else VISIBILITE_PUBLIQUE


def under_root(path):
    """Le chemin est-il dans APP_MANAGER_ROOT (/workspace) ?

    Le navigateur de dossiers et la detection etaient bornes a cette racine,
    mais l'enregistrement et la modification ne l'etaient pas : on pouvait
    declarer une application sur n'importe quel dossier du conteneur, que le
    navigateur du panneau ne sait ensuite plus atteindre. Meme borne partout,
    donc, plutot qu'une regle appliquee une fois sur deux.
    """
    p = os.path.abspath(path)
    return p == ROOT or p.startswith(ROOT + os.sep)


# ------------------------------ auth --------------------------------

def admin_2fa_obligatoire():
    """Second facteur obligatoire pour l'administrateur (par defaut : oui).

    APP_MANAGER_ADMIN_2FA=0 dans le compose est la marche arriere, le jour
    ou le telephone qui portait le facteur est perdu : elle ne passe pas par
    le panneau, donc reste possible quand on en est enferme dehors.
    """
    return (os.environ.get("APP_MANAGER_ADMIN_2FA", "1").strip().lower()
            not in ("0", "non", "false", "no"))


def _ouvrir_session_admin():
    session.pop("inscription_depuis", None)
    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
    session["ouverte"] = int(time.time())
    jeton_session()
    session["role"] = ROLE_ADMIN
    session["utilisateur"] = NOM_ADMIN
    journaliser("connexion", qui=NOM_ADMIN, role=ROLE_ADMIN, ip=_adresse_client())
    signaler_connexion_admin()
    return jsonify({"ok": True, "role": ROLE_ADMIN})


def _inscrire_totp_admin(candidat):
    """Fin de l'inscription forcee du facteur de l'administrateur."""
    global _totp_secret
    # Le verrou reseau, de nouveau : entre les deux etapes, on a pu changer
    # de reseau, et c'est ici que la session nait.
    hors = refus_admin_hors_reseau()
    if hors:
        return jsonify({"error": hors}), 403
    if totp_actif():
        return jsonify({"error": "Un second facteur a déjà été enregistré. "
                                 "Recommence la connexion."}), 409
    if not ecrire_bloc_panneau(admin_password(), flask_app.secret_key, candidat):
        return jsonify({"error": "credentials.env n'a pas pu être écrit : rien "
                                 "n'a été enregistré."}), 500
    _totp_secret = candidat
    return _ouvrir_session_admin()


# ALERTE DE CONNEXION. Une connexion administrateur depuis une adresse jamais
# vue part par mail a l'adresse d'alerte : si ce n'est pas toi, tu le sais
# tout de suite, et pas au prochain coup d'oeil sur le journal. Les adresses
# deja vues (les 20 dernieres) ne declenchent rien, pour ne pas en faire du
# bruit qu'on finit par ignorer.
ADMIN_IPS_CONNUES_MAX = 20


def signaler_connexion_admin():
    ip = _client_ip()
    try:
        connues = list(reglage("ips_admin_connues", []) or [])
    except Exception:                                             # noqa: BLE001
        return
    if ip in connues:
        return
    premiere = not connues
    connues = (connues + [ip])[-ADMIN_IPS_CONNUES_MAX:]
    try:
        ecrire_reglage("ips_admin_connues", connues)
    except (OSError, RuntimeError):
        return
    destinataires = alertes_admin()
    if premiere or not destinataires:
        # La toute premiere connexion n'a rien d'inhabituel : c'est
        # l'installation.
        return
    quand = time.strftime("%d/%m/%Y %H:%M", time.localtime())
    threading.Thread(target=envoyer_avec_repli, daemon=True, args=(
        "[CodeLab] connexion administrateur depuis une nouvelle adresse",
        f"Une session d'administration vient de s'ouvrir le {quand}, depuis {ip}, "
        "une adresse jamais vue jusqu'ici.\n\n"
        "Si c'est toi, rien a faire. Sinon, change tout de suite le mot de passe "
        "d'administration (credentials.env), retire les cles d'acces inconnues et "
        "regarde le journal des connexions.\n\n"
        "-- CodeLab, panneau de gestion des applications", destinataires),
        kwargs={"sensible": True}).start()


@flask_app.post("/login")
def login_submit():
    """La connexion par mot de passe, puis le second facteur.

    Une seule route pour toutes les etapes : la page renvoie le meme
    formulaire, complete du code ou du moyen choisi, jusqu'a ce que la
    reponse porte « ok ». Les deux parcours different trop pour se partager
    le code : l'administrateur (_connexion_admin) n'existe pas dans la table
    des comptes et ne se connecte que du reseau local ; un compte utilisateur
    (_connexion_utilisateur, puis _second_facteur_utilisateur) a toujours un
    second facteur.

    REGLES (SECURITE.md) : 1 et 5 (jamais sur le seul mot de passe), 6 (tout
    echec passe par register_failed_attempt, que rate_limited lit ici), 9
    (compte suspendu), 10 (meme reponse pour un compte inconnu).
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    d = request.get_json(force=True, silent=True) or request.form
    pw = (d.get("password") or "").strip()
    nom = (d.get("nom") or "").strip().lower()

    # L'identifiant est obligatoire, administrateur compris : « admin » se
    # tape comme n'importe quel nom. Un champ vide qui ouvrait le compte le
    # plus puissant etait un raccourci a deviner pour tout le monde.
    if not nom:
        return jsonify({"error": "Renseigne ton identifiant."}), 400
    if nom == NOM_ADMIN:
        return _connexion_admin(d, pw)
    return _connexion_utilisateur(d, nom, pw)


def _connexion_admin(d, pw):
    """Le compte administrateur : mot de passe de credentials.env, reseau
    local seulement, second facteur obligatoire (sauf APP_MANAGER_ADMIN_2FA=0)."""
    # Avant meme de regarder le mot de passe : rien ne sert de laisser
    # essayer -- et surtout, aucune session d'administration ne doit
    # naitre hors du reseau local, pas meme une seconde.
    hors = refus_admin_hors_reseau()
    if hors:
        # Compte comme une tentative ratee, au meme titre qu'un mot de
        # passe faux. Sans cela le refus serait gratuit : on pourrait le
        # marteler sans jamais etre limite, et chaque appel ecrivant une
        # ligne de journal, la rotation finirait par chasser l'historique
        # reel -- une facon discrete d'effacer ses traces.
        register_failed_attempt()
        journaliser("refus", qui=NOM_ADMIN, motif="hors reseau local",
                    ip=_adresse_client())
        return jsonify({"error": hors}), 403
    real = admin_password()
    # compare_digest plutot que "==" : la comparaison de chaines s'arrete
    # au premier caractere different, et la duree de la reponse renseigne
    # alors sur la longueur du prefixe correct.
    if not (real and pw and secrets.compare_digest(pw, real)):
        register_failed_attempt()
        journaliser("echec", qui=NOM_ADMIN, motif="mot de passe", ip=_adresse_client())
        return jsonify({"error": avec_essais_restants("Identifiants incorrects.")}), 401

    # Le code a six chiffres, quand la double authentification est active.
    # Une tentative ratee ici compte comme une tentative ratee tout court :
    # sinon le second facteur serait forcable sans limite une fois le mot
    # de passe connu, ce qui le viderait de son sens.
    # SECOND FACTEUR OBLIGATOIRE pour l'administrateur : son compte
    # ouvre tout, Dagster (donc l'execution de code) et Mailpit (donc
    # tous les codes et liens envoyes) compris. Sans facteur enregistre,
    # le mot de passe juste mene a l'inscription d'une application, pas a
    # une session. Avec Face ID seulement, le mot de passe seul est
    # refuse : l'inscription d'une application le contournerait.
    cles_admin = passkeys_du_compte(NOM_ADMIN)
    if admin_2fa_obligatoire() and not totp_actif() and not cles_admin:
        candidat = totp_nouveau_secret()
        session["totp_candidat"] = candidat
        session["totp_inscription"] = NOM_ADMIN
        session["totp_uri"] = totp_uri(candidat)
        session["inscription_depuis"] = int(time.time())
        # Face ID d'abord quand le serveur le permet : un geste, rien a
        # recopier. Le code a six chiffres reste propose a cote.
        return jsonify({"inscription": True, "secret": candidat,
                        "uri": session["totp_uri"], "compte": NOM_ADMIN,
                        "qr": "/qr/totp.svg",
                        "passkey": passkeys_disponibles()
                        and not passkey_contexte()[2]})

    # LE SECOND FACTEUR, SUR UNE ETAPE A PART. Le mot de passe est juste :
    # la page passe a l'etape suivante -- Face ID si une cle existe, sinon
    # le code de l'application. Le code n'est plus demande a cote du mot
    # de passe.
    if totp_actif() or (admin_2fa_obligatoire() and cles_admin):
        moyens = moyens_admin()
        code = d.get("code")
        if not code:
            if not moyens:
                return jsonify({"error": "Face ID n'est pas utilisable ici : ouvre le "
                                         "panneau en HTTPS, par son nom de domaine."}), 403
            attendre_second_facteur(NOM_ADMIN)
            moyen = d.get("moyen") if d.get("moyen") in moyens else moyens[0]
            if moyen == MOYEN_EMAIL:
                return _code_admin_par_mail(moyens)
            return jsonify({"totp": True, "second_facteur": True,
                            "moyen": moyen, "moyens": moyens}), 401
        if not ((totp_actif() and totp_verifie(_totp_secret, code))
                or verifier_code_admin(code)):
            register_failed_attempt()
            journaliser("echec", qui=NOM_ADMIN, motif="second facteur", ip=_adresse_client())
            return jsonify({"error": avec_essais_restants("Code de vérification incorrect."),
                            "totp": True, "moyens": moyens}), 401

    session.permanent = True
    session["authed"] = True
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_ADMIN
    session["utilisateur"] = NOM_ADMIN
    journaliser("connexion", qui=NOM_ADMIN, role=ROLE_ADMIN, ip=_adresse_client())
    signaler_connexion_admin()
    return jsonify({"ok": True, "role": ROLE_ADMIN})


def _connexion_utilisateur(d, nom, pw):
    """Un compte de la table : mot de passe, puis etat du compte."""
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    # Meme message et meme chemin qu'un mot de passe faux : distinguer
    # "ce compte n'existe pas" de "mauvais mot de passe" donne la liste des
    # comptes valides a qui essaie. Meme duree aussi (_COMPTE_FANTOME).
    reconnu = verifie_mot_de_passe(compte or _COMPTE_FANTOME, pw or "")
    if not (compte and pw and reconnu):
        register_failed_attempt()
        # Le nom tel qu'il a ete tape, meme s'il ne correspond a aucun
        # compte : c'est ce qui distingue une faute de frappe d'un balayage.
        journaliser("echec", qui=nom, motif="mot de passe", ip=_adresse_client())
        return jsonify({"error": avec_essais_restants("Identifiants incorrects.")}), 401
    # Le mot de passe vient d'etre reconnu : c'est le seul moment ou l'on
    # peut recalculer une empreinte d'avant les 600 000 iterations.
    if not empreinte_a_jour(compte):
        try:
            compte["hash"] = derive_mot_de_passe(pw, compte["sel"])
            ecrire_utilisateurs(comptes)
        except Exception:                                         # noqa: BLE001
            pass

    # Suspendu : le mot de passe vient d'etre reconnu, donc le dire ne
    # revele rien -- et se taire enverrait chercher une panne la ou il y a
    # une decision.
    if compte_suspendu(compte):
        journaliser("echec", qui=nom, motif="compte suspendu", ip=_adresse_client())
        return jsonify({"error": "Ce compte est suspendu. Demande à "
                                 "l'administrateur de le réactiver."}), 403

    # Compte cree librement dont l'adresse n'a jamais ete confirmee. Le
    # message est explicite : ici, il ne revele rien qu'on ne sache deja --
    # le mot de passe vient d'etre reconnu.
    if compte.get("attente_email"):
        return jsonify({"error": "Confirme d'abord ton adresse mail : un code "
                                 "t'a été envoyé à l'inscription.",
                        "attente_email": True}), 403

    return _second_facteur_utilisateur(d, nom, compte)


def _second_facteur_utilisateur(d, nom, compte):
    """Le mot de passe d'un compte utilisateur est juste : verifier le code,
    ou dire lequel attendre (et l'envoyer), ou faire inscrire un facteur.

    Le second facteur n'est pas optionnel pour un compte utilisateur. Ces
    comptes existent pour etre distribues -- a un collegue, a un client --
    donc leur mot de passe circule par un canal qu'on ne maitrise pas, et
    sera reutilise ailleurs. C'est exactement le cas ou un seul secret ne
    suffit pas. L'administrateur, lui, garde le choix : lui imposer le
    second facteur d'office pourrait l'enfermer hors de son propre panneau.
    """
    secret = compte.get("totp") or ""
    moyens = moyens_second_facteur(compte)
    if passkeys_du_compte(nom) and passkey_utilisable():
        moyens = [MOYEN_PASSKEY] + moyens
    code = d.get("code")
    # Sans aucun moyen (ni secret, ni code envoye), un code tape ne prouve
    # rien : on passe a l'inscription du facteur, plus bas.
    if code and (moyens or compte.get("code_connexion")):
        ok = bool(secret) and totp_verifie(secret, code)
        if not ok and compte.get("code_connexion"):
            comptes = lire_utilisateurs()
            ok = verifier_code_connexion(comptes[nom], code)
            try:
                # Ecrit dans les deux cas : les essais consommes doivent
                # survivre a la requete, sinon la limite ne limite rien.
                ecrire_utilisateurs(comptes)
            except OSError:
                pass
        if not ok:
            register_failed_attempt()
            journaliser("echec", qui=nom, motif="second facteur", ip=_adresse_client())
            return jsonify({"error": avec_essais_restants("Code de vérification incorrect."),
                            "totp": True, "moyens": moyens}), 401
        _lever_suspension_code_mail(nom)
        return _ouvrir_session_utilisateur(nom)

    if moyens:
        # Pas encore de code : on dit lequel attendre, et on l'envoie s'il
        # doit partir par mail. Le moyen demande n'est retenu que
        # s'il est dans la liste du compte -- la destination, elle, ne vient
        # jamais de la requete.
        moyen = d.get("moyen") if d.get("moyen") in moyens else moyens[0]
        if moyen == MOYEN_PASSKEY:
            # Rien ne part : la page demande Face ID a l'appareil.
            attendre_second_facteur(nom)
            return jsonify({"totp": True, "second_facteur": True,
                            "moyen": moyen, "moyens": moyens}), 401
        # Compte comme une tentative : sans cela, ce formulaire enverrait
        # des mails en boucle a qui connait un mot de passe.
        register_failed_attempt()
        reponse = {"totp": True, "moyen": moyen, "moyens": moyens}
        if moyen == MOYEN_TOTP:
            reponse["error"] = "Saisis le code de ton application d'authentification."
            return jsonify(reponse), 401
        comptes = lire_utilisateurs()
        cible = comptes[nom]
        en_cours = cible.get("code_connexion") or {}
        reponse["destination"] = masquer_destination(moyen, cible.get("email"))
        recent = (en_cours.get("moyen") == moyen
                  and int(time.time()) - int(en_cours.get("envoye") or 0) < CODE_EMAIL_DELAI)
        if not recent:
            code_envoye = poser_code_connexion(cible, moyen)
            try:
                envoyer_code_connexion(cible, nom, moyen, code_envoye)
                ecrire_utilisateurs(comptes)
            except Exception as e:                                # noqa: BLE001
                journaliser("echec", qui=nom, motif=f"envoi du code ({moyen})",
                            detail=f"{type(e).__name__}", ip=_adresse_client())
                reponse["error"] = "Le code n'a pas pu partir par mail."
                reponse["envoi_impossible"] = True
                return jsonify(reponse), 401
        reponse["error"] = ("Un code vient de t'être envoyé par mail à "
                            + reponse["destination"] + ".")
        reponse["envoye"] = True
        return jsonify(reponse), 401

    if (compte.get("email_verifie") or compte.get("code_mail_suspendu")
            or passkeys_du_compte(nom)):
        # Ce compte a deja un facteur (adresse verifiee, Face ID) qu'on ne
        # peut pas utiliser en ce moment : serveur d'envoi en panne, ou code
        # par mail suspendu. Laisser enregistrer une application ici, sur le
        # seul mot de passe, contournerait ce facteur.
        register_failed_attempt()
        journaliser("echec", qui=nom, motif="aucun second facteur utilisable",
                    ip=_adresse_client())
        return jsonify({"error": "Aucun code ne peut être envoyé pour le moment. "
                                 "Connecte-toi avec Face ID, ou demande de l'aide à "
                                 "ton administrateur."}), 403

    if not secret:
        # Premier acces : inscription obligatoire avant toute session. Le
        # secret candidat vit dans le cookie signe -- rien n'est enregistre
        # tant qu'un code valide n'a pas ete fourni, donc une cle mal
        # recopiee ne peut pas enfermer dehors, et deux personnes peuvent
        # s'inscrire en meme temps sans se marcher dessus.
        candidat = totp_nouveau_secret()
        session["totp_candidat"] = candidat
        session["totp_inscription"] = nom
        session["totp_uri"] = totp_uri(candidat, nom)
        return jsonify({"inscription": True, "secret": candidat,
                        "uri": session["totp_uri"], "compte": nom,
                        "qr": "/qr/totp.svg"})

    # Inatteignable : un compte qui a un secret a au moins le moyen TOTP,
    # et la branche "moyens" ci-dessus a deja repondu. Garde par prudence.
    register_failed_attempt()
    return jsonify({"error": avec_essais_restants("Code de vérification incorrect."), "totp": True}), 401


def passkey_utilisable():
    return passkeys_disponibles() and not passkey_contexte()[2]


def moyens_admin():
    """Les seconds facteurs de l'administrateur : Face ID, le mail, l'application.

    Le code par mail part a l'adresse d'alerte, jamais par Mailpit (un mail
    de securite) : il faut donc un vrai serveur d'envoi.
    """
    moyens = []
    if passkeys_du_compte(NOM_ADMIN) and passkey_utilisable():
        moyens.append(MOYEN_PASSKEY)
    if smtp_utilisable()[1] and smtp_utilisable()[0].get("host") and alertes_admin():
        moyens.append(MOYEN_EMAIL)
    if totp_actif():
        moyens.append(MOYEN_TOTP)
    return moyens


def _code_admin_par_mail(moyens):
    """Envoie (au plus une fois par minute) le code de l'administrateur.

    Le code vit au coffre, en empreinte, comme celui d'un compte : jamais
    dans le cookie, ou six chiffres se retrouveraient par force brute.
    """
    destinataires = alertes_admin()
    reponse = {"totp": True, "second_facteur": True, "moyen": MOYEN_EMAIL, "moyens": moyens,
               "destination": masquer_destination(MOYEN_EMAIL, destinataires[0])}
    en_cours = reglage("code_connexion_admin") or {}
    if int(time.time()) - int(en_cours.get("envoye") or 0) >= CODE_EMAIL_DELAI:
        # Compte comme une tentative : sinon, des mails en boucle.
        register_failed_attempt()
        porteur = {}
        code = poser_code_connexion(porteur, MOYEN_EMAIL)
        try:
            # Range d'abord : un code parti mais jamais enregistre ne
            # pourrait pas etre verifie.
            ecrire_reglage("code_connexion_admin", porteur["code_connexion"])
            envoyer_code_connexion(porteur, NOM_ADMIN, MOYEN_EMAIL, code,
                                   destinataires=destinataires)
        except Exception as e:                                    # noqa: BLE001
            journaliser("echec", qui=NOM_ADMIN, motif="envoi du code (email)",
                        detail=f"{type(e).__name__}", ip=_adresse_client())
            reponse.update(error="Le code n'a pas pu partir par mail.", envoi_impossible=True)
            return jsonify(reponse), 401
    reponse.update(error="Un code vient de t'être envoyé par mail à "
                         + reponse["destination"] + ".", envoye=True)
    return jsonify(reponse), 401


def verifier_code_admin(code):
    """Le code par mail de l'administrateur : un essai consomme, le code au succes."""
    en_cours = reglage("code_connexion_admin")
    if not en_cours:
        return False
    porteur = {"code_connexion": en_cours}
    ok = verifier_code_connexion(porteur, code)
    try:
        ecrire_reglage("code_connexion_admin", porteur.get("code_connexion"))
    except (OSError, RuntimeError):
        pass
    return ok


def attendre_second_facteur(nom):
    """Le mot de passe de « nom » vient d'etre reconnu, dans CE navigateur.

    Rien d'autre : pas de session. C'est ce qui autorise, pendant dix
    minutes, a confirmer avec Face ID sans renvoyer le mot de passe.
    """
    session["facteur_attente"] = {"nom": nom, "depuis": int(time.time())}


def nom_en_attente():
    attente = session.get("facteur_attente") or {}
    if int(time.time()) - int(attente.get("depuis") or 0) > SECOND_FACTEUR_VALIDITE:
        return None
    return attente.get("nom") or None


@flask_app.post("/login/second-facteur/passkey/options")
def login_facteur_passkey_options():
    """Le defi Face ID de l'etape 2, limite aux cles du compte en attente."""
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = nom_en_attente()
    if not nom:
        return jsonify({"error": "Recommence la connexion."}), 400
    refus = _refus_passkey()
    if refus:
        return refus
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    rp_id, _, _ = passkey_contexte()
    options = generate_authentication_options(
        rp_id=rp_id, allow_credentials=_descripteurs(nom),
        user_verification=UserVerificationRequirement.REQUIRED)
    session["passkey_defi"] = base64.b64encode(options.challenge).decode()
    return Response(options_to_json(options), mimetype="application/json")


@flask_app.post("/login/second-facteur/passkey")
def login_facteur_passkey():
    """Face ID confirme le mot de passe : la session s'ouvre."""
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = nom_en_attente()
    if not nom:
        return jsonify({"error": "Recommence la connexion."}), 400
    refus = _refus_passkey()
    if refus:
        return refus
    proprietaire, erreur = _verifier_cle(nom_attendu=nom)
    if erreur:
        return erreur
    session.pop("facteur_attente", None)
    return _ouvrir_session_par_cle(proprietaire)


def _lever_suspension_code_mail(nom):
    """Une connexion par un facteur fort rend le code par mail."""
    comptes = lire_utilisateurs()
    if comptes.get(nom, {}).get("code_mail_suspendu"):
        comptes[nom].pop("code_mail_suspendu", None)
        try:
            ecrire_utilisateurs(comptes)
        except (OSError, RuntimeError):
            pass


def _ouvrir_session_utilisateur(nom):
    """Ouvre la session d'un compte utilisateur, une fois tout verifie."""
    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
    # La date d'OUVERTURE, posee ici et jamais repoussee : c'est elle qui
    # fait la limite absolue des trente jours. Le cookie, lui, tient
    # l'inactivite de son cote.
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_UTILISATEUR
    session["utilisateur"] = nom
    journaliser("connexion", qui=nom, role=ROLE_UTILISATEUR, ip=_adresse_client())
    return jsonify({"ok": True, "role": ROLE_UTILISATEUR})


@flask_app.post("/login/second-facteur")
def login_second_facteur():
    """Confirme l'inscription au second facteur, et ouvre la session.

    Etape distincte de /login : entre les deux, la session ne vaut rien --
    elle ne porte pas "authed", donc elle n'ouvre aucune page ni aucune
    application. Le mot de passe seul ne suffit jamais a entrer.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = session.get("totp_inscription")
    candidat = session.get("totp_candidat")
    if not (nom and candidat):
        return jsonify({"error": "Recommence la connexion : aucune inscription en attente."}), 400

    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(candidat, code):
        register_failed_attempt()
        return jsonify({"error": "Code incorrect. Vérifie l'heure de ton téléphone."}), 400

    if nom == NOM_ADMIN:
        return _inscrire_totp_admin(candidat)

    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte inconnu."}), 404
    # Course possible : l'administrateur a pu inscrire un secret entre-temps
    # (une autre session du meme compte). Le premier enregistre gagne, plutot
    # que d'ecraser un facteur deja en service sur un autre telephone.
    if compte.get("totp"):
        return jsonify({"error": "Un second facteur a déjà été enregistré. "
                                 "Recommence la connexion."}), 409
    compte["totp"] = candidat
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Second facteur non enregistré : {e}"}), 500

    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
    # La date d'OUVERTURE, posee ici et jamais repoussee : c'est elle qui
    # fait la limite absolue des trente jours. Le cookie, lui, tient
    # l'inactivite de son cote.
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_UTILISATEUR
    session["utilisateur"] = nom
    journaliser("connexion", qui=nom, role=ROLE_UTILISATEUR, ip=_adresse_client())
    return jsonify({"ok": True, "role": ROLE_UTILISATEUR})


def _mon_compte():
    """Le compte de la session, ou None pour l'administrateur.

    Le compte d'administration ne vit pas dans utilisateurs.json : son mot
    de passe est dans credentials.env, et il n'a pas d'adresse mail a lui.
    """
    if est_admin():
        return None
    return lire_utilisateurs().get(utilisateur_courant())


# ------------------------- routes des cles d'acces -------------------------

@flask_app.get("/api/passkeys/etat")
def api_passkeys_etat():
    """Ce que la page de connexion et les parametres ont besoin de savoir.

    Publique : la page de connexion doit pouvoir demander si le bouton a un
    sens avant que quiconque soit authentifie. Elle ne revele ni compte ni
    cle -- seulement si le serveur est en etat d'en utiliser.
    """
    if not passkeys_disponibles():
        return jsonify({"possible": False,
                        "empechement": "La bibliotheque webauthn n'est pas installee "
                                       "sur ce serveur."})
    _, _, empechement = passkey_contexte()
    return jsonify({"possible": not empechement, "empechement": empechement})


def _refus_passkey():
    """(reponse, code) si les cles d'acces ne sont pas utilisables ici."""
    if not passkeys_disponibles():
        return jsonify({"error": "La bibliothèque webauthn n'est pas installée."}), 501
    _, _, empechement = passkey_contexte()
    if empechement:
        return jsonify({"error": empechement}), 400
    return None


@flask_app.post("/api/mon-compte/passkeys/options")
@require_auth
def api_passkey_options():
    """Prepare l'enregistrement d'une cle pour le compte connecte."""
    refus = _refus_passkey() or refus_authentification_ancienne()
    if refus:
        return refus
    return _options_enregistrement(utilisateur_courant())


def _options_enregistrement(nom):
    """Le defi d'enregistrement d'une cle pour le compte nom."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                          ResidentKeyRequirement,
                                          UserVerificationRequirement)
    rp_id, _, _ = passkey_contexte()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="CodeLab",
        user_name=nom,
        # L'identifiant d'utilisateur est le nom du compte : il ne quitte
        # jamais ce serveur, et deux comptes ne portent jamais le meme nom.
        user_id=nom.encode(),
        user_display_name=nom,
        # Deja enregistrees : le navigateur propose alors d'en ajouter une
        # autre plutot que de remplacer celle qu'on a sous la main.
        exclude_credentials=_descripteurs(nom),
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Decouvrable : c'est ce qui permet de se connecter sans taper
            # son nom -- le navigateur sait deja de qui il s'agit.
            resident_key=ResidentKeyRequirement.PREFERRED,
            # Exigee : une cle qui ne verifie pas la personne (ni empreinte,
            # ni code d'appareil) ne serait qu'un facteur de possession, et
            # ne pourrait pas remplacer mot de passe ET second facteur.
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    session["passkey_defi"] = base64.b64encode(options.challenge).decode()
    return Response(options_to_json(options), mimetype="application/json")


@flask_app.post("/api/mon-compte/passkeys")
@require_auth
def api_passkey_enregistrer():
    """Verifie la reponse du navigateur et range la cle."""
    refus = _refus_passkey() or refus_authentification_ancienne()
    if refus:
        return refus
    return _enregistrer_cle(utilisateur_courant()) or jsonify({"ok": True})


def _enregistrer_cle(nom):
    """Verifie la reponse du navigateur et range la cle de nom.

    None si la cle est enregistree, sinon la reponse d'erreur.
    """
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url
    defi = session.get("passkey_defi")
    if not defi:
        return jsonify({"error": "Recommence : aucun enregistrement en attente."}), 400
    d = request.get_json(force=True, silent=True) or {}
    rp_id, origine, _ = passkey_contexte()
    try:
        verifiee = verify_registration_response(
            credential=d.get("credential"),
            expected_challenge=base64.b64decode(defi),
            expected_rp_id=rp_id,
            expected_origin=origine,
            require_user_verification=True,
        )
    except Exception as e:
        return jsonify({"error": detail_pour_admin("Clé refusée. Réessaie depuis cet appareil.", e)}), 400

    tout = lire_passkeys()
    liste = tout.setdefault(nom, [])
    identifiant = bytes_to_base64url(verifiee.credential_id)
    if any(k["id"] == identifiant for k in liste):
        return jsonify({"error": "Cette clé est déjà enregistrée."}), 400
    liste.append({
        "id": identifiant,
        "cle_publique": bytes_to_base64url(verifiee.credential_public_key),
        "compteur": verifiee.sign_count,
        "nom": description_propre(d.get("nom"))[:PASSKEY_NOM_MAX] or "Cle d'acces",
        "cree": int(time.time()),
        "dernier": 0,
    })
    try:
        ecrire_passkeys(tout)
    except OSError as e:
        return jsonify({"error": f"Clé non enregistrée : {e}"}), 500
    session.pop("passkey_defi", None)
    journaliser("passkey", qui=nom, action="ajout", ip=_adresse_client())
    return None


# PREMIERE CONNEXION DE L'ADMINISTRATEUR : FACE ID AU LIEU D'UN CODE A
# RECOPIER. Le mot de passe vient d'etre reconnu par /login, qui a ouvert
# une inscription en attente (sans session). Enregistrer une cle d'acces ici
# vaut exactement ce que vaut le code a six chiffres a la meme etape : le
# second facteur est lie au navigateur qui a prouve le mot de passe. La cle
# exige la verification de la personne (Face ID, Touch ID, code de
# l'appareil), comme partout ailleurs.
#
# Les gardes : une inscription admin en attente et recente (10 min), aucun
# facteur deja enregistre (cette porte ne sert qu'une fois), le verrou du
# reseau local, la limite de tentatives.
INSCRIPTION_VALIDITE = 600


def _refus_inscription_cle():
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    depuis = session.get("inscription_depuis") or 0
    if (session.get("totp_inscription") != NOM_ADMIN
            or time.time() - depuis > INSCRIPTION_VALIDITE):
        return jsonify({"error": "Recommence la connexion : aucune inscription en attente."}), 400
    hors = refus_admin_hors_reseau()
    if hors:
        return jsonify({"error": hors}), 403
    if totp_actif() or passkeys_du_compte(NOM_ADMIN):
        return jsonify({"error": "Un second facteur a déjà été enregistré. "
                                 "Recommence la connexion."}), 409
    return _refus_passkey()


@flask_app.post("/login/inscription/passkey/options")
def login_inscription_passkey_options():
    return _refus_inscription_cle() or _options_enregistrement(NOM_ADMIN)


@flask_app.post("/login/inscription/passkey")
def login_inscription_passkey():
    refus = _refus_inscription_cle()
    if refus:
        return refus
    erreur = _enregistrer_cle(NOM_ADMIN)
    if erreur:
        register_failed_attempt()
        return erreur
    session.pop("inscription_depuis", None)
    return _ouvrir_session_admin()


@flask_app.get("/api/mon-compte/passkeys")
@require_auth
def api_passkey_liste():
    """Les cles du compte connecte, sans leur cle publique.

    Elle n'apprend rien a l'interface et n'a pas a trainer dans
    l'historique du navigateur.
    """
    return jsonify({"passkeys": [
        {"id": k["id"], "nom": k.get("nom") or "Cle d'acces",
         "cree": k.get("cree"), "dernier": k.get("dernier")}
        for k in passkeys_du_compte(utilisateur_courant())]})


@flask_app.delete("/api/mon-compte/passkeys")
@require_auth
def api_passkey_tout_retirer():
    """Desactive Face ID / Touch ID : retire toutes les cles du compte.

    Le meme droit que retirer une cle a la fois, en un geste. Pour
    l'administrateur sans application de codes, la prochaine connexion
    redemande l'enregistrement d'un second facteur (voir login_submit).
    """
    nom = utilisateur_courant()
    tout = lire_passkeys()
    nombre = len(tout.get(nom, []))
    if nombre:
        tout[nom] = []
        try:
            ecrire_passkeys(tout)
        except OSError as e:
            return jsonify({"error": f"Clés non retirées : {e}"}), 500
        journaliser("passkey", qui=nom, action="retrait", ip=_adresse_client())
    return jsonify({"ok": True, "retirees": nombre})


@flask_app.delete("/api/mon-compte/passkeys/<path:identifiant>")
@require_auth
def api_passkey_supprimer(identifiant):
    nom = utilisateur_courant()
    tout = lire_passkeys()
    liste = tout.get(nom, [])
    restantes = [k for k in liste if k["id"] != identifiant]
    if len(restantes) == len(liste):
        return jsonify({"error": "Clé inconnue."}), 404
    tout[nom] = restantes
    try:
        ecrire_passkeys(tout)
    except OSError as e:
        return jsonify({"error": f"Clé non supprimée : {e}"}), 500
    journaliser("passkey", qui=nom, action="retrait", ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/login/passkey/options")
def login_passkey_options():
    """Prepare une connexion par cle d'acces.

    Sans nom de compte, la demande porte sur les cles decouvrables : c'est
    le navigateur qui sait de qui il s'agit, et le serveur ne revele donc
    aucune liste de comptes.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    refus = _refus_passkey()
    if refus:
        return refus
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    rp_id, _, _ = passkey_contexte()
    nom = (request.get_json(force=True, silent=True) or {}).get("nom") or ""
    nom = (nom or "").strip().lower()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=_descripteurs(nom) if nom else None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session["passkey_defi"] = base64.b64encode(options.challenge).decode()
    return Response(options_to_json(options), mimetype="application/json")


@flask_app.post("/login/passkey")
def login_passkey():
    """Ouvre la session si la signature est bonne.

    Une cle d'acces vaut le mot de passe ET le second facteur : la personne
    a prouve la possession de l'appareil, et l'appareil a verifie que c'est
    bien elle (empreinte ou code). C'est pour cela que l'enregistrement
    exige la verification d'utilisateur -- sans elle, ce ne serait qu'une
    moitie, et ouvrir une session sur cette moitie serait un recul.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    refus = _refus_passkey()
    if refus:
        return refus
    proprietaire, erreur = _verifier_cle()
    if erreur:
        return erreur
    return _ouvrir_session_par_cle(proprietaire)


def _verifier_cle(nom_attendu=None):
    """Verifie la signature d'une cle d'acces.

    Rend (proprietaire, None), ou (None, reponse d'erreur). Avec nom_attendu
    (etape 2 de la connexion), la cle doit appartenir a ce compte.
    """
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes
    defi = session.get("passkey_defi")
    if not defi:
        return None, (jsonify({"error": "Recommence : aucune demande en attente."}), 400)
    d = request.get_json(force=True, silent=True) or {}
    credential = d.get("credential") or {}
    identifiant = credential.get("id") or ""

    # A qui appartient cette cle ? On cherche par identifiant, jamais par le
    # nom annonce par le client : c'est la signature qui fait foi.
    proprietaire, enregistree = None, None
    for compte, cles in lire_passkeys().items():
        for k in cles:
            if k["id"] == identifiant:
                proprietaire, enregistree = compte, k
                break
        if proprietaire:
            break
    if not enregistree:
        register_failed_attempt()
        journaliser("echec", qui="", motif="cle d'acces inconnue", ip=_adresse_client())
        return None, (jsonify({"error": "Clé d'accès inconnue."}), 401)

    # A l'etape 2, la cle doit etre celle du compte dont le mot de passe
    # vient d'etre reconnu : la cle d'un autre compte ne confirme rien.
    if nom_attendu and proprietaire != nom_attendu:
        register_failed_attempt()
        journaliser("echec", qui=nom_attendu, motif="cle d'acces d'un autre compte",
                    ip=_adresse_client())
        return None, (jsonify({"error": "Cette clé n'appartient pas à ce compte."}), 401)

    # Des que l'on sait a qui est la cle, et avant toute cryptographie : une
    # cle d'acces est un excellent second facteur, mais elle voyage avec son
    # porteur et ne dit rien de l'endroit d'ou l'on appelle. Sans ce verrou
    # ici, il suffirait de passer par cette porte-ci plutot que par le mot de
    # passe.
    if proprietaire == NOM_ADMIN:
        hors = refus_admin_hors_reseau()
        if hors:
            register_failed_attempt()   # meme raison que ci-dessus
            journaliser("refus", qui=NOM_ADMIN, motif="hors reseau local",
                        moyen="cle d'acces", ip=_adresse_client())
            return None, (jsonify({"error": hors}), 403)

    rp_id, origine, _ = passkey_contexte()
    try:
        verifiee = verify_authentication_response(
            credential=credential,
            expected_challenge=base64.b64decode(defi),
            expected_rp_id=rp_id,
            expected_origin=origine,
            credential_public_key=base64url_to_bytes(enregistree["cle_publique"]),
            credential_current_sign_count=enregistree.get("compteur", 0),
            require_user_verification=True,
        )
    except Exception as e:
        register_failed_attempt()
        journaliser("echec", qui=proprietaire, motif="cle d'acces", ip=_adresse_client())
        return None, (jsonify({"error": detail_pour_admin(
            "Clé refusée. Réessaie, ou connecte-toi avec ton mot de passe.", e)}), 401)

    # Le compteur ne doit jamais reculer : une cle clonee se trahit la.
    tout = lire_passkeys()
    for k in tout.get(proprietaire, []):
        if k["id"] == identifiant:
            k["compteur"] = verifiee.new_sign_count
            k["dernier"] = int(time.time())
    try:
        ecrire_passkeys(tout)
    except OSError:
        pass

    return proprietaire, None


def _ouvrir_session_par_cle(proprietaire):
    """Ouvre la session du proprietaire d'une cle verifiee."""
    est_administrateur = proprietaire == NOM_ADMIN
    if not est_administrateur:
        compte = lire_utilisateurs().get(proprietaire)
        if not compte:
            return jsonify({"error": "Compte inconnu."}), 401
        if compte.get("attente_email"):
            return jsonify({"error": "Confirme d'abord ton adresse mail."}), 403
        if compte_suspendu(compte):
            journaliser("echec", qui=proprietaire, motif="compte suspendu",
                        moyen="cle d'acces", ip=_adresse_client())
            return jsonify({"error": "Ce compte est suspendu. Demande à "
                                     "l'administrateur de le réactiver."}), 403
        _lever_suspension_code_mail(proprietaire)

    session.pop("passkey_defi", None)
    session.permanent = True
    session["authed"] = True
    # La date d'OUVERTURE, posee ici et jamais repoussee : c'est elle qui
    # fait la limite absolue des trente jours. Le cookie, lui, tient
    # l'inactivite de son cote.
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_ADMIN if est_administrateur else ROLE_UTILISATEUR
    session["utilisateur"] = proprietaire
    journaliser("connexion", qui=proprietaire, role=session["role"],
                moyen="cle d'acces", ip=_adresse_client())
    if est_administrateur:
        signaler_connexion_admin()
    return jsonify({"ok": True, "role": session["role"]})


@flask_app.get("/api/activite")
@require_admin
def api_activite():
    """Le journal des acces, et son resume.

    Reserve a l'administrateur : c'est le seul role a qui la question « qui a
    ouvert quoi » se pose, et la reponse contient des adresses IP.

    Postgres d'abord quand il repond : il garde tout, la ou la base locale
    est plafonnee et oublie le plus ancien. Il ne remplace jamais la base
    locale -- un Postgres eteint rend simplement la vue plus courte, et la
    reponse dit d'ou viennent les lignes.
    """
    app_ = request.args.get("app") or None
    qui = request.args.get("qui")
    if pg_disponible():
        try:
            # Le resume vient de la base LUI AUSSI. Calcule sur le fichier, il
            # comptait sur une fenetre glissante d'un megaoctet : le total
            # d'ouvertures d'un projet diminuait tout seul a mesure que le
            # journal tournait.
            return jsonify({"evenements": pg_lire_acces(app=app_, qui=qui),
                            "resume": pg_resume_acces(), "source": "postgres",
                            "pg": _pg_etat["pret"]})
        except Exception as e:
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
    return jsonify({"evenements": lire_acces(app=app_, qui=qui),
                    "resume": resume_acces(), "source": "fichier",
                    "pg": False, "pg_erreur": _pg_etat["erreur"]})


@flask_app.get("/api/activite/carte")
@require_admin
def api_activite_carte():
    """La carte de chaleur : une case par jour, une annee en un coup d'oeil.

    Meme role que le calendrier de contributions de GitHub, et pour la meme
    raison : une liste d'evenements dit ce qui s'est passe, une carte dit
    QUAND -- les periodes creuses, les week-ends, le projet qu'on n'a plus
    ouvert depuis six semaines. Aucune liste ne montre cela.

    Les filtres sont ceux qu'on se pose : une personne, une application, ou
    les deux.
    """
    qui = request.args.get("qui")
    app_ = request.args.get("app") or None
    # L'annee demandee arrive par l'URL : elle se borne, elle ne se croit
    # pas. Illisible ou farfelue, on retombe sur l'annee en cours plutot que
    # de rendre une carte vide sans expliquer pourquoi.
    courante = annee_courante()
    brut = request.args.get("annee")
    try:
        annee = int(brut) if brut else courante
    except ValueError:
        annee = courante
    if not 1970 <= annee <= courante + 1:
        annee = courante

    if pg_disponible():
        try:
            basse, haute = pg_annees_activite()
            return jsonify({"jours": pg_carte_activite(annee, qui=qui, app=app_),
                            "annee": annee,
                            "annees": annees_proposees(basse, haute),
                            "fuseau": FUSEAU_JOURNAL,
                            "source": "postgres"})
        except Exception as e:                                    # noqa: BLE001
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
    # Repli : le fichier oublie au-dela d'un megaoctet, donc la carte est plus
    # courte. Le dire plutot que d'afficher des jours vides qui laisseraient
    # croire a une inactivite.
    basse, haute = annees_activite()
    return jsonify({"jours": carte_activite(annee, qui=qui, app=app_),
                    "annee": annee,
                    "annees": annees_proposees(basse, haute),
                    "fuseau": FUSEAU_JOURNAL,
                    "source": "fichier", "pg_erreur": _pg_etat["erreur"]})


@flask_app.get("/api/mon-compte")
@require_auth
def api_mon_compte():
    """Ce que la session peut dire d'elle-meme, et rien de plus."""
    compte = _mon_compte()
    _, smtp_ok = smtp_utilisable()
    return jsonify({
        "nom": utilisateur_courant(),
        "role": role_courant(),
        "email": (compte or {}).get("email") or "",
        "email_verifie": bool((compte or {}).get("email_verifie")),
        "passkeys": len(passkeys_du_compte(utilisateur_courant())),
        # Sans serveur d'envoi, la page n'affiche pas un bouton qui echouera.
        "smtp": smtp_ok,
    })


@flask_app.post("/api/mon-compte/email")
@require_auth
def api_mon_email():
    """Declare ou change sa propre adresse, et envoie le code de suite.

    Reserve aux comptes utilisateurs : l'administrateur n'en a pas -- son
    compte n'est pas dans le registre, et les alertes ont deja leur
    destinataire.
    """
    compte = _mon_compte()
    if compte is None:
        return jsonify({"error": "Le compte d'administration n'a pas d'adresse "
                                 "propre : règle les destinataires des alertes."}), 400
    refus = refus_authentification_ancienne()
    if refus:
        return refus
    adresse = email_valide((request.get_json(force=True, silent=True) or {}).get("email"))
    if not adresse:
        return jsonify({"error": "Adresse mail invalide."}), 400

    comptes = lire_utilisateurs()
    nom = utilisateur_courant()
    if nom not in comptes:
        return jsonify({"error": "Compte inconnu."}), 404
    comptes[nom]["email"] = adresse
    comptes[nom]["email_verifie"] = False
    code = poser_code_email(comptes[nom])
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Adresse non enregistrée : {e}"}), 500
    try:
        envoyer_code_email(adresse, nom, code)
    except Exception as e:
        # L'adresse est enregistree, le mail n'est pas parti : le dire tel
        # quel, plutot que de laisser attendre un code qui ne viendra pas.
        return jsonify({"error": detail_pour_admin(
            "Adresse enregistrée, mais le mail n'est pas parti. Réessaie dans un instant.", e)}), 502
    return jsonify({"ok": True, "envoye": True})


@flask_app.post("/api/mon-compte/email/code")
@require_auth
def api_mon_email_code():
    """Renvoie un code a l'adresse deja declaree."""
    compte = _mon_compte()
    if compte is None or not compte.get("email"):
        return jsonify({"error": "Déclare d'abord une adresse."}), 400
    en_cours = compte.get("email_code") or {}
    attente = CODE_EMAIL_DELAI - (int(time.time()) - en_cours.get("envoye", 0))
    if attente > 0:
        # Un bouton qui renvoie sans limite est un moyen d'inonder une boite
        # mail que la personne ne possede peut-etre pas.
        return jsonify({"error": f"Un code vient d'être envoyé. Attends "
                                 f"{attente} seconde{'s' if attente > 1 else ''}."}), 429

    comptes = lire_utilisateurs()
    nom = utilisateur_courant()
    code = poser_code_email(comptes[nom])
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Code non enregistré : {e}"}), 500
    try:
        envoyer_code_email(comptes[nom]["email"], nom, code)
    except Exception as e:
        return jsonify({"error": detail_pour_admin(
            "Le mail n'est pas parti. Réessaie dans un instant.", e)}), 502
    return jsonify({"ok": True})


@flask_app.post("/api/mon-compte/email/confirmer")
@require_auth
def api_mon_email_confirmer():
    """Confirme l'adresse avec le code recu."""
    compte = _mon_compte()
    if compte is None:
        return jsonify({"error": "Rien à confirmer."}), 400
    comptes = lire_utilisateurs()
    nom = utilisateur_courant()
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    ok, message = verifier_code_email(comptes[nom], code)
    try:
        # Ecrit dans les deux cas : le compteur d'essais et l'expiration
        # consommee doivent survivre a la requete, sinon la limite ne limite
        # rien.
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"État non enregistré : {e}"}), 500
    if not ok:
        return jsonify({"error": message}), 400
    return jsonify({"ok": True})


@flask_app.post("/mot-de-passe/oubli")
def mot_de_passe_oubli():
    """Envoie un code de reinitialisation, si tout s'y prete.

    LA REPONSE EST TOUJOURS LA MEME, quoi qu'il arrive : compte inconnu,
    adresse absente, adresse non verifiee, envoi impossible. Repondre
    autrement transformerait ce formulaire en annuaire -- on y taperait des
    noms jusqu'a trouver ceux qui existent.

    Le compte d'administration n'a pas d'adresse : son mot de passe est celui
    de credentials.env, et il ne se reinitialise pas d'ici. La page le dit,
    et c'est une information publique -- elle ne revele l'existence de
    personne.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = nom_utilisateur_valide((request.get_json(force=True, silent=True) or {}).get("nom"))
    register_failed_attempt()   # compte dans la limite : un envoi de mail coute

    comptes = lire_utilisateurs()
    compte = comptes.get(nom) if nom else None
    # OPTION A : la reinitialisation par mail n'est ouverte qu'aux comptes qui
    # ont un facteur HORS de la boite mail (application ou Face ID). Sinon,
    # la boite mail seule suffirait a prendre le compte : elle donnerait le
    # mot de passe ET le code de connexion. Ces comptes passent par
    # l'administrateur.
    if (compte and compte.get("email") and compte.get("email_verifie")
            and facteur_fort(nom, compte)):
        en_cours = compte.get("reinit_code") or {}
        # Pas plus d'un envoi par minute et par compte : sans cela, ce
        # formulaire devient un robinet a mails vers l'adresse de quelqu'un
        # d'autre.
        if int(time.time()) - int(en_cours.get("envoye") or 0) >= CODE_EMAIL_DELAI:
            code = preparer_code_reinit(compte)
            try:
                envoyer_code_reinit(compte["email"], nom, code)
                ecrire_utilisateurs(comptes)
                journaliser("mot-de-passe", qui=nom, action="code envoye",
                            ip=_adresse_client())
            except Exception as e:                                # noqa: BLE001
                # On ne le dit pas au visiteur -- ce serait lui apprendre que
                # le compte existe -- mais l'administrateur doit le voir.
                journaliser("mot-de-passe", qui=nom,
                            action=f"envoi impossible ({type(e).__name__})",
                            ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/mot-de-passe/reinitialiser")
def mot_de_passe_reinitialiser():
    """Pose le nouveau mot de passe, code en main.

    N'ouvre pas de session : on se reconnecte ensuite, second facteur
    compris. Un code recu par mail prouve qu'on releve l'adresse, pas qu'on
    est la personne -- le second facteur, lui, reste exige.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    nouveau = (d.get("nouveau") or "").strip()
    if len(nouveau) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    compte = comptes.get(nom) if nom else None
    if not compte:
        register_failed_attempt()
        # Message identique a celui d'un code faux : meme raison qu'au-dessus.
        return jsonify({"error": "Code incorrect."}), 400

    ok, message = verifier_code_reinit(compte, d.get("code"))
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"État non enregistré : {e}"}), 500
    if not ok:
        register_failed_attempt()
        return jsonify({"error": message}), 400

    sel = secrets.token_hex(16)
    compte["sel"] = sel
    compte["hash"] = derive_mot_de_passe(nouveau, sel)
    # Le mail vient de servir : il ne sera pas, en plus, le second facteur.
    compte["code_mail_suspendu"] = True
    fermer_les_autres_sessions(compte, nom=nom)
    compte.pop("code_connexion", None)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Mot de passe non enregistré : {e}"}), 500
    journaliser("mot-de-passe", qui=nom, action="reinitialise par mail",
                ip=_adresse_client())
    return jsonify({"ok": True})


# ------------------------------ invitations ------------------------------

def _invitation_publique(empreinte, inv):
    return {"id": empreinte, "email": inv.get("email") or "",
            "projets": sorted(inv.get("projets") or []),
            "cree": inv.get("cree"), "expire": inv.get("expire")}


def _envoyer_et_rendre(empreinte, jeton, adresse):
    """Envoie le lien et rend la reponse. Le lien est TOUJOURS rendu a
    l'administrateur : si le mail ne part pas (pas de serveur d'envoi,
    Mailpit en mode test), il peut le transmettre lui-meme."""
    lien = lien_invitation(jeton)
    envoye, detail = True, ""
    try:
        envoyer_invitation(adresse, lien)
    except Exception as e:                                        # noqa: BLE001
        envoye, detail = False, f"{type(e).__name__}: {e}"
    _, relais = smtp_utilisable()
    return jsonify({"ok": True, "id": empreinte, "lien": lien, "envoye": envoye,
                    # Remis a Mailpit sans serveur d'envoi : garde, pas parti.
                    "relaye": envoye and relais, "detail": detail})


@flask_app.get("/api/invitations")
@require_admin
def api_invitations():
    return jsonify({"invitations": sorted(
        (_invitation_publique(k, v) for k, v in lire_invitations().items()),
        key=lambda i: -(i["cree"] or 0))})


@flask_app.post("/api/invitations")
@require_admin
def api_invitation_creer():
    refus = _refus_sans_smtp()
    if refus:
        return refus
    d = request.get_json(force=True, silent=True) or {}
    adresse = email_valide(d.get("email"))
    if not adresse:
        return jsonify({"error": "Adresse mail invalide."}), 400
    if any((c.get("email") or "").lower() == adresse.lower()
           for c in lire_utilisateurs().values() if isinstance(c, dict)):
        return jsonify({"error": "Un compte utilise déjà cette adresse."}), 400
    try:
        empreinte, jeton = creer_invitation(adresse, _projets_valides(d.get("projets"), load()))
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Invitation non enregistrée : {e}"}), 500
    journaliser("compte", qui=utilisateur_courant() or NOM_ADMIN, cible=adresse,
                action="invitation", ip=_adresse_client())
    return _envoyer_et_rendre(empreinte, jeton, adresse)


@flask_app.post("/api/invitations/<empreinte>/renvoyer")
@require_admin
def api_invitation_renvoyer(empreinte):
    """Un nouveau lien : l'ancien, peut-etre perdu dans un mail, ne vaut plus
    rien. Le delai repart de zero."""
    refus = _refus_sans_smtp()
    if refus:
        return refus
    inv = lire_invitations().get(empreinte)
    if not inv:
        return jsonify({"error": "Invitation inconnue ou expirée."}), 404
    try:
        nouvelle, jeton = creer_invitation(inv["email"], inv.get("projets") or [])
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Invitation non enregistrée : {e}"}), 500
    return _envoyer_et_rendre(nouvelle, jeton, inv["email"])


@flask_app.delete("/api/invitations/<empreinte>")
@require_admin
def api_invitation_annuler(empreinte):
    invitations = lire_invitations()
    if invitations.pop(empreinte, None) is None:
        return jsonify({"error": "Invitation inconnue ou expirée."}), 404
    try:
        ecrire_invitations(invitations)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Invitation non annulée : {e}"}), 500
    return jsonify({"ok": True})


@flask_app.get("/invitation/<jeton>")
def invitation_page(jeton):
    """La page de connexion, en mode invitation : c'est son script qui lit
    le jeton dans l'adresse. Une seule page a maintenir."""
    return login_page(invitation=True)


@flask_app.get("/api/invitation/<jeton>")
def api_invitation_etat(jeton):
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    _, inv = invitation_par_jeton(jeton)
    if not inv:
        register_failed_attempt()
        return jsonify({"valide": False}), 404
    return jsonify({"valide": True, "email": inv["email"]})


@flask_app.post("/invitation/<jeton>")
def invitation_accepter(jeton):
    """Cree le compte et ouvre la session.

    Le lien recu par mail tient lieu de second facteur pour CETTE premiere
    connexion : il prouve qu'on releve l'adresse, il ne sert qu'une fois et
    il expire. Les connexions suivantes demandent le mot de passe et un code
    -- ou Face ID, que la page propose d'activer aussitot.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    empreinte, inv = invitation_par_jeton(jeton)
    if not inv:
        register_failed_attempt()
        return jsonify({"error": "Ce lien d'invitation n'est plus valable. "
                                 "Demande-en un nouveau à ton administrateur."}), 404
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    mdp = (d.get("mot_de_passe") or "").strip()
    if not nom:
        return jsonify({"error": "Nom invalide : 2 à 32 caractères, "
                                 "minuscules, chiffres, tiret ou souligné."}), 400
    if nom == NOM_ADMIN:
        return jsonify({"error": "Ce nom est réservé."}), 400
    if len(mdp) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    if nom in comptes:
        return jsonify({"error": "Ce nom est déjà pris."}), 400
    sel = secrets.token_hex(16)
    comptes[nom] = {
        "sel": sel,
        "hash": derive_mot_de_passe(mdp, sel),
        "projets": _projets_valides(inv.get("projets"), load()),
        "email": inv["email"],
        # Le lien est arrive a cette adresse et en est revenu : elle est
        # verifiee, sans code a recopier.
        "email_verifie": True,
        "cree": int(time.time()),
    }
    invitations = lire_invitations()
    invitations.pop(empreinte, None)
    try:
        # L'invitation d'abord : un lien qui survivrait au compte cree
        # permettrait d'en creer un second.
        ecrire_invitations(invitations)
        ecrire_utilisateurs(comptes)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    journaliser("compte", qui=nom, action="invitation acceptée", ip=_adresse_client())
    return _ouvrir_session_utilisateur(nom)


@flask_app.get("/qr/totp.svg")
def qr_totp():
    """Le QR code de l'inscription en cours, et de rien d'autre.

    L'adresse otpauth vient de la session signee, jamais de l'URL : un
    secret dans une adresse se retrouve dans l'historique du navigateur,
    dans les journaux d'acces et dans le referer de la page suivante.

    Pas d'authentification a exiger ici -- une session qui porte une
    inscription en attente n'est pas encore authentifiee, c'est justement
    l'etape ou on se trouve. Ce que la route revele, c'est le secret que le
    serveur vient de tirer pour CETTE session.
    """
    uri = session.get("totp_uri") or ""
    svg = qr_svg(uri) if uri else ""
    if not svg:
        # 404 et non 500 : sans bibliotheque QR, la page affiche la cle a
        # saisir a la main et l'image manquante se masque d'elle-meme.
        return Response("", status=404)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@flask_app.get("/api/categories")
@require_auth
def api_categories():
    """Lisible par tous les comptes : le hub s'en sert pour se ranger.

    « verrouillees » : celles que la page montre sans les laisser modifier.
    """
    return jsonify({"categories": categories_affichees(), "verrouillees": [CATEGORIE_CODELAB]})


@flask_app.put("/api/categories")
@require_admin
def api_categories_enregistrer():
    """Remplace la liste entiere, dans l'ordre recu.

    Renvoie le nombre de projets qui perdent leur rangement, pour que la page
    puisse le dire : supprimer une categorie ne casse rien, mais cela deplace
    des projets, et cela doit se voir.
    """
    brut = (request.get_json(force=True, silent=True) or {}).get("categories")
    if not isinstance(brut, list):
        return jsonify({"error": "Liste de catégories attendue."}), 400

    propres, vues = [], set()
    for x in brut:
        c = categorie_propre(x)
        # CodeLab revient tel quel avec la liste (la page l'affiche) : il
        # n'est pas enregistre, il est pose d'office.
        if est_categorie_codelab(c):
            continue
        # Insensible a la casse pour les doublons : "Outils" et "outils"
        # seraient deux tiroirs pour la meme chose.
        if c and c.lower() not in vues:
            propres.append(c)
            vues.add(c.lower())
    if len(propres) > CATEGORIES_MAX:
        return jsonify({"error": f"{CATEGORIES_MAX} catégories au maximum."}), 400

    apps = load()
    orphelins = [n for n, a in apps.items()
                 if (a.get("categorie") or "") and a["categorie"] not in propres]
    try:
        ecrire_categories(propres)
    except OSError as e:
        return jsonify({"error": f"Catégories non enregistrées : {e}"}), 500

    # Les projets d'une categorie disparue redeviennent non ranges, tout de
    # suite : un champ qui pointe vers un tiroir inexistant se rappellerait a
    # nous plus tard, au pire moment.
    if orphelins:
        for n in orphelins:
            apps[n]["categorie"] = ""
        save(apps)
    return jsonify({"ok": True, "categories": [CATEGORIE_CODELAB] + propres,
                    "verrouillees": [CATEGORIE_CODELAB], "declasses": len(orphelins)})


@flask_app.get("/api/securite")
@require_admin
def api_securite():
    """L'etat des reglages de securite, pour la page Parametres."""
    return jsonify({
        "totp": totp_actif(),
        # Deux choses distinctes, qu'il ne faut pas confondre dans la page :
        #
        #   https_constate : ce que la requete en cours montre reellement ;
        #   https          : ce que le panneau a ete REGLE a croire.
        #
        # Les afficher separement, c'est repondre a la seule question utile
        # quand rien ne marche : est-ce le TLS qui manque, ou le reglage ?
        "https_constate": request.headers.get("X-Forwarded-Proto", "").lower() == "https"
                          or request.scheme == "https",
        "https": https_actif(),
        "cookie_secure": https_actif(),
        "adresse_publique": adresse_publique(),
        # Toujours vrai : plus un reglage. Garde pour la page et le diagnostic.
        "admin_reseau_local": admin_limite_au_reseau_local(),
        "client_local": client_est_local(),
        "client_ip": _client_ip(),
        # Les applications sont-elles servies dans une autre origine que le
        # panneau ? C'est ce qui empeche une XSS dans l'une d'elles d'atteindre
        # le panneau, et c'est invisible sans le dire.
        # Le nombre de cles d'acces du compte connecte : le bilan de securite
        # en a besoin, et un second appel pour un entier serait du gaspillage.
        "passkeys": len(passkeys_du_compte(utilisateur_courant() or NOM_ADMIN)),
        "origines_separees": origines_separees(),
        "adresse_applications": modele_adresse_application(),
        "port_applications": APPS_PORT,
    })


@flask_app.post("/api/compte/mot-de-passe")
@require_auth
def api_changer_mot_de_passe():
    """Change le mot de passe du compte connecte, le sien seulement.

    Pourquoi cela n'existait pas, et pourquoi c'est un manque : le mot de
    passe d'administration ne se changeait qu'en editant credentials.env sur
    le serveur, c'est-a-dire en s'y connectant en SSH. Un secret qu'on ne
    peut pas changer facilement est un secret qu'on ne change jamais -- et
    celui-la donne l'execution de commandes sur la machine.

    L'ANCIEN MOT DE PASSE EST EXIGE, meme pour une session deja ouverte. Une
    session volee ne doit pas pouvoir verrouiller le compte de son
    proprietaire : sans cette verification, un cookie capture suffirait a
    prendre la place de quelqu'un definitivement.
    """
    d = request.get_json(force=True, silent=True) or {}
    ancien = (d.get("ancien") or "").strip()
    nouveau = (d.get("nouveau") or "").strip()

    if len(nouveau) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400
    if nouveau == ancien:
        return jsonify({"error": "Le nouveau mot de passe est identique à l'ancien."}), 400

    if est_admin():
        # LE COMPTE D'ADMINISTRATION NE CHANGE PAS SON MOT DE PASSE ICI.
        #
        # Le sien est celui de credentials.env, et ce fichier est la source :
        # c'est lui qu'on lit pour se depanner quand le panneau ne repond
        # plus, lui qu'on copie en changeant de machine, lui que le
        # diagnostic controle. Laisser le panneau le reecrire, c'etait
        # accepter deux sources pour un meme secret -- et decouvrir laquelle
        # fait foi le jour ou elles divergent, c'est-a-dire au pire moment.
        return jsonify({
            "error": "Le mot de passe d'administration se change dans "
                     "credentials.env, sur le serveur — c'est lui qui fait "
                     "foi. Le panneau ne le réécrit pas."}), 403

    nom = utilisateur_courant()
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte introuvable."}), 404
    if not verifie_mot_de_passe(compte, ancien):
        register_failed_attempt()
        journaliser("echec", qui=nom, motif="changement de mot de passe",
                    ip=_adresse_client())
        return jsonify({"error": "Ancien mot de passe incorrect."}), 403
    compte["sel"] = secrets.token_hex(16)
    compte["hash"] = derive_mot_de_passe(nouveau, compte["sel"])
    fermer_les_autres_sessions(compte, garder_celle_ci=True, nom=nom)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    journaliser("mot-de-passe", qui=nom, ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/api/securite/totp/preparer")
@require_admin
def api_totp_preparer():
    """Tire un secret candidat, sans rien enregistrer.

    Rien n'est persiste tant qu'un code valide n'a pas ete fourni : un secret
    mal recopie dans l'application d'authentification enfermerait dehors des
    le prochain retour sur la page de connexion.
    """
    if totp_actif():
        return jsonify({"error": "La double authentification est déjà activé."}), 400
    candidat = totp_nouveau_secret()
    session["totp_candidat"] = candidat
    session["totp_uri"] = totp_uri(candidat)
    return jsonify({"secret": candidat, "uri": session["totp_uri"],
                    "compte": TOTP_COMPTE, "qr": "/qr/totp.svg"})


@flask_app.post("/api/securite/totp/activer")
@require_admin
def api_totp_activer():
    global _totp_secret
    candidat = session.get("totp_candidat")
    if not candidat:
        return jsonify({"error": "Recommence la préparation : aucun secret en attente."}), 400
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(candidat, code):
        return jsonify({"error": "Code incorrect. Vérifie l'heure de ton téléphone."}), 400
    if not ecrire_bloc_panneau(admin_password(), flask_app.secret_key, candidat):
        return jsonify({"error": "credentials.env n'a pas pu être écrit : rien n'a été activé."}), 500
    _totp_secret = candidat
    session.pop("totp_candidat", None)
    session.pop("totp_uri", None)
    return jsonify({"ok": True})


@flask_app.post("/api/securite/totp/desactiver")
@require_admin
def api_totp_desactiver():
    """Desactivation protegee par un code valide.

    La session admin seule ne suffit pas : une session volee pourrait sinon
    retirer le second facteur, ce qui reviendrait a ne pas en avoir.
    """
    global _totp_secret
    if not totp_actif():
        return jsonify({"ok": True})
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(_totp_secret, code):
        register_failed_attempt()
        return jsonify({"error": "Code incorrect."}), 400
    if not ecrire_bloc_panneau(admin_password(), flask_app.secret_key, ""):
        return jsonify({"error": "credentials.env n'a pas pu être écrit."}), 500
    _totp_secret = ""
    return jsonify({"ok": True})


@flask_app.get("/api/alertes")
@require_admin
def api_alertes():
    """L'etat des alertes et des deux configurations d'envoi.

    Aucun mot de passe n'est renvoye -- seulement le fait qu'il existe. Un
    champ de mot de passe pre-rempli est une valeur qu'on renvoie sans le
    vouloir a chaque enregistrement, et un secret qui traine dans une page
    ouverte.
    """
    reglages = lire_alertes()
    par_app = reglage("alertes_par_app", {}) or {}
    origine = smtp_origine()
    personnalisee = lire_smtp_personnalise()
    effective, manquants = config_smtp()

    def _vue(cfg):
        if cfg is None:
            return None
        return {"host": cfg["host"], "port": cfg["port"], "tls": cfg["tls"],
                "user": cfg["user"], "expediteur": cfg["expediteur"],
                "mot_de_passe_defini": bool(cfg["password"])}

    return jsonify({
        "actif": reglages["actif"],
        "admin": alertes_admin(),
        # Les deux configurations, cote a cote : la page doit pouvoir dire
        # laquelle sert et laquelle attend en repli.
        "origine": _vue(origine),
        "origine_utilisable": not smtp_incomplet(origine),
        "personnalisee": _vue(personnalisee),
        "source": "personnalisee" if personnalisee else "origine",
        "manquants": manquants,
        "smtp_ok": not smtp_incomplet(effective),
        "incidents": sorted(_alertes_en_cours),
        # Les destinataires propres a chaque application, pour la page.
        "par_application": {nom: _adresses(par_app.get(nom))
                            for nom in sorted(load())},
    })


@flask_app.post("/api/alertes")
@require_admin
def api_alertes_enregistrer():
    """Enregistre l'interrupteur, l'adresse d'administration, et -- si le
    formulaire en apporte une -- une configuration d'envoi personnalisee.

    CELLE-CI DOIT FAIRE SES PREUVES AVANT D'ETRE ECRITE. On se connecte
    reellement au serveur, on chiffre, on s'authentifie. C'est ce qui
    remplace l'ancien bouton "envoyer un mail de test" : un test qu'il
    fallait penser a lancer, et dont l'oubli ne se voyait pas. Ici, une
    configuration qui ne marche pas n'entre tout simplement pas.

    La configuration d'origine (credentials.env) n'est jamais touchee : elle
    reste le repli.
    """
    d = request.get_json(force=True, silent=True) or {}

    admin = _adresses(d.get("admin"))
    actif = bool(d.get("actif"))
    if actif and not admin:
        return jsonify({"error": "Une adresse d'alerte de l'administrateur est "
                                 "nécessaire pour activer les alertes : c'est "
                                 "elle qui reçoit ce qu'aucune application "
                                 "n'a pris en charge."}), 400

    # --- la configuration personnalisee -----------------------------------
    if "smtp" in d:
        smtp = d.get("smtp")
        if not smtp or not str((smtp or {}).get("host") or "").strip():
            # Champ serveur vide = revenir a la configuration d'origine.
            # Toujours permis : c'est la marche arriere.
            effacer_smtp_personnalise()
        else:
            candidate = _normalise_smtp(smtp)
            # Mot de passe vide = inchange. Le formulaire ne le pre-remplit
            # pas : sans cette regle, tout enregistrement l'effacerait.
            if not candidate["password"]:
                ancienne = lire_smtp_personnalise()
                if ancienne and ancienne["host"] == candidate["host"] \
                        and ancienne["user"] == candidate["user"]:
                    candidate["password"] = ancienne["password"]
            ok, detail = verifier_smtp(candidate)
            if not ok:
                return jsonify({
                    "error": "Ce serveur d'envoi n'a pas repondu comme attendu, "
                             "rien n'a été enregistré. La configuration "
                             "d'origine continue de servir.",
                    "detail": detail}), 400
            try:
                ecrire_smtp_personnalise(candidate)
            except (OSError, RuntimeError) as e:
                return jsonify({"error": f"Configuration non enregistrée : {e}"}), 500

        # Mailpit relaie par ce meme serveur : il suit le changement.
        publier_relais_mail()

    # --- l'adresse d'administration, dans le bloc partage ------------------
    try:
        ecrire_reglage("alertes_admin", admin or None)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Adresse non enregistrée : {e}"}), 500

    try:
        ecrire_alertes(actif, admin)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Réglages non enregistrés : {e}"}), 500

    _, manquants = config_smtp()
    return jsonify({"ok": True, "manquants": manquants,
                    "source": "personnalisee" if lire_smtp_personnalise() else "origine"})


@flask_app.put("/api/alertes/application/<name>")
@require_admin
def api_alertes_application(name):
    """Les destinataires propres a une application.

    Une application de facturation ne previent pas les memes personnes qu'un
    site vitrine. L'adresse d'administration s'ajoute toujours a celles-ci --
    une application dont on a oublie de remplir la liste ne tombe donc jamais
    en silence.
    """
    apps = load()
    if name not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True, silent=True) or {}
    adresses = _adresses(d.get("alertes"))
    par_app = reglage("alertes_par_app", {}) or {}
    if adresses:
        par_app[name] = adresses
    else:
        par_app.pop(name, None)
    try:
        ecrire_reglage("alertes_par_app", par_app or None)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    return jsonify({"ok": True, "alertes": adresses,
                    "destinataires": destinataires_alerte(name, apps)})


@flask_app.get("/api/utilisateurs")
@require_admin
def api_utilisateurs():
    """Les comptes, sans rien qui ressemble a un mot de passe.

    Ni le hash ni le sel ne sortent d'ici : les afficher n'aide personne et
    les met dans l'historique du navigateur.
    """
    comptes = lire_utilisateurs()
    return jsonify({"utilisateurs": [
        {"nom": nom,
         "projets": sorted(p for p in c.get("projets", []) if not est_app_par_defaut(p)),
         # Pas le secret, seulement le fait qu'il existe : "en attente"
         # signale un compte cree mais jamais utilise, ce qui se voit d'un
         # coup d'oeil et se corrige en relancant la personne.
         "totp": bool(c.get("totp")),
         # Le nombre de cles, pas les cles : l'administrateur doit pouvoir
         # constater qu'un compte en a (et les retirer si l'appareil est
         # perdu), pas les lire.
         "passkeys": len(lire_passkeys().get(nom, [])),
         "email": c.get("email") or "",
         "email_verifie": bool(c.get("email_verifie")),
         # Un compte cree librement qui n'a pas encore confirme son adresse
         # n'ouvre aucune session : le dire evite de chercher pourquoi.
         "attente_email": bool(c.get("attente_email")),
         # Suspendu : le compte existe, garde tout, et n'ouvre plus rien.
         "suspendu": compte_suspendu(c),
         "cree": c.get("cree")}
        for nom, c in sorted(comptes.items())]})


def _projets_valides(brut, apps):
    """Ne garde que des projets qui existent vraiment.

    Un projet supprime puis recree sous le meme nom rendrait sinon un droit
    qu'on croyait perdu -- et la liste se remplirait de noms morts.
    """
    # LES APPLICATIONS RESERVEES NE S'ACCORDENT PAS. Le proxy les refusait
    # deja a un compte utilisateur, mais la fiche du compte les laissait
    # cocher et enregistrait le droit : l'administrateur croyait avoir
    # partage ce qui restait ferme. On ne garde que ce qui peut l'etre.
    return sorted({p for p in (brut or []) if isinstance(p, str) and p in apps
                   and not est_app_par_defaut(p)})


@flask_app.post("/api/utilisateurs")
@require_admin
def api_utilisateur_creer():
    refus = _refus_sans_smtp()
    if refus:
        return refus
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    mdp = (d.get("mot_de_passe") or "").strip()
    email = email_valide(d.get("email"))

    if d.get("email") and not email:
        return jsonify({"error": "Adresse mail invalide."}), 400
    if not nom:
        return jsonify({"error": "Nom invalide : 2 à 32 caractères, "
                                 "minuscules, chiffres, tiret ou souligné."}), 400
    if nom == NOM_ADMIN:
        return jsonify({"error": "Ce nom est celui du compte d'administration."}), 400
    if len(mdp) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    if nom in comptes:
        return jsonify({"error": "Ce compte existe déjà."}), 400

    sel = secrets.token_hex(16)
    comptes[nom] = {
        "sel": sel,
        "hash": derive_mot_de_passe(mdp, sel),
        "projets": _projets_valides(d.get("projets"), load()),
        "email": email,
        # Une adresse posee par l'administrateur n'est pas verifiee pour
        # autant : c'est la personne, a sa premiere visite, qui confirme
        # qu'elle la releve vraiment.
        "email_verifie": False,
        "cree": int(time.time()),
    }
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    return jsonify({"ok": True, "nom": nom})


@flask_app.put("/api/utilisateurs/<nom>")
@require_admin
def api_utilisateur_modifier(nom):
    d = request.get_json(force=True, silent=True) or {}
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte inconnu."}), 404

    # Champs absents = inchanges. Le formulaire des projets et celui du mot
    # de passe sont separes : envoyer l'un ne doit pas remettre l'autre a
    # zero.
    if "projets" in d:
        compte["projets"] = _projets_valides(d.get("projets"), load())
    # Suspendre / reactiver. Un booleen, et rien d'autre : les projets
    # autorises, l'adresse et les cles d'acces restent en place, c'est ce qui
    # distingue une suspension d'une suppression.
    if "suspendu" in d:
        if d.get("suspendu"):
            compte["suspendu"] = True
        else:
            compte.pop("suspendu", None)
        journaliser("compte", qui=utilisateur_courant() or NOM_ADMIN, cible=nom,
                    action="suspendu" if d.get("suspendu") else "réactivé",
                    ip=_adresse_client())
    mdp = (d.get("mot_de_passe") or "").strip()
    if mdp:
        if len(mdp) < MDP_MIN:
            return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400
        compte["sel"] = secrets.token_hex(16)
        compte["hash"] = derive_mot_de_passe(mdp, compte["sel"])
        fermer_les_autres_sessions(compte, nom=nom)

    # Telephone perdu ou remplace : on efface le secret, et la personne
    # s'inscrit de nouveau a sa prochaine connexion. C'est le seul moyen de
    # rendre l'acces sans jamais transmettre un secret par un canal tiers --
    # l'administrateur ne connait a aucun moment le facteur de quelqu'un
    # d'autre.
    if "email" in d:
        adresse = email_valide(d.get("email"))
        if d.get("email") and not adresse:
            return jsonify({"error": "Adresse mail invalide."}), 400
        if adresse != (compte.get("email") or ""):
            compte["email"] = adresse
            # Changer l'adresse annule la verification : sinon il suffirait
            # de remplacer une adresse verifiee par une autre pour heriter
            # de son statut.
            compte["email_verifie"] = False
            compte.pop("email_code", None)

    if d.get("reinitialiser_totp"):
        compte.pop("totp", None)

    # Appareil perdu : l'administrateur pouvait deja remettre le second
    # facteur a zero, mais pas retirer les cles d'acces -- le compte restait
    # ouvrable par un telephone egare. Meme geste, meme raison.
    if d.get("retirer_passkeys"):
        # Appareil perdu : la session qu'il porte peut-etre tombe aussi.
        fermer_les_autres_sessions(compte, nom=nom)
        tout = lire_passkeys()
        if tout.pop(nom, None) is not None:
            try:
                ecrire_passkeys(tout)
            except OSError as e:
                return jsonify({"error": f"Clés non retirées : {e}"}), 500
            journaliser("passkey", qui=nom, action="retrait par l'administrateur",
                        ip=_adresse_client())

    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    return jsonify({"ok": True})


@flask_app.get("/api/apps/<name>/acces")
@require_admin
def api_app_acces(name):
    """Qui accede a cette application.

    La meme information que dans la fiche d'un compte, prise par l'autre
    bout. On l'avait dans un seul sens : pour savoir qui ouvrait une
    application, il fallait ouvrir les fiches une par une -- et pour donner
    l'acces a cinq personnes, cinq allers-retours.
    """
    apps = load()
    if name not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    comptes = lire_utilisateurs()
    return jsonify({
        "application": name,
        "publique": (apps[name].get("visibility") or "privee") == "publique",
        "comptes": [{"nom": nom,
                     "acces": name in (c.get("projets") or []),
                     "email": c.get("email") or ""}
                    for nom, c in sorted(comptes.items())],
    })


@flask_app.put("/api/apps/<name>/acces")
@require_admin
def api_app_acces_modifier(name):
    """Donne ou retire l'acces a cette application, compte par compte.

    N'ecrit QUE cette application dans chaque fiche : les autres projets d'un
    compte ne sont pas touches. Envoyer la liste complete des projets aurait
    efface en silence ce qu'un autre onglet ouvert venait d'accorder.
    """
    # Une application par defaut ne s'accorde pas : elle est reservee a
    # l'administrateur, et le proxy le tient de son cote.
    refus = refus_configuration(name)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if name not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True, silent=True) or {}
    voulus = {str(n).strip() for n in (d.get("utilisateurs") or []) if str(n).strip()}

    comptes = lire_utilisateurs()
    inconnus = sorted(voulus - set(comptes))
    if inconnus:
        return jsonify({"error": "Compte inconnu : " + ", ".join(inconnus)}), 400

    for nom, compte in comptes.items():
        projets = [p for p in (compte.get("projets") or []) if p != name]
        if nom in voulus:
            projets.append(name)
        compte["projets"] = sorted(set(projets))
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    return jsonify({"ok": True, "utilisateurs": sorted(voulus)})


@flask_app.delete("/api/utilisateurs/<nom>")
@require_admin
def api_utilisateur_supprimer(nom):
    comptes = lire_utilisateurs()
    if nom not in comptes:
        return jsonify({"error": "Compte inconnu."}), 404
    comptes.pop(nom)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non supprimé : {e}"}), 500

    # Les cles d'acces partent avec le compte. Les laisser serait pire qu'un
    # oubli de menage : recreer un compte du meme nom lui rendrait les cles
    # de l'ancien, et l'appareil de la personne partie rouvrirait la porte.
    cles = lire_passkeys()
    if cles.pop(nom, None) is not None:
        try:
            ecrire_passkeys(cles)
        except OSError as e:
            return jsonify({"error": f"Compte supprimé, mais ses clés d'accès "
                                     f"n'ont pas pu être retirées : {e}"}), 500
    # La session de ce compte, si elle existe, tombera d'elle-meme : chaque
    # controle relit le registre, et un compte absent n'autorise plus rien.
    return jsonify({"ok": True})


def decrire_agent(agent):
    """« Safari sur iPhone » plutot qu'une chaine User-Agent de 200 signes."""
    a = agent or ""
    if "Edg/" in a:
        nav = "Edge"
    elif "OPR/" in a or "Opera" in a:
        nav = "Opera"
    elif "Firefox/" in a or "FxiOS" in a:
        nav = "Firefox"
    elif "Chrome/" in a or "CriOS" in a:
        nav = "Chrome"
    elif "Safari/" in a:
        nav = "Safari"
    else:
        nav = "Navigateur"
    if "iPhone" in a:
        os_ = "iPhone"
    elif "iPad" in a:
        os_ = "iPad"
    elif "Android" in a:
        os_ = "Android"
    elif "Mac OS X" in a or "Macintosh" in a:
        os_ = "Mac"
    elif "Windows" in a:
        os_ = "Windows"
    elif "Linux" in a:
        os_ = "Linux"
    else:
        os_ = ""
    return nav + (" sur " + os_ if os_ else "")


def liste_des_appareils(compte, sid_courant=None):
    return [{"id": id_public_appareil(l["sid"]), "appareil": decrire_agent(l["agent"]),
             "ip": l["ip"], "ouverte": l["ouverte"], "vue": l["vue"],
             "courant": l["sid"] == sid_courant}
            for l in appareils_du_compte(compte)]


# ------------------------ les notifications (Web Push) ------------------------
#
# Une notification sur le telephone, la tablette ou l'ordinateur, meme
# CodeLab ferme : un nouveau message (administrateur), une application qui
# tombe et qui revient (administrateur), l'avancement d'un message qu'on a
# envoye (compte utilisateur). Le mail continue de partir comme avant : la
# notification s'y ajoute, elle ne le remplace pas.
#
# LE STANDARD, ET RIEN D'AUTRE. Web Push (RFC 8030), chiffre de bout en bout
# (RFC 8291) et signe par le serveur (VAPID, RFC 8292) : le service d'Apple,
# de Google ou de Mozilla transporte un message qu'il ne peut pas lire.
# Aucun compte chez eux, aucune bibliotheque de plus : cryptography, deja la
# pour le coffre, suffit.
#
# Ou ca marche : Chrome, Edge et Firefox (ordinateur et Android), Safari sur
# Mac, et sur iPhone et iPad le panneau INSTALLE sur l'ecran d'accueil
# (iOS 16.4 et plus). Toujours en HTTPS : le navigateur refuse en clair.
#
# QUI RECOIT QUOI. Un abonnement appartient a un compte ET a une session
# (l'appareil connecte) : deconnecter l'appareil, depuis Securite, coupe ses
# notifications, et un compte ne recoit jamais celles d'un autre.
#
# PAS D'ADRESSE ARBITRAIRE. Le navigateur fournit l'adresse ou envoyer
# (endpoint) ; le serveur la contacte. N'importe quelle URL ferait du
# panneau un relais vers le reseau local (SSRF) : seuls les services de
# notification connus, en HTTPS, sont acceptes.
SERVICES_PUSH = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com",
                 "notify.windows.com", "updates.push.services.mozilla.com")
PUSH_ABONNEMENTS_MAX = 20          # par compte : au-dela, le plus ancien s'en va
PUSH_SUJET_VAPID = "https://github.com/studio-codelab/codelab"


def _de_b64url(texte):
    texte = str(texte or "")
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


def service_push_permis(endpoint):
    """Une adresse d'envoi d'un service de notification connu, en HTTPS."""
    try:
        u = urllib.parse.urlsplit(str(endpoint or ""))
    except ValueError:
        return False
    hote = (u.hostname or "").lower()
    return (u.scheme == "https" and len(str(endpoint)) <= 1024 and u.port in (None, 443)
            and any(hote == s or hote.endswith("." + s) for s in SERVICES_PUSH))


def cle_vapid():
    """La cle de signature du serveur, creee au premier besoin, gardee au coffre.

    None quand le coffre n'a pas de cle de chiffrement : sans elle, la cle
    privee ne pourrait pas etre rangee ailleurs qu'en clair, et elle ne l'est
    jamais.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = reglage("vapid_prive")
    if not pem:
        if not _cle_chiffrement:
            return None
        cle = ec.generate_private_key(ec.SECP256R1())
        pem = cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        try:
            ecrire_reglage("vapid_prive", pem)
        except OSError as e:
            print(f"[app-manager] cle de notification non enregistree : {e}", flush=True)
            return None
    return serialization.load_pem_private_key(pem.encode(), password=None)


def cle_publique_vapid():
    from cryptography.hazmat.primitives import serialization
    cle = cle_vapid()
    if cle is None:
        return ""
    return _b64url(cle.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint))


def _jeton_vapid(endpoint, cle):
    """Le JWT ES256 qui prouve au service que l'envoi vient de ce serveur."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    u = urllib.parse.urlsplit(endpoint)
    tete = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    corps = _b64url(json.dumps({"aud": f"{u.scheme}://{u.netloc}",
                                "exp": int(time.time()) + 12 * 3600,
                                "sub": PUSH_SUJET_VAPID}).encode())
    signe = (tete + "." + corps).encode()
    r, s = utils.decode_dss_signature(cle.sign(signe, ec.ECDSA(hashes.SHA256())))
    return tete + "." + corps + "." + _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def chiffrer_push(contenu, p256dh, auth, _ephemere=None, _sel=None):
    """Le corps chiffre d'une notification (RFC 8291, « aes128gcm »).

    _ephemere et _sel ne servent qu'aux tests (le vecteur de la RFC)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    cle_client = _de_b64url(p256dh)
    secret_auth = _de_b64url(auth)
    public_client = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), cle_client)
    ephemere = _ephemere or ec.generate_private_key(ec.SECP256R1())
    public_serveur = ephemere.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    partage = ephemere.exchange(ec.ECDH(), public_client)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=secret_auth,
               info=b"WebPush: info\x00" + cle_client + public_serveur).derive(partage)
    sel = _sel or secrets.token_bytes(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=sel,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=sel,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)
    chiffre = AESGCM(cek).encrypt(nonce, contenu + b"\x02", None)
    return sel + (4096).to_bytes(4, "big") + bytes([len(public_serveur)]) + public_serveur + chiffre


def abonnements_push(compte):
    return stockage.lire(DB_FILE, "SELECT * FROM abonnements_push WHERE compte = ? "
                                  "ORDER BY cree DESC", (compte,))


def retirer_abonnement_push(ident):
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM abonnements_push WHERE id = ?", (ident,))


def _envoyer_un_push(ab, contenu, cle, urgence):
    """Un envoi. Rend False quand l'abonnement est mort (a retirer)."""
    try:
        corps = chiffrer_push(contenu, ab["p256dh"], ab["auth"])
    except Exception:                                             # noqa: BLE001
        return False                 # cles de l'abonnement illisibles : on l'oublie
    req = urllib.request.Request(ab["endpoint"], data=corps, method="POST", headers={
        "TTL": "86400", "Urgency": urgence, "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": "vapid t=" + _jeton_vapid(ab["endpoint"], cle)
                         + ", k=" + cle_publique_vapid()})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except urllib.error.HTTPError as e:
        # 404 et 410 : l'abonnement n'existe plus (notifications coupees,
        # application desinstallee). Le reste est passager.
        if e.code in (404, 410):
            return False
        print(f"[app-manager] notification refusee ({e.code}) par "
              f"{urllib.parse.urlsplit(ab['endpoint']).hostname}", flush=True)
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"[app-manager] notification non envoyee : {e}", flush=True)
        return True


def notifier_compte(compte, titre, texte, url="/", etiquette=None, urgence="normal"):
    """Une notification sur chaque appareil abonne du compte. Ne leve jamais.

    Envoyee dans un thread : le service de notification peut mettre des
    secondes a repondre, et ni une requete ni le moniteur ne l'attendent.
    """
    try:
        abonnes = abonnements_push(compte)
        if not abonnes:
            return 0
        cle = cle_vapid()
        if cle is None:
            return 0
        sessions = {l["sid"] for l in appareils_du_compte(compte)}
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] notifications indisponibles : {e}", flush=True)
        return 0
    contenu = json.dumps({"titre": titre[:120], "texte": texte[:240], "url": url,
                          "etiquette": etiquette or ""}, ensure_ascii=False).encode()

    def _envoyer():
        for ab in abonnes:
            # Appareil deconnecte depuis : son abonnement part avec lui.
            if ab["sid"] not in sessions or not _envoyer_un_push(ab, contenu, cle, urgence):
                try:
                    retirer_abonnement_push(ab["id"])
                except Exception:                                 # noqa: BLE001
                    pass
    threading.Thread(target=_envoyer, daemon=True).start()
    return len(abonnes)


@flask_app.get("/api/notifications")
@require_auth
def api_notifications():
    """La cle publique du serveur, et si CET appareil est deja abonne."""
    qui = utilisateur_courant() or NOM_ADMIN
    sid = session.get("sid")
    try:
        cle = cle_publique_vapid()
    except Exception:                                             # noqa: BLE001
        cle = ""
    abonne = any(a["sid"] == sid for a in abonnements_push(qui))
    return jsonify({"disponible": bool(cle), "cle": cle, "abonne": abonne})


@flask_app.post("/api/notifications/abonnement")
@require_auth
def api_notifications_abonner():
    """{endpoint, keys: {p256dh, auth}}, tel que le rend pushManager.subscribe()."""
    qui = utilisateur_courant() or NOM_ADMIN
    d = request.get_json(force=True, silent=True) or {}
    endpoint = d.get("endpoint")
    cles = d.get("keys") if isinstance(d.get("keys"), dict) else {}
    p256dh, auth = str(cles.get("p256dh") or ""), str(cles.get("auth") or "")
    if not service_push_permis(endpoint):
        return jsonify({"error": "Ce service de notification n'est pas reconnu."}), 400
    try:
        if len(_de_b64url(p256dh)) != 65 or len(_de_b64url(auth)) != 16:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Abonnement illisible."}), 400
    if cle_vapid() is None:
        return jsonify({"error": "Notifications indisponibles sur ce serveur."}), 503
    ident = hashlib.sha256(endpoint.encode()).hexdigest()
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM abonnements_push WHERE id = ?", (ident,))
        cx.execute("INSERT INTO abonnements_push (id, compte, sid, endpoint, p256dh, auth, cree, agent) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (ident, qui, session.get("sid") or "", endpoint, p256dh, auth, int(time.time()),
                    (request.headers.get("User-Agent") or "")[:300]))
        anciens = [l["id"] for l in cx.execute(
            "SELECT id FROM abonnements_push WHERE compte = ? ORDER BY cree DESC LIMIT -1 OFFSET ?",
            (qui, PUSH_ABONNEMENTS_MAX)).fetchall()]
        for x in anciens:
            cx.execute("DELETE FROM abonnements_push WHERE id = ?", (x,))
    return jsonify({"ok": True})


@flask_app.delete("/api/notifications/abonnement")
@require_auth
def api_notifications_desabonner():
    """Coupe les notifications de CET appareil (session courante)."""
    qui = utilisateur_courant() or NOM_ADMIN
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM abonnements_push WHERE compte = ? AND sid = ?",
                   (qui, session.get("sid") or ""))
    return jsonify({"ok": True})


@flask_app.post("/api/notifications/essai")
@require_auth
def api_notifications_essai():
    qui = utilisateur_courant() or NOM_ADMIN
    if not notifier_compte(qui, "CodeLab", "Les notifications arrivent bien sur cet appareil.",
                           url="/parametres", etiquette="essai"):
        return jsonify({"error": "Aucun appareil abonné pour ce compte."}), 404
    return jsonify({"ok": True})


# L'avancement d'un message, pousse a son auteur : la meme etape que la
# pastille du hub (suivi_message), annoncee quand elle change. Le moniteur
# compare chaque minute, et seulement pour les comptes abonnes. Ce qui a
# deja ete annonce est retenu (preference « suivi_notifie ») : un
# redemarrage du panneau ne rejoue rien.
ETAPES_ANNONCEES = {"etude": "est à l'étude", "planifie": "est planifié",
                    "en_cours": "est en cours", "fait": "est fait",
                    "refuse": "n'est pas retenu"}
_suivi_push = {"dernier": 0}


def suivi_push_tick(maintenant=None):
    maintenant = time.time() if maintenant is None else maintenant
    if maintenant - _suivi_push["dernier"] < 60:
        return
    _suivi_push["dernier"] = maintenant
    comptes = {l["compte"] for l in stockage.lire(DB_FILE, "SELECT DISTINCT compte FROM abonnements_push")}
    comptes.discard(NOM_ADMIN)
    if not comptes:
        return
    taches, projets = lire_taches(), lire_projets()
    par_compte = {}
    for m in lire_messages(limite=MESSAGES_AFFICHES):
        if m.get("qui") in comptes:
            par_compte.setdefault(m["qui"], []).append(m)
    for qui in comptes:
        connues = lire_preference("suivi_notifie", qui)
        premiere = not isinstance(connues, dict)
        connues = connues if isinstance(connues, dict) else {}
        nouvelles = dict(connues)
        for m in par_compte.get(qui, []):
            etape = suivi_message(m, taches, projets)["etape"]
            avant = connues.get(m["id"])
            nouvelles[m["id"]] = etape
            if premiere or avant == etape or etape not in ETAPES_ANNONCEES:
                continue
            extrait = (m.get("texte") or "").strip().replace("\n", " ")
            notifier_compte(qui, "Ton message " + ETAPES_ANNONCEES[etape],
                            extrait[:120] or "Ouvre CodeLab pour voir son avancement.",
                            url="/messages", etiquette="suivi-" + m["id"])
        if nouvelles != connues or premiere:
            ecrire_preference("suivi_notifie", nouvelles, qui)


@flask_app.get("/api/mon-compte/appareils")
@require_auth
def api_mes_appareils():
    qui = utilisateur_courant() or NOM_ADMIN
    return jsonify({"appareils": liste_des_appareils(qui, session.get("sid"))})


@flask_app.delete("/api/mon-compte/appareils/<ident>")
@require_auth
def api_deconnecter_un_appareil(ident):
    qui = utilisateur_courant() or NOM_ADMIN
    cible = [l["sid"] for l in appareils_du_compte(qui) if id_public_appareil(l["sid"]) == ident]
    if not cible:
        return jsonify({"error": "Cet appareil n'est plus connecté."}), 404
    revoquer_appareils(cible)
    journaliser("session", qui=qui, action="appareil déconnecté", ip=_adresse_client())
    if cible[0] == session.get("sid"):
        session.clear()
    return jsonify({"ok": True})


@flask_app.post("/api/mon-compte/appareils/deconnecter-autres")
@require_auth
def api_deconnecter_les_autres():
    qui = utilisateur_courant() or NOM_ADMIN
    courant = session.get("sid")
    n = revoquer_appareils([l["sid"] for l in appareils_du_compte(qui) if l["sid"] != courant])
    journaliser("session", qui=qui, action=f"autres appareils déconnectés ({n})",
                ip=_adresse_client())
    return jsonify({"ok": True, "deconnectes": n})


@flask_app.get("/api/utilisateurs/<nom>/appareils")
@require_admin
def api_appareils_d_un_compte(nom):
    if nom not in lire_utilisateurs():
        return jsonify({"error": "Compte inconnu."}), 404
    return jsonify({"appareils": liste_des_appareils(nom)})


@flask_app.delete("/api/utilisateurs/<nom>/appareils")
@require_admin
def api_deconnecter_un_compte(nom):
    """L'administrateur deconnecte tous les appareils d'un compte, sans
    toucher a son mot de passe ni a ses cles."""
    if nom not in lire_utilisateurs():
        return jsonify({"error": "Compte inconnu."}), 404
    n = revoquer_appareils([l["sid"] for l in appareils_du_compte(nom)])
    journaliser("session", qui=utilisateur_courant() or NOM_ADMIN, cible=nom,
                action=f"appareils déconnectés par l'administrateur ({n})", ip=_adresse_client())
    return jsonify({"ok": True, "deconnectes": n})


@flask_app.post("/logout")
def logout():
    try:
        revoquer_appareils([session.get("sid")])
    except Exception:                                             # noqa: BLE001
        pass
    session.clear()
    return jsonify({"ok": True})


@flask_app.get("/api/auth-check")
def api_auth_check():
    """Repond 200 si la session est valide, 401 sinon. Rien d'autre.

    C'est le point d'appui du proxy de Dagster : nginx interroge cette route
    avant chaque requete (directive auth_request) et laisse passer ou renvoie
    vers la page de connexion du panneau. Dagster herite ainsi de la session
    du panneau -- meme mot de passe, meme second facteur, meme deconnexion --
    au lieu d'avoir sa propre authentification HTTP Basic, qui n'a ni session,
    ni expiration, ni deconnexion possible.
    """
    # Sans redirection, contrairement a require_auth : nginx a besoin d'un
    # code, pas d'une page. C'est lui qui decide ou envoyer le visiteur.
    #
    # Reserve a l'administrateur : l'interface de Dagster permet de lancer des
    # jobs, donc d'executer du code sur cette machine. Y donner acces a un
    # compte utilisateur reviendrait a lui donner l'administration par la
    # bande, quels que soient les projets qu'on lui a autorises.
    if not est_admin():
        return Response("", 401)
    return Response("", 204)


_erreurs_navigateur = {}


@flask_app.post("/api/erreur-navigateur")
@require_auth
def api_erreur_navigateur():
    """Une erreur JavaScript de la page du panneau, ecrite dans ses journaux.

    Tout compte connecte peut en envoyer -- c'est sa page qui plante --, mais
    trente par cinq minutes et par adresse au plus, et chaque champ tronque :
    cette route ecrit dans un journal, elle ne doit pas servir a le remplir.
    """
    ip = _client_ip()
    maintenant = time.time()
    recentes = [x for x in _erreurs_navigateur.get(ip, []) if maintenant - x < 300]
    if len(recentes) >= 30:
        _erreurs_navigateur[ip] = recentes
        return jsonify({"ok": False}), 429
    recentes.append(maintenant)
    _erreurs_navigateur[ip] = recentes
    d = request.get_json(silent=True) or {}

    def champ(nom, taille):
        # Une seule ligne par champ : un saut de ligne glisse dans le message
        # fabriquerait de fausses lignes dans le journal.
        return " ".join(str(d.get(nom) or "").split())[:taille]

    print(f"[navigateur] {utilisateur_courant() or NOM_ADMIN} {champ('page', 100)} : "
          f"{champ('message', 500)} ({champ('source', 200)}:{champ('ligne', 10)}:"
          f"{champ('colonne', 10)}) {champ('pile', 1500)}", flush=True)
    return jsonify({"ok": True})


@flask_app.get("/health")
def health():
    return Response("ok\n", mimetype="text/plain")


# ------------------------------ API --------------------------------

@flask_app.get("/api/apps")
@require_admin
def api_apps():
    apps = load()
    out = []
    par_app = reglage("alertes_par_app", {}) or {}
    for name in sorted(apps):
        a = apps[name]
        run = is_running(name)
        stats = proc_stats(procs[name].pid) if run and name in procs else {"cpu_percent": 0.0, "memory_mb": 0.0}
        if run:
            record_metrics(name, stats["cpu_percent"], stats["memory_mb"])
        out.append({
            "name": name, "titre": titre_de(name, a),
            "path": a["path"], "command": a["command"],
            "port": a["port"], "port_listeners": _ecouteurs_port(a["port"]),
            "running": run,
            "failed": bool(a.get("enabled")) and not run,
            # Plantee au lancement (voir start) : la fiche en montre la cause.
            "dernier_echec": None if run else a.get("dernier_echec"),
            "crash_looping": is_crash_looping(name),
            # None tant que la sonde n'est pas passee (app tout juste
            # demarree) : l'interface ne signale "ne repond pas" que sur un
            # False franc, jamais sur une absence de mesure.
            "listening": _listening.get(name) if run else None,
            "visibility": visibilite(a),
            "has_build": bool((a.get("build_command") or "").strip()),
            "build_command": a.get("build_command") or "",
            "max_memory_mb": a.get("max_memory_mb"),
            "description": a.get("description") or "",
            "categorie": categorie_de(name, a),
            # Les destinataires d'alerte se reglent dans la fiche de
            # l'application : la liste doit donc arriver avec elle. Aucun
            # secret ici -- une adresse de destination n'en est pas un, et
            # cette route est deja reservee a l'administrateur.
            "alertes": _adresses(par_app.get(name)),
            # Ce qui n'est pas configurable, la page doit le savoir : c'est
            # elle qui retire les onglets et les boutons correspondants.
            "par_defaut": est_app_par_defaut(name),
            "presentation": presentation_de(a),
            "presentation_masquee": bool(a.get("presentation_masquee")),
            "presentation_fichier": a.get("presentation_fichier", ""),
            **stats,
        })
    # Dagster figure ici comme les autres. Il n'est pas declare dans
    # apps.json -- le panneau ne le lance ni ne l'arrete -- mais c'est une
    # application du point de vue de qui s'en sert : elle doit se trouver la
    # ou on cherche les applications, et pas seulement sur le hub.
    # Messages n'y figure pas : ce n'est pas un service qui tourne ou tombe,
    # c'est une page du panneau, sans journal ni metriques a surveiller.
    out.extend(services_du_hub(avec_messagerie=False))
    out.sort(key=lambda a: a["name"])
    return jsonify({"apps": out})


@flask_app.get("/api/browse")
@require_admin
def api_browse():
    path = os.path.abspath(request.args.get("path", ROOT))
    if not under_root(path):
        path = ROOT
    try:
        entries = os.listdir(path)
    except (PermissionError, FileNotFoundError):
        entries = []
    return jsonify({
        "path": path,
        "label": path.replace(ROOT, "") or "/",
        "parent": os.path.dirname(path) if path != ROOT else None,
        "dirs": sorted(d for d in entries
                       if os.path.isdir(os.path.join(path, d)) and not d.startswith(".")),
        "hasIndex": os.path.exists(os.path.join(path, "index.html")),
    })


@flask_app.get("/api/detect")
@require_admin
def api_detect():
    path = os.path.abspath(request.args.get("path", ""))
    if not under_root(path) or not os.path.isdir(path):
        return jsonify({"command": "", "build_command": ""})
    command, build_command = detect_project(path)
    return jsonify({"command": command, "build_command": build_command})


@flask_app.post("/api/add")
@require_admin
def api_add():
    d = request.get_json(force=True)
    titre = titre_propre(d.get("name"))
    name = valid_name(d.get("name"))
    path = (d.get("path") or "").strip()
    command = (d.get("command") or "").strip()
    build_command = (d.get("build_command") or "").strip()
    max_memory_mb = d.get("max_memory_mb") or None
    vis = d.get("visibility") if d.get("visibility") in VISIBILITES else VISIBILITE_PUBLIQUE
    # Sans adresse publique declaree, une application neuve nait privee --
    # y compris si le formulaire demande autre chose. C'est le defaut sur
    # lequel on ne peut pas se tromper : elle s'ouvre en une bascule, alors
    # qu'une application ouverte par megarde ne se referme qu'apres coup.
    if not adresse_publique():
        vis = VISIBILITE_PRIVEE
    if est_categorie_codelab(d.get("categorie")):
        return jsonify({"error": f"« {CATEGORIE_CODELAB} » est réservée aux applications de CodeLab."}), 400
    apps = load()

    if not titre:
        return jsonify({"error": "Le nom est obligatoire."}), 400
    # Le refus porte sur le CHEMIN, et il le dit : un nom refuse sans qu'on
    # montre ce qu'il donnait dans l'adresse ne s'apprend pas.
    if not name:
        return jsonify({"error": f"« {titre} » ne donne aucune adresse utilisable. "
                                 f"Il faut au moins une lettre sans accent "
                                 f"ou un chiffre."}), 400
    if name in adresses_reservees():
        return jsonify({"error": f"L'adresse « /{name}/ » est réservée."}), 400
    if name in apps:
        return jsonify({"error": f"L'adresse « /{name}/ » est déjà prise par "
                                 f"une autre application."}), 400
    if not os.path.isdir(path):
        return jsonify({"error": "Dossier introuvable : " + path}), 400
    if not under_root(path):
        return jsonify({"error": "Le dossier doit se trouver dans " + ROOT + "."}), 400
    if not command:
        return jsonify({"error": "La commande de lancement est obligatoire."}), 400
    port = next_port(apps)
    if not port:
        return jsonify({"error": "Plus de port interne disponible."}), 400

    apps[name] = {
        "titre": titre,
        "path": path, "command": command, "port": port, "enabled": False,
        "build_command": build_command, "max_memory_mb": max_memory_mb,
        "visibility": vis,
        "description": description_propre(d.get("description")),
        "categorie": categorie_valide(d.get("categorie")),
    }
    save(apps)
    return jsonify({"ok": True, "name": name, "titre": titre, "port": port})


# CE QUI NE SE VOIT QU'AU PROCHAIN DEMARRAGE.
#
# Ces trois-la sont lus au lancement du processus : le dossier de travail, la
# ligne de commande, la limite de memoire posee dans le preexec_fn. Les
# changer pendant qu'une application tourne ne touche pas le processus en
# vie -- il faudra le relancer.
#
# Tout le reste s'applique sur-le-champ, parce que le panneau le relit a
# chaque requete : la description et la categorie (affichees dans le hub), la
# visibilite (le proxy la consulte pour chaque visiteur), la commande de
# build (executee a la demande, jamais au lancement).
#
# La liste vit ICI plutot que dans la page : c'est le serveur qui sait ce
# qu'il relit et quand. Une copie dans le navigateur aurait diverge a la
# premiere evolution.
CHAMPS_AU_DEMARRAGE = ("path", "command", "max_memory_mb")


@flask_app.put("/api/app/<n>")
@require_admin
def api_edit(n):
    """Enregistre la configuration, application en marche ou non.

    CE QUI A CHANGE, ET POURQUOI. Cette route refusait tout net pendant
    qu'une application tournait : « Arrete l'application avant de la
    modifier. » Corriger une faute dans une description demandait donc de
    couper le service. Le refus protegeait d'une illusion reelle -- croire
    qu'une commande modifiee s'appliquait au processus deja lance -- mais il
    la traitait en interdisant tout, alors qu'il suffit de DIRE lequel des
    champs attend un redemarrage.
    """
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True)
    if est_categorie_codelab(d.get("categorie")):
        return jsonify({"error": f"« {CATEGORIE_CODELAB} » est réservée aux applications de CodeLab."}), 400
    path = (d.get("path") or "").strip()
    command = (d.get("command") or "").strip()
    if not os.path.isdir(path):
        return jsonify({"error": "Dossier introuvable : " + path}), 400
    if not under_root(path):
        return jsonify({"error": "Le dossier doit se trouver dans " + ROOT + "."}), 400
    if not command:
        return jsonify({"error": "La commande de lancement est obligatoire."}), 400
    avant = {c: apps[n].get(c) for c in CHAMPS_AU_DEMARRAGE}
    apps[n]["path"] = path
    apps[n]["command"] = command
    apps[n]["build_command"] = (d.get("build_command") or "").strip()
    apps[n]["max_memory_mb"] = d.get("max_memory_mb") or None
    apps[n]["description"] = description_propre(d.get("description"))
    apps[n]["categorie"] = categorie_valide(d.get("categorie"))
    if d.get("visibility") in VISIBILITES:
        apps[n]["visibility"] = d["visibility"]
    save(apps)

    # Ce qui a reellement change parmi les champs lus au demarrage. On
    # compare APRES nettoyage (chemin normalise, memoire en entier ou None) :
    # comparer les valeurs brutes du formulaire annoncerait un redemarrage
    # necessaire pour un espace en fin de ligne.
    attendent = [c for c in CHAMPS_AU_DEMARRAGE if avant.get(c) != apps[n].get(c)]
    en_marche = is_running(n)
    return jsonify({
        "ok": True,
        "running": en_marche,
        # Un redemarrage n'est "requis" que si quelque chose tourne : sur une
        # application arretee, le prochain demarrage prendra la nouvelle
        # configuration tout seul.
        "redemarrage_requis": bool(en_marche and attendent),
        "champs_en_attente": attendent,
    })


# UNE ACTION A LA FOIS PAR APPLICATION.
#
# Un double clic sur « Demarrer » envoyait deux requetes : la premiere
# demarrait, la seconde -- arrivee quand l'application tournait deja --
# l'arretait. Et deux deploiements lances ensemble construisaient dans le
# meme dossier en meme temps. Le bouton desactive dans la page ne suffit
# pas : deux onglets, ou deux administrateurs, ou un appel a la main,
# passent a cote. Le serveur tient donc la regle : pendant qu'une action
# tourne sur une application, les autres sont refusees en 409, avec un
# message qui dit pourquoi.
_actions_en_cours = {}
_actions_verrou = threading.Lock()


def une_action_a_la_fois(route):
    @functools.wraps(route)
    def enveloppe(n, *args, **kwargs):
        with _actions_verrou:
            verrou = _actions_en_cours.setdefault(n, threading.Lock())
        if not verrou.acquire(blocking=False):
            return jsonify({"error": f"Une action est déjà en cours sur « {n} ». "
                                     f"Attends qu'elle se termine, puis réessaie.",
                            "en_cours": True}), 409
        try:
            return route(n, *args, **kwargs)
        finally:
            verrou.release()
    return enveloppe


@flask_app.post("/api/toggle/<n>")
@require_admin
@une_action_a_la_fois
def api_toggle(n):
    refus = refus_action_protegee(n, "arrêt/redémarrage")
    if refus:
        return jsonify({"error": refus}), 403
    """Demarrer / arreter, et DIRE CE QU'ON A ESSAYE DE FAIRE.

    Signale par Lucas : « je ne peux pas arreter les applications », avec le
    message « L'application n'a pas demarre. (reponse 500) ». Deux defauts se
    superposaient, et le second cachait le premier :

      - l'action tentee n'etait renvoyee nulle part. La page ne pouvait donc
        que deviner, et son message de repli est ecrit pour le demarrage :
        tout arret rate s'annoncait « n'a pas demarre », ce qui envoie
        chercher exactement a l'oppose de la panne ;
      - une exception dans stop() ou start() sortait en page d'erreur HTML,
        sans JSON. Le motif reel restait dans les journaux du panneau, que
        l'interface ne montre pas.

    L'action se decide donc AVANT d'agir, elle accompagne toutes les
    reponses, et plus aucune exception ne sort d'ici sans explication.
    """
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    action = "arret" if is_running(n) else "demarrage"
    # L'action VOULUE, quand la page la donne. Un second clic parti avant
    # que la page ait vu le premier aboutir demande encore « demarrage » ;
    # l'application tourne deja, il n'y a donc rien a faire -- surtout pas
    # l'arreter.
    voulue = (request.get_json(silent=True) or {}).get("action")
    if voulue in ("arret", "demarrage") and voulue != action:
        return jsonify({"ok": True, "action": voulue, "deja": True,
                        "running": voulue == "demarrage"})
    try:
        erreur = stop(n) if action == "arret" else start(n)
        if erreur:
            # 409 et non 500 : le panneau a fait son travail, c'est
            # l'application qui refuse de s'arreter ou de demarrer. La
            # nuance compte pour qui lit les journaux du panneau.
            return jsonify({"error": erreur, "action": action}), 409
        return jsonify({"ok": True, "action": action,
                        "running": action == "demarrage"})
    except Exception as e:                                        # noqa: BLE001
        traceback.print_exc()
        verbe = "arrêter" if action == "arret" else "démarrer"
        return jsonify({
            "action": action,
            "error": f"Impossible de {verbe} l'application "
                     f"({type(e).__name__}: {e}). Le détail complet est dans "
                     f"les journaux du panneau : docker logs --tail 50 "
                     f"codelab-app-manager",
        }), 500


def restart_app(n):
    """Rend None si l'application est repartie, sinon POURQUOI.

    Elle se taisait : l'erreur de start() partait a la poubelle, la route
    repondait « ok », et le bouton « Redemarrer » laissait une application
    arretee sans un mot. C'est le meme defaut que start() avait avant d'etre
    corrige -- il restait ici, une ligne plus loin.
    """
    stop(n)
    for _ in range(30):
        if not is_running(n):
            break
        time.sleep(0.1)
    return start(n)


@flask_app.post("/api/visibility/<n>")
@require_admin
def api_visibility(n):
    # Une application par defaut ne se rend pas publique : le diagnostic
    # offrirait a un visiteur anonyme l'etat des lieux complet de
    # l'installation -- les conteneurs, les comptes sans second facteur, ce
    # qui est expose.
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True, silent=True) or {}
    vis = d.get("visibility")
    if vis not in VISIBILITES:
        # Sans valeur explicite, on bascule d'un etat a l'autre.
        vis = (VISIBILITE_PRIVEE if visibilite(apps[n]) == VISIBILITE_PUBLIQUE
               else VISIBILITE_PUBLIQUE)
    # Rendre publique une application sur un serveur que personne ne peut
    # joindre ne partage rien : cela retire seulement l'authentification.
    # Une application DEJA publique reste modifiable dans l'autre sens --
    # on ne bloque jamais le chemin qui referme.
    if vis == VISIBILITE_PUBLIQUE and not adresse_publique():
        return jsonify({"error": "Ce serveur n'a encore jamais été ouvert depuis "
                                 "internet : rendre une application publique ne "
                                 "ferait que retirer l'authentification. Ouvre le "
                                 "panneau une fois par ton nom de domaine."}), 400
    apps[n]["visibility"] = vis
    save(apps)
    return jsonify({"ok": True, "visibility": vis})


@flask_app.post("/api/restart/<n>")
@require_admin
@une_action_a_la_fois
def api_restart(n):
    refus = refus_action_protegee(n, "redémarrage")
    if refus:
        return jsonify({"error": refus}), 403
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    erreur = restart_app(n)
    if erreur:
        return jsonify({"error": erreur}), 409
    return jsonify({"ok": True})


@flask_app.post("/api/deploy/<n>")
@require_admin
@une_action_a_la_fois
def api_deploy(n):
    refus = refus_action_protegee(n, "déploiement")
    if refus:
        return jsonify({"error": refus}), 403
    """Build puis mise en ligne, en une action.

    L'enchainement etait a faire a la main en deux entrees de menu, et rien
    ne rappelait la seconde : on lancait le build, la page ne changeait pas,
    et on cherchait pourquoi -- le process servait toujours l'ancien dist/.

    L'ordre compte. Le build tourne pendant que l'ancienne version est encore
    servie, et un build en echec n'arrete rien du tout : on ne remplace une
    version qui marche que par une version qui compile.
    """
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, msg = run_build(n)
    if not ok:
        return jsonify({"error": msg}), 400
    # "Deployer" sur une application arretee la met en ligne : c'est le sens
    # attendu du mot, et l'alternative (builder puis ne rien faire) est
    # exactement le geste oublie que cette route supprime.
    marquer_nouvelle_version(n)
    erreur = restart_app(n) if is_running(n) else start(n)
    if erreur:
        # Le build a reussi, la mise en ligne non : la nuance compte, et elle
        # se perdait entierement -- la route repondait « ok ».
        return jsonify({"error": "Le build a reussi, mais la mise en ligne a "
                                 "echoue : " + erreur}), 409
    return jsonify({"ok": True})


@flask_app.post("/api/build/<n>")
@require_admin
@une_action_a_la_fois
def api_build(n):
    refus = refus_action_protegee(n, "build")
    if refus:
        return jsonify({"error": refus}), 403
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, msg = run_build(n)
    if not ok:
        return jsonify({"error": msg}), 400
    marquer_nouvelle_version(n)
    return jsonify({"ok": True})


@flask_app.get("/api/metrics/<n>")
@require_admin
def api_metrics(n):
    if n not in load() and not est_app_par_defaut(n):
        return jsonify({"error": "Application inconnue."}), 404

    # La fiche peut etre ouverte avant le premier rafraichissement de
    # /api/apps. Dans ce cas l'historique etait vide et les deux cartes
    # affichaient « Aucune mesure » jusqu'au prochain polling. Une ouverture
    # de l'onglet met maintenant au moins une mesure reelle a disposition.
    if n in procs and is_running(n):
        stats = proc_stats(procs[n].pid)
        record_metrics(n, stats["cpu_percent"], stats["memory_mb"])

    hist = get_metrics_history(n)
    return jsonify({
        "points": [{"t": t, "cpu": cpu, "mem": mem} for t, cpu, mem in hist],
    })


@flask_app.delete("/api/app/<n>")
@require_admin
def api_delete(n):
    # Le projet de diagnostic ne se supprime pas.
    #
    # C'est l'etat des lieux de l'installation : il dit si les services se
    # parlent, si la base repond, si le panneau est correctement expose. Une
    # stack sans lui n'a plus aucun moyen de se controler elle-meme -- et
    # comme l'inscription n'a lieu qu'UNE fois (marqueur), le supprimer le
    # ferait disparaitre pour de bon, pas jusqu'au prochain redemarrage.
    #
    # Refuse ici et pas seulement dans la page : masquer un bouton ne protege
    # rien, la route reste appelable a la main.
    if est_app_par_defaut(n):
        return jsonify({"error": f"« {n} » est une application par défaut de "
                                 f"CodeLab : elle est gérée par CodeLab et ne "
                                 f"se modifie ni ne se supprime à la main."}), 403
    stop(n)
    apps = load()
    apps.pop(n, None)
    save(apps)
    par_app = reglage("alertes_par_app", {}) or {}
    if par_app.pop(n, None) is not None:
        try:
            ecrire_reglage("alertes_par_app", par_app or None)
        except (OSError, RuntimeError):
            pass
    return jsonify({"ok": True})


# ---------------------- renommer une application ----------------------
#
# LE NOM N'EST PAS UNE ETIQUETTE, C'EST LA CLE. Il designe l'application dans
# apps.json, dans l'adresse qui l'ouvre, dans le journal qu'elle ecrit, dans
# la liste des projets autorises de chaque compte, dans les applications que
# chacun a masquees de son hub -- et il decide meme de l'uid sous lequel elle
# tourne. Changer la cle sans changer ce qui la designe, c'est perdre
# l'application de vue tout en la laissant tourner.
#
# D'ou une seule fonction qui fait le tour, plutot qu'un renommage dans la
# page et des oublis qui se decouvrent trois semaines plus tard.

def _renommer_references(ancien, nouveau):
    """Tout ce qui designe une application par son nom, hors apps.json."""
    # Les projets autorises de chaque compte.
    comptes = lire_utilisateurs()
    touche = False
    for compte in comptes.values():
        projets = compte.get("projets")
        if isinstance(projets, list) and ancien in projets:
            compte["projets"] = [nouveau if p == ancien else p for p in projets]
            touche = True
    if touche:
        ecrire_utilisateurs(comptes)

    # Les destinataires d'alerte de l'application, au coffre.
    par_app = reglage("alertes_par_app", {}) or {}
    if ancien in par_app:
        par_app[nouveau] = par_app.pop(ancien)
        ecrire_reglage("alertes_par_app", par_app)

    # Les applications que chacun a retirees de son hub.
    masquees = lire_masquees()
    touche = False
    for qui, liste in masquees.items():
        if isinstance(liste, list) and ancien in liste:
            masquees[qui] = [nouveau if v == ancien else v for v in liste]
            touche = True
    if touche:
        ecrire_masquees(masquees)

    # Le journal suit l'application : le perdre a chaque renommage effacerait
    # justement ce qu'on relit pour comprendre ce qui vient de se passer.
    for suffixe in (".log", ".log.1"):
        avant = os.path.join(LOG_DIR, ancien + suffixe)
        if os.path.exists(avant):
            try:
                os.replace(avant, os.path.join(LOG_DIR, nouveau + suffixe))
            except OSError as e:
                print(f"[app-manager] journal de {ancien} non renomme ({e}).", flush=True)


def _reprendre_les_fichiers(dossier, ancien_uid, nouvel_uid):
    """Redonne au nouvel uid les fichiers qui appartenaient a l'ancien.

    L'uid d'une application est DERIVE de son nom (voir uid_application) :
    la renommer lui en donne un autre, et tout ce qu'elle avait ecrit
    devient illisible pour elle. Un projet qui ne peut plus relire sa propre
    base SQLite apres un simple changement de nom serait un piege.

    On ne touche QUE ce qui appartenait a l'ancien uid -- le code source
    appartient a l'utilisateur SSH, et il doit le rester -- et jamais a
    travers un lien symbolique : ce code tourne en root.
    """
    if os.geteuid() != 0 or ancien_uid == nouvel_uid or not os.path.isdir(dossier):
        return 0
    repris = 0
    for racine, dossiers, fichiers in os.walk(dossier, followlinks=False):
        for nom in dossiers + fichiers:
            cible = os.path.join(racine, nom)
            try:
                st = os.lstat(cible)
                if st.st_uid == ancien_uid:
                    os.lchown(cible, nouvel_uid, st.st_gid)
                    repris += 1
            except OSError:
                pass
    return repris


@flask_app.post("/api/app/<n>/renommer")
@require_admin
def api_renommer(n):
    """Change le nom d'une application, et tout ce qui la designe par ce nom."""
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    # Meme raison que pour la suppression : l'etat des lieux de
    # l'installation est inscrit sous ce nom-la, et le marqueur qui evite une
    # seconde inscription porte dessus.
    if n == DIAGNOSTIC_NOM:
        return jsonify({"error": "Le projet de diagnostic ne se renomme pas : "
                                 "c'est lui qui dit si cette installation va bien, "
                                 "et le panneau le retrouve par son nom."}), 403
    brut = (request.get_json(force=True, silent=True) or {}).get("nom")
    titre = titre_propre(brut)
    nouveau = valid_name(brut)
    if not titre:
        return jsonify({"error": "Le nouveau nom est obligatoire."}), 400
    if not nouveau:
        return jsonify({"error": f"« {titre} » ne donne aucune adresse utilisable. "
                                 f"Il faut au moins une lettre sans accent "
                                 f"ou un chiffre."}), 400
    # LE NOM SEUL A PU CHANGER. « site » renomme en « Site » garde le chemin
    # /site/ : rien a deplacer, rien a arreter -- on enregistre le titre et
    # on s'arrete la. Sans ce cas, le renommage echouait sur « une adresse
    # deja prise » : la sienne.
    if nouveau == n:
        if titre != (apps[n].get("titre") or n):
            apps[n]["titre"] = titre
            save(apps)
        return jsonify({"ok": True, "nom": n, "titre": titre, "redemarree": False})
    if nouveau in adresses_reservees():
        return jsonify({"error": f"L'adresse « /{nouveau}/ » est réservée."}), 400
    if nouveau in apps:
        return jsonify({"error": f"L'adresse « /{nouveau}/ » est déjà prise par "
                                 f"une autre application."}), 400

    # L'application doit s'arreter le temps du renommage : son processus
    # porte l'ancien nom dans son environnement (CODELAB_APP), c'est ce qui
    # permet au panneau de le reconnaitre apres un redemarrage. Le laisser
    # tourner reviendrait a le perdre.
    tournait = is_running(n)
    if tournait:
        souci = stop(n)
        if souci:
            return jsonify({"error": "Arrêt impossible avant le renommage : " + souci}), 409

    ancien_uid, nouvel_uid = uid_application(n), uid_application(nouveau)
    dossier = apps[n].get("path") or ""

    # La cle change de nom SANS changer de place : l'ordre d'apps.json est
    # celui des listes de la page, et une application qui saute en fin de
    # liste parce qu'on l'a renommee se cherche.
    apps = {(nouveau if cle == n else cle): valeur for cle, valeur in apps.items()}
    apps[nouveau]["titre"] = titre
    save(apps)
    _renommer_references(n, nouveau)
    repris = _reprendre_les_fichiers(dossier, ancien_uid, nouvel_uid)

    journaliser("renommage", qui=utilisateur_courant() or NOM_ADMIN, app=nouveau,
                action=f"{n} -> {nouveau}", ip=_adresse_client())

    erreur = None
    if tournait:
        erreur = start(nouveau)
    return jsonify({"ok": True, "nom": nouveau, "titre": titre,
                    "redemarree": tournait and not erreur,
                    "fichiers_repris": repris,
                    "erreur_demarrage": erreur or ""})


@flask_app.get("/api/logs/<n>")
@require_admin
def api_logs(n):
    f = os.path.join(LOG_DIR, n + ".log")
    if not os.path.exists(f):
        return jsonify({"lines": ["Aucun journal pour le moment."]})
    with open(f, errors="replace") as fh:
        lines = [l.rstrip() for l in fh.readlines()[-120:]]
    return jsonify({"lines": lines or ["Journal vide."]})


@flask_app.get("/api/logs/<n>/stream")
@require_admin
def api_logs_stream(n):
    f = os.path.join(LOG_DIR, n + ".log")

    if not _prendre_place_flux():
        return jsonify({"error": f"Trop de journaux suivis en même temps "
                                 f"({SSE_MAX_FLUX} au maximum). Ferme une "
                                 f"fenêtre de journal et réessaie."}), 503

    def gen():
        pos = max(0, os.path.getsize(f) - 4000) if os.path.exists(f) else 0
        debut = derniere_emission = time.time()
        yield "retry: 2000\n\n"
        while True:
            envoye = False
            if os.path.exists(f):
                with open(f, errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n"
                    envoye = True
            maintenant = time.time()
            if envoye:
                derniere_emission = maintenant
            elif maintenant - derniere_emission > SSE_BATTEMENT:
                # Commentaire SSE : ignore par le navigateur, mais il traverse
                # la connexion. Sans lui, un journal silencieux fait passer le
                # flux pour mort aux yeux du serveur (et de tout proxy pose
                # devant), qui finit par le fermer.
                yield ": battement\n\n"
                derniere_emission = maintenant
            if maintenant - debut > SSE_DUREE_MAX:
                # Un flux ouvert occupe un thread du serveur pour toujours :
                # quelques onglets oublies suffiraient a saturer le panneau.
                # On rend la main, et EventSource se reconnecte tout seul
                # (c'est a quoi sert le "retry" envoye en tete).
                return
            time.sleep(0.5)

    reponse = Response(stream_with_context(gen()), mimetype="text/event-stream")
    # call_on_close plutot qu'un "finally" dans le generateur : celui-ci ne
    # s'execute que si le generateur a demarre. Un client qui se deconnecte
    # avant laisserait sinon une place reservee pour toujours, et le plafond
    # se refermerait tout seul sur le panneau.
    reponse.call_on_close(_rendre_place_flux)
    return reponse


@flask_app.put("/api/app/<n>/logo")
@require_admin
def api_logo(n):
    """Depose le logo d'une application, envoye depuis sa page de reglages.

    Le corps est l'image elle-meme, pas un formulaire : il n'y a qu'un seul
    fichier, et un multipart n'apporterait qu'un analyseur de plus a nourrir.
    """
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    a = apps.get(n)
    if not a:
        return jsonify({"error": "Application inconnue."}), 404
    donnees = request.get_data(cache=False)
    if not donnees:
        return jsonify({"error": "Aucune image reçue."}), 400
    if len(donnees) > LOGO_MAX_OCTETS:
        return jsonify({"error": "Image trop lourde : %d Ko pour %d Ko au maximum."
                                 % (len(donnees) // 1024, LOGO_MAX_OCTETS // 1024)}), 400
    format_ = _format_du_logo(donnees)
    if not format_:
        # Le SVG tombe ici, et c'est voulu : servi dans l'origine du panneau,
        # un SVG peut y executer du script.
        return jsonify({"error": "Format non accepté. PNG, JPEG, GIF ou WebP — "
                                 "le SVG est refusé : il peut porter du script."}), 400
    ext, _mime = format_
    dossier = a["path"]
    if not os.path.isdir(dossier) or not under_root(dossier):
        return jsonify({"error": "Dossier du projet introuvable."}), 400

    # Un seul logo a la fois : les autres noms que NOUS ecrivons partent, sinon
    # icon.png survivrait a l'envoi d'un icon.jpg et continuerait de s'afficher
    # (find_icon prend le premier de la liste).
    for nom in LOGO_NOMS_ECRITS:
        chemin = os.path.join(dossier, nom)
        if os.path.isfile(chemin) and not os.path.islink(chemin):
            try:
                os.remove(chemin)
            except OSError:
                pass
    cible = os.path.join(dossier, "icon." + ext)
    tmp = cible + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(donnees)
        os.replace(tmp, cible)
        # Le fichier appartient au projet : il doit rester modifiable depuis
        # une session SSH comme le reste de son dossier.
        try:
            os.chmod(cible, 0o664)
        except OSError:
            pass
    except OSError as e:
        return jsonify({"error": f"Écriture impossible : {e}"}), 500
    journaliser("logo", qui=utilisateur_courant() or NOM_ADMIN, app=n,
                action="depose", ip=_adresse_client())
    return jsonify({"ok": True, "fichier": os.path.basename(cible)})


@flask_app.delete("/api/app/<n>/logo")
@require_admin
def api_logo_retirer(n):
    """Retire le logo depose. Ne touche qu'aux noms que le panneau ecrit :
    un favicon.ico pose a la main dans le projet lui appartient."""
    refus = refus_configuration(n)
    if refus:
        return jsonify({"error": refus}), 403
    a = load().get(n)
    if not a:
        return jsonify({"error": "Application inconnue."}), 404
    retires = []
    for nom in LOGO_NOMS_ECRITS:
        chemin = os.path.join(a["path"], nom)
        if os.path.isfile(chemin) and not os.path.islink(chemin):
            try:
                os.remove(chemin)
                retires.append(nom)
            except OSError as e:
                return jsonify({"error": f"Suppression impossible : {e}"}), 500
    if retires:
        journaliser("logo", qui=utilisateur_courant() or NOM_ADMIN, app=n,
                    action="retire", ip=_adresse_client())
    return jsonify({"ok": True, "retires": retires})


# LES ICONES DES APPLICATIONS PAR DEFAUT.
#
# Sans elles, ces deux-la tombaient sur l'initiale coloree servie a toute
# application sans logo : un « D » pour dagster, un « D » pour diagnostic, et
# un troisieme « D » pour demo. Trois carrees identiques dans la grille du
# hub, a distinguer en lisant le nom dessous -- c'est-a-dire en cessant de
# les reconnaitre d'un coup d'oeil.
#
# Dessinees ici plutot que posees en fichier : ces deux applications
# appartiennent a CodeLab, leur icone doit arriver avec l'image et ne rien
# demander a personne. Le format est le meme que celui des logos deposes, un
# SVG carre de 64, pour que la grille ne fasse aucune difference entre elles
# et les autres.

# Dagster : le graphe oriente acyclique, ce que l'outil manipule. Dessin
# original, dans le violet de la marque -- ce n'est pas le logo officiel, et
# il ne pretend pas l'etre.
ICONE_DAGSTER = (
    # Le logo OFFICIEL, tel que dagster-webserver le sert lui-meme
    # (webapp/build/favicon.svg) : memes traces, memes couleurs -- l'indigo
    # #4F43DD, son ombre #352D8E, l'oeil blanc. Recopie, pas redessine.
    #
    # Une seule retouche : la variante « ecran sombre » du fichier d'origine
    # (@media prefers-color-scheme) est retiree. Elle eclaircit le logo en
    # lavande, pensee pour un fond sombre ; or ici le logo est toujours pose
    # sur la plaque blanche (--plaque-logo), ou ce lavande disparaitrait.
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 67 67" role="img" aria-'
    'label="Dagster" fill="none"><style> .daggy-body { fill: #4F43DD; } .daggy-sh'
    'adow { fill: #352D8E; }  </style><path class="daggy-body" d="M22.0142 63.398'
    '9C22.0153 63.7681 22.0925 64.1331 22.2409 64.4711C22.3894 64.8091 22.606 65.'
    '1129 22.8771 65.3635C23.1482 65.6141 23.4681 65.806 23.8167 65.9275C24.1654 '
    '66.0489 24.5353 66.0971 24.9034 66.0692C38.8959 65.0678 52.1113 55.1693 56.3'
    '304 39.7088C56.5521 38.8196 57.2196 38.3737 58.1088 38.3737C58.5646 38.392 5'
    '8.9949 38.5892 59.3063 38.9226C59.6177 39.256 59.7852 39.6987 59.7725 40.154'
    '7C59.7725 43.6019 55.3395 52.6113 49.0004 57.8395C48.6773 58.1113 48.4197 58'
    '.4524 48.2468 58.8375C48.0739 59.2226 47.9901 59.6418 48.0017 60.0638C48.008'
    '1 60.4069 48.0822 60.7454 48.2198 61.0599C48.3573 61.3743 48.5555 61.6585 48'
    '.8031 61.8961C49.0507 62.1338 49.3428 62.3202 49.6626 62.4448C49.9823 62.569'
    '3 50.3236 62.6295 50.6667 62.6219C51.2221 62.6219 52.1113 62.2881 53.2221 61'
    '.2868C57.6551 57.2815 66.1038 46.0479 66.1038 33.7008C66.1038 15.5988 52.103'
    '5 0 32.5646 0C15.1301 0 0.0267944 14.2376 0.0267944 30.6995C0.0267944 41.599'
    '3 8.68929 49.8289 20.1263 49.8289C28.9009 49.8289 37.008 43.6019 39.2297 35.'
    '0359C39.4513 34.1467 40.1163 33.7008 41.0055 33.7008C41.4615 33.7192 41.892 '
    '33.9163 42.2038 34.2496C42.5157 34.5829 42.6838 35.0256 42.6717 35.4818C42.6'
    '717 39.375 35.3417 52.9528 20.4601 52.9528C16.9059 52.9528 12.4651 51.9515 9'
    '.35423 50.1705C8.93778 49.9687 8.48415 49.8551 8.02174 49.8368C7.66768 49.82'
    '32 7.31463 49.8829 6.9848 50.0124C6.65497 50.1418 6.35549 50.3381 6.10519 50'
    '.5889C5.85489 50.8397 5.65919 51.1396 5.53038 51.4697C5.40158 51.7997 5.3424'
    '7 52.1529 5.35676 52.5069C5.37113 52.9605 5.50107 53.4028 5.73426 53.7921C5.'
    '96744 54.1813 6.29615 54.5046 6.68925 54.7312C10.7989 57.0781 15.6855 58.290'
    '6 20.6817 58.2906C33.12 58.2906 44.4475 49.8367 47.7801 37.3802C48.0017 36.4'
    '91 48.6693 36.0451 49.5558 36.0451C50.0119 36.0634 50.4424 36.2606 50.7542 3'
    '6.5939C51.066 36.9272 51.2341 37.3698 51.2221 37.8261C51.2221 42.9422 42.116'
    '3 59.4041 24.5697 60.7392C23.8908 60.7896 23.2544 61.089 22.7828 61.5799C22.'
    '3111 62.0708 22.0374 62.7186 22.0142 63.3989Z" fill="#DEDEFC"/><path d="M39.'
    '4016 20.8013C41.7966 20.7834 44.1476 21.4453 46.1814 22.71C46.3869 21.5851 4'
    '6.5003 20.4453 46.5204 19.3019C46.5204 14.0215 42.4943 9.29126 37.592 9.2912'
    '6C33.7796 9.29126 31.3963 12.4491 31.3963 16.3501C31.3781 18.4574 32.1185 20'
    '.501 33.4824 22.1077C35.3301 21.2267 37.3548 20.7798 39.4016 20.8013Z" fill='
    '"white"/><path class="daggy-shadow" d="M50.8881 39.9307C51.5687 37.6047 51.8'
    '685 35.9906 51.8685 34.9423C51.8478 34.4896 51.6537 34.0622 51.3266 33.7487C'
    '50.9994 33.4351 50.5642 33.2593 50.111 33.2578C49.6888 33.2657 49.2814 33.41'
    '44 48.9535 33.6804C48.6256 33.9464 48.396 34.3144 48.3013 34.7259C48.1162 35'
    '.4899 47.7198 37.5604 47.3287 38.8746C47.4903 38.3826 47.6381 37.8828 47.772'
    ' 37.3752C47.9936 36.4834 48.6612 36.0401 49.5477 36.0401C50.0036 36.0578 50.'
    '4341 36.2544 50.746 36.5873C51.0579 36.9201 51.226 37.3625 51.214 37.8185C51'
    '.1931 38.5354 51.0783 39.2463 50.8724 39.9333L50.8881 39.9307Z" fill="#C9C6F'
    'A"/><path class="daggy-shadow" d="M60.3271 37.5004C60.3058 37.0484 60.1119 3'
    '6.6218 59.7854 36.3084C59.459 35.995 59.0248 35.8188 58.5722 35.8159C58.1505'
    ' 35.8243 57.7437 35.9734 57.4163 36.2393C57.0889 36.5053 56.8598 36.873 56.7'
    '652 37.284C56.5774 38.0663 56.1732 40.168 55.7769 41.4797H55.7951C55.9881 40'
    '.8955 56.168 40.3036 56.3166 39.7013C56.5383 38.8095 57.2058 38.3662 58.095 '
    '38.3662C58.5506 38.3838 58.9809 38.5805 59.2923 38.9135C59.6038 39.2464 59.7'
    '714 39.6888 59.7587 40.1446C59.746 40.6982 59.6698 41.2485 59.5318 41.7848C6'
    '0.0846 39.8447 60.3271 38.4418 60.3271 37.5004Z" fill="#C9C6FA"/><path d="M3'
    '9.4009 20.8014C40.5911 20.799 41.7761 20.9577 42.9237 21.2734C43.5263 20.451'
    '7 43.8273 19.4476 43.7763 18.43C43.7253 17.4124 43.3253 16.4434 42.6437 15.6'
    '862C41.962 14.9289 41.0403 14.4296 40.0336 14.2722C39.027 14.1148 37.9968 14'
    '.309 37.1166 14.8221L38.9419 17.8261L35.5233 16.3476C35.0603 17.0948 34.8281'
    ' 17.962 34.8559 18.8406C34.8838 19.7192 35.1704 20.57 35.6798 21.2864C36.893'
    ' 20.9594 38.1444 20.7963 39.4009 20.8014Z" fill="#163B36"/><path class="dagg'
    'y-shadow" d="M13.0639 40.1334C14.5041 40.1334 15.6715 38.966 15.6715 37.5258'
    'C15.6715 36.0857 14.5041 34.9182 13.0639 34.9182C11.6238 34.9182 10.4563 36.'
    '0857 10.4563 37.5258C10.4563 38.966 11.6238 40.1334 13.0639 40.1334Z" fill="'
    '#C9C6FA"/><path class="daggy-shadow" d="M13.0639 29.7035C14.5041 29.7035 15.'
    '6715 28.536 15.6715 27.0959C15.6715 25.6558 14.5041 24.4883 13.0639 24.4883C'
    '11.6238 24.4883 10.4563 25.6558 10.4563 27.0959C10.4563 28.536 11.6238 29.70'
    '35 13.0639 29.7035Z" fill="#C9C6FA"/><path class="daggy-shadow" d="M7.84895 '
    '34.9191C9.28909 34.9191 10.4566 33.7516 10.4566 32.3115C10.4566 30.8713 9.28'
    '909 29.7039 7.84895 29.7039C6.4088 29.7039 5.24133 30.8713 5.24133 32.3115C5'
    '.24133 33.7516 6.4088 34.9191 7.84895 34.9191Z" fill="#C9C6FA"/><path class='
    '"daggy-shadow" d="M19.5832 49.8241H20.1047C28.8793 49.8241 36.9864 43.5971 3'
    '9.2081 35.0311C39.4298 34.1419 40.0947 33.696 40.9839 33.696C41.4399 33.7143'
    ' 41.8704 33.9115 42.1823 34.2447C42.4941 34.578 42.6622 35.0207 42.6502 35.4'
    '77C42.6104 36.3872 42.4045 37.2824 42.0426 38.1185C42.1055 38.0214 42.1604 3'
    '7.9193 42.2069 37.8134C42.8667 36.3053 43.2076 34.6771 43.2082 33.031C43.208'
    '2 30.473 41.4298 28.8041 38.8769 28.8041C34.4257 28.8041 33.5339 32.6894 31.'
    '6564 36.8434C29.7086 41.1564 26.1309 45.8214 17.1086 45.8214C7.73419 45.8214'
    ' -1.89834 39.258 0.45112 25.9122C0.479804 25.7453 0.50327 25.5967 0.521523 2'
    '5.4585C0.177264 27.1827 0.00259008 28.9364 0 30.6946C0.0260762 41.438 8.4199'
    '9 49.5737 19.5832 49.8241Z" fill="#C9C6FA"/></svg>'
)

# Diagnostic : le trace d'un moniteur cardiaque, et un point qui bat. C'est
# ce que la page montre -- l'etat de sante de l'installation -- et c'est deja
# la marque que porte sa propre barre du haut : les deux se repondent.
ICONE_DIAGNOSTIC = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="16" fill="#0e7c86"/>'
    '<path d="M12 33h9l5-14 9 28 5-14h12" fill="none" stroke="#fff" '
    'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
    '</svg>')

# Mailpit : une enveloppe blanche au trait sur un degrade violet -- l'icone
# des maquettes de son interface. LE MEME dessin que celui de l'interface
# Mailpit de CodeLab (dagster/proxy/mailpit-ui/logo.svg) : la tuile et
# l'application se reconnaissent. Dessin original, pas le logo de Mailpit.
# Jamais dans un onglet : l'onglet porte toujours le logo CodeLab.
ICONE_MAILPIT = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-'
    'label="Mailpit"><defs><linearGradient id="cl-mp" x1="0" y1="0" x2="1" y2="1"'
    '><stop offset="0" stop-color="#7c6cf6"/><stop offset="1" stop-color="#4f46e5'
    '"/></linearGradient></defs><rect width="64" height="64" rx="18" fill="url(#c'
    'l-mp)"/><rect x="14" y="19" width="36" height="26" rx="5" fill="none" stroke'
    '="#fff" stroke-width="4" stroke-linejoin="round"/><path d="M16 22 32 34 48 2'
    '2" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-'
    'linejoin="round"/></svg>'
)

# Messages : deux bulles de conversation, la premiere qui ecrit (trois
# points), sur un degrade sarcelle -- l'accent de CodeLab. On y lit d'abord
# « messages », ce que l'application recoit, avant le travail qu'on en tire.
ICONE_MESSAGERIE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs>'
    '<linearGradient id="cl-ms" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#2fc7b8"/><stop offset="1" stop-color="#0b6c74"/>'
    '</linearGradient></defs>'
    '<rect width="64" height="64" rx="18" fill="url(#cl-ms)"/>'
    '<path d="M27 13h20a8 8 0 0 1 8 8v10a8 8 0 0 1-8 8h-1v6.5L39.5 39H27a8 8 0 0 1-8-8V21'
    'a8 8 0 0 1 8-8z" fill="#fff" opacity=".42"/>'
    '<path d="M17 22h21a8 8 0 0 1 8 8v11a8 8 0 0 1-8 8H26.5L18 56.5V49h-1a8 8 0 0 1-8-8V30'
    'a8 8 0 0 1 8-8z" fill="#fff"/>'
    '<circle cx="19.5" cy="35.5" r="2.7" fill="#0b6c74"/>'
    '<circle cx="27.5" cy="35.5" r="2.7" fill="#0b6c74"/>'
    '<circle cx="35.5" cy="35.5" r="2.7" fill="#0b6c74"/></svg>')

ICONES_PAR_DEFAUT = {
    MESSAGERIE_NOM: ICONE_MESSAGERIE,
    DAGSTER_NOM: ICONE_DAGSTER,
    DIAGNOSTIC_NOM: ICONE_DIAGNOSTIC,
    MAILPIT_NOM: ICONE_MAILPIT,
}


# OUVRIR DAGSTER PASSE PAR ICI, et pas directement par le port 3000.
#
# Trois choses en une seule redirection :
#
#   1. l'ouverture est NOTEE. C'est ce qui donne a Dagster l'onglet
#      « Activite » des autres applications -- qui l'a ouverte, quand,
#      combien de fois. Sans ce passage, le panneau ne voyait rien : le lien
#      partait droit sur un autre port.
#   2. elle s'ouvre A LA PLACE de la page courante, comme une application.
#      L'ancien lien s'ouvrait dans un nouvel onglet, parce qu'il n'y avait
#      alors aucun moyen de revenir ; le ruban de retour pose par le proxy
#      de Dagster a supprime cette raison.
#   3. l'adresse est construite par le SERVEUR, a partir de l'hote demande :
#      la page n'a plus a deviner sur quel nom de machine on est arrive.
#
# Une redirection, et non un proxy : l'interface de Dagster suit ses runs par
# websocket, et le proxy du panneau ne sait pas relayer une connexion
# montante. Le proxy de Dagster, lui, est fait pour cela.
def ouvrir_dagster():
    hors = refus_admin_hors_reseau()
    if hors:
        journaliser("refus", app=DAGSTER_NOM, motif="hors reseau local",
                    ip=_adresse_client())
        return Response(_page("Accès refusé", hors), 403, mimetype="text/html")
    adresse = origine_dagster()
    if not adresse:
        return Response(_page("Adresse introuvable",
                              "Le panneau n'a pas su construire l'adresse de "
                              "Dagster depuis cette requête."),
                        503, mimetype="text/html")
    journaliser_ouverture(DAGSTER_NOM)
    return redirect(adresse, 302)


flask_app.add_url_rule("/" + DAGSTER_NOM + "/", "ouvrir_dagster",
                       require_admin(ouvrir_dagster))
flask_app.add_url_rule("/" + DAGSTER_NOM, "ouvrir_dagster_sans_barre",
                       require_admin(ouvrir_dagster))


# OUVRIR MAILPIT : la meme porte que Dagster, pour les memes raisons --
# l'ouverture est notee, l'adresse construite par le serveur, et le reseau
# local verifie si l'administration y est limitee.
def ouvrir_mailpit():
    hors = refus_admin_hors_reseau()
    if hors:
        journaliser("refus", app=MAILPIT_NOM, motif="hors reseau local",
                    ip=_adresse_client())
        return Response(_page("Accès refusé", hors), 403, mimetype="text/html")
    adresse = origine_mailpit()
    if not adresse:
        return Response(_page("Adresse introuvable",
                              "Le panneau n'a pas su construire l'adresse de "
                              "Mailpit depuis cette requête."),
                        503, mimetype="text/html")
    journaliser_ouverture(MAILPIT_NOM)
    return redirect(adresse, 302)


flask_app.add_url_rule("/" + MAILPIT_NOM + "/", "ouvrir_mailpit",
                       require_admin(ouvrir_mailpit))
flask_app.add_url_rule("/" + MAILPIT_NOM, "ouvrir_mailpit_sans_barre",
                       require_admin(ouvrir_mailpit))


# L'ICONE DE DAGSTER : CELLE QUE DAGSTER SERT LUI-MEME.
#
# Un dessin fait a la main ne sera jamais le logo de Dagster -- au mieux une
# ressemblance, au pire une marque deformee. Or l'application porte le sien,
# a la racine de son serveur, et le panneau sait lui parler. Il le lui
# demande donc, et le garde : c'est LE logo, exactement celui qu'on voit dans
# l'onglet quand on ouvre Dagster, et il suivra ses evolutions sans que
# personne n'ait a redessiner quoi que ce soit.
#
# Le dessin d'origine reste, en repli : tant que Dagster n'a pas repondu une
# premiere fois -- stack qui demarre, conteneur arrete -- la tuile doit
# afficher quelque chose, et surtout pas la lettre « D » qu'elle partagerait
# avec « demo » et « diagnostic ».
# LE LOGO DE DAGSTER NE SE TELECHARGE PLUS, ET C'EST UNE CORRECTION.
#
# Le panneau allait chercher /favicon.ico sur l'instance Dagster et servait
# ce qu'il trouvait, en priorite sur le SVG ci-dessus. L'intention etait
# bonne -- porter le vrai logo plutot qu'une reproduction. Le resultat ne
# l'etait pas : un favicon fait 32 px de cote, la tuile du hub en fait 40 a
# 48. Le navigateur agrandissait donc une image matricielle, et le logo
# paraissait flou. Signale tel quel : « choisis un logo plus net ».
#
# Un test affirmait depuis longtemps que ce logo « est vectoriel et net ».
# Il lisait la constante, pas la reponse servie : la garantie existait sur le
# papier et etait defaite a l'execution par la branche qui est retiree ici.
# Le test de la reponse, lui, est ajoute avec ce changement.
#
# Ce qui disparait avec la branche : quatre chemins candidats, un cache sur
# disque et son fichier de type, une lecture des octets pour verifier que
# Dagster ne renvoie pas sa page d'accueil en guise d'image, et un appel
# reseau toutes les 24 h. Le SVG livre avec l'image ne demande rien a
# personne, reste net a toutes les tailles, et s'affiche meme quand Dagster
# est eteint -- ce qui est precisement le moment ou l'on regarde sa tuile.


# L'ONGLET DU HUB PORTE LE LOGO CODELAB, ET RIEN D'AUTRE.
#
# L'icone etait ecrite DANS la page (<link rel="icon" href="data:...">).
# Plusieurs navigateurs n'en tiennent pas compte pour l'onglet -- Safari en
# tete -- et retombent sur leur cache d'icones ou sur /favicon.ico, qui
# repondait 404 : l'onglet du hub montrait alors une icone vue ailleurs.
# Les deux pages pointent desormais vers /favicon.png?v=... : une vraie
# adresse, que tous suivent, et versionnee pour qu'aucun cache ne serve un
# ancien dessin. /favicon.ico repond la meme chose, pour ceux qui la
# demandent d'eux-memes.
#
# Cela n'a pas suffi : Safari range les icones par NOM D'HOTE, sans le port.
# Mailpit (8025) servait son logo sous /favicon.ico, et l'onglet du panneau
# le reprenait des qu'on revenait d'une application. D'ou :
#   - /icone-codelab.png, une adresse qu'aucun autre service ne sert ;
#   - /favicon.ico = le logo CodeLab sur TOUS les ports du nom d'hote
#     (le panneau, l'origine des applications, Mailpit) : quoi que Safari
#     retienne pour l'hote, c'est le notre.
# Une application garde son icone : celle qu'elle declare, ou celle que le
# proxy lui injecte, passe toujours avant /favicon.ico.
@flask_app.get("/favicon.ico")
@flask_app.get("/favicon.png")
@flask_app.get("/icone-codelab.png")
@flask_app.get("/codelab-onglet.png")
def favicon_panneau():
    r = Response(ICONE_CODELAB_PNG, mimetype="image/png")
    r.headers["Cache-Control"] = "no-cache"
    return r


# LE PANNEAU S'AJOUTE A L'ECRAN D'ACCUEIL D'UN TELEPHONE.
#
# Safari > Partager > « Sur l'ecran d'accueil » : sans ce qui suit, iOS posait
# une capture de la page en guise d'icone, et l'ouvrait dans un onglet Safari
# ordinaire, barre d'adresse comprise. Le manifeste et les balises
# apple-mobile-web-app-* des deux pages en font une application a part :
# plein ecran, son propre nom sous l'icone, son propre logo.
#
# Publics, comme le favicon : iOS les demande au moment d'ajouter l'icone,
# souvent depuis la page de connexion, et ils ne revelent qu'un nom et un
# logo. /apple-touch-icon.png est aussi l'adresse qu'iOS essaie de lui-meme
# quand une page n'en declare aucune.
#
# A savoir : sur iOS, l'application ajoutee a SES PROPRES cookies, separes de
# ceux de Safari. Il faut donc s'y connecter une fois, la session ouverte
# dans Safari ne la suit pas.
@flask_app.get("/apple-touch-icon.png")
@flask_app.get("/apple-touch-icon-precomposed.png")
def ecran_accueil_icone():
    r = Response(ICONE_ECRAN_ACCUEIL_PNG, mimetype="image/png")
    r.headers["Cache-Control"] = "no-cache"
    return r


# L'ECRAN DE DEMARRAGE. Ouverte depuis l'icone, une application web iOS
# affichait un ecran BLANC le temps de charger : une vraie app montre son fond
# et son logo. iOS exige une image par taille d'ecran (apple-touch-startup-
# image, choisie par media query) : le panneau les fabrique a la demande, au
# pixel pres, en Python pur -- fond de la couleur du bandeau, logo au centre
# -- et les garde en memoire. Clair et sombre.
ECRANS_DEMARRAGE = {
    # (largeur, hauteur) en points, portrait, densite. Les iPhone depuis le
    # SE...
    (320, 568, 2), (375, 667, 2), (414, 896, 2), (375, 812, 3), (414, 896, 3),
    (390, 844, 3), (428, 926, 3), (393, 852, 3), (430, 932, 3), (402, 874, 3),
    (440, 956, 3), (360, 780, 3),
    # ... et les iPad, du mini au Pro 13 pouces.
    (744, 1133, 2), (768, 1024, 2), (810, 1080, 2), (820, 1180, 2),
    (834, 1112, 2), (834, 1194, 2), (834, 1210, 2), (1024, 1366, 2),
    (1032, 1376, 2),
}
# Une tablette s'ouvre aussi bien couchee : les iPad ont aussi leur image en
# paysage. Un iPhone, lui, demarre toujours en portrait.
def _orientations(largeur):
    return ("portrait", "landscape") if largeur >= 744 else ("portrait",)


def _images_de_demarrage():
    """{(largeur, hauteur) en pixels: (densite, orientation, media)}."""
    images = {}
    for w, h, d in ECRANS_DEMARRAGE:
        for orientation in _orientations(w):
            taille = (w * d, h * d) if orientation == "portrait" else (h * d, w * d)
            media = (f"screen and (device-width: {w}px) and (device-height: {h}px) and "
                     f"(-webkit-device-pixel-ratio: {d}) and (orientation: {orientation})")
            images[taille] = (d, media)
    return images


IMAGES_DEMARRAGE = _images_de_demarrage()
FONDS_DEMARRAGE = {"clair": (0xE9, 0xEB, 0xEE), "sombre": (0x0D, 0x10, 0x15)}
_ecrans_faits = {}


def _png_lire_rgba(donnees):
    """Decode un PNG 8 bits RGBA non entrelace (le logo du panneau)."""
    largeur, hauteur, prof, couleur = struct.unpack(">IIBB", donnees[16:26])
    if prof != 8 or couleur != 6:
        raise ValueError("PNG RGBA 8 bits attendu")
    idat, i = b"", 8
    while i < len(donnees):
        n = struct.unpack(">I", donnees[i:i + 4])[0]
        if donnees[i + 4:i + 8] == b"IDAT":
            idat += donnees[i + 8:i + 8 + n]
        i += 12 + n
    brut, ligne, pas = zlib.decompress(idat), largeur * 4, 4
    pixels, prec, j = [], bytearray(ligne), 0
    for _ in range(hauteur):
        filtre, cur = brut[j], bytearray(brut[j + 1:j + 1 + ligne])
        j += 1 + ligne
        for x in range(ligne):
            a = cur[x - pas] if x >= pas else 0
            b = prec[x]
            c = prec[x - pas] if x >= pas else 0
            if filtre == 1:
                cur[x] = (cur[x] + a) & 255
            elif filtre == 2:
                cur[x] = (cur[x] + b) & 255
            elif filtre == 3:
                cur[x] = (cur[x] + ((a + b) >> 1)) & 255
            elif filtre == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[x] = (cur[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        pixels.append(bytes(cur))
        prec = cur
    return largeur, hauteur, pixels


def _agrandir(largeur, hauteur, pixels, facteur):
    """Agrandissement bilineaire, pour un logo net sur un ecran Retina."""
    if facteur == 1:
        return largeur, hauteur, pixels
    nl, nh = int(largeur * facteur), int(hauteur * facteur)
    sortie = []
    for y in range(nh):
        fy = min(max((y + .5) / facteur - .5, 0), hauteur - 1)
        y0 = int(fy); y1 = min(y0 + 1, hauteur - 1); dy = fy - y0
        r0, r1, ligne = pixels[y0], pixels[y1], bytearray(nl * 4)
        for x in range(nl):
            fx = min(max((x + .5) / facteur - .5, 0), largeur - 1)
            x0 = int(fx); x1 = min(x0 + 1, largeur - 1); dx = fx - x0
            for k in range(4):
                v = (r0[x0 * 4 + k] * (1 - dx) + r0[x1 * 4 + k] * dx) * (1 - dy) \
                    + (r1[x0 * 4 + k] * (1 - dx) + r1[x1 * 4 + k] * dx) * dy
                ligne[x * 4 + k] = int(v + .5)
        sortie.append(bytes(ligne))
    return nl, nh, sortie


def _png_ecrire_rgb(largeur, hauteur, lignes):
    def bloc(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    brut = b"".join(b"\x00" + l for l in lignes)
    return (b"\x89PNG\r\n\x1a\n" + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut, 6)) + bloc(b"IEND", b""))


def ecran_de_demarrage(largeur, hauteur, theme):
    cle = (largeur, hauteur, theme)
    if cle not in _ecrans_faits:
        fond = FONDS_DEMARRAGE[theme]
        lw, lh, logo = _png_lire_rgba(ICONE_CODELAB_PNG)
        # Le logo fait environ 90 points de large, quelle que soit la densite.
        densite = IMAGES_DEMARRAGE[(largeur, hauteur)][0]
        facteur = max(1, round(densite * 90 / lw * 10) / 10)
        lw, lh, logo = _agrandir(lw, lh, logo, facteur)
        ligne_fond = bytes(fond) * largeur
        x0, y0 = (largeur - lw) // 2, (hauteur - lh) // 2
        lignes = []
        for y in range(hauteur):
            if y0 <= y < y0 + lh:
                ligne = bytearray(ligne_fond)
                src = logo[y - y0]
                for x in range(lw):
                    a = src[x * 4 + 3]
                    if a:
                        o = (x0 + x) * 3
                        for k in range(3):
                            ligne[o + k] = (src[x * 4 + k] * a + fond[k] * (255 - a)) // 255
                lignes.append(bytes(ligne))
            else:
                lignes.append(ligne_fond)
        _ecrans_faits[cle] = _png_ecrire_rgb(largeur, hauteur, lignes)
    return _ecrans_faits[cle]


# LES ICONES DU MANIFESTE. Android et Chrome demandent du 192 et du 512 px ;
# l'icone d'ecran d'accueil d'iOS (180 px) etait etiree jusque-la, floue.
# « maskable » : Android decoupe l'icone en cercle ou en goutte, le logo se
# tient donc dans la zone sure (les 80 % du centre). Meme fabrication que
# l'ecran de demarrage, fond blanc comme l'icone d'iOS, gardee en memoire.
ICONES_MANIFESTE = {"192": (192, .62), "512": (512, .62), "512-maskable": (512, .46)}
_icones_faites = {}


def icone_manifeste(nom):
    if nom not in _icones_faites:
        cote, part = ICONES_MANIFESTE[nom]
        fond = (255, 255, 255)
        lw, lh, logo = _png_lire_rgba(ICONE_CODELAB_PNG)
        lw, lh, logo = _agrandir(lw, lh, logo, cote * part / lw)
        x0, y0 = (cote - lw) // 2, (cote - lh) // 2
        ligne_fond, lignes = bytes(fond) * cote, []
        for y in range(cote):
            if not y0 <= y < y0 + lh:
                lignes.append(ligne_fond)
                continue
            ligne, src = bytearray(ligne_fond), logo[y - y0]
            for x in range(lw):
                a = src[x * 4 + 3]
                if a:
                    o = (x0 + x) * 3
                    for k in range(3):
                        ligne[o + k] = (src[x * 4 + k] * a + fond[k] * (255 - a)) // 255
            lignes.append(bytes(ligne))
        _icones_faites[nom] = _png_ecrire_rgb(cote, cote, lignes)
    return _icones_faites[nom]


@flask_app.get("/icone-<nom>.png")
def icone_du_manifeste(nom):
    if nom not in ICONES_MANIFESTE:
        return Response("Inconnu", status=404, mimetype="text/plain")
    r = Response(icone_manifeste(nom), mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


@flask_app.get("/ecran-demarrage/<int:largeur>x<int:hauteur>-<theme>.png")
def ecran_demarrage(largeur, hauteur, theme):
    if (largeur, hauteur) not in IMAGES_DEMARRAGE or theme not in FONDS_DEMARRAGE:
        return Response("Inconnu", status=404, mimetype="text/plain")
    r = Response(ecran_de_demarrage(largeur, hauteur, theme), mimetype="image/png")
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


def balises_ecran_de_demarrage():
    """Les <link rel="apple-touch-startup-image"> des pages du panneau."""
    return "\n".join(
        f'<link rel="apple-touch-startup-image" media="{media} and '
        f'(prefers-color-scheme: {schema})" href="/ecran-demarrage/{w}x{h}-{theme}.png">'
        for (w, h), (_, media) in sorted(IMAGES_DEMARRAGE.items())
        for theme, schema in (("clair", "light"), ("sombre", "dark")))


BALISES_ECRAN_DE_DEMARRAGE = balises_ecran_de_demarrage()


# HORS LIGNE, L'APPLICATION S'OUVRE QUAND MEME. Sans serveur (ou sans
# reseau), une application web installee affichait la page d'erreur de
# Safari : plus rien d'une app. Un service worker -- enregistre seulement en
# mode application, pas dans le navigateur, ou le panneau reste un site --
# garde UNE page en reserve, /hors-ligne.html, et la montre quand une page du
# panneau ne repond pas. Il ne met en cache ni les pages du panneau (elles
# portent la session) ni aucune donnee, et laisse passer tout le reste.
PAGE_HORS_LIGNE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#E9EBEE" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0D1015" media="(prefers-color-scheme: dark)">
<title>CodeLab</title>
<style>
:root{--bg:#E9EBEE;--txt:#14181d;--dim:#5b6470;--carte:#fff;--ligne:#dadee3}
@media (prefers-color-scheme: dark){:root{--bg:#0D1015;--txt:#e8ebef;--dim:#98a2ae;--carte:#161B22;--ligne:#262E38}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
 font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 -webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;overscroll-behavior:none}
main{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
 padding:24px;box-sizing:border-box;text-align:center}
img{width:72px;height:72px}
h1{font-size:20px;margin:6px 0 0}
p{margin:0;color:var(--dim);max-width:320px}
button{margin-top:8px;font:inherit;font-weight:650;padding:10px 20px;border-radius:999px;border:1px solid var(--ligne);
 background:var(--carte);color:var(--txt)}
</style></head><body><main>
<img src="data:image/png;base64,__LOGO__" alt="">
<h1>Serveur injoignable</h1>
<p>CodeLab ne répond pas : le serveur est arrêté, ou ton téléphone n'a pas de réseau.</p>
<p id="etat">Nouvel essai dans 5 s.</p>
<button type="button" onclick="essayer()">Réessayer</button>
</main><script>
var n=5;
function essayer(){
  document.getElementById('etat').textContent='Connexion…';
  fetch('/health',{cache:'no-store'}).then(function(r){ if(r.ok) location.reload(); else attendre(); }, attendre);
}
function attendre(){ n=5; tic(); }
function tic(){
  if(n<=0){ essayer(); return; }
  document.getElementById('etat').textContent='Nouvel essai dans '+n+' s.';
  n--; setTimeout(tic,1000);
}
tic();
window.addEventListener('online', essayer);
</script></body></html>"""

SERVICE_WORKER = """// CodeLab, mode application : une page de reserve quand le serveur se tait.
const CACHE = 'codelab-hors-ligne-v2';
const RESERVE = ['/hors-ligne.html'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(RESERVE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(cles => Promise.all(cles.filter(c => c !== CACHE).map(c => caches.delete(c))))
    .then(() => self.clients.claim()));
});
// Seules les PAGES passent par ici, et seulement en cas d'echec : le reste
// (API, images, applications) va au reseau sans detour.
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => caches.match('/hors-ligne.html')));
});
// Les notifications : le panneau pousse {titre, texte, url, etiquette},
// chiffre de bout en bout. Une meme etiquette remplace la precedente (une
// application tombee puis revenue ne laisse qu'une notification).
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {}
  e.waitUntil(self.registration.showNotification(d.titre || 'CodeLab', {
    body: d.texte || '', icon: '/apple-touch-icon.png?v=4',
    tag: d.etiquette || undefined, renotify: !!d.etiquette,
    data: { url: d.url || '/' }
  }));
});
// Toucher la notification ouvre CodeLab a la bonne page : la fenetre deja
// ouverte si elle existe, sinon une nouvelle. Jamais une adresse d'ailleurs.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  let cible = new URL('/', self.location.origin);
  try {
    const u = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin);
    if (u.origin === self.location.origin) cible = u;
  } catch (err) {}
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(fenetres => {
    for (const f of fenetres) {
      if (new URL(f.url).origin === self.location.origin && 'focus' in f) {
        return f.focus().then(g => (g || f).navigate ? (g || f).navigate(cible.href) : null);
      }
    }
    return self.clients.openWindow(cible.href);
  }));
});
"""


@flask_app.get("/hors-ligne.html")
def page_hors_ligne():
    # Le logo est DANS la page : hors ligne, rien d'autre ne se charge.
    r = Response(PAGE_HORS_LIGNE.replace("__LOGO__", base64.b64encode(ICONE_CODELAB_PNG).decode()),
                 mimetype="text/html")
    r.headers["Cache-Control"] = "no-cache"
    return r


@flask_app.get("/sw.js")
def service_worker():
    r = Response(SERVICE_WORKER, mimetype="text/javascript")
    # Toujours relu : une nouvelle version du panneau remplace l'ancienne.
    r.headers["Cache-Control"] = "no-cache"
    return r


@flask_app.get("/manifest.webmanifest")
def ecran_accueil_manifeste():
    manifeste = {
        "name": "CodeLab",
        "short_name": "CodeLab",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        # La couleur du fond et du bandeau (style par defaut, clair) : c'est
        # celle de l'ecran de lancement et de la barre d'etat d'Android. Une
        # barre blanche au-dessus d'un bandeau gris cassait l'effet
        # d'application ; la page l'ajuste ensuite au theme choisi.
        "background_color": "#E9EBEE",
        "theme_color": "#E9EBEE",
        # Un identifiant stable : l'application installee reste la meme si
        # start_url change un jour.
        "id": "/",
        "lang": "fr",
        "description": "Ton atelier de développement : ouvrir, mettre en ligne et partager tes applications.",
        "orientation": "any",
        "categories": ["developer", "productivity"],
        "icons": [{"src": "/apple-touch-icon.png", "sizes": "180x180",
                   "type": "image/png", "purpose": "any"},
                  {"src": "/icone-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/icone-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": "/icone-512-maskable.png", "sizes": "512x512", "type": "image/png",
                   "purpose": "maskable"}],
        # L'appui long sur l'icone (Android, Windows, Dock du Mac) : aller
        # droit ou l'on va le plus souvent.
        "shortcuts": [
            {"name": "Hub", "url": "/", "icons": [{"src": "/icone-192.png", "sizes": "192x192"}]},
            {"name": "Messages", "url": "/messages",
             "icons": [{"src": "/icone-192.png", "sizes": "192x192"}]},
            {"name": "Paramètres", "url": "/parametres",
             "icons": [{"src": "/icone-192.png", "sizes": "192x192"}]},
        ],
    }
    r = Response(json.dumps(manifeste), mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    return r


@flask_app.get("/api/icon/<n>")
@require_auth
def api_icon(n):
    reponse = _api_icon(n)
    # Gardee cinq minutes par le navigateur : le hub se redessine toutes les
    # dix secondes, et une icone redemandee a chaque fois faisait clignoter
    # toutes les tuiles. « private » : la reponse depend de la session.
    reponse.headers["Cache-Control"] = "private, max-age=300"
    return reponse


def _api_icon(n):
    """L'icone d'une application. Trois sources, dans cet ordre.

      1. le logo DEPOSE dans le projet -- il gagne toujours, c'est un choix
         explicite de celui qui l'a pose ;
      2. l'icone livree avec CodeLab, pour les applications par defaut. C'est
         un DEFAUT, pas une reservation : elle ne recouvre pas un logo pose ;
      3. l'initiale coloree, pour tout le reste.

    Sans le 2, « dagster », « diagnostic » et « demo » tombaient sur le meme
    « D » colore : trois carres a distinguer en lisant le nom dessous,
    c'est-a-dire en cessant de les reconnaitre d'un coup d'oeil.
    """
    # Accessible a un compte utilisateur, pour que son espace affiche les
    # icones -- mais seulement des projets qu'il peut ouvrir : la liste des
    # icones est une liste des projets existants.
    if not peut_voir(n):
        return Response(default_icon_svg(n), mimetype="image/svg+xml")
    apps = load()
    a = apps.get(n)
    icon_path = find_icon(a["path"]) if a else None
    if not icon_path and n in ICONES_PAR_DEFAUT:
        return Response(ICONES_PAR_DEFAUT[n], mimetype="image/svg+xml",
                        headers={"X-Content-Type-Options": "nosniff"})
    if icon_path:
        # UNE IMAGE, ET RIEN QUE CA. Ce chemin sert un fichier pris dans le
        # dossier d'un projet, dans l'ORIGINE DU PANNEAU. Un SVG depose a la
        # main y executerait son script : la politique ci-dessous le rend
        # inerte, et nosniff empeche le navigateur de deviner un autre type
        # que celui annonce.
        reponse = send_file(icon_path)
        reponse.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        return reponse
    return Response(default_icon_svg(n), mimetype="image/svg+xml",
                    headers={"X-Content-Type-Options": "nosniff"})




# ------------------------------ pages --------------------------------

# La police du panneau, servie depuis l'image. 25 Ko, une seule fois, et le
# panneau ne demande rien a personne : un serveur auto-heberge qui irait
# chercher sa police chez Google ferait fuiter l'adresse IP de chaque visiteur
# vers un tiers, et s'afficherait mal des que la machine est hors ligne.
POLICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polices")


@flask_app.get("/polices/<nom>")
def police(nom):
    """Un fichier de police, et rien d'autre.

    La liste blanche est explicite : sans elle, ce chemin deviendrait une
    lecture de fichier arbitraire des qu'un nom contient "..". Werkzeug
    refuse deja les segments de ce genre, mais la garde ne doit pas dependre
    d'un detail du routeur.
    """
    if nom not in POLICES_SERVIES:
        return Response("Inconnu", status=404, mimetype="text/plain")
    return send_file(os.path.join(POLICES_DIR, nom), mimetype="font/woff2",
                     max_age=31536000)


@flask_app.get("/theme.css")
def theme_css():
    """Le theme, servi aux deux pages.

    Public sans session : la page de CONNEXION en a besoin, et il n'y a la
    que des couleurs. Le proteger n'aurait rien protege et aurait servi une
    page de connexion sans style.

    Werkzeug range les regles litterales avant les regles a variable : cette
    route gagne sur "/<n>", qui sert les applications hebergees. Et un nom
    d'application ne peut de toute facon pas contenir de point.

    Un cache court plutot qu'aucun : le fichier ne change qu'au deploiement
    d'une nouvelle image, mais une minute suffit a eviter de le redemander a
    chaque page sans qu'une mise a jour tarde a se voir.
    """
    return Response(THEME_CSS, mimetype="text/css",
                    headers={"Cache-Control": "public, max-age=60"})


@flask_app.get("/mode.js")
def mode_js():
    """Le mode -- site web ou application --, lu une fois pour les trois pages.

    Public sans session pour la meme raison que le theme : la page de
    connexion en a besoin, et il n'y a la qu'une detection d'affichage. Meme
    cache court.
    """
    return Response(MODE_JS, mimetype="text/javascript",
                    headers={"Cache-Control": "public, max-age=60"})


def _json_dans_un_script(valeur):
    """Une valeur en JSON, prete a entrer dans une balise script : « < »
    s'ecrit \\u003c (le meme caractere pour JavaScript), et rien ne peut
    donc y ecrire « </script> »."""
    return json.dumps(valeur).replace("<", "\\u003c")


@flask_app.get("/panneau.js")
@require_auth
def panneau_js():
    """Le script de la page du panneau (dashboard.html).

    A une session seulement : il ne porte aucun secret (chaque valeur propre
    a la session est dans la page, window.CODELAB_PAGE), mais il n'a rien a
    faire chez un visiteur, et la page qui le charge exige deja la session.
    CACHE : l'adresse porte l'empreinte du fichier (PANNEAU_JS_VERSION). A la
    bonne empreinte, le navigateur le garde un an sans redemander ; sinon
    (ancienne page, adresse tapee a la main), il le revalide a chaque fois.
    « private » : il passe par une session, aucun cache partage ne le garde.
    """
    garder = request.args.get("v") == PANNEAU_JS_VERSION
    return Response(PANNEAU_JS, mimetype="text/javascript",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"
                             if garder else "private, no-cache"})


@flask_app.get("/login")
def login_page(invitation=False):
    # Une invitation s'ouvre meme avec une session en cours : c'est souvent
    # l'administrateur qui essaie le lien qu'il vient d'envoyer.
    if is_authed() and not invitation:
        # Deja connecte : « Se connecter avec CodeLab » depuis une
        # application ramene directement dedans, sans formulaire.
        return redirect(suite_locale(request.args.get("suite")))
    return Response(avec_style(LOGIN_PAGE), mimetype="text/html")


# LES REGLAGES DU HUB, poses en attribut sur <html> (data-tuiles,
# data-defilement...). La premiere valeur de chaque reglage est celle par
# defaut. Le fond et les surfaces sont ceux de data-variante="bento"
# (theme.css).
REGLAGES_HUB = {
    "tuiles": ("cartes", "icones"),
    "defilement": ("horizontal", "vertical"),
    "widgets": ("melanges", "haut"),
    # Le nuancier : la couleur d'accent du panneau (boutons, liens, onglet
    # actif), en clair comme en sombre. Jetons dans theme.css.
    "couleurs": ("lagon", "ocean", "foret", "prune", "corail", "ardoise"),
}


def reglages_du_hub():
    """Les reglages du hub, lus dans le cookie codelab-hub.

    Le cookie liste des valeurs separees par des points
    (« icones.vertical.haut ») : chaque valeur n'appartient qu'a un
    reglage, l'ordre est libre. Rien d'inconnu n'est retenu -- le cookie
    vient du client, et seules les valeurs de REGLAGES_HUB arrivent dans
    la page.
    """
    reglages = {cle: valeurs[0] for cle, valeurs in REGLAGES_HUB.items()}
    brut = request.cookies.get("codelab-hub") or ""
    for valeur in brut.split(".")[:len(REGLAGES_HUB)]:
        for cle, valeurs in REGLAGES_HUB.items():
            if valeur in valeurs:
                reglages[cle] = valeur
    return reglages


def avec_style(page):
    """Pose le style, les reglages du hub et les ecrans de demarrage sur la
    page servie.

    Pose cote serveur, l'attribut est la avant la premiere peinture : la
    page ne change pas de forme le temps que son script le lise.
    """
    attributs = ' data-style="design" data-variante="bento"' + "".join(
        f' data-{cle}="{valeur}"' for cle, valeur in reglages_du_hub().items())
    return (page.replace('<html lang="fr">', '<html lang="fr"' + attributs + '>', 1)
            .replace("__ECRANS_DEMARRAGE__", BALISES_ECRAN_DE_DEMARRAGE, 1))


# LES PAGES DU PANNEAU ONT CHACUNE LEUR ADRESSE. Recharger les parametres
# laisse sur les parametres, un lien vers la fiche d'une application s'ouvre
# sur elle. C'est toujours la meme page (dashboard.html) : elle lit l'adresse
# et ouvre la bonne rubrique. Ces noms ne peuvent plus servir de chemin a une
# application (adresses_reservees).
PAGES_DU_PANNEAU = ("apercu", "applications", "utilisateurs", "messages", "parametres", "demandes")


def adresses_reservees():
    """Les premiers segments d'adresse qu'aucune application ne peut prendre.

    DERIVES DE LA TABLE DES ROUTES, pas recopies a la main : toute route du
    panneau -- /parametres, /invitation, /mot-de-passe, /qr, /polices,
    celles de demain -- est reservee des qu'elle existe. Une liste tenue a la
    main oubliait forcement la suivante, et l'application qui prenait ce nom
    devenait injoignable sur le port du panneau (la route du panneau passe
    avant le relais des applications).
    """
    noms = {"api", "static", "health", "login", "logout", *PAGES_DU_PANNEAU}
    for regle in flask_app.url_map.iter_rules():
        if regle.endpoint in ("proxy", "proxy_noslash", "static"):
            continue
        premier = regle.rule.lstrip("/").split("/", 1)[0]
        if premier and "<" not in premier:
            noms.add(premier.lower())
    return frozenset(noms)


def signaler_adresses_en_conflit():
    """Une application declaree AVANT qu'une route du panneau prenne son nom
    reste servie sur son origine (le port des applications), mais son lien
    sur le port du panneau ouvre la page du panneau. On le dit au demarrage."""
    for nom in sorted(set(load()) & adresses_reservees()):
        print(f"[codelab] l'application « {nom} » porte un nom reserve au panneau : "
              f"renomme-la (Applications > fiche > Renommer).", flush=True)


@flask_app.get("/apercu")
@flask_app.get("/applications")
@flask_app.get("/applications/<nom>")
@flask_app.get("/applications/<nom>/<onglet>")
@flask_app.get("/utilisateurs")
@flask_app.get("/parametres")
@flask_app.get("/parametres/<onglet>")
@require_auth
def page_du_panneau(**_):
    return index()


# L'APPLICATION « MESSAGES ». Elle s'est appelee « Demandes » (/demandes) :
# ces adresses ont pu etre mises en favori, elles y menent.
@flask_app.get("/demandes")
@flask_app.get("/demandes/<vue>")
@require_auth
def anciens_messages(**_):
    return redirect("/messages")


@flask_app.get("/messages")
@flask_app.get("/messages/<vue>")
@flask_app.get("/messages/<vue>/<cle>")
@require_auth
def page_messagerie(**_):
    """Les messages et la gestion de projet qui en sort, sur une page a eux.

    Pour l'administrateur. Un compte utilisateur a, a la meme adresse, SA
    rubrique « Messages » du panneau : ecrire a l'administrateur et suivre
    ce que chaque message est devenu (la page le route sur sa section).
    """
    if not est_admin():
        return index()
    page = MESSAGERIE_PAGE.replace("__JETON__", _json_dans_un_script(jeton_session()))
    return Response(avec_style(page), mimetype="text/html")


@flask_app.get("/")
@require_auth
def index():
    """Une seule application, un seul accueil.

    CodeLab est une page unique, servie a la meme adresse a tout le monde,
    et le hub (la liste des projets qu'on peut ouvrir) en est l'accueil,
    administrateur compris. Le role ne change pas de page : il ouvre en plus
    le menu lateral (vue d'ensemble, applications, journaux) et l'entree
    « Configuration » du menu du compte.

    Le role est injecte dans la page pour qu'elle sache quoi afficher --
    mais ce n'est qu'un confort d'affichage : chaque route d'administration
    verifie le role de son cote (require_admin), et une page bricolee ne
    donne donc aucun droit supplementaire.
    """
    # Les valeurs entrent dans un script en ligne (window.CODELAB_PAGE) :
    # _json_dans_un_script les ecrit en JSON ET y echappe « < », pour qu'aucune
    # ne puisse fermer la balise (un nom de compte, une racine, une adresse).
    page = (DASHBOARD_PAGE
            .replace("__JETON__", _json_dans_un_script(jeton_session()))
            .replace("__APPS_MODELE__", _json_dans_un_script(modele_adresse_application()))
            .replace("__ROOT__", _json_dans_un_script(ROOT))
            .replace("__ROLE__", _json_dans_un_script(role_courant() or ""))
            # Seul l'admin en a besoin : la page lui propose de regler l'envoi
            # tant qu'il manque, et grise la creation de comptes.
            .replace("__SMTP__", _json_dans_un_script(
                envoi_mail_configure() if role_courant() == "admin" else True))
            .replace("__UTILISATEUR__", _json_dans_un_script(utilisateur_courant()))
            .replace("__PANNEAU_VERSION__", PANNEAU_JS_VERSION))
    r = Response(avec_style(page), mimetype="text/html")
    # Le widget « A la une » du hub montre un APERCU de la derniere
    # application ouverte, dans un cadre : la politique du panneau, plus le
    # droit d'encadrer l'origine des applications -- et elle seule. Les
    # applications, elles, n'acceptent d'etre encadrees que par le panneau
    # (proteger_l_encadrement).
    r.headers["Content-Security-Policy"] = CSP_PANNEAU + "; frame-src " + cadres_permis()
    return r


# PARCOURIR LES APPLICATIONS SANS QUITTER LE PANNEAU INSTALLE.
#
# Le panneau ajoute a l'ecran d'accueil est une app dont la portee est SON
# origine. Les applications vivent sur une autre (le port des applications,
# voir separer_les_origines) : y naviguer, c'est sortir de l'app, et iOS
# l'affiche dans son navigateur integre -- « Done » en haut, fleches en bas.
# Aucun manifeste ne couvre deux origines.
#
# L'application est donc ENCADREE dans une page du panneau : la fenetre reste
# sur l'origine du panneau, donc dans l'app, et l'application garde la
# sienne -- l'iframe ne lui donne aucun acces au panneau, la separation tient.
# Le hub n'y passe qu'en mode app (display-mode: standalone) ; dans Safari,
# une tuile ouvre toujours l'application directement.
#
# Le point dans le chemin n'est pas decoratif : un nom d'application ne peut
# pas en contenir (valid_name), cette adresse ne peut donc jamais masquer une
# application.
@flask_app.get("/ouvrir.html")
@require_auth
def ouvrir_dans_le_panneau():
    nom = request.args.get("app", "")
    apps = load()
    a = apps.get(nom)
    if not a or not peut_voir(nom) or a.get("externe"):
        return Response(_page("Introuvable", "Aucune application \u00ab " + nom + " \u00bb."),
                        404, mimetype="text/html")
    cible = adresse_application(nom)
    titre = titre_de(nom, a)

    def esc(x):
        return (str(x).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    # Un seul passage : une valeur remplacee n'est jamais relue, donc un
    # titre qui contiendrait « __CIBLE__ » reste du texte. Chaque valeur est
    # echappee pour SON contexte (texte, URL, attribut, script) ; dans le
    # script, « < » s'ecrit \u003c pour qu'aucune valeur ne ferme la balise.
    valeurs = {"TITRE": esc(titre),
               "ICONE": urllib.parse.quote(nom, safe=""),
               "CIBLE": esc(cible),
               "ORIGINE": json.dumps(origine_de_l_application(nom)).replace("<", "\\u003c")}
    page = re.sub(r"__(TITRE|ICONE|CIBLE|ORIGINE)__", lambda m: valeurs[m.group(1)], OUVRIR_PAGE)
    r = Response(page, mimetype="text/html")
    # La politique du panneau, plus le droit d'encadrer l'origine des
    # applications -- et elle seule.
    r.headers["Content-Security-Policy"] = CSP_PANNEAU + "; frame-src " + cadres_permis()
    r.headers["Cache-Control"] = "no-store"
    return r


@flask_app.get("/espace")
@require_auth
def espace():
    """Ancienne adresse de l'espace utilisateur.

    Conservee parce qu'elle a pu etre mise en favori : le hub vit maintenant
    dans la page principale, en tant que mode.
    """
    return redirect("/")


# ------------------------ les videos de presentation ------------------------
#
# Une application peut porter sa video de presentation : video/presentation.mp4
# dans son dossier, et son image d'affiche video/affiche.jpg -- ce que
# produit « codelab video » dans le poste de developpement. Le panneau ne
# fabrique rien et n'appelle aucun modele : il montre un fichier s'il existe.
#
# Memes droits que l'application : qui ne peut pas l'ouvrir ne voit pas sa
# video. L'administrateur peut la retirer du hub sans supprimer le fichier
# (« presentation_masquee »), et choisir une AUTRE video du projet -- celle
# d'un rendu Remotion, d'un montage fait ailleurs (« presentation_fichier »,
# un chemin relatif au projet). L'affiche ne va qu'avec la video par defaut.
PRESENTATION = {"video": ("video/presentation.mp4", "video/mp4"),
                "affiche": ("video/affiche.jpg", "image/jpeg")}
TYPES_VIDEO = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
# Ce qu'on ne fouille pas en cherchant les videos d'un projet : dependances,
# environnements, caches. Les dossiers caches non plus.
DOSSIERS_SANS_VIDEO = {"node_modules", "venv", "__pycache__", "vendor", "site-packages"}


def videos_du_projet(a, profondeur=6, limite=200):
    """Les videos du dossier du projet, en chemins relatifs tries.

    Sans suivre les liens symboliques : rien hors du projet n'est propose.
    """
    dossier = (a or {}).get("path") or ""
    if not dossier or not os.path.isdir(dossier):
        return []
    base = os.path.realpath(dossier)
    trouvees = []
    for racine, dossiers, fichiers in os.walk(base):
        niveau = os.path.relpath(racine, base).count(os.sep) + (racine != base)
        dossiers[:] = sorted(d for d in dossiers if not d.startswith(".")
                             and d not in DOSSIERS_SANS_VIDEO and niveau < profondeur)
        for f in sorted(fichiers):
            if os.path.splitext(f)[1].lower() in TYPES_VIDEO and not os.path.islink(os.path.join(racine, f)):
                trouvees.append(os.path.relpath(os.path.join(racine, f), base).replace(os.sep, "/"))
                if len(trouvees) >= limite:
                    return sorted(trouvees)
    return sorted(trouvees)


def fichier_presentation(a, quoi="video"):
    """Le chemin du fichier, s'il existe DANS le dossier du projet."""
    dossier = (a or {}).get("path") or ""
    if not dossier or quoi not in PRESENTATION:
        return None
    relatif = PRESENTATION[quoi][0]
    choisi = (a or {}).get("presentation_fichier")
    if choisi:
        if quoi == "affiche":
            return None
        relatif = choisi
    base = os.path.realpath(dossier)
    cible = os.path.realpath(os.path.join(base, relatif))
    # Un lien qui sortirait du projet ne mene a rien.
    if not cible.startswith(base + os.sep) or not os.path.isfile(cible):
        return None
    return cible


def presentation_de(a):
    """{maj, affiche} si la video existe, sinon None. maj sert a renouveler
    le cache du navigateur quand la video est regeneree."""
    video = fichier_presentation(a, "video")
    if not video:
        return None
    try:
        maj = int(os.path.getmtime(video))
    except OSError:
        return None
    return {"maj": maj, "affiche": bool(fichier_presentation(a, "affiche"))}


@flask_app.get("/api/presentations/<nom>/<quoi>")
@require_auth
def api_presentation(nom, quoi):
    """La video (ou l'affiche) d'une application, a qui peut l'ouvrir.

    conditional=True : le navigateur demande des morceaux (Range), et peut
    avancer dans la video sans tout telecharger.
    """
    a = load().get(nom)
    autorises = projets_autorises()
    if (quoi not in PRESENTATION or not a or not peut_voir(nom)
            or (autorises is not None and nom not in autorises)
            or (a.get("presentation_masquee") and not est_admin())):
        return jsonify({"error": "Présentation introuvable."}), 404
    chemin = fichier_presentation(a, quoi)
    if not chemin:
        return jsonify({"error": "Présentation introuvable."}), 404
    type_mime = (TYPES_VIDEO.get(os.path.splitext(chemin)[1].lower(), "video/mp4")
                 if quoi == "video" else PRESENTATION[quoi][1])
    reponse = send_file(chemin, mimetype=type_mime, conditional=True, max_age=0)
    reponse.headers["Cache-Control"] = "private, no-cache"
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    return reponse


@flask_app.put("/api/apps/<nom>/presentation")
@require_admin
def api_presentation_visibilite(nom):
    """Montrer ou retirer la video du hub ({visible}), sans toucher au
    fichier ; ou choisir la video a montrer ({fichier}, un chemin relatif
    parmi ceux de videos_du_projet ; vide : video/presentation.mp4)."""
    d = request.get_json(force=True, silent=True) or {}
    apps = load()
    if nom not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    if "fichier" in d:
        fichier = d.get("fichier") or ""
        if not isinstance(fichier, str) or (fichier and fichier not in videos_du_projet(apps[nom])):
            return jsonify({"error": "Cette vidéo n'est pas dans le dossier du projet."}), 400
        if fichier:
            apps[nom]["presentation_fichier"] = fichier
        else:
            apps[nom].pop("presentation_fichier", None)
    if "visible" in d:
        if d.get("visible"):
            apps[nom].pop("presentation_masquee", None)
        else:
            apps[nom]["presentation_masquee"] = True
    save(apps)
    return jsonify({"ok": True, "visible": not apps[nom].get("presentation_masquee"),
                    "fichier": apps[nom].get("presentation_fichier", "")})


@flask_app.get("/api/apps/<nom>/videos")
@require_admin
def api_videos_du_projet(nom):
    """Les videos qu'on peut choisir pour la presentation de l'application."""
    a = load().get(nom)
    if not a:
        return jsonify({"error": "Application inconnue."}), 404
    return jsonify({"fichiers": videos_du_projet(a), "choisi": a.get("presentation_fichier", ""),
                    "defaut": PRESENTATION["video"][0]})


@flask_app.get("/api/mes-apps")
@require_auth
def api_mes_apps():
    """Les projets ouvrables par la session, sans rien de plus.

    Ni chemin, ni commande, ni metriques : l'espace utilisateur n'a pas a
    connaitre l'organisation du serveur, et une reponse d'API est aussi
    publique que la page qui l'appelle.
    """
    autorises = projets_autorises()
    connues = lire_categories()
    # Ce que CE compte a range hors de son hub. La liste part avec la
    # reponse : la page des reglages doit pouvoir les rendre sans redemander,
    # et le hub doit savoir qu'il en cache -- une liste silencieusement plus
    # courte inquiete plus qu'elle ne simplifie.
    cachees = set(masquees_du_compte())
    liste, masquees = [], []
    toutes = load()
    versions = versions_des_apps(toutes)
    ouvertes = ouvertures_du_compte()
    admin = est_admin()
    for nom, a in sorted(toutes.items()):
        # Une application par defaut ne figure pas dans le hub d'un compte
        # utilisateur. Le proxy la lui refuse deja ; lui montrer une tuile qui
        # ne s'ouvre pas, ce serait promettre une porte fermee -- et il
        # chercherait ce qu'il a mal fait.
        if not peut_voir(nom):
            continue
        if autorises is not None and nom not in autorises:
            continue
        if nom in cachees:
            masquees.append({"name": nom, "titre": titre_de(nom, a),
                             "description": a.get("description") or ""})
            continue
        tuile = {
            "name": nom,
            "titre": titre_de(nom, a),
            "description": a.get("description") or "",
            "categorie": (CATEGORIE_CODELAB if est_app_par_defaut(nom)
                          else categorie_valide(a.get("categorie"), connues)),
            "visibility": visibilite(a),
            # Jamais ouverte par ce compte : pas d'indicateur. Sinon, une
            # version plus recente que celle vue a la derniere ouverture.
            "nouvelle_version": (nom in ouvertes
                                 and versions.get(nom, 0) > ouvertes[nom]["version"]),
            "ouverte_le": (ouvertes.get(nom) or {}).get("ts"),
            # La video de presentation, si le projet en a une et qu'elle
            # n'est pas retiree du hub.
            "presentation": (None if a.get("presentation_masquee") else presentation_de(a)),
        }
        # L'ETAT NE REGARDE QUE L'ADMINISTRATEUR. Un compte utilisateur ne
        # doit jamais savoir ce qui marche ou non : ni pastille, ni compteur,
        # ni aperçu qui trahirait une panne. La reponse ne porte donc pas
        # l'etat -- le cacher dans la page seulement le laisserait lisible
        # dans l'outil de developpement. Une application en panne, il la
        # decouvre en l'ouvrant, sur une page neutre (page_inaccessible).
        if admin:
            tuile["running"] = is_running(nom)
            tuile["listening"] = _listening.get(nom)
        liste.append(tuile)
    # Les categories accompagnent la liste : le hub les affiche dans l'ordre
    # voulu, sans avoir a deviner cet ordre a partir des projets.
    return jsonify({"apps": liste,
                    # A part, et pas melees aux applications : ce sont des
                    # services de la stack, que le panneau ne gere pas. La
                    # page les peint dans la meme grille, mais la liste des
                    # applications -- celle qu'on masque, celle qu'on cite
                    # dans un message -- reste ce qu'elle est.
                    "services": services_du_hub(),
                    # Les services RANGES partent a part des applications
                    # rangees : la page des reglages doit pouvoir les
                    # reproposer, et le choix « de quelle application
                    # veux-tu parler » ne doit toujours pas les voir.
                    "services_masques": services_masques(),
                    "masquees": masquees,
                    "categories": [CATEGORIE_CODELAB] + connues,
                    # Le widget Sauvegardes de l'administrateur : le resume,
                    # pas la liste (elle est dans Parametres > Securite).
                    "sauvegardes": resume_sauvegardes() if admin else None,
                    "utilisateur": utilisateur_courant(),
                    "role": role_courant()})


@flask_app.post("/api/messages")
@require_auth
def api_message():
    """Un mot laisse depuis le hub, range et envoye a l'administrateur.

    Trois sujets possibles, et pas un de plus : une application a laquelle on
    a acces, le hub lui-meme, ou l'idee d'une application qui manque. Un
    champ libre aurait demande a l'administrateur de deviner de quoi on
    parle -- ce qu'il ne peut pas faire depuis un mail de trois lignes.
    """
    d = request.get_json(force=True, silent=True) or {}
    cible = (d.get("cible") or "").strip()
    texte = (d.get("texte") or "").strip()
    nom_app = (d.get("app") or "").strip()
    qui = utilisateur_courant() or NOM_ADMIN

    if cible not in MESSAGE_CIBLES:
        return jsonify({"error": "Choisis de quoi tu veux parler."}), 400
    ids_pieces = d.get("pieces") or []
    if (not isinstance(ids_pieces, list) or len(ids_pieces) > PIECES_PAR_MESSAGE
            or not all(isinstance(x, str) for x in ids_pieces)):
        return jsonify({"error": f"{PIECES_PAR_MESSAGE} pièces jointes au plus."}), 400
    pieces = pieces_en_attente(qui, list(dict.fromkeys(ids_pieces)))
    if len(pieces) != len(set(ids_pieces)):
        return jsonify({"error": "Une pièce jointe a expiré : ajoute-la de nouveau."}), 400
    # Une maquette peut se passer de mots ; un message sans rien, non.
    if not texte and not pieces:
        return jsonify({"error": "Écris ton message."}), 400
    if len(texte) > MESSAGE_LONGUEUR_MAX:
        return jsonify({"error": "Message trop long : %d caractères pour %d au maximum."
                                 % (len(texte), MESSAGE_LONGUEUR_MAX)}), 400
    if cible == "application":
        # On ne parle que d'une application qu'on peut ouvrir : sinon ce
        # formulaire dirait l'existence de projets qu'on n'a pas le droit de
        # connaitre, exactement comme la liste des masquees.
        if nom_app not in load() or not peut_voir(nom_app):
            return jsonify({"error": "Application inconnue."}), 404
    else:
        nom_app = ""

    maintenant = time.time()
    if maintenant - _dernier_message.get(qui, 0) < MESSAGE_DELAI:
        return jsonify({"error": "Un message vient de partir. Laisse une minute "
                                 "avant le suivant."}), 429
    _dernier_message[qui] = maintenant

    message = {"id": secrets.token_hex(12), "ts": int(maintenant), "qui": qui,
               "cible": cible, "app": nom_app, "texte": texte, "lu": False,
               "statut": "en_attente", "archive": False}
    if pieces:
        message["pieces"] = pieces
    if not enregistrer_message(message):
        return jsonify({"error": "Message non enregistré : le disque n'a pas "
                                 "accepté l'écriture."}), 500

    # ENREGISTRE D'ABORD, ENVOYE ENSUITE. Un envoi qui echoue -- SMTP mal
    # configure, serveur injoignable -- ne doit pas faire perdre le message :
    # il est deja range, l'administrateur le verra dans le panneau.
    envoye, souci = False, ""
    cfg, ok = smtp_utilisable()
    destinataires = alertes_admin()
    if ok and destinataires:
        try:
            envoyer_mail(cfg, sujet_message(message), corps_message(message),
                         destinataires=destinataires)
            envoye = True
        except Exception as e:                                    # noqa: BLE001
            souci = f"{type(e).__name__}: {e}"
    extrait = texte.replace("\n", " ")[:140] or "Une pièce jointe."
    notifier_compte(NOM_ADMIN, f"Message de {qui}", extrait, url="/messages",
                    etiquette="message-" + message["id"])
    journaliser("message", qui=qui, app=nom_app or None,
                action=("envoye" if envoye else "enregistre sans mail"),
                ip=_adresse_client())
    return jsonify({"ok": True, "envoye": envoye, "erreur_mail": souci})


def sujet_message(message):
    ou = {"application": f"l'application « {message.get('app')} »",
          "hub": "le hub",
          "idee": "une idée d'application"}.get(message.get("cible"), "CodeLab")
    return f"[CodeLab] message de {message.get('qui')} sur {ou}"


def corps_message(message):
    quand = datetime.datetime.fromtimestamp(message.get("ts") or 0).strftime(
        "%Y-%m-%d %H:%M")
    sujet = {"application": f"Application : {message.get('app')}",
             "hub": "Sujet      : le hub",
             "idee": "Sujet      : une idee d'application"}.get(
                 message.get("cible"), "Sujet      : ?")
    pieces = message.get("pieces") or []
    jointes = ([f"Joint      : {', '.join(p['nom'] for p in pieces)} (a voir dans Messages)"]
               if pieces else [])
    return "\n".join([
        f"De         : {message.get('qui')}",
        sujet,
        f"Le         : {quand}",
        *jointes,
        "",
        (message.get("texte") or "").strip(),
        "",
        "-- CodeLab, message laisse depuis le hub",
    ])


@flask_app.post("/api/pieces")
@require_auth
def api_piece_deposer():
    """Depose une piece jointe : le corps EST le fichier, son nom dans X-Nom-Fichier.

    Rend son identifiant, que le message nommera a l'envoi.
    """
    donnees = request.get_data(cache=False)
    if not donnees:
        return jsonify({"error": "Fichier vide."}), 400
    if len(donnees) > PIECE_MAX:
        return _trop_gros(PIECE_MAX)
    type_, ext = type_de_piece(donnees)
    if not type_:
        return jsonify({"error": "Une image (PNG, JPEG, WebP, GIF) ou un PDF seulement."}), 415
    qui = utilisateur_courant() or NOM_ADMIN
    piece = {"id": secrets.token_hex(12), "nom": nom_de_piece(request.headers.get("X-Nom-Fichier"), ext),
             "type": type_, "taille": len(donnees)}
    with stockage.transaction(DB_FILE) as cx:
        _oublier_pieces_orphelines(cx)
        n = cx.execute("SELECT COUNT(*) FROM pieces WHERE message IS NULL AND qui = ?",
                       (qui,)).fetchone()[0]
        if n >= PIECES_EN_ATTENTE_MAX:
            return jsonify({"error": "Trop de pièces en attente : envoie ton message d'abord."}), 429
        cx.execute("INSERT INTO pieces (id, message, qui, nom, type, taille, ts, donnees) "
                   "VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
                   (piece["id"], qui, piece["nom"], type_, len(donnees), int(time.time()),
                    donnees))
    return jsonify(piece)


@flask_app.get("/api/pieces/<ident>")
@require_auth
def api_piece_lire(ident):
    """Une piece jointe : a l'administrateur, ou a qui l'a deposee."""
    lignes = stockage.lire(DB_FILE, "SELECT * FROM pieces WHERE id = ?", (ident,))
    if not lignes or (not est_admin() and lignes[0]["qui"] != utilisateur_courant()):
        return jsonify({"error": "Pièce introuvable."}), 404
    p = lignes[0]
    reponse = Response(bytes(p["donnees"]), mimetype=p["type"])
    reponse.headers["Content-Disposition"] = (
        "inline; filename*=UTF-8''" + urllib.parse.quote(p["nom"]))
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Cache-Control"] = "private, max-age=86400"
    if p["type"].startswith("image/"):
        # Une image n'execute rien ; on le dit quand meme au navigateur.
        reponse.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    return reponse


@flask_app.get("/api/messages")
@require_admin
def api_messages_liste():
    """Les messages recus. Reserve a l'administrateur : ce sont des mots qui
    lui sont adresses, et ils portent le nom de qui les a ecrits.

    « apres » rend ce qui est arrive DEPUIS une date : c'est ce que demande
    la page toutes les vingt secondes pour annoncer un message nouveau. Sans
    lui, elle retelechargerait trois cents messages pour en decouvrir un.
    """
    try:
        apres = int(request.args.get("apres") or 0)
    except ValueError:
        apres = 0
    messages = lire_messages(limite=MESSAGES_AFFICHES)
    if apres:
        messages = [m for m in messages if (m.get("ts") or 0) > apres]
    # Les messages retenus d'abord : on les retient justement pour ne pas
    # avoir a les rechercher.
    messages.sort(key=lambda m: (not m.get("retenu"), -(m.get("ts") or 0)))
    return jsonify({"messages": messages,
                     "non_lus": sum(1 for m in messages if not m.get("lu")),
                     "boite": sum(1 for m in lire_messages(limite=MESSAGES_AFFICHES)
                                   if not m.get("archive"))})


@flask_app.post("/api/messages/lus")
@require_admin
def api_messages_lus():
    changes = marquer_messages_lus()
    return jsonify({"ok": True, "lus": changes})


@flask_app.put("/api/messages/<identifiant>")
@require_admin
def api_message_modifier(identifiant):
    """Change l'etat d'un message : retenu, statut, archive.

    Seules les cles PRESENTES changent. La route ne lisait que « retenu » :
    accepter, refuser ou archiver envoyaient leur etat, qui etait ignore --
    et l'epingle tombait au passage, faute de cle.
    """
    d = request.get_json(force=True, silent=True) or {}
    statut = d.get("statut")
    if statut is not None and statut not in STATUTS_MESSAGE:
        return jsonify({"error": "Statut inconnu."}), 400
    projet = _INCHANGE
    if "projet" in d:
        projet = d.get("projet") or None
        if projet and (projet == PROJET_GENERAL or not _projet_valide(projet)):
            return jsonify({"error": "Projet inconnu."}), 400
    if not modifier_message(identifiant,
                            retenu=bool(d["retenu"]) if "retenu" in d else None,
                            statut=statut,
                            archive=bool(d["archive"]) if "archive" in d else None,
                            projet=projet):
        return jsonify({"error": "Message introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.put("/api/messages/ordre")
@require_admin
def api_messages_ordre():
    """Ranger la boite a sa guise : {ordre: [ids]}, du haut vers le bas.

    Les messages nommes prennent ce rang ; un message qui arrive ensuite,
    sans rang, se place en tete jusqu'a ce qu'on le range.
    """
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > MESSAGES_MAX):
        return jsonify({"error": "Ordre invalide."}), 400
    if modifier_messages({mid: {"ordre": rang} for rang, mid in enumerate(ordre)}) != len(ordre):
        return jsonify({"error": "La liste nomme un message inconnu."}), 409
    return jsonify({"ok": True})


@flask_app.post("/api/messages/grouper")
@require_admin
def api_messages_grouper():
    """Regroupe des messages dans un projet : {ids, projet} ou {ids, nouveau_projet}.

    Des messages qui parlent de la meme chose forment un projet : on les y
    range ensemble, dans un projet qui existe ou qu'on cree a l'instant.
    """
    d = request.get_json(force=True, silent=True) or {}
    ids = d.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        return jsonify({"error": "Choisis au moins un message."}), 400
    connus = {m.get("id") for m in lire_messages(limite=100000)}
    if not set(ids) <= connus:
        return jsonify({"error": "Message introuvable."}), 404
    nouveau = d.get("nouveau_projet")
    try:
        if isinstance(nouveau, dict):
            nom = _titre_tache(nouveau.get("nom"))[:PROJET_NOM_MAX]
            if not nom:
                return jsonify({"error": "Donne un nom au projet."}), 400
            projet = creer_projet(nom, _texte_long(nouveau.get("description"),
                                                  PROJET_DESCRIPTION_MAX), message=ids[0])
        else:
            projet = d.get("projet")
            if not projet or projet == PROJET_GENERAL or not _projet_valide(projet):
                return jsonify({"error": "Projet inconnu."}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    modifier_messages({mid: {"projet": projet, "lu": True} for mid in ids})
    return jsonify({"ok": True, "projet": projet})


@flask_app.put("/api/messages/<identifiant>/lu")
@require_admin
def api_message_lu(identifiant):
    if not modifier_message(identifiant, lu=True):
        return jsonify({"error": "Message introuvable."}), 404
    _pg_deposer(("messages_lus",))
    return jsonify({"ok": True})


@flask_app.delete("/api/messages/<identifiant>")
@require_admin
def api_message_supprimer(identifiant):
    """Supprime un message, du fichier comme de la base."""
    if not modifier_message(identifiant, supprimer=True):
        return jsonify({"error": "Message introuvable."}), 404
    journaliser("message", qui=utilisateur_courant() or NOM_ADMIN,
                action="supprime", ip=_adresse_client())
    return jsonify({"ok": True})


# ------------------------------- les taches -------------------------------
#
# Un message accepte devient une tache, rangee dans un projet : une
# application declaree, ou « general » pour ce qui ne touche aucune d'elles.
# Une tache a des sous-taches qu'on coche une a une. Reserve a
# l'administrateur, comme les messages.
TACHE_TITRE_MAX = 200
SOUS_TACHES_MAX = 30
TACHES_MAX = 2000
PROJET_GENERAL = "general"
# L'application « Demandes » : un gestionnaire de taches. Une tache a un
# STATUT (sa colonne sur le tableau), une PRIORITE, et un ORDRE dans sa
# colonne, que le glisser-deposer fixe. « faite » reste tenue en accord avec
# le statut, pour les lectures anciennes.
STATUTS_TACHE = ("a_faire", "en_cours", "fait")
PRIORITES = (0, 1, 2, 3)              # urgente, haute, normale, basse
PRIORITE_NORMALE = 2
NOTES_MAX = 4000
# Un projet se CONSTRUIT puis se CONCRETISE : idee -> construction -> realise.
ETAPES_PROJET = ("idee", "construction", "realise")
PROJETS_MAX = 500
PROJET_NOM_MAX = 120
PROJET_DESCRIPTION_MAX = 4000
PREFIXE_PROJET = "p-"


def _titre_tache(brut):
    return " ".join(str(brut or "").split())[:TACHE_TITRE_MAX]


def _texte_long(brut, maximum):
    return str(brut or "").strip()[:maximum]


def _echeance_valide(brut):
    """Une date AAAA-MM-JJ, ou None. Rend False si elle est mal formee."""
    if brut in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(brut)).isoformat()
    except ValueError:
        return False


DUREE_MAX = 60                        # jours ouvres


def _duree_valide(brut):
    try:
        d = int(brut)
    except (TypeError, ValueError):
        return None
    return d if 1 <= d <= DUREE_MAX else None


def _priorite_valide(brut):
    try:
        p = int(brut)
    except (TypeError, ValueError):
        return None
    return p if p in PRIORITES else None


def _projet_valide(projet):
    """« general », une application declaree, ou un projet existant."""
    projet = str(projet or "").strip()
    if projet == PROJET_GENERAL:
        return projet
    if projet.startswith(PREFIXE_PROJET):
        try:
            existe = stockage.lire(DB_FILE, "SELECT 1 FROM projets WHERE id = ?", (projet,))
        except Exception:                                         # noqa: BLE001
            existe = []
        return projet if existe else None
    return projet if projet in load() else None


def lire_taches():
    """Les taches et leurs sous-taches, dans l'ordre du planning."""
    try:
        taches = stockage.lire(DB_FILE, "SELECT * FROM taches ORDER BY ordre, cree, rowid")
        sous = stockage.lire(DB_FILE, "SELECT * FROM sous_taches ORDER BY ordre, rowid")
    except Exception:                                             # noqa: BLE001
        return []
    par_tache = {}
    for st in sous:
        par_tache.setdefault(st["tache"], []).append(
            {"id": st["id"], "titre": st["titre"], "faite": bool(st["faite"])})
    return [{"id": t["id"], "projet": t["projet"], "titre": t["titre"],
             "message": t["message"], "cree": t["cree"], "faite": bool(t["faite"]),
             "statut": t["statut"], "priorite": t["priorite"], "echeance": t["echeance"],
             "notes": t["notes"], "ordre": t["ordre"], "duree": t["duree"],
             "fait_le": t["fait_le"],
             "sous_taches": par_tache.get(t["id"], [])} for t in taches]


def creer_tache(projet, titre, sous_taches=(), message=None, priorite=PRIORITE_NORMALE,
                statut="a_faire", echeance=None, notes="", duree=1, cx=None):
    """Cree la tache et ses sous-taches. Rend son id.

    En fin de planning : une tache neuve ne passe pas devant ce qu'on a
    deja ordonne a la main. Dans la transaction donnee, ou dans la sienne.
    """
    if cx is None:
        with stockage.transaction(DB_FILE) as cx:
            return creer_tache(projet, titre, sous_taches, message, priorite,
                               statut, echeance, notes, duree, cx=cx)
    ident = secrets.token_hex(8)
    nombre = cx.execute("SELECT COUNT(*) FROM taches").fetchone()[0]
    if nombre >= TACHES_MAX:
        raise ValueError("Trop de tâches : termine ou supprime les anciennes.")
    fin = cx.execute("SELECT COALESCE(MAX(ordre), -1) + 1 FROM taches").fetchone()[0]
    maintenant = int(time.time())
    cx.execute("INSERT INTO taches (id, projet, titre, message, cree, faite, ordre, statut,"
               " priorite, echeance, notes, duree, fait_le)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (ident, projet, titre, message, maintenant, 1 if statut == "fait" else 0,
                fin, statut, priorite, echeance, notes, duree,
                maintenant if statut == "fait" else None))
    for ordre, st in enumerate(sous_taches):
        cx.execute("INSERT INTO sous_taches (id, tache, titre, ordre) VALUES (?, ?, ?, ?)",
                   (secrets.token_hex(8), ident, st, ordre))
    return ident


def _sous_taches_propres(liste):
    propres = [_titre_tache(x) for x in (liste or []) if _titre_tache(x)]
    return propres[:SOUS_TACHES_MAX]


def lire_projets():
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM projets ORDER BY ordre, cree, rowid")
    except Exception:                                             # noqa: BLE001
        return []
    return [{"id": p["id"], "nom": p["nom"], "description": p["description"],
             "etape": p["etape"], "application": p["application"], "message": p["message"],
             "cree": p["cree"], "maj": p["maj"], "ordre": p["ordre"]} for p in lignes]


def creer_projet(nom, description="", etape="idee", message=None, taches=(), cx=None):
    """Cree un projet, et ses premieres taches (une par titre). Rend son id."""
    if cx is None:
        with stockage.transaction(DB_FILE) as cx:
            return creer_projet(nom, description, etape, message, taches, cx=cx)
    if cx.execute("SELECT COUNT(*) FROM projets").fetchone()[0] >= PROJETS_MAX:
        raise ValueError(f"{PROJETS_MAX} projets au plus : archive ou supprime les anciens.")
    ident = PREFIXE_PROJET + secrets.token_hex(6)
    maintenant = int(time.time())
    fin = cx.execute("SELECT COALESCE(MAX(ordre), -1) + 1 FROM projets").fetchone()[0]
    cx.execute("INSERT INTO projets (id, nom, description, etape, message, cree, maj, ordre)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (ident, nom, description, etape, message, maintenant, maintenant, fin))
    for titre in taches:
        creer_tache(ident, titre, message=message, cx=cx)
    return ident


@flask_app.post("/api/messages/<identifiant>/accepter")
@require_admin
def api_message_accepter(identifiant):
    """Accepte une demande, et en fait une tache, un projet, ou rien.

    - « nouveau_projet » {nom, description} : la demande devient un PROJET a
      construire ; les lignes de « taches » en sont les premieres taches ;
    - « titre » (+ projet, priorite, sous_taches) : elle devient une TACHE ;
    - rien de tout cela : elle est acceptee sans suite.
    """
    d = request.get_json(force=True, silent=True) or {}
    if not any(m.get("id") == identifiant for m in lire_messages(limite=100000)):
        return jsonify({"error": "Message introuvable."}), 404
    tache = projet = None
    nouveau = d.get("nouveau_projet")
    titre = _titre_tache(d.get("titre"))
    try:
        if isinstance(nouveau, dict):
            nom = _titre_tache(nouveau.get("nom"))[:PROJET_NOM_MAX]
            if not nom:
                return jsonify({"error": "Donne un nom au projet."}), 400
            projet = creer_projet(nom, _texte_long(nouveau.get("description"), PROJET_DESCRIPTION_MAX),
                                  message=identifiant,
                                  taches=_sous_taches_propres(d.get("taches")))
        elif titre:
            cible = _projet_valide(d.get("projet"))
            if not cible:
                return jsonify({"error": "Projet inconnu."}), 400
            priorite = _priorite_valide(d.get("priorite", PRIORITE_NORMALE))
            if priorite is None:
                return jsonify({"error": "Priorité inconnue."}), 400
            tache = creer_tache(cible, titre, _sous_taches_propres(d.get("sous_taches")),
                                message=identifiant, priorite=priorite)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    modifier_message(identifiant, statut="accepte", lu=True)
    return jsonify({"ok": True, "tache": tache, "projet": projet})


# ------------------------- le suivi, cote utilisateur -------------------------
#
# Qui a ecrit veut savoir ce que son message est devenu, sans demander. Le
# suivi se DEDUIT de ce que l'administrateur fait deja dans Messages : lire,
# mettre a l'etude, accepter, refuser, en faire des taches, les cocher. Rien
# a remplir de plus de son cote.
#
# Un compte ne voit que SES messages, et de leurs taches seulement le titre
# et si elle est faite : ni les notes, ni le reste du planning.


def suivi_message(m, taches, projets):
    """L'etape d'un message, et ce qui en est ne."""
    lies = [t for t in taches if t["message"] == m.get("id")]
    projet = None
    for p in projets:
        if p["message"] == m.get("id") or p["id"] == m.get("projet"):
            projet = p
            break
    if projet:
        deja = {t["id"] for t in lies}
        lies += [t for t in taches if t["projet"] == projet["id"] and t["id"] not in deja]
    faites = [t for t in lies if t["statut"] == "fait"]
    ouvertes = [t for t in lies if t["statut"] != "fait"]
    statut = m.get("statut") or "en_attente"
    if statut == "refuse":
        etape = "refuse"
    elif (lies and not ouvertes) or (projet and projet["etape"] == "realise"):
        etape = "fait"
    elif faites or any(t["statut"] == "en_cours" for t in lies) or (
            projet and projet["etape"] == "construction"):
        etape = "en_cours"
    elif lies or projet or statut == "accepte":
        etape = "planifie"
    elif statut == "a_reflechir":
        etape = "etude"
    elif m.get("lu"):
        etape = "lu"
    else:
        etape = "envoye"
    echeances = sorted(t["echeance"] for t in ouvertes if t["echeance"])
    fins = [t["fait_le"] for t in faites if t["fait_le"]]
    return {"etape": etape,
            "taches": [{"titre": t["titre"], "faite": t["statut"] == "fait"} for t in lies],
            "faites": len(faites), "total": len(lies),
            "prevu": echeances[-1] if echeances and etape != "fait" else None,
            "fait_le": max(fins) if fins and etape == "fait" else None,
            "projet": projet["nom"] if projet else None}


def mes_messages():
    qui = utilisateur_courant() or NOM_ADMIN
    taches, projets = lire_taches(), lire_projets()
    vus = lire_preference("suivi_vu", qui) or {}
    if not isinstance(vus, dict):
        vus = {}
    rendus = []
    for m in lire_messages(limite=MESSAGES_AFFICHES):
        if m.get("qui") != qui:
            continue
        suivi = suivi_message(m, taches, projets)
        # Nouveau : l'etape a change depuis la derniere visite. Un message
        # qu'on vient d'envoyer n'a rien de neuf a annoncer.
        vu = vus.get(m.get("id"), "envoye")
        rendus.append({"id": m.get("id"), "ts": m.get("ts"), "cible": m.get("cible"),
                       "app": m.get("app") or "", "texte": m.get("texte") or "",
                       "pieces": m.get("pieces") or [],
                       "suivi": suivi, "nouveau": suivi["etape"] != vu})
    return rendus


@flask_app.get("/api/mes-messages")
@require_auth
def api_mes_messages():
    """Les messages de ce compte, chacun avec son avancement."""
    rendus = mes_messages()
    return jsonify({"messages": rendus, "nouveaux": sum(1 for m in rendus if m["nouveau"])})


@flask_app.post("/api/mes-messages/vus")
@require_auth
def api_mes_messages_vus():
    """Retient l'etape vue de chaque message : les pastilles s'eteignent."""
    ecrire_preference("suivi_vu", {m["id"]: m["suivi"]["etape"] for m in mes_messages()})
    return jsonify({"ok": True})


@flask_app.get("/api/taches")
@require_admin
def api_taches():
    return jsonify({"taches": lire_taches()})


@flask_app.post("/api/taches")
@require_admin
def api_tache_creer():
    d = request.get_json(force=True, silent=True) or {}
    titre = _titre_tache(d.get("titre"))
    projet = _projet_valide(d.get("projet"))
    if not titre:
        return jsonify({"error": "Le titre est obligatoire."}), 400
    if not projet:
        return jsonify({"error": "Projet inconnu."}), 400
    priorite = _priorite_valide(d.get("priorite", PRIORITE_NORMALE))
    statut = d.get("statut") or "a_faire"
    echeance = _echeance_valide(d.get("echeance"))
    if priorite is None:
        return jsonify({"error": "Priorité inconnue."}), 400
    if statut not in STATUTS_TACHE:
        return jsonify({"error": "Statut inconnu."}), 400
    if echeance is False:
        return jsonify({"error": "Échéance invalide (AAAA-MM-JJ)."}), 400
    duree = _duree_valide(d.get("duree", 1))
    if duree is None:
        return jsonify({"error": f"Durée invalide : de 1 à {DUREE_MAX} jours."}), 400
    # Une tache peut naitre d'un message : elle le garde en reference.
    message = d.get("message") or None
    if message and not any(m.get("id") == message for m in lire_messages(limite=100000)):
        return jsonify({"error": "Message introuvable."}), 404
    try:
        ident = creer_tache(projet, titre, _sous_taches_propres(d.get("sous_taches")),
                            message=message, priorite=priorite, statut=statut,
                            echeance=echeance, notes=_texte_long(d.get("notes"), NOTES_MAX),
                            duree=duree)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": ident})


@flask_app.put("/api/taches/<ident>")
@require_admin
def api_tache_modifier(ident):
    """Seuls les champs PRESENTS changent. Changer de statut tient « faite »
    et « fait_le » en accord ; la tache garde sa place dans le planning."""
    d = request.get_json(force=True, silent=True) or {}
    champs, valeurs = [], []
    if "titre" in d:
        titre = _titre_tache(d.get("titre"))
        if not titre:
            return jsonify({"error": "Le titre est obligatoire."}), 400
        champs.append("titre = ?"); valeurs.append(titre)
    statut = None
    if "statut" in d:
        statut = d.get("statut")
        if statut not in STATUTS_TACHE:
            return jsonify({"error": "Statut inconnu."}), 400
    elif "faite" in d:
        statut = "fait" if d.get("faite") else "a_faire"
    if "projet" in d:
        projet = _projet_valide(d.get("projet"))
        if not projet:
            return jsonify({"error": "Projet inconnu."}), 400
        champs.append("projet = ?"); valeurs.append(projet)
    if "priorite" in d:
        priorite = _priorite_valide(d.get("priorite"))
        if priorite is None:
            return jsonify({"error": "Priorité inconnue."}), 400
        champs.append("priorite = ?"); valeurs.append(priorite)
    if "echeance" in d:
        echeance = _echeance_valide(d.get("echeance"))
        if echeance is False:
            return jsonify({"error": "Échéance invalide (AAAA-MM-JJ)."}), 400
        champs.append("echeance = ?"); valeurs.append(echeance)
    if "notes" in d:
        champs.append("notes = ?"); valeurs.append(_texte_long(d.get("notes"), NOTES_MAX))
    if "duree" in d:
        duree = _duree_valide(d.get("duree"))
        if duree is None:
            return jsonify({"error": f"Durée invalide : de 1 à {DUREE_MAX} jours."}), 400
        champs.append("duree = ?"); valeurs.append(duree)
    if not champs and statut is None:
        return jsonify({"error": "Rien à modifier."}), 400
    with stockage.transaction(DB_FILE) as cx:
        ligne = cx.execute("SELECT statut FROM taches WHERE id = ?", (ident,)).fetchone()
        if not ligne:
            return jsonify({"error": "Tâche introuvable."}), 404
        if statut is not None and statut != ligne["statut"]:
            # La tache garde sa place dans le planning ; cochee, elle date
            # sa fin -- la feuille de route l'y affiche.
            champs += ["statut = ?", "faite = ?", "fait_le = ?"]
            valeurs += [statut, 1 if statut == "fait" else 0,
                        int(time.time()) if statut == "fait" else None]
        if champs:
            cx.execute(f"UPDATE taches SET {', '.join(champs)} WHERE id = ?", (*valeurs, ident))
    return jsonify({"ok": True})


@flask_app.put("/api/taches/ordre")
@require_admin
def api_taches_ordre():
    """Une colonne du tableau, apres un glisser-deposer : {statut, ordre}.

    « ordre » nomme les taches de la colonne de haut en bas -- la plus haute
    est la plus prioritaire. Une tache venue d'une autre colonne y change de
    statut. Chaque id doit exister ; la liste ne peut pas en inventer.
    """
    d = request.get_json(force=True, silent=True) or {}
    statut, ordre = d.get("statut"), d.get("ordre")
    if statut not in STATUTS_TACHE:
        return jsonify({"error": "Statut inconnu."}), 400
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > TACHES_MAX):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        connues = {r[0] for r in cx.execute("SELECT id FROM taches")}
        if not set(ordre) <= connues:
            return jsonify({"error": "La liste nomme une tâche inconnue."}), 409
        for rang, tid in enumerate(ordre):
            cx.execute("UPDATE taches SET statut = ?, faite = ?, ordre = ?, "
                       "fait_le = CASE WHEN ? = 'fait' THEN COALESCE(fait_le, ?) ELSE NULL END "
                       "WHERE id = ?",
                       (statut, 1 if statut == "fait" else 0, rang, statut, int(time.time()), tid))
    return jsonify({"ok": True})


@flask_app.put("/api/taches/plan")
@require_admin
def api_taches_plan():
    """L'ORDRE DU PLANNING : {ordre: [ids]}, la premiere tache d'abord.

    C'est ainsi qu'on priorise -- par la place, pas par une etiquette
    d'urgence. Les taches sans date se suivent sur la feuille de route dans
    cet ordre. Le statut n'est pas touche. Chaque id doit exister.
    """
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > TACHES_MAX):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        connues = {r[0] for r in cx.execute("SELECT id FROM taches")}
        if not set(ordre) <= connues:
            return jsonify({"error": "La liste nomme une tâche inconnue."}), 409
        for rang, tid in enumerate(ordre):
            cx.execute("UPDATE taches SET ordre = ? WHERE id = ?", (rang, tid))
    return jsonify({"ok": True})


@flask_app.post("/api/taches/<ident>/scinder")
@require_admin
def api_tache_scinder(ident):
    """Scinde une tache : {titres: [...]}, deux au moins.

    La premiere ligne devient le titre de la tache ; les suivantes sont de
    nouvelles taches du meme projet, nees du meme message, placees juste
    apres elle dans le planning. La duree se partage entre elles.
    """
    titres = [_titre_tache(t) for t in ((request.get_json(force=True, silent=True) or {})
                                         .get("titres") or []) if _titre_tache(t)]
    if len(titres) < 2:
        return jsonify({"error": "Donne au moins deux titres pour scinder."}), 400
    if len(titres) > SOUS_TACHES_MAX:
        return jsonify({"error": f"{SOUS_TACHES_MAX} morceaux au plus."}), 400
    with stockage.transaction(DB_FILE) as cx:
        t = cx.execute("SELECT * FROM taches WHERE id = ?", (ident,)).fetchone()
        if not t:
            return jsonify({"error": "Tâche introuvable."}), 404
        if cx.execute("SELECT COUNT(*) FROM taches").fetchone()[0] + len(titres) - 1 > TACHES_MAX:
            return jsonify({"error": "Trop de tâches : termine ou supprime les anciennes."}), 400
        part = max(1, round((t["duree"] or 1) / len(titres)))
        cx.execute("UPDATE taches SET ordre = ordre + ? WHERE ordre > ?",
                   (len(titres) - 1, t["ordre"]))
        cx.execute("UPDATE taches SET titre = ?, duree = ? WHERE id = ?", (titres[0], part, ident))
        nouvelles = []
        for i, titre in enumerate(titres[1:], start=1):
            nid = secrets.token_hex(8)
            cx.execute("INSERT INTO taches (id, projet, titre, message, cree, faite, ordre, statut,"
                       " priorite, echeance, notes, duree) VALUES (?, ?, ?, ?, ?, 0, ?, 'a_faire',"
                       " ?, NULL, '', ?)",
                       (nid, t["projet"], titre, t["message"], int(time.time()),
                        t["ordre"] + i, t["priorite"], part))
            nouvelles.append(nid)
    return jsonify({"ok": True, "taches": [ident] + nouvelles})


@flask_app.delete("/api/taches/<ident>")
@require_admin
def api_tache_supprimer(ident):
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute("DELETE FROM taches WHERE id = ?", (ident,)).rowcount
    if not n:
        return jsonify({"error": "Tâche introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.get("/api/projets")
@require_admin
def api_projets():
    return jsonify({"projets": lire_projets()})


# ------------------------- exporter un projet -------------------------
#
# TOUT LE PROJET, D'UN COUP : sa description, ses taches dans l'ordre du
# planning, les messages qui l'ont fait naitre et leurs pieces jointes. Un
# fichier ZIP, que le systeme ouvre tout seul : un « projet.md » a lire (ou a
# donner tel quel a qui va le construire), un « projet.json » pour un
# programme, et les pieces dans « pieces/ ».
ETAPES_NOMS = {"idee": "Idée", "construction": "En construction", "realise": "Réalisé"}
STATUTS_NOMS = {"a_faire": "À faire", "en_cours": "En cours", "fait": "Fait"}
PRIORITES_NOMS = {0: "urgente", 1: "haute", 2: "normale", 3: "basse"}
SUJETS_NOMS = {"hub": "Améliorer le hub", "idee": "Nouveau projet", "application": "Application"}


def _slug(texte):
    t = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:60] or "projet"


def _cles_de_projets(taches, projets):
    """Toutes les cles qui portent du travail : projets, applications, general."""
    cles = [p["id"] for p in projets]
    for t in taches:
        if t["projet"] not in cles:
            cles.append(t["projet"])
    return cles


def _contenu_projet(cle, taches, projets, messages):
    projet = next((p for p in projets if p["id"] == cle), None)
    if projet:
        nom = projet["nom"]
    elif cle == PROJET_GENERAL:
        nom = "Général"
    else:
        nom = (load().get(cle) or {}).get("titre") or cle
    siennes = [t for t in taches if t["projet"] == cle]
    lies = {t["message"] for t in siennes if t["message"]}
    if projet and projet["message"]:
        lies.add(projet["message"])
    msgs = sorted((m for m in messages if m.get("projet") == cle or m.get("id") in lies),
                  key=lambda m: m.get("ts") or 0)
    return {"cle": cle, "nom": nom, "projet": projet, "taches": siennes, "messages": msgs}


def _date(ts):
    return datetime.datetime.fromtimestamp(ts or 0).strftime("%d/%m/%Y")


def _markdown_projet(c, chemins):
    p = c["projet"] or {}
    ouvertes = [t for t in c["taches"] if t["statut"] != "fait"]
    faites = [t for t in c["taches"] if t["statut"] == "fait"]
    lignes = [f"# {c['nom']}", ""]
    meta = []
    if p.get("etape"):
        meta.append("Étape : " + ETAPES_NOMS.get(p["etape"], p["etape"]))
    if p.get("application"):
        meta.append("Application : " + p["application"])
    meta.append(f"{len(faites)}/{len(c['taches'])} tâches faites")
    meta.append("Exporté le " + datetime.datetime.now().strftime("%d/%m/%Y à %H:%M"))
    lignes += [" · ".join(meta), ""]
    if p.get("description"):
        lignes += ["## Description", "", p["description"].strip(), ""]

    def tache(t):
        coche = "x" if t["statut"] == "fait" else " "
        details = [STATUTS_NOMS.get(t["statut"], t["statut"]),
                   "priorité " + PRIORITES_NOMS.get(t["priorite"], str(t["priorite"]))]
        if t["echeance"]:
            details.append("pour le " + t["echeance"])
        if t["duree"] and t["duree"] != 1:
            details.append(f"{t['duree']} jours")
        if t["fait_le"]:
            details.append("fait le " + _date(t["fait_le"]))
        out = [f"- [{coche}] **{t['titre']}** — {', '.join(details)}"]
        out += [f"  - [{'x' if st['faite'] else ' '}] {st['titre']}" for st in t["sous_taches"]]
        if t["notes"].strip():
            out += ["", *("  > " + l for l in t["notes"].strip().splitlines()), ""]
        return out

    if ouvertes:
        lignes += ["## Tâches, dans l'ordre du planning", ""]
        for t in ouvertes:
            lignes += tache(t)
        lignes.append("")
    if faites:
        lignes += ["## Tâches faites", ""]
        for t in faites:
            lignes += tache(t)
        lignes.append("")
    if c["messages"]:
        lignes += [f"## Messages ({len(c['messages'])})", ""]
        for m in c["messages"]:
            sujet = SUJETS_NOMS.get(m.get("cible"), "")
            if m.get("cible") == "application" and m.get("app"):
                sujet += " · " + m["app"]
            lignes += [f"### {m.get('qui') or '?'} — {_date(m.get('ts'))} — {sujet}", ""]
            if (m.get("texte") or "").strip():
                lignes += [m["texte"].strip(), ""]
            for piece in m.get("pieces") or []:
                chemin = chemins.get(piece["id"])
                if not chemin:
                    continue
                lien = urllib.parse.quote(chemin)
                lignes += [f"![{piece['nom']}]({lien})" if piece["type"].startswith("image/")
                           else f"[{piece['nom']}]({lien})", ""]
    return "\n".join(lignes).rstrip() + "\n"


def _ecrire_projet(z, dossier, c):
    """Ecrit un projet dans l'archive, sous « dossier » (vide : a la racine)."""
    chemins, pris = {}, set()
    for m in c["messages"]:
        for piece in m.get("pieces") or []:
            lignes = stockage.lire(DB_FILE, "SELECT donnees FROM pieces WHERE id = ?", (piece["id"],))
            if not lignes:
                continue
            base = datetime.datetime.fromtimestamp(m.get("ts") or 0).strftime("%Y-%m-%d") + "-" + piece["nom"]
            nom, k = base, 2
            while nom in pris:
                racine, _, ext = base.rpartition(".")
                nom, k = f"{racine}-{k}.{ext}", k + 1
            pris.add(nom)
            chemins[piece["id"]] = "pieces/" + nom
            z.writestr(dossier + "pieces/" + nom, bytes(lignes[0]["donnees"]))
    z.writestr(dossier + "projet.md", _markdown_projet(c, chemins))
    donnees = {"nom": c["nom"], "cle": c["cle"], "projet": c["projet"], "taches": c["taches"],
               "messages": [dict(m, pieces=[dict(p, fichier=chemins.get(p["id"]))
                                            for p in m.get("pieces") or []])
                            for m in c["messages"]]}
    z.writestr(dossier + "projet.json", json.dumps(donnees, ensure_ascii=False, indent=2))


def _envoyer_zip(tampon, nom):
    tampon.seek(0)
    return send_file(tampon, mimetype="application/zip", as_attachment=True, download_name=nom)


@flask_app.get("/api/projets/<cle>/export")
@require_admin
def api_projet_export(cle):
    taches, projets = lire_taches(), lire_projets()
    if cle not in _cles_de_projets(taches, projets) and cle != PROJET_GENERAL:
        return jsonify({"error": "Projet introuvable."}), 404
    c = _contenu_projet(cle, taches, projets, lire_messages(limite=100000))
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        _ecrire_projet(z, "", c)
    return _envoyer_zip(tampon, f"{_slug(c['nom'])}-{time.strftime('%Y-%m-%d')}.zip")


@flask_app.get("/api/projets-export")
@require_admin
def api_projets_export():
    """Tous les projets, un dossier chacun, et un sommaire."""
    taches, projets = lire_taches(), lire_projets()
    messages = lire_messages(limite=100000)
    tampon = io.BytesIO()
    sommaire = ["# Projets CodeLab", "",
                "Exporté le " + datetime.datetime.now().strftime("%d/%m/%Y à %H:%M"), ""]
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        pris = set()
        for cle in _cles_de_projets(taches, projets):
            c = _contenu_projet(cle, taches, projets, messages)
            dossier, k = _slug(c["nom"]), 2
            while dossier in pris:
                dossier, k = f"{_slug(c['nom'])}-{k}", k + 1
            pris.add(dossier)
            _ecrire_projet(z, dossier + "/", c)
            ouvertes = sum(1 for t in c["taches"] if t["statut"] != "fait")
            sommaire.append(f"- [{c['nom']}]({dossier}/projet.md) — {ouvertes} tâche"
                            f"{'s' if ouvertes > 1 else ''} ouverte{'s' if ouvertes > 1 else ''}, "
                            f"{len(c['messages'])} message{'s' if len(c['messages']) > 1 else ''}")
        z.writestr("LISEZMOI.md", "\n".join(sommaire) + "\n")
    return _envoyer_zip(tampon, f"projets-codelab-{time.strftime('%Y-%m-%d')}.zip")


@flask_app.post("/api/projets")
@require_admin
def api_projet_creer():
    d = request.get_json(force=True, silent=True) or {}
    nom = _titre_tache(d.get("nom"))[:PROJET_NOM_MAX]
    if not nom:
        return jsonify({"error": "Donne un nom au projet."}), 400
    etape = d.get("etape") or "idee"
    if etape not in ETAPES_PROJET:
        return jsonify({"error": "Étape inconnue."}), 400
    try:
        ident = creer_projet(nom, _texte_long(d.get("description"), PROJET_DESCRIPTION_MAX),
                             etape=etape, taches=_sous_taches_propres(d.get("taches")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "id": ident})


@flask_app.put("/api/projets/<ident>")
@require_admin
def api_projet_modifier(ident):
    """Nom, description, etape (idee, construction, realise) et l'application
    qui le concretise. Seuls les champs PRESENTS changent."""
    d = request.get_json(force=True, silent=True) or {}
    champs, valeurs = [], []
    if "nom" in d:
        nom = _titre_tache(d.get("nom"))[:PROJET_NOM_MAX]
        if not nom:
            return jsonify({"error": "Donne un nom au projet."}), 400
        champs.append("nom = ?"); valeurs.append(nom)
    if "description" in d:
        champs.append("description = ?")
        valeurs.append(_texte_long(d.get("description"), PROJET_DESCRIPTION_MAX))
    if "etape" in d:
        if d.get("etape") not in ETAPES_PROJET:
            return jsonify({"error": "Étape inconnue."}), 400
        champs.append("etape = ?"); valeurs.append(d.get("etape"))
    if "application" in d:
        nom_app = str(d.get("application") or "").strip()
        if nom_app and nom_app not in load():
            return jsonify({"error": "Application inconnue."}), 400
        champs.append("application = ?"); valeurs.append(nom_app or None)
    if not champs:
        return jsonify({"error": "Rien à modifier."}), 400
    champs.append("maj = ?"); valeurs.append(int(time.time()))
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute(f"UPDATE projets SET {', '.join(champs)} WHERE id = ?",
                       (*valeurs, ident)).rowcount
    if not n:
        return jsonify({"error": "Projet introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.put("/api/projets/ordre")
@require_admin
def api_projets_ordre():
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        connus = {r[0] for r in cx.execute("SELECT id FROM projets")}
        if set(ordre) != connus or len(ordre) != len(connus):
            return jsonify({"error": "La liste ne correspond pas aux projets."}), 409
        for rang, pid in enumerate(ordre):
            cx.execute("UPDATE projets SET ordre = ? WHERE id = ?", (rang, pid))
    return jsonify({"ok": True})


@flask_app.delete("/api/projets/<ident>")
@require_admin
def api_projet_supprimer(ident):
    """Supprime le projet. Ses taches ne disparaissent pas avec lui : elles
    passent dans « General », ou l'on decide de leur sort."""
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute("DELETE FROM projets WHERE id = ?", (ident,)).rowcount
        if n:
            cx.execute("UPDATE taches SET projet = ? WHERE projet = ?", (PROJET_GENERAL, ident))
    if n:
        # Ses messages redeviennent « a classer ».
        modifier_messages({m["id"]: {"projet": None} for m in lire_messages(limite=100000)
                           if m.get("projet") == ident})
    if not n:
        return jsonify({"error": "Projet introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.post("/api/taches/<ident>/sous-taches")
@require_admin
def api_sous_tache_creer(ident):
    titre = _titre_tache((request.get_json(force=True, silent=True) or {}).get("titre"))
    if not titre:
        return jsonify({"error": "Le titre est obligatoire."}), 400
    with stockage.transaction(DB_FILE) as cx:
        if not cx.execute("SELECT 1 FROM taches WHERE id = ?", (ident,)).fetchone():
            return jsonify({"error": "Tâche introuvable."}), 404
        nombre = cx.execute("SELECT COUNT(*) FROM sous_taches WHERE tache = ?",
                            (ident,)).fetchone()[0]
        if nombre >= SOUS_TACHES_MAX:
            return jsonify({"error": f"{SOUS_TACHES_MAX} sous-tâches au plus."}), 400
        sid = secrets.token_hex(8)
        cx.execute("INSERT INTO sous_taches (id, tache, titre, ordre) VALUES (?, ?, ?, ?)",
                   (sid, ident, titre, nombre))
    return jsonify({"ok": True, "id": sid})


@flask_app.put("/api/taches/<ident>/ordre")
@require_admin
def api_sous_taches_ordre(ident):
    """L'ordre des sous-taches, apres un glisser-deposer : la plus haute est
    la plus prioritaire. La liste doit nommer exactement les sous-taches de
    la tache, ni plus ni moins."""
    ordre = (request.get_json(force=True, silent=True) or {}).get("ordre")
    if not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre):
        return jsonify({"error": "Ordre invalide."}), 400
    with stockage.transaction(DB_FILE) as cx:
        if not cx.execute("SELECT 1 FROM taches WHERE id = ?", (ident,)).fetchone():
            return jsonify({"error": "Tâche introuvable."}), 404
        actuelles = {r[0] for r in cx.execute("SELECT id FROM sous_taches WHERE tache = ?",
                                              (ident,))}
        if set(ordre) != actuelles or len(ordre) != len(actuelles):
            return jsonify({"error": "La liste ne correspond pas aux sous-tâches."}), 409
        for rang, sid in enumerate(ordre):
            cx.execute("UPDATE sous_taches SET ordre = ? WHERE id = ?", (rang, sid))
    return jsonify({"ok": True})


@flask_app.put("/api/sous-taches/<sid>")
@require_admin
def api_sous_tache_modifier(sid):
    d = request.get_json(force=True, silent=True) or {}
    champs, valeurs = [], []
    if "titre" in d:
        titre = _titre_tache(d.get("titre"))
        if not titre:
            return jsonify({"error": "Le titre est obligatoire."}), 400
        champs.append("titre = ?"); valeurs.append(titre)
    if "faite" in d:
        champs.append("faite = ?"); valeurs.append(1 if d.get("faite") else 0)
    if not champs:
        return jsonify({"error": "Rien à modifier."}), 400
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute(f"UPDATE sous_taches SET {', '.join(champs)} WHERE id = ?",
                       (*valeurs, sid)).rowcount
    if not n:
        return jsonify({"error": "Sous-tâche introuvable."}), 404
    return jsonify({"ok": True})


@flask_app.delete("/api/sous-taches/<sid>")
@require_admin
def api_sous_tache_supprimer(sid):
    with stockage.transaction(DB_FILE) as cx:
        n = cx.execute("DELETE FROM sous_taches WHERE id = ?", (sid,)).rowcount
    if not n:
        return jsonify({"error": "Sous-tâche introuvable."}), 404
    return jsonify({"ok": True})


# ------------------- versions des applications, et ouvertures -------------------
#
# « Une nouvelle version depuis la derniere fois que je l'ai ouverte. » Deux
# signaux font une version : un deploiement ou un build reussi depuis le
# panneau, et un nouveau commit dans le depot du projet (lu dans .git, sans
# lancer git). Chaque compte garde la version qu'il a vue en ouvrant
# l'application ; la tuile le dit quand elle a change depuis.

def commit_du_projet(chemin):
    """Le commit courant du depot du projet, ou None (pas de depot lisible).

    Lu directement dans .git -- HEAD, puis la reference, puis packed-refs --
    pour ne pas lancer un processus par application a chaque hub affiche.
    """
    if not chemin:
        return None
    racine = os.path.join(chemin, ".git")
    try:
        with open(os.path.join(racine, "HEAD"), encoding="utf-8") as f:
            tete = f.read().strip()
        if not tete.startswith("ref:"):
            return tete[:40] or None
        ref = tete[4:].strip()
        if ".." in ref.split("/"):
            return None
        chemin_ref = os.path.join(racine, *ref.split("/"))
        if os.path.isfile(chemin_ref):
            with open(chemin_ref, encoding="utf-8") as f:
                return f.read().strip()[:40] or None
        with open(os.path.join(racine, "packed-refs"), encoding="utf-8") as f:
            for ligne in f:
                morceaux = ligne.split()
                if len(morceaux) == 2 and morceaux[1] == ref:
                    return morceaux[0][:40]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def versions_des_apps(apps):
    """{nom: horodatage de la version courante}, en notant au passage un
    commit nouveau. Une application jamais vue prend sa version maintenant :
    personne ne l'a encore ouverte, rien ne s'affiche."""
    maintenant = int(time.time())
    connues = {l["app"]: l for l in stockage.lire(DB_FILE, "SELECT * FROM versions_apps")}
    resultat, ecrire = {}, []
    for nom, a in apps.items():
        commit = commit_du_projet(a.get("path"))
        ligne = connues.get(nom)
        if ligne is None:
            ecrire.append((nom, maintenant, commit))
            resultat[nom] = maintenant
        elif commit and commit != ligne["commit_"]:
            ecrire.append((nom, maintenant, commit))
            resultat[nom] = maintenant
        else:
            resultat[nom] = ligne["ts"]
    if ecrire:
        with stockage.transaction(DB_FILE) as cx:
            cx.executemany("INSERT OR REPLACE INTO versions_apps (app, ts, commit_) "
                           "VALUES (?, ?, ?)", ecrire)
    return resultat


def marquer_nouvelle_version(nom):
    """Un deploiement ou un build a reussi : c'est une nouvelle version."""
    a = load().get(nom) or {}
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("INSERT OR REPLACE INTO versions_apps (app, ts, commit_) VALUES (?, ?, ?)",
                   (nom, int(time.time()), commit_du_projet(a.get("path"))))


def ouvertures_du_compte(qui=None):
    """{application: {ts, version}} : ce que ce compte a ouvert, et quand."""
    qui = qui or utilisateur_courant() or NOM_ADMIN
    return {l["app"]: {"ts": l["ts"], "version": l["version"]}
            for l in stockage.lire(DB_FILE, "SELECT app, ts, version FROM apps_vues "
                                            "WHERE compte = ?", (qui,))}


# ------------------------- la composition du hub -------------------------
#
# Le style Bento se COMPOSE comme un ecran d'accueil : chaque compte range
# ses tuiles d'application et ses widgets dans l'ordre qu'il veut, et choisit
# la taille de chaque widget. La disposition est gardee cote serveur, pour
# suivre la personne d'un appareil a l'autre.
#
# Peu de widgets, et seulement ceux qui parlent du hub ou de ses
# applications : pas d'horloge ni de bloc-notes, que l'ordinateur a deja.
# Le catalogue est FERME : rien de ce que le client envoie ne devient un nom
# de widget.
#   portee « hub »  : un widget sur le hub lui-meme ;
#   portee « app »  : un widget sur UNE application, a choisir.
WIDGETS = {
    "une": {"titre": "Dernière ouverte", "portee": "hub", "tailles": ("moyen", "grand"),
            "description": "L'application ouverte en dernier, en aperçu."},
    "etat": {"titre": "État des applications", "portee": "hub", "tailles": ("petit", "moyen"),
             "description": "Combien répondent, et laquelle ne répond pas.", "admin": True},
    "messages": {"titre": "Messages", "portee": "hub", "tailles": ("petit", "moyen"),
                 "description": "Les messages à lire et à classer.", "admin": True},
    "sauvegardes": {"titre": "Sauvegardes", "portee": "hub", "tailles": ("petit", "moyen"),
                    "description": "La dernière sauvegarde, et si une copie existe ailleurs.",
                    "admin": True},
    "serveur": {"titre": "Serveur", "portee": "hub", "tailles": ("petit", "moyen", "grand"),
                "description": "Processeur, mémoire et disque, en direct.", "admin": True},
    "activite": {"titre": "Activité", "portee": "hub", "tailles": ("moyen", "grand"),
                 "description": "Les ouvertures des deux dernières semaines.", "admin": True},
    "nouveautes": {"titre": "Nouvelles versions", "portee": "hub", "tailles": ("petit", "moyen"),
                   "description": "Les applications mises à jour depuis ta dernière visite."},
    "message": {"titre": "Écrire à l'administrateur", "portee": "hub", "tailles": ("moyen", "grand"),
                "description": "Une idée, un souci, une demande.", "utilisateur": True},
    "suivi": {"titre": "Mes messages", "portee": "hub", "tailles": ("petit", "moyen"),
              "description": "Où en sont les messages que tu as envoyés.", "utilisateur": True},
    "apercu": {"titre": "Aperçu", "portee": "app", "tailles": ("moyen", "grand"),
               "description": "La page d'une application, en direct."},
}
DISPOSITION_MAX = 300
# Avant la composition, un compte ne choisissait que ses widgets : on en
# garde ce qui a encore un sens.
_ANCIENNES_TAILLES = {"large": "grand", "normal": "moyen"}


def widgets_permis():
    admin = est_admin()
    return [w for w, d in WIDGETS.items()
            if not (d.get("admin") and not admin) and not (d.get("utilisateur") and admin)]


def lire_preference(cle, qui=None):
    qui = qui or utilisateur_courant() or NOM_ADMIN
    lignes = stockage.lire(DB_FILE, "SELECT valeur FROM preferences WHERE compte = ? AND cle = ?",
                           (qui, cle))
    if not lignes:
        return None
    try:
        return json.loads(lignes[0]["valeur"])
    except ValueError:
        return None


def ecrire_preference(cle, valeur, qui=None):
    qui = qui or utilisateur_courant() or NOM_ADMIN
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("INSERT OR REPLACE INTO preferences (compte, cle, valeur) VALUES (?, ?, ?)",
                   (qui, cle, json.dumps(valeur, ensure_ascii=False)))


def apps_du_hub_visibles():
    """Les tuiles qu'un compte peut ranger : ses applications, et les services."""
    autorises = projets_autorises()
    noms = {n for n in load() if peut_voir(n) and (autorises is None or n in autorises)}
    return noms, {s["name"] for s in services_du_hub()}


def disposition_propre(elements):
    """Valide une disposition. Rend (liste propre, erreur)."""
    if not isinstance(elements, list) or len(elements) > DISPOSITION_MAX:
        return None, "Disposition invalide."
    permis = widgets_permis()
    apps, services = apps_du_hub_visibles()
    propres, vus = [], set()
    for e in elements:
        if not isinstance(e, dict):
            return None, "Disposition invalide."
        if e.get("t") == "a":
            nom = e.get("id")
            if nom not in apps and nom not in services:
                continue        # une application disparue ou retiree : on l'oublie
            cle = "a:" + nom
            propre = {"t": "a", "id": nom}
        elif e.get("t") == "w":
            wid, taille = e.get("id"), e.get("taille")
            if wid not in permis:
                return None, "Widget inconnu."
            if taille not in WIDGETS[wid]["tailles"]:
                return None, "Taille inconnue pour ce widget."
            propre = {"t": "w", "id": wid, "taille": taille}
            if WIDGETS[wid]["portee"] == "app":
                if e.get("app") not in apps:
                    return None, "Application inconnue."
                propre["app"] = e["app"]
            cle = "w:" + wid + ":" + (propre.get("app") or "")
        else:
            return None, "Disposition invalide."
        if cle in vus:
            return None, "Élément en double."
        vus.add(cle)
        propres.append(propre)
    return propres, None


def disposition_du_compte():
    """La disposition enregistree, ou celle d'avant la composition."""
    elements = lire_preference("hub_disposition")
    if not isinstance(elements, list):
        anciens = lire_preference("widgets")
        if isinstance(anciens, list):
            elements = [{"t": "w", "id": "messages" if w.get("id") == "demandes" else w.get("id"),
                         "taille": _ANCIENNES_TAILLES.get(w.get("taille"), "moyen")}
                        for w in anciens if isinstance(w, dict)]
        else:
            # Serveur et Activite ne tiennent que chez l'administrateur ; le
            # filtre des widgets permis ci-dessous les retire pour les autres.
            elements = [{"t": "w", "id": "une", "taille": "grand"},
                        {"t": "w", "id": "serveur", "taille": "moyen"},
                        {"t": "w", "id": "activite", "taille": "moyen"},
                        {"t": "w", "id": "etat", "taille": "petit"},
                        {"t": "w", "id": "messages", "taille": "petit"},
                        {"t": "w", "id": "message", "taille": "moyen"},
                        {"t": "w", "id": "suivi", "taille": "moyen"}]
    permis = widgets_permis()
    apps, services = apps_du_hub_visibles()
    garde = []
    for e in elements:
        if not isinstance(e, dict):
            continue
        if e.get("t") == "w":
            d = WIDGETS.get(e.get("id"))
            if not d or e["id"] not in permis:
                continue
            if e.get("taille") not in d["tailles"]:
                e = dict(e, taille=d["tailles"][-1] if e.get("taille") == "grand" else d["tailles"][0])
            if d["portee"] == "app" and e.get("app") not in apps:
                continue
        elif e.get("t") == "a" and e.get("id") not in apps and e.get("id") not in services:
            continue
        garde.append(e)
    return garde


@flask_app.get("/api/hub/disposition")
@require_auth
def api_disposition():
    """La disposition du hub, et le catalogue des widgets que ce compte peut poser.

    Les applications absentes de la disposition (nouvelles, jamais rangees)
    viennent a la suite : c'est la page qui les y ajoute, dans l'ordre des noms.
    """
    return jsonify({"elements": disposition_du_compte(),
                    "catalogue": [{"id": w, "titre": WIDGETS[w]["titre"],
                                   "description": WIDGETS[w]["description"],
                                   "portee": WIDGETS[w]["portee"],
                                   "tailles": list(WIDGETS[w]["tailles"])}
                                  for w in widgets_permis()]})


@flask_app.get("/api/hub/pouls")
@require_admin
def api_hub_pouls():
    """Les chiffres des widgets « Serveur » et « Activité ».

    Reserve a l'administrateur : la charge de la machine et le rythme des
    ouvertures ne regardent que lui, comme le journal des acces dont le
    second est tire.
    """
    quoi = set((request.args.get("quoi") or "serveur,activite").split(","))
    out = {}
    if "serveur" in quoi:
        out["serveur"] = etat_serveur()
    if "activite" in quoi:
        out["activite"] = activite_recente()
    return jsonify(out)


@flask_app.put("/api/hub/disposition")
@require_auth
def api_disposition_modifier():
    """{elements: [{t: "a", id} | {t: "w", id, taille, app?}]}, dans l'ordre."""
    propres, erreur = disposition_propre((request.get_json(force=True, silent=True) or {})
                                         .get("elements"))
    if erreur:
        return jsonify({"error": erreur}), 400
    ecrire_preference("hub_disposition", propres)
    return jsonify({"ok": True, "elements": propres})


@flask_app.post("/api/mes-apps/<n>/ouverte")
@require_auth
def api_app_ouverte(n):
    """Le hub previent qu'il ouvre une application : on note la version vue.

    Seule une application que ce compte peut ouvrir est notee -- sinon la
    route dirait l'existence de projets qu'il n'a pas le droit de connaitre.
    """
    apps = load()
    autorises = projets_autorises()
    if n not in apps or not peut_voir(n) or (autorises is not None and n not in autorises):
        return jsonify({"error": "Application inconnue."}), 404
    version = versions_des_apps({n: apps[n]})[n]
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("INSERT OR REPLACE INTO apps_vues (compte, app, ts, version) VALUES (?, ?, ?, ?)",
                   (utilisateur_courant() or NOM_ADMIN, n, int(time.time()), version))
    return jsonify({"ok": True})


@flask_app.post("/api/mes-apps/<n>/masquer")
@require_auth
def api_masquer(n):
    """Retire une application du hub de CE compte.

    Ce n'est pas un droit retire : le projet tourne toujours, les autres
    comptes le voient, et son adresse reste ouverte a qui la connait. On ne
    peut masquer que ce qu'on peut deja voir -- sinon la liste des masquees
    dirait l'existence de projets qu'on n'a pas le droit de connaitre.

    LES SERVICES DE LA STACK AUSSI. Dagster n'est pas une application
    declaree : il ne figure pas dans load(), et cette route le refusait donc
    en 404. Sa tuile etait la seule qu'on ne pouvait pas ecarter de son
    propre hub. peut_voir() garde le dernier mot -- pour une application par
    defaut, il exige l'administrateur -- donc ouvrir ce nom-ci n'ouvre rien
    de plus a un compte utilisateur.
    """
    if not (n in load() or n in SERVICES_MASQUABLES) or not peut_voir(n):
        return jsonify({"error": "Application inconnue."}), 404
    qui = utilisateur_courant() or NOM_ADMIN
    d = lire_masquees()
    liste = [x for x in (d.get(qui) or []) if isinstance(x, str)]
    if n not in liste:
        liste.append(n)
        d[qui] = sorted(liste)
        ecrire_masquees(d)
        # Journalise : l'administrateur doit pouvoir constater qu'un projet a
        # disparu d'un hub sans que personne n'ait touche a ses droits.
        journaliser("masquage", qui=qui, app=n, action="masque",
                    ip=_adresse_client())
    return jsonify({"ok": True, "masquees": sorted(liste)})


@flask_app.delete("/api/mes-apps/<n>/masquer")
@require_auth
def api_afficher(n):
    """Remet une application dans le hub de ce compte."""
    qui = utilisateur_courant() or NOM_ADMIN
    d = lire_masquees()
    liste = [x for x in (d.get(qui) or []) if isinstance(x, str)]
    if n in liste:
        liste.remove(n)
        # On retire la cle vide plutot que de laisser un tableau vide : le
        # fichier reste lisible a l'oeil, et un compte supprime ne laisse pas
        # d'entree derriere lui.
        if liste:
            d[qui] = sorted(liste)
        else:
            d.pop(qui, None)
        ecrire_masquees(d)
        journaliser("masquage", qui=qui, app=n, action="affiche",
                    ip=_adresse_client())
    return jsonify({"ok": True, "masquees": sorted(liste)})


# ------------------------------ proxy --------------------------------

def strip_session_cookie(raw):
    """Retire le cookie de session du panneau d'un en-tete Cookie."""
    nom = flask_app.config.get("SESSION_COOKIE_NAME") or "session"
    gardes = [c.strip() for c in raw.split(";")
              if c.strip() and c.split("=", 1)[0].strip() != nom]
    return "; ".join(gardes)


# Le ruban de retour, glisse dans les pages HTML servies par le proxy.
#
# POURQUOI L'INJECTER PLUTOT QUE LE DEMANDER AUX APPLICATIONS : une
# application deployee est du code quelconque, souvent ecrit avant d'arriver
# ici, et parfois pas par nous. Lui demander d'ajouter un lien vers le hub,
# c'est n'en avoir aucun dans la plupart des cas. Le proxy, lui, voit passer
# toutes les pages.
#
# Styles en ligne et nom de classe improbable : la page d'accueil de
# l'application a ses propres regles, et le ruban ne doit ni les subir ni les
# changer. all:initial coupe l'heritage dans les deux sens.
def ruban_retour():
    """Le ruban, construit a chaque page : son lien depend de l'origine."""
    return (
        # target=_top : dans le panneau installe, l'application est encadree
        # (voir ouvrir_dans_le_panneau). Sans lui, le hub s'ouvrirait DANS
        # le cadre, un panneau dans le panneau.
        '<a href="' + lien_panneau() + '" target="_top" id="codelab-retour-hub" '
        'title="Revenir au hub CodeLab" '
        # Au-dessus de la barre de geste et a cote de l'encoche : les bords
        # que pose environnement-app.js (0 hors du hub installe).
        'style="all:initial;position:fixed;left:calc(14px + var(--codelab-bord-gauche, 0px));'
        'bottom:calc(14px + var(--codelab-bord-bas, 0px));z-index:2147483647;'
        'display:inline-flex;align-items:center;gap:7px;padding:8px 13px;'
        'font:600 13px/1 -apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;'
        'color:#fff;background:#141a21;border-radius:999px;cursor:pointer;'
        'box-shadow:0 2px 10px rgba(0,0,0,.28);text-decoration:none">'
        '<span style="all:initial;color:#fff;font:600 15px/1 sans-serif">&#8592;</span>'
        '<span style="all:initial;color:#fff;font:600 13px/1 -apple-system,'
        'BlinkMacSystemFont,sans-serif">CodeLab</span></a>'
    ).encode()


# L'ENVIRONNEMENT D'UNE APPLICATION : ce qui l'entoure (site ou app, appareil,
# plateforme, bords de l'ecran), lu ou simule, et pose sur sa page avant ses
# propres scripts. Le detail est dans environnement-app.js. Glisse EN TETE :
# un script qui demande navigator.standalone doit deja avoir la reponse.
ENVIRONNEMENT_APP_JS = _lire_ressource("environnement-app.js")


def script_environnement(connecte):
    """Le script, avec l'origine du panneau pour qui est connecte.

    Un visiteur anonyme n'a pas a lire l'adresse du panneau : il recoit le
    script sans elle, qui lit l'environnement sans rien simuler -- il n'est
    de toute facon jamais encadre par le panneau.
    """
    panneau = ""
    if connecte:
        # Relatif (« / ») quand panneau et applications partagent l'adresse :
        # l'origine est alors celle de la requete.
        u = urllib.parse.urlsplit(urllib.parse.urljoin(request.host_url, lien_panneau()))
        if u.scheme and u.netloc:
            panneau = u.scheme + "://" + u.netloc
    return ("<script>" + ENVIRONNEMENT_APP_JS.replace("__PANNEAU__", json.dumps(panneau))
            + "</script>").encode()


def injecter_environnement(data, status, entetes, connecte):
    """Glisse le script juste apres <head>, avant tout ce que la page charge."""
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    m = re.search(rb"<head(\s[^>]*)?>", data[:65536], re.I)
    if not m:
        return data, entetes
    data = data[:m.end()] + script_environnement(connecte) + data[m.end():]
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


def _entete(entetes, nom):
    for k, v in entetes:
        if k.lower() == nom:
            return v
    return ""


def injecter_ruban(data, status, entetes):
    """Glisse le ruban de retour avant </body>, quand c'est sans risque.

    Quatre refus, et chacun evite de casser une application :

      - un code autre que 200 : une page d'erreur de l'application n'a pas a
        etre retouchee ;
      - autre chose que du HTML : une image ou du JSON ne se modifient pas ;
      - un corps COMPRESSE : les octets ne contiennent alors pas "</body>",
        et y ecrire ferait un flux illisible ;
      - pas de </body> : fragment HTML renvoye a du JavaScript, reponse
        partielle. On ne devine pas ou l'inserer.

    Travaille sur les OCTETS et jamais sur du texte decode : une page dans un
    encodage qu'on aurait mal devine reviendrait abimee, et une page n'a pas
    a payer le passage par le proxy.
    """
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    i = data.lower().rfind(b"</body>")
    if i < 0:
        return data, entetes
    data = data[:i] + ruban_retour() + data[i:]
    # Content-Length devient faux si on ne le refait pas : le navigateur
    # tronquerait la page a l'ancienne taille, juste avant le ruban.
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


# L'ICONE D'ONGLET D'UNE APPLICATION. Le panneau porte le logo CodeLab dans
# ses onglets ; une application porte le SIEN, et seulement la. Beaucoup
# d'applications n'en declarent aucun : l'onglet restait vide, ou prenait
# ce que le navigateur trouvait. Le logo qu'on voit dans le hub est donc
# pose comme icone d'onglet -- a condition que la page n'en declare pas
# deja une : celle de l'application gagne toujours.
_MARQUES_ICONE = (b'rel="icon"', b"rel='icon'", b"rel=icon", b'rel="shortcut icon"',
                  b"rel='shortcut icon'", b'rel="apple-touch-icon"')


def injecter_icone(data, status, entetes, nom):
    """Ajoute <link rel="icon"> avant </head>, si la page n'en a aucun."""
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    bas = data.lower()
    i = bas.find(b"</head>")
    if i < 0 or any(m in bas[:i] for m in _MARQUES_ICONE):
        return data, entetes
    lien = (lien_panneau() + "api/icon/" + urllib.parse.quote(nom, safe="")).encode()
    data = data[:i] + b'<link rel="icon" href="' + lien + b'">' + data[i:]
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


# CHAQUE APPLICATION S'AJOUTE A L'ECRAN D'ACCUEIL COMME UNE APP.
#
# Le panneau le fait pour lui-meme (voir ecran_accueil_manifeste). Une
# application deployee, elle, s'ajoutait comme un simple signet : capture de
# la page en guise d'icone, onglet Safari a l'ouverture, barre d'adresse et
# retour arriere compris. Le proxy lui donne donc ce qu'il faut, comme il lui
# donne deja son ruban et son icone d'onglet :
#
#   - un manifeste PROPRE A L'APPLICATION, servi sous son prefixe
#     (/<nom>/__codelab/manifest.webmanifest). Sa portee s'arrete a /<nom>/ :
#     l'app installee reste dans son projet, et un lien vers un autre projet
#     sort de l'app au lieu de la detourner ;
#   - les balises apple-mobile-web-app-*, et une icone servie sur l'ORIGINE
#     DE L'APPLICATION -- /api/icon vit sur celle du panneau et exige une
#     session, qu'une app fraichement ajoutee n'a pas encore.
#
# Chaque piece n'est posee que si la page ne la declare pas deja : une
# application qui a son propre manifeste ou sa propre icone garde les siens.
#
# Le ruban « ← CodeLab » reste affiche en mode app : c'est le seul chemin
# vers le hub depuis une app installee, et il est voulu.
PREFIXE_CODELAB = "__codelab/"


def racine_application(nom):
    """Le chemin ou commence l'application, vu de son navigateur.

    « / » sur son sous-domaine (blog.toi.duckdns.org), « /<nom>/ » sur
    l'adresse partagee. Le manifeste, son icone et sa portee en dependent :
    ecrits « /blog/ » sur le sous-domaine, ils pointaient vers une page que
    l'application n'a pas, et l'app installee s'ouvrait sur une erreur.
    """
    if getattr(g, "sous_domaine", ""):
        return "/"
    return "/" + urllib.parse.quote(nom, safe="") + "/"


def injecter_webapp(data, status, entetes, nom, titre):
    """Ajoute avant </head> ce qui fait de la page une app installable."""
    if status != 200:
        return data, entetes
    if "text/html" not in _entete(entetes, "content-type").lower():
        return data, entetes
    if _entete(entetes, "content-encoding"):
        return data, entetes
    bas = data.lower()
    i = bas.find(b"</head>")
    if i < 0:
        return data, entetes
    tete = bas[:i]
    base = racine_application(nom) + PREFIXE_CODELAB
    titre_html = (titre.replace("&", "&amp;").replace("<", "&lt;")
                  .replace(">", "&gt;").replace('"', "&quot;"))
    ajout = ""
    if b'rel="manifest"' not in tete and b"rel='manifest'" not in tete \
            and b"rel=manifest" not in tete:
        ajout += '<link rel="manifest" href="' + base + 'manifest.webmanifest">'
    if b"apple-touch-icon" not in tete:
        ajout += '<link rel="apple-touch-icon" href="' + base + 'icone.png">'
    if b"apple-mobile-web-app-capable" not in tete:
        ajout += '<meta name="apple-mobile-web-app-capable" content="yes">'
    if b"mobile-web-app-capable" not in tete.replace(b"apple-mobile-web-app-capable", b""):
        ajout += '<meta name="mobile-web-app-capable" content="yes">'
    if b"apple-mobile-web-app-title" not in tete:
        ajout += '<meta name="apple-mobile-web-app-title" content="' + titre_html + '">'
    data = data[:i] + ajout.encode() + data[i:]
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


def webapp_manifeste(nom, titre):
    """Le manifeste d'une application : sa portee, son nom, son icone."""
    racine = racine_application(nom)
    manifeste = {
        "name": titre,
        "short_name": titre,
        "start_url": racine,
        "scope": racine,
        "display": "standalone",
        # La couleur du fond et du bandeau (style par defaut, clair) : c'est
        # celle de l'ecran de lancement et de la barre d'etat d'Android. Une
        # barre blanche au-dessus d'un bandeau gris cassait l'effet
        # d'application ; la page l'ajuste ensuite au theme choisi.
        "background_color": "#E9EBEE",
        "theme_color": "#E9EBEE",
        "icons": [{"src": racine + PREFIXE_CODELAB + "icone.png",
                   "sizes": "180x180", "purpose": "any"}],
    }
    r = Response(json.dumps(manifeste), mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    return r


def webapp_icone(a):
    """L'icone d'ecran d'accueil d'une application.

    iOS ne lit ni le SVG ni l'initiale coloree du hub : il lui faut une image
    matricielle. Le logo depose dans le projet sert s'il en est une (PNG ou
    JPEG, reconnu a sa signature) ; sinon, le logo CodeLab.
    """
    chemin = find_icon(a["path"]) if a.get("path") else None
    if chemin:
        try:
            with open(chemin, "rb") as f:
                octets = f.read()
        except OSError:
            octets = b""
        for signature, type_mime in ((b"\x89PNG\r\n\x1a\n", "image/png"),
                                     (b"\xff\xd8\xff", "image/jpeg")):
            if octets.startswith(signature):
                r = Response(octets, mimetype=type_mime)
                break
        else:
            r = Response(ICONE_ECRAN_ACCUEIL_PNG, mimetype="image/png")
    else:
        r = Response(ICONE_ECRAN_ACCUEIL_PNG, mimetype="image/png")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


# QUI PEUT AFFICHER UNE APPLICATION DANS UN CADRE : LE PANNEAU, ET LUI SEUL.
#
# Sans regle, n'importe quel site pouvait poser une application dans une
# iframe invisible et faire cliquer l'utilisateur dedans a son insu
# (clickjacking) -- avec SA session, puisque le navigateur l'envoie. A
# l'inverse, une application qui se protegeait elle-meme (X-Frame-Options,
# pose d'office par Django, Rails, Helmet...) s'affichait vide dans le
# panneau installe, qui l'encadre (voir ouvrir_dans_le_panneau) : DENY
# refuse tout, et SAMEORIGIN refuse le panneau, qui vit sur un autre port.
#
# Le proxy tranche donc pour toutes : frame-ancestors n'autorise que le
# panneau, sous chacune des adresses par lesquelles on l'atteint -- celle
# publiee par un reverse proxy, le meme hote que la requete sur le port du
# panneau, et localhost pour l'adresse locale. X-Frame-Options est retire :
# il ne sait pas nommer une autre origine, et frame-ancestors le remplace.
#
# Une application qui pose SA propre regle frame-ancestors la garde : c'est
# un choix explicite, et les politiques CSP se cumulent -- la plus stricte
# l'emporte.
def origines_encadrantes():
    """Les sources frame-ancestors : le panneau, sous toutes ses adresses."""
    if not origines_separees():
        return "'self'"
    sources = []
    domaine = domaine_des_applications()
    if domaine:
        sources.append(f"{schema_vu()}://{domaine}")
    hote = hote_demande()
    if hote and has_request_context():
        sources.append(f"{request.scheme}://{hote}:{PANEL_PORT}")
    sources += [f"http://localhost:{PANEL_PORT}", f"http://127.0.0.1:{PANEL_PORT}"]
    return " ".join(dict.fromkeys(sources))


def proteger_l_encadrement(entetes):
    entetes = [(k, v) for k, v in entetes if k.lower() != "x-frame-options"]
    politiques = " ".join(v for k, v in entetes
                          if k.lower() == "content-security-policy").lower()
    if "frame-ancestors" not in politiques:
        entetes.append(("Content-Security-Policy",
                        "frame-ancestors " + origines_encadrantes()))
    return entetes


def _proxy(name, sub):
    a = load().get(name)
    if not a:
        return Response(_page("Introuvable", "Aucune application \u00ab " + name + " \u00bb."),
                        404, mimetype="text/html")
    # Application privee : il faut une session, ET le droit sur ce projet
    # precis. Le controle est ici, dans le proxy, et pas dans l'interface --
    # une application dont le lien circule doit rester fermee quel que soit le
    # chemin emprunte, y compris par un compte utilisateur qui connaitrait
    # l'adresse d'un projet qu'on ne lui a pas autorise.
    # Une application par defaut suit la MEME frontiere que l'administration.
    # Le diagnostic dit ce qui tourne, ce qui est expose et quels comptes
    # n'ont pas de second facteur : c'est une page d'administration par ce
    # qu'elle montre, elle doit donc s'arreter ou l'administration s'arrete.
    # Verifie a chaque requete, pas seulement a l'ouverture : une session
    # commencee dans le salon puis reprise depuis l'exterieur n'emporte pas
    # le droit avec elle.
    if est_app_par_defaut(name):
        hors = refus_admin_hors_reseau()
        if hors:
            journaliser("refus", app=name, motif="hors reseau local",
                        ip=_adresse_client())
            return Response(_page("Accès refusé", hors), 403,
                            mimetype="text/html")
    if visibilite(a) == VISIBILITE_PRIVEE:
        if not is_authed():
            # La connexion vit sur l'origine du PANNEAU : un "/login" relatif
            # tombait, sur le port des applications, sur « Ce n'est pas le
            # panneau ». C'est le premier ecran d'une app privee ajoutee a
            # l'ecran d'accueil, qui n'a pas encore de session a elle.
            # Avec la suite : apres la connexion, le visiteur revient dans
            # l'application, a la page qu'il demandait (/ouvrir.app/...).
            return redirect(lien_panneau() + "login?suite=" + urllib.parse.quote(
                suite_vers_application(name, sub), safe="/"))
        if not peut_voir(name):
            return Response(_page("Accès refusé",
                                  "Ton compte n'a pas accès à \u00ab " + name + " \u00bb.",
                                  "Demande l'accès à l'administrateur."),
                            403, mimetype="text/html")
    # Le manifeste et l'icone d'ecran d'accueil, APRES les controles d'acces
    # (le nom d'une app privee ne regarde que ceux qui peuvent l'ouvrir) mais
    # AVANT le journal : iOS les demande de lui-meme, ce ne sont pas des
    # visites. Repondus meme application arretee.
    if request.method == "GET" and sub == PREFIXE_CODELAB + "manifest.webmanifest":
        return webapp_manifeste(name, titre_de(name, a))
    if request.method == "GET" and sub == PREFIXE_CODELAB + "icone.png":
        return webapp_icone(a)
    # Note l'ouverture APRES les controles d'acces : un refus n'est pas une
    # visite, et le journal servirait mal s'il melangeait les deux.
    journaliser_ouverture(name)
    if not is_running(name):
        return page_application_fermee(name, a)
    url = ("http://127.0.0.1:" + str(a["port"]) + "/"
           + urllib.parse.quote(sub, safe="/"))
    if request.query_string:
        url += "?" + request.query_string.decode()
    body = request.get_data() if request.method in ("POST", "PUT", "PATCH") else None
    req = urllib.request.Request(url, data=body, method=request.method)
    # Les en-tetes SSO sont controles par le proxy. Ceux fournis par le
    # navigateur sont TOUJOURS supprimes avant injection : une application
    # publique ne doit pas pouvoir s'attribuer une identite, et une application
    # privee ne doit recevoir que l'identite de la session qui a franchi les
    # controles de ce proxy.
    # TOUT en-tete X-CodeLab-* : la liste des en-tetes poses par le proxy
    # grandit, et un en-tete oublie dans une liste serait un en-tete que le
    # navigateur pourrait fabriquer.
    for k, v in request.headers:
        if k.lower() in HOP or k.lower() == "host" or k.lower().startswith("x-codelab-"):
            continue
        if k.lower() == "cookie":
            # Les applications sont servies sur la MEME origine que le
            # panneau (:9001/<app>/) : le navigateur leur envoie donc le
            # cookie de session admin, et le proxy le transmettait tel quel.
            # Une application deployee pouvait ainsi lire cette session et
            # piloter le panneau -- c'est-a-dire faire executer n'importe
            # quelle commande. On retire ce seul cookie et on laisse passer
            # les autres, qui appartiennent a l'application.
            #
            # Cela ne supprime pas la meme origine elle-meme : une XSS dans
            # une application reste une XSS dans l'origine du panneau. Y
            # remedier demanderait un port ou un sous-domaine par
            # application, ce que ce proxy a justement pour but d'eviter.
            v = strip_session_cookie(v)
            if not v:
                continue
        req.add_header(k, v)

    # Assertion signee courte : l'application ne connait ni le cookie Flask
    # ni le secret de session. Pour un visiteur anonyme, aucune identite n'est
    # injectee, meme si l'application est publique.
    # L'adresse du panneau, vue du navigateur : les liens « Se connecter avec
    # CodeLab » et « Se deconnecter » de l'application en ont besoin, et
    # l'application ne peut pas la deviner (autre port, autre nom public).
    req.add_header("X-CodeLab-Panneau", lien_panneau())
    token = jeton_sso(name)
    if token:
        req.add_header("X-CodeLab-Auth", token)
        req.add_header("X-CodeLab-User", utilisateur_courant())
        req.add_header("X-CodeLab-Role", role_courant() or "")
        req.add_header("X-CodeLab-Authenticated", "true")
        compte = None if est_admin() else lire_utilisateurs().get(utilisateur_courant())
        email = (compte or {}).get("email", "") if compte else ""
        if email:
            req.add_header("X-CodeLab-Email", email)

    try:
        r = urllib.request.urlopen(req, timeout=30)
        data, status, headers = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        data, status, headers = e.read(), e.code, e.headers
    except Exception:
        return page_application_fermee(name, a, muette=True)
    # UNE PAGE QUI PLANTE, VUE PAR UN UTILISATEUR : la page neutre plutot que
    # la trace de l'application. Seulement pour une page qu'on OUVRE (une
    # navigation) : un appel d'API de l'application garde sa reponse, que
    # son propre code sait lire. L'administrateur voit la vraie reponse.
    if (status >= 500 and request.method == "GET" and not est_admin()
            and request.headers.get("Sec-Fetch-Mode") == "navigate"):
        return page_inaccessible(name, a)
    sortants = [(k, v) for k, v in headers.items() if k.lower() not in HOP]
    # Le ruban n'est pose que pour quelqu'un de CONNECTE. Une application
    # publique vue par un visiteur anonyme ne doit pas lui annoncer qu'un
    # panneau existe derriere, ni lui offrir un lien qui le renverrait a une
    # page de connexion dont il n'a que faire.
    if is_authed():
        data, sortants = injecter_ruban(data, status, sortants)
        # Meme condition que le ruban : /api/icon exige une session, et un
        # visiteur anonyme n'a pas a voir d'adresse du panneau.
        data, sortants = injecter_icone(data, status, sortants, name)
    # Pour tout le monde, connecte ou non : une application publique doit
    # pouvoir s'installer aussi, et rien de ce qui est pose ici ne pointe
    # vers le panneau. Apres injecter_icone, qui renonce des qu'il voit une
    # apple-touch-icon -- et celle-ci en ajoute une.
    data, sortants = injecter_webapp(data, status, sortants, name,
                                     titre_de(name, a))
    data, sortants = injecter_environnement(data, status, sortants, is_authed())
    return Response(data, status, sortants)


def _from_referer():
    m = re.search(r"://[^/]+/([^/]+)/", request.headers.get("Referer", ""))
    if m and m.group(1) in load() and not request.path.startswith("/" + m.group(1) + "/"):
        return redirect("/" + m.group(1) + request.path, 302)
    return None


def _miss(name):
    return _from_referer() or Response(
        _page("Introuvable", "Aucune application \u00ab " + name + " \u00bb."),
        404, mimetype="text/html")


@flask_app.route("/<n>/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@flask_app.route("/<n>/<path:sub>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy(n, sub=""):
    return _proxy(n, sub) if n in load() else _miss(n)


@flask_app.route("/<n>")
def proxy_noslash(n):
    return redirect("/" + n + "/", 302) if n in load() else _miss(n)


@flask_app.errorhandler(404)
def not_found(e):
    return _miss(request.path.strip("/"))


def _page(title, msg, extra=""):
    """Une page d'erreur simple, servie par les DEUX origines.

    Elle echappe ses trois textes, et c'est la seule facon tenable : elle est
    appelee depuis le proxy avec un nom d'application qui vient de l'URL.
    « /zz<script>alert(1)</script>/ » ressortait tel quel dans la page --
    une XSS reflechie sur l'origine des applications. Elle n'y portait pas la
    session du panneau (c'est tout l'interet de la separation), mais elle
    permettait de se faire passer pour n'importe quelle application hebergee,
    dans son origine, devant l'utilisateur qui vient de cliquer.

    Echapper ICI plutot qu'a chaque appel : aucun appelant ne passe de balise
    volontairement, et la regle qui tient est celle qu'on ne peut pas oublier
    d'appliquer.
    """
    title, msg, extra = (str(x).replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;").replace('"', "&quot;")
                         for x in (title, msg, extra))
    return ("<!doctype html><meta charset=utf-8><title>" + title + "</title>"
            "<body style=\"margin:0;background:#0d1117;color:#c9d1d9;display:flex;"
            "align-items:center;justify-content:center;height:100vh;font:15px/1.6 "
            "-apple-system,Segoe UI,Roboto,sans-serif\"><div style=\"text-align:center\">"
            "<div style=\"font-size:17px;font-weight:600;color:#e6edf3;margin-bottom:6px\">"
            + title + "</div><div style=\"color:#8b949e\">" + msg + "</div>"
            "<div style=\"color:#6e7681;font-size:13px;margin-top:6px\">" + extra + "</div>"
            # Meme raison que pour le ruban : ces pages sortent aussi du
            # port des applications, ou "/" ne mene pas au hub.
            "<div style=\"margin-top:22px\"><a href=\"" + lien_panneau() + "\" style=\"color:#4c8eff;"
            "text-decoration:none;font-size:14px\">Retour au panneau</a></div></div>")


# ------------------- une application qu'on ne peut pas ouvrir -------------------
#
# DEUX PAGES, SELON QUI REGARDE. L'administrateur a besoin de la cause --
# arretee, plantee, muette sur son port -- et du chemin vers la fiche ou il
# la reparera. Un compte utilisateur, lui, ne doit JAMAIS savoir ce qui
# marche ou non : il apprend seulement que l'application est momentanement
# inaccessible, et qu'il peut revenir au hub. Pas un mot technique, pas de
# code, pas de port. Un visiteur anonyme d'une application publique est
# traite comme un utilisateur, sans lien vers le hub qu'il n'a pas.

def _echapper(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _page_application(titre, texte, initiale, boutons, pied=""):
    """La mise en page commune : une carte au centre, dans les couleurs du
    panneau, claires ou sombres selon le systeme. Aucun script : la page
    sort sur l'origine des applications, ou rien ne doit s'executer."""
    return ("<!doctype html><html lang=fr><meta charset=utf-8>"
            "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
            "<title>" + titre + "</title><style>"
            ":root{--bg:#E9EBEE;--carte:#fff;--txt:#12161C;--dim:#525C67;--ligne:#DADEE3;"
            "--accent:#0E7C86;--accent-h:#0B646C;--sur:#fff}"
            "@media(prefers-color-scheme:dark){:root{--bg:#0D1015;--carte:#161B22;--txt:#EEF1F4;"
            "--dim:#A0AAB5;--ligne:#262E38;--accent:#5FD4DC;--accent-h:#8FE3E9;--sur:#04161A}}"
            "body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);"
            "color:var(--txt);font:15px/1.55 Manrope,-apple-system,BlinkMacSystemFont,'Segoe UI',"
            "Roboto,sans-serif;padding:24px;box-sizing:border-box}"
            "main{width:100%;max-width:520px;background:var(--carte);border-radius:28px;"
            "padding:40px 36px 30px;box-shadow:0 20px 50px rgba(18,22,28,.10);text-align:center}"
            ".ini{width:76px;height:76px;margin:0 auto 18px;border-radius:24px;background:var(--accent);"
            "color:var(--sur);display:grid;place-items:center;font-size:32px;font-weight:700}"
            "h1{margin:0;font-size:24px;line-height:1.2;letter-spacing:-.03em}"
            "p{margin:12px 0 0;color:var(--dim)}"
            ".actions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:24px}"
            ".b{display:inline-flex;align-items:center;min-height:46px;padding:0 20px;border-radius:14px;"
            "border:1px solid var(--ligne);background:var(--carte);color:var(--txt);font-weight:700;"
            "text-decoration:none}.b:hover{border-color:var(--accent);color:var(--accent)}"
            ".p{background:var(--accent);border-color:var(--accent);color:var(--sur)}"
            ".p:hover{background:var(--accent-h);color:var(--sur)}"
            ".pied{margin-top:22px;padding-top:16px;border-top:1px solid var(--ligne);font-size:13px}"
            ".pied a{color:var(--accent);font-weight:700;text-decoration:none}"
            "</style><main><div class=ini aria-hidden=true>" + initiale + "</div>"
            "<h1>" + titre + "</h1><p>" + texte + "</p>"
            "<div class=actions>" + boutons + "</div>"
            + (("<div class=pied>" + pied + "</div>") if pied else "") + "</main></html>")


# Le dernier signalement, par application : un utilisateur qui recharge la
# page dix fois ne doit pas envoyer dix mails. Une heure entre deux.
_inaccessibles_signales = {}
INACCESSIBLE_RAPPEL = 3600


def prevenir_admin_inaccessible(name, titre):
    """Note qu'un compte n'a pas pu ouvrir l'application, et previent
    l'administrateur (au plus une fois par heure et par application).

    Renvoie True quand l'administrateur EST prevenu -- par ce signalement ou
    par l'alerte de chute deja partie -- et seulement alors : la page ne
    doit pas promettre un mail qui ne partira pas faute de destinataire.
    """
    qui = utilisateur_courant() if is_authed() else "visiteur"
    journaliser("inaccessible", app=name, qui=qui, ip=_adresse_client())
    try:
        joignable = bool(lire_alertes()["actif"] and alertes_admin())
    except Exception:
        joignable = False
    if not joignable:
        return False
    if name in _alertes_en_cours:
        return True
    maintenant = time.time()
    if maintenant - _inaccessibles_signales.get(name, 0) < INACCESSIBLE_RAPPEL:
        return True
    _inaccessibles_signales[name] = maintenant
    corps = "\n".join([
        f"Quelqu'un a voulu ouvrir « {titre} » et ne l'a pas pu.",
        "",
        f"Compte    : {qui}",
        f"Panneau   : {lien_panneau()}applications/{name}",
        "",
        "Il a vu une page « momentanément inaccessible », sans détail.",
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])
    # En arriere-plan : un serveur d'envoi lent ne doit pas faire attendre
    # la page.
    threading.Thread(target=alerter, args=(f"[CodeLab] {titre} : ouverture impossible", corps),
                     daemon=True).start()
    return True


def page_inaccessible(name, a, code=503):
    """Ce que voit un compte utilisateur (ou un visiteur) quand
    l'application ne peut pas s'ouvrir. Aucune cause, aucun etat."""
    titre = titre_de(name, a)
    # Un APERCU (le widget du hub l'encadre) n'est pas une ouverture : sans
    # ce tri, chaque affichage du hub d'un compte enverrait un mail.
    encadre = request.headers.get("Sec-Fetch-Dest") in ("iframe", "frame")
    prevenu = False if encadre else prevenir_admin_inaccessible(name, titre)
    t = _echapper(titre)
    boutons = '<a class="b" href="">Réessayer</a>'
    pied = ""
    if is_authed():
        boutons = ('<a class="b p" href="' + _echapper(lien_panneau()) + '">Retour au hub</a>'
                   + boutons)
        pied = (("L'administrateur est prévenu. " if prevenu else "")
                + '<a href="' + _echapper(lien_panneau()) + 'messages">Lui laisser un message</a>')
    html = _page_application(t + " est momentanément inaccessible",
                             "Elle revient très vite. Réessaie dans quelques minutes.",
                             _echapper(titre[:1].upper() or "?"), boutons, pied)
    r = Response(html, code, mimetype="text/html")
    r.headers["Retry-After"] = "60"
    r.headers["Cache-Control"] = "no-store"
    return r


def page_panne_admin(name, a, cause, detail="", code=503):
    """Ce que voit l'administrateur : la cause, et le chemin vers la fiche."""
    titre = _echapper(titre_de(name, a))
    fiche = _echapper(lien_panneau() + "applications/" + name)
    boutons = ('<a class="b p" href="' + fiche + '">Ouvrir sa fiche</a>'
               '<a class="b" href="">Réessayer</a>')
    pied = "Un compte utilisateur voit seulement « momentanément inaccessible »."
    html = _page_application(titre + " " + _echapper(cause), _echapper(detail),
                             _echapper(titre_de(name, a)[:1].upper() or "?"), boutons, pied)
    r = Response(html, code, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


def page_application_fermee(name, a, muette=False):
    """L'application ne repond pas : la page qui convient a qui regarde.

    502 quand le processus tourne mais que son port ne repond pas (la
    passerelle n'a rien obtenu), 503 quand il n'y a pas de processus."""
    code = 502 if muette else 503
    if not est_admin():
        return page_inaccessible(name, a, code)
    if muette:
        return page_panne_admin(name, a, "ne répond pas encore",
                                "Elle est lancée mais n'écoute pas encore sur son port. "
                                "Réessaie dans quelques secondes.", code)
    if is_crash_looping(name) or a.get("dernier_echec"):
        return page_panne_admin(name, a, "s'est arrêtée",
                                "Elle a planté au lancement. La cause est dans sa fiche, "
                                "avec la fin du journal.")
    return page_panne_admin(name, a, "est arrêtée",
                            "Démarre-la depuis sa fiche.")


# --------------------- inscription du projet de diagnostic ---------------------
#
# Le conteneur dagster depose le projet "diagnostic" dans /workspace au premier
# demarrage de la stack ; il fallait ensuite l'ajouter a la main dans le
# panneau, en recopiant deux commandes depuis son README. C'est la seule etape
# manuelle qui separait une stack fraiche d'une stack verifiee -- et c'est
# precisement celle qu'on saute quand on est presse, donc celle qui manque le
# jour ou quelque chose ne marche pas.
#
# Le projet est donc inscrit tout seul, et il l'est a CHAQUE demarrage --
# voir amorcer_diagnostic(), qui dit pourquoi ce n'est plus une amorce jouee
# une seule fois. Une seule condition subsiste : le dossier doit exister.
# app-manager et dagster demarrent en parallele, c'est dagster qui amorce
# /workspace, et on ne peut pas inscrire un chemin qui n'est pas la.

# Les deux commandes du README du projet, pas une detection automatique :
# detect_project() proposerait bien "python3 app.py", mais rendrait une
# commande de build vide (il cherche un requirements.txt, que ce projet n'a
# pas), et l'application demarrerait sans pilote Postgres.
DIAGNOSTIC_COMMANDE = "python3 app.py"
DIAGNOSTIC_BUILD = 'pip install --target vendor "psycopg[binary]"'
# L'ANCIENNE APPLICATION AICHAT. Elle etait inscrite ici, copiee dans
# /workspace et lancee par le panneau. CodeLab n'heberge plus d'interface de
# chat du tout : plus rien a inscrire, plus rien a copier, plus de port a
# attribuer. Les installations qui la portent encore la voient retiree du
# registre au demarrage -- voir retirer_aichat().
AICHAT_ANCIEN_NOM = "aichat"


def retirer_aichat():
    """Retire l'ancienne application AIChat du registre, une fois.

    Le dossier /workspace/aichat, lui, n'est PAS touche : c'est du code, il
    appartient a l'utilisateur, et le supprimer serait une perte de donnees
    silencieuse. Seule l'inscription disparait -- la tuile pointait vers une
    application qui ne peut plus rien faire.
    """
    apps = load()
    if AICHAT_ANCIEN_NOM not in apps:
        return None
    stop(AICHAT_ANCIEN_NOM)
    apps = load()
    apps.pop(AICHAT_ANCIEN_NOM, None)
    save(apps)
    print("[app-manager] AIChat retire du registre : CodeLab n'heberge plus "
          "d'interface de chat. Le dossier /workspace/aichat n'a pas ete "
          "touche.", flush=True)
    return AICHAT_ANCIEN_NOM


def amorcer_diagnostic():
    """Inscrit le projet de diagnostic et le configure. A CHAQUE demarrage.

    CE QUI A CHANGE. L'inscription etait une amorce a jouer UNE FOIS : elle
    ne se declenchait que sur un registre vide, et un marqueur sur le disque
    l'empechait de se rejouer. Trois facons de se retrouver sans diagnostic
    en decoulaient, et aucune ne se voyait :

      - le dossier n'etait pas encore la (dagster amorce /workspace, et les
        deux conteneurs demarrent en parallele) et une autre application
        avait ete inscrite entre-temps : plus jamais de diagnostic ;
      - le marqueur avait ete pose sans que l'inscription reussisse ;
      - une installation d'avant l'existence du diagnostic gardait un
        registre non vide, donc ne l'a jamais recu.

    Ce n'est plus une amorce, c'est un invariant : le diagnostic est une
    application PAR DEFAUT, au meme titre que Dagster. Elle ne se supprime
    pas (est_app_par_defaut la protege), ne se configure pas, ne s'arrete
    pas -- le marqueur qui evitait de la reinscrire n'a donc plus rien a
    eviter.

    Renvoie le nom si l'inscription vient d'avoir lieu (il reste a builder
    et a lancer), None si elle etait deja la ou si le dossier manque encore.
    """
    chemin = os.path.join(ROOT, DIAGNOSTIC_NOM)
    if not os.path.isfile(os.path.join(chemin, "app.py")):
        # Pas encore amorce par dagster : on retentera au prochain demarrage.
        # C'est maintenant vrai a chaque fois, et non plus seulement tant que
        # le registre reste vide.
        return None

    apps = load()
    a = apps.get(DIAGNOSTIC_NOM)
    if a is not None:
        # Deja inscrite : on ne recree rien, on remet seulement ce qui doit
        # etre vrai. Le chemin et la commande ne sont pas configurables pour
        # cette application-la ; les laisser deriver n'apporterait rien.
        change = False
        for cle, valeur in (("path", chemin),
                            ("command", DIAGNOSTIC_COMMANDE),
                            ("build_command", DIAGNOSTIC_BUILD)):
            if a.get(cle) != valeur:
                a[cle] = valeur
                change = True
        if not a.get("port"):
            a["port"] = next_port(apps)
            change = True
        if change:
            save(apps)
        return None

    apps[DIAGNOSTIC_NOM] = {
        "titre": TITRES_PAR_DEFAUT[DIAGNOSTIC_NOM],
        "path": chemin,
        "command": DIAGNOSTIC_COMMANDE,
        "port": next_port(apps),
        "enabled": True,
        # La commande de build EST la configuration par defaut : sans elle,
        # la fiche n'ayant pas de formulaire, le pilote Postgres ne
        # s'installait sur ordre de personne.
        "build_command": DIAGNOSTIC_BUILD,
        "max_memory_mb": None,
        "description": DIAGNOSTIC_DESCRIPTION,
        # Privee : la page nomme les conteneurs, l'utilisateur SSH et l'etat de
        # la base. Rien de secret, mais rien non plus a offrir a un visiteur
        # anonyme le jour ou le port 9001 est publie. L'utilisateur peut la
        # rendre publique en un clic depuis le panneau.
        "visibility": VISIBILITE_PRIVEE,
    }
    save(apps)
    return DIAGNOSTIC_NOM


def _preparer_diagnostic(name):
    """Build puis demarrage, en tache de fond.

    Le build installe le pilote Postgres (quelques secondes a quelques
    dizaines, et un acces reseau) : le faire dans le thread principal
    retarderait d'autant l'ouverture du panneau, c'est-a-dire la seule
    interface depuis laquelle on peut constater ce qui se passe.

    Un build en echec (pas de reseau, miroir pip injoignable) n'empeche pas le
    demarrage : l'application affiche alors "aucun pilote Postgres" sur la
    ligne concernee et toutes les autres sondes repondent normalement. Une
    page qui explique ce qui manque vaut mieux qu'une application absente.

    LE BUILD MANQUAIT. Ce texte decrivait « build puis demarrage » depuis le
    debut, et la fonction ne faisait que demarrer : la commande de build
    n'etait meme pas inscrite. Le pilote Postgres ne s'installait donc sur
    ordre de personne, et la page de diagnostic annoncait son absence sur une
    installation neuve, sans que rien ne dise quoi faire.
    """
    try:
        ok, message = run_build(name)
        if not ok:
            print(f"[app-manager] {name} : build initial en echec ({message}). "
                  f"L'application demarre quand meme.", flush=True)
    except Exception as e:
        print(f"[app-manager] {name} : build initial impossible ({e}).", flush=True)
    try:
        start(name)
    except Exception as e:
        print(f"[app-manager] {name} : echec du demarrage initial ({e}).", flush=True)


# ------------------------------ serveur HTTP ------------------------------
#
# Le serveur de developpement de Flask affiche lui-meme un avertissement, et
# il est merite : ce panneau ne sert pas que ses propres pages, il relaie TOUT
# le trafic de toutes les applications deployees. waitress est un serveur WSGI
# de production, en Python pur, sans configuration -- le remplacement le moins
# couteux possible, et le comportement est identique cote application.
#
# Repli sur le serveur de Flask si waitress n'est pas installe : le service
# doit rester lancable depuis un depot fraichement clone, sans rien installer
# de plus que Flask.

def _ouvrir_port_des_applications(servir_sur):
    """Le second ecouteur, celui des applications.

    Ouvert AVANT le port du panneau et de maniere synchrone jusqu'a ce qu'on
    sache s'il tient : c'est ce resultat qui decide si la separation des
    origines est active, et le panneau doit le savoir des sa premiere reponse.

    S'il ne s'ouvre pas -- port deja pris, non publie par le compose -- on ne
    separe rien et les applications restent servies par le panneau, comme
    avant. Une stack qui marche moins bien vaut mieux qu'une stack morte, et
    le panneau le dit dans le diagnostic.
    """
    if not APPS_PORT:
        print("[app-manager] APP_MANAGER_APPS_PORT=0 : les applications "
              "restent servies sur le port du panneau.", flush=True)
        return
    pret = threading.Event()
    souci = {}

    def _servir():
        try:
            import socket as _s
            sonde = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
            sonde.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
            sonde.bind(("0.0.0.0", APPS_PORT))
            sonde.close()
        except OSError as e:
            souci["erreur"] = e
            pret.set()
            return
        pret.set()
        servir_sur(APPS_PORT)

    threading.Thread(target=_servir, daemon=True).start()
    pret.wait(timeout=10)
    if souci:
        print(f"[app-manager] port {APPS_PORT} indisponible ({souci['erreur']}) : "
              f"les applications restent servies sur le port du panneau. "
              f"Publie ce port dans docker-compose.yml pour les isoler.", flush=True)
        return
    _origines_separees["actif"] = True
    print(f"[app-manager] applications isolees sur 0.0.0.0:{APPS_PORT} "
          f"-- origine distincte de celle du panneau.", flush=True)


def servir(port):
    # Le cookie de session suit le reglage d'exposition. Pose ici et non a
    # l'import : STATE_DIR peut etre redirige (tests, autre installation),
    # et lire le fichier trop tot donnerait la valeur du mauvais endroit.
    appliquer_cookie_securise()

    try:
        from waitress import serve
        def _sur(p):
            # ident : l'en-tete Server annonce "CodeLab" plutot que la version
            # exacte de waitress, qui ne renseigne que celui qui cherche une
            # faille connue.
            serve(flask_app, host="0.0.0.0", port=p, threads=WSGI_THREADS,
                  channel_timeout=WSGI_TIMEOUT, ident="CodeLab")
    except ImportError:
        print("[app-manager] waitress absent : repli sur le serveur de "
              "developpement de Flask (a eviter en service).", flush=True)
        def _sur(p):
            flask_app.run(host="0.0.0.0", port=p, threaded=True)

    # Dans les DEUX cas : le repli sans waitress doit separer les origines
    # comme le service normal. Le premier jet ne le faisait pas -- il rendait
    # la main avant -- et une installation sans waitress se serait retrouvee
    # avec des applications dans l'origine du panneau, sans que rien ne le
    # dise.
    _ouvrir_port_des_applications(_sur)
    print(f"[app-manager] panneau sur 0.0.0.0:{port}.", flush=True)
    _sur(port)


# -------------------------------- main --------------------------------

if __name__ == "__main__":
    bootstrap_secrets()
    bootstrap_sso()
    importer_anciens_fichiers()
    migrer_vers_le_coffre()
    signaler_adresses_en_conflit()
    publier_relais_mail()
    os.makedirs(LOG_DIR, exist_ok=True)
    inscrit = amorcer_diagnostic()
    # AVANT resume() : les applications survivent a l'arret du panneau, et
    # relancer par-dessus une application deja en vie ferait mourir la
    # nouvelle sur un port occupe -- sans que personne ne comprenne pourquoi.
    adopter_processus_survivants()
    resume()
    retirer_aichat()
    if inscrit:
        threading.Thread(target=_preparer_diagnostic, args=(inscrit,),
                         daemon=True).start()
    start_monitor_thread()
    demarrer_miroir_pg()
    demarrer_les_sauvegardes()
    demarrer_la_surveillance_externe()
    servir(int(os.environ.get("MANAGER_PORT", "9001")))

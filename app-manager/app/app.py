"""
CodeLab app-manager -- panneau de controle + reverse proxy, sur un port unique.

  http://<IP>:9001/            panneau CodeLab (protege par mot de passe)
  http://<IP>:9001/login       page de connexion
  http://<IP>:9001/<projet>/   le projet, servi via proxy interne (pas protege)
  http://<IP>:9001/health      sonde du HEALTHCHECK Docker (pas protege)

Autonome : aucune dependance a Supervisor. Les processus sont geres ici.
Dependances externes : Flask, psutil.

Les chemins sont pilotes par variables d'environnement pour rester alignes
sur les points de montage declares dans docker-compose.yml :
  data/app-manager   -> /var/lib/codelab/app-manager  (APP_MANAGER_STATE)
  workspace          -> /workspace                    (APP_MANAGER_ROOT)
  config (partage)   -> /var/lib/codelab/config       (APP_MANAGER_SHARED_CONFIG)
Le code est en lecture seule ; apps.json et les journaux vivent dans
STATE_DIR. Aucun secret n'y est stocke : le mot de passe admin et la cle
de session sont lus et ecrits dans credentials.env, le fichier unique
d'identifiants CodeLab, consultable directement depuis le disque de
l'hote. Les anciens fichiers admin_password / flask_secret_key sont
repris puis supprimes au premier demarrage.

Fonctionnalites de fiabilite/observabilite/deploiement ajoutees :
  - redemarrage automatique en cas de crash (plafonne, voir monitor_tick)
  - rotation des journaux (2 Mo, un seul fichier .1 conserve)
  - vrai bouton "Redemarrer" (stop puis start), distinct du toggle
  - historique de metriques en memoire (~2.5 min) + mini-graphiques SVG
  - recherche dans les logs en direct (filtre cote client)
  - "Lancer le build" (commande optionnelle, separee du lancement)
  - limite memoire optionnelle par app (RLIMIT_AS via preexec_fn)

Le panneau n'ecrit jamais dans /workspace : il n'y cree aucun projet et
n'y depose aucun fichier. Les dossiers sont crees par l'utilisateur (SSH,
VS Code, git clone) puis simplement declares ici.
"""
import base64
import datetime
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
import socket
import ssl
import stat as stat_mod
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from email.message import EmailMessage

import psutil
from flask import (Flask, Response, jsonify, redirect, request, session, send_file,
                   stream_with_context)

STATE_DIR = os.environ.get("APP_MANAGER_STATE", "/var/lib/codelab/app-manager")
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
LOGIN_PAGE = _lire_ressource("login.html")
# Le theme : un seul fichier de variables, partage par les deux pages. Servi
# plutot que recopie dans chacune, pour qu'il n'existe qu'un endroit a changer
# et qu'aucune des deux ne puisse deriver de l'autre.
THEME_CSS = _lire_ressource("theme.css")

# Les seuls fichiers que /polices/<nom> accepte de servir.
POLICES_SERVIES = {"manrope-latin.woff2"}


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
    # Duree explicite : session.permanent sans cette valeur laisse le defaut
    # de Flask, 31 jours.
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=7),
)
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
        "# APP_MANAGER_ADMIN_PASSWORD : mot de passe de connexion au panneau.",
        "# APP_MANAGER_SESSION_SECRET : cle de signature des sessions -- la",
        "#   changer deconnecte tout le monde ; ne jamais la partager.",
    ]
    valeurs = {
        "APP_MANAGER_URL": "http://<IP-du-serveur>:9001",
        "APP_MANAGER_ADMIN_PASSWORD": pw,
        "APP_MANAGER_SESSION_SECRET": key,
    }
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
    if trust_proxy():
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
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


def is_authed():
    return session.get("authed") is True


def _refus(message, code):
    """Refus coherent entre l'API et les pages : un appel fetch() veut un
    code et un message, un clic dans le navigateur veut la page de
    connexion."""
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), code
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

# Derriere un reverse proxy, un second PORT n'est pas forcement joignable de
# l'exterieur : on declare alors l'adresse complete (un sous-domaine, par
# exemple https://apps.tondomaine.fr). Elle prend le pas sur le port.
APPS_URL = (os.environ.get("APP_MANAGER_APPS_URL") or "").rstrip("/")

# Pose par servir() une fois le second ecouteur reellement ouvert. Tant qu'il
# est faux, rien ne change : ni redirection, ni restriction.
_origines_separees = {"actif": False}


def origines_separees():
    return _origines_separees["actif"]


def origine_applications():
    """L'adresse ou vivent les applications, vue depuis le navigateur."""
    if APPS_URL:
        return APPS_URL
    hote = (request.host or "").split(":")[0] if request else ""
    return f"{request.scheme}://{hote}:{APPS_PORT}"


ROUTES_APPLICATIONS = {"proxy", "proxy_noslash", "health"}


@flask_app.before_request
def separer_les_origines():
    """Chaque port ne sert que ce qui lui appartient.

    Sans cette garde, ouvrir le second port ne separerait rien : le panneau
    repondrait sur les deux, et les deux origines se vaudraient.
    """
    if not origines_separees():
        return None
    sur_le_port_des_apps = str(request.environ.get("SERVER_PORT", "")) == str(APPS_PORT)
    if sur_le_port_des_apps:
        if request.endpoint in ROUTES_APPLICATIONS or request.endpoint is None:
            return None
        # Le panneau ne s'affiche pas ici, et ses API n'y repondent pas : ce
        # port n'est pas de confiance, c'est tout l'interet.
        return Response(_page("Ce n'est pas le panneau",
                              "Cette adresse ne sert que les applications."),
                        404, mimetype="text/html")
    # Sur le port du panneau : une application demandee ici est renvoyee chez
    # elle. Les favoris et les liens deja partages continuent de marcher.
    if request.endpoint in ("proxy", "proxy_noslash"):
        nom = request.view_args.get("n", "") if request.view_args else ""
        sous = request.view_args.get("sub", "") if request.view_args else ""
        cible = origine_applications() + "/" + urllib.parse.quote(nom) + "/"
        if sous:
            cible += sous
        if request.query_string:
            cible += "?" + request.query_string.decode("latin-1")
        return redirect(cible, 302)
    return None


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
    "inscription_creer", "inscription_confirmer",
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
UTILISATEURS_FILE = os.path.join(STATE_DIR, "utilisateurs.json")

# Nom reserve : le compte d'administration n'est pas dans ce fichier, son mot
# de passe vit dans credentials.env. Laisser creer un utilisateur "admin"
# donnerait deux comptes pour un seul nom, et l'un masquerait l'autre.
NOM_ADMIN = "admin"

ROLE_ADMIN = "admin"
ROLE_UTILISATEUR = "utilisateur"

# PBKDF2-HMAC-SHA256. Pas de dependance a ajouter (hashlib est dans la
# bibliotheque standard), et un cout de calcul qui rend une liste de mots de
# passe voles inexploitable en pratique. 200 000 iterations : quelques
# dizaines de millisecondes ici, des annees pour qui essaie un dictionnaire.
PBKDF2_ITERATIONS = 200_000


def lire_utilisateurs():
    """Le registre des comptes. Jamais d'exception : un fichier illisible ne
    doit pas empecher l'administrateur de se connecter pour le reparer."""
    try:
        with open(UTILISATEURS_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def ecrire_utilisateurs(comptes):
    tmp = UTILISATEURS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(comptes, f, indent=2)
    os.replace(tmp, UTILISATEURS_FILE)
    try:
        # Meme si le contenu est derive, ce fichier dit qui existe : il n'a
        # aucune raison d'etre lisible par les applications lancees.
        os.chmod(UTILISATEURS_FILE, 0o600)
    except OSError:
        pass
    # APRES l'ecriture, jamais avant : le miroir relit le fichier pour le
    # recopier, et prendrait sinon l'etat d'avant la modification.
    _pg_deposer(("utilisateurs", None))


def derive_mot_de_passe(mot_de_passe, sel):
    return hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(),
                               bytes.fromhex(sel), PBKDF2_ITERATIONS).hex()


def verifie_mot_de_passe(compte, mot_de_passe):
    try:
        attendu = compte["hash"]
        calcule = derive_mot_de_passe(mot_de_passe, compte["sel"])
    except (KeyError, TypeError, ValueError):
        return False
    return secrets.compare_digest(calcule, attendu)


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
                 destinataires=[adresse])


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
    annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
    if trust_proxy() and annonce:
        return hote, annonce, False
    return hote, request.scheme, (annonce == "https" and request.scheme != "https")


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
                            "coche \"Proxy de confiance\" dans Exposition.")
        return "", "", ("Les cles d'acces exigent une connexion HTTPS : le navigateur "
                        "refuse de les creer en clair. Mets le TLS en place, puis "
                        "reviens ici.")
    # Une adresse IP ne peut pas servir de "relying party id" : la norme
    # exige un nom de domaine. C'est la meme exigence que le certificat.
    if re.fullmatch(r"[0-9.]+|\[[0-9a-fA-F:]+\]", hote) and not local:
        return "", "", ("Les cles d'acces exigent un nom de domaine, pas une adresse IP. "
                        "Ouvre le panneau par son nom (celui du certificat).")
    return hote, origine, ""


def lire_passkeys():
    try:
        with open(PASSKEYS_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def ecrire_passkeys(tout):
    tmp = PASSKEYS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tout, f, indent=2)
    os.replace(tmp, PASSKEYS_FILE)
    try:
        os.chmod(PASSKEYS_FILE, 0o600)
    except OSError:
        pass
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
ACCES_MAX_OCTETS = 1024 * 1024      # 1 Mo, soit ~8 000 evenements
ACCES_LIGNES_LUES = 400             # ce que l'interface affiche au plus
# Une page web, c'est des dizaines de requetes. Une ouverture par personne et
# par application n'est donc notee qu'une fois par quart d'heure : au-dela on
# ne journalise plus une visite, on journalise le HTML.
ACCES_REGROUPEMENT = 900

_dernier_acces = {}
_acces_verrou = threading.Lock()


def _adresse_client():
    """L'adresse du visiteur, selon qu'on est derriere un proxy de confiance."""
    if trust_proxy():
        avant = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if avant:
            return avant
    return request.remote_addr or ""


def journaliser(genre, **details):
    """Ajoute un evenement. N'echoue jamais : journaliser n'est pas le travail.

    Une ecriture impossible (disque plein, montage en lecture seule) ne doit
    ni refuser une connexion ni casser le proxy -- ce serait faire tomber le
    service pour proteger son journal.
    """
    # Un identifiant par evenement : c'est lui qui rend la copie vers
    # Postgres rejouable. Sans lui, un rattrapage apres une coupure de la
    # base insererait deux fois les memes lignes.
    evenement = {"id": secrets.token_hex(12), "ts": int(time.time()), "genre": genre}
    evenement.update(details)
    _pg_deposer(("acces", evenement))
    try:
        with _acces_verrou:
            if (os.path.exists(ACCES_FILE)
                    and os.path.getsize(ACCES_FILE) > ACCES_MAX_OCTETS):
                os.replace(ACCES_FILE, ACCES_FILE + ".1")
            with open(ACCES_FILE, "a") as f:
                f.write(json.dumps(evenement, ensure_ascii=False) + "\n")
    except OSError:
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
        cx.execute("CREATE INDEX IF NOT EXISTS acces_ts ON acces (ts DESC)")
        cx.execute("CREATE INDEX IF NOT EXISTS acces_qui ON acces (qui, ts DESC)")
        cx.execute("CREATE INDEX IF NOT EXISTS acces_app ON acces (application, ts DESC)")
        # Aucun secret ici : ni empreinte de mot de passe, ni sel, ni cle du
        # second facteur, ni cle d'acces. Cette base est joignable par les
        # projets deployes -- elle ne porte que ce qui se lit deja dans le
        # panneau.
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
            """, (nom, c.get("email") or "", bool(c.get("email_verifie")),
                  bool(c.get("attente_email")), bool(c.get("totp")),
                  len(cles.get(nom, [])), sorted(c.get("projets", [])),
                  datetime.datetime.fromtimestamp(c.get("cree") or 0,
                                                  datetime.timezone.utc)))
        cur.execute("""UPDATE utilisateurs SET supprime = now(), maj = now()
                        WHERE supprime IS NULL AND NOT (nom = ANY(%s))""",
                    (list(comptes),))


def _pg_rattraper(cx):
    """Rejoue le fichier en entier : ce qui manque entre, le reste glisse.

    Appele au demarrage et apres chaque reconnexion. Le fichier est plafonne
    a 1 Mo, donc c'est quelques milliers de lignes -- et ON CONFLICT DO
    NOTHING rend l'operation sans consequence quand tout est deja la.
    """
    _pg_ecrire_acces(cx, lire_acces(limite=100000))
    _pg_ecrire_utilisateurs(cx)


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
                    elif genre == "utilisateurs":
                        _pg_ecrire_utilisateurs(cx)
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


def lire_acces(limite=ACCES_LIGNES_LUES, app=None, qui=None):
    """Les evenements les plus recents d'abord.

    Lit le fichier en entier : a 1 Mo plafonne, c'est un coup de disque
    negligeable, et cela evite un index a tenir a jour pour une page qu'on
    ouvre trois fois par mois.
    """
    lignes = []
    for chemin in (ACCES_FILE, ACCES_FILE + ".1"):
        try:
            with open(chemin) as f:
                lignes.extend(f.readlines())
        except OSError:
            continue
    evenements = []
    for ligne in reversed(lignes):
        try:
            e = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if app is not None and e.get("app") != app:
            continue
        if qui is not None and (e.get("qui") or "") != qui:
            continue
        evenements.append(e)
        if len(evenements) >= limite:
            break
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


def projets_autorises():
    """Les projets que la session peut ouvrir. None = tous (administrateur)."""
    if est_admin():
        return None
    compte = lire_utilisateurs().get(utilisateur_courant())
    return set(compte.get("projets", [])) if compte else set()


def peut_voir(name):
    """La session a-t-elle le droit d'ouvrir cette application ?

    Une application publique est ouverte a tous, y compris a un visiteur non
    connecte -- c'est le sens de "publique", et c'est ce qui permet de
    partager un projet par un simple lien. Le controle par compte ne concerne
    donc que les applications privees.
    """
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


def load():
    try:
        st = os.stat(APPS_FILE)
        signature = (st.st_mtime_ns, st.st_size)
    except OSError:
        # Fichier absent : cas normal au tout premier demarrage.
        return {}
    if _apps_cache["signature"] != signature:
        try:
            with open(APPS_FILE) as f:
                _apps_cache["apps"] = json.load(f)
            _apps_cache["signature"] = signature
        except Exception:
            # JSON tronque ou illisible : on ne met pas le cache a jour, la
            # prochaine lecture reessaiera. Renvoyer un registre vide ici
            # arreterait toutes les applications au tour de moniteur suivant.
            return _apps_cache["apps"]
    # Copie : les appelants modifient librement ce qu'ils recoivent avant de
    # le repasser a save(), et une reference partagee ferait apparaitre ces
    # modifications avant meme l'ecriture -- y compris si elle echoue.
    #
    # Un seul niveau, pas un deepcopy : les valeurs sont des dictionnaires
    # plats (chemin, commande, port, etat). Mesure sur 8 applications et
    # 20 000 appels : 24 ms ainsi, 570 ms avec deepcopy, 347 ms sans cache du
    # tout -- le deepcopy rendait l'optimisation PLUS lente que le code
    # qu'elle remplacait.
    return {nom: dict(a) if isinstance(a, dict) else a
            for nom, a in _apps_cache["apps"].items()}


def save(apps):
    tmp = APPS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(apps, f, indent=2)
    os.replace(tmp, APPS_FILE)
    try:
        st = os.stat(APPS_FILE)
        _apps_cache["apps"] = {nom: dict(a) if isinstance(a, dict) else a
                               for nom, a in apps.items()}
        _apps_cache["signature"] = (st.st_mtime_ns, st.st_size)
    except OSError:
        _apps_cache["signature"] = None   # forcera une relecture


def next_port(apps):
    used = {a["port"] for a in apps.values()}
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p not in used:
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
                        or cle in CLES_RESERVEES or cle in CLES_PANNEAU):
                    continue
                if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
                    valeur = valeur[1:-1]
                valeurs[cle] = valeur
    except OSError:
        # Volume config non monte, ou fichier pas encore ecrit : les
        # applications se debrouillent avec leur propre .env, comme avant.
        pass
    return valeurs


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

    os.makedirs(LOG_DIR, exist_ok=True)
    rotate_log_if_needed(name)
    out = open(os.path.join(LOG_DIR, name + ".log"), "ab", buffering=0)
    env = dict(os.environ, **secrets_partages())
    env.update(PORT=str(a["port"]), PYTHONUNBUFFERED="1",
               HOME=ensure_child_home(name))
    argv, env_isolement = commande_isolee(name, a["path"], a["command"], apps)
    env.update(env_isolement)

    with lock:
        procs[name] = subprocess.Popen(
            argv,
            cwd=a["path"], env=env, stdout=out, stderr=out,
            start_new_session=True,
            preexec_fn=child_setup(a.get("max_memory_mb"), name))
    apps[name]["enabled"] = True
    save(apps)

    if not attendre:
        return None

    # On regarde l'application vivre un instant. Sans cela, "demarree" veut
    # seulement dire "Popen n'a pas leve d'exception" -- ce qui reste vrai
    # d'une commande qui meurt a la ligne suivante.
    fin = time.time() + DELAI_DEMARRAGE
    while time.time() < fin:
        if procs[name].poll() is not None:
            code = procs[name].returncode
            procs.pop(name, None)
            apps = load()
            if name in apps:
                apps[name]["enabled"] = False
                save(apps)
            return (f"L'application s'est arretee aussitot (code {code}). "
                    + derniere_ligne_utile(name))
        time.sleep(0.05)
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


def stop(name):
    p = procs.get(name)
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            for _ in range(30):
                if p.poll() is not None:
                    break
                time.sleep(0.1)
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    if p:
        for pid in list(_proc_cache):
            try:
                if _proc_cache[pid].pid == p.pid or True:
                    pass
            except Exception:
                pass
    procs.pop(name, None)
    _restart_history.pop(name, None)  # arret volontaire : on oublie l'historique de crash
    apps = load()
    if name in apps:
        apps[name]["enabled"] = False
        save(apps)


def resume():
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
            # Sonde a la suite du redemarrage automatique, dans le meme
            # thread : une app qui vient d'etre relancee n'ecoute pas encore,
            # et la sonder au tour suivant evite un faux "ne repond pas" a
            # chaque redemarrage.
            try:
                probe_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans la sonde d'ecoute : {e}", flush=True)
            # Les alertes en dernier, et dans le meme thread : un envoi SMTP
            # peut prendre jusqu'a 20 secondes, mais il n'a lieu qu'une fois
            # l'incident deja constate -- le redemarrage automatique a donc
            # deja eu lieu, et rien d'urgent n'attend derriere.
            try:
                alerte_tick()
            except Exception as e:
                print(f"[app-manager] erreur dans les alertes : {e}", flush=True)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


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
    """Reglages non secrets. Jamais d'exception : une configuration illisible
    ne doit pas empecher le panneau de demarrer ni le moniteur de tourner."""
    try:
        with open(ALERTES_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {
        "actif": bool(d.get("actif")),
        "destinataires": [a for a in d.get("destinataires", []) if isinstance(a, str) and a.strip()],
    }


def ecrire_alertes(actif, destinataires):
    tmp = ALERTES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"actif": bool(actif), "destinataires": list(destinataires)}, f, indent=2)
    os.replace(tmp, ALERTES_FILE)


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
        "# ALERTE_ADMIN : l'adresse qui recoit TOUTES les alertes, en plus de",
        "#   celles propres a chaque application.",
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
    try:
        with open(SMTP_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not str(d.get("host") or "").strip():
        return None
    return _normalise_smtp(d)


def ecrire_smtp_personnalise(cfg):
    tmp = SMTP_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SMTP_FILE)
    try:
        # Il contient un mot de passe : meme regime que credentials.env.
        os.chmod(SMTP_FILE, 0o600)
    except OSError:
        pass


def effacer_smtp_personnalise():
    """Revenir a la configuration d'origine. Toujours permis -- c'est la
    marche arriere, et elle ne doit dependre d'aucune condition."""
    try:
        os.remove(SMTP_FILE)
    except OSError:
        pass


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
    depuis_bloc = _adresses(read_shared_value("ALERTE_ADMIN") or "")
    return depuis_bloc or _adresses(lire_alertes()["destinataires"])


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


def envoyer_mail(cfg, sujet, corps, destinataires=None):
    """Envoie, ou leve. Les trois modes de chiffrement du SMTP."""
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = cfg["expediteur"]
    msg["To"] = ", ".join(destinataires if destinataires is not None
                          else cfg["destinataires"])
    msg.set_content(corps)

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
    propres = _adresses((apps.get(name) or {}).get("alertes"))
    vus, tout = set(), []
    for adresse in alertes_admin() + propres:
        if adresse not in vus:
            vus.add(adresse)
            tout.append(adresse)
    return tout


def envoyer_avec_repli(sujet, corps, destinataires):
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
            envoyer_mail(cfg, sujet, corps, destinataires=destinataires)
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
        f"L'application « {name} » ne repond plus.",
        "",
        f"Dossier   : {a.get('path', '?')}",
        f"Commande  : {a.get('command', '?')}",
        f"Port      : {a.get('port', '?')}",
        f"Etat      : arretee apres {RESTART_MAX_ATTEMPTS} tentatives de "
        f"redemarrage en {RESTART_WINDOW // 60} minutes",
        "",
        f"Panneau   : {read_shared_value('APP_MANAGER_URL') or 'http://<IP-du-serveur>:9001'}/",
        "",
        f"Fin du journal ({ALERTE_LOG_LIGNES} dernieres lignes)",
        "-" * 46,
        fin_du_journal(name),
        "",
        "-- CodeLab, panneau de gestion des applications",
    ])


def corps_alerte_retour(name):
    return "\n".join([
        f"L'application « {name} » repond de nouveau.",
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

    for name, a in apps.items():
        if (a.get("enabled") and not is_running(name) and is_crash_looping(name)
                and name not in _alertes_en_cours):
            _alertes_en_cours.add(name)
            alerter(f"[CodeLab] {name} est tombee", corps_alerte_chute(name, a),
                    name=name, apps=apps)


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
    env = dict(os.environ, **secrets_partages(), HOME=ensure_child_home(name))
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


def default_icon_svg(name):
    color = PALETTE[sum(map(ord, name or "?")) % len(PALETTE)]
    letter = (name[:1] or "?").upper()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="16" fill="{color}"/>'
        f'<text x="32" y="43" font-family="system-ui,sans-serif" font-size="26" '
        f'font-weight="600" fill="#fff" text-anchor="middle">{letter}</text></svg>'
    )




def valid_name(raw):
    return re.sub(r"[^a-z0-9_-]", "-", (raw or "").strip().lower()).strip("-")


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


def categorie_propre(brute):
    """Un intitule sur une ligne, borne en longueur."""
    return re.sub(r"\s+", " ", str(brute or "")).strip()[:CATEGORIE_MAX]


def lire_categories():
    """La liste des categories, dans l'ordre voulu par l'administrateur.

    L'ordre est celui de l'affichage dans le hub : il se regle en rangeant
    la liste, pas par un tri alphabetique impose.
    """
    try:
        with open(CATEGORIES_FILE) as f:
            brut = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(brut, list):
        return []
    return [c for c in (categorie_propre(x) for x in brut) if c][:CATEGORIES_MAX]


def ecrire_categories(liste):
    tmp = CATEGORIES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(liste, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CATEGORIES_FILE)


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
# ouverture qui n'existe pas. Tant qu'aucune adresse publique n'est declaree,
# le panneau ne propose donc pas de rendre une application publique.
#
# L'adresse est declaree a la main plutot que devinee : le panneau ne peut pas
# savoir si le port 443 de la box est ouvert, si le tunnel tourne, ni quel nom
# de domaine y mene. La declarer, c'est dire « j'ai fait le necessaire ».
EXPOSITION_FILE = os.path.join(STATE_DIR, "exposition.json")

# Les trois reglages qui changent quand la stack sort du reseau local, et la
# variable d'environnement qui l'emporte sur chacun.
#
# POURQUOI L'ENVIRONNEMENT GAGNE TOUJOURS. Une stack dont le compose fixe
# deja le nom de domaine ne doit pas le voir change depuis une page ; et
# surtout, c'est la seule marche arriere qui ne depend pas du panneau. Si un
# reglage pose ici rendait le panneau inatteignable, il resterait le compose
# pour reprendre la main.
REGLAGES_EXPOSITION = {
    "adresse_publique": "APP_MANAGER_PUBLIC_URL",
    "https": "APP_MANAGER_HTTPS",
    "trust_proxy": "APP_MANAGER_TRUST_PROXY",
    "admin_reseau_local": "APP_MANAGER_ADMIN_LAN_ONLY",
}


def _vrai(valeur):
    return str(valeur or "").strip().lower() in ("1", "true", "yes")


def lire_exposition():
    try:
        with open(EXPOSITION_FILE) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def ecrire_exposition(reglages):
    tmp = EXPOSITION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reglages, f, indent=2, ensure_ascii=False)
    os.replace(tmp, EXPOSITION_FILE)


def fixe_par_environnement(nom):
    """Le compose impose-t-il ce reglage ?"""
    return bool((os.environ.get(REGLAGES_EXPOSITION[nom]) or "").strip())


def adresse_publique():
    """L'adresse publique du serveur, ou "".

    La variable d'environnement l'emporte : dans une stack ou le nom de
    domaine est deja connu du compose, on ne veut pas le ressaisir dans une
    page.
    """
    depuis_env = (os.environ.get("APP_MANAGER_PUBLIC_URL") or "").strip()
    if depuis_env:
        return depuis_env.rstrip("/")
    return str(lire_exposition().get("adresse_publique") or "").strip().rstrip("/")


def https_actif():
    """Le panneau se considere-t-il servi en HTTPS ?

    Lu a chaque appel, et non fige au demarrage : c'est ce qui permet de le
    regler depuis la page Exposition sans redemarrer le service.
    """
    if fixe_par_environnement("https"):
        return _vrai(os.environ.get("APP_MANAGER_HTTPS"))
    return bool(lire_exposition().get("https"))


def trust_proxy():
    """Croit-on les en-tetes X-Forwarded-* ?

    Etait une constante calculee a l'import. Devenue une fonction pour la
    meme raison que ci-dessus -- et toutes les lectures passent par elle,
    sans quoi la moitie du panneau garderait l'ancienne valeur.
    """
    if fixe_par_environnement("trust_proxy"):
        return _vrai(os.environ.get("APP_MANAGER_TRUST_PROXY"))
    return bool(lire_exposition().get("trust_proxy"))


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
    """Le reglage est-il actif ? L'environnement l'emporte, comme les autres
    -- c'est la seule marche arriere qui ne passe pas par le panneau, et
    celle qui compte le jour ou l'on se retrouve enferme dehors."""
    if fixe_par_environnement("admin_reseau_local"):
        return _vrai(os.environ.get("APP_MANAGER_ADMIN_LAN_ONLY"))
    return bool(lire_exposition().get("admin_reseau_local"))


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
        return ("L'administration est limitee au reseau local, et un proxy non "
                "declare empeche d'etablir d'ou vient cette requete. Active "
                "\"Proxy de confiance\" dans Parametres > Serveur, ou pose "
                "APP_MANAGER_ADMIN_LAN_ONLY=0 dans le compose.")
    return ("L'administration est limitee au reseau local. Cette requete vient "
            "de " + _client_ip() + ".")


def appliquer_cookie_securise():
    """Aligne le cookie de session sur le reglage HTTPS courant.

    Flask lit SESSION_COOKIE_SECURE dans app.config au moment de poser le
    cookie : il suffit donc de tenir cette valeur a jour. Appele au
    demarrage et apres chaque ecriture.
    """
    flask_app.config["SESSION_COOKIE_SECURE"] = https_actif()


def adresse_publique_valide(brute):
    """Une adresse http(s) plausible, ou ""."""
    adresse = re.sub(r"\s+", "", str(brute or ""))[:200].rstrip("/")
    return adresse if re.fullmatch(r"https?://[^/\s]+(/[^\s]*)?", adresse) else ""


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

@flask_app.post("/login")
def login_submit():
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    d = request.get_json(force=True, silent=True) or request.form
    pw = (d.get("password") or "").strip()
    nom = (d.get("nom") or "").strip().lower()

    # Nom vide = administrateur. Le champ est arrive avec les comptes
    # utilisateurs : exiger d'un coup que l'administrateur tape "admin"
    # casserait l'habitude de tout le monde pour ne rien apporter.
    if nom in ("", NOM_ADMIN):
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
            return jsonify({"error": "Identifiants incorrects."}), 401

        # Le code a six chiffres, quand la double authentification est active.
        # Une tentative ratee ici compte comme une tentative ratee tout court :
        # sinon le second facteur serait forcable sans limite une fois le mot
        # de passe connu, ce qui le viderait de son sens.
        if totp_actif() and not totp_verifie(_totp_secret, d.get("code")):
            register_failed_attempt()
            journaliser("echec", qui=NOM_ADMIN, motif="second facteur", ip=_adresse_client())
            return jsonify({"error": "Code de vérification incorrect.",
                            "totp": True}), 401

        session.permanent = True
        session["authed"] = True
        # Le jeton nait avec la session, jamais apres : une session
        # authentifiee sans jeton ferait de verifier_jeton une passoire.
        jeton_session()
        session["role"] = ROLE_ADMIN
        session["utilisateur"] = NOM_ADMIN
        journaliser("connexion", qui=NOM_ADMIN, role=ROLE_ADMIN, ip=_adresse_client())
        return jsonify({"ok": True, "role": ROLE_ADMIN})

    compte = lire_utilisateurs().get(nom)
    # Meme message et meme chemin qu'un mot de passe faux : distinguer
    # "ce compte n'existe pas" de "mauvais mot de passe" donne la liste des
    # comptes valides a qui essaie.
    if not (compte and pw and verifie_mot_de_passe(compte, pw)):
        register_failed_attempt()
        # Le nom tel qu'il a ete tape, meme s'il ne correspond a aucun
        # compte : c'est ce qui distingue une faute de frappe d'un balayage.
        journaliser("echec", qui=nom, motif="mot de passe", ip=_adresse_client())
        return jsonify({"error": "Identifiants incorrects."}), 401

    # Le second facteur n'est pas optionnel pour un compte utilisateur. Ces
    # comptes existent pour etre distribues -- a un collegue, a un client --
    # donc leur mot de passe circule par un canal qu'on ne maitrise pas, et
    # sera reutilise ailleurs. C'est exactement le cas ou un seul secret ne
    # suffit pas. L'administrateur, lui, garde le choix : lui imposer le
    # second facteur d'office pourrait l'enfermer hors de son propre panneau.
    # Compte cree librement dont l'adresse n'a jamais ete confirmee. Le
    # message est explicite : ici, il ne revele rien qu'on ne sache deja --
    # le mot de passe vient d'etre reconnu.
    if compte.get("attente_email"):
        return jsonify({"error": "Confirme d'abord ton adresse mail : un code "
                                 "t'a été envoyé à l'inscription.",
                        "attente_email": True}), 403

    secret = compte.get("totp") or ""
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

    if not totp_verifie(secret, d.get("code")):
        register_failed_attempt()
        journaliser("echec", qui=nom, motif="second facteur", ip=_adresse_client())
        return jsonify({"error": "Code de vérification incorrect.",
                        "totp": True}), 401

    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
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
        return jsonify({"error": "Code incorrect. Vérifié l'heure de ton téléphone."}), 400

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
    refus = _refus_passkey()
    if refus:
        return refus
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                          ResidentKeyRequirement,
                                          UserVerificationRequirement)
    rp_id, _, _ = passkey_contexte()
    nom = utilisateur_courant()
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
    refus = _refus_passkey()
    if refus:
        return refus
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
        return jsonify({"error": f"Clé refusée : {type(e).__name__}: {e}"}), 400

    nom = utilisateur_courant()
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
    return jsonify({"ok": True})


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
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes
    defi = session.get("passkey_defi")
    if not defi:
        return jsonify({"error": "Recommence : aucune demande en attente."}), 400
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
        return jsonify({"error": "Clé d'accès inconnue."}), 401

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
            return jsonify({"error": hors}), 403

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
        return jsonify({"error": f"Clé refusée : {type(e).__name__}: {e}"}), 401

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

    est_administrateur = proprietaire == NOM_ADMIN
    if not est_administrateur:
        compte = lire_utilisateurs().get(proprietaire)
        if not compte:
            return jsonify({"error": "Compte inconnu."}), 401
        if compte.get("attente_email"):
            return jsonify({"error": "Confirme d'abord ton adresse mail."}), 403

    session.pop("passkey_defi", None)
    session.permanent = True
    session["authed"] = True
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_ADMIN if est_administrateur else ROLE_UTILISATEUR
    session["utilisateur"] = proprietaire
    journaliser("connexion", qui=proprietaire, role=session["role"],
                moyen="cle d'acces", ip=_adresse_client())
    return jsonify({"ok": True, "role": session["role"]})


@flask_app.get("/api/activite")
@require_admin
def api_activite():
    """Le journal des acces, et son resume.

    Reserve a l'administrateur : c'est le seul role a qui la question « qui a
    ouvert quoi » se pose, et la reponse contient des adresses IP.

    Postgres d'abord quand il repond : il garde tout, la ou le fichier est
    plafonne a 1 Mo et oublie le plus ancien. Il ne remplace jamais le
    fichier -- une base eteinte rend simplement la vue plus courte, et la
    reponse dit d'ou viennent les lignes.
    """
    app_ = request.args.get("app") or None
    qui = request.args.get("qui")
    if pg_disponible():
        try:
            return jsonify({"evenements": pg_lire_acces(app=app_, qui=qui),
                            "resume": resume_acces(), "source": "postgres",
                            "pg": _pg_etat["pret"]})
        except Exception as e:
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
    return jsonify({"evenements": lire_acces(app=app_, qui=qui),
                    "resume": resume_acces(), "source": "fichier",
                    "pg": False, "pg_erreur": _pg_etat["erreur"]})


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
                                 "propre : réglé les destinataires des alertes."}), 400
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
        return jsonify({"error": f"Adresse enregistrée, mais le mail n'est pas "
                                 f"parti : {type(e).__name__}: {e}"}), 502
    return jsonify({"ok": True, "envoye": True})


@flask_app.post("/api/mon-compte/email/code")
@require_auth
def api_mon_email_code():
    """Renvoie un code a l'adresse deja declaree."""
    compte = _mon_compte()
    if compte is None or not compte.get("email"):
        return jsonify({"error": "Déclaré d'abord une adresse."}), 400
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
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
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


# --------------------- inscription libre ---------------------
#
# Ouverte seulement si un serveur d'envoi est configure : sans mail, aucun
# moyen de verifier que l'adresse declaree existe, et la creation de comptes
# devient un formulaire a remplir en boucle.
#
# Un compte cree ainsi n'ouvre AUCUN projet : il attend que l'administrateur
# lui en autorise. C'est ce qui rend l'inscription libre sans consequence --
# au pire, des comptes vides.

@flask_app.get("/api/inscription")
def api_inscription_etat():
    _, ok = smtp_utilisable()
    return jsonify({"ouverte": ok})


@flask_app.post("/inscription")
def inscription_creer():
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    _, smtp_ok = smtp_utilisable()
    if not smtp_ok:
        return jsonify({"error": "La création de compte n'est pas ouverte sur "
                                 "ce serveur."}), 403

    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    mdp = (d.get("mot_de_passe") or "").strip()
    adresse = email_valide(d.get("email"))
    if not nom:
        return jsonify({"error": "Nom invalide : 2 a 32 caractères, "
                                 "minuscules, chiffres, tiret ou souligné."}), 400
    if nom == NOM_ADMIN:
        return jsonify({"error": "Ce nom est réservé."}), 400
    if not adresse:
        return jsonify({"error": "Adresse mail invalide."}), 400
    if len(mdp) < 8:
        return jsonify({"error": "Mot de passe : 8 caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    if nom in comptes:
        # Un nom deja pris se dit : il faudra bien en choisir un autre, et
        # l'inscription ne revele rien de plus que la page de connexion.
        return jsonify({"error": "Ce nom est déjà pris."}), 400

    sel = secrets.token_hex(16)
    comptes[nom] = {
        "sel": sel,
        "hash": derive_mot_de_passe(mdp, sel),
        "projets": [],
        "email": adresse,
        "email_verifie": False,
        # Tant que ce drapeau est la, le compte ne se connecte pas : c'est
        # ce qui distingue une adresse declaree d'une adresse relevee.
        "attente_email": True,
        "cree": int(time.time()),
    }
    code = poser_code_email(comptes[nom])
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    try:
        envoyer_code_email(adresse, nom, code)
    except Exception as e:
        # Compte cree mais injoignable : on le retire plutot que de laisser
        # un nom pris par quelqu'un qui ne pourra jamais s'en servir.
        comptes.pop(nom, None)
        try:
            ecrire_utilisateurs(comptes)
        except OSError:
            pass
        return jsonify({"error": f"Le mail n'est pas parti : {type(e).__name__}: {e}"}), 502

    # Compte dans le compteur de tentatives : sans cela, un robot creerait
    # des comptes en boucle depuis la meme adresse, et chacun ferait partir
    # un mail. Cinq par fenetre, comme les connexions.
    register_failed_attempt()
    session["inscription_email"] = nom
    return jsonify({"ok": True, "nom": nom, "confirmation": True})


@flask_app.post("/inscription/confirmer")
def inscription_confirmer():
    """Confirme l'adresse, et rend le compte utilisable.

    N'ouvre pas de session : la personne se connecte ensuite normalement, et
    enregistre a ce moment-la son second facteur. Le mail prouve l'adresse,
    pas l'identite.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = session.get("inscription_email")
    comptes = lire_utilisateurs()
    if not nom or nom not in comptes:
        return jsonify({"error": "Recommence l'inscription : rien en attente."}), 400

    code = (request.get_json(force=True, silent=True) or {}).get("code")
    ok, message = verifier_code_email(comptes[nom], code)
    if ok:
        comptes[nom].pop("attente_email", None)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"État non enregistré : {e}"}), 500
    if not ok:
        register_failed_attempt()
        return jsonify({"error": message}), 400
    session.pop("inscription_email", None)
    return jsonify({"ok": True})


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
    """Lisible par tous les comptes : le hub s'en sert pour se ranger."""
    return jsonify({"categories": lire_categories()})


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
    return jsonify({"ok": True, "categories": propres, "declasses": len(orphelins)})


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
        "trust_proxy": trust_proxy(),
        "cookie_secure": bool(flask_app.config.get("SESSION_COOKIE_SECURE")),
        "adresse_publique": adresse_publique(),
        # Fige par l'environnement : la page n'offre pas de modifier ce
        # qu'un redemarrage remettrait comme avant.
        "adresse_figee": fixe_par_environnement("adresse_publique"),
        "https_fige": fixe_par_environnement("https"),
        "trust_proxy_fige": fixe_par_environnement("trust_proxy"),
        # L'administration est-elle limitee au reseau local, et cette requete
        # y est-elle ? La page a besoin des deux : l'une pour l'etat de la
        # case, l'autre pour dire pourquoi elle est grisee.
        "admin_reseau_local": admin_limite_au_reseau_local(),
        "admin_reseau_local_fige": fixe_par_environnement("admin_reseau_local"),
        "client_local": client_est_local(),
        "client_ip": _client_ip(),
        "proxy_non_declare": proxy_non_declare(),
        # De quoi griser la case plutot que de laisser cliquer sur un refus :
        # les memes conditions que celles appliquees par la route d'ecriture.
        "peut_activer_https": request.is_secure or (
            trust_proxy()
            and request.headers.get("X-Forwarded-Proto", "").lower() == "https"),
        "peut_activer_trust_proxy": bool(
            (request.headers.get("X-Forwarded-For")
             or request.headers.get("X-Forwarded-Proto") or "").strip()),
        "peut_activer_admin_reseau_local": client_est_local(),
        # Les applications sont-elles servies dans une autre origine que le
        # panneau ? C'est ce qui empeche une XSS dans l'une d'elles d'atteindre
        # le panneau, et c'est invisible sans le dire.
        # Le nombre de cles d'acces du compte connecte : le bilan de securite
        # en a besoin, et un second appel pour un entier serait du gaspillage.
        "passkeys": len(passkeys_du_compte(utilisateur_courant() or NOM_ADMIN)),
        "origines_separees": origines_separees(),
        "origine_applications": origine_applications() if origines_separees() else "",
        "port_applications": APPS_PORT,
    })


@flask_app.put("/api/securite/exposition")
@require_admin
def api_exposition():
    """Les quatre reglages qui changent quand la stack sort du reseau local.

    CE QUI A CHANGE, ET POURQUOI. HTTPS et le proxy de confiance se posaient
    uniquement dans le compose, a decommenter a la main. Le code disait
    pourquoi : « l'activer depuis une page servie en clair deconnecterait
    sur-le-champ la session qui vient de l'activer, sans moyen de revenir en
    arriere. » L'objection etait juste. Elle ne l'est plus, parce qu'on ne
    permet plus d'allumer un interrupteur que la situation ne justifie pas :

      HTTPS          ne s'active que depuis une requete DEJA en https --
                     la session qui l'active garde donc son cookie ;
      proxy          ne s'active que si un en-tete X-Forwarded-* est
                     reellement present -- sinon c'est une regression pure,
                     n'importe quel client pouvant alors se declarer une
                     adresse neuve a chaque essai et annuler la limite de
                     tentatives de connexion.

      admin local    ne s'active que depuis une requete qui vient elle-meme
                     du reseau local -- sinon on se retirerait
                     l'administration a l'instant meme.

    ETEINDRE est toujours permis : la marche arriere ne doit jamais dependre
    d'une condition. Et le compose garde le dernier mot sur les quatre.
    """
    demande = request.get_json(force=True, silent=True) or {}
    reglages = lire_exposition()

    # ------------------------------------------------ adresse publique
    if "adresse_publique" in demande:
        if fixe_par_environnement("adresse_publique"):
            return jsonify({"error": "L'adresse est fixee par APP_MANAGER_PUBLIC_URL "
                                     "dans le compose : modifié-la la-bas."}), 400
        brute = demande.get("adresse_publique")
        adresse = adresse_publique_valide(brute)
        if brute and not adresse:
            return jsonify({"error": "Adresse invalide : elle doit commencer par "
                                     "http:// ou https://."}), 400
        reglages["adresse_publique"] = adresse

    # ------------------------------------------------ proxy de confiance
    #
    # Traite AVANT https : derriere un proxy qui termine le TLS, la requete
    # arrive ici en clair et n'annonce https que par un en-tete. Declarer le
    # proxy d'abord, puis https, se fait alors en deux enregistrements -- et
    # dans cet ordre, ce qui est le bon.
    if "trust_proxy" in demande:
        if fixe_par_environnement("trust_proxy"):
            return jsonify({"error": "Le proxy de confiance est fixe par "
                                     "APP_MANAGER_TRUST_PROXY dans le compose : "
                                     "modifié-le la-bas."}), 400
        voulu = bool(demande.get("trust_proxy"))
        devant = (request.headers.get("X-Forwarded-For")
                  or request.headers.get("X-Forwarded-Proto") or "")
        if voulu and not devant.strip():
            return jsonify({"error": "Aucun en-tete X-Forwarded-* sur cette requête : "
                                     "rien ne prouve qu'un proxy est devant. L'activer "
                                     "ici laisserait n'importe quel client s'inventer "
                                     "une adresse, et annulerait la limite de "
                                     "tentatives de connexion."}), 400
        reglages["trust_proxy"] = voulu

    # ------------------------------------------------ https
    if "https" in demande:
        if fixe_par_environnement("https"):
            return jsonify({"error": "HTTPS est fixe par APP_MANAGER_HTTPS dans le "
                                     "compose : modifié-le la-bas."}), 400
        voulu = bool(demande.get("https"))
        # "Deja en https" au sens de ce que le panneau croit : une connexion
        # TLS directe, ou un proxy annoncant https ET declare de confiance.
        # Sans cette seconde moitie, la case resterait impossible a cocher
        # derriere un reverse proxy -- c'est-a-dire dans le cas courant.
        annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
        deja = request.is_secure or (reglages.get("trust_proxy") and annonce == "https")
        if voulu and not deja:
            return jsonify({"error": "Cette page n'est pas servie en HTTPS. L'activer "
                                     "maintenant rendrait le cookie de session "
                                     "\"Secure\", et te deconnecterait sans retour "
                                     "possible. Mets le TLS en place, reviens par "
                                     "https, et la case s'activera."}), 400
        reglages["https"] = voulu

    # --------------------------------- administration sur le reseau local
    if "admin_reseau_local" in demande:
        if fixe_par_environnement("admin_reseau_local"):
            return jsonify({"error": "Ce réglage est fixe par "
                                     "APP_MANAGER_ADMIN_LAN_ONLY dans le compose : "
                                     "modifié-le la-bas."}), 400
        voulu = bool(demande.get("admin_reseau_local"))
        # Meme regle que pour HTTPS, pour la meme raison : on n'allume pas un
        # interrupteur qui couperait la branche sur laquelle on est assis.
        # L'activer depuis l'exterieur reviendrait a se retirer
        # l'administration dans la seconde, et la seule facon de revenir
        # serait d'aller editer le compose.
        if voulu and not client_est_local():
            if proxy_non_declare():
                return jsonify({"error": "Un proxy non déclaré empeche de savoir d'ou "
                                         "viennent les requêtes : toutes paraitraient "
                                         "locales, et ce réglage ne protegerait rien. "
                                         "Activé d'abord \"Proxy de confiance\"."}), 400
            return jsonify({"error": "Cette requête ne vient pas du réseau local. "
                                     "L'activer maintenant te retirerait "
                                     "l'administration à l'instant même, sans retour "
                                     "possible depuis cette page. Reconnecte-toi "
                                     "depuis chez toi, et la case s'activera."}), 400
        reglages["admin_reseau_local"] = voulu

    try:
        ecrire_exposition(reglages)
    except OSError as e:
        return jsonify({"error": f"Réglages non enregistrés : {e}"}), 500

    # Le cookie suit immediatement : c'est tout l'interet de ne plus figer ce
    # reglage au demarrage.
    appliquer_cookie_securise()

    return jsonify({"ok": True,
                    "adresse_publique": adresse_publique(),
                    "https": https_actif(),
                    "trust_proxy": trust_proxy(),
                    "admin_reseau_local": admin_limite_au_reseau_local()})


# ------------------- assistant de liaison avec un VPS -------------------
#
# CE QU'IL FAIT, ET CE QU'IL NE FAIT PAS. Il prend un domaine et l'adresse
# publique d'un VPS, et rend les fichiers de configuration prets a copier --
# ceux-la memes que documente app-manager/vps/README.md, avec les valeurs
# substituees. Puis il dit ou en est la liaison, d'apres ce qu'il constate
# sur les requetes qui lui arrivent.
#
# Il ne se connecte PAS au VPS. Lui donner une cle SSH avec les droits qui
# vont avec reviendrait a confier a ce panneau l'administration d'une machine
# exposee sur internet -- c'est-a-dire a faire de lui la cible la plus
# interessante de l'installation. Recopier trois fichiers a la main coute
# quelques minutes, une fois.
VPS_MODELES = os.environ.get(
    "APP_MANAGER_VPS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vps"))

# Les valeurs des modeles livres, et ce par quoi les remplacer. Substituer
# plutot que reecrire : les modeles sont la source de verite, et un assistant
# qui regenere son propre texte finit toujours par decrire autre chose que ce
# que dit le README.
# La seule question qu'on se pose devant un fichier de configuration est :
# "je le colle OU ?". Le cote est donc porte par la donnee, et non devine
# dans la page a partir du texte d'un chemin.
#
# L'ordre compte aussi : le tunnel d'abord (sans lui, nginx n'a personne a
# joindre), nginx ensuite. C'est l'ordre dans lequel on fait les choses.
COTE_VPS = "Sur le VPS"
COTE_LOCAL = "Sur cette machine"

VPS_FICHIERS = {
    "wireguard_vps": ("wireguard/wg0-vps.conf.exemple",
                      "/etc/wireguard/wg0.conf", COTE_VPS,
                      "Le tunnel, cote VPS."),
    "wireguard_local": ("wireguard/wg0-zimablade.conf.exemple",
                        "/etc/wireguard/wg0.conf", COTE_LOCAL,
                        "Le tunnel, cote ZimaBlade. A poser sur l'HOTE, pas dans un conteneur."),
    "nginx": ("nginx/codelab.conf",
              "/etc/nginx/sites-available/codelab.conf", COTE_VPS,
              "Le domaine, le certificat, et le renvoi dans le tunnel."),
    "nginx_upgrade": ("nginx/00-codelab-upgrade.conf",
                      "/etc/nginx/conf.d/00-codelab-upgrade.conf", COTE_VPS,
                      "Laisse passer les websockets. Une ligne, mais sans elle le terminal "
                      "de Dagster reste muet."),
}


def lire_vps():
    d = lire_exposition().get("vps") or {}
    return {
        "domaine": str(d.get("domaine") or "").strip(),
        "ip": str(d.get("ip") or "").strip(),
        "reseau": str(d.get("reseau") or "10.8.0").strip(),
    }


def _ip_publique_valide(brute):
    """Une adresse IP qui a un sens comme point de rendez-vous du tunnel.

    Refuse ce qui n'est pas une adresse, et refuse aussi une adresse LOCALE :
    un VPS joignable de partout n'a pas une adresse privee, et saisir celle
    de sa propre machine donnerait une configuration qui ne peut pas marcher
    -- autant le dire tout de suite plutot qu'apres trois copies de fichiers.
    """
    texte = str(brute or "").strip()
    try:
        adresse = ipaddress.ip_address(texte)
    except ValueError:
        return ""
    return "" if adresse_est_locale(texte) else texte


def vps_configuration(reglages):
    """Les fichiers a copier, valeurs substituees."""
    domaine = reglages["domaine"] or "codelab.exemple.fr"
    reseau = reglages["reseau"] or "10.8.0"
    sorties = {}
    for cle, (relatif, destination, cote, role) in VPS_FICHIERS.items():
        chemin = os.path.join(VPS_MODELES, relatif)
        try:
            with open(chemin, encoding="utf-8") as f:
                texte = f.read()
        except OSError:
            # Modeles absents de l'image : on le dit plutot que de rendre un
            # fichier invente qui n'aurait jamais ete relu par personne.
            continue
        texte = texte.replace("codelab.exemple.fr", domaine)
        texte = texte.replace("10.8.0.1", reseau + ".1").replace("10.8.0.2", reseau + ".2")
        texte = texte.replace("10.8.0.0/24", reseau + ".0/24")
        if reglages["ip"]:
            texte = texte.replace("203.0.113.10", reglages["ip"])
        sorties[cle] = {"destination": destination, "contenu": texte,
                        "cote": cote, "role": role}
    return sorties


def vps_diagnostic(reglages):
    """Ou en est la liaison, d'apres ce qu'on CONSTATE sur cette requete.

    Aucune de ces lignes n'est une supposition : le panneau regarde la
    requete qu'il est en train de traiter. C'est la seule chose qu'il puisse
    honnetement affirmer sans se connecter au VPS.
    """
    annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
    devant = bool(request.headers.get("X-Forwarded-For") or annonce)
    publique = adresse_publique()
    etapes = [
        ("Domaine et adresse du VPS declares",
         bool(reglages["domaine"] and reglages["ip"]),
         "Saisis-les ci-dessus : ils servent a produire les fichiers de configuration."),
        ("Un intermediaire relaie cette requete", devant,
         "Aucun en-tete X-Forwarded-* sur cette requete. Soit tu regardes cette page "
         "directement depuis le reseau local -- c'est normal -- soit nginx n'est pas "
         "encore en place sur le VPS."),
        ("Le proxy est declare de confiance", trust_proxy(),
         "Case « Proxy de confiance », plus haut. Sans elle le panneau ne croit pas "
         "l'adresse annoncee, et tous les visiteurs comptent pour un seul."),
        ("La requete arrive en HTTPS", annonce == "https" or request.is_secure,
         "Le certificat se pose sur le VPS (certbot), pas ici. Voir l'etape 3 du README."),
        ("Adresse publique declaree dans le panneau", bool(publique),
         "Carte « Adresse publique », plus bas. Tant qu'elle manque, aucune application "
         "ne peut etre rendue publique."),
    ]
    return [{"etape": nom, "ok": bool(ok), "aide": aide} for nom, ok, aide in etapes]


@flask_app.get("/api/vps")
@require_admin
def api_vps():
    reglages = lire_vps()
    config = vps_configuration(reglages)
    return jsonify({
        "reglages": reglages,
        "diagnostic": vps_diagnostic(reglages),
        # cote et role partent avec : c'est la page qui les affiche, mais
        # c'est ici qu'ils sont connus. Les deviner cote navigateur a partir
        # d'un chemin serait une regle de plus a tenir a jour ailleurs.
        "fichiers": [{"cle": cle, "destination": v["destination"],
                      "contenu": v["contenu"], "cote": v["cote"],
                      "role": v["role"]}
                     for cle, v in config.items()],
        "modeles_absents": not config,
    })


@flask_app.put("/api/vps")
@require_admin
def api_vps_enregistrer():
    d = request.get_json(force=True, silent=True) or {}
    domaine = re.sub(r"[^a-zA-Z0-9.-]", "", str(d.get("domaine") or "").strip())[:200]
    ip = str(d.get("ip") or "").strip()
    reseau = str(d.get("reseau") or "").strip() or "10.8.0"

    if ip and not _ip_publique_valide(ip):
        return jsonify({"error": "Ce n'est pas une adresse publique. Un VPS joignable "
                                 "depuis internet n'a pas une adresse privée -- vérifié "
                                 "que tu n'as pas saisi celle de ta propre machine."}), 400
    if not re.fullmatch(r"(\d{1,3}\.){2}\d{1,3}", reseau):
        return jsonify({"error": "Réseau du tunnel : trois nombres, par exemple 10.8.0."}), 400

    reglages = lire_exposition()
    reglages["vps"] = {"domaine": domaine, "ip": ip, "reseau": reseau}
    try:
        ecrire_exposition(reglages)
    except OSError as e:
        return jsonify({"error": f"Réglages non enregistrés : {e}"}), 500
    return jsonify({"ok": True, "reglages": lire_vps()})


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

    if len(nouveau) < 12:
        # Douze, et non huit comme pour les comptes crees par
        # l'administrateur : celui-ci se choisit lui-meme, il n'a pas a etre
        # transmis, et rien n'oblige a le raccourcir.
        return jsonify({"error": "Mot de passe : 12 caractères au minimum."}), 400
    if nouveau == ancien:
        return jsonify({"error": "Le nouveau mot de passe est identique à l'ancien."}), 400

    if est_admin():
        reel = admin_password()
        if not (reel and ancien and secrets.compare_digest(ancien, reel)):
            register_failed_attempt()
            journaliser("echec", qui=NOM_ADMIN, motif="changement de mot de passe",
                        ip=_adresse_client())
            return jsonify({"error": "Ancien mot de passe incorrect."}), 403
        global _admin_password
        # La cle de session est relue a sa source, jamais reconstituee depuis
        # flask_app.secret_key : ecrire une cle differente de celle en place
        # deconnecterait tout le monde au redemarrage suivant, sans rapport
        # visible avec le changement de mot de passe.
        cle = read_shared_value("APP_MANAGER_SESSION_SECRET") or ""
        if not cle:
            return jsonify({"error": "Clé de session introuvable dans "
                                     "credentials.env : le mot de passe reste "
                                     "inchangé plutot que de risquer de "
                                     "deconnecter tout le monde."}), 500
        if not ecrire_bloc_panneau(nouveau, cle, _totp_secret):
            return jsonify({"error": "credentials.env n'a pas pu être écrit : "
                                     "le mot de passe reste inchangé."}), 500
        _admin_password = nouveau
        journaliser("mot-de-passe", qui=NOM_ADMIN, ip=_adresse_client())
        return jsonify({"ok": True})

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
        return jsonify({"error": "Code incorrect. Vérifié l'heure de ton téléphone."}), 400
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
        "par_application": {nom: _adresses(a.get("alertes"))
                            for nom, a in sorted(load().items())},
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
            except OSError as e:
                return jsonify({"error": f"Configuration non enregistrée : {e}"}), 500

    # --- l'adresse d'administration, dans le bloc partage ------------------
    if not ecrire_bloc_alertes({"ALERTE_ADMIN": ", ".join(admin)}):
        return jsonify({"error": "credentials.env n'a pas pu être écrit."}), 500

    try:
        ecrire_alertes(actif, admin)
    except OSError as e:
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
    apps[name]["alertes"] = adresses
    try:
        save(apps)
    except OSError as e:
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
         "projets": sorted(c.get("projets", [])),
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
         "cree": c.get("cree")}
        for nom, c in sorted(comptes.items())]})


def _projets_valides(brut, apps):
    """Ne garde que des projets qui existent vraiment.

    Un projet supprime puis recree sous le meme nom rendrait sinon un droit
    qu'on croyait perdu -- et la liste se remplirait de noms morts.
    """
    return sorted({p for p in (brut or []) if isinstance(p, str) and p in apps})


@flask_app.post("/api/utilisateurs")
@require_admin
def api_utilisateur_creer():
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    mdp = (d.get("mot_de_passe") or "").strip()
    email = email_valide(d.get("email"))

    if d.get("email") and not email:
        return jsonify({"error": "Adresse mail invalide."}), 400
    if not nom:
        return jsonify({"error": "Nom invalide : 2 a 32 caractères, "
                                 "minuscules, chiffres, tiret ou souligné."}), 400
    if nom == NOM_ADMIN:
        return jsonify({"error": "Ce nom est celui du compte d'administration."}), 400
    if len(mdp) < 8:
        return jsonify({"error": "Mot de passe : 8 caractères au minimum."}), 400

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
    mdp = (d.get("mot_de_passe") or "").strip()
    if mdp:
        if len(mdp) < 8:
            return jsonify({"error": "Mot de passe : 8 caractères au minimum."}), 400
        compte["sel"] = secrets.token_hex(16)
        compte["hash"] = derive_mot_de_passe(mdp, compte["sel"])

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


@flask_app.post("/logout")
def logout():
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


@flask_app.get("/health")
def health():
    return Response("ok\n", mimetype="text/plain")


# ------------------------------ API --------------------------------

@flask_app.get("/api/apps")
@require_admin
def api_apps():
    apps = load()
    out = []
    for name in sorted(apps):
        a = apps[name]
        run = is_running(name)
        stats = proc_stats(procs[name].pid) if run and name in procs else {"cpu_percent": 0.0, "memory_mb": 0.0}
        if run:
            record_metrics(name, stats["cpu_percent"], stats["memory_mb"])
        out.append({
            "name": name, "path": a["path"], "command": a["command"],
            "port": a["port"], "running": run,
            "failed": bool(a.get("enabled")) and not run,
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
            "categorie": a.get("categorie") or "",
            **stats,
        })
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
    apps = load()

    if not name:
        return jsonify({"error": "Le nom est obligatoire."}), 400
    if name in ("api", "static", "health", "login", "logout"):
        return jsonify({"error": "Ce nom est réservé."}), 400
    if name in apps:
        return jsonify({"error": "Une application porte déjà ce nom."}), 400
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
        "path": path, "command": command, "port": port, "enabled": False,
        "build_command": build_command, "max_memory_mb": max_memory_mb,
        "visibility": vis,
        "description": description_propre(d.get("description")),
        "categorie": categorie_valide(d.get("categorie")),
    }
    save(apps)
    return jsonify({"ok": True, "name": name, "port": port})


@flask_app.put("/api/app/<n>")
@require_admin
def api_edit(n):
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    if is_running(n):
        return jsonify({"error": "Arrêté l'application avant de la modifier."}), 400
    d = request.get_json(force=True)
    path = (d.get("path") or "").strip()
    command = (d.get("command") or "").strip()
    if not os.path.isdir(path):
        return jsonify({"error": "Dossier introuvable : " + path}), 400
    if not under_root(path):
        return jsonify({"error": "Le dossier doit se trouver dans " + ROOT + "."}), 400
    if not command:
        return jsonify({"error": "La commande de lancement est obligatoire."}), 400
    apps[n]["path"] = path
    apps[n]["command"] = command
    apps[n]["build_command"] = (d.get("build_command") or "").strip()
    apps[n]["max_memory_mb"] = d.get("max_memory_mb") or None
    apps[n]["description"] = description_propre(d.get("description"))
    apps[n]["categorie"] = categorie_valide(d.get("categorie"))
    if d.get("visibility") in VISIBILITES:
        apps[n]["visibility"] = d["visibility"]
    save(apps)
    return jsonify({"ok": True})


@flask_app.post("/api/toggle/<n>")
@require_admin
def api_toggle(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    if is_running(n):
        stop(n)
        return jsonify({"ok": True, "running": False})
    erreur = start(n)
    if erreur:
        # 409 et non 500 : le panneau a fait son travail, c'est
        # l'application qui refuse de demarrer. La nuance compte pour qui
        # lit les journaux du panneau.
        return jsonify({"error": erreur}), 409
    return jsonify({"ok": True, "running": True})


def restart_app(n):
    stop(n)
    for _ in range(30):
        if not is_running(n):
            break
        time.sleep(0.1)
    start(n)


@flask_app.post("/api/visibility/<n>")
@require_admin
def api_visibility(n):
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
        return jsonify({"error": "Aucune adresse publique n'est déclarée pour ce "
                                 "serveur : rendre une application publique ne "
                                 "ferait que retirer l'authentification. "
                                 "Déclaré-la dans Configuration > Serveur."}), 400
    apps[n]["visibility"] = vis
    save(apps)
    return jsonify({"ok": True, "visibility": vis})


@flask_app.post("/api/restart/<n>")
@require_admin
def api_restart(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    restart_app(n)
    return jsonify({"ok": True})


@flask_app.post("/api/deploy/<n>")
@require_admin
def api_deploy(n):
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
    restart_app(n) if is_running(n) else start(n)
    return jsonify({"ok": True})


@flask_app.post("/api/build/<n>")
@require_admin
def api_build(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, msg = run_build(n)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True})


@flask_app.get("/api/metrics/<n>")
@require_admin
def api_metrics(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
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
    if n == DIAGNOSTIC_NOM:
        return jsonify({"error": "Le projet de diagnostic ne se supprimé pas : "
                                 "c'est lui qui dit si cette installation va "
                                 "bien. Tu peux l'arrêter si tu ne veux pas "
                                 "qu'il tourne."}), 403
    stop(n)
    apps = load()
    apps.pop(n, None)
    save(apps)
    return jsonify({"ok": True})


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


@flask_app.get("/api/icon/<n>")
@require_auth
def api_icon(n):
    # Accessible a un compte utilisateur, pour que son espace affiche les
    # icones -- mais seulement des projets qu'il peut ouvrir : la liste des
    # icones est une liste des projets existants.
    if not peut_voir(n):
        return Response(default_icon_svg(n), mimetype="image/svg+xml")
    apps = load()
    a = apps.get(n)
    icon_path = find_icon(a["path"]) if a else None
    if icon_path:
        return send_file(icon_path)
    return Response(default_icon_svg(n), mimetype="image/svg+xml")




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


@flask_app.get("/login")
def login_page():
    if is_authed():
        return redirect("/")
    # __TOTP__ vaut "1" quand la double authentification est active : la page
    # affiche alors le champ du code. Substitue au moment de servir, comme
    # __ROOT__ dans le tableau de bord.
    page = LOGIN_PAGE.replace("__TOTP__", "1" if totp_actif() else "0")
    return Response(page, mimetype="text/html")


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
    page = (DASHBOARD_PAGE
            .replace("__JETON__", json.dumps(jeton_session()))
            .replace("__APPS_BASE__", json.dumps(
                origine_applications() if origines_separees() else ""))
            .replace("__ROOT__", json.dumps(ROOT))
            .replace("__ROLE__", json.dumps(role_courant() or ""))
            .replace("__UTILISATEUR__", json.dumps(utilisateur_courant())))
    return Response(page, mimetype="text/html")


@flask_app.get("/espace")
@require_auth
def espace():
    """Ancienne adresse de l'espace utilisateur.

    Conservee parce qu'elle a pu etre mise en favori : le hub vit maintenant
    dans la page principale, en tant que mode.
    """
    return redirect("/")


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
    liste = []
    for nom, a in sorted(load().items()):
        if autorises is not None and nom not in autorises:
            continue
        liste.append({
            "name": nom,
            "description": a.get("description") or "",
            "categorie": categorie_valide(a.get("categorie"), connues),
            "running": is_running(nom),
            "listening": _listening.get(nom),
            "visibility": visibilite(a),
        })
    # Les categories accompagnent la liste : le hub les affiche dans l'ordre
    # voulu, sans avoir a deviner cet ordre a partir des projets.
    return jsonify({"apps": liste,
                    "categories": connues,
                    "utilisateur": utilisateur_courant(),
                    "role": role_courant()})


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
RUBAN_RETOUR = (
    '<a href="/" id="codelab-retour-hub" title="Revenir au hub CodeLab" '
    'style="all:initial;position:fixed;left:14px;bottom:14px;z-index:2147483647;'
    'display:inline-flex;align-items:center;gap:7px;padding:8px 13px;'
    'font:600 13px/1 -apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;'
    'color:#fff;background:#141a21;border-radius:999px;cursor:pointer;'
    'box-shadow:0 2px 10px rgba(0,0,0,.28);text-decoration:none">'
    '<span style="all:initial;color:#fff;font:600 15px/1 sans-serif">&#8592;</span>'
    '<span style="all:initial;color:#fff;font:600 13px/1 -apple-system,'
    'BlinkMacSystemFont,sans-serif">CodeLab</span></a>'
).encode()


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
    data = data[:i] + RUBAN_RETOUR + data[i:]
    # Content-Length devient faux si on ne le refait pas : le navigateur
    # tronquerait la page a l'ancienne taille, juste avant le ruban.
    entetes = [(k, v) for k, v in entetes if k.lower() != "content-length"]
    entetes.append(("Content-Length", str(len(data))))
    return data, entetes


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
    if visibilite(a) == VISIBILITE_PRIVEE:
        if not is_authed():
            return redirect("/login")
        if not peut_voir(name):
            return Response(_page("Acces refuse",
                                  "Ton compte n'a pas acces a \u00ab " + name + " \u00bb.",
                                  "Demande l'acces a l'administrateur."),
                            403, mimetype="text/html")
    # Note l'ouverture APRES les controles d'acces : un refus n'est pas une
    # visite, et le journal servirait mal s'il melangeait les deux.
    journaliser_ouverture(name)
    if not is_running(name):
        return Response(_page("Application arretee",
                              "\u00ab " + name + " \u00bb n'est pas demarree.",
                              "Active-la depuis le panneau."), 503, mimetype="text/html")
    url = ("http://127.0.0.1:" + str(a["port"]) + "/"
           + urllib.parse.quote(sub, safe="/"))
    if request.query_string:
        url += "?" + request.query_string.decode()
    body = request.get_data() if request.method in ("POST", "PUT", "PATCH") else None
    req = urllib.request.Request(url, data=body, method=request.method)
    for k, v in request.headers:
        if k.lower() in HOP or k.lower() == "host":
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
    try:
        r = urllib.request.urlopen(req, timeout=30)
        data, status, headers = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        data, status, headers = e.read(), e.code, e.headers
    except Exception:
        return Response(_page("Demarrage en cours",
                              "\u00ab " + name + " \u00bb ne repond pas encore.",
                              "Reessaie dans quelques secondes."), 502, mimetype="text/html")
    sortants = [(k, v) for k, v in headers.items() if k.lower() not in HOP]
    # Le ruban n'est pose que pour quelqu'un de CONNECTE. Une application
    # publique vue par un visiteur anonyme ne doit pas lui annoncer qu'un
    # panneau existe derriere, ni lui offrir un lien qui le renverrait a une
    # page de connexion dont il n'a que faire.
    if is_authed():
        data, sortants = injecter_ruban(data, status, sortants)
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
    return ("<!doctype html><meta charset=utf-8><title>" + title + "</title>"
            "<body style=\"margin:0;background:#0d1117;color:#c9d1d9;display:flex;"
            "align-items:center;justify-content:center;height:100vh;font:15px/1.6 "
            "-apple-system,Segoe UI,Roboto,sans-serif\"><div style=\"text-align:center\">"
            "<div style=\"font-size:17px;font-weight:600;color:#e6edf3;margin-bottom:6px\">"
            + title + "</div><div style=\"color:#8b949e\">" + msg + "</div>"
            "<div style=\"color:#6e7681;font-size:13px;margin-top:6px\">" + extra + "</div>"
            "<div style=\"margin-top:22px\"><a href=\"/\" style=\"color:#4c8eff;"
            "text-decoration:none;font-size:14px\">Retour au panneau</a></div></div>")


# --------------------- inscription du projet de diagnostic ---------------------
#
# Le conteneur dagster depose le projet "diagnostic" dans /workspace au premier
# demarrage de la stack ; il fallait ensuite l'ajouter a la main dans le
# panneau, en recopiant deux commandes depuis son README. C'est la seule etape
# manuelle qui separait une stack fraiche d'une stack verifiee -- et c'est
# precisement celle qu'on saute quand on est presse, donc celle qui manque le
# jour ou quelque chose ne marche pas.
#
# Le projet est donc inscrit tout seul, une fois, au premier demarrage.
#
# "Une fois" est la partie delicate. Trois garde-fous, dans cet ordre :
#
#   1. Un marqueur dans STATE_DIR. Pose des que la question est tranchee, il
#      garantit qu'un projet supprime depuis le panneau ne reapparait pas au
#      redemarrage suivant -- meme raison d'etre que le marqueur de squelette
#      cote dagster (voir dagster/entrypoint.sh).
#   2. Un registre non vide veut dire "installation deja en service" : on ne
#      touche pas a un panneau existant, on pose seulement le marqueur.
#   3. Le dossier peut ne pas encore exister : app-manager et dagster demarrent
#      en parallele, et c'est dagster qui amorce /workspace. Dans ce cas on ne
#      pose PAS le marqueur et on reessaiera au prochain demarrage.
DIAGNOSTIC_NOM = "diagnostic"
DIAGNOSTIC_MARQUEUR = os.path.join(STATE_DIR, "diagnostic-inscrit")

# Les deux commandes du README du projet, pas une detection automatique :
# detect_project() proposerait bien "python3 app.py", mais rendrait une
# commande de build vide (il cherche un requirements.txt, que ce projet n'a
# pas), et l'application demarrerait sans pilote Postgres.
DIAGNOSTIC_COMMANDE = "python3 app.py"
DIAGNOSTIC_BUILD = 'pip install --target vendor "psycopg[binary]"'


def amorcer_diagnostic():
    """Inscrit le projet de diagnostic au premier demarrage, et le lance.

    Ne fait rien du tout sur une installation deja en service. Renvoie le nom
    inscrit, ou None.
    """
    if os.path.exists(DIAGNOSTIC_MARQUEUR):
        return None

    apps = load()
    if apps:
        # Panneau deja utilise : l'utilisateur a peut-etre inscrit ce projet
        # lui-meme, ou l'a supprime volontairement. On s'efface, definitivement.
        _poser_marqueur_diagnostic()
        return None

    chemin = os.path.join(ROOT, DIAGNOSTIC_NOM)
    if not os.path.isfile(os.path.join(chemin, "app.py")):
        # Pas encore amorce par dagster : on retentera au prochain demarrage.
        return None

    apps[DIAGNOSTIC_NOM] = {
        "path": chemin,
        "command": DIAGNOSTIC_COMMANDE,
        "port": next_port(apps),
        # Demarree par le thread d'amorcage, apres le build : la mettre a True
        # ici la ferait lancer par resume() sans son pilote Postgres.
        "enabled": False,
        "build_command": DIAGNOSTIC_BUILD,
        "max_memory_mb": None,
        # Privee : la page nomme les conteneurs, l'utilisateur SSH et l'etat de
        # la base. Rien de secret, mais rien non plus a offrir a un visiteur
        # anonyme le jour ou le port 9001 est publie. L'utilisateur peut la
        # rendre publique en un clic depuis le panneau.
        "visibility": VISIBILITE_PRIVEE,
    }
    save(apps)
    _poser_marqueur_diagnostic()
    return DIAGNOSTIC_NOM


def _poser_marqueur_diagnostic():
    try:
        with open(DIAGNOSTIC_MARQUEUR, "w") as f:
            f.write("Le projet de diagnostic a ete inscrit une fois au premier "
                    "demarrage. Supprimer ce fichier le fera reinscrire, s'il "
                    "n'est plus dans le panneau et qu'aucune autre application "
                    "n'y figure.\n")
    except OSError as e:
        # Sans marqueur l'inscription se rejouerait, mais seulement tant que le
        # registre est vide : le pire cas reste borne, et il ne justifie pas
        # d'empecher le panneau de demarrer.
        print(f"[app-manager] marqueur de diagnostic non ecrit ({e}).", flush=True)


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
    """
    ok, msg = run_build(name)
    if not ok:
        print(f"[app-manager] {name} : build initial en echec ({msg}) -- "
              f"l'application demarre quand meme, la sonde Postgres le dira.",
              flush=True)
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
    le panneau le dit dans Parametres > Serveur.
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
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(APPS_FILE):
        save({})
    inscrit = amorcer_diagnostic()
    resume()
    if inscrit:
        threading.Thread(target=_preparer_diagnostic, args=(inscrit,),
                         daemon=True).start()
    start_monitor_thread()
    demarrer_miroir_pg()
    servir(int(os.environ.get("MANAGER_PORT", "9001")))

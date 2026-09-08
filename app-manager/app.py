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
  - "Git pull" (visible seulement si le dossier contient .git)
  - limite memoire optionnelle par app (RLIMIT_AS via preexec_fn)

Le panneau n'ecrit jamais dans /workspace : il n'y cree aucun projet et
n'y depose aucun fichier. Les dossiers sont crees par l'utilisateur (SSH,
VS Code, git clone) puis simplement declares ici.
"""
import datetime
import json
import os
import re
import resource
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

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

_admin_password = None  # valeur courante, chargee par bootstrap_secrets()

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
    # Le panneau est servi en clair sur le reseau local : exiger un cookie
    # "Secure" empecherait purement et simplement de se connecter. C'est le
    # compromis assume d'un service LAN sans TLS.
    SESSION_COOKIE_SECURE=False,
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

    written = upsert_shared_block(
        "codelab-app-manager",
        [
            "# Panneau web de gestion des applications deployees (http://<IP>:9001/).",
            "# APP_MANAGER_ADMIN_PASSWORD : mot de passe de connexion au panneau.",
            "# APP_MANAGER_SESSION_SECRET : cle de signature des sessions -- la",
            "#   changer deconnecte tout le monde ; ne jamais la partager.",
        ],
        {
            "APP_MANAGER_URL": "http://<IP-du-serveur>:9001",
            "APP_MANAGER_ADMIN_PASSWORD": pw,
            "APP_MANAGER_SESSION_SECRET": key,
        },
    )

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


# --------------------------- auth ---------------------------

RATE_LIMIT_WINDOW = 300  # 5 min
RATE_LIMIT_MAX = 5


# X-Forwarded-For n'est croyable que derriere un proxy de confiance qui le
# reecrit. Le panneau est publie directement sur le port 9001 : n'importe quel
# client peut donc poser l'en-tete qu'il veut, et le faire varier a chaque
# essai -- ce qui donnait a chaque tentative de connexion un compteur neuf et
# annulait purement et simplement la limite de 5 essais par 5 minutes.
# Derriere un vrai reverse proxy, poser APP_MANAGER_TRUST_PROXY=1.
TRUST_PROXY = os.environ.get("APP_MANAGER_TRUST_PROXY", "").lower() in ("1", "true", "yes")


def _client_ip():
    if TRUST_PROXY:
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


def require_auth(view):
    def wrapped(*a, **kw):
        if not is_authed():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Non authentifie."}), 401
            return redirect("/login")
        return view(*a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


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


# Dossier personnel des processus enfants. Sans lui, ils heritent de
# HOME=/root -- illisible et surtout non ecrivable une fois l'uid abandonne,
# et "npm ci" echoue alors sur son cache (~/.npm) avant meme d'installer quoi
# que ce soit. Range dans le volume d'etat plutot que dans /tmp : le cache npm
# survit ainsi d'un build a l'autre.
CHILD_HOME = os.path.join(STATE_DIR, "home")


def ensure_child_home():
    os.makedirs(CHILD_HOME, exist_ok=True)
    if os.geteuid() == 0:
        try:
            os.chown(CHILD_HOME, RUN_AS_UID, RUN_AS_GID)
            os.chmod(CHILD_HOME, 0o2770)
        except OSError as e:
            print(f"[app-manager] {CHILD_HOME} : droits non poses ({e}).", flush=True)
    return CHILD_HOME


def drop_privileges():
    """Bascule le processus courant sur l'utilisateur non privilegie.

    Appelee dans le preexec_fn, donc APRES le fork et AVANT l'exec : elle ne
    touche jamais au service lui-meme. Sans effet si l'on n'est pas root, ce
    qui est le cas quand app.py tourne hors conteneur (tests, mise au point).
    """
    if os.geteuid() != 0:
        return
    # setgroups avant setuid : une fois l'uid abandonne, le processus n'a plus
    # le droit de modifier ses groupes secondaires, et garderait ceux de root.
    os.setgroups([RUN_AS_GID])
    os.setgid(RUN_AS_GID)
    os.setuid(RUN_AS_UID)
    # Reposé ici : le umask n'est pas herite du service de maniere fiable a
    # travers toute la chaine, et sans 002 les fichiers produits par un build
    # (dist/, node_modules/) ressortent en lecture seule pour le groupe --
    # donc non modifiables depuis une session SSH.
    os.umask(0o002)


def child_setup(max_memory_mb=None):
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
        drop_privileges()
    return _setup


# ------------------------- cycle de vie ----------------------------

def is_running(name):
    p = procs.get(name)
    return p is not None and p.poll() is None


def start(name):
    apps = load()
    a = apps.get(name)
    if not a or is_running(name):
        return

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
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    rotate_log_if_needed(name)
    out = open(os.path.join(LOG_DIR, name + ".log"), "ab", buffering=0)
    env = dict(os.environ, PORT=str(a["port"]), PYTHONUNBUFFERED="1",
               HOME=ensure_child_home())

    with lock:
        procs[name] = subprocess.Popen(
            ["bash", "-lc", a["command"]],
            cwd=a["path"], env=env, stdout=out, stderr=out,
            start_new_session=True,
            preexec_fn=child_setup(a.get("max_memory_mb")))
    apps[name]["enabled"] = True
    save(apps)


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
                start(name)
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
            start(name)
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
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ------------------------- build / git pull ----------------------------

def run_build(name):
    apps = load()
    a = apps.get(name)
    if not a:
        return False, "Application inconnue."
    cmd = (a.get("build_command") or "").strip()
    if not cmd:
        return False, "Aucune commande de build definie pour cette application."
    os.makedirs(LOG_DIR, exist_ok=True)
    env = dict(os.environ, HOME=ensure_child_home())
    logf = os.path.join(LOG_DIR, name + ".log")
    with open(logf, "ab") as out:
        out.write(f"\n$ {cmd}\n".encode())
        try:
            # Meme abandon de privileges que pour l'application : c'est le
            # build qui execute le plus de code tiers (scripts postinstall).
            r = subprocess.run(["bash", "-lc", cmd], cwd=a["path"], env=env,
                                stdout=out, stderr=out, timeout=600,
                                preexec_fn=child_setup())
            ok = r.returncode == 0
            msg = None if ok else f"Le build a echoue (code {r.returncode}) -- voir le journal."
        except subprocess.TimeoutExpired:
            out.write(b"\n[build] delai depasse (10 min), arrete.\n")
            ok, msg = False, "Le build a depasse le delai de 10 minutes."
    return ok, msg


def is_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))


def git_pull(name):
    apps = load()
    a = apps.get(name)
    if not a:
        return False, "Application inconnue."
    if not is_git_repo(a["path"]):
        return False, "Ce dossier n'est pas un depot Git (pas de .git)."
    try:
        # git execute les hooks du depot (post-merge notamment) : le pull
        # abandonne donc les privileges comme le build.
        r = subprocess.run(["git", "pull", "--ff-only"], cwd=a["path"],
                           preexec_fn=child_setup(),
                           env=dict(os.environ, HOME=ensure_child_home()),
                            capture_output=True, text=True, timeout=120)
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Delai depasse (2 min)."
    except FileNotFoundError:
        return False, "La commande git n'est pas installee dans cette image."


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
        return jsonify({"error": "Trop de tentatives. Reessaie dans quelques minutes."}), 429
    d = request.get_json(force=True, silent=True) or request.form
    pw = (d.get("password") or "").strip()
    real = admin_password()
    # compare_digest plutot que "==" : la comparaison de chaines s'arrete au
    # premier caractere different, et la duree de la reponse renseigne alors
    # sur la longueur du prefixe correct.
    if real and pw and secrets.compare_digest(pw, real):
        session.permanent = True
        session["authed"] = True
        return jsonify({"ok": True})
    register_failed_attempt()
    return jsonify({"error": "Mot de passe incorrect."}), 401


@flask_app.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@flask_app.get("/health")
def health():
    return Response("ok\n", mimetype="text/plain")


# ------------------------------ API --------------------------------

@flask_app.get("/api/apps")
@require_auth
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
            "is_git": is_git_repo(a["path"]),
            "has_build": bool((a.get("build_command") or "").strip()),
            "build_command": a.get("build_command") or "",
            "max_memory_mb": a.get("max_memory_mb"),
            **stats,
        })
    return jsonify({"apps": out})


@flask_app.get("/api/browse")
@require_auth
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
@require_auth
def api_detect():
    path = os.path.abspath(request.args.get("path", ""))
    if not under_root(path) or not os.path.isdir(path):
        return jsonify({"command": "", "build_command": ""})
    command, build_command = detect_project(path)
    return jsonify({"command": command, "build_command": build_command})


@flask_app.post("/api/add")
@require_auth
def api_add():
    d = request.get_json(force=True)
    name = valid_name(d.get("name"))
    path = (d.get("path") or "").strip()
    command = (d.get("command") or "").strip()
    build_command = (d.get("build_command") or "").strip()
    max_memory_mb = d.get("max_memory_mb") or None
    apps = load()

    if not name:
        return jsonify({"error": "Le nom est obligatoire."}), 400
    if name in ("api", "static", "health", "login", "logout"):
        return jsonify({"error": "Ce nom est reserve."}), 400
    if name in apps:
        return jsonify({"error": "Une application porte deja ce nom."}), 400
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
    }
    save(apps)
    return jsonify({"ok": True, "name": name, "port": port})


@flask_app.put("/api/app/<n>")
@require_auth
def api_edit(n):
    apps = load()
    if n not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    if is_running(n):
        return jsonify({"error": "Arrete l'application avant de la modifier."}), 400
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
    save(apps)
    return jsonify({"ok": True})


@flask_app.post("/api/toggle/<n>")
@require_auth
def api_toggle(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    stop(n) if is_running(n) else start(n)
    return jsonify({"ok": True})


def restart_app(n):
    stop(n)
    for _ in range(30):
        if not is_running(n):
            break
        time.sleep(0.1)
    start(n)


@flask_app.post("/api/restart/<n>")
@require_auth
def api_restart(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    restart_app(n)
    return jsonify({"ok": True})


@flask_app.post("/api/deploy/<n>")
@require_auth
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
@require_auth
def api_build(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, msg = run_build(n)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True})


@flask_app.post("/api/git-pull/<n>")
@require_auth
def api_git_pull(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    ok, output = git_pull(n)
    if not ok:
        return jsonify({"error": output}), 400
    return jsonify({"ok": True, "output": output})


@flask_app.get("/api/metrics/<n>")
@require_auth
def api_metrics(n):
    if n not in load():
        return jsonify({"error": "Application inconnue."}), 404
    hist = get_metrics_history(n)
    return jsonify({
        "points": [{"t": t, "cpu": cpu, "mem": mem} for t, cpu, mem in hist],
    })


@flask_app.delete("/api/app/<n>")
@require_auth
def api_delete(n):
    stop(n)
    apps = load()
    apps.pop(n, None)
    save(apps)
    return jsonify({"ok": True})


@flask_app.get("/api/logs/<n>")
@require_auth
def api_logs(n):
    f = os.path.join(LOG_DIR, n + ".log")
    if not os.path.exists(f):
        return jsonify({"lines": ["Aucun journal pour le moment."]})
    with open(f, errors="replace") as fh:
        lines = [l.rstrip() for l in fh.readlines()[-120:]]
    return jsonify({"lines": lines or ["Journal vide."]})


@flask_app.get("/api/logs/<n>/stream")
@require_auth
def api_logs_stream(n):
    f = os.path.join(LOG_DIR, n + ".log")

    def gen():
        pos = max(0, os.path.getsize(f) - 4000) if os.path.exists(f) else 0
        yield "retry: 2000\n\n"
        while True:
            if os.path.exists(f):
                with open(f, errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n"
            time.sleep(0.5)

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


@flask_app.get("/api/icon/<n>")
@require_auth
def api_icon(n):
    apps = load()
    a = apps.get(n)
    icon_path = find_icon(a["path"]) if a else None
    if icon_path:
        return send_file(icon_path)
    return Response(default_icon_svg(n), mimetype="image/svg+xml")




# ------------------------------ pages --------------------------------

@flask_app.get("/login")
def login_page():
    if is_authed():
        return redirect("/")
    return Response(LOGIN_PAGE, mimetype="text/html")


@flask_app.get("/")
@require_auth
def index():
    return Response(DASHBOARD_PAGE.replace("__ROOT__", json.dumps(ROOT)), mimetype="text/html")


# ------------------------------ proxy --------------------------------

def strip_session_cookie(raw):
    """Retire le cookie de session du panneau d'un en-tete Cookie."""
    nom = flask_app.config.get("SESSION_COOKIE_NAME") or "session"
    gardes = [c.strip() for c in raw.split(";")
              if c.strip() and c.split("=", 1)[0].strip() != nom]
    return "; ".join(gardes)


def _proxy(name, sub):
    a = load().get(name)
    if not a:
        return Response(_page("Introuvable", "Aucune application \u00ab " + name + " \u00bb."),
                        404, mimetype="text/html")
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
    return Response(data, status, [(k, v) for k, v in headers.items()
                                   if k.lower() not in HOP])


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


# -------------------------------- main --------------------------------

if __name__ == "__main__":
    bootstrap_secrets()
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(APPS_FILE):
        save({})
    resume()
    start_monitor_thread()
    flask_app.run(host="0.0.0.0",
                  port=int(os.environ.get("MANAGER_PORT", "9001")),
                  threaded=True)

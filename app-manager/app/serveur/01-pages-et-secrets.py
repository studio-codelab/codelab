# CodeLab -- LE PANNEAU, PAGES ET SECRETS
# ---------------------------------------------------------------------------
# ROLE        les pages et scripts servis, lus une fois ; les secrets (credentials.env).
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   pages servies, bootstrap secrets.
#

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
# La seule page d'un compte en attente de suppression (voir « supprimer son
# propre compte »), a marqueurs : __JETON__, __DATE__.
SUPPRESSION_PAGE = _lire_ressource("suppression.html")
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
# Compresse UNE fois, au demarrage : 269 Ko, 87 en gzip. Voir panneau_js.
PANNEAU_JS_GZIP = gzip.compress(PANNEAU_JS.encode("utf-8"), compresslevel=9, mtime=0)
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
    # Le cookie suit ensuite chaque requete (voir SessionSelonLaConnexion).
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


def lire_fichier_de_repli(path):
    """Lit un fichier de repli d'un secret. Renvoie None s'il est absent ou vide."""
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
        "# APP_MANAGER_TOURNAGE_CLE : « codelab video » la presente pour obtenir",
        "#   un laissez-passer de tournage (une application, dix minutes). La",
        "#   changer et redemarrer codelab-dev suffit a revoquer l'ancienne.",
    ]
    valeurs = {
        "APP_MANAGER_URL": "http://<IP-du-serveur>:9001",
        "APP_MANAGER_ADMIN_LOGIN": NOM_ADMIN,
        "APP_MANAGER_ADMIN_PASSWORD": pw,
        "APP_MANAGER_SESSION_SECRET": key,
    }
    if _cle_chiffrement:
        valeurs["APP_MANAGER_CHIFFREMENT_CLE"] = _cle_chiffrement
    if _cle_tournage:
        valeurs["APP_MANAGER_TOURNAGE_CLE"] = _cle_tournage
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
    defaut on reprend les fichiers de repli ; a defaut on genere. Ne leve jamais : un secret illisible est regenere plutot que
    d'empecher le service de demarrer."""
    global _admin_password
    os.makedirs(STATE_DIR, exist_ok=True)

    pw = read_shared_value("APP_MANAGER_ADMIN_PASSWORD")
    key = read_shared_value("APP_MANAGER_SESSION_SECRET")

    if not pw:
        pw = lire_fichier_de_repli(REPLI_ADMIN_PASSWORD_FILE)
        if pw:
            print("[app-manager] mot de passe admin repris de "
                  f"{REPLI_ADMIN_PASSWORD_FILE} (repli).", flush=True)
        else:
            pw = secrets.token_urlsafe(18)
            print("[app-manager] mot de passe admin genere.", flush=True)

    if not key:
        key = lire_fichier_de_repli(REPLI_SECRET_KEY_FILE) or secrets.token_hex(32)

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

    # La cle des laissez-passer de tournage (voir « le laissez-passer de
    # tournage », dans le proxy). Generee une fois, gardee ensuite : le poste
    # de developpement la recopie pour sa session.
    global _cle_tournage
    _cle_tournage = read_shared_value("APP_MANAGER_TOURNAGE_CLE") or secrets.token_urlsafe(32)

    written = ecrire_bloc_panneau(pw, key, _totp_secret)

    if written:
        # Les valeurs sont dans credentials.env : les fichiers de repli
        # n'ont plus de raison d'exister. Supprimes seulement apres une
        # ecriture reussie, jamais avant.
        for path in (REPLI_ADMIN_PASSWORD_FILE, REPLI_SECRET_KEY_FILE):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    print(f"[app-manager] {path} supprime (valeur reprise dans "
                          f"{SHARED_ENV_FILE}).", flush=True)
            except OSError:
                pass
    else:
        # credentials.env inaccessible : on retombe sur les fichiers dedies,
        # sinon un redemarrage regenererait un mot de passe different.
        for path, value in ((REPLI_ADMIN_PASSWORD_FILE, pw),
                            (REPLI_SECRET_KEY_FILE, key)):
            try:
                with open(path, "w") as f:
                    f.write(value)
                os.chmod(path, 0o600)
            except OSError:
                pass

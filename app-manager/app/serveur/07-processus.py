# CodeLab -- LE PANNEAU, PROCESSUS
# ---------------------------------------------------------------------------
# ROLE        la persistance des applications, les privileges, les secrets transmis, la base de chaque projet, l'isolation, le cycle de vie, la surveillance.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   persistance, privileges, uid par application, secrets transmis, une base un compte, isolation, cycle de vie, survie des processus, redemarrage, sonde d'ecoute.
#

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
    # .get : une application archivee dont la configuration est effacee n'a
    # plus de port.
    used = {a.get("port") for a in apps.values()}
    # Un releve neuf, mais un seul pour toute la plage : il en faisait un
    # par port essaye (voir _ports_ecoutes).
    releve = _ports_ecoutes()
    for p in range(PORT_MIN, PORT_MAX + 1):
        if p in used:
            continue
        if _ecouteurs_port(p, releve):
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
    valeurs.update(alias_du_relais_mail(valeurs))
    return valeurs


# Les noms sous lesquels les cadriciels cherchent leur serveur d'envoi :
# SMTP_* (Nodemailer, la plupart des exemples), EMAIL_* (Django),
# MAIL_SERVER (Flask-Mail). Vecu : « une nouvelle app doit prendre le SMTP du
# hub par defaut si je n'en parametre pas dans l'app » -- une application qui
# lisait SMTP_HOST ne trouvait rien, alors que Mailpit l'attendait.
ALIAS_MAIL = {"SMTP_HOST": "MAIL_HOST", "SMTP_PORT": "MAIL_PORT", "SMTP_URL": "MAIL_URL",
              "EMAIL_HOST": "MAIL_HOST", "EMAIL_PORT": "MAIL_PORT",
              "MAIL_SERVER": "MAIL_HOST"}


def alias_du_relais_mail(valeurs):
    """Les autres noms du SMTP de Mailpit, recopies de MAIL_*.

    POURQUOI : une application sans serveur d'envoi a elle doit passer par
      le relais du hub (Mailpit, qui relaie par Parametres > E-mail) sans
      jamais recevoir de quoi s'identifier aupres du vrai serveur.
    REGLE : 19 de SECURITE.md. Seuls l'hote, le port et l'URL de Mailpit
      sont recopies : jamais SMTP_USER, SMTP_PASSWORD ni SMTP_TLS, et la
      valeur vient de MAIL_* (le bloc codelab-mailpit), jamais des SMTP_* du
      panneau, que CLES_PANNEAU retire plus haut.
    DEPEND DE : mailpit/entrypoint.sh (ecrire_bloc), qui publie MAIL_*.
    Le .env du projet l'emporte : un projet qui y nomme son serveur ne recoit
    rien du relais (retirer_le_relais_si_le_projet_a_son_smtp).
    """
    return {alias: valeurs[source] for alias, source in ALIAS_MAIL.items()
            if valeurs.get(source)}


# Tout ce qui designe le relais du hub : les MAIL_* de Mailpit et leurs alias.
VARIABLES_DU_RELAIS = frozenset(ALIAS_MAIL) | frozenset(ALIAS_MAIL.values())
# Ce qui, dans le .env d'un projet, nomme un serveur d'envoi a lui.
SERVEUR_MAIL_DU_PROJET = ("SMTP_HOST", "SMTP_URL", "EMAIL_HOST", "MAIL_SERVER",
                          "MAIL_HOST", "MAIL_URL")


def retirer_le_relais_si_le_projet_a_son_smtp(env, chemin):
    """Sans le relais du hub, des qu'un projet nomme son serveur d'envoi.

    POURQUOI : « si je n'en parametre pas dans l'app ». La plupart des
      chargeurs de .env (python-dotenv, dotenv de Node) n'ecrasent pas une
      variable deja recue : avec SMTP_HOST pose par le panneau, le SMTP_HOST
      du .env du projet etait ignore, et le mail partait par Mailpit. Pire,
      un .env qui ne posait que l'hote recevait le port 1025 de Mailpit.
    Le projet qui nomme un serveur (SERVEUR_MAIL_DU_PROJET) ne recoit donc
    aucune variable du relais : son .env decide seul, port compris.
    """
    if any(_valeur_env_projet(chemin, cle) for cle in SERVEUR_MAIL_DU_PROJET):
        for cle in VARIABLES_DU_RELAIS:
            env.pop(cle, None)


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
    if est_archivee(a):
        return f"« {name} » est aux archives : désarchive-la avant de la démarrer."

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
    retirer_le_relais_si_le_projet_a_son_smtp(env, a["path"])
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


def _ports_ecoutes():
    """Les ports TCP en ecoute, avec les PID qui les tiennent : {port: [pid]}.

    UN RELEVE, ET UN SEUL PAR REQUETE. psutil.net_connections() passe en
    revue toutes les connexions et tous les descripteurs de tous les
    processus du conteneur -- applications comprises -- : c'est cher, et ce
    cout grandit avec chaque application. Vecu (test de charge) :
    /api/mes-apps le faisait trois fois par appel, une par tuile de service,
    et /api/apps une fois par application ; 96 % du temps de la requete y
    passait, et le hub tombait de 46 a 11 requetes par seconde des dix
    clients. Une liste demande donc ce releve une fois et le lit pour chaque
    ligne.
    ESSAYE ET ECARTE : un releve garde deux secondes pour toutes les
    requetes. Il servait aussi next_port et le demarrage, ou un
    port pris depuis une seconde doit se voir.
    """
    ports = {}
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr or not conn.pid:
                continue
            ports.setdefault(int(conn.laddr.port), set()).add(int(conn.pid))
    except (psutil.Error, OSError):
        pass
    return {p: sorted(pids) for p, pids in ports.items()}


def _ecouteurs_port(port, releve=None):
    """Retourne les PID qui ecoutent reellement sur le port TCP.

    Le simple bind sur 127.0.0.1 ne voit pas proprement tous les cas
    (IPv6, adresse specifique, socket deja publie). psutil donne ici le meme
    niveau d'information que « ss -ltnp », sans dependre d'un binaire
    externe. Sans releve fourni, un releve neuf : c'est ce que veulent le
    demarrage et le choix d'un port libre.
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        return []
    return (_ports_ecoutes() if releve is None else releve).get(port, [])


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
        if nom == DIAGNOSTIC_NOM and not (a.get("description") or "").strip():
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

# Connexions ouvertes en meme temps, actives ou inactives. Le defaut de
# waitress (100) se remplit vite : une connexion inactive reste ouverte
# jusqu'a WSGI_TIMEOUT, un navigateur en garde jusqu'a six, et un flux de
# journal en tient une. Au-dela, la connexion suivante attend dans la file
# du systeme sans qu'on lui reponde. Vecu (test de charge) : des 100 a 200
# clients, des requetes de 12 secondes sur tous les ecrans, healthcheck
# compris ; a 1 000, le debit reste stable et le 99e centile tombe sous
# 1,5 s. Mille sockets restent tres loin de la limite de descripteurs
# d'un conteneur.
WSGI_CONNEXIONS = int(os.environ.get("APP_MANAGER_CONNEXIONS", "1000"))

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


# nom -> True si l'application ecoute sur toutes les interfaces (0.0.0.0,
# ::) plutot que sur 127.0.0.1. Meme convention que _listening.
_ecoute_large = {}

# Les adresses qui ouvrent le port a tout le reseau du conteneur.
ADRESSES_TOUT_RESEAU = {"0.0.0.0", "::", "::ffff:0.0.0.0"}


def ports_ouverts_au_reseau():
    """Les ports TCP ecoutes sur toutes les interfaces du conteneur.

    POURQUOI : une application qui ecoute sur 0.0.0.0 est joignable depuis
      les autres conteneurs du reseau codelab (codelab-dev, codelab-dagster)
      sans passer par le proxy, donc sans connexion ni droit : une
      application privee y perd sa protection. Le panneau ne peut pas
      l'interdire (le code de l'application choisit son adresse), il le dit
      dans la fiche.
    DEPEND DE : ECOUTE_LOCALE (09-applications.py), l'adresse des commandes
      proposees.
    ECARTE : un pare-feu dans le conteneur (il faudrait NET_ADMIN, une
      permission de plus pour le panneau) ; un espace reseau par
      application (le proxy ne la joindrait plus par 127.0.0.1).
    Un releve par tour de sonde (toutes les 10 s), pas par requete : voir
    le cout de psutil.net_connections dans _ports_ecoutes.
    """
    ports = set()
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if (conn.status == psutil.CONN_LISTEN and conn.laddr
                    and conn.laddr.ip in ADRESSES_TOUT_RESEAU):
                ports.add(int(conn.laddr.port))
    except (psutil.Error, OSError):
        pass
    return ports


def probe_tick():
    """Un tour de sonde sur les applications en cours d'execution."""
    apps = load()
    for name in list(_listening):
        if name not in apps or not is_running(name):
            _listening.pop(name, None)
            _ecoute_large.pop(name, None)
    ouverts = None
    for name, a in apps.items():
        if is_running(name):
            _listening[name] = probe_port(a["port"])
            if ouverts is None:
                ouverts = ports_ouverts_au_reseau()
            _ecoute_large[name] = int(a["port"]) in ouverts


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

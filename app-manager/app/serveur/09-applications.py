# CodeLab -- LE PANNEAU, APPLICATIONS
# ---------------------------------------------------------------------------
# ROLE        le build, les metriques, la detection de commande, icones et logos, categories, adresse publique, administration sur le reseau local, visibilite.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   build, metriques et pouls du serveur, auto-detection, icones, logo, categories, adresse publique, reseau local, visibilite.
#

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
#
# Chaque commande proposee ecoute sur 127.0.0.1 (ECOUTE_LOCALE) : le panneau
# relaie depuis le meme conteneur, et rien d'autre n'a a joindre le port
# interne. POURQUOI : ecoutee sur toutes les interfaces, une application
# privee etait joignable depuis les autres conteneurs du reseau codelab
# (codelab-dev, codelab-dagster...) sans passer par le panneau, donc sans
# connexion ni droit. Vecu : python3 -m http.server et next start ecoutent
# partout par defaut, et la commande Django portait 0.0.0.0 en dur.
# DEPEND DE : probe_port et le proxy (13-hub-et-proxy.py), qui joignent
# l'application par 127.0.0.1 ; ports_ouverts_au_reseau signale dans la
# fiche une application qui ecoute encore partout.
ECOUTE_LOCALE = "127.0.0.1"

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
            return f"npx next start --port $PORT --hostname {ECOUTE_LOCALE}", build

        for dep, outdir in STATIC_BUILDERS.items():
            if dep in deps and "build" in scripts:
                return (f"python3 -m http.server $PORT --bind {ECOUTE_LOCALE} "
                        f"--directory {outdir}"), build

        if "start" in scripts:
            return "npm start", build
        if pkg.get("main"):
            return f"node {pkg['main']}", build
        return "npm start", build

    if exists("manage.py"):
        return f"python3 manage.py runserver {ECOUTE_LOCALE}:$PORT", _python_install(path)

    if exists("app.py"):
        return "python3 app.py", _python_install(path)

    if exists("main.py"):
        return "python3 main.py", _python_install(path)

    if exists("Procfile"):
        for line in read("Procfile").splitlines():
            if line.strip().startswith("web:"):
                return line.split(":", 1)[1].strip(), ""

    if exists("index.html"):
        return f"python3 -m http.server $PORT --bind {ECOUTE_LOCALE}", ""

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
# La liste vit dans sa propre table plutot que dans le registre des
# applications : une categorie existe avant qu'un projet la porte (on la cree
# pour ranger ensuite), et elle survit a la suppression du dernier projet qui
# l'utilisait.
# Un champ libre par projet aurait produit "Outils", "outils" et "Outil ".
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

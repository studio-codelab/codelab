# CodeLab -- LE PANNEAU, MESSAGES ET JOURNAL
# ---------------------------------------------------------------------------
# ROLE        la messagerie, les pieces jointes, les applications masquees, le journal des acces, son miroir Postgres, les analyses.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   messages, pieces jointes, applications masquees, journal des acces, miroir Postgres, analyses.
#

# ------------------------- messages des utilisateurs -------------------------
#
# Un mot laisse depuis le hub : une remarque sur une application, une
# remarque sur le hub lui-meme, ou l'idee d'une application qui manque.
#
# Deux ecritures, comme pour le journal des acces, et pour la meme raison :
# la base LOCALE d'abord (codelab.db), qui tient sans Postgres et se lit
# depuis une session SSH, puis Postgres, qui garde tout et s'interroge en
# SQL. Un message perdu parce que Postgres redemarrait serait un message que
# personne ne saura jamais avoir ete ecrit.
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
# Une table a part plutot qu'un champ dans le registre des comptes : le
# compte d'administration n'y figure pas, et il a le droit de ranger son hub
# comme les autres.


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
    return [str(v) for v in valeurs if isinstance(v, str)]


# ------------------------- journal des acces -------------------------
#
# Qui s'est connecte, quand, et quelle application il a ouverte. Deux usages,
# et deux seulement : reconnaitre une tentative d'intrusion (des echecs de
# connexion en rafale, une connexion a 4 h du matin), et savoir si un projet
# sert encore a quelqu'un avant de l'arreter.
#
# Une table de la base locale (journal), ajoutee a la fin : des evenements,
# jamais modifies, qui se lisent depuis une session SSH (sqlite3) le jour ou
# le panneau ne repond plus. Elle est plafonnee, et ne garde que la duree
# choisie (voir purger_le_journal) -- un journal qui remplit le disque
# transforme une curiosite en panne.
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
    """L'adresse du visiteur pour le journal : celle de la connexion, comme
    _client_ip, et jamais un en-tete que le client choisit."""
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


# LA DUREE DE CONSERVATION. Le journal note les adresses IP, et une adresse IP
# est une donnee personnelle (RGPD) : on ne la garde pas plus longtemps que
# son usage -- retrouver qui s'est connecte, comprendre un incident.
# Postgres gardait tout, sans limite ; le fichier local, 50 000 evenements
# quel que soit leur age. Une duree, reglee par l'administrateur, vaut
# desormais pour les DEUX : purger Postgres seul ne suffit pas, le
# rattrapage du demarrage (_pg_rattraper) y recopierait le journal local.
# Vecu : « limiter la duree de conservation du journal ». Douze mois par
# defaut, l'ordre de grandeur que recommande la CNIL pour des journaux de
# connexion ; « sans limite » reste possible, c'est un choix assume.
# Les sauvegardes, elles, gardent le journal du jour ou elles ont ete
# faites, et partent avec leur rotation.
CONSERVATION_CHOIX = (3, 6, 12, 24, 0)     # en mois ; 0 = sans limite
CONSERVATION_DEFAUT = 12
PURGE_INTERVALLE = 86400                   # une fois par jour
PURGE_PREMIERE = 120                       # apres le demarrage : Postgres a eu le temps de repondre


def lire_conservation():
    """La duree choisie, en mois (0 = sans limite). Une valeur inconnue,
    venue d'une ancienne version ou d'un coffre abime, retombe sur le
    defaut plutot que de tout garder -- ou de tout effacer."""
    d = reglage("conservation_journal", {}) or {}
    mois = d.get("mois") if isinstance(d, dict) else None
    return mois if mois in CONSERVATION_CHOIX and not isinstance(mois, bool) else CONSERVATION_DEFAUT


def ecrire_conservation(mois):
    ecrire_reglage("conservation_journal", {"mois": mois})


def seuil_conservation(mois, maintenant=None):
    """L'instant (epoch) avant lequel un evenement est trop vieux, ou None
    sans limite. Un mois vaut 365,25 / 12 jours : douze mois font un an."""
    if not mois:
        return None
    maintenant = time.time() if maintenant is None else maintenant
    return int(maintenant - mois * (365.25 / 12) * 86400)


def purger_le_journal(maintenant=None):
    """Retire du journal local ET de Postgres ce qui depasse la duree choisie.

    Rend (local, base) : le nombre de lignes retirees de chaque cote, base
    valant None quand Postgres ne repond pas (il sera purge au prochain
    passage). Ne leve jamais : une purge ratee ne doit pas arreter le fil.
    """
    seuil = seuil_conservation(lire_conservation(), maintenant)
    if seuil is None:
        return 0, 0
    local = 0
    try:
        with stockage.transaction(DB_FILE) as cx:
            local = cx.execute("DELETE FROM journal WHERE ts < ?", (seuil,)).rowcount
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] purge du journal local impossible : {e}", flush=True)
    base = None
    if pg_disponible():
        try:
            with _pg_connexion(PG_BASE) as cx:
                base = cx.execute("DELETE FROM acces WHERE ts < to_timestamp(%s)",
                                  (seuil,)).rowcount
        except Exception as e:                                    # noqa: BLE001
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
    if local or base:
        print(f"[app-manager] journal purge : {local} ligne(s) locale(s), "
              f"{base or 0} dans la base.", flush=True)
    return local, base


def demarrer_la_purge_du_journal():
    def _boucle():
        time.sleep(PURGE_PREMIERE)
        while True:
            purger_le_journal()
            # Les comptes dont le delai de suppression est passe : le meme
            # rythme, une fois par jour (voir « supprimer son propre compte »).
            purger_les_comptes_supprimes()
            # Et les applications archivees depuis trente jours (voir
            # « l'archivage d'une application »).
            purger_les_apps_archivees()
            time.sleep(PURGE_INTERVALLE)
    threading.Thread(target=_boucle, daemon=True).start()


@flask_app.get("/api/journal/conservation")
@require_admin
def api_conservation_lire():
    return jsonify({"mois": lire_conservation(), "choix": list(CONSERVATION_CHOIX)})


@flask_app.put("/api/journal/conservation")
@require_admin
def api_conservation_ecrire():
    """Enregistre la duree et purge AUSSITOT : raccourcir la duree, c'est
    vouloir que les vieilles lignes partent maintenant, pas demain."""
    mois = (request.get_json(silent=True) or {}).get("mois")
    # bool est un int en Python : True ne doit pas passer pour « 1 mois ».
    if isinstance(mois, bool) or mois not in CONSERVATION_CHOIX:
        return jsonify({"error": "Durée invalide."}), 400
    try:
        ecrire_conservation(mois)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Réglage non enregistré : {e}"}), 500
    journaliser("reglage", qui=utilisateur_courant() or NOM_ADMIN,
                action=f"conservation du journal : {mois} mois" if mois
                else "conservation du journal : sans limite", ip=_adresse_client())
    local, base = purger_le_journal()
    return jsonify({"ok": True, "mois": mois, "retirees": local + (base or 0)})


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
    # En attente de suppression : de meme, plus rien pendant le delai.
    if not compte or compte_suspendu(compte) or compte_en_suppression(compte):
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
    # Archivee : personne ne l'ouvre, administrateur compris, jusqu'a ce
    # qu'elle soit desarchivee (voir « l'archivage d'une application »).
    if est_archivee(load().get(name)):
        return False
    autorises = projets_autorises()
    return autorises is None or name in autorises

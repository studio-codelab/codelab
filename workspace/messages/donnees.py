"""
CodeLab -- MESSAGES : LES DONNEES
---------------------------------------------------------------------------
ROLE        Tout ce que l'application garde : les messages laisses depuis le
            hub et leurs pieces jointes, les projets, les taches et leurs
            sous-taches, le catalogue des applications que le panneau lui
            transmet. Les regles de validation vivent ici, avec les
            ecritures : une route ne fait que traduire HTTP.
            Ne fait PAS : l'identite (app.py), l'envoi de mails ni les
            notifications (le panneau, qui sait joindre l'administrateur).
CHARGE PAR  app.py, et la suite de tests du depot.
CONTRAINTES - La base est celle du projet, dans Postgres (POSTGRES_* et
              CODELAB_DB, poses par le panneau : « une base, un compte »).
              MESSAGES_SQLITE, un chemin de fichier, la remplace par SQLite :
              c'est ce que font les tests, qui tournent sans Postgres.
            - Le SQL est donc ecrit pour les deux : des « ? » (convertis en
              « %s » pour psycopg), ni « % » ni « ? » litteraux, rien de
              propre a l'un des deux moteurs hors du type des octets.
            - MIGRATIONS : on AJOUTE a la fin, on ne modifie jamais une
              migration passee (elle a tourne chez l'utilisateur).
STRUCTURE   la base, les migrations, le catalogue des applications, les
            messages, les pieces jointes, les projets et les taches, le
            suivi cote auteur, l'oubli d'un compte, la reprise des donnees
            du panneau, l'export d'un projet.
CHOIX       Une connexion par requete : quelques millisecondes, et aucune
            connexion morte a surveiller quand Postgres redemarre.
A AMELIORER Un pool de connexions, si la boite depasse quelques milliers de
            messages et que la page ralentit (a mesurer d'abord).
"""
import contextlib
import datetime
import io
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
import urllib.parse
import zipfile

# --------------------------------- la base ---------------------------------

SQLITE = os.environ.get("MESSAGES_SQLITE") or ""


class Base:
    """Une connexion, et les trois gestes dont le reste a besoin."""

    def __init__(self, cx, pg):
        self.cx, self.pg = cx, pg

    def _sql(self, requete):
        return requete.replace("?", "%s") if self.pg else requete

    def executer(self, requete, valeurs=()):
        cur = self.cx.cursor()
        cur.execute(self._sql(requete), tuple(valeurs))
        return cur

    def lignes(self, requete, valeurs=()):
        cur = self.executer(requete, valeurs)
        noms = [d[0] for d in cur.description]
        return [dict(zip(noms, l)) for l in cur.fetchall()]

    def un(self, requete, valeurs=()):
        lignes = self.lignes(requete, valeurs)
        return lignes[0] if lignes else None

    def valeur(self, requete, valeurs=()):
        ligne = self.executer(requete, valeurs).fetchone()
        return ligne[0] if ligne else None


def _connexion():
    if SQLITE:
        cx = sqlite3.connect(SQLITE, timeout=10)
        return Base(cx, pg=False)
    import psycopg  # installe dans vendor/ par le build
    cx = psycopg.connect(host=os.environ.get("POSTGRES_HOST") or "codelab-postgres",
                         port=int(os.environ.get("POSTGRES_PORT") or 5432),
                         dbname=os.environ.get("CODELAB_DB") or "messages",
                         user=os.environ.get("POSTGRES_USER") or "messages",
                         password=os.environ.get("POSTGRES_PASSWORD") or "",
                         connect_timeout=5)
    return Base(cx, pg=True)


@contextlib.contextmanager
def base():
    """Une transaction : validee en sortie, annulee sur exception."""
    b = _connexion()
    try:
        yield b
        b.cx.commit()
    except BaseException:
        b.cx.rollback()
        raise
    finally:
        b.cx.close()


# ------------------------------ les migrations ------------------------------
#
# {OCTETS} devient BYTEA (Postgres) ou BLOB (SQLite) : le seul mot qui
# differe entre les deux moteurs.
MIGRATIONS = [
    # --- version 1 : ce que le panneau gardait dans codelab.db ---
    """
    CREATE TABLE messages (
        id       TEXT PRIMARY KEY,
        ts       BIGINT NOT NULL,
        qui      TEXT NOT NULL DEFAULT '',
        cible    TEXT NOT NULL,
        app      TEXT NOT NULL DEFAULT '',
        texte    TEXT NOT NULL DEFAULT '',
        statut   TEXT NOT NULL DEFAULT 'en_attente',
        lu       INTEGER NOT NULL DEFAULT 0,
        retenu   INTEGER NOT NULL DEFAULT 0,
        archive  INTEGER NOT NULL DEFAULT 0,
        projet   TEXT,
        ordre    INTEGER
    );
    CREATE INDEX messages_ts ON messages(ts);
    CREATE INDEX messages_qui ON messages(qui, ts);
    CREATE TABLE pieces (
        id       TEXT PRIMARY KEY,
        message  TEXT,
        qui      TEXT NOT NULL,
        nom      TEXT NOT NULL,
        type     TEXT NOT NULL,
        taille   INTEGER NOT NULL,
        ts       BIGINT NOT NULL,
        rang     INTEGER NOT NULL DEFAULT 0,
        donnees  {OCTETS} NOT NULL
    );
    CREATE INDEX pieces_message ON pieces(message);
    CREATE INDEX pieces_qui ON pieces(qui, message);
    CREATE TABLE projets (
        id           TEXT PRIMARY KEY,
        nom          TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        etape        TEXT NOT NULL DEFAULT 'idee',
        application  TEXT,
        message      TEXT,
        cree         BIGINT NOT NULL,
        maj          BIGINT NOT NULL,
        ordre        INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE taches (
        id        TEXT PRIMARY KEY,
        projet    TEXT NOT NULL,
        titre     TEXT NOT NULL,
        message   TEXT,
        cree      BIGINT NOT NULL,
        statut    TEXT NOT NULL DEFAULT 'a_faire',
        priorite  INTEGER NOT NULL DEFAULT 2,
        echeance  TEXT,
        notes     TEXT NOT NULL DEFAULT '',
        ordre     INTEGER NOT NULL DEFAULT 0,
        duree     INTEGER NOT NULL DEFAULT 1,
        fait_le   BIGINT
    );
    CREATE INDEX taches_statut ON taches(statut, ordre);
    CREATE TABLE sous_taches (
        id     TEXT PRIMARY KEY,
        tache  TEXT NOT NULL,
        titre  TEXT NOT NULL,
        faite  INTEGER NOT NULL DEFAULT 0,
        ordre  INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX sous_taches_tache ON sous_taches(tache, ordre);
    CREATE TABLE applications (
        name   TEXT PRIMARY KEY,
        titre  TEXT NOT NULL
    );
    """,
]


def preparer():
    """Joue les migrations qui manquent, chacune dans sa transaction."""
    with base() as b:
        b.executer("CREATE TABLE IF NOT EXISTS version_schema (version INTEGER NOT NULL)")
        if b.valeur("SELECT COUNT(*) FROM version_schema") == 0:
            b.executer("INSERT INTO version_schema (version) VALUES (0)")
    for rang, migration in enumerate(MIGRATIONS, start=1):
        with base() as b:
            if b.valeur("SELECT version FROM version_schema") >= rang:
                continue
            texte = migration.replace("{OCTETS}", "BYTEA" if b.pg else "BLOB")
            for ordre in (o.strip() for o in texte.split(";")):
                if ordre:
                    b.executer(ordre)
            b.executer("UPDATE version_schema SET version = ?", (rang,))


# ------------------------ le catalogue des applications ------------------------
#
# Une tache peut appartenir a une application declaree, et la boite cite
# l'application dont parle un message. L'application Messages ne lit pas le
# registre du panneau (elle tourne isolee) : le panneau lui envoie la liste,
# nom et titre, a chaque synchronisation (voir /interne/synchroniser).

def ecrire_applications(liste):
    propres = {}
    for a in liste or []:
        if not isinstance(a, dict):
            continue
        nom, titre = str(a.get("name") or ""), str(a.get("titre") or a.get("name") or "")
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", nom):
            propres[nom] = titre[:120]
    with base() as b:
        b.executer("DELETE FROM applications")
        for nom, titre in sorted(propres.items()):
            b.executer("INSERT INTO applications (name, titre) VALUES (?, ?)", (nom, titre))


def lire_applications():
    with base() as b:
        return b.lignes("SELECT name, titre FROM applications ORDER BY name")


def _application_connue(b, nom):
    return bool(nom) and b.valeur("SELECT 1 FROM applications WHERE name = ?", (nom,)) == 1


# ------------------------------- les messages -------------------------------
#
# Un mot laisse depuis le hub : une remarque sur une application, sur le hub
# lui-meme, ou l'idee d'une application qui manque. Le panneau a verifie qui
# ecrit et sur quoi (il sait quelles applications ce compte peut ouvrir) ;
# ce module verifie la forme.
MESSAGES_MAX = 5000          # au-dela, les plus anciens non retenus partent
MESSAGE_LONGUEUR_MAX = 2000
MESSAGE_CIBLES = ("application", "hub", "idee")
MESSAGES_AFFICHES = 300
# en_attente : nouveau, rien de decide. a_reflechir : mis de cote pour y
# revenir. accepte / refuse : traite.
STATUTS_MESSAGE = ("en_attente", "a_reflechir", "accepte", "refuse")
BOOLEENS_MESSAGE = ("lu", "retenu", "archive")


class Refus(ValueError):
    """Une demande mal formee : le texte est montre tel quel, le code dit
    pourquoi (400 par defaut, 404 pour ce qui n'existe pas...)."""

    def __init__(self, texte, code=400):
        super().__init__(texte)
        self.code = code


def _message(ligne, pieces):
    m = dict(ligne)
    for cle in BOOLEENS_MESSAGE:
        m[cle] = bool(m[cle])
    m["pieces"] = pieces.get(m["id"], [])
    return m


def _pieces_des_messages(b, ids):
    par_message = {}
    if not ids:
        return par_message
    for l in b.lignes(f"SELECT id, message, nom, type, taille FROM pieces "
                      f"WHERE message IN ({','.join('?' * len(ids))}) ORDER BY rang, ts, id", ids):
        par_message.setdefault(l.pop("message"), []).append(l)
    return par_message


def lire_messages(limite=MESSAGES_AFFICHES, qui=None, apres=0):
    """Les plus recents d'abord ; ceux d'un compte, ou arrives apres une date."""
    conditions, valeurs = ["ts > ?"], [int(apres or 0)]
    if qui is not None:
        conditions.append("qui = ?")
        valeurs.append(qui)
    with base() as b:
        lignes = b.lignes(f"SELECT * FROM messages WHERE {' AND '.join(conditions)} "
                          f"ORDER BY ts DESC, id DESC LIMIT ?", (*valeurs, int(limite)))
        pieces = _pieces_des_messages(b, [l["id"] for l in lignes])
    return [_message(l, pieces) for l in lignes]


def compter_messages():
    """(non lus, dans la boite) : la tuile du hub et l'entete de la boite."""
    with base() as b:
        return (b.valeur("SELECT COUNT(*) FROM messages WHERE lu = 0 AND archive = 0"),
                b.valeur("SELECT COUNT(*) FROM messages WHERE archive = 0"))


def _message_existe(b, ident):
    return b.valeur("SELECT 1 FROM messages WHERE id = ?", (ident,)) == 1


def nouveau_message(qui, cible, app, texte, ids_pieces):
    """Range un message et rattache ses pieces deposees. Rend le message."""
    texte = str(texte or "").strip()
    if cible not in MESSAGE_CIBLES:
        raise Refus("Choisis de quoi tu veux parler.")
    if (not isinstance(ids_pieces, list) or len(ids_pieces) > PIECES_PAR_MESSAGE
            or not all(isinstance(x, str) for x in ids_pieces)):
        raise Refus(f"{PIECES_PAR_MESSAGE} pièces jointes au plus.")
    ids_pieces = list(dict.fromkeys(ids_pieces))
    if len(texte) > MESSAGE_LONGUEUR_MAX:
        raise Refus("Message trop long : %d caractères pour %d au maximum."
                    % (len(texte), MESSAGE_LONGUEUR_MAX))
    app = str(app or "") if cible == "application" else ""
    m = {"id": secrets.token_hex(12), "ts": int(time.time()), "qui": qui, "cible": cible,
         "app": app, "texte": texte}
    with base() as b:
        _oublier_pieces_orphelines(b)
        if ids_pieces:
            trouvees = b.valeur(f"SELECT COUNT(*) FROM pieces WHERE message IS NULL AND qui = ? "
                                f"AND id IN ({','.join('?' * len(ids_pieces))})",
                                (qui, *ids_pieces))
            if trouvees != len(ids_pieces):
                raise Refus("Une pièce jointe a expiré : ajoute-la de nouveau.")
        # Une maquette peut se passer de mots ; un message sans rien, non.
        if not texte and not ids_pieces:
            raise Refus("Écris ton message.")
        b.executer("INSERT INTO messages (id, ts, qui, cible, app, texte) VALUES (?, ?, ?, ?, ?, ?)",
                   (m["id"], m["ts"], qui, cible, app, texte))
        # Dans l'ordre ou le message les nomme : c'est celui de l'auteur.
        for rang, ident in enumerate(ids_pieces):
            b.executer("UPDATE pieces SET message = ?, rang = ? WHERE message IS NULL "
                       "AND qui = ? AND id = ?", (m["id"], rang, qui, ident))
        # Plafond : une boite qui se remplit en boucle ne doit pas remplir le
        # disque. Les plus anciens partent, les retenus restent.
        b.executer("DELETE FROM messages WHERE retenu = 0 AND id NOT IN "
                   "(SELECT id FROM messages WHERE retenu = 0 ORDER BY ts DESC LIMIT ?)",
                   (MESSAGES_MAX,))
        _oublier_pieces_orphelines(b)
        m["pieces"] = _pieces_des_messages(b, [m["id"]]).get(m["id"], [])
    return m


def modifier_message(ident, d):
    """Change l'etat d'un message. Seules les cles PRESENTES changent."""
    champs, valeurs = [], []
    if "statut" in d:
        if d["statut"] not in STATUTS_MESSAGE:
            raise Refus("Statut inconnu.")
        champs.append("statut = ?")
        valeurs.append(d["statut"])
    for cle in BOOLEENS_MESSAGE:
        if cle in d:
            champs.append(f"{cle} = ?")
            valeurs.append(1 if d[cle] else 0)
    with base() as b:
        if "projet" in d:
            projet = d.get("projet") or None
            if projet and (projet == PROJET_GENERAL or not _projet_valide(b, projet)):
                raise Refus("Projet inconnu.")
            champs.append("projet = ?")
            valeurs.append(projet)
        if not _message_existe(b, ident):
            raise Refus("Message introuvable.", 404)
        if champs:
            b.executer(f"UPDATE messages SET {', '.join(champs)} WHERE id = ?", (*valeurs, ident))


def marquer_lus():
    with base() as b:
        return b.executer("UPDATE messages SET lu = 1 WHERE lu = 0").rowcount


def supprimer_message(ident):
    with base() as b:
        if not b.executer("DELETE FROM messages WHERE id = ?", (ident,)).rowcount:
            raise Refus("Message introuvable.", 404)
        _oublier_pieces_orphelines(b)


def ordonner_messages(ordre):
    """Ranger la boite a sa guise : les messages nommes prennent ce rang ; un
    message qui arrive ensuite, sans rang, se place en tete."""
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > MESSAGES_MAX):
        raise Refus("Ordre invalide.")
    with base() as b:
        connus = {l["id"] for l in b.lignes("SELECT id FROM messages")}
        if not set(ordre) <= connus:
            raise Refus("La liste nomme un message inconnu.", 409)
        for rang, ident in enumerate(ordre):
            b.executer("UPDATE messages SET ordre = ? WHERE id = ?", (rang, ident))


def grouper_messages(ids, projet=None, nouveau=None):
    """Des messages qui parlent de la meme chose forment un projet : on les y
    range ensemble, dans un projet qui existe ou qu'on cree a l'instant."""
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        raise Refus("Choisis au moins un message.")
    with base() as b:
        connus = {l["id"] for l in b.lignes("SELECT id FROM messages")}
        if not set(ids) <= connus:
            raise Refus("Message introuvable.", 404)
        if isinstance(nouveau, dict):
            nom = _titre(nouveau.get("nom"))[:PROJET_NOM_MAX]
            if not nom:
                raise Refus("Donne un nom au projet.")
            projet = _creer_projet(b, nom, _texte_long(nouveau.get("description"),
                                                       PROJET_DESCRIPTION_MAX), message=ids[0])
        elif not projet or projet == PROJET_GENERAL or not _projet_valide(b, projet):
            raise Refus("Projet inconnu.")
        for ident in ids:
            b.executer("UPDATE messages SET projet = ?, lu = 1 WHERE id = ?", (projet, ident))
    return projet


# ----------------------------- les pieces jointes -----------------------------
#
# Une capture d'ecran, une maquette : ce qu'un message ne sait pas dire en
# mots. Images (PNG, JPEG, WebP, GIF) et PDF, rien d'autre -- reconnus a
# leurs premiers octets, pas au nom ni au type annonce. Pas de SVG : c'est
# du code, qui s'executerait a l'ouverture.
#
# Le fichier se depose d'abord, seul, puis le message le nomme. Un depot
# jamais envoye part au bout d'un jour.
# La meme limite que le panneau, qui la verifie avant de relayer le fichier.
PIECE_MAX = 10 * 1024 * 1024
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
    racine = nom.rsplit(".", 1)[0] if "." in nom else nom
    racine = racine[:PIECE_NOM_MAX - len(ext) - 1] or "piece"
    return f"{racine}.{ext}"


def _oublier_pieces_orphelines(b):
    """Les pieces d'un message disparu, et les depots jamais envoyes."""
    b.executer("DELETE FROM pieces WHERE message IS NOT NULL "
               "AND message NOT IN (SELECT id FROM messages)")
    b.executer("DELETE FROM pieces WHERE message IS NULL AND ts < ?",
               (int(time.time()) - PIECE_ATTENTE_MAX,))


def deposer_piece(qui, nom_brut, donnees):
    if not donnees:
        raise Refus("Fichier vide.")
    if len(donnees) > PIECE_MAX:
        raise Refus(f"Fichier trop gros : {PIECE_MAX // (1024 * 1024)} Mo au plus.", 413)
    type_, ext = type_de_piece(donnees)
    if not type_:
        raise Refus("Une image (PNG, JPEG, WebP, GIF) ou un PDF seulement.", 415)
    piece = {"id": secrets.token_hex(12), "nom": nom_de_piece(nom_brut, ext),
             "type": type_, "taille": len(donnees)}
    with base() as b:
        _oublier_pieces_orphelines(b)
        if b.valeur("SELECT COUNT(*) FROM pieces WHERE message IS NULL AND qui = ?",
                    (qui,)) >= PIECES_EN_ATTENTE_MAX:
            raise Refus("Trop de pièces en attente : envoie ton message d'abord.", 429)
        b.executer("INSERT INTO pieces (id, message, qui, nom, type, taille, ts, donnees) "
                   "VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
                   (piece["id"], qui, piece["nom"], type_, len(donnees), int(time.time()),
                    donnees))
    return piece


def lire_piece(ident):
    """La piece et ses octets, ou None."""
    with base() as b:
        p = b.un("SELECT * FROM pieces WHERE id = ?", (ident,))
    if p:
        p["donnees"] = bytes(p["donnees"])
    return p


# ---------------------------- projets et taches ----------------------------
#
# Un message accepte devient une tache, rangee dans un projet : un projet
# cree ici (prefixe « p- »), une application declaree, ou « general » pour
# ce qui ne touche aucune d'elles. Une tache a un STATUT (sa colonne sur le
# tableau), une PRIORITE, un ORDRE dans le planning, que le glisser-deposer
# fixe, et des sous-taches qu'on coche une a une.
TACHE_TITRE_MAX = 200
SOUS_TACHES_MAX = 30
TACHES_MAX = 2000
PROJET_GENERAL = "general"
STATUTS_TACHE = ("a_faire", "en_cours", "fait")
PRIORITES = (0, 1, 2, 3)              # urgente, haute, normale, basse
PRIORITE_NORMALE = 2
NOTES_MAX = 4000
DUREE_MAX = 60                        # jours ouvres
# Un projet se CONSTRUIT puis se CONCRETISE : idee -> construction -> realise.
ETAPES_PROJET = ("idee", "construction", "realise")
PROJETS_MAX = 500
PROJET_NOM_MAX = 120
PROJET_DESCRIPTION_MAX = 4000
PREFIXE_PROJET = "p-"


def _titre(brut):
    return " ".join(str(brut or "").split())[:TACHE_TITRE_MAX]


def _texte_long(brut, maximum):
    return str(brut or "").strip()[:maximum]


def _echeance(brut):
    """Une date AAAA-MM-JJ, ou None. Leve si elle est mal formee."""
    if brut in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(brut)).isoformat()
    except ValueError:
        raise Refus("Échéance invalide (AAAA-MM-JJ).") from None


def _duree(brut):
    try:
        d = int(brut)
    except (TypeError, ValueError):
        d = 0
    if not 1 <= d <= DUREE_MAX:
        raise Refus(f"Durée invalide : de 1 à {DUREE_MAX} jours.")
    return d


def _priorite(brut):
    try:
        p = int(brut)
    except (TypeError, ValueError):
        p = None
    if p not in PRIORITES:
        raise Refus("Priorité inconnue.")
    return p


def _projet_valide(b, projet):
    """« general », une application du catalogue, ou un projet existant."""
    projet = str(projet or "").strip()
    if projet == PROJET_GENERAL:
        return projet
    if projet.startswith(PREFIXE_PROJET):
        return projet if b.valeur("SELECT 1 FROM projets WHERE id = ?", (projet,)) == 1 else None
    return projet if _application_connue(b, projet) else None


def _sous_taches_propres(liste):
    propres = [_titre(x) for x in (liste or []) if _titre(x)]
    return propres[:SOUS_TACHES_MAX]


def lire_taches():
    """Les taches et leurs sous-taches, dans l'ordre du planning."""
    with base() as b:
        taches = b.lignes("SELECT * FROM taches ORDER BY ordre, cree, id")
        sous = b.lignes("SELECT * FROM sous_taches ORDER BY ordre, id")
    par_tache = {}
    for st in sous:
        par_tache.setdefault(st["tache"], []).append(
            {"id": st["id"], "titre": st["titre"], "faite": bool(st["faite"])})
    for t in taches:
        t["faite"] = t["statut"] == "fait"
        t["sous_taches"] = par_tache.get(t["id"], [])
    return taches


def _creer_tache(b, projet, titre, sous_taches=(), message=None, priorite=PRIORITE_NORMALE,
                 statut="a_faire", echeance=None, notes="", duree=1):
    """En fin de planning : une tache neuve ne passe pas devant ce qu'on a
    deja ordonne a la main."""
    if b.valeur("SELECT COUNT(*) FROM taches") >= TACHES_MAX:
        raise Refus("Trop de tâches : termine ou supprime les anciennes.")
    ident = secrets.token_hex(8)
    fin = b.valeur("SELECT COALESCE(MAX(ordre), -1) + 1 FROM taches")
    maintenant = int(time.time())
    b.executer("INSERT INTO taches (id, projet, titre, message, cree, ordre, statut, priorite,"
               " echeance, notes, duree, fait_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (ident, projet, titre, message, maintenant, fin, statut, priorite, echeance,
                notes, duree, maintenant if statut == "fait" else None))
    for ordre, st in enumerate(sous_taches):
        b.executer("INSERT INTO sous_taches (id, tache, titre, ordre) VALUES (?, ?, ?, ?)",
                   (secrets.token_hex(8), ident, st, ordre))
    return ident


def creer_tache(d):
    titre = _titre(d.get("titre"))
    if not titre:
        raise Refus("Le titre est obligatoire.")
    statut = d.get("statut") or "a_faire"
    if statut not in STATUTS_TACHE:
        raise Refus("Statut inconnu.")
    priorite = _priorite(d.get("priorite", PRIORITE_NORMALE))
    echeance = _echeance(d.get("echeance"))
    duree = _duree(d.get("duree", 1))
    with base() as b:
        projet = _projet_valide(b, d.get("projet"))
        if not projet:
            raise Refus("Projet inconnu.")
        # Une tache peut naitre d'un message : elle le garde en reference.
        message = d.get("message") or None
        if message and not _message_existe(b, message):
            raise Refus("Message introuvable.", 404)
        return _creer_tache(b, projet, titre, _sous_taches_propres(d.get("sous_taches")),
                            message=message, priorite=priorite, statut=statut,
                            echeance=echeance, notes=_texte_long(d.get("notes"), NOTES_MAX),
                            duree=duree)


def modifier_tache(ident, d):
    """Seuls les champs PRESENTS changent. Changer de statut date sa fin ;
    la tache garde sa place dans le planning."""
    champs, valeurs = [], []
    if "titre" in d:
        titre = _titre(d.get("titre"))
        if not titre:
            raise Refus("Le titre est obligatoire.")
        champs.append("titre = ?"); valeurs.append(titre)
    statut = None
    if "statut" in d:
        statut = d.get("statut")
        if statut not in STATUTS_TACHE:
            raise Refus("Statut inconnu.")
    elif "faite" in d:
        statut = "fait" if d.get("faite") else "a_faire"
    if "priorite" in d:
        champs.append("priorite = ?"); valeurs.append(_priorite(d.get("priorite")))
    if "echeance" in d:
        champs.append("echeance = ?"); valeurs.append(_echeance(d.get("echeance")))
    if "notes" in d:
        champs.append("notes = ?"); valeurs.append(_texte_long(d.get("notes"), NOTES_MAX))
    if "duree" in d:
        champs.append("duree = ?"); valeurs.append(_duree(d.get("duree")))
    with base() as b:
        if "projet" in d:
            projet = _projet_valide(b, d.get("projet"))
            if not projet:
                raise Refus("Projet inconnu.")
            champs.append("projet = ?"); valeurs.append(projet)
        if not champs and statut is None:
            raise Refus("Rien à modifier.")
        avant = b.valeur("SELECT statut FROM taches WHERE id = ?", (ident,))
        if avant is None:
            raise Refus("Tâche introuvable.", 404)
        if statut is not None and statut != avant:
            champs += ["statut = ?", "fait_le = ?"]
            valeurs += [statut, int(time.time()) if statut == "fait" else None]
        if champs:
            b.executer(f"UPDATE taches SET {', '.join(champs)} WHERE id = ?", (*valeurs, ident))


def planifier(ordre):
    """L'ORDRE DU PLANNING, la premiere tache d'abord. C'est ainsi qu'on
    priorise : par la place, pas par une etiquette d'urgence."""
    if (not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre)
            or len(set(ordre)) != len(ordre) or len(ordre) > TACHES_MAX):
        raise Refus("Ordre invalide.")
    with base() as b:
        connues = {l["id"] for l in b.lignes("SELECT id FROM taches")}
        if not set(ordre) <= connues:
            raise Refus("La liste nomme une tâche inconnue.", 409)
        for rang, ident in enumerate(ordre):
            b.executer("UPDATE taches SET ordre = ? WHERE id = ?", (rang, ident))


def scinder_tache(ident, titres):
    """La premiere ligne devient le titre de la tache ; les suivantes sont de
    nouvelles taches du meme projet, nees du meme message, placees juste
    apres elle. La duree se partage entre elles."""
    titres = [_titre(t) for t in (titres or []) if _titre(t)]
    if len(titres) < 2:
        raise Refus("Donne au moins deux titres pour scinder.")
    if len(titres) > SOUS_TACHES_MAX:
        raise Refus(f"{SOUS_TACHES_MAX} morceaux au plus.")
    with base() as b:
        t = b.un("SELECT * FROM taches WHERE id = ?", (ident,))
        if not t:
            raise Refus("Tâche introuvable.", 404)
        if b.valeur("SELECT COUNT(*) FROM taches") + len(titres) - 1 > TACHES_MAX:
            raise Refus("Trop de tâches : termine ou supprime les anciennes.")
        part = max(1, round((t["duree"] or 1) / len(titres)))
        b.executer("UPDATE taches SET ordre = ordre + ? WHERE ordre > ?",
                   (len(titres) - 1, t["ordre"]))
        b.executer("UPDATE taches SET titre = ?, duree = ? WHERE id = ?", (titres[0], part, ident))
        nouvelles = []
        for i, titre in enumerate(titres[1:], start=1):
            nid = secrets.token_hex(8)
            b.executer("INSERT INTO taches (id, projet, titre, message, cree, ordre, statut,"
                       " priorite, notes, duree) VALUES (?, ?, ?, ?, ?, ?, 'a_faire', ?, '', ?)",
                       (nid, t["projet"], titre, t["message"], int(time.time()),
                        t["ordre"] + i, t["priorite"], part))
            nouvelles.append(nid)
    return [ident] + nouvelles


def supprimer_tache(ident):
    with base() as b:
        if not b.executer("DELETE FROM taches WHERE id = ?", (ident,)).rowcount:
            raise Refus("Tâche introuvable.", 404)
        b.executer("DELETE FROM sous_taches WHERE tache = ?", (ident,))


def ajouter_sous_tache(ident, titre):
    titre = _titre(titre)
    if not titre:
        raise Refus("Le titre est obligatoire.")
    with base() as b:
        if b.valeur("SELECT 1 FROM taches WHERE id = ?", (ident,)) != 1:
            raise Refus("Tâche introuvable.", 404)
        nombre = b.valeur("SELECT COUNT(*) FROM sous_taches WHERE tache = ?", (ident,))
        if nombre >= SOUS_TACHES_MAX:
            raise Refus(f"{SOUS_TACHES_MAX} sous-tâches au plus.")
        sid = secrets.token_hex(8)
        b.executer("INSERT INTO sous_taches (id, tache, titre, ordre) VALUES (?, ?, ?, ?)",
                   (sid, ident, titre, nombre))
    return sid


def ordonner_sous_taches(ident, ordre):
    """La liste doit nommer exactement les sous-taches de la tache."""
    if not isinstance(ordre, list) or not all(isinstance(x, str) for x in ordre):
        raise Refus("Ordre invalide.")
    with base() as b:
        if b.valeur("SELECT 1 FROM taches WHERE id = ?", (ident,)) != 1:
            raise Refus("Tâche introuvable.", 404)
        actuelles = {l["id"] for l in b.lignes("SELECT id FROM sous_taches WHERE tache = ?",
                                               (ident,))}
        if set(ordre) != actuelles or len(ordre) != len(actuelles):
            raise Refus("La liste ne correspond pas aux sous-tâches.", 409)
        for rang, sid in enumerate(ordre):
            b.executer("UPDATE sous_taches SET ordre = ? WHERE id = ?", (rang, sid))


def modifier_sous_tache(sid, d):
    champs, valeurs = [], []
    if "titre" in d:
        titre = _titre(d.get("titre"))
        if not titre:
            raise Refus("Le titre est obligatoire.")
        champs.append("titre = ?"); valeurs.append(titre)
    if "faite" in d:
        champs.append("faite = ?"); valeurs.append(1 if d.get("faite") else 0)
    if not champs:
        raise Refus("Rien à modifier.")
    with base() as b:
        if not b.executer(f"UPDATE sous_taches SET {', '.join(champs)} WHERE id = ?",
                          (*valeurs, sid)).rowcount:
            raise Refus("Sous-tâche introuvable.", 404)


def supprimer_sous_tache(sid):
    with base() as b:
        if not b.executer("DELETE FROM sous_taches WHERE id = ?", (sid,)).rowcount:
            raise Refus("Sous-tâche introuvable.", 404)


def lire_projets():
    with base() as b:
        return b.lignes("SELECT * FROM projets ORDER BY ordre, cree, id")


def _creer_projet(b, nom, description="", etape="idee", message=None, taches=()):
    if b.valeur("SELECT COUNT(*) FROM projets") >= PROJETS_MAX:
        raise Refus(f"{PROJETS_MAX} projets au plus : archive ou supprime les anciens.")
    ident = PREFIXE_PROJET + secrets.token_hex(6)
    maintenant = int(time.time())
    fin = b.valeur("SELECT COALESCE(MAX(ordre), -1) + 1 FROM projets")
    b.executer("INSERT INTO projets (id, nom, description, etape, message, cree, maj, ordre)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (ident, nom, description, etape, message, maintenant, maintenant, fin))
    for titre in taches:
        _creer_tache(b, ident, titre, message=message)
    return ident


def creer_projet(d):
    nom = _titre(d.get("nom"))[:PROJET_NOM_MAX]
    if not nom:
        raise Refus("Donne un nom au projet.")
    etape = d.get("etape") or "idee"
    if etape not in ETAPES_PROJET:
        raise Refus("Étape inconnue.")
    with base() as b:
        return _creer_projet(b, nom, _texte_long(d.get("description"), PROJET_DESCRIPTION_MAX),
                             etape=etape, taches=_sous_taches_propres(d.get("taches")))


def modifier_projet(ident, d):
    """Nom, description, etape et l'application qui le concretise."""
    champs, valeurs = [], []
    if "nom" in d:
        nom = _titre(d.get("nom"))[:PROJET_NOM_MAX]
        if not nom:
            raise Refus("Donne un nom au projet.")
        champs.append("nom = ?"); valeurs.append(nom)
    if "description" in d:
        champs.append("description = ?")
        valeurs.append(_texte_long(d.get("description"), PROJET_DESCRIPTION_MAX))
    if "etape" in d:
        if d.get("etape") not in ETAPES_PROJET:
            raise Refus("Étape inconnue.")
        champs.append("etape = ?"); valeurs.append(d.get("etape"))
    with base() as b:
        if "application" in d:
            nom_app = str(d.get("application") or "").strip()
            if nom_app and not _application_connue(b, nom_app):
                raise Refus("Application inconnue.")
            champs.append("application = ?"); valeurs.append(nom_app or None)
        if not champs:
            raise Refus("Rien à modifier.")
        champs.append("maj = ?"); valeurs.append(int(time.time()))
        if not b.executer(f"UPDATE projets SET {', '.join(champs)} WHERE id = ?",
                          (*valeurs, ident)).rowcount:
            raise Refus("Projet introuvable.", 404)


def supprimer_projet(ident):
    """Ses taches passent dans « General », ou l'on decide de leur sort ; ses
    messages redeviennent « a classer »."""
    with base() as b:
        if not b.executer("DELETE FROM projets WHERE id = ?", (ident,)).rowcount:
            raise Refus("Projet introuvable.", 404)
        b.executer("UPDATE taches SET projet = ? WHERE projet = ?", (PROJET_GENERAL, ident))
        b.executer("UPDATE messages SET projet = NULL WHERE projet = ?", (ident,))


def accepter_message(ident, d):
    """Accepte une demande, et en fait un projet, une tache, ou rien.

    - « nouveau_projet » {nom, description} : un PROJET a construire, dont
      les lignes de « taches » sont les premieres taches ;
    - « titre » (+ projet, priorite, sous_taches) : une TACHE ;
    - rien de tout cela : acceptee sans suite.
    """
    tache = projet = None
    nouveau = d.get("nouveau_projet")
    titre = _titre(d.get("titre"))
    with base() as b:
        if not _message_existe(b, ident):
            raise Refus("Message introuvable.", 404)
        if isinstance(nouveau, dict):
            nom = _titre(nouveau.get("nom"))[:PROJET_NOM_MAX]
            if not nom:
                raise Refus("Donne un nom au projet.")
            projet = _creer_projet(b, nom, _texte_long(nouveau.get("description"),
                                                       PROJET_DESCRIPTION_MAX),
                                   message=ident, taches=_sous_taches_propres(d.get("taches")))
        elif titre:
            cible = _projet_valide(b, d.get("projet"))
            if not cible:
                raise Refus("Projet inconnu.")
            tache = _creer_tache(b, cible, titre, _sous_taches_propres(d.get("sous_taches")),
                                 message=ident,
                                 priorite=_priorite(d.get("priorite", PRIORITE_NORMALE)))
        b.executer("UPDATE messages SET statut = 'accepte', lu = 1 WHERE id = ?", (ident,))
    return tache, projet


# ------------------------- le suivi, cote auteur -------------------------
#
# Qui a ecrit veut savoir ce que son message est devenu, sans demander. Le
# suivi se DEDUIT de ce que l'administrateur fait deja ici : lire, mettre a
# l'etude, accepter, refuser, en faire des taches, les cocher.
#
# Un compte ne voit que SES messages, et de leurs taches seulement le titre
# et si elle est faite : ni les notes, ni le reste du planning.

def suivi_message(m, taches, projets):
    """L'etape d'un message, et ce qui en est ne."""
    lies = [t for t in taches if t["message"] == m.get("id")]
    projet = next((p for p in projets
                   if p["message"] == m.get("id") or p["id"] == m.get("projet")), None)
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


def messages_suivis(qui=None):
    """Les messages d'un compte (ou de tous), chacun avec son avancement."""
    taches, projets = lire_taches(), lire_projets()
    return [{"id": m["id"], "ts": m["ts"], "qui": m["qui"], "cible": m["cible"],
             "app": m["app"], "texte": m["texte"], "pieces": m["pieces"],
             "suivi": suivi_message(m, taches, projets)}
            for m in lire_messages(qui=qui)]


# ----------------------------- l'oubli d'un compte -----------------------------
#
# Un compte efface a sa demande emporte ses messages et leurs pieces. Les
# taches et projets nes d'un de ses messages restent : c'est le travail de
# l'administrateur, et il ne porte pas le nom de l'auteur.

def oublier_comptes(noms):
    noms = [n for n in noms or [] if isinstance(n, str) and n]
    if not noms:
        return 0
    marques = ",".join("?" * len(noms))
    with base() as b:
        b.executer(f"DELETE FROM pieces WHERE qui IN ({marques})", noms)
        n = b.executer(f"DELETE FROM messages WHERE qui IN ({marques})", noms).rowcount
        _oublier_pieces_orphelines(b)
    return n


# ---------------------- la reprise des donnees du panneau ----------------------
#
# Vecu : « l'application Messages doit etre une application a part entiere,
# exactement comme Diagnostic ». Ses donnees vivaient dans la base du panneau
# (codelab.db) ; le panneau les envoie ici une fois, puis les efface chez lui
# APRES avoir verifie que chaque ligne est bien arrivee (voir
# migrer_la_messagerie, cote panneau). Rejouer l'envoi ne double rien : une
# ligne deja la est laissee telle quelle.
_COLONNES = {
    "messages": ("id", "ts", "qui", "cible", "app", "texte", "statut", "lu", "retenu", "archive",
                 "projet", "ordre"),
    "projets": ("id", "nom", "description", "etape", "application", "message", "cree", "maj",
                "ordre"),
    "taches": ("id", "projet", "titre", "message", "cree", "statut", "priorite", "echeance",
               "notes", "ordre", "duree", "fait_le"),
    "sous_taches": ("id", "tache", "titre", "faite", "ordre"),
}
_DEFAUTS = {"qui": "", "app": "", "texte": "", "statut": "en_attente", "lu": 0, "retenu": 0,
            "archive": 0, "description": "", "etape": "idee", "notes": "", "ordre": 0,
            "priorite": 2, "duree": 1, "faite": 0}


def importer(donnees):
    """Insere ce qui manque ; rend, par table, les identifiants presents."""
    with base() as b:
        for table, colonnes in _COLONNES.items():
            for ligne in donnees.get(table) or []:
                if not isinstance(ligne, dict) or not isinstance(ligne.get("id"), str):
                    continue
                valeurs = []
                for c in colonnes:
                    v = ligne.get(c)
                    if v is None and c in _DEFAUTS and not (table == "messages" and c == "ordre"):
                        v = _DEFAUTS[c]
                    if isinstance(v, bool):
                        v = int(v)
                    valeurs.append(v)
                b.executer(f"INSERT INTO {table} ({', '.join(colonnes)}) VALUES "
                           f"({', '.join('?' * len(colonnes))}) ON CONFLICT (id) DO NOTHING",
                           valeurs)
        return {table: [l["id"] for l in b.lignes(f"SELECT id FROM {table}")]
                for table in (*_COLONNES, "pieces")}


def importer_piece(meta, donnees):
    with base() as b:
        b.executer("INSERT INTO pieces (id, message, qui, nom, type, taille, ts, donnees) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                   (meta["id"], meta.get("message"), meta.get("qui") or "", meta["nom"],
                    meta["type"], len(donnees), int(meta.get("ts") or time.time()), donnees))


# ----------------------------- exporter un projet -----------------------------
#
# TOUT LE PROJET, D'UN COUP : sa description, ses taches dans l'ordre du
# planning, les messages qui l'ont fait naitre et leurs pieces jointes. Un
# ZIP : un « projet.md » a lire (ou a donner tel quel a qui va le
# construire), un « projet.json » pour un programme, les pieces dans
# « pieces/ ».
ETAPES_NOMS = {"idee": "Idée", "construction": "En construction", "realise": "Réalisé"}
STATUTS_NOMS = {"a_faire": "À faire", "en_cours": "En cours", "fait": "Fait"}
PRIORITES_NOMS = {0: "urgente", 1: "haute", 2: "normale", 3: "basse"}
SUJETS_NOMS = {"hub": "Améliorer le hub", "idee": "Nouveau projet", "application": "Application"}


def _slug(texte):
    t = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:60] or "projet"


def _date(ts):
    return datetime.datetime.fromtimestamp(ts or 0).strftime("%d/%m/%Y")


def _contenu_projet(cle):
    taches, projets = lire_taches(), lire_projets()
    projet = next((p for p in projets if p["id"] == cle), None)
    if not projet and cle != PROJET_GENERAL and not any(t["projet"] == cle for t in taches):
        raise Refus("Projet introuvable.", 404)
    if projet:
        nom = projet["nom"]
    elif cle == PROJET_GENERAL:
        nom = "Général"
    else:
        nom = next((a["titre"] for a in lire_applications() if a["name"] == cle), cle)
    siennes = [t for t in taches if t["projet"] == cle]
    lies = {t["message"] for t in siennes if t["message"]}
    if projet and projet["message"]:
        lies.add(projet["message"])
    msgs = sorted((m for m in lire_messages(limite=MESSAGES_MAX)
                   if m.get("projet") == cle or m["id"] in lies), key=lambda m: m["ts"])
    return {"cle": cle, "nom": nom, "projet": projet, "taches": siennes, "messages": msgs}


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

    for titre, groupe in (("## Tâches, dans l'ordre du planning", ouvertes),
                          ("## Tâches faites", faites)):
        if groupe:
            lignes += [titre, ""]
            for t in groupe:
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


def exporter_projet(cle):
    """(octets du ZIP, nom du fichier)."""
    c = _contenu_projet(cle)
    tampon = io.BytesIO()
    chemins, pris = {}, set()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for m in c["messages"]:
            for piece in m.get("pieces") or []:
                p = lire_piece(piece["id"])
                if not p:
                    continue
                racine = datetime.datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d")
                nom, k = racine + "-" + piece["nom"], 2
                while nom in pris:
                    debut, _, ext = (racine + "-" + piece["nom"]).rpartition(".")
                    nom, k = f"{debut}-{k}.{ext}", k + 1
                pris.add(nom)
                chemins[piece["id"]] = "pieces/" + nom
                z.writestr("pieces/" + nom, p["donnees"])
        z.writestr("projet.md", _markdown_projet(c, chemins))
        donnees = {"nom": c["nom"], "cle": c["cle"], "projet": c["projet"],
                   "taches": c["taches"],
                   "messages": [dict(m, pieces=[dict(p, fichier=chemins.get(p["id"]))
                                                for p in m.get("pieces") or []])
                                for m in c["messages"]]}
        z.writestr("projet.json", json.dumps(donnees, ensure_ascii=False, indent=2))
    return tampon.getvalue(), f"{_slug(c['nom'])}-{time.strftime('%Y-%m-%d')}.zip"

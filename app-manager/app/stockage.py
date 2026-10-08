"""Le stockage du panneau : une seule base SQLite, codelab.db.

POURQUOI SQLITE, ET PAS DES FICHIERS JSON NI POSTGRES.

Les donnees du panneau vivaient dans une quinzaine de fichiers aux formats
differents (apps.json, utilisateurs.json, passkeys.json, acces.jsonl...). Il
fallait lire le code pour savoir ou etait quoi, et rien ne reliait un compte
a ses droits ou a ses cles. Ici, chaque chose a sa table, ses colonnes typees
et ses index ; la base s'ouvre avec n'importe quel outil SQLite (DB Browser,
TablePlus, la commande sqlite3) et se lit comme un tableur.

Postgres aurait fait la meme chose, mais il est PARTAGE avec les
applications : si le panneau en dependait pour lire ses comptes, une base
saturee ou cassee empecherait de se connecter au panneau -- l'outil qui sert
justement a la reparer. SQLite est un fichier local, sans serveur, inclus dans
Python : le panneau demarre quoi qu'il arrive au reste de la stack. Postgres
garde son role : une copie pour l'historique long et les requetes (voir
_pg_deposer dans app.py).

GARANTIES.
  - Chaque ecriture est une transaction : complete ou absente, meme si le
    panneau est tue au milieu. Les fichiers JSON s'en approchaient par un
    renommage atomique, mais deux fichiers ecrits l'un apres l'autre (un
    compte et ses cles) pouvaient diverger.
  - Mode WAL : les lectures (le proxy lit les applications a chaque requete)
    ne sont jamais bloquees par une ecriture.
  - Le fichier est en 0600 : il contient les empreintes de mots de passe.
  - Le schema est VERSIONNE (PRAGMA user_version) : une mise a jour du
    panneau qui change une table le fait par une migration numerotee, jouee
    une fois, ici.

Les donnees sensibles (adresses mail, serveur d'envoi, invitations) restent
CHIFFREES, valeur par valeur, dans la table reglages_chiffres : qui copie la
base sans credentials.env n'en lit aucune.
"""
import contextlib
import json
import os
import sqlite3
import threading

# Ce qui change le schema s'ajoute ICI, a la suite, et jamais ailleurs : la
# version de la base dit combien d'etapes ont deja ete jouees.
MIGRATIONS = [
    # --- version 1 : le schema de depart ---
    """
    CREATE TABLE meta (
        cle     TEXT PRIMARY KEY,
        valeur  TEXT NOT NULL
    );

    -- Les applications declarees.
    CREATE TABLE applications (
        nom             TEXT PRIMARY KEY,
        titre           TEXT,
        description     TEXT,
        dossier         TEXT,
        commande        TEXT,
        commande_build  TEXT,
        port            INTEGER,
        actif           INTEGER,
        visibilite      TEXT,
        categorie       TEXT,
        memoire_max_mo  INTEGER,
        options         TEXT NOT NULL DEFAULT '{}'
    );

    -- Toute modification des applications fait avancer leur compteur, y
    -- compris faite hors du panneau (sqlite3, DB Browser) : le cache du
    -- panneau s'en apercoit a la requete suivante.
    CREATE TRIGGER applications_ins AFTER INSERT ON applications BEGIN
        INSERT INTO meta (cle, valeur) VALUES ('gen:applications', '1')
        ON CONFLICT(cle) DO UPDATE SET valeur = CAST(valeur AS INTEGER) + 1;
    END;
    CREATE TRIGGER applications_maj AFTER UPDATE ON applications BEGIN
        INSERT INTO meta (cle, valeur) VALUES ('gen:applications', '1')
        ON CONFLICT(cle) DO UPDATE SET valeur = CAST(valeur AS INTEGER) + 1;
    END;
    CREATE TRIGGER applications_sup AFTER DELETE ON applications BEGIN
        INSERT INTO meta (cle, valeur) VALUES ('gen:applications', '1')
        ON CONFLICT(cle) DO UPDATE SET valeur = CAST(valeur AS INTEGER) + 1;
    END;

    -- Les comptes utilisateurs. L'administrateur n'y est pas : son mot de
    -- passe vit dans credentials.env. L'adresse mail non plus : elle est
    -- chiffree, dans reglages_chiffres.
    CREATE TABLE comptes (
        nom                 TEXT PRIMARY KEY,
        sel                 TEXT,
        empreinte_mdp       TEXT,
        email_verifie       INTEGER,
        attente_email       INTEGER,
        suspendu            INTEGER,
        totp                TEXT,
        cree                INTEGER,
        sessions_depuis     INTEGER,
        code_mail_suspendu  INTEGER,
        options             TEXT NOT NULL DEFAULT '{}'
    );

    -- Qui peut ouvrir quelle application privee.
    CREATE TABLE acces_applications (
        compte       TEXT NOT NULL REFERENCES comptes(nom) ON DELETE CASCADE,
        application  TEXT NOT NULL,
        PRIMARY KEY (compte, application)
    );

    -- Les cles d'acces (Face ID, Touch ID, empreinte, Windows Hello).
    -- compte vaut "admin" pour l'administrateur.
    CREATE TABLE cles_acces (
        id            TEXT PRIMARY KEY,
        compte        TEXT NOT NULL,
        cle_publique  TEXT NOT NULL,
        compteur      INTEGER NOT NULL DEFAULT 0,
        nom           TEXT,
        cree          INTEGER,
        dernier       INTEGER
    );
    CREATE INDEX cles_acces_compte ON cles_acces(compte);

    -- Les categories du hub, dans l'ordre voulu.
    CREATE TABLE categories (
        nom       TEXT PRIMARY KEY,
        position  INTEGER NOT NULL
    );

    -- Ce que chaque compte a range hors de son hub.
    CREATE TABLE masquees (
        compte       TEXT NOT NULL,
        application  TEXT NOT NULL,
        PRIMARY KEY (compte, application)
    );

    -- Le journal : connexions, echecs, ouvertures d'applications...
    CREATE TABLE journal (
        id       TEXT PRIMARY KEY,
        ts       INTEGER NOT NULL,
        genre    TEXT NOT NULL,
        qui      TEXT,
        app      TEXT,
        ip       TEXT,
        details  TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX journal_ts ON journal(ts);
    CREATE INDEX journal_qui ON journal(qui, ts);
    CREATE INDEX journal_app ON journal(app, ts);

    -- Les messages laisses a l'administrateur.
    CREATE TABLE messages (
        id       TEXT PRIMARY KEY,
        ts       INTEGER NOT NULL,
        qui      TEXT,
        cible    TEXT,
        app      TEXT,
        texte    TEXT,
        statut   TEXT,
        lu       INTEGER,
        retenu   INTEGER,
        archive  INTEGER,
        options  TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX messages_ts ON messages(ts);

    -- Les processus lances par le panneau, pour les retrouver apres un
    -- redemarrage.
    CREATE TABLE processus (
        nom     TEXT PRIMARY KEY,
        pid     INTEGER NOT NULL,
        depuis  INTEGER
    );

    -- Les reglages en clair (exposition, VPS...), une ligne par sujet.
    CREATE TABLE parametres (
        cle     TEXT PRIMARY KEY,
        valeur  TEXT NOT NULL
    );

    -- Les reglages sensibles, chiffres valeur par valeur.
    CREATE TABLE reglages_chiffres (
        cle     TEXT PRIMARY KEY,
        valeur  BLOB NOT NULL
    );
    """,
    # --- version 2 : les taches nees des messages ---
    # Un message accepte peut devenir une tache, rangee dans un projet (une
    # application, ou « general »), avec ses sous-taches. La tache garde le
    # lien vers le message qui l'a fait naitre ; supprimer le message ne
    # supprime pas le travail qu'il a declenche.
    """
    CREATE TABLE taches (
        id       TEXT PRIMARY KEY,
        projet   TEXT NOT NULL,
        titre    TEXT NOT NULL,
        message  TEXT,
        cree     INTEGER NOT NULL,
        faite    INTEGER NOT NULL DEFAULT 0,
        ordre    INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX taches_projet ON taches(projet, faite);

    CREATE TABLE sous_taches (
        id     TEXT PRIMARY KEY,
        tache  TEXT NOT NULL REFERENCES taches(id) ON DELETE CASCADE,
        titre  TEXT NOT NULL,
        faite  INTEGER NOT NULL DEFAULT 0,
        ordre  INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX sous_taches_tache ON sous_taches(tache, ordre);
    """,
    # --- version 3 : l'application « Demandes », un gestionnaire de taches ---
    # Des PROJETS qu'on construit puis qu'on concretise (idee -> en
    # construction -> realise), et des taches qu'on priorise et qu'on deplace
    # sur un tableau (a faire -> en cours -> fait). Une tache peut toujours
    # appartenir a une application ou a « general » : la colonne projet des
    # taches porte un nom d'application, « general », ou l'id d'un projet
    # (prefixe « p- », qui ne peut pas etre un nom d'application).
    """
    CREATE TABLE projets (
        id           TEXT PRIMARY KEY,
        nom          TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        etape        TEXT NOT NULL DEFAULT 'idee',
        application  TEXT,
        message      TEXT,
        cree         INTEGER NOT NULL,
        maj          INTEGER NOT NULL,
        ordre        INTEGER NOT NULL DEFAULT 0
    );
    ALTER TABLE taches ADD COLUMN statut TEXT NOT NULL DEFAULT 'a_faire';
    ALTER TABLE taches ADD COLUMN priorite INTEGER NOT NULL DEFAULT 2;
    ALTER TABLE taches ADD COLUMN echeance TEXT;
    ALTER TABLE taches ADD COLUMN notes TEXT NOT NULL DEFAULT '';
    UPDATE taches SET statut = 'fait' WHERE faite = 1;
    CREATE INDEX taches_statut ON taches(statut, ordre);
    """,
]

VERSION = len(MIGRATIONS)

_local = threading.local()
_verrou_schema = threading.Lock()


def _instructions(script):
    """Decoupe un script en instructions completes (un trigger contient des
    « ; » a l'interieur de son BEGIN ... END)."""
    morceau = ""
    for ligne in script.splitlines(keepends=True):
        if ligne.strip().startswith("--"):
            continue
        morceau += ligne
        if sqlite3.complete_statement(morceau):
            if morceau.strip():
                yield morceau.strip()
            morceau = ""
    if morceau.strip():
        yield morceau.strip()


def _preparer(chemin):
    dossier = os.path.dirname(chemin)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    cx = sqlite3.connect(chemin, timeout=15, isolation_level=None,
                         check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.execute("PRAGMA foreign_keys=ON")
    # Une ligne supprimee est EFFACEE du fichier, pas seulement oubliee :
    # sans cela, une adresse retiree (ou un code consomme) resterait lisible
    # dans les pages libres de la base jusqu'a leur reutilisation.
    cx.execute("PRAGMA secure_delete=ON")
    cx.execute("PRAGMA busy_timeout=15000")
    with _verrou_schema:
        version = cx.execute("PRAGMA user_version").fetchone()[0]
        if version < VERSION:
            cx.execute("BEGIN IMMEDIATE")
            try:
                version = cx.execute("PRAGMA user_version").fetchone()[0]
                for i in range(version, VERSION):
                    for instruction in _instructions(MIGRATIONS[i]):
                        cx.execute(instruction)
                cx.execute(f"PRAGMA user_version = {VERSION}")
                cx.execute("COMMIT")
            except BaseException:
                cx.execute("ROLLBACK")
                raise
    for suffixe in ("", "-wal", "-shm"):
        try:
            os.chmod(chemin + suffixe, 0o600)
        except OSError:
            pass
    return cx


def connexion(chemin):
    """La connexion de CE thread a cette base, ouverte a la demande.

    Une connexion par thread : sqlite3 le recommande, et le panneau a
    plusieurs threads (requetes, moniteur, sauvegardes). Si le chemin change
    (les tests le font a chaque test), l'ancienne est fermee.
    """
    cx = getattr(_local, "cx", None)
    if cx is not None and getattr(_local, "chemin", None) == chemin:
        return cx
    if cx is not None:
        try:
            cx.close()
        except sqlite3.Error:
            pass
    cx = _preparer(chemin)
    _local.cx, _local.chemin = cx, chemin
    return cx


@contextlib.contextmanager
def transaction(chemin):
    """Tout ce qui est ecrit dans le bloc l'est d'un coup, ou pas du tout."""
    cx = connexion(chemin)
    cx.execute("BEGIN IMMEDIATE")
    try:
        yield cx
    except BaseException:
        cx.execute("ROLLBACK")
        raise
    cx.execute("COMMIT")


def lire(chemin, requete, parametres=()):
    return connexion(chemin).execute(requete, parametres).fetchall()


# ------------------- d'un dictionnaire a une ligne, et retour -------------------
#
# Le code du panneau manipule des dictionnaires (une application, un compte).
# Chaque table declare quelles cles ont leur propre colonne ; ce qui reste va
# dans la colonne « options », en JSON, pour qu'aucun champ ne se perde. Une
# colonne NULL veut dire « cle absente » : la relecture rend le dictionnaire
# tel qu'il a ete ecrit.

def vers_ligne(d, champs):
    ligne = {}
    for cle, colonne, genre in champs:
        if cle not in d or d[cle] is None:
            ligne[colonne] = None
        elif genre == "bool":
            ligne[colonne] = 1 if d[cle] else 0
        elif genre == "int":
            try:
                ligne[colonne] = int(d[cle])
            except (TypeError, ValueError):
                ligne[colonne] = None
        else:
            ligne[colonne] = d[cle] if isinstance(d[cle], str) else str(d[cle])
    connues = {cle for cle, _, _ in champs}
    reste = {k: v for k, v in d.items() if k not in connues}
    return ligne, json.dumps(reste, ensure_ascii=False)


def depuis_ligne(ligne, champs, options="{}"):
    d = {}
    for cle, colonne, genre in champs:
        v = ligne[colonne]
        if v is None:
            continue
        d[cle] = bool(v) if genre == "bool" else v
    try:
        reste = json.loads(options or "{}")
    except ValueError:
        reste = {}
    if isinstance(reste, dict):
        for k, v in reste.items():
            d.setdefault(k, v)
    return d


def inserer(cx, table, ligne, options=None):
    colonnes = list(ligne)
    valeurs = [ligne[c] for c in colonnes]
    if options is not None:
        colonnes.append("options")
        valeurs.append(options)
    cx.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(colonnes)}) "
               f"VALUES ({', '.join('?' * len(colonnes))})", valeurs)


def generation(chemin, sujet):
    """Un compteur qui avance a chaque ecriture d'un sujet : il sert de cle
    de cache a qui relit souvent (le proxy relit les applications a chaque
    requete)."""
    ligne = lire(chemin, "SELECT valeur FROM meta WHERE cle = ?", (f"gen:{sujet}",))
    return ligne[0][0] if ligne else "0"


def avancer_generation(cx, sujet):
    cx.execute("INSERT INTO meta (cle, valeur) VALUES (?, '1') "
               "ON CONFLICT(cle) DO UPDATE SET valeur = CAST(valeur AS INTEGER) + 1",
               (f"gen:{sujet}",))


def vider_le_journal(chemin):
    """Reverse le journal d'ecriture (-wal) dans la base et le remet a zero :
    les anciennes versions des pages qu'il gardait disparaissent avec lui."""
    try:
        connexion(chemin).execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def sauvegarder_vers(chemin, destination):
    """Copie coherente de la base, meme pendant des ecritures (API backup de
    SQLite). Copier le fichier a la main pourrait saisir une ecriture a
    moitie, et oublierait ce qui est encore dans le -wal."""
    cible = sqlite3.connect(destination)
    try:
        connexion(chemin).backup(cible)
    finally:
        cible.close()
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass

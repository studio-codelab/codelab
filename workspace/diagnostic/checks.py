"""
Sondes de diagnostic CodeLab -- partagees entre l'application web et l'asset
Dagster, pour que les deux racontent exactement la meme chose.

Uniquement la bibliotheque standard ici. Le pilote Postgres est resolu par
connect_pg(), qui accepte psycopg (v3, installe par la commande de build de
l'application) ou psycopg2 (deja present dans l'image Dagster via
dagster-postgres).
"""
import os
import socket
import stat
import urllib.error
import urllib.request

ENV_FILE = os.environ.get("CODELAB_ENV_FILE", "/var/lib/codelab/config/credentials.env")
WORKSPACE = os.environ.get("APP_MANAGER_ROOT", "/workspace")
# Dans app-manager comme dans dagster, le volume config est monte au meme
# endroit : les cles SSH sont donc visibles a cote de credentials.env.
SSH_DIR = os.environ.get("CODELAB_SSH_DIR") or os.path.join(os.path.dirname(ENV_FILE), "ssh")
# uid de l'utilisateur SSH du conteneur dev, tel que vu depuis les autres
# conteneurs (le volume est partage, les uid sont les memes).
SSH_UID = int(os.environ.get("CODELAB_SSH_UID", "1000"))
TABLE = "codelab_diagnostic"


# ------------------------------ credentials.env ------------------------------

def read_env(key, env_file=None):
    """Lit une cle dans credentials.env. Derniere occurrence : chaque bloc est
    reecrit en fin de fichier, donc une valeur laissee plus haut est perimee."""
    valeur = None
    try:
        with open(env_file or ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    valeur = line[len(key) + 1:].strip() or None
    except OSError:
        return None
    return valeur


def pg_settings(env_file=None):
    return {
        "host": read_env("POSTGRES_HOST", env_file) or "codelab-postgres",
        "port": int(read_env("POSTGRES_PORT", env_file) or 5432),
        "dbname": read_env("POSTGRES_DB", env_file) or "codelab",
        "user": read_env("POSTGRES_USER", env_file) or "codelab",
        "password": read_env("POSTGRES_PASSWORD", env_file),
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


def connect_pg(env_file=None):
    mod, _ = pilote_pg()
    return mod.connect(**pg_settings(env_file))


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS " + TABLE + " ("
            "  id     BIGSERIAL PRIMARY KEY,"
            "  source TEXT        NOT NULL,"
            "  detail TEXT,"
            "  vu_le  TIMESTAMPTZ NOT NULL DEFAULT now())")
    conn.commit()


def write_heartbeat(conn, source, detail=""):
    ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO " + TABLE + " (source, detail) VALUES (%s, %s) RETURNING id",
                    (source, detail))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def read_heartbeats(conn, limit=10):
    ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT source, count(*), max(vu_le) FROM " + TABLE
                    + " GROUP BY source ORDER BY source")
        par_source = cur.fetchall()
        cur.execute("SELECT id, source, detail, vu_le FROM " + TABLE
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


# ---------------------------------- sondes ----------------------------------
# Chacune renvoie (ok, titre, detail). Aucune ne leve : une sonde qui echoue
# doit afficher pourquoi, pas faire tomber la page.

def check_config(env_file=None):
    """Volume config monte + secret partage lisible."""
    path = env_file or ENV_FILE
    if not os.path.exists(path):
        return False, "credentials.env", f"introuvable : {path} (volume config non monte ?)"
    pw = read_env("POSTGRES_PASSWORD", env_file)
    if not pw:
        return False, "credentials.env", "lisible, mais POSTGRES_PASSWORD absent"
    return True, "credentials.env", f"{path} -- POSTGRES_PASSWORD lu ({len(pw)} caracteres)"


def check_workspace(workspace=None):
    """Volume /workspace partage entre dev, dagster et app-manager."""
    root = workspace or WORKSPACE
    if not os.path.isdir(root):
        return False, "/workspace", f"{root} n'est pas un dossier (volume non monte ?)"
    defs = os.path.join(root, "definitions.py")
    if not os.path.exists(defs):
        return False, "/workspace", f"{root} monte, mais definitions.py absent -- Dagster n'a rien a charger"
    n = len([x for x in os.listdir(root) if not x.startswith(".")])
    return True, "/workspace", f"{root} -- definitions.py present, {n} entrees visibles"


def check_pilote_pg():
    try:
        _, nom = pilote_pg()
        return True, "Postgres (pilote)", f"{nom} disponible"
    except PiloteAbsent as e:
        return False, "Postgres (pilote)", str(e)


def check_postgres(env_file=None):
    """Reseau + mot de passe partage + base accessible."""
    cfg = pg_settings(env_file)
    try:
        pilote_pg()
    except PiloteAbsent:
        return False, "Postgres", "pilote absent -- voir la sonde precedente"
    try:
        conn = connect_pg(env_file)
    except Exception as e:
        return False, "Postgres", f"{cfg['host']}:{cfg['port']} -- {type(e).__name__}: {e}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            v = cur.fetchone()[0].split(" on ")[0]
        return True, "Postgres", f"{cfg['host']}:{cfg['port']}/{cfg['dbname']} -- {v}"
    except Exception as e:
        return False, "Postgres", f"connecte mais requete refusee -- {e}"
    finally:
        conn.close()


def check_http(nom, url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, nom, f"{url} -- HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, nom, f"{url} -- HTTP {e.code} (service joignable)"
    except Exception as e:
        return False, nom, f"{url} -- {type(e).__name__}: {e}"


def check_tcp(nom, host, port, timeout=4, lire_banniere=False):
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            if lire_banniere:
                s.settimeout(timeout)
                b = s.recv(128).decode("utf-8", "replace").strip()
                return True, nom, f"{host}:{port} -- {b or 'connexion acceptee'}"
            return True, nom, f"{host}:{port} -- connexion acceptee"
    except socket.gaierror as e:
        # Le DNS de Docker ne connait que les conteneurs DEMARRES du reseau :
        # un nom qui ne resout pas designe presque toujours un conteneur arrete.
        return False, nom, (f"{host}:{port} -- nom introuvable ({e}). Conteneur arrete, "
                            f"ou hors du reseau codelab : docker ps -a --filter name={host}")
    except Exception as e:
        return False, nom, f"{host}:{port} -- {type(e).__name__}: {e}"


def check_cles_ssh(ssh_dir=None, uid=None):
    """Droits sur authorized_keys, du point de vue de l'utilisateur SSH.

    sshd lit les cles hote en root, mais ouvre authorized_keys APRES avoir
    pris l'uid de l'utilisateur cible. Un dossier non traversable ou un
    fichier non lisible par lui donne un "Permission denied (publickey)"
    cote client, strictement identique a celui d'une cle absente. C'est
    exactement le genre de panne qu'aucune sonde reseau ne verra."""
    d = ssh_dir or SSH_DIR
    u = SSH_UID if uid is None else uid
    ak = os.path.join(d, "authorized_keys")
    hk = os.path.join(d, "host_keys")

    if not os.path.isdir(d):
        return False, "Cles SSH (droits)", f"{d} absent -- codelab-dev n'a jamais demarre ?"
    if not _accessible_par(u, d, stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH):
        return False, "Cles SSH (droits)", (
            f"{d} est en {_mode(d)} : l'uid {u} ne peut pas le traverser, donc sshd "
            f"n'atteindra jamais authorized_keys. Corriger : chmod 755 {d}")
    if not os.path.exists(ak):
        return False, "Cles SSH (droits)", f"{ak} absent -- aucune cle autorisee"
    if not _accessible_par(u, ak, stat.S_IRUSR, stat.S_IRGRP, stat.S_IROTH):
        st = os.stat(ak)
        return False, "Cles SSH (droits)", (
            f"{ak} est en {_mode(ak)} et appartient a {st.st_uid}:{st.st_gid} : "
            f"illisible par l'uid {u}. Corriger : chown {u}:{u} {ak}")

    try:
        with open(ak) as f:
            cles = [l for l in f.read().splitlines()
                    if l.strip() and not l.strip().startswith("#")]
    except OSError as e:
        return False, "Cles SSH (droits)", f"{ak} -- {e}"
    if not cles:
        return False, "Cles SSH (droits)", f"{ak} est vide -- aucune connexion SSH ne passera"

    n_hotes = len([x for x in os.listdir(hk)
                   if x.endswith("_key")]) if os.path.isdir(hk) else 0
    return True, "Cles SSH (droits)", (
        f"{len(cles)} cle(s) autorisee(s), lisible(s) par l'uid {u} ; "
        f"{n_hotes} cle(s) hote persistee(s)")


def run_all(env_file=None, workspace=None, ssh_dir=None):
    """Les huit sondes, dans l'ordre ou on veut les lire."""
    host = read_env("POSTGRES_HOST", env_file) or "codelab-postgres"
    port = int(read_env("POSTGRES_PORT", env_file) or 5432)
    return [
        check_config(env_file),
        check_workspace(workspace),
        check_pilote_pg(),
        check_postgres(env_file),
        check_tcp("codelab-postgres (TCP)", host, port),
        check_http("codelab-dagster", "http://codelab-dagster:3000/"),
        check_tcp("codelab-dev (SSH)", "codelab-dev", 22, lire_banniere=True),
        check_cles_ssh(ssh_dir),
    ]

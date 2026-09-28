#!/bin/sh
# Entrypoint de codelab-redis.
#
# Genere (ou reprend) le mot de passe Redis, l'ecrit dans credentials.env --
# d'ou le panneau le transmet aux applications qu'il lance, sous REDIS_URL --
# puis ecrit la configuration du serveur et rend la main a l'entrypoint
# officiel, qui bascule sur l'utilisateur redis.
#
# Le mot de passe ne passe jamais sur la ligne de commande de redis-server :
# il serait lisible par ps. Il vit dans un fichier de configuration 0600,
# regenere a chaque demarrage, hors du volume de donnees.
set -e

CONFIG_DIR="${CODELAB_CONFIG_DIR:-/var/lib/codelab/config}"
ENV_FILE="$CONFIG_DIR/credentials.env"
CONF="${CODELAB_REDIS_CONF:-/etc/codelab-redis/redis.conf}"
REDIS_HOST_PUBLIC="${CODELAB_REDIS_HOST:-codelab-redis}"
REDIS_PORT="${CODELAB_REDIS_PORT:-6379}"
# Plafond de la memoire des DONNEES, sous celui du conteneur (320 Mo dans le
# compose) : Redis a besoin de marge au-dela de ses cles, notamment pendant la
# reecriture du journal, qui duplique le processus.
REDIS_MAXMEMORY="${CODELAB_REDIS_MAXMEMORY:-192mb}"

mkdir -p "$CONFIG_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

get_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# Meme format de bloc que codelab-postgres (voir postgres/entrypoint.sh).
upsert_block() {
  name="$1"; content="$2"
  awk -v s="# ===== $name =====" -v e="# ===== /$name =====" \
    '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.redis.tmp"
  {
    cat "$ENV_FILE.redis.tmp"
    echo "# ===== $name ====="
    printf '%s\n' "$content" | sed 's/^[[:space:]]*//'
    echo "# ===== /$name ====="
  } > "$ENV_FILE.redis.new"
  mv "$ENV_FILE.redis.new" "$ENV_FILE"
  rm -f "$ENV_FILE.redis.tmp"
}

# Le verrou partage avec les autres services qui ecrivent credentials.env
# (codelab-postgres, le panneau, le proxy Dagster). Sans lui, deux services
# qui demarrent ensemble lisent le meme fichier, et le dernier a ecrire
# efface le bloc que l'autre venait d'ajouter -- celui de Postgres compris,
# dont le mot de passe ne se regenere pas sans casser la base.
exec 9>"$ENV_FILE.lock"
chmod 600 "$ENV_FILE.lock" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then flock 9; fi

# La valeur deja presente fait autorite. La regenerer ne perdrait aucune
# donnee -- Redis ne chiffre rien avec -- mais deconnecterait toutes les
# applications jusqu'a leur redemarrage : on ne le fait que si elle manque.
REDIS_PASSWORD="$(get_value REDIS_PASSWORD)"
case "$REDIS_PASSWORD" in
  *[!0-9a-f]*)
    # Seul un mot de passe hexadecimal est sur dans une URL et dans la
    # ligne ACL ci-dessous. Une valeur retouchee a la main est remplacee.
    echo "[codelab-redis] REDIS_PASSWORD illisible : regenere." >&2
    REDIS_PASSWORD="" ;;
esac
if [ -z "$REDIS_PASSWORD" ]; then
  REDIS_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "[codelab-redis] mot de passe genere."
fi

upsert_block "codelab-redis" "# Serveur Redis partage : cache, sessions, files de taches, compteurs.
# Transmis aux applications lancees par le panneau. REDIS_URL suffit a la
# plupart des bibliotheques (redis-py, node-redis, ioredis, RQ, Celery).
#
# Un seul mot de passe pour toutes les applications : prefixer ses cles par
# le nom de l'application (\"monapp:session:42\") pour ne pas marcher sur
# celles des voisines.
REDIS_HOST=$REDIS_HOST_PUBLIC
REDIS_PORT=$REDIS_PORT
REDIS_PASSWORD=$REDIS_PASSWORD
REDIS_URL=redis://default:$REDIS_PASSWORD@$REDIS_HOST_PUBLIC:$REDIS_PORT/0"

chmod 600 "$ENV_FILE"
if command -v flock >/dev/null 2>&1; then flock -u 9; fi
exec 9>&-

# ------------------------------ configuration ------------------------------
mkdir -p "$(dirname "$CONF")"
umask 077
cat > "$CONF" <<CONF
# Genere par codelab-redis a chaque demarrage -- ne pas editer.

# Joignable sur le reseau Docker de la stack, et seulement la : le compose ne
# publie aucun port sur l'hote.
bind 0.0.0.0
port $REDIS_PORT
dir ${CODELAB_REDIS_DIR:-/data}

# Le mot de passe, et ce qu'il n'autorise PAS : les commandes
# d'administration (CONFIG, MONITOR, SHUTDOWN, REPLICAOF, MODULE, DEBUG...).
# CONFIG SET dir + dbfilename est le chemin classique par lequel un client
# Redis ecrit un fichier ou il veut sur le disque ; une application n'en a
# jamais besoin.
user default on >$REDIS_PASSWORD ~* &* +@all -@admin

# Persistance : le journal des ecritures, synchronise chaque seconde. Au pire
# une seconde perdue sur une coupure de courant -- et une file de taches qui
# survit au redemarrage. Pas d'instantanes RDB en plus : le journal suffit,
# et chaque instantane duplique le processus.
appendonly yes
appendfsync everysec
save ""

# Memoire : au plafond, seules les cles qui ont une duree de vie (le cache)
# sont evincees. Les autres -- une file de taches, une session -- ne
# disparaissent jamais en silence : l'ecriture est refusee avec une erreur
# OOM, ce qui se voit.
maxmemory $REDIS_MAXMEMORY
maxmemory-policy volatile-lru

logfile ""
CONF
if id redis >/dev/null 2>&1; then
  chown redis:redis "$CONF" 2>/dev/null || true
fi
chmod 600 "$CONF"
umask 022

echo "[codelab-redis] pret -- $REDIS_HOST_PUBLIC:$REDIS_PORT, donnees plafonnees a $REDIS_MAXMEMORY."
exec docker-entrypoint.sh "$@"

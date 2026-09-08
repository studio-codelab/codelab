#!/bin/sh
# Entrypoint de codelab-postgres.
#
# Genere (ou reprend) le mot de passe Postgres au premier demarrage, l'ecrit
# dans credentials.env -- LE fichier de secrets de CodeLab, aucun autre --
# puis rend la main a l'entrypoint officiel de l'image.
#
# Pourquoi un fichier dans une image plutot qu'un script inline dans le
# docker-compose : ZimaOS reecrit le compose a l'import et desechappe les
# "$$" en "$". Docker Compose interpole alors ces "$VAR" avant que le shell
# ne les voie, et le script recoit des chaines vides ("mkdir: cannot create
# directory ''"). Un script versionne dans l'image ne traverse jamais cette
# reecriture -- c'est la seule facon fiable d'avoir du shell ici.
set -e

CONFIG_DIR="${CODELAB_CONFIG_DIR:-/var/lib/codelab/config}"
ENV_FILE="$CONFIG_DIR/credentials.env"
LEGACY_PW_FILE="$CONFIG_DIR/postgres_password"

mkdir -p "$CONFIG_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Lit une cle dans credentials.env (chaine vide si absente).
get_value() {
  # Derniere occurrence : upsert_block reecrit toujours son bloc en fin de
  # fichier, donc une valeur laissee plus haut (edition a la main, ancien
  # format sans marqueurs) est forcement la perimee.
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# Remplacement par BLOC entier (commentaires inclus), delimite par des
# marqueurs "# ===== NOM =====" / "# ===== /NOM =====" -- pas juste par
# prefixe de cle, sinon les commentaires documentant chaque bloc
# s'accumuleraient en double a chaque redemarrage. Chaque service ne touche
# qu'a son propre bloc, les autres restent intacts quel que soit l'ordre de
# demarrage.
upsert_block() {
  name="$1"; content="$2"
  awk -v s="# ===== $name =====" -v e="# ===== /$name =====" \
    '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.tmp"
  {
    cat "$ENV_FILE.tmp"
    echo "# ===== $name ====="
    printf '%s\n' "$content" | sed 's/^[[:space:]]*//'
    echo "# ===== /$name ====="
  } > "$ENV_FILE.new"
  mv "$ENV_FILE.new" "$ENV_FILE"
  rm -f "$ENV_FILE.tmp"
}

# Ordre de priorite : la valeur deja presente dans credentials.env fait
# autorite (c'est celle avec laquelle la base a ete initialisee), sinon on
# reprend l'ancien fichier postgres_password d'une install anterieure, sinon
# on genere. Ne JAMAIS regenerer par-dessus une valeur existante : la base
# refuserait la connexion.
PG_PASSWORD="$(get_value POSTGRES_PASSWORD)"
MIGRATED=0
if [ -z "$PG_PASSWORD" ] && [ -s "$LEGACY_PW_FILE" ]; then
  PG_PASSWORD="$(cat "$LEGACY_PW_FILE")"
  MIGRATED=1
fi
if [ -z "$PG_PASSWORD" ]; then
  PG_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "[codelab-postgres] mot de passe genere."
fi

# ------------------- noms des bases (voir le bloc plus bas) -------------------
# Definis ici parce que credentials.env, ecrit juste apres, publie le nom de
# la base d'instance a tous les autres services.
#
# Base d'INSTANCE de Dagster : runs, evenements, planifications. C'est
# POSTGRES_DB, donc l'image officielle la cree elle-meme a l'initialisation
# du cluster.
CODELAB_INSTANCE_DB="${POSTGRES_DB:-dagster}"
# Bases de projet creees d'office. Liste separee par des espaces : un projet
# ajoute ici est provisionne au prochain redemarrage de la stack. Les projets
# crees en cours de route passent plutot par "codelab-project <nom>" depuis
# une session SSH, qui fait exactement la meme chose sans redemarrage.
CODELAB_PROJECT_DBS="${CODELAB_PROJECT_DBS:-diagnostic}"
# Schema qui recoit les tables des assets, dans CHAQUE base de projet. Meme
# nom partout : c'est ce qui permet de copier un projet d'une base a l'autre
# sans toucher aux requetes.
CODELAB_PROJECT_SCHEMA="${CODELAB_PROJECT_SCHEMA:-dagster}"

upsert_block "codelab-header" "# credentials.env -- TOUS les identifiants CodeLab, generes et
# geres automatiquement par les services au demarrage. C'est le seul
# fichier de secrets : rien n'est stocke ailleurs. Ne pas editer a la
# main, chaque bloc est entierement reecrit au redemarrage du service
# concerne. Permissions 600 -- lisible uniquement par root sur le
# disque du ZimaOS."

upsert_block "codelab-postgres" "# Serveur Postgres partage par tous les services CodeLab.
# Utilise par : codelab-dev, codelab-dagster, codelab-dagster-daemon,
# qui lisent POSTGRES_PASSWORD ici meme (plus de fichier dedie).
#
# POSTGRES_DB est la base d'INSTANCE de Dagster (runs, evenements,
# planifications) -- pas une base fourre-tout. Les donnees d'un projet
# vont dans la base du projet (schema \"dagster\"), dont le nom est celui
# du dossier ; un projet le redit dans son .env avec CODELAB_DB.
POSTGRES_HOST=codelab-postgres
POSTGRES_PORT=5432
POSTGRES_DB=$CODELAB_INSTANCE_DB
POSTGRES_USER=codelab
POSTGRES_PASSWORD=$PG_PASSWORD"

upsert_block "codelab-dev" "# Pas de mot de passe : l'acces SSH (port 2222) se fait uniquement par
# cle publique. Les cles autorisees et les cles hote sont regroupees
# dans /DATA/AppData/codelab/config/ssh/ (authorized_keys et
# host_keys/) -- des fichiers de cles, pas des valeurs a lister ici."

chmod 600 "$ENV_FILE"

# Migration terminee et ecrite : l'ancien fichier n'a plus de raison
# d'exister. Supprime seulement apres coup, jamais avant.
if [ "$MIGRATED" = "1" ]; then
  rm -f "$LEGACY_PW_FILE"
  echo "[codelab-postgres] postgres_password migre vers credentials.env puis supprime."
fi

# L'image officielle refuse POSTGRES_PASSWORD et POSTGRES_PASSWORD_FILE
# simultanement : on ne passe que la variable, lue du fichier unique.
unset POSTGRES_PASSWORD_FILE
POSTGRES_PASSWORD="$PG_PASSWORD"
export POSTGRES_PASSWORD

# ---------------------- bases de donnees de la stack ----------------------
#
# CodeLab n'a pas de base fourre-tout. Il y a :
#
#   - "dagster"     : les tables d'instance de Dagster (runs, evenements,
#                     planifications). C'est POSTGRES_DB, donc l'image
#                     officielle la cree elle-meme a l'initialisation.
#   - une base PAR PROJET, nommee comme le dossier du projet ("diagnostic"
#                     pour celui livre en modele), avec un schema "dagster"
#                     dedans : les tables produites par les assets y
#                     atterrissent sans que les pipelines aient a prefixer
#                     quoi que ce soit.
#
# La base "postgres" reste presente : c'est la base de maintenance creee par
# initdb, celle a laquelle on se connecte pour en creer une autre. Aucun
# service CodeLab n'y ecrit -- elle doit rester vide.
#
# Pourquoi pas /docker-entrypoint-initdb.d : ce dossier n'est joue qu'a la
# toute premiere initialisation du cluster. Les donnees survivent aux
# reinstallations, donc une stack existante ne le rejouerait jamais, et un
# projet ajoute plus tard n'aurait pas sa base. Ce bloc-ci tourne a CHAQUE
# demarrage et ne fait rien quand tout est deja en place.

# CREATE DATABASE n'accepte pas IF NOT EXISTS, d'ou le test prealable.
# La connexion passe par la socket Unix locale, en "trust" (initdb --auth-local),
# donc pas de mot de passe a fournir ici.
create_db_if_missing() {
  db="$1"
  if [ "$(psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -tAc \
          "SELECT 1 FROM pg_database WHERE datname='$db'")" = "1" ]; then
    return 0
  fi
  psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -c "CREATE DATABASE \"$db\"" >/dev/null
  echo "[codelab-postgres] base \"$db\" creee."
}

# Provisionnement en tache de fond : le serveur doit d'abord ecouter, et
# c'est "exec docker-entrypoint.sh" plus bas qui le demarre. Toute erreur
# ici est non fatale -- une base de projet manquante se rattrape avec
# "codelab-project", alors qu'un Postgres qui ne demarre pas ne se rattrape
# pas du tout.
provision_databases() {
  i=0
  while [ "$i" -lt 60 ]; do
    if pg_isready -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -q; then
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if ! pg_isready -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -q; then
    echo "[codelab-postgres] serveur injoignable apres 60 s : bases de projet" \
         "non provisionnees." >&2
    return 1
  fi

  for db in $CODELAB_PROJECT_DBS; do
    create_db_if_missing "$db" || continue
    # Le schema d'abord, le search_path ensuite. ALTER ROLE ... IN DATABASE
    # est ce qui fait qu'un "CREATE TABLE ma_table" dans un asset atterrit
    # dans le schema du projet, sans prefixe dans le code.
    psql -U "$POSTGRES_USER" -d "$db" >/dev/null <<SQL || true
CREATE SCHEMA IF NOT EXISTS "$CODELAB_PROJECT_SCHEMA";
ALTER ROLE "$POSTGRES_USER" IN DATABASE "$db"
  SET search_path TO "$CODELAB_PROJECT_SCHEMA", public;
SQL
  done
}

provision_databases &

exec docker-entrypoint.sh "$@"

#!/bin/sh
# codelab-project -- cree (ou complete) la base d'un projet CodeLab.
#
#   codelab-project mon-projet
#
# Chaque projet du workspace a SA base, nommee comme son dossier, avec un
# schema "dagster" dedans : c'est la que les assets ecrivent leurs tables.
# La base d'instance de Dagster (runs, evenements, planifications) est une
# base a part, "dagster", a laquelle un projet ne touche pas.
#
# Idempotent : relancer la commande sur un projet existant ne detruit rien et
# se contente de reposer le schema et le search_path. C'est aussi la
# reparation a tenter en premier si un projet ne trouve plus sa base.
#
# L'entrypoint de codelab-postgres fait exactement la meme chose au demarrage
# pour les projets listes dans CODELAB_PROJECT_DBS. Cette commande sert aux
# projets crees ensuite, sans redemarrer la stack.
set -eu

usage() {
  echo "usage: codelab-project <nom-du-projet>" >&2
  echo "       cree la base <nom-du-projet> et son schema \"dagster\"." >&2
}

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
esac
if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

projet="$1"
# Le nom sert d'identifiant SQL et de nom de dossier. On refuse tout de suite
# ce qui n'est ni l'un ni l'autre plutot que de laisser Postgres renvoyer une
# erreur de syntaxe a rallonge sur un nom a espaces ou a guillemets.
case "$projet" in
  *[!a-zA-Z0-9_-]* | "" | [!a-zA-Z]*)
    echo "codelab-project: nom invalide \"$projet\" -- lettres, chiffres," \
         "tirets et soulignes, en commencant par une lettre." >&2
    exit 2 ;;
esac

ENV_FILE="${CODELAB_ENV_FILE:-/var/lib/codelab/config/credentials.env}"
# Base de maintenance : on ne peut pas creer une base depuis elle-meme, il
# faut etre connecte ailleurs. La base d'instance de Dagster existe toujours,
# c'est le point d'entree naturel.
ADMIN_DB="${CODELAB_INSTANCE_DB:-dagster}"
SCHEMA="${CODELAB_PROJECT_SCHEMA:-dagster}"

PGHOST="${PGHOST:-codelab-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-codelab}"
export PGHOST PGPORT PGUSER

if [ -z "${PGPASSWORD:-}" ] && [ -r "$ENV_FILE" ]; then
  # tail : la derniere occurrence fait autorite (bloc reecrit en fin de fichier).
  PGPASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | tail -n 1)"
  export PGPASSWORD
fi

if [ "$(psql -d "$ADMIN_DB" -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$projet'")" = "1" ]; then
  echo "[codelab-project] base \"$projet\" deja presente."
else
  # CREATE DATABASE n'accepte pas IF NOT EXISTS, d'ou le test ci-dessus.
  psql -d "$ADMIN_DB" -c "CREATE DATABASE \"$projet\"" >/dev/null
  echo "[codelab-project] base \"$projet\" creee."
fi

# ALTER ROLE ... IN DATABASE est ce qui fait qu'un "CREATE TABLE ma_table"
# dans un asset atterrit dans le schema du projet, sans prefixe dans le code.
psql -d "$projet" >/dev/null <<SQL
CREATE SCHEMA IF NOT EXISTS "$SCHEMA";
ALTER ROLE "$PGUSER" IN DATABASE "$projet" SET search_path TO "$SCHEMA", public;
SQL
echo "[codelab-project] schema \"$SCHEMA\" et search_path en place sur \"$projet\"."
echo "[codelab-project] dans le .env du projet : CODELAB_DB=$projet"

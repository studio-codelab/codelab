#!/bin/sh
# Entrypoint de codelab-dagster-proxy.
#
# Ce service n'a plus de mot de passe a lui : Dagster herite de la session du
# panneau (voir nginx.conf). L'entrypoint ne sert donc qu'a une chose --
# retirer l'ancien bloc d'identifiants de credentials.env, pour qu'une
# installation mise a jour ne conserve pas un mot de passe qui ne sert plus a
# rien et laisserait croire qu'il protege encore quelque chose.
set -eu

CONFIG_DIR="${CODELAB_CONFIG_DIR:-/var/lib/codelab/config}"
ENV_FILE="$CONFIG_DIR/credentials.env"

mkdir -p "$CONFIG_DIR"
[ -f "$ENV_FILE" ] || : > "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true

# Remplacement par BLOC entier, delimite par des marqueurs -- meme mecanique
# que codelab-postgres et codelab-app-manager : chaque service ne touche qu'a
# son propre bloc, les autres restent intacts quel que soit l'ordre de
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

# Le bloc n'est pas supprime mais remplace par une explication : quelqu'un qui
# cherchera DAGSTER_PASSWORD dans ce fichier doit comprendre ou il est passe,
# plutot que de trouver un vide.
upsert_block "codelab-dagster-proxy" "# Dagster (http://<IP>:3000/) utilise desormais la SESSION DU PANNEAU.
# Il n'y a plus ni DAGSTER_USER ni DAGSTER_PASSWORD : le mot de passe est
# celui du panneau (APP_MANAGER_ADMIN_PASSWORD), avec son second facteur s'il
# est active, et la deconnexion du panneau ferme aussi l'acces a Dagster.
# L'authentification HTTP Basic precedente n'avait ni session, ni expiration,
# ni deconnexion possible."
chmod 600 "$ENV_FILE" 2>/dev/null || true

echo "[codelab-dagster-proxy] pret -- Dagster sur le port 3000."
echo "[codelab-dagster-proxy] acces via la session du panneau (port 9001)."

exec "$@"

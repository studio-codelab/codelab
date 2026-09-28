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

# Le bloc est RETIRE, pas remplace : il ne portait plus aucune valeur. Qui
# cherche DAGSTER_PASSWORD trouve l'explication dans le sommaire en tete de
# credentials.env (« Dagster n'a pas d'identifiant a lui »), ecrit par
# codelab-postgres. L'authentification HTTP Basic precedente n'avait ni
# session, ni expiration, ni deconnexion possible.
awk -v s="# ===== codelab-dagster-proxy =====" -v e="# ===== /codelab-dagster-proxy =====" \
  '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true

echo "[codelab-dagster-proxy] pret -- Dagster sur le port 3000."
echo "[codelab-dagster-proxy] acces via la session du panneau (port 9001)."

exec "$@"

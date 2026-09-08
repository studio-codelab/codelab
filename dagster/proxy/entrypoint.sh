#!/bin/sh
# Entrypoint de codelab-dagster-proxy.
#
# Un seul role : garantir qu'un fichier d'authentification existe avant que
# nginx ne demarre. Le mot de passe suit exactement la convention des autres
# services -- genere une fois, ecrit dans credentials.env par bloc delimite,
# jamais regenere par-dessus une valeur existante (sinon il changerait a
# chaque redemarrage, et l'utilisateur devrait le relire a chaque fois).
set -eu

CONFIG_DIR="${CODELAB_CONFIG_DIR:-/var/lib/codelab/config}"
ENV_FILE="$CONFIG_DIR/credentials.env"
HTPASSWD=/etc/nginx/.htpasswd
USER_NAME="${DAGSTER_USER:-codelab}"

mkdir -p "$CONFIG_DIR"
[ -f "$ENV_FILE" ] || : > "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true

# Derniere occurrence : upsert_block reecrit toujours son bloc en fin de
# fichier, donc une valeur laissee plus haut est forcement la perimee.
get_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# Remplacement par BLOC entier (commentaires inclus), delimite par des
# marqueurs -- meme mecanique que codelab-postgres et codelab-app-manager :
# chaque service ne touche qu'a son propre bloc, les autres restent intacts
# quel que soit l'ordre de demarrage.
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

PASSWORD="$(get_value DAGSTER_PASSWORD)"
if [ -z "$PASSWORD" ]; then
  PASSWORD="$(openssl rand -base64 18 | tr -d '\n/+=' | cut -c1-20)"
  echo "[codelab-dagster-proxy] mot de passe genere."
fi

upsert_block "codelab-dagster-proxy" "# Acces a Dagster (http://<IP>:3000/), protege par mot de passe.
# Dagster n'a aucune authentification a lui : ce proxy en ajoute une devant.
# DAGSTER_USER / DAGSTER_PASSWORD : identifiants demandes par le navigateur.
DAGSTER_USER=$USER_NAME
DAGSTER_PASSWORD=$PASSWORD"
chmod 600 "$ENV_FILE" 2>/dev/null || true

# -b : mot de passe en argument, -c : cree le fichier. Reecrit a chaque
# demarrage, ce qui reapplique une valeur changee a la main dans
# credentials.env sans autre manipulation que le redemarrage du conteneur.
htpasswd -bc "$HTPASSWD" "$USER_NAME" "$PASSWORD" >/dev/null 2>&1
chmod 644 "$HTPASSWD"

echo "[codelab-dagster-proxy] pret -- Dagster sur le port 3000, utilisateur \"$USER_NAME\"."
echo "[codelab-dagster-proxy] mot de passe dans $ENV_FILE (cle DAGSTER_PASSWORD)."

exec "$@"

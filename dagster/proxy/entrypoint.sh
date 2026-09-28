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
# Sous le verrou partage avec les autres services qui ecrivent ce fichier
# (voir postgres/entrypoint.sh) : sans lui, ce retrait pouvait effacer un bloc
# qu'un voisin venait d'ajouter.
exec 9>"$ENV_FILE.lock"
if command -v flock >/dev/null 2>&1; then flock 9; fi
awk -v s="# ===== codelab-dagster-proxy =====" -v e="# ===== /codelab-dagster-proxy =====" \
  '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then flock -u 9; fi
exec 9>&-

# ------------------------------- MAILPIT -------------------------------
#
# Ce proxy garde aussi l'interface de Mailpit (port 8025), avec la meme
# session d'administration du panneau. Mailpit, lui, exige un mot de passe
# sur toute son API : sans lui, n'importe quelle application du reseau de la
# stack lirait les mails de ses voisines -- liens de reinitialisation de mot
# de passe compris. Ce mot de passe ne sert qu'entre ce proxy et Mailpit :
# genere ici, publie dans credentials.env sous un prefixe que le panneau ne
# transmet jamais aux applications, et ajoute par nginx a chaque requete
# deja autorisee.
INCLUDE_MAILPIT="${CODELAB_MAILPIT_AUTH_INCLUDE:-/etc/nginx/codelab/mailpit-auth.conf}"

exec 9>"$ENV_FILE.lock"
if command -v flock >/dev/null 2>&1; then flock 9; fi
MAILPIT_MDP="$(sed -n 's/^CODELAB_INTERNE_MAILPIT_UI_PASSWORD=//p' "$ENV_FILE" | tail -n 1)"
case "$MAILPIT_MDP" in
  ""|*[!0-9a-f]*) MAILPIT_MDP="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" ;;
esac
awk -v s="# ===== codelab-proxy =====" -v e="# ===== /codelab-proxy =====" \
  '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.proxy.tmp"
{
  cat "$ENV_FILE.proxy.tmp"
  echo "# ===== codelab-proxy ====="
  echo "# Mot de passe interne entre codelab-dagster-proxy et codelab-mailpit."
  echo "# Personne n'a a le taper : l'interface de Mailpit s'ouvre avec la"
  echo "# session d'administration du panneau. Jamais transmis aux applications."
  echo "CODELAB_INTERNE_MAILPIT_UI_PASSWORD=$MAILPIT_MDP"
  echo "# ===== /codelab-proxy ====="
} > "$ENV_FILE.proxy.new"
mv "$ENV_FILE.proxy.new" "$ENV_FILE"
rm -f "$ENV_FILE.proxy.tmp"
chmod 600 "$ENV_FILE" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then flock -u 9; fi
exec 9>&-

mkdir -p "$(dirname "$INCLUDE_MAILPIT")"
(
  umask 077
  printf 'proxy_set_header Authorization "Basic %s";\n' \
    "$(printf 'codelab:%s' "$MAILPIT_MDP" | base64 | tr -d '\n')" > "$INCLUDE_MAILPIT"
)

echo "[codelab-dagster-proxy] pret -- Dagster sur le port 3000, Mailpit sur le port 8025."
echo "[codelab-dagster-proxy] acces via la session du panneau (port 9001)."

exec "$@"

#!/bin/sh
# Entrypoint de codelab-mailpit.
#
# Mailpit recoit les mails des applications (SMTP sur le port 1025, sans
# authentification, joignable seulement depuis le reseau de la stack), les
# garde consultables dans son interface, et les RELAIE vers le vrai serveur
# d'envoi -- celui de Parametres > E-mail dans le panneau.
#
# Trois choses se font ici :
#   1. publier dans credentials.env l'adresse SMTP a donner aux applications
#      (MAIL_HOST, MAIL_PORT, MAIL_URL) ;
#   2. attendre le mot de passe interne de l'interface, que publie
#      codelab-dagster-proxy -- sans lui, n'importe quelle application du
#      reseau lirait les mails de ses voisines par l'API de Mailpit ;
#   3. lancer Mailpit, et le relancer quand le serveur d'envoi change dans le
#      panneau : Mailpit ne relit sa configuration de relais qu'au demarrage.
set -eu

CONFIG_DIR="${CODELAB_CONFIG_DIR:-/var/lib/codelab/config}"
ENV_FILE="$CONFIG_DIR/credentials.env"
MAILPIT_BIN="${CODELAB_MAILPIT_BIN:-/mailpit}"
MAIL_HOTE="${CODELAB_MAILPIT_HOST:-codelab-mailpit}"
# "auto" : relaie des qu'un serveur d'envoi est configure. "non" : garde
# tout sans rien envoyer -- le mode test, pour essayer une inscription sans
# ecrire a personne.
RELAIS="${CODELAB_MAILPIT_RELAIS:-auto}"
INTERVALLE="${CODELAB_MAILPIT_INTERVALLE:-15}"
ATTENTE_MAX="${CODELAB_MAILPIT_ATTENTE:-120}"

mkdir -p "$CONFIG_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

get_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

avec_verrou() {
  # $1 = x (ecriture) ou s (lecture) ; le reste est la commande. Meme verrou
  # que les autres services qui ecrivent credentials.env.
  mode="$1"; shift
  (
    exec 9>"$ENV_FILE.lock"
    if command -v flock >/dev/null 2>&1; then flock "-$mode" 9; fi
    "$@"
  )
}

ecrire_bloc() {
  awk -v s="# ===== codelab-mailpit =====" -v e="# ===== /codelab-mailpit =====" \
    '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.mailpit.tmp"
  {
    cat "$ENV_FILE.mailpit.tmp"
    echo "# ===== codelab-mailpit ====="
    echo "# Envoi de mails pour les applications : un serveur SMTP interne, sans"
    echo "# authentification, qui garde chaque mail consultable (port 8025, session"
    echo "# d'administration du panneau) et le relaie par le serveur d'envoi du"
    echo "# panneau (Parametres > E-mail). Transmis aux applications."
    echo "MAIL_HOST=$MAIL_HOTE"
    echo "MAIL_PORT=1025"
    echo "MAIL_URL=smtp://$MAIL_HOTE:1025"
    echo "# ===== /codelab-mailpit ====="
  } > "$ENV_FILE.mailpit.new"
  mv "$ENV_FILE.mailpit.new" "$ENV_FILE"
  rm -f "$ENV_FILE.mailpit.tmp"
  chmod 600 "$ENV_FILE"
}

# L'empreinte de ce qui, change, demande une relance.
signature() {
  grep '^CODELAB_INTERNE_' "$ENV_FILE" 2>/dev/null | md5sum | cut -c1-32
}

avec_verrou x ecrire_bloc

# ---------------------- le mot de passe de l'interface ----------------------
i=0
UI_MDP=""
while [ "$i" -lt "$ATTENTE_MAX" ]; do
  UI_MDP="$(avec_verrou s get_value CODELAB_INTERNE_MAILPIT_UI_PASSWORD)"
  [ -n "$UI_MDP" ] && break
  [ "$i" = 0 ] && echo "[codelab-mailpit] attente du mot de passe de l'interface" \
    "(ecrit par codelab-dagster-proxy)..."
  i=$((i + 1))
  sleep 1
done
if [ -z "$UI_MDP" ]; then
  # Demarrer sans lui laisserait l'API ouverte a toutes les applications :
  # on s'arrete, et la politique de redemarrage reessaiera.
  echo "[codelab-mailpit] CODELAB_INTERNE_MAILPIT_UI_PASSWORD absent de" \
       "$ENV_FILE : codelab-dagster-proxy tourne-t-il ? Arret." >&2
  exit 1
fi
export MP_UI_AUTH="codelab:$UI_MDP"

# ------------------------------- le relais -------------------------------
preparer_relais() {
  for v in HOST PORT STARTTLS TLS AUTH USERNAME PASSWORD OVERRIDE_FROM; do
    unset "MP_SMTP_RELAY_$v"
  done
  unset MP_SMTP_RELAY_ALL
  hote="$(avec_verrou s get_value CODELAB_INTERNE_RELAIS_HOST)"
  if [ "$RELAIS" = "non" ] || [ -z "$hote" ]; then
    if [ "$RELAIS" = "non" ]; then
      echo "[codelab-mailpit] mode test : les mails sont gardes, aucun n'est envoye."
    else
      echo "[codelab-mailpit] aucun serveur d'envoi dans le panneau" \
           "(Parametres > E-mail) : les mails sont gardes, aucun n'est envoye."
    fi
    return 0
  fi
  export MP_SMTP_RELAY_HOST="$hote"
  export MP_SMTP_RELAY_PORT="$(avec_verrou s get_value CODELAB_INTERNE_RELAIS_PORT)"
  case "$(avec_verrou s get_value CODELAB_INTERNE_RELAIS_TLS)" in
    ssl)  export MP_SMTP_RELAY_TLS=true ;;
    none) ;;
    *)    export MP_SMTP_RELAY_STARTTLS=true ;;
  esac
  utilisateur="$(avec_verrou s get_value CODELAB_INTERNE_RELAIS_USER)"
  if [ -n "$utilisateur" ]; then
    export MP_SMTP_RELAY_AUTH=plain
    export MP_SMTP_RELAY_USERNAME="$utilisateur"
    export MP_SMTP_RELAY_PASSWORD="$(avec_verrou s get_value CODELAB_INTERNE_RELAIS_PASSWORD)"
  else
    export MP_SMTP_RELAY_AUTH=none
  fi
  # L'expediteur est remplace par celui du compte : Gmail et la plupart des
  # fournisseurs refusent d'expedier au nom d'une autre adresse.
  expediteur="$(avec_verrou s get_value CODELAB_INTERNE_RELAIS_FROM)"
  if [ -n "$expediteur" ]; then export MP_SMTP_RELAY_OVERRIDE_FROM="$expediteur"; fi
  export MP_SMTP_RELAY_ALL=true
  echo "[codelab-mailpit] relais actif par $hote."
}

PID=""
lancer() {
  preparer_relais
  "$MAILPIT_BIN" "$@" &
  PID=$!
}
arreter() {
  if [ -n "$PID" ]; then
    kill -TERM "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
}
trap 'arreter; exit 0' TERM INT

lancer "$@"
SIG="$(avec_verrou s signature)"
while :; do
  sleep "$INTERVALLE" &
  wait $! || true
  if ! kill -0 "$PID" 2>/dev/null; then
    # Mailpit s'est arrete de lui-meme : on sort avec son code, et la
    # politique de redemarrage de Docker prend le relais.
    code=0
    wait "$PID" || code=$?
    exit "$code"
  fi
  NOUVELLE="$(avec_verrou s signature)"
  if [ "$NOUVELLE" != "$SIG" ]; then
    echo "[codelab-mailpit] configuration changee : relance."
    arreter
    UI_MDP="$(avec_verrou s get_value CODELAB_INTERNE_MAILPIT_UI_PASSWORD)"
    [ -n "$UI_MDP" ] && export MP_UI_AUTH="codelab:$UI_MDP"
    lancer "$@"
    SIG="$NOUVELLE"
  fi
done

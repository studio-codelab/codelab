# bases-du-dossier.sh -- le compte Postgres du projet ou l'on se trouve.
#
# Lu par chaque shell du poste (profil et .bashrc, voir dev/entrypoint.sh).
#
# UNE BASE, UN COMPTE. Le poste n'a plus le compte administrateur de
# Postgres : dans /workspace/<projet>, le shell -- donc psql, le code lance
# a la main, et un agent comme Codex -- recoit le compte de la base de CE
# projet, et seulement lui :
#   PGDATABASE, PGUSER, PGPASSWORD          pour psql et la libpq ;
#   POSTGRES_USER, POSTGRES_PASSWORD        pour le code (les memes noms que
#   CODELAB_DB                              dans le panneau).
# Hors d'un projet, rien : aucune base n'est ouverte par defaut.
#
# La base d'un projet : CODELAB_DB dans son .env, sinon le nom du dossier --
# la regle du panneau. Les mots de passe viennent de ~/.codelab-bases, que
# le poste tient a jour (une ligne « base mot-de-passe » par base).
#
# Recalcule a chaque invite (PROMPT_COMMAND), mais seulement quand le
# dossier a change : un « cd » d'un projet a l'autre change de compte.

codelab_base_du_dossier() {
  [ "${_CODELAB_DOSSIER_VU:-}" = "$PWD" ] && return 0
  _CODELAB_DOSSIER_VU=$PWD
  _codelab_projet=""
  _codelab_ws=${CODELAB_WORKSPACE:-/workspace}
  case "$PWD/" in
    "$_codelab_ws"/?*/*) _codelab_projet=${PWD#"$_codelab_ws"/}; _codelab_projet=${_codelab_projet%%/*} ;;
  esac
  _codelab_base=""
  if [ -n "$_codelab_projet" ]; then
    _codelab_base=$(sed -n "s/^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}CODELAB_DB[[:space:]]*=[[:space:]]*[\"']\{0,1\}\([A-Za-z][A-Za-z0-9_-]*\).*/\2/p" \
                      "$_codelab_ws/$_codelab_projet/.env" 2>/dev/null | tail -n 1)
    [ -n "$_codelab_base" ] || _codelab_base=$_codelab_projet
  fi
  _codelab_mdp=""
  if [ -n "$_codelab_base" ] && [ -r "$HOME/.codelab-bases" ]; then
    _codelab_mdp=$(awk -v b="$_codelab_base" '$1 == b { m = $2 } END { print m }' "$HOME/.codelab-bases")
  fi
  if [ -n "$_codelab_mdp" ]; then
    export PGDATABASE="$_codelab_base" PGUSER="$_codelab_base" PGPASSWORD="$_codelab_mdp"
    export POSTGRES_USER="$_codelab_base" POSTGRES_PASSWORD="$_codelab_mdp" CODELAB_DB="$_codelab_base"
    _CODELAB_BASE_POSEE=1
  elif [ -n "${_CODELAB_BASE_POSEE:-}" ]; then
    unset PGDATABASE PGUSER PGPASSWORD POSTGRES_USER POSTGRES_PASSWORD CODELAB_DB
    _CODELAB_BASE_POSEE=""
  fi
  unset _codelab_ws _codelab_projet _codelab_base _codelab_mdp
}

codelab_base_du_dossier
case ";${PROMPT_COMMAND:-};" in
  *";codelab_base_du_dossier;"*) ;;
  *) PROMPT_COMMAND="codelab_base_du_dossier${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
esac

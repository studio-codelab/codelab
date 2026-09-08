#!/bin/sh
# codelab-agents -- installe (ou rafraichit) le AGENTS.md d'un projet.
#
#   codelab-agents [dossier]      # defaut : le dossier courant
#
# Codex lit deux niveaux d'instructions : un fichier global dans son dossier
# de configuration ($CODEX_HOME/AGENTS.md, pose par l'entrypoint de
# codelab-dev), puis le AGENTS.md a la racine du projet. Le niveau global n'est
# pas honore par toutes les versions de la CLI -- cette commande recopie donc
# le manuel dans le projet, ou il est lu a coup sur.
#
# Le manuel est insere dans un bloc delimite, reecrit a chaque appel. Tout ce
# qui est ecrit HORS de ce bloc est conserve : c'est la que vivent les
# consignes propres au projet, et les relancer apres une mise a jour de la
# stack ne les efface pas.
set -eu

MANUEL="${CODELAB_AGENTS_SOURCE:-/usr/local/share/codelab/agents-codelab.md}"
DEBUT='<!-- codelab:debut -- bloc gere par codelab-agents, ne pas editer a la main -->'
FIN='<!-- codelab:fin -->'

usage() {
  echo "usage: codelab-agents [dossier-du-projet]" >&2
  echo "       ecrit le manuel CodeLab dans le AGENTS.md du projet." >&2
}

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
esac
if [ "$#" -gt 1 ]; then usage; exit 2; fi

PROJET="$(cd "${1:-.}" 2>/dev/null && pwd)" || {
  echo "codelab-agents: dossier introuvable : ${1:-.}" >&2
  exit 1
}
[ -r "$MANUEL" ] || {
  echo "codelab-agents: manuel introuvable ($MANUEL)." >&2
  exit 1
}

CIBLE="$PROJET/AGENTS.md"
TMP="$CIBLE.tmp.$$"
# Le fichier est reconstruit dans un temporaire puis deplace : une ecriture
# interrompue ne doit pas laisser un AGENTS.md tronque derriere elle.
trap 'rm -f "$TMP"' EXIT

{
  echo "$DEBUT"
  cat "$MANUEL"
  echo "$FIN"
} > "$TMP"

if [ -f "$CIBLE" ] && grep -qF "$DEBUT" "$CIBLE"; then
  # Bloc deja present : on ne remplace que lui, en gardant ce qu'il y a autour.
  awk -v debut="$DEBUT" -v fin="$FIN" -v bloc="$TMP" '
    $0 == debut { while ((getline ligne < bloc) > 0) print ligne; close(bloc); saut = 1; next }
    $0 == fin   { saut = 0; next }
    !saut       { print }
  ' "$CIBLE" > "$TMP.merge"
  mv "$TMP.merge" "$CIBLE"
  echo "codelab-agents: manuel CodeLab mis a jour dans $CIBLE."
else
  if [ -f "$CIBLE" ]; then
    # Fichier ecrit avant l'existence de cette commande : son contenu devient
    # la partie "projet", sous le bloc.
    { cat "$TMP"; echo; cat "$CIBLE"; } > "$TMP.merge"
    mv "$TMP.merge" "$CIBLE"
    echo "codelab-agents: manuel CodeLab ajoute en tete de $CIBLE (contenu existant conserve)."
  else
    NOM="$(basename "$PROJET")"
    {
      cat "$TMP"
      cat <<SQUELETTE

# Projet $NOM

*A completer -- ces sections sont lues par l'agent avant chaque tache.*

## Ce que fait ce projet

## Commandes

| But | Commande |
|---|---|
| Installer | |
| Tester | |
| Construire | |
| Lancer en local | |

## Conventions propres a ce projet
SQUELETTE
    } > "$TMP.merge"
    mv "$TMP.merge" "$CIBLE"
    echo "codelab-agents: $CIBLE cree -- complete la section \"Projet $NOM\"."
  fi
fi

chmod g+rw "$CIBLE" 2>/dev/null || true

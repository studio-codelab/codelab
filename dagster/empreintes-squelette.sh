#!/bin/sh
# Regenere dagster/squelette.sums : l'empreinte SHA-256 de CHAQUE version que
# CodeLab a livree, pour chaque fichier du squelette /workspace.
#
# A QUOI CELA SERT
#
# Le squelette est copie dans /workspace au premier demarrage, puis plus
# jamais : le workspace appartient a l'utilisateur, et une copie qui ecrase
# est une perte de donnees silencieuse. Consequence longtemps invisible : une
# correction apportee au projet "diagnostic" ne pouvait atteindre AUCUNE
# installation existante. L'image portait le correctif, le disque gardait le
# defaut, et "docker compose pull" n'y changeait rien.
#
# Ce fichier permet de distinguer les deux seuls cas qui comptent :
#
#   le fichier sur disque est l'une de nos anciennes versions -- personne n'y
#     a touche, on peut donc le mettre a jour sans rien perdre ;
#   le fichier sur disque ne correspond a aucune version livree -- c'est du
#     travail de l'utilisateur, on n'y touche pas.
#
# C'est la meme idee que les "conffiles" d'un gestionnaire de paquets, reduite
# a ce dont on a besoin ici.
#
# QUAND LE RELANCER
#
# A chaque fois qu'un fichier de workspace/ est modifie, AVANT de committer :
#
#     ./dagster/empreintes-squelette.sh
#
# Le fichier produit est versionne. Il doit l'etre : l'image se construit sans
# l'historique git (le contexte Docker ne contient pas .git), le calcul ne peut
# donc pas se faire au moment du build.
#
# Oublier de le relancer n'est pas dangereux -- la nouvelle version n'est
# simplement pas reconnue comme etant la notre, et le fichier est laisse tel
# quel sur les installations existantes, ce qui est le comportement prudent.
set -eu

cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  echo "empreintes-squelette.sh : a lancer depuis le depot git." >&2
  exit 1
fi

SORTIE=dagster/squelette.sums
TMP="$SORTIE.tmp"

{
  echo "# Empreintes SHA-256 de toutes les versions livrees du squelette."
  echo "# Genere par dagster/empreintes-squelette.sh -- ne pas editer a la main."
  echo "# Format : <sha256> <chemin relatif dans le squelette>"
} > "$TMP"

# --follow suit les renommages : un fichier deplace garde son historique, donc
# ses anciennes empreintes restent reconnues.
for chemin in $(git ls-tree -r --name-only HEAD workspace/); do
  relatif="${chemin#workspace/}"

  # La version du repertoire de travail d'abord, et pas seulement
  # l'historique : c'est ELLE qui part dans l'image. Sans cette ligne, une
  # modification pas encore committee ne serait jamais reconnue comme etant
  # la notre -- et le manifeste serait perpetuellement en retard d'un
  # commit, ce qui rend la regeneration impossible a faire au bon moment.
  [ -f "$chemin" ] && echo "$(sha256sum < "$chemin" | cut -d' ' -f1) $relatif"

  for commit in $(git log --format=%H --follow -- "$chemin"); do
    # Passage par un fichier temporaire, et NON par un tube. Dans
    # "git show ... | sha256sum", le statut du pipeline est celui de
    # sha256sum : un git show en echec (le fichier n'existe pas a ce commit,
    # ce qui arrive en remontant un renommage) donnerait quand meme un
    # succes, et l'empreinte du VIDE entrerait dans le manifeste. Un fichier
    # vide chez l'utilisateur serait alors reconnu comme l'une de nos
    # versions, donc ecrase.
    if git show "$commit:$chemin" > "$TMP.blob" 2>/dev/null; then
      echo "$(sha256sum < "$TMP.blob" | cut -d' ' -f1) $relatif"
    fi
  done
done | sort -u >> "$TMP"

rm -f "$TMP.blob"
mv "$TMP" "$SORTIE"
echo "$SORTIE : $(grep -cv '^#' "$SORTIE") empreintes."

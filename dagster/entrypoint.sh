#!/bin/sh
# Entrypoint commun a codelab-dagster (webserver) et codelab-dagster-daemon.
# Trois roles :
#   1. Lire le mot de passe Postgres dans credentials.env -- le fichier unique
#      de secrets CodeLab -- et l'exposer en DAGSTER_PG_PASSWORD, car
#      dagster.yaml ne sait lire un secret que depuis une env var.
#   2. Amorcer /opt/dagster/home et /workspace au tout premier demarrage,
#      sans jamais ecraser ce que l'utilisateur a deja modifie. /workspace
#      recoit un squelette complet : README des conventions, definitions.py
#      agregateur, et le projet "diagnostic" qui sert de modele.
#   3. Poser le socle de permissions sur /workspace (groupe commun, setgid),
#      pour que les fichiers ecrits par les jobs restent modifiables depuis
#      une session SSH.
set -e

# Ce service tourne en root. Sans cet umask, tout ce qu'un job ecrit dans
# /workspace sort en 0644 : le bit setgid donne le bon groupe, mais ce
# groupe n'a que la lecture, et l'utilisateur SSH ne peut pas reprendre le
# fichier. C'est LA ligne qui rend le workspace reellement partage.
umask 002

ENV_FILE="${CODELAB_ENV_FILE:-/var/lib/codelab/config/credentials.env}"

# Ces deux services ont "depends_on: codelab-postgres: service_healthy", donc
# credentials.env est deja ecrit quand on arrive ici. L'attente couvre le cas
# ou quelqu'un lance le conteneur seul, sans la stack.
i=0
while [ "$i" -lt 30 ]; do
  if [ -r "$ENV_FILE" ] && grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE"; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

if [ -r "$ENV_FILE" ]; then
  # tail : la derniere occurrence fait autorite (bloc reecrit en fin de fichier).
  DAGSTER_PG_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | tail -n 1)"
  export DAGSTER_PG_PASSWORD
fi
if [ -z "${DAGSTER_PG_PASSWORD}" ]; then
  echo "[codelab] POSTGRES_PASSWORD introuvable dans $ENV_FILE -- la connexion" \
       "a la base va echouer." >&2
fi

# --------------------- permissions partagees sur /workspace ---------------------
#
# /workspace est ecrit par trois services aux identites differentes : les
# sessions SSH en "vscode" (uid 1000), Dagster et app-manager en root. Sans
# precaution, un fichier produit par un job Dagster sort en "root:root 0644"
# et n'est plus modifiable depuis VS Code -- et l'inverse est vrai aussi.
#
# Trois mecanismes, tous les trois necessaires :
#   1. le groupe "codelab" (gid 2000), present dans les trois images sous le
#      MEME numero -- le noyau ne connait que des numeros ;
#   2. le bit setgid (2775) : un fichier cree herite du groupe du dossier
#      parent, pas du groupe primaire de son createur ;
#   3. umask 002 (pose plus haut) : sans lui le setgid donne le bon groupe,
#      mais en lecture seule.
#
# La passe recursive sur les fichiers deja presents ne tourne qu'une fois,
# tracee par un marqueur. Supprimer /workspace/.codelab/permissions-v1 force
# une reapplication complete au prochain demarrage : c'est la reparation a
# tenter en premier si un fichier resiste.
CODELAB_GROUP="${CODELAB_GROUP:-codelab}"
WORKSPACE_DIR="${WORKSPACE:-/workspace}"
PERM_MARKER="$WORKSPACE_DIR/.codelab/permissions-v1"

mkdir -p "$WORKSPACE_DIR"
chgrp "$CODELAB_GROUP" "$WORKSPACE_DIR" 2>/dev/null || true
chmod 2775 "$WORKSPACE_DIR" 2>/dev/null || true
# ACL par defaut : filet supplementaire pour les processus qui reimposent
# leur propre umask. Optionnel -- sans support ACL, les trois mecanismes
# ci-dessus suffisent.
setfacl -d -m "g:$CODELAB_GROUP:rwx" "$WORKSPACE_DIR" 2>/dev/null || true

if [ ! -f "$PERM_MARKER" ]; then
    echo "[codelab-dagster] premiere passe de permissions sur $WORKSPACE_DIR..."
    # Le groupe d'abord, les droits ensuite : un chmod g+w sur un fichier
    # encore dans le mauvais groupe ne servirait a rien. Le X majuscule ne
    # rend executables que les dossiers, pas chaque fichier de code.
    chgrp -R "$CODELAB_GROUP" "$WORKSPACE_DIR" 2>/dev/null || true
    chmod -R g+rwX "$WORKSPACE_DIR" 2>/dev/null || true
    find "$WORKSPACE_DIR" -type d -exec chmod g+s {} + 2>/dev/null || true
    setfacl -R -d -m "g:$CODELAB_GROUP:rwx" "$WORKSPACE_DIR" 2>/dev/null || true
    mkdir -p "$(dirname "$PERM_MARKER")"
    echo "Supprimer ce fichier force une reapplication complete au prochain demarrage." > "$PERM_MARKER"
    chgrp "$CODELAB_GROUP" "$(dirname "$PERM_MARKER")" "$PERM_MARKER" 2>/dev/null || true
    chmod 2775 "$(dirname "$PERM_MARKER")" 2>/dev/null || true
fi

mkdir -p "${DAGSTER_HOME}"
if [ ! -f "${DAGSTER_HOME}/dagster.yaml" ]; then
  cp /opt/dagster/dagster.yaml.default "${DAGSTER_HOME}/dagster.yaml"
  echo "[codelab] dagster.yaml initialise dans ${DAGSTER_HOME} (stockage Postgres)."
fi

# --------------------------- amorcage du workspace ---------------------------
#
# Le squelette livre avec l'image (README, definitions.py agregateur, projet
# "diagnostic" qui sert de modele) est copie dans /workspace au premier
# demarrage. Un marqueur evite de le refaire ensuite : sans lui, supprimer un
# projet le verrait reapparaitre a chaque redemarrage, ce qui est
# insupportable a l'usage.
#
# Regle absolue : on ne remplace JAMAIS un fichier existant. Le workspace
# contient le travail de l'utilisateur ; une copie qui ecrase est une perte de
# donnees silencieuse. Un fichier deja present sous le meme nom est copie a
# cote avec le suffixe .exemple, et l'utilisateur decide.
WORKSPACE_SEED=/opt/dagster/workspace.default
SEED_MARKER=/workspace/.codelab/workspace-v1

if [ ! -f "$SEED_MARKER" ] && [ -d "$WORKSPACE_SEED" ]; then
  echo "[codelab] amorcage de /workspace depuis le squelette de l'image..."
  for entree in "$WORKSPACE_SEED"/*; do
    [ -e "$entree" ] || continue
    nom="$(basename "$entree")"
    cible="/workspace/$nom"
    if [ ! -e "$cible" ]; then
      cp -r "$entree" "$cible"
      echo "[codelab]   $nom copie."
    elif [ -f "$entree" ]; then
      # Cas typique : un definitions.py ecrit avant cette version. On depose
      # la nouvelle version a cote plutot que de l'ecraser -- il contient
      # peut-etre des assets qui n'existent que la.
      cp -f "$entree" "$cible.exemple"
      echo "[codelab]   $nom existe deja : nouvelle version deposee dans $nom.exemple," \
           "a fusionner a la main."
    else
      echo "[codelab]   $nom existe deja : laisse tel quel."
    fi
  done

  # Le squelette sort avec le groupe et les droits du workspace, sinon les
  # sessions SSH ne pourraient pas modifier des fichiers copies par root.
  chgrp -R "$CODELAB_GROUP" /workspace 2>/dev/null || true
  chmod -R g+rwX /workspace 2>/dev/null || true
  find /workspace -type d -exec chmod g+s {} + 2>/dev/null || true

  mkdir -p "$(dirname "$SEED_MARKER")"
  echo "Supprimer ce fichier fait recopier le squelette de l'image au prochain demarrage." > "$SEED_MARKER"
  chgrp "$CODELAB_GROUP" "$SEED_MARKER" 2>/dev/null || true
fi

exec "$@"

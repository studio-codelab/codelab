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
#   4. Abandonner root avant de lancer Dagster : les trois premiers roles en
#      ont besoin, l'execution des jobs non.
set -e

# Ce script demarre en root (voir le role 4 plus bas). Sans cet umask, tout ce
# qu'un job ecrit dans /workspace sort en 0644 : le bit setgid donne le bon
# groupe, mais ce groupe n'a que la lecture, et l'utilisateur SSH ne peut pas
# reprendre le fichier. C'est LA ligne qui rend le workspace reellement
# partage -- et elle est heritee par le processus lance apres la bascule.
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
# /workspace est ecrit par des identites differentes : les sessions SSH en
# "vscode" (uid 1000), Dagster en "dagster" (uid 1002), les applications du
# panneau chacune sous le sien, et app-manager en root. Sans precaution, un
# fichier produit par un job Dagster n'est plus modifiable depuis VS Code --
# et l'inverse est vrai aussi. C'est le GROUPE, commun a tous, qui recolle
# tout cela.
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
# donnees silencieuse. Un fichier deja present sous le meme nom est donc
# simplement laisse tel quel -- aucune copie ".exemple" n'est deposee a cote :
# ces fichiers n'etaient jamais relus et polluaient le workspace. La version de
# reference reste dans l'image, sous /opt/dagster/workspace.default.
WORKSPACE_SEED=/opt/dagster/workspace.default
SEED_MARKER=/workspace/.codelab/workspace-v1

if [ ! -f "$SEED_MARKER" ] && [ -d "$WORKSPACE_SEED" ]; then
  echo "[codelab] amorcage de /workspace depuis le squelette de l'image..."
  # Les trois motifs couvrent aussi les entrees cachees : le squelette livre
  # un dossier .vscode (taches CodeLab), et un simple "*" ne l'aurait jamais
  # copie -- le glob du shell ignore les noms commencant par un point. Le
  # motif ".[!.]*" prend tout ce qui commence par un point sans etre "." ni
  # "..", et "..?*" rattrape le cas rare d'un nom commencant par deux points.
  # Pas de recouvrement entre les deux, donc pas d'entree traitee deux fois.
  # Les motifs sans correspondance sont ecartes par le test d'existence.
  for entree in "$WORKSPACE_SEED"/* "$WORKSPACE_SEED"/.[!.]* "$WORKSPACE_SEED"/..?*; do
    [ -e "$entree" ] || continue
    nom="$(basename "$entree")"
    cible="/workspace/$nom"
    if [ ! -e "$cible" ]; then
      cp -r "$entree" "$cible"
      echo "[codelab]   $nom copie."
    else
      # Ni ecrasement ni copie a cote : la version de l'image reste dans
      # /opt/dagster/workspace.default, a comparer a la main en cas de besoin
      # (docker exec codelab-dagster diff ...).
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

# ----------------------- abandon des privileges -----------------------
#
# Tout ce qui precede demande root : poser le groupe et le setgid sur
# /workspace, lire credentials.env (0600 root), amorcer le squelette. Rien de
# ce qui SUIT n'en a besoin -- et ce qui suit, c'est justement l'execution du
# code des jobs.
#
# En repli plutot qu'en echec, comme le reste de CodeLab : si l'utilisateur
# dagster ou gosu manquent (image construite ailleurs, image plus ancienne),
# on continue en root en le disant clairement. Un orchestrateur qui refuse de
# demarrer est un plus gros probleme que celui qu'on essaie de resoudre.
CODELAB_USER="${CODELAB_RUN_AS:-dagster}"

if [ "$(id -u)" -eq 0 ] && id "$CODELAB_USER" >/dev/null 2>&1 \
   && command -v gosu >/dev/null 2>&1; then

  # DAGSTER_HOME est un volume : son contenu appartient a root sur une
  # installation existante, et Dagster doit pouvoir y ecrire (dagster.yaml,
  # les journaux de runs). Idempotent, quelques millisecondes.
  chown -R "$CODELAB_USER":"$CODELAB_GROUP" "${DAGSTER_HOME}" 2>/dev/null || true

  # Verification avant de sauter : si gosu ne peut pas basculer (capability
  # SETUID retiree, par exemple), mieux vaut le savoir ici que de perdre le
  # service. Voir cap_add dans docker-compose.yml.
  if gosu "$CODELAB_USER" true 2>/dev/null; then
    echo "[codelab-dagster] execution en $CODELAB_USER (uid $(id -u "$CODELAB_USER"))."
    exec gosu "$CODELAB_USER" "$@"
  fi
  echo "[codelab-dagster] bascule vers $CODELAB_USER impossible (capability" \
       "SETUID retiree ?) -- poursuite en root." >&2
elif [ "$(id -u)" -eq 0 ]; then
  echo "[codelab-dagster] utilisateur $CODELAB_USER ou gosu absent --" \
       "poursuite en root." >&2
fi

exec "$@"

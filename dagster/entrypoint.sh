#!/bin/sh
# Entrypoint commun a codelab-dagster (webserver) et codelab-dagster-daemon.
# Quatre roles :
#   1. Lire le mot de passe Postgres dans credentials.env -- le fichier unique
#      de secrets CodeLab -- et l'exposer en DAGSTER_PG_PASSWORD, car
#      dagster.yaml ne sait lire un secret que depuis une env var.
#   2. Amorcer /opt/dagster/home et /workspace au tout premier demarrage,
#      sans jamais ecraser ce que l'utilisateur a deja modifie. /workspace
#      recoit un squelette complet : README des conventions, definitions.py
#      agregateur, et le projet "diagnostic" qui sert de modele. Ensuite, a
#      chaque demarrage, mettre a jour les seuls fichiers de ce squelette
#      que personne n'a touches -- sans quoi une correction livree dans
#      l'image n'atteint jamais une installation existante.
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

# ------------------- mise a jour des fichiers non modifies -------------------
#
# Le bloc ci-dessus ne s'execute qu'une fois. Pris seul, il a un defaut qui est
# reste invisible longtemps : une correction apportee au squelette ne pouvait
# atteindre AUCUNE installation existante. L'image portait le correctif, le
# disque gardait le defaut, et "docker compose pull" n'y changeait rien -- il
# fallait le savoir et recopier le fichier a la main.
#
# Ce qui suit rattrape exactement ce cas, et rien d'autre. Trois interdits :
#
#   ne cree jamais un fichier absent -- un projet supprime expres ne doit pas
#     reapparaitre, c'est la raison d'etre du marqueur ci-dessus ;
#   ne supprime jamais rien ;
#   ne remplace un fichier que s'il est prouve INTACT, c'est-a-dire si son
#     contenu est exactement celui que CodeLab y a ecrit la derniere fois.
#
# La preuve porte sur le CONTENU, jamais sur une date : une horloge de
# conteneur, un bind mount et un "cp -r" donnent des dates qui ne veulent rien
# dire.
#
# COMMENT ON SAIT CE QUE NOUS AVONS ECRIT
#
# Simplement en le notant. A chaque fois que CodeLab depose un fichier du
# squelette, il inscrit son empreinte dans .codelab/empreintes. Au demarrage
# suivant, un fichier dont l'empreinte n'a pas bouge n'a ete touche par
# personne ; un fichier dont elle a bouge appartient desormais a
# l'utilisateur. Aucun catalogue a tenir a jour, aucune etape a ne pas
# oublier avant de committer : le mecanisme s'entretient tout seul.
#
# Reste le cas des installations anterieures a ce mecanisme, qui n'ont encore
# rien de note -- et ce sont precisement celles qu'il faut rattraper.
# workspace.sums porte, pour elles seules, la liste des versions livrees
# avant. Cette liste est FIGEE : elle ne decrit que le passe, et n'a donc
# jamais a etre regeneree.
SEED_SUMS=/opt/dagster/workspace.sums
EMPREINTES="$WORKSPACE_DIR/.codelab/empreintes"

# Format d'une ligne, dans les deux fichiers : "<sha256> <chemin relatif>".
# Aucun chemin du squelette ne contient d'espace, et aucun n'a de raison d'en
# contenir : le second champ est donc le chemin entier.
#
# Ce fichier de notes vit dans le workspace, donc dans un dossier inscriptible
# par le groupe : une application compromise peut le modifier. Ce qu'elle y
# gagnerait est borne -- faire remplacer un fichier d'exemple par la version
# de l'image -- et jamais une elevation de privilege : le contenu ecrit vient
# toujours de l'image, jamais du fichier de notes. Il est malgre tout ecrit en
# 644 (personne d'autre que root n'a de raison d'y ecrire), et jamais suivi
# s'il devient un lien.
empreinte_notee() {
  [ -f "$EMPREINTES" ] && [ ! -L "$EMPREINTES" ] || return 0
  awk -v r="$1" '$2 == r { print $1; exit }' "$EMPREINTES" 2>/dev/null
}

noter_empreinte() {
  # Pas de refus si le fichier de notes est un lien : le "mv" final remplace
  # le LIEN, il n'ecrit jamais a travers. Refuser serait meme nuisible --
  # le lien resterait, et la prise de notes s'arreterait pour toujours ;
  # n'importe qui pouvant ecrire dans le workspace desactiverait le
  # mecanisme avec un simple "ln -s". En le remplacant, on se repare.
  mkdir -p "$(dirname "$EMPREINTES")" 2>/dev/null || return 0
  # mktemp plutot qu'un nom previsible : ce code tourne en root dans un
  # dossier inscriptible par le groupe, un temporaire devinable serait une
  # ecriture root detournable par un lien pose d'avance.
  n=$(mktemp "$(dirname "$EMPREINTES")/.empreintes-XXXXXX" 2>/dev/null) || return 0
  if [ -f "$EMPREINTES" ]; then
    awk -v r="$1" '$2 != r' "$EMPREINTES" > "$n" 2>/dev/null || true
  fi
  echo "$2 $1" >> "$n"
  chgrp "$CODELAB_GROUP" "$n" 2>/dev/null || true
  chmod 644 "$n" 2>/dev/null || true
  mv "$n" "$EMPREINTES" 2>/dev/null || rm -f "$n"
}

if [ -d "$WORKSPACE_SEED" ] && [ -f "$SEED_MARKER" ]; then
  liste=$(mktemp)
  ( cd "$WORKSPACE_SEED" && find . -type f -printf '%P\n' ) > "$liste" 2>/dev/null || true

  majs=0
  gardes=0
  while IFS= read -r relatif; do
    [ -n "$relatif" ] || continue
    source="$WORKSPACE_SEED/$relatif"
    cible="$WORKSPACE_DIR/$relatif"

    # Jamais a travers un lien symbolique. Ce bloc tourne EN ROOT, avant
    # l'abandon des privileges, et /workspace est inscriptible par les
    # sessions SSH comme par les applications du panneau. Suivre un lien
    # reviendrait a offrir a n'importe laquelle d'entre elles une ecriture
    # root n'importe ou sur le disque. Le squelette ne livre aucun lien : un
    # lien a cet emplacement est soit un choix de l'utilisateur, soit une
    # attaque -- dans les deux cas on passe son chemin.
    if [ -L "$cible" ]; then
      echo "[codelab]   $relatif est un lien symbolique : laisse tel quel." >&2
      continue
    fi

    # Absent : c'est soit un ajout du squelette posterieur a l'amorcage, soit
    # une suppression deliberee. On ne peut pas distinguer les deux, et se
    # tromper dans un sens fait reapparaitre un projet supprime. On s'abstient.
    [ -f "$cible" ] || continue

    somme_disque=$(sha256sum < "$cible" | cut -d' ' -f1)
    somme_image=$(sha256sum < "$source" | cut -d' ' -f1)

    if [ "$somme_disque" = "$somme_image" ]; then
      # Deja a jour. On note quand meme si ce n'etait pas encore fait : c'est
      # ce qui fait entrer en douceur une installation existante dans le
      # mecanisme, sans rien ecrire dans le workspace.
      [ "$(empreinte_notee "$relatif")" = "$somme_disque" ] \
        || noter_empreinte "$relatif" "$somme_disque"
      continue
    fi

    # Intact ? Ce que nous avons note pour ce fichier fait foi. A defaut de
    # note -- installation anterieure au mecanisme -- on se rabat sur la
    # liste figee des versions livrees autrefois.
    notee=$(empreinte_notee "$relatif")
    if [ -n "$notee" ]; then
      [ "$notee" = "$somme_disque" ] && intact=oui || intact=non
    elif [ -f "$SEED_SUMS" ] && grep -q "^$somme_disque $relatif\$" "$SEED_SUMS"; then
      intact=oui
    else
      intact=non
    fi

    if [ "$intact" = oui ]; then
      # mktemp, et non un nom de temporaire previsible : il cree le fichier
      # avec O_EXCL, donc sans jamais suivre un lien qu'un tiers aurait pose
      # d'avance a cet emplacement. Un "cp vers $cible.codelab-tmp" aurait
      # ete exactement cette faille, en root.
      tmp=$(mktemp "$(dirname "$cible")/.codelab-maj-XXXXXX" 2>/dev/null) || tmp=""
      if [ -n "$tmp" ] && cat "$source" > "$tmp" 2>/dev/null; then
        chgrp "$CODELAB_GROUP" "$tmp" 2>/dev/null || true
        chmod g+rw "$tmp" 2>/dev/null || true
        # Renommage atomique : un arret au mauvais moment laisse l'ancienne
        # version entiere, jamais un fichier a moitie ecrit.
        mv "$tmp" "$cible"
        noter_empreinte "$relatif" "$somme_image"
        echo "[codelab]   $relatif mis a jour (version d'origine, non modifiee)."
        majs=$((majs + 1))
      else
        [ -n "$tmp" ] && rm -f "$tmp"
        echo "[codelab]   $relatif : mise a jour impossible (droits ?)." >&2
      fi
    else
      # Modifie sur place. C'est le cas normal des que l'utilisateur s'est
      # approprie le projet d'exemple -- on le signale sans insister. On ne
      # note SURTOUT pas son empreinte : le fichier est a lui desormais, et
      # doit le rester meme s'il le remanie encore.
      gardes=$((gardes + 1))
    fi
  done < "$liste"
  rm -f "$liste"

  if [ "$majs" -gt 0 ]; then
    echo "[codelab] squelette : $majs fichier(s) mis a jour depuis l'image."
  fi
  if [ "$gardes" -gt 0 ]; then
    echo "[codelab] squelette : $gardes fichier(s) modifies sur place, laisses" \
         "tels quels (reference dans $WORKSPACE_SEED)."
  fi
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

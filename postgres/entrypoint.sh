#!/bin/sh
# Entrypoint de codelab-postgres.
#
# Genere (ou reprend) le mot de passe Postgres au premier demarrage, l'ecrit
# dans credentials.env -- LE fichier de secrets de CodeLab, aucun autre --
# puis rend la main a l'entrypoint officiel de l'image.
#
# Pourquoi un fichier dans une image plutot qu'un script inline dans le
# docker-compose : Compose interpole les "$VAR" d'un script inline avant que
# le shell ne les voie, et une interface qui reecrit le compose a l'import
# annule l'echappement cense y remedier ("$$" redevenu "$"). Le script
# recoit alors des chaines vides ("mkdir: cannot create directory ''"). Un
# script versionne dans l'image ne traverse jamais ces reecritures -- c'est
# la seule facon fiable d'avoir du shell ici.
set -e

CONFIG_DIR="${CODELAB_CONFIG_DIR:-/var/lib/codelab/config}"
ENV_FILE="$CONFIG_DIR/credentials.env"
LEGACY_PW_FILE="$CONFIG_DIR/postgres_password"

mkdir -p "$CONFIG_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Lit une cle dans credentials.env (chaine vide si absente).
get_value() {
  # Derniere occurrence : upsert_block reecrit toujours son bloc en fin de
  # fichier, donc une valeur laissee plus haut (edition a la main, ancien
  # format sans marqueurs) est forcement la perimee.
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# Remplacement par BLOC entier (commentaires inclus), delimite par des
# marqueurs "# ===== NOM =====" / "# ===== /NOM =====" -- pas juste par
# prefixe de cle, sinon les commentaires documentant chaque bloc
# s'accumuleraient en double a chaque redemarrage. Chaque service ne touche
# qu'a son propre bloc, les autres restent intacts quel que soit l'ordre de
# demarrage.
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

# Ordre de priorite : la valeur deja presente dans credentials.env fait
# autorite (c'est celle avec laquelle la base a ete initialisee), sinon on
# reprend l'ancien fichier postgres_password d'une install anterieure, sinon
# on genere. Ne JAMAIS regenerer par-dessus une valeur existante : la base
# refuserait la connexion.
PG_PASSWORD="$(get_value POSTGRES_PASSWORD)"
MIGRATED=0
if [ -z "$PG_PASSWORD" ] && [ -s "$LEGACY_PW_FILE" ]; then
  PG_PASSWORD="$(cat "$LEGACY_PW_FILE")"
  MIGRATED=1
fi
if [ -z "$PG_PASSWORD" ]; then
  PG_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "[codelab-postgres] mot de passe genere."
fi

# ------------------- noms des bases (voir le bloc plus bas) -------------------
# Definis ici parce que credentials.env, ecrit juste apres, publie le nom de
# la base d'instance a tous les autres services.
#
# Base d'INSTANCE de Dagster : runs, evenements, planifications. C'est
# POSTGRES_DB, donc l'image officielle la cree elle-meme a l'initialisation
# du cluster.
CODELAB_INSTANCE_DB="${POSTGRES_DB:-dagster}"
# Bases de projet creees d'office. Liste separee par des espaces : un projet
# ajoute ici est provisionne au prochain redemarrage de la stack. Les projets
# crees en cours de route passent plutot par "codelab db <nom>" depuis
# une session SSH, qui fait exactement la meme chose sans redemarrage.
CODELAB_PROJECT_DBS="${CODELAB_PROJECT_DBS:-diagnostic}"
# Schema qui recoit les tables des assets, dans CHAQUE base de projet. Meme
# nom partout : c'est ce qui permet de copier un projet d'une base a l'autre
# sans toucher aux requetes.
CODELAB_PROJECT_SCHEMA="${CODELAB_PROJECT_SCHEMA:-dagster}"

# ---------------------- le compte du service LLM ----------------------
#
# Un ROLE A LUI, et un mot de passe a lui. Tous les autres services se
# connectent avec le superutilisateur "codelab" -- c'est deja beaucoup, et
# c'est un vieux sujet -- mais codelab-llm est celui qui embarque le plus de
# code tiers et qui parle a l'exterieur. Lui donner le superutilisateur
# reviendrait a offrir toute la stack au premier paquet compromis.
#
# Son mot de passe ne vit pas dans credentials.env : ce fichier porte le mot
# de passe d'administration du panneau, la cle privee SSO et le mot de passe
# SMTP, et codelab-llm n'a aucune raison de les voir. Il recoit un dossier a
# lui, avec le seul secret dont il a besoin.
CODELAB_LLM_DB="${CODELAB_LLM_DB:-codelab_llm}"
CODELAB_LLM_USER="${CODELAB_LLM_USER:-codelab_llm}"
LLM_DIR="$CONFIG_DIR/llm"
LLM_ENV_FILE="$LLM_DIR/llm.env"

# Retire un bloc entier, marqueurs compris. Pour les blocs que plus aucun
# service n'ecrit : un bloc sans valeur n'est que du bruit a relire.
remove_block() {
  name="$1"
  awk -v s="# ===== $name =====" -v e="# ===== /$name =====" \
    '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
}

# Comme upsert_block, mais EN TETE du fichier : c'est le sommaire, il doit se
# lire en premier. upsert_block ajoute en fin de fichier, et le panneau
# reecrit ses blocs apres ce service -- l'en-tete finissait au milieu.
header_block() {
  content="$1"
  awk -v s="# ===== codelab-header =====" -v e="# ===== /codelab-header =====" \
    '$0==s{skip=1} !skip{print} $0==e{skip=0}' "$ENV_FILE" > "$ENV_FILE.tmp"
  {
    echo "# ===== codelab-header ====="
    printf '%s\n' "$content" | sed 's/^[[:space:]]*//'
    echo "# ===== /codelab-header ====="
    cat "$ENV_FILE.tmp"
  } > "$ENV_FILE.new"
  mv "$ENV_FILE.new" "$ENV_FILE"
  rm -f "$ENV_FILE.tmp"
}

header_block "# credentials.env -- les identifiants des services CodeLab.
#
# Genere par les services eux-memes : rien a y saisir pour que la stack
# tourne. Chaque bloc « ===== nom ===== » appartient a UN service, qui le
# reecrit en entier a son demarrage -- une retouche a la main dans un de ces
# blocs est perdue. Un bloc ajoute sous un autre nom est conserve.
# Fichier 0600 root : ne pas le copier, il porte plusieurs secrets.
#
#   bloc                 ecrit par          sert a                     transmis aux apps
#   codelab-postgres     codelab-postgres   base partagee (Postgres)   oui
#   codelab-app-manager  panneau            connexion au panneau       NON
#   codelab-sso          panneau            identite des applications  cle publique seule
#   codelab-alertes      panneau            envoi des mails d'alerte   NON
#
# « Transmis aux apps » : ce que le panneau passe, par l'environnement, aux
# applications qu'il lance. Le .env d'un projet l'emporte toujours.
#
# Ailleurs, et c'est voulu :
#   config/llm/llm.env   la cle du fournisseur IA -- seul codelab-llm la lit
#   config/ssh/          les cles SSH autorisees et les cles hote
#   Dagster n'a pas d'identifiant a lui : il reprend la session du panneau."

upsert_block "codelab-postgres" "# Serveur Postgres partage par tous les services CodeLab.
# Utilise par : codelab-dev, codelab-dagster, codelab-dagster-daemon,
# qui lisent POSTGRES_PASSWORD ici meme (plus de fichier dedie).
#
# POSTGRES_DB est la base d'INSTANCE de Dagster (runs, evenements,
# planifications) -- pas une base fourre-tout. Les donnees d'un projet
# vont dans la base du projet (schema \"dagster\"), dont le nom est celui
# du dossier ; un projet le redit dans son .env avec CODELAB_DB.
POSTGRES_HOST=codelab-postgres
POSTGRES_PORT=5432
POSTGRES_DB=$CODELAB_INSTANCE_DB
POSTGRES_USER=codelab
POSTGRES_PASSWORD=$PG_PASSWORD"

# Plus de bloc « codelab-dev » : il ne portait aucune valeur, seulement une
# phrase sur SSH, que le sommaire ci-dessus reprend.
remove_block "codelab-dev"

chmod 600 "$ENV_FILE"

# ------------------- le dossier de secrets de codelab-llm -------------------
#
# Cree ICI et pas ailleurs, parce que ce conteneur demarre le premier (tous
# les autres ont depends_on: service_healthy). Un DOSSIER monte, pas un
# fichier : Docker cree un dossier vide a la place d'un fichier absent, et la
# stack partirait avec un "llm.env/" que personne ne saurait lire.
# Le mot de passe ne se lit QUE dans llm.env : le mettre aussi dans
# credentials.env annulerait la separation qu'on vient d'etablir.
LLM_PASSWORD=""
if [ -s "$LLM_ENV_FILE" ]; then
  LLM_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$LLM_ENV_FILE" | tail -n 1)"
fi
if [ -z "$LLM_PASSWORD" ]; then
  LLM_PASSWORD="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
mkdir -p "$LLM_DIR"
chmod 700 "$LLM_DIR"
# A CODELAB-LLM, PAS A ROOT. La passerelle tourne sous un utilisateur dedie
# (uid 1003, voir llm/Dockerfile) et sans aucune capacite : un dossier 0700
# et un fichier 0600 appartenant a root lui etaient illisibles. Elle lisait
# alors un fichier « vide » sans erreur -- mot de passe Postgres vide, cle du
# fournisseur vide -- et chaque appel finissait en « fe_sendauth: no password
# supplied ». Le panneau, qui reecrit ce fichier, reprend ce proprietaire.
CODELAB_LLM_UID=1003
chown "$CODELAB_LLM_UID" "$LLM_DIR"

# La cle du fournisseur : elle arrive par l'environnement du compose (fichier
# .env de l'hote) ou reste celle deja posee ici. Jamais copiee depuis
# credentials.env -- l'interet du dossier serait perdu.
LLM_PROVIDER_KEY="${OPENROUTER_API_KEY:-}"
if [ -z "$LLM_PROVIDER_KEY" ] && [ -s "$LLM_ENV_FILE" ]; then
  LLM_PROVIDER_KEY="$(sed -n 's/^OPENROUTER_API_KEY=//p' "$LLM_ENV_FILE" | tail -n 1)"
fi

# Les reglages poses a la main dans ce fichier -- un autre fournisseur, un
# autre modele derriere un nom logique -- survivent a la reecriture. Seuls
# ceux-la : le reste est regenere, et CODELAB_LLM_AMONT_KEY (la liaison avec
# le routeur retire) disparait avec lui.
LLM_REGLAGES=""
if [ -s "$LLM_ENV_FILE" ]; then
  LLM_REGLAGES="$(grep -E '^(CODELAB_LLM_FOURNISSEUR|CODELAB_LLM_MODELE_[A-Z0-9_]+)=' "$LLM_ENV_FILE" || true)"
fi

cat > "$LLM_ENV_FILE.new" <<LLMENV
# llm.env -- les reglages du service IA (codelab-llm), et rien d'autre.
#
# Lu par codelab-llm SEUL, a chaque appel : une valeur changee ici prend
# effet sans redemarrage. Ni les applications ni le poste de developpement
# ne voient ce fichier -- ils presentent une cle CodeLab, pas celle-ci.
#
# Ecrit par codelab-postgres au demarrage (POSTGRES_*, et la cle venue du
# .env de l'hote s'il en porte une) et par le panneau (Parametres > Cles et
# secrets). Une cle deja posee n'est jamais effacee par une valeur vide.
#
# A la main, et conserve aux redemarrages :
#   CODELAB_LLM_MODELE_<NOM>=...   le modele reel derriere un nom logique
#   CODELAB_LLM_FOURNISSEUR=...    un autre fournisseur compatible OpenAI
POSTGRES_HOST=codelab-postgres
POSTGRES_PORT=5432
POSTGRES_USER=$CODELAB_LLM_USER
POSTGRES_PASSWORD=$LLM_PASSWORD
POSTGRES_DB=$CODELAB_LLM_DB
OPENROUTER_API_KEY=$LLM_PROVIDER_KEY
LLMENV
[ -n "$LLM_REGLAGES" ] && printf '%s\n' "$LLM_REGLAGES" >> "$LLM_ENV_FILE.new"
mv "$LLM_ENV_FILE.new" "$LLM_ENV_FILE"
chmod 600 "$LLM_ENV_FILE"
chown "$CODELAB_LLM_UID" "$LLM_ENV_FILE"

# L'ancien fichier du routeur retire (codelab-litellm). Plus personne ne le
# lit ; on le supprime pour qu'aucune cle de fournisseur ne dorme sur le
# disque sans usage. Un dossier vide cree par Docker a sa place part aussi.
rm -f "$LLM_DIR/fournisseurs.env" 2>/dev/null || rmdir "$LLM_DIR/fournisseurs.env" 2>/dev/null || true

# Migration terminee et ecrite : l'ancien fichier n'a plus de raison
# d'exister. Supprime seulement apres coup, jamais avant.
if [ "$MIGRATED" = "1" ]; then
  rm -f "$LEGACY_PW_FILE"
  echo "[codelab-postgres] postgres_password migre vers credentials.env puis supprime."
fi

# L'image officielle refuse POSTGRES_PASSWORD et POSTGRES_PASSWORD_FILE
# simultanement : on ne passe que la variable, lue du fichier unique.
unset POSTGRES_PASSWORD_FILE
POSTGRES_PASSWORD="$PG_PASSWORD"
export POSTGRES_PASSWORD

# ---------------------- bases de donnees de la stack ----------------------
#
# CodeLab n'a pas de base fourre-tout. Il y a :
#
#   - "dagster"     : les tables d'instance de Dagster (runs, evenements,
#                     planifications). C'est POSTGRES_DB, donc l'image
#                     officielle la cree elle-meme a l'initialisation.
#   - une base PAR PROJET, nommee comme le dossier du projet ("diagnostic"
#                     pour celui livre en modele), avec un schema "dagster"
#                     dedans : les tables produites par les assets y
#                     atterrissent sans que les pipelines aient a prefixer
#                     quoi que ce soit.
#
# La base "postgres", creee par initdb, est SUPPRIMEE : aucun service CodeLab
# n'y ecrit, et le role de base de maintenance -- celle a laquelle on se
# connecte pour en creer une autre -- est tenu par la base d'instance, qui
# existe toujours. Voir drop_maintenance_db plus bas pour les garde-fous.
#
# Pourquoi pas /docker-entrypoint-initdb.d : ce dossier n'est joue qu'a la
# toute premiere initialisation du cluster. Les donnees survivent aux
# reinstallations, donc une stack existante ne le rejouerait jamais, et un
# projet ajoute plus tard n'aurait pas sa base. Ce bloc-ci tourne a CHAQUE
# demarrage et ne fait rien quand tout est deja en place.

# CREATE DATABASE n'accepte pas IF NOT EXISTS, d'ou le test prealable.
# La connexion passe par la socket Unix locale, en "trust" (initdb --auth-local),
# donc pas de mot de passe a fournir ici.
create_db_if_missing() {
  db="$1"
  if [ "$(psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -tAc \
          "SELECT 1 FROM pg_database WHERE datname='$db'")" = "1" ]; then
    return 0
  fi
  psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -c "CREATE DATABASE \"$db\"" >/dev/null
  echo "[codelab-postgres] base \"$db\" creee."
}

# Supprime la base "postgres" livree par initdb. CodeLab ne s'en sert pas : la
# base d'instance sert de point d'entree pour creer les autres, le healthcheck
# et "codelab db" la visent aussi.
#
# Deux garde-fous, parce qu'une base supprimee ne revient pas :
#   - on ne touche a rien si elle contient le moindre objet utilisateur
#     (quelqu'un a pu y ranger des donnees avant cette version) ;
#   - un echec est non fatal, notamment si une session y est encore connectee.
#     Le prochain demarrage reessaiera.
#
# Consequence a connaitre : les outils qui se connectent a "postgres" par
# defaut (psql sans -d depuis un autre conteneur, pgAdmin, createdb) doivent
# desormais nommer une base explicitement.
drop_maintenance_db() {
  if [ "$(psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -tAc \
          "SELECT 1 FROM pg_database WHERE datname='postgres'")" != "1" ]; then
    return 0
  fi

  # Tables, vues, sequences... hors catalogues systeme. Zero = base laissee
  # telle que initdb l'a creee, donc supprimable sans rien perdre.
  objets="$(psql -U "$POSTGRES_USER" -d postgres -tAc \
    "SELECT count(*) FROM pg_class c
       JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
        AND n.nspname NOT LIKE 'pg_toast%'
        AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')" 2>/dev/null)"
  if [ -z "$objets" ]; then
    echo "[codelab-postgres] base \"postgres\" illisible : conservee." >&2
    return 0
  fi
  if [ "$objets" != "0" ]; then
    echo "[codelab-postgres] base \"postgres\" non vide ($objets objet(s)) :" \
         "conservee. La vider ou la supprimer a la main si elle ne sert plus." >&2
    return 0
  fi

  if psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" \
       -c "DROP DATABASE postgres" >/dev/null 2>&1; then
    echo "[codelab-postgres] base de maintenance \"postgres\" supprimee."
  else
    echo "[codelab-postgres] suppression de la base \"postgres\" impossible" \
         "(session encore connectee ?) : nouvelle tentative au prochain demarrage." >&2
  fi
}

# Provisionnement en tache de fond : le serveur doit d'abord ecouter, et
# c'est "exec docker-entrypoint.sh" plus bas qui le demarre. Toute erreur
# ici est non fatale -- une base de projet manquante se rattrape avec
# "codelab db", alors qu'un Postgres qui ne demarre pas ne se rattrape
# pas du tout.
provision_databases() {
  # Attente sur TCP, pas sur la socket Unix : a la toute premiere
  # initialisation, l'entrypoint officiel demarre un serveur TEMPORAIRE en
  # "listen_addresses = ''" pour creer le role et la base, puis l'arrete. Ce
  # serveur-la repond deja sur la socket ; provisionner (et surtout supprimer
  # une base) pendant qu'il tourne s'inserait au milieu de son initialisation.
  # Le port TCP n'ouvre qu'avec le vrai serveur.
  attente_hote=127.0.0.1
  attente_port="${PGPORT:-5432}"
  i=0
  while [ "$i" -lt 120 ]; do
    if pg_isready -h "$attente_hote" -p "$attente_port" \
                  -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -q; then
      break
    fi
    i=$((i + 1))
    sleep 1
  done
  if ! pg_isready -h "$attente_hote" -p "$attente_port" \
                  -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -q; then
    echo "[codelab-postgres] serveur injoignable apres 120 s : bases de projet" \
         "non provisionnees." >&2
    return 1
  fi

  for db in $CODELAB_PROJECT_DBS; do
    create_db_if_missing "$db" || continue
    # Le schema d'abord, le search_path ensuite. ALTER ROLE ... IN DATABASE
    # est ce qui fait qu'un "CREATE TABLE ma_table" dans un asset atterrit
    # dans le schema du projet, sans prefixe dans le code.
    psql -U "$POSTGRES_USER" -d "$db" >/dev/null <<SQL || true
CREATE SCHEMA IF NOT EXISTS "$CODELAB_PROJECT_SCHEMA";
ALTER ROLE "$POSTGRES_USER" IN DATABASE "$db"
  SET search_path TO "$CODELAB_PROJECT_SCHEMA", public;
SQL
    # pgvector, dans le schema « public » et non dans celui du projet : une
    # extension posee dans un schema qui peut etre supprime disparait avec
    # lui. public est dans le search_path de toutes les bases, donc le type
    # « vector » se nomme sans prefixe depuis le code du projet.
    #
    # Separe du bloc ci-dessus, et sans "|| true" partage : si l'extension
    # manque dans l'image, on veut le lire dans les journaux au demarrage,
    # pas le decouvrir au premier CREATE TABLE.
    if ! psql -U "$POSTGRES_USER" -d "$db" >/dev/null 2>&1 \
         -c 'CREATE EXTENSION IF NOT EXISTS vector SCHEMA public'; then
      echo "[codelab-postgres] pgvector indisponible pour \"$db\" :" \
           "le type vector ne sera pas utilisable." >&2
    fi
  done

  # La base du service LLM et SON role, proprietaire de cette base et de
  # rien d'autre. CREATE ROLE n'accepte pas IF NOT EXISTS : on regarde
  # d'abord, et on repose le mot de passe a chaque demarrage -- c'est ce qui
  # permet de le regenerer en effacant simplement le dossier llm/.
  if [ "$(psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -tAc \
          "SELECT 1 FROM pg_roles WHERE rolname='$CODELAB_LLM_USER'")" != "1" ]; then
    psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" >/dev/null \
      -c "CREATE ROLE \"$CODELAB_LLM_USER\" LOGIN" && \
      echo "[codelab-postgres] role \"$CODELAB_LLM_USER\" cree."
  fi
  psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" >/dev/null 2>&1 \
    -c "ALTER ROLE \"$CODELAB_LLM_USER\" WITH PASSWORD '$LLM_PASSWORD'" || true
  if create_db_if_missing "$CODELAB_LLM_DB"; then
    psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" >/dev/null 2>&1 \
      -c "ALTER DATABASE \"$CODELAB_LLM_DB\" OWNER TO \"$CODELAB_LLM_USER\"" || true
  fi

  # En dernier : les bases de la stack existent, plus rien n'a besoin de la
  # base livree par initdb.
  drop_maintenance_db
}

# --------------------------- LES SAUVEGARDES ---------------------------
#
# Elles tournent ICI, et nulle part ailleurs, pour une raison qui n'est pas
# un choix d'organisation : pg_dump refuse de sauvegarder un serveur PLUS
# RECENT que lui. Le panneau tourne sur une image Debian dont le client
# Postgres a une version de retard sur le serveur -- il ne pourrait pas. Ce
# conteneur, lui, porte le pg_dump de la version exacte du serveur, et parle
# a la base par la socket locale : pas de mot de passe a promener, pas de
# depot apt tiers a ajouter dans l'image qui detient les secrets.
#
# CE QUI EST SAUVEGARDE : toutes les bases, une par une, au format custom
# (-Fc) -- celui qui permet de restaurer une seule table si besoin. L'etat du
# panneau (comptes, applications declarees, journal) est sauvegarde de son
# cote, par codelab-app-manager, qui est le seul a voir ces fichiers.
#
# CHAQUE SAUVEGARDE EST VERIFIEE avant d'etre gardee : pg_restore --list la
# relit entierement. Un fichier tronque par un disque plein est ecarte sur
# place, et l'ancienne sauvegarde reste -- une sauvegarde qu'on decouvre
# illisible le jour de la restauration ne vaut pas mieux que pas de
# sauvegarde.
#
# RESTAURATION : voir postgres/README.md.
SAUVEGARDES_DIR="${CODELAB_SAUVEGARDES_DIR:-/sauvegardes}"
SAUVEGARDE_HEURES="${CODELAB_SAUVEGARDE_HEURES:-24}"
SAUVEGARDES_GARDEES="${CODELAB_SAUVEGARDES_GARDEES:-7}"

sauvegarder_une_base() {
  db="$1"
  horodatage=$(date -u +%Y%m%d-%H%M%S)
  fichier="$SAUVEGARDES_DIR/base-$db-$horodatage.dump"
  if ! pg_dump -U "$POSTGRES_USER" -d "$db" -Fc -f "$fichier" 2>/dev/null; then
    echo "[codelab-postgres] sauvegarde de $db : echec du pg_dump" >&2
    rm -f "$fichier"
    return 1
  fi
  # La verification. Sans elle, on ne saurait qu'a la restauration.
  if ! pg_restore --list "$fichier" >/dev/null 2>&1; then
    echo "[codelab-postgres] sauvegarde de $db : fichier illisible, ecarte" >&2
    rm -f "$fichier"
    return 1
  fi
  chmod 600 "$fichier"
  # Rotation : on ne garde que les N plus recentes DE CETTE BASE. Le tri est
  # sur le nom, et le nom porte la date en AAAAMMJJ-HHMMSS -- donc l'ordre
  # alphabetique est l'ordre chronologique, y compris apres un changement
  # d'annee.
  ls -1 "$SAUVEGARDES_DIR/base-$db-"*.dump 2>/dev/null \
    | sort -r | tail -n +$((SAUVEGARDES_GARDEES + 1)) \
    | while read -r vieux; do rm -f "$vieux"; done
  return 0
}

sauvegardes_periodiques() {
  if [ "$SAUVEGARDE_HEURES" = "0" ]; then
    echo "[codelab-postgres] sauvegardes desactivees (CODELAB_SAUVEGARDE_HEURES=0)."
    return 0
  fi
  # Le dossier n'existe que si le volume est monte. Sans lui, ecrire ici
  # remplirait la couche du conteneur et tout disparaitrait au premier
  # docker compose down -- une sauvegarde qui s'efface avec ce qu'elle
  # sauvegarde est pire que rien, parce qu'on croit en avoir une.
  if [ ! -d "$SAUVEGARDES_DIR" ]; then
    echo "[codelab-postgres] pas de volume de sauvegarde monte sur" \
         "$SAUVEGARDES_DIR : aucune sauvegarde ne sera faite." >&2
    return 0
  fi
  chmod 700 "$SAUVEGARDES_DIR" 2>/dev/null || true

  while :; do
    # On attend que le serveur reponde avant le premier tour : au demarrage
    # de la stack, il ne repond pas encore.
    if pg_isready -q -U "$POSTGRES_USER" 2>/dev/null; then
      # -d "$CODELAB_INSTANCE_DB" et non -d postgres : la base de
      # maintenance est justement SUPPRIMEE par drop_maintenance_db plus
      # haut. La requete echouait donc, l'erreur partait dans /dev/null,
      # la liste revenait vide -- et la boucle annoncait fierement
      # « sauvegarde : 0 base(s) ecrite(s) » a chaque tour. Une
      # sauvegarde qui ne sauvegarde rien sans le dire est pire que pas
      # de sauvegarde du tout.
      bases=$(psql -U "$POSTGRES_USER" -d "$CODELAB_INSTANCE_DB" -Atc \
        "SELECT datname FROM pg_database
          WHERE NOT datistemplate AND datallowconn ORDER BY datname" 2>/dev/null)
      faites=0
      for db in $bases; do
        sauvegarder_une_base "$db" && faites=$((faites + 1))
      done
      echo "[codelab-postgres] sauvegarde : $faites base(s) ecrite(s) dans" \
           "$SAUVEGARDES_DIR"
    fi
    sleep $((SAUVEGARDE_HEURES * 3600))
  done
}

provision_databases &
sauvegardes_periodiques &

exec docker-entrypoint.sh "$@"

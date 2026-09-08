#!/bin/bash
# Entrypoint de codelab-dev. Six roles :
#
#   1. Cles hote SSH persistantes. Elles sont regenerees par apt au moment du
#      build : sans les persister, elles changent a chaque recreation du
#      conteneur (reinstall, mise a jour d'image), ce qui fait echouer VS Code
#      Remote-SSH (MitmPortForwardingDisabled). Generees une seule fois dans
#      CODELAB_SSH_DIR/host_keys, recopiees vers /etc/ssh a chaque demarrage.
#
#   2. Cles autorisees, une par machine. CODELAB_SSH_DIR/authorized_keys.d/
#      contient un fichier "<nom>.pub" par ordinateur ; authorized_keys est
#      reconstruit a partir de ce dossier au demarrage. Autoriser une
#      machine = y deposer un fichier puis redemarrer le conteneur ; lui
#      retirer l'acces = supprimer ce fichier. SSH_PUBLIC_KEY n'est qu'une
#      source parmi d'autres et devient optionnelle une fois une cle
#      enregistree : la stack redemarre sans elle sans perdre l'acces.
#
#   3. Identifiants Postgres dans les shells SSH. Un sshd ne fait pas heriter
#      ses sessions de l'environnement du process qui l'a lance (comportement
#      OpenSSH normal), donc on les ecrit dans /etc/profile.d (shells de
#      login) et on source ce fichier depuis ~/.bashrc (shells interactifs
#      non-login, dont Remote-SSH de VS Code).
#
#   4. Environnement Codex. CODEX_HOME pointe sur /workspace/.codex, cree ici
#      (Codex ne cree pas ce dossier et refuse de demarrer s'il manque) :
#      l'authentification et le config.toml survivent ainsi a une recreation
#      du conteneur, alors qu'ils seraient perdus sous /home/vscode.
#
#   5. Permissions du workspace. /workspace est partage avec Dagster et
#      app-manager, qui y ecrivent en root. Le socle (groupe commun, setgid,
#      umask 002) est pose plus bas.
#
#   6. Ne jamais bloquer le demarrage. Ce service n'a volontairement pas de
#      "depends_on: service_healthy" (certains orchestrateurs laissent le
#      conteneur en "Created" si Postgres tarde a l'installation), donc
#      credentials.env peut ne pas
#      encore exister au premier boot : on l'attend brievement, puis on
#      demarre quand meme. SSH reste utilisable sans la base.
set -e

# Herite par sshd et par tout ce que cet entrypoint lance. Sans cet umask,
# le bit setgid pose sur /workspace donne le bon GROUPE aux fichiers crees,
# mais en lecture seule pour ce groupe (0644) -- les autres services ne
# pourraient toujours pas les modifier.
umask 002

SSH_USER="${CODELAB_SSH_USER:-vscode}"
SSH_DIR="${CODELAB_SSH_DIR:-/var/lib/codelab/ssh}"
HOST_KEYS_DIR="$SSH_DIR/host_keys"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"
KEYS_D="$SSH_DIR/authorized_keys.d"
# Empreinte de la derniere reconstruction, utilisee pour distinguer deux
# lignes d'authorized_keys qui se ressemblent : une cle ajoutee a la main
# apres coup (a recuperer) et une cle que NOUS avons ecrite au demarrage
# precedent puis retiree du dossier depuis (a laisser mourir). Sans elle,
# supprimer un .pub ne retirerait rien -- la cle serait reprise pour une
# ajoutee a la main au demarrage suivant.
DERIVED_SNAPSHOT="$SSH_DIR/.derived-keys"
ENV_FILE="${CODELAB_ENV_FILE:-/var/lib/codelab/config/credentials.env}"
PROFILE=/etc/profile.d/codelab-pg.sh
UMASK_PROFILE=/etc/profile.d/codelab-umask.sh
CODEX_PROFILE=/etc/profile.d/codelab-codex.sh
BASHRC=/home/vscode/.bashrc

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
    echo "[codelab-dev] premiere passe de permissions sur $WORKSPACE_DIR..."
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

# Ceinture et bretelles avec UMASK dans /etc/login.defs : couvre les shells
# de login et les shells interactifs, y compris quand un ~/.profile
# utilisateur reimpose un umask plus restrictif apres coup.
printf '%s\n' '# Genere par codelab-dev -- ecriture partagee du workspace.' 'umask 002' > "$UMASK_PROFILE"
chmod 644 "$UMASK_PROFILE"

# ------------------------------ Codex ------------------------------
#
# CODEX_HOME dans /workspace, seul dossier qui survit a une recreation du
# conteneur : sans lui, Codex range son auth.json sous /home/vscode et il
# faut refaire "codex login" apres chaque mise a jour d'image. Le dossier est
# cree ici parce que Codex ne le cree pas lui-meme et refuse de demarrer si
# le chemin n'existe pas.
CODEX_HOME_DIR="${CODEX_HOME:-$WORKSPACE_DIR/.codex}"
mkdir -p "$CODEX_HOME_DIR"
chown "$SSH_USER" "$CODEX_HOME_DIR" 2>/dev/null || true
chgrp "$CODELAB_GROUP" "$CODEX_HOME_DIR" 2>/dev/null || true
chmod 2770 "$CODEX_HOME_DIR" 2>/dev/null || true

# config.toml par defaut, ecrit une seule fois : jamais reecrit ensuite, les
# reglages faits a la main depuis une session survivent aux redemarrages.
#
# "danger-full-access" est un choix, pas une negligence : sans lui Codex
# tente de s'isoler avec bubblewrap, qui exige des user namespaces
# indisponibles dans un conteneur non privilegie ("bwrap: No permissions to
# create a new namespace") et aucune commande ne s'execute. L'isolation est
# deja celle du conteneur ; en empiler une seconde ne fait que tout bloquer.
if [ ! -f "$CODEX_HOME_DIR/config.toml" ]; then
    {
        echo '# Ecrit une seule fois par codelab-dev -- modifiable librement.'
        echo 'sandbox_mode = "danger-full-access"'
        echo 'approval_policy = "on-request"'
    } > "$CODEX_HOME_DIR/config.toml"
    chown "$SSH_USER" "$CODEX_HOME_DIR/config.toml" 2>/dev/null || true
fi

printf '%s\n' '# Genere par codelab-dev -- authentification Codex persistante.' \
    "export CODEX_HOME=$CODEX_HOME_DIR" > "$CODEX_PROFILE"
chmod 644 "$CODEX_PROFILE"

# Manuel de l'environnement, lu par Codex comme instructions globales. Recopie
# a CHAQUE demarrage, contrairement au config.toml : ce fichier decrit la
# stack, pas les preferences de l'utilisateur, et une version perimee
# donnerait a l'agent des consignes fausses. Les consignes personnelles vont
# dans le AGENTS.md du projet, jamais ici.
#
# Toutes les versions de la CLI n'honorent pas ce niveau global : la commande
# "codelab agents" recopie le meme manuel dans le AGENTS.md d'un projet, ou il
# est lu a coup sur.
AGENTS_SOURCE=/usr/local/share/codelab/AGENTS.md
if [ -r "$AGENTS_SOURCE" ]; then
    cp -f "$AGENTS_SOURCE" "$CODEX_HOME_DIR/AGENTS.md"
    chown "$SSH_USER" "$CODEX_HOME_DIR/AGENTS.md" 2>/dev/null || true
    chmod 664 "$CODEX_HOME_DIR/AGENTS.md" 2>/dev/null || true
fi

# ------------------------------ cles SSH ------------------------------

mkdir -p "$HOST_KEYS_DIR"
# Droits differencies, et c'est LE point delicat de ce fichier : sshd lit les
# cles hote en root, mais il ouvre authorized_keys apres avoir pris l'uid de
# l'utilisateur cible (temporarily_use_uid). Un dossier 700 root:root donne
# donc "Could not open user 'vscode' authorized keys: Permission denied", et
# un refus cote client reduit a "Permission denied (publickey)", sans autre
# explication. Le dossier doit rester traversable par cet utilisateur.
chmod 755 "$SSH_DIR"
chmod 700 "$HOST_KEYS_DIR"

# Cles hote : generees une seule fois, jamais regenerees ensuite. C'est ce
# qui garantit que l'empreinte du serveur ne change pas d'une recreation de
# conteneur a l'autre (sinon VS Code Remote-SSH echoue en
# MitmPortForwardingDisabled et ssh refuse la connexion).
#
# La condition n'est PAS "le fichier existe" mais "le fichier est une cle
# valide" : un fichier de taille nulle ou tronque -- copie interrompue,
# disque plein, ecriture coupee par un arret brutal -- passerait le test
# d'existence, ferait echouer le demarrage de sshd, et l'on repartirait
# alors sur des cles neuves, c'est-a-dire sur une empreinte modifiee.
for t in rsa ecdsa ed25519; do
    key="$HOST_KEYS_DIR/ssh_host_${t}_key"
    if [ -f "$key" ] && ssh-keygen -lf "$key" >/dev/null 2>&1; then
        continue
    fi
    if [ -e "$key" ]; then
        echo "[codelab-dev] cle hote $t illisible ou corrompue : regeneration" \
             "(l'empreinte de cette cle change)."
        rm -f "$key" "$key.pub"
    fi
    ssh-keygen -q -t "$t" -f "$key" -N ''
    echo "[codelab-dev] cle hote $t generee dans $HOST_KEYS_DIR."
done

# Droits refaits a chaque demarrage, sur le volume ET sur les copies. Un
# bind mount peut ressortir les fichiers avec les droits de l'hote : sshd
# refuse de demarrer sur une cle privee lisible par d'autres, et un
# redemarrage rate ici se solderait par des cles neuves au boot suivant.
chown root:root "$HOST_KEYS_DIR"/ssh_host_*_key "$HOST_KEYS_DIR"/ssh_host_*_key.pub 2>/dev/null || true
chmod 600 "$HOST_KEYS_DIR"/ssh_host_*_key
chmod 644 "$HOST_KEYS_DIR"/ssh_host_*_key.pub
cp -f "$HOST_KEYS_DIR"/ssh_host_*_key "$HOST_KEYS_DIR"/ssh_host_*_key.pub /etc/ssh/
chmod 600 /etc/ssh/ssh_host_*_key
chmod 644 /etc/ssh/ssh_host_*_key.pub

# ----------------------- cles autorisees par machine -----------------------
#
# authorized_keys.d/ contient un fichier "<nom>.pub" par ordinateur autorise.
# authorized_keys est DERIVE : reconstruit ici a chaque demarrage comme
# l'union de ce dossier. Ajouter une machine, c'est deposer un fichier ; en
# retirer une, c'est en supprimer un. Rien n'est jamais perdu implicitement.
#
# Une cle ecrite directement dans authorized_keys (a la main depuis l'hote,
# ou par une version anterieure de cette image) n'est pas ignoree :
# elle est recuperee dans authorized_keys.d/manuel.pub avant reconstruction.
# Le vieux geste continue donc de fonctionner.

# Identite d'une cle : son type et son corps base64, sans le commentaire.
# Deux lignes qui ne different que par le commentaire ("... lucas@portable"
# vs "... lucas@mac") sont la MEME cle ; sans cette normalisation elles
# s'accumuleraient toutes les deux.
key_ids() {
    awk '{ for (i = 1; i <= NF; i++) if ($i ~ /^(ssh-|ecdsa-|sk-)/) { print $i " " $(i+1); next } }' "$@" 2>/dev/null
}

sync_authorized_keys() {
    mkdir -p "$KEYS_D"
    chmod 755 "$KEYS_D"

    known="$(key_ids "$KEYS_D"/*.pub)"

    # 1. Recuperation des lignes ecrites directement dans authorized_keys.
    if [ -s "$AUTHORIZED_KEYS" ]; then
        recovered=0
        while IFS= read -r line; do
            case "$line" in ''|\#*) continue ;; esac
            id="$(printf '%s\n' "$line" | key_ids)"
            [ -n "$id" ] || continue
            if printf '%s\n' "$known" | grep -qxF "$id"; then continue; fi
            if [ -f "$DERIVED_SNAPSHOT" ] && grep -qxF "$id" "$DERIVED_SNAPSHOT"; then continue; fi
            printf '%s\n' "$line" >> "$KEYS_D/manuel.pub"
            recovered=$((recovered + 1))
        done < "$AUTHORIZED_KEYS"
        if [ "$recovered" -gt 0 ]; then
            echo "[codelab-dev] $recovered cle(s) ecrite(s) a la main recuperee(s) dans manuel.pub."
            known="$(key_ids "$KEYS_D"/*.pub)"
        fi
    fi

    # 2. SSH_PUBLIC_KEY : variable passee au premier demarrage, facultative
    #    une fois la cle enregistree dans le dossier.
    if [ -n "${SSH_PUBLIC_KEY:-}" ]; then
        id="$(printf '%s\n' "$SSH_PUBLIC_KEY" | key_ids)"
        if [ -n "$id" ] && ! printf '%s\n' "$known" | grep -qxF "$id"; then
            printf '%s\n' "$SSH_PUBLIC_KEY" >> "$KEYS_D/compose.pub"
            echo "[codelab-dev] cle de SSH_PUBLIC_KEY enregistree dans compose.pub."
        fi
    fi

    # 3. Reconstruction. Ecriture dans un temporaire puis deplacement : sshd
    #    ne doit jamais tomber sur un authorized_keys tronque, ce qui
    #    refuserait toutes les connexions pendant l'ecriture.
    tmp="$AUTHORIZED_KEYS.tmp.$$"
    snap="$DERIVED_SNAPSHOT.tmp.$$"
    : > "$snap"
    {
        echo "# Fichier DERIVE, reconstruit a chaque demarrage de codelab-dev."
        echo "# Ne pas editer ici : deposer un fichier <machine>.pub dans"
        echo "# authorized_keys.d/, puis redemarrer le conteneur. Une ligne"
        echo "# ajoutee malgre tout ici n'est pas perdue : elle est recuperee"
        echo "# dans authorized_keys.d/manuel.pub au demarrage suivant."
    } > "$tmp"

    count=0
    for f in "$KEYS_D"/*.pub; do
        [ -f "$f" ] || continue
        name="$(basename "$f" .pub)"
        while IFS= read -r line; do
            case "$line" in ''|\#*) continue ;; esac
            id="$(printf '%s\n' "$line" | key_ids)"
            [ -n "$id" ] || continue
            if grep -qxF "$id" "$snap"; then continue; fi
            printf '%s\n' "$id" >> "$snap"
            echo "# $name" >> "$tmp"
            printf '%s\n' "$line" >> "$tmp"
            count=$((count + 1))
        done < "$f"
    done

    mv "$snap" "$DERIVED_SNAPSHOT"
    chmod 600 "$DERIVED_SNAPSHOT"
    mv "$tmp" "$AUTHORIZED_KEYS"
    # Refaits a chaque demarrage : un fichier depose depuis l'hote appartient
    # a root, et sshd (StrictModes) refuse alors de le lire pour le compte de
    # l'utilisateur.
    chown "$SSH_USER:$SSH_USER" "$AUTHORIZED_KEYS" 2>/dev/null || true
    chmod 600 "$AUTHORIZED_KEYS"

    if [ "$count" -eq 0 ]; then
        echo "[codelab-dev] AUCUNE cle autorisee -- aucune connexion SSH ne sera possible."
    else
        echo "[codelab-dev] $count cle(s) autorisee(s) depuis $KEYS_D."
    fi
}

# Non fatal : une erreur ici doit laisser sshd demarrer avec le fichier
# precedent plutot que couper l'acces au conteneur.
sync_authorized_keys || echo "[codelab-dev] reconstruction des cles autorisees en echec :" \
    "authorized_keys reste tel quel."

# Pose la directive au demarrage plutot qu'au build : le chemin vient de
# CODELAB_SSH_DIR, sshd et l'entrypoint ne peuvent donc pas diverger.
sed -i '/^AuthorizedKeysFile /d' /etc/ssh/sshd_config
echo "AuthorizedKeysFile $AUTHORIZED_KEYS" >> /etc/ssh/sshd_config

# -------------------------- identifiants Postgres --------------------------

# Tout ce bloc est une commodite : il pre-remplit PGHOST/PGPASSWORD/... dans
# les shells SSH. Il est appele plus bas de maniere non fatale -- une erreur
# ici ne doit jamais empecher sshd de demarrer, sinon une base indisponible
# couperait aussi l'acces SSH, c'est-a-dire le moyen d'aller la reparer.
configure_pg_profile() {

# Attente bornee : 30 s suffisent largement a codelab-postgres pour ecrire son
# bloc, et si le fichier n'arrive jamais on demarre quand meme sans PGPASSWORD.
for _ in $(seq 1 30); do
    if [ -r "$ENV_FILE" ] && grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE"; then
        break
    fi
    sleep 1
done

PG_PASSWORD=""
if [ -r "$ENV_FILE" ]; then
    # tail : la derniere occurrence fait autorite (bloc reecrit en fin de fichier).
    PG_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | tail -n 1)"
fi
if [ -z "$PG_PASSWORD" ]; then
    echo "[codelab-dev] POSTGRES_PASSWORD introuvable dans $ENV_FILE :" \
         "les shells SSH demarreront sans PGPASSWORD."
fi

{
    echo '# Genere par codelab-dev au demarrage -- ne pas editer.'
    if [ -n "${PGHOST:-}" ]; then echo "export PGHOST=$(printf '%q' "$PGHOST")"; fi
    if [ -n "${PGPORT:-}" ]; then echo "export PGPORT=$(printf '%q' "$PGPORT")"; fi
    if [ -n "${PGDATABASE:-}" ]; then echo "export PGDATABASE=$(printf '%q' "$PGDATABASE")"; fi
    if [ -n "${PGUSER:-}" ]; then echo "export PGUSER=$(printf '%q' "$PGUSER")"; fi
    if [ -n "$PG_PASSWORD" ]; then echo "export PGPASSWORD=$(printf '%q' "$PG_PASSWORD")"; fi
} > "$PROFILE"
chmod 644 "$PROFILE"

# Une seule ligne de source, ajoutee une fois. L'ancienne version concatenait
# le contenu du profil dans .bashrc a chaque demarrage : le fichier grossissait
# d'un jeu d'exports a chaque "docker restart".
touch "$BASHRC"
for f in "$UMASK_PROFILE" "$CODEX_PROFILE" "$PROFILE"; do
    if ! grep -qxF ". $f" "$BASHRC"; then
        printf '\n. %s\n' "$f" >> "$BASHRC"
    fi
done
chown vscode:vscode "$BASHRC"

}

configure_pg_profile || echo "[codelab-dev] identifiants Postgres non pre-remplis" \
    "dans les shells SSH -- le service demarre quand meme."

# ------------------------------- demarrage -------------------------------

mkdir -p /run/sshd
echo "[codelab-dev] pret -- SSH sur le port 22 (2222 depuis l'hote), workspace : $WORKSPACE_DIR"

# sshd devient le processus principal (voir le CMD du Dockerfile). Deux
# consequences qui valent d'etre dites :
#
#   - ses journaux d'authentification arrivent dans "docker logs codelab-dev",
#     y compris les refus. Un "Permission denied (publickey)" cote client ne
#     dit jamais pourquoi ; c'est ici qu'on le lit.
#   - il recoit le SIGTERM de "docker stop" et s'arrete proprement. La version
#     precedente lancait sshd en tache de fond derriere un veilleur Python qui,
#     en PID 1 et sans gestionnaire de signal, ignorait SIGTERM : chaque arret
#     attendait les dix secondes du delai de grace avant un SIGKILL.
exec "$@"

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
#      retirer l'acces = supprimer ce fichier. C'est la SEULE source : il n'y
#      a pas de variable d'environnement qui injecte une cle au passage, donc
#      pas de cle autorisee dont l'origine ne se lise pas dans ce dossier.
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
GITCONFIG=/home/vscode/.gitconfig
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
# tracee par un marqueur. Supprimer /workspace/.codelab/permissions-v2 force
# une reapplication complete au prochain demarrage : c'est la reparation a
# tenter en premier si un fichier resiste.
CODELAB_GROUP="${CODELAB_GROUP:-codelab}"
WORKSPACE_DIR="${WORKSPACE:-/workspace}"
PERM_MARKER="$WORKSPACE_DIR/.codelab/permissions-v2"

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
#
# Le provider pointe la PASSERELLE CodeLab, pas un fournisseur en direct.
# Sans cela, "codex login" envoie le trafic chez OpenAI avec un compte
# personnel : aucune cle centralisee, aucune comptabilite, aucun routage --
# exactement ce que codelab-llm existe pour eviter.
#
# wire_api n'est pas pose : "responses" est le seul protocole accepte depuis
# fevrier 2026, et c'est aussi le defaut. C'est la raison pour laquelle
# codelab-llm expose /v1/responses.
#
# La cle est lue dans l'environnement (env_key), jamais ecrite ici : voir le
# profil plus bas.
if [ ! -f "$CODEX_HOME_DIR/config.toml" ]; then
    cat > "$CODEX_HOME_DIR/config.toml" <<'CODEXCFG'
# Ecrit une seule fois par codelab-dev -- modifiable librement.
sandbox_mode = "danger-full-access"
approval_policy = "on-request"

model_provider = "codelab"
model = "codelab-coding"

[model_providers.codelab]
name = "CodeLab"
base_url = "http://codelab-llm:8080/v1"
env_key = "CODELAB_LLM_KEY"
CODEXCFG
    chown "$SSH_USER" "$CODEX_HOME_DIR/config.toml" 2>/dev/null || true
fi

# LA CLE DE LA PASSERELLE, si elle a ete deposee.
#
# C'est une cle CodeLab -- enregistree en base, revocable, portant un quota et
# une comptabilite -- et surtout PAS une cle de fournisseur. Sa compromission
# se repare en une commande ("revoke-key") et ne donne acces a aucun compte
# OpenAI ou Anthropic : les cles des fournisseurs vivent dans
# codelab-litellm, que ce conteneur ne voit meme pas.
#
# Elle est lue dans un fichier et exportee dans les sessions, plutot
# qu'inscrite dans config.toml : un fichier se remplace sans toucher a la
# configuration, et se retire sans la casser.
#
# La creer depuis une session, une fois :
#
#   docker exec codelab-llm python3 app.py create-key \
#       --name vscode --user VOTRE-NOM --app vscode
#
# puis coller la valeur affichee dans $CODEX_HOME/codelab-llm.key.
CLE_LLM_FICHIER="$CODEX_HOME_DIR/codelab-llm.key"
{
    echo '# Genere par codelab-dev -- environnement Codex.'
    echo "export CODEX_HOME=$CODEX_HOME_DIR"
    echo "if [ -r \"$CLE_LLM_FICHIER\" ]; then"
    echo "    CODELAB_LLM_KEY=\"\$(head -n 1 \"$CLE_LLM_FICHIER\" | tr -d '[:space:]')\""
    echo "    export CODELAB_LLM_KEY"
    echo "fi"
} > "$CODEX_PROFILE"
chmod 644 "$CODEX_PROFILE"

# Droits du fichier de cle, s'il existe deja : personne d'autre que son
# proprietaire n'a a le lire.
[ -f "$CLE_LLM_FICHIER" ] && chmod 600 "$CLE_LLM_FICHIER" 2>/dev/null
[ -f "$CLE_LLM_FICHIER" ] && chown "$SSH_USER" "$CLE_LLM_FICHIER" 2>/dev/null
true

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

# ----------------------------- OpenClaw -----------------------------
#
# Second agent de developpement, a cote de codex. Sa configuration vit dans
# /workspace comme celle de Codex, pour la meme raison : c'est le seul dossier
# qui survit a une recreation du conteneur. OpenClaw lit ~/.openclaw ; un lien
# symbolique suffit a l'y emmener, sans dependre d'un nom de variable
# d'environnement qui pourrait changer.
OPENCLAW_HOME_DIR="$WORKSPACE_DIR/.openclaw"
mkdir -p "$OPENCLAW_HOME_DIR"
chown "$SSH_USER" "$OPENCLAW_HOME_DIR" 2>/dev/null || true
chgrp "$CODELAB_GROUP" "$OPENCLAW_HOME_DIR" 2>/dev/null || true
chmod 2770 "$OPENCLAW_HOME_DIR" 2>/dev/null || true
if [ ! -e /home/vscode/.openclaw ]; then
    ln -s "$OPENCLAW_HOME_DIR" /home/vscode/.openclaw 2>/dev/null || true
    chown -h vscode:vscode /home/vscode/.openclaw 2>/dev/null || true
fi

# La configuration d'amorce d'OpenClaw, et son gateway.
#
# CE BLOC A ETE VERIFIE EN EXECUTANT OPENCLAW, ce qui n'etait pas le cas de
# la version precedente -- son commentaire le disait, et elle etait fausse.
# Lancee sur la machine de Lucas, la CLI repondait :
#
#     Config: invalid. Default agent: main.
#     Gateway: not reachable at ws://127.0.0.1:18789
#     Using: deterministic typed commands until we configure a model.
#
# « openclaw config validate » sur le fichier qu'on ecrivait donne la cause
# exacte, et elle n'etait pas devinable :
#
#     models.providers.codelab.models: custom model providers must declare
#     models; provider overlays without models are only supported for
#     bundled providers
#
# Un provider sans « models » n'est pas une configuration incomplete : c'est
# une configuration REFUSEE. OpenClaw tombait alors sur ses commandes
# deterministes, sans modele, et le fichier restait tel quel indefiniment.
#
# TROIS CHOSES MANQUAIENT, chacune constatee a l'execution :
#
#   models[]          la liste des modeles du provider. Ce sont les noms
#                     LOGIQUES de codelab-llm, les memes que ceux du
#                     config.toml de Codex -- un test tient les deux d'accord.
#   agents.defaults   sans modele par defaut, un provider declare ne sert a
#     .model.primary  personne : rien ne dit lequel utiliser.
#   gateway.mode      « Gateway start blocked: existing config is missing
#                     gateway.mode. » Le gateway REFUSE de demarrer sans.
#
# Et un quatrieme point, signale par le gateway lui-meme au demarrage :
# sans gateway.auth.token persiste, il en genere un NOUVEAU a chaque
# demarrage. La CLI et l'extension VS Code lisent le meme fichier : un jeton
# qui change a chaque redemarrage les laisserait dehors. On le tire une fois
# et on le garde.
CLE_POUR_OPENCLAW=""
if [ -r "$CODEX_HOME_DIR/codelab-llm.key" ]; then
    CLE_POUR_OPENCLAW="$(head -n 1 "$CODEX_HOME_DIR/codelab-llm.key" | tr -d '[:space:]')"
fi

OPENCLAW_CFG="$OPENCLAW_HOME_DIR/openclaw.json"

# Le jeton du gateway : celui deja pose, ou un neuf. Lu avec python3 plutot
# qu'a la grep -- c'est du JSON, et une accolade deplacee suffirait a faire
# regenerer un jeton a chaque demarrage sans que personne le remarque.
JETON_OPENCLAW="$(python3 - "$OPENCLAW_CFG" <<'PYJETON' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        config = json.load(f)
    jeton = ((config.get("gateway") or {}).get("auth") or {}).get("token") or ""
except Exception:
    jeton = ""
print(jeton if isinstance(jeton, str) else "")
PYJETON
)"
if [ -z "$JETON_OPENCLAW" ]; then
    JETON_OPENCLAW="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi

# QUAND REECRIT-ON ? Trois cas, et le troisieme est nouveau.
#
#   absent              rien a preserver ;
#   cle perimee         la cle inscrite ne vaut plus celle du fichier --
#                       la configuration est de toute facon inutilisable ;
#   refuse par OpenClaw c'est LUI qui juge, pas nous. Ce cas repare les
#                       installations deja cassees par la version
#                       precedente, sans toucher a une configuration valide
#                       que quelqu'un aurait ajustee a la main.
OPENCLAW_A_ECRIRE=0
if [ ! -f "$OPENCLAW_CFG" ]; then
    OPENCLAW_A_ECRIRE=1
elif [ -n "$CLE_POUR_OPENCLAW" ] \
        && ! grep -q "\"apiKey\": \"$CLE_POUR_OPENCLAW\"" "$OPENCLAW_CFG" 2>/dev/null; then
    OPENCLAW_A_ECRIRE=1
    echo "[codelab-dev] openclaw.json ne porte pas la cle courante : reecrit." >&2
elif ! su -s /bin/sh -c "openclaw config validate" "$SSH_USER" >/dev/null 2>&1; then
    OPENCLAW_A_ECRIRE=1
    echo "[codelab-dev] openclaw.json refuse par OpenClaw : reecrit." >&2
fi

if [ "$OPENCLAW_A_ECRIRE" = "1" ]; then
    cat > "$OPENCLAW_CFG" <<OPENCLAWCFG
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "loopback",
    "auth": { "mode": "token", "token": "$JETON_OPENCLAW" }
  },
  "agents": {
    "defaults": { "model": { "primary": "codelab/codelab-coding" } }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "codelab": {
        "baseUrl": "http://codelab-llm:8080/v1",
        "apiKey": "$CLE_POUR_OPENCLAW",
        "api": "openai-completions",
        "timeoutSeconds": 300,
        "models": [
          { "id": "codelab-coding", "name": "CodeLab Coding", "reasoning": false,
            "input": ["text"], "contextWindow": 200000, "maxTokens": 16384 },
          { "id": "codelab-fast", "name": "CodeLab Fast", "reasoning": false,
            "input": ["text"], "contextWindow": 128000, "maxTokens": 8192 },
          { "id": "codelab-reasoning", "name": "CodeLab Reasoning", "reasoning": true,
            "input": ["text"], "contextWindow": 200000, "maxTokens": 32768 },
          { "id": "codelab-local", "name": "CodeLab Local", "reasoning": false,
            "input": ["text"], "contextWindow": 32000, "maxTokens": 4096 }
        ],
        "request": { "allowPrivateNetwork": true }
      }
    }
  }
}
OPENCLAWCFG
    chown "$SSH_USER" "$OPENCLAW_CFG" 2>/dev/null || true
    chmod 600 "$OPENCLAW_CFG" 2>/dev/null || true
fi

# ------------------- le serveur VS Code, et ses droits -------------------
#
# Le dossier est monte depuis l'hote, donc CREE PAR DOCKER EN ROOT quand il
# n'existe pas encore. Remote-SSH s'y installe sous « vscode » : sans ce
# chown, la premiere connexion echoue sur un refus d'ecriture, et le message
# cote VS Code ne nomme ni le dossier ni le proprietaire.
#
# Seulement s'il n'appartient pas deja au bon compte : un chown -R sur des
# milliers de fichiers d'extensions couterait plusieurs secondes a chaque
# demarrage, pour ne rien changer.
VSCODE_SERVER_DIR=/home/vscode/.vscode-server
if [ -d "$VSCODE_SERVER_DIR" ]; then
    if [ "$(stat -c %U "$VSCODE_SERVER_DIR" 2>/dev/null)" != "$SSH_USER" ]; then
        chown -R "$SSH_USER:$SSH_USER" "$VSCODE_SERVER_DIR" 2>/dev/null \
            && echo "[codelab-dev] $VSCODE_SERVER_DIR rendu a $SSH_USER."
    fi
fi


# ---------------------------- le gateway ----------------------------
#
# SANS LUI, RIEN NE REPOND. La CLI, la TUI et l'extension VS Code sont
# toutes des clients du gateway : c'est lui qui tient les sessions, les
# outils et le routage. Il ne se lance pas tout seul -- d'ou le « Gateway:
# not reachable at ws://127.0.0.1:18789 » que voyait Lucas, meme avec une
# configuration correcte.
#
# SUR LA BOUCLE LOCALE, ET RIEN D'AUTRE. bind: "loopback" est le defaut
# d'OpenClaw, et c'est ce qu'on veut : ses clients sont dans CE conteneur
# (les sessions SSH, et le serveur VS Code qui s'y installe). Aucun port
# publie, aucune surface de plus sur le reseau -- et la doc d'OpenClaw note
# au passage que les connexions en boucle locale sont approuvees d'office,
# donc pas d'appairage a faire a la main.
#
# En tache de fond et pas en PID 1 : sshd reste le processus principal, pour
# garder ses journaux dans « docker logs » et son arret propre sur SIGTERM.
# Si le gateway tombe, les sessions SSH continuent -- l'inverse serait un
# agent qui emporte l'acces au conteneur avec lui.
OPENCLAW_LOG="$OPENCLAW_HOME_DIR/gateway.log"
touch "$OPENCLAW_LOG" 2>/dev/null || true
chown "$SSH_USER" "$OPENCLAW_LOG" 2>/dev/null || true
if su -s /bin/sh -c "openclaw gateway >> '$OPENCLAW_LOG' 2>&1 &" "$SSH_USER"; then
    echo "[codelab-dev] gateway OpenClaw lance sur 127.0.0.1:18789" \
         "(journal : $OPENCLAW_LOG)"
else
    echo "[codelab-dev] gateway OpenClaw non lance -- openclaw repondra" \
         "« not reachable ». Journal : $OPENCLAW_LOG" >&2
fi

# L'extension VS Code, PROPOSEE et pas imposee. VS Code lit ce fichier a
# l'ouverture du dossier et offre de l'installer ; il ne l'installe pas dans
# le dos de l'utilisateur, et une extension retiree a la main le reste.
#
# Ecrit une seule fois : c'est une liste que l'on complete soi-meme.
VSCODE_DIR="$WORKSPACE_DIR/.vscode"
if [ ! -f "$VSCODE_DIR/extensions.json" ]; then
    mkdir -p "$VSCODE_DIR"
    cat > "$VSCODE_DIR/extensions.json" <<'VSCODEEXT'
{
  "recommendations": [
    "openknot.openclaw-extension"
  ]
}
VSCODEEXT
    chown -R "$SSH_USER" "$VSCODE_DIR" 2>/dev/null || true
    chgrp -R "$CODELAB_GROUP" "$VSCODE_DIR" 2>/dev/null || true
fi

# ------------------------------- git -------------------------------
#
# Un .gitconfig minimal, ecrit UNE SEULE FOIS : comme le config.toml de
# Codex, il n'est jamais reecrit ensuite, pour que les reglages faits a la
# main depuis une session survivent aux mises a jour d'image.
#
# CE FICHIER NE PORTE AUCUNE IDENTITE ET AUCUN SECRET.
#
#   - Pas de user.name ni de user.email : personne ici ne sait qui vous etes,
#     et une valeur inventee signerait vos commits d'un faux nom. Sans eux,
#     git refuse de commiter avec un message qui dit exactement quoi taper.
#   - Pas de credential helper, pas de jeton. Pour pousser vers GitHub,
#     apportez votre agent SSH avec vous :
#
#         ssh -A -p 2222 vscode@l-hote
#
#     Les cles restent sur votre poste, la session les emprunte, et
#     l'emprunt s'arrete avec elle. Un jeton depose dans /workspace, lui,
#     resterait lisible par tout ce qui tourne en "vscode" -- dont l'agent
#     de developpement -- et par Dagster et app-manager, qui montent le meme
#     dossier.
#
# safe.directory = * demande une explication, parce que c'est large.
# /workspace est ecrit par trois services aux identites differentes (voir
# plus haut) : un depot dont le .git appartient a root fait refuser git avec
# "dubious ownership". La propriete mixte est ici VOULUE par l'architecture,
# pas un accident a signaler. Ce reglage est acceptable parce que ce
# conteneur est dedie et ne porte qu'un seul utilisateur humain ; il ne le
# serait pas sur une machine partagee, ou il revient a faire confiance aux
# depots des autres.
if [ ! -f "$GITCONFIG" ]; then
    cat > "$GITCONFIG" <<'GITCFG'
# Ecrit une seule fois par codelab-dev -- modifiable librement.
# Ni identite ni secret ici : voir dev/entrypoint.sh.
[init]
	defaultBranch = main
[pull]
	rebase = false
[safe]
	directory = *
GITCFG
fi
chown vscode:vscode "$GITCONFIG" 2>/dev/null || true
chmod 644 "$GITCONFIG" 2>/dev/null || true


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

    # 2. Reconstruction. Ecriture dans un temporaire puis deplacement : sshd
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
        echo "[codelab-dev] Deposer une cle publique dans $KEYS_D/<machine>.pub"
        echo "[codelab-dev] (cote hote : .../config/ssh/authorized_keys.d/), puis redemarrer ce conteneur."
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

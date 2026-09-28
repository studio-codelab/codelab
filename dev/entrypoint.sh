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
#   4. Environnement Codex (compte ChatGPT). CODEX_HOME pointe sur
#      /workspace/.codex, cree ici (Codex ne cree pas ce dossier et refuse
#      de demarrer s'il manque) :
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

# ET UNE PASSE DE RATTRAPAGE A CHAQUE DEMARRAGE.
#
# Le marqueur ci-dessus ne protege qu'UNE passe complete, et c'est voulu :
# un chgrp -R sur un gros workspace coute plusieurs secondes a chaque
# demarrage. Mais il laissait un trou. /workspace est partage avec les
# autres conteneurs -- Dagster y amorce des dossiers, et un projet clone
# depuis une autre session nait avec le groupe et les droits de qui l'a
# cree. Ces entrees-la arrivent APRES la premiere passe, donc plus rien ne
# les rattrapait : vscode les voyait sans pouvoir y ecrire, et « git config »
# ou un simple enregistrement echouait sur un refus de droits.
#
# Celle-ci ne touche QUE ce qui ne va pas : find ecarte d'emblee les entrees
# deja conformes, donc le cas courant ne coute qu'un parcours. Rien a
# corriger, rien d'ecrit.
echo "[codelab-dev] rattrapage des permissions du workspace..."
find "$WORKSPACE_DIR" \! -group "$CODELAB_GROUP" -exec chgrp "$CODELAB_GROUP" {} + 2>/dev/null || true
# -perm -g+w ne garde que ce qui A deja le droit : la negation garde donc ce
# qui ne l'a pas. Le X majuscule laisse les fichiers de code non executables.
find "$WORKSPACE_DIR" \! -perm -g+w -exec chmod g+rwX {} + 2>/dev/null || true
# Le setgid sur les dossiers : sans lui, ce qui nait dedans reprend le groupe
# de son createur et le trou se rouvre au fichier suivant.
find "$WORKSPACE_DIR" -type d \! -perm -g+s -exec chmod g+s {} + 2>/dev/null || true

# Ceinture et bretelles avec UMASK dans /etc/login.defs : couvre les shells
# de login et les shells interactifs, y compris quand un ~/.profile
# utilisateur reimpose un umask plus restrictif apres coup.
printf '%s\n' '# Genere par codelab-dev -- ecriture partagee du workspace.' 'umask 002' > "$UMASK_PROFILE"
chmod 644 "$UMASK_PROFILE"

# ------------------------------ Codex ------------------------------
#
# CODEX_HOME dans /workspace, seul dossier qui survit a une recreation du
# conteneur : sans lui, Codex range son auth.json sous /home/vscode et il
# faut refaire la connexion apres chaque mise a jour d'image. Le dossier est
# cree ici parce que Codex ne le cree pas lui-meme et refuse de demarrer si
# le chemin n'existe pas.
CODEX_HOME_DIR="${CODEX_HOME:-$WORKSPACE_DIR/.codex}"
mkdir -p "$CODEX_HOME_DIR"
chown "$SSH_USER" "$CODEX_HOME_DIR" 2>/dev/null || true
chgrp "$CODELAB_GROUP" "$CODEX_HOME_DIR" 2>/dev/null || true
chmod 2770 "$CODEX_HOME_DIR" 2>/dev/null || true

# CODEX SE CONNECTE AVEC UN COMPTE CHATGPT, pas par codelab-llm.
#
# Un agent de code envoie des dizaines de requetes par tache, chacune avec
# une vingtaine de milliers de jetons d'instructions : aucune part gratuite
# de fournisseur n'y suffit, et la passerelle est reservee aux applications.
# L'abonnement ChatGPT inclut l'usage de Codex -- connexion une fois, par le
# bouton « Sign in » de l'extension ou « codex login » dans un terminal VS
# Code (qui transfere tout seul le port de retour). Le jeton est range dans
# $CODEX_HOME/auth.json.
#
# config.toml par defaut, ecrit une seule fois : jamais reecrit ensuite, les
# reglages faits a la main survivent aux redemarrages. Aucun fournisseur :
# celui d'OpenAI est le defaut de Codex, et le modele se choisit dans
# l'extension ou avec /model.
#
# "danger-full-access" est un choix, pas une negligence : sans lui Codex
# tente de s'isoler avec bubblewrap, qui exige des user namespaces
# indisponibles dans un conteneur non privilegie ("bwrap: No permissions to
# create a new namespace") et aucune commande ne s'execute. L'isolation est
# deja celle du conteneur ; en empiler une seconde ne fait que tout bloquer.
CODEX_CFG="$CODEX_HOME_DIR/config.toml"
if [ ! -f "$CODEX_CFG" ]; then
    cat > "$CODEX_CFG" <<CODEXCFG
# Ecrit une seule fois par codelab-dev -- modifiable librement.
sandbox_mode = "danger-full-access"
approval_policy = "on-request"
CODEXCFG
    chown "$SSH_USER" "$CODEX_CFG" 2>/dev/null || true
fi

# LES config.toml QUI PASSAIENT PAR LA PASSERELLE. On ne retire QUE ce que
# ce conteneur y avait ecrit -- le fournisseur « codelab » et ses sections,
# le catalogue de modeles de l'image, un modele « codelab-* » -- et le reste
# (les [projects...] que Codex ajoute, les reglages faits a la main) reste
# tel quel. Sans ce menage, Codex continuerait d'appeler codelab-llm.
if grep -q '^model_provider = "codelab"' "$CODEX_CFG" 2>/dev/null \
        || grep -qF '[model_providers.codelab]' "$CODEX_CFG" 2>/dev/null; then
    python3 - "$CODEX_CFG" <<'PYCODEXCHATGPT' \
        && echo "[codelab-dev] config.toml de Codex : passerelle retiree, compte ChatGPT."
import re, sys
chemin = sys.argv[1]
gardees, dans_codelab = [], False
for ligne in open(chemin, encoding="utf-8").read().split("\n"):
    section = re.match(r"^\s*\[([^\]]+)\]\s*$", ligne)
    if section:
        dans_codelab = section.group(1).strip() in (
            "model_providers.codelab", "model_providers.codelab.auth")
        if dans_codelab:
            continue
    if dans_codelab:
        continue
    nette = ligne.strip()
    if nette == 'model_provider = "codelab"':
        continue
    if re.fullmatch(r'model = "codelab-[a-z-]+"', nette):
        continue
    if re.fullmatch(r'model_catalog_json = "/usr/local/share/codelab/[^"]*"', nette):
        continue
    gardees.append(ligne)
texte = re.sub(r"\n{3,}", "\n\n", "\n".join(gardees)).strip("\n") + "\n"
open(chemin, "w", encoding="utf-8").write(texte)
PYCODEXCHATGPT
fi

# L'ancienne cle de la passerelle : le panneau la revoque, elle part d'ici.
rm -f "$CODEX_HOME_DIR/codelab-llm.key" 2>/dev/null || true

# LE JETON CHATGPT N'EST LISIBLE QUE PAR vscode. Le dossier est partage avec
# le groupe codelab (le panneau, Dagster) : un auth.json en 0660 leur
# donnerait l'acces au compte ChatGPT.
[ -f "$CODEX_HOME_DIR/auth.json" ] && chmod 600 "$CODEX_HOME_DIR/auth.json" 2>/dev/null
[ -f "$CODEX_HOME_DIR/auth.json" ] && chown "$SSH_USER" "$CODEX_HOME_DIR/auth.json" 2>/dev/null
true

# ~/.codex vers CODEX_HOME. CODEX_HOME n'est pose que par le profil de
# session : un Codex lance sans passer par lui -- l'extension VS Code, un
# script -- cherchait sa configuration dans ~/.codex, ne trouvait rien, et
# redemandait la connexion.
if [ ! -e /home/vscode/.codex ]; then
    ln -s "$CODEX_HOME_DIR" /home/vscode/.codex 2>/dev/null || true
    chown -h vscode:vscode /home/vscode/.codex 2>/dev/null || true
fi

{
    echo '# Genere par codelab-dev -- environnement Codex.'
    echo "export CODEX_HOME=$CODEX_HOME_DIR"
} > "$CODEX_PROFILE"
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

# ------------------------ les restes d'OpenClaw ------------------------
#
# OpenClaw a quitte l'image (voir dev/README.md). Les fichiers qu'il laissait
# dans /workspace -- sa configuration, son journal, et la recommandation
# d'extension et les reglages VS Code que CE script ecrivait pour lui -- ne
# sont plus lus par personne. On les retire, mais les fichiers VS Code
# SEULEMENT s'ils sont encore exactement ceux qu'on avait ecrits : un
# settings.json complete a la main est a toi, il reste.
rm -rf "$WORKSPACE_DIR/.openclaw" 2>/dev/null || true
python3 - "$WORKSPACE_DIR/.vscode" <<'PYRESTES' || true
import json, os, sys
dossier = sys.argv[1]
ports = {"18789": {"label": "OpenClaw - chat", "onAutoForward": "silent"}}
ecrits = {
    "extensions.json": [{"recommendations": ["openknot.openclaw-extension"]}],
    "settings.json": [
        {"remote.autoForwardPorts": True, "remote.portsAttributes": ports},
        {"openclaw.chat.agent": "codex", "openclaw.chat.models": ["codex"],
         "remote.autoForwardPorts": True, "remote.portsAttributes": ports},
    ],
}
for nom, modeles in ecrits.items():
    chemin = os.path.join(dossier, nom)
    try:
        with open(chemin, encoding="utf-8") as f:
            contenu = json.load(f)
    except (OSError, ValueError):
        continue
    if contenu in modeles:
        os.remove(chemin)
        print(f"[codelab-dev] {chemin} (ecrit pour OpenClaw) retire.")
try:
    os.rmdir(dossier)
except OSError:
    pass
PYRESTES

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

# ------------------ les extensions VS Code du poste ------------------
#
# L'extension PostgreSQL (Microsoft) fait partie du poste : parcourir les
# bases, lancer une requete, lire un schema, sans quitter VS Code. D'autres
# s'ajoutent ici, separees par des espaces (CODELAB_VSCODE_EXTENSIONS dans le
# compose ; vide pour n'en installer aucune).
#
# DEUX MECANISMES, parce qu'aucun ne suffit seul :
#
#   1. l'INSTALLATION sur le serveur VS Code de ce conteneur. Le serveur
#      n'existe qu'apres la premiere connexion Remote-SSH -- c'est VS Code
#      qui le depose, a la version du poste. Un guetteur en tache de fond
#      attend donc qu'il apparaisse, puis installe ce qui manque. Le dossier
#      est monte depuis l'hote : une extension installee survit aux mises a
#      jour d'image, et une extension deja la n'est pas retelechargee.
#   2. la RECOMMANDATION dans /workspace/.vscode/extensions.json, pour
#      qu'un poste qui ouvre le dossier la propose meme si l'installation
#      automatique n'a pas pu se faire (pas de reseau, serveur neuf).
VSCODE_EXTENSIONS="${CODELAB_VSCODE_EXTENSIONS-ms-ossdata.vscode-pgsql}"

if [ -n "$VSCODE_EXTENSIONS" ]; then
python3 - "$WORKSPACE_DIR/.vscode/extensions.json" $VSCODE_EXTENSIONS <<'PYRECO' || true
import json, os, sys
chemin, voulues = sys.argv[1], sys.argv[2:]
contenu = {}
if os.path.exists(chemin):
    try:
        with open(chemin, encoding="utf-8") as f:
            contenu = json.load(f)
    except (OSError, ValueError):
        # Un fichier avec des commentaires (JSONC, ce que VS Code accepte)
        # ou ecrit a la main d'une autre facon : on n'y touche pas.
        sys.exit(0)
    if not isinstance(contenu, dict):
        sys.exit(0)
reco = contenu.get("recommendations")
if not isinstance(reco, list):
    reco = []
manquantes = [e for e in voulues if e.lower() not in {str(r).lower() for r in reco}]
if not manquantes:
    sys.exit(0)
contenu["recommendations"] = reco + manquantes
os.makedirs(os.path.dirname(chemin), exist_ok=True)
with open(chemin, "w", encoding="utf-8") as f:
    json.dump(contenu, f, indent=2)
    f.write("\n")
print(f"[codelab-dev] {chemin} : {', '.join(manquantes)} recommandee(s).")
PYRECO
chown -R "$SSH_USER" "$WORKSPACE_DIR/.vscode" 2>/dev/null || true
fi

# Le binaire du serveur VS Code : l'emplacement a change entre versions.
cli_vscode() {
    for cli in "$VSCODE_SERVER_DIR"/cli/servers/*/server/bin/code-server \
               "$VSCODE_SERVER_DIR"/bin/*/bin/code-server; do
        if [ -x "$cli" ]; then echo "$cli"; fi
    done
    return 0
}

installer_les_extensions() {
    for cli in $(cli_vscode); do
        for ext in $VSCODE_EXTENSIONS; do
            ext_min=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
            # Deja installee (le dossier porte l'identifiant en minuscules,
            # suivi de la version) : rien a faire, rien a telecharger.
            if ls -d "$VSCODE_SERVER_DIR/extensions/$ext_min-"* >/dev/null 2>&1; then
                continue
            fi
            # Un echec recent (pas de reseau) : on reessaie dans une heure,
            # pas a chaque minute.
            echec="/tmp/codelab-extension-echec-$ext_min"
            if [ -n "$(find "$echec" -mmin -60 2>/dev/null)" ]; then
                continue
            fi
            if runuser -u "$SSH_USER" -- env HOME="/home/$SSH_USER" \
                   "$cli" --install-extension "$ext" >/dev/null 2>&1; then
                echo "[codelab-dev] extension VS Code $ext installee."
                rm -f "$echec"
            else
                echo "[codelab-dev] extension VS Code $ext : installation" \
                     "impossible, nouvel essai dans une heure."
                touch "$echec"
            fi
        done
    done
}

if [ -n "$VSCODE_EXTENSIONS" ]; then
    ( while :; do installer_les_extensions || true; sleep 60; done ) &
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

# ---------------------- identifiants Postgres et Redis ----------------------

# Tout ce bloc est une commodite : il pre-remplit PGHOST/PGPASSWORD/... dans
# les shells SSH. Il est appele plus bas de maniere non fatale -- une erreur
# ici ne doit jamais empecher sshd de demarrer, sinon une base indisponible
# couperait aussi l'acces SSH, c'est-a-dire le moyen d'aller la reparer.
configure_pg_profile() {

# Attente bornee : 30 s suffisent largement a codelab-postgres pour ecrire son
# bloc, et si le fichier n'arrive jamais on demarre quand meme sans PGPASSWORD.
for _ in $(seq 1 30); do
    if [ -r "$ENV_FILE" ] && grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE" \
       && grep -q '^REDIS_PASSWORD=' "$ENV_FILE"; then
        break
    fi
    sleep 1
done

PG_PASSWORD=""
if [ -r "$ENV_FILE" ]; then
    # tail : la derniere occurrence fait autorite (bloc reecrit en fin de fichier).
    PG_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | tail -n 1)"
fi
# Redis, meme logique : REDIS_URL pour le code lance depuis une session, et
# REDISCLI_AUTH pour que "redis-cli -h codelab-redis" s'authentifie sans
# mettre le mot de passe sur la ligne de commande. Absent si codelab-redis
# n'a pas encore ecrit son bloc : le prochain redemarrage le reprendra.
REDIS_PASSWORD=""
REDIS_URL=""
if [ -r "$ENV_FILE" ]; then
    REDIS_PASSWORD="$(sed -n 's/^REDIS_PASSWORD=//p' "$ENV_FILE" | tail -n 1)"
    REDIS_URL="$(sed -n 's/^REDIS_URL=//p' "$ENV_FILE" | tail -n 1)"
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
    if [ -n "$REDIS_PASSWORD" ]; then
        echo "export REDISCLI_AUTH=$(printf '%q' "$REDIS_PASSWORD")"
        echo "export REDIS_URL=$(printf '%q' "$REDIS_URL")"
    fi
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

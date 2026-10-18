# codelab-dev — le poste de développement

Un conteneur SSH prêt pour VS Code Remote-SSH, où l'on écrit le code de `/workspace`. Tout y est
configuré : Postgres, Redis, git, l'agent de code et l'outil `codelab`.

```bash
ssh vscode@<serveur> -p 2222
```

## Accès

- **Clé publique uniquement** (`PasswordAuthentication no`, `PermitRootLogin no`). Un fichier par
  machine dans `/DATA/AppData/codelab/config/ssh/authorized_keys.d/`. Retirer le fichier retire
  l'accès au redémarrage suivant. Sans clé déposée, personne n'entre.
- **Empreinte stable** : les clés hôte vivent dans `config/ssh/host_keys/` et survivent aux mises à
  jour. Si elle a changé malgré tout : `ssh-keygen -R "[<serveur>]:2222"`.
- **`Permission denied (publickey)`** alors que la clé est là : `docker logs codelab-dev` donne la
  vraie cause, en général des droits sur `authorized_keys`. L'entrypoint les repose à chaque
  démarrage.

## Où suis-je ?

| Environnement | Comment y aller | Ce qu'on y trouve |
|---|---|---|
| Ton ordinateur | terminal habituel | VS Code, rien du projet |
| `codelab-dev` | `ssh vscode@<serveur> -p 2222` | le code, `/workspace`, les outils |
| L'hôte | `ssh <toi>@<serveur>` | Docker, les volumes |

**Seul `/workspace` survit** à une mise à jour d'image. `/home/vscode`, `/usr/local` et `/etc` sont
reconstruits à chaque fois.

Depuis ce conteneur, `127.0.0.1` désigne `codelab-dev` lui-même : les autres services se joignent par
leur nom (`codelab-postgres`, `codelab-app-manager`…).

## Outils installés

- **Langages** : Python 3.13 avec `psycopg` et `dagster` ; Node et npm.
- **Ligne de commande** : git, curl, `psql`, `redis-cli`, `codex`.
- **Vidéo** : Chromium (Playwright), `piper` et sa voix française `fr_FR-siwis-medium`, `ffmpeg` — pour `codelab video`.
- **Connexions prêtes** dans chaque session (`/etc/profile.d/`) :

| Variables | Effet |
|---|---|
| `PGHOST`, `PGPORT` | le serveur Postgres |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `CODELAB_DB` | **dans le dossier d'un projet seulement** : le compte de SA base (voir « Une base, un compte ») ; `psql` et `psycopg.connect()` sans argument |
| `REDIS_URL`, `REDISCLI_AUTH` | `redis-cli -h codelab-redis` sans mot de passe à taper |
| `CODEX_HOME=/workspace/.codex` | la connexion et la configuration de Codex survivent aux mises à jour |

## L'outil `codelab`

| Commande | Effet |
|---|---|
| `codelab create <nom>` | crée `/workspace/<nom>` : `git init`, `.gitignore` (`.env`, `node_modules/`, `vendor/`, `.dagster-venv/`, `dist/`, `build/`…), `README.md`, `AGENTS.md`, `CLAUDE.md`, `codelab_auth.py`, `codelab-auth.js`, `codelab_web.py`, `codelab-web.js`, et la base du projet |
| `… --sans-db` (`--no-db`) | sans la base |
| `codelab update [projet]` | remet un projet existant au niveau de la stack, sans rien effacer : réécrit le bloc du manuel dans `AGENTS.md`, pose `CLAUDE.md` s'il manque, ajoute les modules manquants et remplace ceux que git garde intacts (une copie modifiée reste), ajoute `.env` et `.dagster-venv/` au `.gitignore`, crée la base si elle manque. Sans nom : le projet du dossier courant |
| `… --tous` (`--all`) | chaque projet de `/workspace` qui porte le manuel CodeLab |
| `codelab video [projet]` | la vidéo de présentation : pose `video/scenario.mjs` s'il manque, sinon le joue dans Chromium, ajoute la voix off (Piper, en français, hors ligne) et monte `video/presentation.mp4` et `video/affiche.jpg` avec ffmpeg. Filmée par le hub, une application privée s'ouvre sans connexion, et démarre si elle est arrêtée (une adresse locale muette passe d'elle-même par le hub, et le panneau retrouve l'application par le dossier du projet, quel que soit son nom) : un laissez-passer de tournage obtenu avec `~/.codelab-tournage` (la clé que le poste recopie du panneau, `0600`). Voir la section 14 du manuel |
| `codelab help [commande]` | l'aide (en anglais) ; avec un nom, le détail de la commande ; `codelab` seul l'affiche aussi |
| `codelab db <nom>` | la base seule : `<nom>`, son compte, son schéma `dagster` et le `search_path` (idempotent), par le gardien des bases ; appelée par `create` et `update` |
| `codelab vscode-bases` | les connexions SQLTools (voir plus bas) ; appelée par `create`, `update` et le poste |

Toutes les commandes passent par le terminal (SSH, ou celui de VS Code). Le hub reprend la liste dans
*Paramètres > Aide* (administrateur).

**Après chaque mise à jour de CodeLab**, lance `codelab update --tous` : les projets reçoivent le
manuel du jour, que l'agent (Codex ou Claude Code) lit avant chaque tâche.

Le nom d'un projet : des lettres, des chiffres, `-` et `_`, en commençant par une lettre. La commande
refuse un dossier existant. Elle ne génère pas de squelette de framework : pour un front, lance
ensuite `npm create vite@latest .`.

## Le cycle complet

```bash
codelab create mon-projet
cd /workspace/mon-projet
codex                         # ou Claude Code, ou VS Code
npm run build                 # vérifier que le build passe
```

Puis, dans le panneau :

1. *Applications > Ajouter un projet* ;
2. choisir le dossier, accepter les commandes proposées ;
3. activer l'application. Elle est servie sur `http://<serveur>:9002/mon-projet/` (derrière un
   VPS : `https://mon-projet.<domaine>/`).

À chaque modification : **Build** (s'il y en a un), puis **Redémarrer**, dans la fiche de
l'application. Les règles d'intégration au hub (sous-chemin, port) sont dans [`AGENTS.md`](AGENTS.md).

## Git et GitHub

- **Configuration globale** : elle vit dans `/workspace/.codelab/gitconfig`, et `~/.gitconfig` y mène.
  Elle survit donc aux mises à jour.
- **Valeurs posées par défaut** : `init.defaultBranch=main`, `pull.rebase=false` et
  `safe.directory=*` (la propriété des fichiers est mixte par construction).
- **Se nommer**, une seule fois :

  ```bash
  git config --global user.name "Ton Nom"
  git config --global user.email "toi@exemple.fr"
  ```

- **Pousser** : viens avec ton agent SSH (`ssh -A -p 2222 vscode@<serveur>`, puis vérifie avec
  `ssh-add -l`).
- **Jamais de jeton ni de clé dans `/workspace`** : le dossier est lisible par l'agent de code,
  Dagster et le panneau.

## Une base, un compte

Le poste n'a **pas** le compte administrateur de Postgres : il entre dans toutes les bases, et le
code (le tien, ou celui d'un agent) n'a pas à pouvoir le faire.

- **Dans le dossier d'un projet** (`/workspace/<projet>`), le shell reçoit le compte de la base de
  ce projet, et lui seul : `psql`, `psycopg.connect()` et le code lancé à la main s'y connectent sans
  rien saisir. La base : `CODELAB_DB` dans le `.env` du projet, sinon le nom du dossier. Un `cd`
  vers un autre projet change de compte ; hors d'un projet, aucun.
- **Les autres bases** refusent ce compte, celles de la stack (`dagster`, `codelab`) comprises.
- **`codelab db <nom>`** demande la base au **gardien des bases** : un processus root du poste, seul à
  détenir le mot de passe administrateur, que les sessions ne peuvent pas lire. Il crée la base et
  son compte, puis met à jour `~/.codelab-bases` (le compte de chaque base de projet, lu par le
  shell et SQLTools), au démarrage, toutes les cinq minutes et après chaque création.
- **Pour explorer une base de la stack**, passe par le serveur (`docker exec codelab-postgres psql -U
  codelab -d dagster`), pas par le poste.

## VS Code et agents de code

- **Extensions conservées** : `~/.vscode-server` est monté depuis l'hôte. Supprime-le de temps en
  temps, il garde une copie par version.
- **SQLTools** (`mtxr.sqltools` et son pilote PostgreSQL) est installé d'office, **connexions
  comprises** : chaque base du serveur y apparaît, dans le groupe « CodeLab », pour n'importe quel
  projet ouvert. Rien à créer à la main.
  - Chaque connexion utilise le **compte de sa base**, jamais l'administrateur ; les bases de la
    stack (`dagster`, `codelab`) n'y sont pas.
  - Les connexions sont déclarées dans les réglages *Remote* du serveur VS Code
    (`~/.vscode-server/data/Machine/settings.json`, en `0600`), au démarrage puis toutes les cinq
    minutes, et aussitôt après `codelab create`, `codelab update` ou `codelab db`. `codelab vscode-bases` les met
    à jour à la demande.
  - Seul le groupe « CodeLab » est réécrit : une connexion ajoutée à la main reste. Un fichier de
    réglages avec des commentaires n'est pas touché (la commande le signale).
  - Au premier usage, SQLTools propose d'installer la bibliothèque `pg` : accepte.
- **Database Client** (`cweijan.vscode-database-client2`) reste installé, pour Redis notamment. Il
  range ses connexions sur ton poste, hors de portée du serveur : elles se créent à la main (hôte
  `codelab-postgres` ; depuis le dossier du projet, utilisateur `echo $PGUSER`, mot de passe
  `echo $PGPASSWORD`).
- **D'autres extensions** : `CODELAB_VSCODE_EXTENSIONS` dans le compose, séparées par des espaces.
- **Codex**
  - Connexion avec un compte ChatGPT : `codex login`, ou `printenv OPENAI_API_KEY | codex login --with-api-key`.
  - Configuration dans `/workspace/.codex/config.toml`, écrit une seule fois
    (`sandbox_mode = "danger-full-access"`, `approval_policy = "on-request"`).
  - Le jeton `auth.json` est remis en `0600` à chaque démarrage.
  - Hors VS Code, la connexion demande un tunnel : `ssh -p 2222 -L 1455:127.0.0.1:1455 vscode@<serveur>`.
- **Claude Code** lit `CLAUDE.md`, qui importe `AGENTS.md`.
- **Le manuel** est aussi copié à chaque démarrage dans `/workspace/.codex/AGENTS.md`, comme
  instructions globales de Codex.

## Quand ça ne marche pas

| Symptôme | Cause habituelle |
|---|---|
| Page blanche, 404 sur les assets | base des adresses non réglée (voir [`AGENTS.md`](AGENTS.md), section 5) |
| « Ne répond pas » | port en dur au lieu de `$PORT`, écoute sur `localhost` en IPv6 seulement (`::1`), ou plantage après le démarrage ; voir le journal |
| « Elle écoute sur toutes les interfaces » | écoute sur `0.0.0.0` : passe à `127.0.0.1`, sinon les autres conteneurs la joignent sans connexion |
| Pastille rouge clignotante | 5 crashs en 10 minutes : relance automatique suspendue |
| Build en échec | journal de l'application, dans sa fiche |
| Fichier non modifiable | `rm /workspace/.codelab/permissions-v1`, puis redémarrer la stack |
| VS Code ou l'agent redémarrent en boucle | manque de mémoire ou de processus : `docker stats codelab-dev` ; remonter le plafond (1280 Mo, 1024 processus) sans dépasser le tiers de la machine |

## L'image

| Fichier | Rôle |
|---|---|
| `Dockerfile` | `python:3.13-slim`, openssh, git, Node, `postgresql-client`, `codex`, Chromium, Piper, ffmpeg ; utilisateur `vscode` (uid 1000), groupe `codelab` (gid 2000) |
| `entrypoint.sh` | au démarrage : permissions de `/workspace`, `CODEX_HOME` et manuel, clés hôte, `authorized_keys` reconstruit depuis `authorized_keys.d/`, variables de session, gitconfig, extensions VS Code ; puis `sshd -D -e` en processus principal |
| `codelab` | l'outil de projet |
| `AGENTS.md` | le manuel des projets, livré dans l'image |
| `modeles/` | les modules déposés par `codelab create` et tenus à jour par `codelab update` |
| `video/` | `fabriquer.mjs` (le tournage et le montage de `codelab video`) et le modèle de scénario |

| Variable (compose) | Rôle |
|---|---|
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` | connexion Postgres des sessions |
| `CODELAB_ENV_FILE` | `credentials.env` (lu en root) : le mot de passe administrateur, gardé par le gardien des bases, et Redis |
| `CODELAB_SSH_DIR` | dossier des clés (`authorized_keys.d/`, `host_keys/`) |
| `CODELAB_VSCODE_EXTENSIONS` | extensions installées d'office |

| Volume | Contenu |
|---|---|
| `/workspace` | les projets |
| `/var/lib/codelab/ssh` | clés autorisées et clés hôte |
| `/var/lib/codelab/config` (lecture seule) | `credentials.env` |
| `/home/vscode/.vscode-server` | serveur et extensions VS Code |

Une base indisponible ne bloque jamais `sshd` : l'accès SSH est justement ce qui sert à la réparer.

```bash
docker build -f dev/Dockerfile -t codelab-dev-test .
mkdir -p essai-ssh/authorized_keys.d && cp ~/.ssh/id_ed25519.pub essai-ssh/authorized_keys.d/
docker run --rm -it -v "$PWD/essai-ssh:/var/lib/codelab/ssh" -p 2222:22 codelab-dev-test
```

## Retiré

Le nettoyage des restes d'OpenClaw dans `/workspace` (`.openclaw/`, et les réglages VS Code écrits
pour lui). Dernière version qui le portait : le commit `f9caf52`. Une installation qui n'a pas démarré depuis
longtemps passe d'abord par celle-là, puis par la version courante.

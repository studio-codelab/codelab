# codelab-dev

Conteneur d'environnement de developpement : serveur SSH + utilisateur dedie, pret pour VS Code Remote-SSH,
avec un acces Postgres deja configure dans l'environnement de session.

## Ce que fait l'image

- Base `python:3.13-slim` + `openssh-server`, `git`, `curl`, `sudo`, `postgresql-client`, `node`/`npm`.
- `codex`, l'agent de developpement en ligne de commande d'OpenAI (`npm install -g @openai/codex`),
  disponible dans le PATH de toute session SSH. `CODEX_HOME` est pose sur `/workspace/.codex` par
  l'entrypoint, avec un `config.toml` par defaut ecrit une seule fois : l'authentification et les reglages
  survivent a une recreation du conteneur, `codex login` n'est a faire qu'une fois. Usage quotidien dans
  [`../DEVELOPPER.md`](../DEVELOPPER.md).
- Un utilisateur `vscode` (UID 1000), sans mot de passe, authentification **uniquement par cle publique**
  (`PasswordAuthentication no`, `PermitRootLogin no`).
- Un script de demarrage (`ENTRYPOINT` du `Dockerfile`) qui, a chaque lancement du conteneur :
  1. Pose le socle de permissions sur `/workspace` (groupe partage, setgid, `umask 002`) pour que les
     fichiers restent modifiables depuis SSH comme depuis Dagster.
  2. Pose `CODEX_HOME=/workspace/.codex` et cree ce dossier, pour que l'authentification de `codex`
     survive a une recreation du conteneur, et y recopie le manuel `agents-codelab.md` comme
     instructions globales de l'agent (recopie a chaque demarrage : il decrit la stack, une version
     perimee donnerait des consignes fausses).
  3. Genere les cles hote SSH si elles n'existent pas encore dans le volume persistant, sinon reutilise celles
     deja presentes (voir [Cles hote SSH](#cles-hote-ssh-et-empreinte-stable)).
  4. Reconstruit `authorized_keys` a partir de `authorized_keys.d/`.
  5. Exporte les variables de connexion Postgres (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`) pour
     qu'elles soient disponibles dans toute session SSH interactive (voir
     [Variables Postgres dans une session SSH](#variables-postgres-dans-une-session-ssh)).
  6. Passe la main a `sshd`, qui devient le processus principal du conteneur.

`sshd` est le processus principal, pas un demon lance en arriere-plan derriere un veilleur. Deux
consequences : ses journaux d'authentification arrivent dans `docker logs codelab-dev` (c'est la
qu'on lit *pourquoi* un `Permission denied (publickey)` se produit), et il recoit le `SIGTERM` de
`docker stop`, donc l'arret est immediat au lieu d'attendre les dix secondes du delai de grace.

## Fichiers

| Fichier | Role |
|---|---|
| `Dockerfile` | Construction de l'image (paquets, utilisateur, dependances Python) |
| `entrypoint.sh` | Toute la logique de demarrage |
| `agents-codelab.md` | Manuel de l'environnement lu par les agents (perimetre, base, Dagster, app-manager) |
| `codelab-agents.sh` | Installe ce manuel dans le `AGENTS.md` d'un projet |
| `codelab-project.sh` | Cree la base d'un projet et son schema `dagster` |

Le `Dockerfile` et l'`entrypoint.sh` portent l'essentiel. Les dependances Python sont declarees directement dans le `Dockerfile`
plutot que dans un `requirements.txt` separe : trois paquets ne justifient pas un fichier de plus, et
la liste se lit a l'endroit ou elle est installee.

> L'image inclut `dagster` et `dagster-webserver` pour permettre d'inspecter ou de tester du code Dagster
> directement depuis ce conteneur (ex. `dagster asset materialize` avant de passer par l'interface), en plus
> de l'instance dediee qui tourne dans les conteneurs `codelab-dagster*`.

## Variables d'environnement

| Variable | Origine | Usage |
|---|---|---|
| `SSH_PUBLIC_KEY` | Passee au premier demarrage, **facultative** | Enregistree dans `authorized_keys.d/compose.pub` si absente — sert a amorcer une installation neuve, plus necessaire ensuite |
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` | Fixees dans `docker-compose.yml` | Connexion a `codelab-postgres`. `PGDATABASE=diagnostic` : une session SSH atterrit dans la base du projet modele, pas dans la base d'instance de Dagster |
| `CODELAB_ENV_FILE` | Fixee dans `docker-compose.yml` | `credentials.env` : `POSTGRES_PASSWORD` y est lu pour construire `PGPASSWORD` |
| `CODELAB_SSH_DIR` | Fixee dans `docker-compose.yml` | Dossier unique des cles : `authorized_keys.d/`, `authorized_keys` (derive) + `host_keys/` |

## Volumes attendus

| Point de montage | Contenu |
|---|---|
| `/workspace` | Ton code — partage avec `codelab-dagster`, `codelab-dagster-daemon` et `codelab-app-manager` |
| `/var/lib/codelab/ssh` | Tout le SSH au meme endroit : `authorized_keys.d/`, `authorized_keys` et `host_keys/` (cote hote : `config/ssh/`) |
| `/var/lib/codelab/config` | Lecture seule — `credentials.env`, d'ou est lu le mot de passe Postgres |

## Droits sur les cles : le piege

`sshd` ne lit pas ces deux fichiers avec la meme identite.

- **Cles hote** : lues en `root`, avant toute bascule d'identite. `host_keys/` reste donc en `700 root:root`.
- **`authorized_keys`** : ouvert **apres** que `sshd` a pris l'uid de l'utilisateur cible
  (`temporarily_use_uid` dans les logs). Le fichier doit appartenir a `vscode` et le dossier qui le contient
  doit lui etre traversable, sinon `sshd` ne peut pas l'ouvrir.

L'entrypoint applique donc `755` sur `CODELAB_SSH_DIR`, `700` sur `host_keys/`, et
`chown vscode:vscode` + `600` sur `authorized_keys` a chaque demarrage — y compris sur un fichier ajoute a la
main depuis l'hote, ou il appartient a `root`.

Sans ca, le serveur repond `Permission denied (publickey)` alors que la cle est bien la et bien formee. Le
message cote client est identique a celui d'une cle absente : c'est pour cette raison que `sshd` est lance
avec `-D -e`, qui fait apparaitre la vraie cause dans `docker logs codelab-dev` :

```
Could not open user 'vscode' authorized keys '/var/lib/codelab/ssh/authorized_keys': Permission denied
```

## Cles hote SSH et empreinte stable

`apt-get install openssh-server` genere des cles hote **au moment du build de l'image**. Sans precaution, ces
cles changent a chaque reconstruction d'image (nouvelle version poussee, reinstallation...), ce qui declenche
l'avertissement `REMOTE HOST IDENTIFICATION HAS CHANGED` cote client — et, pire, VS Code Remote-SSH bloque
carrement la connexion (`MitmPortForwardingDisabled`) au lieu de simplement avertir.

Pour eviter ca : au premier demarrage, le script genere les cles hote (`rsa`, `ecdsa`, `ed25519`) dans
`/var/lib/codelab/ssh/host_keys` (mappe sur `/DATA/AppData/codelab/config/ssh/host_keys` cote hote) si elles n'y sont pas deja, puis
les copie vers `/etc/ssh/` avant de lancer `sshd`. Tant que ce dossier n'est pas efface, l'empreinte reste
identique a travers tous les redemarrages et reinstallations.

Si l'empreinte a change malgre tout (premiere migration vers cette version, ou dossier efface manuellement), sur
la machine cliente :

```bash
ssh-keygen -R "[<IP-du-serveur>]:2222"
ssh vscode@<IP-du-serveur> -p 2222   # accepter yes a la nouvelle empreinte
```

## Variables Postgres dans une session SSH

`sshd` n'herite **pas** de l'environnement du process qui l'a lance (comportement standard d'OpenSSH, y compris
dans ce script) : les variables `environment:` du `docker-compose.yml` ne sont donc pas automatiquement visibles
dans un shell ouvert via SSH. Le script de demarrage les ecrit explicitement dans deux endroits pour contourner
ca :

- `/etc/profile.d/codelab-pg.sh` — lu par les shells de connexion (login shells)
- `~/.bashrc` de `vscode` — lu par les shells interactifs non-login (le cas le plus courant pour un terminal VS
  Code ou une session `ssh` classique)

Verifier que ca fonctionne, une fois connecte :

```bash
env | grep ^PG
python3 -c "import psycopg; print(psycopg.connect().execute('SELECT version();').fetchone()[0])"
```

`psycopg.connect()` sans argument lit ces variables automatiquement — donc la base `diagnostic`, pas la base
d'instance de Dagster. Pour viser une autre base : `psql -d mon-projet`, ou
`psycopg.connect(dbname="mon-projet")`.

## Creer la base d'un projet

Chaque projet du workspace a sa propre base, nommee comme son dossier, avec un schema `dagster` dedans (voir
`workspace/README.md`). Celles listees dans `CODELAB_PROJECT_DBS` sont creees par `codelab-postgres` au
demarrage ; pour un projet ajoute ensuite, sans redemarrer la stack :

```bash
codelab-project mon-projet
```

La commande est idempotente : relancee sur un projet existant, elle ne detruit rien et se contente de
reposer le schema et le `search_path`. Puis, dans le `.env` du projet : `CODELAB_DB=mon-projet`.

## Developper / tester localement

```bash
docker build -f dev/Dockerfile -t codelab-dev-test .   # contexte = racine du depot
docker run --rm -it \
  -e SSH_PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
  -p 2222:22 \
  codelab-dev-test
```

Sans variables `PG*` ni `credentials.env` accessible, la partie Postgres du script est ignoree : le script
attend le fichier 30 s, log un avertissement, puis demarre `sshd` quand meme. C'est deliberé — une base
indisponible ne doit jamais couper l'acces SSH, qui est justement le moyen d'aller la reparer.

# codelab-dagster

> **Ce service n'est plus publie sur le reseau.** Dagster n'a aucune authentification, et son interface
> permet de lancer un job -- donc d'executer du code. Le port `3000` de l'hote arrive desormais sur
> `codelab-dagster-proxy`, qui demande un mot de passe et relaie ici. Voir
> [`proxy/README.md`](proxy/README.md).

Un seul conteneur, `codelab-dagster`, fait tourner les deux programmes de Dagster : l'interface web
(`dagster-webserver`) et le daemon (`dagster-daemon run`, qui execute les plannings et les capteurs). C'est
`lancer.sh` qui les demarre : le daemon en tache de fond, relance s'il s'arrete, et l'interface au premier plan.
Un arret du conteneur les arrete proprement tous les deux.

Ils etaient deux services, `codelab-dagster` et `codelab-dagster-daemon` : meme image, memes montages, memes
capacites, meme utilisateur. Les fusionner retire un bloc du compose et 384 Mo de plafond, sans rien changer a
la securite : `lancer.sh` tourne apres la bascule sur l'utilisateur `dagster`, le daemon n'a jamais eu de port,
et l'interface reste derriere `codelab-dagster-proxy`. Les journaux des deux arrivent dans
`docker logs codelab-dagster`.

## Ce que fait l'image

- Installe `dagster`, `dagster-webserver` (memes versions, `DAGSTER_VERSION`), plus `dagster-postgres`
  (version **non figee** — voir [Versionnement des paquets Dagster](#versionnement-des-paquets-dagster)).
- Embarque une configuration et un exemple de code par defaut (`dagster.yaml.default`,
  `definitions.default.py`), deployes automatiquement au tout premier demarrage si absents — jamais utilises
  directement en fonctionnement normal (voir [entrypoint.sh](#entrypointsh)).

## Fichiers

| Fichier | Role |
|---|---|
| `Dockerfile` | Construction de l'image |
| `entrypoint.sh` | Amorcage au demarrage (voir ci-dessous) |
| `dagster.yaml` | Modele de configuration du stockage Dagster — pointe vers Postgres |
| `definitions.py` | Exemple de code Dagster, copie dans `/workspace` s'il y est absent |

## entrypoint.sh

Execute avant la commande du conteneur (`lancer.sh`, ou une commande ponctuelle), dans cet ordre :

1. **Resout le mot de passe Postgres.** Lit `POSTGRES_PASSWORD` dans `CODELAB_ENV_FILE`
   (`credentials.env`, le fichier unique de secrets ecrit par `codelab-postgres`) et l'exporte en
   `DAGSTER_PG_PASSWORD` — `dagster.yaml` ne sait lire un secret que depuis une variable d'environnement.
   Attend le fichier jusqu'a 30 s, au cas ou le conteneur soit lance seul, hors de la stack.
2. **Deploie `dagster.yaml` dans `DAGSTER_HOME`** s'il n'y est pas deja (premier demarrage uniquement — ne
   jamais ecraser une configuration existante).
3. **Amorce `/workspace`** depuis le squelette de l'image (`README`, `definitions.py` agregateur, taches
   VS Code dans `.vscode/`, projet
   `diagnostic`) au premier demarrage, pour eviter un crash au boot sur une installation neuve avant que tu
   aies ajoute ton propre code. Une entree deja presente est laissee telle quelle, sans copie `.exemple` a
   cote.
4. **Met a jour les fichiers du squelette que personne n'a modifies**, a chaque demarrage cette fois.

   Sans cette etape, une correction apportee au projet d'exemple `diagnostic` ne pouvait atteindre aucune
   installation existante : l'image portait le correctif, le disque gardait le defaut, et `docker compose
   pull` n'y changeait rien. Il fallait le savoir et recopier le fichier a la main.

   La decision se prend sur le **contenu**, jamais sur une date. CodeLab note l'empreinte SHA-256 de chaque
   fichier qu'il depose, dans `.codelab/empreintes` ; si le fichier sur disque porte toujours cette
   empreinte, personne n'y a touche et il est remplace. Sinon c'est ton travail, et il est laisse tel quel
   (le demarrage te le dit). Rien n'est jamais cree ni supprime a cette etape : un projet que tu as efface
   ne revient pas.

   Le mecanisme s'entretient seul — **rien a regenerer, aucune etape a ne pas oublier avant de committer**.
   `dagster/squelette.sums` ne sert qu'a amorcer les installations mises en service avant lui, qui n'ont
   encore rien de note ; cette liste est figee et ne decrit que le passe.
5. Passe la main a la commande reelle (`exec "$@"`).

## dagster.yaml : stockage dans Postgres

Sans ce fichier, Dagster utilise du SQLite local (comportement par defaut), ce qui isole ses metadonnees
(runs, event logs, schedules) du reste de l'application et les rend vulnerables a la perte de volume. Ce
`dagster.yaml` configure les trois stockages (`run_storage`, `event_log_storage`, `schedule_storage`) sur la
base **`dagster`** du serveur Postgres partage (`DAGSTER_PG_DB`), avec les identifiants lus depuis
l'environnement :

```yaml
postgres_db:
  username: {env: DAGSTER_PG_USER}
  password: {env: DAGSTER_PG_PASSWORD}
  hostname: {env: DAGSTER_PG_HOST}
  db_name:  {env: DAGSTER_PG_DB}
  port:     {env: DAGSTER_PG_PORT}
```

Cette base ne contient **que** les tables d'instance de Dagster. Les tables produites par les assets d'un
projet vont dans la base de ce projet (schema `dagster`), qui est une autre base — voir
`postgres/README.md` et `workspace/README.md`.

## Chargement de `/workspace/definitions.py`

`lancer.sh` lance `dagster-webserver -h 0.0.0.0 -p 3000 -m definitions` et `dagster-daemon run -m definitions`.
Dans les deux cas, `-m definitions` demande a Python de resoudre un **module** nomme
`definitions` — la variable d'environnement `PYTHONPATH=/workspace` (fixee dans le compose) garantit que c'est
bien `/workspace/definitions.py` qui est charge, et non une eventuelle copie embarquee dans l'image.

> **`dagster-daemon run` sans `-m definitions` echoue au demarrage** avec
> `Error: No arguments given and no [tool.dagster] block in pyproject.toml found.` — c'est le meme mecanisme de
> resolution de code que le webserver, l'argument est simplement facile a oublier puisqu'il n'a pas de valeur
> par defaut cote CLI.

## Versionnement des paquets Dagster

Piege classique de l'ecosysteme Dagster : `dagster` et `dagster-webserver` suivent la meme version
(`DAGSTER_VERSION`, ex. `1.13.7`), mais les paquets d'integration (`dagster-postgres`, et plus
generalement tous les `dagster-*` sauf le coeur et le webserver) suivent **leur propre schema de version**,
historiquement decale — `dagster-postgres==1.13.7` n'existe simplement pas sur PyPI.

C'est pourquoi le `Dockerfile` l'installe **sans version figee** :

```dockerfile
RUN pip install --no-cache-dir \
    "dagster==${DAGSTER_VERSION}" \
    "dagster-webserver==${DAGSTER_VERSION}" \
    "dagster-postgres"
```

`dagster-docker` n'est plus installe : il ne sert qu'a lancer chaque run dans son propre conteneur
(`DockerRunLauncher`), ce que CodeLab ne fait pas -- les runs tournent dans `codelab-dagster`. Il tirait le
SDK Docker pour rien.

`pip` resout alors automatiquement la version de `dagster-postgres` compatible avec la version
exacte de `dagster` installee, via leurs propres contraintes de dependance declarees. Ne jamais figer ce
paquet sur `${DAGSTER_VERSION}`.

## Developper / tester localement

```bash
docker build -f dagster/Dockerfile -t codelab-dagster-test .   # contexte = racine du depot

# Webserver seul, sans Postgres (dagster.yaml par defaut ne sera pas utilisable sans base --
# utile uniquement pour verifier que l'image demarre et sert du HTTP)
docker run --rm -p 3000:3000 -v "$PWD/workspace-test:/workspace" codelab-dagster-test
```

Pour un test complet avec stockage Postgres, plus simple de passer par `docker compose up codelab-postgres
codelab-dagster` depuis la racine du depot, avec les variables `DAGSTER_PG_*` deja definies dans
`docker-compose.yml`.
## Piege : `docker exec` contourne l'entrypoint

C'est l'entrypoint qui lit `POSTGRES_PASSWORD` dans `credentials.env` et l'exporte en
`DAGSTER_PG_PASSWORD`. Or `docker exec` execute la commande **sans** passer par lui : la variable n'existe
pas dans cette session, et toute commande `dagster` echoue avec

```
PostProcessingError: You have attempted to fetch the environment variable "DAGSTER_PG_PASSWORD"
which is not set.
```

Ce n'est pas une panne : le webserver et le daemon, eux, l'ont bien. Pour une commande ponctuelle, fournir
la variable soi-meme :

```bash
docker exec -w /workspace codelab-dagster sh -c \
  'DAGSTER_PG_PASSWORD=$(sed -n "s/^POSTGRES_PASSWORD=//p" /var/lib/codelab/config/credentials.env | tail -n1) \
   dagster asset materialize -m definitions --select <asset>'
```

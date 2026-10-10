# `/workspace` — tes projets

Le seul dossier où tu écris. Il est partagé par trois services, et ce qu'on y dépose est vu aussitôt
par les autres :

| Service | Ce qu'il fait de `/workspace` |
|---|---|
| `codelab-dev` | ton dossier de travail en SSH et dans VS Code |
| `codelab-app-manager` | construit et lance les applications déclarées dans le panneau |
| `codelab-dagster` | charge les `definitions.py` et exécute leurs jobs |

Il vit sur l'hôte (`/DATA/AppData/codelab/workspace`) et survit aux mises à jour.

Les règles complètes qu'un projet doit suivre sont dans le `AGENTS.md` de chaque projet, que pose
`codelab new`.

## Contenu

```
/workspace/
├── README.md            ce fichier
├── definitions.py       agrégateur Dagster : découvre les projets, ne pas modifier
├── .vscode/             tâches VS Code (nouveau projet, base, manuel de l'agent)
├── .codelab/            marqueurs et configuration git de la stack, ne pas modifier
├── .codex/              configuration et connexion de Codex
├── diagnostic/          projet modèle et diagnostic de l'installation
└── mon-projet/          un projet = un dossier
```

Ces fichiers sont déposés au premier démarrage. **Rien n'est jamais écrasé** : un fichier que tu as
modifié reste tel quel, un fichier intact est mis à jour avec l'image, et un projet supprimé ne
revient pas.

## Un projet

Un projet est un dossier. Il n'y a rien à déclarer ailleurs. Il peut contenir :

| Fichier | Rôle |
|---|---|
| `app.py`, `package.json`… | une application web, à déclarer dans le panneau (*Applications > Ajouter un projet*) |
| `definitions.py` exposant `defs` | des jobs Dagster, découverts au prochain *Reload* |
| `requirements-dagster.txt` | les dépendances du côté Dagster ; le projet a alors son propre environnement |
| `.env` | sa configuration, jamais versionnée |
| `AGENTS.md`, `CLAUDE.md` | le manuel lu par les agents de code |
| `codelab_auth.*`, `codelab_web.*` | connexion avec CodeLab ; pages d'erreur et en-têtes |

Créer un projet : `codelab new mon-projet --db` depuis une session SSH, ou **Ctrl+Maj+B** dans
VS Code ouvert sur `/workspace`. Copier `diagnostic/` donne aussi un projet complet qui fonctionne.

## Configuration

Trois couches, de la plus faible à la plus forte :

1. **l'environnement** : celui du conteneur, et pour une application, ce que le panneau lui passe ;
2. **`credentials.env`** : les secrets des services ;
3. **le `.env` du projet**, que le projet lit lui-même. Il l'emporte toujours.

**`credentials.env`** (`/DATA/AppData/codelab/config/credentials.env`, `0600 root`) est découpé en
blocs. Chaque service réécrit le sien à son démarrage.

| Bloc | Contenu | Transmis aux applications |
|---|---|---|
| `codelab-postgres` | `POSTGRES_HOST`, `_PORT`, `_DB` (instance Dagster), `_USER`, `_PASSWORD` | oui |
| `codelab-redis` | `REDIS_URL`, `REDIS_HOST`, `_PORT`, `_USER`, `_PASSWORD` | oui |
| `codelab-mailpit` | `MAIL_HOST`, `MAIL_PORT`, `MAIL_URL` | oui |
| `codelab-sso` | la paire de clés des assertions | la clé publique seule |
| `codelab-app-manager` | mot de passe admin, clé de session, clé de chiffrement | non |
| `codelab-alertes` | serveur d'envoi d'origine (`SMTP_*`) | non |
| `codelab-relais-mail`, `codelab-proxy` | réglages internes entre services | non |

- **Une valeur commune à plusieurs projets** : ajoute **ton propre** bloc à la fin du fichier
  (`# ===== mon-bloc =====` … `# ===== /mon-bloc =====`). Aucun service ne le réécrit.
- **Si une clé apparaît plusieurs fois**, c'est la dernière occurrence qui compte.
- **Une application ne lit pas ce fichier** : le panneau lui passe les valeurs par l'environnement,
  au lancement comme au build. Un mot de passe changé est pris en compte au redémarrage de
  l'application.

## Bases de données

Une base **par projet**, nommée comme son dossier, avec un schéma `dagster` sur lequel pointe le
`search_path`, et **son propre compte** : seul ce compte (et l'administrateur) peut s'y connecter.
La base `dagster` ne contient que les tables d'instance de Dagster.

```bash
codelab db mon-projet      # crée la base et son compte (idempotent)
cd /workspace/mon-projet && psql   # le shell a le compte de la base du projet
```

- L'application reçoit le compte de sa base dans `POSTGRES_USER` / `POSTGRES_PASSWORD` (et
  `CODELAB_DB`) ; le panneau crée la base au lancement si elle manque. Elle n'a accès à aucune
  autre base.

- Un projet redit le nom de sa base dans son `.env` (`CODELAB_DB=mon-projet`) s'il change de dossier.
- `pgvector` est disponible dans chaque base.
- Supprimer un projet proprement : `DROP DATABASE "mon-projet";`.

## Dépendances

**Application web.** Elle est construite par `codelab-app-manager`, qui a Python, Node, npm et
Flask.

- En Python, le reste s'installe dans `vendor/` par la commande de build
  (`pip install --target vendor -r requirements.txt`), et `vendor/` s'ajoute au `sys.path`.
- En Node, `npm ci` avec un lockfile versionné.

**Dagster.** Le processus commun n'a que `dagster`, `dagster-postgres` et `psycopg2`, et tous les
projets y sont chargés ensemble.

- Un projet qui a besoin d'autre chose écrit un `requirements-dagster.txt`, versions figées.
- Au démarrage de `codelab-dagster`, son environnement est construit dans `.dagster-venv/`
  (reconstruit seulement si le fichier change). Le projet apparaît sous son propre nom dans Dagster.
- **Après modification** : `docker restart codelab-dagster`. *Reload* ne reconstruit pas
  l'environnement, et ce redémarrage dure le temps de l'installation.
- **Coût** : 200 à 300 Mo par projet isolé. Au-delà d'un, remonte le `mem_limit` de
  `codelab-dagster`.
- **En cas d'échec de construction**, le projet s'affiche en erreur dans Dagster, avec la cause dans
  `docker logs codelab-dagster`. Les autres projets continuent de tourner.

**Audit.** Chaque lundi, l'asset `audit_dependances` du diagnostic confronte les versions figées de
chaque projet à la base de vulnérabilités OSV. Il lit :

- les lignes `==` des `requirements*.txt` ;
- `vendor/` et `.dagster-venv/` ;
- `package-lock.json`.

Une vulnérabilité fait échouer le run, ce qui envoie un mail. Seules les versions exactes sont lues :
fige-les.

## Dagster

```python
from dagster import Definitions, asset

@asset(group_name="mon-projet")
def mon_asset(context):
    context.log.info("bonjour")

defs = Definitions(assets=[mon_asset])
```

- **Chargement** : le `definitions.py` racine charge chaque projet qui expose `defs`. Un projet en
  erreur est ignoré, avec sa trace dans `docker logs codelab-dagster`, sans gêner les autres.
- **Dossiers ignorés** : ceux qui commencent par `.` ou `_`. Préfixer un dossier par `_` met un
  projet de côté sans le supprimer.
- **Imports** : les modules voisins s'importent sans préfixe, mais deux projets ne peuvent pas avoir
  un module du même nom. Préfixe-les.
- **Un module partagé** entre l'application et Dagster n'importe ni Flask ni Dagster.
- **Interface** : `http://<serveur>:3000/`, avec la session d'administration du panneau.

## Permissions

Les fichiers restent modifiables par tous les services grâce au groupe `codelab` (gid 2000), au bit
setgid sur les dossiers et à l'umask `002`. Si un fichier résiste :

```bash
docker exec codelab-dev rm -f /workspace/.codelab/permissions-v1 && docker restart codelab-dev
```

## Sauvegarde du code

`codelab-app-manager` archive `/workspace` chaque semaine (`workspace-<date>.tar.gz`, quatre gardées).
L'archive exclut les dépendances, les builds, les caches et les fichiers de plus de 50 Mo. Git reste
la vraie sauvegarde du code.

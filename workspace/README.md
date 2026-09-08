# `/workspace` — tes projets

Ce dossier est le seul endroit ou tu ecris. Il est partage entre les trois services de CodeLab, et
ce qu'on y depose est visible immediatement par les autres, sans redeployer quoi que ce soit :

| Service | Ce qu'il fait de `/workspace` |
|---|---|
| `codelab-dev` | c'est ton dossier de travail en SSH et dans VS Code Remote-SSH |
| `codelab-dagster` | charge `definitions.py` et execute les jobs qu'il y trouve |
| `codelab-app-manager` | lance les applications web que tu declares dans le panneau |

Il survit aux mises a jour d'image et aux reinstallations : il vit sur le disque du ZimaOS, dans
`/DATA/AppData/codelab/workspace`.

## Anatomie d'un projet

Un projet est **un dossier**. Rien de plus — pas de fichier a declarer ailleurs, pas de registre
central a tenir a jour.

```
/workspace/
├── README.md            <- ce fichier
├── definitions.py       <- point d'entree Dagster, tu n'as pas a le modifier
└── mon-projet/
    ├── README.md        <- a quoi sert ce projet, comment le lancer
    ├── .env             <- OPTIONNEL : la configuration propre au projet
    ├── definitions.py   <- OPTIONNEL : tout le cote Dagster (assets, jobs, capteurs)
    └── app.py           <- OPTIONNEL : une application web
```

Les deux fichiers optionnels sont independants. Un projet peut n'etre qu'un job Dagster, ou qu'une
application web, ou les deux, ou aucun des deux (un simple dossier de scripts que tu lances a la
main en SSH).

### `definitions.py` — la seule convention

Si ton projet a des jobs Dagster, il expose une variable `defs` :

```python
from dagster import Definitions, asset

@asset(group_name="mon-projet")
def mon_asset(context):
    context.log.info("bonjour")

defs = Definitions(assets=[mon_asset])
```

Le `definitions.py` a la racine du workspace decouvre tout seul les dossiers qui en contiennent un,
les charge, et les fusionne en une interface unique. **Tu n'as jamais a le modifier.** Ton projet
apparait au prochain rechargement du code (bouton *Reload* dans Dagster, ou
`docker restart codelab-dagster`).

`group_name` n'est pas obligatoire mais fortement conseille : c'est ce qui regroupe visuellement tes
assets par projet dans l'interface.

Un projet qui ne se charge pas (erreur de syntaxe, import manquant) est **ignore avec un message
dans les logs**, les autres continuent de fonctionner :

```bash
docker logs codelab-dagster | grep workspace
```

### Imports a l'interieur d'un projet

Le dossier du projet est ajoute au chemin d'import avant chargement. Un module voisin s'importe
donc directement, sans prefixe :

```python
import checks          # /workspace/mon-projet/checks.py
```

C'est ce qui permet d'ecrire un projet comme un simple dossier de scripts.

Attention en revanche a **ce qu'un module partage importe**. Les images n'ont pas les memes paquets :
Flask n'existe que dans `app-manager`, Dagster que dans `codelab-dagster`. Un module importe des deux
cotes -- comme `diagnostic/checks.py` -- ne doit donc importer ni l'un ni l'autre, sinon il fera
planter le service qui n'a pas le paquet. Garde le code commun dans un module neutre, et le code
specifique dans `definitions.py` (Dagster) ou `app.py` (web).

Autre revers :
deux projets ne peuvent pas avoir deux modules de meme nom charges en meme temps. Si tu ecris un
`utils.py` dans deux projets differents, prefixe-les (`utils_facturation.py`) ou regroupe-les dans
un sous-dossier.

### Application web

Depose un `app.py`, puis dans le panneau `http://<IP-ZimaOS>:9001/` → **Ajouter un projet** :

| Champ | Valeur |
|---|---|
| Dossier | `/workspace/mon-projet` |
| Commande de lancement | `python3 app.py` |
| Commande de build | *(optionnelle, voir ci-dessous)* |

## Dependances

L'image `app-manager` ne contient que Flask, `psutil` et `requests`. Pour tout le reste, la commande
de build installe dans un dossier `vendor/` **a cote de ton code**, sans modifier le conteneur :

```
pip install --target vendor "psycopg[binary]"
```

et en tete de `app.py` :

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
```

Cote Dagster, les paquets disponibles sont ceux de l'image (`dagster`, `dagster-postgres`,
`psycopg2`). Un projet qui a besoin d'autre chose doit pour l'instant l'installer lui-meme au
demarrage — l'isolation des dependances par projet est prevue mais pas encore en place.

## Configuration et secrets

**Aucun mot de passe dans le code.** La configuration se lit en trois couches, de la plus faible a
la plus forte :

| Couche | Contenu | Portee |
|---|---|---|
| Environnement du conteneur | pose par le `docker-compose.yml` | toute la stack |
| `credentials.env` | ce qui fait tourner les services : Postgres, SMTP | toute la stack |
| `<projet>/.env` | ce qui ne concerne que ce projet | ce projet seul |

Le `.env` d'un projet **s'ajoute et remplace** : une cle qu'il redefinit gagne toujours.

```
/workspace/mon-projet/.env

    API_TOKEN=xxxxxxxx
    SEUIL_ALERTE=42
    # pointe ce projet sur une autre base, sans toucher au fichier commun
    CODELAB_DB=bac-a-sable
```

La lecture se fait avec `read_env()`, defini dans `checks.py` :

```python
import checks
token = checks.read_env("API_TOKEN")
```

Chaque projet emporte sa propre copie de `checks.py` — c'est ce que tu obtiens en copiant
`diagnostic/`. Il lit le `.env` situe **a cote de lui**, donc un projet ne peut pas lire par erreur
la configuration d'un autre. C'est aussi ce qui evite la collision qui existerait avec
`os.environ` : `codelab-dagster` charge tous les projets dans un seul processus, et deux projets
definissant la meme cle s'y ecraseraient mutuellement.

Ne versionne pas ton `.env` : il vit dans `/workspace`, qui n'est pas chiffre. Un `.gitignore`
contenant `.env` est le minimum si tu pousses le projet sur git.

### `credentials.env`

Le fichier commun se trouve dans `/DATA/AppData/codelab/config/credentials.env`, visible depuis les
conteneurs sous `/var/lib/codelab/config/credentials.env`.

Ce fichier est gere **par bloc** : chaque service ne reecrit que le sien. Un bloc ajoute a la main
sous un nom qu'aucun service ne connait survit donc a tous les redemarrages et a une
reinstallation.

```bash
sudo tee -a /DATA/AppData/codelab/config/credentials.env > /dev/null <<'EOF'
# ===== mon-projet =====
# Bloc ajoute a la main : aucun service ne le reecrit.
API_TOKEN=xxxxxxxx
# ===== /mon-projet =====
EOF
```

Les blocs y sont reecrits en fin de fichier au demarrage des services : c'est la **derniere**
occurrence d'une cle qui fait foi, une valeur laissee plus haut est perimee. Le module `codelab`
s'en charge.

## Base de donnees — une base par projet

Il n'y a **pas de base fourre-tout**. Chaque projet a la sienne, nommee comme son dossier, avec un
schema `dagster` dedans :

| Base | Contenu |
|---|---|
| `dagster` | tables d'instance de Dagster : runs, journal d'evenements, planifications |
| `diagnostic` | le projet `diagnostic` — ses tables dans le schema `dagster` |
| `mon-projet` | ton projet — ses tables dans le schema `dagster` |
| `postgres` | base de maintenance du serveur, volontairement vide |

Deux projets peuvent donc avoir une table `clients` sans se marcher dessus, et supprimer un projet
se fait proprement, sans risquer d'emporter les donnees du voisin :

```sql
DROP DATABASE "mon-projet";
```

Le schema porte le meme nom (`dagster`) dans **toutes** les bases de projet : c'est ce qui permet de
copier un projet d'une base a l'autre sans toucher a une seule requete. Le `search_path` du role est
positionne dessus par base, donc un `CREATE TABLE ma_table` dans un asset y atterrit sans prefixe.

La base d'un projet ajoute apres l'installation se cree depuis une session SSH :

```bash
codelab-project mon-projet
```

Elle est aussi creee automatiquement a la premiere connexion : `checks.connect_pg()`, copie du
projet `diagnostic`, cree la base puis le schema s'ils manquent. Le projet lit son nom de base dans
son `.env` :

```
CODELAB_DB=mon-projet
CODELAB_SCHEMA=dagster
```

Sans `CODELAB_DB`, c'est le nom du dossier qui sert — un projet copie sous un autre nom vise donc sa
propre base sans edition.

En SSH, `psql` fonctionne sans argument (l'entrypoint de `codelab-dev` pre-remplit `PGHOST`,
`PGUSER`, `PGPASSWORD`, et `PGDATABASE=diagnostic`). Pour aller dans une autre base :

```bash
psql -d mon-projet
```

```sql
\dt          -- le search_path pointe deja sur le schema dagster de cette base
```

## Permissions

Les fichiers crees par Dagster ou par une application web appartiennent a `root`, ceux de tes
sessions SSH a `vscode`. Ils restent modifiables des deux cotes grace a un groupe commun
(`codelab`) et au bit setgid pose sur `/workspace`.

Si un fichier resiste malgre tout :

```bash
docker exec codelab-dev rm -f /workspace/.codelab/permissions-v1
docker restart codelab-dev
```

Le marqueur supprime, les permissions sont reappliquees a tout le workspace au demarrage suivant.

## Le projet `diagnostic`

Il est installe par defaut et **sert de modele**. Il montre, sur un cas reel et fonctionnel, a peu
pres tout ce qui est decrit ci-dessus : un asset Dagster, une application web, un module partage
entre les deux, la lecture de `credentials.env`, l'ecriture en base, un capteur d'alerte mail.

Le plus rapide pour demarrer un projet est de le copier :

```bash
cp -r /workspace/diagnostic /workspace/mon-projet
cd /workspace/mon-projet
# puis vider ce qui ne sert pas et renommer l'asset
```

Il verifie aussi que la stack est saine — utile quand quelque chose ne marche pas et qu'on ne sait
pas quel maillon accuser. Voir `diagnostic/README.md`.

Tu peux le supprimer si tu n'en veux pas : rien d'autre n'en depend. Il ne sera pas reinstalle,
sauf si tu supprimes aussi `/workspace/.codelab/workspace-v1`.

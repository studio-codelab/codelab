# CodeLab -- regles de developpement

Tu travailles dans le conteneur `codelab-dev` de la stack CodeLab. Ce document
decrit l'environnement et les conventions qui s'y appliquent. Il est fourni par
l'environnement, pas par le projet : les specificites du projet courant sont
dans le `AGENTS.md` et le `README.md` a la racine de ce projet, et ils
l'emportent sur ce qui suit en cas de contradiction.

## Perimetre -- ce que tu peux modifier

- **Uniquement le dossier du projet courant.** Un projet CodeLab est un dossier
  sous `/workspace/`, et c'est ta seule zone d'ecriture.
- **Ne modifie jamais** `/workspace/definitions.py` (point d'entree Dagster
  partage, il decouvre les projets tout seul), `/workspace/README.md`, ni le
  dossier d'un autre projet. Si le travail semble en exiger, dis-le au lieu de
  le faire.
- N'ecris rien hors de `/workspace` : `/home`, `/usr/local` et `/etc` sont
  recrees a partir de l'image a chaque mise a jour, tout y est perdu.
- Pas de `sudo apt install` : la modification ne survivra pas. Une dependance
  se declare dans le projet (voir Dependances).

## L'environnement

Conteneur Linux, utilisateur `vscode`. Sont deja installes : `python3`,
`node`/`npm`, `git`, `psql`, `codex`, et cote Python `dagster` et
`psycopg`. Trois conteneurs distincts se partagent `/workspace` et **n'ont pas
les memes paquets** :

| Conteneur | Role | Paquets |
|---|---|---|
| `codelab-dev` | ou tu travailles (SSH, VS Code) | python3, node, npm, git, psql, dagster, psycopg |
| `codelab-dagster` | execute les jobs Dagster | dagster, dagster-postgres, psycopg2 |
| `codelab-app-manager` | build et sert les applications web | python3, node, npm, git, flask, psutil, requests |

Consequence directe : un module importe des deux cotes ne doit importer ni
Flask ni Dagster. Garde le code commun dans un module neutre, le code Dagster
dans `definitions.py`, le code web dans `app.py`.

## Verifier avant de conclure

Ne declare jamais une tache terminee sans avoir execute ce que le projet
fournit : `npm test`, `npm run build`, ou l'execution directe du script. Si une
commande echoue, corrige et relance. Un resultat non verifie doit etre annonce
comme tel.

## Base de donnees

Une base **par projet**, nommee comme le dossier, avec un schema `dagster`
dedans. Il n'y a pas de base fourre-tout, et la base `postgres` n'existe pas.

```bash
psql                  # se connecte sans argument (PGHOST/PGUSER/PGPASSWORD deja poses)
psql -d mon-projet    # la base d'un autre projet
codelab-project mon-projet   # cree la base et son schema (idempotent)
```

Le `search_path` pointe deja sur le schema `dagster` de la base : un
`CREATE TABLE ma_table` y atterrit sans prefixe.

En Python, la connexion ne prend aucun argument -- les variables `PG*` suffisent :

```python
import psycopg
with psycopg.connect() as conn:            # base par defaut de la session
    conn.execute("SELECT 1")
psycopg.connect(dbname="mon-projet")       # une base precise
```

Le nom de base d'un projet se declare dans son `.env` (`CODELAB_DB=mon-projet`) ;
sans lui, c'est le nom du dossier qui sert.

## Dagster

Un projet expose ses jobs en deposant un `definitions.py` **dans son dossier**,
avec une variable `defs`. Le fichier racine le decouvre seul : rien a declarer
ailleurs.

```python
from dagster import Definitions, asset

@asset(group_name="mon-projet")     # group_name : regroupe les assets par projet
def mon_asset(context):
    context.log.info("bonjour")

defs = Definitions(assets=[mon_asset])
```

- Les modules voisins s'importent sans prefixe (`import checks`), le dossier du
  projet etant ajoute au chemin d'import.
- Deux projets ne peuvent pas exposer deux modules de meme nom : prefixe
  (`utils_facturation.py`) ou regroupe dans un sous-dossier.
- Le projet apparait au prochain rechargement du code (bouton *Reload* dans
  Dagster, ou `docker restart codelab-dagster`).
- Un projet qui ne se charge pas est ignore avec sa trace dans
  `docker logs codelab-dagster`, sans empecher les autres de tourner.

## Application web servie par l'app-manager

L'app-manager lance la commande du projet dans **son** conteneur et l'expose
sur `http://<serveur>:9001/<nom-du-projet>/`. Cinq regles :

1. **Ecoute sur `$PORT`** — la variable est fournie par l'app-manager. Ne code
   jamais un numero de port en dur.
2. **Ecoute sur `0.0.0.0`**, pas sur `127.0.0.1` (Flask :
   `app.run(host="0.0.0.0", port=int(os.environ["PORT"]))`).
3. **L'application est servie sous un sous-chemin** `/<nom-du-projet>/`. Un
   front construit pour la racine y affiche une page blanche : configure
   `base` (Vite), `basePath` (Next.js), `homepage` (CRA).
4. **Front : build puis service statique.** La commande de build produit
   `dist/`, la commande de lancement le sert
   (`python3 -m http.server $PORT --directory dist`). Ne deploie jamais un
   serveur de developpement (`vite dev`, `npm run dev`).
5. **Dependances Python** : l'image app-manager n'a que Flask, `psutil` et
   `requests`. Le reste s'installe a cote du code par la commande de build,
   `pip install --target vendor "psycopg[binary]"`, avec en tete de `app.py` :

   ```python
   import os, sys
   sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
   ```

## Configuration et secrets

- **Aucun mot de passe, jeton ou cle en clair dans le code**, jamais.
- La configuration propre au projet va dans un `.env` **a cote du code**, lu par
  le projet lui-meme. Ne le versionne pas : ajoute `.env` au `.gitignore`.
- N'ecris jamais dans `credentials.env` (secrets de la stack) et ne recopie
  aucune de ses valeurs dans un fichier du projet.

## Chemins

Ecris des chemins **relatifs au projet**, jamais de `/workspace/...` en dur : le
meme code doit fonctionner si le dossier est renomme ou copie.

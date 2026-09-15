# CodeLab -- regles de developpement

> Manuel de l'environnement, livre par l'image `codelab-dev` et recopie dans
> chaque projet par `codelab agents`. Il decrit la stack dans laquelle tournent
> les projets, jamais le depot CodeLab lui-meme.

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
codelab db mon-projet        # cree la base et son schema (idempotent)
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

## Le style de CodeLab

Une application servie par le panneau est vue **dans** CodeLab : elle gagne a
en porter les couleurs plutot qu'a en inventer d'autres. Le theme est servi par
le panneau, sur la meme origine que ton application.

```html
<link rel="stylesheet" href="/theme.css">
```

Tu disposes alors de ces variables. **Ne recopie jamais leurs valeurs** : une
palette recopiee est une palette qui divergera le jour ou le panneau changera
la sienne.

| Variable | Ce que c'est |
|---|---|
| `--bg` `--surface` `--surface2` | le fond de la page, les cartes, les fonds sourds |
| `--line` `--line2` | les traits : separation, et bordure d'un champ |
| `--txt` `--dim` `--dim2` | le texte, le texte secondaire, le texte tres discret |
| `--accent` `--accent-h` `--accent-soft` | **ce qui se clique**, et rien d'autre |
| `--ok` `--warn` `--err` (+ `-bg`, `-border`) | les etats : en ligne, a surveiller, en panne |
| `--r` `--r-s` `--r-xs` | les rayons : carte, bouton, pastille |
| `--t-xs` a `--t-xl` | l'echelle de tailles de texte |
| `--sans` `--mono` | les polices : Manrope, et la chasse fixe pour le code |

### Les cinq regles qui font qu'une page ressemble a CodeLab

1. **L'accent ne designe que ce qui se clique.** Les etats ont leurs propres
   couleurs. Un vert veut dire "en ligne" partout, un rouge "en panne"
   partout, et le turquoise ne veut jamais dire autre chose que
   "actionnable". Sans cette separation, une ligne en panne se confond avec
   un bouton -- et c'est exactement le cas ou il ne faut pas se tromper.
2. **La severite se lit au bord.** Une ligne de tableau porte un bandeau de
   3 px sur son bord gauche : `box-shadow:inset 3px 0 0 var(--ok)`. On repere
   une anomalie sans lire une seule ligne.
3. **Le trait plutot que l'ombre.** La hierarchie vient des bordures et des
   fonds. Une carte : `background:var(--surface)`, `border:1px solid
   var(--line)`, `border-radius:var(--r)`, et une ombre d'un pixel au plus.
   Rien ne se souleve au survol.
4. **Les chiffres s'alignent.** `font-variant-numeric:tabular-nums` partout ou
   des nombres se lisent en colonne. C'est cette propriete qui les aligne, pas
   la chasse fixe -- `--mono` est reserve a ce qui EST du code.
5. **Aucune police tierce.** `--sans` suffit. Un serveur auto-heberge qui irait
   chercher sa police chez Google ferait fuiter l'adresse IP de chaque
   visiteur, et s'afficherait mal des que la machine est hors ligne.

### Le squelette d'une page

```html
<link rel="stylesheet" href="/theme.css">
<style>
  body{background:var(--bg);color:var(--txt);font:var(--t-b)/1.5 var(--sans)}
  .carte{background:var(--surface);border:1px solid var(--line);
         border-radius:var(--r);padding:14px 16px}
  .etat{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;
        border-radius:999px;font-size:var(--t-xs);font-weight:700;
        color:var(--ok);background:var(--ok-bg)}
</style>
```

`workspace/diagnostic/app.py` est l'exemple de reference : c'est une
application comme les tiennes, et elle se lit comme une page du panneau.

**Le theme clair / sombre suit le systeme.** Le choix manuel fait dans le
panneau vit dans le stockage de SON origine, que ton application ne peut pas
lire -- c'est cette separation qui l'empeche aussi de lire la session. Ne
cherche pas a la contourner.

## Configuration et secrets

- **Aucun mot de passe, jeton ou cle en clair dans le code**, jamais.
- La configuration propre au projet va dans un `.env` **a cote du code**, lu par
  le projet lui-meme. Ne le versionne pas : ajoute `.env` au `.gitignore`.
- N'ecris jamais dans `credentials.env` (secrets de la stack) et ne recopie
  aucune de ses valeurs dans un fichier du projet.

## Chemins

Ecris des chemins **relatifs au projet**, jamais de `/workspace/...` en dur : le
meme code doit fonctionner si le dossier est renomme ou copie.

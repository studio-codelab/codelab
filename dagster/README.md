# codelab-dagster

L'orchestrateur des jobs de `/workspace`. Un seul conteneur fait tourner les deux programmes de
Dagster, lancés par `lancer.sh` :

- l'**interface web** (`dagster-webserver`), au premier plan ;
- le **daemon** (plannings, capteurs, file des runs), relancé s'il s'arrête.

Dagster n'a aucune authentification. Ce conteneur ne publie donc **aucun port** : l'interface passe
par [`codelab-dagster-proxy`](proxy/README.md) (`:3000`), qui exige la session d'administration du
panneau.

## Fichiers

| Fichier | Rôle |
|---|---|
| `Dockerfile` | `python:3.13-slim` ; `dagster` et `dagster-webserver` en `DAGSTER_VERSION`, `dagster-postgres` sans version (elle suit son propre schéma), `sqlalchemy<2.1` ; utilisateur `dagster` (uid 1002) |
| `entrypoint.sh` | mot de passe Postgres, `dagster.yaml`, squelette de `/workspace`, permissions, bascule sur l'utilisateur `dagster` |
| `lancer.sh` | lance le daemon et l'interface, transmet l'arrêt aux deux |
| `isolation.py` | environnements des projets isolés et `workspace.yaml` |
| `dagster.yaml` | stockage des runs, événements et plannings dans la base Postgres `dagster` ; télémétrie coupée |
| `dagster.yaml.sums`, `squelette.sums` | empreintes figées des versions anciennes, pour reconnaître un fichier non modifié |

## Au démarrage

1. **Secret Postgres.** L'entrypoint lit `POSTGRES_PASSWORD` dans `credentials.env` et l'exporte en
   `DAGSTER_PG_PASSWORD`.
2. **`dagster.yaml`.** Il est déposé s'il manque, puis mis à jour tant que personne ne l'a modifié.
3. **Squelette de `/workspace`.** Il est déposé au premier démarrage : `README.md`, `definitions.py`,
   `.vscode/` et `diagnostic/`.
   - À chaque démarrage, un fichier dont l'empreinte n'a pas changé est remplacé par la version de
     l'image (empreintes dans `/workspace/.codelab/empreintes`).
   - Un fichier modifié est laissé tel quel, et rien n'est recréé.
4. **Permissions.** Groupe `codelab`, setgid et ACL par défaut sur `/workspace`, la première fois.
5. **Projets isolés** (`isolation.py`), voir plus bas.
6. **Lancement** sous l'uid 1002 :
   - sans projet isolé : `dagster-webserver -m definitions` et `dagster-daemon run -m definitions` ;
   - avec : `-w $DAGSTER_HOME/workspace.yaml`.

## Chargement du code

`PYTHONPATH=/workspace` : le module `definitions` est `/workspace/definitions.py`, l'agrégateur. Il
importe le `definitions.py` de chaque projet qui expose `defs`, puis fusionne le tout.

- Un projet en erreur est ignoré, avec sa trace dans les journaux.
- Les dossiers qui commencent par `.` ou `_` sont ignorés.
- Les projets isolés ne sont pas chargés ici.

## Projets isolés

Un projet qui porte un `requirements-dagster.txt` a son propre environnement Python et son propre
emplacement de code. `isolation.py`, lancé par `lancer.sh` :

1. **Construit `<projet>/.dagster-venv/`** (`python -m venv`, puis `pip install -r`), seulement si
   l'empreinte (contenu du fichier et version de Python) a changé. Un environnement qui échoue est
   supprimé : l'emplacement s'affiche alors en erreur, au lieu de charger des versions non demandées.
2. **Relie l'environnement aux paquets de l'image** par un fichier `.pth`, placé après ceux du
   projet. Dagster n'est donc pas réinstallé, et une version demandée l'emporte.
3. **Écrit `$DAGSTER_HOME/workspace.yaml`** :
   - l'emplacement commun (`python_module: definitions`), qui garde le même nom, donc les mêmes
     plannings et le même historique ;
   - un emplacement `python_file` par projet isolé.

Sans projet isolé, le fichier est supprimé et rien ne change. Si `isolation.py` est absent ou
échoue, Dagster démarre sans isolation.

**Coût** : l'interface et le daemon lancent chacun un serveur de code par emplacement, soit 200 à
300 Mo par projet isolé, sous un plafond de 896 Mo. Au-delà d'un projet isolé, remonte `mem_limit`.

## Variables et volumes

| Variable | Rôle |
|---|---|
| `DAGSTER_HOME` | `/opt/dagster/home` : `dagster.yaml`, `workspace.yaml`, état local |
| `PYTHONPATH` | `/workspace` |
| `DAGSTER_PG_HOST`, `_PORT`, `_DB`, `_USER` | stockage Postgres (`DAGSTER_PG_PASSWORD` est posé par l'entrypoint) |
| `CODELAB_ENV_FILE` | `credentials.env` |
| `CODELAB_ISOLATION_DELAI` | délai d'installation d'un environnement isolé (900 s) |

| Volume | Contenu |
|---|---|
| `/opt/dagster/home` | `DAGSTER_HOME` |
| `/workspace` | les projets |
| `/var/lib/codelab/config` (lecture seule) | `credentials.env` |

## Commandes utiles

```bash
docker restart codelab-dagster              # recharger le code et reconstruire les environnements isolés
docker logs codelab-dagster | grep workspace   # projets chargés ou ignorés
```

**`docker exec` ne passe pas par l'entrypoint** : `DAGSTER_PG_PASSWORD` n'y existe pas. Pour une
commande ponctuelle, fournis-la toi-même :

```bash
docker exec -w /workspace codelab-dagster sh -c \
  'DAGSTER_PG_PASSWORD=$(sed -n "s/^POSTGRES_PASSWORD=//p" /var/lib/codelab/config/credentials.env | tail -n1) \
   dagster asset materialize -m definitions --select <asset>'
```

## Tester localement

```bash
docker build -f dagster/Dockerfile -t codelab-dagster-test .
docker compose up codelab-postgres codelab-dagster        # test complet, avec stockage Postgres
```

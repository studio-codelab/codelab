# codelab-postgres

L'image officielle `postgres:18`, plus un entrypoint versionne. Le serveur Postgres partage par tous les
services CodeLab : `codelab-dev`, `codelab-dagster`, `codelab-dagster-daemon` s'y connectent, et le panneau
`codelab-app-manager` cohabite avec lui dans le meme `credentials.env`.

Pas de port publie : la base n'est joignable que depuis le reseau `codelab`.

## Pourquoi cette image existe

Le script d'initialisation vivait directement dans le `docker-compose.yml`, en `entrypoint:` inline. Deux
problemes se sont succede :

1. Docker Compose interpole les `$VAR` d'un script inline **avant** que le shell ne les voie. `$ENV_FILE`,
   `$name`, `$content` arrivaient vides, le script echouait des `touch ""`, et `set -e` le faisait sortir
   avant de lancer Postgres. Corrige en doublant les `$`.
2. Sauf qu'une interface d'installation qui **reecrit le compose a l'import** annule cet echappement. Les
   `$$` redevenaient `$`, Compose les vidait a nouveau, et Postgres repartait en boucle de redemarrage
   (`mkdir: cannot create directory ''`).

Un fichier copie dans une image ne traverse aucune de ces deux reecritures. D'ou la regle du projet :
**aucune logique shell dans le `docker-compose.yml`** — elle vit dans un entrypoint versionne.

## Ce que fait l'entrypoint

Avant de rendre la main a `docker-entrypoint.sh` de l'image officielle :

1. **Resout le mot de passe**, dans cet ordre de priorite :
   - la valeur deja presente dans `credentials.env` (c'est celle avec laquelle la base a ete initialisee) ;
   - sinon l'ancien fichier `config/postgres_password` d'une installation anterieure, qui est ensuite
     supprime (migration) ;
   - sinon une valeur generee (32 octets aleatoires en hexadecimal).

   Une valeur existante n'est **jamais** regeneree par-dessus : la base refuserait la connexion.

2. **Ecrit ses blocs dans `credentials.env`** (`codelab-header`, `codelab-postgres`, `codelab-dev`),
   delimites par des marqueurs `# ===== <nom> =====`. Chaque service ne touche qu'a son propre bloc, les
   autres restent intacts quel que soit l'ordre de demarrage.

3. **Exporte `POSTGRES_PASSWORD`** et retire `POSTGRES_PASSWORD_FILE` — l'image officielle refuse les deux
   en meme temps.

4. **Provisionne les bases de projet** en tache de fond, une fois le serveur a l'ecoute (voir plus bas).

Le fichier est relu a chaque demarrage, et l'operation est idempotente : redemarrer le conteneur ne duplique
aucun bloc et ne change aucune valeur.

## Les bases

Il n'y a **pas de base fourre-tout**. Le decoupage :

| Base | Contenu | Creee par |
|---|---|---|
| `dagster` | Tables d'instance de Dagster : runs, evenements, planifications | `POSTGRES_DB`, a l'initialisation du cluster |
| `diagnostic` | Le projet livre en modele, tables dans son schema `dagster` | L'entrypoint, a chaque demarrage |
| `<projet>` | Une base par projet, meme structure | L'entrypoint (`CODELAB_PROJECT_DBS`) ou `codelab db` |
| ~~`postgres`~~ | Base de maintenance livree par `initdb` — **supprimee au demarrage** | — |

Chaque base de projet recoit un schema `dagster` et un `search_path` par defaut
(`ALTER ROLE ... IN DATABASE ... SET search_path TO dagster, public`) : un `CREATE TABLE ma_table` dans un
asset y atterrit sans prefixe dans le code.

La base `postgres` que cree `initdb` est supprimee : CodeLab ne s'en sert pas. Son role de point d'entree —
il faut etre connecte a une base pour en creer une autre — est tenu par la base d'instance `dagster`, qui
existe toujours ; c'est elle que visent le healthcheck, le provisionnement et `codelab db`.

Deux garde-fous, parce qu'une base supprimee ne revient pas : elle est **conservee** si elle contient le
moindre objet utilisateur (quelqu'un a pu y ranger des donnees avant cette version), et l'echec de la
suppression n'est jamais fatal — une session encore connectee dessus, par exemple, la fait simplement
reessayer au demarrage suivant. Les deux cas sont tracés dans `docker logs codelab-postgres`.

**A savoir** : un outil qui se connecte a `postgres` par defaut (`psql` sans `-d` depuis un autre
conteneur, `createdb`, pgAdmin) doit desormais nommer une base explicitement, par exemple `-d dagster`.

**Pourquoi pas `/docker-entrypoint-initdb.d`** : ce dossier n'est joue qu'a la toute premiere
initialisation du cluster. Les donnees survivent aux reinstallations, donc une stack existante ne le
rejouerait jamais et un projet ajoute plus tard n'aurait pas sa base. Le provisionnement de l'entrypoint
tourne a chaque demarrage et ne fait rien quand tout est deja en place (`CREATE DATABASE` n'acceptant pas
`IF NOT EXISTS`, il est precede d'un test sur `pg_database`).

Pour un projet cree apres coup, sans redemarrer la stack, depuis une session SSH :

```bash
codelab db mon-projet
```

### Migration depuis la base unique `codelab`

Les versions anterieures rangeaient tout dans une base unique `codelab` : les tables d'instance de Dagster
dans son schema `dagster`, chaque projet dans un schema a son nom. Sur une installation existante, ce
decoupage-ci repart de bases neuves — **l'historique des runs et l'etat des schedules ne suivent pas**, et
les tables d'un projet restent dans l'ancienne base. Rien n'est supprime : la base `codelab` est laissee
telle quelle.

Si l'historique compte, sauvegarder avant de redemarrer la stack :

```bash
docker exec codelab-postgres pg_dump -U codelab codelab > codelab-avant-migration.sql
```

Puis, pour recuperer les tables d'un projet dans sa nouvelle base :

```bash
docker exec codelab-postgres pg_dump -U codelab -n diagnostic codelab \
  | docker exec -i codelab-postgres psql -U codelab -d diagnostic
```

Le schema garde son nom d'origine a l'arrivee (`diagnostic`), alors que le projet cherche maintenant ses
tables dans `dagster`. Le schema `dagster` cree par le provisionnement etant vide a ce stade, on le retire
avant de renommer l'autre a sa place :

```sql
DROP SCHEMA dagster;                        -- vide : echoue s'il ne l'est pas, c'est voulu
ALTER SCHEMA diagnostic RENAME TO dagster;
```

## Variables d'environnement

| Variable | Role |
|---|---|
| `CODELAB_CONFIG_DIR` | Dossier de `credentials.env` (defaut `/var/lib/codelab/config`) |
| `POSTGRES_DB`, `POSTGRES_USER` | Lues par l'image officielle, fixees dans le compose. `POSTGRES_DB` est la base d'instance de Dagster (`dagster`) |
| `CODELAB_PROJECT_DBS` | Bases de projet a creer au demarrage, separees par des espaces (defaut `diagnostic`) |
| `CODELAB_PROJECT_SCHEMA` | Schema pose dans chaque base de projet (defaut `dagster`) |

Pas de `POSTGRES_PASSWORD` ni `POSTGRES_PASSWORD_FILE` dans le compose : l'entrypoint s'en charge.

## Volumes attendus

| Chemin conteneur | Contenu |
|---|---|
| `/var/lib/postgresql` | Donnees de la base |
| `/var/lib/codelab/config` | En lecture-ecriture : c'est ce service qui cree `credentials.env` |

## Sauvegardes, et comment restaurer

La stack se sauvegarde toute seule, une fois par jour, dans
`/DATA/AppData/codelab/sauvegardes` sur l'hote. **Deux services y ecrivent, et chacun sauvegarde ce
qu'il est seul a pouvoir sauvegarder :**

| Fichier | Ecrit par | Contenu |
|---|---|---|
| `base-<nom>-<date>.dump` | `codelab-postgres` | une base, au format custom (`pg_dump -Fc`) |
| `panneau-<date>.tar.gz` | `codelab-app-manager` | l'etat du panneau : comptes, applications declarees, categories, journal des acces |

Pourquoi ce partage : **`pg_dump` refuse de sauvegarder un serveur plus recent que lui**, et le panneau
tourne sur une image dont le client Postgres a une version de retard. Ce conteneur-ci porte le `pg_dump`
de la version exacte du serveur et parle a la base par la socket locale — pas de mot de passe a promener,
pas de depot apt tiers a ajouter dans l'image qui detient les secrets.

Chaque fichier est **relu avant d'etre garde** (`pg_restore --list` pour les bases, une relecture complete
de l'archive pour le panneau). Un fichier tronque — disque plein, arret au mauvais moment — est ecarte sur
place et l'ancienne sauvegarde reste. Sept exemplaires sont conserves par base et pour le panneau.

Reglages, dans `docker-compose.yml` : `CODELAB_SAUVEGARDE_HEURES` (24 par defaut, `0` desactive) et
`CODELAB_SAUVEGARDES_GARDEES` (7). Cote panneau : `APP_MANAGER_SAUVEGARDE_HEURES` et
`APP_MANAGER_SAUVEGARDES_GARDEES`. La sonde **sauvegardes** du diagnostic surveille leur fraicheur : elle
passe en alerte a 26 h sans nouvelle sauvegarde, et en erreur a 72 h.

### Restaurer une base

```bash
ls -lh /DATA/AppData/codelab/sauvegardes/          # choisir le fichier voulu

# Dans une base VIDE, ou en ecrasant ce qui existe (--clean --if-exists).
docker exec -i codelab-postgres pg_restore -U codelab -d diagnostic \
  --clean --if-exists /sauvegardes/base-diagnostic-20260115-030000.dump
```

Pour restaurer a cote sans toucher a l'existante, creer d'abord la base cible :

```bash
docker exec codelab-postgres createdb -U codelab diagnostic_restaure
docker exec codelab-postgres pg_restore -U codelab -d diagnostic_restaure \
  /sauvegardes/base-diagnostic-20260115-030000.dump
```

Inspecter le contenu d'une sauvegarde sans rien ecrire — c'est exactement la verification que fait
l'entrypoint apres chaque dump :

```bash
docker exec codelab-postgres pg_restore --list /sauvegardes/base-diagnostic-20260115-030000.dump
```

### Restaurer l'etat du panneau

Le panneau doit etre **arrete** : il garde des fichiers ouverts, et les reecrit au fil de l'eau.

```bash
docker compose stop codelab-app-manager

# Voir ce que l'archive contient avant de l'ouvrir.
tar -tzf /DATA/AppData/codelab/sauvegardes/panneau-20260115-030000.tar.gz

# Mettre l'existant de cote, puis restaurer.
mv /DATA/AppData/codelab/app-manager /DATA/AppData/codelab/app-manager.avant-restauration
mkdir -p /DATA/AppData/codelab/app-manager
tar -xzf /DATA/AppData/codelab/sauvegardes/panneau-20260115-030000.tar.gz \
  -C /DATA/AppData/codelab/app-manager

docker compose start codelab-app-manager
```

**Ce que l'archive ne contient pas**, et c'est voulu : `credentials.env` (le mot de passe du panneau, celui
de Postgres et la cle de session) vit dans `/DATA/AppData/codelab/config`, pas dans le dossier d'etat.
L'archive se recopie donc ailleurs sans diffuser les secrets avec elle — mais il faut sauvegarder ce
fichier **separement**, et le garder ailleurs que sur la machine :

```bash
cp /DATA/AppData/codelab/config/credentials.env ~/codelab-credentials.env
chmod 600 ~/codelab-credentials.env
```

Sans lui, les bases restaurees restent lisibles (le mot de passe se regenere), mais les sessions ouvertes
tombent et le mot de passe du panneau change.

### Emporter les sauvegardes ailleurs

Une sauvegarde sur le meme disque que la donnee ne protege que de l'erreur de manipulation, pas de la
panne de disque. Le dossier entier se copie d'un seul geste :

```bash
rsync -a --delete /DATA/AppData/codelab/sauvegardes/ ailleurs:/sauvegardes-codelab/
```

## Monter de version

Changer le tag `FROM` dans `postgres/Dockerfile`, puis pousser un tag Git — les quatre images sortent
ensemble. **Attention** : un saut de version majeure demande une migration du repertoire de donnees
(`pg_upgrade` ou dump/restore). Ce n'est pas un simple changement de tag, et un demarrage sur un repertoire
d'une version anterieure echoue avec un message explicite dans les logs.

## Tester localement

```bash
docker build -f postgres/Dockerfile -t codelab-postgres-test .
docker run --rm -e POSTGRES_DB=dagster -e POSTGRES_USER=codelab \
  -v /tmp/codelab-config:/var/lib/codelab/config codelab-postgres-test
cat /tmp/codelab-config/credentials.env
```

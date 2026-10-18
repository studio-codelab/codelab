# codelab-postgres

L'image officielle `postgres:18` avec pgvector, et un entrypoint versionné. C'est le serveur partagé
par toute la stack. Il n'a **aucun port publié** : seul le réseau `codelab` le joint.

## Ce que fait l'entrypoint

1. **Mot de passe.** Il reprend celui de `credentials.env`, sinon il en génère un. Une valeur
   existante n'est jamais régénérée. La reprise d'un ancien `config/postgres_password`, et le retrait
   de l'ancien bloc `codelab-dev`, sont partis ; dernière version qui la portait : le commit `f9caf52`. Une installation qui n'a pas démarré depuis
   longtemps passe d'abord par celle-là, puis par la version courante.
2. **`credentials.env`.** Il écrit son bloc `codelab-postgres` et le sommaire `codelab-header`, en
   tête du fichier. Le fichier est en `0600`, et chaque écriture se fait sous verrou `flock`.
3. **Bases de projet**, une fois le serveur à l'écoute : celles de `CODELAB_PROJECT_DBS` sont créées,
   chacune avec son schéma `dagster`, son `search_path` et l'extension `vector`.
4. **Une base, un compte.** Chaque base de projet reçoit son propre compte, du même nom, seul
   (avec l'administrateur) à pouvoir s'y connecter. Les tables existantes passent à ce compte, et
   les bases `dagster` et `codelab` sont fermées à tout autre compte que l'administrateur. Le travail
   est dans `proteger-base.sql`, joué aussi par le panneau et par le poste de développement.
5. **Base `postgres`.** Celle livrée par `initdb` est supprimée, sauf si elle contient des données.
6. **Sauvegardes** quotidiennes, chacune relue puis restaurée à l'essai (voir plus bas).

**Aucune logique shell dans le compose** : un script inline y était réécrit par Compose et par les
interfaces d'import (CasaOS). Il vit donc dans l'image.

## Les bases

| Base | Contenu |
|---|---|
| `dagster` | tables d'instance de Dagster (runs, événements, plannings) ; c'est aussi le point d'entrée de maintenance |
| `codelab` | copie de l'historique du panneau (journal, comptes sans secret) |
| `diagnostic`, `<projet>` | une base par projet, tables dans son schéma `dagster` |

- **Une base de projet** se crée sans redémarrer : `codelab create mon-projet` ou `codelab update mon-projet` (ou `codelab db mon-projet`, la base seule), depuis `codelab-dev`,
  ou d'elle-même au lancement de l'application par le panneau.
- **Son compte** porte son nom. Son mot de passe se dérive du mot de passe administrateur
  (`POSTGRES_PASSWORD`) : `sha256(<mot de passe>:codelab-base:<base>)`, en hexadécimal. Le panneau le
  donne à l'application, le poste au terminal du projet.
- **Les comptes** n'ont aucun privilège de plus : ni créer de base, ni de compte, ni contourner les
  droits. Ce que l'administrateur (Dagster) écrit ensuite dans une base de projet reste accessible à
  son compte (privilèges par défaut).
- **Un outil qui vise `postgres` par défaut** (pgAdmin, `createdb`) doit nommer une base, par exemple
  `-d dagster`.
- **pgvector** : l'extension `vector` est créée dans le schéma `public` de chaque base de projet.

```sql
CREATE TABLE documents (id bigserial PRIMARY KEY, texte text, plonge vector(1536));
CREATE INDEX ON documents USING hnsw (plonge vector_cosine_ops);
SELECT texte FROM documents ORDER BY plonge <=> $1 LIMIT 5;
```

## Variables et volumes

| Variable | Rôle |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER` | base d'instance (`dagster`) et administrateur (`codelab`, réservé à la stack) |
| `CODELAB_PROJECT_DBS` | bases de projet à créer au démarrage (défaut `diagnostic`) |
| `CODELAB_PROJECT_SCHEMA` | schéma posé dans chaque base (défaut `dagster`) |
| `CODELAB_CONFIG_DIR` | dossier de `credentials.env` |
| `CODELAB_SAUVEGARDE_HEURES` | intervalle des sauvegardes (24 ; `0` désactive) |
| `CODELAB_SAUVEGARDES_GARDEES` | nombre gardé par base (7) |

| Volume | Contenu |
|---|---|
| `/var/lib/postgresql` | les données |
| `/var/lib/codelab/config` | `credentials.env` (lecture-écriture) |
| `/sauvegardes` | les sauvegardes (`/DATA/AppData/codelab/sauvegardes` sur l'hôte) |

## Sauvegardes

Toutes dans `/DATA/AppData/codelab/sauvegardes` :

| Fichier | Écrit par | Fréquence | Gardés |
|---|---|---|---|
| `base-<nom>-<date>.dump` (`pg_dump -Fc`) | `codelab-postgres` | quotidienne | 7 par base |
| `panneau-<date>.tar.gz` | `codelab-app-manager` | quotidienne | 7 |
| `workspace-<date>.tar.gz` | `codelab-app-manager` | hebdomadaire | 4 |
| `verification-restauration.txt` | `codelab-postgres` | après chaque passage | — |

- **Relecture** : chaque fichier est relu avant d'être gardé (`pg_restore --list`, ou relecture
  complète de l'archive). Un fichier tronqué est écarté, et l'ancienne sauvegarde reste.
- **Restauration d'essai** : la dernière sauvegarde de chaque base est restaurée dans une base
  jetable, puis on vérifie que chaque table annoncée existe. Une restauration ratée met le
  diagnostic au rouge.
- **Fraîcheur** : le diagnostic passe en alerte à 26 h sans nouvelle sauvegarde, en erreur à 72 h.
- **Pourquoi deux services** : `pg_dump` doit avoir la version exacte du serveur, et seul ce
  conteneur l'a.

**`credentials.env` n'est dans aucune archive.** Sauvegarde-le à part, hors du serveur :

```bash
sudo cp /DATA/AppData/codelab/config/credentials.env ~/codelab-credentials.env && chmod 600 ~/codelab-credentials.env
```

**Emporter les sauvegardes ailleurs** (une sauvegarde sur le même disque ne protège pas d'une panne
de disque) :

```bash
rsync -a --delete /DATA/AppData/codelab/sauvegardes/ ailleurs:/sauvegardes-codelab/
```

### Restaurer une base

```bash
ls -lh /DATA/AppData/codelab/sauvegardes/

# Écraser la base existante :
docker exec -i codelab-postgres pg_restore -U codelab -d diagnostic \
  --clean --if-exists /sauvegardes/base-diagnostic-20260115-030000.dump

# Ou restaurer à côté :
docker exec codelab-postgres createdb -U codelab diagnostic_restaure
docker exec codelab-postgres pg_restore -U codelab -d diagnostic_restaure \
  /sauvegardes/base-diagnostic-20260115-030000.dump

# Restaurer ailleurs que sur ce serveur (un compte de projet peut y manquer) :
# ajoute --no-owner ; au démarrage suivant, codelab-postgres rend les tables
# au compte du projet.

# Voir le contenu sans rien écrire :
docker exec codelab-postgres pg_restore --list /sauvegardes/base-diagnostic-20260115-030000.dump
```

### Restaurer l'état du panneau

Le panneau doit être arrêté.

```bash
docker compose stop codelab-app-manager
tar -tzf /DATA/AppData/codelab/sauvegardes/panneau-20260115-030000.tar.gz      # voir le contenu
mv /DATA/AppData/codelab/app-manager /DATA/AppData/codelab/app-manager.avant-restauration
mkdir -p /DATA/AppData/codelab/app-manager
tar -xzf /DATA/AppData/codelab/sauvegardes/panneau-20260115-030000.tar.gz \
  -C /DATA/AppData/codelab/app-manager
docker compose start codelab-app-manager
```

Sans le `credentials.env` d'origine :

- les réglages chiffrés (adresses, serveur d'envoi) sont illisibles ;
- les sessions tombent ;
- le mot de passe du panneau change.

### Restaurer un projet de `/workspace`

```bash
mkdir -p /tmp/r && tar -xzf /DATA/AppData/codelab/sauvegardes/workspace-<date>.tar.gz -C /tmp/r ./mon-projet
```

Puis recopie ce qu'il faut. Les dépendances et les builds ne sont pas dans l'archive : relance le
build.

## Monter de version

1. Change le tag `FROM` dans `postgres/Dockerfile`, **et** le paquet
   `postgresql-<majeure>-pgvector`. Un test vérifie qu'ils correspondent.
2. Une version **majeure** demande une migration des données (`pg_upgrade`, ou dump puis
   restauration) : un simple changement de tag ne suffit pas.
3. Démarrer sur des données d'une version antérieure échoue avec un message explicite.

## Tester localement

```bash
docker build -f postgres/Dockerfile -t codelab-postgres-test .
docker run --rm -e POSTGRES_DB=dagster -e POSTGRES_USER=codelab \
  -v /tmp/codelab-config:/var/lib/codelab/config codelab-postgres-test
```

# CodeLab

Environnement de développement personnel, auto-hébergé sur son propre serveur. Une seule
installation fournit une base de données partagée, un Redis, un envoi de mails, un accès SSH/VS Code, un orchestrateur de jobs et
un gestionnaire d'applications — un `docker-compose.yml`, rien d'autre à installer sur l'hôte que
Docker.

On écrit son code dans `/workspace`, on le déclare dans le panneau, il est en ligne. C'est tout le
propos.

## Services

| Service | Rôle | Port |
|---|---|---|
| **Postgres** | Serveur de bases partagé : une base par projet, `pgvector` inclus | interne |
| **Redis** | Cache, sessions, files de tâches et compteurs, partagé par les applications | interne |
| **Mailpit** | Envoi de mails des applications : les garde consultables, les relaie par ton serveur d'envoi | `8025` (via le proxy) |
| **Dev** | Accès SSH et VS Code Remote-SSH | `2222` |
| **Dagster** | Orchestration des jobs : l'interface, et le daemon qui déclenche plannings et capteurs | interne |
| **Dagster-proxy** | Authentification devant Dagster et Mailpit | `3000`, `8025` |
| **App-manager** | Le panneau : déploiement et supervision des applications | `9001` |
| **Applications** | Les projets déployés, servis sur leur propre adresse | `9002` |

Aucune configuration manuelle après l'installation : les identifiants de base de données et les clés
SSH sont générés au premier démarrage, et les connexions à Postgres et à Redis sont déjà prêtes dans
le conteneur `dev`.

## Prérequis

| | |
|---|---|
| Mémoire | **4 Go** — la cible du dimensionnement |
| Processeur | **2 cœurs** |
| Disque | ~3 Go pour les images, plus ce que pèsent vos projets |
| Docker | Docker Engine avec le plugin Compose |

Les limites de chaque conteneur sont calculées pour cette machine : aucun service ne peut prendre
à lui seul plus du tiers de la mémoire, pour que le panneau reste joignable même quand une
application part en boucle — sans quoi on ne peut plus l'arrêter depuis le panneau. Deux tests
vérifient que ces plafonds restent cohérents avec la cible annoncée.

Sur une machine plus grande, remonter l'ensemble proportionnellement dans les deux fichiers
`docker-compose*.yml`, et la constante `RAM_CIBLE_MO` de `workspace/diagnostic/checks.py`.

## Installation

```bash
git clone https://github.com/studio-codelab/codelab.git && cd codelab
docker compose up -d
```

L'accès SSH s'ouvre en déposant une clé publique dans le dossier de configuration, **un fichier par
machine** : autoriser un ordinateur, c'est y déposer un fichier ; lui retirer l'accès, c'est le
supprimer. Tant qu'aucune clé n'est déposée, aucune connexion SSH n'est possible — c'est voulu. Le
panneau et Dagster, eux, restent joignables sans SSH.

Les données vivent en dehors des conteneurs et survivent donc aux mises à jour d'image. Pour les
ranger ailleurs, il suffit de changer les chemins hôte des volumes dans `docker-compose.yml`.

Le nom du projet Compose est fixé à `codelab` dans les deux fichiers. Sans cela, il serait déduit du
dossier d'où l'on démarre — `codelab-main` pour l'archive ZIP de GitHub, ou un nom du genre
`compose-d4601441b33edb0f` pour une interface qui recopie le fichier chez elle, comme CasaOS, qui en
fait aussi le nom du dossier de l'application.

> **Si votre installation existe déjà sous un autre nom de projet**, faites `docker compose down`
> avant la mise à jour, puis `up -d`. Compose repère ses conteneurs par un label de projet : sous un
> nouveau nom il voudrait en recréer, alors que les noms de conteneurs sont pris. Les données ne
> bougent pas — elles sont dans `/DATA/AppData/codelab/`, pas dans des volumes nommés par le projet.

Si le serveur expose une interface d'installation par collage de compose (les app stores de type
CasaOS, par exemple), `docker-compose-casaos.yml` est le même fichier avec les métadonnées
d'affichage que ces interfaces savent lire.

Ce jumeau ne porte **aucun commentaire**, et c'est délibéré : il se colle dans une zone de texte,
il ne se lit pas. La même explication écrite à deux endroits finit par diverger, et c'est la copie
qu'on ne lit jamais qui se met à mentir. Tout ce qu'il y a à dire est donc dans
`docker-compose.yml`, commenté ligne à ligne, et dans les README ; celui-ci ne porte que les
valeurs. Un test (`test_le_compose_casaos_ne_porte_aucun_commentaire`) tient la règle dans le sens
où elle se perd : on ajoute un commentaire par réflexe, on ne l'enlève jamais par hasard.

> **En le collant, remplacer TOUT le contenu de la zone** (Ctrl+A, puis coller). Un collage inséré
> au curseur laisse la fin de l'ancienne version derrière lui : les deux fichiers se retrouvent
> soudés, et l'interface refuse le résultat avec une erreur YAML qui désigne une ligne sans rapport
> — `Map keys must be unique`, parce que `networks:` apparaît alors deux fois.

## Utilisation

**SSH et VS Code**

```bash
ssh vscode@<IP-du-serveur> -p 2222
```

Le code vit dans `/workspace`. La connexion à Postgres ne demande aucune configuration :

```bash
python3 -c "import psycopg; print(psycopg.connect().execute('SELECT version();').fetchone()[0])"
```

**Les bases de données.** Pas de base fourre-tout : chaque projet a la sienne, nommée comme son
dossier, avec un schéma `dagster` dedans pour les tables de ses assets. Une session SSH arrive
directement dans la base du projet modèle ; celle d'un projet créé après coup s'ajoute avec
`codelab db mon-projet`.

**Redis.** Un serveur partagé, pour ce qui vit à la vitesse des requêtes : cache, sessions, files
de tâches, limitation de débit. Les applications lancées par le panneau reçoivent `REDIS_URL` ; une
session SSH aussi, et `redis-cli -h codelab-redis` s'y connecte sans mot de passe à taper. Trois
règles, détaillées dans [`redis/README.md`](redis/README.md) : **préfixer ses clés** par le nom de
l'application, **donner une durée de vie** à ce qui est du cache, et garder dans Postgres ce qui ne
doit pas se perdre.

**Les e-mails.** Une application envoie ses mails à Mailpit (`MAIL_HOST`, `MAIL_PORT`, transmis par
le panneau), sans identifiants. Chaque mail reste consultable sur `http://<IP-du-serveur>:8025/`
(session d'administration du panneau) et part par le serveur d'envoi réglé dans *Paramètres >
E-mail* — le même que pour les alertes, un simple compte Gmail suffit. Détails et mode test :
[`mailpit/README.md`](mailpit/README.md).

**Le panneau** — `http://<IP-du-serveur>:9001/`. Démarrer et arrêter ses applications, les
déployer, consulter leurs journaux. L'accueil est un **hub** : la liste des projets ouvrables,
chacun avec sa description d'une ligne. Le parcours complet, du dossier vide à l'application en
ligne, est décrit dans [`dev/README.md`](dev/README.md).

Ce qui se règle depuis le panneau, quand la stack sert à plusieurs ou qu'on ne veut plus la
surveiller à l'œil :

- **Comptes utilisateurs** — des comptes nommés qui n'ouvrent que les projets qu'on leur autorise,
  sans rien pouvoir administrer. Chaque compte porte une adresse mail, et si un serveur d'envoi est
  configuré, la page de connexion propose de créer un compte — sans qu'il ouvre le moindre projet
  tant qu'on ne l'y autorise pas.
- **Clés d'accès (passkeys)** — se connecter avec l'empreinte ou le code de son appareil. Demande
  une connexion chiffrée et un nom de domaine ; le panneau le dit plutôt que d'afficher un bouton
  qui échouerait.
- **Journal des accès** — qui s'est connecté, quand, et quelle application il a ouverte. De quoi
  savoir si un projet sert encore avant de l'arrêter.
- **Alertes par mail** — un message quand une application ne redémarre plus, un autre quand elle
  revient.
- **Si le serveur entier tombe** — le panneau appelle toutes les 5 minutes une adresse de
  surveillance (healthchecks.io, gratuit) ; quand les appels cessent, c'est le service extérieur qui
  t'écrit. À régler dans *Paramètres > Alertes*.
- **Catégories** — des tiroirs pour ranger les projets dans le hub. Purement visuel : une catégorie
  ne donne aucun droit.
- **Applications du hub** — ce que *ton* hub affiche, compte par compte. Cochée, elle est affichée.
  **Dagster et Mailpit en font partie** : sur une installation où l'on n'ouvre jamais l'orchestrateur, sa tuile
  se range comme les autres. C'est du rangement, pas une fermeture : le service continue de tourner
  et son adresse répond toujours.

**Dagster** — `http://<IP-du-serveur>:3000/`. Charge `/workspace/definitions.py` comme code
Dagster. Il n'a pas d'identifiants à lui : il utilise la session du panneau, donc la même connexion
et la même déconnexion.

**Agents** — `codelab agents`, dans un projet, y écrit le mode d'emploi de la stack sous forme d'un
`AGENTS.md` que `codex` lit avant chaque tâche. Voir [`dev/README.md`](dev/README.md#codex).

## Où vit la configuration

Trois mondes, et chacun a ses fichiers. **Rien n'est à saisir pour que la stack tourne** : les mots
de passe et les clés sont générés au premier démarrage. La seule valeur à fournir est, pour SSH,
une clé publique par machine.

### 1. Le serveur — ce qui fait tourner la stack

Tout vit dans `/DATA/AppData/codelab/config/`, en dehors des conteneurs.

| Fichier | Contenu | Écrit par | Lu par |
|---|---|---|---|
| `credentials.env` | identifiants des services, **un bloc par service** ; un sommaire en tête dit qui écrit quoi | les services, à leur démarrage | les services ; une partie est passée aux apps (voir 3) |
| `ssh/authorized_keys.d/` | une clé publique par machine autorisée | toi | `codelab-dev` |

On n'édite `credentials.env` qu'en y **ajoutant** un bloc à soi : chaque bloc d'un service est
réécrit à son démarrage. Détails : [`workspace/README.md`](workspace/README.md#credentialsenv).

### 2. Le poste de développement — VS Code, Codex, SSH

Ce qui sert à **écrire** du code, dans le conteneur `codelab-dev`. Détails :
[`dev/README.md`](dev/README.md).

| Où | Contenu |
|---|---|
| `/workspace/.codex/` | configuration de Codex (`config.toml`), sa connexion au compte ChatGPT (`auth.json`, en `0600`), le manuel `AGENTS.md` |
| `~/.vscode-server` (→ `/DATA/AppData/codelab/vscode-server`) | le serveur VS Code et ses extensions, conservés aux mises à jour |
| profil de session (`/etc/profile.d/`) | variables `PG*` pour `psql`, `REDIS_URL` et `REDISCLI_AUTH` pour Redis, `CODEX_HOME` — posées à chaque connexion SSH |

Aucun de ces fichiers ne va aux applications déployées. **Codex se connecte avec un compte
ChatGPT** (bouton *Sign in* de l'extension, ou `codex login`).

### 3. Les applications — ce que le panneau lance

Ce qui sert à **faire tourner** un projet. Détails :
[`workspace/README.md`](workspace/README.md#configuration-et-secrets).

| Couche (de la plus faible à la plus forte) | Contenu |
|---|---|
| variables passées par le panneau | les blocs `codelab-postgres`, `codelab-redis` et `codelab-mailpit` de `credentials.env`, `PORT`, l'identité SSO (clé publique, émetteur, audience) — jamais le mot de passe du panneau, sa clé privée SSO ni la configuration d'envoi des alertes |
| `/workspace/<projet>/.env` | ce qui ne concerne que ce projet ; **l'emporte toujours** |
| réglages de l'app dans le panneau | commande de lancement, build, visibilité, adresse — stockés dans `app-manager/apps.json` |

**Un service externe** (paiement, API tierce…) s'appelle depuis l'application elle-même, avec
**une clé à elle** posée dans son `.env` — jamais commité. Une clé par application chez le
fournisseur garde la révocation et le suivi de consommation application par application.

## Contenu par défaut du workspace

Au tout premier démarrage, un squelette est déposé dans `/workspace` :

```text
/workspace/
├── README.md            <- conventions : anatomie d'un projet, secrets, base, dépendances
├── definitions.py       <- agrégateur Dagster : découvre les projets, ne pas modifier
└── diagnostic/          <- projet modèle ET outil de diagnostic de la stack
```

**Un projet est un dossier.** S'il contient un `definitions.py` exposant une variable `defs`,
Dagster le découvre tout seul au rechargement du code : il n'y a aucun fichier central à éditer pour
déclarer un nouveau projet. Un projet qui ne se charge pas est ignoré avec un message dans les
journaux, sans rendre les autres invisibles.

`diagnostic/` sert de modèle : il montre sur un cas qui fonctionne un asset Dagster, une application
web et un module partagé entre les deux. Le copier est le moyen le plus rapide de démarrer :

```bash
cp -r /workspace/diagnostic /workspace/mon-projet
```

**Rien n'est jamais écrasé.** Un fichier déjà présent sous le même nom est laissé tel quel, et la
copie n'a lieu qu'une fois : un projet supprimé ne réapparaît pas au redémarrage. `diagnostic/` est
aussi inscrit tout seul dans le panneau, en visibilité privée — une stack fraîche est donc
vérifiable sans aucune saisie.

## Pour aller plus loin

- **Développer un projet, le déployer, le dépanner** : [`dev/README.md`](dev/README.md).
- **L'état de l'installation** : le projet `diagnostic`, inscrit tout seul dans le panneau. Il dit si
  les services se parlent et si l'ensemble est correctement fermé. Il ne se supprime pas — sans lui,
  une installation n'a plus aucun moyen de se contrôler elle-même.
- **Le fonctionnement interne d'un service** : le `README.md` de son dossier —
  [`postgres/`](postgres/README.md), [`redis/`](redis/README.md), [`mailpit/`](mailpit/README.md), [`dev/`](dev/README.md), [`dagster/`](dagster/README.md),
  [`app-manager/`](app-manager/README.md).

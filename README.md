# CodeLab

Environnement de développement personnel, auto-hébergé sur son propre serveur. Une seule
installation fournit une base de données partagée, un accès SSH/VS Code, un orchestrateur de jobs et
un gestionnaire d'applications — un `docker-compose.yml`, rien d'autre à installer sur l'hôte que
Docker.

On écrit son code dans `/workspace`, on le déclare dans le panneau, il est en ligne. C'est tout le
propos.

## Services

| Service | Rôle | Port |
|---|---|---|
| **Postgres** | Serveur de bases partagé : une base par projet | interne |
| **Dev** | Accès SSH et VS Code Remote-SSH | `2222` |
| **Dagster** | Orchestration et planification de jobs | interne |
| **Dagster-proxy** | Authentification devant Dagster | `3000` |
| **App-manager** | Le panneau : déploiement et supervision des applications | `9001` |
| **Applications** | Les projets déployés, servis sur leur propre adresse | `9002` |

Aucune configuration manuelle après l'installation : les identifiants de base de données et les clés
SSH sont générés au premier démarrage, et la connexion à Postgres est déjà prête dans le conteneur
`dev`.

## Installation

```bash
git clone https://github.com/lucasrtn/codelab.git && cd codelab
docker compose up -d
```

L'accès SSH s'ouvre en déposant une clé publique dans le dossier de configuration, **un fichier par
machine** : autoriser un ordinateur, c'est y déposer un fichier ; lui retirer l'accès, c'est le
supprimer. Tant qu'aucune clé n'est déposée, aucune connexion SSH n'est possible — c'est voulu. Le
panneau et Dagster, eux, restent joignables sans SSH.

Les données vivent en dehors des conteneurs et survivent donc aux mises à jour d'image. Pour les
ranger ailleurs, il suffit de changer les chemins hôte des volumes dans `docker-compose.yml`.

Si le serveur expose une interface d'installation par collage de compose (les app stores de type
CasaOS, par exemple), `docker-compose-casaos.yml` est le même fichier avec les métadonnées
d'affichage que ces interfaces savent lire.

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
- **Catégories** — des tiroirs pour ranger les projets dans le hub. Purement visuel : une catégorie
  ne donne aucun droit.

**Dagster** — `http://<IP-du-serveur>:3000/`. Charge `/workspace/definitions.py` comme code
Dagster. Il n'a pas d'identifiants à lui : il utilise la session du panneau, donc la même connexion
et la même déconnexion.

**Agents** — `codelab agents`, dans un projet, y écrit le mode d'emploi de la stack sous forme d'un
`AGENTS.md` que `codex` lit avant chaque tâche. Voir [`dev/README.md`](dev/README.md#codex).

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
  [`postgres/`](postgres/README.md), [`dev/`](dev/README.md), [`dagster/`](dagster/README.md),
  [`app-manager/`](app-manager/README.md).

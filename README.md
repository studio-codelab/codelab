# CodeLab

Environnement de developpement personnel, auto-heberge sur ton propre serveur. Une seule
installation fournit une base de donnees partagee, un acces SSH/VS Code, un orchestrateur de jobs et
un gestionnaire d'applications -- un `docker-compose.yml`, rien d'autre a installer sur l'hote que
Docker.

Tu ecris ton code dans `/workspace`, tu le declares dans le panneau, il est en ligne. C'est tout le
propos.

| | |
|---|---|
| **Ce README** | ce que CodeLab fait, et comment s'en servir |
| [`TECHNIQUE.md`](TECHNIQUE.md) | comment c'est construit, expose, sauvegarde et durci |
| [`dev/README.md`](dev/README.md) | developper un projet et le mettre en ligne -- le travail quotidien |

## Services

| Service | Role | Port |
|---|---|---|
| **Postgres** | Serveur de bases partage : une base par projet, plus la base `dagster` | interne uniquement |
| **Dev** | Acces SSH + VS Code Remote-SSH, avec un utilisateur dedie | `2222` |
| **Dagster** | Orchestration et planification de jobs (interface web + daemon) | interne uniquement |
| **Dagster-proxy** | Authentification devant Dagster, qui n'en a aucune | `3000` |
| **App-manager** | Le panneau : deploiement et supervision de tes applications | `9001` |
| **Tes applications** | Servies par l'app-manager, sur une **origine a part** | `9002` |

Zero configuration manuelle apres l'installation : mot de passe de base de donnees genere automatiquement, cles
SSH generees et persistees, et connexion a Postgres deja prete dans l'environnement du conteneur `dev`.

## Installation

```bash
git clone https://github.com/lucasrtn/codelab.git && cd codelab
docker compose up -d

# puis autoriser ta machine en SSH : un fichier par ordinateur
sudo install -Dm644 ~/.ssh/id_ed25519.pub \
  /DATA/AppData/codelab/config/ssh/authorized_keys.d/mon-portable.pub
docker restart codelab-dev
```

1. **Rien a fournir au demarrage.** L'acces SSH s'ouvre en deposant une cle publique dans
   `config/ssh/authorized_keys.d/`, un fichier par machine ; `authorized_keys` en est **derive** a chaque
   demarrage. Autoriser une machine = y deposer un fichier ; lui retirer l'acces = le supprimer. Tant
   qu'aucune cle n'est deposee, aucune connexion SSH n'est possible — c'est voulu : la liste des machines
   autorisees se lit entierement dans ce dossier, et nulle part ailleurs. Le panneau (`9001`) et Dagster
   (`3000`) restent accessibles sans SSH.
2. Les donnees vivent sous `/DATA/AppData/codelab/` (voir [Persistance des donnees](TECHNIQUE.md#persistance-des-donnees)).
   Pour les ranger ailleurs, changer les chemins hote des volumes dans `docker-compose.yml` — ce sont des
   chemins litteraux, pas des variables.
3. Mot de passe Postgres, etat interne des services et cles hote SSH sont generes automatiquement au premier
   demarrage. Rien d'autre a configurer.

Si ton serveur expose une interface d'installation par collage de compose (les app stores de type CasaOS,
par exemple), utilise plutot `docker-compose-casaos.yml` : c'est le meme fichier, plus les metadonnees
d'affichage que ces interfaces savent lire — icone, titre, et une note recapitulant la marche a suivre
pour la cle SSH, les ports et l'emplacement des donnees, affichee avant l'installation.

## Utilisation

**SSH / VS Code**
```bash
ssh vscode@<IP-du-serveur> -p 2222
```
Ton code vit dans `/workspace`. La connexion Postgres ne demande aucune configuration :
```bash
python3 -c "import psycopg; print(psycopg.connect().execute('SELECT version();').fetchone()[0])"
```

**Les bases** : pas de base fourre-tout. `dagster` contient les tables d'instance de Dagster (runs,
evenements, planifications) ; chaque projet du workspace a la sienne, nommee comme son dossier
(`diagnostic` pour celui livre en modele), avec un schema `dagster` dedans qui recoit les tables des
assets. Une session SSH arrive directement dans `diagnostic` ; la base d'un projet cree apres coup
s'ajoute avec `codelab db mon-projet`. La base `postgres` livree par Postgres est supprimee au
demarrage : elle ne servait a rien ici, mais un outil qui s'y connectait par defaut doit maintenant
nommer une base (`psql -d dagster`). Details dans `workspace/README.md`.

**App-manager** : `http://<IP-du-serveur>:9001/` — demarrer/arreter tes apps deployees depuis `/workspace`, consulter
leurs logs. Protege par mot de passe, genere automatiquement au premier demarrage (voir ci-dessous pour le
recuperer). Le parcours complet, du dossier vide a l'application en ligne, est decrit dans
[`dev/README.md`](dev/README.md).

Deux choses s'y reglent quand la stack sert a plusieurs, ou quand on ne veut plus la surveiller a l'oeil :

- **Alertes par mail** (*Parametres > Alertes*) : un mail quand une application a epuise ses tentatives de
  redemarrage, un autre quand elle revient. Le serveur d'envoi est celui du bloc `codelab-alertes` de
  `credentials.env`, partage avec les alertes de Dagster.
- **Comptes utilisateurs** (*Utilisateurs*, dans le menu lateral) : des comptes nommes qui n'ouvrent que les projets
  qu'on leur autorise, sans rien pouvoir administrer ni atteindre Dagster. L'autorisation ne concerne
  que les projets **prives** : un projet public reste ouvert a tous. Le **second facteur y est
  obligatoire** : la personne enregistre elle-meme une cle a sa premiere connexion, et tu peux la
  remettre a zero si elle change de telephone.

Le panneau est **une seule application**, a la meme adresse, avec le **hub** pour accueil de tout le
monde : la liste des projets ouvrables, chacun avec sa description d'une ligne. Toi seul y gagnes le
menu lateral (vue d'ensemble, applications, journaux, utilisateurs) et les onglets d'administration
de **Parametres** ; les deux premiers onglets, eux, existent pour chaque compte.

- **Adresse mail et inscription libre** : chaque compte porte une adresse, verifiee par un code a six
  chiffres envoye dessus. Si un serveur d'envoi est configure, la page de connexion propose
  **« Creer un compte »** — le compte cree n'ouvre **aucun projet** tant que tu ne lui en autorises pas.

- **Cles d'acces (passkeys)** (*Parametres > Securite*) : se connecter avec l'empreinte ou le
  code de son appareil, sans mot de passe ni code a six chiffres. **Exige HTTPS et un nom de
  domaine** — le navigateur refuse WebAuthn en clair ; le panneau le dit au lieu d'afficher un
  bouton qui echouerait.

- **Journal des acces** (*Utilisateurs*, et l'onglet *Activite* d'une application) : qui s'est
  connecte, quand, et quelle application il a ouverte. De quoi reperer des echecs de connexion en
  rafale, et savoir si un projet sert encore avant de l'arreter. Le journal et la liste des comptes
  sont **recopies dans Postgres** (base `codelab`) pour l'historique long — en plus du fichier local,
  jamais a la place : une base eteinte ne ralentit ni une connexion ni l'ouverture d'un projet.

- **Categories** (*Parametres > Categories*) : des tiroirs pour ranger les projets dans le hub —
  « Outils », « Sites », « Donnees ». Purement visuel : aucune categorie ne donne de droit. Un projet
  non range apparait sous « Autres ».

Details dans [`app-manager/README.md`](app-manager/README.md).

**Pour sortir de chez toi** : [`TECHNIQUE.md`](TECHNIQUE.md#sortir-de-chez-toi--mettre-du-tls-devant-codelab) explique comment mettre du TLS devant CodeLab
(Cloudflare Tunnel, Caddy, ou un VPS), et quelles variables poser ensuite — c'est aussi ce qui
debloque les cles d'acces et le partage d'une application.

**Dagster** : `http://<IP-du-serveur>:3000/` — charge `/workspace/definitions.py` comme code Dagster.
Protege par mot de passe : Dagster n'a aucune authentification a lui, et son interface permet de lancer un
job, donc d'executer du code. Un reverse proxy (`codelab-dagster-proxy`) en ajoute une devant, et le port de
Dagster lui-meme n'est plus publie. **Pas d'identifiants a lui** : il utilise la session du panneau, donc le
meme mot de passe, la meme double authentification si elle est activee, et la meme deconnexion. Voir
[`dagster/proxy/README.md`](dagster/proxy/README.md).

**Agents** : `codelab agents` dans un projet y ecrit le mode d'emploi de la stack (perimetre d'ecriture,
acces a la base, conventions Dagster et app-manager) sous forme d'un `AGENTS.md`, lu par `codex` avant
chaque tache. Voir [`dev/README.md`](dev/README.md#codex).

## Contenu par defaut du workspace

[#contenu-par-defaut-du-workspace](#contenu-par-defaut-du-workspace)

Au tout premier demarrage, `codelab-dagster` depose un squelette dans `/workspace` :

```
/workspace/
├── README.md            <- conventions : anatomie d'un projet, secrets, base, dependances
├── definitions.py       <- agregateur Dagster : decouvre les projets, ne pas modifier
└── diagnostic/          <- projet modele ET outil de diagnostic de la stack
```

Un projet est **un dossier**. S'il contient un `definitions.py` exposant une variable `defs`,
Dagster le decouvre tout seul au rechargement du code -- il n'y a aucun fichier central a editer
pour declarer un nouveau projet. Un projet qui ne se charge pas est ignore avec un message dans les
logs, sans rendre les autres invisibles.

`diagnostic/` sert de modele : il montre sur un cas fonctionnel un asset Dagster, une application
web, un module partage entre les deux, la lecture de `credentials.env` et l'ecriture en base. Le
copier est le moyen le plus rapide de demarrer (`cp -r /workspace/diagnostic /workspace/mon-projet`).

**Rien n'est jamais ecrase, et rien n'est depose a cote.** Un fichier deja present sous le meme nom
est laisse tel quel ; la version de reference reste dans l'image, sous
`/opt/dagster/workspace.default`. La copie n'a lieu qu'une fois, tracee par
`/workspace/.codelab/workspace-v1` : un projet supprime ne reapparait pas au redemarrage.

`diagnostic/` est aussi **inscrit tout seul dans le panneau** au premier demarrage, en visibilite
privee, avec sa commande de build lancee une fois — une stack fraiche est donc verifiable sans
aucune saisie. Comme la copie, l'inscription n'a lieu qu'une fois (marqueur
`/var/lib/codelab/app-manager/diagnostic-inscrit`), et jamais sur un panneau qui contient deja des
applications.

## Pour aller plus loin

- **Developper un projet, le deployer, le depanner** : [`dev/README.md`](dev/README.md).
- **Exposer CodeLab en HTTPS, sauvegarder, durcir, auditer** : [`TECHNIQUE.md`](TECHNIQUE.md).
- **Le fonctionnement interne d'un service** : le `README.md` de son dossier
  ([`postgres/`](postgres/README.md), [`dev/`](dev/README.md), [`dagster/`](dagster/README.md),
  [`app-manager/`](app-manager/README.md)).

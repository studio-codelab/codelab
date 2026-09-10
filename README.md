# CodeLab

Environnement de developpement personnel, auto-heberge sur ton propre serveur. Une seule installation fournit
une base de donnees partagee, un acces SSH/VS Code, un orchestrateur de jobs et un gestionnaire d'applications
— un `docker-compose.yml`, rien d'autre a installer sur l'hote que Docker.

## Services

| Service | Role | Port |
|---|---|---|
| **Postgres** | Serveur de bases partage : une base par projet, plus la base `dagster` | interne uniquement |
| **Dev** | Acces SSH + VS Code Remote-SSH, avec un utilisateur dedie | `2222` |
| **Dagster** | Orchestration et planification de jobs (interface web + daemon) | interne uniquement |
| **Dagster-proxy** | Authentification devant Dagster, qui n'en a aucune | `3000` |
| **App-manager** | Deploiement et supervision des applications que tu developpes | `9001` |

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
2. Les donnees vivent sous `/DATA/AppData/codelab/` (voir [Persistance des donnees](#persistance-des-donnees)).
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
recuperer). Le parcours complet, du dossier vide a l'application en ligne, est decrit plus bas dans
[Developper et deployer](#developper-et-deployer).

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

**Pour sortir de chez toi** : [Sortir de chez toi](#sortir-de-chez-toi--mettre-du-tls-devant-codelab) explique comment mettre du TLS devant CodeLab
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
chaque tache. Voir [Codex](#codex).

## Developper et deployer

De la page blanche a l'application en ligne. Cette partie decrit le travail
quotidien : ou lancer ses commandes, comment creer un projet, comment le
mettre en ligne, et quoi regarder quand ca ne marche pas.

---

### Ou suis-je ?

La moitie des problemes vient d'une commande lancee dans le mauvais
environnement. Il y en a trois.

| Environnement | Comment y aller | Ce qu'on y trouve |
|---|---|---|
| Ordinateur local | terminal habituel | VS Code et ses extensions « UI », rien du projet |
| Conteneur `codelab-dev` | `ssh vscode@<IP-du-serveur> -p 2222` | le code, node, npm, codex, `/workspace` |
| Hote (le serveur) | `ssh <user>@<IP-du-serveur>` (port 22) | Docker, les volumes de la stack |

Pour trancher, une commande :

```bash
hostname && whoami && ls -d /workspace
```

Dans `codelab-dev` : un hostname hexadecimal, l'utilisateur `vscode`, et
`/workspace` existe. Sinon, on est ailleurs.

**`/workspace` est le seul dossier partage** entre les services et le seul qui
survive a une mise a jour d'image. `/home/vscode` et `/usr/local` sont
reconstruits a partir de l'image a chaque recreation du conteneur : ce qui doit
durer va dans `/workspace`, ou dans le `Dockerfile` du service.

> Ne jamais donner un chemin absolu a un agent lance depuis l'ordinateur local :
> il chercherait un `/workspace/...` qui n'existe que sur le serveur. Dire
> « dans le projet courant ».


### Le cycle complet

```bash
ssh vscode@<IP-du-serveur> -p 2222     # 1. entrer dans le conteneur
codelab new mon-projet                 # 2. dossier, git, .gitignore, AGENTS.md
cd /workspace/mon-projet               # 3. jamais depuis l'ordinateur local
codex                                  # 4. developper (voir « Codex », plus bas)
npm run build                          # 5. verifier que le build passe
```

6. Ouvrir `http://<IP-du-serveur>:9001/`, **Ajouter un projet**, choisir le
   dossier : les commandes de lancement et de build sont proposees
   automatiquement (« Deployer », plus bas).
7. Activer l'application. Elle est servie sur
   `http://<IP-du-serveur>:9001/mon-projet/`.

Ensuite, a chaque modification du code : **Déployer** dans le menu « ... » de
la tuile — build puis remise en ligne en une action.

Aucune installation prealable : node, npm, `codex`, `git` et le client Postgres
sont dans l'image `codelab-dev` ; node, npm et `git` sont aussi dans l'image
`codelab-app-manager`, qui est celle qui execute les builds.

#### Creer un nouveau projet

Un projet **est un dossier** sous `/workspace/`. Il n'y a aucun registre a tenir
a jour, aucun fichier central a editer : ni `/workspace/definitions.py`, ni le
`docker-compose.yml`, ni `apps.json` a la main.

```bash
codelab new mon-projet          # dossier, depot git, .gitignore, AGENTS.md
codelab new mon-projet --db     # ... et sa base de donnees
```

La commande refuse un dossier existant et un nom invalide : elle est sans danger a relancer.
Elle ne fait **pas** l'echafaudage du framework — `npm create vite@latest .` telecharge, pose des
questions et evolue a son rythme ; le reproduire ici vieillirait mal. La commande l'affiche en
piste suivante, c'est tout.

Puis, **selon ce que le projet fait** — les trois cas sont independants et se
cumulent :

| Le projet... | Ce qu'il faut en plus |
|---|---|
| a besoin d'une base | `codelab db mon-projet`, puis `CODELAB_DB=mon-projet` dans son `.env` |
| a des jobs Dagster | un `definitions.py` exposant `defs` ; il est decouvert seul, *Reload* dans Dagster pour le voir |
| est une application web | le declarer dans l'app-manager (« Deployer », plus bas) |

Enfin, completer la section « Projet » du `AGENTS.md` — ce que fait le projet,
ses commandes — puis lancer `codex`.

Trois choses a ne pas oublier, dans l'ordre ou elles se retournent contre toi :

1. **`base` / `basePath`** si c'est un front, sinon page blanche derriere le
   sous-chemin `/mon-projet/` (« Deployer », plus bas).
2. **`.env` dans le `.gitignore`** : `/workspace` n'est pas chiffre.
3. **Un nom de module unique** si le projet a du Dagster : tous les projets
   sont charges dans le meme processus, deux `utils.py` se marchent dessus.

Le plus rapide reste de copier le projet `diagnostic`, qui montre un asset
Dagster, une application web et un module partage entre les deux :

```bash
cp -r /workspace/diagnostic /workspace/mon-projet
```

#### Sans passer par le terminal

**Ctrl+Maj+B** (Cmd+Maj+B sur Mac) cree un projet, depuis n'importe ou dans VS Code. Pas de menu :
la tache « CodeLab : nouveau projet » est declaree tache de *build* par defaut, et c'est le raccourci
que VS Code reserve a celle-ci. Une boite de dialogue demande le nom, le resultat s'affiche.

Les autres passent par **Terminal > Executer la tache...** :

| Tache | Ce qu'elle fait |
|---|---|
| **CodeLab : nouveau projet** | `codelab new` — **Ctrl+Maj+B** |
| **CodeLab : nouveau projet + base de donnees** | idem, avec `--db` |
| **CodeLab : creer la base d'un projet existant** | `codelab db` |
| **CodeLab : mettre a jour le manuel de l'agent** | `codelab agents` |

Les taches appellent l'outil `codelab` du conteneur plutot que de recopier ses commandes : la logique
reste a un seul endroit.

##### Un raccourci dedie a une autre tache

Un dossier de travail ne peut reserver qu'un seul raccourci — celui de la tache de build. Pour en
dedier un autre, il faut passer par **tes** raccourcis (Ctrl+Maj+P, « Preferences: Open Keyboard
Shortcuts (JSON) ») ; ce fichier est personnel a ton VS Code, il ne peut pas etre livre par le depot :

```json
{
  "key": "ctrl+alt+n",
  "command": "workbench.action.tasks.runTask",
  "args": "CodeLab : nouveau projet + base de donnees"
}
```

Ouvre `/workspace` comme dossier dans VS Code, sinon les taches ne sont pas
proposees (elles vivent dans le `.vscode` de ce dossier).

Le reste du parcours est deja sans terminal : le panneau de l'app-manager fait
l'ajout, le build, le deploiement et les logs, et l'explorateur de VS Code cree
fichiers et dossiers au clic droit.

Ce qui echappe encore aux taches : l'echafaudage d'un projet front
(`npm create vite`), qui reste une commande a lancer. C'est la place naturelle
d'un `codelab new --stack`, pas d'une ligne de shell recopiee dans un fichier
de configuration.

> **Installation deja en place :** le squelette n'est copie qu'une fois, au
> premier demarrage. Pour recuperer `.vscode/` sur un workspace existant, une
> commande sur l'hote suffit :
>
> ```bash
> docker cp codelab-dagster:/opt/dagster/workspace.default/.vscode /DATA/AppData/codelab/workspace/
> ```


### Deployer

Le formulaire d'ajout inspecte le dossier et propose **deux** commandes : une de
build, une de lancement. Cliquer sur la suggestion remplit les deux champs.

| Le dossier contient | Build propose | Lancement propose |
|---|---|---|
| Vite, Astro, Parcel, CRA, Angular (avec un script `build`) | `npm ci && npm run build` | `python3 -m http.server $PORT --directory dist` |
| Next.js | `npm ci && npm run build` | `npx next start --port $PORT` |
| un `package.json` avec un script `start` | `npm ci` (+ `&& npm run build` si le script existe) | `npm start` |
| `manage.py` (Django) | `pip install -r requirements.txt` | `python3 manage.py runserver 0.0.0.0:$PORT` |
| `app.py` / `main.py` | `pip install -r requirements.txt` | `python3 app.py` |
| `index.html` seul | — | `python3 -m http.server $PORT` |

Trois regles derriere ce tableau :

- **`$PORT` est fourni par l'app-manager.** La variable est injectee dans
  l'environnement du processus lance : ecrire `$PORT` plutot qu'un numero en dur
  evite d'avoir a resynchroniser la commande quand le port change.
- **Le build a besoin de node, le service non.** Pour un front, ce qui part en
  ligne est le dossier produit par le build, servi par le `http.server` de
  Python. Rien a redemarrer si node change de version, et pas de serveur de
  developpement (`vite dev`) expose en continu.
- **`npm ci` quand il y a un lockfile**, `npm install` sinon : installation
  reproductible, et plus rapide.

#### Mettre a jour une application en ligne

**Déployer**, dans le menu « ... » de la tuile : build, puis redemarrage
seulement si le build a reussi. L'ancienne version continue d'etre servie
pendant le build, et un build casse ne met rien hors ligne — on ne remplace une
version qui marche que par une version qui compile.

« Lancer le build » et « Redemarrer » restent disponibles separement, pour
construire sans mettre en ligne ou relancer sans reconstruire.

#### Application servie sous un sous-chemin

L'app-manager sert chaque application sous `/<nom>/`. Un front construit pour la
racine y affiche une page blanche et des 404 sur ses assets. Pour Vite :

```js
// vite.config.js
export default defineConfig({ base: '/mon-projet/' })
```

L'equivalent existe partout : `basePath` pour Next.js, `--base-href` pour
Angular, `homepage` dans le `package.json` pour Create React App.


### Codex

`codex` est installe dans l'image et `CODEX_HOME` pointe sur
`/workspace/.codex`, cree au demarrage du conteneur avec un `config.toml` par
defaut. L'authentification et les reglages survivent donc aux mises a jour
d'image : `codex login` n'est a refaire que la premiere fois.

Le flux OAuth ouvre un serveur sur le port 1455 **dans le conteneur**. Depuis
l'ordinateur local, dans un second terminal :

```bash
ssh -p 2222 -L 1455:127.0.0.1:1455 vscode@<IP-du-serveur>
```

puis, cote conteneur, `codex login` et ouvrir l'URL affichee. Sans tunnel :
`codex login --device-auth`. C'est fait quand `/workspace/.codex/auth.json`
existe.

```bash
cd /workspace/mon-projet
codex                 # session interactive (/model, /approvals, /new)
codex exec "..."      # commande unique, sans memoire entre deux appels
codex resume          # reprendre une session
```

#### Donner le mode d'emploi a l'agent

Codex lit un `AGENTS.md` avant chaque tache. CodeLab en fournit le contenu : le
manuel de l'environnement — perimetre d'ecriture, acces a la base, conventions
Dagster, regles d'une application servie par l'app-manager — vit dans l'image,
et se pose dans un projet en une commande :

```bash
cd /workspace/mon-projet
codelab agents
```

Le fichier obtenu a deux parties. Le **bloc CodeLab**, entre marqueurs, est
gere par la commande : relancer `codelab agents` apres une mise a jour de la
stack le rafraichit. Tout ce qui est ecrit **hors** de ce bloc t'appartient et
n'est jamais touche — c'est la que vont les specificites du projet, et le
squelette cree a la premiere execution attend exactement ca :

```markdown
# Projet mon-projet

## Ce que fait ce projet
## Commandes
| But | Commande |
## Conventions propres a ce projet
```

Ce que le manuel dit deja a l'agent, sans que tu aies a le repeter :

- **Ne modifier que le projet courant** — jamais `/workspace/definitions.py`,
  jamais un autre projet, rien hors de `/workspace`, pas de `sudo apt`.
- **Verifier avant de conclure** — `npm test` / `npm run build` executes, pas
  supposes.
- **La base** : une base par projet nommee comme le dossier, `psql` sans
  argument, `psycopg.connect()` sans argument, `codelab db` pour la creer.
- **Dagster** : deposer un `definitions.py` exposant `defs`, le fichier racine
  le decouvre seul ; `group_name` par projet ; les collisions de noms de
  modules entre projets.
- **App-manager** : ecouter sur `$PORT` et `0.0.0.0`, le sous-chemin
  `/<projet>/`, build puis service statique, jamais un serveur de
  developpement, et les dependances Python dans `vendor/`.
- **Secrets** : rien en clair dans le code, `.env` a cote du projet, jamais
  dans `credentials.env`.
- **Chemins relatifs**, jamais de `/workspace/...` en dur.

Le meme manuel est aussi ecrit dans `/workspace/.codex/AGENTS.md` au demarrage
du conteneur, ou Codex le lit comme instructions globales. Les deux existent a
dessein : ce niveau global n'est pas honore par toutes les versions de la CLI,
le fichier du projet l'est toujours.

**L'extension VS Code `openai.chatgpt` ne fonctionne pas ici** : elle est
declaree « UI-only », donc executee sur l'ordinateur local, ou il n'y a ni le
projet ni node (`Failed to create unified exec process`). Le reglage
`"remote.extensionKind": { "openai.chatgpt": ["ui"] }` reflete cette contrainte,
il est correct. Utiliser la CLI dans le terminal integre — glisser l'onglet du
terminal vers le bord droit donne une disposition editeur + agent equivalente a
un panneau.


### Quand ca ne marche pas

```bash
# l'application repond-elle ?
curl -I http://codelab-app-manager:9001/mon-projet/

# l'app-manager est-il debout ? (302 vers /login = oui)
curl -I http://<IP-du-serveur>:9001/
```

Depuis `codelab-dev`, `127.0.0.1` designe **codelab-dev**, pas les autres
services : les joindre par leur nom de conteneur (`codelab-app-manager`,
`codelab-postgres`).

| Symptome | Cause habituelle |
|---|---|
| Page blanche, 404 sur les assets | `base` non configure — voir « Deployer » |
| `Permission denied` sur un script | preferer `bash script.sh` a `./script.sh` |
| Build en echec | le journal complet est dans « Voir les logs » |
| Pastille rouge clignotante | boucle de crash : 5 echecs de suite, redemarrage automatique suspendu |
| Pastille orange, « Ne repond pas » | le process vit, mais rien n'ecoute sur son port : port en dur au lieu de `$PORT`, ecoute sur `127.0.0.1`, ou plantage du serveur apres le demarrage — les logs disent lequel |
| Un fichier n'est plus modifiable | supprimer `/workspace/.codelab/permissions-v1` et redemarrer la stack |

## Un seul fichier de secrets

Mot de passe Postgres, mot de passe admin app-manager, cle de session, secret de double authentification : **tout est dans `credentials.env`, et nulle part ailleurs.** Aucun fichier mono-secret a cote, rien a aller chercher dans un conteneur :
```bash
cat /DATA/AppData/codelab/config/credentials.env
```
Chaque service ecrit son propre bloc au demarrage (delimite par `# ===== <service> =====`, avec le commentaire
qui explique a quoi sert chaque valeur) et lit celui des autres. `codelab-postgres` genere le mot de passe et
l'ecrit la ; `codelab-dev` et les deux services Dagster le relisent depuis ce fichier ; `app-manager` y depose
son mot de passe admin et sa cle de session. Une valeur deja presente n'est jamais regeneree par-dessus.

Sauvegarder ce fichier (et `config/ssh/`) suffit a sauvegarder tous les acces.

## Acces SSH depuis plusieurs ordinateurs

[Acces SSH depuis plusieurs ordinateurs](#acces-ssh-depuis-plusieurs-ordinateurs)

Chaque machine autorisee a son propre fichier dans `config/ssh/authorized_keys.d/`.
`authorized_keys` n'est plus edite a la main : c'est un fichier **derive**, reconstruit au demarrage
comme l'union de ce dossier. Ajouter un ordinateur, c'est deposer un fichier ; en retirer un, c'est
en supprimer un.

```
# autoriser une machine
cp ~/cle.pub /DATA/AppData/codelab/config/ssh/authorized_keys.d/portable.pub
docker restart codelab-dev

# lui retirer l'acces
rm /DATA/AppData/codelab/config/ssh/authorized_keys.d/portable.pub
docker restart codelab-dev

# voir les machines autorisees
ls /DATA/AppData/codelab/config/ssh/authorized_keys.d/
```

Une consequence utile :

- **Ecrire directement dans `authorized_keys` continue de marcher.** Une ligne ajoutee a la main est
  recuperee dans `authorized_keys.d/manuel.pub` au demarrage suivant, puis reprise dans le fichier
  derive -- elle n'est pas ecrasee par la reconstruction.

L'empreinte du serveur, elle, ne change pas : les cles hote sont generees une seule fois dans
`config/ssh/host_keys/`. Elles ne sont regenerees que si une cle est **illisible**, pas seulement
absente -- un fichier tronque par un arret brutal ferait echouer `sshd` et repartir sur une nouvelle
identite, donc sur le `REMOTE HOST IDENTIFICATION HAS CHANGED` cote client.

## Sortir de chez toi : mettre du TLS devant CodeLab

Tant que CodeLab reste sur ton reseau, le HTTP en clair passe. Des qu'il en
sort, il faut du TLS -- et c'est aussi ce qui debloque les cles d'acces et le
partage d'une application.

Le panneau affiche trois lignes rouges dans *Parametres > Serveur* tant que la connexion n'est pas
chiffree :

| Ligne | Ce qu'elle dit |
|---|---|
| **Connexion chiffree (HTTPS)** | Le panneau est servi en clair. Ton mot de passe, ton code a six chiffres et ton cookie de session traversent le reseau lisibles par qui les intercepte. |
| **Cookie de session en Secure** | Le cookie n'est pas marque `Secure`, donc le navigateur accepterait de l'envoyer en clair. |
| **Adresse reelle des visiteurs** | Derriere un proxy, toutes les requetes semblent venir du proxy : la limite de tentatives compte alors pour un seul visiteur, et le journal des acces note une seule adresse. |

**Elles se resolvent dans cet ordre, et le premier point fait le gros du travail** : les deux autres
sont une variable chacun, a poser une fois le TLS en place. Les poser avant couperait la connexion
en cours — un cookie `Secure` n'est plus envoye en clair, donc la session tombe au rechargement.

> Les **cles d'acces (passkeys)** dependent aussi de cette etape : le navigateur refuse WebAuthn hors
> HTTPS, et exige un **nom de domaine** (pas une adresse IP). Le TLS les debloque.


### Etape 1 — mettre du TLS devant le panneau

Trois routes. Elles se valent techniquement ; ce qui les separe, c'est ce que tu possedes deja.

#### Route A — Cloudflare Tunnel (aucun port a ouvrir)

La plus simple pour une machine a la maison, et **la seule qui marche derriere un CGNAT** (quand ton
operateur ne te donne pas d'adresse publique a toi). Un conteneur ouvre une connexion **sortante**
vers Cloudflare ; le trafic revient par la. Rien a ouvrir sur la box, TLS et nom de domaine fournis.

1. Un compte Cloudflare, un domaine delegue chez eux (les leurs sont gratuits sur `.workers.dev`,
   mais pour un nom a toi il faut un domaine que tu possedes).
2. Zero Trust → Networks → Tunnels → *Create a tunnel* → note le jeton.
3. Ajoute le service au `docker-compose.yml` :

```yaml
  codelab-tunnel:
    image: cloudflare/cloudflared:latest
    container_name: codelab-tunnel
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: colle-ici-le-jeton
    networks:
      - codelab
```

4. Dans la console Cloudflare, route le nom de domaine vers `http://codelab-app-manager:9001`
   (le nom du conteneur, puisque le tunnel est sur le meme reseau).

Ce que ca donne : `https://codelab.tondomaine.fr` en TLS valide, sans ouvrir un seul port.

**Attention** : le tunnel expose le panneau a tout internet. Active la double authentification (ou une
cle d'acces) **avant**, et pense a l'acces Zero Trust de Cloudflare si tu veux une porte de plus.

#### Route B — Caddy sur la ZimaBlade + ton domaine

Si tu peux ouvrir les ports 80 et 443 de ta box vers la ZimaBlade, et que ton domaine pointe vers ton
adresse publique (avec un DNS dynamique si elle change).

```yaml
  codelab-tls:
    image: caddy:2-alpine
    container_name: codelab-tls
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /DATA/AppData/codelab/caddy:/data
      - /DATA/AppData/codelab/Caddyfile:/etc/caddy/Caddyfile:ro
    networks:
      - codelab
```

`Caddyfile` (deux lignes, le certificat Let's Encrypt est obtenu et renouvele tout seul) :

```
codelab.tondomaine.fr {
    reverse_proxy codelab-app-manager:9001
}
```

#### Route C — un VPS devant, la ZimaBlade derriere

Le VPS porte le nom de domaine et le certificat, et relaie vers la maison par un tunnel WireGuard.
Utile quand la box ne peut pas ouvrir de ports, ou quand tu veux que l'adresse publique soit celle du
VPS.

**Les fichiers sont prets dans [`app-manager/vps/`](app-manager/vps/)** : les deux configurations
WireGuard, la configuration nginx du site, et la marche a suivre dans l'ordre. Extrait de
`app-manager/vps/nginx/codelab.conf` :

```nginx
server {
    listen 443 ssl;
    server_name codelab.tondomaine.fr;
    # certificat obtenu par certbot
    ssl_certificate     /etc/letsencrypt/live/codelab.tondomaine.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/codelab.tondomaine.fr/privkey.pem;

    location / {
        proxy_pass http://10.8.0.2:9001;   # la ZimaBlade, au bout du WireGuard
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For   $remote_addr;
        # Les journaux en direct sont du Server-Sent Events : sans ces deux
        # lignes, nginx tamponne et le journal n'avance qu'a la fermeture.
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
```

**Les trois en-tetes comptent** : `Host` (sinon le panneau ne connait pas son propre nom, et les cles
d'acces se lient au mauvais domaine), `X-Forwarded-Proto` (sinon le panneau se croit en clair) et
`X-Forwarded-For` (sinon tous les visiteurs partagent une adresse).


### Etape 2 — le dire au panneau

Une fois le TLS en place et verifie dans un navigateur, **decommente** les trois lignes deja
preparees dans `docker-compose.yml`, sous `codelab-app-manager` → `environment` :

```yaml
      # Marque le cookie de session "Secure" : le navigateur ne l'enverra plus
      # jamais en clair. A ne poser QU'UNE FOIS le TLS en place, sinon la
      # session tombe au premier rechargement.
      APP_MANAGER_HTTPS: "1"
      # Croit X-Forwarded-For : la limite de tentatives et le journal des acces
      # retrouvent l'adresse reelle des visiteurs. A ne poser QUE derriere un
      # proxy qui reecrit vraiment cet en-tete -- publie en direct, n'importe
      # quel client peut le poser et contourner la limite.
      APP_MANAGER_TRUST_PROXY: "1"
      # L'adresse publique du serveur. Sans elle, le panneau ne propose pas de
      # rendre une application publique.
      APP_MANAGER_PUBLIC_URL: "https://codelab.tondomaine.fr"
```

Puis :

```bash
docker compose up -d codelab-app-manager
```

Les trois lignes de *Parametres > Serveur* passent au vert, et le bouton « Rendre publique »
reapparait sur les fiches d'application.

### Etape 3 — verifier

1. Ouvre `https://codelab.tondomaine.fr` : cadenas, pas d'avertissement.
2. *Parametres > Serveur* : les trois lignes en vert, l'adresse publique declaree.
3. *Utilisateurs* : ton adresse IP reelle apparait dans les connexions recentes (pas celle du proxy).
4. *Parametres > Securite* : le bouton « Ajouter une cle » est actif — enregistres-en une.
5. Deconnecte-toi, puis reconnecte-toi avec la cle.

### Ce que ca ne couvre pas

- **Les applications deployees** sont servies par le meme proxy, donc elles heritent du TLS. Une
  application **publique** devient alors accessible a tout internet : c'est le but, mais c'est aussi
  la raison pour laquelle CodeLab ne le propose pas tant que l'adresse publique n'est pas declaree.
- **Dagster** (port 3000) n'est pas derriere ce proxy. Si tu l'exposes aussi, ajoute-lui une entree
  dans la meme configuration, en gardant `codelab-dagster-proxy` devant lui.
- **La meme origine.** Toutes les applications sont servies sous `https://codelab.tondomaine.fr/<nom>/`.
  Une faille XSS dans une application reste une faille dans l'origine du panneau. Un sous-domaine par
  application y remedierait, au prix d'un certificat generique et d'une entree DNS par projet.

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

## Workspace partage entre les services

[Workspace partage entre les services](#workspace-partage-entre-les-services)

`/workspace` est ecrit par trois services aux identites differentes : les sessions SSH en `vscode`
(uid 1000), Dagster et app-manager en `root`. Sans precaution, un fichier produit par un job Dagster
sort en `root:root 0644` et n'est plus modifiable depuis VS Code -- et l'inverse est vrai aussi.

Trois mecanismes, poses automatiquement au demarrage, garantissent l'ecriture partagee :

| Mecanisme                        | Role                                                     |
| -------------------------------- | -------------------------------------------------------- |
| Groupe `codelab` (**gid 2000**)  | present dans les 3 images, sous le meme numero            |
| `setgid` sur `/workspace` (2775) | tout fichier cree herite du groupe du dossier parent      |
| `umask 002`                      | ce groupe herite aussi du droit d'ecriture, pas juste du nom |

Les trois sont necessaires : le setgid seul donne le bon groupe en lecture seule, l'umask seul ne
change pas le groupe. Des ACL par defaut (`setfacl -d`) viennent en filet supplementaire quand le
systeme de fichiers les supporte.

Le tout est applique par l'entrypoint de `codelab-dev`, `codelab-dagster` et `codelab-app-manager`.
La passe recursive sur les fichiers deja presents n'est faite qu'une fois, tracee par
`/workspace/.codelab/permissions-v1` -- **supprimer ce marqueur force une reapplication complete** au
prochain redemarrage, ce qui est la reparation a tenter en premier si un fichier resiste.

## Limiter les ports au reseau local

Les ports `9001`, `9002`, `3000` et `2222` sont publies sur l'hote : c'est ce qui permet d'ouvrir le
panneau depuis un autre poste, et c'est voulu. Le risque n'est pas la : c'est le jour ou la machine
gagne une interface a laquelle personne ne pense -- un VPN, un Wi-Fi invite, une regle uPnP posee par
la box. Le port suit, sans que rien ne le dise.

```bash
sudo outils/codelab-pare-feu poser 192.168.1.0/24   # ton reseau local
outils/codelab-pare-feu verifier
sudo outils/codelab-pare-feu retirer                # revenir en arriere
```

**Le piege qu'il faut connaitre** : les ports publies par Docker **ne passent pas par la chaine
INPUT**. Docker installe ses propres regles de redirection, et le trafic traverse `FORWARD`. Un
pare-feu ecrit comme d'habitude -- `ufw`, une regle `INPUT`, `nft` sur le hook input -- ne filtre donc
**rien** de CodeLab, tout en donnant l'impression du contraire. C'est une protection qui rassure sans
proteger. Le bon endroit est la chaine `DOCKER-USER`, que Docker traverse avant ses propres regles et
n'ecrase jamais : c'est la que cet outil ecrit.

**Ce qu'il ne touche pas** : le port 22 de l'hote. Il ne passe pas par Docker, et une erreur dessus
t'enfermerait dehors. Si tu veux le restreindre aussi, fais-le a la main, avec une session ouverte a
cote.

Les regles ne survivent pas a un redemarrage de l'hote -- `iptables-persistent`, ou une unite systemd
qui rejoue la commande. L'outil le rappelle a chaque pose.

## Sauvegarder, et verifier la sauvegarde

Une sauvegarde de CodeLab contient `credentials.env` : le mot de passe Postgres, celui du panneau,
la cle de signature des sessions et le secret du second facteur. Plus les cles hote SSH et le fichier
des comptes. **En clair sur un disque externe ou chez un hebergeur, elle vaut la machine entiere** --
et elle est plus facile a voler que la machine. Elle est donc chiffree.

L'outil vit dans `outils/` et se lance **sur l'hote**, pas dans un conteneur : il a besoin des
volumes et de Docker.

```bash
# creer une archive chiffree (demande une phrase de passe)
outils/codelab-sauvegarde creer /mnt/disque-externe

# la verifier -- restauration a blanc dans un dossier jetable
outils/codelab-sauvegarde verifier /mnt/disque-externe/codelab-20260910-121015.tar.gz.enc

# la remettre en place (demande confirmation, ecrase les donnees)
outils/codelab-sauvegarde restaurer /mnt/disque-externe/codelab-...enc
```

Ce qui est sauvegarde : `config/` (les secrets, irremplacables), `app-manager/` (comptes, cles
d'acces, registre, journal), `dagster/`, `workspace/` (ton code) et **toutes les bases** dans un seul
dump SQL. Ce qui ne l'est pas : les images Docker et les `node_modules`, qui se retelechargent -- une
sauvegarde trop grosse est une sauvegarde qu'on ne lance plus.

**`verifier` n'est pas optionnel.** Une sauvegarde jamais restauree n'est pas une sauvegarde : la
commande dechiffre pour de vrai, extrait dans un dossier jetable et controle que le dump et les
secrets sont bien la. A lancer le jour ou tu crees l'archive, pas le jour ou tu en as besoin.

La phrase de passe ne passe jamais par la ligne de commande -- elle serait lisible dans `ps` et
resterait dans l'historique du shell. Saisie interactive, ou variable `CODELAB_PASSPHRASE` pour une
tache planifiee.

## Auditer une instance en marche

Un audit qui lit le code ne voit pas la configuration reelle : une variable oubliee, un port publie
par erreur, une garde active en developpement et pas en service. Celui-ci parle a la machine.

```bash
outils/codelab-audit-dynamique https://codelab.tondomaine.fr --mot-de-passe "$(cat mdp)"
```

Il verifie que les API refusent les visiteurs, que le cookie porte bien `HttpOnly`, `Secure` et
`SameSite`, qu'une ecriture sans jeton est refusee, que l'explorateur ne sort pas de `/workspace`,
que le panneau ne repond pas sur l'origine des applications, et que le mot de passe n'est pas
essayable a l'infini. Il sort en erreur s'il reste un point rouge, donc il se met dans une tache
planifiee.

Deux choses a savoir : la derniere sonde **epuise volontairement le compteur d'essais**, donc les
connexions sont refusees quelques minutes ensuite ; et pour auditer une instance en `http`, il faut
lui retirer `APP_MANAGER_HTTPS` -- sinon le cookie `Secure` n'est pas renvoye, la session n'est pas
portee, et l'outil le dit au lieu de conclure a tort.

**Il ne remplace pas un pentest par un humain** : il verifie ce qu'on sait deja devoir verifier, pas
ce a quoi personne n'a pense.

## Persistance des donnees

Tout vit sous `/DATA/AppData/codelab/` sur le disque de l'hote (aucun volume Docker nomme) : ca survit a un
redemarrage, une recreation de conteneur et une reinstallation.

```
/DATA/AppData/codelab/
├── config/
│   ├── credentials.env        tous les secrets, et rien d'autre
│   └── ssh/
│       ├── authorized_keys    cles autorisees a ouvrir une session
│       └── host_keys/         identite du serveur (empreinte stable)
├── workspace/                 ton code, partage par les 4 services
├── postgres/                  donnees de la base
├── dagster/                   etat Dagster
└── app-manager/               apps.json + logs des applications
```

Seule exception a connaitre : si ton interface de gestion propose, a la desinstallation, de supprimer aussi
les donnees de l'application, il faut refuser pour les conserver. En ligne de commande, `docker compose down`
n'y touche pas (ce sont des bind mounts, pas des volumes geres par Docker).

## Migration depuis une version anterieure

Les secrets se migrent **tout seuls** au premier demarrage : `config/postgres_password`,
`app-manager/admin_password` et `app-manager/flask_secret_key` sont repris dans `credentials.env` puis
supprimes. La base de donnees et le mot de passe du panneau continuent de fonctionner, rien a faire.

Les cles SSH demandent **une commande**, parce que leurs anciens dossiers ne sont plus montes par le compose et
qu'un conteneur ne peut donc plus les lire. A executer sur l'hote avant de redemarrer la stack, pour
conserver l'empreinte du serveur et les cles deja autorisees :

```bash
cd /DATA/AppData/codelab
sudo mkdir -p config/ssh
sudo mv dev-host-keys config/ssh/host_keys
sudo mv dev-ssh/authorized_keys config/ssh/
sudo rmdir dev-ssh
```

Le dossier `dagster-home` a par ailleurs ete renomme en `dagster`, pour s'aligner sur les autres (`postgres`,
`app-manager`, `workspace`) qui portent tous le nom de leur service. A renommer avant de relancer la stack,
sinon Dagster repart d'un `DAGSTER_HOME` vide et reinitialise sa configuration :

```bash
sudo mv /DATA/AppData/codelab/dagster-home /DATA/AppData/codelab/dagster
```

Sans cette etape, de nouvelles cles hote sont generees — l'empreinte du serveur change
(`ssh-keygen -R "[<IP-du-serveur>]:2222"` cote client) et **les cles autorisees sont perdues** : il faut
redeposer un `.pub` dans `authorized_keys.d/` pour retrouver l'acces SSH.

## Tests

```bash
pip install flask psutil requests pytest
python -m pytest app-manager/tests -q
```

Portee volontairement etroite : ce qui se verifie sans conteneur, sans Postgres et sans reseau — detection
de stack a l'ajout d'un projet, bornage des chemins a `/workspace`, attribution des ports,
authentification du panneau et sa limite de tentatives. Le cycle de vie des process et le reverse proxy
demandent une stack en marche et se verifient a la main.

## Publication des images (CI)

`.github/workflows/build-images.yml` lance les tests, puis build et pousse les images vers
`ghcr.io/lucasrtn/...` a chaque push sur `main`. Sur une **pull request**, les tests tournent et les quatre
images sont construites en `amd64` seul, sans rien publier : un Dockerfile casse se voit avant la fusion,
pas apres. **Etape unique a faire a la main** apres le premier run : les packages GitHub sont crees
prives par defaut, donc un `docker pull` anonyme echoue tant qu'ils ne sont pas passes en **Public**
(`github.com/lucasrtn?tab=packages` → package → Package settings → Danger Zone → Change visibility).

## Versionner CodeLab (figer une release)

`latest` bouge a chaque push sur `main` — pratique en developpement, risque en production si un changement
casse quelque chose (comme observe pendant ce projet). CodeLab se versionne **comme un tout** : dev, dagster et
app-manager sortent toujours ensemble, sous un seul numero — pas de version separee par service.

```bash
git tag v1.0.0
git push origin v1.0.0
```

Ce tag declenche le workflow, qui reconstruit et publie **les 3 images en meme temps**, toutes avec ce numero :
`ghcr.io/lucasrtn/codelab-dev:1.0.0`, `codelab-dagster:1.0.0`, `codelab-app-manager:1.0.0`. `latest` n'est pas
touche.

**Sur GitHub (sans ligne de commande)** : Releases → Create a new release → tag `v1.0.0` (target `main`) →
Publish release. Gabarit a reprendre :

- **Titre** : `CodeLab v1.0.0`
- **Description** :
  ```
  Version complete de CodeLab : dev, dagster et app-manager.
  Images : ghcr.io/lucasrtn/codelab-{dev,dagster,app-manager}:1.0.0

  Changements :
  - ...
  ```

**Revenir a cette version precise** : dans `docker-compose.yml`, remplacer le tag `:latest` par `:1.0.0` sur les
**3 services** (`codelab-dev`, `codelab-dagster`, `codelab-app-manager`), puis `docker compose up -d`.

**Lister les versions disponibles** :
```bash
git tag -l "v*"
```
ou visuellement sur `github.com/lucasrtn/codelab/releases`, ou sur `github.com/lucasrtn?tab=packages` pour
chaque image individuellement.

## Icone de l'application

L'icone ne sert qu'aux interfaces d'installation qui affichent une vignette, et elle vit dans
`docker-compose-casaos.yml` uniquement. Elle y est **encodee en base64 directement dans le fichier** plutot
que referencee par URL : le depot etant prive, un lien `raw.githubusercontent.com/.../icon.png` demanderait
une authentification que ces interfaces n'ont pas. Aucune requete externe n'est donc necessaire pour
l'afficher. Le fichier source reste `icon.svg`/`icon.png` a la racine du depot, a re-encoder si tu la
changes :

```bash
python3 -c "import base64; print('data:image/png;base64,' + base64.b64encode(open('icon.png','rb').read()).decode())"
```

## Organisation du depot

```text
codelab/
├── outils/         # sauvegarde chiffree et audit dynamique (a lancer sur l'hote)
├── docker-compose.yml
├── icon.svg / icon.png
├── .github/workflows/build-images.yml
├── workspace/      # squelette depose dans /workspace au premier demarrage
├── postgres/       # serveur de bases — voir postgres/README.md
├── dev/            # SSH + VS Code Remote-SSH — voir dev/README.md
├── dagster/        # orchestration de jobs — voir dagster/README.md
│   └── proxy/      # authentification devant Dagster (image a part)
└── app-manager/    # deploiement d'applications — voir app-manager/README.md
    ├── app/        # le service : app.py et les deux pages qu'il sert
    ├── tests/      # regressions gardees : detection, chemins, auth, privileges
    └── vps/        # tunnel WireGuard + nginx pour l'exposer — voir son README
```

**Un dossier par service**, et tout ce qui concerne un service vit dedans : son image, son code, sa
documentation, ses tests. Le proxy de Dagster est une image distincte -- deux processus, deux conteneurs --
mais il n'a rien a faire a la racine : il n'existe que pour Dagster.

`app-manager/vps/` suit la meme regle, jusqu'au bout : ces fichiers s'installent sur une autre
machine, mais ils n'existent que pour exposer le panneau. Les ranger a la racine aurait fait croire
a un sixieme service ; les ranger ici dit de quoi ils dependent.

Ce README couvre CodeLab dans son ensemble : installation, usage, developpement d'un projet
et exposition en HTTPS
(developper dans le conteneur, deployer sur l'app-manager). Le fonctionnement interne de chaque service (scripts de
demarrage, variables d'environnement, pieges connus) est documente dans son propre `README.md`.

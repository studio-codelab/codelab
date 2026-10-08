# codelab-dev

Conteneur d'environnement de developpement : serveur SSH + utilisateur dedie, pret pour VS Code Remote-SSH,
avec un acces Postgres deja configure dans l'environnement de session.

Ce fichier couvre deux choses : **le travail quotidien** -- creer un projet, le mettre en ligne, le
depanner -- puis **le conteneur lui-meme**, pour qui veut comprendre ou modifier l'image.

Pour ce que CodeLab fait dans son ensemble, voir le [README](../README.md).

---

# Developper et deployer

De la page blanche a l'application en ligne. Cette partie decrit le travail
quotidien : ou lancer ses commandes, comment creer un projet, comment le
mettre en ligne, et quoi regarder quand ca ne marche pas.

---

## Ou suis-je ?

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


## Ce qui configure le poste — et ce qui n'en fait pas partie

Deux choses a ne pas confondre : ce qui sert a **ecrire** du code (ce
conteneur, VS Code, Codex) et ce qui sert a **faire tourner** une application
(le panneau). Elles ne partagent aucun fichier de configuration.

**Le poste de developpement** — tout est pose automatiquement, sauf la connexion ChatGPT de Codex (une fois) :

| Ou | Quoi | Qui l'ecrit |
|---|---|---|
| `/workspace/.codex/config.toml` | reglages de Codex (sandbox, approbations, modele choisi) | ce conteneur, une seule fois — ensuite a toi |
| `/workspace/.codex/auth.json` | la connexion au compte ChatGPT, en `0600` | Codex, a la connexion |
| `/workspace/.codex/AGENTS.md` | le manuel de l'environnement, lu par Codex | ce conteneur, a chaque demarrage |
| `/etc/profile.d/codelab-*.sh` | `PGHOST`, `PGUSER`… pour `psql` sans argument ; `CODEX_HOME` | ce conteneur, a chaque demarrage |
| `~/.vscode-server` | serveur VS Code et extensions (monte depuis l'hote) | VS Code |
| `config/ssh/authorized_keys.d/` (sur l'hote) | une cle publique par machine autorisee | toi |

**Une application deployee** ne voit rien de tout ca. Elle lit, de la couche
la plus faible a la plus forte : les variables que le panneau lui passe, puis
le `.env` de son projet — voir [`workspace/README.md`](../workspace/README.md#configuration-et-secrets).


## Le cycle complet

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

### Creer un nouveau projet

Un projet **est un dossier** sous `/workspace/`. Il n'y a aucun registre a tenir
a jour, aucun fichier central a editer : ni `/workspace/definitions.py`, ni le
`docker-compose.yml`, ni `apps.json` a la main.

```bash
codelab new mon-projet          # dossier, depot git, .gitignore, AGENTS.md, CLAUDE.md
codelab new mon-projet --db     # ... et sa base de donnees
codelab new mon-projet --ouvrir # ... et la fenetre VS Code se rouvre dessus
```

Le projet recoit aussi `codelab_auth.py` et `codelab-auth.js` : la connexion avec CodeLab, prete a
brancher (voir leur en-tete, et « SSO des applications » dans `app-manager/README.md`). Une personne
connectee a CodeLab entre sans formulaire ; les autres gardent celui de l'application.

Et `codelab_web.py` / `codelab-web.js` : des pages 404 et 500 au style de CodeLab, sans pile d'appels
(la trace part dans le journal de l'application), et des en-tetes de securite a poser si
l'application le choisit — CSP stricte, `nosniff`, `Referrer-Policy`. Le panneau n'impose pas ces
en-tetes aux applications, a dessein : une politique imposee casserait la premiere qui charge une
carte ou un script d'ailleurs. Le module rend le bon choix facile, il ne le fait pas a leur place.

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
   Un projet qui a des dependances a lui les declare dans un
   `requirements-dagster.txt` et tourne alors a part (voir
   [`workspace/README.md`](../workspace/README.md#dependances)).

Le plus rapide reste de copier le projet `diagnostic`, qui montre un asset
Dagster, une application web et un module partage entre les deux :

```bash
cp -r /workspace/diagnostic /workspace/mon-projet
```

### Sans passer par le terminal

**Ctrl+Maj+B** (Cmd+Maj+B sur Mac) cree un projet, depuis n'importe ou dans VS Code. Pas de menu :
la tache « CodeLab : nouveau projet » est declaree tache de *build* par defaut, et c'est le raccourci
que VS Code reserve a celle-ci. Une boite de dialogue demande le nom, puis **la fenetre se rouvre
directement dans le nouveau projet** : on tape un nom, on se retrouve dedans, pret a ecrire.

La reouverture demande une fenetre VS Code : dans une session SSH ordinaire, `codelab new --ouvrir`
cree le projet et le dit, sans rien tenter. Une ouverture qui echoue ne fait jamais echouer la
creation -- le projet est deja sur le disque a ce moment-la.

Les autres passent par **Terminal > Executer la tache...** :

| Tache | Ce qu'elle fait |
|---|---|
| **CodeLab : nouveau projet** | `codelab new --ouvrir` — **Ctrl+Maj+B**, puis la fenetre s'ouvre sur le projet |
| **CodeLab : nouveau projet + base de donnees** | idem, avec `--db` |
| **CodeLab : creer la base d'un projet existant** | `codelab db` |
| **CodeLab : mettre a jour le manuel de l'agent** | `codelab agents` |

Les taches appellent l'outil `codelab` du conteneur plutot que de recopier ses commandes : la logique
reste a un seul endroit.

#### Un raccourci dedie a une autre tache

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


## Deployer

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

### Mettre a jour une application en ligne

**Déployer**, dans le menu « ... » de la tuile : build, puis redemarrage
seulement si le build a reussi. L'ancienne version continue d'etre servie
pendant le build, et un build casse ne met rien hors ligne — on ne remplace une
version qui marche que par une version qui compile.

« Lancer le build » et « Redemarrer » restent disponibles separement, pour
construire sans mettre en ligne ou relancer sans reconstruire.

### Application servie sous un sous-chemin

L'app-manager sert chaque application sous `/<nom>/`. Un front construit pour la
racine y affiche une page blanche et des 404 sur ses assets. Pour Vite :

```js
// vite.config.js
export default defineConfig({ base: '/mon-projet/' })
```

L'equivalent existe partout : `basePath` pour Next.js, `--base-href` pour
Angular, `homepage` dans le `package.json` pour Create React App.


## Git et GitHub

La configuration git vit dans `/workspace/.codelab/gitconfig`, et
`~/.gitconfig` n'est qu'un lien vers elle. `/home` est recree a chaque mise a
jour de l'image : un `git config --global` y etait perdu a chaque fois. Rien ne
change a l'usage, `git config --global` ecrit a travers le lien. Un ancien
`~/.gitconfig` est repris au premier demarrage (ou mis de cote dans
`~/.gitconfig.avant-codelab` si `/workspace` en a deja un), jamais efface.

Le fichier d'amorce est ecrit au premier demarrage, puis plus jamais
touche : `init.defaultBranch = main`, `pull.rebase = false`, et
`safe.directory = *`.

Ce dernier merite un mot. `/workspace` est ecrit par trois services aux
identites differentes — les sessions SSH en `vscode`, Dagster et app-manager
en root. Un depot dont le `.git` appartient a root fait refuser git avec
« dubious ownership ». Ici la propriete mixte est voulue par l'architecture,
pas un accident : le reglage est donc juste. Il ne le serait pas sur une
machine partagee, ou il revient a faire confiance aux depots des autres.

**Le fichier d'amorce ne porte ni identité ni secret**, et c'est délibéré.
Ajoutez-y votre nom et votre adresse, mais **jamais de secret** : il est dans
`/workspace`, lisible par l'agent de développement, Dagster et app-manager.
Pas de jeton, pas de `credential.helper store`.

### Se nommer

Aucun `user.name` ni `user.email` n'est pose : une valeur inventee signerait
vos commits d'un faux nom. Git refuse de commiter tant que vous ne vous etes
pas nomme, avec un message qui dit quoi taper. Une fois pour toutes -- le
reglage survit aux mises a jour :

```bash
git config --global user.name "Votre Nom"
git config --global user.email "vous@exemple.fr"
```

### Pousser vers GitHub

Apportez votre agent SSH avec vous, plutot que de deposer une cle dans le
conteneur :

```bash
ssh -A -p 2222 vscode@<IP-du-serveur>
```

Les cles restent sur votre poste, la session les emprunte, et l'emprunt
s'arrete avec elle. Verifier que l'agent suit : `ssh-add -l` doit lister vos
cles une fois connecte.

Ne deposez pas de jeton ni de cle de deploiement dans `/workspace` : le
dossier est lisible par tout ce qui tourne en `vscode` — dont l'agent de
developpement, qui execute des commandes sans qu'on relise chacune — et il
est monte par Dagster et app-manager. Un credential permanent y serait
utilisable par un agent travaillant sans personne devant.

## Les extensions Database Client

Deux extensions font partie du poste : elles permettent de parcourir les bases, lire un schéma et lancer
une requête sans quitter VS Code.

- **Database Client** (`cweijan.vscode-database-client2`) se connecte nativement à Postgres et à Redis.
- **Database Client JDBC** (`cweijan.dbclient-jdbc`) ne sert qu'aux autres bases, et demande alors un Java
  que l'image n'embarque pas.

- **Installées d'office** sur le serveur VS Code du conteneur. Ce serveur n'existe qu'après la première
  connexion Remote-SSH ; le conteneur attend qu'il apparaisse, puis installe les extensions dans la minute.
  Elles sont gardées dans `~/.vscode-server`, monté depuis l'hôte, et survivent donc aux mises à jour d'image.
- **Recommandées** dans `/workspace/.vscode/extensions.json`, pour que VS Code les propose même si
  l'installation automatique n'a pas pu se faire (pas de réseau). Un fichier existant n'est que complété
  (l'ancienne recommandation `ms-ossdata.vscode-pgsql` en est retirée), et un fichier avec des commentaires
  est laissé tel quel. L'ancienne extension PostgreSQL déjà installée n'est pas désinstallée : elle se
  retire d'un clic dans VS Code.

Pour s'y connecter : hôte `codelab-postgres`, port `5432`, utilisateur `codelab`, base au choix (celle du
projet). Le mot de passe s'affiche dans une session SSH avec `echo $PGPASSWORD`.

D'autres extensions s'ajoutent de la même façon avec `CODELAB_VSCODE_EXTENSIONS` dans le compose, les
identifiants séparés par des espaces. Une valeur vide n'en installe aucune.

## Discuter avec l'IA dans VS Code

**L'extension Codex, en panneau de chat.** Dans VS Code connecté en
Remote-SSH, installer l'extension *Codex – OpenAI's coding agent*
(`openai.chatgpt`) **côté serveur** (« Install in SSH: … »), puis cliquer
sur **Sign in with ChatGPT**. L'usage de Codex est compris dans les
abonnements ChatGPT : pas de clé, pas de facture à l'appel. Le modèle se
choisit dans le sélecteur de l'extension.

**Ou dans le terminal intégré** : `codex` dans le dossier du projet. Glisser
l'onglet du terminal vers le bord droit donne la même disposition éditeur +
conversation. Extension et terminal partagent la même connexion et la même
configuration (`/workspace/.codex`).

```
codex ──compte ChatGPT──▶ OpenAI
```

Un agent de code envoie des dizaines de requêtes par tâche, chacune avec une
vingtaine de milliers de jetons d'instructions : aucune part gratuite de
fournisseur n'y suffit, d'où le compte ChatGPT.

L'image portait aussi OpenClaw, une extension VS Code de chat, `acpx` et
`codex-acp` : un message passait par six étapes, chacune avec sa version et
sa configuration, pour un résultat que Codex donne seul. Ils sont retirés,
ce qui allège l'image de plusieurs centaines de Mo. Sur une installation
existante, le conteneur retire `/workspace/.openclaw` à son démarrage, ainsi
que les fichiers de `/workspace/.vscode/` qu'il avait écrits pour OpenClaw —
seulement s'ils sont intacts : un `settings.json` retouché à la main reste.

`~/.vscode-server` est **monté depuis l'hôte**, pour que les extensions
survivent à une mise à jour d'image. VS Code y garde une copie complète de
son serveur **par version** et ne supprime jamais les anciennes : un
nettoyage de temps en temps (supprimer le dossier, VS Code le réinstalle à
la connexion suivante) évite qu'il grossisse sans fin.

### VS Code : l'agent IA ou la vue Git redemarrent en boucle

C'est l'hote d'extensions de VS Code qui tombe, presque toujours faute de
memoire ou de fils d'execution dans `codelab-dev`. Le conteneur a 1280 Mo et
1024 processus (fils d'execution compris) depuis que 896 Mo et 512 ne
suffisaient plus. Pour le verifier sur le serveur :

```bash
docker stats --no-stream codelab-dev        # memoire et PIDS face aux plafonds
docker exec codelab-dev ps -eLf | wc -l     # fils d'execution en cours
sudo dmesg | grep -i -E "oom|memory cgroup" # le noyau a-t-il tue quelque chose ?
```

Si un plafond est encore atteint, remonte la ligne de `codelab-dev` dans
le compose (sans depasser le tiers de la machine), ou desactive les
extensions lourdes inutiles (Database Client JDBC lance une JVM).

## Codex

`codex` est installe dans l'image et `CODEX_HOME` pointe sur
`/workspace/.codex`, cree au demarrage du conteneur avec un `config.toml` par
defaut, ecrit une seule fois :

```toml
sandbox_mode = "danger-full-access"
approval_policy = "on-request"
```

Aucun fournisseur n'y est nomme : Codex emploie celui d'OpenAI, avec le
compte ChatGPT connecte. `~/.codex` mene a `/workspace/.codex`, pour que
l'extension et un Codex lance sans le profil de session trouvent la meme
configuration et la meme connexion.

### Se connecter

**Depuis l'extension** : *Sign in with ChatGPT*. VS Code transfere tout seul
le port de retour de la connexion (1455) vers le conteneur.

**Ou dans un terminal VS Code** : `codex login`, puis ouvrir l'adresse
affichee — meme transfert de port. Depuis un simple terminal SSH, sans VS
Code, ouvrir d'abord un tunnel depuis l'ordinateur local :

```bash
ssh -p 2222 -L 1455:127.0.0.1:1455 vscode@<IP-du-serveur>
```

C'est fait quand `codex login status` dit « Logged in ». Le jeton est range
dans `/workspace/.codex/auth.json` : il survit aux mises a jour d'image, et
le conteneur le remet en `0600` a chaque demarrage -- le dossier est partage
avec le groupe `codelab` (le panneau, Dagster), qui n'a pas a lire le compte
ChatGPT.

**Avec une cle d'API OpenAI** plutot qu'un abonnement :
`printenv OPENAI_API_KEY | codex login --with-api-key`.

```bash
cd /workspace/mon-projet
codex                 # session interactive (/model, /approvals, /new)
codex exec "..."      # commande unique, sans memoire entre deux appels
codex resume          # reprendre une session
```

### Donner le mode d'emploi a l'agent

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

**Claude Code** lit `CLAUDE.md`, pas `AGENTS.md`. La meme commande pose donc a cote un `CLAUDE.md`
d'une ligne, `@AGENTS.md`, qui importe le manuel : les deux agents lisent les memes consignes, ecrites
a un seul endroit. Un `CLAUDE.md` deja present n'est jamais modifie ; s'il n'importe pas `AGENTS.md`,
la commande le signale.

Le manuel se termine par une liste **« Avant de declarer une application prete »** — rendu a trois
largeurs, pages d'erreur, secrets, en-tetes, versions figees, base sauvegardee — que l'agent doit
parcourir avant d'annoncer qu'une application peut etre deployee.

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


## Quand ca ne marche pas

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

---

# Le conteneur lui-meme

Ce qui suit ne sert qu'a comprendre ou modifier l'image `codelab-dev`. Rien de tout cela n'est
necessaire pour developper au quotidien.

## Ce que fait l'image

- Base `python:3.13-slim` + `openssh-server`, `git`, `curl`, `sudo`, `postgresql-client`, `node`/`npm`.
- `codex`, l'agent de developpement en ligne de commande d'OpenAI (`npm install -g @openai/codex`),
  disponible dans le PATH de toute session SSH. `CODEX_HOME` est pose sur `/workspace/.codex` par
  l'entrypoint, avec un `config.toml` par defaut ecrit une seule fois : l'authentification et les reglages
  survivent a une recreation du conteneur, `codex login` n'est a faire qu'une fois. Usage quotidien dans
  plus bas dans ce fichier.
- Un utilisateur `vscode` (UID 1000), sans mot de passe, authentification **uniquement par cle publique**
  (`PasswordAuthentication no`, `PermitRootLogin no`).
- Un script de demarrage (`ENTRYPOINT` du `Dockerfile`) qui, a chaque lancement du conteneur :
  1. Pose le socle de permissions sur `/workspace` (groupe partage, setgid, `umask 002`) pour que les
     fichiers restent modifiables depuis SSH comme depuis Dagster.
  2. Pose `CODEX_HOME=/workspace/.codex` et cree ce dossier, pour que l'authentification de `codex`
     survive a une recreation du conteneur, et y recopie le manuel `AGENTS.md` comme
     instructions globales de l'agent (recopie a chaque demarrage : il decrit la stack, une version
     perimee donnerait des consignes fausses).
  3. Genere les cles hote SSH si elles n'existent pas encore dans le volume persistant, sinon reutilise celles
     deja presentes (voir [Cles hote SSH](#cles-hote-ssh-et-empreinte-stable)).
  4. Reconstruit `authorized_keys` a partir de `authorized_keys.d/`.
  5. Exporte les variables de connexion Postgres (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`) pour
     qu'elles soient disponibles dans toute session SSH interactive (voir
     [Variables Postgres dans une session SSH](#variables-postgres-dans-une-session-ssh)).
  6. Passe la main a `sshd`, qui devient le processus principal du conteneur.

`sshd` est le processus principal, pas un demon lance en arriere-plan derriere un veilleur. Deux
consequences : ses journaux d'authentification arrivent dans `docker logs codelab-dev` (c'est la
qu'on lit *pourquoi* un `Permission denied (publickey)` se produit), et il recoit le `SIGTERM` de
`docker stop`, donc l'arret est immediat au lieu d'attendre les dix secondes du delai de grace.

## Fichiers

| Fichier | Role |
|---|---|
| `Dockerfile` | Construction de l'image (paquets, utilisateur, dependances Python) |
| `entrypoint.sh` | Toute la logique de demarrage |
| `AGENTS.md` | Manuel de l'environnement lu par les agents (perimetre, base, Dagster, app-manager) |
| `codelab` | Outil de projet installe dans le PATH : `codelab new`, `codelab db`, `codelab agents` |
| `modeles/` | Modules deposes par `codelab new` : `codelab_auth` (connexion avec CodeLab) et `codelab_web` (pages d'erreur, en-tetes de securite), en Python et en Node |

Le `Dockerfile` et l'`entrypoint.sh` portent l'essentiel. `AGENTS.md` est une **ressource livree par l'image**, pas les instructions du depot CodeLab : il decrit l'environnement des projets, et l'image le recopie dans `$CODEX_HOME/AGENTS.md` puis, via `codelab agents`, dans les projets. `codelab` est sans extension parce que c'est un executable du PATH, pas un fichier a sourcer : l'`entrypoint.sh` reste le seul `.sh` du service, comme dans les trois autres. Les dependances Python sont declarees directement dans le `Dockerfile`
plutot que dans un `requirements.txt` separe : trois paquets ne justifient pas un fichier de plus, et
la liste se lit a l'endroit ou elle est installee.

> L'image inclut `dagster` et `dagster-webserver` pour permettre d'inspecter ou de tester du code Dagster
> directement depuis ce conteneur (ex. `dagster asset materialize` avant de passer par l'interface), en plus
> de l'instance dediee qui tourne dans les conteneurs `codelab-dagster*`.

## Variables d'environnement

| Variable | Origine | Usage |
|---|---|---|
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` | Fixees dans `docker-compose.yml` | Connexion a `codelab-postgres`. `PGDATABASE=diagnostic` : une session SSH atterrit dans la base du projet modele, pas dans la base d'instance de Dagster |
| `CODELAB_ENV_FILE` | Fixee dans `docker-compose.yml` | `credentials.env` : `POSTGRES_PASSWORD` y est lu pour construire `PGPASSWORD` |
| `CODELAB_VSCODE_EXTENSIONS` | Fixee dans `docker-compose.yml` | Extensions VS Code installees d'office sur le serveur VS Code (defaut : Database Client JDBC) |
| `REDIS_URL`, `REDISCLI_AUTH` | Lus dans `credentials.env` (bloc `codelab-redis`) | Connexion a `codelab-redis` : `REDIS_URL` pour le code, `REDISCLI_AUTH` pour que `redis-cli -h codelab-redis` s'authentifie seul |
| `CODELAB_SSH_DIR` | Fixee dans `docker-compose.yml` | Dossier unique des cles : `authorized_keys.d/`, `authorized_keys` (derive) + `host_keys/` |

## Volumes attendus

| Point de montage | Contenu |
|---|---|
| `/workspace` | Ton code — partage avec `codelab-dagster` et `codelab-app-manager` |
| `/var/lib/codelab/ssh` | Tout le SSH au meme endroit : `authorized_keys.d/`, `authorized_keys` et `host_keys/` (cote hote : `config/ssh/`) |
| `/var/lib/codelab/config` | Lecture seule — `credentials.env`, d'ou est lu le mot de passe Postgres |

## Droits sur les cles : le piege

`sshd` ne lit pas ces deux fichiers avec la meme identite.

- **Cles hote** : lues en `root`, avant toute bascule d'identite. `host_keys/` reste donc en `700 root:root`.
- **`authorized_keys`** : ouvert **apres** que `sshd` a pris l'uid de l'utilisateur cible
  (`temporarily_use_uid` dans les logs). Le fichier doit appartenir a `vscode` et le dossier qui le contient
  doit lui etre traversable, sinon `sshd` ne peut pas l'ouvrir.

L'entrypoint applique donc `755` sur `CODELAB_SSH_DIR`, `700` sur `host_keys/`, et
`chown vscode:vscode` + `600` sur `authorized_keys` a chaque demarrage — y compris sur un fichier ajoute a la
main depuis l'hote, ou il appartient a `root`.

Sans ca, le serveur repond `Permission denied (publickey)` alors que la cle est bien la et bien formee. Le
message cote client est identique a celui d'une cle absente : c'est pour cette raison que `sshd` est lance
avec `-D -e`, qui fait apparaitre la vraie cause dans `docker logs codelab-dev` :

```
Could not open user 'vscode' authorized keys '/var/lib/codelab/ssh/authorized_keys': Permission denied
```

## Cles hote SSH et empreinte stable

`apt-get install openssh-server` genere des cles hote **au moment du build de l'image**. Sans precaution, ces
cles changent a chaque reconstruction d'image (nouvelle version poussee, reinstallation...), ce qui declenche
l'avertissement `REMOTE HOST IDENTIFICATION HAS CHANGED` cote client — et, pire, VS Code Remote-SSH bloque
carrement la connexion (`MitmPortForwardingDisabled`) au lieu de simplement avertir.

Pour eviter ca : au premier demarrage, le script genere les cles hote (`rsa`, `ecdsa`, `ed25519`) dans
`/var/lib/codelab/ssh/host_keys` (mappe sur `/DATA/AppData/codelab/config/ssh/host_keys` cote hote) si elles n'y sont pas deja, puis
les copie vers `/etc/ssh/` avant de lancer `sshd`. Tant que ce dossier n'est pas efface, l'empreinte reste
identique a travers tous les redemarrages et reinstallations.

Si l'empreinte a change malgre tout (premiere migration vers cette version, ou dossier efface manuellement), sur
la machine cliente :

```bash
ssh-keygen -R "[<IP-du-serveur>]:2222"
ssh vscode@<IP-du-serveur> -p 2222   # accepter yes a la nouvelle empreinte
```

## Variables Postgres dans une session SSH

`sshd` n'herite **pas** de l'environnement du process qui l'a lance (comportement standard d'OpenSSH, y compris
dans ce script) : les variables `environment:` du `docker-compose.yml` ne sont donc pas automatiquement visibles
dans un shell ouvert via SSH. Le script de demarrage les ecrit explicitement dans deux endroits pour contourner
ca :

- `/etc/profile.d/codelab-pg.sh` — lu par les shells de connexion (login shells)
- `~/.bashrc` de `vscode` — lu par les shells interactifs non-login (le cas le plus courant pour un terminal VS
  Code ou une session `ssh` classique)

Verifier que ca fonctionne, une fois connecte :

```bash
env | grep ^PG
python3 -c "import psycopg; print(psycopg.connect().execute('SELECT version();').fetchone()[0])"
```

`psycopg.connect()` sans argument lit ces variables automatiquement — donc la base `diagnostic`, pas la base
d'instance de Dagster. Pour viser une autre base : `psql -d mon-projet`, ou
`psycopg.connect(dbname="mon-projet")`.

## Creer la base d'un projet

Chaque projet du workspace a sa propre base, nommee comme son dossier, avec un schema `dagster` dedans (voir
`workspace/README.md`). Celles listees dans `CODELAB_PROJECT_DBS` sont creees par `codelab-postgres` au
demarrage ; pour un projet ajoute ensuite, sans redemarrer la stack :

```bash
codelab db mon-projet
```

La commande est idempotente : relancee sur un projet existant, elle ne detruit rien et se contente de
reposer le schema et le `search_path`. Puis, dans le `.env` du projet : `CODELAB_DB=mon-projet`.

## Developper / tester localement

```bash
docker build -f dev/Dockerfile -t codelab-dev-test .   # contexte = racine du depot
docker run --rm -it \
  -v "$PWD/essai-ssh:/var/lib/codelab/ssh" \
  -p 2222:22 \
  codelab-dev-test
```

Deposer sa cle avant de lancer, sinon aucune connexion n'est possible :

```bash
mkdir -p essai-ssh/authorized_keys.d
cp ~/.ssh/id_ed25519.pub essai-ssh/authorized_keys.d/essai.pub
```

Sans variables `PG*` ni `credentials.env` accessible, la partie Postgres du script est ignoree : le script
attend le fichier 30 s, log un avertissement, puis demarre `sshd` quand meme. C'est deliberé — une base
indisponible ne doit jamais couper l'acces SSH, qui est justement le moyen d'aller la reparer.

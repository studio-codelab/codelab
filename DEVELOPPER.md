# Developper et deployer dans CodeLab

De la page blanche a l'application en ligne. Ce document remplace les notes
accumulees au fil des essais : tout ce qui suit correspond a l'etat actuel des
images, sans contournement a rejouer a la main.

---

## 1. Ou suis-je ?

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

---

## 2. Le cycle complet

```bash
ssh vscode@<IP-du-serveur> -p 2222     # 1. entrer dans le conteneur
cd /workspace/mon-projet               # 2. jamais depuis l'ordinateur local
codelab agents                         # 3. donner le mode d'emploi a l'agent
codex                                  # 4. developper (voir section 4)
npm run build                          # 5. verifier que le build passe
```

6. Ouvrir `http://<IP-du-serveur>:9001/`, **Ajouter un projet**, choisir le
   dossier : les commandes de lancement et de build sont proposees
   automatiquement (section 3).
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
cd /workspace
mkdir mon-projet && cd mon-projet       # ou : npm create vite@latest mon-projet
git init                                # optionnel, mais debloque "Git pull"
codelab agents                          # le AGENTS.md du projet
```

Puis, **selon ce que le projet fait** — les trois cas sont independants et se
cumulent :

| Le projet... | Ce qu'il faut en plus |
|---|---|
| a besoin d'une base | `codelab db mon-projet`, puis `CODELAB_DB=mon-projet` dans son `.env` |
| a des jobs Dagster | un `definitions.py` exposant `defs` ; il est decouvert seul, *Reload* dans Dagster pour le voir |
| est une application web | le declarer dans l'app-manager (section 3) |

Enfin, completer la section « Projet » du `AGENTS.md` — ce que fait le projet,
ses commandes — puis lancer `codex`.

Trois choses a ne pas oublier, dans l'ordre ou elles se retournent contre toi :

1. **`base` / `basePath`** si c'est un front, sinon page blanche derriere le
   sous-chemin `/mon-projet/` (section 3).
2. **`.env` dans le `.gitignore`** : `/workspace` n'est pas chiffre.
3. **Un nom de module unique** si le projet a du Dagster : tous les projets
   sont charges dans le meme processus, deux `utils.py` se marchent dessus.

Le plus rapide reste de copier le projet `diagnostic`, qui montre un asset
Dagster, une application web et un module partage entre les deux :

```bash
cp -r /workspace/diagnostic /workspace/mon-projet
```

### Sans passer par le terminal

Les memes gestes existent en **taches VS Code**, livrees dans
`/workspace/.vscode/tasks.json`. Menu **Terminal > Executer la tache...** (ou
Ctrl+Maj+P, « Executer la tache ») :

| Tache | Ce qu'elle fait |
|---|---|
| **CodeLab : nouveau projet** | dossier, `git init`, `.gitignore`, `AGENTS.md` |
| **CodeLab : creer la base du projet** | `codelab db`, schema et `search_path` |
| **CodeLab : mettre a jour le manuel de l'agent** | rafraichit le bloc CodeLab du `AGENTS.md` |

VS Code demande le nom du projet dans une boite de dialogue et affiche le
resultat ; il n'y a rien a taper. Les taches appellent l'outil `codelab` du
conteneur plutot que de recopier ses commandes : la logique reste a un seul
endroit.

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

---

## 3. Deployer

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

---

## 4. Codex

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

---

## 5. Quand ca ne marche pas

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
| Page blanche, 404 sur les assets | `base` non configure — section 3 |
| `Permission denied` sur un script | preferer `bash script.sh` a `./script.sh` |
| Build en echec | le journal complet est dans « Voir les logs » |
| Pastille rouge clignotante | boucle de crash : 5 echecs de suite, redemarrage automatique suspendu |
| Pastille orange, « Ne repond pas » | le process vit, mais rien n'ecoute sur son port : port en dur au lieu de `$PORT`, ecoute sur `127.0.0.1`, ou plantage du serveur apres le demarrage — les logs disent lequel |
| Un fichier n'est plus modifiable | supprimer `/workspace/.codelab/permissions-v1` et redemarrer la stack |

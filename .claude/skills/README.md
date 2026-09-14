# Skills embarquées dans le dépôt

Ce dossier contient **du code tiers recopié** (« vendoré »), pas une dépendance
résolue automatiquement. Il est là pour que Claude Code dispose de ces skills
dans **toutes** les sessions sur ce dépôt, y compris les sessions distantes.

Deux origines, décrites chacune dans sa section : **ui-ux-pro-max** (7 skills) et
**21st.dev** (7 skills, plus un serveur MCP).

# ui-ux-pro-max

## Ce qui est installé

| | |
|---|---|
| Projet | [ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) |
| Commit repris | `7f69fed6a2717900085f1bc3b263721f8ba025e2` (2026-09-10) |
| Contenu | 7 skills : `ui-ux-pro-max`, `design`, `design-system`, `ui-styling`, `brand`, `slides`, `banner-design` |
| Poids | ~11 Mo, 260 fichiers |

Une base de connaissances locale sur la conception d'interfaces : styles,
palettes, appariements de polices, règles d'accessibilité, types de graphiques.
Interrogeable par un script Python fourni.

## Pourquoi recopié, et pas installé comme plugin

Le projet prévoit une installation par le marketplace de Claude Code :

```
/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill
/plugin install ui-ux-pro-max@ui-ux-pro-max-skill
```

**`/plugin` n'est pas disponible dans l'environnement Claude Code distant**, qui
est celui utilisé pour travailler sur CodeLab. La seule voie qui fonctionne des
deux côtés est donc de poser les skills dans le dépôt.

## Ce qui a été modifié par rapport à l'amont

**62 invocations de scripts**, réparties sur trois skills. C'était nécessaire :
telles quelles, elles ne résolvaient pas, et les skills auraient été installées
**sans fonctionner** — le pire des deux mondes, puisque rien ne l'aurait signalé.

| Forme d'origine | Pourquoi elle échoue ici | Forme posée |
|---|---|---|
| `${CLAUDE_PLUGIN_ROOT}/.claude/skills/…` | Cette variable n'est définie que par le mécanisme de plugins | `.claude/skills/…` |
| `python scripts/search-slides.py` | Suppose que le répertoire courant est celui de la skill ; il est à la racine du projet | `python .claude/skills/design-system/scripts/…` |

Les chemins sont donc **relatifs à la racine du projet**, qui est le répertoire
de travail par défaut de Claude Code ici. Rien d'autre n'a été touché : ni les
données, ni les scripts, ni le contenu des règles.

## Ce qui a été vérifié avant de le poser

- **Aucune dépendance externe** : bibliothèque standard Python uniquement.
- **Aucun appel réseau** dans la skill principale. Le seul `urllib` rencontré
  construit une chaîne d'URL, il ne va rien chercher.
- **`subprocess`** est présent dans deux skills annexes, pour lancer la CLI
  `shadcn` et un validateur local. Visible, et cohérent avec leur rôle.
- **Aucun lien symbolique** — c'était le défaut connu de leurs versions
  antérieures à la 2.5.1.
- **Les trois points d'entrée répondent réellement** : `search.py`,
  `search-slides.py` et `logo/search.py` ont été exécutés après correction.

## Leurs propres tests : 14 répondent, 4 sont inertes

Les skills embarquent 18 fichiers de tests amont. Lancés ici :

| | |
|---|---|
| Passent | 14 |
| Ne démarrent même pas | 4, tous sur `StopIteration` à l'import |

Les quatre cherchent la racine du **dépôt amont** en remontant les dossiers
jusqu'à trouver un fichier qui n'est livré qu'avec lui
(`scripts/generate-catalog-summary.py`, l'arborescence `cli/assets/skills/`).
Ils ne la trouvent pas, et s'arrêtent avant la première assertion.

Ce n'est pas une régression introduite par la recopie : ces tests portent sur le
dépôt amont, pas sur la skill. En particulier `test_skill_script_paths.py`
vérifie une convention de chemins **relatifs au dossier de la skill** ; c'est
justement celle qui ne résout pas ici, où le répertoire courant est la racine du
projet — la raison même des 62 corrections ci-dessus. S'il démarrait, il
refuserait la forme qui, elle, fonctionne.

À ne pas prendre pour une panne lors d'une mise à jour.

## Mettre à jour

Il n'y a pas de mécanisme automatique : c'est une copie.

```bash
git clone --depth 1 https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git /tmp/uupm
rm -rf .claude/skills/*/ && cp -r /tmp/uupm/.claude/skills/. .claude/skills/
```

**Puis refaire les deux corrections de chemin ci-dessus**, et relancer les trois
scripts pour vérifier qu'ils répondent. Une mise à jour qui oublie cette étape
réinstalle des skills muettes.

## Ce que ça n'apporte pas

Ces skills traitent de **conception d'interface**. Elles sont utiles sur
`app-manager/app/dashboard.html` et `login.html` — 183 Ko de HTML et de CSS
écrits à la main. Elles n'ont rien à dire sur le Python, les composes, les
entrypoints ou Postgres, et leur propre documentation le précise.

`ui-styling` (5,8 Mo, soit plus de la moitié du poids) porte sur React et
shadcn/ui, absents de CodeLab. Elle peut être supprimée sans conséquence pour
les autres si l'on veut alléger le dépôt.

# 21st.dev

| | |
|---|---|
| Projet | [21st-dev/claude-code-plugin](https://github.com/21st-dev/claude-code-plugin) |
| Commit repris | `f76b07a` (2026-09-09), version `0.4.1` |
| Contenu | 7 skills : `21st-ai`, `21st-cli-use`, `21st-design-sync`, `21st-registry`, `21st-ui-build`, `21st-ui-explore`, `21st-ui-review` |
| Poids | 108 Ko, 10 fichiers |
| Serveur MCP | déclaré dans le `.mcp.json` à la racine du projet |

Même raison qu'au-dessus : `/plugin` n'existe pas dans l'environnement distant.

## Rien à corriger, cette fois

Contrairement à ui-ux-pro-max, ces skills sont **du Markdown seul** : aucun
script, aucun `${CLAUDE_PLUGIN_ROOT}`, aucun lien symbolique, aucun fichier
exécutable. La recopie est à l'identique, octet pour octet.

Le seul chemin en dur rencontré est `~/.config/21st/auth.json`, où la CLI range
le jeton après `21st login` — une information, pas une invocation. **Attention
dans `codelab-dev` : `/home/vscode` n'est pas sur un volume**, donc une
authentification faite là est perdue à la recréation du conteneur. Passer par
la variable `API_KEY_21ST` plutôt que par `21st login`.

## Ce qui ne marchera pas, et pourquoi

Il faut le dire avant de s'y fier :

| Point mesuré | Résultat |
|---|---|
| `21st.dev:443` depuis l'environnement distant | **refusé par le proxy de sortie** |
| `API_KEY_21ST` | non définie |
| `npx @21st-dev/cli --help` | fonctionne (le registre npm, lui, est joignable) |
| `npx @21st-dev/cli search button` | `Not signed in.` |

Les 7 skills pilotent toutes la CLI `21st` ou le serveur MCP. **Depuis une
session distante, aucune n'ira au bout** : la CLI s'installe, puis bute sur le
réseau. Sur ton Mac, où 21st.dev est joignable, il suffit d'une clé prise sur
<https://21st.dev/settings/api-keys> exportée en `API_KEY_21ST`.

## Ce que ça vise, et ce que CodeLab a

Ces skills parlent **React, Tailwind et shadcn/ui**. Vérifié dans le dépôt :

| | |
|---|---|
| `package.json` | **aucun**, nulle part |
| React / Tailwind / shadcn dans le panneau | **aucune occurrence** |
| Le panneau | `dashboard.html` (180 Ko) et `login.html` (44 Ko), HTML et CSS écrits à la main |

`21st-ai`, `21st-cli-use`, `21st-design-sync` et `21st-registry` supposent un
projet React : elles n'ont rien à dire sur ce panneau. `21st-ui-review` est la
seule dont la liste de priorités (noms accessibles, focus visible, cibles
tactiles, débordement responsive, `prefers-reduced-motion`, valeurs visuelles en
dur) s'applique telle quelle à du HTML écrit à la main — mais elle propose
`21st review <chemin>`, qui ne connaît pas ce format.

Autrement dit : **posées comme demandé, mais ce dépôt n'est pas leur terrain.**

## Le `.mcp.json` à la racine

```json
{ "mcpServers": { "21st": { "type": "http", "url": "https://21st.dev/api/mcp",
  "headers": { "x-api-key": "${API_KEY_21ST}" } } } }
```

C'est la moitié du plugin que la recopie des skills ne couvre pas. **Il ne
contient aucun secret** : seulement le nom d'une variable d'environnement, lue
au démarrage de la session. Claude Code demande l'autorisation avant de s'y
connecter, à chaque projet.

Sans `API_KEY_21ST`, le serveur ne se connecte simplement pas — pas d'erreur
bloquante.


# Skills embarquées dans le dépôt

Ce dossier contient **du code tiers recopié** (« vendoré »), pas une dépendance
résolue automatiquement. Il est là pour que Claude Code dispose de ces skills
dans **toutes** les sessions sur ce dépôt, y compris les sessions distantes.

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

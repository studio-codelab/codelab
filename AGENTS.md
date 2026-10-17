# CodeLab : consignes pour un agent qui modifie ce dépôt

> **À qui s'adresse ce fichier.** À tout agent (Claude Code, Codex…) et à tout humain qui modifie
> **le dépôt CodeLab lui-même**. Claude Code le lit par `CLAUDE.md`, Codex directement.
>
> **À ne pas confondre** avec [`dev/AGENTS.md`](dev/AGENTS.md), le manuel livré *aux projets* de
> `/workspace` : on n'y décrit pas comment modifier CodeLab, mais comment écrire une application qui
> tourne dedans. Une règle de ce fichier-ci ne s'applique pas aux projets, et inversement.
>
> **Priorité en cas de contradiction.** D'abord la demande explicite de l'utilisateur, puis ce
> fichier, puis [`SECURITE.md`](SECURITE.md). Les commentaires du code expliquent le pourquoi
> d'une implémentation, ils ne remplacent pas ces règles. Une règle de sécurité (section 3) ne se
> lève jamais sur simple déduction : si une demande semble l'exiger, dis-le et attends la réponse.

---

## 0. Méthode : mesurer avant de modifier

Chaque modification suit le même ordre, sans sauter d'étape :

1. **Lire avant d'écrire.** Lis la fonction, son bloc de commentaires, ses appelants (`grep` du nom
   dans tout le dépôt, tests compris), et la section correspondante de la carte des dépendances
   (section 2). Le code de CodeLab explique presque toujours *pourquoi* il est écrit ainsi. Un
   commentaire « Vécu : … » ou « Essayé et écarté : … » rapporte un vrai incident ou un choix déjà
   tranché : ne le défais pas sans raison nouvelle.
2. **Mesurer la portée.** Pose-toi ces quatre questions avant de toucher au code :
   - Qui charge ce fichier ? Le panneau, les applications, Dagster, le diagnostic.
   - Dans quel mode ? Site web ou application.
   - Sur quelle largeur ? Téléphone, tablette ou ordinateur.
   - Pour quel compte ? Administrateur, utilisateur ou visiteur.
3. **Faire le plus petit changement correct.** Ne réorganise pas un fichier pour une correction
   locale. Un nettoyage qui touche beaucoup de lignes fait l'objet de son propre commit.
4. **Prouver.** Lance la suite de tests (section 6), et pour tout ce qui s'affiche, rends la page
   dans Chromium (section 6.3). Un changement visuel se vérifie à l'œil, pas seulement par un test.
5. **Documenter.** Mets à jour les commentaires du code, puis les fichiers de documentation
   (section 7) dans le même commit.

Ce qu'un agent ne fait **jamais** sans demande explicite :
- fusionner une PR ou pousser sur `main` ;
- réécrire l'historique d'une branche partagée ;
- désactiver, sauter ou affaiblir un test ;
- élargir une permission, une origine, une CSP ou une liste blanche ;
- écrire un secret, une adresse mail ou un mot de passe dans le dépôt, un journal ou une réponse
  d'API.

---

## 1. Carte du dépôt

| Dossier | Ce que c'est | Lu par |
|---|---|---|
| `app-manager/` | **le panneau** : Flask + waitress, authentification, hub, proxy des applications, mails, sauvegardes | tout le monde |
| `app-manager/app/app.py` | le service : imports, chemins et constantes partagés, le chargeur de `serveur/`, puis `main` | — |
| `app-manager/app/serveur/` | le reste du service, en 14 morceaux de sections `# ---- titre ----`, exécutés dans la portée d'`app` (`MODULES_DU_SERVEUR`) | `app.py` |
| `app-manager/app/stockage.py` | SQLite : connexions, `MIGRATIONS` versionnées, sauvegarde cohérente | `app.py` |
| `app-manager/app/dashboard.html` | le panneau (hub, applications, paramètres…), page unique : balisage et CSS | navigateur |
| `app-manager/app/panneau/` | le script de `dashboard.html`, en modules assemblés dans l'ordre (`PANNEAU_MODULES`), mis en cache | navigateur |
| `app-manager/app/login.html`, `messages.html` | connexion, messagerie | navigateur |
| `app-manager/app/theme.css` | **toutes** les couleurs, formes, tailles ; **aussi servi aux applications** (`/theme.css`) | panneau **et** applications |
| `app-manager/app/mode.js` | le **mode** (site / application), la barre d'état, le verrou portrait | les trois pages |
| `app-manager/app/environnement-app.js` | **injecté par le proxy en tête de chaque page HTML de chaque application** | toutes les applications |
| `app-manager/app/ouvrir.html` | le cadre d'une application ouverte depuis le hub installé | mode application |
| `app-manager/vps/` | exposition par un VPS (nginx, WireGuard) ; hors image | l'administrateur |
| `dagster/` | Dagster et le proxy nginx devant Dagster et Mailpit (`proxy/`, dont l'interface `mailpit-ui/`) | — |
| `dev/` | l'image du poste de développement, l'outil `codelab`, les modèles, **le manuel des projets** `dev/AGENTS.md` | les projets |
| `postgres/`, `redis/`, `mailpit/` | les services de données, avec leurs scripts d'entrée et de santé | — |
| `workspace/` | ce qui est copié dans `/workspace` ; `workspace/diagnostic/` est l'application de diagnostic, et `workspace/diagnostic/tests/` la suite de tests du dépôt | — |
| `.github/workflows/` | `build-images.yml` (images, actions épinglées par SHA), `securite.yml` (Trivy) | CI |

Documentation de référence : [`README.md`](README.md) (installation),
[`FONCTIONNALITES.md`](FONCTIONNALITES.md) (ce que voit l'utilisateur),
[`SECURITE.md`](SECURITE.md) (garanties et **règles à ne jamais casser**),
[`INCIDENTS.md`](INCIDENTS.md) (que faire en cas de panne, chaque commande vérifiée), et le
`README.md` de chaque service.

---

## 2. Carte des dépendances : si tu touches X, pense à Y

Le tableau ci-dessous liste les couplages qui ne se voient pas en lisant un seul fichier. Si ta
modification touche une case de gauche, vérifie chaque point de droite.

| Si tu modifies… | Alors… |
|---|---|
| **`theme.css`** | Il est servi aux applications (`/theme.css`, liste `ROUTES_APPLICATIONS`) et repris dans `dagster/proxy/mailpit-ui/`. Une variable renommée casse leur affichage. Les seuils de largeur sont **760** et **1180 px**, écrits en chiffres (une variable CSS ne vaut pas dans une media query), et les mêmes partout : `theme.css`, `dashboard.html`, `environnement-app.js` (`appareil()`) et `/ouvrir.html`. |
| **`mode.js`** | Il est chargé **avant tout affichage** par `dashboard.html`, `login.html` et `messages.html`, et il verrouille le portrait sur un téléphone Android (voir « PAS DE ROTATION »). Il ne retourne plus la page sur iPhone : la contre-rotation a été retirée à la demande de l'utilisateur, ne la remets pas. Les tests découpent le fichier sur les marqueurs `(function(){`, `})();` et `if(!app) return;` : n'écris pas `(function(){` ailleurs dans le bloc (écris `function(e){`). |
| **`environnement-app.js`** | Le proxy le glisse **dans une balise `<script>` de pages qu'on ne contrôle pas**. Quatre contraintes en découlent. Pas de `</` dans le fichier, commentaires compris : il fermerait la balise. Un seul `__PANNEAU__`, remplacé par l'origine du panneau. Du **ES5**, puisqu'il tourne avant tout, sur n'importe quel navigateur. Et il ne doit **jamais** casser l'application : tout ce qui peut échouer passe par `try`, et une CSP stricte de l'application le bloque sans rien casser. Il simule iOS pour une application encadrée ; toute nouvelle simulation est documentée dans `dev/AGENTS.md` (« Ce que le hub te dit »). |
| **`ouvrir.html`** (servi par `ouvrir_dans_le_panneau`, `app.py`) | C'est une page à marqueurs (`__TITRE__`, `__ICONE__`, `__CIBLE__`, `__ORIGINE__`), remplie **en un seul passage**, chaque valeur étant échappée pour son contexte. N'écris pas de marqueur ailleurs, commentaires compris. Elle dialogue avec `environnement-app.js` par `postMessage`. **Les deux côtés vérifient l'origine exacte** (`e.origin !== origine`, `e.origin !== PANNEAU`) et valident chaque valeur reçue (expression rationnelle des couleurs). Son `frame-src` vient de `cadres_permis()` : ne l'élargis jamais à `*`. |
| **`serveur/*.py`** | Ce ne sont pas des modules : `app.py` les exécute dans **sa** portée, dans l'ordre de `MODULES_DU_SERVEUR`. Ils partagent donc les globales, et `monkeypatch.setattr(app, …)` atteint leurs appels. Un nouveau fichier ne tourne que s'il est ajouté à la liste (un test le vérifie). Chaque morceau s'analyse seul : une frontière ne coupe jamais une fonction. N'importe jamais un morceau. Les tests lisent le source réassemblé (`_source_du_panneau`), jamais `app.py` seul. |
| **`panneau/*.js`** | Les modules s'assemblent en UN script, dans l'ordre de `PANNEAU_MODULES` (`app.py`) : une seule portée, et un nouveau fichier n'est servi que s'il est ajouté à la liste (un test le vérifie). Chaque module s'analyse seul : une frontière ne coupe jamais une fonction. Le script est le même pour toutes les sessions et se met en cache : aucune valeur propre à la session n'y entre. Elles passent par `window.CODELAB_PAGE`, que `page_du_panneau` écrit dans la page avec `_json_dans_un_script` (« < » échappé). Les tests le lisent remis en ligne dans la page (`_page_panneau`, `_page_servie`). |
| **Le CSS de `dashboard.html`** | `data-style="design"` et `data-variante="bento"` sont toujours posés : une propriété de base que la règle préfixée redéfinit ne sert à rien, ne l'y remets pas. Un commentaire ou une accolade déséquilibrés effacent des règles sans aucun message : un test le vérifie. |
| **Un nom de route Flask** | Les noms d'application ne peuvent pas prendre celui d'une route (règle 21 de `SECURITE.md`) : la liste est tirée de la table des routes. Une route que les applications doivent atteindre va dans `ROUTES_APPLICATIONS`, avec sa justification. |
| **`stockage.MIGRATIONS`** | **Ajoute seulement à la fin**, ne modifie ni ne réordonne jamais une migration passée : elle a déjà tourné chez l'utilisateur. Une migration est idempotente, et elle est testée sur une base de la version précédente. |
| **Le cookie `codelab-hub` / `REGLAGES_HUB`** | Seules les valeurs de la liste blanche sont recopiées sur `<html>` : une nouvelle valeur s'ajoute à `REGLAGES_HUB`, sinon elle est ignorée en silence. Seule exception, la couleur personnalisée : un jeton `x` suivi de six chiffres hexadécimaux (`COULEUR_PERSO`), vérifié avant d'entrer dans `data-couleur-perso` et dans l'adresse de `/couleur.css`. Une teinte nouvelle du nuancier se calcule par `palette_accent` (jamais à la main), puis se recopie dans `theme.css`. |
| **Le ruban « ← CodeLab »** (`RUBAN_CSS`, `ruban_retour`) | La même feuille vit dans `dagster/proxy/mailpit-ui/app.css` et dans le `sub_filter` de `dagster/proxy/nginx.conf` : une modification se recopie dans les trois (un test le vérifie). Pas d'apostrophe dans `RUBAN_CSS` : la chaîne de nginx en est délimitée. |
| **Les applications par défaut** (`APPS_PAR_DEFAUT`, `est_app_par_defaut`, `CATEGORIE_CODELAB`, `categorie_de`) | Elles sont réservées à l'administrateur, et ce droit ne peut être accordé à personne (règle 12). Leur catégorie « CodeLab » n'est ni enregistrée, ni renommable, ni attribuable à un projet. Leurs descriptions (`DESCRIPTION_*`, `DIAGNOSTIC_DESCRIPTION`) tiennent en **40 caractères**, parce qu'une tuile en montre environ 45 (`DESCRIPTION_VISIBLE` dans `dashboard.html`). Une description vide est complétée au démarrage ; un texte personnalisé n'est jamais écrasé. |
| **Les notifications** (`SERVICES_PUSH`, `chiffrer_push`, `cle_vapid`) | `SERVICES_PUSH` est une **liste blanche anti-SSRF** : le panneau n'envoie de requête qu'aux services de push connus. Le chiffrement suit RFC 8291 (aes128gcm) et RFC 8292 (VAPID) ; une modification se vérifie contre les vecteurs de test des RFC (tests existants). |
| **Le proxy** (`proxy`, `injecter_*`) | Il ne réécrit que le `text/html` et recalcule la longueur. Il ne transmet **jamais** le cookie du panneau à une application (règle 19). L'ordre des injections compte : environnement d'abord, avant les scripts de la page. |
| **Les secrets transmis aux applications** (section « secrets transmis aux applications ») | Une application est du code arbitraire. Elle ne reçoit que le compte de **sa** base : ni le bloc du panneau, ni la clé de session, ni le mot de passe d'envoi des mails, ni l'administrateur Postgres. |
| **Les en-têtes et la CSP** (`CSP_PANNEAU`, `poser_les_entetes`, `HSTS`) | Ne relâche jamais une directive. Si une fonctionnalité semble l'exiger, cherche une autre implémentation, ou demande à l'utilisateur. Le HSTS ne se pose que si le panneau sait qu'il est servi en TLS. |
| **Un `Dockerfile` ou un workflow** | Les actions GitHub sont épinglées par SHA, les images de base par tag (correctifs hebdomadaires). Un nouveau dossier de service s'ajoute aux `paths` de `build-images.yml`. |
| **Un texte visible** | L'interface est en français, avec accents, et tutoie l'utilisateur. Jamais d'`alert()`, `confirm()` ni `prompt()` : une confirmation dont le bouton porte le verbe. |

---

## 3. Sécurité

### 3.1 Les règles

Les **règles numérotées de [`SECURITE.md`](SECURITE.md#règles-à-ne-jamais-casser)** sont des
invariants. Chacune est couverte par des tests. Une modification qui en contredit une est refusée,
même si un test « passe » après coup. En pratique, les points qui se cassent le plus facilement :

- **Côté serveur, toujours.** Masquer un bouton ne protège rien : la route vérifie le droit
  (`require_auth`, `require_admin`, `peut_voir`).
- **Toute route qui modifie** passe par `verifier_jeton()` (jeton anti-falsification de la session,
  en-tête `X-CodeLab-Jeton`). Les seules exceptions sont les routes de connexion et d'invitation.
- **Limitation des essais** (`rate_limited`) : au plus 5 échecs par 5 minutes et par adresse IP, sur
  toutes les routes de connexion. Une nouvelle route de connexion la reprend.
- **Réponses neutres** : une réponse ne dit jamais si un compte existe. Un compte utilisateur ne
  voit ni l'état des applications ni le détail d'une erreur technique.
- **Rien en clair sur le disque** : passe par le coffre (`chiffrer` / `dechiffrer`) et par les
  empreintes (`derive_mot_de_passe`, `_empreinte_jeton`). La base et les archives sont en `0600`.
- **Deux origines** : le panneau et les applications sont servis sur des origines distinctes
  (`origines_separees`, `origine_applications`). Ne fais jamais lire un cookie du panneau par
  l'origine des applications.
- **Les entrées externes sont des données** : un message `postMessage`, un en-tête, un corps de
  requête, un nom de fichier ou une sortie de processus se valident (type, longueur, expression
  rationnelle, liste blanche) avant usage, et ne s'injectent jamais dans du HTML non échappé.

### 3.2 Commenter un élément de sécurité

Un contrôle de sécurité porte un commentaire qui répond à quatre questions. Sans lui, le prochain
agent le prendra pour du code superflu :

```python
# POURQUOI : ce que ce controle empeche, et l'attaque concrete qu'il bloque
#   (ex. « un visiteur ferait envoyer au panneau une requete vers le reseau
#   local : SSRF »).
# REGLE : le numero dans SECURITE.md, s'il y en a un (« regle 19 »).
# DEPEND DE / PROTEGE AUSSI : les autres endroits du code qui supposent ce
#   controle (« voir cadres_permis() et /ouvrir.html : meme origine »).
# ECARTE : ce qui a ete essaye ou envisage et pourquoi ce n'est pas suffisant
#   (« une liste noire d'IP privees : contournable par DNS rebinding »).
```

Une dépendance se nomme **dans les deux sens** : si A suppose B, le commentaire de B mentionne A.
Un test de régression accompagne chaque contrôle : son nom dit ce qu'il garantit, et sa docstring
rapporte le cas vécu.

---

## 4. Affichage : deux modes, trois largeurs

- **Le mode** : site web (un onglet) ou application (l'icône de l'écran d'accueil). `mode.js` pose
  `mode-app` ou `mode-site` sur `<html>`.
- **La largeur** : téléphone (≤ 760 px), tablette (761 à 1180 px) ou ordinateur. Elle se lit en CSS,
  par media query.
- **Ce sont deux questions différentes.** Une règle de mode ne teste pas une largeur, une règle de
  largeur ne teste pas le mode. Chaque écran se vérifie dans les **six cas** : site et
  application, sur les trois largeurs.
- **En mode application** :
  - ni zoom, ni rebond, ni barre de défilement visible ;
  - la barre d'état prend la couleur du bandeau ;
  - sur téléphone, l'affichage reste vertical, par deux verrous seulement : le manifeste en
    portrait (`est_un_telephone`, `app.py`) et `screen.orientation.lock` sur Android. Sur iPhone,
    c'est le verrou d'orientation du Centre de contrôle. Couché malgré tout, un téléphone garde un
    panneau lisible : `@media (max-height:500px) and (orientation:landscape)` compacte le bandeau,
    le hub défile vers le bas (`majSensDuHub`), et les marges latérales prennent l'encoche
    (`env(safe-area-inset-left|right)`) ;
  - les applications du hub, encadrées par `/ouvrir.html`, reçoivent la simulation iOS
    d'`environnement-app.js`.
- **Au doigt, pas de survol** : ce qui n'apparaît qu'au survol a un autre chemin
  (`@media (hover:none)`), et un aplat de survol ne reste pas « collé » après un appui.
- **Pas de style en dur** : les couleurs, rayons et tailles viennent de `theme.css`.

---

## 5. Qualité du code

### 5.1 Structure

- **Un fichier, un rôle**, annoncé dans son en-tête (5.4). Dans un gros fichier comme un morceau de
  `serveur/` ou `dashboard.html`, le code se range en **sections titrées**
  (`# ------------------------------ titre ------------------------------`), dans l'ordre où l'on
  en a besoin. Une fonction nouvelle va dans la section de son sujet, pas en fin de fichier.
- **Une fonction fait une chose.** Au-delà d'environ 60 lignes, ou de trois niveaux d'imbrication,
  découpe-la en fonctions nommées pour ce qu'elles font.
- **Pas de duplication.** Avant d'écrire une fonction, cherche si elle existe (`grep` du verbe).
  Une constante partagée (seuil, liste blanche, limite) est définie **une fois** et référencée
  ailleurs par son nom.
- **Noms en français**, cohérents avec l'existant : `lire_…`, `ecrire_…`, `peut_…`, `est_…`. Une
  fonction qui protège quelque chose le dit dans son nom (`verifier_jeton`, `peut_voir`).

### 5.2 Lisibilité et indentation

- **Python** : 4 espaces, PEP 8, lignes de 100 caractères au plus ; les arguments longs s'alignent
  sous la parenthèse ouvrante.
- **JavaScript** : 2 espaces. Du **ES5** pour `mode.js` et `environnement-app.js`, qui tournent
  avant tout et partout ; du JS moderne dans les pages. Accolades sur la même ligne. Une condition
  complexe se nomme dans une variable (`var paysage = …`) plutôt que d'être répétée.
- **CSS** : dans `dashboard.html`, une règle courte tient sur une ligne (c'est la convention du
  fichier). Une règle longue passe à la ligne, avec une indentation d'un espace pour les
  propriétés suivantes. Les règles d'un composant restent groupées sous un commentaire de titre.
- **Shell** : `set -eu`, variables entre guillemets, 4 espaces.
- **L'indentation suit la logique**, pas la largeur de l'écran : on doit voir d'un coup d'œil ce
  qui dépend de quoi. Ne compacte pas une logique sur une ligne pour gagner de la place.

### 5.3 Commentaires

- **Ils disent pourquoi, pas quoi.** `# incremente i` ne sert à rien ; « `# Le clavier d'Android
  rend la page plus large que haute sans rien tourner : on lit le sens de l'appareil` » sert.
- **En français, sans accents dans le code** (`.py`, `.js`, `.css`, `.sh`), comme l'existant. Les
  textes affichés et les fichiers `.md` gardent leurs accents.
- **Les mots-clés réservés**, en tête de paragraphe de commentaire :
  - `Vécu :` (écrit `Vecu :` dans le code) : le cas réel ou la demande qui a motivé le code, cité
    si possible ;
  - `ESSAYE ET ECARTE :` : ce qui a été tenté et pourquoi ce n'est pas resté (mesure, bug, limite
    d'un navigateur). C'est ce qui empêche le prochain agent de refaire la même erreur ;
  - `POURQUOI`, `REGLE`, `DEPEND DE` : pour la sécurité (3.2) ;
  - `A FAIRE :` : une amélioration connue, aussi reprise dans l'en-tête du fichier (5.4).
- **Un commentaire faux est pire qu'aucun.** Si tu changes le comportement, réécris le commentaire
  dans le même commit. Les en-têtes de fichier vieillissent vite : relis-les.

### 5.4 En-tête de fichier

Chaque fichier de code commence par un bloc qui suit ce modèle (adapté à la syntaxe du langage) :

```text
CodeLab -- <NOM DU FICHIER EN UNE LIGNE>
---------------------------------------------------------------------------
ROLE        ce que fait ce fichier, et ce qu'il ne fait PAS.
CHARGE PAR  qui l'importe ou le sert (pages, proxy, Dagster, tests...).
CONTRAINTES ce qu'il ne faut jamais faire ici (ex. « pas de </ » pour
            environnement-app.js), avec la raison.
STRUCTURE   les sections, dans l'ordre (pour un gros fichier).
CHOIX       les implementations essayees et ecartees qui expliquent la
            forme actuelle (une ligne chacune, renvoi au commentaire
            detaille).
A AMELIORER les ameliorations envisagees, par ordre d'interet, avec ce
            qu'elles demanderaient.
```

### 5.5 Code inutile, code trop long

- **Retire le code mort** dès qu'il l'est devenu : fonction sans appelant, branche inatteignable,
  ancien style CSS, option abandonnée. Vérifie d'abord qu'il est bien mort : `grep` du nom dans
  **tout** le dépôt, pages HTML, tests et documentation compris (un nom peut être construit, comme
  `'param-' + o`).
- **Une rétrocompatibilité garde son code tant que des installations en ont besoin**, par exemple
  la reprise d'anciens fichiers. Elle porte alors un commentaire qui dit ce qu'elle reprend, et à
  partir de quand elle pourra partir. Quand elle part, le `README.md` du service nomme la dernière
  version qui la portait, pour qu'une installation en retard y passe d'abord. Les migrations de
  `stockage.MIGRATIONS` ne partent jamais (section 2).
- **Optimiser, c'est mesurer.** Avant de réécrire pour la vitesse, mesure (temps, taille servie,
  requêtes). Après, mesure encore, et note les chiffres dans le commentaire ou le commit. La
  lisibilité ne se sacrifie que pour un gain mesuré.
- **Simplifier sans changer le comportement** : un commit de nettoyage ne change rien de visible et
  la suite de tests reste identique. Si un test doit changer, c'est que le comportement change :
  ce n'est plus un nettoyage.

---

## 6. Tests et vérifications

### 6.1 La suite

```bash
python3 -m pytest workspace/diagnostic/tests -q        # depuis la racine
```

- Elle tourne en ≈ 50 s. Elle passe **entièrement** avant chaque push, et la CI la relance.
- **Un fichier par sujet** dans `workspace/diagnostic/tests/` (`test_securite.py`, `test_comptes.py`,
  `test_affichage.py`…). Les aides et constantes vont dans `commun.py`, que chaque fichier importe ;
  les fixtures, dont le filet qui détourne les chemins d'état, dans `conftest.py`. Un test nouveau
  va dans le fichier de son sujet.
- **Beaucoup de tests lisent le source** : ils vérifient qu'une chaîne, un marqueur ou une règle CSS
  est présent ou absent. C'est voulu. Ils figent une garantie (sécurité, affichage) que rien
  d'autre ne vérifierait. Quand un tel test échoue après ta modification, deux cas :
  - **la garantie tient toujours** sous une autre forme : mets le test à jour pour qu'il vérifie la
    nouvelle forme, avec la **même exigence**, et explique-le dans le commit ;
  - **la garantie ne tient plus** : c'est ta modification qui est fausse.

  Supprimer un test, l'affaiblir ou le marquer « ignoré » pour passer est **interdit**.
- **Un test par garantie nouvelle.** Son nom est une phrase
  (`test_l_application_ne_tourne_pas_sur_un_telephone`), sa docstring cite le cas vécu.
- **Les tests ne touchent jamais l'installation** : les fixtures détournent les chemins d'état.
  N'écris pas de test qui lit `/var/lib/codelab` ou `/workspace` réels.

### 6.2 Les fichiers injectés et les pages

- `node --check` sur tout `.js` modifié.
- Pour `environnement-app.js`, vérifie l'absence de `</` (`grep -c "</"` doit afficher 0) et la
  présence d'un seul `__PANNEAU__`.

### 6.3 Vérifier dans un navigateur

Un changement d'affichage se rend dans **Chromium par Playwright** :

- les six cas : site et application ; téléphone 390 × 844, tablette 1024 × 768 et 768 × 1024,
  ordinateur 1440 × 900 ;
- en mode application : `navigator.standalone` forcé par un script d'initialisation ; les bords
  d'iPhone émulés (`Emulation.setSafeAreaInsetsOverride`, 59 px en haut et 34 px en bas) ; la
  rotation (`Emulation.setDeviceMetricsOverride` avec `screenOrientation`), capturée par
  `Page.captureScreenshot`. La capture de Playwright garde la taille d'origine ;
- thème clair **et** sombre ;
- un compte administrateur **et** un compte utilisateur, dont les droits et les écrans diffèrent.

Pour un changement censé ne rien changer à l'écran, compare les captures avant et après, pixel par
pixel, et donne le résultat.

---

## 7. Documentation à tenir à jour

Mets à jour dans le même commit :

| Si tu changes… | Mets à jour |
|---|---|
| ce que voit ou fait l'utilisateur | `FONCTIONNALITES.md` |
| un chemin, un nom de conteneur, une commande de dépannage ou de restauration | `INCIDENTS.md` |
| une garantie, un contrôle, une exposition | `SECURITE.md` (et une règle numérotée si c'est un invariant) |
| un fichier, une route, une variable d'environnement, un volume | le `README.md` du service |
| ce qu'une application reçoit du hub (environnement, simulation iOS, couleurs, bords) | `dev/AGENTS.md` |
| une règle de ce fichier | ce fichier |

Les fichiers `.md` sont en français, avec accents. Les listes y restent courtes, et les tableaux
servent pour ce qui se compare.

---

## 8. Git et pull requests

- **Une branche par travail**, une PR par sujet, en **brouillon** tant que l'utilisateur n'a pas
  vérifié. Une PR ne se fusionne **que sur son accord explicite**.
- **Messages de commit en français** : un titre court au présent, puis un corps qui dit ce qui
  change et **pourquoi** (cas vécu, choix écartés, mesures).
- **La description de la PR** reprend les choix, les limites connues et les vérifications faites
  (tests, rendus, mesures), avec leurs chiffres.
- **Pas de secret dans l'historique.** Une fuite se signale ; une réécriture ne l'efface pas des
  copies déjà faites.

---

## 9. Liste de contrôle avant de pousser

1. J'ai lu le code touché, ses appelants et la ligne correspondante de la section 2.
2. Aucune règle de `SECURITE.md` n'est affaiblie ; chaque contrôle nouveau est commenté (3.2) et
   testé.
3. Mode et largeur restent séparés ; l'affichage est vérifié dans les cas concernés (6.3).
4. Le code mort que j'ai rendu mort est retiré ; les commentaires et l'en-tête du fichier sont
   justes.
5. `node --check` passe ; `environnement-app.js` ne contient ni `</` ni deux `__PANNEAU__`.
6. `python3 -m pytest workspace/diagnostic/tests -q` : tout passe, aucun test
   affaibli.
7. La documentation de la section 7 est à jour.
8. Le message de commit dit pourquoi ; la PR est en brouillon et dit ce qui a été vérifié.

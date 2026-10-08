# CodeLab -- regles de developpement

> Manuel de l'environnement, livre par l'image `codelab-dev` et recopie dans
> chaque projet par `codelab agents` (lu par Codex dans `AGENTS.md`, par Claude
> Code via `CLAUDE.md`). Il decrit la stack ou tournent les projets, pas le
> depot CodeLab lui-meme. Les consignes propres au projet sont sous ce bloc et
> dans son `README.md` ; en cas de contradiction, elles l'emportent.

Tu travailles dans le conteneur `codelab-dev` de CodeLab, un environnement
auto-heberge. Un projet est un dossier de `/workspace`. Il peut etre une
application web servie dans le **hub** (le panneau), un job Dagster, ou les
deux. Ce manuel dit tout ce qu'il faut respecter pour qu'un projet s'integre au
hub sans rien casser.

## 1. Perimetre

- **N'ecris que dans le dossier du projet courant.**
- **Ne modifie jamais** `/workspace/definitions.py`, `/workspace/README.md`,
  `/workspace/.codelab/`, `/workspace/.codex/` ni le dossier d'un autre projet.
  Si la tache semble l'exiger, dis-le au lieu de le faire.
- **Rien hors de `/workspace`** : `/home`, `/usr/local` et `/etc` sont recrees a
  chaque mise a jour. Pas de `sudo apt install`. Une dependance se declare dans
  le projet (section 5).
- **Chemins relatifs au projet**, jamais `/workspace/<projet>/...` en dur : le
  projet doit marcher renomme ou copie.
- **Ne lis ni n'ecris jamais** `credentials.env`, ni aucun fichier de
  configuration de la stack.

## 2. Les trois conteneurs

Le meme `/workspace` est vu par trois conteneurs qui **n'ont pas les memes
paquets** :

| Conteneur | Role | Ce qui y est installe |
|---|---|---|
| `codelab-dev` | ou tu travailles (SSH, VS Code) | python3, node, npm, git, psql, redis-cli, psycopg, dagster |
| `codelab-app-manager` | **build et fait tourner les applications web** | python3, node, npm, git, Flask |
| `codelab-dagster` | execute les jobs Dagster | dagster, dagster-postgres, psycopg2 |

- Un module importe par l'application **et** par Dagster ne doit importer ni
  Flask ni Dagster : code commun dans un module neutre, code web dans `app.py`,
  code Dagster dans `definitions.py`.
- Depuis `codelab-dev`, `127.0.0.1` designe `codelab-dev` lui-meme. Les autres
  services se joignent par leur nom (`codelab-postgres`, `codelab-redis`,
  `codelab-mailpit`).

## 3. Verifier avant de conclure

Ne declare jamais une tache terminee sans avoir execute ce que le projet
fournit : tests, `npm run build`, lancement du script. Si une commande echoue,
corrige et relance. Ce qui n'a pas pu etre verifie s'annonce comme tel.

## 4. Anatomie d'un projet

```
mon-projet/
├── AGENTS.md            manuel CodeLab (bloc gere) + section « Projet » a tenir a jour
├── CLAUDE.md            importe AGENTS.md pour Claude Code -- ne pas y ecrire
├── README.md            ce que fait le projet, comment le lancer
├── .gitignore           .env, node_modules/, vendor/, .dagster-venv/, dist/, build/
├── .env                 OPTIONNEL : configuration propre au projet, jamais versionnee
├── codelab_auth.py      connexion avec CodeLab (Python) -- codelab-auth.js en Node
├── codelab_web.py       pages d'erreur et en-tetes (Python) -- codelab-web.js en Node
├── app.py | package.json  OPTIONNEL : l'application web
├── definitions.py       OPTIONNEL : le cote Dagster (variable defs)
├── requirements-dagster.txt  OPTIONNEL : dependances du cote Dagster
└── icon.png             OPTIONNEL : l'icone du hub
```

Les modules `codelab_auth` et `codelab_web` sont fournis par CodeLab : ne les
modifie pas, pour pouvoir les remplacer par une version plus recente.

## 5. Application web servie par le hub

Le panneau lance la commande de lancement dans **son** conteneur, et la sert sur
`http://<serveur>:9002/<adresse>/`. Le port `9002` est une origine separee de
celle du panneau (`9001`). Le hub l'ouvre aussi dans un cadre (`/ouvrir.html`).

### Les regles qui la rendent joignable

1. **Ecoute sur `$PORT`**, fourni par le panneau. Jamais de port en dur.
2. **Ecoute sur `0.0.0.0`**, pas sur `127.0.0.1` :
   `app.run(host="0.0.0.0", port=int(os.environ["PORT"]))`.
3. **Le panneau retire le prefixe `/<adresse>/`** avant de transmettre la
   requete : l'application recoit `/`, `/factures/12`. Mais le navigateur, lui,
   est sur `/<adresse>/...`.
   - Dans le HTML, toutes les adresses sont **relatives** (`href="factures"`,
     `src="static/app.js"`), jamais `/factures`. Seule exception : `/theme.css`,
     servi par le panneau sur la meme origine.
   - Pour un front, regle la base des adresses : `base` (Vite), `basePath`
     (Next.js), `--base-href` (Angular) ou `homepage` (CRA).
   - Une redirection serveur vise une adresse relative.
4. **Front : build puis service statique.** Le build produit `dist/`, le
   lancement le sert (`python3 -m http.server $PORT --directory dist`). Ne
   deploie jamais un serveur de developpement (`vite dev`, `npm run dev`,
   Flask en `debug=True`).
5. **Il est surveille par le port, pas par une route.** Toutes les 10 s, le
   panneau tente une connexion TCP sur `$PORT`. Si rien n'ecoute, l'etat devient
   « Ne repond pas ». Aucune route de sante n'est requise.
6. **Un crash est relance** 5 fois au plus en 10 minutes, puis l'application
   reste arretee et une alerte part. Ne fais pas de boucle de relance toi-meme.
7. **Le journal, c'est la sortie standard et la sortie d'erreur.** Le panneau
   les ecrit dans le journal de l'application (rotation a 2 Mo). Pas de fichier
   de log a part. `PYTHONUNBUFFERED=1` est deja pose.
8. **Limites** : 32 Mo par requete entrante, 600 s de silence au plus sur une
   connexion. Le long-polling et les SSE passent ; un envoi de fichier plus gros
   doit etre decoupe.

### Ce qu'elle voit

- **Son propre uid**, jamais root, et le groupe `codelab` (umask `002`).
- **Une vue du disque reduite a elle-meme** : `/workspace` ne contient que son
  projet, et `/tmp` lui est propre. Ne compte sur aucun autre projet, aucun
  fichier commun.
- **Un `HOME` dedie** (caches npm et pip), non sauvegarde.
- **Un repertoire de travail** egal au dossier du projet.

### Ses variables d'environnement

| Variable | Contenu |
|---|---|
| `PORT` | le port interne a ecouter |
| `CODELAB_APP` | l'adresse de l'application (`mon-projet`) |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | le serveur Postgres partage |
| `POSTGRES_DB` | la base d'instance de **Dagster** : **ne t'en sers pas**, utilise la base du projet (section 6) |
| `REDIS_URL` (+ `REDIS_HOST`, `REDIS_PORT`, `REDIS_USER`, `REDIS_PASSWORD`) | le Redis partage |
| `MAIL_HOST`, `MAIL_PORT`, `MAIL_URL` | le SMTP de Mailpit, sans identifiants |
| `CODELAB_SSO_PUBLIC_KEY`, `CODELAB_SSO_ISSUER`, `CODELAB_SSO_AUDIENCE` | pour verifier l'identite signee (section 9) |

Les memes variables sont passees a la commande de build. Le `.env` du projet
**n'est pas charge** par le panneau : l'application le lit elle-meme (section 11).

### Dependances

- **Python** : seul Flask est garanti dans l'image. Installe le reste a cote du
  code, par la commande de build (`pip install --target vendor -r requirements.txt`),
  puis en tete de `app.py` :

  ```python
  import os, sys
  sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
  ```

- **Node** : `npm ci` avec un `package-lock.json` versionne.
- **Versions figees** (`==` dans `requirements.txt`, lockfile versionne) :
  l'audit hebdomadaire des dependances ne lit que les versions exactes.

### Dans le hub

- **Nom et adresse.** Le nom saisi dans le panneau est libre ; l'adresse en est
  derivee (minuscules, tirets : `Mon Site` donne `/mon-site/`). Un nom qui
  correspond a une route du panneau est refuse. Un nom de dossier simple sert
  bien d'adresse.
- **Icone.** `icon.png` (ou `icon.svg`, `favicon.png`, `favicon.ico`,
  `logo.png`, `logo.svg`) a la racine du projet, carre, 256 px ou plus. Un logo
  depose depuis le panneau devient `icon.png`, `icon.jpg` ou `icon.gif`.
- **Description** : une ligne, saisie dans la fiche de l'application.
- **Visibilite.** *Privee* : le panneau impose la connexion CodeLab et le droit
  sur l'application avant la premiere requete. *Publique* : ouverte a tous,
  possible seulement si une adresse publique est declaree. Ce reglage se fait
  dans le panneau, **jamais dans le code** : n'ajoute pas ta propre barriere
  devant une application privee.
- **Le cadre.** Le panneau pose `frame-ancestors` pour que le hub puisse
  l'afficher. Ne pose ni `X-Frame-Options` ni ton propre `frame-ancestors`.

## 6. Base de donnees

Une base **par projet**, nommee comme le dossier, avec un schema `dagster` dans
lequel pointe le `search_path` : un `CREATE TABLE clients` y atterrit sans
prefixe. Il n'y a ni base fourre-tout ni base `postgres`. L'extension `vector`
(pgvector) est disponible dans le schema `public`.

```bash
codelab db mon-projet     # cree la base et son schema (idempotent) ; elle sera sauvegardee chaque jour
psql -d mon-projet        # depuis codelab-dev, sans mot de passe a taper
```

- **Depuis `codelab-dev`**, `psycopg.connect(dbname="mon-projet")` suffit : les
  variables `PG*` sont posees.
- **Depuis l'application ou un job**, les `PG*` n'existent pas. Construis la
  connexion a partir des `POSTGRES_*` et du nom de la base du projet :

  ```python
  import os, psycopg
  base = os.environ.get("CODELAB_DB") or os.path.basename(os.path.dirname(os.path.abspath(__file__)))
  conn = psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"],
                         user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
                         dbname=base)
  ```

- Le nom de la base se redit dans le `.env` (`CODELAB_DB=mon-projet`) si le
  dossier change de nom.
- **Requetes parametrees** uniquement, jamais de SQL construit par
  concatenation.
- Ce qui doit durer va dans Postgres, pas dans Redis ni dans des fichiers.

## 7. Redis

Un serveur partage par toutes les applications, avec le meme mot de passe.

- **Prefixe chaque cle** par le nom de l'application (`mon-projet:session:42`).
- **Jamais** `FLUSHDB` ni `FLUSHALL` : cela vide aussi les donnees des autres.
- **Une duree de vie a tout cache** (`SET cle valeur EX 300`). Quand Redis est
  plein (192 Mo), seules les cles a duree de vie sont evincees ; les autres
  ecritures echouent (`OOM`).
- Redis n'est **pas sauvegarde** : rien d'irremplacable dedans.
- Les commandes d'administration (`CONFIG`, `MONITOR`…) sont desactivees.

## 8. Mails

```python
import os, smtplib
from email.message import EmailMessage
msg = EmailMessage()
msg["From"] = "Mon projet <noreply@exemple.fr>"
msg["To"] = destinataire
msg["Subject"] = "Bienvenue"
msg.set_content("Bonjour")
with smtplib.SMTP(os.environ["MAIL_HOST"], int(os.environ["MAIL_PORT"])) as s:
    s.send_message(msg)
```

- **Ni TLS ni identifiants** : Mailpit garde chaque mail et le relaie par le
  serveur d'envoi du panneau.
- **L'expediteur est remplace** par l'adresse du compte d'envoi ; le nom affiche
  est conserve.
- **Plafond commun** de 50 mails par heure et 200 par jour, pour toute la stack.
  Au-dela, les mails sont gardes sans partir. Pas d'envoi en masse.
- Un service d'envoi propre au projet se configure avec **sa** cle, dans le
  `.env`.

## 9. Comptes : se connecter avec CodeLab

Ne recode jamais un systeme de connexion de zero. Utilise `codelab_auth.py`
(Python) ou `codelab-auth.js` (Node) ; leur en-tete donne un exemple complet. A
chaque requete, dans cet ordre :

1. une session de l'application existe : on continue ;
2. sinon, la personne est connectee a CodeLab : `connexion_silencieuse` (Python)
   ou `connexionSilencieuse` (Node) verifie le jeton signe `X-CodeLab-Auth`, puis
   la fonction `trouver_ou_creer` du projet retrouve le compte local (colonne
   `codelab_sub`) ou le cree, sans formulaire ;
3. sinon : le formulaire de l'application, pour les visiteurs sans CodeLab.

L'identite contient `sub`, `nom`, `role` (`admin` ou `utilisateur`), `email` et
`email_verified`.

**Interdits**

- croire `X-CodeLab-User`, `X-CodeLab-Role` ou `X-CodeLab-Email` seuls : seul le
  jeton signe, verifie par le module, compte ;
- relier un compte local existant par l'adresse mail si `email_verified` n'est
  pas vrai ;
- deconnecter autrement que par `deconnecter(session)`, sans quoi CodeLab
  reconnecte aussitot.

**Liens**

- `lien_connexion` / `lienConnexion` : « Se connecter avec CodeLab », puis
  retour au meme endroit. Appelle `reprendre(session)` juste avant.
- `lien_deconnexion` / `lienDeconnexion` : se deconnecter de CodeLab aussi, avec
  confirmation.

Une application privee n'a rien a faire : la connexion est deja imposee.

## 10. Pages d'erreur et en-tetes de securite

Branche `codelab_web` des la premiere route :

```python
import codelab_web
codelab_web.pages_d_erreur(app)        # 404/500 au style CodeLab, trace dans le journal
codelab_web.entetes_de_securite(app)   # CSP stricte, nosniff, Referrer-Policy
```

```js
const codelabWeb = require("./codelab-web");
app.use(codelabWeb.entetesDeSecurite());
// ... les routes ...
codelabWeb.pagesDErreur(app);          // EN DERNIER, apres les routes
```

- **Jamais de pile d'appels dans une page.** Une exception se journalise ; le
  visiteur ne voit qu'une page « Erreur interne ».
- **La CSP par defaut interdit les scripts en ligne** (`<script>…</script>` dans
  le HTML, `onclick=`). Mets le JavaScript dans un fichier. Pour une ressource
  externe (carte, CDN), elargis la politique en passant `csp=` ; ne la retire
  pas.
- **Valide toute entree cote serveur**, et echappe tout ce que tu inseres dans le
  HTML (gabarits avec echappement automatique).
- **Formulaires qui modifient des donnees** : `POST` et un jeton anti-CSRF.

## 11. Configuration et secrets

- **Aucun mot de passe, jeton ou cle dans le code**, jamais.
- **La configuration du projet** va dans `.env`, a cote du code, et
  l'application le lit elle-meme. `.env` est dans le `.gitignore` : ne le
  versionne pas. Le `.env` l'emporte sur les variables recues.
- **Un service externe** (paiement, API tierce) utilise une cle propre a cette
  application, dans son `.env`, pour pouvoir la revoquer sans toucher aux autres.
- **Un fichier a soi** (base SQLite, televersements) se cree dans le projet,
  avec des droits `0600` s'il est sensible, et figure dans le `.gitignore`.

## 12. Dagster

Un projet expose ses jobs par un `definitions.py` dans son dossier, avec une
variable `defs`. Le fichier racine le decouvre seul.

```python
from dagster import Definitions, asset

@asset(group_name="mon-projet")     # regroupe les assets du projet dans l'interface
def mon_asset(context):
    context.log.info("bonjour")

defs = Definitions(assets=[mon_asset])
```

- Les modules voisins s'importent sans prefixe (`import outils`), mais **tous
  les projets partagent un meme processus** : deux modules de meme nom se
  marchent dessus. Prefixe-les (`outils_facturation.py`).
- **Noms uniques** : un nom d'asset, de job ou de planning doit etre unique dans
  toute l'installation. Prefixe-les par le projet.
- Le projet apparait au prochain *Reload* de Dagster. S'il ne se charge pas, il
  est ignore, avec sa trace dans `docker logs codelab-dagster`.
- **Un run en echec envoie un mail** a l'administrateur. N'echoue que pour une
  vraie panne ; journalise le reste en `warning`.
- Un planning se declare avec `default_status=DefaultScheduleStatus.RUNNING`,
  sinon il ne tourne pas tant qu'on ne l'active pas a la main. Evite l'heure
  pile.

**Dependances Dagster.** Le processus commun n'a que `dagster`,
`dagster-postgres` et `psycopg2`. Si un job a besoin d'autre chose, declare-le
dans `requirements-dagster.txt`, versions figees (`pandas==2.2.3`). Le projet
tourne alors dans son propre environnement Python, a son propre nom dans Dagster.

- Apres creation ou modification du fichier : `docker restart codelab-dagster`.
  *Reload* ne reconstruit pas l'environnement.
- Chaque projet isole coute 200 a 300 Mo : ne l'isole que s'il le faut, et ne
  fais jamais de `pip install` depuis le code.
- L'environnement vit dans `.dagster-venv/` : ne le versionne pas, ne le modifie
  pas a la main.

## 13. Le style de CodeLab

Une application du hub est vue **dans** CodeLab : elle en porte les couleurs. Le
theme est servi sur la meme origine que l'application.

```html
<link rel="stylesheet" href="/theme.css">
```

**Ne recopie jamais les valeurs** des variables : une palette recopiee divergera.

| Variable | Ce que c'est |
|---|---|
| `--bg` `--surface` `--surface2` | le fond de la page, les cartes, les fonds sourds |
| `--line` `--line2` | les traits : separation, et bordure d'un champ |
| `--txt` `--dim` `--dim2` | le texte, le texte secondaire, le texte tres discret |
| `--accent` `--accent-h` `--accent-soft` | **ce qui se clique**, et rien d'autre |
| `--ok` `--warn` `--err` (+ `-bg`, `-border`) | les etats : en ligne, a surveiller, en panne |
| `--r` `--r-s` `--r-xs` | les rayons : carte, bouton, pastille |
| `--t-xs` a `--t-xl`, `--t-b` | l'echelle de tailles de texte |
| `--sans` `--mono` | les polices : Manrope, et la chasse fixe pour le code |

1. **L'accent ne designe que ce qui se clique.** Les etats ont leurs couleurs :
   vert en ligne, ambre a surveiller, rouge en panne.
2. **La severite se lit au bord** : `box-shadow:inset 3px 0 0 var(--ok)` sur une
   ligne de tableau.
3. **Le trait plutot que l'ombre.** Une carte : `background:var(--surface)`,
   `border:1px solid var(--line)`, `border-radius:var(--r)`.
4. **Les chiffres s'alignent** : `font-variant-numeric:tabular-nums`. `--mono`
   est reserve a ce qui est du code.
5. **Aucune police ni script tiers** : un chargement chez Google ferait fuiter
   l'adresse IP de chaque visiteur.

```html
<link rel="stylesheet" href="/theme.css">
<style>
  body{background:var(--bg);color:var(--txt);font:var(--t-b)/1.5 var(--sans)}
  .carte{background:var(--surface);border:1px solid var(--line);
         border-radius:var(--r);padding:14px 16px}
  .etat{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;
        border-radius:999px;font-size:var(--t-xs);font-weight:700;
        color:var(--ok);background:var(--ok-bg)}
</style>
```

- **Le theme clair ou sombre suit le systeme.** Le choix fait dans le panneau
  vit dans son origine, illisible depuis l'application : ne cherche pas a le
  contourner.
- **Mobile d'abord** : le hub s'ouvre sur telephone. Mets
  `<meta name="viewport" content="width=device-width,initial-scale=1">`, sans
  defilement horizontal.
- **Exemple de reference** : `workspace/diagnostic/app.py`.

### Direction artistique

Les variables donnent les couleurs ; la direction dit **quoi en faire**. Le
cap : une interface d'outil moderne, nette et vivante -- sobre par defaut,
genereuse la ou elle compte. Six references, chacune pour une raison precise :

| Reference | Ce qu'on en prend |
|---|---|
| [shadcn/ui](https://ui.shadcn.com) | **La base.** Composants neutres et accessibles : boutons, champs, dialogues, menus, onglets, tableaux. Espacements reguliers, bordures fines, etats de focus visibles. C'est le niveau de finition attendu partout. |
| [Refero](https://refero.design) | **La methode.** Avant de dessiner un ecran (connexion, tableau de bord, reglages, liste vide), regarder comment de vraies applications l'ont resolu, et partir du schema qui marche. |
| [Motion](https://www.usemotion.com) | **La densite utile.** Taches, agenda, priorites : beaucoup d'information lisible d'un coup d'oeil, hierarchie franche, actions au survol, glisser-deposer. Le modele pour les ecrans de travail. |
| [Kokonut UI](https://kokonutui.com) | **Les details.** Cartes, listes, champs et boutons un cran plus soignes : micro-interactions, etats vides travailles, retours immediats apres une action. |
| [Aceternity UI](https://ui.aceternity.com) | **Le moment fort.** Grille bento, projecteur au survol, degrade anime : pour l'accueil ou l'ecran vitrine d'une application, une seule fois par page. |
| [Ron Design Lab](https://rondesignlab.com) | **L'audace visuelle.** Grands titres, compositions affirmees, couleur posee en aplat, illustrations : le ton des pages d'accueil et des ecrans vides. |

**Comment les appliquer dans CodeLab**

1. **On s'inspire, on n'importe pas.** Aucune librairie, police ni script
   charge depuis un CDN (regle 5 ci-dessus). Un composant shadcn, Kokonut ou
   Aceternity se **reecrit** dans le projet, avec les variables de
   `/theme.css`. Un projet React avec etape de build peut installer shadcn/ui
   (le code est copie dans le projet) et mapper ses variables sur celles de
   CodeLab ; un projet en HTML simple refait l'effet en CSS.
2. **Hierarchie d'abord.** Un titre fort par ecran (`--t-xl`, graisse 800,
   interlettrage resserre), une action principale par zone, le reste en
   secondaire (`--dim`). La taille et la graisse hierarchisent avant la couleur.
3. **Ecrans de travail sobres, ecrans d'accueil genereux.** Listes, tableaux et
   formulaires suivent shadcn et Motion. Les effets d'Aceternity et la
   composition de Ron Design Lab sont reserves a l'accueil, a l'ecran vide et
   a la vitrine -- jamais sur un formulaire.
4. **Le mouvement explique, il ne decore pas.** 150 a 250 ms, sur `transform`
   et `opacity` uniquement ; une apparition, un deplacement, un retour d'etat.
   Tout s'arrete sous `@media (prefers-reduced-motion:reduce)`.
5. **Les effets restent lisibles.** Pas de texte sur un degrade ou un flou sans
   contraste suffisant (4,5:1), en clair **et** en sombre. Un effet qui ne
   marche que dans un theme est retire.
6. **Chaque etat est dessine** : vide, chargement, erreur, succes. Un ecran
   vide dit quoi faire et porte le bouton pour le faire.
7. **Les regles du style CodeLab restent prioritaires** : l'accent pour ce qui
   se clique, les etats en vert, ambre et rouge, le trait plutot que l'ombre
   sur les ecrans de travail.

## 14. Sortir le projet de CodeLab

La fiche de l'application propose des fichiers de conteneurisation
(`Dockerfile`, `docker-compose.yml`, `.env.exemple`, `.dockerignore`,
`CONTENEUR.md`) deduits de ses commandes, a telecharger. S'ils sont deposes dans
le projet, garde-les a jour avec lui. Ils ne remplacent pas le deploiement par
le panneau.

## Avant de declarer une application prete

Ne dis pas qu'une application est prete a etre deployee sans avoir verifie
chaque point, et dis lesquels tu n'as pas pu verifier.

**Integration au hub**
- [ ] Ecoute sur `$PORT` et `0.0.0.0`, pas de serveur de developpement
- [ ] Adresses relatives (ou `base` / `basePath` regle) : aucune page blanche ni
      lien casse sous `/<adresse>/`
- [ ] Commandes de build et de lancement verifiees ; build reproductible
      (`npm ci`, `pip install --target vendor`)
- [ ] Icone `icon.png` carree a la racine ; description d'une ligne prete
- [ ] Ni `X-Frame-Options` ni `frame-ancestors` poses par l'application

**Rendu**
- [ ] Affichage correct a 375 px, 768 px et 1280 px de large
- [ ] Theme clair et sombre lisibles (jetons de `/theme.css`, aucune couleur en dur)

**Erreurs**
- [ ] `codelab_web` branche : 404 et 500 propres, aucune pile d'appels affichee
- [ ] Les exceptions arrivent dans le journal de l'application (sortie standard)

**Donnees et securite**
- [ ] `.env` dans le `.gitignore`, aucune cle ni mot de passe dans le code
- [ ] Une cle d'API par application chez chaque fournisseur, dans son `.env`
- [ ] Connexion via `codelab_auth` si l'application a des comptes ; aucun
      en-tete `X-CodeLab-*` cru sans le jeton signe
- [ ] En-tetes de securite poses (`entetes_de_securite`) ou ecart justifie
- [ ] Entrees utilisateur validees cote serveur ; requetes SQL parametrees
- [ ] Base du projet (jamais `POSTGRES_DB`) ; cles Redis prefixees, cache avec
      une duree de vie
- [ ] Versions figees (`requirements.txt` avec `==`, `package-lock.json`
      versionne) : c'est ce que l'audit hebdomadaire des dependances sait lire

**Performance**
- [ ] Page principale legere : images compressees, pas de bibliotheque chargee
      pour une seule fonction
- [ ] Aucune police ni script charge depuis un tiers sans raison

**Exploitation**
- [ ] Base creee avec `codelab db` (donc sauvegardee chaque jour)
- [ ] Noms Dagster (assets, jobs, plannings) prefixes par le projet
- [ ] Section « Projet » du `AGENTS.md` et `README.md` a jour : ce que fait le
      projet, ses commandes

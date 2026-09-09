# codelab-app-manager

Panneau de controle et reverse-proxy pour les applications que tu developpes dans `/workspace`, servi sur un
unique port (`9001`), protege par mot de passe. Autonome : aucune dependance a Supervisor — le cycle de vie des
applications est gere directement par ce service.

## Ce que fait le service

- Sert un dashboard (`http://<IP-du-serveur>:9001/`) organise autour d'un **bandeau fixe pleine largeur en haut** :
  a gauche, un bouton dedie pour reduire/etendre la barre laterale (transition douce — cubic-bezier, texte en
  fondu plutot qu'un `display:none` brutal), puis le logo CodeLab (fige, toujours visible que la barre soit
  repliee ou non — cliquer dessus ramene a la Vue d'ensemble depuis n'importe quelle page) ; a droite, le
  **bouton profil**. En dessous, la barre laterale reductible (etat retenu entre les sessions) avec 2 sections,
  et le contenu principal :
  - **Vue d'ensemble** — page d'accueil par defaut : compteurs (total / en ligne / arretees), barre de sante
    (repartition en ligne / arretee / erreur), ressources cumulees (CPU/memoire), et deux graphiques en barres
    (CPU et memoire par application en ligne). Pas d'actions rapides — uniquement des statistiques.
  - **Projets** — grille de tuiles compactes façon icônes iOS : l'icône occupe presque toute la tuile, le nom
    en dessous en petit texte centré, sans bordure ni fond autour (juste l'icône elle-même, deja arrondie).
    Cliquer sur une tuile ouvre le site dans un nouvel onglet. Un menu **"..."** discret (visible en permanence
    mais peu contrasté) donne acces a Modifier (masque tant que l'app tourne), Redemarrer (si en ligne),
    Activer/Desactiver, Rendre publique/privee, Lancer le build (si une commande de build est definie), Metriques, Voir les logs et Supprimer. Une pastille sur l'icone indique le statut : verte (en
    ligne), grise (arretee), rouge (erreur), rouge clignotante (boucle de crash — redemarrage automatique
    interrompu apres 5 echecs). Le bouton "Ajouter un projet" vit dans la barre d'outils. **Bascule grille/liste** dans la barre d'outils (preference retenue) : la vue liste garde le meme
    menu "..." mais affiche icone + nom sur une ligne, plus dense. Pas de bandeau de stats ici (deja dans Vue
    d'ensemble), pas de message "aucun projet" quand c'est vide — juste la tuile, pas de tri (toujours par
    ordre alphabetique, recherche disponible en haut).
  - **Compte, 1er niveau** — clic sur l'icone de profil (generique, pas le logo CodeLab) en haut a droite :
    mini-menu deroulant avec seulement **Parametres** et **Deconnexion**, pour un acces rapide sans changer de
    page.
  - **Compte, 2e niveau** — clic sur **Parametres** dans le mini-menu : page dediee complete (preference
    d'apparence Auto / Clair / Sombre, deconnexion). Pas de commande de copie des identifiants dans
    l'interface — `credentials.env` est deja directement lisible depuis le disque de l'hote.
- Pour chaque application activee, lance sa commande de demarrage comme sous-processus, sur un port interne
  attribue automatiquement (plage `9101`–`9140`).
- Fait office de **reverse-proxy interne** : `http://<IP-du-serveur>:9001/<nom-app>/` route vers le port interne de
  l'application correspondante — un seul port a exposer sur l'hote, quel que soit le nombre d'applications
  gerees.
- Protege l'ensemble du panneau et de son API par une **authentification par mot de passe**, generee
  automatiquement au premier demarrage (meme principe que le mot de passe Postgres de `codelab-postgres`).

## Fiabilite, observabilite, deploiement

- **Les applications et leurs builds ne tournent pas en root** — le service, lui, en a besoin au demarrage
  (groupe et setgid sur `/workspace`), mais tout ce qu'il lance bascule sur l'utilisateur `codelab-app`
  (uid 1001, groupe `codelab`). Ce que cela fermait : `credentials.env` est monte ici en `0600 root`, donc
  un processus enfant lance en root pouvait lire le mot de passe Postgres, celui du panneau et la cle de
  session. Le vecteur realiste n'est pas l'application mais son build — `npm ci` execute les scripts
  `postinstall` de toutes les dependances transitives, et une seule compromise suffit. `git pull` est
  concerne aussi : un depot peut porter des hooks.
  Les enfants recoivent un `HOME` dedie dans le volume d'etat (`home/`) : sans lui `npm` echoue sur son
  cache, `/root` n'etant plus accessible. Le marqueur de permissions passe a `permissions-v2` pour rejouer
  une fois la passe sur un workspace existant, dont les fichiers ont pu etre produits en root.
- **Le cookie de session du panneau n'est pas transmis aux applications** — elles sont servies sur la meme
  origine (`:9001/<app>/`), le navigateur le leur envoie donc, et le proxy le relayait tel quel : une
  application deployee pouvait lire la session admin et piloter le panneau. Seul ce cookie est retire, ceux
  de l'application passent. La meme origine reste : une XSS dans une application reste une XSS dans
  l'origine du panneau, et y remedier demanderait un port ou un sous-domaine par application — ce que ce
  proxy a justement pour but d'eviter.
- **Le panneau est le point d'entree de toute la stack** — il execute des commandes arbitraires (lancement,
  build) sous l'identite du service. Quatre garde-fous, dans cet esprit :
  - cookie de session en `SameSite=Lax` et `HttpOnly`. Les actions du panneau sont des `POST` sans corps :
    sans `SameSite`, n'importe quelle page ouverte dans le meme navigateur pouvait poster un formulaire vers
    `/api/toggle/<app>` et piloter la stack a l'insu de l'utilisateur, commande de build comprise ;
  - `X-Forwarded-For` n'est cru que si `APP_MANAGER_TRUST_PROXY=1`. Le service etant publie directement sur
    le port 9001, l'en-tete est pose par le client : le faire varier a chaque essai donnait un compteur neuf
    et annulait la limite de 5 tentatives par 5 minutes ;
  - comparaison du mot de passe en temps constant (`secrets.compare_digest`) ;
  - chemin d'une application borne a `APP_MANAGER_ROOT` a l'ajout **et** a la modification, comme le
    navigateur de dossiers l'etait deja.

- **Deployer en une action** — « Déployer » dans le menu « ... » enchaine build et redemarrage, et ne
  redemarre que si le build a reussi. L'ancienne version reste servie pendant le build. C'etait
  auparavant deux entrees de menu distinctes, dont la seconde s'oubliait : on lancait le build, la page
  ne changeait pas, et le process servait toujours l'ancien `dist/`.
- **Sonde d'ecoute** — « le process est vivant » et « l'application est joignable » sont deux choses
  differentes : un serveur qui plante dans son thread d'ecoute, ou qui n'a jamais pris son port, laisse
  un process bien vivant derriere lui, et la pastille restait verte devant une page blanche. Toutes les
  10 s, une connexion TCP sur `127.0.0.1:<port interne>` — exactement la cible du reverse proxy — donne
  l'etat reel : pastille orange et « Ne repond pas » quand rien n'ecoute. Pas de requete HTTP : le port
  ouvert est le signal cherche, et un 404 applicatif ne veut pas dire que l'application est en panne.
- **Node et git sont dans l'image** — ce service build les applications qu'il deploie : sans `npm`, tout projet
  front echouait en `npm: command not found` alors que la meme commande marchait en SSH, et la parade etait
  d'ecrire un `PATH` avec un numero de version de node fige dans la commande de build. Une commande de build
  s'ecrit donc simplement `npm ci && npm run build`. `git` reste installe non pour le panneau, qui ne
  l'appelle plus, mais parce que `npm ci` en a besoin des qu'une dependance transitive est declaree par une
  adresse git.
- **Detection du couple build + lancement** — a l'ajout d'un projet, le dossier est inspecte et les deux
  commandes sont proposees d'un coup (Vite, Astro, Parcel, CRA, Angular, Next.js, Django, Flask, statique).
  Pour un front, la suggestion sert le dossier produit par le build avec le `http.server` de Python plutot
  que le serveur de developpement du framework. Le tableau complet est dans
  [`../DEVELOPPER.md`](../DEVELOPPER.md).
- **`$PORT` dans l'environnement de l'application** — le port interne attribue est injecte dans le processus
  lance : la commande peut s'ecrire `--port $PORT` au lieu d'un numero en dur a resynchroniser.

- **Redemarrage automatique en cas de crash** — un thread de fond (`monitor_tick`, toutes les 10s) redemarre
  automatiquement toute application marquee active dont le process est mort de maniere inattendue (crash, pas
  un arret volontaire via le menu). Plafonne a 5 tentatives par tranche de 10 minutes : au-dela, l'application
  est laissee arretee et la pastille de statut clignote en rouge ("boucle de crash") jusqu'a intervention
  manuelle. Un arret volontaire (Desactiver) efface cet historique.
- **Rotation des journaux** — un fichier de log qui depasse 2 Mo est renomme en `.log.1` (ecrasant l'ancien
  s'il existe) au demarrage suivant de l'application concernee. Evite une croissance illimitee sur le disque.
- **Redemarrer** — action distincte d'Activer/Desactiver dans le menu "...", visible seulement si l'application
  tourne : l'arrete puis la relance immediatement (utile apres avoir modifie du code sans que l'app le recharge
  seule).
- **Historique de metriques** — CPU et memoire de chaque application en ligne sont enregistres a chaque
  actualisation du dashboard (~5s), conserves en memoire sur les ~30 derniers points (~2,5 min). Consultable via
  "Metriques" dans le menu "..." : deux mini-graphiques SVG (CPU, memoire), generes cote client sans
  dependance supplementaire.
- **Recherche dans les logs** — un champ de filtre au-dessus du journal en direct ; ne montre que les lignes
  correspondantes, cote client, sans requete supplementaire au serveur.
- **Build a la demande** — commande optionnelle (ex. `npm install`, `pip install -r requirements.txt`),
  configurable par application dans le formulaire d'ajout/edition, executee separement du lancement via
  "Lancer le build" dans le menu "..." (delai max 10 min). La sortie est ecrite dans le meme journal que
  l'application, consultable normalement.
- **Visibilite par application** — *publique* (accessible a qui a le lien, le comportement historique) ou
  *privee* (la meme session que le panneau est exigee). Le controle est applique dans le reverse proxy et non
  dans l'interface : une application privee reste fermee quel que soit le chemin emprunte pour l'atteindre.
  Une application declaree avant ce reglage, sans le champ, est traitee comme publique — une mise a jour ne
  ferme rien toute seule.
- **Double authentification (TOTP)** — desactivee par defaut, activable depuis *Parametres > Securite*. Voir
  plus bas.
- **Limite memoire optionnelle** — champ "Limite memoire en Mo" dans le formulaire d'ajout/edition ; applique
  une limite dure via `RLIMIT_AS` (herite par le process et ses enfants) au demarrage. Le process est arrete
  par le noyau s'il tente de la depasser — protege contre une fuite memoire qui saturerait le serveur entier.
  Pas de limite CPU equivalente : `RLIMIT_CPU` tue un process une fois un total de secondes CPU cumule atteint,
  ce qui n'a pas de sens pour un serveur cense tourner indefiniment.

> **Ce qui n'a volontairement pas ete ajoute** : un terminal web par application (redondant avec l'acces SSH
> deja fourni par `codelab-dev` ; un vrai terminal interactif necessiterait un PTY + des websockets, une
> surface de securite supplementaire pour un gain marginal) ; des domaines personnalises (necessiterait de
> controler du DNS externe, impossible a automatiser depuis l'interieur d'un conteneur — le routage `/nom/`
> actuel fonctionne sans dependance externe) ; toute interaction avec git (le
> bouton « Git pull » a ete retire : le depot d'un projet se met a jour depuis une session SSH, la ou l'on a
> deja l'authentification, l'historique et de quoi resoudre un conflit — un bouton qui lance un `git pull`
> sans pouvoir rien resoudre rendait surtout des echecs) ; un auto-deploiement par webhook GitHub
> (necessiterait un endpoint public joignable et une verification de signature).

> **Structuration visuelle** : chaque page regroupe son contenu dans des "zones" (fond legerement different du
> fond de page, titre de section en majuscules) plutot que de laisser les cartes flotter librement — Vue
> d'ensemble a une zone "Resume" (les 3 cartes de stats) et une zone "Activite" (les 2 graphiques), Projets a
> une zone "Projets" (la grille), Parametres a une zone par carte.

## Fichiers

| Fichier | Role |
|---|---|
| `Dockerfile` | Construction de l'image (Flask, psutil, requests, Node.js, git) |
| `entrypoint.sh` | Permissions partagees sur `/workspace`, puis demarrage |
| `app/app.py` | Le service : authentification, API, cycle de vie des process, reverse proxy |
| `app/dashboard.html` | L'interface du panneau |
| `app/login.html` | La page de connexion |
| `tests/` | Les regressions gardees (`python -m pytest app-manager/tests -q`) |

L'application vit dans `app/` : une seule ligne de `COPY` dans le `Dockerfile`, et la racine du service
reste lisible — l'image, le demarrage, la documentation, les tests.

Les deux pages etaient des chaines Python dans `app/app.py` — 67 Ko sur une seule ligne pour le tableau de
bord. Elles sont lues une fois au demarrage, et `__ROOT__` y est remplace par la racine du workspace au
moment de servir la page. Un fichier `.html` se relit, se diffe et se colore ; une chaine echappee, non.

## Authentification

Au tout premier demarrage, un mot de passe admin est genere aleatoirement et ecrit dans
`credentials.env` (permissions `600`), sous `APP_MANAGER_ADMIN_PASSWORD` — jamais defini par toi, jamais dans le
compose. C'est le seul endroit ou il est stocke, lisible directement
depuis le disque de l'hote sans `docker exec` :

```bash
cat /DATA/AppData/codelab/config/credentials.env
```

`upsert_shared_block()` n'ecrit que son propre bloc, commentaires inclus (delimite par
`# ===== codelab-app-manager =====` / `# ===== /codelab-app-manager =====`) — les blocs deposes par
`codelab-postgres` (et l'en-tete general du fichier) restent intacts, peu importe l'ordre de demarrage des deux
services. Voir [Fichier `credentials.env`](#fichier-credentialsenv) plus bas pour le detail du
mecanisme.

Les sessions sont signees avec une cle secrete elle aussi generee et persistee dans le meme fichier
(`APP_MANAGER_SESSION_SECRET`), donc la connexion survit a un redemarrage
du conteneur. Une bascule anti-bruteforce limite les tentatives de connexion echouees a 5 par tranche de 5
minutes, par adresse IP.

> **Le proxy `/<nom-app>/...` n'est volontairement pas protege par cette authentification** — seuls le dashboard
> et son API le sont. Une application que tu deploies reste directement joignable (utile pour tester un
> webhook, par exemple), independamment du mot de passe du panneau.

## Fichier `credentials.env`

`upsert_shared_block()` (appelee depuis `bootstrap_secrets()`) ecrit un **bloc entier** — commentaires de
documentation inclus — dans `/var/lib/codelab/config/credentials.env`
(`/DATA/AppData/codelab/config/credentials.env` cote hote), le meme fichier et le meme volume que
`codelab-postgres` utilise pour ses propres identifiants. Le bloc est delimite par des marqueurs
(`# ===== codelab-app-manager =====` ... `# ===== /codelab-app-manager =====`) et entierement remplace a
chaque demarrage : toutes les lignes entre les deux marqueurs sont supprimees puis reecrites d'un coup,
commentaires compris — pas juste les paires `CLE=valeur`, sinon la documentation s'accumulerait en double a
chaque redemarrage. Les blocs des autres services (l'en-tete general du fichier et `codelab-postgres`, tous
deux ecrits par `codelab-postgres` dans `docker-compose.yml`) restent intacts, quel que soit l'ordre de
demarrage. Si le volume partage n'est pas monte (tests locaux hors compose, par exemple), l'ecriture echoue
silencieusement sans bloquer le demarrage du service — c'est une commodite, pas une dependance critique.

## Variables d'environnement

| Variable | Role |
|---|---|
| `APP_MANAGER_STATE` | Ou vivent `apps.json`, les logs, le mot de passe admin et la cle de session — le seul dossier que le service ecrit |
| `APP_MANAGER_ROOT` | Racine du navigateur de dossiers et des chemins d'applications (`/workspace`) |
| `APP_MANAGER_SHARED_CONFIG` | Dossier de `credentials.env`, le fichier unique de secrets (`/var/lib/codelab/config`) |
| `MANAGER_PORT` | Port d'ecoute du panneau lui-meme (`9001`) |

## Volumes attendus

| Point de montage | Contenu |
|---|---|
| `/var/lib/codelab/app-manager` | `apps.json`, `logs/` et `diagnostic-inscrit` — l'etat des applications. Aucun secret : ils sont tous dans `credentials.env` |
| `/workspace` | Racine dans laquelle chercher/lancer les applications |

## Double authentification et exposition

Trois reglages n'ont d'interet que le jour ou ce panneau devient joignable au-dela du reseau local. Ils sont
**tous inactifs par defaut** : rien ne change tant qu'ils ne sont pas actives.

### Le second facteur (TOTP)

*Parametres > Securite > Double authentification.* Le panneau tire un secret, l'affiche en clair a saisir dans
une application d'authentification, et **n'enregistre rien tant qu'un code valide n'a pas ete fourni** — une
cle mal recopiee ne peut donc pas enfermer dehors. La desactivation exige elle aussi un code valide : une
session volee ne doit pas pouvoir retirer le second facteur.

L'algorithme (RFC 6238, SHA1, 6 chiffres, 30 s, tolerance d'un intervalle) est ecrit directement dans `app.py`
plutot qu'importe : il tient en vingt lignes de bibliotheque standard, et ce service n'a que trois
dependances. Un test verifie le vecteur officiel de la RFC — si ce test tombe, aucune application
d'authentification du marche ne saura plus se synchroniser.

Le secret vit dans `credentials.env`, sous `APP_MANAGER_TOTP_SECRET`. **En cas de perte du telephone** : vider
cette valeur dans le fichier et redemarrer le service suffit a revenir au seul mot de passe.

### Les deux variables d'environnement

Elles se posent au demarrage du service, pas depuis l'interface — et c'est deliberé : les activer depuis une
page servie en clair deconnecterait sur-le-champ la session qui vient de les activer.

| Variable | Quand | Pourquoi |
|---|---|---|
| `APP_MANAGER_HTTPS=1` | des qu'un reverse proxy termine le TLS devant | Pose le cookie de session en `Secure` : il cesse de circuler sur une connexion en clair |
| `APP_MANAGER_TRUST_PROXY=1` | derriere un reverse proxy **de confiance** uniquement | Fait lire l'adresse reelle du visiteur dans `X-Forwarded-For`. Sans reverse proxy, l'activer permettrait a n'importe qui de contourner la limite de tentatives en variant l'en-tete |

La page *Parametres > Securite* affiche l'etat des trois, pour verifier d'un coup d'oeil ce qui est en place.

### Ce que ces reglages ne couvrent pas

Les applications deployees sont servies sur **la meme origine** que le panneau (`:9001/<app>/`). Une faille
d'injection dans l'une d'elles reste une faille dans l'origine du panneau, second facteur ou non. Y remedier
demande de servir chaque application sur son propre nom d'hote — un routage par `Host` plutot que par chemin,
qui n'est pas implemente ici.

## API

| Route | Methode | Auth | Role |
|---|---|---|---|
| `/health` | GET | non | Sonde du `HEALTHCHECK` Docker |
| `/login` | GET / POST | non | Page de connexion / verification du mot de passe |
| `/logout` | POST | oui | Termine la session |
| `/api/apps` | GET | oui | Liste des applications, avec statut, metriques en direct, `crash_looping`, `visibility`, `has_build` |
| `/api/browse?path=...` | GET | oui | Navigateur de dossiers, borne a `APP_MANAGER_ROOT` |
| `/api/detect?path=...` | GET | oui | Suggere une commande de lancement **et** une commande de build a partir du contenu du dossier |
| `/api/add` | POST | oui | Enregistre une application existante (nom, chemin, commande, build, limite memoire) |
| `/api/app/<nom>` | PUT | oui | Modifie le chemin/la commande/le build/la limite memoire d'une application **arretee** |
| `/api/toggle/<nom>` | POST | oui | Demarre ou arrete une application |
| `/api/restart/<nom>` | POST | oui | Arrete puis relance immediatement une application |
| `/api/build/<nom>` | POST | oui | Execute la commande de build (si definie), sortie dans le journal |
| `/api/deploy/<nom>` | POST | oui | Build **puis** mise en ligne ; un build en echec ne touche pas l'application qui tourne |
| `/api/visibility/<nom>` | POST | oui | Bascule publique / privee (ou impose la valeur donnee) |
| `/api/securite` | GET | oui | Etat des reglages de securite (TOTP, HTTPS, cookie, proxy de confiance) |
| `/api/securite/totp/preparer` | POST | oui | Tire un secret candidat, sans rien enregistrer |
| `/api/securite/totp/activer` | POST | oui | Enregistre le secret candidat, apres verification d'un code |
| `/api/securite/totp/desactiver` | POST | oui | Retire le secret, apres verification d'un code |
| `/api/metrics/<nom>` | GET | oui | Historique CPU/memoire en memoire (~30 derniers points) |
| `/api/app/<nom>` | DELETE | oui | Retire une application du registre (le dossier n'est jamais touche) |
| `/api/logs/<nom>` | GET | oui | 120 dernieres lignes du journal |
| `/api/logs/<nom>/stream` | GET | oui | Flux de logs en direct (Server-Sent Events) |
| `/api/icon/<nom>` | GET | oui | Icone du projet si trouvee, sinon avatar SVG genere |
| `/<nom>/...` | * | **non** | Proxy transparent vers l'application, si elle est demarree |

## Auto-detection de la commande de lancement

`detect_command()` inspecte le dossier choisi et propose une commande sans jamais l'imposer (le champ reste
editable dans le formulaire) :

| Indice trouve | Commande suggeree |
|---|---|
| `package.json` avec `scripts.start` | `npm start` |
| `package.json` avec `main` | `node <main>` |
| `manage.py` | `python3 manage.py runserver 0.0.0.0:$PORT` |
| `app.py` / `main.py` | `python3 app.py` / `python3 main.py` |
| `Procfile` avec une ligne `web:` | le contenu de cette ligne |
| `index.html` seul | `python3 -m http.server $PORT` |

## Metriques CPU / memoire

`proc_stats()` agrege le CPU et la memoire du process lance (`bash -lc <commande>`) et de **tous ses
descendants** via `psutil` — indispensable puisque `bash -lc` est presque toujours un parent transparent, le
vrai travail se faisant dans un process enfant (`python3`, `node`, etc.).

Point d'implementation a connaitre : `psutil.Process.cpu_percent()` ne renvoie une valeur exploitable qu'a
partir du **second** appel sur un meme objet `Process` (le premier sert d'amorce et renvoie toujours `0.0`). Un
cache de `Process` par PID (`_proc_cache`) est donc maintenu entre deux appels a `/api/apps`, pour que chaque
requete affiche le delta depuis la precedente plutot qu'un `0.0` permanent.

## Fonctionnement du proxy

`_proxy()` relaie chaque requete (methode, en-tetes hors `HOP` — `connection`, `keep-alive`, etc. —, corps pour
`POST`/`PUT`/`PATCH`) vers `http://127.0.0.1:<port-interne>/<chemin>`, et renvoie la reponse telle quelle. Deux
cas particuliers geres explicitement :

- **Application non demarree** : reponse `503` avec une page d'explication plutot qu'une erreur de connexion
  brute.
- **Application en train de demarrer** : `urllib.request.urlopen` echoue avant que le process interne n'ecoute
  encore sur son port → reponse `502` invitant a reessayer, plutot qu'une erreur cryptique.

Les chemins contenant espaces ou accents sont re-encodes (`urllib.parse.quote(sub, safe="/")`) avant transmission
— necessaires car Flask les livre deja decodes, et une ligne de requete HTTP brute n'accepte ni espace ni
caractere non-ASCII tel quel.

## Cycle de vie d'une application

Le panneau ne cree jamais de projet : il n'ecrit rien dans `/workspace`. Un projet nait d'un `mkdir`, d'un
`git clone` ou d'un `code .` depuis une session SSH sur `codelab-dev` ; le panneau se contente de le declarer,
de le lancer et de le superviser. `POST /api/add` refuse d'ailleurs un chemin qui n'existe pas deja.

1. **Attribution du port** : `next_port()` prend le premier port libre dans `9101`–`9140`.
2. **Demarrage** (`start()`) : `subprocess.Popen(["bash", "-lc", <commande>], cwd=<chemin>, env={PORT: ..., ...})`,
   sortie standard et erreur redirigees vers `logs/<nom>.log`. Le process tourne dans son propre groupe
   (`start_new_session=True`), pour permettre un arret propre du groupe entier (pas juste du process racine).
3. **Arret** (`stop()`) : `SIGTERM` au groupe, jusqu'a 3 secondes pour un arret propre, puis `SIGKILL` si
   necessaire.
4. **Edition** (`PUT /api/app/<nom>`) : refusee tant que l'application tourne (`400`), pour eviter un
   changement de chemin/commande en plein vol.
5. **Reprise au redemarrage du conteneur** (`resume()`) : toute application marquee `enabled: true` dans
   `apps.json` est relancee automatiquement au demarrage du service — l'etat "actif" survit donc a un
   redemarrage du conteneur `codelab-app-manager` lui-meme.

## Inscription automatique du projet de diagnostic

Au tout premier demarrage, le panneau inscrit lui-meme le projet `diagnostic` — celui que le
conteneur `codelab-dagster` depose dans `/workspace` — puis lance sa commande de build et le
demarre. Une stack fraiche est donc verifiable sans aucune saisie : l'etape « Ajouter un projet »
etait la seule chose qui separait une installation neuve d'une installation constatee saine, et
c'est precisement celle qu'on saute quand on est presse.

| Champ | Valeur inscrite |
|---|---|
| Nom | `diagnostic` |
| Dossier | `/workspace/diagnostic` |
| Commande de lancement | `python3 app.py` |
| Commande de build | `pip install --target vendor "psycopg[binary]"` |
| Visibilite | **privee** — la page nomme les conteneurs, l'utilisateur SSH et l'etat de la base |

Les deux commandes sont ecrites en dur plutot que deduites par `detect_project()` : celle-ci
proposerait bien `python3 app.py`, mais rendrait une commande de build vide (elle cherche un
`requirements.txt`, que ce projet n'a pas) et l'application demarrerait sans pilote Postgres.

Le build tourne dans un thread, pas dans le demarrage : il demande un acces reseau et quelques
dizaines de secondes, pendant lesquelles le panneau doit rester ouvrable — c'est la seule interface
d'ou constater ce qui se passe. Un build en echec n'empeche pas le demarrage : la sonde
`Postgres (pilote)` affiche alors ce qui manque, ce qui vaut mieux qu'une application absente.

**L'inscription ne se rejoue jamais.** Trois garde-fous, dans cet ordre :

1. un marqueur `diagnostic-inscrit` dans le dossier d'etat, pose des que la question est tranchee —
   un projet supprime depuis le panneau ne reapparait pas au redemarrage suivant, meme raison
   d'etre que le marqueur de squelette cote `dagster/entrypoint.sh` ;
2. un `apps.json` non vide vaut « installation deja en service » : rien n'est ajoute, seul le
   marqueur est pose. Une mise a jour ne touche donc pas a un panneau existant ;
3. si le dossier n'est pas encore la — `app-manager` et `dagster` demarrent en parallele, et c'est
   `dagster` qui amorce `/workspace` — le marqueur n'est **pas** pose et l'inscription est retentee
   au demarrage suivant.

Supprimer le marqueur autorise une nouvelle inscription, a condition que le panneau soit vide.

## Developper / tester localement

```bash
docker build -f app-manager/Dockerfile -t codelab-app-manager-test .   # contexte = racine du depot
docker run --rm -p 9001:9001 \
  -v "$PWD/workspace-test:/workspace" \
  -v codelab-app-manager-test-state:/var/lib/codelab/app-manager \
  codelab-app-manager-test
```

Au premier lancement, le mot de passe admin genere apparait dans les logs du conteneur
(`docker logs codelab-app-manager-test`). Se connecter sur `http://localhost:9001/`, puis tester le cycle
complet (ajout d'un dossier existant, demarrage, proxy, metriques, logs en direct, edition, arret).

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
> et son API le sont. Une application **publique** que tu deploies reste directement joignable (utile pour tester un
> webhook, par exemple), independamment du mot de passe du panneau. Une application **privee**, elle,
> exige une session : voir [Une seule application, deux roles](#une-seule-application-deux-roles).

## Une seule application, deux roles

| | Administrateur | Utilisateur |
|---|---|---|
| Connexion | nom vide (ou `admin`) + mot de passe de `credentials.env` | son nom + son mot de passe |
| Second facteur | optionnel, a activer depuis Parametres | **obligatoire** |
| Page d'accueil | le hub | le hub |
| Declarer, editer, supprimer un projet | oui | non |
| Demarrer, arreter, deployer, build | oui | non |
| Visibilite, alertes, comptes, journaux | oui | non |
| Dagster (`/api/auth-check`) | oui | **non** |
| Ouvrir un projet | tous | ceux qu'on lui a autorises |

**L'application s'appelle CodeLab, et il n'y en a qu'une** : une seule page, servie a la meme
adresse (`/`) a tout le monde, avec le hub pour accueil. Il n'y a pas de bascule de mode a
comprendre — c'est le role qui decide de ce que le menu propose en plus :

- **Le hub** — la liste des projets ouvrables, l'accueil de tous les comptes (a ceci pres que
  l'administrateur y voit tous les projets, l'utilisateur seulement les siens).
- **Le menu lateral**, administrateur seulement — la vue d'ensemble, les applications (et la fiche
  d'un projet avec ses journaux), et les **utilisateurs**.
- **Le menu du compte**, en haut a droite — **Parametres** (l'affichage, le second facteur, la
  session) pour tout le monde ; **Configuration** (exposition, comptes, alertes) pour
  l'administrateur seul.

Le role est injecte dans la page pour qu'elle sache quoi afficher, mais **ce n'est qu'un confort
d'affichage** : chaque route d'administration verifie le role de son cote (`require_admin`), et une
page bricolee dans le navigateur ne donne aucun droit supplementaire.

`/espace`, l'ancienne adresse de l'espace utilisateur, redirige vers `/` : elle a pu etre mise en
favori.

Les comptes se creent depuis **Utilisateurs**, dans le menu lateral. Chacun porte la liste des projets qu'il peut
ouvrir ; la retirer prend effet immediatement, sans deconnexion — chaque controle relit le registre.

Trois points meritent d'etre explicites :

- **L'autorisation ne concerne que les projets prives.** Un projet public est ouvert a tout le monde,
  y compris a un visiteur non connecte : c'est le sens de « publique », et c'est ce qui permet de
  partager un projet par un lien. Pour restreindre un projet a certains comptes, il faut donc
  d'abord le passer en prive.
- **Dagster reste reserve a l'administrateur.** Son interface lance des jobs, donc execute du code
  sur la machine : y donner acces serait donner l'administration par la bande, quels que soient les
  projets autorises.
- **Le controle est dans les routes, pas dans l'interface.** Masquer un bouton ne protege rien : une
  route reste appelable a la main. `require_admin` garde les routes d'administration, et le proxy
  verifie l'autorisation projet par projet — connaitre l'adresse d'un projet prive ne suffit pas.

### Le second facteur, obligatoire pour les comptes utilisateurs

Ces comptes existent pour etre distribues — a un collegue, a un client. Leur mot de passe circule
donc par un canal qu'on ne maitrise pas, et sera reutilise ailleurs : c'est exactement le cas ou un
seul secret ne suffit pas. Un compte utilisateur n'ouvre **jamais** de session sur le seul mot de
passe.

L'administrateur, lui, garde le choix (Parametres > Securite) : le lui imposer d'office pourrait
l'enfermer hors de son propre panneau. Il est vivement conseille de l'activer avant toute
exposition.

Le parcours, en trois etats :

1. **Compte cree** — le panneau affiche « 2FA en attente ». Rien a transmettre a la personne en
   dehors de son nom et de son mot de passe.
2. **Premiere connexion** — apres le mot de passe, la page affiche un **QR code** a scanner avec
   une application d'authentification (la cle reste affichee dessous, pour qui prefere la saisir),
   puis demande le code produit. Entre les deux, **la session ne vaut rien** : elle ne porte pas
   `authed`, donc elle n'ouvre ni page ni application. Rien n'est enregistre tant que le code n'est
   pas valide — une cle mal recopiee ne peut pas enfermer dehors.
3. **Ensuite** — le code est exige a chaque connexion, et le panneau affiche « 2FA enregistree ».

L'administrateur ne connait a aucun moment la cle de quelqu'un d'autre : elle est tiree au premier
acces et n'apparait que sur l'ecran de la personne concernee. **Telephone perdu ou remplace** :
*Reinitialiser la 2FA* efface le secret, et la personne en enregistre un nouveau a sa prochaine
connexion. Les sessions deja ouvertes ne sont pas coupees par cette remise a zero.

Une inscription ne remplace jamais un facteur deja en service (409) : deux connexions en parallele
ne peuvent pas invalider le telephone que l'une des deux vient de configurer.

Les mots de passe des comptes sont derives (PBKDF2-HMAC-SHA256, 200 000 iterations, un sel par
compte) et stockes dans `utilisateurs.json` en `0600`. Une copie de sauvegarde du dossier d'etat
n'est donc pas une liste de mots de passe. Le compte d'administration, lui, n'est pas dans ce
fichier : son mot de passe vit dans `credentials.env`, et le nom `admin` est reserve.

## Adresse mail des comptes, et inscription libre

Chaque compte utilisateur porte une **adresse mail**, posee a la creation (par l'administrateur) ou
choisie par la personne dans ses *Parametres*. L'adresse relie le compte a quelqu'un de joignable.

**Verifier une adresse** consiste a y envoyer un code a six chiffres et a attendre qu'il revienne :
c'est le seul controle qui vaille, une expression reguliere stricte refusant des adresses valides
sans arreter personne. Volontairement le meme geste que le second facteur, que la personne connait
deja. Cette verification **ne remplace pas le second facteur et n'ouvre aucune session** : elle
atteste seulement que l'adresse existe et qu'elle est bien relevee par qui la declare.

- Le code est range **sous forme d'empreinte** dans `utilisateurs.json` : il n'a pas a y rester
  lisible a cote du nom du compte.
- Il vaut 15 minutes, tolere 5 essais, et un nouvel envoi est refuse pendant 60 secondes — un bouton
  « renvoyer » sans limite est un moyen d'inonder une boite mail qu'on ne possede pas.
- **Changer d'adresse annule la verification**, cote personne comme cote administrateur : sinon il
  suffirait de remplacer une adresse verifiee par une autre pour heriter de son statut.

**L'inscription libre** (« Creer un compte » sur la page de connexion) n'est ouverte que si un
serveur d'envoi est configure : sans mail, une adresse declaree ne peut pas etre verifiee, et la
creation de comptes devient un formulaire a remplir en boucle. Le parcours :

1. nom, adresse, mot de passe ; un code part vers l'adresse ;
2. tant que ce code n'est pas revenu, **le compte ne se connecte pas** — mot de passe juste compris ;
3. une fois confirme, il se connecte comme tout compte utilisateur, en enregistrant son second
   facteur a la premiere connexion.

**Un compte cree ainsi n'ouvre aucun projet** tant que l'administrateur ne lui en autorise pas :
c'est ce qui rend l'inscription libre sans consequence — au pire, des comptes vides. La creation est
comptee dans la limite de tentatives (5 par fenetre et par adresse IP), pour qu'un robot ne fasse pas
partir des mails en boucle. Un mail qui ne part pas retire le compte : sinon le nom resterait pris
par quelqu'un qui ne pourra jamais s'en servir.

## Cles d'acces (passkeys)

Une cle d'acces remplace le mot de passe **et** le code a six chiffres : l'appareil prouve la
possession, et l'empreinte (ou le code de l'appareil) prouve la personne. Rien a retenir, rien a
recopier — et **rien a hameconner**, puisque la cle ne signe que pour le domaine qui l'a
enregistree.

Elle s'ajoute depuis *Parametres > Cles d'acces*, une fois connecte : on enregistre une cle sur le
compte qu'on occupe deja. Plusieurs cles par compte (telephone, ordinateur), retirables une par une.

**L'enregistrement exige la verification d'utilisateur** (`user_verification: required`) : sans
empreinte ni code d'appareil, une cle ne serait qu'un facteur de possession, et ouvrir une session
sur cette moitie serait un recul par rapport au mot de passe + TOTP. La connexion l'exige aussi.

### Trois conditions que le navigateur impose

Elles sont **annoncees** dans la page plutot que subies — un bouton qui echoue toujours est pire
qu'un bouton absent :

1. **HTTPS.** Le navigateur refuse WebAuthn hors contexte securise (sauf sur `localhost`). Sur
   `http://192.168.1.20:9001`, les cles d'acces sont donc impossibles.
2. **Un nom de domaine, pas une adresse IP.** Le `rp_id` ne peut pas etre une IP.
3. **Toujours le meme nom.** Une cle enregistree sur `codelab.exemple.fr` ne fonctionne pas sur
   `192.168.1.20`, et c'est voulu : c'est ce qui la rend inhameconnable.

Un cas merite son message a lui : quand un proxy annonce `X-Forwarded-Proto: https` mais que
`APP_MANAGER_TRUST_PROXY` n'est pas pose, le panneau ne le croit pas (n'importe quel client peut
poser cet en-tete) et le dit — « declare ton proxy », pas « mets du TLS ».

Les cles vivent dans `passkeys.json` (`0600`), une entree par compte, avec le compteur de signature
que la norme demande de faire avancer : **il ne doit jamais reculer**, c'est la qu'une cle clonee se
trahit. La bibliotheque `webauthn` fait la cryptographie — ecrire soi-meme la verification d'une
signature ECDSA et le decodage CBOR d'une attestation, c'est le genre de code ou une erreur discrete
ne se voit que de celui qui la cherche. Import optionnel : sans elle, les cles d'acces sont
indisponibles et le reste ne bouge pas.

## Adresse publique, et ce que « publique » veut dire

Une application **publique** est servie **sans authentification** : c'est ce qui permet de partager
un projet par un simple lien. Tant que ce serveur n'est joignable que depuis ton reseau, le mot
promet une ouverture qui n'existe pas — il ne retire que l'authentification, sans rien partager.

Le panneau **ne propose donc pas de rendre une application publique tant qu'aucune adresse publique
n'est declaree** (*Configuration > Serveur > Adresse publique*), et la route refuse aussi le
changement. Trois consequences, voulues :

- une application **deja publique** n'est pas touchee, et peut toujours etre **refermee** — on ne
  bloque jamais le chemin qui referme ;
- une application **neuve nait privee** sur un serveur prive, meme si le formulaire demande autre
  chose : elle s'ouvre en une bascule, alors qu'une application ouverte par megarde ne se referme
  qu'apres coup ;
- des que l'adresse est declaree, tout redevient possible, sans redemarrage.

**Elle se declare a la main**, et c'est deliberé : le panneau ne peut pas savoir si le port 443 de
la box est ouvert, si le tunnel tourne, ni quel nom de domaine y mene. La renseigner, c'est dire
« j'ai fait le necessaire ». `APP_MANAGER_PUBLIC_URL` dans le compose l'emporte sur la page, et la
page le dit plutot que de laisser modifier ce qu'un redemarrage remettrait.

## Journal des acces

Qui s'est connecte, quand, depuis quelle adresse, et quelle application il a ouverte. Deux usages, et
deux seulement : **reconnaitre une tentative d'intrusion** (des echecs de connexion en rafale, une
connexion a une heure inhabituelle) et **savoir si un projet sert encore a quelqu'un** avant de
l'arreter. Il se lit dans **Utilisateurs** (connexions recentes, et la derniere connexion sous chaque
compte) et dans la fiche d'une application, onglet **Activite**.

Trois decisions qui comptent :

- **Une ouverture est notee apres les controles d'acces.** Un refus n'est pas une visite : les
  compter donnerait a une application fermee l'air d'etre tres frequentee.
- **Une ouverture par personne et par application, au plus une fois par quart d'heure.** Une page
  web, c'est des dizaines de requetes ; les compter toutes ne dirait plus rien de la frequentation
  et remplirait le disque.
- **Journaliser n'echoue jamais.** Disque plein, montage en lecture seule : l'evenement est perdu,
  le service continue. Refuser une connexion pour proteger son journal reviendrait a eteindre le
  service au moment ou on veut justement l'observer.

Le fichier est `acces.jsonl` (dans `STATE_DIR`), une ligne JSON par evenement, plafonne a 1 Mo avec
un `.1` conserve — la meme rotation que les journaux d'application. Un fichier texte se relit depuis
une session SSH le jour ou le panneau ne repond plus, ce qu'une base ne permettrait pas.

`GET /api/activite` est **reserve a l'administrateur** : il contient des adresses IP et le detail de
qui ouvre quoi.

## Categories

Une categorie est un intitule libre — « Outils », « Sites », « Donnees » — qui **regroupe les projets
dans le hub**, et rien d'autre : elle ne donne aucun droit, ne change rien au deploiement et
n'apparait pas dans le proxy. C'est du rangement.

- La liste se tient dans **Configuration > Categories**. Son ordre est l'ordre d'affichage des
  groupes : on la range, on n'impose pas un tri alphabetique.
- La categorie d'un projet se choisit dans sa fiche, onglet **Configuration**, parmi cette liste.
- Un projet sans categorie apparait a la fin, sous **Autres**. Tant qu'aucune categorie n'existe, le
  hub reste une seule liste — un titre « Autres » tout seul ne rangerait rien.

**Supprimer une categorie ne casse rien mais deplace des projets** : ceux qui la portaient
redeviennent non ranges, tout de suite, et la page dit combien. Un projet ne garde jamais une
categorie disparue — il serait range dans un tiroir que le hub n'affiche plus, donc invisible.

La liste vit dans `categories.json` (dans `STATE_DIR`), a cote de `apps.json` : une categorie existe
avant qu'un projet la porte, et survit a la suppression du dernier projet qui l'utilisait.

## Alertes par mail

Le panneau redemarre deja tout seul une application qui plante — mais il fallait avoir le panneau
sous les yeux pour le savoir. Une application qui tombe la nuit reste tombee jusqu'a ce qu'on pense
a regarder.

**Parametres > Alertes** : destinataires, serveur d'envoi, mail de test, interrupteur.

Ce qui declenche un mail, et ce qui n'en declenche pas :

| Evenement | Mail |
|---|---|
| L'application plante et redemarre toute seule | non — c'est le filet de securite qui fonctionne |
| Elle a epuise ses 5 tentatives en 10 minutes | **oui**, une fois |
| Elle reste a terre, tour de moniteur apres tour | non — un incident, un mail |
| Elle repond de nouveau | **oui**, un mail de retour |
| Tu l'arretes toi-meme depuis le panneau | non — personne n'a besoin d'un mail pour son propre geste |

Le mail de chute contient le dossier, la commande, le port et les 25 dernieres lignes du journal —
de quoi reconnaitre une trace d'exception sans se connecter au serveur.

La configuration SMTP vit dans le bloc `codelab-alertes` de `credentials.env`, **le meme** que lit le
capteur d'alerte de Dagster : une seule configuration d'envoi pour toute la stack. Le formulaire du
panneau reecrit ce bloc ; laisser le champ mot de passe vide conserve celui deja enregistre.

| Cle | Role |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | Serveur d'envoi. 587 avec STARTTLS, 465 avec `SMTP_TLS=ssl` |
| `SMTP_TLS` | `starttls` (defaut), `ssl`, ou `none` pour un relais interne non chiffre |
| `SMTP_USER`, `SMTP_PASSWORD` | Optionnels : un relais interne peut ne pas demander d'authentification |
| `ALERTE_FROM` | Rarement utile : l'expediteur suit `SMTP_USER`, que Gmail impose de toute facon |

Les destinataires et l'interrupteur vivent dans `alertes.json`, dans le dossier d'etat : une adresse
de destination n'est pas un secret, et la garder hors du fichier de secrets evite de le reecrire pour
un changement anodin.

**Gmail** : validation en deux etapes activee, puis un *mot de passe d'application* de 16
caracteres. Le mot de passe habituel du compte sera refuse.

Un envoi qui echoue (serveur injoignable, authentification refusee) est journalise et rien de plus :
une alerte qui ne part pas ne doit pas ajouter une panne a celle qu'elle signale.

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
| `APP_MANAGER_THREADS` | Threads du serveur HTTP (`16`). Le panneau relaie le trafic des applications : une application lente retient un thread pendant toute sa reponse |
| `APP_MANAGER_PUBLIC_URL` | Adresse publique du serveur (`https://codelab.mondomaine.fr`). Vide = serveur prive : le panneau ne propose alors pas de rendre une application publique |
| `APP_MANAGER_TIMEOUT` | Silence tolere sur une connexion avant fermeture, en secondes (`600`). Genereux, pour ne pas couper une application qui fait du long-polling |

## Volumes attendus

| Point de montage | Contenu |
|---|---|
| `/var/lib/codelab/app-manager` | `apps.json`, `logs/`, `alertes.json`, `utilisateurs.json`, `categories.json`, `acces.jsonl`, `exposition.json`, `passkeys.json` et `diagnostic-inscrit` — l'etat du panneau. Aucun secret en clair : les mots de passe des comptes sont derives, ceux des services sont dans `credentials.env` |
| `/workspace` | Racine dans laquelle chercher/lancer les applications |

## Double authentification et exposition

Trois reglages n'ont d'interet que le jour ou ce panneau devient joignable au-dela du reseau local. Ils sont
**tous inactifs par defaut** : rien ne change tant qu'ils ne sont pas actives.

### Le second facteur (TOTP)

*Parametres > Securite > Double authentification.* Le panneau tire un secret, l'affiche en **QR code** (et en
clair dessous), et **n'enregistre rien tant qu'un code valide n'a pas ete fourni** — une cle mal recopiee ne
peut donc pas enfermer dehors. La desactivation exige elle aussi un code valide : une session volee ne doit
pas pouvoir retirer le second facteur.

**Le QR code ne voyage jamais par l'adresse.** `GET /qr/totp.svg` lit l'inscription en attente dans la session
signee : un secret place dans une URL se retrouverait dans l'historique du navigateur, dans les journaux
d'acces et dans le `Referer` de la page suivante. La route repond 404 des que l'inscription est terminee.

La bibliotheque `qrcode` (Python pur, aucune dependance sous Linux) est installee dans l'image, mais le code
ne la suppose pas : sans elle, `qr_svg()` renvoie une chaine vide, la route repond 404, l'image se masque
d'elle-meme et la cle a recopier suffit. Le panneau reste lancable depuis un depot fraichement clone avec
Flask pour seule dependance.

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
| `/api/categories` | GET | oui | La liste des categories, dans l'ordre d'affichage (tous les roles) |
| `/api/categories` | PUT | oui | Remplace la liste (**administrateur**) ; renvoie le nombre de projets declasses |
| `/api/passkeys/etat` | GET | **non** | Les cles d'acces sont-elles utilisables ici (et sinon, pourquoi) |
| `/api/mon-compte/passkeys` | GET / POST | oui | Les cles du compte connecte ; en enregistre une nouvelle |
| `/api/mon-compte/passkeys/options` | POST | oui | Prepare l'enregistrement (defi range dans la session) |
| `/api/mon-compte/passkeys/<id>` | DELETE | oui | Retire une cle du compte connecte |
| `/login/passkey/options` | POST | **non** | Prepare une connexion par cle d'acces |
| `/login/passkey` | POST | **non** | Ouvre la session si la signature est bonne |
| `/api/securite/exposition` | PUT | oui | Declare (ou retire) l'adresse publique du serveur |
| `/api/activite` | GET | oui | Journal des acces et son resume (**administrateur**) |
| `/api/mon-compte` | GET | oui | Ce que la session dit d'elle-meme : nom, role, adresse et son etat |
| `/api/mon-compte/email` | POST | oui | Declare ou change sa propre adresse, et envoie un code |
| `/api/mon-compte/email/code` | POST | oui | Renvoie un code (une fois par minute au plus) |
| `/api/mon-compte/email/confirmer` | POST | oui | Confirme l'adresse avec le code recu |
| `/api/inscription` | GET | **non** | L'inscription libre est-elle ouverte (serveur d'envoi configure) |
| `/inscription` | POST | **non** | Cree un compte sans aucun projet, et envoie un code a l'adresse |
| `/inscription/confirmer` | POST | **non** | Confirme l'adresse et rend le compte utilisable |
| `/qr/totp.svg` | GET | **non** | Le QR code de l'inscription au second facteur en attente dans la session |
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

## Secrets transmis aux applications

`credentials.env` est en `0600 root` — il contient le mot de passe du panneau et le secret de
double authentification. Les applications lancees par le panneau, elles, tournent sous l'uid 1001
(voir `drop_privileges`) et ne peuvent donc pas le lire, alors que c'est precisement la que la
documentation leur dit de prendre le mot de passe Postgres. Le symptome etait un
`fe_sendauth: no password supplied`, une erreur qui ne dit rien de sa cause.

Le panneau, qui tourne en root, lit le fichier pour elles et leur transmet les valeurs **par
l'environnement**, au lancement (`start()`) comme au build (`run_build()`). C'est la couche la plus
faible du modele de configuration decrit dans `/workspace/README.md` : le `.env` du projet continue
de gagner.

Deux exclusions :

- **le bloc du panneau** (`APP_MANAGER_*`) n'est jamais transmis. Une application est du code
  arbitraire tournant sous un autre uid ; lui donner le mot de passe admin annulerait cette
  separation pour lui offrir l'acces au panneau ;
- **les cles reservees** (`PATH`, `HOME`, `PORT`, `PYTHONPATH`, `PYTHONHOME`, `LD_PRELOAD`,
  `LD_LIBRARY_PATH`) : elles changent la maniere dont le process s'execute plutot que ce qu'il
  fait, et une ligne `PATH=` ajoutee a la main dans `credentials.env` casserait sinon toutes les
  applications d'un coup, sans rien pour l'expliquer.

Le fichier est relu a chaque lancement, pas mis en cache : un mot de passe change est pris en
compte en redemarrant l'application, sans redemarrer le panneau.

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

## Identite visuelle

Les quatre ecrans -- connexion, second facteur, hub et outil de developpement -- partagent une
seule identite : **fond clair, barre de navigation en bleu nuit, accent indigo, cartes blanches**.

Trois decisions structurantes, et leurs raisons :

- **Fond clair, barre sombre.** Le contenu vit sur un gris tres clair, les cartes sont blanches, la
  navigation est en bleu nuit. Le contraste entre les deux donne la hierarchie sans avoir besoin de
  bordures partout. La barre reste sombre dans les deux themes : c'est l'element d'identite, il ne
  doit pas changer de nature selon l'heure de la journee.
- **Un seul accent.** L'indigo est reserve a ce qui est actionnable ou selectionne. Les etats
  (En ligne, Arretee, Ne repond pas, En erreur, Public, Prive) ont leurs propres couleurs, jamais
  l'accent -- sinon plus rien ne ressort.
- **Deux densites, une identite.** Le hub respire (cartes, icones, peu de texte) ; le mode
  developpeur est dense (tableau, chiffres alignes, actions compactes). C'est la meme interface,
  reglee pour deux usages.

La typographie est la pile systeme. Un panneau auto-heberge ne doit pas dependre d'un serveur de
polices tiers pour s'afficher correctement -- et la police de l'appareil est deja chargee, deja
lisible.

## La fiche d'une application

Chaque application a **une page**, ouverte en cliquant sa ligne dans le tableau. Avant, son etat,
ses metriques, son journal et sa configuration vivaient dans trois pop-up differentes qu'il fallait
ouvrir une par une pour se faire une idee.

En-tete : l'icone, le nom, la description, les pastilles d'etat et de visibilite, et les actions --
**Demarrer / Arreter**, **Redemarrer**, **Build** (si une commande de build existe), **Deployer**,
la bascule de visibilite, et **Ouvrir**. Puis quatre onglets :

| Onglet | Contenu |
|---|---|
| General | Adresse, port interne, reponse du port, visibilite, dossier, commandes, limite memoire, CPU et memoire du moment |
| Metriques | Courbes CPU et memoire sur les deux dernieres minutes |
| Journal | Le flux en direct, avec filtre |
| Configuration | Description, commandes, limite memoire, visibilite, emplacement, et la suppression |

Le flux de journal n'est ouvert **que** lorsque l'onglet Journal est affiche, et il est ferme des
qu'on quitte la fiche : une place de flux est une ressource cote serveur (voir le plafond plus bas),
la garder ouverte derriere un onglet qu'on ne regarde pas serait du gaspillage.

## Serveur HTTP

Le panneau tourne derriere **waitress**, un serveur WSGI de production en Python pur. Ce n'est pas
un detail cosmetique : le panneau ne sert pas que ses propres pages, il **relaie tout le trafic de
toutes les applications deployees**, ce pour quoi le serveur de developpement de Flask n'est pas
dimensionne — il le dit lui-meme au demarrage.

Si `waitress` n'est pas installe, le service retombe sur le serveur de Flask avec un message
explicite : un depot fraichement clone reste lancable sans rien installer de plus.

### Le plafond de flux de journal

Le suivi d'un journal en direct est un flux SSE, et **un flux occupe un thread tant qu'il est
ouvert**. Mesure faite sur une instance reelle : avec 16 threads, 20 flux simultanes rendaient le
panneau entierement muet — `/health` compris, donc le conteneur passait `unhealthy`.

Le panneau plafonne donc les flux simultanes a **la moitie du pool** (8 par defaut). Au-dela, le
flux de trop recoit un `503` qui nomme la cause, et la fenetre de journal l'affiche au lieu de
rester sur « Connexion... ». Verifie : avec 8 flux ouverts, le panneau repond toujours en 5 ms.

Deux details qui rendent le mecanisme fiable :

- **un battement toutes les 10 secondes** (un commentaire SSE, ignore par le navigateur). Il tient
  la connexion ouverte quand le journal est silencieux, et c'est aussi lui qui fait decouvrir au
  serveur qu'un onglet a ete ferme — donc qui libere la place, dans ce delai au pire ;
- **une duree de vie de 10 minutes**, apres quoi le flux se termine et `EventSource` se reconnecte
  tout seul. Un onglet oublie ne monopolise pas une place indefiniment.

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

# Fonctionnalités de CodeLab

Le catalogue complet de ce que fait CodeLab : chaque fonctionnalité, où elle se trouve et où elle est
détaillée. La sécurité a son propre document : [`SECURITE.md`](SECURITE.md).

## Sommaire

1. [Le panneau](#1-le-panneau)
2. [Applications](#2-applications)
3. [Comptes et accès](#3-comptes-et-accès)
4. [Mails et alertes](#4-mails-et-alertes)
5. [Messages : boîte de réception, projets, planning, feuille de route](#5-messages--boîte-de-réception-projets-planning-feuille-de-route)
6. [Poste de développement](#6-poste-de-développement)
7. [Projets et Dagster](#7-projets-et-dagster)
8. [Données, sauvegardes, restauration](#8-données-sauvegardes-restauration)
9. [Diagnostic](#9-diagnostic)
10. [Exposition sur Internet](#10-exposition-sur-internet)
11. [Installation et mise à jour](#11-installation-et-mise-à-jour)

---

## 1. Le panneau

`http://<serveur>:9001/` — détails : [`app-manager/README.md`](app-manager/README.md).

| Page | Adresse | Pour qui | Ce qu'on y fait |
|---|---|---|---|
| Hub | `/` | tous | les applications ouvrables, une mosaïque à composer, avec ses widgets ; laisser un message à l'administrateur |
| Vue d'ensemble | `/apercu` | admin | compteurs (en ligne, arrêtées, en erreur), charge CPU et mémoire |
| Applications | `/applications`, `/applications/<nom>[/<onglet>]` | admin | liste, puis fiche d'une application |
| Utilisateurs | `/utilisateurs` | admin | comptes, invitations, journal des accès, carte d'activité |
| Messages | `/messages`, `/messages/planning`, `/messages/feuille`, `/messages/projet/<id>` | admin | application à part, ouverte depuis sa tuile du hub (voir § 5) |
| Paramètres | `/parametres/<onglet>` | tous | onglets selon le rôle (voir plus bas) |

- **Une seule application pour tous les rôles.** Le rôle décide de ce que le menu propose. Chaque
  rubrique a son adresse : recharger y ramène, « Précédent » y revient.
- **Deux styles** (*Paramètres > Apparence*), retenus par navigateur. Le reste du panneau
  est le même : menu en pilule, barre du bas sur téléphone, réglages en fenêtre.
  - **Erreurs** : chaque message dit quoi faire ensuite. Une panne du serveur porte une
    **référence** courte (« 7F3A2C »), écrite aussi dans `docker logs codelab-app-manager` ; un
    message d'erreur reste affiché jusqu'à ce qu'on le ferme.
  - **Annuler** : masquer une application ou retirer un widget se rattrape depuis le message de
    confirmation.
  - **Serveur injoignable** : un bandeau le dit, la page garde ce qu'elle affiche, retente toutes les
    5 s et se remet à jour au retour.
  - **Bandeau du haut fondu** : il reste collé en haut, au-dessus des applications ; quand la page
    défile, ce qui passe dessous s'estompe dans le fond de la page, au lieu d'être coupé net. En haut
    de page, rien ne change.
  - **Un seul style, qui se règle** (Paramètres › Apparence) — les anciens « Lanceur » et
    « Bento » n'en diffèrent que par ces réglages, gardés dans un cookie et posés par le serveur avant
    le premier affichage :
    - **Applications** : en *cartes* (nom, état, description) ou en *icônes* seules, comme un écran
      d'accueil (cerclées si l'application ne répond pas ; un widget y prend 2×2, 4×2 ou 4×4 cases) ;
    - **Défilement** (grand écran) : *horizontal* (la mosaïque défile de côté, la page jamais vers
      le bas) ou *vertical* (la grille remplit la largeur, la page défile) ; le téléphone défile
      toujours vers le bas ;
    - **Widgets** : *mêlés* aux applications, ou *en haut*, dans leur propre grille ;
    - **Couleurs** : un nuancier pour la couleur d'accent (Lagon, Océan, Forêt, Prune, Corail,
      Ardoise, Framboise, Ambre, Indigo, Olive), en clair comme en sombre, et **Personnalisée** :
      n'importe quelle teinte, dont le panneau calcule la version claire et la sombre, toujours
      lisibles (texte blanc dessus en clair, 7:1 sur le fond sombre).
    Qui avait choisi le Lanceur retrouve icônes, défilement vertical et widgets en haut.
  - **Un hub vivant** : sous le bonjour (le nom en dégradé d'accent, traversé d'un reflet à
    l'arrivée), la journée en une ligne (« 4 sur 6 en ligne · 2 messages à lire ») ; les tuiles
    montent en cascade à l'ouverture ; les chiffres défilent jusqu'à leur valeur et repartent de
    l'ancienne quand elle change ; jauges, barres et courbes se dessinent à leur première
    apparition seulement (le rechargement toutes les dix secondes ne les rejoue pas) ; à la
    souris, une tuile s'incline à peine vers le pointeur et un reflet le suit. Rien ne bouge quand
    l'appareil demande moins d'animations.
  - **Icônes stables** : le hub se met à jour toutes les dix secondes sans faire clignoter les
    icônes. Une image déjà affichée est gardée telle quelle, même quand la tuile change.
  - **Le menu d'une application** : trois petits points dans le coin de la
    tuile à la souris (au survol de l'icône en mode icônes), un appui long au doigt (le menu
    s'ouvre alors en bas de l'écran), ou le clic droit. Il propose : Ouvrir, Voir la présentation
    (si le projet a une vidéo), Copier le lien, Réglages de l'application (admin) et Masquer du hub.
    Un simple clic ou un simple toucher sur la tuile ouvre toujours l'application.
  - La mosaïque :
    - **Sur un écran large** (défilement horizontal), elle défile de côté et jamais à la verticale : trois rangées à
      la hauteur de la fenêtre, et chaque application ajoute une colonne à droite. La molette fait
      défiler de côté. La recherche est à droite de l'en-tête. On n'ajoute pas d'application depuis
      le hub (c'est l'onglet *Applications*).
    - **Sur téléphone**, elle redevient une colonne qu'on fait défiler vers le bas.
    - **La mosaïque va d'un bord à l'autre** : au repos, la première tuile s'aligne sur le bonjour ;
      en défilant, les tuiles passent jusqu'au bord gauche de la fenêtre. La recherche est tout à
      droite, alignée sur le bouton du compte.
    - **Réorganiser le hub** (une icône discrète à droite de la recherche ; *Terminé* ou Échap pour
      sortir) : les tuiles tremblent légèrement ; on en attrape une (au doigt, après un appui de
      0,35 s : elle se soulève, le téléphone vibre ; un doigt qui glisse tout de suite fait défiler
      la page), elle suit le doigt ou la souris, en cartes comme en icônes,
      et les autres glissent pour lui faire place à mesure qu'elle passe ; près d'un bord, la
      mosaïque défile seule. Chaque tuile porte un « − » (retirer un widget, masquer une
      application) ; un widget a ses tailles (petit, moyen, grand) ; au clavier, des flèches
      apparaissent sur la tuile qui a le focus. La disposition (applications et widgets mêlés) est
      gardée côté serveur, d'un appareil à l'autre. Widgets en haut, chaque tuile reste dans sa
      grille.
    - **Galerie de widgets** (« + » en mode Modifier) : seulement des widgets utiles, propres au hub
      ou à une application du hub, chacun avec ses tailles en aperçu :
      - hub : « Dernière ouverte » (la dernière application ouverte, son icône et la date, sans
        aperçu de la page ; aux couleurs de son icône, et sa vidéo de présentation en boucle, sans le
        son, quand elle en a une ; sinon une lueur qui dérive dans sa couleur et l'icône qui respire), « État des applications » (admin : en ligne, arrêtées, et le compte de ce
        qui est en erreur ou à surveiller), « Messages » à lire (admin), « Sauvegardes » (admin : la
        dernière, sa taille, et l'alerte tant qu'aucune copie n'est sortie du serveur), « Serveur »
        (admin : processeur, mémoire et disque en jauges ; en grand, la courbe des dix dernières
        minutes, la valeur exacte au survol ; « élevé » au-delà de 80 %, « saturé » au-delà de 92 %),
        « Activité » (admin : les ouvertures des quatorze derniers jours en barres, le total, la
        tendance par rapport aux quatorze jours d'avant ; en grand, les applications les plus
        ouvertes), « Nouveautés » (les applications pas encore ouvertes, puis celles mises à jour ;
        dans le hub de départ), « Écrire à l'administrateur » et « Mes messages » (comptes
        utilisateurs). Le
        serveur ne propose et n'accepte que les widgets du rôle : un compte utilisateur ne peut pas
        poser un widget d'administration, même en appelant la route à la main ;
      - applications : « Aperçu » d'une application choisie, en direct, réduit et inerte ; un clic
        l'ouvre.
- **Indicateur « Nouvelle version »** sur la tuile d'une application qui a changé depuis la dernière
  fois que ce compte l'a ouverte : un déploiement ou un build réussi depuis le panneau, ou un
  nouveau commit dans son dépôt git. Une application jamais ouverte n'en porte pas.
- **Indicateur « Nouveau »** (comptes utilisateurs) sur la tuile d'une application à laquelle le
  compte a accès et qu'il n'a encore jamais ouverte depuis le hub ; le résumé du hub les compte
  (« 2 nouvelles »). L'ouvrir l'éteint.
- **Thème clair, sombre ou automatique.** Les jetons de couleur vivent dans
  `app-manager/app/theme.css`, aussi servi aux applications sur `/theme.css`.
- **Installable sur l'écran d'accueil** d'un téléphone ou d'une tablette (manifeste web et icônes fournis),
  et comme application sur ordinateur (Chrome, Edge, Dock du Mac).
  - **« Installer CodeLab »** (*Paramètres › Apparence*, dans le navigateur seulement) : un bouton là où
    le navigateur le propose (Chrome, Edge, Android), sinon le geste à faire (iPhone et iPad :
    Partager › « Sur l'écran d'accueil » ; Mac : Fichier › « Ajouter au Dock »). La carte disparaît
    une fois CodeLab installé.
  - **Icônes** 192 et 512 px, dont une découpable par Android (« maskable ») ; **raccourcis** par appui
    long sur l'icône : Hub, Messages, Paramètres.
  - **Pastille sur l'icône** : les messages à lire (administrateur), les nouvelles d'un message
    envoyé (compte utilisateur), là où l'appareil la connaît.
- **Notifications** (*Paramètres › Apparence*, un interrupteur par appareil), même CodeLab fermé :
  - l'administrateur : un nouveau message, une application qui tombe (et ne redémarre plus), puis son
    retour ;
  - un compte utilisateur : une nouvelle application à laquelle il a désormais accès (une fois par
    accès accordé), et l'avancement d'un message qu'il a envoyé (à l'étude, planifié, en cours,
    fait, non retenu) ;
  - un bouton « Envoyer une notification d'essai » ; toucher une notification ouvre la bonne page ;
  - les mails partent toujours : la notification s'y ajoute ;
  - chiffrées de bout en bout (standard Web Push) : Apple, Google ou Mozilla les transportent sans
    pouvoir les lire. Aucun compte chez eux ;
  - partout en HTTPS : Chrome, Edge, Firefox, Safari sur Mac, et sur iPhone et iPad le panneau
    **installé** sur l'écran d'accueil (iOS 16.4 et plus). Sinon, la carte dit pourquoi ;
  - attachées à l'appareil connecté : le déconnecter depuis *Sécurité* coupe aussi ses notifications.
- **Comportement d'application** une fois ouvert depuis l'icône : pas de zoom (ni pince, ni double
  tape, ni zoom à la saisie), pas de rebond en bout de page, pas de surlignage au toucher ni de
  sélection de texte sur l'interface (les champs et les messages restent copiables), et la barre d'état
  prend la couleur du bandeau, thème clair ou sombre, au lieu d'une bande blanche.
  - **Écran de démarrage** : fond de la couleur du bandeau et logo, au lieu d'un écran blanc ; une
    image par taille d'iPhone et d'iPad (l'iPad debout comme couché), clair et sombre, fabriquée par
    le panneau.
  - **Téléphone : affichage bloqué à la verticale**. Le manifeste demande le portrait au téléphone,
    et Android verrouille l'écran. Il compte à l'installation : après une mise à jour, il faut
    rajouter l'icône à l'écran d'accueil. iOS ne laisse pas une page web se verrouiller : sur
    iPhone, active le verrou d'orientation (Centre de contrôle). La tablette, l'ordinateur et le site
    web tournent librement, tout comme les applications du hub ouvertes dans CodeLab.
  - **Téléphone couché** : si l'iPhone tourne quand même, le panneau reste lisible. Le hub défile vers
    le bas, le bandeau se compacte (56 px), et en mode application le contenu s'écarte de l'encoche.
  - **Barre d'état et barre de geste des applications du hub** : elles prennent la couleur réellement
    affichée en haut et en bas de l'application (comme Safari), et la suivent au défilement : plus de
    bandeau d'une autre couleur au-dessus de la page.
  - **Descriptions des applications de CodeLab** (Dagster, Mailpit, Messages, Diagnostic) : courtes,
    lisibles en entier sur leur tuile.
  - **Tablette** : en portrait, le hub défile vers le bas comme un écran d'accueil ; en paysage, il
    suit le réglage « Défilement ».
  - **Aucune barre de défilement visible**, sur ordinateur, tablette et téléphone, iPhone compris
    (au doigt, c'est un bloc intérieur qui défile : iOS ne laisse pas masquer celle de la page).
  - **Hors ligne** : l'application s'ouvre quand même, sur « Serveur injoignable », retente toutes les
    5 s et revient d'elle-même. Rien n'est mis en cache que cette page (ni pages du panneau, ni
    données). Il faut une adresse en `https` (ou `localhost`) : c'est une exigence des navigateurs.
  - **Applications ouvertes depuis le hub** : même comportement (pas de zoom, pas de rebond), la barre
    d'état prend la couleur de l'application, et un glissement depuis le bord gauche ramène au hub.
    Le hub **simule iOS** pour elles : elles se croient installées (`navigator.standalone`,
    `display-mode: standalone`) ; comme sur iOS, une page en `viewport-fit=cover` s'étend sous
    l'encoche et reçoit les bords de l'écran (ses `env(safe-area-inset-*)` sont réécrits), les autres
    sont posées dans la zone sûre avec leur couleur autour.
  - **Chaque application est une vraie application** : elle s'installe seule sur l'écran d'accueil
    (manifeste, icône, nom, fournis par le panneau), y compris sur son sous-domaine.
  - **L'environnement, posé sur chaque page d'application** : `html.mode-app` ou `mode-site`,
    `data-appareil` (téléphone, tablette, ordinateur), `data-plateforme`, les bords de l'écran en
    variables CSS (`--codelab-bord-haut`…), et `window.CODELAB.environnement` pour le script. Les mêmes
    clés partout : ouverte seule, installée seule ou depuis le hub.
  - **Dans le navigateur**, CodeLab reste un site, adapté au téléphone : zoom permis, applications
    ouvertes directement, pas de page de réserve -- sauf une fois les notifications activées, que
    porte le même service worker.
  - Après la mise à jour, supprimer l'icône puis la rajouter : iOS relit ces réglages à l'ajout.
- **Ce qu'un compte utilisateur ne voit jamais** : l'état des applications (ni pastille, ni cadre
  de couleur, ni compteur ; le serveur ne l'envoie pas) et le détail des erreurs techniques (une
  erreur serveur lui dit seulement de réessayer, avec une référence à donner à l'administrateur).
- **Une application qui ne s'ouvre pas** (arrêtée, muette sur son port, ou dont la page plante) :
  - un compte utilisateur, ou un visiteur d'une application publique, arrive sur « *Nom* est
    momentanément inaccessible », avec « Retour au hub » et « Réessayer », sans un mot sur la cause ;
    l'administrateur est prévenu par mail (une fois par heure et par application, si les alertes sont
    réglées), et la page le dit seulement dans ce cas ;
  - l'administrateur voit la cause (arrêtée, plantée au lancement, ne répond pas encore) et
    « Ouvrir sa fiche ».
- **Connexion refusée** : à partir de trois essais restants, le message dit combien il en reste avant
  la pause de 5 minutes.
- **Chargement** : des lignes grises à la forme du contenu à la place de « Chargement… ».
- **Police auto-hébergée** (Manrope) : aucune requête vers un service tiers.
- **Erreurs JavaScript** de la page envoyées dans les journaux du panneau.

**Onglets des paramètres** : un par catégorie, les mêmes pour tous les comptes (l'administrateur a
en plus *E-mail et alertes*). Sur ordinateur, une colonne à gauche ; au téléphone, une pastille
segmentée de cinq onglets de même largeur. Au doigt, une carte touchée ne reste pas grisée comme
sélectionnée.

| Onglet | Pour qui | Contenu |
|---|---|---|
| Compte | tous | adresse mail (vérifiée par code), session ; version de CodeLab pour l'admin |
| Apparence | tous | thème, réglages du hub, notifications de cet appareil, « Installer CodeLab » |
| Mon hub | tous | applications affichées dans *son* hub ; catégories pour l'admin |
| Sécurité | tous | mot de passe, second facteur (TOTP), clés d'accès, appareils connectés ; sauvegardes et bilan de sécurité pour l'admin |
| E-mail et alertes | admin | serveur d'envoi, alertes par application, surveillance externe |

## 2. Applications

Détails : [`app-manager/README.md`](app-manager/README.md) et, côté développeur,
[`dev/AGENTS.md`](dev/AGENTS.md).

**Déclarer**

- **Ajouter un projet** à partir d'un dossier de `/workspace`. Le panneau ne crée jamais de projet.
- **Détection automatique** des commandes de build et de lancement (Vite, Astro, Parcel, CRA, Angular,
  Next.js, Django, Flask, script `start`, page statique).
- **Nom libre, adresse dérivée** : `Mon Site` est servi sous `/mon-site/`. Un nom réservé par une
  route du panneau est refusé.
- **Renommer** une application : son adresse, ses droits et ses fichiers suivent.
- **Archiver** une application (fiche, onglet *Configuration*) : elle s'arrête, quitte le hub et son
  adresse (qui répond comme une adresse inconnue), et prend le statut **« Archivée »**, au même rang
  que « En ligne » ou « Arrêtée » : la liste des applications la garde, et le filtre « Archivées »
  la retrouve. Sa fiche dit quand elle a été archivée ; « Désarchiver » la ramène telle quelle,
  arrêtée.
  - Au bout de 30 jours, **seule sa configuration est effacée** : commandes, port, visibilité,
    mémoire, droits des comptes, alertes. L'application reste, archivée, avec son nom, son dossier et
    sa description. Désarchivée ensuite, elle revient privée, avec un port et des commandes
    redétectées, à vérifier avant de la démarrer.
  - Le dossier du projet dans `/workspace` n'est jamais touché.
- **Description**, affichée sous le nom dans la tuile du hub, sur deux lignes : environ 45 caractères
  s'y voient, le reste au survol (140 au plus). La fiche le dit sous le champ, avec un compteur. Le
  nom passe toujours avant la description : un nom long prend un corps plus petit plutôt que d'être
  coupé.
- **Logo** déposé depuis la fiche (PNG, JPEG ou GIF, 512 Ko au plus), ou détecté dans le dossier
  (`icon.png`, `favicon.png`, `logo.png`…). À défaut, un avatar est généré.
- **Catégorie** pour ranger le hub. La catégorie **CodeLab**, posée d'office et non modifiable,
  regroupe les applications de la stack réservées à l'administrateur (Dagster, Mailpit, Messages,
  Diagnostic) ; elle ne se renomme pas, ne se supprime pas et ne se donne pas à un projet.
- **Vidéo de présentation** : si le projet a une `video/presentation.mp4` (faite par `codelab video`,
  voir § 6), « Voir la présentation » dans le menu de sa tuile la lit dans une fenêtre, avec « Ouvrir
  l'application » en dessous. Servie avec les droits de l'application : un compte qui ne la voit pas
  ne voit pas sa vidéo. La fiche la montre (carte « Présentation ») et l'admin peut la masquer du hub.
  L'admin peut aussi **choisir le fichier** dans la fiche (« Fichier vidéo ») : toute vidéo du dossier
  du projet (`.mp4`, `.webm`, `.m4v`, `.mov` ; ni `node_modules`, ni dossiers cachés, ni liens qui
  sortent du projet), par exemple un rendu Remotion dans `out/`. Vide : `video/presentation.mp4`.

**Faire tourner**

- **Démarrer, arrêter, redémarrer.**
- **Build** à la demande.
- **Port interne attribué** automatiquement (`9101`–`9140`), passé à l'application dans `$PORT`. Les
  commandes proposées écoutent sur `127.0.0.1` ; une application qui écoute sur toutes les
  interfaces (`0.0.0.0`) le voit signalé dans sa fiche, parce que les autres conteneurs la
  joindraient sans connexion.
- **Reprise au redémarrage** du panneau : une application active est relancée.
- **Redémarrage automatique** après un crash, 5 fois au plus en 10 minutes. Au-delà, la pastille
  passe en « boucle de crash ».
- **Sonde d'écoute** toutes les 10 s : « Ne répond pas » si rien n'écoute sur le port.
- **Limite mémoire** optionnelle par application.
- **Isolation** : un uid par application, une vue de `/workspace` réduite à son propre projet, un
  `/tmp` à elle.
- **Variables reçues** : Postgres, Redis, Mailpit, identité SSO, `PORT`, `CODELAB_APP`.

**Observer**

- **Liste des applications** : une recherche (nom, description, catégorie, port), des filtres par
  état (en ligne, arrêtées, à surveiller), visibilité et catégorie, un tri par nom, état,
  visibilité, port, CPU ou mémoire. Deux affichages, retenus par navigateur : des cartes (état,
  jauges CPU et mémoire, démarrer/arrêter) ou une liste dont chaque colonne se trie d'un clic.
- **Fiche d'une application**, en six onglets :

  | Onglet | Contenu |
  |---|---|
  | Général | état, port, dossier, commandes, ressources du moment, vidéo de présentation |
  | Métriques | CPU et mémoire sur les deux dernières minutes |
  | Journal | flux en direct, avec filtre |
  | Activité | ouvertures, comptes qui s'en servent, dernières ouvertures |
  | Accès | autoriser ou retirer chaque compte d'une case (recherche au-delà de six comptes) |
  | Configuration | description, commandes, limite mémoire, visibilité, logo, suppression |

- **Journaux** : rotation à 2 Mo, flux en direct plafonné pour ne pas bloquer le panneau.
- **La cause d'une panne, en clair** : une application qui plante au lancement, redémarre en boucle,
  s'arrête seule ou ne répond pas sur son port montre en tête de sa fiche les dernières lignes de son
  journal (les lignes d'erreur en rouge) et quoi faire.
- **En-tête de fiche collé en haut** sur grand écran : Démarrer, Arrêter et Redémarrer restent à
  portée pendant qu'on fait défiler.

**Partager**

- **Visibilité** : privée (comptes autorisés) ou publique (lien partageable, possible seulement si une
  adresse publique est déclarée).
- **Se connecter avec CodeLab** (SSO) : l'application reconnaît la personne sans formulaire, grâce
  aux modules `codelab_auth.py` et `codelab-auth.js`.

**Le fond** : uni, dans les couleurs du thème, semé de petites étoiles à intervalle régulier,
discrètes en clair comme en sombre (panneau, connexion, messagerie).

**Comme une vraie application**

- **Le bouton « ← CodeLab »**, posé sur chaque page, est replié par défaut : une flèche au bord
  gauche de l'écran, en bas (30 × 46 px). Un appui le déplie ; il n'a pas de bouton pour se
  replier : faute d'appui, il se replie seul au bout de 10 secondes. L'application garde sa place.
- **Ouverte depuis le hub installé**, l'application suit **son manifeste**, comme le ferait le
  système : son fond et sa barre peignent l'écran avant sa première image, et son orientation
  (paysage, libre) tourne l'écran sur Android. Sur iPhone, l'écran suit le verrou du Centre de
  contrôle : iOS ne laisse aucune page web le tourner. Sans manifeste à elle, celui que le panneau
  lui fournit sert.

**Applications par défaut**

- **Dagster**, **Diagnostic**, **Mailpit** et **Messages** ont leur tuile, réservée à l'administrateur.
- **Elles ne s'accordent pas** : dans la fiche d'un compte, leur case est grisée (« Réservée à
  l'administrateur ») ; le serveur ne garde pas ce droit, ni depuis la fiche, ni dans une invitation,
  et ignore celui qu'une version plus ancienne aurait enregistré.
- Elles ne se configurent pas, ne s'arrêtent pas et ne se suppriment pas : le panneau les remet en
  route à chaque tour.
- Chaque compte peut les ranger hors de son hub.

## 3. Comptes et accès

Détails : [`SECURITE.md`](SECURITE.md) et [`app-manager/README.md`](app-manager/README.md).

- **Deux rôles** : administrateur et utilisateur.
- **Appareils connectés** (*Paramètres > Sécurité*, chaque compte) : où le compte est ouvert (« Safari
  sur iPhone », adresse, dernière activité, « Cet appareil »), un bouton pour déconnecter chacun, et
  « Déconnecter les autres appareils ». Téléphone perdu : on le coupe sans changer de mot de passe.
  Changer de mot de passe ferme aussi les autres appareils.
- **Profil** (*Paramètres > Compte*, comptes utilisateurs) : prénom et nom (facultatif), côte à
  côte sur ordinateur. Le compte d'administration garde son nom et n'a pas cette carte. Le prénom
  remplace l'identifiant dans le bonjour du hub, le nom complet dans l'en-tête et dans la liste des
  comptes de l'administrateur ; on se connecte toujours avec son identifiant.
- **Session** (*Paramètres > Compte*, carte toujours dépliée) : « Déconnexion », et pour un compte
  utilisateur « Supprimer mon compte ». La suppression demande le mot de passe, ferme le compte tout
  de suite sur tous ses appareils et prévient par mail (adresse vérifiée). Les données sont effacées
  30 jours plus tard : fiche, adresse, clés d'accès, appareils, notifications, préférences, messages
  et pièces jointes, lignes du journal. D'ici là, se reconnecter ne montre qu'une page, avec la date,
  « Annuler la suppression » et « Me déconnecter ». L'administrateur voit « Suppression le … » dans
  la liste des comptes. Le compte d'administration ne se supprime pas.
- **Invitation par mail.** La personne choisit elle-même son nom et son mot de passe. Le lien est
  aussi affiché à l'administrateur ; il peut être renvoyé ou annulé.
- **Création directe** d'un compte par l'administrateur.
- **Fiche d'un compte** :
  - adresse mail et mot de passe ;
  - applications autorisées ;
  - suspension ;
  - remise à zéro du second facteur, retrait des clés d'accès ;
  - ses appareils connectés, et « Déconnecter tous ses appareils » (sans toucher au mot de passe) ;
  - suppression, qui emporte les clés d'accès.
- **Deux filtres sur la liste** : par personne, ou par application (« qui a accès à… »).
- **Connexion par Face ID / Touch ID** (clé d'accès) : un bouton en tête de la page de connexion,
  qui vaut mot de passe et second facteur ; aucun code à saisir. Il demande le panneau en HTTPS, par
  son nom de domaine : sinon la page dit pourquoi il n'est pas proposé.
- **Second facteur obligatoire**, deux méthodes indépendantes, sans HTTPS :
  - un **code par mail** (comptes avec adresse vérifiée, et l'administrateur à son adresse
    d'alerte), saisi dans six cases : un code collé ou proposé par le clavier les remplit d'un coup,
    le sixième chiffre valide, « Renvoyer le code » attend une minute ; le mail le montre en grand ;
  - le code de l'**application d'authentification** (TOTP). Chaque compte l'ajoute ou la retire
    lui-même dans *Paramètres > Sécurité* (retrait par un code valide, jamais s'il est le seul
    second facteur) ;
  - en HTTPS, **Face ID / Touch ID** en plus, si le compte en a une clé.

  La méthode proposée est **la dernière utilisée** par le compte (le mail sans historique) ; ce
  n'est pas un réglage. « Essayer une autre méthode » déplie les autres : choisir l'application
  n'envoie aucun mail. **Seul le code de la méthode affichée est accepté** : celui du mail ne passe
  pas sur l'écran de l'application, et inversement.
- **Remplissage automatique sur iPhone** : la page de connexion est un vrai formulaire. Safari
  propose d'enregistrer le mot de passe, le remplit, et suggère le code reçu par mail ou le code de
  validation gardé dans Mots de passe.
- **Mot de passe oublié** par code mail, si le compte a aussi un autre facteur.
- **Journal des accès** paginé (25 ou 50 lignes), filtrable, avec une **carte de chaleur** sur un an
  (*Ouvertures d'applications*). Survoler une case, ou l'appuyer au doigt, ouvre son détail en
  chiffres : le jour en toutes lettres, le total, puis les ouvertures **par utilisateur** et **par
  application**, de la plus grande à la plus petite (cinq lignes, puis « N autres »). Les visiteurs
  sans compte apparaissent comme « Visiteur anonyme ».
- **Durée de conservation du journal** (*Paramètres › Sécurité*) : 3, 6, 12 (par défaut) ou 24 mois,
  ou sans limite. Ce qui dépasse s'efface chaque jour, ici et dans Postgres ; raccourcir la durée
  demande confirmation et efface tout de suite.
- **Copie de l'historique dans Postgres** (base `codelab`) pour les requêtes longues.

## 4. Mails et alertes

Détails : [`mailpit/README.md`](mailpit/README.md).

**Mailpit**

- **Tous les mails passent par Mailpit** et s'y relisent : ceux des applications, ceux du panneau
  (alertes, messages à l'administrateur) et les alertes d'échec Dagster.
- **Sauf les mails de sécurité**, envoyés en direct par le serveur d'envoi et jamais gardés dans
  Mailpit : codes de vérification, de connexion et de réinitialisation du mot de passe, liens
  d'invitation, alerte de connexion administrateur depuis une adresse inconnue. Sans serveur d'envoi
  réglé, ils ne partent pas.
- **Historique** : les 500 derniers mails, un mois au plus, relisibles dans une interface au style de
  CodeLab (`:8025`, administrateur).
- **Relais** par le serveur d'envoi de *Paramètres > E-mail* (un compte Gmail suffit).
- **Les applications envoient par le hub par défaut**, sans identifiants : `MAIL_HOST`, `MAIL_PORT`, et
  les mêmes sous les noms usuels (`SMTP_HOST`, `EMAIL_HOST`…). Un serveur propre au projet se règle
  dans son `.env`, qui l'emporte.
- **Plafond d'envoi** : au-delà de 50 mails par heure ou 200 par jour, les mails sont gardés sans être
  envoyés.
- **Mode test** : tout est gardé, rien ne part.
- **Deux configurations d'envoi** :
  - celle d'origine, dans `credentials.env` ;
  - une personnalisée, saisie dans le panneau. Elle est essayée avant d'être enregistrée, et en cas
    d'échec, les mails repartent par celle d'origine.

**Alertes**

- Une application qui ne redémarre plus, puis son retour. Le mail contient les 25 dernières lignes du
  journal.
- Des destinataires propres à chaque application ; l'adresse d'alerte de l'administrateur s'ajoute
  toujours.
- Une connexion administrateur depuis une adresse IP inconnue.
- Chaque run Dagster en échec (capteur `alerte_mail_echec`), dont l'audit des dépendances.
- Surveillance externe : un appel à healthchecks.io toutes les 5 minutes.

## 5. Messages : boîte de réception, projets, planning, feuille de route

Une application du hub, au même rang que Dagster, Mailpit et le diagnostic, réservée à
l'administrateur. Sa tuile (deux bulles de conversation) porte le nombre de messages non lus. Servie
par le panneau sur `/messages` : même session, mêmes contrôles d'accès, même base.

- **Écrire un message** (tout compte) : dans sa rubrique **Messages** (`/messages`, une entrée du menu, de la
  barre du bas au téléphone ; ce n'est plus un onglet des paramètres), ou le widget « Écrire à
  l'administrateur », en choisissant le sujet : **améliorer le hub**, **une idée de
  nouveau projet**, ou **améliorer une application existante** (laquelle). Un message qui arrive
  s'annonce dans le coin du panneau de l'administrateur.
- **Joindre des captures et des maquettes** (jusqu'à 6 par message, 10 Mo chacune) : images (PNG,
  JPEG, WebP, GIF) ou PDF, reconnus à leur contenu (pas de SVG). Par le sélecteur de fichiers du
  système (Photos et l'appareil photo sur iPhone), en collant une capture dans le message, ou en la
  glissant dessus. Un message peut n'être qu'une maquette. Les pièces s'ouvrent dans le navigateur ;
  seuls l'administrateur et l'auteur y ont accès, et elles partent avec le message.
- **Exporter un projet** (*Exporter*, en tête d'un projet) : un ZIP avec `projet.md` (description,
  tâches dans l'ordre du planning avec sous-tâches et notes, messages, maquettes en images),
  `projet.json`, et les pièces jointes dans `pieces/`. *Tout exporter* (bas du menu) rassemble tous
  les projets, un dossier chacun, avec un sommaire.
- **Suivre ses messages** (comptes utilisateurs) : *Messages > Mes messages* montre où en
  est chacun, sur une frise **Envoyé → Lu (ou À l'étude) → Planifié → En cours → Réalisé**, ou
  **Non retenu**. L'étape se déduit de ce que fait l'administrateur dans Messages (lire, mettre à
  l'étude, accepter, refuser, en faire des tâches ou un projet, les cocher) : rien de plus à remplir.
  - On y voit le projet, les tâches nées du message (titre et coche seulement), la date prévue s'il
    y en a une, et la date de réalisation.
  - Un message qui a avancé depuis la dernière visite est mis en avant, et le menu *Messages* porte
    leur nombre ; ouvrir la liste les marque vus.
  - Le widget Bento « Mes messages » résume le tout sur le hub (dans la composition par défaut).
  - Un compte ne voit que ses propres messages.
- **Boîte de réception** (`/messages`) : à classer, tous, archivés ; filtre par sujet et recherche.
  - Les messages se rangent à la main (glisser par la poignée, ou flèches ↑ ↓ au clavier) ; un message
    neuf arrive en tête.
  - Chacun se range dans un projet (existant ou créé à la volée), devient une tâche, s'archive ou se
    supprime (avec confirmation).
  - **Regrouper** : plusieurs messages choisis forment un projet, neuf ou existant. Le panneau
    **suggère** les messages qui semblent parler de la même chose (mots communs) ; une suggestion
    s'accepte ou s'ignore.
- **Planning** (`/messages/planning`) : toutes les tâches ouvertes, dans l'ordre où on veut les
  faire. **La priorité est la place dans la liste**, pas une étiquette d'urgence.
  - Glisser (ou ↑ ↓) pour réordonner ; filtre par projet ; ajout rapide d'une tâche.
  - Une tâche a une **date prévue facultative** et une **durée** en jours ouvrés (1 par défaut).
  - Une fiche par tâche : titre, projet, date, durée, notes, sous-tâches, message d'origine.
  - **Scinder** une tâche en plusieurs (une ligne par tâche, sous-tâches reprises au besoin) : les
    nouvelles la suivent dans le planning, la durée se partage.
  - Cocher une tâche la marque faite et date sa fin ; les tâches faites se replient en bas.
- **Feuille de route** (`/messages/feuille`) : le planning posé sur un calendrier de jours ouvrés.
  - *Aujourd'hui* : en retard, aujourd'hui, ensuite, fait aujourd'hui.
  - *Semaines* : huit semaines, du lundi au vendredi, chaque tâche sur ses jours.
  - *Mois* : une frise sur 1, 3, 6 ou 12 mois, une ligne par projet.
  - Une tâche datée se place à sa date ; les tâches sans date se suivent dans l'ordre du planning à
    partir d'aujourd'hui. **Tant qu'une tâche n'est pas cochée, elle glisse avec le temps** : une
    date dépassée la ramène à aujourd'hui, marquée « en retard ».
- **Projets** (`/messages/projet/<id>`, listés dans la barre latérale) : leurs tâches (dans l'ordre du
  planning, réordonnables), leurs messages, leur avancement, leur étape (*idée*, *en construction*,
  *réalisé*) et l'application liée. Supprimer un projet ne perd rien : ses tâches passent dans
  « Général », ses messages redeviennent à classer.

## 6. Poste de développement

`ssh vscode@<serveur> -p 2222` — détails : [`dev/README.md`](dev/README.md).

**Accès**

- **SSH par clé uniquement.** Un fichier de clé publique par machine dans `config/ssh/authorized_keys.d/`.
- **Empreinte stable** : les clés hôte survivent aux mises à jour d'image.
- **VS Code Remote-SSH** : le serveur VS Code et ses extensions sont conservés. D'autres
  extensions s'ajoutent dans le compose.
- **Les bases dans VS Code, sans rien créer** : SQLTools est installé d'office, et chaque base du
  serveur y apparaît déjà (groupe « CodeLab »), chacune avec son propre compte, pour n'importe quel
  projet ouvert ; une base créée par `codelab db` s'y ajoute aussitôt.
- **Une base, un compte** : le poste n'a pas le compte administrateur de Postgres. Dans le dossier
  d'un projet, le terminal (et donc un agent de code) a le compte de sa base, et lui seul ; les
  autres bases et celles de la stack le refusent. Database Client (Postgres, Redis) reste installé, avec des
  connexions à créer à la main.

**Prêt à l'emploi**

- **Outils installés** : Python 3.13, Node et npm, git, `psql`, `codex`, `dagster` ; Chromium, Piper
  (voix française) et ffmpeg pour les vidéos.
- **Connexions déjà configurées** : `psql`, `psycopg.connect()` et `redis-cli -h codelab-redis`
  marchent sans argument.
- **Git** : la configuration globale survit aux mises à jour. Les clés restent sur ton poste (`ssh -A`).

**Outil `codelab`**

| Commande | Effet |
|---|---|
| `codelab new <nom> [--db] [--ouvrir]` | crée le projet : dossier, git, `.gitignore`, `README.md`, `AGENTS.md`, `CLAUDE.md` et les modules `codelab_auth` et `codelab_web` |
| `codelab db <nom>` | crée la base du projet, son compte et son schéma `dagster` |
| `codelab agents [dossier]` | écrit ou met à jour le manuel CodeLab dans `AGENTS.md`, et pose un `CLAUDE.md` qui l'importe |
| `codelab video [projet]` | la vidéo de présentation : pose un scénario (`video/scenario.mjs`), puis le joue dans Chromium, ajoute une voix off française et monte `video/presentation.mp4` |

**Raccourcis et agents**

- **Tâches VS Code** : Ctrl+Maj+B crée un projet et rouvre la fenêtre dessus. Les autres commandes
  sont dans *Terminal > Exécuter la tâche*.
- **Agents de code** : Codex (compte ChatGPT) et Claude Code lisent le même manuel, `AGENTS.md`.
- **Vidéos sans IA côté CodeLab** : le scénario est du code du projet, qu'un agent de code peut
  écrire ; le tournage, la voix (Piper) et le montage (ffmpeg) tournent sur le poste, hors ligne.

## 7. Projets et Dagster

Détails : [`workspace/README.md`](workspace/README.md) et [`dagster/README.md`](dagster/README.md).

- **Un projet est un dossier** de `/workspace`. Il n'y a aucun registre central.
- **Une base Postgres par projet**, nommée comme le dossier, avec un schéma `dagster`. **pgvector** y
  est installé d'office.
- **Redis partagé** : cache, sessions, files de tâches. Préfixer ses clés par le nom de l'application.
- **Configuration en couches** : l'environnement du conteneur, puis `credentials.env`, puis le `.env`
  du projet, qui l'emporte.
- **Dagster** découvre seul tout `definitions.py` qui expose `defs`. Un projet qui ne se charge pas
  est ignoré sans gêner les autres.
- **Dépendances isolées par projet**, sur demande : un fichier `requirements-dagster.txt` donne au
  projet son propre environnement Python.
- **Interface Dagster** sur `:3000`, avec la session d'administration du panneau.
- **Audit hebdomadaire des dépendances** : les versions figées de chaque projet sont confrontées à la
  base OSV.

## 8. Données, sauvegardes, restauration

Détails : [`postgres/README.md`](postgres/README.md).

- **Une base, un compte.** Chaque base de projet a son propre compte Postgres, seul (avec
  l'administrateur) à pouvoir s'y connecter ; une application ne reçoit que le compte de sa base,
  et le panneau la crée au lancement si elle manque. Les bases de la stack (`dagster`, `codelab`)
  sont réservées à l'administrateur. Limite : Dagster garde le compte administrateur.

| Sauvegarde | Fréquence | Gardées | Écrite par |
|---|---|---|---|
| Chaque base Postgres (`base-<nom>-<date>.dump`) | quotidienne | 7 | `codelab-postgres` |
| État du panneau (`panneau-<date>.tar.gz`) | quotidienne | 7 | `codelab-app-manager` |
| Code de `/workspace` (`workspace-<date>.tar.gz`) | hebdomadaire | 4 | `codelab-app-manager` |

- **Chaque archive est relue** avant d'être gardée.
- **Restauration d'essai** quotidienne de chaque base dans une base jetable.
- **Procédures de restauration** écrites, commandes comprises.
- **Toutes les données vivent hors des conteneurs**, dans `/DATA/AppData/codelab/` : elles survivent
  aux mises à jour.
- **Sauvegardes dans le panneau** (*Paramètres > Sécurité*, administrateur) : la date de la dernière,
  le téléchargement de la plus récente du panneau et des projets, et une **alerte tant qu'aucune copie
  n'est sortie du serveur depuis 7 jours** (un disque qui lâche emporterait tout). Télécharger une
  archive l'éteint, comme la case « Je copie déjà ce dossier ailleurs ».

## 9. Diagnostic

Détails : [`workspace/diagnostic/README.md`](workspace/diagnostic/README.md).

- **Page d'état des lieux** (`:9002/diagnostic/`), en lecture seule :
  - les services se parlent-ils ?
  - ce qui doit être fermé l'est-il ?
  - sauvegardes, disque, applications, exposition.
- **Trois rangs** : échec, alerte, sans objet. Seul l'échec fait tomber le run Dagster, donc partir un
  mail.
- **Vérification approfondie** (`/tests`) : écrit puis relit en base, traverse le proxy, lance la
  suite de régression.
- **Même sondes côté Dagster**, toutes les heures.
- **Projet modèle** à copier pour démarrer un projet.

## 10. Exposition sur Internet

Détails : [`app-manager/vps/README.md`](app-manager/vps/README.md).

- **VPS + WireGuard** : le VPS porte le domaine et le certificat, ton serveur compose le tunnel.
- **Une application, un sous-domaine** : ouvert par `toi.duckdns.org`, le panneau sert chaque
  application à `https://<son-nom>.toi.duckdns.org` — le nom de sa fiche. Rien à déclarer : ni
  variable, ni champ. En local, rien ne change (le port des applications). Il faut seulement que le
  port `9002` soit ouvert : sans lui, les applications restent sous `https://toi.duckdns.org/<nom>/`.
- **L'adresse publique s'apprend** : c'est le domaine par lequel le panneau a été ouvert depuis
  internet. Elle sert aux liens d'invitation et permet de rendre une application publique.

## 11. Installation et mise à jour

Détails : [`README.md`](README.md).

- **Un seul fichier compose.** Une variante pour les interfaces de type CasaOS existe :
  `docker-compose-casaos.yml`.
- **Secrets générés au premier démarrage** dans `credentials.env`, un bloc par service.
- **Images publiées sur GHCR** : `latest` à chaque fusion, version `X.Y.Z` à chaque tag `vX.Y.Z`,
  reconstruction hebdomadaire.
- **Version installée** (*Paramètres > Compte*, administrateur) : le commit de l'image et sa date, un
  lien vers les nouveautés depuis cette version sur GitHub, et les commandes de mise à jour à copier
  d'un clic. **Le panneau ne contacte jamais GitHub** de lui-même (aucun appel sortant) : le lien
  s'ouvre dans le navigateur, si on le choisit. Il ne se met pas non plus à jour tout seul.
- **Squelette de `/workspace`** déposé au premier démarrage. Ses fichiers non modifiés sont mis à jour
  automatiquement.

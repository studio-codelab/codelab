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
| Hub | `/` | tous | les applications ouvrables, et (style Bento) une mosaïque à composer, avec ses widgets ; laisser un message à l'administrateur |
| Vue d'ensemble | `/apercu` | admin | compteurs (en ligne, arrêtées, en erreur), charge CPU et mémoire |
| Applications | `/applications`, `/applications/<nom>[/<onglet>]` | admin | liste, puis fiche d'une application |
| Utilisateurs | `/utilisateurs` | admin | comptes, invitations, journal des accès, carte d'activité |
| Messages | `/messages`, `/messages/planning`, `/messages/feuille`, `/messages/projet/<id>` | admin | application à part, ouverte depuis sa tuile du hub (voir § 5) ; `/demandes` y redirige |
| Paramètres | `/parametres/<onglet>` | tous | onglets selon le rôle (voir plus bas) |

- **Une seule application pour tous les rôles.** Le rôle décide de ce que le menu propose. Chaque
  rubrique a son adresse : recharger y ramène, « Précédent » y revient.
- **Deux styles** (*Paramètres > Compte > Apparence*), retenus par navigateur. Le reste du panneau
  est le même : menu en pilule, barre du bas sur téléphone, réglages en fenêtre.
  - « Lanceur » : le hub comme un écran d'accueil (trame de points, bonjour en grand, recherche au
    centre, grandes icônes cerclées si l'application ne répond pas). Dagster, Mailpit et Messages y
    ont la même tuile que les autres applications ;
  - « Bento » (par défaut) : le hub en mosaïque.
    - **Sur un écran large**, elle défile à l'horizontale et jamais à la verticale : trois rangées à
      la hauteur de la fenêtre, et chaque application ajoute une colonne à droite. La molette fait
      défiler de côté. La recherche est à droite de l'en-tête. On n'ajoute pas d'application depuis
      le hub (c'est l'onglet *Applications*).
    - **Sur téléphone**, elle redevient une colonne qu'on fait défiler vers le bas.
    - **La mosaïque va d'un bord à l'autre** : au repos, la première tuile s'aligne sur le bonjour ;
      en défilant, les tuiles passent jusqu'au bord gauche de la fenêtre. La recherche est tout à
      droite, alignée sur le bouton du compte.
    - **Réorganiser le hub** (une icône discrète à droite de la recherche ; *Terminé* ou Échap pour
      sortir) : les tuiles tremblent légèrement ; on en attrape une, elle suit le doigt ou la souris,
      et les autres glissent pour lui faire place à mesure qu'elle passe ; près d'un bord, la
      mosaïque défile seule. Chaque tuile porte un « − » (retirer un widget, masquer une
      application) ; un widget a ses tailles (petit, moyen, grand) ; au clavier, des flèches
      apparaissent sur la tuile qui a le focus. La disposition (applications et widgets mêlés) est
      gardée côté serveur, d'un appareil à l'autre.
    - **Galerie de widgets** (« + » en mode Modifier) : seulement des widgets utiles, propres au hub
      ou à une application du hub, chacun avec ses tailles en aperçu :
      - hub : « Dernière ouverte » (aperçu en direct de la dernière application ouverte, avec la
        date), « État des applications » (admin), « Messages » à lire (admin), « Nouvelles
        versions », « Écrire à l'administrateur » et « Mes messages » (comptes utilisateurs) ;
      - applications : « Aperçu » d'une application choisie, en direct, réduit et inerte ; un clic
        l'ouvre.
- **Indicateur « Nouvelle version »** sur la tuile d'une application qui a changé depuis la dernière
  fois que ce compte l'a ouverte : un déploiement ou un build réussi depuis le panneau, ou un
  nouveau commit dans son dépôt git. Une application jamais ouverte n'en porte pas.
- **Thème clair, sombre ou automatique.** Les jetons de couleur vivent dans
  `app-manager/app/theme.css`, aussi servi aux applications sur `/theme.css`.
- **Installable sur l'écran d'accueil** d'un téléphone (manifeste web et icônes fournis).
- **Police auto-hébergée** (Manrope) : aucune requête vers un service tiers.
- **Erreurs JavaScript** de la page envoyées dans les journaux du panneau.

**Onglets des paramètres**

| Onglet | Pour qui | Contenu |
|---|---|---|
| Compte | tous | apparence, style, adresse mail (vérifiée par code), mot de passe, taille de pages du journal, applications affichées dans *son* hub |
| Sécurité | tous | second facteur (TOTP), clés d'accès ; bilan de sécurité pour l'admin |
| Serveur | admin | adresse publique, liaison avec un VPS (fichiers prêts à copier), constat sur la requête en cours |
| Catégories | admin | les tiroirs du hub, dans l'ordre voulu |
| E-mail | admin | serveur d'envoi, adresse d'alerte de l'administrateur |
| Alertes | admin | interrupteur, destinataires par application, surveillance externe |

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
- **Description** d'une ligne, affichée sous la tuile du hub.
- **Logo** déposé depuis la fiche (PNG, JPEG ou GIF, 512 Ko au plus), ou détecté dans le dossier
  (`icon.png`, `favicon.png`, `logo.png`…). À défaut, un avatar est généré.
- **Catégorie** pour ranger le hub.
- **Vidéo de présentation** : si le projet a une `video/presentation.mp4` (faite par `codelab video`,
  voir § 6), sa tuile du hub porte un bouton ▶ qui la lit dans une fenêtre, avec « Ouvrir
  l'application » en dessous. Servie avec les droits de l'application : un compte qui ne la voit pas
  ne voit pas sa vidéo. La fiche la montre (carte « Présentation ») et l'admin peut la masquer du hub.

**Faire tourner**

- **Démarrer, arrêter, redémarrer.**
- **Build** à la demande.
- **Déployer** : build puis redémarrage, seulement si le build réussit. L'ancienne version reste
  servie pendant le build.
- **Port interne attribué** automatiquement (`9101`–`9140`), passé à l'application dans `$PORT`.
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

**Partager**

- **Visibilité** : privée (comptes autorisés) ou publique (lien partageable, possible seulement si une
  adresse publique est déclarée).
- **Se connecter avec CodeLab** (SSO) : l'application reconnaît la personne sans formulaire, grâce
  aux modules `codelab_auth.py` et `codelab-auth.js`.

**Applications par défaut**

- **Dagster**, **Diagnostic**, **Mailpit** et **Messages** ont leur tuile, réservée à l'administrateur.
- Elles ne se configurent pas, ne s'arrêtent pas et ne se suppriment pas : le panneau les remet en
  route à chaque tour.
- Chaque compte peut les ranger hors de son hub.

## 3. Comptes et accès

Détails : [`SECURITE.md`](SECURITE.md) et [`app-manager/README.md`](app-manager/README.md).

- **Deux rôles** : administrateur et utilisateur.
- **Invitation par mail.** La personne choisit elle-même son nom et son mot de passe. Le lien est
  aussi affiché à l'administrateur ; il peut être renvoyé ou annulé.
- **Création directe** d'un compte par l'administrateur.
- **Fiche d'un compte** :
  - adresse mail et mot de passe ;
  - applications autorisées ;
  - suspension ;
  - remise à zéro du second facteur, retrait des clés d'accès ;
  - suppression, qui emporte les clés d'accès.
- **Deux filtres sur la liste** : par personne, ou par application (« qui a accès à… »).
- **Connexion par Face ID / Touch ID** (clé d'accès) : un bouton en tête de la page de connexion,
  qui vaut mot de passe et second facteur ; aucun code à saisir. Il demande le panneau en HTTPS, par
  son nom de domaine : sinon la page dit pourquoi il n'est pas proposé.
- **Second facteur obligatoire**, dans cet ordre de préférence :
  - Face ID / Touch ID si le compte en a une clé : l'appareil confirme, aucun code n'est affiché ;
  - sinon un **code par mail** (comptes avec adresse vérifiée, et l'administrateur à son adresse
    d'alerte), saisi dans six cases : un code collé ou proposé par le clavier les remplit d'un coup,
    le sixième chiffre valide, « Renvoyer le code » attend une minute ; le mail le montre en grand ;
  - l'application d'authentification (TOTP), à un clic.
- **Mot de passe oublié** par code mail, si le compte a aussi un autre facteur.
- **Journal des accès** paginé (25 ou 50 lignes), filtrable, avec une **carte de chaleur** sur un an.
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
- **Les applications envoient sans identifiants** : `MAIL_HOST`, `MAIL_PORT`.
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
par le panneau sur `/messages` : même session, mêmes contrôles d'accès, même base. Les anciennes
adresses `/demandes` y mènent.

- **Écrire un message** (tout compte) : depuis le hub (*Paramètres > Message*, ou le widget « Écrire à
  l'administrateur » du style Bento), en choisissant le sujet : **améliorer le hub**, **une idée de
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
- **Suivre ses messages** (comptes utilisateurs) : *Paramètres > Message > Mes messages* montre où en
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
- **Assistant dans le panneau** (*Paramètres > Serveur*) : les fichiers de configuration avec tes
  valeurs, prêts à copier.
- **Clés d'accès et applications publiques** deviennent disponibles une fois l'adresse publique
  déclarée.

## 11. Installation et mise à jour

Détails : [`README.md`](README.md).

- **Un seul fichier compose.** Une variante pour les interfaces de type CasaOS existe :
  `docker-compose-casaos.yml`.
- **Secrets générés au premier démarrage** dans `credentials.env`, un bloc par service.
- **Images publiées sur GHCR** : `latest` à chaque fusion, version `X.Y.Z` à chaque tag `vX.Y.Z`,
  reconstruction hebdomadaire.
- **Squelette de `/workspace`** déposé au premier démarrage. Ses fichiers non modifiés sont mis à jour
  automatiquement.

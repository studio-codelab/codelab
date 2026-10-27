# Sécurité de CodeLab

Ce que CodeLab garantit, comment, et ce qu'il ne couvre pas. Chaque règle de la section
[« Règles à ne jamais casser »](#règles-à-ne-jamais-casser) est vérifiée par la suite de tests
(`workspace/diagnostic/tests`). Une règle modifiée dans le code se modifie ici dans le
même commit.

## En bref

- **Une seule porte d'entrée** : le panneau (`:9001`). Il authentifie tout le monde et décide pour
  Dagster et Mailpit, qui n'ont pas d'authentification à eux.
- **Second facteur obligatoire** pour tous les comptes, administrateur compris.
- **Aucun secret en clair** : mots de passe hachés, réglages sensibles chiffrés, secrets générés au
  premier démarrage. Il n'existe aucun mot de passe par défaut.
- **Applications cloisonnées** : elles sont servies sur une autre origine (`:9002`, ou un sous-domaine
  derrière un VPS), chacune tourne
  sous son propre uid et ne voit que son propre dossier.
- **Rien n'est exposé sans raison** : Postgres, Redis et le SMTP ne sont pas publiés, l'administration
  ne répond qu'au réseau local.
- **Tout reste sur ton serveur.** Seuls les mails (par ton serveur d'envoi) et, si tu l'actives, un
  signal de vie (healthchecks.io) en sortent.

## Comptes et authentification

| | Administrateur | Utilisateur |
|---|---|---|
| Création | au premier démarrage, mot de passe aléatoire dans `credentials.env` | **par invitation** ou par l'administrateur, jamais par inscription libre |
| Second facteur | **obligatoire** : application d'authentification (TOTP) ou clé d'accès | **obligatoire** : code par mail, TOTP ou clé d'accès |
| Session | **12 h** au plus | 30 jours au plus, 3 jours sans activité |
| D'où | **réseau local, en connexion directe** uniquement | de partout |
| Ouvre | tout | les applications privées autorisées et les applications publiques |
| Dagster, Mailpit | oui | **jamais** |

**Mots de passe.** 12 caractères au minimum. Stockés sous forme d'empreinte PBKDF2-HMAC-SHA256,
600 000 itérations (recommandation OWASP), avec un sel par compte ; une empreinte plus ancienne
est recalculée à la connexion suivante. Un nom inconnu coûte le même calcul qu'un vrai compte : la
durée de la réponse ne trahit pas quels comptes existent. Le mot de passe administrateur vit dans `credentials.env`.
Pour le changer, il faut redonner l'ancien, même avec une session déjà ouverte.

**Connexion.** L'identifiant et le mot de passe sont demandés d'abord, puis le second facteur sur un
écran séparé. Le second facteur peut être :

- une **clé d'accès** (Face ID, Touch ID, empreinte, Windows Hello ; norme WebAuthn). La clé ne signe
  que pour ce domaine et ce compte, et elle exige HTTPS et un nom de domaine. On peut aussi se
  connecter par clé d'accès seule, sans mot de passe. Chaque défi est tenu par le serveur : il sert
  une fois, cinq minutes au plus, si bien qu'une réponse signée capturée ne se rejoue pas ;
- un **code TOTP** (RFC 6238, 6 chiffres, 30 s). Le QR code n'apparaît jamais dans une URL.
  L'inscription d'un premier code vaut dix minutes, et elle est refusée si le compte a déjà un
  facteur ;
- un **code par mail** (comptes utilisateurs seulement). Il vaut 10 minutes, sert une fois, tolère
  5 essais et ne part que vers l'adresse vérifiée du compte, après un mot de passe juste.

Les méthodes sont indépendantes : choisir l'une n'envoie ni ne prépare rien pour une autre, et seul
le code de la méthode affichée est accepté (retenue dans le cookie signé, avec le compte). La page
propose la dernière utilisée (retenue sur la fiche du compte, réglage `dernier_moyen_admin` pour
l'administrateur) ; ce choix ne change que l'ordre, jamais la liste des méthodes permises. Chaque
envoi de mail compte comme un essai de la limite de connexion, le choix de l'application non (rien
ne part). Un compte utilisateur ajoute son application TOTP avec une connexion de moins de
15 minutes (règle 8) et la retire avec un code valide ; le serveur refuse de retirer son dernier
second facteur.

**Invitation.** Le lien contient un jeton aléatoire de 256 bits, dont seule l'empreinte est gardée,
chiffrée. Il ne sert qu'une fois et expire au bout de 7 jours. On peut le renvoyer (l'ancien lien
meurt) ou l'annuler. Ouvrir le lien prouve que l'adresse est la bonne : le compte naît avec une
adresse vérifiée.

**Mot de passe oublié.** Un code part par mail seulement si le compte a aussi une clé d'accès ou une
application TOTP. Le code par mail est alors suspendu jusqu'à la prochaine connexion par ce facteur.
Sans ce facteur, c'est l'administrateur qui pose le nouveau mot de passe. La boîte mail seule ne
permet donc jamais de prendre un compte.

**Sessions.**

- Le cookie est `HttpOnly`, `SameSite=Lax`, et `Secure` dès que la requête arrive en HTTPS.
- Toute route qui modifie quelque chose exige en plus un **jeton anti-falsification** lié à la session.
- Changer de mot de passe ou retirer ses clés d'accès **ferme les autres sessions**.
- Ajouter une clé d'accès ou changer d'adresse exige une connexion de **moins de 15 minutes**.
- Un compte suspendu perd sa session ouverte à la requête suivante.

**Supprimer son compte.** Un compte utilisateur peut demander sa suppression, avec son mot de passe
(la même limite d'essais que la connexion) : une session volée ne suffit pas. Toutes ses sessions
tombent aussitôt. Pendant 30 jours, une reconnexion ne sert que sa page de suppression : le contrôle
est fait avant chaque requête, et l'API comme les applications privées lui sont refusées. Passé ce
délai, la purge quotidienne efface le compte et ses données, ici et dans Postgres ; une ligne du
journal garde seulement qu'il a été effacé. Annuler à la reconnexion est un geste explicite : une
connexion par habitude n'annule rien, et la page de connexion ne dit pas qu'un compte est en cours
de suppression (règle 10). Le compte d'administration ne se supprime pas.

**Limite d'essais.** Au plus 5 échecs par 5 minutes et par adresse IP, sur toutes les routes de
connexion. L'en-tête `X-Forwarded-For` n'est jamais cru.

**Alerte.** Un mail part à chaque connexion administrateur depuis une adresse IP absente des 20
dernières vues.

**Téléphone perdu.**

- Administrateur : poser `APP_MANAGER_ADMIN_2FA: "0"` dans le compose, le temps de reprendre la main.
- Utilisateur : l'administrateur retire ses clés d'accès ou remet son second facteur à zéro.

## Données

| Donnée | Où | Protection |
|---|---|---|
| Comptes, droits, clés d'accès, applications, journal | `codelab.db` (SQLite) dans `/DATA/AppData/codelab/app-manager` | fichier en `0600`, `secure_delete` (une ligne supprimée est effacée du fichier) |
| Adresses mail, serveur d'envoi, invitations, destinataires d'alerte, adresse de surveillance | `codelab.db`, table `reglages_chiffres` | **chiffrés** valeur par valeur (Fernet : AES-128 + HMAC-SHA256), clé `APP_MANAGER_CHIFFREMENT_CLE` dans `credentials.env` |
| Mots de passe des comptes | `codelab.db`, table `comptes` | empreinte PBKDF2 seulement |
| Codes (connexion, vérification, réinitialisation), jetons d'invitation | `codelab.db` | empreinte seulement, usage unique, expiration |
| Secrets des services, mot de passe admin, clé de chiffrement, clé privée SSO | `credentials.env` | `0600 root`, jamais transmis aux applications |
| Historique long (journal, copie des comptes) | Postgres, base `codelab` | ni empreinte de mot de passe, ni adresse en clair |
| Messages, pièces jointes, tâches et projets de Messages | Postgres, base `messages` (celle de l'application, voir *Applications hébergées*) | le compte de cette base seulement ; repris de `codelab.db` au premier démarrage, puis effacés de `codelab.db` une fois chaque ligne vérifiée |
| Données des projets | Postgres, une base par projet | **un compte Postgres par base** : seul ce compte (et l'administrateur) peut s'y connecter |

**Une base, un compte.** Le compte administrateur de Postgres (`codelab`, super-utilisateur) n'est
plus distribué : seuls `codelab-postgres`, le panneau, Dagster et le gardien des bases du poste de
développement l'ont.

- **Chaque base de projet** a son propre compte, du même nom, propriétaire de la base. `PUBLIC`
  n'a plus le droit de connexion : un compte n'entre que dans sa base.
- **Les bases de la stack** (`dagster`, `codelab`) sont réservées à l'administrateur.
- **Une application** reçoit le compte de sa base (`POSTGRES_USER` / `POSTGRES_PASSWORD`), jamais
  celui de l'administrateur. Sa base est `CODELAB_DB` dans le `.env` du projet, sinon le nom de son
  dossier ; le panneau la crée au lancement si elle manque.
- **Le poste de développement** n'a plus le compte administrateur. Le terminal reçoit le compte du
  projet où il se trouve, et lui seul ; `codelab db` passe par le gardien des bases.
- **Les mots de passe des comptes** ne sont stockés nulle part : chacun se dérive du mot de passe
  administrateur et du nom de la base (`sha256`). Ils changent tous si ce mot de passe change, et
  `codelab-postgres` les réaligne au démarrage.
- **Le script** `postgres/proteger-base.sql` fait ce travail, le même pour les trois services. Il
  rattrape aussi les bases d'avant ce mécanisme : leurs tables passent au compte du projet.

**Conservation.**

| Donnée | Durée |
|---|---|
| Journal des accès (local et Postgres) | 12 mois par défaut, réglable (3, 6, 12, 24 mois ou sans limite, *Paramètres › Sécurité*) ; purgé chaque jour, et dès que la durée change. Le fichier local garde en plus au plus 50 000 événements |
| Messages à l'administrateur | 5 000 derniers |
| Mails dans Mailpit | 500 derniers, un mois au plus |
| Sauvegardes quotidiennes (bases, panneau) | 7 par base et pour le panneau |
| Archives hebdomadaires du code (`/workspace`) | 4 |
| Codes de connexion / de réinitialisation | 10 / 15 minutes |
| Liens d'invitation | 7 jours |

Les archives de sauvegarde sont en `0600`, et chacune est relue avant d'être gardée. Elles ne
contiennent **pas** `credentials.env` : ce fichier se sauvegarde à part, ailleurs que sur le serveur.
Sans lui, les réglages chiffrés deviennent illisibles.

## Applications hébergées

- **Une autre origine.** Les applications sont servies sur le port `9002` (un sous-domaine par
  application derrière un VPS), le panneau sur le `9001`. Un script d'une application ne peut donc
  lire ni les pages du panneau ni son jeton. Si le port `9002` ne s'ouvre pas, ou si
  `APP_MANAGER_APPS_PORT` vaut `0`, la séparation tombe : les applications reviennent dans l'origine
  du panneau, et le diagnostic le signale.
- **Le cookie du panneau ne leur parvient jamais.** Le proxy le retire de chaque requête.
- **Un uid par application.** Chaque application et son build tournent sous un uid propre (dérivé de
  son nom, à partir de 10000), jamais en root. Elles ne peuvent pas lire `credentials.env`.
- **Une vue du disque par application.** Dans un namespace utilisateur, `/workspace` ne contient que
  le projet de l'application, et son `/tmp` lui est propre. Si le noyau refuse les namespaces, le
  diagnostic le signale et l'uid par application continue d'isoler les processus.
- **Des secrets au strict nécessaire.** Une application reçoit Postgres, Redis, Mailpit et la clé
  *publique* SSO. Elle ne reçoit jamais le mot de passe du panneau, sa clé privée, le mot de passe
  d'envoi des mails ni les variables qui changent l'exécution (`PATH`, `LD_PRELOAD`…). Les
  `SMTP_HOST`, `SMTP_PORT` et `EMAIL_HOST` qu'elle reçoit désignent Mailpit, jamais le serveur
  d'envoi du panneau, et aucun identifiant ne les accompagne.
- **Une identité signée.** Une application sait qui la consulte grâce à une assertion Ed25519
  (`X-CodeLab-Auth`), valable 60 s et liée à cette application (`aud`). Le proxy retire tout en-tête
  `X-CodeLab-*` envoyé par le navigateur avant d'ajouter les vrais.
- **Privée ou publique.** Une application privée ne s'ouvre qu'à un compte autorisé ; le contrôle se
  fait dans le proxy, pas dans l'interface. Une application ne peut devenir publique que si l'adresse
  publique du serveur est déclarée.
- **Le cadre.** Seul le panneau peut afficher une application dans un cadre : il pose
  `frame-ancestors` si l'application ne le fait pas elle-même.
- **Des ressources bornées.** Les corps de requête sont limités (32 Mo vers une application, 2 Mo vers
  le panneau). Une limite mémoire optionnelle peut être posée par application, et un redémarrage
  automatique est tenté au plus 5 fois en 10 minutes.
- **Des modules sûrs par défaut** déposés par `codelab create` (et tenus à jour par `codelab update`, qui ne remplace jamais une copie modifiée) :
  - `codelab_auth` : connexion avec CodeLab, sans faire confiance aux en-têtes non signés ;
  - `codelab_web` : pages d'erreur sans pile d'appels, et en-têtes optionnels (CSP stricte, `nosniff`,
    `Referrer-Policy`, `Permissions-Policy`).
- **Messages, une application par défaut.** Elle vit dans `/workspace/messages`, sous son uid,
  avec sa base, comme toute application ; ce qui la distingue, c'est qu'elle vérifie elle-même le
  jeton signé à **chaque** requête, sans se fier au proxy :
  - sa page et son API ne répondent qu'au rôle `admin` ;
  - `/compte/...`, par où le panneau relaie le hub, au compte du jeton, pour ses seuls messages ;
  - `/interne/...` au seul rôle `panneau`, qu'aucune session ne peut porter (`jeton_du_panneau`).
  Ce qui modifie exige en plus l'en-tête `X-Messages` (un formulaire d'un autre site ne sait pas le
  poser). Le panneau garde la vérification des droits (sur quelle application un compte peut
  écrire), le délai entre deux messages et les envois (mail, notifications).
- **Logos.** Seuls les PNG, JPEG et GIF sont acceptés (512 Ko au plus), reconnus à leur signature. Le
  SVG est refusé, parce qu'il peut porter du script.

## Réseau et exposition

| Port | Service | Protection |
|---|---|---|
| `9001` | panneau | connexion + second facteur ; administration réservée au réseau local |
| `9002` | applications, toutes | selon chaque application : publique, ou privée (compte autorisé) |
| `9101`–`9140` | port interne de chaque application (`$PORT`) | non publiés ; relayés par le `9002`. L'application écoute sur `127.0.0.1` : sur `0.0.0.0`, les autres conteneurs la joindraient sans le panneau, et sa fiche le signale |
| `3000` | Dagster (par `codelab-dagster-proxy`) | session d'**administration** du panneau, vérifiée à chaque requête |
| `8025` | Mailpit (par le même proxy) | idem ; Mailpit exige en plus son propre mot de passe interne |
| `2222` | SSH (`codelab-dev`) | **clé publique uniquement**, ni mot de passe ni root |
| — | Postgres, Redis, SMTP de Mailpit | non publiés : réseau Docker interne seulement |

- **Toujours au plus strict**, sans case à cocher :
  - le HTTPS est constaté (directement ou par `X-Forwarded-Proto`), ce qui active le cookie `Secure`
    et HSTS (6 mois) ;
  - `X-Forwarded-For` n'est jamais cru ;
  - une requête relayée (`X-Forwarded-*`) n'atteint jamais l'administration.
- **En-têtes du panneau** : CSP stricte (`default-src 'self'`, `frame-ancestors 'none'`,
  `form-action 'self'`), `nosniff`, `Referrer-Policy: no-referrer`. La page du hub ajoute
  `frame-src` pour la seule origine des applications : le widget « À la une » y affiche un aperçu
  inerte (`sandbox`, sans clic) de la dernière application ouverte.
- **Exposition recommandée** : un VPS qui porte le domaine et le certificat, et un tunnel WireGuard
  vers ton serveur ([`app-manager/vps/README.md`](app-manager/vps/README.md)). Aucun port n'est
  alors ouvert sur ta box.
- Ne publie pas `3000`, `8025` ni `2222` sur Internet sans besoin.

## Conteneurs et chaîne logicielle

- **Six services sur sept** tournent avec `cap_drop: ALL` et `no-new-privileges`. Chaque capacité
  rendue est justifiée dans `docker-compose.yml`. `codelab-dev` en est exclu volontairement : c'est
  le shell de développement.
- **Les services ne tournent pas en root** une fois démarrés : Dagster sous l'uid 1002, les
  applications sous leur propre uid.
- **Des plafonds partout** : mémoire, processeur et nombre de processus par service. Aucun service ne
  dépasse le tiers de la machine, pour que le panneau reste joignable même quand une application
  s'emballe. Les journaux Docker tournent (3 × 10 Mo).
- **Redis** : un mot de passe, et les commandes d'administration désactivées (`CONFIG`, `MONITOR`,
  `SHUTDOWN`, `MODULE`, `DEBUG`…).
- **CI** :
  - les actions GitHub sont épinglées par SHA et tenues à jour par Dependabot ;
  - les images sont reconstruites chaque semaine pour recevoir les correctifs de leur image de base ;
  - Trivy analyse le dépôt chaque semaine (vulnérabilités, secrets, mauvaises configurations).
- **Les dépendances des projets** sont auditées chaque semaine contre la base OSV (asset
  `audit_dependances` du diagnostic). Une vulnérabilité fait échouer le run, donc partir un mail.

## Surveillance

- **Journal des accès** : connexions, refus, ouvertures d'applications, changements de compte, avec
  l'adresse IP. Une carte de chaleur montre l'activité sur l'année. Une adresse IP est une donnée
  personnelle : le journal ne la garde que la durée choisie (voir *Conservation*).
- **Alertes par mail** :
  - une application tombée qui ne redémarre plus, puis son retour ;
  - une connexion administrateur depuis une adresse inconnue ;
  - un run Dagster en échec.
- **Notifications sur l'appareil** (Web Push) : chiffrées de bout en bout (RFC 8291) et signées par
  le serveur (VAPID, RFC 8292) ; la clé de signature vit chiffrée dans le coffre, jamais en clair.
  Le serveur n'envoie qu'aux services de notification connus (Apple, Google, Mozilla, Microsoft), en
  HTTPS : l'adresse fournie par le navigateur ne peut pas faire de lui un relais vers le réseau
  local. Un abonnement appartient à une session : déconnecter l'appareil le supprime.
- **Surveillance externe** : le panneau appelle une adresse healthchecks.io toutes les 5 minutes. Si
  le serveur entier tombe, c'est ce service qui prévient.
- **Diagnostic** : des sondes en lecture seule vérifient ce qui doit marcher et ce qui doit être
  fermé (panneau sans session, origine des applications, exposition, isolation, sauvegardes, disque,
  surface exposée).
- **Pentests** (vérification approfondie du diagnostic) : six attaques sans compte contre le panneau
  en marche. Elles visent les routes d'administration, les en-têtes, la traversée de chemin,
  l'en-tête Host, le laissez-passer de tournage et la limitation des essais. Chacune vise une cible
  inexistante : une garde cassée se voit à la réponse, jamais à un dégât. La suite du dépôt les joue
  contre le vrai panneau et contre un serveur troué exprès.
- **Bilan de sécurité** (*Paramètres > Sécurité*) : sept contrôles, chacun avec ce qu'il reste à faire.
  Un contrôle sans objet ici n'est pas compté.

## Règles à ne jamais casser

**Authentification**

1. Aucun compte n'ouvre de session sur le seul mot de passe. Seule exception, documentée : le compte
   administrateur avec `APP_MANAGER_ADMIN_2FA=0`.
2. Un code (connexion, vérification, réinitialisation) n'est stocké que sous forme d'empreinte. Il
   sert une fois, expire et accepte 5 essais au plus.
3. Un code ne part que vers une destination enregistrée sur le compte, après un mot de passe juste.
4. La boîte mail seule ne permet jamais de prendre un compte.
5. Un compte qui a déjà un facteur ne peut pas en enregistrer un nouveau sur le seul mot de passe.
6. Au plus 5 échecs par 5 minutes et par IP sur toutes les routes de connexion.
7. Changer de mot de passe ou retirer ses clés d'accès ferme les autres sessions.
8. Ajouter une clé d'accès ou changer d'adresse exige une connexion de moins de 15 minutes.
9. Un compte suspendu n'entre plus, par aucun moyen.
9 bis. Un compte ne demande sa suppression qu'avec son mot de passe ; pendant le délai, il n'ouvre
    que sa page de suppression. Le compte d'administration ne se supprime pas.
10. Les réponses ne révèlent pas si un compte existe.

**Accès**

11. Dagster et Mailpit ne s'ouvrent qu'à une session d'administration.
12. Une application privée ne s'ouvre qu'à un compte autorisé. Les applications par défaut sont
    réservées à l'administrateur, et ce droit ne peut être accordé à aucun compte (fiche, invitation
    ou droit ancien : il est ignoré).
12 bis. Un compte utilisateur ne reçoit ni l'état des applications ni le détail des erreurs
    techniques ; une application qui ne s'ouvre pas lui montre une page neutre. Les widgets
    d'administration ne se posent que pour l'administrateur, contrôlé côté serveur.
12 ter. Seule exception à la règle 12 : le laissez-passer de tournage de `codelab video`. Il
    s'obtient avec la clé du poste de développement (`APP_MANAGER_TOURNAGE_CLE`), depuis le réseau
    local, sous la limite de la règle 6. Il vaut pour UNE application, ni par défaut ni archivée :
    un ticket à usage unique d'une minute, puis un cookie de dix minutes. Il donne à cette
    application l'identité de l'administrateur (marquée `tournage`), jamais une session du panneau.
    Arrêtée, l'application démarre, par le même chemin que le bouton du panneau : seulement sa
    commande déjà enregistrée, jamais une nouvelle. Elle peut se désigner par son dossier de projet,
    dans `/workspace` seulement.
    La session du poste a déjà le compte de chaque base de projet : le laissez-passer ne lui donne
    pas plus.
13. Toute route qui modifie quelque chose exige le jeton anti-falsification de la session, sauf les
    routes de connexion et d'invitation, et celle du tournage, qui n'a pas de session et se prouve
    par la clé en en-tête (12 ter).
14. Pas d'inscription libre.
15. Le contrôle des droits se fait côté serveur, jamais seulement dans l'interface.

**Données**

16. Aucun mot de passe, aucune adresse mail, aucun mot de passe d'envoi n'est écrit en clair sur le
    disque.
17. La base du panneau et les archives de sauvegarde sont en `0600`.
18. Le diagnostic n'ouvre la base du panneau qu'en lecture seule.
18 bis. L'application Messages ne répond à aucune requête sans le jeton signé du panneau ; ses
    routes `/interne/` n'acceptent que le rôle `panneau`, que le panneau seul signe et qu'aucune
    session ne porte. Le panneau n'efface les données qu'il lui transmet qu'après avoir vérifié
    qu'elles y sont toutes.

**Applications**

19. Les applications ne reçoivent jamais le cookie du panneau, ni celui de tournage, ni la clé de
    tournage, ni son mot de passe, sa clé privée, le mot de passe d'envoi des mails ou le compte
    administrateur de Postgres : seulement le compte de leur base.
22. Une base de projet n'accepte que son propre compte (et l'administrateur) ; les bases `dagster`
    et `codelab`, que l'administrateur. Le poste de développement n'a pas le compte administrateur.
20. Les applications et leurs builds ne tournent jamais en root.
21. Un nom d'application ne peut pas prendre celui d'une route du panneau : la liste est tirée de la
    table des routes elle-même.

## Ce que CodeLab ne protège pas

- **Une faille dans une de tes applications** reste une faille dans cette application. CodeLab
  l'isole du panneau et des autres secrets, il ne la corrige pas.
- **Les applications partagent le groupe `codelab`** sur `/workspace`, pour que ton code reste
  modifiable en SSH. Sans namespaces utilisateur, une application peut lire les fichiers d'une autre
  par ce groupe.
- **Les applications partagent le réseau local du conteneur.** Une application malveillante ou
  compromise peut joindre directement le port d'une autre, sans passer par le panneau, donc sans
  la connexion qu'impose la visibilité *Privée*. Une application aux données sensibles vérifie
  elle-même le jeton signé `X-CodeLab-Auth` (`codelab_auth.utilisateur`), qu'une autre
  application ne peut pas fabriquer.
- **Sur le port des applications (sans VPS), toutes partagent une origine** (`:9002`). Un script
  d'une application peut donc agir, au nom du visiteur, dans une autre application que ce visiteur
  ouvre. Messages en fait partie depuis qu'elle est une application : un administrateur qui ouvre
  une application hostile sur `:9002` lui expose sa boîte. Derrière le VPS, chaque application a son
  sous-domaine, donc son origine.
- **Derrière le VPS, tous les visiteurs d'Internet partagent une adresse** pour la limite des
  essais : cinq mauvais mots de passe, de qui que ce soit, suspendent les connexions distantes
  cinq minutes. C'est voulu : croire `X-Forwarded-For` permettrait de contourner la limite.
- **L'administrateur voit tout** : les applications, le journal, et les mails passés par Mailpit.
  Il ne voit jamais un mot de passe, ni un mail de sécurité : codes, liens d'invitation et alertes
  de connexion partent en direct, sans copie dans Mailpit.
- **Sans HTTPS**, une session peut être interceptée sur le réseau.
- **La boîte mail de chaque utilisateur** reçoit ses codes : sa sécurité compte. La clé d'accès reste
  le moyen le plus sûr.
- **Le serveur lui-même** (système, mises à jour, accès physique) est sous ta responsabilité.
- **Dagster garde le compte administrateur de Postgres.** Il charge les `definitions.py` de tous
  les projets dans un même processus : le code d'un job peut donc atteindre toutes les bases. C'est
  la limite du cloisonnement des bases ; Dagster reste réservé à l'administrateur.
- **Dans le poste de développement, tu peux écrire le code de tous les projets.** Le terminal n'a
  que le compte du projet ouvert, et SQLTools ouvre chaque base avec son propre compte ; mais rien
  n'empêche de lire volontairement le compte d'un autre projet (`~/.codelab-bases`), ni
  d'utiliser la clé de tournage (`~/.codelab-tournage`) pour filmer une application privée. Le cloisonnement
  y est un garde-fou contre l'erreur, pas une barrière contre toi-même.

## Mise en production

- [ ] HTTPS en place (VPS recommandé). Le cookie `Secure` et HSTS suivent d'eux-mêmes.
- [ ] Second facteur actif sur le compte administrateur (clé d'accès de préférence).
- [ ] Serveur d'envoi réglé (*Paramètres > E-mail*) et adresse d'alerte renseignée. Sans serveur
      d'envoi, aucun compte ne peut être invité.
- [ ] Surveillance externe réglée (*Paramètres > Alertes*).
- [ ] `credentials.env` sauvegardé à part, hors du serveur.
- [ ] Sauvegardes copiées ailleurs (`rsync` du dossier `sauvegardes/`).
- [ ] Ports `3000`, `8025` et `2222` non publiés sur Internet.
- [ ] Diagnostic au vert, et bilan de sécurité lu.
- [ ] [`INCIDENTS.md`](INCIDENTS.md) lu une fois, au calme : `credentials.env` et les sauvegardes
      sont bien là où il dit de les chercher.

## Signaler une faille

Ouvre une *issue* privée (*Security advisory*) sur le dépôt GitHub, ou écris au mainteneur. Ne publie
pas le détail avant qu'un correctif soit disponible.

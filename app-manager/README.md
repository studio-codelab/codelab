# codelab-app-manager — le panneau

Le cœur de CodeLab. Un seul service Flask (servi par waitress) qui :

- **authentifie** tout le monde (comptes, second facteur, clés d'accès) et décide pour Dagster et
  Mailpit ;
- **déclare, construit, lance et supervise** les applications de `/workspace` ;
- **sert les applications** par un reverse proxy, sur une origine à part ;
- **envoie** les mails (par Mailpit), les alertes et les invitations ;
- **sauvegarde** son propre état et le code de `/workspace`.

| Port | Sert |
|---|---|
| `9001` | le panneau et son API |
| `9002` | les applications, sous `/<nom>/` (`APP_MANAGER_APPS_PORT`, `0` pour les remettre sur `9001`) |

Ce que l'utilisateur voit du panneau est décrit dans [`docs/FONCTIONNALITES.md`](../docs/FONCTIONNALITES.md) ;
ses garanties de sécurité dans [`SECURITE.md`](../SECURITE.md).

## Fichiers

| Fichier | Rôle |
|---|---|
| `Dockerfile` | Python 3.13, Node, npm, git ; Flask, waitress, psutil, qrcode, webauthn, psycopg, cryptography |
| `entrypoint.sh` | permissions partagées sur `/workspace`, puis démarrage |
| `app/app.py` | le service : authentification, API, cycle de vie des processus, proxy, mails, sauvegardes |
| `app/stockage.py` | la base SQLite : connexions, schéma versionné (`MIGRATIONS`), sauvegarde cohérente |
| `app/dashboard.html`, `app/login.html` | les deux pages |
| `app/theme.css` | **toutes** les couleurs, formes et tailles, pour les deux styles et leurs thèmes clair et sombre ; servi aussi aux applications |
| `app/polices/` | Manrope et Plus Jakarta Sans, auto-hébergées, servies par liste blanche |
| `vps/` | exposer le panneau par un VPS ([`vps/README.md`](vps/README.md)) ; ne fait pas partie de l'image |

## Cycle de vie d'une application

Le panneau ne crée jamais de projet : il **déclare** un dossier existant de `/workspace`.

1. **Ajout** :
   - le dossier est inspecté et les commandes de build et de lancement sont proposées (tableau dans
     [`dev/README.md`](../dev/README.md#déployer)) ;
   - un port libre est attribué dans `9101`–`9140`.
2. **Nom et adresse.** Le nom saisi est libre. L'adresse en est dérivée : minuscules, espaces et
   caractères interdits changés en tirets (`Mon Site` → `/mon-site/`).
   - Une adresse vide est refusée, de même qu'une adresse prise par une route du panneau
     (`adresses_reservees()`).
   - Renommer met à jour l'adresse, les droits et les fichiers.
3. **Lancement** : `bash -lc <commande>` dans le dossier.
   - **Identité** : un uid propre, dérivé du nom (`APP_MANAGER_UID_BASE`, 10000 par défaut), groupe
     `codelab`, umask `002`.
   - **Isolation** : un namespace utilisateur et de montage où `/workspace` ne contient que le projet,
     et `/tmp` est privé (`APP_MANAGER_ISOLER=0` pour couper).
   - **Mémoire** : une limite optionnelle (`RLIMIT_AS`).
   - **Journal** : la sortie va dans `logs/<nom>.log`, avec rotation à 2 Mo.
4. **Environnement** :
   - `PORT`, `CODELAB_APP`, `PYTHONUNBUFFERED`, un `HOME` dédié ;
   - `CODELAB_SSO_PUBLIC_KEY`, `CODELAB_SSO_ISSUER`, `CODELAB_SSO_AUDIENCE` ;
   - les blocs `codelab-postgres`, `codelab-redis` et `codelab-mailpit` de `credentials.env`, relus à
     chaque lancement ;
   - **jamais** : `APP_MANAGER_*`, le bloc `codelab-alertes`, la clé privée SSO, ni `PATH`, `HOME`,
     `PYTHONPATH`, `PYTHONHOME`, `LD_PRELOAD` ou `LD_LIBRARY_PATH` lus dans le fichier.
5. **Build** : même identité et même environnement, délai de 10 minutes, sortie dans le journal.
   **Déployer** enchaîne build puis redémarrage, seulement si le build réussit.
6. **Surveillance** (`monitor_tick`, toutes les 10 s) :
   - un crash est relancé, 5 fois au plus en 10 minutes ; au-delà, « boucle de crash » et alerte ;
   - une sonde TCP sur le port interne donne l'état « Ne répond pas » ;
   - les applications par défaut sont remises en route.
7. **Reprise** : au démarrage du panneau, toute application active est relancée.
8. **Arrêt** : `SIGTERM` au groupe de processus, puis `SIGKILL` après 3 s.

**Applications par défaut.** `dagster`, `diagnostic` et `mailpit`, réservées à l'administrateur.
- Elles ne se configurent, ne s'arrêtent et ne se suppriment pas (`403` côté serveur).
- Le diagnostic est réinscrit à chaque démarrage : commande `python3 app.py`, build
  `pip install --target vendor "psycopg[binary]"`, visibilité privée.

## Le proxy

- `/<nom>/...` sur `9002` est relayé vers `127.0.0.1:<port interne>/...`, **sans** le préfixe.
- Réponses quand l'application n'est pas prête :
  - `503` (avec une page) si elle est arrêtée ;
  - `502` si elle démarre encore.
- **En-têtes** :
  - le cookie du panneau et tout en-tête `X-CodeLab-*` envoyé par le navigateur sont retirés ;
  - le proxy ajoute `X-CodeLab-Panneau` (l'adresse du panneau) et, pour une personne connectée,
    `X-CodeLab-Auth` : une assertion EdDSA/JWT valable 60 s, avec `aud` = l'application.
  - S'y ajoutent, à titre indicatif seulement, `X-CodeLab-User`, `X-CodeLab-Role`, `X-CodeLab-Email`
    et `X-CodeLab-Authenticated`.
- **Visibilité** : une application privée exige une session autorisée pour elle. Sans session, la
  personne passe par `/login?suite=/ouvrir.app/<nom>/<chemin>`. Une application ne peut devenir
  publique que si une adresse publique est déclarée.
- **Cadre** : `frame-ancestors` est ajouté si l'application ne le pose pas. `nosniff` est toujours
  posé.
- **Limites** :
  - corps de 32 Mo au plus vers une application (`APP_MANAGER_CORPS_MAX_MO`), 2 Mo vers le panneau ;
  - 600 s de silence toléré (`APP_MANAGER_TIMEOUT`).
- **Déconnexion depuis une application** : `/deconnexion.html?app=<nom>&chemin=…` demande confirmation.

## Comptes, mails, alertes

Le fonctionnement des comptes est décrit dans [`SECURITE.md`](../SECURITE.md). Côté service :

**Administrateur**

- Son mot de passe (`APP_MANAGER_ADMIN_PASSWORD`) et son secret TOTP vivent dans `credentials.env`.
- Le nom `admin` est réservé.

**Envoi de mails**

- Tous les mails partent par Mailpit (`APP_MANAGER_MAIL_PAR_MAILPIT`), sauf les mails de sécurité
  (codes, invitations, alerte de connexion admin : `sensible=True`), envoyés en direct sans copie.
- Si Mailpit ne répond pas, le panneau envoie directement par le serveur d'envoi.
- Le panneau publie ce serveur à Mailpit dans le bloc `codelab-relais-mail`.

**Deux configurations d'envoi**

- **D'origine** : le bloc `codelab-alertes` de `credentials.env`, aussi lu par le capteur Dagster.
  Le panneau ne le réécrit jamais.
- **Personnalisée** : saisie dans *Paramètres > E-mail*, chiffrée en base. Elle est essayée avant
  d'être enregistrée. Si un envoi échoue, le mail repart par celle d'origine. Vider l'hôte revient à
  celle d'origine.

| Clé (`codelab-alertes`) | Rôle |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | serveur d'envoi (587 STARTTLS, 465 SSL) |
| `SMTP_TLS` | `starttls` (défaut), `ssl`, `none` |
| `SMTP_USER`, `SMTP_PASSWORD` | optionnels ; Gmail exige un mot de passe d'application |
| `ALERTE_FROM` | expéditeur, si différent de `SMTP_USER` |
| `ALERTE_ADMIN` | reçoit toutes les alertes |

**Alertes d'application**

- Un mail quand l'application épuise ses 5 relances, puis un autre à son retour. Un arrêt volontaire
  n'en envoie pas.
- Les destinataires se règlent par application ; l'adresse d'alerte de l'administrateur s'ajoute
  toujours.

**Surveillance externe** : une adresse healthchecks.io appelée toutes les 5 minutes, et aussitôt à
l'enregistrement.

## Données : `codelab.db`

Une base SQLite dans `APP_MANAGER_STATE` (`/DATA/AppData/codelab/app-manager` sur l'hôte).

- **Fichier** en `0600`, mode WAL, `secure_delete`.
- **Écritures** : chacune est une transaction.
- **Schéma** versionné par `PRAGMA user_version` : une évolution s'ajoute à `MIGRATIONS` dans
  `stockage.py`.

| Table | Contenu |
|---|---|
| `applications` | dossier, commandes, port, actif, visibilité, catégorie, limite mémoire, titre, description |
| `comptes` | empreinte du mot de passe et sel, adresse vérifiée, suspension, second facteur |
| `acces_applications` | qui ouvre quelle application privée |
| `cles_acces` | clés WebAuthn : clé publique, compteur, nom, dernière utilisation |
| `categories`, `masquees` | catégories du hub ; ce que chaque compte a rangé hors de son hub |
| `journal` | accès, refus, ouvertures, changements de compte (50 000 derniers) |
| `versions_apps`, `apps_vues`, `preferences` | dernière version connue de chaque application (déploiement, build, commit), version vue par chaque compte, widgets et bloc-notes du hub |
| `messages`, `taches`, `sous_taches`, `projets` | demandes à l'administrateur, tâches (statut, priorité, échéance, notes), sous-tâches et projets (idée, construction, réalisé) |
| `processus` | processus lancés, retrouvés après un redémarrage du panneau |
| `parametres` | réglages en clair : exposition, VPS |
| `reglages_chiffres` | réglages sensibles, chiffrés (Fernet) avec `APP_MANAGER_CHIFFREMENT_CLE` |
| `meta` | compteurs internes |

- **Pourquoi SQLite** : le panneau doit démarrer et se réparer même si Postgres est en panne.
- **Copie dans Postgres** : le journal et les comptes, sans secret, dans la base `codelab`
  (`APP_MANAGER_PG=0` pour couper).
- **Diagnostic** : il ouvre la base en lecture seule.
- **Ancienne version** : les anciens fichiers JSON sont importés au premier démarrage, puis renommés
  en `.migre`.

```sh
sqlite3 /DATA/AppData/codelab/app-manager/codelab.db ".tables"
```

## Sauvegardes

| Archive | Fréquence | Gardées | Réglage |
|---|---|---|---|
| `panneau-<date>.tar.gz` : état du panneau, avec un instantané SQLite cohérent ; ni journaux ni `credentials.env` | `APP_MANAGER_SAUVEGARDE_HEURES` (24) | `APP_MANAGER_SAUVEGARDES_GARDEES` (7) | `APP_MANAGER_SAUVEGARDES` (`/sauvegardes`) |
| `workspace-<date>.tar.gz` : code des projets, sans dépendances, builds, caches ni fichiers de plus de 50 Mo | `APP_MANAGER_WORKSPACE_JOURS` (7 jours) | 4 | — |

Chaque archive est en `0600` et relue avant d'être gardée. Pour restaurer, voir
[`postgres/README.md`](../postgres/README.md#restaurer-létat-du-panneau).

## `credentials.env`

Le fichier unique de secrets, partagé par les services. Chaque service écrit **son** bloc entre
marqueurs (`# ===== <bloc> =====`), sous un verrou `flock`. Le panneau écrit :

- `codelab-app-manager` : `APP_MANAGER_ADMIN_PASSWORD`, `APP_MANAGER_SESSION_SECRET`,
  `APP_MANAGER_CHIFFREMENT_CLE`, `APP_MANAGER_TOTP_SECRET` ;
- `codelab-sso` : la clé de signature des assertions ;
- `codelab-relais-mail` : le serveur d'envoi publié à Mailpit.

Une valeur existante n'est jamais régénérée.

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `APP_MANAGER_STATE` | `/var/lib/codelab/app-manager` | dossier d'état : `codelab.db`, `logs/`, `home/` des applications |
| `APP_MANAGER_ROOT` | `/workspace` | racine des projets ; tout chemin d'application y est borné |
| `APP_MANAGER_SHARED_CONFIG` | `/var/lib/codelab/config` | dossier de `credentials.env` |
| `MANAGER_PORT` | `9001` | port du panneau |
| `APP_MANAGER_APPS_PORT` | `9002` | port des applications ; `0` = sous le port du panneau |
| `APP_MANAGER_APPS_URL`, `APP_MANAGER_PANEL_URL` | — | adresses complètes quand un reverse proxy publie autrement |
| `APP_MANAGER_PUBLIC_URL` | — | adresse publique ; également réglable dans *Paramètres > Serveur* |
| `APP_MANAGER_HTTPS` | — | `1` force le cookie `Secure` (sinon constaté à chaque requête) |
| `APP_MANAGER_ADMIN_2FA` | `1` | `0` : marche arrière si le second facteur admin est perdu |
| `APP_MANAGER_ISOLER` | `1` | isolation du système de fichiers par application |
| `APP_MANAGER_UID_BASE`, `APP_MANAGER_UID_PLAGE` | `10000`, `5000` | plage des uid par application |
| `APP_MANAGER_RUN_AS_UID`, `APP_MANAGER_RUN_AS_GID` | `1001`, `2000` | identité de repli et groupe partagé |
| `APP_MANAGER_THREADS` | `16` | threads de waitress ; les flux de journal en prennent la moitié au plus |
| `APP_MANAGER_TIMEOUT` | `600` | silence toléré sur une connexion, en secondes |
| `APP_MANAGER_CORPS_MAX_MO` | `32` | taille maximale d'une requête vers une application |
| `APP_MANAGER_PG`, `APP_MANAGER_PG_BASE` | `1`, `codelab` | copie du journal et des comptes dans Postgres |
| `APP_MANAGER_MAIL_PAR_MAILPIT` | `1` | envoi des mails du panneau par Mailpit |
| `APP_MANAGER_MAILPIT_SMTP_HOTE`, `APP_MANAGER_MAILPIT_SMTP_PORT` | `codelab-mailpit`, `1025` | SMTP de Mailpit |
| `APP_MANAGER_DAGSTER_*`, `APP_MANAGER_MAILPIT_*` (`_HOTE`, `_PORT`, `_AMONT`) | proxy et services de la stack | où sonder Dagster et Mailpit |
| `APP_MANAGER_SAUVEGARDES`, `APP_MANAGER_SAUVEGARDE_HEURES`, `APP_MANAGER_SAUVEGARDES_GARDEES`, `APP_MANAGER_WORKSPACE_JOURS` | voir *Sauvegardes* | sauvegardes |

## Volumes

| Montage | Contenu |
|---|---|
| `/var/lib/codelab/app-manager` | `codelab.db`, `logs/`, `home/` |
| `/workspace` | les projets |
| `/var/lib/codelab/config` | `credentials.env` (lecture-écriture) |
| `/sauvegardes` | archives du panneau et du code |

## API

Toutes les routes ci-dessous exigent une session, sauf mention contraire, et toute route qui modifie
quelque chose exige le jeton anti-falsification. Les routes marquées **admin** refusent les autres
comptes (`require_admin`).

| Domaine | Routes |
|---|---|
| Santé | `GET /health` (sans session) ; `GET /api/auth-check` (sans session : 200 si la session est admin, interrogé par le proxy Dagster et Mailpit) |
| Connexion | `GET/POST /login`, `POST /login/second-facteur`, `POST /login/second-facteur/passkey[/options]`, `POST /login/passkey[/options]`, `POST /login/inscription/passkey[/options]`, `POST /logout`, `POST /mot-de-passe/oubli`, `POST /mot-de-passe/reinitialiser` (sans session, sauf `logout`) |
| Invitations | `GET/POST /api/invitations`, `POST /api/invitations/<id>/renvoyer`, `DELETE /api/invitations/<id>` (admin) ; `GET/POST /invitation/<jeton>`, `GET /api/invitation/<jeton>` (sans session) |
| Mon compte | `GET /api/mon-compte`, `POST /api/mon-compte/email[/code\|/confirmer]`, `POST /api/compte/mot-de-passe`, `GET/POST/DELETE /api/mon-compte/passkeys[/options\|/<id>]`, `GET /api/passkeys/etat` (sans session), `GET /qr/totp.svg` |
| Sécurité | `GET /api/securite`, `PUT /api/securite/exposition`, `POST /api/securite/totp/preparer\|activer\|desactiver`, `GET/PUT /api/vps` |
| Comptes (admin) | `GET/POST /api/utilisateurs`, `PUT/DELETE /api/utilisateurs/<nom>`, `GET/PUT /api/apps/<nom>/acces`, `GET /api/activite`, `GET /api/activite/carte` |
| Applications (admin) | `GET /api/apps`, `GET /api/browse`, `GET /api/detect`, `POST /api/add`, `PUT/DELETE /api/app/<nom>`, `POST /api/app/<nom>/renommer`, `POST /api/toggle\|restart\|build\|deploy\|visibility/<nom>`, `GET /api/metrics/<nom>`, `GET /api/logs/<nom>[/stream]`, `PUT/DELETE /api/app/<nom>/logo` |
| Hub | `GET /api/mes-apps` (avec `nouvelle_version` et `ouverte_le` par application), `POST/DELETE /api/mes-apps/<nom>/masquer`, `POST /api/mes-apps/<nom>/ouverte` (note la version vue), `GET/PUT /api/hub/widgets`, `PUT /api/hub/notes`, `GET /api/icon/<nom>`, `GET /api/categories` (tous) ; `PUT /api/categories` (admin) |
| Messages | `POST /api/messages` (tous) ; `GET /api/messages`, `POST /api/messages/lus`, `PUT/DELETE /api/messages/<id>`, `PUT /api/messages/<id>/lu`, `POST /api/messages/<id>/accepter` (admin : en tâche `{titre, projet, priorite, sous_taches}`, en projet `{nouveau_projet, taches}`, ou `{}` sans suite) |
| Tâches (admin) | `GET/POST /api/taches`, `PUT/DELETE /api/taches/<id>`, `PUT /api/taches/ordre` (une colonne du tableau : statut et ordre), `POST /api/taches/<id>/sous-taches`, `PUT /api/taches/<id>/ordre`, `PUT/DELETE /api/sous-taches/<id>` |
| Projets (admin) | `GET/POST /api/projets`, `PUT/DELETE /api/projets/<id>` (supprimer renvoie ses tâches dans « Général »), `PUT /api/projets/ordre` |
| Alertes (admin) | `GET/POST /api/alertes`, `PUT /api/alertes/application/<nom>`, `GET/POST /api/surveillance-externe` |
| Pages | `/`, `/apercu`, `/applications[/<nom>[/<onglet>]]`, `/utilisateurs`, `/demandes[/<vue>]` (admin ; `/messages` y redirige), `/parametres[/<onglet>]`, `/ouvrir.app/<nom>/<chemin>`, `/deconnexion.html` ; `/espace` redirige vers `/` |
| Ressources | `/theme.css`, `/polices/<nom>`, `/favicon.*`, `/apple-touch-icon*.png`, `/manifest.webmanifest` (sans session) ; `POST /api/erreur-navigateur` |
| Proxy | `/<nom>/...` sur le port des applications |

## Développer, tester

```bash
python3 -m pytest workspace/diagnostic/test_regressions.py -q     # depuis la racine du dépôt

docker build -f app-manager/Dockerfile -t codelab-app-manager-test .
docker run --rm -p 9001:9001 -p 9002:9002 \
  -v "$PWD/workspace-test:/workspace" \
  -v codelab-app-manager-test-state:/var/lib/codelab/app-manager \
  codelab-app-manager-test
```

Au premier lancement, le mot de passe administrateur apparaît dans `docker logs`.

**Styles** : le cookie `codelab-style` (`lanceur` ou `bento`) est lu par le serveur
(`STYLES_PAGE`), qui pose `data-style="design"` et `data-variante` sur `<html>` avant le premier
affichage. Sans cookie, ou avec celui d'un ancien style, c'est `bento`.

**Conventions de l'interface** :
- jamais d'`alert()`, `confirm()` ni `prompt()` : une confirmation dont le bouton porte le verbe, et
  des notifications pour les erreurs ;
- l'accent de couleur ne désigne que ce qui se clique ; les états ont leurs propres couleurs ;
- les chiffres s'alignent (`tabular-nums`).

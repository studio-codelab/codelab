# CodeLab -- documentation technique

Comment CodeLab est construit, expose, sauvegarde et durci. Pour ce qu'il **fait**, voir le
[README](README.md) ; pour developper un projet dedans, [`dev/README.md`](dev/README.md).

## Sommaire

| | |
|---|---|
| [Organisation du depot](#organisation-du-depot) | un dossier par service |
| [Un seul fichier de secrets](#un-seul-fichier-de-secrets) | `credentials.env` |
| [Acces SSH](#acces-ssh-depuis-plusieurs-ordinateurs) | une cle par machine |
| [Workspace partage](#workspace-partage-entre-les-services) | groupe commun, setgid, umask |
| [HTTPS](#sortir-de-chez-toi--mettre-du-tls-devant-codelab) | trois routes, et ce qu'il faut poser ensuite |
| [Isolation des applications](#ce-quune-application-peut-voir-des-autres) | ce qu'une application voit des autres |
| [Pare-feu](#limiter-les-ports-au-reseau-local) | borner les ports au reseau local |
| [Sauvegarde](#sauvegarder-et-verifier-la-sauvegarde) | chiffree, et verifiee |
| [Audit](#auditer-une-instance-en-marche) | sonder une instance qui tourne |
| [Persistance](#persistance-des-donnees) | ou vivent les donnees |
| [Tests et CI](#tests) | ce qui est garde, et comment les images sortent |

## Organisation du depot

```text
codelab/
├── README.md       # ce que CodeLab fait, et comment s'en servir
├── TECHNIQUE.md    # ce fichier : construction, exposition, sauvegarde, durcissement
├── docker-compose.yml
├── icon.svg / icon.png
├── outils/         # sauvegarde chiffree, pare-feu, audit dynamique (sur l'hote)
├── .github/
│   ├── dependabot.yml
│   └── workflows/  # construction des images, et analyse de securite
├── workspace/      # squelette depose dans /workspace au premier demarrage
├── postgres/       # serveur de bases — voir postgres/README.md
├── dev/            # SSH + VS Code Remote-SSH — voir dev/README.md
├── dagster/        # orchestration de jobs — voir dagster/README.md
│   └── proxy/      # authentification devant Dagster (image a part)
└── app-manager/    # deploiement d'applications — voir app-manager/README.md
    ├── app/        # le service : app.py et les deux pages qu'il sert
    ├── tests/      # regressions gardees : detection, chemins, auth, privileges
    └── vps/        # tunnel WireGuard + nginx pour l'exposer — voir son README
```

**Un dossier par service**, et tout ce qui concerne un service vit dedans : son image, son code, sa
documentation, ses tests. Le proxy de Dagster est une image distincte -- deux processus, deux conteneurs --
mais il n'a rien a faire a la racine : il n'existe que pour Dagster.

`app-manager/vps/` suit la meme regle, jusqu'au bout : ces fichiers s'installent sur une autre
machine, mais ils n'existent que pour exposer le panneau. Les ranger a la racine aurait fait croire
a un sixieme service ; les ranger ici dit de quoi ils dependent.

**Trois documents a la racine**, et chacun repond a une question differente :

| | |
|---|---|
| [`README.md`](README.md) | ce que CodeLab fait, et comment s'en servir |
| `TECHNIQUE.md` | comment c'est construit, expose, sauvegarde et durci |
| [`dev/README.md`](dev/README.md) | developper un projet et le mettre en ligne |

Le fonctionnement interne de chaque service -- scripts de demarrage, variables d'environnement,
pieges connus -- est documente dans le `README.md` de son dossier.

## Un seul fichier de secrets

Mot de passe Postgres, mot de passe admin app-manager, cle de session, secret de double authentification : **tout est dans `credentials.env`, et nulle part ailleurs.** Aucun fichier mono-secret a cote, rien a aller chercher dans un conteneur :
```bash
cat /DATA/AppData/codelab/config/credentials.env
```
Chaque service ecrit son propre bloc au demarrage (delimite par `# ===== <service> =====`, avec le commentaire
qui explique a quoi sert chaque valeur) et lit celui des autres. `codelab-postgres` genere le mot de passe et
l'ecrit la ; `codelab-dev` et les deux services Dagster le relisent depuis ce fichier ; `app-manager` y depose
son mot de passe admin et sa cle de session. Une valeur deja presente n'est jamais regeneree par-dessus.

Sauvegarder ce fichier (et `config/ssh/`) suffit a sauvegarder tous les acces.

## Acces SSH depuis plusieurs ordinateurs

[Acces SSH depuis plusieurs ordinateurs](#acces-ssh-depuis-plusieurs-ordinateurs)

Chaque machine autorisee a son propre fichier dans `config/ssh/authorized_keys.d/`.
`authorized_keys` n'est plus edite a la main : c'est un fichier **derive**, reconstruit au demarrage
comme l'union de ce dossier. Ajouter un ordinateur, c'est deposer un fichier ; en retirer un, c'est
en supprimer un.

```
# autoriser une machine
cp ~/cle.pub /DATA/AppData/codelab/config/ssh/authorized_keys.d/portable.pub
docker restart codelab-dev

# lui retirer l'acces
rm /DATA/AppData/codelab/config/ssh/authorized_keys.d/portable.pub
docker restart codelab-dev

# voir les machines autorisees
ls /DATA/AppData/codelab/config/ssh/authorized_keys.d/
```

Une consequence utile :

- **Ecrire directement dans `authorized_keys` continue de marcher.** Une ligne ajoutee a la main est
  recuperee dans `authorized_keys.d/manuel.pub` au demarrage suivant, puis reprise dans le fichier
  derive -- elle n'est pas ecrasee par la reconstruction.

L'empreinte du serveur, elle, ne change pas : les cles hote sont generees une seule fois dans
`config/ssh/host_keys/`. Elles ne sont regenerees que si une cle est **illisible**, pas seulement
absente -- un fichier tronque par un arret brutal ferait echouer `sshd` et repartir sur une nouvelle
identite, donc sur le `REMOTE HOST IDENTIFICATION HAS CHANGED` cote client.

## Workspace partage entre les services

[Workspace partage entre les services](#workspace-partage-entre-les-services)

`/workspace` est ecrit par trois services aux identites differentes : les sessions SSH en `vscode`
(uid 1000), Dagster et app-manager en `root`. Sans precaution, un fichier produit par un job Dagster
sort en `root:root 0644` et n'est plus modifiable depuis VS Code -- et l'inverse est vrai aussi.

Trois mecanismes, poses automatiquement au demarrage, garantissent l'ecriture partagee :

| Mecanisme                        | Role                                                     |
| -------------------------------- | -------------------------------------------------------- |
| Groupe `codelab` (**gid 2000**)  | present dans les 3 images, sous le meme numero            |
| `setgid` sur `/workspace` (2775) | tout fichier cree herite du groupe du dossier parent      |
| `umask 002`                      | ce groupe herite aussi du droit d'ecriture, pas juste du nom |

Les trois sont necessaires : le setgid seul donne le bon groupe en lecture seule, l'umask seul ne
change pas le groupe. Des ACL par defaut (`setfacl -d`) viennent en filet supplementaire quand le
systeme de fichiers les supporte.

Le tout est applique par l'entrypoint de `codelab-dev`, `codelab-dagster` et `codelab-app-manager`.
La passe recursive sur les fichiers deja presents n'est faite qu'une fois, tracee par
`/workspace/.codelab/permissions-v1` -- **supprimer ce marqueur force une reapplication complete** au
prochain redemarrage, ce qui est la reparation a tenter en premier si un fichier resiste.

## Sortir de chez toi : mettre du TLS devant CodeLab

Tant que CodeLab reste sur ton reseau, le HTTP en clair passe. Des qu'il en
sort, il faut du TLS -- et c'est aussi ce qui debloque les cles d'acces et le
partage d'une application.

Le panneau affiche trois lignes rouges dans *Parametres > Serveur* tant que la connexion n'est pas
chiffree :

| Ligne | Ce qu'elle dit |
|---|---|
| **Connexion chiffree (HTTPS)** | Le panneau est servi en clair. Ton mot de passe, ton code a six chiffres et ton cookie de session traversent le reseau lisibles par qui les intercepte. |
| **Cookie de session en Secure** | Le cookie n'est pas marque `Secure`, donc le navigateur accepterait de l'envoyer en clair. |
| **Adresse reelle des visiteurs** | Derriere un proxy, toutes les requetes semblent venir du proxy : la limite de tentatives compte alors pour un seul visiteur, et le journal des acces note une seule adresse. |

**Elles se resolvent dans cet ordre, et le premier point fait le gros du travail** : les deux autres
sont une variable chacun, a poser une fois le TLS en place. Les poser avant couperait la connexion
en cours — un cookie `Secure` n'est plus envoye en clair, donc la session tombe au rechargement.

> Les **cles d'acces (passkeys)** dependent aussi de cette etape : le navigateur refuse WebAuthn hors
> HTTPS, et exige un **nom de domaine** (pas une adresse IP). Le TLS les debloque.


### Etape 1 — mettre du TLS devant le panneau

Trois routes. Elles se valent techniquement ; ce qui les separe, c'est ce que tu possedes deja.

#### Route A — Cloudflare Tunnel (aucun port a ouvrir)

La plus simple pour une machine a la maison, et **la seule qui marche derriere un CGNAT** (quand ton
operateur ne te donne pas d'adresse publique a toi). Un conteneur ouvre une connexion **sortante**
vers Cloudflare ; le trafic revient par la. Rien a ouvrir sur la box, TLS et nom de domaine fournis.

1. Un compte Cloudflare, un domaine delegue chez eux (les leurs sont gratuits sur `.workers.dev`,
   mais pour un nom a toi il faut un domaine que tu possedes).
2. Zero Trust → Networks → Tunnels → *Create a tunnel* → note le jeton.
3. Ajoute le service au `docker-compose.yml` :

```yaml
  codelab-tunnel:
    image: cloudflare/cloudflared:latest
    container_name: codelab-tunnel
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: colle-ici-le-jeton
    networks:
      - codelab
```

4. Dans la console Cloudflare, route le nom de domaine vers `http://codelab-app-manager:9001`
   (le nom du conteneur, puisque le tunnel est sur le meme reseau).

Ce que ca donne : `https://codelab.tondomaine.fr` en TLS valide, sans ouvrir un seul port.

**Attention** : le tunnel expose le panneau a tout internet. Active la double authentification (ou une
cle d'acces) **avant**, et pense a l'acces Zero Trust de Cloudflare si tu veux une porte de plus.

#### Route B — Caddy sur la ZimaBlade + ton domaine

Si tu peux ouvrir les ports 80 et 443 de ta box vers la ZimaBlade, et que ton domaine pointe vers ton
adresse publique (avec un DNS dynamique si elle change).

```yaml
  codelab-tls:
    image: caddy:2-alpine
    container_name: codelab-tls
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /DATA/AppData/codelab/caddy:/data
      - /DATA/AppData/codelab/Caddyfile:/etc/caddy/Caddyfile:ro
    networks:
      - codelab
```

`Caddyfile` (deux lignes, le certificat Let's Encrypt est obtenu et renouvele tout seul) :

```
codelab.tondomaine.fr {
    reverse_proxy codelab-app-manager:9001
}
```

#### Route C — un VPS devant, la ZimaBlade derriere

Le VPS porte le nom de domaine et le certificat, et relaie vers la maison par un tunnel WireGuard.
Utile quand la box ne peut pas ouvrir de ports, ou quand tu veux que l'adresse publique soit celle du
VPS.

**Les fichiers sont prets dans [`app-manager/vps/`](app-manager/vps/)** : les deux configurations
WireGuard, la configuration nginx du site, et la marche a suivre dans l'ordre. Extrait de
`app-manager/vps/nginx/codelab.conf` :

```nginx
server {
    listen 443 ssl;
    server_name codelab.tondomaine.fr;
    # certificat obtenu par certbot
    ssl_certificate     /etc/letsencrypt/live/codelab.tondomaine.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/codelab.tondomaine.fr/privkey.pem;

    location / {
        proxy_pass http://10.8.0.2:9001;   # la ZimaBlade, au bout du WireGuard
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For   $remote_addr;
        # Les journaux en direct sont du Server-Sent Events : sans ces deux
        # lignes, nginx tamponne et le journal n'avance qu'a la fermeture.
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
```

**Les trois en-tetes comptent** : `Host` (sinon le panneau ne connait pas son propre nom, et les cles
d'acces se lient au mauvais domaine), `X-Forwarded-Proto` (sinon le panneau se croit en clair) et
`X-Forwarded-For` (sinon tous les visiteurs partagent une adresse).


### Etape 2 — le dire au panneau

Une fois le TLS en place et verifie dans un navigateur, **decommente** les trois lignes deja
preparees dans `docker-compose.yml`, sous `codelab-app-manager` → `environment` :

```yaml
      # Marque le cookie de session "Secure" : le navigateur ne l'enverra plus
      # jamais en clair. A ne poser QU'UNE FOIS le TLS en place, sinon la
      # session tombe au premier rechargement.
      APP_MANAGER_HTTPS: "1"
      # Croit X-Forwarded-For : la limite de tentatives et le journal des acces
      # retrouvent l'adresse reelle des visiteurs. A ne poser QUE derriere un
      # proxy qui reecrit vraiment cet en-tete -- publie en direct, n'importe
      # quel client peut le poser et contourner la limite.
      APP_MANAGER_TRUST_PROXY: "1"
      # L'adresse publique du serveur. Sans elle, le panneau ne propose pas de
      # rendre une application publique.
      APP_MANAGER_PUBLIC_URL: "https://codelab.tondomaine.fr"
```

Puis :

```bash
docker compose up -d codelab-app-manager
```

Les trois lignes de *Parametres > Serveur* passent au vert, et le bouton « Rendre publique »
reapparait sur les fiches d'application.

### Etape 3 — verifier

1. Ouvre `https://codelab.tondomaine.fr` : cadenas, pas d'avertissement.
2. *Parametres > Serveur* : les trois lignes en vert, l'adresse publique declaree.
3. *Utilisateurs* : ton adresse IP reelle apparait dans les connexions recentes (pas celle du proxy).
4. *Parametres > Securite* : le bouton « Ajouter une cle » est actif — enregistres-en une.
5. Deconnecte-toi, puis reconnecte-toi avec la cle.

### Ce que ca ne couvre pas

- **Les applications deployees** sont servies par le meme proxy, donc elles heritent du TLS. Une
  application **publique** devient alors accessible a tout internet : c'est le but, mais c'est aussi
  la raison pour laquelle CodeLab ne le propose pas tant que l'adresse publique n'est pas declaree.
- **Dagster** (port 3000) n'est pas derriere ce proxy. Si tu l'exposes aussi, ajoute-lui une entree
  dans la meme configuration, en gardant `codelab-dagster-proxy` devant lui.
- **La meme origine.** Toutes les applications sont servies sous `https://codelab.tondomaine.fr/<nom>/`.
  Une faille XSS dans une application reste une faille dans l'origine du panneau. Un sous-domaine par
  application y remedierait, au prix d'un certificat generique et d'une entree DNS par projet.

## Ce qu'une application peut voir des autres

Chaque application deployee tourne **sous son propre utilisateur** et dans **sa propre vue du
systeme de fichiers**. Concretement, une application qui demarre voit ceci :

| | Sans isolation | Avec (defaut) |
|---|---|---|
| `/workspace` | tous les projets | **le sien seulement** |
| le `.env` d'un projet voisin | lisible | **illisible** |
| `/tmp` | partage | **prive** |
| les process d'une autre application | visibles, non tuables | visibles, non tuables |
| l'environnement d'une autre application | illisible | illisible |

L'isolation ne coute **aucune capability** : elle passe par un namespace utilisateur, qui s'ouvre
sans privilege, et non par un namespace de montage direct qui exigerait `CAP_SYS_ADMIN` -- celle-la
meme que le compose retire. Les deux durcissements ne se contredisent pas.

**Ce qu'elle coute, en revanche** : dans son namespace, l'application se voit `uid 0`. Elle ne gagne
aucun pouvoir dehors -- les fichiers des autres lui apparaissent comme appartenant a `nobody` -- mais
les namespaces utilisateur ont un historique de failles d'evasion du noyau. On echange « une
application lit les fichiers d'une autre » contre « une application touche une surface noyau plus
large ». Sur un serveur ou les projets ne communiquent pas entre eux, l'echange est bon.

Ce qui **ne change pas** : ce qu'un build ecrit arrive bien sur le disque, et reste modifiable depuis
une session SSH -- c'est le point qu'il ne fallait surtout pas casser.

Pour couper l'isolation, si une application a besoin de voir autre chose :

```bash
# globalement, dans docker-compose.yml
APP_MANAGER_ISOLER: "0"

# ou pour une seule application, dans apps.json
"mon-projet": { "isolation": false, ... }
```

Sans `unshare` -- une image reconstruite ailleurs, un noyau ou les namespaces utilisateur sont
desactives -- l'application demarre quand meme, sans isolation. Une application qui tourne moins
protegee vaut mieux qu'une application qui ne tourne pas.

## Limiter les ports au reseau local

Les ports `9001`, `9002`, `3000` et `2222` sont publies sur l'hote : c'est ce qui permet d'ouvrir le
panneau depuis un autre poste, et c'est voulu. Le risque n'est pas la : c'est le jour ou la machine
gagne une interface a laquelle personne ne pense -- un VPN, un Wi-Fi invite, une regle uPnP posee par
la box. Le port suit, sans que rien ne le dise.

```bash
sudo outils/codelab-pare-feu poser 192.168.1.0/24   # ton reseau local
outils/codelab-pare-feu verifier
sudo outils/codelab-pare-feu retirer                # revenir en arriere
```

**Le piege qu'il faut connaitre** : les ports publies par Docker **ne passent pas par la chaine
INPUT**. Docker installe ses propres regles de redirection, et le trafic traverse `FORWARD`. Un
pare-feu ecrit comme d'habitude -- `ufw`, une regle `INPUT`, `nft` sur le hook input -- ne filtre donc
**rien** de CodeLab, tout en donnant l'impression du contraire. C'est une protection qui rassure sans
proteger. Le bon endroit est la chaine `DOCKER-USER`, que Docker traverse avant ses propres regles et
n'ecrase jamais : c'est la que cet outil ecrit.

**Ce qu'il ne touche pas** : le port 22 de l'hote. Il ne passe pas par Docker, et une erreur dessus
t'enfermerait dehors. Si tu veux le restreindre aussi, fais-le a la main, avec une session ouverte a
cote.

Les regles ne survivent pas a un redemarrage de l'hote -- `iptables-persistent`, ou une unite systemd
qui rejoue la commande. L'outil le rappelle a chaque pose.

## Sauvegarder, et verifier la sauvegarde

Une sauvegarde de CodeLab contient `credentials.env` : le mot de passe Postgres, celui du panneau,
la cle de signature des sessions et le secret du second facteur. Plus les cles hote SSH et le fichier
des comptes. **En clair sur un disque externe ou chez un hebergeur, elle vaut la machine entiere** --
et elle est plus facile a voler que la machine. Elle est donc chiffree.

L'outil vit dans `outils/` et se lance **sur l'hote**, pas dans un conteneur : il a besoin des
volumes et de Docker.

```bash
# creer une archive chiffree (demande une phrase de passe)
outils/codelab-sauvegarde creer /mnt/disque-externe

# la verifier -- restauration a blanc dans un dossier jetable
outils/codelab-sauvegarde verifier /mnt/disque-externe/codelab-20260910-121015.tar.gz.enc

# la remettre en place (demande confirmation, ecrase les donnees)
outils/codelab-sauvegarde restaurer /mnt/disque-externe/codelab-...enc
```

Ce qui est sauvegarde : `config/` (les secrets, irremplacables), `app-manager/` (comptes, cles
d'acces, registre, journal), `dagster/`, `workspace/` (ton code) et **toutes les bases** dans un seul
dump SQL. Ce qui ne l'est pas : les images Docker et les `node_modules`, qui se retelechargent -- une
sauvegarde trop grosse est une sauvegarde qu'on ne lance plus.

**`verifier` n'est pas optionnel.** Une sauvegarde jamais restauree n'est pas une sauvegarde : la
commande dechiffre pour de vrai, extrait dans un dossier jetable et controle que le dump et les
secrets sont bien la. A lancer le jour ou tu crees l'archive, pas le jour ou tu en as besoin.

La phrase de passe ne passe jamais par la ligne de commande -- elle serait lisible dans `ps` et
resterait dans l'historique du shell. Saisie interactive, ou variable `CODELAB_PASSPHRASE` pour une
tache planifiee.

## Auditer une instance en marche

Un audit qui lit le code ne voit pas la configuration reelle : une variable oubliee, un port publie
par erreur, une garde active en developpement et pas en service. Celui-ci parle a la machine.

```bash
outils/codelab-audit-dynamique https://codelab.tondomaine.fr --mot-de-passe "$(cat mdp)"
```

Il verifie que les API refusent les visiteurs, que le cookie porte bien `HttpOnly`, `Secure` et
`SameSite`, qu'une ecriture sans jeton est refusee, que l'explorateur ne sort pas de `/workspace`,
que le panneau ne repond pas sur l'origine des applications, et que le mot de passe n'est pas
essayable a l'infini. Il sort en erreur s'il reste un point rouge, donc il se met dans une tache
planifiee.

Deux choses a savoir : la derniere sonde **epuise volontairement le compteur d'essais**, donc les
connexions sont refusees quelques minutes ensuite ; et pour auditer une instance en `http`, il faut
lui retirer `APP_MANAGER_HTTPS` -- sinon le cookie `Secure` n'est pas renvoye, la session n'est pas
portee, et l'outil le dit au lieu de conclure a tort.

**Il ne remplace pas un pentest par un humain** : il verifie ce qu'on sait deja devoir verifier, pas
ce a quoi personne n'a pense.

## Persistance des donnees

Tout vit sous `/DATA/AppData/codelab/` sur le disque de l'hote (aucun volume Docker nomme) : ca survit a un
redemarrage, une recreation de conteneur et une reinstallation.

```
/DATA/AppData/codelab/
├── config/
│   ├── credentials.env        tous les secrets, et rien d'autre
│   └── ssh/
│       ├── authorized_keys    cles autorisees a ouvrir une session
│       └── host_keys/         identite du serveur (empreinte stable)
├── workspace/                 ton code, partage par les 4 services
├── postgres/                  donnees de la base
├── dagster/                   etat Dagster
└── app-manager/               apps.json + logs des applications
```

Seule exception a connaitre : si ton interface de gestion propose, a la desinstallation, de supprimer aussi
les donnees de l'application, il faut refuser pour les conserver. En ligne de commande, `docker compose down`
n'y touche pas (ce sont des bind mounts, pas des volumes geres par Docker).

## Migration depuis une version anterieure

Les secrets se migrent **tout seuls** au premier demarrage : `config/postgres_password`,
`app-manager/admin_password` et `app-manager/flask_secret_key` sont repris dans `credentials.env` puis
supprimes. La base de donnees et le mot de passe du panneau continuent de fonctionner, rien a faire.

Les cles SSH demandent **une commande**, parce que leurs anciens dossiers ne sont plus montes par le compose et
qu'un conteneur ne peut donc plus les lire. A executer sur l'hote avant de redemarrer la stack, pour
conserver l'empreinte du serveur et les cles deja autorisees :

```bash
cd /DATA/AppData/codelab
sudo mkdir -p config/ssh
sudo mv dev-host-keys config/ssh/host_keys
sudo mv dev-ssh/authorized_keys config/ssh/
sudo rmdir dev-ssh
```

Le dossier `dagster-home` a par ailleurs ete renomme en `dagster`, pour s'aligner sur les autres (`postgres`,
`app-manager`, `workspace`) qui portent tous le nom de leur service. A renommer avant de relancer la stack,
sinon Dagster repart d'un `DAGSTER_HOME` vide et reinitialise sa configuration :

```bash
sudo mv /DATA/AppData/codelab/dagster-home /DATA/AppData/codelab/dagster
```

Sans cette etape, de nouvelles cles hote sont generees — l'empreinte du serveur change
(`ssh-keygen -R "[<IP-du-serveur>]:2222"` cote client) et **les cles autorisees sont perdues** : il faut
redeposer un `.pub` dans `authorized_keys.d/` pour retrouver l'acces SSH.

## Tests

```bash
pip install flask psutil requests pytest
python -m pytest app-manager/tests -q
```

Portee volontairement etroite : ce qui se verifie sans conteneur, sans Postgres et sans reseau — detection
de stack a l'ajout d'un projet, bornage des chemins a `/workspace`, attribution des ports,
authentification du panneau et sa limite de tentatives. Le cycle de vie des process et le reverse proxy
demandent une stack en marche et se verifient a la main.

## Publication des images (CI)

`.github/workflows/build-images.yml` lance les tests, puis build et pousse les images vers
`ghcr.io/lucasrtn/...` a chaque push sur `main`. Sur une **pull request**, les tests tournent et les quatre
images sont construites en `amd64` seul, sans rien publier : un Dockerfile casse se voit avant la fusion,
pas apres. **Etape unique a faire a la main** apres le premier run : les packages GitHub sont crees
prives par defaut, donc un `docker pull` anonyme echoue tant qu'ils ne sont pas passes en **Public**
(`github.com/lucasrtn?tab=packages` → package → Package settings → Danger Zone → Change visibility).

## Versionner CodeLab (figer une release)

`latest` bouge a chaque push sur `main` — pratique en developpement, risque en production si un changement
casse quelque chose (comme observe pendant ce projet). CodeLab se versionne **comme un tout** : dev, dagster et
app-manager sortent toujours ensemble, sous un seul numero — pas de version separee par service.

```bash
git tag v1.0.0
git push origin v1.0.0
```

Ce tag declenche le workflow, qui reconstruit et publie **les 3 images en meme temps**, toutes avec ce numero :
`ghcr.io/lucasrtn/codelab-dev:1.0.0`, `codelab-dagster:1.0.0`, `codelab-app-manager:1.0.0`. `latest` n'est pas
touche.

**Sur GitHub (sans ligne de commande)** : Releases → Create a new release → tag `v1.0.0` (target `main`) →
Publish release. Gabarit a reprendre :

- **Titre** : `CodeLab v1.0.0`
- **Description** :
  ```
  Version complete de CodeLab : dev, dagster et app-manager.
  Images : ghcr.io/lucasrtn/codelab-{dev,dagster,app-manager}:1.0.0

  Changements :
  - ...
  ```

**Revenir a cette version precise** : dans `docker-compose.yml`, remplacer le tag `:latest` par `:1.0.0` sur les
**3 services** (`codelab-dev`, `codelab-dagster`, `codelab-app-manager`), puis `docker compose up -d`.

**Lister les versions disponibles** :
```bash
git tag -l "v*"
```
ou visuellement sur `github.com/lucasrtn/codelab/releases`, ou sur `github.com/lucasrtn?tab=packages` pour
chaque image individuellement.

## Icone de l'application

L'icone ne sert qu'aux interfaces d'installation qui affichent une vignette, et elle vit dans
`docker-compose-casaos.yml` uniquement. Elle y est **encodee en base64 directement dans le fichier** plutot
que referencee par URL : le depot etant prive, un lien `raw.githubusercontent.com/.../icon.png` demanderait
une authentification que ces interfaces n'ont pas. Aucune requete externe n'est donc necessaire pour
l'afficher. Le fichier source reste `icon.svg`/`icon.png` a la racine du depot, a re-encoder si tu la
changes :

```bash
python3 -c "import base64; print('data:image/png;base64,' + base64.b64encode(open('icon.png','rb').read()).decode())"
```

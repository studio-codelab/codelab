# CodeLab

Environnement de developpement personnel, auto-heberge sur [ZimaOS](https://www.zimaspace.com/zimaos) (compatible CasaOS).
Une seule installation fournit une base de donnees partagee, un acces SSH/VS Code, un orchestrateur de jobs et un
gestionnaire d'applications — installable en un import de `docker-compose.yml` depuis l'interface ZimaOS, sans
commande a executer sur le serveur.

## Services

| Service | Role | Port |
|---|---|---|
| **Postgres** | Serveur de bases partage : une base par projet, plus la base `dagster` | interne uniquement |
| **Dev** | Acces SSH + VS Code Remote-SSH, avec un utilisateur dedie | `2222` |
| **Dagster** | Orchestration et planification de jobs (interface web + daemon) | `3000` |
| **App-manager** | Deploiement et supervision des applications que tu developpes | `9001` |

Zero configuration manuelle apres l'installation : mot de passe de base de donnees genere automatiquement, cles
SSH generees et persistees, et connexion a Postgres deja prete dans l'environnement du conteneur `dev`.

## Installation

1. Dans ZimaOS : **App Store → Install via docker-compose**, coller le contenu de `docker-compose.yml`.
2. ZimaOS demande une valeur pour **`SSH_PUBLIC_KEY`** : ta cle publique SSH (contenu de
   `~/.ssh/id_ed25519.pub` ou equivalent).
3. Le meme ecran liste les volumes avec leurs chemins par defaut
   (`/DATA/AppData/codelab/workspace` et `/DATA/AppData/codelab/postgres`) — modifiables directement la si tu
   veux un autre emplacement.
4. Installer. Mot de passe Postgres, etat interne des services et cles hote SSH sont generes automatiquement au
   premier demarrage.

## Utilisation

**SSH / VS Code**
```bash
ssh vscode@<IP-ZimaOS> -p 2222
```
Ton code vit dans `/workspace`. La connexion Postgres ne demande aucune configuration :
```bash
python3 -c "import psycopg; print(psycopg.connect().execute('SELECT version();').fetchone()[0])"
```

**Les bases** : pas de base fourre-tout. `dagster` contient les tables d'instance de Dagster (runs,
evenements, planifications) ; chaque projet du workspace a la sienne, nommee comme son dossier
(`diagnostic` pour celui livre en modele), avec un schema `dagster` dedans qui recoit les tables des
assets. Une session SSH arrive directement dans `diagnostic` ; la base d'un projet cree apres coup
s'ajoute avec `codelab-project mon-projet`. Details dans `workspace/README.md`.

**App-manager** : `http://<IP-ZimaOS>:9001/` — demarrer/arreter tes apps deployees depuis `/workspace`, consulter
leurs logs. Protege par mot de passe, genere automatiquement au premier demarrage (voir ci-dessous pour le
recuperer).

**Dagster** : `http://<IP-ZimaOS>:3000/` — charge `/workspace/definitions.py` comme code Dagster.

## Un seul fichier de secrets

Mot de passe Postgres, mot de passe admin app-manager, cle de session : **tout est dans `credentials.env`, et
nulle part ailleurs.** Aucun fichier mono-secret a cote, rien a aller chercher dans un conteneur :
```bash
cat /DATA/AppData/codelab/config/credentials.env
```
Chaque service ecrit son propre bloc au demarrage (delimite par `# ===== <service> =====`, avec le commentaire
qui explique a quoi sert chaque valeur) et lit celui des autres. `codelab-postgres` genere le mot de passe et
l'ecrit la ; `codelab-dev` et les deux services Dagster le relisent depuis ce fichier ; `app-manager` y depose
son mot de passe admin et sa cle de session. Une valeur deja presente n'est jamais regeneree par-dessus.

Sauvegarder ce fichier (et `config/ssh/`) suffit a sauvegarder tous les acces.

## Acces SSH depuis plusieurs ordinateurs

[#acces-ssh-plusieurs-ordinateurs](#acces-ssh-plusieurs-ordinateurs)

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

Deux consequences utiles :

- **`SSH_PUBLIC_KEY` devient facultative.** Elle ne sert qu'a enregistrer une premiere cle sur une
  installation neuve. Ensuite, reimporter le compose en laissant le champ vide ne coupe plus rien.
- **Ecrire directement dans `authorized_keys` continue de marcher.** Une ligne ajoutee a la main est
  recuperee dans `authorized_keys.d/manuel.pub` au demarrage suivant, puis reprise dans le fichier
  derive -- elle n'est pas ecrasee par la reconstruction.

L'empreinte du serveur, elle, ne change pas : les cles hote sont generees une seule fois dans
`config/ssh/host_keys/`. Elles ne sont regenerees que si une cle est **illisible**, pas seulement
absente -- un fichier tronque par un arret brutal ferait echouer `sshd` et repartir sur une nouvelle
identite, donc sur le `REMOTE HOST IDENTIFICATION HAS CHANGED` cote client.

## Contenu par defaut du workspace

[#contenu-par-defaut-du-workspace](#contenu-par-defaut-du-workspace)

Au tout premier demarrage, `codelab-dagster` depose un squelette dans `/workspace` :

```
/workspace/
├── README.md            <- conventions : anatomie d'un projet, secrets, base, dependances
├── definitions.py       <- agregateur Dagster : decouvre les projets, ne pas modifier
└── diagnostic/          <- projet modele ET outil de diagnostic de la stack
```

Un projet est **un dossier**. S'il contient un `definitions.py` exposant une variable `defs`,
Dagster le decouvre tout seul au rechargement du code -- il n'y a aucun fichier central a editer
pour declarer un nouveau projet. Un projet qui ne se charge pas est ignore avec un message dans les
logs, sans rendre les autres invisibles.

`diagnostic/` sert de modele : il montre sur un cas fonctionnel un asset Dagster, une application
web, un module partage entre les deux, la lecture de `credentials.env` et l'ecriture en base. Le
copier est le moyen le plus rapide de demarrer (`cp -r /workspace/diagnostic /workspace/mon-projet`).

**Rien n'est jamais ecrase.** Un fichier deja present sous le meme nom est copie a cote en
`.exemple`, a fusionner a la main. La copie n'a lieu qu'une fois, tracee par
`/workspace/.codelab/workspace-v1` : un projet supprime ne reapparait pas au redemarrage.

## Workspace partage entre les services

[#workspace-partage](#workspace-partage)

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

## Persistance des donnees

Tout vit sous `/DATA/AppData/codelab/` sur le disque du ZimaOS (aucun volume Docker nomme) : ca survit a un
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

Seule exception a connaitre : si l'ecran de desinstallation ZimaOS propose de supprimer les donnees de
l'application, il faut decocher cette case pour les conserver.

## Migration depuis une version anterieure

Les secrets se migrent **tout seuls** au premier demarrage : `config/postgres_password`,
`app-manager/admin_password` et `app-manager/flask_secret_key` sont repris dans `credentials.env` puis
supprimes. La base de donnees et le mot de passe du panneau continuent de fonctionner, rien a faire.

Les cles SSH demandent **une commande**, parce que leurs anciens dossiers ne sont plus montes par le compose et
qu'un conteneur ne peut donc plus les lire. A executer sur le ZimaOS avant de reimporter le compose, pour
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

Sans cette etape, tout fonctionne quand meme : de nouvelles cles hote sont generees et `SSH_PUBLIC_KEY` est
re-autorisee automatiquement — mais l'empreinte du serveur change (`ssh-keygen -R "[<IP-ZimaOS>]:2222"` cote
client) et les cles ajoutees a la main sont perdues.

## Publication des images (CI)

`.github/workflows/build-images.yml` build et pousse automatiquement les images vers `ghcr.io/lucasrtn/...` a
chaque push sur `main`. **Etape unique a faire a la main** apres le premier run : les packages GitHub sont crees
prives par defaut, donc ZimaOS ne peut pas les tirer tant qu'ils ne sont pas passes en **Public**
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
**3 services** (`codelab-dev`, `codelab-dagster`, `codelab-app-manager`), puis reimporter le compose sur ZimaOS.

**Lister les versions disponibles** :
```bash
git tag -l "v*"
```
ou visuellement sur `github.com/lucasrtn/codelab/releases`, ou sur `github.com/lucasrtn?tab=packages` pour
chaque image individuellement.

## Icone de l'application

Le depot etant prive, `raw.githubusercontent.com/.../icon.png` n'est pas accessible sans authentification —
ZimaOS n'en a pas. L'icone est donc encodee directement dans `docker-compose.yml`
(`x-casaos.icon: "data:image/png;base64,..."`), aucune requete externe n'est necessaire pour l'afficher. Le
fichier source reste `icon.svg`/`icon.png` a la racine du depot, a re-encoder si tu la changes :

```bash
python3 -c "import base64; print('data:image/png;base64,' + base64.b64encode(open('icon.png','rb').read()).decode())"
```

## Organisation du depot

```text
codelab/
├── docker-compose.yml
├── icon.svg / icon.png
├── .github/workflows/build-images.yml
├── workspace/     # squelette depose dans /workspace au premier demarrage
├── dev/           # SSH + VS Code Remote-SSH — voir dev/README.md
├── dagster/       # orchestration de jobs — voir dagster/README.md
└── app-manager/   # deploiement d'applications — voir app-manager/README.md
```

Ce README couvre l'installation et l'usage global. Le fonctionnement interne de chaque service (scripts de
demarrage, variables d'environnement, pieges connus) est documente dans son propre `README.md`.

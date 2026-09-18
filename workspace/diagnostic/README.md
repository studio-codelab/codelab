# Diagnostic CodeLab

Ce projet a deux vies. C'est le **modele de reference** : il montre sur un cas fonctionnel ce a quoi
ressemble un projet CodeLab complet — un asset Dagster, une application web, un module partage entre
les deux, la lecture des secrets, l'ecriture en base, un capteur d'alerte. Pour demarrer un nouveau
projet, le plus rapide est de le copier (`cp -r /workspace/diagnostic /workspace/mon-projet`) et de
vider ce qui ne sert pas.

C'est aussi **l'etat des lieux de l'installation**, et il ne se supprime pas depuis le panneau : sans lui, une stack
n'a plus aucun moyen de se controler elle-meme. Il verifie que les cinq services se parlent, depuis les deux extremites de la chaine : une
application web lancee par **app-manager**, et un asset execute par **Dagster**. Les deux ecrivent dans la
meme table Postgres — voir les deux sources cote a cote est la preuve que tout est relie.

## Deux niveaux de verification

| | Quand | Ce que ca fait |
|---|---|---|
| **Sondes** (page d'accueil) | a chaque affichage | elles REGARDENT : un fichier, une connexion, une variable. Instantanees, sans effet |
| **Verification approfondie** (`/tests`) | a la demande | elles AGISSENT : ecrire en base et relire, traverser le reverse proxy, verifier que le journal enregistre |

## Trois rangs, et un seul reveille quelqu'un

Une sonde ne repond pas par oui ou par non : elle rend un **rang**. C'est ce qui decide si le run
Dagster echoue -- donc si le mail part.

| Rang | Ce que ca veut dire | Ce qui se passe |
|---|---|---|
| **ECHEC** | la stack ne rend plus son service, ou elle est ouverte : secret absent, Postgres muet, conteneur tombe, route d'administration qui repond sans session, disque qui va refuser la prochaine ecriture | le run echoue, le capteur envoie le mail |
| **ALERTE** | degrade, mais ca tourne : isolement indisponible, disque qui se remplit, application declaree qui ne repond plus, reglage d'exposition a poser | journalise en `warning`, **le run reste vert** |
| **SANS OBJET** | la sonde ne peut pas repondre *d'ici* : l'etat du panneau n'est monte que dans `codelab-app-manager` | ni bon ni mauvais, aucune consequence |
| **OK** | verifie, ici, maintenant | |

Le rang n'est pas attache a la sonde mais a ce qu'elle **constate** : « le panneau est injoignable
depuis ce conteneur » est une alerte (absence de preuve), « le panneau repond 200 sans session » est
un echec (preuve). Le plafond de chaque sonde se lit dans `SEVERITE_MAX`, en un seul endroit de
`checks.py` -- et il est applique, pas seulement documente : une sonde de confort ne peut pas faire
tomber un run, meme par inadvertance.

Pourquoi ce reglage : avant, un disque a 87 %, un namespace utilisateur refuse par le noyau de
l'hote et une base de donnees injoignable faisaient exactement la meme chose -- un run rouge et un
mail. Trois alertes sur quatre ne demandaient aucun geste immediat. C'est ainsi qu'on cesse de lire
ses alertes, puis qu'on rate la quatrieme.

La verification approfondie ne modifie **rien** de l'installation : aucune application, aucun compte,
aucun reglage. Ses ecritures vont dans la table du diagnostic ou dans son propre dossier, et les
actions interdites doivent etre REFUSEES -- si l'une passe, c'est le resultat du test.

**La suite de regressions du panneau vit ici aussi**, dans `checks.py`, et la verification
approfondie la lance en dernier. C'est le seul endroit d'ou elle est joignable des deux cotes : par
la CI avant qu'une image ne parte, et depuis l'installation qui tourne.

```bash
python -m pytest workspace/diagnostic/checks.py -q
```

Ces tests ecrivent -- ils enregistrent des applications, creent des comptes, posent des cles
d'acces. **Un filet les en empeche** : avant chaque test, sans exception, tous les chemins d'etat du
panneau sont detournes vers un dossier jetable, et l'on verifie qu'aucun ne pointe encore vers le
vrai. Un test ecrit demain sans precaution ne peut plus rien abimer.

Verifie en faisant tourner la suite entiere avec `APP_MANAGER_STATE` pointant sur de vrais fichiers :
aucun n'a bouge, aucun n'est apparu. Sans le filet, le journal des acces se remplissait de
connexions de test.

## Installation

**Rien a faire.** Le projet est copie dans `/workspace` au premier demarrage de la stack, charge
par Dagster, **et inscrit tout seul dans le panneau** : il y apparait sous le nom `diagnostic`,
demarre, en visibilite *privee*. Ouvre `http://<IP-du-serveur>:9001/diagnostic/` et c'est tout.

L'image `app-manager` ne contient pas de pilote Postgres : la commande de build
(`pip install --target vendor "psycopg[binary]"`) l'installe dans `vendor/`, a cote du code, sans
modifier le conteneur. Elle est lancee automatiquement, une fois, juste avant le premier demarrage
de l'application — quelques dizaines de secondes pendant lesquelles la page n'est pas encore servie.
Si elle echoue (pas de reseau, miroir pip injoignable), l'application demarre quand meme et la ligne
`Postgres (pilote)` affiche `aucun pilote Postgres` : relance le build depuis le menu « ... » du
projet. Cote Dagster il n'y a rien a installer, `psycopg2` est deja la, tire par `dagster-postgres`.

L'inscription n'a lieu **qu'une fois**, et seulement sur un panneau encore vide : un projet supprime
depuis le panneau ne revient pas au redemarrage suivant, et une installation qui a deja des
applications n'est jamais modifiee. Le marqueur est
`/var/lib/codelab/app-manager/diagnostic-inscrit` ; le supprimer autorise une nouvelle inscription.

Si tu preferes l'inscrire a la main (panneau -> **Ajouter un projet**) :

| Champ | Valeur |
|---|---|
| Nom | `diagnostic` |
| Dossier | `/workspace/diagnostic` |
| Commande de lancement | `python3 app.py` |
| Commande de build | `pip install --target vendor "psycopg[binary]"` |

## Structure

```
/workspace/
├── definitions.py            <- agregateur : decouvre les projets, ne pas modifier
├── README.md                 <- conventions communes a tous les projets
└── diagnostic/
    ├── definitions.py        <- cote Dagster : l'asset, son planning, le capteur d'alerte mail
    ├── app.py                <- cote web : l'application lancee par app-manager
    ├── checks.py             <- les sondes, partagees par les deux
    └── README.md
```

C'est la structure type d'un projet CodeLab, decrite en detail dans `/workspace/README.md`. Un
projet est un dossier ; s'il contient un `definitions.py` exposant une variable `defs`, Dagster le
decouvre tout seul. Il n'y a **pas** de fichier central a editer pour declarer un nouveau projet.

## Utilisation

1. Ouvre `http://<IP-du-serveur>:9001/diagnostic/`. Dix-sept verifications s'affichent, et la page ecrit une ligne
   `app-manager` en base a chaque rechargement.
2. Le bandeau reste orange tant que Dagster n'a rien ecrit. **Il passe au vert tout seul dans le
   quart d'heure** : l'asset est planifie toutes les quinze minutes. Pour ne pas attendre, va sur
   `http://<IP-du-serveur>:3000/` (la session du panneau suffit : si tu y es deja connecte, Dagster
   s'ouvre sans rien redemander) et materialise l'asset **`diagnostic_codelab`** a la main.
3. Bandeau vert = chaine complete.

### Le planning

L'asset tourne **toutes les quinze minutes**, sans intervention (`diagnostic_toutes_les_quinze_minutes`,
actif des le chargement du code).

Ce n'est pas un confort : le capteur d'alerte ci-dessous reagit a un run **en echec**. Sans planning,
aucun run ne demarre tout seul, donc aucun ne peut echouer, donc **aucune alerte ne part** — un systeme
d'alerte complet qui n'attend qu'un clic pour servir. Une surveillance qu'il faut declencher ne previent
de rien : on ne la declenche que quand on soupconne deja quelque chose.

Ce qui n'est **pas** planifie, et volontairement : la verification approfondie de `/tests`. Ces tests-la
agissent — ils ecrivent, traversent le proxy, lancent la suite de regressions du panneau. Les jouer
quatre fois par heure remplirait les journaux de traces que personne n'a demandees. Seules les sondes
en lecture seule tournent en boucle.

## Ce que chaque verification prouve

| Verification | Ce qui est teste |
|---|---|
| `credentials.env` | Volume `config` monte, et mot de passe Postgres **disponible** — par le fichier, ou par l'environnement quand le fichier est illisible (la sonde dit par ou) |
| `/workspace` | Volume partage entre `dev`, `dagster` et `app-manager` |
| `Postgres (pilote)` | `psycopg` ou `psycopg2` disponible dans ce conteneur |
| `Postgres` | Reseau + mot de passe du fichier partage + base accessible |
| `codelab-postgres (TCP)` | Resolution DNS du nom de conteneur sur le reseau interne |
| `codelab-dagster` | Le webserver Dagster repond en HTTP depuis un autre conteneur |
| `codelab-dev (SSH)` | `sshd` accepte une connexion (sa banniere est affichee) |
| `codelab-app-manager` | Le panneau repond -- cherche sur `127.0.0.1` puis sur le nom de service, selon le conteneur d'ou l'on regarde |
| `Cles SSH (droits)` | `authorized_keys` est **lisible par l'utilisateur SSH**, pas seulement present (rang 2 : une installation neuve n'a aucune cle, et c'est voulu) |

Puis ce qui doit etre **ferme**, et l'etat des lieux :

| Verification | Ce qui est teste |
|---|---|
| `panneau ferme` | Les routes d'administration exigent une session. Echec **seulement** sur un 200 constate ; injoignable = alerte |
| `origine des applications` | Le panneau ne repond pas sur le port des applications. Meme regle : echec sur preuve, alerte sinon |
| `exposition` | Ce qui doit etre pose quand la stack sort du reseau local (HTTPS, proxy de confiance, adresse publique) |
| `isolation des applications` | Le noyau accepte-t-il de creer un namespace utilisateur ? Rang 2 : l'uid par application tient toujours |
| `provenance des connexions` | Des adresses publiques dans le journal des acces alors que rien n'est publie |
| `applications declarees` | Dossier present, commande resolvable, port a l'ecoute -- application par application |
| `espace disque` | Deux paliers : alerte a 85 % et moins de 5 Go, echec a 95 % et moins de 1 Go |
| `surface exposee` | Applications publiques, administration joignable, comptes sans second facteur |

Les six dernieres lisent l'etat du panneau, qui n'est monte que dans `codelab-app-manager` : depuis
Dagster, elles repondent **SANS OBJET** plutot que d'annoncer un panneau vide. Un faux vert est pire
qu'une case vide -- il fait cesser de chercher.

La derniere merite un mot. `sshd` lit les cles hote en `root`, mais ouvre `authorized_keys` **apres** avoir
pris l'uid de l'utilisateur cible. Un dossier non traversable ou un fichier appartenant a `root` donne un
`Permission denied (publickey)` cote client, strictement identique a celui d'une cle absente. Aucune sonde
reseau ne voit cette panne — celle-ci calcule les droits depuis les metadonnees (et non avec `os.access()`,
qui mentirait puisque le code tourne en `root`) et affiche la commande exacte a lancer.

C'est aussi pour cela qu'elle ne se fie jamais a sa propre capacite a ouvrir le fichier : cote
app-manager elle tourne sous l'uid 1001, et `authorized_keys` appartient a l'uid 1000 en `0600` —
exactement ce qu'on veut. Elle perd alors le comptage des cles, qu'elle remplace par la taille du
fichier (une metadonnee, donc encore lisible) : un fichier vide reste detecte.

### Le secret Postgres n'arrive pas par le meme chemin partout

`credentials.env` est en `0600 root`, et **aucun** des processus qui s'en servent ne tourne en root.
Chaque entrypoint lit donc le fichier avant d'abandonner ses privileges, et passe la valeur par
l'environnement -- sous un nom different selon le service :

| Conteneur | Variable | Pose par |
|---|---|---|
| `codelab-app-manager` | `POSTGRES_PASSWORD` | le panneau, qui la transmet aux applications qu'il lance |
| `codelab-dagster`, `codelab-dagster-daemon` | `DAGSTER_PG_PASSWORD` | l'entrypoint, avant de basculer sur l'uid 1002 |
| `codelab-dev` (session SSH) | `PGPASSWORD` | l'entrypoint, dans le profil du shell |

Un fichier illisible est donc l'etat **normal** de deux conteneurs sur trois. Ce qui compte est que
le secret soit arrive, et la sonde dit par quelle variable.

## En cas d'echec

Rien ne plante : chaque sonde affiche l'exception ou la cause dans la colonne de droite. Cote Dagster,
l'asset echoue explicitement avec la liste des sondes en defaut, detail dans les logs du run -- et
seules les sondes de rang ECHEC le font echouer.

| Symptome | Piste |
|---|---|
| `aucun pilote Postgres` | La commande de build n'a pas ete lancee (dossier `vendor/` absent) |
| `nom introuvable` | Conteneur arrete : le DNS Docker n'inscrit que les conteneurs demarres |
| `connexion refusee` | Le conteneur tourne, mais le service qu'il heberge non : il demarre encore (Dagster met une dizaine de secondes a charger le code) ou il est tombe. `docker logs --tail 50 <conteneur>` |
| `password authentication failed` | Le mot de passe en base ne correspond plus a `credentials.env` |
| `no password supplied` | Aucune des trois variables ci-dessus n'est arrivee dans le conteneur. Cote app-manager : redemarre `codelab-app-manager`, puis l'application. Cote Dagster : `docker logs codelab-dagster \| head -20` dira si l'entrypoint a trouve `POSTGRES_PASSWORD` dans `credentials.env` |
| `credentials.env introuvable` | Volume `config` non monte sur le service |
| `ne peut pas le traverser` | `chmod 755` sur `config/ssh` |
| `illisible par l'uid 1000` | `chown 1000:1000` sur `authorized_keys` |
| `contenu non verifiable depuis ce conteneur` | Pas une panne : la sonde tourne sous l'uid 1001 et le fichier appartient a l'uid 1000. Les droits, eux, ont bien ete verifies |
| `definitions.py absent` | L'agregateur n'est pas a la racine de `/workspace` |

Comparer les deux cotes est souvent plus parlant que chaque sonde prise isolement : une verification qui
passe dans la page web mais echoue dans l'asset Dagster (ou l'inverse) designe un volume mal monte ou un
service hors du reseau, pas une panne du service lui-meme.

## Alertes par mail sur echec

`definitions.py` contient un capteur Dagster qui envoie un mail a **chaque run en echec**, tous jobs confondus —
pas seulement l'asset de diagnostic. Il est execute par `codelab-dagster-daemon`, qui tourne deja dans la
stack : rien a installer, rien a ajouter au `docker-compose.yml`.

### Configuration

Deux endroits, et un seul contient un secret.

**Les destinataires, en tete de `definitions.py`.** Une adresse de destination n'est pas un secret : la garder dans le
code la rend visible en relecture, suivie par git, et evite de toucher au fichier d'identifiants pour un
changement anodin.

```python
DESTINATAIRES = [
    "moi@example.com",
    # une ligne par adresse
]
```

**Les identifiants SMTP, dans `credentials.env`.** Ce bloc `codelab-alertes` est la configuration
**d'origine** : celle que lit ce capteur, et celle que le panneau garde en repli. Le panneau ne la
reecrit pas — une configuration saisie dans *Parametres > E-mail* va dans un fichier a part, pour
qu'une faute de frappe depuis une page web ne puisse pas supprimer ce qui fonctionne.

A la main, si tu preferes. Le fichier est gere **par bloc** — chaque service ne reecrit que le sien :

```bash
sudo tee -a /DATA/AppData/codelab/config/credentials.env > /dev/null <<'EOF'
# ===== codelab-alertes =====
# Identifiants SMTP, partages par ce capteur et par les alertes du panneau.
# Configuration D'ORIGINE : le panneau ne l'ecrase pas, il la garde en repli.
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_TLS=starttls
SMTP_USER=moi@gmail.com
SMTP_PASSWORD=xxxxxxxxxxxxxxxx
# ===== /codelab-alertes =====
EOF
docker restart codelab-dagster-daemon
```

| Cle | Role |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | Serveur d'envoi. 587 avec STARTTLS, ou 465 avec `SMTP_TLS=ssl` |
| `SMTP_TLS` | `starttls` (defaut), `ssl`, ou `none` pour un relais interne non chiffre |
| `SMTP_USER`, `SMTP_PASSWORD` | Optionnels : un relais interne peut ne pas demander d'authentification |
| `ALERTE_FROM` | Rarement utile. L'expediteur suit `SMTP_USER`, que Gmail impose de toute facon |

Le lien vers le run dans le mail utilise `CODELAB_DAGSTER_URL`, `http://<IP-du-serveur>:3000` par defaut. Pour
un lien cliquable, mets ton IP reelle dans l'`environment:` du service `codelab-dagster-daemon`.

**Gmail** : il faut la validation en deux etapes activee, puis un *mot de passe d'application* de 16
caracteres (myaccount.google.com → Securite → Mots de passe des applications). Ton mot de passe Google
habituel sera refuse. Ce mot de passe dedie est revocable sans toucher au compte.

### Comportement

Le capteur est `default_status=RUNNING` : actif des le chargement du code, sans avoir a l'activer dans
l'interface — on ne s'apercevrait de l'oubli qu'en ratant une alerte.

Il ne fait **jamais** echouer un run. Configuration absente, mot de passe refuse, serveur injoignable : il
journalise et s'arrete la. Une alerte qui ne part pas ne doit pas ajouter une panne a celle qu'elle signale.
Les messages apparaissent dans l'onglet **Sensors** de Dagster et dans `docker logs codelab-dagster-daemon`.

### Verifier que ca marche

Le plus simple est de provoquer un echec reel : arrete `codelab-postgres`, materialise
`diagnostic_codelab` — la sonde Postgres echoue, donc l'asset aussi — et le mail doit partir dans la minute. Le capteur est evalue toutes les 30 secondes par defaut.

```bash
docker logs --tail 30 codelab-dagster-daemon | grep -i alerte
```

## Nettoyage

Rien ne depend de ce projet : tu peux le supprimer. Ni le dossier ni l'inscription dans le panneau
ne reviennent au redemarrage — chacun a son marqueur, et il faut supprimer les deux pour les
retrouver : `/workspace/.codelab/workspace-v1` (le dossier) et
`/var/lib/codelab/app-manager/diagnostic-inscrit` (l'inscription).

```bash
# supprimer le projet dans le panneau, puis depuis codelab-dev :
python3 - <<'EOF'
import sys; sys.path.insert(0, "/workspace/diagnostic")
import checks
c = checks.connect_pg()
with c.cursor() as cur: cur.execute("DROP TABLE IF EXISTS " + checks.TABLE)
c.commit(); c.close(); print("table supprimee")
EOF
rm -rf /workspace/diagnostic
```


## Supervision de codelab-llm / LiteLLM

Diagnostic vérifie maintenant TCP, `/health`, `/v1/models`, l'authentification Bearer,
puis, si une clé diagnostique est configurée, une vraie completion et `/v1/usage`.
Une exception inattendue d'une sonde devient un résultat `ECHEC` au lieu de produire un HTTP 500.

La completion est opt-in car elle consomme une requête OpenRouter. La clé dédiée doit rester dans
`credentials.env` sur le serveur sous `CODELAB_LLM_DIAGNOSTIC_KEY` et ne doit jamais être committée.

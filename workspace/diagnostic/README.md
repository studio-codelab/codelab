# Diagnostic CodeLab

Ce projet a deux vies. C'est le **modele de reference** : il montre sur un cas fonctionnel ce a quoi
ressemble un projet CodeLab complet — un asset Dagster, une application web, un module partage entre
les deux, la lecture des secrets, l'ecriture en base, un capteur d'alerte. Pour demarrer un nouveau
projet, le plus rapide est de le copier (`cp -r /workspace/diagnostic /workspace/mon-projet`) et de
vider ce qui ne sert pas.

C'est aussi un outil : il verifie que les cinq services de la stack se parlent, depuis les deux extremites de la chaine : une
application web lancee par **app-manager**, et un asset execute par **Dagster**. Les deux ecrivent dans la
meme table Postgres — voir les deux sources cote a cote est la preuve que tout est relie.

## Installation

**Rien a faire : ce projet est installe par defaut** dans `/workspace` au premier demarrage de la
stack. Il est deja charge par Dagster et n'attend qu'une chose de toi, cote application web (une
seule fois) :

Panneau `http://<IP-du-serveur>:9001/` -> **Ajouter un projet**

| Champ | Valeur |
|---|---|
| Nom | `diagnostic` |
| Dossier | `/workspace/diagnostic` |
| Commande de lancement | `python3 app.py` |
| Commande de build | `pip install --target vendor "psycopg[binary]"` |

L'image `app-manager` ne contient pas de pilote Postgres : la commande de build l'installe dans
`vendor/`, a cote du code, sans modifier le conteneur. **Lance le build une fois** (menu « … » du
projet -> *Lancer le build*), puis demarre l'application. Cote Dagster il n'y a rien a installer,
`psycopg2` est deja la, tire par `dagster-postgres`.

## Structure

```
/workspace/
├── definitions.py            <- agregateur : decouvre les projets, ne pas modifier
├── README.md                 <- conventions communes a tous les projets
└── diagnostic/
    ├── definitions.py        <- cote Dagster : l'asset + le capteur d'alerte mail
    ├── app.py                <- cote web : l'application lancee par app-manager
    ├── checks.py             <- les sondes, partagees par les deux
    └── README.md
```

C'est la structure type d'un projet CodeLab, decrite en detail dans `/workspace/README.md`. Un
projet est un dossier ; s'il contient un `definitions.py` exposant une variable `defs`, Dagster le
decouvre tout seul. Il n'y a **pas** de fichier central a editer pour declarer un nouveau projet.

## Utilisation

1. Ouvre `http://<IP-du-serveur>:9001/diagnostic/`. Huit verifications s'affichent, et la page ecrit une ligne
   `app-manager` en base a chaque rechargement.
2. Le bandeau reste rouge tant que Dagster n'a rien ecrit. Va sur `http://<IP-du-serveur>:3000/`, materialise
   l'asset **`diagnostic_codelab`**, puis recharge la page.
3. Bandeau vert = chaine complete.

## Ce que chaque verification prouve

| Verification | Ce qui est teste |
|---|---|
| `credentials.env` | Volume `config` monte, secret partage lisible — le fichier unique fonctionne |
| `/workspace` | Volume partage entre `dev`, `dagster` et `app-manager` |
| `Postgres (pilote)` | `psycopg` ou `psycopg2` disponible dans ce conteneur |
| `Postgres` | Reseau + mot de passe du fichier partage + base accessible |
| `codelab-postgres (TCP)` | Resolution DNS du nom de conteneur sur le reseau interne |
| `codelab-dagster` | Le webserver Dagster repond en HTTP depuis un autre conteneur |
| `codelab-dev (SSH)` | `sshd` accepte une connexion (sa banniere est affichee) |
| `Cles SSH (droits)` | `authorized_keys` est **lisible par l'utilisateur SSH**, pas seulement present |

La derniere merite un mot. `sshd` lit les cles hote en `root`, mais ouvre `authorized_keys` **apres** avoir
pris l'uid de l'utilisateur cible. Un dossier non traversable ou un fichier appartenant a `root` donne un
`Permission denied (publickey)` cote client, strictement identique a celui d'une cle absente. Aucune sonde
reseau ne voit cette panne — celle-ci calcule les droits depuis les metadonnees (et non avec `os.access()`,
qui mentirait puisque le code tourne en `root`) et affiche la commande exacte a lancer.

## En cas d'echec

Rien ne plante : chaque sonde affiche l'exception ou la cause dans la colonne de droite. Cote Dagster,
l'asset echoue explicitement avec la liste des sondes en defaut, detail dans les logs du run.

| Symptome | Piste |
|---|---|
| `aucun pilote Postgres` | La commande de build n'a pas ete lancee (dossier `vendor/` absent) |
| `nom introuvable` | Conteneur arrete : le DNS Docker n'inscrit que les conteneurs demarres |
| `password authentication failed` | Le mot de passe en base ne correspond plus a `credentials.env` |
| `credentials.env introuvable` | Volume `config` non monte sur le service |
| `ne peut pas le traverser` | `chmod 755` sur `config/ssh` |
| `illisible par l'uid 1000` | `chown 1000:1000` sur `authorized_keys` |
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

**Les identifiants SMTP, dans `credentials.env`.** Le fichier est gere **par bloc** — chaque service ne
reecrit que le sien — donc un bloc sous un nom qu'aucun service ne connait survit a tous les redemarrages
et a une reinstallation.

```bash
sudo tee -a /DATA/AppData/codelab/config/credentials.env > /dev/null <<'EOF'
# ===== codelab-alertes =====
# Identifiants SMTP pour les alertes Dagster. Bloc ajoute a la main :
# aucun service ne le reecrit.
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

Rien ne depend de ce projet : tu peux le supprimer. Il ne sera pas reinstalle au redemarrage, sauf
si tu supprimes aussi le marqueur `/workspace/.codelab/workspace-v1`.

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

# Diagnostic CodeLab

Ce projet a deux rôles :

- **l'état des lieux de l'installation** : il vérifie que les services se parlent et que ce qui doit
  être fermé l'est ;
- **le projet modèle** : un asset Dagster, une application web, un module partagé entre les deux,
  l'écriture en base, un capteur d'alerte. Pour démarrer un projet, le copier
  (`cp -r /workspace/diagnostic /workspace/mon-projet`) puis retirer ce qui ne sert pas, sans
  oublier de renommer ses assets et son planning, qui doivent être uniques.

C'est une **application par défaut** : elle est inscrite dans le panneau à chaque démarrage, en
visibilité privée. Elle ne se configure pas, ne s'arrête pas et ne se supprime pas. Sans elle,
l'installation n'aurait plus aucun moyen de se contrôler.

## Fichiers

| Fichier | Rôle |
|---|---|
| `checks.py` | les sondes et l'audit des dépendances ; bibliothèque standard seulement, importé par `app.py` et par Dagster |
| `app.py` | la page web, servie sur `http://<serveur>:9002/diagnostic/` |
| `definitions.py` | le côté Dagster : assets, plannings, capteur d'alerte mail |
| `tests/` | la suite de régression de tout CodeLab (CI, et `/tests`) : un fichier par sujet (`test_securite.py`, `test_comptes.py`…), les aides partagées dans `commun.py`, les fixtures et le filet des chemins d'état dans `conftest.py` |
| `.env` | sa configuration (`CODELAB_DB`, `CODELAB_SCHEMA`, `CODELAB_DAGSTER_URL`) ; versionné car il ne porte aucun secret |

Le build installe le pilote Postgres dans `vendor/` : `pip install --target vendor "psycopg[binary]"`.
S'il a échoué, la ligne `Postgres (pilote)` le dit ; relance le build depuis la fiche.

## La page

Elle reprend la forme de CleanMyMac : à gauche, « Analyse intelligente » puis une entrée par
rubrique, avec un point à la couleur de sa pire sonde ; au centre, une seule vue à la fois.
L'analyse montre l'anneau des sondes vertes, le verdict et le bouton rond **Analyser** (la
vérification approfondie) ; une rubrique montre ses sondes et son bouton **Vérifier**. Le fond est
un dégradé de l'accent de `theme.css`, sans aucune couleur écrite en dur. Sur téléphone, la colonne
devient une rangée horizontale qui défile.

## Deux niveaux de vérification

| | Quand | Ce que ça fait |
|---|---|---|
| **Sondes** (page d'accueil, asset Dagster) | à chaque affichage, et toutes les heures dans Dagster | elles **regardent**, sans effet de bord |
| **Vérification approfondie** (`/tests`) | à la demande | elles **agissent** : écrivent puis relisent en base, traversent le proxy, vérifient le journal et l'écriture dans le projet, jouent six pentests, puis lancent la suite de régression |

Les **pentests** attaquent le panneau sans compte, comme un visiteur du réseau : routes
d'administration, en-têtes de sécurité, traversée de chemin, en-tête Host, laissez-passer de
tournage, limitation des essais. Chaque attaque vise une cible inexistante, ou une route qui ne
change rien même si elle passait : une garde cassée se voit à son code de réponse, jamais à un
dégât. Le dernier pentest met l'adresse du diagnostic en pause cinq minutes, ce qui est
justement ce qu'il vérifie. Chaque pentest est joué, dans la suite du dépôt, contre le vrai panneau
(il doit passer) et contre un serveur troué exprès (il doit échouer).

Les deux côtés (application et Dagster) écrivent un battement dans la même table. Les voir tous les
deux prouve que la chaîne est complète. Un écart entre eux désigne un volume mal monté ou un service
hors du réseau.

## Ce qui est vérifié

**Que tout se parle**

- `credentials.env` monté, et mot de passe Postgres arrivé (par le fichier ou par l'environnement) ;
- `/workspace` partagé ;
- pilote Postgres présent ;
- Postgres joignable, et pgvector installé ;
- Redis, Mailpit (SMTP), Dagster, SSH et le panneau répondent ;
- droits de `authorized_keys` lisibles par l'utilisateur SSH.

**Que tout est fermé**

| Sonde | Ce qu'elle vérifie |
|---|---|
| panneau fermé | ses routes d'administration exigent une session |
| origine des applications | le panneau ne répond pas sur le port des applications |
| exposition | ce qui doit être posé quand la stack sort du réseau local |
| isolation | le noyau accepte les namespaces utilisateur |
| provenance | des adresses publiques dans le journal alors que rien n'est publié |

**L'état des lieux**

- chaque application déclarée : dossier, commande, port à l'écoute ;
- espace disque ;
- fraîcheur et restauration d'essai des sauvegardes ;
- surface exposée : applications publiques, comptes sans second facteur.

Les sondes qui lisent l'état du panneau répondent **sans objet** depuis Dagster, où cet état n'est
pas monté.

## Trois rangs

| Rang | Sens | Effet |
|---|---|---|
| **Échec** | service rendu impossible, ou porte ouverte | le run Dagster échoue, un mail part |
| **Alerte** | dégradé mais en marche (disque qui se remplit, isolation indisponible…) | journalisé, le run reste vert |
| **Sans objet** | la sonde ne peut pas répondre d'ici | aucun |

Le plafond de chaque sonde est fixé dans `SEVERITE_MAX` (`checks.py`). Une sonde de confort ne peut
pas faire tomber un run.

## Dagster

| Élément | Quand | Rôle |
|---|---|---|
| `diagnostic_codelab` (job `diagnostic_periodique`) | toutes les heures, à la minute 7 | les sondes, et un battement en base |
| `audit_dependances` (job `audit_dependances_hebdomadaire`) | le lundi à 6 h 23 | versions figées des projets confrontées à OSV ; une vulnérabilité, ou OSV injoignable, fait échouer le run |
| `alerte_mail_echec` | à chaque run en échec, tous projets confondus | envoie un mail |

Le capteur lit le serveur d'envoi dans le bloc `codelab-alertes` de `credentials.env`. Les
destinataires sont dans la liste `DESTINATAIRES`, en tête de `definitions.py`. Si la configuration
manque, il le journalise et n'échoue jamais.

```bash
sudo tee -a /DATA/AppData/codelab/config/credentials.env > /dev/null <<'EOF'
# ===== codelab-alertes =====
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_TLS=starttls
SMTP_USER=moi@gmail.com
SMTP_PASSWORD=motdepassedapplication
# ===== /codelab-alertes =====
EOF
docker restart codelab-dagster
```

Pour un lien cliquable vers le run dans le mail, mets l'adresse réelle dans `CODELAB_DAGSTER_URL`
(`.env`).

**Tester l'alerte** : arrête `codelab-postgres`, puis matérialise `diagnostic_codelab`. Le mail part
dans la minute.

## En cas d'échec

| Message | Piste |
|---|---|
| `aucun pilote Postgres` | build non lancé (`vendor/` absent) |
| `nom introuvable` | conteneur arrêté |
| `connexion refusée` | le service démarre encore ou est tombé : `docker logs --tail 50 <conteneur>` |
| `password authentication failed` | mot de passe en base différent de `credentials.env` |
| `no password supplied` | le secret n'est pas arrivé : redémarre le conteneur concerné |
| `ne peut pas le traverser` / `illisible par l'uid 1000` | `chmod 755 config/ssh` / `chown 1000:1000 authorized_keys` |

## Lancer la suite de régression

```bash
python3 -m pytest workspace/diagnostic/tests -q -rs
```

Avant chaque test, tous les chemins d'état du panneau sont détournés vers un dossier jetable : un
test ne peut rien abîmer de l'installation.

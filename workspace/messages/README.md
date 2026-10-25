# Messages CodeLab

Les messages laissés depuis le hub, et le travail qu'on en tire : boîte de réception, planning,
feuille de route et projets.

C'est une **application par défaut**, comme le Diagnostic :

- son dossier est `/workspace/messages`, recopié depuis l'image s'il manque ;
- le panneau l'inscrit à chaque démarrage, en visibilité privée, réservée à l'administrateur ;
- elle ne se configure pas, ne s'arrête pas, ne se renomme pas et ne se supprime pas ;
- le proxy la sert sur le port des applications (`http://<serveur>:9002/messages/`), avec le ruban
  « ← CodeLab » de toutes les applications.

## Fichiers

| Fichier | Rôle |
|---|---|
| `app.py` | le serveur (Flask) : l'identité, la page, l'API de l'administrateur, le relais du hub, le dialogue avec le panneau |
| `donnees.py` | la base et ses règles : messages, pièces jointes, projets, tâches, suivi, reprise, export |
| `index.html` | la page : boîte de réception, planning, feuille de route, projets |
| `codelab_auth.py` | la vérification du jeton signé du panneau ; copie exacte de `dev/modeles/codelab_auth.py` (un test le vérifie) |
| `icon.svg` | l'icône, servie au hub par le panneau et à la page par l'application |
| `.env` | `CODELAB_DB=messages` ; versionné car il ne porte aucun secret |

Le build installe le pilote Postgres dans `vendor/` : `pip install --target vendor "psycopg[binary]"`.
Lancement : `python3 app.py`, sur `127.0.0.1:$PORT`.

## La base

Une base Postgres à elle, `messages`, avec son compte du même nom. Le panneau la crée et transmet
le compte au lancement (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `CODELAB_DB`). Les tables se créent à
la première requête (`donnees.MIGRATIONS`, on n'ajoute qu'à la fin).

`MESSAGES_SQLITE=<chemin>` remplace Postgres par un fichier SQLite. C'est ce que font les tests,
qui tournent sans Postgres ; un test rejoue les mêmes opérations sur un vrai Postgres quand la
machine en a un.

## Trois portes

Toute requête porte le jeton signé du panneau (`X-CodeLab-Auth`). Sans lui, la réponse est 403.

| Chemin | Qui | Pour quoi |
|---|---|---|
| `/`, `/api/...` | l'administrateur, par le proxy | la page et son API ; ce qui modifie exige aussi l'en-tête `X-Messages: 1` (CSRF) |
| `/compte/...` | un compte, relayé par le panneau | écrire un message, déposer et relire ses pièces, suivre ses messages |
| `/interne/...` | le panneau seul (rôle `panneau`) | synchroniser, annoncer les nouveaux messages, oublier un compte, reprendre les anciennes données |

Le panneau garde ce que lui seul sait faire :

- vérifier sur quelle application un compte a le droit d'écrire, et le délai d'une minute entre
  deux messages ;
- prévenir l'administrateur par mail et par notification ;
- annoncer à l'auteur l'avancement de son message.

Toutes les minutes, il donne à l'application la liste des applications et reprend le nombre de
messages non lus (la tuile du hub) et l'étape de chaque message (les notifications de suivi).

## La reprise des anciennes données

Avant d'être un projet, Messages gardait ses données dans `codelab.db`, la base du panneau. Au
premier démarrage, le panneau les envoie ici, pièces jointes comprises. Il ne les efface chez lui
qu'après avoir vérifié que chaque identifiant est arrivé. Une coupure au milieu ne perd rien : la
reprise recommence au tour suivant, sans doublon.

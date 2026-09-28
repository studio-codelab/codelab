# codelab-redis

Un serveur Redis partagé par les applications de la stack : cache, sessions, files de tâches,
compteurs, verrous, messages en direct. L'image officielle, plus un entrypoint qui génère le mot
de passe et la configuration.

## S'en servir

Les applications lancées par le panneau reçoivent, par l'environnement :

| Variable | Exemple |
|---|---|
| `REDIS_URL` | `redis://default:<mot de passe>@codelab-redis:6379/0` |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | les mêmes valeurs, séparées |

`REDIS_URL` suffit à la plupart des bibliothèques :

```python
import os, redis
r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
r.set("monapp:visites", 0, nx=True)
r.incr("monapp:visites")
```

```js
import { createClient } from "redis";
const r = await createClient({ url: process.env.REDIS_URL }).connect();
```

Depuis une session SSH, `REDIS_URL` est aussi posée, et `redis-cli -h codelab-redis` s'authentifie
tout seul (par `REDISCLI_AUTH`, sans mot de passe sur la ligne de commande).

## Les trois règles

1. **Préfixer ses clés par le nom de l'application** (`monapp:session:42`). Le serveur est partagé
   et toutes les applications ont le même mot de passe : c'est la convention qui les sépare. Même
   raison pour ne jamais lancer `FLUSHDB` ou `FLUSHALL` : cela vide aussi les données des voisines.
2. **Donner une durée de vie au cache** (`SET clé valeur EX 300`). Quand les données atteignent
   leur plafond (192 Mo), Redis n'évince **que les clés qui ont une durée de vie**. Les autres (une
   file de tâches, une session) ne disparaissent jamais en silence : l'écriture est refusée avec
   une erreur `OOM`, visible dans les journaux de l'application.
3. **Ce qui ne doit pas se perdre va dans Postgres.** Redis écrit un journal sur disque chaque
   seconde et survit donc à un redémarrage, mais il n'est pas sauvegardé : les sauvegardes
   quotidiennes ne concernent que Postgres et l'état du panneau.

## Ce qui est fermé

- **Aucun port publié sur l'hôte** : Redis n'est joignable que depuis le réseau de la stack.
- **Un mot de passe**, généré au premier démarrage dans le bloc `codelab-redis` de
  `credentials.env`. Il ne passe jamais sur une ligne de commande.
- **Pas de commandes d'administration** (`CONFIG`, `MONITOR`, `SHUTDOWN`, `REPLICAOF`, `MODULE`,
  `DEBUG`…) : une application n'en a jamais besoin, et `CONFIG SET` est le chemin classique par
  lequel un client Redis écrit un fichier où il veut sur le disque.

## Réglages

La configuration est réécrite à chaque démarrage à partir de variables d'environnement, à poser
dans le service du `docker-compose.yml` :

| Variable | Défaut | Rôle |
|---|---|---|
| `CODELAB_REDIS_MAXMEMORY` | `192mb` | plafond des données ; rester sous la limite du conteneur (320 Mo) |

**Changer le mot de passe** : supprimer le bloc `codelab-redis` de `credentials.env`, redémarrer
`codelab-redis`, puis les applications qui s'en servent.

**Repartir de zéro** : arrêter le service et vider `/DATA/AppData/codelab/redis/`.

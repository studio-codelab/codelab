# codelab-dagster-proxy

Reverse proxy authentifiant devant Dagster, sur le port `3000`.

## Pourquoi ce service existe

Dagster, en edition open source, **n'a aucune authentification**. Son interface permet de lancer un job,
c'est-a-dire d'executer du code sur la machine. Publier son port revenait donc a ouvrir cette porte a tout
le reseau local — Postgres n'est pas expose, `codelab-dev` exige une cle publique, `codelab-app-manager`
demande un mot de passe : Dagster etait le seul maillon nu de la stack.

Le port `3000` de l'hote arrive maintenant sur ce service. Celui de `codelab-dagster` n'est plus publie :
il n'est joignable que depuis le reseau interne de la stack, donc uniquement a travers ce proxy.

## Ce que fait l'image

- `nginx` alpine, avec `apache2-utils` (`htpasswd`) et `openssl`.
- Authentification HTTP Basic : un utilisateur (`codelab` par defaut), un mot de passe genere au premier
  demarrage et ecrit dans `credentials.env`.
- Relais des **websockets** : l'interface de Dagster suit les runs par souscription GraphQL. Sans cela, la
  page s'affiche mais les journaux d'execution restent figes — une panne d'autant plus deroutante que tout
  le reste fonctionne.
- Delais longs (1 h) et `proxy_buffering off` : un run peut rester silencieux longtemps, et ses journaux
  doivent arriver au fil de l'eau.

## Identifiants

Comme partout dans CodeLab, ils vivent dans `credentials.env`, dans un bloc delimite que ce service est
seul a reecrire :

```
# ===== codelab-dagster-proxy =====
DAGSTER_USER=codelab
DAGSTER_PASSWORD=<genere au premier demarrage>
# ===== /codelab-dagster-proxy =====
```

Le mot de passe **n'est jamais regenere** par-dessus une valeur existante : il ne changerait sinon a chaque
redemarrage. Pour le changer, editer la valeur dans `credentials.env` puis redemarrer le conteneur — le
fichier `.htpasswd` est reconstruit a chaque demarrage a partir de cette valeur.

## Fichiers

| Fichier | Role |
|---|---|
| `Dockerfile` | Image (nginx + htpasswd + openssl) |
| `entrypoint.sh` | Genere le mot de passe si besoin, ecrit le bloc, fabrique `.htpasswd` |
| `nginx.conf` | Le proxy lui-meme : authentification, websockets, delais |
| `map-upgrade.conf` | `Connection: upgrade` seulement quand le client le demande |

Les deux fichiers de configuration atterrissent dans `conf.d/`, donc dans le contexte `http` de nginx :
c'est le seul endroit ou une directive `map` est acceptee.

## Limite connue

L'authentification est en **HTTP Basic, sur une connexion en clair** : le mot de passe circule en clair sur
le reseau local. C'est le compromis assume d'un service LAN sans TLS — le meme que pour le panneau
(port `9001`). Cela ferme l'acces a qui passe par la, pas a qui ecoute le trafic.

## Diagnostic

```bash
curl -I http://<IP-du-serveur>:3000/            # 401 attendu : l'authentification est en place
curl -I -u codelab:<mot-de-passe> http://<IP-du-serveur>:3000/   # 200
docker logs codelab-dagster-proxy
```

Un `200` sans identifiants signifierait que l'authentification est tombee. C'est exactement ce que verifie
le `healthcheck` du service, qui attend un `401`.

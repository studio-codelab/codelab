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

- `nginx` alpine, sans paquet supplementaire.
- **Authentification par la session du panneau** : avant chaque requete, nginx interroge
  `codelab-app-manager` (directive `auth_request`). Session valide, la requete passe ; sinon, le visiteur
  est redirige vers la page de connexion du panneau.
- Relais des **websockets** : l'interface de Dagster suit les runs par souscription GraphQL. Sans cela, la
  page s'affiche mais les journaux d'execution restent figes — une panne d'autant plus deroutante que tout
  le reste fonctionne.
- Delais longs (1 h) et `proxy_buffering off` : un run peut rester silencieux longtemps, et ses journaux
  doivent arriver au fil de l'eau.

## Pourquoi la session du panneau, et pas un mot de passe a lui

L'authentification precedente etait en HTTP Basic. Elle fonctionnait, mais :

- **elle n'avait aucune session** — ni expiration, ni deconnexion, et le navigateur renvoyait les
  identifiants a chaque requete jusqu'a sa fermeture complete ;
- **sa fenetre etait celle du navigateur** — impossible a habiller, et l'interface deja chargee restait
  visible derriere pendant qu'elle s'affichait ;
- **elle faisait un deuxieme mot de passe** a retenir pour la meme personne, sans second facteur.

Desormais Dagster herite de la session du panneau : meme mot de passe, meme double authentification si elle
est activee, meme deconnexion. `DAGSTER_USER` et `DAGSTER_PASSWORD` n'existent plus — l'entrypoint remplace
l'ancien bloc de `credentials.env` par une note expliquant ou ils sont passes, plutot que de laisser un vide.

**Le cookie traverse les deux ports** parce que la portee d'un cookie ignore le numero de port : celui pose
sur `:9001` est envoye a `:3000`. Et `SameSite=Lax` laisse passer une navigation de premier niveau, ce
qu'est cette redirection.

**En cas de panne du panneau**, la requete d'autorisation echoue et nginx rend une erreur : l'acces reste
ferme. C'est le bon sens de defaillance — mieux vaut Dagster injoignable qu'ouvert a tous.


## Fichiers

| Fichier | Role |
|---|---|
| `Dockerfile` | Image (nginx seul) |
| `entrypoint.sh` | Remplace l'ancien bloc d'identifiants de `credentials.env` par une note |
| `nginx.conf` | Le proxy lui-meme : authentification, websockets, delais |
| `map-upgrade.conf` | `Connection: upgrade` seulement quand le client le demande |

Les deux fichiers de configuration atterrissent dans `conf.d/`, donc dans le contexte `http` de nginx :
c'est le seul endroit ou une directive `map` est acceptee.

## Le nom d'hote amont passe par une variable

`proxy_pass http://$amont:3000;` plutot que `proxy_pass http://codelab-dagster:3000;`, avec un
`resolver 127.0.0.11` (le DNS interne de Docker). Ce n'est pas un detail de style :

- avec un nom ecrit en clair, nginx resout l'adresse **au chargement de la configuration**, puis la **fige**
  pour la duree de vie du processus. Un `docker restart codelab-dagster` — le geste meme que la
  documentation recommande pour recharger le code — change son adresse, et le proxy continuerait de parler
  dans le vide jusqu'a ce qu'on le redemarre lui aussi ;
- accessoirement, c'est aussi ce qui permet a `nginx -t` de passer au build, la ou `codelab-dagster`
  n'existe pas.

**Contrepartie** : la resolution ne consulte plus `/etc/hosts`, uniquement le `resolver`. Ce service doit
donc tourner sur un reseau Docker **defini par l'utilisateur** — celui du compose en est un — car c'est ce
qui active le DNS interne sur `127.0.0.11`. Sur le reseau `bridge` par defaut, toutes les requetes
ressortiraient en `502`.

## Limite connue

La session circule **sur une connexion en clair** tant qu'aucun TLS n'est en place : le cookie est
interceptable sur le reseau local. C'est le compromis assume d'un service LAN sans TLS — le meme que pour le
panneau (port `9001`), et la meme reponse : `APP_MANAGER_HTTPS=1` cote panneau des qu'un reverse proxy
termine du TLS devant.

## Diagnostic

```bash
curl -I http://<IP-du-serveur>:3000/          # 302 vers /login : l'authentification est en place
curl -I http://<IP-du-serveur>:3000/_sante    # 200 : nginx est debout (sonde du healthcheck)
docker logs codelab-dagster-proxy
```

Un `200` sur `/` sans session signifierait que l'authentification est tombee. Le `healthcheck`, lui, sonde
`/_sante` : une requete sur `/` redirige desormais, et le client du healthcheck suivrait cette redirection
vers un hote qui n'existe pas dans ce conteneur.

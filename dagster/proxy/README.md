# codelab-dagster-proxy

Un nginx qui garde deux interfaces sans authentification propre :

| Port | Interface | Fichier |
|---|---|---|
| `3000` | Dagster | `nginx.conf` |
| `8025` | Mailpit (interface CodeLab, `mailpit-ui/`) | `mailpit.conf` |

**Pourquoi** : l'interface de Dagster lance des jobs, donc exécute du code, et celle de Mailpit
montre tous les mails, liens de réinitialisation compris. Ni Dagster ni Mailpit ne publient de port :
ce proxy est leur seule porte.

## Fonctionnement

- **Avant chaque requête**, nginx demande au panneau `GET /api/auth-check` (`auth_request`). Seule une
  session d'**administration** passe. Sans elle, le visiteur est redirigé vers la connexion du panneau.
  Même mot de passe, même second facteur, même déconnexion.
- **Le cookie du panneau** (`:9001`) est envoyé au `:3000` et au `:8025`, parce que la portée d'un
  cookie ignore le port.
- **Si le panneau ne répond pas**, l'accès reste fermé.
- **Websockets** relayés (suivi des runs Dagster), délais d'une heure et pas de mise en tampon, pour
  que les journaux arrivent au fil de l'eau.
- **Le nom amont passe par une variable** et le resolver Docker (`127.0.0.11`). Un
  `docker restart codelab-dagster` change son adresse IP sans casser le proxy. Le service doit donc
  rester sur un réseau Docker défini par l'utilisateur, comme celui du compose.

## Mailpit

- **Mot de passe interne.** Mailpit exige un mot de passe sur toute son API, pour qu'une application
  du même réseau ne lise pas les mails des autres.
  - Ce mot de passe est généré par l'entrypoint et publié dans le bloc `codelab-proxy` de
    `credentials.env`, jamais transmis aux applications.
  - nginx l'ajoute aux requêtes déjà autorisées par le panneau.
  - Tant qu'il n'existe pas, `:8025` répond `503`.
- **Interface au style de CodeLab** (`mailpit-ui/`), bâtie sur l'API publique de Mailpit :
  - aucun script ni style en ligne ;
  - le texte d'un mail passe par `textContent` ;
  - le HTML d'un mail ne s'affiche que dans un cadre isolé, sans script.

## Fichiers

| Fichier | Rôle |
|---|---|
| `Dockerfile` | nginx alpine |
| `entrypoint.sh` | mot de passe interne de Mailpit ; retrait de l'ancien bloc d'identifiants Dagster |
| `nginx.conf`, `mailpit.conf` | les deux proxys |
| `indisponible.html` | la page servie quand l'amont ne répond pas |
| `mailpit-ui/` | l'interface de Mailpit |

## Vérifier

```bash
curl -I http://<serveur>:3000/          # 302 vers la connexion : l'authentification est en place
curl -I http://<serveur>:3000/_sante    # 200 : nginx est debout (sonde du healthcheck)
```

Un `200` sur `/` sans session voudrait dire que l'authentification est tombée.

## Retiré

Le retrait de l'ancien bloc `codelab-dagster-proxy` de `credentials.env` (l'identifiant HTTP
Basic d'avant la session du panneau). Dernière version qui le portait : le commit `f9caf52`. Une installation qui n'a pas démarré depuis
longtemps passe d'abord par celle-là, puis par la version courante.

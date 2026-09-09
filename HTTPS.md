# Exposer CodeLab en HTTPS

Le panneau affiche trois lignes rouges dans *Configuration > Serveur* tant que la connexion n'est pas
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

---

## Etape 1 — mettre du TLS devant le panneau

Trois routes. Elles se valent techniquement ; ce qui les separe, c'est ce que tu possedes deja.

### Route A — Cloudflare Tunnel (aucun port a ouvrir)

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

### Route B — Caddy sur la ZimaBlade + ton domaine

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

### Route C — un VPS devant, la ZimaBlade derriere

Si tu as deja un VPS : il porte le nom de domaine et le certificat, et relaie vers la maison par un
tunnel WireGuard. Utile quand la box ne peut pas ouvrir de ports, ou quand tu veux que l'adresse
publique soit celle du VPS.

Sur le VPS, `nginx` :

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

---

## Etape 2 — le dire au panneau

Une fois le TLS en place et verifie dans un navigateur, ajoute dans `docker-compose.yml`, sous
`codelab-app-manager` → `environment` :

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

Les trois lignes de *Configuration > Serveur* passent au vert, et le bouton « Rendre publique »
reapparait sur les fiches d'application.

## Etape 3 — verifier

1. Ouvre `https://codelab.tondomaine.fr` : cadenas, pas d'avertissement.
2. *Configuration > Serveur* : les trois lignes en vert, l'adresse publique declaree.
3. *Utilisateurs* : ton adresse IP reelle apparait dans les connexions recentes (pas celle du proxy).
4. *Parametres > Cles d'acces* : le bouton « Ajouter une cle » est actif — enregistres-en une.
5. Deconnecte-toi, puis reconnecte-toi avec la cle.

## Ce que ca ne couvre pas

- **Les applications deployees** sont servies par le meme proxy, donc elles heritent du TLS. Une
  application **publique** devient alors accessible a tout internet : c'est le but, mais c'est aussi
  la raison pour laquelle CodeLab ne le propose pas tant que l'adresse publique n'est pas declaree.
- **Dagster** (port 3000) n'est pas derriere ce proxy. Si tu l'exposes aussi, ajoute-lui une entree
  dans la meme configuration, en gardant `codelab-dagster-proxy` devant lui.
- **La meme origine.** Toutes les applications sont servies sous `https://codelab.tondomaine.fr/<nom>/`.
  Une faille XSS dans une application reste une faille dans l'origine du panneau. Un sous-domaine par
  application y remedierait, au prix d'un certificat generique et d'une entree DNS par projet.

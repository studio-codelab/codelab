# CodeLab derriere un VPS

```
navigateur --HTTPS--> VPS (nginx + certificat) --WireGuard--> ZimaBlade :9001
```

Le VPS porte le nom de domaine et le certificat ; la ZimaBlade ne publie rien sur internet et reste
joignable seulement par le tunnel. Aucun port a ouvrir sur la box : c'est la ZimaBlade qui compose
vers le VPS.

Ce dossier vit dans `app-manager/` parce qu'il n'existe que pour exposer ce panneau — mais
**rien ici ne tourne dans la stack, ni dans l'image** : tout s'installe sur le VPS, sauf un bout de
tunnel pose sur l'hote de la ZimaBlade. **Le VPS n'a pas besoin d'exister pour que CodeLab
fonctionne** : tant qu'aucune adresse publique n'est declaree, le panneau vit tres bien en local — il
ne propose simplement pas de rendre une application publique, et les cles d'acces restent
indisponibles.

| Fichier | Ou il va |
|---|---|
| `wireguard/wg0-vps.conf.exemple` | VPS, `/etc/wireguard/wg0.conf` |
| `wireguard/wg0-zimablade.conf.exemple` | ZimaBlade (l'**hote**, pas un conteneur), `/etc/wireguard/wg0.conf` |
| `nginx/00-codelab-upgrade.conf` | VPS, `/etc/nginx/conf.d/` |
| `nginx/codelab-relais.conf` | VPS, `/etc/nginx/snippets/` |
| `nginx/codelab.conf` | VPS, `/etc/nginx/sites-available/`, puis un lien dans `sites-enabled/` |

## Dans l'ordre

**1. Le tunnel.** Sur chaque machine : `apt install wireguard`, puis
`wg genkey | tee cle.privee | wg pubkey > cle.publique`. Recopie les cles croisees dans les deux
fichiers d'exemple (la publique de l'un va chez l'autre), et `systemctl enable --now wg-quick@wg0`
des deux cotes.

Verifie depuis le VPS — c'est le seul test qui compte a cette etape :

```bash
ping -c3 10.8.0.2
curl -s http://10.8.0.2:9001/health     # doit repondre "ok"
```

**2. Le DNS.** Fais pointer ton domaine (`toi.duckdns.org`) vers l'adresse publique du **VPS**.
Les sous-domaines suivent d'eux-memes : chez DuckDNS, `blog.toi.duckdns.org` mene deja au meme
endroit que `toi.duckdns.org`. Ailleurs, ajoute un enregistrement `*.tondomaine.fr`.

**3. Le certificat.** Un seul, pour le domaine et tous ses sous-domaines (`*.toi.duckdns.org`). Un
certificat « joker » se prouve par le DNS ; avec DuckDNS, ton jeton suffit :

```bash
apt install certbot python3-pip && pip install certbot-dns-duckdns
certbot certonly --authenticator dns-duckdns --dns-duckdns-token <ton-jeton> \
  --dns-duckdns-propagation-seconds 60 -d toi.duckdns.org -d '*.toi.duckdns.org'
```

Le renouvellement est automatique.

**4. nginx.** Recopie les trois fichiers, remplace `exemple.duckdns.org` par ton domaine et
`10.8.0.2` par l'adresse WireGuard de la ZimaBlade si tu as choisi un autre reseau. Puis
`nginx -t && systemctl reload nginx`.

> `nginx -t` est la seule etape ou une faute de frappe se voit tout de suite. Fais-la avant chaque
> `reload`.

**5. Rien a dire a CodeLab.** Ouvre le panneau une fois par ton domaine : il retient cette adresse
(pour les liens d'invitation, et pour permettre de rendre une application publique), et chaque
application est servie a `https://<son-nom>.toi.duckdns.org` — le nom de l'application, celui de
sa fiche. Aucune variable a poser, aucun champ a remplir.

C'est tout : il n'y a plus de case a cocher. La securite reseau est toujours au plus strict :

- **HTTPS** est constate, pas declare : des que la requete arrive en https (annoncee par nginx via
  `X-Forwarded-Proto`), le cookie de session devient `Secure` et HSTS part.
- **`X-Forwarded-For` n'est jamais cru.** Derriere le tunnel, tous les visiteurs partagent donc
  l'adresse du VPS pour la limite de tentatives : plus strict, jamais moins.
- **L'administration ne repond qu'au reseau local, en connexion directe.** Une requete qui passe
  par le VPS (en-tetes `X-Forwarded-*`) n'atteint jamais l'administration, meme si l'adresse du
  tunnel est privee. Les comptes nommes, eux, entrent par le domaine. Pour administrer,
  connecte-toi depuis chez toi, sur l'adresse locale du panneau.

## Verifier

1. `https://toi.duckdns.org` : cadenas, pas d'avertissement.
2. `https://<une-application>.toi.duckdns.org` : l'application, avec le meme cadenas.
3. Un compte nomme se connecte par le domaine ; l'administration, elle, y repond « reseau local
   uniquement » — c'est voulu.
4. *Parametres > Securite* : « Ajouter une cle » est actif. S'il ne l'est pas, la page dit
   pourquoi — et le message distingue « il manque du TLS » de « declare ton proxy ».
5. Une fiche d'application : « Rendre publique » est revenu.

## Ce qui reste a savoir

- **Le tunnel est le point unique de panne.** Si WireGuard tombe, nginx repond 502 : le site est
  injoignable alors que la ZimaBlade va bien. `PersistentKeepalive` cote ZimaBlade evite le cas le
  plus courant — la box qui oublie la connexion apres une periode de calme.
- **Les applications deployees heritent du TLS**, puisqu'elles passent par le meme panneau. Une
  application *publique* devient donc accessible a tout internet : c'est le but, et c'est la raison
  pour laquelle CodeLab ne le propose pas tant que le panneau n'a jamais ete ouvert par son domaine.
- **Chaque application est sa propre origine.** Un sous-domaine par application, servi par le port
  `9002` : une faille XSS dans une application n'atteint pas le panneau. Ne remets pas les
  applications sous le port du panneau (`APP_MANAGER_APPS_PORT: "0"`).
- **Le trafic passe par ton VPS.** Debit et volume mensuel sont ceux de l'offre, pas ceux de ta
  fibre.

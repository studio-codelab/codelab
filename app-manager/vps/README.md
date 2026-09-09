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

**2. Le DNS.** Fais pointer `codelab.tondomaine.fr` vers l'adresse publique du **VPS**.

**3. Le certificat.** Sur le VPS : `apt install certbot python3-certbot-nginx`, puis
`certbot --nginx -d codelab.tondomaine.fr`. Le renouvellement est automatique.

**4. nginx.** Recopie les deux fichiers, remplace `codelab.exemple.fr` par ton domaine et `10.8.0.2`
par l'adresse WireGuard de la ZimaBlade si tu as choisi un autre reseau. Puis
`nginx -t && systemctl reload nginx`.

> `nginx -t` est la seule etape ou une faute de frappe se voit tout de suite. Fais-la avant chaque
> `reload`.

**5. Le dire a CodeLab.** Dans `docker-compose.yml`, sous `codelab-app-manager` → `environment`,
decommente les trois variables preparees pour ca, avec ton domaine dans `APP_MANAGER_PUBLIC_URL` :

```yaml
      APP_MANAGER_HTTPS: "1"
      APP_MANAGER_TRUST_PROXY: "1"
      APP_MANAGER_PUBLIC_URL: "https://codelab.tondomaine.fr"
```

Puis `docker compose up -d codelab-app-manager`.

**Pas avant que le TLS reponde vraiment** : `APP_MANAGER_HTTPS=1` marque le cookie de session
`Secure`, et le navigateur cesse alors de l'envoyer en clair — ta session tombe au premier
rechargement.

## Verifier

1. `https://codelab.tondomaine.fr` : cadenas, pas d'avertissement.
2. *Configuration > Serveur* : les trois lignes en vert, et l'adresse publique declaree.
3. *Utilisateurs* : ton adresse IP reelle dans les connexions recentes — pas `10.8.0.1`. Si tu vois
   l'adresse du tunnel, `X-Forwarded-For` ne remonte pas ou `APP_MANAGER_TRUST_PROXY` manque.
4. *Parametres > Cles d'acces* : « Ajouter une cle » est actif. S'il ne l'est pas, la page dit
   pourquoi — et le message distingue « il manque du TLS » de « declare ton proxy ».
5. Une fiche d'application : « Rendre publique » est revenu.

## Ce qui reste a savoir

- **Le tunnel est le point unique de panne.** Si WireGuard tombe, nginx repond 502 : le site est
  injoignable alors que la ZimaBlade va bien. `PersistentKeepalive` cote ZimaBlade evite le cas le
  plus courant — la box qui oublie la connexion apres une periode de calme.
- **Les applications deployees heritent du TLS**, puisqu'elles passent par le meme panneau. Une
  application *publique* devient donc accessible a tout internet : c'est le but, et c'est la raison
  pour laquelle CodeLab ne le propose pas tant que l'adresse publique n'est pas declaree.
- **Meme origine pour tout.** Tout est servi sous `https://codelab.tondomaine.fr/<nom>/`. Une faille
  XSS dans une application reste une faille dans l'origine du panneau. Un sous-domaine par
  application y remedierait, au prix d'un certificat generique et d'une entree DNS par projet.
- **Le trafic passe par ton VPS.** Debit et volume mensuel sont ceux de l'offre, pas ceux de ta
  fibre.

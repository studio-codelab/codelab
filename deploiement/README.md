# Deploiement : ce qui vit devant CodeLab

La racine du depot tient **un dossier par service de la stack** — `app-manager`, `dagster`, `dev`,
`postgres`, `workspace`. Ce qui tourne ailleurs que dans le `docker compose` n'y avait pas sa place :
c'est ici.

| Dossier | Ce qu'il contient | Sur quelle machine |
|---|---|---|
| [`vps/`](vps/) | Tunnel WireGuard + nginx : le VPS porte le domaine et le certificat, la ZimaBlade reste injoignable depuis internet | Un VPS, plus l'hote de la ZimaBlade pour son bout de tunnel |

Les deux autres routes decrites dans [`../HTTPS.md`](../HTTPS.md) — **Cloudflare Tunnel** et
**Caddy** — n'ont pas de dossier ici : elles tiennent chacune dans un service a ajouter au
`docker-compose.yml`, et leur configuration complete est dans le guide.

Rien de ce dossier n'est actif par defaut. Tant qu'aucune adresse publique n'est declaree, CodeLab
vit sur ton reseau, sans rien exposer.

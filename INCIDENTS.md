# CodeLab : que faire si…

Une page à lire **avant** d'en avoir besoin. Chaque situation dit quoi regarder, quoi faire, et dans
quel ordre. Les commandes se lancent sur le serveur, dans le dossier où tu as cloné CodeLab (celui
du `docker-compose.yml`). Les données vivent sous `/DATA/AppData/codelab/`.

**Trois règles, quelle que soit la panne :**

1. **Ne supprime rien dans la précipitation.** Renomme (`mv … .avant-incident`) plutôt qu'effacer :
   on revient en arrière en une commande.
2. **Note ce que tu fais, avec l'heure.** C'est ce qui permettra de comprendre, et de faire le bilan.
3. **`credentials.env` est la pièce maîtresse.** Sans lui, les réglages chiffrés sont illisibles. Il
   doit avoir une copie hors du serveur (voir [`SECURITE.md`](SECURITE.md#mise-en-production)).

| Situation | Aller à |
|---|---|
| Le panneau ne s'ouvre plus | [1](#1-le-panneau-ne-souvre-plus) |
| Le serveur entier ne répond plus (alerte de healthchecks.io) | [2](#2-le-serveur-entier-ne-répond-plus) |
| Une application est tombée | [3](#3-une-application-est-tombée) |
| Un compte a été piraté, ou un appareil perdu | [4](#4-un-compte-a-été-piraté-ou-un-appareil-perdu) |
| L'administrateur est bloqué dehors, ou son mot de passe a fuité | [5](#5-ladministrateur-est-bloqué-dehors-ou-son-mot-de-passe-a-fuité) |
| Le disque est plein | [6](#6-le-disque-est-plein) |
| Il faut restaurer | [7](#7-restaurer) |

---

## 1. Le panneau ne s'ouvre plus

Le serveur répond (SSH marche), mais `http://<serveur>:9001/` non.

1. **L'état des conteneurs** : `docker compose ps`. Un service `unhealthy` ou `exited` est le
   suspect.
2. **Le journal du panneau** : `docker logs --tail 200 codelab-app-manager`. Une erreur au démarrage
   (fichier illisible, disque plein, port occupé) s'y lit en clair. Une panne d'une requête porte
   une **référence** courte (« 7F3A2C »), la même que celle affichée dans la page.
3. **Relancer le panneau seul** : `docker compose restart codelab-app-manager`. Les applications
   survivent au redémarrage du panneau, qui les reprend sans les relancer.
4. **Si rien ne change** : `docker compose up -d` remet en place tout ce qui manque.
5. **Toujours rien** : regarde le disque ([6](#6-le-disque-est-plein)), puis restaure l'état du
   panneau ([7](#7-restaurer)).

## 2. Le serveur entier ne répond plus

C'est le cas que la **surveillance externe** (*Paramètres › E-mail et alertes*, healthchecks.io)
signale : le panneau ne l'appelle plus depuis plus de cinq minutes.

1. **Vérifie depuis un autre réseau** (le téléphone en 4G) : une box ou un VPN en panne ressemble à
   un serveur éteint.
2. **Électricité, réseau, machine** : redémarre-la si elle est accessible physiquement.
3. **Au retour**, tout repart seul (`restart: unless-stopped`). Vérifie avec `docker compose ps`,
   puis la tuile **Diagnostic** du hub.
4. **Si la machine ne redémarre pas** (disque mort) : c'est une réinstallation, avec la
   restauration complète ([7](#7-restaurer)). Il te faut alors `credentials.env` et les sauvegardes
   copiés **ailleurs**.

## 3. Une application est tombée

Le panneau la redémarre seul. Une alerte par mail part quand elle ne redémarre plus, puis une autre
à son retour.

1. **Sa fiche** (*Applications › l'application*) : son état, son **journal** (onglet Journal) et
   la cause probable de l'arrêt.
2. **Le fichier du journal**, si la page ne suffit pas :
   `/DATA/AppData/codelab/app-manager/logs/<nom>.log`.
3. **Corriger, puis Build et Redémarrer** depuis la fiche. Une dépendance cassée par la dernière
   modification se répare souvent en revenant au commit précédent (`git log`, `git revert`).
4. **Sa base** se restaure seule, sans toucher aux autres ([7](#7-restaurer)).
5. **« Le port … est encore tenu »** : un reste de l'application tient son port. Le panneau termine
   d'office ce qui reste d'une application morte avant de la relancer ; si le message persiste,
   `docker restart codelab-app-manager` libère tous les ports, ou change le port dans la fiche.

## 4. Un compte a été piraté, ou un appareil perdu

**D'abord fermer, ensuite comprendre.**

1. **Suspendre le compte** : *Utilisateurs › le compte › Suspendre*. L'accès se ferme tout de suite,
   sessions ouvertes comprises, et rien n'est effacé : un clic le rouvre.
2. **Déconnecter ses appareils** : dans la même fiche, « Déconnecter partout ». Pour un téléphone
   perdu, cela suffit souvent, sans suspendre.
3. **Lire ce qu'il a fait** : *Utilisateurs › Journal des accès*, filtré sur ce compte. Les
   connexions disent l'adresse IP et le moyen (mot de passe, clé d'accès, code).
4. **Avant de rouvrir** : un nouveau mot de passe, et, si l'appareil qui portait le second facteur
   est perdu, « Réinitialiser le second facteur » dans sa fiche : il en enregistrera un nouveau à
   sa prochaine connexion.
5. **Si un projet a pu être modifié** : `git log` et `git diff` dans `/workspace/<projet>`.

## 5. L'administrateur est bloqué dehors, ou son mot de passe a fuité

Le mot de passe et le secret du second facteur de l'administrateur vivent dans
`/DATA/AppData/codelab/config/credentials.env` (bloc `codelab-app-manager`).

**Mot de passe oublié** : il se relit, il ne se perd pas.

```bash
sudo grep APP_MANAGER_ADMIN_PASSWORD /DATA/AppData/codelab/config/credentials.env
```

**Téléphone perdu (second facteur)** : la marche arrière passe par le compose, pas par le panneau.

1. Dans `docker-compose.yml`, décommente `APP_MANAGER_ADMIN_2FA: "0"`.
2. `docker compose up -d codelab-app-manager`.
3. Connecte-toi au mot de passe seul, enregistre un nouveau second facteur (*Paramètres ›
   Sécurité*).
4. **Recommente la ligne** et relance (`docker compose up -d codelab-app-manager`) : tant qu'elle
   reste active, le second facteur n'est plus exigé.

**Mot de passe fuité, ou doute sur le compte administrateur** :

1. **Change le mot de passe** : remplace la valeur de `APP_MANAGER_ADMIN_PASSWORD` dans
   `credentials.env` (une valeur longue et aléatoire, par exemple `openssl rand -base64 24`).
2. **Ferme toutes les sessions** : remplace aussi `APP_MANAGER_SESSION_SECRET` (`openssl rand -hex
   32`). Chaque appareil devra se reconnecter, comptes utilisateurs compris.
3. `docker compose restart codelab-app-manager`.
4. **Lis le journal des accès** : connexions administrateur, adresses inconnues, réglages modifiés.
5. **Ne touche jamais à `APP_MANAGER_CHIFFREMENT_CLE`** : une autre clé rendrait illisibles tous
   les réglages chiffrés (serveur d'envoi, adresses, clé des notifications).

## 6. Le disque est plein

Les symptômes : des sauvegardes qui échouent, une base qui refuse d'écrire, un panneau qui ne
démarre plus. Le **Diagnostic** surveille l'espace et prévient avant.

1. **Où va la place** :

   ```bash
   df -h /DATA
   sudo du -sh /DATA/AppData/codelab/* | sort -h
   docker system df
   ```

2. **Ce qui se libère sans risque** :
   - les images Docker inutilisées : `docker image prune` (seulement celles qu'aucun conteneur
     n'utilise) ;
   - les dépendances et builds d'un projet (`node_modules`, `.venv`, `dist`), qui se reconstruisent
     au prochain Build ;
   - les vieilles sauvegardes, **une fois copiées ailleurs** : le panneau en garde 7 de l'état, 4 du
     code, et Postgres les siennes.
3. **Ce qu'il ne faut pas effacer** : `config/` (les secrets), `app-manager/codelab.db`, `postgres/`.
   Pour gagner de la place sur la base de données, passe par Postgres, jamais par ses fichiers.
4. **Relance** ce qui s'était arrêté : `docker compose up -d`.

## 7. Restaurer

Les commandes détaillées sont dans [`postgres/README.md`](postgres/README.md#restaurer-une-base).
Les sauvegardes sont dans `/DATA/AppData/codelab/sauvegardes/` :

| Archive | Contient |
|---|---|
| `base-<projet>-<date>.dump` | la base d'un projet (Postgres) |
| `panneau-<date>.tar.gz` | l'état du panneau : comptes, applications, droits, journal, réglages |
| `workspace-<date>.tar.gz` | le code des projets, sans dépendances ni builds |

**Une base seule** : voir [Restaurer une base](postgres/README.md#restaurer-une-base). Restaure
d'abord **à côté** (`diagnostic_restaure`) pour vérifier, puis écrase.

**L'état du panneau** : voir
[Restaurer l'état du panneau](postgres/README.md#restaurer-létat-du-panneau). Le panneau doit être
arrêté, et l'ancien dossier est mis de côté, pas effacé.

**Tout, sur un nouveau serveur** (disque mort, machine perdue) :

1. Installe CodeLab ([`README.md`](README.md#installer)), **sans le démarrer** encore.
2. Remets `credentials.env` dans `/DATA/AppData/codelab/config/`, puis les sauvegardes dans
   `/DATA/AppData/codelab/sauvegardes/`.
3. Le code des projets :

   ```bash
   sudo mkdir -p /DATA/AppData/codelab/workspace
   sudo tar -xzf /DATA/AppData/codelab/sauvegardes/workspace-<date>.tar.gz \
     -C /DATA/AppData/codelab/workspace
   ```

4. L'état du panneau, comme ci-dessus (dans `/DATA/AppData/codelab/app-manager`).
5. `docker compose up -d`, puis restaure chaque base (`pg_restore`, voir plus haut).
6. Reconstruis les projets (**Build** dans chaque fiche : les dépendances ne sont pas dans
   l'archive), puis vérifie avec la tuile **Diagnostic**.

Sans le `credentials.env` d'origine, les réglages chiffrés sont perdus (serveur d'envoi, adresses
d'alerte), les sessions tombent et le mot de passe administrateur change : tout le reste se
retrouve.

---

## Après l'incident

Quelques lignes, à garder avec tes notes :

- **Quoi** : ce qui a cessé de marcher, et pendant combien de temps.
- **Pourquoi** : la cause, pas seulement le symptôme.
- **Comment on l'a vu** : une alerte, un utilisateur, par hasard. Si c'est par hasard, il manque une
  alerte.
- **Ce qui change** : le réglage, la sauvegarde ou la surveillance qui l'aurait évité ou signalé
  plus tôt.

# CodeLab -- conventions de travail

## Audit de securite a chaque branche

**Avant d'ouvrir une PR, auditer la branche.** Ce n'est pas optionnel et ce
n'est pas la CI qui le fait : les scans automatiques (CodeQL, Trivy) tournent
sur `main`, ils rapportent des vulnerabilites connues dans des dependances.
Ils ne lisent pas ce que la branche vient d'ecrire.

Ce qu'il faut regarder, dans cet ordre :

1. **Ce que la branche ouvre comme surface.** Une route ajoutee, un champ
   accepte, un fichier lu, un process lance. Pour chacun : qui peut
   l'atteindre sans etre authentifie ? sans etre administrateur ?
2. **Les gardes qui existent deja et qu'on aurait contournees.**
   `verifier_jeton` (jeton CSRF, before_request), `require_admin`,
   `drop_privileges`, `strip_session_cookie`, le bornage des chemins a
   `/workspace`. Une route d'ecriture ajoutee sans exemption est protegee
   d'office -- une exemption ajoutee ne l'est pas.
3. **Ce qui n'est protege que par accident.** La question qui a deja paye :
   « si quelqu'un ecrit la ligne evidente demain, est-ce que ca casse ? »
   C'est ainsi qu'on a trouve que l'enveloppe de `fetch` posait le jeton sans
   regarder l'origine de la cible.
4. **Ce qu'on affirme sans l'avoir mesure.** Relire les commentaires et le
   message de commit : chaque affirmation de securite doit correspondre a
   quelque chose qu'on a EXECUTE.

## Verifier, jamais supposer

- **Reproduire avant de corriger.** Un correctif dont on n'a pas vu le
  probleme ne prouve rien.
- **Muter chaque test ajoute.** Casser le code qu'il garde, verifier qu'il
  rougit, restaurer. Un test qui ne rougit jamais ne garde rien. Sur la suite
  COMPLETE : `-k` selectionne un sous-ensemble et fait croire qu'une mutation
  a survecu.
- **Verifier qu'on mesure le bon code.** Piege rencontre deux fois : une
  instance de test lancee AVANT une modification continue de servir l'ancienne
  page. Verifier le pid, ou chercher la nouvelle ligne dans la reponse, avant
  de conclure.
- **Mesurer plutot que raisonner** : styles calcules, positions, uid reels,
  ce que recoit vraiment un serveur en face.

## Ce qui n'est pas negociable

Ces choix ont ete pris contre un audit, avec leurs raisons. Ne pas les
"corriger" sans en reparler :

- **Les ports 9001 et 3000 sont publies sur l'hote.** Le panneau existe pour
  etre ouvert depuis un autre poste. Les binder sur localhost imposerait un
  tunnel SSH a chaque usage.
- **Une application = un process, pas un conteneur.** Un conteneur par
  application voudrait dire monter le socket Docker dans app-manager :
  l'equivalent de root sur l'hote, donne a un service expose.
- **Les images de base restent referencees par tag** (`python:3.13-slim`).
  Un digest fige bloquerait les correctifs de securite au lieu de les
  recevoir a chaque reconstruction.
- **`codelab-dev` n'est pas durci.** C'est le shell : `no-new-privileges`
  y casserait `sudo`, `cap_drop: ALL` la separation de privileges de sshd.

## Le reste

- Francais, **sans accents** dans le code, les commentaires et les commits.
- Les commentaires expliquent POURQUOI, pas quoi : ils font partie du produit.
- `docker-compose.yml` et `docker-compose-casaos.yml` doivent rester
  identiques service pour service.
- Une fonctionnalite optionnelle s'eteint proprement (`qrcode`, `webauthn`,
  `psycopg`, la bascule d'uid de Dagster) plutot que de faire echouer le
  demarrage.

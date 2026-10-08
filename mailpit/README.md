# codelab-mailpit

L'envoi de mails des applications. Une application parle SMTP à ce service. Il **garde** chaque mail
consultable et le **relaie** par le serveur d'envoi réglé dans le panneau.

## Ce que cela apporte par rapport à un SMTP direct

- **Un historique** : chaque mail envoyé par une application se relit dans une page web, avec son
  destinataire, son contenu et son rendu. Avec un SMTP direct, on ne voit rien de ce qui est parti.
- **Un seul endroit pour les identifiants** : les applications envoient sans mot de passe. Celui du
  serveur d'envoi reste dans le panneau et n'entre jamais dans leur environnement. Changer de
  fournisseur se fait à un seul endroit.
- **Un mode test** : tout est gardé et rien n'est envoyé. On peut essayer une inscription ou un « mot
  de passe oublié » sans écrire à personne.

## Les mails du panneau aussi

Le panneau envoie **ses propres mails** (alertes, codes de connexion, invitations, mots de passe
oublies) par ce meme service. Ils se relisent donc dans l'interface, comme ceux des applications —
pratique pour retrouver un lien d'invitation, ou un code en mode test. Si Mailpit ne repond pas, le
panneau repasse en envoi direct par le serveur de *Parametres > E-mail*.

## S'en servir depuis une application

Le panneau transmet :

| Variable | Valeur |
|---|---|
| `MAIL_HOST` | `codelab-mailpit` |
| `MAIL_PORT` | `1025` |
| `MAIL_URL` | `smtp://codelab-mailpit:1025` |

```python
import os, smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg["From"] = "Mon app <noreply@exemple.fr>"   # remplacé par l'adresse du compte d'envoi
msg["To"] = "ami@exemple.org"
msg["Subject"] = "Bienvenue"
msg.set_content("Bonjour !")
with smtplib.SMTP(os.environ["MAIL_HOST"], int(os.environ["MAIL_PORT"])) as s:
    s.send_message(msg)
```

Il n'y a ni TLS ni identifiants à fournir : le service n'est joignable que depuis le réseau de la stack.

## Le serveur d'envoi

C'est celui de **Paramètres > E-mail**, dans le panneau, le même que pour les alertes. Un compte Gmail
avec un mot de passe d'application suffit, sans nom de domaine. Le panneau le recopie dans le bloc
`codelab-relais-mail` de `credentials.env`. Mailpit y lit le changement et se relance tout seul, dans
les quinze secondes.

**L'expéditeur est remplacé par l'adresse du compte** : Gmail et la plupart des fournisseurs
refusent d'expédier au nom d'une autre adresse. Le nom affiché (« Mon app ») est conservé.

Sans serveur d'envoi réglé, les mails sont gardés et aucun ne part.

## Consulter les mails

Par la **tuile Mailpit du hub**, ou sur `http://<serveur>:8025/`, avec la session d'**administration**
du panneau. Aucun compte utilisateur n'y accède, puisque l'interface montre tous les mails, liens de
réinitialisation compris.

L'interface est celle de CodeLab, servie par `codelab-dagster-proxy` (`dagster/proxy/mailpit-ui/`) sur
l'API de Mailpit. Le contenu d'un mail n'est jamais inséré dans la page : son HTML ne s'affiche que
dans un cadre isolé, sans script.

Mailpit exige en plus un mot de passe interne sur son API, ajouté par le proxy : une application du
même réseau ne peut pas lire les mails des autres. Les 500 derniers mails sont gardés, un mois au
plus.

## Le mode test

Dans le service du `docker-compose.yml` : `CODELAB_MAILPIT_RELAIS: "non"`, puis redémarrer
`codelab-mailpit`. Tout est gardé, rien n'est envoyé. Remettre `"auto"` pour relayer de nouveau.

## Le plafond d'envoi

Une application partie en boucle enverrait un mail par tour de boucle, et le compte d'envoi finirait
suspendu, alertes du panneau comprises. Au-delà de **50 mails sur l'heure** ou de **200 sur les
24 dernières heures** (réglables dans le compose : `CODELAB_MAILPIT_MAX_HEURE`,
`CODELAB_MAILPIT_MAX_JOUR`, `0` pour désactiver), Mailpit **garde** les mails sans les relayer.
`docker logs codelab-mailpit` le signale par une ligne `ALERTE : plafond d'envoi atteint`.

Le relais reprend tout seul quand la fenêtre se vide. Les mails retenus se relaient à la main depuis
l'interface (bouton *Release*). Le plafond est vérifié toutes les quinze secondes, donc à quelques
mails près lors d'une rafale.

## Limites

- Un serveur d'envoi Gmail accepte environ 500 mails par jour : largement assez pour des inscriptions
  et des notifications, pas pour une lettre d'information.
- Un envoi refusé par le serveur d'envoi se lit dans `docker logs codelab-mailpit` (`[relay] error`).
  L'application, elle, a déjà reçu un « OK » de Mailpit.

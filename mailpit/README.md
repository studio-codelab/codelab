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

`http://<IP-du-serveur>:8025/`, avec la **session d'administration du panneau**, comme Dagster.
L'interface montre tous les mails, liens de réinitialisation de mot de passe compris. Elle n'est donc
ouverte à aucun compte utilisateur.

Derrière, Mailpit exige lui-même un mot de passe sur toute son API. Sans cela, une application qui
partage son réseau pourrait lire les mails de ses voisines. Ce mot de passe est généré par
`codelab-dagster-proxy`, qui l'ajoute à chaque requête déjà autorisée ; personne n'a à le taper.

Les 500 derniers mails sont conservés, un mois au plus.

## Le mode test

Dans le service du `docker-compose.yml` : `CODELAB_MAILPIT_RELAIS: "non"`, puis redémarrer
`codelab-mailpit`. Tout est gardé, rien n'est envoyé. Remettre `"auto"` pour relayer de nouveau.

## Limites

- Un serveur d'envoi Gmail accepte environ 500 mails par jour : largement assez pour des inscriptions
  et des notifications, pas pour une lettre d'information.
- Un envoi refusé par le serveur d'envoi se lit dans `docker logs codelab-mailpit` (`[relay] error`).
  L'application, elle, a déjà reçu un « OK » de Mailpit.

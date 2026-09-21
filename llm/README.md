# Le profil IA de CodeLab

Un conteneur, une seule porte. Le profil `llm` est optionnel : sans lui, rien
de tout cela ne démarre.

| Conteneur | Rôle | Joignable depuis |
|---|---|---|
| `codelab-llm` | Passerelle compatible OpenAI : identité, quota, comptabilité | le réseau interne seulement |

`codelab-llm` ne publie aucun port : il détient la clé du fournisseur, et ses
clients sont les applications de CodeLab, qui partagent le réseau `codelab`.

## Pas d'interface de conversation

CodeLab n'héberge pas d'interface de chat. L'ancienne application `aichat` a
été retirée, et l'interface Open WebUI qui l'avait remplacée l'a été à son
tour : l'outil du développeur est son éditeur, et les applications appellent
la passerelle directement. Une interface web de conversation n'apportait
qu'une couche de plus, avec son portier, son réseau dédié et son volume de
conversations à sauvegarder.

Un client extérieur — un éditeur, un agent de développement — s'identifie par
une clé CodeLab (voir plus bas), pas par une session de navigateur.

## Démarrage

Une valeur à renseigner dans `.env`, décrite dans `.env.example` : la clé du
fournisseur.

```bash
docker compose --profile llm up -d
```

La base `codelab_llm` et son rôle dédié sont créés par Postgres au premier
démarrage ; le schéma est initialisé par la passerelle. La clé du fournisseur
n'est pas posée dans l'environnement du conteneur : le panneau l'écrit dans
`config/llm/llm.env`, que `codelab-llm` lit au démarrage.

L'interface s'ouvre ensuite depuis le hub CodeLab, sous « Chat ».

## Qui est qui

Le portier interroge le panneau à chaque requête et réécrit lui-même les
en-têtes d'identité, en écrasant ce que le client aurait pu envoyer. Sans
session CodeLab valide, la requête est renvoyée vers la page de connexion.

L'interface prouve son identité à la passerelle par le secret partagé,
comparé en temps constant. C'est ce qui empêche une autre application de se
faire passer pour elle : sur un réseau partagé, un en-tête d'identité ne
prouve rien.

Et le portier ne protégerait rien si l'on pouvait passer à côté : les
applications déclarées dans le panneau tournent dans son conteneur et
partagent donc son réseau. L'interface vit pour cette raison sur un réseau
à part, fermé vers l'extérieur, que seuls son portier et la passerelle
voient.

## Les modèles proposés

Une entrée par défaut, `codelab-smart`, laisse le fournisseur choisir. Pour
en ajouter ou en changer, poser une variable `CODELAB_LLM_MODELE_<NOM>` :
le nom logique est celui de la variable, en minuscules, tirets à la place des
soulignés. Un nom absent de cette table est refusé avant tout appel sortant.

## Une clé pour une application

Une application peut parler directement à la passerelle avec sa propre clé,
liée à une identité et à un usage :

```bash
docker compose exec codelab-llm \
  python3 app.py create-key --name vscode --user alice --app vscode
```

La clé est affichée une seule fois — la base n'en garde que l'empreinte, donc
personne ne peut la relire ensuite. Pour la révoquer, fournir cette même
valeur à `revoke-key`.

## Ce qui est conservé

La passerelle ne garde que la comptabilité : qui, quelle application, quel
modèle demandé, quel modèle réel, combien de jetons. Le contenu des échanges
n'est ni stocké ni journalisé par elle — il appartient au client qui les
mène.

## Diagnostic

Sans le profil démarré, les sondes IA sont classées « sans objet » plutôt que
« en panne ». Les deux vérifications approfondies — le modèle répond, la
consommation est comptée — demandent une clé CodeLab dédiée au diagnostic,
déclarée dans `credentials.env` sous `CODELAB_LLM_DIAGNOSTIC_KEY` ; sans
elle, elles restent « sans objet ».

# Le profil IA de CodeLab

Trois conteneurs, une seule porte. Le profil `llm` est optionnel : sans lui,
rien de tout cela ne démarre.

| Conteneur | Rôle | Joignable depuis |
|---|---|---|
| `codelab-llm` | Passerelle compatible OpenAI : identité, quota, comptabilité | le réseau interne seulement |
| `codelab-open-webui` | L'interface de conversation | le réseau interne seulement |
| `codelab-open-webui-proxy` | Le portier : il vérifie la session CodeLab | port `3001` |

La seule adresse publiée est celle du portier. `codelab-llm` ne publie aucun
port : il détient la clé du fournisseur, et son unique client est l'interface.

## Pourquoi Open WebUI et non une application maison

L'ancienne application `aichat` réécrivait une interface de conversation —
historique, pièces jointes, rendu Markdown, reprise de fil — que ce projet
n'a pas vocation à maintenir. Open WebUI le fait déjà, se branche sur une API
compatible OpenAI, et ne connaît aucune clé de fournisseur : elle parle à
`codelab-llm`, et rien d'autre.

## Démarrage

Deux valeurs à renseigner dans `.env`, décrites dans `.env.example` : la clé
du fournisseur et le secret partagé entre l'interface et la passerelle.

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

Les conversations vivent dans Open WebUI, et nulle part ailleurs. La
passerelle ne garde que la comptabilité : qui, quelle application, quel
modèle demandé, quel modèle réel, combien de jetons. Le contenu des échanges
n'est ni stocké ni journalisé par elle.

## Diagnostic

Sans le profil démarré, les sondes IA sont classées « sans objet » plutôt que
« en panne ». Les deux vérifications approfondies — le modèle répond, la
consommation est comptée — demandent une clé CodeLab dédiée au diagnostic,
déclarée dans `credentials.env` sous `CODELAB_LLM_DIAGNOSTIC_KEY` ; sans
elle, elles restent « sans objet ».

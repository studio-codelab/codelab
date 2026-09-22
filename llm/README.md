# Le profil IA de CodeLab

Deux conteneurs, une seule porte. Le profil `llm` est optionnel : sans lui,
rien de tout cela ne démarre.

| Conteneur | Rôle | Joignable depuis |
|---|---|---|
| `codelab-llm` | Identité CodeLab, quota par personne, comptabilité | réseau `codelab` (les applications) |
| `codelab-litellm` | Routage, replis, clés des fournisseurs | réseau `codelab-ia` (codelab-llm seul) |

**Aucun des deux ne publie de port.** Les applications et les éditeurs
s'adressent à `codelab-llm`, qui va lui-même chercher le routeur.

## La frontière entre les deux

| | `codelab-llm` | `codelab-litellm` |
|---|---|---|
| Qui parle, à quel rythme | ✅ | — |
| Ce qui a été consommé, en base | ✅ | — |
| Vers quel fournisseur, avec quel repli | — | ✅ |
| Les clés des fournisseurs | — | ✅ |

Le routeur n'est pas sur le réseau `codelab`, et c'est le point important :
les applications déployées par le panneau y tournent, et pouvoir l'appeler
en direct reviendrait à contourner identité, quota et comptabilité — avec
les clés de chaque fournisseur au bout.

Les noms logiques (`codelab-coding`, `codelab-fast`, `codelab-reasoning`,
`codelab-local`) sont le contrat avec les clients. Changer de modèle se fait
dans `litellm/config.yaml`, sans toucher à un seul client.

`codelab-local` ne se replie jamais sur un fournisseur distant : sans serveur
Ollama joignable, il répond en erreur. Un repli silencieux ferait sortir de
la machine des données qu'on gardait ici exprès.

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

Deux valeurs à renseigner : `CODELAB_LITELLM_KEY` (la clé partagée entre la
passerelle et le routeur, n'importe quelle valeur au hasard) et au moins une
clé de fournisseur (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

Deux endroits possibles, et ils ne s'écrasent pas :

- **le panneau**, *Paramètres → Clés et secrets*. Il écrit
  `config/llm/fournisseurs.env` (en `0600`), que le routeur reçoit par
  `env_file`, et n'en réaffiche que les quatre derniers caractères ;
- **le `.env` de l'hôte**, décrit dans `.env.example`, pour une installation
  scriptée. Au démarrage, `codelab-postgres` recopie ce qu'il y trouve dans
  le même fichier — **une valeur vide ne remplace jamais celle déjà posée**,
  donc ce qui a été saisi dans le panneau survit aux redémarrages.

Le panneau n'atteint ni le compose ni le `.env` de l'hôte, et c'est
délibéré : les lui monter lui donnerait de quoi réécrire la stack. C'est
pour cette raison, et pas une autre, qu'une clé de fournisseur posée depuis
l'interface ne prend effet qu'au prochain démarrage du conteneur.

```bash
docker compose --profile llm up -d
```

La base `codelab_llm` et son rôle dédié sont créés par Postgres au premier
démarrage ; le schéma est initialisé par la passerelle.

## Qui est qui

Aucun en-tête n'est cru sur parole. Une requête arrive avec l'une de deux
preuves, et sans elle, 401 :

| Preuve | Pour qui |
|---|---|
| Une assertion SSO signée par le panneau (Ed25519) | une application déclarée, dont le nom figure dans `CODELAB_LLM_AUDIENCE` |
| Une clé CodeLab enregistrée en base, révocable | un éditeur, un agent de développement, une application à soi |

Une audience vide n'autorise personne : sans nom configuré, aucune assertion
n'est acceptée. C'est volontaire — un défaut qui nommerait une application la
rendrait autorisée sur une installation qui ne l'a jamais demandée.

Un en-tête `X-CodeLab-User-Id` reste lu, mais il ne **prouve** rien : il sert
à attribuer la consommation au bon utilisateur, une fois la clé vérifiée. Sur
un réseau partagé, un en-tête d'identité ne prouve jamais rien.

## Les modèles proposés

Quatre noms logiques par défaut, alignés sur `litellm/config.yaml` :

| Nom | Usage |
|---|---|
| `codelab-coding` | le modèle de travail des agents de développement |
| `codelab-fast` | les tâches courtes, le peu coûteux |
| `codelab-reasoning` | ce qu'on accepte de payer plus cher et d'attendre |
| `codelab-local` | un Ollama à soi — aucun jeton ne sort de la machine |

Pour en ajouter ou en changer, poser une variable `CODELAB_LLM_MODELE_<NOM>` :
le nom logique est celui de la variable, en minuscules, tirets à la place des
soulignés. Un nom absent de cette table est refusé avant tout appel sortant.

Ces noms doivent exister **des deux côtés** : servis ici, et déclarés dans
`litellm/config.yaml`. Un test le vérifie — un nom présent d'un seul côté
échouerait au premier appel, pas au démarrage.

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

Le panneau fait le même geste, sans commande et sans copier-coller :
*Paramètres → Clés et secrets*. Il écrit la même table avec la même
empreinte, puis pose la valeur dans les deux fichiers où Codex et OpenClaw
la lisent — et ne l'affiche jamais. Une clé créée d'un côté se révoque de
l'autre.

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

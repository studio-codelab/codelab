# Le service IA de CodeLab

Un conteneur, une seule porte : `codelab-llm`. Il est joignable depuis le
réseau `codelab` (tes applications, le poste de développement) et **ne publie
aucun port**.

```
application / Codex ──clé CodeLab──▶ codelab-llm ──clé OpenRouter──▶ OpenRouter ──▶ Claude, GPT…
```

| | `codelab-llm` |
|---|---|
| Qui parle, à quel rythme | ✅ identité CodeLab, quota |
| Ce qui a été consommé | ✅ comptabilité en base, par clé |
| Vers quel fournisseur | OpenRouter par défaut, appelé en direct |
| La clé du fournisseur | ✅ dans `config/llm/llm.env`, que lui seul lit |

Ni tes applications ni le poste de développement ne voient la clé du
fournisseur : ils présentent une **clé CodeLab**, révocable, avec quota et
comptabilité. C'est ce qui rend acceptable qu'un agent comme Codex tourne
avec tous les droits dans `/workspace` : au pire, il fuite une clé CodeLab,
qui se révoque en un clic.

## Pourquoi plus de routeur

Il y avait un second conteneur, `codelab-litellm`, entre la passerelle et les
fournisseurs, et une clé de *liaison* entre les deux. Il est retiré : sur une
petite machine, il coûtait plus d'un Go de disque, 512 Mo de RAM, un fichier
de clés et une panne muette de plus — pour router entre fournisseurs, ce
qu'**OpenRouter** fait déjà, avec une seule clé et ses propres replis.

Le fournisseur doit servir `/responses` (Codex n'a plus que ce protocole) et
`/chat/completions` (les applications). OpenRouter sert les deux.

## Démarrage

**Une seule valeur à renseigner : la clé OpenRouter.** Deux endroits
possibles, et ils ne s'écrasent pas :

- **le panneau**, *Paramètres → Clés et secrets* : on colle la clé, le
  panneau l'écrit dans `config/llm/llm.env` (en `0600`) et n'en réaffiche que
  les quatre derniers caractères. **Prise en compte immédiatement** — la
  passerelle relit ce fichier à chaque appel ;
- **le `.env` de l'hôte** (`OPENROUTER_API_KEY`, voir `.env.example`), pour
  une installation scriptée. Au démarrage, `codelab-postgres` la recopie dans
  le même fichier — **une valeur vide ne remplace jamais celle déjà posée**.

Avec `docker-compose.yml`, le service est derrière le profil optionnel
`llm` :

```bash
docker compose --profile llm up -d
```

Avec `docker-compose-casaos.yml`, il n'y a **pas de profil** : CasaOS ne sait
pas en activer un. Le service démarre avec le reste, et le compose se suffit à
lui-même — c'est le seul fichier à importer dans CasaOS.

La base `codelab_llm` et son rôle dédié sont créés par Postgres au premier
démarrage ; le schéma est initialisé par la passerelle.

## Les modèles proposés

Trois noms logiques — le contrat avec les clients :

| Nom | Modèle réel (OpenRouter) | Usage |
|---|---|---|
| `codelab-coding` | `anthropic/claude-sonnet-5` | le modèle de travail de Codex |
| `codelab-fast` | `anthropic/claude-haiku-4.5` | les tâches courtes, le peu coûteux |
| `codelab-reasoning` | `anthropic/claude-opus-5.5` | ce qu'on accepte de payer plus cher |

Vérifie ces identifiants sur [openrouter.ai/models](https://openrouter.ai/models)
le jour de l'installation : un identifiant inexact se voit au premier appel,
pas au démarrage. Pour en changer — ou ajouter un nom — pose une ligne
`CODELAB_LLM_MODELE_<NOM>` dans `config/llm/llm.env` : le nom logique est
celui de la variable, en minuscules, tirets à la place des soulignés.

```
CODELAB_LLM_MODELE_CODELAB_FAST=openai/gpt-5-mini
```

Pour un autre fournisseur compatible OpenAI, `CODELAB_LLM_FOURNISSEUR=` (son
adresse `…/v1`) dans le même fichier. Ces réglages posés à la main survivent
aux redémarrages : `codelab-postgres` réécrit le fichier mais les conserve.

## Qui est qui

Aucun en-tête n'est cru sur parole. Une requête arrive avec l'une de deux
preuves, et sans elle, 401 :

| Preuve | Pour qui |
|---|---|
| Une assertion SSO signée par le panneau (Ed25519) | une application déclarée, dont le nom figure dans `CODELAB_LLM_AUDIENCE` |
| Une clé CodeLab enregistrée en base, révocable | un éditeur, un agent de développement, une application à soi |

Une audience vide n'autorise personne : sans nom configuré, aucune assertion
n'est acceptée.

Un en-tête `X-CodeLab-User-Id` reste lu, mais il ne **prouve** rien : il sert
à attribuer la consommation au bon utilisateur, une fois la clé vérifiée.

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
empreinte, et une clé créée d'un côté se révoque de l'autre.

La console y ajoute ce que la ligne de commande ne donne pas :

| | |
|---|---|
| **Créer** | un nom, une application, et la clé est montrée **une seule fois** |
| **Révoquer** | une clé parmi d'autres, par son identifiant |
| **Suivre** | requêtes, jetons et dernier appel, **par clé** |

Le suivi par clé demande la colonne `llm_usage.key_id` : sans elle, deux clés
du même poste sont indistinguables. Elle est ajoutée aux bases existantes par
un `ALTER TABLE` au démarrage — `CREATE TABLE IF NOT EXISTS` ne touche pas
une table qui existe déjà.

**La clé du poste de développement est un cas à part** : le panneau la crée
au démarrage si elle manque, et la pose lui-même dans
`.codex/codelab-llm.key`, où Codex la relit. Elle ne s'affiche
jamais, pas même à la création — elle est écrite directement là où elle sert.

Le nom et l'application sont une **étiquette comptable** : ils disent qui a
consommé quoi et permettent de couper une clé qui dérape. Ils ne
*restreignent* pas ce que la clé peut demander — ce serait un contrôle à
faire dans la passerelle.

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

# Le service IA de CodeLab

Un conteneur, une seule porte : `codelab-llm`. Il est joignable depuis le
réseau `codelab` (tes applications, le poste de développement) et **ne publie
aucun port**.

```
application / Codex ──clé CodeLab──▶ codelab-llm ──▶ Gemini ─┐ quota épuisé ?
                                                  ──▶ Mistral ─┤ le suivant
                                                  ──▶ Groq ────┤
                                                  ──▶ OpenRouter (modèles « :free »)
```

| | `codelab-llm` |
|---|---|
| Qui parle, à quel rythme | ✅ identité CodeLab, quota |
| Ce qui a été consommé | ✅ comptabilité en base, par clé et par fournisseur |
| Vers quel fournisseur | une **chaîne** : Gemini, Mistral, Groq, OpenRouter, appelés en direct |
| Les clés des fournisseurs | ✅ dans `config/llm/llm.env`, que lui seul lit |

Ni tes applications ni le poste de développement ne voient les clés des
fournisseurs : ils présentent une **clé CodeLab**, révocable, avec quota et
comptabilité. C'est ce qui rend acceptable qu'un agent comme Codex tourne
avec tous les droits dans `/workspace` : au pire, il fuite une clé CodeLab,
qui se révoque en un clic.

## La part gratuite de plusieurs fournisseurs

Chaque fournisseur offre un quota gratuit — requêtes par minute, par jour,
jetons par mois — et aucun ne suffit seul à une journée de Codex. Un nom
logique désigne donc une **chaîne** : le premier fournisseur dont la clé est
posée répond ; s'il refuse (quota épuisé, crédit insuffisant, modèle retiré,
panne), le suivant prend la requête. Ni Codex ni l'application ne le voient.

Un fournisseur qui vient de dire « quota épuisé » est mis **au repos** le
temps qu'il indique (une minute sinon) : on ne lui redemande pas à chaque
appel. Si tous refusent, la réponse dit pourquoi, fournisseur par
fournisseur — et c'est aussi dans `docker logs codelab-llm`.

| Fournisseur | Clé gratuite | Ce qu'il apporte |
|---|---|---|
| Google Gemini | [aistudio.google.com](https://aistudio.google.com) | le quota le plus large, très grand contexte |
| Mistral | [console.mistral.ai](https://console.mistral.ai) (palier « Experiment ») | Devstral, fait pour le code |
| Groq | [console.groq.com](https://console.groq.com) | très rapide ; limites par minute basses |
| OpenRouter | [openrouter.ai](https://openrouter.ai) | modèles « :free » ; avec du crédit, Claude, GPT… |

**Ce que coûte le gratuit.** Au palier gratuit, ces services peuvent se
servir des échanges pour améliorer leurs modèles : n'y envoie pas ce qui ne
doit pas sortir. Les quotas et les modèles offerts changent souvent.

**Une seule clé suffit** pour que tout marche ; chacune de plus repousse le
moment où tout est épuisé.

## Pourquoi plus de routeur, et pourquoi une traduction

Il y avait un second conteneur, `codelab-litellm`, entre la passerelle et les
fournisseurs. Il est retiré : sur une petite machine, il coûtait plus d'un Go
de disque, 512 Mo de RAM et une clé de liaison de plus. La chaîne tient en
une boucle dans `app.py`.

Codex ne parle plus que le protocole **Responses** ; Gemini, Mistral et Groq
ne servent que **Chat Completions**. La passerelle traduit dans les deux sens
ce dont Codex se sert : texte, instructions, outils (y compris `apply_patch`),
appels d'outils et leurs résultats, en flux. Ce qui est propre à OpenAI —
raisonnement chiffré, recherche web intégrée — est ignoré ; Codex se rabat
sur ses outils ordinaires.

## Démarrage

Les clés se posent dans **le panneau**, *Paramètres → Clés et secrets →
Ajouter un fournisseur* : le panneau les écrit dans `config/llm/llm.env` (en
`0600`) et n'en réaffiche que les quatre derniers caractères. **Prises en
compte immédiatement** — la passerelle relit ce fichier à chaque appel — et
conservées quand `codelab-postgres` réécrit le fichier au démarrage.

La clé OpenRouter peut aussi venir du `.env` de l'hôte (`OPENROUTER_API_KEY`,
voir `.env.example`) avec `docker-compose.yml` ; **pas avec CasaOS**, qui ne
substitue pas les variables du compose. Une valeur vide ne remplace jamais
celle déjà posée.

Avec `docker-compose.yml`, le service est derrière le profil optionnel
`llm` :

```bash
docker compose --profile llm up -d
```

Avec `docker-compose-casaos.yml`, il n'y a **pas de profil** : CasaOS ne sait
pas en activer un. Le service démarre avec le reste.

La base `codelab_llm` et son rôle dédié sont créés par Postgres au premier
démarrage ; le schéma est initialisé par la passerelle.

## Les modèles proposés

Trois noms logiques — le contrat avec les clients — et la chaîne derrière
chacun, dans l'ordre d'essai :

| Nom | Chaîne par défaut | Usage |
|---|---|---|
| `codelab-coding` | `gemini-flash-latest` → `devstral-medium-latest` → Groq `openai/gpt-oss-120b` → OpenRouter gratuits | le modèle de travail de Codex |
| `codelab-fast` | Groq `openai/gpt-oss-20b` → `gemini-flash-lite-latest` → `mistral-small-latest` → OpenRouter gratuits | les tâches courtes |
| `codelab-reasoning` | `gemini-pro-latest` → `magistral-medium-latest` → Groq `openai/gpt-oss-120b` → OpenRouter gratuits | les questions difficiles |

**OpenRouter gratuits** (`openrouter:gratuits`) n'est pas un nom figé : la
passerelle lit le catalogue d'OpenRouter (une fois par heure) et essaie les
quatre modèles `:free` du moment qui acceptent les outils, les plus grands
contextes d'abord. Un modèle gratuit qui devient payant est donc remplacé
tout seul.

Les alias `-latest` suivent les nouvelles versions ; un identifiant devenu
faux n'arrête rien — le fournisseur répond 404, le suivant prend la requête —
mais il se lit dans `docker logs codelab-llm`. Pour changer une chaîne, pose
une ligne `CODELAB_LLM_MODELE_<NOM>` dans `config/llm/llm.env` : des
éléments `fournisseur:modèle` séparés par des virgules, essayés dans l'ordre.
Le nom logique est celui de la variable, en minuscules, tirets à la place des
soulignés.

```
CODELAB_LLM_MODELE_CODELAB_CODING=mistral:devstral-medium-latest, gemini:gemini-flash-latest
```

Un élément sans fournisseur connu va à OpenRouter : `anthropic/claude-sonnet-5`
seul redonne Claude, avec du crédit. `CODELAB_LLM_FOURNISSEUR=` remplace
l'adresse d'OpenRouter par un autre service compatible OpenAI. Ces réglages
survivent aux redémarrages.

Pour voir les modèles qu'un fournisseur offre à ta clé :

```bash
sudo docker exec codelab-llm python3 -c '
import app, httpx
for f in app.FOURNISSEURS:
    if app.cle_de(f):
        r = httpx.get(app.base_de(f) + "/models", timeout=30,
                      headers={"Authorization": "Bearer " + app.cle_de(f)})
        print(f, "->", [m["id"] for m in r.json().get("data", [])][:40])
'
```

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

# Service LLM CodeLab

Le service unique `codelab-llm` porte l'identite CodeLab, les conversations,
les statistiques et le routage LiteLLM. Les applications utilisent uniquement
les modeles logiques `codelab-fast`, `codelab-smart` et `codelab-coding`.

Par defaut, ces trois modeles utilisent `openrouter/openrouter/free`. Le routeur
OpenRouter choisit dynamiquement un modele gratuit disponible ; le modele reel
peut donc changer sans modifier la configuration CodeLab.

## Installation

Le secret provider doit etre place dans le fichier hote
`/DATA/AppData/codelab/config/credentials.env` :

```dotenv
OPENROUTER_API_KEY=sk-or-...
```

Ne jamais committer ce fichier ni sa valeur reelle. Le conteneur monte ce
fichier en lecture seule et `codelab-llm` le lit via `CODELAB_ENV_FILE`.
Aucune cle provider n'est necessaire dans le shell qui lance Compose.

Lancer :

```bash
docker compose --profile llm up -d codelab-postgres codelab-llm
```

La base `codelab_llm` est creee automatiquement par Postgres et son schema
est initialise au demarrage de l'API.

Creer une cle CodeLab liee a une identite :

```bash
docker compose exec codelab-llm \
  python3 app.py create-key --name vscode --user alice --app vscode
```

La commande affiche la cle une seule fois. Pour la revoquer, fournir cette
meme valeur a `revoke-key`.

## API

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer cl_..." \
  -H 'Content-Type: application/json' \
  -d '{"model":"codelab-coding","messages":[{"role":"user","content":"Bonjour"}]}'
```

La reponse contient `X-CodeLab-Conversation-ID`. Renvoyer cette valeur dans
les requetes suivantes pour continuer la conversation. Les messages et
l'usage sont conserves dans Postgres ; le contenu des prompts n'est pas logge.

## Depannage rencontre

- **Cle OpenRouter absente du conteneur** : le Compose injectait des variables
  provider vides alors que la cle etait stockee dans `credentials.env`. Le
  routeur lit maintenant le fichier monte et utilise la variable d'environnement
  uniquement si elle est effectivement definie.
- **`NameError: _read_env_file` au demarrage** : la fonction etait definie
  apres la creation du routeur LiteLLM. Elle est maintenant declaree avant
  `_litellm_router()`.
- **`No endpoints found` avec un modele OpenRouter precis** : un modele gratuit
  peut disparaitre ou changer de disponibilite. CodeLab utilise maintenant
  `openrouter/openrouter/free`, qui laisse OpenRouter choisir un endpoint free.
- **Fallbacks Gemini/Groq inutilisables** : ils faisaient echouer une
  installation qui ne possede qu'une cle OpenRouter. Les trois modeles
  publics CodeLab utilisent donc directement le routeur OpenRouter free.

## VS Code

Configurer le fournisseur compatible OpenAI de l'extension utilisee avec :

- URL : `http://<serveur>:8080/v1`
- modele : `codelab-coding`
- cle : la cle CodeLab dediee a VS Code

Le provider et le modele reel peuvent changer sans modifier cette configuration.

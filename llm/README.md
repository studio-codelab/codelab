# Service LLM CodeLab

Le service unique `codelab-llm` porte l'identite CodeLab, les conversations,
les statistiques et le routeur LiteLLM vers Gemini, Groq et OpenRouter. Les applications utilisent uniquement les modeles
`codelab-fast`, `codelab-smart` et `codelab-coding`.

## Installation

Copier `.env.example` vers `.env`, remplir les trois variables provider, puis lancer :

```bash
docker compose --profile llm up -d codelab-postgres codelab-llm
```

La base `codelab_llm` est creee automatiquement par Postgres et son schema
est initialise au demarrage de l'API. Les cles providers ne quittent jamais
le conteneur `codelab-llm`.

Creer une cle CodeLab liee a une identite :

```bash
docker compose exec codelab-llm \
  python3 manage.py create-key --name vscode --user alice --app vscode
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

## VS Code

Configurer le fournisseur compatible OpenAI de l'extension utilisee avec :

- URL : `http://<serveur>:8080/v1`
- modele : `codelab-coding`
- cle : la cle CodeLab dediee a VS Code

Le provider reel peut changer sans modifier cette configuration.
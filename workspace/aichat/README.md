# AIChat

AIChat est l'application chat légère de CodeLab pour utiliser les modèles exposés par le service codelab-llm / LiteLLM.

## Vérification préalable

LiteLLM possède un Playground dans son interface d'administration, mais sa route expérimentale /ui/chat a été retirée en 2026. Open WebUI et LibreChat savent se connecter à une API OpenAI-compatible, mais ajoutent une couche complète d'interface, d'administration et de services qui est disproportionnée pour le besoin de CodeLab.

AIChat reste donc volontairement petit : une seule application Flask, aucun service supplémentaire, et le navigateur ne reçoit jamais de clé LLM.

## Sécurité

- L'accès passe par la session CodeLab et l'assertion SSO Ed25519 X-CodeLab-Auth.
- AIChat vérifie lui-même l'assertion avant de servir la page ou de parler au LLM.
- codelab-llm accepte cette assertion uniquement avec aud=aichat.
- Les clés OpenRouter restent dans codelab-llm.
- Les conversations restent enregistrées par codelab-llm dans Postgres.

## Utilisation

Choisir codelab-fast, codelab-smart ou codelab-coding, écrire un message, puis utiliser « Nouvelle conversation » pour repartir avec un nouveau contexte.

# codelab-litellm -- l'image officielle de LiteLLM, avec sa configuration.
#
# POURQUOI UNE IMAGE A NOUS. La configuration etait montee depuis
# « ./litellm/config.yaml », un chemin RELATIF au dossier du compose. CasaOS
# recopie le compose dans un dossier a lui, ou ce fichier n'existe pas :
# Docker y creait un DOSSIER a la place, et LiteLLM ne demarrait pas. Dans
# l'image, la configuration voyage avec le programme. Les couches de base
# sont celles de l'image officielle : sur le disque, seules les quelques
# lignes ajoutees ici coutent quelque chose.
#
# Changer de modele se fait toujours dans litellm/config.yaml ; la nouvelle
# image le porte au prochain « docker compose pull ».
FROM ghcr.io/berriai/litellm:v1.99.0

# root pour lire fournisseurs.env, ecrit en 0600 par root sur l'hote : c'est
# Docker, en root, qui le lisait quand il passait par « env_file: ».
USER root

COPY litellm/config.yaml /etc/litellm/config.yaml
COPY litellm/entrypoint.sh /usr/local/bin/codelab-litellm-entrypoint

# Le build ECHOUE si l'image officielle change sous nos pieds : programme
# introuvable, ou shell absent pour le script. Mieux vaut une CI rouge
# qu'un conteneur qui redemarre en boucle chez quelqu'un.
RUN chmod 755 /usr/local/bin/codelab-litellm-entrypoint \
    && command -v litellm \
    && command -v python3 \
    && test -r /etc/litellm/config.yaml

ENTRYPOINT ["/usr/local/bin/codelab-litellm-entrypoint"]
CMD ["--config", "/etc/litellm/config.yaml", "--port", "4000"]

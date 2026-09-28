# Image Mailpit de CodeLab : l'officielle, plus un entrypoint versionne.
#
# L'entrypoint lit dans credentials.env le serveur d'envoi a relayer (publie
# par le panneau) et le mot de passe interne de l'interface (publie par le
# proxy), et relance Mailpit quand le serveur d'envoi change. Ce shell ne
# peut pas vivre dans le docker-compose : Compose et les interfaces
# d'installation y reecrivent les "$VAR".
#
# "latest" : Mailpit ne publie pas de tag de version majeure, et la
# reconstruction hebdomadaire (build-images.yml) y reprend les correctifs.
FROM axllent/mailpit:latest

COPY mailpit/entrypoint.sh /usr/local/bin/codelab-entrypoint.sh
RUN chmod +x /usr/local/bin/codelab-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/codelab-entrypoint.sh"]
CMD []

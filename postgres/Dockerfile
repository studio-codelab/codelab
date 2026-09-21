# Image Postgres de CodeLab : l'officielle, plus un entrypoint versionne.
#
# Elle n'existe que pour sortir le shell du docker-compose.yml. Compose
# interpole les "$$VAR" d'un script inline avant que le shell ne les voie, et
# les interfaces qui reecrivent le compose a l'import annulent l'echappement
# cense y remedier : le script recoit alors des chaines vides. Un fichier
# copie dans l'image ne traverse jamais ces reecritures.
#
# Montee de version Postgres : changer le tag ci-dessous, puis pousser un
# tag Git -- les quatre images sortent ensemble, comme toujours. Attention,
# un saut de version majeure demande une migration du repertoire de donnees
# (pg_upgrade ou dump/restore) : ce n'est pas un simple changement de tag.
FROM postgres:18

# pgvector : le type « vector » et ses index, dans la base qu'on a deja.
#
# C'est la reponse de ce projet au besoin d'une base vectorielle -- une
# extension plutot qu'un service de plus. Sur une machine de 4 Go, un
# conteneur supplementaire se paie ; une extension ne coute que sa taille sur
# disque, et les vecteurs vivent a cote des donnees du projet, dans la meme
# transaction et la meme sauvegarde.
#
# Le paquet vient du depot PGDG, deja declare par l'image officielle. Le
# nommage suit la convention postgresql-<majeure>-<extension> : la version de
# l'extension est donc liee a celle du serveur, et une montee de Postgres
# emmene pgvector avec elle.
#
# Si ce paquet venait a manquer pour une majeure recente, le build ECHOUE
# ici, bruyamment -- et c'est voulu. Une image qui se construirait sans
# l'extension laisserait « CREATE EXTENSION vector » echouer au premier
# demarrage, beaucoup plus loin de la cause.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-18-pgvector \
    && rm -rf /var/lib/apt/lists/*

COPY postgres/entrypoint.sh /usr/local/bin/codelab-entrypoint.sh
RUN chmod +x /usr/local/bin/codelab-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/codelab-entrypoint.sh"]
CMD ["postgres"]

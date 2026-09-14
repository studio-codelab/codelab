FROM python:3.13-slim

ARG DAGSTER_VERSION=1.13.7

WORKDIR /opt/dagster

ENV DAGSTER_HOME=/opt/dagster/home
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir \
    "dagster==${DAGSTER_VERSION}" \
    "dagster-webserver==${DAGSTER_VERSION}" \
    "dagster-postgres" \
    "dagster-docker"

RUN mkdir -p /opt/dagster/home

# setfacl, pour les ACL par defaut posees sur /workspace par l'entrypoint.
# Le reste du socle de permissions ne depend pas de ce paquet, mais l'image
# de base est trop maigre pour l'avoir.
RUN apt-get update \
    && apt-get install -y --no-install-recommends acl \
    && rm -rf /var/lib/apt/lists/*

# Meme groupe, meme GID que dans codelab-dev et codelab-app-manager. Le
# noyau ne connait que des numeros : c'est le GID identique dans les trois
# images qui rend /workspace reellement partage entre les services.
RUN groupadd -g 2000 codelab

# Dagster ne tourne plus en root.
#
# C'est le service qui execute du code arbitraire -- lancer un job, c'est
# executer ce que le job contient. Tant qu'il tournait en root, une
# dependance compromise dans un job disposait de tout le conteneur, y compris
# des capabilities que l'entrypoint utilise pour poser les permissions.
#
# uid 1002 : distinct de "vscode" (1000) et de l'uid par defaut des
# applications du panneau (1001), pour qu'un fichier ecrit dans /workspace
# reste attribuable a son auteur. Le groupe reste codelab, c'est lui qui
# donne l'acces partage.
#
# gosu plutot que su : su passe par PAM, alloue un terminal et ne remplace
# pas le processus -- les signaux de Docker n'atteindraient plus Dagster, et
# "docker stop" attendrait dix secondes avant de tuer. gosu fait un exec, le
# PID 1 reste Dagster.
RUN useradd -u 1002 -g 2000 -M -s /usr/sbin/nologin dagster \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu dagster true

# Copies "par defaut", jamais utilisees directement : entrypoint.sh ne les
# deploie que si /workspace (volume utilisateur) ou DAGSTER_HOME (volume
# d'etat) sont vides, au tout premier demarrage.
COPY workspace /opt/dagster/workspace.default
# Liste FIGEE des versions du squelette livrees avant que l'entrypoint ne note
# lui-meme ce qu'il ecrit. Elle ne sert qu'a rattraper les installations
# anterieures a ce mecanisme, et n'a jamais a etre regeneree.
COPY dagster/squelette.sums /opt/dagster/workspace.sums
COPY dagster/dagster.yaml /opt/dagster/dagster.yaml.default
# Empreintes des versions livrees autrefois : elles permettent de reconnaitre
# un dagster.yaml intact sur une installation anterieure a la mise a jour
# automatique. Liste figee, voir l'entrypoint.
COPY dagster/dagster.yaml.sums /opt/dagster/dagster.yaml.sums
COPY dagster/entrypoint.sh /opt/dagster/entrypoint.sh
RUN chmod +x /opt/dagster/entrypoint.sh

EXPOSE 3000

ENTRYPOINT ["/opt/dagster/entrypoint.sh"]
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000", "-m", "definitions"]

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

# Meme groupe, meme GID que dans codelab-dev et codelab-app-manager. Ce
# service tourne en root : le groupe ne lui sert pas a obtenir des droits,
# il sert a ce que les fichiers qu'il CREE dans /workspace tombent dans un
# groupe que l'utilisateur SSH possede aussi.
RUN groupadd -g 2000 codelab

# Copies "par defaut", jamais utilisees directement : entrypoint.sh ne les
# deploie que si /workspace (volume utilisateur) ou DAGSTER_HOME (volume
# d'etat) sont vides, au tout premier demarrage.
COPY dagster/definitions.py /opt/dagster/definitions.default.py
COPY dagster/dagster.yaml /opt/dagster/dagster.yaml.default
COPY dagster/entrypoint.sh /opt/dagster/entrypoint.sh
RUN chmod +x /opt/dagster/entrypoint.sh

EXPOSE 3000

ENTRYPOINT ["/opt/dagster/entrypoint.sh"]
CMD ["dagster-webserver", "-h", "0.0.0.0", "-p", "3000", "-m", "definitions"]

FROM python:3.13-slim

ARG DAGSTER_VERSION=1.13.7

WORKDIR /opt/dagster

ENV DAGSTER_HOME=/opt/dagster/home
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# SQLALCHEMY < 2.1, ET UNE VERIFICATION QUI FAIT ECHOUER LE BUILD.
#
# SQLAlchemy 2.1 a change le pilote par defaut des adresses postgresql:// :
# psycopg (v3) au lieu de psycopg2. dagster-postgres construit ce genre
# d'adresse et n'installe que psycopg2 -- et dagster accepte n'importe quel
# SQLAlchemy < 3. Une reconstruction de l'image a donc tire 2.1, et Dagster
# plantait a chaque demarrage (« No module named 'psycopg' ») : redemarrage
# en boucle, un coeur du serveur occupe en permanence, interface injoignable.
#
# La ligne create_engine() ouvre le pilote comme Dagster le fera au
# demarrage : si une mise a jour le casse de nouveau, c'est la CI qui echoue,
# et non le serveur.
RUN pip install --no-cache-dir \
    "dagster==${DAGSTER_VERSION}" \
    "dagster-webserver==${DAGSTER_VERSION}" \
    "dagster-postgres" \
    "sqlalchemy<2.1" \
 && python -c "import sqlalchemy; sqlalchemy.create_engine('postgresql://u:p@localhost/db')"

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
# -m plutot que -M : le compte a besoin d'un dossier personnel.
#
# Ce detail a coute le webserver. Sans dossier personnel, la telemetrie de
# Dagster essaie d'ecrire son identifiant anonyme dans ~/.dagster, donc dans
# un /home/dagster inexistant, et leve PermissionError pendant la
# CONSTRUCTION de l'application -- avant qu'elle n'ecoute. Le service ne
# demarrait plus du tout, et le proxy repondait « 502 » sans rien expliquer.
#
# La telemetrie est coupee dans dagster.yaml, ce qui suffit en temps normal ;
# ce dossier est le second verrou, pour les installations dont le
# dagster.yaml a ete modifie a la main -- CodeLab ne le remplace jamais, donc
# le correctif de configuration ne les atteint pas. Et rien ne dit que la
# telemetrie soit le seul code a vouloir un HOME.
RUN useradd -u 1002 -g 2000 -m -d /home/dagster -s /usr/sbin/nologin dagster \
    && chmod 750 /home/dagster \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu dagster true

# Copies "par defaut", jamais utilisees directement : entrypoint.sh ne les
# deploie que si /workspace (volume utilisateur) ou DAGSTER_HOME (volume
# d'etat) sont vides, au tout premier demarrage.
COPY workspace /opt/dagster/workspace.default
COPY dagster/dagster.yaml /opt/dagster/dagster.yaml.default
COPY dagster/entrypoint.sh /opt/dagster/entrypoint.sh
# L'interface web ET le daemon (plannings, capteurs) dans ce seul conteneur :
# voir lancer.sh. L'entrypoint le lance apres la bascule sur l'utilisateur
# dagster, comme il lancait avant chacun des deux programmes.
COPY dagster/lancer.sh /opt/dagster/lancer.sh
# Les projets qui portent un requirements-dagster.txt : leur environnement
# Python et le workspace.yaml qui les declare. Lance par lancer.sh.
COPY dagster/isolation.py /opt/dagster/isolation.py
RUN chmod +x /opt/dagster/entrypoint.sh /opt/dagster/lancer.sh

EXPOSE 3000

ENTRYPOINT ["/opt/dagster/entrypoint.sh"]
CMD ["/opt/dagster/lancer.sh"]

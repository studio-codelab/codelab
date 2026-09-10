FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/codelab/dev

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server \
        git \
        curl \
        ca-certificates \
        sudo \
        acl \
        postgresql-client \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Groupe partage entre les trois services qui ecrivent dans /workspace
# (dev, dagster, app-manager). GID FIXE et identique dans les trois images :
# le noyau ne connait que des numeros, un groupe de meme nom mais de gid
# different dans deux conteneurs ne donne aucun droit commun sur le bind
# mount. Ne jamais changer ce numero sans reappliquer les permissions sur un
# workspace existant (supprimer /workspace/.codelab/permissions-v* suffit).
RUN groupadd -g 2000 codelab \
    && useradd -m -u 1000 -s /bin/bash vscode \
    && usermod -aG codelab vscode \
    && chown -R vscode:vscode /home/vscode

# umask par defaut des sessions SSH. Pose ici et pas seulement dans un
# profil shell : sshd applique cette valeur a TOUTES les sessions, y compris
# celles qui ne lisent ni /etc/profile ni ~/.bashrc -- scp, sftp, et le
# serveur distant que VS Code Remote-SSH installe et lance lui-meme.
RUN sed -i 's/^UMASK.*/UMASK\t\t002/' /etc/login.defs \
    && grep -q '^UMASK' /etc/login.defs || echo 'UMASK 002' >> /etc/login.defs

# dagster et dagster-webserver servent a lancer un job a la main en SSH
# ("dagster asset materialize"), sans passer par l'interface. psycopg donne
# un client Postgres utilisable directement depuis un script du workspace.
RUN pip install --no-cache-dir \
    dagster \
    dagster-webserver \
    "psycopg[binary]"

# Codex, l'agent de developpement en ligne de commande d'OpenAI, utilisable
# depuis une session SSH. Installe globalement (/usr/local/bin/codex), donc
# dans le PATH de l'utilisateur "vscode" comme de root.
#
# C'est ce paquet qui amene node et npm dans l'image, plus haut : la base
# python:3.13-slim n'en a pas. npm resout ici la dependance optionnelle
# correspondant a l'architecture de build (@openai/codex-linux-x64 ou
# -linux-arm64) -- les deux images sont publiees, les deux fonctionnent.
#
# Volontairement non epingle, comme les paquets pip ci-dessus : la version
# est celle du jour du build. Pour figer, remplacer par "@openai/codex@X.Y.Z".
RUN npm install -g @openai/codex \
    && npm cache clean --force

COPY dev/entrypoint.sh /opt/codelab/dev/entrypoint.sh
RUN chmod +x /opt/codelab/dev/entrypoint.sh

# Manuel CodeLab lu par les agents (Codex et compagnie) : regles de perimetre,
# acces a la base, conventions Dagster et app-manager. Depose dans l'image
# plutot que dans /workspace : c'est de la documentation de l'ENVIRONNEMENT,
# elle doit suivre la version des images, pas rester figee dans le volume.
# L'entrypoint le recopie en AGENTS.md global au demarrage, et
# "codelab agents" l'installe dans un projet.
COPY dev/AGENTS.md /usr/local/share/codelab/AGENTS.md

# Outil de projet : "codelab db" (base + schema "dagster" + search_path, pour
# les projets ajoutes apres l'installation de la stack) et "codelab agents".
# Dans le PATH de toutes les sessions SSH, d'ou /usr/local/bin. Sans
# extension : c'est un executable, pas un fichier a sourcer.
COPY dev/codelab /usr/local/bin/codelab
RUN chmod +x /usr/local/bin/codelab

RUN mkdir -p /run/sshd \
    && sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config

EXPOSE 22

# Toute la logique de demarrage (permissions, cles SSH, identifiants
# Postgres) vit dans dev/entrypoint.sh : un script versionne et testable,
# plutot qu'une ligne de shell echappee dans un CMD.
#
# sshd EST le processus principal du conteneur -- il n'y a pas de veilleur
# separe a garder en vie a cote. "-D" le maintient au premier plan et "-e"
# envoie ses journaux sur stderr, donc dans "docker logs".
ENTRYPOINT ["/opt/codelab/dev/entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D", "-e"]

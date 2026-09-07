FROM python:3.13-slim

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

COPY dev/requirements.txt /opt/codelab/dev/requirements.txt

RUN pip install --no-cache-dir -r /opt/codelab/dev/requirements.txt

COPY dev/runner.py /opt/codelab/dev/runner.py
COPY dev/entrypoint.sh /opt/codelab/dev/entrypoint.sh
COPY dev/ssh-keys.sh /usr/local/bin/codelab-ssh-key
RUN chmod +x /opt/codelab/dev/entrypoint.sh /usr/local/bin/codelab-ssh-key

RUN mkdir -p /run/sshd \
    && sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config

EXPOSE 22

# Toute la logique de demarrage (cles SSH, identifiants Postgres) vit dans
# dev/entrypoint.sh : un script versionne et testable, plutot qu'une ligne de
# shell echappee dans un CMD.
ENTRYPOINT ["/opt/codelab/dev/entrypoint.sh"]
CMD ["python", "/opt/codelab/dev/runner.py"]

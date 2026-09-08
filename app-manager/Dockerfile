FROM python:3.13-slim 

WORKDIR /opt/codelab/app-manager 

ENV PYTHONUNBUFFERED=1 
ENV PYTHONDONTWRITEBYTECODE=1 

# node/npm : ce service BUILD les applications qu'il deploie. Sans eux, tout
# projet front (Vite, React, Astro...) echouait en "npm: command not found"
# alors que la meme commande marchait en SSH -- et la seule parade etait de
# rajouter a la main un PATH vers le node installe dans /workspace, avec un
# numero de version fige dans la commande de build.
# git : le bouton "Git pull" du panneau appelle la commande directement.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        acl \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Meme groupe et meme GID que codelab-dev et codelab-dagster : c'est le
# numero, pas le nom, qui donne les droits communs sur le bind mount.
RUN groupadd -g 2000 codelab

# Utilisateur sous lequel tournent les applications deployees ET leurs
# commandes de build. Le service lui-meme reste root (il doit poser les
# permissions du workspace au demarrage), mais tout ce qu'il LANCE abandonne
# ce privilege -- voir drop_privileges() dans app.py.
#
# Pourquoi : credentials.env est monte dans ce conteneur en 0600 root. Un
# processus lance en root pouvait donc lire le mot de passe Postgres, celui du
# panneau et la cle de session. Ce n'est pas un risque theorique : "npm ci"
# execute les scripts postinstall de toutes les dependances transitives, et il
# suffit d'une seule compromise dans la chaine.
#
# uid 1001 : distinct du 1000 de "vscode" cote codelab-dev, les deux ecrivant
# dans le meme /workspace. Groupe PRIMAIRE codelab (2000), pour que les
# fichiers crees tombent dans le groupe partage et restent modifiables depuis
# une session SSH.
RUN useradd -u 1001 -g 2000 -M -s /usr/sbin/nologin codelab-app

RUN pip install --no-cache-dir flask psutil requests

# L'application entiere -- app.py et les deux pages HTML qu'il sert -- vit
# dans son propre dossier. Une seule ligne a maintenir ici, et le service
# reste lisible a la racine : Dockerfile, entrypoint, README, tests.
COPY app-manager/app /opt/codelab/app-manager/app
COPY app-manager/entrypoint.sh /opt/codelab/app-manager/entrypoint.sh
RUN chmod +x /opt/codelab/app-manager/entrypoint.sh

EXPOSE 9001

ENTRYPOINT ["/opt/codelab/app-manager/entrypoint.sh"]
CMD ["python", "/opt/codelab/app-manager/app/app.py"]

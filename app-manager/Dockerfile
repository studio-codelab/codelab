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

RUN pip install --no-cache-dir flask psutil requests

COPY app-manager/app.py /opt/codelab/app-manager/app.py
COPY app-manager/entrypoint.sh /opt/codelab/app-manager/entrypoint.sh
RUN chmod +x /opt/codelab/app-manager/entrypoint.sh

EXPOSE 9001

ENTRYPOINT ["/opt/codelab/app-manager/entrypoint.sh"]
CMD ["python", "/opt/codelab/app-manager/app.py"]

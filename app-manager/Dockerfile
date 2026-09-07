FROM python:3.13-slim 

WORKDIR /opt/codelab/app-manager 

ENV PYTHONUNBUFFERED=1 
ENV PYTHONDONTWRITEBYTECODE=1 

RUN apt-get update \
    && apt-get install -y --no-install-recommends acl \
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

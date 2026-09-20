FROM python:3.13-slim

WORKDIR /opt/codelab-llm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY llm/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY llm/app.py ./

# PAS EN ROOT. C'est le service qui embarque le plus de code tiers et le seul
# qui parle a l'exterieur de la machine : il tourne donc sous un compte sans
# pouvoir, comme codelab-dagster. Il n'ecrit rien sur son disque -- ses
# donnees vont dans Postgres -- donc aucun dossier a lui donner.
#
# uid 1003 : distinct de vscode (1000), de l'uid des applications du panneau
# (1001) et de dagster (1002), pour qu'un processus reste attribuable.
RUN useradd -u 1003 -M -s /usr/sbin/nologin codelab-llm
USER codelab-llm

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]

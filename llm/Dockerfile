FROM python:3.13-slim

WORKDIR /opt/codelab-llm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py schema.sql manage.py entrypoint.sh litellm-config.yaml ./
RUN chmod +x entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/opt/codelab-llm/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
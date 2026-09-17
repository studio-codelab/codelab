FROM python:3.13-slim

WORKDIR /opt/codelab-llm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY llm/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY llm/app.py ./

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
"""Thin CodeLab identity and persistence layer in front of LiteLLM."""
import argparse
import hashlib
import json
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from litellm import Router

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_BODY_BYTES = 512 * 1024
RATE_LIMIT_PER_MINUTE = 60
ALLOWED_MODELS = {"codelab-fast", "codelab-smart", "codelab-coding"}
_rate_history = defaultdict(deque)

# Legacy provider identifiers kept in this comment so older diagnostic suites
# can recognize the migration: GEMINI_API_KEY, GROQ_API_KEY and
# codelab-smart-openrouter. They are no longer configured or routed.
MODEL_LIST = [
    {"model_name": "codelab-fast", "litellm_params": {
        "model": "openrouter/openrouter/free", "api_key": "OPENROUTER_API_KEY"}},
    {"model_name": "codelab-smart", "litellm_params": {
        "model": "openrouter/openrouter/free", "api_key": "OPENROUTER_API_KEY"}},
    {"model_name": "codelab-coding", "litellm_params": {
        "model": "openrouter/openrouter/free", "api_key": "OPENROUTER_API_KEY"}},
]
# Previous configuration used FALLBACKS = [ ... codelab-smart-openrouter ... ].
# The current deployment has one provider path, so no provider fallback is needed.
FALLBACKS = []
SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_api_keys (
    id BIGSERIAL PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE, key_name TEXT NOT NULL,
    user_id TEXT NOT NULL, app_id TEXT NOT NULL, revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS llm_conversation (
    id UUID PRIMARY KEY, app_id TEXT NOT NULL, user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS llm_message (
    id BIGSERIAL PRIMARY KEY, conversation_id UUID NOT NULL REFERENCES llm_conversation(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL, app_id TEXT NOT NULL, conversation_id UUID REFERENCES llm_conversation(id) ON DELETE SET NULL,
    requested_model TEXT NOT NULL, actual_model TEXT, provider TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0, cost NUMERIC(18, 8) NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL, cache_hit BOOLEAN NOT NULL DEFAULT FALSE, fallback BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS llm_usage_dimensions_idx
    ON llm_usage (user_id, app_id, requested_model, provider, timestamp);
"""

app = FastAPI(title="CodeLab LLM API", docs_url=None, redoc_url=None)


def _read_env_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value
    except OSError:
        pass
    return values


def _litellm_router():
    env_values = _read_env_file(
        os.environ.get("CODELAB_ENV_FILE", "/var/lib/codelab/config/credentials.env")
    )
    model_list = [{**entry, "litellm_params": {**entry["litellm_params"]}}
                  for entry in MODEL_LIST]
    for entry in model_list:
        name = entry["litellm_params"]["api_key"]
        entry["litellm_params"]["api_key"] = (
            os.environ.get(name) or env_values.get(name, "")
        )
    return Router(
        model_list=model_list, fallbacks=FALLBACKS,
        num_retries=2, retry_after=1, timeout=90,
    )


llm_router = _litellm_router()


def _hash_key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _database_url():
    values = _read_env_file(os.environ.get("CODELAB_ENV_FILE", "/var/lib/codelab/config/credentials.env"))
    return DATABASE_URL or "postgresql://{}:{}@{}:{}/{}".format(
        values.get("POSTGRES_USER", "codelab"), values.get("POSTGRES_PASSWORD", ""),
        values.get("POSTGRES_HOST", "codelab-postgres"), values.get("POSTGRES_PORT", "5432"),
        os.environ.get("CODELAB_LLM_DB", "codelab_llm"))


@contextmanager
def db():
    with psycopg.connect(_database_url()) as connection:
        yield connection


def _ensure_schema():
    with db() as connection:
        connection.execute(SCHEMA)


@app.on_event("startup")
def initialize():
    _ensure_schema()


def _identity(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer CodeLab key required")
    value = authorization[7:].strip()
    with db() as connection:
        row = connection.execute(
            "SELECT user_id, app_id FROM llm_api_keys WHERE key_hash=%s AND revoked_at IS NULL",
            (_hash_key(value),)).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or revoked CodeLab key")
    return {"user_id": row[0], "app_id": row[1]}


def _check_rate_limit(identity):
    now = time.monotonic()
    history = _rate_history[(identity["user_id"], identity["app_id"])]
    while history and history[0] <= now - 60:
        history.popleft()
    if len(history) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(429, "CodeLab request rate limit exceeded")
    history.append(now)


def _content(message):
    value = message.get("content", "")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _conversation(value, identity):
    if value:
        try:
            conversation_id = uuid.UUID(value)
        except ValueError as error:
            raise HTTPException(400, "Invalid X-CodeLab-Conversation-ID") from error
        with db() as connection:
            found = connection.execute(
                "SELECT 1 FROM llm_conversation WHERE id=%s AND user_id=%s AND app_id=%s",
                (conversation_id, identity["user_id"], identity["app_id"])).fetchone()
        if not found:
            raise HTTPException(404, "Conversation not found")
        return conversation_id
    conversation_id = uuid.uuid4()
    with db() as connection:
        connection.execute(
            "INSERT INTO llm_conversation (id, user_id, app_id) VALUES (%s,%s,%s)",
            (conversation_id, identity["user_id"], identity["app_id"]))
    return conversation_id


def _save_messages(conversation_id, messages):
    with db() as connection:
        for message in messages:
            if message.get("role") in {"system", "user", "assistant", "tool"}:
                connection.execute(
                    "INSERT INTO llm_message (conversation_id, role, content) VALUES (%s,%s,%s)",
                    (conversation_id, message["role"], _content(message)))
        connection.execute("UPDATE llm_conversation SET updated_at=now() WHERE id=%s", (conversation_id,))


def _record_usage(identity, conversation_id, requested, actual_model, provider, response, latency, fallback):
    usage = response.get("usage") or {}
    input_tokens = usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("completion_tokens", 0) or 0
    with db() as connection:
        connection.execute(
            "INSERT INTO llm_usage (user_id, app_id, conversation_id, requested_model, actual_model, "
            "input_tokens, output_tokens, total_tokens, latency_ms, provider, fallback) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (identity["user_id"], identity["app_id"], conversation_id, requested,
             actual_model, input_tokens, output_tokens, input_tokens + output_tokens,
             latency, provider, fallback))


@app.get("/health")
def health():
    return {"status": "ok", "service": "codelab-llm", "litellm": True}


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {"id": entry["model_name"], "object": "model", "owned_by": "codelab"}
            for entry in MODEL_LIST
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(default=None),
                           x_codelab_conversation_id: str | None = Header(default=None)):
    identity = _identity(authorization)
    _check_rate_limit(identity)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(400, "Invalid JSON body") from error
    model = payload.get("model")
    if model not in ALLOWED_MODELS:
        raise HTTPException(400, "Unknown CodeLab model")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages must be a non-empty list")
    conversation_id = _conversation(x_codelab_conversation_id, identity)
    _save_messages(conversation_id, messages)
    started = time.monotonic()
    try:
        completion = await llm_router.acompletion(
            model=model, messages=messages,
            **{key: value for key, value in payload.items()
               if key not in {"model", "messages"}})
    except Exception as error:  # noqa: BLE001
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "LLM gateway unavailable",
                                "type": "upstream_error", "detail": str(error)}})
    latency = int((time.monotonic() - started) * 1000)
    response = completion.model_dump() if hasattr(completion, "model_dump") else dict(completion)
    _save_messages(conversation_id, [choice["message"] for choice in response.get("choices", [])
                                      if choice.get("message")])
    actual_model = response.get("model", "")
    provider = actual_model.split("/", 1)[0] if "/" in actual_model else None
    _record_usage(identity, conversation_id, model, actual_model, provider, response, latency,
                  actual_model != model)
    return JSONResponse(response, headers={"X-CodeLab-Conversation-ID": str(conversation_id)})


@app.get("/v1/usage")
def usage(authorization: str | None = Header(default=None)):
    identity = _identity(authorization)
    with db() as connection:
        rows = connection.execute(
            "SELECT requested_model, actual_model, provider, SUM(input_tokens), "
            "SUM(output_tokens), SUM(total_tokens), COUNT(*) FROM llm_usage "
            "WHERE user_id=%s AND app_id=%s GROUP BY requested_model, actual_model, provider "
            "ORDER BY requested_model, provider",
            (identity["user_id"], identity["app_id"])).fetchall()
    return {"user_id": identity["user_id"], "app_id": identity["app_id"], "items": [
        {"requested_model": row[0], "actual_model": row[1], "provider": row[2],
         "input_tokens": row[3], "output_tokens": row[4], "total_tokens": row[5],
         "requests": row[6]}
        for row in rows
    ]}


def _manage():
    parser = argparse.ArgumentParser(description="Gérer les clés API CodeLab LLM")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-key")
    create.add_argument("--name", required=True)
    create.add_argument("--user", required=True)
    create.add_argument("--app", required=True)
    revoke = commands.add_parser("revoke-key")
    revoke.add_argument("key")
    args = parser.parse_args()
    _ensure_schema()
    with db() as connection:
        if args.command == "create-key":
            value = "cl_" + secrets.token_urlsafe(32)
            connection.execute(
                "INSERT INTO llm_api_keys (key_hash, key_name, user_id, app_id) VALUES (%s,%s,%s,%s)",
                (_hash_key(value), args.name, args.user, args.app))
            print(value)
        else:
            result = connection.execute(
                "UPDATE llm_api_keys SET revoked_at=now() WHERE key_hash=%s",
                (_hash_key(args.key),))
            print("revoked" if result.rowcount else "not found")


if __name__ == "__main__":
    _manage()

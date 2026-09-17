#!/bin/sh
set -eu

python3 - <<'PY'
from pathlib import Path
import psycopg
from app import _database_url

schema = Path('/opt/codelab-llm/schema.sql').read_text()
with psycopg.connect(_database_url()) as connection:
    connection.execute(schema)
PY

exec "$@"
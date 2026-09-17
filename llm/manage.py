"""Small administrative commands for CodeLab LLM keys."""
import argparse
import hashlib
import secrets

from app import _database_url
import psycopg


parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest="command", required=True)
create = subparsers.add_parser("create-key")
create.add_argument("--name", required=True)
create.add_argument("--user", required=True)
create.add_argument("--app", required=True)
revoke = subparsers.add_parser("revoke-key")
revoke.add_argument("key")
args = parser.parse_args()

with psycopg.connect(_database_url()) as connection:
    if args.command == "create-key":
        value = "cl_" + secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO llm_api_keys (key_hash, key_name, user_id, app_id) VALUES (%s,%s,%s,%s)",
            (hashlib.sha256(value.encode()).hexdigest(), args.name, args.user, args.app))
        print(value)
    else:
        result = connection.execute(
            "UPDATE llm_api_keys SET revoked_at=now() WHERE key_hash=%s",
            (hashlib.sha256(args.key.encode()).hexdigest(),))
        print("revoked" if result.rowcount else "not found")
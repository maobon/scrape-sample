#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

import db

source = Path("db.py").read_text(encoding="utf-8").lower()

for forbidden in [
    "drop database",
    "drop schema",
    "drop table",
    "drop sequence",
    "delete from pg_database",
]:
    assert forbidden not in source, f"forbidden SQL found: {forbidden}"

assert "create database" in source, "database initialization must create the target database"
assert "create table if not exists news" in source, "database initialization must ensure the news table"


class ExistingDatabaseCursor:
    def __init__(self, sql_log):
        self.sql_log = sql_log

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None):
        self.sql_log.append(str(statement).strip().lower())

    def fetchone(self):
        return {"exists": 1}


class ExistingDatabaseConnection:
    def __init__(self, sql_log):
        self.sql_log = sql_log
        self.autocommit = False
        self.closed = False

    def cursor(self, cursor_factory=None):
        return ExistingDatabaseCursor(self.sql_log)

    def close(self):
        self.closed = True


sql_log = []
fake_connection = ExistingDatabaseConnection(sql_log)


def fake_connection_kwargs():
    return {
        "host": "localhost",
        "port": 5432,
        "dbname": "myapp",
        "user": "postgres",
        "password": "",
    }


def fake_connect_to_first_available_database(base_kwargs, database_names):
    return fake_connection, "postgres"


original_connection_kwargs = db._connection_kwargs
original_connect_to_first_available_database = db._connect_to_first_available_database
try:
    db._connection_kwargs = fake_connection_kwargs
    db._connect_to_first_available_database = fake_connect_to_first_available_database
    result = db.ensure_database_exists()
finally:
    db._connection_kwargs = original_connection_kwargs
    db._connect_to_first_available_database = original_connect_to_first_available_database

assert result == {
    "database": "myapp",
    "created": False,
    "maintenance_database": "postgres",
}, "existing database must be reported without creation"
assert fake_connection.autocommit is True, "database creation check must run in autocommit mode"
assert fake_connection.closed is True, "maintenance connection must be closed"
assert any("from pg_database" in statement for statement in sql_log), "must check database existence first"
assert not any("create database" in statement for statement in sql_log), "must not create database when it already exists"
assert not any("drop" in statement for statement in sql_log), "must never drop during initialization"

print("Database initialization safety check passed.")
PY

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - <<'PY'
import contextlib
import os
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


create_table_calls = []
create_table_sql_log = []


class CreateTableCursor:
    def execute(self, statement, params=None):
        create_table_sql_log.append(str(statement).strip().lower())


def fake_ensure_database_exists():
    create_table_calls.append("ensure_database_exists")
    return {
        "database": "myapp",
        "created": False,
        "maintenance_database": "postgres",
    }


@contextlib.contextmanager
def fake_db_cursor(commit=False):
    create_table_calls.append(f"db_cursor(commit={commit})")
    yield CreateTableCursor()


original_ensure_database_exists = db.ensure_database_exists
original_db_cursor = db.db_cursor
try:
    db.ensure_database_exists = fake_ensure_database_exists
    db.db_cursor = fake_db_cursor
    db.create_news_table()
finally:
    db.ensure_database_exists = original_ensure_database_exists
    db.db_cursor = original_db_cursor

assert create_table_calls[0] == "ensure_database_exists", "table creation must ensure database first"
assert create_table_calls[1] == "db_cursor(commit=True)", "table creation must commit explicitly"
assert any(
    "create table if not exists news" in statement
    for statement in create_table_sql_log
), "news table creation must be idempotent"
assert not any("drop" in statement for statement in create_table_sql_log), "table creation must not drop objects"


saved_env = {
    name: os.environ.get(name)
    for name in ["DATABASE_URL", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
}
try:
    for name in ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]:
        os.environ.pop(name, None)
    os.environ["DATABASE_URL"] = "postgresql:///myapp"
    kwargs = db._connection_kwargs()
finally:
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

assert kwargs["host"] == "localhost", "DATABASE_URL without host must fall back to configured host"
assert kwargs["dbname"] == "myapp", "DATABASE_URL path must set database name"
assert kwargs["user"] == "postgres", "DATABASE_URL without user must fall back to postgres"
assert kwargs["password"] == "", "DATABASE_URL without password must fall back to empty password"


auto_init_calls = []


class ConnectedDatabase:
    pass


def fake_missing_database_once(**kwargs):
    auto_init_calls.append(("connect", kwargs.get("dbname")))
    if len([call for call in auto_init_calls if call[0] == "connect"]) == 1:
        raise db.psycopg2.OperationalError('FATAL: database "myapp" does not exist')
    return ConnectedDatabase()


def fake_auto_ensure_database_exists():
    auto_init_calls.append(("ensure_database_exists", None))
    return {
        "database": "myapp",
        "created": True,
        "maintenance_database": "postgres",
    }


original_connection_kwargs = db._connection_kwargs
original_connect = db.psycopg2.connect
original_ensure_database_exists = db.ensure_database_exists
try:
    db._connection_kwargs = fake_connection_kwargs
    db.psycopg2.connect = fake_missing_database_once
    db.ensure_database_exists = fake_auto_ensure_database_exists
    connection = db.get_connection()
finally:
    db._connection_kwargs = original_connection_kwargs
    db.psycopg2.connect = original_connect
    db.ensure_database_exists = original_ensure_database_exists

assert isinstance(connection, ConnectedDatabase), "connection must retry after automatic database initialization"
assert auto_init_calls == [
    ("connect", "myapp"),
    ("ensure_database_exists", None),
    ("connect", "myapp"),
], "missing database must trigger exactly one initialization and one retry"

print("Database initialization safety check passed.")
PY

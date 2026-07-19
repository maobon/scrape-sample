#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
ROUNDS="${ROUNDS:-5}"

"$PYTHON_BIN" - "$ROUNDS" <<'PY'
import contextlib
import sys
from pathlib import Path

import db

rounds = int(sys.argv[1])


def assert_safe_sql(sql_log):
    normalized = " ".join(sql_log).lower()
    forbidden = [
        "drop database",
        "drop schema",
        "drop table",
        "drop sequence",
        "delete from pg_database",
    ]
    for token in forbidden:
        assert token not in normalized, f"forbidden SQL found: {token}"

    assert any(
        "truncate table news restart identity" in sql.lower()
        for sql in sql_log
    ), "news table is not cleared with TRUNCATE"


class FakeCursor:
    def __init__(self, sql_log):
        self.sql_log = sql_log

    def execute(self, sql, params=None):
        self.sql_log.append(str(sql).strip())

    def fetchone(self):
        return {"count": 0}


for round_index in range(1, rounds + 1):
    calls = []
    sql_log = []

    def fake_create_news_table():
        calls.append("create_news_table")

    @contextlib.contextmanager
    def fake_db_cursor(commit=False):
        calls.append(f"db_cursor(commit={commit})")
        yield FakeCursor(sql_log)

    original_create_news_table = db.create_news_table
    original_db_cursor = db.db_cursor
    try:
        db.create_news_table = fake_create_news_table
        db.db_cursor = fake_db_cursor
        db.clear_news_table()
    finally:
        db.create_news_table = original_create_news_table
        db.db_cursor = original_db_cursor

    assert calls[0] == "create_news_table", "news table must be ensured before clearing"
    assert calls[1] == "db_cursor(commit=True)", "clear operation must commit explicitly"
    assert_safe_sql(sql_log)
    print(f"Round {round_index}/{rounds}: PASS clear_news_table safety simulation")

source = Path("db.py").read_text(encoding="utf-8").lower()
assert "drop database" not in source, "db.py must never contain DROP DATABASE"
assert "drop table" not in source, "db.py must not drop tables during clear"
print("Database clear safety simulation passed.")
PY

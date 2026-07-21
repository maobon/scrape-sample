#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv"

if [[ -z "$VENV_DIR" || ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "ERROR: Virtual environment not found: $VENV_DIR" >&2
  exit 1
fi

source "$VENV_DIR/bin/activate"

PYTHON_BIN="${PYTHON_BIN:-python}"
STATUS=0

run_check() {
  local name="$1"
  local log_file
  log_file="$(mktemp "${TMPDIR:-/tmp}/${name}.XXXXXX.log")"

  echo "Checking ${name}..."
  if export PYTHONPATH="$ROOT_DIR"; "$PYTHON_BIN" - "$name" >"$log_file" 2>&1 <<'PY'
import sys
import os

check_name = sys.argv[1]

if check_name == "postgres":
    from src.db import client as db

    kwargs = db._connection_kwargs()
    safe_kwargs = {key: value for key, value in kwargs.items() if key != "password"}
    print(f"PostgreSQL config: {safe_kwargs}")

    result = db.test_connection()
    print(f"PostgreSQL connected: {result['version']}")

elif check_name == "minio":
    from src.utils.minio import MINIO_BUCKET, MINIO_ENDPOINT, _get_minio_client

    client = _get_minio_client()
    bucket_exists = client.bucket_exists(MINIO_BUCKET)
    print(f"MinIO endpoint: {MINIO_ENDPOINT}")
    print(f"MinIO bucket: {MINIO_BUCKET}")
    print(f"MinIO connected: bucket_exists={bucket_exists}")

else:
    raise ValueError(f"Unknown check: {check_name}")
PY
  then
    cat "$log_file"
    echo "${name}: OK"
  else
    local exit_code=$?
    STATUS=1
    echo "ERROR: ${name} check failed with exit code ${exit_code}" >&2
    echo "----- ${name} output -----" >&2
    cat "$log_file" >&2
    echo "--------------------------" >&2
  fi

  rm -f "$log_file"
}

run_check postgres
run_check minio

if [[ "$STATUS" -eq 0 ]]; then
  echo "Database checks completed successfully."
else
  echo "ERROR: One or more database checks failed." >&2
fi

exit "$STATUS"

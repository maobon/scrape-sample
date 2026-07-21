#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv"

if [[ -z "$VENV_DIR" || ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "ERROR: Virtual environment not found: $VENV_DIR" >&2
  exit 1
fi

source "$VENV_DIR/bin/activate"

python main.py "$@"

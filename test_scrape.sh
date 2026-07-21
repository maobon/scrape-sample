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
CONFIG_PATH="${CONFIG_PATH:-config.json}"
LIMIT="${LIMIT:-1}"
LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/scrape-test.XXXXXX.log")"
URL_ARG_PRESENT=0

cleanup() {
  rm -f "$LOG_FILE"
}
trap cleanup EXIT

for arg in "$@"; do
  if [[ "$arg" == "--url" || "$arg" == --url=* ]]; then
    URL_ARG_PRESENT=1
    break
  fi
done

if [[ ! -f "$CONFIG_PATH" && -z "${SCRAPER_TARGET_URL:-}" && "$URL_ARG_PRESENT" -eq 0 ]]; then
  echo "ERROR: Missing scraper config: $CONFIG_PATH" >&2
  echo "Create config.json from config.example.json, set CONFIG_PATH, set SCRAPER_TARGET_URL, or pass --url." >&2
  exit 1
fi

echo "Running scrape test..."
echo "Python: $PYTHON_BIN"
echo "Config: $CONFIG_PATH"
echo "Limit: $LIMIT"

if "$PYTHON_BIN" main.py --config "$CONFIG_PATH" --limit "$LIMIT" "$@" >"$LOG_FILE" 2>&1; then
  cat "$LOG_FILE"
  echo "Scrape test completed successfully."
else
  status=$?
  echo "ERROR: Scrape test failed with exit code $status" >&2
  echo "----- scrape output -----" >&2
  cat "$LOG_FILE" >&2
  echo "-------------------------" >&2
  exit "$status"
fi

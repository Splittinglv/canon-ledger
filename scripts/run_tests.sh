#!/usr/bin/env sh
set -eu

MODE="${1:-smoke}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
TEST_PYTHON="${CANON_LEDGER_TEST_PYTHON:-python3}"

exec "$TEST_PYTHON" "$SCRIPT_DIR/run_acceptance.py" \
  --mode "$MODE" \
  --project-root "$PROJECT_ROOT"

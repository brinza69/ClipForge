#!/usr/bin/env bash
# Run the ClipForge backend smoke tests.
#
# First time: install the dev deps into the venv:
#   server/.venv/bin/pip install -r server/requirements-dev.txt
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTEST="$ROOT/server/.venv/bin/pytest"
if [ ! -x "$PYTEST" ]; then
  echo "pytest not found in the venv. Install it first:"
  echo "  $ROOT/server/.venv/bin/pip install -r $ROOT/server/requirements-dev.txt"
  exit 1
fi
cd "$ROOT/server"
exec "$PYTEST" tests/ -v

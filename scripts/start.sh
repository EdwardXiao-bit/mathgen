#!/usr/bin/env sh
# mathgen one-click launcher for macOS / Linux.
#
# All user-facing text lives in scripts/start.py so there is exactly one place to edit.
# Arguments are passed straight through, e.g.:
#   ./scripts/start.sh --host 0.0.0.0      # let students on the LAN connect
#   ./scripts/start.sh --no-mirror         # install deps from pypi.org
set -e

here=$(cd "$(dirname "$0")" && pwd)

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

PY=""
for candidate in python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "  Python 3.12+ was not found. Install it from https://www.python.org/downloads/"
    echo "  (macOS: brew install python@3.12   Debian/Ubuntu: sudo apt install python3.12 python3.12-venv)"
    exit 1
fi

exec "$PY" "$here/start.py" "$@"

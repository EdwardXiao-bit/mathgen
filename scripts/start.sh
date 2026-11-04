#!/usr/bin/env sh
# mathgen one-click launcher for macOS / Linux.
#
# All user-facing text lives in scripts/start.py so there is exactly one place to edit.
# Arguments are passed straight through, e.g.:
#   ./scripts/start.sh                     # LAN mode (default): students can join from phones
#   MATHGEN_HOST=127.0.0.1 ./scripts/start.sh   # this machine only
#   ./scripts/start.sh --no-mirror         # install deps from pypi.org
set -e

here=$(cd "$(dirname "$0")" && pwd)

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# LAN mode is the default: the whole point of this tool is that students join from their
# phones, and a localhost-only server cannot be joined at all. Override with MATHGEN_HOST.
: "${MATHGEN_HOST:=0.0.0.0}"
export MATHGEN_HOST

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

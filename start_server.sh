#!/usr/bin/env bash
# Start the wingman dev API (FastAPI via the `python server.py` shim, ops console enabled)
# in the FOREGROUND, so the terminal you launch it from is the server's log and Ctrl-C
# stops it. macOS/Linux counterpart to restart_server.ps1 (which is Windows-only).
#
# Usage, from a spare terminal:
#     ./start_server.sh          # port 8000
#     ./start_server.sh 8002     # or PORT=8002 ./start_server.sh
#
# It refuses to start a second instance on a port that is already bound rather than
# silently leaving a stale process serving old code — that failure mode is the whole
# reason the Windows script exists.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PORT="${1:-${PORT:-8000}}"
export PORT

# Prefer the repo venv; fall back to whatever python3 is on PATH.
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

# Whoever already owns the port wins — report it and stop, don't stack a second listener.
if OWNER="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null)" && [ -n "$OWNER" ]; then
    echo "Port $PORT is already bound by PID(s): $(echo "$OWNER" | tr '\n' ' ')" >&2
    echo "Stop it first (kill $(echo "$OWNER" | head -1)) or pick another port: ./start_server.sh 8002" >&2
    exit 1
fi

echo "Starting wingman API on http://127.0.0.1:$PORT  (admin console at /admin)"
echo "Using $PYTHON"
exec "$PYTHON" server.py

#!/usr/bin/env bash
# Read-only diagnostics for a new TeleRepka deployment.
set -u
echo "=== Service ==="
systemctl status lab-hub-kiosk.service --no-pager | head -16 || true
echo "=== HTTP ==="
curl -sS --max-time 5 -o /dev/null -w 'HTTP %{http_code}\n' 'http://127.0.0.1:5000/?kiosk=1' || true
echo "=== Employee SQLite database ==="
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/data/laboratory_employees.db"
if [[ -f "$DB" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    "$ROOT/.venv/bin/python" - "$DB" <<'PY'
import sqlite3
import sys
from pathlib import Path
db = Path(sys.argv[1])
try:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        print(f"SQLite OK: {db} (employees: {count})")
    finally:
        conn.close()
except sqlite3.Error as exc:
    print(f"SQLite ERROR: {exc}")
PY
  else
    echo "SQLite file exists, but project Python environment is missing"
  fi
else
  echo "SQLite file not found: $DB"
fi
echo "=== Employees API ==="
curl -fsS --max-time 5 'http://127.0.0.1:5000/api/employees' | head -c 250 || true
printf '\\n'
echo "=== Chromium CDP (after GDM login) ==="
curl -fsS --max-time 3 'http://127.0.0.1:9222/json/list' | head -c 300 || true
printf '\n'
echo "=== Resolution (requires graphical DISPLAY) ==="
xrandr --current 2>&1 | head -12 || true
echo "=== Recent logs ==="
journalctl -u lab-hub-kiosk.service -n 12 --no-pager || true

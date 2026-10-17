#!/usr/bin/env bash
# Read-only diagnostics for a new TeleRepka deployment.
set -u
echo "=== Service ==="
systemctl status lab-hub-kiosk.service --no-pager | head -16 || true
echo "=== HTTP ==="
curl -sS --max-time 5 -o /dev/null -w 'HTTP %{http_code}\n' 'http://127.0.0.1:5000/?kiosk=1' || true
echo "=== Chromium CDP (after GDM login) ==="
curl -fsS --max-time 3 'http://127.0.0.1:9222/json/list' | head -c 300 || true
printf '\n'
echo "=== Resolution (requires graphical DISPLAY) ==="
xrandr --current 2>&1 | head -12 || true
echo "=== Recent logs ==="
journalctl -u lab-hub-kiosk.service -n 12 --no-pager || true

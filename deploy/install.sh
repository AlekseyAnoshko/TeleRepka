#!/usr/bin/env bash
# New Repka Pi 4: Ubuntu 22.04, GDM, Snap Chromium.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_USER=""
WITH_KIOSK=0
DRY_RUN=0
while (($#)); do
  case "$1" in
    --user) TARGET_USER="$2"; shift 2 ;;
    --with-kiosk) WITH_KIOSK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) echo "sudo bash deploy/install.sh --user USER [--with-kiosk] [--dry-run]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ $EUID == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
[[ -n "$TARGET_USER" ]] && id "$TARGET_USER" >/dev/null || { echo "Specify existing --user USER" >&2; exit 1; }
[[ "$ROOT" != *" "* ]] || { echo "Project path must not contain spaces" >&2; exit 1; }
[[ -f "$ROOT/app.py" && -f "$ROOT/requirements.txt" && -f "$ROOT/kiosk/setup-kiosk-session.sh" ]] || {
  echo "Run from a complete TeleRepka checkout" >&2; exit 1;
}
[[ "$(uname -m)" == "aarch64" ]] || echo "WARNING: only aarch64 was tested"
if [[ $WITH_KIOSK == 1 && ! -f /etc/gdm3/custom.conf ]]; then
  echo "GDM is required for --with-kiosk" >&2; exit 1
fi
if systemctl is-active --quiet lab-hub-kiosk.service; then
  echo "Refusing to replace an active TeleRepka installation" >&2; exit 1
fi
echo "Repository: $ROOT; kiosk user: $TARGET_USER; GDM kiosk: $WITH_KIOSK"
if [[ $DRY_RUN == 1 ]]; then
  echo "DRY RUN: apt Python/venv/curl/x11/snapd; Snap Chromium; pip requirements;"
  echo "create secret and systemd Flask unit; optionally configure GDM autologin."
  echo "No changes made. See deploy/README.md."
  exit 0
fi
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl x11-xserver-utils snapd
if [[ $WITH_KIOSK == 1 && ! -x /snap/bin/chromium ]]; then
  systemctl enable --now snapd.socket
  snap install chromium
fi
if [[ $WITH_KIOSK == 1 && ! -x /snap/bin/chromium ]]; then
  echo "Chromium Snap missing" >&2; exit 1
fi
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
install -d -m 700 /etc/telerepka
if [[ ! -f /etc/telerepka/lab-hub.env ]]; then
  secret="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'LAB_HUB_SECRET=%s\n' "$secret" > /etc/telerepka/lab-hub.env
  chmod 600 /etc/telerepka/lab-hub.env
fi
UNIT=/etc/systemd/system/lab-hub-kiosk.service
if [[ -e "$UNIT" ]]; then
  cp -a "$UNIT" "$UNIT.before-telerepka-$(date +%Y%m%d-%H%M%S)"
fi
cat > "$UNIT" <<EOF
[Unit]
Description=TeleRepka Flask server (Chromium runs in GDM session)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$ROOT
EnvironmentFile=/etc/telerepka/lab-hub.env
ExecStart=$ROOT/.venv/bin/python $ROOT/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now lab-hub-kiosk.service
if [[ $WITH_KIOSK == 1 ]]; then
  bash "$ROOT/kiosk/setup-kiosk-session.sh" --user "$TARGET_USER"
fi
echo "Installed. Check: sudo bash deploy/check.sh"
echo "If --with-kiosk: reboot manually with sudo reboot"
echo "Sensors, hotspot, TLS/reverse proxy and photo frames require separate configuration."

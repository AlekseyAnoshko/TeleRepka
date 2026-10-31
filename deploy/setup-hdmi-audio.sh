#!/usr/bin/env bash
# Configure persistent HDMI audio for a TeleRepka kiosk user.
# Run: sudo bash deploy/setup-hdmi-audio.sh --user aaf
set -Eeuo pipefail
TARGET_USER=""
while (($#)); do
  case "$1" in
    --user) [[ $# -ge 2 ]] || { echo "Missing --user value" >&2; exit 2; }; TARGET_USER="$2"; shift 2 ;;
    -h|--help) echo "Usage: sudo bash deploy/setup-hdmi-audio.sh --user USER"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }
[[ -n "$TARGET_USER" ]] && id "$TARGET_USER" >/dev/null 2>&1 || { echo "Specify an existing --user" >&2; exit 1; }
HOME_DIR="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -d "$HOME_DIR" && "$HOME_DIR" != "/" ]] || { echo "Invalid user home" >&2; exit 1; }
USER_GROUP="$(id -gn "$TARGET_USER")"
BIN_DIR="$HOME_DIR/.local/bin"
UNIT_DIR="$HOME_DIR/.config/systemd/user"
WANTS_DIR="$UNIT_DIR/default.target.wants"
install -d -o "$TARGET_USER" -g "$USER_GROUP" -m 0755 "$BIN_DIR" "$UNIT_DIR" "$WANTS_DIR"
# Preserve any pre-existing files rather than silently replacing local configuration.
for file in "$BIN_DIR/telerepka-hdmi-audio" "$UNIT_DIR/telerepka-hdmi-audio.service"; do
  if [[ -e "$file" ]]; then
    cp -a "$file" "$file.before-telerepka-$(date +%Y%m%d-%H%M%S)"
  fi
done
cat > "$BIN_DIR/telerepka-hdmi-audio" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SINK="alsa_output.platform-sound_hdmi.stereo-fallback"
for ((i=0; i<60; i++)); do
  if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fxq "$SINK"; then
    pactl set-default-sink "$SINK"
    pactl set-sink-mute "$SINK" 0
    pactl set-sink-volume "$SINK" 80%
    pactl list short sink-inputs | awk '{print $1}' | while read -r id; do
      [[ -n "$id" ]] && pactl move-sink-input "$id" "$SINK" 2>/dev/null || true
    done
    exit 0
  fi
  sleep 1
done
echo "HDMI sink not found: $SINK" >&2
exit 1
EOF
cat > "$UNIT_DIR/telerepka-hdmi-audio.service" <<'EOF'
[Unit]
Description=TeleRepka HDMI audio output
After=pulseaudio.service pipewire-pulse.service

[Service]
Type=oneshot
ExecStart=%h/.local/bin/telerepka-hdmi-audio
TimeoutStartSec=75

[Install]
WantedBy=default.target
EOF
chown "$TARGET_USER:$USER_GROUP" "$BIN_DIR/telerepka-hdmi-audio" "$UNIT_DIR/telerepka-hdmi-audio.service"
chmod 0755 "$BIN_DIR/telerepka-hdmi-audio"
chmod 0644 "$UNIT_DIR/telerepka-hdmi-audio.service"
ln -sfn ../telerepka-hdmi-audio.service "$WANTS_DIR/telerepka-hdmi-audio.service"
chown -h "$TARGET_USER:$USER_GROUP" "$WANTS_DIR/telerepka-hdmi-audio.service"
echo "Installed and enabled for $TARGET_USER; runs when the user systemd session starts."
echo "For an existing session, run AS $TARGET_USER:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user start telerepka-hdmi-audio.service"
echo "  pactl get-default-sink"

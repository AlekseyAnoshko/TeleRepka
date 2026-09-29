#!/usr/bin/env bash
# setup-kiosk-session.sh — сеанс GDM «TeleRepka Kiosk» для Repka OS (Ubuntu 22.04).
#
# Что делает:
#   - копирует kiosk/telerepka-kiosk-session в /usr/local/lib/telerepka/kiosk-session;
#   - копирует kiosk/telerepka.desktop в /usr/share/xsessions/;
#   - делает резервные копии /etc/gdm3/custom.conf и файла AccountsService пользователя;
#   - включает автологин пользователя в GDM и назначает ему сеанс telerepka.
#
# Использование: sudo ./kiosk/setup-kiosk-session.sh [--user ИМЯ] [--dry-run]
# Откат: см. kiosk/KIOSK.md

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_PREFIX="[TeleRepka-kiosk]"
KIOSK_USER="${SUDO_USER:-aaf}"
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --user) KIOSK_USER="${2:?нужно имя пользователя}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "$LOG_PREFIX Неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

log() { echo "$LOG_PREFIX $*"; }
run() { echo "$LOG_PREFIX + $*"; [ "$DRY_RUN" -eq 1 ] || "$@"; }

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo "$LOG_PREFIX Запускайте с sudo." >&2
    exit 1
fi

GDM_CONF=/etc/gdm3/custom.conf
ACC_FILE="/var/lib/AccountsService/users/$KIOSK_USER"

[ -x /snap/bin/chromium ] || log "ВНИМАНИЕ: /snap/bin/chromium не найден. Установите: sudo snap install chromium"
id "$KIOSK_USER" >/dev/null 2>&1 || { echo "$LOG_PREFIX Пользователь $KIOSK_USER не найден" >&2; exit 1; }

log "=== 1. Файлы сеанса ==="
run install -D -m 755 "$DIR/telerepka-kiosk-session" /usr/local/lib/telerepka/kiosk-session
run install -D -m 644 "$DIR/telerepka.desktop" /usr/share/xsessions/telerepka.desktop

log "=== 2. Резервные копии ==="
[ -e "$GDM_CONF.before-telerepka" ] || run cp -a "$GDM_CONF" "$GDM_CONF.before-telerepka"
if [ -e "$ACC_FILE" ] && [ ! -e "$ACC_FILE.before-telerepka" ]; then
    run cp -a "$ACC_FILE" "$ACC_FILE.before-telerepka"
fi

log "=== 3. Автологин GDM ==="
run sed -i -E '/^[#[:space:]]*AutomaticLogin(Enable)?[[:space:]]*=/d' "$GDM_CONF"
if grep -q '^\[daemon\]' "$GDM_CONF"; then
    run sed -i "/^\[daemon\]/a AutomaticLoginEnable=true\nAutomaticLogin=$KIOSK_USER" "$GDM_CONF"
else
    run bash -c "printf '\n[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=%s\n' '$KIOSK_USER' >> '$GDM_CONF'"
fi

log "=== 4. Сеанс пользователя (AccountsService) ==="
if [ ! -e "$ACC_FILE" ]; then
    run bash -c "printf '[User]\n' > '$ACC_FILE'"
    run chmod 600 "$ACC_FILE"
fi
run sed -i -E '/^(X)?Session=/d' "$ACC_FILE"
run sed -i '/^\[User\]/a Session=telerepka\nXSession=telerepka' "$ACC_FILE"

log "=== Готово ==="
log "Перезагрузите плату: sudo reboot"

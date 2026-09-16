#!/usr/bin/env bash
# restore-hardware-config.sh — восстановление hardware-специфичных настроек TeleRepka
# после чистой установки ОС (Repka OS / Debian) на Repka Pi 4.
#
# Скрипт восстанавливает:
#   1. Отключение onewire overlay в /boot/repkaEnv.txt (освобождает GPIO
#      для DHT11 на физическом pin 33)
#   2. Права доступа на /sys/class/gpio/export и /sys/class/gpio/unexport
#      (root:gpio, mode 220) через udev-правило (переживает перезагрузки)
#   3. Добавление пользователя user в группы gpio и dialout
#      (dialout нужен для MH-Z19B по serial через pyserial)
#   4. Конфиг dnsmasq для фоторамок (photoframes.conf с привязкой MAC->IP)
#   5. LXDM autologin для kiosk-режима
#   6. Проверочный вывод в конце: что применилось, что требует
#      ручной проверки/перезагрузки
#
# Использование:
#   chmod +x restore-hardware-config.sh
#   sudo ./restore-hardware-config.sh
#
# Опции:
#   --dry-run   только показать, что будет сделано, без реальных правок
#
# ВАЖНО: скрипт НЕ восстанавливает сам проект TeleRepka (код, venv,
# nginx-конфиг) — для этого используйте i/install.sh. Этот скрипт только
# для специфичных hardware-настроек GPIO/датчиков/сети/автологина.

set -euo pipefail

LOG_PREFIX="[TeleRepka-hw-restore]"
DRY_RUN=0
TARGET_USER="${SUDO_USER:-user}"
PHOTOFRAME_MAC="a4:cb:8f:df:89:b0"
PHOTOFRAME_IP="10.42.0.101"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "$LOG_PREFIX Неизвестный аргумент: $arg" >&2
      exit 1
      ;;
  esac
done

run() {
  echo "$LOG_PREFIX + $*"
  if [ "$DRY_RUN" -eq 0 ]; then
    "$@"
  fi
}

log() {
  echo "$LOG_PREFIX $*"
}

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "$LOG_PREFIX Скрипт нужно запускать с sudo (нужны права root)." >&2
  exit 1
fi

log "Целевой пользователь: $TARGET_USER"
log ""
log "=== 1. Отключение onewire overlay (освобождение GPIO для DHT11 на pin 33) ==="

REPKA_ENV="/boot/repkaEnv.txt"
if [ -f "$REPKA_ENV" ]; then
  if grep -q "^onewire=" "$REPKA_ENV" 2>/dev/null; then
    log "Найдена строка onewire= в $REPKA_ENV, показываю текущее значение:"
    grep "^onewire=" "$REPKA_ENV" | sed "s/^/$LOG_PREFIX   /"
    log "Комментирую/отключаю (создаю бэкап .bak перед правкой)..."
    if [ "$DRY_RUN" -eq 0 ]; then
      cp "$REPKA_ENV" "${REPKA_ENV}.bak.$(date +%Y%m%d%H%M%S)"
      sed -i 's/^onewire=1/onewire=0/' "$REPKA_ENV"
      sed -i 's/^onewire=on/onewire=off/' "$REPKA_ENV"
    fi
  else
    log "Строка onewire= не найдена в $REPKA_ENV."
    log "Добавляю 'onewire=0' в конец файла (бэкап создаётся перед правкой)."
    if [ "$DRY_RUN" -eq 0 ]; then
      cp "$REPKA_ENV" "${REPKA_ENV}.bak.$(date +%Y%m%d%H%M%S)"
      echo "onewire=0" >> "$REPKA_ENV"
    fi
  fi
  log "ВНИМАНИЕ: проверьте вручную итоговое содержимое $REPKA_ENV —"
  log "формат опции onewire может отличаться в Repka OS от вашей текущей Debian-сборки:"
  log "  cat $REPKA_ENV | grep -i onewire"
else
  log "Файл $REPKA_ENV не найден. Если в Repka OS используется другой путь"
  log "конфигурации overlay (например /boot/firmware/config.txt или repka-config),"
  log "отключите onewire/w1 overlay через него вручную, либо через утилиту:"
  log "  sudo repka-config"
fi

log ""
log "=== 2. Права доступа на GPIO sysfs (root:gpio, mode 220) ==="

UDEV_RULE_PATH="/etc/udev/rules.d/99-telerepka-gpio.rules"
log "Создаю udev-правило для устойчивых прав на GPIO export/unexport: $UDEV_RULE_PATH"

UDEV_RULE_CONTENT='SUBSYSTEM=="gpio", KERNEL=="export", GROUP="gpio", MODE="0220"
SUBSYSTEM=="gpio", KERNEL=="unexport", GROUP="gpio", MODE="0220"
SUBSYSTEM=="gpio", KERNEL=="gpio*", GROUP="gpio", MODE="0660"'

if [ "$DRY_RUN" -eq 0 ]; then
  echo "$UDEV_RULE_CONTENT" > "$UDEV_RULE_PATH"
  udevadm control --reload-rules
  udevadm trigger
else
  log "(dry-run) Содержимое правила:"
  echo "$UDEV_RULE_CONTENT" | sed "s/^/$LOG_PREFIX   /"
fi

log "Также применяю права немедленно к уже существующим файлам (на случай,"
log "если экспорт GPIO уже произошёл до применения udev-правила):"
if [ -e /sys/class/gpio/export ]; then
  run chown root:gpio /sys/class/gpio/export
  run chmod 220 /sys/class/gpio/export
fi
if [ -e /sys/class/gpio/unexport ]; then
  run chown root:gpio /sys/class/gpio/unexport
  run chmod 220 /sys/class/gpio/unexport
fi

log ""
log "=== 3. Группы пользователя: gpio, dialout ==="
log "gpio — для доступа к GPIO без sudo (DHT11 через libgpiod/RPi.GPIO)"
log "dialout — для serial-доступа к MH-Z19B через pyserial"

run usermod -aG gpio "$TARGET_USER"
run usermod -aG dialout "$TARGET_USER"

log "Группы применятся после перелогина/перезагрузки. Проверить можно так:"
log "  groups $TARGET_USER"

log ""
log "=== 4. Конфиг dnsmasq для фоторамок (DHCP-привязка MAC->IP) ==="

DNSMASQ_DIR="/etc/NetworkManager/dnsmasq-shared.d"
DNSMASQ_FILE="$DNSMASQ_DIR/photoframes.conf"

if [ -d "$DNSMASQ_DIR" ] || [ "$DRY_RUN" -eq 1 ]; then
  run mkdir -p "$DNSMASQ_DIR"
  log "Записываю $DNSMASQ_FILE с привязкой MAC $PHOTOFRAME_MAC -> IP $PHOTOFRAME_IP"
  if [ "$DRY_RUN" -eq 0 ]; then
    cat > "$DNSMASQ_FILE" <<EOF
# TeleRepka: статическая привязка фоторамки по MAC-адресу
dhcp-host=$PHOTOFRAME_MAC,$PHOTOFRAME_IP
EOF
  fi
  log "ВНИМАНИЕ: проверьте, что MAC-адрес и IP всё ещё актуальны для вашей фоторамки"
  log "(могло поменяться железо/сеть с момента последней настройки)."
  log "После правки перезапустите NetworkManager:"
  log "  sudo systemctl restart NetworkManager"
else
  log "Каталог $DNSMASQ_DIR не существует — NetworkManager с dnsmasq, возможно,"
  log "не установлен/не настроен на этой системе. Настройте вручную, если нужно."
fi

log ""
log "=== 5. LXDM autologin (kiosk-режим) ==="

LXDM_CONF="/etc/lxdm/lxdm.conf"
if [ -f "$LXDM_CONF" ]; then
  log "Найден $LXDM_CONF. Проверяю/включаю autologin для пользователя $TARGET_USER."
  if [ "$DRY_RUN" -eq 0 ]; then
    cp "$LXDM_CONF" "${LXDM_CONF}.bak.$(date +%Y%m%d%H%M%S)"
    if grep -q "^autologin=" "$LXDM_CONF"; then
      sed -i "s/^autologin=.*/autologin=$TARGET_USER/" "$LXDM_CONF"
    else
      sed -i "/^\[base\]/a autologin=$TARGET_USER" "$LXDM_CONF"
    fi
  fi
  log "Проверьте вручную секцию [base] в $LXDM_CONF:"
  log "  grep -A2 '\[base\]' $LXDM_CONF"
else
  log "Файл $LXDM_CONF не найден. Если Repka OS использует другой дисплей-менеджер"
  log "(lightdm, sddm и т.п.), настройте autologin через его собственный конфиг."
  log "Обратитесь к файлу lxdm.conf.reference в репозитории TeleRepka как к образцу"
  log "нужных значений, если LXDM всё же используется."
fi

log ""
log "=== Готово. Обязательно проверьте вручную после перезагрузки ==="
log "1) DHT11 (BOARD pin 33): физическая линия GPIO освобождена от onewire?"
log "   gpioinfo | grep -i -A2 'line.*33\|onewire\|w1'"
log "2) MH-Z19B (BOARD pin 11, serial): устройство доступно и доступ есть?"
log "   ls -l /dev/ttyS* /dev/ttyUSB* /dev/ttyAMA* 2>/dev/null"
log "   groups $TARGET_USER   # должна быть dialout"
log "3) GPIO права применились?"
log "   ls -l /sys/class/gpio/export /sys/class/gpio/unexport"
log "4) Фоторамка получает корректный IP по DHCP?"
log "   journalctl -u NetworkManager --since '10 minutes ago' | grep -i dhcp"
log "5) Kiosk стартует автоматически после перезагрузки без ручного логина?"
log ""
log "Рекомендуется перезагрузить систему для применения групп/udev/overlay:"
log "  sudo reboot"

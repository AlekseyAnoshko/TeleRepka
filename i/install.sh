#!/usr/bin/env bash
# install.sh — установка окружения для проекта TeleRepka (Repka Pi 4, Debian/Ubuntu)
#
# Скрипт устанавливает:
#   - системные пакеты (Python, GPIO/I2C, nginx, squid, ffmpeg)
#   - GStreamer со всеми плагинами (включая аппаратное H.264/VP8/VP9 декодирование через cedrus/V4L2)
#   - v4l-utils для диагностики видео-декодера
#   - Python-зависимости проекта (requirements.txt)
#   - systemd-юниты проекта (lab-hub.service, lab-hub-kiosk.service)
#
# Использование:
#   chmod +x install.sh
#   sudo ./install.sh
#
# Опции:
#   --no-gpu       пропустить настройку/проверку аппаратного видео-декодирования
#   --no-services  не устанавливать и не включать systemd-юниты
#   --dry-run      только показать, что будет сделано, без реальной установки

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_PREFIX="[TeleRepka-install]"
NO_GPU=0
NO_SERVICES=0
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --no-gpu) NO_GPU=1 ;;
    --no-services) NO_SERVICES=1 ;;
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
  echo "$LOG_PREFIX Скрипт нужно запускать с sudo (нужны права root для apt/systemd)." >&2
  exit 1
fi

log "=== 1. Обновление списка пакетов ==="
run apt-get update

log "=== 2. Системные утилиты общего назначения ==="
run apt-get install -y \
  git \
  curl \
  wget \
  ca-certificates \
  build-essential \
  pkg-config \
  ffmpeg \
  nginx \
  squid \
  nftables

log "=== 3. Python и виртуальное окружение ==="
run apt-get install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev

log "=== 4. GPIO / I2C / датчики (DHT, gpiod) ==="
run apt-get install -y \
  gpiod \
  libgpiod2 \
  libgpiod-dev \
  python3-libgpiod \
  i2c-tools \
  python3-smbus

log "=== 5. Chromium (kiosk) ==="
run apt-get install -y \
  chromium \
  chromium-sandbox

if [ "$NO_GPU" -eq 0 ]; then
  log "=== 6. GStreamer + аппаратное декодирование видео (V4L2/cedrus) ==="
  run apt-get install -y \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-vaapi \
    v4l-utils \
    libv4l-0 \
    libv4l-dev \
    vainfo

  log "--- Диагностика видео-декодера (информационно, не прерывает установку) ---"
  set +e
  log "Список V4L2/DRM устройств:"
  v4l2-ctl --list-devices 2>&1 | sed "s/^/$LOG_PREFIX   /"
  ls -l /dev/video* 2>&1 | sed "s/^/$LOG_PREFIX   /"
  log "Проверка VA-API (vainfo):"
  vainfo 2>&1 | sed "s/^/$LOG_PREFIX   /"
  log "Проверка плагина GStreamer v4l2:"
  gst-inspect-1.0 v4l2h264dec 2>&1 | head -5 | sed "s/^/$LOG_PREFIX   /"
  set -e
else
  log "=== 6. GStreamer/видео-декодирование пропущено (--no-gpu) ==="
fi

log "=== 7. Python-зависимости проекта (requirements.txt) ==="
if [ -f "$REPO_DIR/requirements.txt" ]; then
  run pip3 install --break-system-packages -r "$REPO_DIR/requirements.txt"
else
  log "requirements.txt не найден рядом со скриптом ($REPO_DIR) — пропускаю pip install."
fi

if [ "$NO_SERVICES" -eq 0 ]; then
  log "=== 8. Установка systemd-юнитов проекта ==="
  for unit in lab-hub.service lab-hub-kiosk.service; do
    if [ -f "$REPO_DIR/$unit" ]; then
      run cp "$REPO_DIR/$unit" "/etc/systemd/system/$unit"
    else
      log "Файл $unit не найден рядом со скриптом — пропускаю."
    fi
  done

  if [ -f "$REPO_DIR/nginx_telerepka.conf" ]; then
    run cp "$REPO_DIR/nginx_telerepka.conf" "/etc/nginx/sites-available/telerepka.conf"
    run ln -sf "/etc/nginx/sites-available/telerepka.conf" "/etc/nginx/sites-enabled/telerepka.conf"
    run systemctl restart nginx
  fi

  if [ -f "$REPO_DIR/squid.conf" ]; then
    run cp "$REPO_DIR/squid.conf" "/etc/squid/squid.conf"
    run systemctl restart squid
  fi

  if [ -f "$REPO_DIR/nftables-photoframe-proxy.conf" ]; then
    run cp "$REPO_DIR/nftables-photoframe-proxy.conf" "/etc/nftables-photoframe-proxy.conf"
    log "Правила nftables скопированы. Подключите их в основной конфиг nftables вручную, если это ещё не сделано."
  fi

  run systemctl daemon-reload
  run systemctl enable lab-hub.service
  run systemctl enable lab-hub-kiosk.service

  log "Юниты установлены и включены. Запустите вручную при готовности:"
  log "  sudo systemctl start lab-hub.service"
  log "  sudo systemctl start lab-hub-kiosk.service"
else
  log "=== 8. Установка systemd-юнитов пропущена (--no-services) ==="
fi

log "=== Готово ==="
log "Рекомендуется перезагрузить плату, чтобы применились все изменения (GPIO-группы, drm-модули):"
log "  sudo reboot"

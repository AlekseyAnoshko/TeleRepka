"""
Опрос датчиков, подключённых к GPIO/I2C/UART/1-Wire Repka Pi 4.

ДАТЧИК ТЕМПЕРАТУРЫ: DS18B20 (1-Wire).
DS18B20 не использует RepkaPi.GPIO — драйвер ядра w1-gpio/w1-therm публикует
показания напрямую в /sys/bus/w1/devices/28-xxxxxxxxxxxx/w1_slave, чтение
идёт обычным open()/read(), без GPIO-библиотеки вообще.

Включение 1-Wire на Repka Pi (сделать один раз на самой репке, до запуска
сервиса) — см. README.md, раздел "1-Wire / DS18B20". Коротко:
  1. Создать /root/onewire.dts с overlay под используемый пин (например PA7).
  2. sudo apt-get install device-tree-compiler
  3. dtc -I dts -O dtb -o onewire.dtbo onewire.dts
  4. sudo cp onewire.dtbo /boot/overlays/
  5. Добавить "onewire" в overlays= в /boot/repkaEnv.txt
  6. sudo reboot
  7. Проверить: ls /sys/bus/w1/devices/  -> должна появиться папка 28-xxxxxxxxxxxx

CO2 (MH-Z19, опционально) по-прежнему опрашивается через UART, если
установлен пакет mh-z19. Если датчик CO2/влажности не подключён —
соответствующие поля просто возвращаются как None, фронтенд это уже
корректно обрабатывает (см. index.html: data.humidity/co2 !== null ? ...).
"""
from __future__ import annotations

import os
import re
import time

W1_BASE_DIR = "/sys/bus/w1/devices"
W1_DEVICE_PREFIX = "28-"  # DS18B20 всегда начинается с семейного кода 28-

try:
    import mh_z19  # type: ignore
    HAVE_MHZ19 = True
except ImportError:
    HAVE_MHZ19 = False

_last_good_temperature: float | None = None


def _find_ds18b20_ids() -> list[str]:
    if not os.path.isdir(W1_BASE_DIR):
        return []
    return sorted(
        entry for entry in os.listdir(W1_BASE_DIR)
        if entry.startswith(W1_DEVICE_PREFIX)
    )


def read_temperature() -> float | None:
    """Читает температуру с первого найденного DS18B20.

    Возвращает None, если 1-Wire не включён (нет /sys/bus/w1/devices),
    датчик не подключён/не найден, либо CRC чтения не сошёлся (YES/NO
    в первой строке w1_slave) — в этом случае лучше вернуть последнее
    валидное значение, чем дёргать интерфейс дребезгом.
    """
    global _last_good_temperature

    device_ids = _find_ds18b20_ids()
    if not device_ids:
        return None

    device_path = f"{W1_BASE_DIR}/{device_ids[0]}/w1_slave"
    try:
        with open(device_path, "r", encoding="ascii") as fp:
            lines = fp.readlines()
    except OSError:
        return _last_good_temperature

    if len(lines) != 2 or not lines[0].strip().endswith("YES"):
        # CRC не сошёлся при этом опросе — типичная ситуация для 1-Wire,
        # просто пропускаем один цикл и вернём предыдущее валидное значение
        return _last_good_temperature

    match = re.search(r"t=(-?\d+)", lines[1])
    if not match:
        return _last_good_temperature

    _last_good_temperature = round(int(match.group(1)) / 1000.0, 1)
    return _last_good_temperature


def _read_co2() -> int | None:
    if not HAVE_MHZ19:
        return None
    try:
        result = mh_z19.read()
        return result.get("co2") if result else None
    except Exception:  # noqa: BLE001
        return None


def read_all() -> dict:
    return {
        "temperature": read_temperature(),
        "humidity": None,
        "co2": _read_co2(),
        "simulated": False,
        "timestamp": time.time(),
    }

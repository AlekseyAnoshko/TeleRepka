"""
Опрос датчиков, подключённых к GPIO/I2C/UART Repka Pi 4.

Реализация ниже — рабочий каркас с понятными точками расширения. Библиотека
GPIO для Repka Pi (RepkaPiGPIO / RepkaPiGPIOFS) устанавливается отдельно, см.
README.md. Конкретные пины и протокол датчика CO2 (обычно UART, MH-Z19 или
аналог) нужно поправить под реальную распиновку лаборатории.

Установка зависимостей (пример для DHT22 + MH-Z19):
    sudo apt-get install python3-dev python3-setuptools git
    git clone https://gitflic.ru/project/repka_pi/repkapigpiofs.git
    cd repkapigpiofs && sudo python3 setup.py install
    pip3 install adafruit-circuitpython-dht mh-z19
"""
from __future__ import annotations

import random
import time

try:
    import adafruit_dht  # type: ignore
    import board  # type: ignore
    HAVE_DHT = True
except ImportError:
    HAVE_DHT = False

try:
    import mh_z19  # type: ignore
    HAVE_MHZ19 = True
except ImportError:
    HAVE_MHZ19 = False

# Порт GPIO, к которому подключён датчик температуры/влажности (BCM-нумерация)
DHT_PIN = "D4"

# Если ни один драйвер датчика не установлен — сервис всё равно можно
# запустить и проверить веб-интерфейс на демо-данных.
SIMULATE = not (HAVE_DHT and HAVE_MHZ19)

_dht_device = None
if HAVE_DHT:
    _dht_device = adafruit_dht.DHT22(getattr(board, DHT_PIN))


def _read_temp_humidity() -> tuple[float | None, float | None]:
    if not HAVE_DHT:
        return None, None
    try:
        return _dht_device.temperature, _dht_device.humidity
    except RuntimeError:
        # DHT22 периодически отдаёт одиночные сбойные чтения — это нормально
        return None, None


def _read_co2() -> int | None:
    if not HAVE_MHZ19:
        return None
    try:
        result = mh_z19.read()
        return result.get("co2") if result else None
    except Exception:  # noqa: BLE001
        return None


def _simulate() -> dict:
    return {
        "temperature": round(20 + random.uniform(-1.5, 1.5), 1),
        "humidity": round(45 + random.uniform(-5, 5), 1),
        "co2": round(600 + random.uniform(-100, 300)),
        "simulated": True,
        "timestamp": time.time(),
    }


def read_all() -> dict:
    if SIMULATE:
        return _simulate()

    temperature, humidity = _read_temp_humidity()
    co2 = _read_co2()
    return {
        "temperature": temperature,
        "humidity": humidity,
        "co2": co2,
        "simulated": False,
        "timestamp": time.time(),
    }

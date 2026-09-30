"""
Датчики TeleRepka для Repka OS.

Аппаратная конфигурация:
- DHT11 DATA: физический контакт 33 40-pin разъёма;
- DHT11: RepkaPi.GPIO в режиме GPIO.BOARD;
- MH-Z19B: S-UART, /dev/ttyS2, 9600 бод;
- /boot/repkaEnv.txt: overlays=s_uart.

Важно:
RepkaPi.GPIO 1.0.0 использует GPIO SysFS. На текущей Repka OS экспорт
линии разрешён root, поэтому процесс, который читает DHT11, должен
запускаться с root-правами (например, отдельным systemd-сервисом).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

try:
    import RepkaPi.GPIO as GPIO
except ImportError:
    GPIO = None  # type: ignore[assignment]

try:
    import serial
except ImportError:
    serial = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)

# --- Подтверждённая аппаратная конфигурация Repka Pi ---
DHT11_BOARD_PIN = 33
MHZ19_PORT = "/dev/ttyS2"
MHZ19_BAUDRATE = 9600

# DHT11 нельзя опрашивать чаще одного раза в секунду.
DHT11_MIN_INTERVAL_SECONDS = 1.2
DHT11_READ_ATTEMPTS = 3
DHT11_RETRY_DELAY_SECONDS = 1.1

# Тайминги протокола DHT11. Значения указаны в секундах.
DHT11_START_LOW_SECONDS = 0.020
DHT11_RELEASE_SETTLE_SECONDS = 0.000040
DHT11_RESPONSE_TIMEOUT_SECONDS = 0.000300
DHT11_BIT_TIMEOUT_SECONDS = 0.000180
DHT11_ONE_THRESHOLD_SECONDS = 0.000050

MHZ19_COMMAND_READ_CO2 = bytes.fromhex("FF 01 86 00 00 00 00 00 79")
MHZ19_RESPONSE_SIZE = 9
MHZ19_TIMEOUT_SECONDS = 2.0

_last_dht11_read_at = 0.0
_last_dht11_value: tuple[Optional[float], Optional[float]] = (None, None)


def _wait_for_level(
    level: int,
    timeout_seconds: float,
) -> tuple[bool, int]:
    """Ждёт заданный уровень на линии и возвращает (успех, время начала ns)."""
    deadline = time.perf_counter_ns() + int(timeout_seconds * 1_000_000_000)

    while time.perf_counter_ns() < deadline:
        if GPIO.input(DHT11_BOARD_PIN) == level:
            return True, time.perf_counter_ns()

    return False, 0


def _measure_level_duration(
    level: int,
    timeout_seconds: float,
) -> tuple[bool, int]:
    """Измеряет длительность указанного уровня сигнала DHT11 в наносекундах."""
    found, started_at = _wait_for_level(level, timeout_seconds)
    if not found:
        return False, 0

    deadline = time.perf_counter_ns() + int(timeout_seconds * 1_000_000_000)

    while time.perf_counter_ns() < deadline:
        if GPIO.input(DHT11_BOARD_PIN) != level:
            return True, time.perf_counter_ns() - started_at

    return False, 0


def _read_dht11_raw() -> Optional[tuple[int, int, int, int, int]]:
    """
    Выполняет один низкоуровневый цикл чтения DHT11.

    Возвращает пять байтов:
    humidity_int, humidity_decimal, temperature_int, temperature_decimal, checksum.
    При ошибке таймингов или checksum возвращает None.
    """
    if GPIO is None:
        LOGGER.error("RepkaPi.GPIO не установлен.")
        return None

    configured = False

    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)

        # Стартовый сигнал: контроллер удерживает линию LOW не менее 18 мс.
        GPIO.setup(DHT11_BOARD_PIN, GPIO.OUT, initial=GPIO.HIGH)
        time.sleep(0.002)

        GPIO.output(DHT11_BOARD_PIN, GPIO.LOW)
        time.sleep(DHT11_START_LOW_SECONDS)

        GPIO.output(DHT11_BOARD_PIN, GPIO.HIGH)
        time.sleep(DHT11_RELEASE_SETTLE_SECONDS)

        # Отпускаем линию: DHT11 формирует собственный ответ.
        GPIO.setup(DHT11_BOARD_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        configured = True

        # Ответ DHT11: LOW ~80 мкс, HIGH ~80 мкс, затем LOW перед данными.
        if not _wait_for_level(GPIO.LOW, DHT11_RESPONSE_TIMEOUT_SECONDS)[0]:
            LOGGER.debug("DHT11: не получен начальный LOW.")
            return None

        if not _measure_level_duration(
            GPIO.LOW,
            DHT11_RESPONSE_TIMEOUT_SECONDS,
        )[0]:
            LOGGER.debug("DHT11: не завершился стартовый LOW.")
            return None

        if not _measure_level_duration(
            GPIO.HIGH,
            DHT11_RESPONSE_TIMEOUT_SECONDS,
        )[0]:
            LOGGER.debug("DHT11: не завершился стартовый HIGH.")
            return None

        # После preamble датчик начинает первый бит с LOW.
        if not _wait_for_level(GPIO.LOW, DHT11_RESPONSE_TIMEOUT_SECONDS)[0]:
            LOGGER.debug("DHT11: не начался первый бит.")
            return None

        data: list[int] = []

        for _byte_index in range(5):
            value = 0

            for _bit_index in range(8):
                # Каждый бит начинается с LOW примерно 50 мкс.
                if not _measure_level_duration(
                    GPIO.LOW,
                    DHT11_BIT_TIMEOUT_SECONDS,
                )[0]:
                    LOGGER.debug("DHT11: timeout LOW в бите.")
                    return None

                # Длина следующего HIGH определяет значение:
                # 0: около 26–28 мкс; 1: около 70 мкс.
                ok, high_duration_ns = _measure_level_duration(
                    GPIO.HIGH,
                    DHT11_BIT_TIMEOUT_SECONDS,
                )
                if not ok:
                    LOGGER.debug("DHT11: timeout HIGH в бите.")
                    return None

                value <<= 1
                if high_duration_ns >= int(
                    DHT11_ONE_THRESHOLD_SECONDS * 1_000_000_000
                ):
                    value |= 1

            data.append(value)

        humidity_int, humidity_decimal, temperature_int, temperature_decimal, checksum = data

        if ((humidity_int + humidity_decimal + temperature_int + temperature_decimal) & 0xFF) != checksum:
            LOGGER.debug(
                "DHT11: checksum не совпала: data=%s, checksum=0x%02x",
                data[:4],
                checksum,
            )
            return None

        return (
            humidity_int,
            humidity_decimal,
            temperature_int,
            temperature_decimal,
            checksum,
        )

    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.warning("DHT11: ошибка GPIO: %s", exc)
        return None

    finally:
        if configured:
            try:
                GPIO.cleanup(DHT11_BOARD_PIN)
            except (OSError, RuntimeError, ValueError) as exc:
                LOGGER.debug("DHT11: GPIO cleanup не выполнен: %s", exc)


def read_dht11() -> tuple[Optional[float], Optional[float]]:
    """
    DHT11 отключён: TeleRepka переводится на DS18B20.

    DS18B20 будет отдавать только температуру через штатный драйвер 1-Wire.
    До его подключения возвращаем пустые значения без повторных ошибок GPIO.
    """
    return None, None


def _mhz19_checksum(packet: bytes) -> int:
    """
    Возвращает checksum протокола MH-Z19 для байтов packet[1:8].

    Формула датчика:
    checksum = 0xFF - (sum(bytes 1..7) & 0xFF) + 1.
    """
    return (0xFF - (sum(packet[1:8]) & 0xFF) + 1) & 0xFF


def read_mhz19b() -> Optional[int]:
    """
    Запрашивает концентрацию CO₂ у MH-Z19B.

    Возвращает значение ppm как int либо None, если датчик не ответил,
    порт недоступен или пакет не прошёл проверку.
    """
    if serial is None:
        LOGGER.error(
            "Не установлен pyserial. Установите пакет: sudo apt install python3-serial"
        )
        return None

    try:
        with serial.Serial(
            port=MHZ19_PORT,
            baudrate=MHZ19_BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=MHZ19_TIMEOUT_SECONDS,
        ) as port:
            port.reset_input_buffer()
            port.write(MHZ19_COMMAND_READ_CO2)
            port.flush()

            response = port.read(MHZ19_RESPONSE_SIZE)

    except (OSError, serial.SerialException) as exc:
        LOGGER.warning("MH-Z19B: невозможно открыть %s: %s", MHZ19_PORT, exc)
        return None

    if len(response) != MHZ19_RESPONSE_SIZE:
        LOGGER.warning(
            "MH-Z19B: неполный ответ (%d из %d байт): %s",
            len(response),
            MHZ19_RESPONSE_SIZE,
            response.hex(" "),
        )
        return None

    if response[0] != 0xFF or response[1] != 0x86:
        LOGGER.warning(
            "MH-Z19B: неожиданный пакет: %s",
            response.hex(" "),
        )
        return None

    expected_checksum = _mhz19_checksum(response)
    if response[8] != expected_checksum:
        LOGGER.warning(
            "MH-Z19B: неверная checksum: получено 0x%02x, ожидалось 0x%02x; пакет: %s",
            response[8],
            expected_checksum,
            response.hex(" "),
        )
        return None

    return (response[2] << 8) | response[3]


def read_all() -> dict[str, Optional[float] | Optional[int]]:
    """
    Возвращает все доступные показания в одном словаре.

    Формат подходит для JSON-ответа:
    {
        "temperature": 24.0,
        "humidity": 42.0,
        "co2": 776,
    }
    """
    temperature, humidity = read_dht11()
    co2 = read_mhz19b()

    return {
        "temperature": temperature,
        "humidity": humidity,
        "co2": co2,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    temperature_value, humidity_value = read_dht11()
    co2_value = read_mhz19b()

    print(f"Temperature: {temperature_value!r} °C")
    print(f"Humidity: {humidity_value!r} %")
    print(f"CO2: {co2_value!r} ppm")

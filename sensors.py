"""
Опрос DHT11 через GPIO sysfs и опционального MH-Z19 через UART.

DHT11:
- физический пин 11 Repka Pi 4;
- системный GPIO 111 (PD15);
- используется пользовательский sysfs GPIO-интерфейс.

Важно:
sysfs GPIO устарел в современных ядрах Linux; предпочтителен GPIO character
device (/dev/gpiochip*) через libgpiod. Но этот вариант оставлен совместимым
с существующей конфигурацией Repka Pi.
"""
from __future__ import annotations

import os
import time
from typing import Callable

try:
    import eventlet.tpool
    HAVE_EVENTLET_TPOOL = True
except ImportError:
    HAVE_EVENTLET_TPOOL = False

try:
    import mh_z19  # type: ignore
    HAVE_MHZ19 = True
except ImportError:
    HAVE_MHZ19 = False


DHT11_GPIO = 111
GPIO_BASE = "/sys/class/gpio"
GPIO_PIN_DIR = f"{GPIO_BASE}/gpio{DHT11_GPIO}"
GPIO_DIRECTION_PATH = f"{GPIO_PIN_DIR}/direction"
GPIO_VALUE_PATH = f"{GPIO_PIN_DIR}/value"

_MAX_READ_ATTEMPTS = 8
_RETRY_DELAY_S = 1.0

# Таймаут выражен не в микросекундах, а в количестве обращений к sysfs.
# Значение следует подобрать для конкретной платы/ядра при необходимости.
_LOOP_TIMEOUT_ITERATIONS = 20_000

# DHT11: HIGH для 0 около 26–28 мкс, для 1 около 70 мкс.
# Минимальный разрыв между кластерами, чтобы считать измерение достоверным.
_MIN_CLUSTER_GAP_ITERATIONS = 2

_last_good_temperature: float | None = None
_last_good_humidity: float | None = None


def _gpio_export() -> bool:
    """Экспортирует GPIO в sysfs и ждёт появления каталога gpioN."""
    if os.path.isdir(GPIO_PIN_DIR):
        return True

    try:
        with open(f"{GPIO_BASE}/export", "w", encoding="ascii") as fp:
            fp.write(str(DHT11_GPIO))
    except OSError:
        # GPIO уже мог быть экспортирован другим процессом.
        pass

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if os.path.isdir(GPIO_PIN_DIR):
            return True
        time.sleep(0.01)

    return os.path.isdir(GPIO_PIN_DIR)


def _gpio_set_direction(direction: str) -> bool:
    """
    Допустимые direction для sysfs: in, out, high, low.

    'high' и 'low' важны: позволяют выставить начальное состояние выхода
    одновременно с переключением направления, уменьшая паразитные импульсы.
    """
    try:
        with open(GPIO_DIRECTION_PATH, "w", encoding="ascii") as fp:
            fp.write(direction)
        return True
    except OSError:
        return False


def _gpio_write(value: int) -> bool:
    try:
        with open(GPIO_VALUE_PATH, "w", encoding="ascii") as fp:
            fp.write("1" if value else "0")
        return True
    except OSError:
        return False


def _busy_wait_us(microseconds: float) -> None:
    """Busy-wait для критичного окна 20–40 мкс после стартового LOW."""
    deadline = time.perf_counter() + microseconds / 1_000_000.0
    while time.perf_counter() < deadline:
        pass


def _wait_while_level(
    read_level: Callable[[], int],
    expected_level: int,
) -> int | None:
    """
    Ждёт, пока вход перестанет быть expected_level.

    Возвращает количество итераций чтения либо None по таймауту.
    """
    count = 0

    while read_level() == expected_level:
        count += 1
        if count >= _LOOP_TIMEOUT_ITERATIONS:
            return None

    return count


def _read_dht11_raw_blocking() -> tuple[int, int, int, int, int] | None:
    """
    Выполняет один физический цикл чтения DHT11.

    Возвращает:
        (humidity_int, humidity_dec, temp_int, temp_dec, checksum)

    Либо None, если ответ не получен, имеет неверную структуру или checksum.
    """
    if not _gpio_export():
        return None

    # Важно: "high", а не "out" + _gpio_write(1).
    # "out" в sysfs по умолчанию устанавливает LOW.
    if not _gpio_set_direction("high"):
        return None

    # Линия должна быть в HIGH перед стартовым импульсом.
    time.sleep(0.05)

    # Стартовый импульс: LOW >= 18 мс.
    if not _gpio_write(0):
        return None
    time.sleep(0.020)

    # Отпускаем линию. DHT11 ожидает HIGH примерно 20–40 мкс.
    if not _gpio_write(1):
        return None
    _busy_wait_us(30)

    # Линия должна быть отпущена: датчик и pull-up формируют уровни.
    if not _gpio_set_direction("in"):
        return None

    try:
        fd = os.open(GPIO_VALUE_PATH, os.O_RDONLY)
    except OSError:
        return None

    def read_level() -> int:
        os.lseek(fd, 0, os.SEEK_SET)
        return 1 if os.read(fd, 8).strip() == b"1" else 0

    try:
        # Синхронизация ответа DHT11:
        #
        # после стартового HIGH датчик выдаёт:
        #   LOW  ~80 мкс
        #   HIGH ~80 мкс
        # затем начинается LOW-разделитель первого бита.
        #
        # Не предполагаем, в какой точке ответа мы начали читать:
        # сначала ждём появления LOW, затем ждём его окончания,
        # затем ждём окончания HIGH-преамбулы.
        #
        # Если к моменту первого read_level() линия уже LOW, первый цикл
        # сразу корректно измеряет оставшуюся часть LOW ответа.
        if read_level() == 1:
            if _wait_while_level(read_level, 1) is None:
                return None

        # LOW ответа датчика.
        if _wait_while_level(read_level, 0) is None:
            return None

        # HIGH ответа датчика.
        if _wait_while_level(read_level, 1) is None:
            return None

        # Теперь линия должна находиться в LOW-разделителе первого бита.
        high_counts: list[int] = []

        for _ in range(40):
            # LOW-разделитель текущего бита, около 50 мкс.
            if _wait_while_level(read_level, 0) is None:
                return None

            # HIGH-импульс текущего бита:
            # короткий для 0, длинный для 1.
            high_duration = _wait_while_level(read_level, 1)
            if high_duration is None:
                return None

            high_counts.append(high_duration)

    finally:
        os.close(fd)

    if len(high_counts) != 40:
        return None

    # Нельзя брать среднее по всем битам:
    # оно меняется в зависимости от того, сколько в кадре единиц.
    #
    # Вместо этого ищем разрыв между двумя отсортированными кластерами:
    # короткие HIGH = 0, длинные HIGH = 1.
    ordered = sorted(high_counts)
    gaps = [
        ordered[index + 1] - ordered[index]
        for index in range(len(ordered) - 1)
    ]

    largest_gap = max(gaps, default=0)
    split_index = gaps.index(largest_gap) if gaps else -1

    if largest_gap < _MIN_CLUSTER_GAP_ITERATIONS:
        return None

    threshold = (ordered[split_index] + ordered[split_index + 1]) / 2.0
    bits = [1 if duration > threshold else 0 for duration in high_counts]

    byte_values: list[int] = []

    for byte_index in range(5):
        byte = 0

        for bit in bits[byte_index * 8:(byte_index + 1) * 8]:
            byte = (byte << 1) | bit

        byte_values.append(byte)

    humidity_int, humidity_dec, temp_int, temp_dec, checksum = byte_values

    calculated_checksum = (
        humidity_int + humidity_dec + temp_int + temp_dec
    ) & 0xFF

    if calculated_checksum != checksum:
        return None

    # Для классического DHT11 дробные части обычно равны нулю.
    # Это не обязательная проверка: некоторые совместимые модули могут
    # вести себя иначе, поэтому здесь данные не отбрасываются.
    return humidity_int, humidity_dec, temp_int, temp_dec, checksum


def _read_dht11_raw() -> tuple[int, int, int, int, int] | None:
    """Выносит блокирующее чтение в нативный поток при наличии eventlet."""
    if HAVE_EVENTLET_TPOOL:
        return eventlet.tpool.execute(_read_dht11_raw_blocking)

    return _read_dht11_raw_blocking()


def read_dht11() -> tuple[float | None, float | None]:
    """
    Читает DHT11 с повторными попытками.

    При всех неудачах возвращает последнее корректно прочитанное значение.
    """
    global _last_good_temperature, _last_good_humidity

    for attempt in range(_MAX_READ_ATTEMPTS):
        raw = _read_dht11_raw()

        if raw is not None:
            humidity_int, humidity_dec, temp_int, temp_dec, _checksum = raw

            _last_good_humidity = (
                float(humidity_int) + float(humidity_dec) / 10.0
            )
            _last_good_temperature = (
                float(temp_int) + float(temp_dec) / 10.0
            )

            return _last_good_temperature, _last_good_humidity

        if attempt < _MAX_READ_ATTEMPTS - 1:
            time.sleep(_RETRY_DELAY_S)

    return _last_good_temperature, _last_good_humidity


def _read_co2() -> int | None:
    """Читает CO₂ с MH-Z19, если библиотека установлена и датчик доступен."""
    if not HAVE_MHZ19:
        return None

    try:
        result = mh_z19.read()
        value = result.get("co2") if result else None
        return int(value) if value is not None else None
    except Exception:
        return None


def read_all() -> dict[str, float | int | bool | None]:
    """Возвращает единый набор показаний для приложения."""
    temperature, humidity = read_dht11()

    return {
        "temperature": temperature,
        "humidity": humidity,
        "co2": _read_co2(),
        "simulated": False,
        "timestamp": time.time(),
    }

"""
Опрос DHT11 через GPIO character device (libgpiod v1.x) и опционального
MH-Z19 через UART.

DHT11:
- физический пин 11 Repka Pi 4;
- системный GPIO 111 (PD15), находится на /dev/gpiochip1, line 111;
- используется libgpiod (python3-libgpiod, API v1.6.x), а не устаревший
  sysfs (/sys/class/gpio) — переход сделан из-за того, что sysfs давал
  систематическую потерю ~9-25 бит из 40 на каждом опросе (подтверждено
  многократной инструментированной трассировкой: физика линии стабильна,
  0=>7459 без единого сбоя в покое, но при реальном опросе с частыми
  файловыми I/O через /sys/class/gpio/gpioNNN/value код регулярно не
  успевал за протоколом с точностью до единиц микросекунд).

libgpiod читает/пишет значения через ioctl() на уже открытый файловый
дескриптор чипа, а не через open()/read() отдельного sysfs-файла на
каждое обращение — это заметно меньше накладных расходов на итерацию
busy-wait, что критично для протокола DHT11 (биты длятся 26-70 мкс).

Протокол опроса (см. datasheet DHT11):
  1. Хост держит линию LOW не менее 18 мс.
  2. Хост отпускает линию (HIGH) на 20-40 мкс (busy-wait), переключается
     на вход.
  3. Датчик отвечает: LOW ~80 мкс, затем HIGH ~80 мкс (преамбула).
  4. Датчик передаёт 40 бит: LOW ~50 мкс (разделитель), HIGH ~26-28 мкс
     (бит "0") либо ~70 мкс (бит "1").

Синхронизация не предполагает, в какой фазе кадра начато чтение — сначала
дожидаемся ухода с текущего уровня, затем явно проходим LOW и HIGH фазы
преамбулы, и только потом считываем 40 бит.

Длительность бита измеряется подсчётом итераций busy-wait цикла (число
итераций пропорционально времени), а порог 0/1 определяется поиском
наибольшего разрыва в отсортированном списке 40 измерений — это устойчиво
независимо от соотношения нулей и единиц в конкретном кадре.

CO2 (MH-Z19, опционально) по-прежнему опрашивается через UART, если
установлен пакет mh-z19.
"""
from __future__ import annotations

import time
from typing import Callable

try:
    import gpiod  # python3-libgpiod, API v1.6.x
    HAVE_GPIOD = True
except ImportError:
    HAVE_GPIOD = False

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

DHT11_GPIOCHIP = "/dev/gpiochip1"  # подтверждено: line 111 находится здесь
DHT11_LINE_OFFSET = 111  # физический пин 11 -> gpio111 (PD15)

_MAX_READ_ATTEMPTS = 8
_RETRY_DELAY_S = 1.0
_LOOP_TIMEOUT_ITERATIONS = 20_000
_MIN_CLUSTER_GAP_ITERATIONS = 2


_last_good_temperature: float | None = None
_last_good_humidity: float | None = None


def _busy_wait_us(microseconds: float) -> None:
    deadline = time.perf_counter() + microseconds / 1_000_000.0
    while time.perf_counter() < deadline:
        pass


def _wait_while_level(read_level: Callable[[], int], expected_level: int) -> int | None:
    count = 0
    while read_level() == expected_level:
        count += 1
        if count >= _LOOP_TIMEOUT_ITERATIONS:
            return None
    return count


def _read_dht11_raw_blocking() -> tuple[int, int, int, int, int] | None:
    if not HAVE_GPIOD:
        return None

    chip = gpiod.Chip(DHT11_GPIOCHIP)
    try:
        line = chip.get_line(DHT11_LINE_OFFSET)

        # Стартовый импульс: настраиваем как выход, HIGH -> LOW(18мс) -> HIGH.
        line.request(consumer="dht11", type=gpiod.LINE_REQ_DIR_OUT, default_val=1)
        time.sleep(0.05)
        line.set_value(0)
        time.sleep(0.020)
        line.set_value(1)
        _busy_wait_us(30)
        line.release()

        # Переключаемся на вход для чтения ответа датчика.
        line.request(consumer="dht11", type=gpiod.LINE_REQ_DIR_IN)

        def read_level() -> int:
            return line.get_value()

        try:
            # Синхронизация без предположений о текущей фазе кадра.
            initial = read_level()
            if initial == 1:
                if _wait_while_level(read_level, 1) is None:
                    return None
            if _wait_while_level(read_level, 0) is None:  # LOW ответа
                return None
            if _wait_while_level(read_level, 1) is None:  # HIGH преамбулы
                return None

            high_counts: list[int] = []
            for _ in range(40):
                if _wait_while_level(read_level, 0) is None:  # LOW-разделитель
                    return None
                high_duration = _wait_while_level(read_level, 1)  # HIGH-бит
                if high_duration is None:
                    return None
                high_counts.append(high_duration)
        finally:
            line.release()
    finally:
        chip.close()

    if len(high_counts) != 40:
        return None

    ordered = sorted(high_counts)
    gaps = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
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
    calculated_checksum = (humidity_int + humidity_dec + temp_int + temp_dec) & 0xFF
    if calculated_checksum != checksum:
        return None

    return humidity_int, humidity_dec, temp_int, temp_dec, checksum


def _read_dht11_raw() -> tuple[int, int, int, int, int] | None:
    if HAVE_EVENTLET_TPOOL:
        return eventlet.tpool.execute(_read_dht11_raw_blocking)
    return _read_dht11_raw_blocking()


def read_dht11() -> tuple[float | None, float | None]:
    global _last_good_temperature, _last_good_humidity

    for attempt in range(_MAX_READ_ATTEMPTS):
        raw = _read_dht11_raw()
        if raw is not None:
            humidity_int, humidity_dec, temp_int, temp_dec, _ = raw
            _last_good_humidity = float(humidity_int) + float(humidity_dec) / 10.0
            _last_good_temperature = float(temp_int) + float(temp_dec) / 10.0
            return _last_good_temperature, _last_good_humidity
        if attempt < _MAX_READ_ATTEMPTS - 1:
            time.sleep(_RETRY_DELAY_S)

    return _last_good_temperature, _last_good_humidity


def _read_co2() -> int | None:
    if not HAVE_MHZ19:
        return None
    try:
        result = mh_z19.read()
        value = result.get("co2") if result else None
        return int(value) if value is not None else None
    except Exception:
        return None


def read_all() -> dict:
    temperature, humidity = read_dht11()
    return {
        "temperature": temperature,
        "humidity": humidity,
        "co2": _read_co2(),
        "simulated": False,
        "timestamp": time.time(),
    }

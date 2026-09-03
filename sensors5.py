"""
Опрос датчиков, подключённых к GPIO/I2C/UART Repka Pi 4.

ДАТЧИК ТЕМПЕРАТУРЫ И ВЛАЖНОСТИ: DHT11 (цифровой GPIO, bit-banging).

DHT11 подключён к физическому пину 11, что соответствует системному
GPIO 111 (PD15) — соответствие подтверждено вручную через sysfs
(см. README.md, раздел "Датчики DHT11 и MQ-135").

ВАЖНО про eventlet (app.py делает eventlet.monkey_patch()):
Весь блокирующий bit-banging выполняется в настоящем OS-потоке через
eventlet.tpool.execute() — не блокирует основной хаб/WebSocket.

ИСТОРИЯ ОТЛАДКИ (важно для дальнейшей поддержки):
1) Первая версия мерила длительность HIGH через time.perf_counter() до/
   после фронта — давала нестабильные короткие "мусорные" интервалы.
2) Переход на подсчёт итераций busy-wait (как в C/WiringPi) вместо
   секундомера сразу дал чистые, стабильные показания без выбросов.
3) НО даже с чистым подсчётом итераций реальная трассировка показала:
   мы стабильно теряем ПЕРВЫЕ ~19 переходов (преамбулу и начало данных).
   Причина — вызов time.sleep(0.00003) (30 мкс) перед переключением
   direction "out"->"in": на Linux time.sleep() для интервалов <1 мс не
   гарантирует точность и на практике может растягиваться на десятки-
   сотни микросекунд из-за гранулярности планировщика ОС (см. Python
   docs: "suspension time may be longer than requested by an arbitrary
   amount"). За это время датчик уже успевает начать и частично передать
   ответ, и код упускает начало кадра.
4) Исправление: убрали time.sleep() для этого критичного 20-40 мкс окна,
   заменили на busy-wait по time.perf_counter() — тот же механизм, что и
   используется для чтения битов, с точностью порядка микросекунд вместо
   миллисекунд.

Протокол опроса (см. datasheet DHT11):
  1. Хост держит линию LOW не менее 18 мс.
  2. Хост отпускает линию (HIGH) на 20-40 мкс (busy-wait, НЕ time.sleep),
     переключается на вход.
  3. Датчик отвечает преамбулой: LOW ~80 мкс, HIGH ~80 мкс.
  4. Датчик передаёт 40 бит: LOW ~50 мкс (разделитель), затем HIGH
     ~26-28 мкс (бит "0") либо ~70 мкс (бит "1").

Измерение длительности бита — через подсчёт итераций busy-wait цикла
(число итераций пропорционально реальному времени), а не через явные
вызовы time.perf_counter() на каждой границе — это устойчивее к джиттеру.

Конец кадра определяется НЕ фиксированным количеством переходов, а по
факту: после 40-го бита датчик отпускает линию и резистор подтяжки держит
её HIGH бессрочно — следующая "итерация ожидания смены уровня" зависает.
Поэтому останавливаемся сразу после накопления 40 бит, не ожидая доп.
переходов.

Ошибки чтения (CRC не сошёлся, слишком мало переходов до таймаута) —
обычная ситуация для DHT11; возвращается последнее валидное значение.

CO2 (MH-Z19, опционально) по-прежнему опрашивается через UART, если
установлен пакет mh-z19.
"""
from __future__ import annotations

import os
import time

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

DHT11_GPIO = 111  # физический пин 11 -> gpio111 (PD15), подтверждено на плате
GPIO_BASE = "/sys/class/gpio"
GPIO_PIN_DIR = f"{GPIO_BASE}/gpio{DHT11_GPIO}"

_MAX_READ_ATTEMPTS = 8
_RETRY_DELAY_S = 1.0  # DHT11 нельзя опрашивать чаще раза в секунду
_LOOP_TIMEOUT_ITERATIONS = 20000  # защита от вечного цикла на одном уровне

_last_good_temperature: float | None = None
_last_good_humidity: float | None = None


def _gpio_export() -> None:
    if os.path.isdir(GPIO_PIN_DIR):
        return
    try:
        with open(f"{GPIO_BASE}/export", "w", encoding="ascii") as fp:
            fp.write(str(DHT11_GPIO))
    except OSError:
        pass


def _gpio_set_direction(direction: str) -> bool:
    try:
        with open(f"{GPIO_PIN_DIR}/direction", "w", encoding="ascii") as fp:
            fp.write(direction)
        return True
    except OSError:
        return False


def _gpio_write(value: int) -> None:
    try:
        with open(f"{GPIO_PIN_DIR}/value", "w", encoding="ascii") as fp:
            fp.write("1" if value else "0")
    except OSError:
        pass


def _busy_wait_us(microseconds: float) -> None:
    """Точный busy-wait через perf_counter() — НЕ time.sleep(), который
    на Linux не гарантирует точность для интервалов <1мс и может растянуть
    паузу на десятки-сотни микросекунд, съедая начало ответа DHT11."""
    deadline = time.perf_counter() + microseconds / 1_000_000.0
    while time.perf_counter() < deadline:
        pass


def _read_dht11_raw_blocking() -> tuple[int, int, int, int, int] | None:
    """Один цикл опроса DHT11. Возвращает 5 байт или None при ошибке."""
    _gpio_export()
    if not _gpio_set_direction("out"):
        return None

    _gpio_write(1)
    time.sleep(0.05)
    _gpio_write(0)
    time.sleep(0.018)  # >=18мс старт-сигнал — здесь точность не критична
    _gpio_write(1)
    _busy_wait_us(30)  # 20-40 мкс — КРИТИЧНО точный busy-wait, не sleep()

    if not _gpio_set_direction("in"):
        return None

    try:
        fd = os.open(f"{GPIO_PIN_DIR}/value", os.O_RDONLY)
    except OSError:
        return None

    def read_level() -> int:
        os.lseek(fd, 0, os.SEEK_SET)
        return 1 if os.read(fd, 8).strip() == b"1" else 0

    try:
        last_state = read_level()
        # Преамбула: LOW ~80мкс, затем HIGH ~80мкс — пропускаем 2 перехода.
        for _ in range(2):
            count = 0
            while read_level() == last_state:
                count += 1
                if count >= _LOOP_TIMEOUT_ITERATIONS:
                    return None
            last_state = 1 - last_state

        high_counts: list[int] = []
        frame_ok = True
        for _ in range(40):
            # LOW-разделитель (~50мкс) — считаем итерации, но не используем
            # для решения о бите, только чтобы пройти этот интервал.
            count_low = 0
            while read_level() == last_state:
                count_low += 1
                if count_low >= _LOOP_TIMEOUT_ITERATIONS:
                    frame_ok = False
                    break
            if not frame_ok:
                break
            last_state = 1 - last_state

            # HIGH-бит данных — длительность (в итерациях) определяет 0/1.
            count_high = 0
            while read_level() == last_state:
                count_high += 1
                if count_high >= _LOOP_TIMEOUT_ITERATIONS:
                    frame_ok = False
                    break
            if not frame_ok:
                break
            high_counts.append(count_high)
            last_state = 1 - last_state
    finally:
        os.close(fd)

    if len(high_counts) != 40:
        return None

    # Порог между битом "0" (короткий HIGH) и "1" (длинный HIGH) —
    # среднее по всем 40 значениям надёжно разделяет два кластера
    # (короткие ~3-4 итерации, длинные ~12-14 итераций на данной плате).
    threshold = sum(high_counts) / len(high_counts)
    bits = [1 if c > threshold else 0 for c in high_counts]

    byte_values = []
    for i in range(5):
        byte = 0
        for bit in bits[i * 8:(i + 1) * 8]:
            byte = (byte << 1) | bit
        byte_values.append(byte)

    humidity_int, humidity_dec, temp_int, temp_dec, checksum = byte_values
    if (humidity_int + humidity_dec + temp_int + temp_dec) & 0xFF != checksum:
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
            _last_good_humidity = float(humidity_int) + humidity_dec / 10.0
            _last_good_temperature = float(temp_int) + temp_dec / 10.0
            return _last_good_temperature, _last_good_humidity
        if attempt < _MAX_READ_ATTEMPTS - 1:
            time.sleep(_RETRY_DELAY_S)

    return _last_good_temperature, _last_good_humidity


def _read_co2() -> int | None:
    if not HAVE_MHZ19:
        return None
    try:
        result = mh_z19.read()
        return result.get("co2") if result else None
    except Exception:  # noqa: BLE001
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

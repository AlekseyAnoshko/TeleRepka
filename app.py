"""
Repka Pi Lab Hub — центральный веб-сервис лаборатории.

Функции (всё на одном сайте, единый порт 127.0.0.1:5000, наружу отдаётся
через nginx по имени telerepka-k207.istu.int — см. nginx_telerepka.conf):
- "/" — главная страница для ТВ (kiosk), датчики + статус рамок
- "/broadcast" — страница сотрудника: трансляция экрана/камеры на ТВ (WebRTC)
- "/slideshow.jpg" — раздача следующего слайда для ESP32 PhotoFrame
- "/photoframe/<name>/<subpath>" — reverse-proxy на веб-интерфейс
  конкретной фоторамки (см. ниже, почему это нужно)

Почему нужен reverse-proxy для рамок: сама рамка живёт в подсети Wi-Fi
хотспота Repka Pi (10.42.0.x) — эта подсеть недоступна снаружи (из сети
университета, с обычного Wi-Fi ноутбука сотрудника и т.п.), там просто
нет маршрута. Раньше страница отдавала прямую ссылку вида http://10.42.0.101/,
которая работала только для устройств, физически подключённых к хотспоту
рамки. Теперь вместо прямой ссылки используется /photoframe/<имя>/ —
запрос до рамки выполняет сам процесс app.py (он и есть шлюз хотспота,
у него есть маршрут в 10.42.0.0/24), а результат отдаётся браузеру через
уже работающий домен telerepka-k207.istu.int, куда маршрут есть у всех.

Управление ТВ без клавиатуры/мыши: у телевизора лаборатории нет ввода —
все действия выполняет сотрудник, открыв тот же адрес
https://telerepka-k207.istu.int/ на своём компьютере. Значит "/" одновременно
открыта в двух ролях: как немой Chromium-kiosk на самой Repka Pi (подключён
к ТВ по HDMI, запускается через ?kiosk=1 в URL — см. lab-hub-kiosk.service)
и как интерактивная копия у сотрудника. Кнопки RuTube/YouTube/Яндекс.Музыки
поэтому не переходят по ссылке сами, а шлют команду open-url-on-tv через
Socket.IO — реальный переход по URL выполняет только тот клиент, который
зарегистрировался как настоящий ТВ (tv-join), то есть Chromium с ?kiosk=1.

Запуск: python3 app.py
Слушает 127.0.0.1:5000 по HTTP. TLS-терминацию и единую точку входа
(80 и 443, домен telerepka-k207.istu.int) обеспечивает nginx перед этим
процессом — см. nginx_telerepka.conf.

Примечание про шум в логах: в сетях с периодическими сканерами/ботами
(например, в сети университета) возможны единичные трассировки вида
"SSLError: [SSL: SSLV3_ALERT_CERTIFICATE_UNKNOWN]" — это безобидно, клиент
просто отклоняет самоподписанный сертификат без взаимодействия с
пользователем. Такие трассировки приглушаются через hub_exceptions(False).
"""
import eventlet
eventlet.monkey_patch()

import eventlet.debug
eventlet.debug.hub_exceptions(False)

import itertools
import json
import os
import threading
from pathlib import Path

from flask import Flask, Response, abort, render_template, request, send_file
from flask_socketio import SocketIO, emit, join_room

import sensors
import photoframes

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "sensor_poll_interval_sec": 5,
    "photoframe_poll_interval_sec": 30,
    "photoframes": [
        {"name": "Фоторамка №1", "ip": "10.42.0.101"},
    ],
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
    return DEFAULT_CONFIG


config = load_config()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("LAB_HUB_SECRET", "change-me")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Подключённые HDMI-приёмники (Chromium kiosk на Repka Pi, страница "/" с ?kiosk=1).
tv_clients: set[str] = set()

# Домены, на которые разрешено удалённо переключать ТВ кнопками медиасервисов.
# Без этого белого списка любой, кто достучится до Socket.IO-эндпоинта,
# мог бы заставить киоск открыть произвольную страницу.
ALLOWED_MEDIA_PREFIXES = (
    "https://rutube.ru",
    "https://www.youtube.com",
    "https://music.yandex.ru",
)

# --- HTTP routes: главная панель и трансляция ---------------------------

@app.route("/")
def index():
    """Главная страница — показывается на телевизоре лаборатории (kiosk-режим)."""
    return render_template("index.html")


@app.route("/broadcast")
def broadcast_page():
    """Страница сотрудника: трансляция экрана/камеры и звука на ТВ."""
    return render_template("broadcast.html")


# --- HTTP route: reverse-proxy на веб-интерфейс фоторамки ---------------
# Ссылки на страницу конкретной рамки в шаблонах должны указывать сюда
# (/photoframe/<имя>/), а не на её реальный IP в подсети 10.42.0.x —
# см. пояснение в шапке файла и в photoframes.py.

@app.route("/photoframe/<name>/", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/photoframe/<name>/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def photoframe_proxy(name: str, subpath: str):
    ip = photoframes.find_ip_by_name(config["photoframes"], name)
    if ip is None:
        abort(404, description=f"Фоторамка с именем '{name}' не найдена в config.json")

    upstream = photoframes.proxy_request(
        ip=ip,
        subpath=subpath,
        method=request.method,
        headers=dict(request.headers),
        data=request.get_data(),
        params=request.args,
    )

    excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    response_headers = [
        (k, v) for k, v in upstream.raw.headers.items()
        if k.lower() not in excluded_headers
    ]
    return Response(upstream.content, upstream.status_code, response_headers)


# --- HTTP route: слайд-шоу для ESP32 PhotoFrame --------------------------
# Перенесено из slideshow_server.py (был отдельным процессом на порту 5001).
# Прошивка рамки не умеет смотреть в сетевой каталог напрямую — она лишь
# периодически скачивает один и тот же URL, указанный в image_url (см.
# rotation_mode=url в docs/API.md репозитория esp32-photoframe). Этот
# маршрут отдаёт на каждый запрос следующее по очереди изображение из
# SLIDESHOW_DIR, так что каждый плановый опрос рамки сдвигает слайд вперёд.

SLIDESHOW_DIR = BASE_DIR / "pic_aaf"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

_slideshow_lock = threading.Lock()
_slideshow_cycle = None
_slideshow_files_snapshot: list[Path] = []


def _refresh_slideshow_cycle() -> None:
    """Перечитывает папку и пересоздаёт бесконечный циклический итератор,
    если состав файлов изменился (можно добавлять/удалять картинки без
    перезапуска сервера)."""
    global _slideshow_cycle, _slideshow_files_snapshot
    files = sorted(
        p for p in SLIDESHOW_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT
    )
    if files != _slideshow_files_snapshot:
        _slideshow_files_snapshot = files
        _slideshow_cycle = itertools.cycle(files) if files else None


@app.route("/slideshow.jpg")
def slideshow():
    with _slideshow_lock:
        _refresh_slideshow_cycle()
        if _slideshow_cycle is None:
            abort(404, description=f"Нет изображений в {SLIDESHOW_DIR}")
        next_file = next(_slideshow_cycle)
    return send_file(next_file)


# --- Датчики: фоновый поток публикует показания всем подключённым клиентам

def sensor_loop():
    while True:
        try:
            data = sensors.read_all()
            socketio.emit("sensor_data", data)
        except Exception as exc:  # noqa: BLE001
            socketio.emit("sensor_error", {"message": str(exc)})
        socketio.sleep(config["sensor_poll_interval_sec"])


# --- Фоторамки: фоновый поток опрашивает REST API каждой рамки

def photoframe_loop():
    while True:
        statuses = [photoframes.poll(f["name"], f["ip"]) for f in config["photoframes"]]
        socketio.emit("photoframe_status", statuses)
        socketio.sleep(config["photoframe_poll_interval_sec"])


@socketio.on("connect")
def on_connect():
    # Новому клиенту (например, ТВ после перезагрузки) сразу отдаём последние показания
    emit("sensor_data", sensors.read_all())


# --- WebRTC-сигнализация -------------------------------------------------
# ТВ-страница входит в комнату "tv". У каждого клиента Socket.IO автоматически
# есть персональная комната с именем, равным его sid — этим пользуемся для
# адресной пересылки offer/answer/ICE конкретному сотруднику.

@socketio.on("tv-join")
def tv_join():
    """Регистрируем Chromium-киоск как готовый HDMI-приёмник.
    Вызывается только настоящим ТВ (страница "/" открыта с ?kiosk=1) —
    обычная копия "/" в браузере сотрудника этот эвент не шлёт."""
    tv_clients.add(request.sid)
    join_room("tv")
    socketio.emit("tv-status", {"ready": True})


@socketio.on("tv-ready-check")
def tv_ready_check():
    """Отдаём сотруднику состояние HDMI-приёмника."""
    emit("tv-status", {"ready": bool(tv_clients)})


@socketio.on("disconnect")
def on_disconnect():
    if request.sid in tv_clients:
        tv_clients.discard(request.sid)
        socketio.emit("tv-status", {"ready": bool(tv_clients)})


@socketio.on("broadcaster-offer")
def broadcaster_offer(data):
    """data: {name, sdp}. Пересылаем предложение в комнату ТВ вместе с sid отправителя."""
    emit(
        "broadcaster-offer",
        {"sid": request.sid, "name": data["name"], "sdp": data["sdp"]},
        room="tv",
    )


@socketio.on("tv-answer")
def tv_answer(data):
    """data: {target_sid, sdp}. Ответ ТВ конкретному сотруднику по его sid."""
    emit("tv-answer", {"sdp": data["sdp"]}, room=data["target_sid"])


@socketio.on("ice-candidate")
def ice_candidate(data):
    """data: {target_sid, candidate}. Пересылка ICE-кандидатов в обе стороны.
    Сотрудник указывает target_sid="tv" (комната), ТВ — конкретный sid сотрудника."""
    emit(
        "ice-candidate",
        {"sid": request.sid, "candidate": data["candidate"]},
        room=data["target_sid"],
    )


@socketio.on("broadcaster-stop")
def broadcaster_stop():
    emit("broadcaster-stop", {"sid": request.sid}, room="tv")


# --- Удалённое переключение ТВ на RuTube / YouTube / Яндекс.Музыку ------
# У телевизора нет клавиатуры/мышки: все действия выполняет сотрудник,
# открыв тот же https://telerepka-k207.istu.int/ на своём компьютере.
# Поэтому кнопки медиасервисов на "/" не открывают ссылку в браузере
# кликающего, а шлют это событие — реальный переход выполняет только
# клиент, зарегистрированный как ТВ через tv-join (room "tv").

@socketio.on("open-url-on-tv")
def open_url_on_tv(data):
    """data: {url}. Проверяем URL по белому списку и пересылаем в комнату "tv"."""
    url = (data or {}).get("url", "")
    if not url.startswith(ALLOWED_MEDIA_PREFIXES):
        return
    emit("open-url-on-tv", {"url": url}, room="tv")


@socketio.on("tv-go-home")
def tv_go_home():
    """Возврат ТВ на дашборд — сотрудник вызывает это со своей копии страницы,
    когда телевизор нужно вернуть с RuTube/YouTube/Яндекс.Музыки на датчики."""
    emit("tv-go-home", {}, room="tv")


if __name__ == "__main__":
    SLIDESHOW_DIR.mkdir(exist_ok=True)
    socketio.start_background_task(sensor_loop)
    socketio.start_background_task(photoframe_loop)

    # TLS теперь только в nginx (см. nginx_telerepka.conf), поэтому Flask
    # слушает исключительно loopback — снаружи процесс недостижим напрямую.
    socketio.run(app, host="127.0.0.1", port=5000)

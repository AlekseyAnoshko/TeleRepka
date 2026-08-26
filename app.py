"""
Repka Pi Lab Hub — центральный веб-сервис лаборатории.

Функции (всё на одном сайте, единый порт 127.0.0.1:5000, наружу отдаётся
через nginx по имени telerepka-k207.istu.int — см. nginx_telerepka.conf):
- "/" — главная страница для ТВ (kiosk), датчики + статус рамок
- "/broadcast" — страница сотрудника: трансляция экрана/камеры на ТВ (WebRTC)
- "/slideshow.jpg" — раздача следующего слайда для ESP32 PhotoFrame
- "/photoframe/<name>/<subpath>" — reverse-proxy на веб-интерфейс
  конкретной фоторамки

Управление ТВ без клавиатуры/мыши: все действия выполняет сотрудник, открыв тот же
адрес https://telerepka-k207.istu.int/ на своём компьютере. Кнопки RuTube/
 YouTube/Яндекс.Музыки и «Вернуть дашборд на ТВ» — быстрые ярлыки: шлют
команду серверу (open-url-on-tv через Socket.IO / HTTP на /tv/go-home), который
управляет вкладкой ТВ через Chrome DevTools Protocol (CDP, порт 9222, см.
lab-hub-kiosk.service).

Полноценное интерактивное управление экраном ТВ — через CDP Screencast:
сервер держит одно постоянное CDP-соединение (класс ScreencastSession),
получает поток JPEG-кадров вкладки (Page.startScreencast) и ретранслирует
их всем подключённым браузерам сотрудников через Socket.IO. Клики/нажатия
сотрудника идут в обратную сторону через Input.dispatchMouseEvent/dispatchKeyEvent.

Запуск: python3 app.py. Слушает 127.0.0.1:5000 по HTTP, TLS и единая точка
входа — через nginx (см. nginx_telerepka.conf).
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

import requests as _requests
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

tv_clients: set[str] = set()

ALLOWED_MEDIA_PREFIXES = (
    "https://rutube.ru",
    "https://www.youtube.com",
    "https://music.yandex.ru",
)


@app.route("/")
def index():
    """Главная страница — показывается на телевизоре лаборатории (kiosk-режим)."""
    return render_template("index.html")


@app.route("/broadcast")
def broadcast_page():
    """Страница сотрудника: трансляция экрана/камеры и звука на ТВ."""
    return render_template("broadcast.html")


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


SLIDESHOW_DIR = BASE_DIR / "pic_aaf"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

_slideshow_lock = threading.Lock()
_slideshow_cycle = None
_slideshow_files_snapshot: list[Path] = []


def _refresh_slideshow_cycle() -> None:
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


def sensor_loop():
    while True:
        try:
            data = sensors.read_all()
            socketio.emit("sensor_data", data)
        except Exception as exc:  # noqa: BLE001
            socketio.emit("sensor_error", {"message": str(exc)})
        socketio.sleep(config["sensor_poll_interval_sec"])


def photoframe_loop():
    while True:
        statuses = [photoframes.poll(f["name"], f["ip"]) for f in config["photoframes"]]
        socketio.emit("photoframe_status", statuses)
        socketio.sleep(config["photoframe_poll_interval_sec"])


@socketio.on("connect")
def on_connect():
    emit("sensor_data", sensors.read_all())


@socketio.on("tv-join")
def tv_join():
    """Вызывается только настоящим ТВ (страница "/" открыта с ?kiosk=1)."""
    tv_clients.add(request.sid)
    join_room("tv")
    socketio.emit("tv-status", {"ready": True})


@socketio.on("tv-ready-check")
def tv_ready_check():
    emit("tv-status", {"ready": bool(tv_clients)})


@socketio.on("disconnect")
def on_disconnect():
    if request.sid in tv_clients:
        tv_clients.discard(request.sid)
        socketio.emit("tv-status", {"ready": bool(tv_clients)})


@socketio.on("broadcaster-offer")
def broadcaster_offer(data):
    emit(
        "broadcaster-offer",
        {"sid": request.sid, "name": data["name"], "sdp": data["sdp"]},
        room="tv",
    )


@socketio.on("tv-answer")
def tv_answer(data):
    emit("tv-answer", {"sdp": data["sdp"]}, room=data["target_sid"])


@socketio.on("ice-candidate")
def ice_candidate(data):
    emit(
        "ice-candidate",
        {"sid": request.sid, "candidate": data["candidate"]},
        room=data["target_sid"],
    )


@socketio.on("broadcaster-stop")
def broadcaster_stop():
    emit("broadcaster-stop", {"sid": request.sid}, room="tv")


@socketio.on("open-url-on-tv")
def open_url_on_tv(data):
    """data: {url}. Белый список + пересылка в комнату "tv"."""
    url = (data or {}).get("url", "")
    if not url.startswith(ALLOWED_MEDIA_PREFIXES):
        return
    emit("open-url-on-tv", {"url": url}, room="tv")


CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
DASHBOARD_URL = "http://localhost:5000/?kiosk=1"

MEDIA_KEY_CODES = {
    "play_pause": ("MediaPlayPause", 179),
    "next":       ("MediaTrackNext", 176),
    "prev":       ("MediaTrackPrevious", 177),
    "vol_up":     ("AudioVolumeUp", 175),
    "vol_down":   ("AudioVolumeDown", 174),
    "mute":       ("AudioVolumeMute", 173),
}


def _cdp_tab_ws_url():
    """webSocketDebuggerUrl единственной вкладки Chromium-kiosk или None."""
    try:
        tabs = _requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=3).json()
    except Exception:
        return None
    pages = [t for t in tabs if t.get("type") == "page"]
    if not pages:
        return None
    return pages[0].get("webSocketDebuggerUrl")


def _cdp_send(ws_url: str, method: str, params: dict) -> bool:
    try:
        import websocket
        ws = websocket.create_connection(ws_url, timeout=3)
        ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        ws.recv()
        ws.close()
        return True
    except Exception:
        return False


def _cdp_navigate(url: str) -> bool:
    ws_url = _cdp_tab_ws_url()
    if not ws_url:
        return False
    return _cdp_send(ws_url, "Page.navigate", {"url": url})


def _cdp_media_key(action: str) -> bool:
    if action not in MEDIA_KEY_CODES:
        return False
    code, vk = MEDIA_KEY_CODES[action]
    ws_url = _cdp_tab_ws_url()
    if not ws_url:
        return False
    key_down = {"type": "keyDown", "code": code, "windowsVirtualKeyCode": vk, "key": code}
    key_up = {"type": "keyUp", "code": code, "windowsVirtualKeyCode": vk, "key": code}
    return _cdp_send(ws_url, "Input.dispatchKeyEvent", key_down) and \
        _cdp_send(ws_url, "Input.dispatchKeyEvent", key_up)


@app.route("/tv/go-home", methods=["POST"])
def tv_go_home_http():
    """Кнопка «Вернуть дашборд на ТВ» — быстрый ярлык через CDP Page.navigate."""
    ok = _cdp_navigate(DASHBOARD_URL)
    return {"ok": ok}, (200 if ok else 502)


@app.route("/tv/media", methods=["POST"])
def tv_media_control():
    """play_pause/next/prev/vol_up/vol_down/mute через CDP-медиаклавиши (API сохранён, кнопки пульта в UI убраны)."""
    action = (request.get_json(silent=True) or {}).get("action", "")
    ok = _cdp_media_key(action)
    return {"ok": ok}, (200 if ok else 502)


class ScreencastSession:
    """Держит одно долгоживущее CDP-соединение с вкладкой ТВ, пересылает
    кадры всем подключённым клиентам через Socket.IO, обслуживает команды
    мыши/клавиатуры. Общий singleton — вкладка ТВ всегда одна (kiosk)."""

    def __init__(self):
        self._ws = None
        self._lock = threading.Lock()
        self._running = False
        self._msg_id = 1000
        self._viewers = 0

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def add_viewer(self) -> bool:
        with self._lock:
            self._viewers += 1
        return self._ensure_started()

    def remove_viewer(self):
        with self._lock:
            self._viewers = max(0, self._viewers - 1)
            should_stop = self._viewers == 0
        if should_stop:
            self._stop_locked()

    def _ensure_started(self) -> bool:
        with self._lock:
            if self._running:
                return True
            try:
                import websocket
                ws_url = _cdp_tab_ws_url()
                if not ws_url:
                    return False
                ws = websocket.create_connection(ws_url, timeout=5)
                ws.send(json.dumps({
                    "id": self._next_id(),
                    "method": "Page.startScreencast",
                    "params": {
                        "format": "jpeg", "quality": 60,
                        "maxWidth": 1280, "maxHeight": 720, "everyNthFrame": 1,
                    },
                }))
            except Exception:
                return False
            self._ws = ws
            self._running = True
        socketio.start_background_task(self._read_loop)
        return True

    def _stop_locked(self):
        with self._lock:
            if self._ws is not None:
                try:
                    self._ws.send(json.dumps({"id": self._next_id(), "method": "Page.stopScreencast"}))
                    self._ws.close()
                except Exception:
                    pass
            self._ws = None
            self._running = False

    def _read_loop(self):
        while True:
            with self._lock:
                ws = self._ws
                running = self._running
            if not running or ws is None:
                break
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") == "Page.screencastFrame":
                params = msg.get("params", {})
                data = params.get("data")
                session_id = params.get("sessionId")
                if data:
                    socketio.emit("tv_screencast_frame", {"jpeg": data})
                if session_id is not None:
                    try:
                        ws.send(json.dumps({
                            "id": self._next_id(),
                            "method": "Page.screencastFrameAck",
                            "params": {"sessionId": session_id},
                        }))
                    except Exception:
                        break
        with self._lock:
            self._running = False
            self._ws = None

    def send_input(self, method: str, params: dict) -> bool:
        with self._lock:
            ws = self._ws
        if ws is None:
            return False
        try:
            ws.send(json.dumps({"id": self._next_id(), "method": method, "params": params}))
            return True
        except Exception:
            return False


_screencast = ScreencastSession()


@app.route("/tv/screencast/start", methods=["POST"])
def screencast_start():
    """Сотрудник открыл дашборд — подключаемся (или переиспользуем уже идущий)
    к потоку экрана ТВ. Несколько одновременных зрителей — норма."""
    ok = _screencast.add_viewer()
    return {"ok": ok}, (200 if ok else 502)


@app.route("/tv/screencast/stop", methods=["POST"])
def screencast_stop():
    """Сотрудник закрыл вкладку/ушёл со страницы. Останавливается только
    когда ушли все зрители."""
    _screencast.remove_viewer()
    return {"ok": True}


@app.route("/tv/input/mouse", methods=["POST"])
def tv_input_mouse():
    """data: {type, x, y, button}. x/y — координаты в системе координат вкладки ТВ."""
    data = request.get_json(silent=True) or {}
    params = {
        "type": data.get("type", "mousePressed"),
        "x": data.get("x", 0),
        "y": data.get("y", 0),
        "button": data.get("button", "left"),
        "clickCount": 1,
    }
    ok = _screencast.send_input("Input.dispatchMouseEvent", params)
    return {"ok": ok}, (200 if ok else 502)


@app.route("/tv/input/key", methods=["POST"])
def tv_input_key():
    """data: {type, key, code, text}. Клавиатурный ввод сотрудника в вкладку ТВ."""
    data = request.get_json(silent=True) or {}
    params = {
        "type": data.get("type", "keyDown"),
        "key": data.get("key", ""),
        "code": data.get("code", ""),
        "text": data.get("text", ""),
    }
    ok = _screencast.send_input("Input.dispatchKeyEvent", params)
    return {"ok": ok}, (200 if ok else 502)


if __name__ == "__main__":
    SLIDESHOW_DIR.mkdir(exist_ok=True)
    socketio.start_background_task(sensor_loop)
    socketio.start_background_task(photoframe_loop)
    socketio.run(app, host="127.0.0.1", port=5000)

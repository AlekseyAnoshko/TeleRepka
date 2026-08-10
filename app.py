"""
Repka Pi Lab Hub — центральный веб-сервис лаборатории.

Функции:
  - показ данных с датчиков (температура, влажность, CO2) через GPIO/UART
  - отображение статуса подключённых фоторамок ESP32 PhotoFrame (REST API)
  - приём видео/аудио потоков с рабочих мест сотрудников (WebRTC) и показ на ТВ

Запуск: python3 app.py
Слушает 0.0.0.0:5000. Если в папке certs/ лежат cert.pem и key.pem —
поднимается по HTTPS (обязательно для getDisplayMedia/getUserMedia в браузере
сотрудника, см. README.md).

Примечание про шум в логах: в сетях с периодическими сканерами/ботами
(например, в сети университета) возможны единичные трассировки вида
"SSLError: [SSL: SSLV3_ALERT_CERTIFICATE_UNKNOWN]" — это безобидно, клиент
просто отклоняет самоподписанный сертификат без взаимодействия с
пользователем. Ниже такие трассировки приглушаются через
hub_exceptions(False), чтобы не засорять логи; реальные ошибки приложения
это не затрагивает.
"""
import eventlet
eventlet.monkey_patch()

import eventlet.debug
# Отключаем вывод в консоль трассировок для оборванных соединений в hub'е
# eventlet (включая типичные SSLError от сканеров сети, отклоняющих
# самоподписанный сертификат). Сам сервер при этом продолжает работать.
eventlet.debug.hub_exceptions(False)

import json
import os
from pathlib import Path

from flask import Flask, render_template, request
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


# --- HTTP routes -------------------------------------------------------

@app.route("/")
def index():
    """Главная страница — показывается на телевизоре лаборатории (kiosk-режим)."""
    return render_template("index.html")


@app.route("/broadcast")
def broadcast_page():
    """Страница сотрудника: трансляция экрана/камеры и звука на ТВ."""
    return render_template("broadcast.html")


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
    join_room("tv")


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


if __name__ == "__main__":
    socketio.start_background_task(sensor_loop)
    socketio.start_background_task(photoframe_loop)

    cert_path = BASE_DIR / "certs" / "cert.pem"
    key_path = BASE_DIR / "certs" / "key.pem"
    ssl_args = {}
    if cert_path.exists() and key_path.exists():
        ssl_args = {"certfile": str(cert_path), "keyfile": str(key_path)}
    else:
        print("ВНИМАНИЕ: certs/cert.pem и certs/key.pem не найдены — сервер запущен по HTTP. "
              "getDisplayMedia/getUserMedia в браузере сотрудника работать НЕ будет. "
              "См. README.md, раздел про self-signed сертификат.")

    socketio.run(app, host="0.0.0.0", port=5000, **ssl_args)

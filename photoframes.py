"""
Опрос статуса фоторамок ESP32 PhotoFrame через их REST API (см. docs/API.md
в репозитории esp32-photoframe: GET /api/config, GET /api/ota/status).

Добавлен reverse-proxy для веб-интерфейса рамки (см. proxy_request ниже):
сама рамка живёт по адресу 10.42.0.x — это внутренняя подсеть Wi-Fi
хотспота Repka Pi, недоступная снаружи (из сети университета, с обычного
Wi-Fi ноутбука и т.п. — туда просто нет маршрута). Раньше страница
telerepka-k207.istu.int/ давала прямую ссылку на http://10.42.0.101/, что
работало только для устройств, подключённых к самому хотспоту рамки.
Теперь вместо прямой ссылки используется /photoframe/<name>/ — этот путь
обрабатывает Flask (см. app.py), а сам запрос до рамки уходит с самой
Repka Pi, у которой есть маршрут в 10.42.0.0/24, и результат отдаётся
браузеру через уже работающий домен telerepka-k207.istu.int.
"""
from __future__ import annotations

import time

import requests

REQUEST_TIMEOUT_SEC = 2


def poll(name: str, ip: str) -> dict:
    base = f"http://{ip}"
    status = {
        "name": name,
        "ip": ip,
        "online": False,
        "config": None,
        "checked_at": time.time(),
    }
    try:
        resp = requests.get(f"{base}/api/config", timeout=REQUEST_TIMEOUT_SEC)
        resp.raise_for_status()
        status["online"] = True
        status["config"] = resp.json()
    except requests.RequestException:
        status["online"] = False
    return status


def find_ip_by_name(frames: list[dict], name: str) -> str | None:
    """Ищет IP рамки по имени в списке из config.json (см. app.py)."""
    for frame in frames:
        if frame["name"] == name:
            return frame["ip"]
    return None


def proxy_request(ip: str, subpath: str, method: str, headers: dict,
                   data: bytes, params: dict):
    """Пересылает HTTP-запрос от браузера сотрудника на веб-интерфейс
    рамки по её внутреннему IP (10.42.0.x) и возвращает ответ как есть.

    Вызывается из маршрута /photoframe/<name>/<path:subpath> в app.py —
    сам запрос "requests.request" выполняется с самой Repka Pi, у которой
    есть прямой доступ к подсети хотспота, поэтому браузеру never
    приходится обращаться к 10.42.0.x напрямую.
    """
    url = f"http://{ip}/{subpath}".rstrip("/")
    # Заголовок Host важен для рамки, если её веб-сервер проверяет Host;
    # но пересылать заголовки Connection/Host исходного запроса — плохая
    # идея при проксировании, поэтому передаём только безопасный минимум.
    forward_headers = {
        k: v for k, v in headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }
    return requests.request(
        method=method,
        url=url,
        headers=forward_headers,
        data=data,
        params=params,
        timeout=REQUEST_TIMEOUT_SEC * 3,
        allow_redirects=False,
    )

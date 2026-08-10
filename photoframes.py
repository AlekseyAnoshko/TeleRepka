"""
Опрос статуса фоторамок ESP32 PhotoFrame через их REST API (см. docs/API.md
в репозитории esp32-photoframe: GET /api/config, GET /api/ota/status).
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

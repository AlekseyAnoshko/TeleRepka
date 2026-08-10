"""
Slideshow-сервер для ESP32 PhotoFrame.

Прошивка рамки не умеет смотреть в сетевой каталог напрямую — она лишь
периодически скачивает один и тот же URL, указанный в image_url (см.
rotation_mode=url в docs/API.md репозитория esp32-photoframe). Этот сервер отдаёт
на каждый запрос к /slideshow.jpg следующее по очереди изображение из папки
SLIDESHOW_DIR, так что каждый очередной плановый опрос рамки сдвигает слайд
вперёд — получается настоящее слайд-шоу.

Частота смены кадров = частота опроса рамкой (cron-расписание в
прошивке, по умолчанию 1 час, настраивается через веб-интерфейс рамки
или PATCH /api/config).

Запуск: python3 slideshow_server.py
Слушает 0.0.0.0:5001 (отдельный порт от основного Lab Hub на 5000).
"""
from pathlib import Path
import itertools
import threading

from flask import Flask, send_file, abort

SLIDESHOW_DIR = Path(__file__).resolve().parent / "slideshow"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp"}

app = Flask(__name__)
_lock = threading.Lock()
_cycle = None
_files_snapshot = []


def _refresh_cycle():
    """Перечитывает папку и пересоздаёт бесконечный циклический итератор,
    если состав файлов в папке изменился (можно добавлять/удалять
    картинки без перезапуска сервера)."""
    global _cycle, _files_snapshot
    files = sorted(
        p for p in SLIDESHOW_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT
    )
    if files != _files_snapshot:
        _files_snapshot = files
        _cycle = itertools.cycle(files) if files else None


@app.route("/slideshow.jpg")
def slideshow():
    with _lock:
        _refresh_cycle()
        if _cycle is None:
            abort(404, description=f"Нет изображений в {SLIDESHOW_DIR}")
        next_file = next(_cycle)
    return send_file(next_file)


if __name__ == "__main__":
    SLIDESHOW_DIR.mkdir(exist_ok=True)
    app.run(host="0.0.0.0", port=5001)

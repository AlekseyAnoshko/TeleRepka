# Архитектура TeleRepka

Состояние описано по исходникам ветки `main` на 17.10.2026. Детали развёрнутой платы — в [CURRENT_STATE.md](CURRENT_STATE.md).

## Общая схема

```text
Браузер сотрудника ── HTTPS / WebSocket ── reverse proxy (внешняя настройка)
                                                    │
                                                    ▼
                                     Flask + Flask-SocketIO (app.py)
                                       │       │           │
                                       │       │           ├── HTTP API / TV control
                                       │       ├── SQLite employees + ISTU schedule
                                       └── WebRTC signaling / Socket.IO
                                                    │
                                         Chromium kiosk на ТВ
                                         localhost:5000/?kiosk=1
                                         CDP 127.0.0.1:9222

Repka Pi ── Wi-Fi hotspot 10.42.0.0/24 ── ESP32 PhotoFrame
         └─ GPIO / UART ── датчики
```

Сигнализация WebRTC проходит через сервер; медиапоток между отправителем и ТВ передаётся по WebRTC. Реальная маршрутизация зависит от сети и браузеров.

## Сервер и интерфейсы

- `app.py` — Flask, Flask-SocketIO, фоновые циклы датчиков/фоторамок, WebRTC-сигнализация, управление ТВ и CDP.
- `templates/index.html` — главная панель, киоск ТВ, трансляции, предпросмотр и удалённое управление, список сотрудников/расписания.
- `templates/broadcast.html` — отправка экрана либо камеры и звука с рабочего места.
- `static/` — клиентские ресурсы и видео.
- `config.json` — интервалы опроса и адреса фоторамок.

### Основные HTTP-маршруты

| Маршрут | Назначение |
|---|---|
| `/` | Панель управления и ТВ (`?kiosk=1`) |
| `/broadcast` | Страница отправителя WebRTC |
| `/api/employees` | JSON-список сотрудников |
| `/api/employees/<int:employee_id>/schedule` | Расписание сотрудника |
| `/photoframe/<name>/...` | Прокси к ESP32 в сети хотспота |
| `/slideshow.jpg` | Изображение для слайдшоу |
| `/tv/open-meetings`, `/tv/go-home`, `/tv/media` | Управление содержимым ТВ |
| `/tv/screencast/start`, `/tv/screencast/stop` | Управление предпросмотром ТВ |
| `/tv/input/mouse`, `/tv/input/key` | Удалённый ввод |

### Socket.IO

- ТВ подключается к комнате `tv` событием `tv-join`.
- WebRTC: `broadcaster-offer`, `tv-answer`, `ice-candidate`, `broadcaster-stop`.
- Расписания: `tv-show-schedules`, `tv-schedule-select`, `tv-schedule-back`.
- Состояние ТВ и предпросмотр: `tv-ready-check`, `tv-active-state-check`, `tv-screencast-start`, `tv-screencast-stop` и соответствующие серверные уведомления.
- В интерфейсе ТВ активная WebRTC-трансляция имеет приоритет перед расписанием.

## Расписания и SQLite

- `employees.py`: SQLite `data/laboratory_employees.db`; `init_db()` создаёт таблицу `employees` и выполняет предусмотренные миграции старой схемы.
- Основные поля таблицы: `id`, `name`, `position`, `istu_id`, `schedule_url`, `profile_url`, `active`, `last_seen_at`.
- `laboratory_sync.py`: извлекает список сотрудников с официальной страницы ИРНИТУ, сопоставляет записи по профилю или имени; режим просмотра по умолчанию, запись — `--apply`.
- `schedule_parser.py`: преобразует HTML университетского расписания в данные для API.
- Привязки к расписанию могут отсутствовать; синхронизация справочника не создаёт их автоматически.
- База является **локальными данными**, не включается в репозиторий; перенос/резервирование описаны в [deploy/README.md](../deploy/README.md).

## ТВ и киоск

- `lab-hub-kiosk.service` — процесс Flask.
- GDM-сессия `TeleRepka Kiosk` запускает Chromium отдельно; файлы настройки в `kiosk/`.
- ТВ открывает `http://localhost:5000/?kiosk=1`.
- CDP на `127.0.0.1:9222` используется для Screencast, навигации и мультимедийного управления.
- Управление вводом использует X11 и системные инструменты; графическая сессия отличается от SSH-сеанса.
- При трансляции интерфейс ТВ переключается в полноэкранный видеорежим, затем возвращается к панели.

## Датчики, фоторамки и сеть

- `sensors.py`: интерфейс показаний температуры/влажности и CO₂. DHT11 сейчас отключён в `read_dht11()`; реализован обмен с MH-Z19B по `/dev/ttyS2`. Реальная доступность зависит от подключённого оборудования.
- `photoframes.py`: опрос REST API фоторамок и проксирование их веб-интерфейса через Flask.
- `network/`: настройка NetworkManager hotspot `TeleRepka` (`wlan0`, `10.42.0.1/24`).
- `nginx_telerepka.conf`, `squid.conf`, `nftables-photoframe-proxy.conf` — конфигурации сетевого доступа; фактически применённые настройки нужно проверять на устройстве.

## Зависимости

`requirements.txt`: Flask, Flask-SocketIO, eventlet, requests, websocket-client, pyserial. `sqlite3` входит в стандартную библиотеку Python. Аппаратная библиотека `RepkaPi.GPIO` устанавливается отдельно при необходимости.

См. также [киоск](../kiosk/KIOSK.md), [установку](../deploy/README.md) и [правила разработки](DEVELOPMENT.md).

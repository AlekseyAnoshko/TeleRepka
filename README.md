# Repka Pi Lab Hub

Веб-сервис информационной панели лаборатории на **Repka Pi 4**. Показывается
на большом телевизоре по HDMI в режиме киоска и совмещает три функции:

1. **Датчики** — температура, влажность и уровень CO₂ с GPIO/UART в реальном времени.
2. **Фоторамки** — статус подключённых устройств ESP32 PhotoFrame (см. `docs/REPKA_PI_PHOTOFRAME.md`
   в репозитории esp32-photoframe) через их REST API.
3. **Трансляция с рабочих мест** — любой сотрудник открывает страницу `/broadcast`
   со своего компьютера и выводит экран или камеру+звук прямо на ТВ лаборатории
   через WebRTC, без установки дополнительного ПО.

Repka Pi при этом одновременно: подключена к сети университета по проводному
Ethernet (`end0`), раздаёт Wi-Fi как хотспот для фоторамок сотрудников, и
выводит картинку на ТВ по HDMI. Такая сетевая схема описана в
`docs/REPKA_PI_PHOTOFRAME.md` — сервис ниже рассчитан именно на неё.

## Архитектура

```
                    ┌─────────────── Repka Pi 4 ───────────────┐
  Ethernet (end0) → │  сеть университета                       │
  Wi-Fi hotspot   → │  ↔ фоторамки сотрудников (ESP32)         │
  HDMI            → │  ↔ телевизор лаборатории (Chromium kiosk)│
                    │                                           │
                    │  Flask + Flask-SocketIO (app.py)          │
                    │   ├─ sensors.py     — опрос GPIO/UART      │
                    │   ├─ photoframes.py — опрос REST API рамок │
                    │   └─ WebRTC signaling (offer/answer/ICE)  │
                    └───────────────────────────────────────┘
                                     ▲
                                     │ HTTPS + WebSocket
                       ┌──────────────────────────────┐
                       │  Рабочее место сотрудника   │
                       │  открывает /broadcast        │
                       │  (getDisplayMedia/getUserMedia)│
                       └─────────────────────────────┘
```

Видео/аудио с рабочего места идёт к ТВ напрямую по WebRTC (peer-to-peer),
Repka Pi выступает только сигнальным сервером — это разгружает и без того
слабый канал Wi-Fi/Ethernet платы от постоянной трансляции медиапотока.

## Почему обязателен HTTPS

Браузерные API `getDisplayMedia()` (захват экрана) и `getUserMedia()` (камера/
микрофон) работают только в защищённом контексте — то есть по HTTPS, кроме
адреса `localhost`. Поскольку сотрудники открывают страницу `/broadcast` по IP
Repka Pi в локальной сети, а не с `localhost`, без TLS браузер заблокирует
доступ к камере и экрану. Поэтому `app.py` поднимает сервер по HTTPS
с самоподписанным сертификатом (см. ниже).

## Установка

### 1. Системные зависимости

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv chromium-browser
```

Для реальных датчиков (замените под свою распиновку и модель CO₂-датчика):

```bash
sudo apt-get install -y python3-dev python3-setuptools git
git clone https://gitflic.ru/project/repka_pi/repkapigpiofs.git
cd repkapigpiofs && sudo python3 setup.py install && cd ..
```

### 2. Python-окружение

```bash
mkdir -p ~/lab-hub && cd ~/lab-hub
# скопируйте сюда все файлы из этого проекта: app.py, sensors.py, photoframes.py,
# config.json, requirements.txt, templates/, static/ (если добавите свои стили)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Без установленных `adafruit-circuitpython-dht`/`mh-z19` сервис автоматически
переходит в режим симуляции данных датчиков — удобно, чтобы сразу проверить
веб-интерфейс, не дожидаясь физического подключения оборудования.

### 3. Самоподписанный TLS-сертификат

```bash
mkdir -p ~/lab-hub/certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout ~/lab-hub/certs/key.pem \
  -out ~/lab-hub/certs/cert.pem \
  -days 3650 \
  -subj "/CN=repka-pi4.local"
```

При первом заходе на `/broadcast` браузер сотрудника покажет предупреждение
о недоверенном сертификате — это ожидаемо для внутреннего инструмента,
нужно один раз нажать «Дополнительно» → «Перейти на сайт».

### 4. Настройте `config.json`

Впишите реальные IP-адреса фоторамок в локальной Wi-Fi-сети Repka Pi
(диапазон вида `10.42.0.x`, см. `docs/REPKA_PI_PHOTOFRAME.md`):

```json
{
  "photoframes": [
    { "name": "Фоторамка Иванова", "ip": "10.42.0.101" },
    { "name": "Фоторамка Петрова", "ip": "10.42.0.102" }
  ]
}
```

### 5. Автозапуск сервера (systemd)

```bash
sudo cp lab-hub.service /etc/systemd/system/
sudo nano /etc/systemd/system/lab-hub.service   # поправьте пути и User, если нужно
sudo systemctl daemon-reload
sudo systemctl enable --now lab-hub.service
sudo systemctl status lab-hub.service
```

### 6. Автозапуск Chromium в режиме киоска на ТВ

Требуется графическая сессия автологина (например, LightDM/lightweight X11 с
автовходом пользователя `user`) — иначе `DISPLAY=:0` будет недоступен.

```bash
sudo cp lab-hub-kiosk.service /etc/systemd/system/
sudo nano /etc/systemd/system/lab-hub-kiosk.service   # поправьте пути и User
sudo systemctl daemon-reload
sudo systemctl enable --now lab-hub-kiosk.service
```

Проверьте, что автовход в графическую сессию включён:
```bash
sudo raspi-config   # или repka-config, если доступен: System Options → Boot / Auto Login
```

## Использование сотрудниками

Любой сотрудник в сети лаборатории открывает в браузере:

```
https://<IP Repka Pi>:5000/broadcast
```

Вводит своё имя, нажимает «Показать экран» или «Показать камеру + звук» —
трансляция сразу появляется плиткой на ТВ. Несколько сотрудников могут
транслировать одновременно, сетка на ТВ подстраивается автоматически.
Кнопка «Остановить» завершает трансляцию.

## Структура проекта

```
lab-hub/
├── app.py               # Flask + SocketIO сервер, WebRTC-сигнализация
├── sensors.py           # опрос датчиков (или симуляция, если не подключены)
├── photoframes.py       # опрос REST API фоторамок ESP32 PhotoFrame
├── config.json          # список фоторамок, интервалы опроса
├── requirements.txt
├── templates/
│   ├── index.html       # страница для ТВ (kiosk)
│   └── broadcast.html   # страница для рабочего места сотрудника
├── certs/               # самоподписанный TLS-сертификат (создать локально)
├── lab-hub.service          # systemd unit для сервера
└── lab-hub-kiosk.service    # systemd unit для Chromium kiosk
```

## Возможные доработки

- Подключить реальный датчик CO₂ (обычно UART, например MH-Z19) и DHT22/BME280
  на GPIO — точные пины зависят от распиновки в лаборатории.
- Добавить историю показаний датчиков (SQLite) и график за сутки.
- Добавить пароль/токен для страницы `/broadcast`, если лаборатория открыта
  для внешних посетителей.
- Настроить TURN-сервер, если у части рабочих мест окажется симметричный NAT
  и одного STUN (`stun.l.google.com`) не хватит для установления P2P-соединения.

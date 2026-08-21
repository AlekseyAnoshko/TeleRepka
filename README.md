# Repka Pi Lab Hub

Веб-сервис информационной панели лаборатории на **Repka Pi 4**. Показывается
на большом телевизоре по HDMI в режиме киоска и совмещает три функции:

1. **Датчики** — температура (DS18B20, 1-Wire), CO2 с UART в реальном времени.
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
                    ┌───────────── Repka Pi 4 ─────────────┐
  Ethernet (end0) → │  сеть университета                       │
  Wi-Fi hotspot   → │  ↔ фоторамки сотрудников (ESP32)         │
  HDMI            → │  ↔ телевизор лаборатории (Chromium kiosk)│
                    │                                           │
                    │  Flask + Flask-SocketIO (app.py)          │
                    │   ├─ sensors.py     — опрос 1-Wire/UART   │
                    │   ├─ photoframes.py — опрос REST API рамок │
                    │   └─ WebRTC signaling (offer/answer/ICE)  │
                    └──────────────────────────────────┘
                                     ▲
                                     │ HTTPS + WebSocket
                       ┌──────────────────────────┐
                       │  Рабочее место сотрудника   │
                       │  открывает /broadcast        │
                       │  (getDisplayMedia/getUserMedia)│
                       └──────────────────────────┘
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

Для датчика CO2 (опционально, UART, например MH-Z19):

```bash
pip3 install mh-z19
```

Для датчика температуры DS18B20 отдельная Python-библиотека GPIO не нужна —
см. отдельный раздел «Датчик температуры DS18B20 (1-Wire)» ниже, там нужна
только настройка на уровне ядра/device tree.

### 2. Python-окружение

```bash
mkdir -p ~/lab-hub && cd ~/lab-hub
# скопируйте сюда все файлы из этого проекта: app.py, sensors.py, photoframes.py,
# config.json, requirements.txt, templates/, static/ (если добавите свои стили)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Если 1-Wire на устройстве ещё не включён (нет `/sys/bus/w1/devices/`) —
`sensors.py` вернёт `temperature: None`, и веб-интерфейс корректно покажет
прочерк вместо значения, без падения сервиса.

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

## Датчик температуры DS18B20 (1-Wire)

`sensors.py` читает температуру напрямую из sysfs
(`/sys/bus/w1/devices/28-xxxxxxxxxxxx/w1_slave`) — библиотека `RepkaPi.GPIO`
для этого не используется, так как DS18B20 работает по протоколу 1-Wire,
который на уровне Linux обслуживают модули ядра `w1-gpio`/`w1-therm`, а не
пользовательский GPIO API.

### Важно для Repka Pi 4 (Allwinner H6): распиновка нестандартная

На платформе Repka-Pi4-Optimal физический **пин 7** 40-pin разъёма подключён
не к основному pinctrl-контроллеру (`pio`, база `300b000`, порты A–H), а к
**отдельному R_PIO-контроллеру** (`r_pio`, база `7022000`), который на SoC
Allwinner H6 управляет исключительно портом **L**. Внутри этого проекта пин 7
соответствует **PL10** (это подтверждено через
`/sys/kernel/debug/gpio`, где GPIO362 из диапазона `r_pio` GPIO 352-415
совпадает с тем же пином, что ранее конфигурировался через `RepkaPi.GPIO`
как «пин 7, BOARD-нумерация»).

Из-за этого готовые примеры оверлеев 1-Wire для Repka Pi 3 (SoC Allwinner H5,
`compatible = "allwinner,h5"`, ссылка на `&pio`) **не работают** на Repka Pi 4
без изменений — они вызывают ошибку ядра:
```
w1-gpio onewire: there is not valid maps for state default
w1-gpio onewire: error -EINVAL: gpio_request (pin) failed
```

### Рабочий device tree overlay для Repka Pi 4

Создайте `/root/onewire.dts`:

```dts
/dts-v1/;
/plugin/;
/ {
    fragment@0 {
        target-path = "/";
        __overlay__ {
            onewire {
                compatible = "w1-gpio";
                gpios = <&r_pio 0 10 0>;
                status = "okay";
            };
        };
    };
};
```

Ключевые моменты:
- используется `&r_pio` (контроллер `7022000.pinctrl`), а не `&pio`;
- банк-индекс всегда `0`, так как `r_pio` управляет только одним портом (L);
- отдельный pinctrl-фрагмент с `pinctrl-names`/`pinctrl-0` не нужен — драйвер
  `w1-gpio` сам запрашивает GPIO напрямую и программно переключает
  направление пина, поэтому лишний pinctrl state только ломает регистрацию.

Компиляция и подключение оверлея:

```bash
sudo apt-get install -y device-tree-compiler
sudo dtc -I dts -O dtb -o /root/onewire.dtbo /root/onewire.dts
sudo cp /root/onewire.dtbo /boot/overlays/
sudo nano /boot/repkaEnv.txt   # overlays=onewire
sudo reboot
```

Проверка после перезагрузки:

```bash
dmesg | grep -i w1
ls /sys/bus/w1/devices/     # должна появиться папка 28-xxxxxxxxxxxx
cat /sys/bus/w1/devices/28-*/w1_slave
```

Вторая строка `w1_slave` должна оканчиваться на `t=NNNNN` (температура в
тысячных долях градуса), а первая — на `YES` (контрольная сумма CRC сошлась).

### Обязательный подтягивающий резистор

DS18B20 требует резистор **4.7 кОм** между линией данных (DQ, физический
пин 7) и питанием (VCC, 3.3V). Без него шина 1-Wire поднимается и даже
находит «устройство», но это шум, а не реальный датчик — характерный
признак в `dmesg`:

```
w1_master_driver w1_bus_master1: Attaching one wire slave 00.800000000000 crc 8c
w1_master_driver w1_bus_master1: Family 0 for 00.800000000000.8c is not registered.
```

Family **0** вместо ожидаемого Family **28**, и разный случайный адрес при
каждом повторном поиске — верный признак плавающей линии без подтяжки.
"Утолщение" или термоусадка на кабеле датчика резистором не являются —
резистор нужно ставить отдельно между DQ и VCC, если это не готовый модуль
с резистором, впаянным на плату переходника.

### `repka-config` на чистом Debian не работает

Официальная утилита `repka-config` (в норме позволяет включить W1-GPIO через
`I1 Pinout Options → вариант 11` без ручного overlay) рассчитана на полный
образ RepkaOS и падает на чистом Debian с ошибкой вида
`advanced_options: команда не найдена`, так как зависит от отсутствующей в
Debian вспомогательной инфраструктуры RepkaOS. На данной установке
(Debian 12 bookworm поверх Repka Pi 4) 1-Wire включается только вручную,
через overlay выше.

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
├── sensors.py           # чтение DS18B20 (1-Wire) и CO2 (UART, опционально)
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

- Добавить резистор 4.7 кОм на линию DS18B20 и завершить проверку реальных
  показаний температуры (на момент последнего обновления README — GPIO и
  device tree overlay настроены и работают, ожидается физическое подключение
  резистора).
- Подключить реальный датчик CO2 (обычно UART, например MH-Z19) — точные
  параметры порта зависят от распиновки в лаборатории.
- Добавить датчик влажности (сейчас `sensors.py` всегда отдаёт
  `humidity: None`, так как DS18B20 меряет только температуру).
- Добавить историю показаний датчиков (SQLite) и график за сутки.
- Добавить пароль/токен для страницы `/broadcast`, если лаборатория открыта
  для внешних посетителей.
- Настроить TURN-сервер, если у части рабочих мест окажется симметричный NAT
  и одного STUN (`stun.l.google.com`) не хватит для установления P2P-соединения.

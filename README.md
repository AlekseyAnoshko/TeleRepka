# Repka Pi Lab Hub

Веб-сервис информационной панели лаборатории на **Repka Pi 4**. Показывается
на большом телевизоре по HDMI в режиме киоска и совмещает четыре функции:

1. **Датчики** — температура, влажность и уровень CO₂ с GPIO/UART в реальном времени.
2. **Фоторамки** — статус подключённых устройств ESP32 PhotoFrame через их REST API,
   плюс reverse-proxy на их веб-интерфейс (см. раздел «Сеть фоторамок» ниже).
3. **Трансляция с рабочих мест** — любой сотрудник открывает страницу `/broadcast`
   со своего компьютера и выводит экран или камеру+звук прямо на ТВ лаборатории
   через WebRTC, без установки дополнительного ПО.
4. **Transparent proxy для рамок** (`squid.conf` + `nftables-photoframe-proxy.conf`) —
   даёт фоторамкам, у которых нет настройки HTTP-прокси в прошивке, доступ
   в интернет для OTA-обновлений через прокси-сервер университета. **Проверено
   и работает**: в `access.log` Squid видны успешные `TCP_TUNNEL/200` запросы рамки
   к `api.github.com` через `FIRSTUP_PARENT/172.27.100.5`.

Repka Pi при этом одновременно: подключена к сети университета по проводному
Ethernet (`end0`), раздаёт Wi-Fi как хотспот для фоторамок сотрудников (подсеть
`10.42.0.0/24`), и выводит картинку на ТВ по HDMI.

## Единый домен вместо портов

Весь сайт открывается по одному внутреннему имени —
**`telerepka-k207.istu.int`** — вместо отдельных портов на каждый сервис.
`app.py` слушает только `127.0.0.1:5000`, а `nginx` (см. `nginx_telerepka.conf`)
терминирует TLS и раздаёт домен на портах 80/443:

| Страница | Назначение |
|---|---|
| `/` | Главная панель для ТВ (kiosk): датчики, статус рамок |
| `/broadcast` | Трансляция экрана/камеры сотрудника на ТВ (WebRTC) |
| `/slideshow.jpg` | Слайд для ESP32 PhotoFrame (только HTTP — прошивка рамки не умеет обрабатывать HTTPS-редиректы с самоподписанным сертификатом) |
| `/photoframe/<name>/` | Reverse-proxy на веб-интерфейс конкретной рамки |

Резолвинг `telerepka-k207.istu.int`:
- на самой Repka Pi — строка в `/etc/hosts`;
- для устройств в хотспоте (`10.42.0.0/24`) — запись в
  `/etc/NetworkManager/dnsmasq-shared.d/`.

## Сеть фоторамок: два независимых потока трафика

Важно не путать два разных направления трафика к рамке — у них разные решения.

### 1. Входящий: браузер сотрудника → веб-интерфейс рамки

Сама рамка живёт в подсети хотспота (`10.42.0.x`), которая недоступна снаружи —
у обычного ноутбука или устройства из сети университета просто нет туда
маршрута. Поэтому ссылка на карточке рамки на главной странице ведёт не на
`http://10.42.0.101/` напрямую, а на `/photoframe/<name>/` — этот маршрут в
`app.py` сам делает запрос к рамке (Repka Pi физически имеет доступ в
`10.42.0.0/24`, так как она и есть шлюз хотспота) и отдаёт результат браузеру
через уже работающий домен сайта. Логика проксирования — в `photoframes.py`
(`find_ip_by_name()`, `proxy_request()`).

### 2. Исходящий: рамка → интернет (OTA-обновления прошивки)

Сеть университета блокирует прямые исходящие TCP/80/443-соединения и
пропускает трафик наружу только через явный HTTP-прокси университета
(`172.27.100.5:4444`). Прошивка ESP32 PhotoFrame не умеет настраиваться на
HTTP-прокси вообще — она всегда пытается соединиться напрямую. Значит обойти
это на стороне рамки невозможно, и трафик нужно перехватывать прозрачно на
уровне сети, силами самой Repka Pi.

Решение — **transparent proxy** на связке Squid + nftables, подтверждённое
работающим на практике:

- `squid.conf` — конфиг Squid: `http_port 3129 intercept` и
  `https_port 3130 intercept ssl-bump ...`. Squid слушает в режиме
  перехвата, клиент (рамка) не знает о его существовании. Нужен
  также обычный `http_port 3128` (без intercept) — без него Squid валится
  с `FATAL: mimeLoadIcon: cannot parse internal URL` при генерации служебных
  внутренних URL.
- Для HTTPS используется `ssl_bump peek/splice`, а не полноценный MITM —
  Squid лишь подсматривает SNI из TLS-хендшейка (какой домен запрашивается),
  но не расшифровывает и не подменяет сертификат. Это принципиально,
  потому что прошить кастомный CA-сертификат в ESP32 нельзя. `generate-host-
  certificates=on` требует обязательно инициализированной базы
  `sslcrtd_program`/`ssl_db` (см. установку ниже) — без неё Squid падает с
  `FATAL: The sslcrtd_program helpers are crashing too rapidly`.
- `cache_peer 172.27.100.5 parent 4444 0 no-query default` — весь трафик,
  прошедший через Squid, дальше уходит на прокси университета как на
  «родителя последней инстанции».
- `nftables-photoframe-proxy.conf` — `redirect` для пакетов от IP рамки на
  порты `3129`/`3130` — без этого правила Squid просто ничего не увидит.

**Важно:** правила nftables, добавленные вручную (`nft add ...`), живут только
в памяти и пропадают при перезагрузке Repka Pi. Файл `nftables-photoframe-proxy.conf`
нужно подключить через `/etc/nftables.conf` (см. установку ниже), иначе
после каждой перезагрузки transparent proxy придется настраивать снова.

### 3. Время (NTP) — отдельная, но связанная проблема

NTP работает по UDP/123, для которого нет понятия «HTTP-прокси» — сеть
университета блокирует его так же, как и прямой TCP, но проксировать через
Squid не получится (Squid не умеет проксировать произвольный UDP). Из-за
этого у рамки без верного времени TLS-проверка сертификата при OTA-запросе
может проваливаться («certificate not yet valid», раз дата упала к 1970 году).

Решение — `chrony` на самой Repka Pi, синхронизирующийся с внутрикампусным
NTP-сервером университета (`172.27.100.5`, отзывается на UDP/123 в отличие от
внешних `pool.ntp.org` — трафик внутри кампуса не блокируется, в отличие от
исходящего в интернет; подтверждено ответом через `chronyc sources -v`, статус
`*`, имя `free.istu`), и рамка настраивается брать время не с
`pool.ntp.org`, а с самой Repka Pi (`10.42.0.1`) как с локального NTP-сервера
(`PATCH /api/config`, поле `ntp_server`):

```bash
sudo apt-get install -y chrony
# в /etc/chrony/chrony.conf:
#   server 172.27.100.5 iburst
#   allow 10.42.0.0/24
sudo systemctl restart chrony
chronyc sources -v   # ожидаем '*' у 172.27.100.5 (free.istu)
```

## Почему обязателен HTTPS для `/broadcast`

Браузерные API `getDisplayMedia()` (захват экрана) и `getUserMedia()` (камера/
микрофон) работают только в защищённом контексте — то есть по HTTPS, кроме
адреса `localhost`. Поскольку сотрудники открывают `/broadcast` по домену сайта
в локальной сети, а не с `localhost`, без TLS браузер заблокирует доступ к
камере и экрану. TLS терминируется в `nginx` (см. `nginx_telerepka.conf`) с
самоподписанным сертификатом — при первом заходе браузер один раз спросит
подтверждение, это ожидаемо для внутреннего инструмента.

## Установка

### 1. Системные зависимости

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv chromium-browser nginx
```

Для transparent proxy рамок:
```bash
sudo apt-get install -y squid-openssl   # именно openssl-вариант, нужен для ssl_bump
```

Для реальных датчиков (замените под свою распиновку и модель CO₂-датчика):
```bash
sudo apt-get install -y python3-dev python3-setuptools git
git clone https://gitflic.ru/project/repka_pi/repkapigpiofs.git
cd repkapigpiofs && sudo python3 setup.py install && cd ..
```

### 2. Python-окружение

```bash
mkdir -p ~/git && cd ~/git
git clone https://github.com/AlekseyAnoshko/TeleRepka.git
cd TeleRepka
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Без установленных `adafruit-circuitpython-dht`/`mh-z19` сервис автоматически
переходит в режим симуляции данных датчиков.

### 3. nginx и TLS-сертификат сайта

```bash
sudo mkdir -p /etc/nginx/certs
sudo openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout /etc/nginx/certs/telerepka.key \
  -out /etc/nginx/certs/telerepka.crt \
  -days 3650 -subj "/CN=telerepka-k207.istu.int"

sudo cp nginx_telerepka.conf /etc/nginx/sites-available/telerepka
sudo ln -sf /etc/nginx/sites-available/telerepka /etc/nginx/sites-enabled/telerepka
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Резолвинг домена на самой Repka Pi:
```bash
echo "127.0.0.1  telerepka-k207.istu.int" | sudo tee -a /etc/hosts
```

### 4. Настройте `config.json`

Впишите реальные IP-адреса фоторамок в локальной Wi-Fi-сети Repka Pi:

```json
{
  "photoframes": [
    { "name": "Фоторамка Иванова", "ip": "10.42.0.101" },
    { "name": "Фоторамка Петрова", "ip": "10.42.0.102" }
  ]
}
```

### 5. Transparent proxy для OTA-обновлений рамок

```bash
sudo cp squid.conf /etc/squid/squid.conf
sudo mkdir -p /etc/squid/certs
sudo openssl req -new -newkey rsa:2048 -sha256 -days 3650 -nodes -x509 \
  -keyout /etc/squid/certs/dummy.pem -out /etc/squid/certs/dummy.pem \
  -subj "/CN=repka-transparent-proxy"
sudo chown -R proxy:proxy /etc/squid/certs

# База сертификатов для ssl-bump generate-host-certificates — важно НЕ
# создавать /var/spool/squid/ssl_db заранее через mkdir — утилита
# создаёт её сама от нужного владельца:
sudo -u proxy /usr/lib/squid/security_file_certgen -c -s /var/spool/squid/ssl_db -M 4MB

sudo systemctl restart squid
sudo systemctl status squid   # ожидаем active (running), без FATAL в логе

sudo ss -tlnp | grep squid    # ожидаем 3128, 3129, 3130
```

Активация nftables-редиректа — через постоянный конфиг, чтобы правила не
пропадали при перезагрузке (см. `nftables-photoframe-proxy.conf`):
```bash
sudo cp nftables-photoframe-proxy.conf /etc/nftables-photoframe-proxy.conf
sudo nft -f /etc/nftables-photoframe-proxy.conf
echo 'include "/etc/nftables-photoframe-proxy.conf"' | sudo tee -a /etc/nftables.conf
sudo systemctl enable nftables

sudo nft list table inet nat   # проверка
```

Проверка сквозного прохождения:
```bash
sudo tail -f /var/log/squid/access.log
```
При пробуждении рамки в логе должна появиться строка вида
`TCP_TUNNEL/200 ... CONNECT api.github.com:443 - FIRSTUP_PARENT/172.27.100.5` —
это подтверждает, что OTA-трафик рамки успешно ушёл через transparent
proxy и дальше в интернет.

### 6. Chrony (время для рамок)

```bash
sudo apt-get install -y chrony
# см. раздел "Время (NTP)" выше
```

### 7. Автозапуск сервера (systemd)

```bash
sudo cp lab-hub.service /etc/systemd/system/
sudo nano /etc/systemd/system/lab-hub.service   # поправьте пути на ~/git/TeleRepka и User
sudo systemctl daemon-reload
sudo systemctl enable --now lab-hub.service
sudo systemctl status lab-hub.service
```

### 8. Автозапуск Chromium в режиме киоска на ТВ

Требуется графическая сессия автологина (LightDM/lightweight X11 с автовходом
пользователя `user`) — иначе `DISPLAY=:0` будет недоступен.

```bash
sudo cp lab-hub-kiosk.service /etc/systemd/system/
sudo nano /etc/systemd/system/lab-hub-kiosk.service   # поправьте URL на https://telerepka-k207.istu.int/
sudo systemctl daemon-reload
sudo systemctl enable --now lab-hub-kiosk.service
```

## Использование сотрудниками

Любой сотрудник в сети лаборатории открывает в браузере:

```
https://telerepka-k207.istu.int/broadcast
```

Вводит своё имя, нажимает «Показать экран» или «Показать камеру + звук» —
трансляция сразу появляется плиткой на ТВ. Несколько сотрудников могут
транслировать одновременно, сетка на ТВ подстраивается автоматически.

## Структура проекта

```
TeleRepka/
├── app.py                        # Flask + SocketIO сервер, WebRTC-сигнализация,
│                                #   слайд-шоу, reverse-proxy на рамки
├── sensors.py                     # опрос датчиков (или симуляция, если не подключены)
├── photoframes.py                  # опрос REST API рамок + reverse-proxy до них
├── config.json                     # список фоторамок, интервалы опроса
├── requirements.txt
├── nginx_telerepka.conf             # единая точка входа на 80/443, домен сайта
├── squid.conf                       # transparent proxy для OTA-обновлений рамок
├── nftables-photoframe-proxy.conf    # постоянные правила redirect для squid.conf
├── templates/
│   ├── index.html                    # страница для ТВ (kiosk)
│   └── broadcast.html                 # страница для рабочего места сотрудника
├── lab-hub.service                     # systemd unit для сервера
└── lab-hub-kiosk.service                # systemd unit для Chromium kiosk
```

## Возможные доработки

- Подключить реальный датчик CO₂ (обычно UART, например MH-Z19) и DHT22/BME280
  на GPIO — точные пины зависят от распиновки в лаборатории.
- Добавить историю показаний датчиков (SQLite) и график за сутки.
- Добавить пароль/токен для страницы `/broadcast`, если лаборатория открыта
  для внешних посетителей.
- Настроить TURN-сервер, если у части рабочих мест окажется симметричный NAT
  и одного STUN (`stun.l.google.com`) не хватит для установления P2P-соединения.
- Проверить не только метаданные OTA-запросы (`api.github.com`), но и самую
  загрузку бинарника прошивки (обычно `objects.githubusercontent.com` или
  `github.com/.../releases/download/...`) — убедиться, что и она успешно
  прошла через transparent proxy, а не только запросы к API.

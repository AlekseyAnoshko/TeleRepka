# Киоск TeleRepka: дашборд на ТВ сразу после включения

Проверено на Repka Pi 4 (Repka OS, Ubuntu 22.04.5, ядро 6.12.9.7-repka-pi4, aarch64), менеджер входа GDM.

## Как это устроено

- `lab-hub-kiosk.service` запускает `app.py` (Flask, `127.0.0.1:5000`). Браузер он не запускает.
- GDM автоматически входит пользователем киоска (по умолчанию `aaf`) в сеанс `TeleRepka Kiosk` (`/usr/share/xsessions/telerepka.desktop`).
- Сеанс выполняет `/usr/local/lib/telerepka/kiosk-session`: ждёт готовности дашборда, включает предпочтительный режим ТВ через `xrandr`, читает текущее разрешение и запускает Chromium с `--kiosk` и `--window-size` под это разрешение. Если браузер завершился, он перезапускается через 3 секунды и снова читает разрешение.
- Chromium открывает `http://localhost:5000/?kiosk=1`. CDP слушает `127.0.0.1:9222`: через него `app.py` управляет ТВ (кнопка «Вернуть дашборд», медиаклавиши, трансляция экрана). Порт 9222 нельзя открывать наружу.
- XFCE остаётся установленным и доступен как запасной сеанс на экране входа.

## Требования

- Система на eMMC. На SD-карте Snap-Chromium запускается очень медленно.
- Chromium из Snap: `sudo snap install chromium`. Пакет `chromium-browser` в APT для Ubuntu 22.04 — переходная заглушка, которая ставит тот же Snap; пакета `chromium` в источниках нет.
- `xrandr` и `xset` (пакет `x11-xserver-utils`) и `curl`.
- Запущенный `lab-hub-kiosk.service`.

## Установка

```
sudo ./kiosk/setup-kiosk-session.sh --dry-run   # показать действия
sudo ./kiosk/setup-kiosk-session.sh             # применить
sudo reboot
```

Скрипт копирует сеанс, делает резервные копии `/etc/gdm3/custom.conf.before-telerepka` и `/var/lib/AccountsService/users/<user>.before-telerepka`, включает автологин и назначает пользователю сеанс `telerepka`.

## Проверка после перезагрузки

```
loginctl list-sessions
grep 'resolution=' ~/telerepka-session.log | tail -3
curl -s http://127.0.0.1:9222/json/list | head -c 300
curl -s -X POST http://localhost:5000/tv/go-home
```

Ожидается: у пользователя есть графический сеанс, в журнале указано разрешение ТВ, CDP отдаёт вкладку дашборда, `go-home` возвращает `{"ok":true}`.

## Откат

```
sudo cp -a /etc/gdm3/custom.conf.before-telerepka /etc/gdm3/custom.conf
sudo cp -a /var/lib/AccountsService/users/aaf.before-telerepka /var/lib/AccountsService/users/aaf
sudo reboot
```

Если файла AccountsService раньше не было и его создал скрипт, удалите из него строки `Session=` и `XSession=`.

## Версия Chromium и GPU

- На Mali-T720 (Allwinner H6) драйвер Panfrost даёт только OpenGL ES 2.0, а Chromium 153 требует GLES3: GPU-процесс завершается при старте, браузер рисует на CPU.
- Chromium 139 (Snap, ревизия 3236 для arm64) GPU-процесс не роняет, но графика всё равно идёт через SwiftShader (`SystemInfo.getInfo`: `gpu_compositing: disabled_software`). Выигрыша от закрепления версии нет; 139 оставлена как проверенная рабочая.
- Закрепить версию: `sudo snap refresh chromium --revision=3236` и `sudo snap refresh --hold=forever chromium`. Вернуть актуальную: `sudo snap refresh --unhold chromium` и `sudo snap refresh chromium --channel=latest/stable`. Перед понижением версии удалите профиль `~/snap/chromium/common/telerepka-profile`.
- Chromium от 136 версии требует отдельный `--user-data-dir` для `--remote-debugging-port`; от 111 версии нужен `--remote-allow-origins`.
- Основная нагрузка на CPU — зацикленный ролик `static/video_lns.mp4`: с ним процессы chrome занимают около 166% CPU, на паузе около 10%. Для заставки лучше использовать уменьшенное видео (например, 960 px по ширине, 15 к/с, H.264 baseline).

## Диагностика

```
tail -50 ~/telerepka-session.log
curl -s http://127.0.0.1:9222/json/list | head -c 300
journalctl -b -u gdm --no-pager | tail -40
```

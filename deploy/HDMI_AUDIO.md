# Постоянный HDMI-звук TeleRepka

На основной Repka Pi 4 после аварийного отключения питания PulseAudio иногда выбирал не тот выход. Рабочее устройство: `alsa_output.platform-sound_hdmi.stereo-fallback`.

## Установка на новой плате

При работающем PulseAudio, из корня репозитория:

```bash
sudo bash deploy/setup-hdmi-audio.sh --user aaf
```

Замените `aaf` на имя пользователя графического киоска. Скрипт устанавливает `~/.local/bin/telerepka-hdmi-audio` и `~/.config/systemd/user/telerepka-hdmi-audio.service`, включает запуск через `default.target.wants`. Он не перезапускает GDM, Chromium, аудиосервер и компьютер. При повторном запуске сохраняет резервные копии существующих файлов.

В уже запущенной пользовательской сессии выполните **без sudo**:

```bash
systemctl --user daemon-reload
systemctl --user start telerepka-hdmi-audio.service
pactl get-default-sink
pactl get-sink-volume alsa_output.platform-sound_hdmi.stereo-fallback
systemctl --user is-enabled telerepka-hdmi-audio.service
```

Ожидается HDMI-выход, громкость 80% и `enabled`. Для `Type=oneshot` состояние `inactive (dead)` после `status=0/SUCCESS` нормально. Скрипт ждёт появления устройства до 60 секунд и переносит уже существующие аудиопотоки.

## Проверка после отключения питания / перезагрузки

После входа в графическую сессию:

```bash
systemctl --user status telerepka-hdmi-audio.service --no-pager
pactl get-default-sink
pactl list short sinks
```

Проверьте звук на самом ТВ. Проверка 31.10.2026 на основной плате: `status=0/SUCCESS`, HDMI выбран, громкость 80%, автозапуск `enabled`. Поведение после последующей перезагрузки ещё требует подтверждения.

**Ограничение:** служба запускается при старте пользовательского systemd. Если аудиосервер перезапустится позднее, однократная служба не применит настройки повторно. При отсутствии звука проверьте `journalctl --user -u telerepka-hdmi-audio.service -b` и наличие нужного sink.

## Откат

От пользователя киоска:

```bash
systemctl --user disable --now telerepka-hdmi-audio.service
rm -f ~/.config/systemd/user/telerepka-hdmi-audio.service
rm -f ~/.local/bin/telerepka-hdmi-audio
systemctl --user daemon-reload
```

Если до установки были файлы с теми же именами, сначала восстановите их из `*.before-telerepka-YYYYMMDD-HHMMSS`.

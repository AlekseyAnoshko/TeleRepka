# Wi-Fi hotspot

Repka Pi создаёт точку доступа:

- SSID: `TeleRepka`
- Интерфейс: `wlan0`
- Адрес роутера: `10.42.0.1/24`
- Режим: NetworkManager `ipv4.method shared`
- DHCP/DNS/NAT: управляются NetworkManager

Восстановление:

```bash
sudo env HOTSPOT_PASSWORD='ЗАДАТЬ-ПАРОЛЬ' \
  ./network/setup-hotspot.sh
```

Настоящий пароль Wi-Fi в Git не хранится.

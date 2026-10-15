#!/usr/bin/env bash
set -euo pipefail

CONNECTION="${HOTSPOT_CONNECTION:-TeleRepka-Hotspot}"
SSID="${HOTSPOT_SSID:-TeleRepka}"
ADDRESS="${HOTSPOT_ADDRESS:-10.42.0.1/24}"
PASSWORD="${HOTSPOT_PASSWORD:?Set HOTSPOT_PASSWORD}"

if nmcli -t -f NAME connection show | grep -Fxq "$CONNECTION"; then
    nmcli connection delete "$CONNECTION"
fi

nmcli connection add \
    type wifi \
    ifname wlan0 \
    con-name "$CONNECTION" \
    ssid "$SSID"

nmcli connection modify "$CONNECTION" \
    connection.autoconnect yes \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel 1 \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "$PASSWORD" \
    ipv4.method shared \
    ipv4.addresses "$ADDRESS" \
    ipv6.method disabled

nmcli connection up "$CONNECTION"

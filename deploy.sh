#!/bin/bash
set -e
cd ~/git/TeleRepka
git pull --rebase origin main
sudo systemctl restart lab-hub.service
sudo systemctl restart lab-hub-kiosk.service
echo "Деплой завершён: $(git log -1 --oneline)"

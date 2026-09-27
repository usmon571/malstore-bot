#!/usr/bin/env bash
# MalStore botni Ubuntu serverga o'rnatish (Oracle Cloud ARM yoki oddiy VPS).
# Serverda ishga tushiring:   bash setup.sh
set -euo pipefail

APP_DIR="$HOME/malstore-bot"
USER_NAME="$(whoami)"

echo "==> Sana/vaqt mintaqasi (reys sanalari to'g'ri hisoblanishi uchun)"
sudo timedatectl set-timezone Asia/Tashkent

echo "==> Tizim paketlari"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

echo "==> Virtual muhit"
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "==> Chromium va uning tizim kutubxonalari"
# --with-deps kerakli apt paketlarni ham o'rnatadi (ARM'da ham ishlaydi)
./venv/bin/python -m playwright install --with-deps chromium

echo "==> Tekshiruv"
./venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    b.new_page().goto("about:blank")
    b.close()
print("Chromium ishladi")
PY

echo "==> Avtomatik ishga tushirish xizmati"
sudo tee /etc/systemd/system/malstore-bot.service >/dev/null <<EOF
[Unit]
Description=MalStore Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/bot.py
# Yiqilsa o'zi qayta ko'tariladi
Restart=always
RestartSec=10
StandardOutput=append:$APP_DIR/bot.log
StandardError=append:$APP_DIR/bot.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now malstore-bot

echo
echo "TAYYOR. Bot ishlayapti va server qayta yuklansa ham o'zi ko'tariladi."
echo "  Holati:   sudo systemctl status malstore-bot"
echo "  Loglar:   tail -f $APP_DIR/bot.log"
echo "  Qayta:    sudo systemctl restart malstore-bot"

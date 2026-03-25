#!/bin/bash

echo "========================================"
echo "  Tempest Inky Dashboard Setup          "
echo "========================================"

# 1. Install system dependencies (bypasses heavy numpy compilation)
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y git python3-venv python3-numpy python3-pil python3-requests

# 2. Enable Hardware Interfaces (SPI and I2C)
echo "Enabling SPI and I2C..."
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# 3. Apply Bookworm SPI Chip Select Fix
CONFIG_FILE="/boot/firmware/config.txt"
if ! grep -q "dtoverlay=spi0-0cs" "$CONFIG_FILE"; then
    echo "Applying Bookworm SPI fix to config.txt..."
    sudo sed -i '/dtparam=spi=on/a dtoverlay=spi0-0cs' "$CONFIG_FILE"
fi

# 4. Set up Virtual Environment
echo "Setting up Python virtual environment..."
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install inky

# 5. Configure Secrets
if [ ! -f secrets.py ]; then
    echo ""
    echo "--- Configuration ---"
    read -p "Enter your Tempest Station ID: " station_id
    read -p "Enter your Tempest API Token: " api_token
    
    cat <<EOF > secrets.py
STATION_ID = "$station_id"
TOKEN = "$api_token"
EOF
    echo "secrets.py created successfully."
else
    echo "secrets.py already exists, skipping configuration."
fi

# 6. Set up the 15-minute Cron Job
echo "Setting up automatic refresh (every 15 minutes)..."
CRON_CMD="*/15 * * * * cd $(pwd) && $(pwd)/venv/bin/python3 main.py > /tmp/tempest-inky.log 2>&1"
# This safely adds the new cron job without deleting any existing ones
(crontab -l 2>/dev/null | grep -v "tempest-inky"; echo "$CRON_CMD") | crontab -

echo "========================================"
echo " Setup Complete! "
echo " Please REBOOT your Raspberry Pi now to apply the hardware changes."
echo " Command: sudo reboot"
echo "========================================"

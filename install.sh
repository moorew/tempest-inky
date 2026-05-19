#!/usr/bin/env bash
# Tempest Inky Dashboard — Raspberry Pi installer
# Run from inside the tempest-inky directory: bash install.sh

set -euo pipefail

# Resolve the directory containing this script regardless of where it is called from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine the real user even when called with sudo
APP_USER="${SUDO_USER:-$USER}"
APP_HOME=$(eval echo "~$APP_USER")

echo "========================================"
echo "  Tempest Inky Dashboard Setup          "
echo "========================================"
echo ""
echo "  Install directory : $SCRIPT_DIR"
echo "  User account      : $APP_USER"
echo ""

# ── Sanity checks ─────────────────────────────────────────────────────────────

if ! command -v apt-get &>/dev/null; then
    echo "ERROR: apt-get not found. This installer requires Raspberry Pi OS (Debian-based)."
    exit 1
fi

if [[ "$EUID" -eq 0 && -z "${SUDO_USER:-}" ]]; then
    echo "ERROR: Do not run as root directly. Use: bash install.sh"
    exit 1
fi

# ── Step 1: System packages ───────────────────────────────────────────────────

echo "[1/7] Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y \
    git \
    python3 \
    python3-venv \
    python3-pip \
    python3-numpy \
    python3-pil \
    python3-requests \
    libopenblas0

echo "  Done."

# ── Step 2: Enable SPI and I2C ────────────────────────────────────────────────

echo "[2/7] Enabling SPI and I2C interfaces..."
if command -v raspi-config &>/dev/null; then
    sudo raspi-config nonint do_spi 0
    sudo raspi-config nonint do_i2c 0
    echo "  SPI and I2C enabled."
else
    echo "  WARNING: raspi-config not found. Enable SPI and I2C manually via /boot/firmware/config.txt"
fi

# ── Step 3: Hardware group membership ────────────────────────────────────────

echo "[3/7] Checking hardware group membership for $APP_USER..."
NEEDS_GROUP_REBOOT=false
for grp in spi i2c gpio; do
    if getent group "$grp" &>/dev/null; then
        if ! id -nG "$APP_USER" | grep -qw "$grp"; then
            sudo usermod -aG "$grp" "$APP_USER"
            echo "  Added $APP_USER to group: $grp"
            NEEDS_GROUP_REBOOT=true
        fi
    fi
done
if ! $NEEDS_GROUP_REBOOT; then
    echo "  Group membership OK."
fi

# ── Step 4: Bookworm SPI chip-select overlay fix ──────────────────────────────

echo "[4/7] Checking SPI overlay config..."
CONFIG_FILE=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "$candidate" ]; then
        CONFIG_FILE="$candidate"
        break
    fi
done

if [ -n "$CONFIG_FILE" ]; then
    if grep -q "dtparam=spi=on" "$CONFIG_FILE" && ! grep -q "dtoverlay=spi0-0cs" "$CONFIG_FILE"; then
        echo "  Applying Bookworm SPI chip-select fix to $CONFIG_FILE..."
        sudo sed -i '/dtparam=spi=on/a dtoverlay=spi0-0cs' "$CONFIG_FILE"
    else
        echo "  SPI overlay config OK."
    fi
else
    echo "  WARNING: Could not locate config.txt. Apply the SPI overlay fix manually if the display does not work."
fi

# ── Step 5: Python virtual environment ───────────────────────────────────────
# Self-contained venv with piwheels for fast ARM wheel delivery.
# Only rebuild from scratch if the Python version has changed; otherwise
# just upgrade packages in place — much faster on a Pi Zero 2W.

echo "[5/7] Setting up Python virtual environment..."
VENV_STAMP="$SCRIPT_DIR/venv/.python_stamp"
CURRENT_PY=$(python3 -c "import sys; print(sys.version_info[:2])")

needs_rebuild=true
if [ -f "$VENV_STAMP" ] && grep -qF "$CURRENT_PY" "$VENV_STAMP" 2>/dev/null; then
    needs_rebuild=false
fi

if $needs_rebuild; then
    echo "  Building fresh venv (Python version changed or first install)..."
    rm -rf "$SCRIPT_DIR/venv"
    python3 -m venv "$SCRIPT_DIR/venv"
    "$SCRIPT_DIR/venv/bin/pip" install --quiet --upgrade pip
    echo "$CURRENT_PY" > "$VENV_STAMP"
else
    echo "  Venv OK — upgrading packages..."
fi

"$SCRIPT_DIR/venv/bin/pip" install \
    --quiet \
    --upgrade \
    --extra-index-url https://www.piwheels.org/simple \
    "inky[rpi]" \
    Pillow \
    requests \
    numpy

echo "  Packages ready."

# ── Step 6: API credentials ───────────────────────────────────────────────────

echo "[6/7] Configuring Tempest API credentials..."
if [ ! -f "$SCRIPT_DIR/secrets.py" ]; then
    echo ""
    read -rp "  Enter your Tempest Station ID : " station_id
    read -rp "  Enter your Tempest API Token  : " api_token
    cat > "$SCRIPT_DIR/secrets.py" <<EOF
STATION_ID = "$station_id"
TOKEN = "$api_token"
EOF
    chmod 600 "$SCRIPT_DIR/secrets.py"
    echo "  secrets.py created."
else
    echo "  secrets.py already exists — skipping."
fi

# ── Step 7: systemd service + timer ──────────────────────────────────────────
# Using systemd instead of cron gives: persistent logs across reboots (journald),
# network-online dependency (waits for WiFi before first run), and a 5-minute
# timeout that kills a hung display write rather than blocking forever.

echo "[7/7] Installing systemd service and timer..."

sudo tee /etc/systemd/system/tempest-inky.service > /dev/null <<EOF
[Unit]
Description=Tempest Inky Weather Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$APP_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/main.py
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300
SyslogIdentifier=tempest-inky

# Run at lower priority so SSH and system processes always stay responsive
Nice=10
IOSchedulingClass=idle

# Hard memory cap: if the process exceeds this it is killed cleanly
# rather than the kernel OOM-killing sshd and other critical services
MemoryMax=350M
MemorySwapMax=0

# Make this process the first candidate for OOM killing if the system
# ever gets into trouble, protecting the rest of the OS
OOMScoreAdjust=500
EOF

sudo tee /etc/systemd/system/tempest-inky.timer > /dev/null <<EOF
[Unit]
Description=Tempest Inky refresh — every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tempest-inky.timer
sudo systemctl start tempest-inky.timer

echo "  Timer enabled."

# ── Configure journald volatile storage to reduce SD card writes ──────────────

sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/volatile.conf > /dev/null <<EOF
[Journal]
Storage=volatile
EOF
sudo systemctl restart systemd-journald

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "========================================"
echo "  Setup Complete!"
echo ""
echo "  The display will update 2 minutes after reboot,"
echo "  then every 15 minutes automatically."
echo ""
echo "  Useful commands:"
echo "    Check status : systemctl status tempest-inky.timer"
echo "    View logs    : journalctl -u tempest-inky.service -n 50"
echo "    Run now      : sudo systemctl start tempest-inky.service"
echo "    Force refresh: sudo -u $APP_USER $SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/main.py"
echo ""
echo "  IMPORTANT: Reboot now to activate SPI/I2C and group changes."
echo "  Command: sudo reboot"
echo "========================================"

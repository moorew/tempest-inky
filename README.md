# Tempest Inky

A Python application for the Raspberry Pi that displays a clean, readable weather dashboard on a Pimoroni Inky e-ink display, pulling live data directly from a WeatherFlow Tempest weather station.

![Dashboard preview](dashboard-preview.jpg)

---

## Hardware

| Component | Details |
|-----------|---------|
| Pi | Raspberry Pi Zero 2 W (or any Pi with 40-pin header) |
| Display | Pimoroni Inky Impression 7.3" (800×480, 7-colour) |
| OS | Raspberry Pi OS Lite — Bookworm (64-bit recommended) |

---

## Pre-install: Setting up a fresh headless Pi

These steps are done on your laptop/desktop before you touch the Pi.

### 1. Flash the SD card

Use the [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Choose:
- **OS**: Raspberry Pi OS Lite (64-bit) — Bookworm
- **Storage**: your SD card (16 GB minimum; use a reputable brand — SanDisk or Samsung)

Before writing, click the **gear icon** (Advanced Options) and configure:
- ✅ Enable SSH (password authentication)
- ✅ Set username and password
- ✅ Configure WiFi (SSID + password)
- ✅ Set locale / timezone

Write the image to the SD card.

### 2. First boot

Insert the card into the Pi, attach the Inky display, and power on. Wait ~60 seconds, then SSH in:

```bash
ssh yourname@raspberrypi.local
# or use the IP address shown in your router's device list
```

### 3. Update the system

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Wait for the Pi to reboot, then SSH back in before continuing.

> **Note:** Always update before installing. It avoids broken package dependencies and ensures you have the latest kernel/firmware for the Inky SPI driver.

---

## Installation

### 1. Install git (not pre-installed on Pi OS Lite)

```bash
sudo apt install -y git
```

### 2. Clone the repository

```bash
git clone https://github.com/moorew/tempest-inky.git
cd tempest-inky
```

### 3. Get your Tempest credentials

You need:
- **Station ID** — visible in the WeatherFlow app under *Settings → Stations*
- **Personal Use Token** — generate one at [tempestwx.com/account/tokens](https://tempestwx.com/account/tokens)

### 4. Run the installer

```bash
bash install.sh
```

The installer will:
- Install system dependencies
- Enable SPI and I2C hardware interfaces
- Ensure your user is in the `spi`, `i2c`, and `gpio` groups
- Apply the Bookworm SPI chip-select overlay fix
- Create a self-contained Python virtual environment
- Prompt you for your Station ID and API token
- Install a **systemd timer** that refreshes the display every 15 minutes

### 5. Reboot

```bash
sudo reboot
```

The display will update automatically ~2 minutes after boot, then every 15 minutes.

---

## How it works

| Feature | Detail |
|---------|--------|
| **Refresh schedule** | systemd timer: 2 min after boot, then every 15 min |
| **Virtual environment** | Self-contained; immune to system Python upgrades |
| **Network resilience** | Waits up to 2 minutes for WiFi before fetching |
| **API retry** | Up to 3 attempts with exponential backoff on failure |
| **Logging** | systemd journal (RAM-backed; no extra SD card wear) |
| **Hang protection** | systemd kills the process after 5 minutes |

---

## Managing the service

```bash
# Check whether the timer is running
systemctl status tempest-inky.timer

# See recent logs (most useful for debugging)
journalctl -u tempest-inky.service -n 50

# Force an immediate refresh
sudo systemctl start tempest-inky.service

# Stop automatic refreshes
sudo systemctl stop tempest-inky.timer

# Re-enable after stopping
sudo systemctl start tempest-inky.timer
```

---

## Troubleshooting

### Display never updates after install

1. Check that you rebooted after installation.
2. Check logs: `journalctl -u tempest-inky.service -n 50`
3. Try a manual run to see errors directly:
   ```bash
   cd ~/tempest-inky
   sudo systemctl start tempest-inky.service
   journalctl -u tempest-inky.service -n 20
   ```

### "No module named inky" or import errors

The virtual environment may be corrupted (common after a system Python upgrade). Re-run the installer — it rebuilds the venv cleanly:

```bash
cd ~/tempest-inky
bash install.sh
```

### Display shows "DATA FETCH ERROR"

Your API credentials may be wrong, or the Pi has no internet. Check:
```bash
# Test connectivity
curl -s "https://swd.weatherflow.com/swd/rest/observations/station/YOUR_ID?token=YOUR_TOKEN" | python3 -m json.tool | head -20

# Check secrets file
cat ~/tempest-inky/secrets.py
```

### SPI / display not detected

Verify SPI is enabled:
```bash
ls /dev/spidev*   # should show /dev/spidev0.0
```

If missing, run `sudo raspi-config` → Interface Options → SPI → Enable, then reboot.

### Works fine but stops after a few months

The most common cause is SD card corruption from power loss, or sectors wearing out on a low-quality card. Recommendations:
- Use a **name-brand SD card** (Samsung PRO Endurance or SanDisk High Endurance are designed for constant-write workloads like this)
- **Avoid powering off the Pi by unplugging** — use `sudo shutdown -h now` first whenever possible
- If it stops working, re-run `bash install.sh` before reinstalling the OS — it rebuilds the venv and re-registers the service, which fixes most software-level failures

---

## Manual run (without the timer)

```bash
cd ~/tempest-inky
venv/bin/python3 main.py
```

---

## Updating

Pull the latest code and re-run the installer. It is safe to run as many times as needed — it rebuilds the venv only if the Python version has changed, updates all packages, and refreshes the systemd unit files:

```bash
cd ~/tempest-inky
git pull
bash install.sh
```

No reboot is required for code-only updates. The timer picks up the new `main.py` on its next 15-minute tick. If the systemd unit files changed (e.g. after upgrading from the old cron-based setup), reboot once after the first run of the new installer.

To check that the update took effect:

```bash
journalctl -u tempest-inky.service -n 20
```

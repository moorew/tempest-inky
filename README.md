# Tempest Inky

A Python application for the Raspberry Pi that displays a weather dashboard on a Pimoroni Inky e-ink display, pulling live data directly from a WeatherFlow Tempest weather station.

The layout is built for a panel on a wall: type is sized by viewing distance, so the temperature and the day's headline read from across a room while the standing metrics read at walk-up. Colour is categorical, never decorative — hue carries condition, height carries quantity.

![Dashboard preview](dashboard-preview.png)

*Illustrative render. The panel shows your own station's data.*

---

## What's on the display

Four full-width regions with fixed geometry — nothing moves between refreshes, which is what keeps an e-ink panel from ghosting and means the number you want is always where you last found it.

| Region | Contents |
|--------|----------|
| **Concern band** | The single highest-priority thing worth knowing — lightning, rain starting, gusts, frost, or station trouble — filled in that concern's colour. Reads `NOTHING TO REPORT` when all is quiet, and keeps its height either way. |
| **Spine** | Current temperature, condition and feels-like, today's low → high, and how old the station reading is. |
| **Next 12 hours** | Precipitation probability per hour, on a fixed 0–100 % scale so an empty chart reads as *dry* rather than broken. Blue bars for snow, green for rain. |
| **Five metrics** | Dew point, rain today, wind, pressure and daylight — always these five, always in this order. A slot fills with its own colour when it needs attention; a metric with no data shows a dash rather than disappearing. |
| **Ten days** | Daily highs as bars: height is temperature, fill is condition, with a 0 °C reference line when freezing falls inside the range. |

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
- Prompt you for your Station ID and API token, saved as `~/secrets.py`
- Install a **systemd timer** that refreshes the display on an adaptive schedule

### 5. Reboot

```bash
sudo reboot
```

The display will update automatically ~2 minutes after boot, then every 15 minutes — more often when rain is likely within the hour, less often overnight.

---

## How it works

| Feature | Detail |
|---------|--------|
| **Refresh schedule** | Adaptive: every 15 min normally, 10 min when rain is likely in the next hour, 30 min overnight (22:00–06:00). The timer ticks every 5 min and `main.py` decides whether a tick is due. |
| **Stale data** | If a fetch fails, the last good reading is redrawn with a `STALE` marker instead of wiping the panel |
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

This appears only when a fetch fails **and** there is no cached reading to fall back on — normally a failed fetch redraws the last good data with a `STALE` marker instead. Your API credentials may be wrong, or the Pi has no internet. Check:
```bash
# Test connectivity
curl -s "https://swd.weatherflow.com/swd/rest/observations/station/YOUR_ID?token=YOUR_TOKEN" | python3 -m json.tool | head -20

# Check secrets file
cat ~/secrets.py
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
venv/bin/python3 main.py --force
```

`--force` renders immediately, ignoring the adaptive schedule. Without it, a run that is not yet due exits straight away without fetching or repainting.

---

## Updating

Pull the latest code and re-run the installer. It is safe to run as many times as needed — it rebuilds the venv only if the Python version has changed, updates all packages, and refreshes the systemd unit files:

```bash
cd ~/tempest-inky
git pull
bash install.sh
```

No reboot is required for code-only updates. The timer picks up the new `main.py` on its next tick. If the systemd unit files changed (e.g. after upgrading from the old cron-based setup), reboot once after the first run of the new installer.

To check that the update took effect:

```bash
journalctl -u tempest-inky.service -n 20
```

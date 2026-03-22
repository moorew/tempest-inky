# Tempest Inky

A Python application for the Raspberry Pi that displays a clean, readable weather dashboard on a Pimoroni Inky e-ink display, pulling live data directly from a WeatherFlow Tempest weather station.

## Prerequisites

* **Hardware:** A Raspberry Pi (Zero 2 W or newer recommended) and a Pimoroni Inky display.
* **OS:** Raspberry Pi OS (Bookworm or newer, 32-bit or 64-bit).
* **Tempest API:** A WeatherFlow Tempest station ID and a Personal Use Token. You can generate a token by logging into the [WeatherFlow Tempest API page](https://weatherflow.github.io/Tempest/api/).

## Automated Installation

The included installation script handles everything: installing system dependencies (to bypass slow library compilation on older Pis), enabling the necessary SPI/I2C hardware ports, applying OS-specific pin fixes, and setting up an automated refresh schedule.

**1. Clone the repository**
```bash
git clone https://github.com/moorew/tempest-inky.git ~cd tempest-inky
```

**2. Run the installer**
Make the script executable and run it. It will prompt you for your Tempest Station ID and API Token during the setup.
```bash
chmod +x install.sh
./install.sh
```

**3. Reboot**
The installer enables hardware interfaces that require a system restart to take effect.
```bash
sudo reboot
```

## How it Works

* **Virtual Environment:** The script uses a local Python virtual environment (`venv`) with the `--system-site-packages` flag. This safely isolates the project while taking advantage of fast, pre-compiled system libraries like NumPy and Pillow.
* **Automated Refresh:** The installer adds a cron job to automatically run the script every 15 minutes. This is the ideal refresh rate to keep weather data current without causing permanent ghosting or wear on the e-ink display. 
* **Logging:** If the screen isn't updating, you can check the background logs by running: `cat /tmp/tempest-inky.log`.

## Manual Execution

If you ever want to force a manual refresh of the screen outside of the 15-minute schedule, navigate to the folder, activate the virtual environment, and run the script:

```bash
cd ~/tempest-inky
source venv/bin/activate
python3 main.py
```

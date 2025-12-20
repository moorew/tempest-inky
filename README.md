# Tempest Inky Dashboard 🌩️

A clean, high-contrast weather dashboard for the [Pimoroni Inky Impression 7.3"](https://shop.pimoroni.com/products/inky-impression-7-3) powered by a [Tempest Weather System](https://weatherflow.com/tempest-weather-system/).

<img src="dashboard-preview.jpg" width="600" alt="Dashboard Preview">
<br>
<img src="IRL.jpg" width="600" alt="IRL Image">

## The Story
I have dreamed of building a custom weather display like this for years, but I never felt I had the technical ability to pull it off. This project was "vibe coded" with the help of Google Gemini acting as my pair programmer.

It is a passion project, built by a hobbyist for hobbyists. I welcome any suggestions, forks, or pull requests from those who really know what they are doing to help make the code cleaner and better!

## Features
* **Crisp E-Paper Design:** Uses the **Merriweather** Serif font for a classic "newspaper" aesthetic that looks great on e-ink.
* **Smart Colors:** Logic-based coloring (Green/Orange/Red) for temperature, wind, and UV comfort levels based on hardware capabilities.
* **Forecast Integration:** 5-Day forecast row populated by the Tempest "Better Forecast" API.
* **Reliable:** Auto-recovers from network issues and runs automatically via cron.

## Hardware Required
* **Raspberry Pi:** Works on Zero 2 W, 3, 4, or 5.
* **Display:** [Pimoroni Inky Impression 7.3"](https://shop.pimoroni.com/products/inky-impression-7-3) (7-color e-paper).
* **Weather Station:** [Tempest Weather System](https://weatherflow.com/tempest-weather-system/).

## Installation (Fresh Install)
These instructions assume you are starting with a fresh Raspberry Pi OS (Bookworm or newer) image.

### Clone the Repo
```
git clone https://github.com/moorew/tempest-inky.git
cd tempest-inky
```

### Run the Installer
I have included a script to set up the Python environment, install dependencies (Pillow, Inky, Requests), and download the necessary fonts automatically.
```
chmod +x install.sh
./install.sh
```

### Configuration
**You must add your Tempest credentials for the dashboard to work.**

⚠️ Station ID: Find this on the Tempest website (Settings > Stations > public-url-id).

⚠️ API Token: Generate a Personal Use Token here: [Tempest Settings > Data Authorizations](https://tempestwx.com/settings/tokens).

  **1. Open the script:** [nano main.py](https://github.com/moorew/tempest-inky/blob/main/main.py)
    
  **2. Locate the CONFIGURATION section at the top and paste your details:**
    
    ```
    STATION_ID = "12345"
    TOKEN = "your-long-token-string"`
    ```
    
  **3. Save and exit (Ctrl+X, Y, Enter).**

### Run It

**Test the dashboard manually:**
```
./venv/bin/python3 main.py`
```

✅ If successful, the installer has already set up a cron job to refresh the screen every 20 minutes automatically.

## Credits & Licenses
This is a non-commercial passion project built by a hobbyist, for hobbyists. I welcome suggestions and improvements!

All data and assets belong to their respective creators:

* **Weather Data:** Powered by the [Tempest API](https://weatherflow.github.io/Tempest/api/).
* **Icons:** [Weather Icons](https://erikflowers.github.io/weather-icons/) by Erik Flowers (Licensed under SIL OFL 1.1).
* **Typography:** [Merriweather](https://fonts.google.com/specimen/Merriweather) by Sorkin Type (Licensed under SIL OFL 1.1).
* **Hardware Library:** [Inky](https://github.com/pimoroni/inky) by Pimoroni.

### Prerequisite: Remote Access
I highly recommend installing [Tailscale](https://tailscale.com) on your Pi for easy, secure SSH access from anywhere without opening ports.


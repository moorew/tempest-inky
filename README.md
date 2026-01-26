# Tempest Inky Dashboard V2 🌩️

A rich, high-contrast weather dashboard for the [Pimoroni Inky Impression 7.3"](https://shop.pimoroni.com/products/inky-impression-7-3) powered by a [Tempest Weather System](https://weatherflow.com/tempest-weather-system/).

<img src="dashboard-preview.jpg" width="600" alt="Dashboard Preview">

## The Story
I have dreamed of building a custom weather display like this for years, but I never felt I had the technical ability to pull it off. This project was "vibe coded" with the help of Google Gemini acting as my pair programmer.

It is a passion project, built by a hobbyist for hobbyists. I welcome any suggestions, forks, or pull requests from those who really know what they are doing to help make the code cleaner and better!

## V2 Features (New!)
* **Dark Mode Aesthetic:** A pure black background with high-contrast white text, designed to make the e-ink colors pop.
* **"Sugar Fruit" Palette:** Sophisticated temperature coloring that shifts from **Periwinkle (Cold)** to **Sage**, **Peach**, **Sand**, and **Burnt Orange (Hot)**.
* **Dynamic Trend Graph:** A custom-built 24-hour temperature graph with gradient hatching that visualizes the warming or cooling trend of the day.
* **Rich Data:** Now displays "Feels Like" temp, Rain Accumulation (mm), Humidity, Pressure, UV Index, and specific Beaufort Scale wind icons.
* **Forecast Integration:** 5-Day forecast row populated by the Tempest "Better Forecast" API.

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

Get your Station ID: Find this on the Tempest website (Settings > Stations > public-url-id).

Get your API Token: Generate a Personal Use Token here: Tempest Settings > Data Authorizations.

Create the secrets file:

`nano secrets.py`
Paste your details into the file:

```
STATION_ID = "12345"
TOKEN = "your-long-token-string"
```
Save and exit.

_Note: secrets.py is ignored by git, so your keys will remain safe._

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
* **Hardware Library:** [Inky](https://github.com/pimoroni/inky) by Pimoroni.

### Prerequisite: Remote Access
I highly recommend installing [Tailscale](https://tailscale.com) on your Pi for easy, secure SSH access from anywhere without opening ports.


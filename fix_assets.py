import os
import requests

# Ensure assets folder exists
if not os.path.exists('assets'):
    os.makedirs('assets')

# We use the Bas Milius 'Meteocons' repo which is public and reliable
BASE_URL = "https://raw.githubusercontent.com/basmilius/weather-icons/master/design/fill/final"

# Map our local filenames -> Remote filenames
# Note: This set uses slightly different naming, so we map them manually
icon_map = {
    "icon-clear-day.png":           "clear-day.png",
    "icon-clear-night.png":         "clear-night.png",
    "icon-cloudy.png":              "cloudy.png",
    "icon-partly-cloudy-day.png":   "partly-cloudy-day.png",
    "icon-partly-cloudy-night.png": "partly-cloudy-night.png",
    "icon-rain.png":                "rain.png",
    "icon-snow.png":                "snow.png",
    "icon-sleet.png":               "sleet.png",
    "icon-wind.png":                "wind.png",
    "icon-fog.png":                 "fog.png",
    "icon-thunderstorm.png":        "thunderstorms.png" 
}

print("⬇️  Downloading icons from Bas Milius repo...")

for local_name, remote_name in icon_map.items():
    url = f"{BASE_URL}/{remote_name}"
    path = os.path.join('assets', local_name)
    
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(path, 'wb') as f:
                f.write(r.content)
            print(f"✅ Saved {local_name}")
        else:
            print(f"❌ Failed {local_name} (Status {r.status_code})")
            print(f"   URL tried: {url}")
    except Exception as e:
        print(f"❌ Error: {e}")

print("\n✨ Done! If you see green checks, you are ready.")
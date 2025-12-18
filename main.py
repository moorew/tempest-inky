import sys
import time
import os
import requests
from PIL import Image, ImageDraw, ImageFont

# --- ATTEMPT TO IMPORT INKY (Hardware Check) ---
try:
    from inky.auto import auto
    INKY_AVAILABLE = True
except ImportError:
    INKY_AVAILABLE = False

# --- CONFIGURATION ---
# ⚠️ REPLACE THESE WITH YOUR ACTUAL KEYS AFTER DOWNLOADING
STATION_ID = "YOUR_STATION_ID"
TOKEN = "YOUR_API_TOKEN"

URL_OBS = f"https://swd.weatherflow.com/swd/rest/observations/station/{STATION_ID}?token={TOKEN}"
URL_FORECAST = f"https://swd.weatherflow.com/swd/rest/better_forecast?station_id={STATION_ID}&token={TOKEN}"

# Font Paths (Dynamic for Windows/Linux compatibility)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE_DIR, "assets")
FONT_BOLD = os.path.join(ASSETS, "Merriweather-Bold.ttf")
FONT_MED = os.path.join(ASSETS, "Merriweather-Regular.ttf")
FONT_WI = os.path.join(ASSETS, "weathericons.ttf")

WIDTH = 800
HEIGHT = 480

# --- HARDWARE COLORS ---
WHITE  = (255, 255, 255)
BLACK  = (0, 0, 0)
RED    = (255, 0, 0)
BLUE   = (0, 0, 255)
GREEN  = (0, 255, 0)
ORANGE = (255, 128, 0)
YELLOW = (255, 255, 0)

# --- HELPERS ---

def get_wind_direction(degrees):
    dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    ix = round(degrees / (360. / len(dirs)))
    return dirs[ix % len(dirs)]

def get_temp_color(temp):
    if temp < 5: return BLUE
    if 15 <= temp < 20: return GREEN
    if 20 <= temp < 25: return ORANGE
    if temp >= 25: return RED
    return BLACK

def get_stat_color(value, type_):
    if type_ == 'wind':
        if value > 15: return RED
        if value > 10: return ORANGE
        return GREEN 
    if type_ == 'uv':
        if value > 8: return RED
        if value > 5: return ORANGE
        return GREEN
    if type_ == 'humidity':
        if value > 70: return BLUE
        if value < 40: return ORANGE
        return GREEN
    return BLACK

def get_wi_icon(icon_name):
    mapping = {
        'clear-day':           ('\uf00d', ORANGE),
        'clear-night':         ('\uf02e', BLACK),
        'cloudy':              ('\uf013', BLACK),
        'partly-cloudy-day':   ('\uf002', ORANGE),
        'partly-cloudy-night': ('\uf031', BLACK),
        'foggy':               ('\uf014', BLACK),
        'windy':               ('\uf050', BLACK),
        'rainy':               ('\uf019', BLUE),
        'possibly-rainy-day':  ('\uf008', BLUE),
        'possibly-rainy-night':('\uf028', BLUE),
        'very-light-rain':     ('\uf01c', BLUE),
        'snow':                ('\uf01b', BLUE),
        'possibly-snow-day':   ('\uf00a', BLUE),
        'possibly-snow-night': ('\uf02a', BLUE),
        'sleet':               ('\uf0b5', BLUE),
        'possibly-sleet-day':  ('\uf0b2', BLUE),
        'possibly-sleet-night':('\uf0b4', BLUE),
        'wintry-mix-likely':   ('\uf017', BLUE),
        'wintry-mix-possible': ('\uf017', BLUE),
        'thunderstorm':                ('\uf01e', RED),
        'thunderstorms-likely':        ('\uf01e', RED),
        'thunderstorms-possible':      ('\uf01d', RED),
        'possibly-thunderstorm-day':   ('\uf010', RED),
        'possibly-thunderstorm-night': ('\uf02d', RED),
    }
    return mapping.get(icon_name, ('\uf013', BLACK))

def fetch_weather():
    try:
        r_obs = requests.get(URL_OBS, timeout=20).json()
        # API Error Handling
        if 'status' in r_obs and r_obs['status'].get('status_code') != 0:
             print(f"API Error: {r_obs}")
             return None
        if 'obs' not in r_obs:
             return None
             
        obs = r_obs['obs'][0]
        
        r_for = requests.get(URL_FORECAST, timeout=20).json()
        current = r_for['current_conditions']
        daily = r_for['forecast']['daily']

        forecast_data = []
        for day in daily[:5]:
            d_high = round(day['air_temp_high'], 0)
            d_low = round(day['air_temp_low'], 0)
            d_icon = day.get('icon', 'cloudy')
            d_name = time.strftime("%a", time.localtime(day['day_start_local']))
            i_char, i_color = get_wi_icon(d_icon)
            forecast_data.append({
                'day': d_name, 'high': int(d_high), 'low': int(d_low),
                'icon_char': i_char, 'icon_color': i_color
            })

        main_char, main_color = get_wi_icon(current.get('icon', 'clear-day'))

        return {
            "temp": round(obs.get('air_temperature', 0), 1),
            "humidity": obs.get('relative_humidity', 0),
            "wind_speed": round(obs.get('wind_avg', 0), 1),
            "wind_dir": get_wind_direction(obs.get('wind_direction', 0)),
            "pressure": round(obs.get('sea_level_pressure', 0), 0),
            "rain_today": round(obs.get('precip_accum_local_day', 0), 1),
            "uv": round(obs.get('uv', 0), 1),
            "strikes": obs.get('strike_count', 0),
            "icon_char": main_char,
            "icon_color": main_color,
            "summary": current.get('summary', 'Clear'),
            "forecast": forecast_data
        }
    except Exception as e:
        print(f"Error fetching: {e}")
        return None

def create_dashboard(weather):
    # Setup Palette
    img = Image.new("P", (WIDTH, HEIGHT), color=1)
    palette = [
        0, 0, 0,        255, 255, 255,  0, 255, 0,
        0, 0, 255,      255, 0, 0,      255, 255, 0,
        255, 128, 0,
    ]
    palette += [255, 255, 255] * (256 - 7)
    img.putpalette(palette)
    draw = ImageDraw.Draw(img)

    def get_color_index(rgb):
        if rgb == BLACK: return 0
        if rgb == WHITE: return 1
        if rgb == GREEN: return 2
        if rgb == BLUE:  return 3
        if rgb == RED:   return 4
        if rgb == YELLOW: return 5
        if rgb == ORANGE: return 6
        return 0

    try:
        font_huge = ImageFont.truetype(FONT_BOLD, 130)
        font_wi_main = ImageFont.truetype(FONT_WI, 110) 
        font_wi_small = ImageFont.truetype(FONT_WI, 50) 
        font_header = ImageFont.truetype(FONT_BOLD, 24)
        font_med = ImageFont.truetype(FONT_MED, 28)
        font_val = ImageFont.truetype(FONT_BOLD, 28)
        font_small = ImageFont.truetype(FONT_MED, 20)
    except OSError:
        print("Error loading fonts. Check assets folder.")
        sys.exit(1)

    if not weather:
        draw.text((100, 200), "Connect Error", fill=4, font=font_header)
        return img

    # --- DRAWING ---
    # 1. Header
    draw.rectangle([(0, 0), (WIDTH, 65)], fill=0)
    draw.text((30, 32), "Tempest Weather", fill=1, font=font_header, anchor="lm")
    
    # TIMESTAMP (Cross-Platform)
    if os.name == 'nt':
        time_fmt = "%b %d, %#I:%M %p" # Windows
    else:
        time_fmt = "%b %d, %-I:%M %p" # Linux/Pi
        
    ts = time.strftime(time_fmt)
    draw.text((WIDTH - 30, 32), ts, fill=1, font=font_header, anchor="rm")

    # 2. Main Stats
    icon_idx = get_color_index(weather['icon_color'])
    draw.text((40, 120), weather['icon_char'], fill=icon_idx, font=font_wi_main)

    temp_idx = get_color_index(get_temp_color(weather['temp']))
    draw.text((200, 110), f"{weather['temp']}°", fill=temp_idx, font=font_huge)
    draw.text((210, 250), weather['summary'], fill=0, font=font_med)

    # 3. Data Grid
    x_label = 480 
    x_val_anchor = 760 
    y_start = 100
    row_h = 45

    def draw_row(y, label, raw_value, unit_str, type_):
        draw.text((x_label, y), label, fill=0, font=font_med)
        val_rgb = get_stat_color(raw_value, type_)
        val_idx = get_color_index(val_rgb)
        full_str = f"{raw_value} {unit_str}" if unit_str else str(raw_value)
        draw.text((x_val_anchor, y), full_str, fill=val_idx, font=font_val, anchor="ra")

    draw_row(y_start, "Wind", weather['wind_speed'], f"{weather['wind_dir']} m/s", 'wind')
    draw_row(y_start + row_h, "Rain", weather['rain_today'], "mm", 'rain')
    draw_row(y_start + row_h*2, "Humidity", weather['humidity'], "%", 'humidity')
    draw_row(y_start + row_h*3, "Pressure", weather['pressure'], "hPa", 'pressure')
    draw_row(y_start + row_h*4, "UV Index", weather['uv'], "", 'uv')

    # 4. Footer
    divider_y = 330
    if weather['strikes'] > 0:
        draw.rectangle([(0, HEIGHT - 50), (WIDTH, HEIGHT)], fill=4)
        draw.text((150, HEIGHT - 40), f"LIGHTNING DETECTED: {weather['strikes']}", fill=1, font=font_header)
    else:
        draw.line([(20, divider_y), (WIDTH - 20, divider_y)], fill=0, width=2)
        start_x = 40
        gap_x = 150 
        for i, day in enumerate(weather['forecast']):
            x = start_x + (i * gap_x)
            y = divider_y + 15
            draw.text((x + 20, y), day['day'], fill=0, font=font_small)
            i_idx = get_color_index(day['icon_color'])
            draw.text((x + 15, y + 25), day['icon_char'], fill=i_idx, font=font_wi_small)
            draw.text((x + 5, y + 90), f"{day['high']}° / {day['low']}°", fill=0, font=font_small)

    return img

def main():
    print("Fetching weather...")
    weather = fetch_weather()
    
    if weather:
        img = create_dashboard(weather)
        
        if INKY_AVAILABLE:
            # Running on Raspberry Pi with Hardware
            print("Updating Inky display...")
            inky_display = auto()
            inky_display.set_image(img)
            inky_display.show()
        else:
            # Running on Windows (or Pi without hardware)
            print("Inky not detected. Saving preview to 'dashboard-preview.jpg'...")
            img = img.convert("RGB") # Convert for saving as JPG
            img.save("dashboard-preview.jpg")
            print("✅ Saved!")
    else:
        print("Fetch failed.")

if __name__ == "__main__":
    main()
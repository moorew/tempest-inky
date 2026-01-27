import sys
import time
import os
import requests
from PIL import Image, ImageDraw, ImageFont

# --- HARDWARE CHECK ---
try:
    from inky.auto import auto
    INKY_AVAILABLE = True
except ImportError:
    INKY_AVAILABLE = False

# --- CONFIGURATION ---
try:
    from secrets import STATION_ID, TOKEN
except ImportError:
    print("Error: secrets.py not found.")
    sys.exit(1)

URL_OBS = f"https://swd.weatherflow.com/swd/rest/observations/station/{STATION_ID}?token={TOKEN}"
URL_FORECAST = f"https://swd.weatherflow.com/swd/rest/better_forecast?station_id={STATION_ID}&token={TOKEN}"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE_DIR, "assets")

FONT_LIGHT = os.path.join(ASSETS, "font_light.ttf")
FONT_BOLD = os.path.join(ASSETS, "font_bold.ttf")

WIDTH = 800
HEIGHT = 480

# --- HIGH CONTRAST PALETTE ---
BG_COLOR = (255, 255, 255, 255) # Pure White
TEXT_COLOR = (0, 0, 0)          # Pure Black
LINE_COLOR = (0, 0, 0)          # Black

# UPDATED: Softer Badge Color (Steel Grey)
# Old was (50,50,50). New is (130,130,130)
BADGE_COLOR = (130, 130, 130)   

# DEEP SATURATED COLORS (Maximum Contrast)
COL_COLD = (0, 0, 139)      # Dark Blue
COL_COOL = (0, 100, 0)      # Dark Green
COL_MILD = (160, 82, 45)    # Sienna (Brown/Orange)
COL_WARM = (178, 34, 34)    # Fire Brick Red
COL_HOT  = (255, 0, 0)      # Pure Red

# --- LOGIC HELPERS ---

def get_wind_direction(degrees):
    dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    ix = round(degrees / (360. / len(dirs)))
    return dirs[ix % len(dirs)]

def get_beaufort_icon_name(speed_mph):
    if speed_mph < 1: return "wind-beaufort-0"
    if speed_mph < 4: return "wind-beaufort-1"
    if speed_mph < 8: return "wind-beaufort-2"
    if speed_mph < 13: return "wind-beaufort-3"
    if speed_mph < 19: return "wind-beaufort-4"
    if speed_mph < 25: return "wind-beaufort-5"
    if speed_mph < 32: return "wind-beaufort-6"
    if speed_mph < 39: return "wind-beaufort-7"
    if speed_mph < 47: return "wind-beaufort-8"
    if speed_mph < 55: return "wind-beaufort-9"
    if speed_mph < 64: return "wind-beaufort-10"
    if speed_mph < 73: return "wind-beaufort-11"
    return "wind-beaufort-12"

def get_uv_icon_name(uv_index):
    uv = int(round(uv_index))
    if uv <= 0: return "uv-index"
    if uv > 11: uv = 11
    return f"uv-index-{uv}"

def get_temp_color(temp):
    if temp < 5: return COL_COLD
    if 5 <= temp < 15: return COL_COOL
    if 15 <= temp < 22: return COL_MILD
    if 22 <= temp < 28: return COL_WARM
    return COL_HOT

def get_icon_image(icon_name, size=(100, 100)):
    clean_name = icon_name.lower().replace(".svg", "").replace(".png", "")
    
    name_map = {
        'clear-day': 'clear-day', 'clear-night': 'clear-night',
        'rainy': 'rain', 'snow': 'snow', 'sleet': 'sleet',
        'wind': 'wind', 'foggy': 'fog', 'cloudy': 'cloudy',
        'partly-cloudy-day': 'partly-cloudy-day',
        'partly-cloudy-night': 'partly-cloudy-night',
        'thunderstorm': 'thunderstorms',
    }
    
    suffix = name_map.get(clean_name, clean_name)
    if suffix == clean_name:
        if 'thunder' in clean_name: suffix = 'thunderstorms'
        elif 'rain' in clean_name: suffix = 'rain'
        elif 'snow' in clean_name: suffix = 'snow'
        elif 'fog' in clean_name: suffix = 'fog'
    
    candidates = [
        f"weather-icons_{clean_name}.png",
        f"weather-icons_{suffix}.png",
        f"{clean_name}.png"
    ]

    for filename in candidates:
        path = os.path.join(ASSETS, filename)
        if os.path.exists(path):
            try:
                icon = Image.open(path).convert("RGBA")
                return icon.resize(size, Image.Resampling.LANCZOS)
            except: pass
            
    return Image.new("RGBA", size, (0,0,0,0))

# --- VISUAL HELPER: BADGES ---
def paste_icon_with_badge(base_img, draw, icon, center_x, center_y, bg_size=100):
    r = bg_size / 2
    draw.ellipse(
        [(center_x - r, center_y - r), (center_x + r, center_y + r)],
        fill=BADGE_COLOR
    )
    w, h = icon.size
    paste_x = int(center_x - (w / 2))
    paste_y = int(center_y - (h / 2))
    base_img.paste(icon, (paste_x, paste_y), icon)

# --- GRAPH ENGINE ---

def interpolate(val_start, val_end, fraction):
    return val_start + (val_end - val_start) * fraction

def draw_graph_hatching(draw, data_points, box):
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    
    if not data_points: return

    min_val = min(data_points)
    max_val = max(data_points)
    val_range = max_val - min_val if max_val != min_val else 1
    
    points_count = len(data_points)
    step_px = w / (points_count - 1)

    hatch_spacing = 2
    
    for px in range(0, int(w), hatch_spacing):
        index = int(px / step_px)
        if index >= points_count - 1: break
        
        remainder = px % step_px
        fraction = remainder / step_px
        val = interpolate(data_points[index], data_points[index + 1], fraction)
        
        py = y2 - ((val - min_val) / val_range * h)
        screen_x = x1 + px
        
        bar_color = get_temp_color(val)
        draw.line([(screen_x, py), (screen_x, y2)], fill=bar_color, width=1)

    points = []
    for i, val in enumerate(data_points):
        px = x1 + (i * step_px)
        py = y2 - ((val - min_val) / val_range * h)
        points.append((px, py))
    draw.line(points, fill=TEXT_COLOR, width=3)

def fetch_weather():
    try:
        r_obs = requests.get(URL_OBS, timeout=20).json()
        if 'obs' not in r_obs or len(r_obs['obs']) == 0: return None
        obs = r_obs['obs'][0]
        
        r_for = requests.get(URL_FORECAST, timeout=20).json()
        current = r_for['current_conditions']
        daily = r_for['forecast']['daily']
        hourly = r_for['forecast']['hourly']

        forecast_data = []
        for day in daily[:5]:
            d_high = round(day['air_temp_high'], 0)
            d_low = round(day['air_temp_low'], 0)
            d_icon = day.get('icon', 'cloudy')
            d_name = time.strftime("%a", time.localtime(day['day_start_local']))
            forecast_data.append({
                'day': d_name, 'high': int(d_high), 'low': int(d_low), 'icon_name': d_icon
            })

        hourly_temps = [x['air_temperature'] for x in hourly[:24]]
        wind_speed_mph = obs.get('wind_avg', 0) * 2.237

        return {
            "temp": round(obs.get('air_temperature', 0), 1),
            "feels_like": int(round(current.get('feels_like', 0))), 
            "wind_speed": round(obs.get('wind_avg', 0), 1),
            "wind_dir": get_wind_direction(obs.get('wind_direction', 0)),
            "wind_icon": get_beaufort_icon_name(wind_speed_mph),
            "pressure": round(obs.get('sea_level_pressure', 0), 0),
            "rain_today": round(obs.get('precip_accum_local_day', 0), 1),
            "humidity": obs.get('relative_humidity', 0),
            "uv": obs.get('uv', 0),
            "uv_icon": get_uv_icon_name(obs.get('uv', 0)),
            "icon_name": current.get('icon', 'clear-day'),
            "summary": current.get('summary', 'Clear'),
            "forecast": forecast_data,
            "hourly_temps": hourly_temps
        }
    except Exception as e:
        print(f"Error fetching: {e}")
        return None

def create_dashboard(weather):
    img = Image.new("RGBA", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    if not weather:
        print("❌ Weather data was empty.")
        return img

    try:
        # BOLD FONTS EVERYWHERE
        font_huge = ImageFont.truetype(FONT_BOLD, 130)
        font_condition = ImageFont.truetype(FONT_BOLD, 35)
        font_feels = ImageFont.truetype(FONT_BOLD, 25)
        font_forecast = ImageFont.truetype(FONT_BOLD, 22)
        font_val = ImageFont.truetype(FONT_BOLD, 25) 
        font_label = ImageFont.truetype(FONT_BOLD, 20)
        font_tiny = ImageFont.truetype(FONT_BOLD, 16)
        
    except Exception as e:
        print(f"❌ FONT ERROR: {e}")
        return img

    # --- TOP RIGHT: Timestamp ---
    updated_time = time.strftime("Updated: %H:%M")
    draw.text((780, 5), updated_time, fill=LINE_COLOR, font=font_tiny, anchor="rt")

    # 1. TOP LEFT: Main Condition (WITH BADGE)
    main_icon = get_icon_image(weather['icon_name'], size=(140, 140))
    paste_icon_with_badge(img, draw, main_icon, 115, 110, bg_size=170)
    
    # 2. TOP CENTER: Big Temp & Layout
    temp_str = f"{weather['temp']}°"
    draw.text((380, 30), temp_str, fill=get_temp_color(weather['temp']), font=font_huge, anchor="mt")
    
    feels_str = f"Feels Like {weather['feels_like']}°"
    draw.text((380, 160), feels_str, fill=TEXT_COLOR, font=font_feels, anchor="mm")
    
    draw.text((380, 195), weather['summary'], fill=TEXT_COLOR, font=font_condition, anchor="mm")

    # 3. TOP RIGHT: Rich Stats Grid
    draw.line([(580, 20), (580, 215)], fill=LINE_COLOR, width=2)
    
    stats_rows = [
        (weather['wind_icon'], f"{weather['wind_speed']} {weather['wind_dir']}"),
        ("rain", f"{weather['rain_today']} mm"), 
        ("humidity", f"{weather['humidity']}%"),
        ("barometer", f"{weather['pressure']} hPa"),
        (weather['uv_icon'], f"UV {weather['uv']}")
    ]
    
    start_y = 15
    gap = 40  
    
    for i, (icon_name, val_text) in enumerate(stats_rows):
        y = start_y + (i * gap)
        icon = get_icon_image(icon_name, size=(30, 30))
        paste_icon_with_badge(img, draw, icon, 620, y + 20, bg_size=42)
        
        draw.text((780, y + 20), val_text, fill=TEXT_COLOR, font=font_val, anchor="rm")

    # 4. MIDDLE: Graph
    if 'hourly_temps' in weather:
        draw.text((40, 235), "24hr Trend", fill=TEXT_COLOR, font=font_label)
        
        low = min(weather['hourly_temps'])
        high = max(weather['hourly_temps'])
        draw.text((760, 235), f"L:{low}°  H:{high}°", fill=TEXT_COLOR, font=font_label, anchor="rs")

        graph_box = (40, 260, 760, 315)
        draw_graph_hatching(draw, weather['hourly_temps'], graph_box)

    # 5. BOTTOM: Forecast (LIFTED UP FOR SAFETY)
    # Line: y=340 -> 325 (Shifted -15)
    draw.line([(40, 325), (760, 325)], fill=LINE_COLOR, width=3)
    
    start_x = 50
    for i, day in enumerate(weather['forecast']):
        x = start_x + (i * 150)
        
        # Day: y=360 -> 345
        draw.text((x + 40, 345), day['day'], fill=TEXT_COLOR, font=font_forecast, anchor="mm")
        
        day_icon = get_icon_image(day['icon_name'], size=(70, 70))
        # Icon Center: y=415 -> 400
        paste_icon_with_badge(img, draw, day_icon, x + 40, 400, bg_size=85)
        
        # Temps: y=465 -> 450 (Plenty of room at bottom now)
        draw.text((x + 40, 450), f"{day['high']}° / {day['low']}°", fill=TEXT_COLOR, font=font_forecast, anchor="mm")

    return img

def main():
    print("Fetching weather...")
    weather = fetch_weather()
    
    if weather:
        img = create_dashboard(weather)
        
        if INKY_AVAILABLE:
            from inky.auto import auto
            display = auto()
            display.set_image(img)
            display.show()
        else:
            print("Preview saved to 'dashboard-preview.jpg'")
            img = img.convert("RGB")
            img.save("dashboard-preview.jpg")

if __name__ == "__main__":
    main()

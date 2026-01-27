import sys
import time
import os
import requests
import math
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
    STATION_ID = "00000"
    TOKEN = "dummy"

URL_OBS = f"https://swd.weatherflow.com/swd/rest/observations/station/{STATION_ID}?token={TOKEN}"
URL_FORECAST = f"https://swd.weatherflow.com/swd/rest/better_forecast?station_id={STATION_ID}&token={TOKEN}"

# --- PATH CONFIGURATION ---
def get_base_path():
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return base_path

BASE_DIR = get_base_path()
ASSETS_ROOT = os.path.join(BASE_DIR, "assets")

FONT_LIGHT = os.path.join(ASSETS_ROOT, "font_light.ttf")
FONT_BOLD = os.path.join(ASSETS_ROOT, "font_bold.ttf")

WIDTH = 800
HEIGHT = 480

# --- THEMES ---
STYLES = {
    "inky": {
        "asset_folder": "inky",
        "bg_color": (255, 255, 255, 255),
        "text_color": (0, 0, 0),
        "line_color": (0, 0, 0),
        "graph_type": "vivid_bar",
        "graph_width": 4,
        "is_desktop": False
    },
    "desktop": {
        "asset_folder": "desktop",
        "bg_color": (30, 30, 40, 255),
        "text_color": (240, 240, 240),
        "line_color": (80, 80, 90),
        "graph_type": "gradient_fill",
        "graph_width": 3,
        "is_desktop": True
    }
}

# --- PALETTES ---
COL_COLD = (0, 0, 200)      
COL_COOL = (0, 150, 0)      
COL_MILD = (255, 200, 0)    
COL_WARM = (255, 120, 0)    
COL_HOT  = (200, 0, 0)      

DT_COLD = (100, 149, 237)
DT_COOL = (144, 238, 144)
DT_MILD = (255, 222, 173)
DT_WARM = (255, 160, 122)
DT_HOT  = (255, 99, 71)

# --- HELPERS ---
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

def get_temp_color(temp, theme_mode="inky"):
    if temp < 5: return COL_COLD
    if 5 <= temp < 15: return COL_COOL
    if 15 <= temp < 22: return COL_MILD
    if 22 <= temp < 28: return COL_WARM
    return COL_HOT

def interpolate_rgb(c1, c2, factor):
    return (
        int(c1[0] + (c2[0] - c1[0]) * factor),
        int(c1[1] + (c2[1] - c1[1]) * factor),
        int(c1[2] + (c2[2] - c1[2]) * factor)
    )

def get_smooth_color(temp):
    if temp <= 0: return DT_COLD
    if temp >= 30: return DT_HOT
    if temp < 10: return interpolate_rgb(DT_COLD, DT_COOL, (temp)/10)
    elif temp < 20: return interpolate_rgb(DT_COOL, DT_MILD, (temp-10)/10)
    else: return interpolate_rgb(DT_MILD, DT_HOT, (temp-20)/10)

def get_icon_image(icon_name, theme_config, size=(100, 100)):
    clean_name = icon_name.lower().replace(".svg", "").replace(".png", "")
    name_map = {'clear-day': 'clear-day', 'clear-night': 'clear-night', 'rainy': 'rain', 'snow': 'snow', 'sleet': 'sleet', 'wind': 'wind', 'foggy': 'fog', 'cloudy': 'cloudy', 'partly-cloudy-day': 'partly-cloudy-day', 'partly-cloudy-night': 'partly-cloudy-night', 'thunderstorm': 'thunderstorms'}
    suffix = name_map.get(clean_name, clean_name)
    if suffix == clean_name:
        if 'thunder' in clean_name: suffix = 'thunderstorms'
        elif 'rain' in clean_name: suffix = 'rain'
        elif 'snow' in clean_name: suffix = 'snow'
        elif 'fog' in clean_name: suffix = 'fog'
    
    candidates = [f"weather-icons_{clean_name}.png", f"weather-icons_{suffix}.png", f"{clean_name}.png"]
    theme_folder = os.path.join(ASSETS_ROOT, theme_config['asset_folder'])
    for filename in candidates:
        path = os.path.join(theme_folder, filename)
        if os.path.exists(path):
            try: return Image.open(path).convert("RGBA").resize(size, Image.Resampling.LANCZOS)
            except: pass
    for filename in candidates:
        path = os.path.join(ASSETS_ROOT, filename)
        if os.path.exists(path):
            try: return Image.open(path).convert("RGBA").resize(size, Image.Resampling.LANCZOS)
            except: pass
    return Image.new("RGBA", size, (0,0,0,0))

def interpolate(val_start, val_end, fraction):
    return val_start + (val_end - val_start) * fraction

def get_spline_point(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    v0 = (p2 - p0) * 0.5
    v1 = (p3 - p1) * 0.5
    return (2 * p1 - 2 * p2 + v0 + v1) * t3 + (-3 * p1 + 3 * p2 - 2 * v0 - v1) * t2 + v0 * t + p1

def draw_graph(draw, data_points, box, theme_config):
    # Base Layout
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if not data_points: return

    # --- HEADROOM LOGIC ---
    # Add 20% breathing room to the top and bottom so the graph floats
    min_val, max_val = min(data_points), max(data_points)
    real_range = max_val - min_val
    if real_range < 5: real_range = 5 # Prevent flatline errors
    
    padding = real_range * 0.2 # 20% total padding (10% top, 10% bottom)
    visual_min = min_val - (padding / 2)
    visual_max = max_val + (padding / 2)
    val_range = visual_max - visual_min

    step_px = w / (len(data_points) - 1)

    if theme_config['graph_type'] == "vivid_bar":
        # INKY STYLE
        for px in range(0, int(w), 2): 
            index = int(px / step_px)
            if index >= len(data_points) - 1: break
            fraction = (px % step_px) / step_px
            val = interpolate(data_points[index], data_points[index + 1], fraction)
            py = y2 - ((val - visual_min) / val_range * h)
            py = max(y1, min(y2, py))
            bar_color = get_temp_color(val, "inky")
            draw.line([(x1+px, py), (x1+px, y2)], fill=bar_color, width=1)
        points = []
        for i, val in enumerate(data_points):
            px = x1 + (i * step_px)
            py = y2 - ((val - visual_min) / val_range * h)
            py = max(y1, min(y2, py))
            points.append((px, py))
        draw.line(points, fill=theme_config['text_color'], width=theme_config['graph_width'])
    else:
        # DESKTOP STYLE
        draw_graph_supersampled(draw._image, data_points, box, theme_config, visual_min, visual_max, val_range)

def draw_graph_supersampled(bg_img, data_points, box, theme_config, v_min, v_max, v_range):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    
    SCALE = 4
    sw, sh = int(w * SCALE), int(h * SCALE)
    super_img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    super_draw = ImageDraw.Draw(super_img)
    
    raw_points = []
    for i, val in enumerate(data_points):
        # Add slight X padding (+8px) so it doesn't hug the left wall
        px = (i * (sw / (len(data_points) - 1))) + 8 
        py = sh - ((val - v_min) / v_range * sh)
        py = max(0, min(sh, py))
        raw_points.append((px, py))
    
    spline_points = [raw_points[0]] + raw_points + [raw_points[-1]]
    smooth_curve = []
    steps_per_segment = 40 
    
    for i in range(len(raw_points) - 1):
        p0, p1, p2, p3 = spline_points[i], spline_points[i+1], spline_points[i+2], spline_points[i+3]
        for t_step in range(steps_per_segment):
            t = t_step / steps_per_segment
            sx = get_spline_point(p0[0], p1[0], p2[0], p3[0], t)
            sy = get_spline_point(p0[1], p1[1], p2[1], p3[1], t)
            # Clip X to stop it drawing backwards or off canvas
            sx = max(0, min(sw, sx))
            norm_val = (sh - sy) / sh
            val = (norm_val * v_range) + v_min
            smooth_curve.append((sx, sy, val))
    
    smooth_curve.append((raw_points[-1][0], raw_points[-1][1], data_points[-1]))

    for sx, sy, val in smooth_curve:
        base_col = get_smooth_color(val)
        fill_col = (base_col[0], base_col[1], base_col[2], 50) 
        super_draw.line([(sx, sy), (sx, sh)], fill=fill_col, width=int(2*SCALE))

    for i in range(len(smooth_curve) - 1):
        start, end = smooth_curve[i], smooth_curve[i+1]
        line_col = get_smooth_color(start[2])
        super_draw.line([(start[0], start[1]), (end[0], end[1])], fill=line_col, width=int(3*SCALE))

    smooth_graph = super_img.resize((int(w), int(h)), Image.Resampling.LANCZOS)
    bg_img.paste(smooth_graph, (x1, y1), smooth_graph)

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
            d_high, d_low = round(day['air_temp_high'], 0), round(day['air_temp_low'], 0)
            forecast_data.append({'day': time.strftime("%a", time.localtime(day['day_start_local'])), 'high': int(d_high), 'low': int(d_low), 'icon_name': day.get('icon', 'cloudy')})
        
        # WIND LOGIC: Convert m/s to km/h
        wind_ms = obs.get('wind_avg', 0)
        wind_kmh = round(wind_ms * 3.6, 1)

        return {
            "temp": round(obs.get('air_temperature', 0), 1),
            "feels_like": int(round(current.get('feels_like', 0))),
            "wind_speed": wind_kmh, # km/h value
            "wind_dir": get_wind_direction(obs.get('wind_direction', 0)),
            "wind_icon": get_beaufort_icon_name(wind_ms * 2.237),
            "pressure": round(obs.get('sea_level_pressure', 0), 0),
            "rain_today": round(obs.get('precip_accum_local_day', 0), 1),
            "humidity": obs.get('relative_humidity', 0),
            "uv": obs.get('uv', 0),
            "uv_icon": get_uv_icon_name(obs.get('uv', 0)),
            "icon_name": current.get('icon', 'clear-day'),
            "summary": current.get('summary', 'Clear'),
            "forecast": forecast_data,
            "hourly_temps": [x['air_temperature'] for x in hourly[:24]]
        }
    except: return None

def create_dashboard(weather, theme_name="inky"):
    theme = STYLES.get(theme_name, STYLES["inky"])
    img = Image.new("RGBA", (WIDTH, HEIGHT), theme['bg_color'])
    if not weather: return img
    try:
        draw = ImageDraw.Draw(img)
        draw._image = img 
        
        font_huge = ImageFont.truetype(FONT_BOLD, 130)
        font_condition = ImageFont.truetype(FONT_BOLD, 35) 
        font_feels = ImageFont.truetype(FONT_BOLD, 25) 
        font_forecast = ImageFont.truetype(FONT_BOLD, 22)
        font_val = ImageFont.truetype(FONT_BOLD, 25) 
        font_label = ImageFont.truetype(FONT_BOLD, 20)
        font_tiny = ImageFont.truetype(FONT_LIGHT, 16)
        TEXT, LINE = theme['text_color'], theme['line_color']

        draw.text((780, 5), time.strftime("Updated: %H:%M"), fill=LINE, font=font_tiny, anchor="rt")
        main_icon = get_icon_image(weather['icon_name'], theme, size=(200, 200))
        img.paste(main_icon, (15, 10), main_icon)
        
        temp_col = get_smooth_color(weather['temp']) if theme['is_desktop'] else TEXT
        draw.text((380, 50), f"{weather['temp']}°", fill=temp_col, font=font_huge, anchor="mt", stroke_width=5)
        draw.text((380, 180), f"Feels Like {weather['feels_like']}°", fill=TEXT, font=font_feels, anchor="mm", stroke_width=1)
        draw.text((380, 215), weather['summary'], fill=TEXT, font=font_condition, anchor="mm", stroke_width=1)

        draw.line([(580, 25), (580, 215)], fill=LINE, width=2)
        stats = [
            (weather['wind_icon'], f"{weather['wind_speed']} km/h {weather['wind_dir']}"), # FORCE UNIT HERE
            ("rain", f"{weather['rain_today']} mm"), 
            ("humidity", f"{weather['humidity']}%"),
            ("barometer", f"{weather['pressure']} hPa"),
            (weather['uv_icon'], f"UV {weather['uv']}")
        ]
        for i, (icon, val) in enumerate(stats):
            y = 20 + (i * 40)
            img_icon = get_icon_image(icon, theme, size=(40, 40))
            img.paste(img_icon, (600, int(y)), img_icon)
            draw.text((780, y + 20), val, fill=TEXT, font=font_val, anchor="rm", stroke_width=1)

        if 'hourly_temps' in weather:
            # MOVED DOWN TO 250
            draw.text((40, 250), "24hr Trend", fill=TEXT, font=font_label, stroke_width=1)
            draw.text((760, 250), f"L:{min(weather['hourly_temps'])}°  H:{max(weather['hourly_temps'])}°", fill=TEXT, font=font_label, anchor="rs", stroke_width=1)
            # GRAPH MOVED TO 280
            draw_graph(draw, weather['hourly_temps'], (40, 280, 760, 310), theme)

        draw.line([(40, 315), (760, 315)], fill=LINE, width=3)
        for i, day in enumerate(weather['forecast']):
            x = 50 + (i * 150)
            draw.text((x + 40, 335), day['day'], fill=TEXT, font=font_forecast, anchor="mm", stroke_width=1)
            icon = get_icon_image(day['icon_name'], theme, size=(100, 100))
            img.paste(icon, (int(x - 10), 345), icon)
            draw.text((x + 40, 455), f"{day['high']}° / {day['low']}°", fill=TEXT, font=font_forecast, anchor="mm", stroke_width=1)

    except Exception as e:
        print(f"Error drawing dashboard: {e}")
        return img
    return img
def main():
    print("Fetching weather...")
    weather = fetch_weather()
    img = create_dashboard(weather, theme_name="inky")
    if INKY_AVAILABLE:
        from inky.auto import auto
        auto().set_image(img).show()
    else:
        img.convert("RGB").save("dashboard-preview.jpg")
if __name__ == "__main__":
    main()
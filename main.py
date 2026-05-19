import importlib.util
import json
import os
import socket
import sys
import time
from functools import lru_cache

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    from inky.auto import auto
    INKY_AVAILABLE = True
except ImportError:
    INKY_AVAILABLE = False

user_home = os.path.expanduser("~")
API_BASE_URL = "https://swd.weatherflow.com/swd/rest"
HTTP_TIMEOUT = 20
LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
DITHER_NONE = getattr(getattr(Image, "Dither", Image), "NONE", 0)

# Pimoroni Inky Impression 7-color palette.
INKY_PALETTE = [
    (0, 0, 0),
    (255, 255, 255),
    (0, 255, 0),
    (0, 0, 255),
    (255, 0, 0),
    (255, 255, 0),
    (255, 128, 0),
]

PRESSURE_FILE = os.path.join(user_home, ".tempest-pressure.json")


def get_base_path():
    try:
        return sys._MEIPASS
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def get_app_path():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_secrets_file(path):
    spec = importlib.util.spec_from_file_location("tempest_user_secrets", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {path}")
    user_secrets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(user_secrets)
    return str(user_secrets.STATION_ID), str(user_secrets.TOKEN)


def load_config():
    station_id = os.environ.get("TEMPEST_STATION_ID")
    token = os.environ.get("TEMPEST_TOKEN")
    if station_id and token:
        print("Loaded configuration from environment")
        return station_id, token
    if station_id or token:
        print("Incomplete environment configuration; falling back to secrets.py")

    for secret_path in [
        os.path.join(user_home, "secrets.py"),
        os.path.join(get_app_path(), "secrets.py"),
    ]:
        if not os.path.exists(secret_path):
            continue
        try:
            config = load_secrets_file(secret_path)
            print(f"Loaded configuration from {secret_path}")
            return config
        except Exception as e:
            print(f"Error loading {secret_path}: {e}")

    print("No secrets found. Using dummy data.")
    return "00000", "dummy"


STATION_ID, TOKEN = load_config()

BASE_DIR = get_base_path()
ASSETS_ROOT = os.path.join(BASE_DIR, "assets")
FONT_LIGHT  = os.path.join(ASSETS_ROOT, "font_light.ttf")
FONT_BOLD   = os.path.join(ASSETS_ROOT, "font_bold.ttf")

WIDTH  = 800
HEIGHT = 480

STYLES = {
    "inky": {
        "asset_folder": "inky",
        "bg_color":    (255, 255, 255, 255),
        "text_color":  (0, 0, 0),
        "line_color":  (0, 0, 0),
        "graph_type":  "vivid_bar",
        "graph_width": 4,
        "is_desktop":  False,
    },
    "desktop": {
        "asset_folder": "desktop",
        "bg_color":    (30, 30, 40, 255),
        "text_color":  (240, 240, 240),
        "line_color":  (80, 80, 90),
        "graph_type":  "gradient_fill",
        "graph_width": 3,
        "is_desktop":  True,
    },
}

COL_COLD = (0,   0,   200)
COL_COOL = (0,   150, 0)
COL_MILD = (255, 200, 0)
COL_WARM = (255, 120, 0)
COL_HOT  = (200, 0,   0)

DT_COLD = (100, 149, 237)
DT_COOL = (144, 238, 144)
DT_MILD = (255, 222, 173)
DT_WARM = (255, 160, 122)
DT_HOT  = (255, 99,  71)


def get_wind_direction(degrees):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(degrees / (360.0 / len(dirs))) % len(dirs)]


def get_beaufort_icon_name(speed_mph):
    for threshold, name in [
        (1, "wind-beaufort-0"),  (4,  "wind-beaufort-1"),
        (8, "wind-beaufort-2"),  (13, "wind-beaufort-3"),
        (19,"wind-beaufort-4"),  (25, "wind-beaufort-5"),
        (32,"wind-beaufort-6"),  (39, "wind-beaufort-7"),
        (47,"wind-beaufort-8"),  (55, "wind-beaufort-9"),
        (64,"wind-beaufort-10"), (73, "wind-beaufort-11"),
    ]:
        if speed_mph < threshold:
            return name
    return "wind-beaufort-12"


def get_uv_icon_name(uv_index):
    uv = int(round(uv_index))
    if uv <= 0:
        return "uv-index"
    if uv > 11:
        uv = 11
    return f"uv-index-{uv}"


def get_temp_color(temp):
    if temp < 5:
        return COL_COLD
    if temp < 15:
        return COL_COOL
    if temp < 22:
        return COL_MILD
    if temp < 28:
        return COL_WARM
    return COL_HOT


def interpolate_rgb(c1, c2, factor):
    return (
        int(c1[0] + (c2[0] - c1[0]) * factor),
        int(c1[1] + (c2[1] - c1[1]) * factor),
        int(c1[2] + (c2[2] - c1[2]) * factor),
    )


def get_smooth_color(temp):
    if temp <= 0:
        return DT_COLD
    if temp >= 30:
        return DT_HOT
    if temp < 10:
        return interpolate_rgb(DT_COLD, DT_COOL, temp / 10)
    if temp < 20:
        return interpolate_rgb(DT_COOL, DT_MILD, (temp - 10) / 10)
    return interpolate_rgb(DT_MILD, DT_HOT, (temp - 20) / 10)


def get_icon_image(icon_name, theme_config, size=(100, 100)):
    icon = _get_icon_image_cached(str(icon_name), theme_config["asset_folder"], int(size[0]), int(size[1]))
    return icon.copy()


@lru_cache(maxsize=256)
def _get_icon_image_cached(icon_name, asset_folder, width, height):
    size = (width, height)
    clean = icon_name.lower().replace(".svg", "").replace(".png", "")

    name_map = {
        "clear-day":                  "clear-day",
        "clear-night":                "clear-night",
        "cloudy":                     "cloudy",
        "partly-cloudy-day":          "partly-cloudy-day",
        "partly-cloudy-night":        "partly-cloudy-night",
        "foggy":                      "fog",
        "wind":                       "wind",
        "rainy":                      "rain",
        "possibly-rainy-day":         "partly-cloudy-day-rain",
        "possibly-rainy-night":       "partly-cloudy-night-rain",
        "snow":                       "snow",
        "possibly-snow-day":          "partly-cloudy-day-snow",
        "possibly-snow-night":        "partly-cloudy-night-snow",
        "sleet":                      "sleet",
        "wintry-mix":                 "sleet",
        "possibly-sleet-day":         "partly-cloudy-day-sleet",
        "possibly-sleet-night":       "partly-cloudy-night-sleet",
        "thunderstorm":               "thunderstorms",
        "possibly-thunderstorm-day":  "thunderstorms-day",
        "possibly-thunderstorm-night": "thunderstorms-night",
    }
    suffix = name_map.get(clean, clean)
    if suffix == clean:
        if "thunder" in clean:
            suffix = "thunderstorms"
        elif "rain" in clean:
            suffix = "rain"
        elif "snow" in clean:
            suffix = "snow"
        elif "fog" in clean:
            suffix = "fog"
        elif "sleet" in clean or "mix" in clean:
            suffix = "sleet"

    candidates = [
        f"weather-icons_{suffix}.png",
        f"weather-icons_{clean}.png",
        f"{suffix}.png",
    ]
    theme_folder = os.path.join(ASSETS_ROOT, asset_folder)
    for fn in candidates:
        p = os.path.join(theme_folder, fn)
        if os.path.exists(p):
            return Image.open(p).convert("RGBA").resize(size, LANCZOS)
    for fn in candidates:
        p = os.path.join(ASSETS_ROOT, fn)
        if os.path.exists(p):
            return Image.open(p).convert("RGBA").resize(size, LANCZOS)

    print(f"Warning: missing icon '{clean}' (tried: {candidates})")
    return Image.new("RGBA", size, (0, 0, 0, 0))


@lru_cache(maxsize=32)
def get_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError as e:
        print(f"Font load error for {path}: {e}")
        return ImageFont.load_default()


def text_width(draw, text, font, stroke_width=0):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return bbox[2] - bbox[0]


def text_height(draw, text, font, stroke_width=0):
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return bbox[3] - bbox[1]


def trim_to_width(draw, text, font, max_width, stroke_width=0):
    text = str(text).strip()
    if text_width(draw, text, font, stroke_width) <= max_width:
        return text

    suffix = "..."
    for end in range(len(text), 0, -1):
        candidate = text[:end].rstrip() + suffix
        if text_width(draw, candidate, font, stroke_width) <= max_width:
            return candidate
    return suffix


def wrap_text(draw, text, font, max_width, stroke_width=0):
    words = str(text).split()
    if not words:
        return [""]

    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if text_width(draw, candidate, font, stroke_width) <= max_width:
            current = candidate
        else:
            lines.append(trim_to_width(draw, current, font, max_width, stroke_width))
            current = word
    lines.append(trim_to_width(draw, current, font, max_width, stroke_width))
    return lines


def fit_wrapped_text(draw, text, font_path, start_size, min_size, max_width, max_lines, stroke_width=0):
    for size in range(start_size, min_size - 1, -1):
        font = get_font(font_path, size)
        lines = wrap_text(draw, text, font, max_width, stroke_width)
        if len(lines) <= max_lines:
            return lines, font

    font = get_font(font_path, min_size)
    lines = wrap_text(draw, text, font, max_width, stroke_width)
    if len(lines) > max_lines:
        remaining = " ".join(lines[max_lines - 1:])
        lines = lines[:max_lines - 1] + [
            trim_to_width(draw, remaining, font, max_width, stroke_width)
        ]
    return lines, font


def draw_centered_lines(draw, center, lines, font, fill, stroke_width=0, spacing=2):
    x, center_y = center
    heights = [text_height(draw, line, font, stroke_width) for line in lines]
    total_height = sum(heights) + max(0, len(lines) - 1) * spacing
    y = center_y - total_height / 2
    for line, height in zip(lines, heights):
        draw.text((x, y), line, fill=fill, font=font, anchor="mt", stroke_width=stroke_width)
        y += height + spacing


@lru_cache(maxsize=1)
def get_inky_palette_image():
    palette_img = Image.new("P", (1, 1))
    palette = []
    for color in INKY_PALETTE:
        palette.extend(color)
    palette.extend([255, 255, 255] * (256 - len(INKY_PALETTE)))
    palette_img.putpalette(palette)
    return palette_img


def quantize_for_inky(img):
    return img.convert("RGB").quantize(
        palette=get_inky_palette_image(),
        dither=DITHER_NONE,
    ).convert("RGB")


def interpolate(a, b, t):
    return a + (b - a) * t


def get_spline_point(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    v0 = (p2 - p0) * 0.5
    v1 = (p3 - p1) * 0.5
    return (2*p1 - 2*p2 + v0 + v1)*t3 + (-3*p1 + 3*p2 - 2*v0 - v1)*t2 + v0*t + p1


def draw_graph(draw, bg_img, data_points, box, theme_config):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if len(data_points) < 2:
        return

    mn, mx  = min(data_points), max(data_points)
    rng     = max(mx - mn, 5)
    pad     = rng * 0.2
    v_min   = mn - pad / 2
    v_max   = mx + pad / 2
    v_range = v_max - v_min
    step    = w / (len(data_points) - 1)

    if theme_config["graph_type"] == "vivid_bar":
        for px in range(0, int(w), 2):
            idx = int(px / step)
            if idx >= len(data_points) - 1:
                break
            val = interpolate(data_points[idx], data_points[idx + 1], (px % step) / step)
            py  = max(y1, min(y2, y2 - ((val - v_min) / v_range * h)))
            draw.line([(x1+px, py), (x1+px, y2)], fill=get_temp_color(val), width=1)
        pts = []
        for i, val in enumerate(data_points):
            pts.append((x1 + i*step, max(y1, min(y2, y2 - ((val - v_min) / v_range * h)))))
        draw.line(pts, fill=theme_config["text_color"], width=theme_config["graph_width"])
    elif theme_config["graph_type"] == "clean_line":
        pts = []
        for i, val in enumerate(data_points):
            px = x1 + i * step
            py = max(y1 + 2, min(y2 - 2, y2 - ((val - v_min) / v_range * h)))
            pts.append((int(px), int(py)))
        fill_pts = [(x1, y2)] + pts + [(x2, y2)]
        draw.polygon(fill_pts, fill=(220, 220, 220))
        if len(pts) > 1:
            draw.line(pts, fill=theme_config["text_color"], width=theme_config["graph_width"])
    else:
        draw_graph_supersampled(bg_img, data_points, box, theme_config, v_min, v_max, v_range)


def draw_graph_supersampled(bg_img, data_points, box, theme_config, v_min, v_max, v_range):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    S  = 4
    sw, sh = int(w*S), int(h*S)
    si  = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    sd  = ImageDraw.Draw(si)
    raw = []
    for i, val in enumerate(data_points):
        px = i * (sw / (len(data_points)-1)) + 8
        py = sh - ((val - v_min) / v_range * sh)
        raw.append((max(0, min(sw, px)), max(0, min(sh, py))))
    sp  = [raw[0]] + raw + [raw[-1]]
    curve = []
    for i in range(len(raw)-1):
        p0, p1, p2, p3 = sp[i], sp[i+1], sp[i+2], sp[i+3]
        for s in range(40):
            t  = s / 40
            sx = get_spline_point(p0[0], p1[0], p2[0], p3[0], t)
            sy = get_spline_point(p0[1], p1[1], p2[1], p3[1], t)
            sx = max(0, min(sw, sx))
            curve.append((sx, sy, ((sh-sy)/sh)*v_range + v_min))
    curve.append((raw[-1][0], raw[-1][1], data_points[-1]))
    for sx, sy, val in curve:
        c = get_smooth_color(val)
        sd.line([(sx, sy), (sx, sh)], fill=(c[0], c[1], c[2], 50), width=int(2*S))
    for i in range(len(curve)-1):
        a, b = curve[i], curve[i+1]
        sd.line([(a[0], a[1]), (b[0], b[1])], fill=get_smooth_color(a[2]), width=int(3*S))
    resized = si.resize((int(w), int(h)), LANCZOS)
    bg_img.paste(resized, (x1, y1), resized)


# ── Reliability helpers ────────────────────────────────────────────────────────

def wait_for_network(timeout=120):
    """Block until swd.weatherflow.com is reachable or timeout expires."""
    print(f"Waiting for network (up to {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("swd.weatherflow.com", 443), timeout=5):
                print("Network ready.")
                return True
        except OSError:
            time.sleep(5)
    print("Network not available — attempting fetch anyway.")
    return False


def get_pressure_trend(pressure):
    """Compare current pressure to last saved value; persist for next run."""
    try:
        prev = None
        if os.path.exists(PRESSURE_FILE):
            with open(PRESSURE_FILE) as f:
                prev = json.load(f).get("pressure")
        trend = "Steady"
        if prev is not None:
            diff = pressure - prev
            if diff > 1.0:
                trend = "Rising"
            if diff < -1.0:
                trend = "Falling"
        with open(PRESSURE_FILE, "w") as f:
            json.dump({"pressure": pressure, "time": time.time()}, f)
        return trend
    except Exception as e:
        print(f"Pressure trend error: {e}")
        return "Steady"


# ── API fetch ─────────────────────────────────────────────────────────────────

def fetch_weather(retries=3):
    """Fetch from the Tempest API with exponential-backoff retry."""
    last_err = None
    session = requests.Session()
    for attempt in range(retries):
        if attempt > 0:
            delay = 10 * (2 ** (attempt - 1))
            print(f"Retry {attempt}/{retries-1} in {delay}s...")
            time.sleep(delay)
        try:
            obs_response = session.get(
                f"{API_BASE_URL}/observations/station/{STATION_ID}",
                params={"token": TOKEN},
                timeout=HTTP_TIMEOUT,
            )
            obs_response.raise_for_status()
            r_obs = obs_response.json()
            if "obs" not in r_obs or not r_obs["obs"]:
                raise ValueError("No observations in API response")
            obs = r_obs["obs"][0]

            forecast_response = session.get(
                f"{API_BASE_URL}/better_forecast",
                params={"station_id": STATION_ID, "token": TOKEN},
                timeout=HTTP_TIMEOUT,
            )
            forecast_response.raise_for_status()
            r_for = forecast_response.json()
            if "forecast" not in r_for:
                raise ValueError("No forecast in API response")
            current = r_for.get("current_conditions", {})
            daily   = r_for["forecast"].get("daily", [])
            hourly  = r_for["forecast"].get("hourly", [])
            if not daily:
                raise ValueError("Empty daily forecast")

            sunrise_epoch = daily[0].get("sunrise") or 0
            sunset_epoch  = daily[0].get("sunset")  or 0
            sunrise_str   = time.strftime("%H:%M", time.localtime(sunrise_epoch)) if sunrise_epoch else "--:--"
            sunset_str    = time.strftime("%H:%M", time.localtime(sunset_epoch))  if sunset_epoch  else "--:--"

            forecast_data = [
                {
                    "day":        time.strftime("%a", time.localtime(day.get("day_start_local") or 0)),
                    "high":       int(round(day.get("air_temp_high") or 0)),
                    "low":        int(round(day.get("air_temp_low")  or 0)),
                    "icon_name":  day.get("icon", "cloudy"),
                    "precip_prob": day.get("precip_probability") or 0,
                }
                for day in daily[:5]
            ]

            wind_ms      = obs.get("wind_avg")  or 0
            wind_gust_ms = obs.get("wind_gust") or 0
            pressure     = round(obs.get("sea_level_pressure") or 0, 1)

            hourly_temps  = [x["air_temperature"] for x in hourly[:120] if x.get("air_temperature") is not None]
            hourly_precip = [x.get("precip_probability") or 0 for x in hourly[:24]]

            feels_like_raw = (
                current.get("feels_like")
                or obs.get("feels_like")
                or obs.get("air_temperature")
                or 0
            )

            return {
                "temp":           round(obs.get("air_temperature") or 0, 1),
                "feels_like":     int(round(feels_like_raw)),
                "wind_speed":     round(wind_ms * 3.6, 1),
                "wind_gust":      round(wind_gust_ms * 3.6, 1),
                "wind_dir":       get_wind_direction(obs.get("wind_direction") or 0),
                "wind_icon":      get_beaufort_icon_name(wind_ms * 2.237),
                "pressure":       pressure,
                "pressure_trend": get_pressure_trend(pressure),
                "rain_today":     round(obs.get("precip_accum_local_day")       or 0, 1),
                "rain_yesterday": round(obs.get("precip_accum_local_yesterday") or 0, 1),
                "humidity":       obs.get("relative_humidity") or 0,
                "dew_point":      round(obs.get("dew_point") or 0, 1),
                "uv":             obs.get("uv") or 0,
                "uv_icon":        get_uv_icon_name(obs.get("uv") or 0),
                "icon_name":      current.get("icon", "clear-day"),
                "summary":        current.get("conditions", current.get("summary", "Clear")),
                "forecast":       forecast_data,
                "hourly_temps":   hourly_temps,
                "hourly_precip":  hourly_precip,
                "sunrise":        sunrise_str,
                "sunset":         sunset_str,
                "lightning_count":obs.get("lightning_strike_count") or 0,
            }
        except Exception as e:
            last_err = e
            print(f"Fetch attempt {attempt+1}/{retries} failed: {e}")

    print(f"All {retries} fetch attempts failed. Last error: {last_err}")
    return None


# ── Dashboard rendering ────────────────────────────────────────────────────────

def create_dashboard(weather, theme_name="inky"):
    theme = STYLES.get(theme_name, STYLES["inky"])
    img = Image.new("RGBA", (WIDTH, HEIGHT), theme["bg_color"])
    draw = ImageDraw.Draw(img)

    if not weather:
        print("Drawing Error Screen...")
        font_err = get_font(FONT_BOLD, 40)
        draw.text(
            (WIDTH//2, HEIGHT//2),
            "DATA FETCH ERROR\nCheck Console Logs",
            fill=theme["text_color"],
            font=font_err,
            anchor="mm",
            align="center",
        )
        return img

    try:
        font_huge      = get_font(FONT_BOLD,  130)
        font_feels     = get_font(FONT_BOLD,   25)
        font_forecast  = get_font(FONT_BOLD,   22)
        font_val       = get_font(FONT_BOLD,   25)
        font_label     = get_font(FONT_BOLD,   20)
        font_tiny      = get_font(FONT_LIGHT,  16)
        TEXT, LINE = theme["text_color"], theme["line_color"]

        draw.text((780, 5), time.strftime("Updated: %H:%M"), fill=LINE, font=font_tiny, anchor="rt")

        main_icon = get_icon_image(weather["icon_name"], theme, size=(200, 200))
        img.paste(main_icon, (15, 10), main_icon)

        temp_col = get_smooth_color(weather["temp"]) if theme["is_desktop"] else TEXT
        draw.text((380, 50),  f"{weather['temp']}°",             fill=temp_col, font=font_huge,      anchor="mt", stroke_width=5)
        draw.text((380, 180), f"Feels Like {weather['feels_like']}°", fill=TEXT, font=font_feels, anchor="mm", stroke_width=1)
        summary_lines, font_condition = fit_wrapped_text(
            draw,
            weather["summary"],
            FONT_BOLD,
            start_size=27,
            min_size=20,
            max_width=340,
            max_lines=2,
            stroke_width=1,
        )
        draw_centered_lines(
            draw,
            (390, 222),
            summary_lines,
            font_condition,
            TEXT,
            stroke_width=1,
            spacing=1,
        )

        draw.line([(580, 25), (580, 215)], fill=LINE, width=2)
        stats = [
            (weather["wind_icon"], f"{weather['wind_speed']} km/h {weather['wind_dir']}"),
            ("rain",               f"{weather['rain_today']} mm"),
            ("humidity",           f"{weather['humidity']}%"),
            ("barometer",          f"{weather['pressure']} hPa"),
            (weather["uv_icon"],   f"UV {weather['uv']}"),
        ]
        for i, (icon, val) in enumerate(stats):
            y = 20 + (i * 40)
            img_icon = get_icon_image(icon, theme, size=(40, 40))
            img.paste(img_icon, (600, int(y)), img_icon)
            draw.text((780, y + 20), val, fill=TEXT, font=font_val, anchor="rm", stroke_width=1)

        if weather.get("hourly_temps"):
            t_min = min(weather["hourly_temps"])
            t_max = max(weather["hourly_temps"])
            draw.text((40, 250), "24hr Trend", fill=TEXT, font=font_label, stroke_width=1)
            if not theme["is_desktop"]:
                t_str = f"L:{int(round(t_min))}°  H:{int(round(t_max))}°"
            else:
                t_str = f"L:{t_min}°  H:{t_max}°"
            draw.text((760, 250), t_str, fill=TEXT, font=font_label, anchor="rs", stroke_width=1)
            draw_graph(draw, img, weather["hourly_temps"], (40, 280, 760, 310), theme)

        draw.line([(40, 315), (760, 315)], fill=LINE, width=3)
        for i, day in enumerate(weather["forecast"]):
            x = 50 + (i * 150)
            draw.text((x + 40, 335), day["day"],                        fill=TEXT, font=font_forecast, anchor="mm", stroke_width=1)
            fc_icon = get_icon_image(day["icon_name"], theme, size=(100, 100))
            img.paste(fc_icon, (int(x - 10), 345), fc_icon)
            draw.text((x + 40, 455), f"{day['high']}° / {day['low']}°", fill=TEXT, font=font_forecast, anchor="mm", stroke_width=1)

    except Exception as e:
        print(f"Error drawing dashboard: {e}")
        img = Image.new("RGBA", (WIDTH, HEIGHT), theme["bg_color"])
        draw = ImageDraw.Draw(img)
        font_err = get_font(FONT_BOLD, 34)
        draw.text(
            (WIDTH//2, HEIGHT//2),
            "RENDER ERROR\nCheck Console Logs",
            fill=theme["text_color"],
            font=font_err,
            anchor="mm",
            align="center",
        )

    return img


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if INKY_AVAILABLE:
        wait_for_network(timeout=120)

    print("Fetching weather...")
    weather = fetch_weather()
    img     = create_dashboard(weather, theme_name="inky")

    if INKY_AVAILABLE:
        try:
            display = auto()
            display.set_image(quantize_for_inky(img))
            display.show()
            print("Display updated.")
        except Exception as e:
            print(f"Display error: {e}")
            raise   # let systemd record the failure
    else:
        quantize_for_inky(img).save("dashboard-preview.jpg")
        print("Saved dashboard-preview.jpg")


if __name__ == "__main__":
    main()

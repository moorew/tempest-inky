import sys
import time
import os
import json
import socket
import requests
import importlib.util
from PIL import Image, ImageDraw, ImageFont

try:
    from inky.auto import auto
    INKY_AVAILABLE = True
except ImportError:
    INKY_AVAILABLE = False

user_home = os.path.expanduser("~")
secret_path = os.path.join(user_home, "secrets.py")
STATION_ID = "00000"
TOKEN = "dummy"

if os.path.exists(secret_path):
    try:
        spec = importlib.util.spec_from_file_location("secrets", secret_path)
        user_secrets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(user_secrets)
        STATION_ID = user_secrets.STATION_ID
        TOKEN = user_secrets.TOKEN
        print(f"Loaded configuration from {secret_path}")
    except Exception as e:
        print(f"Error loading external secrets: {e}")
else:
    try:
        from secrets import STATION_ID, TOKEN
        print("Loaded configuration from local folder")
    except ImportError:
        print("No secrets found. Using dummy data.")

URL_OBS      = f"https://swd.weatherflow.com/swd/rest/observations/station/{STATION_ID}?token={TOKEN}"
URL_FORECAST = f"https://swd.weatherflow.com/swd/rest/better_forecast?station_id={STATION_ID}&token={TOKEN}"

PRESSURE_FILE = os.path.join(user_home, ".tempest-pressure.json")


def get_base_path():
    try:
        return sys._MEIPASS
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


BASE_DIR   = get_base_path()
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
        "graph_type":  "clean_line",
        "graph_width": 3,
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
    if uv <= 0:  return "uv-index"
    if uv > 11:  uv = 11
    return f"uv-index-{uv}"


def get_temp_color(temp, theme_mode="inky"):
    if temp < 5:  return COL_COLD
    if temp < 15: return COL_COOL
    if temp < 22: return COL_MILD
    if temp < 28: return COL_WARM
    return COL_HOT


def interpolate_rgb(c1, c2, factor):
    return (
        int(c1[0] + (c2[0] - c1[0]) * factor),
        int(c1[1] + (c2[1] - c1[1]) * factor),
        int(c1[2] + (c2[2] - c1[2]) * factor),
    )


def get_smooth_color(temp):
    if temp <= 0:  return DT_COLD
    if temp >= 30: return DT_HOT
    if temp < 10:  return interpolate_rgb(DT_COLD, DT_COOL, temp / 10)
    if temp < 20:  return interpolate_rgb(DT_COOL, DT_MILD, (temp - 10) / 10)
    return interpolate_rgb(DT_MILD, DT_HOT, (temp - 20) / 10)


def get_icon_image(icon_name, theme_config, size=(100, 100)):
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
        "possibly-thunderstorm-night":"thunderstorms-night",
    }
    suffix = name_map.get(clean, clean)
    if suffix == clean:
        if "thunder" in clean:          suffix = "thunderstorms"
        elif "rain" in clean:           suffix = "rain"
        elif "snow" in clean:           suffix = "snow"
        elif "fog" in clean:            suffix = "fog"
        elif "sleet" in clean or "mix" in clean: suffix = "sleet"

    candidates = [
        f"weather-icons_{suffix}.png",
        f"weather-icons_{clean}.png",
        f"{suffix}.png",
    ]
    theme_folder = os.path.join(ASSETS_ROOT, theme_config["asset_folder"])
    for fn in candidates:
        p = os.path.join(theme_folder, fn)
        if os.path.exists(p):
            return Image.open(p).convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    for fn in candidates:
        p = os.path.join(ASSETS_ROOT, fn)
        if os.path.exists(p):
            return Image.open(p).convert("RGBA").resize(size, Image.Resampling.LANCZOS)

    print(f"Warning: missing icon '{clean}' (tried: {candidates})")
    return Image.new("RGBA", size, (0, 0, 0, 0))


def interpolate(a, b, t):
    return a + (b - a) * t


def get_spline_point(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    v0 = (p2 - p0) * 0.5
    v1 = (p3 - p1) * 0.5
    return (2*p1 - 2*p2 + v0 + v1)*t3 + (-3*p1 + 3*p2 - 2*v0 - v1)*t2 + v0*t + p1


def draw_graph(draw, data_points, box, theme_config):
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
        draw_graph_supersampled(draw._image, data_points, box, theme_config, v_min, v_max, v_range)


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
    resized = si.resize((int(w), int(h)), Image.Resampling.LANCZOS)
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
            if diff >  1.0: trend = "Rising"
            if diff < -1.0: trend = "Falling"
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
    for attempt in range(retries):
        if attempt > 0:
            delay = 10 * (2 ** (attempt - 1))
            print(f"Retry {attempt}/{retries-1} in {delay}s...")
            time.sleep(delay)
        try:
            r_obs = requests.get(URL_OBS, timeout=20).json()
            if "obs" not in r_obs or not r_obs["obs"]:
                raise ValueError("No observations in API response")
            obs = r_obs["obs"][0]

            r_for = requests.get(URL_FORECAST, timeout=20).json()
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

            forecast_data = []
            for day in daily[:5]:
                forecast_data.append({
                    "day":        time.strftime("%a", time.localtime(day.get("day_start_local") or 0)),
                    "high":       int(round(day.get("air_temp_high") or 0)),
                    "low":        int(round(day.get("air_temp_low")  or 0)),
                    "icon_name":  day.get("icon", "cloudy"),
                    "precip_prob":day.get("precip_probability") or 0,
                })

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
    img   = Image.new("RGBA", (WIDTH, HEIGHT), theme["bg_color"])
    draw  = ImageDraw.Draw(img)
    draw._image = img  # needed by draw_graph_supersampled

    if not weather:
        print("Drawing error screen...")
        try:
            fe = ImageFont.truetype(FONT_BOLD, 36)
            draw.text((WIDTH//2, HEIGHT//2),
                      "DATA FETCH ERROR\njournalctl -u tempest-inky",
                      fill=theme["text_color"], font=fe, anchor="mm", align="center")
        except Exception:
            pass
        return img

    try:
        font_temp = ImageFont.truetype(FONT_BOLD,  96)
        font_feel = ImageFont.truetype(FONT_BOLD,  36)
        font_summ = ImageFont.truetype(FONT_BOLD,  22)
        font_sv   = ImageFont.truetype(FONT_BOLD,  26)
        font_ss   = ImageFont.truetype(FONT_LIGHT, 21)
        font_fcd  = ImageFont.truetype(FONT_BOLD,  20)
        font_fct  = ImageFont.truetype(FONT_BOLD,  19)
        font_tiny = ImageFont.truetype(FONT_LIGHT, 15)

        TEXT = theme["text_color"]
        LINE = theme["line_color"]
        BLUE = (0, 80, 200) if not theme["is_desktop"] else (100, 160, 255)

        def temp_col(t):
            if theme["is_desktop"]:
                return get_smooth_color(t)
            return TEXT  # solid black on e-ink — no dithering

        # ══ ZONE 1  y: 0–170   Current conditions ══════════════════════════════

        main_icon = get_icon_image(weather["icon_name"], theme, size=(120, 120))
        img.paste(main_icon, (12, 14), main_icon)

        draw.text((400, 6), f"{weather['temp']}°",
                  fill=temp_col(weather["temp"]), font=font_temp, anchor="mt")

        draw.text((400, 108), f"Feels Like {weather['feels_like']}°",
                  fill=TEXT, font=font_feel, anchor="mt")

        draw.text((400, 146), weather["summary"],
                  fill=LINE, font=font_summ, anchor="mt")

        draw.text((790,  8), time.strftime("Updated %H:%M"), fill=LINE, font=font_tiny, anchor="rt")
        draw.text((790, 26), f"Rise  {weather['sunrise']}",   fill=TEXT, font=font_tiny, anchor="rt")
        draw.text((790, 44), f"Set   {weather['sunset']}",    fill=TEXT, font=font_tiny, anchor="rt")
        uv_icon = get_icon_image(weather["uv_icon"], theme, size=(20, 20))
        img.paste(uv_icon, (766, 62), uv_icon)
        draw.text((790, 73), f"UV {weather['uv']}",           fill=TEXT, font=font_tiny, anchor="rt")

        draw.line([(8, 172), (792, 172)], fill=LINE, width=1)

        # ══ ZONE 2  y: 174–264   Current details ═══════════════════════════════

        COLS   = [12, 212, 412, 612]
        ICON_Y = 180
        VAL_Y  = 203
        SUB_Y  = 228
        ICO_SZ = 40
        TX_OFF = 48

        ico = get_icon_image(weather["wind_icon"], theme, size=(ICO_SZ, ICO_SZ))
        img.paste(ico, (COLS[0], ICON_Y), ico)
        draw.text((COLS[0]+TX_OFF, VAL_Y), f"{weather['wind_speed']} km/h {weather['wind_dir']}",
                  fill=TEXT, font=font_sv, anchor="lm")
        draw.text((COLS[0]+TX_OFF, SUB_Y), f"Gust {weather['wind_gust']} km/h",
                  fill=LINE, font=font_ss, anchor="lm")

        ico = get_icon_image("rain", theme, size=(ICO_SZ, ICO_SZ))
        img.paste(ico, (COLS[1], ICON_Y), ico)
        draw.text((COLS[1]+TX_OFF, VAL_Y), f"{weather['rain_today']} mm today",
                  fill=TEXT, font=font_sv, anchor="lm")
        draw.text((COLS[1]+TX_OFF, SUB_Y), f"Yest {weather['rain_yesterday']} mm",
                  fill=LINE, font=font_ss, anchor="lm")

        ico = get_icon_image("humidity", theme, size=(ICO_SZ, ICO_SZ))
        img.paste(ico, (COLS[2], ICON_Y), ico)
        draw.text((COLS[2]+TX_OFF, VAL_Y), f"{weather['humidity']}%  Dew {weather['dew_point']}°",
                  fill=TEXT, font=font_sv, anchor="lm")
        if weather.get("lightning_count", 0) > 0:
            draw.text((COLS[2]+TX_OFF, SUB_Y), f"Lightning: {weather['lightning_count']}",
                      fill=(200, 80, 0), font=font_ss, anchor="lm")

        ico = get_icon_image("barometer", theme, size=(ICO_SZ, ICO_SZ))
        img.paste(ico, (COLS[3], ICON_Y), ico)
        draw.text((COLS[3]+TX_OFF, VAL_Y), f"{weather['pressure']} hPa",
                  fill=TEXT, font=font_sv, anchor="lm")
        draw.text((COLS[3]+TX_OFF, SUB_Y), weather.get("pressure_trend", "Steady"),
                  fill=LINE, font=font_ss, anchor="lm")

        draw.line([(8, 266), (792, 266)], fill=LINE, width=1)

        # ══ ZONE 3  y: 268–358   5-Day temperature trend ══════════════════════

        if weather.get("hourly_temps"):
            t_min = min(weather["hourly_temps"])
            t_max = max(weather["hourly_temps"])
            draw.text(( 12, 270), "5-Day Trend",
                      fill=TEXT, font=font_ss)
            draw.text((790, 270), f"L {int(round(t_min))}°   H {int(round(t_max))}°",
                      fill=TEXT, font=font_ss, anchor="rs")
            draw_graph(draw, weather["hourly_temps"], (12, 292, 790, 356), theme)

        draw.line([(8, 360), (792, 360)], fill=LINE, width=1)

        # ══ ZONE 4  y: 362–478   5-day forecast ════════════════════════════════

        for i, day in enumerate(weather["forecast"]):
            cx = 80 + i * 160

            draw.text((cx, 363), day["day"].upper(),
                      fill=TEXT, font=font_fcd, anchor="mt")

            fc_icon = get_icon_image(day["icon_name"], theme, size=(62, 62))
            img.paste(fc_icon, (cx - 31, 383), fc_icon)

            draw.text((cx, 448), f"{day['high']}° / {day['low']}°",
                      fill=TEXT, font=font_fct, anchor="mt")

            prob = day.get("precip_prob", 0)
            if prob >= 10:
                draw.text((cx, 465), f"{prob}%",
                          fill=BLUE, font=font_tiny, anchor="mt")

    except Exception as e:
        print(f"Error drawing dashboard: {e}")

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
            display.set_image(img.convert("RGB"))
            display.show()
            print("Display updated.")
        except Exception as e:
            print(f"Display error: {e}")
            raise   # let systemd record the failure
    else:
        img.convert("RGB").save("dashboard-preview.jpg")
        print("Saved dashboard-preview.jpg")


if __name__ == "__main__":
    main()

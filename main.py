"""Tempest Inky dashboard — layout 3A.

Renders an 800x480 panel for the Pimoroni Inky Impression 7.3" (7 colour)
from a WeatherFlow Tempest station.

The panel is read from across a room, so type is budgeted by viewing
distance: cap height ~= distance / 200. Nothing here is under 16 px, no
numeric value is under 24 px, and no icon glyph is under 26 px.

Colour is a categorical channel only: hue carries condition, height
carries quantity. All type is black, or white on blue.
"""

import argparse
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
DITHER_NONE = getattr(getattr(Image, "Dither", Image), "NONE", 0)

STATE_FILE = os.path.join(user_home, ".tempest-last.json")
CYCLE_FILE = os.path.join(user_home, ".tempest-cycle.json")

# Thresholds for the concern band.
GUST_THRESHOLD_KPH = 40.0
BATTERY_LOW_VOLTS = 2.40
STATION_SILENT_SECONDS = 3600
LIGHTNING_NEAR_KM = 15
LIGHTNING_RECENT_SECONDS = 1800
PRECIP_LIKELY_PCT = 50

# Adaptive refresh (minutes). The systemd timer ticks every 5 minutes and
# main() decides whether this tick is due, because the service runs as the
# app user and cannot rewrite a unit file in /etc.
REFRESH_NIGHT_MIN = 30
REFRESH_NORMAL_MIN = 15
REFRESH_ACTIVE_MIN = 10
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6


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
FONT_DISPLAY = os.path.join(ASSETS_ROOT, "ArchivoBlack-Regular.ttf")
FONT_TEXT = os.path.join(ASSETS_ROOT, "AtkinsonHyperlegible-Regular.ttf")
FONT_BOLD = os.path.join(ASSETS_ROOT, "AtkinsonHyperlegible-Bold.ttf")
FONT_ICON = os.path.join(ASSETS_ROOT, "weathericons.ttf")

WIDTH = 800
HEIGHT = 480

# ── Design tokens ─────────────────────────────────────────────────────────────

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
INK_CLEAR = (255, 255, 0)
INK_RAIN = (0, 255, 0)
INK_SNOW = (0, 0, 255)
INK_STORM = (255, 0, 0)
INK_HEAT = (255, 128, 0)

# Pimoroni Inky Impression 7-color palette.
INKY_PALETTE = [
    BLACK, WHITE, INK_RAIN, INK_SNOW, INK_STORM, INK_CLEAR, INK_HEAT,
]

# Cloud is not an ink: it renders white with a black keyline, so a dull
# week spends almost no colour.
CATEGORY_INK = {
    "clear": INK_CLEAR,
    "cloud": WHITE,
    "rain": INK_RAIN,
    "snow": INK_SNOW,
    "storm": INK_STORM,
    "hot": INK_HEAT,
}

DASH = "—"          # em dash, the "no data" mark
MIDDOT = "·"
ARROW = "→"         # present in Archivo Black; Atkinson has no arrows at all

# Verified against assets/weathericons.ttf — every one renders, no tofu.
WI = {
    "clear-day": "",
    "clear-night": "",
    "partly-cloudy-day": "",
    "partly-cloudy-night": "",
    "cloudy": "",
    "overcast": "",
    "rain": "",
    "rain-night": "",
    "day-rain": "",
    "snow": "",
    "snow-night": "",
    "day-snow": "",
    "sleet": "",
    "thunderstorm": "",
    "thunderstorm-night": "",
    "fog": "",
    "fog-night": "",
    "wind": "",
    "humidity": "",
    "barometer": "",
    "sunrise": "",
    "sunset": "",
    "snowflake": "",
    "raindrop": "",
    "lightning": "",
    "na": "",
}

# ── Region geometry ───────────────────────────────────────────────────────────
# Heights are exact and sum to 480. Geometry must stay byte-identical between
# refreshes and between concern states, or the panel ghosts.

BAND_H = 58
MID_H = 218
SLOT_H = 92
TENDAY_H = 112

BAND_Y0, BAND_Y1 = 0, BAND_H                      # 0   - 58
MID_Y0, MID_Y1 = BAND_Y1, BAND_Y1 + MID_H         # 58  - 276
SLOT_Y0, SLOT_Y1 = MID_Y1, MID_Y1 + SLOT_H        # 276 - 368
TEN_Y0, TEN_Y1 = SLOT_Y1, SLOT_Y1 + TENDAY_H      # 368 - 480

SPINE_W = 296
RULE_DIVIDER = 3
RULE_SUB = 2

assert BAND_H + MID_H + SLOT_H + TENDAY_H == HEIGHT


# ── Small helpers ─────────────────────────────────────────────────────────────

def _num(value):
    """Return value only if it is a real number.

    The old code used `obs.get("x") or 0`, which treats a legitimate 0 as
    missing. A genuine 0 C feels-like silently became the air temperature.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


@lru_cache(maxsize=64)
def get_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError as e:
        print(f"Font load error for {path}: {e}")
        return ImageFont.load_default()


def display(size):
    return get_font(FONT_DISPLAY, size)


def text(size):
    return get_font(FONT_TEXT, size)


def bold(size):
    return get_font(FONT_BOLD, size)


def icon(size):
    return get_font(FONT_ICON, size)


def type_on(ink):
    """Black on every ink except blue, which is the only dark one.

    Black on blue measures ~2.4:1 and fails; white on blue is ~8.6:1.
    """
    return WHITE if ink == INK_SNOW else BLACK


def fmt_temp(value, decimals=0):
    if value is None:
        return DASH
    return f"{value:.{decimals}f}°"


def fmt_num(value, decimals=0, suffix=""):
    if value is None:
        return DASH
    return f"{value:.{decimals}f}{suffix}"


def hhmm(epoch):
    if not epoch:
        return DASH
    return time.strftime("%H:%M", time.localtime(epoch))


def glyph_advance(draw, char, size):
    """Width actually consumed by an icon glyph.

    Weather Icons glyphs vary from 18 px to 50 px wide at the same point
    size, so the text beside one cannot sit at a fixed offset.
    """
    font = icon(size)
    box = draw.textbbox((0, 0), char, font=font, anchor="lt")
    return max(draw.textlength(char, font=font), box[2])


def draw_trend_arrow(draw, x, y, size, trend, fill=BLACK):
    """Pressure trend as a drawn triangle.

    None of the shipped faces contain U+2197/U+2198, so an arrow character
    renders as .notdef. A solid triangle needs no font and stays crisp
    through the 7-colour quantiser.
    """
    half = size / 2
    if trend == "rising":
        points = [(x + half, y - half), (x + size, y + half), (x, y + half)]
    elif trend == "falling":
        points = [(x, y - half), (x + size, y - half), (x + half, y + half)]
    else:
        points = [(x, y - half), (x + size, y), (x, y + half)]
    draw.polygon(points, fill=fill)


def knockout_text(draw, xy, content, font, fill=BLACK, anchor="mm", pad=3):
    """Draw text over an opaque white box.

    On a two-ink panel this is the only halo technique that works, so any
    number that can cross a rule knocks white out of it first.
    """
    box = draw.textbbox(xy, content, font=font, anchor=anchor)
    draw.rectangle(
        [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], fill=WHITE
    )
    draw.text(xy, content, font=font, fill=fill, anchor=anchor)


def fit_text(draw, content, font_path, start_size, min_size, max_width):
    """Shrink to fit, but never below the type floor — truncate instead."""
    for size in range(start_size, min_size - 1, -1):
        font = get_font(font_path, size)
        if draw.textlength(content, font=font) <= max_width:
            return content, font

    font = get_font(font_path, min_size)
    trimmed = content
    while trimmed and draw.textlength(trimmed + DASH, font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + DASH) if trimmed else DASH, font


def is_night(weather):
    now = weather.get("obs_time") or time.time()
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")
    if not sunrise or not sunset:
        return False
    return now < sunrise or now > sunset


def condition_category(icon_name, high=None):
    """Map the API icon string to one of six categories.

    `hot` overrides when the daily high exceeds 28 C.
    """
    if high is not None and high > 28:
        return "hot"
    name = (icon_name or "").lower()
    if "thunder" in name or "storm" in name:
        return "storm"
    if "snow" in name or "wintry" in name:
        return "snow"
    if any(k in name for k in ("rain", "drizzle", "sleet", "hail")):
        return "rain"
    if any(k in name for k in ("cloud", "overcast", "fog", "haze", "mist", "smoke", "dust")):
        return "cloud"
    if "clear" in name:
        return "clear"
    return "cloud"


def condition_glyph(icon_name, night=False):
    name = (icon_name or "").lower()
    if "thunder" in name or "storm" in name:
        return WI["thunderstorm-night"] if night else WI["thunderstorm"]
    if "sleet" in name or "wintry" in name or "hail" in name:
        return WI["sleet"]
    if "snow" in name:
        if "partly" in name or "possibly" in name:
            return WI["snow-night"] if night else WI["day-snow"]
        return WI["snow"]
    if "rain" in name or "drizzle" in name:
        if "partly" in name or "possibly" in name:
            return WI["rain-night"] if night else WI["day-rain"]
        return WI["rain"]
    if "fog" in name or "haze" in name or "mist" in name:
        return WI["fog-night"] if night else WI["fog"]
    if "partly" in name:
        return WI["partly-cloudy-night"] if night else WI["partly-cloudy-day"]
    if "cloud" in name or "overcast" in name:
        return WI["cloudy"]
    if "clear" in name:
        return WI["clear-night"] if night else WI["clear-day"]
    return WI["na"]


def get_wind_direction(degrees):
    if degrees is None:
        return DASH
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(degrees / (360.0 / len(dirs))) % len(dirs)]


# ── Persistent state ──────────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"Could not write {STATE_FILE}: {e}")


def next_ambient_index():
    """Advance the ambient cycle one step per refresh (wind, sun)."""
    try:
        with open(CYCLE_FILE) as f:
            index = int(json.load(f).get("index", 0))
    except Exception:
        index = 0
    try:
        with open(CYCLE_FILE, "w") as f:
            json.dump({"index": (index + 1) % 2}, f)
    except Exception as e:
        print(f"Could not advance ambient cycle: {e}")
    return index % 2


# ── Reliability helpers ───────────────────────────────────────────────────────

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


# ── API fetch ─────────────────────────────────────────────────────────────────

UNIT_PARAMS = {
    "units_temp": "c",
    "units_wind": "kph",
    "units_pressure": "mb",
    "units_precip": "mm",
    "units_distance": "km",
}


def fetch_weather(retries=3):
    """Fetch from the Tempest API with exponential-backoff retry.

    Units are requested from the API rather than converted by hand, so the
    numbers can never drift out of sync with their own labels.
    """
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
                params={"token": TOKEN, **UNIT_PARAMS},
                timeout=HTTP_TIMEOUT,
            )
            obs_response.raise_for_status()
            r_obs = obs_response.json()
            if "obs" not in r_obs or not r_obs["obs"]:
                raise ValueError("No observations in API response")
            obs = r_obs["obs"][0]

            forecast_response = session.get(
                f"{API_BASE_URL}/better_forecast",
                params={"station_id": STATION_ID, "token": TOKEN, **UNIT_PARAMS},
                timeout=HTTP_TIMEOUT,
            )
            forecast_response.raise_for_status()
            r_for = forecast_response.json()
            if "forecast" not in r_for:
                raise ValueError("No forecast in API response")
            current = r_for.get("current_conditions", {})
            daily = r_for["forecast"].get("daily", [])
            hourly = r_for["forecast"].get("hourly", [])
            if not daily:
                raise ValueError("Empty daily forecast")

            # All ten days, not five — the ten-day chart needs them.
            forecast_daily = [
                {
                    "day": time.strftime(
                        "%a", time.localtime(_num(day.get("day_start_local")) or 0)
                    ).upper(),
                    "high": _num(day.get("air_temp_high")),
                    "low": _num(day.get("air_temp_low")),
                    "icon": day.get("icon", "cloudy"),
                    "conditions": day.get("conditions", ""),
                    "precip_prob": _num(day.get("precip_probability")),
                }
                for day in daily[:10]
            ]

            forecast_hourly = [
                {
                    "time": _num(hour.get("time")),
                    "prob": _num(hour.get("precip_probability")),
                    "type": hour.get("precip_type"),
                    "temp": _num(hour.get("air_temperature")),
                }
                for hour in hourly[:24]
            ]

            return {
                "temp": _num(obs.get("air_temperature")),
                "feels_like": _num(current.get("feels_like")) if _num(
                    current.get("feels_like")
                ) is not None else _num(obs.get("feels_like")),
                "condition": current.get("conditions") or daily[0].get("conditions") or "",
                "icon_name": current.get("icon") or daily[0].get("icon") or "clear-day",
                "obs_time": _num(obs.get("timestamp")),
                "today_high": _num(daily[0].get("air_temp_high")),
                "today_low": _num(daily[0].get("air_temp_low")),
                "dew_point": _num(obs.get("dew_point")),
                "wind_avg": _num(obs.get("wind_avg")),
                "wind_gust": _num(obs.get("wind_gust")),
                "wind_dir": _num(obs.get("wind_direction")),
                "pressure": _num(obs.get("sea_level_pressure")),
                "pressure_trend": (current.get("pressure_trend") or "steady").lower(),
                "rain_today": _num(obs.get("precip_accum_local_day")),
                "rain_yesterday": _num(obs.get("precip_accum_local_yesterday")),
                "rain_minutes_today": _num(obs.get("precip_minutes_local_day")),
                "humidity": _num(obs.get("relative_humidity")),
                "wet_bulb": _num(current.get("wet_bulb_temperature")),
                "delta_t": _num(current.get("delta_t")),
                "battery": _num(obs.get("battery")),
                "solar_radiation": _num(obs.get("solar_radiation")),
                "brightness": _num(obs.get("brightness")),
                "lightning_count": _num(obs.get("lightning_strike_count")),
                "lightning_distance": _num(obs.get("lightning_strike_last_distance")),
                "lightning_epoch": _num(obs.get("lightning_strike_last_epoch")),
                "sunrise": _num(daily[0].get("sunrise")),
                "sunset": _num(daily[0].get("sunset")),
                "daily": forecast_daily,
                "hourly": forecast_hourly,
                "fetched_at": time.time(),
            }
        except Exception as e:
            last_err = e
            print(f"Fetch attempt {attempt+1}/{retries} failed: {e}")

    print(f"All {retries} fetch attempts failed. Last error: {last_err}")
    return None


# ── Concern selection ─────────────────────────────────────────────────────────

def select_concern(weather):
    """Highest-priority *active* concern, held for as long as it is active.

    Deliberately not a round-robin: at a 15-minute cadence a rotation shows
    any given item for 15 minutes in every 75, so you can walk up wanting
    the wind and have to wait. Only the ambient fallback rotates.
    """
    now = weather.get("obs_time") or time.time()

    # 1. Station health.
    obs_time = weather.get("obs_time")
    if obs_time and (time.time() - obs_time) > STATION_SILENT_SECONDS:
        silent_for = int((time.time() - obs_time) / 60)
        return {
            "glyph": WI["na"], "ink": INK_STORM,
            "headline": "STATION SILENT",
            "figure": f"last {hhmm(obs_time)} {MIDDOT} {silent_for} min",
        }
    battery = weather.get("battery")
    if battery is not None and battery < BATTERY_LOW_VOLTS:
        return {
            "glyph": WI["na"], "ink": INK_STORM,
            "headline": "STATION BATTERY LOW",
            "figure": f"{battery:.2f} V",
        }

    # 2. Lightning.
    strike_epoch = weather.get("lightning_epoch")
    strike_km = weather.get("lightning_distance")
    if (
        strike_epoch
        and (now - strike_epoch) <= LIGHTNING_RECENT_SECONDS
        and strike_km is not None
        and strike_km <= LIGHTNING_NEAR_KM
    ):
        count = weather.get("lightning_count")
        figure = f"{hhmm(strike_epoch)}"
        if count:
            figure = f"{int(count)} strikes {MIDDOT} {figure}"
        return {
            "glyph": WI["lightning"], "ink": INK_STORM,
            "headline": f"LIGHTNING {strike_km:.0f} km",
            "figure": figure,
        }

    # 3. Precipitation starting or stopping within 3 h.
    transition = precip_transition(weather)
    if transition:
        return transition

    # 4. Gust above threshold.
    gust = weather.get("wind_gust")
    if gust is not None and gust >= GUST_THRESHOLD_KPH:
        direction = get_wind_direction(weather.get("wind_dir"))
        return {
            "glyph": WI["wind"], "ink": INK_HEAT,
            "headline": f"GUSTS {gust:.0f} km/h",
            "figure": f"{direction} {MIDDOT} avg {fmt_num(weather.get('wind_avg'), 0)}",
        }

    # 5. Frost crossing.
    temp = weather.get("temp")
    dew = weather.get("dew_point")
    low = weather.get("today_low")
    if any(v is not None and v <= 0 for v in (temp, dew, low)):
        return {
            "glyph": WI["snowflake"], "ink": INK_SNOW,
            "headline": "FROST",
            "figure": f"low {fmt_temp(low)} {MIDDOT} dew {fmt_temp(dew)}",
        }

    # 6. Nothing flagged — alternate through the ambient cycle.
    if next_ambient_index() == 0:
        avg = weather.get("wind_avg")
        if avg is not None:
            return {
                "glyph": WI["wind"], "ink": WHITE,
                "headline": "NOTHING TO REPORT",
                "figure": f"wind {avg:.0f} km/h {get_wind_direction(weather.get('wind_dir'))}",
            }
    sunset = weather.get("sunset")
    if sunset:
        return {
            "glyph": WI["sunset"], "ink": WHITE,
            "headline": "NOTHING TO REPORT",
            "figure": f"sunset {hhmm(sunset)}",
        }
    return {
        "glyph": WI["clear-day"], "ink": WHITE,
        "headline": "NOTHING TO REPORT",
        "figure": fmt_temp(weather.get("today_low")),
    }


def precip_transition(weather):
    """Precipitation starting or stopping inside the next three hours."""
    hours = [h for h in weather.get("hourly", [])[:4] if h.get("prob") is not None]
    if len(hours) < 2:
        return None

    wet = [h["prob"] >= PRECIP_LIKELY_PCT for h in hours]
    kind = None
    for hour in hours:
        if hour.get("type"):
            kind = str(hour["type"]).lower()
            break
    is_snow = kind == "snow"
    ink = INK_SNOW if is_snow else INK_RAIN
    word = "SNOW" if is_snow else "RAIN"
    glyph = WI["snow"] if is_snow else WI["rain"]

    for i in range(1, len(wet)):
        if wet[i] and not wet[i - 1]:
            hour = hours[i]
            return {
                "glyph": glyph, "ink": ink,
                "headline": f"{word} FROM {hhmm(hour['time'])}",
                "figure": f"{int(hour['prob'])}%",
            }
        if wet[i - 1] and not wet[i]:
            hour = hours[i]
            return {
                "glyph": glyph, "ink": ink,
                "headline": f"{word} STOPS {hhmm(hour['time'])}",
                "figure": f"{int(hours[i-1]['prob'])}% now",
            }

    if wet and wet[0]:
        hour = hours[0]
        return {
            "glyph": glyph, "ink": ink,
            "headline": f"{word} NOW",
            "figure": f"{int(hour['prob'])}%",
        }
    return None


# ── Region 1: concern band (y 0-58) ───────────────────────────────────────────

def draw_concern_band(draw, weather):
    """Full width, 58 px including a 3 px bottom border.

    Keeps its height even when nothing is flagged — stable geometry between
    refreshes matters more than reclaiming the space, and redrawing a region
    with different geometry each cycle accelerates ghosting.
    """
    concern = select_concern(weather)
    ink = concern["ink"]
    fg = type_on(ink)

    draw.rectangle([0, BAND_Y0, WIDTH, BAND_Y1], fill=ink)
    draw.rectangle([0, BAND_Y1 - RULE_DIVIDER, WIDTH, BAND_Y1], fill=BLACK)

    mid = (BAND_Y0 + BAND_Y1 - RULE_DIVIDER) // 2
    x = 20

    draw.text((x, mid), concern["glyph"], font=icon(34), fill=fg, anchor="lm")
    x += glyph_advance(draw, concern["glyph"], 34) + 12

    figure = concern.get("figure") or ""
    figure_font = bold(22)
    figure_w = draw.textlength(figure, font=figure_font) if figure else 0
    headline_max = WIDTH - 20 - x - (figure_w + 16 if figure else 0)

    headline, headline_font = fit_text(
        draw, concern["headline"], FONT_DISPLAY, 32, 24, headline_max
    )
    draw.text((x, mid), headline, font=headline_font, fill=fg, anchor="lm")

    if figure:
        draw.text((WIDTH - 20, mid), figure, font=figure_font, fill=fg, anchor="rm")


# ── Region 2: spine (x 0-296, y 58-276) ───────────────────────────────────────

def draw_spine(draw, weather):
    """296 px wide, 3 px right border, bottom block pinned to the bottom."""
    right = SPINE_W - RULE_DIVIDER
    draw.rectangle([right, MID_Y0, SPINE_W, MID_Y1], fill=BLACK)

    pad_x = 18
    inner_right = right - pad_x
    y = MID_Y0 + 10

    night = is_night(weather)
    draw.text(
        (pad_x, y), condition_glyph(weather.get("icon_name"), night),
        font=icon(38), fill=BLACK, anchor="lt",
    )
    y += 40

    temp = weather.get("temp")
    if temp is None:
        # An em dash set at 96 px is a solid black bar that reads as a
        # redaction rather than as missing data.
        draw.text((pad_x, y + 18), DASH, font=display(48), fill=BLACK, anchor="lt")
    else:
        temp_str, temp_font = fit_text(
            draw, fmt_temp(temp, 1), FONT_DISPLAY, 96, 72, inner_right - pad_x
        )
        draw.text((pad_x, y), temp_str, font=temp_font, fill=BLACK, anchor="lt")
    y += 84

    condition = (weather.get("condition") or "").upper()
    feels = weather.get("feels_like")
    line = condition if condition else ""
    if feels is not None:
        line = f"{line} {MIDDOT} feels {fmt_temp(feels)}" if line else f"feels {fmt_temp(feels)}"
    line, line_font = fit_text(draw, line, FONT_BOLD, 24, 16, inner_right - pad_x)
    draw.text((pad_x, y), line, font=line_font, fill=BLACK, anchor="lt")

    # Bottom block: 2 px rule, 4 px padding, then range + observation age.
    bottom = MID_Y1 - 8
    draw.text(
        (pad_x, bottom),
        f"{fmt_temp(weather.get('today_low'))} {ARROW} {fmt_temp(weather.get('today_high'))}",
        font=display(25), fill=BLACK, anchor="ls",
    )

    if weather.get("stale"):
        age = f"STALE {MIDDOT} {hhmm(weather.get('fetched_at'))}"
    else:
        age = observation_age(weather)
    draw.text((inner_right, bottom), age, font=text(16), fill=BLACK, anchor="rs")

    rule_y = bottom - 25 - 4 - RULE_SUB
    draw.rectangle([pad_x, rule_y, inner_right, rule_y + RULE_SUB], fill=BLACK)


def observation_age(weather):
    """Age of the station observation, not of the render.

    Those diverge exactly when you need to know. This is the one place the
    panel prints a relative time, because it is *about* staleness.
    """
    obs_time = weather.get("obs_time")
    if not obs_time:
        return DASH
    minutes = int(max(0, time.time() - obs_time) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 24 * 60:
        return f"{minutes // 60} h ago"
    # Past a day, an hour count stops being readable — name the moment.
    return time.strftime("%-d %b %H:%M", time.localtime(obs_time))


# ── Region 3: 12-hour precipitation chart (x 296-800, y 58-276) ───────────────

BAR_AREA_H = 129
HOUR_ROW_H = 22
CHART_HEADER_H = 24


def draw_precip_chart(draw, weather):
    """Probability bars on a full 0-100 % scale.

    The 100 % and 50 % rules are always drawn, even when no bar reaches
    them: without a scale an empty chart reads as broken hardware, with one
    it reads as dry.
    """
    pad_x = 18
    x0 = SPINE_W + pad_x
    x1 = WIDTH - pad_x

    bottom = MID_Y1 - 8
    label_top = bottom - HOUR_ROW_H
    border_y = label_top - RULE_SUB
    baseline = border_y
    area_top = baseline - BAR_AREA_H
    header_y = area_top - CHART_HEADER_H

    hours = weather.get("hourly", [])[:12]
    kinds = [str(h.get("type") or "").lower() for h in hours]
    is_snow = any(k == "snow" for k in kinds)
    bar_ink = INK_SNOW if is_snow else INK_RAIN

    draw.text(
        (x0, header_y + CHART_HEADER_H // 2), "NEXT 12 HOURS",
        font=text(17), fill=BLACK, anchor="lm",
    )
    draw.text(
        (x1, header_y + CHART_HEADER_H // 2),
        "CHANCE OF SNOW" if is_snow else "CHANCE OF RAIN",
        font=bold(17), fill=BLACK, anchor="rm",
    )

    # Bars first, so the scale rules and their labels sit on top.
    count = 12
    gap = 5
    span = x1 - x0
    bar_w = (span - gap * (count - 1)) / count
    for i in range(count):
        hour = hours[i] if i < len(hours) else {}
        prob = hour.get("prob")
        if prob is None or prob <= 0:
            continue  # a 0 % hour draws no bar; a stub would read as noise
        height = max(4, round(prob / 100 * BAR_AREA_H))
        left = x0 + i * (bar_w + gap)
        draw.rectangle(
            [round(left), baseline - height, round(left + bar_w), baseline],
            fill=bar_ink,
        )

    draw.rectangle([x0, area_top, x1, area_top + RULE_SUB], fill=BLACK)
    half_y = baseline - 64
    draw.rectangle([x0, half_y, x1, half_y + RULE_SUB], fill=BLACK)

    knockout_text(draw, (x1, area_top + 8), "100%", text(16), anchor="rm", pad=2)
    knockout_text(draw, (x1, baseline - 68 + 8), "50%", text(16), anchor="rm", pad=2)

    draw.rectangle([x0, border_y, x1, border_y + RULE_SUB], fill=BLACK)

    hour_font = text(16)
    for i in range(count):
        hour = hours[i] if i < len(hours) else {}
        stamp = hour.get("time")
        label = time.strftime("%H", time.localtime(stamp)) if stamp else DASH
        left = x0 + i * (bar_w + gap)
        draw.text(
            (left + bar_w / 2, label_top + HOUR_ROW_H // 2),
            label, font=hour_font, fill=BLACK, anchor="mm",
        )


# ── Region 4: five fixed slots (y 276-368) ────────────────────────────────────

def slot_values(weather):
    """The five metrics and their order are fixed forever.

    That is what guarantees nothing is ever missing: the number you want is
    always in the position you last found it. A metric with no data renders
    an em dash — it does not vanish and it is not skipped. Units live in the
    16 px label, not the 24 px value, to keep the number as large as
    possible inside a ~150 px slot.

    Lightning has no slot: it is an event, not a standing metric, so it
    appears in the concern band at priority 2 and nowhere else.
    """
    dew = weather.get("dew_point")
    rain = weather.get("rain_today")
    gust = weather.get("wind_gust")
    wind = weather.get("wind_avg")
    pressure = weather.get("pressure")
    trend = weather.get("pressure_trend", "steady")
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")

    if sunrise and sunset and sunset > sunrise:
        total = int(sunset - sunrise)
        daylight = f"{total // 3600}h{(total % 3600) // 60:02d}"
    else:
        daylight = DASH

    pressure_str = fmt_num(pressure, 0)

    return [
        {
            "label": "DEW POINT", "glyph": WI["snowflake"],
            "value": fmt_temp(dew, 1),
            "ink": INK_SNOW if (dew is not None and dew <= 0) else None,
        },
        {
            "label": "RAIN TODAY", "glyph": WI["raindrop"],
            "value": fmt_num(rain, 1, " mm"),
            "ink": INK_RAIN if (rain is not None and rain > 0) else None,
        },
        {
            "label": "WIND km/h", "glyph": WI["wind"],
            "value": fmt_num(wind, 0),
            "ink": INK_HEAT if (gust is not None and gust >= GUST_THRESHOLD_KPH) else None,
        },
        {
            "label": "PRESSURE", "glyph": WI["barometer"],
            "value": pressure_str,
            "ink": None,
            "trend": trend if pressure is not None else None,
        },
        {
            "label": "DAYLIGHT", "glyph": WI["sunrise"],
            "value": daylight,
            "ink": None,
        },
    ]


def draw_slots(draw, weather):
    draw.rectangle([0, SLOT_Y0, WIDTH, SLOT_Y0 + RULE_DIVIDER], fill=BLACK)

    pad_x, pad_y, gap = 14, 8, 6
    top = SLOT_Y0 + RULE_DIVIDER + pad_y
    bottom = SLOT_Y1 - pad_y
    usable = WIDTH - 2 * pad_x
    slot_w = (usable - gap * 4) / 5

    for i, slot in enumerate(slot_values(weather)):
        left = pad_x + i * (slot_w + gap)
        right = left + slot_w
        ink = slot["ink"] or WHITE
        fg = type_on(ink)

        if slot["ink"]:
            draw.rectangle([round(left), top, round(right), bottom], fill=ink)

        mid = (top + bottom) / 2
        gx = left + 6
        draw.text((gx, mid), slot["glyph"], font=icon(32), fill=fg, anchor="lm")

        tx = gx + glyph_advance(draw, slot["glyph"], 32) + 6
        trend = slot.get("trend")
        arrow_w = 18 if trend else 0
        avail = right - 6 - tx - arrow_w

        value, value_font = fit_text(draw, slot["value"], FONT_DISPLAY, 24, 24, avail)
        draw.text((tx, mid - 2), value, font=value_font, fill=fg, anchor="ls")

        if trend:
            ax = tx + draw.textlength(value, font=value_font) + 5
            draw_trend_arrow(draw, ax, mid - 10, 13, trend, fill=fg)

        label, label_font = fit_text(draw, slot["label"], FONT_TEXT, 16, 16, avail + arrow_w)
        draw.text((tx, bottom - 4), label, font=label_font, fill=fg, anchor="ls")


# ── Region 5: ten-day chart (y 368-480) ───────────────────────────────────────

TEN_BAR_ROW_H = 76
TEN_DAY_ROW_H = 22
TEN_GUTTER = 34
TEN_BAR_MIN = 8
TEN_BAR_RANGE = 48


def draw_tenday(draw, weather):
    """Double encoding: bar height carries temperature, fill carries condition.

    Height is a continuous channel for a continuous variable; hue is a
    categorical channel for a categorical one. You read the shape of the
    week before reading any digit.
    """
    draw.rectangle([0, TEN_Y0, WIDTH, TEN_Y0 + RULE_DIVIDER], fill=BLACK)

    pad_x, pad_y = 18, 6
    bottom = TEN_Y1 - pad_y
    label_top = bottom - TEN_DAY_ROW_H
    baseline = label_top
    x0 = pad_x + TEN_GUTTER
    x1 = WIDTH - pad_x

    days = weather.get("daily", [])[:10]
    highs = [d["high"] for d in days if d.get("high") is not None]
    if not highs:
        return

    lo, hi = min(highs), max(highs)
    span = hi - lo

    def bar_height(value):
        if span <= 0:  # every high identical — no scale to map onto
            return TEN_BAR_MIN + TEN_BAR_RANGE // 2
        return round(TEN_BAR_MIN + (value - lo) / span * TEN_BAR_RANGE)

    count = max(len(days), 1)
    gap = 5
    col_w = (x1 - x0 - gap * (count - 1)) / count

    for i, day in enumerate(days):
        high = day.get("high")
        if high is None:
            continue
        left = x0 + i * (col_w + gap)
        height = bar_height(high)
        box = [round(left), baseline - height, round(left + col_w), baseline]
        category = condition_category(day.get("icon"), high)
        ink = CATEGORY_INK.get(category, WHITE)
        if category == "cloud":
            draw.rectangle(box, fill=WHITE)
            draw.rectangle(box, outline=BLACK, width=3)
        else:
            draw.rectangle(box, fill=ink)

    # The 0 C rule is hidden entirely when freezing is outside the ten-day
    # range — pinning it to the floor would misrepresent the scale.
    if span > 0 and lo <= 0 <= hi:
        zero_y = baseline - bar_height(0)
        draw.rectangle([x0, zero_y, x1, zero_y + RULE_DIVIDER], fill=BLACK)
        draw.text((pad_x, zero_y - 5), "0°C", font=text(16), fill=BLACK, anchor="ls")

    # Numbers knock white out of any rule they cross.
    high_font = display(20)
    for i, day in enumerate(days):
        high = day.get("high")
        if high is None:
            continue
        left = x0 + i * (col_w + gap)
        height = bar_height(high)
        knockout_text(
            draw, (left + col_w / 2, baseline - height - 2),
            f"{high:.0f}°", high_font, anchor="ms",
        )

    day_font = text(17)
    for i, day in enumerate(days):
        left = x0 + i * (col_w + gap)
        draw.text(
            (left + col_w / 2, label_top + TEN_DAY_ROW_H // 2),
            day.get("day", DASH), font=day_font, fill=BLACK, anchor="mm",
        )


# ── Dashboard ─────────────────────────────────────────────────────────────────

def draw_error_screen(draw, message):
    draw.text(
        (WIDTH // 2, HEIGHT // 2), message,
        fill=BLACK, font=display(34), anchor="mm", align="center",
    )


def create_dashboard(weather, theme_name="inky"):
    """Render layout 3A.

    theme_name is accepted for backwards compatibility with desktop.py;
    the panel and the desktop window now render identically, so the desktop
    app is a true preview of what the Inky shows.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    if not weather:
        draw_error_screen(draw, "DATA FETCH ERROR\nCheck Console Logs")
        return img

    try:
        draw_concern_band(draw, weather)
        draw_spine(draw, weather)
        draw_precip_chart(draw, weather)
        draw_slots(draw, weather)
        draw_tenday(draw, weather)
    except Exception as e:
        print(f"Error drawing dashboard: {e}")
        img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
        draw = ImageDraw.Draw(img)
        draw_error_screen(draw, "RENDER ERROR\nCheck Console Logs")

    return img


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


# ── Refresh cadence ───────────────────────────────────────────────────────────

def refresh_interval_minutes(weather):
    """Fewer lifetime repaints, better resolution when something is happening."""
    hour = time.localtime().tm_hour
    if hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR:
        return REFRESH_NIGHT_MIN
    if weather:
        upcoming = [
            h.get("prob") for h in weather.get("hourly", [])[:1]
            if h.get("prob") is not None
        ]
        if upcoming and upcoming[0] > 40:
            return REFRESH_ACTIVE_MIN
    return REFRESH_NORMAL_MIN


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Render the Tempest Inky dashboard.")
    parser.add_argument(
        "--force", action="store_true",
        help="Render now, ignoring the adaptive refresh schedule.",
    )
    parser.add_argument(
        "--output", default="dashboard-preview.png",
        help="Where to save the preview when no Inky panel is attached. "
             "PNG, not JPEG: the panel renders 7 flat inks, and JPEG "
             "ringing turns those into thousands of colours.",
    )
    args = parser.parse_args()

    state = load_state()
    next_due = state.get("next_due")
    if not args.force and next_due and time.time() < next_due:
        print(f"Not due until {hhmm(next_due)} — skipping (use --force to override).")
        return

    if INKY_AVAILABLE:
        wait_for_network(timeout=120)

    print("Fetching weather...")
    weather = fetch_weather()

    if weather:
        state["payload"] = weather
    else:
        cached = state.get("payload")
        if cached:
            # 20-minute-old weather beats an error screen.
            print("Fetch failed — rendering cached payload as stale.")
            weather = dict(cached)
            weather["stale"] = True
        else:
            print("Fetch failed and no cache available — error screen.")

    state["next_due"] = time.time() + refresh_interval_minutes(weather) * 60
    save_state(state)

    img = create_dashboard(weather, theme_name="inky")

    if INKY_AVAILABLE:
        try:
            panel = auto()
            panel.set_image(quantize_for_inky(img))
            panel.show()
            print("Display updated.")
        except Exception as e:
            print(f"Display error: {e}")
            raise   # let systemd record the failure
    else:
        quantize_for_inky(img).save(args.output)
        print(f"Saved {args.output}")


if __name__ == "__main__":
    main()

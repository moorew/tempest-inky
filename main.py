"""Tempest Inky dashboard — layout 5B.

Renders an 800x480 panel for the Pimoroni Inky Impression 7.3" (7 colour)
from a WeatherFlow Tempest station, plus an optional government alert feed.

The panel is read from a sofa about 4.5 m away, in a dim room. Comfortable
reading needs a cap height of roughly distance / 200, which at 137 ppi is
~122 px — and Archivo Black's caps run ~0.72em, so couch-readable type
starts at 170 px. There is no arrangement of 800x480 that makes two text
elements couch-readable, so the panel carries exactly two things at that
distance: a number and a shape. The 228 px temperature and the 168 px
condition glyph. Everything else is a walk-up element and is sized as one.

Colour is a categorical channel only. The 800x300 hero field carries
official alert severity and nothing else; the small fills (ten-day bars)
carry condition and nothing else. All type is black, or white on blue.
"""

import argparse
import importlib.util
import json
import os
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFont

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
ALERT_FILE = os.path.join(user_home, ".tempest-alert.json")

# Thresholds for the headline band.
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
    """Import a secrets.py and hand back the module.

    The module rather than a tuple, because it now carries optional
    location settings alongside the credentials.
    """
    spec = importlib.util.spec_from_file_location("tempest_user_secrets", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {path}")
    user_secrets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(user_secrets)
    return user_secrets


@lru_cache(maxsize=1)
def get_secrets():
    for secret_path in [
        os.path.join(user_home, "secrets.py"),
        os.path.join(get_app_path(), "secrets.py"),
    ]:
        if not os.path.exists(secret_path):
            continue
        try:
            module = load_secrets_file(secret_path)
            print(f"Loaded configuration from {secret_path}")
            return module
        except Exception as e:
            print(f"Error loading {secret_path}: {e}")
    return None


def load_config():
    station_id = os.environ.get("TEMPEST_STATION_ID")
    token = os.environ.get("TEMPEST_TOKEN")
    if station_id and token:
        print("Loaded configuration from environment")
        return station_id, token
    if station_id or token:
        print("Incomplete environment configuration; falling back to secrets.py")

    module = get_secrets()
    if module is not None:
        found_id = getattr(module, "STATION_ID", None)
        found_token = getattr(module, "TOKEN", None)
        if found_id and found_token:
            return str(found_id), str(found_token)

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

# The alert ladder. Deliberately reuses inks from the condition scale: the
# two are separated by region and size, and never occur in the same element.
SEVERITY_INK = {
    "advisory": INK_CLEAR,
    "watch": INK_HEAT,
    "warning": INK_STORM,
}
SEVERITY_RANK = {"advisory": 1, "watch": 2, "warning": 3}
SEVERITY_COLOUR_NAME = {"advisory": "yellow", "watch": "orange", "warning": "red"}

DASH = "—"          # em dash, the "no data" mark
MIDDOT = "·"

# Verified against assets/weathericons.ttf — every one renders, no tofu.
WI = {
    "clear-day": "",
    "clear-night": "",
    "partly-cloudy-day": "",
    "partly-cloudy-night": "",
    "cloudy": "",
    "overcast": "",
    "rain": "",
    "rain-night": "",
    "day-rain": "",
    "snow": "",
    "snow-night": "",
    "day-snow": "",
    "sleet": "",
    "thunderstorm": "",
    "thunderstorm-night": "",
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
    "hot": "",
    "na": "",
}

# ── Region geometry ───────────────────────────────────────────────────────────
# Four stacked full-width regions, each with a 4 px black bottom rule except
# the last, and the rule lives inside the region's own height. Geometry must
# stay byte-identical between refreshes and between states, or the panel
# ghosts: only fills and text content are allowed to change.
#
# The handoff lists 300 + 80 + 44 + 52, which sums to 476 rather than 480 —
# the HTML leaves a 4 px white strip below the last row. The ten-day region
# takes that strip (56 px) and keeps its content top-aligned at exactly the
# specified 18 / 24 / 8, so it renders identically with nothing left over.

RULE = 4

HERO_H = 300
BAND_H = 80
METRICS_H = 44
TENDAY_H = 56

HERO_Y0, HERO_Y1 = 0, HERO_H                            # 0   - 300
BAND_Y0, BAND_Y1 = HERO_Y1, HERO_Y1 + BAND_H            # 300 - 380
METRICS_Y0, METRICS_Y1 = BAND_Y1, BAND_Y1 + METRICS_H   # 380 - 424
TEN_Y0, TEN_Y1 = METRICS_Y1, METRICS_Y1 + TENDAY_H      # 424 - 480

PAD_X = 22
HERO_GAP = 10
BAND_GAP = 14
METRIC_GAP = 7
TEN_GAP = 4

# Type scale. Nothing under 19 px except the 16 px day labels, which are the
# shortest strings on the panel.
SIZE_TEMP = 228
SIZE_HERO_GLYPH = 168
SIZE_HERO_GLYPH_MIN = 130
SIZE_HILO = 46
SIZE_BAND_GLYPH = 42
SIZE_HEADLINE = 40
SIZE_METRIC_GLYPH = 26
SIZE_METRIC = 24
SIZE_FIGURE = 24
SIZE_TEN_HIGH = 22
SIZE_LABEL = 19
SIZE_DAY = 16

TRACK_TEMP = -0.035 * SIZE_TEMP     # letter-spacing -0.035em
TRACK_LABEL = 0.1 * SIZE_LABEL      # letter-spacing 0.1em

assert HERO_H + BAND_H + METRICS_H + TENDAY_H == HEIGHT


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

    Black on blue measures ~2.4:1 and fails; white on blue is ~8.6:1. No
    severity ink is blue, so the hero is always black type — this is here
    so a future ink cannot silently break the contrast rule.
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

    Weather Icons glyphs vary from 124 px to 237 px wide at 168 px, so the
    text beside one cannot sit at a fixed offset.
    """
    font = icon(size)
    box = draw.textbbox((0, 0), char, font=font, anchor="lt")
    return max(draw.textlength(char, font=font), box[2])


def tracked_width(draw, content, font, track):
    """Width of a string drawn with letter-spacing.

    PIL has no tracking, so it is applied per character and measured the
    same way. No trailing space after the last glyph, which is what makes
    right-aligned tracked text land where it should.
    """
    if not content:
        return 0.0
    return sum(draw.textlength(c, font=font) for c in content) + track * (len(content) - 1)


def draw_tracked(draw, x, baseline, content, font, track, fill=BLACK, align="left"):
    if not content:
        return
    if align == "right":
        x -= tracked_width(draw, content, font, track)
    for char in content:
        draw.text((x, baseline), char, font=font, fill=fill, anchor="ls")
        x += draw.textlength(char, font=font) + track


def baseline_for(draw, content, font, centre_y):
    """Baseline that centres a string on its own ink, not on its metrics.

    PIL's "m" anchor centres between ascender and descender, which sits
    digits visibly low because they have no descender. The design's
    line-height 0.8 is doing the same job in CSS.
    """
    if not content:
        return centre_y
    box = draw.textbbox((0, 0), content, font=font, anchor="ls")
    return centre_y - (box[1] + box[3]) / 2


def draw_centred(draw, x, centre_y, content, font, fill=BLACK, anchor_x="l"):
    """Draw a string horizontally at x, vertically centred on its ink."""
    if not content:
        return
    baseline = baseline_for(draw, content, font, centre_y)
    draw.text((x, baseline), content, font=font, fill=fill, anchor=f"{anchor_x}s")


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


def alert_glyph(event):
    """A glyph for an official alert, chosen from its event name."""
    name = (event or "").lower()
    if any(k in name for k in ("snow", "blizzard", "winter", "flurr", "squall")):
        return WI["snow"]
    if any(k in name for k in ("freezing", "frost", "cold", "wind chill", "ice")):
        return WI["snowflake"]
    if any(k in name for k in ("thunder", "tornado", "hurricane", "tropical")):
        return WI["thunderstorm"]
    if any(k in name for k in ("rain", "flood", "rainfall")):
        return WI["rain"]
    if "fog" in name:
        return WI["fog"]
    if "wind" in name or "gale" in name:
        return WI["wind"]
    if "heat" in name or "humidex" in name:
        return WI["hot"]
    return WI["na"]


def get_wind_direction(degrees):
    if degrees is None:
        return DASH
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(degrees / (360.0 / len(dirs))) % len(dirs)]


# ── Persistent state ──────────────────────────────────────────────────────────

def load_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"Could not write {path}: {e}")


def load_state():
    return load_json(STATE_FILE)


def save_state(state):
    save_json(STATE_FILE, state)


# ── Location and region ───────────────────────────────────────────────────────
# Everything below is optional. With nothing configured the station's own
# coordinates and timezone come back from the API and pick the provider.

CA_TIMEZONES = {
    "America/St_Johns", "America/Halifax", "America/Glace_Bay", "America/Moncton",
    "America/Goose_Bay", "America/Toronto", "America/Nipigon", "America/Thunder_Bay",
    "America/Iqaluit", "America/Pangnirtung", "America/Atikokan", "America/Winnipeg",
    "America/Rainy_River", "America/Resolute", "America/Rankin_Inlet", "America/Regina",
    "America/Swift_Current", "America/Edmonton", "America/Cambridge_Bay",
    "America/Yellowknife", "America/Inuvik", "America/Creston", "America/Dawson_Creek",
    "America/Fort_Nelson", "America/Vancouver", "America/Whitehorse", "America/Dawson",
}
UK_TIMEZONES = {"Europe/London", "Europe/Belfast"}
US_EXTRA_TIMEZONES = {"Pacific/Honolulu", "America/Adak", "America/Anchorage"}


def _env_float(name):
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        print(f"Ignoring {name}={raw!r}: not a number")
        return None


def load_location_config():
    """Where the panel is, and whose alerts apply.

    Env wins over secrets.py, which wins over the station's own metadata.
    Region "auto" resolves from the station timezone at fetch time.
    """
    module = get_secrets()

    def from_secrets(name):
        return getattr(module, name, None) if module is not None else None

    lat = _env_float("TEMPEST_LAT")
    lon = _env_float("TEMPEST_LON")
    if lat is None:
        lat = _num(from_secrets("LATITUDE"))
    if lon is None:
        lon = _num(from_secrets("LONGITUDE"))

    region = os.environ.get("TEMPEST_ALERT_REGION") or from_secrets("ALERT_REGION") or "auto"
    area = os.environ.get("TEMPEST_ALERT_AREA") or from_secrets("ALERT_AREA") or "uk"
    return {
        "lat": lat,
        "lon": lon,
        "region": str(region).strip().lower(),
        "area": str(area).strip().lower(),
    }


def detect_region(tz_name=None, lat=None, lon=None):
    """Pick an alert provider for a station.

    Timezone first, because the US and Canadian bounding boxes overlap for
    hundreds of kilometres either side of the border — Toronto sits inside
    the contiguous-US box. The boxes are only a fallback for the case where
    the API returned coordinates but no timezone.
    """
    tz_name = (tz_name or "").strip()
    if tz_name:
        if tz_name in CA_TIMEZONES:
            return "ca"
        if tz_name in UK_TIMEZONES:
            return "uk"
        if tz_name in US_EXTRA_TIMEZONES or tz_name.startswith("America/"):
            return "us"
        return None

    if lat is None or lon is None:
        return None
    if 49.8 <= lat <= 61.0 and -8.7 <= lon <= 1.9:
        return "uk"

    in_us = (
        (24.4 <= lat <= 49.0 and -125.0 <= lon <= -66.9)        # contiguous
        or (51.0 <= lat <= 71.5 and -180.0 <= lon <= -129.9)    # Alaska
        or (18.9 <= lat <= 22.3 and -160.3 <= lon <= -154.8)    # Hawaii
    )
    in_ca = 41.6 <= lat <= 83.2 and -141.1 <= lon <= -52.6
    if in_us and in_ca:
        # The two boxes overlap for hundreds of kilometres either side of
        # the border — Toronto is further south than Minneapolis — and
        # serving the wrong country's alerts is worse than serving none.
        print("Station sits in the Canada/US border band — set ALERT_REGION to ca or us.")
        return None
    if in_us:
        return "us"
    if in_ca:
        return "ca"
    return None


# ── Alert feed ────────────────────────────────────────────────────────────────
# A second network dependency that must never be able to take the weather
# display down with it: every failure here is "no alert".

ALERT_TIMEOUT = 10
ALERT_USER_AGENT = "tempest-inky (https://github.com/moorew/tempest-inky)"
CA_ALERTS_URL = "https://api.weather.gc.ca/collections/weather-alerts/items"
US_ALERTS_URL = "https://api.weather.gov/alerts/active"
UK_ALERTS_URL = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{area}"

UK_COLOUR_SEVERITY = {"yellow": "advisory", "amber": "watch", "red": "warning"}

# Met Office regional warning feeds. "uk" is the national one; the rest are
# what ALERT_AREA selects, because the Met Office has no free point API and
# every item in a regional feed applies to that region.
UK_REGIONS = {
    "uk": "UK (national)",
    "os": "Orkney & Shetland",
    "he": "Highlands & Eilean Siar",
    "gr": "Grampian",
    "ta": "Central, Tayside & Fife",
    "st": "Strathclyde",
    "dg": "Dumfries, Galloway, Lothian & Borders",
    "ni": "Northern Ireland",
    "wl": "Wales",
    "sw": "South West England",
    "se": "London & South East England",
    "ee": "East of England",
    "em": "East Midlands",
    "wm": "West Midlands",
    "yh": "Yorkshire & Humber",
    "nw": "North West England",
    "ne": "North East England",
}
UK_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}


def cap_severity(value):
    """CAP severity to the panel's three-rung ladder.

    Minor -> advisory, Moderate -> watch, Severe/Extreme -> warning. Written
    once and shared, because all three national services speak CAP.
    """
    name = (value or "").strip().lower()
    if name in ("extreme", "severe"):
        return "warning"
    if name == "moderate":
        return "watch"
    return "advisory"


def parse_iso(value):
    """ISO-8601 to epoch seconds, tolerating a trailing Z and no offset."""
    if not value:
        return None
    try:
        stamp = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _alert_ca(session, lat, lon, area):
    """Environment Canada, via the MSC GeoMet alerts collection.

    A point is expressed as a hair-thin bbox: the alert geometries are
    regional polygons, so an intersection test is the point-in-region test.
    """
    delta = 0.02
    response = session.get(
        CA_ALERTS_URL,
        params={
            "f": "json",
            "limit": 50,
            "bbox": f"{lon - delta:.4f},{lat - delta:.4f},{lon + delta:.4f},{lat + delta:.4f}",
        },
        headers={"User-Agent": ALERT_USER_AGENT},
        timeout=ALERT_TIMEOUT,
    )
    response.raise_for_status()

    alerts = []
    for feature in response.json().get("features", []):
        props = feature.get("properties", {})
        if (props.get("status_en") or "").strip().lower() == "ended":
            continue
        kind = (props.get("alert_type") or "").strip().lower()
        alerts.append({
            "severity": kind if kind in SEVERITY_RANK else "advisory",
            "event": (props.get("alert_name_en") or "alert").upper(),
            "expires": parse_iso(props.get("expiration_datetime")),
            "area": props.get("feature_name_en") or "",
        })
    return alerts


def _alert_us(session, lat, lon, area):
    """US National Weather Service. Free, no key, native CAP fields."""
    response = session.get(
        US_ALERTS_URL,
        params={"point": f"{lat:.4f},{lon:.4f}", "status": "actual"},
        headers={"User-Agent": ALERT_USER_AGENT, "Accept": "application/geo+json"},
        timeout=ALERT_TIMEOUT,
    )
    response.raise_for_status()

    alerts = []
    for feature in response.json().get("features", []):
        props = feature.get("properties", {})
        if (props.get("messageType") or "").strip().lower() == "cancel":
            continue
        alerts.append({
            "severity": cap_severity(props.get("severity")),
            "event": (props.get("event") or "alert").upper(),
            "expires": parse_iso(props.get("ends") or props.get("expires")),
            "area": props.get("areaDesc") or "",
        })
    return alerts


def _uk_expiry(description):
    """Pull the end of validity out of a Met Office description string.

    The text reads "... valid from 1200 Mon 12 Aug to 2100 Mon 12 Aug". No
    year is printed, so the current one is assumed and a match that lands
    in the past is rolled forward. Anything unparseable falls back to 12 h,
    which bounds how long a warning can linger in the cache.
    """
    fallback = time.time() + 12 * 3600
    match = re.search(
        r"\bto\s+(\d{2})(\d{2})\s+\w{3}\s+(\d{1,2})\s+(\w{3})",
        description or "",
        re.IGNORECASE,
    )
    if not match:
        return fallback
    hour, minute, day, month_name = match.groups()
    month = UK_MONTHS.get(month_name[:3].lower())
    if not month:
        return fallback
    try:
        # The feed prints UK local wall-clock, and a panel configured for UK
        # warnings is in the UK, so the panel's own offset is the right one.
        now = datetime.now().astimezone()
        end = datetime(now.year, month, int(day), int(hour), int(minute), tzinfo=now.tzinfo)
        if end < now - timedelta(days=1):
            end = end.replace(year=now.year + 1)
        return end.timestamp()
    except ValueError:
        return fallback


def _alert_uk(session, lat, lon, area):
    """Met Office warnings RSS.

    There is no free keyless point API for the UK, so this reads a regional
    feed and every item in it applies — set ALERT_AREA to the Met Office
    region code (`uk` for the national feed, `wl`, `se`, `os` and so on).
    Severity comes from the colour word the Met Office already uses, which
    maps onto the ladder directly.
    """
    area = area or "uk"
    if area not in UK_REGIONS:
        print(f"ALERT_AREA {area!r} is not a Met Office region — "
              f"expected one of {', '.join(sorted(UK_REGIONS))}.")
    response = session.get(
        UK_ALERTS_URL.format(area=area),
        headers={"User-Agent": ALERT_USER_AGENT},
        timeout=ALERT_TIMEOUT,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    alerts = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        match = re.match(
            r"(yellow|amber|red)\s+warning\s+of\s+(.+?)(?:\s+affecting\s+(.*))?$",
            title,
            re.IGNORECASE,
        )
        if not match:
            continue
        colour, hazard, where = match.groups()
        alerts.append({
            "severity": UK_COLOUR_SEVERITY.get(colour.lower(), "advisory"),
            "event": f"{hazard.strip()} warning".upper(),
            "expires": _uk_expiry(item.findtext("description") or ""),
            "area": (where or "").strip(),
        })
    return alerts


ALERT_PROVIDERS = {"ca": _alert_ca, "us": _alert_us, "uk": _alert_uk}


def fetch_alert(region, lat, lon, area="uk"):
    """The single alert that should own the panel, or None.

    Highest severity wins; between equals, the one that runs longest, so a
    warning does not flicker to a shorter overlapping one mid-event.
    """
    provider = ALERT_PROVIDERS.get(region or "")
    if provider is None:
        return None
    if provider is not _alert_uk and (lat is None or lon is None):
        return None

    session = requests.Session()
    now = time.time()
    active = [
        a for a in provider(session, lat, lon, area)
        if a.get("expires") is None or a["expires"] > now
    ]
    if not active:
        return None
    return max(active, key=lambda a: (SEVERITY_RANK.get(a["severity"], 0), a.get("expires") or 0))


def resolve_alert(region, lat, lon, area="uk"):
    """Alert with cache fallback. Never raises, never blocks the weather."""
    if not region or region == "none":
        return None
    try:
        alert = fetch_alert(region, lat, lon, area)
        save_json(ALERT_FILE, {
            "alert": alert, "region": region, "area": area, "fetched_at": time.time(),
        })
        if alert:
            print(f"Alert: {alert['event']} ({alert['severity']})")
        return alert
    except Exception as e:
        print(f"Alert fetch failed ({region}): {e} — treating as no alert.")

    # The cache is keyed by feed: a cached Canadian warning must never
    # resurface because a UK region code was mistyped.
    cache = load_json(ALERT_FILE)
    if cache.get("region") != region or cache.get("area") != area:
        return None
    cached = cache.get("alert")
    if cached and (cached.get("expires") or 0) > time.time():
        print("Using cached alert.")
        return cached
    return None


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


def fetch_station_location(session):
    """Station coordinates and timezone, for stations the forecast omits them for."""
    try:
        response = session.get(
            f"{API_BASE_URL}/stations/{STATION_ID}",
            params={"token": TOKEN},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        stations = response.json().get("stations", [])
        if not stations:
            return None, None, None
        station = stations[0]
        return (
            _num(station.get("latitude")),
            _num(station.get("longitude")),
            station.get("timezone"),
        )
    except Exception as e:
        print(f"Could not read station location: {e}")
        return None, None, None


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

            # All ten days, not five — the ten-day row needs them.
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

            lat = _num(r_for.get("latitude"))
            lon = _num(r_for.get("longitude"))
            tz_name = r_for.get("timezone")
            if lat is None or lon is None or not tz_name:
                found_lat, found_lon, found_tz = fetch_station_location(session)
                lat = lat if lat is not None else found_lat
                lon = lon if lon is not None else found_lon
                tz_name = tz_name or found_tz

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
                "today_conditions": daily[0].get("conditions", ""),
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
                "lat": lat,
                "lon": lon,
                "timezone": tz_name,
                "daily": forecast_daily,
                "hourly": forecast_hourly,
                "fetched_at": time.time(),
            }
        except Exception as e:
            last_err = e
            print(f"Fetch attempt {attempt+1}/{retries} failed: {e}")

    print(f"All {retries} fetch attempts failed. Last error: {last_err}")
    return None


def fetch_all():
    """Weather plus alerts, with the cache fallbacks both need.

    Shared by the panel and the desktop preview so the two render the same
    thing. The weather is authoritative for the panel; the alert is added
    when it exists and silently skipped when it does not.
    """
    state = load_state()

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

    location = load_location_config()
    lat = location["lat"] if location["lat"] is not None else (weather or {}).get("lat")
    lon = location["lon"] if location["lon"] is not None else (weather or {}).get("lon")
    tz_name = (weather or {}).get("timezone")

    region = location["region"]
    if region == "auto":
        region = detect_region(tz_name, lat, lon)
        if region is None:
            print("No alert provider for this location — alerts disabled.")

    if weather is not None:
        weather["alert"] = resolve_alert(region, lat, lon, location["area"])

    if weather:
        state["payload"] = {k: v for k, v in weather.items() if k != "stale"}
    save_state(state)
    return weather


# ── Headline selection ────────────────────────────────────────────────────────

def select_concern(weather):
    """Highest-priority *active* concern, held for as long as it is active.

    Deliberately not a round-robin: at a 15-minute cadence a rotation shows
    any given item for 15 minutes in every 75, so you can walk up wanting
    the wind and have to wait. Nothing here rotates at all.
    """
    now = weather.get("obs_time") or time.time()

    # 1. Official alert. Also fills the hero beacon.
    alert = weather.get("alert")
    if alert:
        figure = f"until {hhmm(alert.get('expires'))}" if alert.get("expires") else ""
        exact = weather.get("temp")
        if exact is not None:
            figure = f"{figure} {MIDDOT} {exact:.1f}°" if figure else f"{exact:.1f}°"
        return {
            "glyph": alert_glyph(alert.get("event")),
            "headline": (alert.get("event") or "WEATHER ALERT").upper(),
            "figure": figure,
        }

    # 2. Station health.
    obs_time = weather.get("obs_time")
    if obs_time and (time.time() - obs_time) > STATION_SILENT_SECONDS:
        silent_for = int((time.time() - obs_time) / 60)
        return {
            "glyph": WI["na"],
            "headline": "STATION SILENT",
            "figure": f"last {hhmm(obs_time)} {MIDDOT} {silent_for} min",
        }
    battery = weather.get("battery")
    if battery is not None and battery < BATTERY_LOW_VOLTS:
        return {
            "glyph": WI["na"],
            "headline": "BATTERY LOW",
            "figure": f"{battery:.2f} V",
        }

    # 3. Lightning.
    strike_epoch = weather.get("lightning_epoch")
    strike_km = weather.get("lightning_distance")
    if (
        strike_epoch
        and (now - strike_epoch) <= LIGHTNING_RECENT_SECONDS
        and strike_km is not None
        and strike_km <= LIGHTNING_NEAR_KM
    ):
        count = weather.get("lightning_count")
        figure = hhmm(strike_epoch)
        if count:
            figure = f"{int(count)} strikes {MIDDOT} {figure}"
        return {
            "glyph": WI["lightning"],
            "headline": f"LIGHTNING {strike_km:.0f} KM",
            "figure": figure,
        }

    # 4. Precipitation starting or stopping within 3 h.
    transition = precip_transition(weather)
    if transition:
        return transition

    # 5. Gust above threshold.
    gust = weather.get("wind_gust")
    if gust is not None and gust >= GUST_THRESHOLD_KPH:
        direction = get_wind_direction(weather.get("wind_dir"))
        return {
            "glyph": WI["wind"],
            "headline": f"GUSTS {gust:.0f} KM/H",
            "figure": f"{direction} {MIDDOT} avg {fmt_num(weather.get('wind_avg'), 0)}",
        }

    # 6. Frost crossing.
    temp = weather.get("temp")
    dew = weather.get("dew_point")
    low = weather.get("today_low")
    if any(v is not None and v <= 0 for v in (temp, dew, low)):
        return {
            "glyph": WI["snowflake"],
            "headline": "FROST",
            "figure": f"low {fmt_temp(low)} {MIDDOT} dew {fmt_temp(dew)}",
        }

    # 7. Nothing active.
    summary = (weather.get("today_conditions") or "").strip().lower()
    exact = weather.get("temp")
    parts = []
    if summary:
        parts.append(summary)
    if exact is not None:
        parts.append(f"{exact:.1f}° exactly")
    return {
        "glyph": condition_glyph(day_icon(weather)),
        "headline": "ALL CLEAR TODAY",
        "figure": f" {MIDDOT} ".join(parts),
    }


def day_icon(weather):
    days = weather.get("daily") or []
    if days:
        return days[0].get("icon")
    return weather.get("icon_name")


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
    word = "SNOW" if is_snow else "RAIN"
    glyph = WI["snow"] if is_snow else WI["rain"]

    for i in range(1, len(wet)):
        if wet[i] and not wet[i - 1]:
            hour = hours[i]
            return {
                "glyph": glyph,
                "headline": f"{word} FROM {hhmm(hour['time'])}",
                "figure": f"{int(hour['prob'])}%",
            }
        if wet[i - 1] and not wet[i]:
            hour = hours[i]
            return {
                "glyph": glyph,
                "headline": f"{word} STOPS {hhmm(hour['time'])}",
                "figure": f"{int(hours[i-1]['prob'])}% now",
            }

    if wet and wet[0]:
        hour = hours[0]
        return {
            "glyph": glyph,
            "headline": f"{word} NOW",
            "figure": f"{int(hour['prob'])}%",
        }
    return None


# ── Headline copy ─────────────────────────────────────────────────────────────

# Official event names that no generic rule shortens well.
EVENT_ALIASES = {
    "SPECIAL WEATHER STATEMENT": "WEATHER STATEMENT",
    "SEVERE THUNDERSTORM": "T-STORM",
}
WORD_ABBREVIATIONS = {
    "THUNDERSTORM": "T-STORM",
    "PRECIPITATION": "PRECIP",
    "TEMPERATURE": "TEMP",
    "KILOMETRE": "KM",
}
LEVEL_WORDS = ("WARNING", "WATCH", "ADVISORY", "STATEMENT")


def shorten_headline(draw, headline, font, max_width):
    """Make the headline fit on one line without shrinking the type.

    The type size comes from a legibility budget, so when a string is too
    long the copy gives way and never the size. In order: the name as
    issued, then known abbreviations, then the hazard without its level
    word (the beacon is already carrying the level), then the last two
    words, then the level alone, then a truncation.

    Dropping the level rather than the hazard is deliberate — `FREEZING
    RAIN` tells you more than `RAIN WARNING`, and the field behind it is
    already red.
    """
    short = headline
    for long_form, replacement in EVENT_ALIASES.items():
        short = short.replace(long_form, replacement)
    short = " ".join(WORD_ABBREVIATIONS.get(w, w) for w in short.split())

    candidates = [headline, short]
    words = short.split()
    for level in LEVEL_WORDS:
        # Only when something meaningful is left: "WEATHER STATEMENT"
        # collapsing to "WEATHER" would say nothing at all.
        if short.endswith(f" {level}") and len(words) > 2:
            candidates.append(" ".join(words[:-1]))
    if len(words) > 2:
        candidates.append(" ".join(words[-2:]))
    if len(words) > 1:
        candidates.append(words[-1])

    for candidate in candidates:
        if candidate and draw.textlength(candidate, font=font) <= max_width:
            return candidate

    trimmed = candidates[-1]
    while trimmed and draw.textlength(trimmed + DASH, font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + DASH) if trimmed else DASH


def fit_figure(draw, figure, font, max_width):
    """Drop the figure's trailing clauses until it fits its right-hand slot."""
    parts = [p.strip() for p in (figure or "").split(MIDDOT) if p.strip()]
    while parts:
        candidate = f" {MIDDOT} ".join(parts)
        if draw.textlength(candidate, font=font) <= max_width:
            return candidate
        parts.pop()
    return ""


# ── Region 1: hero (y 0-300) ──────────────────────────────────────────────────

def hero_glyph_size(draw, glyph, temp_str, column_width):
    """168 px unless the row would overflow, then as much as fits.

    The design's 168 px assumes a glyph around 186 px wide. Weather Icons
    advances run from 124 px to 237 px at that size, and the composite
    day-rain and partly-cloudy glyphs plus a four-character temperature
    overflow the row by up to 33 px. Type sizes never move, so the glyph
    gives way — a 144 px silhouette still reads at 4.5 m.
    """
    # Two gaps: glyph to temperature, and temperature to the HIGH/LOW column.
    available = WIDTH - 2 * PAD_X - column_width - 2 * HERO_GAP
    available -= tracked_width(draw, temp_str, display(SIZE_TEMP), TRACK_TEMP)
    size = SIZE_HERO_GLYPH
    while size > SIZE_HERO_GLYPH_MIN and glyph_advance(draw, glyph, size) > available:
        size -= 2
    return size


def draw_hero(draw, weather):
    """Glyph, temperature and today's range, on the alert severity field.

    The field is the beacon: a full-bleed 800x296 area of ink has no
    legibility threshold at all, so it stays detectable in peripheral vision
    where no type can. It is also the heaviest possible e-ink refresh, and
    red is among the slowest inks — so it fires only for an official alert,
    never for ordinary conditions.
    """
    alert = weather.get("alert")
    ink = SEVERITY_INK.get((alert or {}).get("severity"), WHITE)
    fg = type_on(ink)

    content_bottom = HERO_Y1 - RULE
    if ink != WHITE:
        draw.rectangle([0, HERO_Y0, WIDTH - 1, content_bottom - 1], fill=ink)
    draw.rectangle([0, content_bottom, WIDTH - 1, HERO_Y1 - 1], fill=BLACK)

    centre_y = (HERO_Y0 + content_bottom) / 2
    right = WIDTH - PAD_X

    # Right-hand HIGH/LOW column, measured first because the hero row is
    # sized around it.
    label_font, value_font = text(SIZE_LABEL), display(SIZE_HILO)
    high_str = fmt_temp(weather.get("today_high"))
    low_str = fmt_temp(weather.get("today_low"))
    column_width = max(
        draw.textlength(high_str, font=value_font),
        draw.textlength(low_str, font=value_font),
        tracked_width(draw, "HIGH", label_font, TRACK_LABEL),
        tracked_width(draw, "LOW", label_font, TRACK_LABEL),
    )

    # 19 + 2 + 46 + 2 + 8 + 19 + 2 + 46, centred on the region.
    column_height = 2 * SIZE_LABEL + 2 * SIZE_HILO + 3 * 2 + 8
    y = centre_y - column_height / 2
    for label, value in (("HIGH", high_str), ("LOW", low_str)):
        draw_tracked(
            draw, right, baseline_for(draw, label, label_font, y + SIZE_LABEL / 2),
            label, label_font, TRACK_LABEL, fill=fg, align="right",
        )
        y += SIZE_LABEL + 2
        draw_centred(
            draw, right, y + SIZE_HILO / 2, value, value_font, fill=fg, anchor_x="r",
        )
        y += SIZE_HILO + 2 + 8

    # Temperature: whole degrees only. 22 degrees, not 21.8 — dropping the
    # decimal is 35 % narrower, which is what funds the size. The exact
    # value is in the band, read at walk-up; nobody decides anything on
    # 0.8 C from a sofa.
    temp = weather.get("temp")
    temp_font = display(SIZE_TEMP)
    if temp is None:
        # An em dash at 228 px is a solid black slab that reads as a
        # redaction rather than as a missing reading, so the no-data mark
        # drops to the tier-2 size on the same baseline.
        temp_str, temp_font, track = DASH, display(SIZE_HILO), 0.0
    else:
        temp_str, track = f"{temp:.0f}°", TRACK_TEMP

    glyph = condition_glyph(weather.get("icon_name"), is_night(weather))
    glyph_size = hero_glyph_size(draw, glyph, temp_str if temp is not None else "", column_width)

    x = PAD_X
    draw_centred(draw, x, centre_y, glyph, icon(glyph_size), fill=fg)
    x += glyph_advance(draw, glyph, glyph_size) + HERO_GAP
    draw_tracked(
        draw, x, baseline_for(draw, temp_str, temp_font, centre_y),
        temp_str, temp_font, track, fill=fg,
    )


# ── Region 2: headline band (y 300-380) ───────────────────────────────────────

def draw_band(draw, weather):
    """One line: what is happening, and the figure that qualifies it.

    White ground in both 5A and 5B — the colour channel belongs to the hero
    field, and a second coloured region would put severity and condition ink
    side by side.
    """
    content_bottom = BAND_Y1 - RULE
    draw.rectangle([0, content_bottom, WIDTH - 1, BAND_Y1 - 1], fill=BLACK)

    concern = select_concern(weather)
    centre_y = (BAND_Y0 + content_bottom) / 2
    right = WIDTH - PAD_X

    x = PAD_X
    glyph = concern["glyph"]
    draw_centred(draw, x, centre_y, glyph, icon(SIZE_BAND_GLYPH))
    x += glyph_advance(draw, glyph, SIZE_BAND_GLYPH) + BAND_GAP

    if weather.get("stale"):
        figure = f"STALE {MIDDOT} {hhmm(weather.get('fetched_at'))}"
    else:
        figure = concern.get("figure") or ""

    figure_font = bold(SIZE_FIGURE)
    figure = fit_figure(draw, figure, figure_font, (WIDTH - 2 * PAD_X) * 0.42)
    figure_width = draw.textlength(figure, font=figure_font) if figure else 0

    headline_font = display(SIZE_HEADLINE)
    headline_max = right - x - (figure_width + BAND_GAP if figure else 0)
    headline = shorten_headline(draw, concern["headline"], headline_font, headline_max)
    draw_centred(draw, x, centre_y, headline, headline_font)

    if figure:
        draw_centred(draw, right, centre_y, figure, figure_font, anchor_x="r")


# ── Region 3: metrics line (y 380-424) ────────────────────────────────────────

def metric_values(weather):
    """The five metrics and their order are fixed forever.

    That is what guarantees nothing is ever missing: the number you want is
    always in the position you last found it. A metric with no data renders
    an em dash — it does not vanish and it is not reordered. There are no
    text labels: the glyph is the label, which is fine at 0.5 m and saves
    48 px of height.

    Lightning has no metric: it is an event, not a standing value, so it
    appears in the headline band at priority 3 and nowhere else.
    """
    dew = weather.get("dew_point")
    rain = weather.get("rain_today")
    wind = weather.get("wind_avg")
    pressure = weather.get("pressure")
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")

    if sunrise and sunset and sunset > sunrise:
        total = int(sunset - sunrise)
        daylight = f"{total // 3600}h{(total % 3600) // 60:02d}"
    else:
        daylight = DASH

    return [
        {"glyph": WI["snowflake"], "value": fmt_temp(dew, 1)},
        {"glyph": WI["raindrop"], "value": fmt_num(rain, 1, "mm")},
        {"glyph": WI["wind"], "value": fmt_num(wind, 0)},
        {
            "glyph": WI["barometer"],
            "value": fmt_num(pressure, 0),
            "trend": weather.get("pressure_trend", "steady") if pressure is not None else None,
        },
        {"glyph": WI["sunrise"], "value": daylight},
    ]


def draw_metrics(draw, weather):
    content_bottom = METRICS_Y1 - RULE
    draw.rectangle([0, content_bottom, WIDTH - 1, METRICS_Y1 - 1], fill=BLACK)

    centre_y = (METRICS_Y0 + content_bottom) / 2
    cell_w = (WIDTH - 2 * PAD_X) / 5
    value_font = display(SIZE_METRIC)

    for i, metric in enumerate(metric_values(weather)):
        x = PAD_X + i * cell_w
        draw_centred(draw, x, centre_y, metric["glyph"], icon(SIZE_METRIC_GLYPH))
        x += glyph_advance(draw, metric["glyph"], SIZE_METRIC_GLYPH) + METRIC_GAP
        draw_centred(draw, x, centre_y, metric["value"], value_font)

        trend = metric.get("trend")
        if trend:
            x += draw.textlength(metric["value"], font=value_font) + 5
            draw_trend_arrow(draw, x, centre_y, 13, trend)


# ── Region 4: ten-day row (y 424-480) ─────────────────────────────────────────

TEN_DAY_H = 18
TEN_HIGH_H = 24
TEN_BAR_H = 8


def draw_tenday(draw, weather):
    """Day, high, and an 8 px bar of condition ink.

    No height encoding and no 0 C rule: there is no vertical room for a
    scale. Condition is carried entirely by the bar's ink and temperature
    entirely by the number, which is the honest split when a channel has to
    go. The ten columns are always drawn, so the geometry cannot move
    between refreshes even if the forecast comes back short.
    """
    days = (weather.get("daily") or [])[:10]
    col_w = (WIDTH - 2 * PAD_X - TEN_GAP * 9) / 10

    day_font = text(SIZE_DAY)
    high_font = display(SIZE_TEN_HIGH)
    day_centre = TEN_Y0 + TEN_DAY_H / 2
    high_centre = TEN_Y0 + TEN_DAY_H + TEN_HIGH_H / 2
    bar_top = TEN_Y0 + TEN_DAY_H + TEN_HIGH_H

    for i in range(10):
        day = days[i] if i < len(days) else {}
        left = PAD_X + i * (col_w + TEN_GAP)
        centre_x = left + col_w / 2

        draw_centred(draw, centre_x, day_centre, day.get("day", DASH), day_font, anchor_x="m")

        high = day.get("high")
        draw_centred(draw, centre_x, high_centre, fmt_temp(high), high_font, anchor_x="m")

        if high is None:
            continue
        box = [round(left), bar_top, round(left + col_w) - 1, bar_top + TEN_BAR_H - 1]
        ink = CATEGORY_INK.get(condition_category(day.get("icon"), high), WHITE)
        if ink == WHITE:
            draw.rectangle(box, fill=WHITE, outline=BLACK, width=2)
        else:
            draw.rectangle(box, fill=ink)


# ── Dashboard ─────────────────────────────────────────────────────────────────

def draw_error_screen(draw, message):
    draw.text(
        (WIDTH // 2, HEIGHT // 2), message,
        fill=BLACK, font=display(34), anchor="mm", align="center",
    )


def create_dashboard(weather, theme_name="inky"):
    """Render layout 5B.

    theme_name is accepted for backwards compatibility with desktop.py;
    the panel and the desktop window render identically, so the desktop
    app is a true preview of what the Inky shows.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    if not weather:
        draw_error_screen(draw, "DATA FETCH ERROR\nCheck Console Logs")
        return img

    try:
        draw_hero(draw, weather)
        draw_band(draw, weather)
        draw_metrics(draw, weather)
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


NEUTRAL_TOLERANCE = 40


def flatten_text_edges(img):
    """Resolve anti-aliased greys to black or white before quantising.

    PIL anti-aliases text, and with DITHER_NONE those grey edge pixels land
    on whichever of the seven inks is nearest in RGB — which for mid-grey is
    orange, so black type on white picks up a coloured fringe that is very
    visible at 228 px. Type on this panel is only ever black or white, so
    any near-neutral pixel is a text edge and is resolved to one or the
    other. Coloured fills are left alone: they are not neutral, and the
    edge between black type and a severity field quantises correctly on its
    own.
    """
    red, green, blue = img.split()
    brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    neutral = ImageChops.difference(brightest, darkest).point(
        lambda v: 255 if v < NEUTRAL_TOLERANCE else 0
    )
    hard = img.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("RGB")
    return Image.composite(hard, img, neutral)


def quantize_for_inky(img):
    return flatten_text_edges(img.convert("RGB")).quantize(
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

def check_alerts():
    """Print what the alert feed resolves to, and what it currently returns.

    The alert path is silent by design — a failure is "no alert" — so this
    is how you tell "nothing is happening" apart from "nothing is wired up".
    """
    location = load_location_config()
    lat, lon, tz_name = location["lat"], location["lon"], None

    # The UK feed is regional, not point-based, so it needs no coordinates.
    needs_point = location["region"] != "uk" and (lat is None or lon is None)
    if needs_point or location["region"] == "auto":
        print("Reading station location from the Tempest API...")
        session = requests.Session()
        found_lat, found_lon, tz_name = fetch_station_location(session)
        lat = lat if lat is not None else found_lat
        lon = lon if lon is not None else found_lon

    region = location["region"]
    source = "configured"
    if region == "auto":
        region = detect_region(tz_name, lat, lon)
        source = "auto-detected"

    print(f"  Coordinates : {lat}, {lon}")
    print(f"  Timezone    : {tz_name or 'unknown'}")
    print(f"  Region      : {region or 'none'} ({source})")
    if region == "uk":
        area = location["area"]
        print(f"  Met Office  : {area} — {UK_REGIONS.get(area, 'UNKNOWN REGION CODE')}")

    if not region or region == "none":
        print("\nAlerts are off. Set ALERT_REGION to ca, us or uk in ~/secrets.py.")
        return

    alert = resolve_alert(region, lat, lon, location["area"])
    if alert is None:
        print("\nNo alert active for this location right now.")
    else:
        print(f"\n  {alert['severity'].upper()}: {alert['event']}")
        print(f"  Area    : {alert.get('area') or 'unspecified'}")
        print(f"  Until   : {hhmm(alert.get('expires'))}")
        print(f"  Beacon  : {SEVERITY_COLOUR_NAME[alert['severity']]}")


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
    parser.add_argument(
        "--check-alerts", action="store_true",
        help="Report which alert feed this station resolves to and what it "
             "returns right now, then exit.",
    )
    args = parser.parse_args()

    if args.check_alerts:
        check_alerts()
        return

    next_due = load_state().get("next_due")
    if not args.force and next_due and time.time() < next_due:
        print(f"Not due until {hhmm(next_due)} — skipping (use --force to override).")
        return

    if INKY_AVAILABLE:
        wait_for_network(timeout=120)

    print("Fetching weather...")
    weather = fetch_all()

    state = load_state()
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

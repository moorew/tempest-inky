"""Tempest Inky dashboard — layout 10.

Renders an 800x480 panel for the Pimoroni Inky Impression 7.3" (7 colour)
from a WeatherFlow Tempest station, plus an optional government alert feed.

The panel is read from a sofa about 4.5 m away, in a dim room. Comfortable
reading needs a cap height of roughly distance / 200, which at 137 ppi is
~122 px — and Jost's caps run ~0.70em, so couch-readable type starts at
170 px. There is no arrangement of 800x480 that makes two text elements
couch-readable, so the panel carries exactly two things at that distance:
a number and a shape. The 172 px temperature and the 100 px condition
glyph. Everything else is a room-distance or walk-up element and is sized
as one.

Two columns split by a 3 px rule at x=362: NOW on the left at full height,
and METRICS / NEXT / LATER stacked on the right at 168 + 160 + 152.

The layout is a grid, not a set of centred boxes. Three rules generate most
of the numbers: one 24 px margin every zone insets by, fixed-width label
columns so values start on shared verticals, and nothing within 12 px of a
rule.

Colour is fill only. The 362x480 left field carries official alert severity
and nothing else; the 12 px forecast bars carry absolute temperature and
nothing else. Condition is carried by glyphs and has no ink of its own. All
type is black, or white on blue.
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

# One family: Jost, a geometric sans in the Futura tradition, which is what
# suits a mid-century room. 600 for every numeral and the headline, 400 for
# every label and sub-line, 300 for the `/` between high and low and
# nothing else. Plus Weather Icons for every glyph — meteorological rather
# than cartoon, and a font, so it hints to the pixel grid at any size
# instead of being resampled from a 712 px PNG.
FONT_SEMIBOLD = os.path.join(ASSETS_ROOT, "Jost-SemiBold.ttf")
FONT_REGULAR = os.path.join(ASSETS_ROOT, "Jost-Regular.ttf")
FONT_LIGHT = os.path.join(ASSETS_ROOT, "Jost-Light.ttf")
FONT_ICON = os.path.join(ASSETS_ROOT, "weathericons.ttf")

WIDTH = 800
HEIGHT = 480

# ── Design tokens ─────────────────────────────────────────────────────────────

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
INK_GREEN = (0, 255, 0)
INK_BLUE = (0, 0, 255)
INK_YELLOW = (255, 255, 0)
INK_ORANGE = (255, 128, 0)
INK_RED = (255, 0, 0)

# Pimoroni Inky Impression 7-colour palette. This is the entire palette.
INKY_PALETTE = [
    BLACK, WHITE, INK_GREEN, INK_BLUE, INK_RED, INK_YELLOW, INK_ORANGE,
]

# Temperature, divergent rather than sequential. A sequential cold-to-hot
# ramp renders the panel blue from November to March, because the palette
# has exactly one cold ink. Here white sits in the comfortable band and ink
# is spent only on departure from it, so a mild week is colourless and
# colour appearing across the room always means something changed.
TEMP_BANDS = [
    (0, INK_BLUE),       # below 0
    (10, INK_GREEN),     # 0-9
    (20, WHITE),         # 10-19, drawn with a 2 px keyline
    (25, INK_YELLOW),    # 20-24
    (30, INK_ORANGE),    # 25-29
    (None, INK_RED),     # 30 and above
]

# The alert ladder. Deliberately reuses inks from the temperature scale: the
# two are separated by region and size — the field is 362x480 and the bars
# are 12 px — and they never occur in the same element.
SEVERITY_INK = {
    "advisory": INK_YELLOW,
    "watch": INK_ORANGE,
    "warning": INK_RED,
}
SEVERITY_RANK = {"advisory": 1, "watch": 2, "warning": 3}
SEVERITY_COLOUR_NAME = {"advisory": "yellow", "watch": "orange", "warning": "red"}

DASH = "—"          # em dash, the "no data" mark
MIDDOT = "·"

# Verified against assets/weathericons.ttf — every one renders, no tofu.
WI = {
    "clear-day": "\uf00d",
    "clear-night": "\uf02e",
    "partly-cloudy-day": "\uf002",
    "partly-cloudy-night": "\uf086",
    "cloudy": "\uf013",
    "overcast": "\uf013",
    "rain": "\uf019",
    "rain-night": "\uf028",
    "day-rain": "\uf008",
    "snow": "\uf01b",
    "snow-night": "\uf02a",
    "day-snow": "\uf00a",
    "sleet": "\uf0b5",
    "thunderstorm": "\uf01e",
    "thunderstorm-night": "\uf02d",
    "fog": "\uf014",
    "fog-night": "\uf04a",
    "wind": "\uf050",
    "humidity": "\uf07a",
    "barometer": "\uf079",
    "sunrise": "\uf051",
    "sunset": "\uf052",
    "snowflake": "\uf076",
    "raindrop": "\uf078",
    "lightning": "\uf016",
    "hot": "\uf072",
    "na": "\uf07b",
}

# ── Geometry ──────────────────────────────────────────────────────────────────
# Two columns split by a 3 px rule. Geometry must stay byte-identical between
# refreshes and between states or the panel ghosts: zone heights, metric
# count and order, and forecast row count never move. Only fills and text
# content are allowed to change.

RULE_ZONE = 3       # column rule and zone dividers
RULE_INNER = 2      # dividers inside a zone, and the cloud-band keyline
MARGIN = 24         # the one margin: every zone in both columns insets by it
CLEARANCE = 12      # nothing sits closer than this to a rule

# The column rule occupies x=362..364; the right column starts at 365.
COLUMN_X = 362
RIGHT_X = COLUMN_X + RULE_ZONE

LEFT_X0 = MARGIN                    # 24
LEFT_X1 = COLUMN_X - MARGIN         # 338
RIGHT_X0 = RIGHT_X + MARGIN         # 389
RIGHT_X1 = WIDTH - MARGIN           # 776

# Right column zones. Load-bearing: an earlier revision of the spec summed
# to 476 and silently squeezed an element, so the sum is asserted rather
# than trusted.
METRICS_H = 168
NEXT_H = 160
LATER_H = 152
assert METRICS_H + NEXT_H + LATER_H == HEIGHT, "right column must sum to 480"

METRICS_Y0, METRICS_Y1 = 0, METRICS_H                       # 0   - 168
NEXT_Y0, NEXT_Y1 = METRICS_Y1, METRICS_Y1 + NEXT_H          # 168 - 328
LATER_Y0, LATER_Y1 = NEXT_Y1, NEXT_Y1 + LATER_H             # 328 - 480

# ── Type scale ────────────────────────────────────────────────────────────────
# Nothing under 19 px anywhere on the panel, no numeric value under 24 px,
# no glyph under 21 px, and tracking never tighter than -0.03em.

SIZE_TEMP = 172
SIZE_HERO_GLYPH = 100
SIZE_HILO = 44          # feels-like, today's high and low
SIZE_SLASH = 26         # the Jost 300 `/` between high and low
SIZE_METRIC = 42
SIZE_NEXT_GLYPH = 32
SIZE_HEADLINE = 31
SIZE_HOUR_TEMP = 24
SIZE_DAY_GLYPH = 23
SIZE_DAY_HIGH = 23
SIZE_HOUR_GLYPH = 21
SIZE_LABEL = 20         # floor: labels, day names, the NEXT sub-line
SIZE_HOUR_TIME = 19     # floor

# Tracking, in pixels, from the design's em values. The 0.16em on the
# micro-labels is both a small-size legibility gain and the most
# recognisably mid-century detail on the panel.
TRACK_LABEL = 0.16 * SIZE_LABEL
TRACK_DAY = 0.1 * SIZE_LABEL
TRACK_HOUR = 0.1 * SIZE_HOUR_TIME
TRACK_TEMP = -0.03 * SIZE_TEMP
TRACK_HEADLINE = -0.01 * SIZE_HEADLINE
TRACK_VALUE = -0.02        # em, applied per size to the tier-2 numerals

# ── Left column: NOW (x 0-361, full height) ───────────────────────────────────
# padding 26 vertical / 24 horizontal, content vertically centred as one
# block. Every box height below is the design's, so the block sums to a
# fixed 416 and the centring cannot drift when a value changes width.

NOW_PAD_Y = 26
NOW_GLYPH_BOX = 108
NOW_TEMP_BOX = 136          # 172 px at line-height 0.78
NOW_TEMP_GAP = 10
NOW_RULE_ABOVE = 32
NOW_RULE_BELOW = 22
NOW_ROW_H = 46              # FEELS and TODAY rows
NOW_ROW_GAP = 14
NOW_LABEL_COL = 96          # fixed label column: values start at x=120
NOW_SLASH_MARGIN = 10

NOW_BLOCK_H = (
    NOW_GLYPH_BOX + NOW_TEMP_GAP + NOW_TEMP_BOX
    + NOW_RULE_ABOVE + RULE_INNER + NOW_RULE_BELOW
    + NOW_ROW_H + NOW_ROW_GAP + NOW_ROW_H
)
assert NOW_BLOCK_H + 2 * NOW_PAD_Y <= HEIGHT, "left column block does not fit"

# ── Right zone A: METRICS (168 px) ────────────────────────────────────────────
# 2x2. The zone is 168 rather than smaller purely to buy the top row its
# clearance from the panel edge — at 144 the labels sat 2 px from it.

METRIC_PAD_L = 24
METRIC_PAD_R = 14
METRIC_LABEL_LINE = SIZE_LABEL
METRIC_LABEL_GAP = 6
METRIC_BLOCK_H = METRIC_LABEL_LINE + METRIC_LABEL_GAP + SIZE_METRIC   # 68

# ── Right zone B: NEXT (160 px) ───────────────────────────────────────────────

NEXT_PAD_TOP = 16
NEXT_PAD_BOTTOM = 12
NEXT_TITLE_H = 32
NEXT_GLYPH_COL = 46
NEXT_SUB_GAP = 6
NEXT_SUB_H = SIZE_LABEL
NEXT_STRIP_GAP = 10         # clearance above the strip's rule
NEXT_HOUR_TIME_H = 20
NEXT_HOUR_GAP = 6
NEXT_HOUR_ROW_H = 25
NEXT_HOUR_GLYPH_COL = 34
NEXT_HOURS = 4

# ── Right zone C: LATER (152 px) ──────────────────────────────────────────────

LATER_PAD_Y = 8
LATER_ROWS = 5
LATER_ROW_GAP = 4
LATER_DAY_COL = 60
LATER_GLYPH_COL = 34
LATER_HIGH_COL = 56
LATER_BAR_H = 12
LATER_BAR_PAD_R = 16

LATER_ROW_H = (
    (LATER_Y1 - LATER_Y0 - 2 * LATER_PAD_Y) - LATER_ROW_GAP * (LATER_ROWS - 1)
) / LATER_ROWS
assert LATER_ROW_H == 24, "forecast rows must be 24 px"

LATER_BAR_X0 = RIGHT_X0 + LATER_DAY_COL + LATER_GLYPH_COL           # 483
LATER_BAR_X1 = RIGHT_X1 - LATER_HIGH_COL - LATER_BAR_PAD_R          # 704
LATER_BAR_MIN = 0.34        # the shortest bar is a third of the track
LATER_BAR_SPAN = 0.66


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


def semibold(size):
    """Jost 600 — every numeral, and the headline."""
    return get_font(FONT_SEMIBOLD, size)


def regular(size):
    """Jost 400 — every label and sub-line."""
    return get_font(FONT_REGULAR, size)


def light(size):
    """Jost 300 — the `/` between high and low, and nothing else."""
    return get_font(FONT_LIGHT, size)


def icon(size):
    return get_font(FONT_ICON, size)


def fonts_loaded():
    """True when the real TTFs are in place rather than PIL's fallback.

    The width assertions below are meaningless against the default bitmap
    font, and a missing font should not stop the panel drawing.
    """
    return all(
        isinstance(get_font(path, 20), ImageFont.FreeTypeFont)
        for path in (FONT_SEMIBOLD, FONT_REGULAR, FONT_LIGHT, FONT_ICON)
    )


def type_on(ink):
    """Black on every ink except blue, which is the only dark one.

    Black on blue measures ~2.4:1 and fails; white on blue is ~8.6:1. No
    severity ink is blue, so the left field is always black type — this is
    here so a future ink cannot silently break the contrast rule.
    """
    return WHITE if ink == INK_BLUE else BLACK


def _rounded(value, decimals):
    """Round for display, without printing a negative zero.

    -0.4 C formats to "-0" at zero decimals, which reads as a distinct
    temperature rather than as zero.
    """
    out = f"{value:.{decimals}f}"
    return out[1:] if out.startswith("-") and float(out) == 0 else out


def fmt_temp(value, decimals=0):
    if value is None:
        return DASH
    return f"{_rounded(value, decimals)}°"


def fmt_num(value, decimals=0, suffix=""):
    if value is None:
        return DASH
    return f"{_rounded(value, decimals)}{suffix}"


def hhmm(epoch):
    if not epoch:
        return DASH
    return time.strftime("%H:%M", time.localtime(epoch))


def sentence_case(value):
    """First letter up, the rest down.

    The headline is sentence case, not caps: all-caps is reserved for the
    tracked micro-labels, it stops the panel shouting twice, and it is what
    makes a long alert name fit — `Snowfall warning` is 240 px in Jost 600
    at 31 px against 497 px for the same string in caps at 42 px.
    """
    text = " ".join((value or "").split())
    return text[:1].upper() + text[1:].lower() if text else ""


# ── Text: tracking and tabular figures ────────────────────────────────────────

DIGITS = "0123456789"


@lru_cache(maxsize=128)
def digit_advance(font):
    """The advance every digit is drawn on, so figures are tabular.

    Jost's digits differ by a third — `1` is 45 units where `0` is 60 — so
    a value shuffles sideways as it changes, which is visible jitter and
    needless extra e-ink repainting. This is what
    `font-variant-numeric: tabular-nums` does in the design file. Doing it
    here rather than through the OpenType `tnum` feature keeps it working on
    a Pillow built without libraqm, which is the common case on a Pi.
    """
    return max(font.getlength(c) for c in DIGITS)


def text_width(content, font, track=0.0):
    """Width of a string as draw_text will lay it out.

    Kerning is ignored, which makes this very slightly wide for lettered
    strings — the fit tests below are the only callers and erring wide is
    the right direction for them.
    """
    if not content:
        return 0.0
    advance = digit_advance(font)
    total = sum(advance if c in DIGITS else font.getlength(c) for c in content)
    return total + track * (len(content) - 1)


def hard_text(draw, xy, content, font, fill):
    """Draw one run of text with no anti-aliased pixels.

    PIL anti-aliases text, and with DITHER_NONE every grey edge pixel lands
    on whichever of the seven inks is nearest in RGB. Mid-grey is nearest to
    orange, and a black-on-yellow edge blend is nearest to red — so type on
    a severity field picks up a coloured fringe that is very visible at
    172 px. Type here is only ever black or white, so the coverage mask is
    thresholded and the fill painted through it. The result is a hard edge,
    which is the correct rendering for a panel with seven flat inks and no
    dithering.
    """
    box = draw.textbbox(xy, content, font=font, anchor="ls")
    left, top = int(box[0]) - 1, int(box[1]) - 1
    size = (int(box[2]) - left + 2, int(box[3]) - top + 2)
    if size[0] <= 0 or size[1] <= 0:
        return
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text(
        (xy[0] - left, xy[1] - top), content, font=font, fill=255, anchor="ls",
    )
    draw.bitmap((left, top), mask.point(lambda v: 255 if v >= 128 else 0), fill=fill)


def draw_text(draw, x, baseline, content, font, track=0.0, fill=BLACK, align="left"):
    """Draw one line on a baseline, with letter-spacing and tabular figures.

    PIL has no tracking and no `tnum`, so both are applied by hand. Digits
    are placed one at a time, centred in the tabular advance; runs of
    anything else are drawn whole when there is no tracking, so the face's
    kerning survives in the headline and the sub-line.

    `stroke_width` is never passed. The old faces used a stroke to fake a
    bold weight, which grows glyphs outward and closes the counters of 8, 6
    and 0; Jost 600 is an actual weight and needs none.
    """
    if not content:
        return
    if align == "right":
        x -= text_width(content, font, track)
    advance = digit_advance(font)
    run = ""

    def flush(x):
        if run:
            hard_text(draw, (x, baseline), run, font, fill)
            x += font.getlength(run)
        return x

    for char in content:
        if char in DIGITS:
            x = flush(x)
            run = ""
            natural = font.getlength(char)
            hard_text(draw, (x + (advance - natural) / 2, baseline), char, font, fill)
            x += advance + track
        elif track:
            x = flush(x)
            run = ""
            hard_text(draw, (x, baseline), char, font, fill)
            x += font.getlength(char) + track
        else:
            run += char
    flush(x)


def baseline_for(draw, ref, font, centre_y):
    """Baseline that centres `ref`'s ink on centre_y.

    PIL's "m" anchor centres between ascender and descender, which sits
    digits visibly low because they have no descender — the design's
    line-heights are doing the same job in CSS. `ref` is deliberately a
    reference string rather than the content: passing "0" keeps a numeric
    row on one baseline whatever the value is, which is what stops the
    panel repainting ink that did not need to move.
    """
    if not ref:
        return centre_y
    box = draw.textbbox((0, 0), ref, font=font, anchor="ls")
    return centre_y - (box[1] + box[3]) / 2


def draw_row(draw, x, centre_y, content, font, track=0.0, fill=BLACK,
             align="left", ref="0"):
    """Draw a line centred vertically on centre_y."""
    if not content:
        return
    draw_text(
        draw, x, baseline_for(draw, ref, font, centre_y),
        content, font, track, fill=fill, align=align,
    )


def draw_glyph(draw, x, centre_y, glyph, size, fill=BLACK):
    """Draw an icon glyph left-aligned at x, centred on its own ink.

    Weather Icons advances vary by more than a factor of two, so a glyph is
    centred on itself rather than on a shared reference.
    """
    if not glyph:
        return
    font = icon(size)
    draw_text(draw, x, baseline_for(draw, glyph, font, centre_y), glyph, font, fill=fill)


def temp_band_ink(value):
    """Absolute temperature to its ink. Divergent, white in the middle."""
    if value is None:
        return WHITE
    for ceiling, ink in TEMP_BANDS:
        if ceiling is None or value < ceiling:
            return ink
    return INK_RED


def is_night(weather):
    now = weather.get("obs_time") or time.time()
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")
    if not sunrise or not sunset:
        return False
    return now < sunrise or now > sunset


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
    if any(k in name for k in ("thunder", "storm", "tornado", "hurricane", "tropical")):
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
            "event": props.get("alert_name_en") or "Alert",
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
            "event": props.get("event") or "Alert",
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
            "event": f"{hazard.strip()} warning",
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

            # Five days: that is what the LATER zone draws.
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
                for day in daily[:5]
            ]

            forecast_hourly = [
                {
                    "time": _num(hour.get("time")),
                    "prob": _num(hour.get("precip_probability")),
                    "type": hour.get("precip_type"),
                    "temp": _num(hour.get("air_temperature")),
                    "icon": hour.get("icon"),
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


# ── The NEXT zone's content ───────────────────────────────────────────────────

def select_concern(weather):
    """Highest-priority *active* concern, held for as long as it is active.

    Deliberately not a round-robin: at a 15-minute cadence a five-item
    rotation shows any given item for 15 minutes in every 75, so you can
    walk up wanting the wind and have to wait. Nothing here rotates.

    Returns a headline in sentence case and a list of sub-line clauses in
    priority order — draw_next drops them from the end until the line fits,
    so the clause that matters most is always the one that survives.
    """
    now = weather.get("obs_time") or time.time()
    exact = weather.get("temp")
    # The hero rounds to whole degrees, so the exact reading lives here.
    exact_clause = f"{exact:.1f}°" if exact is not None else None

    # 1. Official alert. Also lights the beacon.
    alert = weather.get("alert")
    if alert:
        clauses = []
        if alert.get("expires"):
            clauses.append(f"until {hhmm(alert['expires'])}")
        if exact_clause:
            clauses.append(exact_clause)
        if alert.get("area"):
            clauses.append(str(alert["area"]).lower())
        return {
            "glyph": alert_glyph(alert.get("event")),
            "headline": sentence_case(alert.get("event") or "Weather alert"),
            "clauses": clauses,
        }

    # 2. Station health. No ambient state: it appears here or not at all.
    obs_time = weather.get("obs_time")
    if obs_time and (time.time() - obs_time) > STATION_SILENT_SECONDS:
        silent_for = int((time.time() - obs_time) / 60)
        return {
            "glyph": WI["na"],
            "headline": "Station silent",
            "clauses": [f"last {hhmm(obs_time)}", f"{silent_for} min ago"],
        }
    battery = weather.get("battery")
    if battery is not None and battery < BATTERY_LOW_VOLTS:
        return {
            "glyph": WI["na"],
            "headline": "Battery low",
            "clauses": [f"{battery:.2f} V", exact_clause],
        }

    # 3. Lightning inside 15 km in the last 30 minutes.
    strike_epoch = weather.get("lightning_epoch")
    strike_km = weather.get("lightning_distance")
    if (
        strike_epoch
        and (now - strike_epoch) <= LIGHTNING_RECENT_SECONDS
        and strike_km is not None
        and strike_km <= LIGHTNING_NEAR_KM
    ):
        count = weather.get("lightning_count")
        clauses = [f"nearest strike {strike_km:.0f} km", hhmm(strike_epoch)]
        if count:
            clauses.append(f"{int(count)} strikes")
        return {
            "glyph": WI["lightning"],
            "headline": f"Lightning {strike_km:.0f} km",
            "clauses": clauses,
        }

    # 4. Precipitation starting or stopping within three hours.
    transition = precip_transition(weather)
    if transition:
        return transition

    # 5. Gust above the threshold.
    gust = weather.get("wind_gust")
    if gust is not None and gust >= GUST_THRESHOLD_KPH:
        direction = get_wind_direction(weather.get("wind_dir"))
        return {
            "glyph": WI["wind"],
            "headline": f"Gusts {gust:.0f} km/h",
            "clauses": [
                f"{direction} · avg {fmt_num(weather.get('wind_avg'), 0)}",
                exact_clause,
            ],
        }

    # 6. Frost crossing — air temperature or dew point through 0 C.
    dew = weather.get("dew_point")
    low = weather.get("today_low")
    if any(v is not None and v <= 0 for v in (exact, dew, low)):
        return {
            "glyph": WI["snowflake"],
            "headline": "Frost",
            "clauses": [f"low {fmt_temp(low)}", f"dew {fmt_temp(dew)}", exact_clause],
        }

    # 7. Nothing active.
    trend = weather.get("pressure_trend")
    clauses = [
        (weather.get("today_conditions") or "").strip().lower() or None,
        exact_clause,
        f"dew {fmt_temp(dew)}" if dew is not None else None,
        f"pressure {trend}" if trend in ("rising", "falling") else None,
    ]
    return {
        "glyph": condition_glyph(day_icon(weather)),
        "headline": "All clear today",
        "clauses": clauses,
    }


def day_icon(weather):
    days = weather.get("daily") or []
    if days:
        return days[0].get("icon")
    return weather.get("icon_name")


def precip_transition(weather):
    """Precipitation starting or stopping inside the next three hours.

    Times are printed absolutely. The panel is up to 15 minutes stale, so
    "in 3h 25m" is wrong for 14 of every 15 minutes; `18:00` never is.
    """
    hours = [h for h in weather.get("hourly", [])[:4] if h.get("prob") is not None]
    if len(hours) < 2:
        return None

    wet = [h["prob"] >= PRECIP_LIKELY_PCT for h in hours]
    kind = None
    for hour in hours:
        if hour.get("type"):
            kind = str(hour["type"]).lower()
            break
    stormy = any("thunder" in str(h.get("icon") or "").lower() for h in hours)

    if stormy:
        word, glyph = "Storm", WI["thunderstorm"]
    elif kind == "snow":
        word, glyph = "Snow", WI["snow"]
    elif kind == "sleet":
        word, glyph = "Sleet", WI["sleet"]
    else:
        word, glyph = "Rain", WI["rain"]

    exact = weather.get("temp")

    def clauses(hour):
        rain = weather.get("rain_today")
        return [
            f"{int(hour['prob'])}% chance",
            f"{exact:.1f}°" if exact is not None else None,
            f"{fmt_num(rain, 1)} mm so far" if rain is not None else None,
        ]

    for i in range(1, len(wet)):
        if wet[i] and not wet[i - 1]:
            hour = hours[i]
            return {
                "glyph": glyph,
                "headline": f"{word} from {hhmm(hour['time'])}",
                "clauses": clauses(hour),
            }
        if wet[i - 1] and not wet[i]:
            hour = hours[i]
            return {
                "glyph": glyph,
                "headline": f"{word} until {hhmm(hour['time'])}",
                "clauses": clauses(hours[i - 1]),
            }

    if wet and wet[0]:
        return {
            "glyph": glyph,
            "headline": f"{word} now",
            "clauses": clauses(hours[0]),
        }
    return None


# ── Headline copy ─────────────────────────────────────────────────────────────

# Official event names that no generic rule shortens well.
EVENT_ALIASES = {
    "special weather statement": "Weather statement",
    "severe thunderstorm": "Thunderstorm",
}
WORD_ABBREVIATIONS = {
    "precipitation": "precip",
    "temperature": "temp",
    "kilometre": "km",
}
LEVEL_WORDS = ("warning", "watch", "advisory", "statement")


def shorten_headline(draw, headline, font, max_width, track=0.0):
    """Make the headline fit on one line without shrinking the type.

    The type size comes from a legibility budget, so when a string is too
    long the copy gives way and never the size. In order: the name as
    issued, then known abbreviations, then the hazard without its level
    word — the field behind it is already carrying the level — then the
    last two words, then the last word, then a truncation.

    Dropping the level rather than the hazard is deliberate: `Freezing
    rain` tells you more than `Rain warning`, and the panel is already red.
    """
    short = sentence_case(headline)
    for long_form, replacement in EVENT_ALIASES.items():
        if long_form in short.lower():
            short = sentence_case(
                re.sub(long_form, replacement, short, flags=re.IGNORECASE)
            )
    short = sentence_case(
        " ".join(WORD_ABBREVIATIONS.get(w.lower(), w) for w in short.split())
    )

    candidates = [sentence_case(headline), short]
    words = short.split()
    for level in LEVEL_WORDS:
        # Only when something meaningful is left: "Weather statement"
        # collapsing to "Weather" would say nothing at all.
        if short.lower().endswith(f" {level}") and len(words) > 2:
            candidates.append(sentence_case(" ".join(words[:-1])))
    if len(words) > 2:
        candidates.append(sentence_case(" ".join(words[-2:])))
    if len(words) > 1:
        candidates.append(sentence_case(words[-1]))

    for candidate in candidates:
        if candidate and text_width(candidate, font, track) <= max_width:
            return candidate

    trimmed = candidates[-1]
    while trimmed and text_width(trimmed + DASH, font, track) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + DASH) if trimmed else DASH


def fit_clauses(clauses, font, max_width, track=0.0):
    """Drop trailing clauses until the sub-line fits its zone."""
    parts = [str(c).strip() for c in clauses if c]
    while parts:
        candidate = f" {MIDDOT} ".join(parts)
        if text_width(candidate, font, track) <= max_width:
            return candidate
        parts.pop()
    return ""


def next_hours(weather, count=NEXT_HOURS):
    """The next `count` whole hours, skipping the one already in progress."""
    now = weather.get("obs_time") or time.time()
    upcoming = [
        h for h in weather.get("hourly", [])
        if h.get("time") and h["time"] > now
    ]
    return upcoming[:count]


# ── Left column: NOW (x 0-361, full height) ───────────────────────────────────

def draw_now(draw, weather):
    """Condition glyph, temperature and today's range, on the severity field.

    The field is the beacon: a 362x480 area of ink has no legibility
    threshold at all, so it stays detectable in peripheral vision where no
    type can. It is also the heaviest possible e-ink refresh and red is
    among the slowest inks, so it fires only for an official alert and
    never for ordinary conditions — a beacon that lights on every wet day
    means nothing.
    """
    alert = weather.get("alert")
    ink = SEVERITY_INK.get((alert or {}).get("severity"), WHITE)
    fg = type_on(ink)

    if ink != WHITE:
        draw.rectangle([0, 0, COLUMN_X - 1, HEIGHT - 1], fill=ink)
    draw.rectangle([COLUMN_X, 0, COLUMN_X + RULE_ZONE - 1, HEIGHT - 1], fill=BLACK)

    # The block is a fixed 416 px tall whatever the values are, so nothing
    # below it moves when a digit is added.
    y = NOW_PAD_Y + (HEIGHT - 2 * NOW_PAD_Y - NOW_BLOCK_H) / 2

    glyph = condition_glyph(weather.get("icon_name"), is_night(weather))
    draw_glyph(draw, LEFT_X0, y + NOW_GLYPH_BOX / 2, glyph, SIZE_HERO_GLYPH, fill=fg)
    y += NOW_GLYPH_BOX + NOW_TEMP_GAP

    # Whole degrees: 22°, not 21.8°. Dropping the decimal is ~35 % narrower,
    # which is what funds 172 px. The exact value is on the NEXT sub-line,
    # read at walk-up — nobody decides anything on 0.8 C from a sofa.
    temp = weather.get("temp")
    if temp is None:
        # An em dash at 172 px is a solid slab that reads as a redaction
        # rather than as a missing reading, so the no-data mark drops to the
        # tier-2 size on the same baseline.
        temp_str, temp_font, track = DASH, semibold(SIZE_HILO), 0.0
    else:
        temp_str, temp_font, track = fmt_temp(temp), semibold(SIZE_TEMP), TRACK_TEMP
    draw_row(
        draw, LEFT_X0, y + NOW_TEMP_BOX / 2, temp_str, temp_font, track,
        fill=fg, ref="0",
    )
    y += NOW_TEMP_BOX + NOW_RULE_ABOVE

    draw.rectangle(
        [LEFT_X0, round(y), LEFT_X1 - 1, round(y) + RULE_INNER - 1], fill=fg,
    )
    y += RULE_INNER + NOW_RULE_BELOW

    # Two rows on fixed label columns, each label sharing its value's real
    # baseline — the values start on the same vertical at x=120.
    value_font = semibold(SIZE_HILO)
    label_font = regular(SIZE_LABEL)
    track_value = TRACK_VALUE * SIZE_HILO
    value_x = LEFT_X0 + NOW_LABEL_COL

    baseline = baseline_for(draw, "0", value_font, y + NOW_ROW_H / 2)
    draw_text(draw, LEFT_X0, baseline, "FEELS", label_font, TRACK_LABEL, fill=fg)
    draw_text(
        draw, value_x, baseline, fmt_temp(weather.get("feels_like")),
        value_font, track_value, fill=fg,
    )
    y += NOW_ROW_H + NOW_ROW_GAP

    baseline = baseline_for(draw, "0", value_font, y + NOW_ROW_H / 2)
    draw_text(draw, LEFT_X0, baseline, "TODAY", label_font, TRACK_LABEL, fill=fg)
    high = fmt_temp(weather.get("today_high"))
    low = fmt_temp(weather.get("today_low"))
    x = value_x
    draw_text(draw, x, baseline, high, value_font, track_value, fill=fg)
    x += text_width(high, value_font, track_value) + NOW_SLASH_MARGIN
    slash_font = light(SIZE_SLASH)
    draw_text(draw, x, baseline, "/", slash_font, fill=fg)
    x += slash_font.getlength("/") + NOW_SLASH_MARGIN
    draw_text(draw, x, baseline, low, value_font, track_value, fill=fg)


# ── Right zone A: METRICS (y 0-168) ───────────────────────────────────────────

def metric_values(weather):
    """The four metrics and their order are fixed forever.

    That is what guarantees nothing is ever missing: the number you want is
    always in the position you last found it. A metric with no data renders
    an em dash — it does not vanish and it is not reordered.

    Units live in the label, never in the value. A cell is only ~217 px
    wide and `1021 hPa` beside `13h 56m` collides; that was measured, not
    preferred.
    """
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")
    if sunrise and sunset and sunset > sunrise:
        total = int(sunset - sunrise)
        daylight = f"{total // 3600}h{(total % 3600) // 60:02d}"
    else:
        daylight = DASH

    return [
        {"label": "WIND KM/H", "value": fmt_num(weather.get("wind_avg"), 0)},
        {"label": "RAIN MM", "value": fmt_num(weather.get("rain_today"), 1)},
        {"label": "PRESSURE", "value": fmt_num(weather.get("pressure"), 0)},
        {"label": "DAYLIGHT", "value": daylight},
    ]


def draw_metrics(draw, weather):
    """2x2, left-aligned, with 2 px dividers between the cells.

    The content is 68 px tall in an ~82 px cell, and that clearance is the
    whole reason the zone is 168 px rather than smaller: the top row sits
    against the panel edge. At 42 px rather than 46 the values gained 8 px
    of air and read larger for it.
    """
    zone_bottom = METRICS_Y1 - RULE_ZONE
    draw.rectangle([RIGHT_X, zone_bottom, WIDTH - 1, METRICS_Y1 - 1], fill=BLACK)

    divider_x = round((RIGHT_X + WIDTH) / 2 - RULE_INNER / 2)
    divider_y = round((METRICS_Y0 + zone_bottom) / 2 - RULE_INNER / 2)
    draw.rectangle(
        [divider_x, METRICS_Y0, divider_x + RULE_INNER - 1, zone_bottom - 1], fill=BLACK,
    )
    draw.rectangle(
        [RIGHT_X, divider_y, WIDTH - 1, divider_y + RULE_INNER - 1], fill=BLACK,
    )

    columns = [RIGHT_X, divider_x + RULE_INNER]
    rows = [(METRICS_Y0, divider_y), (divider_y + RULE_INNER, zone_bottom)]

    label_font = regular(SIZE_LABEL)
    value_font = semibold(SIZE_METRIC)
    track_value = TRACK_VALUE * SIZE_METRIC

    for i, metric in enumerate(metric_values(weather)):
        x = columns[i % 2] + METRIC_PAD_L
        row_top, row_bottom = rows[i // 2]
        top = row_top + (row_bottom - row_top - METRIC_BLOCK_H) / 2
        draw_row(
            draw, x, top + METRIC_LABEL_LINE / 2, metric["label"],
            label_font, TRACK_LABEL, ref="H",
        )
        draw_row(
            draw, x, top + METRIC_LABEL_LINE + METRIC_LABEL_GAP + SIZE_METRIC / 2,
            metric["value"], value_font, track_value,
        )


# ── Right zone B: NEXT (y 168-328) ────────────────────────────────────────────

def draw_next(draw, weather):
    """What is happening, the figures that qualify it, and the next 4 hours.

    White ground: the colour channel belongs to the left field, and a second
    coloured region would put severity and temperature ink side by side.
    """
    zone_bottom = NEXT_Y1 - RULE_ZONE
    draw.rectangle([RIGHT_X, zone_bottom, WIDTH - 1, NEXT_Y1 - 1], fill=BLACK)

    concern = select_concern(weather)
    content_bottom = zone_bottom - NEXT_PAD_BOTTOM
    y = NEXT_Y0 + NEXT_PAD_TOP

    # Title row: glyph in a fixed 46 px column, then the headline.
    draw_glyph(
        draw, RIGHT_X0, y + NEXT_TITLE_H / 2, concern["glyph"], SIZE_NEXT_GLYPH,
    )
    headline_font = semibold(SIZE_HEADLINE)
    headline_x = RIGHT_X0 + NEXT_GLYPH_COL
    headline = shorten_headline(
        draw, concern["headline"], headline_font,
        RIGHT_X1 - headline_x, TRACK_HEADLINE,
    )
    draw_row(
        draw, headline_x, y + NEXT_TITLE_H / 2, headline, headline_font,
        TRACK_HEADLINE, ref="H",
    )
    y += NEXT_TITLE_H + NEXT_SUB_GAP

    # Sub-line. A stale render says so here and keeps its weather.
    clauses = list(concern["clauses"])
    if weather.get("stale"):
        clauses.insert(0, f"STALE {MIDDOT} {hhmm(weather.get('fetched_at'))}")
    sub_font = regular(SIZE_LABEL)
    sub_line = fit_clauses(clauses, sub_font, RIGHT_X1 - RIGHT_X0)
    draw_row(draw, RIGHT_X0, y + NEXT_SUB_H / 2, sub_line, sub_font, ref="H")

    # Hour strip, pushed to the bottom of the zone under a 2 px rule.
    cell_h = NEXT_HOUR_TIME_H + NEXT_HOUR_GAP + NEXT_HOUR_ROW_H
    cells_top = content_bottom - cell_h
    rule_y = cells_top - NEXT_STRIP_GAP - RULE_INNER
    draw.rectangle(
        [RIGHT_X0, rule_y, RIGHT_X1 - 1, rule_y + RULE_INNER - 1], fill=BLACK,
    )

    cell_w = (RIGHT_X1 - RIGHT_X0) / NEXT_HOURS
    time_font = regular(SIZE_HOUR_TIME)
    temp_font = semibold(SIZE_HOUR_TEMP)
    hours = next_hours(weather)
    for i in range(NEXT_HOURS):
        hour = hours[i] if i < len(hours) else {}
        x = RIGHT_X0 + i * cell_w
        draw_row(
            draw, x, cells_top + NEXT_HOUR_TIME_H / 2,
            hhmm(hour.get("time")), time_font, TRACK_HOUR,
        )
        row_centre = cells_top + NEXT_HOUR_TIME_H + NEXT_HOUR_GAP + NEXT_HOUR_ROW_H / 2
        if hour:
            draw_glyph(
                draw, x, row_centre,
                condition_glyph(hour.get("icon"), is_night(weather)),
                SIZE_HOUR_GLYPH,
            )
        draw_row(
            draw, x + NEXT_HOUR_GLYPH_COL, row_centre,
            fmt_temp(hour.get("temp")), temp_font,
        )


# ── Right zone C: LATER (y 328-480) ───────────────────────────────────────────

def draw_later(draw, weather):
    """Five days: name, condition glyph, temperature bar, high.

    Length and colour answer different questions and stay independent.
    Length is relative to this week's own min and max, so it shows the
    week's shape; fill is absolute against the temperature bands, so it
    shows the week's level. A flat hot week keeps its shape, and a mild
    week spends no ink at all.

    There is no key. Warm-is-warm and blue-is-freezing is a convention
    people already hold, and the high is printed beside every bar, so the
    panel teaches its own scale within a couple of days.
    """
    days = (weather.get("daily") or [])[:LATER_ROWS]
    highs = [d.get("high") for d in days if d.get("high") is not None]
    lowest, highest = (min(highs), max(highs)) if highs else (0.0, 1.0)
    span = (highest - lowest) or 1.0
    track = LATER_BAR_X1 - LATER_BAR_X0

    day_font = regular(SIZE_LABEL)
    high_font = semibold(SIZE_DAY_HIGH)

    # Always five rows, so the geometry cannot move between refreshes even
    # if the forecast comes back short.
    for i in range(LATER_ROWS):
        day = days[i] if i < len(days) else {}
        top = LATER_Y0 + LATER_PAD_Y + i * (LATER_ROW_H + LATER_ROW_GAP)
        centre = top + LATER_ROW_H / 2

        draw_row(draw, RIGHT_X0, centre, day.get("day") or DASH, day_font,
                 TRACK_DAY, ref="H")
        high = day.get("high")
        draw_row(draw, RIGHT_X1, centre, fmt_temp(high), high_font, align="right")
        if high is None:
            continue

        draw_glyph(
            draw, RIGHT_X0 + LATER_DAY_COL, centre,
            condition_glyph(day.get("icon")), SIZE_DAY_GLYPH,
        )

        width = round(
            (LATER_BAR_MIN + (high - lowest) / span * LATER_BAR_SPAN) * track
        )
        bar_top = round(centre - LATER_BAR_H / 2)
        box = [
            LATER_BAR_X0, bar_top,
            LATER_BAR_X0 + width - 1, bar_top + LATER_BAR_H - 1,
        ]
        ink = temp_band_ink(high)
        if ink == WHITE:
            draw.rectangle(box, fill=WHITE, outline=BLACK, width=RULE_INNER)
        else:
            draw.rectangle(box, fill=ink)


# ── Dashboard ─────────────────────────────────────────────────────────────────

def draw_error_screen(draw, message):
    draw.text(
        (WIDTH // 2, HEIGHT // 2), message,
        fill=BLACK, font=semibold(34), anchor="mm", align="center",
    )


def create_dashboard(weather, theme_name="inky"):
    """Render layout 10.

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
        draw_now(draw, weather)
        draw_metrics(draw, weather)
        draw_next(draw, weather)
        draw_later(draw, weather)
    except Exception as e:
        print(f"Error drawing dashboard: {e}")
        img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
        draw = ImageDraw.Draw(img)
        draw_error_screen(draw, "RENDER ERROR\nCheck Console Logs")

    return img


def check_widths():
    """Assert the two strings most likely to overflow actually fit.

    The hero temperature's widest realistic value and the longest alert
    name in the design set. Both are inside a budget rather than measured
    at draw time, and a budget that is never checked is a budget that has
    already been broken once.
    """
    if not fonts_loaded():
        print("Fonts missing — skipping the width checks.")
        return
    hero = text_width("-19°", semibold(SIZE_TEMP), TRACK_TEMP)
    assert hero <= LEFT_X1 - LEFT_X0, f"hero temperature overflows: {hero:.0f} px"

    headline = text_width(
        "Snowfall warning", semibold(SIZE_HEADLINE), TRACK_HEADLINE,
    )
    budget = RIGHT_X1 - RIGHT_X0 - NEXT_GLYPH_COL
    assert headline <= budget, f"headline overflows: {headline:.0f} px of {budget}"


check_widths()


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
    visible at 182 px. Type on this panel is only ever black or white, so
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


# ── Canned scenarios ──────────────────────────────────────────────────────────
# The five states from the design file, so every one can be checked without
# waiting for the weather. These never touch the network.

SCENARIO_ICONS = {
    "clear": "clear-day",
    "partly": "partly-cloudy-day",
    "cloud": "cloudy",
    "rain": "rain",
    "snow": "snow",
    "storm": "thunderstorm",
}

SCENARIOS = {
    "quiet": {
        "temp": 21.8, "feels": 24.4, "high": 30.0, "low": 18.0, "dew": 18.0,
        "icon": "partly", "conditions": "dry all day",
        "wind": 0.0, "rain": 0.0, "pressure": 1021.0, "daylight": "13h56",
        "alert": None,
        "hours": [("partly", 24.0, 5), ("clear", 24.0, 0),
                  ("partly", 22.0, 0), ("cloud", 21.0, 10)],
        "days": [("clear", 30.0), ("clear", 31.0), ("clear", 28.0),
                 ("cloud", 26.0), ("rain", 24.0)],
    },
    "storm": {
        "temp": 25.4, "feels": 27.1, "high": 26.0, "low": 17.0, "dew": 19.0,
        "icon": "storm", "conditions": "thunderstorms",
        "wind": 34.0, "rain": 4.2, "pressure": 998.0, "daylight": "15h12",
        "alert": {"severity": "watch", "event": "Storm watch", "area": "", "hours": 4},
        "hours": [("cloud", 25.0, 30), ("rain", 24.0, 70),
                  ("rain", 22.0, 80), ("storm", 21.0, 90)],
        "days": [("storm", 24.0), ("rain", 26.0), ("rain", 22.0),
                 ("cloud", 19.0), ("clear", 23.0)],
    },
    "snow": {
        "temp": -9.2, "feels": -14.3, "high": -8.0, "low": -17.0, "dew": -13.0,
        "icon": "snow", "conditions": "snow all day",
        "wind": 41.0, "rain": 0.0, "pressure": 994.0, "daylight": "8h36",
        "alert": {"severity": "warning", "event": "Snowfall warning",
                  "area": "", "hours": 14},
        "hours": [("cloud", -9.0, 20), ("snow", -10.0, 80),
                  ("snow", -12.0, 90), ("snow", -13.0, 90)],
        "days": [("snow", -8.0), ("snow", -11.0), ("cloud", -11.0),
                 ("clear", -13.0), ("cloud", -10.0)],
    },
    "rain": {
        "temp": 11.3, "feels": 9.4, "high": 13.0, "low": 8.0, "dew": 10.0,
        "icon": "rain", "conditions": "rain easing this evening",
        "wind": 22.0, "rain": 12.6, "pressure": 1002.0, "daylight": "11h08",
        "alert": None,
        "hours": [("rain", 11.0, 90), ("rain", 11.0, 90),
                  ("rain", 10.0, 80), ("cloud", 10.0, 20)],
        "days": [("rain", 12.0), ("rain", 11.0), ("cloud", 13.0),
                 ("rain", 10.0), ("cloud", 14.0)],
    },
    "heat": {
        "temp": 33.4, "feels": 36.2, "high": 34.0, "low": 21.0, "dew": 20.0,
        "icon": "clear", "conditions": "clear and very hot",
        "wind": 8.0, "rain": 0.0, "pressure": 1018.0, "daylight": "15h44",
        "alert": {"severity": "advisory", "event": "Heat advisory",
                  "area": "", "hours": 5},
        "hours": [("clear", 33.0, 0), ("clear", 32.0, 0),
                  ("clear", 30.0, 0), ("partly", 28.0, 0)],
        "days": [("clear", 33.0), ("clear", 34.0), ("clear", 35.0),
                 ("clear", 32.0), ("clear", 29.0)],
    },
}


def scenario_weather(name):
    """A payload shaped exactly like fetch_weather's, from canned data."""
    spec = SCENARIOS[name]
    clock = time.localtime()
    midday = time.mktime((clock.tm_year, clock.tm_mon, clock.tm_mday,
                          12, 0, 0, 0, 0, -1))
    now = midday + 3.5 * 3600        # 15:30, so the strip starts at 16:00

    hours, minutes = spec["daylight"].split("h")
    daylight = int(hours) * 3600 + int(minutes) * 60

    alert = None
    if spec["alert"]:
        alert = dict(spec["alert"])
        alert["expires"] = now + alert.pop("hours") * 3600

    return {
        "temp": spec["temp"],
        "feels_like": spec["feels"],
        "condition": spec["conditions"],
        "icon_name": SCENARIO_ICONS[spec["icon"]],
        "obs_time": now,
        "today_high": spec["high"],
        "today_low": spec["low"],
        "today_conditions": spec["conditions"],
        "dew_point": spec["dew"],
        "wind_avg": spec["wind"],
        "wind_gust": spec["wind"] * 1.3,
        "wind_dir": 225,
        "pressure": spec["pressure"],
        "pressure_trend": "falling" if spec["pressure"] < 1000 else "steady",
        "rain_today": spec["rain"],
        "humidity": 70,
        "battery": 2.72,
        "lightning_count": None,
        "lightning_distance": None,
        "lightning_epoch": None,
        "sunrise": midday - daylight / 2,
        "sunset": midday + daylight / 2,
        "daily": [
            {
                "day": time.strftime("%a", time.localtime(now + i * 86400)).upper(),
                "high": high,
                "low": high - 8,
                "icon": SCENARIO_ICONS[cat],
                "conditions": spec["conditions"],
                "precip_prob": None,
            }
            for i, (cat, high) in enumerate(spec["days"])
        ],
        "hourly": [
            {
                "time": midday + (4 + i) * 3600,
                "prob": prob,
                "type": "snow" if cat == "snow" else "rain",
                "temp": temp,
                "icon": SCENARIO_ICONS[cat],
            }
            for i, (cat, temp, prob) in enumerate(spec["hours"])
        ],
        "alert": alert,
        "fetched_at": now,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Render the Tempest Inky dashboard.")
    parser.add_argument(
        "--force", action="store_true",
        help="Render now, ignoring the adaptive refresh schedule.",
    )
    parser.add_argument(
        "--output", default="dashboard-preview.png",
        help="Where to save the render when no Inky panel is attached. "
             "PNG, not JPEG: the panel renders 7 flat inks, and JPEG "
             "ringing turns those into thousands of colours.",
    )
    parser.add_argument(
        "--preview", metavar="PATH",
        help="Write the rendered PNG to PATH instead of pushing it to the "
             "panel, so the layout can be checked with no display attached. "
             "Ignores the refresh schedule and leaves it untouched.",
    )
    parser.add_argument(
        "--scenario", choices=sorted(SCENARIOS),
        help="Render from canned data matching the design file's scenarios "
             "instead of fetching, so every state can be checked without "
             "waiting for the weather.",
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

    # A preview or a scenario is a rendering job, not a refresh: it neither
    # consults the schedule nor moves it on.
    render_only = bool(args.preview or args.scenario)

    if args.scenario:
        weather = scenario_weather(args.scenario)
    else:
        if not render_only:
            next_due = load_state().get("next_due")
            if not args.force and next_due and time.time() < next_due:
                print(f"Not due until {hhmm(next_due)} — skipping "
                      f"(use --force to override).")
                return
            if INKY_AVAILABLE:
                wait_for_network(timeout=120)

        print("Fetching weather...")
        weather = fetch_all()

        if not render_only:
            state = load_state()
            state["next_due"] = time.time() + refresh_interval_minutes(weather) * 60
            save_state(state)

    img = quantize_for_inky(create_dashboard(weather, theme_name="inky"))

    if args.preview:
        img.save(args.preview)
        print(f"Saved {args.preview}")
        return

    if INKY_AVAILABLE:
        try:
            panel = auto()
            panel.set_image(img)
            panel.show()
            print("Display updated.")
        except Exception as e:
            print(f"Display error: {e}")
            raise   # let systemd record the failure
    else:
        img.save(args.output)
        print(f"Saved {args.output}")


if __name__ == "__main__":
    main()

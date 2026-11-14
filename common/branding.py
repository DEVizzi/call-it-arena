"""
Call It Arena — Brand Identity, Palette, Fonts, Flag Assets & Channel Art Generator.

Visual Identity:
- Background: #0F0F19 (RGB: 15, 15, 25)
- Text Primary: #FFFFFF (Bold White)
- Text Secondary: #98A2B3 (Muted Silver)
- Accent Red: #FF3B5C
- Accent Blue: #2E90FA
- Accent Green: #12B76A
- Accent Yellow: #F79009
- Bundled OFL Fonts: BebasNeue-Regular.ttf (Titles), Inter-Bold.ttf / Inter-Regular.ttf (Body)
"""

import logging
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("call_it_arena")

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
FLAGS_DIR = ASSETS_DIR / "flags"
MUSIC_DIR = ASSETS_DIR / "music"
SFX_DIR = ASSETS_DIR / "sfx"
STATE_DIR = BASE_DIR / "state"
OUTPUT_DIR = BASE_DIR / "output"
BRANDING_DIR = BASE_DIR / "branding"
REPORTS_DIR = BASE_DIR / "reports"

# Video Dimensions & Frame Rate
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30

# Brand Colors (RGB tuples & Hex)
BG_HEX = "#0F0F19"
BG_COLOR: Tuple[int, int, int] = (15, 15, 25)
PANEL_BG_COLOR: Tuple[int, int, int] = (26, 27, 46)
PANEL_BORDER_COLOR: Tuple[int, int, int] = (52, 56, 88)
TEXT_WHITE: Tuple[int, int, int] = (255, 255, 255)
TEXT_MUTED: Tuple[int, int, int] = (160, 170, 195)

ACCENT_RED: Tuple[int, int, int] = (255, 59, 92)
ACCENT_BLUE: Tuple[int, int, int] = (46, 144, 250)
ACCENT_GREEN: Tuple[int, int, int] = (18, 183, 106)
ACCENT_YELLOW: Tuple[int, int, int] = (247, 144, 9)

ACCENT_PALETTE: List[Tuple[int, int, int]] = [
    ACCENT_RED,
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_YELLOW,
]

BRAND_NAME = "CALL IT ARENA"
BRAND_HANDLE = "@callitarena"
BRAND_TAGLINE = "Call it before the reveal. 👇"

# Core 16 League Countries + 8 Extra Global Quiz Countries
COUNTRIES_META: Dict[str, Dict[str, object]] = {
    "IN": {"name": "India", "emoji": "🇮🇳", "colors": [(255, 153, 51), (255, 255, 255), (19, 136, 8)], "capital": "New Delhi", "fact": "India has the world's largest postal network with over 155,000 post offices, including a floating one in Kashmir."},
    "US": {"name": "USA", "emoji": "🇺🇸", "colors": [(178, 34, 52), (255, 255, 255), (60, 59, 110)], "capital": "Washington, D.C.", "fact": "The current 50-star US flag was designed by a 17-year-old high school student, Robert G. Heft, for a class project."},
    "BR": {"name": "Brazil", "emoji": "🇧🇷", "colors": [(0, 156, 59), (255, 223, 0), (0, 39, 118)], "capital": "Brasília", "fact": "Brazil's flag depicts the night sky over Rio de Janeiro on November 15, 1889, with 27 stars representing its states."},
    "ID": {"name": "Indonesia", "emoji": "🇮🇩", "colors": [(255, 0, 0), (255, 255, 255)], "capital": "Jakarta", "fact": "Indonesia is the world's largest archipelago, spanning more than 17,000 islands across three time zones."},
    "PH": {"name": "Philippines", "emoji": "🇵🇭", "colors": [(0, 56, 168), (206, 17, 38), (252, 209, 22)], "capital": "Manila", "fact": "The eight rays of the sun on the Philippine flag represent the first eight provinces that revolted against Spain."},
    "PK": {"name": "Pakistan", "emoji": "🇵🇰", "colors": [(1, 65, 28), (255, 255, 255)], "capital": "Islamabad", "fact": "Pakistan is home to K2, the second-highest mountain on Earth, and five of the world's fourteen 8,000-meter peaks."},
    "BD": {"name": "Bangladesh", "emoji": "🇧🇩", "colors": [(0, 106, 78), (244, 42, 65)], "capital": "Dhaka", "fact": "The red disc on Bangladesh's flag is slightly offset toward the hoist so it appears centered when flying in the wind."},
    "NG": {"name": "Nigeria", "emoji": "🇳🇬", "colors": [(0, 135, 81), (255, 255, 255), (0, 135, 81)], "capital": "Abuja", "fact": "Nigeria's green stripes represent its lush forests and agriculture, while the white band stands for peace and unity."},
    "MX": {"name": "Mexico", "emoji": "🇲🇽", "colors": [(0, 104, 71), (255, 255, 255), (206, 17, 38)], "capital": "Mexico City", "fact": "Mexico's coat of arms depicts an Aztec legend: an eagle devouring a serpent perched atop a prickly pear cactus."},
    "GB": {"name": "UK", "emoji": "🇬🇧", "colors": [(1, 33, 105), (200, 16, 46), (255, 255, 255)], "capital": "London", "fact": "The Union Jack combines the crosses of St George (England), St Andrew (Scotland), and St Patrick (Ireland)."},
    "DE": {"name": "Germany", "emoji": "🇩🇪", "colors": [(0, 0, 0), (221, 0, 0), (255, 206, 0)], "capital": "Berlin", "fact": "Germany's black, red, and gold colors trace back to the uniforms of the Lützow Free Corps in the early 1800s."},
    "JP": {"name": "Japan", "emoji": "🇯🇵", "colors": [(255, 255, 255), (188, 0, 45)], "capital": "Tokyo", "fact": "Japan's flag is officially called 'Nisshoki' (Sun-Mark Flag), though it is popularly known as 'Hinomaru' (Circle of the Sun)."},
    "TR": {"name": "Turkey", "emoji": "🇹🇷", "colors": [(227, 10, 23), (255, 255, 255)], "capital": "Ankara", "fact": "Istanbul in Turkey is the only major city in the world that straddles two continents: Europe and Asia."},
    "EG": {"name": "Egypt", "emoji": "🇪🇬", "colors": [(206, 17, 38), (255, 255, 255), (0, 0, 0)], "capital": "Cairo", "fact": "At the center of Egypt's flag sits the golden Eagle of Saladin, a symbol of strength dating back to the 12th century."},
    "VN": {"name": "Vietnam", "emoji": "🇻🇳", "colors": [(218, 37, 29), (255, 255, 0)], "capital": "Hanoi", "fact": "The five points of the golden star on Vietnam's flag represent farmers, workers, intellectuals, youth, and soldiers."},
    "CA": {"name": "Canada", "emoji": "🇨🇦", "colors": [(255, 0, 0), (255, 255, 255), (255, 0, 0)], "capital": "Ottawa", "fact": "Canada has more lakes than the rest of the world combined—over 60% of all natural lakes on Earth are in Canada."},
    "FR": {"name": "France", "emoji": "🇫🇷", "colors": [(0, 85, 164), (255, 255, 255), (239, 65, 53)], "capital": "Paris", "fact": "France covers 12 different time zones across the globe—more than any other country—due to its overseas territories."},
    "IT": {"name": "Italy", "emoji": "🇮🇹", "colors": [(0, 146, 70), (255, 255, 255), (206, 43, 55)], "capital": "Rome", "fact": "Italy holds the most UNESCO World Heritage Sites of any country in the world, with nearly 60 protected landmarks."},
    "KR": {"name": "South Korea", "emoji": "🇰🇷", "colors": [(255, 255, 255), (205, 46, 58), (0, 71, 160)], "capital": "Seoul", "fact": "South Korea's flag, the Taegeukgi, features four black trigrams representing sky, fire, water, and earth."},
    "AR": {"name": "Argentina", "emoji": "🇦🇷", "colors": [(116, 172, 223), (255, 255, 255), (246, 180, 14)], "capital": "Buenos Aires", "fact": "The 'Sun of May' at the center of Argentina's flag has 32 rays—16 straight and 16 wavy—modeled after the Incan sun god Inti."},
}


def resolve_safe_path(base_dir: Path, relative_path: str) -> Path:
    """
    Strictly resolves a path inside `base_dir` and prevents path traversal (`../`).
    """
    base_resolved = base_dir.resolve()
    clean_rel = os.path.normpath(relative_path).lstrip(os.sep)
    candidate = (base_resolved / clean_rel).resolve()
    if candidate != base_resolved and not str(candidate).startswith(str(base_resolved) + os.sep):
        raise ValueError(f"Path traversal blocked: {relative_path} escapes {base_resolved}")
    return candidate


def sanitize_username(username: str) -> str:
    """
    Sanitizes an external social media username to a safe alphanumeric/underscore string
    before rendering onto frames or storing in state.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", (username or "").strip().lstrip("@"))
    return f"@{cleaned[:28]}" if cleaned else "@arena_fan"


def get_font(size: int, title: bool = False, bold: bool = True) -> ImageFont.FreeTypeFont:
    """
    Loads the bundled OFL font (Bebas Neue for titles, Inter for body) with safe fallbacks.
    """
    candidates: List[Path] = []
    if title:
        candidates.append(FONTS_DIR / "BebasNeue-Regular.ttf")
    if bold:
        candidates.append(FONTS_DIR / "Inter-Bold.ttf")
    candidates.append(FONTS_DIR / "Inter-Regular.ttf")
    candidates.append(FONTS_DIR / "BebasNeue-Regular.ttf")

    for path in candidates:
        if path.exists() and path.stat().st_size > 1000:
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue

    for sys_font in (
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(sys_font):
            try:
                return ImageFont.truetype(sys_font, size=size)
            except Exception:
                continue

    return ImageFont.load_default()


def _draw_star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r_outer: float, color: Tuple[int, int, int], points: int = 5) -> None:
    r_inner = r_outer * 0.4
    coords = []
    for i in range(points * 2):
        angle = -math.pi / 2 + i * math.pi / points
        r = r_outer if i % 2 == 0 else r_inner
        coords.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    draw.polygon(coords, fill=color)


def render_vector_flag_png(code: str, width: int = 640, height: int = 420) -> Image.Image:
    """
    Generates a crisp, high-resolution flag image for the given ISO-2 country code.
    Used when pre-populating `/assets/flags/{code}.png` or if a flag PNG is missing.
    """
    code = code.upper()
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    if code == "IN":
        h3 = height // 3
        draw.rectangle([0, 0, width, h3], fill=(255, 153, 51))
        draw.rectangle([0, h3, width, 2 * h3], fill=(255, 255, 255))
        draw.rectangle([0, 2 * h3, width, height], fill=(19, 136, 8))
        r = int(h3 * 0.4)
        cx, cy = width // 2, height // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 0, 128), width=6)
        for i in range(24):
            ang = i * (2 * math.pi / 24)
            draw.line([cx, cy, cx + math.cos(ang) * r, cy + math.sin(ang) * r], fill=(0, 0, 128), width=2)
    elif code == "US":
        stripe_h = height / 13.0
        for i in range(13):
            color = (178, 34, 52) if i % 2 == 0 else (255, 255, 255)
            draw.rectangle([0, int(i * stripe_h), width, int((i + 1) * stripe_h) + 1], fill=color)
        canton_w, canton_h = int(width * 0.42), int(stripe_h * 7)
        draw.rectangle([0, 0, canton_w, canton_h], fill=(60, 59, 110))
        for row in range(5):
            for col in range(6):
                sx = int((col + 0.5) * (canton_w / 6))
                sy = int((row + 0.5) * (canton_h / 5))
                _draw_star(draw, sx, sy, 8, (255, 255, 255))
    elif code == "BR":
        draw.rectangle([0, 0, width, height], fill=(0, 156, 59))
        mx, my = int(width * 0.1), int(height * 0.12)
        draw.polygon([(width // 2, my), (width - mx, height // 2), (width // 2, height - my), (mx, height // 2)], fill=(255, 223, 0))
        r = int(height * 0.25)
        cx, cy = width // 2, height // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 39, 118))
        draw.arc([cx - int(r * 1.3), cy - int(r * 0.4), cx + int(r * 1.3), cy + int(r * 1.8)], start=200, end=340, fill=(255, 255, 255), width=12)
    elif code == "ID":
        draw.rectangle([0, 0, width, height // 2], fill=(239, 51, 64))
        draw.rectangle([0, height // 2, width, height], fill=(255, 255, 255))
    elif code == "PH":
        draw.rectangle([0, 0, width, height // 2], fill=(0, 56, 168))
        draw.rectangle([0, height // 2, width, height], fill=(206, 17, 38))
        draw.polygon([(0, 0), (int(width * 0.45), height // 2), (0, height)], fill=(255, 255, 255))
        _draw_star(draw, int(width * 0.16), height // 2, 26, (252, 209, 22), points=8)
    elif code == "PK":
        draw.rectangle([0, 0, int(width * 0.25), height], fill=(255, 255, 255))
        draw.rectangle([int(width * 0.25), 0, width, height], fill=(1, 65, 28))
        cx, cy = int(width * 0.62), height // 2
        r = int(height * 0.26)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255))
        draw.ellipse([cx - r + 26, cy - r - 18, cx + r + 26, cy + r - 18], fill=(1, 65, 28))
        _draw_star(draw, cx + 42, cy - 28, 22, (255, 255, 255))
    elif code == "BD":
        draw.rectangle([0, 0, width, height], fill=(0, 106, 78))
        cx, cy = int(width * 0.45), height // 2
        r = int(height * 0.3)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(244, 42, 65))
    elif code == "NG":
        w3 = width // 3
        draw.rectangle([0, 0, w3, height], fill=(0, 135, 81))
        draw.rectangle([w3, 0, 2 * w3, height], fill=(255, 255, 255))
        draw.rectangle([2 * w3, 0, width, height], fill=(0, 135, 81))
    elif code == "MX":
        w3 = width // 3
        draw.rectangle([0, 0, w3, height], fill=(0, 104, 71))
        draw.rectangle([w3, 0, 2 * w3, height], fill=(255, 255, 255))
        draw.rectangle([2 * w3, 0, width, height], fill=(206, 17, 38))
        cx, cy = width // 2, height // 2
        draw.ellipse([cx - 38, cy - 38, cx + 38, cy + 38], fill=(139, 90, 43), outline=(0, 104, 71), width=5)
    elif code == "GB":
        draw.rectangle([0, 0, width, height], fill=(1, 33, 105))
        draw.line([(0, 0), (width, height)], fill=(255, 255, 255), width=64)
        draw.line([(width, 0), (0, height)], fill=(255, 255, 255), width=64)
        draw.line([(0, 0), (width, height)], fill=(200, 16, 46), width=32)
        draw.line([(width, 0), (0, height)], fill=(200, 16, 46), width=32)
        cx, cy = width // 2, height // 2
        draw.rectangle([cx - 52, 0, cx + 52, height], fill=(255, 255, 255))
        draw.rectangle([0, cy - 52, width, cy + 52], fill=(255, 255, 255))
        draw.rectangle([cx - 30, 0, cx + 30, height], fill=(200, 16, 46))
        draw.rectangle([0, cy - 30, width, cy + 30], fill=(200, 16, 46))
    elif code == "DE":
        h3 = height // 3
        draw.rectangle([0, 0, width, h3], fill=(0, 0, 0))
        draw.rectangle([0, h3, width, 2 * h3], fill=(221, 0, 0))
        draw.rectangle([0, 2 * h3, width, height], fill=(255, 206, 0))
    elif code == "JP":
        draw.rectangle([0, 0, width, height], fill=(255, 255, 255))
        cx, cy = width // 2, height // 2
        r = int(height * 0.3)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(188, 0, 45))
    elif code == "TR":
        draw.rectangle([0, 0, width, height], fill=(227, 10, 23))
        cx, cy = int(width * 0.42), height // 2
        r = int(height * 0.25)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255))
        draw.ellipse([cx - r + 24, cy - r + 16, cx + r + 8, cy + r - 16], fill=(227, 10, 23))
        _draw_star(draw, cx + r + 24, cy, 24, (255, 255, 255))
    elif code == "EG":
        h3 = height // 3
        draw.rectangle([0, 0, width, h3], fill=(206, 17, 38))
        draw.rectangle([0, h3, width, 2 * h3], fill=(255, 255, 255))
        draw.rectangle([0, 2 * h3, width, height], fill=(0, 0, 0))
        _draw_star(draw, width // 2, height // 2, 28, (192, 147, 0))
    elif code == "VN":
        draw.rectangle([0, 0, width, height], fill=(218, 37, 29))
        _draw_star(draw, width // 2, height // 2, int(height * 0.28), (255, 255, 0))
    elif code == "CA":
        w4 = width // 4
        draw.rectangle([0, 0, w4, height], fill=(255, 0, 0))
        draw.rectangle([w4, 0, 3 * w4, height], fill=(255, 255, 255))
        draw.rectangle([3 * w4, 0, width, height], fill=(255, 0, 0))
        _draw_star(draw, width // 2, height // 2, int(height * 0.26), (255, 0, 0), points=7)
    elif code == "FR":
        w3 = width // 3
        draw.rectangle([0, 0, w3, height], fill=(0, 85, 164))
        draw.rectangle([w3, 0, 2 * w3, height], fill=(255, 255, 255))
        draw.rectangle([2 * w3, 0, width, height], fill=(239, 65, 53))
    elif code == "IT":
        w3 = width // 3
        draw.rectangle([0, 0, w3, height], fill=(0, 146, 70))
        draw.rectangle([w3, 0, 2 * w3, height], fill=(255, 255, 255))
        draw.rectangle([2 * w3, 0, width, height], fill=(206, 43, 55))
    elif code == "KR":
        draw.rectangle([0, 0, width, height], fill=(255, 255, 255))
        cx, cy = width // 2, height // 2
        r = int(height * 0.25)
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], 180, 360, fill=(205, 46, 58))
        draw.pieslice([cx - r, cy - r, cx + r, cy + r], 0, 180, fill=(0, 71, 160))
    else:
        meta = COUNTRIES_META.get(code, {})
        cols = list(meta.get("colors", [(46, 144, 250), (255, 255, 255), (46, 144, 250)]))
        h_band = height // len(cols)
        for idx, c in enumerate(cols):
            draw.rectangle([0, idx * h_band, width, (idx + 1) * h_band], fill=tuple(c))

    return img


def ensure_flag_assets() -> Dict[str, Path]:
    """
    Ensures high-res PNG flags exist in `/assets/flags/{code.lower()}.png` for all supported countries.
    Downloads from `lipis/flag-icons` (MIT) if available or renders deterministic vector PNGs.
    """
    FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for code in COUNTRIES_META:
        target = FLAGS_DIR / f"{code.lower()}.png"
        if not target.exists() or target.stat().st_size < 500:
            img = render_vector_flag_png(code, width=640, height=420)
            img.save(target, format="PNG")
        paths[code] = target
    return paths


def load_flag_image(code: str, size: Optional[Tuple[int, int]] = None) -> Image.Image:
    """
    Loads a country flag image from `/assets/flags/{code.lower()}.png` (generating it if missing).
    """
    ensure_flag_assets()
    code_up = code.upper()
    path = FLAGS_DIR / f"{code_up.lower()}.png"
    if path.exists():
        img = Image.open(path).convert("RGBA")
    else:
        img = render_vector_flag_png(code_up).convert("RGBA")
    if size:
        img = img.resize(size, Image.Resampling.LANCZOS)
    return img


def load_circular_flag_icon(code: str, diameter: int, border_color: Tuple[int, int, int] = TEXT_WHITE, border_width: int = 3) -> Image.Image:
    """
    Creates a circular masked country flag icon with a glowing border for marbles and leaderboards.
    """
    flag = load_flag_image(code, size=(diameter, diameter))
    # Crop/fit center square
    mask = Image.new("L", (diameter, diameter), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse([0, 0, diameter - 1, diameter - 1], fill=255)

    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(flag, (0, 0), mask)

    if border_width > 0:
        draw = ImageDraw.Draw(out)
        draw.ellipse(
            [border_width // 2, border_width // 2, diameter - 1 - border_width // 2, diameter - 1 - border_width // 2],
            outline=(*border_color, 255),
            width=border_width,
        )
    return out


def generate_channel_branding_assets() -> Tuple[Path, Path]:
    """
    Generates the official Call It Arena Profile Picture (800x800) and YouTube Banner (2560x1440)
    in `/branding/` for the manual rebrand checklist (skips if already present on disk).
    """
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    pfp_path = BRANDING_DIR / "profile_picture.png"
    banner_path = BRANDING_DIR / "youtube_banner.png"
    if pfp_path.exists() and banner_path.exists():
        return pfp_path, banner_path

    # 1. Profile Picture (800 x 800)
    pfp_size = 800
    pfp = Image.new("RGB", (pfp_size, pfp_size), BG_COLOR)
    pdraw = ImageDraw.Draw(pfp)

    cx, cy = pfp_size // 2, pfp_size // 2
    # 4 quadrant glowing arena rings
    ring_r = 340
    bbox = [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r]
    pdraw.arc(bbox, start=10, end=80, fill=ACCENT_RED, width=28)
    pdraw.arc(bbox, start=100, end=170, fill=ACCENT_BLUE, width=28)
    pdraw.arc(bbox, start=190, end=260, fill=ACCENT_GREEN, width=28)
    pdraw.arc(bbox, start=280, end=350, fill=ACCENT_YELLOW, width=28)

    inner_r = 285
    pdraw.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], fill=PANEL_BG_COLOR, outline=PANEL_BORDER_COLOR, width=4)

    font_big = get_font(150, title=True)
    font_sub = get_font(88, title=True)
    for idx, (text, y_pos, col) in enumerate([
        ("CALL IT", cy - 95, TEXT_WHITE),
        ("ARENA", cy + 45, ACCENT_YELLOW),
    ]):
        tb = pdraw.textbbox((0, 0), text, font=font_big if idx == 0 else font_sub)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pdraw.text((cx - tw // 2, y_pos - th // 2), text, font=font_big if idx == 0 else font_sub, fill=col)

    pfp_path = BRANDING_DIR / "profile_picture.png"
    pfp.save(pfp_path, format="PNG")

    # 2. YouTube Banner (2560 x 1440, safe area centered 1546 x 423)
    bw, bh = 2560, 1440
    banner = Image.new("RGB", (bw, bh), BG_COLOR)
    bdraw = ImageDraw.Draw(banner)

    # Decorative background grid & glowing rings
    for gx in range(0, bw, 120):
        bdraw.line([(gx, 0), (gx, bh)], fill=(22, 24, 40), width=2)
    for gy in range(0, bh, 120):
        bdraw.line([(0, gy), (bw, gy)], fill=(22, 24, 40), width=2)

    bcx, bcy = bw // 2, bh // 2
    # Safe zone card
    safe_w, safe_h = 1540, 420
    bdraw.rounded_rectangle(
        [bcx - safe_w // 2, bcy - safe_h // 2, bcx + safe_w // 2, bcy + safe_h // 2],
        radius=36,
        fill=PANEL_BG_COLOR,
        outline=ACCENT_BLUE,
        width=6,
    )
    # 4 accent stripes on left/right of safe zone
    for i, col in enumerate(ACCENT_PALETTE):
        y0 = bcy - 140 + i * 75
        bdraw.rounded_rectangle([bcx - safe_w // 2 + 35, y0, bcx - safe_w // 2 + 65, y0 + 50], radius=8, fill=col)
        bdraw.rounded_rectangle([bcx + safe_w // 2 - 65, y0, bcx + safe_w // 2 - 35, y0 + 50], radius=8, fill=col)

    title_font = get_font(135, title=True)
    tag_font = get_font(46, title=False, bold=True)
    sched_font = get_font(34, title=False, bold=False)

    t_text = "CALL IT ARENA"
    tb = bdraw.textbbox((0, 0), t_text, font=title_font)
    bdraw.text((bcx - (tb[2] - tb[0]) // 2, bcy - 155), t_text, font=title_font, fill=TEXT_WHITE)

    sub_text = "CALL IT BEFORE THE REVEAL  •  MARBLE LEAGUE  •  DAILY QUIZZES"
    sb = bdraw.textbbox((0, 0), sub_text, font=tag_font)
    bdraw.text((bcx - (sb[2] - sb[0]) // 2, bcy + 15), sub_text, font=tag_font, fill=ACCENT_YELLOW)

    sc_text = "2X DAILY BATTLES  |  @callitarena  |  COMMENT YOUR COUNTRY TO ENTER"
    scb = bdraw.textbbox((0, 0), sc_text, font=sched_font)
    bdraw.text((bcx - (scb[2] - scb[0]) // 2, bcy + 100), sc_text, font=sched_font, fill=TEXT_MUTED)

    banner_path = BRANDING_DIR / "youtube_banner.png"
    banner.save(banner_path, format="PNG")
    return pfp_path, banner_path

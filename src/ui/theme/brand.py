"""
The CrossTrans II crest and mark - the Royal Sapphire Crest with the sweeping cyan-blue and
amber-gold winged X, 'CrossTrans' above, and sculpted Roman numeral 'II' below (TODO.md U5,
V9, V16; Decision 48).

Master 512 px RGBA assets live in `src/assets/brand/` (bundled into PyInstaller via
`CrossTrans.spec`):
  - `crest_full.png`    for sizes > 36 px (Explorer, desktop shortcut, README, dialogs)
  - `crest_compact.png` for sizes <= 36 px (system tray and title bars, where an enlarged
                        winged X on the same gold-rimmed sapphire plate reads crisply)
  - `mark_only.png`     for `plate=False` (winged X alone on transparency)

Pure Pillow; no Tk, no application imports. `tools/make_icons.py` writes the .ico and .png files.
"""
import ctypes
from functools import lru_cache
import os
import sys
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw

NAVY_TOP = '#15376B'
NAVY_BOTTOM = '#050F23'
SKY = '#42D4FF'
SKY_EDGE = '#0B66B8'
AMBER = '#FFB224'
AMBER_EDGE = '#E85B10'
INK = '#050F23'

# Windows wants these frames in an .ico: the notification area at 100-200 %, the title bar, the
# taskbar, Explorer's list, medium and large views, and the 256 px "jumbo".
ICO_SIZES: Tuple[int, ...] = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256)
TRAY_SIZES: Tuple[int, ...] = (16, 20, 24, 32)
WORK = 1024                      # fallback design grid when master PNGs are unavailable
COMPACT_MAX_SIZE = 36            # <= 36 px uses the optically enlarged compact crest


def _rgb(colour: str) -> Tuple[int, int, int]:
    colour = colour.lstrip('#')
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16)


def stroke_for(size: int) -> Tuple[int, int]:
    """(stroke width, half extent) on the 1024 grid: the X gets bolder as the icon shrinks,
    because thin strokes at 16 px turn into a grey blur."""
    if size <= 20:
        return 270, 292
    if size <= 24:
        return 250, 290
    if size <= 36:
        return 224, 296
    if size <= 64:
        return 204, 300
    return 188, 304


def _brand_asset_path(filename: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, 'src', 'assets', 'brand', filename)


@lru_cache(maxsize=4)
def _load_master(filename: str) -> Optional[Image.Image]:
    path = _brand_asset_path(filename)
    if not os.path.isfile(path):
        return None
    with Image.open(path) as img:
        return img.convert('RGBA').copy()


def _gradient(size: int, top: str, bottom: str) -> Image.Image:
    base = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(base)
    (r1, g1, b1), (r2, g2, b2) = _rgb(top), _rgb(bottom)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line([(0, y), (size, y)], fill=(round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t),
                                              round(b1 + (b2 - b1) * t), 255))
    return base


def _bar(draw: ImageDraw.ImageDraw, start: Tuple[int, int], end: Tuple[int, int], width: int, fill):
    """A thick line with round caps."""
    draw.line([start, end], fill=fill, width=width)
    radius = width / 2
    for x, y in (start, end):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _render_fallback(size: int, plate: bool = True) -> Image.Image:
    """Pure-Pillow fallback mark used only if bundled master assets are absent."""
    width, half = stroke_for(size)
    centre = WORK // 2
    layer = Image.new('RGBA', (WORK, WORK), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    a, b = centre - half, centre + half
    blue, amber, ink = _rgb(SKY) + (255,), _rgb(AMBER) + (255,), _rgb(INK) + (255,)
    _bar(draw, (a, a), (b, b), width, blue)
    _bar(draw, (b, a), (a, b), width + 56, ink)
    _bar(draw, (b, a), (a, b), width, amber)
    if plate:
        base = _gradient(WORK, NAVY_TOP, NAVY_BOTTOM)
        mask = Image.new('L', (WORK, WORK), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, WORK - 1, WORK - 1), radius=232, fill=255)
        base.putalpha(mask)
        base.alpha_composite(layer)
        layer = base
    return layer.convert('RGBa').resize((size, size), Image.Resampling.LANCZOS).convert('RGBA')


def render_mark(size: int, plate: bool = True) -> Image.Image:
    """The CrossTrans II mark as a square RGBA image `size` pixels on a side.

    Sizes <= 36 px render the optically compact Royal Sapphire Crest (enlarged winged X on
    the gold-rimmed sapphire plate); larger sizes render the full Royal Sapphire Crest with
    'CrossTrans' above and Roman numeral 'II' below. With `plate=False`, only the winged X
    is returned on transparency.
    """
    if not plate:
        master = _load_master('mark_only.png')
    elif size <= COMPACT_MAX_SIZE:
        master = _load_master('crest_compact.png')
    else:
        master = _load_master('crest_full.png')

    if master is None:
        return _render_fallback(size, plate=plate)
    if master.size == (size, size):
        return master.copy()
    # premultiplied shrink: no dark halo round the transparent corners
    return master.convert('RGBa').resize((size, size), Image.Resampling.LANCZOS).convert('RGBA')


def render_set(sizes: Iterable[int] = ICO_SIZES, plate: bool = True) -> List[Image.Image]:
    return [render_mark(size, plate) for size in sizes]


def build_ico(path: str, sizes: Iterable[int] = ICO_SIZES) -> List[int]:
    """Write a multi-frame .ico, every frame square and drawn at its own size (not scaled down
    from the largest). Returns the sizes written."""
    sizes = sorted(set(sizes))
    frames = [render_mark(size) for size in sizes]
    biggest = frames[-1]
    biggest.save(path, format='ICO', sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    return sizes


def system_tray_size() -> int:
    """The notification-area icon size for this display (16 at 100 %, 20 at 125 %, 24 at 150 %,
    32 at 200 %): what `GetSystemMetrics(SM_CXSMICON)` says."""
    try:
        size = int(ctypes.windll.user32.GetSystemMetrics(49))          # SM_CXSMICON
        return size if size >= 16 else 16
    except Exception:
        return 16


def tray_image(size: int = 0) -> Image.Image:
    """The tray icon at the size the system uses - 64 px if the size cannot be read (pystray
    scales it), never squashed."""
    return render_mark(size or max(64, system_tray_size()))

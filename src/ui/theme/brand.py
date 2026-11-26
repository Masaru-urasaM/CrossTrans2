"""
The CrossTrans mark - a square, flat version of the logo's blue-and-amber X for the places the full
logo cannot go (TODO.md U5, V9, V16; Decision 48).

The full logo (`CrossTrans.png`: a silver disc, an X and the word "CrossTrans", 488 x 511, not even
square) turns into a smudge at 16-24 px and was squashed to 64 x 64 for the tray. The mark keeps
what the eye keeps at that size - two crossing strokes, sky blue and amber - on a navy rounded
square, so it reads on a light and a dark taskbar alike, and it is drawn from code: at the exact
size the system asks for (`SM_CXSMICON`), with strokes that thicken as the icon shrinks.

Pure Pillow; no Tk, no application imports. `tools/make_icons.py` writes the .ico and .png files.
"""
import ctypes
from typing import Iterable, List, Tuple

from PIL import Image, ImageDraw

NAVY_TOP = '#12427F'
NAVY_BOTTOM = '#082A55'
SKY = '#3DB5EA'
SKY_EDGE = '#1B86C8'
AMBER = '#FFB224'
AMBER_EDGE = '#F0801E'
INK = '#06203F'                 # the thin separation where the amber crosses the blue

# Windows wants these frames in an .ico: the notification area at 100-200 %, the title bar, the
# taskbar, Explorer's list, medium and large views, and the 256 px "jumbo".
ICO_SIZES: Tuple[int, ...] = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256)
TRAY_SIZES: Tuple[int, ...] = (16, 20, 24, 32)
WORK = 1024                      # the design grid; everything below is drawn at this size


def _rgb(colour: str) -> Tuple[int, int, int]:
    colour = colour.lstrip('#')
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16)


def stroke_for(size: int) -> Tuple[int, int]:
    """(stroke width, half extent) on the 1024 grid: the X gets bolder and bigger as the icon
    gets smaller, because a thin stroke at 16 px is a grey blur."""
    if size <= 20:
        return 270, 292
    if size <= 24:
        return 250, 290
    if size <= 36:
        return 224, 296
    if size <= 64:
        return 204, 300
    return 188, 304


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


def render_mark(size: int, plate: bool = True) -> Image.Image:
    """The mark as a square RGBA image `size` pixels on a side. With `plate=False` only the X is
    drawn, on transparency (for a place that supplies its own background)."""
    width, half = stroke_for(size)
    centre = WORK // 2
    layer = Image.new('RGBA', (WORK, WORK), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    a, b = centre - half, centre + half
    blue, amber, ink = _rgb(SKY) + (255,), _rgb(AMBER) + (255,), _rgb(INK) + (255,)
    # woven: amber over blue at the top right, blue over amber at the top left - an X, not a slash
    _bar(draw, (a, a), (b, b), width, blue)
    _bar(draw, (b, a), (a, b), width + 56, ink)
    _bar(draw, (b, a), (a, b), width, amber)
    over = Image.new('RGBA', (WORK, WORK), (0, 0, 0, 0))
    over_draw = ImageDraw.Draw(over)
    _bar(over_draw, (a, a), (b, b), width + 56, ink)
    _bar(over_draw, (a, a), (b, b), width, blue)
    half_plane = Image.new('L', (WORK, WORK), 0)                                  # the top-left half
    ImageDraw.Draw(half_plane).polygon([(0, 0), (WORK, 0), (0, WORK)], fill=255)
    layer.paste(over, (0, 0), Image.composite(over.getchannel('A'), Image.new('L', (WORK, WORK), 0),
                                              half_plane))
    if plate:
        base = _gradient(WORK, NAVY_TOP, NAVY_BOTTOM)
        mask = Image.new('L', (WORK, WORK), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, WORK - 1, WORK - 1), radius=232, fill=255)
        base.putalpha(mask)
        base.alpha_composite(layer)
        layer = base
    # premultiplied shrink: no dark halo round the transparent corners
    return layer.convert('RGBa').resize((size, size), Image.Resampling.LANCZOS).convert('RGBA')


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

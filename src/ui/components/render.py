"""
Rounded shapes as Tk images, drawn without the dark fringe (TODO.md U4b).

A rounded rectangle has transparent corners. Shrinking an RGBA picture that has them mixes the
corner pixels' colour (black, by default) into the edge pixels, which shows as a dark halo - on a
light theme the corners of every pill read as slightly square. The picture is therefore drawn large,
converted to premultiplied alpha, shrunk, and converted back: the edge colours are the shape's own.

Sizes here are in physical pixels; the caller scales logical sizes (`Builder.logical`).
"""
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageTk

SUPERSAMPLE = 4


def _rgba(colour: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    value = colour.lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha


def rounded_rect(width: int, height: int, radius: int, fill: Optional[str] = None,
                 outline: Optional[str] = None, outline_width: int = 0,
                 under: Optional[str] = None) -> Image.Image:
    """An RGBA rounded rectangle, `fill` and/or `outline` (None = transparent). With `under` the
    corners are painted that colour instead of left transparent: an opaque image, which Tk draws
    with plain block copies instead of per-pixel alpha blending."""
    factor = SUPERSAMPLE
    big = Image.new('RGBA', (width * factor, height * factor),
                    _rgba(under) if under else (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        (0, 0, width * factor - 1, height * factor - 1), radius=radius * factor,
        fill=_rgba(fill) if fill else None,
        outline=_rgba(outline) if outline and outline_width else None,
        width=outline_width * factor if outline and outline_width else 0)
    return shrink(big, (width, height))


def shrink(image: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Resample RGBA with premultiplied alpha: no halo of the transparent pixels' colour."""
    return image.convert('RGBa').resize(size, Image.Resampling.LANCZOS).convert('RGBA')


def grip(size: int, colour: str) -> Image.Image:
    """A resize grip: six dots in the lower-right triangle, on transparency."""
    factor = SUPERSAMPLE
    big = Image.new('RGBA', (size * factor, size * factor), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    step, radius = size * factor / 4, size * factor / 13
    for a in range(3):
        for b in range(3 - a):
            cx, cy = size * factor - (a + 0.8) * step, size * factor - (b + 0.8) * step
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=_rgba(colour))
    return shrink(big, (size, size))


def swap_arrows(size: int, colour: str, *, vertical: bool = False) -> Image.Image:
    """Two balanced, round-ended arrows, independent of installed icon fonts."""
    big = Image.new('RGBA', (size * SUPERSAMPLE, size * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    unit = (big.width - 1) / 24
    width = max(1, round(2 * unit))
    radius = (width - 1) / 2
    ink = _rgba(colour)
    for path in (((4, 8), (20, 8)), ((16, 4), (20, 8), (16, 12)),
                 ((20, 16), (4, 16)), ((8, 12), (4, 16), (8, 20))):
        points = [(round(x * unit), round(y * unit)) for x, y in path]
        draw.line(points, fill=ink, width=width, joint='curve')
        for x, y in points:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=ink)
    if vertical:
        big = big.transpose(Image.Transpose.ROTATE_270)
    return shrink(big, (size, size))


def to_photo(root, image: Image.Image, *, shared: bool = False) -> ImageTk.PhotoImage:
    """Share immutable style bitmaps within one interpreter without pinning unused images."""
    if not shared:
        return ImageTk.PhotoImage(image, master=root)
    import hashlib
    from weakref import WeakValueDictionary
    cache = getattr(root, '_ct_style_photo_cache', None)
    if cache is None:
        cache = root._ct_style_photo_cache = WeakValueDictionary()
    key = (image.mode, image.size, hashlib.sha256(image.tobytes()).digest())
    photo = cache.get(key)
    if photo is None:
        photo = ImageTk.PhotoImage(image, master=root)
        cache[key] = photo
    return photo

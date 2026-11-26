"""
The mascot, "Poco" (TODO.md U5, UQ4): a few poses for the moments that deserve a face - the welcome,
an empty page, a success, an error, waiting. Never in the reading path (TODO.md 6.1).

The pictures are `src/assets/mascot/<pose>_<scale>.png` at 128, 192 and 256 px - drawn for 100 %,
150 % and 200 % screens. The developer accepted this artwork on 2026-10-24. Replacing a file with
an illustrator's version of the same name and size changes nothing else. `mascot('happy', 96)` returns
the Tk image name of the pose at `96` logical pixels on this screen, picked from the nearest
variant and shrunk or stretched to the exact size.
"""
import os
from typing import Optional

from PIL import Image

from src.ui.components import context as C
from src.ui.components import render

POSES = ('hello', 'thinking', 'happy', 'oops', 'sleepy', 'celebrate')
VARIANTS = (('1x', 128), ('1_5x', 192), ('2x', 256))
FOLDER = os.path.join('src', 'assets', 'mascot')


def assets_root() -> str:
    import sys
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def variant_for(scale: float) -> str:
    """Which stored size to start from at a screen scale: 100 % -> 1x, 150 % -> 1.5x, 200 % -> 2x
    (the next one up between them, so a picture is shrunk, not blown up)."""
    if scale <= 1.0:
        return '1x'
    if scale <= 1.5:
        return '1_5x'
    return '2x'


def path_of(pose: str, variant: str) -> str:
    return os.path.join(assets_root(), FOLDER, f'{pose}_{variant}.png')


def load(pose: str, size: Optional[int] = None) -> str:
    """The Tk image name of `pose`, `size` logical pixels square (default: 96)."""
    if pose not in POSES:
        raise ValueError(f"unknown mascot pose {pose!r}; expected one of {POSES}")
    ctx = C.get()
    pixels = ctx.px(size or 96)
    key = ('mascot', pose, pixels)
    photo = ctx._images.get(key)
    if photo is None:
        with Image.open(path_of(pose, variant_for(ctx.scale))) as opened:
            image = opened.convert('RGBA')
        if image.size != (pixels, pixels):
            image = render.shrink(image, (pixels, pixels))
        photo = render.to_photo(ctx.root, image)
        ctx._images[key] = photo
    return str(photo)

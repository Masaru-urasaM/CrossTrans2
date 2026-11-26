"""WCAG 2.2 contrast arithmetic. Pure: no Tk, no application imports."""
from typing import Tuple

RGB = Tuple[int, int, int]

WHITE = '#FFFFFF'         # the two ends of a tint: a hover darkens toward BLACK, lightens toward WHITE
BLACK = '#000000'

TEXT_MIN = 4.5          # SC 1.4.3: text
BOUNDARY_MIN = 3.0      # SC 1.4.11: control boundaries, icons, focus indicators


def hex_to_rgb(value: str) -> RGB:
    """'#RRGGBB' or '#RGB' -> (r, g, b)."""
    value = value.lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"not a colour: #{value}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def relative_luminance(rgb: RGB) -> float:
    """WCAG 2.x relative luminance of an sRGB colour."""
    def channel(v: int) -> float:
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b) -> float:
    """(L1 + 0.05) / (L2 + 0.05) for two colours, each '#hex' or an (r, g, b) tuple."""
    la = relative_luminance(hex_to_rgb(a) if isinstance(a, str) else a)
    lb = relative_luminance(hex_to_rgb(b) if isinstance(b, str) else b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def passes_text(foreground, background) -> bool:
    return contrast_ratio(foreground, background) >= TEXT_MIN


def passes_boundary(foreground, background) -> bool:
    return contrast_ratio(foreground, background) >= BOUNDARY_MIN


def blend(foreground: str, background: str, alpha: float) -> str:
    """`foreground` over `background` at `alpha` (0-1), as '#rrggbb' - for hover and pressed
    tints that are a colour at some opacity over the surface."""
    fg, bg = hex_to_rgb(foreground), hex_to_rgb(background)
    mixed = tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))
    return '#%02x%02x%02x' % mixed

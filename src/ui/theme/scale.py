"""
One place that turns a size at 100 % into pixels on this screen (Decision 46, UQ17).

CrossTrans is system-DPI-aware: at 150 % Tk is given `scaling` 2.0 and draws in physical pixels.
Fonts in points follow that; raw pixel paddings and window sizes do not - which is how the
fixed-size windows of 1.9.x clipped their own buttons at 125-200 % (TODO.md V11, V12). Every
size in the redesigned UI goes through `px()`. Pure functions: no Tk import.
"""
BASE_DPI = 96.0


def scale_for_dpi(dpi: float) -> float:
    """1.0 at 96 DPI, 1.5 at 144, 2.0 at 192."""
    return float(dpi) / BASE_DPI


def scale_of(widget) -> float:
    """The scale factor of the screen `widget` is on, from Tk's own pixels-per-inch."""
    return scale_for_dpi(widget.winfo_fpixels('1i'))


def px(value: float, scale: float = 1.0) -> int:
    """`value` pixels at 100 %, in pixels at `scale`, rounded half up. A non-zero size never
    rounds to zero: a 1 px hairline stays visible at any scale."""
    if value < 0:
        return -px(-value, scale)
    scaled = int(value * scale + 0.5)
    return max(1, scaled) if value > 0 else 0


def inverse_px(pixels: float, scale: float = 1.0) -> float:
    """Pixels on this screen back to pixels at 100 % (for comparing a measurement with a token)."""
    return pixels / scale if scale else pixels

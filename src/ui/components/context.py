"""
What every component needs to know about the running application, in one place (TODO.md U4b).

`install()` is called once, by the application, after the theme engine: it records the Tk root, the
theme manager, ttkbootstrap's `Style`, the installed font families and builds the component styles
for the theme in force (and again for each theme the user switches to). Components then ask the
context for sizes (`px`), fonts (`font`), the palette and icons - never for a literal.

A widget built before `install()` (a test with no application around it) gets a default
context lazily: `get()` installs one on the default root with a Light/Dark manager that reads
Windows' setting, so a component can always be built.
"""
from typing import Optional

from src.ui.theme import fonts, tokens
from src.ui.theme.manager import ThemeManager
from src.ui.theme.scale import px as scaled_px

PLANES = ('bg', 'surface', 'alt')   # what a component sits on: the window, a card, a card on a card


class Context:
    def __init__(self, root, manager: ThemeManager, style, text_scale: Optional[float] = None):
        self.root, self.manager, self.style = root, manager, style
        self._text_scale = text_scale
        self.families = fonts.installed_families(root)
        self._icons = {}               # switch images, kept alive
        self._images = {}              # rounded shapes, kept alive and shared

    # --- the palette and the screen -------------------------------------------------------------
    @property
    def palette(self) -> tokens.Palette:
        return self.manager.palette

    @property
    def scale(self) -> float:
        """The screen's scale factor (1.0 at 96 DPI) - the same one ttkbootstrap sizes images by."""
        return float(self.style.scaling.factor)

    def px(self, value: float) -> int:
        return scaled_px(value, self.scale)

    def plane_colour(self, plane: str) -> str:
        p = self.palette
        return {'bg': p.bg, 'surface': p.surface, 'alt': p.alt, 'accent': p.accent}[plane]

    # --- type -----------------------------------------------------------------------------------
    def font(self, name: str, script: Optional[str] = None) -> tuple:
        """A Tk font tuple for a ramp style at this screen's scale and the in-app text size."""
        return fonts.font_tuple(name, self.families, self.scale, self.text_scale,
                                script or ('japanese' if self.manager.ui_language == 'ja' else 'latin'))

    @property
    def text_scale(self):
        return self.manager.text_scale if self._text_scale is None else self._text_scale

    # --- icons ----------------------------------------------------------------------------------
    def icon(self, name: str, size: int = 16, colour: Optional[str] = None) -> str:
        """A cached theme-coloured glyph; swap arrows use balanced vector geometry."""
        if name in ('swap-horizontal', 'swap-vertical'):
            from src.ui.components import render
            colour = colour or self.palette.muted
            physical_size = self.style.scaling.logical(size, minimum=1)
            key = (name, physical_size, colour)
            if key not in self._images:
                self._images[key] = render.to_photo(self.root,
                    render.swap_arrows(physical_size, colour, vertical=name == 'swap-vertical'))
            return str(self._images[key])
        from ttkbootstrap.style.assets import Assets
        return Assets(self.style).icon(name, size, colour or self.palette.muted)

    def grip(self, size: int = 12, colour: Optional[str] = None) -> str:
        """The Tk image name of a resize grip (the popup's bottom-right corner), drawn once per
        size and colour."""
        from src.ui.components import render
        colour = colour or self.palette.muted
        key = ('grip', size, colour, self.scale)
        photo = self._images.get(key)
        if photo is None:
            photo = render.to_photo(self.root, render.grip(self.px(size), colour))
            self._images[key] = photo
        return str(photo)


_context: Optional[Context] = None


def install(root, manager: ThemeManager, style) -> Context:
    """Create the context and build the component styles; rebuilt on every theme change."""
    global _context
    from src.ui.components import styles
    from src.utils import gc_owner
    gc_owner.install(root)
    _context = Context(root, manager, style)
    styles.install(_context)
    return _context


def get() -> Context:
    """The installed context; installs a default one on the first use after a bare root."""
    global _context
    if _context is None:
        import tkinter
        import ttkbootstrap  # noqa: F401 - kept: loads ttkbootstrap before the default context is built
        from src.ui.theme import compat, engine
        root = tkinter._default_root
        if root is None:
            raise RuntimeError("no Tk root: build the application (or a root) before a component")
        compat.protect_explicit_colours()
        manager = ThemeManager()
        eng = engine.install(root, manager)
        install(root, manager, eng.style)
    return _context


def reset() -> None:
    """Forget the context (tests); the styles built stay in their ttk themes."""
    global _context
    if _context is not None:
        from src.ui.components import styles
        styles.uninstall(_context)
    _context = None

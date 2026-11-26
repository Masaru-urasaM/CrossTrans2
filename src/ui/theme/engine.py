"""
Puts the design tokens on ttkbootstrap 2.x (Decision 45): one registered theme family,
"crosstrans-light" / "crosstrans-dark", whose colours are the tokens, switched by the theme manager.

ttkbootstrap builds every ttk widget style from a `Colors` scheme; we hand it one made from our
palette instead of letting it derive a family from anchors, so what a plain `ttk.Button` looks
like is decided here, in tokens, and the components of `src.ui.components` (rounded buttons, cards,
chips ...) read the same palette. A theme change is one `style.theme_use()`: ttk widgets repaint
themselves, ttkbootstrap's theme-change callbacks rebuild image assets, and the manager's
subscribers (title bars, `tk` widgets that hold explicit colours) do the rest.

Nothing here runs at import time and nothing imports the application.
"""
import logging
import weakref
from typing import Callable, List, Optional

from src.ui.theme import compat, tokens, windows, fonts, accessibility
from src.ui.theme.contrast import blend
from src.ui import i18n
from src.ui.theme.manager import ThemeManager

FAMILY = 'crosstrans'


def theme_name(mode: str) -> str:
    """'crosstrans-light' or 'crosstrans-dark'."""
    return f"{FAMILY}-{mode}"


def colors_for(palette: tokens.Palette) -> dict:
    """The sixteen ttkbootstrap `Colors` fields, from a palette.

    The accents ttkbootstrap offers as button fills are our accent and the status colours; a
    button's text colour on them is chosen by ttkbootstrap for contrast. `secondary` is the quiet
    tonal fill, `light` / `dark` the two neutral extremes.
    """
    extremes = (palette.surface, palette.text) if palette.mode == tokens.LIGHT_MODE \
        else (palette.text, palette.surface)
    return dict(
        primary=palette.accent, secondary=palette.alt, success=palette.success,
        info=palette.accent, warning=palette.warm, danger=palette.danger,
        light=extremes[0], dark=extremes[1],
        bg=palette.bg, fg=palette.text, selectbg=palette.selection, selectfg=palette.selection_text,
        border=palette.border, inputfg=palette.text, inputbg=palette.surface,
        active=blend(palette.text, palette.bg, 0.08))


def definitions() -> List:
    """One definition per registered palette, with its light/dark rendering mode."""
    from ttkbootstrap.style.theme import ThemeDefinition
    return [ThemeDefinition(theme_name(name), colors_for(p), mode=p.mode)
            for name, p in tokens.PALETTES.items()]


class Engine:
    """The installed engine: the `Style`, and the windows whose chrome follows the theme."""

    def __init__(self, root, manager: ThemeManager, style):
        self.root, self.manager, self.style = root, manager, style
        self._windows: 'weakref.WeakSet' = weakref.WeakSet()
        self._unsubscribe: Optional[Callable[[], None]] = None
        self._text_scale = manager.text_scale
        self._palette = manager.palette

    # --- wiring -------------------------------------------------------------------------------
    def start(self) -> 'Engine':
        i18n.track_title(self.root)
        self.apply(self.manager)
        self._unsubscribe = self.manager.subscribe(self.apply)
        return self

    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def apply(self, manager: ThemeManager) -> None:
        """Make the theme in force the one on screen: ttk styles, the root, every tracked window."""
        palette = manager.palette
        try:
            name = theme_name(manager.effective)
            if manager.ui_language == 'ja':
                name += '-ja'
            if manager.high_contrast:
                name += '-contrast-' + str(abs(hash(tuple(palette.as_dict().values()))))
            if manager.text_scale != 1.0:
                name += f'-text{round(manager.text_scale * 100)}'
            if name not in self.style.theme_names():
                from ttkbootstrap.style.theme import ThemeDefinition
                self.style.register_theme(ThemeDefinition(name, colors_for(palette), mode=palette.mode))
            self.style.theme_use(name)
            if palette != self._palette:
                accessibility.remap_widgets(self.root, self._palette, palette)
                self._palette = palette
            if self._text_scale != manager.text_scale:
                ratio = manager.text_scale / self._text_scale
                previous_scale = self._text_scale
                self._text_scale = manager.text_scale
                fonts.resize_widgets(self.root, ratio, previous_scale=previous_scale,
                                     target_scale=manager.text_scale)
        except Exception:
            logging.exception("[Theme] could not switch ttkbootstrap's theme")
        try:
            self.root.configure(bg=palette.bg)
        except Exception:
            pass
        for window in list(self._windows):
            self._style_window(window, palette)

    # --- windows ------------------------------------------------------------------------------
    def track(self, window) -> None:
        """Style `window`'s chrome now and on every later theme change (weakly held)."""
        i18n.track_title(window)
        self._windows.add(window)
        self._style_window(window, self.manager.palette)

    def _style_window(self, window, palette: tokens.Palette) -> None:
        try:
            if not window.winfo_exists():
                return
            if not window.overrideredirect():
                window.update_idletasks()
                windows.apply_title_bar(window, palette)
            window.configure(bg=palette.bg)
        except Exception:
            logging.debug("[Theme] could not style a window", exc_info=True)


_engine: Optional[Engine] = None


def install(root, manager: ThemeManager) -> Engine:
    """Create ttkbootstrap's `Style` on `root`, register our theme family and select the one the
    manager says is in force. Call once, before any widget is built."""
    global _engine
    import ttkbootstrap as ttk
    compat.protect_explicit_colours()           # before the first widget exists
    style = ttk.Style(theme='bootstrap-light')  # a root-bound singleton; any built-in will do, ours follows
    for definition in definitions():
        style.register_theme(definition)
    _engine = Engine(root, manager, style).start()
    return _engine


def get_engine() -> Optional[Engine]:
    return _engine


def attach(window) -> None:
    """Give `window` the current theme's chrome and keep it in step. A no-op before `install()`
    (a test that builds a window with no application around it)."""
    if _engine is not None:
        _engine.track(window)


def reset() -> None:
    """Forget the installed engine (tests)."""
    global _engine
    if _engine is not None:
        _engine.stop()
    _engine = None
    compat.unprotect_explicit_colours()

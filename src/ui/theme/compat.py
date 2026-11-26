"""
What differs between ttkbootstrap 1.x and 2.x for this code (Decision 45).

ttkbootstrap 1.x monkey-patched every `tkinter` widget: a `tk.Label(fg='#80b8ff')` came back
white unless it was built with `autostyle=False`. 2.x stops patching: a
`tk` widget keeps the colour it is given, and `autostyle=False` is now an unknown option that
raises TclError. `NO_AUTOSTYLE` is the one guarded constant every call site unpacks, so the same
source runs on either - and, with the redesign finished, on 2.x only.
"""
import tkinter
from typing import Optional

# The options that mean "this widget has chosen its own colours".
COLOUR_OPTIONS = frozenset({
    'bg', 'fg', 'background', 'foreground', 'activebackground', 'activeforeground',
    'selectbackground', 'selectforeground', 'highlightbackground', 'highlightcolor',
    'insertbackground', 'disabledforeground', 'troughcolor', 'readonlybackground'})


def ttkbootstrap_major() -> Optional[int]:
    """2 for ttkbootstrap 2.x, 1 for 1.x, None when it is not installed. 2.x carries a
    `__version__`; 1.x has none (so no metadata lookup, which a frozen build may lack)."""
    try:
        import ttkbootstrap
    except ImportError:
        return None
    version = getattr(ttkbootstrap, '__version__', None)
    try:
        return int(str(version).split('.')[0]) if version else 1
    except ValueError:
        return 1


# Unpack into a `tk.*` widget that must keep its colours: `tk.Label(..., **NO_AUTOSTYLE)`.
# The Text/Canvas widgets that should match their themed parent simply do not.
NO_AUTOSTYLE = {'autostyle': False} if ttkbootstrap_major() == 1 else {}


# --- 2.x still repaints raw tk widgets on a theme change -------------------------------------
# ttkbootstrap 2.x no longer touches a raw `tk` widget at construction, but its theme walk
# (`Style._theme_walk`, on every `theme_use`) repaints every widget in the tree that does not carry
# the `_tb_no_autostyle` flag - measured 2026-10-21: a `tk.Label(fg='#777777')` came back with the
# theme's colours as soon as a Style was created or the theme switched. So the flag is set for the
# widgets that chose their colours: unspecified colours follow the theme, explicit ones are kept.
_ORIGINAL_INIT = tkinter.BaseWidget.__init__
_protecting = False


def _guarded_init(self, master, widgetName, cnf={}, kw={}, extra=()):
    _ORIGINAL_INIT(self, master, widgetName, cnf, kw, extra)
    chosen = (set(cnf) if cnf else set()) | (set(kw) if kw else set())
    if chosen & COLOUR_OPTIONS:
        try:
            self._tb_no_autostyle = True
        except AttributeError:
            pass


def protect_explicit_colours() -> bool:
    """Make every `tk` widget built with a colour option opt out of ttkbootstrap's theme walk.
    Idempotent; returns True the first time. Called by `engine.install()`."""
    global _protecting
    if _protecting:
        return False
    tkinter.BaseWidget.__init__ = _guarded_init
    _protecting = True
    return True


def unprotect_explicit_colours() -> None:
    """Undo `protect_explicit_colours()` (tests)."""
    global _protecting
    tkinter.BaseWidget.__init__ = _ORIGINAL_INIT
    _protecting = False


def keep_colours(widget) -> None:
    """Opt one `tk` widget out of the theme walk after the fact - for a widget built without a
    colour option that is then coloured with `configure()`."""
    try:
        widget._tb_no_autostyle = True
    except AttributeError:
        pass

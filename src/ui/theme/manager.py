"""
The theme manager (Decision 46): which of Light and Dark is in force, and who hears about a change.

The user picks a mode - System (the default), Light or Dark. "System" follows Windows: the registry
value `AppsUseLightTheme` (spike S4) is re-read on a timer while the app runs, because Windows
sends no message a Tk window can use without a message-only window of its own, and one cheap
registry read every couple of seconds is enough ("every open window follows within a few seconds",
TODO.md section 8). Surfaces never ask Windows themselves: they read `palette()` and subscribe.

Pure logic around two injectable seams - the registry reader and the persistence store - so every
rule is testable without a display. Applying a palette to widgets (the ttk style, the title bar)
belongs to the subscribers, which are added where the widgets are (U4 onwards).
"""
import logging
from typing import Callable, List, Optional

from src.ui.theme import tokens
from src.ui.theme import accessibility
from src.ui import i18n

PERSONALIZE_KEY = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
APPS_VALUE = 'AppsUseLightTheme'
DEFAULT_WATCH_MS = 2000

Subscriber = Callable[['ThemeManager'], None]


def read_apps_use_light_theme() -> Optional[bool]:
    """True when Windows apps are in light mode, False for dark, None when it cannot be read
    (not Windows, or the value is absent - Windows before 10 1903 had no dark mode)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APPS_VALUE)
        return bool(value)
    except (ImportError, OSError):
        return None


def detect_system_mode(reader: Callable[[], Optional[bool]] = read_apps_use_light_theme) -> str:
    """'light' or 'dark', as Windows says. An unreadable setting is light - what Windows itself
    shows an app that has no dark mode."""
    light = reader()
    return tokens.DARK_MODE if light is False else tokens.LIGHT_MODE


class MemoryStore:
    """Where the chosen mode lives when nothing persists it (tests, a first run)."""

    def __init__(self, mode: str = tokens.SYSTEM_MODE):
        self._mode = mode

    def get_theme_mode(self) -> str:
        return self._mode

    def set_theme_mode(self, mode: str) -> None:
        self._mode = mode


class ThemeManager:
    def __init__(self, store=None, detect: Callable[[], str] = detect_system_mode, read_accessibility=None):
        self._store = store if store is not None else MemoryStore()
        self._detect = detect
        self._read_accessibility = read_accessibility or accessibility.read_preferences
        self.accessibility = self._read_accessibility()
        self._text_scale = accessibility.text_scale(self._store.get('text_scale', 1.0)) if hasattr(self._store, 'get') else 1.0
        self._ui_language = i18n.normalize(self._store.get('ui_language', 'en')) if hasattr(self._store, 'get') else 'en'
        i18n.set_language(self._ui_language)
        self._subscribers: List[Subscriber] = []
        self._watch_job = None
        self._watch_root = None
        self._mode = self._valid(self._store.get_theme_mode())
        self._system = detect()
        self._effective = self._resolve()

    # --- state ------------------------------------------------------------------------------
    @staticmethod
    def _valid(mode) -> str:
        return mode if mode in tokens.MODES else tokens.SYSTEM_MODE

    def _resolve(self) -> str:
        if self._mode == tokens.SYSTEM_MODE:
            return tokens.DARK_MODE if self._system == tokens.DARK_MODE else tokens.PINK_MODE
        return self._mode

    @property
    def mode(self) -> str:
        """The saved palette ID, or 'system' to follow Windows."""
        return self._mode

    @property
    def system_mode(self) -> str:
        """What Windows is set to right now ('light' or 'dark')."""
        return self._system

    @property
    def effective(self) -> str:
        """The concrete palette ID currently in force."""
        return self._effective

    @property
    def palette(self) -> tokens.Palette:
        if self.accessibility.high_contrast and len(self.accessibility.colours) == 5:
            return accessibility.contrast_palette(self.accessibility)
        return tokens.PALETTES[self._effective]

    @property
    def high_contrast(self):
        return self.accessibility.high_contrast

    @property
    def animations_enabled(self):
        return self.accessibility.animations and not self.high_contrast

    @property
    def notification_duration_ms(self):
        return self.accessibility.notification_ms

    @property
    def text_scale(self):
        return self._text_scale

    def set_text_scale(self, value):
        from src.ui.deferred_resize import defer_global_update
        if defer_global_update(('text-scale', id(self)), lambda: self.set_text_scale(value)):
            return
        value = accessibility.text_scale(value)
        if value != self._text_scale:
            self._text_scale = value
            if hasattr(self._store, 'set'):
                self._store.set('text_scale', value)
            self._notify()

    @property
    def ui_language(self):
        return self._ui_language

    def set_ui_language(self, value):
        from src.ui.deferred_resize import defer_global_update
        if defer_global_update(('ui-language', id(self)), lambda: self.set_ui_language(value)):
            return
        value = i18n.normalize(value)
        if value != self._ui_language:
            self._ui_language = value
            if hasattr(self._store, 'set'):
                self._store.set('ui_language', value)
            i18n.set_language(value)
            self._notify()

    def refresh_accessibility(self):
        current = self._read_accessibility()
        if current != self.accessibility:
            self.accessibility = current
            self._notify()
            return True
        return False

    @property
    def is_dark(self) -> bool:
        return self.palette.mode == tokens.DARK_MODE

    # --- changing it ------------------------------------------------------------------------
    def set_mode(self, mode: str) -> bool:
        """The user's choice. Persisted; subscribers hear about it only if the theme in force
        actually changes. Returns whether it did. An unknown mode is refused."""
        if mode not in tokens.MODES:
            raise ValueError(f"unknown theme mode {mode!r}; expected one of {tokens.MODES}")
        self._mode = mode
        self._store.set_theme_mode(mode)
        return self._settle()

    def refresh_system(self) -> bool:
        """Re-read Windows' setting; True when that changed the theme in force."""
        self._system = self._detect()
        return self._settle()

    def _settle(self) -> bool:
        effective = self._resolve()
        if effective == self._effective:
            return False
        self._effective = effective
        self._notify()
        return True

    # --- hearing about it -------------------------------------------------------------------
    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """`callback(manager)` runs after every change of the theme in force. Returns the
        function that unsubscribes it."""
        self._subscribers.append(callback)

        def unsubscribe():
            nonlocal callback
            saved, callback = callback, None
            if saved in self._subscribers:
                self._subscribers.remove(saved)
        return unsubscribe

    def _notify(self) -> None:
        from src.ui.deferred_resize import defer_global_update
        if defer_global_update(('theme-notify', id(self)), self._notify):
            return
        for callback in list(self._subscribers):
            try:
                callback(self)
            except Exception:                       # one broken window must not stop the rest
                logging.exception("[Theme] a subscriber failed")

    # --- following Windows ------------------------------------------------------------------
    def watch(self, root, interval_ms: int = DEFAULT_WATCH_MS) -> None:
        """Poll Windows' setting from the Tk event loop while the mode is 'system'. Safe to call
        twice; `stop_watching()` ends it."""
        self.stop_watching()
        self._watch_root = root

        def tick():
            self._watch_job = None
            try:
                if self._mode == tokens.SYSTEM_MODE:
                    self.refresh_system()
                self.refresh_accessibility()
                self._watch_job = root.after(interval_ms, tick)
            except Exception:                      # the root is gone
                self._watch_job = None
        self._watch_job = root.after(interval_ms, tick)

    def stop_watching(self) -> None:
        root, job = self._watch_root, self._watch_job
        self._watch_root = self._watch_job = None
        if root is not None and job is not None:
            try:
                root.after_cancel(job)
            except Exception:
                pass


# --- the application's one manager ----------------------------------------------------------
_current: Optional[ThemeManager] = None


def get_manager() -> ThemeManager:
    """The application's manager; a default, memory-backed one until `install_manager()`."""
    global _current
    if _current is None:
        _current = ThemeManager()
    return _current


def install_manager(manager: Optional[ThemeManager]) -> Optional[ThemeManager]:
    """Make `manager` the application's (None resets to a lazily built default); returns it."""
    global _current
    _current = manager
    return manager


def palette() -> tokens.Palette:
    """The palette in force now - what a surface reads when it draws."""
    return get_manager().palette

"""
UI components for CrossTrans.

`SettingsWindow` and `APIErrorDialog` are re-exported lazily: importing any `src.ui.*` module
runs this file first, and `src.constants` imports `src.ui.theme` for its colours - an eager import
of the settings window here would close an import cycle (settings -> constants -> theme -> ui).
"""

__all__ = ['SettingsWindow', 'APIErrorDialog']


def __getattr__(name):
    if name == 'SettingsWindow':
        from src.ui.settings import SettingsWindow
        return SettingsWindow
    if name == 'APIErrorDialog':
        from src.ui.dialogs import APIErrorDialog
        return APIErrorDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

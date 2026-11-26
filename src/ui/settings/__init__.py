"""
Settings module for CrossTrans.

This module contains the Settings window split into multiple files for maintainability:
- main.py: SettingsWindow - navigation, search, and the pages (TODO.md U8)
- page.py: the base every page shares; pages_basic.py (General, Appearance, Translation),
  page_shortcuts.py, page_providers.py, page_dictionary.py, page_help.py, page_about.py
- background.py: BackgroundRunner - slow work off the UI thread, results collected by the UI thread
- update_manager.py: Update checking and downloading functionality
"""

__all__ = ['SettingsWindow']


def __getattr__(name):
    if name == 'SettingsWindow':
        from src.ui.settings.main import SettingsWindow
        return SettingsWindow
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

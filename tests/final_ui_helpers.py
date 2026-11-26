"""Isolated preferences and component context for U9c-U12 acceptance tests."""
from types import SimpleNamespace

import pytest

from src.ui import components as ui, i18n
from src.ui.theme import engine
from src.ui.theme import manager as theme_manager
from src.ui.theme.accessibility import Preferences


class Store(theme_manager.MemoryStore):
    def __init__(self):
        super().__init__('light')
        self.values = {}
        self.saved = []
        self.target = 'English'
        self.autostart = False
        self.autostart_writes = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.saved.append((key, value))

    def get_default_target_language(self):
        return self.target

    def set_default_target_language(self, value):
        self.target = value

    def get_autostart(self):
        return self.autostart

    def set_autostart(self, value):
        self.autostart = value
        self.autostart_writes.append(value)

    def get_hotkeys(self):
        return {'English': 'win+alt+e', 'Japanese': 'win+alt+j'}

    get_all_hotkeys = get_hotkeys

    def get_api_keys(self):
        return []


@pytest.fixture
def design(tk_root):
    previous = theme_manager._current
    preferences = [Preferences(notification_ms=0)]
    store = Store()
    manager = theme_manager.ThemeManager(store, detect=lambda: 'light',
                                          read_accessibility=lambda: preferences[0])
    theme_manager.install_manager(manager)
    eng = engine.install(tk_root, manager)
    context = ui.install(tk_root, manager, eng.style)
    yield SimpleNamespace(root=tk_root, store=store, manager=manager, ctx=context, preferences=preferences)
    for child in list(tk_root.winfo_children()):
        child.destroy()
    ui.reset()
    engine.reset()
    theme_manager.install_manager(previous)
    i18n.set_language(previous.ui_language if previous is not None else 'en')


def pump(root, ready, timeout=3):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if ready():
            return
        time.sleep(0.005)
    assert ready(), 'UI result was not delivered before the deadline'

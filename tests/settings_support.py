"""
What the Settings page and window tests share (TODO.md U8): a configuration that never touches the
registry or the disk, a host a page can be built against, and a keyboard that is only a queue.

Nothing here reaches the developer's session: the real `Config` would write `%APPDATA%` and the Run key,
the real hook would listen to the keyboard (quiet_session refuses all of it).
"""
import copy
import queue
import time
from types import SimpleNamespace
from unittest.mock import Mock

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from config import Config


class SettingsConfig(Config):
    """The real accessors over an in-memory dictionary, with start-with-Windows kept off the registry."""

    def __init__(self, **overrides):
        self._config = copy.deepcopy(Config.DEFAULT_CONFIG)
        self._config.update(overrides)
        self.api_status_cache = {}
        self.runtime_capabilities = {'vision': False, 'file': False}
        self.saves = 0
        self.secure_saves = 0
        self.autostart_calls = []

    def save(self, secure=False):
        self.saves += 1
        self.secure_saves += 1 if secure else 0

    def get_api_keys(self):
        """The keys as plain dictionaries: DPAPI is `Config`'s own tests' business, not a page's."""
        return [dict(item) for item in self._config.get('api_keys', [])]

    def set_api_keys(self, api_keys, secure=False):
        self._config['api_keys'] = [dict(item) for item in api_keys]
        self.save(secure=secure)

    def is_autostart_enabled(self):
        return bool(self._config.get('autostart', False))

    def set_autostart(self, enable):
        self.autostart_calls.append(bool(enable))
        self._config['autostart'] = bool(enable)
        self.save()


class FakeSource:
    """The keyboard hook, as a queue the test fills."""

    def __init__(self):
        self.events = queue.Queue()
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def down(self, *names):
        for name in names:
            self.events.put(('down', name))

    def up(self, *names):
        for name in names:
            self.events.put(('up', name))

    def press(self, *names):
        """A chord: every key down, then every key up."""
        self.down(*names)
        self.up(*reversed(names))


class Host:
    """What a page asks of the Settings window, recorded instead of done."""

    def __init__(self, window, config=None):
        self.window = window
        self.config = config or SettingsConfig()
        self.update_manager = SimpleNamespace(credentials_changed=Mock(), available_version=None, ready_update=None,
                                              auto_check_changed=Mock(), request_check=Mock(),
                                              request_update=Mock())
        self.changes, self.recordings = [], []
        self.refreshed = self.adopted = self.update_refreshes = self.api_changes = 0
        self.reveals, self.opened = [], []
        self.key_source_factory = None
        self.status = {}
        self._check_for_updates_click = Mock()

    def changed(self, key):
        self.changes.append(key)

    def refresh_pages(self):
        self.refreshed += 1

    def adopt(self, key):
        self.adopted += 1

    def reveal(self, key, widget):
        self.reveals.append((key, widget))

    def api_changed(self):
        self.api_changes += 1

    def set_recording(self, active):
        self.recordings.append(bool(active))

    def shortcut_status(self):
        return self.status

    def open_page(self, key, highlight=None):
        self.opened.append(key)

    def refresh_update_state(self):
        self.update_refreshes += 1


def pump(widget, seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        widget.update()
        time.sleep(0.005)


def wait_for(widget, condition, seconds=1.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        widget.update()
        if condition():
            return True
        time.sleep(0.005)
    return condition()

"""
Tests for what the Settings window needs from src/core/hotkey.py (TODO.md U8): how each
registration went, and a switch that silences the hotkeys while a shortcut is being recorded.

The real RegisterHotKey is never called - a test run stays off the developer's session: the
manager's `user32` and `kernel32` are replaced by fakes after construction.
"""
import threading
from types import SimpleNamespace

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.core.hotkey import HotkeyManager

TAKEN = 1409                       # ERROR_HOTKEY_ALREADY_REGISTERED


class FakeUser32:
    def __init__(self):
        self.refuse = {}           # vk code -> True to refuse
        self.registered = []
        self.unregistered = []

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):
        if self.refuse.get(vk):
            return 0
        self.registered.append((hotkey_id, modifiers, vk))
        return 1

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregistered.append(hotkey_id)
        return 1


class FakeConfig:
    def __init__(self, hotkeys=None, custom=None, screenshot='win+alt+s', fix='win+alt+g', fix_enabled=False):
        self.hotkeys = hotkeys if hotkeys is not None else {'English': 'win+alt+e', 'Japanese': 'win+alt+j'}
        self.custom = custom or {}
        self.screenshot, self.fix, self.fix_enabled = screenshot, fix, fix_enabled

    def get_hotkeys(self):
        return self.hotkeys

    def get_custom_hotkeys(self):
        return self.custom

    def get_screenshot_hotkey(self):
        return self.screenshot

    def get_fix_grammar_hotkey_enabled(self):
        return self.fix_enabled

    def get_fix_grammar_hotkey(self):
        return self.fix


def make(config=None, callback=None, error=0):
    manager = HotkeyManager(config or FakeConfig(), callback or (lambda language: None))
    manager.user32 = FakeUser32()
    manager.kernel32 = SimpleNamespace(GetLastError=lambda: error)
    return manager


E, J, S, G = ord('E'), ord('J'), ord('S'), ord('G')


class TestRegistrationStatus:
    def test_every_hotkey_that_registers_is_ok(self):
        manager = make(FakeConfig(fix_enabled=True))
        manager.register_hotkeys()
        assert manager.registration == {'English': 'ok', 'Japanese': 'ok', '__screenshot__': 'ok',
                                        '__fix_grammar__': 'ok'}

    def test_one_another_app_holds_is_taken_and_the_others_are_unaffected(self):
        manager = make(error=TAKEN)
        manager.user32.refuse[J] = True
        manager.register_hotkeys()
        assert manager.registration['Japanese'] == 'taken'
        assert manager.registration['English'] == 'ok' and manager.registration['__screenshot__'] == 'ok'

    def test_any_other_failure_is_failed(self):
        manager = make(error=5)
        manager.user32.refuse[E] = True
        manager.register_hotkeys()
        assert manager.registration['English'] == 'failed'

    def test_the_screenshot_hotkey_is_reported_too(self):
        manager = make(error=TAKEN)
        manager.user32.refuse[S] = True
        manager.register_hotkeys()
        assert manager.registration['__screenshot__'] == 'taken'

    def test_the_fix_grammar_hotkey_is_reported_when_it_is_on(self):
        manager = make(FakeConfig(fix_enabled=True), error=TAKEN)
        manager.user32.refuse[G] = True
        manager.register_hotkeys()
        assert manager.registration['__fix_grammar__'] == 'taken'

    def test_a_switched_off_fix_grammar_hotkey_is_not_listed(self):
        manager = make(FakeConfig(fix_enabled=False))
        manager.register_hotkeys()
        assert '__fix_grammar__' not in manager.registration

    def test_a_combination_that_cannot_be_parsed_is_invalid(self):
        manager = make(FakeConfig(hotkeys={'English': 'print screen', 'Japanese': 'win+alt+j'}))
        manager.register_hotkeys()
        assert manager.registration['English'] == 'invalid' and manager.registration['Japanese'] == 'ok'

    def test_a_language_with_no_combination_is_not_listed(self):
        manager = make(FakeConfig(hotkeys={'English': '', 'Japanese': 'win+alt+j'}))
        manager.register_hotkeys()
        assert 'English' not in manager.registration

    def test_custom_hotkeys_are_listed_with_the_rest(self):
        manager = make(FakeConfig(custom={'French': 'win+alt+f'}))
        manager.register_hotkeys()
        assert manager.registration['French'] == 'ok'

    def test_registering_again_starts_the_table_afresh(self):
        config = FakeConfig()
        manager = make(config)
        manager.register_hotkeys()
        config.hotkeys = {'Japanese': 'win+alt+j'}
        manager.register_hotkeys()
        assert 'English' not in manager.registration and manager.registration['Japanese'] == 'ok'

    def test_nothing_is_known_before_the_first_registration(self):
        assert make().registration == {}


class TestSuspended:
    def press(self, manager, language='English'):
        done = threading.Event()
        calls = []

        def callback(name):
            calls.append(name)
            done.set()
        manager.callback = callback
        manager._on_hotkey(language)
        return done, calls

    def test_a_press_runs_the_callback(self):
        done, calls = self.press(make())
        assert done.wait(2) and calls == ['English']

    def test_a_press_while_suspended_does_nothing(self):
        manager = make()
        manager.suspended = True
        done, calls = self.press(manager)
        assert not done.wait(0.3) and calls == []

    def test_suspending_does_not_start_the_debounce_clock(self):
        manager = make()
        manager.suspended = True
        self.press(manager)
        manager.suspended = False
        done, calls = self.press(manager)
        assert done.wait(2) and calls == ['English']

    def test_it_starts_not_suspended(self):
        assert make().suspended is False

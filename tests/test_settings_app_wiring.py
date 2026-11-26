"""
How the application answers the Settings window (TODO.md U8, Decision 51): a stored change becomes a
registration of the hotkeys again - off the UI thread, debounced, polled from the UI thread - or a
refreshed tray; a recording silences the hotkeys; the old names still open the right page.

`TranslatorApp` is built with `__new__` (no hotkeys, no tray, no window); its root is a clock the test
turns by hand, so nothing waits on real time.
"""
import ast
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import src.core  # noqa: F401  (must precede `config`)
from src.app import TranslatorApp


class Clock:
    """A root with `after`/`after_cancel` that runs a job only when the test says so."""

    def __init__(self):
        self.jobs = {}
        self.counter = 0
        self.event_generate = Mock()

    def after(self, delay, callback=None, *args):
        self.counter += 1
        job = f'job{self.counter}'
        self.jobs[job] = (delay, (lambda: callback(*args)) if callback else (lambda: None))
        return job

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def run(self, job):
        _, callback = self.jobs.pop(job)
        callback()

    def step(self):
        self.run(next(iter(self.jobs)))

    def run_all(self, limit=50):
        for _ in range(limit):
            if not self.jobs:
                return
            self.run(next(iter(self.jobs)))
        raise AssertionError('jobs keep scheduling themselves')

    def delays(self):
        return [delay for delay, _ in self.jobs.values()]


@pytest.fixture
def app():
    app = TranslatorApp.__new__(TranslatorApp)
    app.root = Clock()
    app.hotkey_manager = SimpleNamespace(suspended=False, registration={}, unregister_all=Mock())
    app.tray_manager = Mock()
    app.settings_window = None
    return app


def wait_until(predicate, seconds=2.0):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class TestWhatChangesDo:
    @pytest.mark.parametrize('key', ['shortcuts_enabled', 'language_shortcuts', 'screenshot_shortcut', 'fix_grammar_shortcut',
                                     'audio_shortcut', 'audio_enabled', 'restore_defaults'])
    def test_a_shortcut_change_schedules_registering_the_hotkeys_again(self, app, key):
        app._on_settings_changed(key)
        assert app.root.delays() == [TranslatorApp.HOTKEY_REFRESH_DELAY_MS]
        app.hotkey_manager.unregister_all.assert_not_called()               # not on the spot

    @pytest.mark.parametrize('key', ['shortcuts_enabled', 'fix_grammar_button', 'restore_defaults'])
    def test_a_tray_setting_refreshes_the_tray_at_once(self, app, key):
        app._on_settings_changed(key)
        app.tray_manager.refresh_menu.assert_called_once()

    @pytest.mark.parametrize('key', ['furigana', 'theme', 'default_language', 'replace_mode', 'autostart',
                                     'auto_update', 'linked_reading', 'screenshot_language', 'anything'])
    def test_every_other_setting_needs_nothing_from_the_application(self, app, key):
        app._on_settings_changed(key)
        assert app.root.jobs == {}
        app.tray_manager.refresh_menu.assert_not_called()

    def test_several_changes_in_a_row_register_once(self, app):
        app._on_settings_changed('language_shortcuts')
        app._on_settings_changed('language_shortcuts')
        app._on_settings_changed('screenshot_shortcut')
        assert len(app.root.jobs) == 1

    def test_the_sets_hold_exactly_what_the_page_keys_say(self):
        from src.core import settings_index as index
        keys = {entry.key for entry in index.ENTRIES}
        assert TranslatorApp.HOTKEY_SETTINGS <= keys and TranslatorApp.TRAY_SETTINGS <= keys
        assert TranslatorApp.AUDIO_PANEL_SETTINGS <= keys


class TestRegisteringAgain:
    def test_it_runs_off_the_ui_thread_and_ends_with_the_tray_and_the_status(self, app):
        seen = []
        app.hotkey_manager.unregister_all = lambda: seen.append(threading.current_thread())
        app.settings_window = SimpleNamespace(window=SimpleNamespace(winfo_exists=lambda: True),
                                              refresh_shortcut_status=Mock())
        app._start_hotkey_refresh()
        assert wait_until(lambda: seen)
        # the UI thread learns of the end by polling, never by being called from the worker
        for _ in range(100):
            if app.tray_manager.refresh_menu.called:
                break
            app.root.step() if app.root.jobs else None
            import time
            time.sleep(0.005)
        assert seen[0] is not threading.main_thread()
        app.tray_manager.refresh_menu.assert_called_once()
        app.settings_window.refresh_shortcut_status.assert_called_once()
        assert app._hotkey_refresh_busy is False

    def test_a_failure_still_ends_and_is_logged(self, app, caplog):
        import logging
        import time
        app.hotkey_manager.unregister_all = Mock(side_effect=RuntimeError('boom'))
        with caplog.at_level(logging.ERROR):
            app._start_hotkey_refresh()
            for _ in range(200):
                if app.tray_manager.refresh_menu.called:
                    break
                if app.root.jobs:
                    app.root.step()
                time.sleep(0.005)
        assert app.tray_manager.refresh_menu.called and 'could not register the hotkeys again' in caplog.text
        assert app._hotkey_refresh_busy is False

    def test_a_request_during_a_refresh_runs_again_afterwards_not_at_the_same_time(self, app):
        import time
        started, release, calls = threading.Event(), threading.Event(), []

        def slow():
            calls.append(1)
            started.set()
            release.wait(2)
        app.hotkey_manager.unregister_all = slow
        app._start_hotkey_refresh()
        assert started.wait(2)
        app._start_hotkey_refresh()                       # asked again while it is running
        assert app._hotkey_refresh_again is True and len(calls) == 1
        release.set()
        for _ in range(400):
            if len(calls) == 2 and not app._hotkey_refresh_busy and not app.root.jobs:
                break
            if app.root.jobs:
                app.root.step()
            time.sleep(0.005)
        assert len(calls) == 2 and app._hotkey_refresh_again is False

    def test_a_closed_settings_window_is_left_alone(self, app):
        import tkinter as tk
        import time

        def gone():
            raise tk.TclError('destroyed')
        app.settings_window = SimpleNamespace(window=SimpleNamespace(winfo_exists=gone),
                                              refresh_shortcut_status=Mock())
        app._start_hotkey_refresh()
        for _ in range(200):
            if app.tray_manager.refresh_menu.called:
                break
            if app.root.jobs:
                app.root.step()
            time.sleep(0.005)
        app.settings_window.refresh_shortcut_status.assert_not_called()


class TestRecording:
    def test_the_hotkeys_are_silenced_while_recording_and_back_after(self, app):
        app._on_settings_recording(True)
        assert app.hotkey_manager.suspended is True
        app._on_settings_recording(False)
        assert app.hotkey_manager.suspended is False


class TestOpeningSettings:
    def test_the_popups_gear_opens_the_replace_button_setting(self, app):
        app._show_settings_tab = Mock()
        app._show_settings_hotkeys_tab()
        app._show_settings_tab.assert_called_once_with('Translation', highlight='replace_mode')

    def test_a_page_is_opened_by_name_with_an_optional_row(self, app):
        app.show_settings = Mock()
        window = SimpleNamespace(open_tab=Mock())
        app.settings_window = window
        app._focus_settings_window = Mock()
        app._show_settings_tab('Dictionary')
        app.root.run_all()
        window.open_tab.assert_called_once_with('Dictionary', highlight=None)

    def test_the_row_is_passed_on(self, app):
        app.show_settings = Mock()
        window = SimpleNamespace(open_tab=Mock())
        app.settings_window = window
        app._focus_settings_window = Mock()
        app._show_settings_tab('Translation', highlight='replace_mode')
        app.root.run_all()
        window.open_tab.assert_called_once_with('Translation', highlight='replace_mode')

    def test_the_window_gets_the_applications_answers(self, app, monkeypatch):
        import src.app as app_module
        made = {}

        class Window:
            def __init__(self, *args, **kwargs):
                made['args'], made['kwargs'] = args, kwargs
                self.window = Mock()
        monkeypatch.setattr(app_module, 'SettingsWindow', Window)
        app.hotkey_manager.suspended = True
        app.hotkey_manager.registration = {'English': 'taken'}
        app.config = Mock()
        app.update_manager = Mock()
        app._translation_is_busy = Mock()
        app.show_main_window = Mock()
        app._view = SimpleNamespace(workspace=SimpleNamespace(settings_host=Mock(), show_primary=Mock()))
        app.show_settings()
        args, kwargs = made['args'], made['kwargs']
        assert args[1] is app.config and args[2] == app._on_settings_changed
        assert kwargs['on_shortcut_recording'] == app._on_settings_recording
        status = kwargs['shortcut_status']()
        assert status == {'English': 'taken'} and status is not app.hotkey_manager.registration
        assert app.hotkey_manager.suspended is False                 # a new window starts un-suspended

    def test_an_available_update_opens_the_about_page(self, app):
        app.show_settings = Mock()
        app.settings_window = SimpleNamespace(open_page=Mock(), offer_update=Mock())
        app._open_available_update('9.9.9', 'snapshot')
        app.settings_window.open_page.assert_called_once_with('about')
        app.settings_window.offer_update.assert_called_once_with('9.9.9', 'snapshot')


class TestTheStartingLanguage:
    def test_the_translator_starts_in_the_language_settings_names(self):
        """TranslatorApp.__init__ is too heavy to build in a test: read what it assigns."""
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'src' / 'app.py').read_text(encoding='utf-8'))
        init = next(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == 'TranslatorApp'
                    for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        assigned = [ast.unparse(node.value) for node in ast.walk(init) if isinstance(node, ast.Assign)
                    and any(ast.unparse(t) == 'self.selected_language' for t in node.targets)]
        assert assigned == ['self.config.get_default_target_language()']

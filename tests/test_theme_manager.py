"""
Tests for src/ui/theme/manager.py and windows.py (TODO.md U3a).

The registry and DWM are injected, so every rule - what "follow Windows" means, who is told
when, what a broken subscriber does - runs without a display or a real setting.
"""
import os
import sys
from unittest.mock import patch

import pytest

from src.ui.theme import manager as mgr
from src.ui.theme import tokens, windows


class Windows:
    """A stand-in for the registry: `light` is what AppsUseLightTheme says (None = unreadable)."""

    def __init__(self, light=True):
        self.light = light

    def read(self):
        return self.light

    def detect(self):
        return mgr.detect_system_mode(self.read)


def make(mode='system', light=True):
    win = Windows(light)
    return mgr.ThemeManager(mgr.MemoryStore(mode), detect=win.detect), win


class TestDetection:
    def test_light_and_dark_as_windows_says(self):
        assert mgr.detect_system_mode(lambda: True) == 'light'
        assert mgr.detect_system_mode(lambda: False) == 'dark'

    def test_an_unreadable_setting_is_light(self):
        assert mgr.detect_system_mode(lambda: None) == 'light'

    def test_the_registry_reader_returns_a_bool_or_none(self):
        assert mgr.read_apps_use_light_theme() in (True, False, None)

    def test_a_missing_key_reads_as_none(self):
        import winreg
        with patch.object(winreg, 'OpenKey', side_effect=FileNotFoundError):
            assert mgr.read_apps_use_light_theme() is None

    def test_the_value_is_read_from_the_personalize_key(self):
        import winreg
        seen = {}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(root, path):
            seen['path'] = path
            return Key()

        with patch.object(winreg, 'OpenKey', open_key), \
                patch.object(winreg, 'QueryValueEx', lambda key, name: (0, 4) if name == mgr.APPS_VALUE else None):
            assert mgr.read_apps_use_light_theme() is False
        assert seen['path'] == mgr.PERSONALIZE_KEY


class TestModes:
    def test_system_is_the_default_and_follows_windows(self):
        m, win = make()
        assert (m.mode, m.effective) == ('system', 'pink')
        win.light = False
        assert m.refresh_system() is True
        assert (m.mode, m.effective, m.is_dark) == ('system', 'dark', True)

    def test_a_chosen_mode_ignores_windows(self):
        m, win = make('dark', light=True)
        assert m.effective == 'dark'
        win.light = True
        assert m.refresh_system() is False and m.effective == 'dark'

    def test_the_palette_is_the_one_in_force(self):
        m, _ = make('dark')
        assert m.palette is tokens.DARK
        m.set_mode('light')
        assert m.palette is tokens.LIGHT

    def test_an_unknown_stored_mode_is_system(self):
        m, _ = make('plaid', light=False)
        assert m.mode == 'system' and m.effective == 'dark'

    def test_an_unknown_mode_is_refused_and_changes_nothing(self):
        m, _ = make('dark')
        with pytest.raises(ValueError):
            m.set_mode('plaid')
        assert m.mode == 'dark'

    def test_the_choice_is_persisted(self):
        store = mgr.MemoryStore('system')
        m = mgr.ThemeManager(store, detect=lambda: 'light')
        m.set_mode('dark')
        assert store.get_theme_mode() == 'dark'


class TestSubscribers:
    def test_subscribers_hear_about_a_change_of_theme(self):
        m, _ = make('light')
        heard = []
        m.subscribe(lambda manager: heard.append(manager.effective))
        assert m.set_mode('dark') is True
        assert heard == ['dark']

    def test_no_change_means_no_call(self):
        m, win = make('system', light=False)
        heard = []
        m.subscribe(lambda manager: heard.append(1))
        assert m.set_mode('dark') is False                # system was already dark
        assert m.refresh_system() is False
        assert heard == []

    def test_choosing_system_after_dark_follows_windows(self):
        m, _ = make('dark', light=True)
        heard = []
        m.subscribe(lambda manager: heard.append(manager.effective))
        m.set_mode('system')
        assert heard == ['pink']

    def test_unsubscribe_stops_the_calls(self):
        m, _ = make('light')
        heard = []
        stop = m.subscribe(lambda manager: heard.append(1))
        stop()
        stop()                                            # twice is harmless
        m.set_mode('dark')
        assert heard == []

    def test_a_broken_subscriber_does_not_stop_the_others(self):
        m, _ = make('light')
        heard = []

        def broken(manager):
            raise RuntimeError("boom")

        m.subscribe(broken)
        m.subscribe(lambda manager: heard.append(manager.effective))
        m.set_mode('dark')
        assert heard == ['dark']

    def test_a_subscriber_may_unsubscribe_itself(self):
        m, _ = make('light')
        calls = []
        stop = []

        def once(manager):
            calls.append(1)
            stop[0]()
        stop.append(m.subscribe(once))
        m.set_mode('dark')
        m.set_mode('light')
        assert calls == [1]


class FakeRoot:
    """`after` / `after_cancel` without a Tk: jobs run when the test says so."""

    def __init__(self):
        self.jobs = {}
        self.next = 1
        self.cancelled = []

    def after(self, ms, fn):
        job = f"job{self.next}"
        self.next += 1
        self.jobs[job] = (ms, fn)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)
        self.jobs.pop(job, None)

    def run_all(self):
        for job, (ms, fn) in list(self.jobs.items()):
            self.jobs.pop(job, None)
            fn()


class TestWatching:
    def test_a_poll_notices_windows_changing_theme(self):
        m, win = make('system', light=True)
        root, heard = FakeRoot(), []
        m.subscribe(lambda manager: heard.append(manager.effective))
        m.watch(root, interval_ms=500)
        assert [ms for ms, _ in root.jobs.values()] == [500]
        win.light = False
        root.run_all()
        assert heard == ['dark']
        assert len(root.jobs) == 1                        # and it re-arms

    def test_a_fixed_mode_does_not_poll_the_registry(self):
        reads = []
        m = mgr.ThemeManager(mgr.MemoryStore('dark'), detect=lambda: reads.append(1) or 'light')
        reads.clear()
        root = FakeRoot()
        m.watch(root)
        root.run_all()
        assert reads == []

    def test_watching_twice_leaves_one_timer(self):
        m, _ = make()
        root = FakeRoot()
        m.watch(root)
        m.watch(root)
        assert len(root.jobs) == 1 and len(root.cancelled) == 1

    def test_stop_cancels_the_timer(self):
        m, _ = make()
        root = FakeRoot()
        m.watch(root)
        m.stop_watching()
        assert not root.jobs
        m.stop_watching()                                 # twice is harmless

    def test_a_destroyed_root_ends_the_watch_quietly(self):
        m, _ = make()

        class Dead(FakeRoot):
            def after(self, ms, fn):
                if self.next > 1:
                    raise RuntimeError("application has been destroyed")
                return super().after(ms, fn)
        root = Dead()
        m.watch(root)
        root.run_all()                                     # must not raise


class TestTheApplicationsManager:
    def test_there_is_always_a_manager(self):
        mgr.install_manager(None)
        try:
            assert isinstance(mgr.get_manager(), mgr.ThemeManager)
            assert mgr.palette() in (tokens.PINK, tokens.DARK)
        finally:
            mgr.install_manager(None)

    def test_install_replaces_it(self):
        m, _ = make('dark')
        try:
            assert mgr.install_manager(m) is m
            assert mgr.get_manager() is m and mgr.palette() is tokens.DARK
        finally:
            mgr.install_manager(None)


class FakeWindow:
    def wm_frame(self):
        return '0x1f2e'


class TestWindowChrome:
    def recorder(self, accept=True):
        calls = []

        def setter(hwnd, attribute, value):
            calls.append((hwnd, attribute, value))
            return 0 if accept else 0x80070057
        return calls, setter

    def test_colorref_is_bgr(self):
        assert windows.colorref('#112233') == 0x332211

    def test_dark_theme_turns_dark_mode_on_and_paints_the_caption(self):
        calls, setter = self.recorder()
        assert windows.apply_title_bar(FakeWindow(), tokens.DARK, setter) is True
        attrs = {attribute: value for _, attribute, value in calls}
        assert attrs[windows.DWMWA_USE_IMMERSIVE_DARK_MODE] == 1
        assert attrs[windows.DWMWA_CAPTION_COLOR] == windows.colorref(tokens.DARK.bg)
        assert attrs[windows.DWMWA_TEXT_COLOR] == windows.colorref(tokens.DARK.text)
        assert attrs[windows.DWMWA_BORDER_COLOR] == windows.colorref(tokens.DARK.border)
        assert all(hwnd == 0x1f2e for hwnd, _, _ in calls)

    def test_light_theme_turns_dark_mode_off(self):
        calls, setter = self.recorder()
        windows.apply_title_bar(FakeWindow(), tokens.LIGHT, setter)
        assert {a: v for _, a, v in calls}[windows.DWMWA_USE_IMMERSIVE_DARK_MODE] == 0

    def test_a_refusing_dwm_is_reported_not_raised(self):
        _, setter = self.recorder(accept=False)
        assert windows.apply_title_bar(FakeWindow(), tokens.DARK, setter) is False

    def test_a_window_without_a_frame_is_skipped(self):
        class Gone:
            def wm_frame(self):
                raise RuntimeError("no such window")
        _, setter = self.recorder()
        assert windows.apply_title_bar(Gone(), tokens.DARK, setter) is False
        assert windows.round_corners(Gone(), setter=setter) is False

    def test_corner_preference_round_or_small(self):
        calls, setter = self.recorder()
        assert windows.round_corners(FakeWindow(), setter=setter) is True
        windows.round_corners(FakeWindow(), small=True, setter=setter)
        assert [(a, v) for _, a, v in calls] == [
            (windows.DWMWA_WINDOW_CORNER_PREFERENCE, windows.DWMWCP_ROUND),
            (windows.DWMWA_WINDOW_CORNER_PREFERENCE, windows.DWMWCP_ROUNDSMALL)]

    def test_the_default_icon_is_one_call_on_the_root(self):
        seen = []

        class Root:
            def iconbitmap(self, **kw):
                seen.append(kw)
        assert windows.set_default_icon(Root(), 'x.ico') is True
        assert seen == [{'default': 'x.ico'}]
        assert windows.set_default_icon(Root(), None) is False

    def test_a_missing_icon_file_is_not_fatal(self):
        class Root:
            def iconbitmap(self, **kw):
                raise RuntimeError("bitmap not defined")
        assert windows.set_default_icon(Root(), 'missing.ico') is False

    def test_the_real_setter_never_raises_on_a_bad_handle(self):
        assert isinstance(windows.dwm_set(0, windows.DWMWA_CAPTION_COLOR, 0), int)


class TestRealWindow:
    def test_a_real_toplevel_takes_the_dark_title_bar(self, tk_root):
        import tkinter as tk
        window = tk.Toplevel(tk_root)
        try:
            window.update()
            assert windows.apply_title_bar(window, tokens.DARK) is True
        finally:
            window.destroy()

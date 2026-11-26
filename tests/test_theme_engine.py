"""
Tests for src/ui/theme/engine.py (TODO.md U4a, Decision 45): our tokens on ttkbootstrap 2.x.

The pure half checks the colour scheme handed to ttkbootstrap; the real half installs the engine
on the shared test root and watches a theme change repaint the styles and the window chrome.
"""
import tkinter as tk

import pytest

from src.ui.theme import compat, engine, tokens, windows
from src.ui.theme.manager import MemoryStore, ThemeManager

COLOR_FIELDS = {'primary', 'secondary', 'success', 'info', 'warning', 'danger', 'light', 'dark',
                'bg', 'fg', 'selectbg', 'selectfg', 'border', 'inputfg', 'inputbg', 'active'}


class TestColourScheme:
    @pytest.mark.parametrize('palette', [tokens.LIGHT, tokens.DARK], ids=lambda p: p.mode)
    def test_the_scheme_has_every_field_ttkbootstrap_wants(self, palette):
        assert set(engine.colors_for(palette)) == COLOR_FIELDS

    @pytest.mark.parametrize('palette', [tokens.LIGHT, tokens.DARK], ids=lambda p: p.mode)
    def test_the_tokens_land_where_widgets_look_for_them(self, palette):
        colors = engine.colors_for(palette)
        assert colors['bg'] == palette.bg and colors['fg'] == palette.text
        assert colors['primary'] == palette.accent == colors['info']
        assert colors['inputbg'] == palette.surface and colors['border'] == palette.border
        assert colors['selectbg'] == palette.selection
        assert colors['success'] == palette.success and colors['danger'] == palette.danger

    def test_the_hover_tint_is_a_valid_colour_between_text_and_window(self):
        from src.ui.theme.contrast import hex_to_rgb
        for palette in (tokens.LIGHT, tokens.DARK):
            hex_to_rgb(engine.colors_for(palette)['active'])

    def test_all_palette_definitions_have_distinct_names(self):
        definitions = {definition.name: definition for definition in engine.definitions()}
        assert len(definitions) == len(tokens.PALETTES) == 14
        assert set(definitions) == {engine.theme_name(name) for name in tokens.PALETTES}
        light, dark = definitions[engine.theme_name("light")], definitions[engine.theme_name("dark")]
        assert (light.name, light.mode) == ('crosstrans-light', 'light')
        assert (dark.name, dark.mode) == ('crosstrans-dark', 'dark')
        assert str(light.colors.bg).upper() == tokens.LIGHT.bg.upper()

    def test_theme_names(self):
        assert engine.theme_name('dark') == 'crosstrans-dark'


class TestCompat:
    def test_this_environment_runs_ttkbootstrap_2(self):
        assert compat.ttkbootstrap_major() == 2

    def test_on_2x_a_tk_widget_takes_no_autostyle_option(self):
        assert compat.NO_AUTOSTYLE == {}

    def test_a_tk_widget_keeps_the_colour_it_is_given(self, tk_root):
        label = tk.Label(tk_root, text='x', fg='#ff0000', bg='#00ff00', **compat.NO_AUTOSTYLE)
        try:
            assert (str(label.cget('fg')), str(label.cget('bg'))) == ('#ff0000', '#00ff00')
        finally:
            label.destroy()


class TestExplicitColoursSurviveAThemeChange:
    """ttkbootstrap 2.x repaints raw tk widgets on a theme switch unless they opted out; a widget
    that chose its colours must keep them, one that did not must follow the theme."""

    def test_a_widget_with_its_own_colours_keeps_them_through_both_switches(self, installed, tk_root):
        label = tk.Label(tk_root, text='x', fg='#ff0000', bg='#00ff00')
        try:
            installed.manager.set_mode('dark')
            installed.manager.set_mode('light')
            installed.manager.set_mode('dark')
            assert (str(label.cget('fg')), str(label.cget('bg'))) == ('#ff0000', '#00ff00')
        finally:
            label.destroy()

    def test_a_widget_with_no_colours_follows_the_theme(self, installed, tk_root):
        frame = tk.Frame(tk_root)
        try:
            installed.manager.set_mode('dark')
            assert str(frame.cget('bg')).upper() == tokens.DARK.bg.upper()
            installed.manager.set_mode('light')
            assert str(frame.cget('bg')).upper() == tokens.LIGHT.bg.upper()
        finally:
            frame.destroy()

    def test_colours_given_in_a_config_dict_count_too(self, installed, tk_root):
        label = tk.Label(tk_root, {'text': 'x', 'bg': '#123456'})
        try:
            installed.manager.set_mode('dark')
            assert str(label.cget('bg')) == '#123456'
        finally:
            label.destroy()

    def test_a_widget_coloured_after_the_fact_can_opt_out(self, installed, tk_root):
        label = tk.Label(tk_root, text='x')
        try:
            label.configure(bg='#112233')
            compat.keep_colours(label)
            installed.manager.set_mode('dark')
            assert str(label.cget('bg')) == '#112233'
        finally:
            label.destroy()

    def test_a_ttk_widget_keeps_an_explicit_foreground(self, installed, tk_root):
        from tkinter import ttk
        label = ttk.Label(tk_root, text='x', foreground='#ff0000')
        try:
            installed.manager.set_mode('dark')
            assert str(label.cget('foreground')) == '#ff0000'
        finally:
            label.destroy()


class TestTheGuard:
    def test_it_is_idempotent_and_reversible(self):
        compat.unprotect_explicit_colours()
        assert compat.protect_explicit_colours() is True
        assert compat.protect_explicit_colours() is False
        compat.unprotect_explicit_colours()
        assert tk.BaseWidget.__init__ is compat._ORIGINAL_INIT

    def test_the_colour_options_are_the_ones_the_audit_counts(self):
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
        import ui_audit
        assert compat.COLOUR_OPTIONS == frozenset(ui_audit.COLOUR_OPTIONS)


class FakeWindow:
    """Enough of a Toplevel for the chrome code, recording what it was told."""

    def __init__(self, framed=True, alive=True):
        self.framed, self.alive, self.bg = framed, alive, None

    def winfo_exists(self):
        return self.alive

    def overrideredirect(self):
        return not self.framed

    def update_idletasks(self):
        pass

    def configure(self, **kw):
        self.bg = kw.get('bg', self.bg)

    def wm_frame(self):
        return '0x10'


@pytest.fixture
def installed(tk_root):
    """The engine on the shared root with a manager in light mode and Windows set to dark."""
    manager = ThemeManager(MemoryStore('light'), detect=lambda: 'dark')
    eng = engine.install(tk_root, manager)
    try:
        yield eng
    finally:
        engine.reset()
        try:
            eng.style.theme_use('bootstrap-light')
        except Exception:
            pass


class TestInstalled:
    def test_the_manager_chooses_the_theme(self, installed):
        assert installed.style.theme.name == 'crosstrans-light'
        assert str(installed.style.colors.bg).upper() == tokens.LIGHT.bg.upper()

    def test_a_ttk_button_is_filled_with_the_accent(self, installed, tk_root):
        import ttkbootstrap as ttk
        button = ttk.Button(tk_root, text='x', bootstyle='primary')
        try:
            assert str(installed.style.colors.primary).upper() == tokens.LIGHT.accent.upper()
            assert str(installed.style.lookup(button.cget('style'), 'background')).upper() \
                in (tokens.LIGHT.accent.upper(), tokens.LIGHT.hover.upper(), '')
        finally:
            button.destroy()

    def test_changing_the_mode_switches_the_theme_and_the_root(self, installed, tk_root):
        installed.manager.set_mode('dark')
        assert installed.style.theme.name == 'crosstrans-dark'
        assert str(installed.style.colors.bg).upper() == tokens.DARK.bg.upper()
        assert tk_root.cget('bg').upper() == tokens.DARK.bg.upper()
        installed.manager.set_mode('light')
        assert installed.style.theme.name == 'crosstrans-light'

    def test_the_engine_is_reachable_and_reset_forgets_it(self, installed):
        assert engine.get_engine() is installed
        engine.reset()
        assert engine.get_engine() is None

    def test_stop_unsubscribes(self, installed):
        installed.stop()
        installed.manager.set_mode('dark')
        assert installed.style.theme.name == 'crosstrans-light'
        installed.stop()                                   # twice is harmless


class TestWindowChrome:
    def test_attach_styles_the_window_now_and_on_every_change(self, installed, monkeypatch):
        seen = []
        monkeypatch.setattr(windows, 'apply_title_bar',
                            lambda window, palette, setter=None: seen.append(palette.mode))
        window = FakeWindow()
        engine.attach(window)
        assert seen == ['light'] and window.bg == tokens.LIGHT.bg
        installed.manager.set_mode('dark')
        assert seen == ['light', 'dark'] and window.bg == tokens.DARK.bg

    def test_a_popup_without_a_frame_gets_a_background_but_no_title_bar(self, installed, monkeypatch):
        seen = []
        monkeypatch.setattr(windows, 'apply_title_bar', lambda *a, **k: seen.append(1))
        window = FakeWindow(framed=False)
        engine.attach(window)
        assert seen == [] and window.bg == tokens.LIGHT.bg

    def test_a_destroyed_window_is_skipped_quietly(self, installed, monkeypatch):
        monkeypatch.setattr(windows, 'apply_title_bar', lambda *a, **k: None)
        window = FakeWindow(alive=False)
        engine.attach(window)
        installed.manager.set_mode('dark')                 # must not raise
        assert window.bg is None

    def test_tracked_windows_are_held_weakly(self, installed, monkeypatch):
        import gc
        monkeypatch.setattr(windows, 'apply_title_bar', lambda *a, **k: None)
        window = FakeWindow()
        engine.attach(window)
        assert len(installed._windows) == 1
        del window
        gc.collect()
        assert len(installed._windows) == 0

    def test_attach_before_install_is_a_no_op(self):
        engine.reset()
        engine.attach(FakeWindow())                        # must not raise

    def test_a_real_toplevel_follows_the_theme(self, installed, tk_root):
        window = tk.Toplevel(tk_root)
        try:
            window.update()
            engine.attach(window)
            assert windows_dark(window) is False
            installed.manager.set_mode('dark')
            assert windows_dark(window) is True
        finally:
            window.destroy()


def windows_dark(window):
    """DWM's immersive-dark-mode attribute of the window, read back."""
    import ctypes
    from ctypes import wintypes
    value = ctypes.c_int(0)
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(int(window.wm_frame(), 16)), 20, ctypes.byref(value), ctypes.sizeof(value))
    assert hr == 0
    return bool(value.value)

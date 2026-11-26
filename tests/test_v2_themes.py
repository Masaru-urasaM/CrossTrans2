"""Every registered palette survives selection, persistence and live repaint."""
import tkinter as tk
import pytest

from src.ui.theme import engine, tokens
from src.ui.theme.manager import MemoryStore, ThemeManager


@pytest.mark.parametrize('preset', tuple(tokens.PALETTES))
def test_explicit_preset_survives_reload_and_windows_changes(preset):
    store = MemoryStore(preset)
    first = ThemeManager(store, detect=lambda: 'dark')
    assert first.palette is tokens.PALETTES[preset]
    second = ThemeManager(store, detect=lambda: 'light')
    assert second.mode == second.effective == preset
    assert second.palette is tokens.PALETTES[preset]


def test_follow_windows_uses_pink_and_black_and_keeps_the_user_choice():
    windows = ['light']
    manager = ThemeManager(MemoryStore(), detect=lambda: windows[0])
    assert manager.mode == 'system' and manager.palette is tokens.PINK
    windows[0] = 'dark'
    assert manager.refresh_system()
    assert manager.mode == 'system' and manager.palette is tokens.DARK
    manager.set_mode('yellow')
    windows[0] = 'light'
    assert not manager.refresh_system()
    assert manager.palette is tokens.YELLOW


def test_engine_repaints_between_two_light_presets(tk_root):
    manager = ThemeManager(MemoryStore('pink'), detect=lambda: 'light')
    installed = engine.install(tk_root, manager)
    label = tk.Label(tk_root, text='Retained content')
    try:
        pink_style = installed.style.theme_use()
        manager.set_mode('green')
        assert installed.style.theme_use() != pink_style
        assert str(installed.style.lookup('TFrame', 'background')).upper() == tokens.GREEN.bg.upper()
        assert label.cget('text') == 'Retained content'
        assert manager.palette.mode == 'light'
    finally:
        label.destroy()
        engine.reset()


@pytest.mark.parametrize('preset', tuple(tokens.PALETTES))
def test_real_config_accepts_and_reloads_every_preset(monkeypatch, tmp_path, preset):
    import src.core
    from config import Config
    monkeypatch.setattr(Config, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(Config, 'CONFIG_FILE', str(tmp_path / 'config.json'))
    config = Config()
    config.set_theme_mode(preset)
    restored = Config()
    assert restored.get_theme_mode() == preset
    manager = ThemeManager(restored, detect=lambda: 'dark')
    assert manager.palette is tokens.PALETTES[preset]

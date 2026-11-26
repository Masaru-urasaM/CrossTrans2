"""Main Settings is a persistent page; switching tabs preserves drafts and active work."""
import tkinter as tk
from unittest.mock import Mock

from tests.final_ui_helpers import design
from tests.settings_support import SettingsConfig
from src.ui import components as ui
from src.ui.main_workspace import MainWorkspace
from src.ui.settings.main import SettingsWindow


def workspace(design):
    window = tk.Toplevel(design.root)
    window.geometry('1100x700')
    translation = ui.Frame(window)
    translation.pack(fill='both', expand=True)
    text = tk.Text(translation)
    text.pack(fill='both', expand=True)
    text.insert('1.0', 'Unfinished source draft')
    shell = MainWorkspace(window, translation, lambda: shell.show_primary('settings'))
    settings = SettingsWindow(design.root, SettingsConfig(), container=shell.settings_host,
                               on_close=lambda: shell.show_primary('home'))
    shell.settings = settings
    window.update_idletasks()
    return window, shell, settings, text


def test_settings_is_embedded_in_the_same_toplevel(design):
    window, shell, settings, text = workspace(design)
    assert settings.window.winfo_toplevel() is window
    assert not isinstance(settings.window, tk.Toplevel)
    shell.show_primary('settings')
    window.update_idletasks()
    assert settings.window.winfo_viewable()
    assert not shell.home_host.winfo_viewable()
    shell.show_primary('home')
    window.update_idletasks()
    assert shell.home_host.winfo_viewable()
    assert text.get('1.0', 'end-1c') == 'Unfinished source draft'


def test_switching_away_is_not_settings_destruction_or_download_cancellation(design):
    window, shell, settings, text = workspace(design)
    shell.show_primary('settings')
    settings._download_active = True
    settings._close_update_work = Mock()
    shell.show_primary('home')
    assert settings._download_active
    assert settings.window.winfo_exists()
    settings._close_update_work.assert_not_called()
    shell.show_primary('settings')
    assert text.get('1.0', 'end-1c') == 'Unfinished source draft'


def test_leaving_settings_flushes_pending_key_edits_and_masks_revealed_keys(design):
    window, shell, settings, text = workspace(design)
    shell.show_primary('settings')
    settings.open_page('providers')
    page = settings.pages['providers']
    card = page.cards[0]
    card.field.set('fixture-new-key')
    card._typed()
    card.set_revealed(True)
    assert page._save_job is not None
    shell.show_primary('home')
    assert page._save_job is None
    assert settings.config.get_api_keys()[0]['api_key'] == 'fixture-new-key'
    assert not card.revealed and card.field.cget('show') == '*'
    assert settings.window.winfo_exists()


def test_escape_from_settings_returns_home_without_closing_the_window(design):
    window, shell, settings, text = workspace(design)
    shell.show_primary('settings')
    settings._escape()
    assert shell.primary == 'home'
    assert window.winfo_exists() and settings.window.winfo_exists()


def test_production_translator_is_built_inside_home_instead_of_behind_its_sibling(design):
    from src.ui.translator_window import TranslatorWindow
    from src.constants import LANGUAGES
    view = TranslatorWindow(design.root, SettingsConfig(), LANGUAGES)
    view.build('', 'English', recent=lambda: [], pinned=lambda: [])
    view.show()
    assert view.frame.master is view.workspace.translate_host
    assert view.frame.winfo_viewable() and view.translate_btn.winfo_viewable()
    view.workspace.show_primary('settings')
    view.window.update_idletasks()
    assert not view.frame.winfo_viewable()
    view.workspace.show_primary('home')
    view.window.update_idletasks()
    assert view.frame.winfo_viewable() and view.translate_btn.winfo_viewable()


def test_narrow_translator_keeps_all_primary_actions_fully_visible(design):
    from src.ui.translator_window import TranslatorWindow
    from src.constants import LANGUAGES
    view = TranslatorWindow(design.root, SettingsConfig(), LANGUAGES)
    view.build('source', 'English', recent=lambda: [], pinned=lambda: [])
    view.window.minsize(1, 1)
    view.window.geometry('650x600')
    view.show()
    for _ in range(3):
        view.window.update()
    for button in (view.options_btn, view.fix_grammar_btn, view.translate_btn):
        assert button.winfo_ismapped()
        assert button.winfo_width() >= button.winfo_reqwidth()


def test_resizing_action_rows_never_leaves_overlapping_column_spans(design):
    from src.ui.translator_window import TranslatorWindow
    from src.constants import LANGUAGES
    view = TranslatorWindow(design.root, SettingsConfig(), LANGUAGES)
    view.build('source', 'English', recent=lambda: [], pinned=lambda: [])
    view.window.minsize(1, 1)
    view.show()
    for width in [650, 1200, 700, 1500]:
        view.window.geometry(f'{width}x650')
        for _ in range(3): view.window.update()
        buttons = [view.options_btn, view.fix_grammar_btn, view.translate_btn]
        rectangles = [(b.winfo_rootx(), b.winfo_rooty(), b.winfo_width(), b.winfo_height()) for b in buttons]
        for i, (x,y,w,h) in enumerate(rectangles):
            for ox,oy,ow,oh in rectangles[i+1:]:
                assert x+w <= ox or ox+ow <= x or y+h <= oy or oy+oh <= y


def test_main_window_can_be_reopened_immediately_after_close(design):
    from types import SimpleNamespace
    from src.app import TranslatorApp
    owner = SimpleNamespace(popup=None, root=design.root, selected_language='English', show_popup=Mock())
    TranslatorApp.show_main_window(owner)
    assert owner.show_popup.call_count == 1 and not owner._showing_popup
    TranslatorApp.show_main_window(owner)
    assert owner.show_popup.call_count == 2 and not owner._showing_popup

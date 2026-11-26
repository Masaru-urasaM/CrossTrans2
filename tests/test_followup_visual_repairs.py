"""Regressions derived from inspected native follow-up captures."""
import tkinter as tk
from tkinter import ttk

import pytest

from src.core.text_files import read_text_file
from src.ui import components as ui, popup_errors
from src.ui.theme.compat import NO_AUTOSTYLE
from tests.final_ui_helpers import design, pump
from tests.settings_support import Host
from tests.test_source_choice import main_window


def test_a_literal_replacement_character_is_valid_user_text(tmp_path):
    path = tmp_path / 'unicode.md'
    text = 'The Unicode replacement character is \ufffd.\n'
    path.write_bytes(text.encode('utf-8'))
    assert read_text_file(path) == text


def test_disabled_swap_explains_the_required_source_choice(main_window):
    app = main_window('')
    view = app._view
    assert view.swap_btn.tooltip.text == 'Choose a supported source language to swap.'
    view.set_swap_enabled(True)
    assert view.swap_btn.tooltip.text == 'Swap languages'


def test_hidden_ancestor_is_excluded_from_contrast_but_visible_failure_is_retained(design):
    from tools import ui_gallery
    window = tk.Toplevel(design.root)
    window.geometry('400x300')
    parent = ui.Frame(window)
    parent.pack(fill='x')
    label = tk.Label(parent, text='Visible low contrast fixture', fg='white', bg='white', **NO_AUTOSTYLE)
    label.pack()
    window.update()
    assert any('Visible low contrast fixture' in row.widget
               for row in ui_gallery.low_contrast(window, ttk.Style(window)))
    parent.pack_forget()
    assert not ui_gallery.low_contrast(window, ttk.Style(window))


@pytest.mark.parametrize('language', ['en', 'ja'])
@pytest.mark.parametrize('width', [300, 420, 620])
def test_large_error_bar_keeps_message_and_action_inside(design, language, width):
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(1.5)
    window = tk.Toplevel(design.root)
    window.geometry(f'{width}x500')
    message = popup_errors.explain('Error: API key invalid or quota exceeded (401)').message
    bar = ui.InfoBar(window, message, kind='danger', action='API Settings')
    bar.pack(fill='x')
    pump(window, lambda: bar.winfo_width() == width)
    for _ in range(3):
        window.update()
    right = bar.winfo_rootx() + bar.winfo_width()
    for widget in (bar.text, bar.action):
        assert widget.winfo_rootx() + widget.winfo_width() <= right
        assert widget.winfo_width() + 1 >= widget.winfo_reqwidth()


@pytest.mark.parametrize('language', ['en', 'ja'])
def test_shortcut_wraps_long_keycaps_and_recovers_when_widened(design, language):
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(1.5)
    window = tk.Toplevel(design.root)
    row = ui.SettingRow(window, 'Japanese')
    row.pack(fill='x')
    editor = ui.ShortcutEditor(row.control, 'ctrl+alt+shift+win+page down', default='win+alt+j')
    editor.pack(fill='x', expand=True)
    for width in (360, 1200, 360, 1200):
        window.geometry(f'{width}x600')
        for _ in range(4):
            window.update()
        for widget in (editor.change_btn, editor.clear_btn, *editor.keycaps.caps):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() + widget.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert widget.winfo_width() + 1 >= widget.winfo_reqwidth()
        if width == 1200:
            assert not editor._line_wrapped


def test_repeated_stacked_resizes_keep_real_editors_and_scrollbar(main_window):
    app = main_window('私は毎日日本語を勉強しています。' * 4, 'A translated paragraph. ' * 4)
    view = app._view
    view.ctx.manager.set_ui_language('ja')
    view.ctx.manager.set_text_scale(1.5)
    view.ctx.manager.set_mode('dark')
    for width in (1400, 700, 1200, 700, 1400, 700):
        app.popup.geometry(f'{width}x640')
        pump(app.root, lambda: app.popup.winfo_width() == width and view._arrange_job is None
             and view.body_area._layout_job is None
             and view._last_size == (view.frame.winfo_width(), view.frame.winfo_height()))
        for _ in range(3):
            app.root.update()
        assert app.original_text.winfo_viewable() and app.trans_text.winfo_viewable()
        region = view.body_area.canvas.bbox('all')
        if region and region[3] - region[1] > view.body_area.canvas.winfo_height() + 3:
            pump(app.root, lambda: bool(view.body_area.scrollbar.winfo_manager()))


def test_installed_summary_wraps_at_large_japanese_minimum(design, monkeypatch):
    from src.ui.settings import page_dictionary as module
    from tests.test_settings_dictionary import FakeNLP, PACKS
    monkeypatch.setattr(module, 'load_nlp', lambda: (FakeNLP(installed=list(PACKS)), PACKS))
    monkeypatch.setattr(module, 'reader_is_accurate', lambda: False)
    design.manager.set_ui_language('ja')
    design.manager.set_text_scale(1.5)
    window = tk.Toplevel(design.root)
    window.geometry('420x700')
    page = module.DictionaryPage(Host(window))
    page.build(window).pack(fill='both', expand=True)
    pump(window, lambda: page.scanned)
    for width in (420, 1200, 420):
        window.geometry(f'{width}x700')
        for _ in range(4):
            window.update()
        summary = page.installed_summary
        assert summary.winfo_width() + 1 >= summary.winfo_reqwidth()
        assert summary.winfo_rootx() + summary.winfo_width() <= window.winfo_rootx() + window.winfo_width()
    for theme in ('pink', 'blue', 'yellow', 'green', 'light', 'dark'):
        design.manager.set_mode(theme)
        window.update()
        assert summary.winfo_width() + 1 >= summary.winfo_reqwidth()

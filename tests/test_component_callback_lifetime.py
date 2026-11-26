"""Flyouts and editors must release deferred work when their own UI disappears."""
import tkinter as tk
import pytest
from src.ui import components as ui
from tests.test_components import env, holder, light, shown


@pytest.mark.parametrize('kind', ['language', 'dropdown'])
def test_closing_flyout_cancels_its_focus_check(holder, light, tk_root, kind):
    if kind == 'language':
        picker = ui.LanguagePicker(holder, [('English', 'en', 'English'), ('Japanese', 'ja', 'Japanese')],
                                   'English', lambda value: None)
    else:
        picker = ui.Dropdown(holder, [('en', 'English'), ('ja', 'Japanese')], 'en')
    picker.pack()
    picker.open()
    window = picker.window
    assert window is not None
    before = set(tk_root.tk.call('after', 'info'))
    window.event_generate('<FocusOut>', when='now')
    scheduled = set(tk_root.tk.call('after', 'info')) - before
    assert scheduled, 'The flyout did not defer its focus check'
    picker.close()
    assert not scheduled.intersection(tk_root.tk.call('after', 'info'))


def test_destroying_shortcut_editor_cancels_its_deferred_refocus(holder, light, tk_root):
    editor = ui.ShortcutEditor(holder, 'win+alt+e', owner='English')
    before = set(tk_root.tk.call('after', 'info'))
    editor._refocus()
    scheduled = set(tk_root.tk.call('after', 'info')) - before
    assert scheduled
    editor.destroy()
    assert not scheduled.intersection(tk_root.tk.call('after', 'info'))


def test_destroying_entry_cancels_its_deferred_variable_sync(holder, light, tk_root):
    field = ui.TextField(holder, textvariable=tk.StringVar(tk_root, value='Fixture'))
    field.bindtags((str(field),))  # Exercise the custom binding without a real clipboard action.
    before = set(tk_root.tk.call('after', 'info'))
    field.event_generate('<<Paste>>', when='now')
    scheduled = set(tk_root.tk.call('after', 'info')) - before
    assert scheduled
    field.destroy()
    assert not scheduled.intersection(tk_root.tk.call('after', 'info'))


def test_deferred_refocus_cannot_take_focus_after_editor_is_hidden(shown, light, tk_root, monkeypatch):
    import time
    from unittest.mock import Mock
    editor = ui.ShortcutEditor(shown, 'win+alt+e', owner='English')
    editor.pack()
    shown.update()
    assert editor.winfo_viewable()
    focus = Mock()
    monkeypatch.setattr(editor.change_btn, 'focus_set', focus)
    editor._refocus()
    editor.pack_forget()
    tk_root.update_idletasks()
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        tk_root.update()
        time.sleep(0.005)
    assert not editor.winfo_viewable()
    focus.assert_not_called()

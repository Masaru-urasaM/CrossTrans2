"""Closing a Translator must remove its delayed Tcl work before command names can be reused."""
import tkinter as tk
from tests.test_source_choice import main_window


def test_closing_translator_cancels_its_pending_drop_setup(main_window, tk_root, monkeypatch):
    scheduled = []
    original = tk.Misc.after
    def record(widget, delay, callback=None, *args):
        job = original(widget, delay, callback, *args)
        if callback is not None and delay == 300:
            scheduled.append(job)
        return job
    monkeypatch.setattr(tk.Misc, 'after', record)
    app = main_window('Fixture text')
    assert scheduled, 'The real Translator did not schedule its deferred drop setup'
    app.popup.destroy()
    pending = set(tk_root.tk.call('after', 'info'))
    assert not pending.intersection(scheduled), 'Closing left a Tcl timer pointing at a deleted callback'


def test_closing_translator_cancels_pending_focus_release(main_window, tk_root, monkeypatch):
    app = main_window('Fixture text')
    scheduled = []
    original = tk.Misc.after
    def record(widget, delay, callback=None, *args):
        job = original(widget, delay, callback, *args)
        if callback is not None and delay == 100:
            scheduled.append(job)
        return job
    monkeypatch.setattr(tk.Misc, 'after', record)
    app.popup.event_generate('<FocusIn>', when='now')
    assert scheduled
    app.popup.destroy()
    assert not set(scheduled).intersection(tk_root.tk.call('after', 'info'))


def test_child_destruction_does_not_cancel_the_owner_callback(tk_root):
    from src.utils.ui_helpers import schedule_window_callback
    owner = tk.Toplevel(tk_root)
    child = tk.Label(owner, text='Child')
    job = schedule_window_callback(tk_root, owner, 10000, lambda: None)
    child.destroy()
    assert job in tk_root.tk.call('after', 'info')
    owner.destroy()
    assert job not in tk_root.tk.call('after', 'info')


def test_completed_callback_is_removed_from_its_owner(tk_root):
    from src.utils.ui_helpers import schedule_window_callback
    owner = tk.Toplevel(tk_root)
    seen = []
    schedule_window_callback(tk_root, owner, 0, lambda: seen.append('done'))
    tk_root.update()
    assert seen == ['done']
    assert not owner._ct_window_callback_jobs
    owner.destroy()


def test_cancelled_callback_releases_window_bookkeeping(tk_root):
    from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback
    owner = tk.Toplevel(tk_root)
    job = schedule_window_callback(tk_root, owner, 10000, lambda: None)
    cancel_window_callback(tk_root, owner, job)
    assert not owner._ct_window_callback_jobs
    assert job not in tk_root.tk.call('after', 'info')
    owner.destroy()


def test_closing_translator_cancels_reading_and_animation(main_window, tk_root):
    app = main_window('Fixture text')
    app._schedule_reading_refresh(10000)
    reading = app._reading_pane_job
    app._start_translate_animation()
    animation = getattr(app, '_translate_animation_job', None)
    app.popup.destroy()
    pending = set(tk_root.tk.call('after', 'info'))
    assert reading not in pending
    assert animation is None or animation not in pending


def test_late_request_for_a_closed_window_schedules_nothing(tk_root):
    from src.utils.ui_helpers import schedule_window_callback
    owner = tk.Toplevel(tk_root)
    owner.destroy()
    before = set(tk_root.tk.call('after', 'info'))
    assert schedule_window_callback(tk_root, owner, 10, lambda: None) is None
    assert set(tk_root.tk.call('after', 'info')) == before


def test_quick_copy_feedback_belongs_to_its_original_popup(tk_root, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.app import TranslatorApp
    import src.app as app_module
    app = TranslatorApp.__new__(TranslatorApp)
    app.root = tk_root
    popup = tk.Toplevel(tk_root)
    app.quick_translate_manager = SimpleNamespace(popup=popup, set_copy_button_text=Mock())
    app.toast = Mock()
    app.current_translated = 'Fixture result'
    monkeypatch.setattr(app_module.pyperclip, 'copy', Mock())
    before = set(tk_root.tk.call('after', 'info'))
    app._on_quick_translate_copy()
    scheduled = set(tk_root.tk.call('after', 'info')) - before
    assert scheduled
    popup.destroy()
    try:
        assert not scheduled.intersection(tk_root.tk.call('after', 'info'))
    finally:
        for job in scheduled:
            tk_root.after_cancel(job)


def test_destroying_search_results_cancels_deferred_teardown(tk_root):
    from src.ui.settings.main import SettingsWindow
    owner = tk.Toplevel(tk_root)
    old = tk.Frame(owner)
    settings = SettingsWindow.__new__(SettingsWindow)
    settings._results = old
    settings.current = None
    settings._frames = {}
    before = set(tk_root.tk.call('after', 'info'))
    settings._leave_results()
    scheduled = set(tk_root.tk.call('after', 'info')) - before
    assert scheduled
    owner.destroy()
    try:
        assert not scheduled.intersection(tk_root.tk.call('after', 'info'))
    finally:
        for job in scheduled:
            tk_root.after_cancel(job)

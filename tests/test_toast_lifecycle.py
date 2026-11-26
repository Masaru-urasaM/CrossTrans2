"""Toast timers must outlive their Tcl commands only until explicitly cancelled."""
import time

import pytest

from src.ui.toast import ToastType
from tests.test_components import env  # noqa: F401
from tests.test_toast_notice_design import toasts  # noqa: F401


def pending(root):
    return set(root.tk.splitlist(root.tk.call('after', 'info')))


@pytest.mark.parametrize('shake', [False, True])
def test_timer_commands_belong_to_the_root(toasts, tk_root, shake):
    before = pending(tk_root)
    toast = toasts.show_warning_with_shake('Notice') if shake else toasts.show('Notice')
    jobs = pending(tk_root) - before
    assert jobs
    for job in jobs:
        command = tk_root.tk.splitlist(tk_root.tk.call('after', 'info', job))[0]
        assert command in tk_root._tclCommands
        assert command not in (toast._tclCommands or [])


@pytest.mark.parametrize('shake', [False, True])
def test_dismiss_cancels_animation_and_shake_jobs(toasts, tk_root, shake):
    before = pending(tk_root)
    toast = toasts.show_warning_with_shake('Notice') if shake else toasts.show('Notice')
    jobs = pending(tk_root) - before
    assert jobs
    toasts.dismiss_all()
    assert not (jobs & pending(tk_root))
    assert not toast.winfo_exists()


def test_external_destroy_cancels_dismiss_and_forgets_the_toast(toasts, tk_root):
    toasts.FADE_IN_STEP = 1
    before = pending(tk_root)
    toast = toasts.show('Notice', duration=1000)
    jobs = pending(tk_root) - before
    assert toast in toasts._dismiss_callbacks and jobs
    toast.destroy()
    assert not (jobs & pending(tk_root))
    assert toast not in toasts.active_toasts
    assert toast not in toasts._dismiss_callbacks


def test_child_destroy_does_not_cancel_the_toast(toasts, tk_root):
    toast = toasts.show('Notice')
    before = pending(tk_root)
    toast.bar.text.destroy()
    assert toast in toasts.active_toasts
    assert pending(tk_root) == before


def test_automatic_expiry_still_dismisses(toasts, tk_root, env, monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(env.manager, 'accessibility', replace(env.manager.accessibility, notification_ms=0))
    toasts.FADE_IN_STEP = toasts.FADE_OUT_STEP = 1
    toast = toasts.show('Notice', ToastType.INFO, duration=10)
    deadline = time.monotonic() + 2
    while toast.winfo_exists() and time.monotonic() < deadline:
        tk_root.update()
        time.sleep(0.005)
    assert not toast.winfo_exists()
    assert not toasts.active_toasts
    assert not toasts._dismiss_callbacks

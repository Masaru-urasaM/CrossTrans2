"""Tests for Task 38 (Universal Quick Translate Pipeline — Phương án Toàn Diện A).

Covers all four universal layers without touching the real desktop, keyboard, or clipboard:
1. Menu-Mask Key (`VK = 0xE8`) injected on Alt/Win hotkeys to suppress Ribbon KeyTips / Menu Mode.
2. Delayed `show_loading()` (opened only after capture succeeds) and foreground restoration on retry.
3. Modifier-release wait and atomic `SendInput` Ctrl+C (never sending blind Escape).
4. `GetClipboardSequenceNumber()` polling + `win32clipboard.OpenClipboard()` retry + non-PII logs.
"""
import ctypes
import logging
import os
import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.app import TranslatorApp
from src.core import clipboard as clipboard_module
from src.core.clipboard import ClipboardManager
from src.core.hotkey import HotkeyManager
from src.core.selection_capture import (
    INPUT,
    KEYEVENTF_KEYUP,
    VK_C,
    VK_CONTROL,
    VK_LMENU,
    VK_LWIN,
    VK_MENU_MASK,
    capture_selected_text,
    inject_menu_mask_key,
    send_ctrl_combo,
    wait_for_modifiers_release,
)
from src.core.translation_task import TranslationCancelled, TranslationTasks


class _FakeUser32:
    """In-memory stand-in for user32: records SendInput events and controls key/window state."""

    def __init__(self):
        self.sent = []            # list of [(vk, flags), ...] per SendInput call
        self.down_vks = set()
        self.fg_hwnd = 1001
        self.pids = {1001: 99999, 2002: os.getpid()}
        self.valid_hwnds = {1001, 2002}
        self.visible_hwnds = {1001, 2002}
        self.seq = 10
        self.on_send = None
        self.set_fg_calls = []

    def SendInput(self, count, rows, size):
        assert size == ctypes.sizeof(INPUT)
        batch = [(rows[i].union.ki.wVk, rows[i].union.ki.dwFlags) for i in range(count)]
        self.sent.append(batch)
        if self.on_send:
            return self.on_send(batch)
        return count

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.down_vks else 0

    def GetForegroundWindow(self):
        return self.fg_hwnd

    def SetForegroundWindow(self, hwnd):
        self.set_fg_calls.append(hwnd)
        if hwnd in self.valid_hwnds:
            self.fg_hwnd = hwnd
            return 1
        return 0

    def IsWindow(self, hwnd):
        return 1 if hwnd in self.valid_hwnds else 0

    def IsWindowVisible(self, hwnd):
        return 1 if hwnd in self.visible_hwnds else 0

    def GetWindowThreadProcessId(self, hwnd, pid_ptr):
        pid_ptr._obj.value = self.pids.get(hwnd, 0)
        return 1

    def GetClipboardSequenceNumber(self):
        return self.seq

    def RegisterHotKey(self, *args):
        return 1

    def UnregisterHotKey(self, *args):
        return 1


class _FakeClock:
    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.now)


# --------------------------------------------------------------------------- #
# Step 1: Menu-Mask Key (VK = 0xE8)
# --------------------------------------------------------------------------- #
class TestMenuMaskKey:
    def test_input_struct_matches_platform_abi_size(self):
        expected = 40 if sys.maxsize > 2**32 else 28
        assert ctypes.sizeof(INPUT) == expected

    def test_inject_menu_mask_key_sends_down_and_up_of_0xe8(self):
        user32 = _FakeUser32()
        assert inject_menu_mask_key(user32=user32) is True
        assert user32.sent == [[(VK_MENU_MASK, 0), (VK_MENU_MASK, KEYEVENTF_KEYUP)]]

    def test_hotkey_with_alt_or_win_injects_menu_mask_key(self):
        done = threading.Event()
        cfg = SimpleNamespace(get_shortcuts_enabled=lambda: True)
        mgr = HotkeyManager(cfg, lambda _lang: done.set())
        mgr.user32 = _FakeUser32()
        mgr._on_hotkey('English', modifiers=HotkeyManager.MOD_WIN | HotkeyManager.MOD_ALT)
        assert done.wait(2)
        assert mgr.user32.sent == [[(VK_MENU_MASK, 0), (VK_MENU_MASK, KEYEVENTF_KEYUP)]]

    def test_hotkey_without_alt_or_win_does_not_inject_mask_key(self):
        done = threading.Event()
        cfg = SimpleNamespace(get_shortcuts_enabled=lambda: True)
        mgr = HotkeyManager(cfg, lambda _lang: done.set())
        mgr.user32 = _FakeUser32()
        mgr._on_hotkey('English', modifiers=HotkeyManager.MOD_CTRL | HotkeyManager.MOD_SHIFT)
        assert done.wait(2)
        assert mgr.user32.sent == []

    def test_suspended_hotkey_never_injects_mask_key(self):
        cfg = SimpleNamespace(get_shortcuts_enabled=lambda: True)
        mgr = HotkeyManager(cfg, lambda _lang: None)
        mgr.user32 = _FakeUser32()
        mgr.suspended = True
        mgr._on_hotkey('English', modifiers=HotkeyManager.MOD_WIN | HotkeyManager.MOD_ALT)
        assert mgr.user32.sent == []


# --------------------------------------------------------------------------- #
# Step 2: Delayed show_loading & Foreground Restoration on Retry
# --------------------------------------------------------------------------- #
class TestDelayedLoadingAndForegroundRestore:
    def _make_app(self, user32):
        app = TranslatorApp.__new__(TranslatorApp)
        app.running = True
        app._hotkey_events = queue.SimpleQueue()
        app._capture_loading_events = queue.SimpleQueue()
        app._source_hwnd = None
        app._last_external_hwnd = None
        app.audio_controller = None
        app.toast = MagicMock()
        app.config = SimpleNamespace(get_fix_grammar_hotkey_enabled=lambda: True)
        app.quick_translate_manager = MagicMock()
        app.quick_translate_manager.is_open = False
        app.translation_service = MagicMock()
        app.root = MagicMock()
        return app

    def test_show_loading_is_not_called_before_selection_capture_succeeds(self, monkeypatch):
        user32 = _FakeUser32()
        monkeypatch.setattr('ctypes.windll.user32', user32)
        app = self._make_app(user32)

        captured_callbacks = []

        def fake_start(operation, target_lang, on_result=None, owner=None):
            captured_callbacks.append(operation)

        app._start_translation_task = fake_start

        order = []

        def fake_do_translate_or_fix(language, on_captured=None):
            order.append('capture_started')
            app.quick_translate_manager.show_loading.assert_not_called()
            if on_captured:
                on_captured()
            order.append('capture_done')

        app.translation_service.do_translate_or_fix = fake_do_translate_or_fix

        app._on_hotkey_translate('Vietnamese')
        # Crucially: show_loading was NOT opened before capture ran!
        app.quick_translate_manager.show_loading.assert_not_called()
        assert app._source_hwnd == 1001
        assert app._last_external_hwnd == 1001

        # Now run worker operation -> triggers on_captured -> UI drains queue -> show_loading opens
        captured_callbacks[0]()
        assert order == ['capture_started', 'capture_done']
        app.quick_translate_manager.show_loading.assert_not_called()  # worker never calls Tk!
        app._drain_capture_loading_events()
        app.quick_translate_manager.show_loading.assert_called_once_with('Vietnamese')

    def test_retry_when_crosstrans_popup_took_focus_closes_popup_and_restores_external_hwnd(self, monkeypatch):
        user32 = _FakeUser32()
        monkeypatch.setattr('ctypes.windll.user32', user32)
        app = self._make_app(user32)
        app._start_translation_task = lambda *a, **k: None

        # Attempt 1: Excel (hwnd 1001) is foreground
        user32.fg_hwnd = 1001
        app._on_hotkey_translate('English')
        assert app._last_external_hwnd == 1001

        # Attempt 2: CrossTrans's own popup (hwnd 2002) is open and somehow holds foreground
        user32.fg_hwnd = 2002
        app.quick_translate_manager.is_open = True
        app._on_hotkey_translate('English')
        app.quick_translate_manager.close.assert_called_once()
        assert 1001 in user32.set_fg_calls
        assert app._source_hwnd == 1001
        assert user32.fg_hwnd == 1001

    def test_stale_loading_event_for_superseded_task_is_discarded(self):
        app = self._make_app(_FakeUser32())
        tasks = TranslationTasks()
        old = tasks.begin()
        tasks.begin()  # supersedes `old`
        app._capture_loading_events.put((old, 'English', None))
        app._drain_capture_loading_events()
        app.quick_translate_manager.show_loading.assert_not_called()


# --------------------------------------------------------------------------- #
# Step 3: Modifier-Release Wait & Atomic SendInput Ctrl+C (Never Blind Esc)
# --------------------------------------------------------------------------- #
class TestModifierReleaseAndSendInput:
    def test_waits_for_held_win_and_alt_to_release(self):
        user32 = _FakeUser32()
        user32.down_vks = {VK_LWIN, VK_LMENU}
        clock = _FakeClock(on_sleep=lambda now: user32.down_vks.clear() if now >= 0.045 else None)

        released, waited_ms = wait_for_modifiers_release(
            timeout=0.35, poll=0.015, user32=user32, check=None,
            monotonic=clock.monotonic, sleep=clock.sleep,
        )
        assert released is True
        assert 40 <= waited_ms <= 60

    def test_timeout_when_modifiers_stay_held_neutralizes_them_in_send_ctrl_combo_without_escape(self):
        user32 = _FakeUser32()
        user32.down_vks = {VK_LWIN, VK_LMENU}
        clock = _FakeClock()

        released, _ = wait_for_modifiers_release(
            timeout=0.06, poll=0.02, user32=user32, check=None,
            monotonic=clock.monotonic, sleep=clock.sleep,
        )
        assert released is False

        accepted = send_ctrl_combo(VK_C, user32=user32, release_held_modifiers=True)
        assert accepted == 6
        batch = user32.sent[0]
        # Releases held Win & Alt, then sends Ctrl+C cleanly; NEVER sends VK_ESCAPE (0x1B)
        assert (VK_LWIN, KEYEVENTF_KEYUP) in batch[:2]
        assert (VK_LMENU, KEYEVENTF_KEYUP) in batch[:2]
        assert batch[2:] == [
            (VK_CONTROL, 0),
            (VK_C, 0),
            (VK_C, KEYEVENTF_KEYUP),
            (VK_CONTROL, KEYEVENTF_KEYUP),
        ]
        assert all(vk != 0x1B for vk, _ in batch)

    def test_modifier_wait_aborts_immediately_on_task_cancel(self):
        user32 = _FakeUser32()
        user32.down_vks = {VK_LWIN}
        tasks = TranslationTasks()
        old = tasks.begin()
        tasks.begin()  # cancel old
        with pytest.raises(TranslationCancelled):
            wait_for_modifiers_release(timeout=0.35, user32=user32, check=old.check)

    def test_partial_send_input_explicitly_releases_ctrl(self):
        user32 = _FakeUser32()
        calls = iter([2, 2])  # first SendInput only accepted 2 of 4 events
        user32.on_send = lambda _batch: next(calls)
        send_ctrl_combo(VK_C, user32=user32, release_held_modifiers=False)
        assert len(user32.sent) == 2
        assert user32.sent[1] == [(VK_C, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)]


# --------------------------------------------------------------------------- #
# Step 4: Sequence Tracking, OpenClipboard Retry & Non-PII Logs
# --------------------------------------------------------------------------- #
class TestSequenceTrackingAndClipboardRetry:
    def test_capture_uses_sequence_number_without_emptying_clipboard_and_logs_no_pii(self, caplog):
        user32 = _FakeUser32()
        clock = _FakeClock()
        events = []

        class _FakeClipboard:
            @staticmethod
            def save_clipboard():
                return {13: 'PRIOR_USER_CLIPBOARD'}

            @staticmethod
            def restore_clipboard(saved):
                events.append(('restore', saved))

            @staticmethod
            def set_text(_text):
                raise AssertionError('EmptyClipboard / set_text must NOT be called before Ctrl+C')

            @staticmethod
            def get_sequence_number(user32=None):
                return user32.seq

            @staticmethod
            def get_text():
                return 'SUPER_SECRET_USER_SELECTION'

        def simulate_excel_copy(batch):
            user32.seq += 1
            return len(batch)

        user32.on_send = simulate_excel_copy

        with caplog.at_level(logging.INFO):
            text, copy_failed = capture_selected_text(
                clipboard=_FakeClipboard, user32=user32, check=None,
                monotonic=clock.monotonic, sleep=clock.sleep,
            )

        assert text == 'SUPER_SECRET_USER_SELECTION'
        assert copy_failed is False
        assert events == [('restore', {13: 'PRIOR_USER_CLIPBOARD'})]
        assert 'SUPER_SECRET_USER_SELECTION' not in caplog.text
        assert 'PRIOR_USER_CLIPBOARD' not in caplog.text
        assert any('selection' in rec.getMessage() for rec in caplog.records)

    def test_capture_fails_cleanly_when_sequence_never_changes_and_does_not_leak_old_clipboard(self):
        user32 = _FakeUser32()
        clock = _FakeClock()

        class _StaleClipboard:
            @staticmethod
            def save_clipboard():
                return {13: 'STALE_OLD_CLIPBOARD'}

            @staticmethod
            def restore_clipboard(_saved):
                pass

            @staticmethod
            def get_sequence_number(user32=None):
                return user32.seq  # never increments

            @staticmethod
            def get_text():
                return 'STALE_OLD_CLIPBOARD'

        text, copy_failed = capture_selected_text(
            clipboard=_StaleClipboard, user32=user32, check=None,
            attempts=3, base_wait=0.02, wait_step=0.01, poll_interval=0.01,
            monotonic=clock.monotonic, sleep=clock.sleep,
        )
        assert text is None
        assert copy_failed is False
        assert len(user32.sent) == 3

    def test_clipboard_get_text_retries_transient_open_clipboard_locks(self, monkeypatch):
        attempts = {'open': 0, 'closed': 0}

        class _BusyWin32Clipboard:
            def OpenClipboard(self):
                attempts['open'] += 1
                if attempts['open'] < 4:
                    raise OSError('Access is denied (locked by Excel)')

            def IsClipboardFormatAvailable(self, fmt):
                return True

            def GetClipboardData(self, fmt):
                return 'Excel Cell Text\x00trailing'

            def CloseClipboard(self):
                attempts['closed'] += 1

        monkeypatch.setattr(clipboard_module, 'HAS_WIN32', True)
        monkeypatch.setattr(clipboard_module, 'win32clipboard', _BusyWin32Clipboard(), raising=False)
        monkeypatch.setattr(clipboard_module, 'win32con', SimpleNamespace(CF_UNICODETEXT=13), raising=False)
        monkeypatch.setattr(clipboard_module.time, 'sleep', lambda _d: None)

        assert ClipboardManager.get_text(retries=10, delay=0.001) == 'Excel Cell Text'
        assert attempts['open'] == 4
        assert attempts['closed'] == 1

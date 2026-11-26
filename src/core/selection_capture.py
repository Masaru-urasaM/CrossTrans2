"""Universal Windows selection capture and modifier-safe keyboard input (Task 38).

Solves three Windows-wide failure modes without per-application hacks:
1. Menu-Mask Key (`VK = 0xE8` via SendInput) on Alt/Win hotkeys prevents Office Ribbon
   KeyTips, Win32 Menu Bars, and Start Menu focus theft across Excel and all apps.
2. Waiting briefly for physical modifier release (`GetAsyncKeyState`) and sending an
   atomic `SendInput` Ctrl+C sequence prevents `Ctrl+Alt+Win+C` corruption without ever
   sending a blind `Esc` (which would cancel Excel cell editing or close dialogs).
3. Tracking `GetClipboardSequenceNumber()` instead of wiping the clipboard with
   `EmptyClipboard()` avoids breaking Excel's OLE clipboard/copy state, while
   retrying `OpenClipboard()` handles transient locks from Excel and Clipboard History.
"""
import ctypes
from ctypes import wintypes
import logging
import os
import time
from typing import Callable, Optional, Sequence, Tuple

from src.core.clipboard import ClipboardManager
from src.core.translation_task import TranslationStopped, check_current
from src.utils.log_events import log_event

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

# Unassigned virtual key code used by Windows automation tools (AutoHotkey / PowerToys)
# to suppress Alt menu activation and Win Start Menu popups after RegisterHotKey fires.
VK_MENU_MASK = 0xE8

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_C = 0x43
VK_V = 0x56
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

ALL_MODIFIER_VKS = (
    VK_LWIN, VK_RWIN,
    VK_MENU, VK_LMENU, VK_RMENU,
    VK_SHIFT, VK_LSHIFT, VK_RSHIFT,
    VK_CONTROL, VK_LCONTROL, VK_RCONTROL,
)

# Modifiers that must not stay logically down while injecting Ctrl+C / Ctrl+V.
NON_CTRL_MODIFIER_VKS = (
    VK_LWIN, VK_RWIN,
    VK_LMENU, VK_RMENU,
    VK_LSHIFT, VK_RSHIFT,
)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ('ki', _KEYBDINPUT),
        ('mi', _MOUSEINPUT),
        ('hi', _HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ('type', wintypes.DWORD),
        ('union', _INPUT_UNION),
    ]


def _default_user32():
    return ctypes.windll.user32


def send_key_events(events: Sequence[Tuple[int, int]], user32=None) -> int:
    """Send a batch of `(vk, flags)` keyboard events atomically via `user32.SendInput`.

    Always routes through `ctypes.windll.user32` unless a fake is injected so
    `tests/quiet_session.py` continues to guard live keyboard injection.
    """
    if not events:
        return 0
    api = user32 if user32 is not None else _default_user32()
    array_type = INPUT * len(events)
    rows = array_type()
    for row, (vk, flags) in zip(rows, events):
        row.type = INPUT_KEYBOARD
        row.union.ki.wVk = int(vk)
        row.union.ki.wScan = 0
        row.union.ki.dwFlags = int(flags)
        row.union.ki.time = 0
        row.union.ki.dwExtraInfo = 0
    return int(api.SendInput(len(events), rows, ctypes.sizeof(INPUT)))


def inject_menu_mask_key(user32=None) -> bool:
    """Tap the unassigned VK 0xE8 key so releasing Alt/Win never activates Ribbon/Menu Mode."""
    try:
        accepted = send_key_events(((VK_MENU_MASK, 0), (VK_MENU_MASK, KEYEVENTF_KEYUP)), user32=user32)
        return accepted == 2
    except Exception as exc:
        logging.debug("[Hotkeys] Menu mask key injection skipped: %s", exc)
        return False


def is_key_down(vk: int, user32=None) -> bool:
    api = user32 if user32 is not None else _default_user32()
    return bool(api.GetAsyncKeyState(int(vk)) & 0x8000)


def wait_for_modifiers_release(
    timeout: float = 0.35,
    poll: float = 0.015,
    user32=None,
    check: Optional[Callable[[], None]] = check_current,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[bool, int]:
    """Wait briefly until the user releases held hotkey modifiers (Win/Alt/Shift/Ctrl)."""
    api = user32 if user32 is not None else _default_user32()
    started = monotonic()
    deadline = started + max(0.0, timeout)
    while True:
        if check is not None:
            check()
        if not any(api.GetAsyncKeyState(vk) & 0x8000 for vk in ALL_MODIFIER_VKS):
            return True, round((monotonic() - started) * 1000)
        if monotonic() >= deadline:
            return False, round((monotonic() - started) * 1000)
        sleep(poll)


def send_ctrl_combo(vk: int, user32=None, release_held_modifiers: bool = True) -> int:
    """Send an atomic Ctrl+<vk> keystroke via SendInput, neutralizing held Alt/Win/Shift."""
    api = user32 if user32 is not None else _default_user32()
    events = []
    if release_held_modifiers:
        for mod_vk in NON_CTRL_MODIFIER_VKS:
            try:
                if api.GetAsyncKeyState(mod_vk) & 0x8000:
                    events.append((mod_vk, KEYEVENTF_KEYUP))
            except Exception:
                break
    events.extend([
        (VK_CONTROL, 0),
        (int(vk), 0),
        (int(vk), KEYEVENTF_KEYUP),
        (VK_CONTROL, KEYEVENTF_KEYUP),
    ])
    accepted = send_key_events(events, user32=api)
    if 0 < accepted < len(events):
        # Never leave Ctrl logically stuck down after a partial SendInput delivery.
        send_key_events(((int(vk), KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)), user32=api)
    return accepted


def window_pid(hwnd: int, user32=None) -> int:
    if not hwnd:
        return 0
    api = user32 if user32 is not None else _default_user32()
    pid = wintypes.DWORD(0)
    api.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


def is_own_window(hwnd: int, own_pid: Optional[int] = None, user32=None) -> bool:
    if not hwnd:
        return False
    target_pid = os.getpid() if own_pid is None else own_pid
    try:
        return window_pid(hwnd, user32=user32) == target_pid
    except Exception:
        return False


def is_valid_external_window(hwnd: int, own_pid: Optional[int] = None, user32=None) -> bool:
    if not hwnd:
        return False
    api = user32 if user32 is not None else _default_user32()
    try:
        if not api.IsWindow(int(hwnd)) or not api.IsWindowVisible(int(hwnd)):
            return False
        return not is_own_window(hwnd, own_pid=own_pid, user32=api)
    except Exception:
        return False


def restore_foreground(
    hwnd: int,
    user32=None,
    timeout: float = 0.15,
    poll: float = 0.015,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Return foreground focus to an external window before capturing or pasting."""
    if not hwnd:
        return False
    api = user32 if user32 is not None else _default_user32()
    try:
        if int(api.GetForegroundWindow() or 0) == int(hwnd):
            return True
        api.SetForegroundWindow(int(hwnd))
        deadline = monotonic() + max(0.0, timeout)
        while int(api.GetForegroundWindow() or 0) != int(hwnd) and monotonic() < deadline:
            sleep(poll)
        return int(api.GetForegroundWindow() or 0) == int(hwnd)
    except Exception as exc:
        logging.debug("[Capture] Could not restore foreground window: %s", exc)
        return False


def capture_selected_text(
    *,
    clipboard=ClipboardManager,
    user32=None,
    attempts: int = 3,
    modifier_timeout: float = 0.35,
    poll_interval: float = 0.015,
    base_wait: float = 0.22,
    wait_step: float = 0.10,
    check: Optional[Callable[[], None]] = check_current,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[Optional[str], bool]:
    """Capture the active window's selection without wiping the clipboard first.

    Returns:
        `(text_or_none, copy_failed)` where `copy_failed` is True if Win32 input/clipboard
        calls raised errors during capture.
    """
    started = monotonic()
    copy_failed = False
    any_seq_changed = False
    wait_mod_ms = 0

    try:
        released, wait_mod_ms = wait_for_modifiers_release(
            timeout=modifier_timeout, poll=poll_interval, user32=user32,
            check=check, monotonic=monotonic, sleep=sleep,
        )
    except TranslationStopped:
        raise
    except Exception as exc:
        released = True
        logging.debug("[Capture] Modifier release check skipped: %s", exc)

    original_clipboard = clipboard.save_clipboard()

    try:
        for attempt in range(attempts):
            try:
                if check is not None:
                    check()
                seq_before = clipboard.get_sequence_number(user32=user32)
                accepted = send_ctrl_combo(VK_C, user32=user32, release_held_modifiers=True)
                if accepted == 0:
                    copy_failed = True

                attempt_deadline = monotonic() + base_wait + (attempt * wait_step)
                seq_changed = False
                while True:
                    if check is not None:
                        check()
                    seq_now = clipboard.get_sequence_number(user32=user32)
                    if seq_before and seq_now and seq_now != seq_before:
                        seq_changed = True
                        any_seq_changed = True
                        break
                    if monotonic() >= attempt_deadline:
                        # Fallback when GetClipboardSequenceNumber is unavailable (returns 0)
                        if seq_before == 0 and seq_now == 0:
                            seq_changed = True
                        break
                    sleep(poll_interval)

                if seq_changed:
                    if check is not None:
                        check()
                    new_text = clipboard.get_text()
                    if new_text and new_text.strip():
                        total_ms = round((monotonic() - started) * 1000)
                        log_event(
                            'Capture', 'selection',
                            ok=True, attempts=attempt + 1, wait_mod_ms=wait_mod_ms,
                            modifiers_released=released, seq_changed=True,
                            ms=total_ms, chars=len(new_text),
                        )
                        return new_text, False

            except TranslationStopped:
                raise
            except Exception as exc:
                copy_failed = True
                logging.warning("Selection capture attempt %s failed: %s", attempt + 1, exc)

        total_ms = round((monotonic() - started) * 1000)
        log_event(
            'Capture', 'selection', level=logging.WARNING,
            ok=False, attempts=attempts, wait_mod_ms=wait_mod_ms,
            modifiers_released=released, seq_changed=any_seq_changed,
            copy_failed=copy_failed, ms=total_ms,
        )
        return None, copy_failed
    finally:
        if any_seq_changed and original_clipboard is not None:
            clipboard.restore_clipboard(original_clipboard)

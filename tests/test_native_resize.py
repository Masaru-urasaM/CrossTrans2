"""Own-window native messages on the private desktop, with no injected input."""
import ctypes
from ctypes import wintypes
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ui import native_resize as N
from src.ui.popup_layout import WorkArea


@pytest.fixture
def bridge():
    item = N.WindowsSizing(None, rect=(100, 100, 400, 300), minimum=(250, 120),
        bounds=lambda _x, _y: WorkArea(-1920, -200, 1920, 1040), on_rect=Mock())
    item.hwnd = 10
    item.common = SimpleNamespace(DefSubclassProc=Mock(return_value=71),
                                 RemoveWindowSubclass=Mock(return_value=True))
    item.user = SimpleNamespace(GetCursorPos=Mock(return_value=True),
        GetWindowRect=Mock(return_value=True), InvalidateRect=Mock(return_value=True))
    return item


@pytest.mark.parametrize('edge,rectangle,expected', [
    ('w', (-3000, 100, 500, 400), (-1920, 100, 500, 400)),
    ('e', (100, 100, 4000, 400), (100, 100, 1920, 400)),
    ('n', (100, -1000, 500, 400), (100, -200, 500, 400)),
    ('s', (100, 100, 500, 2000), (100, 100, 500, 1040)),
    ('nw', (490, 390, 500, 400), (250, 280, 500, 400)),
    ('ne', (100, 390, 110, 400), (100, 280, 350, 400)),
    ('sw', (490, 100, 500, 110), (250, 100, 500, 220)),
    ('se', (100, 100, 110, 110), (100, 100, 350, 220)),
])
def test_native_sizing_preserves_opposite_edge_and_limits(bridge, edge, rectangle, expected):
    rect = wintypes.RECT(*rectangle)
    assert bridge._message(10, N.WM_SIZING, N.SIZING_EDGES[edge],
                           ctypes.addressof(rect), 1, 0) == 1
    assert (rect.left, rect.top, rect.right, rect.bottom) == expected
    bridge.common.DefSubclassProc.assert_not_called()


def test_minimums_are_applied_after_forwarding_tk_limits(bridge):
    limits = N.MinMaxInfo()
    assert bridge._message(10, N.WM_GETMINMAXINFO, 0, ctypes.addressof(limits), 1, 0) == 71
    assert (limits.min_track.x, limits.min_track.y) == (250, 120)
    assert bridge.common.DefSubclassProc.call_count == 1


@pytest.mark.parametrize('message', [0x0231, 0x0232, 0x0084, 0x0201, 0x0100])
def test_tk_event_servicing_and_input_messages_remain_in_chain(bridge, message):
    assert bridge._message(10, message, 0, 0, 1, 0) == 71
    bridge.common.DefSubclassProc.assert_called_once_with(10, message, 0, 0)


def test_size_callback_failure_does_not_forward_native_message_twice(bridge, caplog):
    bridge.user.GetWindowRect = Mock(side_effect=RuntimeError('fixture native failure'))
    assert bridge._message(10, N.WM_SIZE, 0, 0, 1, 0) == 71
    assert bridge.common.DefSubclassProc.call_count == 1
    assert 'native sizing callback failed' in caplog.text


def test_destroy_releases_wrapper_and_callable_owner(bridge):
    bridge.callback = Mock()
    N._INSTALLED[id(bridge)] = bridge
    bridge._message(10, N.WM_NCDESTROY, 0, 0, id(bridge), 0)
    assert bridge.hwnd is None and bridge.on_rect is None
    assert id(bridge) not in N._INSTALLED


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows sizing API')
def test_hidden_desktop_cannot_enter_native_input_loop(monkeypatch):
    item = N.WindowsSizing(None, rect=(100, 100, 400, 300), minimum=(250, 120),
        bounds=lambda _x, _y: WorkArea(0, 0, 1920, 1040), on_rect=Mock())
    item.user = SimpleNamespace(GetAsyncKeyState=Mock(side_effect=AssertionError('Read input on hidden desktop')))
    monkeypatch.setattr(item, '_api', lambda: None)
    monkeypatch.setattr(item, '_is_input_desktop', lambda: False)
    assert not item.begin('se', 500, 400)
    item.user.GetAsyncKeyState.assert_not_called()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows wrapper API')
def test_actual_private_wrapper_keeps_client_geometry_and_restores_style(tk_root):
    import tkinter as tk
    from src.ui.theme.compat import NO_AUTOSTYLE
    window = tk.Toplevel(tk_root, **NO_AUTOSTYLE)
    window.overrideredirect(True)
    window.geometry('420x300+100+100')
    tk_root.update()
    item = N.WindowsSizing(window, rect=(100, 100, 420, 300), minimum=(250, 120),
        bounds=lambda _x, _y: WorkArea(0, 0, 1920, 1040), on_rect=Mock())
    item._api()
    hwnd = int(window.wm_frame(), 16)
    style = item.user.GetWindowLongW(hwnd, N.GWL_STYLE)
    try:
        assert item.attach()
        tk_root.update()
        rect = wintypes.RECT()
        assert item.user.GetWindowRect(hwnd, ctypes.byref(rect))
        assert (rect.right - rect.left, rect.bottom - rect.top) == (420, 300)
        assert (window.winfo_width(), window.winfo_height()) == (420, 300)
        limits = N.MinMaxInfo()
        item.user.SendMessageW(hwnd, N.WM_GETMINMAXINFO, 0, ctypes.addressof(limits))
        assert (limits.min_track.x, limits.min_track.y) == (250, 120)
        item.running = True
        window.geometry('460x320+100+100')
        tk_root.update()
        item.on_rect.assert_any_call((100, 100, 460, 320))
        window.geometry('420x300+100+100')
        tk_root.update()
        item.running = False
        item.close()
        tk_root.update()
        assert item.user.GetWindowLongW(hwnd, N.GWL_STYLE) == style
        assert id(item) not in N._INSTALLED
        assert (window.winfo_width(), window.winfo_height()) == (420, 300)
    finally:
        item.close()
        window.destroy()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows wrapper API')
def test_actual_wrapper_destroy_drops_subclass_without_pumping_input(tk_root):
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    window.overrideredirect(True)
    window.geometry('420x300+100+100')
    tk_root.update()
    item = N.WindowsSizing(window, rect=(100, 100, 420, 300), minimum=(250, 120),
        bounds=lambda _x, _y: WorkArea(0, 0, 1920, 1040), on_rect=Mock())
    assert item.attach()
    window.destroy()
    assert item.hwnd is None
    assert id(item) not in N._INSTALLED


def test_duplicate_native_size_messages_do_not_repeat_body_layout(bridge):
    def rectangle(_hwnd, output):
        rect = ctypes.cast(output, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 100, 100, 550, 450
        return True
    bridge.user.GetWindowRect = rectangle
    for message in (N.WM_WINDOWPOSCHANGED, N.WM_SIZE, N.WM_WINDOWPOSCHANGED):
        bridge._message(10, message, 0, 0, id(bridge), 0)
    bridge.on_rect.assert_not_called()
    bridge.running = True
    bridge._tk_event(SimpleNamespace(widget=None))
    bridge.on_rect.assert_called_once_with((100, 100, 450, 350))


@pytest.mark.parametrize('point,edge', [((-199, -99), 'nw'), ((399, -99), 'ne'),
    ((-199, 299), 'sw'), ((399, 299), 'se'), ((50, -99), 'n'),
    ((50, 299), 's'), ((-199, 50), 'w'), ((399, 50), 'e')])
def test_native_hit_testing_reaches_wrapper_through_children_without_tk_calls(bridge, point, edge):
    def rectangle(_hwnd, output):
        rect = ctypes.cast(output, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = -200, -100, 400, 300
        return True
    bridge.user.GetWindowRect = rectangle
    bridge.window = Mock()
    bridge.enabled = True
    packed = (point[0] & 0xffff) | ((point[1] & 0xffff) << 16)
    assert bridge._message(10, N.WM_NCHITTEST, 0, packed, id(bridge), 0) == N.HIT_EDGES[edge]
    assert bridge._message(20, N.WM_NCHITTEST, 0, packed, id(bridge), 0) == -1
    assert not bridge.window.method_calls
    bridge.on_rect.assert_not_called()


def test_client_edge_mode_does_not_redirect_quick_children_to_native_sizing(bridge):
    bridge.client_edges=True
    bridge.enabled=True
    bridge._hit_edge=Mock(side_effect=AssertionError('Client targets own hit testing'))
    bridge.common.DefSubclassProc.return_value=1
    assert bridge._message(10,N.WM_NCHITTEST,0,0,id(bridge),0)==1
    assert bridge._message(20,N.WM_NCHITTEST,0,0,id(bridge),0)==1
    bridge._hit_edge.assert_not_called()
    bridge.common.DefSubclassProc.assert_called_once_with(20,N.WM_NCHITTEST,0,0)


@pytest.mark.parametrize('desired', [False, True])
def test_native_style_policy_needs_no_reentrant_win32_write(bridge, desired):
    bridge.noactivate = desired
    style = N.StyleInfo(0, 0x0200 | N.WS_EX_NOACTIVATE)
    assert bridge._message(10, N.WM_STYLECHANGING, ctypes.c_size_t(N.GWL_EXSTYLE).value,
        ctypes.addressof(style), id(bridge), 0) == 71
    assert bool(style.new & N.WS_EX_NOACTIVATE) is desired
    assert style.new & 0x0200


def test_fast_native_release_is_delivered_at_the_next_safe_tk_binding(bridge):
    bridge.on_start, bridge.on_end = Mock(), Mock()
    bridge.start_rect = bridge.rect
    bridge.pointer = (500, 400)
    bridge._pending_end = True
    bridge.resizing = True
    bridge._tk_event(SimpleNamespace(widget=None))
    bridge.on_start.assert_called_once_with('se', bridge.rect, (500, 400))
    bridge.on_end.assert_called_once_with()
    assert not bridge._pending_end and not bridge._started


@pytest.mark.parametrize('custom_frame', [False, True])
@pytest.mark.parametrize('ending', [N.WM_EXITSIZEMOVE, N.WM_CANCELMODE, N.WM_CAPTURECHANGED])
def test_native_end_and_capture_loss_deliver_one_safe_final_callback(bridge, custom_frame, ending):
    bridge.custom_frame = custom_frame
    bridge.on_start, bridge.on_end = Mock(), Mock()
    bridge._message(10, N.WM_SYSCOMMAND, N.SC_SIZE, 0, 1, 0)
    bridge._message(10, N.WM_ENTERSIZEMOVE, 0, 0, 1, 0)
    assert bridge.running and bridge.resizing
    bridge._message(10, ending, 0, 0, 1, 0)
    assert not bridge.running and bridge._pending_end
    bridge.on_start.assert_not_called()
    bridge.on_end.assert_not_called()
    bridge._tk_event(SimpleNamespace(widget=None))
    bridge._message(10, N.WM_EXITSIZEMOVE, 0, 0, 1, 0)
    bridge._tk_event(SimpleNamespace(widget=None))
    bridge.on_start.assert_called_once()
    bridge.on_end.assert_called_once_with()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows own-window lifecycle regression')
def test_posted_native_lifecycle_updates_ui_only_through_tk_commands(tmp_path):
    from tools.tk_resize.process import run_owned
    import os
    pids = []
    with (tmp_path / 'native-lifecycle.log').open('w', encoding='utf-8') as stream:
        result = run_owned([sys.executable, '-m', 'tools.tk_resize.native_lifecycle_probe'],
            cwd=__import__('pathlib').Path(__file__).resolve().parents[1], env=os.environ.copy(),
            stream=stream, timeout=15, started=pids.append)
    output = (tmp_path / 'native-lifecycle.log').read_text(encoding='utf-8')
    assert result['exit_code'] == 0, output
    assert '"status": "pass"' in output and '"no_input_loop": true' in output


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows wrapper API')
def test_fifty_scoped_bridges_leave_no_registered_callback_owner(tk_root):
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    window.overrideredirect(True)
    window.geometry('420x300+100+100')
    tk_root.update()
    try:
        assert not N._INSTALLED
        for index in range(50):
            item = N.WindowsSizing(window,
                rect=(window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height()),
                minimum=(250, 120), bounds=lambda _x, _y: WorkArea(0, 0, 1920, 1040), on_rect=Mock())
            assert item.attach()
            window.geometry(f'{420 + index % 4 * 10}x{300 + index % 3 * 10}+100+100')
            tk_root.update()
            item.close()
            assert not N._INSTALLED
    finally:
        window.destroy()

"""Own-window native outline sizing without OS input injection or global settings."""
import ctypes
from ctypes import wintypes
import tkinter as tk
from types import SimpleNamespace

import pytest

from src.ui import native_resize as N
from src.ui.deferred_resize import DeferredContentResize
from src.ui.resize_outline import ResizeOutline
from tests.final_ui_helpers import design
from tests.test_deferred_resize import pump, wait_for, quick_ready

pytestmark = pytest.mark.usefixtures('design')


@pytest.fixture
def geometry_probe(tk_root):
    from tools.tk_resize import native_trace
    from tools.tk_resize.native_geometry import NativeGeometryProbe
    if not (native_trace.RUNTIME / 'build.json').exists():
        pytest.skip('needs the locally built native helper (python -m tools.tk_resize.build_native_trace --compiler <gcc>)')
    with NativeGeometryProbe(tk_root) as probe:
        yield probe


def outer(bridge):
    rect = wintypes.RECT()
    assert bridge.user.GetWindowRect(bridge.hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top


@pytest.mark.parametrize('edge', ['se','nw'])
@pytest.mark.parametrize('cancel', [False,True])
def test_native_proposals_keep_actual_window_fixed_until_release(tk_root, geometry_probe, edge, cancel):
    window = tk.Toplevel(tk_root)
    window.geometry('640x420+120+120')
    window.minsize(250,120)
    pump(tk_root)
    owner = DeferredContentResize(window,background='#202124',size=(640,420))
    editor = tk.Text(owner.host)
    editor.pack(fill='both',expand=True)
    editor.insert('1.0','日本語 / Original text')
    owner.attach_observer()
    bridge = owner.native
    assert bridge is not None and bridge.outline_mode and not bridge.custom_frame
    try:
        pump(tk_root)
        initial = outer(bridge)
        active = ctypes.windll.user32.GetActiveWindow()
        count = owner.commit_count
        bridge._size_command = True
        assert bridge.user.PostMessageW(bridge.hwnd,N.WM_ENTERSIZEMOVE,0,0)
        wait_for(tk_root,lambda:bridge.running)
        x,y,width,height = initial
        for delta in [30,80,50]:
            target = ((x-delta,y-delta,width+delta,height+delta) if edge=='nw'
                      else (x,y,width+delta,height+delta))
            tx,ty,tw,th = target
            proposed = wintypes.RECT(tx,ty,tx+tw,ty+th)
            bridge.user.SendMessageW(bridge.hwnd,N.WM_SIZING,N.SIZING_EDGES[edge],
                                     ctypes.addressof(proposed))
            geometry_probe.position(bridge.hwnd,target)
            pump(tk_root,.07)
            assert outer(bridge)==initial
            assert owner.host.winfo_width()==640
            assert bridge.outline.visible and bridge.outline.rect==target
            assert len(bridge.outline.handles)==4
        pump(tk_root,.25)
        assert outer(bridge)==initial and owner.holding
        assert owner.state=='RESIZING'
        assert ctypes.windll.user32.GetActiveWindow()==active
        ending = N.WM_CANCELMODE if cancel else N.WM_EXITSIZEMOVE
        assert bridge.user.PostMessageW(bridge.hwnd,ending,0,0)
        wait_for(tk_root,lambda:owner.state=='IDLE')
        assert outer(bridge)==(initial if cancel else target)
        assert not bridge.outline.visible
        assert editor.get('1.0','end-1c')=='日本語 / Original text'
        assert owner.commit_count==count+1
        assert bridge.user.PostMessageW(bridge.hwnd,N.WM_EXITSIZEMOVE,0,0)
        pump(tk_root)
        assert owner.commit_count==count+1
    finally:
        owner.close()
        window.destroy()


def test_outline_native_windows_and_class_brush_are_released(tk_root):
    window = tk.Toplevel(tk_root)
    pump(tk_root)
    for _ in range(30):
        marker = ResizeOutline(int(window.wm_frame(),16))
        marker.show((40,40,400,300))
        handles = list(marker.handles)
        assert marker.visible
        marker.hide()
        marker.close()
        assert not marker.handles and marker.atom is None and marker.brush is None
        assert not any(marker.user.IsWindow(handle) for handle in handles)
    window.destroy()


@pytest.fixture
def quick(tk_root,tmp_path):
    from src.ui.quick_translate import QuickTranslateManager
    from tools.tk_resize.profile import TrialConfig
    manager=QuickTranslateManager(tk_root,TrialConfig(tmp_path/'profile'))
    manager.show('Translated text.','English',original='Original text.')
    wait_for(tk_root,lambda:quick_ready(manager))
    yield manager
    manager.close()


def test_fallback_escape_discards_preview_and_keeps_quick_open(quick,tk_root):
    window=quick.popup
    before=(window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())
    previous=quick._user_rect,quick._user_size
    quick._grip_press(SimpleNamespace(x_root=100,y_root=100),'nw')
    quick._grip_drag(SimpleNamespace(x_root=60,y_root=70))
    wait_for(tk_root,lambda:quick._grip_outline is not None)
    assert quick._grip_outline.visible
    assert quick._escape()=='break'
    pump(tk_root,.2)
    assert quick.popup is window and quick._grip_outline is None
    assert (window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())==before
    assert (quick._user_rect,quick._user_size)==previous
    assert quick.popup_text.get_plain()=='Translated text.'


def test_close_button_event_finishes_before_deleted_toplevel_bindings(quick,tk_root):
    from tools.ui_gallery import capture_tk_callback_errors
    errors=[]
    previous=capture_tk_callback_errors(tk_root,errors)
    try:
        button=quick.popup_close_btn
        button.event_generate('<ButtonPress-1>',x=5,y=5)
        button.event_generate('<ButtonRelease-1>',x=5,y=5)
        pump(tk_root,.2)
        assert quick.popup is None
        assert not errors,errors
    finally:
        tk_root.report_callback_exception=previous


@pytest.mark.parametrize('edge',['se','nw'])
@pytest.mark.parametrize('cancel',[False,True])
def test_quick_native_outline_releases_or_cancels_without_activation(quick,tk_root,geometry_probe,edge,cancel):
    from src.ui.popup_layout import WorkArea
    bridge=quick._native_sizing
    owner=quick._content_resize
    assert bridge is not None and bridge.custom_frame and bridge.outline_mode
    bridge.bounds=lambda _x,_y:WorkArea(0,0,4096,2160)
    # Keep the opposite-edge check away from monitor clamping, which has its
    # own coverage. Submit native positions, not Tk's separate move bookkeeping.
    quick.popup.geometry('+200+150')
    pump(tk_root,.15)
    initial=outer(bridge)
    saved=quick._user_rect,quick._user_size
    active=ctypes.windll.user32.GetActiveWindow()
    bridge.edge,bridge.pointer,bridge._size_command=edge,(100,100),True
    assert bridge.user.PostMessageW(bridge.hwnd,N.WM_ENTERSIZEMOVE,0,0)
    wait_for(tk_root,lambda:owner.state=='RESIZING')
    x,y,width,height=initial
    for delta in (30,60):
        target=((x-delta,y-delta,width+delta,height+delta) if edge=='nw'
                else (x,y,width+delta,height+delta))
        tx,ty,tw,th=target
        proposed=wintypes.RECT(tx,ty,tx+tw,ty+th)
        bridge.user.SendMessageW(bridge.hwnd,N.WM_SIZING,N.SIZING_EDGES[edge],
                                 ctypes.addressof(proposed))
        geometry_probe.position(bridge.hwnd,target)
        pump(tk_root,.1)
        assert outer(bridge)==initial
        assert bridge.outline.visible and bridge.outline.rect==target
        assert quick.popup_text.get_plain()=='Translated text.'
    ending=N.WM_CANCELMODE if cancel else N.WM_EXITSIZEMOVE
    assert bridge.user.PostMessageW(bridge.hwnd,ending,0,0)
    wait_for(tk_root,lambda:owner.state=='IDLE')
    assert outer(bridge)==(initial if cancel else target)
    assert not bridge.outline.visible
    assert ctypes.windll.user32.GetActiveWindow()==active
    if cancel:
        assert (quick._user_rect,quick._user_size)==saved
    else:
        assert quick._user_size==target[2:]


@pytest.mark.parametrize('external_destroy',[False,True])
def test_closing_quick_with_a_visible_marker_removes_native_windows(quick,tk_root,external_destroy):
    errors=[]
    from tools.ui_gallery import capture_tk_callback_errors
    previous=capture_tk_callback_errors(tk_root,errors)
    try:
        quick._grip_press(SimpleNamespace(x_root=100,y_root=100))
        quick._grip_drag(SimpleNamespace(x_root=140,y_root=130))
        wait_for(tk_root,lambda:quick._grip_outline is not None)
        marker=quick._grip_outline
        handles=list(marker.handles)
        assert marker.visible
        if external_destroy:
            quick.popup.destroy()
        else:
            quick.close()
        pump(tk_root,.2)
        assert quick._grip_outline is None and quick._grip_preview_after is None
        assert not any(marker.user.IsWindow(handle) for handle in handles)
        assert marker.atom is None and marker.brush is None
        assert not errors,errors
    finally:
        tk_root.report_callback_exception=previous

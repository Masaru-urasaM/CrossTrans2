"""Real Tk geometry and lifecycle contracts for a live shell with held content."""
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ui.deferred_resize import DeferredContentResize, defer_update, defer_global_update
from src.ui import native_resize as N
from tests.test_source_choice import main_window
from tests.final_ui_helpers import design

pytestmark = pytest.mark.usefixtures('design')


def pump(root, seconds=.12):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        root.update()
        time.sleep(.003)


@pytest.fixture
def held_window(tk_root):
    window=tk.Toplevel(tk_root)
    window.geometry('600x400+40+40')
    committed=[]
    owner=DeferredContentResize(window,background='#123456',size=(600,400),
                                on_commit=lambda *size:committed.append(size))
    editor=tk.Text(owner.host,wrap='word',undo=True)
    editor.pack(fill='both',expand=True)
    editor.insert('1.0','A😀日本語 e\u0301\n'*100)
    owner.editors=lambda:(editor,)
    pump(tk_root)
    committed.clear()
    try:
        yield window,owner,editor,committed
    finally:
        if window.winfo_exists():
            window.destroy()
        pump(tk_root,.02)


def shape(widget):
    return widget.winfo_x(),widget.winfo_y(),widget.winfo_width(),widget.winfo_height()


def test_shell_changes_and_pause_while_held_never_commits(held_window,tk_root):
    window,owner,editor,committed=held_window
    original=shape(owner.host),shape(editor)
    owner.begin()
    for width,height in ((720,480),(450,320),(840,520)):
        window.geometry(f'{width}x{height}')
        pump(tk_root,.12)
        assert (window.winfo_width(),window.winfo_height())==(width,height)
        assert (shape(owner.host),shape(editor))==original
        assert not committed
    pump(tk_root,.25)
    assert owner.state=='RESIZING' and not committed
    owner.end()
    owner.end()
    pump(tk_root)
    assert committed==[(840,520)]
    assert (owner.host.winfo_width(),owner.host.winfo_height())==(840,520)
    assert editor.winfo_width()==840 and owner.state=='IDLE'


def test_held_native_text_keeps_canonical_selection_caret_and_undo(held_window,tk_root):
    window,owner,editor,_=held_window
    editor.edit_reset()
    editor.mark_set('insert','2.2')
    editor.insert('insert','X')
    editor.edit_separator()
    editor.tag_add('sel','2.0','3.1')
    editor.yview('20.0')
    pump(tk_root)
    before=editor.get('1.0','end-1c'),editor.index('insert'),tuple(map(str,editor.tag_ranges('sel')))
    top=editor.index('@0,0')
    owner.begin()
    window.geometry('460x330')
    pump(tk_root)
    owner.end()
    pump(tk_root)
    assert before==(editor.get('1.0','end-1c'),editor.index('insert'),tuple(map(str,editor.tag_ranges('sel'))))
    assert editor.index('@0,0')==top
    editor.edit_undo()
    assert 'X' not in editor.get('1.0','end-1c')


def test_latest_view_update_only_and_close_discards_window_work(held_window,tk_root):
    window,owner,editor,_=held_window
    calls=[]
    before=editor.get('1.0','end-1c')
    owner.begin()
    for value in range(100):
        assert defer_update(window,'result',lambda current=value:calls.append(current))
    assert len(owner.pending)==1 and not calls
    assert editor.get('1.0','end-1c')==before
    owner.end()
    pump(tk_root)
    assert calls==[99]
    owner.begin()
    owner.defer('late',lambda:calls.append('invalid'))
    window.destroy()
    pump(tk_root)
    assert calls==[99] and owner.state=='CLOSED' and not owner.pending


def test_global_update_survives_closing_the_dragged_window(held_window,tk_root):
    window,owner,_editor,_=held_window
    calls=[]
    owner.begin()
    assert defer_global_update('theme',lambda:calls.append('applied'))
    window.destroy()
    pump(tk_root)
    assert calls==['applied']


@pytest.mark.parametrize('themed', [False, True])
def test_background_update_supports_plain_and_themed_hosts(tk_root, themed):
    from tkinter import ttk
    window = tk.Toplevel(tk_root)
    host = ttk.Frame(window) if themed else None
    owner = DeferredContentResize(window, background='#123456', host=host)
    try:
        owner.set_background('#654321')
        assert window.cget('background') == '#654321'
        if not themed:
            assert owner.host.cget('background') == '#654321'
    finally:
        window.destroy()


def test_destroy_delivers_later_handlers_after_controller_and_native_cleanup(held_window, tk_root):
    window, owner, _editor, _committed = held_window
    owner.attach_observer()
    bridge = owner.native
    assert bridge is not None
    delivered = []
    window.bind('<Destroy>', lambda event: delivered.append(True)
                if event.widget is window else None, add='+')
    window.destroy()
    pump(tk_root)
    assert delivered == [True]
    assert owner.state == 'CLOSED' and id(bridge) not in N._INSTALLED


def test_independent_hosts_and_programmatic_changes(held_window,tk_root):
    window,owner,editor,committed=held_window
    other=tk.Toplevel(tk_root)
    other.geometry('300x220')
    peer=DeferredContentResize(other,background='#654321',size=(300,220))
    pump(tk_root)
    try:
        owner.begin()
        window.geometry('700x450')
        other.geometry('350x260')
        pump(tk_root)
        assert owner.host.winfo_width()==600 and peer.host.winfo_width()==350
        assert not peer.holding and not committed
        owner.end()
        pump(tk_root)
        assert committed==[(700,450)]
        committed.clear()
        window.geometry('680x430')
        pump(tk_root)
        assert committed==[(680,430)]
        committed.clear()
        window.geometry('+110+90')
        pump(tk_root)
        assert not committed
    finally:
        other.destroy()


def test_final_native_state_delivered_without_a_configure_or_expose(held_window,tk_root):
    window,owner,_editor,committed=held_window
    bridge=SimpleNamespace(running=True,resizing=True)
    def dispatch(_event):
        owner.end()
    bridge._tk_event=Mock(side_effect=dispatch)
    owner.bind_native(bridge)
    owner.begin()
    window.geometry('730x470')
    pump(tk_root,.08)
    assert owner.holding and not committed
    bridge.running=False
    pump(tk_root,.15)
    assert bridge._tk_event.call_count==1
    assert committed==[(730,470)] and owner.state=='IDLE'


def test_main_real_chrome_and_editors_hold_across_the_breakpoint(main_window,tk_root):
    app=main_window('日本語を勉強しています。\n'*25)
    pump(tk_root)
    view=app._view
    owner=view._content_resize
    before={name:shape(widget) for name,widget in (
        ('tabs',view.workspace.tabs),('frame',view.frame),('source',app.original_text),('result',app.trans_text))}
    mode=view.mode
    owner.begin()
    app.popup.geometry('1100x760')
    pump(tk_root,.2)
    app.popup.geometry('740x650')
    pump(tk_root,.2)
    assert view.mode==mode
    assert before=={name:shape(widget) for name,widget in (
        ('tabs',view.workspace.tabs),('frame',view.frame),('source',app.original_text),('result',app.trans_text))}
    owner.end()
    pump(tk_root,.25)
    assert view.frame.winfo_width()<=app.popup.winfo_width()
    assert owner.state=='IDLE'


def test_main_result_waits_until_release_without_replacing_the_editor(main_window,tk_root):
    app=main_window('Original text')
    pump(tk_root)
    owner=app._view._content_resize
    editor=app.trans_text
    before=editor.get_plain()
    owner.begin()
    app._update_translation_with_original('A new result')
    assert editor.get_plain()==before
    owner.end()
    pump(tk_root,.2)
    assert app.trans_text is editor and editor.get_plain()=='A new result'


def test_standard_main_observer_preserves_native_frame_and_move_only_policy():
    bridge=N.WindowsSizing(None,rect=(0,0,600,400),minimum=(1,1),bounds=None,
                           on_rect=Mock(),custom_frame=False)
    bridge.hwnd=123
    bridge.common=SimpleNamespace(DefSubclassProc=Mock(return_value=77))
    assert bridge._message(123,N.WM_NCCALCSIZE,0,0,1,0)==77
    assert bridge._message(123,N.WM_GETMINMAXINFO,0,0,1,0)==77
    bridge._message(123,N.WM_SYSCOMMAND,N.SC_MOVE,0,1,0)
    bridge._message(123,N.WM_ENTERSIZEMOVE,0,0,1,0)
    assert bridge.running and not bridge.resizing


def test_quick_fallback_waits_for_release_even_after_a_long_pointer_pause(tk_root,tmp_path):
    from src.ui.quick_translate import QuickTranslateManager
    from tools.tk_resize.profile import TrialConfig
    manager=QuickTranslateManager(tk_root,TrialConfig(tmp_path/'profile'))
    try:
        manager.show('Translated text\n'*12,'English',original='日本語を勉強しています。\n'*12,
                     furigana_text='{日本語|にほんご}を勉強しています。\n'*12)
        deadline=time.monotonic()+6
        while time.monotonic()<deadline and not (manager._layout and manager._layout.get('revealed')
                and manager._layout.get('resize_metrics') and manager._content_resize.state=='IDLE'):
            pump(tk_root,.02)
        assert manager._layout and manager._layout.get('revealed')
        window=manager.popup
        owner=manager._content_resize
        native=manager._native_sizing
        assert native is not None and not native.running
        before=shape(manager._main_frame),shape(manager.popup_text)
        x,y=window.winfo_x(),window.winfo_y()
        width,height=window.winfo_width(),window.winfo_height()
        manager._grip_press(SimpleNamespace(x_root=x+width,y_root=y+height))
        manager._grip_drag(SimpleNamespace(x_root=x+width+100,y_root=y+height+50))
        pump(tk_root,.3)
        assert owner.holding and (shape(manager._main_frame),shape(manager.popup_text))==before
        assert window.winfo_width()==width
        assert manager._grip_outline is not None and manager._grip_outline.visible
        manager._grip_release()
        pump(tk_root,.3)
        assert owner.state=='IDLE' and manager._main_frame.winfo_width()==window.winfo_width()-2*manager._look.ring
        assert manager.popup_text.get_plain()=='Translated text\n'*12
    finally:
        manager.close()


def wait_for(root, predicate, timeout=6):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline and not predicate():
        pump(root,.02)
    assert predicate(), 'The expected asynchronous UI state did not arrive'


def quick_ready(manager):
    return bool(manager.popup is not None and manager._layout and manager._layout.get('revealed')
                and manager._layout.get('resize_metrics') and manager._content_resize.state=='IDLE')


@pytest.mark.parametrize('close_before_release', [False,True])
def test_quick_worker_result_waits_for_release_or_is_discarded_on_close(
        tk_root,tmp_path,close_before_release):
    import threading
    from src.ui.quick_translate import QuickTranslateManager
    from src.ui.translation_tasks import TranslationTaskRunner
    from tools.tk_resize.profile import TrialConfig
    manager = QuickTranslateManager(tk_root,TrialConfig(tmp_path/'profile'))
    runner = TranslationTaskRunner(tk_root)
    gate,completed = threading.Event(),threading.Event()
    replace = Mock()
    manager.configure_callbacks(on_copy_and_replace=replace)
    try:
        manager.show('First result.','English',original='Original source.')
        wait_for(tk_root,lambda:quick_ready(manager))
        window,owner = manager.popup,manager._content_resize
        owner.begin()
        def operation():
            assert gate.wait(2)
            completed.set()
            return 'A later result.'
        runner.start(operation,on_result=lambda value:manager.show(value,'English',
            original='A later source.'),owner=window)
        gate.set()
        wait_for(tk_root,lambda:completed.is_set() and bool(owner.pending))
        assert manager.popup is window and manager.popup_text.get_plain()=='First result.'
        assert manager.popup_furigana.get_plain()=='Original source.'
        if close_before_release:
            manager.close()
            owner.end()
            pump(tk_root,.15)
            assert manager.popup is None and not owner.pending
        else:
            owner.end()
            wait_for(tk_root,lambda:quick_ready(manager) and manager.popup is not window)
            assert manager.popup_text.get_plain()=='A later result.'
            assert manager.popup_furigana.get_plain()=='A later source.'
        replace.assert_not_called()
    finally:
        gate.set()
        runner.cancel()
        manager.close()


def test_real_theme_language_and_scale_wait_for_quick_release(design,tk_root,tmp_path):
    from src.ui.quick_translate import QuickTranslateManager
    from tools.tk_resize.profile import TrialConfig
    manager = QuickTranslateManager(tk_root,TrialConfig(tmp_path/'profile'))
    theme = design.manager
    previous = theme.text_scale,theme.ui_language
    try:
        manager.show('Translated text.','English',original='Original text.')
        wait_for(tk_root,lambda:quick_ready(manager))
        owner = manager._content_resize
        before = shape(owner.host),shape(manager.popup_text)
        owner.begin()
        theme.set_text_scale(1.5)
        theme.set_ui_language('ja')
        pump(tk_root,.15)
        assert (theme.text_scale,theme.ui_language)==previous
        assert (shape(owner.host),shape(manager.popup_text))==before
        owner.end()
        wait_for(tk_root,lambda:quick_ready(manager) and theme.text_scale==1.5
                 and theme.ui_language=='ja')
        assert manager.popup_text.get_plain()=='Translated text.'
        assert manager.popup_furigana.get_plain()=='Original text.'
    finally:
        manager.close()
        theme.set_text_scale(previous[0])
        theme.set_ui_language(previous[1])

"""No-activate client gestures continue beyond the unchanged popup rectangle."""
from types import SimpleNamespace
from unittest.mock import Mock
import ctypes
import json

import pytest

from src.ui.pointer_resize import PointerResize, PointerState
from src.ui.popup_layout import WorkArea
from tests.final_ui_helpers import design
from tests.test_deferred_resize import pump, wait_for, quick_ready
from tests.test_outline_resize import quick

pytestmark=pytest.mark.usefixtures('design')


def prepare(quick,root,monkeypatch):
    quick._grip_press(SimpleNamespace(x_root=100,y_root=100))
    quick._user_rect=(400,100,640,660)
    quick._grip_release()
    wait_for(root,lambda:quick_ready(quick) and quick.popup.winfo_width()==640)
    monkeypatch.setattr(quick,'_screen_at',lambda _x,_y:WorkArea(0,0,1920,1032))
    state=[PointerState(1040,760,True)]
    original=quick._start_grip_pointer
    monkeypatch.setattr(quick,'_start_grip_pointer',lambda:original(lambda:state[0]))
    return state


def test_pointer_only_shrink_then_grow_outside_old_box_and_release(quick,tk_root,monkeypatch):
    state=prepare(quick,tk_root,monkeypatch)
    window=quick.popup
    active=ctypes.windll.user32.GetActiveWindow()
    canonical=quick.popup_text.get_plain()
    quick._begin_resize(SimpleNamespace(x_root=1040,y_root=760))
    state[0]=PointerState(960,700,True)
    wait_for(tk_root,lambda:quick._grip_outline is not None and quick._grip_outline.rect==(400,100,560,600))
    assert (window.winfo_width(),window.winfo_height())==(640,660)
    # No B1-Motion or ButtonRelease event is delivered outside the old client.
    state[0]=PointerState(1240,880,True)
    wait_for(tk_root,lambda:quick._grip_outline.rect==(400,100,840,780))
    pump(tk_root,.2)
    assert (window.winfo_width(),window.winfo_height())==(640,660)
    state[0]=PointerState(1260,900,False)
    wait_for(tk_root,lambda:quick._grip_pointer is None and quick._user_size==(860,800)
             and window.winfo_width()==860)
    assert quick._grip_outline is None
    assert quick.popup_text.get_plain()==canonical
    assert ctypes.windll.user32.GetActiveWindow()==active


def test_separate_shrink_gesture_does_not_cap_the_next_growth(quick,tk_root,monkeypatch):
    state=prepare(quick,tk_root,monkeypatch)
    for start,finish,target in [((1040,760),(960,700),(560,600)),
                                ((960,700),(1240,880),(840,780))]:
        state[0]=PointerState(*start,True)
        quick._begin_resize(SimpleNamespace(x_root=start[0],y_root=start[1]))
        state[0]=PointerState(*finish,False)
        wait_for(tk_root,lambda:quick._grip_pointer is None and quick._user_size==target
                 and quick.popup.winfo_width()==target[0])
    assert quick.popup.winfo_width()>640


@pytest.mark.parametrize('ending',['escape','close','destroy','reset'])
def test_pointer_tracking_cleanup_discards_or_finishes_owned_work(quick,tk_root,monkeypatch,ending):
    state=prepare(quick,tk_root,monkeypatch)
    quick._begin_resize(SimpleNamespace(x_root=1040,y_root=760))
    tracker=quick._grip_pointer
    state[0]=PointerState(1200,840,True)
    wait_for(tk_root,lambda:quick._grip_outline is not None)
    if ending=='escape':
        state[0]=PointerState(1200,840,True,True)
        wait_for(tk_root,lambda:quick._grip_pointer is None)
        assert (quick.popup.winfo_width(),quick.popup.winfo_height())==(640,660)
    elif ending=='close':quick.close()
    elif ending=='destroy':quick.popup.destroy()
    else:quick._grip_reset()
    pump(tk_root,.15)
    assert not tracker.active and tracker.job is None
    assert quick._grip_pointer is None and quick._grip_outline is None
    if ending in ('close','destroy'):
        assert not quick._resize_handles and quick._grip is None


def test_native_start_takes_ownership_from_client_pointer_tracking(quick,tk_root,monkeypatch):
    prepare(quick,tk_root,monkeypatch)
    quick._begin_resize(SimpleNamespace(x_root=1040,y_root=760))
    tracker=quick._grip_pointer
    quick._native_resize_started('se',(400,100,640,660),(1040,760))
    assert not tracker.active and tracker.job is None and quick._grip_pointer is None
    quick._cancel_user_resize()


def test_unpressed_pointer_does_not_start_a_timer(tk_root):
    callback=Mock()
    tracker=PointerResize(tk_root,tk_root,lambda:PointerState(0,0,False),
        on_move=callback,on_release=callback,on_cancel=callback)
    assert not tracker.start() and tracker.job is None and not tracker.active
    callback.assert_not_called()


@pytest.mark.parametrize('sample',[None,PointerState(1200,840,False),PointerState(1200,840,True,True)])
def test_ended_pointer_before_start_does_not_leave_quick_holding(quick,tk_root,monkeypatch,sample):
    state=prepare(quick,tk_root,monkeypatch)
    window=quick.popup
    previous=(window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())
    state[0]=sample
    quick._begin_resize(SimpleNamespace(x_root=1040,y_root=760),'se')
    wait_for(tk_root,lambda:quick_ready(quick))
    assert quick._grip_pointer is None and quick._grip_start is None
    assert quick._grip_outline is None and not quick._content_resize.holding
    assert (window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())==previous


def client_press(quick, root, state, edge):
    target=quick._resize_handles[edge]
    x=target.winfo_rootx()+target.winfo_width()//2
    y=target.winfo_rooty()+target.winfo_height()//2
    hit=root.winfo_containing(x,y)
    # The visible bottom-right grip overlaps its equivalent corner target.
    assert hit is target or (edge=='se' and hit is quick._grip)
    state[0]=PointerState(x,y,True)
    hit.event_generate('<ButtonPress-1>',x=x-hit.winfo_rootx(),y=y-hit.winfo_rooty(),
                       rootx=x,rooty=y)
    assert quick._grip_start is not None and quick._grip_start['edge']==edge
    assert quick._grip_pointer is not None and quick._grip_pointer.active
    return x,y


@pytest.mark.parametrize('theme',['light','dark','pink','blue','yellow','green'])
@pytest.mark.parametrize('language,scale',[('en',1.0),('ja',1.5)])
@pytest.mark.parametrize('edge',['n','s','e','w','ne','nw','se','sw'])
def test_actual_edge_targets_shrink_then_grow_beyond_old_box(
        quick,tk_root,monkeypatch,design,tmp_path,theme,language,scale,edge):
    design.manager.set_mode(theme)
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(scale)
    quick.show('Translated text.','English',original='Original text.')
    wait_for(tk_root,lambda:quick_ready(quick))
    metrics=quick._layout['resize_metrics']
    width=max(640,quick._required_width()+100)
    height=max(660,quick._layout['chrome']+metrics['min_translation']+metrics['min_original']+100)
    quick._grip_press(SimpleNamespace(x_root=100,y_root=100))
    quick._user_rect=(120,100,width,height)
    quick._grip_release()
    wait_for(tk_root,lambda:quick_ready(quick) and quick.popup.winfo_width()==width)
    monkeypatch.setattr(quick,'_screen_at',lambda _x,_y:WorkArea(-1920,-1080,3840,2160))
    state=[PointerState(0,0,False)]
    original=quick._start_grip_pointer
    monkeypatch.setattr(quick,'_start_grip_pointer',lambda:original(lambda:state[0]))
    window=quick.popup
    owner=quick._content_resize
    active=ctypes.windll.user32.GetActiveWindow()
    canonical=quick.popup_text.get_plain()
    assert quick._native_sizing.client_edges
    changes=[]
    window.bind('<Configure>',lambda event:changes.append(
        (event.x,event.y,event.width,event.height)) if event.widget is window else None,add='+')
    assert all(target.cget('bg')==design.manager.palette.surface
               for target in quick._resize_handles.values())
    for amounts in [(-30,), (60,100)]:
        base=(window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())
        commits=owner.commit_count
        start=client_press(quick,tk_root,state,edge)
        for amount in amounts:
            dx=amount*(-1 if 'w' in edge else 1 if 'e' in edge else 0)
            dy=amount*(-1 if 'n' in edge else 1 if 's' in edge else 0)
            expected=(base[0]-(amount if 'w' in edge else 0),
                      base[1]-(amount if 'n' in edge else 0),
                      base[2]+(amount if 'w' in edge or 'e' in edge else 0),
                      base[3]+(amount if 'n' in edge or 's' in edge else 0))
            state[0]=PointerState(start[0]+dx,start[1]+dy,True)
            wait_for(tk_root,lambda:quick._grip_outline is not None
                     and quick._grip_outline.rect==expected)
            pump(tk_root,.05)
            assert (window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())==base
            assert owner.commit_count==commits
        # Outside release is sampled without delivering B1-Motion or Release events.
        state[0]=state[0]._replace(pressed=False)
        try:
            wait_for(tk_root,lambda:quick._grip_pointer is None and quick_ready(quick)
                     and (window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())==expected)
        except AssertionError:
            details=dict(expected=expected,base=base,
                actual=(window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height()),
                owner_state=owner.state,commits=owner.commit_count,previous_commits=commits,
                user_rect=quick._user_rect,user_size=quick._user_size,grip=quick._grip_start,
                pointer=repr(quick._grip_pointer),layout_position=quick._layout.get('position'),
                layout_size=quick._layout.get('size'),native_running=quick._native_sizing.running,
                configure_events=changes)
            (tmp_path/'release-failure.json').write_text(json.dumps(details,indent=2),encoding='utf-8')
            raise AssertionError(details) from None
        assert owner.commit_count==commits+1
        assert quick._grip_outline is None
    assert window.winfo_width()>=width and window.winfo_height()>=height
    assert quick.popup_text.get_plain()==canonical
    assert ctypes.windll.user32.GetActiveWindow()==active
    if edge=='nw':
        from tools.ui_gallery import capture
        assert capture(int(window.wm_frame(),16),str(tmp_path/'edge-targets.png'))


def test_live_theme_change_recolours_resize_targets_and_close_drops_them(quick,tk_root,design):
    handles=list(quick._resize_handles.values())
    design.manager.set_mode('dark')
    pump(tk_root)
    assert all(target.cget('bg')==design.manager.palette.surface for target in handles)
    assert quick._grip.cget('bg')==design.manager.palette.surface
    quick.close()
    assert not quick._resize_handles and quick._grip is None
    assert not any(target.winfo_exists() for target in handles)


@pytest.mark.parametrize('edge',['n','s','e','w','ne','nw','se','sw'])
def test_late_motion_cannot_turn_a_finished_move_or_resize_into_window_movement(
        quick,tk_root,monkeypatch,edge):
    state=prepare(quick,tk_root,monkeypatch)
    window=quick.popup
    frame=quick._main_frame
    frame.event_generate('<ButtonPress-1>',x=10,y=100,rootx=410,rooty=200)
    frame.event_generate('<B1-Motion>',x=30,y=120,rootx=430,rooty=220)
    wait_for(tk_root,lambda:(window.winfo_x(),window.winfo_y())==(420,120))
    frame.event_generate('<ButtonRelease-1>',x=10,y=100,rootx=430,rooty=220)
    frame.event_generate('<B1-Motion>',x=10,y=100,rootx=630,rooty=420)
    pump(tk_root,.05)
    assert (window.winfo_x(),window.winfo_y())==(420,120)
    x,y=client_press(quick,tk_root,state,edge)
    dx=-40 if 'w' in edge else 40 if 'e' in edge else 0
    dy=-40 if 'n' in edge else 40 if 's' in edge else 0
    state[0]=PointerState(x+dx,y+dy,True)
    wait_for(tk_root,lambda:quick._grip_outline is not None)
    expected=quick._grip_outline.rect
    state[0]=state[0]._replace(pressed=False)
    wait_for(tk_root,lambda:quick._grip_pointer is None and quick_ready(quick))
    frame.event_generate('<B1-Motion>',x=10,y=100,rootx=630,rooty=420)
    pump(tk_root,.1)
    assert (window.winfo_x(),window.winfo_y(),window.winfo_width(),window.winfo_height())==expected

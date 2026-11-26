"""Private-desktop resize experiment; never imported by production UI.

Usage: python tools/prototype_native_resize.py --label native-prototype --child 100
Native hit testing and message-driven geometry are real; injected size messages
are not physical mouse motion or pointer-to-photon measurements.
"""
import bisect
from collections import Counter
import ctypes as C
from ctypes import wintypes as W
import json
import math
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from tools import ui_gallery as gallery

HT = {'w':10,'e':11,'n':12,'nw':13,'ne':14,'s':15,'sw':16,'se':17}
WM_ENTER,WM_EXIT,WM_SIZE,WM_PAINT,WM_NCHITTEST = 0x231,0x232,5,15,0x84


class MinMax(C.Structure):
    _fields_=[(name,W.POINT) for name in ('reserved','max_size','max_position','min_track','max_track')]


def hit_test(rect,x,y,edge):
    left,top,right,bottom=rect
    if not (left<=x<right and top<=y<bottom):return 0
    vertical='n' if y<top+edge else 's' if y>=bottom-edge else ''
    horizontal='w' if x<left+edge else 'e' if x>=right-edge else ''
    return HT.get(vertical+horizontal,1)


class NativeSizing:
    """A same-thread, pointer-sized, owned wrapper subclass with deterministic removal."""

    def __init__(self,window,work,minimum,popup=False,edge=6):
        self.window,self.work,self.minimum,self.popup,self.edge=window,work,minimum,popup,edge
        self.hwnd=int(window.wm_frame(),16)
        self.events,self.errors=[],[]
        self.attached=False
        self.user=C.WinDLL('user32',use_last_error=True)
        self.common=C.WinDLL('comctl32',use_last_error=True)
        self.proc_type=C.WINFUNCTYPE(C.c_ssize_t,W.HWND,W.UINT,C.c_size_t,C.c_ssize_t,C.c_size_t,C.c_size_t)
        declarations=(
            (self.user.GetWindowThreadProcessId,[W.HWND,C.POINTER(W.DWORD)],W.DWORD),
            (self.user.GetWindowRect,[W.HWND,C.POINTER(W.RECT)],W.BOOL),
            (self.user.SetWindowPos,[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL),
            (self.user.GetWindowLongPtrW,[W.HWND,C.c_int],C.c_ssize_t),
            (self.user.SetWindowLongPtrW,[W.HWND,C.c_int,C.c_ssize_t],C.c_ssize_t),
            (self.user.SendMessageW,[W.HWND,W.UINT,C.c_size_t,C.c_ssize_t],C.c_ssize_t),
            (self.common.SetWindowSubclass,[W.HWND,self.proc_type,C.c_size_t,C.c_size_t],W.BOOL),
            (self.common.RemoveWindowSubclass,[W.HWND,self.proc_type,C.c_size_t],W.BOOL),
            (self.common.DefSubclassProc,[W.HWND,W.UINT,C.c_size_t,C.c_ssize_t],C.c_ssize_t),
        )
        for function,args,result in declarations:function.argtypes,function.restype=args,result
        outer=self.rect()
        self.chrome=(outer[2]-outer[0]-window.winfo_width(),outer[3]-outer[1]-window.winfo_height())
        if not popup:
            self.minimum=tuple(minimum[index]+self.chrome[index] for index in (0,1))
        import os
        pid=W.DWORD()
        thread=self.user.GetWindowThreadProcessId(self.hwnd,C.byref(pid))
        kernel=C.WinDLL('kernel32')
        kernel.GetCurrentThreadId.restype=W.DWORD
        if pid.value!=os.getpid() or thread!=kernel.GetCurrentThreadId():
            raise RuntimeError('Only an owned UI-thread wrapper may be subclassed')
        self.original_style=self.user.GetWindowLongPtrW(self.hwnd,-16)
        self.original_exstyle=self.user.GetWindowLongPtrW(self.hwnd,-20)
        self.callback=self.proc_type(self._message)
        self.identity=id(self)
        if not self.common.SetWindowSubclass(self.hwnd,self.callback,self.identity,0):raise C.WinError(C.get_last_error())
        self.attached=True
        if popup:
            self.user.SetWindowLongPtrW(self.hwnd,-16,self.original_style|0x40000)
            self.dispatch(lambda: self.user.SetWindowPos(self.hwnd,None,0,0,0,0,0x27))

    def dispatch(self,call):
        """Deliver native messages while the UI thread is inside Tk's event dispatcher.

        A direct ctypes SetWindowPos/SendMessage outside ENTER_TCL can re-enter
        Tcl_ServiceAll without _tkinter's saved thread state. Only the native
        call runs on the helper; every window procedure and all Tk stay on UI.
        The measured cost includes this experimental dispatch overhead.
        """
        result=[]
        def native_call():
            try:result.append((True,call()))
            except Exception as error:result.append((False,error))
        worker=threading.Thread(target=native_call,daemon=True)
        worker.start()
        while worker.is_alive():
            self.window.update()
            worker.join(.001)
        self.window.update()
        if not result[0][0]:raise result[0][1]
        return result[0][1]

    def rect(self):
        value=W.RECT()
        if not self.user.GetWindowRect(self.hwnd,C.byref(value)):raise C.WinError(C.get_last_error())
        return value.left,value.top,value.right,value.bottom

    def _message(self,hwnd,message,wparam,lparam,identity,data):
        # Never call Tk from this native callback, including after()/update().
        try:
            if message in (WM_ENTER,WM_EXIT,WM_SIZE,WM_PAINT):self.events.append((message,time.perf_counter()))
            if self.popup and message==WM_NCHITTEST:
                return hit_test(self.rect(),C.c_int16(lparam&0xffff).value,C.c_int16((lparam>>16)&0xffff).value,self.edge)
            if self.popup and message==0x83:return 0
            if self.popup and message==0x21:return 3
            if message==0x24 and lparam:
                result=self.common.DefSubclassProc(hwnd,message,wparam,lparam)
                limits=C.cast(lparam,C.POINTER(MinMax)).contents
                limits.min_track.x,limits.min_track.y=self.minimum
                limits.max_track.x=self.work[2]-self.work[0]
                limits.max_track.y=self.work[3]-self.work[1]
                return result
            if message==0x82:
                self.common.RemoveWindowSubclass(hwnd,self.callback,self.identity)
                self.attached=False
        except Exception as error:
            self.errors.append(type(error).__name__)
        return self.common.DefSubclassProc(hwnd,message,wparam,lparam)

    def send(self,message,wparam=0,lparam=0):
        return self.dispatch(lambda: self.user.SendMessageW(self.hwnd,message,wparam,lparam))

    def set_rect(self,rect):
        x,y,width,height=rect
        # Tk's native geometry command establishes ENTER_TCL before WmProc can
        # service callbacks. Keep cross-thread dispatch outside the measured motion.
        self.window.geometry(f'{max(1,width-self.chrome[0])}x{max(1,height-self.chrome[1])}{x:+d}{y:+d}')

    def close(self):
        if self.attached:
            if self.popup:
                self.user.SetWindowLongPtrW(self.hwnd,-16,self.original_style)
                self.dispatch(lambda: self.user.SetWindowPos(self.hwnd,None,0,0,0,0,0x27))
            if not self.common.RemoveWindowSubclass(self.hwnd,self.callback,self.identity):raise C.WinError(C.get_last_error())
            self.attached=False
        # Keep the callback pinned until this method has returned and no native call is active.


def summaries(values):
    ordered=sorted(values)
    return dict(samples=len(values),p50_ms=ordered[len(ordered)//2],p95_ms=ordered[math.ceil(len(ordered)*.95)-1],max_ms=max(values))


def viewport_reading(reading):
    """Temporarily restrict the existing renderer to visible segments, preserving offsets."""
    from src.core.furigana import RubySegment
    original=reading.draw
    def draw():
        segments=reading.segments
        if not segments or reading.closed:return original()
        widget=reading.widget
        start=len(widget.get('1.0',widget.index('@0,0')))
        end=len(widget.get('1.0',widget.index(f'@{max(1,widget.winfo_width()-1)},{max(1,widget.winfo_height()-1)}')))+1
        offsets=[]
        total=0
        for segment in segments:
            total+=len(segment.base)
            offsets.append(total)
        first=max(0,bisect.bisect_right(offsets,start)-1)
        last=min(len(segments),bisect.bisect_left(offsets,end)+2)
        prefix=reading.snapshot[:offsets[first-1]] if first else ''
        reading.segments=((RubySegment(prefix,None),) if prefix else ())+tuple(segments[first:last])
        try:return original()
        finally:reading.segments=segments
    reading.draw=draw
    return original


def cheap_popup(manager,rect):
    """Only geometry and cached allocation; full measurements happen after the gesture."""
    from src.ui import popup_layout
    x,y,width,height=rect
    previous=manager._layout['heights']
    room=max(2,height-manager._layout['chrome'])
    share=previous.original/max(1,previous.original+previous.translation)
    original=min(max(0,round(room*share)),max(0,room-1))
    translated=max(1,room-original)
    gutter=manager._text_scrollbar.winfo_reqwidth()
    manager._fit_box(manager._text_holder,manager.popup_text,manager._text_scrollbar,translated,True,gutter)
    if manager.popup_furigana is not None:
        manager._fit_box(manager._original_holder,manager.popup_furigana,manager._original_scrollbar,original,True,gutter)
    manager._fit_header(width)
    manager._layout.update(size=(width,height),position=(x,y),heights=popup_layout.Heights(translated,original,True,True))


RESULTS=[]


def surface(ctx):
    from src.core.translation_alignment import AlignedText,make_metadata
    app=ctx.app
    corpus=[('short','Good morning.','おはようございます。','Japanese'),
            ('ruby-medium','毎日日本語を勉強しています。'*24,'I study Japanese every day. '*24,'English'),
            ('long','A long source paragraph. '*250,'日本語の長い翻訳結果です。'*180,'Japanese')]
    for linked in (False,True):
        app.config.set_linked_reading_enabled(linked)
        for name,source,text,target in corpus:
            metadata=make_metadata(source,text,target,'text',[dict(source=[0,len(source)],translated=[0,len(text)])]) if linked else None
            result=AlignedText(text,metadata) if linked else text
            for host in ('main','popup'):
                app.config.set_furigana_enabled(True)
                app.show_popup(source,result,target,force_new=True)
                manager=app.quick_translate_manager
                if host=='popup':
                    manager._last_mouse_x,manager._last_mouse_y=500,300
                    manager.show(result,target,original=source)
                    ctx.pump(.3)
                    window=manager.popup
                    reading=None
                    manager._linked_active=linked
                    manager._sync_linked_reading()
                else:
                    ctx.pump(.2)
                    window=app.popup
                    reading=app._inline_reading
                    app._main_linked.reading_active=linked
                    app._main_linked.sync()
                ctx.pump(.2)
                if host=='popup':
                    deadline=time.perf_counter()+4
                    while 'chrome' not in (manager._layout or {}) and time.perf_counter()<deadline:
                        ctx.pump(.05)
                    if 'chrome' not in (manager._layout or {}):
                        yield f'{host}-{name}-startup-pending',window
                        raise RuntimeError('The popup did not finish its initial layout within four seconds')
                frame=gallery.window_rect(int(window.wm_frame(),16))
                client=(window.winfo_width(),window.winfo_height())
                chrome=(frame[2]-frame[0]-client[0],frame[3]-frame[1]-client[1])
                base_width=min(client[0],1100)
                base_height=min(client[1],700)
                work=gallery.work_area()
                minimum=tuple(map(int,window.minsize()))
                if host=='popup':
                    from src.constants import POPUP_USER_MIN_ROWS
                    min_height=manager._layout['chrome']
                    for box in (manager.popup_text,manager.popup_furigana):
                        if box is not None:min_height+=manager._rows_px(box,POPUP_USER_MIN_ROWS)+manager._insets(box,'y')
                    minimum=(manager._required_width(),min_height)
                motions=[]
                for step in range(12):
                    width=max(500,minimum[0],min(base_width+step%4*18,work[2]-work[0]-80-chrome[0]))
                    height=max(380,minimum[1],min(base_height+step%3*10,work[3]-work[1]-80-chrome[1]))
                    motions.append((work[0]+30,work[1]+30,width+chrome[0],height+chrome[1]))
                canonical=(app.original_text.get('1.0','end-1c'),app.trans_text.get_plain())
                hook=NativeSizing(window,work,minimum,popup=host=='popup',edge=round(6*ctx.scale/100))
                tests={}
                try:
                    limits=MinMax()
                    hook.send(0x24,lparam=C.addressof(limits))
                    assert (limits.min_track.x,limits.min_track.y)==hook.minimum
                    assert (limits.max_track.x,limits.max_track.y)==(work[2]-work[0],work[3]-work[1])
                    if host=='popup':
                        left,top,right,bottom=hook.rect()
                        points={'nw':(left+1,top+1),'n':((left+right)//2,top+1),'ne':(right-1,top+1),
                                'w':(left+1,(top+bottom)//2),'e':(right-1,(top+bottom)//2),
                                'sw':(left+1,bottom-1),'s':((left+right)//2,bottom-1),'se':(right-1,bottom-1)}
                        for edge,(x,y) in points.items():
                            tests[edge]=hook.send(WM_NCHITTEST,lparam=((y&0xffff)<<16)|(x&0xffff))
                        assert all(tests[edge]==value for edge,value in HT.items())
                        assert hook.user.GetWindowLongPtrW(hook.hwnd,-20)&0x08000000 == hook.original_exstyle&0x08000000
                    for phase,experimental in enumerate((False,True,True,False)):
                        old_draw=viewport_reading(reading) if experimental and reading else None
                        calls,pumps=[],[]
                        first_event=len(hook.events)
                        phase_start=time.perf_counter()
                        hook.send(WM_ENTER)
                        try:
                            for rect in motions:
                                stamp=time.perf_counter()
                                hook.set_rect(rect)
                                if host=='popup':
                                    body=(rect[0],rect[1],rect[2]-chrome[0],rect[3]-chrome[1])
                                    if experimental:cheap_popup(manager,body)
                                    else:manager._fit_boxes(reposition=False,size=body[2:],position=body[:2])
                                calls.append((time.perf_counter()-stamp)*1000)
                                stamp=time.perf_counter()
                                ctx.pump(.03)
                                pumps.append((time.perf_counter()-stamp)*1000)
                        finally:
                            hook.send(WM_EXIT)
                            if old_draw is not None:reading.draw=old_draw
                        stamp=time.perf_counter()
                        if host=='popup':manager._fit_boxes(reposition=False,size=body[2:],position=body[:2])
                        ctx.pump(.1)
                        settled=(time.perf_counter()-stamp)*1000
                        duration=time.perf_counter()-phase_start
                        messages=Counter(message for message,stamp in hook.events[first_event:])
                        assert (app.original_text.get('1.0','end-1c'),app.trans_text.get_plain())==canonical
                        if host=='popup':
                            assert manager.popup_text.get_plain()==text
                            assert manager.popup_furigana.get_plain()==source
                        RESULTS.append(dict(host=host,corpus=name,linked=linked,experimental=experimental,phase=phase,
                                            callback=summaries(calls),event_pump_including_30ms_observation=summaries(pumps),
                                            settle_including_100ms_observation_ms=settled,hit_tests=tests,
                                            source_ruby_segments=sum(bool(s.ruby) for s in reading.segments) if reading else None,
                                            result_embedded_windows=len(app.trans_text.window_names()),native_events=dict(messages),
                                            wrapper_events_per_second=sum(messages.values())/duration,
                                            native_minimum=hook.minimum,phase_seconds=duration))
                    assert not hook.errors
                    yield f'{host}-{name}-linked-{linked}',window
                finally:
                    hook.close()
                    assert not hook.attached
                    if host=='popup':manager.close()


def main(argv=None):
    gallery.SURFACES['native_resize_prototype']=surface
    args=gallery.parse_args(argv)
    if args.child is None:raise SystemExit('Use --child 100 or --child 150; run profiles serially.')
    report=gallery.run_scale(args,args.child)
    folder=ROOT/'logs/ui_gallery'/args.label
    folder.mkdir(parents=True,exist_ok=True)
    (folder/f's{args.child}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/f'measurements-s{args.child}.json').write_text(json.dumps(dict(methodology=__doc__,cases=RESULTS),indent=2),encoding='utf-8')
    return int(bool(report['errors'] or report['blocked'] or report['windows_on_user_desktop']))


if __name__=='__main__':raise SystemExit(main())

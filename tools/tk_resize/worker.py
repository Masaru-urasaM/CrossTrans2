"""Run one Tk source version on a private desktop, with the existing full interface."""
import argparse
import ctypes
from ctypes import wintypes
from collections import defaultdict, deque
import functools
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace


HOST_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ENTRY = time.perf_counter()
sys.path.insert(0, str(HOST_ROOT))
from tools.tk_resize.settlement import LayoutSettlement, snapshot_layout
from tools.ui_gallery import capture as capture_native, is_blank as capture_is_blank


class MemoryCounters(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('page_faults', wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set', 'peak_paged',
        'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]


def gesture_rectangle(work, scale, minimum):
    """Choose one bounded user-size fixture before the measured drag begins."""
    left, top, right, bottom = work
    width = max(round(880 * scale), minimum[0])
    height = max(round(740 * scale), minimum[1])
    width, height = min(width, right - left - 20), min(height, bottom - top - 20)
    if width < minimum[0] or height < minimum[1] or min(width, height) <= 0:
        raise ValueError('Work area cannot contain the gesture minimum')
    x = left + max(10, min(515, right - left - width - 10))
    return x, top + 10, width, height


def process_start_epoch():
    if os.name != 'nt':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, cpu_kernel, cpu_user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(cpu_kernel), ctypes.byref(cpu_user)):
        return None
    return ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10_000_000 - 11_644_473_600


def scenario_class(base):
    class ResizeScenario(base):
        def __init__(self, *args):
            super().__init__(*args)
            self.callback_costs = defaultdict(list)
            self.loop_gaps = []
            self.previous_resize_tick = None
            self.quick_start = None
            self.quick_constraints = None
            self.release_costs = []
            self.quick_body_observations = []
            self.main_body_observations = []
            self.held_captures = []
            self.background_checks = []
            self.outline_checks = []
            self.main_outline_start = None
            self.geometry_probe = None
            self.capture_waiting = False
            self.capture_queue = deque()
            self.capture_active = False
            self.capture_visibility = []
            self.finish_requested = self.finished = False
            self.manual_geometry = deque(maxlen=12000)
            self.manual_geometry_total = 0
            self.gesture_preparation = None
            self.gesture_prepared = False
            self.settlement = None
            self.release_time = None
            self.settlement_jobs = set()
            self.early_capture_done = False
            self.settlement_failure = None
            self._instrument(self.app._view, '_arrange_for_width', 'main.layout')
            self._instrument(self.app._inline_reading, 'draw', 'main.inline_reading')
            self._instrument(self.app._view.body_area, '_sync_layout', 'main.scroll_layout')
            manager = self.app.quick_translate_manager
            for method in ('_fit_boxes', '_content_px', '_rows_px', '_live_user_layout', '_settle_user_size',
                           '_grip_drag', '_apply_user_size', '_grip_release', '_native_resize_rect',
                           '_native_resize_started', '_native_resize_finished'):
                self._instrument(manager, method, 'quick.' + method)

        def _show(self):
            state = getattr(self.arguments, 'view_state', 'normal')
            if state in ('home-history','home-dictionary'):
                key = state.removeprefix('home-')
                self.page = self.app._view.workspace.select_home(key)
                if key == 'dictionary':
                    self.page.receive_text(self.workload.source,
                        source_language=self.workload.source_language,target_language=self.workload.target)
                    self.page.analyze()
                    boxes = self.page.custom_boxes
                    if boxes is None:
                        raise RuntimeError('Home Dictionary did not create its word selector')
                    for _ in range(self.workload.phrase_count-len(boxes._boxes)):
                        boxes._add_box_at(len(boxes._boxes)-1)
                    for index,box in enumerate(boxes._boxes):
                        box.text_widget.insert('1.0',f'Fixture phrase {index+1}')
                self.app.popup.bind('<Configure>',lambda event:self.recorder.geometry('main',event.width,event.height)
                    if event.widget is self.app.popup else None,add='+')
            else:
                super()._show()
            manager = self.app.quick_translate_manager
            if state == 'options':
                self.app._view.show_options(True)
            elif state == 'busy':
                self.app._view.set_busy(True, 'Translating')
            elif state == 'error':
                self.app._view.show_error('Fixture connection timed out. Please try again.')
                if manager.popup is not None:
                    manager.show('Error: Fixture connection timed out. Please try again.',
                        self.workload.target, original=self.workload.source,
                        source_lang=self.workload.source_language)
            elif state == 'preview' and manager.popup is not None:
                manager._show_replace_preview()
            elif state == 'settings':
                self.app.show_settings()
            if self.workload.name == 'quick-expanded':
                self.app.quick_translate_manager._toggle_original()
            if self.arguments.manual_visible_desktop:
                for name, window in (('main', self.app.popup),
                                     ('quick', self.app.quick_translate_manager.popup)):
                    if window is not None:
                        def changed(event, owner=window, surface=name):
                            if event.widget is owner:
                                row = dict(window=surface, x=event.x, y=event.y,
                                           width=event.width, height=event.height,
                                           elapsed_ms=(time.perf_counter() - self.started) * 1000)
                                self.manual_geometry.append(row)
                                self.manual_geometry_total = getattr(self, 'manual_geometry_total', 0) + 1
                        window.bind('<Configure>', changed, add='+')

        def _capture(self, suffix, on_complete=None):
            if self.arguments.manual_visible_desktop:
                return
            self.capture_queue.append((suffix,on_complete))
            self.capture_waiting = True
            if not self.capture_active:
                self.capture_active = True
                self._settlement_after(1,self._next_capture)

        def _capture_user(self):
            if not hasattr(self,'capture_user'):
                user = ctypes.WinDLL('user32',use_last_error=True)
                user.GetWindowLongW.argtypes = [wintypes.HWND,ctypes.c_int]
                user.GetWindowLongW.restype = wintypes.LONG
                user.SetWindowPos.argtypes = [wintypes.HWND,wintypes.HWND,ctypes.c_int,
                    ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
                user.SetWindowPos.restype = wintypes.BOOL
                user.GetActiveWindow.restype = user.GetFocus.restype = wintypes.HWND
                self.capture_user = user
            return self.capture_user

        def _next_capture(self):
            if not self.capture_queue:
                self.capture_active = self.capture_waiting = False
                self.previous_resize_tick = None
                if self.finish_requested:
                    ResizeScenario._finish(self)
                elif self.phase != 'layout-settle':
                    if self.timer is not None:
                        self.root.after_cancel(self.timer)
                    self.timer = self.root.after(16,self._tick)
                return
            self.capture_suffix,self.capture_done = self.capture_queue.popleft()
            self.capture_previous = self.recorder.current_scenario
            self.recorder.scenario(self.capture_previous+'-capture')
            self.capture_surfaces = deque((role,window) for role,window in (
                ('main',self.app.popup),('quick',self.app.quick_translate_manager.popup))
                if window is not None and window.winfo_exists() and window.winfo_ismapped())
            self._raise_capture_surface()

        def _raise_capture_surface(self):
            if not self.capture_surfaces:
                try:
                    if self.capture_suffix.startswith('during-'):
                        self._capture_held_contract(self.capture_suffix)
                except Exception as error:
                    self.recorder.exception('held-capture.error',error)
                    self.findings.append(dict(kind='held-capture-error',error_type=type(error).__name__))
                self.recorder.scenario(self.capture_previous)
                if self.capture_done:
                    self.capture_done()
                self._settlement_after(1,self._next_capture)
                return
            role,window = self.capture_surfaces.popleft()
            user = self._capture_user()
            hwnd = int(window.wm_frame(),16)
            state = dict(role=role,window=window,hwnd=hwnd,
                topmost=bool(user.GetWindowLongW(hwnd,-20)&8),
                active=user.GetActiveWindow(),focus=user.GetFocus())
            if not user.SetWindowPos(hwnd,-1,0,0,0,0,0x13):
                self.findings.append(dict(kind='capture-raise-failed',window=role))
            self._settlement_after(100,lambda:self._warm_capture_surface(state))

        def _warm_capture_surface(self,state):
            path = self.recorder.directory/f"{state['role']}-{self.capture_suffix}-raw.png"
            try:
                if not capture_native(state['hwnd'],str(path),flags=0):
                    self.findings.append(dict(kind='capture-control-unavailable',window=state['role']))
                state['raw_file'] = path.name
            except Exception as error:
                self.recorder.exception('capture-control.error',error)
                self.findings.append(dict(kind='capture-control-error',window=state['role']))
            self._settlement_after(100,lambda:self._take_capture_surface(state))

        def _take_capture_surface(self,state):
            user = self._capture_user()
            role = state['role']
            suffix = self.capture_suffix
            try:
                path = self.recorder.directory/f'{role}-{suffix}.png'
                if not capture_native(state['hwnd'],str(path),flags=0):
                    self.findings.append(dict(kind='capture-unavailable',window=role,state=suffix))
                else:
                    self.captures.append(dict(window=role,state=suffix,file=path.name))
                    if capture_is_blank(str(path)):
                        self.findings.append(dict(kind='capture-blank',window=role,state=suffix))
            except Exception as error:
                self.recorder.exception('capture.error',error)
                self.findings.append(dict(kind='capture-error',window=role,error_type=type(error).__name__))
            finally:
                if not state['topmost']:
                    user.SetWindowPos(state['hwnd'],-2,0,0,0,0,0x13)
                stable = state['active']==user.GetActiveWindow() and state['focus']==user.GetFocus()
                self.capture_visibility.append(dict(role=role,state=suffix,
                    method='Raise own private window, retain initial print, yield 100 ms, capture again',
                    raw_control=state.get('raw_file'),
                    focus_preserved=stable))
                if not stable:
                    self.findings.append(dict(kind='capture-changed-focus',window=role,state=suffix))
            self._raise_capture_surface()

        def _capture_held_contract(self, suffix):
            from PIL import Image
            from tools.ui_gallery import window_rect
            for role, window in (('main', self.app.popup), ('quick', self.app.quick_translate_manager.popup)):
                if window is None or not window.winfo_ismapped():
                    continue
                owner = getattr(window, '_ct_deferred_resize', None)
                if owner is None:
                    continue
                shell = (window.winfo_width(), window.winfo_height())
                host = (owner.host.winfo_x(), owner.host.winfo_y(),
                        owner.host.winfo_width(), owner.host.winfo_height())
                self.held_captures.append(dict(role=role,state=owner.state,shell=shell,host=host,
                                              file=f'{role}-{suffix}.png'))
                bridge = owner.native
                marker = (getattr(bridge, 'outline', None) if role == 'main' else
                          self.app.quick_translate_manager._grip_outline)
                if marker is not None:
                    bars = []
                    for index, handle in enumerate(marker.handles):
                        bounds = window_rect(handle)
                        filename = f'{role}-outline-{index}-{suffix}.png'
                        available = capture_native(handle, str(self.recorder.directory/filename), flags=0)
                        bars.append(dict(rect=list(bounds),file=filename,captured=available))
                    passed = marker.visible and len(bars)==4 and all(row['captured'] for row in bars)
                    self.outline_checks.append(dict(role=role,rect=marker.rect,bars=bars,
                        passed=passed,criterion='four native border windows; no filled interior'))
                    if not passed:
                        self.findings.append(dict(kind='outline-unavailable',role=role))
                path = self.recorder.directory/f'{role}-{suffix}.png'
                rectangle = window_rect(int(window.wm_frame(),16))
                origin = window.winfo_rootx()-rectangle[0], window.winfo_rooty()-rectangle[1]
                with Image.open(path) as captured:
                    pixels = captured.convert('RGB')
                rows = self.main_body_observations if role == 'main' else self.quick_body_observations
                shapes = [row['shell' if role == 'main' else 'actual'] for row in rows]
                probes = []
                if shapes:
                    for axis in (0, 1):
                        start = max(host[axis], min(shape[axis] for shape in shapes))
                        end = min(shell[axis], host[axis]+host[axis+2])
                        if end-start > 6:
                            for fraction in (.25,.5,.75,.95):
                                value = round(start+(end-start)*fraction)
                                probes.append((value,host[1]+3) if axis == 0 else (host[0]+3,value))
                for probe in probes:
                    x, y = origin[0]+probe[0], origin[1]+probe[1]
                    black = all(pixels.getpixel((x+dx,y+dy)) == (0,0,0)
                                for dx in (-1,0,1) for dy in (-1,0,1))
                    check = dict(role=role,file=path.name,client_point=probe,passed=not black,
                        criterion='no-solid-black-block-in-reexposed-margin')
                    self.background_checks.append(check)
                    if black:
                        self.findings.append(dict(kind='unpainted-reexposed-content',**check))
                point = None
                if shell[0] > host[0]+host[2]+5:
                    point = ((shell[0]+host[0]+host[2])//2, shell[1]//2)
                elif shell[1] > host[1]+host[3]+5:
                    point = (shell[0]//2, (shell[1]+host[1]+host[3])//2)
                if point is None:
                    continue
                x, y = origin[0]+point[0], origin[1]+point[1]
                actual = pixels.getpixel((x,y))
                expected = tuple(value//257 for value in window.winfo_rgb(window.cget('bg')))
                check = dict(role=role,file=path.name,client_point=point,expected=expected,actual=actual,
                             passed=max(abs(a-b) for a,b in zip(actual,expected))<=2)
                self.background_checks.append(check)
                if not check['passed']:
                    self.findings.append(dict(kind='exposed-shell-background-mismatch',**check))

        def _describe(self):
            import hashlib
            data = super()._describe()
            manager = self.app.quick_translate_manager
            for name, box in (('source', manager.popup_furigana), ('result', manager.popup_text)):
                text = box.get_plain() if box is not None else None
                data['quick_' + name + '_sha256'] = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
                data['quick_' + name + '_ruby_segments'] = sum(
                    bool(segment.ruby) for segment in getattr(box, '_segments', ()))
            controller = getattr(manager, '_linked_controller', None)
            data['quick_link_groups'] = len(controller.groups) if controller else 0
            native = getattr(manager, '_native_sizing', None)
            data['native_sizing_installed'] = bool(getattr(native, 'hwnd', None))
            data['native_sizing_enabled'] = bool(getattr(native, 'enabled', False))
            data['main_size'] = data['main_geometry'][2:]
            data['quick_size'] = data['quick_geometry'][2:] if data['quick_geometry'] else None
            return data

        def _instrument(self, owner, name, label):
            if self.arguments.manual_visible_desktop and name not in (
                    '_native_resize_started', '_native_resize_finished'):
                return  # Physical review keeps the hot path free of per-motion JSON writes.
            original = getattr(owner, name, None)
            if original is None:
                return
            @functools.wraps(original)
            def measured(*args, **kwargs):
                started = time.perf_counter()
                phase = self.phase
                try:
                    return original(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter() - started) * 1000
                    key = phase + ':' + label
                    self.callback_costs[key].append(elapsed)
                    self.recorder.event('tk.callback.cost', phase=phase, operation=label, milliseconds=elapsed)
            setattr(owner, name, measured)

        def _resize(self):
            from src.ui.components.dialog import work_area
            main=self.app.popup
            hold=getattr(self.app._view,'_content_resize',None)
            native=getattr(hold,'native',None)
            if (self.index==0 and main.winfo_ismapped() and native is not None
                    and getattr(native,'outline_mode',False)):
                if not getattr(self,'outline_enter_posted',False):
                    from src.ui import native_resize as N
                    from tools.tk_resize.native_geometry import NativeGeometryProbe
                    self.geometry_probe=NativeGeometryProbe(self.root)
                    self.recorder.update_manifest(native_geometry_probe=self.geometry_probe.identity)
                    native._size_command=True
                    native.user.PostMessageW(native.hwnd,N.WM_ENTERSIZEMOVE,0,0)
                    self.outline_enter_posted=True
                    return
                if not native.running:
                    return
            now = time.perf_counter()
            if self.index == 0:
                self.recorder.scenario(self.workload.name + '-resize')
            if self.previous_resize_tick is not None:
                self.loop_gaps.append((now - self.previous_resize_tick) * 1000)
            self.previous_resize_tick = now
            left, top, right, bottom = work_area()
            fraction = self.index / 79
            width = min(round((1050 - 370 * abs(2 * fraction - 1)) * self.scale), right - left - 40)
            height = min(round((790 - 110 * abs(2 * fraction - 1)) * self.scale), bottom - top - 60)
            main = self.app.popup
            if main.winfo_ismapped():
                hold = getattr(self.app._view, '_content_resize', None)
                native = getattr(hold, 'native', None)
                outline = bool(native and getattr(native,'outline_mode',False))
                if self.index == 0:
                    if hold is not None:
                        hold.begin()
                    if outline:
                        from src.ui import native_resize as N
                        outer = wintypes.RECT()
                        native.user.GetWindowRect(native.hwnd,ctypes.byref(outer))
                        self.main_outline_start = (outer.left,outer.top,
                            outer.right-outer.left-main.winfo_width(),
                            outer.bottom-outer.top-main.winfo_height())
                    self.recorder.resize('main', True, 'scripted-native-geometry')
                self.recorder.size_request('main', width, height, 'scripted-native-geometry')
                if outline:
                    from src.ui import native_resize as N
                    x,y,extra_width,extra_height = self.main_outline_start
                    rectangle = wintypes.RECT(x,y,x+width+extra_width,y+height+extra_height)
                    native.user.SendMessageW(native.hwnd,N.WM_SIZING,8,ctypes.addressof(rectangle))
                    self.geometry_probe.position(native.hwnd,(x,y,width+extra_width,height+extra_height))
                else:
                    main.geometry(f'{width}x{height}')
                self.main_body_observations.append(dict(
                    shell=(main.winfo_width(), main.winfo_height()),
                    host=((self.app._view._content_resize.host.winfo_width(),
                           self.app._view._content_resize.host.winfo_height())
                          if hasattr(self.app._view,'_content_resize') else
                          (main.winfo_width(),main.winfo_height())),
                    source=(self.app.original_text.winfo_width(), self.app.original_text.winfo_height()),
                    result=(self.app.trans_text.winfo_width(), self.app.trans_text.winfo_height())))
            manager = self.app.quick_translate_manager
            if manager.popup is not None and manager.popup.winfo_ismapped():
                if self.quick_start is None:
                    window = manager.popup
                    self.quick_start = (window.winfo_x(), window.winfo_y(),
                                        window.winfo_width(), window.winfo_height())
                    x, y, w, h = self.quick_start
                    manager._grip_press(SimpleNamespace(x_root=x + w, y_root=y + h))
                    self.quick_constraints = dict(start_rect=list(self.quick_start),
                        minimum=[manager._grip_start['min_width'], manager._grip_start['min_height']],
                        work_area=[left, top, right, bottom], scale=self.scale)
                    self.recorder.resize('quick', True, 'scripted-production-grip')
                x, y, w, h = self.quick_start
                target_w = max(manager._grip_start['min_width'], min(width, right - x))
                target_h = max(manager._grip_start['min_height'], min(height, bottom - y))
                self.recorder.size_request('quick', target_w, target_h, 'scripted-production-grip')
                manager._grip_drag(SimpleNamespace(x_root=x + target_w, y_root=y + target_h))
                self.quick_body_observations.append(dict(request=(target_w, target_h),
                    actual=(manager.popup.winfo_width(), manager.popup.winfo_height()),
                    body=(manager.popup_text.winfo_width(), manager.popup_text.winfo_height()),
                    outline=manager._grip_outline is not None))
            self.index += 1
            if self.index in (20,40,60) and not self.arguments.manual_visible_desktop:
                self.capture_waiting = True
                self.recorder.scenario(self.workload.name+'-capture-pause')
                self._settlement_after(80,lambda number=self.index:self._capture_paused(number))
            if self.index >= 80:
                if self.quick_start is not None:
                    started = time.perf_counter()
                    manager._grip_release()
                    self.release_costs.append((time.perf_counter() - started) * 1000)
                    self.recorder.resize('quick', False, 'scripted-production-grip')
                if main.winfo_ismapped():
                    hold = getattr(self.app._view, '_content_resize', None)
                    if hold is not None:
                        if hold.native is not None and getattr(hold.native,'outline_mode',False):
                            from src.ui import native_resize as N
                            hold.native.user.PostMessageW(hold.native.hwnd,N.WM_EXITSIZEMOVE,0,0)
                        else:
                            hold.end()
                    self.recorder.resize('main', False, 'scripted-native-geometry')
                self.recorder.scenario(self.workload.name + '-settle')
                self.release_time = time.perf_counter()
                self.phase = 'layout-settle'
                self.settlement = LayoutSettlement()
                self._settlement_after('idle', self._observe_settlement)
                self._settlement_after(500, self._early_resize_capture)
                self._settlement_after(self.settlement.timeout_ms, self._settlement_timeout)

        def _capture_paused(self, number):
            self._capture(f'during-{number:02}',on_complete=lambda:
                self.recorder.scenario(self.workload.name+'-resize'))

        def _settlement_after(self, delay, function):
            holder = []
            def invoke():
                self.settlement_jobs.discard(holder[0])
                function()
            job = (self.root.after_idle(invoke) if delay == 'idle'
                   else self.root.after(delay, invoke))
            holder.append(job)
            self.settlement_jobs.add(job)

        def _early_resize_capture(self):
            def done():
                self.early_capture_done = True
                self.recorder.event('settlement.early-capture',
                    release_elapsed_ms=(time.perf_counter()-self.release_time)*1000)
            self._capture('resized',on_complete=done)

        def _observe_settlement(self):
            try:
                status = self.settlement.observe(snapshot_layout(self.app),
                    (time.perf_counter() - self.release_time) * 1000)
                if status == 'timeout':
                    self._settlement_timeout()
                    return
                if status == 'settled' and self.early_capture_done:
                    self._capture('settled')
                    self._finish()
                    return
                # Give Tk's own geometry/redraw idles room between observations.
                self._settlement_after(40, lambda: self._settlement_after('idle', self._observe_settlement))
            except Exception as error:
                self.settlement_failure = 'observation-error'
                self.recorder.exception('settlement.error', error)
                self.findings.append(dict(kind='settlement-error', error_type=type(error).__name__))
                self._finish()

        def _settlement_timeout(self):
            self.settlement_failure = 'timeout'
            self.findings.append(dict(kind='settlement-timeout',
                                      seconds=time.perf_counter() - self.release_time))
            self._finish()

        def _tick(self):
            if self.phase == 'layout-settle':
                self.timer = None
                return
            if getattr(self,'capture_waiting',False):
                self.timer = None
                return
            if (not self.arguments.manual_visible_desktop and self.phase == 'visible'
                    and time.perf_counter() >= self.deadline):
                manager = self.app.quick_translate_manager
                if (manager.popup is not None and not (manager._layout
                        and manager._layout.get('revealed') and manager._layout.get('resize_metrics'))):
                    if time.perf_counter() - self.started > 30:
                        self.findings.append(dict(kind='initial-quick-readiness-timeout'))
                        self._finish()
                    else:
                        self.timer = self.root.after(25, self._tick)
                    return
            if (not self.arguments.manual_visible_desktop
                    and not getattr(self.arguments, 'natural_gesture_start', False)):
                manager = self.app.quick_translate_manager
                if (self.phase == 'resize-start' and time.perf_counter() >= self.deadline
                        and manager.popup is not None and not self.gesture_prepared):
                    from src.ui.components.dialog import work_area
                    window = manager.popup
                    manager._grip_press(SimpleNamespace(x_root=window.winfo_x(), y_root=window.winfo_y()))
                    if manager._grip_start is None:
                        raise RuntimeError('Quick was not ready for the common gesture fixture')
                    minimum = manager._grip_start['min_width'], manager._grip_start['min_height']
                    self.gesture_rect = gesture_rectangle(work_area(), self.scale, minimum)
                    self.recorder.scenario(self.workload.name + '-gesture-preparation')
                    manager._user_rect = self.gesture_rect
                    manager._user_size = self.gesture_rect[2:]
                    manager._grip_release()
                    self.gesture_preparation = LayoutSettlement(stable_ms=120, timeout_ms=10000)
                    self.gesture_preparation_started = time.perf_counter()
                    self.phase = 'gesture-preparation'
                if self.phase == 'gesture-preparation':
                    elapsed = (time.perf_counter() - self.gesture_preparation_started) * 1000
                    status = self.gesture_preparation.observe(snapshot_layout(self.app), elapsed)
                    window = manager.popup
                    actual = (window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height())
                    if status == 'timeout':
                        self.findings.append(dict(kind='gesture-preparation-timeout', actual=actual,
                                                  expected=self.gesture_rect))
                        self._finish()
                        return
                    if status != 'settled' or actual != self.gesture_rect:
                        self.timer = self.root.after(40, self._tick)
                        return
                    self.gesture_prepared = True
                    self.recorder.write_json('gesture-preparation.json', dict(rectangle=actual,
                        source='Production grip press/release before the measured trajectory',
                        settlement=self.gesture_preparation.report()))
                    self._capture('gesture-prepared')
                    self.phase = 'resize-start'
            if self.arguments.manual_visible_desktop:
                if self.phase == 'visible' and time.perf_counter() >= self.deadline:
                    self.recorder.scenario('manual-visible-fixture', **self._describe())
                    self.app.popup.protocol('WM_DELETE_WINDOW', self._finish)
                    self._later('manual', self.arguments.manual_seconds)
                    until_close = getattr(self.arguments, 'manual_until_close', False)
                    if until_close:
                        self.deadline = float('inf')
                    print(json.dumps(dict(status='manual-ready', seconds=None if until_close else self.arguments.manual_seconds,
                        duration='until-close' if until_close else 'bounded',
                        effects='Offline fixture; clipboard, keys, registry, network and input injection guarded')),
                        flush=True)
                if self.phase == 'manual':
                    if getattr(self.arguments, 'manual_until_close', False):
                        self.timer = None
                        return
                    if time.perf_counter() >= self.deadline:
                        self._finish()
                    else:
                        self.timer = self.root.after(50, self._tick)
                    return
            if (self.phase == 'cycle-close' and time.perf_counter() >= self.deadline
                    and self.app.quick_translate_manager._layout
                    and self.app.quick_translate_manager._layout.get('revealed')):
                manager = self.app.quick_translate_manager
                window = manager.popup
                x, y, w, h = window.winfo_x(), window.winfo_y(), window.winfo_width(), window.winfo_height()
                manager._grip_press(SimpleNamespace(x_root=x + w, y_root=y + h))
                manager._grip_drag(SimpleNamespace(x_root=x + w + 30, y_root=y + h + 20))
                manager._grip_release()
                self._later('cycle-resized', .12)
            if self.phase == 'cycle-resized':
                if time.perf_counter() < self.deadline:
                    self.timer = self.root.after(16, self._tick)
                    return
                self.phase = 'cycle-close'
            super()._tick()

        def _finish(self):
            if getattr(self,'finished',False):
                return
            if getattr(self,'capture_active',False) or getattr(self,'capture_queue',None):
                self.finish_requested = True
                return
            self.finished = True
            if getattr(self,'geometry_probe',None) is not None:
                self.geometry_probe.close()
                self.geometry_probe=None
            from tools.tk_resize.telemetry import distribution
            for job in self.settlement_jobs:
                self.root.after_cancel(job)
            self.settlement_jobs.clear()
            settlement = self.settlement.report() if self.settlement is not None else dict(status='not-exercised')
            if self.settlement_failure:
                settlement.update(status='failed', failure=self.settlement_failure)
            self.recorder.write_json('settlement.json', settlement)
            if hasattr(self.app._view, '_content_resize'):
                if len({tuple(row['body']) for row in self.quick_body_observations}) > 1:
                    self.findings.append(dict(kind='quick-interior-changed-during-gesture'))
                for name in ('host','source','result'):
                    if len({tuple(row[name]) for row in self.main_body_observations}) > 1:
                        self.findings.append(dict(kind='main-interior-changed-during-gesture',component=name))
            self.recorder.write_json('resize.json', dict(
                callback_cost_ms={name: distribution(values) for name, values in self.callback_costs.items()},
                scripted_event_loop_gap_ms=distribution(self.loop_gaps),
                release_call_ms=distribution(self.release_costs),
                quick_body_observations=self.quick_body_observations,
                main_body_observations=self.main_body_observations,
                held_captures=self.held_captures,background_checks=self.background_checks,
                outline_checks=self.outline_checks,
                interaction=('outline-on-release' if hasattr(self.app._view,'_content_resize')
                    and getattr(self.app._view._content_resize.native,'outline_mode',False)
                    else 'legacy-resize'),
                capture_visibility=self.capture_visibility,
                deferred_content_contract=hasattr(self.app._view, '_content_resize'),
                quick_constraints=self.quick_constraints,
                quick_distinct_window_sizes=len({tuple(row['actual']) for row in self.quick_body_observations}),
                quick_distinct_body_sizes=len({tuple(row['body']) for row in self.quick_body_observations}),
                manual_geometry=list(self.manual_geometry),
                manual_geometry_total=self.manual_geometry_total,
                manual_geometry_dropped=max(0, self.manual_geometry_total - len(self.manual_geometry)),
                settlement=settlement,
                physical_drag='manual-session-awaiting-review' if self.arguments.manual_visible_desktop else 'not-exercised',
                frame_present_ms=None, pointer_to_photon_ms=None,
                measurement='Callback durations, event-loop gaps and geometry are not FPS or visible paint times'))
            super()._finish()
            if self.arguments.manual_visible_desktop:
                path = self.recorder.directory / 'scenario.json'
                data = json.loads(path.read_text(encoding='utf-8'))
                data['method'] = 'Explicit visible offline fixture; gestures and visual observation require separate review'
                self.recorder.write_json('scenario.json', data)
    return ResizeScenario


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--final-validation', action='store_true')
    mode.add_argument('--manual-visible-desktop', action='store_true',
                      help='Explicit controlled session with fake data and guarded effects; no scripted drag')
    parser.add_argument('--manual-seconds', type=int, default=120)
    parser.add_argument('--manual-until-close', action='store_true',
                        help='Keep an explicitly visible manual fixture open until its main window closes')
    parser.add_argument('--natural-gesture-start', action='store_true',
                        help='First-gesture regression without common-rectangle preparation')
    parser.add_argument('--theme', default='light')
    parser.add_argument('--view-state', choices=('normal','options','busy','error','preview',
        'home-history','home-dictionary','settings'), default='normal')
    parser.add_argument('--ui-language', default='en', choices=('en', 'ja'))
    parser.add_argument('--text-scale', type=float, default=1.0, choices=(1.0, 1.5))
    parser.add_argument('--dpi-factor', type=float, default=1.0, choices=(1.0, 1.5))
    parser.add_argument('--idle-seconds', type=float, default=30)
    parser.add_argument('--cycles', type=int, default=50)
    args = parser.parse_args(argv)
    if args.manual_until_close and not args.manual_visible_desktop:
        parser.error('--manual-until-close requires --manual-visible-desktop')
    if args.idle_seconds < 30 or args.cycles < 50:
        parser.error('At least 30 idle seconds and 50 cycles are required')
    if not 30 <= args.manual_seconds <= 600:
        parser.error('A visible fixture session lasts between 30 and 600 seconds')
    source, directory = args.source.resolve(), args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    os.environ['APPDATA'] = str(directory / 'appdata')
    sys.path[:0] = [str(source), str(source / 'tests'), str(HOST_ROOT)]
    import quiet_session
    os.environ.pop(quiet_session.REAL_CLIPBOARD_ENV, None)
    if args.manual_visible_desktop:
        os.environ[quiet_session.ON_SCREEN_ENV] = '1'
    else:
        os.environ.pop(quiet_session.ON_SCREEN_ENV, None)
    quiet_session.install()
    if os.name == 'nt' and not quiet_session.DESKTOP_NAME and not args.manual_visible_desktop:
        raise RuntimeError('Refusing Tk measurements without a private desktop')
    import src.core  # noqa: F401
    from src import app as app_module
    if not Path(app_module.__file__).resolve().is_relative_to(source):
        raise RuntimeError('The production application was imported from the wrong checkout')
    from src.constants import VERSION
    from src.core.nlp_manager import nlp_manager
    from tools.tk_resize.profile import TrialConfig
    from tools.tk_resize.services import TrialServices
    from tools.tk_resize.scenarios import workload, Workload, JAPANESE
    from tools.tk_resize.tk_baseline import TkScenario
    from tools.tk_resize.telemetry import RunRecorder, ProcessProbe
    from tools.tk_resize.snapshot import manifest
    from tools.ui_gallery import install_stubs, capture_tk_callback_errors
    import tkinter
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    install_stubs()
    extra = {
        'quick-long': Workload('quick-long', 'A long source sentence. ' * 1000,
                              source_language='English', target='Japanese', surface='quick'),
        'quick-ruby': Workload('quick-ruby', (JAPANESE + '\n') * 50, surface='quick'),
        'quick-expanded': Workload('quick-expanded', (JAPANESE + '\n') * 50, surface='quick'),
    }
    current = extra[args.scenario] if args.scenario in extra else workload(args.scenario)
    if current.gallery_only:
        parser.error('Use ui_gallery for gallery-only workloads')
    config = TrialConfig(directory / 'profile')
    for key, value in dict(last_run_version=VERSION, welcome_seen=True,
            default_target_language=current.target, theme_mode=args.theme, ui_language=args.ui_language,
            text_scale=args.text_scale, furigana_enabled=current.furigana,
            linked_reading_enabled=current.linked).items():
        config.set(key, value)
    recorder = RunRecorder(directory, source, scenario='tk-resize-' + current.name)
    recorder.update_manifest(source=manifest(source), harness_source=manifest(HOST_ROOT), renderer='production-tk',
        capture_method='PrintWindow flags=0; native non-client rendering is not a DWM presentation capture',
        held_capture_method='Pause requests, raise each private window, retain first-print control, yield 100 ms and capture; no interior layout or release. Native presented frames unmeasured',
        desktop='visible-explicit-fixture' if args.manual_visible_desktop else 'private', graphics_api='Tk/GDI',
        gesture_start=('human-input' if args.manual_visible_desktop else
                       'natural-first-gesture' if args.natural_gesture_start else 'prepared-user-rectangle'),
        measurement_limits=['No frame-present notification: callback gaps are not FPS',
            'No physical input, clipboard or paid API effects',
            'Fresh process is not a proven cold OS cache',
            'Background machine load and OS caches are not controlled by this harness',
            'Native system sizing and visible compositor response require a separate real gesture'])
    services = TrialServices(config, recorder)
    services.seed_history(current.history_count)
    old_config, old_service = app_module.Config, app_module.TranslationService
    old_nlp, old_load = nlp_manager.config, tkinter.Tk._loadtk
    nlp_manager.set_config(config)
    def load(widget):
        old_load(widget)
        widget.tk.call('tk', 'scaling', float(widget.tk.call('tk', 'scaling')) * args.dpi_factor)
    tkinter.Tk._loadtk = load
    app_module.Config, app_module.TranslationService = lambda: config, lambda _config: services.translation
    app, sampler, controller = None, None, None
    stop = threading.Event()
    code = 1
    try:
        show_started = time.perf_counter()
        app = app_module.TranslatorApp()
        app.theme_manager.stop_watching()
        app.show_main_window()
        ready_epoch, ready_perf = time.time(), time.perf_counter()
        birth = process_start_epoch()
        from src.ui import components
        from src.ui.components.dialog import work_area
        recorder.update_manifest(tcl_patchlevel=str(app.root.tk.call('info', 'patchlevel')),
            tk_scaling=float(app.root.tk.call('tk', 'scaling')), ui_scale=components.get().scale,
            theme=args.theme, ui_language=args.ui_language, text_scale=args.text_scale,
            dpi_factor=args.dpi_factor, dpi_method='Tk scaling multiplier; not a physical mixed-DPI transition',
            startup=dict(process_creation_to_main_show_return_ms=(ready_epoch - birth) * 1000 if birth is not None else None,
                         python_entry_to_main_show_return_ms=(ready_perf - PYTHON_ENTRY) * 1000,
                         app_construction_and_show_ms=(ready_perf - show_started) * 1000,
                         method='Kernel creation/show return; not cold OS cache or a visible frame notification'),
            memory_method='Sampled working set/private bytes plus kernel lifetime peaks; includes instrumentation and captures',
            private_peak_limit='Private samples begin after main UI construction; lifetime commit peak is reported separately',
            work_area=work_area())
        app.root.report_callback_exception = lambda _kind, error, tb: recorder.exception(
            'tk.callback.error', error.with_traceback(tb))
        def sample():
            probe = ProcessProbe()
            try:
                while not stop.wait(.5):
                    values = probe.sample()
                    if probe.psapi:
                        memory = MemoryCounters()
                        memory.size = ctypes.sizeof(memory)
                        if probe.psapi.GetProcessMemoryInfo(probe.process, ctypes.byref(memory), memory.size):
                            values.update(lifetime_peak_working_set_bytes=memory.peak_working_set,
                                          lifetime_peak_commit_bytes=memory.peak_pagefile)
                        else:
                            values['memory_peak_error'] = ctypes.get_last_error()
                    recorder.sample(values)
            except Exception as error:
                recorder.exception('resource-probe.error', error)
            finally:
                probe.close()
        sampler = threading.Thread(target=sample, name='TkResizeResourceProbe', daemon=True)
        sampler.start()
        controller = scenario_class(TkScenario)(app, services, recorder, current, args)
        capture_tk_callback_errors(app.root, controller.callback_errors)
        controller.start()
        print(json.dumps(dict(status='ready', output=str(directory), source=str(source))), flush=True)
        app.root.mainloop()
        code = int(bool(controller.findings or controller.callback_errors or quiet_session.BLOCKED or recorder.errors))
    except Exception as error:
        recorder.exception('tk-resize.run.error', error)
    finally:
        stop.set()
        if sampler:
            sampler.join(2)
        if app:
            app.quick_translate_manager.close()
            app.root.destroy()
        tkinter.Tk._loadtk = old_load
        app_module.Config, app_module.TranslationService = old_config, old_service
        nlp_manager.set_config(old_nlp)
        recorder.update_manifest(fixture_save_retries=config.save_retry_stats)
        recorder.close(dict(automated_execution=dict(status='pass' if code == 0 else 'fail'),
                            physical_drag=dict(status='manual-session-awaiting-review'
                                if args.manual_visible_desktop else 'not-exercised')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

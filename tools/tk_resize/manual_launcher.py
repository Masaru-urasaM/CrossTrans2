"""Double-click entry for a persistent, isolated production-Tk resize fixture."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import traceback
import time
import weakref
from collections import deque

ROOT = Path(__file__).resolve().parents[2]


def bootstrap():
    sys.dont_write_bytecode = True
    runtime = Path(sys.executable).resolve().parent
    dependencies = [runtime.parent / 'python3149-packages', ROOT]
    paths = [str(path) for path in dependencies if path.is_dir()]
    sys.path[:0] = paths
    os.environ['PYTHONPATH'] = os.pathsep.join(paths + [os.environ.get('PYTHONPATH', '')])
    os.environ.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    with open(sys.executable, 'rb') as stream:
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        signature, machine = struct.unpack('<4sH', stream.read(6))
    if signature != b'PE\0\0':
        raise RuntimeError('A Windows Python runtime is required')
    os.environ['PROCESSOR_ARCHITECTURE'] = {0x8664: 'AMD64', 0x14c: 'x86', 0xaa64: 'ARM64'}[machine]


def check_runtime():
    if sys.version_info[:3] != (3, 14, 9):
        raise RuntimeError(f'Python 3.14.9 is required; found {sys.version.split()[0]}')
    import tkinter
    interpreter = tkinter.Tcl()
    tcl = str(interpreter.call('info', 'patchlevel'))
    if tcl != '9.0.4':
        raise RuntimeError(f'Tcl 9.0.4 is required; found {tcl}')
    for package in ('ttkbootstrap', 'tkinterdnd2', 'PIL', 'requests'):
        if importlib.util.find_spec(package) is None:
            raise RuntimeError(f'Missing dependency: {package}')
    return dict(source=str(ROOT), python=sys.executable, version=sys.version.split()[0], tcl=tcl)


class LaunchChannel:
    """One fixture per worktree; a later double-click raises that same fixture."""
    def __init__(self, root=ROOT, *, event_only=False):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel.SetEvent.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        key = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:20]
        name = 'Local\\CrossTrans2-ResizeTest-' + key
        self.mutex, self.event, self.existing = None, None, False
        try:
            if not event_only:
                ctypes.set_last_error(0)
                self.mutex = self.kernel.CreateMutexW(None, False, name + '-mutex')
                if not self.mutex:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.existing = ctypes.get_last_error() == 183
            self.event = self.kernel.CreateEventW(None, False, False, name + '-raise')
            if not self.event:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def request_raise(self):
        if not self.kernel.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def requested(self):
        status = self.kernel.WaitForSingleObject(self.event, 0)
        if status not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return status == 0

    def close(self):
        for name in ('event', 'mutex'):
            handle = getattr(self, name, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)


def run_fixture(output, *, private_check=False):
    from tools.tk_resize import manual_fixture
    original_factory = manual_fixture.scenario_factory
    label = ROOT.parent.name if ROOT.parent.parent.name == 'worktrees' else ROOT.name
    def scenario_factory(original_scenario, private_check=False):
        factory = original_factory(original_scenario, private_check)
        def identified(base):
            prepared = factory(base)
            class PersistentFixture(prepared):
                def __init__(self, *args):
                    super().__init__(*args)
                    self.launch_channel = None if private_check else LaunchChannel(event_only=True)
                    self.raise_job = None
                    self.launcher_closed = False
                    self.native_trace = None
                    self.trace_error = None
                    self.trace_partial = None
                    self.deferred_geometry = deque(maxlen=4000)
                    self.deferred_geometry_total = 0
                    self.deferred_bound = weakref.WeakSet()
                    manager = self.app.quick_translate_manager
                    manager._end_front = lambda _popup: None
                    original_show = manager.show
                    def show(*args, **kwargs):
                        kwargs.setdefault('model_info', 'OUTLINE RESIZE TEST ' + label)
                        result = original_show(*args, **kwargs)
                        if self.native_trace is not None:
                            try:
                                self.native_trace.attach_app(self.app)
                            except Exception as error:
                                self._trace_failed(error)
                        return result
                    manager.show = show

                def _prepare_windows(self):
                    ready = super()._prepare_windows()
                    if ready:
                        for role, window in (('main',self.app.popup),('quick',self.app.quick_translate_manager.popup)):
                            if window in self.deferred_bound:
                                continue
                            self.deferred_bound.add(window)
                            def record(event, surface=role, target=window):
                                if event.widget is not target:
                                    return
                                owner = getattr(target,'_ct_deferred_resize',None)
                                if owner is not None and owner.state!='CLOSED':
                                    self.deferred_geometry_total += 1
                                    self.deferred_geometry.append(dict(role=surface,state=owner.state,
                                        elapsed=time.perf_counter()-self.started,
                                        shell=(target.winfo_width(),target.winfo_height()),
                                        host=(owner.host.winfo_width(),owner.host.winfo_height()),
                                        commit_count=owner.commit_count))
                            window.bind('<Configure>',record,add='+')
                        if self.native_trace is None and self.trace_error is None:
                            try:
                                from tools.tk_resize.native_trace import NativeResizeTrace
                                self.native_trace = NativeResizeTrace()
                                self.native_trace.attach_app(self.app)
                            except Exception as error:
                                self._trace_failed(error)
                        identity = self.recorder.manifest.get('source', {}).get('sha256', '')[:8]
                        self.app.popup.title(f'CrossTrans2 - OUTLINE RESIZE TEST [{label}] {identity}')
                        self.app.quick_translate_manager.popup.title(f'Quick Translate - OUTLINE RESIZE TEST [{label}] {identity}')
                        self.app.popup.attributes('-topmost', True)
                        self.app.quick_translate_manager.popup.attributes('-topmost', True)
                        if self.launch_channel and self.raise_job is None:
                            self.raise_job = self.root.after(250, self._check_raise)
                    return ready

                def _trace_failed(self, error):
                    self.trace_error = dict(error_type=type(error).__name__, message=str(error))
                    if self.native_trace is not None:
                        try:
                            self.trace_partial = self.native_trace.finish()
                            self.trace_partial['detach_failures'] = self.native_trace.detach_failures
                        except Exception:
                            self.native_trace.close()
                        self.native_trace = None

                def _check_raise(self):
                    self.raise_job = None
                    if self.launcher_closed:
                        return
                    if self.launch_channel.requested():
                        main = self.app.popup
                        main.deiconify()
                        main.lift()
                        main.focus_force()
                        manager = self.app.quick_translate_manager
                        if manager.popup is None:
                            self._open_quick()
                        else:
                            manager.popup.deiconify()
                            manager.popup.lift()
                    self.raise_job = self.root.after(250, self._check_raise)

                def _finish(self):
                    if self.launcher_closed:
                        return
                    self.launcher_closed = True
                    trace = self.trace_partial or dict(available=False)
                    if self.native_trace is not None:
                        try:
                            trace = self.native_trace.finish()
                            trace['detach_failures'] = self.native_trace.detach_failures
                        except Exception as error:
                            self._trace_failed(error)
                            trace = dict(available=False, error=self.trace_error)
                    trace['error'] = self.trace_error
                    trace['fixture_started_perf_counter'] = self.started
                    self.recorder.write_json('native-trace.json', trace)
                    self.recorder.write_json('deferred-content.json',dict(
                        scope='Manual real-window geometry and controller state; not presented frames',
                        samples=list(self.deferred_geometry),capacity=4000,
                        total=self.deferred_geometry_total,
                        dropped=max(0,self.deferred_geometry_total-len(self.deferred_geometry)),
                        behavior='Outline only; real window unchanged until release'))
                    if self.raise_job is not None:
                        self.root.after_cancel(self.raise_job)
                        self.raise_job = None
                    if self.launch_channel:
                        self.launch_channel.close()
                    super()._finish()
            return PersistentFixture
        return identified
    manual_fixture.scenario_factory = scenario_factory
    arguments = ['--source', str(ROOT), '--output', str(output), '--scenario', 'two-windows', '--theme', 'system']
    if private_check:
        arguments += ['--private-check', '--final-validation']
    else:
        arguments += ['--manual-visible-desktop', '--manual-until-close']
    return manual_fixture.main(arguments)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--private-check', action='store_true')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    channel, folder = None, None
    try:
        bootstrap()
        runtime = check_runtime()
        if args.check:
            print(json.dumps(runtime))
            return 0
        if args.child:
            if args.output is None:
                raise ValueError('Child output path is required')
            return run_fixture(args.output, private_check=args.private_check)
        if not args.private_check:
            channel = LaunchChannel()
            if channel.existing:
                channel.request_raise()
                return 0
        from tools.tk_resize.process import run_owned
        from tools.tk_resize.snapshot import manifest
        identifier = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
        folder = ROOT / 'logs/tk_resize/manual-launches' / identifier
        folder.mkdir(parents=True)
        record = dict(status='running', owner_pid=os.getpid(), source=manifest(ROOT), runtime=runtime,
            started_utc=datetime.now(timezone.utc).isoformat(), until_close=not args.private_check,
            effects='Fake data; guarded clipboard/hotkeys/registry/input/network',
            visibility='Fixture-only topmost; not evidence of normal product Z-order')
        def save():
            (folder / 'run.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        def started(pid):
            record['pid'] = pid
            save()
        save()
        command = [str(Path(sys.executable).with_name('python.exe')), str(Path(__file__).resolve()),
                   '--child', '--output', str(folder / 'worker')]
        if args.private_check:
            command.append('--private-check')
        with (folder / 'fixture.log').open('w', encoding='utf-8') as stream:
            record.update(run_owned(command, cwd=ROOT, env=dict(os.environ), stream=stream,
                timeout=90 if args.private_check else None, started=started))
        record.update(status='finished', source_unchanged=manifest(ROOT) == record['source'])
        save()
        if record['exit_code']:
            raise RuntimeError(f'The resize test stopped. See {folder / "fixture.log"}')
        return 0
    except Exception as error:
        if folder is not None and 'record' in locals():
            record.update(status='failed', error_type=type(error).__name__)
            save()
        if args.check or args.private_check or args.child:
            traceback.print_exc()
        else:
            message = str(error) + (f'\nLogs: {folder}' if folder else '')
            ctypes.windll.user32.MessageBoxW(None, message, 'CrossTrans2 resize test', 0x10)
        return 1
    finally:
        if channel:
            channel.close()


if __name__ == '__main__':
    raise SystemExit(main())

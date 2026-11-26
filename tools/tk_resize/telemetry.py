"""Bounded telemetry with explicit unavailable measurements and sanitized tracebacks."""
from collections import defaultdict, deque
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import threading
import time
import traceback


def distribution(values):
    samples = sorted(value for value in values if math.isfinite(value))
    if not samples:
        return dict(count=0, p50=None, p95=None, p99=None, maximum=None)
    def quantile(fraction):
        position = (len(samples) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(samples) - 1)
        return samples[lower] + (samples[upper] - samples[lower]) * (position - lower)
    return dict(count=len(samples), p50=quantile(.5), p95=quantile(.95), p99=quantile(.99),
                maximum=samples[-1])


def source_manifest(root):
    root = Path(root).resolve()
    files = [root / name for name in ('config.py', 'main.py', 'src/app.py', 'TODO.md', 'ROADMAP.md',
                                      'tools/ui_gallery.py', 'tools/ui_audit.py', 'tools/check_test_counts.py')]
    for directory in ('src/core', 'src/ui', 'src/utils', 'tools/tk_resize', 'tests', 'docs'):
        files.extend(path for path in (root / directory).rglob('*')
                     if path.suffix in ('.py', '.qml', '.json', '.txt', '.md') and '__pycache__' not in path.parts
                     and path != root / 'docs/todo_implementation_audit.json')
    hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(set(files)) if path.is_file()}
    combined = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return dict(sha256=combined, files=hashes)


class RunRecorder:
    def __init__(self, directory, root, *, scenario='interactive-fixture'):
        self.directory, self.root = Path(directory).resolve(), Path(root).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started = time.perf_counter()
        self.lock = threading.RLock()
        self.private = deque(maxlen=64)
        self.errors = 0
        self.expected_errors = 0
        self.events = (self.directory / 'events.jsonl').open('a', encoding='utf-8', buffering=1)
        self.resources = deque(maxlen=14400)
        self.frames = defaultdict(lambda: deque(maxlen=120000))
        self.last_frame = {}
        self.frame_dropped = defaultdict(int)
        self.resizing = set()
        self.resize_started = {}
        self.resize_durations = defaultdict(list)
        self.resize_methods = {}
        self.release_times = {}
        self.settle_notifications = defaultdict(lambda: deque(maxlen=10000))
        self.geometry_requests = defaultdict(lambda: deque(maxlen=32))
        self.geometry_latencies = defaultdict(lambda: deque(maxlen=120000))
        self.request_counts = defaultdict(int)
        self.open_requests = {}
        self.open_notifications = defaultdict(lambda: deque(maxlen=10000))
        self.open_calls = defaultdict(lambda: deque(maxlen=10000))
        self.current_scenario = 'startup'
        self.scenario_started = self.started
        self.manifest = dict(schema=1, started_utc=datetime.now(timezone.utc).isoformat(),
            source=source_manifest(root), python=platform.python_version(), os=platform.platform(),
            machine=platform.machine(), logical_cpus=os.cpu_count(), scenario=scenario,
            transport='offline-fixture', units=dict(memory='bytes', time='milliseconds'),
            measurement_limits=['frameSwapped measures Qt notification cadence, not pointer-to-photon latency',
                'PDH GPU counters require corroboration; some Windows versions report inaccurate values',
                'private desktop or offscreen runs do not establish visible compositor smoothness'])
        self.write_json('manifest.json', self.manifest)
        self.write_json('summary.json', dict(status='implementation-pending', gates={}, errors=0))

    def protect(self, *values):
        with self.lock:
            for value in values:
                if value and str(value) not in self.private:
                    self.private.append(str(value))
            while len(self.private) > 1 and sum(len(value) for value in self.private) > 1024 * 1024:
                self.private.popleft()

    def sanitize(self, value):
        if isinstance(value, dict):
            return {key: '<redacted>' if re.search(r'password|api.?key|credential|token', str(key), re.I)
                    else self.sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        if not isinstance(value, str):
            return value
        for private in sorted(self.private, key=len, reverse=True):
            value = value.replace(private, '<private>')
        value = re.sub(r'(?i)(?:sk-|gsk_|AIza)[\w-]{12,}', '<credential>', value)
        value = re.sub(r'(?i)[a-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\s"\']+', '<user-path>', value)
        return value

    def event(self, name, **fields):
        with self.lock:
            if self.events.closed:
                return
            item = self.sanitize(dict(event=name, elapsed_ms=(time.perf_counter() - self.started) * 1000,
                                      thread=threading.current_thread().name, **fields))
            self.events.write(json.dumps(item, ensure_ascii=False) + '\n')

    def exception(self, name, error, *, expected=False, **context):
        with self.lock:
            if expected:
                self.expected_errors += 1
            else:
                self.errors += 1
            frames = []
            for frame in traceback.extract_tb(error.__traceback__):
                path = Path(frame.filename)
                try:
                    filename = path.resolve().relative_to(self.root).as_posix()
                except ValueError:
                    filename = path.name
                frames.append(dict(file=filename, line=frame.lineno, function=frame.name, code=frame.line))
            status = re.search(r'\b([45]\d\d)\b', str(error))
            self.event(name, error_type=type(error).__name__,
                       message='Exception message omitted; it may contain input. See reason, codes and stack locations.',
                       errno=getattr(error, 'errno', None), winerror=getattr(error, 'winerror', None),
                       http_status=int(status.group(1)) if status else None,
                       traceback=frames, expected=expected, context=context)

    def write_json(self, name, data):
        with self.lock:
            path = self.directory / name
            temporary = path.with_suffix('.pending')
            temporary.write_text(json.dumps(self.sanitize(data), ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)

    def update_manifest(self, **fields):
        self.manifest.update(fields)
        self.write_json('manifest.json', self.manifest)

    def scenario(self, scenario, **workload):
        with self.lock:
            self.current_scenario = scenario
            self.scenario_started = time.perf_counter()
            self.event('scenario.begin', scenario=scenario, workload=workload)

    def resize(self, window, active, method='native-system-sizing'):
        with self.lock:
            if active and window not in self.resizing:
                self.resizing.add(window)
                self.resize_started[window] = time.perf_counter()
                self.resize_methods[window] = method
                self.last_frame.pop(window, None)
                self.event('resize.begin', window=window, method=method, scenario=self.current_scenario)
            elif not active and window in self.resizing:
                self.resizing.remove(window)
                duration = (time.perf_counter() - self.resize_started.pop(window)) * 1000
                self.resize_durations[window].append(duration)
                self.last_frame.pop(window, None)
                self.release_times[window] = time.perf_counter()
                self.event('resize.end', window=window, duration_ms=duration,
                           method=self.resize_methods.pop(window, method), scenario=self.current_scenario)

    def size_request(self, window, width, height, method):
        with self.lock:
            self.request_counts[(window, method)] += 1
            self.geometry_requests[window].append((int(width), int(height), time.perf_counter(), method))

    def geometry(self, window, width, height):
        with self.lock:
            requests = self.geometry_requests[window]
            for index in range(len(requests) - 1, -1, -1):
                expected_width, expected_height, started, method = requests[index]
                if (expected_width, expected_height) == (int(width), int(height)):
                    self.geometry_latencies[(window, method)].append((time.perf_counter() - started) * 1000)
                    for _ in range(index + 1):
                        requests.popleft()
                    break

    def opening(self, window):
        with self.lock:
            self.open_requests[window] = time.perf_counter()

    def opened(self, window, method):
        with self.lock:
            started = self.open_requests.pop(window, None)
            if started is not None:
                self.open_notifications[(window, method)].append((time.perf_counter() - started) * 1000)

    def open_call(self, window, seconds):
        with self.lock:
            self.open_calls[window].append(seconds * 1000)

    def frame(self, window):
        now = time.perf_counter()
        with self.lock:
            self.opened(window, 'qt-frameSwapped-notification')
            released = self.release_times.pop(window, None)
            if released is not None:
                self.settle_notifications[window].append((now - released) * 1000)
            if window not in self.resizing:
                return
            previous = self.last_frame.get(window)
            self.last_frame[window] = now
            if previous is not None:
                if len(self.frames[window]) == self.frames[window].maxlen:
                    self.frame_dropped[window] += 1
                self.frames[window].append((now - previous) * 1000)

    def sample(self, data):
        with self.lock:
            now = time.perf_counter()
            full = now - data.get('sample_interval_seconds', 0) >= self.scenario_started
            self.resources.append(dict(elapsed_ms=(now - self.started) * 1000,
                scenario=self.current_scenario, sample_fully_within_scenario=full, **data))

    def flush(self):
        with self.lock:
            metrics = dict(resource_samples=list(self.resources),
                frame_notifications_ms={key: distribution(values) for key, values in self.frames.items()},
                overwritten_frame_samples=dict(self.frame_dropped),
                resize_duration_ms={key: distribution(values) for key, values in self.resize_durations.items()},
                geometry_callback_latency_ms={window + ':' + method: distribution(values)
                    for (window, method), values in self.geometry_latencies.items()},
                geometry_requests={window + ':' + method: count for (window, method), count in self.request_counts.items()},
                first_swap_notification_after_release_ms={key: distribution(values) for key, values in self.settle_notifications.items()},
                show_call_ms={key: distribution(values) for key, values in self.open_calls.items()},
                opening_notification_ms={window + ':' + method: distribution(values)
                    for (window, method), values in self.open_notifications.items()})
            self.write_json('metrics.json', metrics)
            return metrics

    def close(self, gates=None):
        with self.lock:
            if self.events.closed:
                return
            self.flush()
            gates = gates or {}
            complete = bool(gates) and all(value.get('status') == 'pass' for value in gates.values())
            self.write_json('summary.json', dict(status='pass' if complete and not self.errors else 'incomplete',
                                                gates=gates, errors=self.errors, expected_errors=self.expected_errors))
            self.events.close()


class _CounterValue(ctypes.Union):
    _fields_ = [('longValue', wintypes.LONG), ('doubleValue', ctypes.c_double),
                ('largeValue', ctypes.c_longlong), ('stringValue', wintypes.LPWSTR)]


class _FormattedValue(ctypes.Structure):
    _fields_ = [('status', wintypes.DWORD), ('value', _CounterValue)]


class _CounterItem(ctypes.Structure):
    _fields_ = [('name', wintypes.LPWSTR), ('formatted', _FormattedValue)]


class GPUProcessCounters:
    """Read-only PDH estimates; a missing instance is unavailable, never zero usage."""
    def __init__(self):
        self.query, self.counters, self.error = wintypes.HANDLE(), {}, None
        self.pdh = None
        if os.name != 'nt':
            self.error = 'Windows PDH is unavailable on this platform'
            return
        try:
            pdh = self.pdh = ctypes.WinDLL('pdh', use_last_error=True)
            pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
            pdh.PdhOpenQueryW.restype = wintypes.LONG
            pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                                ctypes.POINTER(wintypes.HANDLE)]
            pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
            pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
            pdh.PdhCollectQueryData.restype = wintypes.LONG
            pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
            pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
            pdh.PdhCloseQuery.restype = wintypes.LONG
            status = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
            if status:
                raise OSError(f'PdhOpenQueryW status 0x{status & 0xffffffff:08x}')
            for key, name in [('dedicated', 'Dedicated Usage'), ('shared', 'Shared Usage')]:
                counter = wintypes.HANDLE()
                path = f'\\GPU Process Memory(pid_{os.getpid()}_*)\\{name}'
                status = pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter))
                if status:
                    raise OSError(f'PdhAddEnglishCounterW status 0x{status & 0xffffffff:08x}')
                self.counters[key] = counter
        except (OSError, AttributeError) as error:
            self.error = str(error)
            self.close()

    def sample(self):
        result = dict(dedicated_bytes=None, shared_bytes=None, source='Windows PDH estimate',
                      corroborated=False, error=self.error)
        if not self.query:
            return result
        status = self.pdh.PdhCollectQueryData(self.query)
        if status:
            result['error'] = f'PdhCollectQueryData status 0x{status & 0xffffffff:08x}'
            return result
        for key, counter in self.counters.items():
            size, count = wintypes.DWORD(), wintypes.DWORD()
            status = self.pdh.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size),
                                                          ctypes.byref(count), None)
            if status & 0xffffffff != 0x800007d2 or not size.value:
                result['error'] = f'GPU instance unavailable (0x{status & 0xffffffff:08x})'
                continue
            buffer = ctypes.create_string_buffer(size.value)
            status = self.pdh.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size),
                                                          ctypes.byref(count), buffer)
            if status:
                result['error'] = f'GPU counter read status 0x{status & 0xffffffff:08x}'
                continue
            items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))
            values = [items[index].formatted.value.largeValue for index in range(count.value)
                      if items[index].formatted.status in (0, 1)]
            if values:
                result[key + '_bytes'] = sum(values)
        return result

    def close(self):
        if self.pdh and self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = wintypes.HANDLE()


class ProcessProbe:
    def __init__(self):
        self.gpu = GPUProcessCounters()
        self.last_wall, self.last_cpu = time.perf_counter(), time.process_time()
        self.kernel = self.psapi = self.user = None
        if os.name == 'nt':
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.GetCurrentProcess.argtypes = []
            self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
            self.process = self.kernel.GetCurrentProcess()
            self.psapi = ctypes.WinDLL('psapi', use_last_error=True)
            self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            self.user = ctypes.WinDLL('user32', use_last_error=True)
            self.user.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.user.GetGuiResources.restype = wintypes.DWORD

    def sample(self):
        now, cpu = time.perf_counter(), time.process_time()
        wall, used = now - self.last_wall, cpu - self.last_cpu
        self.last_wall, self.last_cpu = now, cpu
        single = used / wall * 100 if wall > 0 else None
        result = dict(cpu_percent_one_core=single,
            cpu_percent_machine=single / (os.cpu_count() or 1) if single is not None else None,
            sample_interval_seconds=wall, working_set_bytes=None, private_bytes=None,
            user_handles=None, gdi_handles=None, gpu=self.gpu.sample())
        if self.psapi:
            class Memory(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('page_faults', wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set', 'peak_paged',
                    'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
            memory = Memory()
            memory.cb = ctypes.sizeof(memory)
            if self.psapi.GetProcessMemoryInfo(self.process, ctypes.byref(memory), memory.cb):
                result.update(working_set_bytes=memory.working_set, private_bytes=memory.private)
            else:
                result['memory_error'] = ctypes.get_last_error()
            for key, flag in (('user_handles', 1), ('gdi_handles', 0)):
                ctypes.set_last_error(0)
                value = self.user.GetGuiResources(self.process, flag)
                error = ctypes.get_last_error()
                if value or not error:
                    result[key] = value
                else:
                    result[key + '_error'] = error
        return result

    def close(self):
        self.gpu.close()

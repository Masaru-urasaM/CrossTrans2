"""Bounded native telemetry for a fake fixture; never loaded by production UI."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'logs/tk_resize/native-trace-runtime'
UNSIGNED = ('sequence', 'begin', 'end', 'hwnd', 'surface', 'role', 'message', 'flags', 'active')
SIGNED = ('x', 'y', 'width', 'height', 'proposed_x', 'proposed_y',
          'proposed_width', 'proposed_height', 'pointer_x', 'pointer_y')
MESSAGES = {0x0231: 'enter', 0x0232: 'exit', 0x0214: 'sizing', 0x0216: 'moving',
            0x0005: 'size', 0x0047: 'position', 0x000f: 'paint'}


class TraceRow(ctypes.Structure):
    _fields_ = [(key, ctypes.c_uint64) for key in UNSIGNED] + [
        (key, ctypes.c_int64) for key in SIGNED]


def verified_library(runtime=RUNTIME):
    runtime = Path(runtime)
    library = runtime / 'native_trace.dll'
    metadata = json.loads((runtime / 'build.json').read_text(encoding='utf-8'))
    if metadata.get('abi') != 1:
        raise ValueError('Native trace ABI is unsupported')
    if hashlib.sha256(Path(__file__).with_suffix('.c').read_bytes()).hexdigest() != metadata['source_sha256']:
        raise ValueError('Native trace source differs from its build')
    if hashlib.sha256(library.read_bytes()).hexdigest() != metadata['dll_sha256']:
        raise ValueError('Native trace library differs from its build')
    return library.resolve(), metadata


class NativeResizeTrace:
    def __init__(self, runtime=RUNTIME):
        self.thread = threading.get_ident()
        self.library_path, self.build = verified_library(runtime)
        self.api = ctypes.CDLL(str(self.library_path))
        for name in ('trace_version', 'trace_row_size', 'trace_count', 'trace_close', 'trace_open'):
            getattr(self.api, name).restype = ctypes.c_uint
        for name in ('trace_frequency', 'trace_total', 'trace_clock'):
            getattr(self.api, name).restype = ctypes.c_uint64
        self.api.trace_attach.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
        self.api.trace_attach.restype = ctypes.c_int
        self.api.trace_read.argtypes = [ctypes.c_uint, ctypes.POINTER(TraceRow)]
        self.api.trace_read.restype = ctypes.c_int
        self.api.trace_pause.restype = None
        if self.api.trace_version() != 1 or self.api.trace_row_size() != ctypes.sizeof(TraceRow):
            raise ValueError('Native trace structure layout is incompatible')
        if not self.api.trace_open():
            raise RuntimeError('A native fixture trace is already active')
        before = time.perf_counter()
        native = self.api.trace_clock()
        after = time.perf_counter()
        self.clock_anchor = dict(native_ticks=native, python_perf_counter=(before + after) / 2,
                                 bracket_seconds=after - before)
        self.windows = {}
        self.closed = False

    def _owner(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError('Native fixture telemetry belongs to the Tk UI thread')

    def attach_app(self, app):
        self._owner()
        if self.closed:
            return
        manager = app.quick_translate_manager
        targets = [(0, 1, app.popup, True), (0, 2, app.original_text, False),
                   (0, 2, app.trans_text, False), (0, 3, app._view.body_area.canvas, False),
                   (1, 1, manager.popup, True), (1, 2, manager.popup_furigana, False),
                   (1, 2, manager.popup_text, False)]
        for surface, role, widget, wrapper in targets:
            if widget is None or not widget.winfo_exists():
                continue
            hwnd = int(widget.wm_frame(), 16) if wrapper else int(widget.winfo_id())
            descriptor = dict(surface=('main', 'quick')[surface],
                role={1:'wrapper', 2:'text', 3:'viewport'}[role], widget=str(widget))
            if self.windows.get(hwnd) == descriptor:
                continue
            if not self.api.trace_attach(hwnd, surface, role):
                raise RuntimeError('Could not attach fixture native telemetry')
            self.windows[hwnd] = descriptor

    def finish(self):
        self._owner()
        if self.closed:
            raise RuntimeError('Native fixture telemetry already closed')
        self.api.trace_pause()
        try:
            frequency = self.api.trace_frequency()
            if frequency <= 0:
                raise ValueError('Native trace clock is unavailable')
            count, total, rows = self.api.trace_count(), self.api.trace_total(), []
            for index in range(count):
                value = TraceRow()
                if not self.api.trace_read(index, ctypes.byref(value)):
                    raise RuntimeError('Could not read fixture native telemetry')
                row = {key:getattr(value, key) for key in UNSIGNED + SIGNED}
                row['message_name'] = MESSAGES.get(row['message'], 'unknown')
                row['forward_ms'] = (row['end'] - row['begin']) * 1000 / frequency
                rows.append(row)
            return dict(available=True, abi=1, frequency=frequency, total=total,
                dropped=max(0, total - count), windows=self.windows, events=rows,
                clock_anchor=self.clock_anchor,
                dll_sha256=self.build['dll_sha256'], source_sha256=self.build['source_sha256'],
                limits=['Native events/forwarded calls are not displayed frames or pointer-to-photon latency',
                    'Nested forwarded-call durations overlap and must not be summed',
                    'Only fixture wrapper, text and viewport handles are observed',
                    'Pointer coordinates are sampled only during fixture sizing/moving messages',
                    'The bounded ring retains the newest 8192 events; dropped events are reported'])
        finally:
            self.closed = True
            self.detach_failures = self.api.trace_close()

    def close(self):
        self._owner()
        if not self.closed:
            self.closed = True
            self.detach_failures = self.api.trace_close()

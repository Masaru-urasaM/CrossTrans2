"""Fixture telemetry preserves native boundaries and cannot turn into input injection."""
import ctypes
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.tk_resize import native_trace as N
from tools.tk_resize.snapshot import manifest


def metadata(tmp_path):
    dll = tmp_path / 'native_trace.dll'
    dll.write_bytes(b'fixture native library')
    data = dict(abi=1, source_sha256=hashlib.sha256(Path(N.__file__).with_suffix('.c').read_bytes()).hexdigest(),
        dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest())
    (tmp_path / 'build.json').write_text(json.dumps(data), encoding='utf-8')
    return dll, data


@pytest.mark.parametrize('changed', ['source', 'library', 'abi'])
def test_optional_observer_rejects_a_stale_or_modified_build(tmp_path, changed):
    dll, data = metadata(tmp_path)
    if changed == 'source':
        data['source_sha256'] = 'different'
    elif changed == 'library':
        dll.write_bytes(b'changed library')
    else:
        data['abi'] = 99
    (tmp_path / 'build.json').write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        N.verified_library(tmp_path)


@pytest.fixture
def fake_api(monkeypatch):
    api = SimpleNamespace(**{name:Mock(return_value=result) for name, result in dict(
        trace_version=1, trace_row_size=ctypes.sizeof(N.TraceRow), trace_open=1,
        trace_frequency=1000, trace_clock=75, trace_count=1, trace_total=8200, trace_close=0,
        trace_attach=1, trace_pause=None).items()})
    value = N.TraceRow(sequence=15, begin=100, end=110, hwnd=42, surface=1, role=1,
        message=0x0214, active=2, x=-30, y=-20, width=640, height=660,
        pointer_x=-10, pointer_y=25)
    def read(_index, pointer):
        ctypes.memmove(pointer, ctypes.byref(value), ctypes.sizeof(value))
        return 1
    api.trace_read = Mock(side_effect=read)
    monkeypatch.setattr(N, 'verified_library', lambda *_: (Path('fixture.dll'),
        dict(dll_sha256='fixture-binary', source_sha256='fixture-source')))
    monkeypatch.setattr(N.ctypes, 'CDLL', lambda *_: api)
    return api


def test_trace_reports_signed_positions_ring_loss_and_forwarding_time(fake_api):
    trace = N.NativeResizeTrace()
    report = trace.finish()
    assert report['available'] and report['total'] == 8200 and report['dropped'] == 8199
    row = report['events'][0]
    assert (row['x'], row['y'], row['pointer_x']) == (-30, -20, -10)
    assert row['message_name'] == 'sizing' and row['forward_ms'] == 10
    assert row['active'] == 2
    assert report['clock_anchor']['native_ticks'] == 75
    fake_api.trace_pause.assert_called_once()
    fake_api.trace_close.assert_called_once()
    trace.close()
    fake_api.trace_close.assert_called_once()


def test_native_read_failure_still_detaches_every_hook(fake_api):
    trace = N.NativeResizeTrace()
    fake_api.trace_read.side_effect = None
    fake_api.trace_read.return_value = 0
    with pytest.raises(RuntimeError, match='Could not read'):
        trace.finish()
    assert trace.closed
    fake_api.trace_close.assert_called_once()


def test_trace_ownership_rejects_another_thread_before_touching_native_state(fake_api):
    trace = N.NativeResizeTrace()
    owner, trace.thread = trace.thread, -1
    try:
        with pytest.raises(RuntimeError, match='Tk UI thread'):
            trace.finish()
        fake_api.trace_pause.assert_not_called()
    finally:
        trace.thread = owner
        trace.close()


def test_new_trace_cannot_reset_an_existing_live_native_session(fake_api):
    fake_api.trace_open.return_value = 0
    with pytest.raises(RuntimeError, match='already active'):
        N.NativeResizeTrace()


def test_source_manifest_covers_the_compiled_observers_actual_source(tmp_path):
    (tmp_path / 'tools').mkdir()
    source = tmp_path / 'tools/fixture.c'
    source.write_text('int fixture(void) { return 1; }', encoding='utf-8')
    original = manifest(tmp_path)
    assert 'tools/fixture.c' in original['files']
    source.write_text('int fixture(void) { return 2; }', encoding='utf-8')
    assert manifest(tmp_path)['sha256'] != original['sha256']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows fixture observer')
@pytest.mark.skipif(not (N.RUNTIME / 'build.json').exists(),
                    reason='needs the locally built native helper (python -m tools.tk_resize.build_native_trace --compiler <gcc>)')
def test_actual_observer_records_own_window_geometry_without_native_input(tk_root):
    import tkinter as tk
    from src.ui.theme.compat import NO_AUTOSTYLE
    window = tk.Toplevel(tk_root, **NO_AUTOSTYLE)
    box = tk.Text(window, **NO_AUTOSTYLE)
    box.pack(fill='both', expand=True)
    box.insert('1.0', 'Offline fixture text.')
    window.geometry('420x300+100+100')
    tk_root.update()
    app = SimpleNamespace(popup=window, original_text=box, trans_text=box,
        _view=SimpleNamespace(body_area=SimpleNamespace(canvas=box)),
        quick_translate_manager=SimpleNamespace(popup=None, popup_furigana=None, popup_text=None))
    trace = N.NativeResizeTrace()
    try:
        trace.attach_app(app)
        trace.attach_app(app)
        assert len(trace.windows) == 2
        window.geometry('470x340+100+100')
        tk_root.update()
        report = trace.finish()
        assert report['available'] and report['events']
        assert any(row['message_name'] == 'position' for row in report['events'])
        assert all(row['active'] == 0 for row in report['events'])
        assert box.get('1.0', 'end-1c') == 'Offline fixture text.'
        assert trace.detach_failures == 0
        fresh = N.NativeResizeTrace()
        try:
            assert fresh.api.trace_count() == 0
        finally:
            fresh.close()
    finally:
        trace.close()
        window.destroy()

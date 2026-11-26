"""Persistent manual sessions stay user-controlled and do not duplicate windows."""
from collections import deque
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.tk_resize.manual_launcher import LaunchChannel
from tools.tk_resize.worker import main, scenario_class


@pytest.mark.parametrize('until_close', [False, True])
def test_manual_lifetime_preserves_timed_mode_and_waits_for_user_close(monkeypatch, until_close):
    from tools.tk_resize import worker
    clock = [1000.0]
    monkeypatch.setattr(worker.time, 'perf_counter', lambda: clock[0])
    class Base:
        def _tick(self):
            pytest.fail('Manual mode must not run the automated driver')
        def _later(self, phase, seconds):
            self.phase, self.deadline = phase, clock[0] + seconds
    controller = object.__new__(scenario_class(Base))
    controller.phase, controller.deadline = 'visible', 0
    controller.arguments = SimpleNamespace(manual_visible_desktop=True, manual_seconds=120,
                                           manual_until_close=until_close)
    controller.app = SimpleNamespace(popup=Mock())
    controller.root, controller.recorder = Mock(), Mock()
    controller._describe = lambda: {}
    controller._finish = Mock()
    controller._tick()
    close = controller.app.popup.protocol.call_args.args[1]
    clock[0] = 100000.0
    controller._tick()
    if until_close:
        controller._finish.assert_not_called()
        assert controller.timer is None
        controller.root.after.assert_not_called()
        close()
    controller._finish.assert_called_once()


def test_unbounded_mode_cannot_be_used_by_an_automated_worker(tmp_path):
    output = tmp_path / 'uncreated'
    with pytest.raises(SystemExit) as stopped:
        main(['--source', str(tmp_path), '--output', str(output), '--scenario', 'two-windows',
              '--final-validation', '--manual-until-close'])
    assert stopped.value.code == 2
    assert not output.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows launch-channel contract')
def test_second_launch_signals_the_first_and_handles_are_released(tmp_path):
    first, second, receiver = None, None, None
    try:
        first = LaunchChannel(tmp_path)
        second = LaunchChannel(tmp_path)
        receiver = LaunchChannel(tmp_path, event_only=True)
        assert not first.existing and second.existing
        assert not receiver.requested()
        second.request_raise()
        assert receiver.requested()
        assert not receiver.requested()
    finally:
        for channel in (receiver, second, first):
            if channel:
                channel.close()
    fresh = LaunchChannel(tmp_path)
    try:
        assert not fresh.existing
    finally:
        fresh.close()


def test_manual_geometry_buffer_stays_bounded_during_a_long_session():
    callbacks = []
    window = SimpleNamespace(bind=lambda _sequence, callback, **_kwargs: callbacks.append(callback))
    class Base:
        def _show(self):
            pass
    controller = object.__new__(scenario_class(Base))
    controller.workload = SimpleNamespace(name='two-windows')
    controller.arguments = SimpleNamespace(manual_visible_desktop=True)
    controller.app = SimpleNamespace(popup=window, quick_translate_manager=SimpleNamespace(popup=None))
    controller.started = 0
    controller.manual_geometry = deque(maxlen=2)
    controller.manual_geometry_total = 0
    controller._show()
    for width in (600, 610, 620):
        callbacks[0](SimpleNamespace(widget=window, x=10, y=10, width=width, height=400))
    assert controller.manual_geometry_total == 3
    assert [row['width'] for row in controller.manual_geometry] == [610, 620]


@pytest.mark.parametrize('failure', ['missing', 'attachment'])
def test_optional_native_trace_failure_keeps_fixture_usable_and_preserves_partial_data(monkeypatch, tmp_path, failure):
    from tools.tk_resize import manual_fixture, manual_launcher, native_trace
    recorder, native = Mock(), Mock()
    recorder.manifest = dict(source=dict(sha256='fixture-source'))
    native.finish.return_value = dict(available=True, events=[dict(message_name='position')])
    native.detach_failures = 0
    if failure == 'missing':
        constructor = Mock(side_effect=FileNotFoundError('Optional trace is absent'))
    else:
        constructor = Mock(return_value=native)
        native.attach_app.side_effect = RuntimeError('Fixture attachment failed')
    monkeypatch.setattr(native_trace, 'NativeResizeTrace', constructor)
    monkeypatch.setattr(manual_fixture, 'scenario_factory', lambda _original, _private: lambda base: base)

    class Base:
        def __init__(self):
            self.app = SimpleNamespace(popup=Mock(), quick_translate_manager=Mock())
            self.root, self.recorder, self.started = Mock(), recorder, 1.0
            self.finishes = 0
        def _prepare_windows(self):
            return True
        def _finish(self):
            self.finishes += 1

    def run(_arguments):
        cls = manual_fixture.scenario_factory(None, private_check=True)(Base)
        controller = cls()
        assert controller._prepare_windows()
        assert controller._prepare_windows()
        constructor.assert_called_once()
        controller._finish()
        controller._finish()
        assert controller.finishes == 1
        return 0

    monkeypatch.setattr(manual_fixture, 'main', run)
    assert manual_launcher.run_fixture(tmp_path, private_check=True) == 0
    reports = {call.args[0]: call.args[1] for call in recorder.write_json.call_args_list}
    assert set(reports) == {'native-trace.json', 'deferred-content.json'}
    assert recorder.write_json.call_count == 2
    report = reports['native-trace.json']
    assert reports['deferred-content.json']['samples'] == []
    assert report['error'] and report['fixture_started_perf_counter'] == 1.0
    if failure == 'attachment':
        assert report['available'] and report['events']
        native.finish.assert_called_once()
    else:
        assert not report['available']

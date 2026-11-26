"""The controller side of the audio panel (CHANGELOG Phase 28, Decision 88).

A recording started from the panel ignores the shortcut keys and stops on the panel's Stop, the limit or Cancel; it
opens no status popup, because the panel shows the recording. Watchers follow every phase, with a level per device.
The held-shortcut recording is unchanged (tests/test_audio_workflow.py).
"""
import json
import logging
import threading
from types import SimpleNamespace

import pytest

from src.core import audio_media as media
from src.utils import logging_setup
from tests.final_ui_helpers import design, pump  # noqa: F401  (fixture)
from tests.test_audio_workflow import controller, rows, signal  # noqa: F401  (fixture)


class Capture:
    """A recording that runs until stopped, with two devices of different loudness."""
    source = 'both'
    limit = 180
    limit_reached = False

    def __init__(self, *args):
        self.stopped = threading.Event()
        self.cancelled = threading.Event()
        self.files = None

    def stop(self):
        self.stopped.set()

    def cancel(self):
        self.cancelled.set()
        self.stopped.set()

    def current_levels(self):
        return {'PC': .5, 'Microphone': .1}

    def run(self, progress):
        progress('recording', 0, .5)
        assert self.stopped.wait(3)
        if self.cancelled.is_set():
            raise media.AudioCancelled('Audio translation was cancelled.')
        progress('recording', 1.4, .5)
        files = media.AudioFiles()
        path = files.directory / 'pc.wav'
        media.write_pcm(path, signal())
        files.tracks = [media.AudioTrack(str(path), 'PC')]
        return files


@pytest.fixture
def panel_run(controller, monkeypatch):
    """The controller with a fake capture, recognizer and translation, and a watcher's log of phases."""
    from src.ui import audio_controller as module
    captures = []

    def make(*args):
        captures.append(Capture())
        return captures[-1]
    monkeypatch.setattr(module, 'CaptureSession', make)
    monkeypatch.setattr(controller, '_recognizer', lambda: SimpleNamespace(rows=rows(), transcribe=lambda *a: 'Speech'))
    monkeypatch.setattr(controller, '_translate', lambda *args: ('Translation', 'fixture (Groq)'))
    seen = []
    controller.watchers.append(lambda phase, info: seen.append((phase, info)))
    return SimpleNamespace(controller=controller, captures=captures, seen=seen)


def phases(seen):
    names = []
    for phase, _ in seen:
        if not names or names[-1] != phase:
            names.append(phase)
    return names


class TestPanelRecording:
    def test_it_ignores_the_shortcut_keys_and_opens_no_status_popup(self, panel_run):
        controller = panel_run.controller
        controller.app.hotkey_manager.audio_keys_down = lambda chord: (False, False, False)   # keys are up
        controller.start_recording('panel')
        pump(controller.root, lambda: controller.state == 'recording')
        controller._pump()
        assert not panel_run.captures[0].stopped.is_set(), 'released keys do not stop a panel recording'
        assert controller.phase == 'recording' and controller.trigger == 'panel'
        assert not getattr(controller.app.quick_translate_manager, '_audio_status', False), 'the panel shows it'
        assert controller.app.quick_translate_manager.popup is None
        controller.stop_recording()
        pump(controller.root, lambda: controller.state == 'result')
        assert controller.transcript == 'Speech' and controller.phase == 'idle'

    def test_watchers_follow_every_phase_with_a_level_per_device(self, panel_run):
        controller = panel_run.controller
        controller.toggle_recording()
        pump(controller.root, lambda: controller.state == 'recording')
        controller.toggle_recording()
        pump(controller.root, lambda: controller.state == 'result')
        assert phases(panel_run.seen) == ['preparing', 'recording', 'processing', 'idle']
        recording = next(info for phase, info in panel_run.seen if phase == 'recording')
        assert recording['levels'] == {'PC': .5, 'Microphone': .1} and recording['limit'] == 180
        assert panel_run.seen[-1] == ('idle', {'outcome': 'ok', 'message': ''})

    def test_toggle_is_ignored_while_the_recording_is_processed(self, panel_run, monkeypatch):
        controller = panel_run.controller
        controller.busy, controller.capture, controller.state = True, None, 'transcribing'
        started = []
        monkeypatch.setattr(controller, 'start_recording', lambda trigger='hold': started.append(trigger))
        controller.toggle_recording()
        assert controller.phase == 'processing' and started == []
        controller.busy = False

    def test_record_again_after_a_result_records_once_the_save_question_is_answered(self, panel_run):
        controller = panel_run.controller
        controller.start_recording('panel')
        pump(controller.root, lambda: controller.state == 'recording')
        controller.stop_recording()
        pump(controller.root, lambda: controller.state == 'result')
        assert controller.files is not None
        controller.start_recording('panel')            # 'ask': the save question comes first
        assert controller.dialog is not None and len(panel_run.captures) == 1
        controller.dialog._default.invoke()            # No: discard the first recording
        assert len(panel_run.captures) == 2, 'the panel records after the question; a held shortcut would not'
        pump(controller.root, lambda: controller.state == 'recording')
        controller.cancel_from_panel()
        assert panel_run.captures[1].cancelled.is_set()

    def test_cancel_returns_the_panel_to_idle_without_a_result(self, panel_run):
        controller = panel_run.controller
        controller.start_recording('panel')
        pump(controller.root, lambda: controller.state == 'recording')
        controller.cancel_from_panel()
        assert controller.phase == 'idle' and panel_run.seen[-1][0] == 'idle'
        assert panel_run.seen[-1][1]['outcome'] == 'cancelled'
        assert controller.app.translation_service.history_manager.add_entry.call_count == 0

    def test_a_failing_watcher_is_logged_and_the_recording_goes_on(self, panel_run, caplog):
        controller = panel_run.controller
        controller.watchers.insert(0, lambda phase, info: 1 / 0)
        with caplog.at_level(logging.WARNING):
            controller.start_recording('panel')
            pump(controller.root, lambda: controller.state == 'recording')
            controller.stop_recording()
            pump(controller.root, lambda: controller.state == 'result')
        assert 'ZeroDivisionError' in caplog.text and phases(panel_run.seen)[-1] == 'idle'

    def test_the_session_says_what_started_it(self, panel_run):
        lines = []

        class Capture(logging.Handler):
            def emit(self, record):
                lines.append(json.loads(logging_setup.JsonLinesFormatter().format(record)))
        handler = Capture(level=logging.INFO)
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        try:
            panel_run.controller.start_recording('panel')
            pump(panel_run.controller.root, lambda: panel_run.controller.state == 'recording')
            panel_run.controller.cancel_from_panel()
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        start = next(line for line in lines if line.get('event') == 'session.start')
        assert start['data']['trigger'] == 'panel'


class TestLevels:
    def test_each_device_has_its_own_level_and_a_silent_one_falls_to_zero(self):
        clock = [0.0]

        def now():
            clock[0] += .01
            return clock[0]

        class Stream:
            def __init__(self, callback, loud):
                self.callback, self.loud = callback, loud

            def start_stream(self):
                if self.loud:
                    self.callback((signal(.1) * 4).astype('<i2').tobytes(), 1600, {}, 0)

            def stop_stream(self):
                pass

            def close(self):
                pass

            def is_active(self):
                return True

        class Device:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_default_wasapi_loopback(self):
                return dict(index=1, maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=True)

            def get_default_input_device_info(self):
                return dict(index=2, maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=False)

            def open(self, **kwargs):
                return Stream(kwargs['stream_callback'], loud=kwargs['input_device_index'] == 1)
        backend = SimpleNamespace(PyAudio=Device, paInt16=8, paComplete=1, paContinue=0)
        capture = media.CaptureSession('both', limit=.5, backend=backend, clock=now)
        seen = []
        files = capture.run(lambda stage, *args: seen.append(dict(capture.levels)) if stage == 'recording' else None)
        try:
            assert set(capture.levels) == {'PC', 'Microphone'}, 'every recording device has a meter'
            pc_level = capture.levels['PC'][0]
            assert pc_level > .1 and capture.levels['Microphone'][0] == 0.0
            clock[0] = capture.levels['PC'][1] + .1
            assert capture.current_levels(window=.3)['PC'] == pytest.approx(pc_level)
            clock[0] = capture.levels['PC'][1] + 1
            assert capture.current_levels(window=.3) == {'PC': 0.0, 'Microphone': 0.0}, 'no packets: no level'
        finally:
            files.cleanup()

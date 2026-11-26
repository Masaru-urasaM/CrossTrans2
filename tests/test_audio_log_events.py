"""The audio pipeline's log events (CHANGELOG Phase 24): one session readable end to end, no user content.

The acceptance log of 2026-11-16 said three recordings had started and nothing else: not which devices delivered
sound, which model answered or that a one-step request had run as two steps. These events answer that from the
log. Fake devices and providers; no real capture, no network.
"""
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core import audio_media as media, audio_service as speech
from src.ui import components as ui
from src.utils import logging_setup
from tests.final_ui_helpers import design, pump  # noqa: F401  (design is a fixture)
from tests.test_audio_loopback_channels import Backend
from tests.test_audio_workflow import controller, queue_events, rows, signal, track  # noqa: F401  (fixtures)


@pytest.fixture
def events():
    """The structured lines logged on the root logger, as the file formatter writes them."""
    lines = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(json.loads(logging_setup.JsonLinesFormatter().format(record)))

    root = logging.getLogger()
    handler = Capture(level=logging.DEBUG)
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def named(lines, event):
    return [line for line in lines if line.get('event') == event]


def record_with(backend, source='both', sid='feedc0de'):
    clock = [0.0]

    def now():
        clock[0] += .05
        return clock[0]
    capture = media.CaptureSession(source, limit=1, backend=backend, clock=now)
    capture.sid = sid
    return capture.run(lambda *args: None)


class TestRecordingStop:
    def test_each_device_reports_what_it_delivered(self, events):
        files = record_with(Backend(2))
        try:
            stop, = named(events, 'recording.stop')
            assert stop['sid'] == 'feedc0de' and stop['data']['tracks'] == 2
            devices = {device['role']: device for device in stop['data']['devices']}
            assert set(devices) == {'PC', 'Microphone'}
            for device in devices.values():
                assert device['frames'] == 4800 and device['rate'] == 48000 and device['track'] is True
                assert 0.2 < device['peak'] < 0.3 and device['rms'] > 0
            assert devices['Microphone']['name'] == 'Microphone (Desk Mic)'
            assert stop['data']['skipped'] == []
        finally:
            files.cleanup()

    def test_a_headset_microphone_left_out_by_the_bluetooth_safeguard_is_named(self, events):
        backend = Backend(2)
        base = backend.PyAudio

        class SameHeadset(base):
            def get_default_input_device_info(self):
                return dict(index=22, name='Headset Microphone (Gamer 7.1)', maxInputChannels=1,
                            defaultSampleRate=48000, isLoopbackDevice=False)
        backend.PyAudio = SameHeadset
        files = record_with(backend)
        try:
            stop, = named(events, 'recording.stop')
            assert [device['role'] for device in stop['data']['devices']] == ['PC']
            assert stop['data']['skipped'] == [{'role': 'Microphone', 'name': 'Headset Microphone (Gamer 7.1)',
                                                'reason': 'bluetooth_clash'}]
        finally:
            files.cleanup()


class TestRecognition:
    def test_plan_chunk_and_mode_for_a_transcription_key(self, tmp_path, events):
        recognizer = speech.AudioRecognizer(rows(), transport=lambda *args: {'text': 'Speech.'})
        recognizer.sid = 'a1b2c3d4'
        recognizer.transcribe([track(tmp_path)], threading.Event(), lambda *args: None)
        plan, = named(events, 'stt.plan')
        assert plan['data'] == {'tracks': [{'source': 'File', 'seconds': 1.0}], 'chunks': 1, 'silent_skipped': 0,
                                'mode_requested': 'two_step', 'keys': 2, 'denoise': False}
        chunk, = named(events, 'stt.chunk')
        assert chunk['sid'] == 'a1b2c3d4'
        assert {key: chunk['data'][key] for key in ('index', 'total', 'cached', 'key_slot', 'engine', 'chars')} == \
            {'index': 1, 'total': 1, 'cached': False, 'key_slot': 1, 'engine': 'custom', 'chars': len('Speech.')}
        assert chunk['data']['provider'] == 'Groq' and isinstance(chunk['data']['ms'], int)
        mode, = named(events, 'mode.used')
        assert (mode['data']['requested'], mode['data']['used'], mode['data']['reason']) == ('two_step', 'two_step', '')

    def test_a_repeated_chunk_is_logged_as_cached(self, tmp_path, events):
        recognizer = speech.AudioRecognizer(rows(), transport=lambda *args: {'text': 'Speech.'})
        audio = track(tmp_path)
        recognizer.transcribe([audio], threading.Event(), lambda *args: None)
        recognizer.transcribe([audio], threading.Event(), lambda *args: None)
        assert [chunk['data']['cached'] for chunk in named(events, 'stt.chunk')] == [False, True]

    def test_a_failed_key_is_a_warning_with_its_status_and_the_next_key_answers(self, tmp_path, events):
        def provider(wav, key, model, language=''):
            if key.endswith('first'):
                raise speech.SpeechRequestError(401, 'The speech provider did not accept this API key.')
            return {'text': 'Speech.'}
        speech.AudioRecognizer(rows(), transport=provider).transcribe(
            [track(tmp_path)], threading.Event(), lambda *args: None)
        fail, = named(events, 'stt.fail')
        assert fail['level'] == 'WARNING'
        assert (fail['data']['status'], fail['data']['key_slot'], fail['data']['error']) == (401, 1, 'SpeechRequestError')
        assert named(events, 'stt.chunk')[0]['data']['key_slot'] == 2

    def test_one_step_with_a_transcription_key_says_it_ran_as_two_steps(self, tmp_path, events):
        recognizer = speech.AudioRecognizer(rows(), transport=lambda *args: {'text': 'Speech.'}, mode='one_step')
        recognizer.transcribe([track(tmp_path)], threading.Event(), lambda *args: None)
        mode, = named(events, 'mode.used')
        assert (mode['data']['used'], mode['data']['reason']) == ('two_step', 'engine_without_one_step')
        assert (recognizer.mode_used, recognizer.mode_reason) == ('two_step', 'engine_without_one_step')

    def test_one_step_with_gemini_says_it_ran_as_one_step(self, tmp_path, events, monkeypatch):
        monkeypatch.setattr(speech, 'execute_gemini_audio_request', lambda *args: (
            '<TRANSCRIPT>Hello</TRANSCRIPT><TRANSLATION>Xin chao</TRANSLATION>'))
        gemini = [dict(id='g', api_key='fixture-gemini', provider='Google', model_name='gemini-2.5-flash',
                       is_audio_key=True)]
        recognizer = speech.AudioRecognizer(gemini, model='gemini-2.5-flash', mode='one_step',
                                            target_language='Vietnamese')
        recognizer.transcribe([track(tmp_path)], threading.Event(), lambda *args: None)
        chunk, = named(events, 'stt.chunk')
        assert (chunk['data']['provider'], chunk['data']['engine']) == ('Google', 'gemini_one_step')
        mode, = named(events, 'mode.used')
        assert (mode['data']['used'], mode['data']['one_step_replies']) == ('one_step', 1)

    def test_silence_is_counted_and_nothing_is_sent(self, tmp_path, events):
        import numpy as np
        path = tmp_path / 'silence.wav'
        media.write_pcm(path, np.zeros(media.RATE, np.int16))
        recognizer = speech.AudioRecognizer(rows(), transport=Mock(), mode='one_step')
        with pytest.raises(media.AudioError, match='No speech'):
            recognizer.transcribe([media.AudioTrack(str(path), 'PC')], threading.Event(), lambda *args: None)
        plan, = named(events, 'stt.plan')
        assert (plan['data']['chunks'], plan['data']['silent_skipped']) == (0, 1)
        assert named(events, 'mode.used')[0]['data']['reason'] == 'no_speech'
        assert not named(events, 'stt.chunk')


class TestSessions:
    def test_a_file_session_is_one_id_from_start_to_done_without_names_or_text(self, controller, monkeypatch,
                                                                               tmp_path, events):
        source = tmp_path / 'private-board-meeting.wav'
        media.write_pcm(source, signal(1))
        controller.app.popup = ui.Frame(controller.root)
        controller.app.original_text = SimpleNamespace(get=lambda *args: '')
        controller.app.attachment_area = Mock()
        controller.app._start_translate_animation = Mock()
        controller.app._stop_translate_animation = Mock()
        controller.app._view = Mock()
        controller.app._update_translation_with_original = Mock()
        recognizer = SimpleNamespace(rows=rows(), cache={}, mode='two_step', mode_used='two_step', mode_reason='',
                                     transcribe=lambda *args: 'Recognized upload words')
        monkeypatch.setattr(controller, '_recognizer', lambda: recognizer)
        monkeypatch.setattr(controller, '_translate', lambda *args: ('Translated upload words', 'fixture (Groq)'))
        controller.translate_file(str(source), 'Japanese', controller.app.popup)
        pump(controller.root, lambda: not controller.busy)
        start, = named(events, 'session.start')
        decode, = named(events, 'file.decode')
        done, = named(events, 'session.done')
        assert start['sid'] == decode['sid'] == done['sid']
        assert (start['data']['kind'], start['data']['ext'], start['data']['target']) == ('file', '.wav', 'Japanese')
        assert decode['data']['seconds'] == 1.0
        assert done['data']['outcome'] == 'ok' and done['level'] == 'INFO'
        assert done['data']['transcript_chars'] == len('Recognized upload words')
        assert done['data']['translation_chars'] == len('Translated upload words')
        assert done['data']['models'] == 'fixture (Groq)'
        assert {'decode_ms', 'stt_ms', 'translate_ms', 'total_ms'} <= set(done['data'])
        written = json.dumps(events, ensure_ascii=False)
        assert 'private-board-meeting' not in written, 'the file name is the user\'s'
        assert 'upload words' not in written, 'no transcript or translation text'

    def test_cancel_closes_the_session_once(self, controller, events):
        controller._open_session('recording', SimpleNamespace(rows=[], mode='two_step'), source='pc')
        controller.cancel()
        controller.cancel()
        done, = named(events, 'session.done')
        assert done['data']['outcome'] == 'cancelled' and done['data']['kind'] == 'recording'

    def test_an_error_ends_the_session_as_a_warning_with_its_message(self, controller, events):
        controller._open_session('recording', SimpleNamespace(rows=[], mode='two_step'))
        controller._close_session('error', error=media.AudioError('No speech was detected. Check the source and try again.'))
        done, = named(events, 'session.done')
        assert done['level'] == 'WARNING'
        assert (done['data']['error'], done['data']['message']) == (
            'AudioError', 'No speech was detected. Check the source and try again.')

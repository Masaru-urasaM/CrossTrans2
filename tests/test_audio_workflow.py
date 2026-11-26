"""Audio acceptance over generated PCM, fake devices/providers and an isolated Tk desktop."""
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error
import wave

import numpy as np
import pytest

from src.core import audio_media as media, audio_service as speech, key_testing as kt
from src.core.hotkey import HotkeyManager
from src.core.key_usage import KeyUsage
from src.ui import components as ui
from src.ui.audio_controller import AudioController
from src.ui.quick_translate import QuickTranslateManager
from tests.final_ui_helpers import design, pump
from tests.settings_support import Host, SettingsConfig


def signal(seconds=1, frequency=350):
    return (3000 * np.sin(np.arange(round(seconds * media.RATE)) * frequency * 2 * np.pi / media.RATE)).astype(np.int16)


def track(tmp_path, seconds=1, source='File'):
    path = tmp_path / (source + '.wav')
    media.write_pcm(path, signal(seconds))
    return media.AudioTrack(str(path), source)


def rows():
    return [dict(id='first', api_key='fixture-first', provider='Groq', model_name='text-model'),
            dict(id='second', api_key='fixture-second', provider='Groq', model_name='text-model')]


@pytest.mark.parametrize('text', ['日本語の文です。' * 600, 'hello world.\n' * 600, 'x' * 5300])
def test_text_chunking_preserves_every_character(text):
    chunks = list(speech.text_chunks(text))
    assert ''.join(chunks) == text
    assert all(len(part) <= 1800 for part in chunks)


def test_upload_decodes_locally_and_never_changes_original(tmp_path):
    original = track(tmp_path, 1)
    before = Path(original.path).read_bytes()
    destination = tmp_path / 'decoded.wav'
    assert media.decode_media(original.path, destination, threading.Event()) == pytest.approx(1)
    assert Path(original.path).read_bytes() == before
    assert len(media.read_pcm(destination)) == media.RATE


def test_duration_limit_rejects_instead_of_silently_truncating(tmp_path):
    original = track(tmp_path, 2)
    with pytest.raises(media.AudioError, match='30 minutes'):
        media.decode_media(original.path, tmp_path / 'decoded.wav', threading.Event(), limit=1)
    assert len(media.read_pcm(original.path)) == 2 * media.RATE


def test_cancelled_decoding_does_not_accept_a_result(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(media.AudioCancelled):
        media.decode_media(track(tmp_path).path, tmp_path / 'decoded.wav', cancelled)


def test_invalid_media_has_actionable_error(tmp_path):
    source = tmp_path / 'invalid.mp4'
    source.write_bytes(b'not media')
    with pytest.raises(media.AudioError, match='decoded'):
        media.decode_media(source, tmp_path / 'decoded.wav', threading.Event())


def test_save_both_preserves_separate_channels_and_start_offset(tmp_path):
    files = media.AudioFiles()
    try:
        pc, mic = files.directory / 'pc.wav', files.directory / 'mic.wav'
        media.write_pcm(pc, signal())
        media.write_pcm(mic, signal(frequency=500))
        files.tracks = [media.AudioTrack(str(pc), 'PC'), media.AudioTrack(str(mic), 'Microphone', 0.1)]
        destination = tmp_path / 'saved.wav'
        files.save(destination)
        with wave.open(str(destination)) as stream:
            assert stream.getnchannels() == 2
            data = np.frombuffer(stream.readframes(stream.getnframes()), '<i2').reshape(-1, 2)
        assert not data[:1600, 1].any()
        assert data[1600:2000, 1].any()
        files.cleanup()
        assert destination.is_file()
    finally:
        files.cleanup()


def test_noise_reduction_is_bounded_and_retains_sample_count():
    original = signal()
    output = media.reduce_noise(original)
    assert output.shape == original.shape and output.dtype == np.int16
    assert np.isfinite(output).all()


def test_uploaded_mp4_audio_is_decoded_without_playback(tmp_path):
    import av
    source = tmp_path / 'uploaded.mp4'
    with av.open(str(source), 'w') as container:
        stream = container.add_stream('aac', rate=16000)
        stream.layout = 'mono'
        frame = av.AudioFrame.from_ndarray(signal().reshape(1, -1), format='s16', layout='mono')
        frame.sample_rate = 16000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    destination = tmp_path / 'decoded.wav'
    duration = media.decode_media(source, destination, threading.Event())
    assert .9 < duration < 1.2
    assert media.read_pcm(destination).any()


def test_multipart_has_real_wav_and_no_local_filename():
    body, content_type = speech.multipart(speech.wav_bytes(signal()), speech.MODELS[0], 'ja')
    assert content_type.startswith('multipart/form-data; boundary=')
    assert b'RIFF' in body and b'filename="audio.wav"' in body
    assert b'whisper-large-v3' in body and b'\r\n\r\nja\r\n' in body


def test_actual_request_uses_dedicated_speech_endpoint():
    seen = []

    def open_request(request, timeout):
        seen.append(request)
        assert timeout == 45
        return io.BytesIO(json.dumps({'text': 'recognized'}).encode())
    result = speech.request_transcript(speech.wav_bytes(signal()), 'fixture-only', opener=open_request)
    assert result['text'] == 'recognized'
    assert seen[0].full_url.endswith('/audio/transcriptions')
    assert seen[0].get_header('Authorization') == 'Bearer fixture-only'


@pytest.mark.parametrize('code', [401, 403, 404, 413, 429, 500])
def test_speech_errors_preserve_category_without_exposing_key(code):
    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, 'failed', {}, io.BytesIO())
    with pytest.raises(speech.SpeechRequestError) as caught:
        speech.request_transcript(b'fake', 'fixture-secret-never-in-error', opener=fail)
    assert caught.value.code == code
    assert 'fixture-secret' not in str(caught.value)


@pytest.mark.parametrize('response', [[], {}, {'text': None}, {'text': 12}])
def test_invalid_json_response_does_not_become_a_transcript(response):
    with pytest.raises(media.AudioError, match='invalid response'):
        speech.request_transcript(b'fake', 'fixture', opener=lambda req, timeout: io.BytesIO(json.dumps(response).encode()))


def test_speech_keys_keep_priority_and_exclude_non_audio_providers():
    configured = rows()
    configured.insert(0, dict(id='text-only', provider='Anthropic', api_key='sk-ant-fixture'))
    configured.append(dict(id='gemini', provider='Google', api_key='AIzaFixture'))
    assert [row['id'] for row in speech.eligible_keys(configured)] == ['first', 'second', 'gemini']
    configured[1].update(audio_capable=False, audio_model='whisper-large-v3')
    assert [row['id'] for row in speech.eligible_keys(configured)] == ['second', 'gemini']
    assert len(speech.eligible_keys(configured, 'whisper-large-v3-turbo')) == 3


def test_groq_only_translation_config_excludes_paid_fallback():
    config = SettingsConfig(api_keys=rows() + [dict(provider='Google', api_key='fixture-google')])
    view = speech.GroqOnlyConfig(config)
    assert [row['provider'] for row in view.get_api_keys()] == ['Groq', 'Groq']
    assert view.get('audio_model') == 'whisper-large-v3'


def test_recognition_retries_keep_successful_chunk_results(tmp_path):
    calls = []
    failed = [True]

    def provider(wav, key, model, language=''):
        calls.append((len(wav), key))
        if len(calls) > 1 and failed[0]:
            raise speech.SpeechRequestError(429, 'quota')
        return {'text': 'Speech segment.'}
    recognizer = speech.AudioRecognizer(rows(), transport=provider)
    audio = track(tmp_path, 185)
    with pytest.raises(speech.SpeechRequestError):
        recognizer.transcribe([audio], threading.Event(), lambda *args: None)
    failed[0] = False
    text = recognizer.transcribe([audio], threading.Event(), lambda *args: None)
    assert text and len(calls) == 4


def test_failed_key_can_use_next_saved_groq_key(tmp_path):
    called = []

    def provider(wav, key, model, language=''):
        called.append(key)
        if key.endswith('first'):
            raise speech.SpeechRequestError(401, 'unauthorized')
        return {'text': 'Speech.'}
    text = speech.AudioRecognizer(rows(), transport=provider).transcribe(
        [track(tmp_path)], threading.Event(), lambda *args: None)
    assert text == 'Speech.'
    assert called == ['fixture-first', 'fixture-second']


def test_silence_does_not_spend_api_quota(tmp_path):
    path = tmp_path / 'silence.wav'
    media.write_pcm(path, np.zeros(media.RATE, np.int16))
    provider = Mock()
    with pytest.raises(media.AudioError, match='No speech'):
        speech.AudioRecognizer(rows(), transport=provider).transcribe(
            [media.AudioTrack(str(path), 'PC')], threading.Event(), lambda *args: None)
    provider.assert_not_called()


def test_both_sources_preserve_segment_order_and_remove_exact_echo(tmp_path):
    responses = iter([
        dict(text='PC speech', segments=[dict(start=0, text='Hello'), dict(start=2, text='PC next')]),
        dict(text='Microphone speech', segments=[dict(start=0.1, text='Hello'), dict(start=1, text='My reply')]),
    ])
    recognizer = speech.AudioRecognizer(rows(), transport=lambda *args: next(responses))
    pc, mic = track(tmp_path, source='PC'), track(tmp_path, source='Microphone')
    media.write_pcm(mic.path, signal(frequency=500))
    text = recognizer.transcribe([pc, mic], threading.Event(), lambda *args: None)
    assert text.count('Hello') == 1
    assert text.index('My reply') < text.index('PC next')
    assert '[' not in text and 'Microphone' not in text
    assert recognizer.timeline[1][1] == 'Microphone'


def test_non_speech_segments_are_not_translated():
    result = dict(text='invented', segments=[dict(text='invented', start=0, no_speech_prob=.9, avg_logprob=-2)])
    assert speech.speech_segments(result) == []


@pytest.mark.parametrize('text,supported', [('My fellow Americans.', True), ('', None), ('unrelated', None)])
def test_capability_probe_requires_recognizable_fixture_speech(text, supported):
    calls = []

    def provider(wav, key, model):
        with wave.open(io.BytesIO(wav)) as sample:
            assert sample.getnframes() == 4 * media.RATE
        calls.append(key)
        return {'text': text}
    result = speech.probe('fixture-only', 'Groq', transport=provider)
    assert result.supported is supported and calls == ['fixture-only']


@pytest.mark.parametrize('code,supported', [(401, None), (403, None), (429, None), (404, False)])
def test_probe_does_not_save_temporary_errors_as_unsupported(code, supported):
    def provider(*args):
        raise speech.SpeechRequestError(code, 'fixture failure')
    assert speech.probe('fixture', 'Groq', transport=provider).supported is supported


def test_audio_usage_does_not_change_last_successful_translation_key():
    config = SettingsConfig(api_keys=rows())
    usage = KeyUsage(config)
    usage.apply(dict(id='first', at=time.time(), seconds=1, kind='success'))
    usage.apply(dict(id='second', at=time.time(), seconds=.3, kind='audio_success'))
    assert usage.row('first')['is_last_success']
    assert not usage.row('second')['is_last_success']
    assert config.get('key_usage')['rows']['second']['audio_requests'] == 1


def test_key_test_probes_separate_speech_model_and_reports_audio():
    manager = SimpleNamespace(test_connection=Mock(), test_vision_connection=Mock(return_value=False),
                              test_audio_connection=Mock(return_value=speech.SpeechProbe(True, speech.MODELS[0])))
    outcome = kt.run(manager, [('Groq', 'text-model')], 'fixture', audio_model=speech.MODELS[0])
    manager.test_audio_connection.assert_called_once_with('fixture', 'Groq', speech.MODELS[0])
    assert outcome.ok and outcome.audio is True and outcome.vision is False
    assert 'audio' in kt.summary(outcome)


def test_audio_capability_changes_when_model_changes_and_survives_saved_rows():
    config = SettingsConfig(api_keys=rows())
    config.update_api_capabilities('fixture-first', 'text-model', False, True, provider='Groq',
                                   audio_capable=True, audio_model=speech.MODELS[0])
    assert config.has_any_audio_capable()
    saved = kt.merge_rows([('Groq', 'text-model', 'fixture-first')], config.get_api_keys())
    assert saved[0]['audio_capable'] is True and saved[0]['audio_model'] == speech.MODELS[0]
    changed = kt.merge_rows([('Groq', 'text-model', 'fixture-changed')], config.get_api_keys())
    assert 'audio_capable' not in changed[0]
    config.set('audio_model', speech.MODELS[1])
    assert not config.has_any_audio_capable()


def test_release_tracking_waits_for_every_chord_key():
    manager = HotkeyManager(SettingsConfig(), lambda language: None)
    pressed = {0x41, 0xA4, 0x5B}
    manager.user32 = SimpleNamespace(GetAsyncKeyState=lambda key: 0x8000 if key in pressed else 0)
    assert all(manager.audio_keys_down())
    pressed.remove(0x41)
    assert any(manager.audio_keys_down())
    pressed.clear()
    assert not any(manager.audio_keys_down())


def test_cancel_interrupts_waiting_for_provider_and_rejects_late_usage(tmp_path):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    usage, errors = [], []

    def provider(*args):
        entered.set()
        release.wait(3)
        return {'text': 'Late text'}
    recognizer = speech.AudioRecognizer(rows(), transport=provider, usage=usage.append)

    def work():
        try:
            recognizer.transcribe([track(tmp_path)], cancelled, lambda *args: None)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=work)
    thread.start()
    assert entered.wait(2)
    cancelled.set()
    thread.join(timeout=1)
    release.set()
    assert not thread.is_alive()
    assert isinstance(errors[0], media.AudioCancelled)
    assert usage == []


@pytest.mark.parametrize('source,channels', [('pc', 1), ('microphone', 1), ('both', 2)])
def test_capture_all_sources_use_fake_streams_and_enforce_limit(source, channels):
    clock = [0.0]
    closed = []

    def now():
        clock[0] += .05
        return clock[0]

    class Stream:
        def __init__(self, callback):
            self.callback = callback

        def start_stream(self):
            self.callback(signal(.1).tobytes(), 1600, {}, 0)

        def stop_stream(self):
            pass

        def close(self):
            closed.append(self)

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
            assert kwargs['input'] is True and kwargs['start'] is False
            return Stream(kwargs['stream_callback'])
    backend = SimpleNamespace(PyAudio=Device, paInt16=8, paComplete=1, paContinue=0)
    capture = media.CaptureSession(source, limit=1, backend=backend, clock=now)
    files = capture.run(lambda *args: None)
    try:
        assert capture.limit_reached and len(files.tracks) == channels
        assert len(closed) == channels
        assert all(Path(track.path).exists() for track in files.tracks)
    finally:
        files.cleanup()


def test_release_before_device_start_never_opens_a_stream():
    opened = []

    class Device:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_default_wasapi_loopback(self):
            return dict(index=1, maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=True)

        def open(self, **kwargs):
            opened.append(True)
    capture = media.CaptureSession(backend=SimpleNamespace(PyAudio=Device))
    capture.stop()
    with pytest.raises(media.AudioCancelled):
        capture.run(lambda *args: None)
    assert not opened and not capture.files.directory.exists()


@pytest.fixture
def controller(design):
    config = SettingsConfig(api_keys=rows())
    quick = QuickTranslateManager(design.root, config)
    history = Mock()
    app = SimpleNamespace(root=design.root, config=config, quick_translate_manager=quick,
                          translation_service=SimpleNamespace(key_usage_events=queue_events(), history_manager=history,
                                                               _alignment_history_args=lambda text: {}),
                          selected_language='Japanese', popup=None, toast=Mock(), _translation_tasks=Mock())

    def show(original, translated, target, **kwargs):
        quick.show(translated, target, original=original, model_info=kwargs.get('model_info'),
                   audio_result=kwargs.get('audio_result', False), on_audio_play=kwargs.get('on_audio_play'))
    app.show_quick_translate = show
    app.hotkey_manager = SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 1),
                                        audio_keys_down=lambda chord: (True, True, True))
    instance = AudioController(app)
    callback_errors = []
    previous_callback = design.root.report_callback_exception
    design.root.report_callback_exception = lambda *args: callback_errors.append(args)
    yield instance
    instance.shutdown()
    quick.close()
    design.root.report_callback_exception = previous_callback
    assert not callback_errors, callback_errors


def queue_events():
    import queue
    return queue.SimpleQueue()


def test_audio_status_has_real_level_and_no_translation_timeout(controller):
    manager = controller.app.quick_translate_manager
    manager.capture_mouse_position()
    manager.show_audio_status('Preparing recording…', 'Japanese')
    manager.update_audio_status('Recording', 'Release all shortcut keys to translate', .7)
    assert not manager._loading_animation_running
    assert float(manager._audio_level.cget('value')) == pytest.approx(.7)
    assert manager.popup.grab_current() is None


def test_real_recording_progress_localizes_without_callback_errors(controller):
    manager = controller.app.quick_translate_manager
    manager.show_audio_status('Preparing recording…', 'Japanese')
    controller.capture = SimpleNamespace(source='both', cancel=lambda: None)
    controller._progress('recording', 8, .6)
    assert '00:08' in manager._loading_label.cget('text')
    controller.capture = None


@pytest.mark.parametrize('action', ['no', 'escape', 'window_close'])
def test_save_question_defaults_to_discard(controller, action):
    files = media.AudioFiles()
    media.write_pcm(files.directory / 'source.wav', signal())
    files.tracks = [media.AudioTrack(str(files.directory / 'source.wav'), 'PC')]
    controller.files = files
    controller.request_close()
    dialog = controller.dialog
    assert dialog is not None and dialog.window.grab_current() is None
    if action == 'no':
        dialog._default.invoke()
    else:
        dialog.cancel()
    assert controller.files is None and not files.directory.exists()
    assert controller.config.get('audio_save_policy') == 'ask'


def test_dont_ask_again_remembers_no_and_settings_can_restore_ask(controller):
    controller.files = media.AudioFiles()
    controller.request_close()
    toggle = next(child for child in controller.dialog.body.winfo_children() if isinstance(child, ui.Toggle))
    toggle.invoke()
    controller.dialog._default.invoke()
    assert controller.config.get('audio_save_policy') == 'never'
    controller.config.set('audio_save_policy', 'ask')
    assert controller.config.get('audio_save_policy') == 'ask'


def test_save_yes_exports_wav_and_cleans_only_temporary_audio(controller, monkeypatch, tmp_path):
    files = media.AudioFiles()
    path = files.directory / 'source.wav'
    media.write_pcm(path, signal())
    files.tracks = [media.AudioTrack(str(path), 'PC')]
    controller.files = files
    destination = tmp_path / 'kept.wav'
    monkeypatch.setattr('src.ui.audio_controller.filedialog.asksaveasfilename', lambda **kw: str(destination))
    controller.request_close()
    controller.dialog._button_list[1].invoke()
    assert destination.exists() and not files.directory.exists()


def test_quick_result_hides_replace_and_offers_playback(controller):
    quick = controller.app.quick_translate_manager
    quick.show('Translation', 'Japanese', original='Original')
    quick.set_audio_result(True, Mock(), Mock())
    assert not quick.popup_replace_btn.winfo_manager()
    assert quick.audio_play_btn.winfo_manager()


def test_main_upload_runs_the_shared_pipeline_and_preserves_original(controller, monkeypatch, tmp_path):
    source = track(tmp_path)
    original_bytes = Path(source.path).read_bytes()
    controller.app.popup = ui.Frame(controller.root)
    controller.app.original_text = SimpleNamespace(get=lambda *args: '')
    controller.app.attachment_area = Mock()
    controller.app._start_translate_animation = Mock()
    controller.app._stop_translate_animation = Mock()
    controller.app._view = Mock()
    controller.app._update_translation_with_original = Mock()
    recognizer = SimpleNamespace(rows=rows(), cache={}, transcribe=lambda *args: 'Recognized upload')
    monkeypatch.setattr(controller, '_recognizer', lambda: recognizer)
    monkeypatch.setattr(controller, '_translate', lambda *args: ('Translated upload', 'fixture (Groq)'))
    controller.translate_file(source.path, 'Japanese', controller.app.popup)
    pump(controller.root, lambda: not controller.busy)
    assert controller.app._update_translation_with_original.call_args.args[:2] == ('Translated upload', 'Recognized upload')
    controller.app.attachment_area._remove_item.assert_called_once_with(source.path)
    assert Path(source.path).read_bytes() == original_bytes


def test_main_upload_does_not_overwrite_a_draft_edited_during_processing(controller):
    controller.app.popup = ui.Frame(controller.root)
    controller.main_owner = controller.app.popup
    controller.app.original_text = SimpleNamespace(get=lambda *args: 'A new draft')
    controller.app._stop_translate_animation = Mock()
    controller.app._update_translation_with_original = Mock()
    controller._upload_draft = 'Previous draft'
    controller.target = 'Japanese'
    controller._main_done(('Recognized', 'Translated', 'fixture (Groq)'), None)
    controller.app._update_translation_with_original.assert_not_called()
    assert controller.app.translation_service.history_manager.add_entry.called


def test_recording_stops_only_after_all_three_keys_release(controller, monkeypatch):
    from src.ui import audio_controller as module
    files = media.AudioFiles()
    path = files.directory / 'pc.wav'
    media.write_pcm(path, signal())
    files.tracks = [media.AudioTrack(str(path), 'PC')]
    stopped = threading.Event()

    class Capture:
        source = 'pc'
        limit_reached = False

        def __init__(self, *args):
            pass

        def stop(self):
            stopped.set()

        def cancel(self):
            stopped.set()

        def run(self, progress):
            progress('recording', 0, .5)
            assert stopped.wait(3)
            return files
    recognizer = SimpleNamespace(rows=rows(), transcribe=lambda *args: 'Recognized speech')
    monkeypatch.setattr(module, 'CaptureSession', Capture)
    monkeypatch.setattr(controller, '_recognizer', lambda: recognizer)
    monkeypatch.setattr(controller, '_translate', lambda *args: ('Translation', 'fixture (Groq)'))
    keys = [(True, True, True)]
    controller.app.hotkey_manager.audio_keys_down = lambda chord: keys[0]
    controller.start_recording()
    pump(controller.root, lambda: controller.state == 'recording')
    keys[0] = (False, True, True)
    controller._pump()
    assert not stopped.is_set()
    keys[0] = (False, False, False)
    pump(controller.root, lambda: controller.state == 'result')
    assert stopped.is_set() and controller.transcript == 'Recognized speech'
    assert controller.app.translation_service.history_manager.add_entry.call_count == 1


def test_audio_settings_and_capability_are_exposed(design):
    from src.ui.settings.pages_basic import TranslationPage
    from src.ui.settings.page_providers import ProvidersPage
    host = Host(design.root, SettingsConfig(api_keys=rows()))
    translation = TranslationPage(host)
    translation.build(design.root).pack()
    assert not hasattr(translation, 'audio_picker')
    assert 'audio_language' not in translation.rows
    translation.body.destroy()
    providers = ProvidersPage(host)
    providers.build(design.root).pack()
    assert providers.audio_picker.value == 'Auto'
    assert {'audio_language', 'audio_source', 'audio_save_policy', 'audio_devices', 'audio_model'} <= set(providers.rows)
    assert [chip.cget('text') for chip in providers.capability_chips][-1] == 'Reads audio'


def test_audio_self_check_never_opens_the_app_or_real_capture(monkeypatch, tmp_path):
    import main
    output = tmp_path / 'audio-check.json'
    monkeypatch.setattr(main.sys, 'argv', ['CrossTrans2', '--audio-self-check', str(output)])
    monkeypatch.setattr(main, 'is_already_running', Mock(side_effect=AssertionError('No instance-port access')))
    monkeypatch.setattr(main, 'setup_logging', Mock(side_effect=AssertionError('No profile/log access')))
    assert main.main() == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['errors'] == [] and result['samples'] == 64000


def test_retry_refreshes_keys_but_does_not_resend_recognized_audio(controller, monkeypatch):
    controller.files = media.AudioFiles()
    controller.transcript = 'Already recognized'
    controller.target = 'Japanese'
    controller.state = 'error'
    controller.recognizer = SimpleNamespace(cache={b'previous': []}, language='')
    fresh = SimpleNamespace(cache={}, language='', transcribe=Mock(side_effect=AssertionError('Do not resend audio')))
    refresh = Mock(return_value=fresh)
    monkeypatch.setattr(controller, '_recognizer', refresh)
    monkeypatch.setattr(controller, '_translate', lambda *args, **kwargs: ('New translation', 'new-key (Groq)'))
    controller.retry()
    pump(controller.root, lambda: controller.state == 'result')
    refresh.assert_called_once()
    fresh.transcribe.assert_not_called()


def test_cancelled_recognition_keeps_ui_owned_recording_for_save_choice(controller):
    files = media.AudioFiles()
    controller.files = files
    cancelled = threading.Event()
    capture = SimpleNamespace(run=lambda progress: files, limit_reached=False)

    def cancelled_recognition(*args):
        cancelled.set()
        raise media.AudioCancelled('cancelled')
    recognizer = SimpleNamespace(transcribe=cancelled_recognition)
    with pytest.raises(media.AudioCancelled):
        controller._capture_work(capture, recognizer, cancelled, lambda *args: None, False)
    assert files.directory.exists()


def test_capabilities_move_below_title_when_the_side_column_is_too_narrow(design):
    import tkinter as tk
    from src.ui.settings.page_providers import ProvidersPage
    window = tk.Toplevel(design.root)
    page = ProvidersPage(Host(window, SettingsConfig(api_keys=rows())))
    page.build(window).pack(fill='both', expand=True)
    design.root.update_idletasks()
    widest = max(chip.winfo_reqwidth() for chip in page.capability_chips)
    window.geometry(f'{widest + design.ctx.px(52)}x800')
    design.root.update()
    page._fit_capabilities()
    design.root.update_idletasks()
    assert page._capability_layout == 'below'
    assert all(chip.winfo_width() >= chip.winfo_reqwidth() for chip in page.capability_chips)
    window.destroy()


def test_cancel_custom_prompt_keeps_audio_actions_and_save_interception(controller):
    quick = controller.app.quick_translate_manager
    play = Mock()
    quick.show('Translation', 'Japanese', original='Original', model_info='fixture (Groq)',
               audio_result=True, on_audio_play=play)
    quick._handle_custom_prompt()
    quick._handle_custom_prompt_cancel()
    assert quick._audio_result is True
    assert quick.audio_play_btn.winfo_exists()
    assert not quick.popup_replace_btn.winfo_manager()
    assert quick._current_model_info == 'fixture (Groq)'


def test_resolve_wasapi_loopback_fallback():
    class DummyAudio:
        def get_default_wasapi_loopback(self):
            raise OSError(-9996, 'Invalid device info')

        def get_host_api_info_by_type(self, host_type):
            return dict(index=2)

        def get_device_count(self):
            return 3

        def get_device_info_by_index(self, index):
            devices = [
                dict(index=0, name='Mapper', hostApi=0, maxInputChannels=2, maxOutputChannels=0, isLoopbackDevice=False),
                dict(index=1, name='Headphones', hostApi=2, maxInputChannels=0, maxOutputChannels=2, isLoopbackDevice=False),
                dict(index=2, name='Headphones [Loopback]', hostApi=2, maxInputChannels=2, maxOutputChannels=0, isLoopbackDevice=True),
            ]
            return devices[index]

        def get_default_output_device_info(self):
            return dict(name='Headphones')

    audio = DummyAudio()
    resolved = media.resolve_wasapi_loopback(audio, None)
    assert resolved is not None
    assert resolved['index'] == 2
    assert resolved['isLoopbackDevice'] is True


def test_capture_dual_source_graceful_stream_failure():
    clock = [0.0]

    def now():
        clock[0] += 0.05
        return clock[0]

    class Stream:
        def __init__(self, callback):
            self.callback = callback

        def start_stream(self):
            self.callback(signal(0.1).tobytes(), 1600, {}, 0)

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
            if kwargs.get('input_device_index') == 2:
                raise OSError(-9985, 'Device unavailable')
            return Stream(kwargs['stream_callback'])

    backend = SimpleNamespace(PyAudio=Device, paInt16=8, paComplete=1, paContinue=0)
    capture = media.CaptureSession('both', limit=1, backend=backend, clock=now)
    files = capture.run(lambda *args: None)
    try:
        assert len(files.tracks) == 1
        assert files.tracks[0].source == 'PC'
    finally:
        files.cleanup()


def test_audio_shortcut_editor_in_audio_settings(design):
    import tkinter as tk
    from src.ui.settings.page_providers import ProvidersPage
    cfg = SettingsConfig(api_keys=rows())
    cfg.set('audio_hotkey', 'win+alt+a')
    window = tk.Toplevel(design.root)
    page = ProvidersPage(Host(window, cfg))
    page.build(window).pack(fill='both', expand=True)
    design.root.update_idletasks()
    assert hasattr(page.audio_section, 'shortcut_editor')
    assert page.audio_section.shortcut_editor.value == 'win+alt+a'
    page.audio_section._audio_shortcut_changed('win+alt+x')
    assert cfg.get('audio_hotkey') == 'win+alt+x'
    window.destroy()


def test_resolve_wasapi_loopback_shifted_index_and_name_matching():
    devices = [
        dict(index=0, name='Speakers', hostApi=0, maxInputChannels=0, maxOutputChannels=2, isLoopbackDevice=False),
        dict(index=8, name='Other Output', hostApi=2, maxInputChannels=0, maxOutputChannels=2, isLoopbackDevice=False),
        dict(index=9, name='Other Loopback', hostApi=2, maxInputChannels=2, maxOutputChannels=0, isLoopbackDevice=True),
        dict(index=10, name='soundcore Sleep A20 [Loopback]', hostApi=2, maxInputChannels=2, maxOutputChannels=0, isLoopbackDevice=True),
    ]

    class MockAudio:
        def get_device_count(self):
            return len(devices)

        def get_device_info_by_index(self, idx):
            if 0 <= idx < len(devices):
                return devices[idx]
            raise OSError(-9996, 'Invalid device')

        def get_host_api_info_by_type(self, host_type):
            return dict(index=2)

        def get_default_wasapi_loopback(self):
            raise OSError(-9996, 'Invalid device info')

        def get_default_output_device_info(self):
            return dict(name='Microsoft Sound Mapper - Output')

        def get_host_api_count(self):
            return 1

        def get_host_api_info_by_index(self, idx):
            return dict(defaultOutputDevice=0)

    audio = MockAudio()
    # 1. Stored index was 8 (which shifted to 'Other Output'), but target name was 'soundcore Sleep A20 [Loopback]'
    resolved = media.resolve_wasapi_loopback(audio, {'index': 8, 'name': 'soundcore Sleep A20 [Loopback]'})
    assert resolved['index'] == 10
    assert resolved['name'] == 'soundcore Sleep A20 [Loopback]'

    # 2. None (System default) fallback when get_default_wasapi_loopback fails
    # Returns the first valid loopback
    resolved_def = media.resolve_wasapi_loopback(audio, None)
    assert resolved_def is not None
    assert resolved_def['isLoopbackDevice'] is True


def test_dual_capture_preserves_valid_track_when_one_track_empty(tmp_path):
    clock_val = [0.0]

    def now():
        clock_val[0] += 0.05
        return clock_val[0]

    class Stream:
        def __init__(self, callback, has_data=True):
            self.callback = callback
            self.active = True
            self.has_data = has_data

        def is_active(self):
            return self.active

        def start_stream(self):
            if self.has_data:
                # Provide valid PCM audio samples
                sample = b'\x00\x10' * 512
                self.callback(sample, 512, None, 0)

        def stop_stream(self):
            self.active = False

        def close(self):
            self.active = False

    class Device:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_default_wasapi_loopback(self):
            return dict(index=1, maxInputChannels=2, defaultSampleRate=16000, isLoopbackDevice=True)

        def get_default_input_device_info(self):
            return dict(index=2, maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=False)

        def open(self, **kwargs):
            # Loopback (index 1) gets data; Mic (index 2) gets 0 data (empty track)
            has_data = (kwargs.get('input_device_index') == 1)
            return Stream(kwargs['stream_callback'], has_data=has_data)

    backend = SimpleNamespace(PyAudio=Device, paInt16=8, paComplete=1, paContinue=0)
    capture = media.CaptureSession('both', limit=1, backend=backend, clock=now)
    files = capture.run(lambda *args: None)
    try:
        assert len(files.tracks) == 1
        assert files.tracks[0].source == 'PC'
    finally:
        files.cleanup()


def test_dual_capture_skips_same_bluetooth_headset_mic():
    clock_val = [0.0]

    def now():
        clock_val[0] += 0.05
        return clock_val[0]

    class Stream:
        def __init__(self, callback):
            self.callback = callback
            self.active = True

        def is_active(self):
            return self.active

        def start_stream(self):
            self.callback(b'\x00\x10' * 512, 512, None, 0)

        def stop_stream(self):
            self.active = False

        def close(self):
            self.active = False

    def run_with_mic(mic_name):
        opened_indices = []

        class Device:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_default_wasapi_loopback(self):
                return dict(index=1, name='Headphones (soundcore Sleep A20) [Loopback]',
                            maxInputChannels=2, defaultSampleRate=16000, isLoopbackDevice=True)

            def get_default_input_device_info(self):
                return dict(index=2, name=mic_name,
                            maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=False)

            def open(self, **kwargs):
                opened_indices.append(kwargs.get('input_device_index'))
                return Stream(kwargs['stream_callback'])

        clock_val[0] = 0.0
        backend = SimpleNamespace(PyAudio=Device, paInt16=8, paComplete=1, paContinue=0)
        capture = media.CaptureSession('both', limit=1, backend=backend, clock=now)
        files = capture.run(lambda *args: None)
        try:
            return opened_indices, [track.source for track in files.tracks]
        finally:
            files.cleanup()

    opened_bt, sources_bt = run_with_mic('Headset (soundcore Sleep A20)')
    assert opened_bt == [1] and sources_bt == ['PC']

    opened_usb, sources_usb = run_with_mic('Microphone (USB Audio)')
    assert opened_usb == [1, 2] and sources_usb == ['PC', 'Microphone']



def test_auto_audio_mode_uses_main_api_keys_and_maps_speech_models(design, tmp_path):
    import tkinter as tk
    from src.core.speech_engines import resolve_audio_provider, select_audio_model
    from src.ui.attachments import AttachmentArea
    from src.ui.settings.page_providers import ProvidersPage

    cfg = SettingsConfig(
        api_keys=[
            dict(id='g1', provider='Groq', model_name='openai/gpt-oss-120b', api_key='gsk_main'),
            dict(id='m1', provider='Google', model_name='gemini-2.5-flash', api_key='AIzaMain'),
            dict(id='d1', provider='DeepSeek', model_name='deepseek-chat', api_key='sk-deepseek'),
        ],
        audio_keys=[],
    )
    window = tk.Toplevel(design.root)
    page = ProvidersPage(Host(window, cfg))
    page.build(window).pack(fill='both', expand=True)
    # Empty Auto card save preserves Auto mode (audio_keys == []) instead of masking main api_keys
    page.audio_section.save()
    assert cfg.get_audio_keys() == []
    assert not cfg.has_manual_audio_keys()
    assert cfg.has_usable_audio_keys()

    attach = AttachmentArea(window, cfg)
    assert attach._audio_available() is True

    app = SimpleNamespace(
        root=design.root, config=cfg,
        quick_translate_manager=QuickTranslateManager(design.root, cfg),
        translation_service=SimpleNamespace(key_usage_events=queue_events()),
        selected_language='Vietnamese', toast=Mock(),
        hotkey_manager=SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 1),
                                       audio_keys_down=lambda chord: (False,)),
    )
    ctrl = AudioController(app)
    rec = ctrl._recognizer()
    assert rec.manual_mode is False
    assert [r['id'] for r in rec.rows] == ['g1', 'm1']

    assert resolve_audio_provider('Auto', '', 'AIzaMain') == 'Google'
    assert select_audio_model('Groq', rec.rows[0], 'whisper-large-v3') == 'whisper-large-v3'
    assert select_audio_model('Google', rec.rows[1], 'whisper-large-v3') == 'gemini-2.5-flash'

    # Verified transcription in Auto mode skips non-speech chat model name cleanly
    seen = []
    def fake_transport(wav, key, model, language=''):
        seen.append((key, model))
        return {'text': 'hello'}
    rec.transport = fake_transport
    assert rec.transcribe([track(tmp_path)], threading.Event(), lambda *args: None) == 'hello'
    assert seen == [('gsk_main', 'whisper-large-v3')]

    ctrl.shutdown()
    app.quick_translate_manager.close()
    window.destroy()


def test_manual_audio_key_strictly_ignores_main_keys_on_failure_until_reset_to_auto(design, tmp_path):
    import tkinter as tk
    from src.ui.settings.page_providers import ProvidersPage

    cfg = SettingsConfig(
        api_keys=[dict(id='main1', provider='Groq', model_name='openai/gpt-oss-120b', api_key='gsk_working_main')],
        audio_keys=[dict(id='aud1', provider='Groq', model_name='whisper-large-v3',
                         api_key='gsk_broken_manual', is_audio_key=True)],
    )
    app = SimpleNamespace(
        root=design.root, config=cfg,
        quick_translate_manager=QuickTranslateManager(design.root, cfg),
        translation_service=SimpleNamespace(key_usage_events=queue_events()),
        selected_language='Vietnamese', toast=Mock(),
        hotkey_manager=SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 1),
                                       audio_keys_down=lambda chord: (False,)),
    )
    ctrl = AudioController(app)
    rec = ctrl._recognizer()
    assert rec.manual_mode is True
    assert [r['api_key'] for r in rec.rows] == ['gsk_broken_manual']

    called = []
    def failing_manual(wav, key, model, language=''):
        called.append(key)
        raise speech.SpeechRequestError(401, 'unauthorized')
    rec.transport = failing_manual
    with pytest.raises(speech.SpeechRequestError):
        rec.transcribe([track(tmp_path)], threading.Event(), lambda *args: None)
    assert called == ['gsk_broken_manual']

    # Even when tested and marked failed, manual mode never falls back to main keys
    cfg.set_audio_keys([dict(id='aud1', provider='Groq', model_name='whisper-large-v3',
                             api_key='gsk_broken_manual', is_audio_key=True, audio_capable=False)])
    ctrl.start_recording()
    assert app.toast.show_warning.called
    assert 'manual audio key' in app.toast.show_warning.call_args.args[0].lower()

    # Resetting primary card back to Auto restores automatic main-key usage
    window = tk.Toplevel(design.root)
    page = ProvidersPage(Host(window, cfg))
    page.build(window).pack(fill='both', expand=True)
    page.audio_section.remove_audio_card(page.audio_section.audio_cards[0])
    assert cfg.get_audio_keys() == []
    assert ctrl._recognizer().manual_mode is False
    assert [r['api_key'] for r in ctrl._recognizer().rows] == ['gsk_working_main']

    ctrl.shutdown()
    app.quick_translate_manager.close()
    window.destroy()


def test_capture_session_empty_devices_and_mic_fallback(tmp_path):
    class EmptyAudio:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get_default_wasapi_loopback(self):
            raise OSError(-9996, 'Invalid device')
        def get_default_input_device_info(self):
            raise OSError(-9996, 'Invalid device')
        def get_device_count(self):
            return 0
        def get_host_api_count(self):
            return 0

    cap = media.CaptureSession('both', backend=SimpleNamespace(PyAudio=EmptyAudio))
    try:
        with pytest.raises(media.AudioError) as caught:
            cap.run(lambda *args: None)
        assert 'Settings > Translation' not in str(caught.value)
        assert 'Settings > API keys' in str(caught.value)
    finally:
        cap.files.cleanup()

    # Microphone default missing fallback scans non-loopback input devices
    clock = [0.0]
    def now():
        clock[0] += 0.05
        return clock[0]

    class Stream:
        def __init__(self, cb):
            self.cb = cb
        def start_stream(self):
            self.cb(signal(0.1).tobytes(), 1600, {}, 0)
        def stop_stream(self):
            pass
        def close(self):
            pass
        def is_active(self):
            return True

    class ScanMicAudio:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get_default_input_device_info(self):
            raise OSError(-9996, 'No default input')
        def get_device_count(self):
            return 2
        def get_device_info_by_index(self, idx):
            return [
                dict(index=0, name='Loopback Only', maxInputChannels=2, defaultSampleRate=16000, isLoopbackDevice=True),
                dict(index=1, name='USB Mic', maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=False),
            ][idx]
        def open(self, **kwargs):
            assert kwargs['input_device_index'] == 1
            return Stream(kwargs['stream_callback'])

    cap2 = media.CaptureSession('microphone', limit=1, backend=SimpleNamespace(
        PyAudio=ScanMicAudio, paInt16=8, paComplete=1, paContinue=0), clock=now)
    files = cap2.run(lambda *args: None)
    try:
        assert [t.source for t in files.tracks] == ['Microphone']
    finally:
        files.cleanup()


def test_audio_section_shortcut_status_and_controller_rearms(design, controller):
    import tkinter as tk
    from src.ui.settings.page_providers import ProvidersPage

    host = Host(design.root, SettingsConfig(api_keys=rows()))
    host.status = {'__audio__': 'taken'}
    page = ProvidersPage(host)
    page.build(design.root).pack()
    assert 'another app' in page.audio_section.shortcut_editor.message_text.lower()
    host.status = {'__audio__': 'ok'}
    page.audio_section.refresh_status()
    assert page.audio_section.shortcut_editor.message_text == ''

    # Recover if _armed got left False while idle and chord keys are released
    controller._armed = False
    controller.busy = False
    controller.app.hotkey_manager.audio_keys_down = lambda chord: (False, False, False)
    controller.config.set('audio_enabled', False)
    controller.start_recording()
    assert controller.app.toast.show_warning.called

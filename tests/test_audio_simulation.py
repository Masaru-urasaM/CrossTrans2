"""The audio panel end to end on simulated hardware that works (CHANGELOG Phase 33).

Everything between the shortcut and the result is the real code: `TranslatorApp`'s shortcut routing, `AudioPanel`,
`AudioController`, `CaptureSession` with its threads, WAV files and decoding, `AudioRecognizer` and the requests
`speech_engines` builds. Only the edges are simulated (tests/audio_simulation.py): a Realtek speaker loopback and
microphone array that deliver packets in real time, and speech providers that answer under `urllib.request`
from the audio they receive. Real sockets are refused. Every test also reads the JSON Lines log it caused and
checks that no transcript, translation, key or file name is in it (Decision 86).
"""
import json
import logging
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.request
import wave

import numpy as np
import pytest

from src.core import audio_media as media
from src.ui import audio_controller as controller_module, audio_panel as panel_module, popup_layout
from src.ui.i18n import tr
from src.utils import logging_setup
from tests.audio_simulation import (FILE_VOICE, LOOPBACK, MIC_VOICE, MICROPHONE, MME_NAME_LENGTH, PC_VOICE,
                                    SimulatedCloud, SimulatedPortAudio, one_step_translation)
from tests.final_ui_helpers import design  # noqa: F401  (fixture)
from tests.test_audio_workflow import controller  # noqa: F401  (fixture)

GEMINI = [dict(id='gemini-card', provider='Google', model_name='gemini-2.5-flash', api_key='fixture-gemini-key')]
POINTER = (300, 200)
WAIT = 20                    # seconds: decoding and threads on a busy CI runner
SECRET_FILE = 'Quarterly-Plan-Confidential'


def text_translation(target, text):
    return f'[{target}] {text}'


def wait(root, ready, timeout=WAIT, message='the simulated recording did not get there in time'):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if ready():
            return
        time.sleep(0.01)
    assert ready(), message


def settle(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


@pytest.fixture
def sim(controller, monkeypatch, tmp_path):
    """The real panel and controller over simulated devices and providers, with the app's shortcut routing."""
    from src.app import TranslatorApp

    def no_network(self, *args):
        raise AssertionError('the simulation must not open a real connection')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    cloud = SimulatedCloud()
    monkeypatch.setattr(urllib.request, 'build_opener', cloud.build_opener)

    state = SimpleNamespace(devices=SimulatedPortAudio(), limit=media.QUICK_LIMIT, captures=[])

    class SimulatedCapture(media.CaptureSession):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault('backend', state.devices)
            kwargs.setdefault('limit', state.limit)
            super().__init__(*args, **kwargs)
            state.captures.append(self)
    monkeypatch.setattr(controller_module, 'CaptureSession', SimulatedCapture)

    translations = []
    service = controller.app.translation_service

    def translate_text(text, target, custom_prompt=None, source_language=None, allow_linked=True,
                       save_history=True):
        translations.append(dict(text=text, target=target, save_history=save_history))
        return text_translation(target, text)
    service.translate_text, service.last_attribution = translate_text, 'text-model (Groq)'

    root = controller.root
    monkeypatch.setattr(root, 'winfo_pointerx', lambda: POINTER[0])
    monkeypatch.setattr(root, 'winfo_pointery', lambda: POINTER[1])
    host = SimpleNamespace(config=controller.config, audio_controller=controller, root=root, settings_window=None,
                           _show_settings_tab=Mock())
    host._audio_panel = lambda: TranslatorApp._audio_panel(host)
    host._audio_panel_changed = lambda key: TranslatorApp._audio_panel_changed(host, key)

    seen = []
    controller.watchers.append(lambda phase, info: seen.append((phase, dict(info))))
    controller.config.set('audio_source', 'both')   # the hardware these tests cover; the default is PC audio only

    log_path = tmp_path / 'translator_20261117.log'
    handler = logging.FileHandler(log_path, encoding='utf-8')
    handler.setFormatter(logging_setup.JsonLinesFormatter())
    logger = logging.getLogger()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    run = SimpleNamespace(controller=controller, root=root, cloud=cloud, state=state, translations=translations,
                          seen=seen, host=host, log_path=log_path, tmp_path=tmp_path,
                          quick=controller.app.quick_translate_manager,
                          shortcut=lambda: TranslatorApp._on_audio_hotkey(host))
    try:
        yield run
    finally:
        panel = getattr(host, 'audio_panel', None)
        if controller.busy:
            controller.cancel()
            wait(root, lambda: not controller.busy, message='the recording did not stop at the end of the test')
        if controller.dialog is not None:
            controller.dialog.close()
            controller.dialog = None
        if panel is not None and panel.is_open():
            panel.window.destroy()
        # Cancel answers at once; the worker closes the devices and removes the recording just after
        wait(root, lambda: not (controller._thread and controller._thread.is_alive()) and state.devices.all_closed(),
             timeout=5, message='a cancelled recording kept a device open')
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous)
    assert state.devices.all_closed(), 'every simulated stream was stopped and closed'
    leaked = [capture.files.directory for capture in state.captures
              if capture.files.directory.exists() and capture.files is not controller.files]
    assert not leaked, f'temporary recordings were left behind: {leaked}'
    raw = log_path.read_text(encoding='utf-8')
    for secret in (PC_VOICE.line, MIC_VOICE.line, FILE_VOICE.line, 'fixture-first', 'fixture-second',
                   'fixture-gemini-key', SECRET_FILE):
        assert secret not in raw, f'the log holds user content or a key: {secret!r}'


def events(run, name=None):
    entries = [json.loads(line) for line in run.log_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    entries = [entry for entry in entries if entry.get('tag') == 'Audio' and entry.get('event')]
    return [entry for entry in entries if name is None or entry['event'] == name]


def flush(run):
    for handler in logging.getLogger().handlers:
        handler.flush()


def use_gemini(run):
    run.controller.config._config['audio_keys'] = [dict(row) for row in GEMINI]


def open_panel(run):
    run.shortcut()
    panel = run.host.audio_panel
    wait(run.root, lambda: panel.is_open() and panel.window.winfo_ismapped())
    return panel


def levels_seen(run):
    return [info['levels'] for phase, info in run.seen if phase == 'recording' and info.get('levels')]


def record(run, panel, seconds=1.2, start=None):
    """Press Record, let the devices deliver `seconds` of audio, press Stop and wait for the result."""
    (start or panel.record.invoke)()
    wait(run.root, lambda: run.controller.phase == 'recording' and levels_seen(run))
    settle(run.root, seconds)
    assert str(panel.record.cget('text')) == tr('Stop')
    panel.record.invoke()
    wait(run.root, lambda: run.controller.phase == 'idle' and run.quick.popup is not None
         and run.quick.popup.winfo_ismapped())
    flush(run)


class TestPanelRecording:
    def test_pc_and_microphone_two_step_from_the_shortcut_to_the_result_under_the_panel(self, sim):
        panel = open_panel(sim)
        assert panel.window.attributes('-topmost') in (1, True, '1')
        assert str(panel.status.cget('text')) == tr('Ready')
        record(sim, panel, start=sim.shortcut)          # the shortcut again: Record

        both = [levels for levels in levels_seen(sim) if set(levels) == {'PC', 'Microphone'}]
        assert both and max(levels['PC'] for levels in both) > .2 and max(levels['Microphone'] for levels in both) > .2
        assert set(panel.meters) == {'PC', 'Microphone'}

        history = sim.controller.app.translation_service.history_manager.add_entry
        assert history.call_count == 1
        transcript, translation, target = history.call_args.args
        assert PC_VOICE.line in transcript and MIC_VOICE.line in transcript, transcript
        assert translation == text_translation('Japanese', transcript) and target == 'Japanese'
        assert sim.translations == [dict(text=transcript, target='Japanese', save_history=False)]
        assert str(panel.status.cget('text')) == tr('Done')

        speech = sim.cloud.requests
        assert [request['kind'] for request in speech] == ['transcription', 'transcription']
        assert {request['voice'] for request in speech} == {PC_VOICE, MIC_VOICE}, 'each device reached its request'
        for request in speech:
            assert request['url'] == 'https://api.groq.com/openai/v1/audio/transcriptions'
            assert request['key'] == 'fixture-first' and 'fixture' not in request['url']
            assert (request['rate'], request['channels'], request['width']) == (16000, 1, 2)
            assert 1.0 < request['seconds'] < 4.0 and request['model'] == 'whisper-large-v3'

        left, top, width, height = panel.rect()
        popup = sim.quick.popup
        popup.update_idletasks()
        assert popup.winfo_rooty() >= top + height + popup_layout.ANCHOR_GAP - 1, 'the result opens under the panel'
        assert abs(popup.winfo_rootx() - left) <= 1, 'left edges aligned'

        start, = events(sim, 'session.start')
        assert start['data']['trigger'] == 'panel' and start['data']['source'] == 'both'
        stop, = events(sim, 'recording.stop')
        devices = {device['role']: device for device in stop['data']['devices']}
        assert devices['PC']['name'] == LOOPBACK, 'the WASAPI loopback, with its full name'
        assert devices['Microphone']['name'] == MICROPHONE[:MME_NAME_LENGTH], 'the default input is the MME one'
        assert devices['Microphone']['rate'] == 44100
        assert all(device['track'] and device['frames'] > 40000 and device['rms'] > .01 for device in devices.values())
        assert stop['data']['skipped'] == [] and stop['data']['tracks'] == 2
        mode, = events(sim, 'mode.used')
        assert (mode['data']['requested'], mode['data']['used']) == ('two_step', 'two_step')
        done, = events(sim, 'session.done')
        assert done['data']['outcome'] == 'ok' and done['data']['transcript_chars'] == len(transcript)

    def test_microphone_alone_meters_one_device(self, sim):
        sim.controller.config.set('audio_source', 'microphone')
        panel = open_panel(sim)
        assert tuple(panel.meters) == ('Microphone',)
        record(sim, panel)
        assert {tuple(levels) for levels in levels_seen(sim)} == {('Microphone',)}
        assert [request['voice'] for request in sim.cloud.requests] == [MIC_VOICE]
        transcript = sim.controller.app.translation_service.history_manager.add_entry.call_args.args[0]
        assert transcript == MIC_VOICE.line
        assert len(sim.state.devices.streams) == 1, 'the loopback is not opened for the microphone alone'

    def test_the_default_source_records_pc_audio_and_never_opens_the_microphone(self, sim):
        from config import Config
        sim.controller.config.set('audio_source', Config.DEFAULT_CONFIG['audio_source'])
        panel = open_panel(sim)
        assert panel.source.value == 'pc' and tuple(panel.meters) == ('PC',)
        record(sim, panel)
        assert {tuple(levels) for levels in levels_seen(sim)} == {('PC',)}
        assert [stream.device['isLoopbackDevice'] for stream in sim.state.devices.streams] == [True]
        assert [request['voice'] for request in sim.cloud.requests] == [PC_VOICE]
        transcript = sim.controller.app.translation_service.history_manager.add_entry.call_args.args[0]
        assert transcript == PC_VOICE.line


class TestOtherHardware:
    @pytest.mark.parametrize('hardware', [
        dict(speakers='Headphone (CORSAIR HS80 MAX WIRELESS Gaming Headset)', loopback_channels=8,
             microphone='マイク (Razer Seiren Mini)', mic_channels=2, mic_rate=48000),
        dict(speakers='Speakers (5.1 Surround)', loopback_channels=6, loopback_rate=44100,
             microphone='Microphone (Webcam C270)', mic_channels=1, mic_rate=16000),
        dict(speakers='スピーカー (Realtek(R) Audio)', microphone='マイク配列 (Realtek(R) Audio)'),
    ], ids=['7.1 wireless headset and a USB microphone', '5.1 speakers and a webcam microphone',
            'a Japanese laptop'])
    def test_pc_and_microphone_on_other_formats(self, sim, hardware):
        sim.state.devices = SimulatedPortAudio(**hardware)
        panel = open_panel(sim)
        record(sim, panel)
        transcript = sim.controller.app.translation_service.history_manager.add_entry.call_args.args[0]
        assert PC_VOICE.line in transcript and MIC_VOICE.line in transcript, transcript
        assert all((request['rate'], request['channels']) == (16000, 1) for request in sim.cloud.requests)
        stop, = events(sim, 'recording.stop')
        devices = {device['role']: device for device in stop['data']['devices']}
        channels = hardware.get('loopback_channels', 2)
        assert devices['PC']['channels'] == channels and devices['PC']['stored'] == (1 if channels > 2 else 2)
        assert devices['Microphone']['rate'] == 44100, 'opened as the MME default input'

    @pytest.mark.parametrize('speakers, microphone', [
        ('Headphones (soundcore Sleep A20)', 'Headset (soundcore Sleep A20 Hands-Free)'),
        ('ヘッドホン (soundcore Sleep A20)', 'ヘッドセット (soundcore Sleep A20)'),
        ('ヘッドホン (soundcore Sleep A20)', 'マイク (soundcore Sleep A20)'),
    ], ids=['Windows 10 English', 'Windows 11 Japanese', 'named as a plain microphone'])
    def test_the_playing_bluetooth_headset_keeps_its_microphone_closed(self, sim, speakers, microphone):
        sim.state.devices = SimulatedPortAudio(speakers=speakers, microphone=microphone, mic_channels=1,
                                               mic_rate=16000)
        panel = open_panel(sim)
        record(sim, panel)
        assert {tuple(levels) for levels in levels_seen(sim)} == {('PC',)}, 'one meter: the headset microphone is shut'
        assert len(sim.state.devices.streams) == 1
        transcript = sim.controller.app.translation_service.history_manager.add_entry.call_args.args[0]
        assert transcript == PC_VOICE.line
        stop, = events(sim, 'recording.stop')
        assert stop['data']['skipped'] == [{'role': 'Microphone', 'name': microphone[:MME_NAME_LENGTH],
                                            'reason': 'bluetooth_clash'}]


@pytest.mark.parametrize('playback, microphone, same', [
    ('Speakers (Realtek(R) Audio) [Loopback]', 'Microphone Array (Realtek(R) Audio)', False),
    ('Speakers (Realtek High Definition Audio) [Loopback]', 'Microphone (Realtek High Definition Audio)', False),
    ('Speakers (USB Audio Device) [Loopback]', 'Microphone (USB Audio Device)', False),
    ('Headphones (soundcore Sleep A20) [Loopback]', 'Headset (soundcore Sleep A20 Hands-Free)', True),
    ('Headphones (WH-1000XM4 Stereo) [Loopback]', 'Headset (WH-1000XM4 Hands-Free AG Audio)', True),
    ('Headphone (Gamer 7.1) [Loopback]', 'Headset Microphone (Gamer 7.1)', True),
    ('Headphones (soundcore Sleep A20) [Loopback]', 'Microphone (Razer Seiren Mini)', False),
    ('Speakers [Loopback]', 'Headset', False),
    # MME, whose default input pyaudiowpatch returns, cuts names to 31 characters
    ('Speakers (Realtek(R) Audio) [Loopback]', 'Microphone Array (Realtek(R) Au', False),
    ('Headphones (soundcore Sleep A20) [Loopback]', 'Headset (soundcore Sleep A20 Ha', True),
    ('Headphones (WH-1000XM4 Stereo) [Loopback]', 'Headset (WH-1000XM4 Hands-Free ', True),
    ('ヘッドホン (soundcore Sleep A20) [Loopback]', 'ヘッドセット (soundcore Sleep A20)', True),
    ('スピーカー (Realtek(R) Audio) [Loopback]', 'マイク配列 (Realtek(R) Audio)', False),
])
def test_only_the_playing_headsets_own_microphone_counts_as_the_same_headset(playback, microphone, same):
    assert media.same_headset(playback, microphone) is same


def test_the_hands_free_format_marks_a_headset_in_any_language():
    assert media.same_headset('Kopfhörer (soundcore Sleep A20) [Loopback]', 'Mikrofon (soundcore Sleep A20)',
                              hands_free=True)
    assert not media.same_headset('Speakers (Realtek(R) Audio) [Loopback]', 'Microphone (Webcam C270)',
                                  hands_free=True), 'a different device is never the same headset'


@pytest.mark.parametrize('channels, rate, expected', [(1, 16000, True), (1, 8000, True), (2, 48000, False),
                                                      (1, 48000, False), (2, 16000, False)])
def test_the_hands_free_format_is_read_from_the_microphones_wasapi_twin(channels, rate, expected):
    devices = SimulatedPortAudio(speakers='Headphones (soundcore Sleep A20)',
                                 microphone='Microphone (soundcore Sleep A20 Hands-Free)', mic_channels=channels,
                                 mic_rate=rate)
    with devices.PyAudio() as audio:
        default = audio.get_default_input_device_info()
        assert default['hostApi'] == 0 and len(default['name']) == MME_NAME_LENGTH
        assert media.hands_free_format(audio, default) is expected
        assert media.hands_free_format(audio, audio.get_device_info_by_index(4)) is expected, 'a WASAPI choice'


def test_without_a_wasapi_host_nothing_has_the_hands_free_format():
    audio = SimpleNamespace(get_host_api_info_by_type=Mock(side_effect=OSError(-9979, 'Host API not found')))
    assert media.hands_free_format(audio, dict(name='Headset (X)', maxInputChannels=1,
                                               defaultSampleRate=16000.0)) is False


class TestOneStep:
    def test_the_microphone_while_nothing_plays_is_translated_in_one_step(self, sim):
        use_gemini(sim)
        sim.controller.config.set('audio_mode', 'one_step')
        sim.state.devices.pc_voice = None               # nothing plays: the loopback sends no packets
        panel = open_panel(sim)
        record(sim, panel)
        recording = levels_seen(sim)
        assert all(levels['PC'] == 0.0 for levels in recording), 'a silent loopback shows no level'
        assert max(levels['Microphone'] for levels in recording) > .2
        transcript, translation, _ = sim.controller.app.translation_service.history_manager.add_entry.call_args.args
        assert transcript == MIC_VOICE.line
        assert translation == one_step_translation('Japanese', MIC_VOICE.line), 'the one-step reply is the result'
        assert sim.translations == [], 'no second call translates the text'
        request, = sim.cloud.requests
        assert request['kind'] == 'gemini' and request['key'] == 'fixture-gemini-key'
        assert 'translate it into Japanese' in request['prompt']
        stop, = events(sim, 'recording.stop')
        devices = {device['role']: device for device in stop['data']['devices']}
        assert devices['PC']['frames'] == 0 and not devices['PC']['track'] and devices['Microphone']['track']
        assert {'role': 'PC', 'name': LOOPBACK, 'reason': 'no_audio'} in stop['data']['skipped']
        mode, = events(sim, 'mode.used')
        assert (mode['data']['used'], mode['data']['reason'], mode['data']['sources']) == ('one_step', '', 1)

    def test_both_sources_with_speech_are_translated_from_the_joined_transcript(self, sim):
        use_gemini(sim)
        sim.controller.config.set('audio_mode', 'one_step')
        panel = open_panel(sim)
        assert str(panel.mode_note.cget('text')) == tr(panel_module.ONE_STEP_WITH_BOTH)
        record(sim, panel)
        transcript, translation, _ = sim.controller.app.translation_service.history_manager.add_entry.call_args.args
        assert PC_VOICE.line in transcript and MIC_VOICE.line in transcript
        assert translation == text_translation('Japanese', transcript), 'two steps, as the panel note says'
        assert sorted(request['kind'] for request in sim.cloud.requests) == ['gemini', 'gemini']
        mode, = events(sim, 'mode.used')
        assert (mode['data']['used'], mode['data']['reason']) == ('two_step', 'several_sources')


class TestHoldToRecord:
    def test_the_held_shortcut_records_until_every_key_is_released(self, sim):
        sim.controller.config.set('audio_hotkey_action', 'hold')
        keys = SimpleNamespace(down=True)
        sim.controller.app.hotkey_manager.audio_keys_down = lambda chord: (keys.down,) * 3
        sim.shortcut()
        assert getattr(sim.host, 'audio_panel', None) is None, 'holding does not open the panel'
        wait(sim.root, lambda: sim.controller.state == 'recording' and levels_seen(sim))
        shown = []                                       # the meter follows the syllables: watch it a while
        end = time.monotonic() + 1.2
        while time.monotonic() < end:
            sim.root.update()
            if sim.quick._audio_status:
                shown.append(float(sim.quick._audio_level.cget('value')))
            time.sleep(0.01)
        assert shown and max(shown) > .2, f'the status popup meters the recording: {max(shown, default=None)}'
        keys.down = False
        wait(sim.root, lambda: sim.controller.phase == 'idle' and sim.quick.popup is not None
             and not sim.quick._audio_status and sim.quick.popup.winfo_ismapped())
        transcript = sim.controller.app.translation_service.history_manager.add_entry.call_args.args[0]
        assert PC_VOICE.line in transcript and MIC_VOICE.line in transcript
        flush(sim)
        start, = events(sim, 'session.start')
        assert start['data']['trigger'] == 'hold'


class TestStopping:
    def test_cancel_stops_the_devices_and_sends_nothing(self, sim):
        panel = open_panel(sim)
        panel.record.invoke()
        wait(sim.root, lambda: sim.controller.phase == 'recording' and levels_seen(sim))
        capture = sim.state.captures[-1]
        settle(sim.root, .5)
        panel.cancel_button.invoke()
        wait(sim.root, lambda: sim.controller.phase == 'idle' and not sim.controller.busy)
        wait(sim.root, lambda: sim.state.devices.all_closed() and not capture.files.directory.exists())
        assert sim.cloud.requests == [] and sim.quick.popup is None
        assert str(panel.status.cget('text')) == tr('Audio translation was cancelled.')
        assert panel.close(), 'an idle panel closes'
        flush(sim)
        done, = events(sim, 'session.done')
        assert done['data']['outcome'] == 'cancelled'

    def test_the_limit_ends_the_recording_and_it_is_still_translated(self, sim):
        sim.state.limit = 1.0
        panel = open_panel(sim)
        panel.record.invoke()
        wait(sim.root, lambda: sim.controller.phase == 'idle' and sim.quick.popup is not None
             and sim.quick.popup.winfo_ismapped())
        sim.controller.app.toast.show_warning.assert_any_call(
            'The 3-minute recording limit was reached. Translating the recording.')
        transcript = sim.controller.app.translation_service.history_manager.add_entry.call_args.args[0]
        assert PC_VOICE.line in transcript and MIC_VOICE.line in transcript
        flush(sim)
        stop, = events(sim, 'recording.stop')
        assert stop['data']['limit_reached'] and 0.95 <= stop['data']['seconds'] <= 1.05


class TestProviderFailures:
    def test_a_rate_limited_key_hands_the_chunk_to_the_next_key(self, sim):
        sim.controller.config.set('audio_source', 'microphone')
        sim.cloud.fail = [(429, 7)]
        panel = open_panel(sim)
        record(sim, panel)
        assert [(request['key'], request['status']) for request in sim.cloud.requests] == [
            ('fixture-first', 429), ('fixture-second', 200)]
        assert str(panel.status.cget('text')) == tr('Done')
        failure, = events(sim, 'stt.fail')
        assert failure['data']['status'] == 429 and failure['data']['key_slot'] == 1
        chunk, = events(sim, 'stt.chunk')
        assert chunk['data']['key_slot'] == 2

    def test_every_key_rate_limited_shows_the_wait_on_the_panel(self, sim):
        sim.controller.config.set('audio_source', 'microphone')
        sim.cloud.fail = [(429, 7), (429, 7)]
        panel = open_panel(sim)
        record(sim, panel)
        message = tr('Speech recognition quota or rate limit was reached. Try again in 7 seconds.')
        assert str(panel.status.cget('text')) == message and panel.status_icon.cget('image')
        assert sim.controller.app.translation_service.history_manager.add_entry.call_count == 0
        assert sim.controller.files is not None, 'the recording is kept for Retry and Play'
        flush(sim)
        assert [entry['data']['status'] for entry in events(sim, 'stt.fail')] == [429, 429]
        done, = events(sim, 'session.done')
        assert done['level'] == 'WARNING' and done['data']['outcome'] == 'error'


class TestRecordingAgain:
    def test_the_save_question_opens_over_the_panel_and_yes_saves_both_channels(self, sim, monkeypatch):
        panel = open_panel(sim)
        record(sim, panel)
        destination = sim.tmp_path / 'saved.wav'
        asked = []

        def ask(**options):
            asked.append(options)
            return str(destination)
        monkeypatch.setattr(controller_module.filedialog, 'asksaveasfilename', ask)
        panel.record.invoke()                            # Record again: the first recording needs an answer
        dialog = sim.controller.dialog
        assert dialog is not None and len(sim.state.captures) == 1
        wait(sim.root, lambda: dialog.window.winfo_ismapped())
        settle(sim.root, .3)                             # past the moment a dialog leaves the topmost band
        assert dialog.window.attributes('-topmost') in (1, True, '1')
        left, top, width, height = panel.rect()
        centre_x = dialog.window.winfo_rootx() + dialog.window.winfo_width() / 2
        centre_y = dialog.window.winfo_rooty() + dialog.window.winfo_height() / 2
        assert left <= centre_x <= left + width and top <= centre_y <= top + height, 'over the panel'
        dialog_buttons = [widget for widget in _descendants(dialog.window) if _text(widget) == tr('Yes')]
        dialog_buttons[0].invoke()
        assert asked and asked[0]['parent'] is panel.window, 'the save-as box belongs to the panel'
        with wave.open(str(destination), 'rb') as saved:
            assert saved.getnchannels() == 2 and saved.getframerate() == media.RATE
            data = np.frombuffer(saved.readframes(saved.getnframes()), '<i2').reshape(-1, 2)
        assert data[:, 0].any() and data[:, 1].any(), 'PC left, microphone right'
        wait(sim.root, lambda: len(sim.state.captures) == 2 and sim.controller.phase == 'recording')
        panel.cancel_button.invoke()
        wait(sim.root, lambda: sim.controller.phase == 'idle' and not sim.controller.busy)


class TestFileFromThePanel:
    def test_a_video_file_is_translated_and_its_result_opens_under_the_panel(self, sim, monkeypatch):
        import av
        source = sim.tmp_path / f'{SECRET_FILE}.mp4'
        with av.open(str(source), 'w') as container:
            stream = container.add_stream('aac', rate=16000)
            stream.layout = 'mono'
            samples = FILE_VOICE.samples(0, 2 * media.RATE, media.RATE).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format='s16', layout='mono')
            frame.sample_rate = 16000
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        before = source.read_bytes()
        monkeypatch.setattr(panel_module.filedialog, 'askopenfilename', lambda **options: str(source))
        panel = open_panel(sim)
        panel.file_button.invoke()
        wait(sim.root, lambda: sim.controller.phase == 'idle' and sim.quick.popup is not None
             and sim.quick.popup.winfo_ismapped())
        transcript, translation, _ = sim.controller.app.translation_service.history_manager.add_entry.call_args.args
        assert transcript == FILE_VOICE.line and translation == text_translation('Japanese', FILE_VOICE.line)
        assert source.read_bytes() == before, 'the original is only read'
        assert sim.controller.files is None, 'nothing to play or save for a file'
        left, top, width, height = panel.rect()
        assert sim.quick.popup.winfo_rooty() >= top + height + popup_layout.ANCHOR_GAP - 1
        assert len(sim.state.devices.streams) == 0, 'no device is opened for a file'
        flush(sim)
        decode, = events(sim, 'file.decode')
        assert decode['data']['ext'] == '.mp4' and 1.5 < decode['data']['seconds'] < 2.5


class TestTheLogReport:
    def test_the_report_reads_what_a_panel_recording_and_a_file_wrote(self, sim, monkeypatch):
        from tools import log_report
        use_gemini(sim)
        sim.controller.config.set('audio_mode', 'one_step')
        sim.state.devices.pc_voice = None
        panel = open_panel(sim)
        record(sim, panel)
        sim.controller.config.set('audio_source', 'pc')
        sim.state.devices.pc_voice = PC_VOICE
        sim.state.devices.mic_voice = None
        sim.controller.config.set('audio_save_policy', 'never')     # Record again discards the first one
        panel.sync_from_config()
        record(sim, panel)
        entries, bad = log_report.read_entries([str(sim.log_path)])
        summary = log_report.summarize(entries, bad, [str(sim.log_path)])
        assert bad == 0
        sessions = [session for session in summary['audio_sessions'] if session.get('done')]
        assert [session['start']['trigger'] for session in sessions] == ['panel', 'panel']
        assert [session['mode']['used'] for session in sessions] == ['one_step', 'one_step']
        assert [session['recording']['tracks'] for session in sessions] == [1, 1]
        report = log_report.render(summary)
        assert 'mode=one_step->one_step' in report and LOOPBACK in report


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _text(widget):
    try:
        return str(widget.cget('text'))
    except Exception:
        return ''

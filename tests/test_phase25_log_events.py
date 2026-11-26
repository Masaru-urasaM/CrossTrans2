"""Phase 25: API calls, screenshots and attachments in the log, the screenshot clean-up, and an honest
one-step promise (CHANGELOG Phase 25, Decision 86).

Each event must say enough to explain a result - which key slot and model answered, how long it took, why a
file was refused - and nothing the user wrote, captured or named.
"""
import json
import logging
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from src.core import audio_service, screenshot, speech_engines
from src.core.api_manager import AIAPIManager, ProviderRequestError
from src.ui import attachments as A
from src.ui.screenshot_handler import ScreenshotHandler
from src.ui.settings.audio_settings import AudioSection
from src.utils import logging_setup
from tests.test_attachments import FakeConfig, area, files, no_dialogs  # noqa: F401  (fixtures)
from tests.test_components import env, holder, light  # noqa: F401  (fixtures)


@pytest.fixture
def events():
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


class TestApiCalls:
    @staticmethod
    def manager(monkeypatch, outcomes):
        manager = AIAPIManager()
        manager.configure([dict(id='k1', api_key='fixture-one', model_name='openai/gpt-oss-120b', provider='Groq'),
                           dict(id='k2', api_key='fixture-two', model_name='qwen/qwen3.8-27b', provider='Groq')])
        answers = iter(outcomes)

        def generate(provider, key, model, prompt, image_path=None):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(manager, '_generate_content', generate)
        monkeypatch.setattr(manager, '_try_model_rotation', lambda *args, **kwargs: None)
        return manager

    def test_a_failed_key_and_the_key_that_answered_are_logged_by_slot(self, monkeypatch, events):
        manager = self.manager(monkeypatch, [ProviderRequestError(401, 'Invalid API Key'), 'Xin chao'])
        assert manager.translate('PRIVATE SENTENCE') == 'Xin chao'
        fail, = named(events, 'call.fail')
        ok, = named(events, 'call.ok')
        assert fail['level'] == 'WARNING'
        assert (fail['data']['key_slot'], fail['data']['status'], fail['data']['model']) == (1, 401, 'openai/gpt-oss-120b')
        assert (ok['data']['key_slot'], ok['data']['provider'], ok['data']['kind']) == (2, 'Groq', 'text')
        assert isinstance(ok['data']['ms'], int) and ok['data']['rotated'] is False
        written = json.dumps(events)
        assert 'PRIVATE SENTENCE' not in written and 'fixture-two' not in written, 'no text, no key'

    def test_the_generation_line_is_a_structured_event_that_reads_as_before(self, events):
        from src.core.generation_result import check_generation_result
        check_generation_result({'choices': [{'finish_reason': 'stop', 'message': {'content': 'PRIVATE BODY'}}],
                                 'usage': {'prompt_tokens': 200, 'completion_tokens': 12}})
        line, = named(events, 'result')
        assert line['tag'] == 'Generation' and line['msg'].startswith('result finish_reason=stop')
        assert line['data'] == {'finish_reason': 'stop', 'usage': {'prompt_tokens': 200, 'completion_tokens': 12}}
        assert 'PRIVATE BODY' not in json.dumps(line)


class TestScreenshotFiles:
    def test_captures_use_the_app_prefix(self):
        assert screenshot.SHOT_PREFIX == 'CrossTrans2-shot-'

    def test_the_sweep_removes_only_this_apps_screenshots(self, tmp_path):
        ours = tmp_path / (screenshot.SHOT_PREFIX + 'abc.png')
        attached = tmp_path / (screenshot.SHOT_PREFIX + 'attached-1700000000.png')
        theirs = tmp_path / 'tmpabcdef12.png'
        other = tmp_path / 'CrossTrans2-notes.png'
        for path in (ours, attached, theirs, other):
            path.write_bytes(b'png')
        assert screenshot.sweep_old_shots(str(tmp_path)) == 2
        assert sorted(p.name for p in tmp_path.iterdir()) == ['CrossTrans2-notes.png', 'tmpabcdef12.png']

    @staticmethod
    def handler():
        handler = ScreenshotHandler.__new__(ScreenshotHandler)
        handler._pending_screenshot_path = None
        handler._screenshot_target_language = 'Vietnamese'
        handler._get_quick_translate_manager = None
        handler._get_selected_language = None
        handler._run_translation = lambda *args: None
        handler.translation_service = SimpleNamespace(api_manager=SimpleNamespace(last_attribution=None))
        return handler

    def test_a_new_capture_removes_the_one_it_replaces_and_logs_size_not_path(self, tmp_path, events):
        first, second = tmp_path / 'CrossTrans2-shot-first.png', tmp_path / 'CrossTrans2-shot-second.png'
        Image.new('RGB', (120, 40)).save(first)
        Image.new('RGB', (300, 90)).save(second)
        handler = self.handler()
        handler._on_screenshot_captured(str(first))
        handler._on_screenshot_captured(str(second))
        assert not first.exists() and second.exists()
        assert handler.get_pending_screenshot() == str(second)
        sizes = [(line['data']['width'], line['data']['height']) for line in named(events, 'captured')]
        assert sizes == [(120, 40), (300, 90)]
        assert str(tmp_path) not in json.dumps(events), 'where the screenshot is stays out of the log'

    def test_dismissing_the_popup_deletes_the_kept_screenshot(self, tmp_path):
        from src.app import TranslatorApp
        kept = tmp_path / 'CrossTrans2-shot-kept.png'
        kept.write_bytes(b'png')
        handler = self.handler()
        handler._pending_screenshot_path = str(kept)
        app = SimpleNamespace(screenshot_handler=handler, _cancel_quick_translation=lambda: None)
        app._cleanup_pending_screenshot = lambda: TranslatorApp._cleanup_pending_screenshot(app)
        TranslatorApp._on_quick_translate_dismissed(app)
        assert not kept.exists() and handler.get_pending_screenshot() is None


class TestAttachments:
    def test_an_added_image_is_logged_by_kind_and_size(self, area, files, events):
        widget = area()
        assert widget.add_file(files.image)
        added, = named(events, 'added')
        assert (added['tag'], added['data']['ext'], added['data']['kind'], added['data']['count']) == (
            'Attachments', '.png', 'image', 1)
        assert 'menu' not in json.dumps(events), 'the file name is the user\'s'

    def test_a_refused_image_says_why(self, area, files, events):
        widget = area(vision=False)
        assert not widget.add_file(files.image)
        rejected, = named(events, 'rejected')
        assert (rejected['data']['ext'], rejected['data']['reason']) == ('.png', 'no_vision_key')

    def test_an_unreadable_image_logs_why_it_has_no_thumbnail(self, area, files, events):
        broken = files.dir / 'broken.png'
        broken.write_bytes(b'not a picture')
        widget = area()
        assert widget.add_file(str(broken))
        fail, = named(events, 'thumbnail.fail')
        assert fail['level'] == 'WARNING' and fail['data']['ext'] == '.png'
        assert fail['data']['error'] == 'UnidentifiedImageError'


class TestOneStepTruth:
    @pytest.mark.parametrize('provider, model, expected', [
        ('Google', 'gemini-2.5-flash', True),
        ('Auto', 'gemini-2.5-flash', True),
        ('Groq', 'whisper-large-v3', False),
        ('OpenRouter', 'google/gemini-2.5-flash', False),
        ('OpenAI', 'gpt-4o-audio-preview', False),
    ])
    def test_one_rule_for_what_runs_as_one_step(self, provider, model, expected):
        assert speech_engines.runs_one_step(provider, model) is expected

    def test_the_test_button_no_longer_promises_one_step_for_a_model_that_transcribes(self):
        transport = lambda *args: {'text': 'And so my fellow Americans, ask not'}
        outcome = speech_engines.probe_speech('fixture', 'OpenAI', 'gpt-4o-audio-preview', transport=transport)
        assert outcome.supported is True and outcome.one_step is False
        gemini = speech_engines.probe_speech('fixture', 'Google', 'gemini-2.5-flash', transport=transport)
        assert gemini.one_step is True

    @staticmethod
    def config(mode, audio_keys):
        values = {'audio_mode': mode, 'audio_model': 'whisper-large-v3'}
        return SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                               get_audio_keys=lambda: audio_keys, get_api_keys=lambda: [],
                               has_manual_audio_keys=True)

    def test_the_outlook_of_a_whisper_key_and_of_a_gemini_key(self, monkeypatch):
        monkeypatch.setattr(audio_service, 'has_manual_audio_keys', lambda config: True)
        whisper = [dict(provider='Groq', model_name='whisper-large-v3', api_key='fixture', is_audio_key=True)]
        gemini = [dict(provider='Google', model_name='gemini-2.5-flash', api_key='fixture', is_audio_key=True)]
        assert audio_service.one_step_outlook(self.config('one_step', whisper)) == 'engine_without_one_step'
        assert audio_service.one_step_outlook(self.config('one_step', gemini)) == ''
        assert audio_service.one_step_outlook(self.config('one_step', [])) == 'no_key'

    def test_settings_show_the_note_only_when_one_step_would_run_as_two(self, monkeypatch):
        monkeypatch.setattr(audio_service, 'has_manual_audio_keys', lambda config: True)
        calls = []
        note = SimpleNamespace(configure=lambda **kw: calls.append(('text', kw['text'])),
                               pack=lambda **kw: calls.append(('pack',)), pack_forget=lambda: calls.append(('hide',)))
        whisper = [dict(provider='Groq', model_name='whisper-large-v3', api_key='fixture', is_audio_key=True)]
        section = SimpleNamespace(mode_note=note, config=self.config('one_step', whisper))
        AudioSection._refresh_mode_note(section)
        assert calls[0][0] == 'text' and 'Google Gemini' in calls[0][1] and calls[1] == ('pack',)
        calls.clear()
        section.config = self.config('two_step', whisper)
        AudioSection._refresh_mode_note(section)
        assert calls == [('hide',)]

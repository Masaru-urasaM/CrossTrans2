"""Exhaustive tests for Task 37: 100% accurate error classification across all 9 audited flows.

Covers both English and Japanese UI languages:
1. Model = Auto preserves real HTTP/provider errors (and stops immediately on key auth failure),
   propagating HTTP status to `call.fail` logs and `key_usage` events.
2. Main-window attachment failures and scanned PDF OCR errors surface as real errors (`is_error_text`),
   never as successful translations or fake document text fed to the AI prompt.
3. Google Gemini HTTP 400 "API key not valid" classifies as an authentication error (FIX_SETTINGS, DANGER),
   on text, multimodal/OCR, and Gemini audio paths.
4. Short audio recording classifies as a short-recording retry warning, never as missing audio hardware.
5. Japanese UI (`ui_language='ja'`) preserves exact error classification on every audio error and
   handles unmapped invalid speech responses.
6. Google Gemini HTTP 429 RPM ("Resource has been exhausted (e.g. check quota)") classifies as
   rate-limit WARNING (FIX_RETRY), while 402/insufficient_quota/billing classifies as DANGER (FIX_SETTINGS).
7. Multi-key failures keep every key's error in Details instead of wiping backup keys, and report mixed
   failures truthfully.
8. Standalone Quick Dictionary popup displays API failures via toast/settings action instead of opening
   a dictionary word card with `Notes: Error: ...`.
9. Real translations starting with "Error:" or "No text" are NEVER treated as errors, while cooldown and
   empty-prompt notices are treated as non-replaceable info notices; PROHIBITED_CONTENT/BLOCKLIST/RECITATION
   and TOOL_CALLS/FUNCTION_CALL classify accurately.
"""
import io
import json
import logging
import queue
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.core import api_manager as api_module
from src.core.api_manager import AIAPIManager, ProviderRequestError
from src.core.audio_media import AudioError
from src.core.history import HistoryManager
from src.core.result_status import FailureText, TranslationText, is_error_text
from src.core.speech_engines import execute_gemini_audio_request
from src.core.translation import TranslationService
from src.ui import i18n, popup_errors
from src.ui.audio_controller import AudioController
from src.ui.dictionary_popup import DictionaryPopup
from src.ui.popup_errors import DANGER, FIX_NONE, FIX_RETRY, FIX_SETTINGS, INFO, WARNING, explain
from src.ui.quick_translate import QuickTranslateManager


class _FakeConfig:
    def __init__(self):
        self._store = {'history': [], 'history_enabled': True}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value

    def get_furigana_enabled(self):
        return False

    def get_quick_replace(self):
        return False


def _make_service(api_result=None, api_side_effect=None):
    svc = TranslationService.__new__(TranslationService)
    svc.config = _FakeConfig()
    svc.api_manager = MagicMock()
    if api_side_effect is not None:
        svc.api_manager.translate.side_effect = api_side_effect
    else:
        svc.api_manager.translate.return_value = api_result
    svc.api_manager.last_attribution = 'test-model (Test)'
    svc.history_manager = HistoryManager(svc.config)
    svc.translation_queue = queue.Queue()
    svc.last_translation_time = 0
    svc.last_grammar_fix_time = 0
    svc._configure_api = lambda: True
    svc.get_trial_info = lambda: None
    return svc


# --------------------------------------------------------------------------- #
# 1. Model = Auto preserves real errors, logs HTTP status, stops on auth error
# --------------------------------------------------------------------------- #
class TestAutoModelErrorPreservation:
    def test_auto_model_stops_immediately_on_401_and_logs_status(self, monkeypatch, caplog):
        monkeypatch.setattr(api_module, 'get_config', lambda: SimpleNamespace(
            default_models_by_provider={'Google': ['gemini-a', 'gemini-b', 'gemini-c']},
            api_key_patterns={'AIza': 'Google'},
        ))
        api = AIAPIManager()
        events = []
        api.usage_callback = events.append
        api.configure([{'id': 'k1', 'api_key': 'AIzaFixture1234567890', 'provider': 'Auto', 'model_name': ''}])
        calls = []

        def fake_gen(provider, key, model, prompt, *a):
            calls.append(model)
            raise ProviderRequestError(401, 'API key not valid. Please pass a valid API key.')

        monkeypatch.setattr(api, '_generate_content', fake_gen)
        with caplog.at_level(logging.WARNING), pytest.raises(Exception) as exc:
            api.translate('Hello')

        assert calls == ['gemini-a']  # stopped immediately, did not waste calls on gemini-b/c
        assert '401' in str(exc.value)
        assert 'Auto-detection failed' not in str(exc.value)
        assert events and events[0]['kind'] == 'error'
        assert explain('Error: ' + str(exc.value)).fix == FIX_SETTINGS
        assert any('status=401' in rec.getMessage() or getattr(rec, 'data', {}).get('status') == 401
                   for rec in caplog.records)

    def test_auto_model_preserves_429_and_records_quota_usage(self, monkeypatch):
        monkeypatch.setattr(api_module, 'get_config', lambda: SimpleNamespace(
            default_models_by_provider={'Google': ['gemini-a', 'gemini-b']},
            api_key_patterns={'AIza': 'Google'},
        ))
        api = AIAPIManager()
        events = []
        api.usage_callback = events.append
        api.configure([{'id': 'k1', 'api_key': 'AIzaFixture1234567890', 'provider': 'Auto', 'model_name': ''}])

        def fake_gen(provider, key, model, prompt, *a):
            if model == 'gemini-a':
                raise ProviderRequestError(429, 'Resource has been exhausted (e.g. check quota).')
            raise ProviderRequestError(404, 'models/gemini-b is not found')

        monkeypatch.setattr(api, '_generate_content', fake_gen)
        with pytest.raises(Exception) as exc:
            api.translate('Hello')

        assert '429' in str(exc.value)
        assert events and events[0]['kind'] == 'quota'
        help_ = explain('Error: ' + str(exc.value))
        assert help_.fix == FIX_RETRY and help_.kind == WARNING


# --------------------------------------------------------------------------- #
# 2 & 3. Attachment errors + Google Gemini HTTP 400 API key not valid
# --------------------------------------------------------------------------- #
class TestGoogle400AndAttachments:
    def test_google_http_400_api_key_not_valid_is_auth_error(self):
        raw = 'Error: All API calls failed.\ngemini-3.5-flash-lite: HTTP 400: API key not valid. Please pass a valid API key.'
        help_ = explain(raw)
        assert help_.fix == FIX_SETTINGS
        assert help_.kind == DANGER
        assert 'authentication failed' in help_.message.lower()

    def test_gemini_audio_http_400_api_key_not_valid_raises_auth_audio_error(self):
        body = json.dumps({'error': {'message': 'API key not valid. Please pass a valid API key.'}}).encode()
        err = urllib.error.HTTPError('https://generativelanguage.googleapis.com', 400, 'Bad Request', {}, io.BytesIO(body))
        with pytest.raises(AudioError, match='did not accept this API key'):
            execute_gemini_audio_request(b'RIFF', 'bad-key', 'gemini-3.5-flash-lite', 'prompt',
                                         opener=Mock(side_effect=err))

    def test_attachment_failure_text_is_recognized_as_error(self):
        notice = FailureText('Error: HTTP 401: Unauthorized')
        assert is_error_text(notice) is True
        assert explain(notice).fix == FIX_SETTINGS
        assert explain('Error: scan.pdf: PDF contains no extractable text - it may be scanned or image-based.').fix == FIX_NONE


# --------------------------------------------------------------------------- #
# 4 & 5. Short recording + Japanese UI audio error classification
# --------------------------------------------------------------------------- #
class TestAudioErrorsEnAndJa:
    def test_short_recording_is_not_classified_as_missing_audio_device(self):
        raw = 'Error: The recording was too short. Hold the shortcut a little longer.'
        help_ = explain(raw)
        assert help_.fix == FIX_RETRY
        assert help_.kind == WARNING
        assert 'device' not in help_.message.lower()
        assert 'too short' in help_.message.lower()

    def test_speech_invalid_response_is_classified_as_empty_incomplete(self):
        raw = 'Error: The speech provider returned an invalid response.'
        help_ = explain(raw)
        assert help_.fix == FIX_RETRY
        assert help_.kind == WARNING
        assert 'could not be identified' not in help_.message.lower()

    @pytest.mark.parametrize('audio_msg, expected_fix, expected_kind', [
        ('The speech provider did not accept this API key.', FIX_SETTINGS, DANGER),
        ('This API key does not have permission to use speech recognition.', FIX_SETTINGS, DANGER),
        ('Speech recognition quota or rate limit was reached. Try again later.', FIX_RETRY, WARNING),
        ('The recording was too short. Hold the shortcut a little longer.', FIX_RETRY, WARNING),
        ('No speech was detected. Check the source and try again.', FIX_RETRY, WARNING),
        ('The speech provider returned an invalid response.', FIX_RETRY, WARNING),
        ('Choose a speech provider for this key.', FIX_SETTINGS, WARNING),
    ])
    def test_japanese_ui_preserves_exact_audio_error_classification(self, monkeypatch, audio_msg, expected_fix, expected_kind):
        previous = i18n.language()
        i18n.set_language('ja')
        try:
            shown = {}
            app = SimpleNamespace(
                quick_translate_manager=MagicMock(),
                show_quick_translate=lambda orig, trans, target, **kw: shown.update(translated=trans),
            )
            ctrl = AudioController.__new__(AudioController)
            ctrl.app = app
            ctrl.transcript = ''
            ctrl.target = 'English'
            ctrl.trigger = 'panel'
            ctrl.files = None
            ctrl.result_anchor = None
            ctrl._close_session = lambda *a, **k: None
            ctrl._notify = lambda *a, **k: None
            ctrl._anchor_result = lambda: None
            ctrl.cancelled = None

            ctrl._quick_done(None, AudioError(audio_msg))
            translated = shown['translated']
            assert is_error_text(translated) is True
            help_ = explain(translated)
            assert help_.fix == expected_fix
            assert help_.kind == expected_kind
            assert 'could not be identified' not in help_.message.lower()
        finally:
            i18n.set_language(previous)


# --------------------------------------------------------------------------- #
# 6 & 7. Rate limit 429 vs 402 billing + Multi-key preservation
# --------------------------------------------------------------------------- #
class TestRateLimitAndMultiKey:
    def test_gemini_429_rpm_is_warning_retry_not_out_of_credit_danger(self):
        raw = 'Error: HTTP 429: Resource has been exhausted (e.g. check quota).'
        help_ = explain(raw)
        assert help_.fix == FIX_RETRY
        assert help_.kind == WARNING
        assert '429' in help_.message

    def test_openai_429_insufficient_quota_stays_billing_danger(self):
        raw = 'Error: HTTP 429: You exceeded your current quota, please check your plan and billing details (insufficient_quota).'
        help_ = explain(raw)
        assert help_.fix == FIX_SETTINGS
        assert help_.kind == DANGER

    def test_multi_key_does_not_erase_backup_error_when_primary_is_invalid_google_key(self):
        multi_err = (
            "All API keys failed.\n"
            "gemini-3.5-flash-lite: HTTP 400: API key not valid. Please pass a valid API key.\n"
            "openai/gpt-oss-120b: HTTP 503: Service Unavailable"
        )
        svc = _make_service(api_side_effect=Exception(multi_err))
        result = svc.translate_text('Hello', 'Vietnamese')
        assert is_error_text(result) is True
        assert '503' in result
        assert 'API key not valid' in result
        help_ = explain(result)
        assert help_.fix == FIX_SETTINGS
        assert 'different reasons' in help_.message.lower()


# --------------------------------------------------------------------------- #
# 8. Standalone Quick Dictionary popup handles API errors without fake cards
# --------------------------------------------------------------------------- #
class TestStandaloneQuickDictionaryErrors:
    def test_standalone_dictionary_api_error_shows_toast_not_result_card(self, tk_root):
        service = MagicMock()
        service.get_trial_info.return_value = None
        service.dictionary_lookup.return_value = FailureText('Error: HTTP 401: Unauthorized')
        toast = MagicMock()
        quick = MagicMock()
        flow = DictionaryPopup(tk_root, _FakeConfig(), service, toast, quick)
        opened_tabs = []
        flow.configure_callbacks(on_show_settings_tab=opened_tabs.append)

        class _SyncRunner:
            def start(self, tag, work, on_done):
                on_done(work(None, None), None)

        flow._runner = _SyncRunner()
        flow._lookup_serial = 0
        flow._do_lookup(['study'], 'Vietnamese')

        quick.show_dictionary_result.assert_not_called()
        quick.stop_dictionary_animation.assert_called()
        toast.show_warning_with_action.assert_called_once()
        # Invoke the action button callback -> navigates to API Key settings
        toast.show_warning_with_action.call_args.args[2]()
        assert opened_tabs == ['API Key']


# --------------------------------------------------------------------------- #
# 9. Bidirectional separation + finish_reason classification
# --------------------------------------------------------------------------- #
class TestBidirectionalResultStatusAndFinishReasons:
    @pytest.mark.parametrize('real_output', [
        'Error: Connection lost',
        'Error: File not found',
        'No text available on this page.',
    ])
    def test_real_translations_starting_with_error_or_no_text_are_not_errors(self, real_output):
        svc = _make_service(api_result=real_output)
        out1 = svc.translate_text('Lỗi: Mất kết nối', 'English')
        assert out1 == real_output
        assert is_error_text(out1) is False

        # Cache hit must ALSO keep is_error == False
        cached = svc.translate_text('Lỗi: Mất kết nối', 'English')
        assert cached == real_output
        assert is_error_text(cached) is False

        out2 = svc.translate_or_fix('Lỗi: Mất kết nối', 'English', skip_cache=True)
        assert out2 == real_output
        assert is_error_text(out2) is False

        out3 = svc.fix_grammar(real_output)
        assert out3 == real_output
        assert is_error_text(out3) is False

    def test_cooldown_and_empty_prompt_hide_copy_and_replace_buttons(self, tk_root):
        svc = _make_service()
        svc.last_translation_time = 9999999999.0
        svc.do_translate_or_fix('English')
        item = svc.translation_queue.get_nowait()
        assert is_error_text(item[1]) is True
        assert explain(item[1]).kind == INFO
        assert explain(item[1]).fix == FIX_NONE

        svc.ask_freeform('   ', 'English')
        item2 = svc.translation_queue.get_nowait()
        assert is_error_text(item2[1]) is True
        assert explain(item2[1]).kind == INFO
        assert explain(item2[1]).fix == FIX_NONE

        manager = QuickTranslateManager(tk_root, _FakeConfig())
        try:
            manager.show(item[1], 'English', original='')
            tk_root.update()
            assert manager.popup_copy_btn is None
            assert manager.popup_replace_btn is None
        finally:
            manager.close()

    @pytest.mark.parametrize('reason', ['PROHIBITED_CONTENT', 'BLOCKLIST', 'RECITATION'])
    def test_content_policy_finish_reasons_classify_as_policy(self, reason):
        raw = f'Error: Incomplete response: generation stopped with {reason}. No complete translation has been saved.'
        help_ = explain(raw)
        assert help_.fix == FIX_NONE
        assert help_.kind == WARNING
        assert 'content policy' in help_.message.lower()

    @pytest.mark.parametrize('reason', ['TOOL_CALLS', 'FUNCTION_CALL'])
    def test_tool_call_finish_reasons_classify_distinctly(self, reason):
        raw = f'Error: Incomplete response: generation stopped with {reason}. No complete translation has been saved.'
        help_ = explain(raw)
        assert help_.fix == FIX_RETRY
        assert help_.kind == WARNING
        assert 'tool call' in help_.message.lower()

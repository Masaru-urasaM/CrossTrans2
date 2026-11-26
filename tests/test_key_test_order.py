"""
Decision 102 (2026-11-20, TODO Task 22) - the Test button tries the defaults first and says in the log what it did.

The developer's keys, tested for real on 2026-11-20, showed two faults. With Model = Auto the Test walked
`model_provider_map`, which is alphabetical, so it chose `allam-2-7b` for a Groq key and the deprecated
`gemini-2.5-flash` for a Google key - and wrote that model into the card - while a real call with Model = Auto
starts from `default_models_by_provider`. And neither Test (API card or Audio card) wrote a log line, so a test
run could only be read back from the configuration. No network here: the pieces are called directly.
"""
import logging
import time
import urllib.error
from types import SimpleNamespace

import pytest

from src.constants import API_KEY_PATTERNS, MODEL_PROVIDER_MAP
from src.core import key_testing as kt
from src.core.api_manager import DEFAULT_MODELS_BY_PROVIDER
from src.core.audio_media import AudioError
from src.core.speech_engines import SpeechProbeOutcome, probe_speech
from src.ui.settings.audio_settings import AudioSection
from src.ui.settings.page_providers import ProvidersPage

MAP = {'Google': ['gemini-a', 'gemini-b'], 'Groq': ['allam', 'llama', 'qwen'], 'OpenAI': ['gpt-a']}
PATTERNS = {'AIza': 'Google', 'gsk_': 'Groq'}
SECRET = 'AIza-fixture-secret-key'


class Coded(Exception):
    def __init__(self, code):
        super().__init__(f'HTTP {code}: fixture')
        self.code = code


class Refusing:
    """Every pair fails with `code`."""

    def __init__(self, code):
        self.code = code

    def test_connection(self, model, key, provider):
        raise Coded(self.code)

    def test_vision_connection(self, model, key, provider):
        return None


class TestOrder:
    def test_a_default_the_map_also_lists_is_tried_once_and_first(self):
        assert kt.plan('Groq', 'Auto', 'k', MAP, PATTERNS, {'Groq': ['llama']}) == [
            ('Groq', 'llama'), ('Groq', 'allam'), ('Groq', 'qwen')]

    def test_a_recognised_key_under_auto_starts_with_its_defaults(self):
        assert kt.plan('Auto', 'Auto', 'gsk_1', MAP, PATTERNS, {'Groq': ['qwen', 'llama']}) == [
            ('Groq', 'qwen'), ('Groq', 'llama'), ('Groq', 'allam')]

    def test_an_unknown_key_puts_each_providers_defaults_first(self):
        assert kt.plan('Auto', 'Auto', 'zzz', MAP, PATTERNS, {'Groq': ['qwen']}) == [
            ('Google', 'gemini-a'), ('Google', 'gemini-b'), ('Groq', 'qwen'), ('Groq', 'allam'),
            ('Groq', 'llama'), ('OpenAI', 'gpt-a')]

    def test_a_detected_provider_with_defaults_only_is_not_widened_to_everyone(self):
        assert kt.plan('Auto', 'Auto', 'AIza1', {'Groq': ['x']}, PATTERNS, {'Google': ['g']}) == [('Google', 'g')]

    def test_a_named_model_is_still_exactly_that_model(self):
        assert kt.plan('Groq', 'allam', 'k', MAP, PATTERNS, {'Groq': ['qwen']}) == [('Groq', 'allam')]

    def test_models_to_try_without_defaults_is_the_map(self):
        assert kt.models_to_try('Groq', MAP) == ['allam', 'llama', 'qwen']

    @pytest.mark.parametrize('provider', sorted(name for name, models in DEFAULT_MODELS_BY_PROVIDER.items() if models))
    def test_with_the_apps_own_tables_every_provider_starts_with_its_first_default(self, provider):
        first = kt.plan(provider, 'Auto', 'k', MODEL_PROVIDER_MAP, API_KEY_PATTERNS, DEFAULT_MODELS_BY_PROVIDER)[0]
        assert first == (provider, DEFAULT_MODELS_BY_PROVIDER[provider][0])


class TestStatus:
    def test_a_failed_run_keeps_the_last_http_status(self):
        result = kt.run(Refusing(429), [('P', 'a'), ('P', 'b')], 'k')
        assert not result.ok and result.status == 429 and result.tried == 2

    def test_an_error_without_a_status_leaves_it_empty(self):
        class Plain(Refusing):
            def test_connection(self, model, key, provider):
                raise RuntimeError('offline')
        assert kt.run(Plain(0), [('P', 'a')], 'k').status is None

    def test_a_success_has_no_status(self):
        class Works(Refusing):
            def test_connection(self, model, key, provider):
                return True
        assert kt.run(Works(0), [('P', 'a')], 'k').status is None

    def test_a_refused_speech_probe_keeps_the_http_status(self):
        def transport(*_):
            try:
                raise urllib.error.HTTPError('https://fixture', 429, 'Too Many Requests', {}, None)
            except urllib.error.HTTPError as error:
                raise AudioError('Speech recognition quota or rate limit was reached.') from error
        outcome = probe_speech('gsk_fixture', 'Groq', 'whisper-large-v3', transport=transport)
        assert outcome.supported is False and outcome.status == 429


def _events(caplog, name):
    return [record for record in caplog.records if getattr(record, 'event', None) == name]


def _no_secret(caplog):
    text = ' '.join(f'{record.getMessage()} {getattr(record, "data", "")}' for record in caplog.records)
    assert SECRET not in text and 'fixture-provider-message' not in text


class TestKeyTestLog:
    def page(self, card):
        return SimpleNamespace(cards=['other', card])

    def test_a_pass_names_the_card_the_answer_and_the_time(self, caplog):
        card = object()
        outcome = kt.Outcome(True, 'Google', 'gemini-3.5-flash-lite', True, True, tried=1)
        with caplog.at_level(logging.INFO):
            ProvidersPage._log_test(self.page(card), card, outcome, None, ('Auto', 'Auto', SECRET),
                                    time.monotonic() - 0.25, 3)
        [record] = _events(caplog, 'key_test.ok')
        assert record.data['card'] == 2 and record.data['provider'] == 'Google'
        assert record.data['model'] == 'gemini-3.5-flash-lite' and record.data['vision'] is True
        assert (record.data['tried'], record.data['total']) == (1, 3) and record.data['ms'] >= 250
        _no_secret(caplog)

    def test_a_failure_names_what_was_asked_and_the_status_never_the_message(self, caplog):
        card = object()
        outcome = kt.Outcome(False, error='HTTP 401: fixture-provider-message', tried=2, status=401)
        with caplog.at_level(logging.INFO):
            ProvidersPage._log_test(self.page(card), card, outcome, None, ('Groq', '', SECRET), time.monotonic(), 2)
        [record] = _events(caplog, 'key_test.fail')
        assert record.levelno == logging.WARNING
        assert (record.data['provider'], record.data['model'], record.data['status']) == ('Groq', 'Auto', 401)
        assert record.data['cancelled'] is False
        _no_secret(caplog)

    def test_a_test_that_could_not_run_names_the_exception(self, caplog):
        card = object()
        with caplog.at_level(logging.INFO):
            ProvidersPage._log_test(self.page(card), card, None, RuntimeError(SECRET), ('Google', 'm', SECRET),
                                    time.monotonic(), 1)
        [record] = _events(caplog, 'key_test.fail')
        assert record.data['error'] == 'RuntimeError' and record.data['model'] == 'm'
        _no_secret(caplog)


class TestAudioTestLog:
    def section(self, card):
        return SimpleNamespace(audio_cards=[card])

    def test_a_pass_names_the_engine_and_one_step(self, caplog):
        card = object()
        outcome = SpeechProbeOutcome(True, 'gemini-3.5-flash-lite', True, '')
        with caplog.at_level(logging.INFO):
            AudioSection._log_test(self.section(card), card, 'Google', '', outcome, None, time.monotonic(), True)
        [record] = _events(caplog, 'audio_test.ok')
        assert record.data['card'] == 1 and record.data['provider'] == 'Google'
        assert record.data['model'] == 'gemini-3.5-flash-lite' and record.data['one_step'] is True
        assert record.data['auto'] is True and record.data['ms'] >= 0

    def test_a_refusal_has_the_status_and_not_the_message(self, caplog):
        card = object()
        outcome = SpeechProbeOutcome(False, 'whisper-large-v3', False, 'fixture-provider-message', 403)
        with caplog.at_level(logging.INFO):
            AudioSection._log_test(self.section(card), card, 'Groq', 'whisper-large-v3', outcome, None,
                                   time.monotonic(), False)
        [record] = _events(caplog, 'audio_test.fail')
        assert record.data['status'] == 403 and record.data['supported'] is False
        assert record.data['cancelled'] is False
        _no_secret(caplog)

    def test_a_cancelled_test_says_so(self, caplog):
        card = object()
        with caplog.at_level(logging.INFO):
            AudioSection._log_test(self.section(card), card, 'Groq', 'whisper-large-v3',
                                   SpeechProbeOutcome(None, 'whisper-large-v3', False, 'Cancelled'), None,
                                   time.monotonic(), False)
        [record] = _events(caplog, 'audio_test.fail')
        assert record.data['cancelled'] is True and record.data['supported'] is None


class TestKeepsAuto:
    DEFAULTS = {'Google': ['gemini-new', 'gemini-old']}

    def ok(self, model, provider='Google'):
        return kt.Outcome(True, provider, model, True, True, tried=1)

    def test_auto_asked_and_a_default_answered(self):
        assert kt.keeps_auto('Auto', self.ok('gemini-old'), self.DEFAULTS) is True
        assert kt.keeps_auto('', self.ok('gemini-new'), self.DEFAULTS) is True

    def test_a_model_outside_the_defaults_is_named(self):
        assert kt.keeps_auto('Auto', self.ok('gemini-map-only'), self.DEFAULTS) is False

    def test_a_named_model_is_never_turned_into_auto(self):
        assert kt.keeps_auto('gemini-new', self.ok('gemini-new'), self.DEFAULTS) is False

    def test_a_failure_or_no_defaults_keeps_nothing(self):
        assert kt.keeps_auto('Auto', kt.Outcome(False, 'Google', 'gemini-new'), self.DEFAULTS) is False
        assert kt.keeps_auto('Auto', self.ok('gemini-new'), None) is False
        assert kt.keeps_auto('Auto', self.ok('gemini-new', provider='Groq'), self.DEFAULTS) is False

    def test_the_sentence_names_what_auto_answered_with(self):
        outcome = self.ok('gemini-new')
        assert kt.summary(outcome, auto=True) == 'Auto: works with gemini-new. It can read images and files.'
        assert kt.summary(outcome) == 'Works with gemini-new. It can read images and files.'

    @pytest.mark.parametrize('vision, audio, japanese', [
        (True, None, '自動：gemini-newで利用できます。 画像とファイルに対応しています。'),
        (False, None, '自動：gemini-newで利用できます。 ファイルに対応しています。'),
        (True, True, '自動：gemini-newで動作します。画像・ファイル・音声を読み取れます。'),
    ])
    def test_the_sentence_has_its_japanese(self, vision, audio, japanese):
        from src.ui import i18n
        outcome = kt.Outcome(True, 'Google', 'gemini-new', vision, True, tried=1, audio=audio)
        i18n.set_language('ja')
        try:
            assert i18n.tr(kt.summary(outcome, auto=True)) == japanese
            assert i18n.tr('Auto: works with gemini-new.') == '自動：gemini-newで利用できます。'
        finally:
            i18n.set_language('en')

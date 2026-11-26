"""TODO Task 21 - key prefixes for Google's two kinds of key and OpenAI's project keys.

Google's API keys began `AIza`, but every key Google AI Studio has created since 2026-05-28 is an
auth key that begins `AQ.` (https://aistudio.google.com/docs/api-key: "all new API keys created in
Google AI Studio are automatically created as auth keys"). Neither was in the key-pattern tables, so
Google was the provider without a prefix: the fallback of `_detect_provider_from_key()`, and in
`_claimant_for_key()` a provider no key could rule out - a SambaNova key with `gemma-4-31b-it` under
Provider='Auto' went to Google (DEFERRED D11). OpenAI had no prefix either, so the same tie-break
sent a SambaNova key with `gpt-oss-120b` to OpenAI. Both prefixes of each, in the app's table and
the Worker's alike: with only one of Google's, a key of the other kind would stop counting as
Google's in that tie-break.

Also here: the Gemini REST calls carry the key in the `x-goog-api-key` header instead of `?key=`, and
an `AQ.` key is redacted wherever an `AIza` key is.
"""
from pathlib import Path
import re

import pytest

import src.core.api_manager as api_manager_module
from src.constants import API_KEY_PATTERNS, MODEL_PROVIDER_MAP
from src.core import key_testing as kt
from src.core.api_manager import AIAPIManager, DEFAULT_MODELS_BY_PROVIDER, _provider_error
from src.core.speech_engines import resolve_audio_provider
from src.utils import diagnostics

ROOT = Path(__file__).resolve().parents[1]
STANDARD_KEY = 'AIzaSyFixtureKey0123456789abcdefghijk'
AUTH_KEY = 'AQ.Ab8RN6FixtureKey0123456789abcdefghijk'
PROJECT_KEY = 'sk-proj-Fixture0123456789abcdefghijk'
SERVICE_KEY = 'sk-svcacct-Fixture0123456789abcdefghijk'
UUID_KEY = '0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b'     # SambaNova's shape: no prefix


class _HardcodedConfig:
    model_provider_map = MODEL_PROVIDER_MAP
    api_key_patterns = API_KEY_PATTERNS
    default_models_by_provider = DEFAULT_MODELS_BY_PROVIDER


class _ServedConfig:
    """The two IDs the served table of 2026-11-19 shares with Google or OpenAI."""

    model_provider_map = {
        'Google': ['gemini-3.5-flash-lite', 'gemma-4-31b-it'],
        'OpenAI': ['gpt-6-luna', 'gpt-oss-120b'],
        'Cerebras': ['gpt-oss-120b'],
        'SambaNova': ['gpt-oss-120b', 'gemma-4-31B-it'],
    }
    api_key_patterns = API_KEY_PATTERNS


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(api_manager_module, 'get_config', _HardcodedConfig)
    return AIAPIManager()


@pytest.fixture
def served_manager(monkeypatch):
    monkeypatch.setattr(api_manager_module, 'get_config', _ServedConfig)
    return AIAPIManager()


@pytest.mark.parametrize('key, provider', [
    (STANDARD_KEY, 'Google'), (AUTH_KEY, 'Google'),
    (PROJECT_KEY, 'OpenAI'), (SERVICE_KEY, 'OpenAI'),
])
def test_both_kinds_of_google_and_openai_key_are_recognised(manager, key, provider):
    assert manager._detect_provider_from_key(key) == provider
    assert kt.detect_provider(key, API_KEY_PATTERNS) == provider


@pytest.mark.parametrize('key', ['sk-' + 'a' * 48, UUID_KEY, 'x' * 32])
def test_a_key_without_a_known_prefix_refuses_to_guess_google(manager, key):
    """Task 34: keys without a distinctive prefix (legacy OpenAI, DeepSeek, Mistral, SambaNova)
    must not silently default to Google under Provider=Auto + Model=Auto."""
    assert manager._detect_provider_from_key(key) == ''
    manager.configure([{'api_key': key, 'provider': 'Auto', 'model_name': ''}])
    with pytest.raises(Exception, match='Cannot auto-detect provider'):
        manager.translate('Hello')
    with pytest.raises(Exception, match='Cannot auto-detect provider'):
        manager.translate_image('Describe', 'sample.png')
    with pytest.raises(Exception, match='Cannot auto-detect provider'):
        manager.translate_multimodal('Describe', image_paths=['sample.png'])


def test_ambiguous_auto_key_still_falls_back_to_a_valid_backup_key(manager, monkeypatch):
    manager.configure([
        {'api_key': UUID_KEY, 'provider': 'Auto', 'model_name': ''},
        {'api_key': STANDARD_KEY, 'provider': 'Auto', 'model_name': 'gemini-3.5-flash-lite'},
    ])
    called = []
    monkeypatch.setattr(manager, '_generate_content', lambda p, k, m, prompt, *a: (called.append((p, m)), 'OK')[1])
    assert manager.translate('Hello') == 'OK'
    assert called == [('Google', 'gemini-3.5-flash-lite')]


def test_no_prefix_shadows_another():
    """`_detect_provider_from_key()` returns the first prefix that matches, so none may start another."""
    prefixes = list(API_KEY_PATTERNS)
    assert [(a, b) for a in prefixes for b in prefixes if a != b and b.startswith(a)] == []


@pytest.mark.parametrize('model, key, provider', [
    ('gemma-4-31b-it', UUID_KEY, 'SambaNova'),      # went to Google before (DEFERRED D11)
    ('gemma-4-31b-it', AUTH_KEY, 'Google'),
    ('gemma-4-31b-it', STANDARD_KEY, 'Google'),
    ('gpt-oss-120b', UUID_KEY, 'SambaNova'),        # went to OpenAI before
    ('gpt-oss-120b', PROJECT_KEY, 'OpenAI'),
    ('gpt-oss-120b', 'csk-' + 'x' * 20, 'Cerebras'),
])
def test_a_shared_model_goes_to_the_provider_the_key_belongs_to(served_manager, model, key, provider):
    assert served_manager._identify_provider(model, key) == provider


def test_testing_an_auth_key_under_auto_tries_google_only():
    combinations = kt.plan('Auto', 'Auto', AUTH_KEY, MODEL_PROVIDER_MAP, API_KEY_PATTERNS)
    assert combinations and {provider for provider, _ in combinations} == {'Google'}


def test_an_auth_key_on_the_audio_card_is_google():
    assert resolve_audio_provider('Auto', '', AUTH_KEY) == 'Google'
    assert resolve_audio_provider('Auto', '', STANDARD_KEY) == 'Google'


@pytest.mark.parametrize('call', ['_call_google_rest', '_call_google_rest_multimodal'])
def test_gemini_requests_carry_the_key_in_a_header_not_the_url(manager, monkeypatch, call):
    sent = {}

    def capture(url, data, headers, response_parser, timeout):
        sent.update(url=url, headers=headers)
        return 'ok'

    monkeypatch.setattr(manager, '_make_request_with_retry', capture)
    argument = None if call == '_call_google_rest' else []
    assert getattr(manager, call)(AUTH_KEY, 'gemini-3.5-flash-lite', 'Hello', argument) == 'ok'
    assert AUTH_KEY not in sent['url'] and 'key=' not in sent['url']
    assert sent['headers']['x-goog-api-key'] == AUTH_KEY


class _Rejected:
    reason = 'Bad Request'
    code = 400

    def __init__(self, body):
        self.body = body.encode('utf-8')

    def read(self, limit):
        return self.body[:limit]


@pytest.mark.parametrize('key', [AUTH_KEY, STANDARD_KEY])
def test_a_provider_error_never_repeats_a_google_key(key):
    error = _provider_error(_Rejected('{"error": {"message": "API key not valid: %s"}}' % key), {}, {})
    assert key not in error.detail and '[credential]' in error.detail


def test_diagnostics_redact_an_auth_key():
    cleaned = diagnostics.redact_text(f'Gemini rejected {AUTH_KEY} at 10:00')
    assert 'Ab8RN6' not in cleaned and 'at 10:00' in cleaned


def test_the_workers_table_matches_the_apps():
    """The Worker's `api_key_patterns` replace the app's whenever the config loads, so they must agree.
    `cloudflare_worker.js` is not in git (the developer's decision, DEFERRED D34): checked where it exists."""
    worker = ROOT / 'cloudflare_worker.js'
    if not worker.exists():
        pytest.skip('cloudflare_worker.js is kept out of git; only the developer checkout has it')
    block = re.search(r'const STATIC_API_KEY_PATTERNS = \{(.*?)\n\};', worker.read_text(encoding='utf-8'), re.S)
    served = dict(re.findall(r"^\s*'([^']+)':\s*'([^']+)',", block.group(1), re.M))
    assert served == API_KEY_PATTERNS

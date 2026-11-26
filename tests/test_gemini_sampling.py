"""TODO Task 20 - no `temperature` for the Gemini models Google says ignore it.

Google, https://aistudio.google.com/docs/whats-new-gemini-3.6 (read 2026-11-19): for "Gemini 3.6 Flash
and Gemini 3.5 Flash-Lite" and "all future Gemini model releases", `temperature`, `top_p` and `top_k`
"are deprecated and ignored", and "In future model generations, supplying these parameters returns an
HTTP 400 error". Every Gemini request carried one (0.3 for translation, 0.0 for speech), and the cron of
2026-11-19 added `gemini-4-neon` to the served list. `takes_temperature()` decides for all four request
builders; older Gemini models, whose handling the page does not describe, keep it, so nothing they return
changes either.
"""
import io
import json
from unittest.mock import Mock

import pytest

import src.core.api_manager as api_manager_module
from src.constants import API_KEY_PATTERNS, MODEL_PROVIDER_MAP
from src.core.api_manager import AIAPIManager
from src.core.model_names import takes_temperature
from src.core.speech_engines import execute_chat_audio_request, execute_gemini_audio_request

OPENROUTER = 'https://openrouter.ai/api/v1/chat/completions'


@pytest.mark.parametrize('model, expected', [
    ('gemini-2.5-flash', True),
    ('gemini-2.5-flash-lite', True),
    ('gemini-3.5-flash', True),                 # not named by the page: unchanged
    ('gemini-3-pro-preview', True),
    ('gemini-3.5-flash-lite', False),           # Google's first default (Decision 94)
    ('gemini-3.5-flash-lite-preview-09-2026', False),
    ('gemini-3.6-flash', False),
    ('gemini-4-neon', False),                   # added by the cron of 2026-11-19
    ('models/gemini-4-carbon', False),
    ('gemini-flash-latest', False),             # an alias names the newest generation
    ('google/gemini-3.5-flash-lite', False),    # OpenRouter's slug
    ('google/gemini-2.0-flash-exp:free', True),
    ('gemma-4-31b-it', True),                   # served by the Gemini API, not a Gemini model
    ('gpt-6-luna', True),
    ('', True),
])
def test_which_models_take_temperature(model, expected):
    assert takes_temperature(model) is expected


class _HardcodedConfig:
    model_provider_map = MODEL_PROVIDER_MAP
    api_key_patterns = API_KEY_PATTERNS


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(api_manager_module, 'get_config', _HardcodedConfig)
    manager, sent = AIAPIManager(), {}

    def capture(url, data, headers, response_parser, timeout):
        sent['data'] = data
        return 'ok'

    monkeypatch.setattr(manager, '_make_request_with_retry', capture)
    return manager, sent


@pytest.mark.parametrize('call, argument', [('_call_google_rest', None), ('_call_google_rest_multimodal', [])])
@pytest.mark.parametrize('model, temperature', [('gemini-3.5-flash-lite', None), ('gemini-2.5-flash-lite', 0.3)])
def test_gemini_translation_requests(captured, call, argument, model, temperature):
    manager, sent = captured
    getattr(manager, call)('AQ.Ab-fixture', model, 'Hello', argument)
    config = sent['data']['generationConfig']
    assert config.get('temperature') == temperature
    assert config['maxOutputTokens'] == 4096


@pytest.mark.parametrize('call, argument', [('_call_generic_openai_style', None), ('_call_openai_style_multimodal', [])])
@pytest.mark.parametrize('model, temperature', [('google/gemini-3.5-flash-lite', None),
                                                ('google/gemini-2.5-flash', 0.3),
                                                ('meta-llama/llama-4-scout', 0.3)])
def test_openai_compatible_requests(captured, call, argument, model, temperature):
    manager, sent = captured
    getattr(manager, call)('sk-or-v1-fixture', model, 'Hello', OPENROUTER, argument)
    assert sent['data'].get('temperature') == temperature


def _reply(payload):
    return Mock(return_value=io.BytesIO(json.dumps(payload).encode('utf-8')))


@pytest.mark.parametrize('model, config', [('gemini-3.5-flash-lite', None), ('gemini-2.5-flash', {'temperature': 0.0})])
def test_gemini_speech_requests(model, config):
    opener = _reply({'candidates': [{'content': {'parts': [{'text': 'heard'}]}}]})
    assert execute_gemini_audio_request(b'wav', 'AQ.Ab-fixture', model, 'Transcribe', opener=opener) == 'heard'
    body = json.loads(opener.call_args.args[0].data)
    assert body.get('generationConfig') == config


@pytest.mark.parametrize('model, temperature', [('google/gemini-3.5-flash-lite', None), ('google/gemini-2.5-flash', 0.0)])
def test_openrouter_speech_requests(model, temperature):
    opener = _reply({'choices': [{'message': {'content': 'heard'}}]})
    execute_chat_audio_request(OPENROUTER, b'wav', 'sk-or-v1-fixture', model, 'Transcribe', opener=opener)
    body = json.loads(opener.call_args.args[0].data)
    assert body.get('temperature') == temperature

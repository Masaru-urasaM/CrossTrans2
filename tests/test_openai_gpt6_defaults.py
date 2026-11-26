"""OpenAI's defaults after gpt-4o (Decision 92): gpt-6-luna and gpt-6.1-terra, sent with reasoning_effort 'none'.

OpenAI shuts gpt-4o and gpt-4o-mini down on 2027-01-14 and names gpt-6-luna as their replacement. Both new models
reason at 'medium' unless told otherwise, and OpenAI's guide says to drop temperature at any effort but 'none'.
Nothing here touches the network: urlopen and the retry helper are replaced.
"""
import json
from unittest.mock import Mock

import pytest

from src.core.api_manager import DEFAULT_MODELS_BY_PROVIDER, OPENAI_UNREASONED_MODELS, AIAPIManager
from src.core.multimodal import MultimodalProcessor

OPENAI_URL = 'https://api.openai.com/v1/chat/completions'


def test_the_openai_defaults_are_the_named_replacement_then_the_next_model_up():
    assert DEFAULT_MODELS_BY_PROVIDER['OpenAI'] == ['gpt-6-luna', 'gpt-6.1-terra']
    assert set(DEFAULT_MODELS_BY_PROVIDER['OpenAI']) <= set(OPENAI_UNREASONED_MODELS)


@pytest.mark.parametrize('model', ['gpt-6-luna', 'gpt-6.1-terra'])
def test_a_translation_reaches_openai_without_reasoning_and_with_its_temperature(monkeypatch, model):
    api = AIAPIManager()
    api.configure([{'model_name': model, 'provider': 'OpenAI', 'api_key': 'test-key'}])
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({'choices': [
        {'finish_reason': 'stop', 'message': {'content': 'Bonjour.'}}]}).encode()
    request = Mock(return_value=response)
    monkeypatch.setattr('urllib.request.urlopen', request)
    assert api.translate('Hello.') == 'Bonjour.'
    request.assert_called_once()
    sent = request.call_args.args[0]
    assert sent.host == 'api.openai.com'
    body = json.loads(sent.data)
    assert body['model'] == model and body['reasoning_effort'] == 'none' and body['temperature'] == 0.3
    assert 'max_tokens' not in body and 'max_completion_tokens' not in body
    assert request.call_args.kwargs['timeout'] == 10


@pytest.mark.parametrize('files', [False, True])
def test_images_and_files_keep_their_own_timeout(monkeypatch, files):
    api = AIAPIManager()
    request = Mock(return_value='Complete answer.')
    monkeypatch.setattr(api, '_make_request_with_retry', request)
    if files:
        api._call_openai_style_multimodal('test-key', 'gpt-6-luna', 'Translate.', OPENAI_URL, [])
    else:
        api._call_generic_openai_style('test-key', 'gpt-6-luna', 'Translate.', OPENAI_URL)
    data = request.call_args.kwargs['data']
    assert data['reasoning_effort'] == 'none' and data['temperature'] == 0.3
    assert request.call_args.kwargs['timeout'] == (60 if files else 10)


@pytest.mark.parametrize('url,model', [
    (OPENAI_URL, 'gpt-4.1'),                                                   # another OpenAI model
    (OPENAI_URL, 'gpt-6-astra'),                                               # GPT-6 without 'none'
    ('https://openrouter.ai/api/v1/chat/completions', 'openai/gpt-6-luna'),    # the model through another API
    ('https://api.openai.com.example.com/v1/chat/completions', 'gpt-6-luna'),  # a lookalike host
])
def test_other_requests_are_unchanged(monkeypatch, url, model):
    api = AIAPIManager()
    request = Mock(return_value='Complete answer.')
    monkeypatch.setattr(api, '_make_request_with_retry', request)
    api._call_generic_openai_style('test-key', model, 'Translate.', url)
    assert 'reasoning_effort' not in request.call_args.kwargs['data']
    assert request.call_args.kwargs['timeout'] == 10


def test_the_first_default_still_reads_screenshots():
    # gpt-4o-mini matched 'gpt-4o-*'; its replacement must not lose screenshot translation.
    assert MultimodalProcessor.is_vision_capable(DEFAULT_MODELS_BY_PROVIDER['OpenAI'][0], 'OpenAI')

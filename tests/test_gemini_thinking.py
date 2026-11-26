"""Decision 105 (2026-11-20) - Gemini requests keep the model's thinking short, and fall back when it is refused.

Measured with the developer's keys on 2026-11-20 (a 290-character paragraph into Japanese): `gemini-2.5-flash`
thought 2,669-3,699 tokens and took 13.3-17.4 s, and with `thinkingBudget: 0` it took 1.2 s;
`gemini-flash-latest` took up to 10.9 s or hit the 20 s limit. `gemini-3.5-flash-lite` answers 400 to
`thinkingBudget: 0`, and Google routes `gemini-2.5-flash` to it from 2026-12-01, so a refused thinkingConfig is
retried once without it. No network here: the request is a fake.
"""
import json

import pytest

import src.core.api_manager as api_manager_module
from src.core.api_manager import AIAPIManager, ProviderRequestError
from src.core.model_names import thinking_config
from src.core.translation_task import current_task
from tests.test_gemini_sampling import _HardcodedConfig

LOW, OFF = {'thinkingLevel': 'low'}, {'thinkingBudget': 0}
OPENROUTER = 'https://openrouter.ai/api/v1/chat/completions'


@pytest.mark.parametrize('model, expected', [
    ('gemini-2.5-flash', OFF),                     # 13-17 s -> 1.2 s; refuses thinkingLevel
    ('gemini-2.5-flash-lite', None),               # does not think by default
    ('gemini-3.5-flash-lite', None),               # thinks at minimal by default; refuses the budget
    ('gemini-3.1-flash-lite', None),
    ('gemini-flash-lite-latest', None),            # names gemini-3.5-flash-lite
    ('gemini-flash-latest', LOW),                  # refuses minimal
    ('gemini-3-flash-preview', LOW),
    ('gemini-3-pro-preview', LOW),
    ('gemini-3.6-flash', LOW),
    ('gemini-4-neon', LOW),
    ('GEMINI-3.6-FLASH', LOW),
    ('gemini-2.5-pro', None),                      # cannot turn thinking off
    ('gemini-2.0-flash', None),                    # does not think
    ('gemma-4-31b-it', None),
    ('gpt-6-luna', None),
    ('', None),
])
def test_what_each_model_is_sent(model, expected):
    assert thinking_config(model) == expected


@pytest.fixture
def requests(monkeypatch):
    """`refuse` decides whether a request is answered 400; every request's body is recorded as sent."""
    monkeypatch.setattr(api_manager_module, 'get_config', _HardcodedConfig)
    monkeypatch.setattr(api_manager_module, '_THINKING_REFUSED', set())
    manager, state = AIAPIManager(), {'sent': [], 'tasks': [], 'refuse': lambda data: False, 'code': 400}

    def fake(url, data, headers, response_parser, timeout):
        state['sent'].append(json.loads(json.dumps(data)))
        state['tasks'].append(current_task())
        if state['refuse'](data):
            raise ProviderRequestError(state['code'], 'fixture-provider-message')
        return 'ok'

    monkeypatch.setattr(manager, '_make_request_with_retry', fake)
    return manager, state


def thinking_sent(body):
    return body['generationConfig'].get('thinkingConfig')


@pytest.mark.parametrize('call, argument', [('_call_google_rest', None), ('_call_google_rest_multimodal', [])])
@pytest.mark.parametrize('model, expected', [('gemini-2.5-flash', OFF), ('gemini-flash-latest', LOW),
                                             ('gemini-3.5-flash-lite', None)])
def test_both_gemini_calls_carry_it(requests, call, argument, model, expected):
    manager, state = requests
    assert getattr(manager, call)('AQ.Ab-fixture', model, 'Hello', argument) == 'ok'
    assert len(state['sent']) == 1 and thinking_sent(state['sent'][0]) == expected
    assert state['sent'][0]['generationConfig']['maxOutputTokens'] == 4096


def test_a_refused_config_is_retried_without_it_and_then_not_sent(requests, caplog):
    manager, state = requests
    state['refuse'] = lambda data: 'thinkingConfig' in data['generationConfig']
    with caplog.at_level('INFO'):
        assert manager._call_google_rest('AQ.Ab-fixture', 'gemini-2.5-flash', 'Hello') == 'ok'
    assert [thinking_sent(body) for body in state['sent']] == [OFF, None]
    [record] = [r for r in caplog.records if getattr(r, 'event', None) == 'thinking.refused']
    assert record.data == {'model': 'gemini-2.5-flash', 'status': 400, 'sent': ['thinkingBudget']}
    assert 'fixture-provider-message' not in record.getMessage()
    state['sent'].clear()
    assert manager._call_google_rest('AQ.Ab-fixture', 'gemini-2.5-flash', 'Hello') == 'ok'
    assert [thinking_sent(body) for body in state['sent']] == [None]          # one request, no retry


def test_the_retry_shares_the_first_requests_deadline(requests):
    manager, state = requests
    state['refuse'] = lambda data: 'thinkingConfig' in data['generationConfig']
    manager._call_google_rest('AQ.Ab-fixture', 'gemini-flash-latest', 'Hello')
    first, second = state['tasks']
    assert first is not None and first is second


def test_a_400_the_retry_gets_too_is_raised_and_marks_nothing(requests):
    manager, state = requests
    state['refuse'] = lambda data: True                     # a bad image, a prompt too long
    with pytest.raises(ProviderRequestError):
        manager._call_google_rest('AQ.Ab-fixture', 'gemini-2.5-flash', 'Hello')
    assert len(state['sent']) == 2
    assert api_manager_module._THINKING_REFUSED == set()
    state['refuse'], state['sent'] = (lambda data: False), []
    manager._call_google_rest('AQ.Ab-fixture', 'gemini-2.5-flash', 'Hello')
    assert thinking_sent(state['sent'][0]) == OFF


@pytest.mark.parametrize('code', [401, 429, 503])
def test_other_failures_are_not_retried(requests, code):
    manager, state = requests
    state['refuse'], state['code'] = (lambda data: True), code
    with pytest.raises(ProviderRequestError):
        manager._call_google_rest('AQ.Ab-fixture', 'gemini-2.5-flash', 'Hello')
    assert len(state['sent']) == 1


def test_one_models_refusal_does_not_change_another(requests):
    manager, state = requests
    state['refuse'] = lambda data: data['generationConfig'].get('thinkingConfig') == OFF
    manager._call_google_rest('AQ.Ab-fixture', 'gemini-2.5-flash', 'Hello')
    state['sent'].clear()
    manager._call_google_rest('AQ.Ab-fixture', 'gemini-flash-latest', 'Hello')
    assert [thinking_sent(body) for body in state['sent']] == [LOW]


@pytest.mark.parametrize('call, argument', [('_call_generic_openai_style', None),
                                            ('_call_openai_style_multimodal', [])])
def test_openrouter_requests_never_carry_it(requests, call, argument):
    manager, state = requests
    getattr(manager, call)('sk-or-v1-fixture', 'google/gemini-2.5-flash', 'Hello', OPENROUTER, argument)
    assert 'thinking' not in json.dumps(state['sent'][0]).lower()

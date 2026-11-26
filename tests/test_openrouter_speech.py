"""TODO Task 19 (DEFERRED D35) - OpenRouter's speech suggestions, and a Test that probes the path a recording takes.

OpenRouter suggested `google/gemini-2.5-flash`, which Google has deprecated (routed to `gemini-3.5-flash-lite`
from 2026-12-01). OpenRouter's public lists of 2026-11-19 carry `openai/whisper-large-v3` among the
transcription models (`/api/v1/models?output_modalities=transcription`) and `google/gemini-3.5-flash-lite` with
`audio` among its input modalities. And the Test button sent an OpenRouter Gemini model to
`/audio/transcriptions` while the recognizer sends it to chat completions with an `input_audio` block, so
the Test probed an endpoint a recording never uses.
"""
import pytest

from src.core import audio_service, speech_engines
from src.core.speech_engines import AUDIO_MODEL_SUGGESTIONS, probe_speech, uses_openrouter_chat

HEARD = 'And so, my fellow Americans, ask not what your country can do for you'


def test_openrouter_suggests_whisper_then_a_current_gemini():
    assert AUDIO_MODEL_SUGGESTIONS['OpenRouter'] == ['openai/whisper-large-v3', 'google/gemini-3.5-flash-lite']


@pytest.mark.parametrize('provider, model, expected', [
    ('OpenRouter', 'google/gemini-3.5-flash-lite', True),
    ('OpenRouter', 'google/gemini-2.5-flash', True),
    ('OpenRouter', 'google/gemini-3.5-transcribe', False),     # transcription-only on OpenRouter
    ('OpenRouter', 'openai/whisper-large-v3', False),
    ('Google', 'gemini-3.5-flash-lite', False),                # Google's own endpoint
    ('Together', 'google/gemini-3.5-flash-lite', False),
])
def test_which_openrouter_models_go_through_chat(provider, model, expected):
    assert uses_openrouter_chat(provider, model) is expected


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def chat(endpoint, wav, key, model, prompt, **kwargs):
        calls.append(('chat', endpoint, model))
        return f'<TRANSCRIPT>{HEARD}</TRANSCRIPT>'

    def transcription(endpoint, wav, key, model, language='', **kwargs):
        calls.append(('transcription', endpoint, model))
        return {'text': HEARD}

    def gemini(wav, key, model, prompt, **kwargs):
        calls.append(('gemini', 'generativelanguage', model))
        return f'<TRANSCRIPT>{HEARD}</TRANSCRIPT>'

    monkeypatch.setattr(speech_engines, 'execute_chat_audio_request', chat)
    monkeypatch.setattr(speech_engines, 'execute_transcription_request', transcription)
    monkeypatch.setattr(speech_engines, 'execute_gemini_audio_request', gemini)
    return calls


@pytest.mark.parametrize('model, engine, endpoint', [
    ('google/gemini-3.5-flash-lite', 'chat', speech_engines.OPENROUTER_CHAT_ENDPOINT),
    ('openai/whisper-large-v3', 'transcription', speech_engines.TRANSCRIPTION_ENDPOINTS['OpenRouter']),
    ('google/gemini-3.5-transcribe', 'transcription', speech_engines.TRANSCRIPTION_ENDPOINTS['OpenRouter']),
])
def test_the_openrouter_test_probes_the_recognizers_endpoint(requests, model, engine, endpoint):
    outcome = probe_speech('sk-or-v1-fixture', 'OpenRouter', model)
    assert outcome.supported is True and outcome.one_step is False
    assert requests == [(engine, endpoint, model)]


def test_a_google_gemini_test_still_uses_googles_endpoint(requests):
    outcome = probe_speech('AQ.Ab-fixture', 'Google', 'gemini-3.5-flash-lite')
    assert outcome.supported is True and outcome.one_step is True
    assert requests == [('gemini', 'generativelanguage', 'gemini-3.5-flash-lite')]


def test_the_recognizer_and_the_test_share_the_rule():
    """The recognizer's OpenRouter branch is the same function and endpoint the Test uses."""
    assert audio_service.uses_openrouter_chat is uses_openrouter_chat
    assert audio_service.OPENROUTER_CHAT_ENDPOINT == speech_engines.OPENROUTER_CHAT_ENDPOINT

"""Unit tests for multi-provider speech engines, audio settings, and idle pump efficiency."""
from unittest.mock import Mock, patch
import io
import json
import threading
import urllib.error

import pytest

from src.core.audio_media import AudioError
from src.core.speech_engines import (
    build_multipart, parse_transcription_response,
    execute_transcription_request, execute_gemini_audio_request,
    execute_chat_audio_request, probe_speech, SpeechProbeOutcome,
    AUDIO_MODEL_SUGGESTIONS, get_default_model
)
import config


def test_build_multipart():
    wav = b"RIFF....WAVE"
    body, content_type = build_multipart(wav, "whisper-large-v3", "en")
    assert b"CrossTransAudio" in body
    assert b"whisper-large-v3" in body
    assert b"RIFF....WAVE" in body
    assert b'name="language"\r\n\r\nen' in body
    assert "multipart/form-data; boundary=" in content_type


@pytest.mark.parametrize("model,expected", [
    ("gpt-transcribe", "json"),
    ("gpt-4o-transcribe", "json"),
    ("gpt-4o-mini-transcribe", "json"),
    ("whisper-1", "verbose_json"),
    ("whisper-large-v3", "verbose_json"),
    ("Whisper-Large-v3", "verbose_json"),
])
def test_openai_gpt_transcription_models_are_asked_for_json(model, expected):
    body, _ = build_multipart(b"RIFF....WAVE", model)
    assert f'name="response_format"\r\n\r\n{expected}\r\n'.encode() in body


# Shut down or retiring by their providers' own pages (read 2026-11-17, DEFERRED D35).
RETIRED_SPEECH_MODELS = {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "gpt-4o-audio-preview",
                         "gemini-2.0-flash", "gemini-2.5-flash"}


def test_no_suggestion_is_a_retired_speech_model():
    for provider, models in AUDIO_MODEL_SUGGESTIONS.items():
        # Without the provider prefix too: OpenRouter suggested google/gemini-2.5-flash until TODO Task 19.
        assert not RETIRED_SPEECH_MODELS & {model.rsplit('/', 1)[-1] for model in models}, provider
    assert get_default_model("OpenAI") == "gpt-transcribe"
    assert get_default_model("Google") == "gemini-3.5-flash-lite"


def test_parse_transcription_response_verbose_json():
    raw = json.dumps({
        "text": "Hello world.",
        "segments": [{"start": 0.5, "text": "Hello world."}]
    }).encode("utf-8")
    result = parse_transcription_response(raw)
    assert result["text"] == "Hello world."
    assert result["segments"] == [(0.5, "Hello world.")]


def test_parse_transcription_response_simple_json():
    raw = json.dumps({"text": "Simple transcript."}).encode("utf-8")
    result = parse_transcription_response(raw)
    assert result["text"] == "Simple transcript."
    assert result["segments"] == [(0.0, "Simple transcript.")]


def test_parse_transcription_response_invalid():
    with pytest.raises(AudioError):
        parse_transcription_response(b"not json")
    with pytest.raises(AudioError):
        parse_transcription_response(json.dumps(["not", "a", "dict"]).encode("utf-8"))


def test_execute_transcription_request_success():
    payload = json.dumps({"text": "Test transcript", "segments": []}).encode("utf-8")
    mock_resp = io.BytesIO(payload)
    mock_opener = Mock(return_value=mock_resp)

    res = execute_transcription_request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        b"wav-data", "fake-key", "whisper-large-v3", opener=mock_opener
    )
    assert res["text"] == "Test transcript"


def test_execute_transcription_request_http_errors():
    for code, expected_fragment in [
        (401, "did not accept this API key"),
        (403, "does not have permission"),
        (404, "was not found"),
        (413, "too large"),
        (429, "quota or rate limit"),
    ]:
        err = urllib.error.HTTPError("url", code, "Err", {}, None)
        mock_opener = Mock(side_effect=err)
        with pytest.raises(AudioError) as exc_info:
            execute_transcription_request(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                b"wav-data", "fake-key", "whisper-large-v3", opener=mock_opener
            )
        assert expected_fragment.lower() in str(exc_info.value).lower()


def _transcription_429(headers):
    err = urllib.error.HTTPError("url", 429, "Too Many Requests", headers, None)
    with pytest.raises(AudioError) as exc_info:
        execute_transcription_request(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            b"wav-data", "fake-key", "whisper-large-v3", opener=Mock(side_effect=err)
        )
    return str(exc_info.value)


def test_transcription_429_names_the_retry_after_wait():
    message = _transcription_429({'retry-after': '30'})
    assert message == 'Speech recognition quota or rate limit was reached. Try again in 30 seconds.'


@pytest.mark.parametrize('headers', [{}, {'retry-after': ''}, {'retry-after': 'soon'}, {'retry-after': '0'},
                                     {'retry-after': '-5'}, {'retry-after': str(2 * 24 * 60 * 60)}])
def test_transcription_429_without_a_usable_retry_after_says_later(headers):
    assert _transcription_429(headers) == 'Speech recognition quota or rate limit was reached. Try again later.'


def test_retry_after_seconds_reads_delay_seconds_and_http_dates():
    from datetime import datetime, timezone
    from src.core.speech_engines import retry_after_seconds
    now = datetime(2026, 11, 13, 6, 0, 0, tzinfo=timezone.utc)
    assert retry_after_seconds({'retry-after': ' 45 '}, now=now) == 45
    assert retry_after_seconds({'retry-after': 'Fri, 13 Nov 2026 06:01:30 GMT'}, now=now) == 90
    assert retry_after_seconds({'retry-after': 'Fri, 13 Nov 2026 05:59:00 GMT'}, now=now) is None
    assert retry_after_seconds({'retry-after': 'Fri, 13 Nov 2026 06:01:30'}, now=now) is None  # no zone
    assert retry_after_seconds(None, now=now) is None


def test_transcription_429_messages_are_translated(monkeypatch):
    from src.ui import i18n
    previous = i18n.language()
    i18n.set_language('ja')
    try:
        assert i18n.tr(_transcription_429({'retry-after': '30'})) == '音声認識の利用上限に達しました。30秒後に再実行してください。'
        assert i18n.tr(_transcription_429({})) == '音声認識の利用上限に達しました。時間をおいて再実行してください。'
    finally:
        i18n.set_language(previous)


def test_execute_gemini_audio_request():
    payload = json.dumps({
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "<TRANSCRIPT>Hello from Gemini</TRANSCRIPT>"}]
                }
            }
        ]
    }).encode("utf-8")
    mock_resp = io.BytesIO(payload)
    mock_opener = Mock(return_value=mock_resp)

    text = execute_gemini_audio_request(
        b"wav-data", "fake-key", "gemini-2.5-flash", "Prompt", opener=mock_opener
    )
    assert text == "<TRANSCRIPT>Hello from Gemini</TRANSCRIPT>"


def test_execute_chat_audio_request():
    payload = json.dumps({
        "choices": [
            {"message": {"content": "Transcribed audio"}}
        ]
    }).encode("utf-8")
    mock_resp = io.BytesIO(payload)
    mock_opener = Mock(return_value=mock_resp)

    text = execute_chat_audio_request(
        "https://api.openai.com/v1/chat/completions",
        b"wav-data", "fake-key", "gpt-4o-audio-preview", "Prompt", opener=mock_opener
    )
    assert text == "Transcribed audio"


def test_probe_speech_with_transport():
    transport = Mock(return_value={"text": "And so my fellow Americans, ask not..."})
    outcome = probe_speech("fake-key", "Groq", "whisper-large-v3", transport=transport)
    assert outcome.supported is True
    assert outcome.model == "whisper-large-v3"
    assert outcome.error == ""

    # When words are missing
    bad_transport = Mock(return_value={"text": "Unrelated words."})
    outcome_bad = probe_speech("fake-key", "Groq", "whisper-large-v3", transport=bad_transport)
    assert outcome_bad.supported is None
    assert "could not be verified" in outcome_bad.error


def test_audio_key_migration_and_config():
    cfg = config.Config()
    cfg._config = {
        "api_keys": [
            {"id": "k1", "provider": "Groq", "model_name": "whisper-large-v3", "api_key": "gsk_test"},
            {"id": "k2", "provider": "Google", "model_name": "gemini-2.5-flash", "api_key": "ai_test"},
        ],
        "audio_model": "whisper-large-v3-turbo"
    }
    cfg._migrate_audio_keys()

    # Audio keys migrated
    audio_keys = cfg.get_audio_keys()
    assert len(audio_keys) == 1
    assert audio_keys[0]["provider"] == "Groq"
    assert audio_keys[0]["api_key"] == "gsk_test"
    assert audio_keys[0]["from_key_id"] == "k1"

    # Text card model reset from whisper to Auto
    text_keys = cfg.get_api_keys()
    assert text_keys[0]["model_name"] == ""

    # has_any_audio_capable
    assert cfg.has_any_audio_capable() is True


def test_plain_groq_key_stays_auto_across_migration_reload():
    cfg = config.Config()
    cfg._config = {
        "api_keys": [
            {"id": "k1", "provider": "Groq", "model_name": "openai/gpt-oss-120b", "api_key": "gsk_text_only"},
        ],
        "audio_keys": [],
    }
    cfg._migrate_audio_keys()
    assert cfg.get_audio_keys() == []
    assert cfg.has_manual_audio_keys() is False
    assert cfg.has_usable_audio_keys() is True
    # Clearing back to Auto after migration stays Auto even on subsequent loads
    cfg._migrate_audio_keys()
    assert cfg.get_audio_keys() == []


def test_idle_pump_stops_scheduling(monkeypatch, tk_root):
    from src.ui.audio_controller import AudioController
    app = Mock()
    app.root = tk_root
    app.config = config.Config()
    app.quick_translate_manager = Mock()
    app.translation_service = Mock()
    app.toast = Mock()

    controller = AudioController(app)
    # On init, pump is not scheduled because controller is idle
    assert controller._pump_scheduled is False

    # Simulate start_recording with disabled audio
    controller.config.set("audio_enabled", False)
    controller.start_recording()
    assert controller._pump_scheduled is False
    assert app.toast.show_warning.call_count == 1


def test_probe_speech_with_callable_cancelled():
    from src.core.audio_media import is_cancelled
    event = threading.Event()
    assert is_cancelled(None) is False
    assert is_cancelled(event.is_set) is False
    assert is_cancelled(event) is False
    event.set()
    assert is_cancelled(event.is_set) is True
    assert is_cancelled(event) is True

    cancel_event = threading.Event()
    def fake_transport(wav, key, model):
        return {"text": "And so, my fellow Americans, ask not what your country can do for you"}

    outcome = probe_speech("test_key", "Groq", "whisper-large-v3",
                           cancelled=cancel_event.is_set,
                           transport=fake_transport)
    assert outcome.supported is True
    assert outcome.error == ""

    cancel_event.set()
    outcome_cancelled = probe_speech("test_key", "Groq", "whisper-large-v3",
                                     cancelled=cancel_event.is_set,
                                     transport=fake_transport)
    assert outcome_cancelled.supported is None
    assert outcome_cancelled.error == "Cancelled"


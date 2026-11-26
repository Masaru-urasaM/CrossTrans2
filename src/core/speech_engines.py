"""Multi-provider speech recognition and audio translation engines without Tk.

Supports:
1. Standard OpenAI-compatible transcription endpoint (/v1/audio/transcriptions):
   Groq, OpenAI, Mistral, OpenRouter, Together, SambaNova, SiliconFlow.
2. Google Gemini multimodal audio via generateContent inline_data.
3. OpenAI-compatible chat completions with input_audio block.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import base64
import json
import math
import re
import threading
import time  # noqa: F401 - kept: tests may patch this module attribute
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.constants import API_KEY_PATTERNS
from src.core.audio_media import AudioError, AudioCancelled, check_cancel
from src.core.model_names import takes_temperature
from src.core.ssl_pinning import get_ssl_context_for_url


# OpenAI and Google, 2026-11-17 (DEFERRED D35): whisper-1 and the gpt-4o transcribe models shut down
# 2027-02-26 and gpt-transcribe replaces them (gpt-live-transcribe is Realtime only); gemini-2.0-flash is
# shut down and gemini-2.5-flash is deprecated in favour of gemini-3.5-flash-lite, which takes audio.
# The alias last survives the next deprecation. Listed by the providers, not yet answered: no key at hand.
# OpenRouter, 2026-11-19 (TODO Task 19): its transcription list (models?output_modalities=transcription)
# has openai/whisper-large-v3; google/gemini-3.5-flash-lite takes audio input on chat completions.
AUDIO_MODEL_SUGGESTIONS: Dict[str, List[str]] = {
    'Groq': ['whisper-large-v3', 'whisper-large-v3-turbo'],
    'OpenAI': ['gpt-transcribe'],
    'Google': ['gemini-3.5-flash-lite', 'gemini-flash-latest'],
    'Mistral': ['voxtral-mini-latest'],
    'OpenRouter': ['openai/whisper-large-v3', 'google/gemini-3.5-flash-lite'],
    'SambaNova': ['Whisper-Large-v3'],
    'Together': ['openai/whisper-large-v3'],
    'SiliconFlow': ['FunAudioLLM/SenseVoiceSmall'],
}

SUPPORTED_AUDIO_PROVIDERS = tuple(AUDIO_MODEL_SUGGESTIONS.keys())

TRANSCRIPTION_ENDPOINTS: Dict[str, str] = {
    'Groq': 'https://api.groq.com/openai/v1/audio/transcriptions',
    'OpenAI': 'https://api.openai.com/v1/audio/transcriptions',
    'Mistral': 'https://api.mistral.ai/v1/audio/transcriptions',
    'OpenRouter': 'https://openrouter.ai/api/v1/audio/transcriptions',
    'Together': 'https://api.together.xyz/v1/audio/transcriptions',
    'SambaNova': 'https://api.sambanova.ai/v1/audio/transcriptions',
    'SiliconFlow': 'https://api.siliconflow.cn/v1/audio/transcriptions',
}

OPENROUTER_CHAT_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
VERBATIM_PROMPT = (
    "You are a verbatim speech recognition system. Transcribe the spoken speech in this audio "
    "exactly in its original spoken language. Wrap transcription between <TRANSCRIPT> and </TRANSCRIPT>."
)

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_default_model(provider: str) -> str:
    suggestions = AUDIO_MODEL_SUGGESTIONS.get(provider)
    if suggestions:
        return suggestions[0]
    return 'whisper-large-v3'


def _key_patterns() -> Dict[str, str]:
    try:
        from src.core.remote_config import get_config
        return dict(get_config().api_key_patterns)
    except Exception:
        return dict(API_KEY_PATTERNS)


def resolve_audio_provider(provider: str, model_name: str = '', api_key: str = '') -> str:
    """Resolve 'Auto' using API key prefix first, then model hints."""
    prov = (provider or '').strip()
    if prov and prov != 'Auto':
        return prov

    key = (api_key or '').strip()
    if key.startswith(('AIza', 'AQ.')):     # Google standard and auth keys
        return 'Google'
    if key.startswith('sk-or-'):
        return 'OpenRouter'
    for prefix, matched in _key_patterns().items():
        if key.startswith(prefix):
            return matched

    model = (model_name or '').strip()
    model_lower = model.lower()
    if model_lower.startswith('openrouter/'):
        return 'OpenRouter'
    if 'gemini' in model_lower:
        return 'Google'
    if 'voxtral' in model_lower or 'mistral' in model_lower:
        return 'Mistral'
    if 'sensevoice' in model_lower:
        return 'SiliconFlow'
    if model_lower == 'whisper-1' or 'transcribe' in model_lower or 'gpt-4o' in model_lower:
        return 'OpenAI'
    if 'whisper' in model_lower:
        return 'Groq'
    if model_lower.startswith('gpt-'):
        return 'OpenAI'
    if 'claude' in model_lower:
        return 'Anthropic'
    if 'deepseek' in model_lower:
        return 'DeepSeek'
    if 'grok' in model_lower:
        return 'xAI'

    try:
        from src.core.remote_config import get_config
        claimants = [
            name for name, models in get_config().model_provider_map.items()
            if any(m.lower() == model_lower for m in models)
        ]
        if len(claimants) == 1:
            return claimants[0]
    except Exception:
        pass
    return 'Auto'


def _looks_audio_model(provider: str, model: str) -> bool:
    low = (model or '').strip().lower()
    if not low or low == 'auto':
        return False
    if provider == 'Google':
        return 'gemini' in low and not any(bad in low for bad in ('embedding', 'imagen', 'veo'))
    if provider == 'OpenAI':
        return any(tok in low for tok in ('whisper', 'transcribe', '-audio'))
    if provider == 'OpenRouter':
        return any(tok in low for tok in ('whisper', 'gemini', '-audio'))
    return any(tok in low for tok in ('whisper', 'voxtral', 'sensevoice', 'transcribe'))


def select_audio_model(provider: str, row: Dict[str, Any], fallback_model: str = 'whisper-large-v3') -> str:
    """Choose the speech model for a manual audio row or an Auto main-key row."""
    resolved = resolve_audio_provider(provider, row.get('model_name', ''), row.get('api_key', ''))
    raw_model = (row.get('model_name') or '').strip()
    if row.get('is_audio_key'):
        if raw_model and raw_model != 'Auto':
            return raw_model
        return get_default_model(resolved)

    tested_audio = (row.get('audio_model') or '').strip()
    if tested_audio and _looks_audio_model(resolved, tested_audio):
        return tested_audio
    if _looks_audio_model(resolved, raw_model):
        return raw_model
    if resolved == 'Groq' and fallback_model and 'whisper' in fallback_model.lower():
        return fallback_model
    return get_default_model(resolved)


def transcription_format(model: str) -> str:
    """verbose_json carries segment times; OpenAI's GPT transcription models answer json only
    (the audio/transcriptions reference: "the only supported format is json" for gpt-4o-transcribe)."""
    name = (model or '').strip().lower()
    return 'json' if name.startswith('gpt-') and 'transcribe' in name else 'verbose_json'


def build_multipart(wav: bytes, model: str, language: str = '') -> Tuple[bytes, str]:
    boundary = 'CrossTransAudio' + uuid.uuid4().hex
    parts = []
    fields = {'model': model, 'response_format': transcription_format(model), 'temperature': '0'}
    if language:
        fields['language'] = language
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8'))
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode('utf-8'))
    parts.extend((wav, f'\r\n--{boundary}--\r\n'.encode('utf-8')))
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


def parse_transcription_response(raw_data: bytes) -> Dict[str, Any]:
    if len(raw_data) > 8 * 1024 * 1024:
        raise AudioError('The speech provider returned an invalid response.')
    try:
        data = json.loads(raw_data)
    except Exception as e:
        raise AudioError('The speech provider returned an invalid response.') from e

    if not isinstance(data, dict):
        raise AudioError('The speech provider returned an invalid response.')

    text = data.get('text', '')
    segments = []
    raw_segments = data.get('segments')
    if isinstance(raw_segments, list) and raw_segments:
        for seg in raw_segments:
            if isinstance(seg, dict) and 'start' in seg and 'text' in seg:
                seg_text = str(seg['text']).strip()
                if seg_text:
                    segments.append((float(seg['start']), seg_text))

    if not segments and text.strip():
        segments.append((0.0, text.strip()))

    return {'text': text.strip(), 'segments': segments}


RETRY_AFTER_MAX_SECONDS = 24 * 60 * 60


def retry_after_seconds(headers, now: Optional[datetime] = None) -> Optional[int]:
    """Whole seconds a Retry-After header asks for (delay-seconds or an HTTP date), or None."""
    value = headers.get('retry-after') if headers else None
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.isdigit():
        seconds = int(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None or when.tzinfo is None:
            return None
        seconds = math.ceil((when - (now or datetime.now(timezone.utc))).total_seconds())
    return seconds if 0 < seconds <= RETRY_AFTER_MAX_SECONDS else None


def execute_transcription_request(endpoint: str, wav: bytes, api_key: str, model: str,
                                  language: str = '', timeout: int = 45, opener=None) -> Dict[str, Any]:
    body, content_type = build_multipart(wav, model, language)
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': content_type,
        'User-Agent': USER_AGENT,
    }
    req = urllib.request.Request(endpoint, data=body, headers=headers)
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
            context=get_ssl_context_for_url(endpoint))).open
    try:
        with opener(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        return parse_transcription_response(raw)
    except urllib.error.HTTPError as error:
        messages = {
            401: 'The speech provider did not accept this API key.',
            403: 'This API key does not have permission to use speech recognition.',
            404: f'Speech recognition model "{model}" was not found or is unavailable.',
            413: 'The audio request is too large.',
            429: 'Speech recognition quota or rate limit was reached. Try again later.',
        }
        msg = messages.get(error.code, f'The speech provider returned HTTP {error.code}.')
        wait = retry_after_seconds(error.headers) if error.code == 429 else None
        if wait:
            msg = f'Speech recognition quota or rate limit was reached. Try again in {wait} seconds.'
        raise AudioError(msg) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AudioError('Could not reach the speech provider. Check your network and try again.') from error


def execute_gemini_audio_request(wav: bytes, api_key: str, model: str, prompt: str,
                                 timeout: int = 50, opener=None) -> str:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    b64_audio = base64.b64encode(wav).decode('utf-8')
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": b64_audio,
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
        }
    }
    if not takes_temperature(model):
        del payload['generationConfig']
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'x-goog-api-key': api_key,
        'User-Agent': USER_AGENT,
    }
    req = urllib.request.Request(endpoint, data=body, headers=headers)
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
            context=get_ssl_context_for_url(endpoint))).open
    try:
        with opener(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        data = json.loads(raw)
        candidates = data.get('candidates', [])
        if not candidates:
            raise AudioError('Gemini did not return any response.')
        parts = candidates[0].get('content', {}).get('parts', [])
        texts = [p.get('text', '') for p in parts if 'text' in p]
        return '\n'.join(texts).strip()
    except urllib.error.HTTPError as error:
        if error.code == 400:
            try:
                body_text = error.read(4096).decode('utf-8', errors='replace').lower()
            except Exception:
                body_text = ''
            if 'api key not valid' in body_text or 'api_key_invalid' in body_text:
                raise AudioError('Google did not accept this API key.') from error
        messages = {
            400: 'Invalid request to Gemini audio API.',
            401: 'Google did not accept this API key.',
            403: 'This Google API key does not have permission for this Gemini model.',
            404: f'Gemini model "{model}" was not found.',
            429: 'Gemini rate limit or quota exceeded. Try again later.',
        }
        msg = messages.get(error.code, f'Google Gemini returned HTTP {error.code}.')
        raise AudioError(msg) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AudioError('Could not reach Google Gemini. Check your network and try again.') from error


def execute_chat_audio_request(endpoint: str, wav: bytes, api_key: str, model: str, prompt: str,
                               timeout: int = 50, opener=None) -> str:
    b64_audio = base64.b64encode(wav).decode('utf-8')
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": b64_audio,
                            "format": "wav"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.0,
    }
    if not takes_temperature(model):                 # google/gemini-... through OpenRouter
        del payload['temperature']
    body = json.dumps(payload).encode('utf-8')
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT,
    }
    req = urllib.request.Request(endpoint, data=body, headers=headers)
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
            context=get_ssl_context_for_url(endpoint))).open
    try:
        with opener(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        data = json.loads(raw)
        choices = data.get('choices', [])
        if not choices:
            raise AudioError('The speech model did not return any answer.')
        return choices[0].get('message', {}).get('content', '').strip()
    except urllib.error.HTTPError as error:
        messages = {
            401: 'The speech provider did not accept this API key.',
            403: 'This API key does not have permission to use this audio model.',
            404: f'Audio model "{model}" was not found.',
            429: 'Speech model rate limit or quota exceeded.',
        }
        msg = messages.get(error.code, f'Audio model returned HTTP {error.code}.')
        raise AudioError(msg) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AudioError('Could not reach the audio provider. Check your network and try again.') from error


def uses_openrouter_chat(provider: str, model: str) -> bool:
    """Whether OpenRouter gets this model through chat completions with an `input_audio` block - a Gemini
    chat model such as `google/gemini-3.5-flash-lite` - rather than `/audio/transcriptions`, which serves
    Whisper and the transcription-only models (`google/gemini-3.5-transcribe`). The recognizer and the
    Test button both ask this, so the Test probes the path a recording takes (TODO Task 19)."""
    low = (model or '').lower()
    return provider == 'OpenRouter' and 'gemini' in low and 'transcribe' not in low


def runs_one_step(provider: str, model: str) -> bool:
    """Whether a one-step request runs as one step with this resolved provider and model.

    `AudioRecognizer.transcribe` sends the one-step prompt only to Google Gemini; every other engine
    transcribes and the text is translated in a second call. The Test button and the Settings note use this
    too, so what they say is what happens (CHANGELOG Phase 25).
    """
    return provider == 'Google' or (provider == 'Auto' and 'gemini' in (model or '').lower())


@dataclass(frozen=True)
class SpeechProbeOutcome:
    supported: Optional[bool]
    model: str = ''
    one_step: bool = False
    error: str = ''
    status: Optional[int] = None            # the provider's HTTP status when it refused (the Test's log line)


def probe_speech(key: str, provider: str, model_name: str,
                 cancelled: Optional[Union[threading.Event, Callable[[], bool]]] = None,
                 transport: Optional[Callable] = None) -> SpeechProbeOutcome:
    """Probe audio capability using packaged JFK speech sample."""
    sample = Path(__file__).resolve().parents[1] / 'assets' / 'audio_probe.wav'
    if not sample.exists():
        return SpeechProbeOutcome(False, model_name, False, 'Audio probe sample fixture missing.')

    wav_bytes = sample.read_bytes()
    resolved = resolve_audio_provider(provider, model_name, key)
    raw_model = (model_name or '').strip()
    if resolved != 'Auto' and provider == 'Auto' and not _looks_audio_model(resolved, raw_model):
        model = get_default_model(resolved)
    else:
        model = raw_model or get_default_model(resolved)

    try:
        check_cancel(cancelled)
        if transport:
            result = transport(wav_bytes, key, model)
            text = result.get('text', '') if isinstance(result, dict) else str(result)
        elif uses_openrouter_chat(resolved, model):
            raw = execute_chat_audio_request(OPENROUTER_CHAT_ENDPOINT, wav_bytes, key, model, VERBATIM_PROMPT)
            match = re.search(r'<TRANSCRIPT>(.*?)</TRANSCRIPT>', raw, re.DOTALL | re.IGNORECASE)
            text = match.group(1).strip() if match else raw
        elif resolved == 'Google' or ('gemini' in model.lower() and resolved != 'OpenRouter'):
            raw = execute_gemini_audio_request(wav_bytes, key, model, VERBATIM_PROMPT)
            match = re.search(r'<TRANSCRIPT>(.*?)</TRANSCRIPT>', raw, re.DOTALL | re.IGNORECASE)
            text = match.group(1).strip() if match else raw
        elif resolved in TRANSCRIPTION_ENDPOINTS or 'whisper' in model.lower() or 'voxtral' in model.lower():
            endpoint = TRANSCRIPTION_ENDPOINTS.get(resolved, TRANSCRIPTION_ENDPOINTS['Groq'])
            res = execute_transcription_request(endpoint, wav_bytes, key, model)
            text = res.get('text', '')
        elif resolved == 'Auto':
            raise AudioError('Choose a speech provider for this key.')
        else:
            chat_url = f"https://api.{resolved.lower()}.com/v1/chat/completions"
            prompt = "Transcribe the audio exactly in English. Wrap in <TRANSCRIPT> and </TRANSCRIPT>."
            raw = execute_chat_audio_request(chat_url, wav_bytes, key, model, prompt)
            match = re.search(r'<TRANSCRIPT>(.*?)</TRANSCRIPT>', raw, re.DOTALL | re.IGNORECASE)
            text = match.group(1).strip() if match else raw

        check_cancel(cancelled)
        words = text.lower()
        if 'fellow' not in words or 'american' not in words:
            return SpeechProbeOutcome(None, model, False, 'Audio recognition could not be verified. Test again.')

        # One-step only where the recognizer really sends the one-step prompt
        one_step_supported = runs_one_step(resolved, model)
        return SpeechProbeOutcome(True, model, one_step_supported, '')
    except AudioCancelled:
        return SpeechProbeOutcome(None, model, False, 'Cancelled')
    except AudioError as error:
        return SpeechProbeOutcome(False, model, False, str(error), getattr(error.__cause__, 'code', None))
    except Exception as error:
        return SpeechProbeOutcome(None, model, False, str(error), getattr(error, 'code', None))

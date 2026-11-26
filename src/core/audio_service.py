"""Multi-provider speech recognition with bounded requests, independent capability checks and no Tk."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import io
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

from src.core.audio_media import (AudioError, AudioCancelled, RATE, CHUNK_SECONDS,
                                 check_cancel, is_cancelled, read_pcm, reduce_noise)
from src.core.ssl_pinning import get_ssl_context_for_url
from src.core.translation_task import TranslationTasks, bounded_io, TranslationStopped
from src.utils.log_events import log_event
from src.core.speech_engines import (
    SUPPORTED_AUDIO_PROVIDERS, TRANSCRIPTION_ENDPOINTS, execute_transcription_request,
    execute_gemini_audio_request, execute_chat_audio_request, OPENROUTER_CHAT_ENDPOINT, VERBATIM_PROMPT,
    probe_speech, resolve_audio_provider, runs_one_step, select_audio_model, uses_openrouter_chat,
)


MODELS = ('whisper-large-v3', 'whisper-large-v3-turbo')
ENDPOINT = 'https://api.groq.com/openai/v1/audio/transcriptions'
PROBE_VERSION = 1


def groq_row(row):
    provider = row.get('provider', 'Auto')
    return bool(row.get('api_key', '').strip()) and (provider == 'Groq' or
           (provider == 'Auto' and row.get('api_key', '').startswith('gsk_')))


def has_manual_audio_keys(config) -> bool:
    """True only when the user manually set or picked at least one audio key card."""
    if config is None or not hasattr(config, 'get_audio_keys'):
        return False
    for row in config.get_audio_keys() or []:
        if not isinstance(row, dict):
            continue
        if (row.get('api_key') or '').strip() or row.get('from_key_id'):
            return True
    return False


def resolve_audio_source_rows(config):
    """Return ('manual', audio_keys) when user manually configured keys, else ('auto', main api_keys)."""
    if has_manual_audio_keys(config):
        return 'manual', list(config.get_audio_keys() or [])
    if config is not None and hasattr(config, 'get_api_keys'):
        return 'auto', list(config.get_api_keys() or [])
    return 'auto', []


def one_step_outlook(config) -> str:
    """'' when a one-step request would run as one step with the first audio key the recognizer tries;
    otherwise why not: 'no_key' or 'engine_without_one_step'."""
    model = config.get('audio_model', MODELS[0]) if config is not None else MODELS[0]
    rows = eligible_keys(resolve_audio_source_rows(config)[1], model)
    if not rows:
        return 'no_key'
    first = rows[0]
    provider = resolve_audio_provider(first.get('provider', 'Groq' if first.get('is_audio_key') else 'Auto'),
                                      first.get('model_name', ''), first.get('api_key', ''))
    return '' if runs_one_step(provider, select_audio_model(provider, first, model)) else 'engine_without_one_step'


def eligible_keys(rows, model=MODELS[0]):
    """Keep saved priority; unknown capability is retryable, explicit unsupported is skipped."""
    result = []
    for row in rows:
        key = row.get('api_key', '').strip()
        if not key:
            continue
        # If explicitly marked unsupported for this specific model (or manual card failed test), skip
        tested_model = row.get('audio_model')
        if row.get('audio_capable') is False and (
            tested_model == model
            or (not tested_model and (row.get('model_name') == model or row.get('is_audio_key')))
        ):
            continue
        # Manual audio cards (or picked keys) stay eligible cleanly; Auto main keys accept all supported audio providers
        if row.get('is_audio_key') or 'from_key_id' in row or groq_row(row):
            result.append(dict(row))
            continue
        resolved = resolve_audio_provider(row.get('provider', 'Auto'), row.get('model_name', ''), key)
        if resolved in SUPPORTED_AUDIO_PROVIDERS:
            result.append(dict(row))
    return result


class GroqOnlyConfig:
    """Use the user's saved Groq priority without falling back to other paid providers."""
    def __init__(self, config):
        self.config = config
        self.rows = [dict(row) for row in config.get_api_keys() if groq_row(row)]

    def get_api_keys(self):
        return [dict(row) for row in self.rows]

    def __getattr__(self, name):
        return getattr(self.config, name)


def multipart(wav, model, language=''):
    boundary = 'CrossTransAudio' + uuid.uuid4().hex
    parts = []
    fields = {'model': model, 'response_format': 'verbose_json', 'temperature': '0'}
    if language:
        fields['language'] = language
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts.extend((wav, f'\r\n--{boundary}--\r\n'.encode()))
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


class SpeechRequestError(AudioError):
    def __init__(self, code, message, retry_after=None):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def request_transcript(wav, key, model=MODELS[0], language='', opener=None):
    """No private filenames, key prefixes or transcripts enter diagnostics."""
    body, content_type = multipart(wav, model, language)
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': content_type,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
            context=get_ssl_context_for_url(ENDPOINT))).open
    try:
        with opener(request, timeout=45) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise AudioError('The speech provider returned an invalid response.')
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise AudioError('The speech provider returned an invalid response.')
        return result
    except urllib.error.HTTPError as error:
        messages = {
            401: 'The speech provider did not accept this API key.',
            403: 'This API key does not have permission to use speech recognition.',
            404: 'This speech recognition model is not available for this API key.',
            413: 'The audio request is too large.',
            429: 'Groq speech recognition quota or rate limit was reached. Try again later.',
        }
        raise SpeechRequestError(error.code, messages.get(error.code,
                                 'The speech provider could not process the audio.'),
                                 error.headers.get('retry-after') if error.headers else None) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AudioError('Could not reach the speech provider. Check the connection and try again.') from error
    except (ValueError, TypeError) as error:
        raise AudioError('The speech provider returned an invalid response.') from error


def wav_bytes(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        stream.writeframes(samples.astype('<i2').tobytes())
    return buffer.getvalue()


@dataclass(frozen=True)
class SpeechProbe:
    supported: object
    model: str = ''
    error: str = ''


def probe(key, provider, model=MODELS[0], cancelled=None, transport=request_transcript):
    if transport is not request_transcript:
        # Custom mock/fake transport passed by unit tests
        cancelled = cancelled or threading.Event()
        sample = Path(__file__).resolve().parents[1] / 'assets' / 'audio_probe.wav'
        try:
            check_cancel(cancelled)
            with TranslationTasks().begin(timeout=50).activate():
                result = bounded_io(lambda: transport(sample.read_bytes(), key, model))
            check_cancel(cancelled)
            words = (result.get('text', '') if isinstance(result, dict) else str(result)).lower()
            if 'fellow' not in words or 'american' not in words:
                return SpeechProbe(None, model, 'Audio recognition could not be verified. Test again.')
            return SpeechProbe(True, model)
        except AudioCancelled:
            return SpeechProbe(None, model, 'Cancelled')
        except SpeechRequestError as error:
            return SpeechProbe(False if error.code == 404 else None, model, str(error))
        except (AudioError, TranslationStopped, OSError) as error:
            return SpeechProbe(None, model, str(error))
        except Exception as error:
            return SpeechProbe(None, model, str(error))

    outcome = probe_speech(key, provider, model, cancelled=cancelled)
    return SpeechProbe(outcome.supported, outcome.model, outcome.error)


def speech_text(result):
    if isinstance(result, str):
        return result.strip()
    segments = result.get('segments')
    if isinstance(segments, list) and segments:
        parts = []
        for segment in segments:
            if isinstance(segment, tuple) and len(segment) == 2:
                parts.append(str(segment[1]).strip())
                continue
            if not isinstance(segment, dict):
                continue
            if segment.get('no_speech_prob', 0) > 0.65 and segment.get('avg_logprob', 0) < -1:
                continue
            parts.append(str(segment.get('text', '')).strip())
        return ' '.join(part for part in parts if part)
    return result.get('text', '').strip()


def speech_segments(result):
    if isinstance(result, str):
        text = result.strip()
        return [(0.0, text)] if text else []
    segments = result.get('segments')
    if not isinstance(segments, list) or not segments:
        text = speech_text(result)
        return [(0.0, text)] if text else []
    output = []
    for segment in segments:
        if isinstance(segment, tuple) and len(segment) == 2:
            output.append((float(segment[0]), str(segment[1]).strip()))
            continue
        if not isinstance(segment, dict):
            continue
        if segment.get('no_speech_prob', 0) > 0.65 and segment.get('avg_logprob', 0) < -1:
            continue
        text = str(segment.get('text', '')).strip()
        start = segment.get('start', 0)
        if text and isinstance(start, (int, float)) and 0 <= start <= CHUNK_SECONDS + 1:
            output.append((float(start), text))
    return output


def text_chunks(text, maximum=1800):
    """Break on a nearby sentence/line boundary, preserving all canonical characters."""
    while len(text) > maximum:
        boundary = max(text.rfind(mark, 0, maximum) for mark in ('\n', '。', '. ', '！', '？', ' '))
        end = boundary + 1 if boundary >= maximum // 2 else maximum
        yield text[:end]
        text = text[end:]
    if text:
        yield text


class AudioRecognizer:
    def __init__(self, rows, model=MODELS[0], language='', transport=request_transcript, usage=None, config=None,
                 mode='two_step', target_language='', manual_mode=False):
        self.model = model if model in MODELS else (model or MODELS[0])
        self.manual_mode = bool(manual_mode)
        self.rows = eligible_keys(rows, self.model)
        self.language, self.transport, self.usage = language, transport, usage
        self.config = config
        self.mode = mode
        self.target_language = target_language
        self.cache = {}
        self.last_model = ''
        self.timeline = []
        self.one_step_translation = ''
        self.sid = None          # the log session (AudioController)
        self.mode_used = ''      # what actually ran: 'one_step' only when a one-step reply was used
        self.mode_reason = ''

    def _log_failure(self, index, total, slot, provider, model, engine, started, error):
        status = getattr(error, 'code', None) or getattr(getattr(error, '__cause__', None), 'code', None)
        log_event('Audio', 'stt.fail', level=logging.WARNING, sid=self.sid, index=index, total=total, key_slot=slot,
                  provider=provider, model=model, engine=engine, ms=round((time.monotonic() - started) * 1000),
                  status=status, error=type(error).__name__, message=str(error))

    def _settle_mode(self, chunks, one_step_chunks, pieces, cached_chunks):
        """Join the one-step translations, or fall back to two steps for the whole session, and log which ran.

        Every chunk must have answered with its translation: one missing piece (a cached chunk, a reply without
        <TRANSLATION>) would leave part of the speech untranslated, so then the joined transcript is translated
        as text instead. Translations come per chunk and per source, so PC + Microphone, whose transcript is
        interleaved by segment and freed of echoes, is also translated as text (CHANGELOG Phase 27).
        """
        sources = {track.source for track, _, _ in chunks}
        self.one_step_translation = ''
        if self.mode == 'one_step' and chunks and len(pieces) == len(chunks) and len(sources) == 1:
            self.one_step_translation = '\n\n'.join(text for _, text in sorted(pieces, key=lambda piece: piece[0]))
        if self.one_step_translation:
            self.mode_used, self.mode_reason = 'one_step', ''
        elif self.mode == 'one_step':
            self.mode_used = 'two_step'
            if not chunks:
                self.mode_reason = 'no_speech'
            elif not one_step_chunks and cached_chunks == len(chunks):
                self.mode_reason = 'cached_transcript'      # a retry reuses the transcript; no audio call is made
            elif not one_step_chunks:
                self.mode_reason = 'engine_without_one_step'  # e.g. a Whisper transcription key
            elif len(pieces) == len(chunks):
                self.mode_reason = 'several_sources'
            elif pieces:
                self.mode_reason = 'partial_one_step_reply'
            else:
                self.mode_reason = 'no_translation_in_reply'
        else:
            self.mode_used, self.mode_reason = 'two_step', ''
        log_event('Audio', 'mode.used', sid=self.sid, requested=self.mode, used=self.mode_used,
                  reason=self.mode_reason, chunks=len(chunks), one_step_chunks=one_step_chunks,
                  one_step_replies=len(pieces), cached_chunks=cached_chunks, sources=len(sources))

    def transcribe(self, tracks, cancelled, progress, denoise=False):
        import numpy as np
        if not self.rows:
            if self.manual_mode:
                raise AudioError('Your manual audio key failed or is unsupported. Replace it or switch Audio to Auto in Settings > API keys.')
            raise AudioError('Add a working API key in Settings > API keys to translate audio.')
        chunks, timeline = [], []
        planned, silent = [], 0
        self.one_step_translation = ''      # never the previous call's
        for track in tracks:
            check_cancel(cancelled)
            data = read_pcm(track.path)
            planned.append({'source': track.source, 'seconds': round(len(data) / RATE, 2)})
            if denoise and track.source == 'Microphone':
                data = reduce_noise(data)
            for start in range(0, len(data), RATE * CHUNK_SECONDS):
                begin = max(0, start - RATE // 2)
                samples = data[begin:start + RATE * CHUNK_SECONDS + RATE // 2]
                rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if len(samples) else 0
                if rms < 25:
                    silent += 1
                    continue
                chunks.append((track, begin / RATE, wav_bytes(samples)))
        log_event('Audio', 'stt.plan', sid=self.sid, tracks=planned, chunks=len(chunks), silent_skipped=silent,
                  mode_requested=self.mode, keys=len(self.rows), denoise=bool(denoise))
        one_step_chunks = cached_chunks = 0
        pieces = []                         # (start in the recording, translation) per one-step chunk
        for index, (track, offset, wav) in enumerate(chunks, 1):
            check_cancel(cancelled)
            progress('transcribing', index, len(chunks))
            digest = hashlib.sha256(wav + self.model.encode() + self.language.encode() + self.mode.encode()).digest()
            segments = self.cache.get(digest)
            chunk_seconds = round(max(0, len(wav) - 44) / 2 / RATE, 2)
            if segments is not None:
                cached_chunks += 1
                log_event('Audio', 'stt.chunk', sid=self.sid, index=index, total=len(chunks), source=track.source,
                          seconds=chunk_seconds, cached=True, segments=len(segments),
                          chars=sum(len(text) for _, text in segments))
            if segments is None:
                last_error = None
                for slot, row in enumerate(self.rows, 1):
                    check_cancel(cancelled)
                    started = time.monotonic()
                    provider, model, engine = row.get('provider', ''), row.get('model_name', '') or self.model, 'unknown'
                    task = TranslationTasks().begin(timeout=50)
                    watcher_done = threading.Event()

                    def watch():
                        while not watcher_done.wait(0.03):
                            if is_cancelled(cancelled):
                                task.cancel()
                                return
                    watcher = threading.Thread(target=watch, daemon=True, name='AudioRequestCancel')
                    watcher.start()
                    try:
                        with task.activate():
                            raw_provider = row.get('provider', 'Groq' if row.get('is_audio_key') else 'Auto')
                            provider = resolve_audio_provider(raw_provider, row.get('model_name', ''), row.get('api_key', ''))
                            model = select_audio_model(provider, row, self.model)

                            if self.transport is not request_transcript:
                                engine = 'custom'
                                result = bounded_io(lambda: self.transport(wav, row['api_key'], model, self.language))
                            elif self.mode == 'one_step' and runs_one_step(provider, model):
                                engine = 'gemini_one_step'
                                one_step_chunks += 1
                                prompt = (
                                    f"You are a simultaneous speech translation system. Listen to this spoken audio "
                                    f"and translate it into {self.target_language or 'English'}. Provide both the exact original "
                                    f"transcript and translation in the following format:\n"
                                    f"<TRANSCRIPT>\n[verbatim transcript]\n</TRANSCRIPT>\n"
                                    f"<TRANSLATION>\n[translation]\n</TRANSLATION>"
                                )
                                raw = bounded_io(lambda: execute_gemini_audio_request(wav, row['api_key'], model, prompt))
                                t_match = re.search(r'<TRANSCRIPT>(.*?)</TRANSCRIPT>', raw, re.DOTALL | re.IGNORECASE)
                                trans_match = re.search(r'<TRANSLATION>(.*?)</TRANSLATION>', raw, re.DOTALL | re.IGNORECASE)
                                transcript_text = t_match.group(1).strip() if t_match else raw
                                if trans_match and trans_match.group(1).strip():
                                    pieces.append((track.offset + offset, trans_match.group(1).strip()))
                                result = {'text': transcript_text, 'segments': [(0.0, transcript_text)] if transcript_text else []}
                            elif provider == 'Google' or (provider == 'Auto' and 'gemini' in model.lower()):
                                engine = 'gemini'
                                prompt = (
                                    "You are a verbatim speech recognition system. Transcribe the spoken speech in this audio "
                                    "exactly in its original spoken language. Wrap transcription between <TRANSCRIPT> and </TRANSCRIPT>."
                                )
                                raw = bounded_io(lambda: execute_gemini_audio_request(wav, row['api_key'], model, prompt))
                                match = re.search(r'<TRANSCRIPT>(.*?)</TRANSCRIPT>', raw, re.DOTALL | re.IGNORECASE)
                                text = match.group(1).strip() if match else raw
                                result = {'text': text, 'segments': [(0.0, text)] if text else []}
                            elif uses_openrouter_chat(provider, model):
                                engine = 'openrouter_chat'
                                raw = bounded_io(lambda: execute_chat_audio_request(
                                    OPENROUTER_CHAT_ENDPOINT, wav, row['api_key'], model, VERBATIM_PROMPT))
                                match = re.search(r'<TRANSCRIPT>(.*?)</TRANSCRIPT>', raw, re.DOTALL | re.IGNORECASE)
                                text = match.group(1).strip() if match else raw
                                result = {'text': text, 'segments': [(0.0, text)] if text else []}
                            else:
                                engine = 'transcription'
                                ep = TRANSCRIPTION_ENDPOINTS.get(provider, ENDPOINT)
                                result = bounded_io(lambda: execute_transcription_request(ep, wav, row['api_key'], model, self.language))

                        check_cancel(cancelled)
                        segments = speech_segments(result)
                        self.cache[digest] = segments
                        self.last_model = f"{model} ({provider})"
                        if self.usage:
                            self.usage(dict(id=row.get('id'), at=time.time(), seconds=time.monotonic() - started,
                                            kind='audio_success'))
                        log_event('Audio', 'stt.chunk', sid=self.sid, index=index, total=len(chunks),
                                  source=track.source, seconds=chunk_seconds, cached=False, key_slot=slot,
                                  provider=provider, model=model, engine=engine,
                                  ms=round((time.monotonic() - started) * 1000), segments=len(segments),
                                  chars=sum(len(text) for _, text in segments))
                        break
                    except SpeechRequestError as error:
                        last_error = error
                        self._log_failure(index, len(chunks), slot, provider, model, engine, started, error)
                    except TranslationStopped:
                        check_cancel(cancelled)
                        self._log_failure(index, len(chunks), slot, provider, model, engine, started,
                                          TimeoutError('speech request timed out'))
                        raise AudioError('Speech recognition timed out. Try again.') from None
                    except AudioError as error:
                        last_error = error
                        if not isinstance(error, AudioCancelled):
                            self._log_failure(index, len(chunks), slot, provider, model, engine, started, error)
                    finally:
                        watcher_done.set()
                        watcher.join(timeout=0.2)
                if segments is None:
                    raise last_error or AudioError('Speech recognition failed.')
            timeline.extend((track.offset + offset + start, track.source, text) for start, text in segments)
        self._settle_mode(chunks, one_step_chunks, pieces, cached_chunks)
        if not timeline:
            raise AudioError('No speech was detected. Check the source and try again.')
        timeline.sort(key=lambda item: item[0])
        output = []
        for offset, source, text in timeline:
            normalized = re.sub(r'\W', '', text).casefold()
            if any(abs(offset - prior[0]) < 3 and normalized == prior[2] for prior in output[-4:]):
                continue
            output.append((offset, source, normalized, text))
        self.timeline = [(offset, source, text) for offset, source, normalized, text in output]
        return '\n\n'.join(item[3] for item in output)

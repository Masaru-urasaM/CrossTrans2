"""
Testing an API key from Settings, as pure logic (TODO.md U8, step 4).

The Test button used to be one 200-line method of the window that looped over provider and model
combinations, rewrote a label's colour on every step and called `window.update()` from inside the loop.
Here the decisions are separate from the drawing: `plan()` says which (provider, model) pairs are worth
trying for what the user entered, `run()` tries them one by one against any object with the two
`AIAPIManager` test methods, and `summary()` says the outcome in a sentence. Nothing here touches Tk or
the network by itself; the Settings page runs `run()` on a worker thread and reads its progress from a
queue (Decision 43: a worker never calls Tk).

The iteration (kept from the old method, it is how "Auto" works):
- provider Auto + model Auto: the provider the key's prefix names, else every provider; all its models
- a provider + model Auto: all that provider's models
- "all its models" is the provider's default list first, the order a real call with Model = Auto tries
  (`AIAPIManager`), then the rest of `model_provider_map`, which is in alphabetical order: before
  2026-11-20 the map alone made Test pick `allam-2-7b` for Groq and a deprecated `gemini-2.5-flash` for
  Google, and write it into the card (Decision 102)
- provider Auto + a model: the provider the key's prefix names, else that model with every provider
- both given: exactly that pair
"""
import re
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

AUTO = 'Auto'
MUTED, SUCCESS, WARNING, DANGER = 'muted', 'success', 'warning', 'danger'   # a status sentence's tone (palette roles)
Combination = Tuple[str, str]               # (provider, model)
FILES_CAPABLE = True                        # documents are read as text by the app: any working model does


def detect_provider(api_key: str, patterns: Dict[str, str]) -> str:
    """The provider whose key prefix `api_key` starts with, or ''. `patterns` is prefix -> provider."""
    key = (api_key or '').strip()
    for prefix, provider in patterns.items():
        if key.startswith(prefix):
            return provider
    return ''


def is_auto(value: Optional[str]) -> bool:
    return not value or not str(value).strip() or str(value).strip() == AUTO


def models_to_try(provider: str, model_map: Dict[str, Sequence[str]],
                  defaults: Optional[Dict[str, Sequence[str]]] = None) -> List[str]:
    """`provider`'s models for Model = Auto: its defaults (`default_models_by_provider`) first, then the rest of
    `model_map`, each once."""
    return list(dict.fromkeys([*((defaults or {}).get(provider) or []), *(model_map.get(provider) or [])]))


def plan(provider: str, model: str, api_key: str, model_map: Dict[str, Sequence[str]],
         patterns: Dict[str, str], defaults: Optional[Dict[str, Sequence[str]]] = None) -> List[Combination]:
    """The (provider, model) pairs to try for what the user entered, best guess first. `defaults` is the
    per-provider list a real call with Model = Auto tries; it comes before the rest of `model_map`."""
    defaults = defaults or {}
    model = '' if is_auto(model) else model.strip()
    if is_auto(provider):
        detected = detect_provider(api_key, patterns)
        if not model:
            providers = [detected] if detected and models_to_try(detected, model_map, defaults) else list(model_map)
            return [(name, each) for name in providers for each in models_to_try(name, model_map, defaults)]
        if detected:
            return [(detected, model)]
        return [(name, model) for name in model_map]
    if not model:
        return [(provider, each) for each in models_to_try(provider, model_map, defaults)]
    return [(provider, model)]


class Outcome(NamedTuple):
    ok: bool
    provider: str = ''
    model: str = ''
    vision: Optional[bool] = False
    files: bool = False
    error: str = ''
    tried: int = 0
    cancelled: bool = False
    audio: Optional[bool] = None
    audio_model: str = ''
    audio_error: str = ''
    status: Optional[int] = None            # the HTTP status of the last failure, when there was one


def keeps_auto(asked_model: str, outcome: Outcome, defaults: Optional[Dict[str, Sequence[str]]] = None) -> bool:
    """Whether a passed Test leaves Model = Auto on the card (Decision 104): the user asked for Auto and the
    model that answered is one of its provider's defaults, which a real call with Auto tries. Then the card
    keeps following the served defaults; otherwise it names the model, since Auto would not reach it."""
    return bool(outcome.ok and is_auto(asked_model)
                and outcome.model in ((defaults or {}).get(outcome.provider) or []))


def run(manager, combinations: Sequence[Combination], api_key: str,
        progress: Optional[Callable[[int, int, str], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None, audio_model=None) -> Outcome:
    """Try `combinations` in order until one answers. `manager.test_connection(model, key, provider)`
    returns or raises; `manager.test_vision_connection(...)` returns a bool and never raises.
    `progress(done, total, stage)` is called before each step (stage 'connect', then 'vision' once a pair
    works) - from whatever thread runs this, so it must only queue. `cancelled()` is asked before each pair."""
    total = len(combinations)
    if not total:
        return Outcome(False, error='There is no model to try for this provider.')
    last_error, last_status = '', None
    for index, (provider, model) in enumerate(combinations, 1):
        if cancelled is not None and cancelled():
            return Outcome(False, tried=index - 1, cancelled=True)
        if progress is not None:
            progress(index, total, 'connect')
        try:
            manager.test_connection(model, api_key, provider)
        except Exception as error:                      # any failure of this pair: try the next
            last_error, last_status = str(error), getattr(error, 'code', None)
            continue
        if progress is not None:
            progress(index, total, 'vision')
        vision = manager.test_vision_connection(model, api_key, provider)
        if audio_model is not None:
            if cancelled is not None and cancelled():
                return Outcome(False, tried=index, cancelled=True)
            if progress:
                progress(index, total, 'audio')
            from src.core.audio_service import SpeechProbe
            tester = getattr(manager, 'test_audio_connection', None)
            audio = tester(api_key, provider, audio_model) if tester else SpeechProbe(None)
            if cancelled is not None and cancelled():
                return Outcome(False, tried=index, cancelled=True)
            return Outcome(True, provider, model, vision, FILES_CAPABLE, tried=index,
                           audio=audio.supported, audio_model=audio.model or audio_model,
                           audio_error=audio.error)
        return Outcome(True, provider, model, vision=vision, files=FILES_CAPABLE, tried=index)
    if audio_model is not None and (cancelled is None or not cancelled()):
        provider, model = combinations[0]
        tester = getattr(manager, 'test_audio_connection', None)
        if tester is not None and provider == 'Groq':
            if progress:
                progress(total, total, 'audio')
            audio = tester(api_key, provider, audio_model)
            if cancelled is not None and cancelled():
                return Outcome(False, tried=total, cancelled=True)
            is_audio_model = bool(model and (model == audio_model or model.startswith('whisper')))
            if is_audio_model and audio.supported:
                return Outcome(True, provider, model, vision=False, files=False, tried=total,
                               audio=True, audio_model=audio.model or audio_model,
                               audio_error=audio.error)
            return Outcome(False, provider, model, error=last_error, tried=total,
                           audio=audio.supported, audio_model=audio.model or audio_model,
                           audio_error=audio.error, status=last_status)
    return Outcome(False, error=last_error, tried=total, status=last_status)


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def reason(error: str, many: bool = False) -> str:
    """What a failed test most likely means, in plain words - '' when the error is not recognised. `many`
    is true when several models were tried: the error is then only the last one's, so a sentence about a
    model the provider does not know would blame the wrong thing."""
    text = (error or '').lower()
    if _has(text, r'\b40[13]\b', r'unauthori[sz]ed', r'forbidden', r'invalid.*key', r'key.*invalid',
            r'key.*not valid', r'incorrect.*key', r'permission'):
        return 'The provider did not accept the key. Check that it was copied whole and is for this provider.'
    if _has(text, r'\b429\b', r'quota', r'rate.?limit', r'too many requests', r'exhausted'):
        return 'The provider says this key is out of quota or over its rate limit for now.'
    if _has(text, r'timed? ?out', r'took too long', r'deadline'):
        return 'The provider took too long to answer. Try again in a moment.'
    if _has(text, r'connection', r'network', r'unreachable', r'getaddrinfo', r'resolve', r'\bssl\b',
            r'certificate', r'offline'):
        return 'CrossTrans could not reach the provider. Check your internet connection.'
    if not many and _has(text, r'\b404\b', r'not found', r'does not exist', r'unknown model', r'no such model'):
        return 'The provider does not know this model. Pick another, or choose Auto.'
    return ''


def summary(outcome: Outcome, auto: bool = False) -> str:
    """One sentence for the status beside a key; a failure adds what it most likely means. `auto`: the card
    kept Model = Auto (`keeps_auto`), so the sentence names what Auto answered with."""
    if outcome.cancelled:
        return 'Stopped.'
    if outcome.ok:
        parts = []
        if outcome.vision:
            parts.append('images')
        if outcome.files:
            parts.append('files')
        if outcome.audio is True:
            parts.append('audio')
        extra = f" It can read {' and '.join(parts)}." if parts else ''
        lead = f"Auto: works with {outcome.model}." if auto else f"Works with {outcome.model}."
        return f"{lead}{extra}"
    headline = f"None of the {outcome.tried} models answered." if outcome.tried > 1 else 'It did not answer.'
    why = reason(outcome.error, many=outcome.tried > 1)
    if outcome.audio is True:
        return 'Audio recognition works. Text translation could not be verified.'
    return f"{headline} {why}" if why else headline


def persisted_status(entry: Dict, cached: Optional[bool]) -> Tuple[str, str]:
    """(sentence, tone) for a saved key shown again: what the last startup check (`cached`: True, False
    or None) and the last Test (the entry's capability flags) found out. ('', MUTED) when nothing is known."""
    if cached is False:
        return 'It did not answer the last time CrossTrans checked.', DANGER
    if entry.get('file_capable'):
        if entry.get('audio_capable') is True:
            return ('Tested. It can read images, files and audio.' if entry.get('vision_capable') else
                    'Tested. It can read files and audio.'), SUCCESS
        what = 'images and files' if entry.get('vision_capable') else 'files'
        return f'Tested. It can read {what}.', SUCCESS
    if cached is True:
        return 'It answered the last time CrossTrans checked.', SUCCESS
    if 'file_capable' in entry:
        return 'The last test did not pass.', DANGER
    return '', MUTED


def progress_text(done: int, total: int, stage: str) -> str:
    """The status while a test runs."""
    if stage == 'vision':
        return 'Connected. Checking images...'
    if stage == 'audio':
        return 'Connected. Checking audio...'
    return f'Trying {done} of {total}...' if total > 1 else 'Testing...'


def merge_rows(rows: Sequence[Tuple[str, str, str]], existing: Sequence[Dict]) -> List[Dict]:
    """The key list to save from what the page shows. `rows` is (provider, model, key) in the order shown;
    `existing` is what is saved now. A row with neither a model nor a key is dropped, "Auto" is saved as an
    empty model (auto-detection), and the capability flags a test found (`vision_capable`,
    `file_capable`) are carried over from the saved entry with the same key and model - they change only
    when that key is tested again."""
    saved = {((item.get('api_key') or ''), (item.get('model_name') or ''), item.get('provider') or AUTO):
             item for item in existing}
    out: List[Dict] = []
    for provider, model, key in rows:
        model = '' if is_auto(model) else model.strip()
        key = (key or '').strip()
        if not (model or key):
            continue
        entry = {'model_name': model, 'api_key': key, 'provider': provider or AUTO}
        previous = saved.get((key, model, provider or AUTO))
        if previous:
            for flag in ('vision_capable', 'vision_probe_version', 'file_capable', 'id',
                         'audio_capable', 'audio_model', 'audio_error', 'audio_probe_version'):
                if flag in previous:
                    entry[flag] = previous[flag]
        out.append(entry)
    return out


def any_working(manager, keys: Sequence[Dict[str, str]], model_map: Dict[str, Sequence[str]],
                patterns: Dict[str, str], defaults: Optional[Dict[str, Sequence[str]]] = None,
                per_key: int = 4, progress: Optional[Callable[[int, int], None]] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> Tuple[bool, Dict[str, bool]]:
    """Does any saved key work? Each key tries at most `per_key` pairs (the full search belongs to its own Test
    button). Returns (any worked, {key: worked}) for the keys that were tried; stops at the first that works."""
    with_keys = [item for item in keys if (item.get('api_key') or '').strip()]
    status: Dict[str, bool] = {}
    for index, item in enumerate(with_keys, 1):
        if cancelled is not None and cancelled():
            return False, status
        key = item['api_key'].strip()
        if progress is not None:
            progress(index, len(with_keys))
        pairs = plan(item.get('provider', AUTO), item.get('model_name', ''), key, model_map, patterns,
                     defaults)[:per_key]
        outcome = run(manager, pairs, key, cancelled=cancelled)
        status[key] = outcome.ok
        if outcome.ok:
            return True, status
    return False, status

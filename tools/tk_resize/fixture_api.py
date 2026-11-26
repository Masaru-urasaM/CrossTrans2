"""Replace transport only; retain production prompts, fallback and attribution."""
from dataclasses import dataclass
from collections import deque
from functools import wraps
import json
from pathlib import Path

from src.core.api_manager import AIAPIManager
from src.core.source_marker import source_clause
from src.core.translation import TranslationService
from src.core.translation_alignment import segment_source
from src.core.translation_task import check_current, pause, TranslationStopped


def record_transport(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception as error:
            if self.recorder:
                self.recorder.exception('fixture.transport.error', error,
                    expected=isinstance(error, (RuntimeError, OSError, TranslationStopped)), mode=self.request.mode)
            raise
    return guarded


@dataclass(frozen=True)
class FixtureRequest:
    source: str
    target: str
    mode: str = 'text'
    delay: float = 0.15
    failure: str = ''
    fail_primary: bool = False
    words: tuple[str, ...] = ()
    source_language: str | None = None


class FixtureAPIManager(AIAPIManager):
    def __init__(self, recorder=None, capture_payloads=False):
        super().__init__()
        self.request = FixtureRequest('', 'English')
        self.recorder = recorder
        self.calls = deque(maxlen=32)
        self.capture_payloads = capture_payloads

    def _start(self, model, prompt, images=(), files=None):
        check_current()
        call = dict(model=model, prompt_chars=len(prompt), images=len(images), files=len(files or {}))
        if self.capture_payloads:
            call.update(prompt=prompt, image_paths=tuple(images), file_contents=dict(files or {}))
        self.calls.append(call)
        if self.recorder:
            self.recorder.event('fixture.request', model=model, mode=self.request.mode,
                                source_chars=len(self.request.source), images=len(images),
                                files=len(files or {}))
        pause(self.request.delay)
        if self.request.failure:
            raise RuntimeError(self.request.failure)
        if self.request.fail_primary and model == 'fixture-primary':
            raise RuntimeError('Fixture primary rejected the request (HTTP 429)')

    def _text(self, text):
        if not text.strip():
            return text
        if self.request.mode == 'grammar':
            return text
        pairs = {'こんにちは。': 'Hello.', '今日は晴れです。': 'It is sunny today.',
                 '日本語を勉強しています。': 'I am studying Japanese.',
                 'Hello.': 'こんにちは。', 'It is sunny today.': '今日は晴れです。',
                 'I am studying Japanese.': '日本語を勉強しています。'}
        if self.request.target == 'Japanese' and text.strip() in pairs:
            candidate = pairs[text.strip()]
            if any(ord(char) > 0x3000 for char in candidate):
                return candidate
        if self.request.target == 'English' and text.strip() in pairs:
            candidate = pairs[text.strip()]
            if candidate.isascii():
                return candidate
        # Unknown fixture text is deliberately preserved, never presented as AI output.
        return text

    @record_transport
    def _route_generate_content(self, provider, api_key, model_name, prompt, image_path=None):
        self._start(model_name, prompt, [image_path] if image_path else [])
        request = self.request
        if request.mode == 'dictionary':
            source = request.source_language or 'Unknown'
            return '\n\n---\n\n'.join(f'''## [{word}]

1. **Translation**: Offline fixture equivalent
2. **Source Language**: {source}
3. **Definition**: Offline fixture definition for {word}.
4. **Word Type**: noun
5. **Pronunciation**: /fixture/
6. **Synonyms**: None
7. **Antonyms**: None
8. **Examples**:
   - {word} → Offline fixture example
   - {word} → Another fixture example''' for word in request.words)
        if 'SOURCE UNITS:\n' in prompt and '\nEND SOURCE UNITS' in prompt:
            payload = json.loads(prompt.split('SOURCE UNITS:\n', 1)[1].split('\nEND SOURCE UNITS', 1)[0])
            units = segment_source(request.source)
            if payload != [dict(id=unit.id, text=unit.text) for unit in units]:
                raise ValueError('Fixture received a different canonical source')
            answer = json.dumps(dict(version=1, segments=[dict(source_ids=[unit.id],
                                text=self._text(unit.text)) for unit in units]), ensure_ascii=False)
        else:
            answer = self._text(request.source)
        if source_clause(request.source).asks and 'Begin your response with exactly one' in prompt:
            answer = '[[SOURCE: Unknown]]\n' + answer
        return answer

    @record_transport
    def _route_generate_multimodal(self, provider, api_key, model_name, prompt, image_paths, file_contents):
        self._start(model_name, prompt, image_paths, file_contents)
        source = [f'**[{name}]:**\n{content}' for name, content in file_contents.items()]
        source.extend(f'**[{Path(path).name}]:**\nOffline fixture image text.' for path in image_paths)
        original = '\n\n'.join(source)
        return f'===ORIGINAL===\n{original}\n\n===TRANSLATION===\n{self._text(original)}'


class FixtureTranslationService(TranslationService):
    def __init__(self, config, recorder=None):
        self.recorder = recorder
        super().__init__(config)

    def _configure_api(self):
        if not isinstance(self.api_manager, FixtureAPIManager):
            self.api_manager = FixtureAPIManager(self.recorder)
            self.api_manager.usage_callback = self.key_usage_events.put
        return super()._configure_api()

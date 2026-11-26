"""Renderer-independent operations; all calls run under the shared task deadline."""
from dataclasses import dataclass
from pathlib import Path
import threading

from src.core.attachment_prompt import build_attachment_prompt, split_attachment_answer
from src.core.file_processor import FileProcessor
from src.core.text_files import validate_document
from src.core.translation_task import check_current
from tools.tk_resize.fixture_api import FixtureRequest, FixtureTranslationService


IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}


@dataclass(frozen=True)
class Attachment:
    path: str
    kind: str

    @classmethod
    def admit(cls, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError('Choose a readable file, not a folder.')
        if path.suffix.lower() in IMAGE_SUFFIXES:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            return cls(str(path), 'image')
        validate_document(path)
        return cls(str(path), 'file')


@dataclass(frozen=True)
class TranslationIntent:
    source: str
    target: str
    source_choice: str | None = None
    instruction: str = ''
    mode: str = 'text'
    attachments: tuple[Attachment, ...] = ()
    skip_cache: bool = False
    words: tuple[str, ...] = ()
    linked: bool = True
    fixture_delay: float = 0.15
    fixture_failure: str = ''
    fixture_fail_primary: bool = False


@dataclass(frozen=True)
class TranslationOutcome:
    text: str
    attribution: str | None
    source_verdict: str | None
    extracted_source: str = ''
    attachment_errors: tuple[str, ...] = ()


class TrialServices:
    """A serialized production core with a replaceable offline transport boundary."""
    def __init__(self, config, recorder=None):
        self.config, self.recorder = config, recorder
        self.translation = FixtureTranslationService(config, recorder)
        self.files = FileProcessor(self.translation.api_manager)
        self._operation = threading.Lock()

    def execute(self, task, intent):
        with self._operation, task.activate():
            check_current()
            service = self.translation
            service.api_manager.request = FixtureRequest(intent.source, intent.target, intent.mode,
                intent.fixture_delay, intent.fixture_failure, intent.fixture_fail_primary, intent.words, intent.source_choice)
            if intent.attachments:
                return self._attachments(intent)
            if intent.mode == 'dictionary' and not intent.words:
                raise ValueError('Select a word or enter a phrase first.')
            if not intent.source.strip() and not intent.words:
                raise ValueError('No text selected. Please select text and try again.')
            if intent.mode == 'dictionary':
                result = service.dictionary_lookup(list(intent.words), intent.target,
                    source_language=intent.source_choice, context=intent.source)
            elif intent.mode == 'grammar':
                result = service.fix_grammar(intent.source)
            elif intent.mode == 'merged':
                result = service.translate_or_fix(intent.source, intent.target,
                    skip_cache=intent.skip_cache, source_language=intent.source_choice)
            else:
                result = service.translate_text(intent.source, intent.target, intent.instruction or None,
                    skip_cache=intent.skip_cache, source_language=intent.source_choice,
                    allow_linked=intent.linked)
            check_current()
            return TranslationOutcome(result, service.last_attribution, service.last_source_language)

    def _attachments(self, intent):
        images, contents, errors = [], {}, []
        for attachment in intent.attachments:
            check_current()
            path = Path(attachment.path)
            if not path.is_file():
                errors.append(path.name + ': File no longer exists.')
                continue
            if attachment.kind == 'image':
                images.append(str(path))
            else:
                try:
                    contents[path.name] = self.files.extract_text(str(path))
                except Exception as error:
                    errors.append(path.name + ': ' + str(error))
        if not images and not contents:
            raise ValueError('\n'.join(errors) or 'No readable attachments.')
        prompt = build_attachment_prompt(intent.target, intent.source, [Path(path).name for path in images],
                                          contents, intent.instruction, intent.source_choice)
        service = self.translation
        answer = service.api_manager.translate_multimodal(prompt, images, contents)
        check_current()
        extracted, result = split_attachment_answer(answer)
        credit = service.api_manager.last_attribution
        service.history_manager.add_entry(intent.source or f'[{len(intent.attachments)} attachment(s)]',
            result, intent.target, source_type='multimodal', model_used=credit or 'Auto',
            source_lang=intent.source_choice or '', source_verdict=intent.source_choice or '',
            source_choice=intent.source_choice or '')
        return TranslationOutcome(result, credit, intent.source_choice, extracted, tuple(errors))

    def history_snapshot(self):
        return self.translation.history_manager.get_history()

    def seed_history(self, count=200):
        """Deterministic data in display order, without repeated profile disk writes."""
        count = min(self.translation.history_manager.MAX_HISTORY, max(0, count))
        rows = [dict(id=f'fixture-{index:03}', timestamp=1800000000 - index,
                     original=f'{index + 1:03} 日本語を勉強しています。\n今日は晴れです。',
                     translated=f'{index + 1:03} I am studying Japanese.\nIt is sunny today.',
                     target_lang='English', source_lang='Japanese', source_choice='Japanese',
                     source_type='text', model_used='fixture-primary (OpenAI)') for index in range(count)]
        self.config.set('history', rows)
        return rows

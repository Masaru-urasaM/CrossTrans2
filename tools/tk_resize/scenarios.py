"""Deterministic paired workloads. Descriptors record actual content hashes and limits."""
from dataclasses import dataclass
import hashlib
import json


JAPANESE = '日本語を勉強しています。\n今日は晴れです。'
ENGLISH = 'I am studying Japanese.\nIt is sunny today.'
DICTIONARY = 'one two three four five six seven eight nine ten alpha beta gamma delta epsilon zeta eta theta iota kappa'


def content_identity(source, result, *, dictionary_context='', words=(), phrases=(), dictionary_rows=None, basic_modes=()):
    words, phrases = list(words), list(phrases)
    def text_hash(text):
        return hashlib.sha256(text.encode('utf-8')).hexdigest()
    def rows_hash(rows):
        return text_hash(json.dumps(rows, ensure_ascii=False, separators=(',', ':')))
    return dict(identity_schema=1, actual_source_chars=len(source), actual_source_sha256=text_hash(source),
                actual_result_sha256=text_hash(result), nlp_basic_mode_languages=sorted(set(basic_modes)),
                actual_dictionary_context_sha256=text_hash(dictionary_context),
                actual_dictionary_words=len(words), actual_dictionary_words_sha256=rows_hash(words),
                actual_dictionary_phrases=len(phrases), actual_dictionary_phrases_sha256=rows_hash(phrases),
                actual_dictionary_rows=len(phrases) if dictionary_rows is None else dictionary_rows)


@dataclass(frozen=True)
class Workload:
    name: str
    source: str
    source_language: str = 'Japanese'
    target: str = 'English'
    furigana: bool = True
    linked: bool = True
    surface: str = 'main'
    history_count: int = 200
    phrase_count: int = 1
    attachment: bool = False
    gallery_only: bool = False

    def descriptor(self):
        return dict(name=self.name, source_chars=len(self.source), source_utf16_units=len(self.source.encode('utf-16-le')) // 2,
                    source_sha256=hashlib.sha256(self.source.encode()).hexdigest(), source_language=self.source_language,
                    target=self.target, furigana_requested=self.furigana, linked_requested=self.linked,
                    surface=self.surface, history_count=self.history_count, phrase_count=self.phrase_count,
                    attachment=self.attachment)


WORKLOADS = {
    row.name: row for row in [
        Workload('resident-hidden', '', furigana=False, linked=False, surface='hidden'),
        Workload('empty', ''),
        Workload('short', JAPANESE),
        Workload('short-result-japanese', ENGLISH, source_language='English', target='Japanese'),
        Workload('ruby-linked', (JAPANESE + '\n') * 50),
        Workload('ruby-only', (JAPANESE + '\n') * 50, linked=False),
        Workload('linked-only', (JAPANESE + '\n') * 50, furigana=False),
        Workload('plain-long', (JAPANESE + '\n') * 50, furigana=False, linked=False),
        Workload('annotation-cutoff', (JAPANESE + '\n') * 180),
        Workload('quick', JAPANESE, surface='quick'),
        Workload('two-windows', (JAPANESE + '\n') * 50, surface='both'),
        Workload('history-200', JAPANESE, surface='history'),
        Workload('dictionary-20', DICTIONARY, source_language='English', target='Japanese',
                 surface='dictionary', phrase_count=20),
        Workload('text-attachment', '', source_language='English', attachment=True),
        Workload('lifecycle-50', JAPANESE, surface='cycles'),
        Workload('error-view', 'Hello.', source_language='English', target='Japanese', surface='error', gallery_only=True),
        Workload('replace-preview', JAPANESE, surface='replace', gallery_only=True),
        Workload('expanded-view', ENGLISH, source_language='English', target='Japanese', surface='expanded', gallery_only=True),
        Workload('options-view', ENGLISH, source_language='English', target='Japanese', surface='options', gallery_only=True),
        Workload('dictionary-result', DICTIONARY, source_language='English', target='Japanese', surface='dictionary-result', gallery_only=True),
        Workload('external-preview', 'Hello.', source_language='English', target='Japanese', surface='external-preview', gallery_only=True),
        Workload('external-error', 'Hello.', source_language='English', target='Japanese', surface='external-error', gallery_only=True),
    ]
}


def workload(name):
    try:
        return WORKLOADS[name]
    except KeyError as error:
        raise ValueError(f'Unknown fixture workload: {name}') from error

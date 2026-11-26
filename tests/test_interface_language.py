"""Acceptance checks for presentation translation and catalog integrity."""
import gc
import string
import weakref

import pytest

from src.ui import i18n


@pytest.fixture(autouse=True)
def english_interface(monkeypatch):
    monkeypatch.setattr(i18n, '_language', 'en')
    monkeypatch.setattr(i18n, '_widgets', type(i18n._widgets)())


def fields(message):
    return sorted(field for _, field, _, _ in string.Formatter().parse(message) if field is not None)


def test_every_japanese_message_preserves_its_arguments():
    messages = i18n.catalog()
    assert len(messages) >= 400
    for source, translations in messages.items():
        assert isinstance(source, str) and source.strip()
        assert isinstance(translations['ja'], str) and translations['ja'].strip(), source
        assert fields(source) == fields(translations['ja']), source


def test_english_is_default_and_unknown_locale_falls_back():
    assert i18n.language() == 'en'
    assert i18n.tr('Copy') == 'Copy'
    i18n.set_language('unsupported')
    assert i18n.language() == 'en'


def test_japanese_controls_and_unknown_messages():
    i18n.set_language('ja')
    assert i18n.tr('Copy') == 'コピー'
    assert i18n.tr('unregistered diagnostic') == 'unregistered diagnostic'


def test_message_arguments_are_not_recursively_translated():
    i18n.set_language('ja')
    assert i18n.tr('Remove {0}', 'Copy') == 'Copyを削除'
    assert i18n.tr('Remove {0}', '{secret}') == '{secret}を削除'


def test_reordered_and_named_arguments():
    i18n.set_language('ja')
    assert i18n.tr('{0} of {1}', 2, 3) == '2 / 3'
    assert i18n.tr('{languages} languages are supported.', languages=15) == '15言語に対応しています。'


def test_formatted_legacy_message_keeps_its_dynamic_data():
    i18n.set_language('ja')
    assert i18n.tr('Remove Copy.txt') == 'Copy.txtを削除'
    assert i18n.tr('Downloading... 37%') == 'ダウンロード中… 37%'


class PresentationLabel:
    def __init__(self):
        self.display = ''

    def configure(self, **options):
        self.display = options['text']


def test_open_label_switches_both_ways_from_english_source():
    label = PresentationLabel()
    i18n.register(label, 'Copy')
    i18n.set_language('ja')
    assert label.display == 'コピー'
    i18n.set_language('en')
    assert label.display == 'Copy'


def test_closed_labels_are_not_retained_by_registry():
    label = PresentationLabel()
    i18n.register(label, 'Copy')
    reference = weakref.ref(label)
    del label
    gc.collect()
    assert reference() is None
    assert len(i18n._widgets) == 0


def test_switching_language_never_formats_data_braces_again():
    label = PresentationLabel()
    i18n.register(label, 'Remove {customer}.txt')
    i18n.set_language('ja')
    assert label.display == '{customer}.txtを削除'
    i18n.set_language('en')
    assert label.display == 'Remove {customer}.txt'


def test_dictionary_title_translates_each_language_without_modifying_payloads():
    i18n.set_language('ja')
    assert i18n.tr('Dictionary (Japanese → English)') == '辞書（日本語 → 英語）'

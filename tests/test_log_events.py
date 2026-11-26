"""Structured log events (src/utils/log_events.py) and how the JSON Lines file carries them.

A result the user saw has to be checkable from the log afterwards - which device recorded, which model
answered, how long each step took - without the log holding what the user said or wrote (Decision 78).
"""
import json
import logging
import re

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.utils import log_events, logging_setup


@pytest.fixture
def lines():
    """Records `log_event` emits on the root logger, formatted by the file formatter."""
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append((record, json.loads(logging_setup.JsonLinesFormatter().format(record))))

    root = logging.getLogger()
    handler = Capture(level=logging.DEBUG)
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield captured
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


class TestLogEvent:
    def test_the_file_line_carries_event_session_and_data(self, lines):
        log_events.log_event('Audio', 'recording.stop', sid='ab12cd34', seconds=12.5, frames=200000)
        record, entry = lines[-1]
        assert entry['tag'] == 'Audio'
        assert entry['event'] == 'recording.stop'
        assert entry['sid'] == 'ab12cd34'
        assert entry['data'] == {'seconds': 12.5, 'frames': 200000}
        assert set(entry) >= {'ts', 'level', 'tag', 'msg'}, 'the required fields stay'

    def test_the_message_stays_readable_on_the_console(self, lines):
        log_events.log_event('API', 'call.ok', provider='Groq', ms=840)
        record, entry = lines[-1]
        assert record.getMessage() == '[API] call.ok provider=Groq ms=840'
        assert entry['msg'] == 'call.ok provider=Groq ms=840', 'the tag is said once'

    def test_no_fields_means_no_data_and_no_trailing_space(self, lines):
        log_events.log_event('Audio', 'panel.open')
        record, entry = lines[-1]
        assert record.getMessage() == '[Audio] panel.open'
        assert 'data' not in entry and 'sid' not in entry

    def test_the_level_is_chosen_by_the_caller(self, lines):
        log_events.log_event('Audio', 'stt.fail', level=logging.WARNING, status=429)
        assert lines[-1][1]['level'] == 'WARNING'

    def test_nested_measurements_are_kept_as_structure(self, lines):
        devices = [{'role': 'pc', 'name': 'Speakers (Realtek)', 'channels': 2, 'frames': 48000}]
        log_events.log_event('Audio', 'recording.stop', devices=devices, skipped=('mic',))
        data = lines[-1][1]['data']
        assert data['devices'] == devices
        assert data['skipped'] == ['mic']

    def test_a_long_string_is_cut_so_a_slip_cannot_log_a_paragraph(self, lines):
        log_events.log_event('API', 'call.fail', reason='x' * 500)
        reason = lines[-1][1]['data']['reason']
        assert len(reason) == log_events.MAX_TEXT_FIELD + 3 and reason.endswith('...')

    def test_a_value_json_cannot_hold_becomes_text(self, lines):
        log_events.log_event('Audio', 'file.decode', kind=object)
        assert lines[-1][1]['data']['kind'] == str(object)

    def test_session_ids_are_short_hex_and_differ(self):
        first, second = log_events.new_session_id(), log_events.new_session_id()
        assert re.fullmatch(r'[0-9a-f]{8}', first) and first != second


class TestPlainRecordsAreUnchanged:
    def test_an_ordinary_line_has_no_event_fields(self):
        record = logging.LogRecord('root', logging.INFO, 'src/core/translation.py', 1, '[Trial] used 3', None, None)
        entry = json.loads(logging_setup.JsonLinesFormatter().format(record))
        assert not {'event', 'sid', 'data'} & set(entry)

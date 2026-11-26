"""tools/log_report.py reads the events the app writes and turns one day into sessions, calls and problems
(CHANGELOG Phase 26). The log fixture is written by the real formatter from real `log_event` calls.
"""
import json
import logging

import pytest

from src.utils import logging_setup
from src.utils.log_events import log_event
from tools import log_report


@pytest.fixture
def write_log(tmp_path):
    """Run `body()` with the root logger writing JSON Lines to translator_<day>.log, as the app does."""
    def write(body, name='translator_20261116.log'):
        path = tmp_path / name
        handler = logging.FileHandler(path, encoding='utf-8')
        handler.setFormatter(logging_setup.JsonLinesFormatter())
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            body()
        finally:
            root.removeHandler(handler)
            handler.close()
            root.setLevel(previous)
        return path
    return write


def a_day():
    logging.info('[Startup] platform=Windows-11 python=3.14.10 frozen=True')
    log_event('Audio', 'session.start', sid='s1', kind='recording', source='both', mode_requested='one_step')
    log_event('Audio', 'recording.stop', sid='s1', seconds=6.2, limit_reached=False, tracks=2, skipped=[],
              devices=[{'role': 'PC', 'name': 'Speakers', 'frames': 297600, 'peak': 0.41, 'rms': 0.05, 'track': True},
                       {'role': 'Microphone', 'name': 'Razer Seiren Mini', 'frames': 297600, 'peak': 0.002,
                        'rms': 0.0001, 'track': True}])
    log_event('Audio', 'stt.chunk', sid='s1', index=1, total=1, source='PC', seconds=6.7, cached=False,
              key_slot=1, provider='Groq', model='whisper-large-v3', engine='transcription', ms=812, chars=96)
    log_event('Audio', 'mode.used', sid='s1', requested='one_step', used='two_step', reason='engine_without_one_step')
    log_event('Audio', 'session.done', sid='s1', kind='recording', outcome='ok', record_s=6.2, stt_ms=830,
              translate_ms=1210, total_ms=8400, transcript_chars=96, translation_chars=88,
              models='openai/gpt-oss-120b (Groq)')
    log_event('API', 'call.fail', level=logging.WARNING, kind='text', provider='Groq', model='openai/gpt-oss-120b',
              key_slot=1, ms=300, status=429, error='ProviderRequestError', message='HTTP 429: rate limit')
    log_event('API', 'call.ok', kind='text', provider='Groq', model='openai/gpt-oss-120b', key_slot=1, ms=1100)
    log_event('API', 'call.ok', kind='text', provider='Groq', model='openai/gpt-oss-120b', key_slot=1, ms=900)
    log_event('Screenshot', 'captured', width=514, height=117, kb=7.4)
    log_event('Screenshot', 'translated', ms=1400, ocr_chars=160, translation_chars=150)
    log_event('Attachments', 'added', ext='.png', kind='image', kb=12.0, count=1)
    log_event('Attachments', 'rejected', ext='.mp4', reason='audio_off')
    logging.warning('[Update] Private updates need a GitHub token.')
    logging.warning('[Update] Private updates need a GitHub token.')


class TestSummary:
    def test_one_session_is_joined_by_its_id(self, write_log):
        path = write_log(a_day)
        summary = log_report.summarize(*log_report.read_entries([str(path)]), files=[str(path)])
        session, = summary['audio_sessions']
        assert session['sid'] == 's1' and session['start']['kind'] == 'recording'
        assert session['mode']['reason'] == 'engine_without_one_step'
        assert [d['role'] for d in session['recording']['devices']] == ['PC', 'Microphone']
        assert session['done']['outcome'] == 'ok' and session['chunks'][0]['provider'] == 'Groq'

    def test_api_calls_are_grouped_with_timings_and_statuses(self, write_log):
        path = write_log(a_day)
        group, = log_report.summarize(*log_report.read_entries([str(path)]))['api_calls']
        assert (group['ok'], group['fail'], group['median_ms'], group['max_ms']) == (2, 1, 900, 1100)
        assert group['statuses'] == {'429': 1} and group['slots'] == {'1': 3}

    def test_screenshots_attachments_and_repeated_warnings(self, write_log):
        path = write_log(a_day)
        summary = log_report.summarize(*log_report.read_entries([str(path)]))
        assert summary['screenshots'] == {'captured': 1, 'translated': 1, 'failed': 0, 'median_ms': 1400}
        assert summary['attachments']['added'] == {'image': 1}
        assert summary['attachments']['rejected'] == {'audio_off': 1}
        update = next(p for p in summary['problems'] if p['tag'] == 'Update')
        assert update['count'] == 2

    def test_a_broken_line_is_counted_not_fatal(self, tmp_path):
        path = tmp_path / 'translator_20261116.log'
        path.write_text('{"ts": "x", "level": "INFO", "tag": "a", "msg": "ok"}\nnot json\n[1, 2]\n', encoding='utf-8')
        entries, bad = log_report.read_entries([str(path)])
        assert len(entries) == 1 and bad == 2


class TestFiles:
    def test_the_newest_day_and_its_rotated_files_oldest_first(self, tmp_path):
        for name in ('translator_20261115.log', 'translator_20261116.log', 'translator_20261116.log.1',
                     'translator_20261116.log.2', 'notes.log', 'translator_2026111.log'):
            (tmp_path / name).write_text('', encoding='utf-8')
        names = [path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1] for path in log_report.day_files(str(tmp_path))]
        assert names == ['translator_20261116.log.2', 'translator_20261116.log.1', 'translator_20261116.log']
        assert len(log_report.day_files(str(tmp_path), '20261115')) == 1


class TestCommandLine:
    def test_the_text_report_names_the_session_and_its_devices(self, write_log, capsys):
        path = write_log(a_day)
        assert log_report.main([str(path)]) == 0
        out = capsys.readouterr().out
        assert '[s1]' in out and 'mode=one_step->two_step (engine_without_one_step)' in out
        assert 'Microphone: Razer Seiren Mini frames=297600 peak=0.002' in out
        assert 'API calls: 3' in out and '2 x WARNING [Update]' in out

    def test_json_is_one_object_a_tool_can_read(self, write_log, capsys):
        path = write_log(a_day)
        assert log_report.main([str(path), '--json']) == 0
        data = json.loads(capsys.readouterr().out)
        assert data['audio_sessions'][0]['done']['translate_ms'] == 1210
        assert data['levels'] == {'INFO': 12, 'WARNING': 3}

    def test_no_log_is_an_error_exit(self, tmp_path, capsys):
        assert log_report.main(['--dir', str(tmp_path)]) == 1
        assert 'no log file' in capsys.readouterr().err

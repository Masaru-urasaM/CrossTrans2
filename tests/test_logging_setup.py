"""
Where the log file goes, and how long it stays.

The log is the only thing anyone has when a user reports a crash, and in the
shipped EXE it was being written where it could never be read. `setup_logging()`
derived the directory from `__file__`, which under a PyInstaller onefile build
points inside the temporary `_MEIxxxxx` extraction directory - deleted on exit.
Measured 2026-09-07: six orphaned `_MEI*` directories (202 MB) on one machine,
each holding a `logs/translator_*.log` that nothing would ever have found.

Moving the frozen log to %APPDATA% creates the opposite problem - nothing there
cleans up after it - so retention is pinned here as well.
"""
import os
import json
import logging
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.constants import APP_NAME
from src.utils import logging_setup


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def frozen(monkeypatch):
    """Pretend we are the PyInstaller EXE."""
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    return True


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv('APPDATA', str(tmp_path))
    return tmp_path


def touch(path, age_days=0.0):
    path.write_text('log line\n', encoding='utf-8')
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


class TestWhereTheLogGoes:
    def test_from_source_it_sits_next_to_the_code(self):
        assert logging_setup.is_frozen() is False
        assert logging_setup.resolve_log_dir() == os.path.join(PROJECT_ROOT, 'logs')

    def test_from_the_exe_it_goes_to_appdata(self, frozen, appdata):
        assert logging_setup.resolve_log_dir() == os.path.join(str(appdata), APP_NAME, 'logs')

    def test_from_the_exe_it_is_not_next_to_the_bundle(self, frozen, appdata, monkeypatch):
        # The regression: a path derived from __file__ lands in _MEIxxxxx, which
        # PyInstaller deletes on exit, taking the log with it.
        meipass = os.path.join(str(appdata), '_MEI123456')
        monkeypatch.setattr(sys, '_MEIPASS', meipass, raising=False)

        resolved = logging_setup.resolve_log_dir()

        assert '_MEI' not in resolved
        assert resolved.startswith(str(appdata))
        assert os.path.commonpath((resolved, meipass)) != meipass

    def test_it_lands_beside_the_config_the_app_already_keeps(self, frozen, appdata):
        # Same %APPDATA%/CrossTrans2 root as config.json, so support has one place
        # to ask for.
        assert os.path.dirname(logging_setup.resolve_log_dir()) == \
            os.path.join(str(appdata), APP_NAME)


class TestRetention:
    def test_it_deletes_logs_past_the_cutoff(self, tmp_path):
        old = touch(tmp_path / 'translator_20250101.log', age_days=90)

        assert logging_setup.prune_old_logs(str(tmp_path), retention_days=14) == 1
        assert not old.exists()

    def test_it_keeps_recent_logs(self, tmp_path):
        today = touch(tmp_path / 'translator_20260907.log')
        yesterday = touch(tmp_path / 'translator_20260906.log', age_days=1)

        assert logging_setup.prune_old_logs(str(tmp_path), retention_days=14) == 0
        assert today.exists() and yesterday.exists()

    def test_it_touches_nothing_it_did_not_write(self, tmp_path):
        strangers = [
            touch(tmp_path / 'important.log', age_days=999),
            touch(tmp_path / 'translator_notes.log', age_days=999),
            touch(tmp_path / 'translator_2026.log', age_days=999),
            touch(tmp_path / 'translator_20250101.log.bak', age_days=999),
        ]

        logging_setup.prune_old_logs(str(tmp_path), retention_days=14)

        assert all(p.exists() for p in strangers)

    def test_a_file_it_cannot_delete_is_not_fatal(self, tmp_path, monkeypatch):
        # Another instance may hold today's file open on Windows.
        touch(tmp_path / 'translator_20250101.log', age_days=90)
        monkeypatch.setattr(logging_setup.os, 'remove',
                            lambda p: (_ for _ in ()).throw(OSError("locked")))

        assert logging_setup.prune_old_logs(str(tmp_path), retention_days=14) == 0

    def test_an_empty_directory_is_fine(self, tmp_path):
        assert logging_setup.prune_old_logs(str(tmp_path)) == 0

    def test_the_default_retention_is_bounded_but_useful(self):
        assert 7 <= logging_setup.LOG_RETENTION_DAYS <= 90


class TestSetupComposesThem:
    @pytest.fixture
    def quiet(self, monkeypatch, tmp_path):
        """Run setup_logging without touching this process's logging state."""
        monkeypatch.setattr(logging_setup.logging, 'basicConfig', lambda **kw: None)
        monkeypatch.setattr(logging_setup.logging, 'info', lambda *a, **k: None)
        monkeypatch.setattr(logging_setup, 'resolve_log_dir', lambda: str(tmp_path / 'logs'))
        hook = sys.excepthook
        thread_hook = threading.excepthook
        yield tmp_path / 'logs'
        sys.excepthook = hook
        threading.excepthook = thread_hook

    def test_it_creates_the_directory_and_returns_the_file(self, quiet):
        path = logging_setup.setup_logging()

        assert os.path.isdir(str(quiet))
        assert os.path.dirname(path) == str(quiet)
        assert os.path.basename(path).startswith('translator_')
        assert path.endswith('.jsonl')         # .log until 2026-11-19 (TODO Task 26)

    def test_it_prunes_on_the_way_in(self, quiet, monkeypatch):
        pruned = []
        monkeypatch.setattr(logging_setup, 'prune_old_logs',
                            lambda d, *a, **k: pruned.append(d))

        logging_setup.setup_logging()

        assert pruned == [str(quiet)]

    def test_it_installs_the_crash_hook(self, quiet):
        logging_setup.setup_logging()

        assert sys.excepthook is not sys.__excepthook__

    def test_a_keyboard_interrupt_still_goes_to_python(self, quiet, monkeypatch):
        logging_setup.setup_logging()
        seen = []
        monkeypatch.setattr(sys, '__excepthook__', lambda *a: seen.append(a))
        critical = []
        monkeypatch.setattr(logging_setup.logging, 'critical', lambda *a, **k: critical.append(a))

        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)

        assert len(seen) == 1
        assert critical == []

    def test_write_crash_report_writes_structured_diagnostics_and_traceback(self, quiet):
        try:
            raise FileNotFoundError('Tcl data directory "_tcl_data" not found.')
        except FileNotFoundError:
            report_path = logging_setup.write_crash_report(
                *sys.exc_info(), stage='startup', log_dir=str(quiet)
            )
        assert report_path is not None and os.path.isfile(report_path)
        content = open(report_path, encoding='utf-8').read()
        assert 'Stage: startup' in content
        assert 'FileNotFoundError: Tcl data directory "_tcl_data" not found.' in content
        assert 'Traceback:' in content

    def test_thread_exception_handler_logs_and_writes_crash_report(self, quiet, monkeypatch):
        logging_setup.setup_logging()
        critical = []
        monkeypatch.setattr(logging_setup.logging, 'critical', lambda *a, **k: critical.append((a, k)))
        try:
            raise RuntimeError('worker failed')
        except RuntimeError:
            exc_type, exc_val, exc_tb = sys.exc_info()
        threading.excepthook(SimpleNamespace(
            exc_type=exc_type, exc_value=exc_val, exc_traceback=exc_tb,
            thread=SimpleNamespace(name='AudioWorker')
        ))
        assert any('AudioWorker' in str(args[0]) for args, _ in critical)
        report_file = quiet / logging_setup.CRASH_REPORT_FILENAME
        assert report_file.is_file()
        assert 'Stage: thread:AudioWorker' in report_file.read_text(encoding='utf-8')

    def test_early_boot_events_are_flushed_into_main_log_on_setup(self, quiet, monkeypatch):
        warnings = []
        monkeypatch.setattr(logging_setup.logging, 'warning', lambda msg, *a, **k: warnings.append(msg))
        monkeypatch.setattr(sys, '_crosstrans_boot_events',
                            [{'level': 'WARNING', 'msg': '[Boot] Extracted 839 Tcl library files'}],
                            raising=False)
        logging_setup.setup_logging()
        assert warnings == ['[Boot] Extracted 839 Tcl library files']
        assert sys._crosstrans_boot_events == []

    def test_debug_mode_detects_cli_env_flag_file_and_toggle(self, quiet, monkeypatch):
        monkeypatch.setattr(sys, '_crosstrans_debug', False, raising=False)
        monkeypatch.delenv('CROSSTRANS_DEBUG', raising=False)
        assert logging_setup.is_debug_mode(argv=[]) is False
        assert logging_setup.is_debug_mode(argv=['--debug']) is True

        monkeypatch.setenv('CROSSTRANS_DEBUG', '1')
        assert logging_setup.is_debug_mode(argv=[]) is True
        monkeypatch.delenv('CROSSTRANS_DEBUG', raising=False)

        logging_setup.set_debug_logging(True)
        assert os.path.isfile(logging_setup.debug_flag_path(str(quiet)))
        assert logging_setup.is_debug_mode(argv=[]) is True
        logging_setup.set_debug_logging(False)
        assert not os.path.isfile(logging_setup.debug_flag_path(str(quiet)))
        assert logging_setup.is_debug_mode(argv=[]) is False


class TestJsonLines:
    """The file log is machine-readable (global Rule 9).

    None of the ~458 call sites changed: the tag is derived from the record, so
    the format is a property of the formatter and not of everybody's messages.
    """

    @staticmethod
    def _record(msg, level=logging.INFO, pathname='src/core/translation.py',
                args=None, exc_info=None):
        return logging.LogRecord('root', level, pathname, 42, msg, args, exc_info)

    @staticmethod
    def _emit(record):
        return json.loads(logging_setup.JsonLinesFormatter().format(record))

    def test_every_required_field_is_present(self):
        entry = self._emit(self._record("hello"))
        assert set(entry) >= {'ts', 'level', 'tag', 'msg'}
        assert entry['level'] == 'INFO'
        assert entry['msg'] == 'hello'

    def test_one_record_is_exactly_one_line(self):
        line = logging_setup.JsonLinesFormatter().format(
            self._record("first\nsecond"))
        assert '\n' not in line, "a newline inside a record would split the entry"
        assert json.loads(line)['msg'] == "first\nsecond"

    def test_a_bracketed_prefix_becomes_the_tag_and_leaves_the_message(self):
        entry = self._emit(self._record("[ModelRotation] cached model failed"))
        assert entry['tag'] == 'ModelRotation'
        assert entry['msg'] == 'cached model failed', "the tag must not be said twice"

    def test_without_a_prefix_the_tag_is_the_module(self):
        entry = self._emit(self._record("plain message"))
        assert entry['tag'] == 'translation'
        assert 'mod' not in entry, "mod would just repeat the tag"

    def test_the_module_is_kept_when_the_tag_hides_it(self):
        entry = self._emit(self._record("[Trial] quota exhausted"))
        assert entry['tag'] == 'Trial' and entry['mod'] == 'translation'

    def test_a_bracket_that_is_not_a_tag_is_left_alone(self):
        entry = self._emit(self._record("[] empty"))
        assert entry['tag'] == 'translation' and entry['msg'] == '[] empty'

    def test_percent_args_are_formatted(self):
        entry = self._emit(self._record("tried %s times", args=(3,)))
        assert entry['msg'] == 'tried 3 times'

    def test_a_broken_format_string_does_not_lose_the_line(self):
        entry = self._emit(self._record("tried %d times", args=('not a number',)))
        assert entry['msg'].startswith('<unformattable log record:')

    def test_japanese_stays_readable_in_the_file(self):
        # Round-tripping proves nothing here: json.loads decodes \uXXXX escapes
        # back to the same string either way. What matters is that somebody
        # opening the log sees the words and not 16 characters of escape.
        line = logging_setup.JsonLinesFormatter().format(
            self._record("翻訳しました: 日本語"))
        assert "日本語" in line, "ensure_ascii=False is what keeps this legible"
        assert json.loads(line)['msg'] == "翻訳しました: 日本語"

    def test_a_crash_carries_its_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            entry = self._emit(self._record("Uncaught exception:", logging.CRITICAL,
                                            exc_info=sys.exc_info()))
        assert 'ValueError: boom' in entry['trace']

    def test_the_timestamp_is_iso_8601_with_a_real_offset(self):
        from datetime import datetime as _dt
        ts = self._emit(self._record("x"))['ts']
        parsed = _dt.fromisoformat(ts)          # raises if it is not ISO-8601
        assert parsed.utcoffset() is not None, "a bare local time is ambiguous"


class TestTheFileAndConsoleDiffer:
    @pytest.fixture
    def handlers(self, monkeypatch, tmp_path):
        captured = {}
        monkeypatch.setattr(logging_setup.logging, 'basicConfig',
                            lambda **kw: captured.update(kw))
        monkeypatch.setattr(logging_setup.logging, 'info', lambda *a, **k: None)
        monkeypatch.setattr(logging_setup, 'resolve_log_dir', lambda: str(tmp_path / 'logs'))
        hook = sys.excepthook
        thread_hook = threading.excepthook
        logging_setup.setup_logging()
        yield captured['handlers']
        sys.excepthook = hook
        threading.excepthook = thread_hook

    def test_the_file_gets_json(self, handlers):
        file_handler = next(h for h in handlers if hasattr(h, 'baseFilename'))
        assert isinstance(file_handler.formatter, logging_setup.JsonLinesFormatter)

    def test_the_console_stays_readable(self, handlers):
        console = next(h for h in handlers if not hasattr(h, 'baseFilename'))
        assert not isinstance(console.formatter, logging_setup.JsonLinesFormatter)

    def test_the_file_is_size_capped_and_rotated(self, handlers):
        from logging.handlers import RotatingFileHandler
        file_handler = next(h for h in handlers if hasattr(h, 'baseFilename'))
        assert isinstance(file_handler, RotatingFileHandler)
        assert file_handler.maxBytes == logging_setup.LOG_MAX_BYTES > 0
        assert file_handler.backupCount == logging_setup.LOG_BACKUP_COUNT > 0

    def test_a_run_that_never_logs_leaves_no_empty_file(self, handlers, tmp_path):
        assert not list((tmp_path / 'logs').glob('translator_*')), \
            "delay=True keeps setup from creating a file nothing wrote to"

    def test_it_really_writes_parseable_lines(self, tmp_path):
        path = tmp_path / 'probe.log'
        handler = logging_setup.RotatingFileHandler(str(path), encoding='utf-8', delay=True)
        handler.setFormatter(logging_setup.JsonLinesFormatter())
        logger = logging.getLogger('jsonl-probe')
        logger.handlers = [handler]
        logger.propagate = False
        logger.error("[API] request failed: 日本語")
        handler.close()

        lines = path.read_text(encoding='utf-8').splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert (entry['level'], entry['tag']) == ('ERROR', 'API')


class TestRotatedFilesArePrunedToo:
    def test_a_rotated_sibling_is_eligible(self, tmp_path):
        old = touch(tmp_path / 'translator_20250101.log.1', age_days=90)
        assert logging_setup.prune_old_logs(str(tmp_path), retention_days=14) == 1
        assert not old.exists()

    def test_a_recent_rotated_sibling_is_kept(self, tmp_path):
        recent = touch(tmp_path / 'translator_20260908.log.2')
        assert logging_setup.prune_old_logs(str(tmp_path), retention_days=14) == 0
        assert recent.exists()

    def test_the_broader_glob_still_spares_strangers(self, tmp_path):
        strangers = [
            touch(tmp_path / 'translator_20250101.log.bak', age_days=999),
            touch(tmp_path / 'translator_20250101.logged', age_days=999),
            touch(tmp_path / 'translator_2026.log.1', age_days=999),
        ]
        logging_setup.prune_old_logs(str(tmp_path), retention_days=14)
        assert all(p.exists() for p in strangers)

    def test_the_daily_size_is_bounded(self):
        per_day = logging_setup.LOG_MAX_BYTES * (1 + logging_setup.LOG_BACKUP_COUNT)
        assert per_day <= 32 * 1024 * 1024, "retention alone cannot cap a runaway loop"


def test_tk_callback_errors_are_logged_when_windowed_exe_has_no_stderr(caplog, monkeypatch):
    monkeypatch.setattr(sys, 'stderr', None)
    error = ValueError('fixture callback failure')
    logging_setup.log_tk_callback_exception(ValueError, error, None)
    assert '[Tk] UI callback failed' in caplog.text
    assert 'ValueError: fixture callback failure' in caplog.text


def test_tk_callback_keyboard_interrupt_keeps_the_exit_contract():
    with pytest.raises(KeyboardInterrupt):
        logging_setup.log_tk_callback_exception(KeyboardInterrupt, KeyboardInterrupt(), None)

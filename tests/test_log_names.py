"""TODO Task 26 (DEFERRED D34, global Rule 9) - the daily JSON Lines logs are named `.jsonl`.

Until 2026-11-19 the file was `translator_YYYYMMDD.log`, though every line is a JSON object. Logs written
before keep that name until the 14-day pruning takes them, so everything that prunes, reads or bundles
the logs takes both kinds, and on the day of the change a day's `.jsonl` counts as newer than its `.log`.
"""
import json
import os
from pathlib import Path
import time
import zipfile

from src.utils import diagnostics, logging_setup
from tests.settings_support import SettingsConfig
from tools import log_report

ROOT = Path(__file__).resolve().parents[1]


def touch(path, age_days=0, text=''):
    path.write_text(text, encoding='utf-8')
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def line(msg):
    return json.dumps({'ts': '2026-11-19T10:00:00+09:00', 'level': 'INFO', 'tag': 'Test', 'msg': msg}) + '\n'


def names(paths):
    return [os.path.basename(str(path)) for path in paths]


def test_a_days_log_is_named_jsonl():
    assert logging_setup.daily_log_name('20261119') == 'translator_20261119.jsonl'
    assert logging_setup.daily_log_name().endswith('.jsonl')


def test_the_boot_hook_writes_the_same_file():
    """`pyi_rth_boot` runs before the app's modules load, so it spells the name itself."""
    source = (ROOT / 'src' / 'utils' / 'pyi_rth_boot.py').read_text(encoding='utf-8')
    assert "f'translator_{datetime.now().strftime(\"%Y%m%d\")}.jsonl'" in source
    assert "strftime(\"%Y%m%d\")}.log'" not in source


MIXED = ['translator_20261118.log', 'translator_20261119.log', 'translator_20261119.log.1',
         'translator_20261119.jsonl', 'translator_20261119.jsonl.1', 'translator_20261119.jsonl.2',
         'notes.log', 'translator_20261119.jsonl.bak', 'translator_2026111.jsonl']


def test_daily_logs_order_both_kinds_oldest_first(tmp_path):
    for name in MIXED:
        touch(tmp_path / name)
    assert names(logging_setup.daily_logs(str(tmp_path))) == [
        'translator_20261118.log', 'translator_20261119.log', 'translator_20261119.jsonl']
    assert names(logging_setup.daily_logs(str(tmp_path), rotated=True)) == [
        'translator_20261118.log', 'translator_20261119.log.1', 'translator_20261119.log',
        'translator_20261119.jsonl.2', 'translator_20261119.jsonl.1', 'translator_20261119.jsonl']
    assert logging_setup.daily_logs(str(tmp_path / 'missing')) == []


def test_pruning_takes_old_files_of_both_kinds_and_nothing_else(tmp_path):
    old = [touch(tmp_path / name, age_days=30) for name in
           ('translator_20261001.log', 'translator_20261002.jsonl', 'translator_20261002.jsonl.1')]
    kept = [touch(tmp_path / 'translator_20261119.jsonl'),
            touch(tmp_path / 'translator_20261001.jsonl.bak', age_days=30)]
    assert logging_setup.prune_old_logs(str(tmp_path), retention_days=14) == 3
    assert not any(path.exists() for path in old) and all(path.exists() for path in kept)


def test_diagnostics_read_the_jsonl_of_the_day_of_the_change(tmp_path):
    touch(tmp_path / 'translator_20261119.log', text=line('before the update'))
    touch(tmp_path / 'translator_20261119.jsonl', text=line('after the update'))
    latest, lines = diagnostics._recent_log_lines(str(tmp_path))
    assert latest == 'translator_20261119.jsonl'
    assert lines and 'after the update' in lines[-1]


def test_the_bug_report_bundles_the_newest_three_files(tmp_path, monkeypatch):
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    for name in ('translator_20261118.log', 'translator_20261119.log', 'translator_20261119.jsonl.1',
                 'translator_20261119.jsonl'):
        touch(log_dir / name, text=line(name))
    monkeypatch.setattr(diagnostics, 'resolve_log_dir', lambda: str(log_dir))
    monkeypatch.setattr(logging_setup, 'resolve_log_dir', lambda: str(log_dir))
    out = diagnostics.export_diagnostic_bundle(tmp_path / 'out' / 'report.zip', config=SettingsConfig())
    with zipfile.ZipFile(out) as bundle:
        logs = {name for name in bundle.namelist() if name.startswith('logs/translator_')}
    assert logs == {'logs/translator_20261119.log', 'logs/translator_20261119.jsonl.1',
                    'logs/translator_20261119.jsonl'}


def test_the_log_report_reads_a_mixed_day_oldest_first(tmp_path):
    for name in MIXED:
        touch(tmp_path / name)
    assert names(log_report.day_files(str(tmp_path))) == [
        'translator_20261119.log.1', 'translator_20261119.log',
        'translator_20261119.jsonl.2', 'translator_20261119.jsonl.1', 'translator_20261119.jsonl']
    assert names(log_report.day_files(str(tmp_path), '20261118')) == ['translator_20261118.log']

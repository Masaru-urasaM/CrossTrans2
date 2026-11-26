"""Real Main/Quick stay fixed while hollow outline previews move until release."""
import json
import os
from pathlib import Path
import sys

import pytest
import quiet_session

from tools.tk_resize.native_trace import RUNTIME
from tools.tk_resize.process import run_owned

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not quiet_session.DESKTOP_NAME, reason=quiet_session.skip_reason())
@pytest.mark.skipif(not (RUNTIME / 'build.json').exists(),
                    reason='needs the locally built native helper (python -m tools.tk_resize.build_native_trace --compiler <gcc>)')
@pytest.mark.parametrize('large', [False, True])
@pytest.mark.parametrize('prepare', [False, True])
def test_two_open_windows_hold_interiors_during_the_full_trajectory(tmp_path, large, prepare):
    output = tmp_path / 'worker'
    command = [sys.executable, '-m', 'tools.tk_resize.worker', '--source', str(ROOT),
               '--output', str(output), '--scenario', 'two-windows', '--final-validation']
    if large:
        command += ['--ui-language', 'ja', '--text-scale', '1.5', '--dpi-factor', '1.5']
    if not prepare:
        command += ['--natural-gesture-start']
    with (tmp_path / 'worker.log').open('w', encoding='utf-8') as stream:
        result = run_owned(command, cwd=ROOT, env=dict(os.environ), stream=stream,
                           timeout=100, started=lambda _pid: None)
    assert result['exit_code'] == 0, (tmp_path / 'worker.log').read_text(encoding='utf-8')[-4000:]
    resize = json.loads((output / 'resize.json').read_text(encoding='utf-8'))
    scenario = json.loads((output / 'scenario.json').read_text(encoding='utf-8'))
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    prepared_path = output / 'gesture-preparation.json'
    if prepare:
        prepared = json.loads(prepared_path.read_text(encoding='utf-8'))
        assert resize['quick_constraints']['start_rect'] == prepared['rectangle']
        assert manifest['gesture_start'] == 'prepared-user-rectangle'
    else:
        assert not prepared_path.exists()
        assert manifest['gesture_start'] == 'natural-first-gesture'
    assert resize['interaction'] == 'outline-on-release'
    assert resize['quick_distinct_window_sizes'] == 1
    assert resize['deferred_content_contract']
    assert resize['quick_distinct_body_sizes'] == 1
    assert len({tuple(row['host']) for row in resize['main_body_observations']}) == 1
    assert len({tuple(row['source']) for row in resize['main_body_observations']}) == 1
    assert len({tuple(row['result']) for row in resize['main_body_observations']}) == 1
    assert len({tuple(row['shell']) for row in resize['main_body_observations']}) == 1
    assert any(row['outline'] for row in resize['quick_body_observations'])
    assert {row['role'] for row in resize['outline_checks']} == {'main','quick'}
    assert all(row['passed'] for row in resize['outline_checks'])
    for role in ('main','quick'):
        assert len({tuple(row['rect']) for row in resize['outline_checks'] if row['role']==role}) >= 2
    assert resize['settlement']['status'] == 'settled'
    assert scenario['workload']['source_ruby_segments'] > 0
    assert scenario['workload']['quick_source_ruby_segments'] > 0
    assert scenario['workload']['quick_link_groups'] > 0
    assert not scenario['callback_errors'] and not scenario['blocked_effects']

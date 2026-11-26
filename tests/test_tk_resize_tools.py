"""Source provenance and acceptance gates cannot silently accept missing evidence."""
import hashlib
import json
from pathlib import Path
import zipfile
import os
import sys

import pytest

from tools.tk_resize import snapshot
from tools.tk_resize.final_validation import (reviewed_clauses, verify_review, jobs_for,
                                             resume_evidence, write_reconciled_report)
from tools.tk_resize.comparison import assess, IDENTITY
from tools.tk_resize.settlement import LayoutSettlement


@pytest.mark.parametrize('work,scale,minimum', [
    ((0, 0, 1920, 1032), 1.0, (415, 217)),
    ((0, 0, 1920, 1032), 1.5, (629, 367)),
    ((-1920, -200, 0, 832), 1.5, (629, 367)),
    ((0, 0, 1000, 700), 1.5, (629, 367)),
])
def test_common_gesture_rectangle_keeps_equal_inputs_inside_the_work_area(work, scale, minimum):
    from tools.tk_resize.worker import gesture_rectangle
    rect = gesture_rectangle(work, scale, minimum)
    x, y, width, height = rect
    assert rect == gesture_rectangle(work, scale, minimum)
    assert work[0] + 10 <= x and work[1] + 10 <= y
    assert x + width <= work[2] - 10 and y + height <= work[3] - 10
    assert width >= minimum[0] and height >= minimum[1]


def test_common_gesture_rectangle_rejects_an_impossible_minimum():
    from tools.tk_resize.worker import gesture_rectangle
    with pytest.raises(ValueError, match='minimum'):
        gesture_rectangle((0, 0, 600, 400), 1.0, (629, 367))


@pytest.mark.parametrize('after_start', [None, [515, 320, 1020, 900], [515, 10, 1020, 900]])
def test_quick_comparison_requires_identical_recorded_gesture_starts(after_start):
    workload = {key: 'same' for key in IDENTITY}
    workload['name'] = 'quick'
    before = dict(manifest={}, metrics={}, summary={}, scenario=dict(workload=workload),
        resize=dict(quick_constraints=dict(start_rect=[515, 10, 1020, 900])))
    after = json.loads(json.dumps(before))
    after['resize']['quick_constraints'] = dict(start_rect=after_start) if after_start else None
    result = assess(before, after)
    assert result['gates']['gesture_start_identity'] == ('pass' if after_start == [515, 10, 1020, 900] else 'fail')


def test_settlement_waits_for_children_and_new_pending_work_even_with_fixed_outer_size():
    tracker = LayoutSettlement(stable_ms=100)
    def state(width, pending=(), inconsistent=()):
        return dict(geometry=[('window', '680x680'), ('content', str(width))],
                    pending=list(pending), inconsistent=list(inconsistent))
    assert tracker.observe(state(511), 0) == 'pending'
    assert tracker.observe(state(502), 80) == 'pending'
    assert tracker.observe(state(474, pending=['scroll-layout']), 200) == 'pending'
    assert tracker.observe(state(474), 250) == 'pending'
    assert tracker.observe(state(474), 360) == 'settled'
    assert tracker.observe(state(474, pending=['text-metrics']), 400) == 'pending'
    assert tracker.report()['release_to_verified_stable_ms'] is None
    assert tracker.observe(state(474), 450) == 'pending'
    assert tracker.observe(state(474), 560) == 'settled'
    assert tracker.report()['release_to_verified_stable_ms'] == 560


def test_settlement_rejects_inconsistent_geometry_missing_evidence_and_deadline():
    tracker = LayoutSettlement(stable_ms=100, timeout_ms=500)
    with pytest.raises(ValueError, match='mapped geometry'):
        tracker.observe(dict(geometry=[], pending=[], inconsistent=[]), 0)
    state = dict(geometry=[('canvas', '474'), ('content', '502')], pending=[],
                 inconsistent=['canvas/content mismatch'])
    assert tracker.observe(state, 0) == 'pending'
    assert tracker.observe(state, 400) == 'pending'
    assert tracker.observe(state, 500) == 'timeout'
    assert tracker.report()['release_to_verified_stable_ms'] is None


def test_resize_driver_stops_recurring_ticks_while_observers_wait():
    from tools.tk_resize.worker import scenario_class
    class Base:
        def _tick(self):
            pytest.fail('The resize driver must not keep polling during settlement')
    controller = object.__new__(scenario_class(Base))
    controller.phase, controller.timer = 'layout-settle', 'previous-driver-timer'
    controller._tick()
    assert controller.timer is None


def test_manual_geometry_observer_buffers_events_without_timestamp_collision_or_disk_writes():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tools.tk_resize.worker import scenario_class
    class Window:
        def __init__(self):
            self.callback = None
        def bind(self, sequence, callback, **kwargs):
            assert sequence == '<Configure>'
            self.callback = callback
    class Base:
        def _show(self):
            pass
    main, quick = Window(), Window()
    controller = object.__new__(scenario_class(Base))
    controller.workload = SimpleNamespace(name='two-windows')
    controller.arguments = SimpleNamespace(manual_visible_desktop=True)
    controller.app = SimpleNamespace(popup=main, quick_translate_manager=SimpleNamespace(popup=quick))
    controller.started = 0
    controller.manual_geometry = []
    controller.recorder = Mock()
    controller._show()
    for window in (main, quick):
        window.callback(SimpleNamespace(widget=window, x=20, y=30, width=640, height=480))
    assert len(controller.manual_geometry) == 2
    assert [row['window'] for row in controller.manual_geometry] == ['main', 'quick']
    assert all('elapsed_ms' in row and row['x'] == 20 for row in controller.manual_geometry)
    controller.recorder.event.assert_not_called()


def test_manual_preparation_rejects_provisional_width_before_positioning(monkeypatch):
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tools.tk_resize.manual_fixture import scenario_factory
    from src.ui.components import dialog
    monkeypatch.setattr(dialog, 'work_area', lambda: (0, 0, 1920, 1032))
    controller = object.__new__(scenario_factory(lambda base: base)(object))
    controller.preparation_started = time.perf_counter()
    popup = Mock()
    popup.winfo_width.return_value = 1
    controller.app = SimpleNamespace(popup=Mock(), quick_translate_manager=SimpleNamespace(
        popup=popup, _layout={'done': True, 'resize_metrics': {'ready': True}}))
    assert not controller._prepare_windows()
    controller.app.popup.geometry.assert_not_called()
    popup.geometry.assert_not_called()


def test_manual_fixture_rejects_offscreen_rectangles_and_supports_negative_origin():
    from tools.tk_resize.manual_fixture import inside
    assert inside((20, 30, 916, 809), (0, 0, 1920, 1032))
    assert not inside((1899, 40, 2314, 1052), (0, 0, 1920, 1032))
    assert inside((-1900, -180, -1000, 580), (-1920, -200, 0, 880))


@pytest.mark.parametrize('status', [None, 'pending', 'failed', 'settled'])
def test_comparison_requires_actual_settlement_from_both_workers(status):
    workload = {key: 0 for key in IDENTITY}
    workload['name'] = 'short'
    data = dict(manifest=dict(python='fixture', os='fixture', logical_cpus=8, tcl_patchlevel='9.0.4',
        tk_scaling=1.33, ui_scale=1.0, theme='light', ui_language='en', text_scale=1.0,
        dpi_factor=1.0, work_area=[0, 0, 1920, 1040], desktop='private'), metrics={},
        summary=dict(errors=0, gates=dict(automated_execution=dict(status='pass'))),
        scenario=dict(workload=workload), resize={'settlement': {'status': 'settled'}})
    after = json.loads(json.dumps(data))
    after['resize']['settlement']['status'] = status
    result = assess(data, after)
    assert result['gates']['layout_settlement'] == ('pass' if status == 'settled' else 'fail')


@pytest.mark.parametrize('omit_failed', [False, True])
def test_complete_module_recheck_retires_renamed_cases_without_losing_original_failures(tmp_path, omit_failed):
    import xml.etree.ElementTree as ET
    original, repair = tmp_path / 'original', tmp_path / 'repair'
    original.mkdir()
    repair.mkdir()
    old = dict(sha256='old', files={'tests/test_example.py': 'old-test', 'src/example.py': 'old-code'})
    current = dict(sha256='current', files={'tests/test_example.py': 'new-test', 'src/example.py': 'new-code'})
    suite = ET.Element('testsuite')
    for name in ('old-name', 'keep', 'failed'):
        case = ET.SubElement(suite, 'testcase', classname='tests.test_example', name=name)
        if name == 'failed':
            ET.SubElement(case, 'failure')
    for index in range(4347):
        ET.SubElement(suite, 'testcase', classname='tests.test_untouched', name=str(index))
    ET.ElementTree(suite).write(original / 'todo-suite-original.xml', encoding='utf-8')
    (original / 'batch.json').write_text(json.dumps(dict(source=old,
        jobs=[dict(name='full-suite', status='finished', exit_code=1)])))
    suite = ET.Element('testsuite')
    for name in (('new-name', 'keep') if omit_failed else ('new-name', 'keep', 'failed')):
        ET.SubElement(suite, 'testcase', classname='tests.test_example', name=name)
    ET.ElementTree(suite).write(repair / 'results.xml', encoding='utf-8')
    (repair / 'run.json').write_text(json.dumps(dict(status='finished', exit_code=0, source=current,
        complete_module_scope=True, scope=['tests/test_example.py'],
        covered_source={'src/example.py': ['tests/test_example.py']})))
    if omit_failed:
        with pytest.raises(ValueError, match='removed'):
            resume_evidence(original / 'batch.json', repair / 'run.json', current)
    else:
        evidence = resume_evidence(original / 'batch.json', repair / 'run.json', current)
        assert evidence['reconciled_cases'] == 4350
        assert evidence['rechecks'][0]['retired_cases'] == [dict(
            classname='tests.test_example', name='old-name', module='tests/test_example.py')]
        report = tmp_path / 'reconciled.xml'
        assert write_reconciled_report(evidence, report)['tests'] == 4350
        names = {row.get('name') for row in ET.parse(report).iter('testcase')}
        assert 'old-name' not in names and 'new-name' in names


@pytest.mark.parametrize('followup', ['markdown', 'code', 'asset', 'hash-only'])
def test_documentation_followup_never_relabels_tests_or_accepts_code_asset_drift(tmp_path, followup):
    import xml.etree.ElementTree as ET
    original, repair = tmp_path / 'original', tmp_path / 'repair'
    original.mkdir()
    repair.mkdir()
    tested = dict(sha256='tested', files={'tests/test_example.py': 'same', 'src/example.py': 'same',
                                         'TODO.md': 'tested-doc', 'CrossTrans.png': 'same-asset'})
    suite = ET.Element('testsuite')
    for index in range(4350):
        ET.SubElement(suite, 'testcase', classname='tests.test_example', name=str(index))
    ET.ElementTree(suite).write(original / 'todo-suite-original.xml', encoding='utf-8')
    (original / 'batch.json').write_text(json.dumps(dict(source=tested,
        jobs=[dict(name='full-suite', status='finished', exit_code=0)])))
    suite = ET.Element('testsuite')
    ET.SubElement(suite, 'testcase', classname='tests.test_example', name='0')
    ET.ElementTree(suite).write(repair / 'results.xml', encoding='utf-8')
    (repair / 'run.json').write_text(json.dumps(dict(status='finished', exit_code=0, source=tested,
        scope=['tests/test_example.py'], covered_source={})))
    current = json.loads(json.dumps(tested))
    current['sha256'] = 'reviewed'
    if followup != 'hash-only':
        field = {'markdown': 'TODO.md', 'code': 'src/example.py', 'asset': 'CrossTrans.png'}[followup]
        current['files'][field] = 'changed'
    if followup != 'markdown':
        with pytest.raises(ValueError, match='another current source'):
            resume_evidence(original / 'batch.json', repair / 'run.json', current)
    else:
        evidence = resume_evidence(original / 'batch.json', repair / 'run.json', current)
        assert evidence['recheck_source_sha256'] == 'tested'
        assert evidence['current_review_source_sha256'] == 'reviewed'
        assert evidence['documentation_followup']['changed_markdown'] == ['TODO.md']
        report = tmp_path / 'derived.xml'
        write_reconciled_report(evidence, report)
        provenance = json.loads(report.with_suffix('.provenance.json').read_text())
        assert all(row['source_sha256'] == 'tested' for row in provenance['cases'])


def test_snapshot_round_trip_includes_runtime_constants_and_assets(tmp_path):
    root, target = tmp_path / 'root', tmp_path / 'checkout'
    (root / 'src').mkdir(parents=True)
    (root / 'src/constants.py').write_text('LIMIT = 200\n', encoding='utf-8')
    (root / 'CrossTrans.png').write_bytes(b'fixture asset')
    source = snapshot.archive_source(root, tmp_path / 'source.zip')
    snapshot.extract_checked(tmp_path / 'source.zip', target, source)
    assert snapshot.manifest(target) == source
    (root / 'src/constants.py').write_text('LIMIT = 201\n', encoding='utf-8')
    assert snapshot.manifest(root) != source


@pytest.mark.parametrize('kind', ['traversal', 'tampering', 'missing'])
def test_source_archive_rejects_untrusted_or_incomplete_contents(tmp_path, kind):
    archive = tmp_path / 'source.zip'
    expected = dict(files={'src/example.py': hashlib.sha256(b'expected').hexdigest()})
    with zipfile.ZipFile(archive, 'w') as output:
        if kind == 'traversal':
            output.writestr('../outside.txt', b'expected')
        elif kind == 'tampering':
            output.writestr('src/example.py', b'different')
    with pytest.raises(ValueError):
        snapshot.extract_checked(archive, tmp_path / 'checkout', expected)
    assert not (tmp_path / 'outside.txt').exists()


@pytest.mark.parametrize('defect', ['stale-source', 'stale-todo', 'missing-clause', 'unimplemented', 'no-verification'])
def test_final_batch_requires_a_current_complete_requirement_review(defect):
    todo = '- [ ] Keep the real text visible.\npython tools/check_test_counts.py logs/todo-suite.xml --min-total 4350\n'
    review = dict(implementation_complete=True, source_sha256='current',
        todo_sha256=hashlib.sha256(todo.encode()).hexdigest(), requirements=[dict(line=line,
        requirement=clause, implementation='code-ready', source=['src/example.py'], verification=['tests/example.py'])
        for line, clause in reviewed_clauses(todo).items()])
    assert verify_review(review, {'sha256': 'current'}, todo)
    if defect == 'stale-source':
        review['source_sha256'] = 'old'
    elif defect == 'stale-todo':
        todo += '- [ ] A new requirement.\n'
    elif defect == 'missing-clause':
        review['requirements'].pop()
    elif defect == 'unimplemented':
        review['implementation_complete'] = False
    else:
        review['requirements'][0]['verification'] = []
    with pytest.raises(ValueError):
        verify_review(review, {'sha256': 'current'}, todo)


def test_review_includes_numbered_workflow_and_normative_outcome():
    todo = '1. Finish implementation before validation.\n2. Retain the original report.\n'
    assert reviewed_clauses(todo) == {'1': todo.splitlines()[0], '2': todo.splitlines()[1]}


def test_tk_batch_keeps_full_suite_floor_and_all_visual_profiles(tmp_path):
    jobs = jobs_for(tmp_path, 'fixture', Path('baseline'))
    assert jobs[0]['command'][1:] == ['-m', 'pytest', 'tests/', '--cov=src', '--junitxml=logs/todo-suite.xml']
    assert jobs[1]['command'][-2:] == ['--min-total', '4350']
    assert len([job for job in jobs if job.get('report')]) == 7 * 2 * 2 * 2
    assert not any('tools.qt_quick_trial.' in ' '.join(job['command']) for job in jobs)


def test_matching_empty_reports_are_not_acceptance_evidence():
    data = dict(manifest={}, metrics={}, summary={}, scenario=dict(workload={'name': 'empty'}), resize={})
    result = assess(data, json.loads(json.dumps(data)))
    assert result['status'] == 'automated-fail'
    assert result['missing_identity']
    assert result['gates']['runtime'] == 'fail'
    assert result['gates']['physical_drag'] == 'not-exercised'
    assert result['gates']['frame_present'] == 'unavailable'


@pytest.mark.parametrize('scenario,gate', [('ruby-linked', 'actual_ruby'), ('linked-only', 'actual_links'),
                                         ('quick', 'quick_live_body')])
def test_named_workload_requires_actual_annotations_links_or_live_body(scenario, gate):
    workload = {key: 0 for key in IDENTITY}
    workload.update(name=scenario)
    data = dict(manifest=dict(python='fixture', os='fixture', logical_cpus=8, tcl_patchlevel='9.0.4',
        tk_scaling=1.33, ui_scale=1.0, theme='light', ui_language='en', text_scale=1.0,
        dpi_factor=1.0, work_area=[0, 0, 1920, 1040], desktop='private'), metrics={},
        summary=dict(errors=0, gates=dict(automated_execution=dict(status='pass'))),
        scenario=dict(workload=workload), resize={})
    result = assess(data, json.loads(json.dumps(data)))
    assert result['gates']['identity'] == 'pass'
    assert result['gates'][gate] == 'fail'
    assert result['status'] == 'automated-fail'


@pytest.mark.skipif(os.name != 'nt', reason='Windows owned-process Job Object')
def test_validation_timeout_terminates_its_owned_descendant(tmp_path):
    import ctypes
    from ctypes import wintypes
    from tools.tk_resize.process import run_owned
    child_file = tmp_path / 'child-pid.txt'
    script = ('import subprocess,sys,time; from pathlib import Path; '
              'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],'
              'creationflags=subprocess.CREATE_NO_WINDOW); '
              'Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)')
    pids = []
    with (tmp_path / 'process.log').open('w') as stream:
        result = run_owned([sys.executable, '-c', script, str(child_file)], cwd=tmp_path,
            env=dict(os.environ), stream=stream, timeout=2, started=pids.append)
    assert result['timeout_reached'] and result['exit_code'] != 0
    assert result['exit_code'] == 124
    assert len(pids) == 1 and child_file.exists()
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    for pid in (pids[0], int(child_file.read_text())):
        handle = api.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                assert api.WaitForSingleObject(handle, 2000) == 0
            finally:
                api.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # The process is already gone.


@pytest.mark.parametrize('fail_after_start', [False, True])
def test_batch_records_named_job_and_terminal_state_even_on_orchestration_error(tmp_path, monkeypatch,
                                                                             fail_after_start):
    from tools.tk_resize import final_validation as final
    (tmp_path / 'docs').mkdir()
    todo = '- [ ] Fixture requirement.\n'
    (tmp_path / 'TODO.md').write_text(todo, encoding='utf-8')
    source = snapshot.manifest(tmp_path)
    audit = dict(implementation_complete=True, source_sha256=source['sha256'],
        todo_sha256=hashlib.sha256(todo.encode()).hexdigest(), requirements=[dict(line=1,
        requirement=todo.strip(), implementation='code-ready', source=['TODO.md'], verification=['fixture'])])
    audit_path = tmp_path / 'docs/todo_implementation_audit.json'
    audit_path.write_text(json.dumps(audit), encoding='utf-8')
    monkeypatch.setattr(final, 'ROOT', tmp_path)
    monkeypatch.setattr(final, 'jobs_for', lambda *_args: [dict(name='full-suite', command=['fixture'], timeout=1)])
    def owned(command, **kwargs):
        kwargs['started'](1234)
        if fail_after_start:
            raise RuntimeError('Fixture orchestration error')
        (tmp_path / 'logs/todo-suite.xml').write_text(
            '<testsuites><testsuite tests="1"><testcase name="fixture"/></testsuite></testsuites>', encoding='utf-8')
        return dict(exit_code=0, timeout_reached=False, seconds=.1)
    monkeypatch.setattr(final, 'run_owned', owned)
    arguments = ['--audit', str(audit_path), '--baseline', str(tmp_path), '--label', 'fixture', '--final-validation']
    if fail_after_start:
        with pytest.raises(RuntimeError, match='Fixture orchestration error'):
            final.main(arguments)
    else:
        assert final.main(arguments) == 0
    folder = tmp_path / 'logs/tk_resize/fixture'
    rows = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
    begin = next(row for row in rows if row['event'] == 'job.begin')
    assert begin['name'] == 'full-suite' and begin['pid'] == 1234
    batch = json.loads((folder / 'batch.json').read_text())
    summary = json.loads((folder / 'summary.json').read_text())
    assert batch['status'] == ('interrupted' if fail_after_start else 'finished')
    assert summary['automated_status'] == ('fail' if fail_after_start else 'pass')
    assert summary['acceptance'].startswith('pending')


@pytest.mark.parametrize('defect', [None, 'source-mismatch', 'missing-failure', 'uncovered-change', 'skip'])
def test_resumed_validation_requires_current_repair_of_original_failures(tmp_path, defect):
    import xml.etree.ElementTree as ET
    original, repair = tmp_path / 'original', tmp_path / 'repair'
    original.mkdir()
    repair.mkdir()
    current = dict(sha256='current', files={'src/example.py': 'new', 'tests/test_example.py': 'same'})
    (original / 'batch.json').write_text(json.dumps(dict(source=dict(sha256='old', files={
        'src/example.py': 'old', 'tests/test_example.py': 'same'}),
        jobs=[dict(name='full-suite', status='finished', exit_code=1)])), encoding='utf-8')
    suite = ET.Element('testsuite')
    for index in range(4350):
        row = ET.SubElement(suite, 'testcase', classname='tests.test_example', name='case-' + str(index))
        if index == 0:
            ET.SubElement(row, 'failure')
    ET.ElementTree(suite).write(original / 'todo-suite-original.xml', encoding='utf-8')
    run = dict(status='finished', exit_code=0, source=current, scope=['tests/test_example.py'],
               covered_source={'src/example.py': ['tests/test_example.py']})
    if defect == 'source-mismatch':
        run = dict(run, source=dict(sha256='different', files=current['files']))
    elif defect == 'uncovered-change':
        run['covered_source'] = {}
    (repair / 'run.json').write_text(json.dumps(run), encoding='utf-8')
    suite = ET.Element('testsuite')
    row = ET.SubElement(suite, 'testcase', classname='tests.test_example',
                        name='case-1' if defect == 'missing-failure' else 'case-0')
    if defect == 'skip':
        ET.SubElement(row, 'skipped')
    ET.ElementTree(suite).write(repair / 'results.xml', encoding='utf-8')
    if defect:
        with pytest.raises(ValueError):
            resume_evidence(original / 'batch.json', repair / 'run.json', current)
    else:
        evidence = resume_evidence(original / 'batch.json', repair / 'run.json', current)
        assert evidence['original_cases'] == 4350 and evidence['original_failed_cases'] == 1
        assert evidence['changed_scope'] == ['src/example.py']
        assert 'not a new clean full-suite pass' in evidence['interpretation']
        before = (original / 'todo-suite-original.xml').read_bytes()
        output = tmp_path / 'reconciled.xml'
        result = write_reconciled_report(evidence, output)
        from tools.check_test_counts import read_results
        assert read_results(str(output))[:4] == (4350, 0, 0, 0)
        assert result['tests'] == 4350
        assert (original / 'todo-suite-original.xml').read_bytes() == before
        provenance = json.loads(output.with_suffix('.provenance.json').read_text())
        repaired_case = next(row for row in provenance['cases'] if row['name'] == 'case-0')
        assert repaired_case['report'] == str(repair / 'results.xml')


@pytest.mark.parametrize('change_app_again', [False, True])
def test_repair_chain_carries_ui_proof_only_across_covered_source_changes(tmp_path, change_app_again):
    import xml.etree.ElementTree as ET
    folders = [tmp_path / name for name in ('original', 'ui-repair', 'tool-repair')]
    for folder in folders:
        folder.mkdir()
    original, ui, tool = folders
    initial = dict(sha256='initial', files={'src/example.py': 'old', 'tools/example.py': 'old'})
    ui_source = dict(sha256='ui', files={'src/example.py': 'fixed', 'tools/example.py': 'old'})
    current = dict(sha256='current', files={'src/example.py': 'changed-again' if change_app_again else 'fixed',
                                           'tools/example.py': 'fixed'})
    (original / 'batch.json').write_text(json.dumps(dict(source=initial,
        jobs=[dict(name='full-suite', status='finished', exit_code=1)])), encoding='utf-8')
    suite = ET.Element('testsuite')
    for index in range(4350):
        case = ET.SubElement(suite, 'testcase', classname='tests.test_ui', name='case-' + str(index))
        if index == 0:
            ET.SubElement(case, 'failure')
    ET.ElementTree(suite).write(original / 'todo-suite-original.xml', encoding='utf-8')
    for folder, source, module, implementation, name in (
            (ui, ui_source, 'test_ui', 'src/example.py', 'case-0'),
            (tool, current, 'test_tool', 'tools/example.py', 'tool-case')):
        run = dict(status='finished', exit_code=0, source=source, scope=['tests/' + module + '.py'],
                   covered_source={implementation: ['tests/' + module + '.py']})
        (folder / 'run.json').write_text(json.dumps(run), encoding='utf-8')
        suite = ET.Element('testsuite')
        ET.SubElement(suite, 'testcase', classname='tests.' + module, name=name)
        ET.ElementTree(suite).write(folder / 'results.xml', encoding='utf-8')
    if change_app_again:
        with pytest.raises(ValueError, match='coverage'):
            resume_evidence(original / 'batch.json', [ui / 'run.json', tool / 'run.json'], current)
    else:
        proof = resume_evidence(original / 'batch.json', [ui / 'run.json', tool / 'run.json'], current)
        assert len(proof['rechecks']) == 2 and proof['reconciled_cases'] == 4351
        result = write_reconciled_report(proof, tmp_path / 'reconciled.xml')
        assert result['tests'] == 4351


@pytest.mark.parametrize('defect', [None, 'new-reason', 'changed-test', 'previously-passed',
                                  'missing-source-hash', 'only-skipped-coverage'])
def test_resume_preserves_only_identical_original_skips_without_counting_them_as_coverage(tmp_path, defect):
    import xml.etree.ElementTree as ET
    original, repair = tmp_path / 'original', tmp_path / 'repair'
    original.mkdir()
    repair.mkdir()
    original_files = {'src/example.py': 'old', 'tests/test_example.py': 'same'}
    if defect == 'missing-source-hash':
        original_files.pop('tests/test_example.py')
    current = dict(sha256='current', files={'src/example.py': 'new',
        'tests/test_example.py': 'changed' if defect == 'changed-test' else 'same'})
    (original / 'batch.json').write_text(json.dumps(dict(source=dict(sha256='old', files=original_files),
        jobs=[dict(name='full-suite', status='finished', exit_code=1)])), encoding='utf-8')
    suite = ET.Element('testsuite')
    for index in range(4350):
        case = ET.SubElement(suite, 'testcase', classname='tests.test_example', name='case-' + str(index))
        if index == 0 and defect != 'only-skipped-coverage':
            ET.SubElement(case, 'failure')
        if (index == 1 and defect != 'previously-passed') or (index == 0 and defect == 'only-skipped-coverage'):
            ET.SubElement(case, 'skipped', type='pytest.skip', message='Expected clipboard guard')
    ET.ElementTree(suite).write(original / 'todo-suite-original.xml', encoding='utf-8')
    run = dict(status='finished', exit_code=0, source=current, scope=['tests/test_example.py'],
               covered_source={'src/example.py': ['tests/test_example.py']})
    (repair / 'run.json').write_text(json.dumps(run), encoding='utf-8')
    suite = ET.Element('testsuite')
    if defect != 'only-skipped-coverage':
        ET.SubElement(suite, 'testcase', classname='tests.test_example', name='case-0')
    case = ET.SubElement(suite, 'testcase', classname='tests.test_example',
                        name='case-0' if defect == 'only-skipped-coverage' else 'case-1')
    ET.SubElement(case, 'skipped', type='pytest.skip',
                  message='Different reason' if defect == 'new-reason' else 'Expected clipboard guard')
    ET.ElementTree(suite).write(repair / 'results.xml', encoding='utf-8')
    if defect:
        with pytest.raises(ValueError):
            resume_evidence(original / 'batch.json', repair / 'run.json', current)
    else:
        evidence = resume_evidence(original / 'batch.json', repair / 'run.json', current)
        assert evidence['rechecks'][0]['preserved_original_skips'] == [dict(
            classname='tests.test_example', name='case-1', reason='Expected clipboard guard')]
        report = tmp_path / 'reconciled.xml'
        write_reconciled_report(evidence, report)
        from tools.check_test_counts import read_results
        assert read_results(str(report))[:4] == (4350, 0, 0, 1)

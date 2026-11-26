"""One audited Tk acceptance batch: full suite, count floor, Tk pairs and galleries."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import xml.etree.ElementTree as ET

from tools.tk_resize.snapshot import archive_source, manifest
from tools.tk_resize.process import run_owned


ROOT = Path(__file__).resolve().parents[2]
SURFACES = ('translator,settings,home_quick_tools,history,home_dictionary,'
            'followup_responsive,followup_regressions,quick_popup')
THEMES = ('system', 'pink', 'blue', 'yellow', 'green', 'light', 'dark')


def reviewed_clauses(todo):
    return {str(index + 1): line for index, line in enumerate(todo.splitlines())
            if line.startswith('- ') or line.startswith('| ') and not re.match(r'^\|[ |:\-]+\|$', line)
            or re.match(r'^\d+\.\s+\S', line)
            or line.startswith(('**V\u00ed d\u1ee5', '**Nghi\u1ec7m thu', '**K\u1ebft qu\u1ea3 b\u01b0\u1edbc n\u00e0y:',
                               '**K\u1ebft qu\u1ea3 c\u1ea7n \u0111\u1ea1t:',
                               '**Y\u00eau c\u1ea7u ng\u01b0\u1eddi d\u00f9ng:',
                               '**Ch\u01b0a thu\u1ed9c vi\u1ec7c tri\u1ec3n khai/nghi\u1ec7m thu',
                               'python -m pytest ', 'python tools/check_test_counts.py '))}


def verify_review(review, source, todo):
    if review.get('implementation_complete') is not True or review.get('source_sha256') != source['sha256']:
        raise ValueError('Implementation review is incomplete or belongs to another source')
    if review.get('todo_sha256') != hashlib.sha256(todo.encode()).hexdigest():
        raise ValueError('TODO changed after the implementation review')
    records = {str(row.get('line')): row for row in review.get('requirements', [])}
    for line, clause in reviewed_clauses(todo).items():
        row = records.get(line, {})
        if (row.get('requirement') != clause or row.get('implementation') not in ('code-ready', 'verification-only')
                or not row.get('source') or not row.get('verification')):
            raise ValueError('Unreviewed requirement at TODO line ' + line)
    return True


def jobs_for(folder, label, baseline):
    jobs = [dict(name='full-suite', command=[sys.executable, '-m', 'pytest', 'tests/', '--cov=src',
                 '--junitxml=logs/todo-suite.xml'], timeout=2400),
            dict(name='count-floor', command=[sys.executable, 'tools/check_test_counts.py',
                 'logs/todo-suite.xml', '--min-total', '4350'], timeout=60),
            dict(name='tk-pairs', command=[sys.executable, '-m', 'tools.tk_resize.comparison',
                 '--baseline', str(baseline), '--output', str(folder / 'pairs'), '--final-validation'], timeout=7200)]
    for theme in THEMES:
        for language in ('en', 'ja'):
            for text in (1.0, 1.5):
                for dpi in (100, 150):
                    name = f'{theme}-{language}-t{int(text * 100)}-d{dpi}'
                    gallery_label = label + '-' + name
                    jobs.append(dict(name='tk-' + name, command=[sys.executable, 'tools/ui_gallery.py',
                        '--label', gallery_label, '--child', str(dpi), '--surfaces',
                        SURFACES + (',theme_switching' if theme == 'pink' else ''),
                        '--theme', theme, '--ui-language', language, '--text-scale', str(text)], timeout=300,
                        report=str(ROOT / 'logs/ui_gallery' / gallery_label / f's{dpi}.json')))
    return jobs


def resume_evidence(previous_path, recheck_path, current):
    """Validate a source-linked repair chain without repeating the original full run."""
    previous_path = Path(previous_path)
    paths = [Path(path) for path in recheck_path] if isinstance(recheck_path, (list, tuple)) else [Path(recheck_path)]
    if not paths:
        raise ValueError('A repair chain cannot be empty')
    previous = json.loads(previous_path.read_text(encoding='utf-8-sig'))
    original_xml = previous_path.with_name('todo-suite-original.xml')
    original = list(ET.parse(original_xml).iter('testcase'))
    if len(original) < 4350:
        raise ValueError('The retained original suite does not satisfy the unchanged count floor')
    suite = next((row for row in previous.get('jobs', []) if row['name'] == 'full-suite'), {})
    if suite.get('status') != 'finished' or suite.get('exit_code') not in (0, 1):
        raise ValueError('The original suite did not finish with ordinary test results')
    def identity(row):
        return row.get('classname', ''), row.get('name', '')
    cases = {identity(row): row for row in original}
    if len(cases) != len(original):
        raise ValueError('Duplicate original JUnit identities require explicit reconciliation')
    failed = {key for key, row in cases.items() if any(row.find(tag) is not None for tag in ('failure', 'error'))}
    original_skips = {key: row.find('skipped') for key, row in cases.items() if row.find('skipped') is not None}
    original_files = previous.get('source', {}).get('files', {})
    prior_source = previous.get('source', {})
    proofs, all_changed = [], set()
    for path in paths:
        recheck = json.loads(path.read_text(encoding='utf-8-sig'))
        source = recheck.get('source', {})
        if recheck.get('status') != 'finished' or recheck.get('exit_code') != 0 or not source.get('files'):
            raise ValueError('A repair recheck is incomplete, failed or lacks source evidence')
        repair_xml = path.with_name('results.xml')
        repaired = list(ET.parse(repair_xml).iter('testcase'))
        if not repaired or any(row.find(tag) is not None for row in repaired for tag in ('failure', 'error')):
            raise ValueError('Repair cases must have actually run without failures')
        preserved_skips = []
        for row in repaired:
            skip = row.find('skipped')
            if skip is None:
                continue
            key = identity(row)
            old = original_skips.get(key)
            module = '/'.join(row.get('classname', '').split('.')[:2]) + '.py'
            prior = cases.get(key)
            if (old is None or prior is None or prior.find('skipped') is None
                    or (skip.get('type'), skip.get('message')) != (old.get('type'), old.get('message'))
                    or not original_files.get(module)
                    or original_files[module] != source['files'].get(module)):
                raise ValueError('New, changed or previously passing repair cases cannot be skipped')
            preserved_skips.append(dict(classname=key[0], name=key[1], reason=skip.get('message')))
        # Expected original build/clipboard guards stay explicit. A skipped
        # case never counts as executed coverage for a changed module.
        executed = {'/'.join(row.get('classname', '').split('.')[:2]) + '.py'
                    for row in repaired if row.find('skipped') is None}
        scope, covered = set(recheck.get('scope', [])), recheck.get('covered_source', {})
        before, after = prior_source.get('files', {}), source['files']
        complete_modules = sorted(scope & executed) if recheck.get('complete_module_scope') is True else []
        repaired_keys = {identity(row) for row in repaired}
        retired = []
        for key, row in list(cases.items()):
            module = '/'.join(key[0].split('.')[:2]) + '.py'
            if module not in complete_modules:
                continue
            if key not in repaired_keys:
                if key in failed or row.find('skipped') is not None:
                    raise ValueError('A failed or guarded original case was removed from a complete recheck')
                if before.get(module) == after.get(module):
                    raise ValueError('A complete module recheck omitted an unchanged testcase')
                retired.append(dict(classname=key[0], name=key[1], module=module))
            del cases[key]
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name)
                         and not name.startswith('docs/') and not name.endswith('.md'))
        for name in changed:
            if name.startswith('tests/') and name.endswith('.py'):
                if name not in scope or name not in executed:
                    raise ValueError('A changed test module did not run: ' + name)
            elif not covered.get(name) or not set(covered[name]) <= scope & executed:
                raise ValueError('Changed implementation lacks executed repair coverage: ' + name)
        cases.update((identity(row), row) for row in repaired)
        all_changed.update(changed)
        proofs.append(dict(run=str(path), junit=str(repair_xml), cases=len(repaired),
                           source_sha256=source.get('sha256'), changed_scope=changed,
                           complete_modules=complete_modules, retired_cases=retired,
                           preserved_original_skips=preserved_skips))
        prior_source = source
    documentation_followup = None
    if prior_source != current:
        before, after = prior_source.get('files', {}), current.get('files', {})
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
        if not changed or any(not name.endswith('.md') for name in changed):
            raise ValueError('The last repair evidence belongs to another current source')
        documentation_followup = dict(tested_source_sha256=prior_source['sha256'],
            reviewed_source_sha256=current['sha256'], changed_markdown=changed,
            interpretation='Only Markdown changed; actual test-source identities remain unchanged')
    if any(row.find(tag) is not None for row in cases.values() for tag in ('failure', 'error')):
        raise ValueError('At least one original failure was not repaired in the recheck')
    return dict(original_batch=str(previous_path), original_junit=str(original_xml),
                original_source_sha256=previous['source']['sha256'], original_cases=len(original),
                original_failed_cases=len(failed), rechecks=proofs,
                recheck=str(paths[-1]), recheck_junit=proofs[-1]['junit'],
                recheck_cases=proofs[-1]['cases'], recheck_source_sha256=prior_source['sha256'],
                current_review_source_sha256=current['sha256'], documentation_followup=documentation_followup,
                changed_scope=sorted(all_changed), reconciled_cases=len(cases),
                interpretation='Retained original full suite plus scoped repair; not a new clean full-suite pass')


def write_reconciled_report(evidence, destination):
    """Copy actual testcase results with per-case provenance; preserve all originals."""
    from copy import deepcopy
    destination = Path(destination)
    reports = [(evidence['original_junit'], evidence['original_source_sha256'], [])]
    reports += [(row['junit'], row['source_sha256'], row.get('complete_modules', [])) for row in evidence['rechecks']]
    cases, origins = {}, {}
    for report, source, complete_modules in reports:
        for key in list(cases):
            if '/'.join(key[0].split('.')[:2]) + '.py' in complete_modules:
                del cases[key]
                del origins[key]
        for row in ET.parse(report).iter('testcase'):
            key = row.get('classname', ''), row.get('name', '')
            cases[key], origins[key] = deepcopy(row), dict(report=report, source_sha256=source)
    if any(row.find(tag) is not None for row in cases.values() for tag in ('failure', 'error')):
        raise ValueError('Unrepaired failures cannot be removed from a derived report')
    skipped = sum(row.find('skipped') is not None for row in cases.values())
    suite = ET.Element('testsuite', name='reconciled-original-plus-scoped-rechecks',
                       tests=str(len(cases)), failures='0', errors='0', skipped=str(skipped))
    properties = ET.SubElement(suite, 'properties')
    ET.SubElement(properties, 'property', name='report_kind', value='derived-evidence-not-a-single-test-run')
    ET.SubElement(properties, 'property', name='interpretation', value=evidence['interpretation'])
    for row in cases.values():
        suite.append(row)
    ET.ElementTree(suite).write(destination, encoding='utf-8', xml_declaration=True)
    provenance = dict(interpretation=evidence['interpretation'], tests=len(cases), skipped=skipped,
        originals_preserved=True, cases=[dict(classname=key[0], name=key[1], **origins[key]) for key in cases])
    destination.with_suffix('.provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    return dict(path=str(destination), tests=len(cases), skipped=skipped,
                interpretation=evidence['interpretation'], provenance=str(destination.with_suffix('.provenance.json')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, default=Path('docs/todo_implementation_audit.json'))
    parser.add_argument('--baseline', type=Path, default=Path('logs/tk_resize/baseline-20261029'))
    parser.add_argument('--label', required=True)
    parser.add_argument('--final-validation', action='store_true', required=True)
    parser.add_argument('--resume-batch', type=Path)
    parser.add_argument('--recheck', type=Path, action='append')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.label):
        parser.error('Use a simple label')
    source = manifest(ROOT)
    review = json.loads(args.audit.read_text(encoding='utf-8-sig'))
    try:
        verify_review(review, source, (ROOT / 'TODO.md').read_text(encoding='utf-8-sig'))
    except ValueError as error:
        parser.error(str(error))
    if bool(args.resume_batch) != bool(args.recheck):
        parser.error('--resume-batch and --recheck are required together')
    suite_evidence = None
    if args.resume_batch:
        try:
            suite_evidence = resume_evidence(args.resume_batch, args.recheck, source)
        except (ValueError, OSError, KeyError, ET.ParseError) as error:
            parser.error(str(error))
    folder = ROOT / 'logs/tk_resize' / args.label
    folder.mkdir(parents=True, exist_ok=False)
    archive_source(ROOT, folder / 'source.zip')
    if suite_evidence:
        suite_evidence['reconciled_report'] = write_reconciled_report(suite_evidence, folder / 'todo-suite-reconciled.xml')
    (folder / 'implementation-review.json').write_text(json.dumps(review, indent=2) + '\n', encoding='utf-8')
    xml = ROOT / 'logs/todo-suite.xml'
    if xml.exists() and suite_evidence is None:
        xml.replace(folder / 'previous-todo-suite.xml')
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    environment.pop('CROSSTRANS_TESTS_ON_SCREEN', None)
    environment.pop('CROSSTRANS_TESTS_REAL_CLIPBOARD', None)
    environment['CROSSTRANS_RESIZE_REPORT'] = str(folder / 'callback-probe.json')
    batch = dict(schema=1, status='running', owner_pid=os.getpid(), source=source,
                 started_utc=datetime.now(timezone.utc).isoformat(), jobs=[], acceptance='pending',
                 suite_evidence=suite_evidence)
    (folder / 'manifest.json').write_text(json.dumps(dict(schema=1, source=source,
        started_utc=batch['started_utc'], python=sys.version, executable=sys.executable,
        os=platform.platform(), logical_cpus=os.cpu_count(),
        baseline=str(args.baseline.resolve()), runtime_detail='Actual Tcl/scaling/workloads are in per-worker manifests',
        units=dict(time='milliseconds', memory='bytes', cpu='percent of machine'),
        measurement_limits=['Geometry/callback notifications are not compositor frames',
                            'Visible/manual, PNG and resource review are separate acceptance gates']), indent=2)
        + '\n', encoding='utf-8')
    aggregate = dict(status='pending', pairs=[], detail='Measurements are retained in the paired-worker reports')
    def save():
        (folder / 'batch.json').write_text(json.dumps(batch, indent=2) + '\n', encoding='utf-8')
        (folder / 'metrics.json').write_text(json.dumps(aggregate, indent=2) + '\n', encoding='utf-8')
        completed = [job for job in batch['jobs'] if job.get('status') == 'finished']
        (folder / 'summary.json').write_text(json.dumps(dict(status=batch['status'],
            automated_status='fail' if batch.get('automated_failure') else
                             'pass' if batch['status'] == 'finished' else 'pending',
            completed_jobs=len(completed), acceptance=batch['acceptance'],
            source_sha256=source['sha256']), indent=2) + '\n', encoding='utf-8')
    def event(kind, **fields):
        with (folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=kind, utc=datetime.now(timezone.utc).isoformat(),
                                         **fields)) + '\n')
    save()
    event('batch.begin', owner_pid=os.getpid(), source_sha256=source['sha256'])
    try:
        jobs = jobs_for(folder, args.label, args.baseline.resolve())
        if suite_evidence:
            jobs = [dict(name='reconciled-count-floor', command=[sys.executable, 'tools/check_test_counts.py',
                         suite_evidence['reconciled_report']['path'], '--min-total', '4350'], timeout=60)] + jobs[2:]
        for job in jobs:
            if manifest(ROOT) != source:
                raise RuntimeError('Source changed while the batch was active')
            job.update(status='running')
            batch['jobs'].append(job)
            with (folder / (job['name'] + '.log')).open('w', encoding='utf-8') as stream:
                def launched(pid):
                    job['pid'] = pid
                    save()
                    event('job.begin', name=job['name'], pid=pid)
                job.update(run_owned(job['command'], cwd=ROOT, env=environment, stream=stream,
                                     timeout=job['timeout'], started=launched))
            job.update(status='finished')
            event('job.end', name=job['name'], pid=job['pid'], exit_code=job['exit_code'],
                  seconds=job['seconds'], timeout_reached=job['timeout_reached'])
            if job['name'] == 'full-suite' and xml.exists():
                import shutil
                shutil.copy2(xml, folder / 'todo-suite-original.xml')
            elif job['name'] == 'full-suite' and job['exit_code'] == 0:
                job.update(exit_code=1, report_missing=True)
                event('job.report-missing', name=job['name'], artifact='logs/todo-suite.xml')
            if job.get('report'):
                path = Path(job['report'])
                if path.exists():
                    report = json.loads(path.read_text(encoding='utf-8'))
                    job.update(findings=len(report.get('findings', [])), errors=len(report.get('errors', [])),
                               blocked=len(report.get('blocked', [])),
                               windows_on_user_desktop=report.get('windows_on_user_desktop', 0))
                else:
                    job['report_missing'] = True
            if job['name'] == 'tk-pairs':
                pair_report = folder / 'pairs/comparison.json'
                if pair_report.exists():
                    comparison = json.loads(pair_report.read_text(encoding='utf-8'))
                    aggregate.update(status=comparison.get('status', 'incomplete'),
                        pairs=[{key: row.get(key) for key in ('scenario', 'profile', 'status', 'gates', 'peak_memory_bytes',
                            'peak_memory_delta_bytes', 'hidden_idle_cpu', 'cpu_by_phase', 'startup', 'lifecycle')}
                            for row in comparison.get('pairs', [])])
            save()
            print(json.dumps({key: job[key] for key in ('name', 'exit_code', 'seconds')}), flush=True)
            if job['name'] in ('full-suite', 'count-floor', 'reconciled-count-floor') and job['exit_code']:
                batch['status'] = 'stopped-on-suite-failure'
                batch['automated_failure'] = True
                save()
                event('batch.stopped-on-suite-failure', job=job['name'])
                return 1
        if manifest(ROOT) != source:
            raise RuntimeError('Source changed before the final batch seal')
        batch['status'] = 'finished'
        batch['acceptance'] = 'pending-physical-drag-resource-and-PNG-review'
        batch['automated_failure'] = any(job['exit_code'] or job.get('findings') or job.get('errors')
            or job.get('blocked') or job.get('windows_on_user_desktop') or job.get('report_missing') for job in batch['jobs'])
        save()
        event('batch.end', automated_failure=batch['automated_failure'])
        return int(batch['automated_failure'])
    except BaseException as error:
        import traceback
        batch.update(status='interrupted', automated_failure=True, error_type=type(error).__name__)
        if batch['jobs'] and batch['jobs'][-1].get('status') == 'running':
            batch['jobs'][-1]['status'] = 'interrupted'
        save()
        event('batch.error', error_type=type(error).__name__,
              traceback=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                         for frame in traceback.extract_tb(error.__traceback__)])
        raise


if __name__ == '__main__':
    raise SystemExit(main())

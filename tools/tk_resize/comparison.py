"""Serial Tk/Tk comparison of retained source versions, with no Qt renderer launch."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from tools.tk_resize.snapshot import manifest, archive_source, extract_checked
from tools.tk_resize.process import run_owned


ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ('resident-hidden', 'empty', 'short', 'short-result-japanese', 'ruby-linked',
             'ruby-only', 'linked-only', 'plain-long', 'annotation-cutoff', 'quick',
             'quick-long', 'quick-ruby', 'quick-expanded', 'two-windows', 'history-200',
             'dictionary-20', 'text-attachment', 'lifecycle-50')
LARGE_SCENARIOS = ('ruby-linked', 'quick-long', 'quick-ruby', 'two-windows', 'history-200', 'dictionary-20')
IDENTITY = ('actual_source_sha256', 'actual_result_sha256', 'actual_dictionary_context_sha256',
            'actual_dictionary_words_sha256', 'actual_dictionary_phrases_sha256',
            'actual_dictionary_rows', 'history_count', 'source_ruby_segments', 'result_ruby_segments',
            'actual_link_groups', 'actual_ui_link_groups', 'nlp_basic_mode_languages',
            'quick_source_sha256', 'quick_result_sha256', 'quick_source_ruby_segments',
            'quick_result_ruby_segments', 'quick_link_groups', 'main_size', 'quick_size')


def load_worker(folder):
    return {name: json.loads((Path(folder) / (name + '.json')).read_text(encoding='utf-8'))
            for name in ('manifest', 'metrics', 'summary', 'scenario', 'resize')}


MIB = 1024 * 1024


def numeric_peak(rows, field):
    values = [row.get(field) for row in rows if isinstance(row.get(field), (int, float))]
    return max(values) if values else None


def weighted_cpu(rows):
    rows = [row for row in rows if isinstance(row.get('cpu_percent_machine'), (int, float))
            and row.get('sample_interval_seconds', 0) > 0 and row.get('sample_fully_within_scenario', True)]
    duration = sum(row['sample_interval_seconds'] for row in rows)
    return dict(seconds=duration, percent=sum(row['cpu_percent_machine'] * row['sample_interval_seconds']
                                             for row in rows) / duration if duration else None)


def retention(rows):
    unique = {row.get('elapsed_ms'): row for row in rows if row.get('elapsed_ms') is not None}
    samples = list(unique.values())
    if len(samples) < 8:
        return dict(status='unavailable', reason='Not enough distinct process samples')
    settled = samples[max(1, len(samples) // 3):]
    span = max(1, len(settled) // 4)
    changes = {}
    for key in ('working_set_bytes', 'private_bytes', 'user_handles', 'gdi_handles'):
        first = [row[key] for row in settled[:span] if isinstance(row.get(key), (int, float))]
        last = [row[key] for row in settled[-span:] if isinstance(row.get(key), (int, float))]
        changes[key] = sum(last) / len(last) - sum(first) / len(first) if first and last else None
    within = all(changes[key] is not None and changes[key] <= 5 for key in ('user_handles', 'gdi_handles'))
    within = within and all(changes[key] is not None and changes[key] <= 8 * MIB for key in ('working_set_bytes', 'private_bytes'))
    return dict(status='pass' if within else 'review', distinct_samples=len(samples), changes=changes,
                method='Compare last quarter with first quarter after one-third warmup; bounded-run evidence only')


def assess(before, after):
    workloads = [data['scenario']['workload'] for data in (before, after)]
    mismatches = {key: [workload.get(key) for workload in workloads]
                  for key in IDENTITY if workloads[0].get(key) != workloads[1].get(key)}
    missing_identity = [key for key in IDENTITY if any(key not in workload for workload in workloads)]
    runtime_keys = ('python', 'os', 'logical_cpus', 'tcl_patchlevel', 'tk_scaling', 'ui_scale',
                    'theme', 'ui_language', 'text_scale', 'dpi_factor', 'work_area')
    runtime_mismatches = [key for key in runtime_keys
                          if before['manifest'].get(key) != after['manifest'].get(key)
                          or any(data['manifest'].get(key) is None for data in (before, after))]
    failures = []
    memory = {}
    idle = {}
    cycles = {}
    cpu_by_phase = {}
    startup = {}
    for name, data in (('before', before), ('after', after)):
        if (data['summary'].get('errors') or data['scenario'].get('findings')
                or data['scenario'].get('callback_errors') or data['scenario'].get('blocked_effects')
                or data['summary'].get('gates', {}).get('automated_execution', {}).get('status') != 'pass'):
            failures.append(name + '-worker-errors')
        rows = data['metrics'].get('resource_samples', [])
        memory[name] = {field: numeric_peak(rows, field) for field in ('working_set_bytes', 'private_bytes',
                         'lifetime_peak_working_set_bytes', 'lifetime_peak_commit_bytes')}
        idle[name] = weighted_cpu([row for row in rows if row.get('scenario') == 'resident-hidden-idle'])
        cpu_by_phase[name] = {phase: weighted_cpu([row for row in rows
                              if row.get('scenario', '').endswith('-' + phase)])
                              for phase in ('steady', 'resize', 'settle', 'capture')}
        startup[name] = data['manifest'].get('startup')
        cycles[name] = retention(data['scenario'].get('cycles', []))
    delta = {field: memory['after'][field] - memory['before'][field]
             if memory['after'][field] is not None and memory['before'][field] is not None else None
             for field in ('working_set_bytes', 'private_bytes', 'lifetime_peak_working_set_bytes',
                            'lifetime_peak_commit_bytes')}
    scenario = workloads[0]['name']
    gates = dict(identity='pass' if not mismatches and not missing_identity else 'fail',
                 runtime='pass' if not runtime_mismatches else 'fail',
                 automated_execution='pass' if not failures else 'fail',
                 private_desktop='pass' if all(data['manifest'].get('desktop') == 'private'
                                               for data in (before, after)) else 'fail',
                 physical_drag='not-exercised', frame_present='unavailable',
                 pointer_to_photon='unavailable', visual_review='pending', memory_tradeoff='review')
    if scenario == 'resident-hidden':
        value = idle['after']
        gates['hidden_idle_cpu'] = ('unavailable' if value['seconds'] < 30 or value['percent'] is None
                                    else 'pass' if value['percent'] < 1 else 'fail')
    if scenario == 'lifecycle-50':
        gates['lifecycle'] = cycles['after']['status']
    if scenario not in ('resident-hidden', 'lifecycle-50'):
        gates['layout_settlement'] = ('pass' if all(
            data['resize'].get('settlement', {}).get('status') == 'settled' for data in (before, after)) else 'fail')
    if scenario in ('quick', 'quick-long', 'quick-ruby', 'quick-expanded', 'two-windows'):
        starts = [data['resize'].get('quick_constraints') for data in (before, after)]
        gates['gesture_start_identity'] = ('pass' if all(row and row.get('start_rect') for row in starts)
            and starts[0]['start_rect'] == starts[1]['start_rect'] else 'fail')
        if after['resize'].get('interaction') == 'outline-on-release':
            checks=after['resize'].get('outline_checks',[])
            gates['quick_outline_resize'] = ('pass' if checks and all(row['passed'] for row in checks)
                and after['resize'].get('quick_distinct_window_sizes')==1
                and after['resize'].get('quick_distinct_body_sizes')==1 else 'fail')
        else:
            gates['quick_live_body'] = ('pass' if after['resize'].get('quick_distinct_window_sizes', 0) >= 3
            and after['resize'].get('quick_distinct_body_sizes', 0) >= 3
            and after['resize'].get('quick_body_observations')
            and not any(row.get('outline') for row in after['resize']['quick_body_observations']) else 'fail')
    if scenario in ('ruby-linked', 'ruby-only', 'two-windows', 'quick-ruby', 'quick-expanded'):
        field = 'quick_source_ruby_segments' if scenario.startswith('quick-') else 'source_ruby_segments'
        gates['actual_ruby'] = 'pass' if all(row.get(field, 0) > 0 for row in workloads) else 'fail'
    if scenario in ('ruby-linked', 'linked-only', 'two-windows', 'quick-ruby', 'quick-expanded'):
        field = 'quick_link_groups' if scenario.startswith('quick-') else 'actual_ui_link_groups'
        gates['actual_links'] = 'pass' if all(row.get(field, 0) > 0 for row in workloads) else 'fail'
    return dict(status='automated-fail' if 'fail' in gates.values() else 'automated-evidence-only',
                gates=gates, identity_mismatches=mismatches, missing_identity=missing_identity,
                runtime_mismatches=runtime_mismatches, failures=failures,
                peak_memory_bytes=memory, peak_memory_delta_bytes=delta, hidden_idle_cpu=idle,
                cpu_by_phase=cpu_by_phase, startup=startup, lifecycle=cycles,
                before_resize=before['resize'], after_resize=after['resize'])


def baseline_checkout(folder, output):
    folder, output = Path(folder), Path(output)
    expected = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    checkout = extract_checked(folder / 'source.zip', output, expected)
    supplement_path = folder / 'asset-supplement.json'
    if supplement_path.exists():
        supplement = json.loads(supplement_path.read_text(encoding='utf-8'))
        import hashlib
        import zipfile
        with zipfile.ZipFile(folder / 'asset-supplement.zip') as archive:
            for name, digest in supplement['files'].items():
                target = (checkout / name).resolve()
                if not target.is_relative_to(checkout) or target.exists():
                    raise ValueError('Asset supplement conflicts with the retained source')
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('Asset supplement digest mismatch')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    return checkout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--final-validation', action='store_true', required=True)
    parser.add_argument('--scenarios', default=','.join(SCENARIOS))
    args = parser.parse_args(argv)
    scenarios = args.scenarios.split(',')
    if not scenarios or any(name not in SCENARIOS for name in scenarios):
        parser.error('Unknown or empty scenario list')
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    source = archive_source(ROOT, folder / 'candidate-source.zip')
    checkout = baseline_checkout(args.baseline.resolve(), folder / 'baseline-checkout')
    candidate = extract_checked(folder / 'candidate-source.zip', folder / 'candidate-checkout', source)
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    environment.pop('CROSSTRANS_TESTS_ON_SCREEN', None)
    environment.pop('CROSSTRANS_TESTS_REAL_CLIPBOARD', None)
    batch = dict(schema=1, started_utc=datetime.now(timezone.utc).isoformat(), status='running',
                 owner_pid=os.getpid(), source=source, baseline=manifest(checkout), jobs=[], pairs=[])
    def save():
        (folder / 'comparison.json').write_text(json.dumps(batch, indent=2) + '\n', encoding='utf-8')
    save()
    tasks = [('standard', scenario) for scenario in scenarios]
    tasks.extend(('large-ja', scenario) for scenario in scenarios if scenario in LARGE_SCENARIOS)
    for profile, scenario in tasks:
        for version, version_root in (('before', checkout), ('after', candidate)):
            if manifest(ROOT) != source:
                raise RuntimeError('Source changed during validation; preserved results are source-bound')
            identifier = scenario + '-' + profile
            output = folder / (identifier + '-' + version)
            command = [sys.executable, str(ROOT / 'tools/tk_resize/worker.py'), '--source', str(version_root),
                       '--output', str(output), '--scenario', scenario, '--final-validation']
            if profile == 'large-ja':
                command += ['--ui-language', 'ja', '--text-scale', '1.5', '--dpi-factor', '1.5']
            job = dict(scenario=scenario, profile=profile, version=version, command=command, status='running')
            batch['jobs'].append(job)
            with (folder / (identifier + '-' + version + '.log')).open('w', encoding='utf-8') as stream:
                def launched(pid):
                    job['pid'] = pid
                    save()
                job.update(run_owned(command, cwd=version_root, env=environment,
                                     stream=stream, timeout=240, started=launched))
            job.update(status='finished')
            save()
            print(json.dumps(dict(scenario=scenario, profile=profile, version=version,
                                  exit_code=job['exit_code'])), flush=True)
        try:
            result = assess(load_worker(folder / (identifier + '-before')),
                            load_worker(folder / (identifier + '-after')))
        except (OSError, KeyError, ValueError) as error:
            result = dict(status='incomplete', error_type=type(error).__name__)
        batch['pairs'].append(dict(scenario=scenario, profile=profile, **result))
        save()
    if manifest(ROOT) != source:
        raise RuntimeError('Source changed before the final evidence seal')
    batch['status'] = 'finished'
    batch['automated_failure'] = (any(job['exit_code'] != 0 for job in batch['jobs']) or
                                  any(row['status'] in ('incomplete', 'automated-fail') for row in batch['pairs']))
    batch['acceptance'] = 'pending-physical-drag-resource-and-visual-review'
    save()
    return int(batch['automated_failure'])


if __name__ == '__main__':
    raise SystemExit(main())

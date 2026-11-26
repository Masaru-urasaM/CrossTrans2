"""Run one consolidated suite, then source-bound outline evidence serially."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from tools.tk_resize.snapshot import archive_source, extract_checked, manifest
from tools.tk_resize.owned import run_owned
from tools.tk_deferred_resize.validation import validation_plan

ROOT = Path(__file__).resolve().parents[2]


def run(output):
    directory = Path(output).resolve()
    if not directory.is_relative_to(ROOT/'logs/tk_outline_resize'):
        raise ValueError('Outline evidence must remain in this workspace')
    directory.mkdir(parents=True,exist_ok=False)
    frozen=archive_source(ROOT,directory/'source.zip')
    baseline=ROOT/'logs/tk_outline_resize/baseline-20261105'
    if not (baseline/'checkout').exists():
        expected=json.loads((baseline/'source.json').read_text(encoding='utf-8'))
        extract_checked(baseline/'source.zip',baseline/'checkout',expected)
    result=dict(scope='Outline-only sizing; all code before this consolidated run',
        source_sha256=frozen['sha256'],stages=[],complete=False,
        physical_drag='not-exercised',user_acceptance='pending')

    def execute(name,command,timeout):
        print('Starting '+name,flush=True)
        row=run_owned(command,directory/(name+'-process'),cwd=ROOT,timeout=timeout)
        row.update(stage=name,source_after=manifest(ROOT)['sha256'])
        row['source_unchanged']=row['source_after']==frozen['sha256']
        result['stages'].append(row)
        (directory/'progress.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(f"Finished {name}: exit {row.get('exit_code')}, {row['elapsed_seconds']:.2f}s",flush=True)
        if not row['source_unchanged']:
            raise RuntimeError('Source changed while a reader was live')
        return row.get('exit_code')==0 and row.get('owned_processes_stopped',False)

    try:
        old=ROOT/'logs/todo-suite.xml'
        if old.exists():
            shutil.copy2(old,directory/'previous-suite.xml')
        if (ROOT/'.coverage').exists():
            shutil.copy2(ROOT/'.coverage',directory/'previous-coverage')
        started=time.time_ns()
        success=execute('full-suite',[sys.executable,'-m','pytest','tests/','--cov=src',
                '-p','tools.tk_deferred_resize.pytest_report',
                '--dr-event-log='+str(directory/'pytest-events.jsonl'),
                '-o','faulthandler_timeout=60','--junitxml=logs/todo-suite.xml'],7200)
        fresh=old.exists() and old.stat().st_mtime_ns>=started
        if fresh:
            shutil.copy2(old,directory/'todo-suite.xml')
        if not success:
            result['stop_reason']='Consolidated suite failure: retain evidence, group repairs and recheck affected scope'
            return 1
        if not fresh:
            raise RuntimeError('The full suite did not produce a fresh JUnit report')
        shutil.copy2(old,directory/'todo-suite.xml')
        if not execute('count-floor',[sys.executable,'tools/check_test_counts.py',
                'logs/todo-suite.xml','--min-total','4350'],120):
            return 1
        for row in validation_plan():
            source=baseline/'checkout' if row['source']=='baseline' else ROOT
            command=[sys.executable,'-m','tools.tk_resize.worker','--source',str(source),
                '--output',str(directory/row['name']),'--scenario',row['scenario'],
                '--theme',row['theme'],'--ui-language',row['language'],
                '--text-scale',str(row['text_scale']),'--dpi-factor',str(row['dpi_factor']),
                '--view-state',row['state'],'--final-validation']
            if not execute(row['name'],command,180):
                result['stop_reason']='Private evidence failure; do not call later stages passed'
                return 1
        if not execute('manual-launcher-private',[sys.executable,
                str(ROOT/'tools/tk_resize/manual_launcher.py'),'--private-check'],90):
            return 1
        result['complete']=True
        return 0
    finally:
        result['source_after']=manifest(ROOT)['sha256']
        result['source_unchanged']=result['source_after']==frozen['sha256']
        (directory/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(run(parser.parse_args().output))


if __name__=='__main__':
    main()

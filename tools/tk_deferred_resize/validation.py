"""Run the completed implementation's suite and source-bound UI evidence serially."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from tools.tk_resize.snapshot import archive_source, manifest
from tools.tk_resize.owned import run_owned

ROOT=Path(__file__).resolve().parents[2]


def validation_plan():
    rows=[]
    themes=('pink','blue','yellow','green','light','dark','system')
    for index,theme in enumerate(themes):
        for variant in range(2):
            rows.append(dict(name=f'visual-{theme}-{variant}',source='candidate',scenario='two-windows',
                theme=theme,language='ja' if (index+variant)%2 else 'en',
                text_scale=1.5 if variant else 1.,dpi_factor=1.,state='normal'))
    for scenario,state in (('empty','normal'),('short','normal'),('two-windows','options'),
            ('two-windows','busy'),('two-windows','error'),('two-windows','preview'),
            ('history-200','normal'),('dictionary-20','normal'),('text-attachment','normal'),
            ('quick-long','normal'),('quick-expanded','normal'),
            ('history-200','home-history'),('dictionary-20','home-dictionary'),('two-windows','settings')):
        rows.append(dict(name=f'state-{scenario}-{state}',source='candidate',scenario=scenario,
            theme='dark',language='ja' if state in ('options','error') else 'en',
            text_scale=1.5 if state in ('options','error') else 1.,dpi_factor=1.,state=state))
    for index,source in enumerate(('baseline','candidate','candidate','baseline')):
        rows.append(dict(name=f'comparison-{index}-{source}',source=source,scenario='two-windows',
            theme='dark',language='en',text_scale=1.,dpi_factor=1.,state='normal'))
    rows.append(dict(name='lifecycle-50',source='candidate',scenario='lifecycle-50',
        theme='dark',language='en',text_scale=1.,dpi_factor=1.,state='normal'))
    return rows


def run(output, *, recheck_of=None, targeted=None, evidence_only=False, selected_stages=None):
    if targeted and not recheck_of:
        raise ValueError('A scoped repair recheck must retain its original run identity')
    if evidence_only and not recheck_of:
        raise ValueError('Evidence-only continuation must identify its original consolidated run')
    if selected_stages and not evidence_only:
        raise ValueError('A stage selection is only valid for an identified evidence continuation')
    directory=Path(output).resolve()
    if not directory.is_relative_to((ROOT/'logs/tk_deferred_resize').resolve()):
        raise ValueError('Validation artifacts must stay in this workspace')
    directory.mkdir(parents=True,exist_ok=False)
    frozen=archive_source(ROOT,directory/'source.zip')
    rows=validation_plan()
    full_plan=rows
    if selected_stages:
        unknown=set(selected_stages)-{row['name'] for row in rows}-{'manual-launcher-private'}
        if unknown:
            raise ValueError('Unknown evidence stages: '+', '.join(sorted(unknown)))
        rows=[row for row in rows if row['name'] in selected_stages]
    result=dict(scope='DR4 single serial consolidated validation',source_sha256=frozen['sha256'],
        recheck_of=recheck_of,targeted=targeted,stages=[],complete=False,
        selected_stages=selected_stages,full_plan_count=len(full_plan),
        physical_drag='not-exercised',user_acceptance='pending')
    (directory/'plan.json').write_text(json.dumps(dict(stages=rows,full_plan=full_plan,
        required_suite='python -m pytest tests/ --cov=src --junitxml=logs/todo-suite.xml',
        required_floor='python tools/check_test_counts.py logs/todo-suite.xml --min-total 4350',
        interpretation='Scripted gestures verify shell/interior geometry, not native input or displayed FPS',
        image_review='Required separately; clean counters do not approve appearance'),indent=2),encoding='utf-8')
    def execute(name,command,timeout):
        print('Starting '+name,flush=True)
        record=run_owned(command,directory/(name+'-process'),cwd=ROOT,timeout=timeout)
        record.update(stage=name,source_after=manifest(ROOT)['sha256'])
        record['source_unchanged']=record['source_after']==frozen['sha256']
        result['stages'].append(record)
        (directory/'progress.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(f"Finished {name}: exit {record.get('exit_code')}, elapsed {record['elapsed_seconds']:.2f}s",flush=True)
        if not record['source_unchanged']:
            raise RuntimeError('Source changed while a reader was live')
        return record.get('exit_code')==0 and record.get('owned_processes_stopped',False)
    try:
        if targeted:
            success=execute('affected-suite',[sys.executable,'-m','pytest',*targeted,
                '-p','tools.tk_deferred_resize.pytest_report',
                '--dr-event-log='+str(directory/'pytest-events.jsonl'),
                '-o','faulthandler_timeout=60', '--cov=src',
                '--junitxml='+str(directory/'affected-suite.xml')],3600)
            result['complete']=success
            return 0 if success else 1
        if not evidence_only:
            for name in ('logs/todo-suite.xml','.coverage'):
                old=ROOT/name
                if old.is_file():
                    shutil.copy2(old,directory/('preexisting-'+old.name))
            started_ns=time.time_ns()
            success=execute('full-suite',[sys.executable,'-m','pytest','tests/','--cov=src',
                '--junitxml=logs/todo-suite.xml'],7200)
            report=ROOT/'logs/todo-suite.xml'
            fresh=report.is_file() and report.stat().st_mtime_ns>=started_ns
            result['fresh_suite_report']=fresh
            if fresh:
                shutil.copy2(ROOT/'logs/todo-suite.xml',directory/'todo-suite.xml')
                floor=execute('count-floor',[sys.executable,'tools/check_test_counts.py',
                    'logs/todo-suite.xml','--min-total','4350'],120)
            else:
                floor=False
            if not success or not floor:
                result['stop_reason']='Original suite/floor failure retained; repair the reported group before affected recheck'
                return 1
        for row in rows:
            source=ROOT if row['source']=='candidate' else ROOT/'logs/tk_deferred_resize/baseline-20261104/checkout'
            command=[sys.executable,'-m','tools.tk_resize.worker','--source',str(source),
                '--output',str(directory/row['name']),'--scenario',row['scenario'],'--final-validation',
                '--theme',row['theme'],'--ui-language',row['language'],'--text-scale',str(row['text_scale']),
                '--dpi-factor',str(row['dpi_factor']),'--view-state',row['state']]
            if not execute(row['name'],command,240):
                result['stop_reason']='UI evidence reported a failure; original output retained for grouped repair'
                return 1
        if not selected_stages or 'manual-launcher-private' in selected_stages:
            if not execute('manual-launcher-private',[sys.executable,'tools/tk_resize/manual_launcher.py',
                        '--private-check'],150):
                result['stop_reason']='Private manual-launch preparation failed'
                return 1
        result['complete']=True
        return 0
    finally:
        result['source_after']=manifest(ROOT)['sha256']
        result['source_unchanged']=result['source_after']==frozen['sha256']
        (directory/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--recheck-of')
    parser.add_argument('--targeted',nargs='+')
    parser.add_argument('--evidence-only',action='store_true')
    parser.add_argument('--stages',nargs='+')
    arguments=parser.parse_args()
    raise SystemExit(run(arguments.output,recheck_of=arguments.recheck_of,
                         targeted=arguments.targeted,evidence_only=arguments.evidence_only,
                         selected_stages=arguments.stages))

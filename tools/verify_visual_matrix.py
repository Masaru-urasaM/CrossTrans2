"""Run the complete private-desktop product matrix with bounded independent processes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.ui.theme.tokens import PALETTES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='v2-final-matrix')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--scales', default='100,125,150,175,200')
    parser.add_argument('--themes', default=','.join(PALETTES))
    parser.add_argument('--locales', default='en,ja')
    args = parser.parse_args()
    output = ROOT / 'logs' / 'visual-matrix'
    output.mkdir(parents=True, exist_ok=True)
    jobs = [(theme, locale) for theme in args.themes.split(',') for locale in args.locales.split(',')]
    def run(job):
        theme, locale = job
        label = f'{args.label}-{theme}-{locale}'
        log = output / f'{label}.txt'
        with log.open('w', encoding='utf-8') as stream:
            process = subprocess.run([sys.executable, str(ROOT / 'tools/ui_gallery.py'),
                                      '--label', label, '--theme', theme, '--ui-language', locale,
                                      '--scales', args.scales, '--surfaces', 'product', '--strict'],
                                     cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        report = ROOT / 'logs/ui_gallery' / f'{label}.json'
        data = json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
        result = dict(theme=theme, locale=locale, returncode=process.returncode,
                      captures=data.get('captures'), findings=data.get('findings_by_kind'),
                      errors=data.get('errors'), report=str(report.relative_to(ROOT)))
        print(json.dumps(result), flush=True)
        return result
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            results.append(future.result())
            (output / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    return int(any(row['returncode'] != 0 for row in results))


if __name__ == '__main__':
    raise SystemExit(main())

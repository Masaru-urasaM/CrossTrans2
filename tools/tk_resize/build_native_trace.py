"""Build the optional fixture-only observer with an explicitly selected compiler."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name('native_trace.c')
OUTPUT = ROOT / 'logs/tk_resize/native-trace-runtime'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, required=True)
    args = parser.parse_args(argv)
    compiler = args.compiler.resolve(strict=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    library = OUTPUT / 'native_trace.dll'
    command = [str(compiler), '-shared', '-O2', '-Wall', '-Wextra', '-Werror',
               str(SOURCE), '-o', str(library), '-lcomctl32', '-luser32']
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (OUTPUT / 'build.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
    completed.check_returncode()
    metadata = dict(abi=1, source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        dll_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
        compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest(), command=command,
        purpose='Optional native rectangle/timing observer for isolated manual fixtures only')
    (OUTPUT / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status='built', dll=str(library), **metadata)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

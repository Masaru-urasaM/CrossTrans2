"""
Delete the constants of `src/ui/theme/legacy.py` that nothing reads any more.

    python tools/prune_legacy.py          # report
    python tools/prune_legacy.py --apply  # delete them

A surface that adopts the design tokens stops reading the old literals (TODO.md U3-U13); the
module is a bridge, so what no `L.NAME` reaches goes - `tests/test_theme_legacy.py` fails on an
orphan, and this is the way to meet it without editing a generated file by hand. Lines are
removed whole (each constant is one line with its trailing note); comments and the highlight
palette are left alone.
"""
import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = os.path.join(REPO, 'src', 'ui', 'theme', 'legacy.py')
ASSIGNMENT = re.compile(r'^([A-Z][A-Z0-9_]*) = ')
KEEP = {'HIGHLIGHT_COLORS'}                     # read as L.HIGHLIGHT_COLORS, but never one line


def source_text() -> str:
    """Every .py under src/ except the theme package's own files (manager.py excepted, as
    the test does), joined: the text an `L.NAME` could be in."""
    parts = []
    for folder, dirs, files in os.walk(os.path.join(REPO, 'src')):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'theme')]
        for name in files:
            if name.endswith('.py'):
                with open(os.path.join(folder, name), encoding='utf-8') as fh:
                    parts.append(fh.read())
    with open(os.path.join(REPO, 'src', 'ui', 'theme', 'manager.py'), encoding='utf-8') as fh:
        parts.append(fh.read())
    return '\n'.join(parts)


def orphans(lines, text: str):
    found = []
    for number, line in enumerate(lines):
        match = ASSIGNMENT.match(line)
        if match and match.group(1) not in KEEP and not re.search(rf'\bL\.{match.group(1)}\b', text):
            found.append((number, match.group(1)))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if not os.path.exists(LEGACY):
        print('legacy.py is retired; no compatibility constants remain')
        return 0
    with open(LEGACY, 'rb') as fh:
        raw = fh.read()
    crlf = b'\r\n' in raw
    lines = raw.decode('utf-8').replace('\r\n', '\n').split('\n')
    found = orphans(lines, source_text())
    for _number, name in found:
        print(name)
    print(f"{len(found)} orphan constant(s) in legacy.py")
    if args.apply and found:
        drop = {number for number, _name in found}
        kept = [line for number, line in enumerate(lines) if number not in drop]
        text = '\n'.join(kept)
        with open(LEGACY, 'wb') as fh:
            fh.write((text.replace('\n', '\r\n') if crlf else text).encode('utf-8'))
        print("deleted")
    return 0


if __name__ == '__main__':
    sys.exit(main())

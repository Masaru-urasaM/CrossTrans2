"""Every relative link in the tracked Markdown resolves to a tracked file (DEFERRED D34, audit D12).

Moving files into `docs/archive/` broke 107 relative links, found by the repository audit of 2026-11-11 and
repointed on 2026-11-19 (TODO Task 26). A link counts only when its target is in git, so a link to a file
kept out of the repository (`CLAUDE.md`, `cloudflare_worker.js`) fails here as it would on GitHub.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
FENCE = re.compile(r'```.*?```', re.S)


def tracked():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        pytest.skip('needs a git checkout')
    listing = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True).stdout
    files = {name for name in listing.decode('utf-8').split('\0') if name}
    folders = {str(Path(name).parent).replace('\\', '/') for name in files}
    parents = {'/'.join(folder.split('/')[:depth]) for folder in folders for depth in range(1, folder.count('/') + 2)}
    return files, parents


def broken_links():
    files, folders = tracked()
    found = []
    for name in sorted(name for name in files if name.endswith('.md')):
        text = FENCE.sub('', (ROOT / name).read_text(encoding='utf-8'))
        for match in LINK.finditer(text):
            target = match.group(1)
            if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I) or target.startswith('#'):
                continue
            path = unquote(target.split('#', 1)[0])
            if not path:
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(name), path)).replace('\\', '/')
            if resolved not in files and resolved not in folders:
                found.append(f'{name} -> {target}')
    return found


def test_every_relative_markdown_link_resolves():
    assert broken_links() == []

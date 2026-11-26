"""Source-bound snapshots; neither a Git commit nor runtime acceptance evidence."""
import hashlib
import json
from pathlib import Path
import zipfile


SUFFIXES = {'.py', '.c', '.h', '.qml', '.json', '.md', '.txt', '.toml', '.ini', '.bat', '.ps1',
            '.yml', '.yaml', '.ico', '.png', '.svg', '.spec', '.iss'}


def manifest(root):
    root = Path(root).resolve()
    paths = [path for path in root.iterdir() if path.is_file() and path.suffix in SUFFIXES]
    for name in ('src', 'tests', 'tools', 'docs', 'installer', '.github'):
        paths.extend(path for path in (root / name).rglob('*')
                     if path.is_file() and path.suffix in SUFFIXES and '__pycache__' not in path.parts
                     and path != root / 'docs/todo_implementation_audit.json')
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(set(paths))}
    return dict(sha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), files=files)


def archive_source(root, destination):
    root, destination = Path(root).resolve(), Path(destination)
    source = manifest(root)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name in source['files']:
            archive.write(root / name, name)
    destination.with_suffix('.json').write_text(json.dumps(source, indent=2) + '\n', encoding='utf-8')
    return source


def extract_checked(archive_path, destination, expected):
    """Reject traversal and verify every retained file before importing a baseline."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            path = (destination / item.filename).resolve()
            if not path.is_relative_to(destination):
                raise ValueError('Source archive contains a path outside the checkout')
            if item.is_dir():
                continue
            data = archive.read(item)
            if hashlib.sha256(data).hexdigest() != expected['files'].get(item.filename):
                raise ValueError('Source archive digest mismatch: ' + item.filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    for name, digest in expected['files'].items():
        path = (destination / name).resolve()
        if not path.is_relative_to(destination) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Source archive is missing a verified file: ' + name)
    return destination

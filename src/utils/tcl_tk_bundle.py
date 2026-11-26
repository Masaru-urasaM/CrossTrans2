"""Tcl/Tk 9.0 runtime data and TkDnD binary collection helpers for PyInstaller builds.

Python 3.14+ on Windows ships Tcl/Tk 9.0 with its standard script libraries
embedded inside ``tcl90.dll`` (``tcl_library/``) and ``tcl9tk90.dll``
(``tk_library/``) as ZIP archives mounted via ``//zipfs:``.

PyInstaller 6.x queries ``tcl.eval("info library")``, receives
``//zipfs:/lib/tcl/tcl_library``, checks ``os.path.isdir(...)`` (which is False
for a virtual zipfs path), and skips collecting ``_tcl_data`` and ``_tk_data``
while still injecting ``pyi_rth__tkinter.py`` - causing the frozen executable
to fail at boot with ``FileNotFoundError: Tcl data directory "...\\_tcl_data"
not found``.

In addition, ``tkinterdnd2 >= 0.6.4`` separates Tcl 9 shared libraries into
``tkdnd/<platform>-tcl9/`` (e.g. ``win-x64-tcl9/libtkdnd2.10.3.dll``), which
older PyInstaller hooks overlook when collecting only ``tkdnd/win-x64/`` (Tcl 8.x).
This module provides explicit collection and verification for both.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import os
from pathlib import Path
import platform
import sys
from typing import Iterable, Sequence
import zipfile

TCL_ROOTNAME = '_tcl_data'
TK_ROOTNAME = '_tk_data'
REQUIRED_TCL_TK_FILES = (
    f'{TCL_ROOTNAME}/init.tcl',
    f'{TK_ROOTNAME}/tk.tcl',
)
TKDND_TCL9_WINDOWS_ARCH_DIRS = {
    'AMD64': 'win-x64-tcl9',
    'ARM64': 'win-arm64-tcl9',
    'x86': 'win-x86-tcl9',
}
REQUIRED_TKDND_TCL9_FILES = (
    'tkinterdnd2/tkdnd/win-x64-tcl9/pkgIndex.tcl',
    'tkinterdnd2/tkdnd/win-x64-tcl9/libtkdnd2.10.3.dll',
)
_EXCLUDED_PATTERNS = ('demos', '*.lib', 'tclConfig.sh', 'tkConfig.sh')


def _is_excluded(rel_parts: Sequence[str]) -> bool:
    for part in rel_parts:
        if any(fnmatch.fnmatch(part, pat) for pat in _EXCLUDED_PATTERNS):
            return True
    return False


def extract_zipfs_library(
    dll_path: str | os.PathLike[str],
    archive_prefix: str,
    target_dir: str | os.PathLike[str],
) -> list[Path]:
    """Extract ``archive_prefix/`` from a Tcl/Tk DLL with an embedded zipfs archive."""
    dll = Path(dll_path)
    dest_root = Path(target_dir)
    prefix = archive_prefix.rstrip('/') + '/'
    extracted: list[Path] = []
    if not dll.is_file() or not zipfile.is_zipfile(dll):
        return extracted

    with zipfile.ZipFile(dll, 'r') as zf:
        for member in zf.infolist():
            if member.is_dir() or not member.filename.startswith(prefix):
                continue
            rel = member.filename[len(prefix):].strip('/')
            if not rel:
                continue
            rel_parts = rel.split('/')
            if _is_excluded(rel_parts):
                continue
            out_path = dest_root.joinpath(*rel_parts)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(member)
            if not out_path.is_file() or out_path.stat().st_size != len(data):
                out_path.write_bytes(data)
            extracted.append(out_path)
    return extracted


def _candidate_dll_dirs(extra_dirs: Iterable[str | os.PathLike[str]] = ()) -> list[Path]:
    dirs: list[Path] = []
    for item in extra_dirs:
        if item:
            dirs.append(Path(item))
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        dirs.append(Path(meipass))
    for prefix in {sys.base_prefix, sys.prefix, os.path.dirname(sys.executable)}:
        if prefix:
            p = Path(prefix)
            dirs.append(p / 'DLLs')
            dirs.append(p)
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen and d.is_dir():
            seen.add(key)
            unique.append(d)
    return unique


def _find_dll(patterns: Sequence[str], search_dirs: Sequence[Path]) -> Path | None:
    for directory in search_dirs:
        for pattern in patterns:
            for match in sorted(directory.glob(pattern)):
                if match.is_file() and zipfile.is_zipfile(match):
                    return match
    return None


def ensure_tcl_tk_dirs(
    base_dir: str | os.PathLike[str],
    dll_search_dirs: Sequence[str | os.PathLike[str]] = (),
) -> dict[str, object]:
    """Ensure ``_tcl_data/init.tcl`` and ``_tk_data/tk.tcl`` exist under ``base_dir``."""
    root = Path(base_dir)
    tcl_dir = root / TCL_ROOTNAME
    tk_dir = root / TK_ROOTNAME
    tcl_init = tcl_dir / 'init.tcl'
    tk_init = tk_dir / 'tk.tcl'

    extracted_any = False
    search_dirs = _candidate_dll_dirs(dll_search_dirs)

    if not tcl_init.is_file():
        tcl_dll = _find_dll(('tcl9*.dll', 'tcl8*.dll'), search_dirs)
        if tcl_dll is not None:
            extract_zipfs_library(tcl_dll, 'tcl_library', tcl_dir)
            extracted_any = True

    if not tk_init.is_file():
        tk_dll = _find_dll(('tcl9tk9*.dll', 'tk9*.dll', 'tk8*.dll'), search_dirs)
        if tk_dll is not None:
            extract_zipfs_library(tk_dll, 'tk_library', tk_dir)
            extracted_any = True

    return {
        'tcl_dir': str(tcl_dir),
        'tk_dir': str(tk_dir),
        'tcl_ready': tcl_init.is_file(),
        'tk_ready': tk_init.is_file(),
        'extracted': extracted_any,
    }


def collect_pyinstaller_tcl_tk_datas(staging_dir: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Return ``(source_file, dest_dir)`` pairs for ``Analysis(datas=...)`` when PyInstaller skips zipfs Tcl/Tk."""
    try:
        from PyInstaller.utils.hooks.tcl_tk import tcltk_info
        existing = {
            str(dest).replace('\\', '/').lstrip('./')
            for dest, _src, _kind in getattr(tcltk_info, 'data_files', ())
        }
        if all(req in existing for req in REQUIRED_TCL_TK_FILES):
            return []
    except Exception:
        pass

    staging = Path(staging_dir)
    status = ensure_tcl_tk_dirs(staging)
    if not (status['tcl_ready'] and status['tk_ready']):
        raise RuntimeError(
            f'Failed to prepare Tcl/Tk runtime data in {staging}: {status}'
        )

    datas: list[tuple[str, str]] = []
    for root_name in (TCL_ROOTNAME, TK_ROOTNAME):
        tree = staging / root_name
        for file_path in sorted(tree.rglob('*')):
            if file_path.is_file():
                rel_parent = file_path.parent.relative_to(staging).as_posix()
                datas.append((str(file_path), rel_parent))
    return datas


def _resolve_tkdnd_tcl9_subdir(arch: str | None = None, system: str | None = None) -> tuple[str | None, str]:
    sys_name = system or platform.system()
    if sys_name == 'Windows':
        machine = arch or os.environ.get('PROCESSOR_ARCHITECTURE', platform.machine())
        return TKDND_TCL9_WINDOWS_ARCH_DIRS.get(machine), '*.dll'
    if sys_name == 'Darwin':
        machine = arch or platform.machine()
        return {'arm64': 'osx-arm64-tcl9', 'x86_64': 'osx-x64-tcl9'}.get(machine), '*.dylib'
    if sys_name == 'Linux':
        machine = arch or platform.machine()
        return {'aarch64': 'linux-arm64-tcl9', 'x86_64': 'linux-x64-tcl9'}.get(machine), '*.so'
    return None, ''


def collect_tkinterdnd2_tcl9(
    pkg_dir: str | os.PathLike[str] | None = None,
    *,
    arch: str | None = None,
    system: str | None = None,
    require_when_tcl9: bool = True,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Collect Tcl 9 ``tkinterdnd2`` script data and native binaries.

    PyInstaller's built-in hook only collects ``tkdnd/win-x64`` (compiled for
    Tcl 8.x), leaving frozen Tcl 9 builds without ``win-x64-tcl9``.
    """
    root_dir: Path | None = Path(pkg_dir) if pkg_dir is not None else None
    if root_dir is None:
        spec = importlib.util.find_spec('tkinterdnd2')
        if spec and spec.origin:
            root_dir = Path(spec.origin).parent

    if root_dir is None or not root_dir.is_dir():
        return [], []

    subdir, lib_glob = _resolve_tkdnd_tcl9_subdir(arch=arch, system=system)
    if not subdir:
        return [], []

    src_dir = root_dir / 'tkdnd' / subdir
    datas: list[tuple[str, str]] = []
    binaries: list[tuple[str, str]] = []

    if src_dir.is_dir():
        dest_dir = f'tkinterdnd2/tkdnd/{subdir}'
        for tcl_file in sorted(src_dir.glob('*.tcl')):
            if tcl_file.is_file():
                datas.append((str(tcl_file), dest_dir))
        for bin_file in sorted(src_dir.glob(lib_glob)):
            if bin_file.is_file():
                binaries.append((str(bin_file), dest_dir))

    if require_when_tcl9 and (not binaries or not datas):
        try:
            import tkinter
            tcl_major = int(str(tkinter.TclVersion).split('.')[0])
        except Exception:
            tcl_major = 0
        if tcl_major >= 9:
            raise RuntimeError(
                f'Tcl {tkinter.TclVersion} requires tkinterdnd2 Tcl 9 runtime files in {src_dir}, '
                f'found {len(datas)} .tcl and {len(binaries)} {lib_glob} files. '
                'Ensure tkinterdnd2 >= 0.6.4 is active in the build environment.'
            )

    return datas, binaries

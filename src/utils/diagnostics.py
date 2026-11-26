"""Diagnostics, privacy-safe bug-report export, native fatal notices, and frozen runtime self-check."""
from __future__ import annotations

import ctypes
from datetime import datetime
import importlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import tkinter as tk
import urllib.parse
import zipfile

from src.constants import APP_NAME, FEEDBACK_URL, VERSION
from src.utils.logging_setup import (
    CRASH_REPORT_FILENAME,
    collect_runtime_diagnostics,
    daily_logs as logging_daily_logs,
    is_debug_mode,
    resolve_log_dir,
)

# Secret patterns redacted before any diagnostic summary or log bundle leaves the machine.
_HEADER_SECRET_PATTERN = re.compile(
    r'(?i)\b(authorization|x-goog-api-key|api[_-]?key|token|secret|password)\s*[:=]\s*(?:Bearer\s+)?([^\s,;"}]+)'
)
_SECRET_PATTERNS = (
    re.compile(r'(?i)\bBearer\s+[^\s,;"}]+'),
    re.compile(r'\bAIza[0-9A-Za-z\-_]{16,}\b'),
    re.compile(r'\bAQ\.[0-9A-Za-z\-_.]{16,}'),       # Google auth keys (AI Studio since 2026-05-28)
    re.compile(r'\bsk-[A-Za-z0-9\-_]{16,}\b'),
    re.compile(r'\bgsk_[A-Za-z0-9\-_]{16,}\b'),
    re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{16,}\b'),
)
_SENSITIVE_KEY_WORDS = ('api_key', 'token', 'secret', 'password', 'encrypted', 'history')


def redact_text(text: str) -> str:
    """Mask API keys, tokens, and authorization headers in free-form logs or reports."""
    if not text:
        return ''
    cleaned = _HEADER_SECRET_PATTERN.sub(r'\1=[REDACTED]', text)
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub('[REDACTED]', cleaned)
    return cleaned


def sanitize_config_snapshot(config=None) -> dict:
    """Return non-sensitive configuration metadata for bug reports."""
    if config is None:
        return {}
    try:
        api_keys = list(config.get_api_keys() or [])
    except Exception:
        api_keys = []
    try:
        audio_keys = list(config.get_audio_keys() or [])
    except Exception:
        audio_keys = []

    def _summarize_keys(rows):
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            has_secret = bool((row.get('api_key') or row.get('api_key_encrypted') or '').strip())
            items.append({
                'provider': row.get('provider', 'Auto'),
                'model': row.get('model_name', ''),
                'configured': has_secret,
                'vision_capable': row.get('vision_capable'),
                'file_capable': row.get('file_capable'),
                'audio_capable': row.get('audio_capable'),
            })
        return items

    getter = getattr(config, 'get', lambda _k, default=None: default)
    return {
        'ui_language': getter('ui_language', 'en'),
        'theme_mode': getter('theme_mode', 'system'),
        'text_scale': getter('text_scale', 1.0),
        'welcome_seen': bool(getter('welcome_seen', False)),
        'autostart': bool(getter('autostart', False)),
        'debug_logging': bool(getter('debug_logging', False)),
        'shortcuts_enabled': bool(getter('shortcuts_enabled', True)),
        'furigana_enabled': bool(getter('furigana_enabled', True)),
        'linked_reading_enabled': bool(getter('linked_reading_enabled', True)),
        'audio_enabled': bool(getter('audio_enabled', True)),
        'audio_mode': getter('audio_mode', 'two_step'),
        'audio_source': getter('audio_source', 'pc'),
        'audio_hotkey_action': getter('audio_hotkey_action', 'panel'),
        'audio_model': getter('audio_model', ''),
        'audio_microphone': getter('audio_microphone', None),
        'audio_playback': getter('audio_playback', None),
        'nlp_installed': list(getter('nlp_installed', []) or []),
        'api_keys_count': len(api_keys),
        'api_keys': _summarize_keys(api_keys),
        'audio_keys_count': len(audio_keys),
        'audio_keys': _summarize_keys(audio_keys),
        'has_github_update_token': bool(getter('github_update_token_encrypted', '')),
    }


def _probe_module_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for mod_name in (
        'ttkbootstrap',
        'tkinterdnd2',
        'windnd',
        'pymupdf',
        'pykakasi',
        'av',
        'numpy',
        'pyaudiowpatch',
        'pystray',
        'keyboard',
    ):
        try:
            mod = importlib.import_module(mod_name)
            versions[mod_name] = str(
                getattr(mod, '__version__', None)
                or getattr(mod, 'version', None)
                or 'installed'
            )
        except Exception as exc:
            versions[mod_name] = f'unavailable ({type(exc).__name__}: {exc})'
    try:
        import tkinterdnd2
        versions['tkinterdnd2_Version'] = str(getattr(tkinterdnd2.TkinterDnD, 'TkdndVersion', None) or 'uninitialized')
    except Exception:
        pass
    return versions


def collect_environment_report(config=None, shortcut_status: dict | None = None) -> dict:
    """Assemble structured runtime, dependency, log, and sanitized config metadata."""
    base = collect_runtime_diagnostics()
    log_dir = base['log_dir']
    crash_file = os.path.join(log_dir, CRASH_REPORT_FILENAME)
    fault_file = os.path.join(log_dir, 'native_fault.log')
    daily_logs = logging_daily_logs(log_dir)

    return {
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'app': APP_NAME,
        'version': VERSION,
        'runtime': base,
        'tcl_version': str(getattr(tk, 'TclVersion', '')),
        'tk_version': str(getattr(tk, 'TkVersion', '')),
        'debug_logging': is_debug_mode(config=config),
        'dependencies': _probe_module_versions(),
        'shortcut_status': dict(shortcut_status or {}),
        'logs': {
            'log_dir': log_dir,
            'daily_log_count': len(daily_logs),
            'latest_log': os.path.basename(daily_logs[-1]) if daily_logs else None,
            'crash_report_exists': os.path.isfile(crash_file),
            'crash_report_bytes': os.path.getsize(crash_file) if os.path.isfile(crash_file) else 0,
            'native_fault_bytes': os.path.getsize(fault_file) if os.path.isfile(fault_file) else 0,
        },
        'settings': sanitize_config_snapshot(config),
    }


def _tail_text(path: str | os.PathLike[str], max_lines: int) -> str:
    p = Path(path)
    if not p.is_file():
        return ''
    try:
        lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return ''
    return redact_text('\n'.join(lines[-max_lines:]))


def _recent_log_lines(log_dir: str, max_lines: int = 50) -> tuple[str, list[str]]:
    logs = logging_daily_logs(log_dir)
    if not logs:
        return 'none', []
    latest = logs[-1]
    try:
        raw_lines = Path(latest).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return os.path.basename(latest), []
    formatted = []
    for raw in raw_lines[-max_lines:]:
        try:
            item = json.loads(raw)
            formatted.append(
                f"[{item.get('ts', '')}] {item.get('level', '')} "
                f"[{item.get('tag', '')}] {item.get('msg', '')}"
            )
            if item.get('trace'):
                formatted.append(item['trace'].rstrip())
        except Exception:
            formatted.append(raw)
    return os.path.basename(latest), [redact_text(line) for line in formatted]


def format_diagnostic_report(config=None, shortcut_status: dict | None = None,
                             max_log_lines: int = 45) -> str:
    """Render a concise, readable, privacy-safe bug-report summary."""
    env = collect_environment_report(config=config, shortcut_status=shortcut_status)
    rt = env['runtime']
    logs_meta = env['logs']
    lines = [
        f'CrossTrans2 Diagnostic Report (v{env["version"]})',
        f'Generated: {env["generated_at"]}',
        f'Platform: {rt["platform"]}',
        f'Python: {rt["python"]} | Tcl/Tk: {env["tcl_version"]}/{env["tk_version"]} | Frozen: {rt["frozen"]}',
        (
            f'Bundle readiness: tcl={rt["tcl_data_ready"]}, tk={rt["tk_data_ready"]}, '
            f'tkdnd_tcl9={rt["tkdnd_tcl9_ready"]}'
        ),
        f'Debug mode: {env["debug_logging"]}',
        f'Log directory: {logs_meta["log_dir"]}',
        '',
        'Dependencies:',
    ]
    for name, val in env['dependencies'].items():
        lines.append(f'  - {name}: {val}')

    if env['shortcut_status']:
        lines.extend(['', 'Shortcut registration status:'])
        for owner, status in sorted(env['shortcut_status'].items()):
            lines.append(f'  - {owner}: {status}')

    if env['settings']:
        s = env['settings']
        lines.extend([
            '',
            'Sanitized settings:',
            f'  - ui_language={s["ui_language"]}, theme={s["theme_mode"]}, text_scale={s["text_scale"]}',
            f'  - welcome_seen={s["welcome_seen"]}, autostart={s["autostart"]}, shortcuts={s["shortcuts_enabled"]}',
            f'  - audio_enabled={s["audio_enabled"]}, mode={s["audio_mode"]}, source={s["audio_source"]}, model={s["audio_model"]}',
            f'  - api_keys={s["api_keys_count"]}, audio_keys={s["audio_keys_count"]}, nlp_installed={s["nlp_installed"]}',
        ])

    crash_tail = _tail_text(os.path.join(logs_meta['log_dir'], CRASH_REPORT_FILENAME), 35)
    if crash_tail:
        lines.extend(['', 'Latest crash_report.txt excerpt:', crash_tail])

    fault_tail = _tail_text(os.path.join(logs_meta['log_dir'], 'native_fault.log'), 25)
    if fault_tail.strip():
        lines.extend(['', 'Latest native_fault.log excerpt:', fault_tail])

    latest_name, recent = _recent_log_lines(logs_meta['log_dir'], max_lines=max_log_lines)
    if recent:
        lines.extend(['', f'Recent log lines ({latest_name}):'])
        lines.extend(recent)

    return '\n'.join(lines)


def default_bundle_filename() -> str:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'CrossTrans2_diagnostics_{stamp}.zip'


def export_diagnostic_bundle(destination: str | os.PathLike[str] | None = None, *,
                             config=None, shortcut_status: dict | None = None) -> Path:
    """Write a redacted ZIP bundle with summary, environment metadata, crash reports, and logs."""
    log_dir = Path(resolve_log_dir())
    if destination is None:
        log_dir.mkdir(parents=True, exist_ok=True)
        dest_path = log_dir / default_bundle_filename()
    else:
        dest_path = Path(destination)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

    env = collect_environment_report(config=config, shortcut_status=shortcut_status)
    summary = format_diagnostic_report(config=config, shortcut_status=shortcut_status)

    with zipfile.ZipFile(dest_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('summary.txt', summary)
        zf.writestr('environment.json', redact_text(json.dumps(env, indent=2, ensure_ascii=False)))

        for filename in (CRASH_REPORT_FILENAME, 'native_fault.log'):
            src = log_dir / filename
            if src.is_file() and src.stat().st_size > 0:
                zf.writestr(f'logs/{filename}', redact_text(src.read_text(encoding='utf-8', errors='replace')))

        # The newest three files, rotated siblings included (sorted by name, the oldest rotated
        # sibling of a busy day came last and the day's own file could be left out).
        daily_logs = [Path(path) for path in logging_daily_logs(str(log_dir), rotated=True)]
        for src in daily_logs[-3:]:
            if src.is_file():
                zf.writestr(f'logs/{src.name}', redact_text(src.read_text(encoding='utf-8', errors='replace')))

    return dest_path


def build_github_issue_url(summary_text: str = '') -> str:
    """Create a GitHub new-issue URL prefilled with non-sensitive environment summary."""
    excerpt = redact_text(summary_text or format_diagnostic_report(max_log_lines=12))
    if len(excerpt) > 1200:
        excerpt = excerpt[:1200] + '\n... (truncated; full report copied to clipboard / exported zip)'
    body = (
        '### Describe the bug\n'
        'Please describe what happened and steps to reproduce.\n\n'
        '### Diagnostics\n'
        '```\n'
        f'{excerpt}\n'
        '```\n'
    )
    params = urllib.parse.urlencode({'title': f'[v{VERSION}] Bug report: ', 'body': body})
    return f'{FEEDBACK_URL}?{params}'


def open_logs_folder(log_dir: str | os.PathLike[str] | None = None) -> Path:
    """Ensure the log directory exists and open it in the file explorer."""
    target = Path(log_dir or resolve_log_dir())
    target.mkdir(parents=True, exist_ok=True)
    os.startfile(str(target))
    return target


def copy_text_native(text: str) -> bool:
    """Write Unicode text to the Windows clipboard via Win32 ctypes (no Tk needed)."""
    if sys.platform != 'win32' or os.environ.get('PYTEST_CURRENT_TEST') or not text:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            encoded = (text + '\x00').encode('utf-16-le')
            handle = kernel32.GlobalAlloc(0x0042, len(encoded))  # GMEM_MOVEABLE | GMEM_ZEROINIT
            if not handle:
                return False
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(ptr, encoded, len(encoded))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
                kernel32.GlobalFree(handle)
                return False
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def notify_fatal_error(title: str, error_summary: str, *, report_text: str = '',
                       report_path: str | None = None, log_dir: str | None = None) -> None:
    """Display an actionable Windows startup error dialog so failures are never silent."""
    if sys.platform != 'win32' or os.environ.get('PYTEST_CURRENT_TEST'):
        return
    resolved_dir = log_dir or resolve_log_dir()
    shown_path = report_path or os.path.join(resolved_dir, CRASH_REPORT_FILENAME)
    message = (
        'CrossTrans 2 encountered a critical error and could not start.\n\n'
        f'Error: {redact_text(error_summary)}\n\n'
        f'Diagnostic report:\n{shown_path}\n\n'
        'Yes: Copy diagnostic report & open logs folder\n'
        'No: Open logs folder only\n'
        'Cancel: Close'
    )
    try:
        # MB_YESNOCANCEL (0x3) | MB_ICONERROR (0x10) | MB_SETFOREGROUND (0x10000)
        choice = ctypes.windll.user32.MessageBoxW(None, message, title, 0x3 | 0x10 | 0x10000)
        if choice == 6:  # IDYES
            if report_text:
                copy_text_native(redact_text(report_text))
            open_logs_folder(resolved_dir)
        elif choice == 7:  # IDNO
            open_logs_folder(resolved_dir)
    except Exception:
        pass


def run_self_check(destination: str | os.PathLike[str] | None = None) -> int:
    """Run an isolated frozen/source startup & subsystem health check.

    Instantiates a real withdrawn Tk / TkinterDnD root, installs ttkbootstrap theme
    components, checks furigana/i18n/icon/PDF resources and verifies audio decoding
    and codecs without touching user profiles, clipboard, hotkeys, or network.
    """
    result: dict[str, object] = {
        'frozen': bool(getattr(sys, 'frozen', False)),
        'python': sys.version.split()[0],
        'errors': [],
        'checks': {},
    }
    errors: list[str] = result['errors']  # type: ignore[assignment]
    checks: dict[str, object] = result['checks']  # type: ignore[assignment]

    root = None
    temp_profile = tempfile.TemporaryDirectory(prefix='crosstrans_selfcheck_')
    files = None
    old_appdata = os.environ.get('APPDATA')
    os.environ['APPDATA'] = temp_profile.name
    try:
        from src.core.audio_media import AudioFiles, RATE, decode_media, read_pcm
        from src.ui import components as ui
        from src.ui.i18n import catalog
        from src.ui.theme.manager import ThemeManager, install_manager
        from src.ui.theme import engine as theme_engine
        from src.ui.tray import get_resource_path

        files = AudioFiles()

        # Check frozen Tcl/Tk/TkDnD data directories when running packaged
        runtime_diag = collect_runtime_diagnostics()
        checks['runtime'] = runtime_diag
        if runtime_diag['frozen']:
            if not runtime_diag['tcl_data_ready'] or not runtime_diag['tk_data_ready']:
                errors.append('Frozen _tcl_data or _tk_data directory is missing or incomplete.')
            if int(str(getattr(tk, 'TclVersion', '0')).split('.')[0]) >= 9 and not runtime_diag['tkdnd_tcl9_ready']:
                errors.append('Frozen tkinterdnd2 win-x64-tcl9 runtime is missing.')

        # 1. Real Tk / TkinterDnD initialization (exact failure point of the Tcl 8/9 bug)
        import tkinterdnd2
        root = tkinterdnd2.TkinterDnD.Tk()
        root.withdraw()
        tcl_ver = str(root.tk.eval('info patchlevel'))
        tkdnd_ver = str(getattr(tkinterdnd2.TkinterDnD, 'TkdndVersion', None) or root.tk.eval('package present tkdnd'))
        checks['tk'] = {'tcl_patchlevel': tcl_ver, 'tkdnd_version': tkdnd_ver}

        # 2. Isolated Config + ThemeManager + ttkbootstrap styles + private icon font
        from config import Config
        cfg = Config()
        cfg.CONFIG_DIR = os.path.join(temp_profile.name, APP_NAME)
        cfg.CONFIG_FILE = os.path.join(cfg.CONFIG_DIR, 'config.json')
        cfg._ensure_config_dir()
        cfg._config = dict(Config.DEFAULT_CONFIG)

        manager = install_manager(ThemeManager(cfg))
        t_engine = theme_engine.install(root, manager)
        ui.install(root, manager, t_engine.style)
        icon_img = ui.get().icon('bug', 16, ui.get().palette.text)
        checks['theme_and_icons'] = {'mode': manager.mode, 'icon_built': icon_img is not None}

        # 3. Essential packaged UI / furigana / PDF assets
        ico_path = get_resource_path(os.path.join('src', 'assets', 'CrossTrans.ico'))
        if not os.path.isfile(ico_path):
            errors.append(f'Missing application icon resource: {ico_path}')
        if len(catalog()) < 400:
            errors.append('UI translation catalog is incomplete.')

        from src.core import furigana
        annotated = furigana.annotate('日本語', 'Japanese')
        checks['furigana'] = {'segments': len(annotated)}

        import pymupdf
        checks['pymupdf'] = {'version': getattr(pymupdf, '__version__', 'installed')}

        # 4. Audio decode & codec checks
        import av
        import numpy
        import pyaudiowpatch as pa
        sample = Path(__file__).resolve().parents[1] / 'assets' / 'audio_probe.wav'
        output = files.directory / 'decoded.wav'
        seconds = decode_media(sample, output, threading.Event())
        samples = len(read_pcm(output))
        codecs = sorted({'aac', 'mp3', 'flac', 'opus', 'pcm_s16le'} & av.codecs_available)
        portaudio_bytes = pa.get_sample_size(pa.paInt16)
        checks['audio'] = {
            'av': av.__version__,
            'numpy': numpy.__version__,
            'seconds': seconds,
            'samples': samples,
            'portaudio_sample_bytes': portaudio_bytes,
            'codecs': codecs,
        }
        if samples != 4 * RATE or portaudio_bytes != 2 or len(codecs) != 5:
            errors.append('Audio runtime data or codecs are incomplete.')

    except Exception as exc:
        import traceback
        errors.append(f'{type(exc).__name__}: {exc}')
        result['traceback'] = traceback.format_exc()
    finally:
        if old_appdata is None:
            os.environ.pop('APPDATA', None)
        else:
            os.environ['APPDATA'] = old_appdata
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        if files is not None:
            files.cleanup()
        temp_profile.cleanup()

    payload = json.dumps(result, indent=2)
    if destination and str(destination) != '-':
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_text(payload, encoding='utf-8')
    else:
        print(payload)
    logging.info(f'[SelfCheck] complete errors={len(errors)}')
    return 1 if errors else 0

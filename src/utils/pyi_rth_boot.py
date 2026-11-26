"""Early PyInstaller runtime hook for CrossTrans2.

Executed before ``pyi_rth__tkinter.py`` and before ``main.py``.
Responsibilities:
1. Ensure ``_tcl_data`` and ``_tk_data`` exist inside ``sys._MEIPASS`` (self-healing
   from ``tcl90.dll`` / ``tcl9tk90.dll`` zipfs archives if needed) so
   ``pyi_rth__tkinter.py`` never fails with ``FileNotFoundError``.
2. Verify ``tkinterdnd2`` Tcl 9 runtime availability and record early boot telemetry.
3. Enable ``faulthandler`` and install early ``sys.excepthook`` /
   ``threading.excepthook`` handlers writing to ``%APPDATA%/CrossTrans2/logs``
   so runtime-hook or early-import failures are always logged on disk and surfaced
   in interactive GUI launches rather than failing silently.
"""
from __future__ import annotations


def _pyi_rthook() -> None:
    import ctypes
    from datetime import datetime
    import faulthandler
    import fnmatch
    import json
    import os
    from pathlib import Path
    import platform
    import subprocess
    import sys
    import tempfile
    import threading
    import traceback
    import zipfile

    app_name = 'CrossTrans2'
    boot_events: list[dict[str, str]] = []
    setattr(sys, '_crosstrans_boot_events', boot_events)

    def _resolve_profile_dir() -> Path:
        appdata = os.environ.get('APPDATA')
        if appdata:
            return Path(appdata) / app_name
        return Path(tempfile.gettempdir()) / app_name

    def _resolve_boot_log_dir() -> Path:
        candidates = [
            _resolve_profile_dir() / 'logs',
            Path(tempfile.gettempdir()) / f'{app_name}_logs',
        ]
        for directory in candidates:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                return directory
            except OSError:
                continue
        return Path('.')

    profile_dir = _resolve_profile_dir()
    log_dir = _resolve_boot_log_dir()
    setattr(sys, '_crosstrans_boot_log_dir', str(log_dir))

    cli_args = sys.argv[1:] if len(sys.argv) > 1 else []
    debug_enabled = (
        '--debug' in cli_args
        or os.environ.get('CROSSTRANS_DEBUG') == '1'
        or (profile_dir / 'debug.flag').is_file()
    )
    setattr(sys, '_crosstrans_debug', bool(debug_enabled))

    is_cli_check = any(
        flag in cli_args
        for flag in ('--self-check', '--audio-self-check', '--export-diagnostics')
    )

    def _append_jsonl(level: str, tag: str, msg: str, trace: str | None = None) -> None:
        entry: dict[str, str] = {
            'ts': datetime.now().astimezone().isoformat(timespec='milliseconds'),
            'level': level,
            'tag': tag,
            'msg': msg,
        }
        if trace:
            entry['trace'] = trace
        try:
            # The name logging_setup.daily_log_name() gives (this hook runs before the app's modules load).
            log_file = log_dir / f'translator_{datetime.now().strftime("%Y%m%d")}.jsonl'
            with open(log_file, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def _copy_native_clipboard(text: str) -> bool:
        if sys.platform != 'win32' or not text:
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

    def _show_early_error_dialog(detail: str, report_path: Path | None, report_text: str) -> None:
        if (
            sys.platform != 'win32'
            or not getattr(sys, 'frozen', False)
            or is_cli_check
            or os.environ.get('PYTEST_CURRENT_TEST')
        ):
            return
        try:
            path_hint = str(report_path or (log_dir / 'crash_report.txt'))
            message = (
                'CrossTrans 2 failed during early startup.\n\n'
                f'Error: {detail}\n\n'
                f'Diagnostic report:\n{path_hint}\n\n'
                'Yes: Copy diagnostic report & open logs folder\n'
                'No: Open logs folder only\n'
                'Cancel: Close'
            )
            # MB_YESNOCANCEL (0x3) | MB_ICONERROR (0x10) | MB_SETFOREGROUND (0x10000)
            choice = ctypes.windll.user32.MessageBoxW(
                None, message, 'CrossTrans 2 - Startup Error', 0x3 | 0x10 | 0x10000
            )
            if choice == 6:  # IDYES
                _copy_native_clipboard(report_text)
                subprocess.Popen(['explorer.exe', str(log_dir)])
            elif choice == 7:  # IDNO
                subprocess.Popen(['explorer.exe', str(log_dir)])
        except Exception:
            pass

    def _write_boot_crash_report(stage: str, detail: str, trace: str = '') -> tuple[Path | None, str]:
        now = datetime.now().astimezone().isoformat(timespec='milliseconds')
        meipass = getattr(sys, '_MEIPASS', None)
        meipass_summary = 'N/A'
        if meipass and os.path.isdir(meipass):
            try:
                items = sorted(os.listdir(meipass))
                meipass_summary = f'{len(items)} entries (sample: {items[:20]})'
            except OSError as exc:
                meipass_summary = f'<unreadable: {exc}>'
        lines = [
            '=== CrossTrans2 Crash / Diagnostic Report ===',
            f'Timestamp: {now}',
            f'Stage: {stage}',
            f'Executable: {getattr(sys, "executable", "")}',
            f'Frozen: {bool(getattr(sys, "frozen", False))}',
            f'DebugMode: {debug_enabled}',
            f'MEIPASS: {meipass} ({meipass_summary})',
            f'Platform: {platform.platform()}',
            f'Python: {sys.version}',
            f'Detail: {detail}',
        ]
        if trace:
            lines.extend(['', 'Traceback:', trace.rstrip()])
        lines.append('=' * 45 + '\n')
        report_text = '\n'.join(lines)
        saved_path: Path | None = None
        for target in (log_dir / 'crash_report.txt', Path(tempfile.gettempdir()) / 'CrossTrans2_crash_report.txt'):
            try:
                with open(target, 'a', encoding='utf-8') as fh:
                    fh.write(report_text)
                saved_path = target
                break
            except OSError:
                continue
        _append_jsonl('CRITICAL', 'BootCrash', f'{stage}: {detail}', trace or None)
        return saved_path, report_text

    # Enable faulthandler for native C/DLL faults if possible
    try:
        fault_path = log_dir / 'native_fault.log'
        fault_stream = open(fault_path, 'a', encoding='utf-8')
        faulthandler.enable(file=fault_stream, all_threads=True)
        setattr(sys, '_crosstrans_fault_stream', fault_stream)
    except Exception:
        pass

    # Install early excepthooks (replaced/augmented later by setup_logging)
    def _early_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        trace = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        detail = f'{exc_type.__name__}: {exc_value}'
        report_path, report_text = _write_boot_crash_report('early_uncaught_exception', detail, trace)
        _show_early_error_dialog(detail, report_path, report_text)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _early_thread_excepthook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        trace = ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        thread_name = getattr(args.thread, 'name', 'unknown')
        _write_boot_crash_report(
            f'early_thread_exception:{thread_name}',
            f'{args.exc_type.__name__}: {args.exc_value}',
            trace,
        )

    sys.excepthook = _early_excepthook
    threading.excepthook = _early_thread_excepthook

    # Self-heal Tcl/Tk 9.0 data directories inside sys._MEIPASS before pyi_rth__tkinter runs
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass and os.path.isdir(meipass):
        base = Path(meipass)
        tcl_dir = base / '_tcl_data'
        tk_dir = base / '_tk_data'
        excluded = ('demos', '*.lib', 'tclConfig.sh', 'tkConfig.sh')

        def _extract_dll_zip(dll_candidates: tuple[str, ...], prefix: str, dest: Path) -> int:
            count = 0
            prefix_slash = prefix.rstrip('/') + '/'
            for pattern in dll_candidates:
                for dll in sorted(base.glob(pattern)):
                    if not (dll.is_file() and zipfile.is_zipfile(dll)):
                        continue
                    with zipfile.ZipFile(dll, 'r') as zf:
                        for member in zf.infolist():
                            if member.is_dir() or not member.filename.startswith(prefix_slash):
                                continue
                            rel = member.filename[len(prefix_slash):].strip('/')
                            if not rel:
                                continue
                            parts = rel.split('/')
                            if any(fnmatch.fnmatch(p, pat) for p in parts for pat in excluded):
                                continue
                            target = dest.joinpath(*parts)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(zf.read(member))
                            count += 1
                    if count > 0:
                        return count
            return count

        try:
            if not (tcl_dir / 'init.tcl').is_file():
                n_tcl = _extract_dll_zip(('tcl9*.dll', 'tcl8*.dll'), 'tcl_library', tcl_dir)
                boot_events.append({
                    'level': 'WARNING' if n_tcl else 'ERROR',
                    'msg': f'[Boot] Extracted {n_tcl} Tcl library files into {tcl_dir}',
                })
            if not (tk_dir / 'tk.tcl').is_file():
                n_tk = _extract_dll_zip(('tcl9tk9*.dll', 'tk9*.dll', 'tk8*.dll'), 'tk_library', tk_dir)
                boot_events.append({
                    'level': 'WARNING' if n_tk else 'ERROR',
                    'msg': f'[Boot] Extracted {n_tk} Tk library files into {tk_dir}',
                })
        except Exception as exc:
            _write_boot_crash_report('tcl_tk_self_heal_failed', repr(exc), traceback.format_exc())

        tcl_ok = (tcl_dir / 'init.tcl').is_file()
        tk_ok = (tk_dir / 'tk.tcl').is_file()
        tkdnd_tcl9_ok = (base / 'tkinterdnd2' / 'tkdnd' / 'win-x64-tcl9' / 'pkgIndex.tcl').is_file()
        if not tcl_ok or not tk_ok:
            _write_boot_crash_report(
                'tcl_tk_data_missing',
                f'tcl_init={tcl_ok}, tk_init={tk_ok}, tkdnd_tcl9={tkdnd_tcl9_ok}',
            )
        elif debug_enabled:
            boot_events.append({
                'level': 'INFO',
                'msg': f'[Boot] MEIPASS ready tcl={tcl_ok} tk={tk_ok} tkdnd_tcl9={tkdnd_tcl9_ok}',
            })


_pyi_rthook()
del _pyi_rthook

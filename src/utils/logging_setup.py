"""
Logging setup for CrossTrans.

The **file** log is JSON Lines - one `{"ts","level","tag","msg"}` object per line,
so a tool can filter it instead of a human guessing at a regex (global Rule 9).
Structured events add `event`, `sid` and `data` (src/utils/log_events.py).
The **console** stays human-readable: `python main.py` prints to a terminal
somebody is reading, and JSON there helps nobody.
"""
import os
import re
import sys
import json
import time
import ctypes
import logging
import platform
import tempfile
import threading
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler

from src.constants import VERSION, APP_NAME

CRASH_REPORT_FILENAME = 'crash_report.txt'
DEBUG_FLAG_FILENAME = 'debug.flag'

# How long a daily log file is kept. The frozen app writes into %APPDATA%, which
# nothing ever cleans up on its behalf, so the growth has to be bounded here.
LOG_RETENTION_DAYS = 14

# ...and bounded in size as well as in time. Retention alone caps a *quiet* app;
# one runaway loop inside a single day is unbounded without this. At most
# (1 + LOG_BACKUP_COUNT) x LOG_MAX_BYTES per day.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# Today's file plus the rotated siblings RotatingFileHandler leaves beside it
# (translator_20261119.jsonl.1). They must be prunable too, or the size cap simply
# converts unbounded growth within a day into unbounded growth across days.
# The files are JSON Lines and since 2026-11-19 named for it (global Rule 9, TODO Task 26);
# files written before keep their translator_YYYYMMDD.log names until they are pruned, so
# everything that prunes or reads the logs takes both. `.bak` and anything else does not match.
_LOG_NAME_RE = re.compile(r'^translator_(\d{8})\.(jsonl|log)(?:\.(\d+))?$')

# 55 of the project's ~458 log calls already open with a bracketed tag -
# [ModelRotation], [Trial], [TOKENIZE]. That convention is more informative than
# the module name, so it becomes the `tag` field and leaves the message. Nothing
# at the call sites had to change for this.
_TAG_RE = re.compile(r'^\[([^\]\r\n]{1,40})\]\s*')


def is_frozen() -> bool:
    """True when running from the PyInstaller EXE rather than from source."""
    return getattr(sys, 'frozen', False)


class _SafeConsoleHandler(logging.StreamHandler):
    """Console handler that tolerates windowed builds where stdout/stderr is None."""

    def emit(self, record: logging.LogRecord) -> None:
        if self.stream is None:
            return
        super().emit(record)


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line: ts, level, tag, msg (global Rule 9).

    `ts` carries a real UTC offset rather than a bare local timestamp: a log is
    read next to a user saying "it broke at three", and read by a tool that must
    not guess which three.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception as exc:                     # a bad %-format must not lose the line
            message = f"<unformattable log record: {exc!r}>"

        matched = _TAG_RE.match(message)
        if matched:
            tag = matched.group(1)
            message = message[matched.end():]
        else:
            tag = record.module

        entry = {
            'ts': datetime.fromtimestamp(record.created).astimezone()
                          .isoformat(timespec='milliseconds'),
            'level': record.levelname,
            'tag': tag,
            'msg': message,
        }
        # Only when it adds something: with no bracketed tag the two are equal.
        if record.module != tag:
            entry['mod'] = record.module
        # Structured events (src/utils/log_events.py): the event name, the session it belongs to and
        # its measurements, so a tool reads fields instead of parsing `msg`.
        event = getattr(record, 'event', None)
        if isinstance(event, str) and event:
            entry['event'] = event
        sid = getattr(record, 'sid', None)
        if isinstance(sid, str) and sid:
            entry['sid'] = sid
        data = getattr(record, 'data', None)
        if isinstance(data, dict) and data:
            entry['data'] = data
        if record.exc_info:
            entry['trace'] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)


def resolve_log_dir() -> str:
    """Where the log file belongs.

    From source: `<project>/logs`, next to the code being worked on.

    From the EXE: `%APPDATA%/CrossTrans2/logs`. It must NOT be derived from
    `__file__` there - under a onefile build that path is inside the temporary
    `_MEIxxxxx` extraction directory, which PyInstaller deletes on exit. Logs
    written there are gone by the time anyone asks for them, which is the exact
    moment they matter. (Measured 2026-09-07: six orphaned `_MEI*` directories,
    202 MB, each holding a `logs/translator_*.log` nobody could have found;
    the files are `.jsonl` since 2026-11-19.)
    """
    if is_frozen():
        return os.path.join(os.environ.get('APPDATA', '.'), APP_NAME, 'logs')

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, 'logs')


def debug_flag_path(log_dir: str | None = None) -> str:
    """Location of the persistent early-boot debug flag beside the profile."""
    if is_frozen():
        return os.path.join(os.environ.get('APPDATA', '.'), APP_NAME, DEBUG_FLAG_FILENAME)
    return os.path.join(log_dir or resolve_log_dir(), DEBUG_FLAG_FILENAME)


def is_debug_mode(argv=None, config=None) -> bool:
    """True if verbose debug logging is active via CLI flag, env, flag file, or config."""
    args = sys.argv[1:] if argv is None else list(argv)
    if '--debug' in args:
        return True
    if os.environ.get('CROSSTRANS_DEBUG') == '1':
        return True
    if getattr(sys, '_crosstrans_debug', False):
        return True
    if config is not None:
        try:
            if config.get('debug_logging', False):
                return True
        except Exception:
            pass
    try:
        return os.path.isfile(debug_flag_path())
    except OSError:
        return False


def set_debug_logging(enabled: bool, *, persist_flag: bool = True) -> None:
    """Toggle root logger verbosity live and optionally persist the boot debug flag."""
    enabled = bool(enabled)
    setattr(sys, '_crosstrans_debug', enabled)
    level = logging.DEBUG if enabled else logging.INFO
    logging.getLogger().setLevel(level)
    if persist_flag:
        flag = debug_flag_path()
        try:
            if enabled:
                parent = os.path.dirname(flag)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(flag, 'w', encoding='utf-8') as fh:
                    fh.write('1\n')
            elif os.path.isfile(flag):
                os.remove(flag)
        except OSError:
            pass
    logging.info(f"[Diagnostics] Verbose debug logging {'enabled' if enabled else 'disabled'}")


def attach_debug_console() -> bool:
    """Allocate a Windows console window when frozen GUI app is launched with --debug."""
    if sys.platform != 'win32' or os.environ.get('PYTEST_CURRENT_TEST'):
        return False
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AllocConsole():
            return False
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
        sys.stderr = open('CONOUT$', 'w', encoding='utf-8', buffering=1)
        return True
    except Exception:
        return False


def collect_runtime_diagnostics() -> dict:
    """Collect non-sensitive environment details for startup and crash diagnostics."""
    meipass = getattr(sys, '_MEIPASS', None)
    tcl_ready = None
    tk_ready = None
    tkdnd_tcl9_ready = None
    if meipass and os.path.isdir(meipass):
        tcl_ready = os.path.isfile(os.path.join(meipass, '_tcl_data', 'init.tcl'))
        tk_ready = os.path.isfile(os.path.join(meipass, '_tk_data', 'tk.tcl'))
        tkdnd_tcl9_ready = (
            os.path.isfile(os.path.join(meipass, 'tkinterdnd2', 'tkdnd', 'win-x64-tcl9', 'pkgIndex.tcl'))
            and os.path.isfile(os.path.join(meipass, 'tkinterdnd2', 'tkdnd', 'win-x64-tcl9', 'libtkdnd2.10.3.dll'))
        )
    return {
        'version': VERSION,
        'frozen': is_frozen(),
        'debug_mode': is_debug_mode(),
        'executable': getattr(sys, 'executable', ''),
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'meipass': meipass,
        'tcl_data_ready': tcl_ready,
        'tk_data_ready': tk_ready,
        'tkdnd_tcl9_ready': tkdnd_tcl9_ready,
        'log_dir': resolve_log_dir(),
    }


def build_crash_report_text(exc_type, exc_value, exc_tb, *, stage: str = 'uncaught') -> str:
    """Format a structured human-readable diagnostic report for fatal/uncaught errors."""
    diag = collect_runtime_diagnostics()
    now = datetime.now().astimezone().isoformat(timespec='milliseconds')
    trace = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)) if exc_type else ''
    lines = [
        '=== CrossTrans2 Crash / Diagnostic Report ===',
        f'Timestamp: {now}',
        f'Stage: {stage}',
        f'Version: {diag["version"]}',
        f'Frozen: {diag["frozen"]}',
        f'DebugMode: {diag["debug_mode"]}',
        f'Executable: {diag["executable"]}',
        f'Platform: {diag["platform"]}',
        f'Python: {diag["python"]}',
        f'MEIPASS: {diag["meipass"]} (tcl_ready={diag["tcl_data_ready"]}, tk_ready={diag["tk_data_ready"]}, tkdnd_tcl9={diag["tkdnd_tcl9_ready"]})',
        f'Exception: {getattr(exc_type, "__name__", str(exc_type))}: {exc_value}',
    ]
    if trace:
        lines.extend(['', 'Traceback:', trace.rstrip()])
    lines.append('=' * 45 + '\n')
    return '\n'.join(lines)


def write_crash_report(exc_type, exc_value, exc_tb, *, stage: str = 'uncaught',
                       log_dir: str | None = None) -> str | None:
    """Append a structured human-readable diagnostic report for fatal/uncaught errors."""
    if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
        return None
    target_dir = log_dir or resolve_log_dir()
    report_text = build_crash_report_text(exc_type, exc_value, exc_tb, stage=stage)

    candidates = [
        os.path.join(target_dir, CRASH_REPORT_FILENAME),
        os.path.join(tempfile.gettempdir(), f'{APP_NAME}_{CRASH_REPORT_FILENAME}'),
    ]
    for path in candidates:
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(report_text)
            return path
        except OSError:
            continue
    return None


def daily_log_name(day: str | None = None) -> str:
    """The file a day's log goes to, today's by default. `pyi_rth_boot` writes the same name."""
    return f'translator_{day or datetime.now().strftime("%Y%m%d")}.jsonl'


def daily_logs(log_dir: str, rotated: bool = False) -> list[str]:
    """The daily log files in `log_dir`, oldest first: by day, a day's `.log` (written before the
    rename) before its `.jsonl`, and with `rotated` each file's rotated siblings, `.3` first, before it."""
    try:
        names = os.listdir(log_dir)
    except OSError:
        return []
    found = []
    for name in names:
        matched = _LOG_NAME_RE.match(name)
        if matched and (rotated or matched.group(3) is None):
            day, kind, number = matched.groups()
            found.append(((day, kind == 'jsonl', -int(number or 0)), os.path.join(log_dir, name)))
    return [path for _, path in sorted(found)]


def prune_old_logs(log_dir: str, retention_days: int = LOG_RETENTION_DAYS) -> int:
    """Delete daily logs older than `retention_days`. Returns how many went.

    Only files this module writes are considered - the name pattern is matched
    exactly, so nothing else that happens to sit in the directory is touched.
    """
    cutoff = time.time() - retention_days * 86400
    removed = 0

    for path in daily_logs(log_dir, rotated=True):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass  # in use by another instance, or already gone

    return removed


def setup_logging(debug: bool | None = None):
    """Setup logging to file and console for crash debugging."""
    log_dir = resolve_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    prune_old_logs(log_dir)

    debug_active = is_debug_mode() if debug is None else bool(debug)
    setattr(sys, '_crosstrans_debug', debug_active)
    if '--debug' in sys.argv[1:]:
        attach_debug_console()

    log_file = os.path.join(log_dir, daily_log_name())

    # delay=True: no empty file for a run that never logs, and nothing to leak
    # when a caller builds the handler without installing it.
    #
    # Deliberately NOT buffered, though Rule 9 suggests it. Every line this app
    # most needs survives an ending that skips cleanup: the updater exits with
    # os._exit(0) (see CLAUDE.md), the crash hook writes as the process dies, and
    # the already-running watchdog force-exits. A buffer would drop exactly those.
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8', delay=True)
    file_handler.setFormatter(JsonLinesFormatter())

    console_handler = _SafeConsoleHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

    level = logging.DEBUG if debug_active else logging.INFO
    logging.basicConfig(level=level, handlers=[file_handler, console_handler], force=True)

    def exception_handler(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.critical("Uncaught exception:", exc_info=(exc_type, exc_value, exc_tb))
        logging.critical("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        write_crash_report(exc_type, exc_value, exc_tb, stage='uncaught', log_dir=log_dir)

    def thread_exception_handler(args):
        if issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            return
        thread_name = getattr(args.thread, 'name', 'unknown')
        logging.critical(f"[Thread] Uncaught exception in {thread_name}:",
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        write_crash_report(args.exc_type, args.exc_value, args.exc_traceback,
                           stage=f'thread:{thread_name}', log_dir=log_dir)

    sys.excepthook = exception_handler
    threading.excepthook = thread_exception_handler
    logging.info(f"CrossTrans v{VERSION} started")
    logging.info(f"Log file: {log_file}")
    diag = collect_runtime_diagnostics()
    logging.info(
        f"[Startup] platform={diag['platform']} python={diag['python']} "
        f"frozen={diag['frozen']} debug={debug_active} "
        f"tcl_ready={diag['tcl_data_ready']} tk_ready={diag['tk_data_ready']} "
        f"tkdnd_tcl9={diag['tkdnd_tcl9_ready']}"
    )
    boot_events = getattr(sys, '_crosstrans_boot_events', None)
    if isinstance(boot_events, list) and boot_events:
        for item in list(boot_events):
            msg = item.get('msg', '')
            if item.get('level') == 'ERROR':
                logging.error(msg)
            else:
                logging.warning(msg)
        boot_events.clear()

    return log_file


def log_tk_callback_exception(exc_type, exc_value, exc_tb):
    """Windowed EXEs have no stderr; preserve asynchronous UI failures in the profile log."""
    if issubclass(exc_type, KeyboardInterrupt):
        raise exc_value
    logging.error('[Tk] UI callback failed', exc_info=(exc_type, exc_value, exc_tb))

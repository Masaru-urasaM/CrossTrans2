#!/usr/bin/env python3
"""
CrossTrans - Main Entry Point

This is the modular entry point for CrossTrans.
It initializes the application using the refactored module structure.

For backward compatibility, you can still run translator.py directly.
"""
import sys
import os
import threading

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import messagebox

from src.utils.logging_setup import build_crash_report_text, setup_logging, write_crash_report
from src.utils.single_instance import (
    is_already_running,
    signal_running_instance,
    serve_show_requests,
)

# A second instance has one notice to deliver and must never outlive it. It used
# to: ttkbootstrap's Messagebox builds its dialog with transient=<the root>, and
# Tk withdraws a transient window whose master is withdrawn - so show() waited in
# wait_window() on a dialog nobody could see or close. Measured 2026-09-07 with
# py-spy, on a process that had been resident since 2026-08-19.
ALREADY_RUNNING_TIMEOUT_S = 60.0


def _warn_already_running():
    """Tell the user CrossTrans is already up, then leave - whatever happens.

    The stdlib messagebox is a native Windows dialog, so a withdrawn root cannot
    hide it (verified: window class #32770, visible). The watchdog is the second
    line of defence: no trouble here can ever strand a process again.
    """
    watchdog = threading.Timer(ALREADY_RUNNING_TIMEOUT_S, lambda: os._exit(0))
    watchdog.daemon = True
    watchdog.start()
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "CrossTrans",
            "CrossTrans is already running!\n\n"
            "Check the system tray (bottom-right corner)."
        )
        root.destroy()
    finally:
        watchdog.cancel()


def _cli_flag_target(args, flag: str):
    if flag not in args:
        return False, None
    idx = args.index(flag)
    next_arg = args[idx + 1] if idx + 1 < len(args) and not str(args[idx + 1]).startswith('-') else None
    return True, next_arg


def _handle_cli_command(args) -> int | None:
    if len(args) >= 2 and args[0] == '--audio-self-check':
        from src.utils.audio_diagnostics import run
        return run(args[1])

    requested, target = _cli_flag_target(args, '--self-check')
    if requested:
        from src.utils.diagnostics import run_self_check
        return run_self_check(target)

    requested, target = _cli_flag_target(args, '--export-diagnostics')
    if requested:
        setup_logging()
        from config import Config
        from src.utils.diagnostics import export_diagnostic_bundle
        cfg = Config()
        bundle_path = export_diagnostic_bundle(target, config=cfg)
        print(str(bundle_path))
        return 0

    return None


def main(argv=None):
    """Main entry point for CrossTrans."""
    args = sys.argv[1:] if argv is None else list(argv)
    cli_exit = _handle_cli_command(args)
    if cli_exit is not None:
        return cli_exit

    # Enable DPI awareness for crisp UI on high-DPI displays (must be before any tkinter)
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        pass  # Older Windows versions don't have this API

    debug_flag = '--debug' in args
    lock_socket = None
    try:
        # Setup logging first
        if debug_flag:
            setup_logging(debug=True)
        else:
            setup_logging()

        # Check single instance
        already_running, lock_socket = is_already_running()
        if already_running:
            # Somebody who launches CrossTrans twice wants the window, not a lecture
            # about the system tray. Ask the running instance for it; only fall back
            # to the notice when it does not answer - an older build still accepts
            # the connection but will never act on it.
            if signal_running_instance():
                return 0
            _warn_already_running()
            return 0

        # Import and run the app from modular structure
        import src.core  # noqa: F401
        from src.app import TranslatorApp
        app = TranslatorApp()
        if lock_socket:
            serve_show_requests(lock_socket, app.on_second_instance)
        app.run()
        return 0
    except Exception as exc:
        import logging
        import traceback
        logging.critical(f"Failed to start application: {exc}", exc_info=True)
        exc_info = sys.exc_info()
        report_path = write_crash_report(*exc_info, stage='startup')
        report_text = build_crash_report_text(*exc_info, stage='startup')
        traceback.print_exc()
        from src.utils.diagnostics import notify_fatal_error
        notify_fatal_error(
            'CrossTrans 2 - Startup Error',
            f'{type(exc).__name__}: {exc}',
            report_text=report_text,
            report_path=report_path,
        )
        return 1
    finally:
        if lock_socket:
            lock_socket.close()


if __name__ == "__main__":
    sys.exit(main())

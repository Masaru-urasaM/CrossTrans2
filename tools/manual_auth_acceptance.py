"""Interactive OS authentication acceptance with fake keys and memory-only state.

Run explicitly with --interactive on the user's desktop. Never run from a test,
gallery or unattended session. Credentials belong only in the application's
Windows password dialog or the OS Hello prompt, never in a terminal or report.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--password-fallback', action='store_true',
                        help='exercise the real password dialog directly, without a blank-password probe')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    parser.add_argument('--report', type=Path, default=ROOT/f'logs/manual-auth-{stamp}.json')
    args = parser.parse_args(argv)
    if not args.interactive:
        parser.error('An explicit --interactive session is required; no windows were opened.')
    if sys.platform != 'win32':
        parser.error('Windows is required.')
    if args.report.exists():
        parser.error('The report already exists. Choose a new report path.')

    import tkinter as tk
    import src.core  # noqa: F401  # Initialize the package before Config.
    from config import Config
    from src.core import auth
    from src.ui import components as ui
    from src.ui.theme import engine, manager, windows
    from tools import auth_acceptance_evidence as evidence

    class MemoryConfig(Config):
        def __init__(self):
            self._config = copy.deepcopy(Config.DEFAULT_CONFIG)
            self._config['api_keys'] = [dict(api_key=f'fixture-only-key-{i}', provider='Auto', model_name='')
                                        for i in range(2)]
            self.api_status_cache = {}
            self.runtime_capabilities = dict(vision=False, file=False)

        def save(self, secure=False):
            pass

        def get_api_keys(self):
            return [dict(row) for row in self._config['api_keys']]

        def set_api_keys(self, rows, secure=False):
            self._config['api_keys'] = [dict(row) for row in rows]

    root = tk.Tk()
    root.withdraw()
    windows.set_default_icon(root, str(ROOT/'src/assets/CrossTrans.ico'))
    root.title('CrossTrans2 - Interactive authentication acceptance')
    root.geometry('1100x850')
    cfg = MemoryConfig()
    theme = manager.ThemeManager(cfg)
    manager.install_manager(theme)
    eng = engine.install(root, theme)
    ui.install(root, theme, eng.style)
    from src.ui.components.dialog import fit_geometry, work_area
    width, height, x, y = fit_geometry(1100, 850, work_area(), None)
    root.geometry(f'{width}x{height}+{x}+{y}')
    if args.password_fallback:
        verifier = evidence.native_password_verifier
    else:
        def verifier(parent):
            return evidence.Verification(bool(auth.require_auth(parent)), method='normal-product-flow')
    view = evidence.AcceptanceView(root, cfg, verifier, native=True,
                                   mode='password-fallback' if args.password_fallback else 'normal-product-flow')

    def finish():
        report = view.recorder.report()
        report.update(date=datetime.now(timezone.utc).isoformat(),
                      python=sys.version.split()[0], tcl=root.tk.call('info', 'patchlevel'),
                      windows=platform.version(),
                      tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      recorder_sha256=hashlib.sha256(Path(evidence.__file__).read_bytes()).hexdigest())
        report['source_sha256'] = {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                                 for path in ('src/core/auth.py', 'src/core/windows_identity.py',
                                              'src/core/windows_hello.py', 'src/ui/settings/page_providers.py')}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
        view.close()
        root.destroy()

    ui.Button(view.footer, 'Save report and close', command=finish).pack(pady=4)
    root.protocol('WM_DELETE_WINDOW', finish)
    try:
        root.deiconify()
        root.mainloop()
    finally:
        view.close()
        ui.reset()
        engine.reset()
        manager.install_manager(None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

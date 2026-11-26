"""Private own-window lifecycle regression: no native input/sizing loop is entered."""
import ctypes
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    import quiet_session
    quiet_session.install()
    if not quiet_session.DESKTOP_NAME:
        raise RuntimeError('Private desktop required')
    import tkinter as tk
    from src.ui.native_resize import WindowsSizing, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE, GWL_STYLE
    from src.ui.popup_layout import WorkArea
    root = tk.Tk()
    root.withdraw()
    window = tk.Toplevel(root)
    window.overrideredirect(True)
    window.geometry('420x300+100+100')
    label = tk.Label(window, text='Private native lifecycle regression')
    label.pack()
    counts = dict(start=0, rect=0, end=0)
    errors = []
    root.report_callback_exception = lambda kind, value, tb: errors.append((kind.__name__, str(value)))
    def started(edge, rect, point):
        counts['start'] += 1
        label.configure(text='Tk callback owns native start')
    def resized(rect):
        counts['rect'] += 1
        label.configure(text='Tk callback owns native rectangle ' + str(rect))
    def ended():
        counts['end'] += 1
        label.configure(text='Tk callback owns native end')
        root.after(20, root.quit)
    item = WindowsSizing(window, rect=(100, 100, 420, 300), minimum=(250, 120),
        bounds=lambda _x, _y: WorkArea(0, 0, 1920, 1040), on_rect=resized,
        on_start=started, on_end=ended, noactivate=True)
    item._api()
    state = dict(mapped=False)
    def enter():
        hwnd = state['hwnd']
        item._size_command = True  # Fixture intent only; never enter a native SC_SIZE loop.
        assert item.user.PostMessageW(hwnd, WM_ENTERSIZEMOVE, 0, 0)
        root.after(40, lambda: window.geometry('460x320+100+100'))
        root.after(80, lambda: window.attributes('-topmost', True))
        root.after(100, lambda: window.attributes('-topmost', False))
        root.after(140, lambda: item.user.PostMessageW(hwnd, WM_EXITSIZEMOVE, 0, 0))
    def mapped(event):
        if event.widget is not window or state['mapped']:
            return
        state.update(mapped=True, hwnd=int(window.wm_frame(), 16))
        state['style'] = item.user.GetWindowLongW(state['hwnd'], GWL_STYLE)
        assert item.attach()
        assert not item.enabled  # No real pointer sizing on the private desktop.
        root.after(40, enter)
    window.bind('<Map>', mapped, add='+')
    root.after(5000, root.quit)
    try:
        root.mainloop()
        assert counts['start'] >= 1 and counts['rect'] >= 1 and counts['end'] == 1, (counts, errors)
        assert not errors and not quiet_session.BLOCKED
        item.close()
        assert item.user.GetWindowLongW(state['hwnd'], GWL_STYLE) == state['style']
        print(json.dumps(dict(status='pass', counts=counts, private_desktop=True,
                              no_input_loop=True, pid=os.getpid())), flush=True)
    finally:
        item.close(restore=False)
        window.destroy()
        root.destroy()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

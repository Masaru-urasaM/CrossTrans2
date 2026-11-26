"""An update offer that never grabs input or takes keyboard focus."""
import tkinter as tk

from src.ui import components as ui
from src.ui.theme import windows
from src.ui.theme.compat import NO_AUTOSTYLE


class UpdateNotice:
    def __init__(self, root, version, on_action):
        ctx = ui.get()
        p = ctx.palette
        # A card like the popup: a hairline ring on the surface colour, square on Windows 10 and
        # rounded by DWM on Windows 11 once it is mapped.
        self.window = tk.Toplevel(root, bg=p.surface, highlightthickness=ctx.px(1),
                                  highlightbackground=p.border, highlightcolor=p.border, **NO_AUTOSTYLE)
        self.window.withdraw()
        self.window.title('CrossTrans update')
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self.window.protocol('WM_DELETE_WINDOW', lambda: self.choose('later'))
        self.window.bind('<Map>', lambda event: (
            windows.style_floating(self.window, p) if event.widget is self.window else None), add='+')
        self._on_action = on_action
        panel = ui.Frame(self.window, on='surface', padding=ctx.px(18))
        panel.pack(fill='both', expand=True)
        ui.Label(panel, text='Update available', kind='strong').pack(anchor='w')
        ui.Label(panel, text=f'CrossTrans v{version} is ready to download.',
                 wraplength=ctx.px(340)).pack(anchor='w', pady=(ctx.px(8), ctx.px(4)))
        ui.Label(panel, text='Your work stays open until you choose to restart.',
                 kind='muted', wraplength=ctx.px(340)).pack(anchor='w', pady=(0, ctx.px(14)))
        buttons = ui.Frame(panel, on='surface')
        buttons.pack(fill='x')
        self.buttons = {}
        for label, action, kind, icon in [('Update', 'update', 'accent', 'download'),
                                          ('Later', 'later', 'soft', None),
                                          ('Skip this version', 'skip', 'subtle', None)]:
            button = ui.Button(buttons, label, command=lambda a=action: self.choose(a), kind=kind,
                               icon=icon)
            button.pack(side='left', padx=(0, ctx.px(8)))
            self.buttons[action] = button
        self.window.bind('<Escape>', lambda _event: self.choose('later'))
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        left, top, right, bottom = self._work_area(root)
        x = max(left, right - width - 20)
        y = max(top, bottom - height - 20)
        self.window.geometry(f'{width}x{height}{x:+d}{y:+d}')
        self._prevent_activation()
        self.window.deiconify()

    def _prevent_activation(self):
        """Mapping a Toplevel can activate it on Windows even without focus_force."""
        try:
            import win32con
            import win32gui
            client = self.window.winfo_id()
            hwnd = win32gui.GetParent(client) or client
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                  style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
        except (ImportError, OSError):
            pass

    @staticmethod
    def _work_area(root):
        try:
            import win32api
            monitor = win32api.MonitorFromPoint(root.winfo_pointerxy(), 2)
            return win32api.GetMonitorInfo(monitor)['Work']
        except Exception:
            return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 60

    def choose(self, action):
        callback, self._on_action = self._on_action, None
        self.close()
        if callback:
            callback(action)

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()

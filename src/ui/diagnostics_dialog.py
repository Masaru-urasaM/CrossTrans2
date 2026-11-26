"""Themed diagnostics viewer and one-click bug-report exporter."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
import webbrowser

from src.ui import components as ui
from src.ui.components import context as C
from src.ui.i18n import tr
from src.ui.ruby_text import NO_AUTOSTYLE
from src.utils import diagnostics


class DiagnosticsDialog:
    """Interactive diagnostics & bug-report dialog hosted on ``ui.Dialog``."""

    def __init__(self, parent, config=None, shortcut_status: dict | None = None):
        self.parent = parent
        self.config = config
        self.shortcut_status = dict(shortcut_status or {})
        self.report_text = ''
        self._stop_theme = None

        self.view = ui.Dialog(
            parent,
            'Diagnostics and bug report',
            'Review system details and recent logs. API keys, tokens, and translation history are excluded.',
            icon='bug',
            min_width=560,
        )
        self.window = self.view.window
        self._build_contents()
        self.refresh()

        self.view.add_button('Close')
        self.view.add_button('Report on GitHub', self.report_on_github, kind='soft', close=False)
        self.view.add_button('Export bundle', self.export_bundle, kind='soft', close=False)
        self.view.add_button('Copy report', self.copy_report, kind='accent', default=True, close=False)

        ctx = C.get()
        self._stop_theme = ctx.manager.subscribe(self._apply_text_palette)
        self.window.bind('<Destroy>', self._on_destroy, add='+')
        self.view.show()

    def _build_contents(self) -> None:
        ctx = C.get()
        px = ctx.px
        body = self.view.body

        meta_card = ui.Card(body, on='bg', padding=(12, 8))
        meta_card.pack(fill='x', pady=(0, px(8)))
        meta_top = ui.Frame(meta_card, on=meta_card.plane)
        meta_top.pack(fill='x')
        self.health_label = ui.Label(meta_top, '', kind='strong', on=meta_card.plane, localize=False,
                                     justify='left', wraplength=px(520))
        self.health_label.pack(anchor='w', fill='x')

        toolbar = ui.Frame(meta_card, on=meta_card.plane)
        toolbar.pack(fill='x', pady=(px(6), 0))
        self.refresh_btn = ui.Button(toolbar, 'Refresh', command=self.refresh,
                                     kind='subtle', icon='arrow-repeat', on=meta_card.plane)
        self.refresh_btn.pack(side='left')
        self.open_logs_btn = ui.Button(toolbar, 'Open logs folder', command=self.open_logs,
                                       kind='soft', icon='folder2-open', on=meta_card.plane)
        self.open_logs_btn.pack(side='left', padx=(px(8), 0))

        self.preview_card = ui.Card(body, on='bg', padding=6)
        self.preview_card.pack(fill='both', expand=True)
        self.preview_card.columnconfigure(0, weight=1)
        self.preview_card.rowconfigure(0, weight=1)

        text_rows = 7 if ctx.scale >= 1.35 else 11
        self.text = tk.Text(
            self.preview_card,
            width=66,
            height=text_rows,
            wrap='none',
            relief='flat',
            borderwidth=0,
            highlightthickness=0,
            padx=px(8),
            pady=px(6),
            font=ctx.font('mono'),
            **NO_AUTOSTYLE,
        )
        self.text.grid(row=0, column=0, sticky='nsew')
        vbar = ui.ThinScrollbar(self.preview_card, orient='vertical', command=self.text.yview)
        vbar.grid(row=0, column=1, sticky='ns', padx=(px(4), 0))
        hbar = ui.ThinScrollbar(self.preview_card, orient='horizontal', command=self.text.xview)
        hbar.grid(row=1, column=0, sticky='ew', pady=(px(4), 0))
        self.text.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        self.status_label = ui.Label(body, '', kind='caption', on='bg', localize=False,
                                     justify='left', wraplength=px(520))
        self.status_label.pack(anchor='w', fill='x', pady=(px(6), 0))
        self._apply_text_palette(ctx.manager)

    def _apply_text_palette(self, manager) -> None:
        p = manager.palette
        try:
            self.text.configure(
                bg=p.surface,
                fg=p.text,
                insertbackground=p.text,
                selectbackground=p.selection,
                selectforeground=p.selection_text,
                font=C.get().font('mono'),
            )
        except tk.TclError:
            pass

    def refresh(self) -> str:
        env = diagnostics.collect_environment_report(
            config=self.config,
            shortcut_status=self.shortcut_status,
        )
        rt = env['runtime']
        crash_note = tr('Crash log detected') if env['logs']['crash_report_exists'] else tr('No crash reports recorded')
        summary_line = (
            f"v{env['version']}  |  Python {rt['python']}  |  "
            f"Tcl/Tk {env['tcl_version']}/{env['tk_version']}  |  "
            f"TkDnD {env['dependencies'].get('tkinterdnd2_Version', 'unknown')}  |  "
            f"{crash_note}"
        )
        self.health_label.configure(text=summary_line)
        self.report_text = diagnostics.format_diagnostic_report(
            config=self.config,
            shortcut_status=self.shortcut_status,
        )
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', self.report_text)
        self.text.configure(state='disabled')
        return self.report_text

    def copy_report(self) -> None:
        text = self.report_text or self.refresh()
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.status_label.configure(text=tr('Copied diagnostic report to clipboard.'))

    def export_bundle(self) -> str | None:
        chosen = filedialog.asksaveasfilename(
            parent=self.window,
            title=tr('Save diagnostic bundle'),
            defaultextension='.zip',
            initialfile=diagnostics.default_bundle_filename(),
            filetypes=[(tr('Zip archive'), '*.zip')],
        )
        if not chosen:
            return None
        path = diagnostics.export_diagnostic_bundle(
            chosen,
            config=self.config,
            shortcut_status=self.shortcut_status,
        )
        self.status_label.configure(text=tr('Saved bundle: {0}', str(path)))
        return str(path)

    def open_logs(self) -> None:
        try:
            opened = diagnostics.open_logs_folder()
            self.status_label.configure(text=tr('Opened folder: {0}', str(opened)))
        except Exception as exc:
            self.status_label.configure(text=tr('Could not open logs folder: {0}', exc))

    def report_on_github(self) -> None:
        text = self.report_text or self.refresh()
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        url = diagnostics.build_github_issue_url(text)
        webbrowser.open(url)
        self.status_label.configure(text=tr('Opened GitHub and copied full report to clipboard.'))

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self.window:
            if self._stop_theme is not None:
                self._stop_theme()
                self._stop_theme = None

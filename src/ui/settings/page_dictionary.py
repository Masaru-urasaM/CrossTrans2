"""
Dictionary (TODO.md U8, step 5, Decision 51): language packs and the furigana reader.

The old tab was 1,000 lines in one mixin: it asked every pack whether it was installed on the UI thread
(the page took four seconds to open), animated a progress bar and a button's dots with `after` timers, and
called `window.after()` from its install threads. Here the page opens at once and a worker finds out which
packs are installed; installing or removing runs on a worker too, whose progress and result the UI thread
collects from a queue (`BackgroundRunner`, Decision 43). Which packs show, what a failure means and what each
sentence says are pure functions of `core/pack_catalog.py`.

Installed packs and the others are two groups of rows - an Installed chip and a Remove button, or an Install
button - under a search field. Bulk actions are visible buttons and ask first.
"""
import logging
import tkinter as tk
from tkinter import font as tkfont
from typing import Dict, List, Optional

from src.core import pack_catalog as catalog
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.settings.background import BackgroundRunner
from src.ui.settings.page import SettingsPage

SEARCH_DELAY_MS = 150               # typing pauses this long before the lists are rebuilt
COLLAPSED_ROWS = 6                  # available packs shown until "Show all" (a search shows every match)
CHECKING = 'Checking which packs are installed...'
SCAN_FAILED = 'Could not check which packs are installed.'
UNAVAILABLE = 'Language packs are not available. Look in the log for details.'
WORK_FAILED = 'Something went wrong. Look in the log for details.'
LEFT_QUOTE, RIGHT_QUOTE = chr(0x201C), chr(0x201D)


def load_nlp():
    """The language pack manager and the catalogue. Imported here: the module sets up the packages path
    and a failure to import it must reach the page, not stop the Settings window from opening."""
    from src.core.nlp_manager import LANGUAGE_PACKS, nlp_manager
    return nlp_manager, LANGUAGE_PACKS


def reader_is_accurate() -> bool:
    from src.core import furigana
    return furigana.active_provider_name() == 'fugashi'


class DictionaryPage(SettingsPage):
    key = 'dictionary'

    def populate(self) -> None:
        ctx = C.get()
        px = ctx.px
        self.runner = BackgroundRunner(self.body)
        self.installed: List[str] = []
        self.scanned = False
        self.busy = False
        self.expanded = False
        self.accurate = False
        self.dialog: Optional[ui.Dialog] = None
        self.progress: Optional[ui.Frame] = None
        self.notice: Optional[ui.InfoBar] = None
        self.pack_rows: Dict[str, ui.SettingRow] = {}
        self.install_buttons: Dict[str, ui.Button] = {}
        self.remove_buttons: Dict[str, ui.IconButton] = {}
        self._search_job: Optional[str] = None
        self._built = False
        try:
            self.manager, self.packs = load_nlp()
            self.manager.set_config(self.config)
        except Exception:
            logging.exception("[Dictionary] the language pack manager could not be loaded")
            self.manager, self.packs = None, {}

        group = self.group()
        row = self.row(group, 'language_packs', stacked=True)
        bar = ui.ActionBar(row.control, on=row.plane)
        bar.pack(fill='x')
        self.install_all_btn = bar.add(ui.Button(bar, 'Install all packs', self.confirm_install_all,
                                               icon='cloud-download', on=row.plane))
        self.remove_all_btn = bar.add(ui.Button(bar, 'Delete all packs', self.confirm_remove_all,
                                              icon='trash', kind='danger', on=row.plane))
        self.summary_label = ui.Label(row.control, '', kind='muted', on=row.plane)

        self.status_box = ui.Frame(self.body, on='bg')          # packed only while it holds something
        self.query = tk.StringVar(master=self.body)
        self.search = ui.TextField(self.body, placeholder='Search languages', textvariable=self.query,
                                   on='bg')
        self.search.pack(fill='x', pady=(0, px(12)))
        self.query.trace_add('write', self._query_changed)
        self.lists_box = ui.Frame(self.body, on='bg')
        self.lists_box.pack(fill='x')
        self.body.bind('<Destroy>', self._on_destroy, add='+')
        self._built = True
        if self.manager is None:
            self.summary_label.configure(text='')
            self.show_notice(catalog.DANGER, UNAVAILABLE)
        else:
            self._scan()
        self._apply_busy()

    def _scan(self) -> None:
        """Ask the manager which packs are installed, off the UI thread (it imports each pack's modules)."""
        self.busy = True
        self.summary_label.configure(text=CHECKING)
        self.summary_label.pack(anchor='w')
        self._apply_busy()
        manager = self.manager

        def work(progress, cancelled):
            return list(manager.get_installed_languages())

        self.runner.start('scan', work, on_done=self._scanned)

    def _scanned(self, result, error) -> None:
        self.busy = False
        if error is not None:
            self.show_notice(catalog.DANGER, SCAN_FAILED, action='Try again', on_action=self._scan)
            self.summary_label.configure(text='')
            self._apply_busy()
            return
        self.installed = result
        self.scanned = True
        self.summary_label.configure(text='')
        self.summary_label.pack_forget()
        self._rebuild_lists()

    def _query_changed(self, *args) -> None:
        if self._search_job is not None:
            try:
                self.body.after_cancel(self._search_job)
            except (tk.TclError, ValueError):
                pass
        self._search_job = self.body.after(SEARCH_DELAY_MS, self._search_pause)

    def _search_pause(self) -> None:
        self._search_job = None
        self._rebuild_lists()

    def _rebuild_lists(self) -> None:
        """Draw the Installed and Available groups for what is installed now and what the search says."""
        if not self.scanned:
            return
        px = C.get().px
        for child in self.lists_box.winfo_children():
            child.destroy()
        self.groups = [group for group in self.groups if group.winfo_exists()]
        self.pack_rows.clear()
        self.install_buttons.clear()
        self.remove_buttons.clear()
        query = self.query.get().strip()
        installed, available = catalog.split(self.packs, self.installed, query)
        group = self.group('Installed', parent=self.lists_box)
        self.installed_heading = group.heading_label
        heading = ui.Frame(group)
        heading.pack(fill='x', before=group.card)
        self.installed_heading.destroy()
        self.installed_heading = ui.Label(heading, 'Installed', kind='strong')
        group.heading_label = self.installed_heading
        self.installed_heading.pack(side='left')
        summary = ui.Label(heading, (catalog.summary(self.installed, self.packs) if self.installed else '0 packs, about 0 MB.'), kind='caption')
        summary.pack(side='left', padx=px(8))
        self.installed_summary = summary
        title = self.installed_heading
        state = [None]

        def fit_heading(event=None):
            width = heading.winfo_width()
            if width <= 1:
                return
            font = tkfont.Font(root=heading, font=C.get().font('caption'))
            stacked = width < title.winfo_reqwidth() + font.measure(str(summary.cget('text'))) + px(8)
            if stacked != state[0]:
                state[0] = stacked
                title.pack_forget()
                summary.pack_forget()
                if stacked:
                    title.pack(anchor='w')
                    summary.pack(fill='x', anchor='w', pady=(px(4), 0))
                else:
                    title.pack(side='left')
                    summary.pack(side='left', padx=(px(8), 0))
            room = max(1, width - (0 if stacked else title.winfo_reqwidth() + px(8)))
            if abs(float(summary.cget('wraplength') or 0) - room) > 2:
                summary.configure(wraplength=room)

        heading.bind('<Configure>', fit_heading, add='+')
        summary.bind('<Configure>', fit_heading, add='+')
        for name in installed:
            self._installed_row(group, name)
        if available:
            shown = available if query or self.expanded else available[:COLLAPSED_ROWS]
            hidden = len(available) - len(shown)
            group = self.group('Available', parent=self.lists_box, gap=8 if hidden else 24)
            for name in shown:
                self._available_row(group, name)
            if hidden:
                self.expand_btn = ui.Button(self.lists_box, f'Show all {len(available)} languages',
                                            command=self.expand, kind='subtle', on='bg',
                                            icon='chevron-down', icon_side='right')
                self.expand_btn.pack(anchor='w', pady=(0, px(24)))
        if not installed and not available:
            ui.Label(self.lists_box, f'No language matches {LEFT_QUOTE}{query}{RIGHT_QUOTE}.', kind='muted',
                     on='bg').pack(anchor='w', pady=(0, px(24)))
        self._apply_busy()
        if self._built:
            self.adopt()

    def _installed_row(self, group: ui.SettingGroup, name: str) -> None:
        px = C.get().px
        row = group.row(name, catalog.describe(self.packs[name]))
        ui.Chip(row.control, 'Installed', tone='soft', on=row.plane).pack(side='left')
        remove = ui.Button(row.control, 'Remove', command=lambda language=name: self.confirm_remove(language),
                           icon='trash', on=row.plane)
        remove.pack(side='left', padx=(px(8), 0))
        self.pack_rows[name], self.remove_buttons[name] = row, remove

    def _available_row(self, group: ui.SettingGroup, name: str) -> None:
        row = group.row(name, catalog.describe(self.packs[name]))
        button = ui.Button(row.control, 'Install', command=lambda language=name: self.install(language),
                           kind='soft', on=row.plane)
        button.pack()
        self.pack_rows[name], self.install_buttons[name] = row, button

    def expand(self) -> None:
        self.expanded = True
        self._rebuild_lists()

    def _apply_busy(self) -> None:
        state = 'disabled' if self.busy else 'normal'
        for button in (*self.install_buttons.values(), *self.remove_buttons.values()):
            button.configure(state=state)
        idle = self.scanned and not self.busy and self.manager is not None
        self.install_all_btn.configure(state='normal' if idle and self._available() else 'disabled')
        self.remove_all_btn.configure(state='normal' if idle and self.installed else 'disabled')

    # --- the status area: progress and the last result -----------------------------------------------------------------
    def _sync_status(self) -> None:
        if self.status_box.winfo_children():
            self.status_box.pack(fill='x', pady=(0, C.get().px(12)), before=self.search)
        else:
            self.status_box.pack_forget()

    def show_notice(self, kind: str, message: str, action: str = '', on_action=None) -> None:
        self.clear_notice()
        self.notice = ui.InfoBar(self.status_box, message, kind=kind, action=action, on_action=on_action,
                                 closable=True, on_close=self.clear_notice, on='bg')
        self.notice.pack(fill='x')
        self._sync_status()
        if self._built:
            self.adopt()

    def clear_notice(self) -> None:
        notice, self.notice = self.notice, None
        if notice is not None:
            try:
                notice.destroy()
            except tk.TclError:
                pass
        self._sync_status()

    def _show_progress(self, title: str) -> None:
        px = C.get().px
        self._hide_progress()
        self.progress = ui.Frame(self.status_box, on='bg')
        self.progress_title = ui.Label(self.progress, title, kind='strong', on='bg')
        self.progress_title.pack(anchor='w')
        self.progress_detail = ui.Label(self.progress, '', kind='muted', on='bg')
        self.progress_detail.pack(anchor='w', pady=(px(2), px(6)))
        self.progress.bind('<Configure>', lambda event: self._wrap(self.progress_title, self.progress), add='+')
        self.progress_bar = ui.Progress(self.progress, mode='indeterminate')
        self.progress_bar.pack(fill='x')
        self.progress_bar.start(12)
        self.progress.pack(fill='x', pady=(0, px(8)))
        self._sync_status()

    def _hide_progress(self) -> None:
        progress, self.progress = self.progress, None
        if progress is not None:
            try:
                self.progress_bar.stop()
                progress.destroy()
            except tk.TclError:
                pass
        self._sync_status()

    # --- installing and removing ------------------------------------------------------------------------------------------
    def install(self, language: str) -> None:
        self._start(catalog.INSTALL, [language])

    def _start(self, action: str, languages: List[str]) -> None:
        """Run `action` on `languages` one after another on a worker. One run at a time: every button
        waits until it is over and the packs have been looked at again."""
        if self.busy or not languages or self.manager is None:
            return
        self.busy = True
        self._apply_busy()
        self.clear_notice()
        self._show_progress(catalog.progress_title(action, 1, len(languages), languages[0]))
        manager = self.manager

        def work(progress, cancelled):
            return catalog.run_batch(manager, action, languages, progress=progress, cancelled=cancelled)

        self.runner.start('op', work,
                          on_progress=lambda *step: self._op_progress(action, *step),
                          on_done=lambda outcomes, error: self._op_done(action, languages, outcomes, error))

    def _op_progress(self, action: str, index: int, total: int, language: str, message: str,
                     percent: int) -> None:
        self.progress_title.configure(text=catalog.progress_title(action, index, total, language))
        self.progress_detail.configure(text=message.rstrip('.'))

    def _op_done(self, action: str, languages: List[str], outcomes, error) -> None:
        self._hide_progress()
        if error is not None:
            self.show_notice(catalog.DANGER, WORK_FAILED)
        else:
            kind, message = catalog.result_notice(action, outcomes, requested=len(languages))
            self.show_notice(kind, message)
            logging.info(f"[Dictionary] {action}: {sum(1 for o in outcomes if o.ok)} of {len(languages)} done")
        self._scan()

    # --- questions asked first ------------------------------------------------------------------------------------------------
    def _available(self) -> List[str]:
        return [name for name in sorted(self.packs, key=lambda n: (n.lower(), n)) if name not in self.installed]

    def confirm_remove(self, language: str) -> ui.Dialog:
        return self._confirm(catalog.REMOVE, [language], 'Remove')

    def confirm_remove_all(self) -> ui.Dialog:
        return self._confirm(catalog.REMOVE, list(self.installed), 'Remove all')

    def confirm_install_all(self) -> ui.Dialog:
        return self._confirm(catalog.INSTALL, self._available(), 'Install all')

    def _confirm(self, action: str, languages: List[str], verb: str) -> ui.Dialog:
        """Ask before installing or removing. Removing keeps Cancel as the default button, so a stray Enter
        leaves the packs alone; installing is the default."""
        title, message = catalog.confirm_text(action, languages, self.packs)
        dialog = ui.Dialog(self.host.window, title, message,
                           icon='trash' if action == catalog.REMOVE else 'cloud-download')
        run = lambda: self._start(action, languages)                # noqa: E731
        if action == catalog.INSTALL:
            dialog.add_button(verb, command=run, default=True)
            dialog.add_button('Cancel')
        else:
            dialog.add_button('Cancel', default=True)
            dialog.add_button(verb, command=run, kind='danger')
        dialog.show()
        self.dialog = dialog
        return dialog

    # --- visible bulk actions ------------------------------------------------------------------------------------------------------
    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.body:
            return
        if self._search_job is not None:
            try:
                self.body.after_cancel(self._search_job)
            except (tk.TclError, ValueError):
                pass
            self._search_job = None

"""
The Settings window (TODO.md U8, Decision 51).

Five tabs organised by how the code was written became seven pages organised by what a person wants to
do, with a search field over every setting (`settings_index`). On the left the search and the page list,
on the right the page: its title, a quiet "Saved" that appears after a change (there is no Save button -
every change is stored at once, as before), and the page itself, built the first time it is opened.

The window owns no setting. A page reads and writes `config` and calls `changed(key)`; the application
answers that (`on_changed`) by registering the hotkeys again, refreshing the tray, and so on. While a
shortcut is being recorded `set_recording()` tells the application to stop answering its hotkeys.

`open_tab()` keeps the application's old tab names working. The update check and download are
`UpdateManagerMixin`'s; the update window is rebuilt on components with the other windows (U9).
"""
import logging
import tkinter as tk
from typing import Callable, Dict, Optional

from src.constants import (SETTINGS_DEFAULT_SIZE, SETTINGS_HIGHLIGHT_MS, SETTINGS_MIN_SIZE,
                           SETTINGS_NAV_WIDTH, SETTINGS_RESULTS_MAX,
                           SETTINGS_SAVED_MS)
from src.core import settings_index as index
from src.ui import components as ui
from src.ui import translator_layout as layout
from src.ui.components import context as C
from src.ui.components.dialog import work_area
from src.ui.settings.page import SettingsPage
from src.ui.settings.page_about import AboutPage
from src.ui.settings.page_dictionary import DictionaryPage
from src.ui.settings.page_help import HelpPage
from src.ui.settings.page_providers import ProvidersPage
from src.ui.settings.page_shortcuts import ShortcutsPage
from src.ui.settings.pages_basic import AppearancePage, GeneralPage, TranslationPage
from src.ui.settings.update_manager import UpdateManagerMixin
from src.ui.theme import engine
from src.utils.updates import AutoUpdater

RESULTS = '*results'                # the search results' place among the page frames
SEARCH_DELAY_MS = 120               # typing pauses this long before the results are worked out
SIDE_PADDING = 32                   # at 100 %: a page's left and right margin


class SettingsWindow(UpdateManagerMixin):
    """The Settings window: navigation, search, and the pages."""

    PAGES = (GeneralPage, AppearancePage, TranslationPage, ShortcutsPage, ProvidersPage, DictionaryPage,
             HelpPage, AboutPage)

    def __init__(self, parent, config, on_changed: Optional[Callable] = None, on_api_change_callback=None,
                 update_manager=None, is_translation_busy=None, *,
                 on_shortcut_recording: Optional[Callable] = None,
                 shortcut_status: Optional[Callable] = None, key_source_factory: Optional[Callable] = None,
                 on_show_welcome: Optional[Callable] = None, container=None, on_close=None):
        """
        Args:
            parent: the application's root window
            config: where every setting is read and written
            on_changed: `on_changed(key)` after a page stored a change (a `settings_index` key)
            on_api_change_callback: after the API keys changed (trial mode, tray)
            on_shortcut_recording: `f(active)` while a shortcut is being recorded
            shortcut_status: `f()` -> {owner: 'ok' | 'taken' | 'invalid' | 'failed'}, how the hotkeys registered
            key_source_factory: a test's stand-in for the keyboard hook
        """
        self.root = parent
        self.container = container
        self.on_close = on_close
        self.config = config
        self.on_changed = on_changed
        self.on_show_welcome = on_show_welcome
        self.on_api_change_callback = on_api_change_callback
        self.on_shortcut_recording = on_shortcut_recording
        self.shortcut_status = shortcut_status or (lambda: {})
        self.key_source_factory = key_source_factory
        self.recording = False
        self.updater = AutoUpdater()
        self.is_translation_busy = is_translation_busy or (lambda: False)
        self._download_active = False
        self._download_poll_id = None
        self._owns_update_manager = update_manager is None
        if update_manager is None:
            from src.core.update_ui_manager import UpdateUIManager
            from src.ui.toast import ToastManager
            update_manager = UpdateUIManager(parent, config, ToastManager(parent))
            update_manager.configure_callbacks(
                on_state_changed=self.refresh_update_state, on_open_update=self.offer_update,
                is_busy=self.is_translation_busy)
        self.update_manager = update_manager

        self.pages: Dict[str, SettingsPage] = {}
        self._frames: Dict[str, tk.Misc] = {}
        self._areas: Dict[str, ui.ScrollArea] = {}
        self._extra: Dict[str, int] = {}
        self.current: Optional[str] = None
        self._results = None
        self._highlighted = None
        self._saved_job = self._search_job = self._highlight_job = None
        self._unsubscribe = None

        try:
            self._build()
        except Exception:
            logging.exception("[Settings] could not build the Settings window")
            raise
        self.window.bind('<Destroy>', self._close_update_work, add='+')
        self.window.bind('<Destroy>', self._on_destroy, add='+')
        self.open_page('general')
        if self.container is None:
            self.window.deiconify()

    # ------------------------------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------------------------------
    def _build(self) -> None:
        ctx = C.get()
        px = ctx.px
        if self.container is not None:
            self.window = w = ui.Frame(self.container, on='bg')
            w.pack(fill='both', expand=True)
        else:
            self.window = w = tk.Toplevel(self.root)
            w.withdraw()
            w.title('Settings - CrossTrans')
            w.sizes_from_work_area = True       # so a gallery judges the rule, not one screen's pixels
            engine.attach(w)
            area = work_area()
            size = layout.window_size(area, self.config.get_settings_window_size(), ctx.scale,
                                      default=SETTINGS_DEFAULT_SIZE, minimum=SETTINGS_MIN_SIZE)
            x, y = layout.window_position(size, area)
            w.geometry(f'{size.width}x{size.height}+{x}+{y}')
            w.minsize(size.min_width, size.min_height)
            w.protocol('WM_DELETE_WINDOW', self.close)
        self.frame = ui.Frame(w, on='bg')
        self.frame.pack(fill='both', expand=True)
        self.frame.columnconfigure(0, minsize=px(SETTINGS_NAV_WIDTH))
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self._build_navigation()
        self._build_content()
        w.bind('<Escape>', self._escape, add='+')

    def _build_navigation(self) -> None:
        px = C.get().px
        self.nav = ui.Frame(self.frame, on='alt', padding=(px(12), px(16), px(12), px(16)))
        self.nav.grid(row=0, column=0, sticky='nsew')
        self.search = ui.TextField(self.nav, placeholder='Search settings', on='alt')
        self.search.pack(fill='x', pady=(0, px(12)))
        for sequence in ('<KeyRelease>', '<<Paste>>', '<<Cut>>'):
            self.search.bind(sequence, self._search_typed, add='+')
        self.search.bind('<Return>', self._search_enter, add='+')
        self.search.bind('<Escape>', self._search_escape, add='+')
        self.navigation_scroll = ui.ScrollArea(self.nav, on='alt')
        self.navigation_scroll.pack(fill='both', expand=True)
        self.navigation = ui.NavigationView(
            self.navigation_scroll.content, [(page.key, page.title, page.icon) for page in index.PAGES], value=index.PAGES[0].key,
            on_select=self.open_page, on='alt')
        self.navigation.pack(fill='x')
        self.navigation_scroll.adopt()
        self.navigation.bind('<Configure>', self._fit_navigation, add='+')

    def _fit_navigation(self, event=None):
        width = self.navigation.winfo_reqwidth() + self.navigation_scroll.scrollbar.winfo_reqwidth()
        canvas = self.navigation_scroll.canvas
        if int(canvas.cget('width')) != width:
            canvas.configure(width=width)

    def _build_content(self) -> None:
        ctx = C.get()
        px = ctx.px
        self.content = ui.Frame(self.frame, on='bg')
        self.content.grid(row=0, column=1, sticky='nsew')
        header = ui.Frame(self.content, on='bg', padding=(px(SIDE_PADDING), px(24), px(SIDE_PADDING), px(8)))
        header.pack(fill='x')
        self.title = ui.Label(header, '', kind='title', on='bg')
        self.title.pack(side='left')
        self.saved = ui.Chip(header, 'Saved', tone='soft', on='bg', compound='left',
                             image=ctx.icon('check-lg', 14, ctx.palette.soft_text))
        header.bind('<Configure>', lambda event: self._fit_header_title(header), add='+')
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.body = ui.Frame(self.content, on='bg')
        self.body.pack(fill='both', expand=True)

    def _fit_header_title(self, header):
        room = header.winfo_width() - 2 * C.get().px(SIDE_PADDING)
        if self.saved.winfo_manager():
            room -= self.saved.winfo_reqwidth() + C.get().px(12)
        self.title.configure(wraplength=max(1, room))

    def _retheme(self, manager) -> None:
        try:
            self.saved.configure(image=C.get().icon('check-lg', 14, manager.palette.soft_text))
            if self._results is not None and self.search.get():
                self._run_search()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------------------------------
    def _ensure_page(self, key: str) -> SettingsPage:
        """The page `key`, built the first time it is asked for."""
        page = self.pages.get(key)
        if page is not None:
            return page
        px = C.get().px
        page = next(cls for cls in self.PAGES if cls.key == key)(self)
        if page.scrolls:
            frame = ui.ScrollArea(self.body, on='bg')
            self._areas[key] = frame
            parent = ui.Frame(frame.content, on='bg',
                              padding=(px(SIDE_PADDING), px(8), px(SIDE_PADDING), px(32)))
            parent.pack(fill='both', expand=True)
        else:
            frame = ui.Frame(self.body, on='bg', padding=(px(SIDE_PADDING), px(8), px(SIDE_PADDING), px(16)))
            parent = frame
        self._frames[key] = frame
        self.pages[key] = page
        try:
            page.build(parent)
        except Exception as error:
            logging.exception(f"[Settings] could not build the {key} page")
            ui.Label(parent, f'This page could not be shown: {error}', kind='muted', on='bg').pack(anchor='w')
            return page
        if page.scrolls:
            page.body.pack(fill='x')
            frame.bind('<Configure>', lambda event, p=page, f=frame: self._fit_page(p, f), add='+')
            frame.adopt()
        else:
            page.body.pack(fill='both', expand=True)
        return page

    def _fit_page(self, page: SettingsPage, frame) -> None:
        """Let every Settings page use the available content width."""
        extra = 0
        if page.body is not None and self._extra.get(page.key) != extra:
            self._extra[page.key] = extra
            page.body.pack_configure(padx=(0, extra))

    def _show(self, key: str) -> None:
        """Make the frame `key` the visible one."""
        for name, frame in self._frames.items():
            if name != key:
                frame.pack_forget()
        if self._results is not None and key != RESULTS:
            self._results.pack_forget()
        if self._frames[key].winfo_manager() != 'pack':
            self._frames[key].pack(fill='both', expand=True)

    def open_page(self, key: str, highlight: Optional[str] = None) -> bool:
        """Show a page - by its key, its title, or what the application called it before the
        redesign - and, with `highlight`, scroll to that setting's row and mark it for a moment."""
        resolved = key if any(cls.key == key for cls in self.PAGES) else index.page_for(key)
        if resolved is None:
            return False
        self._clear_highlight()
        if self.search.get() or self._results is not None:
            self._clear_search()
        if self.current == 'shortcuts' and resolved != 'shortcuts' and 'shortcuts' in self.pages:
            for editor in self.pages['shortcuts'].editors.values():
                editor.cancel_recording()
        elif self.current == 'providers' and resolved != 'providers' and 'providers' in self.pages:
            sec = getattr(self.pages['providers'], 'audio_section', None)
            if sec is not None and hasattr(sec, 'shortcut_editor'):
                sec.shortcut_editor.cancel_recording()
        page = self._ensure_page(resolved)
        if resolved == 'shortcuts' and hasattr(page, 'sync_hotkeys'):
            page.sync_hotkeys()
            page.refresh_status()
        elif resolved == 'providers' and hasattr(page, 'audio_section'):
            page.audio_section.refresh()
        self.current = resolved
        self._show(resolved)
        self.title.configure(text=index.page(resolved).title)
        if self.navigation.value != resolved:
            self.navigation.value = resolved
        if highlight:
            self._highlight_row(page, highlight)
        return True

    def open_tab(self, name: str, highlight: Optional[str] = None) -> bool:
        """What the application calls to open a page by a name (it still says "API Key")."""
        return self.open_page(name, highlight=highlight)

    def _highlight_row(self, page: SettingsPage, key: str) -> None:
        row = page.row_for(key)
        if row is None:
            return
        self.window.update_idletasks()
        area = self._areas.get(page.key)
        if area is not None:
            area.reveal(row, margin=C.get().px(24))
        row.highlight(True)
        self._highlighted = row
        self._highlight_job = self.window.after(SETTINGS_HIGHLIGHT_MS, self._clear_highlight)

    def _clear_highlight(self) -> None:
        if self._highlight_job is not None:
            try:
                self.window.after_cancel(self._highlight_job)
            except (tk.TclError, ValueError):
                pass
            self._highlight_job = None
        row, self._highlighted = self._highlighted, None
        if row is not None:
            try:
                row.highlight(False)
            except tk.TclError:
                pass

    def adopt(self, key: str) -> None:
        """Bind the wheel on what a page added after it was built."""
        area = self._areas.get(key)
        if area is not None:
            area.adopt()

    def reveal(self, key: str, widget) -> None:
        """Scroll page `key` so `widget` (something a page added) is in view."""
        area = self._areas.get(key)
        if area is not None:
            area.reveal(widget, margin=C.get().px(24))

    def api_changed(self) -> None:
        """The saved API keys or what they can do changed: tell the application (the translation service,
        the attachment area and the tray all depend on them)."""
        if self.on_api_change_callback is not None:
            try:
                self.on_api_change_callback()
            except Exception:
                logging.exception("[Settings] the application could not apply the API change")

    def rebuild_page(self, key: str) -> None:
        """Throw a page away and build it again (the Dictionary page after a pack was installed)."""
        frame = self._frames.pop(key, None)
        self.pages.pop(key, None)
        self._areas.pop(key, None)
        self._extra.pop(key, None)
        if frame is not None:
            frame.destroy()
        if self.current == key:
            self.open_page(key)

    def refresh_pages(self) -> None:
        """Every built page shows what the settings are now (after Restore defaults)."""
        for key, page in list(self.pages.items()):
            try:
                page.refresh()
            except Exception:
                logging.exception(f"[Settings] could not refresh the {key} page")

    def refresh_shortcut_status(self) -> None:
        """The hotkeys were registered again: show how each one went."""
        page = self.pages.get('shortcuts')
        if page is not None and page.body is not None:
            page.refresh_status()
        providers = self.pages.get('providers')
        if providers is not None and hasattr(providers, 'audio_section'):
            providers.audio_section.refresh_status()

    # ------------------------------------------------------------------------------------------
    # What pages and the application tell the window
    # ------------------------------------------------------------------------------------------
    def changed(self, key: str) -> None:
        """A page stored a change: say "Saved", then let the application act on it."""
        self._flash_saved()
        if self.on_changed is not None:
            try:
                self.on_changed(key)
            except Exception:
                logging.exception(f"[Settings] the application could not apply a change to {key}")

    def _flash_saved(self) -> None:
        if self._saved_job is not None:
            try:
                self.window.after_cancel(self._saved_job)
            except (tk.TclError, ValueError):
                pass
        self.saved.pack(side='right')
        self._saved_job = self.window.after(SETTINGS_SAVED_MS, self._hide_saved)

    def _hide_saved(self) -> None:
        self._saved_job = None
        try:
            self.saved.pack_forget()
        except tk.TclError:
            pass

    def set_recording(self, active: bool) -> None:
        """A shortcut editor started or ended a recording: tell the application (once per change)."""
        active = bool(active)
        if active == self.recording:
            return
        self.recording = active
        if self.on_shortcut_recording is not None:
            try:
                self.on_shortcut_recording(active)
            except Exception:
                logging.exception("[Settings] the application could not follow the recording")

    # ------------------------------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------------------------------
    def _search_typed(self, event=None) -> None:
        if self._search_job is not None:
            self.window.after_cancel(self._search_job)
        self._search_job = self.window.after(SEARCH_DELAY_MS, self._run_search)

    def _run_search(self) -> None:
        self._search_job = None
        query = self.search.get().strip()
        if query:
            self._show_results(query)
        else:
            self._leave_results()

    def _search_enter(self, event=None) -> str:
        """Enter opens the best result."""
        if self._search_job is not None:
            self.window.after_cancel(self._search_job)
            self._search_job = None
        found = index.search(self.search.get())
        if found:
            self.open_page(found[0].page, highlight=found[0].key)
        return 'break'

    def _search_escape(self, event=None) -> str:
        if self.search.get():
            self._clear_search()
            self.title.configure(text=index.page(self.current).title if self.current else '')
        else:
            self.close()
        return 'break'

    def _clear_search(self) -> None:
        if self._search_job is not None:
            self.window.after_cancel(self._search_job)
            self._search_job = None
        self.search.clear()
        self._leave_results()

    def _leave_results(self) -> None:
        old, self._results = self._results, None
        if old is not None:
            old.pack_forget()
            from src.utils.ui_helpers import schedule_window_callback
            schedule_window_callback(old._root(), old, 'idle', old.destroy)
        if self.current in self._frames:
            self._show(self.current)
            self.title.configure(text=index.page(self.current).title)

    def _show_results(self, query: str) -> None:
        px = C.get().px
        found = index.search(query)
        old, self._results = self._results, None
        if old is not None:
            old.destroy()
        area = ui.ScrollArea(self.body, on='bg')
        holder = ui.Frame(area.content, on='bg', padding=(px(SIDE_PADDING), px(8), px(SIDE_PADDING), px(32)))
        holder.pack(fill='both', expand=True)
        if found:
            count = f'{len(found)} setting found' if len(found) == 1 else f'{len(found)} settings found'
            group = ui.SettingGroup(holder, count, on='bg')
            group.pack(fill='x')
            for entry in found[:SETTINGS_RESULTS_MAX]:
                row = group.row(entry.title, entry.description, key=entry.key)
                ui.Chip(row.control, index.page(entry.page).title, tone='tag', on=row.plane).pack(
                    side='left', padx=(0, px(8)))
                ui.Button(row.control, 'Show', kind='soft', on=row.plane,
                          command=lambda e=entry: self.open_page(e.page, highlight=e.key)).pack(side='left')
            if len(found) > SETTINGS_RESULTS_MAX:
                note = ui.Label(holder, f'Showing the first {SETTINGS_RESULTS_MAX}. Type more to narrow it down.',
                                kind='muted', on='bg', justify='left')
                note.pack(anchor='w', pady=(px(8), 0))
                holder.bind('<Configure>', lambda event: SettingsPage._wrap(note, holder), add='+')
        else:
            ui.EmptyState(holder, f'Nothing matches "{query}"',
                          'Try a shorter word, like "shortcut" or "dark".', icon='search').pack(pady=px(48))
        self._results = area
        for frame in self._frames.values():
            frame.pack_forget()
        area.pack(fill='both', expand=True)
        area.adopt()
        self.title.configure(text='Search results')

    # ------------------------------------------------------------------------------------------
    # Keys, closing
    # ------------------------------------------------------------------------------------------
    def _escape(self, event=None):
        if self.recording:
            return None              # Esc cancels the recording (the editor reads it), not the window
        if self.search.get():
            return self._search_escape(event)
        self.close()
        return 'break'

    def deactivate(self) -> None:
        """Leaving the page flushes edits and masks keys without cancelling downloads."""
        page = self.pages.get('providers')
        if page is not None:
            page._finish_drag(False)
            page._cancel_save()
            page._save_if_changed()
            sec = getattr(page, 'audio_section', None)
            if sec is not None and hasattr(sec, 'shortcut_editor'):
                sec.shortcut_editor.cancel_recording()
            for card in page.cards:
                card.set_revealed(False)
            page.invalidate_auth()
            page._update_add_button()
        shortcuts = self.pages.get('shortcuts')
        if shortcuts is not None:
            for editor in shortcuts.editors.values():
                editor.cancel_recording()
        self.set_recording(False)

    def close(self) -> None:
        if self.container is not None:
            self.deactivate()
            if self.on_close:
                self.on_close()
            return
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def _remember_size(self) -> None:
        """Keep the size the user left the window at (not while it is maximised or minimised)."""
        if self.container is not None:
            return
        try:
            if self.window.state() != 'normal':
                return
            size = (self.window.winfo_width(), self.window.winfo_height())
            if size != tuple(self.config.get_settings_window_size()):
                self.config.set_settings_window_size(*size)
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.window:
            return
        self._remember_size()
        for job in (self._saved_job, self._search_job, self._highlight_job):
            if job is not None:
                try:
                    self.window.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
        self._saved_job = self._search_job = self._highlight_job = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self.set_recording(False)
        # Release page-owned Tcl variables on their UI thread, not a later worker GC cycle.
        self.pages.clear()
        self._frames.clear()
        self._areas.clear()
        self._extra.clear()

    # ------------------------------------------------------------------------------------------
    # Updates (the mixin drives the widgets of the About page, by name)
    # ------------------------------------------------------------------------------------------
    def offer_update(self, version, updater) -> None:
        self.open_page('about')            # its widgets are made when it is first shown
        super().offer_update(version, updater)

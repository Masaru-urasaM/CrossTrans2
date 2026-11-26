"""
The base every Settings page shares (TODO.md U8, Decision 51).

A page is built from `settings_index` entries: a row's title and sentence come from there, so a row and
its search result can never say different things. A page owns no window behaviour. It reads and writes
`host.config`, and tells `host.changed(key)` what it changed so the application can react (register
the hotkeys again, refresh the tray). Pages are built the first time they are opened.

The host (the Settings window; a test passes a stand-in) offers: `config`, `window` (the Toplevel),
`update_manager`, `changed(key)`, `refresh_pages()`, `adopt(page_key)` (bind the wheel on widgets a page
added later), `reveal(page_key, widget)` (scroll to a widget a page added), `api_changed()` (the saved keys
changed: the application reconfigures), `set_recording(active)`, `key_source_factory`, `shortcut_status()`.
"""
import tkinter as tk
from typing import Callable, Dict, List, Optional, Tuple

from src.core import settings_index as index
from src.ui import components as ui
from src.ui.components import context as C


class SettingsPage:
    key = ''
    scrolls = True              # False: the page manages its own scrolling (a hosted older page)

    def __init__(self, host):
        self.host = host
        self.config = host.config
        self.body: Optional[ui.Frame] = None
        self.groups: List[ui.SettingGroup] = []
        self.rows: Dict[str, ui.SettingRow] = {}
        self.variables: Dict[str, tk.BooleanVar] = {}
        self.switches: Dict[str, ui.Switch] = {}
        self._readers: List[Tuple[tk.BooleanVar, Callable[[], bool]]] = []

    # --- building -----------------------------------------------------------------------------
    def build(self, parent) -> ui.Frame:
        """Create the page's widgets in `parent`. The caller places `self.body`."""
        self.body = ui.Frame(parent, on='bg')
        self.populate()
        return self.body

    def populate(self) -> None:
        raise NotImplementedError

    def group(self, heading: str = '', caption: str = '', parent=None, gap: int = 24) -> ui.SettingGroup:
        """A card of rows with `heading` above it and, optionally, a sentence under the heading."""
        px = C.get().px
        group = ui.SettingGroup(parent or self.body, heading, on='bg')
        group.caption_label = None
        if caption:
            if group.heading_label is not None:
                group.heading_label.pack_configure(pady=(0, px(2)))
            label = ui.Label(group, caption, kind='muted', on=group.plane, justify='left',
                             wraplength=px(560))
            label.pack(anchor='w', before=group.card, pady=(0, px(8)))
            group.bind('<Configure>', lambda e, lb=label, g=group: self._wrap(lb, g), add='+')
            group.caption_label = label
        group.pack(fill='x', pady=(0, px(gap)))
        self.groups.append(group)
        return group

    @staticmethod
    def _wrap(label, frame) -> None:
        """Wrap a caption to its group. The group's width comes from its parent, not from the caption, so a
        wrap can never feed back into the width it came from."""
        padding = list(frame.tk.splitlist(frame.cget('padding')))
        padding = [frame.winfo_pixels(value) for value in padding]
        sides = (padding * 4)[:4] if len(padding) == 1 else (padding * 2)[:4]
        inset = sides[0] + sides[2] if sides else 0
        room = max(1, frame.winfo_width() - inset - C.get().px(4))
        if abs(int(float(str(label.cget('wraplength')) or 0)) - room) > 2:
            label.configure(wraplength=room)

    def row(self, group: ui.SettingGroup, key: str, *, stacked: bool = False) -> ui.SettingRow:
        """The row for the index entry `key`, with the entry's words."""
        entry = index.entry(key)
        row = group.row(entry.title, entry.description, key=key, stacked=stacked)
        self.rows[key] = row
        return row

    def toggle(self, group: ui.SettingGroup, key: str, read: Callable[[], bool],
               write: Callable[[bool], None]) -> ui.SettingRow:
        """A row with an on/off switch: `read()` gives the setting, `write(flag)` stores a change."""
        row = self.row(group, key)
        variable = tk.BooleanVar(master=self.body, value=bool(read()))

        def command():
            write(variable.get())
            self.changed(key)
        switch = ui.Switch(row.control, variable=variable, command=command, on=row.plane)
        switch.pack()
        self.variables[key], self.switches[key] = variable, switch
        self._readers.append((variable, read))
        return row

    # --- what the host and the window ask of a page ----------------------------------------------
    def changed(self, key: str) -> None:
        self.host.changed(key)

    def row_for(self, key: str) -> Optional[ui.SettingRow]:
        return self.rows.get(key)

    def refresh(self) -> None:
        """Show what the settings are now (after Restore defaults, or a change made elsewhere)."""
        for variable, read in self._readers:
            variable.set(bool(read()))

    def adopt(self) -> None:
        """Bind the wheel on widgets added after the page was built."""
        self.host.adopt(self.key)

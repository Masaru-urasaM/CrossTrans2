"""
Help (TODO.md U8, step 6, Decision 51): the guide, one card per topic.

The guide used to be a tab of 430 lines of widget code with the page names written into its sentences. The
words now live in `core/guide_content.py` (and are what the Settings search finds); this page only draws
them: the topic's sentence, its numbered steps, its bullet points, and a button to the page it talks about.
A bullet is a small round icon rather than a typed symbol, so it takes the theme's colour like everything else.
"""
import tkinter as tk
from typing import List

from src.constants import LANGUAGES
from src.core import guide_content
from src.core.remote_config import get_config
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.settings.page import SettingsPage

MARKER_WIDTH = 22                   # at 100 %: the number or the bullet, and the gap after it
WRAP_SLACK = 4                      # at 100 %: a wrapped label asks a pixel or two more than its wrap length
BULLET_ICON, BULLET_SIZE = 'circle-fill', 6
BULLET_DROP = 8                     # at 100 %: a bullet sits level with the middle of the first line


class HelpPage(SettingsPage):
    key = 'help'

    def populate(self) -> None:
        ctx = C.get()
        remote = get_config()
        self.values = dict(languages=len(LANGUAGES), providers=max(0, len(remote.providers_list) - 1))
        self.texts: List[ui.Label] = []         # every line of text, wrapped to the card
        self.bullets: List[ui.Label] = []
        self.links = {}
        for topic in guide_content.TOPICS:
            row = self.row(self.group(), topic.key, stacked=True)
            self._fill(row, topic)
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.body.bind('<Destroy>', self._on_destroy, add='+')

    def _fill(self, row: ui.SettingRow, topic: guide_content.Topic) -> None:
        px = C.get().px
        control = row.control
        for number, step in enumerate(topic.steps, 1):
            self._line(control, row.plane, f'{number}.', step)
        for note in topic.notes:
            self._line(control, row.plane, None, note)
        if topic.link is not None:
            page, text = topic.link
            button = ui.Button(control, text, kind='soft', on=row.plane,
                               command=lambda target=page: self.host.open_page(target))
            button.pack(anchor='w', pady=(px(10), 0))
            self.links[topic.key] = button
        control.bind('<Configure>', lambda event, box=control: self._wrap_all(box), add='+')

    def _line(self, parent, plane: str, number, text: str) -> None:
        """One step (`number` is "1.") or one bullet (`number` is None)."""
        px = C.get().px
        line = ui.Frame(parent, on=plane)
        line.pack(fill='x', pady=(px(4), 0))
        marker = ui.Label(line, number or '', kind='strong' if number else 'muted', on=plane)
        if number is None:
            self.bullets.append(marker)
            self._paint_bullet(marker)
        marker.place(x=0, y=px(1 if number else BULLET_DROP))
        label = ui.Label(line, guide_content.fill(text, **self.values), kind='body', on=plane, justify='left',
                         wraplength=px(480))
        label.pack(anchor='w', padx=(px(MARKER_WIDTH), 0))
        self.texts.append(label)

    def _paint_bullet(self, marker) -> None:
        ctx = C.get()
        marker.configure(image=ctx.icon(BULLET_ICON, BULLET_SIZE, ctx.palette.muted))

    def _wrap_all(self, box) -> None:
        """Wrap every line to its card. The card's width comes from its parent, not from the text, so a wrap
        cannot feed back into the width it came from."""
        px = C.get().px
        room = max(px(160), box.winfo_width() - px(MARKER_WIDTH + WRAP_SLACK))
        for label in self.texts:
            try:
                if label.master.master is box and abs(int(float(str(label.cget('wraplength')) or 0)) - room) > 2:
                    label.configure(wraplength=room)
            except tk.TclError:
                pass

    def _retheme(self, manager) -> None:
        try:
            for marker in self.bullets:
                self._paint_bullet(marker)
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self.body:
            self._unsubscribe()

"""Rounded word pills shared by Dictionary selection, phrase tags and drag previews."""
import tkinter as tk

from src.ui import components as ui
from src.ui.components import render
from src.ui.ruby_text import RubyRow
from src.ui.theme.compat import NO_AUTOSTYLE


class WordPill(tk.Canvas):
    """A focusable pill holding a plain label or an intact two-row ruby sequence.

    Only the backing plate is drawn as an image. Text remains real labels, so
    readings retain their alignment and every part can receive mouse events.
    """

    def __init__(self, parent, word, segments=(), *, selected=False, removable=False,
                 on_remove=None, takefocus=True):
        self.ctx = ui.get()
        self.selected, self.dimmed, self.hovered, self.focused = selected, False, False, False
        self.word = word
        super().__init__(parent, bd=0, highlightthickness=0, takefocus=takefocus,
                         cursor='hand2', **NO_AUTOSTYLE)
        self.row = None
        self.body = tk.Frame(self, bd=0, **NO_AUTOSTYLE)
        p, px = self.ctx.palette, self.ctx.px
        if any(segment.ruby for segment in segments):
            self.row = RubyRow(self.body, segments, base_font=self.ctx.font('body'),
                               ruby_font=self.ctx.font('caption', 'japanese'),
                               bg=p.surface, base_fg=p.text, ruby_fg=p.ruby, padx=0, pady=0)
            self.row.frame.pack(side='left')
            self.label = self.row.bases[0]
            self.parts = [self, self.body] + self.row.widgets
        else:
            self.label = tk.Label(self.body, text=word, font=self.ctx.font('body'),
                                  padx=0, pady=0, wraplength=px(480), justify='left',
                                  cursor='hand2', **NO_AUTOSTYLE)
            self.label.pack(side='left')
            self.parts = [self, self.body, self.label]
        self.close_btn = None
        if removable:
            self.close_btn = tk.Label(self.body, image=self.ctx.icon('x', 16, p.soft_text),
                                      cursor='hand2', padx=px(2), **NO_AUTOSTYLE)
            self.close_btn.pack(side='left', anchor='s', padx=(px(4), 0))
            self.close_btn.bind('<Button-1>', lambda e: on_remove() if on_remove else None)
            self.parts.append(self.close_btn)
            self.bind('<Delete>', lambda e: on_remove() if on_remove else None)
            self.bind('<BackSpace>', lambda e: on_remove() if on_remove else None)
        self._plate = self.create_image(0, 0, anchor='nw')
        self._content = self.create_window(px(10), px(5), anchor='nw', window=self.body)
        self.body.bind('<Configure>', self._fit, add='+')
        self.bind('<FocusIn>', lambda e: self._focus(True))
        self.bind('<FocusOut>', lambda e: self._focus(False))
        for part in self.parts:
            part.bind('<Enter>', lambda e: self._hover(True), add='+')
            part.bind('<Leave>', lambda e: self._hover(False), add='+')
        self._unsubscribe = self.ctx.manager.subscribe(self._paint)
        self.bind('<Destroy>', self._destroyed, add='+')
        self._paint()

    def _fit(self, event=None):
        px = self.ctx.px
        self.configure(width=self.body.winfo_reqwidth() + px(20),
                       height=max(px(32), self.body.winfo_reqheight() + px(10)))
        self._paint()

    def _hover(self, value):
        self.hovered = value
        self._paint()

    def _focus(self, value):
        self.focused = value
        self._paint()

    def set_selected(self, value):
        self.selected = bool(value)
        self._paint()

    def set_dimmed(self, value):
        self.dimmed = bool(value)
        self._paint()

    def _paint(self, manager=None):
        p, px = self.ctx.palette, self.ctx.px
        under = str(self.master.cget('bg'))
        if not under.startswith('#'):
            under = '#%02x%02x%02x' % tuple(value // 256 for value in self.winfo_rgb(under))
        fill = p.alt if self.dimmed else p.soft if self.selected else p.alt if self.hovered else p.surface
        fg = p.muted if self.dimmed else p.soft_text if self.selected else p.text
        ruby = p.muted if self.dimmed else p.soft_text if self.selected else p.ruby
        edge = p.focus if self.focused else p.accent if self.selected else p.strong
        self.configure(bg=under)
        self.body.configure(bg=fill)
        if self.row:
            self.row.set_colors(bg=fill, base_fg=fg, ruby_fg=ruby)
        else:
            self.label.configure(bg=fill, fg=fg)
        if self.close_btn:
            self.close_btn.configure(bg=fill, image=self.ctx.icon('x', 16, fg))
        width = self.body.winfo_reqwidth() + px(20)
        height = max(px(32), self.body.winfo_reqheight() + px(10))
        key = ('word-pill', width, height, fill, edge, under, self.focused)
        photo = self.ctx._images.get(key)
        if photo is None:
            photo = render.to_photo(self.ctx.root, render.rounded_rect(
                width, height, height // 2, fill, edge, px(2 if self.focused else 1), under))
            # Word contents vary: bound the shared cache, unlike fixed control assets.
            if sum(k[0] == 'word-pill' for k in self.ctx._images) > 256:
                for old in [k for k in self.ctx._images if k[0] == 'word-pill']:
                    self.ctx._images.pop(old)
            self.ctx._images[key] = photo
        self._photo = photo
        self.itemconfigure(self._plate, image=photo)
        self.coords(self._content, px(10), (height - self.body.winfo_reqheight()) // 2)

    def _destroyed(self, event):
        if event.widget is self:
            self._unsubscribe()

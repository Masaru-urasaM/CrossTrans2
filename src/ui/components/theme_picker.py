"""Illustrative palette previews for first-run theme selection."""
import tkinter as tk

from src.ui import components as ui
from src.ui.components import context as C
from src.ui.theme import tokens


class ThemePicker(ui.Frame):
    def __init__(self, parent, config, on_change=None):
        super().__init__(parent)
        self.config = config
        self.on_change = on_change
        self.tiles = {}
        self.previews = {}
        self.follow = ui.Button(self, 'Follow Windows', lambda: self.choose('system'), kind='soft')
        self.follow.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, C.get().px(12)))
        for index, (name, label) in enumerate(tokens.THEME_OPTIONS[1:]):
            tile = ui.Card(self, padding=8)
            tile.grid(row=index // 2 + 1, column=index % 2, sticky='nsew', padx=C.get().px(4), pady=C.get().px(4))
            canvas = tk.Canvas(tile, width=C.get().px(180), height=C.get().px(92),
                               highlightthickness=2, takefocus=False)
            canvas.pack(fill='x')
            canvas.bind('<Button-1>', lambda event, key=name: self.choose(key))
            caption = ui.Label(tile, label, justify='center', wraplength=C.get().px(180))
            caption.pack(fill='x', pady=(C.get().px(4), 0))
            caption.bind('<Button-1>', lambda event, key=name: self.choose(key))
            tile.bind('<Configure>', lambda event, text=caption: text.configure(wraplength=max(1, event.width - C.get().px(24))), add='+')
            ui.Button(tile, 'Use theme', lambda key=name: self.choose(key), kind='subtle').pack(fill='x')
            self.tiles[name] = tile
            self.previews[name] = canvas
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self._unsubscribe = C.get().manager.subscribe(lambda manager: self.redraw())
        self.bind('<Destroy>', self._destroyed, add='+')
        self.redraw()

    def choose(self, name):
        self.config.set_theme_mode(name)
        C.get().manager.set_mode(name)
        if self.on_change:
            self.on_change(name)
        self.redraw()

    def redraw(self):
        selected = self.config.get_theme_mode()
        self.follow.configure(text='Follow Windows (selected)' if selected == 'system' else 'Follow Windows')
        scale = C.get().px
        for name, canvas in self.previews.items():
            p = tokens.PALETTES[name]
            canvas.delete('all')
            canvas.configure(bg=p.bg, highlightbackground=C.get().palette.focus if selected == name else p.border)
            def box(x1, y1, x2, y2, fill):
                canvas.create_rectangle(scale(x1), scale(y1), scale(x2), scale(y2), fill=fill, outline='')
            box(0, 0, 180, 15, p.alt)
            box(0, 16, 29, 92, p.alt)
            box(5, 24, 24, 29, p.soft)
            box(5, 37, 24, 40, p.border)
            box(5, 49, 24, 52, p.border)
            box(35, 22, 101, 66, p.surface)
            box(108, 22, 174, 66, p.surface)
            for offset in (29, 36, 43):
                box(40, offset, 92, offset + 2, p.muted)
                box(113, offset, 165, offset + 2, p.text)
            box(134, 74, 173, 85, p.accent)
            box(38, 76, 93, 79, p.border)

    def _destroyed(self, event):
        if event.widget is self:
            self._unsubscribe()

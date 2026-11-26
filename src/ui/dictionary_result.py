"""Dictionary entries as reading cards; the model's eight-field protocol stays unchanged."""
import math
import tkinter as tk
from tkinter import font as tkfont

from src.core.furigana import RubySegment
from src.ui import components as ui
from src.ui.dictionary_render import split_dictionary_text
from src.ui.ruby_text import RubyText
from src.ui.theme import engine
from src.ui.theme.compat import NO_AUTOSTYLE
from src.ui.translator_layout import window_size

from src.core.dictionary_entries import take_runs, parse_entries  # noqa: F401 - tests import take_runs from here


class DictionaryResultWindow:
    def __init__(self, root, result, target_lang, *, source_lang=None, trial_info=None,
                 words=(), annotate=True, area=None, position=(0, 0), container=None, on_close=None, on_open_translate=None):
        self.ctx = ui.get()
        px = self.ctx.px
        self.result, self.boxes, self.cards = result, [], []
        self.container = container
        self.window = ui.Frame(container, on='bg') if container is not None else tk.Toplevel(root)
        if container is None:
            self.window.withdraw()
        else:
            self.window.pack(fill='both', expand=True)
        self.window.dictionary_view = self
        languages = f'{source_lang} → {target_lang}' if source_lang else target_lang
        title = f'Dictionary - {languages}'
        if container is None:
            self.window.title(title)
            engine.attach(self.window)
        if area is None:
            from src.ui.components.dialog import work_area
            area = work_area()
        left, top, right, bottom = area
        size = window_size(area, scale=self.ctx.scale, default=(720, 760), minimum=(420, 320))
        width, height = size.width, size.height
        self.window.sizes_from_work_area = True
        x = max(left + px(12), min(position[0], right - width - px(12)))
        y = max(top + px(12), min(position[1], bottom - height - px(36)))
        if container is None:
            self.window.geometry(f'{width}x{height}+{x}+{y}')
            self.window.minsize(min(px(420), width), min(px(320), height))
        body = ui.Frame(self.window, padding=px(20))
        body.pack(fill='both', expand=True)
        header = ui.Frame(body)
        header.pack(fill='x', pady=(0, px(16)))
        ui.Label(header, 'Dictionary', kind='subtitle').pack(anchor='w')
        ui.Chip(header, languages).pack(anchor='w', pady=(px(8), 0))
        footer = ui.Frame(body)
        footer.pack(side='bottom', fill='x', pady=(px(16), 0))
        self.copy_button = ui.Button(footer, 'Copy', icon='copy', command=self.copy)
        self.copy_button.pack(side='left')
        ui.Button(footer, 'Back to lookup' if container is not None else 'Done', kind='accent',
                  command=on_close or self.window.destroy).pack(side='right')
        if on_open_translate is not None:
            ui.Button(footer, 'Open in Translate', on_open_translate, kind='soft').pack(side='left', padx=px(8))
        self.scroll = ui.ScrollArea(body)
        self.scroll.pack(fill='both', expand=True)
        # One annotation pass over each complete entry, before slicing off labels.
        # A single accent replaces the old twenty arbitrary highlight colours.
        self.runs = split_dictionary_text(result, target_lang, words, [self.ctx.palette.accent],
                                          annotate=annotate, source_lang=source_lang)
        self.entries = parse_entries(result, self.runs)
        for entry in self.entries:
            self._entry(entry)
        if not self.entries:
            ui.EmptyState(self.scroll.content, 'No dictionary entry',
                          'Choose another word and try again.', icon='book').pack(fill='both', expand=True)
        self.scroll.adopt()
        self._copy_job = None
        self._unsubscribe = self.ctx.manager.subscribe(self._retheme)
        self.window.bind('<Destroy>', self._destroyed, add='+')
        if container is None:
            self.window.bind('<Escape>', lambda e: self.window.destroy())
            self.window.deiconify()
            self.window.update_idletasks()
            # A short entry fits its content; long entries scroll within the work area.
            desired = self.scroll.content.winfo_reqheight() + header.winfo_reqheight() + footer.winfo_reqheight() + px(76)
            self.window.geometry(f'{width}x{min(height, max(px(320), desired))}+{x}+{y}')
            self.window.lift()

    def _entry(self, entry):
        px = self.ctx.px
        card = ui.Card(self.scroll.content, padding=20)
        card.pack(fill='x', pady=(0, px(12)))
        self.cards.append(card)
        if entry.heading:
            self._text(card, entry.heading, kind='subtitle', script='japanese')
        for field in entry.fields:
            key = field.label.strip().casefold()
            section = ui.Frame(card)
            section.pack(fill='x', pady=(px(12), 0))
            if key in ('source language', 'word type') and '\n' not in field.text and len(field.text) < 80:
                ui.Label(section, field.label, kind='caption').pack(side='left', padx=(0, px(8)))
                # Keep ruby values in RubyText; a static chip cannot draw readings.
                if not any(run.ruby for run in field.runs):
                    ui.Chip(section, field.text or '—', tone='tag', localize=False).pack(side='left')
                    continue
            else:
                ui.Label(section, field.label, kind='strong').pack(anchor='w', pady=(0, px(4)))
            self._text(section, field.runs, kind='reading' if key == 'translation' else 'body')

    def _text(self, parent, runs, kind='body', script='latin'):
        p = self.ctx.palette
        box = RubyText(parent, wrap='word', width=1, height=1, relief='flat',
                       base_font=self.ctx.font(kind, script), ruby_font=self.ctx.font('caption', 'japanese'),
                       bg=p.surface, base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby,
                       spacing1=0, spacing3=self.ctx.px(2), padx=0, pady=0,
                       bd=0, highlightthickness=0, **NO_AUTOSTYLE)
        box.fits_to_content = True
        box.dictionary_runs = tuple(runs)
        self._populate(box)
        box.configure(state='disabled')
        box.pack(fill='x')
        box.bind('<Configure>', lambda e: self._fit_box(box), add='+')
        self.boxes.append(box)
        return box

    def _populate(self, box):
        p = self.ctx.palette
        for run in box.dictionary_runs:
            tag = 'lookup' if run.color else None
            if tag:
                box.tag_configure(tag, foreground=p.accent)
            if run.ruby:
                box.insert_segments('end', (RubySegment(run.base, run.ruby),), tag,
                                    kanji_fg=p.accent if tag else None)
            else:
                box.insert_plain('end', run.base, tag)

    def _fit_box(self, box):
        if box.winfo_width() > 1:
            actual = box.count('1.0', 'end', 'update', 'ypixels')
            needed = int(actual[0] if isinstance(actual, tuple) else actual or 0)
            rows = max(1, math.ceil(needed / tkfont.Font(font=box.base_font).metrics('linespace')))
            if int(box.cget('height')) != rows:
                box.configure(height=rows)

    def copy(self):
        import pyperclip
        pyperclip.copy(self.result)
        self.copy_button.configure(text='Copied')
        self.copy_button.set_icon('check2')
        if self._copy_job:
            self.ctx.root.after_cancel(self._copy_job)
        self._copy_job = self.ctx.root.after(1200, self._reset_copy)

    def _reset_copy(self):
        self._copy_job = None
        self.copy_button.configure(text='Copy')
        self.copy_button.set_icon('copy')

    def _retheme(self, manager):
        p = self.ctx.palette
        for box in self.boxes:
            box.clear()
            box.recolour(bg=p.surface, base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby,
                         selection=p.selection)
            self._populate(box)
            self.scroll.adopt(box)

    def _destroyed(self, event):
        if event.widget is self.window:
            self._unsubscribe()
            if self._copy_job:
                self.ctx.root.after_cancel(self._copy_job)
                self._copy_job = None

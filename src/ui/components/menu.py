"""Native Tk menus with localized labels and unchanged command/value bindings."""
import tkinter as tk

from src.ui import i18n


class Menu(tk.Menu):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._sources = []
        i18n.register(self, '', 'refresh')

    def add(self, itemType, cnf=None, **kw):
        options = {**(cnf or {}), **kw}
        source = options.get('label')
        if source is not None:
            options['label'] = i18n.tr(source)
        result = super().add(itemType, **options)
        self._sources.append(source)
        return result

    def _refresh_language(self):
        offset = 1 if bool(int(self.cget('tearoff'))) else 0
        for index, source in enumerate(self._sources):
            if source is not None:
                self.entryconfigure(index + offset, label=i18n.tr(source))

"""
The Settings model chooser (TODO.md U8, step 4): `LanguagePicker`'s dropdown over a provider's models.

A provider offers anywhere from a handful to a few hundred models ("Auto" lists every provider's), so the
old autocomplete combobox became the same searchable list the language picker is: type to narrow it,
arrows and Enter to choose, Esc to close. Unlike languages, a model the list does not have is allowed - a
new release the remote list has not caught up with - so a name that matches nothing is offered as
"Use "name"". The first row is "Auto" (the app picks and remembers a working model).
"""
from tkinter import font as tkfont

from typing import Callable, List, Optional, Sequence, Tuple

from src.core import model_names
from src.ui import i18n
from src.ui.components import context as C
from src.ui.components.language_picker import LANGUAGE, LanguagePicker, place_below

AUTO = model_names.AUTO
DROP_MIN_WIDTH = 380             # at 100 %: the longest names fit whole
BUTTON_CHARS = 28                # initial, unmapped-label safety limit; visible text fits in pixels
FIELD_WIDTH = 240                 # natural field width at 100%; allocated grid width may be smaller
LEFT_QUOTE, RIGHT_QUOTE = chr(0x201C), chr(0x201D)

Model = Tuple[str, str, str]     # the picker's row shape: (name, '', '')


def _rows(models: Sequence[str]) -> List[Model]:
    return [(name, '', '') for name in models]


def shorten(name: str, limit: int = BUTTON_CHARS) -> str:
    """`name` as the field shows it: whole when it fits, else its start and "..." """
    return name if len(name) <= limit else name[:limit - 3] + '...'


class ModelPicker(LanguagePicker):
    """A button naming the model that opens a searchable list. `models` is the list to offer ("Auto"
    first, as `model_names.models_for` returns it); `value` the model shown ('' or "Auto" for Auto);
    `on_change(name)` runs when the user chooses one, a typed name included."""

    PLACEHOLDER = 'Search or type a model'

    def __init__(self, parent, models: Sequence[str], value: str, on_change: Callable[[str], None],
                 label: str = 'Model', on: Optional[str] = None, auto_label: str = AUTO, **kw):
        self.auto_label = auto_label
        super().__init__(parent, _rows(models), value or AUTO, on_change, label=label, on=on, **kw)
        self.button._localize = False
        i18n.unregister(self.button)
        self.button.configure(width=0)
        self.configure(width=C.get().px(FIELD_WIDTH))
        self.pack_propagate(False)
        self._refresh_language()
        i18n.register(self, '', 'refresh')
        self.button.bind('<Configure>', self._fit_label, add='+')
        self._stop_field = C.get().manager.subscribe(lambda manager: self._fit_label())
        self.bind('<Destroy>', self._release_field, add='+')

    def _release_field(self, event):
        if event.widget is self:
            self._stop_field()

    # --- the value and the list -------------------------------------------------------------------------
    def set(self, name: str) -> None:
        """Show `name` ('' is Auto) without calling `on_change`."""
        self._value = name or AUTO
        self._refresh_language()

    def _refresh_language(self):
        self.button.configure(text=i18n.tr(self.auto_label) if self._value == AUTO else shorten(self._value))
        self._fit_label()
        if self.is_open:
            self._populate(select=self._value)

    def _fit_label(self, event=None):
        height = self.button.winfo_reqheight()
        if int(float(str(self.cget('height')) or 0)) != height:
            self.configure(height=height)
        if self.button.winfo_width() <= 1:
            return
        from src.ui.popup_layout import elide
        font = tkfont.Font(root=self.button, font=C.get().font('body_strong'))
        label = i18n.tr(self.auto_label) if self._value == AUTO else self._value
        current = str(self.button.cget('text'))
        chrome = max(C.get().px(36), self.button.winfo_reqwidth() - font.measure(current))
        fitted = elide(label, font.measure, max(1, self.button.winfo_width() - chrome))
        if current != fitted:
            self.button.configure(text=fitted)

    def set_models(self, models: Sequence[str]) -> None:
        """Offer another list (the provider changed). The value shown stays as it is: a name the new
        list lacks is still a name the user may mean."""
        self.languages = _rows(models)
        if self.is_open:
            self._populate(select=self._value)

    @property
    def models(self) -> List[str]:
        return [name for name, _, _ in self.languages]

    # --- the dropdown --------------------------------------------------------------------------------------
    def _place(self, win, ctx) -> None:
        place_below(win, self.button, ctx, ctx.px(DROP_MIN_WIDTH))

    def _populate(self, select: Optional[str] = None) -> None:
        query = self.query.get() if hasattr(self, 'query') else ''
        names = self.models
        self.rows = []
        self.listbox.delete(0, 'end')
        wanted = None
        for name in model_names.rank(names, query):
            self.listbox.insert('end', '   ' + (i18n.tr(AUTO) if name == AUTO else name))
            self.rows.append((LANGUAGE, (name, '', '')))
            if wanted is None and select and name == select:
                wanted = self.listbox.size() - 1
        if model_names.offers_custom(names, query):
            typed = query.strip()
            self.listbox.insert('end', '   ' + i18n.tr('Use {0}', LEFT_QUOTE + typed + RIGHT_QUOTE))
            self.rows.append((LANGUAGE, (typed, '', '')))
        self._select(wanted if wanted is not None else self._first_language_row())

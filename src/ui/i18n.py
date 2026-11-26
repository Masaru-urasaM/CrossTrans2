"""English/Japanese presentation strings; translation payloads never pass through this module.

English source messages are stable catalog keys. Widgets retain that source so switching the
interface language does not translate an already translated label or alter a command's value.
The registry uses weak keys: closing a window releases its labels and their source messages.
"""
import json
import logging
import re
import string
import weakref
from functools import lru_cache
from pathlib import Path

LANGUAGES = ('en', 'ja')
_language = 'en'
_widgets = weakref.WeakKeyDictionary()


@lru_cache(maxsize=1)
def catalog():
    path = Path(__file__).with_name('locales') / 'catalog.json'
    return json.loads(path.read_text(encoding='utf-8'))


def language():
    return _language


def normalize(value):
    return value if value in LANGUAGES else 'en'


def set_language(value):
    """Refresh only registered presentation text; entry values and model output are untouched."""
    global _language
    value = normalize(value)
    if value == _language:
        return False
    _language = value
    for widget in list(_widgets):
        try:
            refresh(widget)
        except Exception:
            logging.exception('[Language] could not refresh a presentation string')
    return True


@lru_cache(maxsize=1)
def _templates():
    result = []
    for source, translations in catalog().items():
        if translations.get('runtime_match', True) is False:
            continue
        pieces = list(string.Formatter().parse(source))
        fields = [field for _, field, _, _ in pieces if field is not None]
        if not fields:
            continue
        literal = ''.join(part for part, _, _, _ in pieces)
        if len(re.sub(r'\W', '', literal)) < 3:
            continue
        pattern = '^' + ''.join(re.escape(part) + ('(.+?)' if field is not None else '')
                                for part, field, _, _ in pieces) + '$'
        result.append((len(literal), re.compile(pattern, re.DOTALL), fields, translations))
    return sorted(result, key=lambda item: item[0], reverse=True)


def _format(template, fields, values, translate_fields=()):
    positional, named = [], {}
    for field, value in zip(fields, values):
        if field in translate_fields:
            value = tr(value)
        if field.isdecimal():
            index = int(field)
            positional.extend([''] * max(0, index + 1 - len(positional)))
            positional[index] = value
        else:
            named[field] = value
    return template.format(*positional, **named)


def tr(source, *values, **named):
    """Translate a UI message, optionally formatting its unchanged dynamic values.

    Already formatted legacy labels are matched against the catalog's explicit templates.
    New messages should pass the template and its values separately.
    """
    if not isinstance(source, str):
        return source
    translated = source
    if _language != 'en':
        entry = catalog().get(source)
        if entry:
            translated = entry.get(_language, source)
            for field in entry.get('translate', ()):
                if field.isdecimal() and int(field) < len(values):
                    changed = list(values)
                    changed[int(field)] = tr(changed[int(field)])
                    values = tuple(changed)
                elif field in named:
                    named = {**named, field: tr(named[field])}
        elif not values and not named:
            stripped = source.strip()
            if stripped and stripped != source:
                before = len(source) - len(source.lstrip())
                after = len(source.rstrip())
                return source[:before] + tr(stripped) + source[after:]
            if '→' in source:
                from src.constants import LANGUAGES as translation_languages
                names = {name for name, _, _ in translation_languages} | {'Unknown', 'Chinese', 'Auto'}
                parts = source.split('→')
                if len(parts) == 2 and all(part.strip() in names for part in parts):
                    return '→'.join(tr(part) for part in parts)
            for _, pattern, fields, translations in _templates():
                match = pattern.fullmatch(source)
                if match:
                    translated = _format(translations.get(_language, source), fields, match.groups(),
                                         translations.get('translate', ()))
                    break
            if translated == source:
                paragraphs = re.split(r'(\n|(?<=\.) +)', source)
                if len(paragraphs) > 1:
                    translated = ''.join(part if index % 2 else tr(part)
                                         for index, part in enumerate(paragraphs))
    return translated.format(*values, **named) if values or named else translated


def register(widget, source, option='text'):
    """Register an explicitly identified UI label, placeholder, or window title."""
    _widgets.setdefault(widget, {})[option] = source


def unregister(widget):
    _widgets.pop(widget, None)


def refresh(widget):
    if hasattr(widget, 'winfo_exists') and not widget.winfo_exists():
        unregister(widget)
        return
    entries = _widgets.get(widget, {})
    for option, source in entries.items():
        value = tr(source)
        if option == 'title':
            widget.tk.call('wm', 'title', widget._w, value)
        elif option == 'placeholder':
            widget._set_localized_placeholder(value)
        elif option == 'refresh':
            widget._refresh_language()
        elif hasattr(widget, '_configure_localized'):
            widget._configure_localized(**{option: value})
        else:
            widget.configure(**{option: value})


def track_title(window):
    """Localize one application's window titles without patching Tk or user text globally."""
    if not hasattr(window, 'title') or not hasattr(window, 'tk'):
        return
    if getattr(window, '_i18n_title_tracked', False):
        return
    window._i18n_title_tracked = True
    source = window.title()
    register(window, source, 'title')
    reference = weakref.ref(window)

    def title(value=None):
        target = reference()
        if target is None:
            return None
        if value is None:
            return target.tk.call('wm', 'title', target._w)
        register(target, value, 'title')
        return target.tk.call('wm', 'title', target._w, tr(value))

    window.title = window.wm_title = title
    refresh(window)


class LocalizedText:
    """Mixin for application labels and controls. Use localize=False for user/model data."""

    def __init__(self, *args, **kw):
        self._localize = kw.pop('localize', True)
        source = kw.get('text')
        if self._localize and isinstance(source, str):
            kw['text'] = tr(source)
        super().__init__(*args, **kw)
        if self._localize and isinstance(source, str):
            register(self, source)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
            cnf = None
        source = kw.get('text')
        if self._localize and isinstance(source, str):
            register(self, source)
            kw['text'] = tr(source)
        return super().configure(cnf, **kw)

    config = configure

    def _configure_localized(self, **kw):
        return super().configure(**kw)

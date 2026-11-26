"""
The type ramp in Tk (Decision 46, TODO.md 6.2): Fluent's sizes as pixels at 100 %, a family
chain per script, and one resolution step with an explicit fallback.

A missing or misspelt family does not fail in Tk - it silently becomes "MS PGothic" on Japanese
Windows (TODO.md V7, measured with `Font.actual()`). So a family is only ever used after it has
been looked up in the installed list, and the chain always ends in "Segoe UI". Pure logic: the
installed list is passed in (`installed_families()` reads it from Tk).
"""
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from src.ui.theme.scale import px

FALLBACK_FAMILY = 'Segoe UI'            # on every supported Windows
MIN_PX = 12                             # Fluent's smallest regular text


@dataclass(frozen=True)
class TypeStyle:
    name: str
    px: int                 # size at 100 %
    line: int               # line height at 100 %
    semibold: bool = False
    family_kind: str = 'text'   # 'text' | 'display' | 'mono' | 'icon'


RAMP: Dict[str, TypeStyle] = {s.name: s for s in (
    TypeStyle('caption', 12, 16),
    TypeStyle('body', 14, 20),
    TypeStyle('body_strong', 14, 20, semibold=True),
    TypeStyle('reading', 16, 24),               # CrossTrans-specific: the Translator's panes
    TypeStyle('reading_large', 20, 32),         # ...and its Expanded view, read at arm's length
    TypeStyle('subtitle', 20, 28, semibold=True, family_kind='display'),
    TypeStyle('title', 28, 36, semibold=True, family_kind='display'),
    TypeStyle('mono', 13, 18, family_kind='mono'),
    TypeStyle('icon', 16, 16, family_kind='icon'),
)}

# Family chains, best first. Windows truncates a family name at 31 characters, so the display
# family has two spellings.
CHAINS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ('latin', 'regular'): ('Segoe UI Variable Text', 'Segoe UI'),
    ('latin', 'semibold'): ('Segoe UI Variable Text Semibold', 'Segoe UI Semibold', 'Segoe UI'),
    ('latin-display', 'semibold'): ('Segoe UI Variable Display Semibold',
                                    'Segoe UI Variable Display Semib', 'Segoe UI Semibold',
                                    'Segoe UI'),
    ('japanese', 'regular'): ('Yu Gothic UI', 'Meiryo UI', 'Segoe UI'),
    ('japanese', 'semibold'): ('Yu Gothic UI Semibold', 'Yu Gothic UI', 'Meiryo UI', 'Segoe UI'),
    ('chinese-simplified', 'regular'): ('Microsoft YaHei UI', 'Segoe UI'),
    ('chinese-traditional', 'regular'): ('Microsoft JhengHei UI', 'Segoe UI'),
    ('korean', 'regular'): ('Malgun Gothic', 'Segoe UI'),
    ('thai', 'regular'): ('Leelawadee UI', 'Segoe UI'),
    ('indic', 'regular'): ('Nirmala UI', 'Segoe UI'),
    ('mono', 'regular'): ('Cascadia Mono', 'Consolas'),
    ('icon', 'regular'): ('Segoe Fluent Icons', 'Segoe MDL2 Assets'),
}

# Language names (src/constants.LANGUAGES) to the script whose UI font reads them best.
LANGUAGE_SCRIPTS = {
    'Japanese': 'japanese', 'Chinese (Simplified)': 'chinese-simplified',
    'Chinese (Traditional)': 'chinese-traditional', 'Korean': 'korean', 'Thai': 'thai',
    'Hindi': 'indic', 'Bengali': 'indic', 'Tamil': 'indic', 'Telugu': 'indic',
    'Marathi': 'indic', 'Gujarati': 'indic', 'Kannada': 'indic', 'Malayalam': 'indic',
    'Punjabi': 'indic', 'Nepali': 'indic', 'Sinhala': 'indic',
}


def script_for_language(language: Optional[str]) -> str:
    """The script key for a language name; anything unlisted is read in the Latin chain."""
    return LANGUAGE_SCRIPTS.get(language or '', 'latin')


def installed_families(root=None) -> frozenset:
    """Lower-cased installed family names, as Tk lists them (a Tk root must exist)."""
    from tkinter import font
    return frozenset(name.lower() for name in font.families(root))


def resolve_family(chain: Iterable[str], available: Iterable[str],
                   fallback: str = FALLBACK_FAMILY) -> str:
    """The first family of `chain` that is installed; else `fallback`. `available` holds
    lower-cased names."""
    installed = available if isinstance(available, (set, frozenset)) else frozenset(available)
    for family in chain:
        if family.lower() in installed:
            return family
    return fallback


def chain_for(style: TypeStyle, script: str = 'latin') -> Tuple[str, ...]:
    """The family chain for a style in a script. Display styles use the display family only
    for Latin text; every other script keeps its own UI font."""
    weight = 'semibold' if style.semibold else 'regular'
    if style.family_kind == 'mono':
        return CHAINS[('mono', 'regular')]
    if style.family_kind == 'icon':
        return CHAINS[('icon', 'regular')]
    if style.family_kind == 'display' and script == 'latin':
        return CHAINS[('latin-display', 'semibold')]
    return (CHAINS.get((script, weight)) or CHAINS.get((script, 'regular'))
            or CHAINS[('latin', weight)])


def size_px(style: TypeStyle, scale: float = 1.0, text_scale: float = 1.0) -> int:
    """The style's size in pixels at this screen scale and in-app text size (U11). Text is
    never smaller than MIN_PX at 100 %."""
    wanted = px(style.px * text_scale, scale)
    if style.family_kind == 'text':
        return max(wanted, px(MIN_PX, scale))
    return wanted


def font_tuple(name: str, available: Iterable[str], scale: float = 1.0, text_scale: float = 1.0,
               script: str = 'latin') -> tuple:
    """A Tk font tuple for a ramp style: (family, -pixels) - a negative size is pixels - plus
    'bold' only when a semibold style had to fall back to a family with no semibold face."""
    style = RAMP[name]
    family = resolve_family(chain_for(style, script), available)
    spec = (family, -size_px(style, scale, text_scale))
    if style.semibold and 'semi' not in family.lower():
        return spec + ('bold',)
    return spec


def line_px(name: str, scale: float = 1.0, text_scale: float = 1.0) -> int:
    return px(RAMP[name].line * text_scale, scale)


def resized_spec(widget, spec, ratio, *, key=None, previous_scale=1.0, target_scale=None):
    parts = list(spec) if isinstance(spec, (tuple, list)) else list(widget.tk.splitlist(str(spec)))
    if len(parts) < 2:
        return None
    try:
        size = int(parts[1])
    except (TypeError, ValueError):
        return None
    wanted = abs(size) * ratio
    if key is not None and target_scale is not None:
        cache = getattr(widget, '_ct_font_sizes', {})
        original, last = cache.get(key, (abs(size) / previous_scale, size))
        if last != size:
            original = abs(size) / previous_scale
        wanted = original * target_scale
        changed = (1 if size >= 0 else -1) * max(1, int(wanted + 0.5))
        cache[key] = original, changed
        widget._ct_font_sizes = cache
    parts[1] = (1 if size >= 0 else -1) * max(1, int(wanted + 0.5))
    return tuple(parts)


def resize_widgets(widget, ratio, *, previous_scale=1.0, target_scale=None):
    """Resize explicit fonts and ruby measurement models without replacing text."""
    import tkinter as tk

    def resize(spec, key):
        return resized_spec(widget, spec, ratio, key=key, previous_scale=previous_scale,
                            target_scale=target_scale)

    try:
        if hasattr(widget, 'base_font') and hasattr(widget, 'ruby_font'):
            widget.base_font = resize(widget.base_font, 'base_font') or widget.base_font
            widget.ruby_font = resize(widget.ruby_font, 'ruby_font') or widget.ruby_font
            if hasattr(widget, '_base_metrics'):
                widget._layout = None
                for metric, spec in ((widget._base_metrics, widget.base_font),
                                     (widget._ruby_metrics, widget.ruby_font)):
                    if metric:
                        metric.configure(family=spec[0], size=spec[1])
                widget.tag_configure(widget.LIFT_TAG, offset=widget.layout.lift)
            elif hasattr(widget, 'set_segments'):
                widget.set_segments(widget.segments)
        if getattr(widget, '_linked_original_font', None):
            widget._linked_original_font = resize(widget._linked_original_font, 'linked_original_font')
        if 'font' in widget.keys():
            spec = resize(widget.cget('font'), 'font')
            if spec:
                widget.configure(font=spec)
        if isinstance(widget, tk.Text):
            for tag in widget.tag_names():
                spec = resize(widget.tag_cget(tag, 'font'), ('tag', tag))
                if spec:
                    widget.tag_configure(tag, font=spec)
            runs = getattr(widget, '_ct_font_runs', None)
            if runs is not None:
                runs.refresh_font()
        for child in widget.winfo_children():
            resize_widgets(child, ratio, previous_scale=previous_scale, target_scale=target_scale)
    except tk.TclError:
        pass

"""Read Windows accessibility preferences; never change the user's system settings.

Contracts: Microsoft Learn, SystemParametersInfoW and GetSysColor (winuser.h).
All calls are injectable, and unsupported systems retain ordinary application defaults.
"""
import ctypes
from dataclasses import dataclass
from typing import Tuple

from src.ui.theme import tokens
from src.ui.theme.contrast import hex_to_rgb, relative_luminance

SPI_GETHIGHCONTRAST = 0x0042
SPI_GETCLIENTAREAANIMATION = 0x1042
SPI_GETMESSAGEDURATION = 0x2016
HCF_HIGHCONTRASTON = 1
COLOR_WINDOW, COLOR_WINDOWTEXT = 5, 8
COLOR_HIGHLIGHT, COLOR_HIGHLIGHTTEXT, COLOR_HOTLIGHT = 13, 14, 26


class HIGHCONTRAST(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwFlags', ctypes.c_uint), ('lpszDefaultScheme', ctypes.c_wchar_p)]


@dataclass(frozen=True)
class Preferences:
    high_contrast: bool = False
    animations: bool = True
    notification_ms: int = 5000
    colours: Tuple[str, ...] = ()


def colour_from_colorref(value):
    return '#%02X%02X%02X' % (value & 255, (value >> 8) & 255, (value >> 16) & 255)


def read_preferences(user32=None):
    try:
        api = user32 if user32 is not None else ctypes.windll.user32
        contrast = HIGHCONTRAST()
        contrast.cbSize = ctypes.sizeof(contrast)
        high_contrast = bool(api.SystemParametersInfoW(
            SPI_GETHIGHCONTRAST, contrast.cbSize, ctypes.byref(contrast), 0) and
            contrast.dwFlags & HCF_HIGHCONTRASTON)
        animation = ctypes.c_int(1)
        api.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(animation), 0)
        duration = ctypes.c_ulong(5)
        api.SystemParametersInfoW(SPI_GETMESSAGEDURATION, 0, ctypes.byref(duration), 0)
        colours = tuple(colour_from_colorref(api.GetSysColor(index)) for index in
                         (COLOR_WINDOW, COLOR_WINDOWTEXT, COLOR_HIGHLIGHT, COLOR_HIGHLIGHTTEXT, COLOR_HOTLIGHT)) \
            if high_contrast else ()
        return Preferences(high_contrast, bool(animation.value), max(0, int(duration.value)) * 1000, colours)
    except (AttributeError, OSError, ValueError):
        return Preferences()


def contrast_palette(preferences):
    """Keep the system's foreground/background pairs together, including selected text."""
    bg, text, accent, on_accent, link = preferences.colours
    mode = tokens.DARK_MODE if relative_luminance(hex_to_rgb(bg)) < 0.5 else tokens.LIGHT_MODE
    return tokens.Palette(mode=mode, bg=bg, surface=bg, alt=bg, border=text, strong=text,
                          text=text, muted=text, accent=accent, on_accent=on_accent, hover=accent,
                          soft=accent, soft_text=on_accent, warm=bg, on_warm=text, success=text,
                          warning=text, danger=text, focus=text, selection=accent, ruby=link, link=bg)


def text_scale(value):
    try:
        return min(1.5, max(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def remap_widgets(widget, old, new):
    """Repaint explicit Tk colours, retaining role metadata across contrast-mode round trips."""
    import tkinter as tk
    foreground = ('text', 'muted', 'ruby', 'soft_text', 'on_accent', 'accent', 'success', 'warning', 'danger', 'on_warm')
    background = ('surface', 'alt', 'bg', 'soft', 'selection', 'border', 'strong', 'accent', 'link')

    def role_for(value, roles):
        return next((role for role in roles if str(getattr(old, role)).lower() == str(value).lower()), None)

    try:
        keys = widget.keys()
        known = getattr(widget, '_ct_colour_roles', {})
        for option in ('background', 'foreground', 'insertbackground', 'selectbackground',
                       'selectforeground', 'inactiveselectbackground', 'highlightbackground', 'highlightcolor'):
            if option not in keys:
                continue
            value = widget.cget(option)
            if not value:
                continue
            if option in ('selectbackground', 'inactiveselectbackground'):
                role = 'selection'
            elif option == 'selectforeground':
                role = 'selection_text'
            elif option == 'insertbackground':
                role = 'text'
            elif option.startswith('highlight'):
                role = known.get(option) or role_for(value, ('focus', 'strong', 'border'))
            else:
                roles = foreground if option == 'foreground' else background
                role = known.get(option) or role_for(value, roles)
            if role:
                known[option] = role
                widget.configure(**{option: getattr(new, role)})
        widget._ct_colour_roles = known
        if isinstance(widget, tk.Text):
            tag_roles = getattr(widget, '_ct_tag_roles', {})
            for tag in widget.tag_names():
                if tag == 'sel':
                    widget.tag_configure(tag, background=new.selection, foreground=new.selection_text)
                    continue
                if tag == 'linked_reading':
                    widget.tag_configure(tag, underline=new.bg == new.surface == new.alt)
                for option, roles in (('foreground', foreground), ('background', background)):
                    value = widget.tag_cget(tag, option)
                    key = (tag, option)
                    role = tag_roles.get(key) or role_for(value, roles) if value else None
                    if role:
                        tag_roles[key] = role
                        widget.tag_configure(tag, **{option: getattr(new, role)})
            widget._ct_tag_roles = tag_roles
            for attr in ('_base_fg', '_kanji_fg', '_ruby_fg', '_ruby_bg'):
                if hasattr(widget, attr):
                    value = getattr(widget, attr)
                    roles = background if attr.endswith('bg') else foreground
                    role = known.get(attr) or ('ruby' if attr == '_ruby_fg' else role_for(value, roles))
                    if role:
                        known[attr] = role
                        setattr(widget, attr, getattr(new, role))
            if hasattr(widget, '_window_colors'):
                window_roles = getattr(widget, '_ct_window_roles', {})
                updated = {}
                for key, colours in widget._window_colors.items():
                    roles = window_roles.get(key)
                    if roles is None:
                        roles = tuple('ruby' if i == 2 else role_for(colour, background if i == 0 else foreground)
                                      for i, colour in enumerate(colours))
                    window_roles[key] = roles
                    updated[key] = tuple(getattr(new, role) if role else colour
                                          for role, colour in zip(roles, colours))
                widget._ct_window_roles = window_roles
                widget._window_colors = updated
        callback = getattr(widget, '_ct_retheme', None)
        if callback:
            callback(new)
        for child in widget.winfo_children():
            remap_widgets(child, old, new)
    except tk.TclError:
        pass

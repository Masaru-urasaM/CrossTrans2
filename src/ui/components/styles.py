"""
The ttk styles behind the components (TODO.md U4b): rounded buttons, chips, cards, fields,
switches, thin scroll bars - every one an image element (a rounded rectangle drawn once per state
by ttkbootstrap's `Assets`) in a ttk layout, with every colour read from the palette.

ttk keeps its styles per ttk *theme* and a ttkbootstrap theme is one ttk theme, so the styles are
built once for `crosstrans-light` and once for `crosstrans-dark`, the first time each is in force
(`Style.on_theme_change` calls `ensure()` after every switch). A widget names its style
(`CtAccentSurface.TButton`); the theme in force supplies the look.

A rounded image has transparent corners, and ttk draws a widget's own `background` behind it - so
every style exists for the two planes a component can sit on, the window (`bg`) and a card
(`surface`), with that plane's colour as the corner colour.

Names: `Ct<Kind><Plane>.T<Class>`, e.g. CtAccentBg.TButton, CtSoftSurface.TButton,
CtChipSurface.TLabel, CtCardBg.TFrame, CtFieldSurface.TEntry, CtSwitchSurface.TCheckbutton,
CtBodySurface.TLabel, CtThin.Vertical.TScrollbar.
"""
import logging
from typing import Dict, Optional, Tuple

from src.ui.theme import tokens
from src.ui.theme.contrast import BLACK, WHITE, blend
from src.ui.components.context import PLANES, Context

PREFIX = 'Ct'
BUTTON_KINDS = ('accent', 'soft', 'subtle', 'danger')
INFO_KINDS = ('info', 'success', 'warning', 'danger')
LABEL_KINDS = ('body', 'muted', 'strong', 'subtitle', 'title', 'caption', 'reading', 'mono')
LABEL_FONT = {'body': 'body', 'muted': 'caption', 'strong': 'body_strong', 'subtitle': 'subtitle',
               'title': 'title', 'caption': 'caption', 'reading': 'reading', 'mono': 'mono'}

# A card on each plane: what it is filled with, and so the plane its children sit on.
CARD_FILL = {'bg': lambda p: p.surface, 'surface': lambda p: p.alt, 'alt': lambda p: p.surface}
INNER_PLANE = {'bg': 'surface', 'surface': 'alt', 'alt': 'surface'}

# The middle of a 9-slice is TILED, not stretched: ttk copies it as many times as the widget is
# wide and tall, so a 4 px middle under a 900 px bar is ~50,000 copies per redraw (measured: 25 s to
# lay out eight info bars). A large middle cuts that by its area.
SLICE_CENTRE = 64          # buttons, chips, fields: a few hundred px at most
SLICE_CENTRE_LARGE = 192   # cards and info bars, which can span a whole window

_built: Dict[int, set] = {}          # id(context) -> ttk theme names already built


def name(kind: str, plane: str, ttk_class: str) -> str:
    """'CtAccentBg.TButton' for ('accent', 'bg', 'TButton')."""
    return f"{PREFIX}{kind[:1].upper()}{kind[1:]}{plane.capitalize()}.{ttk_class}"


def shift(colour: str, amount: float, dark: bool) -> str:
    """`colour` made a little darker (light theme) or lighter (dark theme): a hover or pressed tint."""
    return blend(WHITE if dark else BLACK, colour, amount)


def infobar_colours(p: tokens.Palette, kind: str, plane_colour: str) -> dict:
    """The tint, edge and hover colours of an info bar of `kind` on a plane."""
    base = {'info': p.accent, 'success': p.success, 'warning': p.warning, 'danger': p.danger}[kind]
    if p.bg == p.surface == p.alt:
        return dict(base=p.text, tint=p.bg, edge=p.text, hover=p.bg)
    return dict(base=base, tint=blend(base, plane_colour, 0.13), edge=blend(base, plane_colour, 0.40),
                hover=blend(base, plane_colour, 0.24))


def disabled(colour: str, plane_colour: str) -> str:
    """`colour` faded toward the plane it sits on."""
    return blend(colour, plane_colour, 0.38)


class Builder:
    """Builds every component style for one palette into the ttk theme in force."""

    def __init__(self, ctx: Context):
        from ttkbootstrap.style.assets import Assets
        self.ctx = ctx
        self.st = ctx.style
        self.p = ctx.palette
        self.dark = self.p.mode == tokens.DARK_MODE
        self.assets = Assets(ctx.style)          # for icons
        self.logical = ctx.style.scaling.logical
        # The colour behind the images being drawn. Every style exists per plane, so the corners
        # are baked in that colour: an opaque image is drawn by Tk with block copies, about twice as
        # fast as one with transparent corners (per-pixel alpha), which matters for a wide card.
        self._under: Optional[str] = None

    # --- tiny helpers ---------------------------------------------------------------------------
    def px(self, value) -> int:
        return self.ctx.px(value)

    def border(self, value: int) -> int:
        return max(1, self.logical(value))

    def rr(self, fill, size: Tuple[int, int], radius: int, outline: Optional[str] = None, width: int = 0,
           under: Optional[str] = None):
        """A rounded rectangle as a Tk image name (logical sizes in, physical pixels drawn). Held
        by the context so it lives as long as the application; identical requests share one."""
        from src.ui.components import render
        under = self._under if under is None else under
        key = ('rr', fill, size, radius, outline, width, under, self.ctx.scale)
        photo = self.ctx._images.get(key)
        if photo is None:
            w, h = (self.logical(v, minimum=1) for v in size)
            ow = self.logical(width, minimum=1) if width else 0
            photo = render.to_photo(self.ctx.root, render.rounded_rect(
                w, h, self.logical(radius), fill, outline, ow, under), shared=True)
            self.ctx._images[key] = photo
        return str(photo)

    @staticmethod
    def slice_size(border: int, large: bool = False) -> Tuple[int, int]:
        """An image for a 9-slice with `border`: four corners and a large tiled middle. (An image of
        exactly 2 x border has no middle and draws only its corners.)"""
        edge = 2 * border + (SLICE_CENTRE_LARGE if large else SLICE_CENTRE)
        return edge, edge

    def element(self, element_name: str, default, states: Optional[dict] = None, border: int = 8, **opts):
        """A stretchable nine-slice whose images live as long as their Tcl theme."""
        opts.setdefault('width', 1)
        opts.setdefault('height', 1)
        self._image_element(element_name, default, states,
                            border=self.logical(border, minimum=1) if border else 0,
                            padding=0, sticky='nsew', **opts)

    def _image_element(self, element_name, default, states=None, **options):
        """Publish and retain both nine-slice backgrounds and natural-size indicators."""
        from ttkbootstrap.style.layout import image_element
        if element_name in self.st.element_names():
            return
        image_element(self.st, element_name, default=default, states=states, **options)
        root = self.ctx.root
        anchors = getattr(root, '_ct_theme_image_anchors', None)
        if anchors is None:
            anchors = root._ct_theme_image_anchors = {}
            root.bind('<Destroy>', lambda event, owner=root, images=anchors:
                      images.clear() if event.widget is owner else None, add='+')
        photos = {str(photo): photo for photo in
                  (*self.ctx._images.values(), *self.ctx._icons.values())}
        referenced = (default, *(states or {}).values())
        anchors[(str(self.st.theme_use()), element_name)] = tuple(
            photos[str(image)] for image in referenced if str(image) in photos)

    def layout(self, style_name: str, root):
        from ttkbootstrap.style.layout import layout
        layout(self.st, style_name, root)

    def configure(self, style_name: str, **options):
        """`ttk.Style.configure` itself: ttkbootstrap's override would send an unknown name
        through its bootstyle parser. The name is registered so a ttkbootstrap widget keeps it."""
        from tkinter import ttk as tkttk
        tkttk.Style.configure(self.st, style_name, **options)
        self.st._register_ttkstyle(style_name)
        # A style named `X.TButton` inherits ttkbootstrap's TButton state maps, which recolour the
        # widget's *background* on hover, press and disable - a square showing behind the rounded
        # image's corners. These styles paint with images; the background stays the plane's.
        fixed = {}
        if 'background' in options:
            fixed['background'] = options['background']
        if 'fieldbackground' in options:
            fixed['fieldbackground'] = options['fieldbackground']
        for option, colour in fixed.items():
            tkttk.Style.map(self.st, style_name, **{option: [
                (state, colour) for state in ('disabled', 'pressed', 'active', 'selected', 'focus',
                                              'readonly', 'invalid', 'alternate')]})

    # --- the whole set --------------------------------------------------------------------------
    def build(self) -> None:
        radius = tokens.RADIUS['control']
        for plane in PLANES:
            self._under = self.ctx.plane_colour(plane)
            for kind in BUTTON_KINDS:
                self.button(kind, plane, radius)
            self.icon_button(plane, radius)
            self.keycap(plane)
            self.navigation(plane, radius)
            for kind in INFO_KINDS:
                self.infobar(kind, plane)
            self.chip(plane)
            self.choice(plane)
            self.frame(plane)
            self.card(plane)
            self.labels(plane)
            self.field(plane, radius)
            self.switch(plane)
            self.separator(plane)
        self.accent_status(radius)
        self._under = self.p.bg
        self.scrollbars()
        self.progress()

    def accent_status(self, radius: int) -> None:
        """A compact status surface; ordinary cards keep their existing planes."""
        p = self.p
        self._under = p.accent
        self.frame('accent')
        self.configure(name('body', 'accent', 'TLabel'), background=p.accent,
                       foreground=p.on_accent, font=self.ctx.font('body'))
        self.icon_button('accent', radius)

    # --- buttons --------------------------------------------------------------------------------
    def button_spec(self, kind: str, plane_colour: str) -> dict:
        p, dark = self.p, self.dark
        if kind == 'accent':
            fill, fg, ring = p.accent, p.on_accent, p.text
            hover, pressed = p.hover, shift(p.hover, 0.12, dark)
        elif kind == 'danger':
            fill, fg, ring = p.danger, p.on_accent, p.text
            hover, pressed = shift(fill, 0.10, dark), shift(fill, 0.18, dark)
        elif kind == 'soft':
            fill, fg, ring = p.soft, p.soft_text, p.focus
            hover, pressed = blend(p.soft_text, p.soft, 0.10), blend(p.soft_text, p.soft, 0.18)
        else:                                                    # subtle
            fill, fg, ring = None, p.text, p.focus
            hover, pressed = p.alt, blend(p.text, p.alt, 0.08)
        return dict(fill=fill, hover=hover, pressed=pressed, fg=fg, ring=ring,
                    off_fill=disabled(fill, plane_colour) if fill else None,
                    off_fg=disabled(p.muted, plane_colour))

    def rounded_states(self, spec: dict, size: Tuple[int, int], radius: int) -> Tuple[object, dict]:
        """(normal image, ordered states) for a filled/transparent rounded control."""
        ring_w = 2
        normal = self.rr(spec['fill'], size, radius)
        states = {
            'disabled': self.rr(spec['off_fill'], size, radius),
            'focus pressed': self.rr(spec['pressed'], size, radius, spec['ring'], ring_w),
            'pressed': self.rr(spec['pressed'], size, radius),
            'focus active': self.rr(spec['hover'], size, radius, spec['ring'], ring_w),
            'active': self.rr(spec['hover'], size, radius),
            'focus': self.rr(spec['fill'], size, radius, spec['ring'], ring_w),
        }
        return normal, states

    def button(self, kind: str, plane: str, radius: int) -> None:
        pc = self.ctx.plane_colour(plane)
        spec = self.button_spec(kind, pc)
        size = self.slice_size(radius + 2)
        normal, states = self.rounded_states(spec, size, radius)
        style = name(kind, plane, 'TButton')
        element = style.replace('.TButton', '.Button.bg')
        self.element(element, normal, states, border=radius + 2)
        self.layout(style, _el(element, sticky='nsew', children=[
            _el('Button.padding', sticky='nsew', children=[_el('Button.label', sticky='nsew')])]))
        self.configure(style, font=self.ctx.font('body_strong'), foreground=spec['fg'],
                          background=pc, padding=(self.px(16), self.px(6)), anchor='center',
                          width=0, borderwidth=0)
        self._map(style, foreground={'disabled': spec['off_fg']})

    def icon_button(self, plane: str, radius: int) -> None:
        """The subtle button, square: an icon image and nothing else - and its 'on' twin, a soft
        tinted face, for an icon that is a switch (Linked reading in the popup).

        Always CONTROL_HEIGHT square (`IconButton` pads its icon to fit), so its face is ONE image
        drawn once, not a nine-piece slice: half the drawing time of a slice (measured), which a
        popup with seven of them notices.
        """
        pc = self.ctx.plane_colour(plane)
        for kind, base in (('icon', 'subtle'), ('iconon', 'soft')):
            spec = self.button_spec(base, pc)
            if plane == 'accent' and base == 'subtle':
                spec.update(hover=self.p.hover, pressed=shift(self.p.hover, 0.12, self.dark),
                            fg=self.p.on_accent, ring=self.p.on_accent)
            size = (tokens.CONTROL_HEIGHT, tokens.CONTROL_HEIGHT)
            normal, states = self.rounded_states(spec, size, radius)
            style = name(kind, plane, 'TButton')
            element = style.replace('.TButton', '.Button.bg')
            self.element(element, normal, states, border=0)
            self.layout(style, _el(element, sticky='nsew', children=[
                _el('Button.padding', sticky='nsew', children=[_el('Button.label', sticky='nsew')])]))
            self.configure(style, background=pc, padding=self.px(8), anchor='center', width=0,
                              borderwidth=0, foreground=self.p.on_accent if plane == 'accent' else self.p.muted)

    # --- chips: a static pill label, and a pill you can choose ------------------------------------
    def chip(self, plane: str) -> None:
        pc = self.ctx.plane_colour(plane)
        p = self.p
        for kind, fill, fg in (('chip', p.soft, p.soft_text), ('tag', p.alt, p.text)):
            pill = 14
            image = self.rr(fill, self.slice_size(pill), pill)
            style = name(kind, plane, 'TLabel')
            element = style.replace('.TLabel', '.Label.bg')
            self.element(element, image, border=pill)
            self.layout(style, _el(element, sticky='nsew', children=[
                _el('Label.padding', sticky='nsew', children=[_el('Label.label', sticky='nsew')])]))
            self.configure(style, font=self.ctx.font('caption' if kind == 'tag' else 'body_strong'),
                              foreground=fg, background=pc,
                              padding=(self.px(12), self.px(4)), anchor='center')

    def choice(self, plane: str) -> None:
        """A pill you can pick (a tone, a language pair, a filter): a Radiobutton or a Checkbutton
        with no indicator, `soft` and outlined in the accent when selected."""
        pc, p = self.ctx.plane_colour(plane), self.p
        pill = 15
        size = self.slice_size(pill)
        edge = blend(p.strong, pc, 0.55)
        off = self.rr(p.alt, size, pill, edge, 1)
        states = {
            'disabled selected': self.rr(disabled(p.soft, pc), size, pill, disabled(p.accent, pc), 1),
            'disabled': self.rr(disabled(p.alt, pc), size, pill),
            'focus selected': self.rr(p.soft, size, pill, p.focus, 2),
            'selected': self.rr(p.soft, size, pill, p.accent, 1),
            'focus active': self.rr(blend(p.text, p.alt, 0.10), size, pill, p.focus, 2),
            'active': self.rr(blend(p.text, p.alt, 0.10), size, pill, edge, 1),
            'focus': self.rr(p.alt, size, pill, p.focus, 2),
        }
        for ttk_class in ('TRadiobutton', 'TCheckbutton'):
            style = name('choice', plane, ttk_class)
            base = ttk_class[1:]
            element = f"{PREFIX}Choice{plane.capitalize()}.{base}.bg"
            self.element(element, off, states, border=pill)
            self.layout(style, _el(element, sticky='nsew', children=[
                _el(f'{base}.padding', sticky='nsew', children=[_el(f'{base}.label', sticky='nsew')])]))
            self.configure(style, font=self.ctx.font('body'), foreground=p.text, background=pc,
                              padding=(self.px(14), self.px(5)), anchor='center')
            self._map(style, foreground={'disabled': disabled(p.muted, pc),
                                         'selected': p.soft_text})

    # --- planes -----------------------------------------------------------------------------------
    def frame(self, plane: str) -> None:
        self.configure(name('plane', plane, 'TFrame'), background=self.ctx.plane_colour(plane))

    def card(self, plane: str) -> None:
        """A rounded surface with a hairline border, to sit on `plane`."""
        pc, p = self.ctx.plane_colour(plane), self.p
        radius = tokens.RADIUS['card']
        fill = CARD_FILL[plane](p)
        image = self.rr(fill, self.slice_size(radius + 2, large=True), radius, p.border, 1, under=pc)
        style = name('card', plane, 'TFrame')
        element = style.replace('.TFrame', '.Frame.bg')
        self.element(element, image, border=radius + 2)
        self.layout(style, _el(element, sticky='nsew', children=[_el('Frame.padding', sticky='nsew')]))
        self.configure(style, background=pc)

        # The same card while a file is dragged over it: the fill stays (its children are painted
        # for it), the hairline becomes a 2 px accent ring
        ring = self.rr(fill, self.slice_size(radius + 2, large=True), radius, p.accent, 2, under=pc)
        drop = name('drop', plane, 'TFrame')
        drop_element = drop.replace('.TFrame', '.Frame.bg')
        self.element(drop_element, ring, border=radius + 2)
        self.layout(drop, _el(drop_element, sticky='nsew', children=[_el('Frame.padding', sticky='nsew')]))
        self.configure(drop, background=pc)

    def labels(self, plane: str) -> None:
        pc, p = self.ctx.plane_colour(plane), self.p
        for kind in LABEL_KINDS:
            fg = p.muted if kind in ('muted', 'caption') else p.text
            self.configure(name(kind, plane, 'TLabel'), font=self.ctx.font(LABEL_FONT[kind]),
                           foreground=fg, background=pc)

    def separator(self, plane: str) -> None:
        """A horizontal hairline in the palette's `border`. ttkbootstrap draws a separator from an image
        baked in its own border colour - the stronger one our field edges use - so `background=` on the
        style changes nothing (measured: the Settings rows' hairlines came out #888581 on white, not the
        palette's #E3DED7). This draws its own, one pixel high."""
        style = name('line', plane, 'TSeparator')
        element = style.replace('.TSeparator', '.Separator.line')
        self.element(element, self.rr(self.p.border, (8, 1), 0), border=0)
        self.layout(style, _el(element, sticky='ew'))

    # --- fields -----------------------------------------------------------------------------------
    def field(self, plane: str, radius: int) -> None:
        pc, p = self.ctx.plane_colour(plane), self.p
        fill = p.bg if plane == 'surface' else p.surface
        size = self.slice_size(radius + 3)
        normal = self.rr(fill, size, radius, p.strong, 1)
        states = {
            'disabled': self.rr(disabled(fill, pc), size, radius, p.border, 1),
            'invalid': self.rr(fill, size, radius, p.danger, 2),
            'focus': self.rr(fill, size, radius, p.accent, 2),
            'active': self.rr(fill, size, radius, p.text, 1),
        }
        style = name('field', plane, 'TEntry')
        element = style.replace('.TEntry', '.Entry.bg')
        self.element(element, normal, states, border=radius + 3)
        self.layout(style, _el(element, sticky='nsew', children=[
            _el('Entry.padding', sticky='nsew', children=[_el('Entry.textarea', sticky='nsew')])]))
        self.configure(style, font=self.ctx.font('body'), foreground=p.text, background=pc,
                          fieldbackground=fill, insertcolor=p.text, selectbackground=p.selection,
                          selectforeground=p.selection_text, padding=(self.px(10), self.px(6)))
        self._map(style, foreground={'disabled': disabled(p.muted, pc)})

    # --- switch -------------------------------------------------------------------------------------
    def switch(self, plane: str) -> None:
        pc, p = self.ctx.plane_colour(plane), self.p
        w, h, margin = 44, 24, 2                                   # the margin holds the focus ring

        def image(selected: bool, state: str):
            from PIL import Image, ImageDraw
            scale = self.ctx.scale
            W, H = max(1, round(w * scale)), max(1, round(h * scale))
            factor = 4
            from src.ui.components.render import _rgba
            im = Image.new('RGBA', (W * factor, H * factor), _rgba(pc))
            d = ImageDraw.Draw(im)
            m = round(margin * scale * factor)
            track = p.accent if selected else p.alt
            outline = None if selected else p.strong
            knob = p.on_accent if selected else p.strong
            if state == 'active':
                track = p.hover if selected else blend(p.text, p.alt, 0.10)
            if state == 'disabled':
                track, knob = disabled(track, pc), disabled(knob, pc)
                outline = None if selected else p.border
            if state == 'focus':
                d.rounded_rectangle((0, 0, W * factor - 1, H * factor - 1),
                                    radius=(H * factor) // 2, outline=p.focus,
                                    width=max(1, round(2 * scale * factor)))
            d.rounded_rectangle((m, m, W * factor - 1 - m, H * factor - 1 - m),
                                radius=(H * factor - 2 * m) // 2, fill=track, outline=outline,
                                width=max(1, round(1 * scale * factor)) if outline else 0)
            ih = H * factor - 2 * m
            r = ih * 0.30
            cx = (W * factor - m - ih / 2) if selected else (m + ih / 2)
            cy = H * factor / 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=knob)
            from src.ui.components import render
            photo = render.to_photo(self.ctx.root, render.shrink(im, (W, H)), shared=True)
            self.ctx._icons[(plane, selected, state, self.p.mode)] = photo     # keep it alive
            return str(photo)

        style = name('switch', plane, 'TCheckbutton')
        element = f"{PREFIX}Switch{plane.capitalize()}.indicator"
        self._image_element(element, default=image(False, ''), states={
            'selected disabled': image(True, 'disabled'),
            'disabled': image(False, 'disabled'),
            'selected focus': image(True, 'focus'),
            'focus': image(False, 'focus'),
            'selected active': image(True, 'active'),
            'selected': image(True, ''),
            'active': image(False, 'active'),
        }, sticky='')
        self.layout(style, _el('Checkbutton.padding', sticky='nsew', children=[
            _el(element, side='left', sticky=''),
            _el('Checkbutton.label', side='left', sticky='nsew')]))
        self.configure(style, font=self.ctx.font('body'), foreground=p.text, background=pc,
                          padding=(0, self.px(2)))
        self._map(style, foreground={'disabled': disabled(p.muted, pc)})

    # --- keycaps, navigation, info bars ---------------------------------------------------------------
    def keycap(self, plane: str) -> None:
        """A key on a keyboard: 'Win', 'Alt', 'E' in a shortcut."""
        pc, p = self.ctx.plane_colour(plane), self.p
        radius = 5
        image = self.rr(p.alt, self.slice_size(radius + 1), radius, p.strong, 1)
        style = name('key', plane, 'TLabel')
        element = style.replace('.TLabel', '.Label.bg')
        self.element(element, image, border=radius + 1)
        self.layout(style, _el(element, sticky='nsew', children=[
            _el('Label.padding', sticky='nsew', children=[_el('Label.label', sticky='nsew')])]))
        self.configure(style, font=self.ctx.font('body_strong'), foreground=p.text, background=pc,
                       padding=(self.px(8), self.px(2)), anchor='center')

    def navigation(self, plane: str, radius: int) -> None:
        """A left-hand navigation entry: soft fill and a short accent bar when selected."""
        pc, p = self.ctx.plane_colour(plane), self.p
        border = radius + 2
        size = self.slice_size(border)
        w, h = size
        from src.ui.components import render
        def selected_image(fill, ring=None):
            W, H = (self.logical(v, minimum=1) for v in size)
            base = render.rounded_rect(W, H, self.logical(radius), fill, ring,
                                       self.logical(2) if ring else 0, under=pc)
            bar_w = max(2, self.logical(3))
            x0 = self.logical(4)
            bar = render.rounded_rect(bar_w, max(4, H - 2 * self.logical(border) + 4), bar_w // 2, p.accent)
            canvas = base.copy()
            canvas.alpha_composite(bar, (x0, (H - bar.height) // 2))
            photo = render.to_photo(self.ctx.root, canvas, shared=True)
            self.ctx._icons[('nav', plane, str(fill), str(ring), p.mode)] = photo
            return str(photo)
        hover = blend(p.text, pc, 0.07)
        states = {
            'disabled': self.rr(None, size, radius),
            'focus selected': selected_image(p.soft, p.focus),
            'selected': selected_image(p.soft),
            'focus active': self.rr(hover, size, radius, p.focus, 2),
            'active': self.rr(hover, size, radius),
            'focus': self.rr(None, size, radius, p.focus, 2),
        }
        style = name('nav', plane, 'TRadiobutton')
        element = style.replace('.TRadiobutton', '.Radiobutton.bg')
        self.element(element, self.rr(None, size, radius), states, border=border)
        self.layout(style, _el(element, sticky='nsew', children=[
            _el('Radiobutton.padding', sticky='nsew', children=[_el('Radiobutton.label', sticky='nsew')])]))
        self.configure(style, font=self.ctx.font('body'), foreground=p.text, background=pc,
                       padding=(self.px(18), self.px(8), self.px(12), self.px(8)), anchor='w')
        self._map(style, foreground={'selected': p.soft_text, 'disabled': disabled(p.muted, pc)})

    def infobar(self, kind: str, plane: str) -> None:
        """An InfoBar: a tinted rounded panel and the label, link and icon styles that sit on it."""
        pc, p = self.ctx.plane_colour(plane), self.p
        colours = infobar_colours(p, kind, pc)
        tint = colours['tint']
        radius = tokens.RADIUS['card']
        image = self.rr(tint, self.slice_size(radius + 2, large=True), radius, colours['edge'], 1)
        key = f"Info{kind.capitalize()}"
        frame = name(key, plane, 'TFrame')
        element = frame.replace('.TFrame', '.Frame.bg')
        self.element(element, image, border=radius + 2)
        self.layout(frame, _el(element, sticky='nsew', children=[_el('Frame.padding', sticky='nsew')]))
        self.configure(frame, background=pc)
        self.configure(name(f"{key}Title", plane, 'TLabel'), font=self.ctx.font('body_strong'),
                       foreground=p.text, background=tint)
        self.configure(name(f"{key}Text", plane, 'TLabel'), font=self.ctx.font('body'),
                       foreground=p.text, background=tint)
        # a text link ("Open settings") and a flat icon button (close), both on the tint
        link = name(f"{key}Link", plane, 'TButton')
        self.layout(link, _el('Button.padding', sticky='nsew', children=[_el('Button.label', sticky='nsew')]))
        underline = self.ctx.font('body_strong') + ('underline',)
        self.configure(link, font=underline, foreground=p.soft_text if kind == 'info' else p.text,
                       background=tint, padding=(self.px(6), self.px(2)), borderwidth=0, relief='flat')
        self._map(link, foreground={'active': p.text})
        close = name(f"{key}Close", plane, 'TButton')
        self.layout(close, _el('Button.padding', sticky='nsew', children=[_el('Button.label', sticky='nsew')]))
        self.configure(close, background=tint, padding=self.px(4), borderwidth=0, relief='flat')
        self._map(close, background={'active': colours['hover'], 'pressed': colours['edge']})

    # --- scroll bars and progress -------------------------------------------------------------------
    def scrollbars(self) -> None:
        p = self.p
        for orient in ('Vertical', 'Horizontal'):
            vertical = orient == 'Vertical'
            thumb = (12, 32) if vertical else (32, 12)
            thickness = 12
            normal = self.rr(blend(p.text, p.bg, 0.28), thumb, 4)
            states = {'pressed': self.rr(blend(p.text, p.bg, 0.55), thumb, 4),
                      'active': self.rr(blend(p.text, p.bg, 0.42), thumb, 4)}
            style = f"{PREFIX}Thin.{orient}.TScrollbar"
            element = f"{PREFIX}Thin.{orient}.thumb"
            # the thumb's minimum length is part of the bar's size: a bar asking for 1 px along
            # its length is not arranged by grid when it is added to a laid-out parent
            length = self.logical(24, minimum=1)
            self.element(element, normal, states, border=4,
                         **({'width': self.logical(thickness, minimum=1), 'height': length} if vertical
                            else {'height': self.logical(thickness, minimum=1), 'width': length}))
            self.layout(style, _el(f'{orient}.Scrollbar.trough', sticky='ns' if vertical else 'ew',
                                   children=[_el(element, sticky='nsew')]))
            self.configure(style, troughcolor=p.bg, background=p.bg, borderwidth=0,
                              relief='flat', arrowsize=0)

    def progress(self) -> None:
        p = self.p
        height = 8
        track = self.rr(p.alt, (height + 12, height), height // 2)
        bar = self.rr(p.accent, (height + 12, height), height // 2)
        style = f"{PREFIX}Bar.Horizontal.TProgressbar"
        self.element(f"{PREFIX}Bar.trough", track, border=height // 2, height=self.logical(height, minimum=1))
        self.element(f"{PREFIX}Bar.pbar", bar, border=height // 2, height=self.logical(height, minimum=1))
        self.layout(style, _el(f"{PREFIX}Bar.trough", sticky='nsew', children=[
            _el(f"{PREFIX}Bar.pbar", side='left', sticky='ns')]))
        self.configure(style, background=p.bg, troughcolor=p.bg, borderwidth=0)

    # --- ttk plumbing ---------------------------------------------------------------------------------
    def _map(self, style: str, **options) -> None:
        from ttkbootstrap.style.layout import state_map
        state_map(self.st, style, **options)


def _el(element_name, **kw):
    from ttkbootstrap.style.layout import El
    return El(element_name, **kw)


# --- when to build ----------------------------------------------------------------------------------
def ensure(ctx: Context) -> bool:
    """Build the styles for the ttk theme now in force, once. True when it built."""
    theme = str(ctx.style.theme_use())
    done = _built.setdefault(id(ctx), set())
    if theme in done:
        return False
    try:
        Builder(ctx).build()
    except Exception:
        logging.exception("[Components] could not build the styles for %s", theme)
        raise
    done.add(theme)
    return True


def install(ctx: Context) -> None:
    """Build for the theme in force and for every later switch."""
    def on_change(style):
        ensure(ctx)
    ctx._on_change = on_change
    ctx.style.on_theme_change(on_change)          # runs once now as well


def uninstall(ctx: Context) -> None:
    callback = getattr(ctx, '_on_change', None)
    if callback is not None:
        try:
            ctx.style.remove_theme_change_callback(callback)
        except Exception:
            pass
        finally:
            ctx._on_change = None
    _built.pop(id(ctx), None)

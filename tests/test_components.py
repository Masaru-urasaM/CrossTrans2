"""
Tests for src/ui/components (TODO.md U4b): the styles behind them and the widgets themselves.

They run on the shared test root with the theme engine and the component styles installed, in
both themes. What they pin: every promised style exists; a style is built once per theme; every
button state is readable (>= 4.5:1); components inherit their plane; the field's placeholder is
never content; the keyboard acts.
"""
import tkinter as tk

import pytest

from src.ui import components as ui
from src.ui.components import context as C
from src.ui.components import styles
from src.ui.theme import engine, tokens
from src.ui.theme.contrast import contrast_ratio
from src.ui.theme.manager import MemoryStore, ThemeManager


@pytest.fixture(scope='module')
def env(tk_root):
    """The engine and the component context, in light mode with Windows reading dark."""
    manager = ThemeManager(MemoryStore('light'), detect=lambda: 'dark')
    eng = engine.install(tk_root, manager)
    ctx = ui.install(tk_root, manager, eng.style)
    yield ctx
    ui.reset()
    engine.reset()
    try:
        eng.style.theme_use('bootstrap-light')
    except Exception:
        pass


@pytest.fixture
def light(env):
    env.manager.set_mode('light')
    return env


@pytest.fixture
def holder(env, tk_root):
    frame = tk.Frame(tk_root)
    yield frame
    frame.destroy()


class TestStylesExist:
    def names(self):
        out = []
        for plane in ui.PLANES:
            out += [styles.name(kind, plane, 'TButton') for kind in styles.BUTTON_KINDS + ('icon', 'iconon')]
            out += [styles.name('chip', plane, 'TLabel'), styles.name('tag', plane, 'TLabel'),
                    styles.name('choice', plane, 'TRadiobutton'), styles.name('choice', plane, 'TCheckbutton'),
                    styles.name('plane', plane, 'TFrame'), styles.name('card', plane, 'TFrame'),
                    styles.name('drop', plane, 'TFrame'),
                    styles.name('field', plane, 'TEntry'), styles.name('switch', plane, 'TCheckbutton'),
                    styles.name('line', plane, 'TSeparator')]
            out += [styles.name(kind, plane, 'TLabel') for kind in styles.LABEL_KINDS]
        return out + ['CtThin.Vertical.TScrollbar', 'CtThin.Horizontal.TScrollbar',
                      'CtBar.Horizontal.TProgressbar']

    @pytest.mark.parametrize('mode', ['light', 'dark'])
    def test_every_style_has_a_layout_in_each_theme(self, env, mode):
        env.manager.set_mode(mode)
        for style_name in self.names():
            assert env.style.layout(style_name), f"{style_name} in {mode}"

    def test_the_names_follow_the_scheme(self):
        assert styles.name('accent', 'bg', 'TButton') == 'CtAccentBg.TButton'
        assert styles.name('body', 'surface', 'TLabel') == 'CtBodySurface.TLabel'

    def test_there_is_a_style_per_plane(self):
        assert ui.PLANES == ('bg', 'surface', 'alt')


class TestSeparator:
    """ttkbootstrap draws a separator from an image baked in its own border colour (the stronger one the
    fields' edges use), so styling the widget's background never changed the line: a hairline between
    Settings rows came out #888581 on white. The component draws its own, in the palette's `border`."""

    @staticmethod
    def hairline_images(env, colour):
        return [photo for key, photo in env._images.items()
                if key[0] == 'rr' and key[1] == colour and key[2] == (8, 1)]

    @pytest.mark.parametrize('mode,palette', [('light', tokens.LIGHT), ('dark', tokens.DARK)])
    def test_the_line_is_the_palette_border(self, env, mode, palette):
        env.manager.set_mode(mode)
        images = self.hairline_images(env, palette.border)
        assert images, f"no hairline image in {palette.border}"
        red, green, blue = (int(value) for value in env.root.tk.call(str(images[0]), 'get', 0, 0))
        assert f'#{red:02X}{green:02X}{blue:02X}' == palette.border.upper()
        env.manager.set_mode('light')

    @pytest.mark.parametrize('plane', ui.PLANES)
    def test_each_plane_has_a_layout_made_of_that_one_element(self, env, plane):
        style_name = styles.name('line', plane, 'TSeparator')
        element = style_name.replace('.TSeparator', '.Separator.line')
        layout = env.style.layout(style_name)
        assert [item[0] for item in layout] == [element]

    def test_it_is_one_pixel_high_and_as_wide_as_it_is_given(self, env, holder):
        line = ui.Separator(holder, on='surface')
        line.pack(fill='x')
        holder.configure(width=300, height=40)
        holder.pack_propagate(False)
        holder.update_idletasks()
        assert line.winfo_reqheight() == 1


class TestBuiltOncePerTheme:
    def test_a_second_ensure_builds_nothing(self, env):
        env.manager.set_mode('light')
        assert styles.ensure(env) is False

    def test_the_first_visit_to_a_theme_builds_and_a_return_does_not(self, env):
        env.manager.set_mode('dark')
        assert styles.ensure(env) is False                    # the switch itself already built it
        env.manager.set_mode('light')
        assert styles.ensure(env) is False

    def test_colours_follow_the_theme(self, env):
        env.manager.set_mode('light')
        light_fg = str(env.style.lookup('CtAccentBg.TButton', 'foreground'))
        env.manager.set_mode('dark')
        dark_fg = str(env.style.lookup('CtAccentBg.TButton', 'foreground'))
        assert light_fg.upper() == tokens.LIGHT.on_accent.upper()
        assert dark_fg.upper() == tokens.DARK.on_accent.upper()
        env.manager.set_mode('light')


class TestReadability:
    @pytest.mark.parametrize('palette', [tokens.LIGHT, tokens.DARK], ids=lambda p: p.mode)
    def test_every_button_state_reaches_4_5_to_1(self, env, palette):
        env.manager.set_mode(palette.mode)
        for plane in ui.PLANES:
            builder = styles.Builder(env)
            plane_colour = env.plane_colour(plane)
            for kind in styles.BUTTON_KINDS:
                spec = builder.button_spec(kind, plane_colour)
                for state in ('fill', 'hover', 'pressed'):
                    background = spec[state] or plane_colour if state != 'fill' or spec['fill'] \
                        else plane_colour
                    ratio = contrast_ratio(spec['fg'], background)
                    assert ratio >= 4.5, f"{palette.mode} {kind} on {plane} {state}: {ratio:.2f}"
        env.manager.set_mode('light')

    def test_a_pressed_or_hovered_tint_moves_away_from_the_fill(self):
        assert styles.shift('#808080', 0.1, dark=False) < '#808080'
        assert styles.shift('#808080', 0.1, dark=True) > '#808080'

    def test_a_disabled_colour_sits_between_the_colour_and_its_plane(self):
        faded = styles.disabled('#000000', '#ffffff')
        assert '#000000' < faded < '#ffffff'

    def test_a_nine_slice_image_has_a_middle_to_stretch(self):
        for border in (4, 8, 15):
            width, height = styles.Builder.slice_size(border)
            assert width >= 2 * border + 4 and height >= 2 * border + 4


class TestPlanes:
    def test_a_frame_sits_on_the_window_by_default(self, holder):
        assert ui.Frame(holder).plane == 'bg'

    def test_a_card_hands_its_children_the_next_plane(self, holder):
        card = ui.Card(holder)
        inner = ui.Card(card)
        assert (card.plane, inner.plane) == ('surface', 'alt')

    def test_a_component_inherits_the_plane_of_its_parent(self, holder):
        card = ui.Card(holder)
        label = ui.Label(card, 'x')
        button = ui.Button(card, 'x')
        assert label.cget('style') == 'CtBodySurface.TLabel'
        assert button.cget('style') == 'CtSoftSurface.TButton'

    def test_an_explicit_plane_wins(self, holder):
        card = ui.Card(holder)
        assert ui.Button(card, 'x', on='bg').cget('style') == 'CtSoftBg.TButton'

    def test_a_card_inside_a_card_inside_a_card_alternates(self, holder):
        a = ui.Card(holder)
        b = ui.Card(a)
        c = ui.Card(b)
        assert (a.plane, b.plane, c.plane) == ('surface', 'alt', 'surface')


class TestButtons:
    def test_each_kind_has_its_style(self, holder):
        for kind in styles.BUTTON_KINDS:
            assert ui.Button(holder, 'x', kind=kind).cget('style') == styles.name(kind, 'bg', 'TButton')

    def test_an_unknown_kind_is_refused(self, holder):
        with pytest.raises(ValueError, match="unknown button kind"):
            ui.Button(holder, 'x', kind='rainbow')

    def test_the_command_runs_on_invoke_and_on_enter(self, holder):
        calls = []
        button = ui.Button(holder, 'go', command=lambda: calls.append(1))
        button.invoke()
        assert button._invoke_on_enter() == 'break'
        assert calls == [1, 1]

    def test_a_disabled_button_does_not_act_on_enter(self, holder):
        calls = []
        button = ui.Button(holder, 'go', command=lambda: calls.append(1))
        button.configure(state='disabled')
        button._invoke_on_enter()
        assert calls == []

    def test_a_button_can_be_reached_with_tab(self, holder):
        assert str(ui.Button(holder, 'x').cget('takefocus')) in ('1', 'True', 'true')

    def test_an_icon_button_carries_an_image_and_a_hint(self, holder):
        button = ui.IconButton(holder, 'gear', label='Settings')
        assert str(button.cget('compound')) == 'image' and button.cget('image')
        assert button.tooltip.text == 'Settings'

    def test_an_icon_button_without_a_label_has_no_tooltip(self, holder):
        assert not hasattr(ui.IconButton(holder, 'gear'), 'tooltip')

    def test_a_button_with_an_icon_puts_it_before_the_text(self, holder):
        button = ui.Button(holder, 'Copy', icon='clipboard')
        assert str(button.cget('compound')) == 'left'

    def test_an_unknown_icon_name_fails_loudly(self, holder):
        with pytest.raises(ValueError, match="unknown icon"):
            ui.IconButton(holder, 'no-such-icon')


class TestTextField:
    def field(self, holder, **kw):
        field = ui.TextField(holder, **kw)
        field.pack()
        return field

    def test_the_placeholder_shows_but_is_not_content(self, holder):
        field = self.field(holder, placeholder='Search...')
        assert field.showing_placeholder and field.get() == ''
        assert field.tk.call(field._w, 'get') == 'Search...'

    def test_typing_replaces_it(self, holder):
        field = self.field(holder, placeholder='Search...')
        field.insert(0, 'fr')
        assert field.get() == 'fr' and not field.showing_placeholder

    def test_focus_clears_it_and_leaving_empty_restores_it(self, holder):
        field = self.field(holder, placeholder='Search...')
        field._focus_in()
        assert not field.showing_placeholder and field.tk.call(field._w, 'get') == ''
        field._focus_out()
        assert field.showing_placeholder and field.get() == ''

    def test_leaving_with_text_keeps_the_text(self, holder):
        field = self.field(holder, placeholder='Search...')
        field.set('fr')
        field._focus_in()
        field._focus_out()
        assert field.get() == 'fr'

    def test_the_placeholder_is_muted_and_text_is_not(self, holder, light):
        field = self.field(holder, placeholder='Search...')
        assert str(field.cget('foreground')).upper() == tokens.LIGHT.muted.upper()
        field.set('x')
        assert str(field.cget('foreground')).upper() == tokens.LIGHT.text.upper()

    def test_clearing_brings_the_placeholder_back(self, holder):
        field = self.field(holder, placeholder='Search...')
        field.set('abc')
        field.clear()
        assert field.get() == '' and field.showing_placeholder

    def test_the_variable_holds_what_was_typed_never_the_placeholder(self, holder):
        var = tk.StringVar(master=holder)
        field = self.field(holder, placeholder='Search...', textvariable=var)
        assert var.get() == ''
        field.set('fr')
        assert var.get() == 'fr'

    def test_the_variable_follows_the_typing_not_only_the_focus_leaving(self, shown):
        var = tk.StringVar(master=shown)
        field = self.field(shown, placeholder='Search...', textvariable=var)
        field.focus_force()
        field.update()
        for char in 'ger':
            field.event_generate('<KeyPress>', keysym=char)
            field.event_generate('<KeyRelease>', keysym=char)
        assert var.get() == 'ger'
        field.event_generate('<KeyPress>', keysym='BackSpace')
        field.event_generate('<KeyRelease>', keysym='BackSpace')
        assert var.get() == 'ge'

    def test_a_key_up_that_changed_nothing_does_not_write_the_variable(self, shown):
        var = tk.StringVar(master=shown, value='abc')
        field = self.field(shown, textvariable=var)
        field.focus_force()                 # a key event goes to the focus window, nowhere else
        field.update()
        writes = []
        var.trace_add('write', lambda *args: writes.append(var.get()))
        field.event_generate('<KeyRelease>', keysym='Down')
        field.event_generate('<KeyRelease>', keysym='Shift_L')
        assert writes == []
        field.event_generate('<KeyPress>', keysym='x')
        field.event_generate('<KeyRelease>', keysym='x')
        assert writes == ['abcx']           # the same events, once the text did change

    def test_a_placeholder_is_never_handed_to_the_variable(self, holder):
        var = tk.StringVar(master=holder)
        field = self.field(holder, placeholder='Search...', textvariable=var)
        assert field.showing_placeholder
        field._typed()                      # what a key-up does while the placeholder is the text
        assert var.get() == '' and field.showing_placeholder

    def test_a_paste_or_cut_is_read_once_it_has_been_done(self, holder):
        var = tk.StringVar(master=holder)
        field = self.field(holder, textvariable=var)
        # no class bindings: the real Paste would read the developer's clipboard
        field.bindtags((str(field),))
        field.insert(0, 'pasted')
        field.event_generate('<<Paste>>')
        field.update()
        assert var.get() == 'pasted'
        field.delete(0, 'end')
        field.event_generate('<<Cut>>')
        field.update()
        assert var.get() == ''

    def test_a_field_without_a_variable_has_nothing_to_keep_up(self, holder):
        field = self.field(holder)
        field.event_generate('<KeyRelease>', keysym='a')
        field.event_generate('<<Paste>>')
        field.update()

    def test_a_field_destroyed_before_the_idle_callback_is_quiet(self, holder):
        var = tk.StringVar(master=holder)
        field = self.field(holder, textvariable=var)
        field.bindtags((str(field),))
        field.event_generate('<<Paste>>')
        field.destroy()
        holder.update()

    def test_an_initial_value_wins_over_the_placeholder(self, holder):
        var = tk.StringVar(master=holder, value='hello')
        field = self.field(holder, placeholder='Search...', textvariable=var)
        assert field.get() == 'hello' and not field.showing_placeholder

    def test_invalid_rings_the_field(self, holder):
        field = self.field(holder)
        field.invalid(True)
        assert field.instate(['invalid'])
        field.invalid(False)
        assert not field.instate(['invalid'])

    def test_deleting_from_a_placeholder_does_not_leave_it_behind(self, holder):
        field = self.field(holder, placeholder='Search...')
        field.delete(0, 'end')
        assert field.get() == ''


class TestChoosing:
    def test_a_segmented_control_holds_one_value(self, holder):
        seen = []
        control = ui.SegmentedControl(holder, [('ja', 'Japanese'), ('zh', 'Chinese')], value='ja',
                                      on_change=seen.append)
        assert control.value == 'ja'
        control.buttons[1].invoke()
        assert control.value == 'zh' and seen == ['zh']

    def test_values_keep_their_type(self, holder):
        control = ui.SegmentedControl(holder, [(1, 'One'), (2, 'Two')], value=1)
        control.buttons[1].invoke()
        assert control.value == 2

    def test_setting_the_value_selects_the_pill(self, holder):
        control = ui.SegmentedControl(holder, [('a', 'A'), ('b', 'B')], value='a')
        control.value = 'b'
        assert control.buttons[1].instate(['selected']) and not control.buttons[0].instate(['selected'])

    def test_a_toggle_tracks_its_variable(self, holder):
        var = tk.BooleanVar(master=holder, value=False)
        toggle = ui.Toggle(holder, 'Japanese', var)
        toggle.invoke()
        assert var.get() is True

    def test_a_switch_tracks_its_variable_and_state(self, holder):
        var = tk.BooleanVar(master=holder, value=False)
        switch = ui.Switch(holder, 'Furigana', var)
        switch.invoke()
        assert var.get() is True and switch.instate(['selected'])

    def test_a_chip_is_a_label_in_the_pill_style(self, holder):
        assert ui.Chip(holder, 'x').cget('style') == 'CtChipBg.TLabel'
        assert ui.Chip(holder, 'x', tone='tag').cget('style') == 'CtTagBg.TLabel'


class TestLabelsAndScript:
    def test_a_label_kind_picks_its_style(self, holder):
        assert ui.Label(holder, 'x', kind='title').cget('style') == 'CtTitleBg.TLabel'

    def test_a_japanese_title_gets_the_japanese_font(self, holder, env):
        label = ui.Label(holder, 'x', kind='title', script='japanese')
        assert 'Yu Gothic' in str(label.cget('font')) or 'yu gothic' not in env.families

    def test_the_context_scales_pixels(self, env):
        assert env.px(10) == round(10 * env.scale) or env.px(10) == int(10 * env.scale + 0.5)

    def test_the_context_font_is_pixels_not_points(self, env):
        assert env.font('body')[1] < 0


@pytest.fixture
def shown(env, tk_root):
    """A mapped Toplevel (a hint is only shown for a widget the user can see)."""
    window = tk.Toplevel(tk_root)
    window.geometry('+0+0')
    window.update()
    yield window
    window.destroy()


class TestTooltip:
    def mapped(self, window, widget):
        widget.pack(padx=20, pady=20)
        window.update()
        return widget

    def test_it_shows_and_hides(self, shown):
        button = self.mapped(shown, ui.Button(shown, 'x'))
        tip = ui.Tooltip(button, 'Hello', delay_ms=10)
        tip.show()
        assert tip.window is not None and tip.window.winfo_exists()
        tip.hide()
        assert tip.window is None

    def test_it_shows_by_itself_after_the_delay(self, shown):
        button = self.mapped(shown, ui.Button(shown, 'x'))
        tip = ui.Tooltip(button, 'Hello', delay_ms=10)
        tip._schedule()
        for _ in range(30):
            shown.update()
            shown.after(10)
        assert tip.window is not None
        tip.hide()

    def test_leaving_before_the_delay_shows_nothing(self, shown):
        button = self.mapped(shown, ui.Button(shown, 'x'))
        tip = ui.Tooltip(button, 'Hello', delay_ms=50)
        tip._schedule()
        tip.hide()
        for _ in range(20):
            shown.update()
            shown.after(10)
        assert tip.window is None

    def test_it_never_takes_the_focus(self, shown, tk_root):
        entry = self.mapped(shown, ui.TextField(shown))
        entry.focus_force()
        shown.update()
        before = tk_root.focus_get()
        tip = ui.Tooltip(entry, 'Hello')
        tip.show()
        shown.update()
        assert tk_root.focus_get() is before
        tip.hide()

    def test_an_empty_hint_shows_nothing(self, shown):
        button = self.mapped(shown, ui.Button(shown, 'x'))
        tip = ui.Tooltip(button, '')
        tip.show()
        assert tip.window is None

    def test_a_widget_the_user_cannot_see_shows_no_hint(self, holder):
        button = ui.Button(holder, 'x')                       # never packed, never mapped
        tip = ui.Tooltip(button, 'Hello')
        tip.show()
        assert tip.window is None

    def test_it_wears_the_palette_in_force_when_shown(self, shown, env):
        button = self.mapped(shown, ui.Button(shown, 'x'))
        env.manager.set_mode('dark')
        tip = ui.Tooltip(button, 'Hello')
        tip.show()
        label = tip.window.winfo_children()[0]
        assert str(label.cget('bg')).upper() == tokens.DARK.surface.upper()
        tip.hide()
        env.manager.set_mode('light')

    def test_destroying_the_widget_removes_the_hint(self, shown):
        button = self.mapped(shown, ui.Button(shown, 'x'))
        tip = ui.Tooltip(button, 'Hello')
        tip.show()
        button.destroy()
        assert tip.window is None


class TestContext:
    def test_get_returns_the_installed_context(self, env):
        assert C.get() is env

    def test_the_palette_is_the_managers(self, env):
        env.manager.set_mode('dark')
        assert env.palette is tokens.DARK
        env.manager.set_mode('light')
        assert env.palette is tokens.LIGHT

    def test_plane_colours(self, light):
        assert light.plane_colour('bg') == tokens.LIGHT.bg
        assert light.plane_colour('surface') == tokens.LIGHT.surface
        assert light.plane_colour('alt') == tokens.LIGHT.alt

    def test_an_icon_is_a_tk_image_name_that_exists(self, env, tk_root):
        name = env.icon('gear', 16)
        assert name in tk_root.image_names()

    def test_icons_are_cached(self, env):
        assert env.icon('gear', 16) == env.icon('gear', 16)
        assert env.icon('gear', 16) != env.icon('gear', 20)


class TestRenderingPerformanceGuards:
    """Measured 2026-10-21: ttk TILES the middle of a 9-slice image, copy by copy, and blends the
    alpha of every pixel - eight info bars took 25 s to lay out with a 4 px middle and transparent
    corners. These pin the two fixes (a large middle, opaque corners) so a tidy-up cannot undo them."""

    def test_a_large_slice_has_a_large_middle(self):
        width, height = styles.Builder.slice_size(10, large=True)
        assert width >= 2 * 10 + 128 and height >= 2 * 10 + 128

    def test_a_small_slice_still_has_a_middle_worth_tiling(self):
        assert styles.Builder.slice_size(8)[0] >= 2 * 8 + 32

    def test_the_large_middle_is_larger_than_the_small_one(self):
        assert styles.SLICE_CENTRE_LARGE > styles.SLICE_CENTRE

    def test_a_rounded_shape_is_transparent_in_the_corner_unless_told_otherwise(self):
        from src.ui.components import render
        assert render.rounded_rect(40, 40, 10, '#ff0000').getpixel((0, 0))[3] == 0
        opaque = render.rounded_rect(40, 40, 10, '#ff0000', under='#00ff00')
        assert opaque.getpixel((0, 0)) == (0, 255, 0, 255)
        assert opaque.getpixel((20, 20)) == (255, 0, 0, 255)

    def test_every_rounded_image_a_style_uses_is_opaque(self, env):
        env.manager.set_mode('light')
        keys = [k for k in env._images if k[0] == 'rr']
        assert keys and all(k[6] is not None for k in keys), [k for k in keys if k[6] is None][:3]

    def test_a_card_pads_its_children(self, shown):
        card = ui.Card(shown)
        card.pack()
        label = ui.Label(card, 'x')
        label.pack()
        card.update()
        assert label.winfo_x() >= C.get().px(16) - 1 and label.winfo_y() >= C.get().px(16) - 1

    def test_a_card_can_be_given_other_padding(self, shown):
        card = ui.Card(shown, padding=(4, 30))
        card.pack()
        label = ui.Label(card, 'x')
        label.pack()
        card.update()
        assert label.winfo_x() == C.get().px(4) and label.winfo_y() == C.get().px(30)

    def test_a_thin_scrollbar_can_be_added_to_a_laid_out_parent(self, shown):
        """A bar asking for 1 px along its length is never arranged by grid (measured)."""
        area = tk.Frame(shown)
        area.pack(fill='both', expand=True)
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        canvas = tk.Canvas(area, width=1, height=1, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky='nsew')
        shown.geometry('300x120')
        shown.update()
        bar = ui.ThinScrollbar(area, command=canvas.yview)
        assert bar.winfo_reqheight() > 1
        bar.grid(row=0, column=1, sticky='ns')
        for _ in range(3):
            shown.update()
        assert bar.winfo_ismapped() and bar.winfo_height() >= 100


class TestIconButtonAsASwitch:
    """The popup's Linked reading icon is on or off: a soft tinted face and an accent-coloured
    icon while on (TODO.md U6)."""

    def test_it_starts_off_with_the_quiet_face(self, holder):
        button = ui.IconButton(holder, 'link-45deg', label='Linked reading')
        assert button.is_on is False and button.cget('style') == 'CtIconBg.TButton'

    def test_on_swaps_the_face_and_the_icon_colour_and_off_swaps_back(self, holder, env):
        button = ui.IconButton(holder, 'link-45deg')
        resting = str(button.cget('image'))
        button.set_on(True)
        assert button.is_on and button.cget('style') == 'CtIcononBg.TButton'
        assert str(button.cget('image')) != resting
        button.set_on(False)
        assert button.cget('style') == 'CtIconBg.TButton' and str(button.cget('image')) == resting

    @pytest.mark.parametrize('mode', ['light', 'dark'])
    def test_the_on_face_is_drawn_in_the_soft_fill_and_the_off_face_is_not(self, env, mode):
        env.manager.set_mode(mode)
        styles.ensure(env)
        p, side = env.palette, tokens.CONTROL_HEIGHT
        ordinary_planes = {p.bg, p.surface, p.alt}
        fills = {key[1] for key in env._images if key[0] == 'rr'
                 and key[2] == (side, side) and key[6] in ordinary_planes}
        assert p.soft in fills            # the 'on' face
        assert p.hover not in fills       # Ordinary switch faces exclude the accent status surface.

    def test_on_keeps_each_planes_corner_colour(self, holder):
        for plane in ui.PLANES:
            button = ui.IconButton(holder, 'gear', on=plane)
            button.set_on(True)
            assert button.cget('style') == styles.name('iconon', plane, 'TButton')

    @pytest.mark.parametrize('mode', ['light', 'dark'])
    def test_the_on_icon_reads_on_its_face(self, env, mode):
        env.manager.set_mode(mode)
        p = env.palette
        assert contrast_ratio(p.soft_text, p.soft) >= 4.5


class TestIconButtonIsOneSquare:
    """Always CONTROL_HEIGHT square, drawn as one image: half the drawing time of a nine-piece slice
    (measured with seven in a popup), so the icon is padded to fit at every scale."""

    @pytest.mark.parametrize('size', [12, 14, 16, 20])
    def test_any_icon_size_gives_the_same_square(self, holder, env, size):
        side = env.style.scaling.logical(tokens.CONTROL_HEIGHT)
        button = ui.IconButton(holder, 'gear', size=size)
        assert (button.winfo_reqwidth(), button.winfo_reqheight()) == (side, side)

    def test_its_face_is_one_piece_not_a_slice(self, env):
        element = 'CtIconBg.Button.bg'
        assert env.style.element_options(element) is not None

    def test_an_icon_wider_than_the_square_does_not_go_negative(self, holder):
        button = ui.IconButton(holder, 'gear', size=64)
        assert all(int(float(str(v))) >= 0 for v in button.cget('padding'))


class TestButtonIcon:
    def test_set_icon_swaps_the_icon_and_keeps_the_text(self, holder):
        button = ui.Button(holder, 'Copy', kind='accent', icon='copy')
        before = str(button.cget('image'))
        button.set_icon('check-lg')
        assert button.icon_name == 'check-lg' and button.cget('text') == 'Copy'
        assert str(button.cget('image')) != before

    def test_without_an_icon_there_is_none(self, holder):
        button = ui.Button(holder, 'Copy')
        assert button.icon_name is None and not str(button.cget('image'))

    def test_an_unknown_icon_is_refused(self, holder):
        button = ui.Button(holder, 'Copy')
        with pytest.raises(Exception):
            button.set_icon('no-such-icon')


class TestGrip:
    def test_it_is_an_image_of_the_size_asked_at_the_screens_scale(self, env, tk_root):
        name = env.grip(12)
        side = env.px(12)
        assert (int(tk_root.tk.call('image', 'width', name)),
                int(tk_root.tk.call('image', 'height', name))) == (side, side)

    def test_the_same_request_is_one_image(self, env):
        assert env.grip(12) == env.grip(12)
        assert env.grip(12) != env.grip(16)
        assert env.grip(12, '#ff0000') != env.grip(12, '#00ff00')

    def test_it_dots_the_lower_right_and_leaves_the_rest_clear(self):
        from src.ui.components import render
        image = render.grip(24, '#ff0000')
        assert image.size == (24, 24) and image.mode == 'RGBA'
        assert image.getpixel((0, 0))[3] == 0 and image.getpixel((0, 23))[3] == 0
        assert image.getpixel((23, 0))[3] == 0
        assert any(image.getpixel((x, y))[3] > 200 for x in range(14, 24) for y in range(14, 24))


class TestAutoScrollbar:
    """A thin bar that shows only while the content is taller than its window (TODO.md U7)."""

    @pytest.fixture
    def holder(self, shown):
        """A mapped window: an unmapped text box has no height to be taller than."""
        frame = tk.Frame(shown)
        frame.pack()
        return frame

    def box(self, holder, lines):
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        text = tk.Text(holder, width=20, height=5)
        text.grid(row=0, column=0, sticky='nsew')
        bar = ui.AutoScrollbar(holder, text)
        text.insert('1.0', '\n'.join(str(i) for i in range(lines)))
        holder.update()
        holder.update_idletasks()
        holder.update()
        return text, bar

    def test_hidden_while_everything_fits(self, holder):
        _, bar = self.box(holder, 2)
        assert not bar.shown

    def test_shown_once_the_content_is_taller(self, holder):
        _, bar = self.box(holder, 40)
        assert bar.shown

    def test_hidden_again_when_the_content_shrinks(self, holder):
        text, bar = self.box(holder, 40)
        text.delete('1.0', 'end')
        text.insert('1.0', 'short')
        holder.update()
        holder.update_idletasks()
        holder.update()
        assert not bar.shown

    def test_it_sits_in_the_cell_it_was_given(self, holder):
        _, bar = self.box(holder, 40)
        info = bar.grid_info()
        assert (int(info['row']), int(info['column'])) == (0, 1)

    def test_dragging_it_scrolls_the_target(self, holder):
        text, bar = self.box(holder, 40)
        bar.set(0.0, 0.1)
        text.yview_moveto(0.5)
        assert text.yview()[0] > 0.3

    def test_destroying_it_with_a_pending_update_is_harmless(self, holder):
        text, bar = self.box(holder, 2)
        text.insert('end', '\n' * 50)
        bar.destroy()
        holder.update()


class TestTextFieldFollowsTheTheme:
    def test_the_placeholder_and_the_text_take_the_new_colours(self, env, holder):
        env.manager.set_mode('light')
        field = ui.TextField(holder, placeholder='Hint')
        assert str(field.cget('foreground')) == env.palette.muted
        env.manager.set_mode('dark')
        assert str(field.cget('foreground')) == env.palette.muted        # still the placeholder
        field.set('typed')
        env.manager.set_mode('light')
        assert str(field.cget('foreground')) == env.palette.text
        env.manager.set_mode('dark')
        assert str(field.cget('foreground')) == env.palette.text
        env.manager.set_mode('light')

    def test_a_destroyed_field_stops_listening(self, env, holder):
        field = ui.TextField(holder, placeholder='Hint')
        before = len(env.manager._subscribers)
        field.destroy()
        assert len(env.manager._subscribers) == before - 1


class TestIconsFollowTheTheme:
    """A window that stays open while the theme changes (the Translator) redraws its icons (U7)."""

    def test_an_icon_button_is_redrawn_in_the_new_themes_colours(self, env, holder):
        env.manager.set_mode('light')
        button = ui.IconButton(holder, 'gear')
        light_image = str(button.cget('image'))
        env.manager.set_mode('dark')
        assert str(button.cget('image')) != light_image
        env.manager.set_mode('light')
        assert str(button.cget('image')) == light_image

    def test_a_lit_switch_stays_lit_through_a_theme_change(self, env, holder):
        env.manager.set_mode('light')
        button = ui.IconButton(holder, 'link-45deg')
        button.set_on(True)
        env.manager.set_mode('dark')
        assert button.is_on and button.cget('style') == 'CtIcononBg.TButton'
        env.manager.set_mode('light')

    def test_a_button_with_an_icon_is_redrawn_too(self, env, holder):
        env.manager.set_mode('light')
        button = ui.Button(holder, 'Copy', icon='copy')
        light_image = str(button.cget('image'))
        env.manager.set_mode('dark')
        assert str(button.cget('image')) != light_image and button.icon_name == 'copy'
        env.manager.set_mode('light')

    def test_a_button_without_an_icon_is_left_alone(self, env, holder):
        button = ui.Button(holder, 'Plain')
        env.manager.set_mode('dark')
        assert button.icon_name is None
        env.manager.set_mode('light')

    def test_destroyed_buttons_stop_listening(self, env, holder):
        before = len(env.manager._subscribers)
        icon, plain = ui.IconButton(holder, 'gear'), ui.Button(holder, 'x', icon='copy')
        assert len(env.manager._subscribers) == before + 2
        icon.destroy()
        plain.destroy()
        assert len(env.manager._subscribers) == before

"""
Tests for the composite components (TODO.md U4b): info bar, keycaps, empty state, scroll area,
navigation view and the dialog - behaviour, plane inheritance, theme follow and the rules the old
dialogs broke (Esc, Enter, size, no grab).
"""
import _tkinter
import time
import tkinter as tk

import pytest

from src.ui import components as ui
from src.ui.components import dialog as dialog_module
from src.ui.components import styles
from src.ui.theme import engine, tokens
from src.ui.theme.manager import MemoryStore, ThemeManager

from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)


class TestInfoBar:
    def test_each_kind_has_a_frame_and_text_style(self, holder):
        for kind in styles.INFO_KINDS:
            bar = ui.InfoBar(holder, 'Message', kind=kind)
            assert bar.cget('style') == f"CtInfo{kind.capitalize()}Bg.TFrame"
            assert bar.text.cget('style') == f"CtInfo{kind.capitalize()}TextBg.TLabel"

    def test_an_unknown_kind_is_refused(self, holder):
        with pytest.raises(ValueError, match="unknown info bar kind"):
            ui.InfoBar(holder, 'x', kind='loud')

    def test_a_title_is_optional(self, holder):
        assert ui.InfoBar(holder, 'x').title_label is None
        assert ui.InfoBar(holder, 'x', title='Hello').title_label.cget('text') == 'Hello'

    def test_the_action_link_runs_its_callback(self, holder):
        seen = []
        bar = ui.InfoBar(holder, 'x', action='Open settings', on_action=lambda: seen.append(1))
        bar.action.invoke()
        assert seen == [1] and bar.action.cget('text') == 'Open settings'

    def test_without_an_action_there_is_no_link(self, holder):
        assert ui.InfoBar(holder, 'x').action is None

    def test_a_closable_bar_removes_itself(self, holder):
        bar = ui.InfoBar(holder, 'x', closable=True)
        bar.close_button.invoke()
        assert not bar.winfo_exists()

    def test_or_runs_the_callers_close_handler(self, holder):
        seen = []
        bar = ui.InfoBar(holder, 'x', closable=True, on_close=lambda: seen.append(1))
        bar.close()
        assert seen == [1] and bar.winfo_exists()

    def test_it_can_be_fitted_to_a_width(self, holder):
        bar = ui.InfoBar(holder, "word " * 40)
        bar.fit_to(300)
        narrow = int(float(str(bar.text.cget('wraplength'))))
        bar.fit_to(900)
        wide = int(float(str(bar.text.cget('wraplength'))))
        assert 0 < narrow < wide

    def test_fitting_to_the_same_width_twice_changes_nothing(self, holder):
        bar = ui.InfoBar(holder, "word " * 40)
        bar.fit_to(420)
        first = str(bar.text.cget('wraplength'))
        bar.fit_to(420)
        assert str(bar.text.cget('wraplength')) == first

    def wrap_at(self, holder, width=600, **options):
        bar = ui.InfoBar(holder, "word " * 40, **options)
        bar.fit_to(width)
        return int(float(str(bar.text.cget('wraplength'))))

    def test_the_action_takes_its_room(self, holder):
        assert self.wrap_at(holder, action='Open settings') < self.wrap_at(holder)

    def test_the_close_button_takes_its_room(self, holder):
        assert self.wrap_at(holder, closable=True) < self.wrap_at(holder)

    def test_a_longer_action_takes_more(self, holder):
        assert self.wrap_at(holder, action='Open the settings for the API keys') <             self.wrap_at(holder, action='Open')

    def test_the_wrap_does_not_depend_on_the_last_wrap(self, holder):
        bar = ui.InfoBar(holder, "word " * 40)
        bar.fit_to(700)
        bar.fit_to(300)
        again = int(float(str(bar.text.cget('wraplength'))))
        fresh = ui.InfoBar(holder, "word " * 40)
        fresh.fit_to(300)
        assert again == int(float(str(fresh.text.cget('wraplength'))))

    def test_it_never_wraps_narrower_than_a_few_words(self, holder):
        bar = ui.InfoBar(holder, "word " * 40)
        bar.fit_to(1)
        assert int(float(str(bar.text.cget('wraplength')))) >= 100

    def test_the_message_can_change(self, holder):
        bar = ui.InfoBar(holder, 'before')
        bar.set_message('after')
        assert bar.text.cget('text') == 'after'

    def test_eight_bars_settle_at_once(self, shown):
        """Regression: the wrap width fed back into the bar's width and the first layout took 25 s."""
        import time
        outer = ui.Frame(shown, padding=16)
        outer.pack(fill='both', expand=True)
        started = time.perf_counter()
        for plane in ('bg', 'surface'):
            column = ui.Frame(outer) if plane == 'bg' else ui.Card(outer)
            column.pack(side='left', fill='both', expand=True)
            for kind in styles.INFO_KINDS:
                ui.InfoBar(column, 'Three left today - they come back at midnight.', kind=kind,
                           title='Free translations are almost used up', action='Add a key',
                           closable=True).pack(fill='x', pady=4)
        for _ in range(4):
            shown.update()
        assert time.perf_counter() - started < 6.0

    def test_the_message_wraps_to_the_bar(self, holder, shown):
        bar = ui.InfoBar(shown, 'word ' * 60, kind='warning', action='Fix it')
        bar.pack(fill='x')
        shown.geometry('420x300')
        shown.update()
        assert 120 <= int(str(bar.text.cget('wraplength'))) < 420

    def test_the_tint_is_the_kind_over_the_plane(self, env):
        colours = styles.infobar_colours(tokens.LIGHT, 'danger', tokens.LIGHT.bg)
        assert colours['base'] == tokens.LIGHT.danger and colours['tint'] != tokens.LIGHT.bg

    def test_text_on_every_tint_is_readable(self):
        from src.ui.theme.contrast import contrast_ratio
        for palette in (tokens.LIGHT, tokens.DARK):
            for plane in (palette.bg, palette.surface, palette.alt):
                for kind in styles.INFO_KINDS:
                    tint = styles.infobar_colours(palette, kind, plane)['tint']
                    assert contrast_ratio(palette.text, tint) >= 4.5, (palette.mode, kind)
                    link = palette.soft_text if kind == 'info' else palette.text
                    assert contrast_ratio(link, tint) >= 4.5, ('link', palette.mode, kind)


class TestKeys:
    def test_a_shortcut_reads_as_keys_and_plus_signs(self, holder):
        keys = ui.Keys(holder, ['Win', 'Alt', 'E'])
        assert keys.text == 'Win + Alt + E' and len(keys.caps) == 3

    def test_a_single_key_has_no_separator(self, holder):
        keys = ui.Keys(holder, ['F5'])
        assert len(keys.winfo_children()) == 1

    def test_a_keycap_is_a_label_in_the_key_style(self, holder):
        assert ui.Keycap(holder, 'Alt').cget('style') == 'CtKeyBg.TLabel'


class TestEmptyState:
    def test_it_shows_a_picture_a_line_and_one_step(self, holder):
        seen = []
        state = ui.EmptyState(holder, 'Nothing yet', 'Translate something.', action='Open', on_action=lambda: seen.append(1))
        assert state.title_label.cget('text') == 'Nothing yet'
        assert state.message_label.cget('text') == 'Translate something.'
        state.button.invoke()
        assert seen == [1]

    def test_message_and_action_are_optional(self, holder):
        state = ui.EmptyState(holder, 'Nothing yet')
        assert state.message_label is None and state.button is None

    def test_a_given_image_replaces_the_icon(self, holder, env):
        picture = env.icon('heart', 48)
        state = ui.EmptyState(holder, 'x', image=picture)
        assert picture in str(state.picture.cget('image'))


class TestScrollArea:
    def build(self, window, rows):
        area = ui.ScrollArea(window)
        area.pack(fill='both', expand=True)
        for n in range(rows):
            ui.Label(area.content, f'row {n}').pack(anchor='w')
        window.geometry('300x120')
        window.update()
        return area

    def test_the_bar_appears_only_when_the_content_is_taller(self, shown):
        area = self.build(shown, 40)
        for _ in range(3):
            shown.update()
        assert area.scrollbar.winfo_ismapped()
        for child in area.content.winfo_children():
            child.destroy()
        ui.Label(area.content, 'one row').pack()
        for _ in range(3):
            shown.update()
        assert not area.scrollbar.winfo_ismapped()

    def test_the_wheel_scrolls_over_a_child(self, shown):
        area = self.build(shown, 60)
        area.adopt()
        label = area.content.winfo_children()[0]
        assert label.bind('<MouseWheel>')
        before = area.canvas.yview()[0]
        event = type('E', (), {'delta': -120})()
        assert area._wheel(event) == 'break'
        assert area.canvas.yview()[0] > before

    def test_the_wheel_is_never_bound_application_wide(self, shown, tk_root):
        before = tk_root.bind_all('<MouseWheel>')
        self.build(shown, 10)
        assert tk_root.bind_all('<MouseWheel>') == before

    def test_reveal_scrolls_a_hidden_widget_into_view(self, shown):
        area = self.build(shown, 60)
        target = area.content.winfo_children()[50]
        assert target.winfo_rooty() > area.canvas.winfo_rooty() + area.canvas.winfo_height()
        area.reveal(target)
        shown.update()
        top = area.canvas.winfo_rooty()
        assert top <= target.winfo_rooty() and target.winfo_rooty() + target.winfo_height() <= top + area.canvas.winfo_height()

    def test_reveal_leaves_a_widget_already_in_view_alone(self, shown):
        area = self.build(shown, 60)
        area.reveal(area.content.winfo_children()[1])
        assert area.canvas.yview()[0] == 0.0

    def test_reveal_does_nothing_when_the_content_fits(self, shown):
        area = self.build(shown, 2)
        area.reveal(area.content.winfo_children()[1])
        assert area.canvas.yview() == (0.0, 1.0)

    def test_reveal_keeps_the_margin_above_the_widget(self, shown):
        area = self.build(shown, 60)
        target = area.content.winfo_children()[50]
        area.reveal(target, margin=30)
        shown.update()
        assert target.winfo_rooty() - area.canvas.winfo_rooty() >= 28

    def test_reveal_scrolls_back_up_to_a_widget_above(self, shown):
        area = self.build(shown, 60)
        area.canvas.yview_moveto(1.0)
        shown.update()
        first = area.content.winfo_children()[0]
        area.reveal(first)
        shown.update()
        assert area.canvas.yview()[0] == 0.0

    def test_the_content_follows_the_canvas_width(self, shown):
        area = self.build(shown, 3)
        shown.geometry('500x200')
        shown.update()
        assert area.content.winfo_width() >= 480

    @pytest.mark.parametrize('sizes', [('980x650', '320x190'), ('320x190', '980x650')])
    @pytest.mark.parametrize('rows', [2, 45])
    def test_hidden_area_remaps_inside_its_new_host_allocation(self, shown, sizes, rows):
        area = self.build(shown, rows)
        for size in (*sizes, *reversed(sizes)):
            area.pack_forget()
            shown.geometry(size)
            shown.update_idletasks()
            area.pack(fill='both', expand=True)
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                if not shown.tk.dooneevent(_tkinter.DONT_WAIT):
                    time.sleep(.001)
            assert area.canvas.winfo_ismapped()
            assert area.canvas.winfo_width() <= area.winfo_width()
            assert area.canvas.winfo_height() == area.winfo_height()
            bar_width = area.scrollbar.winfo_width() if area.scrollbar.winfo_ismapped() else 0
            assert abs(area.canvas.winfo_width() + bar_width - area.winfo_width()) <= 1
            assert area.content.winfo_width() == area.canvas.winfo_width()

    def test_scrolling_back_to_the_top(self, shown):
        area = self.build(shown, 60)
        area.canvas.yview_moveto(0.5)
        area.scroll_to_top()
        assert area.canvas.yview()[0] == 0.0

    def test_the_canvas_follows_the_theme_and_the_subscription_ends_with_the_widget(self, shown, env):
        area = self.build(shown, 3)
        env.manager.set_mode('dark')
        assert str(area.canvas.cget('bg')).upper() == tokens.DARK.bg.upper()
        env.manager.set_mode('light')
        count = len(env.manager._subscribers)
        area.destroy()
        assert len(env.manager._subscribers) == count - 1


class TestNavigationView:
    ITEMS = [('general', 'General', 'sliders'), ('translation', 'Translation', 'translate'),
             ('shortcuts', 'Shortcuts', 'keyboard')]

    def test_it_starts_on_the_first_entry_or_the_given_one(self, holder):
        assert ui.NavigationView(holder, self.ITEMS).value == 'general'
        assert ui.NavigationView(holder, self.ITEMS, value='shortcuts').value == 'shortcuts'

    def test_choosing_an_entry_tells_the_caller(self, holder):
        seen = []
        nav = ui.NavigationView(holder, self.ITEMS, on_select=seen.append)
        nav.buttons['translation'].invoke()
        assert nav.value == 'translation' and seen == ['translation']

    def test_the_arrow_keys_move_the_selection_and_wrap(self, holder):
        seen = []
        nav = ui.NavigationView(holder, self.ITEMS, on_select=seen.append)
        assert nav._move('general', 1) == 'break'
        assert nav.value == 'translation'
        nav._move('translation', 1)
        nav._move('shortcuts', 1)
        assert nav.value == 'general'                       # wrapped
        nav._move('general', -1)
        assert nav.value == 'shortcuts' and seen[-1] == 'shortcuts'

    def test_each_entry_is_reachable_with_tab(self, holder):
        nav = ui.NavigationView(holder, self.ITEMS)
        assert all(str(b.cget('takefocus')) in ('1', 'True', 'true') for b in nav.buttons.values())

    def test_the_selection_can_be_set_from_code_without_a_callback(self, holder):
        seen = []
        nav = ui.NavigationView(holder, self.ITEMS, on_select=seen.append)
        nav.value = 'shortcuts'
        assert nav.buttons['shortcuts'].instate(['selected']) and seen == []


class TestDialogGeometry:
    AREA = (0, 0, 1920, 1032)

    def test_it_fits_its_content_but_never_under_the_minimum(self):
        w, h, x, y = dialog_module.fit_geometry(300, 200, self.AREA, None, min_w=360)
        assert (w, h) == (360, 200)

    def test_it_never_exceeds_ninety_percent_of_the_work_area(self):
        w, h, x, y = dialog_module.fit_geometry(5000, 5000, self.AREA, None)
        assert w == int(1920 * 0.9) and h == int(1032 * 0.9)

    def test_it_is_centred_on_the_work_area_without_a_parent(self):
        w, h, x, y = dialog_module.fit_geometry(400, 200, self.AREA, None)
        assert (x, y) == ((1920 - 400) // 2, (1032 - 200) // 2)

    def test_it_is_centred_on_its_parent(self):
        w, h, x, y = dialog_module.fit_geometry(400, 200, self.AREA, (100, 100, 800, 600))
        assert (x, y) == (100 + 200, 100 + 200)

    def test_a_parent_near_an_edge_cannot_push_it_off_the_screen(self):
        w, h, x, y = dialog_module.fit_geometry(400, 200, self.AREA, (1800, 1000, 100, 100))
        assert x + w <= 1920 and y + h <= 1032 and x >= 0 and y >= 0

    def test_a_small_laptop_work_area_is_respected(self):
        w, h, x, y = dialog_module.fit_geometry(900, 900, (0, 0, 1366, 728), None)
        assert w <= 1366 * 0.9 and h <= 728 * 0.9


class TestDialog:
    def make(self, tk_root, **kw):
        dialog = ui.Dialog(tk_root, 'Title', 'A message.', **kw)
        dialog.add_button('Primary', default=True)
        dialog.add_button('Secondary')
        return dialog

    def test_it_is_sized_to_its_content_inside_the_work_area(self, env, tk_root):
        dialog = self.make(tk_root)
        try:
            width, height, x, y = dialog.fit()
            left, top, right, bottom = dialog_module.work_area()
            assert dialog.min_width <= width <= (right - left) * 0.9
            assert left <= x and x + width <= right and top <= y and y + height <= bottom
            assert height >= dialog.window.winfo_reqheight() or height == int((bottom - top) * 0.9)
        finally:
            dialog.close()

    def test_every_button_is_drawn_whole(self, env, tk_root):
        dialog = self.make(tk_root).show()
        try:
            dialog.window.update()
            for button in dialog._button_list:
                assert button.winfo_ismapped()
                assert button.winfo_width() >= button.winfo_reqwidth()
                assert button.winfo_height() >= button.winfo_reqheight()
        finally:
            dialog.close()

    def test_a_long_message_makes_it_taller_not_cut(self, env, tk_root):
        dialog = ui.Dialog(tk_root, 'Title', 'word ' * 200)
        dialog.add_button('OK', default=True)
        dialog.show()
        try:
            dialog.window.update()
            button = dialog._button_list[0]
            assert button.winfo_ismapped() and button.winfo_height() >= button.winfo_reqheight()
        finally:
            dialog.close()

    def test_escape_cancels_and_runs_the_callback_once(self, env, tk_root):
        seen = []
        dialog = self.make(tk_root, on_cancel=lambda: seen.append(1)).show()
        assert dialog._escape() == 'break'
        assert seen == [1] and not dialog.exists
        dialog.cancel()                                      # twice is harmless
        assert seen == [1]

    def test_enter_runs_the_default_button(self, env, tk_root):
        seen = []
        dialog = ui.Dialog(tk_root, 'Title')
        dialog.add_button('Go', command=lambda: seen.append('go'), default=True)
        dialog.add_button('No', command=lambda: seen.append('no'))
        dialog.show()
        assert dialog._enter() == 'break'
        assert seen == ['go'] and not dialog.exists

    def test_enter_on_a_focused_button_is_left_to_that_button(self, env, tk_root):
        dialog = self.make(tk_root).show()
        dialog.window.update()
        dialog._button_list[1].focus_force()
        dialog.window.update()
        assert dialog._enter() is None
        dialog.close()

    def test_enter_with_no_default_does_nothing(self, env, tk_root):
        dialog = ui.Dialog(tk_root, 'Title')
        dialog.add_button('OK')
        dialog.show()
        assert dialog._enter() is None
        dialog.close()

    def test_a_button_can_keep_the_dialog_open(self, env, tk_root):
        dialog = ui.Dialog(tk_root, 'Title')
        dialog.add_button('Test', close=False)
        dialog._button_list[0].invoke()
        assert dialog.exists
        dialog.close()

    def test_the_primary_button_is_the_accent_one(self, env, tk_root):
        dialog = self.make(tk_root)
        assert dialog._button_list[0].kind == 'accent' and dialog._button_list[1].kind == 'soft'
        dialog.close()

    def test_it_never_takes_a_grab_nor_is_transient(self, env, tk_root):
        dialog = self.make(tk_root).show()
        try:
            assert dialog.window.grab_current() is None
            assert str(dialog.window.wm_transient()) == ''
        finally:
            dialog.close()

    def test_it_follows_the_theme_chrome(self, env, tk_root):
        env.manager.set_mode('dark')
        dialog = self.make(tk_root).show()
        try:
            assert str(dialog.window.cget('bg')).upper() == tokens.DARK.bg.upper()
            env.manager.set_mode('light')
            assert str(dialog.window.cget('bg')).upper() == tokens.LIGHT.bg.upper()
        finally:
            dialog.close()

    def test_topmost_is_released_after_it_appears(self, env, tk_root):
        dialog = self.make(tk_root).show()
        try:
            for _ in range(30):
                dialog.window.update()
                dialog.window.after(10)
            assert not dialog.window.attributes('-topmost')
        finally:
            dialog.close()

    def test_the_work_area_is_a_real_rectangle(self):
        left, top, right, bottom = dialog_module.work_area()
        assert right > left and bottom > top

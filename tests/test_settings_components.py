"""
Tests for src/ui/components/settings.py (TODO.md U8): setting rows and groups, the dropdown for a
short list, and the shortcut editor. The editor is fed key events through an injected source - the
real keyboard is never touched (CLAUDE.md: a test run stays off the developer's session).
"""
import queue
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from src.core import shortcut_rules as rules
from src.ui import components as ui
from src.ui.components import settings as S

from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)

LONG = ("A long sentence that explains the setting in plain words and goes on for long enough that it "
        "has to wrap onto several lines in a narrow row, so the wrap width matters. ") * 2


def pump(widget, seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        widget.update()
        time.sleep(0.005)


def wait_for(widget, condition, seconds=1.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        widget.update()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #
class TestSettingRow:
    def row(self, window, title='Furigana', description='Show the reading above kanji.', **kw):
        card = ui.Card(window, padding=0)
        card.pack(fill='x', padx=20, pady=20)
        row = ui.SettingRow(card, title, description, on=card.plane, **kw)
        row.pack(fill='x')
        window.update()
        return row

    def test_it_names_the_setting_and_says_a_sentence_under_it(self, shown, light):
        row = self.row(shown)
        assert row.title_label.cget('text') == 'Furigana'
        assert row.description_label.cget('text') == 'Show the reading above kanji.'
        assert row.title_label.winfo_rooty() < row.description_label.winfo_rooty()

    def test_it_uses_the_strong_and_muted_label_styles_of_its_plane(self, shown, light):
        row = self.row(shown)
        assert row.title_label.cget('style') == 'CtStrongSurface.TLabel'
        assert row.description_label.cget('style') == 'CtMutedSurface.TLabel'

    def test_without_a_sentence_there_is_no_second_line(self, shown, light):
        row = self.row(shown, description='')
        assert row.description_label is None
        row.set_description('ignored')          # harmless

    def test_the_control_sits_at_the_right_end_of_the_row(self, shown, light):
        row = self.row(shown)
        ui.Switch(row.control, on=row.plane).pack()
        shown.update()
        right_of_row = row.winfo_rootx() + row.winfo_width()
        assert abs((row.control.winfo_rootx() + row.control.winfo_width()) -
                   (right_of_row - light.px(S.ROW_PAD_X))) <= 2
        assert row.control.winfo_rootx() > row.title_label.winfo_rootx() + row.title_label.winfo_width()

    def test_a_stacked_row_puts_the_control_under_the_words_across_the_row(self, shown, light):
        row = self.row(shown, stacked=True)
        ui.TextField(row.control, on=row.plane).pack(fill='x')
        shown.update()
        assert row.control.winfo_rooty() >= row.description_label.winfo_rooty() + row.description_label.winfo_height()
        assert row.control.winfo_width() >= row.winfo_width() - 2 * light.px(S.ROW_PAD_X) - 2

    def test_the_sentence_wraps_to_what_the_control_leaves(self, shown, light):
        shown.geometry('620x400+0+0')
        row = self.row(shown, description=LONG)
        ui.Switch(row.control, on=row.plane).pack()
        pump(shown)
        wrap = int(float(str(row.description_label.cget('wraplength'))))
        room = row.winfo_width() - 2 * light.px(S.ROW_PAD_X) - row.control.winfo_reqwidth() - light.px(S.GAP + 4)
        assert abs(wrap - room) <= 3

    def test_a_wider_row_wraps_wider(self, shown, light):
        shown.geometry('520x400+0+0')
        row = self.row(shown, description=LONG)
        pump(shown)
        narrow = int(float(str(row.description_label.cget('wraplength'))))
        shown.geometry('900x400+0+0')
        pump(shown, 0.2)
        assert int(float(str(row.description_label.cget('wraplength')))) > narrow + 100

    def test_asking_again_changes_nothing(self, shown, light):
        row = self.row(shown, description=LONG)
        pump(shown)
        first = str(row.description_label.cget('wraplength'))
        height = row.winfo_height()
        for _ in range(5):
            row._fit()
            shown.update()
        assert str(row.description_label.cget('wraplength')) == first and row.winfo_height() == height

    def test_a_very_narrow_row_wraps_within_available_width(self, shown, light):
        shown.geometry('150x300+0+0')
        row = self.row(shown, description=LONG)
        pump(shown)
        wrap = int(float(str(row.description_label.cget('wraplength'))))
        available = row.winfo_width() - 2 * light.px(S.ROW_PAD_X)
        assert 1 <= wrap <= available
        assert wrap >= min(light.px(S.WRAP_MIN), available - light.px(S.GAP + 5))

    def test_highlight_shows_and_clears_an_accent_bar(self, shown, light):
        row = self.row(shown)
        assert not row.highlighted
        row.highlight(True)
        shown.update()
        assert row.highlighted and str(row._bar.cget('bg')).lower() == light.palette.accent.lower()
        row.highlight(False)
        assert not row.highlighted

    def test_the_key_names_the_setting(self, shown, light):
        assert self.row(shown, key='furigana').key == 'furigana'


# --------------------------------------------------------------------------- #
# Groups
# --------------------------------------------------------------------------- #
class TestSettingGroup:
    def group(self, window, heading='', count=3):
        group = ui.SettingGroup(window, heading)
        group.pack(fill='x', padx=20, pady=20)
        rows = [group.row(f'Setting {i}', f'Sentence {i}.', key=f'k{i}') for i in range(count)]
        window.update()
        return group, rows

    def order(self, group):
        return [(w.key if isinstance(w, ui.SettingRow) else 'line') for w in group.card.pack_slaves()]

    def test_rows_in_order_with_a_hairline_between_each_pair(self, shown, light):
        group, _ = self.group(shown)
        assert self.order(group) == ['k0', 'line', 'k1', 'line', 'k2']

    def test_the_rows_sit_on_the_cards_plane(self, shown, light):
        group, rows = self.group(shown)
        assert group.card.plane == 'surface' and all(r.plane == 'surface' for r in rows)

    def test_a_heading_is_above_the_card_and_optional(self, shown, light):
        with_heading, _ = self.group(shown, 'Appearance')
        assert with_heading.heading_label.cget('text') == 'Appearance'
        assert with_heading.heading_label.winfo_rooty() < with_heading.card.winfo_rooty()
        without, _ = self.group(shown)
        assert without.heading_label is None

    def test_a_row_is_found_by_its_key(self, shown, light):
        group, rows = self.group(shown)
        assert group.by_key('k1') is rows[1] and group.by_key('nope') is None

    def test_hiding_a_middle_row_takes_its_hairline_with_it(self, shown, light):
        group, rows = self.group(shown)
        rows[1].set_visible(False)
        assert self.order(group) == ['k0', 'line', 'k2']
        assert [r.key for r in group.shown_rows] == ['k0', 'k2'] and not rows[1].visible

    def test_a_hidden_row_comes_back_where_it_was(self, shown, light):
        group, rows = self.group(shown)
        rows[1].set_visible(False)
        rows[1].set_visible(True)
        assert self.order(group) == ['k0', 'line', 'k1', 'line', 'k2']

    def test_the_first_row_shown_never_has_a_hairline_above_it(self, shown, light):
        group, rows = self.group(shown)
        rows[0].set_visible(False)
        assert self.order(group) == ['k1', 'line', 'k2']
        rows[0].set_visible(True)
        assert self.order(group) == ['k0', 'line', 'k1', 'line', 'k2']

    def test_a_row_added_while_another_is_hidden_lands_in_order(self, shown, light):
        group, rows = self.group(shown)
        rows[2].set_visible(False)
        group.row('Setting 3', 'Sentence 3.', key='k3')
        assert self.order(group) == ['k0', 'line', 'k1', 'line', 'k3']

    def test_a_group_with_every_row_hidden_shows_nothing(self, shown, light):
        group, rows = self.group(shown)
        for row in rows:
            row.set_visible(False)
        assert group.card.pack_slaves() == []

    def test_a_row_outside_a_group_can_still_be_hidden(self, shown, light):
        card = ui.Card(shown, padding=0)
        card.pack()
        row = ui.SettingRow(card, 'Alone', on=card.plane)
        row.pack()
        row.set_visible(False)
        assert not row.visible
        row.set_visible(True)
        assert row.visible


# --------------------------------------------------------------------------- #
# Dropdown
# --------------------------------------------------------------------------- #
OPTIONS = [('preview', 'Preview first'), ('quick', 'Replace immediately'), ('ask', 'Ask every time')]


class TestDropdown:
    def make(self, window, value='preview', changes=None):
        changes = [] if changes is None else changes
        dropdown = ui.Dropdown(window, OPTIONS, value, changes.append, label='Replace button')
        dropdown.pack(padx=20, pady=20)
        window.update()
        return dropdown, changes

    def test_it_names_the_current_choice(self, shown, light):
        dropdown, _ = self.make(shown, 'quick')
        assert dropdown.button.cget('text') == 'Replace immediately' and dropdown.value == 'quick'

    def test_an_unknown_value_shows_as_it_is(self, shown, light):
        dropdown, _ = self.make(shown, 'weird')
        assert dropdown.button.cget('text') == 'weird'

    def test_it_carries_a_down_chevron_after_the_name(self, shown, light):
        dropdown, _ = self.make(shown)
        assert dropdown.button.icon_name == 'chevron-down' and str(dropdown.button.cget('compound')) == 'right'

    def test_it_is_as_wide_as_its_longest_choice(self, shown, light):
        dropdown, _ = self.make(shown, 'preview')
        before = dropdown.button.winfo_width()
        dropdown.set('quick')
        shown.update()
        assert dropdown.button.winfo_width() == before

    def test_setting_the_value_does_not_call_the_owner(self, shown, light):
        dropdown, changes = self.make(shown)
        dropdown.set('quick')
        assert changes == [] and dropdown.value == 'quick'

    def test_opening_lists_every_choice_with_the_current_one_highlighted(self, shown, light):
        dropdown, _ = self.make(shown, 'quick')
        dropdown.open()
        dropdown.window.update()
        assert [dropdown.listbox.get(i).strip() for i in range(dropdown.listbox.size())] == \
            [text for _, text in OPTIONS]
        assert dropdown.listbox.curselection() == (1,)
        dropdown.close()

    def test_choosing_sets_it_closes_and_tells_the_owner_once(self, shown, light):
        dropdown, changes = self.make(shown)
        dropdown.open()
        dropdown.choose(2)
        assert changes == ['ask'] and dropdown.value == 'ask' and not dropdown.is_open
        assert dropdown.button.cget('text') == 'Ask every time'

    def test_choosing_with_nothing_given_takes_the_highlighted_row(self, shown, light):
        dropdown, changes = self.make(shown, 'preview')
        dropdown.open()
        dropdown._move(1)
        dropdown.choose()
        assert changes == ['quick']

    def test_the_arrows_stop_at_the_ends(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        for _ in range(10):
            dropdown._move(1)
        assert dropdown.listbox.curselection() == (2,)
        for _ in range(10):
            dropdown._move(-1)
        assert dropdown.listbox.curselection() == (0,)
        dropdown.close()

    def test_keys_from_the_open_list(self, shown, light):
        dropdown, changes = self.make(shown)
        dropdown.open()
        dropdown.listbox.focus_force()
        shown.update()
        dropdown.listbox.event_generate('<Down>')
        dropdown.listbox.event_generate('<Return>')
        shown.update()
        assert changes == ['quick'] and not dropdown.is_open

    def test_escape_closes_without_choosing(self, shown, light):
        dropdown, changes = self.make(shown)
        dropdown.open()
        dropdown.listbox.focus_force()
        shown.update()
        dropdown.listbox.event_generate('<Escape>')
        shown.update()
        assert not dropdown.is_open and changes == [] and dropdown.value == 'preview'

    def test_clicking_a_row_chooses_it(self, shown, light):
        dropdown, changes = self.make(shown)
        dropdown.open()
        dropdown.window.update()
        _, y, _, height = dropdown.listbox.bbox(1)
        dropdown.listbox.event_generate('<ButtonRelease-1>', x=10, y=y + height // 2)
        shown.update()
        assert changes == ['quick']

    def test_the_highlight_follows_the_pointer(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        dropdown.window.update()
        _, y, _, height = dropdown.listbox.bbox(2)
        dropdown.listbox.event_generate('<Motion>', x=10, y=y + height // 2)
        assert dropdown.listbox.curselection() == (2,)
        dropdown.close()

    def test_the_down_arrow_on_the_button_opens_it(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.button.focus_force()
        shown.update()
        dropdown.button.event_generate('<Down>')
        shown.update()
        assert dropdown.is_open
        dropdown.close()

    def test_a_click_that_closed_it_does_not_reopen_it(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        dropdown.close()
        dropdown.toggle()
        assert not dropdown.is_open
        dropdown._closed_at = time.monotonic() - 1
        dropdown.toggle()
        assert dropdown.is_open
        dropdown.close()

    def test_it_never_grabs_input_and_is_a_borderless_window_on_top(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        assert dropdown.window.grab_current() is None
        assert dropdown.window.overrideredirect() and dropdown.window.attributes('-topmost')
        dropdown.close()

    def test_it_is_as_wide_as_its_button_at_least(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        dropdown.window.update()
        assert dropdown.window.winfo_width() >= dropdown.button.winfo_width()
        dropdown.close()

    def test_it_is_gone_when_the_dropdown_is(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        window = dropdown.window
        dropdown.destroy()
        assert not window.winfo_exists()

    def test_focus_leaving_the_list_closes_it(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        shown.update()
        shown.focus_force()
        assert wait_for(shown, lambda: not dropdown.is_open)

    def test_the_list_wears_the_palette_in_force(self, shown, light):
        dropdown, _ = self.make(shown)
        dropdown.open()
        assert str(dropdown.listbox.cget('bg')).lower() == light.palette.surface.lower()
        assert str(dropdown.listbox.cget('fg')).lower() == light.palette.text.lower()
        dropdown.close()

    def test_a_long_list_scrolls_instead_of_growing(self, shown, light):
        many = [(i, f'Choice {i}') for i in range(30)]
        dropdown = ui.Dropdown(shown, many, 0)
        dropdown.pack()
        dropdown.open()
        assert int(dropdown.listbox.cget('height')) == S.ROWS
        shown.update()
        assert dropdown.scrollbar is not None and dropdown.scrollbar.winfo_ismapped()
        dropdown.listbox.focus_force()
        shown.update()
        dropdown.listbox.event_generate('<End>')
        shown.update()
        assert dropdown.listbox.bbox(len(many) - 1) is not None
        dropdown.choose()
        assert dropdown.value == len(many) - 1 and dropdown.scrollbar is None
        dropdown.close()


# --------------------------------------------------------------------------- #
# Shortcut editor
# --------------------------------------------------------------------------- #
class FakeSource:
    def __init__(self):
        self.events = queue.Queue()
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def down(self, *names):
        for name in names:
            self.events.put(('down', name))

    def up(self, *names):
        for name in names:
            self.events.put(('up', name))


class Editor:
    """An editor in a mapped window with a fake key source and a log of what it told its owner."""

    def __init__(self, window, value='win+alt+e', default='win+alt+e', validate=None, owner='English'):
        self.source = FakeSource()
        self.changes, self.recordings = [], []
        self.widget = ui.ShortcutEditor(
            window, value, owner=owner, default=default, validate=validate, on_change=self.changes.append,
            on_recording=self.recordings.append, source_factory=lambda: self.source)
        self.widget.pack(padx=20, pady=20)
        window.update()
        self.window = window

    def caps(self):
        return self.widget.keycaps.text if self.widget.keycaps is not None else None

    def settle(self):
        wait_for(self.window, lambda: not self.widget.recording or self.source.events.empty(), 0.5)
        pump(self.window, 0.08)


@pytest.fixture
def editor(shown, light):
    return lambda **kw: Editor(shown, **kw)


class TestShortcutEditorShowsTheValue:
    def test_the_keys_as_keycaps(self, editor):
        assert editor().caps() == 'Win + Alt + E'

    def test_a_recording_style_value_is_normalised(self, editor):
        assert editor(value='Windows+Alt+J').caps() == 'Win + Alt + J'

    def test_nothing_set_says_so(self, editor):
        e = editor(value='')
        assert e.widget.keycaps is None and e.widget.hint.cget('text') == S.NOT_SET

    def test_restore_shows_only_when_it_would_change_something(self, editor):
        e = editor()
        assert not e.widget.restore_btn.winfo_manager()
        e.widget.set_value('win+alt+x')
        assert e.widget.restore_btn.winfo_manager()
        e.widget.set_value('win+alt+e')
        assert not e.widget.restore_btn.winfo_manager()

    def test_no_default_no_restore(self, editor):
        assert editor(default='').widget.restore_btn is None

    def test_set_value_does_not_call_the_owner(self, editor):
        e = editor()
        e.widget.set_value('ctrl+alt+k')
        assert e.changes == [] and e.widget.value == 'ctrl+alt+k'


class TestRecording:
    def test_change_starts_listening_and_asks_for_keys(self, editor):
        e = editor()
        e.widget.change_btn.invoke()
        assert e.widget.recording and e.source.started
        assert e.widget.change_btn.cget('text') == 'Cancel'
        assert e.widget.hint.cget('text') == S.PRESS_KEYS
        assert e.recordings == [True]

    def test_the_focus_leaves_the_button_so_space_cannot_press_it_again(self, editor, shown):
        e = editor()
        e.widget.change_btn.focus_force()
        shown.update()
        e.widget.start_recording()
        shown.update()
        assert shown.focus_get() is not e.widget.change_btn

    def test_the_modifiers_held_show_as_keycaps_while_the_combination_is_made(self, editor):
        e = editor()
        e.widget.start_recording()
        e.source.down('left windows', 'alt')
        assert wait_for(e.window, lambda: e.caps() == 'Win + Alt')

    def test_a_key_after_the_modifiers_finishes_it(self, editor):
        e = editor()
        e.widget.start_recording()
        e.source.down('left windows', 'alt', 'j')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.value == 'win+alt+j' and e.changes == ['win+alt+j']
        assert e.caps() == 'Win + Alt + J' and e.widget.message_text == ''
        assert e.source.stopped and e.recordings == [True, False]
        assert e.widget.change_btn.cget('text') == 'Change'

    def test_a_modifier_let_go_before_the_key_is_not_part_of_it(self, editor):
        e = editor()
        e.widget.start_recording()
        e.source.down('ctrl', 'alt')
        e.source.up('ctrl')
        e.source.down('k')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.value == 'alt+k'

    def test_a_plain_key_is_ignored(self, editor):
        e = editor()
        e.widget.start_recording()
        e.source.down('x')
        pump(e.window, 0.15)
        assert e.widget.recording and e.widget.value == 'win+alt+e'
        e.widget.cancel_recording()

    def test_esc_cancels_and_keeps_the_old_value(self, editor):
        e = editor()
        e.widget.start_recording()
        e.source.down('esc')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.value == 'win+alt+e' and e.changes == [] and e.source.stopped
        assert e.caps() == 'Win + Alt + E' and e.recordings == [True, False]

    def test_pressing_the_button_again_cancels(self, editor):
        e = editor()
        e.widget.change_btn.invoke()
        e.widget.change_btn.invoke()
        assert not e.widget.recording and e.source.stopped and e.recordings == [True, False]

    def test_restore_is_hidden_while_recording(self, editor):
        e = editor(value='win+alt+x')
        assert e.widget.restore_btn.winfo_manager()
        e.widget.start_recording()
        assert not e.widget.restore_btn.winfo_manager()
        e.widget.cancel_recording()
        assert e.widget.restore_btn.winfo_manager()

    def test_a_second_start_while_recording_does_nothing(self, editor):
        e = editor()
        e.widget.start_recording()
        e.widget.start_recording()
        assert e.recordings == [True]
        e.widget.cancel_recording()

    def test_events_queued_before_it_started_are_not_counted(self, editor):
        e = editor()
        e.source.down('ctrl', 'alt', 'z')
        e.widget.start_recording()
        pump(e.window, 0.1)
        # the fake source keeps its queue (a real one starts empty); the stale keys would finish it,
        # which is why the real KeySource is created fresh for each recording
        e.widget.cancel_recording()

    def test_a_keyboard_that_cannot_be_listened_to_says_so(self, shown, light):
        calls = []

        def broken():
            raise OSError('no hook')
        widget = ui.ShortcutEditor(shown, 'win+alt+e', owner='English', source_factory=broken,
                                   on_recording=calls.append)
        widget.pack()
        widget.start_recording()
        assert not widget.recording and 'could not listen' in widget.message_text and calls == []

    def test_each_recording_gets_a_fresh_source(self, shown, light):
        made = []

        def factory():
            made.append(FakeSource())
            return made[-1]
        widget = ui.ShortcutEditor(shown, 'win+alt+e', owner='English', source_factory=factory)
        widget.pack()
        widget.start_recording()
        widget.cancel_recording()
        widget.start_recording()
        widget.cancel_recording()
        assert len(made) == 2 and made[0] is not made[1] and all(m.stopped for m in made)

    def test_closing_the_window_while_recording_stops_the_source_and_says_so(self, shown, light):
        e = Editor(shown)
        e.widget.start_recording()
        e.widget.destroy()
        assert e.source.stopped and e.recordings == [True, False]


class TestTheVerdict:
    def test_an_error_keeps_the_old_value_and_says_why_in_the_danger_colour(self, editor, light):
        taken = {'Japanese': 'win+alt+j'}
        e = editor(validate=lambda combo: rules.validate(combo, 'English', taken))
        e.widget.start_recording()
        e.source.down('win', 'alt', 'j')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.value == 'win+alt+e' and e.changes == []
        assert 'already used for Japanese' in e.widget.message_text
        assert str(e.widget.message.cget('foreground')).lower() == light.palette.danger.lower()
        assert e.caps() == 'Win + Alt + E'

    def test_a_warning_keeps_the_new_value_and_says_why_in_the_warning_colour(self, editor, light):
        e = editor(owner='Fix grammar', default='win+alt+g', value='win+alt+h')
        e.widget.start_recording()
        e.source.down('win', 'alt', 'g')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.value == 'win+alt+g' and e.changes == ['win+alt+g']
        assert 'Xbox Game Bar' in e.widget.message_text
        assert str(e.widget.message.cget('foreground')).lower() == light.palette.warning.lower()

    def test_a_clean_recording_clears_an_old_message(self, editor):
        e = editor()
        e.widget.show_message('error', 'Old problem.')
        e.widget.start_recording()
        assert e.widget.message_text == ''
        e.source.down('win', 'alt', 'k')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.message_text == '' and not e.widget.message.winfo_manager()

    def test_the_default_judgement_is_the_rules_alone(self, editor):
        e = editor()
        e.widget.start_recording()
        e.source.down('alt', 'f4')
        assert wait_for(e.window, lambda: not e.widget.recording)
        assert e.widget.value == 'win+alt+e' and e.changes == []
        assert 'belongs to Windows' in e.widget.message_text

    def test_restore_puts_the_default_back_through_the_same_judgement(self, editor):
        e = editor(value='win+alt+x')
        e.widget.restore()
        assert e.widget.value == 'win+alt+e' and e.changes == ['win+alt+e']

    def test_restore_is_refused_when_the_default_is_taken(self, editor):
        e = editor(value='win+alt+x', validate=lambda combo: rules.Verdict(rules.ERROR, 'Taken.', combo))
        e.widget.restore()
        assert e.widget.value == 'win+alt+x' and e.changes == [] and e.widget.message_text == 'Taken.'

    def test_restore_while_recording_cancels_first(self, editor):
        e = editor(value='win+alt+x')
        e.widget.start_recording()
        e.widget.restore()
        assert not e.widget.recording and e.widget.value == 'win+alt+e'

    def test_a_message_follows_the_theme(self, editor, light):
        e = editor()
        e.widget.show_message('error', 'Problem.')
        light.manager.set_mode('dark')
        assert str(e.widget.message.cget('foreground')).lower() == light.palette.danger.lower()
        light.manager.set_mode('light')
        assert str(e.widget.message.cget('foreground')).lower() == light.palette.danger.lower()

    def test_a_cleared_message_takes_no_room(self, editor):
        e = editor()
        e.widget.show_message('warning', 'Careful.')
        assert e.widget.message.winfo_manager()
        e.widget.show_message('', '')
        assert not e.widget.message.winfo_manager()


class TestStatusAndRevalidation:
    def test_a_status_shows_as_a_warning(self, editor, light):
        e = editor()
        e.widget.set_status('Another app uses this one.')
        assert e.widget.message_text == 'Another app uses this one.'
        assert str(e.widget.message.cget('foreground')).lower() == light.palette.warning.lower()
        assert e.widget.message.winfo_manager()

    def test_clearing_the_status_removes_it(self, editor):
        e = editor()
        e.widget.set_status('Another app uses this one.')
        e.widget.set_status('')
        assert e.widget.message_text == '' and not e.widget.message.winfo_manager()

    def test_the_editors_own_message_outranks_a_status(self, editor):
        e = editor()
        e.widget.set_status('Another app uses this one.')
        e.widget.show_message('error', 'Already used for Japanese.')
        assert e.widget.message_text == 'Already used for Japanese.'
        e.widget.show_message('', '')
        assert e.widget.message_text == 'Another app uses this one.'

    def test_a_status_survives_a_message_coming_and_going(self, editor):
        e = editor()
        e.widget.set_status('Taken.')
        e.widget.show_message('warning', 'Careful.')
        e.widget.show_message('', '')
        assert e.widget.message_text == 'Taken.'

    def test_revalidate_says_what_is_wrong_with_the_saved_value_and_keeps_it(self, editor):
        e = editor(owner='Fix grammar', value='win+alt+g', default='win+alt+g')
        e.widget.revalidate()
        assert 'Xbox Game Bar' in e.widget.message_text and e.widget.value == 'win+alt+g'
        assert e.changes == []

    def test_revalidate_reports_a_conflict_in_what_is_saved(self, editor):
        taken = {'Japanese': 'win+alt+e'}
        e = editor(validate=lambda combo: rules.validate(combo, 'English', taken))
        e.widget.revalidate()
        assert 'already used for Japanese' in e.widget.message_text
        assert e.widget.value == 'win+alt+e' and e.changes == []        # told, never changed

    def test_revalidate_clears_a_message_that_no_longer_applies(self, editor):
        e = editor()
        e.widget.show_message('error', 'Old problem.')
        e.widget.revalidate()
        assert e.widget.message_text == ''

    def test_revalidate_does_nothing_for_an_empty_value(self, editor):
        e = editor(value='')
        e.widget.revalidate()
        assert e.widget.message_text == ''

    def test_revalidate_leaves_a_recording_alone(self, editor):
        e = editor()
        e.widget.start_recording()
        e.widget.show_message('error', 'x')
        e.widget.revalidate()
        assert e.widget.message_text == 'x'
        e.widget.cancel_recording()


class TestKeySource:
    def test_it_only_queues_what_the_hook_reports(self):
        source = S.KeySource()
        source._on_event(SimpleNamespace(event_type='down', name='left windows'))
        source._on_event(SimpleNamespace(event_type='up', name='e'))
        assert source.events.get_nowait() == ('down', 'left windows')
        assert source.events.get_nowait() == ('up', 'e')
        assert source.events.empty()

    def test_stopping_what_never_started_is_harmless(self):
        S.KeySource().stop()

    def test_it_is_the_default_source(self, shown, light):
        widget = ui.ShortcutEditor(shown, 'win+alt+e', owner='English')
        assert widget._source_factory is S.KeySource

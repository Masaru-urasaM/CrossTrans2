"""
Tests for the Translator's language chooser (TODO.md U7, UQ8, V19): a button that opens a
searchable list with Recent and Pinned on top. The list is a real toplevel on the private test
desktop; what these pin is what a person does - open, type, arrow, Enter, Esc, click - and the
rules the old list broke or the dropdown must keep (no grab, closes on its own, on the screen).
"""
import time
import tkinter as tk

import pytest

from src.constants import LANGUAGES
from src.core import language_prefs as prefs
from src.ui import components as ui
from src.ui.components import language_picker as picker_module

from tests.test_components import env, holder, light  # noqa: F401  (shared fixtures)

SMALL = [("Vietnamese", "vi", "Ti" + chr(0x1EBF) + "ng Vi" + chr(0x1EC7) + "t"),
         ("English", "en", "English"),
         ("Japanese", "ja", chr(0x65E5) + chr(0x672C) + chr(0x8A9E)),
         ("French", "fr", "Fran" + chr(0xE7) + "ais"),
         ("German", "de", "Deutsch")]


def make(holder, languages=SMALL, value="English", recent=("French",), pinned=("English", "Japanese"),
         changes=None):
    changes = [] if changes is None else changes
    picker = ui.LanguagePicker(holder, languages, value, changes.append,
                               recent=lambda: list(recent), pinned=lambda: list(pinned))
    picker.pack()
    if not isinstance(holder, tk.Toplevel):
        holder.pack()
    holder.update()
    return picker, changes


def kinds(picker):
    return [kind for kind, _ in picker.rows]


def headers(picker):
    return [payload for kind, payload in picker.rows if kind == picker_module.HEADER]


def language_names(picker):
    return [payload[0] for kind, payload in picker.rows if kind == picker_module.LANGUAGE]


@pytest.fixture
def window(tk_root):
    """A real, mapped toplevel: key events go to the window with the focus."""
    top = tk.Toplevel(tk_root)
    top.geometry('400x200+40+40')
    top.update()
    yield top
    top.destroy()


@pytest.fixture
def opened(holder, light):
    picker, changes = make(holder)
    picker.open()
    picker.window.update()
    yield picker, changes
    picker.close()


class TestTheButton:
    def test_it_names_the_language(self, holder, light):
        picker, _ = make(holder, value="Japanese")
        assert picker.value == "Japanese"
        assert picker.button.cget('text') == "Japanese"

    def test_it_carries_a_down_chevron_after_the_name(self, holder, light):
        picker, _ = make(holder)
        assert picker.button.icon_name == 'chevron-down'
        assert str(picker.button.cget('compound')) == 'right'

    def test_it_has_a_tooltip_naming_its_job(self, holder, light):
        picker, _ = make(holder)
        assert picker.tooltip.text == 'Translate to'

    def test_setting_the_value_does_not_call_the_owner(self, holder, light):
        picker, changes = make(holder)
        picker.set("French")
        assert picker.button.cget('text') == "French" and picker.value == "French"
        assert changes == []

    def test_it_sits_on_the_plane_it_is_given(self, holder, light):
        picker = ui.LanguagePicker(holder, SMALL, "English", lambda name: None, on='surface')
        assert picker.plane == 'surface'
        assert picker.button.plane == 'surface'


class TestOpening:
    def test_it_is_closed_until_asked(self, holder, light):
        picker, _ = make(holder)
        assert not picker.is_open and picker.window is None

    def test_pressing_the_button_opens_it_and_again_closes_it(self, holder, light):
        picker, _ = make(holder)
        picker.button.invoke()
        assert picker.is_open and picker.window.winfo_exists()
        picker.button.invoke()
        assert not picker.is_open
        picker.close()

    def test_the_down_arrow_on_the_button_opens_it(self, window, light):
        picker, _ = make(window)
        window.update()
        picker.button.focus_force()
        window.update()
        picker.button.event_generate('<Down>')
        window.update()
        assert picker.is_open
        picker.close()

    def test_a_click_that_closed_it_does_not_reopen_it(self, holder, light):
        picker, _ = make(holder)
        picker.open()
        picker.close()                       # focus left it, as it does when its button is clicked
        picker.toggle()
        assert not picker.is_open
        picker._closed_at = time.monotonic() - 1
        picker.toggle()
        assert picker.is_open
        picker.close()

    def test_it_never_grabs_input(self, opened):
        picker, _ = opened
        assert picker.window.grab_current() is None

    def test_it_is_borderless_and_on_top(self, opened):
        picker, _ = opened
        assert picker.window.overrideredirect()
        assert picker.window.attributes('-topmost')

    def test_it_is_gone_when_the_picker_is(self, holder, light):
        picker, _ = make(holder)
        picker.open()
        window = picker.window
        picker.destroy()
        assert not window.winfo_exists()

    def test_closing_twice_is_harmless(self, opened):
        picker, _ = opened
        picker.close()
        picker.close()
        assert not picker.is_open

    def test_the_search_field_has_the_focus_so_typing_searches(self, opened):
        picker, _ = opened
        assert picker.search.focus_get() is picker.search or picker.window.focus_get() is picker.search

    def test_focus_leaving_the_list_closes_it(self, opened, window):
        picker, _ = opened
        window.update()
        window.focus_force()
        deadline = time.monotonic() + 1.0
        while picker.is_open and time.monotonic() < deadline:
            window.update()
            time.sleep(0.02)
        assert not picker.is_open


class TestTheRows:
    def test_recent_pinned_then_everything(self, opened):
        picker, _ = opened
        assert headers(picker) == [prefs.RECENT, prefs.PINNED, prefs.ALL]
        assert language_names(picker) == ["French", "English", "Japanese"] + [l[0] for l in SMALL]

    def test_it_opens_scrolled_to_the_top_when_the_current_language_is_near_it(self, opened):
        picker, _ = opened
        assert picker.listbox.yview()[0] == 0.0
        assert picker.listbox.nearest(0) == 0

    def test_a_language_far_down_is_scrolled_into_view_with_its_section(self, holder, light):
        picker, _ = make(holder, languages=LANGUAGES, recent=(), pinned=(), value=LANGUAGES[60][0])
        picker.open()
        picker.window.update()
        index = picker.listbox.curselection()[0]
        first, last = picker.listbox.nearest(0), picker.listbox.nearest(picker.listbox.winfo_height() - 1)
        assert first <= index <= last
        picker.close()

    def test_the_first_row_of_a_section_is_shown_with_its_title(self, holder, light):
        picker, _ = make(holder, languages=LANGUAGES, recent=("French",), pinned=("English", "Japanese"),
                         value="English")
        picker.open()
        picker.window.update()
        all_title = next(i for i, (kind, payload) in enumerate(picker.rows)
                         if kind == picker_module.HEADER and payload == prefs.ALL)
        picker.listbox.yview(all_title + 2)          # the section's first row is just above the view
        picker.window.update()
        assert picker.listbox.nearest(0) == all_title + 2
        picker._see(all_title + 1)
        picker.window.update()
        assert picker.listbox.nearest(0) <= all_title
        picker.close()

    def test_a_header_is_a_row_but_never_a_choice(self, opened):
        picker, _ = opened
        assert kinds(picker)[0] == picker_module.HEADER
        picker._select(0)
        assert picker.selected_language is None

    def test_the_current_language_is_highlighted_where_it_first_appears(self, opened):
        picker, _ = opened
        assert picker.selected_language[0] == "English"
        assert picker.listbox.curselection()[0] == 3         # header, French, header, English

    def test_a_native_name_shows_beside_the_english_one_only_when_different(self, opened):
        picker, _ = opened
        texts = [picker.listbox.get(i) for i in range(picker.listbox.size())]
        assert any(chr(0x65E5) in text and "Japanese" in text for text in texts)
        assert any(text.strip() == "English" for text in texts)

    def test_the_whole_real_list_is_reachable(self, holder, light):
        picker, _ = make(holder, languages=LANGUAGES, recent=(), pinned=())
        picker.open()
        assert language_names(picker) == [language[0] for language in LANGUAGES]
        picker.close()

    def test_a_stale_recent_name_is_ignored(self, holder, light):
        picker, _ = make(holder, recent=("Klingon", "German"))
        picker.open()
        assert language_names(picker)[0] == "German"
        picker.close()

    def test_no_recent_no_pinned_just_all(self, holder, light):
        picker, _ = make(holder, recent=(), pinned=())
        picker.open()
        assert headers(picker) == [prefs.ALL]
        picker.close()


class TestSearching:
    def test_typing_narrows_to_one_results_list(self, opened):
        picker, _ = opened
        picker.query.set("ger")
        picker.update()
        assert headers(picker) == []
        assert language_names(picker) == ["German"]
        assert picker.selected_language[0] == "German"

    def test_the_native_name_is_searched_too(self, opened):
        picker, _ = opened
        picker.query.set(chr(0x65E5))
        assert language_names(picker) == ["Japanese"]

    def test_no_match_shows_nothing_and_chooses_nothing(self, opened):
        picker, changes = opened
        picker.query.set("zzzz")
        assert picker.rows == [] and picker.selected_language is None
        picker.choose()
        assert changes == [] and picker.is_open

    def test_clearing_the_query_brings_the_sections_back(self, opened):
        picker, _ = opened
        picker.query.set("ger")
        picker.query.set("")
        assert headers(picker) == [prefs.RECENT, prefs.PINNED, prefs.ALL]


def type_text(picker, text):
    """Press and release real keys in the search field, as a person typing does."""
    picker.search.focus_force()
    picker.window.update()
    for char in text:
        picker.search.event_generate('<KeyPress>', keysym=char)
        picker.search.event_generate('<KeyRelease>', keysym=char)
    picker.window.update()


class TestTyping:
    def test_typing_narrows_the_list_as_it_goes(self, opened):
        picker, _ = opened
        type_text(picker, 'ger')
        assert picker.search.get() == 'ger'
        assert language_names(picker) == ["German"]

    def test_each_letter_narrows_it_further(self, opened):
        picker, _ = opened
        type_text(picker, 'f')
        assert "French" in language_names(picker) and "German" not in language_names(picker)
        type_text(picker, 'r')
        assert language_names(picker) == ["French"]

    def test_deleting_the_text_brings_the_sections_back(self, opened):
        picker, _ = opened
        type_text(picker, 'ger')
        picker.search.delete(0, 'end')
        picker.search.event_generate('<KeyRelease>', keysym='BackSpace')
        picker.window.update()
        assert headers(picker) == [prefs.RECENT, prefs.PINNED, prefs.ALL]

    def test_a_paste_is_read_once_it_has_been_done(self, opened):
        picker, _ = opened
        # Without the class bindings the event only reaches ours: the real Paste would read the
        # developer's clipboard. The text is put in by hand, as the class binding would have done first.
        picker.search.bindtags((str(picker.search),))
        picker.search.insert(0, 'fre')
        assert len(language_names(picker)) > 1                  # the list has not followed the text yet
        picker.search.event_generate('<<Paste>>')
        picker.window.update()
        assert language_names(picker) == ["French"]

    def test_a_cut_is_read_too(self, opened):
        picker, _ = opened
        type_text(picker, 'ger')
        picker.search.bindtags((str(picker.search),))
        picker.search.delete(0, 'end')                          # what the class binding's cut leaves
        picker.search.event_generate('<<Cut>>')
        picker.window.update()
        assert headers(picker) == [prefs.RECENT, prefs.PINNED, prefs.ALL]

    def test_an_arrow_key_s_release_does_not_rebuild_the_list_under_the_highlight(self, opened):
        picker, _ = opened
        picker.search.focus_force()
        picker.window.update()
        picker.search.event_generate('<KeyPress>', keysym='Down')
        picker.search.event_generate('<KeyRelease>', keysym='Down')
        picker.window.update()
        assert picker.selected_language[0] == "Japanese"        # one row down from English, and it stayed
        picker.search.event_generate('<KeyRelease>', keysym='Down')
        picker.window.update()
        assert picker.selected_language[0] == "Japanese"

    def test_typing_then_enter_chooses_what_was_typed_for(self, opened):
        picker, changes = opened
        type_text(picker, 'fre')
        picker.search.event_generate('<Return>')
        picker.update()
        assert changes == ["French"]


class TestKeys:
    def test_down_and_up_skip_the_headers(self, opened):
        picker, _ = opened
        picker._select(1)                                    # French, first language row
        picker._move(1)
        assert picker.selected_language[0] == "English"      # past the Pinned header
        picker._move(-1)
        assert picker.selected_language[0] == "French"

    def test_the_ends_hold(self, opened):
        picker, _ = opened
        for _ in range(40):
            picker._move(1)
        assert picker.selected_language[0] == "German"
        for _ in range(40):
            picker._move(-1)
        assert picker.selected_language[0] == "French"

    def test_moving_with_nothing_selected_starts_at_an_end(self, opened):
        picker, _ = opened
        picker.query.set("zzz")
        picker.query.set("e")
        picker.listbox.selection_clear(0, 'end')
        picker._move(1)
        assert picker.selected_language is not None

    def test_the_arrow_keys_work_from_the_search_field(self, opened):
        picker, _ = opened
        picker.search.focus_force()
        picker.window.update()
        before = picker.selected_language[0]
        picker.search.event_generate('<Down>')
        picker.window.update()
        assert picker.selected_language[0] != before

    def test_enter_chooses_the_highlighted_language(self, opened):
        picker, changes = opened
        picker.query.set("fre")
        picker.search.focus_force()
        picker.window.update()
        picker.search.event_generate('<Return>')
        picker.update()
        assert changes == ["French"]
        assert picker.value == "French" and picker.button.cget('text') == "French"
        assert not picker.is_open

    def test_escape_closes_without_choosing(self, opened):
        picker, changes = opened
        picker.search.focus_force()
        picker.window.update()
        picker.search.event_generate('<Escape>')
        picker.update()
        assert not picker.is_open and changes == []
        assert picker.value == "English"

    def test_paging_moves_a_window_of_rows(self, holder, light):
        picker, _ = make(holder, languages=LANGUAGES, recent=(), pinned=(), value=LANGUAGES[0][0])
        picker.open()
        picker._move(picker_module.ROWS)
        assert picker.selected_language[0] == LANGUAGES[picker_module.ROWS][0]
        picker.close()


class TestMouse:
    def row_y(self, picker, index):
        picker.listbox.see(index)
        picker.listbox.update()
        x, y, width, height = picker.listbox.bbox(index)
        return y + height // 2

    def test_clicking_a_row_chooses_it(self, opened):
        picker, changes = opened
        index = [i for i, (_, payload) in enumerate(picker.rows)
                 if isinstance(payload, tuple) and payload[0] == "Japanese"][0]
        picker.listbox.event_generate('<ButtonRelease-1>', x=10, y=self.row_y(picker, index))
        picker.update()
        assert changes == ["Japanese"] and not picker.is_open

    def test_clicking_a_header_chooses_nothing(self, opened):
        picker, changes = opened
        picker.listbox.event_generate('<ButtonRelease-1>', x=10, y=self.row_y(picker, 0))
        picker.update()
        assert changes == [] and picker.is_open

    def test_the_highlight_follows_the_pointer(self, opened):
        picker, _ = opened
        index = [i for i, (_, payload) in enumerate(picker.rows)
                 if isinstance(payload, tuple) and payload[0] == "German"][0]
        picker.listbox.event_generate('<Motion>', x=10, y=self.row_y(picker, index))
        assert picker.selected_language[0] == "German"

    def test_pointing_at_a_header_leaves_the_highlight_alone(self, opened):
        picker, _ = opened
        before = picker.selected_language[0]
        picker.listbox.event_generate('<Motion>', x=10, y=self.row_y(picker, 0))
        assert picker.selected_language[0] == before


class TestPlacementAndLook:
    def test_it_opens_under_the_button_at_least_as_wide(self, opened):
        picker, _ = opened
        window, button = picker.window, picker.button
        assert window.winfo_width() >= button.winfo_width()
        assert window.winfo_rooty() >= button.winfo_rooty() + button.winfo_height() - 1

    def test_it_stays_on_the_work_area(self, opened):
        picker, _ = opened
        left, top, right, bottom = picker_module._work_area(picker.window)
        window = picker.window
        assert window.winfo_rootx() >= left and window.winfo_rooty() >= top
        assert window.winfo_rootx() + window.winfo_width() <= right
        assert window.winfo_rooty() + window.winfo_height() <= bottom

    def test_the_list_wears_the_palette_in_force(self, holder, env):
        for mode in ('light', 'dark'):
            env.manager.set_mode(mode)
            picker, _ = make(holder)
            picker.open()
            palette = env.palette
            assert str(picker.listbox.cget('background')) == palette.surface
            assert str(picker.listbox.cget('selectbackground')) == palette.soft
            assert str(picker.listbox.cget('foreground')) == palette.text
            picker.close()
        env.manager.set_mode('light')

    def test_the_scroll_bar_shows_only_for_a_long_list(self, holder, light):
        picker, _ = make(holder, languages=LANGUAGES, recent=(), pinned=())
        picker.open()
        picker.window.update()
        picker.update()
        assert picker.scrollbar.shown
        picker.query.set("japan")
        picker.window.update()
        picker.window.update_idletasks()
        picker.update()
        assert not picker.scrollbar.shown
        picker.close()

"""
Tests for the Settings model chooser (TODO.md U8, step 4): the language picker's dropdown over a
provider's models, where a name the list lacks may be typed and used.
"""
import tkinter as tk

import pytest

from src.ui import components as ui
from src.ui.components import language_picker as picker_module
from src.ui.components import model_picker as module

from tests.test_components import env, holder, light  # noqa: F401  (shared fixtures)

MODELS = ['Auto', 'gemini-2.5-flash', 'gemini-2.5-pro', 'gemma-3', 'llama-3.3-70b']
LONG = 'meta-llama/llama-4-maverick-17b-128e-instruct-fp8-long-name'


def make(holder, models=MODELS, value='gemini-2.5-flash', changes=None):
    changes = [] if changes is None else changes
    picker = ui.ModelPicker(holder, models, value, changes.append)
    picker.pack()
    if not isinstance(holder, tk.Toplevel):
        holder.pack()
    holder.update()
    return picker, changes


def names(picker):
    return [payload[0] for kind, payload in picker.rows if kind == picker_module.LANGUAGE]


def texts(picker):
    return [picker.listbox.get(i).strip() for i in range(picker.listbox.size())]


@pytest.fixture
def opened(holder, light):
    picker, changes = make(holder)
    picker.open()
    picker.window.update()
    yield picker, changes
    picker.close()


class TestShorten:
    def test_a_name_that_fits_is_whole(self):
        assert module.shorten('gemma-3') == 'gemma-3'

    def test_a_name_exactly_at_the_limit_is_whole(self):
        assert module.shorten('x' * 10, 10) == 'x' * 10

    def test_a_longer_name_is_cut_to_the_limit_with_dots(self):
        out = module.shorten('abcdefghijkl', 10)
        assert out == 'abcdefg...' and len(out) == 10

    def test_the_default_limit_is_the_field_width(self):
        assert len(module.shorten(LONG)) == module.BUTTON_CHARS


class TestTheButton:
    def test_it_names_the_model(self, holder, light):
        picker, _ = make(holder)
        assert picker.value == 'gemini-2.5-flash'
        assert picker.button.cget('text') == 'gemini-2.5-flash'

    def test_an_empty_model_is_auto(self, holder, light):
        picker, _ = make(holder, value='')
        assert picker.value == 'Auto' and picker.button.cget('text') == 'Auto'

    def test_a_long_name_is_cut_on_the_button_but_whole_as_the_value(self, holder, light):
        picker, _ = make(holder, value=LONG)
        assert picker.value == LONG
        assert picker.button.cget('text') != LONG
        assert LONG.startswith(str(picker.button.cget('text')).rstrip(chr(0x2026) + '.'))

    def test_the_field_has_a_fixed_width_so_a_long_name_cannot_widen_the_page(self, holder, light):
        short, _ = make(holder, value='gemma-3')
        longer, _ = make(holder, value=LONG)
        assert short.winfo_reqwidth() == longer.winfo_reqwidth() == light.px(module.FIELD_WIDTH)

    def test_it_carries_a_chevron_and_a_tooltip_naming_its_job(self, holder, light):
        picker, _ = make(holder)
        assert picker.button.icon_name == 'chevron-down'
        assert picker.tooltip.text == 'Model'

    def test_setting_it_does_not_call_the_owner(self, holder, light):
        picker, changes = make(holder)
        picker.set('gemma-3')
        assert picker.value == 'gemma-3' and picker.button.cget('text') == 'gemma-3'
        assert changes == []

    def test_setting_an_empty_name_shows_auto(self, holder, light):
        picker, _ = make(holder)
        picker.set('')
        assert picker.value == 'Auto' and picker.button.cget('text') == 'Auto'

    def test_setting_a_long_name_cuts_the_button_text(self, holder, light):
        picker, _ = make(holder)
        picker.set(LONG)
        assert picker.value == LONG and picker.button.cget('text') != LONG
        assert LONG.startswith(str(picker.button.cget('text')).rstrip(chr(0x2026) + '.'))

    def test_the_language_picker_keeps_its_own_hint(self):
        assert picker_module.LanguagePicker.PLACEHOLDER == 'Search languages'
        assert ui.ModelPicker.PLACEHOLDER == 'Search or type a model'


class TestTheList:
    def test_it_lists_the_models_in_the_order_given_with_no_section_titles(self, opened):
        picker, _ = opened
        assert names(picker) == MODELS
        assert all(kind == picker_module.LANGUAGE for kind, _ in picker.rows)

    def test_the_current_model_is_highlighted(self, opened):
        picker, _ = opened
        assert picker.selected_language[0] == 'gemini-2.5-flash'

    def test_a_model_the_list_lacks_opens_on_the_first_row(self, holder, light):
        picker, _ = make(holder, value='my-own-model')
        picker.open()
        assert picker.selected_language[0] == 'Auto'
        picker.close()

    def test_the_search_field_carries_the_models_hint(self, opened):
        picker, _ = opened
        assert picker.search._placeholder == 'Search or type a model'

    def test_it_is_wider_than_the_field_so_long_names_show_whole(self, opened):
        picker, _ = opened
        assert picker.window.winfo_width() >= ui.get().px(module.DROP_MIN_WIDTH)
        assert picker.window.winfo_width() >= picker.button.winfo_width()

    def test_it_never_grabs_input(self, opened):
        picker, _ = opened
        assert picker.window.grab_current() is None


class TestSearching:
    def test_typing_narrows_best_match_first(self, opened):
        picker, _ = opened
        picker.query.set('gem')
        picker.update()
        assert names(picker)[:3] == ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemma-3']

    def test_a_word_inside_a_name_is_found(self, opened):
        picker, _ = opened
        picker.query.set('flash')
        assert names(picker)[0] == 'gemini-2.5-flash'

    def test_the_best_match_is_highlighted(self, opened):
        picker, _ = opened
        picker.query.set('llama')
        assert picker.selected_language[0] == 'llama-3.3-70b'

    def test_an_unknown_name_is_offered_as_use_what_i_typed(self, opened):
        picker, _ = opened
        picker.query.set('my-model')
        assert picker.rows == [(picker_module.LANGUAGE, ('my-model', '', ''))]
        quote = chr(0x201C) + 'my-model' + chr(0x201D)
        assert texts(picker) == ['Use ' + quote]
        assert picker.selected_language[0] == 'my-model'

    def test_the_offer_comes_after_the_matches(self, opened):
        picker, _ = opened
        picker.query.set('gemini-2.5-fl')
        assert names(picker) == ['gemini-2.5-flash', 'gemini-2.5-fl']
        assert picker.selected_language[0] == 'gemini-2.5-flash'

    def test_a_name_already_in_the_list_is_not_offered_again(self, opened):
        picker, _ = opened
        picker.query.set('GEMMA-3')
        assert names(picker) == ['gemma-3']

    def test_spaces_around_the_typed_name_are_ignored(self, opened):
        picker, _ = opened
        picker.query.set('  my-model ')
        assert names(picker) == ['my-model']

    def test_clearing_the_query_brings_the_whole_list_back(self, opened):
        picker, _ = opened
        picker.query.set('gem')
        picker.query.set('')
        assert names(picker) == MODELS

    def test_a_blank_query_offers_nothing_extra(self, opened):
        picker, _ = opened
        picker.query.set('   ')
        assert names(picker) == MODELS


class TestTyping:
    def type_text(self, picker, text):
        picker.search.focus_force()
        picker.window.update()
        for char in text:
            picker.search.event_generate('<KeyPress>', keysym=char)
            picker.search.event_generate('<KeyRelease>', keysym=char)
        picker.window.update()

    def test_typing_narrows_the_models(self, opened):
        picker, _ = opened
        self.type_text(picker, 'gem')
        assert names(picker)[:3] == ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemma-3']
        assert 'llama-3.3-70b' not in names(picker)

    def test_typing_a_name_the_list_lacks_offers_it_and_enter_uses_it(self, opened):
        picker, changes = opened
        self.type_text(picker, 'zed')
        assert names(picker) == ['zed']
        picker.search.event_generate('<Return>')
        picker.update()
        assert changes == ['zed'] and picker.value == 'zed'


class TestChoosing:
    def test_choosing_a_model_tells_the_owner_and_shows_it(self, opened):
        picker, changes = opened
        picker.query.set('llama')
        picker.choose()
        assert changes == ['llama-3.3-70b']
        assert picker.value == 'llama-3.3-70b' and picker.button.cget('text') == 'llama-3.3-70b'
        assert not picker.is_open

    def test_a_typed_name_is_chosen_as_typed(self, opened):
        picker, changes = opened
        picker.query.set('  brand-new-model ')
        picker.choose()
        assert changes == ['brand-new-model']
        assert picker.value == 'brand-new-model'

    def test_choosing_auto_says_auto(self, opened):
        picker, changes = opened
        picker.query.set('auto')
        picker.choose()
        assert changes == ['Auto']

    def test_enter_in_the_search_field_chooses(self, opened):
        picker, changes = opened
        picker.query.set('my-model')
        picker.search.focus_force()
        picker.window.update()
        picker.search.event_generate('<Return>')
        picker.update()
        assert changes == ['my-model'] and not picker.is_open

    def test_a_click_chooses_the_row(self, opened):
        picker, changes = opened
        picker.choose(picker.rows[3][1])
        assert changes == ['gemma-3']

    def test_escape_closes_without_choosing(self, opened):
        picker, changes = opened
        picker.search.focus_force()
        picker.window.update()
        picker.search.event_generate('<Escape>')
        picker.update()
        assert changes == [] and not picker.is_open and picker.value == 'gemini-2.5-flash'


class TestChangingTheList:
    def test_set_models_replaces_the_list_the_next_open_shows(self, holder, light):
        picker, _ = make(holder)
        picker.set_models(['Auto', 'a', 'b'])
        assert picker.models == ['Auto', 'a', 'b']
        picker.open()
        assert names(picker) == ['Auto', 'a', 'b']
        picker.close()

    def test_the_value_shown_stays_when_the_list_changes(self, holder, light):
        picker, changes = make(holder, value='gemma-3')
        picker.set_models(['Auto', 'a'])
        assert picker.value == 'gemma-3' and picker.button.cget('text') == 'gemma-3'
        assert changes == []

    def test_an_open_list_follows_the_change(self, opened):
        picker, _ = opened
        picker.set_models(['Auto', 'x', 'y'])
        assert names(picker) == ['Auto', 'x', 'y']

    def test_an_open_list_keeps_what_was_typed_while_following_the_change(self, opened):
        picker, _ = opened
        picker.query.set('x')
        picker.set_models(['Auto', 'x1', 'x2', 'y'])
        assert names(picker) == ['x1', 'x2', 'x']

    def test_the_models_are_read_back_without_the_picker_s_row_shape(self, holder, light):
        picker, _ = make(holder, models=['Auto', 'a'])
        assert picker.models == ['Auto', 'a']

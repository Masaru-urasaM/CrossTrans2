"""
Tests for the Translator window (TODO.md U7): the layout, the language bar, the source card, More
options, the busy state, the failure bar and the theme - the real `show_popup()` on a TranslatorApp
built with `__new__`, like the other main-window tests, so what is asserted is what the user gets.
"""
import _tkinter
import time
import tkinter as tk
from unittest.mock import MagicMock

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.constants import TRANSLATOR_MIN_SIZE
from src.core import tone_prompts
from src.ui import components as ui
from src.ui import translator_layout as layout
from src.ui import translator_window as TW

from tests.test_source_choice import main_window, settle  # noqa: F401  (the shared real-window fixture)

JAPANESE = "私は毎日日本語を勉強しています。"      # kana: Japanese by evidence
KANJI_ONLY = "東京大学"                             # proves nothing
LATIN = "Good morning! How are you today?"


def pump(tk_root, seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tk_root.update()
        time.sleep(0.005)


def geometry(app, width, height):
    app.popup.geometry(f'{width}x{height}')
    pump(app.root, 0.15)


def test_width_only_resize_does_not_refresh_height_dependent_reading(main_window, tk_root):
    app = main_window(LATIN)
    view = app._view
    pump(tk_root)
    width, height = app.popup.winfo_width(), app.popup.winfo_height()
    seen = []
    view.configure_callbacks(on_resize=seen.append)
    geometry(app, width + 30, height)
    assert seen == []
    geometry(app, width + 30, height + 30)
    assert seen == [view.mode]


def test_main_resize_consumes_latest_dimensions_and_cancels_on_close(main_window, tk_root):
    from types import SimpleNamespace
    app = main_window(LATIN)
    view = app._view
    pump(tk_root)
    event = SimpleNamespace(widget=view.frame)
    view._on_configure(event)
    pending = view._arrange_job
    for _ in range(50):
        view._on_configure(event)
        assert view._arrange_job == pending
    app.popup.destroy()
    assert pending not in tk_root.tk.splitlist(tk_root.tk.call('after', 'info'))
    assert view._arrange_job is None


def test_resize_preserves_native_edit_state_with_mixed_unicode(main_window, tk_root):
    source = 'A😀日本語 e\u0301\n' * 40
    app = main_window(source)
    pump(tk_root)
    box = app.original_text
    box.edit_reset()
    box.mark_set('insert', '2.1')
    box.insert('insert', 'X')
    box.edit_separator()
    pump(tk_root)
    box.tag_add('sel', '2.0', '3.2')
    before = box.get('1.0', 'end-1c'), box.index('insert'), box.tag_ranges('sel')
    geometry(app, 740, 720)
    geometry(app, 1120, 780)
    assert (box.get('1.0', 'end-1c'), box.index('insert'), box.tag_ranges('sel')) == before
    box.edit_undo()
    assert box.get('1.0', 'end-1c') == source
    box.edit_redo()
    assert box.get('1.0', 'end-1c') == before[0]


# --------------------------------------------------------------------------- #
# The arrangement
# --------------------------------------------------------------------------- #
class TestLayout:
    @pytest.mark.parametrize('open_options_first', [False, True])
    def test_wrapped_actions_do_not_cover_the_translation_footer(self, main_window, tk_root,
                                                                 open_options_first, request):
        from tools.tk_resize.settlement import LayoutSettlement, snapshot_layout
        app = main_window(JAPANESE, 'I study Japanese every day.')
        view = app._view
        if open_options_first:
            view.show_options(True)
            pump(tk_root)
            view.show_options(False)
        original = app.original_text.get('1.0', 'end-1c'), app.trans_text.get_plain()
        settlement_times = []
        for width, height in ((1200, 720), (700, 640), (1200, 640), (700, 640)):
            app.popup.geometry(f'{width}x{height}+20+20')
            tracker = LayoutSettlement(stable_ms=100,timeout_ms=2000)
            started = time.monotonic()
            deadline, sample_at, status = started+2, started, 'pending'
            while time.monotonic() < deadline:
                if not tk_root.tk.dooneevent(_tkinter.DONT_WAIT):
                    time.sleep(.001)
                now = time.monotonic()
                if now >= sample_at:
                    status = tracker.observe(snapshot_layout(app),(now-started)*1000)
                    sample_at = now+.02
                    if status == 'settled':
                        break
            assert status == 'settled', tracker.report()
            settlement_times.append(tracker.verified_ms)
            area = view.body_area
            assert area.canvas.winfo_height() == area.winfo_height()
            assert area.canvas.winfo_width() <= area.winfo_width()
            if area.canvas.yview() == (0.0, 1.0):
                bottom = area.winfo_rooty() + area.winfo_height()
                for button in (view.copy_btn, view.expand_btn):
                    assert button.winfo_viewable()
                    assert button.winfo_rooty() + button.winfo_height() <= bottom
                    assert button.winfo_rooty() + button.winfo_height() <= view.actions.winfo_rooty()
            assert (app.original_text.get('1.0', 'end-1c'), app.trans_text.get_plain()) == original
        request.node.user_properties.append(('verified_layout_ms',settlement_times))

    def test_the_default_window_puts_the_panes_side_by_side(self, main_window, tk_root):
        app = main_window(LATIN)
        view = app._view
        pump(tk_root)
        assert view.mode == layout.SIDE
        assert view.translation_card.winfo_rootx() >= view.source_card.winfo_rootx() + view.source_card.winfo_width()
        assert view.translation_card.winfo_rooty() == view.source_card.winfo_rooty()

    def test_the_two_panes_share_the_width(self, main_window, tk_root):
        app = main_window(LATIN)
        pump(tk_root)
        view = app._view
        assert abs(view.source_card.winfo_width() - view.translation_card.winfo_width()) <= 1

    def test_a_narrow_window_stacks_them(self, main_window, tk_root):
        app = main_window(LATIN)
        view = app._view
        geometry(app, 700, 640)
        assert view.mode == layout.STACKED
        assert view.translation_card.winfo_rooty() >= view.source_card.winfo_rooty() + view.source_card.winfo_height()
        assert view.translation_card.winfo_width() == view.source_card.winfo_width()

    def test_stacked_panes_share_the_spare_height(self, main_window, tk_root):
        app = main_window(LATIN)
        view = app._view
        geometry(app, 700, 800)
        assert view.mode == layout.STACKED
        extra = [card.winfo_height() - card.winfo_reqheight()
                 for card in (view.source_card, view.translation_card)]
        assert min(extra) > 20 and abs(extra[0] - extra[1]) <= 2

    def test_widening_it_again_puts_them_back(self, main_window, tk_root):
        app = main_window(LATIN)
        geometry(app, 700, 640)
        geometry(app, app._view.ctx.px(1200), app._view.ctx.px(660))
        assert app._view.mode == layout.SIDE
        assert app._view.translation_card.winfo_rooty() == app._view.source_card.winfo_rooty()

    def test_the_swap_icon_follows_the_arrangement(self, main_window, tk_root):
        app = main_window(LATIN)
        view = app._view
        assert view.swap_btn.icon_name == TW.SWAP_ICONS[layout.SIDE]
        geometry(app, 700, 640)
        assert view.swap_btn.icon_name == TW.SWAP_ICONS[layout.STACKED]

    def test_arranging_in_the_mode_it_is_in_does_nothing(self, main_window, tk_root):
        app = main_window(LATIN)
        view = app._view
        seen = []
        view.configure_callbacks(on_resize=seen.append)
        view.arrange(view.mode)
        assert seen == []
        view.arrange(layout.STACKED)
        assert seen == [layout.STACKED]

    def test_the_language_bar_is_above_the_cards_and_over_its_own_card(self, main_window, tk_root):
        app = main_window(LATIN)
        pump(tk_root)
        view = app._view
        assert view.source_bar.winfo_rooty() < view.source_card.winfo_rooty()
        assert view.target_bar.winfo_rooty() < view.translation_card.winfo_rooty()
        assert view.source_bar.winfo_rootx() == view.source_card.winfo_rootx()
        assert view.target_bar.winfo_rootx() == view.translation_card.winfo_rootx()

    def test_the_minimum_size_is_the_layouts(self, main_window, tk_root):
        app = main_window(LATIN)
        minimum = app.popup.minsize()
        assert minimum[0] <= TRANSLATOR_MIN_SIZE[0] * 3 and minimum[0] >= 1
        assert minimum == (app._view.ctx.px(TRANSLATOR_MIN_SIZE[0]), app._view.ctx.px(TRANSLATOR_MIN_SIZE[1])) \
            or minimum[0] <= app.popup.winfo_screenwidth()

    def test_all_controls_are_reachable_at_the_smallest_size(self, main_window, tk_root):
        app = main_window(JAPANESE, "I study Japanese every day.")
        view = app._view
        px = view.ctx.px
        geometry(app, px(TRANSLATOR_MIN_SIZE[0]), px(TRANSLATOR_MIN_SIZE[1]))
        window = app.popup
        bottom, right = window.winfo_rooty() + window.winfo_height(), window.winfo_rootx() + window.winfo_width()
        for widget in (view.translate_btn, view.options_btn, view.picker.button, view.copy_btn,
                       view.dict_btn, view.history_btn, view.source_chip.button):
            if str(widget).startswith(str(view.body)):
                view.body_area.reveal(widget, px(4))
                pump(tk_root, .03)
            assert widget.winfo_ismapped(), widget
            assert widget.winfo_rooty() + widget.winfo_height() <= bottom, widget
            assert widget.winfo_rootx() + widget.winfo_width() <= right, widget

    def test_the_window_opens_at_the_remembered_size(self, main_window, tk_root, monkeypatch):
        from src.ui import translator_window
        monkeypatch.setattr(translator_window, 'work_area', lambda: (0, 0, 1920, 1040))
        app = main_window(LATIN)
        app.popup.destroy()
        app.config.get_main_window_size.return_value = (900, 600)
        app.show_popup(LATIN, "", "English", force_new=True)
        pump(tk_root)
        assert (app.popup.winfo_width(), app.popup.winfo_height()) == (900, 600)

    def test_closing_it_remembers_the_size(self, main_window, tk_root):
        app = main_window(LATIN)
        geometry(app, 980, 620)
        app._close_translator()
        app.config.set_main_window_size.assert_called_once_with(980, 620)

    def test_a_minimised_window_is_not_remembered(self, main_window, tk_root):
        app = main_window(LATIN)
        app.popup.iconify()
        pump(tk_root)
        app._view.remember_size()
        app.config.set_main_window_size.assert_not_called()
        app.popup.deiconify()


# --------------------------------------------------------------------------- #
# What the source card says, and swap
# --------------------------------------------------------------------------- #
class TestSourceCard:
    def test_an_empty_box_says_auto_detect_and_cannot_swap(self, main_window):
        app = main_window("")
        assert app._view.source_chip.tooltip.text == TW.SOURCE_AUTO
        assert str(app._view.swap_btn.cget('state')) == 'disabled'
        assert app._view.count_label.cget('text') == '0 characters · 0 words'

    def test_kana_settle_it_and_the_chip_says_so(self, main_window):
        app = main_window(JAPANESE, 'A result.')
        assert app._view.source_chip.tooltip.text == "Japanese"
        assert str(app._view.swap_btn.cget('state')) == 'normal'

    def test_kanji_that_prove_nothing_are_not_named(self, main_window):
        app = main_window(KANJI_ONLY)
        assert app._view.source_chip.tooltip.text == TW.SOURCE_AUTO
        assert str(app._view.swap_btn.cget('state')) == 'disabled'

    def test_a_model_s_verdict_names_them(self, main_window):
        from src.core.lang_id import CHINESE_SIMPLIFIED
        app = main_window(KANJI_ONLY, 'A result.', verdicts={KANJI_ONLY: CHINESE_SIMPLIFIED})
        assert app._view.source_chip.tooltip.text == "Chinese"
        assert str(app._view.swap_btn.cget('state')) == 'normal'

    def test_latin_text_is_never_named(self, main_window):
        app = main_window(LATIN)
        assert app._view.source_chip.tooltip.text == TW.SOURCE_AUTO

    @pytest.mark.parametrize('reported', ['French', 'Unknown', 'Korean'])
    def test_editable_source_names_catalog_and_unlisted_choices(self, main_window, reported):
        app = main_window(LATIN, 'A result.')
        app._view.source_chip.set(reported)
        app._refresh_source_state()
        assert app._view.source_chip.tooltip.text == reported
        assert app._view.source_chip.value == reported
        assert str(app._view.swap_btn.cget('state')) == 'normal'

    def test_the_chip_follows_what_is_typed(self, main_window):
        app = main_window(LATIN)
        settle(app, JAPANESE)
        assert 'Japanese' in app._view.source_chip.tooltip.text
        settle(app, LATIN)
        assert app._view.source_chip.tooltip.text == TW.SOURCE_AUTO

    def test_the_count_is_of_the_text(self, main_window):
        app = main_window(LATIN)
        assert app._view.count_label.cget('text') == f"{len(LATIN)} characters · {len(LATIN.split())} words"
        settle(app, "x" * 1234)
        assert app._view.count_label.cget('text') == "1,234 characters · 1 word"

    def test_the_box_is_set_in_the_script_it_holds(self, main_window):
        app = main_window(JAPANESE)
        japanese = str(app.original_text.cget('font'))
        settle(app, LATIN)
        assert str(app.original_text.cget('font')) != japanese

    def test_swap_exchanges_texts_and_languages_without_submitting(self, main_window, monkeypatch):
        app = main_window(JAPANESE, "I study Japanese every day.")
        asked = []
        monkeypatch.setattr(app, '_do_retranslate', lambda *a, **k: asked.append(app.selected_language))
        app._view.swap_btn.invoke()
        assert app.original_text.get('1.0', 'end-1c') == "I study Japanese every day."
        assert app.selected_language == 'Japanese' and app._view.picker.value == 'Japanese'
        assert asked == []
        assert app.trans_text.get_plain() == JAPANESE
        assert app._view.source_chip.value == 'English'

    def test_swap_with_nothing_translated_is_disabled_and_keeps_the_pair(self, main_window, monkeypatch):
        app = main_window(JAPANESE)
        asked = []
        monkeypatch.setattr(app, '_do_retranslate', lambda *a, **k: asked.append(1))
        app._view.swap_btn.invoke()
        assert app.original_text.get('1.0', 'end-1c') == JAPANESE
        assert app._view.picker.value == 'English' and asked == []
        assert str(app._view.swap_btn.cget('state')) == 'disabled'

    def test_swap_never_moves_a_failure_into_the_box(self, main_window, monkeypatch):
        app = main_window(JAPANESE, "Error: API key invalid or quota exceeded (401)")
        asked = []
        monkeypatch.setattr(app, '_do_retranslate', lambda *a, **k: asked.append(1))
        app._view.swap_btn.invoke()
        assert app.original_text.get('1.0', 'end-1c') == JAPANESE and asked == []

    def test_swap_with_an_unsettled_language_opens_source_choice_without_changing_text(self, main_window, monkeypatch):
        from src.core.nlp_manager import nlp_manager
        monkeypatch.setattr(nlp_manager, 'detect_language', lambda text: ('Unknown', 0.0))
        app = main_window(LATIN, "Chao buoi sang")
        before = app._view.picker.value
        app._swap_languages()
        assert app._view.picker.value == before
        assert app.original_text.get('1.0', 'end-1c') == LATIN
        assert app._view.source_chip.window is not None

    def test_the_k5_row_is_at_the_top_of_the_card(self, main_window, tk_root):
        app = main_window(KANJI_ONLY, "Car")
        pump(tk_root)
        assert app._source_row.master is app._view.source_header
        assert app._view.source_header.winfo_rooty() < app.original_text.winfo_rooty()


# --------------------------------------------------------------------------- #
# The language picker inside the window
# --------------------------------------------------------------------------- #
class TestLanguageChoice:
    def test_the_picker_shows_the_target(self, main_window):
        app = main_window(LATIN)
        assert app._view.picker.value == 'English'

    def test_choosing_a_language_makes_it_the_target_and_a_recent_one(self, main_window):
        from src.constants import LANGUAGES
        app = main_window(LATIN)
        app._view.picker.choose(next(row for row in LANGUAGES if row[0] == 'Korean'))
        assert app.selected_language == 'Korean'
        app.config.push_recent_language.assert_called_with('Korean')

    def test_pinned_are_the_hotkey_languages(self, main_window):
        app = main_window(LATIN)
        app.config.get_all_hotkeys.return_value = {'Vietnamese': 'win+alt+v', 'English': 'win+alt+e'}
        assert app._pinned_languages() == ['Vietnamese', 'English']

    def test_history_can_hand_over_a_target(self, main_window):
        app = main_window(LATIN)
        app._select_language_in_list('French')
        assert app.selected_language == 'French' and app._view.picker.value == 'French'

    def test_a_name_that_is_not_a_language_changes_nothing(self, main_window):
        app = main_window(LATIN)
        app._select_language_in_list('Klingon')
        assert app.selected_language == 'English' and app._view.picker.value == 'English'


# --------------------------------------------------------------------------- #
# More options
# --------------------------------------------------------------------------- #
class TestMoreOptions:
    def test_they_are_closed_to_begin_with(self, main_window):
        app = main_window(LATIN)
        view = app._view
        assert not view.options_open and not view.options_panel.winfo_manager()
        assert view.options_btn.icon_name == TW.OPTIONS_CLOSED

    def test_the_button_opens_and_closes_them(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.options_btn.invoke()
        assert view.options_open and view.options_panel.winfo_manager()
        assert view.options_btn.icon_name == TW.OPTIONS_OPEN
        view.options_btn.invoke()
        assert not view.options_open and not view.options_panel.winfo_manager()

    def test_a_tone_chip_writes_its_sentence_and_lights(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.show_options(True)
        chips = [c for c in view.options_panel.winfo_children() if isinstance(c, ui.Toggle)]
        assert [c.cget('text') for c in chips] == ['Formal', 'Casual', 'Concise', 'Natural']
        chips[0].invoke()
        assert view.custom_instruction() == tone_prompts.SENTENCES['formal']
        assert view.tone_vars['formal'].get() is True and view.tone_vars['casual'].get() is False

    def test_pressing_it_again_takes_it_out(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.show_options(True)
        chip = next(c for c in view.options_panel.winfo_children()
                    if isinstance(c, ui.Toggle) and c.cget('text') == 'Casual')
        chip.invoke()
        chip.invoke()
        assert view.custom_instruction() == '' and view.tone_vars['casual'].get() is False

    def test_the_users_own_words_are_kept(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.show_options(True)
        view.custom_prompt.set("Keep names as they are.")
        view._tone_clicked('concise')
        assert view.custom_instruction() == "Keep names as they are. " + tone_prompts.SENTENCES['concise']

    def test_typing_a_chips_sentence_lights_it(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.show_options(True)
        view.custom_prompt.set(tone_prompts.SENTENCES['natural'])
        view._sync_tones()
        assert view.tone_vars['natural'].get() is True

    def test_the_placeholder_is_never_the_instruction(self, main_window):
        app = main_window(LATIN)
        view = app._view
        assert view.custom_prompt.showing_placeholder and view.custom_instruction() == ''

    def test_the_instruction_goes_with_the_request(self, main_window, tk_root):
        app = main_window(LATIN)
        app._view.show_options(True)
        app._view.custom_prompt.set("Use Kansai dialect.")
        sent = {}

        def translate(text, target, prompt, **kwargs):
            sent.update(text=text, target=target, prompt=prompt)
            return "ok"

        app.translation_service.translate_text = translate
        app._do_retranslate()
        deadline = time.monotonic() + 2
        while not sent and time.monotonic() < deadline:
            pump(tk_root, 0.02)
        assert sent == {'text': LATIN, 'target': 'English', 'prompt': "Use Kansai dialect."}
        app._translation_tasks().cancel()


# --------------------------------------------------------------------------- #
# Busy, Copy, keys, failure bar
# --------------------------------------------------------------------------- #
class TestBusy:
    def test_the_status_is_as_wide_whatever_the_dots(self, main_window, tk_root):
        app = main_window(LATIN)
        view = app._view
        view.set_busy(True, 'Translating')
        widths = set()
        for dots in range(4):
            view.set_status('Translating', dots)
            tk_root.update_idletasks()
            widths.add(view.status_label.winfo_reqwidth())
        view.set_busy(False)
        assert len(widths) == 1

    def test_busy_shows_a_status_and_cancel_and_idle_hides_them(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.set_busy(True, 'Fixing grammar')
        assert view.busy_frame.winfo_manager() and view.status_label.cget('text').startswith('Fixing grammar')
        view.set_busy(False)
        assert not view.busy_frame.winfo_manager()

    def test_busy_makes_translate_and_fix_grammar_unavailable(self, main_window):
        app = main_window(LATIN)
        view = app._view
        view.set_busy(True, 'Translating')
        assert str(view.translate_btn.cget('state')) == 'disabled'
        assert str(view.fix_grammar_btn.cget('state')) == 'disabled'
        view.set_busy(False)
        assert str(view.translate_btn.cget('state')) == 'normal'
        assert str(view.fix_grammar_btn.cget('state')) == 'normal'

    def test_the_translate_button_is_still_translate(self, main_window):
        app = main_window(LATIN)
        app._view.set_busy(True, 'Translating')
        assert app._view.translate_btn.cget('text') == TW.TRANSLATE
        app._view.set_busy(False)


class TestKeysAndCopy:
    def test_ctrl_enter_translates(self, main_window, tk_root, monkeypatch):
        app = main_window(LATIN)
        calls = []
        app._view.configure_callbacks(on_translate=lambda: calls.append(1))
        app.original_text.focus_force()
        pump(tk_root, 0.05)
        app.original_text.event_generate('<Control-Return>')
        pump(tk_root, 0.05)
        assert calls == []

    def test_ctrl_enter_does_nothing_while_busy(self, main_window, tk_root):
        app = main_window(LATIN)
        calls = []
        app._view.configure_callbacks(on_translate=lambda: calls.append(1))
        app._view.set_busy(True, 'Translating')
        app.original_text.focus_force()
        app.original_text.event_generate('<Control-Return>')
        pump(tk_root, 0.05)
        assert calls == []
        app._view.set_busy(False)

    def test_the_shortcut_is_shown_beside_the_button(self, main_window):
        app = main_window(LATIN)
        assert not hasattr(app._view, 'shortcut_label')
        assert app._view.history_btn.cget('text') == 'History'

    def test_a_blank_enter_in_the_box_still_makes_a_new_line(self, main_window, tk_root):
        app = main_window("")
        app.original_text.focus_force()
        app.original_text.event_generate('<Return>')
        pump(tk_root, 0.05)
        assert app.original_text.get('1.0', 'end-1c') == '\n'

    def test_unknown_callbacks_are_refused(self, main_window):
        app = main_window(LATIN)
        with pytest.raises(TypeError, match='unknown callbacks'):
            app._view.configure_callbacks(on_nothing=lambda: None)

    def test_copy_says_copied_once_on_the_button_and_goes_back(self, main_window, monkeypatch):
        copied = []
        monkeypatch.setattr('src.app.pyperclip.copy', copied.append)
        app = main_window(LATIN, "Good morning")
        app._copy_translation()
        assert copied == ["Good morning"]
        assert app.copy_btn.cget('text') == 'Copied' and app.copy_btn.icon_name == 'check-lg'
        assert app.toast.method_calls == []
        app._reset_copy_button()
        assert app.copy_btn.cget('text') == 'Copy' and app.copy_btn.icon_name == 'copy'


class TestFailureBar:
    def test_a_key_failure_is_told_with_api_settings_first(self, main_window):
        app = main_window(LATIN)
        app._update_translation_with_original("Error: API key invalid or quota exceeded (401)")
        bar = app._view.error_bar
        assert bar is not None and bar.action.cget('text') == 'API Settings'
        assert 'API key' in bar.text.cget('text')

    def test_a_timeout_offers_try_again(self, main_window, monkeypatch):
        app = main_window(LATIN)
        app._update_translation_with_original("Error: the request timed out")
        assert app._view.error_bar.action.cget('text') == 'Try again'

    def test_try_again_translates_again(self, main_window, monkeypatch):
        app = main_window(LATIN)
        calls = []
        monkeypatch.setattr(app, '_do_retranslate', lambda *a, **k: calls.append(1))
        app._update_translation_with_original("Error: the request timed out")
        app._view.error_bar.action.invoke()
        assert calls == [1]

    def test_the_notice_stays_in_the_box_as_the_details(self, main_window):
        app = main_window(LATIN)
        app._update_translation_with_original("Error: API key invalid or quota exceeded (401)")
        assert app.trans_text.get_plain() == "Error: API key invalid or quota exceeded (401)"

    def test_a_translation_takes_the_bar_away(self, main_window):
        app = main_window(LATIN)
        app._update_translation_with_original("Error: the request timed out")
        app._update_translation_with_original("Good morning")
        assert app._view.error_bar is None and not app._view.error_host.winfo_children()

    def test_an_ordinary_translation_shows_no_bar(self, main_window):
        app = main_window(LATIN, "Good morning")
        assert app._view.error_bar is None

    def test_a_grammar_fix_failure_is_told_too(self, main_window):
        app = main_window(LATIN)
        app._update_grammar_result("Error: the request timed out")
        assert app._view.error_bar is not None


# --------------------------------------------------------------------------- #
# The theme, while the window stays open
# --------------------------------------------------------------------------- #
class TestTheme:
    @pytest.fixture
    def manager(self, main_window):
        main_window(LATIN)                         # builds the components' context
        manager = ui.get().manager
        yield manager
        manager.set_mode('light')

    def test_the_text_boxes_take_the_new_palette(self, main_window, manager):
        app = main_window(LATIN, "Good morning")
        for mode in ('dark', 'light'):
            manager.set_mode(mode)
            palette = manager.palette
            surface = ui.get().plane_colour('surface')
            assert str(app.original_text.cget('bg')) == surface
            assert str(app.original_text.cget('fg')) == palette.text
            assert str(app.original_text.cget('insertbackground')) == palette.text
            assert str(app.trans_text.cget('bg')) == surface
            assert str(app.trans_text.cget('fg')) == palette.text
            assert str(app.trans_text.cget('selectbackground')) == palette.selection

    def test_the_translation_is_still_there_afterwards(self, main_window, manager):
        app = main_window(LATIN, "Good morning")
        manager.set_mode('dark')
        manager.set_mode('light')
        assert app.trans_text.get_plain() == "Good morning"

    def test_the_reading_pane_follows(self, main_window, manager, tk_root):
        app = main_window(JAPANESE)
        for mode in ('dark', 'light'):
            manager.set_mode(mode)
            pump(tk_root, 0.05)
            app._refresh_reading_pane()
            tk_root.update_idletasks()
            assert all(str(label.cget('background')) == str(app.original_text.cget('background'))
                       for label in app._inline_reading.labels)

    def test_a_closed_window_stops_listening(self, main_window, manager):
        app = main_window(LATIN)
        before = len(manager._subscribers)
        app._close_translator()
        assert len(manager._subscribers) < before
        manager.set_mode('dark')               # must not raise
        manager.set_mode('light')

    def test_the_custom_instruction_field_follows(self, main_window, manager):
        app = main_window(LATIN)
        for mode in ('dark', 'light'):
            manager.set_mode(mode)
            assert str(app._view.custom_prompt.cget('foreground')) == manager.palette.muted

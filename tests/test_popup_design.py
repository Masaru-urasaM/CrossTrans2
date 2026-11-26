"""
Tests for the quick-translate popup's design (TODO.md U6, Decisions 46-48).

The popup's measured layout (Decisions 34, 35) is pinned by test_popup_geometry.py; these pin what it
looks like: a hairline ring on the surface colour, colours and type from the tokens in both themes,
the header's language pair and credit, one accent on the action bar, a failure in plain words with
the next step, a loading card that does not shift, and Windows 11's rounded corners (best effort).
"""
import os
import re
import sys
import tkinter as tk
from dataclasses import replace
from tkinter import font as tkfont
from tkinter import ttk

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'tools'))
import popup_cases as cases  # noqa: E402
import popup_gallery as gallery  # noqa: E402

from src.ui import components as ui  # noqa: E402
from src.ui import popup_errors, popup_layout, quick_translate  # noqa: E402
from src.ui.quick_translate import QuickTranslateManager  # noqa: E402
from src.ui.theme import tokens, windows  # noqa: E402
from src.ui.theme.contrast import contrast_ratio  # noqa: E402
from src.core.translation_alignment import AlignedText, make_metadata  # noqa: E402

from tests.test_components import env  # noqa: E402,F401  (the engine and context, both themes)
from tests.test_popup_geometry import put_up, settle  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LinkedConfig(cases.FakeConfig):
    """FakeConfig with the Linked reading switch the popup's icon follows."""

    def __init__(self, linked=False):
        self.linked = linked

    def get_linked_reading_enabled(self):
        return self.linked

    def set_linked_reading_enabled(self, value):
        self.linked = bool(value)


@pytest.fixture(params=['light', 'dark'])
def mode(request, env):
    env.manager.set_mode(request.param)
    return request.param


@pytest.fixture
def manager(env, tk_root):
    mgr = QuickTranslateManager(tk_root, cases.FakeConfig())
    mgr._last_mouse_x, mgr._last_mouse_y = 500, 300
    yield mgr
    mgr.close()


def widgets(popup):
    return list(gallery.descendants(popup))


def by_class(popup, *names):
    return [w for w in widgets(popup) if w.winfo_class() in names]


def style_of(widget):
    return str(widget.cget('style'))


def lower(value):
    return str(value).lower()


class TestTheCard:
    def test_a_hairline_ring_in_the_border_colour(self, manager, tk_root, env, mode):
        put_up(manager, tk_root, cases.case('B'))
        popup, p = manager.popup, env.palette
        assert int(popup.cget('highlightthickness')) == env.px(quick_translate.POPUP_RING_PX)
        assert lower(popup.cget('highlightbackground')) == lower(p.border)
        assert lower(popup.cget('highlightcolor')) == lower(p.border)
        assert lower(popup.cget('bg')) == lower(p.surface)

    def test_the_content_sits_inside_the_ring(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('C'))
        ring = env.px(quick_translate.POPUP_RING_PX)
        frame, popup = manager._main_frame, manager.popup
        assert frame.winfo_width() + 2 * ring == popup.winfo_width()
        assert frame.winfo_height() + 2 * ring == popup.winfo_height()
        assert frame.winfo_rootx() - popup.winfo_rootx() == ring

    def test_the_main_frame_is_on_the_surface_plane(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        assert manager._main_frame.plane == 'surface'
        assert style_of(manager._main_frame) == 'CtPlaneSurface.TFrame'

    def test_the_loading_card_has_the_same_ring(self, manager, tk_root, env):
        manager.show_loading('French')
        tk_root.update()
        assert int(manager.popup.cget('highlightthickness')) == env.px(quick_translate.POPUP_RING_PX)
        assert lower(manager.popup.cget('highlightbackground')) == lower(env.palette.border)

    def test_the_grip_is_a_drawn_image_in_the_corner(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('B'))
        grip = manager._grip
        assert str(grip.cget('image')) and not str(grip.cget('text'))
        assert lower(grip.cget('bg')) == lower(env.palette.surface)
        assert int(tk_root.tk.call('image', 'width', str(grip.cget('image')))) == env.px(12)


class TestColoursFollowTheTheme:
    def test_the_translation_box(self, manager, tk_root, env, mode):
        put_up(manager, tk_root, cases.case('B'))
        box, p = manager.popup_text, env.palette
        assert lower(box.cget('bg')) == lower(p.surface)
        assert lower(box.cget('fg')) == lower(p.text)
        assert lower(box.cget('selectbackground')) == lower(p.selection)
        assert lower(box.cget('selectforeground')) == lower(p.text)
        assert lower(box.cget('insertbackground')) == lower(p.text)

    def test_the_original_is_muted_and_its_readings_have_their_own_colour(self, manager,
                                                                            tk_root, env, mode):
        put_up(manager, tk_root, cases.case('E'))
        box, p = manager.popup_furigana, env.palette
        assert lower(box.cget('bg')) == lower(p.surface)
        assert lower(box.cget('fg')) == lower(p.muted)
        assert lower(box._ruby_fg) == lower(p.ruby)

    def test_the_holders_and_the_edge_match_the_surface(self, manager, tk_root, env, mode):
        put_up(manager, tk_root, cases.case('C'))
        for holder in (manager._text_holder, manager._original_holder):
            assert lower(holder.cget('bg')) == lower(env.palette.surface)

    @pytest.mark.parametrize('key', ['B', 'E', 'K'])
    def test_every_text_is_readable_on_the_card(self, manager, tk_root, env, mode, key):
        put_up(manager, tk_root, cases.case(key))
        surface = env.palette.surface
        for box in (manager.popup_text, manager.popup_furigana):
            if box is not None:
                assert contrast_ratio(str(box.cget('fg')), surface) >= 4.5, (key, mode)
        ttk_style = ttk.Style()
        for label in by_class(manager.popup, 'TLabel'):
            colour = str(ttk_style.lookup(style_of(label), 'foreground'))
            if colour and str(label.cget('text')):
                background = str(ttk_style.lookup(style_of(label), 'background')) or surface
                assert contrast_ratio(colour, background) >= 4.5, (label.cget('text'), mode)

    def test_no_widget_wears_a_colour_from_the_old_palette(self, manager, tk_root, env, mode):
        import json
        from pathlib import Path
        old = {value.lower() for value in json.loads(
            (Path(__file__).parent / 'fixtures/retired_ui_palette.json').read_text(encoding='utf-8')).values()}
        new = {value.lower() for value in env.palette.as_dict().values()
               if isinstance(value, str)}
        for key in ('B', 'E', 'K', 'C-replace', 'C-prompt'):
            put_up(manager, tk_root, cases.case(key))
            used = set()
            for widget in [manager.popup] + widgets(manager.popup):
                for option in ('bg', 'fg', 'background', 'foreground', 'highlightbackground'):
                    try:
                        used.add(lower(widget.cget(option)))
                    except tk.TclError:
                        pass
            assert not ((used & old) - new), (key, mode, (used & old) - new)

    def test_the_theme_is_read_when_the_popup_is_built(self, manager, tk_root, env):
        env.manager.set_mode('light')
        put_up(manager, tk_root, cases.case('B'))
        light = lower(manager.popup_text.cget('bg'))
        env.manager.set_mode('dark')
        put_up(manager, tk_root, cases.case('B'))
        assert lower(manager.popup_text.cget('bg')) != light


class TestTheHeader:
    @pytest.mark.parametrize('key, expected', [
        ('A', "To French"),
        ('E', "Japanese " + quick_translate.ARROW + " English"),
        ('L', "Chinese " + quick_translate.ARROW + " English"),
        ('B-grammar', "Grammar fix"),
    ])
    def test_the_chip_names_the_pair_when_the_source_is_settled(self, manager, tk_root, key,
                                                                 expected):
        put_up(manager, tk_root, cases.case(key))
        assert manager.popup_chip.cget('text') == expected

    def test_a_failure_has_no_chip_and_no_credit(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('K'))
        assert manager.popup_chip is None and manager.popup_attribution is None

    def test_the_chip_is_the_soft_pill_and_the_credit_a_muted_caption(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        assert style_of(manager.popup_chip) == 'CtChipSurface.TLabel'
        assert style_of(manager.popup_attribution) == 'CtCaptionSurface.TLabel'

    def test_header_keeps_pair_credit_and_close_in_one_row(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        chip, credit, close = manager.popup_chip, manager.popup_attribution, manager.popup_close_btn
        assert chip.winfo_rootx() < close.winfo_rootx()
        assert chip.winfo_rootx() < credit.winfo_rootx() < close.winfo_rootx()
        assert chip.master is credit.master is close.master
        assert manager._replace_gear_btn is None

    @pytest.mark.parametrize('key', ['A', 'B', 'E', 'L'])
    def test_credit_fits_the_shared_header_and_full_text_is_available(self, manager, tk_root, key):
        put_up(manager, tk_root, cases.case(key))
        full = "Translated with " + cases.case(key).model
        visible = str(manager.popup_attribution.cget('text'))
        assert visible == full or (visible.endswith(popup_layout.ELLIPSIS)
                                   and full.startswith(visible[:-1]))
        assert manager.popup_attribution.tooltip.text == full

    def test_the_close_button_is_a_named_icon_with_a_tooltip(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        close = manager.popup_close_btn
        assert close.action_name == "Close" and close.icon_name == 'x-lg'
        assert close.tooltip.text == "Close" and close.cget('text') == ''

    def test_the_credit_is_not_italic(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        spec = ttk.Style().lookup(style_of(manager.popup_attribution), 'font')
        assert tkfont.Font(font=spec).actual('slant') == 'roman'


class TestTheActionBar:
    def test_copy_is_the_one_accent(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        accents = [w for w in by_class(manager.popup, 'TButton') if style_of(w).startswith('CtAccent')]
        assert accents == [manager.popup_copy_btn]
        assert style_of(manager.popup_replace_btn) == 'CtSoftSurface.TButton'

    def test_the_other_actions_are_quiet_squares_of_one_size(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('B'))
        side = env.style.scaling.logical(tokens.CONTROL_HEIGHT)
        for button in (manager.popup_retranslate_btn,
                       manager.popup_dict_btn, manager.popup_custom_prompt_btn,
                       manager.popup_linked_btn, manager.popup_open_btn, manager.popup_close_btn):
            assert style_of(button).startswith('CtIcon')
            assert (button.winfo_reqwidth(), button.winfo_reqheight()) == (side, side)

    def test_every_icon_names_its_action_in_a_tooltip(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        for button, name in ((manager.popup_retranslate_btn, "Re-translate"),
                             (manager.popup_dict_btn, "Dictionary"),
                             (manager.popup_custom_prompt_btn, "Custom Prompt"),
                             (manager.popup_open_btn, "Open Translator")):
            assert button.action_name == name and button.tooltip.text == name
            assert button.tooltip in manager._tooltips

    def test_the_linked_icon_is_tinted_while_the_setting_is_on(self, tk_root, env):
        for linked, style in ((True, 'CtIcononSurface.TButton'), (False, 'CtIconSurface.TButton')):
            mgr = QuickTranslateManager(tk_root, LinkedConfig(linked))
            mgr._last_mouse_x, mgr._last_mouse_y = 500, 300
            try:
                put_up(mgr, tk_root, cases.case('B'))
                assert style_of(mgr.popup_linked_btn) == style
                assert mgr.popup_linked_btn.is_on is linked
            finally:
                mgr.close()

    def test_toggling_it_changes_the_tint_in_place(self, tk_root, env):
        config = LinkedConfig(True)
        mgr = QuickTranslateManager(tk_root, config)
        mgr._last_mouse_x, mgr._last_mouse_y = 500, 300
        try:
            case = cases.case('B')
            data = make_metadata(case.original, case.translated, case.target, 'text', [
                {'source': [0, len(case.original)], 'translated': [0, len(case.translated)]}])
            put_up(mgr, tk_root, replace(case, translated=AlignedText(case.translated, data)))
            assert mgr.popup_linked_btn.is_on
            mgr.popup_linked_btn.invoke()
            tk_root.update()
            assert config.linked is True and not mgr.popup_linked_btn.is_on
            assert style_of(mgr.popup_linked_btn) == 'CtIconSurface.TButton'
        finally:
            mgr.close()

    def test_copy_ticks_while_it_says_copied(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        manager.set_copy_button_text("Copied!")
        assert manager.popup_copy_btn.cget('text') == "Copied!"
        assert manager.popup_copy_btn.icon_name == 'check-lg'
        manager.set_copy_button_text("Copy")
        assert manager.popup_copy_btn.icon_name == 'copy'

    def test_a_grammar_fix_keeps_copy_replace_and_open(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B-grammar'))
        assert manager.popup_chip.cget('text') == "Grammar fix"
        assert manager.popup_copy_btn is not None and manager.popup_open_btn is not None

    def test_the_dictionary_button_stays_clickable_without_a_pack(self, manager, tk_root,
                                                                   monkeypatch):
        from src.ui import quick_translate as module
        monkeypatch.setattr(module.nlp_manager, 'is_any_installed', lambda: False)
        put_up(manager, tk_root, cases.case('B'))
        assert str(manager.popup_dict_btn.cget('cursor')) == 'arrow'
        seen = []
        manager._on_open_dictionary = lambda *a: seen.append(a)
        manager.popup_dict_btn.invoke()
        assert len(seen) == 1


class TestTheReplacePreviewAndCustomPrompt:
    def test_the_preview_strikes_the_original_and_confirms_with_the_accent(self, manager,
                                                                            tk_root, env, mode):
        put_up(manager, tk_root, cases.case('C-replace'))
        box, p = manager.popup_furigana, env.palette
        assert 'overstrike' in str(box.tag_cget('replace_preview', 'font')) or \
            tkfont.Font(font=str(box.tag_cget('replace_preview', 'font'))).actual('overstrike')
        assert lower(box.tag_cget('replace_preview', 'foreground')) == lower(p.muted)
        assert lower(manager.popup_text._base_fg) == lower(p.text)
        texts = {str(b.cget('text')): style_of(b) for b in by_class(manager.popup, 'TButton')}
        assert texts['Agree'] == 'CtAccentSurface.TButton'
        assert texts['Cancel'] == 'CtSoftSurface.TButton'

    def test_the_preview_has_no_checkmark_glyphs_in_its_buttons(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('C-replace'))
        for button in by_class(manager.popup, 'TButton'):
            assert not re.search('[✓✗✕]', str(button.cget('text')))

    def test_the_prompt_box_is_ringed_in_the_accent(self, manager, tk_root, env, mode):
        put_up(manager, tk_root, cases.case('C-prompt'))
        box = manager.popup_text
        assert lower(box.cget('highlightbackground')) == lower(env.palette.accent)
        texts = {str(b.cget('text')): style_of(b) for b in by_class(manager.popup, 'TButton')}
        assert texts['Send'] == 'CtAccentSurface.TButton'
        assert texts['Cancel'] == 'CtSoftSurface.TButton'

    def test_preview_keeps_result_separate_from_the_struck_source(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('C-replace'))
        assert manager.popup_text.get_plain() == manager._current_translation
        assert manager.popup_furigana.get_plain() == manager._current_original


class TestAFailureInPlainWords:
    ERROR_503 = "Error: API request failed (503)"
    ERROR_KEY = "Error: Invalid API key. Please check your API key in Settings."

    def show(self, manager, tk_root, text, original=''):
        manager.show(text, "English", None, original)
        settle(manager, tk_root)

    def labels(self, manager):
        return [str(b.cget('text')) for b in by_class(manager.popup, 'TButton')
                if getattr(b, 'action_name', None) is None]

    def test_the_notice_is_an_info_bar_with_the_plain_sentence(self, manager, tk_root):
        self.show(manager, tk_root, self.ERROR_KEY)
        notice, expected = manager._error_notice, popup_errors.explain(self.ERROR_KEY)
        assert isinstance(notice, ui.InfoBar) and notice.kind == expected.kind
        assert notice.text.cget('text') == expected.message

    def test_the_original_notice_stays_as_the_details(self, manager, tk_root):
        self.show(manager, tk_root, self.ERROR_KEY)
        assert manager.popup_text.get_plain() == self.ERROR_KEY
        assert "Details" in [str(w.cget('text')) for w in by_class(manager.popup, 'TLabel')]

    def test_the_details_are_quieter_than_a_translation(self, manager, tk_root, env, mode):
        self.show(manager, tk_root, self.ERROR_KEY)
        assert lower(manager.popup_text.cget('fg')) == lower(env.palette.muted)

    def test_a_rejected_key_leads_with_the_settings(self, manager, tk_root):
        self.show(manager, tk_root, self.ERROR_KEY, original="Bonjour")
        assert self.labels(manager) == ["API Settings", "Open Translator"]
        settings = next(b for b in by_class(manager.popup, 'TButton')
                        if b.cget('text') == "API Settings")
        assert style_of(settings) == 'CtAccentSurface.TButton'

    def test_a_server_failure_with_the_original_leads_with_try_again(self, manager, tk_root):
        retried = []
        manager._on_re_translate = lambda: retried.append(True)
        self.show(manager, tk_root, self.ERROR_503, original="Bonjour")
        assert self.labels(manager) == ["Try again", "API Settings", "Open Translator"]
        again = next(b for b in by_class(manager.popup, 'TButton') if b.cget('text') == "Try again")
        assert style_of(again) == 'CtAccentSurface.TButton'
        again.invoke()
        assert retried == [True]

    def test_with_nothing_to_translate_again_there_is_no_try_again(self, manager, tk_root):
        self.show(manager, tk_root, self.ERROR_503, original='')
        assert self.labels(manager) == ["API Settings", "Open Translator"]

    def test_no_selection_offers_only_the_translator(self, manager, tk_root):
        self.show(manager, tk_root, "No text selected. Please select text and try again.")
        assert self.labels(manager) == ["Open Translator"]
        assert manager._error_notice.kind == popup_errors.WARNING
        assert style_of(manager.popup_open_btn) == 'CtAccentSurface.TButton'

    def test_the_translator_is_at_the_far_right(self, manager, tk_root):
        self.show(manager, tk_root, self.ERROR_KEY, original="Bonjour")
        popup = manager.popup
        right = manager.popup_open_btn.winfo_rootx() + manager.popup_open_btn.winfo_width()
        assert popup.winfo_rootx() + popup.winfo_width() - right <= 2 * (
            ui.get().px(quick_translate.POPUP_FRAME_PADDING + quick_translate.POPUP_RING_PX))

    def test_the_notice_fits_the_popup(self, manager, tk_root, env):
        self.show(manager, tk_root, cases.ERROR_TEXT)
        notice, frame = manager._error_notice, manager._main_frame
        assert notice.winfo_width() <= frame.winfo_width()
        assert int(float(str(notice.text.cget('wraplength')))) <= frame.winfo_width()

    def test_there_is_no_red_error_panel_any_more(self, manager, tk_root, env):
        self.show(manager, tk_root, self.ERROR_KEY)
        assert lower(manager.popup.cget('bg')) == lower(env.palette.surface)


class TestTheLoadingCard:
    def test_it_has_no_emoji_and_names_what_it_does(self, manager, tk_root):
        manager.show_loading('French')
        tk_root.update()
        texts = [str(w.cget('text')) for w in by_class(manager.popup, 'TLabel')]
        assert "Translating to French" in texts
        assert not any(re.search('[⏳⌛⚠]', text) for text in texts)

    def test_a_custom_phrase_is_used(self, manager, tk_root):
        manager.show_loading('French', loading_text="Fixing grammar")
        tk_root.update()
        assert manager._loading_label.cget('text') == "Fixing grammar"

    def test_one_more_dot_each_step_then_none_and_the_slot_never_changes(self, manager, tk_root, env, monkeypatch):
        from dataclasses import replace
        monkeypatch.setattr(env.manager, 'accessibility', replace(env.manager.accessibility, animations=True, high_contrast=False))
        manager.show_loading('French')
        tk_root.update()
        widths, seen = set(), []
        for step in range(8):
            manager._loading_animation_step = step
            manager._animate_loading()
            tk_root.update_idletasks()
            seen.append(str(manager._loading_dots.cget('text')))
            widths.add(manager._loading_dots.winfo_reqwidth())
        assert seen == ['', '.', '..', '...', '', '.', '..', '...']
        assert len(widths) == 1

    def test_the_text_does_not_pulse_in_colour(self, manager, tk_root):
        manager.show_loading('French')
        tk_root.update()
        styles = set()
        for step in range(4):
            manager._loading_animation_step = step
            manager._animate_loading()
            styles.add(style_of(manager._loading_label))
        assert len(styles) == 1

    def test_its_close_button_is_the_named_icon(self, manager, tk_root):
        manager.show_loading('French')
        tk_root.update()
        assert manager.popup_close_btn.action_name == "Close"
        assert gallery.find_close_button(manager.popup) is manager.popup_close_btn

    def test_it_is_gone_when_closed(self, manager, tk_root):
        manager.show_loading('French')
        manager.close()
        assert manager.popup is None and manager._loading_dots is None


class TestTypeAndScript:
    def family_size(self, font_spec):
        actual = tkfont.Font(font=font_spec).actual()
        return actual['family'], actual['size']

    def test_the_translation_is_in_the_body_style(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('B'))
        assert self.family_size(str(manager.popup_text.cget('font'))) == \
            self.family_size(env.font('body'))

    def test_japanese_output_uses_the_japanese_ui_font(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('H'))
        assert self.family_size(str(manager.popup_text.cget('font'))) == \
            self.family_size(env.font('body', 'japanese'))

    def test_the_original_block_uses_its_own_script(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('E'))
        assert self.family_size(str(manager.popup_furigana.cget('font'))) == \
            self.family_size(env.font('body', 'japanese'))

    def test_sizes_are_pixels_so_the_screen_scale_applies(self, manager, tk_root, env):
        put_up(manager, tk_root, cases.case('B'))
        assert env.font('body')[1] < 0                 # a negative Tk size is pixels
        assert manager.popup_text.base_font[1] == env.font('body')[1]

    def test_the_hero_is_not_smaller_than_the_original_above_it(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('E'))
        hero = tkfont.Font(font=str(manager.popup_text.cget('font'))).metrics('linespace')
        original = tkfont.Font(font=str(manager.popup_furigana.cget('font'))).metrics('linespace')
        assert hero <= original + 4          # one size: the original is set apart by colour


class TestWindowsRounding:
    def test_the_popup_asks_for_rounded_corners_once_it_is_mapped(self, manager, tk_root,
                                                                    monkeypatch):
        calls = []
        monkeypatch.setattr(windows, 'style_floating',
                            lambda window, palette, setter=None: calls.append((window, palette)))
        put_up(manager, tk_root, cases.case('B'))
        assert [window for window, _ in calls] == [manager.popup]

    def test_a_child_being_mapped_does_not_repeat_it(self, manager, tk_root, monkeypatch):
        calls = []
        monkeypatch.setattr(windows, 'style_floating', lambda *a, **k: calls.append(a))
        put_up(manager, tk_root, cases.case('C'))
        assert len(calls) == 1

    def test_the_loading_card_asks_too(self, manager, tk_root, monkeypatch):
        calls = []
        monkeypatch.setattr(windows, 'style_floating', lambda *a, **k: calls.append(a))
        manager.show_loading('French')
        tk_root.update()
        assert len(calls) == 1

    def test_rounding_accepted_and_the_border_colour_set(self, tk_root):
        top = tk.Toplevel(tk_root)
        top.update()
        seen = []
        palette = tokens.DARK
        assert windows.style_floating(top, palette, setter=lambda h, a, v: seen.append((a, v)) or 0)
        top.destroy()
        assert (windows.DWMWA_WINDOW_CORNER_PREFERENCE, windows.DWMWCP_ROUND) in seen
        assert (windows.DWMWA_BORDER_COLOR, windows.colorref(palette.border)) in seen

    def test_where_dwm_refuses_the_popup_stays_square_without_a_fuss(self, tk_root):
        top = tk.Toplevel(tk_root)
        top.update()
        seen = []
        refused = windows.style_floating(top, tokens.LIGHT,
                                         setter=lambda h, a, v: seen.append(a) or 0x80070057)
        top.destroy()
        assert refused is False and windows.DWMWA_BORDER_COLOR in seen

    def test_a_window_with_no_frame_is_left_alone(self):
        class Nothing:
            def wm_frame(self):
                raise tk.TclError('no frame')
        assert windows.style_floating(Nothing(), tokens.LIGHT, setter=lambda *a: 0) is False


class TestNoOldMachinery:
    def test_the_old_tooltip_and_icon_font_code_is_gone(self):
        for name in ('Tooltip', 'icon_font_family', 'ICON_FONTS', 'ICON_GLYPHS', 'CLOSE_GLYPH',
                     'GEAR_GLYPH', 'GRIP_GLYPH', 'ATTRIBUTION_FONT', 'ATTRIBUTION_FG'):
            assert not hasattr(quick_translate, name), name

    def test_the_popup_module_holds_no_vietnamese(self):
        letters = re.compile('[' + chr(0x1EA0) + '-' + chr(0x1EF9) + chr(0x110) + chr(0x111) + chr(0x1A0) + chr(0x1A1) + chr(0x1AF) + chr(0x1B0) + ']')
        for path in ('src/ui/quick_translate.py', 'src/ui/popup_errors.py', 'src/ui/toast.py',
                     'src/ui/update_notice.py'):
            with open(os.path.join(REPO, path), encoding='utf-8') as fh:
                assert not letters.search(fh.read()), path


def test_popup_does_not_offer_a_settings_button(manager, tk_root):
    put_up(manager, tk_root, cases.case('B'))
    assert manager._replace_gear_btn is None
    assert manager._header_settings is None

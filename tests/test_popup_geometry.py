"""
Geometry of the quick-translate popup, on the real widgets (TODO.md; Decision 34).

Every case and state of tests/popup_cases.py is put up through the real
QuickTranslateManager on the private desktop (Decision 32) and measured with the
gallery tool's own functions (tools/popup_gallery.py), so a number in TODO.md, an
image in the gallery and an assertion here are the same measurement.
"""
import ctypes
import dataclasses
import os
import statistics
import sys
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'tools'))
import popup_gallery as gallery  # noqa: E402
import popup_cases as cases  # noqa: E402
from src.constants import POPUP_COLLAPSED_ORIGINAL_ROWS  # noqa: E402
from src.core import furigana  # noqa: E402
from src.ui import popup_layout, quick_translate  # noqa: E402
from src.ui.quick_translate import QuickTranslateManager  # noqa: E402
from tests.final_ui_helpers import design  # noqa: E402

EVERY_CASE = pytest.mark.parametrize('case', cases.ALL_CASES, ids=lambda c: c.key)


@pytest.fixture
def manager(tk_root):
    mgr = QuickTranslateManager(tk_root, cases.FakeConfig())
    mgr._last_mouse_x, mgr._last_mouse_y = 500, 300
    yield mgr
    mgr.close()


def settle(manager, tk_root, timeout=2.0):
    """Run the event loop until the popup has been measured and shown."""
    tk_root.update_idletasks()
    tk_root.update()
    deadline = time.perf_counter() + timeout
    while (manager.popup is not None and manager._layout is not None
           and not manager._layout.get('revealed') and time.perf_counter() < deadline):
        time.sleep(0.005)
        tk_root.update()


def put_up(manager, tk_root, case):
    furigana.clear_cache()
    cases.show_case(manager, case)
    settle(manager, tk_root)


def alpha(manager):
    return float(manager.popup.attributes('-alpha'))


# --------------------------------------------------------------------------- #
# P1 - the close button and nothing cut off (TODO.md S1, S2)
# --------------------------------------------------------------------------- #
class TestCloseButton:
    """The bar needed 735 px, the popup was 670, and the close button, packed last,
    was the one squeezed out on every short result."""

    @EVERY_CASE
    def test_the_close_button_is_drawn_inside_the_popup(self, manager, tk_root, case):
        put_up(manager, tk_root, case)
        close = gallery.close_button_metrics(manager.popup)
        assert close == {'found': True, 'shown': True, 'full_width': True, 'inside': True}, (
            f"{case.key} ({case.name}): popup {manager.popup.winfo_width()} px wide, "
            f"close button {close}")

    def test_close_pair_and_credit_share_one_header_without_settings(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('A'))
        assert manager.popup_chip.master is manager.popup_close_btn.master
        assert manager.popup_attribution.master is manager.popup_close_btn.master
        assert manager._replace_gear_btn is None
        assert gallery.clipped_controls(manager.popup) == []

    def test_a_long_credit_is_cut_and_the_button_stays(self, manager, tk_root):
        long_model = ("an-extremely-long-model-name-" * 6) + " (Provider)"
        put_up(manager, tk_root, dataclasses.replace(cases.case('A'), model=long_model))
        assert manager.popup_attribution.cget('text').endswith(popup_layout.ELLIPSIS)
        assert gallery.close_button_metrics(manager.popup)['inside']


class TestNothingIsCutOff:
    """No button or label squeezed, cut at the edge, or given fewer characters than its
    text ("Get Free API Key" in a 14-character button)."""

    @EVERY_CASE
    def test_no_button_or_label_is_cut_off(self, manager, tk_root, case):
        put_up(manager, tk_root, case)
        assert gallery.clipped_controls(manager.popup) == [], (
            f"{case.key} ({case.name}): popup {manager.popup.winfo_width()} px wide")


class TestRowWidth:
    """The width a row of buttons needs is read off the buttons, before anything is
    shown - so the popup can be made wide enough without mapping it first (which would
    flash it at the wrong place) and without a constant that drifts."""

    def test_it_is_known_before_the_window_is_shown(self, tk_root):
        top = tk.Toplevel(tk_root)
        top.withdraw()
        try:
            row = tk.Frame(top)
            row.pack(fill='x')
            for text, padx in (("Copy", 0), ("Replace", (4, 0)), ("Open Translator", 4)):
                tk.Button(row, text=text, padx=12).pack(side='left', padx=padx)
            tk.Button(row, text="x", width=3).pack(side='right')
            before = quick_translate.packed_row_width(row)
            tk_root.update_idletasks()
            assert before == row.winfo_reqwidth()
        finally:
            top.destroy()

    def test_padding_on_one_side_counts_once(self, tk_root):
        top = tk.Toplevel(tk_root)
        top.withdraw()
        try:
            one, two = tk.Frame(top), tk.Frame(top)
            tk.Button(one, text="Replace").pack(side='left', padx=(4, 0))
            tk.Button(two, text="Replace").pack(side='left', padx=4)
            assert quick_translate.packed_row_width(two) - quick_translate.packed_row_width(one) == 4
        finally:
            top.destroy()


# --------------------------------------------------------------------------- #
# P2 - measured, readable, capped, beside the cursor (DoD2, DoD3, DoD5, DoD6, DoD7)
# --------------------------------------------------------------------------- #
class TestBoxesFitTheirContent:
    """Every box exactly as tall as its content up to the cap; what does not fit is
    reachable through a scrollbar that is there to see (TODO.md S4, S6)."""

    @EVERY_CASE
    def test_no_blank_band_and_nothing_hidden_without_a_scrollbar(self, manager, tk_root,
                                                                   case):
        put_up(manager, tk_root, case)
        boxes = [(manager.popup_text, manager._text_scrollbar)]
        if manager.popup_furigana is not None:
            boxes.append((manager.popup_furigana, manager._original_scrollbar))
        for box, scrollbar in boxes:
            metrics = gallery.box_metrics(box, box.get_plain(), measure=len)
            assert metrics['blank'] <= 2, (case.key, metrics)
            if metrics['hidden']:
                assert scrollbar.winfo_ismapped(), (case.key, metrics)


class TestReadableLines:
    """About 70 characters a line for Latin text, 36 for CJK - WCAG 1.4.8 asks for at
    most 80 / 40 (TODO.md S5)."""

    @pytest.mark.parametrize('key', ['B', 'C', 'D', 'F', 'G'])
    def test_latin_lines_stay_within_seventy_five_characters(self, manager, tk_root, key):
        put_up(manager, tk_root, cases.case(key))
        box = manager.popup_text
        assert gallery.chars_per_line(box, box.get_plain()) <= 75

    def test_cjk_lines_stay_within_forty(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('M'))
        box = manager.popup_text
        assert gallery.chars_per_line(box, box.get_plain()) <= 40


class TestShortResultsAreSmall:
    """A short result is no longer held at 735 px by the bar (TODO.md S3, DoD4)."""

    @pytest.mark.parametrize('key', ['A', 'E'])
    def test_a_short_result_gets_a_small_popup(self, manager, tk_root, key):
        put_up(manager, tk_root, cases.case(key))
        # Open Translator now has its own trailing gap at the far right.
        assert manager.popup.winfo_width() <= 460


class TestHeightAndPlace:
    """At most the cap, inside the work area, never over the cursor (TODO.md S6)."""

    @EVERY_CASE
    def test_within_the_cap_and_the_screen_and_off_the_cursor(self, manager, tk_root, case):
        put_up(manager, tk_root, case)
        popup, layout = manager.popup, manager._layout
        x, y = popup.winfo_x(), popup.winfo_y()
        width, height = popup.winfo_width(), popup.winfo_height()
        work = layout['work']
        assert height <= layout['cap']
        assert work.left <= x and x + width <= work.right
        assert work.top <= y and y + height <= work.bottom
        assert not popup_layout.covers(x, y, width, height, manager._anchor())

    def test_at_the_bottom_right_corner_it_opens_up_and_left(self, manager, tk_root):
        work = manager._work_area()
        manager._last_mouse_x, manager._last_mouse_y = work.right - 20, work.bottom - 20
        put_up(manager, tk_root, cases.case('C'))
        popup = manager.popup
        assert popup.winfo_x() + popup.winfo_width() <= manager._last_mouse_x
        assert popup.winfo_y() + popup.winfo_height() <= manager._last_mouse_y


class TestShownOnlyOnceMeasured:
    """Built at alpha 0 and shown when measured: no flash, no jump - and always shown
    in the end (TODO.md §5.2, DoD6)."""

    def test_it_is_invisible_until_measured(self, manager, tk_root):
        cases.show_case(manager, cases.case('C'))
        assert alpha(manager) == 0.0 and not manager._layout['done']
        settle(manager, tk_root)
        assert manager._layout['done'] and alpha(manager) == 1.0

    def test_it_is_shown_only_after_its_final_size_is_drawn(self, manager, tk_root,
                                                            monkeypatch):
        # A delay one update() cannot outlast: at the real 30 ms, a busy machine's first
        # update() took longer, and the popup was already shown within it.
        monkeypatch.setattr(quick_translate, 'POPUP_REVEAL_DELAY_MS', 60000)
        cases.show_case(manager, cases.case('A'))
        tk_root.update_idletasks()
        tk_root.update()
        assert manager._layout['done'] and alpha(manager) == 0.0     # measured, not yet seen
        manager._reveal(manager._generation)
        tk_root.update_idletasks()
        assert alpha(manager) == 1.0

    def test_a_failure_while_measuring_still_shows_it(self, manager, tk_root, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("measuring broke")
        monkeypatch.setattr(manager, '_fit_boxes', broken)
        cases.show_case(manager, cases.case('C'))
        settle(manager, tk_root)
        assert alpha(manager) == 1.0

    def test_the_watchdog_shows_a_popup_never_laid_out(self, manager, tk_root):
        cases.show_case(manager, cases.case('A'))
        manager._finish_layout(manager._generation, forced=True)
        assert alpha(manager) == 1.0

    def test_closing_it_early_leaves_no_timer_on_a_deleted_command(self, manager, tk_root,
                                                                   monkeypatch):
        # Timers set on the popup outlived it: destroying a widget deletes its commands,
        # not its timers, which then fired into whatever new command reused the name -
        # a resize handler was called with no event (2026-10-19). Closed once measured,
        # with the watchdog and the reveal both still pending.
        monkeypatch.setattr(quick_translate, 'POPUP_REVEAL_DELAY_MS', 60000)

        def timers():
            return set(tk_root.tk.splitlist(tk_root.tk.call('after', 'info')))

        before = timers()
        cases.show_case(manager, cases.case('C'))
        run_until(tk_root, lambda: manager._layout['done'])
        assert manager._layout['done'] and not manager._layout.get('revealed')
        manager.close()
        tk_root.update()
        for timer in timers() - before:
            script = tk_root.tk.splitlist(tk_root.tk.call('after', 'info', timer))[0]
            command = tk_root.tk.splitlist(script)[0]
            assert tk_root.tk.call('info', 'commands', command), (
                f"timer {timer} would call the deleted command {command}")

    def test_a_closed_popup_ignores_its_late_watchdog(self, manager, tk_root):
        cases.show_case(manager, cases.case('C'))
        generation = manager._generation
        manager.close()
        tk_root.update()
        manager._finish_layout(generation, forced=True)
        assert manager.popup is None

    def test_a_newer_popup_ignores_the_older_ones_step_two(self, manager, tk_root):
        cases.show_case(manager, cases.case('C'))
        older = manager._generation
        cases.show_case(manager, cases.case('A'))
        manager._finish_layout(older, forced=True)
        assert not manager._layout['done']
        settle(manager, tk_root)
        assert manager._layout['done'] and manager._layout['revealed']

    def test_measuring_is_quick(self, manager, tk_root):
        # Measured 2-8 ms on this case (TODO.md S9); the bound only catches a box
        # measured before it has its width (wrapped a character per line).
        put_up(manager, tk_root, cases.case('C'))
        started = time.perf_counter()
        manager._fit_boxes(reposition=False)
        assert time.perf_counter() - started < 0.5


# --------------------------------------------------------------------------- #
# P3 - the layout (C)
# --------------------------------------------------------------------------- #
class TestActionBar:
    """Copy and Replace as words, the rest as icons with a tooltip (TODO.md Q1 (c))."""

    def test_two_words_and_four_named_icons(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        assert manager.popup_copy_btn.cget('text') == "Copy"
        assert manager.popup_replace_btn.cget('text') == "Replace"
        for button, name, icon in ((manager.popup_retranslate_btn, "Re-translate", 'arrow-repeat'),
                                   (manager.popup_dict_btn, "Dictionary", 'book'),
                                   (manager.popup_open_btn, "Open Translator", 'box-arrow-up-right'),
                                   (manager.popup_custom_prompt_btn, "Custom Prompt", 'pencil')):
            assert button.action_name == name
            assert button.icon_name == icon
            assert button.cget('text') == ''            # an image, not a glyph in a font
            assert str(button.cget('image'))

    def test_a_tooltip_names_the_icon(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        tip = next(t for t in manager._tooltips if t.widget is manager.popup_open_btn)
        tip.show()
        tk_root.update()
        assert tip.window is not None and tip.window.winfo_exists()
        assert tip.window.winfo_children()[0].cget('text') == "Open Translator"
        tip.hide()
        assert tip.window is None

    def test_a_tooltip_never_takes_the_focus(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B'))
        tip = manager._tooltips[0]
        tip.show()
        tk_root.update()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(tip.window.winfo_id()) or tip.window.winfo_id()
        assert user32.GetWindowLongW(hwnd, quick_translate.GWL_EXSTYLE) & \
            quick_translate.WS_EX_NOACTIVATE
        tip.hide()

    def test_the_dictionary_icon_keeps_its_tooltip(self, manager, tk_root):
        # _update_dict_button_state() unbinds <Enter>; the tooltip is attached after it.
        put_up(manager, tk_root, cases.case('B'))
        assert any(t.widget is manager.popup_dict_btn for t in manager._tooltips)
        assert manager.popup_dict_btn.bind('<Enter>')

    def test_a_grammar_fix_keeps_copy_replace_and_open(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('B-grammar'))
        assert manager.popup_open_btn is not None and manager.popup_copy_btn is not None
        assert manager.popup_retranslate_btn is None
        assert manager.popup_dict_btn is None and manager.popup_custom_prompt_btn is None

    def test_the_icons_are_images_so_no_icon_font_is_needed(self, manager, tk_root):
        # They were glyphs of Segoe Fluent Icons / MDL2 and became words without either font;
        # now they are Bootstrap Icons images that ship with the application.
        put_up(manager, tk_root, cases.case('B'))
        assert not hasattr(manager, '_icon_family')
        assert not hasattr(quick_translate, 'ICON_FONTS')
        assert gallery.close_button_metrics(manager.popup)['inside']

    def test_every_action_icon_is_a_real_icon(self, manager, tk_root):
        # a misspelt name fails when the button is built; draw each one to prove they exist
        from src.ui import components as ui
        for key, icon in quick_translate.ACTION_ICONS.items():
            assert str(ui.get().icon(icon, 16)), key


class TestOriginalBlock:
    """The original with its readings: named, and collapsed to three rows when longer
    (TODO.md Q5)."""

    def test_a_long_original_starts_collapsed(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('G'))
        toggle = manager.popup_original_toggle
        assert toggle.winfo_ismapped() and toggle.cget('text') == "Show all"
        rows = manager._rows_px(manager.popup_furigana, POPUP_COLLAPSED_ORIGINAL_ROWS)
        assert gallery.content_box(manager.popup_furigana, 'y') == rows

    def test_show_all_opens_it_within_the_cap(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('G'))
        before = manager.popup_furigana.winfo_height()
        manager._toggle_original()
        tk_root.update()
        assert manager.popup_original_toggle.cget('text') == "Show less"
        assert manager.popup_furigana.winfo_height() > before
        assert manager.popup.winfo_height() <= manager._layout['cap']

    def test_a_short_original_has_no_toggle(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('E'))
        assert not manager.popup_original_toggle.winfo_ismapped()

    def test_source_has_no_redundant_original_label(self, manager, tk_root):
        put_up(manager, tk_root, cases.case('E'))
        names = [w.cget('text') for w in manager._original_header.winfo_children()]
        assert not any(str(name).startswith('Original') for name in names)
        assert manager.popup_furigana.get_plain() == manager._current_original


# --------------------------------------------------------------------------- #
# P4 - a size the user chooses (TODO.md Q6)
# --------------------------------------------------------------------------- #
class SizeConfig(cases.FakeConfig):
    def __init__(self):
        self.saved = (None, None)

    def get_quick_popup_size(self):
        return self.saved

    def set_quick_popup_size(self, width, height):
        self.saved = (width, height)


@pytest.fixture
def sized(tk_root):
    config = SizeConfig()
    mgr = QuickTranslateManager(tk_root, config)
    mgr._last_mouse_x, mgr._last_mouse_y = 500, 300
    yield mgr, config
    mgr.close()


@pytest.fixture
def room_for_grip_growth(sized, monkeypatch):
    """Raw drag-delta tests need room; separate tests verify monitor clamping."""
    mgr, _ = sized
    monkeypatch.setattr(mgr, '_screen_at',
                        lambda _x, _y: popup_layout.WorkArea(0, 0, 4096, 2160))


class TestGrip:
    def test_dragging_the_grip_resizes_and_is_remembered(self, sized, tk_root,
                                                        room_for_grip_growth):
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('C'))
        width, height = mgr.popup.winfo_width(), mgr.popup.winfo_height()
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        mgr._grip_drag(SimpleNamespace(x_root=180, y_root=160))
        mgr._grip_release()
        tk_root.update()
        assert (mgr.popup.winfo_width(), mgr.popup.winfo_height()) == (width + 80, height + 60)
        run_until(tk_root, lambda: config.saved == (width + 80, height + 60))
        assert config.saved == (width + 80, height + 60)
        assert mgr._grip_outline is None

    def test_the_next_popup_reads_at_the_remembered_width(self, sized, tk_root):
        mgr, config = sized
        config.saved = (480, 300)
        put_up(mgr, tk_root, cases.case('D'))
        assert mgr._layout['width'] == 480
        # A reopened popup preserves three readable rows per box (Decision 35),
        # even when the saved height is shorter than its current chrome.
        floor = mgr._layout['chrome']
        floor += min(mgr._content_px(mgr.popup_text), mgr._rows_px(mgr.popup_text, 3))
        floor += mgr._insets(mgr.popup_text, 'y')
        if mgr.popup_furigana is not None:
            floor += min(mgr._content_px(mgr.popup_furigana), mgr._rows_px(mgr.popup_furigana, 3))
            floor += mgr._insets(mgr.popup_furigana, 'y')
        assert floor <= mgr.popup.winfo_height() <= max(300, floor)
        assert gallery.clipped_controls(mgr.popup) == []

    def test_a_short_result_stays_small_whatever_was_remembered(self, sized, tk_root):
        mgr, config = sized
        config.saved = (900, 700)
        put_up(mgr, tk_root, cases.case('A'))
        assert mgr.popup.winfo_width() < 900

    def test_a_double_click_returns_to_automatic(self, sized, tk_root):
        mgr, config = sized
        config.saved = (480, 300)
        put_up(mgr, tk_root, cases.case('D'))
        mgr._grip_reset()
        tk_root.update()
        assert config.saved == (None, None)
        assert mgr._layout['cap'] == popup_layout.height_cap(mgr._layout['work'])


def grip_drag(mgr, dx, dy):
    """One motion of a drag that started at (100, 100)."""
    mgr._grip_drag(SimpleNamespace(x_root=100 + dx, y_root=100 + dy))


def run_until(tk_root, done, timeout=2.0):
    deadline = time.perf_counter() + timeout
    while not done() and time.perf_counter() < deadline:
        time.sleep(0.005)
        tk_root.update()


def resize_settled(manager):
    owner = manager._content_resize
    return (owner.state == 'IDLE' and owner._commit_job is None and owner._restore_job is None
            and manager._resize_settle_job is None and manager._resize_save_job is None)


class TestGripLiveResize:
    """Resize the real shell during motion and lay out the held body on release."""

    def test_long_gesture_and_final_fit_never_synchronize_the_document(
            self, design, sized, tk_root, room_for_grip_growth, monkeypatch):
        mgr, _ = sized
        monkeypatch.setattr(mgr, '_ruby_enabled', lambda: False)
        translated, original = '翻訳された文章。' * 1000, 'A long source sentence. ' * 1000
        mgr.show(translated, 'Japanese', original=original, source_lang='English')
        settle(mgr, tk_root)
        initial = mgr.popup.winfo_width(), mgr.popup.winfo_height()
        def forbidden(*args, **kwargs):
            raise AssertionError('Resize forced synchronous document measurement')
        monkeypatch.setattr(mgr, '_rows_px', forbidden)
        monkeypatch.setattr(mgr, '_content_px', forbidden)
        for box in (mgr.popup_text, mgr.popup_furigana):
            native_index, native_count = box.index, box.count
            def index(value, native=native_index):
                assert 'display line' not in str(value), 'Resize walked document display-line indices'
                return native(value)
            def count(*args, native=native_count):
                assert 'update' not in args, 'Resize synchronized off-screen line metrics'
                return native(*args)
            monkeypatch.setattr(box, 'index', index)
            monkeypatch.setattr(box, 'count', count)
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        for step in (20, 40, 80):
            grip_drag(mgr, step, step)
            tk_root.update()
        mgr._grip_release()
        run_until(tk_root, lambda: mgr._resize_settle_job is None
                  and (mgr.popup.winfo_width(), mgr.popup.winfo_height()) == (initial[0] + 80, initial[1] + 80))
        assert mgr._resize_settle_job is None
        assert mgr.popup_text.get_plain() == translated
        assert mgr.popup_furigana.get_plain() == original

    @pytest.mark.parametrize('key', ['A', 'C', 'G'])
    def test_cached_visible_height_matches_real_plain_or_ruby_rows(self, manager, tk_root, key):
        put_up(manager, tk_root, cases.case(key))
        for box in (manager.popup_text, manager.popup_furigana):
            if box is None:
                continue
            # Start at the document's first row so canonical auto-fit counts
            # are an independent reference for the viewport-only reading.
            box.yview_moveto(0)
            tk_root.update_idletasks()
            expected = manager._rows_px(box, 1)
            assert manager._visible_rows_px(box, 1, from_start=True) == expected

    def test_long_cjk_body_and_final_size_settle_without_nested_event_pumping(
            self, design, sized, tk_root, room_for_grip_growth, monkeypatch):
        mgr, config = sized
        monkeypatch.setattr(mgr, '_ruby_enabled', lambda: False)
        mgr.show('翻訳された文章。' * 1000, 'Japanese',
                 original='A long source sentence. ' * 1000, source_lang='English')
        jobs, errors, observed = [], [], set()
        previews = set()
        state = {'step': -1, 'done': False}
        def tick():
            try:
                if not (mgr._layout and mgr._layout.get('revealed')
                        and mgr._layout.get('done') and mgr._layout.get('resize_metrics')):
                    jobs.append(tk_root.after(25, tick))
                    return
                if state['step'] < 0:
                    mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
                    state['initial'] = mgr._grip_start['rect'][2:]
                    state['step'] = 0
                observed.add((mgr.popup.winfo_width(), mgr._main_frame.winfo_width()))
                if mgr._grip_outline is not None:
                    previews.add(mgr._grip_outline.rect)
                sizes = [(800, 650), (900, 700), (750, 650), (680, 680)]
                if state['step'] < len(sizes):
                    w, h = sizes[state['step']]
                    initial_w, initial_h = state['initial']
                    mgr._grip_drag(SimpleNamespace(x_root=100 + w - initial_w,
                                                   y_root=100 + h - initial_h))
                    state['step'] += 1
                elif mgr._grip_start is not None:
                    mgr._grip_release()
                elif (mgr._resize_settle_job is None and config.saved == (680, 680)
                        and (mgr.popup.winfo_width(), mgr.popup.winfo_height()) == (680, 680)
                        and mgr._main_frame.winfo_width() < 680
                        and mgr.popup_text.winfo_width() < 680):
                    state['done'] = True
                    tk_root.quit()
                    return
                jobs.append(tk_root.after(40, tick))
            except BaseException as error:
                errors.append(error)
                tk_root.quit()
        jobs.append(tk_root.after(20, tick))
        jobs.append(tk_root.after(15000, tk_root.quit))
        try:
            tk_root.mainloop()
        finally:
            for job in jobs:
                tk_root.after_cancel(job)
        assert not errors, errors
        assert state['done'], (state, mgr._layout, mgr._resize_settle_job)
        assert len(observed) >= 2
        assert len(previews) >= 3
        assert mgr.popup_text.get_plain() == '翻訳された文章。' * 1000
        assert mgr._grip_outline is None

    @pytest.fixture
    def layouts(self, sized, monkeypatch):
        mgr, _ = sized
        calls = []
        fit = mgr._fit_boxes
        def counted(*args, **kwargs):
            calls.append(kwargs.get('size'))
            return fit(*args, **kwargs)
        monkeypatch.setattr(mgr, '_fit_boxes', counted)
        return calls

    def test_body_stays_fixed_while_button_remains_held(self, sized, tk_root, room_for_grip_growth):
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('C'))
        width, height = mgr.popup.winfo_width(), mgr.popup.winfo_height()
        text_width = mgr.popup_text.winfo_width()
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        grip_drag(mgr, 80, 60)
        target = width + 80, height + 60
        run_until(tk_root, lambda: mgr._grip_outline is not None)
        assert (mgr.popup.winfo_width(), mgr.popup.winfo_height()) == (width, height)
        assert mgr._grip_outline.rect[2:] == target
        assert mgr.popup_text.winfo_width() == text_width
        assert mgr._grip_start is not None
        assert mgr._grip_outline.visible
        assert config.saved == (None, None)
        mgr._grip_release()
        run_until(tk_root, lambda: mgr.popup_text.winfo_width() > text_width)
        assert mgr.popup_text.winfo_width() > text_width

    def test_motion_and_release_never_measure_the_full_document(self, sized, tk_root,
                                                               layouts, monkeypatch):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('G'))
        layouts.clear()
        def forbidden(*args, **kwargs):
            raise AssertionError('Full-document measurement reached the resize path')
        monkeypatch.setattr(mgr, '_content_px', forbidden)
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        for step in range(1, 21):
            grip_drag(mgr, 4 * step, 3 * step)
            tk_root.update()
        mgr._grip_release()
        tk_root.update()
        assert layouts == []

    def test_continuous_motion_does_not_starve_shell_updates(self, sized, tk_root,
                                                            room_for_grip_growth):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        initial = mgr.popup.winfo_width()
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        deadline, step, observed = time.perf_counter() + .25, 0, set()
        while time.perf_counter() < deadline:
            step += 1
            grip_drag(mgr, step, step)
            tk_root.update()
            observed.add(mgr.popup.winfo_width())
            time.sleep(.003)
        assert observed == {initial}
        assert mgr._grip_outline is not None
        assert mgr._grip_outline.rect[2] > initial
        assert mgr._grip_start is not None
        mgr._grip_release()

    def test_many_pending_events_consume_only_the_latest_size(self, sized, tk_root,
                                                              room_for_grip_growth, monkeypatch):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        width, height = mgr.popup.winfo_width(), mgr.popup.winfo_height()
        applied = []
        original = mgr._live_user_layout
        def counted(w, h, **kwargs):
            applied.append((w, h))
            return original(w, h, **kwargs)
        monkeypatch.setattr(mgr, '_live_user_layout', counted)
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        for step in range(1, 21):
            grip_drag(mgr, 4 * step, 3 * step)
        run_until(tk_root, lambda: mgr._grip_outline is not None)
        assert mgr.popup.winfo_width() == width
        assert mgr._grip_outline.rect[2:] == (width + 80, height + 60)
        assert applied == []
        mgr._grip_release()
        run_until(tk_root, lambda: bool(applied))
        assert applied[0] == (width + 80, height + 60)
        assert all(value == applied[0] for value in applied)

    def test_release_applies_last_motion_before_its_timer(self, sized, tk_root,
                                                         room_for_grip_growth, monkeypatch):
        monkeypatch.setattr(quick_translate, 'RESIZE_INTERVAL_MS', 60_000)
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('C'))
        target = mgr.popup.winfo_width() + 80, mgr.popup.winfo_height() + 60
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        grip_drag(mgr, 80, 60)
        pending = mgr._grip_preview_after
        mgr._grip_release()
        tk_root.update()
        assert (mgr.popup.winfo_width(), mgr.popup.winfo_height()) == target
        assert pending not in tk_root.tk.splitlist(tk_root.tk.call('after', 'info'))
        run_until(tk_root, lambda: config.saved == target)
        assert config.saved == target

    def test_saving_is_deferred_and_coalesces_completed_gestures(self, sized, tk_root,
                                                                monkeypatch, room_for_grip_growth):
        monkeypatch.setattr(quick_translate, 'RESIZE_SAVE_DELAY_MS', 60_000)
        mgr, config = sized
        calls = []
        config.set_quick_popup_size = lambda w, h: calls.append((w, h))
        put_up(mgr, tk_root, cases.case('C'))
        for _ in range(2):
            mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
            grip_drag(mgr, 20, 20)
            mgr._grip_release()
            tk_root.update()
        target = mgr._user_size
        assert calls == []
        mgr.close()
        assert calls == [target]

    def test_resize_keeps_focus_and_noactivate_style(self, sized, tk_root, room_for_grip_growth):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        assert ctypes.windll.user32.GetWindowLongW(window_handle(mgr.popup), quick_translate.GWL_EXSTYLE) & \
            quick_translate.WS_EX_NOACTIVATE
        active = ctypes.windll.user32.GetActiveWindow()
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        grip_drag(mgr, 40, 30)
        run_until(tk_root, lambda: mgr._grip_preview_after is None)
        hwnd = window_handle(mgr.popup)
        assert ctypes.windll.user32.GetWindowLongW(hwnd, quick_translate.GWL_EXSTYLE) & \
            quick_translate.WS_EX_NOACTIVATE
        assert ctypes.windll.user32.GetActiveWindow() == active
        mgr._grip_release()

    @pytest.mark.parametrize('editing', [False, True])
    def test_remap_and_topmost_changes_preserve_result_or_edit_focus_policy(self, sized, tk_root, editing):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        if editing:
            mgr._enter_custom_prompt_mode()
        mgr.popup.withdraw()
        mgr.popup.deiconify()
        tk_root.update()
        mgr._end_front(mgr.popup)
        tk_root.update()
        style = ctypes.windll.user32.GetWindowLongW(window_handle(mgr.popup), quick_translate.GWL_EXSTYLE)
        assert bool(style & quick_translate.WS_EX_NOACTIVATE) is not editing

    def test_end_front_keeps_a_move_that_is_still_waiting_for_the_idle_update(self, sized, tk_root):
        """DEFERRED D31: Tk re-reads the window's place when the topmost bit changes, so a geometry()
        still waiting for its idle update lost its new position and kept only the new size."""
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        popup = mgr.popup
        x, y, width, height = popup.winfo_x(), popup.winfo_y(), popup.winfo_width(), popup.winfo_height()
        target = (x - 40, y - 30, width + 40, height + 30)
        popup.geometry(f'{target[2]}x{target[3]}+{target[0]}+{target[1]}')
        mgr._end_front(popup)  # the POPUP_FRONT_MS timer, before Tk has applied the geometry
        tk_root.update()
        assert (popup.winfo_x(), popup.winfo_y(), popup.winfo_width(), popup.winfo_height()) == target
        assert popup.attributes('-topmost') in (0, False, '0')

    def test_editing_refreshes_only_the_row_floor_before_resize(self, sized, tk_root, monkeypatch):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('H'))
        mgr._enter_custom_prompt_mode()
        tk_root.update()
        expected = mgr._rows_px(mgr.popup_text, 1) + mgr._insets(mgr.popup_text, 'y')
        monkeypatch.setattr(mgr, '_content_px',
                            lambda *_args: pytest.fail('Gesture entry measured the full document'))
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        assert mgr._layout['resize_metrics']['min_translation'] == expected
        mgr._grip_release()

    def test_repeated_motion_preserves_canonical_and_selection(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('G'))
        box = mgr.popup_text
        before = box.get_plain()
        box.tag_add('sel', '1.0', '1.2')
        selection = box.tag_ranges('sel')
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        for step in (10, 20, -10, 30):
            grip_drag(mgr, step, step)
            mgr._apply_user_size()
            tk_root.update()
        mgr._grip_release()
        tk_root.update()
        assert box.get_plain() == before
        assert box.tag_ranges('sel') == selection

    def test_double_click_cancels_live_and_save_work(self, sized, tk_root, monkeypatch):
        monkeypatch.setattr(quick_translate, 'RESIZE_INTERVAL_MS', 60_000)
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('C'))
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        grip_drag(mgr, 40, 30)
        mgr._grip_reset()
        tk_root.update()
        assert config.saved == (None, None)
        assert mgr._grip_preview_after is None
        assert mgr._resize_settle_job is None
        assert mgr._resize_save_job is None
        assert mgr._grip_start is None

    @pytest.mark.parametrize('external_destroy', [False, True])
    def test_closing_mid_gesture_cancels_owned_callbacks(self, sized, tk_root, monkeypatch,
                                                       external_destroy):
        monkeypatch.setattr(quick_translate, 'RESIZE_INTERVAL_MS', 60_000)
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        grip_drag(mgr, 40, 30)
        pending = mgr._grip_preview_after
        if external_destroy:
            mgr.popup.destroy()
        else:
            mgr.close()
        tk_root.update()
        assert pending not in tk_root.tk.splitlist(tk_root.tk.call('after', 'info'))
        assert mgr._grip_preview_after is None
        assert mgr._resize_settle_job is None
        assert mgr._grip_start is None

    def test_cancelled_native_loop_restores_actual_final_geometry(self, sized, tk_root,
                                                                 monkeypatch):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        before = popup_rect(mgr)
        bridge = mgr._native_sizing
        bridge.running = True
        mgr._native_resize_started('se', before, (100, 100))
        mgr._native_resize_rect((before[0], before[1], before[2] + 40, before[3] + 30))
        mgr._native_resize_rect(before)
        bridge.running = False
        mgr._native_resize_finished()
        tk_root.update()
        assert popup_rect(mgr) == before
        assert mgr._grip_start is None
        assert mgr._native_sizing is bridge and bridge.hwnd is not None

    @pytest.mark.parametrize('edge', popup_layout.EDGES)
    def test_each_live_edge_keeps_the_opposite_side_fixed(self, sized, tk_root, edge,
                                                         room_for_grip_growth):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        x, y, width, height = popup_rect(mgr)
        dx, dy = (-30 if 'w' in edge else 30), (-20 if 'n' in edge else 20)
        drag_edge(mgr, edge, dx, dy, release=False)
        expected = popup_layout.resize((x, y, width, height), edge, dx, dy,
            min_width=mgr._grip_start['min_width'], min_height=mgr._grip_start['min_height'],
            bounds=popup_layout.WorkArea(0, 0, 4096, 2160))
        run_until(tk_root, lambda: mgr._grip_outline is not None)
        assert popup_rect(mgr) == (x,y,width,height)
        assert mgr._grip_outline.rect == expected
        assert mgr.popup_text.winfo_ismapped()
        mgr._grip_release()
        run_until(tk_root, lambda: popup_rect(mgr) == expected)

    @pytest.mark.parametrize('key', ['C-replace', 'C-prompt'])
    def test_resize_preserves_preview_or_edit_state(self, sized, tk_root, key):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case(key))
        box = mgr.popup_text
        before = box.get_plain(), str(box.cget('state')), bool(getattr(mgr, '_replace_preview', False))
        drag_edge(mgr, 'se', 40, 30)
        tk_root.update()
        assert (box.get_plain(), str(box.cget('state')),
                bool(getattr(mgr, '_replace_preview', False))) == before
        assert mgr.popup.winfo_ismapped()

    def test_short_panes_remove_the_temporary_gutter_and_settle(self, sized, tk_root):
        mgr, _ = sized
        mgr.show('Small result.', 'English', original='Short source.')
        settle(mgr, tk_root)
        drag_edge(mgr, 'se', 80, 60)
        run_until(tk_root, lambda: resize_settled(mgr))
        tk_root.update_idletasks()
        assert not mgr._text_scrollbar.winfo_manager()
        assert not mgr._original_scrollbar.winfo_manager()
        before = mgr.popup_text.winfo_width(), mgr.popup_furigana.winfo_width()
        for _ in range(5):
            tk_root.update_idletasks()
        assert (mgr.popup_text.winfo_width(), mgr.popup_furigana.winfo_width()) == before
        assert mgr._resize_settle_job is None

    def test_expanded_short_source_yields_to_translation_after_rewrap(self, sized, tk_root):
        mgr, _ = sized
        source = 'This is the original sentence. ' * 3
        mgr.show('A translated sentence with several words. ' * 120, 'English', original=source)
        settle(mgr, tk_root)
        mgr._toggle_original()
        tk_root.update()
        drag_edge(mgr, 'e', -120, 0)
        run_until(tk_root, lambda: resize_settled(mgr))
        natural = mgr._content_px(mgr.popup_furigana) + mgr._insets(mgr.popup_furigana, 'y')
        layout = mgr._layout
        floor = min(natural, layout['resize_metrics']['min_original'])
        assert mgr.popup_furigana.get_plain() == source
        assert int(mgr._original_holder.cget('height')) == floor
        assert layout['heights'].translation_capped
        assert layout['heights'].translation == layout['size'][1] - layout['chrome'] - floor
        assert bool(mgr._original_scrollbar.winfo_ismapped()) == layout['heights'].original_capped
        line = mgr.popup_furigana.dlineinfo('1.0')
        assert line is not None and line[1] + line[3] <= mgr.popup_furigana.winfo_height()

    def test_error_notice_wrap_updates_the_body_budget_without_full_reflow(self, sized, tk_root,
                                                                         monkeypatch):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('K'))
        monkeypatch.setattr(mgr, '_content_px',
                            lambda *_args: pytest.fail('Error resizing reached full document measurement'))
        for delta in (200, -120):
            drag_edge(mgr, 'se', delta, 80)
            run_until(tk_root, lambda: resize_settled(mgr))
            tk_root.update_idletasks()
            chrome = (mgr._main_frame.winfo_reqheight() - mgr._text_holder.winfo_reqheight()
                      + 2 * mgr._look.ring)
            assert abs(mgr._layout['chrome'] - chrome) <= 2
            assert mgr.popup_text.winfo_height() > 1
            assert gallery.clipped_controls(mgr.popup) == []

    def test_native_capture_owns_motion_and_release_until_the_system_loop_returns(self, sized, tk_root):
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('C'))
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        start = mgr._grip_start
        mgr._native_sizing = SimpleNamespace(running=True, hwnd=10, close=lambda **_kwargs: None)
        grip_drag(mgr, 80, 60)
        mgr._grip_release()
        assert mgr._grip_start is start
        assert mgr._grip_preview_after is None
        assert config.saved == (None, None)
        mgr._native_sizing.running = False
        mgr._grip_release()
        assert mgr._grip_start is None

    @pytest.mark.parametrize('native', [False, True])
    def test_close_during_layout_cannot_touch_a_replaced_window(self, sized, tk_root, monkeypatch, native):
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('C'))
        mgr._grip_press(SimpleNamespace(x_root=100, y_root=100))
        grip_drag(mgr, 40, 30)
        rect = mgr._user_rect
        monkeypatch.setattr(mgr, '_live_user_layout', lambda *_args, **_kwargs: mgr.close())
        if native:
            mgr._native_resize_rect(rect)
            assert mgr.popup is not None
            mgr._native_resize_finished()
        else:
            mgr._grip_release()
        tk_root.update()
        assert mgr.popup is None and mgr._layout is None
        assert mgr._grip_preview_after is None and mgr._resize_save_job is None
        assert config.saved == (None, None)


def rect_of(widget):
    return (widget.winfo_rootx(), widget.winfo_rooty(),
            widget.winfo_rootx() + widget.winfo_width(),
            widget.winfo_rooty() + widget.winfo_height())


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def popup_rect(mgr):
    popup = mgr.popup
    return popup.winfo_x(), popup.winfo_y(), popup.winfo_width(), popup.winfo_height()


def drag_edge(mgr, edge, dx, dy, release=True, start=(600, 400)):
    mgr._grip_press(SimpleNamespace(x_root=start[0], y_root=start[1]), edge)
    mgr._grip_drag(SimpleNamespace(x_root=start[0] + dx, y_root=start[1] + dy))
    if release:
        mgr._grip_release()


class TestResizeFromEveryEdge:
    """Every edge and corner resizes, like a window; down to a row a box, out to the
    edge of whichever screen the pointer is on (Decision 35)."""

    @pytest.mark.parametrize('key', ['A', 'C', 'G', 'K', 'L', 'C-replace', 'C-prompt'])
    def test_no_text_and_no_button_reaches_the_edges(self, sized, tk_root, key):
        # The edges are the outer RESIZE_EDGE_PX of the frame's padding; nothing a user
        # clicks for something else may sit there (the grip is a resize control itself).
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case(key))
        edge = quick_translate.RESIZE_EDGE_PX
        popup = mgr.popup
        inner = (popup.winfo_rootx() + edge, popup.winfo_rooty() + edge,
                 popup.winfo_rootx() + popup.winfo_width() - edge,
                 popup.winfo_rooty() + popup.winfo_height() - edge)
        controls = [w for w in gallery.descendants(popup, into_text=False)
                    if w is not mgr._grip and w.winfo_ismapped()
                    and w.winfo_class() in ('Text', 'Button', 'TButton', 'Label', 'TLabel',
                                            'Scrollbar', 'TScrollbar', 'Entry', 'TEntry')]
        assert controls
        for control in controls:
            left, top, right, bottom = rect_of(control)
            assert (inner[0] <= left and inner[1] <= top and right <= inner[2]
                    and bottom <= inner[3]), (
                f"{key}: {control.winfo_class()} {rect_of(control)} reaches the edges "
                f"(inside is {inner})")

    def test_an_edge_shows_a_resize_cursor_and_the_inside_does_not(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('A'))
        frame = mgr._main_frame
        width, height = frame.winfo_width(), frame.winfo_height()
        for (x, y), cursor in (((2, height // 2), 'size_we'),
                               ((width - 2, 2), 'size_ne_sw'),
                               ((width // 2, height - 2), 'size_ns'),
                               ((10, 10), '')):
            frame.event_generate('<Motion>', x=x, y=y)
            tk_root.update()
            assert str(frame.cget('cursor')) == cursor, (x, y)
        frame.event_generate('<Motion>', x=2, y=height // 2)
        frame.event_generate('<Leave>')
        tk_root.update()
        assert str(frame.cget('cursor')) == ''          # children would inherit it

    def test_pressing_an_edge_resizes_and_inside_it_moves(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('A'))
        frame = mgr._main_frame
        x, y, width, height = popup_rect(mgr)
        root_x, root_y = frame.winfo_rootx(), frame.winfo_rooty() + height // 2

        def drag(at_x, dx):
            frame.event_generate('<ButtonPress-1>', x=at_x, y=height // 2,
                                 rootx=root_x + at_x, rooty=root_y)
            frame.event_generate('<B1-Motion>', x=at_x + dx, y=height // 2,
                                 rootx=root_x + at_x + dx, rooty=root_y)
            frame.event_generate('<ButtonRelease-1>', x=at_x + dx, y=height // 2,
                                 rootx=root_x + at_x + dx, rooty=root_y)
            tk_root.update()

        drag(2, -60)                                    # on the left edge: resized
        assert popup_rect(mgr) == (x - 60, y, width + 60, height)
        drag(10, 40)                                    # in the padding: moved
        assert popup_rect(mgr) == (x - 20, y, width + 60, height)

    def test_the_left_edge_moves_and_the_right_stays(self, sized, tk_root):
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('A'))
        x, y, width, height = popup_rect(mgr)
        drag_edge(mgr, 'w', -60, 0)
        tk_root.update()
        assert popup_rect(mgr) == (x - 60, y, width + 60, height)
        run_until(tk_root, lambda: config.saved == (width + 60, height))
        assert config.saved == (width + 60, height)

    def test_the_top_left_corner_moves_and_the_bottom_right_stays(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('A'))
        x, y, width, height = popup_rect(mgr)
        # This test isolates opposite-edge geometry; monitor clamping is tested separately.
        mgr.popup.geometry(f'+{max(x, 100)}+{max(y, 100)}')
        tk_root.update()
        x, y, width, height = popup_rect(mgr)
        drag_edge(mgr, 'nw', -40, -30)
        tk_root.update()
        assert popup_rect(mgr) == (x - 40, y - 30, width + 40, height + 30)

    def test_only_the_outline_follows_a_left_edge_before_release(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('A'))
        before = popup_rect(mgr)
        x, y, width, height = before
        drag_edge(mgr, 'w', -60, 0, release=False)
        tk_root.update()
        target = (x - 60, y, width + 60, height)
        run_until(tk_root, lambda: mgr._grip_outline is not None)
        assert popup_rect(mgr) == before
        assert mgr._grip_outline.rect == target
        mgr._grip_release()
        run_until(tk_root, lambda: popup_rect(mgr) == target)

    def test_it_shrinks_to_a_row_of_text(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        three_rows = mgr.popup.winfo_height() - (mgr.popup_text.winfo_height()
                                                 - mgr._rows_px(mgr.popup_text, 3))
        drag_edge(mgr, 's', 0, -2000)
        tk_root.update()
        one_row = mgr._rows_px(mgr.popup_text, 1)
        assert gallery.content_box(mgr.popup_text, 'y') >= one_row
        assert gallery.content_box(mgr.popup_text, 'y') < mgr._rows_px(mgr.popup_text, 2)
        assert mgr.popup.winfo_height() < three_rows
        assert mgr._text_scrollbar.winfo_ismapped()

    def test_with_readings_each_box_keeps_a_row(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('G'))
        drag_edge(mgr, 'n', 0, 2000)
        tk_root.update()
        for box, scrollbar in ((mgr.popup_text, mgr._text_scrollbar),
                               (mgr.popup_furigana, mgr._original_scrollbar)):
            assert gallery.content_box(box, 'y') >= mgr._rows_px(box, 1)
            assert scrollbar.winfo_ismapped()

    def test_not_narrower_than_its_rows(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('C'))
        drag_edge(mgr, 'e', -2000, 0)
        tk_root.update()
        assert mgr.popup.winfo_width() == mgr._required_width()
        assert gallery.close_button_metrics(mgr.popup)['inside']
        assert gallery.clipped_controls(mgr.popup) == []

    def test_an_edge_stops_at_the_edge_of_the_screen(self, sized, tk_root):
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('A'))
        far = (100_000, 400)
        right = mgr._screen_at(*far).right
        drag_edge(mgr, 'e', far[0] - 600, 0)
        tk_root.update()
        assert mgr.popup.winfo_x() + mgr.popup.winfo_width() == right

    def test_a_popup_moved_to_another_screen_still_resizes(self, sized, tk_root,
                                                           monkeypatch):
        # It used to stop at the screen it opened on: dragged onto a second one, any
        # drag snapped it to its smallest width.
        mgr, _ = sized
        put_up(mgr, tk_root, cases.case('A'))
        real = quick_translate.get_monitor_work_area
        monkeypatch.setattr(quick_translate, 'get_monitor_work_area',
                            lambda x, y: (5000, 0, 7000, 1040) if x >= 5000 else real(x, y))
        mgr.popup.geometry('+5200+200')
        tk_root.update()
        _, _, width, height = popup_rect(mgr)
        drag_edge(mgr, 'se', 80, 60, start=(5200 + width, 200 + height))
        tk_root.update()
        assert popup_rect(mgr) == (5200, 200, width + 80, height + 60)

    def test_a_click_on_an_edge_changes_nothing(self, sized, tk_root):
        # Not even after an earlier drag, the popup moved since and laid out again: the
        # click must not put it back where that drag left it.
        mgr, config = sized
        put_up(mgr, tk_root, cases.case('A'))
        drag_edge(mgr, 'e', 40, 0)
        tk_root.update()
        run_until(tk_root, lambda: mgr._resize_save_job is None)
        mgr.popup.geometry('+300+250')
        tk_root.update()
        mgr._fit_boxes(reposition=False, size=mgr._user_size)
        tk_root.update()
        before, saved = popup_rect(mgr), config.saved
        mgr._grip_press(SimpleNamespace(x_root=600, y_root=400), 'w')
        mgr._grip_release()
        tk_root.update()
        assert popup_rect(mgr) == before
        assert config.saved == saved

    def test_a_tiny_remembered_height_still_opens_with_three_rows(self, sized, tk_root):
        mgr, config = sized
        config.saved = (None, 60)
        put_up(mgr, tk_root, cases.case('C'))
        assert gallery.content_box(mgr.popup_text, 'y') >= mgr._rows_px(mgr.popup_text, 3)


def window_handle(widget):
    return ctypes.windll.user32.GetParent(widget.winfo_id()) or widget.winfo_id()


def is_above(upper, lower):
    """Whether window `upper` is above window `lower` in the z-order."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetWindow(lower, 3)            # GW_HWNDPREV: the next window up
    while hwnd:
        if hwnd == upper:
            return True
        hwnd = user32.GetWindow(hwnd, 3)
    return False


class TestOnTopWhenItAppears:
    """On top when it appears, for POPUP_FRONT_MS, then free to go behind. It was on top
    for 100 ms from its creation, which could end while it was still invisible - and the
    app the text came from rose over it (seen 2026-10-19 under a maximized VS Code)."""

    def test_it_is_on_top_when_it_appears_then_lets_go(self, manager, tk_root, monkeypatch):
        monkeypatch.setattr(quick_translate, 'POPUP_FRONT_MS', 150)
        put_up(manager, tk_root, cases.case('C'))
        assert int(manager.popup.attributes('-topmost')) == 1
        run_until(tk_root, lambda: int(manager.popup.attributes('-topmost')) == 0)
        assert int(manager.popup.attributes('-topmost')) == 0

    def test_the_time_on_top_counts_from_when_it_appears(self, manager, tk_root,
                                                         monkeypatch):
        monkeypatch.setattr(quick_translate, 'POPUP_REVEAL_DELAY_MS', 300)
        monkeypatch.setattr(quick_translate, 'POPUP_FRONT_MS', 300)
        cases.show_case(manager, cases.case('C'))
        run_until(tk_root, lambda: manager._layout and manager._layout.get('revealed'))
        assert manager._layout.get('revealed')
        assert int(manager.popup.attributes('-topmost')) == 1

    def test_a_window_raised_while_it_is_invisible_does_not_cover_it(self, manager, tk_root,
                                                                     monkeypatch):
        monkeypatch.setattr(quick_translate, 'POPUP_REVEAL_DELAY_MS', 300)
        monkeypatch.setattr(quick_translate, 'POPUP_FRONT_MS', 100)
        other = tk.Toplevel(tk_root)
        try:
            other.geometry('1000x700+100+100')
            tk_root.update()
            cases.show_case(manager, cases.case('C'))
            tk_root.after(150, other.lift)       # the source app rising, mid-measuring
            run_until(tk_root, lambda: manager._layout and manager._layout.get('revealed'))
            run_until(tk_root, lambda: int(manager.popup.attributes('-topmost')) == 0)
            tk_root.update()
            assert int(manager.popup.attributes('-topmost')) == 0
            assert not is_above(window_handle(other), window_handle(manager.popup))
        finally:
            other.destroy()


class TestLoadingIndicator:
    """Placed by the same rules, so it never spills off the screen (TODO.md Q9)."""

    def test_at_the_bottom_right_corner_it_stays_on_the_screen(self, manager, tk_root):
        work = manager._work_area()
        manager._last_mouse_x, manager._last_mouse_y = work.right - 5, work.bottom - 5
        manager.show_loading("French")
        tk_root.update()
        popup = manager.popup
        assert work.left <= popup.winfo_x()
        assert popup.winfo_x() + popup.winfo_width() <= work.right
        assert work.top <= popup.winfo_y()
        assert popup.winfo_y() + popup.winfo_height() <= work.bottom
        manager.close()

@pytest.mark.parametrize('case_key', ['A', 'E'])
def test_japanese_large_text_header_keeps_the_settings_action_whole(tk_root, case_key):
    from src.ui import components as ui
    context = ui.get()
    previous_language, previous_scale = context.manager.ui_language, context.manager.text_scale
    mgr = None
    try:
        context.manager.set_ui_language('ja')
        context.manager.set_text_scale(1.3)
        mgr = QuickTranslateManager(tk_root, cases.FakeConfig())
        mgr._last_mouse_x, mgr._last_mouse_y = 500, 300
        put_up(mgr, tk_root, cases.case(case_key))
        assert mgr._replace_gear_btn is None
        assert gallery.clipped_controls(mgr.popup) == []
    finally:
        if mgr is not None:
            mgr.close()
        context.manager.set_ui_language(previous_language)
        context.manager.set_text_scale(previous_scale)

def test_popup_direction_label_switches_locale_without_changing_request_data(design):
    manager = QuickTranslateManager(design.root, cases.FakeConfig())
    manager._last_mouse_x, manager._last_mouse_y = 500, 300
    try:
        put_up(manager, design.root, cases.case('A'))
        payload = (manager._current_target_lang, manager._current_translation)
        assert manager.popup_chip.cget('text') == 'To French'
        design.manager.set_ui_language('ja')
        design.root.update()
        assert manager.popup_chip.cget('text') == 'フランス語へ'
        assert (manager._current_target_lang, manager._current_translation) == payload
        design.manager.set_ui_language('en')
        design.root.update()
        assert manager.popup_chip.cget('text') == 'To French'
        assert (manager._current_target_lang, manager._current_translation) == payload
    finally:
        manager.close()

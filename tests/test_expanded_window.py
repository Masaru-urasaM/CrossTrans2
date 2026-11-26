"""
Tests for the Expanded translation window (TODO.md U7, V26): a centred reading column one comfortable
line wide, a larger type size, real buttons, one acknowledgement for Copy, Ctrl+C that respects a
selection, counts from the real text. (The furigana behaviour is in test_main_window_ruby.)
"""
import time
import tkinter as tk
from tkinter import font as tkfont

import pytest

from src.ui import components as ui
from src.ui import expanded_window as EW
from src.ui import popup_layout
from src.ui.ruby_text import RubyText

from tests.test_components import env, light  # noqa: F401  (the shared component context)

SENTENCE = "I study Japanese every day. I am studying linguistics at the University of Tokyo. "
PARAGRAPH = SENTENCE * 6


class Toast:
    def __init__(self):
        self.messages = []

    def show_warning(self, message):
        self.messages.append(('warning', message))

    def show_success(self, message):
        self.messages.append(('success', message))


class Config:
    def get_furigana_enabled(self):
        return True


def pump(root, seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)


def widgets_of(top, kind):
    found = []

    def walk(item):
        for child in item.winfo_children():
            if isinstance(child, kind):
                found.append(child)
            walk(child)
    walk(top)
    return found


@pytest.fixture
def opened(tk_root, light):
    """(window manager, toplevel, RubyText) of one expanded translation."""
    tops = []

    def show(text=PARAGRAPH, language="English"):
        before = set(tk_root.winfo_children())
        manager = EW.ExpandedTranslationWindow(tk_root, Toast(), Config())
        manager.show(text, language)
        pump(tk_root, 0.15)
        top = next(w for w in tk_root.winfo_children() if w not in before and isinstance(w, tk.Toplevel))
        tops.append(top)
        return manager, top, widgets_of(top, RubyText)[0]

    yield show
    for top in tops:
        if top.winfo_exists():
            top.destroy()


def column_of(box):
    return box.master                               # the holder the reading width is set on


class TestTheColumn:
    def test_it_is_one_comfortable_line_wide_not_as_wide_as_the_window(self, opened):
        _, top, box = opened()
        measure = tkfont.Font(root=top, font=box.base_font).measure
        wanted = popup_layout.reading_width(PARAGRAPH, measure)
        assert abs(column_of(box).winfo_width() - wanted) <= 2
        assert column_of(box).winfo_width() < top.winfo_width() - 2 * top.winfo_fpixels('1i') / 96 * EW.MARGIN + 1

    def test_the_text_wraps_at_a_readable_number_of_characters(self, opened):
        _, _, box = opened()
        per_line = box.winfo_width() / (tkfont.Font(font=box.base_font).measure("n"))
        assert 40 <= per_line <= 90

    def test_it_is_centred_in_the_window(self, opened):
        _, top, box = opened()
        holder = column_of(box)
        left = holder.winfo_rootx() - top.winfo_rootx()
        right = top.winfo_rootx() + top.winfo_width() - (holder.winfo_rootx() + holder.winfo_width())
        assert abs(left - right) <= 2

    def test_a_wider_window_keeps_the_column_and_centres_it(self, opened):
        _, top, box = opened()
        before = column_of(box).winfo_width()
        top.geometry(f"{top.winfo_width() + 300}x{top.winfo_height()}")
        pump(top, 0.2)
        assert column_of(box).winfo_width() == before
        holder = column_of(box)
        left = holder.winfo_rootx() - top.winfo_rootx()
        right = top.winfo_rootx() + top.winfo_width() - (holder.winfo_rootx() + holder.winfo_width())
        assert abs(left - right) <= 2

    def test_a_narrower_window_shrinks_the_column_rather_than_clip_it(self, opened):
        _, top, box = opened()
        top.minsize(1, 1)
        top.geometry("520x600")
        pump(top, 0.2)
        holder = column_of(box)
        assert holder.winfo_rootx() + holder.winfo_width() <= top.winfo_rootx() + top.winfo_width()
        assert holder.winfo_width() < popup_layout.reading_width(
            PARAGRAPH, tkfont.Font(root=top, font=box.base_font).measure)

    def test_a_narrow_window_still_leaves_clear_space_each_side_of_the_column(self, opened):
        _, top, box = opened()
        top.minsize(1, 1)
        top.geometry("520x600")
        pump(top, 0.2)
        holder = column_of(box)
        margin = ui.get().px(EW.MARGIN)
        left = holder.winfo_rootx() - top.winfo_rootx()
        right = top.winfo_rootx() + top.winfo_width() - (holder.winfo_rootx() + holder.winfo_width())
        assert left >= margin - 2 and right >= margin - 2

    def test_japanese_gets_fewer_characters_to_a_line(self, opened):
        _, _, box = opened("私は毎日日本語を勉強しています。" * 12, "Japanese")
        per_line = box.winfo_width() / tkfont.Font(font=box.base_font).measure("日")
        assert 28 <= per_line <= 40                      # WCAG 1.4.8 asks for at most 40

    def test_the_type_is_larger_than_the_translators_panes(self, opened):
        _, top, box = opened()
        reading = ui.get().font('reading')
        assert abs(box.base_font[1]) > abs(reading[1])

    def test_the_bar_shows_only_when_the_text_is_taller_than_the_window(self, opened):
        _, _, short = opened("Hello.")
        _, _, long_ = opened(PARAGRAPH * 12)
        pump(short, 0.2)
        bars = lambda box: [b for b in widgets_of(box.master, ui.AutoScrollbar)]
        assert not bars(short)[0].shown and bars(long_)[0].shown


class TestTheWindow:
    def test_it_opens_as_wide_as_the_column_needs_and_no_wider(self, opened, tk_root):
        _, top, box = opened()
        assert top.winfo_width() < 0.8 * tk_root.winfo_screenwidth() or top.winfo_width() <= 900

    def test_it_sizes_itself_from_the_work_area(self, opened):
        _, top, _ = opened()
        assert top.sizes_from_work_area is True

    def test_it_has_a_title_naming_the_language(self, opened):
        _, top, _ = opened(language="French")
        assert top.title() == "Translation - French"
        assert any(w.cget('text') == "Translation to French" for w in widgets_of(top, ui.Label))

    def test_nothing_to_show_warns_and_opens_nothing(self, tk_root, light):
        toast = Toast()
        before = len(tk_root.winfo_children())
        EW.ExpandedTranslationWindow(tk_root, toast, Config()).show("", "English")
        assert toast.messages == [('warning', "No translation to expand")]
        assert len(tk_root.winfo_children()) == before

    def test_escape_closes_it(self, opened):
        _, top, box = opened()
        top.focus_force()
        pump(top, 0.05)
        box.event_generate('<Escape>')
        pump(top, 0.05)
        assert not top.winfo_exists()

    def test_the_text_is_read_only_and_whole(self, opened):
        _, _, box = opened()
        assert str(box.cget('state')) == 'disabled' and box.get_plain() == PARAGRAPH


class TestButtons:
    def buttons(self, top):
        return {b.cget('text'): b for b in widgets_of(top, ui.Button)}

    def test_copy_and_close_are_real_buttons(self, opened):
        _, top, _ = opened()
        buttons = self.buttons(top)
        assert set(buttons) == {'Copy', 'Close'}
        assert str(buttons['Copy'].cget('state')) != 'disabled'

    def test_copy_says_copied_once_on_itself_and_goes_back(self, opened, monkeypatch):
        copied = []
        monkeypatch.setattr(EW.pyperclip, 'copy', copied.append)
        manager, top, _ = opened()
        button = self.buttons(top)['Copy']
        button.invoke()
        assert copied == [PARAGRAPH.strip()]
        assert button.cget('text') == 'Copied' and button.icon_name == 'check-lg'
        assert manager.toast.messages == []
        top.after(EW.COPY_FEEDBACK_MS + 200)
        deadline = time.monotonic() + (EW.COPY_FEEDBACK_MS + 800) / 1000
        while button.cget('text') != 'Copy' and time.monotonic() < deadline:
            pump(top, 0.05)
        assert button.cget('text') == 'Copy' and button.icon_name == 'copy'

    def test_close_closes_it(self, opened):
        _, top, _ = opened()
        self.buttons(top)['Close'].invoke()
        assert not top.winfo_exists()

    def test_ctrl_c_with_nothing_selected_copies_everything(self, opened, monkeypatch):
        copied = []
        monkeypatch.setattr(EW.pyperclip, 'copy', copied.append)
        _, top, box = opened()
        assert top.bind('<Control-c>')
        # the bound handler, as Tk would call it
        top.event_generate('<Control-c>')
        pump(top, 0.05)
        box.focus_force()
        box.event_generate('<Control-c>')
        pump(top, 0.05)
        assert copied and copied[-1] == PARAGRAPH.strip()

    def test_ctrl_c_with_a_selection_leaves_it_to_the_selection(self, opened, monkeypatch):
        copied = []
        monkeypatch.setattr(EW.pyperclip, 'copy', copied.append)
        _, top, box = opened()
        box.tag_add('sel', '1.0', '1.10')
        box.focus_force()
        pump(top, 0.05)
        box.event_generate('<Control-c>')
        pump(top, 0.05)
        assert copied == []

    def test_ctrl_c_from_elsewhere_does_not_copy_everything_over_a_selection(self, opened, monkeypatch):
        copied = []
        monkeypatch.setattr(EW.pyperclip, 'copy', copied.append)
        _, top, box = opened()
        box.tag_add('sel', '1.0', '1.10')
        top.focus_force()
        pump(top, 0.05)
        top.event_generate('<Control-c>')
        pump(top, 0.05)
        assert copied == []

    def test_the_full_screen_button_toggles_and_says_what_it_will_do(self, opened):
        _, top, _ = opened()
        button = next(b for b in widgets_of(top, ui.IconButton))
        assert button.icon_name == 'arrows-fullscreen' and button.tooltip.text == "Full screen (F11)"
        top.attributes('-fullscreen', False)
        button.invoke()
        assert bool(top.attributes('-fullscreen')) is True
        assert button.icon_name == 'fullscreen-exit' and button.tooltip.text == "Exit full screen (F11)"
        button.invoke()
        assert bool(top.attributes('-fullscreen')) is False and button.icon_name == 'arrows-fullscreen'


class TestCounts:
    def test_plural_follows_the_number(self):
        assert EW.plural(1, 'line') == '1 line' and EW.plural(12, 'line') == '12 lines'
        assert EW.plural(1234, 'character') == '1,234 characters'

    def test_counts_are_of_the_real_text(self):
        assert EW.counts_text("One two\nthree") == f"13 characters {EW.MIDDLE_DOT} 3 words {EW.MIDDLE_DOT} 2 lines"

    def test_the_window_shows_them(self, opened):
        _, top, _ = opened("Hello there.")
        texts = [str(w.cget('text')) for w in widgets_of(top, ui.Label)]
        assert EW.counts_text("Hello there.") in texts


class TestTheme:
    def test_a_theme_change_recolours_the_text_and_keeps_it(self, opened, env):
        _, top, box = opened()
        for mode in ('dark', 'light'):
            env.manager.set_mode(mode)
            assert str(box.cget('bg')) == env.palette.bg
            assert str(box.cget('fg')) == env.palette.text
            assert box.get_plain() == PARAGRAPH
        env.manager.set_mode('light')

    def test_a_closed_window_stops_listening(self, opened, env):
        before = len(env.manager._subscribers)
        _, top, _ = opened()
        assert len(env.manager._subscribers) > before
        top.destroy()
        pump(top.master, 0.05)
        assert len(env.manager._subscribers) == before
        env.manager.set_mode('dark')
        env.manager.set_mode('light')

"""Canonical Python offsets remain correct around embedded ruby and Unicode."""
import tkinter as tk
import pytest

from src.ui.ruby_text import RubyText, linked_background


@pytest.fixture
def box(tk_root):
    window = tk.Toplevel(tk_root)
    window.geometry('500x180+10+10')
    widget = RubyText(window)
    widget.pack(fill='both', expand=True)
    yield widget
    window.destroy()


def test_offsets_around_ruby_emoji_newlines_and_combining_marks(box, tk_root):
    box.set_notation('A\U0001f642 {日本語|にほんご}e\u0301。\nLast.')
    tk_root.update()
    starts, ends, reverse, windows, text = box.prepare_plain_map()
    assert text == box.get_plain() == 'A\U0001f642 日本語e\u0301。\nLast.'
    for offset, ch in enumerate(text):
        if offset < 3 or offset >= 6:
            assert box.get_plain(starts[offset], ends[offset]) == ch
    assert box.get_plain(starts[3], ends[5]) == '日本語'
    assert reverse[starts[5]] == 3


def test_frame_and_reading_color_follow_selection_priority(box, tk_root):
    box.set_notation('A {日本語|にほんご} B')
    tk_root.update()
    starts, ends, _, _, _ = box.prepare_plain_map()
    frame = box._frames[0]
    box.highlight_plain_range([2, 5])
    assert frame.cget('bg') == linked_background()
    reading, bases = box.pair_labels(frame)
    assert reading.cget('bg') == linked_background()
    box.tag_add('sel', starts[2], ends[4])
    box._on_selection()
    assert frame.cget('bg') == box.cget('selectbackground')
    box._on_copy()
    assert box.clipboard_get() == '日本語'
    box.highlight_plain_range()
    assert frame.cget('bg') == box.cget('selectbackground')
    box.highlight_plain_range([2, 5])
    box.tag_remove('sel', '1.0', 'end')
    box._on_selection()
    assert frame.cget('bg') == linked_background()
    box.highlight_plain_range()
    assert frame.cget('bg') == box._window_colors[str(frame)][0]
    assert box.get_plain() == 'A 日本語 B'


def test_hover_hit_testing_ignores_empty_space(box, tk_root):
    box.set_plain('Hello.')
    tk_root.update()
    x, y, w, h = box.bbox('1.0')
    assert box.plain_offset_at(box.winfo_rootx() + x + 1, box.winfo_rooty() + y + h // 2) == 0
    assert box.plain_offset_at(box.winfo_rootx() + 350, box.winfo_rooty() + 100) is None


def test_highlight_hugs_short_lines_and_skips_newlines_and_edge_whitespace(box, tk_root):
    text = '  Short.  \n\n  Another line.  \n'
    box.set_plain(text)
    tk_root.update()
    box.highlight_plain_range([0, len(text)])
    ranges = box.tag_ranges('linked_reading')
    assert [box.get_plain(a, b) for a, b in zip(ranges[::2], ranges[1::2])] == [
        'Short.', 'Another line.']
    assert not box.tag_names('1.end') or 'linked_reading' not in box.tag_names('1.end')
    assert box.get_plain() == text


def test_hover_preserves_reading_and_base_foreground_colors(box, tk_root):
    box.set_notation('{日本語|にほんご} text.')
    tk_root.update()
    reading, bases = box.pair_labels(box._frames[0])
    before = reading.cget('fg'), [label.cget('fg') for label in bases]
    box.highlight_plain_range([0, len(box.get_plain())])
    assert (reading.cget('fg'), [label.cget('fg') for label in bases]) == before
    assert box.tag_cget('linked_reading', 'foreground') == ''


def test_fade_is_cancelled_by_clear_and_destroy(box, tk_root, monkeypatch):
    from dataclasses import replace
    from src.ui.theme.manager import get_manager
    manager = get_manager()
    monkeypatch.setattr(manager, 'accessibility', replace(manager.accessibility, animations=True, high_contrast=False))
    import time
    box.set_plain('Short.\n')
    box.highlight_plain_range([0, 7], animate=True)
    first = box.tag_cget('linked_reading', 'background')
    assert box._linked_animation is not None
    for _ in range(7):
        time.sleep(0.02)
        tk_root.update()
    assert box.tag_cget('linked_reading', 'background') == linked_background() != first
    box.highlight_plain_range([0, 7], animate=True)
    box.highlight_plain_range()
    assert box._linked_animation is None and not box.tag_ranges('linked_reading')
    box.highlight_plain_range([0, 7], animate=True)
    box.destroy()
    time.sleep(0.03)
    tk_root.update()
    assert box._linked_animation is None


def test_each_ruby_part_resolves_to_base_offset(box, tk_root):
    box.set_notation('{変更|へんこう}です。')
    tk_root.update()
    for part in box.linked_event_parts()[1:]:
        offset = box.plain_offset_at(part.winfo_rootx() + max(0, part.winfo_width() // 2),
                                    part.winfo_rooty() + max(0, part.winfo_height() // 2))
        assert offset == 0


def test_clear_rebuilds_map_and_plain_content_is_unchanged(box):
    box.set_notation('{日本|にほん}')
    old = box.prepare_plain_map()
    box.highlight_plain_range([0, 2])
    box.set_plain('New content.')
    new = box.prepare_plain_map()
    assert old is not new and new[-1] == 'New content.'
    assert not box._linked_frames


@pytest.mark.parametrize('length', [3000, 10000, 50000])
def test_long_plain_map_is_cached_and_highlight_does_not_rebuild(box, length, monkeypatch):
    source = 'x' * length
    box.set_plain(source)
    mapping = box.prepare_plain_map()
    monkeypatch.setattr(box, 'dump', lambda *a, **k: pytest.fail('Hover rebuilt full map'))
    for _ in range(10):
        box.highlight_plain_range([length - 10, length])
        assert box.prepare_plain_map() is mapping
    assert mapping[-1] == source


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_the_linked_highlight_is_the_theme_s_and_keeps_text_readable(box, tk_root, mode):
    from src.ui.theme import manager as theme_manager
    from src.ui.theme import tokens
    from src.ui.theme.contrast import contrast_ratio
    previous = theme_manager.get_manager()
    theme_manager.install_manager(theme_manager.ThemeManager(theme_manager.MemoryStore(mode),
                                                              detect=lambda: mode))
    try:
        palette = theme_manager.palette()
        assert linked_background() == palette.link.lower()
        box.set_plain('Hello world.')
        box.highlight_plain_range([0, 5])
        assert box.tag_cget('linked_reading', 'background') == palette.link.lower()
        assert contrast_ratio(palette.text, palette.link) >= 4.5
        assert contrast_ratio(palette.ruby, palette.link) >= 4.5
    finally:
        theme_manager.install_manager(previous)


def test_the_two_themes_highlight_differently():
    from src.ui.theme import tokens
    assert tokens.LIGHT.link != tokens.DARK.link

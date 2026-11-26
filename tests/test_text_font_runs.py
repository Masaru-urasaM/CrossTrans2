"""Preserve native document/layout semantics while bounding Windows font runs."""
import sys
import tkinter as tk
import unicodedata

import pytest

from src.core.furigana import RubySegment
from src.ui.ruby_text import RubyText
from src.ui.text_font_runs import TextFontRuns, font_run_ranges


def mainloop_until(root, ready, timeout=10000):
    done, jobs = [], []
    def poll():
        if ready():
            done.append(True)
            root.quit()
        else:
            jobs.append(root.after(20, poll))
    jobs.append(root.after(20, poll))
    jobs.append(root.after(timeout, root.quit))
    try:
        root.mainloop()
    finally:
        for job in jobs:
            root.after_cancel(job)
    assert done, 'Native layout did not settle in its actual mainloop'


def test_short_paragraphs_unbroken_words_and_non_ascii_latin_are_not_partitioned():
    assert font_run_ranges('日本語。' * 100) == []
    assert font_run_ranges(('日本語。' * 100 + '\n') * 20) == []
    assert font_run_ranges(('AV office long source sentence.\n') * 500) == []
    assert font_run_ranges('AVofficeffi' * 500) == []
    assert font_run_ranges('AV office café e\u0301 👨‍👩‍👧‍👦 source sentence. ' * 500) == []


def test_ascii_ranges_use_whole_word_boundaries():
    text = 'AV office ffi To Ta VA word-boundary sentence. ' * 100
    ranges = font_run_ranges(text)
    assert ranges
    for first, last in ranges:
        for offset in (first, last):
            if offset != len(text):
                assert text[offset - 1] == ' '
                assert text[offset].isalpha()


@pytest.mark.parametrize('unit', ['日本語。', 'か\u3099日本語。', '葛\U000e0100飾区 日本語。',
                                  '国際化 AV ffi e\u0301 👨‍👩‍👧‍👦 日本語。'])
def test_boundaries_leave_combining_variation_emoji_and_latin_context_intact(unit):
    text = unit * 500
    ranges = font_run_ranges(text)
    assert ranges
    for first, last in ranges:
        for offset in (first, last):
            if offset != len(text):
                assert unicodedata.category(text[offset])[0] != 'M'
                assert text[offset] not in ('\u200d', '\U000e0100')
                assert not text[offset].isascii() and not text[offset - 1].isascii()
                assert '\u200d' not in text[max(0, offset - 1):offset + 1]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Text font measurement path')
@pytest.mark.parametrize('width', [420, 680, 950])
@pytest.mark.parametrize('size', [-17, -25])
@pytest.mark.parametrize('family', ['Yu Gothic UI', 'Segoe UI'])
@pytest.mark.parametrize('text', [('国際化 AV ffi e\u0301 👨‍👩‍👧‍👦 日本語。葛\U000e0100飾区。' * 40),
                                 'AV office ffi To Ta VA word-boundary sentence. ' * 100],
                         ids=['mixed-unicode', 'ascii-kerning'])
def test_native_layout_matches_original_font_at_safe_boundaries(tk_root, width, size, family, text):
    top = tk.Toplevel(tk_root)
    top.geometry(f'{width}x820+0+0')
    boxes = [tk.Text(top, wrap='word', font=(family, size), height=4) for _ in range(2)]
    for box in boxes:
        box.pack(fill='both', expand=True)
        box.insert('1.0', text)
    controller = TextFontRuns(boxes[1])
    controller.refresh(text)
    try:
        assert controller.font_name is not None
        assert int(tk_root.tk.call('font', 'configure', controller.font_name, '-size')) == size
        mainloop_until(tk_root, lambda: all(box.winfo_width() == width
            and not box.tk.getboolean(box.tk.call(box._w, 'pendingsync')) for box in boxes))
        def layout(box):
            offsets = {0, 1}
            for first, last in font_run_ranges(text)[:16]:
                offsets.update(offset for edge in (first, last) for offset in (edge - 1, edge, edge + 1)
                               if 0 <= offset < len(text))
            return (box.tk.call(box._w, 'count', '-update', '-ypixels', '1.0', 'end'),
                    [box.index(f'1.0+{i} display lines') for i in range(8)],
                    [box.bbox('1.0+' + str(box.tk.call('string', 'length', text[:i])) + ' chars')
                     for i in sorted(offsets)])
        assert layout(boxes[0]) == layout(boxes[1])
        assert boxes[1].get('1.0', 'end-1c') == text
    finally:
        top.destroy()


@pytest.mark.parametrize('value', ['翻訳された文章。' * 200, 'AV office ffi source sentence. ' * 100],
                         ids=['cjk', 'ascii'])
def test_partition_preserves_editor_selection_caret_undo_and_modified_flag(tk_root, value):
    top = tk.Toplevel(tk_root)
    box = tk.Text(top, undo=True)
    box.pack()
    box.insert('1.0', value)
    box.edit_reset()
    box.edit_modified(False)
    box.mark_set('insert', '1.45')
    box.tag_add('sel', '1.4', '1.75')
    controller = TextFontRuns(box, watch_edits=True)
    controller.refresh(value)
    try:
        assert box.get('1.0', 'end-1c') == value
        assert box.index('insert') == '1.45'
        assert tuple(map(str, box.tag_ranges('sel'))) == ('1.4', '1.75')
        assert not box.edit_modified()
        box.insert('end', '追加。')
        box.edit_separator()
        controller.refresh(box.get('1.0', 'end-1c'))
        box.edit_undo()
        assert box.get('1.0', 'end-1c') == value
        box.edit_redo()
        assert box.get('1.0', 'end-1c') == value + '追加。'
    finally:
        top.destroy()


def test_font_and_tag_are_bounded_and_released_with_the_text(tk_root):
    top = tk.Toplevel(tk_root)
    box = tk.Text(top, font=('Yu Gothic UI', -17))
    box.pack()
    controller = TextFontRuns(box)
    names = set(tk_root.tk.splitlist(tk_root.tk.call('font', 'names')))
    for value in ('翻訳された文章。' * 200, '日本語。' * 600, ''):
        box.delete('1.0', 'end')
        box.insert('1.0', value)
        controller.refresh(value)
    name = controller.font_name
    assert name is not None
    assert set(tk_root.tk.splitlist(tk_root.tk.call('font', 'names'))) - names == {name}
    box.configure(font=('Yu Gothic UI', -25))
    controller.refresh('')
    assert int(tk_root.tk.call('font', 'configure', name, '-size')) == -25
    top.destroy()
    assert controller.closed and controller.font_name is None
    assert name not in tk_root.tk.splitlist(tk_root.tk.call('font', 'names'))


def test_named_font_updates_and_higher_priority_formatting_survive(tk_root):
    alias = str(tk_root.tk.call('font', 'create', 'qaTextRunNamedFont',
                                '-family', 'Yu Gothic UI', '-size', -17))
    top = tk.Toplevel(tk_root)
    box = tk.Text(top, font=alias)
    box.pack()
    value = '日本語。' * 300
    box.insert('1.0', value)
    box.tag_configure('custom', font=('Yu Gothic UI', -30, 'bold'))
    custom_font = box.tag_cget('custom', 'font')
    box.tag_add('custom', '1.0', 'end')
    controller = TextFontRuns(box)
    controller.refresh(value)
    try:
        tagged_position = str(box.tag_ranges(controller.TAG)[0])
        assert box.tag_names(tagged_position).index(controller.TAG) < box.tag_names(tagged_position).index('custom')
        tk_root.tk.call('font', 'configure', alias, '-size', -23)
        controller.refresh(value)
        assert int(tk_root.tk.call('font', 'configure', controller.font_name, '-size')) == -23
        assert box.tag_cget('custom', 'font') == custom_font
    finally:
        top.destroy()
        tk_root.tk.call('font', 'delete', alias)


def test_alias_cannot_collide_with_tkinter_font_counter(monkeypatch):
    from itertools import count
    from tkinter import font
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr(font.Font, 'counter', count(1))
    try:
        first = font.Font(root=root, family='Yu Gothic UI', size=-17)
        box = tk.Text(root, font=first)
        value = '日本語。' * 300
        box.insert('1.0', value)
        controller = TextFontRuns(box)
        controller.refresh(value)
        second = font.Font(root=root, family='Yu Gothic UI', size=-17)
        assert str(first) == 'font1' and str(second) == 'font2'
        assert controller.font_name not in (str(first), str(second))
    finally:
        root.destroy()


@pytest.mark.parametrize('plain', ['翻訳された文章。' * 200, 'AV office ffi source sentence. ' * 100],
                         ids=['cjk', 'ascii'])
def test_ruby_windows_canonical_offsets_and_link_tags_survive(tk_root, plain):
    top = tk.Toplevel(tk_root)
    box = RubyText(top)
    box.pack()
    box.insert_segments('1.0', [RubySegment('東京', 'とうきょう'), RubySegment(plain, None)])
    try:
        assert box._font_runs.font_name is not None
        assert box.get_plain() == '東京' + plain
        assert len(box._frames) == 1
        box.highlight_plain_range((120, 135))
        assert box.get_plain() == '東京' + plain
        assert box.tag_ranges('linked_reading')
        box.set_plain('short')
        assert box.get_plain() == 'short'
        assert not box.tag_ranges(TextFontRuns.TAG)
    finally:
        top.destroy()


@pytest.mark.parametrize('ruby', [False, True])
def test_live_text_scaling_keeps_alias_identical_without_changing_document(tk_root, ruby):
    from src.ui.theme.fonts import resize_widgets
    top = tk.Toplevel(tk_root)
    spec = ('Yu Gothic UI', -17)
    box = RubyText(top, base_font=spec) if ruby else tk.Text(top, font=spec)
    box.pack()
    text = '翻訳された文章。' * 200
    if ruby:
        box.set_plain(text)
        controller = box._font_runs
    else:
        box.insert('1.0', text)
        controller = TextFontRuns(box)
        controller.refresh(text)
    box.tag_add('sel', '1.3', '1.9')
    name = controller.font_name
    try:
        assert name is not None
        for previous, target, wanted in ((1.0, 1.5, -26), (1.5, 1.0, -17)):
            resize_widgets(top, target / previous, previous_scale=previous, target_scale=target)
            assert int(tk_root.tk.call('font', 'configure', name, '-size')) == wanted
            assert controller.font_name == name
            assert (box.get_plain() if ruby else box.get('1.0', 'end-1c')) == text
            assert tuple(map(str, box.tag_ranges('sel'))) == ('1.3', '1.9')
    finally:
        top.destroy()

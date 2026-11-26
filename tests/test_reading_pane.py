"""Native editable input and inline ruby preserve canonical text, selection and undo."""
import time
import tkinter as tk
from unittest.mock import MagicMock

import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from src.app import (
    READING_PANE_DEBOUNCE_MS,
    READING_PANE_PLACEHOLDER,
    READING_PANE_PLACEHOLDER_TAG,
    TranslatorApp,
)
from src.core import furigana as F
from src.core.translation import TranslationService
from tests.final_ui_helpers import design
from src.ui.ruby_text import MAX_ANNOTATE_CHARS, RubyText

HAS_PROVIDER = pytest.mark.skipif(
    not F.is_available(),
    reason="requires a reading provider (fugashi or pykakasi)"
)

JP_SENTENCE = "私は毎日日本語を勉強しています。"
JP_KANJI_ONLY = "東京都"
EN_SENTENCE = "I study Japanese every day."


class FakeConfig:
    def __init__(self, furigana_enabled=True, pane_expanded=True):
        self._furigana = furigana_enabled
        self._pane = pane_expanded
        self.saved = []

    def get_furigana_enabled(self):
        return self._furigana

    def get_furigana_reading_pane(self):
        return self._pane

    def set_furigana_reading_pane(self, expanded):
        self._pane = expanded
        self.saved.append(expanded)


@pytest.fixture(autouse=True)
def _clear_cache():
    F.clear_cache()
    yield
    F.clear_cache()


@pytest.fixture
def frame(design):
    tk_root = design.root
    window = tk.Toplevel(tk_root)
    window.geometry('700x400+0+0')
    holder = tk.Frame(window, width=700, height=400)
    holder.pack_propagate(False)
    holder.pack()
    tk_root.update_idletasks()
    yield holder
    window.destroy()


def build(tk_root, frame, config, text="", verdicts=None):
    """Build the native input and inline reading overlay on a mapped private window."""
    app = TranslatorApp.__new__(TranslatorApp)
    app.root = tk_root
    app.popup = tk_root          # the pane schedules its refresh on the popup
    app.config = config
    app.translation_service = make_service(verdicts)

    app.original_text = tk.Text(frame, height=6, wrap=tk.WORD, undo=True,
                                maxundo=-1)
    app.original_text.pack(fill=tk.X)
    if text:
        app.original_text.insert('1.0', text)
    app._create_reading_pane(frame)
    tk_root.update_idletasks()
    return app


def make_service(verdicts=None):
    """A real TranslationService with nothing but its verdict memory (J7).

    The main window reads the input's settled language through
    `source_language()`; real code, so the tests cover it, with `verdicts` as
    the texts a model has placed this session.
    """
    service = TranslationService.__new__(TranslationService)
    service.history_manager = MagicMock()
    service.history_manager.find_source_verdict.return_value = None
    for text, verdict in (verdicts or {}).items():
        service._remember_source_verdict(text, verdict)
    return service


def refresh(app, text=None):
    if text is not None:
        app.original_text.delete('1.0', 'end')
        app.original_text.insert('1.0', text)
    app._schedule_reading_refresh(delay_ms=0)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        app.root.update()
        if app._reading_pane_job is None and app._inline_reading._job is None:
            break
        time.sleep(.005)
    assert app._reading_pane_job is None and app._inline_reading._job is None
    return app._inline_reading


@pytest.mark.parametrize('text', [JP_SENTENCE, JP_KANJI_ONLY, EN_SENTENCE, '汉语学习', '',
                                      'A😀日本語', '日本語\nSecond line', 'a\t日本語\n'])
def test_inline_refresh_preserves_every_plain_character(tk_root, frame, text):
    app = build(tk_root, frame, FakeConfig(), text)
    inline = refresh(app)
    assert inline.widget is app.original_text
    assert app.original_text.get('1.0', 'end-1c') == text
    assert app.original_text.window_names() == ()
    assert not hasattr(app, '_reading_block')
    assert not hasattr(app, '_reading_toggle')


@HAS_PROVIDER
def test_annotations_are_inside_the_native_editable_input(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_SENTENCE)
    inline = refresh(app)
    assert inline.has_ruby and inline.labels
    assert str(app.original_text.cget('state')) == 'normal'
    assert all(label.master is app.original_text for label in inline.labels)
    assert ''.join(segment.base for segment in inline.segments) == JP_SENTENCE


@pytest.mark.parametrize('expanded', [False, True])
@HAS_PROVIDER
def test_old_collapse_preference_does_not_hide_inline_readings(tk_root, frame, expanded):
    app = build(tk_root, frame, FakeConfig(pane_expanded=expanded), JP_SENTENCE)
    assert refresh(app).has_ruby


@HAS_PROVIDER
def test_visibility_uses_the_canonical_furigana_setting(tk_root, frame):
    cfg = FakeConfig()
    app = build(tk_root, frame, cfg, JP_SENTENCE)
    assert refresh(app).has_ruby
    cfg._furigana = False
    inline = refresh(app)
    assert not inline.labels and not inline.has_ruby
    assert app.original_text.get('1.0', 'end-1c') == JP_SENTENCE
    cfg._furigana = True
    assert refresh(app).has_ruby


@pytest.mark.parametrize('text', [JP_SENTENCE, '😀日本語', 'Line one\n日本語\nLine three'])
def test_annotation_does_not_change_undo_redo_or_caret(tk_root, frame, text):
    app = build(tk_root, frame, FakeConfig(), text)
    box = app.original_text
    box.edit_reset()
    box.mark_set('insert', '1.2')
    box.insert('insert', 'X')
    box.edit_separator()
    caret = box.index('insert')
    expected = box.get('1.0', 'end-1c')
    refresh(app)
    assert box.index('insert') == caret
    assert box.get('1.0', 'end-1c') == expected
    box.edit_undo()
    assert box.get('1.0', 'end-1c') == text
    box.edit_redo()
    assert box.get('1.0', 'end-1c') == expected


@pytest.mark.parametrize('enabled', [False, True])
def test_refresh_preserves_selection_and_exact_plain_copy_range(tk_root, frame, enabled):
    app = build(tk_root, frame, FakeConfig(furigana_enabled=enabled), '日本語を勉強しています。')
    box = app.original_text
    box.tag_add('sel', '1.0', '1.3')
    expected = box.get('sel.first', 'sel.last')
    refresh(app)
    assert box.get('sel.first', 'sel.last') == expected
    assert box.get('1.0', 'end-1c') == '日本語を勉強しています。'


def test_auto_kanji_does_not_gain_unjustified_japanese_ruby(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_KANJI_ONLY)
    assert not refresh(app).has_ruby


@HAS_PROVIDER
def test_a_settled_japanese_verdict_enables_kanji_reading(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_KANJI_ONLY, {JP_KANJI_ONLY: 'Japanese'})
    assert refresh(app).has_ruby


@HAS_PROVIDER
def test_inline_coordinates_preserve_supplementary_unicode_offsets(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), '😀日本語です。')
    inline = refresh(app)
    assert app.original_text.get('1.0', inline._index(1)) == '😀'
    assert inline.snapshot == '😀日本語です。'


def test_stale_annotation_is_not_painted_over_new_input(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_SENTENCE)
    inline = refresh(app)
    app.original_text.delete('1.0', 'end')
    app.original_text.insert('1.0', EN_SENTENCE)
    inline.draw()
    assert all(not label.winfo_manager() for label in inline.labels)
    assert app.original_text.get('1.0', 'end-1c') == EN_SENTENCE


def test_refresh_is_debounced_and_owner_bound(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_SENTENCE)
    app._schedule_reading_refresh(500)
    first = app._reading_pane_job
    app._schedule_reading_refresh(500)
    assert app._reading_pane_job != first
    assert first not in tk_root.tk.splitlist(tk_root.tk.call('after', 'info'))


def test_destroy_cancels_pending_inline_redraw(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_SENTENCE)
    inline = refresh(app)
    inline.schedule()
    pending = inline._job
    app.original_text.destroy()
    assert inline.closed
    assert inline._job is None
    assert pending not in tk_root.tk.splitlist(tk_root.tk.call('after', 'info'))


@pytest.mark.parametrize('width', [180, 280, 700])
@HAS_PROVIDER
def test_wrapping_and_scrolling_do_not_rewrite_the_document(tk_root, frame, width):
    text = (JP_SENTENCE + '\n') * 12
    frame.configure(width=width)
    app = build(tk_root, frame, FakeConfig(), text)
    inline = refresh(app)
    app.original_text.yview_moveto(1)
    tk_root.update_idletasks()
    inline.draw()
    assert app.original_text.get('1.0', 'end-1c') == text
    assert all(label.master is app.original_text for label in inline.labels)

@HAS_PROVIDER
def test_adjacent_readings_never_overlap_each_other(tk_root, frame):
    app = build(tk_root, frame, FakeConfig(), JP_SENTENCE)
    inline = refresh(app)
    rects = [(label.winfo_x(), label.winfo_y(), label.winfo_width(), label.winfo_height())
             for label in inline.labels if label.winfo_manager()]
    assert len(rects) > 1
    for index, (x, y, width, height) in enumerate(rects):
        for other_x, other_y, other_w, other_h in rects[index+1:]:
            assert x+width <= other_x or other_x+other_w <= x or y+height <= other_y or other_y+other_h <= y


def test_resize_uses_cached_readings_and_only_visible_geometry(tk_root, frame, monkeypatch):
    from src.ui.inline_reading import InlineReading
    segments = tuple(F.RubySegment('日本語\n', 'にほんご') for _ in range(500))
    text = ''.join(segment.base for segment in segments)
    annotation_calls = []
    monkeypatch.setattr(F, 'annotate', lambda value, hint: annotation_calls.append(value) or segments)
    box = tk.Text(frame, height=6, width=20, wrap='word', undo=True)
    box.pack(fill='both', expand=True)
    box.insert('1.0', text)
    inline = InlineReading(box)
    inline.update(text, 'Japanese', True)
    tk_root.update_idletasks()
    before = box.get('1.0', 'end-1c')
    measured = []
    original_bbox = box.bbox
    monkeypatch.setattr(box, 'bbox', lambda index: measured.append(index) or original_bbox(index))
    monkeypatch.setattr(inline.ruby_font, 'measure',
                        lambda value: pytest.fail('Resize measured an unchanged ruby font again'))
    box.yview_moveto(.8)
    frame.configure(width=260)
    tk_root.update_idletasks()
    measured.clear()
    inline.draw()
    assert annotation_calls == [text]
    assert len(measured) < 100
    assert len(inline.labels) < 50
    assert any(label.winfo_manager() for label in inline.labels)
    assert box.get('1.0', 'end-1c') == before


@pytest.mark.parametrize('prefix', ['😀', 'A😀\n', '\t😀e\u0301'])
def test_cached_indices_remain_exact_after_wrap_and_scroll(tk_root, frame, monkeypatch, prefix):
    from src.ui.inline_reading import InlineReading
    segments = (F.RubySegment(prefix, ''), F.RubySegment('日本語', 'にほんご'),
                F.RubySegment('\n' + 'end\n' * 60, ''))
    text = ''.join(segment.base for segment in segments)
    monkeypatch.setattr(F, 'annotate', lambda *_args: segments)
    box = tk.Text(frame, height=5, wrap='word', undo=True)
    box.pack(fill='both', expand=True)
    box.insert('1.0', text)
    inline = InlineReading(box)
    inline.update(text, 'Japanese', True)
    tk_root.update_idletasks()
    start = inline._index(len(prefix))
    assert box.get('1.0', start) == prefix
    box.yview_moveto(1)
    frame.configure(width=190)
    tk_root.update_idletasks()
    box.yview_moveto(0)
    tk_root.update_idletasks()
    inline.draw()
    assert box.get('1.0', start) == prefix
    visible = [label for label in inline.labels if label.winfo_manager()]
    assert visible and visible[0].source_index == start

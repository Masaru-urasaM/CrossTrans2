"""
Tests for the Translator's attachment area (TODO.md U7): what can be attached, the tiles, the drop
zone and the pure helpers. There were none: the 644-line widget this replaced had no tests.
"""
import os
import time
import tkinter as tk
from types import SimpleNamespace

import pytest
from PIL import Image

from src.ui import attachments as A
from src.ui import components as ui
from src.ui.components import styles

from tests.test_components import env, holder, light  # noqa: F401  (shared fixtures)


class FakeConfig:
    """Config.get with the two upload switches."""

    def __init__(self, vision=True, files=True):
        self.values = {'vision_enabled': vision, 'file_processing_enabled': files}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_api_keys(self):
        return []


@pytest.fixture
def files(tmp_path):
    image = tmp_path / 'menu.png'
    Image.new('RGB', (80, 60), (10, 120, 200)).save(image)
    notes = tmp_path / 'notes.txt'
    notes.write_text('Meeting notes', encoding='utf-8')
    movie = tmp_path / 'clip.mp4'
    movie.write_bytes(b'\x00\x00\x00\x18ftypmp42\x00\x00')
    return SimpleNamespace(image=str(image), notes=str(notes), movie=str(movie), dir=tmp_path)


@pytest.fixture
def area(holder, light, files):
    def build(vision=True, doc=True, **kwargs):
        widget = A.AttachmentArea(holder, FakeConfig(vision, doc), **kwargs)
        widget.pack(fill='x')
        holder.pack(fill='x')
        holder.update()
        return widget
    return build


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch):
    """A refusal that is not asked to show itself must never open a native box on the screen."""
    from tkinter import messagebox
    shown = []
    for name in ('showwarning', 'showinfo', 'showerror'):
        monkeypatch.setattr(messagebox, name, lambda *args, _n=name, **kw: shown.append((_n, args)))
    return shown


# --------------------------------------------------------------------------- #
# The pure helpers
# --------------------------------------------------------------------------- #
class TestParseDroppedPaths:
    def test_plain_paths_split_on_spaces(self):
        assert A.parse_dropped_paths('C:/a.png C:/b.txt') == ['C:/a.png', 'C:/b.txt']

    def test_braces_hold_a_path_with_spaces(self):
        assert A.parse_dropped_paths('{C:/My Files/a b.txt} C:/c.png') == ['C:/My Files/a b.txt', 'C:/c.png']

    def test_two_braced_paths(self):
        assert A.parse_dropped_paths('{C:/a b.txt} {C:/c d.png}') == ['C:/a b.txt', 'C:/c d.png']

    def test_nothing(self):
        assert A.parse_dropped_paths('') == [] and A.parse_dropped_paths(None) == []

    def test_a_single_braced_path(self):
        assert A.parse_dropped_paths('{C:/only one.txt}') == ['C:/only one.txt']


class TestNames:
    def test_a_short_name_is_unchanged(self):
        assert A.shorten('notes.txt') == 'notes.txt'

    def test_a_long_name_ends_in_an_ellipsis_and_keeps_its_extension(self):
        text = A.shorten('a-very-long-file-name-for-a-meeting.docx')
        assert len(text) <= A.NAME_CHARS and text.endswith(A.ELLIPSIS + '.docx')

    def test_kind_and_size(self, files):
        assert A.kind_text(files.notes, False) == f'TXT {A.KIND_SEPARATOR} 13 B'
        assert A.kind_text(files.image, True).startswith(f'Image {A.KIND_SEPARATOR} ')

    def test_sizes_scale(self, tmp_path):
        big = tmp_path / 'big.pdf'
        big.write_bytes(b'0' * (3 * 1024 * 1024))
        mid = tmp_path / 'mid.pdf'
        mid.write_bytes(b'0' * 4096)
        assert 'MB' in A.kind_text(str(big), False) and A.kind_text(str(mid), False).endswith('4 KB')

    def test_a_missing_file_still_has_a_kind(self):
        assert A.kind_text('nowhere.srt', False) == 'SRT'


# --------------------------------------------------------------------------- #
# Adding
# --------------------------------------------------------------------------- #
class TestAdding:
    def test_an_image_and_a_document_are_added_in_order(self, area, files):
        widget = area()
        assert widget.add_file(files.image) is True and widget.add_file(files.notes) is True
        assert [a['type'] for a in widget.get_attachments()] == ['image', 'file']
        assert [a['path'] for a in widget.get_attachments()] == [files.image, files.notes]

    def test_each_gets_a_tile(self, area, files):
        widget = area()
        widget.add_file(files.image)
        widget.add_file(files.notes)
        assert set(widget.tiles) == {files.image, files.notes}

    def test_the_same_file_is_not_added_twice(self, area, files):
        widget = area()
        widget.add_file(files.notes)
        assert widget.add_file(files.notes) is True
        assert len(widget.get_attachments()) == 1 and len(widget.tiles) == 1

    def test_a_missing_file_is_refused(self, area):
        assert area().add_file('nowhere.png') is False

    def test_an_unsupported_type_is_refused(self, area, files):
        widget = area()
        assert widget.add_file(files.movie) is False and widget.get_attachments() == []

    def test_images_need_the_image_switch(self, area, files):
        widget = area(vision=False)
        assert widget.add_file(files.image) is False and widget.add_file(files.notes) is True

    def test_documents_need_the_file_switch(self, area, files):
        widget = area(doc=False)
        assert widget.add_file(files.notes) is False and widget.add_file(files.image) is True

    def test_a_refusal_speaks_only_when_asked_to(self, area, files, no_dialogs):
        widget = area(vision=False)
        widget.add_file(files.image)
        assert no_dialogs == []
        widget.add_file(files.image, show_warning=True)
        assert [kind for kind, _ in no_dialogs] == ['showwarning']

    def test_the_owner_hears_about_a_change(self, area, files):
        seen = []
        widget = area(on_change=lambda: seen.append(1))
        widget.add_file(files.notes)
        widget.clear()
        assert seen == [1, 1]

    def test_a_picture_that_is_not_one_still_gets_a_tile(self, area, tmp_path):
        broken = tmp_path / 'broken.png'
        broken.write_bytes(b'not a picture')
        widget = area()
        assert widget.add_file(str(broken)) is True and str(broken) in widget.tiles


# --------------------------------------------------------------------------- #
# Tiles and the strip
# --------------------------------------------------------------------------- #
def labels(widget, path):
    found = []

    def walk(item):
        for child in item.winfo_children():
            try:
                found.append(str(child.cget('text')))
            except tk.TclError:
                pass
            walk(child)
    walk(widget.tiles[path])
    return found


class TestTiles:
    def test_a_tile_names_the_file_and_what_it_is(self, area, files):
        widget = area()
        widget.add_file(files.notes)
        texts = labels(widget, files.notes)
        assert 'notes.txt' in texts and f'TXT {A.KIND_SEPARATOR} 13 B' in texts

    def test_no_emoji_anywhere_on_it(self, area, files):
        widget = area()
        widget.add_file(files.image)
        widget.add_file(files.notes)
        for path in widget.tiles:
            for text in labels(widget, path):
                assert not any(ord(ch) >= 0x2600 for ch in text), text

    def test_an_image_shows_a_thumbnail_and_is_kept_alive(self, area, files):
        widget = area()
        widget.add_file(files.image)
        assert len(widget.thumbnails) == 1

    def test_a_document_shows_its_kind_of_icon(self, area, files):
        widget = area()
        widget.add_file(files.notes)
        assert A.FILE_ICONS['.txt'] == 'file-earmark-text'
        assert all(ext in A.FILE_ICONS for ext in ('.txt', '.docx', '.pdf', '.srt'))

    def test_remove_takes_one_tile_and_its_attachment(self, area, files):
        widget = area()
        widget.add_file(files.image)
        widget.add_file(files.notes)
        widget._remove_item(files.image)
        assert [a['path'] for a in widget.get_attachments()] == [files.notes]
        assert set(widget.tiles) == {files.notes}

    def test_every_tile_has_a_remove_button_naming_its_file(self, area, files):
        widget = area()
        widget.add_file(files.notes)
        removers = []

        def walk(item):
            for child in item.winfo_children():
                if isinstance(child, ui.IconButton):
                    removers.append(child)
                walk(child)
        walk(widget.tiles[files.notes])
        assert len(removers) == 1 and removers[0].label == 'Remove notes.txt'
        removers[0].invoke()
        assert widget.get_attachments() == []

    def test_clear_all_empties_it(self, area, files):
        widget = area()
        widget.add_file(files.image)
        widget.add_file(files.notes)
        widget.clear()
        assert widget.get_attachments() == [] and widget.tiles == {} and widget.thumbnails == []

    def test_double_click_opens_the_file(self, area, files, monkeypatch):
        opened = []
        monkeypatch.setattr(A.os, 'startfile', opened.append, raising=False)
        widget = area()
        widget.add_file(files.notes)
        widget._open_file(files.notes)
        assert opened == [files.notes]

    def test_a_file_that_went_away_says_so_instead_of_failing(self, area, files, no_dialogs):
        widget = area()
        widget._open_file(os.path.join(str(files.dir), 'gone.txt'))
        assert [kind for kind, _ in no_dialogs] == ['showwarning']


class TestTheStrip:
    def test_clear_all_and_the_tile_row_exist_only_with_something_attached(self, area, files):
        widget = area()
        assert not widget.clear_btn.winfo_manager() and not widget.tile_row.winfo_manager()
        widget.add_file(files.notes)
        assert widget.clear_btn.winfo_manager() and widget.tile_row.winfo_manager()
        widget.clear()
        assert not widget.clear_btn.winfo_manager() and not widget.tile_row.winfo_manager()

    def test_it_says_what_it_takes(self, area):
        widget = area()
        assert widget.hint.cget('text') == 'Drop files here'
        assert widget.add_btn.cget('text') == 'Attach file'
        assert widget.add_btn.icon_name == 'image'

    def test_tiles_wrap_into_rows_when_the_strip_is_narrow(self, tk_root, light, files):
        # A mapped window: an unmapped widget has no width to wrap at
        top = tk.Toplevel(tk_root)
        try:
            top.geometry('1000x300+30+30')
            widget = A.AttachmentArea(top, FakeConfig())
            widget.pack(fill='x')
            for name in ('a.txt', 'b.txt', 'c.txt', 'd.txt'):
                path = files.dir / name
                path.write_text('x', encoding='utf-8')
                widget.add_file(str(path))
            top.update()
            top.update()
            wide_rows = {int(t.grid_info()['row']) for t in widget.tiles.values()}
            top.geometry('330x300')
            top.update()
            top.update()
            narrow_rows = {int(t.grid_info()['row']) for t in widget.tiles.values()}
            assert len(wide_rows) == 1 and len(narrow_rows) > 1
        finally:
            top.destroy()

    def test_reflow_is_idempotent(self, area, files):
        widget = area()
        widget.add_file(files.notes)
        widget.add_file(files.image)
        widget._reflow()
        first = widget._layout_signature
        widget._reflow()
        assert widget._layout_signature == first


# --------------------------------------------------------------------------- #
# Dropping
# --------------------------------------------------------------------------- #
def drop(widget, *paths):
    return widget._on_drop(SimpleNamespace(data=' '.join('{%s}' % p for p in paths), action='copy'))


class TestDropZone:
    def test_a_drop_adds_what_is_readable(self, area, files):
        widget = area()
        drop(widget, files.image, files.notes)
        assert len(widget.get_attachments()) == 2

    def test_a_drop_ringed_the_card_and_leaves_it_plain_again(self, area):
        widget = area()
        plain = str(widget.zone.cget('style'))
        widget._on_drag_enter(SimpleNamespace(action='copy'))
        ringed = str(widget.zone.cget('style'))
        assert ringed != plain and ringed == styles.name('drop', widget.plane, 'TFrame')
        widget._on_drag_leave(SimpleNamespace(action='copy'))
        assert str(widget.zone.cget('style')) == plain

    def test_dropping_ends_the_ring(self, area, files):
        widget = area()
        widget._on_drag_enter(SimpleNamespace(action='copy'))
        drop(widget, files.notes)
        assert str(widget.zone.cget('style')) == styles.name('card', widget.plane, 'TFrame')

    def test_what_the_models_cannot_read_is_left_out_with_one_note(self, area, files, no_dialogs):
        widget = area(vision=False)
        drop(widget, files.image, files.movie, files.notes)
        assert [a['path'] for a in widget.get_attachments()] == [files.notes]
        assert len(no_dialogs) == 1
        message = no_dialogs[0][1][1]
        assert 'menu.png' in message and 'clip.mp4' in message

    def test_a_drop_that_is_all_welcome_is_silent(self, area, files, no_dialogs):
        drop(area(), files.image, files.notes)
        assert no_dialogs == []

    def test_an_empty_drop_does_nothing(self, area):
        widget = area()
        widget._on_drop(SimpleNamespace(data='', action='copy'))
        assert widget.get_attachments() == []

    def test_paths_that_do_not_exist_are_skipped_quietly(self, area, no_dialogs):
        widget = area()
        drop(widget, 'C:/nowhere/a.png')
        assert widget.get_attachments() == [] and no_dialogs == []

    def test_the_rejection_note_lists_three_and_counts_the_rest(self):
        note = A.AttachmentArea._rejection_message(['a', 'b', 'c', 'd', 'e'], [], [])
        assert note.count('\n') == 4 and '... and 2 more' in note

    def test_no_rejections_no_note(self):
        assert A.AttachmentArea._rejection_message([], [], []) == ''


class TestMenu:
    def test_the_attach_menu_offers_what_is_on(self, area, monkeypatch):
        posted = []
        monkeypatch.setattr(tk.Menu, 'tk_popup', lambda self, x, y: posted.append(
            [self.entrycget(i, 'label') for i in range(self.index('end') + 1)
             if self.type(i) == 'command']))
        area()._show_add_menu()
        assert posted == [['Images...', 'Text files and documents...']]

    def test_with_nothing_on_it_points_to_settings(self, area, monkeypatch):
        posted = []
        monkeypatch.setattr(tk.Menu, 'tk_popup', lambda self, x, y: posted.append(
            [self.entrycget(i, 'label') for i in range(self.index('end') + 1)
             if self.type(i) == 'command']))
        area(vision=False, doc=False)._show_add_menu()
        assert posted == [['No upload features are on yet', 'Open Settings']]

    def test_open_settings_asks_the_app_for_the_api_key_tab(self, area):
        asked = []
        widget = area(on_open_settings_tab=asked.append)
        widget._open_settings_hint()
        assert asked == ['API Key']

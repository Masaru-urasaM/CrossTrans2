"""
Tests for the CrossTrans mark, the icon files and the tray (TODO.md U5, DoD5; V9, V16, V17).

The old tray icon was the 488 x 511 logo squashed to 64 x 64 and the ICO's frames were not square;
every window showed Tk's feather. These pin the replacements: a square mark drawn at the size the
system asks for, an .ico of square frames, a separator that is a separator, and one icon call.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.ui.theme import brand, windows

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import make_icons  # noqa: E402
import ui_gallery  # noqa: E402


class TestTheMark:
    @pytest.mark.parametrize('size', brand.ICO_SIZES)
    def test_it_is_square_rgba_at_every_icon_size(self, size):
        image = brand.render_mark(size)
        assert image.size == (size, size) and image.mode == 'RGBA'

    def test_the_plate_has_rounded_transparent_corners_and_a_solid_middle(self):
        image = brand.render_mark(96)
        assert image.getpixel((0, 0))[3] == 0 and image.getpixel((95, 95))[3] == 0
        assert image.getpixel((48, 4))[3] == 255 or image.getpixel((48, 8))[3] == 255

    def test_without_the_plate_only_the_x_is_drawn(self):
        image = brand.render_mark(96, plate=False)
        assert image.getpixel((2, 48))[3] == 0                    # nothing at the edge
        assert any(image.getpixel((x, 48))[3] == 255 for x in range(96))

    def test_both_strokes_are_there_blue_on_the_left_amber_on_the_right(self):
        image = brand.render_mark(256)
        left_top = image.getpixel((70, 70))[:3]                   # the blue stroke's upper end
        right_top = image.getpixel((186, 70))[:3]                 # the amber stroke's upper end
        assert left_top[2] > left_top[0] and right_top[0] > right_top[2]

    def test_the_x_gets_bolder_as_the_icon_shrinks(self):
        widths = [brand.stroke_for(size)[0] for size in (16, 24, 32, 48, 96)]
        assert widths == sorted(widths, reverse=True) and widths[0] > widths[-1]

    def test_the_same_drawing_at_every_size_is_not_a_scaled_copy(self):
        small = brand.render_mark(16)
        scaled = brand.render_mark(256).resize((16, 16), Image.Resampling.LANCZOS)
        assert small.tobytes() != scaled.tobytes()

    def test_the_mark_reads_on_a_light_and_a_dark_taskbar(self):
        # the navy plate is opaque at the middle whatever is behind it
        image = brand.render_mark(24)
        assert image.getpixel((12, 3))[3] > 200 or image.getpixel((3, 12))[3] > 200


class TestIcoFile:
    def test_a_built_icon_has_every_frame_and_each_is_square(self, tmp_path):
        path = str(tmp_path / 'a.ico')
        assert brand.build_ico(path) == sorted(brand.ICO_SIZES)
        assert make_icons.ico_problems(path) == []

    def test_each_frame_is_drawn_at_its_own_size(self, tmp_path):
        path = str(tmp_path / 'a.ico')
        brand.build_ico(path)
        with Image.open(path) as icon:
            for size in (16, 24, 32, 48):
                icon.size = (size, size)
                frame = icon.convert('RGBA')
                reference = brand.render_mark(size)
                assert frame.size == reference.size
                difference = sum(abs(a - b) for a, b in zip(frame.tobytes(), reference.tobytes()))
                assert difference / len(reference.tobytes()) < 12, size    # a copy of the 256 frame would be far off

    def test_the_tray_and_title_bar_sizes_are_all_there(self):
        assert set(brand.TRAY_SIZES) <= set(brand.ICO_SIZES)
        assert {16, 20, 24, 32, 48, 256} <= set(brand.ICO_SIZES)

    def test_a_missing_file_is_reported(self, tmp_path):
        assert 'missing' in make_icons.ico_problems(str(tmp_path / 'nope.ico'))[0]

    def test_an_icon_lacking_sizes_is_reported(self, tmp_path):
        path = str(tmp_path / 'few.ico')
        brand.build_ico(path, sizes=(16, 32))
        problems = make_icons.ico_problems(path)
        assert problems and 'lacks the frame sizes' in problems[0]

    def test_the_icons_in_the_repository_are_complete(self):
        assert make_icons.check() == 0
        for target in make_icons.TARGETS:
            assert os.path.exists(os.path.join(REPO, target))

    def test_the_picture_of_the_mark_is_square(self):
        with Image.open(os.path.join(REPO, make_icons.PNG)) as picture:
            assert picture.width == picture.height == 256


class TestTray:
    def make(self):
        from src.ui.tray import TrayManager
        config = MagicMock()
        config.get_all_hotkeys.return_value = {'English': 'win+alt+e'}
        config.has_any_vision_capable.return_value = True
        config.get_screenshot_hotkey.return_value = 'win+alt+s'
        config.get_fix_grammar_hotkey_enabled.return_value = True
        config.get_fix_grammar_hotkey.return_value = 'win+alt+g'
        return TrayManager(config)

    def test_the_tray_image_is_the_size_the_display_uses_and_square(self):
        tray = self.make()
        image = tray._create_icon_image()
        assert image.width == image.height == brand.system_tray_size()

    def test_the_system_tray_size_is_a_plausible_icon_size(self):
        assert 16 <= brand.system_tray_size() <= 64

    def test_a_given_size_is_respected(self):
        assert brand.tray_image(24).size == (24, 24)

    def test_the_unknown_size_falls_back_to_a_generous_one(self):
        assert brand.tray_image().width >= 16

    def test_separators_are_separators_not_dashes(self):
        from pystray import Menu
        items = self.make()._build_menu_items()
        assert items.count(Menu.SEPARATOR) == 2
        assert not [i for i in items if getattr(i, 'text', '') and '─' in i.text]

    def test_the_menu_keeps_its_commands(self):
        texts = [getattr(i, 'text', None) for i in self.make()._build_menu_items()]
        assert 'Open Translator' in texts and 'Settings' in texts and 'Quit' in texts

    def test_a_failure_to_draw_falls_back_to_a_badge(self, monkeypatch):
        monkeypatch.setattr(brand, 'tray_image', lambda size=0: (_ for _ in ()).throw(RuntimeError('boom')))
        image = self.make()._create_icon_image()
        assert image.size == (64, 64)


class TestWindowIcon:
    def test_one_call_gives_every_window_the_icon(self, tk_root):
        import tkinter as tk
        ico = os.path.join(REPO, 'src', 'assets', 'CrossTrans.ico')
        assert windows.set_default_icon(tk_root, ico) is True
        window = tk.Toplevel(tk_root)
        try:
            window.update()
            assert ui_gallery.has_window_icon(int(window.wm_frame(), 16))
        finally:
            window.destroy()

    def test_the_application_makes_that_call(self):
        with open(os.path.join(REPO, 'src', 'app.py'), encoding='utf-8') as fh:
            source = fh.read()
        assert 'set_default_icon' in source and 'CrossTrans.ico' in source

    def test_the_spec_points_at_the_icon_the_build_checks(self):
        with open(os.path.join(REPO, 'CrossTrans.spec'), encoding='utf-8') as fh:
            assert "icon='CrossTrans.ico'" in fh.read()

    def test_the_build_script_verifies_the_icons_without_a_parenthesised_block(self):
        with open(os.path.join(REPO, 'build_exe.bat'), encoding='utf-8') as fh:
            script = fh.read()
        assert 'make_icons.py --check' in script and 'goto :icons_ok' in script
        assert ':icons_failed' in script


class TestNoVietnamese:
    def test_the_new_code_holds_none(self):
        import re
        letters = re.compile('[Ạ-ỹĐđƠơƯư]')
        for path in ('src/ui/theme/brand.py', 'tools/make_icons.py'):
            with open(os.path.join(REPO, path), encoding='utf-8') as fh:
                assert not letters.search(fh.read()), path

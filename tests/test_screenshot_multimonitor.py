"""Screenshot overlay and Tk geometry on multi-monitor desktops, including monitors left of / above the primary.

The developer's layout: MON1 (0,0)-(1920,1080) primary, MON2 (-1080,-842)-(0,1078) portrait to the left,
MON3 (0,-1080)-(1920,0) above. Virtual screen: left=-1080, top=-1080, width=3000, height=2160.
"""
import re
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.core import screenshot
from src.ui import components as ui
from src.ui import toast as toast_module
from src.ui.theme import windows
from tests.final_ui_helpers import design, pump  # noqa: F401

SRC = Path(__file__).resolve().parents[1] / 'src'
VIRTUAL = (-1080, -1080, 3000, 2160)
MON1, MON2, MON3 = (0, 0, 1920, 1080), (-1080, -842, 0, 1078), (0, -1080, 1920, 0)
MONITORS = (MON1, MON2, MON3)
LAYOUTS = [
    VIRTUAL,                    # the developer's L-shape
    (-1920, 0, 3840, 1080),     # one monitor to the left
    (0, -1080, 1920, 2160),     # one monitor above
    (0, 0, 3840, 1080),         # positive only
]


def monitor_at(x, y):
    for rect in MONITORS:
        if rect[0] <= x < rect[2] and rect[1] <= y < rect[3]:
            return rect
    return None


@pytest.mark.parametrize('left,top', [(-1080, -1080), (-1080, 0), (0, -1080), (0, 0), (40, 30)])
def test_geometry_strings_use_the_explicit_offset_form(left, top):
    text = windows.tk_geometry(left, top, 3000, 2160)
    assert text == f'3000x2160+{left}+{top}'
    assert not re.search(r'\d-\d', text)         # never "3000x2160-1080-1080": that means "from the right/bottom"
    assert windows.tk_geometry(left, top) == f'+{left}+{top}'
    assert windows.parse_geometry(text) == (3000, 2160, left, top)


@pytest.mark.parametrize('bad', ['', 'abc', '100x50', '100x50-5+5', '+1+x'])
def test_parse_geometry_rejects_other_shapes(bad):
    assert windows.parse_geometry(bad) is None


def test_tk_reads_the_negative_offset_form_as_a_negative_position(design):
    top = tk.Toplevel(design.root)
    try:
        top.geometry(windows.tk_geometry(-60, -40, 200, 100))
        top.update_idletasks()
        assert windows.parse_geometry(top.geometry())[2:] == (-60, -40)
    finally:
        top.destroy()


def test_no_ui_code_formats_a_position_with_a_sign_format_spec():
    offenders = [f'{path.relative_to(SRC)}:{number}'
                 for path in SRC.rglob('*.py')
                 for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
                 if re.search(r'\{[^{}]*:\+d\}', line) and 'geometry' in line]
    assert offenders == []


class FakeToast:
    def __init__(self, geometry):
        self.value, self.calls = geometry, []

    def winfo_exists(self):
        return True

    def geometry(self, value=None):
        if value is None:
            return self.value
        self.calls.append(value)


@pytest.mark.parametrize('before,after', [
    ('240x80+-1080+10', '240x80+-1072+10'),
    ('+-1080+-20', '+-1072+-20'),
    ('240x80+300+-40', '240x80+308+-40'),
])
def test_toast_shake_keeps_negative_monitor_coordinates(monkeypatch, before, after):
    manager = toast_module.ToastManager.__new__(toast_module.ToastManager)
    monkeypatch.setattr(manager, '_after', lambda *args: None)
    monkeypatch.setattr(ui, 'get', lambda: SimpleNamespace(manager=SimpleNamespace(animations_enabled=True)))
    fake = FakeToast(before)
    manager._shake_toast(fake, 0)
    assert fake.calls == [after]


@pytest.mark.parametrize('layout', LAYOUTS)
@pytest.mark.parametrize('rect', MONITORS)
def test_chip_bounds_stay_inside_the_pointer_monitor(layout, rect):
    left, top, width, height = layout
    point = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    bounds = screenshot.monitor_bounds(point, (left, top), (width, height), (width, height), finder=monitor_at)
    x, y = screenshot.instruction_position(bounds, 24)
    assert 0 <= bounds[0] < bounds[2] <= width and 0 <= bounds[1] < bounds[3] <= height
    assert y == bounds[1] + 24
    if left <= point[0] < left + width and top <= point[1] < top + height:
        assert bounds[0] <= x < bounds[2]            # a monitor that is part of this desktop: chip is on it


@pytest.mark.parametrize('rect', MONITORS)
def test_the_instruction_chip_never_lands_in_the_l_shaped_dead_space(rect):
    point = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    bounds = screenshot.monitor_bounds(point, VIRTUAL[:2], VIRTUAL[2:], VIRTUAL[2:], finder=monitor_at)
    x, y = screenshot.instruction_position(bounds, 24)
    virtual_x, virtual_y = x + VIRTUAL[0], y + VIRTUAL[1]
    assert monitor_at(virtual_x, virtual_y) == rect


def test_chip_falls_back_to_the_whole_canvas_without_monitor_information(monkeypatch):
    monkeypatch.setattr(screenshot, 'primary_monitor_rect', lambda: None)
    assert screenshot.monitor_bounds((5, 5), (-1080, -1080), (3000, 2160), (3000, 2160),
                                     finder=lambda x, y: None) == (0, 0, 3000, 2160)


def test_chip_falls_back_to_the_primary_monitor_when_the_finder_cannot_answer(monkeypatch):
    monkeypatch.setattr(screenshot, 'primary_monitor_rect', lambda: MON1)
    assert screenshot.monitor_bounds((5, 5), (-1080, -1080), (3000, 2160), (3000, 2160),
                                     finder=lambda x, y: None) == (1080, 1080, 3000, 2160)


def test_dimension_chip_is_clamped_into_the_pointer_monitor():
    bounds = (1080, 1080, 3000, 2160)
    assert screenshot.dimensions_position((2990, 2150), (100, 30), 12, bounds) == (2888, 2118)
    assert screenshot.dimensions_position((1090, 1090), (100, 30), 12, bounds) == (1102, 1102)
    assert screenshot.dimensions_position((0, 0), (100, 30), 12, bounds) == (1092, 1092)


def test_scale_box_is_identity_for_equal_sizes_and_proportional_otherwise():
    assert screenshot.scale_box((10, 20, 30, 40), (100, 80), (100, 80)) == (10, 20, 30, 40)
    assert screenshot.scale_box((10, 20, 30, 40), (100, 80), (200, 160)) == (20, 40, 60, 80)
    assert screenshot.scale_box((0, 0, 100, 80), (100, 80), (150, 60)) == (0, 0, 150, 60)


class RecordingToplevel(tk.Toplevel):
    geometries = []

    def geometry(self, value=None):
        if value is not None:
            self.geometries.append(value)
        return super().geometry(value)


@pytest.mark.parametrize('layout', LAYOUTS)
def test_overlay_is_placed_exactly_over_the_virtual_screen(design, monkeypatch, layout):
    left, top, width, height = layout
    placed = []
    RecordingToplevel.geometries = []
    pointer = ((MON3[0] + MON3[2]) // 2, (MON3[1] + MON3[3]) // 2) if layout == VIRTUAL else (left + 10, top + 10)
    monkeypatch.setattr(screenshot.ImageGrab, 'grab', lambda all_screens=True: Image.new('RGB', (width, height), 'gray'))
    monkeypatch.setattr(screenshot, 'get_virtual_screen_bounds', lambda: layout)
    monkeypatch.setattr(screenshot.tk, 'Toplevel', RecordingToplevel)
    monkeypatch.setattr(screenshot.windows, 'place_exact',
                        lambda window, *rect: placed.append(rect) or True)
    monkeypatch.setattr(screenshot.ScreenshotCapture, '_pointer', lambda self: pointer)
    monkeypatch.setattr(screenshot.windows, 'monitor_rect', monitor_at)
    capture = screenshot.ScreenshotCapture(design.root)
    capture.capture_region(lambda path: None)
    try:
        assert RecordingToplevel.geometries[0] == f'{width}x{height}+{left}+{top}'
        assert placed == [(left, top, width, height)]
        x, y = capture.canvas.coords(capture._instruction_item)
        assert 0 <= x <= width and 0 <= y <= height
        if layout == VIRTUAL:
            assert monitor_at(x + left, y + top) == MON3
    finally:
        capture._close()


def striped_desktop(layout):
    """A capture whose pixel colour encodes which monitor it came from."""
    left, top, width, height = layout
    image = Image.new('RGB', (width, height), (0, 0, 0))
    for colour, rect in zip(((255, 0, 0), (0, 255, 0), (0, 0, 255)), MONITORS):
        image.paste(colour, (rect[0] - left, rect[1] - top, rect[2] - left, rect[3] - top))
    return image


def drag(capture, start, end):
    done = []
    capture.callback = done.append
    capture.start_x, capture.start_y = start
    capture._dragging = True
    capture._on_release(SimpleNamespace(x_root=end[0], y_root=end[1]))
    assert len(done) == 1 and done[0]
    path = Path(done[0])
    try:
        with Image.open(path) as shot:
            return shot.size, shot.convert('RGB').getpixel((shot.width // 2, shot.height // 2))
    finally:
        path.unlink()


@pytest.mark.parametrize('start,end,size,colour', [
    ((100, 100), (400, 300), (300, 200), (255, 0, 0)),               # MON1
    ((-900, -500), (-300, 100), (600, 600), (0, 255, 0)),            # MON2 (negative x and y)
    ((300, -900), (800, -400), (500, 500), (0, 0, 255)),             # MON3 (negative y)
    ((800, -300), (100, -700), (700, 400), (0, 0, 255)),             # dragged up and to the left
])
def test_the_crop_matches_the_monitor_under_the_drag(start, end, size, colour):
    capture = screenshot.ScreenshotCapture()
    capture.original_image = striped_desktop(VIRTUAL)
    capture._vscreen_left, capture._vscreen_top = VIRTUAL[:2]
    capture._view_size = VIRTUAL[2:]
    assert drag(capture, start, end) == (size, colour)


def test_pointer_positions_are_measured_from_the_real_canvas_origin():
    capture = screenshot.ScreenshotCapture()
    capture.original_image = striped_desktop(VIRTUAL)
    capture._vscreen_left, capture._vscreen_top = VIRTUAL[:2]
    capture._view_size = VIRTUAL[2:]
    capture.canvas = SimpleNamespace(winfo_ismapped=lambda: True, winfo_rootx=lambda: -1080 + 50,
                                     winfo_rooty=lambda: -1080 + 20)
    capture.start_x, capture.start_y, capture.cur_x, capture.cur_y = 250, 120, 350, 220
    assert capture._box() == (1280, 1180, 1380, 1280)
    assert capture._image_box() == (1280, 1180, 1380, 1280)


def test_selection_scales_when_the_capture_and_the_overlay_differ():
    capture = screenshot.ScreenshotCapture()
    capture.original_image = Image.new('RGB', (1500, 1080), 'white')       # half the overlay's width
    capture._vscreen_left, capture._vscreen_top = VIRTUAL[:2]
    capture._view_size = VIRTUAL[2:]
    capture.start_x, capture.start_y, capture.cur_x, capture.cur_y = -1000, -1000, 200, 200
    assert capture._box() == (80, 80, 1280, 1280)
    assert capture._image_box() == (40, 40, 640, 640)


def test_update_notice_formats_negative_monitor_coordinates(design, monkeypatch):
    from src.ui.update_notice import UpdateNotice
    recorded = []
    monkeypatch.setattr(UpdateNotice, '_work_area', staticmethod(lambda root: (-1080, -842, 0, 1078)))
    monkeypatch.setattr(UpdateNotice, '_prevent_activation', lambda self: None)
    orig = tk.Toplevel.geometry
    def spy(self, value=None):
        if value is not None:
            recorded.append(value)
        return orig(self, value)
    monkeypatch.setattr(tk.Toplevel, 'geometry', spy)
    notice = UpdateNotice(design.root, '2.0.1', lambda action: None)
    try:
        assert recorded and re.match(r'^\d+x\d+\+-\d+\+-?\d+$', recorded[-1])
        assert windows.parse_geometry(recorded[-1])[2] < 0
    finally:
        notice.close()


def test_history_dialog_formats_negative_monitor_coordinates(design, monkeypatch):
    from src.ui import history_dialog
    recorded = []
    monkeypatch.setattr(history_dialog, 'work_area', lambda: (-1080, -842, 0, 1078))
    orig = tk.Toplevel.geometry
    def spy(self, value=None):
        if value is not None:
            recorded.append(value)
        return orig(self, value)
    monkeypatch.setattr(tk.Toplevel, 'geometry', spy)
    dialog = history_dialog.HistoryDialog(design.root, SimpleNamespace(get_history=lambda **kw: []), lambda *a: None)
    try:
        assert recorded and re.match(r'^\d+x\d+\+-\d+\+-?\d+$', recorded[0])
        assert windows.parse_geometry(recorded[0])[2] < 0
    finally:
        dialog.window.destroy()


def test_provider_drag_ghost_formats_negative_monitor_coordinates(design, monkeypatch):
    from src.ui.settings.page_providers import ProvidersPage
    fake_ghost = FakeToast('200x88+0+0')
    fake_ghost.deiconify = lambda: None
    page = ProvidersPage.__new__(ProvidersPage)
    page._drag_ghost = fake_ghost
    page._ghost_width = 240
    page._ghost_height = 96
    page._show_drag_ghost(-900, -400)
    assert len(fake_ghost.calls) == 1
    assert re.match(r'^240x96\+-\d+\+-\d+$', fake_ghost.calls[0])

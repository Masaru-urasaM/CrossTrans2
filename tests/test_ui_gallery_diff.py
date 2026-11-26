"""
Tests for tools/ui_gallery_diff.py and the blank-capture guard of tools/ui_gallery.py (TODO.md U3).

The diff is what proves "the gallery is pixel-identical" after the token refactor, so it must
see one changed pixel, a missing image and a resized one - and not cry wolf over an image that
is meant to move.
"""
import os
import sys

import pytest
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import ui_gallery  # noqa: E402
import ui_gallery_diff as gd  # noqa: E402


def make_run(root, label, images):
    """images: {(scale, name): PIL image} -> <root>/<label>/s<scale>/<name>.png"""
    for (scale, name), image in images.items():
        folder = os.path.join(root, label, f's{scale}')
        os.makedirs(folder, exist_ok=True)
        image.save(os.path.join(folder, f'{name}.png'))


def flat(colour, size=(40, 30)):
    return Image.new('RGB', size, colour)


def with_pixel(colour, xy, value, size=(40, 30)):
    image = flat(colour, size)
    image.putpixel(xy, value)
    return image


@pytest.fixture
def runs(tmp_path):
    root = str(tmp_path)
    make_run(root, 'a', {(100, 'translator.empty'): flat('#222222'),
                         (100, 'toasts.success'): flat('#222222'),
                         (150, 'translator.loading'): flat('#222222'),
                         (150, 'history.empty'): flat('#222222'),
                         (200, 'only.in.a'): flat('#222222')})
    make_run(root, 'b', {(100, 'translator.empty'): flat('#222222'),
                         (100, 'toasts.success'): with_pixel('#222222', (3, 4), (255, 0, 0)),
                         (150, 'translator.loading'): flat('#333333'),
                         (150, 'history.empty'): flat('#222222', (41, 30))})
    return root


class TestDiffImages:
    def test_identical_images_have_no_differing_pixels(self, tmp_path):
        a, b = tmp_path / 'a.png', tmp_path / 'b.png'
        flat('#123456').save(a)
        flat('#123456').save(b)
        assert gd.diff_images(str(a), str(b))['differing'] == 0

    def test_one_changed_pixel_is_counted_and_boxed(self, tmp_path):
        a, b = tmp_path / 'a.png', tmp_path / 'b.png'
        flat('#222222').save(a)
        with_pixel('#222222', (3, 4), (255, 0, 0)).save(b)
        result = gd.diff_images(str(a), str(b))
        assert result['differing'] == 1 and result['bbox'] == (3, 4, 4, 5)

    def test_a_threshold_ignores_a_faint_change(self, tmp_path):
        a, b = tmp_path / 'a.png', tmp_path / 'b.png'
        flat((100, 100, 100)).save(a)
        flat((101, 100, 100)).save(b)
        assert gd.diff_images(str(a), str(b))['differing'] == 40 * 30
        assert gd.diff_images(str(a), str(b), threshold=2)['differing'] == 0

    def test_different_sizes_are_reported_not_compared(self, tmp_path):
        a, b = tmp_path / 'a.png', tmp_path / 'b.png'
        flat('#222222').save(a)
        flat('#222222', (41, 30)).save(b)
        result = gd.diff_images(str(a), str(b))
        assert result['same_size'] is False and result['differing'] is None


class TestCompare:
    def rows(self, runs, **kw):
        return {(r['scale'], r['capture']): r for r in gd.compare('a', 'b', root=runs, **kw)}

    def test_each_kind_of_difference_is_named(self, runs):
        rows = self.rows(runs)
        assert rows[(100, 'translator.empty')]['status'] == 'identical'
        assert rows[(100, 'toasts.success')]['status'] == 'changed'
        assert rows[(150, 'history.empty')]['status'] == 'resized'
        assert rows[(200, 'only.in.a')]['status'] == 'missing'

    def test_a_changed_image_gets_a_highlighted_copy(self, runs):
        self.rows(runs)
        marked = os.path.join(runs, 'diff_a_b', 's100', 'toasts.success.png')
        assert Image.open(marked).convert('RGB').getpixel((3, 4)) == (255, 0, 255)

    def test_the_animated_capture_is_ignored_by_default(self, runs):
        rows = self.rows(runs)
        assert rows[(150, 'translator.loading')]['ignored'] is True
        ok, counts = gd.verdict(list(rows.values()))
        assert not ok and counts['ignored'] == 1      # still failing: other images differ

    def test_filters_by_scale_and_surface(self, runs):
        only = self.rows(runs, scales=[100], surfaces=['toasts'])
        assert list(only) == [(100, 'toasts.success')]

    def test_tolerance_allows_a_few_pixels(self, runs):
        rows = self.rows(runs, tolerance=1, surfaces=['toasts'])
        assert rows[(100, 'toasts.success')]['status'] == 'identical'

    def test_identical_runs_are_a_pass(self, tmp_path):
        root = str(tmp_path)
        same = {(100, 'x'): flat('#222222')}
        make_run(root, 'a', same)
        make_run(root, 'b', same)
        ok, counts = gd.verdict(gd.compare('a', 'b', root=root))
        assert ok and counts == {'identical': 1}

    def test_an_unknown_run_is_refused(self, tmp_path):
        with pytest.raises(SystemExit, match="no run"):
            gd.png_map('nope', str(tmp_path))


class TestBlankCapture:
    def test_a_black_interior_is_blank(self, tmp_path):
        path = tmp_path / 'blank.png'
        image = flat('#f0f0f0', (200, 160))
        image.paste(flat((0, 0, 0), (200, 130)), (0, 30))
        image.save(path)
        assert ui_gallery.is_blank(str(path)) is True

    def test_a_painted_dark_window_is_not(self, tmp_path):
        path = tmp_path / 'dark.png'
        image = flat('#f0f0f0', (200, 160))
        image.paste(flat('#222222', (200, 130)), (0, 30))
        image.save(path)
        assert ui_gallery.is_blank(str(path)) is False

    def test_a_tiny_image_is_never_called_blank(self, tmp_path):
        path = tmp_path / 'tiny.png'
        flat((0, 0, 0), (20, 20)).save(path)
        assert ui_gallery.is_blank(str(path)) is False

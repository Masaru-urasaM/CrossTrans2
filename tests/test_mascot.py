"""
Tests for the mascot assets and loader (TODO.md U5, UQ4): every pose at every size exists and is a
real picture, the loader picks the right variant for the screen and draws it at the size asked.
"""
import os
import sys

import pytest
from PIL import Image

from src.ui import components as ui
from src.ui.components import mascots as mascot

from tests.test_components import env, holder  # noqa: F401  (shared fixtures)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import make_mascot  # noqa: E402


class TestAssets:
    @pytest.mark.parametrize('pose', mascot.POSES)
    @pytest.mark.parametrize('variant,size', mascot.VARIANTS)
    def test_every_pose_exists_at_every_size(self, pose, variant, size):
        with Image.open(mascot.path_of(pose, variant)) as image:
            assert image.size == (size, size) and image.mode == 'RGBA'

    @pytest.mark.parametrize('pose', mascot.POSES)
    def test_a_pose_is_a_figure_on_transparency(self, pose):
        with Image.open(mascot.path_of(pose, '2x')) as image:
            alpha = image.getchannel('A')
            assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((255, 255)) == 0
            assert alpha.getbbox() is not None
            visible = 256 * 256 - alpha.histogram()[0]
            assert 0.15 < visible / (256 * 256) < 0.75              # neither empty nor a filled square

    def test_the_poses_differ_from_each_other(self):
        data = {pose: Image.open(mascot.path_of(pose, '1x')).tobytes() for pose in mascot.POSES}
        assert len(set(data.values())) == len(mascot.POSES)

    def test_the_drawing_tool_and_the_loader_agree_on_poses_and_sizes(self):
        assert tuple(make_mascot.POSES) == mascot.POSES
        assert {tag: size for tag, size in make_mascot.SCALES.items()} == dict(mascot.VARIANTS)

    def test_the_whole_set_is_small(self):
        folder = os.path.join(REPO, 'src', 'assets', 'mascot')
        total = sum(os.path.getsize(os.path.join(folder, name)) for name in os.listdir(folder))
        assert total < 1_000_000                                    # the EXE carries it


class TestVariantChoice:
    @pytest.mark.parametrize('scale,variant', [(1.0, '1x'), (1.25, '1_5x'), (1.5, '1_5x'),
                                               (1.75, '2x'), (2.0, '2x'), (3.0, '2x')])
    def test_the_next_one_up_is_used(self, scale, variant):
        assert mascot.variant_for(scale) == variant


class TestLoader:
    def test_it_returns_a_tk_image_of_the_size_asked(self, env, tk_root):
        name = mascot.load('hello', 96)
        assert name in tk_root.image_names()
        expected = env.px(96)
        assert (tk_root.tk.call('image', 'width', name), tk_root.tk.call('image', 'height', name)) == (expected, expected)

    def test_the_default_size_is_96(self, env, tk_root):
        name = mascot.load('happy')
        assert int(tk_root.tk.call('image', 'width', name)) == env.px(96)

    def test_the_same_request_is_one_image(self, env):
        assert mascot.load('oops', 64) == mascot.load('oops', 64)
        assert mascot.load('oops', 64) != mascot.load('oops', 80)

    def test_an_unknown_pose_is_refused(self, env):
        with pytest.raises(ValueError, match="unknown mascot pose"):
            mascot.load('dancing')

    def test_it_is_exported_for_the_surfaces(self):
        assert ui.mascot is mascot.load

    def test_an_empty_state_can_wear_it(self, env, holder):
        picture = ui.mascot('sleepy', 96)
        state = ui.EmptyState(holder, 'Nothing yet', image=picture)
        assert picture in str(state.picture.cget('image'))

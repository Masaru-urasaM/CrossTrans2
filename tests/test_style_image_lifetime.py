"""Ttk theme elements outlive component contexts and must retain their actual image objects."""
import gc
import tkinter as tk
from uuid import uuid4

from tests.test_components import env
from src.ui.components.styles import Builder


def test_element_image_survives_context_cache_release(env, tk_root):
    photo = tk.PhotoImage(master=tk_root, width=4, height=4)
    name = str(photo)
    key = ('lifetime-fixture',)
    env._images[key] = photo
    Builder(env).element('CT.Lifetime.' + uuid4().hex, name, border=0)
    del photo
    env._images.pop(key)
    gc.collect()
    assert name in tk_root.image_names(), 'A live ttk element lost its Python-owned image'


def test_recreating_existing_element_does_not_retain_unused_replacement(env, tk_root):
    element = 'CT.BoundedLifetime.' + uuid4().hex
    first = tk.PhotoImage(master=tk_root, width=4, height=4)
    env._images['first-fixture'] = first
    Builder(env).element(element, str(first), border=0)
    retained_name = str(first)
    del first
    env._images.pop('first-fixture')
    second = tk.PhotoImage(master=tk_root, width=4, height=4)
    unused_name = str(second)
    env._images['second-fixture'] = second
    Builder(env).element(element, unused_name, border=0)
    del second
    env._images.pop('second-fixture')
    gc.collect()
    assert retained_name in tk_root.image_names()
    assert unused_name not in tk_root.image_names()


def test_switch_state_images_outlive_the_context_icon_cache(env, tk_root, monkeypatch):
    from src.ui.components import styles
    import importlib
    layout = importlib.import_module('ttkbootstrap.style.layout')
    monkeypatch.setattr(styles, 'PREFIX', 'CTLifetime' + uuid4().hex)
    published = set()
    create = layout.image_element
    def record(style, name, *, default, states=None, **options):
        published.update(str(image) for image in (default, *(states or {}).values()))
        return create(style, name, default=default, states=states, **options)
    monkeypatch.setattr(layout, 'image_element', record)
    Builder(env).switch('surface')
    assert len(published) == 8
    env._icons.clear()
    gc.collect()
    assert published <= set(tk_root.image_names()), 'A published switch state lost its image'


def test_identical_style_bitmaps_are_shared_between_contexts(env, tk_root):
    from src.ui.components.context import Context
    other = Context(tk_root, env.manager, env.style)
    first = Builder(env).rr('#C1D2E3', (23, 17), 5, under='#FFFFFF')
    second = Builder(other).rr('#C1D2E3', (23, 17), 5, under='#FFFFFF')
    assert first == second, 'A repeated context allocated an identical native bitmap'


def test_uninstall_releases_a_standalone_context_without_waiting_for_gc(env, tk_root):
    import weakref
    from src.ui.components import styles
    from src.ui.components.context import Context
    other = Context(tk_root, env.manager, env.style)
    styles.install(other)
    ref = weakref.ref(other)
    styles.uninstall(other)
    del other
    assert ref() is None, 'The removed theme callback retained a context/image cycle'


def test_unused_shared_photo_is_not_pinned_by_the_interpreter(env, tk_root):
    from PIL import Image
    from src.ui.components import render
    photo = render.to_photo(tk_root, Image.new('RGB', (3, 7), '#132639'), shared=True)
    name = str(photo)
    del photo
    assert name not in tk_root.image_names()


def test_repeated_style_contexts_do_not_multiply_native_images(env, tk_root):
    from src.ui.components import styles
    from src.ui.components.context import Context
    Builder(env).build()
    baseline = len(tk_root.image_names())
    for _ in range(3):
        other = Context(tk_root, env.manager, env.style)
        styles.install(other)
        try:
            assert len(tk_root.image_names()) <= baseline + 2
        finally:
            styles.uninstall(other)
        del other
    assert len(tk_root.image_names()) <= baseline + 2

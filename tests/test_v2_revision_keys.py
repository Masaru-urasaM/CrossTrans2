"""Visible key actions and drag identity survive the compact-row redesign."""
from types import SimpleNamespace
import pytest
from tests.final_ui_helpers import design
from tests.test_v2_providers import make
from src.core.settings_index import entry, page_for


def test_confirmed_rotation_change_only_renames_existing_model_preference(design):
    page, host = make(design)
    assert entry('failover').title == 'Rotate keys'
    initial = host.config.get_model_rotation()
    page.switches['failover'].invoke()
    assert host.config.get_model_rotation() is not initial
    assert page_for('AI providers') == page_for('API keys') == 'providers'


def test_keyboard_drag_commits_order_and_stable_identity(design):
    page, host = make(design, count=8)
    last = page.cards[-1]
    identity, values = last.key_id, last.values()
    page._begin_drag(last)
    for _ in range(7):
        page._keyboard_drag(-1)
    page._finish_drag(True)
    assert page.cards[0] is last
    assert last.key_id == identity and last.values() == values
    assert host.config.get_api_keys()[0]['id'] == identity
    assert not hasattr(last, 'up_button') and not hasattr(last, 'down_button')


def test_escape_cancels_reorder_without_persisting_a_preview(design):
    page, host = make(design, count=8)
    before = [row.key_id for row in page.cards]
    page._begin_drag(page.cards[-1])
    page._keyboard_drag(-1)
    page._finish_drag(False)
    assert [row.key_id for row in page.cards] == before
    assert [item['id'] for item in host.config.get_api_keys()] == before


def test_drop_outside_list_does_not_reorder(design):
    page, host = make(design, count=8)
    before = [row.key_id for row in page.cards]
    page._begin_drag(page.cards[-1])
    page._drag_target = 0
    page._drop(SimpleNamespace(x_root=page.keys_box.winfo_rootx()-10,
                              y_root=page.keys_box.winfo_rooty()))
    assert [row.key_id for row in page.cards] == before
    assert page._drop_marker is None and page._drag_job is None


@pytest.mark.parametrize('vision, files, expected', [
    (True, True, ['Supported', 'Supported']),
    (False, False, ['Unsupported', 'Unsupported']),
    (None, None, ['Not tested', 'Not tested']),
    (True, False, ['Supported', 'Unsupported']),
])
def test_capability_labels_stay_lit_and_status_is_evidence_based(design, vision, files, expected):
    page, host = make(design, count=1)
    rows = host.config.get_api_keys()
    rows[0].update(vision_capable=vision, file_capable=files)
    host.config.set_api_keys(rows)
    page._draw_capabilities()
    assert [chip.cget('text') for chip in page.capability_chips] == ['Reads images', 'Reads files', 'Reads audio']
    assert [chip.capability_state for chip in page.capability_chips] == expected + ['Not tested']
    assert all(chip.cget('image') and str(chip.cget('compound')) == 'right'
               for chip in page.capability_chips)


def test_pending_row_edits_and_usage_stay_with_the_dragged_key(design):
    page, host = make(design, count=8)
    row = page.cards[-1]
    row.field.set('fixture-edited-last')
    identity = row.key_id
    page._begin_drag(row)
    page._drag_target = 0
    page._finish_drag(True)
    assert host.config.get_api_keys()[0]['api_key'] == 'fixture-edited-last'
    assert host.config.get_api_keys()[0]['id'] == identity
    row.set_status('Connection test succeeded.')
    assert page.cards[0].status == 'Connection test succeeded.'
    assert page.cards[0].key_id == identity

@pytest.mark.parametrize('width', [850, 1200, 850])
def test_priority_labels_remain_visible_after_responsive_reflow(design, width):
    page, host = make(design, count=8)
    window = page.body.winfo_toplevel()
    window.geometry(f'{width}x900')
    design.root.update()
    origins = []
    for row in page.cards:
        row._wrap()
        design.root.update_idletasks()
        assert row.title_label.cget('text')
        assert row.title_label.winfo_rootx()+row.title_label.winfo_width() <= row.provider.winfo_rootx()
        origins.append(tuple(widget.winfo_rootx() for widget in
                             (row.provider, row.model, row.field, row.eye, row.test_btn)))
    for column in zip(*origins):
        assert max(column)-min(column) <= 2

def test_drop_below_a_scroll_viewport_does_not_commit_hidden_content(design):
    import tkinter as tk
    from src.ui import components as ui
    from src.ui.settings.page_providers import ProvidersPage
    from src.core.key_identity import normalize
    from tests.settings_support import Host, SettingsConfig
    window = tk.Toplevel(design.root)
    window.geometry('850x430+0+0')
    area = ui.ScrollArea(window)
    area.pack(fill='both', expand=True)
    keys = normalize([dict(api_key=f'fixture-{index}', provider='Google', model_name='fixture')
                      for index in range(8)])
    host = Host(window, SettingsConfig(api_keys=keys))
    host._areas = {'providers': area}
    page = ProvidersPage(host)
    page.build(area.content).pack(fill='x')
    design.root.update()
    viewport_bottom = area.canvas.winfo_rooty() + area.canvas.winfo_height()
    assert page.keys_box.winfo_rooty() + page.keys_box.winfo_height() > viewport_bottom
    before = [row.key_id for row in page.cards]
    page._begin_drag(page.cards[-1])
    page._drag_target = 0
    page._drop(SimpleNamespace(x_root=page.keys_box.winfo_rootx()+20, y_root=viewport_bottom+10))
    assert [row.key_id for row in page.cards] == before
    assert [item['id'] for item in host.config.get_api_keys()] == before


@pytest.mark.parametrize('action', ['remove', 'delete_all', 'unmap'])
def test_list_changes_or_hiding_cancel_the_active_drag(design, action):
    page, host = make(design, count=8)
    page._begin_drag(page.cards[-1])
    page._drag_target = 0
    if action == 'remove':
        page.remove_card(page.cards[-1])
    elif action == 'delete_all':
        page.delete_all()
    else:
        page.body.winfo_toplevel().withdraw()
        design.root.update()
    assert page._drag_card is None and page._drop_marker is None and page._drag_job is None


def test_late_connection_result_after_drag_updates_the_original_key(design):
    from src.core.key_testing import Outcome
    page, host = make(design, count=8)
    card = page.cards[-1]
    key, identity = card.key_text, card.key_id
    card.set_testing(True)
    page._begin_drag(card)
    page._drag_target = 0
    page._finish_drag(True)
    page._test_done(card, key, Outcome(True, 'Google', 'fixture-result', True, True, tried=1), None)
    assert page.cards[0] is card and card.key_id == identity
    assert host.config.get_api_keys()[0]['id'] == identity
    assert host.config.get_api_keys()[0]['model_name'] == 'fixture-result'
    assert page.usage.row(identity)['kind'] == 'test_success'
    assert not any(page.usage.row(row.key_id)['kind'] for row in page.cards[1:])

@pytest.mark.parametrize('kind', ['text', 'image', 'multimodal'])
def test_drag_priority_pending_model_and_usage_survive_real_config_reload(design, temp_config_dir, monkeypatch, kind):
    import os
    from pathlib import Path
    import time
    import tkinter as tk
    from config import Config
    from src.core.key_usage import KeyUsage
    from src.core.key_testing import Outcome
    from src.ui.settings.page_providers import ProvidersPage
    from tests.settings_support import Host
    from tests.test_v2_key_priority import manager
    monkeypatch.setattr(Config, 'CONFIG_DIR', temp_config_dir)
    monkeypatch.setattr(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json'))
    config = Config()
    config.set_api_keys([dict(api_key=f'fixture-credential-{index}', provider='Google',
                             model_name='fixture-model', vision_capable=True, file_capable=True)
                         for index in range(8)])
    original = config.get_api_keys()
    success_id = original[3]['id']
    KeyUsage(config).apply(dict(id=success_id, at=time.time(), kind='success', seconds=1.8))
    window = tk.Toplevel(design.root)
    window.geometry('1000x760')
    host = Host(window, config)
    page = ProvidersPage(host)
    page.build(window).pack(fill='both', expand=True)
    try:
        design.root.update()
        last = page.cards[-1]
        last.model.set('fixture-pending-model')
        last.set_testing(True)
        page._begin_drag(last)
        page._drag_target = 0
        page._finish_drag(True)
        assert Config().get_api_keys()[0]['model_name'] == 'fixture-pending-model'
        page._test_done(last, last.key_text, Outcome(
            True, 'Google', 'fixture-pending-model', True, True, tried=1), None)
        saved = Config()
        rows = saved.get_api_keys()
        expected = [original[-1], *original[:-1]]
        assert [row['api_key'] for row in rows] == [row['api_key'] for row in expected]
        assert [row['id'] for row in rows] == [row['id'] for row in expected]
        assert rows[0]['model_name'] == 'fixture-pending-model'
        assert KeyUsage(saved).row(success_id)['is_last_success']
        assert KeyUsage(saved).row(success_id)['average_seconds'] == pytest.approx(1.8)
        serialized = Path(Config.CONFIG_FILE).read_text(encoding='utf-8')
        assert all(row['api_key'] not in serialized for row in rows)
        api = manager(monkeypatch, rows)
        calls = []
        def generate(provider, credential, *args):
            calls.append(credential)
            if len(calls) < len(rows):
                raise RuntimeError('fixture request failure')
            return 'Answer'
        api._generate_content = api._generate_content_multimodal = generate
        if kind == 'text':
            assert api.translate('Prompt') == 'Answer'
        elif kind == 'image':
            assert api.translate_image('Prompt', 'fixture.png') == 'Answer'
        else:
            assert api.translate_multimodal('Prompt', image_paths=['fixture.png']) == 'Answer'
        assert calls == [row['api_key'] for row in rows]
    finally:
        window.destroy()
        design.root.update_idletasks()

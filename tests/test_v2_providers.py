"""Priority controls, recent status expiry and live key attribution on real widgets."""
import time
import tkinter as tk

from tests.final_ui_helpers import design
from tests.settings_support import Host, SettingsConfig
from src.core.key_identity import normalize
from src.core.key_usage import TTL
from src.ui.settings.page_providers import ProvidersPage


def make(design, count=3):
    window = tk.Toplevel(design.root)
    window.geometry('850x700')
    keys = normalize([dict(api_key=f'fixture-{i}', provider='Google', model_name='fixture-model')
                      for i in range(count)])
    host = Host(window, SettingsConfig(api_keys=keys))
    page = ProvidersPage(host)
    page.build(window).pack(fill='both', expand=True)
    window.update_idletasks()
    return page, host


def test_capability_note_is_above_the_primary_key_and_untested_is_explicit(design):
    page, host = make(design)
    assert page.capability_row.winfo_rooty() < page.cards[0].winfo_rooty()
    assert [chip.cget('text') for chip in page.capability_chips] == ['Reads images', 'Reads files', 'Reads audio']
    assert 'capabilities' not in page.switches


def test_reordering_preserves_identity_usage_and_saved_priority(design):
    page, host = make(design)
    first = page.cards[0]
    identity = first.key_id
    now = time.time()
    page.usage.apply(dict(id=identity, at=now, seconds=1.8, kind='success'))
    page.refresh_usage(now)
    assert first.last_used_label.winfo_manager() == 'grid'
    assert '1.8 s' in first.usage_label.cget('text')
    page.move_card(first, 1)
    assert first is page.cards[1] and first.key_id == identity
    assert [entry['api_key'] for entry in host.config.get_api_keys()] == ['fixture-1', 'fixture-0', 'fixture-2']
    assert host.config.get_api_keys()[1]['id'] == identity
    assert first.last_used_label.winfo_manager() == 'grid'


def test_recent_text_expires_after_24_hours_without_removing_key_or_arrow(design):
    page, host = make(design)
    first = page.cards[0]
    now = time.time()
    page.usage.apply(dict(id=first.key_id, at=now, seconds=2, kind='success'))
    page.refresh_usage(now)
    assert first.usage_label.cget('text')
    page.refresh_usage(now + TTL)
    assert first.usage_label.cget('text') == ''
    assert first.last_used_label.winfo_manager() == 'grid'
    assert len(host.config.get_api_keys()) == 3


def test_add_backup_has_no_six_key_cap(design):
    page, host = make(design, count=7)
    for index in range(4):
        card = page.add_backup()
        card.field.set(f'new-fixture-{index}')
    page.save()
    assert len(page.cards) == len(host.config.get_api_keys()) == 11
    assert str(page.add_btn.cget('state')) == 'normal'


def test_narrow_key_card_stacks_fields_and_keeps_last_success_inside(design):
    design.manager.set_text_scale(1.5)
    design.manager.set_ui_language('ja')
    page, host = make(design, count=1)
    page.body.winfo_toplevel().geometry('420x700')
    design.root.update()
    first = page.cards[0]
    now = time.time()
    page.usage.apply(dict(id=first.key_id, at=now, seconds=1.8, kind='success'))
    page.refresh_usage(now)
    design.root.update_idletasks()
    first._wrap()
    design.root.update_idletasks()
    assert first._compact
    assert first.model.winfo_y() > first.provider.winfo_y() + first.provider.winfo_height()
    left, right = first.winfo_rootx(), first.winfo_rootx() + first.winfo_width()
    for widget in (first.title_label, first.last_used_label, first.provider, first.model,
                   first.field, first.test_btn, first.eye, first.usage_label):
        assert widget.winfo_rootx() >= left
        assert widget.winfo_rootx() + widget.winfo_width() <= right, widget
    page.body.winfo_toplevel().geometry('1200x700')
    design.root.update()
    first._wrap()
    design.root.update_idletasks()
    assert not first._compact
    assert first.provider.winfo_y() == first.model.winfo_y()

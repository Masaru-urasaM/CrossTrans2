"""Real 24-hour boundaries and stable row identity, independent of UI repaint timing."""
from copy import deepcopy
from src.core.key_identity import normalize
from src.core.key_usage import KeyUsage, TTL


class Store:
    def __init__(self, keys):
        self.keys = keys
        self.data = {}
    def get_api_keys(self):
        return deepcopy(self.keys)
    def get(self, key, default=None):
        return self.data.get(key, default)
    def set(self, key, value):
        self.data[key] = deepcopy(value)


def test_ids_survive_reorder_and_model_changes_but_not_credential_replacement():
    original = normalize([dict(api_key='a', provider='Google'), dict(api_key='b', provider='Google')])
    updated = normalize([dict(api_key='b', provider='Google', model_name='new'),
                         dict(api_key='a', provider='Google')], original)
    assert [row['id'] for row in updated] == [original[1]['id'], original[0]['id']]
    replaced = normalize([dict(original[0], api_key='replacement')], original)
    assert replaced[0]['id'] != original[0]['id']


def test_duplicate_keys_still_receive_distinct_non_secret_row_ids():
    rows = normalize([dict(api_key='fixture-secret'), dict(api_key='fixture-secret')])
    assert rows[0]['id'] != rows[1]['id']
    assert all('secret' not in row['id'] for row in rows)
    assert normalize(rows) == rows


def test_status_is_blank_at_exactly_24_hours_but_last_success_persists():
    now = [100000.0]
    keys = normalize([dict(api_key='a')])
    store = Store(keys)
    usage = KeyUsage(store, lambda: now[0])
    identity = keys[0]['id']
    assert usage.apply(dict(id=identity, at=now[0], kind='success', seconds=1.8))
    assert usage.next_expiry() == now[0] + 86400
    now[0] += 86400 - 0.001
    assert usage.row(identity)['kind'] == 'success'
    now[0] += 0.001
    row = KeyUsage(store, lambda: now[0]).row(identity)
    assert row['kind'] is None and row['average_seconds'] is None
    assert row['is_last_success']


def test_latest_event_replaces_status_but_average_uses_successes_only():
    now = [100000.0]
    keys = normalize([dict(api_key='a')])
    store = Store(keys)
    usage = KeyUsage(store, lambda: now[0])
    identity = keys[0]['id']
    for kind, seconds in [('success', 1), ('success', 3), ('quota', 99)]:
        now[0] += 1
        usage.apply(dict(id=identity, at=now[0], kind=kind, seconds=seconds))
    row = usage.row(identity)
    assert row['kind'] == 'quota' and row['average_seconds'] == 2
    assert row['is_last_success']


def test_removed_row_late_result_cannot_recreate_status_or_move_arrow():
    keys = normalize([dict(api_key='a'), dict(api_key='b')])
    store = Store(keys)
    usage = KeyUsage(store, lambda: 1000)
    usage.apply(dict(id=keys[1]['id'], at=1000, kind='success', seconds=2))
    store.keys = [keys[1]]
    assert not usage.apply(dict(id=keys[0]['id'], at=1001, kind='success', seconds=1))
    assert usage.row(keys[1]['id'])['is_last_success']
    assert keys[0]['id'] not in store.data['key_usage']['rows']


def test_editing_a_key_to_duplicate_a_later_row_does_not_steal_that_rows_identity():
    original = normalize([dict(api_key='a'), dict(api_key='b')])
    updated = normalize([dict(original[0], api_key='b'), original[1]], original)
    assert updated[1]['id'] == original[1]['id']
    assert updated[0]['id'] not in {row['id'] for row in original}

"""Per-key recent status and successful latency samples, persisted only by the UI owner."""
from copy import deepcopy
import math
import time

TTL = 24 * 60 * 60


class KeyUsage:
    def __init__(self, config, clock=time.time):
        self.config = config
        self.clock = clock

    def apply(self, event):
        identity = event.get('id')
        known = {item.get('id') for item in self.config.get_api_keys()}
        if not identity or identity not in known:
            return False
        now = self.clock()
        at = float(event.get('at', now))
        seconds = float(event.get('seconds', 0))
        if not math.isfinite(at) or not math.isfinite(seconds) or seconds < 0:
            return False
        state = deepcopy(self.config.get('key_usage', {}))
        rows = state.setdefault('rows', {})
        for row in rows.values():
            row['samples'] = [sample for sample in row.get('samples', []) if 0 <= now - sample[0] < TTL]
        row = rows.setdefault(identity, {'samples': []})
        kind = event.get('kind', 'error')
        if kind == 'audio_success':
            row['audio_at'] = at
            row['audio_requests'] = row.get('audio_requests', 0) + 1
            self.config.set('key_usage', state)
            return True
        if at >= row.get('at', 0):
            row.update(at=at, kind=kind)
        if kind == 'success':
            row['samples'].append([at, seconds])
            if at >= state.get('last_success_at', 0):
                state.update(last_success_id=identity, last_success_at=at)
        self.config.set('key_usage', state)
        return True

    def row(self, identity, now=None):
        now = self.clock() if now is None else now
        state = self.config.get('key_usage', {})
        row = state.get('rows', {}).get(identity, {})
        samples = [sample[1] for sample in row.get('samples', []) if 0 <= now - sample[0] < TTL]
        recent = 'at' in row and 0 <= now - row['at'] < TTL
        return dict(kind=row.get('kind') if recent else None,
                    at=row.get('at') if recent else None,
                    average_seconds=sum(samples) / len(samples) if samples else None,
                    is_last_success=bool(identity) and state.get('last_success_id') == identity)

    def next_expiry(self, now=None):
        now = self.clock() if now is None else now
        deadlines = []
        for row in self.config.get('key_usage', {}).get('rows', {}).values():
            if row.get('at', 0) + TTL > now:
                deadlines.append(row['at'] + TTL)
            deadlines.extend(sample[0] + TTL for sample in row.get('samples', []) if sample[0] + TTL > now)
        return min(deadlines) if deadlines else None

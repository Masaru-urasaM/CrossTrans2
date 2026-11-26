"""Non-secret credential-row identities that survive edits, reordering and restart."""
from collections import defaultdict, deque
from uuid import UUID, uuid4


def valid_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def fingerprint(row):
    return row.get('api_key', '').strip(), row.get('provider', 'Auto')


def normalize(rows, previous=()):
    by_id = {valid_id(row.get('id')): row for row in previous if valid_id(row.get('id'))}
    buckets = defaultdict(deque)
    for row in previous:
        buckets[fingerprint(row)].append(row)
    reserved = {valid_id(row.get('id')) for row in rows
                if valid_id(row.get('id')) in by_id
                and fingerprint(by_id[valid_id(row.get('id'))]) == fingerprint(row)}
    used, result = set(), []
    for row in rows:
        row = dict(row)
        identity = valid_id(row.get('id'))
        prior = by_id.get(identity)
        if prior is not None and fingerprint(prior) != fingerprint(row):
            identity = None
        if identity in used:
            identity = None
        if identity is None:
            matches = buckets[fingerprint(row)]
            while matches:
                candidate = valid_id(matches.popleft().get('id'))
                if candidate and candidate not in used and candidate not in reserved:
                    identity = candidate
                    break
        row['id'] = identity or str(uuid4())
        used.add(row['id'])
        result.append(row)
    return result

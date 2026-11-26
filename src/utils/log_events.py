"""Structured log events: what happened and how it measured, never what the user wrote.

`log_event('Audio', 'recording.stop', sid=sid, seconds=12.4, devices=[...])` writes one JSON line whose
`event` and `data` a tool reads without a regex, and a readable `[Audio] recording.stop seconds=12.4 ...`
on the console. Fields carry counts, lengths, durations, providers, models, HTTP codes and device names.
Text, transcripts, the user's file names and keys never go in: the logs are meant to be shared
(Decision 78), so a value is a measurement of the content, not the content.
"""
import logging
import secrets

# A guard, not a filter: a string field longer than this is cut, so a mistake at a call site cannot
# write a whole paragraph. Device and model names fit.
MAX_TEXT_FIELD = 80


def new_session_id() -> str:
    """A short id that ties together the lines of one recording, file or request."""
    return secrets.token_hex(4)


def _clean(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT_FIELD else value[:MAX_TEXT_FIELD] + '...'
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    return _clean(str(value))


def log_event(tag: str, event: str, *, level: int = logging.INFO, sid: str | None = None, **fields) -> None:
    """Log `event` under `tag` with `fields` as structured data (see the module docstring)."""
    data = {key: _clean(value) for key, value in fields.items()}
    summary = ' '.join(f'{key}={value}' for key, value in data.items())
    logging.log(level, '[%s] %s%s', tag, event, f' {summary}' if summary else '',
                extra={'event': event, 'data': data, 'sid': sid})

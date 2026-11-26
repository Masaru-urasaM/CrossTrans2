"""Summarize CrossTrans2's JSON Lines logs: sessions, calls, warnings (CHANGELOG Phase 26, Decision 86).

    python tools/log_report.py                      # the newest day in %APPDATA%\\CrossTrans2\\logs (or logs/)
    python tools/log_report.py --day 20261116
    python tools/log_report.py path\\to\\translator_20261119.jsonl --json

One audio session is every line with its `sid`, from `session.start` to `session.done`: the devices and what
they delivered, each recognition chunk, the mode that ran and how long each step took. API calls are grouped by
provider and model, warnings and errors by tag and message. `--json` prints the same summary as one JSON
object for a tool to read. The logs hold measurements only, never the user's text, so neither does this.
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Named .jsonl since 2026-11-19 (TODO Task 26); a day written before keeps its .log name.
_DAY_RE = re.compile(r'^translator_(\d{8})\.(jsonl|log)(?:\.(\d+))?$')


def default_log_dir() -> str:
    """The installed app's folder when it exists, else the checkout's `logs/` (a run from source)."""
    appdata = os.environ.get('APPDATA')
    installed = os.path.join(appdata, 'CrossTrans2', 'logs') if appdata else ''
    return installed if installed and os.path.isdir(installed) else os.path.join(REPO, 'logs')


def day_files(directory: str, day: Optional[str] = None) -> List[str]:
    """One day's file and its rotated siblings, oldest first (`.jsonl.3` ... `.jsonl.1`, then `.jsonl`;
    a `.log` of the same day and its siblings, written before the rename, before them)."""
    found: Dict[str, List[Tuple[Tuple[bool, int], str]]] = {}
    for path in glob.glob(os.path.join(directory, 'translator_*')):
        matched = _DAY_RE.match(os.path.basename(path))
        if matched:
            order = (matched.group(2) == 'jsonl', -int(matched.group(3) or 0))
            found.setdefault(matched.group(1), []).append((order, path))
    if not found:
        return []
    chosen = day or max(found)
    return [path for _, path in sorted(found.get(chosen, []))]


def read_entries(paths: Iterable[str]) -> Tuple[List[dict], int]:
    """Every parsable line in order, and how many lines were not JSON objects."""
    entries, bad = [], 0
    for path in paths:
        with open(path, encoding='utf-8', errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
                else:
                    bad += 1
    return entries, bad


def _median(values: List[float]) -> Optional[float]:
    return round(statistics.median(values), 1) if values else None


def _audio_sessions(entries: List[dict]) -> List[dict]:
    sessions: 'OrderedDict[str, dict]' = OrderedDict()
    for entry in entries:
        sid, event, data = entry.get('sid'), entry.get('event'), entry.get('data') or {}
        if entry.get('tag') != 'Audio' or not sid:
            continue
        session = sessions.setdefault(sid, {'sid': sid, 'started': entry.get('ts'), 'chunks': [], 'failures': []})
        if event == 'session.start':
            session['start'] = data
            session['started'] = entry.get('ts')
        elif event == 'recording.stop':
            session['recording'] = data
        elif event == 'file.decode':
            session['file'] = data
        elif event == 'stt.plan':
            session['plan'] = data
        elif event == 'stt.chunk':
            session['chunks'].append(data)
        elif event == 'stt.fail':
            session['failures'].append(data)
        elif event == 'mode.used':
            session['mode'] = data
        elif event == 'session.done':
            session['done'] = data
            session['ended'] = entry.get('ts')
    return list(sessions.values())


def _api_calls(entries: List[dict]) -> List[dict]:
    groups: 'OrderedDict[Tuple[str, str, str], dict]' = OrderedDict()
    for entry in entries:
        if entry.get('tag') != 'API' or entry.get('event') not in ('call.ok', 'call.fail'):
            continue
        data = entry.get('data') or {}
        key = (data.get('kind', ''), data.get('provider', ''), data.get('model', ''))
        group = groups.setdefault(key, {'kind': key[0], 'provider': key[1], 'model': key[2], 'ok': 0, 'fail': 0,
                                        'ms': [], 'statuses': Counter(), 'slots': Counter()})
        group['ok' if entry['event'] == 'call.ok' else 'fail'] += 1
        if isinstance(data.get('ms'), (int, float)):
            group['ms'].append(data['ms'])
        if data.get('status') is not None:
            group['statuses'][str(data['status'])] += 1
        if data.get('key_slot') is not None:
            group['slots'][str(data['key_slot'])] += 1
    result = []
    for group in groups.values():
        ms = group.pop('ms')
        group.update(median_ms=_median(ms), max_ms=max(ms) if ms else None,
                     statuses=dict(group['statuses']), slots=dict(group['slots']))
        result.append(group)
    return result


def summarize(entries: List[dict], bad: int = 0, files: Optional[List[str]] = None) -> dict:
    """The report as plain data (what `--json` prints)."""
    levels = Counter(entry.get('level', '?') for entry in entries)
    problems = Counter((entry.get('level'), entry.get('tag'), (entry.get('msg') or '')[:120])
                       for entry in entries if entry.get('level') in ('WARNING', 'ERROR', 'CRITICAL'))
    startup = next((entry.get('msg') for entry in entries if entry.get('tag') == 'Startup'), None)
    shots = [entry for entry in entries if entry.get('tag') == 'Screenshot']
    shot_ms = [(entry.get('data') or {}).get('ms') for entry in shots if entry.get('event') == 'translated']
    attachments = [entry for entry in entries if entry.get('tag') == 'Attachments']
    return {
        'files': [os.path.basename(path) for path in (files or [])],
        'first': entries[0].get('ts') if entries else None,
        'last': entries[-1].get('ts') if entries else None,
        'lines': len(entries), 'unparsable': bad,
        'levels': dict(levels),
        'startup': startup,
        'audio_sessions': _audio_sessions(entries),
        'api_calls': _api_calls(entries),
        'screenshots': {'captured': sum(1 for e in shots if e.get('event') == 'captured'),
                        'translated': len(shot_ms), 'failed': sum(1 for e in shots if e.get('event') == 'failed'),
                        'median_ms': _median([ms for ms in shot_ms if isinstance(ms, (int, float))])},
        'attachments': {'added': Counter((e.get('data') or {}).get('kind', '') for e in attachments
                                         if e.get('event') == 'added'),
                        'rejected': Counter((e.get('data') or {}).get('reason', '') for e in attachments
                                            if e.get('event') == 'rejected'),
                        'thumbnail_failures': sum(1 for e in attachments if e.get('event') == 'thumbnail.fail')},
        'problems': [{'level': level, 'tag': tag, 'msg': msg, 'count': count}
                     for (level, tag, msg), count in problems.most_common()],
    }


def _session_lines(session: dict) -> List[str]:
    start, done = session.get('start', {}), session.get('done', {})
    mode = session.get('mode', {})
    head = (f"  [{session['sid']}] {str(session.get('started') or '')[11:19]} {start.get('kind', '?')}"
            f" source={start.get('source', start.get('ext', '-'))}"
            f" mode={mode.get('requested', start.get('mode_requested', '?'))}->{mode.get('used', '?')}"
            + (f" ({mode['reason']})" if mode.get('reason') else '')
            + f" outcome={done.get('outcome', 'unfinished')}")
    lines = [head]
    recording = session.get('recording')
    if recording:
        lines.append(f"      recorded {recording.get('seconds')} s"
                     + (' (limit reached)' if recording.get('limit_reached') else ''))
        for device in recording.get('devices', []):
            lines.append(f"        {device.get('role')}: {device.get('name')} frames={device.get('frames')}"
                         f" peak={device.get('peak')} rms={device.get('rms')} track={device.get('track')}")
        for skipped in recording.get('skipped', []):
            lines.append(f"        skipped {skipped.get('role')}: {skipped.get('name', '')} ({skipped.get('reason')})")
    if session.get('file'):
        data = session['file']
        lines.append(f"      file {data.get('ext')} {data.get('mb')} MB, {data.get('seconds')} s of audio,"
                     f" decoded in {data.get('ms')} ms")
    for chunk in session['chunks']:
        source = 'cache' if chunk.get('cached') else (f"{chunk.get('provider')} {chunk.get('model')}"
                                                      f" [{chunk.get('engine')}] slot {chunk.get('key_slot')}"
                                                      f" {chunk.get('ms')} ms")
        lines.append(f"      chunk {chunk.get('index')}/{chunk.get('total')} {chunk.get('source')}"
                     f" {chunk.get('seconds')} s: {source}, {chunk.get('chars')} chars")
    for failure in session['failures']:
        lines.append(f"      FAILED chunk {failure.get('index')} slot {failure.get('key_slot')}"
                     f" {failure.get('provider')} {failure.get('model')}: {failure.get('status')}"
                     f" {failure.get('error')} {failure.get('message', '')}")
    if done:
        timings = ', '.join(f"{key}={done[key]}" for key in ('record_s', 'decode_ms', 'stt_ms', 'translate_ms',
                                                              'total_ms') if key in done)
        lines.append(f"      {timings}; transcript {done.get('transcript_chars', '-')} chars,"
                     f" translation {done.get('translation_chars', '-')} chars; {done.get('models', '')}")
        if done.get('error'):
            lines.append(f"      error {done['error']}: {done.get('message', '')}")
    return lines


def render(summary: dict) -> str:
    """The report for a person."""
    lines = [f"Files: {', '.join(summary['files']) or '-'}",
             f"Lines: {summary['lines']} ({summary['unparsable']} unparsable)   "
             f"{summary['first'] or '-'} -> {summary['last'] or '-'}",
             f"Levels: {summary['levels']}"]
    if summary['startup']:
        lines.append(f"Startup: {summary['startup']}")
    lines.append('')
    lines.append(f"Audio sessions: {len(summary['audio_sessions'])}")
    for session in summary['audio_sessions']:
        lines.extend(_session_lines(session))
    lines.append('')
    lines.append(f"API calls: {sum(g['ok'] + g['fail'] for g in summary['api_calls'])}")
    for group in summary['api_calls']:
        lines.append(f"  {group['kind']:<10} {group['provider']} {group['model']}: ok={group['ok']} fail={group['fail']}"
                     f" median={group['median_ms']} ms max={group['max_ms']} ms slots={group['slots']}"
                     + (f" statuses={group['statuses']}" if group['statuses'] else ''))
    shots = summary['screenshots']
    lines.append('')
    lines.append(f"Screenshots: captured={shots['captured']} translated={shots['translated']}"
                 f" failed={shots['failed']} median={shots['median_ms']} ms")
    attached = summary['attachments']
    lines.append(f"Attachments: added={dict(attached['added'])} rejected={dict(attached['rejected'])}"
                 f" thumbnail failures={attached['thumbnail_failures']}")
    lines.append('')
    lines.append(f"Warnings and errors: {sum(p['count'] for p in summary['problems'])}")
    for problem in summary['problems']:
        lines.append(f"  {problem['count']:>3} x {problem['level']} [{problem['tag']}] {problem['msg']}")
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('paths', nargs='*', help='log files to read (default: one day of the log folder)')
    parser.add_argument('--dir', help='the log folder (default: %%APPDATA%%\\CrossTrans2\\logs, else logs/)')
    parser.add_argument('--day', help='YYYYMMDD (default: the newest day in the folder)')
    parser.add_argument('--json', action='store_true', help='print the summary as one JSON object')
    args = parser.parse_args(argv)
    paths = args.paths or day_files(args.dir or default_log_dir(), args.day)
    if not paths:
        print('log_report: no log file found', file=sys.stderr)
        return 1
    entries, bad = read_entries(paths)
    summary = summarize(entries, bad, paths)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1, default=dict))
    else:
        print(render(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())

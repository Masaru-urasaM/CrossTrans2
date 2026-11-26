"""Structured dictionary entries shared by desktop renderers; no widgets are imported."""
import re
from dataclasses import dataclass
from typing import Tuple
from src.ui.dictionary_render import DictRun


FIELD = re.compile(r'^(\d+)\.\s+\*\*(.+?)\*\*(?:\s*\([^\n]*?\))?:[ \t]*', re.MULTILINE)
HEADER = re.compile(r'^##[ \t]+(.+?)[ \t]*$', re.MULTILINE)


@dataclass(frozen=True)
class Field:
    number: int
    label: str
    runs: Tuple[DictRun, ...]

    @property
    def text(self):
        return ''.join(run.base for run in self.runs)


@dataclass(frozen=True)
class Entry:
    heading: Tuple[DictRun, ...]
    fields: Tuple[Field, ...]


def take_runs(runs, start, end):
    """A presentation slice. Structural delimiters are ASCII, outside ruby pairs."""
    result, position = [], 0
    for run in runs:
        stop = position + len(run.base)
        left, right = max(start, position), min(end, stop)
        if right > left:
            ruby = run.ruby if left == position and right == stop else None
            result.append(DictRun(run.base[left - position:right - position], ruby, run.color))
        position = stop
    return tuple(result)


def parse_entries(text, runs):
    """Present every field and unrecognised paragraph, in order, without re-annotating fragments.

    Numbers and English labels remain a wire format. Only their Markdown wrappers
    become UI labels; Copy always uses the untouched model response.
    """
    headers = list(HEADER.finditer(text))
    bounds = [(m, m.start(), headers[i + 1].start() if i + 1 < len(headers) else len(text))
              for i, m in enumerate(headers)]
    if not headers:
        bounds = [(None, 0, len(text))]
    elif text[:headers[0].start()].strip():
        bounds.insert(0, (None, 0, headers[0].start()))
    entries = []
    for header, start, end in bounds:
        heading = ()
        body = start
        if header:
            a, b = header.span(1)
            if text[a:b].startswith('[') and text[a:b].endswith(']'):
                a, b = a + 1, b - 1
            heading = take_runs(runs, a, b)
            body = header.end()
        matches = list(FIELD.finditer(text, body, end))
        fields = []

        def add(number, label, a, b):
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            # A Markdown entry separator is chrome, never a dictionary value.
            if text[a:b].endswith('\n---'):
                b -= 4
            if a < b or number:
                fields.append(Field(number, label, take_runs(runs, a, b)))

        first = matches[0].start() if matches else end
        add(0, 'Notes', body, first)
        for i, match in enumerate(matches):
            finish = matches[i + 1].start() if i + 1 < len(matches) else end
            add(int(match.group(1)), match.group(2), match.end(), finish)
        if heading or fields:
            entries.append(Entry(heading, tuple(fields)))
    return tuple(entries)



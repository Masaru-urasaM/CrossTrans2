"""No log call writes what the user wrote: lengths and counts only (CHANGELOG Phase 23, Decision 78).

Logs are meant to be opened, zipped and shared by the user (Settings > About > Open diagnostics), and the
redaction there removes keys, not text. So the rule is enforced where the text would enter: a log call may
format `len(text)`, never `text` or a slice of it. Read from the source, so a new call site is covered.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_METHODS = {'debug', 'info', 'warning', 'error', 'exception', 'critical', 'log'}
# Names that hold the user's words in this code base.
CONTENT_NAMES = {'text', 'selected_text', 'original', 'translated', 'translation', 'transcript',
                 'transcript_text', 'prompt', 'raw', 'clipboard_text', 'source_text', 'corrected'}


def _is_log_call(node):
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr in LOG_METHODS
            and isinstance(func.value, ast.Name) and func.value.id in {'logging', 'logger', 'log'})


def _leaks(expr):
    """The formatted value is user text itself, or a slice of something (an excerpt)."""
    if isinstance(expr, ast.Name):
        return expr.id in CONTENT_NAMES
    if isinstance(expr, ast.Attribute):
        return expr.attr in CONTENT_NAMES
    if isinstance(expr, ast.Subscript):
        return isinstance(expr.slice, ast.Slice)
    return False


def _formatted_values(call):
    for arg in list(call.args) + [keyword.value for keyword in call.keywords if keyword.arg != 'extra']:
        if isinstance(arg, ast.JoinedStr):
            for part in arg.values:
                if isinstance(part, ast.FormattedValue):
                    yield part.value
        else:
            yield arg


def scan(root):
    """`file:line: expression` for every log call under `root` that formats user text."""
    found = []
    for path in sorted(Path(root).rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_log_call(node):
                for value in _formatted_values(node):
                    if _leaks(value):
                        found.append(f'{path.relative_to(root).as_posix()}:{node.lineno}: {ast.unparse(value)}')
    return found


# Excerpts that are not the user's words, by file and expression (line numbers move).
ALLOWED = {
    ('core/nlp_manager.py', 'result.stderr[:200]'),     # pip's own error output when a language pack install fails
}


def test_no_log_call_formats_user_text_or_an_excerpt_of_it():
    found = [entry for entry in scan(PROJECT_ROOT / 'src')
             if (entry.split(':', 1)[0], entry.split(': ', 1)[1]) not in ALLOWED]
    assert found == []


def test_the_check_catches_the_patterns_it_exists_for(tmp_path):
    (tmp_path / 'sample.py').write_text(
        'import logging\n'
        'logging.info(f"Selected text: {selected_text[:50]}...")\n'
        'logging.warning("output %s", result.stdout[:100])\n'
        'logging.info(f"Transcript: {transcript}")\n'
        'logging.info(f"Selected text: {len(selected_text)} chars")\n'
        'logging.info("chars=%d", len(text))\n', encoding='utf-8')
    assert scan(tmp_path) == ['sample.py:2: selected_text[:50]', 'sample.py:3: result.stdout[:100]',
                              'sample.py:4: transcript']

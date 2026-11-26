"""
Count the hard-coded styling in the UI code (TODO.md U0; Appendix A, "Literals per file").

    python tools/ui_audit.py                       # a summary on the console
    python tools/ui_audit.py --json logs/ui_audit/before.json

Static: it reads source text and never imports the app, so it needs no display and no
desktop. What it counts is what the redesign removes - colour literals, font tuples,
bootstyles, padding values, emoji and symbols in UI strings, and `tk.*` widgets given a
colour without `autostyle=False` (ttkbootstrap overwrites those colours, CLAUDE.md Known
Issues). `tests/test_ui_audit.py` reads the same numbers as a ratchet: they may only go down.

Scanned: `src/`, `config.py`, `main.py`. Not scanned: tests, tools, build output.
"""
import argparse
import ast
import collections
import json
import os
import re
import sys
import warnings

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_ROOTS = ('src', 'config.py', 'main.py')
# Files that may hold style literals by design - the theme package, once it exists, and
# nothing else (TODO.md DoD1). The ratchet counts everything outside these.
ALLOWED_PREFIXES = ('src/ui/theme/',)

HEX = re.compile(r"['\"](#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})['\"]")
# A font tuple names a family we know; ('padx', 0) and the like are not fonts.
FONT_FAMILIES = ('Segoe', 'Consolas', 'Yu Gothic', 'Cascadia', 'Meiryo', 'Arial', 'Courier',
                 'Microsoft', 'Malgun', 'Nirmala', 'Leelawadee')
FONT = re.compile(r"\(\s*['\"]((?:%s)[A-Za-z0-9 ]*)['\"]\s*,\s*(-?\d+)\s*"
                  r"(?:,\s*['\"]([a-z ]+)['\"])?\s*\)" % '|'.join(FONT_FAMILIES))
BOOTSTYLE = re.compile(r"bootstyle\s*=\s*['\"]([a-z\-\s]+)['\"]")
PADDING = re.compile(r"\b(padx|pady|padding)\s*=\s*\(?\s*(\d+)")
# Emoji, arrows, geometric shapes and dingbats: the characters Tk 8.6 draws small and flat.
SYMBOL = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿←-⇿■-◿]")
UI_LINE = ('text=', 'title', 'show_', 'label', 'message', 'placeholder')

TK_WIDGETS = {'Label', 'Button', 'Frame', 'Canvas', 'Text', 'Entry', 'Listbox', 'Toplevel',
              'Checkbutton', 'Radiobutton', 'Scale', 'Scrollbar', 'Menu', 'Spinbox',
              'LabelFrame', 'PanedWindow', 'Message', 'Menubutton'}
COLOUR_OPTIONS = {'bg', 'fg', 'background', 'foreground', 'activebackground',
                  'activeforeground', 'selectbackground', 'selectforeground',
                  'highlightbackground', 'highlightcolor', 'insertbackground',
                  'disabledforeground', 'troughcolor', 'readonlybackground'}


def is_scanned(rel):
    rel = rel.replace(os.sep, '/')
    return rel.endswith('.py') and (rel in SCAN_ROOTS or any(
        rel.startswith(root.rstrip('/') + '/') for root in SCAN_ROOTS))


def is_allowed(rel):
    return rel.replace(os.sep, '/').startswith(ALLOWED_PREFIXES)


def source_files(repo=REPO):
    found = []
    for root in SCAN_ROOTS:
        path = os.path.join(repo, root)
        if os.path.isfile(path):
            found.append(root)
            continue
        for folder, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in files:
                if name.endswith('.py'):
                    found.append(os.path.relpath(os.path.join(folder, name), repo))
    return sorted(f.replace(os.sep, '/') for f in found)


def _widget_class(call):
    """'Label' for `tk.Label(...)` / `tkinter.Label(...)`; None for anything else (a ttk
    widget is themed by its style, not by explicit colours)."""
    func = call.func
    if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id in ('tk', 'tkinter') and func.attr in TK_WIDGETS):
        return func.attr
    return None


def _carries_guard(value):
    """True for a dict or `dict(...)` that holds `autostyle=False` or unpacks NO_AUTOSTYLE."""
    if isinstance(value, ast.Dict):
        for key, item in zip(value.keys, value.values):
            if key is None and isinstance(item, ast.Name) and item.id == 'NO_AUTOSTYLE':
                return True
            if isinstance(key, ast.Constant) and key.value == 'autostyle':
                return True
        return False
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'dict':
        return any(k.arg == 'autostyle' or (k.arg is None and isinstance(k.value, ast.Name)
                                            and k.value.id == 'NO_AUTOSTYLE')
                   for k in value.keywords)
    return False


def guarded_dicts(tree):
    """Names bound to a kwargs dict that carries the guard (`label_kwargs = dict(...,
    **NO_AUTOSTYLE)`), so `tk.Label(..., **label_kwargs)` counts as guarded."""
    return {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and _carries_guard(node.value)
            for target in node.targets if isinstance(target, ast.Name)}


def _guarded(call, named=frozenset()):
    """True when the call carries `autostyle=False`, unpacks the project's NO_AUTOSTYLE, or
    unpacks a kwargs dict that does."""
    for keyword in call.keywords:
        if keyword.arg == 'autostyle':
            return isinstance(keyword.value, ast.Constant) and keyword.value.value is False
        if (keyword.arg is None and isinstance(keyword.value, ast.Name)
                and (keyword.value.id == 'NO_AUTOSTYLE' or keyword.value.id in named)):
            return True
    return False


def coloured_tk_widgets(tree):
    """[(line, widget class, guarded)] for every `tk.*` widget built with an explicit colour."""
    named = guarded_dicts(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        widget = _widget_class(node)
        if widget and any(k.arg in COLOUR_OPTIONS for k in node.keywords):
            found.append((node.lineno, widget, _guarded(node, named)))
    return found


def legacy_references(tree):
    """How many times the code reads a constant from `src.ui.theme.legacy` - the bridge that
    moved every literal into the theme package without a visual change. Each surface drops
    them as it adopts the tokens; the count reaches zero when `legacy` is deleted (U13)."""
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'src.ui.theme':
            aliases |= {a.asname or a.name for a in node.names if a.name == 'legacy'}
        elif isinstance(node, ast.Import):
            aliases |= {a.asname for a in node.names
                        if a.name == 'src.ui.theme.legacy' and a.asname}
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Attribute)
               and isinstance(n.value, ast.Name) and n.value.id in aliases)


def scan_text(text):
    """The counts for one file's source text."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', SyntaxWarning)   # updates.py's `\S` is TODO.md section 11's
            tree = ast.parse(text)
    except SyntaxError:
        tree = ast.parse('')
    symbols = []
    for number, line in enumerate(text.splitlines(), 1):
        # The requested Last used arrow is typography, not a replacement for an action icon.
        symbol_line = line.replace('← Last used', 'Last used')
        if SYMBOL.search(symbol_line) and any(word in line.lower() for word in UI_LINE):
            symbols.append((number, line.strip()[:110]))
    return {
        'colours': [m.lower() for m in HEX.findall(text)],
        'fonts': [f"{fam} {size} {style}".strip() for fam, size, style in FONT.findall(text)],
        'bootstyles': [m.strip() for m in BOOTSTYLE.findall(text)],
        'paddings': [int(v) for _, v in PADDING.findall(text)],
        'symbols': symbols,
        'tk_coloured': coloured_tk_widgets(tree),
        'legacy_refs': legacy_references(tree),
    }


def scan(repo=REPO):
    """The whole audit as plain data (JSON-ready)."""
    colours, fonts = collections.Counter(), collections.Counter()
    bootstyles, paddings = collections.Counter(), collections.Counter()
    files, symbols = {}, []
    unguarded, coloured, legacy = [], 0, 0
    for rel in source_files(repo):
        with open(os.path.join(repo, rel), encoding='utf-8') as fh:
            counts = scan_text(fh.read())
        colours.update(counts['colours'])
        fonts.update(counts['fonts'])
        bootstyles.update(counts['bootstyles'])
        paddings.update(counts['paddings'])
        symbols += [f"{rel}:{n}: {line}" for n, line in counts['symbols']]
        coloured += len(counts['tk_coloured'])
        if not is_allowed(rel):
            legacy += counts['legacy_refs']
        unguarded += [f"{rel}:{n} tk.{w}" for n, w, guarded in counts['tk_coloured'] if not guarded]
        files[rel] = {'colours': len(counts['colours']), 'fonts': len(counts['fonts']),
                      'bootstyles': len(counts['bootstyles']),
                      'allowed': is_allowed(rel)}
    outside = {k: sum(v[k] for rel, v in files.items() if not v['allowed'])
               for k in ('colours', 'fonts', 'bootstyles')}
    return {
        'files_scanned': len(files),
        'colours': {'literals': sum(colours.values()), 'distinct': len(colours),
                    'outside_theme': outside['colours'], 'by_value': dict(colours.most_common())},
        'fonts': {'literals': sum(fonts.values()), 'distinct': len(fonts),
                  'outside_theme': outside['fonts'], 'by_spec': dict(fonts.most_common())},
        'bootstyles': {'uses': sum(bootstyles.values()), 'distinct': len(bootstyles),
                       'outside_theme': outside['bootstyles'],
                       'by_style': dict(bootstyles.most_common())},
        'paddings': {'distinct': len(paddings), 'values': sorted(paddings)},
        'symbols_in_ui_strings': {'count': len(symbols), 'lines': symbols},
        'legacy_references': {'outside_theme': legacy},
        'tk_widgets_with_colour': {'total': coloured, 'unguarded': len(unguarded),
                                   'where': unguarded},
        'per_file': dict(sorted(files.items(),
                                key=lambda kv: -(kv[1]['colours'] + kv[1]['fonts']
                                                 + kv[1]['bootstyles']))),
    }


def summary(report):
    c, f, b = report['colours'], report['fonts'], report['bootstyles']
    u = report['tk_widgets_with_colour']
    lines = [f"files scanned           {report['files_scanned']}",
             f"colour literals         {c['literals']:>4} ({c['distinct']} distinct, "
             f"{c['outside_theme']} outside src/ui/theme/)",
             f"font literals           {f['literals']:>4} ({f['distinct']} distinct, "
             f"{f['outside_theme']} outside src/ui/theme/)",
             f"bootstyle uses          {b['uses']:>4} ({b['distinct']} distinct, "
             f"{b['outside_theme']} outside src/ui/theme/)",
             f"padding values          {report['paddings']['distinct']:>4} distinct",
             f"symbols in UI strings   {report['symbols_in_ui_strings']['count']:>4}",
             f"legacy-token references {report['legacy_references']['outside_theme']:>4}",
             f"tk widgets with colour  {u['total']:>4} ({u['unguarded']} without autostyle=False)",
             "", "per file (colours, fonts, bootstyles):"]
    for rel, v in report['per_file'].items():
        if v['colours'] or v['fonts'] or v['bootstyles']:
            lines.append(f"  {rel:<44} {v['colours']:>4} {v['fonts']:>4} {v['bootstyles']:>4}")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--json', help="write the full report here (a path under the repo)")
    args = parser.parse_args(argv)
    report = scan()
    print(summary(report))
    if args.json:
        out = os.path.join(REPO, args.json) if not os.path.isabs(args.json) else args.json
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print(f"\nwritten: {os.path.relpath(out, REPO)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

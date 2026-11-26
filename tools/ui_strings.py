"""Check catalog coverage and basic voice rules for application presentation strings (U10/U12).

Data values, protocol text, diagnostic details and the style implementation are deliberately outside
this scan. Variable text is exercised by the UI tests/gallery; this tool checks static source messages
and formatted templates at the application's presentation boundaries.
"""
import argparse
import ast
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / 'src/ui/locales/catalog.json'
WIDGETS = {'Label', 'Button', 'Chip', 'Choice', 'Toggle', 'Switch', 'Tooltip'}
POSITIONS = {'Dialog': (1, 2), 'EmptyState': (1, 2), 'InfoBar': (1,),
             'WaitingDialog': (1,), 'verify_async': (1,),
             'title': (0,), 'add_button': (0,), 'group': (0,), 'set_message': (0,),
             'set_status': (0,), 'show_error': (0,), 'show_warning': (0,), 'show_success': (0,),
             'show_info': (0,), 'show_warning_with_action': (0, 1), 'show_warning_with_shake': (0,),
             'tr': (0,)}
EXEMPT = {
    'CrossTrans': 'Product name', 'Windows Hello': 'Windows feature name',
    'CrossTrans v{0}': 'Product/version identifier', 'Ctrl + Enter': 'Keyboard key names',
    '{0} × {1} px': 'Dimensions and unit', '{0} MB': 'File size and unit',
    '{0} KB': 'File size and unit', '{0} B': 'File size and unit',
    '{0}': 'Dynamic data only', '{0}.': 'Step number',
}
CORE_PRESENTATION = {'key_testing.py', 'pack_catalog.py', 'shortcut_rules.py', 'guide_content.py'}


def message(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        pieces, index = [], 0
        for item in node.values:
            if isinstance(item, ast.Constant):
                pieces.append(item.value)
            else:
                pieces.append('{' + str(index) + '}')
                index += 1
        return ''.join(pieces)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = message(node.left), message(node.right)
        return left + right if left is not None and right is not None else None
    return None


def presentation_messages(root=REPO):
    found = {}

    def add(node, path):
        if isinstance(node, ast.IfExp):
            add(node.body, path)
            add(node.orelse, path)
            return
        text = message(node)
        if not text or text in EXEMPT or not re.search(r'[A-Za-z]{2}', text):
            return
        if text.startswith(('http:', 'https:', '<', '^', '%')):
            return
        found.setdefault(text, []).append(f'{path.relative_to(root).as_posix()}:{node.lineno}')

    for path in sorted((root / 'src').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        if '/theme/' in relative or '/assets/' in relative or path.name in ('i18n.py', 'styles.py'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                if any(kw.arg == 'localize' and isinstance(kw.value, ast.Constant) and kw.value.value is False
                       for kw in node.keywords):
                    continue
                positions = (1,) if name in WIDGETS else POSITIONS.get(name, ())
                if path.name == 'settings_index.py':
                    positions = {'Entry': (2, 3), 'Page': (1,)}.get(name, positions)
                for index in positions:
                    if len(node.args) > index:
                        add(node.args[index], path)
                for keyword in node.keywords:
                    if keyword.arg in ('text', 'title', 'message', 'placeholder', 'label', 'action'):
                        add(keyword.value, path)
            elif isinstance(node, ast.Return) and (path.name in CORE_PRESENTATION or '/ui/' in relative):
                owner = parents.get(node)
                while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = parents.get(owner)
                if owner is not None and owner.name == 'ocr_prompt':
                    continue
                text = message(node.value)
                if text and text[0].isupper():
                    add(node.value, path)
        if '/ui/' in relative:
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.isupper()
                                                        for t in node.targets):
                    candidates = [node.value] if message(node.value) is not None else ast.walk(node.value)
                    for item in candidates:
                        if not isinstance(item, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                            continue
                        text = message(item)
                        if text and text[0].isupper() and ' ' in text and not text.startswith(('http', '^')):
                            add(item, path)
    return found


def audit(root=REPO):
    catalog = json.loads((root / 'src/ui/locales/catalog.json').read_text(encoding='utf-8'))
    sources = presentation_messages(root)
    problems = []
    for text, locations in sources.items():
        if text not in catalog:
            problems.append({'kind': 'missing_translation', 'message': text, 'where': locations})
        if 'OFF =' in text or 'ON =' in text:
            problems.append({'kind': 'switch_explanation', 'message': text, 'where': locations})
        if re.search('[\U0001F300-\U0001FAFF]', text):
            problems.append({'kind': 'emoji', 'message': text, 'where': locations})
    return sources, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    sources, problems = audit()
    if args.json:
        args.json.write_text(json.dumps(problems, ensure_ascii=False, indent=2), encoding='utf-8')
    for problem in problems:
        print(f"{problem['kind']}: {problem['message']!r} ({', '.join(problem['where'])})")
    print(f'{len(sources)} presentation source messages; {len(problems)} findings')
    return bool(problems)


if __name__ == '__main__':
    raise SystemExit(main())

"""Print deterministic CSS from Harbor tokens; --check verifies the committed file."""
import argparse
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LAYERS = ('primitive', 'semantic', 'component')


def build_css(source):
    if source.get('schema') != 'harbor.design-tokens/1':
        raise ValueError('Unknown token schema')
    leaves = {}

    def walk(node, path):
        if not isinstance(node, dict):
            raise ValueError('Expected a token group or leaf')
        if '$value' in node:
            if set(node) != {'$type', '$value'}:
                raise ValueError('Expected an exact typed token')
            leaves[path] = node
        else:
            for key, value in node.items():
                if not re.fullmatch(r'[a-z0-9-]+', key):
                    raise ValueError('Invalid token name')
                walk(value, path + '.' + key)

    for layer in LAYERS:
        walk(source[layer], layer)
    names = {key: '--' + key.replace('.', '-') for key in leaves}
    if len(set(names.values())) != len(names):
        raise ValueError('CSS token names collide')

    def css_value(key, seen=()):
        if key in seen or key not in leaves:
            raise ValueError('Missing or cyclic token')
        node = leaves[key]
        kind, value = node['$type'], node['$value']
        ref = re.fullmatch(r'\{([^{}]+)\}', value) if isinstance(value, str) else None
        if ref:
            target = ref.group(1)
            if (target not in leaves or leaves[target]['$type'] != kind
                    or LAYERS.index(target.split('.')[0]) >= LAYERS.index(key.split('.')[0])):
                raise ValueError('Invalid token alias layer or type')
            css_value(target, seen + (key,))
            return f'var({names[target]})'
        if kind == 'color' and isinstance(value, str) and re.fullmatch(r'#[0-9a-f]{6}', value):
            return value
        if kind == 'dimension' and isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:px|rem|ch)', value):
            return value
        if kind == 'duration' and isinstance(value, str) and re.fullmatch(r'[0-9]+ms', value):
            return value
        if kind == 'number' and type(value) in (int, float) and math.isfinite(value):
            return str(value)
        if kind == 'fontFamily' and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9 ,"\'-]+', value):
            return value
        if (kind == 'cubicBezier' and isinstance(value, list) and len(value) == 4
                and all(type(v) in (int, float) and math.isfinite(v) for v in value)
                and 0 <= value[0] <= 1 and 0 <= value[2] <= 1):
            return 'cubic-bezier(' + ', '.join(map(str, value)) + ')'
        raise ValueError('Invalid token value: ' + key)

    lines = ['/* Generated from design/tokens.json by tools/design_tokens.py; do not edit. */', ':root {']
    lines.extend(f'  {names[key]}: {css_value(key)};' for key in leaves)
    return '\n'.join(lines + ['}', ''])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    css = build_css(json.loads((ROOT / 'design/tokens.json').read_text(encoding='utf-8')))
    if args.check:
        if css != (ROOT / 'harbor/web_assets/tokens.css').read_text(encoding='utf-8'):
            raise SystemExit('tokens.css differs; regenerate it from design/tokens.json')
        print('Token CSS matches source')
    else:
        print(css, end='')


if __name__ == '__main__':
    main()

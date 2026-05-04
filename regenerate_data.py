#!/usr/bin/env python3
"""
Phase 1 helper: regenerate /data/*.json files from the inline data in index.html.

During Phase 1, index.html is still the source of truth — both HTML inline literals
AND data/*.json files exist, with the JSON derived from the HTML. After editing data
in index.html, run this script to keep the JSON files in sync.

The validator's check_json_matches_html will fail in CI if you forget to regenerate.

Usage:  python3 regenerate_data.py [path/to/index.html]
"""

import sys, re, json
from pathlib import Path

try:
    import json5
except ImportError:
    print("Error: json5 not installed. Run: pip install json5", file=sys.stderr)
    sys.exit(1)


def find_block(content, name, kind):
    """Find a const X = {...} | [...] | new Set([...]) literal in HTML/JS source."""
    idx = content.find(f'const {name}')
    if idx == -1:
        return None
    eq_idx = content.find('=', idx)
    after_eq = content[eq_idx + 1:].lstrip()
    abs_start = eq_idx + 1 + len(content[eq_idx + 1:]) - len(after_eq)

    if kind == 'set':
        bracket_idx = content.find('[', abs_start)
        return _balanced(content, bracket_idx, '[', ']')
    open_c, close_c = ('{', '}') if kind == 'object' else ('[', ']')
    return _balanced(content, abs_start, open_c, close_c)


def _balanced(content, start, open_c, close_c):
    depth, i = 0, start
    while i < len(content):
        if content[i] == open_c:
            depth += 1
        elif content[i] == close_c:
            depth -= 1
            if depth == 0:
                return content[start:i + 1]
        i += 1
    return None


def parse_block(content, name, kind):
    raw = find_block(content, name, kind)
    if raw is None:
        raise ValueError(f"Could not find {name} in source")
    cleaned = re.sub(r'\[\s*,', '[', raw)  # strip leading-comma sparse-array trick
    return json5.loads(cleaned)


# Single source of truth for the data layout.
# Format: (output_path, [(html_const, kind, optional_json_subkey), ...])
LAYOUT = [
    ('senate.json',          [('SENATE_DATA',  'array',  None)]),
    ('house.json',           [('HOUSE_DATA',   'array',  None)]),
    ('court.json',           [('COURT_DATA',   'array',  None)]),
    ('cabinet.json',         [('CABINET_DATA', 'array',  None)]),
    ('bills.json',           [('BILLS_DATA',   'array',  None)]),
    ('profiles.json',        [('PROFILES_DATA', 'object', None)]),
    ('aipac.json',           [('AIPAC_DATA',  'object', None)]),
    ('fec.json',             [('FEC_V8_DATA', 'object', None)]),
    ('sectors.json', [
        ('FOSSIL_DATA',     'object', 'fossil'),
        ('PHARMA_DATA',     'object', 'pharma'),
        ('DEFENSE_DATA',    'object', 'defense'),
        ('FINANCE_DATA',    'object', 'finance'),
        ('TECH_DATA',       'object', 'tech'),
        ('NRA_DATA',        'object', 'nra'),
        ('GRASSROOTS_DATA', 'object', 'grassroots'),
    ]),
    ('tags.json', [
        ('PHARMA_NAMES',          'set', 'pharma'),
        ('TECH_NAMES',            'set', 'tech'),
        ('DEFENSE_NAMES',         'set', 'defense'),
        ('FINANCE_NAMES',         'set', 'finance'),
        ('GRASSROOTS_NAMES',      'set', 'grassroots'),
        ('NO_STOCK_NAMES',        'set', 'no_stock'),
        ('EXPLICIT_CLEAN_MEMBERS', 'set', 'explicit_clean'),
    ]),
    ('bioguide.json',        [('BIOGUIDE',             'object', None)]),
    ('birth_dates.json',     [('BIRTH_DATES',          'object', None)]),
    ('photo_overrides.json', [('WIKI_PHOTO_OVERRIDES', 'object', None)]),
    ('corporate.json',       [('CORPORATE_DATA',       'array',  None)]),
    ('sector_counts.json',   [('SECTOR_COUNTS',        'object', None)]),
]


def main():
    html_path = Path(sys.argv[1] if len(sys.argv) > 1 else 'index.html')
    if not html_path.exists():
        print(f"Error: {html_path} not found", file=sys.stderr)
        sys.exit(1)

    content = html_path.read_text(encoding='utf-8')
    data_dir = html_path.parent / 'data'
    data_dir.mkdir(exist_ok=True)

    print(f"Regenerating data/*.json from {html_path}\n")
    written = 0
    for fname, sources in LAYOUT:
        if len(sources) == 1 and sources[0][2] is None:
            data = parse_block(content, sources[0][0], sources[0][1])
        else:
            data = {subkey: parse_block(content, const, kind) for const, kind, subkey in sources}
        out = data_dir / fname
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        written += 1
        print(f"  ✓ data/{fname}")

    print(f"\n✅ Wrote {written} files to {data_dir}/")


if __name__ == '__main__':
    main()

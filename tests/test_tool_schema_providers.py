import json
import re
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / 'plugin/native/schemas'
VECTORS = ROOT / 'spec/uid_label_vectors.json'

PORTABLE_ESCAPE = re.compile(r'\\(?:x[0-9a-fA-F]{2}|[\\^$.|?*+()\[\]{}/-])')


def patterns(node, where):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'pattern' and isinstance(value, str):
                yield where, value
            else:
                yield from patterns(value, where + '.' + key)
    elif isinstance(node, list):
        for at, value in enumerate(node):
            yield from patterns(value, f'{where}[{at}]')


def provider_check(schema):
    meta = jsonschema.Draft202012Validator.META_SCHEMA
    checker = jsonschema.validators.validator_for(meta)(meta, format_checker=jsonschema.FormatChecker())
    return [error.message for error in checker.iter_errors(schema)]


def every_schema():
    found = sorted(SCHEMAS.glob('*.json'))
    assert len(found) > 20, 'the schemas were not found'
    return found


@pytest.mark.parametrize('path', every_schema(), ids=lambda p: p.name)
def test_the_providers_check_takes_every_schema(path):
    raw = path.read_bytes()
    assert all(0x20 <= b <= 0x7E or b in b'\n\r\t' for b in raw), f'{path.name}: the file holds a byte that is not printable ASCII'
    schema = json.loads(raw)
    assert provider_check(schema) == []
    for where, pattern in patterns(schema, path.stem):
        re.compile(pattern)
        rest = PORTABLE_ESCAPE.sub('', pattern)
        assert '\\' not in rest, f'{where}: an escape RE2 and Python read differently, or one of them not at all: {pattern!r}'
        for construct in ('(?=', '(?!', '(?<', '(?P'):
            assert construct not in pattern, f'{where}: {construct} is not RE2'


def test_the_check_refuses_what_the_provider_refused():
    refused = provider_check({'type': 'string', 'pattern': '^[^\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]+$'})
    assert refused and "is not a 'regex'" in refused[0]


def table(rows):
    held = set()
    for row in rows:
        held.update(range(int(row['first'], 16), int(row['last'], 16) + 1))
    return held


def text_patterns():
    found = {}
    for name, field in (('vocabulary-correct', 'heard'), ('vocabulary-correct', 'meant'), ('vocabulary-forget', 'heard'),
                        ('speaker-enroll', 'label'), ('speaker-associate', 'display_label')):
        schema = json.loads((SCHEMAS / f'{name}.input.json').read_text())
        found[f'{name} {field}'] = re.compile(schema['properties'][field]['pattern'])
    return found


def test_python_refuses_the_code_points_the_tables_refuse():
    vectors = json.loads(VECTORS.read_text())
    refused, spaces = table(vectors['refused_code_points']), table(vectors['spaces'])
    joiners = {int(text, 16) for text in vectors['joiners']}
    assert len(refused) == 237 and len(spaces) == 17 and joiners == {0x200C, 0x200D}
    found = text_patterns()
    for r in range(0x110000):
        if 0xD800 <= r <= 0xDFFF:
            continue
        c, bad = chr(r), r in refused
        for where, pattern in found.items():
            assert (pattern.fullmatch(c) is None) == bad, f'{where}: U+{r:04X} alone'
            assert (pattern.fullmatch('b' + c + 'c') is None) == (bad and not ('heard' not in where and r in joiners)), f'{where}: U+{r:04X} in a word'
    for where, pattern in found.items():
        joined = 'heard' not in where
        for joiner in map(chr, joiners):
            assert (pattern.fullmatch('b' + joiner + 'c') is not None) == joined, where
            for space in map(chr, spaces):
                assert pattern.fullmatch('b' + space + joiner + 'c') is None, where
                assert pattern.fullmatch('b' + joiner + space + 'c') is None, where
        assert pattern.fullmatch('b\n') is None, where

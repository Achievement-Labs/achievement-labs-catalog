"""Validate the catalog under titles/ against the repository data rules.

python tools/validate.py [--titles DIR]
Exit 0 when every rule holds; otherwise prints one line per problem and exits 1.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import (FORBIDDEN_OPS, ID, INDEX_KEYS, OP_TYPES, REQUEST, TITLE_KEYS, TITLES,
                     CatalogError, parse, render_request, requests_of, title_ids)

ALLOWED_FILES = {'Data.json', 'Stats.json'}


def check_bytes(path, raw, errors):
    name = path.as_posix()
    if raw.startswith(b'\xef\xbb\xbf'):
        errors.append(f'{name}: has a UTF-8 BOM')
    if b'\r' in raw:
        errors.append(f'{name}: uses CR/CRLF line endings (LF only)')
    if not raw.endswith(b'\n'):
        errors.append(f'{name}: does not end with "\\n"')
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        errors.append(f'{name}: is not UTF-8')


def read_json(path, errors):
    raw = path.read_bytes()
    check_bytes(path, raw, errors)
    try:
        return parse(raw)
    except (ValueError, UnicodeDecodeError) as error:
        errors.append(f'{path.as_posix()}: invalid JSON: {error}')
        return None


def forbidden(value, where, errors):
    """No RawBody/TemplateReplacement anywhere, as an operation type or a property name."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_OPS:
                errors.append(f'{where}: forbidden property {key}')
            if key == 'ReplacementType' and child in FORBIDDEN_OPS:
                errors.append(f'{where}: forbidden operation {child}')
            forbidden(child, where, errors)
    elif isinstance(value, list):
        for child in value:
            forbidden(child, where, errors)


def check_operation(op, where, errors):
    if not isinstance(op, dict):
        errors.append(f'{where}: operation is not an object')
        return
    kind = op.get('ReplacementType')
    if kind not in OP_TYPES:
        errors.append(f'{where}: unsupported ReplacementType {kind!r}')
    if not isinstance(op.get('Target'), str) or not op['Target']:
        errors.append(f'{where}: missing Target')
    if kind == 'Replace' and (isinstance(op.get('Replacement'), bool) or not isinstance(op.get('Replacement'), (str, int, float))):
        errors.append(f'{where}: invalid Replacement value')
    if kind in ('RangeInt', 'RangeFloat') and not all(isinstance(op.get(k), (int, float)) and not isinstance(op.get(k), bool) for k in ('Min', 'Max')):
        errors.append(f'{where}: range needs numeric Min and Max')


def check_achievements(tid, block, template, errors, warnings):
    achievements = block.get('Achievements')
    if not isinstance(achievements, dict):
        errors.append(f'{tid}: Achievements must be an object')
        return 0
    rendered = 0
    for aid, entry in achievements.items():
        where = f'{tid}/Achievements/{aid}'
        if not ID.fullmatch(aid):
            errors.append(f'{where}: achievement key is not numeric')
            continue
        try:
            requests = requests_of(entry)
        except CatalogError as error:
            errors.append(f'{where}: {error}')
            continue
        if not (len(requests) == 1 and requests[0] is entry):
            names = list(entry)
            if not all(REQUEST.fullmatch(n) for n in names) or names != ['Request' + str(i) for i in range(1, len(names) + 1)]:
                errors.append(f'{where}: requests must be Request1..RequestN')
        before = len(errors)
        for number, request in enumerate(requests, 1):
            if not isinstance(request, dict) or not request:
                errors.append(f'{where}: empty request {number}')
                continue
            for name, op in request.items():
                check_operation(op, f'{where}/{name}', errors)
        if len(errors) > before or template is None:
            continue
        try:
            notes = []
            for request in requests:
                render_request(template, request, tid, warnings=notes)
            rendered += 1
            for note in sorted(set(notes)):
                warnings.append(f'{where}: {note}')
        except (CatalogError, KeyError, TypeError, ValueError) as error:
            errors.append(f'{where}: does not render: {error}')
    return rendered


def validate(titles=TITLES, warnings=None):
    errors = []
    warnings = [] if warnings is None else warnings
    index_path = titles / 'index.json'
    if not index_path.is_file():
        return [f'{index_path.as_posix()}: missing']
    index = read_json(index_path, errors)
    if not isinstance(index, dict) or not index:
        return errors + ['index.json: must be a non-empty object']
    if list(index)[-1] != 'SupportedTitleIDs' or not isinstance(index['SupportedTitleIDs'], list):
        errors.append('index.json: SupportedTitleIDs must be the last property and a list')
        return errors
    ids = title_ids(index)
    for key in ids:
        if not ID.fullmatch(key):
            errors.append(f'index.json: non-numeric top-level key {key!r}')
    supported = [str(t) for t in index['SupportedTitleIDs']]
    if len(set(supported)) != len(supported):
        errors.append('index.json: duplicate SupportedTitleIDs')
    if set(supported) != set(ids):
        errors.append(f'index.json: SupportedTitleIDs and title keys differ: {sorted(set(supported) ^ set(ids))[:10]}')
    folders = sorted(p.name for p in titles.iterdir() if p.is_dir())
    stray = sorted(p.name for p in titles.iterdir() if p.is_file() and p.name != 'index.json')
    if stray:
        errors.append(f'titles/: unexpected files {stray}')
    if set(folders) != set(ids):
        errors.append(f'index.json does not match title folders: only in index {sorted(set(ids) - set(folders))[:10]}, '
                      f'only as folders {sorted(set(folders) - set(ids))[:10]}')
    rendered = 0
    for tid in ids:
        summary = index[tid]
        if not isinstance(summary, dict):
            errors.append(f'index.json/{tid}: must be an object')
            continue
        if 'Achievements' in summary:
            errors.append(f'index.json/{tid}: the index must not contain achievement mappings')
        extra = set(summary) - INDEX_KEYS
        if extra:
            errors.append(f'index.json/{tid}: non-runtime fields {sorted(extra)}')
        folder = titles / tid
        if not folder.is_dir():
            continue
        names = {p.name for p in folder.iterdir()}
        unexpected = names - ALLOWED_FILES - {tid + '.json'}
        if unexpected:
            errors.append(f'{tid}/: unexpected files {sorted(unexpected)}')
        for required in ('Data.json', tid + '.json'):
            if required not in names:
                errors.append(f'{tid}/{required}: missing')
        template = None
        if tid + '.json' in names:
            raw = (folder / (tid + '.json')).read_bytes()
            check_bytes(folder / (tid + '.json'), raw, errors)
            template = raw.decode('utf-8-sig', errors='replace')
        if 'Data.json' in names:
            data = read_json(folder / 'Data.json', errors)
            if isinstance(data, dict):
                if list(data) != [tid, 'SupportedTitleIDs'] or [str(t) for t in data.get('SupportedTitleIDs', [None])] != [tid]:
                    errors.append(f'{tid}/Data.json: must be exactly {{"{tid}": ..., "SupportedTitleIDs": ["{tid}"]}} in that order')
                block = data.get(tid)
                if isinstance(block, dict):
                    extra = set(block) - TITLE_KEYS
                    if extra:
                        errors.append(f'{tid}/Data.json: non-runtime fields {sorted(extra)}')
                    if {k: v for k, v in block.items() if k != 'Achievements'} != summary:
                        errors.append(f'{tid}: index summary and Data.json title block disagree')
                    forbidden(block, f'{tid}/Data.json', errors)
                    rendered += check_achievements(tid, block, template, errors, warnings)
                else:
                    errors.append(f'{tid}/Data.json: title block missing')
        if 'Stats.json' in names:
            stats = read_json(folder / 'Stats.json', errors)
            if isinstance(stats, dict):
                if not stats or list(stats)[-1] != 'SupportedTitleIDs' or set(stats) - {tid, 'SupportedTitleIDs'}:
                    errors.append(f'{tid}/Stats.json: must hold only this title followed by SupportedTitleIDs')
                forbidden(stats, f'{tid}/Stats.json', errors)
    validate.rendered = rendered
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--titles', type=Path, default=TITLES)
    args = parser.parse_args(argv)
    warnings = []
    errors = validate(args.titles, warnings)
    by_title = {}
    for warning in warnings:
        by_title.setdefault(warning.split('/')[0], []).append(warning)
    for tid, items in sorted(by_title.items()):
        print(f'WARNING {tid}: {len(items)} recipe(s): {items[0].split(": ", 1)[1]}')
    for error in errors:
        print('ERROR', error)
    if errors:
        print(f'{len(errors)} problem(s)')
        return 1
    print(f'ok: catalog valid ({getattr(validate, "rendered", 0)} achievement recipes rendered)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

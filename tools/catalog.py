"""Shared reading and rendering helpers for the Achievement Labs event catalog.

Standard library only. Rendering mirrors the app's EventCatalog.BuildPayloadsAsync
(src/AchievementLabs.Core/EventCatalog.cs) and tools/event_catalog.py.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLES = ROOT / 'titles'
ID = re.compile(r'[0-9]{1,10}\Z')
REQUEST = re.compile(r'Request[1-9][0-9]*\Z')
TRAILING_COMMA = re.compile(r',(?=\s*[}\]])')
PLACEHOLDER = re.compile(r'REPLACE[A-Z0-9_]+')
TITLE_KEYS = {'TitleId', 'TitleName', 'Devices', 'ServiceConfigId', 'Enabled', 'FullySupported', 'PartiallySupported', 'Achievements'}
INDEX_KEYS = TITLE_KEYS - {'Achievements'}
OP_TYPES = {'Replace', 'RangeInt', 'RangeFloat', 'StupidFuckingLDAPTimestamp'}
FORBIDDEN_OPS = {'RawBody', 'TemplateReplacement'}
# Placeholders the app fills at send time, after the recipe operations run.
RUNTIME_PLACEHOLDERS = ('REPLACESEQ', 'REPLACETIME', 'REPLACEXUID', 'REPLACESESSIONGUID')
SAMPLE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class CatalogError(ValueError):
    pass


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise CatalogError('Duplicate JSON property: ' + key)
        out[key] = value
    return out


def parse(raw):
    """Parse JSON bytes or text, rejecting duplicate properties (BOM tolerated here; validate.py rejects it)."""
    text = raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw
    return json.loads(text, object_pairs_hook=unique)


def title_ids(index):
    return [key for key in index if key != 'SupportedTitleIDs']


def load_index(titles=TITLES):
    return parse((titles / 'index.json').read_bytes())


def load_title(titles, tid):
    return parse((titles / tid / 'Data.json').read_bytes())[tid]


def load_template(titles, tid):
    return (titles / tid / (tid + '.json')).read_bytes().decode('utf-8-sig')


def load_stats(titles, tid):
    path = titles / tid / 'Stats.json'
    return parse(path.read_bytes()).get(tid) if path.exists() else None


def requests_of(entry):
    """Same rule as the app: an entry whose first value has ReplacementType is a single request."""
    if not isinstance(entry, dict) or not entry:
        raise CatalogError('Empty event mapping')
    first = next(iter(entry.values()))
    if not isinstance(first, dict):
        raise CatalogError('Invalid event mapping')
    return [entry] if 'ReplacementType' in first else list(entry.values())


def replacement_text(value):
    """JToken.ToString() for the scalar replacement values used in recipes."""
    if isinstance(value, bool) or value is None:
        raise CatalogError('Invalid replacement value')
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # .NET double.ToString() prints whole numbers without a decimal point.
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    raise CatalogError('Invalid replacement value')


def operation_text(op, timestamp=SAMPLE_TIME):
    kind = op.get('ReplacementType')
    if kind in FORBIDDEN_OPS:
        raise CatalogError('Forbidden operation: ' + kind)
    if kind == 'Replace':
        return replacement_text(op.get('Replacement'))
    if kind == 'RangeInt':
        return str(int(op['Min']))
    if kind == 'RangeFloat':
        return repr(float(op['Min']))
    if kind == 'StupidFuckingLDAPTimestamp':
        return str(int((timestamp - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000))
    raise CatalogError('Unknown replacement operation: ' + str(kind))


def render_request(template, request, tid, timestamp=SAMPLE_TIME, xuid='0', session='00000000-0000-0000-0000-000000000001', seq=1, warnings=None):
    """Render one request exactly as the app does and return the parsed payload."""
    body = template
    for op in request.values():
        if not isinstance(op, dict):
            raise CatalogError('Invalid replacement operation')
        target = op.get('Target')
        if not isinstance(target, str) or not target:
            raise CatalogError('Missing replacement target')
        body = body.replace(target, operation_text(op, timestamp))
    body = (body.replace('REPLACESEQ', str(seq))
                .replace('REPLACETIME', timestamp.strftime('%Y-%m-%dT%H:%M:%S.0000000Z'))
                .replace('REPLACEXUID', xuid)
                .replace('REPLACESESSIONGUID', session))
    left = PLACEHOLDER.search(body)
    if left:
        raise CatalogError('Unresolved placeholder ' + left.group(0))
    try:
        payload = parse(body)
    except json.JSONDecodeError as error:
        # Strict JSON: the app's Newtonsoft parser tolerates trailing commas, mobile clients do not.
        hint = ' (trailing comma)' if TRAILING_COMMA.search(body) else ''
        raise CatalogError('Rendered payload is not valid JSON' + hint + ': ' + str(error)) from None
    if not isinstance(payload, dict):
        raise CatalogError('Rendered payload is not a JSON object')
    data = payload.get('data')
    base = data.get('baseData') if isinstance(data, dict) else None
    if isinstance(base, dict) and 'titleId' in base and str(base['titleId']) != tid:
        raise CatalogError('Event title ID does not match the title')
    return payload


def render_achievement(template, entry, tid, warnings=None):
    return [render_request(template, request, tid, warnings=warnings) for request in requests_of(entry)]


def dumps_canonical(value):
    """Deterministic UTF-8 JSON with sorted keys, LF and a trailing newline."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def dumps_compact(value):
    # Runtime replacement operations and Request1..RequestN are ordered. The validated
    # source files provide deterministic insertion order without sorting those keys.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

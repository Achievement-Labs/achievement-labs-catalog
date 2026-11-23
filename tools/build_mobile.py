"""Build the mobile event feed (dist/mobile/) from titles/.

Port of the app repository's tools/mobile/publish_events.py (catalogue.json) and
tools/mobile/export_event_pack.py (EventPack.json). The output format is unchanged,
so mobile clients can read it from this repository's Pages site:

  dist/mobile/catalogue.json  {"schemaVersion":1,"upstreamRelease":...,"archiveSHA256":...,
                               "generatedAt":...,"titles":{"<id>":{"template":...,
                               "recipes":{"recipes":{"<aid>":[[{"target":...,"value":...}]]}}}}}
  dist/mobile/EventPack.json  the same shape, limited to recipes all three platforms accept.
                              This is part 1 of the offline pack; further parts are
                              EventPack-2.json, EventPack-3.json, ... in the same shape.
  dist/mobile/EventPackIndex.json  {"schema":1,"revision":N,"parts":[{"file":...,"sha256":...,
                              "size":...,"titles":<count>}, ...]} listing every part in order
                              (publish.yml signs it as EventPackIndex.json.sig).

Pack parts: mobile/pack-assignment.json (committed) records which part holds each title, so
adding titles never reshuffles existing parts. Assigned titles keep their part and order; titles
not yet assigned are appended in ascending numeric ID to the last part while it stays within
PART_LIMIT (30 MiB), otherwise a new part is opened. The builder writes newly assigned titles
back to the file; commit it. Titles that leave the pack are removed from it. Every part file
must also stay within the 32 MiB client limit (FEED_LIMIT); the build fails rather than exceed it.

Differences from the legacy feed, which is built from release Events.zip assets:
  - the source is titles/ at the built commit, not a release archive;
  - upstreamRelease is "catalog-r<revision>" and archiveSHA256 is the SHA-256 of
    dist/v1/manifest.json, which identifies the exact catalog snapshot;
  - generatedAt is the commit time, so builds are reproducible;
  - like the legacy feed, catalogue.json carries only additions: titles listed in
    mobile/bundled-titles.json (the titles bundled in the app, which clients already have)
    are left out. EventPack.json carries every title that fits the pack limits.
  - every file keeps the clients' 32 MiB download limit; the build fails rather than exceed it.

python tools/build_mobile.py [--out DIR] [--bundled PATH] [--check]   (needs dist/v1 from build.py)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import ROOT, TITLES, load_index, load_template, load_title, parse, title_ids

SCID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
FEED_LIMIT = 32 * 1024 * 1024  # PublicSync.swift / Android download limit
PART_LIMIT = 30 * 1024 * 1024  # target size when assigning new titles to a pack part
BUNDLED = ROOT / 'mobile' / 'bundled-titles.json'
ASSIGNMENT = ROOT / 'mobile' / 'pack-assignment.json'


def normalize(tid, block, template):
    """Same rules as publish_events.normalize: Replace-only recipes that render with this title's identity."""
    if len(template.encode()) > 1024 * 1024:
        raise ValueError('Template limit')
    result = {}
    achievements = block.get('Achievements', {})
    if not isinstance(achievements, dict) or len(achievements) > 10000:
        raise ValueError('Achievement limit')
    for aid, entry in achievements.items():
        if not re.fullmatch(r'[0-9]{1,10}', aid):
            continue
        try:
            first = next(iter(entry.values()))
            requests = [entry] if 'ReplacementType' in first else list(entry.values())
            if not 0 < len(requests) <= 100:
                raise ValueError('Request limit')
            normalized = []
            for request in requests:
                body = template
                operations = []
                if not 0 < len(request) <= 100:
                    raise ValueError('Operation limit')
                for op in request.values():
                    target, value = op['Target'], op['Replacement']
                    if op['ReplacementType'] != 'Replace' or not isinstance(target, str) or not target or target not in body or not isinstance(value, str):
                        raise ValueError('Unsupported replacement')
                    if len(body) + body.count(target) * (len(value) - len(target)) > 2 * 1024 * 1024:
                        raise ValueError('Expansion limit')
                    body = body.replace(target, value)
                    operations.append({'target': target, 'value': value})
                for target, value in {'REPLACESEQ': '1', 'REPLACETIME': '2026-10-17T00:00:00.000Z', 'REPLACEXUID': '0',
                                      'REPLACESESSIONGUID': '00000000-0000-0000-0000-000000000001'}.items():
                    body = body.replace(target, value)
                if re.search(r'REPLACE[A-Z0-9_]+', body):
                    raise ValueError('Unresolved placeholder')
                base = parse(body.encode())['data']['baseData']
                if str(base['titleId']) != tid or not SCID.fullmatch(base['serviceConfigId']):
                    raise ValueError('Wrong identity')
                normalized.append(operations)
            result[aid] = normalized
        except (KeyError, TypeError, ValueError, StopIteration, AttributeError):
            continue
    if not result:
        raise ValueError('No supported recipes')
    return {'template': template, 'recipes': {'recipes': result}}


def build_feed(titles, tag, digest, generated, reserved=frozenset()):
    index = load_index(titles)
    if list(index)[-1] != 'SupportedTitleIDs':
        raise ValueError('SupportedTitleIDs must remain last')
    result = {}
    skipped = 0
    for tid in dict.fromkeys(map(str, index['SupportedTitleIDs'])):
        if tid in reserved:
            continue
        try:
            if not re.fullmatch(r'[0-9]{1,10}', tid) or index[tid].get('Enabled') is False:
                raise ValueError('Disabled/invalid')
            result[tid] = normalize(tid, load_title(titles, tid), load_template(titles, tid))
        except (KeyError, TypeError, ValueError, UnicodeError, OSError):
            skipped += 1
    return {'schemaVersion': 1, 'upstreamRelease': tag, 'archiveSHA256': digest, 'generatedAt': generated, 'titles': result}, skipped


def event_pack(feed):
    """Same filter as export_event_pack.export: conservative limits accepted by all platforms."""
    pack = json.loads(json.dumps(feed))
    skipped = 0
    for title in list(pack['titles']):
        value = pack['titles'][title]
        recipes = value['recipes']['recipes']
        for achievement in list(recipes):
            requests = recipes[achievement]
            compatible = len(requests) <= 20 and sum(map(len, requests)) <= 100
            for operations in requests:
                body = value['template']
                for op in operations:
                    body = body.replace(op['target'], op['value'])
                for target, replacement in {'REPLACESEQ': '1', 'REPLACETIME': '2026-01-01T00:00:00Z', 'REPLACEXUID': '0',
                                            'REPLACESESSIONGUID': '00000000-0000-0000-0000-000000000001'}.items():
                    body = body.replace(target, replacement)
                payload = json.loads(body)
                base = payload.get('data', {}).get('baseData', {})
                compatible = compatible and payload.get('name', '').startswith('Microsoft.XboxLive.T' + title + '.') and bool(base.get('name'))
            if not compatible:
                del recipes[achievement]
        if not recipes:
            del pack['titles'][title]
            skipped += 1
    return pack, skipped


def encode(value, limit=FEED_LIMIT):
    data = json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if len(data) > limit:
        raise ValueError(f'Feed is {len(data)} bytes, over the 32 MiB client limit')
    return data


def part_name(number):
    return 'EventPack.json' if number == 1 else f'EventPack-{number}.json'


def load_assignment(path):
    if not path.exists():
        return []
    value = parse(path.read_bytes())
    parts = value.get('parts') if isinstance(value, dict) else None
    if value.get('schema') != 1 or not isinstance(parts, list) or not all(isinstance(p, list) for p in parts):
        raise ValueError(f'{path.name}: expected {{"schema":1,"parts":[[...], ...]}}')
    seen = [t for part in parts for t in part]
    if not all(isinstance(t, str) and re.fullmatch(r'[0-9]{1,10}', t) for t in seen) or len(seen) != len(set(seen)):
        raise ValueError(f'{path.name}: title IDs must be unique numeric strings')
    return parts


def dump_assignment(parts):
    return (json.dumps({'schema': 1, 'parts': parts}, indent=2) + '\n').encode('utf-8')


def assign_parts(titles, previous, part_limit=PART_LIMIT):
    """Stable assignment of pack titles ({id: compact title bytes}, in feed order) to parts.

    Previously assigned titles keep their part and order (titles no longer in the pack are
    dropped); new titles go in ascending numeric ID into the last part while it stays within
    part_limit, else into a new part. Sizes count each title's `"id":value,` bytes.
    """
    parts = [[t for t in part if t in titles] for part in previous]
    while parts and not parts[-1]:
        parts.pop()
    assigned = {t for part in parts for t in part}
    def cost(t):
        return len(json.dumps(t)) + 2 + titles[t]
    used = sum(cost(t) for t in parts[-1]) if parts else 0
    for tid in sorted((t for t in titles if t not in assigned), key=int):
        if not parts or (parts[-1] and used + cost(tid) > part_limit):
            parts.append([])
            used = 0
        parts[-1].append(tid)
        used += cost(tid)
    return parts


def load_bundled(path=BUNDLED):
    if not path.exists():
        return frozenset()
    ids = parse(path.read_bytes())['bundledTitleIds']
    if not all(isinstance(t, str) and re.fullmatch(r'[0-9]{1,10}', t) for t in ids):
        raise ValueError('bundledTitleIds must be numeric strings')
    return frozenset(ids)


def build(titles, out, manifest, reserved=None, assignment=ASSIGNMENT, part_limit=PART_LIMIT, hard_limit=FEED_LIMIT,
          check=False):
    """Write catalogue.json, the pack parts and EventPackIndex.json; returns (feed, pack, index).

    `pack` is the whole offline pack (all parts merged). New part assignments are written back
    to `assignment` (mobile/pack-assignment.json), which must be committed. With check=True (CI and
    publish) the file is never written: a change raises ValueError instead.
    """
    reserved = load_bundled() if reserved is None else reserved
    digest = hashlib.sha256((out / 'v1' / 'manifest.json').read_bytes()).hexdigest()
    tag = 'catalog-r' + str(manifest['revision'])
    full, _ = build_feed(titles, tag, digest, manifest['generatedUtc'])
    feed = dict(full, titles={tid: value for tid, value in full['titles'].items() if tid not in reserved})
    skipped = sum(1 for tid in title_ids(load_index(titles)) if tid not in reserved) - len(feed['titles'])
    # As in export_event_pack.py, the pack is built from every title, not only additions.
    pack, pack_skipped = event_pack(full)
    sizes = {tid: len(json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
             for tid, value in pack['titles'].items()}
    previous = load_assignment(assignment)
    parts = assign_parts(sizes, previous, part_limit)
    if parts != previous and check:
        raise ValueError(f'{assignment.name} is out of date (new or removed pack titles). Run '
                         f'"python tools/build.py" locally and commit mobile/pack-assignment.json.')
    if parts != previous:
        assignment.parent.mkdir(parents=True, exist_ok=True)
        assignment.write_bytes(dump_assignment(parts))
        print(f'mobile: updated {assignment.name}; commit it so pack parts stay stable')
    mobile = out / 'mobile'
    mobile.mkdir(parents=True, exist_ok=True)
    for stale in mobile.glob('EventPack-*.json'):
        stale.unlink()
    (mobile / 'catalogue.json').write_bytes(encode(feed, hard_limit))
    entries = []
    for number, part in enumerate(parts or [[]], 1):
        data = encode(dict(pack, titles={tid: pack['titles'][tid] for tid in part}), hard_limit)
        (mobile / part_name(number)).write_bytes(data)
        if len(data) > part_limit:
            print(f'mobile: warning: {part_name(number)} is {len(data)} bytes, above the {part_limit}-byte part target '
                  f'(its assigned titles are kept; new titles go to later parts)')
        entries.append({'file': part_name(number), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                        'titles': len(part)})
    index = {'schema': 1, 'revision': manifest['revision'], 'parts': entries}
    (mobile / 'EventPackIndex.json').write_bytes((json.dumps(index, indent=2) + '\n').encode('utf-8'))
    print(f'mobile: catalogue.json {len(feed["titles"])} titles ({skipped} skipped); '
          f'EventPack {len(pack["titles"])} titles in {len(entries)} part(s) ({pack_skipped} dropped by pack limits): '
          + ', '.join(f'{e["file"]} {e["titles"]} titles {e["size"]} bytes' for e in entries))
    return feed, pack, index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--titles', type=Path, default=TITLES)
    parser.add_argument('--out', type=Path, default=ROOT / 'dist')
    parser.add_argument('--bundled', type=Path, default=BUNDLED, help='JSON {"bundledTitleIds":[...]} left out of catalogue.json')
    parser.add_argument('--assignment', type=Path, default=ASSIGNMENT, help='committed pack part assignment')
    parser.add_argument('--check', action='store_true', help='fail instead of updating the assignment file (CI)')
    args = parser.parse_args(argv)
    manifest = parse((args.out / 'v1' / 'manifest.json').read_bytes())
    reserved = load_bundled(args.bundled)
    build(args.titles, args.out, manifest, reserved, args.assignment, check=args.check)
    return 0


if __name__ == '__main__':
    sys.exit(main())

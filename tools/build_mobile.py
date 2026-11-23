"""Build the mobile event feed (dist/mobile/) from titles/.

Port of the app repository's tools/mobile/publish_events.py (catalogue.json) and
tools/mobile/export_event_pack.py (EventPack.json). The output format is unchanged,
so mobile clients can read it from this repository's Pages site:

  dist/mobile/catalogue.json  {"schemaVersion":1,"upstreamRelease":...,"archiveSHA256":...,
                               "generatedAt":...,"titles":{"<id>":{"template":...,
                               "recipes":{"recipes":{"<aid>":[[{"target":...,"value":...}]]}}}}}
  dist/mobile/EventPack.json  the same shape, limited to recipes all three platforms accept.

Differences from the legacy feed, which is built from release Events.zip assets:
  - the source is titles/ at the built commit, not a release archive;
  - upstreamRelease is "catalog-r<revision>" and archiveSHA256 is the SHA-256 of
    dist/v1/manifest.json, which identifies the exact catalog snapshot;
  - generatedAt is the commit time, so builds are reproducible;
  - like the legacy feed, catalogue.json carries only additions: titles listed in
    mobile/bundled-titles.json (the titles bundled in the app, which clients already have)
    are left out. EventPack.json carries every title that fits the pack limits.
  - both files keep the clients' 32 MiB download limit; the build fails rather than exceed it.

python tools/build_mobile.py [--out DIR] [--bundled PATH]   (needs dist/v1 from build.py)
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
BUNDLED = ROOT / 'mobile' / 'bundled-titles.json'


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


def encode(value):
    data = json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if len(data) > FEED_LIMIT:
        raise ValueError(f'Feed is {len(data)} bytes, over the 32 MiB client limit')
    return data


def load_bundled(path=BUNDLED):
    if not path.exists():
        return frozenset()
    ids = parse(path.read_bytes())['bundledTitleIds']
    if not all(isinstance(t, str) and re.fullmatch(r'[0-9]{1,10}', t) for t in ids):
        raise ValueError('bundledTitleIds must be numeric strings')
    return frozenset(ids)


def build(titles, out, manifest, reserved=None):
    reserved = load_bundled() if reserved is None else reserved
    digest = hashlib.sha256((out / 'v1' / 'manifest.json').read_bytes()).hexdigest()
    tag = 'catalog-r' + str(manifest['revision'])
    full, _ = build_feed(titles, tag, digest, manifest['generatedUtc'])
    feed = dict(full, titles={tid: value for tid, value in full['titles'].items() if tid not in reserved})
    skipped = sum(1 for tid in title_ids(load_index(titles)) if tid not in reserved) - len(feed['titles'])
    # As in export_event_pack.py, the pack is built from every title, not only additions.
    pack, pack_skipped = event_pack(full)
    mobile = out / 'mobile'
    mobile.mkdir(parents=True, exist_ok=True)
    (mobile / 'catalogue.json').write_bytes(encode(feed))
    (mobile / 'EventPack.json').write_bytes(encode(pack))
    print(f'mobile: catalogue.json {len(feed["titles"])} titles ({skipped} skipped); '
          f'EventPack.json {len(pack["titles"])} titles ({pack_skipped} dropped by pack limits)')
    return feed, pack


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--titles', type=Path, default=TITLES)
    parser.add_argument('--out', type=Path, default=ROOT / 'dist')
    parser.add_argument('--bundled', type=Path, default=BUNDLED, help='JSON {"bundledTitleIds":[...]} left out of catalogue.json')
    args = parser.parse_args(argv)
    manifest = parse((args.out / 'v1' / 'manifest.json').read_bytes())
    reserved = load_bundled(args.bundled)
    build(args.titles, args.out, manifest, reserved)
    return 0


if __name__ == '__main__':
    sys.exit(main())

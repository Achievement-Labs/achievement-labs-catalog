"""Build the published catalog into dist/.

python tools/build.py [--titles DIR] [--out DIR] [--no-mobile]

Writes dist/v1/manifest.json plus content-addressed dist/v1/titles/<id>.<sha12>.json
bundles, then (unless --no-mobile) the mobile feed under dist/mobile/ via build_mobile.py.
The output depends only on the checked-out commit, so two builds are byte-identical.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import ROOT, TITLES, dumps_canonical, dumps_compact, load_index, load_stats, load_title, title_ids

SCHEMA = 1


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def git_state(root, titles):
    """Catalog revision, HEAD sha, HEAD commit time and per-title revisions.

    Revision is `git rev-list --count HEAD` (monotonic on main). A title's revision is the
    revision of the last commit that touched titles/<id>/. CI must check out full history.
    """
    revision = int(git(root, 'rev-list', '--count', 'HEAD'))
    commit = git(root, 'rev-parse', 'HEAD')
    stamp = int(git(root, 'log', '-1', '--format=%ct', 'HEAD'))
    generated = datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    relative = titles.resolve().relative_to(Path(git(root, 'rev-parse', '--show-toplevel')).resolve()).as_posix()
    last = {}
    log = git(root, 'log', '--format=@%H', '--name-only', 'HEAD', '--', relative)
    sha = None
    for line in log.splitlines():
        if line.startswith('@'):
            sha = line[1:]
        elif line.startswith(relative + '/'):
            parts = line[len(relative) + 1:].split('/')
            if len(parts) > 1:
                last.setdefault(parts[0], sha)
    counts = {sha: int(git(root, 'rev-list', '--count', sha)) for sha in set(last.values())}
    return revision, commit, generated, {tid: counts[sha] for tid, sha in last.items()}


def build_v1(titles, out, revision, commit, generated, title_revisions):
    v1 = out / 'v1'
    if v1.exists():
        shutil.rmtree(v1)
    (v1 / 'titles').mkdir(parents=True)
    index = load_index(titles)
    entries = {}
    for tid in sorted(title_ids(index), key=int):
        block = load_title(titles, tid)
        rev = title_revisions.get(tid, revision)
        enabled = block.get('Enabled') is not False
        entry = {
            'revision': rev,
            'name': block.get('TitleName') or '',
            'enabled': enabled,
            'fullySupported': block.get('FullySupported') is True,
            'partiallySupported': block.get('PartiallySupported') is True,
            'achievements': len(block.get('Achievements') or {}),
        }
        if enabled:
            template = (titles / tid / (tid + '.json')).read_bytes().decode('utf-8')
            bundle = dumps_compact({'titleId': tid, 'revision': rev, 'data': block, 'template': template,
                                    'stats': load_stats(titles, tid)}) + b'\n'
            digest = hashlib.sha256(bundle).hexdigest()
            path = f'titles/{tid}.{digest[:12]}.json'
            (v1 / path).write_bytes(bundle)
            entry.update(sha256=digest, size=len(bundle), path=path)
        entries[tid] = entry
    manifest = {'schema': SCHEMA, 'revision': revision, 'commit': commit, 'generatedUtc': generated, 'titles': entries}
    (v1 / 'manifest.json').write_bytes(dumps_canonical(manifest))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--titles', type=Path, default=TITLES)
    parser.add_argument('--out', type=Path, default=ROOT / 'dist')
    parser.add_argument('--no-mobile', action='store_true')
    args = parser.parse_args(argv)
    state = git_state(ROOT, args.titles)
    manifest = build_v1(args.titles, args.out, *state)
    enabled = sum(1 for t in manifest['titles'].values() if t['enabled'])
    print(f'v1: revision {manifest["revision"]}, {len(manifest["titles"])} titles ({enabled} enabled bundles)')
    if not args.no_mobile:
        import build_mobile
        build_mobile.build(args.titles, args.out, manifest)
    return 0


if __name__ == '__main__':
    sys.exit(main())

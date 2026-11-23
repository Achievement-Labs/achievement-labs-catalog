import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from fixtures import block, write_catalog, write_json
import build
import build_mobile


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True)


def record(root, message):
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', message)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.titles = self.root / 'titles'
        git(self.root, 'init', '-q')
        write_catalog(self.titles, stats={'Stats': {'S': 1}})
        disabled = block(TitleName='Off', Enabled=False)
        write_json(self.titles / '8' / 'Data.json', {'8': disabled, 'SupportedTitleIDs': ['8']})
        (self.titles / '8' / '8.json').write_bytes(b'{}\n')
        seven = json.loads((self.titles / 'index.json').read_text())['7']
        write_json(self.titles / 'index.json', {'7': seven, '8': {k: v for k, v in disabled.items() if k != 'Achievements'},
                                                'SupportedTitleIDs': ['7', '8']})
        record(self.root, 'one')
        (self.root / 'other.txt').write_text('x')
        record(self.root, 'two')

    def tearDown(self):
        self.tmp.cleanup()

    def run_build(self, out):
        return build.build_v1(self.titles, out, *build.git_state(self.root, self.titles))

    def test_manifest_and_bundles(self):
        out = self.root / 'dist'
        manifest = self.run_build(out)
        raw = (out / 'v1' / 'manifest.json').read_bytes()
        self.assertTrue(raw.endswith(b'\n') and b'\r' not in raw)
        self.assertEqual(json.loads(raw), manifest)
        self.assertEqual(list(json.loads(raw)), sorted(json.loads(raw)))
        self.assertEqual(manifest['revision'], 2)
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual(len(manifest['commit']), 40)
        seven = manifest['titles']['7']
        self.assertEqual(seven['revision'], 1)  # the last commit touching titles/7 is commit 1
        self.assertEqual(seven['achievements'], 2)
        bundle = (out / 'v1' / seven['path']).read_bytes()
        self.assertEqual(hashlib.sha256(bundle).hexdigest(), seven['sha256'])
        self.assertEqual(len(bundle), seven['size'])
        self.assertEqual(seven['path'], 'titles/7.' + seven['sha256'][:12] + '.json')
        parsed = json.loads(bundle)
        self.assertEqual(parsed['titleId'], '7')
        self.assertEqual(parsed['revision'], 1)
        self.assertEqual(parsed['stats'], {'Stats': {'S': 1}})
        self.assertEqual(parsed['template'], (self.titles / '7' / '7.json').read_text())
        self.assertEqual(parsed['data']['TitleName'], 'Seven')
        eight = manifest['titles']['8']
        self.assertFalse(eight['enabled'])
        self.assertNotIn('path', eight)
        self.assertEqual(len(list((out / 'v1' / 'titles').iterdir())), 1)

    def test_reproducible(self):
        self.run_build(self.root / 'a')
        self.run_build(self.root / 'b')

        def files(d):
            return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob('*')) if p.is_file()}
        self.assertEqual(files(self.root / 'a'), files(self.root / 'b'))

    def test_title_revision_advances(self):
        write_catalog(self.titles, block(TitleName='Seven!'), stats={'Stats': {'S': 1}})
        index = json.loads((self.titles / 'index.json').read_text())
        index['8'] = {'TitleId': 7, 'TitleName': 'Off', 'Enabled': False, 'FullySupported': True}
        index['SupportedTitleIDs'] = index.pop('SupportedTitleIDs') + ['8']
        write_json(self.titles / 'index.json', index)
        record(self.root, 'three')
        manifest = self.run_build(self.root / 'dist')
        self.assertEqual(manifest['revision'], 3)
        self.assertEqual(manifest['titles']['7']['revision'], 3)
        self.assertEqual(manifest['titles']['8']['revision'], 1)

    def test_mobile_feed(self):
        out = self.root / 'dist'
        manifest = self.run_build(out)
        feed, pack = build_mobile.build(self.titles, out, manifest, reserved=frozenset())
        self.assertEqual(feed['schemaVersion'], 1)
        self.assertEqual(feed['upstreamRelease'], 'catalog-r2')
        self.assertEqual(feed['generatedAt'], manifest['generatedUtc'])
        self.assertEqual(list(feed['titles']), ['7'])  # the disabled title 8 is left out
        recipes = feed['titles']['7']['recipes']['recipes']
        self.assertEqual(recipes['1'], [[{'target': 'REPLACEEVENTNAME', 'value': 'Unlock'}, {'target': 'REPLACEID', 'value': '1'}]])
        self.assertNotIn('2', recipes)  # mobile clients accept string Replacement values only
        self.assertIn('7', pack['titles'])
        reserved, _ = build_mobile.build(self.titles, out, manifest, reserved=frozenset({'7'}))
        self.assertEqual(reserved['titles'], {})
        self.assertEqual(set(json.loads((out / 'mobile' / 'EventPack.json').read_bytes())['titles']), {'7'})


if __name__ == '__main__':
    unittest.main()

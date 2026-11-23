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

    def assert_invariants(self, manifest):
        for tid, entry in manifest['titles'].items():
            self.assertLessEqual(entry['revision'], manifest['revision'], tid)
            self.assertGreaterEqual(entry['revision'], 1, tid)

    def rewrite_seven(self, name, stat):
        write_catalog(self.titles, block(TitleName=name), stats={'Stats': {'S': stat}})
        index = json.loads((self.titles / 'index.json').read_text())
        index['8'] = {'TitleId': 7, 'TitleName': 'Off', 'Enabled': False, 'FullySupported': True}
        index['SupportedTitleIDs'] = index.pop('SupportedTitleIDs') + ['8']
        write_json(self.titles / 'index.json', index)

    def test_no_ff_merge_credits_the_merge_commit(self):
        git(self.root, 'checkout', '-q', '-b', 'side')
        self.rewrite_seven('Side', 1)
        record(self.root, 'side one')
        (self.root / 'side.txt').write_text('y')
        record(self.root, 'side two')
        git(self.root, 'checkout', '-q', '-')
        (self.root / 'main.txt').write_text('z')
        record(self.root, 'main three')
        before = self.run_build(self.root / 'before')
        git(self.root, 'merge', '-q', '--no-ff', '-m', 'merge side', 'side')
        after = self.run_build(self.root / 'after')
        self.assertEqual(after['revision'], 4)  # first-parent: one, two, main three, merge
        self.assertEqual(after['titles']['7']['revision'], 4)
        self.assertEqual(after['titles']['8']['revision'], 1)
        self.assertGreater(after['titles']['7']['revision'], before['titles']['7']['revision'])
        self.assert_invariants(before)
        self.assert_invariants(after)

    def test_revision_never_decreases_when_content_changes(self):
        previous = self.run_build(self.root / 'r0')
        self.assert_invariants(previous)
        for number in range(1, 4):
            self.rewrite_seven(f'Seven {number}', number)
            record(self.root, f'change {number}')
            current = self.run_build(self.root / f'r{number}')
            self.assertNotEqual(current['titles']['7']['sha256'], previous['titles']['7']['sha256'])
            self.assertGreater(current['titles']['7']['revision'], previous['titles']['7']['revision'])
            self.assertGreaterEqual(current['titles']['8']['revision'], previous['titles']['8']['revision'])
            self.assert_invariants(current)
            previous = current

    def test_mobile_feed(self):
        out = self.root / 'dist'
        manifest = self.run_build(out)
        assignment = self.root / 'pack-assignment.json'
        feed, pack, index = build_mobile.build(self.titles, out, manifest, reserved=frozenset(), assignment=assignment)
        self.assertEqual(feed['schemaVersion'], 1)
        self.assertEqual(feed['upstreamRelease'], 'catalog-r2')
        self.assertEqual(feed['generatedAt'], manifest['generatedUtc'])
        self.assertEqual(list(feed['titles']), ['7'])  # the disabled title 8 is left out
        recipes = feed['titles']['7']['recipes']['recipes']
        self.assertEqual(recipes['1'], [[{'target': 'REPLACEEVENTNAME', 'value': 'Unlock'}, {'target': 'REPLACEID', 'value': '1'}]])
        self.assertNotIn('2', recipes)  # mobile clients accept string Replacement values only
        self.assertIn('7', pack['titles'])
        part = (out / 'mobile' / 'EventPack.json').read_bytes()
        self.assertEqual(index, {'schema': 1, 'revision': 2, 'parts': [
            {'file': 'EventPack.json', 'sha256': hashlib.sha256(part).hexdigest(), 'size': len(part), 'titles': 1}]})
        self.assertEqual(json.loads((out / 'mobile' / 'EventPackIndex.json').read_bytes()), index)
        self.assertEqual(json.loads(assignment.read_bytes()), {'schema': 1, 'parts': [['7']]})
        reserved, _, _ = build_mobile.build(self.titles, out, manifest, reserved=frozenset({'7'}), assignment=assignment)
        self.assertEqual(reserved['titles'], {})
        self.assertEqual(set(json.loads((out / 'mobile' / 'EventPack.json').read_bytes())['titles']), {'7'})

    def test_pack_part_size_guard(self):
        out = self.root / 'dist'
        manifest = self.run_build(out)
        assignment = self.root / 'pack-assignment.json'
        build_mobile.build(self.titles, out, manifest, reserved=frozenset(), assignment=assignment)
        size = len((out / 'mobile' / 'EventPack.json').read_bytes())
        with self.assertRaisesRegex(ValueError, 'over the 32 MiB client limit'):
            build_mobile.build(self.titles, out, manifest, reserved=frozenset(), assignment=assignment, hard_limit=size - 1)

    def test_check_mode_refuses_to_update_assignment(self):
        out = self.root / 'dist'
        manifest = self.run_build(out)
        assignment = self.root / 'pack-assignment.json'
        with self.assertRaisesRegex(ValueError, 'commit mobile/pack-assignment.json'):
            build_mobile.build(self.titles, out, manifest, reserved=frozenset(), assignment=assignment, check=True)
        self.assertFalse(assignment.exists())
        build_mobile.build(self.titles, out, manifest, reserved=frozenset(), assignment=assignment)
        before = assignment.read_bytes()
        build_mobile.build(self.titles, out, manifest, reserved=frozenset(), assignment=assignment, check=True)
        self.assertEqual(assignment.read_bytes(), before)


class PackAssignmentTests(unittest.TestCase):
    def cost(self, sizes, part):
        return sum(len(json.dumps(t)) + 2 + sizes[t] for t in part)

    def test_splits_by_part_limit_in_numeric_order(self):
        sizes = {'30': 40, '4': 40, '200': 40, '1': 40}
        parts = build_mobile.assign_parts(sizes, [], part_limit=100)
        self.assertEqual(parts, [['1', '4'], ['30', '200']])
        self.assertTrue(all(self.cost(sizes, p) <= 100 for p in parts))

    def test_oversized_title_gets_its_own_part(self):
        self.assertEqual(build_mobile.assign_parts({'1': 10, '2': 500, '3': 10}, [], part_limit=100), [['1'], ['2'], ['3']])

    def test_stable_when_titles_are_added(self):
        sizes = {'9': 40, '5': 40, '7': 40}
        first = build_mobile.assign_parts(sizes, [['9', '5'], ['7']], part_limit=100)
        self.assertEqual(first, [['9', '5'], ['7']])  # existing order kept, even if not numeric
        grown = build_mobile.assign_parts(dict(sizes, **{'3': 40, '1': 40, '8': 40}), first, part_limit=100)
        self.assertEqual(grown[:2], [['9', '5'], ['7', '1']])  # new titles fill the last part first
        self.assertEqual(grown[2:], [['3', '8']])
        self.assertEqual(build_mobile.assign_parts(dict(sizes, **{'3': 40, '1': 40, '8': 40}), grown, part_limit=100), grown)

    def test_removed_titles_leave_other_parts_in_place(self):
        self.assertEqual(build_mobile.assign_parts({'1': 10, '3': 10}, [['1', '2'], ['3']], part_limit=100), [['1'], ['3']])

    def test_assignment_file_round_trip_and_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.json'
            self.assertEqual(build_mobile.load_assignment(path), [])
            path.write_bytes(build_mobile.dump_assignment([['1'], ['2']]))
            self.assertEqual(build_mobile.load_assignment(path), [['1'], ['2']])
            path.write_bytes(build_mobile.dump_assignment([['1'], ['1']]))
            with self.assertRaises(ValueError):
                build_mobile.load_assignment(path)


if __name__ == '__main__':
    unittest.main()

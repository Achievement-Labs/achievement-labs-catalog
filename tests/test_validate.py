import tempfile
import unittest
from pathlib import Path

from fixtures import TEMPLATE, block, op, write_catalog, write_json
from catalog import CatalogError, render_achievement, render_request
from validate import validate


class RenderTests(unittest.TestCase):
    def test_renders_like_the_app(self):
        payloads = render_achievement(TEMPLATE, block()['Achievements']['1'], '7')
        self.assertEqual(payloads[0]['data']['baseData']['properties']['AchievementId'], 1)
        self.assertEqual(payloads[0]['data']['baseData']['userId'], '0')
        # A numeric Replacement renders through JToken.ToString().
        self.assertEqual(render_achievement(TEMPLATE, block()['Achievements']['2'], '7')[0]['data']['baseData']['properties']['AchievementId'], 2)

    def test_unresolved_placeholder(self):
        with self.assertRaisesRegex(CatalogError, 'REPLACEID'):
            render_request(TEMPLATE, {'E': op('REPLACEEVENTNAME', 'x')}, '7')

    def test_wrong_title(self):
        with self.assertRaisesRegex(CatalogError, 'title ID'):
            render_request(TEMPLATE, {'I': op('REPLACEID', '1'), 'E': op('REPLACEEVENTNAME', 'x')}, '8')

    def test_trailing_comma_is_an_error(self):
        with self.assertRaisesRegex(CatalogError, 'trailing comma'):
            render_request('{"a":REPLACEID,}', {'I': op('REPLACEID', '1')}, '7')
        with self.assertRaises(CatalogError):
            render_request('{"a":REPLACEID', {'I': op('REPLACEID', '1')}, '7')

    def test_range_and_ldap(self):
        body = render_request('{"a":REPLACEA,"b":REPLACEB}', {
            'A': {'ReplacementType': 'RangeInt', 'Target': 'REPLACEA', 'Min': 3, 'Max': 9},
            'B': {'ReplacementType': 'StupidFuckingLDAPTimestamp', 'Target': 'REPLACEB'}}, '7')
        self.assertEqual(body['a'], 3)
        self.assertGreater(body['b'], 10 ** 17)


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.titles = Path(self.tmp.name) / 'titles'

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self):
        return '\n'.join(validate(self.titles))

    def test_valid_catalog(self):
        write_catalog(self.titles, stats={'Stats': {}})
        self.assertEqual(validate(self.titles), [])

    def test_supported_ids_must_be_last(self):
        write_catalog(self.titles)
        write_json(self.titles / '7' / 'Data.json', {'SupportedTitleIDs': ['7'], '7': block()})
        self.assertIn('must be exactly', self.errors())

    def test_index_without_mappings_and_matching_folders(self):
        write_catalog(self.titles)
        write_json(self.titles / 'index.json', {'7': block(), 'SupportedTitleIDs': ['7']})
        self.assertIn('must not contain achievement mappings', self.errors())
        write_catalog(self.titles)
        (self.titles / '9').mkdir()
        self.assertIn('does not match title folders', self.errors())

    def test_forbidden_operations_and_fields(self):
        raw = block()
        raw['Achievements']['1']['Request1']['E'] = {'ReplacementType': 'RawBody', 'Target': 'x', 'Replacement': '{}'}
        write_catalog(self.titles, raw)
        self.assertIn('forbidden operation RawBody', self.errors())
        write_catalog(self.titles, block(TemplateReplacement={}))
        self.assertIn('TemplateReplacement', self.errors())
        write_catalog(self.titles, block(ResearchNotes='x'))
        self.assertIn('non-runtime fields', self.errors())

    def test_line_endings_and_final_newline(self):
        write_catalog(self.titles)
        path = self.titles / '7' / '7.json'
        path.write_bytes(TEMPLATE.replace('\n', '\r\n').encode())
        self.assertIn('CRLF', self.errors())
        path.write_bytes(TEMPLATE.rstrip('\n').encode())
        self.assertIn('does not end with', self.errors())
        path.write_bytes(b'\xef\xbb\xbf' + TEMPLATE.encode())
        self.assertIn('BOM', self.errors())

    def test_unrenderable_recipe(self):
        raw = block()
        raw['Achievements']['1']['Request1'].pop('I')
        write_catalog(self.titles, raw)
        self.assertIn('Unresolved placeholder REPLACEID', self.errors())

    def test_index_and_title_disagree(self):
        write_catalog(self.titles)
        write_json(self.titles / 'index.json', {'7': {'TitleName': 'Other'}, 'SupportedTitleIDs': ['7']})
        self.assertIn('disagree', self.errors())

    def test_duplicate_property(self):
        write_catalog(self.titles)
        (self.titles / 'index.json').write_bytes(b'{"7":{},"7":{},"SupportedTitleIDs":["7"]}\n')
        self.assertIn('Duplicate', self.errors())


if __name__ == '__main__':
    unittest.main()

"""Unit tests for topview site-pixels config file and canonical validator."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from harbor.cell_site_map import canonical_cell_slot_specs, canonical_cell_wire_refs
from harbor.topview_site_pixels import (
    DEFAULT_SITE_PIXELS_PATH,
    SCHEMA_ID,
    load_topview_site_pixels,
    validate_topview_site_pixels,
)


class TopviewSitePixelsTests(unittest.TestCase):
    def test_committed_config_covers_all_canonical_slots_wire_refs_and_belt(self):
        loaded = load_topview_site_pixels(DEFAULT_SITE_PIXELS_PATH)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['schema'], SCHEMA_ID)
        self.assertEqual(loaded['source_id'], 'omx_overhead')
        self.assertEqual(loaded['image'], [640, 480])
        self.assertEqual(loaded['status'], 'planned_unverified')
        self.assertTrue(loaded['frame_source'])

        points_by_site = {pt['site']: pt for pt in loaded['points']}
        expected_slots = set(canonical_cell_slot_specs().keys())
        actual_slots = {site for site, pt in points_by_site.items() if pt['kind'] == 'slot'}
        self.assertEqual(actual_slots, expected_slots)

        all_refs = {ref for pt in loaded['points'] if pt['kind'] == 'slot' for ref in pt['wire_refs']}
        self.assertEqual(all_refs, canonical_cell_wire_refs())

        # Both temp plates and the yard are in the live top frame; the stand is drawn where ROSY plans it.
        for row in (1, 2, 3):
            self.assertEqual(len(points_by_site[f'temp_a:r{row}-s1']['roi']), 4)
            self.assertEqual(len(points_by_site[f'temp_b:r{row}-s1']['roi']), 4)
        self.assertEqual(points_by_site['stand:r1-s1']['roi'], [410.0, 30.0, 570.0, 125.0])  # planned, not yet installed
        self.assertEqual(points_by_site['belt']['kind'], 'pick')
        self.assertEqual(points_by_site['belt']['point'], [349.0, 101.0])

    def test_non_canonical_sites_and_wire_refs_are_discarded_and_logged(self):
        base = json.loads(DEFAULT_SITE_PIXELS_PATH.read_text(encoding='utf-8'))
        doc = copy.deepcopy(base)
        doc['points'] = [
            {
                'site': 'yard:r1-s1',
                'label': '야드 1행 1',
                'wire_refs': ['yard-r1-c1-l1', 'yard-r1-c1-l2', 'yard-r1-c1-l3'],
                'roi': [425, 271, 480, 380],
            },
            {
                'site': 'yard:r9-s1',
                'label': '없는 자리',
                'wire_refs': ['yard-r9-c1-l1'],
                'roi': [10, 10, 50, 50],
            },
            {
                'site': 'yard:r2-s1',
                'label': 'wire_ref 불일치',
                'wire_refs': ['yard-r2-c4-l1', 'yard-r2-c4-l2', 'yard-r2-c4-l3'],
                'roi': [370, 271, 420, 380],
            },
            {
                'site': 'yard:r3-s1',
                'label': '범위 밖 ROI',
                'wire_refs': ['yard-r3-c1-l1', 'yard-r3-c1-l2', 'yard-r3-c1-l3'],
                'roi': [315, 271, 900, 380],
            },
            {
                'site': 'belt',
                'label': '컨베이어(집기)',
                'kind': 'pick',
                'point': [341, 112],
            },
        ]
        with self.assertLogs('harbor.topview_site_pixels', level='WARNING') as logs:
            validated = validate_topview_site_pixels(doc)
        self.assertIsNotNone(validated)
        self.assertEqual([pt['site'] for pt in validated['points']], ['yard:r1-s1', 'belt'])
        joined = '\n'.join(logs.output)
        self.assertIn('yard:r9-s1', joined)
        self.assertIn('yard:r2-s1', joined)
        self.assertIn('yard:r3-s1', joined)

    def test_missing_or_wrong_schema_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing.json'
            with self.assertLogs('harbor.topview_site_pixels', level='WARNING'):
                self.assertIsNone(load_topview_site_pixels(missing))
            bad = Path(tmp) / 'bad.json'
            bad.write_text(json.dumps({'schema': 'wrong.v1'}), encoding='utf-8')
            with self.assertLogs('harbor.topview_site_pixels', level='WARNING'):
                self.assertIsNone(load_topview_site_pixels(bad))


if __name__ == '__main__':
    unittest.main()

"""Unit tests for topview site-pixels config file and canonical validator."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from harbor.cell_site_map import canonical_cell_slot_specs, canonical_cell_wire_refs
from harbor.topview_site_pixels import (
    DEFAULT_ARM_S_PIXELS_PATH,
    DEFAULT_SIDE_PIXELS_PATH,
    DEFAULT_SITE_PIXELS_PATH,
    SCHEMA_ID,
    load_arm_s_site_pixels,
    load_sideview_site_pixels,
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

        # Temp plates use 4-point fanned polygons; yard & stand use ROI boxes; belt uses pick point.
        for row in (1, 2, 3):
            self.assertEqual(len(points_by_site[f'temp_a:r{row}-s1']['polygon']), 4)
            self.assertEqual(len(points_by_site[f'temp_b:r{row}-s1']['polygon']), 4)
            self.assertIsNone(points_by_site[f'temp_a:r{row}-s1']['roi'])
            self.assertIsNone(points_by_site[f'temp_b:r{row}-s1']['roi'])
        self.assertEqual(points_by_site['stand:r1-s1']['roi'], [408.0, 60.0, 517.0, 134.0])
        self.assertEqual(points_by_site['belt']['kind'], 'pick')
        self.assertEqual(points_by_site['belt']['point'], [310.0, 103.0])

    def test_polygon_validation_accepts_canonical_quad_and_rejects_bad_polygons(self):
        base = json.loads(DEFAULT_SITE_PIXELS_PATH.read_text(encoding='utf-8'))
        doc = copy.deepcopy(base)
        doc['points'] = [
            {
                'site': 'temp_a:r1-s1',
                'label': '임시 A 1레인',
                'wire_refs': ['temp_a-r1-c1-l1', 'temp_a-r1-c1-l2', 'temp_a-r1-c1-l3'],
                'polygon': [[521, 376], [559, 370], [595, 476], [552, 479]],
            },
            {
                'site': 'temp_a:r2-s1',
                'label': '범위 밖 다각형',
                'wire_refs': ['temp_a-r2-c1-l1', 'temp_a-r2-c1-l2', 'temp_a-r2-c1-l3'],
                'polygon': [[468, 381], [700, 380], [528, 479], [476, 479]],
            },
            {
                'site': 'temp_a:r3-s1',
                'label': '일직선 다각형',
                'wire_refs': ['temp_a-r3-c1-l1', 'temp_a-r3-c1-l2', 'temp_a-r3-c1-l3'],
                'polygon': [[10, 10], [20, 20], [30, 30]],
            },
            {
                'site': 'temp_x:r1-s1',
                'label': '비정본 구역 다각형',
                'wire_refs': ['temp_a-r1-c1-l1'],
                'polygon': [[10, 10], [50, 10], [50, 50], [10, 50]],
            },
        ]
        with self.assertLogs('harbor.topview_site_pixels', level='WARNING') as logs:
            validated = validate_topview_site_pixels(doc)
        self.assertEqual([pt['site'] for pt in validated['points']], ['temp_a:r1-s1'])
        self.assertEqual(len(validated['points'][0]['polygon']), 4)
        joined = '\n'.join(logs.output)
        for bad_site in ('temp_a:r2-s1', 'temp_a:r3-s1', 'temp_x:r1-s1'):
            self.assertIn(bad_site, joined)

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


class SideviewSitePixelsTests(unittest.TestCase):
    def test_committed_side_config_lists_yard_tiers_only_and_excludes_temp_plates(self):
        loaded = load_sideview_site_pixels()
        self.assertEqual((loaded['source_id'], loaded['image'], loaded['status']),
                         ('omx_side', [640, 480], 'planned_unverified'))
        self.assertTrue(loaded['reference_frame'])
        by_site = {pt['site']: pt for pt in loaded['points']}
        self.assertEqual(len(by_site), 9)
        self.assertTrue(all(pt['zone'] == 'yard' for pt in loaded['points']))
        self.assertFalse(any(pt['zone'] in ('temp_a', 'temp_b') for pt in loaded['points']))
        self.assertEqual(by_site['side:yard-r2-t3']['wire_refs'], ['yard-r2-c1-l3', 'yard-r2-c4-l3'])
        # Bottom container (t1) sits higher in the frame than the old 375..460 range.
        self.assertEqual(by_site['side:yard-r2-t1']['roi'][1:], [295.0, 312.0, 385.0])
        self.assertEqual(by_site['side:yard-r2-t2']['roi'][1:], [210.0, 312.0, 295.0])
        self.assertEqual(by_site['side:yard-r2-t3']['roi'][1:], [125.0, 312.0, 210.0])
        refs = {ref for pt in loaded['points'] for ref in pt['wire_refs']}
        self.assertEqual(len(refs), 18)  # yard 18 only (temp_a/temp_b excluded on side camera)
        self.assertTrue(refs < canonical_cell_wire_refs())
        self.assertTrue(all(pt['kind'] == 'tier' and len(pt['roi']) == 4 for pt in loaded['points']))
        self.assertEqual(DEFAULT_SIDE_PIXELS_PATH.name, 'sideview-site-pixels.json')

    def test_side_temp_entries_are_excluded_by_loader(self):
        doc = json.loads(DEFAULT_SIDE_PIXELS_PATH.read_text(encoding='utf-8'))
        doc['points'].extend([
            {
                'site': 'side:temp_a-t1',
                'label': '임시 A 1단',
                'zone': 'temp_a',
                'kind': 'tier',
                'wire_refs': ['temp_a-r1-c1-l1', 'temp_a-r2-c1-l1', 'temp_a-r3-c1-l1'],
                'roi': [490, 300, 572, 385],
            },
            {
                'site': 'side:temp_b-t1',
                'label': '임시 B 1단',
                'zone': 'temp_b',
                'kind': 'tier',
                'wire_refs': ['temp_b-r1-c1-l1', 'temp_b-r2-c1-l1', 'temp_b-r3-c1-l1'],
                'roi': [0, 300, 55, 385],
            },
        ])
        with self.assertLogs('harbor.topview_site_pixels', level='WARNING') as logs:
            validated = validate_topview_site_pixels(doc)
        self.assertEqual(len(validated['points']), 9)
        self.assertFalse(any(pt['zone'] in ('temp_a', 'temp_b') for pt in validated['points']))
        joined = '\n'.join(logs.output)
        self.assertIn('side:temp_a-t1', joined)
        self.assertIn('side:temp_b-t1', joined)

    def test_invalid_side_wire_refs_are_dropped_and_logged(self):
        doc = json.loads(DEFAULT_SIDE_PIXELS_PATH.read_text(encoding='utf-8'))
        good = doc['points'][0]
        bad_ref = copy.deepcopy(good)
        bad_ref.update(site='side:yard-r1-t1', wire_refs=['yard-r1-c1-l1', 'yard-r1-c9-l1'])
        missing_slot = copy.deepcopy(good)
        missing_slot.update(site='side:yard-r1-t2', wire_refs=['yard-r1-c1-l2'])
        bad_tier = copy.deepcopy(good)
        bad_tier.update(site='side:yard-r1-t9', wire_refs=['yard-r1-c1-l9', 'yard-r1-c4-l9'])
        doc['points'] = [good, bad_ref, missing_slot, bad_tier]
        with self.assertLogs('harbor.topview_site_pixels', level='WARNING') as logs:
            validated = validate_topview_site_pixels(doc)
        self.assertEqual([pt['site'] for pt in validated['points']], [good['site']])
        joined = '\n'.join(logs.output)
        for site in ('side:yard-r1-t1', 'side:yard-r1-t2', 'side:yard-r1-t9'):
            self.assertIn(site, joined)


class ArmSPixelsTests(unittest.TestCase):
    def test_committed_config_keeps_both_places_with_null_pixels(self):
        loaded = load_arm_s_site_pixels()
        self.assertEqual(loaded['source_id'], 'omx_arm_s_top')
        self.assertEqual([(pt['site'], pt['short_label'], pt['point']) for pt in loaded['points']],
                         [('not_places:vehicle_tray', 'Pinky 짐칸', None), ('belt-in', '컨베이어 투입', None)])

    def test_invented_place_names_are_rejected(self):
        doc = json.loads(DEFAULT_ARM_S_PIXELS_PATH.read_text(encoding='utf-8'))
        doc['points'].append({'site': 'not_places:made_up', 'label': '지어낸', 'kind': 'pick', 'point': None})
        with self.assertLogs('harbor.topview_site_pixels', level='WARNING') as logs:
            validated = validate_topview_site_pixels(doc)
        self.assertEqual(len(validated['points']), 2)
        self.assertIn('not_places:made_up', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()

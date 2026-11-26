"""Unit tests for config/site-3d-layout.json loader and canonical ROSY-name validator."""
import copy
import json
import unittest

from harbor.site_3d_layout import (
    CANONICAL_ARMS,
    CANONICAL_CAMERAS,
    DEFAULT_SITE_3D_LAYOUT_PATH,
    SCHEMA_ID,
    load_site_3d_layout,
    validate_site_3d_layout,
)


class Site3DLayoutTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(DEFAULT_SITE_3D_LAYOUT_PATH.read_text(encoding='utf-8'))

    def test_committed_config_loads_and_matches_canonical_names(self):
        loaded = load_site_3d_layout()
        self.assertEqual(loaded['schema'], SCHEMA_ID)
        self.assertEqual(loaded['status'], 'planned_unverified')
        self.assertEqual(loaded['plane']['x_min'], -1.37)
        self.assertEqual(loaded['plane']['x_max'], 1.37)
        self.assertEqual(loaded['plane']['y_min'], -0.76)
        self.assertEqual(loaded['plane']['y_max'], 0.76)
        self.assertEqual(loaded['plane']['pixels_per_meter'], 400.0)
        self.assertEqual(loaded['plane']['image'], [1096, 608])
        self.assertEqual(set(loaded['arms'].keys()), CANONICAL_ARMS)
        self.assertEqual(tuple(loaded['cameras'].keys()), CANONICAL_CAMERAS)
        self.assertEqual(loaded['cameras']['bird']['view_mode'], 'map')
        self.assertEqual(loaded['cameras']['top']['view_mode'], 'raw')
        self.assertEqual(loaded['cameras']['side']['view_mode'], 'raw')
        self.assertEqual(loaded['cameras']['arms']['view_mode'], 'raw')

    def test_non_canonical_arm_name_is_rejected(self):
        doc = copy.deepcopy(self.base)
        doc['arms']['arm_c'] = [0.1, 0.2]
        with self.assertRaisesRegex(ValueError, 'non-canonical arm'):
            validate_site_3d_layout(doc)

    def test_non_canonical_or_legacy_stop_name_is_rejected(self):
        for bad_stop in ('S99', 'EXP_S1', 'STG_C2', 'parking'):
            with self.subTest(stop=bad_stop):
                doc = copy.deepcopy(self.base)
                doc['stops'][bad_stop] = [0.0, 0.0]
                with self.assertRaisesRegex(ValueError, 'non-canonical stop'):
                    validate_site_3d_layout(doc)

    def test_non_canonical_vehicle_id_or_stop_is_rejected(self):
        doc = copy.deepcopy(self.base)
        doc['vehicle']['id'] = 'robot-alpha'
        with self.assertRaisesRegex(ValueError, 'non-canonical vehicle id'):
            validate_site_3d_layout(doc)

        doc2 = copy.deepcopy(self.base)
        doc2['vehicle']['stop'] = 'EXP_S3_BELT'
        with self.assertRaisesRegex(ValueError, 'non-canonical vehicle stop'):
            validate_site_3d_layout(doc2)

    def test_non_canonical_plate_zone_or_dimensions_are_rejected(self):
        doc = copy.deepcopy(self.base)
        doc['plates']['dock_x'] = copy.deepcopy(doc['plates']['stand'])
        with self.assertRaisesRegex(ValueError, 'non-canonical plate zone'):
            validate_site_3d_layout(doc)

        doc2 = copy.deepcopy(self.base)
        doc2['plates']['yard']['cols'] = [1, 2]
        with self.assertRaisesRegex(ValueError, 'cols'):
            validate_site_3d_layout(doc2)

    def test_non_canonical_conveyor_or_zone_label_is_rejected(self):
        doc = copy.deepcopy(self.base)
        doc['conveyor']['belt_out'] = {'uv': [872, 205]}
        with self.assertRaisesRegex(ValueError, 'non-canonical conveyor'):
            validate_site_3d_layout(doc)

        doc2 = copy.deepcopy(self.base)
        doc2['zone_labels'].append({'zone': 'made_up', 'label': '가짜', 'uv': [100, 100]})
        with self.assertRaisesRegex(ValueError, 'non-canonical zone_label'):
            validate_site_3d_layout(doc2)

    def test_non_canonical_camera_key_or_source_id_is_rejected(self):
        doc = copy.deepcopy(self.base)
        doc['cameras']['extra'] = copy.deepcopy(doc['cameras']['top'])
        with self.assertRaisesRegex(ValueError, 'cameras keys'):
            validate_site_3d_layout(doc)

        doc2 = copy.deepcopy(self.base)
        doc2['cameras']['top']['default_source_id'] = 'fake_cam'
        doc2['cameras']['top']['source_ids'] = ['fake_cam']
        with self.assertRaisesRegex(ValueError, 'non-canonical camera source_id'):
            validate_site_3d_layout(doc2)

    def test_invalid_schema_status_or_plane_bounds_are_rejected(self):
        doc = copy.deepcopy(self.base)
        doc['schema'] = 'wrong.v1'
        with self.assertRaisesRegex(ValueError, 'schema'):
            validate_site_3d_layout(doc)

        doc2 = copy.deepcopy(self.base)
        doc2['status'] = 'draft'
        with self.assertRaisesRegex(ValueError, 'status'):
            validate_site_3d_layout(doc2)

        doc3 = copy.deepcopy(self.base)
        doc3['plane']['x_min'] = 2.0
        with self.assertRaisesRegex(ValueError, 'plane bounds'):
            validate_site_3d_layout(doc3)


if __name__ == '__main__':
    unittest.main()

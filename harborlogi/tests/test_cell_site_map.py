"""Pure unit tests for ROSY canonical cell-site map parsing and normalization."""
import copy
import json
import unittest
from unittest.mock import patch

from harbor.cell_site_map import (
    MAX_DOCUMENT_BYTES,
    SCHEMA_ID,
    decode_cell_site_json,
    default_calibration_ref,
    default_name,
    normalize_cell_site_map,
)
from harbor.rosy_http import RosyClient


def _build_canonical_fixture():
    yard_slots = []
    for row in (1, 2, 3):
        for slot_num, start_col, hold_id in ((1, 1, 2), (2, 4, 1)):
            yard_slots.append({
                'id': f'yard:r{row}-s{slot_num}',
                'label': f'{row}행 {start_col}~{start_col + 2}열',
                'row': row,
                'slot': slot_num,
                'columns': [start_col, start_col + 2],
                'hold': slot_num,
                'arms': ['arm_a', 'arm_b'],
                'physical': [f'0{start_col + i}0{row}82' for i in (0, 2, 4)],
                'tiers': [
                    {'tier': t, 'id': f'yard:r{row}-s{slot_num}-t{t}', 'item': f'ship-h{hold_id}r{row - 1}-l{t - 1}'}
                    for t in (1, 2, 3)
                ],
                'status': 'planned_unverified',
            })

    def _temp_slots(area_id, arm_id, prefix):
        return [
            {
                'id': f'{area_id}:r{row}-s1',
                'label': f'{prefix} {row}',
                'row': row,
                'slot': 1,
                'columns': [1, 3],
                'arms': [arm_id],
                'tiers': [
                    {
                        'tier': t,
                        'id': f'{area_id}:r{row}-s1-t{t}',
                        'item': f'{prefix}-{row}' if t == 1 else f'{prefix}-{row}-l{t - 1}',
                    }
                    for t in (1, 2, 3)
                ],
                'status': 'planned_unverified',
            }
            for row in (1, 2, 3)
        ]

    return {
        'schema': SCHEMA_ID,
        'revision': 1,
        'status': 'planned_unverified',
        'tier_height_mm': 32.4,
        'zones': [
            {
                'id': 'yard',
                'label': '장치 야드',
                'role': '보관',
                'grid': {'rows': 3, 'columns': 6, 'tiers': 3},
                'slot_cells': 3,
                'hold_boundary': {'columns': [3, 4]},
                'arms': ['arm_a', 'arm_b'],
                'status': 'planned_unverified',
                'slots': yard_slots,
            },
            {
                'id': 'temp_a',
                'label': '임시 장치 A',
                'role': '임시',
                'grid': {'rows': 3, 'columns': 3, 'tiers': 3},
                'slot_cells': 3,
                'arms': ['arm_a'],
                'status': 'planned_unverified',
                'slots': _temp_slots('temp_a', 'arm_a', 'tmp-a'),
            },
            {
                'id': 'temp_b',
                'label': '임시 장치 B',
                'role': '임시',
                'grid': {'rows': 3, 'columns': 3, 'tiers': 3},
                'slot_cells': 3,
                'arms': ['arm_b'],
                'status': 'planned_unverified',
                'slots': _temp_slots('temp_b', 'arm_b', 'tmp-b'),
            },
            {
                'id': 'stand',
                'label': '반출대',
                'role': '반출',
                'grid': {'rows': 1, 'columns': 3, 'tiers': 1},
                'slot_cells': 3,
                'arms': ['arm_a'],
                'status': 'planned_unverified',
                'slots': [
                    {
                        'id': 'stand:r1-s1',
                        'label': '반출대',
                        'row': 1,
                        'slot': 1,
                        'columns': [1, 3],
                        'arms': ['arm_a'],
                        'tiers': [{'tier': 1, 'id': 'stand:r1-s1-t1', 'item': 'stand'}],
                        'status': 'planned_unverified',
                    }
                ],
            },
        ],
        'not_places': [
            {'id': 'belt', 'label': '공급 경로'},
            {'id': 'supply_s', 'label': '공급 작업대 S'},
            {'id': 'ship_stern', 'label': '선미'},
            {'id': 'ship_bow', 'label': '선수'},
            {'id': 'vehicle_tray', 'label': '이송 차량 짐칸'},
        ],
    }


def _build_fleet_sites_from_canonical(canonical):
    sites = []
    zone_summaries = []
    for z in canonical['zones']:
        zone_summaries.append({
            'id': z['id'],
            'label': z['label'],
            'role': z['role'],
            'grid': dict(z['grid']),
            'slot_cells': z['slot_cells'],
            'arms': list(z['arms']),
            'status': z['status'],
        })
        for s in z['slots']:
            for t in s['tiers']:
                sites.append({
                    'site_id': t['id'],
                    'slot_id': s['id'],
                    'sim_site': t['item'],
                    'base_site': t['item'].rsplit('-l', 1)[0],
                    'wire_ref': f"{z['id']}-r{s['row']}-c{s['columns'][0]}-l{t['tier']}",
                    'area': z['id'],
                    'row': s['row'],
                    'slot': s['slot'],
                    'col': int(s['row']),  # camera column trap (not yard grid column!)
                    'zone_row': int(s['slot']),
                    'tier': t['tier'],
                    'arms': list(s['arms']),
                    'status': s['status'],
                })
    with_zones = {
        'schema': canonical['schema'],
        'revision': canonical['revision'],
        'status': canonical['status'],
        'tier_height_mm': canonical['tier_height_mm'],
        'count': len(sites),
        'zones': zone_summaries,
        'sites': sites,
        'not_places': list(canonical['not_places']),
    }
    legacy = {k: v for k, v in with_zones.items() if k != 'zones'}
    return with_zones, legacy


class _DummyHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, limit: int = -1):
        return self._body[:limit] if limit and limit > 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CellSiteMapTests(unittest.TestCase):
    def test_three_input_forms_converge_and_restore_start_column_four(self):
        canonical = _build_canonical_fixture()
        fleet_with_zones, fleet_legacy = _build_fleet_sites_from_canonical(canonical)

        # Spot check camera col trap on r1-s2-t2: col == 1 while wire_ref starts at c4
        r1_s2_t2 = next(s for s in fleet_legacy['sites'] if s['site_id'] == 'yard:r1-s2-t2')
        self.assertEqual((r1_s2_t2['col'], r1_s2_t2['wire_ref']), (1, 'yard-r1-c4-l2'))

        norm_file = normalize_cell_site_map(canonical)
        norm_new = normalize_cell_site_map(fleet_with_zones)
        norm_old = normalize_cell_site_map(fleet_legacy)

        for res in (norm_file, norm_new, norm_old):
            self.assertEqual(res['schema'], SCHEMA_ID)
            self.assertEqual(res['revision'], 1)
            self.assertEqual(res['slot_cells'], 3)
            self.assertEqual(len(res['sites']), 37)
            self.assertEqual(
                [(z['id'], z['role'], z['rows'], z['columns'], z['tiers'], z['arms']) for z in res['zones']],
                [
                    ('yard', 'storage', 3, 6, 3, ['A', 'B']),
                    ('temp_a', 'temporary', 3, 3, 3, ['A']),
                    ('temp_b', 'temporary', 3, 3, 3, ['B']),
                    ('stand', 'outbound', 1, 3, 1, ['A']),
                ],
            )
            restored = next(s for s in res['sites'] if s['site_id'] == 'yard:r1-s2-t2')
            self.assertEqual((restored['start_col'], restored['tier']), (4, 2))
            self.assertNotIn('belt', [z['id'] for z in res['zones']])
            self.assertEqual(len(res['not_places']), 5)

        self.assertEqual(norm_file['digest'], norm_new['digest'])
        self.assertEqual(norm_file['warnings'], [])
        self.assertEqual(norm_new['warnings'], [])
        self.assertFalse(norm_file['fallback_labels'])
        self.assertTrue(norm_old['fallback_labels'])
        self.assertTrue(norm_old['warnings'])
        self.assertEqual(default_name(norm_file), 'ROSY 셀 맵 rev1 (planned_unverified)')
        self.assertEqual(default_calibration_ref(norm_file), 'rosy.cell-site-map.v1#rev1 (planned_unverified)')

    def test_strict_json_decoder_rejects_malformed_inputs(self):
        for label, payload in (
            ('empty', b''),
            ('too_big', b'{' + b' ' * MAX_DOCUMENT_BYTES + b'}'),
            ('bad_utf8', b'\xff\xfe{"schema":"rosy.cell-site-map.v1"}'),
            ('array_root', b'[1, 2, 3]'),
            ('duplicate_key', b'{"revision": 1, "revision": 2}'),
            ('nan', b'{"tier_height_mm": NaN}'),
            ('infinity', b'{"tier_height_mm": Infinity}'),
            ('overflow_float', b'{"tier_height_mm": 1e999}'),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                decode_cell_site_json(payload)

    def test_validation_rejects_legacy_and_canonical_anomalies(self):
        canonical = _build_canonical_fixture()
        fleet_with_zones, fleet_legacy = _build_fleet_sites_from_canonical(canonical)

        # 1. Bool or float in strict integer fields
        bad = copy.deepcopy(canonical)
        bad['revision'] = True
        with self.assertRaises(ValueError):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(canonical)
        bad['zones'][0]['slot_cells'] = 3.0
        with self.assertRaises(ValueError):
            normalize_cell_site_map(bad)

        # 2. Unsupported status, arm, role, area
        bad = copy.deepcopy(canonical)
        bad['status'] = 'field_verified'
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_MAP_STATUS'):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(canonical)
        bad['zones'][1]['arms'] = ['arm_c']
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ARM_REACH'):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(canonical)
        bad['zones'][1]['role'] = 'unknown_role'
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ZONE_ROLE'):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(fleet_legacy)
        bad['sites'].append({**bad['sites'][-1], 'site_id': 'x:1', 'area': 'dock_x', 'wire_ref': 'dock_x-r1-c1-l1'})
        bad['count'] = len(bad['sites'])
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_ZONE_AREA'):
            normalize_cell_site_map(bad)

        # 3. Missing outer row/tier, mixed slot_cells, count mismatch, unresolved slot width
        bad = copy.deepcopy(fleet_legacy)
        bad['sites'] = [s for s in bad['sites'] if not (s['area'] == 'yard' and s['row'] == 3 and s['slot'] == 2)]
        bad['count'] = len(bad['sites'])
        with self.assertRaisesRegex(ValueError, 'ZONE_NOT_RECTANGULAR'):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(fleet_legacy)
        bad['count'] = 999
        with self.assertRaisesRegex(ValueError, 'COUNT_MISMATCH'):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(canonical)
        bad['zones'][1]['slot_cells'] = 1
        with self.assertRaisesRegex(ValueError, 'MIXED_SLOT_CELLS'):
            normalize_cell_site_map(bad)

        single_slot_only = copy.deepcopy(fleet_legacy)
        single_slot_only['sites'] = [s for s in single_slot_only['sites'] if s['area'] != 'yard']
        # Force a fake yard without multi-slot rows -> single slot per row -> unresolved width
        single_slot_only['sites'].extend([
            {
                'site_id': f'yard:r1-s1-t{t}', 'slot_id': 'yard:r1-s1', 'sim_site': f'ship-h2r0-l{t - 1}',
                'wire_ref': f'yard-r1-c1-l{t}', 'area': 'yard', 'row': 1, 'slot': 1, 'col': 1,
                'tier': t, 'arms': ['arm_a'], 'status': 'planned_unverified',
            }
            for t in (1, 2, 3)
        ])
        single_slot_only['count'] = len(single_slot_only['sites'])
        with self.assertRaisesRegex(ValueError, 'SLOT_WIDTH_UNRESOLVED'):
            normalize_cell_site_map(single_slot_only)

        # 4. wire_ref contradicts site fields
        bad = copy.deepcopy(fleet_legacy)
        bad['sites'][0]['wire_ref'] = 'yard-r1-c1-l2'
        with self.assertRaisesRegex(ValueError, 'WIRE_REF_MISMATCH'):
            normalize_cell_site_map(bad)

        # 5. Empty zones array in new Fleet response fails closed (never falls back to legacy!)
        bad = copy.deepcopy(fleet_with_zones)
        bad['zones'] = []
        with self.assertRaisesRegex(ValueError, 'INVALID_MAP_DOCUMENT'):
            normalize_cell_site_map(bad)

        # 6. Zones vs sites mismatch
        bad = copy.deepcopy(fleet_with_zones)
        bad['zones'][0]['grid']['rows'] = 2
        with self.assertRaisesRegex(ValueError, 'ZONES_SITES_MISMATCH'):
            normalize_cell_site_map(bad)

        # 7. Missing yard, missing outbound, out-of-bounds, not_places overlap
        bad = copy.deepcopy(canonical)
        bad['zones'] = [z for z in bad['zones'] if z['id'] != 'stand']
        with self.assertRaisesRegex(ValueError, 'OUTBOUND_REQUIRED'):
            normalize_cell_site_map(bad)

        bad = copy.deepcopy(canonical)
        bad['not_places'].append({'id': 'yard', 'label': 'dup'})
        with self.assertRaisesRegex(ValueError, 'overlaps zone'):
            normalize_cell_site_map(bad)

    def test_rosy_client_cell_sites_uses_strict_decoder(self):
        client = RosyClient('https://fleet.local:8443', 'secret-token')
        canonical = _build_canonical_fixture()
        _, legacy = _build_fleet_sites_from_canonical(canonical)
        with patch.object(client._opener, 'open', return_value=_DummyHTTPResponse(json.dumps(legacy).encode('utf-8'))):
            got = client.cell_sites()
            self.assertEqual(got['count'], 37)
        with patch.object(client._opener, 'open', return_value=_DummyHTTPResponse(b'{"a":1,"a":2}')):
            with self.assertRaisesRegex(ValueError, 'DUPLICATE_JSON_KEY'):
                client.cell_sites()

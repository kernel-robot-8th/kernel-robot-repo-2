"""Pure unit tests for ROSY canonical cell-site map parsing and normalization."""
import copy
import json
import unittest
from unittest.mock import patch

from harbor.cell_site_map import (
    CANONICAL_ZONE_SPECS,
    MAX_DOCUMENT_BYTES,
    SCHEMA_ID,
    canonical_cell_slot_specs,
    canonical_cell_wire_refs,
    compare_condition_with_rosy,
    decode_cell_site_json,
    default_calibration_ref,
    default_name,
    normalize_cell_site_map,
    validate_canonical_cell_placement,
    validate_canonical_wire_ref,
)
from harbor.site_places import parse_site_places
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


class CanonicalZoneAlignmentTests(unittest.TestCase):
    def test_canonical_wire_refs_and_slots_match_37_sites_13_slots(self):
        refs = canonical_cell_wire_refs()
        slots = canonical_cell_slot_specs()
        self.assertEqual(len(refs), 37)
        self.assertEqual(len(slots), 13)
        self.assertEqual(set(CANONICAL_ZONE_SPECS), {'yard', 'temp_a', 'temp_b', 'stand'})
        self.assertIn('yard-r3-c4-l3', refs)
        self.assertIn('stand-r1-c1-l1', refs)
        self.assertEqual(slots['yard:r2-s2'], 3)
        self.assertEqual(slots['stand:r1-s1'], 1)

    def test_validate_canonical_placement_and_wire_ref_reject_invalid_coords_and_arms(self):
        self.assertEqual(validate_canonical_cell_placement('yard', 1, 4, 2, arm_id='B'), 'yard-r1-c4-l2')
        self.assertEqual(validate_canonical_wire_ref('temp_a-r2-c1-l3', arm_id='arm_a'), 'temp_a-r2-c1-l3')
        self.assertEqual(validate_canonical_wire_ref('belt', allow_belt=True), 'belt')

        for bad_col in (2, 3, 5, 6):
            with self.assertRaisesRegex(ValueError, 'INVALID_CELL_SLOT_COORDINATE'):
                validate_canonical_cell_placement('yard', 1, bad_col, 1)
        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_SLOT_COORDINATE'):
            validate_canonical_cell_placement('temp_a', 1, 2, 1)
        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_SLOT_COORDINATE'):
            validate_canonical_cell_placement('stand', 1, 1, 2)
        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_SLOT_COORDINATE'):
            validate_canonical_cell_placement('yard', True, 1, 1)
        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_SLOT_COORDINATE'):
            validate_canonical_wire_ref('belt')

        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_ZONE_ARM'):
            validate_canonical_cell_placement('stand', 1, 1, 1, arm_id='B')
        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_ZONE_ARM'):
            validate_canonical_cell_placement('temp_a', 1, 1, 1, arm_id='arm_b')
        with self.assertRaisesRegex(ValueError, 'INVALID_CELL_ZONE_ARM'):
            validate_canonical_wire_ref('temp_b-r1-c1-l1', arm_id='A')

    def _canonical_condition(self):
        return {
            'slot_cells': 3,
            'yard_layout': {'rows': 3, 'columns': 6, 'levels': 3},
            'spatial_areas': [
                {'area_id': 'temp_a', 'rows': 3, 'columns': 3, 'levels': 3},
                {'area_id': 'temp_b', 'rows': 3, 'columns': 3, 'levels': 3},
                {'area_id': 'stand', 'rows': 1, 'columns': 3, 'levels': 1},
            ],
            'arm_reach': [
                ['A', 'yard'], ['B', 'yard'],
                ['A', 'temp_a'], ['B', 'temp_b'], ['A', 'stand'],
            ],
        }

    def _port_places(self, *, exit_stop='S_EXIT', exit_role='outbound_exit', status='verified'):
        return parse_site_places({
            'schema': 'rosy.port-site-places.v1',
            'sources': {
                'site_map': {'map_id': 'test_world', 'version': 1},
                'cell_site_map': {'schema': 'rosy.cell-site-map.v1', 'revision': 1, 'status': 'verified'},
            },
            'digest': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            'vehicle_places': [
                {'place_id': 'S1', 'port_role': 'inbound_load', 'label': '수령구', 'status': 'verified', 'dispatchable': True},
                {'place_id': 'S_OUT', 'port_role': 'outbound_pickup', 'label': '반출 상차', 'status': status, 'dispatchable': status == 'verified'},
                {'place_id': 'S_EXIT', 'port_role': exit_role, 'label': '반출 출구', 'status': status, 'dispatchable': status == 'verified'},
            ],
            'cell_zones': [
                {'zone_id': 'yard', 'role': 'storage', 'label': '야드', 'status': status},
                {'zone_id': 'temp_a', 'role': 'temporary', 'label': '임시A', 'status': status},
                {'zone_id': 'temp_b', 'role': 'temporary', 'label': '임시B', 'status': status},
                {'zone_id': 'stand', 'role': 'outbound', 'label': '반출대', 'status': status},
            ],
            'intake_handoffs': [],
            'outbound_handoffs': [{
                'handoff_id': 'stand_out',
                'cell_zone_id': 'stand',
                'wire_slot_ref': 'stand-r1-c1-l1',
                'vehicle_pickup_stop_id': 'S_OUT',
                'vehicle_exit_stop_id': exit_stop,
                'status': status,
                'dispatchable': status == 'verified',
            }],
            'not_places': ['belt'],
        })

    def test_compare_condition_with_rosy_flags_mismatches_and_passes_when_aligned(self):
        missing = compare_condition_with_rosy(None, site_places=self._port_places())
        self.assertFalse(missing['aligned'])
        self.assertIn('NO_ACTIVE_CONDITION', {i['code'] for i in missing['issues']})

        legacy_condition = {
            'slot_cells': 1,
            'yard_layout': {'rows': 3, 'columns': 3, 'levels': 3},
            'spatial_areas': [{'area_id': 'stand', 'rows': 1, 'columns': 3, 'levels': 1}],
            'arm_reach': [['B', 'stand']],
        }
        mismatch = compare_condition_with_rosy(
            legacy_condition,
            site_places=self._port_places(exit_stop='S1', status='planned_unverified'),
        )
        codes = {i['code'] for i in mismatch['issues']}
        self.assertTrue({
            'SLOT_CELLS_MISMATCH',
            'ZONE_DIMENSIONS_MISMATCH',
            'ZONE_MISSING_IN_HARBOR',
            'ZONE_ARM_MISMATCH',
            'OUTBOUND_EXIT_ROLE_MISMATCH',
            'CELL_ZONES_UNVERIFIED',
            'OUTBOUND_PICKUP_UNVERIFIED',
        }.issubset(codes))

        aligned = compare_condition_with_rosy(
            self._canonical_condition(),
            site_places=self._port_places(),
        )
        self.assertTrue(aligned['aligned'])
        self.assertEqual(aligned['issues'], [])


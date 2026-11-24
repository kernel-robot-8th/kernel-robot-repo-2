"""Unit tests for typed read-only ROSY Fleet models, strict JSON guards, and bug fixes."""
from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from harbor.cell_site_map import normalize_cell_site_map
from harbor.fleet_types import (
    CellSiteMapSnapshot,
    ExecutionProfile,
    FleetSession,
    PortEventPageRecord,
    SiteRecognitionSnapshot,
    WorkcellObservationSnapshot,
    parse_cell_site_map,
    parse_execution_profile,
    parse_fleet_session,
    parse_port_event_page,
    parse_site_recognition_snapshot,
    parse_workcell_observations,
)
from harbor.port_events import parse_event
from harbor.rosy_http import RosyClient


class FleetTypesTests(unittest.TestCase):
    def test_session_and_execution_profile_typing_rejects_bad_types(self):
        sess = parse_fleet_session({'principal_id': 'harbor-svc', 'role': 'service', 'estop': False})
        self.assertEqual(sess, FleetSession(principal_id='harbor-svc', role='service', estop=False))
        with self.assertRaises(ValueError):
            parse_fleet_session({'principal_id': 'harbor-svc', 'role': 'superuser'})
        with self.assertRaises(ValueError):
            parse_fleet_session({'principal_id': 'harbor-svc', 'role': 'service', 'estop': 1})

        prof = parse_execution_profile({
            'profile': 'sim',
            'arm_execution': 'open',
            'observation_producers': [{'id': 'cam-1'}, 'cam-2'],
            'evidence_producers': ['ev-1'],
        })
        self.assertEqual(prof, ExecutionProfile('sim', 'open', ('cam-1', 'cam-2'), ('ev-1',)))
        with self.assertRaises(ValueError):
            parse_execution_profile({'profile': '', 'observation_producers': [], 'evidence_producers': []})

    def test_workcell_observations_typed_snapshot(self):
        snap = parse_workcell_observations({
            'read_only': True,
            'workcells': [{
                'workcell_id': 'cell_ab',
                'cameras': [{'source_id': 'top-1', 'label': 'Top', 'role': 'overhead'}],
                'arms': [{
                    'arm_id': 'arm_a',
                    'incidents': [
                        {'incident_id': 'i-ok', 'state': 'cleared', 'decision': {'hold': False}},
                        {'incident_id': 'i-bad', 'state': 'held', 'decision': {'hold': True, 'reason': 'COLLISION_STOP'}},
                    ],
                }],
            }],
        })
        self.assertIsInstance(snap, WorkcellObservationSnapshot)
        self.assertEqual([c.source_id for c in snap.cameras], ['top-1'])
        self.assertEqual(len(snap.open_incident_holds), 1)
        self.assertEqual(snap.open_incident_holds[0].reason, 'COLLISION_STOP')
        with self.assertRaises(ValueError):
            parse_workcell_observations({'read_only': 1, 'workcells': []})

    def test_port_events_slot_ref_strict_and_typed_page(self):
        ev_id = 'a' * 64
        with self.assertRaises(ValueError):
            parse_event({
                'event_id': ev_id,
                'kind': 'container.stored',
                'observation_ref': 'obs-1',
                'object_ref': 'C1',
                'slot_ref': 'yard-r0-c1-l1',
                'identity': 'observed',
                'observed_at': '2026-11-24T00:00:00Z',
            })
        page = parse_port_event_page({
            'events': [{
                'event_id': ev_id,
                'kind': 'container.stored',
                'observation_ref': 'obs-1',
                'object_ref': 'C1',
                'slot_ref': 'yard-r1-c1-l1',
                'identity': 'observed',
                'observed_at': '2026-11-24T00:00:00Z',
            }],
            'cursor': 'cur-1',
            'complete': True,
        })
        self.assertIsInstance(page, PortEventPageRecord)
        self.assertEqual(page.events[0].slot_ref, 'yard-r1-c1-l1')
        self.assertEqual(page.events[0].observed_at, datetime(2026, 11, 24, tzinfo=timezone.utc))

    def test_cell_site_map_accepts_verified_and_returns_typed_snapshot(self):
        from tests.test_cell_site_map import _build_canonical_fixture

        doc = _build_canonical_fixture()
        doc['status'] = 'verified'
        for zone in doc['zones']:
            zone['status'] = 'verified'
            for slot in zone['slots']:
                slot['status'] = 'verified'
                for tier in slot['tiers']:
                    tier['status'] = 'verified'
        norm = normalize_cell_site_map(doc)
        self.assertEqual(norm['status'], 'verified')

        snap = parse_cell_site_map(doc)
        self.assertIsInstance(snap, CellSiteMapSnapshot)
        self.assertEqual(len(snap.sites), 37)
        self.assertEqual(snap.status, 'verified')

    def test_site_recognition_typed_snapshot(self):
        snap = parse_site_recognition_snapshot({
            'schema': 'rosy.cell-site-recognition.v1',
            'snapshot_id': 'snap-1',
            'observed_at': '2026-11-24T00:00:00Z',
            'source_id': 'cam-1',
            'calibration_revision': 'cal-1',
            'site_map_revision': 1,
            'sites': [{
                'site_ref': 'yard-r1-c1-l1',
                'state': 'OCCUPIED',
                'object_class': 'container',
                'object_ref_read': 'C1',
                'confidence': 0.95,
            }],
        })
        self.assertIsInstance(snap, SiteRecognitionSnapshot)
        self.assertEqual(snap.entries[0].object_ref_read, 'C1')
        with self.assertRaises(ValueError):
            parse_site_recognition_snapshot({
                'schema': 'rosy.cell-site-recognition.v1',
                'snapshot_id': 'snap-1',
                'observed_at': '2026-11-24T00:00:00Z',
                'source_id': 'cam-1',
                'calibration_revision': 'cal-1',
                'site_map_revision': 1,
                'sites': [{
                    'site_ref': 'yard-r1-c1-l1',
                    'state': 'OCCUPIED',
                    'object_class': 'container',
                    'object_ref_read': 'C1',
                    'confidence': float('nan'),
                }],
            })

    def test_rosy_client_null_json_guards_and_typed_methods(self):
        from types import SimpleNamespace

        client = RosyClient('https://fleet.example', 'tok', transport=lambda _m, _p, _b: {'candidate': None, 'summary': None})
        binding = SimpleNamespace(
            compile_request=lambda: {},
            recipe_digest='a' * 64,
            cell_digest='b' * 64,
            process_artifact_digest='c' * 64,
        )
        with self.assertRaises(ValueError):
            client.compile(binding)

        arm_client = RosyClient('https://fleet.example', 'tok', transport=lambda _m, _p, _b: {'job': None})
        with self.assertRaises(ValueError):
            arm_client.arm_job('m-1')

        with self.assertRaises(ValueError):
            client.propose_cell_plan({'mode': 'harbor_plan'}, request_key='')
        with self.assertRaises(ValueError):
            client.cancel_cell_plan('cp-1', reason='   ')

        def fake_transport(method, path, body):
            del method, body
            if path == '/api/fleet/session':
                return {'principal_id': 'svc-1', 'role': 'service'}
            if path == '/api/fleet/execution-profile':
                return {'profile': 'sim', 'arm_execution': 'open', 'observation_producers': [], 'evidence_producers': []}
            if path == '/api/fleet/workcell-observations':
                return {'read_only': True, 'workcells': []}
            if path == '/api/fleet/site-points?view=port':
                from tests.test_site_places import _verified_payload
                return _verified_payload()
            if path == '/api/fleet/pipeline/cell-sites':
                from tests.test_cell_site_map import _build_canonical_fixture
                return _build_canonical_fixture()
            if path == '/api/fleet/port-events?limit=100':
                return {'events': [], 'cursor': '', 'complete': True}
            raise AssertionError(path)

        typed_client = RosyClient('https://fleet.example', 'tok', transport=fake_transport)
        self.assertEqual(typed_client.session_typed().principal_id, 'svc-1')
        self.assertEqual(typed_client.execution_profile_typed().profile, 'sim')
        self.assertTrue(typed_client.workcell_observations_typed().read_only)
        self.assertEqual(typed_client.site_places_typed().schema, 'rosy.port-site-places.v1')
        self.assertEqual(len(typed_client.cell_sites_typed().sites), 37)
        self.assertTrue(typed_client.port_events_typed().complete)


if __name__ == '__main__':
    unittest.main()

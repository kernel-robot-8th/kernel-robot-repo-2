"""Explicit loopback simulation contract test, launched separately from unit discovery."""
import json
import ipaddress
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from harbor.cell_plan import build_harbor_plan_request, build_rosy_directive_request, validate_cell_plan_response
from harbor.cell_site_map import normalize_cell_site_map
from harbor.rosy_http import RosyClient
from harbor.site_places import (
    parse_site_places,
    resolve_intake_destination,
    resolve_outbound_dispatch_stops,
)
from harbor.transport import TripIntent, TransportLedger


class TransportHttpTests(unittest.TestCase):
    def test_lost_response_recovery_and_arrival_without_process_documents(self):
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        self.assertEqual(config['schema'], 'harbor.transport-http-test/1')
        self.assertIs(config['simulation'], True)
        origin = urlsplit(config['origin'])
        self.assertEqual(origin.scheme, 'http')
        self.assertTrue(ipaddress.ip_address(origin.hostname).is_loopback)
        client = RosyClient(config['origin'], config['operator'])
        intent = TripIntent('harbor/이송?key=' + uuid4().hex, tuple(config['candidates']), config['steps'][0]['to'])
        class LostResponse:
            def trip(self, candidates, steps, *, request_key):
                client.trip(candidates, steps, request_key=request_key)
                raise TimeoutError('synthetic response loss after server commit')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'business.sqlite3'
            ledger = TransportLedger(path)
            ledger.prepare(intent)
            with self.assertRaises(TimeoutError):
                ledger.submit(intent.request_key, LostResponse(), operator_confirmed=True)
            self.assertEqual(ledger.read(intent.request_key)['state'], 'UNCERTAIN')
            restarted = TransportLedger(path)
            with self.assertRaises(ValueError):
                restarted.submit(intent.request_key, client, operator_confirmed=True)
            job = restarted.reconcile(intent.request_key, client)
            by_id = client.trip_job(job['mission_id'])['job']
            for field in ('mission_id', 'request_key', 'request_digest', 'robot_id'):
                self.assertEqual(by_id[field], job[field])
            self.assertEqual(by_id['steps'][0]['step'], job['steps'][0]['step'])
            other = RosyClient(config['origin'], config['approver'])
            with self.assertRaises(HTTPError) as denied:
                other.trip_request(intent.request_key)
            self.assertEqual(denied.exception.code, 404)
            deadline = time.monotonic() + 30
            while job['status'] != 'GOAL_CONFIRMED':
                self.assertNotEqual(job['status'], 'HOLD', job.get('reason'))
                if time.monotonic() > deadline:
                    self.fail('The simulation trip did not arrive within 30 seconds')
                time.sleep(.1)
                job = restarted.reconcile(intent.request_key, client)
            self.assertIsNotNone(job['steps'][0]['attempt_id'])
            self.assertEqual(restarted.read(intent.request_key)['state'], 'ACKNOWLEDGED')
            self.assertFalse({'recipe_digest', 'cell_digest', 'process_artifact_digest'} & job.keys())

    def test_connection_rules_against_the_real_fleet(self):
        """docs/platform-contract.md 연결 규약 3·4·8·9·10 on a real Fleet: identity, coded errors, port-events probe."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        client = RosyClient(config['origin'], config['operator'])
        session = client.session()
        self.assertTrue({'principal_id', 'role'} <= session.keys())
        with self.assertRaises(HTTPError) as refused:
            RosyClient(config['origin'], 'not-a-token').session()
        self.assertEqual(refused.exception.code, 401)
        with self.assertRaises(HTTPError) as unknown:
            client.trip_request('harbor-probe-' + uuid4().hex)
        self.assertEqual(unknown.exception.code, 404)
        with self.assertRaises(HTTPError) as probe:
            client.port_events(after='harbor-probe-' + uuid4().hex, limit=1)
        status, body = probe.exception.code, json.loads(probe.exception.read())
        if status == 404:  # a platform older than ROSY D-996 has no such route
            self.assertNotIsInstance(body.get('detail'), dict)
            return
        self.assertEqual((status, body), (400, {'detail': {'code': 'PORT_EVENT_CURSOR_INVALID'}}))
        page = client.port_events(limit=1)
        self.assertEqual(set(page), {'events', 'cursor', 'complete'})

    def test_site_places_and_cell_sites_against_the_real_fleet(self):
        """Consume real ROSY GET /api/fleet/site-points?view=port and /api/fleet/pipeline/cell-sites."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        client = RosyClient(config['origin'], config['operator'])
        viewer = RosyClient(config['origin'], config['viewer'])

        raw = client.site_places()
        places = parse_site_places(raw)
        self.assertEqual(places.schema, 'rosy.port-site-places.v1')
        s_out = places.vehicle_place('S_OUT')
        self.assertIsNotNone(s_out)
        self.assertEqual(s_out.port_role, 'outbound_pickup')
        self.assertEqual(s_out.status, 'planned_unverified')
        self.assertFalse(s_out.dispatchable)
        self.assertEqual({z.zone_id for z in places.cell_zones}, {'yard', 'temp_a', 'temp_b', 'stand'})
        self.assertIn('belt', places.not_places)
        self.assertIsNone(resolve_intake_destination(places)[0])
        self.assertIsNone(resolve_outbound_dispatch_stops(places)[0])
        self.assertEqual(parse_site_places(viewer.site_places()).digest, places.digest)

        with self.assertRaises(HTTPError) as denied_places:
            RosyClient(config['origin'], 'not-a-token').site_places()
        self.assertEqual(denied_places.exception.code, 401)

        req = Request(
            config['origin'].rstrip('/') + '/api/fleet/site-points?view=full',
            headers={'Authorization': 'Bearer ' + config['operator']},
        )
        with urlopen(req, timeout=10) as resp:
            full_doc = json.loads(resp.read().decode('utf-8'))
        self.assertEqual(full_doc['schema'], 'rosy.site-points.v1')
        with self.assertRaises(ValueError):
            parse_site_places(full_doc)

        raw_cells = client.cell_sites()
        normalized = normalize_cell_site_map(raw_cells)
        self.assertEqual(normalized['schema'], 'rosy.cell-site-map.v1')
        self.assertEqual(normalized['input_format'], 'fleet_zones')
        self.assertEqual(len(normalized['sites']), 37)
        self.assertEqual({z['id'] for z in normalized['zones']}, {'yard', 'temp_a', 'temp_b', 'stand'})
        self.assertEqual(normalize_cell_site_map(viewer.cell_sites())['digest'], normalized['digest'])

        with self.assertRaises(HTTPError) as denied_cells:
            RosyClient(config['origin'], 'not-a-token').cell_sites()
        self.assertEqual(denied_cells.exception.code, 401)

    def test_z_dual_mode_cell_plan_harbor_and_rosy_over_real_http(self):
        """Dual-mode OMX cell-plan E2E: Mode 1 explicit Harbor plan order/coords vs Mode 2 ROSY autonomous plan."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        origin = config['origin'].rstrip('/')
        service = RosyClient(origin, config['service'])
        operator = RosyClient(origin, config['operator'])
        observation_payload = {
            'schema': 'grid-placement-v1',
            'placements': [
                {'cargo_id': 'C1', 'area_id': 'yard', 'row': 1, 'column': 1, 'level': 1, 'orientation': 0},
                {'cargo_id': 'C2', 'area_id': 'yard', 'row': 1, 'column': 1, 'level': 2, 'orientation': 0},
            ],
        }

        def admit_plan_tasks(tasks):
            for task in tasks:
                req_rdy = Request(origin + '/api/fleet/pipeline/cells/cell_ab/readiness', headers={'Authorization': 'Bearer ' + config['operator']})
                with urlopen(req_rdy, timeout=10) as resp:
                    rdy = json.loads(resp.read().decode('utf-8'))
                admit_body = json.dumps({
                    'expected_dispatch_generation': rdy['dispatch_generation'],
                    'expected_stop_generation': rdy['stop_generation'],
                    'expected_readiness_digest': rdy['readiness_digest'],
                }).encode('utf-8')
                req_admit = Request(
                    origin + f"/api/fleet/pipeline/cell-tasks/{task['task_id']}/admit",
                    data=admit_body,
                    method='POST',
                    headers={'Authorization': 'Bearer ' + config['operator'], 'Content-Type': 'application/json'},
                )
                with urlopen(req_admit, timeout=10) as resp:
                    self.assertEqual(resp.status, 200)

        # Path 1: Harbor provides exact sequence + slot coordinates -> order_source='harbor'
        harbor_result = {
            'status': 'READY',
            'model': 'grid-placement-v1',
            'moves': [
                {
                    'cargo_id': 'C2', 'primary_arm': 'A', 'phase_label': '재취급',
                    'source_placement': {'area_id': 'yard', 'row': 1, 'column': 1, 'level': 2},
                    'destination_placement': {'area_id': 'temp_a', 'row': 1, 'column': 1, 'level': 1},
                },
                {
                    'cargo_id': 'C1', 'primary_arm': 'A', 'phase_label': '반출',
                    'source_placement': {'area_id': 'yard', 'row': 1, 'column': 1, 'level': 1},
                    'destination_placement': {'area_id': 'stand', 'row': 1, 'column': 1, 'level': 1},
                },
                {
                    'cargo_id': 'C2', 'primary_arm': 'A', 'phase_label': '복귀',
                    'source_placement': {'area_id': 'temp_a', 'row': 1, 'column': 1, 'level': 1},
                    'destination_placement': {'area_id': 'yard', 'row': 1, 'column': 1, 'level': 1},
                },
            ],
        }
        harbor_req = build_harbor_plan_request(harbor_result, observation_payload=observation_payload)
        res1 = validate_cell_plan_response(
            service.propose_cell_plan(harbor_req, request_key='harbor-mode1-' + uuid4().hex),
            expected_order_source='harbor',
            expected_step_count=3,
        )
        self.assertEqual([t['dst_wire_ref'] for t in res1['tasks']], ['temp_a-r1-c1-l1', 'stand-r1-c1-l1', 'yard-r1-c1-l1'])
        self.assertTrue(all(t['status'] == 'PROPOSED' for t in res1['tasks']))

        # Bad Harbor order (pull buried C1 first) must fail closed with HTTP 422.
        bad_req = {**harbor_req, 'steps': [harbor_req['steps'][1]]}
        with self.assertRaises(HTTPError) as blocked:
            service.propose_cell_plan(bad_req, request_key='harbor-bad-' + uuid4().hex)
        self.assertEqual(blocked.exception.code, 422)
        self.assertEqual(json.loads(blocked.exception.read())['detail']['code'], 'BLOCKED_SOURCE_CARGO')

        admit_plan_tasks(res1['tasks'])
        self.assertEqual(operator.cell_plan(res1['plan']['plan_id'])['plan']['status'], 'QUEUED')

        # Path 2: Harbor sends simple high-level directive without steps -> ROSY plans autonomously (order_source='rosy_r')
        rosy_req = build_rosy_directive_request(mode='outbound', cargo_id='C1', destination='stand', observation_payload=observation_payload)
        rosy_key = 'harbor-mode2-' + uuid4().hex
        res2 = validate_cell_plan_response(
            service.propose_cell_plan(rosy_req, request_key=rosy_key),
            expected_order_source='rosy_r',
            expected_step_count=3,
        )
        self.assertEqual([t['phase_label'] for t in res2['tasks']], ['재취급', '반출', '복귀'])
        self.assertEqual(res2['tasks'][1]['dst_wire_ref'], 'stand-r1-c1-l1')
        self.assertEqual(res2['tasks'][2]['dst_wire_ref'], 'yard-r1-c1-l1')
        self.assertEqual(service.propose_cell_plan(rosy_req, request_key=rosy_key)['plan']['plan_id'], res2['plan']['plan_id'])

        admit_plan_tasks(res2['tasks'])
        self.assertEqual(operator.cell_plan(res2['plan']['plan_id'])['plan']['status'], 'QUEUED')

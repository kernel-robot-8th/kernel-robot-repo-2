"""Explicit loopback simulation contract test, launched separately from unit discovery."""
from datetime import datetime, timezone
from hashlib import sha256
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
from harbor.cell_site_map import compare_condition_with_rosy, normalize_cell_site_map
from harbor.port_events import IntakeLedger, parse_slot_ref
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

    def test_trip_proposal_d908_and_uncertain_recovery_over_real_http(self):
        """S2: D-908 trip proposal, proposer/operator admission separation, and 404 UNCERTAIN recovery."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        service_token = config.get('service')
        if not service_token:
            self.skipTest('Service token not provided in harness config')
        proposer = RosyClient(config['origin'], service_token)
        proposer.expected_principal, proposer.expected_role = 'cell-app-service', 'service'
        key = 'harbor-prop-' + uuid4().hex
        intent = TripIntent(key, tuple(config['candidates']), config['steps'][0]['to'], object_ref='C-PROP-1')

        class FailingPost:
            def session(self):
                return proposer.session()

            def trip_propose(self, *args, **kwargs):
                raise HTTPError(config['origin'], 503, 'Unavailable', {}, None)

        with tempfile.TemporaryDirectory() as folder:
            ledger = TransportLedger(Path(folder) / 'transport.sqlite3')
            ledger.prepare(intent)
            with self.assertRaises(HTTPError):
                ledger.propose(key, FailingPost())
            self.assertEqual(ledger.read(key)['state'], 'UNCERTAIN')
            # Real Fleet GET returns coded 404 TRIP_PROPOSAL_NOT_FOUND so ledger recovers safely to PLANNED.
            self.assertIsNone(ledger.reconcile_proposal(key, proposer))
            self.assertEqual(ledger.read(key)['state'], 'PLANNED')

            proposal = ledger.propose(key, proposer)
            self.assertEqual(ledger.read(key)['state'], 'AWAITING_APPROVAL')

            def call(token, method, path, body=None):
                req = Request(
                    config['origin'].rstrip('/') + path,
                    data=None if body is None else json.dumps(body).encode('utf-8'),
                    method=method,
                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                )
                with urlopen(req, timeout=10) as resp:
                    return json.load(resp)

            gen = call(config['approver'], 'GET', '/api/fleet/dispatch-control')['generation']
            admit_body = {
                'expected_proposal_digest': proposal['proposal_digest'],
                'expected_generation': gen,
            }
            # Proposer cannot admit its own proposal (D-908).
            with self.assertRaises(HTTPError) as self_admit:
                call(service_token, 'POST', f"/api/fleet/trip-proposals/{proposal['proposal_id']}/admit", admit_body)
            self.assertEqual(self_admit.exception.code, 403)

            admitted = call(config['approver'], 'POST', f"/api/fleet/trip-proposals/{proposal['proposal_id']}/admit", admit_body)['proposal']
            self.assertEqual(admitted['status'], 'ADMITTED')
            deadline = time.monotonic() + 30
            while True:
                job = ledger.reconcile_proposal(key, proposer)
                self.assertEqual(ledger.read(key)['state'], 'ACKNOWLEDGED')
                if job['status'] == 'GOAL_CONFIRMED':
                    break
                self.assertNotEqual(job['status'], 'HOLD', job.get('reason'))
                if time.monotonic() >= deadline:
                    self.fail('Admitted trip proposal did not finish within 30 seconds')
                time.sleep(0.1)

    def test_port_events_roundtrip_over_real_http(self):
        """S1: ADR 0029 / D-996 workcell port-events ingest -> GET /api/fleet/port-events -> IntakeLedger."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        wc_id, wc_token = config.get('workcell_id'), config.get('workcell_token')
        if not wc_id or not wc_token:
            self.skipTest('Workcell observation credentials not provided in harness config')
        client = RosyClient(config['origin'], config['operator'])
        cargo = 'C-PORT-' + uuid4().hex[:8]
        now = datetime.now(timezone.utc)
        obs_ts = now.strftime('%Y-%m-%dT%H:%M:%SZ')

        def post_event(payload):
            req = Request(
                f"{config['origin'].rstrip('/')}/api/fleet/workcell-observations/{wc_id}/port-events",
                data=json.dumps(payload).encode('utf-8'),
                method='POST',
                headers={'X-Workcell-Observation-Token': wc_token, 'Content-Type': 'application/json'},
            )
            with urlopen(req, timeout=10) as resp:
                return json.load(resp)

        received_id = sha256(f'{wc_id}|obs-{cargo}|container.received_at_intake'.encode()).hexdigest()
        stored_id = sha256(f'{wc_id}|obs-store-{cargo}|container.stored'.encode()).hexdigest()
        post_event({
            'event_id': received_id, 'kind': 'container.received_at_intake',
            'observation_ref': 'obs-' + cargo, 'object_ref': cargo,
            'intake_point': config['steps'][0]['to'], 'identity': 'observed', 'observed_at': obs_ts,
        })
        post_event({
            'event_id': stored_id, 'kind': 'container.stored',
            'observation_ref': 'obs-store-' + cargo, 'object_ref': cargo,
            'slot_ref': 'yard-r1-c1-l1', 'identity': 'observed', 'observed_at': obs_ts,
        })
        with tempfile.TemporaryDirectory() as folder:
            intake = IntakeLedger(Path(folder) / 'intake.sqlite3')
            intake.expect(cargo, now=now)
            intake.poll(client, connection=config['origin'], now=now)
            row = intake.read(cargo)
            self.assertEqual(row['state'], 'COMPLETED')
            self.assertEqual(parse_slot_ref(row['slot_ref']), ('yard', 1, 1, 1))
            # Second poll with persisted cursor consumes zero new events.
            second = intake.poll(client, connection=config['origin'], now=now)
            self.assertEqual(second['outcomes'], {})
            self.assertIs(second['complete'], True)

    def test_z_dual_mode_cell_plan_harbor_and_rosy_over_real_http(self):
        """Dual-mode OMX cell-plan E2E: Mode 1 explicit Harbor plan order/coords vs Mode 2 ROSY autonomous plan."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        origin = config['origin'].rstrip('/')
        service = RosyClient(origin, config['service'])
        operator = RosyClient(origin, config['operator'])
        service.expected_principal, service.expected_role = 'cell-app-service', 'service'
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

        # Typed read-model verification against live ROSY endpoints.
        sess = service.session_typed()
        self.assertEqual((sess.principal_id, sess.role), ('cell-app-service', 'service'))
        places_typed = service.site_places_typed()
        self.assertEqual(places_typed.schema, 'rosy.port-site-places.v1')
        sites_raw = service.cell_sites()
        sites_typed = service.cell_sites_typed()
        self.assertEqual(len(sites_typed.sites), 37)
        alignment_report = compare_condition_with_rosy(
            None,
            cell_site_map=normalize_cell_site_map(sites_raw),
            site_places=places_typed,
        )
        self.assertIn('aligned', alignment_report)
        req_id_probe = Request(origin + '/api/fleet/session', headers={
            'Authorization': 'Bearer ' + config['service'],
            'X-Request-Id': 'harbor-e2e-trace-1',
        })
        with urlopen(req_id_probe, timeout=10) as resp:
            self.assertEqual(resp.headers.get('X-Request-Id'), 'harbor-e2e-trace-1')

        # Rule 8 capability probe: unknown request_key returns coded 404 UNKNOWN_CELL_PLAN.
        with self.assertRaises(HTTPError) as missing_probe:
            service.cell_plan_request('harbor-probe-' + uuid4().hex)
        self.assertEqual(missing_probe.exception.code, 404)
        self.assertEqual(json.loads(missing_probe.exception.read())['detail']['code'], 'UNKNOWN_CELL_PLAN')

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
        harbor_key = 'harbor-mode1-' + uuid4().hex
        res1 = validate_cell_plan_response(
            service.propose_cell_plan(harbor_req, request_key=harbor_key),
            expected_order_source='harbor',
            expected_step_count=3,
        )
        self.assertEqual([t['dst_wire_ref'] for t in res1['tasks']], ['temp_a-r1-c1-l1', 'stand-r1-c1-l1', 'yard-r1-c1-l1'])
        self.assertTrue(all(t['status'] == 'PROPOSED' for t in res1['tasks']))

        # Rule 7 lost-response reconciliation via GET ?request_key= returns exact created plan & tasks.
        reconciled1 = validate_cell_plan_response(
            service.cell_plan_request(harbor_key),
            expected_order_source='harbor',
            expected_step_count=3,
        )
        self.assertEqual(reconciled1['plan']['plan_id'], res1['plan']['plan_id'])
        self.assertEqual([t['task_id'] for t in reconciled1['tasks']], [t['task_id'] for t in res1['tasks']])

        # Bad Harbor order (pull buried C1 first) must fail closed with HTTP 422.
        bad_req = {**harbor_req, 'steps': [harbor_req['steps'][1]]}
        with self.assertRaises(HTTPError) as blocked:
            service.propose_cell_plan(bad_req, request_key='harbor-bad-' + uuid4().hex)
        self.assertEqual(blocked.exception.code, 422)
        self.assertEqual(json.loads(blocked.exception.read())['detail']['code'], 'BLOCKED_SOURCE_CARGO')

        # Batch plan admission via POST /api/fleet/pipeline/cell-plans/{plan_id}/admit.
        req_rdy = Request(origin + '/api/fleet/pipeline/cells/cell_ab/readiness', headers={'Authorization': 'Bearer ' + config['operator']})
        with urlopen(req_rdy, timeout=10) as resp:
            rdy = json.loads(resp.read().decode('utf-8'))
        req_batch = Request(
            origin + f"/api/fleet/pipeline/cell-plans/{res1['plan']['plan_id']}/admit",
            data=json.dumps({
                'expected_dispatch_generation': rdy['dispatch_generation'],
                'expected_stop_generation': rdy['stop_generation'],
                'expected_readiness_digest': rdy['readiness_digest'],
            }).encode('utf-8'),
            method='POST',
            headers={'Authorization': 'Bearer ' + config['operator'], 'Content-Type': 'application/json'},
        )
        with urlopen(req_batch, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            batch_res = json.loads(resp.read().decode('utf-8'))
        self.assertEqual(batch_res['plan']['status'], 'QUEUED')
        self.assertEqual(len(batch_res['admitted_task_ids']), 3)
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
        reconciled2 = validate_cell_plan_response(
            service.cell_plan_request(rosy_key),
            expected_order_source='rosy_r',
            expected_step_count=3,
        )
        self.assertEqual(reconciled2['plan']['plan_id'], res2['plan']['plan_id'])

        admit_plan_tasks(res2['tasks'])
        self.assertEqual(operator.cell_plan(res2['plan']['plan_id'])['plan']['status'], 'QUEUED')

        # Dry-run typed preview (service caller redacts cargo_id / final_state) + goal mode preview.
        dry = service.preview_cell_plan_typed(rosy_req, expected_order_source='rosy_r')
        self.assertEqual(len(dry.moves), 3)
        self.assertEqual(dry.final_state, ())
        dry_raw = service.preview_cell_plan(rosy_req)
        self.assertIsNone(dry_raw['final_state'])
        self.assertTrue(all('cargo_id' not in m for m in dry_raw['moves']))
        goal_req = build_rosy_directive_request(
            mode='goal',
            observation_payload=observation_payload,
            goal={'yard:r1-s2': ['C1', 'C2']},
        )
        goal_dry = operator.preview_cell_plan_typed(goal_req, expected_order_source='rosy_r')
        self.assertGreaterEqual(len(goal_dry.moves), 2)
        self.assertIsNotNone(goal_dry.final_state)
        op_plan = operator.propose_cell_plan_typed(
            {**harbor_req, 'propose': True},
            request_key='harbor-op-cancel-' + uuid4().hex,
            expected_order_source='harbor',
            expected_step_count=3,
        )
        with self.assertRaises(HTTPError) as cancel_denied:
            service.cancel_cell_plan(op_plan.plan.plan_id, reason='unauthorized')
        self.assertEqual(cancel_denied.exception.code, 403)
        self.assertEqual(json.loads(cancel_denied.exception.read())['detail']['code'], 'CELL_PLAN_CANCEL_FORBIDDEN')

        cancel_target = service.propose_cell_plan_typed(
            harbor_req,
            request_key='harbor-cancel-' + uuid4().hex,
            expected_order_source='harbor',
            expected_step_count=3,
        )
        cancelled = service.cancel_cell_plan_typed(
            cancel_target.plan.plan_id,
            reason='operator cancelled plan',
            expected_order_source='harbor',
        )
        self.assertEqual(cancelled.plan.status, 'CANCELLED')
        self.assertEqual(cancelled.plan.hold_reason, 'operator cancelled plan')
        self.assertEqual(list(cancelled.cancelled_task_ids), [t.task_id for t in cancel_target.tasks])
        with self.assertRaises(HTTPError) as cancel_again:
            service.cancel_cell_plan(cancel_target.plan.plan_id, reason='duplicate cancel')
        self.assertEqual(cancel_again.exception.code, 409)
        self.assertEqual(json.loads(cancel_again.exception.read())['detail']['code'], 'CELL_PLAN_NOT_CANCELLABLE')

    def test_z_outbound_stand_two_leg_pickup_and_exit_over_real_http(self):
        """Outbound Stand (S_OUT) -> Pinky docking & pickup -> Exit (S_EXIT) over real ROSY Fleet HTTP."""
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        service_token = config.get('service')
        if not service_token:
            self.skipTest('Service token not provided in harness config')
        operator_token = config['operator']
        approver_token = config['approver']
        proposer = RosyClient(config['origin'], service_token)
        proposer.expected_principal, proposer.expected_role = 'cell-app-service', 'service'

        # Default RealFleet map has unverified S_OUT: fail closed before commissioning.
        self.assertIsNone(resolve_outbound_dispatch_stops(parse_site_places(proposer.site_places()))[0])

        def call(token, method, path, body=None):
            req = Request(
                config['origin'].rstrip('/') + path,
                data=None if body is None else json.dumps(body).encode('utf-8'),
                method=method,
                headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
            )
            with urlopen(req, timeout=10) as resp:
                return json.load(resp)

        # Commission a free-mode ring containing verified S_OUT (stop_zone + approach_m + pinned) and S_EXIT.
        ring_places = [
            {'id': 'dock', 'name': 'dock', 'x': 0.0, 'y': 0.0, 'kind': 'junction'},
            {'id': 'inbound', 'name': 'inbound', 'x': 0.5, 'y': 0.0, 'kind': 'junction'},
            {
                'id': 'S_OUT', 'name': 'S_OUT 반출대', 'x': 0.5, 'y': 0.5, 'yaw': 1.570796, 'kind': 'stop',
                'pinned': True, 'stop_zone': [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]], 'approach_m': 0.1,
            },
            {'id': 'S_EXIT', 'name': 'S_EXIT 출구', 'x': 0.0, 'y': 0.5, 'yaw': 3.14159, 'kind': 'stop', 'pinned': True},
        ]
        coords = {p['id']: [p['x'], p['y']] for p in ring_places}
        ring_order = ['dock', 'inbound', 'S_OUT', 'S_EXIT', 'dock']
        site_map_doc = {
            'schema': 'rosy.site_map/1',
            'map_id': 'harbor-outbound-e2e',
            'places': ring_places,
            'edges': [
                {
                    'id': f'{a}-{b}', 'from': a, 'to': b, 'polyline': [coords[a], coords[b]],
                    'width_m': 0.2, 'speed_cap_mps': 0.2, 'drive_mode': 'free', 'pinned': True,
                }
                for a, b in zip(ring_order, ring_order[1:])
            ],
        }
        rules_doc = {
            'return': {'exit_place': 'S_EXIT'},
            'stops': [{'place': 'S_OUT', 'role': 'load'}, {'place': 'S_EXIT', 'role': 'wait'}],
        }
        draft = call(operator_token, 'PUT', '/api/fleet/site-map/draft', {
            'map': site_map_doc,
            'expected_revision': call(operator_token, 'GET', '/api/fleet/site-map/draft').get('revision'),
        })
        call(operator_token, 'POST', '/api/fleet/site-map/activate', {'expected_revision': draft['revision']})
        rdraft = call(operator_token, 'PUT', '/api/fleet/site-rules/draft', {
            'rules': rules_doc,
            'expected_revision': call(operator_token, 'GET', '/api/fleet/site-rules/draft').get('revision'),
        })
        call(operator_token, 'POST', '/api/fleet/site-rules/activate', {'expected_revision': rdraft['revision']})

        places_after = parse_site_places(proposer.site_places())
        s_out_place = places_after.vehicle_place('S_OUT')
        s_exit_place = places_after.vehicle_place('S_EXIT')
        self.assertIsNotNone(s_out_place)
        self.assertTrue(s_out_place.status == 'verified' and s_out_place.dispatchable)
        self.assertIsNotNone(s_exit_place)
        self.assertEqual((s_exit_place.port_role, s_exit_place.status, s_exit_place.dispatchable),
                         ('outbound_exit', 'verified', True))
        # Real cell-site-map.json keeps stand planned_unverified until cell calibration; resolve_outbound_dispatch_stops
        # fail-closes with OUTBOUND_ZONE_UNVERIFIED while the commissioned vehicle stops execute the 2-leg trip.
        self.assertEqual(resolve_outbound_dispatch_stops(places_after), (None, 'OUTBOUND_ZONE_UNVERIFIED'))
        stops = (s_out_place.place_id, s_exit_place.place_id)

        with tempfile.TemporaryDirectory() as folder:
            ledger = TransportLedger(Path(folder) / 'outbound.sqlite3')
            leg1_key = 'harbor-out-leg1-' + uuid4().hex
            leg1 = TripIntent(leg1_key, tuple(config['candidates']), stops[0], object_ref='')
            ledger.prepare(leg1)
            p1 = ledger.propose(leg1_key, proposer)
            self.assertNotIn('object_ref', p1)
            gen = call(approver_token, 'GET', '/api/fleet/dispatch-control')['generation']
            with self.assertRaises(HTTPError) as self_admit:
                call(service_token, 'POST', f"/api/fleet/trip-proposals/{p1['proposal_id']}/admit", {
                    'expected_proposal_digest': p1['proposal_digest'], 'expected_generation': gen,
                })
            self.assertEqual(self_admit.exception.code, 403)
            call(approver_token, 'POST', f"/api/fleet/trip-proposals/{p1['proposal_id']}/admit", {
                'expected_proposal_digest': p1['proposal_digest'], 'expected_generation': gen,
            })
            deadline = time.monotonic() + 30
            while True:
                job1 = ledger.reconcile_proposal(leg1_key, proposer)
                if job1['status'] == 'GOAL_CONFIRMED':
                    break
                self.assertNotEqual(job1['status'], 'HOLD', job1.get('reason'))
                if time.monotonic() >= deadline:
                    self.fail('Leg 1 S_OUT docking trip did not complete within 30 seconds')
                time.sleep(0.1)
            robot_id = job1['robot_id']
            self.assertEqual(job1['steps'][0]['step']['to'], 'S_OUT')

            leg2_key = 'harbor-out-leg2-' + uuid4().hex
            leg2 = TripIntent(leg2_key, (robot_id,), stops[1], object_ref='')
            ledger.prepare(leg2)
            p2 = ledger.propose(leg2_key, proposer)
            self.assertNotIn('object_ref', p2)
            gen2 = call(approver_token, 'GET', '/api/fleet/dispatch-control')['generation']
            call(approver_token, 'POST', f"/api/fleet/trip-proposals/{p2['proposal_id']}/admit", {
                'expected_proposal_digest': p2['proposal_digest'], 'expected_generation': gen2,
            })
            deadline = time.monotonic() + 30
            while True:
                job2 = ledger.reconcile_proposal(leg2_key, proposer)
                if job2['status'] == 'GOAL_CONFIRMED':
                    break
                self.assertNotEqual(job2['status'], 'HOLD', job2.get('reason'))
                if time.monotonic() >= deadline:
                    self.fail('Leg 2 S_EXIT exit trip did not complete within 30 seconds')
                time.sleep(0.1)
            self.assertEqual(job2['robot_id'], robot_id)
            self.assertEqual(job2['steps'][0]['step']['to'], 'S_EXIT')

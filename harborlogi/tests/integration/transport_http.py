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

from harbor.cell_site_map import normalize_cell_site_map
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
            self.assertEqual(second['events'], [])

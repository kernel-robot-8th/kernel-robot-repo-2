"""Wave 4 Harbor integration tests: outbound loaded port events and S3 ARM recovery client."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from harbor.port_events import IntakeLedger, parse_event
from harbor.rosy_http import RosyClient
from harbor_web import commands, outbound_loop
from harbor_web.command_models import OutboundDispatch
from harbor_web.models import Audit, Cargo, Command, Condition, Observation, Order, Plan

_NOW = datetime(2026, 11, 25, 2, 0, tzinfo=timezone.utc)
_EVENT_ID = 'a' * 64
_DIGEST = 'b' * 64


class _DummyTransport:
    def __init__(self):
        self.prepared = []

    def prepare(self, intent):
        self.prepared.append(intent.request_key)

    def read(self, _key):
        return {'state': 'PLANNED'}

    def propose(self, _key, _client):
        return {'mission_id': 'm-leg2', 'status': 'QUEUED', 'robot_id': 'rosy_46'}


class Wave4HarborIntegrationTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user(username='op1', password='pw')
        from django.contrib.auth.models import Group
        grp, _ = Group.objects.get_or_create(name='operator')
        self.operator.groups.add(grp)
        self.cargo = Cargo.objects.create(
            created_by=self.operator,
            code='CNTR-W4-1',
            name='Container W4',
            weight_kg=Decimal('10.000'),
            volume_m3=Decimal('1.0000'),
            destination='busan',
        )
        self.condition = Condition.objects.create(
            created_by=self.operator,
            name='cal-w4',
            calibration_ref='cal-1',
            payload={'zones': [], 'constraints': {'max_observation_age_s': 3600}},
        )
        self.order = Order.objects.create(created_by=self.operator, kind='OUTBOUND', cargo=self.cargo)
        self.obs = Observation.objects.create(
            created_by=self.operator,
            source_id='cam',
            source_revision='rev-w4',
            observed_at=_NOW,
            provenance='SYNTHETIC',
            payload={},
            condition=self.condition,
        )
        self.plan = Plan.objects.create(
            created_by=self.operator,
            order=self.order, order_version=1, observation=self.obs, condition=self.condition,
            queue_digest='0' * 64, result={'status': 'READY', 'moves': []},
        )

    def test_loaded_for_outbound_port_event_advances_pickup_arrived_to_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            ev = parse_event({
                'event_id': _EVENT_ID,
                'kind': 'container.loaded_for_outbound',
                'observation_ref': 'obs-out-1',
                'object_ref': 'CNTR-W4-1',
                'intake_point': None,
                'slot_ref': None,
                'vehicle_id': 'rosy_46',
                'reason': None,
                'identity': 'observed',
                'observed_at': '2026-11-25T02:00:00Z',
            })
            outcomes = intake.consume([ev], now=_NOW)
            self.assertEqual(outcomes[_EVENT_ID], 'OUTBOUND_LOADED')

            cmd = Command.objects.create(
                plan=self.plan,
                order=self.order,
                actor=self.operator,
                plan_version=1,
                ledger_identity='harbor-w4-outbound',
                fleet_origin='http://127.0.0.1:8000',
                fleet_principal='harbor-service',
                body_digest='d' * 64,
                binding={'moves': []},
                state='COMPLETED',
                mission_id='mission-out-1',
            )
            dispatch = OutboundDispatch.objects.create(
                command=cmd,
                cargo=self.cargo,
                actor=self.operator,
                stand_zone_id='stand',
                pickup_stop_id='S_OUT',
                exit_stop_id='S_EXIT',
                site_places_digest='c' * 64,
                candidate_robot_ids=['rosy_46'],
                state='PICKUP_ARRIVED',
                assigned_vehicle_id='rosy_46',
                pickup_mission_id='trip-leg1',
            )
            transport = _DummyTransport()
            client = SimpleNamespace(propose_trip=lambda *a, **kw: None, trip_request=lambda *a, **kw: None)
            registry = SimpleNamespace(transport=transport, client=client, config={'path': str(ledger_path)})

            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                res = outbound_loop.tick(registry, now=_NOW)
            self.assertEqual(res[dispatch.pk], 'EXIT_PROPOSED')
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.state, 'EXIT_PROPOSED')
            self.assertEqual(dispatch.exit_mission_id, 'm-leg2')
            self.assertEqual(dispatch.loaded_event_id, _EVENT_ID)
            self.assertTrue(any(k.startswith('harbor-outbound-exit:') for k in transport.prepared))
            self.assertTrue(Audit.objects.filter(action='outbound.loaded_from_port_event').exists())

    def test_rosy_client_arm_recovery_preview_and_request_preserve_rosy_authority(self):
        # Confirm RosyClient never exposes admit/approve helpers.
        for name in dir(RosyClient):
            self.assertNotIn('admit', name.lower())
            self.assertNotIn('approve', name.lower())

        calls = []

        def transport(method, path, body):
            calls.append((method, path, body))
            if method == 'GET':
                return {'recovery': {'status': 'AVAILABLE', 'can_request': True, 'context_digest': _DIGEST}}
            return {'request': {'request_id': 'rec-1', 'request_key': body['request_key']}}

        client = RosyClient(transport=transport)
        cfg = {'origin': 'http://127.0.0.1:8000', 'principal': 'harbor-service', 'path': ':memory:'}
        binding = {'moves': []}
        with patch.object(commands, '_identity', return_value='harbor:w4:ledger'):
            command = Command.objects.create(
                plan=self.plan,
                order=self.order,
                actor=self.operator,
                plan_version=1,
                ledger_identity='harbor:w4:ledger',
                fleet_origin=cfg['origin'],
                fleet_principal=cfg['principal'],
                body_digest=commands._digest(binding),
                binding=binding,
                state='CANCEL_PENDING',
                reason='BUSINESS_HOLD',
                mission_id='mission-s3-1',
            )
            registry = SimpleNamespace(
                client=client,
                config=cfg,
                lock=lambda _id: __import__('contextlib').nullcontext(),
            )
            with patch.object(commands, 'acquire_registry', return_value=registry):
                reply = commands.request_arm_recovery(self.operator, command_id=command.pk, expected_version=command.version)
        self.assertEqual(reply['request']['request_id'], 'rec-1')
        self.assertEqual(calls[0], ('GET', '/api/fleet/arm-transfers/jobs/mission-s3-1/recovery', None))
        self.assertEqual(calls[1][0], 'POST')
        self.assertEqual(calls[1][1], '/api/fleet/arm-transfers/jobs/mission-s3-1/recovery')
        self.assertEqual(calls[1][2]['expected_context_digest'], _DIGEST)
        command.refresh_from_db()
        self.assertEqual(command.reason, 'BUSINESS_HOLD')
        self.assertTrue(Audit.objects.filter(action='command.arm_recovery_requested').exists())

        self.client.force_login(self.operator)
        with patch.object(commands, 'request_arm_recovery', return_value={'request': {'request_id': 'rec-1'}}) as mock_req:
            resp = self.client.post(f'/work/{command.pk}/', {'action': 'arm_recovery_request', 'expected_version': command.version})
            self.assertEqual(resp.status_code, 302)
            mock_req.assert_called_once()

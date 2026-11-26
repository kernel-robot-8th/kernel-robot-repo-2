"""Wave 4 Harbor integration tests: outbound loaded port events and S3 ARM recovery client."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

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

    def _pickup_arrived_dispatch(self, suffix, **extra):
        plan = Plan.objects.create(
            created_by=self.operator,
            order=self.order, order_version=1, observation=self.obs, condition=self.condition,
            queue_digest='0' * 64, result={'status': 'READY', 'moves': []},
        )
        cmd = Command.objects.create(
            plan=plan,
            order=self.order,
            actor=self.operator,
            plan_version=1,
            ledger_identity=f'harbor-w4-outbound-{suffix}',
            fleet_origin='http://127.0.0.1:8000',
            fleet_principal='harbor-service',
            body_digest='d' * 64,
            binding={'moves': []},
            state='COMPLETED',
            mission_id=f'mission-out-{suffix}',
        )
        fields = dict(
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
        fields.update(extra)
        return OutboundDispatch.objects.create(**fields)

    def _ledger_event(self, intake, *, event_id, observed_at):
        ev = parse_event({
            'event_id': event_id,
            'kind': 'container.loaded_for_outbound',
            'observation_ref': f'obs-{event_id[:8]}',
            'object_ref': 'CNTR-W4-1',
            'intake_point': None,
            'slot_ref': None,
            'vehicle_id': 'rosy_46',
            'reason': None,
            'identity': 'observed',
            'observed_at': observed_at,
        })
        outcomes = intake.consume([ev], now=_NOW)
        self.assertEqual(outcomes[event_id], 'OUTBOUND_LOADED')

    def _tick_registry(self, ledger_path):
        transport = _DummyTransport()
        client = SimpleNamespace(propose_trip=lambda *a, **kw: None, trip_request=lambda *a, **kw: None)
        registry = SimpleNamespace(transport=transport, client=client, config={'path': str(ledger_path)})
        return registry, transport

    def test_loaded_for_outbound_port_event_advances_pickup_arrived_to_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            dispatch = self._pickup_arrived_dispatch('advance')
            # created_at is wall-clock (auto_now_add); an event observed at/after creation is fresh.
            self._ledger_event(intake, event_id=_EVENT_ID, observed_at=dispatch.created_at.isoformat())
            registry, transport = self._tick_registry(ledger_path)

            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                res = outbound_loop.tick(registry)
            self.assertEqual(res[dispatch.pk], 'EXIT_PROPOSED')
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.state, 'EXIT_PROPOSED')
            self.assertEqual(dispatch.exit_mission_id, 'm-leg2')
            self.assertEqual(dispatch.loaded_event_id, _EVENT_ID)
            self.assertTrue(any(k.startswith('harbor-outbound-exit:') for k in transport.prepared))
            self.assertTrue(Audit.objects.filter(action='outbound.loaded_from_port_event').exists())

    def test_claimed_event_does_not_advance_recreated_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            self._pickup_arrived_dispatch(
                'claimed-prior', state='CANCELLED', loaded_event_id=_EVENT_ID, reason='OPERATOR_CANCELLED',
            )
            dispatch = self._pickup_arrived_dispatch('recreated')
            self._ledger_event(intake, event_id=_EVENT_ID, observed_at=dispatch.created_at.isoformat())
            registry, transport = self._tick_registry(ledger_path)

            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                res = outbound_loop.tick(registry)
            self.assertNotIn(dispatch.pk, res)
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.state, 'PICKUP_ARRIVED')
            self.assertEqual(dispatch.loaded_event_id, '')
            self.assertFalse(any(k.startswith('harbor-outbound-exit:') for k in transport.prepared))
            self.assertFalse(Audit.objects.filter(action='outbound.loaded_from_port_event').exists())

    def test_stale_event_does_not_advance_recreated_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            self._ledger_event(intake, event_id=_EVENT_ID, observed_at='2020-01-01T00:00:00Z')
            dispatch = self._pickup_arrived_dispatch('stale-new')
            registry, transport = self._tick_registry(ledger_path)

            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                res = outbound_loop.tick(registry)
            self.assertNotIn(dispatch.pk, res)
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.state, 'PICKUP_ARRIVED')
            self.assertEqual(dispatch.loaded_event_id, '')
            self.assertFalse(any(k.startswith('harbor-outbound-exit:') for k in transport.prepared))
            self.assertFalse(Audit.objects.filter(action='outbound.loaded_from_port_event').exists())

    def test_fresh_unclaimed_event_advances_recreated_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            dispatch = self._pickup_arrived_dispatch('fresh-new')
            observed = (dispatch.created_at + timedelta(minutes=1)).isoformat()
            self._ledger_event(intake, event_id=_EVENT_ID, observed_at=observed)
            registry, transport = self._tick_registry(ledger_path)

            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                res = outbound_loop.tick(registry)
            self.assertEqual(res[dispatch.pk], 'EXIT_PROPOSED')
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.state, 'EXIT_PROPOSED')
            self.assertEqual(dispatch.exit_mission_id, 'm-leg2')
            self.assertEqual(dispatch.loaded_event_id, _EVENT_ID)
            self.assertTrue(any(k.startswith('harbor-outbound-exit:') for k in transport.prepared))
            self.assertTrue(Audit.objects.filter(action='outbound.loaded_from_port_event').exists())

    def test_single_event_does_not_advance_two_waiting_dispatches_in_same_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            first = self._pickup_arrived_dispatch('same-tick-1', stand_zone_id='stand-1')
            second = self._pickup_arrived_dispatch('same-tick-2', stand_zone_id='stand-2')
            observed = (second.created_at + timedelta(minutes=1)).isoformat()
            self._ledger_event(intake, event_id=_EVENT_ID, observed_at=observed)
            registry, transport = self._tick_registry(ledger_path)

            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                res = outbound_loop.tick(registry)
            self.assertEqual(res[first.pk], 'EXIT_PROPOSED')
            self.assertNotIn(second.pk, res)
            first.refresh_from_db()
            second.refresh_from_db()
            self.assertEqual(first.loaded_event_id, _EVENT_ID)
            self.assertEqual(second.state, 'PICKUP_ARRIVED')
            self.assertEqual(second.loaded_event_id, '')
            self.assertEqual(set(transport.prepared), {f'harbor-outbound-exit:{first.key}'})

    def test_malformed_stored_timestamp_is_ignored_without_advancing(self):
        dispatch = self._pickup_arrived_dispatch('bad-ts')
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            ledger_path.write_text('', encoding='utf-8')
            registry, transport = self._tick_registry(ledger_path)
            bad_event = {
                'event_id': _EVENT_ID,
                'kind': 'container.loaded_for_outbound',
                'object_ref': 'CNTR-W4-1',
                'vehicle_id': 'rosy_46',
                'observed_at': 'not-a-timestamp',
            }
            with patch('harbor.port_events.IntakeLedger.outbound_loaded_events', return_value=[bad_event]):
                with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}):
                    res = outbound_loop.tick(registry)
            self.assertNotIn(dispatch.pk, res)
            dispatch.refresh_from_db()
            self.assertEqual(dispatch.state, 'PICKUP_ARRIVED')
            self.assertEqual(dispatch.loaded_event_id, '')
            self.assertEqual(transport.prepared, [])

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

    def test_loaded_event_id_partial_unique_constraint_allows_empty_and_blocks_duplicates(self):
        # Multiple dispatches with empty loaded_event_id are allowed.
        self._pickup_arrived_dispatch('empty-1', stand_zone_id='stand-1', loaded_event_id='')
        self._pickup_arrived_dispatch('empty-2', stand_zone_id='stand-2', loaded_event_id='')

        # Claiming a non-empty loaded_event_id once succeeds; claiming it again fails at DB level.
        self._pickup_arrived_dispatch(
            'claimed-1', stand_zone_id='stand-3', state='CANCELLED', loaded_event_id=_EVENT_ID,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._pickup_arrived_dispatch(
                'claimed-2', stand_zone_id='stand-4', state='EXITED', loaded_event_id=_EVENT_ID,
            )

    def test_clock_skew_tolerance_default_and_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            registry, transport = self._tick_registry(ledger_path)

            # Default 30s: 31s before created_at is rejected as stale.
            d_stale = self._pickup_arrived_dispatch('skew-stale', stand_zone_id='stand-1')
            ev_stale = '1' * 64
            self._ledger_event(
                intake, event_id=ev_stale,
                observed_at=(d_stale.created_at - timedelta(seconds=31)).isoformat(),
            )
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}, clear=False):
                os.environ.pop('HARBOR_PORT_EVENT_CLOCK_SKEW_S', None)
                res = outbound_loop.tick(registry)
            self.assertNotIn(d_stale.pk, res)
            d_stale.refresh_from_db()
            self.assertEqual(d_stale.state, 'PICKUP_ARRIVED')

            # Default 30s: 20s before created_at is accepted within skew tolerance.
            ev_within = '2' * 64
            self._ledger_event(
                intake, event_id=ev_within,
                observed_at=(d_stale.created_at - timedelta(seconds=20)).isoformat(),
            )
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}, clear=False):
                res = outbound_loop.tick(registry)
            self.assertEqual(res[d_stale.pk], 'EXIT_PROPOSED')
            d_stale.refresh_from_db()
            self.assertEqual(d_stale.loaded_event_id, ev_within)

            # Custom HARBOR_PORT_EVENT_CLOCK_SKEW_S=10: 15s before rejected, 5s before accepted.
            d_custom = self._pickup_arrived_dispatch('skew-custom', stand_zone_id='stand-2')
            ev_too_old = '3' * 64
            self._ledger_event(
                intake, event_id=ev_too_old,
                observed_at=(d_custom.created_at - timedelta(seconds=15)).isoformat(),
            )
            with patch.dict(
                os.environ,
                {'HARBOR_LEDGER_PATH': str(ledger_path), 'HARBOR_PORT_EVENT_CLOCK_SKEW_S': '10'},
                clear=False,
            ):
                res = outbound_loop.tick(registry)
            self.assertNotIn(d_custom.pk, res)

            ev_ok = '4' * 64
            self._ledger_event(
                intake, event_id=ev_ok,
                observed_at=(d_custom.created_at - timedelta(seconds=5)).isoformat(),
            )
            with patch.dict(
                os.environ,
                {'HARBOR_LEDGER_PATH': str(ledger_path), 'HARBOR_PORT_EVENT_CLOCK_SKEW_S': '10'},
                clear=False,
            ):
                res = outbound_loop.tick(registry)
            self.assertEqual(res[d_custom.pk], 'EXIT_PROPOSED')
            d_custom.refresh_from_db()
            self.assertEqual(d_custom.loaded_event_id, ev_ok)

            # HARBOR_PORT_EVENT_CLOCK_SKEW_S=0 enforces strict observed >= created_at.
            d_zero = self._pickup_arrived_dispatch('skew-zero', stand_zone_id='stand-3')
            ev_zero = '5' * 64
            self._ledger_event(
                intake, event_id=ev_zero,
                observed_at=(d_zero.created_at - timedelta(seconds=1)).isoformat(),
            )
            with patch.dict(
                os.environ,
                {'HARBOR_LEDGER_PATH': str(ledger_path), 'HARBOR_PORT_EVENT_CLOCK_SKEW_S': '0'},
                clear=False,
            ):
                res = outbound_loop.tick(registry)
            self.assertNotIn(d_zero.pk, res)

            # Invalid or negative HARBOR_PORT_EVENT_CLOCK_SKEW_S falls back to default 30s.
            for bad_val in ('-10', 'not-a-number', 'nan'):
                with self.subTest(skew=bad_val), patch.dict(
                    os.environ, {'HARBOR_PORT_EVENT_CLOCK_SKEW_S': bad_val}, clear=False
                ):
                    self.assertEqual(outbound_loop._port_event_clock_skew(), timedelta(seconds=30))

    def test_already_claimed_event_within_skew_window_never_matches_next_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / 'intake.sqlite3'
            intake = IntakeLedger(ledger_path)
            registry, transport = self._tick_registry(ledger_path)

            ev_first = '6' * 64
            ev_second = '7' * 64
            prior = self._pickup_arrived_dispatch('prior-exited', stand_zone_id='stand-1')
            # First dispatch claims ev_first and completes (EXITED).
            self._ledger_event(intake, event_id=ev_first, observed_at=prior.created_at.isoformat())
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}, clear=False):
                os.environ.pop('HARBOR_PORT_EVENT_CLOCK_SKEW_S', None)
                res = outbound_loop.tick(registry)
            self.assertEqual(res[prior.pk], 'EXIT_PROPOSED')
            OutboundDispatch.objects.filter(pk=prior.pk).update(state='EXITED')

            # Second dispatch arrives right after; ev_first is still within the 30s skew window,
            # but must NEVER match because prior already claimed it.
            later = self._pickup_arrived_dispatch('later-after-exit', stand_zone_id='stand-1')
            transport.prepared.clear()
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}, clear=False):
                res = outbound_loop.tick(registry)
            self.assertNotIn(later.pk, res)
            later.refresh_from_db()
            self.assertEqual(later.state, 'PICKUP_ARRIVED')
            self.assertEqual(later.loaded_event_id, '')
            self.assertEqual(transport.prepared, [])

            # Once a new unclaimed event arrives, the second dispatch skips ev_first and claims ev_second.
            self._ledger_event(
                intake, event_id=ev_second,
                observed_at=(later.created_at - timedelta(seconds=10)).isoformat(),
            )
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_path)}, clear=False):
                res = outbound_loop.tick(registry)
            self.assertEqual(res[later.pk], 'EXIT_PROPOSED')
            later.refresh_from_db()
            self.assertEqual(later.loaded_event_id, ev_second)


class OutboundLoadedEventMigrationTests(TransactionTestCase):
    def test_migration_0031_deduplicates_existing_loaded_event_ids_and_enforces_constraint(self):
        before = [('harbor_web', '0030_commercial_manifest')]
        after = [('harbor_web', '0031_outbound_dispatch_unique_loaded_event_id')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            User = apps.get_model('auth', 'User')
            Cargo = apps.get_model('harbor_web', 'Cargo')
            Condition = apps.get_model('harbor_web', 'Condition')
            Order = apps.get_model('harbor_web', 'Order')
            Observation = apps.get_model('harbor_web', 'Observation')
            Plan = apps.get_model('harbor_web', 'Plan')
            CommandModel = apps.get_model('harbor_web', 'Command')
            OD = apps.get_model('harbor_web', 'OutboundDispatch')

            user = User.objects.create(username='mig-op')
            cargo = Cargo.objects.create(
                created_by=user, code='MIG-C1', name='MIG-C1',
                weight_kg=Decimal('1.000'), volume_m3=Decimal('1.0000'), destination='out',
            )
            cond = Condition.objects.create(created_by=user, name='c', calibration_ref='ref', payload={})
            order = Order.objects.create(created_by=user, kind='OUTBOUND', cargo=cargo)
            obs = Observation.objects.create(
                created_by=user, source_id='s', source_revision='1', observed_at=_NOW,
                provenance='DECLARED', payload={}, condition=cond,
            )

            def make_od(idx, state):
                plan = Plan.objects.create(
                    created_by=user, order=order, order_version=1, observation=obs, condition=cond,
                    queue_digest='0' * 64, result={'status': 'READY', 'moves': []},
                )
                cmd = CommandModel.objects.create(
                    plan=plan, order=order, actor=user, plan_version=1,
                    ledger_identity=f'mig-{idx}', fleet_origin='http://127.0.0.1:8000',
                    fleet_principal='harbor-service', body_digest='d' * 64, binding={}, state='COMPLETED',
                )
                return OD.objects.create(
                    command=cmd, cargo=cargo, actor=user,
                    stand_zone_id=f'stand-{idx}', pickup_stop_id='S_OUT', exit_stop_id='S_EXIT',
                    site_places_digest='c' * 64, candidate_robot_ids=['rosy_46'],
                    state=state, assigned_vehicle_id='rosy_46', loaded_event_id=_EVENT_ID,
                )

            first = make_od(1, 'EXITED')
            cancelled_dup = make_od(2, 'CANCELLED')
            exited_dup = make_od(3, 'EXITED')

            forward = MigrationExecutor(connection)
            forward.migrate(after)

            first_after = OutboundDispatch.objects.get(pk=first.pk)
            cancelled_after = OutboundDispatch.objects.get(pk=cancelled_dup.pk)
            exited_after = OutboundDispatch.objects.get(pk=exited_dup.pk)
            self.assertEqual(first_after.loaded_event_id, _EVENT_ID)
            self.assertEqual(cancelled_after.loaded_event_id, '')
            self.assertTrue(exited_after.loaded_event_id.startswith(f'dup-{exited_dup.pk}:'))
            self.assertNotEqual(exited_after.loaded_event_id, _EVENT_ID)
        finally:
            cleanup = MigrationExecutor(connection)
            cleanup.migrate(cleanup.loader.graph.leaf_nodes())

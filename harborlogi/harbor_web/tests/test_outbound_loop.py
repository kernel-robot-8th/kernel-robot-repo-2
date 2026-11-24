"""Two-leg Outbound Stand -> Pinky Docking & Pickup -> Exit tests (ADR 0029 / D-908)."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from harbor.rosy_http import RosyClient
from harbor.transport import TransportLedger, TripIntent
from harbor_web import commands, outbound_loop, services
from harbor_web.models import Command, OutboundDispatch, Plan

PRINCIPAL = 'harbor-service'


def _canonical_places(*, outbound_verified=True, exit_id='S_EXIT', exit_role='outbound_exit'):
    vp_status = 'verified' if outbound_verified else 'planned_unverified'
    vp_disp = outbound_verified
    body = {
        'schema': 'rosy.port-site-places.v1',
        'sources': {'site_map': {'map_id': 'w', 'version': 1, 'sha256': 'a' * 64},
                    'cell_site_map': {'schema': 'rosy.cell-site-map.v1', 'revision': 1, 'status': 'verified'}},
        'vehicle_places': [
            {'place_id': 'S1', 'port_role': 'inbound_load', 'status': 'verified', 'dispatchable': True, 'label': 'S1'},
            {'place_id': 'S3', 'port_role': 'intake_dropoff', 'status': 'verified', 'dispatchable': True, 'label': 'S3'},
            {'place_id': 'S_OUT', 'port_role': 'outbound_pickup', 'status': vp_status, 'dispatchable': vp_disp, 'label': 'S_OUT'},
            {'place_id': exit_id, 'port_role': exit_role, 'status': vp_status, 'dispatchable': vp_disp, 'label': exit_id},
        ] if exit_id != 'S1' else [
            {'place_id': 'S1', 'port_role': 'inbound_load', 'status': 'verified', 'dispatchable': True, 'label': 'S1'},
            {'place_id': 'S_OUT', 'port_role': 'outbound_pickup', 'status': vp_status, 'dispatchable': vp_disp, 'label': 'S_OUT'},
        ],
        'cell_zones': [
            {'zone_id': 'yard', 'role': 'storage', 'status': 'verified', 'label': '야드'},
            {'zone_id': 'stand', 'role': 'outbound', 'status': 'verified', 'label': '반출대'},
        ],
        'intake_handoffs': [],
        'outbound_handoffs': [{
            'handoff_id': 'outbound_stand_load',
            'cell_zone_id': 'stand',
            'wire_slot_ref': 'stand-r1-c1-l1',
            'vehicle_pickup_stop_id': 'S_OUT',
            'vehicle_exit_stop_id': exit_id,
            'status': vp_status,
            'dispatchable': vp_disp,
        }],
        'not_places': ['belt'],
    }
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return {**body, 'digest': sha256(encoded.encode('utf-8')).hexdigest()}


class OutboundFleet:
    """Fake ROSY Fleet covering site-points?view=port, D-908 trip-proposals, and HOLD/resume."""
    def __init__(self):
        self.places = _canonical_places()
        self.proposals, self.jobs, self.bodies = {}, {}, []
        self.seq = 0

    def __call__(self, method, path, body):
        if path == '/api/fleet/session':
            return {'principal_id': PRINCIPAL, 'role': 'service'}
        if path == '/api/fleet/site-points?view=port':
            return self.places
        if (method, path) == ('POST', '/api/fleet/trip-proposals'):
            self.bodies.append(body)
            self.seq += 1
            key, step = body['request_key'], body['steps'][0]
            self.proposals.setdefault(key, dict(
                proposal_id=f'p-{self.seq}', proposal_principal_id=PRINCIPAL, request_key=key, status='PROPOSED',
                request_digest=TripIntent(key, tuple(body['candidate_robot_ids']), step['to'], tuple(step['via']),
                                          body.get('object_ref') or '').digest,
                candidate_robot_ids=body['candidate_robot_ids'], steps=body['steps'], object_ref=body.get('object_ref'),
                dispatch_generation=None, approved_by=None, mission_id=None))
            return {'proposal': self.proposals[key]}
        if path.startswith('/api/fleet/trip-proposals?request_key='):
            return {'proposal': self.proposals[unquote(path.split('=', 1)[1])]}
        if path.startswith('/api/fleet/trip-jobs?request_key='):
            return {'job': self.jobs[unquote(path.split('=', 1)[1])]}
        if path.startswith('/api/fleet/port-events?'):
            return {'events': [], 'cursor': 'c', 'complete': True}
        raise AssertionError(f'unexpected {method} {path}')

    def admit_and_set(self, key, *, robot_id='pinky-1', status='GOAL_CONFIRMED', reason=None):
        proposal = self.proposals[key]
        mission_id = f'trip-{proposal["proposal_id"]}'
        proposal.update(status='ADMITTED', dispatch_generation=1, approved_by='operator-2', mission_id=mission_id)
        self.jobs[key] = dict(
            mission_id=mission_id, request_key=key, request_digest=proposal['request_digest'],
            robot_id=robot_id, status=status, current_step_index=1 if status == 'GOAL_CONFIRMED' else 0,
            reason=reason,
            steps=[dict(step_index=0, step=proposal['steps'][0], status=status,
                        action_id='a-1', attempt_id='a-1', reason=reason)],
        )


class OutboundLoopTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / 'ledger.sqlite3')
        self.operator = User.objects.create_user('out-op')
        self.operator.groups.add(Group.objects.get_or_create(name='operator')[0])
        self.field = User.objects.create_user('out-field')
        self.field.groups.add(Group.objects.get_or_create(name='field')[0])
        self.viewer = User.objects.create_user('out-viewer')
        self.viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.admin = User.objects.create_user('out-admin')
        self.admin.groups.add(Group.objects.get_or_create(name='admin')[0])
        self.fleet = OutboundFleet()
        self.client = RosyClient(transport=self.fleet)
        self.client.expected_principal, self.client.expected_role = PRINCIPAL, 'service'
        self.registry = SimpleNamespace(
            config={'path': self.path, 'origin': 'https://fleet-r.invalid'},
            client=self.client,
            transport=TransportLedger(self.path),
        )
        patcher = mock.patch.dict(os.environ, {
            'HARBOR_LEDGER_PATH': self.path,
            'HARBOR_OUTBOUND_VEHICLES': 'pinky-1,pinky-2',
            'HARBOR_TRIP_DESTINATION': '',
        })
        patcher.start()
        self.addCleanup(patcher.stop)

        root = Path(__file__).resolve().parents[2]
        policy = json.loads((root / 'config/spatial-policy-3x6.json').read_text(encoding='utf-8'))
        policy['yard_layout'] = json.loads((root / 'config/yard-layout-3x6.json').read_text(encoding='utf-8'))
        self.condition = services.register_condition(
            self.admin, name='기본', calibration_ref='syn', payload=policy,
        )
        self.cargo1 = services.register_cargo(self.operator, code='OUT-1', name='OUT-1', weight_kg=1, volume_m3='0.01', destination='출하장')
        self.cargo2 = services.register_cargo(self.operator, code='OUT-2', name='OUT-2', weight_kg=1, volume_m3='0.01', destination='출하장')
        self.obs = services.record_observation(
            self.admin, source_id='cam', source_revision='1', observed_at=timezone.now(),
            provenance='SYNTHETIC', condition=self.condition,
            payload={'schema': 'grid-placement-v1', 'observed_areas': ['yard', 'temp_a', 'temp_b', 'stand'], 'holdings': [], 'placements': []},
        )

        self.seq = 0

    def _completed_command(self, cargo, *, state='COMPLETED', binding=None):
        self.seq += 1
        order = services.create_order(self.operator, cargo=cargo, kind='OUTBOUND', priority_group=1, sequence=self.seq, tie_policy='FIXED')
        plan = Plan.objects.create(
            created_by=self.operator,
            order=order, condition=self.condition, observation=self.obs,
            queue_digest='0' * 64, order_version=order.version,
            result={'status': 'READY', 'steps': []},
        )
        return Command.objects.create(
            order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='l', fleet_origin='https://fleet-r.invalid', fleet_principal=PRINCIPAL,
            body_digest='a' * 64, binding=binding if binding is not None else {'outbound_stand': 'stand'}, state=state,
        )

    def test_two_leg_outbound_flow_blocks_exit_until_loaded_and_pins_vehicle(self):
        cmd = self._completed_command(self.cargo1)
        dispatch = outbound_loop.prepare_outbound_dispatch(
            self.operator, command_id=cmd.pk, registry=self.registry,
        )
        self.assertEqual((dispatch.state, dispatch.pickup_stop_id, dispatch.exit_stop_id), ('PICKUP_PLANNED', 'S_OUT', 'S_EXIT'))

        # Tick 1: Leg 1 proposed with multi-candidate pool and NO object_ref.
        outbound_loop.tick(self.registry, now=timezone.now())
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.state, 'PICKUP_PROPOSED')
        self.assertEqual(self.fleet.bodies[0]['candidate_robot_ids'], ['pinky-1', 'pinky-2'])
        self.assertEqual(self.fleet.bodies[0]['steps'], [{'to': 'S_OUT', 'via': []}])
        self.assertNotIn('object_ref', self.fleet.bodies[0])

        # Leg 1 arrives at S_OUT with pinky-2: stays at PICKUP_ARRIVED across repeated ticks until loaded.
        self.fleet.admit_and_set(outbound_loop.pickup_trip_key(dispatch), robot_id='pinky-2', status='GOAL_CONFIRMED')
        outbound_loop.tick(self.registry, now=timezone.now())
        outbound_loop.tick(self.registry, now=timezone.now())
        dispatch.refresh_from_db()
        self.assertEqual((dispatch.state, dispatch.assigned_vehicle_id), ('PICKUP_ARRIVED', 'pinky-2'))
        self.assertEqual(len(self.fleet.bodies), 1)

        # Named field person confirms cargo transferred onto pinky-2.
        outbound_loop.confirm_outbound_loaded(
            self.field, dispatch_id=dispatch.pk, expected_version=dispatch.version, registry=self.registry,
        )
        outbound_loop.tick(self.registry, now=timezone.now())
        self.assertEqual(len(self.fleet.bodies), 2)
        self.assertEqual(self.fleet.bodies[1]['candidate_robot_ids'], ['pinky-2'])
        self.assertEqual(self.fleet.bodies[1]['steps'], [{'to': 'S_EXIT', 'via': []}])
        self.assertNotIn('object_ref', self.fleet.bodies[1])

        self.fleet.admit_and_set(outbound_loop.exit_trip_key(dispatch), robot_id='pinky-2', status='GOAL_CONFIRMED')
        outbound_loop.tick(self.registry, now=timezone.now())
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.state, 'EXITED')

    def test_fail_closed_gates_and_single_active_stand_uniqueness(self):
        uncompleted = self._completed_command(self.cargo1, state='RECONCILED')
        with self.assertRaises(ValidationError):
            outbound_loop.prepare_outbound_dispatch(self.operator, command_id=uncompleted.pk, registry=self.registry)

        cmd1 = self._completed_command(self.cargo2)
        self.fleet.places = _canonical_places(outbound_verified=False)
        with self.assertRaises(ValidationError):
            outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd1.pk, registry=self.registry)

        self.fleet.places = _canonical_places(outbound_verified=True, exit_id='S1', exit_role='inbound_load')
        with self.assertRaises(ValidationError):
            outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd1.pk, registry=self.registry)

        self.fleet.places = _canonical_places(outbound_verified=True)
        with mock.patch.dict(os.environ, {'HARBOR_OUTBOUND_VEHICLES': ''}):
            with self.assertRaises(ValidationError):
                outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd1.pk, registry=self.registry)

        d1 = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd1.pk, registry=self.registry)
        c_done = self._completed_command(
            services.register_cargo(self.operator, code='OUT-3', name='OUT-3', weight_kg=1, volume_m3='0.01', destination='출하장')
        )
        with self.assertRaises(services.Conflict):
            outbound_loop.prepare_outbound_dispatch(self.operator, command_id=c_done.pk, registry=self.registry)

        # Once d1 reaches EXITED, the stand opens for the next outbound dispatch.
        OutboundDispatch.objects.filter(pk=d1.pk).update(
            state='EXITED', assigned_vehicle_id='pinky-1', loaded_by=self.field, version=d1.version + 1,
        )
        d2 = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=c_done.pk, registry=self.registry)
        self.assertEqual(d2.state, 'PICKUP_PLANNED')

    def test_stand_anchor_bindings_normalize_to_stand(self):
        for raw in ('out', 'stand-r1-c1', 'stand:r1', 'stand_r1'):
            cmd = self._completed_command(self.cargo1, binding={'outbound_stand': raw})
            d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
            self.assertEqual(d.stand_zone_id, 'stand', raw)
            OutboundDispatch.objects.filter(pk=d.pk).update(state='CANCELLED')
        empty = self._completed_command(self.cargo1, binding={})
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=empty.pk, registry=self.registry)
        self.assertEqual(d.stand_zone_id, 'stand')

    def test_operator_cancel_closes_dispatch_and_releases_stand_and_vehicle(self):
        cmd = self._completed_command(self.cargo1)
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
        k1 = outbound_loop.pickup_trip_key(d)
        outbound_loop.tick(self.registry, now=timezone.now())
        self.fleet.admit_and_set(k1, robot_id='pinky-1', status='GOAL_CONFIRMED')
        outbound_loop.tick(self.registry, now=timezone.now())
        d.refresh_from_db()
        self.assertEqual((d.state, d.assigned_vehicle_id), ('PICKUP_ARRIVED', 'pinky-1'))

        cancelled = outbound_loop.cancel_outbound_dispatch(self.operator, dispatch_id=d.pk, reason='운영자 반출 취소')
        self.assertEqual(cancelled.state, 'CANCELLED')
        cancelled.refresh_from_db()
        self.assertEqual((cancelled.state, cancelled.reason), ('CANCELLED', '운영자 반출 취소'))
        from harbor_web.models import Audit
        self.assertTrue(Audit.objects.filter(
            action='outbound.dispatch_cancelled', object_id=str(d.pk)).exists())

        # The stand and the vehicle are free again for the next outbound.
        cmd2 = self._completed_command(self.cargo2)
        d2 = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd2.pk, registry=self.registry)
        self.assertEqual(d2.state, 'PICKUP_PLANNED')
        self.assertNotIn('pinky-1', outbound_loop.reserved_vehicles())

        outbound_loop.cancel_outbound_dispatch(self.operator, dispatch_id=d2.pk)
        with self.assertRaises(services.Conflict):
            outbound_loop.cancel_outbound_dispatch(self.operator, dispatch_id=d2.pk)
        with self.assertRaises(ValidationError):
            outbound_loop.cancel_outbound_dispatch(self.operator, dispatch_id=99999)

    def test_operator_cancel_blocked_for_viewer_and_reopens_after_cancel(self):
        cmd = self._completed_command(self.cargo1)
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
        with self.assertRaises(PermissionDenied):
            outbound_loop.cancel_outbound_dispatch(self.viewer, dispatch_id=d.pk)

    def test_tick_marks_cancelled_when_rosy_reports_operator_cancellation(self):
        cmd = self._completed_command(self.cargo1)
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
        k1 = outbound_loop.pickup_trip_key(d)
        outbound_loop.tick(self.registry, now=timezone.now())
        d.refresh_from_db()
        self.assertEqual(d.state, 'PICKUP_PROPOSED')

        self.fleet.admit_and_set(k1, robot_id='pinky-1', status='HOLD', reason='CANCELLED_BY_OPERATOR')
        self.assertEqual(outbound_loop.tick(self.registry, now=timezone.now())[d.pk], 'CANCELLED')
        d.refresh_from_db()
        self.assertEqual((d.state, d.reason), ('CANCELLED', 'CANCELLED_BY_OPERATOR'))
        from harbor_web.models import Audit
        self.assertTrue(Audit.objects.filter(
            action='outbound.dispatch_cancelled', object_id=str(d.pk)).exists())

        # Released immediately: the stand accepts a new dispatch without going through EXITED.
        cmd2 = self._completed_command(self.cargo2)
        d2 = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd2.pk, registry=self.registry)
        self.assertEqual(d2.state, 'PICKUP_PLANNED')
        self.assertNotIn('pinky-1', outbound_loop.reserved_vehicles())

    def test_work_detail_shows_cancel_action_until_closed(self):
        cmd = self._completed_command(self.cargo1)
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
        view = outbound_loop.dispatch_view(cmd)
        self.assertTrue(view['can_cancel'])
        OutboundDispatch.objects.filter(pk=d.pk).update(state='CANCELLED', version=d.version + 1)
        self.assertFalse(outbound_loop.dispatch_view(cmd)['can_cancel'])

    def test_hold_surfacing_resume_and_uncertain_recovery(self):
        cmd = self._completed_command(self.cargo1)
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
        k1 = outbound_loop.pickup_trip_key(d)

        # Simulate crash leaving SUBMITTING before first POST commits on ROSY; reconcile_proposal recovers safely.
        with mock.patch.object(self.client, 'trip_propose', side_effect=OSError('timeout')):
            outbound_loop.tick(self.registry, now=timezone.now())
        self.assertEqual(self.registry.transport.read(k1)['state'], 'UNCERTAIN')
        self.fleet.proposals[k1] = dict(
            proposal_id='p-recovered', proposal_principal_id=PRINCIPAL, request_key=k1, status='PROPOSED',
            request_digest=outbound_loop._leg1_intent(d).digest, candidate_robot_ids=['pinky-1', 'pinky-2'],
            steps=[{'to': 'S_OUT', 'via': []}], object_ref=None,
            dispatch_generation=None, approved_by=None, mission_id=None,
        )
        outbound_loop.tick(self.registry, now=timezone.now())
        d.refresh_from_db()
        self.assertEqual(d.state, 'PICKUP_PROPOSED')
        self.assertEqual(len(self.fleet.bodies), 0)  # reconciled without duplicate POST

        self.fleet.admit_and_set(k1, robot_id='pinky-1', status='HOLD', reason='OBSTACLE_WAIT')
        outbound_loop.tick(self.registry, now=timezone.now())
        d.refresh_from_db()
        self.assertEqual((d.state, d.reason), ('HOLD', 'OBSTACLE_WAIT'))

        # Resumed & arrived cleanly clears HOLD.
        self.fleet.admit_and_set(k1, robot_id='pinky-1', status='GOAL_CONFIRMED')
        outbound_loop.tick(self.registry, now=timezone.now())
        d.refresh_from_db()
        self.assertEqual((d.state, d.reason), ('PICKUP_ARRIVED', ''))

    def test_work_detail_roles_and_actions(self):
        cmd = self._completed_command(self.cargo1)
        from django.test import Client
        op_web, field_web = Client(), Client()
        op_web.force_login(self.operator)
        field_web.force_login(self.field)

        # Field user cannot view completed command before PICKUP_ARRIVED.
        self.assertEqual(field_web.get(f'/work/{cmd.pk}/').status_code, 403)
        self.assertEqual(op_web.get(f'/work/{cmd.pk}/').status_code, 200)

        with mock.patch.object(commands, 'acquire_registry', return_value=self.registry):
            resp = op_web.post(f'/work/{cmd.pk}/', {'action': 'outbound_dispatch', 'expected_version': cmd.version})
            self.assertEqual(resp.status_code, 302)
        d = OutboundDispatch.objects.get(command=cmd)
        OutboundDispatch.objects.filter(pk=d.pk).update(
            state='PICKUP_ARRIVED', assigned_vehicle_id='pinky-1', version=d.version + 1,
        )
        d.refresh_from_db()

        self.assertEqual(field_web.get(f'/work/{cmd.pk}/').status_code, 200)
        with mock.patch.object(commands, 'acquire_registry', return_value=self.registry):
            ok = field_web.post(f'/work/{cmd.pk}/', {
                'action': 'outbound_loaded', 'dispatch_id': d.pk, 'expected_version': d.version,
            })
        self.assertEqual(ok.status_code, 302)
        d.refresh_from_db()
        self.assertEqual(d.state, 'LOADED')

    def test_work_detail_outbound_cancel_action(self):
        cmd = self._completed_command(self.cargo1)
        d = outbound_loop.prepare_outbound_dispatch(self.operator, command_id=cmd.pk, registry=self.registry)
        from django.test import Client
        op_web, viewer_web = Client(), Client()
        op_web.force_login(self.operator)
        viewer_web.force_login(self.viewer)
        with mock.patch.object(commands, 'acquire_registry', return_value=self.registry):
            denied = viewer_web.post(f'/work/{cmd.pk}/', {'action': 'outbound_cancel', 'dispatch_id': d.pk})
            self.assertEqual(denied.status_code, 403)
            page = op_web.get(f'/work/{cmd.pk}/')
            self.assertEqual(page.status_code, 200)
            self.assertIn('반출 취소', page.content.decode('utf-8'))
            ok = op_web.post(f'/work/{cmd.pk}/', {'action': 'outbound_cancel', 'dispatch_id': d.pk})
            self.assertEqual(ok.status_code, 302)
        d.refresh_from_db()
        self.assertEqual((d.state, d.reason), ('CANCELLED', '운영자 반출 취소'))
        with mock.patch.object(commands, 'acquire_registry', return_value=self.registry):
            again = op_web.post(f'/work/{cmd.pk}/', {'action': 'outbound_cancel', 'dispatch_id': d.pk})
        self.assertEqual(again.status_code, 409)

"""Wave 2 & Wave 3 integration tests across order closure, status strip, vehicles, UI, and hardening."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from harbor.rosy_http import FleetIdentityError, RosyClient
from harbor.port_events import IntakeLedger
from harbor.transport import TransportLedger
from harbor_web import cell_plan_dispatch, executor, intake_loop, outbound_loop, strip
from harbor_web.board import board_context
from harbor_web.models import Audit, Cargo, CellPlanDispatch, Condition, DepartureIntent, FieldEpisode, Observation, Order, OutboundDispatch, Plan
from harbor_web.operations import operations_context
from harbor_web.order_queue import completed_order_ids, queue_digest
from harbor_web.pipeline import pipeline_for
from harbor_web.tests.test_outbound_loop import _canonical_places
from harbor_web.work_hub import hub_context
from harbor_web.work_state import day_progress, work_state


def _role_user(username, *roles):
    u = User.objects.create_user(username)
    for role in roles:
        u.groups.add(Group.objects.get_or_create(name=role)[0])
    return u


class Wave2AndWave3IntegrationTests(TestCase):
    def setUp(self):
        strip.clear_strip_fleet_cache()
        executor._failures.clear()
        self.addCleanup(strip.clear_strip_fleet_cache)
        self.addCleanup(executor._failures.clear)
        self.operator = _role_user('w2-op', 'operator', 'viewer')
        self.viewer = _role_user('w2-view', 'viewer')
        self.field = _role_user('w2-field', 'field')
        self.cargo = Cargo.objects.create(created_by=self.operator, code='CW2-1', name='Wave2 Cargo', weight_kg=10, volume_m3=1, destination='Busan')
        self.condition = Condition.objects.create(
            name='wave2-cond', created_by=self.operator, calibration_ref='installer-ref',
            payload={
                'outbound_stand': 'stand',
                'temporary_bays': ['temp'],
                'allowed_moves': [['bay', 'stand'], ['bay', 'temp'], ['temp', 'bay']],
                'max_observation_age_seconds': 3600,
                'conveyor': 'belt',
                'arm_reach': [['A', 'bay'], ['A', 'temp'], ['A', 'stand']],
            },
        )
        self.obs = Observation.objects.create(
            source_id='cam-1', source_revision='rev-w2', observed_at=timezone.now(),
            created_by=self.operator, condition=self.condition,
            payload={
                'bays': [
                    {'bay_id': 'bay', 'cargo': ['CW2-1'], 'capacity': 2},
                    {'bay_id': 'temp', 'cargo': [], 'capacity': 2},
                    {'bay_id': 'stand', 'cargo': [], 'capacity': 1},
                ],
                'holdings': [],
            },
        )
        self.order = Order.objects.create(
            created_by=self.operator, cargo=self.cargo, kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        self.plan = Plan.objects.create(
            created_by=self.operator,
            order=self.order, order_version=1, observation=self.obs, condition=self.condition,
            queue_digest=queue_digest(),
            result={
                'status': 'READY', 'model': 'grid-placement-v1', 'reason': 'OK',
                'moves': [{
                    'cargo_id': 'CW2-1', 'source': 'bay', 'destination': 'stand',
                    'primary_arm': 'A', 'alternate_arms': [], 'phase_label': '반출',
                }],
            },
        )

    def test_p1_1_cell_plan_completion_closes_order_and_updates_views(self):
        before_digest = queue_digest()
        cp = CellPlanDispatch.objects.create(
            order=self.order, plan=self.plan, observation=self.obs,
            dispatch_mode='HARBOR_PLAN', directive_mode='harbor_plan',
            request_key='rk-p1-1', request_payload={}, rosy_plan_id='cp-1',
            order_source='harbor', state='RUNNING', created_by=self.operator,
            planned_steps=[{'cargo_id': 'CW2-1', 'dst_site': 'stand:r1-s1-t1', 'dst_wire_ref': 'stand-r1-c1-l1'}],
            rosy_task_ids=['cp-1-s1'],
        )
        fake = SimpleNamespace(cell_plan=lambda pid: {
            'ok': True, 'schema': 'rosy.cell-plan.v1',
            'plan': {
                'plan_id': pid, 'schema': 'rosy.cell-plan.v1', 'mode': 'harbor_plan',
                'order_source': 'harbor', 'cell_id': 'cell_ab', 'status': 'COMPLETED',
                'total_steps': 1, 'completed_steps': 1, 'task_ids': ['cp-1-s1'],
                'moves': [], 'created_at': 1.0,
            },
            'tasks': [{'task_id': 'cp-1-s1', 'status': 'COMPLETED', 'dst_wire_ref': 'stand-r1-c1-l1'}],
        })
        cell_plan_dispatch.sync_cell_plan_dispatch(cp.pk, client=fake)
        cp.refresh_from_db()
        self.assertIn(self.order.pk, completed_order_ids().values_list('pk', flat=True))
        self.assertNotEqual(queue_digest(), before_digest)
        self.assertTrue(Audit.objects.filter(action='cell_plan.completed', object_id=str(cp.pk)).exists())
        self.assertEqual(day_progress(), {'completed': 1, 'total': 1})
        self.assertEqual(work_state(cell_plan_dispatch=cp), '완료')
        ops = operations_context(self.obs)
        self.assertEqual(ops['actual']['completed'], 1)
        self.assertEqual(ops['workload']['plans'], [])
        self.assertEqual(len(hub_context()['timeline']), 1)
        self.client.force_login(self.operator)
        req = self.client.request().wsgi_request
        req.user = self.operator
        self.assertEqual(board_context(req, observation=self.obs, operations=ops)['day_progress']['completed'], 1)
        self.assertTrue(pipeline_for(self.order, self.plan, cell_plan_dispatch=CellPlanDispatch.objects.get(pk=cp.pk))['stages'][-1]['status'] == 'done')

    def test_p1_2_strip_live_fleet_state_and_available_vehicles_filtering(self):
        env = {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_ALLOWED_ORIGINS': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'tok-1',
        }
        fleet_payload = {
            'robots': [
                {'robot_id': 'r-ok', 'online': True, 'state': {'safety': {'estop': False}, 'battery_status': {'level': 'ok', 'charging': False}}},
                {'robot_id': 'r-off', 'online': False, 'state': None},
                {'robot_id': 'r-estop', 'online': True, 'state': {'safety': {'estop': True}}},
                {'robot_id': 'r-low', 'online': True, 'power_health': {'battery': {'evidence': 'fresh', 'level': 'critical', 'charging_state': 'unconfirmed'}}},
                {'robot_id': 'r-chg', 'online': True, 'power_health': {'battery': {'evidence': 'fresh', 'level': 'ok', 'charging_state': 'confirmed'}}},
                {'robot_id': 'r-inb', 'online': True, 'state': {'safety': {'estop': False}}},
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            ledger_file = Path(folder) / 'ledger.sqlite3'
            IntakeLedger(ledger_file).expect('INB-1', now=timezone.now())
            ep_in = FieldEpisode.objects.create(
                order=Order.objects.create(created_by=self.operator, cargo=Cargo.objects.create(created_by=self.operator, code='INB-1', name='In', weight_kg=1, volume_m3=1, destination='A'), kind='INBOUND', priority_group=1, sequence=2),
                vehicle_id='r-inb', assigned_to=self.field, created_by=self.operator, state='CONFIRMED',
            )
            DepartureIntent.objects.create(episode=ep_in, release_event_id='rel-1', vehicle_id='r-inb', cargo=ep_in.order.cargo, created_by=self.operator)
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger_file)}):
                free = outbound_loop.available_vehicles(['r-ok', 'r-off', 'r-estop', 'r-low', 'r-chg', 'r-inb'], fleet_state=fleet_payload)
        self.assertEqual(free, ('r-ok',))

        self.client.force_login(self.operator)
        with patch.dict(os.environ, env):
            strip.record_fleet_state(fleet_payload)
            resp = self.client.get('/workflow/')
        self.assertContains(resp, '비상 정지 활성 (1대)')
        self.assertContains(resp, 'ROSY Fleet 비상 정지 작동 중')

    def test_p1_3_and_p2_5_cancel_running_cell_plan_and_env_defaults(self):
        cp = CellPlanDispatch.objects.create(
            order=self.order, plan=self.plan, observation=self.obs,
            dispatch_mode='HARBOR_PLAN', directive_mode='harbor_plan',
            request_key='rk-p1-3', request_payload={}, rosy_plan_id='cp-run',
            order_source='harbor', state='RUNNING', created_by=self.operator,
        )
        fake = SimpleNamespace(cancel_cell_plan=lambda pid, reason: {
            'ok': True, 'schema': 'rosy.cell-plan.v1',
            'plan': {
                'plan_id': pid, 'schema': 'rosy.cell-plan.v1', 'mode': 'harbor_plan',
                'order_source': 'harbor', 'cell_id': 'cell_ab', 'status': 'CANCELLED',
                'total_steps': 1, 'completed_steps': 0, 'hold_reason': reason,
                'task_ids': ['cp-run-s1'], 'moves': [], 'created_at': 1.0,
            },
            'tasks': [{'task_id': 'cp-run-s1', 'status': 'CANCELLED', 'dst_wire_ref': 'stand-r1-c1-l1'}], 'cancelled_task_ids': ['cp-run-s1'],
        })
        cancelled = cell_plan_dispatch.cancel_cell_plan_dispatch(self.operator, dispatch_id=cp.pk, reason='abort running', client=fake)
        self.assertEqual(cancelled.state, 'CANCELLED')
        with patch.dict(os.environ, {'HARBOR_CELL_PLAN_ROBOT_ID': 'rosy_07', 'HARBOR_OUTBOUND_STAND': 'stand'}):
            self.assertEqual(cell_plan_dispatch._default_robot_id(), 'rosy_07')
            self.assertEqual(cell_plan_dispatch._default_outbound_stand(), 'stand')

    def test_p1_4_and_p1_5_preview_goal_and_field_load_access(self):
        self.client.force_login(self.operator)
        dry_payload = {
            'ok': True, 'schema': 'rosy.cell-plan.v1', 'mode': 'goal',
            'order_source': 'rosy_r',
            'moves': [{'src_site': 'yard:r1-s1-t1', 'dst_site': 'yard:r1-s2-t1', 'arm_id': 'arm_a', 'tier': 1, 'phase_label': '장치'}],
            'final_state': {'CW2-1': 'yard:r1-s2-t1'},
        }
        with patch('harbor_web.cell_plan_dispatch.preview_harbor_plan', return_value=SimpleNamespace(mode='harbor_plan', order_source='harbor', moves=dry_payload['moves'], final_state=dry_payload['final_state'])):
            prev = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_preview_harbor'})
        self.assertEqual(prev.status_code, 200)
        self.assertContains(prev, '계획 미리보기')

        bad_goal = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_rosy', 'directive_mode': 'goal', 'goal_json': 'not-json'})
        self.assertEqual(bad_goal.status_code, 409)

        cp = CellPlanDispatch.objects.create(
            order=self.order, plan=self.plan, observation=self.obs,
            dispatch_mode='HARBOR_PLAN', directive_mode='harbor_plan',
            request_key='rk-p1-5', request_payload={}, rosy_plan_id='cp-5',
            order_source='harbor', state='COMPLETED', created_by=self.operator,
        )
        od = OutboundDispatch.objects.create(
            cell_plan_dispatch=cp, cargo=self.cargo, actor=self.operator,
            stand_zone_id='stand', pickup_stop_id='S_OUT', exit_stop_id='S_EXIT',
            site_places_digest='d' * 64, candidate_robot_ids=['rosy_01'],
            state='PICKUP_ARRIVED', assigned_vehicle_id='rosy_01',
        )
        self.client.force_login(self.field)
        field_list_resp = self.client.get('/field/')
        self.assertContains(field_list_resp, '차량 적재 확인 열기')
        self.assertEqual(self.client.get(f'/plans/{self.plan.pk}/').status_code, 200)
        self.assertEqual(self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_harbor'}).status_code, 403)
        with tempfile.TemporaryDirectory() as folder, \
             patch('harbor_web.commands.acquire_registry', return_value=SimpleNamespace(transport=TransportLedger(Path(folder) / 'l.sqlite3'))):
            confirm = self.client.post(f'/plans/{self.plan.pk}/', {
                'action': 'cell_plan_outbound_loaded',
                'outbound_dispatch_id': str(od.pk),
                'expected_version': str(od.version),
            })
        self.assertEqual(confirm.status_code, 302)
        od.refresh_from_db()
        self.assertEqual(od.state, 'LOADED')

    def test_p1_6_intake_loop_falls_back_to_site_places_destination(self):
        places_doc = dict(_canonical_places())
        places_doc['vehicle_places'] = list(places_doc['vehicle_places']) + [
            {'place_id': 'S_IN', 'port_role': 'intake_dropoff', 'status': 'verified', 'dispatchable': True, 'label': 'S_IN'},
        ]
        places_doc['intake_handoffs'] = [{
            'handoff_id': 's3_cell_s_unload',
            'vehicle_stop_id': 'S_IN',
            'target_cell_zones': ['yard'],
            'status': 'verified',
            'dispatchable': True,
            'intake_point_ref': '',
        }]
        body_for_digest = {k: v for k, v in places_doc.items() if k != 'digest'}
        encoded = json.dumps(body_for_digest, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        places_doc['digest'] = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / 'intake.sqlite3'
            ep = FieldEpisode.objects.create(
                order=Order.objects.create(created_by=self.operator, cargo=self.cargo, kind='INBOUND', priority_group=1, sequence=3),
                vehicle_id='rosy_01', assigned_to=self.field, created_by=self.operator, state='CONFIRMED',
            )
            DepartureIntent.objects.create(episode=ep, release_event_id='rel-2', vehicle_id='rosy_01', cargo=self.cargo, created_by=self.operator)
            fake_client = SimpleNamespace(
                site_places=lambda: places_doc,
                port_events=lambda **kw: {'events': [], 'cursor': 'c1', 'complete': True},
            )
            reg = SimpleNamespace(config={'path': str(ledger), 'origin': 'https://fleet.example'}, client=fake_client)
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(ledger), 'HARBOR_TRIP_DESTINATION': 'auto'}), \
                 patch('harbor.transport.TransportLedger.propose', return_value=None) as prop:
                intake_loop.tick(reg, now=timezone.now())
            self.assertEqual(prop.call_count, 1)

    def test_p1_9_and_p2_2_x_request_id_and_executor_backoff(self):
        seen_headers = []

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, n=-1):
                return b'{"principal_id":"svc","role":"service"}'

        class FakeOpener:
            def open(self, req, timeout=10):
                seen_headers.append(dict(req.header_items()))
                return FakeResp()

        c = RosyClient('https://fleet.example', 'secret-token')
        c._opener = FakeOpener()
        c._discover = False
        c.session()
        self.assertEqual(len(seen_headers), 1)
        self.assertTrue(any(k.lower() == 'x-request-id' and v for k, v in seen_headers[0].items()))

        c_id = RosyClient(transport=lambda m, p, b: {'principal_id': 'wrong', 'role': 'service'})
        c_id.expected_principal, c_id.expected_role = 'expected-svc', 'service'
        with self.assertRaises(FleetIdentityError):
            c_id.cell_sites()

        executor._failures.clear()
        calls = {'intake': 0}
        reg = SimpleNamespace(config={'path': 'x', 'origin': 'https://fleet.example'}, client=SimpleNamespace())
        with patch('harbor_web.runtime.execution_block_reason', return_value=''), \
             patch('harbor_web.commands.acquire_registry', return_value=reg), \
             patch('harbor_web.rearrangement.auto_check'), \
             patch('harbor_web.intake_loop.tick', side_effect=lambda *a, **kw: calls.__setitem__('intake', calls['intake'] + 1) or (_ for _ in ()).throw(OSError('down'))), \
             patch('harbor_web.cell_plan_dispatch.tick', return_value={}), \
             patch('harbor_web.outbound_loop.tick', return_value={}):
            t0 = timezone.now()
            executor.tick(registry=reg, now=t0)
            executor.tick(registry=reg, now=t0 + timezone.timedelta(seconds=1))
        self.assertEqual(calls['intake'], 1)

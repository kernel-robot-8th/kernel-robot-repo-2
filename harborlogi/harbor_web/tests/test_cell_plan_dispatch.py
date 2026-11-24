"""Django tests for dual-mode OMX cell-plan dispatch (Harbor plan order vs autonomous ROSY directive)."""
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.contrib.auth.models import Group, User
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from harbor.transport import TransportLedger
from harbor_web import cell_plan_dispatch, executor, outbound_loop, services
from harbor_web.models import Audit, CargoFootprint, CellPlanDispatch, OutboundDispatch
from harbor_web.runtime_models import DeploymentState

ROOT = Path(__file__).resolve().parents[2]


def _http_error(status: int, code: str | None = None) -> HTTPError:
    body = json.dumps({'detail': {'code': code}}).encode('utf-8') if code else b'Not Found'
    return HTTPError('https://fleet.invalid/api/fleet/pipeline/cell-plans', status, 'err', {}, BytesIO(body))


def _canonical_places_doc() -> dict:
    body = {
        'schema': 'rosy.port-site-places.v1',
        'sources': {
            'site_map': {'map_id': 'w', 'version': 1, 'sha256': 'a' * 64},
            'cell_site_map': {'schema': 'rosy.cell-site-map.v1', 'revision': 1, 'status': 'verified'},
        },
        'vehicle_places': [
            {'place_id': 'S_OUT', 'port_role': 'outbound_pickup', 'status': 'verified', 'dispatchable': True, 'label': 'S_OUT'},
            {'place_id': 'S_EXIT', 'port_role': 'outbound_exit', 'status': 'verified', 'dispatchable': True, 'label': 'S_EXIT'},
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
            'vehicle_exit_stop_id': 'S_EXIT',
            'status': 'verified',
            'dispatchable': True,
        }],
        'not_places': ['belt'],
    }
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return {**body, 'digest': sha256(encoded.encode('utf-8')).hexdigest()}


class _FakeCellPlanClient:
    def __init__(self):
        self.calls = []
        self.by_key = {}
        self.by_id = {}
        self.status = 'PROPOSED'
        self.post_error = None
        self.get_key_error = None
        self.sites_doc = _canonical_places_doc()

    def propose_cell_plan(self, payload, *, request_key):
        self.calls.append((request_key, payload))
        if payload['mode'] == 'harbor_plan':
            moves = [
                {
                    'src_wire_ref': s['src_site'],
                    'dst_wire_ref': s['dst_site'],
                    'arm_id': s.get('arm_id', 'arm_a'),
                    'phase_label': s.get('phase_label', '이동'),
                }
                for s in payload['steps']
            ]
            source = 'harbor'
        else:
            moves = [
                {'src_wire_ref': 'yard-r1-c1-l2', 'dst_wire_ref': 'temp_a-r1-c1-l1', 'arm_id': 'arm_a', 'phase_label': '재취급'},
                {'src_wire_ref': 'yard-r1-c1-l1', 'dst_wire_ref': 'stand-r1-c1-l1', 'arm_id': 'arm_a', 'phase_label': '반출'},
                {'src_wire_ref': 'temp_a-r1-c1-l1', 'dst_wire_ref': 'yard-r1-c1-l1', 'arm_id': 'arm_a', 'phase_label': '복귀'},
            ]
            source = 'rosy_r'
        plan_id = f'cell-plan-{len(self.calls)}'
        tasks = [
            {
                'task_id': f'{plan_id}-s{i + 1}',
                'depends_on': None if i == 0 else f'{plan_id}-s{i}',
                'status': self.status,
                'dst_wire_ref': m['dst_wire_ref'],
                'arm_id': m['arm_id'],
            }
            for i, m in enumerate(moves)
        ]
        response = {
            'ok': True,
            'schema': 'rosy.cell-plan.v1',
            'plan': {
                'plan_id': plan_id,
                'schema': 'rosy.cell-plan.v1',
                'mode': payload['mode'],
                'order_source': source,
                'status': self.status,
                'total_steps': len(tasks),
                'completed_steps': 0,
                'request_key': request_key,
                'task_ids': [t['task_id'] for t in tasks],
                'moves': moves,
            },
            'tasks': tasks,
        }
        self._last = response
        self.by_key[request_key] = response
        self.by_id[plan_id] = response
        if self.post_error is not None:
            raise self.post_error
        return response

    def cell_plan(self, plan_id):
        rec = self.by_id[plan_id]
        plan_extra = {}
        if getattr(self, 'hold_reason', None):
            plan_extra['hold_reason'] = self.hold_reason
        return {
            **rec,
            'plan': {**rec['plan'], 'status': self.status, **plan_extra},
            'tasks': [{**t, 'status': self.status} for t in rec['tasks']],
        }

    def preview_cell_plan(self, payload):
        return {
            'ok': True,
            'schema': 'rosy.cell-plan.v1',
            'mode': payload['mode'],
            'order_source': 'harbor' if payload['mode'] == 'harbor_plan' else 'rosy_r',
            'moves': payload.get('steps') or [{'src_site': 'yard-r1-c1-l1', 'dst_site': 'stand-r1-c1-l1', 'arm_id': 'arm_a', 'phase_label': '반출'}],
            'final_state': {'C1': 'stand-r1-c1-l1'},
        }

    def cancel_cell_plan(self, plan_id, *, reason):
        rec = self.by_id[plan_id]
        self.status = 'CANCELLED'
        tasks = [{**t, 'status': 'CANCELLED', 'cancel_reason': reason} for t in rec['tasks']]
        return {
            'ok': True,
            'schema': 'rosy.cell-plan.v1',
            'plan': {**rec['plan'], 'status': 'CANCELLED', 'hold_reason': reason},
            'tasks': tasks,
            'cancelled_task_ids': [t['task_id'] for t in tasks],
        }

    def cell_plan_request(self, request_key):
        if self.get_key_error is not None:
            raise self.get_key_error
        if request_key not in self.by_key:
            raise _http_error(404, 'UNKNOWN_CELL_PLAN')
        rec = self.by_key[request_key]
        return {
            **rec,
            'plan': {**rec['plan'], 'status': self.status},
            'tasks': [{**t, 'status': self.status} for t in rec['tasks']],
        }

    def site_places(self):
        return self.sites_doc


class _FakeOutboundTransport:
    def __init__(self, ledger_path: Path):
        self.ledger = TransportLedger(ledger_path)
        self.trip_status = 'QUEUED'
        self.assigned_robot = 'pinky-01'

    def prepare(self, intent):
        self.ledger.prepare(intent)

    def read(self, request_key):
        return self.ledger.read(request_key)

    def propose(self, request_key, client):
        del client
        return {'mission_id': f'trip:{request_key}', 'robot_id': self.assigned_robot, 'status': self.trip_status}

    def reconcile_proposal(self, request_key, client):
        return self.propose(request_key, client)


class CellPlanDispatchTests(TestCase):
    def setUp(self):
        # Dispatch checks the deployment runtime gate; tests run in a reconciled, non-restored state.
        DeploymentState.objects.get_or_create(pk=1, defaults={'mode': settings.MODE})
        self.operator = User.objects.create_user('cell-op')
        for role in ('admin', 'operator', 'field', 'viewer'):
            self.operator.groups.add(Group.objects.get_or_create(name=role)[0])
        self.viewer = User.objects.create_user('cell-viewer')
        self.viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])

        policy = json.loads((ROOT / 'config/spatial-policy-3x6.json').read_text(encoding='utf-8'))
        policy['yard_layout'] = json.loads((ROOT / 'config/yard-layout-3x6.json').read_text(encoding='utf-8'))
        self.condition = services.register_condition(self.operator, name='3x6', calibration_ref='synthetic', payload=policy)
        for code in ('C1', 'C2'):
            services.register_cargo(self.operator, code=code, name=code, weight_kg=2, volume_m3='0.012', destination='stand', length_cells=3)
        c1, c2 = [CargoFootprint.objects.filter(cargo__code=c).latest('pk') for c in ('C1', 'C2')]
        self.observation = services.record_observation(
            self.operator,
            source_id='cam-top',
            source_revision='rev-1',
            observed_at=timezone.now(),
            provenance='SYNTHETIC',
            condition=self.condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                holdings=[],
                placements=[
                    dict(cargo_id='C1', area_id='yard', row=1, column=1, level=1, orientation=0, footprint_revision=c1.pk),
                    dict(cargo_id='C2', area_id='yard', row=1, column=1, level=2, orientation=0, footprint_revision=c2.pk),
                ],
            ),
        )
        self.order = services.create_order(self.operator, cargo=c1.cargo, kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED')
        self.plan = services.make_plan(self.operator, order_id=self.order.pk, expected_version=1, observation_id=self.observation.pk)
        self.assertEqual(self.plan.result['status'], 'READY')

    def test_default_client_requires_service_role_rule_4(self):
        env = {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'tok-svc',
            'HARBOR_FLEET_PRINCIPAL': 'cell-app-service',
            'HARBOR_FLEET_ALLOW_HOSTS': 'fleet.example',
        }
        with patch.dict(os.environ, env, clear=False), patch('harbor_web.cell_plan_dispatch.connection_settings', return_value={'origin': 'https://fleet.example', 'allowed': True, 'principal': 'cell-app-service'}):
            client = cell_plan_dispatch._default_client()
        self.assertEqual(client.expected_principal, 'cell-app-service')
        self.assertEqual(client.expected_role, 'service')

    def test_dual_mode_dispatch_and_sync_lifecycle(self):
        client = _FakeCellPlanClient()
        harbor_row = cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        self.assertEqual(harbor_row.dispatch_mode, 'HARBOR_PLAN')
        self.assertEqual(harbor_row.order_source, 'harbor')
        self.assertEqual(harbor_row.state, 'PROPOSED')
        self.assertEqual(harbor_row.request_payload['mode'], 'harbor_plan')
        self.assertEqual(len(harbor_row.rosy_task_ids), len(self.plan.result['moves']))
        self.assertEqual(client.calls[0][1]['mode'], 'harbor_plan')

        # P0-2 mutex: the still-active harbor dispatch blocks any second dispatch for the same order.
        with self.assertRaises(services.Conflict) as blocked:
            cell_plan_dispatch.dispatch_rosy_directive(
                self.operator, order_id=self.order.pk, observation_id=self.observation.pk,
                directive_mode='outbound', plan_id=self.plan.pk, client=client,
            )
        self.assertEqual(str(blocked.exception), 'ACTIVE_CELL_PLAN_DISPATCH')
        self.assertEqual(CellPlanDispatch.objects.filter(order=self.order).count(), 1)

        client.status = 'COMPLETED'
        synced = cell_plan_dispatch.sync_cell_plan_dispatch(harbor_row.pk, client=client)
        self.assertEqual(synced.state, 'COMPLETED')

        rosy_row = cell_plan_dispatch.dispatch_rosy_directive(
            self.operator, order_id=self.order.pk, observation_id=self.observation.pk,
            directive_mode='outbound', plan_id=self.plan.pk, client=client,
        )
        self.assertEqual(rosy_row.dispatch_mode, 'ROSY_DIRECTIVE')
        self.assertEqual(rosy_row.order_source, 'rosy_r')
        self.assertEqual(len(rosy_row.rosy_task_ids), 3)
        self.assertNotIn('steps', client.calls[1][1])
        self.assertEqual(client.calls[1][1]['cargo_id'], 'C1')

        with self.assertRaises(PermissionDenied):
            cell_plan_dispatch.dispatch_harbor_plan(self.viewer, plan_id=self.plan.pk, client=client)

    def test_active_command_blocks_cell_plan_dispatch(self):
        from harbor_web.models import Command, Plan
        client = _FakeCellPlanClient()
        command_plan = Plan.objects.filter(order=self.order).first()
        Command.objects.create(
            order=self.order, plan=command_plan, actor=self.operator, plan_version=1,
            ledger_identity='l', fleet_origin='f', fleet_principal='p', body_digest='a' * 64,
            binding={}, state='UNKNOWN',
        )
        with self.assertRaises(services.Conflict) as blocked:
            cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        self.assertEqual(str(blocked.exception), 'EXISTING_ORDER_EXECUTION_REQUIRES_RECONCILIATION')
        self.assertEqual(CellPlanDispatch.objects.count(), 0)

    def test_write_before_post_and_get_request_key_reconciliation_rules_6_to_9(self):
        client = _FakeCellPlanClient()
        client.post_error = URLError('network timeout after commit')
        row = cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        self.assertEqual(row.state, 'UNCERTAIN')
        self.assertEqual(row.rosy_plan_id, '')
        self.assertEqual(row.request_payload['mode'], 'harbor_plan')
        self.assertEqual(len(client.calls), 1)

        # P0-2 mutex: an uncertain request still occupies the order.
        with self.assertRaises(services.Conflict):
            cell_plan_dispatch.dispatch_rosy_directive(
                self.operator, order_id=self.order.pk, observation_id=self.observation.pk, client=client,
            )

        # Rule 7: reconciliation queries GET ?request_key= and NEVER re-POSTs.
        client.post_error = None
        recovered = cell_plan_dispatch.reconcile_cell_plan_dispatch(row.pk, client=client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(recovered.state, 'PROPOSED')
        self.assertEqual(recovered.rosy_plan_id, 'cell-plan-1')

        # Complete/cancel the recovered dispatch so the next independent Rule 8 / Rule 9 requests can start.
        cell_plan_dispatch.cancel_cell_plan_dispatch(
            self.operator, dispatch_id=recovered.pk, reason='done', client=client,
        )

        # Rule 8: uncoded 404 keeps UNCERTAIN (capability absent), and coded UNKNOWN_CELL_PLAN closes to NOT_FOUND.
        client.post_error = _http_error(503, 'PIPELINE_LEDGER_UNAVAILABLE')
        uncertain2 = cell_plan_dispatch.dispatch_rosy_directive(
            self.operator, order_id=self.order.pk, observation_id=self.observation.pk, client=client,
        )
        client.by_key.pop(uncertain2.request_key, None)
        client.get_key_error = _http_error(404, code=None)
        still_uncertain = cell_plan_dispatch.reconcile_cell_plan_dispatch(uncertain2.pk, client=client)
        self.assertEqual(still_uncertain.state, 'UNCERTAIN')
        self.assertEqual(still_uncertain.reason, 'CELL_PLAN_ROUTE_UNAVAILABLE')

        client.get_key_error = None
        closed = cell_plan_dispatch.reconcile_cell_plan_dispatch(uncertain2.pk, client=client)
        self.assertEqual(closed.state, 'NOT_FOUND')
        self.assertEqual(closed.reason, 'UNKNOWN_CELL_PLAN')

        # Rule 9: 401/403 marks REJECTED with FLEET_IDENTITY_REJECTED and raises ValidationError.
        client.post_error = _http_error(401, 'UNAUTHORIZED')
        with self.assertRaises(ValidationError):
            cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        latest = CellPlanDispatch.objects.order_by('-pk').first()
        self.assertEqual(latest.state, 'REJECTED')
        self.assertEqual(latest.reason, 'FLEET_IDENTITY_REJECTED')

    def test_executor_tick_reconciles_cell_plan_and_drives_two_leg_outbound_pinky(self):
        import tempfile
        client = _FakeCellPlanClient()
        client.post_error = URLError('lost response')
        row = cell_plan_dispatch.dispatch_rosy_directive(
            self.operator, order_id=self.order.pk, observation_id=self.observation.pk,
            directive_mode='outbound', plan_id=self.plan.pk, client=client,
        )
        self.assertEqual(row.state, 'UNCERTAIN')

        with tempfile.TemporaryDirectory() as tmpdir:
            transport = _FakeOutboundTransport(Path(tmpdir) / 'outbound.sqlite3')

            class _Reg:
                def __init__(self, c, t):
                    self.client = c
                    self.transport = t
                    self.config = {'path': str(Path(tmpdir) / 'outbound.sqlite3')}

            reg = _Reg(client, transport)
            client.post_error = None
            client.status = 'COMPLETED'
            env = {'HARBOR_OUTBOUND_AUTO_DISPATCH': '1', 'HARBOR_OUTBOUND_VEHICLES': 'pinky-01,pinky-02'}
            with patch.dict(os.environ, env, clear=False), patch('harbor_web.runtime.execution_block_reason', return_value=''):
                # Tick 1: reconciles UNCERTAIN -> COMPLETED, auto-prepares OutboundDispatch (PICKUP_PLANNED),
                # then outbound_loop.tick proposes Leg 1 S_OUT -> PICKUP_PROPOSED.
                executor.tick(registry=reg)
                row.refresh_from_db()
                self.assertEqual(row.state, 'COMPLETED')
                od = OutboundDispatch.objects.get(cell_plan_dispatch=row)
                self.assertEqual(od.state, 'PICKUP_PROPOSED')
                self.assertEqual(od.assigned_vehicle_id, 'pinky-01')

                # Tick 2: Leg 1 S_OUT arrives -> PICKUP_ARRIVED (stops cleanly awaiting load confirm).
                transport.trip_status = 'GOAL_CONFIRMED'
                executor.tick(registry=reg)
                od.refresh_from_db()
                self.assertEqual(od.state, 'PICKUP_ARRIVED')

                # Named confirmation -> LOADED -> Leg 2 pinned vehicle S_EXIT -> EXITED.
                outbound_loop.confirm_outbound_loaded(self.operator, dispatch_id=od.pk, expected_version=od.version, registry=reg)
                transport.trip_status = 'QUEUED'
                executor.tick(registry=reg)
                od.refresh_from_db()
                self.assertEqual(od.state, 'EXIT_PROPOSED')

                transport.trip_status = 'GOAL_CONFIRMED'
                executor.tick(registry=reg)
                od.refresh_from_db()
                self.assertEqual(od.state, 'EXITED')

    def test_plan_detail_posts_both_cell_plan_actions(self):
        client = _FakeCellPlanClient()
        self.client.force_login(self.operator)
        with self.settings(ROOT_URLCONF='harbor_web.urls'):
            with patch.object(cell_plan_dispatch, '_default_client', return_value=client), patch(
                    'harbor_web.readiness.first_unmet', return_value=''):
                r1 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_harbor'})
                self.assertEqual(r1.status_code, 302)
                # P0-2 mutex: the active first dispatch blocks a second dispatch for the same order.
                r2 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_rosy', 'directive_mode': 'outbound'})
                self.assertEqual(r2.status_code, 409)
                row = CellPlanDispatch.objects.get(order=self.order)
                client.status = 'COMPLETED'
                r3 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_sync', 'dispatch_id': row.pk})
                self.assertEqual(r3.status_code, 302)
                r4 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_rosy', 'directive_mode': 'outbound'})
                self.assertEqual(r4.status_code, 302)
        rows = list(CellPlanDispatch.objects.filter(order=self.order).order_by('pk'))
        self.assertEqual([r.order_source for r in rows], ['harbor', 'rosy_r'])
        self.assertEqual([r.state for r in rows], ['COMPLETED', 'COMPLETED'])

    def test_plan_detail_manual_sync_and_outbound_pinky_actions(self):
        import tempfile
        client = _FakeCellPlanClient()
        self.client.force_login(self.operator)
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / 'manual-outbound.sqlite3'
            transport = _FakeOutboundTransport(ledger_path)

            class _Reg:
                def __init__(self):
                    self.client = client
                    self.transport = transport
                    self.config = {'path': str(ledger_path)}

            with self.settings(ROOT_URLCONF='harbor_web.urls'), patch.dict(
                os.environ, {'HARBOR_OUTBOUND_VEHICLES': 'pinky-01,pinky-02'}, clear=False
            ), patch.object(cell_plan_dispatch, '_default_client', return_value=client), patch(
                'harbor_web.readiness.first_unmet', return_value=''
            ), patch(
                'harbor_web.commands.acquire_registry', return_value=_Reg()
            ):
                client.post_error = URLError('lost response')
                r1 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_harbor'})
                self.assertEqual(r1.status_code, 302)
                row = CellPlanDispatch.objects.get(order=self.order)
                self.assertEqual(row.state, 'UNCERTAIN')

                page1 = self.client.get(f'/plans/{self.plan.pk}/')
                self.assertEqual(page1.status_code, 200)
                html1 = page1.content.decode('utf-8')
                self.assertIn('응답 확인 중', html1)
                self.assertIn('진행 상태 확인', html1)
                self.assertNotIn('()', html1)

                client.post_error = None
                client.status = 'COMPLETED'
                r2 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_sync', 'dispatch_id': row.pk})
                self.assertEqual(r2.status_code, 302)
                row.refresh_from_db()
                self.assertEqual(row.state, 'COMPLETED')

                html2 = self.client.get(f'/plans/{self.plan.pk}/').content.decode('utf-8')
                self.assertIn('반출대 차량 호출', html2)

                r3 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_outbound_dispatch', 'dispatch_id': row.pk})
                self.assertEqual(r3.status_code, 302)
                od = OutboundDispatch.objects.get(cell_plan_dispatch=row)
                self.assertEqual(od.state, 'PICKUP_PLANNED')

                od.state = 'PICKUP_ARRIVED'
                od.assigned_vehicle_id = 'pinky-01'
                od.save(update_fields=['state', 'assigned_vehicle_id'])

                html3 = self.client.get(f'/plans/{self.plan.pk}/').content.decode('utf-8')
                self.assertIn('차량 적재 확인', html3)

                r4 = self.client.post(f'/plans/{self.plan.pk}/', {
                    'action': 'cell_plan_outbound_loaded',
                    'outbound_dispatch_id': od.pk,
                    'expected_version': od.version,
                })
                self.assertEqual(r4.status_code, 302)
                od.refresh_from_db()
                self.assertEqual(od.state, 'LOADED')

    def test_plan_detail_cancels_the_outbound_dispatch_and_frees_the_stand(self):
        import tempfile
        client = _FakeCellPlanClient()
        self.client.force_login(self.operator)
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / 'cancel-outbound.sqlite3'
            transport = _FakeOutboundTransport(ledger_path)

            class _Reg:
                def __init__(self):
                    self.client = client
                    self.transport = transport
                    self.config = {'path': str(ledger_path)}

            with self.settings(ROOT_URLCONF='harbor_web.urls'), patch.dict(
                os.environ, {'HARBOR_OUTBOUND_VEHICLES': 'pinky-01,pinky-02'}, clear=False
            ), patch.object(cell_plan_dispatch, '_default_client', return_value=client), patch(
                'harbor_web.readiness.first_unmet', return_value=''
            ), patch(
                'harbor_web.commands.acquire_registry', return_value=_Reg()
            ):
                r1 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_harbor'})
                self.assertEqual(r1.status_code, 302)
                row = CellPlanDispatch.objects.get(order=self.order)
                client.status = 'COMPLETED'
                r2 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_sync', 'dispatch_id': row.pk})
                self.assertEqual(r2.status_code, 302)
                r3 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_outbound_dispatch', 'dispatch_id': row.pk})
                self.assertEqual(r3.status_code, 302)
                od = OutboundDispatch.objects.get(cell_plan_dispatch=row)
                self.assertEqual(od.state, 'PICKUP_PLANNED')

                r4 = self.client.post(f'/plans/{self.plan.pk}/', {
                    'action': 'cell_plan_outbound_cancel', 'dispatch_id': row.pk,
                })
                self.assertEqual(r4.status_code, 302)
                od.refresh_from_db()
                self.assertEqual((od.state, od.reason), ('CANCELLED', '운영자 반출 취소'))
                self.assertTrue(Audit.objects.filter(
                    action='outbound.dispatch_cancelled', object_id=str(od.pk)).exists())

                r5 = self.client.post(f'/plans/{self.plan.pk}/', {
                    'action': 'cell_plan_outbound_cancel', 'dispatch_id': row.pk,
                })
                self.assertEqual(r5.status_code, 409)

    def test_preview_cancel_and_hold_reason_propagation(self):
        client = _FakeCellPlanClient()
        dry1 = cell_plan_dispatch.preview_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        self.assertEqual(dry1.order_source, 'harbor')
        dry2 = cell_plan_dispatch.preview_rosy_directive(
            self.operator, order_id=self.order.pk, observation_id=self.observation.pk, client=client,
        )
        self.assertEqual(dry2.order_source, 'rosy_r')

        row = cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        client.status = 'PAUSED_HUMAN'
        client.hold_reason = 'HUMAN_IN_CELL_ZONE'
        synced = cell_plan_dispatch.sync_cell_plan_dispatch(row.pk, client=client)
        self.assertEqual(synced.state, 'PAUSED_HUMAN')
        self.assertEqual(synced.reason, 'HUMAN_IN_CELL_ZONE')

        # A paused dispatch still occupies the order; close it (ABORTED) before the next request.
        client.status = 'ABORTED'
        self.assertEqual(cell_plan_dispatch.sync_cell_plan_dispatch(row.pk, client=client).state, 'ABORTED')

        client2 = _FakeCellPlanClient()
        row2 = cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client2)
        self.client.force_login(self.operator)
        with self.settings(ROOT_URLCONF='harbor_web.urls'), patch.object(cell_plan_dispatch, '_default_client', return_value=client2):
            page_html = self.client.get(f'/plans/{self.plan.pk}/').content.decode('utf-8')
            self.assertIn('셀 계획 취소', page_html)
            resp = self.client.post(f'/plans/{self.plan.pk}/', {
                'action': 'cell_plan_cancel',
                'dispatch_id': row2.pk,
                'reason': '운영자 현장 취소',
            })
            self.assertEqual(resp.status_code, 302)
        row2.refresh_from_db()
        self.assertEqual(row2.state, 'CANCELLED')
        self.assertEqual(row2.reason, '운영자 현장 취소')

    def test_production_dispatch_rejects_restored_runtime_and_unmet_readiness(self):
        with patch('harbor_web.runtime.execution_block_reason', return_value='RESTORE_RECONCILIATION_REQUIRED'):
            with self.assertRaises(services.Conflict) as restore_blocked:
                cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk)
        self.assertEqual(str(restore_blocked.exception), 'RESTORE_RECONCILIATION_REQUIRED')
        self.assertEqual(CellPlanDispatch.objects.count(), 0)

        with patch('harbor_web.readiness.first_unmet', return_value='READINESS_ARM_INCIDENT_UNMET'):
            with self.assertRaises(services.Conflict) as readiness_blocked:
                cell_plan_dispatch.dispatch_rosy_directive(
                    self.operator, order_id=self.order.pk, observation_id=self.observation.pk,
                )
        self.assertEqual(str(readiness_blocked.exception), 'READINESS_ARM_INCIDENT_UNMET')
        self.assertEqual(CellPlanDispatch.objects.count(), 0)

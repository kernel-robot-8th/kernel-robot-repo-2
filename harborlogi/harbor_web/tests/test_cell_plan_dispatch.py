"""Django tests for dual-mode OMX cell-plan dispatch (Harbor plan order vs autonomous ROSY directive)."""
import json
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from harbor_web import cell_plan_dispatch, services
from harbor_web.models import CargoFootprint, CellPlanDispatch

ROOT = Path(__file__).resolve().parents[2]


class _FakeCellPlanClient:
    def __init__(self):
        self.calls = []
        self.status = 'PROPOSED'

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
        self._last = {
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
                'task_ids': [t['task_id'] for t in tasks],
                'moves': moves,
            },
            'tasks': tasks,
        }
        return self._last

    def cell_plan(self, plan_id):
        return {
            **self._last,
            'plan': {**self._last['plan'], 'plan_id': plan_id, 'status': self.status},
            'tasks': [{**t, 'status': self.status} for t in self._last['tasks']],
        }


class CellPlanDispatchTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user('cell-op')
        for role in ('admin', 'operator', 'viewer'):
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

    def test_dual_mode_dispatch_and_sync_lifecycle(self):
        client = _FakeCellPlanClient()
        harbor_row = cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk, client=client)
        self.assertEqual(harbor_row.dispatch_mode, 'HARBOR_PLAN')
        self.assertEqual(harbor_row.order_source, 'harbor')
        self.assertEqual(harbor_row.state, 'PROPOSED')
        self.assertEqual(len(harbor_row.rosy_task_ids), len(self.plan.result['moves']))
        self.assertEqual(client.calls[0][1]['mode'], 'harbor_plan')

        rosy_row = cell_plan_dispatch.dispatch_rosy_directive(
            self.operator, order_id=self.order.pk, observation_id=self.observation.pk,
            directive_mode='outbound', plan_id=self.plan.pk, client=client,
        )
        self.assertEqual(rosy_row.dispatch_mode, 'ROSY_DIRECTIVE')
        self.assertEqual(rosy_row.order_source, 'rosy_r')
        self.assertEqual(len(rosy_row.rosy_task_ids), 3)
        self.assertNotIn('steps', client.calls[1][1])
        self.assertEqual(client.calls[1][1]['cargo_id'], 'C1')

        client.status = 'COMPLETED'
        synced = cell_plan_dispatch.sync_cell_plan_dispatch(rosy_row.pk, client=client)
        self.assertEqual(synced.state, 'COMPLETED')

        with self.assertRaises(PermissionDenied):
            cell_plan_dispatch.dispatch_harbor_plan(self.viewer, plan_id=self.plan.pk, client=client)

    def test_plan_detail_posts_both_cell_plan_actions(self):
        client = _FakeCellPlanClient()
        self.client.force_login(self.operator)
        with self.settings(ROOT_URLCONF='harbor_web.urls'):
            from unittest.mock import patch
            with patch.object(cell_plan_dispatch, '_default_client', return_value=client):
                r1 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_harbor'})
                self.assertEqual(r1.status_code, 302)
                r2 = self.client.post(f'/plans/{self.plan.pk}/', {'action': 'cell_plan_rosy', 'directive_mode': 'outbound'})
                self.assertEqual(r2.status_code, 302)
        rows = list(CellPlanDispatch.objects.filter(order=self.order).order_by('pk'))
        self.assertEqual([r.order_source for r in rows], ['harbor', 'rosy_r'])

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from harbor_web import services
from harbor_web.models import Audit, Command, Observation, Order, Plan
from harbor_web.tests import test_services as fixtures
from harbor_web.workspace import workspace_context


class WorkspaceTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def buried_plan(self, **overrides):
        order = self.order()
        self.cargo('C2')
        observation = self.observation(payload=dict(bays=[
            dict(bay_id='bay', cargo=['C1', 'C2'], capacity=3),
            dict(bay_id='temp', cargo=[], capacity=2),
            dict(bay_id='out', cargo=[], capacity=1)], holdings=[]), **overrides)
        return services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)

    def test_stages_preserve_observation_and_restore_blocker(self):
        plan = self.buried_plan()
        context = workspace_context(plan.order, plan)
        stages = context['yard_stages']
        self.assertEqual([s['phase'] for s in stages], ['관측', '재취급', '반출', '복귀'])
        self.assertEqual(stages[0]['bays'][0]['cargo'], ['C1', 'C2'])
        self.assertEqual(stages[-1]['bays'][0]['cargo'], ['C2'])
        self.assertEqual(stages[-1]['bays'][2]['cargo'], ['C1'])
        self.assertEqual(stages[0]['bays'][0]['cells'][0]['role'], 'target')
        self.assertEqual(stages[0]['bays'][0]['cells'][1]['role'], 'blocker')
        self.assertEqual(context['completion_state'], '미확인')
        plan.observation.refresh_from_db()
        self.assertEqual(plan.observation.payload['bays'][0]['cargo'], ['C1', 'C2'])

    def test_original_unknown_execution_wins_over_new_plan(self):
        original = self.buried_plan()
        command = Command.objects.create(order=original.order, plan=original, actor=self.operator,
            plan_version=1, ledger_identity='test', fleet_origin='http://127.0.0.1:9999',
            fleet_principal='test', body_digest='a' * 64, binding={}, state='UNKNOWN')
        newer = services.make_plan(self.operator, order_id=original.order_id, expected_version=1,
                                   observation_id=original.observation_id)
        with patch('harbor_web.commands.command_detail', return_value=dict(ledger=None, job=None, receipt=None, reason='LEDGER_UNAVAILABLE')):
            context = workspace_context(original.order)
            self.assertEqual(context['plan'].pk, original.pk)
            self.assertEqual(context['command'].pk, command.pk)
            self.assertEqual(context['decision']['href'], f'/work/{command.pk}/')
            history = workspace_context(original.order, newer)
            self.assertTrue(history['different_execution_plan'])
            self.assertEqual(history['completion_state'], '미확인')

    def test_historical_instruction_and_new_source_are_explicit(self):
        plan = self.buried_plan()
        services.update_order(self.operator, order_id=plan.order_id, expected_version=1,
                              priority_group=2, sequence=1, tie_policy='FIXED', note='Changed instruction')
        services.record_observation(self.admin, source_id=plan.observation.source_id,
            source_revision='source-9', observed_at=timezone.now(), provenance='DECLARED',
            payload=plan.observation.payload, condition=plan.condition)
        order = Order.objects.get(pk=plan.order_id)
        context = workspace_context(order, plan)
        self.assertEqual(context['plan'].order_version, 1)
        self.assertEqual(context['order'].version, 2)
        self.assertEqual(context['source'].pk, plan.observation_id)
        self.assertTrue({'PLAN_QUEUE_CHANGED', 'OBSERVATION_REVISION_CHANGED'} <= {c['code'] for c in context['plan_changes']})

    def test_stale_hold_has_no_partial_stages(self):
        plan = self.buried_plan(observed_at=self.now - timedelta(minutes=10))
        context = workspace_context(plan.order, plan)
        self.assertEqual(plan.result['status'], 'HOLD')
        self.assertEqual(len(context['yard_stages']), 1)
        self.assertIn('OBSERVATION_NOT_FRESH', [c['code'] for c in context['plan_changes']])
        self.assertEqual(context['display_moves'], [])

    def test_queue_permissions_and_workspace_get_are_read_only(self):
        plan = self.buried_plan()
        before = [m.objects.count() for m in (Order, Plan, Observation, Audit, Command)]
        viewer = User.objects.create_user('workspace-viewer')
        viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(viewer)
        with patch('harbor_web.commands.acquire_registry', side_effect=AssertionError('GET must not create a writer')):
            for url in ('/outbound/', f'/requests/{plan.order_id}/', f'/plans/{plan.pk}/'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'C1')
            self.assertNotContains(response, '반출 계획 만들기')
        self.assertEqual(before, [m.objects.count() for m in (Order, Plan, Observation, Audit, Command)])
        self.assertEqual(self.client.post(f'/requests/{plan.order_id}/', {'action': 'plan'}).status_code, 403)
        self.assertEqual(self.client.post('/outbound/').status_code, 405)
        self.client.force_login(self.field)
        self.assertEqual(self.client.get('/outbound/').status_code, 403)

    def test_history_is_scoped_and_non_outbound_stays_inbound(self):
        plan = self.buried_plan()
        unrelated = self.order(cargo=self.cargo('C3'), kind='INBOUND', note='Unrelated private note')
        self.client.force_login(self.operator)
        context = workspace_context(plan.order, plan)
        self.assertFalse(any(a.object_type == 'Order' and a.object_id == str(unrelated.pk) for a in context['audit_rows']))
        self.assertNotContains(self.client.get(f'/requests/{plan.order_id}/'), 'Unrelated private note')
        self.assertContains(self.client.get(f'/requests/{unrelated.pk}/'), '입고 요청')

    def test_empty_workspace_has_no_invented_layout_or_execution(self):
        order = self.order()
        context = workspace_context(order)
        self.assertIsNone(context['plan'])
        self.assertIsNone(context['source'])
        self.assertEqual(context['yard_stages'], [])
        self.assertEqual(context['completion_state'], '미확인')
        self.client.force_login(self.operator)
        response = self.client.get(f'/requests/{order.pk}/')
        self.assertContains(response, '관측 원본이 필요합니다')
        self.assertNotContains(response, 'data-yard-stage=')

    def test_held_cargo_is_preserved_without_partial_plan(self):
        order = self.order()
        self.cargo('C2')
        observation = self.observation(payload=dict(bays=[
            dict(bay_id='bay', cargo=['C1'], capacity=2), dict(bay_id='temp', cargo=[], capacity=2),
            dict(bay_id='out', cargo=[], capacity=1)], holdings=[dict(cargo_id='C2', holder_id='A')]))
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        self.assertEqual(plan.result['reason'], 'PHYSICAL_HOLDING_REQUIRES_RECONCILIATION')
        self.assertEqual(len(workspace_context(order, plan)['yard_stages']), 1)
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(f'/plans/{plan.pk}/'), '들고 있는 화물 · 위치 대조 필요')

    def test_receipt_is_not_a_business_completion_or_stage_advance(self):
        plan = self.buried_plan()
        Command.objects.create(order=plan.order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='http://127.0.0.1:9999', fleet_principal='test',
            body_digest='a' * 64, binding={}, state='RECONCILED')
        detail = dict(ledger=dict(state='WAITING', step_index=0, goal_confirmed=1),
            job={'status': 'GOAL_CONFIRMED'}, receipt=dict(physical_status='SUCCEEDED'), reason='')
        with patch('harbor_web.commands.command_detail', return_value=detail):
            context = workspace_context(plan.order, plan)
            self.assertEqual(context['completion_state'], '미확인')
            self.assertEqual(context['execution']['ledger']['step_index'], 0)
            self.client.force_login(self.operator)
            response = self.client.get(f'/plans/{plan.pk}/')
            self.assertContains(response, '현재 단계의 물리 receipt 대조됨')
            self.assertContains(response, '업무 완료 미확인')

    def test_invalid_priority_retains_input_and_revision_conflict_does_not_overwrite(self):
        plan = self.buried_plan()
        self.client.force_login(self.operator)
        url = f'/requests/{plan.order_id}/'
        data = dict(action='priority', expected_version=1, priority_group=0, sequence=1,
                    tie_policy='FIXED', note='Retain my draft')
        self.assertContains(self.client.post(url, data), 'Retain my draft')
        data.update(priority_group=2)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(Order.objects.get(pk=plan.order_id).version, 2)
        self.assertContains(self.client.get(url), '검토 후 달라진 기준')

    def test_completion_subjects_follow_original_execution_not_different_displayed_plan(self):
        original = self.buried_plan()
        Command.objects.create(order=original.order, plan=original, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='http://127.0.0.1:9999', fleet_principal='test',
            body_digest='a' * 64, binding={}, state='UNKNOWN')
        self.cargo('C3')
        payload = dict(original.condition.payload, arm_reach=[['B', name] for name in ('bay', 'temp', 'out')])
        condition = services.register_condition(self.admin, name='Other installation', calibration_ref='other-reference', payload=payload)
        observation = self.observation(source_revision='other-plan-source', condition=condition,
            payload=dict(bays=[dict(bay_id='bay', cargo=['C1', 'C3'], capacity=3),
                dict(bay_id='temp', cargo=[], capacity=2), dict(bay_id='out', cargo=[], capacity=1)], holdings=[]))
        newer = services.make_plan(self.operator, order_id=original.order_id, expected_version=1, observation_id=observation.pk)
        with patch('harbor_web.commands.command_detail', return_value=dict(ledger=None, job=None, receipt=None, reason='')):
            context = workspace_context(original.order, newer)
        self.assertEqual(context['display_moves'][0]['cargo_id'], 'C3')
        self.assertEqual(context['display_moves'][0]['primary_arm'], 'B')
        self.assertEqual(context['related_arms'], ['A'])
        self.assertEqual(context['rehandled_cargo'], ['C2'])
        self.assertEqual(context['completion_plan'].pk, original.pk)

    def test_queue_and_home_show_recheck_after_other_order_changes_or_source_replacement(self):
        plan = self.buried_plan()
        self.order(cargo=self.cargo('C3'), priority_group=9)
        self.client.force_login(self.operator)
        self.assertContains(self.client.get('/outbound/?q=C1'), '계획 재검토')
        self.assertContains(self.client.get('/'), '계획 재검토')
        # A fresh queue digest still cannot make an older source current.
        fresh = services.make_plan(self.operator, order_id=plan.order_id, expected_version=1, observation_id=plan.observation_id)
        services.record_observation(self.admin, source_id=fresh.observation.source_id, source_revision='source-10',
            observed_at=timezone.now(), provenance='DECLARED', payload=fresh.observation.payload, condition=fresh.condition)
        self.assertContains(self.client.get('/outbound/?q=C1'), '같은 출처에 새 관측')

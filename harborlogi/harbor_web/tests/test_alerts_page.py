import re
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from harbor_web import access, services
from harbor_web.models import Audit, Command, FieldEpisode, Observation, Order, Plan
from harbor_web.tests import test_services as fixtures


class AlertsPageTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def open(self, user=None, query=''):
        self.client.force_login(user or self.operator)
        return self.client.get('/alerts/' + query)

    def job(self, state=None):
        number = Order.objects.count() + 1
        order = self.order(self.cargo(f'C{number}'), sequence=number)
        observation = self.observation(code=order.cargo.code, source_revision=f'r{number}')
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
                                  observation_id=observation.pk)
        if state is None:
            return plan
        return Command.objects.create(order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='test', fleet_principal='test',
            body_digest='a' * 64, binding={}, state=state)

    def test_registered_page_and_role_access(self):
        self.assertEqual(reverse('alerts'), '/alerts/')
        self.assertEqual(access.page('alerts').roles, access.EVERYONE)
        for role in access.EVERYONE:
            user = User.objects.create_user(f'alerts-{role}')
            user.groups.add(Group.objects.get_or_create(name=role)[0])
            self.assertEqual(self.open(user).status_code, 200, role)
        self.assertEqual(self.open(User.objects.create_user('nobody')).status_code, 403)
        inactive = User.objects.create_user('inactive', is_active=False)
        inactive.groups.add(Group.objects.get(name='viewer'))
        self.assertEqual(self.open(inactive).status_code, 302)
        self.client.logout()
        self.assertRedirects(self.client.get('/alerts/'), '/accounts/login/?next=/alerts/',
                             fetch_redirect_response=False)

    def test_missing_yard_is_an_alert_not_an_empty_success(self):
        response = self.open()
        self.assertContains(response, '야드 현황이 없습니다')
        self.assertContains(response, 'href="/inventory/"')
        self.assertNotContains(response, '처리할 것 없음 · 자동 운전 중')
        self.assertEqual(response.context['alert_total'], 1)

    def test_empty_with_fresh_yard(self):
        self.cargo()
        self.observation()
        response = self.open()
        self.assertContains(response, '처리할 것 없음 · 자동 운전 중')
        self.assertEqual(response.context['alerts'], [])
        self.assertEqual(response.context['alert_total'], 0)

    def test_problem_commands_once_and_other_states_not_problems(self):
        commands = [self.job(state) for state, _ in Command._meta.get_field('state').choices]
        response = self.open()
        rows = response.context['alerts']
        # An old completion with no outbound binding is separately flagged by operations_context.
        expected = {'UNKNOWN', 'BLOCKED', 'CANCEL_PENDING', 'COMPLETED'}
        for command in commands:
            href = f'/work/{command.pk}/'
            self.assertEqual(sum(row['href'] == href for row in rows), int(command.state in expected), command.state)
        for row in rows:
            if row['href']:
                self.assertEqual(self.client.get(row['href']).status_code, 200, row)

    def test_hold_plan_attention_keeps_count_and_opens_existing_destination(self):
        plan = self.job()
        Order.objects.filter(pk=plan.order_id).update(version=2)
        second = self.order(plan.order.cargo, sequence=2)
        services.make_plan(self.operator, order_id=plan.order_id, expected_version=2,
                          observation_id=plan.observation_id, now=self.now + timedelta(minutes=2))
        services.make_plan(self.operator, order_id=second.pk, expected_version=1,
                          observation_id=plan.observation_id, now=self.now + timedelta(minutes=2))
        response = self.open()
        self.assertContains(response, '계획이 막힘')
        self.assertContains(response, '처리할 것 2건')
        self.assertEqual(response.context['alert_total'], 2)
        self.assertEqual(response.context['alert_total'], sum(row['count'] for row in response.context['alerts']))
        for row in response.context['alerts']:
            self.assertTrue(row['href'].startswith('/'))
            self.assertEqual(self.client.get(row['href']).status_code, 200)

    def test_stale_or_superseded_selection_links_to_board_with_same_pin(self):
        self.cargo()
        old = self.observation(observed_at=self.now - timedelta(minutes=2))
        self.observation(source_revision='new')
        response = self.open(query=f'?observation={old.pk}')
        self.assertEqual(response.context['alert_total'], 2)
        self.assertContains(response, '야드 현황을 새로 받아야 합니다')
        self.assertContains(response, '더 최근 야드 현황이 있습니다')
        for row in response.context['alerts']:
            self.assertEqual(row['href'], f'/?observation={old.pk}#observation-select')
        self.assertEqual(self.open(query='?observation=invalid').status_code, 404)

    def test_open_episodes_only_and_field_assignment_scope(self):
        order = self.order(kind='INBOUND')
        self.observation()
        other = User.objects.create_user('other-field')
        other.groups.add(Group.objects.get(name='field'))
        episodes = []
        for assignee, state in [(self.field, 'OPEN'), (other, 'OPEN'), (self.field, 'CONFIRMED'),
                                 (self.field, 'MISMATCH'), (self.field, 'UNABLE'), (self.field, 'REASSIGNED')]:
            episodes.append(FieldEpisode.objects.create(order=order, assigned_to=assignee,
                vehicle_id=f'vehicle-{len(episodes)}', created_by=self.operator, state=state))
        response = self.open()
        self.assertEqual([row['href'] for row in response.context['alerts']],
                         [f'/field/{episode.pk}/' for episode in episodes[:2]])
        response = self.open(self.field)
        self.assertEqual([row['href'] for row in response.context['alerts']], [f'/field/{episodes[0].pk}/'])
        self.assertContains(response, '적재면 확인 대기')
        self.assertNotContains(response, 'vehicle-1')
        self.assertEqual(self.client.get(response.context['alerts'][0]['href']).status_code, 200)
        viewer = User.objects.create_user('viewer')
        viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        response = self.open(viewer)
        self.assertEqual(response.context['alert_total'], 2)
        self.assertTrue(all(row['href'] is None for row in response.context['alerts']))

    def test_changed_order_requires_plan_review(self):
        plan = self.job()
        Order.objects.filter(pk=plan.order_id).update(version=2)
        response = self.open()
        self.assertContains(response, '계획 재검토 필요')
        self.assertEqual(response.context['alert_total'], 1)
        self.assertEqual(response.context['alerts'][0]['href'],
                         f'/?observation={plan.observation_id}#proposal-title')

    def test_visible_copy_uses_operational_terms(self):
        self.job('BLOCKED')
        response = self.open()
        content = response.content.decode().split('id="alerts-page"', 1)[1]
        content = re.sub(r'<details\b.*?</details>', '', content, flags=re.S)
        visible = re.sub(r'<[^>]+>', '', content)
        self.assertNotRegex(visible, r'관측|원본|인계|대조|회차|#\d+|READY|HOLD|BLOCKED')
        self.assertNotIn('<form', content)
        self.assertContains(response, '상태는 ROSY 실행 결과로 바뀜')

    def test_page_is_read_only_for_every_role(self):
        command = self.job('UNKNOWN')
        models = (Audit, Command, FieldEpisode, Observation, Order, Plan)
        before = [list(model.objects.values()) for model in models]
        viewer = User.objects.create_user('readonly-viewer')
        viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        for user in (self.operator, self.admin, self.field, viewer):
            self.assertEqual(self.open(user).status_code, 200)
            self.assertEqual(self.client.post('/alerts/', {'action': 'resolve', 'command': command.pk}).status_code, 405)
        self.assertEqual(before, [list(model.objects.values()) for model in models])

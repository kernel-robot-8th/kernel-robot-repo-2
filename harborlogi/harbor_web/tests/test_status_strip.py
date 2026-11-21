from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group, User
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from harbor_web import services
from harbor_web.commands import _save
from harbor_web.models import Command, FieldEpisode, Order
from harbor_web.strip import status_strip
from harbor_web.tests import test_services as fixtures


class StatusStripTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def _plan(self, order, *, observation=None):
        obs = observation or self.observation(code=order.cargo.code, source_revision=f'r{order.pk}')
        return services.make_plan(self.operator, order_id=order.pk, expected_version=order.version,
                                  observation_id=obs.pk)

    def _command(self, order, state='PREPARED', *, observation=None):
        plan = self._plan(order, observation=observation)
        return Command.objects.create(order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='test', fleet_principal='test',
            body_digest='a' * 64, binding={}, state=state)

    def test_anonymous_has_zero_queries_and_no_strip_rendered(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        with self.assertNumQueries(0):
            self.assertEqual(status_strip(request), {})
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="status-strip"')

    def test_fresh_yard_empty_work_renders_required_items_without_forbidden_words(self):
        self.cargo()
        obs = self.observation()
        self.client.force_login(self.operator)
        response = self.client.get('/workflow/')
        self.assertEqual(response.status_code, 200)
        expected_time = timezone.localtime(obs.observed_at).strftime('%H:%M')
        for text in ('aria-label="운전 상태"', '자동 운전 중', '0/0 완료', '하는 일 없음',
                     f'{expected_time} 갱신', '장비 상태 연동 전',
                     'disabled', '비상 정지', 'ROSY Fleet에서 정지 · 연동 전',
                     '상태는 ROSY 실행 결과로 바뀜'):
            self.assertContains(response, text)
        strip_html = response.content.decode().split('<section class="status-strip"', 1)[1].split('</section>', 1)[0]
        for forbidden in ('관측', '원본', '인계', '대조', '회차', 'READY', 'HOLD', '#'):
            self.assertNotIn(forbidden, strip_html)

    def test_alert_count_and_invalid_observation_query_are_safe(self):
        self.client.force_login(self.operator)
        # Missing observation -> 1 alert
        self.assertContains(self.client.get('/workflow/'), '처리할 것 1건')

        order = self.order(self.cargo('C1'), sequence=1)
        obs = self.observation(code='C1', source_revision='r1')
        Order.objects.filter(pk=order.pk).update(version=2)
        services.make_plan(self.operator, order_id=order.pk, expected_version=2,
                           observation_id=obs.pk, now=self.now + timedelta(minutes=2))
        FieldEpisode.objects.create(order=self.order(self.cargo('IN1'), kind='INBOUND', sequence=2),
                                    assigned_to=self.field, vehicle_id='v1', created_by=self.operator, state='OPEN')
        other_field = User.objects.create_user('field-2')
        other_field.groups.add(Group.objects.get(name='field'))
        FieldEpisode.objects.create(order=self.order(self.cargo('IN2'), kind='INBOUND', sequence=3),
                                    assigned_to=other_field, vehicle_id='v2', created_by=self.operator, state='OPEN')

        alerts_resp = self.client.get('/alerts/')
        strip_resp = self.client.get('/workflow/?observation=invalid')
        self.assertEqual(strip_resp.status_code, 200)
        self.assertEqual(strip_resp.context['status_strip']['alert_count'], alerts_resp.context['alert_total'])
        self.assertContains(strip_resp, f"처리할 것 {alerts_resp.context['alert_total']}건")

        # Field user only sees their own assigned OPEN episode in alert_count.
        self.client.force_login(self.field)
        field_alerts = self.client.get('/alerts/')
        self.assertEqual(field_alerts.context['status_strip']['alert_count'], 1)

    def test_current_job_states_and_completed_waiting_next(self):
        now = timezone.now()
        first = self._command(self.order(self.cargo('C1'), sequence=1), 'RECONCILED')
        self.client.force_login(self.operator)
        resp = self.client.get('/workflow/')
        self.assertContains(resp, 'C1 반출')
        self.assertContains(resp, '진행 중')

        _save(first, self.operator, 'COMPLETED')
        second = self.order(self.cargo('C2'), sequence=2)
        resp = self.client.get('/workflow/')
        self.assertContains(resp, '완료 · 다음 작업 대기 중')
        self.assertContains(resp, '1/2 완료')

        self._command(second, 'BLOCKED')
        resp = self.client.get('/workflow/')
        self.assertContains(resp, 'C2 반출')
        self.assertContains(resp, '처리할 것')

    def test_query_count_is_bounded_and_constant_as_rows_grow(self):
        self.cargo('BASE')
        obs = self.observation(code='BASE', source_revision='base')

        def count_strip_queries():
            request = RequestFactory().get('/workflow/')
            request.user = self.operator
            with CaptureQueriesContext(connection) as ctx:
                data = status_strip(request)
            self.assertIn('status_strip', data)
            return len(ctx)

        o1 = self.order(self.cargo('C1'), sequence=1)
        self._command(o1, 'PREPARED', observation=obs)
        FieldEpisode.objects.create(order=o1, assigned_to=self.field, vehicle_id='v1', created_by=self.operator)
        small = count_strip_queries()

        for i in range(2, 8):
            oi = self.order(self.cargo(f'C{i}'), sequence=i)
            self._command(oi, 'PREPARED', observation=obs)
            FieldEpisode.objects.create(order=oi, assigned_to=self.field, vehicle_id=f'v{i}', created_by=self.operator)
        large = count_strip_queries()

        self.assertLessEqual(small, 12)
        self.assertEqual(small, large)

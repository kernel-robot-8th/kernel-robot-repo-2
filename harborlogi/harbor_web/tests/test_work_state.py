from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from harbor_web.models import Audit, Command, Order, Plan
from harbor_web.tests import test_services as fixtures
from harbor_web.work_state import day_progress, work_state


class WorkStateTests(SimpleTestCase):
    def test_every_command_state(self):
        expected = {
            'PREPARED': '대기', 'DISPATCHING': '진행 중', 'RECONCILED': '진행 중',
            'COMPLETED': '완료', 'UNKNOWN': '처리할 것', 'BLOCKED': '처리할 것',
            'CANCEL_PENDING': '처리할 것', 'CANCELLED': '대기',
        }
        self.assertEqual(set(expected), {s for s, _ in Command._meta.get_field('state').choices})
        for state, label in expected.items():
            with self.subTest(state=state):
                self.assertEqual(work_state(Command(state=state)), label)

    def test_plan_is_a_proposal_never_execution_or_completion(self):
        for status, label in [('READY', '대기'), ('HOLD', '처리할 것')]:
            with self.subTest(status=status):
                self.assertEqual(work_state(plan=Plan(result={'status': status})), label)
        self.assertEqual(work_state(), '대기')

    def test_unknown_data_requires_attention(self):
        self.assertEqual(work_state(Command(state='FUTURE_STATE')), '처리할 것')
        self.assertEqual(work_state(plan=Plan(result={})), '처리할 것')

    def test_command_takes_precedence_over_plan(self):
        self.assertEqual(work_state(Command(state='COMPLETED'), Plan(result={'status': 'HOLD'})), '완료')

    def test_completed_then_waiting_only_when_next_work_is_waiting(self):
        done = Command(state='COMPLETED')
        self.assertEqual(work_state(done, next_plan=Plan(result={'status': 'READY'})), '완료 · 다음 작업 대기 중')
        for state in ('PREPARED', 'CANCELLED'):
            self.assertEqual(work_state(done, next_command=Command(state=state)), '완료 · 다음 작업 대기 중')
        for state in ('DISPATCHING', 'RECONCILED', 'UNKNOWN', 'BLOCKED', 'CANCEL_PENDING', 'COMPLETED'):
            with self.subTest(state=state):
                self.assertEqual(work_state(done, next_command=Command(state=state)), '완료')
        self.assertEqual(work_state(done), '완료')
        self.assertEqual(work_state(done, next_plan=Plan(result={'status': 'HOLD'})), '완료')
        self.assertEqual(work_state(Command(state='RECONCILED'), next_command=Command(state='PREPARED')), '진행 중')


class DayProgressTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def job(self, state=None, *, order=None):
        from harbor_web import services
        index = Order.objects.count() + 1
        order = order or self.order(self.cargo(f'C{index}'), sequence=index)
        observation = self.observation(code=order.cargo.code, source_revision=f'r{Plan.objects.count()}')
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        if state is None:
            return plan
        return Command.objects.create(order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='test', fleet_principal='test', body_digest='a' * 64,
            binding={}, state=state)

    def complete(self, command, at):
        from harbor_web.commands import _save
        with patch('django.utils.timezone.now', return_value=at):
            _save(command, self.operator, 'COMPLETED')

    def test_empty_schedule(self):
        self.assertEqual(day_progress(now=self.now), {'completed': 0, 'total': 0})

    def test_unfinished_orders_count_once_without_regard_to_creation_date_or_plan_count(self):
        plan = self.job()
        self.job('CANCELLED', order=plan.order)
        self.job('BLOCKED', order=plan.order)
        for state in ('PREPARED', 'DISPATCHING', 'RECONCILED', 'UNKNOWN', 'CANCEL_PENDING'):
            self.job(state)
        self.assertEqual(day_progress(now=self.now), {'completed': 0, 'total': 6})

    def test_local_midnight_and_first_completion_ignore_later_updates_and_repeated_audits(self):
        now = datetime(2026, 11, 21, 3, tzinfo=dt_timezone.utc)  # Seoul noon
        midnight = now.replace(hour=0) - timedelta(hours=9)
        old, today, future = self.job('RECONCILED'), self.job('RECONCILED'), self.job('RECONCILED')
        self.complete(old, midnight - timedelta(microseconds=1))
        self.complete(today, midnight)
        self.complete(future, now + timedelta(seconds=1))
        self.complete(old, now)  # Re-saving COMPLETED is not another completion today.
        Command.objects.filter(pk=today.pk).update(updated_at=now + timedelta(days=1))
        self.job('CANCELLED')
        with timezone.override('Asia/Seoul'):
            self.assertEqual(day_progress(now=now), {'completed': 1, 'total': 2})

    def test_completed_without_audit_is_closed_but_has_no_invented_completion_date(self):
        self.job('COMPLETED')
        self.assertEqual(day_progress(now=self.now), {'completed': 0, 'total': 0})

    def test_duplicate_completions_of_one_order_count_once(self):
        first = self.job('RECONCILED')
        self.complete(first, self.now - timedelta(seconds=2))
        second = self.job('RECONCILED', order=first.order)
        self.complete(second, self.now - timedelta(seconds=1))
        self.assertEqual(day_progress(now=self.now), {'completed': 1, 'total': 1})

    def test_default_clock_and_read_only(self):
        command = self.job('RECONCILED')
        self.complete(command, self.now)
        before = (Order.objects.count(), Plan.objects.count(), Command.objects.count(), Audit.objects.count())
        with patch('django.utils.timezone.now', return_value=self.now):
            self.assertEqual(day_progress(), {'completed': 1, 'total': 1})
        self.assertEqual(before, (Order.objects.count(), Plan.objects.count(), Command.objects.count(), Audit.objects.count()))

    def test_all_registered_kinds_are_included_with_constant_queries(self):
        self.job()
        Order.objects.create(created_by=self.operator, cargo=self.cargo('IN'), kind='INBOUND')
        Order.objects.create(created_by=self.operator, kind='REARRANGE')
        with self.assertNumQueries(2):
            self.assertEqual(day_progress(now=self.now), {'completed': 0, 'total': 3})

    def test_later_completed_retry_does_not_recount_an_order_finished_yesterday(self):
        now = datetime(2026, 11, 21, 3, tzinfo=dt_timezone.utc)
        first = self.job('RECONCILED')
        self.complete(first, now - timedelta(days=1))
        second = self.job('RECONCILED', order=first.order)
        self.complete(second, now)
        self.assertEqual(day_progress(now=now), {'completed': 0, 'total': 0})

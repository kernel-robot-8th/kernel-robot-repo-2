"""P3 acceptance: command history, terminal cancellation, and replan entry (design 28)."""
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from harbor_web import commands, executor, services
from harbor_web.command_models import Command
from harbor_web.services import Conflict
from harbor_web.tests import test_executor


class CommandHistoryTests(TestCase):
    cargo = test_executor.ExecutorTests.cargo
    condition = test_executor.ExecutorTests.condition
    order = test_executor.ExecutorTests.order

    def setUp(self):
        test_executor.ExecutorTests.setUp(self)

    def replan_plan(self, plan):
        """Bump the order version and make a fresh plan on a newer observation."""
        from harbor_web.models import Order
        services.update_order(self.operator, order_id=plan.order_id, expected_version=plan.order.version,
                              priority_group=plan.order.priority_group, sequence=plan.order.sequence,
                              tie_policy=plan.order.tie_policy, note=plan.order.note)
        order = Order.objects.get(pk=plan.order_id)
        observed = plan.observation
        return services.make_plan(self.operator, order_id=order.pk, expected_version=order.version,
                                  observation_id=services.record_observation(self.admin,
                                      source_id=observed.source_id, source_revision='replan-revision',
                                      observed_at=timezone.now(), provenance='SYNTHETIC', payload=observed.payload,
                                      condition=observed.condition).pk)

    def confirmed(self):
        return test_executor.ExecutorTests.confirmed_command(self)

    def plan(self):
        return test_executor.ExecutorTests.plan(self)

    def test_active_command_blocks_other_plans_for_the_order(self):
        command = self.confirmed()
        self.fleet.status = 'GOAL_CONFIRMED'
        executor.tick(registry=self.registry, evidence=test_executor.evidence_provider(timezone.now()),
                      now=timezone.now())  # Confirm step 0: partial completion.
        second = self.replan_plan(command.plan)
        with self.assertRaises(Conflict):
            commands.prepare_command(self.operator, plan_id=second.pk, expected_version=second.order_version)
        self.assertEqual(Command.objects.filter(order=command.order).count(), 1)

    def test_close_cancelled_then_replan_starts_a_new_command_with_history(self):
        command = self.confirmed()
        cancelled = commands.cancel_command(self.operator, command_id=command.pk,
                                            expected_version=command.version, reason='stop this work')
        self.assertEqual(cancelled.state, 'CANCEL_PENDING')
        with self.assertRaises(Conflict):
            commands.close_cancelled(self.operator, command_id=cancelled.pk, expected_version=999)
        closed = commands.close_cancelled(self.operator, command_id=cancelled.pk, expected_version=cancelled.version)
        self.assertEqual(closed.state, 'CANCELLED')
        original = (str(command.key), command.body_digest, command.mission_id)
        second = self.replan_plan(command.plan)
        fresh = commands.prepare_command(self.operator, plan_id=second.pk, expected_version=second.order_version)
        self.assertEqual(fresh.supersedes_id, command.pk)
        self.assertEqual(Command.objects.filter(order=command.order).count(), 2)
        command.refresh_from_db()
        self.assertEqual((str(command.key), command.body_digest, command.mission_id), original)
        self.assertNotEqual(fresh.key, command.key)
        self.assertEqual(commands._key(fresh), 'harbor:' + str(fresh.key))

    def test_close_cancelled_requires_operator_and_pending_cancellation(self):
        command = self.confirmed()
        with self.assertRaises(PermissionDenied):
            commands.close_cancelled(self.field, command_id=command.pk, expected_version=command.version)
        with self.assertRaises(Conflict):
            commands.close_cancelled(self.operator, command_id=command.pk, expected_version=command.version)
        commands.cancel_command(self.operator, command_id=command.pk, expected_version=command.version, reason='halt')
        command.refresh_from_db()
        closed = commands.close_cancelled(self.operator, command_id=command.pk, expected_version=command.version)
        self.assertEqual(closed.state, 'CANCELLED')
        with self.assertRaises(Conflict):
            commands.close_cancelled(self.operator, command_id=command.pk, expected_version=closed.version)

    def test_executor_ignores_terminal_history(self):
        command = self.confirmed()
        commands.cancel_command(self.operator, command_id=command.pk, expected_version=command.version, reason='halt')
        command.refresh_from_db()
        closed = commands.close_cancelled(self.operator, command_id=command.pk, expected_version=command.version)
        self.fleet.status = 'GOAL_CONFIRMED'
        result = executor.tick(registry=self.registry, evidence=test_executor.evidence_provider(timezone.now()),
                               now=timezone.now())
        self.assertNotIn(closed.pk, result['results'])

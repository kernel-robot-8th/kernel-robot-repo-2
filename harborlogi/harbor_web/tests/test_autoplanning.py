from copy import deepcopy
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from harbor_web import services
from harbor_web.models import Order, Plan, Command, CargoFootprint
from harbor_web.tests import test_multicell_services as multicell


class AutomaticPlanningTests(TestCase):
    setUp = multicell.MulticellServicesTests.setUp
    cargo = multicell.MulticellServicesTests.cargo
    condition = multicell.MulticellServicesTests.condition
    observe = multicell.MulticellServicesTests.observe

    def enabled(self):
        cargo = self.cargo('incoming', 1)
        condition = self.condition()
        payload = dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'out'], placements=[], holdings=[])
        observation = self.observe(condition, payload)
        order = services.create_order(self.operator, cargo=cargo, kind='INBOUND', priority_group=1,
            sequence=1, tie_policy='FIXED')
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observation.pk, now=self.now, automatic=True)
        return order, observation, plan

    def test_inbound_pins_shape_and_projects_without_moves(self):
        order, observation, plan = self.enabled()
        self.assertEqual(plan.result['status'], 'READY')
        self.assertEqual(plan.result['moves'], [])
        self.assertEqual(plan.result['plan_kind'], 'inbound-placement')
        self.assertEqual(len(services.placement_projection(plan).placements), 1)
        services.register_footprint(self.operator, cargo_id=order.cargo_id, length_cells=2)
        newer = Plan.objects.filter(order=order).latest('pk')
        self.assertNotEqual(newer.pk, plan.pk)
        self.assertEqual(newer.result['incoming_shape']['length_cells'], 2)
        self.assertEqual(plan.result['incoming_shape']['length_cells'], 1)

    def test_new_observation_dedupes_and_ignores_foreign_source(self):
        order, observation, plan = self.enabled()
        services.record_observation(self.admin, source_id='foreign', source_revision='one',
            observed_at=self.now, provenance='DECLARED', condition=observation.condition, payload=observation.payload)
        self.assertEqual(Plan.objects.filter(order=order).count(), 1)
        newer = self.observe(observation.condition, observation.payload, 'two')
        self.assertEqual(Plan.objects.filter(order=order).count(), 2)
        self.observe(observation.condition, observation.payload, 'two')
        self.assertEqual(Plan.objects.filter(order=order).count(), 2)
        latest = Plan.objects.filter(order=order).latest('pk')
        self.assertEqual(latest.created_by, self.admin)
        self.assertEqual(latest.result['previous_plan_id'], plan.pk)
        self.assertEqual(latest.observation_id, newer.pk)

    def test_condition_change_holds_without_rebinding(self):
        order, observation, plan = self.enabled()
        changed = self.condition()
        self.observe(changed, observation.payload, 'changed')
        latest = Plan.objects.filter(order=order).latest('pk')
        self.assertEqual(latest.result['reason'], 'SOURCE_CONDITION_CHANGED')
        self.assertEqual(latest.result['required_completion'], ['inbound_placed_at_target', 'arms_retreated'])
        order.refresh_from_db()
        self.assertEqual(order.planning_observation_id, observation.pk)

    def test_queue_change_creates_new_immutable_plan(self):
        order, observation, plan = self.enabled()
        outgoing = self.cargo('outgoing', 1)
        services.create_order(self.operator, cargo=outgoing, kind='OUTBOUND', priority_group=1,
            sequence=1, tie_policy='FIXED')
        latest = Plan.objects.filter(order=order).latest('pk')
        self.assertNotEqual(latest.pk, plan.pk)
        self.assertEqual(latest.result['trigger'], 'order.create')
        self.assertEqual(plan.result['queue_snapshot'][0]['id'], order.pk)

    def test_any_command_keeps_original_plan(self):
        order, observation, plan = self.enabled()
        command = Command.objects.create(order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='test', fleet_principal='test', body_digest='a'*64, binding={})
        self.observe(observation.condition, observation.payload, 'two')
        self.assertEqual(Plan.objects.filter(order=order).count(), 1)
        command.refresh_from_db()
        self.assertEqual(command.plan_id, plan.pk)

    def test_limit_rejects_seventeenth_atomically(self):
        order, observation, plan = self.enabled()
        for number in range(15):
            Order.objects.create(created_by=self.operator, cargo=order.cargo, kind='INBOUND', auto_replan=True,
                planning_observation=observation)
        extra = Order.objects.create(created_by=self.operator, cargo=order.cargo, kind='INBOUND')
        with self.assertRaisesMessage(ValidationError, '16'):
            services.make_plan(self.operator, order_id=extra.pk, expected_version=1,
                observation_id=observation.pk, automatic=True, now=self.now)
        extra.refresh_from_db()
        self.assertFalse(extra.auto_replan)
        self.assertFalse(Plan.objects.filter(order=extra).exists())

    def test_outbound_order_change_reverses_inbound_recommendation(self):
        incoming, left, right = self.cargo('incoming', 1), self.cargo('left', 1), self.cargo('right', 1)
        payload = deepcopy(self.condition().payload)
        cells = [dict(bay_id=f'Y1{c}', row=1, column=c) for c in (1, 2)]
        payload['yard_layout'] = dict(rows=1, columns=2, levels=2, cells=cells)
        payload['arm_reach'] = [['A', c['bay_id']] for c in cells] + [['A', 'temp'], ['A', 'out']]
        condition = services.register_condition(self.admin, name='Two stacks', calibration_ref='declared', payload=payload)
        observed = self.observe(condition, dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'out'],
            holdings=[], placements=[dict(cargo_id=cargo.code, area_id='yard', row=1, column=column, level=1,
                orientation=0, footprint_revision=CargoFootprint.objects.get(cargo=cargo).pk)
                for cargo, column in ((left, 1), (right, 2))]))
        outgoing = []
        for cargo, priority in ((left, 1), (incoming, 2), (right, 3)):
            outgoing.append(services.create_order(self.operator, cargo=cargo, kind='OUTBOUND',
                priority_group=priority, sequence=1, tie_policy='FIXED'))
        inbound = services.create_order(self.operator, cargo=incoming, kind='INBOUND',
            priority_group=99, sequence=1, tie_policy='FIXED')
        first = services.make_plan(self.operator, order_id=inbound.pk, expected_version=1,
            observation_id=observed.pk, automatic=True, now=self.now)
        self.assertEqual(first.result['recommendation']['column'], 2)
        services.update_order(self.operator, order_id=outgoing[0].pk, expected_version=1,
            priority_group=4, sequence=1, tie_policy='FIXED')
        latest = Plan.objects.filter(order=inbound).latest('pk')
        self.assertEqual(latest.result['recommendation']['column'], 1)
        self.assertTrue(latest.result['change_summary']['recommendation_changed'])
        self.assertEqual(first.result['recommendation']['column'], 2)

    def test_stale_new_observation_holds_and_disable_stops_events(self):
        order, observed, first = self.enabled()
        services.record_observation(self.admin, source_id=observed.source_id, source_revision='stale',
            observed_at=self.now-timedelta(minutes=2), provenance='DECLARED', condition=observed.condition,
            payload=observed.payload)
        latest = Plan.objects.filter(order=order).latest('pk')
        self.assertEqual(latest.result['reason'], 'OBSERVATION_NOT_CURRENT')
        with self.assertRaises(PermissionDenied):
            services.make_plan(self.admin, order_id=order.pk, expected_version=1,
                observation_id=observed.pk, automatic=True)
        services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observed.pk, automatic=False, now=self.now)
        count = Plan.objects.filter(order=order).count()
        self.observe(observed.condition, observed.payload, 'after-disable')
        self.assertEqual(Plan.objects.filter(order=order).count(), count)

    def test_inbound_reservations_reflow_after_earlier_shape_changes(self):
        first, observed, _ = self.enabled()
        second_cargo = self.cargo('second', 1)
        second = services.create_order(self.operator, cargo=second_cargo, kind='INBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED')
        second_plan = services.make_plan(self.operator, order_id=second.pk, expected_version=1,
            observation_id=observed.pk, automatic=True, now=self.now)
        first_plan = Plan.objects.filter(order=first).latest('pk')
        self.assertEqual(first_plan.result['recommendation']['column'], 1)
        self.assertEqual(second_plan.result['recommendation']['column'], 2)
        self.assertEqual(second_plan.result['reservation_plan_ids'], [first_plan.pk])
        services.register_footprint(self.operator, cargo_id=first.cargo_id, length_cells=2)
        newer_first = Plan.objects.filter(order=first).latest('pk')
        newer_second = Plan.objects.filter(order=second).latest('pk')
        self.assertEqual(newer_second.result['recommendation']['column'], 3)
        self.assertEqual(newer_second.result['reservation_plan_ids'], [newer_first.pk])
        self.assertEqual(second_plan.result['recommendation']['column'], 2)
        # A new explicit calculation cascades the new pinned reservation to later requests.
        manual = services.make_plan(self.operator, order_id=first.pk, expected_version=1,
            observation_id=observed.pk, now=self.now)
        cascaded = Plan.objects.filter(order=second).latest('pk')
        self.assertEqual(cascaded.result['reservation_plan_ids'], [manual.pk])
        self.assertEqual(Plan.objects.filter(order=first).latest('pk').pk, manual.pk)

    def test_reserved_cargo_never_becomes_unobserved_support(self):
        first, observed, _ = self.enabled()
        payload = deepcopy(observed.condition.payload)
        payload['yard_layout'] = dict(rows=1, columns=1, levels=2,
            cells=[dict(bay_id='Y11', row=1, column=1)])
        payload['arm_reach'] = [['A', 'Y11'], ['A', 'temp'], ['A', 'out']]
        condition = services.register_condition(self.admin, name='One stack', calibration_ref='declared', payload=payload)
        smaller = self.observe(condition, observed.payload, 'small')
        services.make_plan(self.operator, order_id=first.pk, expected_version=1,
            observation_id=smaller.pk, automatic=True, now=self.now)
        second = services.create_order(self.operator, cargo=self.cargo('second', 1), kind='INBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED')
        plan = services.make_plan(self.operator, order_id=second.pk, expected_version=1,
            observation_id=smaller.pk, automatic=True, now=self.now)
        self.assertEqual(plan.result['reason'], 'NO_LEGAL_PLACEMENT')
        self.assertEqual(plan.result['status'], 'HOLD')
        self.assertIsNone(plan.result['recommendation'])
        smaller.refresh_from_db()
        self.assertEqual(smaller.payload['placements'], [])

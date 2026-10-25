from datetime import timedelta
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import TestCase

from harbor_web import services
from harbor_web.models import Audit, Command, Plan
from harbor_web.operations import operations_context
from harbor_web.tests import test_multicell_services as spatial_fixtures
from harbor_web.tests import test_services as fixtures
from harbor_web.tests import test_workspace as workspace_fixtures


class OperationsTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order
    buried_plan = workspace_fixtures.WorkspaceTests.buried_plan

    def test_missing_observation_is_unknown_but_registered_orders_are_global(self):
        self.order(kind='INBOUND')
        context = operations_context(None)
        self.assertIsNone(context['inventory']['cargo_count'])
        self.assertIsNone(context['workload']['move_count'])
        self.assertEqual(context['orders']['inbound'], 1)
        self.assertEqual(context['orders']['outbound'], 0)
        self.assertEqual(context['actual'], dict(completed=None, throughput=None))
        html = render_to_string('operations_summary.html', dict(operations=context))
        self.assertIn('미관측', html)
        self.assertIn('미집계', html)

    def test_latest_plan_once_and_rehandle_counts_cargo_not_round_trip_moves(self):
        original = self.buried_plan()
        for _ in range(5):
            latest = services.make_plan(self.operator, order_id=original.order_id,
                expected_version=1, observation_id=original.observation_id)
        before = (Audit.objects.count(), Plan.objects.count(), Command.objects.count())
        with patch.object(Plan, 'from_db', wraps=Plan.from_db) as loaded_plan:
            context = operations_context(original.observation, now=self.now)
        self.assertEqual(loaded_plan.call_count, 1)
        self.assertEqual(before, (Audit.objects.count(), Plan.objects.count(), Command.objects.count()))
        self.assertEqual(context['inventory']['cargo_count'], 2)
        self.assertEqual(context['inventory']['capacity'], 6)
        self.assertFalse(context['inventory']['spatial'])
        workload = context['workload']
        self.assertEqual([r['plan'].pk for r in workload['plans']], [latest.pk])
        self.assertEqual(workload['move_count'], 3)
        self.assertEqual(workload['rehandled_cargo_count'], 1)
        self.assertEqual(workload['phase_counts'], {'재취급': 1, '반출': 1, '복귀': 1, '입고': 0})
        self.assertIn('적재 자리', render_to_string('operations_summary.html', dict(operations=context)))

    def test_stale_superseded_observations_exclude_ready_moves(self):
        plan = self.buried_plan()
        stale = operations_context(plan.observation, now=self.now + timedelta(seconds=61))
        self.assertTrue(stale['freshness']['stale'])
        self.assertEqual(stale['workload']['move_count'], 0)
        self.assertEqual(stale['workload']['excluded_count'], 1)
        services.record_observation(self.admin, source_id=plan.observation.source_id,
            source_revision='new', observed_at=self.now, provenance='DECLARED',
            condition=plan.condition, payload=plan.observation.payload)
        self.assertTrue(operations_context(plan.observation, now=self.now)['freshness']['superseded'])
        self.assertEqual(operations_context(plan.observation, now=self.now)['workload']['move_count'], 0)

    def test_changed_order_revision_excludes_previous_ready_metrics(self):
        plan = self.buried_plan()
        services.update_order(self.operator, order_id=plan.order_id, expected_version=1,
            priority_group=2, sequence=1, tie_policy='FIXED', note='Changed')
        context = operations_context(plan.observation, now=self.now)
        self.assertTrue(context['workload']['plans'][0]['stale'])
        self.assertEqual(context['workload']['move_count'], 0)
        self.assertEqual(context['workload']['rehandled_cargo_count'], 0)

    def test_other_source_current_plan_does_not_fall_back_to_old_selected_plan(self):
        plan = self.buried_plan()
        other = self.observation(source_id='other', source_revision='other', condition=plan.condition,
                                 payload=plan.observation.payload)
        services.make_plan(self.operator, order_id=plan.order_id, expected_version=1, observation_id=other.pk)
        context = operations_context(plan.observation, now=self.now)
        self.assertEqual(context['workload']['plans'], [])
        self.assertEqual(context['orders']['outbound'], 1)
        self.assertFalse(context['freshness']['superseded'])

    def test_handoff_keeps_original_plan_separate_from_newer_proposal(self):
        original = self.buried_plan()
        Command.objects.create(order=original.order, plan=original, actor=self.operator,
            plan_version=1, ledger_identity='test', fleet_origin='http://127.0.0.1:9999',
            fleet_principal='test', body_digest='a' * 64, binding={}, state='UNKNOWN')
        services.make_plan(self.operator, order_id=original.order_id, expected_version=1,
                           observation_id=original.observation_id)
        context = operations_context(original.observation, now=self.now)
        self.assertEqual(context['handoffs'][0]['plan'].pk, original.pk)
        self.assertEqual(context['handoffs'][0]['label'], '결과 불명')
        self.assertEqual(context['workload']['plans'], [])
        self.assertEqual(context['workload']['move_count'], 0)
        self.assertIsNone(context['actual']['completed'])

    def test_hold_has_no_partial_workload_and_holdings_are_separate(self):
        order = self.order()
        self.cargo('C2')
        observation = self.observation(payload=dict(bays=[dict(bay_id='bay', cargo=['C1'], capacity=2),
            dict(bay_id='temp', cargo=[], capacity=2), dict(bay_id='out', cargo=[], capacity=1)],
            holdings=[dict(cargo_id='C2', holder_id='A')]))
        services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        context = operations_context(observation, now=self.now)
        self.assertEqual(context['inventory']['cargo_count'], 1)
        self.assertEqual(context['inventory']['holding_count'], 1)
        self.assertEqual(context['workload']['hold_count'], 1)
        self.assertEqual(context['workload']['move_count'], 0)


class SpatialOperationsTests(TestCase):
    setUp = spatial_fixtures.MulticellServicesTests.setUp
    cargo = spatial_fixtures.MulticellServicesTests.cargo
    condition = spatial_fixtures.MulticellServicesTests.condition
    payload = spatial_fixtures.MulticellServicesTests.payload
    observe = spatial_fixtures.MulticellServicesTests.observe

    def test_multicell_footprint_counts_distinct_cargo_and_occupied_cells(self):
        cargo = self.cargo(length=3)
        held = self.cargo('held', length=1)
        temporary = self.cargo('temporary', length=2)
        outbound = self.cargo('outbound', length=1)
        from harbor_web.models import CargoFootprint
        payload = self.payload(cargo)
        for item, area in [(temporary, 'temp'), (outbound, 'out')]:
            payload['placements'].append(dict(cargo_id=item.code, area_id=area, row=1,
                column=1, level=1, orientation=0, footprint_revision=CargoFootprint.objects.get(cargo=item).pk))
        payload['holdings'] = [dict(cargo_id=held.code, holder_id='A')]
        observation = self.observe(self.condition(), payload)
        inventory = operations_context(observation, now=self.now)['inventory']
        self.assertEqual(inventory['cargo_count'], 3)
        self.assertEqual(inventory['occupied'], 6)
        self.assertEqual(inventory['capacity'], 81)
        self.assertEqual((inventory['storage_count'], inventory['temporary_count'], inventory['outbound_count']), (1, 1, 1))
        self.assertEqual(inventory['holding_count'], 1)

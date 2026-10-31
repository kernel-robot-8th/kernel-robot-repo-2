"""Design 29 web acceptance: installed per-arm bindings decide arm assignment."""
import json
import os
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from harbor_web import commands, executor, services
from harbor_web.tests import test_executor


MOVES = (('C2', 'bay', 'temp'), ('C1', 'bay', 'out'), ('C2', 'temp', 'bay'))


def per_arm_bindings(arm):
    return [dict(cargo_id=cargo, source=source, destination=destination, workcell_id='cell',
                 instance_id=f'instance-{arm}-{cargo}-{source}-{destination}', object_ref=f'object-{cargo}',
                 source_ref=f'{source}-ref', target_ref=f'{destination}-ref', yard_arm=arm)
            for cargo, source, destination in MOVES]


class PerArmBindingTests(TestCase):
    cargo = test_executor.ExecutorTests.cargo
    condition = test_executor.ExecutorTests.condition
    order = test_executor.ExecutorTests.order

    def setUp(self):
        test_executor.ExecutorTests.setUp(self)
        # The legacy table claims only arm B reaches anything; the installed
        # correspondence names A first. If the plan assigns A, the bindings decided.
        self.bindings_path = Path(os.environ['HARBOR_BINDINGS_PATH'])

    def plan(self):
        services.register_cargo(self.operator, code='C2', name='Rehandle', weight_kg=1, volume_m3=1, destination='yard')
        cargo = services.register_cargo(self.operator, code='C1', name='Outbound', weight_kg=1, volume_m3=1, destination='yard')
        condition = services.register_condition(self.admin, name='B-only legacy table', calibration_ref='ref', payload={
            'outbound_stand': 'out', 'temporary_bays': ['temp'], 'conveyor': 'belt',
            'allowed_moves': [['bay', 'out'], ['bay', 'temp'], ['temp', 'bay']],
            'max_observation_age_seconds': 60,
            'arm_reach': [['B', 'bay'], ['B', 'temp'], ['B', 'out']]})
        observation = test_executor.fixtures.ServicesTests.observation(self, payload={
            'bays': [{'bay_id': 'bay', 'cargo': ['C1', 'C2'], 'capacity': 2},
                     {'bay_id': 'temp', 'cargo': [], 'capacity': 2},
                     {'bay_id': 'out', 'cargo': [], 'capacity': 1}], 'holdings': []},
            provenance='SYNTHETIC', condition=condition)
        order = services.create_order(self.operator, cargo=cargo, kind='OUTBOUND', priority_group=1,
                                      sequence=1, tie_policy='FIXED')
        return services.make_plan(self.operator, order_id=order.pk, expected_version=1,
                                  observation_id=observation.pk)

    def test_installed_bindings_decide_the_primary_arm_over_the_legacy_table(self):
        Path(self.bindings_path).write_text(json.dumps(per_arm_bindings('A') + per_arm_bindings('B')))
        plan = self.plan()
        self.assertEqual(plan.result['status'], 'READY')
        self.assertEqual([(m['primary_arm'], tuple(m['alternate_arms'])) for m in plan.result['moves']],
                         [('A', ('B',))] * 3)
        command = commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=1)
        self.assertEqual(command.binding['move_arms'], [['A', 'B']] * 3)
        self.assertEqual(command.binding['moves'][0]['instance_id'], 'instance-A-C2-bay-temp')
        command = commands.submit_command(self.operator, command_id=command.pk, expected_version=command.version)
        self.fleet.status = 'GOAL_CONFIRMED'
        for _ in range(12):
            executor.tick(registry=self.registry, evidence=test_executor.evidence_provider(timezone.now()),
                          now=timezone.now())
            command.refresh_from_db()
            if command.state == 'COMPLETED':
                break
        self.assertEqual(command.state, 'COMPLETED')
        self.assertTrue(all(body['instance_id'].startswith('instance-A-')
                            for _, path, body in self.fleet.calls
                            if path == '/api/fleet/arm-transfers/jobs' and isinstance(body, dict)))

    def test_without_per_arm_entries_the_legacy_table_decides(self):
        Path(self.bindings_path).write_text(json.dumps(
            [dict(entry, yard_arm='') for entry in per_arm_bindings('B')]))
        plan = self.plan()
        self.assertEqual(plan.result['status'], 'READY')
        self.assertEqual([m['primary_arm'] for m in plan.result['moves']], ['B', 'B', 'B'])

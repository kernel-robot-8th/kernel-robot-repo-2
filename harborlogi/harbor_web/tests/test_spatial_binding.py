"""Design 29 acceptance: an exactly-matching installed binding opens spatial inbound execution."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from harbor.process import anchor_address
from harbor.rosy_http import RosyClient
from harbor_web import commands, executor, services
from harbor_web.models import CargoFootprint
from harbor_web.tests import test_executor
from harbor_web.tests.test_executor import PLACEMENT_SOURCE, PRINCIPAL, RETREAT_SOURCE


class SpatialBindingTests(TestCase):
    def setUp(self):
        test_executor.ExecutorTests.setUp(self)  # Ledger env, fake fleet, registry with patched sources.

    def grid_condition(self):
        cells = [dict(bay_id=f'Y{r}{c}', row=r, column=c) for r in range(1, 4) for c in range(1, 4)]
        return services.register_condition(self.admin, name='Grid', calibration_ref='declared', payload={
            'outbound_stand': 'out', 'temporary_bays': ['temp'], 'conveyor': 'belt',
            'allowed_moves': [['Y11', 'out']], 'max_observation_age_seconds': 60,
            'arm_reach': [['A', c['bay_id']] for c in cells] + [['A', 'temp'], ['A', 'out']],
            'yard_layout': dict(rows=3, columns=3, levels=3, cells=cells),
            'spatial_areas': [dict(area_id=a, rows=3, columns=3, levels=3) for a in ['temp', 'out']],
            'spatial_moves': [['yard', 'out'], ['yard', 'temp'], ['temp', 'yard']]})

    def inbound_plan(self, length=2):
        cargo = services.register_cargo(self.operator, code='C1', name='Inbound', weight_kg=1, volume_m3=1,
                                        destination='yard', length_cells=length)
        condition = self.grid_condition()
        observation = services.record_observation(self.admin, source_id='spatial', source_revision='one',
            observed_at=timezone.now(), provenance='SYNTHETIC', condition=condition,
            payload=dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'out'], holdings=[], placements=[]))
        order = services.create_order(self.operator, cargo=cargo, kind='INBOUND', priority_group=1,
                                      sequence=1, tie_policy='FIXED')
        return services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)

    def install_binding(self, plan, **override):
        recommendation, revision = plan.result['recommendation'], plan.result['footprint_revision']
        anchor = anchor_address(recommendation['area_id'], recommendation['row'],
                                recommendation['column'], recommendation['level'])
        entry = dict(cargo_id='C1', footprint_revision=revision, area_id=recommendation['area_id'],
            row=recommendation['row'], column=recommendation['column'], level=recommendation['level'],
            orientation=recommendation.get('orientation', 0), source='supply', yard_arm='A',
            workcell_id='cell', instance_id='instance', object_ref='object-c1',
            source_ref='supply-ref', target_ref=anchor)
        entry.update(override)
        entries = [entry] + (override.pop('_duplicate', []) if '_duplicate' in override else [])
        Path(os.environ['HARBOR_BINDINGS_PATH']).write_text(json.dumps([e for e in entries if isinstance(e, dict)]))

    def test_matching_binding_opens_execution_and_completes_at_the_anchor(self):
        plan = self.inbound_plan(length=2)
        self.assertEqual(plan.result['status'], 'READY')
        self.assertFalse(commands.capability(plan)['enabled'])  # No binding yet: gate stays.
        self.install_binding(plan)
        self.assertEqual(commands.capability(plan)['reason'], '')
        command = commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=1)
        recommendation = plan.result['recommendation']
        anchor = anchor_address(recommendation['area_id'], recommendation['row'],
                                recommendation['column'], recommendation['level'])
        self.assertEqual(command.binding['outbound_stand'], anchor)
        self.assertEqual(command.binding['move_arms'], [['A']])
        command = commands.submit_command(self.operator, command_id=command.pk, expected_version=command.version)
        self.fleet.status = 'GOAL_CONFIRMED'
        for _ in range(12):
            executor.tick(registry=self.registry, evidence=test_executor.evidence_provider(timezone.now()),
                          now=timezone.now())
            command.refresh_from_db()
            if command.state == 'COMPLETED':
                break
        self.assertEqual(command.state, 'COMPLETED')
        posted = self.fleet.calls[0]
        self.assertEqual(posted[1], '/api/fleet/arm-transfers/jobs')
        self.assertEqual(set(posted[2]), {'workcell_id', 'instance_id', 'object_ref', 'source', 'target', 'request_key'})
        self.assertNotIn('C1', posted[2].values())

    def test_mismatched_or_ambiguous_bindings_keep_the_gate(self):
        plan = self.inbound_plan(length=2)
        self.install_binding(plan, orientation=90)
        self.assertEqual(commands.capability(plan)['reason'], 'SPATIAL_EXECUTION_BINDINGS_REQUIRED')
        self.install_binding(plan)
        entry = json.loads(Path(os.environ['HARBOR_BINDINGS_PATH']).read_text())[0]
        Path(os.environ['HARBOR_BINDINGS_PATH']).write_text(json.dumps([entry, dict(entry)]))
        self.assertEqual(commands.capability(plan)['reason'], 'SPATIAL_EXECUTION_BINDINGS_REQUIRED')
        with self.assertRaises(Exception):
            commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=1)

    def test_outbound_spatial_multicell_stays_blocked(self):
        cargo = services.register_cargo(self.operator, code='C1', name='Blocked', weight_kg=1, volume_m3=1,
                                        destination='yard', length_cells=2)
        condition = self.grid_condition()
        observation = services.record_observation(self.admin, source_id='spatial', source_revision='one',
            observed_at=timezone.now(), provenance='SYNTHETIC', condition=condition,
            payload=dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'out'],
                holdings=[], placements=[dict(cargo_id='C1', area_id='yard', row=1, column=1, level=1,
                    orientation=0, footprint_revision=CargoFootprint.objects.get(cargo=cargo).pk)]))
        order = services.create_order(self.operator, cargo=cargo, kind='OUTBOUND', priority_group=1,
                                      sequence=1, tie_policy='FIXED')
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        # The spatial outbound planner may answer READY, but it carries no placement
        # recommendation, so no spatial binding can match and the gate stays (ADR0008:
        # legacy bindings must not extend to multi-cell until design 29 covers outbound).
        self.assertEqual(commands.capability(plan)['reason'], 'SPATIAL_EXECUTION_BINDINGS_REQUIRED')
        self.assertFalse(commands.capability(plan)['enabled'])

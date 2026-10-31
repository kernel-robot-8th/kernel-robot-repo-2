from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from harbor_web import services, commands
from harbor_web.models import Cargo, CargoFootprint, Audit


class MulticellServicesTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user('operator')
        self.operator.groups.add(Group.objects.get_or_create(name='operator')[0])
        self.admin = User.objects.create_user('admin')
        self.admin.groups.add(Group.objects.get_or_create(name='admin')[0])
        self.now = timezone.now()

    def cargo(self, code='C1', length=2):
        return services.register_cargo(self.operator, code=code, name='Cargo', weight_kg=1,
            volume_m3=1, destination='yard', length_cells=length)

    def condition(self):
        cells = [dict(bay_id=f'Y{r}{c}', row=r, column=c) for r in range(1, 4) for c in range(1, 4)]
        return services.register_condition(self.admin, name='Grid', calibration_ref='declared', payload={
            'outbound_stand': 'out', 'temporary_bays': ['temp'], 'conveyor': 'belt',
            'allowed_moves': [['Y11', 'out']], 'max_observation_age_seconds': 60,
            'arm_reach': [['A', c['bay_id']] for c in cells] + [['A', 'temp'], ['A', 'out']],
            'yard_layout': dict(rows=3, columns=3, levels=3, cells=cells),
            'spatial_areas': [dict(area_id=a, rows=3, columns=3, levels=3) for a in ['temp', 'out']],
            'spatial_moves': [['yard', 'out'], ['yard', 'temp'], ['temp', 'yard']]})

    def payload(self, cargo):
        return dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'out'], holdings=[],
            placements=[dict(cargo_id=cargo.code, area_id='yard', row=1, column=1, level=1,
                orientation=0, footprint_revision=CargoFootprint.objects.get(cargo=cargo).pk)])

    def observe(self, condition, payload, revision='one'):
        return services.record_observation(self.admin, source_id='declared', source_revision=revision,
            observed_at=self.now, provenance='DECLARED', condition=condition, payload=payload)

    def order(self, cargo):
        return services.create_order(self.operator, cargo=cargo, kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED')

    def test_footprints_are_immutable_authored_revisions_and_atomic(self):
        cargo = self.cargo()
        first = CargoFootprint.objects.get(cargo=cargo)
        newer = services.register_footprint(self.operator, cargo_id=cargo.pk, length_cells=3)
        self.assertNotEqual(first.pk, newer.pk)
        self.assertEqual(first.length_cells, 2)
        self.assertEqual(newer.created_by, self.operator)
        self.assertEqual(Audit.objects.filter(action='footprint.register').count(), 2)
        with self.assertRaises(ValidationError):
            CargoFootprint.objects.filter(pk=first.pk).update(length_cells=1)
        with self.assertRaises(ValidationError):
            first.delete()
        first.length_cells = 1
        with self.assertRaises(ValidationError):
            first.save()
        with self.assertRaises(PermissionDenied):
            services.register_footprint(self.admin, cargo_id=cargo.pk, length_cells=1)
        with self.assertRaises(ValidationError):
            self.cargo('bad', 4)
        self.assertFalse(Cargo.objects.filter(code='bad').exists())

    def test_shape_revision_full_coverage_and_geometry_are_validated(self):
        cargo, other = self.cargo(), self.cargo('other', 1)
        condition = self.condition()
        payload = self.payload(cargo)
        observed = self.observe(condition, payload)
        self.assertEqual(services.spatial_snapshot(observed).shapes[0].length_cells, 2)
        for modify in ('unknown_revision', 'other_revision', 'partial', 'duplicate', 'floating', 'out_of_bounds', 'overlap'):
            invalid = deepcopy(payload)
            placement = invalid['placements'][0]
            if modify == 'unknown_revision':
                placement['footprint_revision'] = 99999
            elif modify == 'other_revision':
                placement['footprint_revision'] = CargoFootprint.objects.get(cargo=other).pk
            elif modify == 'partial':
                invalid['observed_areas'].remove('temp')
            elif modify == 'duplicate':
                invalid['placements'].append(deepcopy(placement))
            elif modify == 'floating':
                placement['level'] = 2
            elif modify == 'out_of_bounds':
                placement['column'] = 3
            else:
                invalid['placements'].append(dict(placement, cargo_id=other.code,
                    footprint_revision=CargoFootprint.objects.get(cargo=other).pk))
            with self.subTest(modify=modify), self.assertRaises(ValidationError):
                self.observe(condition, invalid, modify)

    def test_plan_pins_shape_source_and_projects_whole_cargo_once(self):
        cargo = self.cargo()
        condition = self.condition()
        payload = self.payload(cargo)
        observed = self.observe(condition, payload)
        services.register_footprint(self.operator, cargo_id=cargo.pk, length_cells=3)
        order = self.order(cargo)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observed.pk, now=self.now)
        self.assertEqual(plan.result['status'], 'READY')
        self.assertEqual(plan.result['model'], 'grid-placement-v1')
        self.assertEqual(plan.result['source_shapes'][0]['length_cells'], 2)
        self.assertEqual(len(plan.result['moves']), 1)
        self.assertEqual(plan.result['moves'][0]['footprint_revision'], payload['placements'][0]['footprint_revision'])
        self.assertEqual(plan.result['projected_placements'][0]['area_id'], 'out')
        self.assertIsNone(plan.result['projected_bays'])
        observed.refresh_from_db()
        self.assertEqual(observed.payload, payload)
        self.assertEqual(self.observe(condition, payload).pk, observed.pk)
        with patch('harbor_web.commands._config') as config, patch('harbor_web.commands._bound') as bound, \
                patch('harbor_web.runtime.execution_block_reason', return_value=''):
            self.assertEqual(commands.capability(plan)['reason'], 'SPATIAL_EXECUTION_BINDINGS_REQUIRED')
            config.assert_not_called()
            bound.assert_not_called()

    def test_legacy_plan_and_existing_plan_require_spatial_source_after_new_shape(self):
        from harbor_web.tests.test_services import ServicesTests
        cargo = self.cargo(length=1)
        condition = ServicesTests.condition(self)
        observed = ServicesTests.observation(self, condition=condition)
        order = self.order(cargo)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observed.pk, now=self.now)
        self.assertEqual(plan.result['status'], 'READY')
        services.register_footprint(self.operator, cargo_id=cargo.pk, length_cells=2)
        blocked = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observed.pk, now=self.now)
        self.assertEqual(blocked.result['reason'], 'SPATIAL_OBSERVATION_REQUIRED')
        self.assertEqual(blocked.result['moves'], [])
        with patch('harbor_web.runtime.execution_block_reason', return_value=''), \
                patch('harbor_web.commands._bound') as bound:
            self.assertEqual(commands.capability(plan)['reason'], 'SPATIAL_OBSERVATION_REQUIRED')
            bound.assert_not_called()

    def test_spatial_conditions_reject_bad_areas_moves_and_reach(self):
        condition = self.condition()
        for key, value in [('spatial_areas', []), ('spatial_areas', [dict(area_id='temp', rows=4, columns=3, levels=3)]),
                ('spatial_areas', condition.payload['spatial_areas'] * 2),
                ('spatial_moves', [['yard', 'unknown']]), ('spatial_moves', [['yard', 'out']] * 2),
                ('arm_reach', [['A', 'unknown']]), ('max_observation_age_seconds', True)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                services.register_condition(self.admin, name='bad', calibration_ref='declared',
                    payload={**condition.payload, key: value})

    def test_spatial_policy_direct_validation_rejects_bad_reach_and_age(self):
        condition = self.condition()
        for key, value in [('arm_reach', [['A', 'unknown']]), ('arm_reach', [['A', 'Y11']] * 2),
                ('max_observation_age_seconds', True)]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                services.spatial_policy({**condition.payload, key: value})

    def test_spatial_input_is_closed_and_roles_preserve_history(self):
        cargo = self.cargo()
        condition = self.condition()
        payload = self.payload(cargo)
        for value in [dict(payload, bays=[]), dict(payload, observed_areas=['yard', 'out', 'temp', 'temp'])]:
            with self.assertRaises(ValidationError):
                self.observe(condition, value)
        with self.assertRaises(PermissionDenied):
            services.record_observation(self.operator, source_id='manual', source_revision='one',
                observed_at=self.now, provenance='DECLARED', condition=condition, payload=payload)
        observation = self.observe(condition, payload)
        order = self.order(cargo)
        with self.assertRaises(PermissionDenied):
            services.make_plan(self.admin, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observation.pk, now=self.now)
        with self.assertRaises(ValidationError):
            type(plan).objects.filter(pk=plan.pk).update(result={})

    def test_spatial_hold_preserves_model_and_source_shapes_without_projection(self):
        cargo = self.cargo()
        condition = self.condition()
        payload = self.payload(cargo)
        observation = self.observe(condition, payload)
        order = services.create_order(self.operator, cargo=cargo, kind='INBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED')
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observation.pk, now=self.now)
        self.assertEqual(plan.result['reason'], 'INBOUND_ALREADY_OBSERVED')
        self.assertEqual(plan.result['model'], 'grid-placement-v1')
        self.assertIsNone(plan.result['projected_placements'])
        self.assertIsNone(plan.result['projected_bays'])
        self.assertEqual(plan.result['moves'], [])

    def test_holdings_remain_unlocated_without_inventing_a_shape_revision(self):
        cargo, held = self.cargo(), self.cargo('held', 3)
        condition = self.condition()
        payload = self.payload(cargo)
        payload['holdings'] = [dict(cargo_id=held.code, holder_id='A')]
        observed = self.observe(condition, payload)
        snapshot = services.spatial_snapshot(observed)
        self.assertEqual([s.cargo_id for s in snapshot.shapes], [cargo.code])
        self.assertEqual(snapshot.holdings[0].cargo_id, held.code)
        order = self.order(cargo)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1,
            observation_id=observed.pk, now=self.now)
        self.assertEqual(plan.result['status'], 'HOLD')
        self.assertEqual(plan.result['moves'], [])
        self.assertIsNone(plan.result['projected_placements'])


class HistoricalCargoTests(TestCase):
    def test_historical_cargo_does_not_gain_an_invented_footprint(self):
        user = User.objects.create_user('historical-owner')
        cargo = Cargo.objects.create(created_by=user, code='historical', name='Original',
            weight_kg=1, volume_m3=1, destination='yard')
        self.assertFalse(CargoFootprint.objects.filter(cargo=cargo).exists())


class FootprintMigrationTests(TransactionTestCase):
    def test_migration_preserves_historical_cargo_without_shape_backfill(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        user = User.objects.create_user('migration-owner')
        target = [('harbor_web', '0008_command_order')]
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        try:
            old_cargo = executor.loader.project_state(target).apps.get_model('harbor_web', 'Cargo')
            cargo = old_cargo.objects.create(created_by_id=user.pk, code='old-source', name='Original',
                weight_kg=1, volume_m3=1, destination='yard')
        finally:
            cleanup = MigrationExecutor(connection)
            cleanup.migrate(cleanup.loader.graph.leaf_nodes())
        self.assertEqual(Cargo.objects.get(pk=cargo.pk).name, 'Original')
        self.assertFalse(CargoFootprint.objects.exists())

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import TestCase

from harbor_web import services
from harbor_web.models import Observation
from harbor_web.tests import test_services as fixtures
from harbor_web.workspace import workspace_context


class ConfiguredYardTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    order = fixtures.ServicesTests.order
    condition = fixtures.ServicesTests.condition

    def condition_payload(self):
        root = Path(__file__).resolve().parents[2]
        payload = json.loads((root / 'config/yard-grid.example.json').read_text(encoding='utf-8'))
        payload['yard_layout'] = json.loads((root / 'config/yard-layout.json').read_text(encoding='utf-8'))
        return payload

    def grid(self):
        order = self.order()
        self.cargo('C2')
        payload = self.condition_payload()
        condition = services.register_condition(self.admin, name='Synthetic 27 positions',
            calibration_ref='synthetic-grid-only', payload=payload)
        # Reverse the observation array: the configured coordinates must still win.
        bays = [dict(bay_id=cell['bay_id'], cargo=['C1', 'C2'] if cell['bay_id'] == 'Y11' else [], capacity=3)
                for cell in reversed(payload['yard_layout']['cells'])]
        bays += [dict(bay_id='temp', cargo=[], capacity=3), dict(bay_id='stand', cargo=[], capacity=1)]
        observation = services.record_observation(self.admin, source_id='synthetic-grid', source_revision='1',
            observed_at=self.now, provenance='SYNTHETIC', payload=dict(bays=bays, holdings=[]), condition=condition)
        return order, observation

    def test_each_stage_uses_configured_coordinates_and_all_27_positions(self):
        order, observation = self.grid()
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        stages = workspace_context(order, plan)['yard_stages']
        self.assertEqual(len(stages), 4)
        for stage in stages:
            self.assertEqual(sum(len(row['cells']) for layer in stage['layers'] for row in layer['rows']), 27)
            self.assertEqual(stage['layers'][0]['rows'][0]['cells'][0]['bay_id'], 'Y11')
            self.assertEqual([b['bay_id'] for b in stage['auxiliary_bays']], ['temp', 'stand'])
        self.assertEqual(stages[0]['layers'][0]['rows'][0]['cells'][0]['code'], 'C1')
        self.assertEqual(stages[0]['layers'][1]['rows'][0]['cells'][0]['code'], 'C2')
        self.assertIsNone(stages[0]['layers'][2]['rows'][0]['cells'][0]['code'])
        self.assertEqual(stages[-1]['layers'][0]['rows'][0]['cells'][0]['code'], 'C2')
        moves = workspace_context(order, plan)['display_moves']
        self.assertEqual([(m['source_level'], m['destination_level']) for m in moves], [(2, 1), (1, 1), (1, 1)])

    def test_missing_or_wrong_capacity_never_becomes_a_declared_empty_cell(self):
        order, observation = self.grid()
        for changed in (observation.payload['bays'][1:], [dict(b, capacity=1) if b['bay_id'] == 'Y33' else b for b in observation.payload['bays']]):
            with self.subTest(bays=changed), self.assertRaises(ValidationError):
                services.record_observation(self.admin, source_id='synthetic-grid', source_revision='bad',
                    observed_at=self.now, provenance='SYNTHETIC', payload=dict(bays=changed, holdings=[]), condition=observation.condition)
        self.assertEqual(Observation.objects.count(), 1)

    def test_invalid_mapping_and_auxiliary_overlap_are_rejected(self):
        for field in ('duplicate', 'stand', 'unknown_move'):
            payload = self.condition_payload()
            if field == 'duplicate':
                payload['yard_layout']['cells'][0]['row'] = 2
            elif field == 'stand':
                payload['yard_layout']['cells'][0]['bay_id'] = payload['outbound_stand']
            else:
                payload['allowed_moves'].append(['unmapped', 'temp'])
            with self.subTest(field=field), self.assertRaises(ValidationError):
                services.register_condition(self.admin, name='bad grid', calibration_ref='synthetic-only', payload=payload)

    def test_nonlexical_configured_mapping_wins_over_id_spelling(self):
        order, observed = self.grid()
        payload = self.condition_payload()
        payload['yard_layout']['cells'][0].update(row=3, column=3)
        payload['yard_layout']['cells'][-1].update(row=1, column=1)
        condition = services.register_condition(self.admin, name='Remapped logical grid',
            calibration_ref='synthetic-only', payload=payload)
        services.record_observation(self.admin, source_id=observed.source_id, source_revision='remapped',
            observed_at=self.now, provenance='SYNTHETIC', payload=observed.payload, condition=condition)
        stage = workspace_context(order)['yard_stages'][0]
        self.assertEqual(stage['layers'][0]['rows'][0]['cells'][0]['bay_id'], 'Y33')
        self.assertEqual(stage['layers'][0]['rows'][2]['cells'][2]['bay_id'], 'Y11')
        self.assertEqual(stage['layers'][0]['rows'][2]['cells'][2]['code'], 'C1')

    def test_inventory_and_workspace_expose_grid_and_legacy_is_not_remapped(self):
        order, observation = self.grid()
        self.client.force_login(self.operator)
        for url in (f'/requests/{order.pk}/', '/inventory/'):
            response = self.client.get(url)
            self.assertContains(response, '3행 × 3열 × 3층')
            self.assertContains(response, 'data-yard-layer="3"')
            self.assertContains(response, 'Y33')
        legacy = fixtures.ServicesTests.observation(self, source_revision='legacy', condition=fixtures.ServicesTests.condition(self))
        self.assertNotIn('yard_layout', legacy.condition.payload)
        response = self.client.get(f'/inventory/?observation={legacy.pk}')
        self.assertContains(response, '좌표 미등록')
        self.assertNotContains(response, 'data-yard-layer=')

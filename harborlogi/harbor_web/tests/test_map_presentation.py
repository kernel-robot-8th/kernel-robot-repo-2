from django.test import TestCase

from harbor_web import services
from harbor_web.models import CargoFootprint, Observation
from harbor_web.tests import test_yard_grid
from harbor_web.workspace import workspace_context


class MapPresentationTests(TestCase):
    setUp = test_yard_grid.ConfiguredYardTests.setUp
    cargo = test_yard_grid.ConfiguredYardTests.cargo
    order = test_yard_grid.ConfiguredYardTests.order
    condition_payload = test_yard_grid.ConfiguredYardTests.condition_payload
    grid = test_yard_grid.ConfiguredYardTests.grid

    def configured(self):
        payload = self.condition_payload()
        payload['spatial_areas'] = [dict(area_id='temp', rows=1, columns=1, levels=3),
                                    dict(area_id='stand', rows=1, columns=1, levels=1)]
        payload['spatial_moves'] = [['yard', 'temp'], ['temp', 'yard'], ['yard', 'stand']]
        payload['area_presentation'] = dict(yard=dict(label='Main storage', x=10, y=20),
            temp=dict(label='Buffer', x=15, y=20), stand=dict(label='Dispatch', x=15, y=25))
        return services.register_condition(self.admin, name='Edited map', calibration_ref='synthetic-only', payload=payload)

    def test_unobserved_map_has_definition_and_never_fabricates_observation(self):
        condition = self.configured()
        self.client.force_login(self.operator)
        response = self.client.get(f'/inventory/?condition={condition.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['observation'])
        scene = response.context['yard_scene']
        self.assertEqual(scene['kind'], 'map-definition')
        self.assertEqual(scene['frames'][0]['boxes'], [])
        self.assertEqual(scene['areas'][0]['x'], 10)
        self.assertContains(response, '배치 미관측')
        self.assertContains(response, 'Y11-L1')
        self.assertNotContains(response, '빈 위치</strong>')
        self.assertFalse(Observation.objects.exists())

    def test_inventory_and_every_plan_frame_use_pinned_metadata(self):
        order, old = self.grid()
        condition = self.configured()
        payload = dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'stand'], holdings=[],
            placements=[dict(cargo_id=shape.cargo.code, area_id='yard', row=1, column=1,
                level=level, orientation=0, footprint_revision=shape.pk)
                for level, shape in enumerate(CargoFootprint.objects.select_related('cargo').order_by('pk'), 1)])
        observed = services.record_observation(self.admin, source_id='edited', source_revision='1',
            observed_at=self.now, provenance='SYNTHETIC', payload=payload, condition=condition)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observed.pk)
        context = workspace_context(order, plan)
        self.assertEqual(context['yard_stages'][0]['area_label'], 'Main storage')
        self.assertEqual(context['yard_scene']['areas'][0]['label'], 'Main storage')
        for stage in context['yard_stages']:
            self.assertEqual(stage['area_presentation'], condition.payload['area_presentation'])
        self.client.force_login(self.operator)
        response = self.client.get(f'/inventory/?observation={observed.pk}')
        self.assertEqual(response.context['condition'], condition)
        geometry = lambda scene: [(box['id'], box['x'], box['y'], box['z']) for box in scene['frames'][0]['boxes']]
        self.assertEqual(geometry(response.context['yard_scene']), geometry(context['yard_scene']))
        inbound = self.order(cargo=order.cargo, kind='INBOUND')
        held = services.make_plan(self.operator, order_id=inbound.pk, expected_version=1, observation_id=observed.pk)
        response = self.client.get(f'/plans/{held.pk}/')
        self.assertEqual(response.context['observation_stage']['area_label'], 'Main storage')
        self.assertEqual(response.context['yard_scene']['areas'][0]['x'], 10)
        old_response = self.client.get(f'/inventory/?observation={old.pk}')
        self.assertEqual(old_response.context['yard_scene']['areas'][0]['x'], 0)

    def test_condition_selection_filters_choices_and_invalid_selection_never_falls_back(self):
        _, observed = self.grid()
        unobserved = self.configured()
        self.client.force_login(self.operator)
        for query in ('condition=bad', 'condition=999999', 'observation=bad', 'observation=999999'):
            self.assertEqual(self.client.get('/inventory/?' + query).status_code, 404)
        self.assertEqual(self.client.get(f'/inventory/?condition={unobserved.pk}&observation={observed.pk}').status_code, 400)
        response = self.client.get(f'/inventory/?condition={observed.condition_id}')
        self.assertEqual(response.context['observation'], observed)
        self.assertTrue(all(item.condition_id == observed.condition_id for item in response.context['observations']))
        response = self.client.get(f'/inventory/?condition={unobserved.pk}')
        self.assertEqual(response.context['observations'], [])
        self.assertIsNone(response.context['observation'])

    def test_old_selected_observation_remains_a_choice_beyond_recent_limit(self):
        _, observed = self.grid()
        for revision in range(101):
            services.record_observation(self.admin, source_id='later', source_revision=str(revision),
                observed_at=self.now, provenance='SYNTHETIC', payload=observed.payload, condition=observed.condition)
        self.client.force_login(self.operator)
        response = self.client.get(f'/inventory/?condition={observed.condition_id}&observation={observed.pk}')
        self.assertEqual(response.context['observation'], observed)
        self.assertIn(observed, response.context['observations'])
        self.assertEqual(len(response.context['observations']), 101)

    def test_no_parameters_use_latest_observation_else_latest_configured_map(self):
        configured = services.register_condition(self.admin, name='Older configured map',
            calibration_ref='synthetic-only', payload=self.condition_payload())
        from harbor_web.tests import test_services
        test_services.ServicesTests.condition(self)
        self.client.force_login(self.operator)
        response = self.client.get('/inventory/')
        self.assertEqual(response.context['condition'], configured)
        self.assertEqual(response.context['yard_scene']['kind'], 'map-definition')
        _, observed = self.grid()
        self.configured()
        response = self.client.get('/inventory/')
        self.assertEqual(response.context['observation'], observed)
        self.assertEqual(response.context['condition'], observed.condition)

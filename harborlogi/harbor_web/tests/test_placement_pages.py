from django.test import TestCase
from harbor_web import services
from harbor_web.models import Observation, Plan
from harbor_web.tests import test_autoplanning


class PlacementPagesTests(TestCase):
    setUp = test_autoplanning.AutomaticPlanningTests.setUp
    cargo = test_autoplanning.AutomaticPlanningTests.cargo
    condition = test_autoplanning.AutomaticPlanningTests.condition
    observe = test_autoplanning.AutomaticPlanningTests.observe
    enabled = test_autoplanning.AutomaticPlanningTests.enabled

    def test_pinned_observation_and_prediction_are_separate_and_get_has_no_writes(self):
        order, observation, first = self.enabled()
        self.client.force_login(self.operator)
        counts = (Plan.objects.count(), Observation.objects.count())
        response = self.client.get(f'/plans/{first.pk}/')
        self.assertEqual(response.status_code, 200)
        frames = response.context['yard_scene']['frames']
        self.assertEqual(frames[0]['boxes'], [])
        self.assertEqual([b['id'] for b in frames[1]['boxes']], [order.cargo.code])
        self.assertContains(response, '입고 자동 배치')
        self.assertContains(response, '자동 재계획 켜짐')
        self.client.get(f'/requests/{order.pk}/')
        self.assertEqual(counts, (Plan.objects.count(), Observation.objects.count()))
        services.register_footprint(self.operator, cargo_id=order.cargo_id, length_cells=2)
        newer = Plan.objects.filter(order=order).latest('pk')
        before = self.client.get(f'/plans/{first.pk}/').context['yard_scene']['frames'][1]['boxes'][0]
        after = self.client.get(f'/plans/{newer.pk}/').context['yard_scene']['frames'][1]['boxes'][0]
        self.assertEqual(before['width'], 1)
        self.assertEqual(after['width'], 2)
        self.assertEqual(Observation.objects.count(), counts[1])

    def test_inbound_form_controls_auto_mode_and_admin_cannot_plan(self):
        order, observation, first = self.enabled()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(f'/requests/{order.pk}/', dict(action='plan', expected_version=1,
            observation=observation.pk, automatic='on')).status_code, 403)
        self.client.force_login(self.operator)
        response = self.client.get(f'/requests/{order.pk}/')
        self.assertContains(response, '입고 배치 자동 계산')
        response = self.client.post(f'/requests/{order.pk}/', dict(action='plan', expected_version=1,
            observation=observation.pk))
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertFalse(order.auto_replan)

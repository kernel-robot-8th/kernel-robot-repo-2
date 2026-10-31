from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase

from harbor_web import services
from harbor_web.models import Audit, Command, FieldEpisode, Plan
from harbor_web.tests import test_services as fixtures


class OperatorJourneyTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def test_selected_source_reaches_workspace_without_rewriting_saved_plan(self):
        order = self.order()
        original = self.observation()
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=original.pk)
        selected = self.observation(source_revision='next')
        self.client.force_login(self.operator)
        response = self.client.get(f'/requests/?observation={selected.pk}')
        self.assertContains(response, f'/requests/{order.pk}/?observation={selected.pk}')
        response = self.client.get(f'/requests/{order.pk}/?observation={selected.pk}')
        self.assertEqual(response.context['source'], original)
        self.assertTrue(response.context['review_differs'])
        self.assertEqual(response.context['plan_form'].initial['observation'], selected)
        self.assertContains(response, '저장된 계획')
        self.assertContains(response, f'/records/?order={order.pk}')
        self.assertContains(response, f'action="/requests/{order.pk}/?observation={selected.pk}"', count=2)
        response = self.client.get(f'/plans/{plan.pk}/?observation={selected.pk}')
        self.assertEqual(response.context['review_observation'], selected)
        self.assertEqual(response.context['source'], original)
        self.assertEqual(self.client.get(f'/plans/{plan.pk}/?observation=bad').status_code, 404)
        plan.refresh_from_db()
        self.assertEqual(plan.observation_id, original.pk)

    def test_expired_confirmation_shows_new_assignment_instead_of_departure(self):
        order = self.order(kind='INBOUND')
        episode = services.assign_episode(self.operator, order_id=order.pk, vehicle_id='test-vehicle', assigned_to=self.field)
        services.confirm_episode(self.field, episode_id=episode.pk, expected_version=1,
            outcome='CLEAR', observed_at=self.now, now=self.now)
        self.client.force_login(self.operator)
        with patch('harbor_web.field_pages.timezone.now', return_value=self.now + timedelta(minutes=6)):
            response = self.client.get(f'/field/{episode.pk}/')
        self.assertNotContains(response, 'name="action" value="departure"')
        self.assertContains(response, '새 회차로 재확인 배정')
        self.assertEqual(FieldEpisode.objects.count(), 1)

    def test_guidance_is_scoped_and_get_does_not_change_business_records(self):
        self.client.force_login(self.field)
        self.assertContains(self.client.get('/workflow/'), '배정된 확인 열기')
        self.assertNotContains(self.client.get('/workflow/'), 'href="/records/"')
        self.assertEqual(self.client.get('/settings/integration/').status_code, 403)
        self.client.force_login(self.admin)
        before = (Audit.objects.count(), Command.objects.count(), Plan.objects.count())
        self.assertContains(self.client.get('/settings/integration/'), '저장된 설정')
        self.assertEqual(before, (Audit.objects.count(), Command.objects.count(), Plan.objects.count()))

    def test_viewer_sees_field_status_without_forbidden_links(self):
        from django.contrib.auth.models import User, Group
        viewer = User.objects.create_user('reader')
        viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        order = self.order(kind='INBOUND')
        episode = services.assign_episode(self.operator, order_id=order.pk, vehicle_id='test', assigned_to=self.field)
        self.client.force_login(viewer)
        self.assertNotContains(self.client.get(f'/requests/{order.pk}/'), f'href="/field/{episode.pk}/"')
        self.assertNotContains(self.client.get(f'/records/?order={order.pk}'), f'href="/field/{episode.pk}/"')

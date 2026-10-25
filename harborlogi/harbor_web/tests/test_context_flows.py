from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase

from harbor_web import forms, services
from harbor_web.field_pages import episode_status
from harbor_web.models import Audit, FieldConfirmation, FieldEpisode, Observation
from harbor_web.tests import test_services as fixtures


class ContextFlowTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def test_context_selection_is_validated_and_preserved(self):
        order = self.order()
        old = self.observation()
        newer = self.observation(source_revision='newer')
        self.client.force_login(self.operator)
        response = self.client.get(f'/requests/?observation={old.pk}')
        self.assertEqual(response.context['review_observation'], old)
        response = self.client.get(f'/requests/new/?cargo={order.cargo_id}&kind=OUTBOUND&observation={old.pk}')
        self.assertEqual(response.context['form'].initial['cargo'], order.cargo)
        for route in ('/requests/', '/requests/new/', '/inventory/', f'/requests/{order.pk}/'):
            for bad in ('nope', '', '999999', '-1'):
                self.assertEqual(self.client.get(f'{route}?observation={bad}').status_code, 404)
        with patch('harbor_web.workspace.workspace_context', return_value={} ) as context:
            self.client.get(f'/requests/{order.pk}/?observation={old.pk}')
            self.assertEqual(context.call_args.kwargs['review_observation'], old)
            self.assertEqual(context.call_args.kwargs['plan_form'].initial['observation'], old)
        self.assertNotEqual(old.pk, newer.pk)

    def test_old_explicit_source_remains_in_plan_choices(self):
        self.cargo()
        old = self.observation()
        Observation.objects.bulk_create([Observation(created_by=self.admin, source_id='batch',
            source_revision=str(i), observed_at=self.now, provenance='DECLARED', condition=old.condition,
            payload=old.payload) for i in range(101)])
        form = forms.PlanForm(review_observation=old)
        self.assertIn(old, form.fields['observation'].queryset)

    def test_inventory_keeps_selected_old_source_in_rendered_options(self):
        self.cargo()
        old = self.observation()
        Observation.objects.bulk_create([Observation(created_by=self.admin, source_id='batch',
            source_revision=str(i), observed_at=self.now, provenance='DECLARED', condition=old.condition,
            payload=old.payload) for i in range(101)])
        self.client.force_login(self.operator)
        response = self.client.get(f'/inventory/?observation={old.pk}')
        self.assertEqual(response.context['observation'], old)
        self.assertEqual(len(response.context['observations']), 101)
        self.assertContains(response, f'<option value="{old.pk}" selected>')
        self.assertContains(response, f'/requests/?kind=OUTBOUND&amp;observation={old.pk}')

    def test_request_redirect_keeps_selected_source_and_footprint_defaults_to_latest(self):
        cargo = self.cargo()
        source = self.observation()
        services.register_footprint(self.operator, cargo_id=cargo.pk, length_cells=3)
        self.client.force_login(self.operator)
        response = self.client.get(f'/cargo/{cargo.pk}/footprint/')
        self.assertEqual(response.context['form'].initial['length_cells'], 3)
        response = self.client.post(f'/requests/new/?observation={source.pk}', dict(cargo=cargo.pk,
            kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED', note=''))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(f'/?observation={source.pk}'))

    def test_confirmation_expiry_requires_new_episode_and_preserves_fact(self):
        order = self.order(kind='INBOUND')
        episode = services.assign_episode(self.operator, order_id=order.pk, vehicle_id='vehicle', assigned_to=self.field)
        confirmation = services.confirm_episode(self.field, episode_id=episode.pk, expected_version=1,
            outcome='CLEAR', observed_at=self.now, now=self.now)
        episode.refresh_from_db()
        self.assertTrue(episode_status(episode, now=self.now)['can_prepare_departure'])
        self.assertTrue(episode_status(episode, now=self.now + timedelta(minutes=5))['confirmation_fresh'])
        stale = episode_status(episode, now=self.now + timedelta(minutes=6))
        self.assertFalse(stale['can_prepare_departure'])
        self.assertTrue(stale['needs_new_confirmation'])
        self.assertFalse(episode_status(episode, now=self.now - timedelta(seconds=1))['confirmation_fresh'])
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(f'/field/new/?order={order.pk}').context['form'].initial['order'], order)
        self.assertEqual(self.client.get('/field/new/?order=nope').status_code, 404)
        outbound = self.order(cargo=self.cargo('C2'), sequence=2)
        self.assertEqual(self.client.get(f'/field/new/?order={outbound.pk}').status_code, 404)
        response = self.client.post(f'/field/{episode.pk}/', dict(action='reassign', expected_version=2, assigned_to=self.field.pk))
        self.assertEqual(response.status_code, 302)
        replacement = FieldEpisode.objects.order_by('-pk').first()
        self.assertEqual(replacement.state, 'OPEN')
        self.assertFalse(FieldConfirmation.objects.filter(episode=replacement).exists())
        self.assertEqual(FieldConfirmation.objects.get(pk=confirmation.pk).observed_at, self.now)
        self.client.force_login(self.field)
        self.assertEqual(self.client.post(f'/field/{replacement.pk}/', dict(action='reassign', expected_version=1,
            assigned_to=self.field.pk)).status_code, 403)

    def test_order_records_do_not_mix_equal_primary_keys(self):
        order = self.order()
        source = self.observation()
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=source.pk)
        self.client.force_login(self.operator)
        response = self.client.get(f'/records/?order={order.pk}')
        found = {(row.object_type, row.object_id) for row in response.context['page']}
        self.assertIn(('Order', str(order.pk)), found)
        self.assertIn(('Plan', str(plan.pk)), found)
        self.assertNotIn(('Cargo', str(order.cargo_id)), found)
        response = self.client.get(f'/records/?object_type=Cargo&q={order.cargo_id}')
        self.assertTrue(all(row.object_type == 'Cargo' for row in response.context['page']))
        self.assertEqual(len(self.client.get('/records/?q=1').context['page']), 0)
        self.assertEqual(self.client.get('/records/?object_type=Secret').status_code, 400)

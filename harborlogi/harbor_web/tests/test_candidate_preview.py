from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from harbor_web.models import Audit, Command, Observation, Plan
from harbor_web.tests import test_ranked_recommendation as fixtures


class CandidatePreviewTests(TestCase):
    setUp = fixtures.RankedRecommendationTests.setUp
    cargo = fixtures.RankedRecommendationTests.cargo
    condition = fixtures.RankedRecommendationTests.condition
    observe = fixtures.RankedRecommendationTests.observe
    order = fixtures.RankedRecommendationTests.order
    payload = fixtures.RankedRecommendationTests.payload
    prepared = fixtures.RankedRecommendationTests.prepared
    calculate = fixtures.RankedRecommendationTests.calculate

    def plan(self):
        order, observed = self.prepared()
        return self.calculate(order, observed)

    def test_other_candidate_replays_its_saved_moves_without_mutation_or_execution(self):
        plan = self.plan()
        saved = deepcopy(plan.result)
        alternative = saved['optimization']['alternatives'][1]
        self.assertNotEqual(alternative['moves'], saved['moves'])
        before = [model.objects.count() for model in (Plan, Observation, Audit, Command)]
        self.client.force_login(self.operator)
        with patch('harbor.spatial.plan_spatial', side_effect=AssertionError('GET searched')),\
             patch('harbor.rosy_http.RosyClient._http', side_effect=AssertionError('GET contacted Fleet')):
            response = self.client.get(f'/plans/{plan.pk}/candidates/2/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '저장된 후보')
        self.assertContains(response, '비교 전용')
        self.assertNotContains(response, '계획 확정·ROSY에 즉시 전송')
        self.assertEqual(len(response.context['yard_scene']['frames']), len(alternative['moves']) + 1)
        for rendered, move in zip(response.context['display_moves'], alternative['moves']):
            self.assertEqual(rendered['destination_placement'], move['destination_placement'])
        self.assertEqual([model.objects.count() for model in (Plan, Observation, Audit, Command)], before)
        plan.refresh_from_db()
        self.assertEqual(plan.result, saved)

    def test_recommended_candidate_matches_saved_plan_and_links_are_discoverable(self):
        plan = self.plan()
        self.client.force_login(self.operator)
        response = self.client.get(f'/plans/{plan.pk}/')
        self.assertContains(response, f'/plans/{plan.pk}/candidates/2/')
        response = self.client.get(f'/plans/{plan.pk}/candidates/1/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_recommended'])
        self.assertEqual([move['destination_placement'] for move in response.context['display_moves']],
                         [move['destination_placement'] for move in plan.result['moves']])

    def test_invalid_legacy_and_unsupported_candidates_do_not_invent_previews(self):
        plan = self.plan()
        self.client.force_login(self.operator)
        for rank in (0, 6, 999):
            self.assertEqual(self.client.get(f'/plans/{plan.pk}/candidates/{rank}/').status_code, 404)
        for update in ({'optimization': None}, {'status': 'HOLD'}):
            result = dict(deepcopy(plan.result), **update)
            other = Plan.objects.create(created_by=self.operator, order=plan.order, order_version=plan.order_version,
                observation=plan.observation, condition=plan.condition, queue_digest=plan.queue_digest, result=result)
            self.assertEqual(self.client.get(f'/plans/{other.pk}/candidates/1/').status_code, 404)
        for malformed in ([], 'unsupported-legacy-result'):
            with self.subTest(result_type=type(malformed).__name__):
                other = Plan.objects.create(created_by=self.operator, order=plan.order, order_version=plan.order_version,
                    observation=plan.observation, condition=plan.condition, queue_digest=plan.queue_digest, result=malformed)
                self.assertEqual(self.client.get(f'/plans/{other.pk}/candidates/1/').status_code, 404)

    def test_corrupted_moves_cannot_show_geometry_that_did_not_move(self):
        plan = self.plan()
        self.client.force_login(self.operator)
        for kind in ('missing-cargo', 'wrong-source', 'wrong-destination-identity'):
            with self.subTest(kind=kind):
                result = deepcopy(plan.result)
                move = result['optimization']['alternatives'][1]['moves'][-1 if kind == 'missing-cargo' else 0]
                if kind == 'missing-cargo':
                    move['cargo_id'] = 'unknown-cargo'
                elif kind == 'wrong-source':
                    move['source_placement']['column'] = 3
                else:
                    move['destination_placement']['cargo_id'] = plan.order.cargo.code
                other = Plan.objects.create(created_by=self.operator, order=plan.order, order_version=plan.order_version,
                    observation=plan.observation, condition=plan.condition, queue_digest=plan.queue_digest, result=result)
                self.assertEqual(self.client.get(f'/plans/{other.pk}/candidates/2/').status_code, 404)

    def test_bounded_saved_report_keeps_its_optimization_limit_visible(self):
        order, observed = self.prepared()
        with patch('harbor.spatial.SPATIAL_SEARCH_LIMIT', 4):
            plan = self.calculate(order, observed)
        self.assertEqual(plan.result['status'], 'READY')
        self.assertFalse(plan.result['optimization']['search_complete'])
        self.client.force_login(self.operator)
        response = self.client.get(f'/plans/{plan.pk}/candidates/1/')
        self.assertContains(response, '탐색 한도 내 추천')
        self.assertContains(response, '최적성은 확정하지 않았습니다')

    def test_roles_get_only_and_stale_original_are_explicit(self):
        plan = self.plan()
        url = f'/plans/{plan.pk}/candidates/2/'
        self.assertEqual(self.client.get(url).status_code, 302)
        viewer = get_user_model().objects.create_user('candidate-viewer')
        viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 405)
        plan.order.version += 1
        plan.order.save(update_fields=['version'])
        response = self.client.get(url)
        self.assertContains(response, '업무 지시가 변경되었습니다')
        self.assertEqual(response.context['plan'].order_version, plan.order_version)
        outsider = get_user_model().objects.create_user('candidate-field')
        outsider.groups.add(Group.objects.get_or_create(name='field')[0])
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

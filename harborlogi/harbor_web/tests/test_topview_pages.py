"""두 야드 화면의 탑뷰 목록·장애 격리·읽기 권한 시험."""
import os
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase

from harbor_web.tests import test_multicell_services as fixtures


class TopviewPagesTests(TestCase):
    condition = fixtures.MulticellServicesTests.condition
    cargo = fixtures.MulticellServicesTests.cargo
    payload = fixtures.MulticellServicesTests.payload
    observe = fixtures.MulticellServicesTests.observe

    def setUp(self):
        fixtures.MulticellServicesTests.setUp(self)
        self.client.force_login(self.operator)
        env = patch.dict(os.environ, {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'test-fleet-secret',
            'HARBOR_FLEET_CA_FILE': '',
        })
        env.start()
        self.addCleanup(env.stop)
        reader = patch('harbor_web.topview_proxy.VisionClient')
        self.reader_class = reader.start()
        self.addCleanup(reader.stop)
        self.reader = self.reader_class.return_value
        self.reader.sources.return_value = {'sources': ['cam-one', 'cam-two']}

    def pages(self):
        for path in ('/', '/inventory/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'ROSY 탑뷰', count=1)
            yield response

    def test_missing_settings_are_visible_even_without_yard_observation(self):
        for key, status in (('HARBOR_FLEET_ORIGIN', 'ROSY 연동 전'), ('HARBOR_FLEET_TOKEN', 'ROSY 로그인 필요')):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                for response in self.pages():
                    self.assertContains(response, status)
                    self.assertNotContains(response, 'data-topview-source')
        self.reader.sources.assert_not_called()

    def test_shared_panel_and_camera_selection_next_to_observed_yard(self):
        self.observe(self.condition(), self.payload(self.cargo()))
        for response in self.pages():
            self.assertTemplateUsed(response, 'topview_panel.html')
            self.assertContains(response, 'data-topview-source', count=1)
            self.assertContains(response, 'value="/topview/cam-one/frame/" selected')
            self.assertContains(response, 'value="/topview/cam-two/frame/"')
            self.assertContains(response, 'class="yard-topview"', count=1)
            self.assertContains(response, 'harbor_web/topview.mjs')
            self.assertIn('blob:', response['Content-Security-Policy'])
            self.assertNotContains(response, 'test-fleet-secret')
        self.assertEqual(self.reader_class.call_args.kwargs['timeout'], 2)

    def test_one_camera_starts_directly_without_selector(self):
        self.reader.sources.return_value = {'sources': ['cam-one', 'cam-one']}
        for response in self.pages():
            self.assertContains(response, 'data-topview-url="/topview/cam-one/frame/"')
            self.assertNotContains(response, 'data-topview-source')

    def test_invalid_peer_sources_do_not_hide_a_valid_camera(self):
        self.reader.sources.return_value = {'sources': [None, '', 'bad/source', '.', '..', 'ceiling_north']}
        for response in self.pages():
            self.assertContains(response, 'data-topview-url="/topview/ceiling_north/frame/"')
            self.assertNotContains(response, 'data-topview-source')
            self.assertContains(response, '<option value="auto" selected>실제 환경 + 좌표</option>')

    def test_omx_overhead_is_selected_first_in_raw_mode_with_points_controls_and_unverified_notice(self):
        self.reader.sources.return_value = {'sources': ['ceiling_north', 'arm_a_wrist', 'omx_overhead']}
        for response in self.pages():
            html = response.content.decode()
            self.assertContains(response, 'data-topview-url="/topview/omx_overhead/frame/"')
            self.assertContains(response, 'value="/topview/omx_overhead/frame/" selected')
            self.assertContains(response, 'value="/topview/ceiling_north/frame/"')
            self.assertContains(response, 'value="/topview/arm_a_wrist/frame/"')
            self.assertLess(html.index('omx_overhead'), html.index('ceiling_north'))
            self.assertContains(response, '<option value="raw" selected>카메라 화면</option>')
            self.assertContains(response, 'data-topview-points-toggle checked')
            self.assertContains(response, '위치는 실측 전 참고값')
            self.assertNotContains(response, '위치 미정:')

    def test_connection_errors_leave_both_pages_available(self):
        for error in (OSError('test-fleet-secret'), ValueError('invalid response')):
            with self.subTest(error=type(error).__name__):
                self.reader.sources.side_effect = error
                for response in self.pages():
                    self.assertContains(response, 'ROSY 연결 안 됨')
                    self.assertNotContains(response, 'test-fleet-secret')

    def test_empty_or_malformed_cameras_leave_both_pages_available(self):
        for snapshot in ({}, {'sources': None}, {'sources': {}}, {'sources': [None]},
                         {'sources': ['bad/source']}, {'sources': []}):
            with self.subTest(snapshot=snapshot):
                self.reader.sources.return_value = snapshot
                for response in self.pages():
                    self.assertContains(response, 'ROSY 연결 안 됨')

    def test_all_read_roles_can_see_the_panel(self):
        for role in ('viewer', 'operator', 'admin'):
            with self.subTest(role=role):
                self.operator.groups.set([Group.objects.get_or_create(name=role)[0]])
                list(self.pages())

    def test_field_only_users_see_panel_on_inventory_without_redirect_lookup(self):
        self.operator.groups.set([Group.objects.get_or_create(name='field')[0]])
        redirect_response = self.client.get('/')
        self.assertEqual((redirect_response.status_code, redirect_response['Location']), (302, '/alerts/'))
        self.assertEqual(self.client.get('/alerts/').status_code, 200)
        self.assertEqual(self.client.get('/settings/').status_code, 403)
        self.reader.sources.assert_not_called()
        inventory_response = self.client.get('/inventory/')
        self.assertEqual(inventory_response.status_code, 200)
        self.assertContains(inventory_response, 'ROSY 탑뷰', count=1)

    def test_board_default_camera_follows_running_job_without_extra_queries(self):
        from harbor_web import services
        from harbor_web.models import CellPlanDispatch, Command, Order, Plan

        condition = self.condition()
        cargo_out = self.cargo('OUT1')
        cargo_in = self.cargo('IN1')
        obs = self.observe(condition, self.payload(cargo_out))
        self.reader.sources.return_value = {
            'sources': ['ceiling_north', 'omx_arm_s_top', 'omx_side', 'top'],
        }

        # 1) No running job -> yard topview ('top'), arm S still in <select>, empty placeholder present.
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['topview']['default_source_id'], 'top')
        self.assertContains(response, 'data-topview-url="/topview/top/frame/"')
        self.assertContains(response, 'data-topview-camera="top"')
        self.assertContains(response, 'value="/topview/top/frame/" selected')
        self.assertContains(response, 'value="/topview/omx_arm_s_top/frame/" data-camera-id="omx_arm_s_top"')
        self.assertContains(response, 'data-topview-empty hidden')

        # 2) Running OUTBOUND Command -> yard topview ('top').
        out_order = services.create_order(
            self.operator, cargo=cargo_out, kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        out_plan = services.make_plan(
            self.operator, order_id=out_order.pk, expected_version=1, observation_id=obs.pk,
        )
        out_cmd = Command.objects.create(
            order=out_order, plan=out_plan, actor=self.operator, plan_version=1,
            ledger_identity='out-1', fleet_origin='https://fleet.example', fleet_principal='svc',
            body_digest='a' * 64, binding={}, state='RECONCILED',
        )
        out_resp = self.client.get('/')
        self.assertEqual(out_resp.context['topview']['default_source_id'], 'top')
        self.assertContains(out_resp, 'value="/topview/top/frame/" selected')
        self.assertNotContains(out_resp, 'data-topview-job-camera')

        # 3) Running REARRANGE Command -> yard topview ('top').
        Command.objects.filter(pk=out_cmd.pk).update(state='COMPLETED')
        rearr_order = Order.objects.create(created_by=self.operator, kind='REARRANGE')
        rearr_plan = Plan.objects.create(
            order=rearr_order, order_version=1, observation=obs, condition=condition,
            queue_digest='q', result={'status': 'READY', 'reason': '', 'moves': [{'cargo_id': 'OUT1', 'source': 'a', 'destination': 'b'}]},
            created_by=self.operator,
        )
        rearr_cmd = Command.objects.create(
            order=rearr_order, plan=rearr_plan, actor=self.operator, plan_version=1,
            ledger_identity='rearr-1', fleet_origin='https://fleet.example', fleet_principal='svc',
            body_digest='b' * 64, binding={}, state='DISPATCHING',
        )
        rearr_resp = self.client.get('/')
        self.assertEqual(rearr_resp.context['topview']['default_source_id'], 'top')

        # 4) Running INBOUND Command -> S-arm topview ('omx_arm_s_top') with pending places shown.
        Command.objects.filter(pk=rearr_cmd.pk).update(state='COMPLETED')
        in_order = services.create_order(
            self.operator, cargo=cargo_in, kind='INBOUND',
            priority_group=1, sequence=2, tie_policy='FIXED',
        )
        in_plan = Plan.objects.create(
            order=in_order, order_version=1, observation=obs, condition=condition,
            queue_digest='q', result={'status': 'READY', 'reason': '', 'moves': []}, created_by=self.operator,
        )
        in_cmd = Command.objects.create(
            order=in_order, plan=in_plan, actor=self.operator, plan_version=1,
            ledger_identity='in-1', fleet_origin='https://fleet.example', fleet_principal='svc',
            body_digest='c' * 64, binding={}, state='RECONCILED',
        )
        # Zero extra queries for the camera choice is asserted in test_topview_proxy (active_order_kind).
        in_resp = self.client.get('/')
        self.assertEqual(in_resp.context['topview']['default_source_id'], 'omx_arm_s_top')
        self.assertContains(in_resp, 'data-topview-url="/topview/omx_arm_s_top/frame/"')
        self.assertContains(in_resp, 'data-topview-camera="omx_arm_s_top" data-topview-job-camera')
        self.assertContains(in_resp, 'value="/topview/omx_arm_s_top/frame/" selected')
        self.assertNotContains(in_resp, 'value="/topview/top/frame/" selected')
        self.assertContains(in_resp, 'data-points-source="omx_arm_s_top">위치 미정: Pinky 짐칸 · 컨베이어 투입</span>')

        # 5) Running INBOUND when arm_s_top is not in ROSY sources -> falls back to yard ('top').
        self.reader.sources.return_value = {'sources': ['ceiling_north', 'omx_side', 'top']}
        fallback_resp = self.client.get('/')
        self.assertEqual(fallback_resp.context['topview']['default_source_id'], 'top')
        self.assertContains(fallback_resp, 'value="/topview/top/frame/" selected')

        # 6) Running INBOUND CellPlanDispatch -> S-arm topview ('arm_s_top').
        Command.objects.filter(pk=in_cmd.pk).update(state='CANCELLED')
        self.reader.sources.return_value = {'sources': ['top', 'arm_s_top']}
        CellPlanDispatch.objects.create(
            order=in_order, plan=in_plan, observation=obs,
            dispatch_mode='ROSY_DIRECTIVE', directive_mode='INBOUND',
            request_key='cp-in-1', order_source='harbor',
            state='RUNNING', created_by=self.operator,
        )
        cp_resp = self.client.get('/')
        self.assertEqual(cp_resp.context['topview']['default_source_id'], 'arm_s_top')
        self.assertContains(cp_resp, 'value="/topview/arm_s_top/frame/" selected')

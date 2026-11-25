"""Tests for ROSY cell-site recognition UI panel, cache, 404 copy, alerts, and read-only safety."""
import io
import json
import os
import re
from urllib.error import HTTPError
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from harbor_web import services
from harbor_web.alert_pages import ATTENTION_KIND
from harbor_web.models import Audit, CargoFootprint, Command, FleetConnection, Observation, Plan
from harbor_web.site_recognition_view import clear_site_recognition_cache
from harbor_web.tests.test_control_board import site_policy
from harbor_web.tests.test_glossary_sweep import FORBIDDEN, _visible_and_summary_text


def _snapshot(sites, *, observed_at='2026-11-25T05:02:00Z', source_id='omx_overhead'):
    return {
        'schema': 'rosy.cell-site-recognition.v1',
        'snapshot_id': 'snap-ui-1',
        'observed_at': observed_at,
        'source_id': source_id,
        'calibration_revision': 'cal-1',
        'site_map_revision': 1,
        'sites': sites,
    }


def _assert_glossary_clean(testcase, html):
    visible, summaries = _visible_and_summary_text(html)
    testcase.assertIsNone(FORBIDDEN.search(visible), visible)
    testcase.assertIsNone(FORBIDDEN.search(summaries), summaries)


class SiteRecognitionPanelTests(TestCase):
    def setUp(self):
        clear_site_recognition_cache()
        self.addCleanup(clear_site_recognition_cache)
        self.admin = User.objects.create_user('recog-admin')
        self.operator = User.objects.create_user('recog-operator')
        self.viewer = User.objects.create_user('recog-viewer')
        for user, roles in (
            (self.admin, ('admin', 'viewer')),
            (self.operator, ('operator', 'viewer')),
            (self.viewer, ('viewer',)),
        ):
            user.groups.add(*[Group.objects.get_or_create(name=name)[0] for name in roles])

        self.condition = services.register_condition(
            self.admin, name='3x6 site', calibration_ref='synthetic', payload=site_policy(),
        )
        placements = []
        for code, row, column, level in (
            ('C-1', 1, 1, 1),
            ('C-3', 1, 1, 2),
            ('C-4', 1, 4, 1),
        ):
            services.register_cargo(
                self.operator, code=code, name=code, weight_kg=2, volume_m3='0.012',
                destination='컨베이어', length_cells=3,
            )
            fp = CargoFootprint.objects.filter(cargo__code=code).latest('pk')
            placements.append(dict(
                cargo_id=code, area_id='yard', row=row, column=column, level=level,
                orientation=0, footprint_revision=fp.pk,
            ))
        self.observation = services.record_observation(
            self.admin, source_id='site-cam', source_revision='1', observed_at=timezone.now(),
            provenance='SYNTHETIC', condition=self.condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                placements=placements,
                holdings=[],
            ),
        )
        self.client.force_login(self.operator)

        # Avoid outbound calls from topview/site_places panels during recognition tests.
        p1 = patch('harbor_web.views.topview_context', return_value=dict(
            sources=[], status='ROSY 연동 전', default_source_id='', default_view_mode='auto',
            site_pixels=None, site_pixels_json='', pending_points=[],
        ))
        p2 = patch('harbor_web.site_places_view.site_places_context', return_value=dict(
            available=False, status='ROSY 연동 전',
        ))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _fleet_env(self, **extra):
        FleetConnection.objects.update_or_create(
            pk=1,
            defaults=dict(origin='https://fleet.example', principal='harbor-svc', arm_console_enabled=False, version=1),
        )
        env = {
            'HARBOR_FLEET_ORIGIN': '',
            'HARBOR_FLEET_ALLOWED_ORIGINS': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'secret-service-token',
        }
        env.update(extra)
        return env

    def test_default_enabled_uses_db_connection_and_zero_disables(self):
        snap = _snapshot([
            {'site_ref': 'yard-r1-c1-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
             'object_ref_read': 'C-1', 'confidence': 0.95},
        ])
        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor.rosy_http.RosyClient.site_recognition', return_value=snap) as mock_read:
            resp = self.client.get('/inventory/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_read.call_count, 1)
        html = resp.content.decode('utf-8')
        self.assertContains(resp, 'ROSY 인식 · 14:02 · omx_overhead · 37자리 중 1자리')
        self.assertContains(resp, '일치 1자리')
        self.assertNotContains(resp, 'secret-service-token')
        self.assertIsNone(re.search(r'\sstyle=', html))
        _assert_glossary_clean(self, html)

        clear_site_recognition_cache()
        with patch.dict(os.environ, self._fleet_env(HARBOR_ROSY_SITE_RECOGNITION='0')), \
                patch('harbor.rosy_http.RosyClient.site_recognition', return_value=snap) as mock_read_off:
            resp_off = self.client.get('/inventory/')
        self.assertEqual(resp_off.status_code, 200)
        mock_read_off.assert_not_called()
        self.assertContains(resp_off, 'ROSY 인식 연동 전')

    def test_404_not_yet_observed_and_missing_api_messages(self):
        not_obs = HTTPError(
            'https://fleet.example/api/fleet/cell-site-recognition',
            404,
            'Not Found',
            {},
            io.BytesIO(json.dumps({'detail': {'code': 'CELL_SITE_RECOGNITION_NOT_YET_OBSERVED'}}).encode()),
        )
        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor.rosy_http.RosyClient.site_recognition', side_effect=not_obs):
            resp = self.client.get('/inventory/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ROSY 인식 스냅샷 수신 대기')
        _assert_glossary_clean(self, resp.content.decode('utf-8'))

        clear_site_recognition_cache()
        missing_api = HTTPError(
            'https://fleet.example/api/fleet/cell-site-recognition',
            404,
            'Not Found',
            {},
            io.BytesIO(b'Not Found'),
        )
        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor.rosy_http.RosyClient.site_recognition', side_effect=missing_api):
            resp2 = self.client.get('/inventory/')
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, 'ROSY에 아직 인식 API 없음')
        _assert_glossary_clean(self, resp2.content.decode('utf-8'))

    def test_cache_calls_rosy_once_across_two_gets_and_keys_by_token_and_ttl(self):
        snap = _snapshot([
            {'site_ref': 'yard-r1-c1-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
             'object_ref_read': 'C-1', 'confidence': 0.9},
        ])
        now_box = [100.0]
        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor_web.site_recognition_view.time.monotonic', side_effect=lambda: now_box[0]), \
                patch('harbor.rosy_http.RosyClient.site_recognition', return_value=snap) as mock_read:
            self.assertEqual(self.client.get('/inventory/').status_code, 200)
            self.assertEqual(self.client.get('/inventory/').status_code, 200)
            self.assertEqual(mock_read.call_count, 1)

            # Different token digest misses cache.
            with patch.dict(os.environ, {'HARBOR_FLEET_TOKEN': 'rotated-service-token'}):
                self.assertEqual(self.client.get('/inventory/').status_code, 200)
            self.assertEqual(mock_read.call_count, 2)

            # After 15s TTL expires, original key fetches again.
            now_box[0] = 116.0
            self.assertEqual(self.client.get('/inventory/').status_code, 200)
            self.assertEqual(mock_read.call_count, 3)

    def test_mismatches_only_on_inventory_and_alerts_with_no_db_mutations(self):
        snap = _snapshot([
            {'site_ref': 'yard-r1-c1-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
             'object_ref_read': 'C-1', 'confidence': 0.94},
            {'site_ref': 'yard-r1-c1-l2', 'state': 'EMPTY', 'object_class': None,
             'object_ref_read': None, 'confidence': 0.89},
            {'site_ref': 'yard-r1-c4-l1', 'state': 'OCCUPIED', 'object_class': 'blue',
             'object_ref_read': 'C-9', 'confidence': 0.91},
            {'site_ref': 'yard-r2-c1-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
             'object_ref_read': 'C-7', 'confidence': 0.85},
            {'site_ref': 'temp_a-r1-c1-l1', 'state': 'UNREADABLE', 'object_class': None,
             'object_ref_read': None, 'confidence': 0.2},
        ])
        before = (Plan.objects.count(), Command.objects.count(), Observation.objects.count(), Audit.objects.count())

        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor.rosy_http.RosyClient.site_recognition', return_value=snap):
            inv = self.client.get('/inventory/')
            alerts = self.client.get('/alerts/')

        self.assertEqual(inv.status_code, 200)
        self.assertEqual(alerts.status_code, 200)
        after = (Plan.objects.count(), Command.objects.count(), Observation.objects.count(), Audit.objects.count())
        self.assertEqual(before, after)

        inv_html = inv.content.decode('utf-8')
        self.assertContains(inv, 'ROSY 인식 · 14:02 · omx_overhead · 37자리 중 5자리')
        self.assertContains(inv, '일치 1자리 · 불일치 4건')
        self.assertContains(inv, 'href="/field/"')
        self.assertContains(inv, '현장 확인')
        self.assertContains(inv, 'yard-r1-c1-l2')
        self.assertContains(inv, 'ROSY는 비어 있다고 봄 · 장부엔 C-3')
        self.assertContains(inv, 'yard-r1-c4-l1')
        self.assertContains(inv, 'ROSY는 C-9로 읽음 · 장부엔 C-4')
        self.assertContains(inv, 'yard-r2-c1-l1')
        self.assertContains(inv, 'ROSY는 C-7 있음으로 봄 · 장부는 비어 있음')
        self.assertContains(inv, 'temp_a-r1-c1-l1')
        self.assertContains(inv, 'ROSY가 읽지 못함 · 장부는 비어 있음')
        # Matched site must not appear in mismatch list.
        panel_html = inv_html.split('id="site-recognition"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('yard-r1-c1-l1', panel_html)
        self.assertNotIn('<form', panel_html)
        self.assertIsNone(re.search(r'\sstyle=', inv_html))
        _assert_glossary_clean(self, inv_html)

        # Alerts page lists 4 인식 불일치 items and matches status strip count.
        self.assertEqual(ATTENTION_KIND['인식 불일치'], '인식 불일치')
        recog_alerts = [a for a in alerts.context['alerts'] if a['kind'] == '인식 불일치']
        self.assertEqual(len(recog_alerts), 4)
        self.assertEqual(sum(a['count'] for a in recog_alerts), 4)
        self.assertEqual(alerts.context['alert_total'], 4)
        self.assertEqual(alerts.context['status_strip']['alert_count'], 4)
        self.assertContains(alerts, '인식 불일치 1건')
        self.assertContains(alerts, 'ROSY는 비어 있다고 봄 · 장부엔 C-3')
        for item in recog_alerts:
            self.assertEqual(self.client.get(item['href']).status_code, 200)
        _assert_glossary_clean(self, alerts.content.decode('utf-8'))

        # Viewer gets a safe 200 link on /alerts/.
        self.client.force_login(self.viewer)
        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor.rosy_http.RosyClient.site_recognition', return_value=snap):
            viewer_alerts = self.client.get('/alerts/')
        viewer_recog = [a for a in viewer_alerts.context['alerts'] if a['kind'] == '인식 불일치']
        self.assertEqual(len(viewer_recog), 4)
        for item in viewer_recog:
            self.assertEqual(item['href'], '/inventory/#site-recognition')
            self.assertEqual(self.client.get(item['href']).status_code, 200)

    def test_rosy_timeout_returns_200_with_status_message_and_2s_timeout(self):
        seen_timeouts = []
        real_fleet_client = __import__('harbor_web.fleet_connection', fromlist=['fleet_client']).fleet_client

        def capture_client(config, token, *, timeout=10, verify_identity=False):
            seen_timeouts.append(timeout)
            return real_fleet_client(config, token, timeout=timeout, verify_identity=verify_identity)

        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor_web.fleet_connection.fleet_client', side_effect=capture_client), \
                patch('harbor.rosy_http.RosyClient.site_recognition', side_effect=TimeoutError('timed out')):
            inv = self.client.get('/inventory/')
            alerts = self.client.get('/alerts/')
        self.assertEqual(inv.status_code, 200)
        self.assertEqual(alerts.status_code, 200)
        self.assertEqual(seen_timeouts, [2])
        self.assertContains(inv, 'ROSY 인식 연결 오류')
        self.assertEqual([a for a in alerts.context['alerts'] if a['kind'] == '인식 불일치'], [])


class SiteRecognitionRosyAccountCredentialTests(SiteRecognitionPanelTests):
    """ROSY account mode reads recognition with the viewer's own token, never the service token (ADR 0037)."""

    def setUp(self):
        super().setUp()
        import time
        override = self.settings(ROSY_ACCOUNT=dict(
            issuer='https://hub.example', client_id='harbor', resource='site:test', callback='https://harbor.example/cb'))
        override.enable()
        self.addCleanup(override.disable)
        self.now = time.time()

    def _sign_in(self):
        session = self.client.session
        session['rosy_account'] = {'access': 'user-access-jwt', 'refresh': 'r', 'exp': self.now + 300,
                                   'sub': 'acc-1', 'checked': self.now}
        session.save()

    def test_signed_in_viewer_token_used_local_account_falls_back_like_topview(self):
        snap = _snapshot([
            {'site_ref': 'yard-r1-c1-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
             'object_ref_read': 'C-1', 'confidence': 0.95},
        ])
        with patch.dict(os.environ, self._fleet_env()), \
                patch('harbor_web.fleet_connection.fleet_client') as make_client:
            make_client.return_value.site_recognition.return_value = snap
            resp = self.client.get('/inventory/')
            self.assertEqual(resp.status_code, 200)

            clear_site_recognition_cache()
            self._sign_in()
            resp = self.client.get('/inventory/')
        self.assertEqual(resp.status_code, 200)
        # Local Harbor account (no ROSY session) uses the service token, same as topview_proxy._fleet_settings.
        self.assertEqual([call.args[1] for call in make_client.call_args_list],
                         ['secret-service-token', 'user-access-jwt'])
        self.assertNotContains(resp, 'user-access-jwt')
        self.assertNotContains(resp, 'secret-service-token')

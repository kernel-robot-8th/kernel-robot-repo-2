"""탑뷰 프록시의 역할·출처 확인과 자격 비노출 시험."""
import os
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from harbor.rosy_vision import (
    MAP_OVERLAY_RECTIFICATION,
    MAP_RECTIFICATION,
    PlaneUnavailableError,
    RectificationUnsupportedError,
    StaleFrameError,
)
from harbor_web.topview_proxy import clear_topview_cache


class TopviewProxyTests(TestCase):
    def setUp(self):
        clear_topview_cache()
        self.addCleanup(clear_topview_cache)
        self.user = User.objects.create_user('topview-reader')
        self.user.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(self.user)
        self.url = reverse('topview_frame', args=['test-cam'])
        self.env = patch.dict(os.environ, {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'test-fleet-secret',
            'HARBOR_FLEET_CA_FILE': '',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.vision = patch('harbor_web.topview_proxy.VisionClient')
        self.camera = self.vision.start().return_value
        self.addCleanup(self.vision.stop)
        self.camera.sources.return_value = {'sources': ['test-cam']}
        self.camera.lease.return_value = {
            'source_id': 'test-cam', 'lease': 'test-lease-secret',
            'frame_path': '/api/vision/sources/test-cam/frame'}
        self.camera.fetch_frame.return_value = (b'jpeg-test', {
            'X-Frame-Seq': '42', 'Authorization': 'test-lease-secret',
            'Set-Cookie': 'test-lease-secret', 'X-Frame-Untrusted': 'test-lease-secret'})

    def test_frame_and_only_selected_headers_are_returned(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'jpeg-test')
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Frame-Seq'], '42')
        self.assertNotIn('X-Frame-Untrusted', response)
        self.assertNotIn('test-lease-secret', str(dict(response.headers)))
        self.assertNotIn(b'test-lease-secret', response.content)

    def test_login_and_read_roles_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        for role, status in (('viewer', 200), ('operator', 200), ('admin', 200),
                             ('field', 200), (None, 403)):
            with self.subTest(role=role):
                self.user.groups.clear()
                if role is not None:
                    self.user.groups.add(Group.objects.get_or_create(name=role)[0])
                self.assertEqual(self.client.get(self.url).status_code, status)

    def test_only_get_is_allowed(self):
        for method in ('post', 'put', 'delete', 'head'):
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)

    def test_invalid_frame_header_becomes_unavailable(self):
        self.camera.fetch_frame.return_value = (b'jpeg-test', {'X-Frame-Seq': '42\r\n folded'})
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 502)

    def test_missing_settings(self):
        for name, text in (('HARBOR_FLEET_ORIGIN', 'ROSY 연동 전'), ('HARBOR_FLEET_TOKEN', 'ROSY 로그인 필요')):
            with self.subTest(name=name), patch.dict(os.environ, {name: ''}):
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 503)
                self.assertContains(response, text, status_code=503)

    def test_db_fleet_connection_serves_origin_and_allow_list_gates_it(self):
        """탑뷰는 DB에 저장된 Fleet 연결(출처·CA 파일)을 쓰고, 허용 목록 밖 출처는 연동 전으로 닫는다."""
        from harbor_web.runtime_models import FleetConnection
        FleetConnection.objects.update_or_create(
            pk=1, defaults={'origin': 'https://fleet.example', 'principal': 'svc', 'version': 4})
        with patch.dict(os.environ, {'HARBOR_FLEET_ORIGIN': '', 'HARBOR_FLEET_CA_FILE': ''}):
            blocked = self.client.get(self.url)
            self.assertEqual((blocked.status_code, blocked.content.decode()), (503, 'ROSY 연동 전'))
            with patch.dict(os.environ, {'HARBOR_FLEET_ALLOWED_ORIGINS': 'https://fleet.example'}):
                allowed = self.client.get(self.url)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.content, b'jpeg-test')

    def test_unknown_camera_is_rejected_before_leasing(self):
        response = self.client.get(reverse('topview_frame', args=['unknown']))
        self.assertEqual(response.status_code, 404)
        self.camera.lease.assert_not_called()

    def test_unusable_peer_source_does_not_block_known_camera(self):
        self.camera.sources.return_value = {'sources': [None, '', 'bad/source', '.', '..', 'test-cam']}
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'jpeg-test')

    def test_vision_only_source_is_allowed_and_unlisted_source_is_rejected(self):
        self.camera.sources.return_value = {'sources': ['test-cam', 'ceiling_north']}
        self.camera.lease.return_value = {
            'source_id': 'ceiling_north', 'lease': 'test-lease-secret',
            'frame_path': '/api/vision/sources/ceiling_north/frame'}
        allowed = self.client.get(reverse('topview_frame', args=['ceiling_north']))
        self.assertEqual((allowed.status_code, allowed.content), (200, b'jpeg-test'))
        self.camera.lease.assert_called_once_with('ceiling_north', rectification=MAP_OVERLAY_RECTIFICATION)

        self.camera.lease.reset_mock()
        rejected = self.client.get(reverse('topview_frame', args=['unapproved-cam']))
        self.assertEqual(rejected.status_code, 404)
        self.camera.lease.assert_not_called()

    def test_falls_back_from_map_overlay_to_map_and_raw_and_supports_raw_query(self):
        self.camera.lease.side_effect = [
            RectificationUnsupportedError('422'),
            {'source_id': 'test-cam', 'lease': 'map-lease-secret',
             'frame_path': '/api/vision/sources/test-cam/frame'},
            {'source_id': 'test-cam', 'lease': 'raw-lease-secret',
             'frame_path': '/api/vision/sources/test-cam/frame'},
            {'source_id': 'test-cam', 'lease': 'explicit-raw-secret',
             'frame_path': '/api/vision/sources/test-cam/frame'},
        ]
        self.camera.fetch_frame.side_effect = [
            PlaneUnavailableError('map plane unavailable'),
            (b'jpeg-raw-fallback', {'X-Frame-Seq': '43', 'X-Frame-Rectified': 'false'}),
            (b'jpeg-explicit-raw', {'X-Frame-Seq': '44', 'X-Frame-Rectified': 'false'}),
        ]
        response = self.client.get(self.url)
        self.assertEqual((response.status_code, response.content), (200, b'jpeg-raw-fallback'))
        self.assertEqual(response['X-Frame-Rectified'], 'false')

        raw_response = self.client.get(self.url + '?view=raw')
        self.assertEqual((raw_response.status_code, raw_response.content), (200, b'jpeg-explicit-raw'))
        self.assertEqual(
            [call.kwargs for call in self.camera.lease.call_args_list],
            [{'rectification': MAP_OVERLAY_RECTIFICATION}, {'rectification': MAP_RECTIFICATION}, {}, {}],
        )
        self.assertNotIn('map-lease-secret', str(dict(response.headers)))
        self.assertNotIn('raw-lease-secret', str(dict(response.headers)))

    def test_stale_and_network_errors_do_not_expose_credentials(self):
        for error, status in ((StaleFrameError('test-lease-secret'), 404),
                              (OSError('test-lease-secret'), 502),
                              (ValueError('test-lease-secret'), 502)):
            with self.subTest(error=type(error).__name__):
                self.camera.fetch_frame.side_effect = error
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status)
                self.assertNotIn(b'test-lease-secret', response.content)
                self.assertNotIn('test-lease-secret', str(dict(response.headers)))


class TopviewUserTokenTests(TestCase):
    """ADR 0037: a person signed in with Rosy reads the topview with their own Rosy access token."""

    def setUp(self):
        import time
        clear_topview_cache()
        self.addCleanup(clear_topview_cache)
        self.user = User.objects.create_user('rosy-acc-1')
        self.user.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(self.user)
        self.settings_override = self.settings(ROSY_ACCOUNT=dict(
            issuer='https://hub.example', client_id='harbor', resource='site:test', callback='https://harbor.example/cb'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.now = time.time()
        self.env = patch.dict(os.environ, {'HARBOR_FLEET_ORIGIN': 'https://fleet.example', 'HARBOR_FLEET_TOKEN': '',
                                           'HARBOR_FLEET_CA_FILE': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        vision = patch('harbor_web.topview_proxy.VisionClient')
        self.vision_class = vision.start()
        self.addCleanup(vision.stop)
        camera = self.vision_class.return_value
        camera.sources.return_value = {'sources': ['test-cam']}
        camera.lease.return_value = {'source_id': 'test-cam', 'lease': 'l', 'frame_path': '/api/vision/sources/test-cam/frame'}
        camera.fetch_frame.return_value = (b'jpeg-user', {})
        self.url = reverse('topview_frame', args=['test-cam'])

    def sign_in(self, exp):
        session = self.client.session
        session['rosy_account'] = {'access': 'user-access-jwt', 'refresh': 'r', 'exp': exp, 'sub': 'acc-1',
                                   'checked': self.now}
        session.save()

    def test_the_signed_in_persons_token_reads_the_frame(self):
        self.sign_in(self.now + 300)
        response = self.client.get(self.url)
        self.assertEqual((response.status_code, response.content), (200, b'jpeg-user'))
        self.assertEqual([call.args[1] for call in self.vision_class.call_args_list],
                         ['user-access-jwt', 'user-access-jwt'])
        self.assertNotIn('user-access-jwt', str(dict(response.headers)))

    def test_an_expired_token_or_no_rosy_session_asks_to_sign_in(self):
        self.sign_in(self.now - 1)
        response = self.client.get(self.url)
        self.assertEqual((response.status_code, response.content.decode()), (503, 'ROSY 로그인 필요'))
        self.vision_class.assert_not_called()

    def test_expired_rosy_session_does_not_fall_back_to_service_token(self):
        self.sign_in(self.now - 1)
        with patch.dict(os.environ, {'HARBOR_FLEET_TOKEN': 'service-token'}):
            response = self.client.get(self.url)
        self.assertEqual((response.status_code, response.content.decode()), (503, 'ROSY 로그인 필요'))
        self.vision_class.assert_not_called()

    def test_local_accounts_keep_the_service_token(self):
        with patch.dict(os.environ, {'HARBOR_FLEET_TOKEN': 'service-token'}):
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.vision_class.call_args.args[1], 'service-token')

    def test_topview_caches_sources_and_lease_and_retries_expired_401(self):
        from urllib.error import HTTPError
        with patch.dict(os.environ, {'HARBOR_FLEET_TOKEN': 'service-token'}):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertEqual(self.client.get(self.url).status_code, 200)
            camera = self.vision_class.return_value
            self.assertEqual(camera.sources.call_count, 1)
            self.assertEqual(camera.lease.call_count, 1)

            camera.fetch_frame.side_effect = [
                HTTPError('https://fleet.example/frame', 401, 'expired', {}, None),
                (b'jpeg-refreshed', {}),
            ]
            refreshed = self.client.get(self.url)
        self.assertEqual((refreshed.status_code, refreshed.content), (200, b'jpeg-refreshed'))
        self.assertEqual(camera.lease.call_count, 2)

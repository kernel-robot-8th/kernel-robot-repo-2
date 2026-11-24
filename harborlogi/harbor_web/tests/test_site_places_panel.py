"""Tests for /inventory/ ROSY port site-places panel and topview 180-degree orientation hint."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase

from harbor.rosy_http import RosyClient
from harbor_web.tests.test_glossary_sweep import FORBIDDEN, _visible_and_summary_text

CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'site-places.example.json'


def _assert_glossary_clean(testcase, html):
    parts = _visible_and_summary_text(html)
    for item in (parts if isinstance(parts, (tuple, list)) else (parts,)):
        text = item if isinstance(item, str) else ' '.join(item)
        testcase.assertIsNone(FORBIDDEN.search(text))


class SitePlacesPanelTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user('places-viewer')
        self.viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(self.viewer)

    def test_rosy_client_calls_port_view_endpoint(self):
        calls = []

        def fake_transport(method, path, body):
            calls.append((method, path, body))
            return {'schema': 'rosy.port-site-places.v1'}

        client = RosyClient(transport=fake_transport)
        self.assertEqual(client.site_places(), {'schema': 'rosy.port-site-places.v1'})
        self.assertEqual(calls, [('GET', '/api/fleet/site-points?view=port', None)])

    def test_inventory_shows_offline_status_and_topview_180_note_without_fleet_env(self):
        with patch.dict(os.environ, {'HARBOR_FLEET_ORIGIN': '', 'HARBOR_FLEET_TOKEN': ''}):
            response = self.client.get('/inventory/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertContains(response, '탑뷰를 180° 돌려 야드 평면과 같은 방향으로 보여 줍니다')
        self.assertContains(response, 'data-site-places-status>ROSY 연동 전')
        _assert_glossary_clean(self, html)

    def test_inventory_renders_valid_port_site_places_and_passes_glossary_sweep(self):
        payload = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        with patch.dict(os.environ, {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_ALLOWED_ORIGINS': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'token-for-test',
        }), patch('harbor.rosy_http.RosyClient.site_places', return_value=payload):
            response = self.client.get('/inventory/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertContains(response, 'ROSY 위치 체계')
        self.assertContains(response, 'S_OUT')
        self.assertContains(response, 'S3')
        self.assertContains(response, 'belt')
        self.assertContains(response, '보류 (INTAKE_ZONE_UNVERIFIED)')
        self.assertContains(response, '보류 (OUTBOUND_ZONE_UNVERIFIED)')
        _assert_glossary_clean(self, html)

    def test_inventory_fails_closed_when_rosy_payload_leaks_geometry(self):
        bad = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        bad_with_xy = copy.deepcopy(bad)
        bad_with_xy['vehicle_places'][0]['x'] = 1.25
        with patch.dict(os.environ, {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_ALLOWED_ORIGINS': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'token-for-test',
        }), patch('harbor.rosy_http.RosyClient.site_places', return_value=bad_with_xy):
            response = self.client.get('/inventory/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ROSY 위치 계약 검증 실패')
        self.assertNotContains(response, '1.25')

"""Django tests for the Yard page Camera <-> 3D Position Mapping panel, json_script data, and read-only safety."""
import json
import os
import re
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from harbor.rosy_vision import MAP_RECTIFICATION
from harbor_web import services
from harbor_web.models import (
    Audit,
    Cargo,
    CargoFootprint,
    CellPlanDispatch,
    Command,
    Condition,
    FieldEpisode,
    Observation,
    Order,
    Plan,
)
from harbor_web.site_recognition_view import clear_site_recognition_cache
from harbor_web.tests.test_control_board import site_policy
from harbor_web.topview_proxy import clear_topview_cache


class YardCameraMapTests(TestCase):
    def setUp(self):
        clear_topview_cache()
        clear_site_recognition_cache()
        self.addCleanup(clear_topview_cache)
        self.addCleanup(clear_site_recognition_cache)

        self.admin = User.objects.create_user('cm-admin')
        self.operator = User.objects.create_user('cm-operator')
        self.field = User.objects.create_user('cm-field')
        self.admin.groups.add(*[Group.objects.get_or_create(name=n)[0] for n in ('admin', 'operator', 'viewer')])
        self.operator.groups.add(*[Group.objects.get_or_create(name=n)[0] for n in ('operator', 'viewer')])
        self.field.groups.add(Group.objects.get_or_create(name='field')[0])

        self.condition = services.register_condition(
            self.admin, name='3x6 camera-map site', calibration_ref='synthetic', payload=site_policy(),
        )
        placements = []
        for code, row, col, level, color, tier in (
            ('C-1', 1, 1, 1, 'pink', 1),
            ('C-2', 1, 1, 2, 'pink', 3),
            ('C-10', 2, 1, 1, 'orange', 2),
            ('C-14', 3, 1, 1, 'sky', 2),
        ):
            services.register_cargo(
                self.operator, code=code, name=code, weight_kg=2, volume_m3='0.012',
                destination='부산', color=color, color_confirmed=True, shipper_tier=tier, length_cells=3,
            )
            fp = CargoFootprint.objects.filter(cargo__code=code).latest('pk')
            placements.append(dict(
                cargo_id=code, area_id='yard', row=row, column=col, level=level,
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

        self.env = patch.dict(os.environ, {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
            'HARBOR_FLEET_TOKEN': 'test-fleet-secret',
            'HARBOR_FLEET_CA_FILE': '',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.vision_patch = patch('harbor_web.topview_proxy.VisionClient')
        self.vision = self.vision_patch.start().return_value
        self.addCleanup(self.vision_patch.stop)
        self.vision.sources.return_value = {
            'sources': ['omx_overhead', 'omx_side', 'omx_arm_s_top', 'ceiling_north'],
        }

        self.recog_patch = patch('harbor.rosy_http.RosyClient.site_recognition', return_value={
            'schema': 'rosy.cell-site-recognition.v1',
            'snapshot_id': 'snap-cm-1',
            'observed_at': '2026-11-26T05:00:00Z',
            'source_id': 'omx_overhead',
            'calibration_revision': 'cal-1',
            'site_map_revision': 1,
            'sites': [
                {'site_ref': 'yard-r1-c1-l1', 'state': 'OCCUPIED', 'object_class': 'pink',
                 'object_ref_read': 'C-1', 'confidence': 0.95},
                {'site_ref': 'yard-r1-c1-l2', 'state': 'EMPTY', 'object_class': None,
                 'object_ref_read': None, 'confidence': 0.88},
                {'site_ref': 'yard-r1-c4-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
                 'object_ref_read': 'C-9', 'confidence': 0.91},
            ],
        })
        self.recog_patch.start()
        self.addCleanup(self.recog_patch.stop)

    def _extract_json_script(self, html: str, script_id: str) -> dict:
        match = re.search(
            rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, f'missing json_script {script_id}')
        return json.loads(match.group(1))

    def _db_counts(self):
        return (
            Cargo.objects.count(),
            Order.objects.count(),
            Plan.objects.count(),
            Command.objects.count(),
            Observation.objects.count(),
            Audit.objects.count(),
            Condition.objects.count(),
            CellPlanDispatch.objects.count(),
            FieldEpisode.objects.count(),
        )

    def test_inventory_replaces_topview_with_camera_map_and_keeps_overview_topview(self):
        before = self._db_counts()
        resp = self.client.get('/inventory/')
        after = self._db_counts()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(before, after, 'GET /inventory/ must never mutate DB rows')

        html = resp.content.decode('utf-8')
        self.assertTemplateUsed(resp, 'camera_map_panel.html')
        self.assertTemplateNotUsed(resp, 'topview_panel.html')
        self.assertContains(resp, '카메라 ↔ 3D 위치 매핑', count=1)
        self.assertContains(resp, 'id="camera-map"', count=1)
        self.assertContains(resp, 'data-camera-map-source="camera-map-data"', count=1)
        self.assertNotContains(resp, 'ROSY 탑뷰')
        self.assertNotContains(resp, 'topview.mjs')
        self.assertNotContains(resp, 'test-fleet-secret')
        self.assertIsNone(re.search(r'\sstyle=', html))
        self.assertNotIn('<script>', html)

        # Overview page still uses topview_panel.html untouched.
        overview_resp = self.client.get('/')
        self.assertEqual(overview_resp.status_code, 200)
        self.assertTemplateUsed(overview_resp, 'topview_panel.html')
        self.assertContains(overview_resp, 'ROSY 탑뷰', count=1)

    def test_camera_map_json_script_contains_cells_colors_urls_and_configs(self):
        resp = self.client.get('/inventory/')
        self.assertEqual(resp.status_code, 200)
        payload = self._extract_json_script(resp.content.decode('utf-8'), 'camera-map-data')

        self.assertEqual(payload['layout']['schema'], 'harbor.site-3d-layout.v1')
        self.assertEqual(payload['layout']['status'], 'planned_unverified')
        self.assertEqual(set(payload['layout']['plates'].keys()), {'yard', 'temp_a', 'temp_b', 'stand'})

        containers = payload['containers']
        self.assertEqual(containers['yard-r1-c1-l1']['code'], 'C-1')
        self.assertEqual(containers['yard-r1-c1-l1']['color'], 'pink')
        self.assertEqual(containers['yard-r1-c1-l1']['priority_tier'], 'urgent')
        self.assertEqual(containers['yard-r2-c1-l1']['color'], 'orange')
        self.assertEqual(containers['yard-r3-c1-l1']['color'], 'sky')

        cams = {c['key']: c for c in payload['cameras']}
        self.assertEqual(cams['top']['url'], '/topview/omx_overhead/frame/?view=raw')
        self.assertEqual(cams['side']['url'], '/topview/omx_side/frame/?view=raw')
        self.assertEqual(cams['arms']['url'], '/topview/omx_arm_s_top/frame/?view=raw')
        self.assertEqual(cams['bird']['url'], '/topview/ceiling_north/frame/?view=map')

        self.assertEqual(payload['overlays']['top']['source_id'], 'omx_overhead')
        top_by_site = {p['site']: p for p in payload['overlays']['top']['points']}
        self.assertEqual(len(top_by_site['temp_a:r1-s1']['polygon']), 4)
        self.assertEqual(len(top_by_site['temp_b:r3-s1']['polygon']), 4)
        self.assertEqual(payload['overlays']['side']['source_id'], 'omx_side')
        self.assertEqual(len(payload['overlays']['side']['points']), 9)
        self.assertTrue(all(p['zone'] == 'yard' for p in payload['overlays']['side']['points']))
        self.assertEqual(payload['overlays']['arms']['source_id'], 'omx_arm_s_top')

    def test_recognition_mismatches_include_camera_check_button_with_site_ref(self):
        resp = self.client.get('/inventory/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-camera-map-focus="yard-r1-c1-l2"')
        self.assertContains(resp, 'data-camera-map-focus="yard-r1-c4-l1"')
        self.assertContains(resp, '카메라로 확인', count=2)
        # Matched site must not get a mismatch button.
        self.assertNotContains(resp, 'data-camera-map-focus="yard-r1-c1-l1"')

    def test_proxy_view_map_requests_map_rectification_directly(self):
        self.vision.lease.return_value = {
            'source_id': 'ceiling_north',
            'lease': 'map-only-lease',
            'frame_path': '/api/vision/sources/ceiling_north/frame',
        }
        self.vision.fetch_frame.return_value = (
            b'jpeg-map',
            {'X-Frame-Rectified': 'map', 'X-Frame-Plane': '-1.37,-0.76,1.37,0.76,400.0000'},
        )
        url = reverse('topview_frame', args=['ceiling_north']) + '?view=map'
        resp = self.client.get(url)
        self.assertEqual((resp.status_code, resp.content), (200, b'jpeg-map'))
        self.assertEqual(resp['X-Frame-Rectified'], 'map')
        self.assertEqual(resp['X-Frame-Plane'], '-1.37,-0.76,1.37,0.76,400.0000')
        self.vision.lease.assert_called_once_with('ceiling_north', rectification=MAP_RECTIFICATION)

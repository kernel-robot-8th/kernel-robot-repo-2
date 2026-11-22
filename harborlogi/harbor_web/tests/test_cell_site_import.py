"""Integration tests for ROSY cell-site map import preview, save, audit, security, and planner gates."""
import json
import os
import re
import socket
import time
from datetime import timedelta
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from harbor.cell_site_map import MAX_DOCUMENT_BYTES, SCHEMA_ID
from harbor.model import Bay, OutboundRequest, YardSnapshot
from harbor.outbound import plan_outbound
from harbor.rosy_http import RosyClient
from harbor_web import map_pages, rearrangement, services, site_config
from harbor_web.map_config import build_map, editor_draft
from harbor_web.models import Audit, CargoFootprint, Condition, Observation
from harbor_web.tests import test_services as fixtures

GLOSSARY_FORBIDDEN = re.compile(r'관측|원본|인계|대조|회차|#\d+|\bREADY\b|\bHOLD\b')


def _canonical_doc():
    yard_slots = []
    for row in (1, 2, 3):
        for slot_num, start_col, hold_id in ((1, 1, 2), (2, 4, 1)):
            yard_slots.append({
                'id': f'yard:r{row}-s{slot_num}',
                'label': f'{row}행 {start_col}~{start_col + 2}열',
                'row': row,
                'slot': slot_num,
                'columns': [start_col, start_col + 2],
                'hold': slot_num,
                'arms': ['arm_a', 'arm_b'],
                'tiers': [
                    {'tier': t, 'id': f'yard:r{row}-s{slot_num}-t{t}', 'item': f'ship-h{hold_id}r{row - 1}-l{t - 1}'}
                    for t in (1, 2, 3)
                ],
                'status': 'planned_unverified',
            })

    def _temp_slots(area_id, arm_id, prefix):
        return [
            {
                'id': f'{area_id}:r{row}-s1',
                'label': f'{prefix} {row}',
                'row': row,
                'slot': 1,
                'columns': [1, 3],
                'arms': [arm_id],
                'tiers': [
                    {'tier': t, 'id': f'{area_id}:r{row}-s1-t{t}', 'item': f'{prefix}-{row}' if t == 1 else f'{prefix}-{row}-l{t - 1}'}
                    for t in (1, 2, 3)
                ],
                'status': 'planned_unverified',
            }
            for row in (1, 2, 3)
        ]

    return {
        'schema': SCHEMA_ID,
        'revision': 1,
        'status': 'planned_unverified',
        'tier_height_mm': 32.4,
        'zones': [
            {
                'id': 'yard', 'label': '장치 야드', 'role': '보관',
                'grid': {'rows': 3, 'columns': 6, 'tiers': 3}, 'slot_cells': 3,
                'arms': ['arm_a', 'arm_b'], 'status': 'planned_unverified', 'slots': yard_slots,
            },
            {
                'id': 'temp_a', 'label': '임시 장치 A', 'role': '임시',
                'grid': {'rows': 3, 'columns': 3, 'tiers': 3}, 'slot_cells': 3,
                'arms': ['arm_a'], 'status': 'planned_unverified', 'slots': _temp_slots('temp_a', 'arm_a', 'tmp-a'),
            },
            {
                'id': 'temp_b', 'label': '임시 장치 B', 'role': '임시',
                'grid': {'rows': 3, 'columns': 3, 'tiers': 3}, 'slot_cells': 3,
                'arms': ['arm_b'], 'status': 'planned_unverified', 'slots': _temp_slots('temp_b', 'arm_b', 'tmp-b'),
            },
            {
                'id': 'stand', 'label': '반출대', 'role': '반출',
                'grid': {'rows': 1, 'columns': 3, 'tiers': 1}, 'slot_cells': 3,
                'arms': ['arm_a'], 'status': 'planned_unverified',
                'slots': [{
                    'id': 'stand:r1-s1', 'label': '반출대', 'row': 1, 'slot': 1, 'columns': [1, 3],
                    'arms': ['arm_a'], 'tiers': [{'tier': 1, 'id': 'stand:r1-s1-t1', 'item': 'stand'}],
                    'status': 'planned_unverified',
                }],
            },
        ],
        'not_places': [
            {'id': 'belt', 'label': '공급 경로'},
            {'id': 'supply_s', 'label': '공급 작업대 S'},
            {'id': 'ship_stern', 'label': '선미'},
            {'id': 'ship_bow', 'label': '선수'},
            {'id': 'vehicle_tray', 'label': '이송 차량 짐칸'},
        ],
    }


def _fleet_docs_from_canonical(canonical):
    sites = []
    zones = []
    for z in canonical['zones']:
        zones.append({
            'id': z['id'], 'label': z['label'], 'role': z['role'], 'grid': dict(z['grid']),
            'slot_cells': z['slot_cells'], 'arms': list(z['arms']), 'status': z['status'],
        })
        for s in z['slots']:
            for t in s['tiers']:
                sites.append({
                    'site_id': t['id'], 'slot_id': s['id'], 'sim_site': t['item'],
                    'base_site': t['item'].rsplit('-l', 1)[0],
                    'wire_ref': f"{z['id']}-r{s['row']}-c{s['columns'][0]}-l{t['tier']}",
                    'area': z['id'], 'row': s['row'], 'slot': s['slot'], 'col': int(s['row']),
                    'zone_row': int(s['slot']), 'tier': t['tier'], 'arms': list(s['arms']), 'status': s['status'],
                })
    with_zones = {
        'schema': canonical['schema'], 'revision': canonical['revision'], 'status': canonical['status'],
        'tier_height_mm': canonical['tier_height_mm'], 'count': len(sites),
        'zones': zones, 'sites': sites, 'not_places': list(canonical['not_places']),
    }
    legacy = {k: v for k, v in with_zones.items() if k != 'zones'}
    return with_zones, legacy


def _extract_token(html: str) -> str:
    match = re.search(r'name="import_token"\s+value="([^"]+)"', html)
    assert match, 'import_token hidden input not found'
    return match.group(1)


class CellSiteImportTests(TestCase):
    setUp = fixtures.ServicesTests.setUp
    cargo = fixtures.ServicesTests.cargo
    order = fixtures.ServicesTests.order

    def _upload(self, content: bytes, name='cell-site-map.json'):
        return SimpleUploadedFile(name, content, content_type='application/json')

    def test_role_and_csrf_enforced_and_settings_has_link(self):
        self.assertEqual(self.client.get('/settings/maps/import/').status_code, 302)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get('/settings/maps/import/').status_code, 403)
        self.assertEqual(self.client.post('/settings/maps/import/', {'source': 'file'}).status_code, 403)
        self.assertEqual(self.client.post('/settings/maps/import/save/', {'import_token': 'x'}).status_code, 403)

        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.admin)
        self.assertEqual(secured.post('/settings/maps/import/', {'source': 'file'}).status_code, 403)
        self.assertEqual(secured.post('/settings/maps/import/save/', {'import_token': 'x'}).status_code, 403)

        self.client.force_login(self.admin)
        settings_resp = self.client.get('/settings/')
        self.assertEqual(settings_resp.status_code, 200)
        self.assertContains(settings_resp, 'href="/settings/maps/import/"')

        import_resp = self.client.get('/settings/maps/import/')
        self.assertEqual(import_resp.status_code, 200)
        self.assertTemplateUsed(import_resp, 'cell_site_import.html')
        html = import_resp.content.decode()
        self.assertNotIn('<script>', html.split('</head>', 1)[1])
        visible = re.sub(r'<details\b[^>]*>.*?</details>', '', html, flags=re.S)
        self.assertNotRegex(visible, GLOSSARY_FORBIDDEN)

    def test_file_preview_writes_nothing_and_save_registers_immutable_condition_and_audit(self):
        self.client.force_login(self.admin)
        raw = json.dumps(_canonical_doc(), ensure_ascii=False).encode('utf-8')
        preview = self.client.post('/settings/maps/import/', {'source': 'file', 'map_file': self._upload(raw)})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(Condition.objects.count(), 0)
        self.assertEqual(Audit.objects.count(), 0)
        html = preview.content.decode()
        self.assertContains(preview, '임시 장치 A')
        self.assertContains(preview, 'yard-r1-c4-l2')
        visible = re.sub(r'<details\b[^>]*>.*?</details>', '', html, flags=re.S)
        self.assertNotRegex(visible, GLOSSARY_FORBIDDEN)

        token = _extract_token(html)
        saved = self.client.post('/settings/maps/import/save/', {
            'import_token': token,
            'name': 'ROSY 셀 맵 rev1 (planned_unverified)',
            'calibration_ref': 'rosy.cell-site-map.v1#rev1 (planned_unverified)',
        })
        condition = Condition.objects.get()
        self.assertRedirects(saved, f'/inventory/?condition={condition.pk}', fetch_redirect_response=False)

        self.assertEqual(condition.payload['arm_reach'], [])
        self.assertEqual(condition.payload['allowed_moves'], [])
        self.assertEqual(condition.payload['spatial_moves'], [])
        self.assertEqual(condition.payload['slot_cells'], 3)
        self.assertEqual(condition.payload['max_observation_age_seconds'], 60)
        self.assertEqual(len(condition.payload['yard_layout']['cells']), 18)
        self.assertEqual(condition.payload['area_presentation']['temp_a']['label'], '임시 장치 A')
        self.assertEqual(condition.payload['area_presentation']['yard']['x'], 0)
        self.assertEqual(condition.payload['area_presentation']['stand']['x'], 7.2)

        audit = Audit.objects.get(action='condition.import.register', object_id=str(condition.pk))
        self.assertEqual(audit.detail['source'], 'file')
        self.assertEqual(audit.detail['revision'], 1)
        self.assertEqual(audit.detail['site_count'], 37)
        self.assertEqual(audit.detail['zone_arms'], {'yard': ['A', 'B'], 'temp_a': ['A'], 'temp_b': ['B'], 'stand': ['A']})
        self.assertTrue(Condition.objects.filter(pk=condition.pk).exists())
        self.assertEqual(Audit.objects.filter(action='condition.import.register', detail__revision=1).count(), 1)

        # Duplicate revision warning on next preview
        second_preview = self.client.post('/settings/maps/import/', {'source': 'file', 'map_file': self._upload(raw)})
        self.assertEqual(second_preview.status_code, 200)
        self.assertContains(second_preview, '같은 rev1 맵에서 등록한 설치 기준이 이미 있습니다.')

    def test_invalid_files_are_rejected_without_writes(self):
        self.client.force_login(self.admin)
        bad_uploads = [
            ('pdf', SimpleUploadedFile('map.pdf', b'%PDF-1.4\n', content_type='application/pdf')),
            ('too_big', self._upload(b'{' + b' ' * MAX_DOCUMENT_BYTES + b'}')),
            ('dup_keys', self._upload(b'{"revision": 1, "revision": 2}')),
            ('bad_utf8', self._upload(b'\xff\xfe{}')),
        ]
        for label, fobj in bad_uploads:
            with self.subTest(label=label):
                resp = self.client.post('/settings/maps/import/', {'source': 'file', 'map_file': fobj})
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(Condition.objects.count(), 0)
        self.assertEqual(Audit.objects.count(), 0)

    def test_fleet_import_legacy_and_zones_with_zero_fleet_calls_on_save_and_error_mapping(self):
        canonical = _canonical_doc()
        with_zones, legacy = _fleet_docs_from_canonical(canonical)
        self.client.force_login(self.admin)

        env = {
            'HARBOR_FLEET_ORIGIN': 'https://fleet.example:8443',
            'HARBOR_FLEET_ALLOWED_ORIGINS': 'https://fleet.example:8443',
            'HARBOR_FLEET_TOKEN': 'bridge-token',
        }
        with patch.dict(os.environ, env):
            legacy_client = RosyClient(transport=lambda method, path, body: legacy)
            with patch('harbor_web.fleet_connection.fleet_client', return_value=legacy_client):
                resp = self.client.post('/settings/maps/import/', {'source': 'fleet'})
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, '임시 판 A')
            self.assertContains(resp, '정본 라벨과 다를 수 있음')
            token = _extract_token(resp.content.decode())

            with patch('harbor_web.fleet_connection.fleet_client', side_effect=AssertionError('Save must not call Fleet')):
                saved = self.client.post('/settings/maps/import/save/', {
                    'import_token': token,
                    'name': 'Fleet legacy rev1',
                    'calibration_ref': 'rosy.cell-site-map.v1#rev1 (planned_unverified)',
                })
            self.assertEqual(saved.status_code, 302)
            cond_legacy = Condition.objects.latest('pk')
            self.assertEqual(cond_legacy.payload['area_presentation']['temp_a']['label'], '임시 판 A')
            self.assertEqual(cond_legacy.payload['yard_layout']['columns'], 6)

            zones_client = RosyClient(transport=lambda method, path, body: with_zones)
            with patch('harbor_web.fleet_connection.fleet_client', return_value=zones_client):
                resp2 = self.client.post('/settings/maps/import/', {'source': 'fleet'})
            self.assertEqual(resp2.status_code, 200)
            self.assertContains(resp2, '임시 장치 A')
            self.assertNotContains(resp2, '정본 라벨과 다를 수 있음')

            # Error mappings
            error_cases = [
                ('401', HTTPError('https://fleet.example:8443/x', 401, 'Unauthorized', {}, None), 'FLEET_HTTP_401'),
                ('timeout', URLError(TimeoutError('timed out')), 'FLEET_TIMEOUT'),
                ('dns', URLError(socket.gaierror('unknown host')), 'FLEET_DNS_UNRESOLVED'),
                ('bad_doc', ValueError('DUPLICATE_JSON_KEY'), 'INVALID_FLEET_RESPONSE'),
            ]
            for label, exc, code in error_cases:
                with self.subTest(label=label):
                    bad_client = RosyClient(transport=lambda m, p, b, e=exc: (_ for _ in ()).throw(e))
                    with patch('harbor_web.fleet_connection.fleet_client', return_value=bad_client):
                        err_resp = self.client.post('/settings/maps/import/', {'source': 'fleet'})
                    self.assertEqual(err_resp.status_code, 400)
                    self.assertContains(err_resp, code, status_code=400)

        with patch.dict(os.environ, {'HARBOR_FLEET_ORIGIN': '', 'HARBOR_FLEET_ALLOWED_ORIGINS': '', 'HARBOR_FLEET_TOKEN': ''}):
            err_resp = self.client.post('/settings/maps/import/', {'source': 'fleet'})
            self.assertEqual(err_resp.status_code, 400)
            self.assertContains(err_resp, 'FLEET_ORIGIN_NOT_CONFIGURED', status_code=400)

    def test_token_security_expiry_user_binding_and_audit_rollback(self):
        self.client.force_login(self.admin)
        raw = json.dumps(_canonical_doc()).encode('utf-8')
        preview = self.client.post('/settings/maps/import/', {'source': 'file', 'map_file': self._upload(raw)})
        token = _extract_token(preview.content.decode())

        # Tampered token
        self.assertEqual(self.client.post('/settings/maps/import/save/', {
            'import_token': token + 'x', 'name': 'x', 'calibration_ref': 'y',
        }).status_code, 400)

        # Other admin user's token
        other_admin = self.admin.__class__.objects.create_user('admin2')
        other_admin.groups.add(self.admin.groups.first())
        self.client.force_login(other_admin)
        self.assertEqual(self.client.post('/settings/maps/import/save/', {
            'import_token': token, 'name': 'x', 'calibration_ref': 'y',
        }).status_code, 400)

        # Expired token
        self.client.force_login(self.admin)
        future = time.time() + map_pages.IMPORT_TOKEN_MAX_AGE + 30
        with patch.object(signing.time, 'time', return_value=future):
            expired_resp = self.client.post('/settings/maps/import/save/', {
                'import_token': token, 'name': 'x', 'calibration_ref': 'y',
            })
        self.assertEqual(expired_resp.status_code, 400)
        self.assertEqual(Condition.objects.count(), 0)

        # Audit failure rolls back Condition creation
        orig_create = Audit.objects.create

        def fail_import_audit(**kwargs):
            if kwargs.get('action') == 'condition.import.register':
                raise RuntimeError('audit boom')
            return orig_create(**kwargs)

        with patch.object(Audit.objects, 'create', side_effect=fail_import_audit), self.assertRaises(RuntimeError):
            self.client.post('/settings/maps/import/save/', {
                'import_token': token,
                'name': 'rollback-check',
                'calibration_ref': 'rosy.cell-site-map.v1#rev1 (planned_unverified)',
            })
        self.assertEqual(Condition.objects.count(), 0)
        self.assertEqual(Audit.objects.count(), 0)

    def test_imported_condition_inventory_editor_and_planner_hold_gates(self):
        self.client.force_login(self.admin)
        raw = json.dumps(_canonical_doc()).encode('utf-8')
        preview = self.client.post('/settings/maps/import/', {'source': 'file', 'map_file': self._upload(raw)})
        token = _extract_token(preview.content.decode())
        self.client.post('/settings/maps/import/save/', {
            'import_token': token,
            'name': 'ROSY 셀 맵 rev1',
            'calibration_ref': 'rosy.cell-site-map.v1#rev1 (planned_unverified)',
        })
        condition = Condition.objects.get()

        # 1. Unobserved inventory view renders cleanly
        inv = self.client.get(f'/inventory/?condition={condition.pk}')
        self.assertEqual(inv.status_code, 200)
        self.assertContains(inv, 'ROSY 셀 맵 rev1')

        # 2. Map editor roundtrip preserves empty reach and moves
        draft = editor_draft(condition.payload)
        built = build_map(condition.payload, draft, source_id=condition.pk)
        edit_resp = self.client.post(
            f'/settings/conditions/{condition.pk}/map/',
            json.dumps(dict(
                map=built['map'], fingerprint=built['fingerprint'],
                name='Edited ROSY rev1', calibration_ref='derived-edit',
            )),
            content_type='application/json',
        )
        self.assertEqual(edit_resp.status_code, 200)
        edited = Condition.objects.get(pk=edit_resp.json()['condition_id'])
        self.assertEqual(edited.payload['arm_reach'], [])
        self.assertEqual(edited.payload['allowed_moves'], [])
        self.assertEqual(edited.payload['spatial_moves'], [])

        # 3. Spatial observation + INBOUND / OUTBOUND / REARRANGE all HOLD on ARM_REACH_UNVERIFIED
        c_in = self.cargo('IN3')
        c_out = self.cargo('OUT3')
        fp_out = CargoFootprint.objects.filter(cargo=c_out).latest('pk')
        obs = services.record_observation(
            self.admin, source_id='cam-top', source_revision='1', observed_at=self.now, provenance='SYNTHETIC',
            condition=condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                holdings=[],
                placements=[dict(
                    cargo_id='OUT3', area_id='yard', row=1, column=1, level=1, orientation=0,
                    footprint_revision=fp_out.pk,
                )],
            ),
        )
        inbound_order = self.order(cargo=c_in, kind='INBOUND', priority_group=1, sequence=1)
        p_in = services.make_plan(self.operator, order_id=inbound_order.pk, expected_version=1, observation_id=obs.pk)
        self.assertEqual((p_in.result['status'], p_in.result['reason']), ('HOLD', 'ARM_REACH_UNVERIFIED'))

        rearrange_plan = rearrangement.request_rearrangement(self.operator, observation_id=obs.pk)
        self.assertEqual((rearrange_plan.result['status'], rearrange_plan.result['reason']), ('HOLD', 'ARM_REACH_UNVERIFIED'))

        outbound_order = self.order(cargo=c_out, kind='OUTBOUND', priority_group=1, sequence=2)
        p_out = services.make_plan(self.operator, order_id=outbound_order.pk, expected_version=1, observation_id=obs.pk)
        self.assertEqual((p_out.result['status'], p_out.result['reason']), ('HOLD', 'ARM_REACH_UNVERIFIED'))

        # 4. Legacy observation rejected at web snapshot(); direct legacy plan_outbound hits OUTBOUND_MOVE_UNAVAILABLE
        legacy_obs = Observation(
            created_by=self.admin, source_id='legacy', source_revision='1', observed_at=self.now,
            provenance='SYNTHETIC', condition=condition,
            payload=dict(bays=[dict(bay_id='Y11', cargo=['OUT3'], capacity=3)], holdings=[]),
        )
        with self.assertRaisesMessage(ValidationError, 'grid-placement-v1'):
            site_config.snapshot(legacy_obs)

        snap = YardSnapshot(
            1, self.now,
            (Bay('Y11', ('OUT3',), 3), Bay('temp_a', (), 3), Bay('temp_b', (), 3), Bay('stand', (), 1)),
        )
        direct = plan_outbound(snap, OutboundRequest('OUT3', 1), site_config.constraints(condition.payload), now=self.now)
        self.assertEqual((direct.status, direct.reason), ('HOLD', 'OUTBOUND_MOVE_UNAVAILABLE'))

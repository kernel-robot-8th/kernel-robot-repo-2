from copy import deepcopy
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, SimpleTestCase, TestCase

from harbor_web import services
from harbor_web.map_config import build_map, editor_draft, map_scene
from harbor_web.models import Audit, Condition, Observation, Plan
from harbor_web.tests import test_services as fixtures


class MapDraftTests(SimpleTestCase):
    def source(self):
        payload = build_map(None, editor_draft())['payload']
        payload.update(arm_reach=[['A', 'Y11'], ['A', 'temp'], ['B', 'stand']],
            allowed_moves=[['Y11', 'temp'], ['temp', 'stand']], spatial_moves=[['yard', 'temp']])
        return payload

    def test_fresh_map_has_no_execution_authority_or_observation(self):
        result = build_map(None, editor_draft())
        self.assertEqual(result['payload']['arm_reach'], [])
        self.assertEqual(result['payload']['allowed_moves'], [])
        self.assertEqual(result['scene']['kind'], 'map-definition')
        self.assertFalse(result['scene']['observed'])
        self.assertEqual(result['scene']['frames'][0]['boxes'], [])
        self.assertIsNone(map_scene({}))

    def test_display_changes_preserve_authority_and_source_is_immutable(self):
        source = self.source()
        original = deepcopy(source)
        draft = editor_draft(source)
        for area in draft['areas']:
            area['x'] += 10
            area['label'] += ' 표시'
        result = build_map(source, draft, source_id=7)
        self.assertEqual(result['payload']['arm_reach'], source['arm_reach'])
        self.assertEqual(result['payload']['allowed_moves'], source['allowed_moves'])
        self.assertEqual(source, original)
        self.assertNotEqual(result['fingerprint'], build_map(source, draft, source_id=8)['fingerprint'])

    def test_remapping_cell_or_changing_height_removes_reach(self):
        source = self.source()
        draft = editor_draft(source)
        yard = next(a for a in draft['areas'] if a['id'] == 'yard')
        yard['cells'][0]['bay_id'], yard['cells'][1]['bay_id'] = yard['cells'][1]['bay_id'], yard['cells'][0]['bay_id']
        result = build_map(source, draft)
        self.assertNotIn(['A', 'Y11'], result['payload']['arm_reach'])
        self.assertNotIn(['Y11', 'temp'], result['payload']['allowed_moves'])
        yard['levels'] += 1
        self.assertNotIn(['A', 'Y11'], build_map(source, draft)['payload']['arm_reach'])

    def test_resize_preserves_existing_primary_cell_reach_but_clears_auxiliary_reach(self):
        source = self.source()
        draft = editor_draft(source)
        yard = next(a for a in draft['areas'] if a['id'] == 'yard')
        yard['columns'] = 4
        yard['cells'] += [dict(bay_id=f'Y{r}4', row=r, column=4) for r in range(1, 4)]
        temp = next(a for a in draft['areas'] if a['id'] == 'temp')
        temp['levels'] = 4
        result = build_map(source, draft)
        self.assertIn(['A', 'Y11'], result['payload']['arm_reach'])
        self.assertNotIn(['A', 'temp'], result['payload']['arm_reach'])
        self.assertEqual(result['payload']['spatial_moves'], [])

    def test_unknown_fields_overlap_bounds_and_reserved_roles_are_rejected(self):
        base = editor_draft()
        invalid = []
        extra = deepcopy(base)
        extra['arm_reach'] = [['A', 'Y11']]
        invalid.append(extra)
        for key, value in [('role', 'outbound'), ('rows', True), ('levels', 7), ('x', float('nan'))]:
            draft = deepcopy(base)
            draft['areas'][0][key] = value
            invalid.append(draft)
        draft = deepcopy(base)
        draft['areas'][1].update(x=0, y=0)
        invalid.append(draft)
        draft = deepcopy(base)
        draft['areas'][0]['cells'][0]['bay_id'] = 'yard'
        invalid.append(draft)
        for draft in invalid:
            with self.subTest(draft=draft), self.assertRaises(ValidationError):
                build_map(None, draft)

    def test_legacy_draft_never_infers_bay_coordinates_or_reach(self):
        source = dict(outbound_stand='out', temporary_bays=['temp'], conveyor='belt',
            arm_reach=[['A', 'bay'], ['A', 'Y11'], ['A', 'temp']], allowed_moves=[['bay', 'out'], ['temp', 'out']],
            max_observation_age_seconds=60)
        result = build_map(source, editor_draft(source))
        self.assertEqual(result['payload']['arm_reach'], [])
        self.assertEqual(result['payload']['allowed_moves'], [])
        source['outbound_stand'] = 'yard'
        with self.assertRaises(ValidationError):
            editor_draft(source)

    def test_excessive_cell_list_rejected_before_copy_or_layout_construction(self):
        draft = editor_draft()
        draft['areas'][0]['cells'] *= 100
        with patch('harbor_web.map_config.deepcopy') as copy, patch('harbor_web.map_config.YardLayout.from_dict') as layout:
            with self.assertRaises(ValidationError):
                build_map(None, draft)
        copy.assert_not_called()
        layout.assert_not_called()

    def test_new_storage_has_no_reach_and_removed_areas_lose_references(self):
        source = self.source()
        draft = editor_draft(source)
        draft['areas'] = [a for a in draft['areas'] if a['id'] != 'temp']
        draft['areas'].append(dict(id='extra', label='Extra storage', role='storage',
            rows=2, columns=3, levels=4, x=10, y=0, cells=[]))
        result = build_map(source, draft)
        self.assertEqual(result['payload']['storage_areas'], ['yard', 'extra'])
        self.assertEqual(result['payload']['arm_reach'], [['A', 'Y11'], ['B', 'stand']])
        self.assertEqual(result['payload']['allowed_moves'], [])
        self.assertEqual(result['summary']['removed_areas'], ['temp'])
        self.assertEqual(result['summary']['added_areas'], ['extra'])


class MapEditorPagesTests(TestCase):
    setUp = fixtures.ServicesTests.setUp

    def post(self, path, data, client=None):
        return (client or self.client).post(path, json.dumps(data), content_type='application/json')

    def test_admin_required_and_csrf_protects_validation_and_save(self):
        url = '/settings/maps/new/'
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.operator)
        self.assertEqual(self.post(url + 'validate/', {'map': editor_draft()}).status_code, 403)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.admin)
        for path in (url, url + 'validate/'):
            self.assertEqual(self.post(path, {'map': editor_draft()}, secured).status_code, 403)
        req = RequestFactory().get('/')
        token = get_token(req)
        secured.cookies['csrftoken'] = req.META['CSRF_COOKIE']
        response = secured.post(url + 'validate/', json.dumps({'map': editor_draft()}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Condition.objects.exists())

    def test_validation_invalid_requests_and_fingerprints_write_nothing(self):
        self.client.force_login(self.admin)
        url = '/settings/maps/new/'
        self.assertEqual(self.post(url + 'validate/', {'map': editor_draft(), 'source_id': 999}).status_code, 400)
        self.assertEqual(self.client.post(url + 'validate/', '{}', content_type='text/plain').status_code, 400)
        result = self.post(url + 'validate/', {'map': editor_draft()}).json()
        data = dict(map=result['map'], fingerprint=result['fingerprint'], name='new', calibration_ref='ref')
        data['map']['areas'][0]['label'] = 'changed after validation'
        self.assertEqual(self.post(url, data).status_code, 400)
        for name, reference in (('', 'ref'), ('new', ''), ('new', 'x' * 193), ('\ud800', 'ref')):
            result = self.post(url + 'validate/', {'map': editor_draft()}).json()
            self.assertEqual(self.post(url, dict(map=result['map'], fingerprint=result['fingerprint'],
                name=name, calibration_ref=reference)).status_code, 400)
        self.assertEqual(Condition.objects.count(), 0)
        self.assertEqual(Audit.objects.count(), 0)

    def test_editor_get_projects_json_and_sets_csrf_without_writes(self):
        self.client.force_login(self.admin)
        response = self.client.get('/settings/maps/new/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'map_editor.html')
        self.assertEqual(response.context['editor_data']['validate_url'], '/settings/maps/new/validate/')
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(Condition.objects.count(), 0)

    def test_save_registers_new_version_and_parent_audit_without_mutating_history(self):
        self.client.force_login(self.admin)
        source = services.register_condition(self.admin, name='original', calibration_ref='source-ref',
            payload=build_map(None, editor_draft())['payload'])
        original = deepcopy(source.payload)
        url = f'/settings/conditions/{source.pk}/map/'
        result = self.post(url + 'validate/', {'map': editor_draft(source.payload)}).json()
        data = dict(map=result['map'], fingerprint=result['fingerprint'], name='new', calibration_ref='new-ref')
        # A fingerprint is tied to the route-owned source.
        self.assertEqual(self.post('/settings/maps/new/', data).status_code, 400)
        response = self.post(url, data)
        self.assertEqual(response.status_code, 200)
        new = Condition.objects.get(pk=response.json()['condition_id'])
        self.assertNotEqual(new.pk, source.pk)
        self.assertEqual(response.json()['redirect'], f'/inventory/?condition={new.pk}')
        source.refresh_from_db()
        self.assertEqual(source.payload, original)
        audit = Audit.objects.get(action='condition.map.register', object_id=str(new.pk))
        self.assertEqual(audit.detail['parent_condition_id'], source.pk)
        self.assertEqual(audit.detail['fingerprint'], result['fingerprint'])
        self.assertEqual(Observation.objects.count(), 0)
        self.assertEqual(Plan.objects.count(), 0)

    def test_parent_audit_failure_rolls_back_registration(self):
        self.client.force_login(self.admin)
        result = build_map(None, editor_draft())
        create = Audit.objects.create

        def fail_parent(**fields):
            if fields['action'] == 'condition.map.register':
                raise RuntimeError('audit failed')
            return create(**fields)

        with patch.object(Audit.objects, 'create', side_effect=fail_parent), self.assertRaises(RuntimeError):
            self.post('/settings/maps/new/', dict(map=result['map'], fingerprint=result['fingerprint'],
                name='new', calibration_ref='ref'))
        self.assertEqual(Condition.objects.count(), 0)
        self.assertEqual(Audit.objects.count(), 0)

import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from harbor.cargo_colors import CARGO_COLORS
from harbor.rosy_http import RosyClient, load_site_recognition
from harbor.site_recognition import (
    compare_with_yard,
    harbor_cell_to_site_ref,
    parse_site_recognition,
    placement_to_site_ref,
    site_ref_to_harbor_cells,
    site_ref_to_placement,
)


def payload():
    return {
        'schema': 'rosy.cell-site-recognition.v1',
        'snapshot_id': 'snapshot-1',
        'observed_at': '2026-11-23T12:00:00Z',
        'source_id': 'omx_overhead',
        'calibration_revision': 'calibration-1',
        'site_map_revision': 1,
        'sites': [
            {'site_ref': 'yard-r1-c1-l1', 'state': 'OCCUPIED', 'object_class': 'orange',
             'object_ref_read': None, 'confidence': 0.93},
            {'site_ref': 'yard-r1-c1-l2', 'state': 'EMPTY', 'object_class': None,
             'object_ref_read': None, 'confidence': 0.88},
            {'site_ref': 'temp_a-r1-c1-l1', 'state': 'UNREADABLE', 'object_class': None,
             'object_ref_read': None, 'confidence': 0.2},
        ],
    }


class SiteRecognitionParserTests(unittest.TestCase):
    def test_valid_snapshot_preserves_fields_without_mutating_input(self):
        doc = payload()
        original = copy.deepcopy(doc)
        result = parse_site_recognition(doc, known_site_refs={s['site_ref'] for s in doc['sites']})
        self.assertEqual(result, original)
        result['sites'][0]['state'] = 'EMPTY'
        self.assertEqual(doc, original)

    def test_rejects_missing_unknown_or_invalid_document_fields(self):
        for key in payload():
            doc = payload()
            del doc[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                parse_site_recognition(doc)
        for doc in (None, [], 'text', {**payload(), 'extra': True},
                    {**payload(), 'schema': 'unknown'}, {**payload(), 'sites': {}},
                    {**payload(), 'sites': [None]}):
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                parse_site_recognition(doc)
        for revision in (True, 0, -1, 1.0, '1', None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                parse_site_recognition({**payload(), 'site_map_revision': revision})

    def test_rejects_invalid_and_duplicate_site_refs_and_states(self):
        for field, value in (
            ('state', 'READ'), ('state', []), ('state', None),
            ('site_ref', 'Y11'), ('site_ref', 'yard-r0-c1-l1'),
            ('site_ref', 'yard-r1-c01-l1'), ('site_ref', 'yard-r1-c1-l0'),
            ('site_ref', 'yard-r1-c1-l1\n'), ('site_ref', 'x' * 193),
            ('object_class', 1), ('object_ref_read', []),
        ):
            doc = payload()
            doc['sites'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                parse_site_recognition(doc)
        doc = payload()
        doc['sites'].append(copy.deepcopy(doc['sites'][0]))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_SITE_REF'):
            parse_site_recognition(doc)
        doc = payload()
        doc['sites'][0].update(state='EMPTY', object_class='orange')
        with self.assertRaisesRegex(ValueError, 'INVALID_EMPTY_SITE'):
            parse_site_recognition(doc)

    def test_site_fields_are_required_and_unknown_fields_rejected(self):
        for key in payload()['sites'][0]:
            doc = payload()
            del doc['sites'][0][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                parse_site_recognition(doc)
        doc = payload()
        doc['sites'][0]['image'] = 'untrusted-photo'
        with self.assertRaises(ValueError):
            parse_site_recognition(doc)

    def test_known_refs_reject_unknown_areas_and_middle_columns(self):
        known = {'yard-r1-c1-l1', 'yard-r1-c1-l2', 'temp_a-r1-c1-l1'}
        for ref in ('yard-r1-c2-l1', 'yard-r1-c3-l1', 'yard-r1-c5-l1',
                    'yard-r1-c6-l1', 'yard-r9-c1-l1', 'missing-r1-c1-l1'):
            doc = payload()
            doc['sites'][0]['site_ref'] = ref
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, 'UNKNOWN_SITE_REF'):
                parse_site_recognition(doc, known_site_refs=known)
        with self.assertRaises(ValueError):
            parse_site_recognition(payload(), known_site_refs=set())

    def test_confidence_must_be_a_finite_number_in_closed_unit_interval(self):
        for value in (-0.1, 1.1, float('nan'), float('inf'), -float('inf'),
                      True, False, '0.9', None, [], 10 ** 400):
            doc = payload()
            doc['sites'][0]['confidence'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'INVALID_CONFIDENCE'):
                parse_site_recognition(doc)
        for value in (0, 1, 0.5):
            doc = payload()
            doc['sites'][0]['confidence'] = value
            self.assertEqual(parse_site_recognition(doc)['sites'][0]['confidence'], value)

    def test_observed_at_requires_valid_utc_iso_timestamp(self):
        for value in ('2026-11-23', '2026-11-23T12:00:00', '2026-11-23T12:00:00+09:00',
                      '2026-02-30T12:00:00Z', '2026-11-23T25:00:00Z',
                      '2026-11-23 12:00:00Z', '2026-11-23T12:00:00+00:01', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'INVALID_OBSERVED_AT'):
                parse_site_recognition({**payload(), 'observed_at': value})
        for value in ('2026-11-23T12:00:00Z', '2026-11-23T12:00:00.123456Z',
                      '2026-11-23T12:00:00+00:00'):
            self.assertEqual(parse_site_recognition({**payload(), 'observed_at': value})['observed_at'], value)

    def test_metadata_and_nullable_text_have_length_and_clean_text_limits(self):
        for field in ('snapshot_id', 'source_id', 'calibration_revision'):
            for value in ('', 'x' * 193, ' leading', 'bad\ntext', '\ud800', 123, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_site_recognition({**payload(), field: value})
        for field in ('object_class', 'object_ref_read'):
            for value in ('', 'x' * 193, 'bad\x00text', '\ud800'):
                doc = payload()
                doc['sites'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_site_recognition(doc)

    def test_sites_count_is_bounded_and_empty_partial_snapshot_is_allowed(self):
        doc = payload()
        doc['sites'] = [{**doc['sites'][0], 'site_ref': f'yard-r{row}-c1-l1'} for row in range(1, 513)]
        self.assertEqual(len(parse_site_recognition(doc)['sites']), 512)
        doc['sites'].append({**doc['sites'][0], 'site_ref': 'yard-r513-c1-l1'})
        with self.assertRaisesRegex(ValueError, 'INVALID_SITES'):
            parse_site_recognition(doc)
        self.assertEqual(parse_site_recognition({**payload(), 'sites': []})['sites'], [])

    def test_canonical_example_parses(self):
        path = Path(__file__).resolve().parents[1] / 'docs/contracts/rosy-cell-site-recognition.v1.example.json'
        doc = json.loads(path.read_text(encoding='utf-8'))
        result = parse_site_recognition(doc)
        self.assertEqual([s['state'] for s in result['sites']], ['OCCUPIED', 'EMPTY', 'UNREADABLE'])


class SiteRecognitionComparisonTests(unittest.TestCase):
    def test_wire_and_harbor_cell_conversions_preserve_anchor_and_level(self):
        for cell, ref in [('Y11', 'yard-r1-c1-l2'), ('Y12', 'yard-r1-c1-l2'),
                          ('Y13', 'yard-r1-c1-l2'), ('Y14', 'yard-r1-c4-l2'),
                          ('Y15', 'yard-r1-c4-l2'), ('Y16', 'yard-r1-c4-l2'),
                          ('Y123', 'yard-r12-c1-l2')]:
            self.assertEqual(harbor_cell_to_site_ref(cell, level=2), ref)
        self.assertEqual(site_ref_to_harbor_cells('yard-r2-c4-l3'), ('Y24', 'Y25', 'Y26'))
        self.assertEqual(site_ref_to_harbor_cells('yard-r1-c1-l1'), ('Y11', 'Y12', 'Y13'))
        self.assertEqual(site_ref_to_placement('temp_a-r2-c1-l3'),
                         {'area': 'temp_a', 'row': 2, 'column': 1, 'level': 3})
        self.assertEqual(placement_to_site_ref('yard', 2, 6, 3), 'yard-r2-c4-l3')
        for cell in ('Y00', 'Y17', 'Y11\n', 'temp_a', None):
            with self.subTest(cell=cell), self.assertRaises(ValueError):
                harbor_cell_to_site_ref(cell)
        for ref in ('yard-r1-c2-l1', 'yard-r1-c7-l1', 'bad'):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                site_ref_to_placement(ref)
        with self.assertRaises(ValueError):
            site_ref_to_harbor_cells('temp_a-r1-c1-l1')
        for coords in [('yard', True, 1, 1), ('yard', 0, 1, 1), ('yard', 1, 7, 1),
                       ('yard', 1, 1, 0), ('BAD', 1, 1, 1)]:
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                placement_to_site_ref(*coords)

    def test_comparison_reports_presence_and_unreadable_without_mutating_ledger(self):
        doc = payload()
        doc['sites'].extend([
            {**doc['sites'][0], 'site_ref': 'yard-r1-c4-l1'},
            {**doc['sites'][1], 'site_ref': 'yard-r2-c1-l1'},
        ])
        placements = [
            {'area': 'yard', 'row': 1, 'column': 1, 'level': 1, 'cargo_id': 'cargo-1'},
            {'area': 'yard', 'row': 1, 'column': 1, 'level': 2, 'cargo_id': 'cargo-2'},
            {'area': 'temp_a', 'row': 1, 'column': 1, 'level': 1, 'cargo_id': 'cargo-3'},
            {'area': 'yard', 'row': 3, 'column': 1, 'level': 1, 'cargo_id': 'not-reported'},
        ]
        before = copy.deepcopy((doc, placements))
        results = compare_with_yard(doc, placements)
        self.assertEqual([r['result'] for r in results], [
            'MATCH', 'ROSY_EMPTY_HARBOR_OCCUPIED', 'UNREADABLE',
            'ROSY_OCCUPIED_HARBOR_EMPTY', 'MATCH',
        ])
        self.assertEqual(results[0]['harbor_cargo_id'], 'cargo-1')
        self.assertEqual((doc, placements), before)
        self.assertEqual(compare_with_yard({**doc, 'sites': []}, placements), [])

    def test_class_mismatch_requires_an_actual_read_identity(self):
        placements = [{'area': 'yard', 'row': 1, 'column': 1, 'level': 1, 'cargo_id': 'cargo-1'}]
        for state, read, want in [('OCCUPIED', None, 'MATCH'), ('OCCUPIED', 'cargo-1', 'MATCH'),
                                  ('OCCUPIED', 'cargo-2', 'CLASS_MISMATCH'),
                                  ('UNREADABLE', 'cargo-2', 'UNREADABLE')]:
            doc = payload()
            doc['sites'] = [{**doc['sites'][0], 'state': state, 'object_ref_read': read}]
            with self.subTest(state=state, read=read):
                self.assertEqual(compare_with_yard(doc, placements)[0]['result'], want)

    def test_bad_or_duplicate_placement_anchors_are_not_silently_overwritten(self):
        placement = {'area': 'yard', 'row': 1, 'column': 1, 'level': 1, 'cargo_id': 'cargo-1'}
        for placements in ([placement, placement], [{**placement, 'column': 2}],
                           [{**placement, 'cargo_id': None}]):
            with self.subTest(placements=placements), self.assertRaises(ValueError):
                compare_with_yard(payload(), placements)

    def test_color_mismatch_uses_shared_colors_without_changing_validation_or_result(self):
        self.assertEqual(CARGO_COLORS, ('orange', 'pink', 'yellow', 'sky'))
        # parse_site_recognition still accepts arbitrary non-color object_class values from ROSY.
        custom_doc = payload()
        custom_doc['sites'] = [{**custom_doc['sites'][0], 'object_class': 'container_box'}]
        self.assertEqual(parse_site_recognition(custom_doc)['sites'][0]['object_class'], 'container_box')

        placements = [{'area': 'yard', 'row': 1, 'column': 1, 'level': 1, 'cargo_id': 'C-1', 'color': 'pink'}]
        # Known ledger color (pink) vs different shared color (orange): keeps MATCH and adds color_mismatch=True.
        doc = payload()
        doc['sites'] = [{**doc['sites'][0], 'object_class': 'orange', 'object_ref_read': None}]
        res = compare_with_yard(doc, placements)[0]
        self.assertEqual(res['result'], 'MATCH')
        self.assertIs(res.get('color_mismatch'), True)

        # Also works when ledger color is supplied via cargo_colors mapping, and preserves CLASS_MISMATCH.
        bare_placements = [{'area': 'yard', 'row': 1, 'column': 1, 'level': 1, 'cargo_id': 'C-1'}]
        doc['sites'] = [{**doc['sites'][0], 'object_class': 'sky', 'object_ref_read': 'C-9'}]
        res_cls = compare_with_yard(doc, bare_placements, cargo_colors={'C-1': 'pink'})[0]
        self.assertEqual(res_cls['result'], 'CLASS_MISMATCH')
        self.assertIs(res_cls.get('color_mismatch'), True)

        # Matching color, non-color ROSY class, unknown ledger color, or UNREADABLE never set color_mismatch.
        for state, obj_class, plc in (
            ('OCCUPIED', 'pink', placements),
            ('OCCUPIED', 'container_box', placements),
            ('OCCUPIED', None, placements),
            ('OCCUPIED', 'orange', bare_placements),
            ('OCCUPIED', 'orange', [{**placements[0], 'color': ''}]),
            ('UNREADABLE', 'orange', placements),
        ):
            doc['sites'] = [{**doc['sites'][0], 'state': state, 'object_class': obj_class, 'object_ref_read': None}]
            with self.subTest(state=state, obj_class=obj_class, has_color=plc[0].get('color')):
                entry = compare_with_yard(doc, plc)[0]
                self.assertNotIn('color_mismatch', entry)


class SiteRecognitionClientTests(unittest.TestCase):
    def test_injected_transport_gets_default_path_and_response_is_validated(self):
        calls = []
        def transport(method, path, body):
            calls.append((method, path, body))
            return payload()
        with patch.dict(os.environ, {}, clear=True):
            client = RosyClient(transport=transport)
            self.assertEqual(client.site_recognition()['snapshot_id'], 'snapshot-1')
        self.assertEqual(calls, [('GET', '/api/fleet/cell-site-recognition', None)])
        with self.assertRaises(ValueError):
            RosyClient(transport=lambda *args: {}).site_recognition()

    def test_environment_and_constructor_path_overrides(self):
        calls = []
        def transport(method, path, body):
            calls.append(path)
            return payload()
        with patch.dict(os.environ, {'HARBOR_ROSY_SITE_RECOGNITION_PATH': '/api/fleet/recognition'}):
            RosyClient(transport=transport).site_recognition()
            RosyClient(transport=transport, site_recognition_path='/api/fleet/custom').site_recognition()
        self.assertEqual(calls, ['/api/fleet/recognition', '/api/fleet/custom'])

    def test_unsafe_path_override_is_rejected_before_transport(self):
        for path in ('https://other.test/read', '//other.test/read', '/bad\npath', '/bad\\path',
                     '/bad?query=1', '/bad#fragment', '', None):
            if path is None:
                continue  # None selects the default.
            calls = []
            with self.subTest(path=path), self.assertRaises(ValueError):
                RosyClient(transport=lambda *args: calls.append(args), site_recognition_path=path).site_recognition()
            self.assertEqual(calls, [])

    def test_http_request_retains_fleet_auth_headers(self):
        class Opener:
            def open(self, request, *, timeout):
                self.request = request
                return io.BytesIO(json.dumps(payload()).encode())
        opener = Opener()
        with patch('harbor.rosy_http.build_opener', return_value=opener), patch.dict(os.environ, {}, clear=True):
            client = RosyClient('http://127.0.0.1:1', 'synthetic-harbor')
            self.assertEqual(client.site_recognition()['source_id'], 'omx_overhead')
        self.assertEqual(opener.request.full_url, 'http://127.0.0.1:1/api/fleet/cell-site-recognition')
        self.assertEqual(opener.request.get_method(), 'GET')
        self.assertEqual(opener.request.get_header('Authorization'), 'Bearer synthetic-harbor')
        self.assertEqual(opener.request.get_header('Content-type'), 'application/json')

    def test_disabled_loader_only_when_zero_or_unconfigured_and_enabled_by_default(self):
        calls = []
        client = RosyClient(transport=lambda *args: (calls.append(args), payload())[1])
        with patch.dict(os.environ, {'HARBOR_ROSY_SITE_RECOGNITION': '0'}):
            self.assertEqual(load_site_recognition(client), (None, 'ROSY 인식 연동 전'))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_site_recognition(), (None, 'ROSY 인식 연동 전'))
        self.assertEqual(calls, [])

        for env in ({}, {'HARBOR_ROSY_SITE_RECOGNITION': ''}, {'HARBOR_ROSY_SITE_RECOGNITION': '1'}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                snapshot, status = load_site_recognition(client)
                self.assertEqual(snapshot['snapshot_id'], 'snapshot-1')
                self.assertEqual(status, '')
        self.assertEqual(len(calls), 3)

    def test_enabled_loader_returns_snapshot_and_known_refs_are_checked(self):
        client = RosyClient(transport=lambda *args: payload())
        with patch.dict(os.environ, {}, clear=True):
            snapshot, status = load_site_recognition(client)
            self.assertEqual(snapshot['snapshot_id'], 'snapshot-1')
            self.assertEqual(status, '')
            self.assertEqual(load_site_recognition(client, known_site_refs=set()),
                             (None, 'ROSY 인식 응답 형식 오류'))

    def test_loader_distinguishes_404_not_yet_observed_missing_api_and_errors(self):
        class BrokenBody:
            def read(self, *_args):
                raise OSError('broken stream')

            def close(self):
                pass

        not_observed_body = json.dumps({'detail': {'code': 'CELL_SITE_RECOGNITION_NOT_YET_OBSERVED'}}).encode()
        other_code_body = json.dumps({'detail': {'code': 'SOME_OTHER_404'}}).encode()
        oversized_body = b'{"detail":{"code":"CELL_SITE_RECOGNITION_NOT_YET_OBSERVED","pad":"' + b'x' * (65 * 1024) + b'"}}'
        cases = [
            (HTTPError('http://synthetic', 404, 'not observed', {}, io.BytesIO(not_observed_body)), 'ROSY 인식 스냅샷 수신 대기'),
            (HTTPError('http://synthetic', 404, 'other', {}, io.BytesIO(other_code_body)), 'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'missing', {}, None), 'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'html', {}, io.BytesIO(b'<html>not found</html>')), 'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'huge', {}, io.BytesIO(oversized_body)), 'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'broken', {}, BrokenBody()), 'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 403, 'forbidden', {}, None), 'ROSY 인식 연결 오류'),
            (URLError('offline'), 'ROSY 인식 연결 오류'),
            (TimeoutError('timed out'), 'ROSY 인식 연결 오류'),
            (ValueError('bad JSON'), 'ROSY 인식 응답 형식 오류'),
        ]
        for error, status in cases:
            def transport(*args):
                raise error
            with self.subTest(error=error), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_site_recognition(RosyClient(transport=transport)), (None, status))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_site_recognition(RosyClient(transport=lambda *args: {})),
                             (None, 'ROSY 인식 응답 형식 오류'))

    def test_loader_404_detail_code_distinguishes_snapshot_wait_from_missing_api(self):
        import http.client

        class TrackedBody(io.BytesIO):
            closed_count = 0

            def close(self):
                self.closed_count += 1
                super().close()

        class TruncatedBody:
            def read(self, _n=-1):
                raise http.client.IncompleteRead(b'{"detail":')

            def close(self):
                pass

        not_yet = json.dumps({'detail': {'code': 'CELL_SITE_RECOGNITION_NOT_YET_OBSERVED'}}).encode()
        oversized_not_yet = json.dumps({
            'detail': {'code': 'CELL_SITE_RECOGNITION_NOT_YET_OBSERVED', 'pad': 'x' * 4100}
        }).encode()
        tracked_404 = TrackedBody(not_yet)
        tracked_503 = TrackedBody(b'service unavailable')
        cases = [
            (HTTPError('http://synthetic', 404, 'waiting', {}, tracked_404),
             'ROSY 인식 스냅샷 수신 대기'),
            (HTTPError('http://synthetic', 404, 'missing', {}, io.BytesIO(b'<html>not json</html>')),
             'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'too big', {}, io.BytesIO(oversized_not_yet)),
             'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'truncated', {}, TruncatedBody()),
             'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 404, 'missing', {}, None),
             'ROSY에 아직 인식 API 없음'),
            (HTTPError('http://synthetic', 403, 'forbidden', {}, io.BytesIO(not_yet)),
             'ROSY 인식 연결 오류'),
            (HTTPError('http://synthetic', 503, 'unavailable', {}, tracked_503),
             'ROSY 인식 연결 오류'),
            (HTTPError('http://synthetic', 503, 'truncated', {}, TruncatedBody()),
             'ROSY 인식 연결 오류'),
        ]
        for error, status in cases:
            def transport(*args):
                raise error
            with self.subTest(status=status, code=error.code), patch.dict(os.environ, {'HARBOR_ROSY_SITE_RECOGNITION': '1'}):
                self.assertEqual(load_site_recognition(RosyClient(transport=transport)), (None, status))
        self.assertGreaterEqual(tracked_404.closed_count, 1)
        self.assertGreaterEqual(tracked_503.closed_count, 1)


if __name__ == '__main__':
    unittest.main()

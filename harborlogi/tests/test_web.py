from copy import deepcopy
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from urllib.request import urlopen
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults
from wsgiref.simple_server import make_server

from harbor.rosy_http import RosyClient
from harbor.workflow import BusinessLedger
from harbor.web import ReadOnlyLedger, Workbench, Application, QuietHandler
from test_arm_transfer_execution import binding, Fleet
from test_execution import SOURCES


def request(app, path='/', *, query='', method='GET', host='localhost:8040', remote='127.0.0.1'):
    env = {}
    setup_testing_defaults(env)
    env.update(PATH_INFO=path, QUERY_STRING=query, REQUEST_METHOD=method, HTTP_HOST=host,
               SERVER_PORT='8040', REMOTE_ADDR=remote, **{'wsgi.input': BytesIO()})
    result = []
    body = b''.join(app(env, lambda status, headers: result.append((status, dict(headers)))))
    return int(result[0][0].split()[0]), result[0][1], body


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'business.sqlite3'
        self.ledger = BusinessLedger(self.path, **SOURCES)
        self.ledger.prepare('outbound', 3, (binding(),), outbound_id='outbound',
                            outbound_stand='stand', move_arms=(('A', 'B'),))
        self.fleet = Fleet()
        self.client = RosyClient(transport=self.fleet)
        self.ledger.propose('outbound', self.client)
        self.fleet.calls.clear()
        self.view = Workbench(ReadOnlyLedger(self.path), self.client)
        self.app = Application(self.view)

    def test_get_never_recovers_or_mutates_ledger_and_only_reads_fleet(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("UPDATE business SET state='SUBMITTING'")
        before = self.path.read_bytes()
        self.assertEqual(self.view.tasks()[0]['state'], 'SUBMITTING')
        detail = self.view.detail('outbound')
        self.assertEqual(detail['fleet']['status'], 'PROPOSED')
        self.assertEqual(detail['business']['state'], 'SUBMITTING')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.fleet.calls, [('GET', '/api/fleet/arm-transfers/jobs/physical-1', None)])

    def test_receipt_is_separate_from_business_completion_and_redacts_extra_fields(self):
        self.fleet.status = 'GOAL_CONFIRMED'
        self.fleet.job['private_token'] = 'do-not-return'
        detail = self.view.detail('outbound')
        self.assertEqual(detail['receipt']['status'], 'VERIFIED')
        self.assertEqual(detail['receipt']['attempt_id'], 'attempt-1')
        self.assertEqual(detail['business']['state'], 'WAITING')
        self.assertNotIn('do-not-return', json.dumps(detail))

    def test_mismatched_mission_binding_and_receipt_are_unverified(self):
        original = deepcopy(self.fleet.job)
        for changes in ({'mission_id': 'other'}, {'transfer': {}}, {'request_key': 'other'}):
            self.fleet.job = {**original, **changes}
            detail = self.view.detail('outbound')
            self.assertEqual(detail['fleet']['status'], 'MISMATCH')
            self.assertNotEqual(detail['receipt']['status'], 'VERIFIED')
        self.fleet.job = original
        self.fleet.status = 'GOAL_CONFIRMED'
        self.fleet.receipt_changes = {'dispatch_generation': 99}
        detail = self.view.detail('outbound')
        self.assertEqual(detail['receipt']['status'], 'MISMATCH')

    def test_failure_does_not_expose_credential_or_fallback_to_demo(self):
        def fail(*args):
            raise TimeoutError('sensitive-token-and-private-endpoint')
        self.view.client = RosyClient(transport=fail)
        status, _, body = request(self.app, '/api/work', query='key=outbound')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['fleet']['status'], 'UNAVAILABLE')
        self.assertNotIn(b'sensitive-token', body)
        self.assertEqual(json.loads(body)['mode'], 'live')

    def test_local_boundary_assets_methods_and_unknown_work(self):
        for options in ({'method': 'POST'}, {'host': 'attacker.test:8040'}, {'remote': '192.0.2.10'}):
            with self.subTest(options=options):
                self.assertIn(request(self.app, '/api/work', **options)[0], (403, 405))
        self.assertEqual(request(self.app, '/api/work', query='key=missing')[0], 404)
        self.assertEqual(request(self.app, '/assets/../workflow.py')[0], 404)
        self.assertEqual(request(self.app, '/assets/app.js')[0], 200)
        status, headers, _ = request(self.app)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_real_navigation_urls_open_shell_without_fleet_or_ledger_mutations(self):
        before = self.path.read_bytes()
        for path in ('/overview', '/work', '/work/outbound', '/work/outbound/plan',
                     '/work/outbound/execution', '/help', '/assets/tokens.css'):
            with self.subTest(path=path):
                self.assertEqual(request(self.app, path)[0], 200)
        self.assertEqual(request(self.app, '/work/outbound/approve')[0], 404)
        self.assertEqual(request(self.app, '/equipment')[0], 404)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.fleet.calls, [])

    def test_existing_special_business_keys_are_data_in_query_routes(self):
        for key in ('.', '..', 'a/b', 'a\\b'):
            self.ledger.prepare(key, 3, (binding(),), outbound_id='outbound',
                                outbound_stand='stand', move_arms=(('A', 'B'),))
            before = self.path.read_bytes()
            status, _, body = request(self.app, '/api/work', query=urlencode({'key': key}))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['business']['request_key'], key)
            self.assertEqual(request(self.app, '/work', query=urlencode({'key': key, 'tab': 'plan'}))[0], 200)
            self.assertEqual(self.path.read_bytes(), before)

    def test_missing_database_is_never_created(self):
        missing = self.path.parent / 'missing.sqlite3'
        with self.assertRaises((ValueError, sqlite3.OperationalError)):
            ReadOnlyLedger(missing).tasks()
        self.assertFalse(missing.exists())

    def test_final_stage_without_current_mission_is_not_described_as_unsubmitted(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("UPDATE business SET state='COMPLETED',step_index=1,mission_id=NULL")
        self.assertEqual(self.view.detail('outbound')['fleet']['status'], 'NO_CURRENT_JOB')

    def test_console_handoff_rejects_credential_and_script_urls(self):
        for url in ('javascript:alert(1)', 'https://user:pass@example.test',
                    'https://example.test/?token=private', 'http://example.test',
                    'https://example.test/#private', 'https://example.test\\@other.test'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Workbench(ReadOnlyLedger(self.path), console_url=url)

    def test_task_changed_while_fleet_read_is_not_combined_with_old_plan(self):
        original = self.fleet
        def concurrent(method, path, body):
            with closing(sqlite3.connect(self.path)) as c, c:
                c.execute('UPDATE business SET row_version=row_version+1')
            return original(method, path, body)
        self.view.client = RosyClient(transport=concurrent)
        self.assertEqual(self.view.detail('outbound')['fleet']['status'], 'STALE')

    def test_workbench_reads_job_and_receipt_through_real_http_without_writes(self):
        fleet, calls = self.fleet, []
        fleet.status = 'GOAL_CONFIRMED'
        class SyntheticFleet(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.command, self.path, self.headers.get('Authorization') == 'Bearer unit-test'))
                value = fleet('GET', self.path, None)
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(('127.0.0.1', 0), SyntheticFleet) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.view.client = RosyClient(f'http://127.0.0.1:{server.server_port}', 'unit-test')
                before = self.path.read_bytes()
                with make_server('127.0.0.1', 0, self.app, handler_class=QuietHandler) as web:
                    web_thread = Thread(target=web.serve_forever, daemon=True)
                    web_thread.start()
                    try:
                        with urlopen(f'http://127.0.0.1:{web.server_port}/api/work?key=outbound') as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(json.load(response)['receipt']['status'], 'VERIFIED')
                    finally:
                        web.shutdown()
                        web_thread.join()
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual([path for _, path, _ in calls], [
                    '/api/fleet/arm-transfers/jobs/physical-1',
                    '/api/fleet/arm-transfers/jobs/physical-1/physical-receipt'])
                self.assertTrue(all(method == 'GET' and authorized for method, _, authorized in calls))
            finally:
                server.shutdown()
                thread.join()


if __name__ == '__main__':
    unittest.main()

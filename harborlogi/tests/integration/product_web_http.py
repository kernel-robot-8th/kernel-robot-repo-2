"""Opt-in authenticated Django HTTP consumer; only the parent imports platform code."""
import http.cookiejar
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from threading import Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, Request
from wsgiref.simple_server import make_server


class ProductWebHttpTests(unittest.TestCase):
    def assert_app_only(self):
        self.assertFalse(any(name == prefix or name.startswith(prefix + '.') for name in sys.modules
                             for prefix in ('fleet', 'core_common', 'omx_adapter', 'rosy', 'rosy_cell')))

    def test_authenticated_product_to_actual_fleet(self):
        self.assertEqual(os.environ.get('HARBOR_INTEGRATION'), '1')
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        self.assertEqual(config['schema'], 'harbor.web-arm-http-test/1')
        self.assertIs(config['simulation'], True)
        origin = urlsplit(config['origin'])
        self.assertEqual(origin.scheme, 'http')
        self.assertTrue(ipaddress.ip_address(origin.hostname).is_loopback)
        self.assertFalse({'approver', 'observer_token', 'viewer'} & config.keys())
        self.assert_app_only()
        with tempfile.TemporaryDirectory(prefix='harbor-web-http-') as folder:
            root = Path(folder)
            bindings = root / 'bindings.json'
            bindings.write_text(json.dumps([config['binding']]), encoding='utf-8')
            os.environ.update(DJANGO_SETTINGS_MODULE='harbor_web.settings', HARBOR_WEB_MODE='test',
                HARBOR_WEB_SECRET_KEY=secrets.token_urlsafe(64), HARBOR_WEB_DATABASE=str(root / 'web.sqlite3'),
                HARBOR_LEDGER_PATH=str(root / 'business.sqlite3'), HARBOR_BINDINGS_PATH=str(bindings),
                HARBOR_FLEET_ORIGIN=config['origin'], HARBOR_FLEET_TOKEN=config['operator'],
                HARBOR_FLEET_PRINCIPAL='operator', HARBOR_WEB_ALLOW_SIMULATION='1')
            import django
            django.setup()
            from django.core.management import call_command
            from django.contrib.auth import get_user_model
            from django.contrib.auth.models import Group
            from django.core.wsgi import get_wsgi_application
            from django.utils import timezone
            from harbor.web import QuietHandler
            from harbor_web import commands
            from harbor_web.models import Cargo, Command, CommandFact, Condition, Observation, Order, Plan
            call_command('migrate', verbosity=0)
            call_command('bootstrap_roles', verbosity=0)
            passwords = {}
            for role in ('admin', 'operator', 'viewer'):
                passwords[role] = secrets.token_urlsafe(32)
                user = get_user_model().objects.create_user(role, password=passwords[role])
                user.groups.add(Group.objects.get(name=role))
            with make_server('127.0.0.1', 0, get_wsgi_application(), handler_class=QuietHandler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}'
                def browser(role):
                    jar = http.cookiejar.CookieJar()
                    opener = build_opener(HTTPCookieProcessor(jar))
                    def request(path, data=None, expected=200, csrf=True):
                        headers = {}
                        if data is not None:
                            data = dict(data)
                            if csrf:
                                data['csrfmiddlewaretoken'] = next(c.value for c in jar if c.name == 'csrftoken')
                            headers['Referer'] = base + path
                        req = Request(base + path, data=urlencode(data).encode() if data is not None else None, headers=headers)
                        try:
                            with opener.open(req, timeout=15) as response:
                                status, body = response.status, response.read().decode()
                        except HTTPError as error:
                            status, body = error.code, error.read().decode()
                        self.assertEqual(status, expected, f'Unexpected HTTP status for {path}')
                        return body
                    request('/accounts/login/')
                    request('/accounts/login/', dict(username=role, password=passwords[role]))
                    return request
                try:
                    admin, operator, viewer = (browser(role) for role in ('admin', 'operator', 'viewer'))
                    cargo_code = config['binding']['cargo_id']
                    operator('/cargo/new/')
                    operator('/cargo/new/', dict(code=cargo_code, name='Synthetic HTTP cargo', length_cells=1,
                        weight_kg=1, volume_m3=1, destination='stand'))
                    self.assertTrue(Cargo.objects.filter(code=cargo_code).exists(), 'Cargo form did not save its synthetic fixture')
                    admin('/settings/conditions/new/')
                    admin('/settings/conditions/new/', dict(name='Synthetic HTTP installation', calibration_ref='synthetic-only', payload=json.dumps(dict(outbound_stand='stand', temporary_bays=['temp'], allowed_moves=[['yard', 'stand']], max_observation_age_seconds=300, conveyor='conveyor', arm_reach=[['A', 'yard'], ['A', 'stand']]))))
                    condition = Condition.objects.get()
                    admin('/inventory/import/')
                    admin('/inventory/import/', dict(condition=condition.pk, source_id='synthetic-camera', source_revision='1', observed_at=timezone.now().isoformat(), provenance='SYNTHETIC', payload=json.dumps(dict(bays=[dict(bay_id='yard', cargo=[cargo_code], capacity=1), dict(bay_id='temp', cargo=[], capacity=1), dict(bay_id='stand', cargo=[], capacity=1)], holdings=[]))))
                    operator('/requests/new/')
                    operator('/requests/new/', dict(cargo=Cargo.objects.get().pk, kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED', note='Synthetic integration'))
                    order, observation = Order.objects.get(), Observation.objects.get()
                    operator(f'/requests/{order.pk}/', dict(action='plan', expected_version=order.version, observation=observation.pk))
                    plan = Plan.objects.get()
                    self.assertEqual(plan.result['status'], 'READY')
                    self.assertEqual(len(plan.result['moves']), 1)
                    operator(f'/plans/{plan.pk}/', dict(expected_version=order.version))
                    command = Command.objects.get()
                    key, digest = command.key, command.body_digest
                    path = f'/work/{command.pk}/'
                    # Publish the original identity before submission; approval remains outside the app.
                    key_path = Path(config['request_key_path'])
                    key_path.write_text('harbor:' + str(key) + ':0', encoding='utf-8')
                    key_path.chmod(0o600)
                    viewer(path)
                    viewer(path, dict(action='submit', expected_version=command.version), expected=403)
                    operator(path, dict(action='submit', expected_version=command.version), expected=403, csrf=False)
                    operator(path, dict(action='submit', expected_version=command.version))
                    deadline = time.monotonic() + 45
                    while True:
                        command.refresh_from_db()
                        operator(path, dict(action='reconcile', expected_version=command.version))
                        fact = CommandFact.objects.filter(command=command, receipt__isnull=False).order_by('-pk').first()
                        if fact:
                            break
                        self.assertLess(time.monotonic(), deadline, 'No validated physical receipt before deadline')
                        time.sleep(.1)
                    self.assertEqual(fact.receipt['physical_status'], 'AT_TARGET_AND_CLEAR')
                    # ADR0006: the receipt hashes the full dispatch grant; the job hashes the transfer body.
                    self.assertNotEqual(fact.receipt['request_digest'], fact.job['request_digest'])
                    self.assertEqual(fact.job['request_key'], 'harbor:' + str(key) + ':0')
                    registry = commands.acquire_registry()
                    before = registry.ledger.read('harbor:' + str(key))
                    self.assertEqual(before['state'], 'WAITING')
                    self.assertTrue(before['goal_confirmed'])
                    html = operator(path)
                    self.assertIn('AT_TARGET_AND_CLEAR', html)
                    self.assertIn(fact.receipt['receipt_id'], html)
                    self.assertEqual(registry.ledger.read('harbor:' + str(key)), before)
                    with self.assertRaises(ValueError):
                        registry.ledger.confirm('harbor:' + str(key), None, now=timezone.now())
                    command.refresh_from_db()
                    operator(path, dict(action='submit', expected_version=command.version), expected=409)
                    commands.registry_close()
                    operator(path)
                    reopened = commands.acquire_registry()
                    self.assertEqual(reopened.ledger.read('harbor:' + str(key)), before)
                    command.refresh_from_db()
                    self.assertEqual((command.key, command.body_digest), (key, digest))
                    self.assertEqual(Command.objects.count(), 1)
                    operator(path, dict(action='cancel', expected_version=command.version, reason='synthetic web cancellation contract check'))
                    command.refresh_from_db()
                    self.assertEqual(command.state, 'CANCEL_PENDING')
                    self.assertEqual(reopened.ledger.read('harbor:' + str(key))['state'], 'HOLD')
                    operator(path, dict(action='reconcile', expected_version=command.version))
                    command.refresh_from_db()
                    self.assertEqual(command.state, 'CANCEL_PENDING')
                    self.assertEqual(reopened.ledger.read('harbor:' + str(key))['state'], 'HOLD')
                    self.assertEqual(command.key, key)
                    self.assert_app_only()
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    commands.registry_close()
                    self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()

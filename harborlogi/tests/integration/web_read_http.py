"""Opt-in authenticated Django HTTP consumer verifying read panels over real ROSY HTTP."""
import http.cookiejar
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, Request
from wsgiref.simple_server import make_server


class WebReadHttpTests(unittest.TestCase):
    def assert_app_only(self):
        self.assertFalse(any(name == prefix or name.startswith(prefix + '.') for name in sys.modules
                             for prefix in ('fleet', 'core_common', 'omx_adapter', 'rosy', 'rosy_cell', 'fleet_harness')))

    def test_inventory_and_cell_site_import_consume_actual_fleet(self):
        self.assertEqual(os.environ.get('HARBOR_INTEGRATION'), '1')
        config = json.loads(Path(os.environ['HARBOR_INTEGRATION_CONFIG']).read_text(encoding='utf-8'))
        self.assertEqual(config['schema'], 'harbor.web-read-http-test/1')
        self.assertIs(config['simulation'], True)
        origin = urlsplit(config['origin'])
        self.assertEqual(origin.scheme, 'http')
        self.assertTrue(ipaddress.ip_address(origin.hostname).is_loopback)
        self.assertFalse({'approver', 'observer_token', 'viewer'} & config.keys())
        self.assert_app_only()
        with tempfile.TemporaryDirectory(prefix='harbor-web-read-http-', ignore_cleanup_errors=True) as folder:
            root = Path(folder)
            bindings = root / 'bindings.json'
            bindings.write_text('[]', encoding='utf-8')
            os.environ.update(
                DJANGO_SETTINGS_MODULE='harbor_web.settings',
                HARBOR_WEB_MODE='test',
                HARBOR_WEB_SECRET_KEY=secrets.token_urlsafe(64),
                HARBOR_WEB_DATABASE=str(root / 'web.sqlite3'),
                HARBOR_LEDGER_PATH=str(root / 'business.sqlite3'),
                HARBOR_BINDINGS_PATH=str(bindings),
                HARBOR_FLEET_ORIGIN='',
                HARBOR_FLEET_ALLOWED_ORIGINS=config['origin'],
                HARBOR_FLEET_TOKEN=config['operator'],
                HARBOR_FLEET_PRINCIPAL='',
                HARBOR_WEB_ALLOW_SIMULATION='1',
            )
            import django
            django.setup()
            from django.contrib.auth import get_user_model
            from django.contrib.auth.models import Group
            from django.core.management import call_command
            from django.core.wsgi import get_wsgi_application
            from django.db import connections
            from harbor.rosy_http import RosyClient
            from harbor.web import QuietHandler
            from harbor_web.models import Audit, FleetConnection
            from harbor_web.tests.test_glossary_sweep import FORBIDDEN, _visible_and_summary_text

            call_command('migrate', verbosity=0)
            call_command('bootstrap_roles', verbosity=0)
            session = RosyClient(config['origin'], config['operator']).session()
            self.assertEqual(session.get('principal_id'), config['principal'])
            self.assertEqual(session.get('role'), 'service')

            passwords = {}
            for role in ('admin', 'viewer'):
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
                                status, body = response.status, response.read().decode('utf-8')
                        except HTTPError as error:
                            status, body = error.code, error.read().decode('utf-8')
                        self.assertEqual(status, expected, f'Unexpected HTTP status for {path}')
                        return body

                    request('/accounts/login/')
                    request('/accounts/login/', dict(username=role, password=passwords[role]))
                    return request

                try:
                    admin, viewer = (browser(role) for role in ('admin', 'viewer'))
                    admin('/settings/integration/')
                    admin('/settings/integration/', dict(
                        action='save',
                        expected_version=0,
                        origin=config['origin'],
                        principal=config['principal'],
                    ))
                    self.assertEqual(FleetConnection.objects.get().version, 1)
                    checked = admin('/settings/integration/', dict(action='check', expected_version=1))
                    self.assertNotIn(config['operator'], checked)
                    check_detail = Audit.objects.filter(action='fleet.check').latest('pk').detail
                    self.assertEqual(check_detail['session'], 'MATCHED')
                    self.assertEqual(check_detail['reason'], 'ARM_NOT_INSTALLED')

                    html = viewer('/inventory/')
                    self.assertIn('ROSY 위치 체계', html)
                    self.assertIn('S_OUT', html)
                    self.assertIn('S3', html)
                    self.assertIn('yard', html)
                    self.assertIn('belt', html)
                    self.assertIn('보류 (', html)
                    self.assertIn('탑뷰 시점은 야드 평면과 180° 반전 방향입니다', html)
                    self.assertNotIn(config['operator'], html)
                    parts = _visible_and_summary_text(html)
                    for item in (parts if isinstance(parts, (tuple, list)) else (parts,)):
                        text = item if isinstance(item, str) else ' '.join(item)
                        self.assertIsNone(FORBIDDEN.search(text))

                    preview = admin('/settings/maps/import/', dict(source='fleet'))
                    self.assertIn('미리보기 요약', preview)
                    self.assertIn('rosy.cell-site-map.v1', preview)
                    self.assertIn('37개', preview)
                    self.assertIn('장치 야드', preview)
                    self.assertIn('반출대', preview)
                    self.assertNotIn(config['operator'], preview)
                    self.assert_app_only()
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    connections.close_all()


if __name__ == '__main__':
    unittest.main()

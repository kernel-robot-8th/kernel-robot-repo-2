from contextlib import closing
from django.contrib.auth.models import Group, User, AnonymousUser
from django.core.management import call_command
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path, include
from harbor_web.auth import has_role, require_roles, roles

@require_roles('operator')
def protected(request):
    return HttpResponse('ok')

urlpatterns = [path('protected/', protected), path('', include('harbor_web.urls'))]

class RoleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('person', password='long-test-password-472!')
        call_command('bootstrap_roles', verbosity=0)

    def test_bootstrap_is_idempotent(self):
        call_command('bootstrap_roles', verbosity=0)
        self.assertEqual(set(Group.objects.values_list('name', flat=True)), {'viewer', 'operator', 'field', 'admin'})
        self.assertEqual(Group.objects.get(name='admin').permissions.count(), 8)
        self.assertEqual(Group.objects.get(name='operator').permissions.count(), 0)

    def test_superuser_does_not_imply_command_roles(self):
        self.user.is_superuser = True
        self.user.save()
        self.user.groups.add(Group.objects.get(name='admin'))
        self.assertEqual(roles(self.user), {'admin'})
        self.assertFalse(has_role(self.user, 'operator'))
        self.assertFalse(has_role(AnonymousUser(), 'viewer'))

    @override_settings(ROOT_URLCONF=__name__)
    def test_revocation_and_inactive_apply_next_request(self):
        self.user.groups.add(Group.objects.get(name='operator'))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/protected/').status_code, 200)
        self.user.groups.clear()
        self.assertEqual(self.client.get('/protected/').status_code, 403)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get('/protected/').status_code, 302)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/accounts/logout/').status_code, 405)
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 302)

    def test_superuser_needs_explicit_admin_group(self):
        self.user.is_superuser = self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.user.groups.add(Group.objects.get(name='admin'))
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_login_rejects_external_next(self):
        response = self.client.post('/accounts/login/?next=https://evil.example/', {'username': self.user.username, 'password': 'long-test-password-472!'})
        self.assertEqual(response.url, '/')

    def test_csrf_and_security_headers(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/accounts/login/', {}).status_code, 403)
        self.client.force_login(self.user)
        response = self.client.get('/accounts/password_change/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn("object-src 'none'", response['Content-Security-Policy'])

    def test_login_throttles(self):
        for _ in range(5):
            response = self.client.post('/accounts/login/', {'username': 'person', 'password': 'wrong'})
        self.assertEqual(response.status_code, 429)

    @override_settings(MODE='development')
    def test_development_rejects_remote_address(self):
        self.assertEqual(self.client.get('/accounts/login/', REMOTE_ADDR='192.0.2.3').status_code, 403)
        self.assertEqual(self.client.get('/accounts/login/', REMOTE_ADDR='127.0.0.1').status_code, 200)

    def test_inactive_has_no_roles(self):
        self.user.groups.add(Group.objects.get(name='operator'))
        self.user.is_active = False
        self.assertFalse(has_role(self.user, 'operator'))

    def test_password_change_invalidates_other_sessions(self):
        from django.test import Client
        other = Client()
        other.force_login(self.user)
        self.client.force_login(self.user)
        response = self.client.post('/accounts/password_change/', {'old_password': 'long-test-password-472!', 'new_password1': 'different-password-852!', 'new_password2': 'different-password-852!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(other.get('/accounts/password_change/').status_code, 302)

class DatabaseOwnershipTests(TestCase):
    def test_business_database_is_rejected_without_mutation(self):
        import sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from django.core.exceptions import ImproperlyConfigured
        from harbor_web.settings import validate_database_path
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'renamed.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE business (id INTEGER)')
            before = path.read_bytes()
            with self.assertRaises(ImproperlyConfigured):
                validate_database_path(path)
            self.assertEqual(path.read_bytes(), before)

    def test_ledger_collision_and_unknown_database_are_rejected(self):
        import os
        import sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.exceptions import ImproperlyConfigured
        from harbor_web.settings import validate_database_path
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'dedicated.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE unrelated (id INTEGER)')
            before = path.read_bytes()
            with self.assertRaises(ImproperlyConfigured):
                validate_database_path(path)
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(path)}):
                with self.assertRaises(ImproperlyConfigured):
                    validate_database_path(path.parent / '.' / path.name)
            self.assertEqual(path.read_bytes(), before)

    def test_new_database_is_not_created_by_validation(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from harbor_web.settings import validate_database_path
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'new.sqlite3'
            validate_database_path(path)
            self.assertFalse(path.exists())

    def test_transport_and_hardlink_ledger_are_rejected(self):
        import os
        import sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.exceptions import ImproperlyConfigured
        from harbor_web.settings import validate_database_path
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'renamed.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE transport_intents (id INTEGER)')
            alias = Path(directory) / 'ledger.sqlite3'
            os.link(path, alias)
            before = path.read_bytes()
            with self.assertRaises(ImproperlyConfigured):
                validate_database_path(path)
            with patch.dict(os.environ, {'HARBOR_LEDGER_PATH': str(alias)}):
                with self.assertRaisesMessage(ImproperlyConfigured, 'must be distinct'):
                    validate_database_path(path)
            self.assertEqual(path.read_bytes(), before)

    def test_django_owned_database_accepted_read_only(self):
        import sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from harbor_web.settings import validate_database_path
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'web.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE django_migrations (id INTEGER, app TEXT, name TEXT)')
                connection.execute("INSERT INTO django_migrations VALUES(1,'harbor_web','0001_initial')")
                connection.execute('CREATE TABLE django_content_type (id INTEGER)')
                connection.commit()
            before = path.read_bytes()
            validate_database_path(path)
            self.assertEqual(path.read_bytes(), before)

    def test_unrelated_django_database_is_not_adopted(self):
        import sqlite3
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from django.core.exceptions import ImproperlyConfigured
        from harbor_web.settings import validate_database_path
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'other.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE django_migrations (id INTEGER, app TEXT)')
                connection.execute("INSERT INTO django_migrations VALUES(1,'other_app')")
                connection.execute('CREATE TABLE django_content_type (id INTEGER)')
                connection.commit()
            before = path.read_bytes()
            with self.assertRaisesMessage(ImproperlyConfigured, 'Harbor web ownership'):
                validate_database_path(path)
            self.assertEqual(path.read_bytes(), before)

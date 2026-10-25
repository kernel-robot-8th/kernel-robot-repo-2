import os
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harbor_web.runtime import require_profile, backup_product, restore_product

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.web = self.root / "web.sqlite3"
        with closing(sqlite3.connect(self.web)) as c, c:
            c.execute("CREATE TABLE django_migrations (app TEXT)")
            c.execute("INSERT INTO django_migrations VALUES ('harbor_web')")
            c.execute("CREATE TABLE harbor_web_deploymentstate (id INTEGER PRIMARY KEY, mode TEXT, restored_at TEXT, relocation TEXT NOT NULL DEFAULT '{}')")
            c.execute("INSERT INTO harbor_web_deploymentstate (id, mode, restored_at) VALUES (1, 'test', NULL)")
    def test_profile_rejects_cross_mode_without_mutation(self):
        before = self.web.read_bytes()
        with self.assertRaises(ValueError):
            require_profile(self.web, 'live')
        self.assertEqual(before, self.web.read_bytes())
    def test_pair_restores_with_gate_and_checks_hash(self):
        backup = self.root / 'backup'
        backup_product(backup, self.web, None, 'test')
        target = self.root / 'restored.sqlite3'
        restore_product(backup, target, self.root / 'ledger.sqlite3', 'test')
        with closing(sqlite3.connect(target)) as c:
            self.assertIsNotNone(c.execute('SELECT restored_at FROM harbor_web_deploymentstate').fetchone()[0])
        with self.assertRaises(ValueError):
            restore_product(backup, target, self.root / 'other.sqlite3', 'test')
        (backup / 'web.sqlite3').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            restore_product(backup, self.root / 'second.sqlite3', self.root / 'third.sqlite3', 'test')
    def test_busy_backup_rejected(self):
        from harbor.ownership import WriterLease
        with WriterLease(self.web):
            with self.assertRaises(RuntimeError):
                backup_product(self.root / 'backup', self.web, None, 'test')

    def test_unrelated_django_schema_rejected(self):
        with closing(sqlite3.connect(self.web)) as c, c:
            c.execute("DELETE FROM django_migrations")
        before = self.web.read_bytes()
        with self.assertRaises(ValueError):
            require_profile(self.web, 'test', allow_initial=True)
        self.assertEqual(before, self.web.read_bytes())

    def test_real_pair_and_manifest_path_rejection(self):
        import json
        ledger = self.root / 'business.sqlite3'
        with closing(sqlite3.connect(ledger)) as c, c:
            c.execute('CREATE TABLE business (key TEXT)')
            c.execute('CREATE TABLE evidence (key TEXT)')
            c.execute("INSERT INTO business VALUES ('saved')")
        backup = self.root / 'backup'
        backup_product(backup, self.web, ledger, 'test')
        restored = self.root / 'new-ledger.sqlite3'
        restore_product(backup, self.root / 'new-web.sqlite3', restored, 'test')
        with closing(sqlite3.connect(restored)) as c:
            self.assertEqual(c.execute('SELECT key FROM business').fetchone()[0], 'saved')
        with self.assertRaises(ValueError):
            backup_product(backup, self.web, ledger, 'test')
        manifest = json.loads((backup / 'manifest.json').read_text())
        manifest['files']['../escape'] = 'x'
        (backup / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            restore_product(backup, self.root / 'third-web', self.root / 'third-ledger', 'test')

    def test_lease_cross_process(self):
        import subprocess
        import sys
        from harbor.ownership import WriterLease
        code = "from harbor.ownership import WriterLease; import sys; WriterLease(sys.argv[1]).__enter__()"
        with WriterLease(self.web):
            result = subprocess.run([sys.executable, '-c', code, str(self.web)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'already has an execution owner', result.stderr)

    @unittest.skipUnless(__import__("importlib.util", fromlist=["find_spec"]).find_spec("django"), "Django optional web dependency")
    def test_settings_reject_renamed_cross_mode_read_only(self):
        import subprocess
        import sys
        env = dict(os.environ, HARBOR_WEB_MODE='live', HARBOR_WEB_ALLOWED_HOSTS='harbor.invalid',
                   HARBOR_WEB_SECRET_KEY=__import__('secrets').token_urlsafe(64),
                   HARBOR_WEB_DATABASE=str(self.web))
        before = self.web.read_bytes()
        result = subprocess.run([sys.executable, '-c', 'import harbor_web.settings'], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'deployment mode differs', result.stderr)
        self.assertEqual(before, self.web.read_bytes())
        self.assertEqual(list(self.root.iterdir()), [self.web])

    def test_second_publication_failure_cleans_both(self):
        from unittest.mock import patch
        ledger = self.root / 'business.sqlite3'
        with closing(sqlite3.connect(ledger)) as c, c:
            c.execute('CREATE TABLE business (key TEXT)')
            c.execute('CREATE TABLE evidence (key TEXT)')
        backup = self.root / 'backup'
        backup_product(backup, self.web, ledger, 'test')
        web, target = self.root / 'new-web', self.root / 'new-ledger'
        real_link = os.link
        def fail_second(source, destination):
            if destination == target:
                raise OSError('injected second publication failure')
            real_link(source, destination)
        with patch('harbor_web.runtime.os.link', side_effect=fail_second):
            with self.assertRaises(OSError):
                restore_product(backup, web, target, 'test')
        self.assertFalse(web.exists())
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob('*.restore-temporary')))

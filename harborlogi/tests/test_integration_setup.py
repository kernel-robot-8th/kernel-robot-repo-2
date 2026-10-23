import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class IntegrationSetupTests(unittest.TestCase):
    def test_pinned_sources_reject_wrong_revision_and_dirty_tracked_files(self):
        source = Path(__file__).resolve().parents[1] / 'tools/integration/real_fleet_http.py'
        self.assertTrue(source.is_file(), 'the integration launcher must verify its source revisions')
        spec = importlib.util.spec_from_file_location('port_integration_launcher', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            (root / 'source.txt').write_text('original')
            git('add', 'source.txt')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
            sha = git('rev-parse', 'HEAD')
            self.assertEqual(module.verify_checkout(root, sha), sha)
            for invalid in [sha[:12], '0' * 40]:
                with self.assertRaises(ValueError):
                    module.verify_checkout(root, invalid)
            (root / 'source.txt').write_text('changed')
            with self.assertRaises(ValueError):
                module.verify_checkout(root, sha)

    def test_app_in_a_repo_folder_verifies_the_containing_checkout(self):
        module = _launcher()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            app = root / 'harborlogi'
            app.mkdir()
            (app / 'source.txt').write_text('original')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
            self.assertEqual(module.app_checkout(app), git('rev-parse', 'HEAD'))
            (root / 'outside.txt').write_text('untracked')
            with self.assertRaises(ValueError):
                module.app_checkout(app)

    def test_harness_is_harbor_owned_and_not_imported_by_default(self):
        harness = Path(__file__).resolve().parents[1] / 'tools/integration/fleet_harness.py'
        tree = ast.parse(harness.read_text(encoding='utf-8'))
        self.assertTrue(any(isinstance(n, ast.ClassDef) and n.name == 'RealFleet' for n in tree.body))
        self.assertNotIn('fleet_harness', sys.modules)


def _launcher():
    source = Path(__file__).resolve().parents[1] / 'tools/integration/real_fleet_http.py'
    spec = importlib.util.spec_from_file_location('harbor_integration_launcher', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == '__main__':
    unittest.main()

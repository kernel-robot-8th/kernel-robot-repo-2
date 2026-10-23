import importlib.util
from pathlib import Path
import subprocess
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


if __name__ == '__main__':
    unittest.main()

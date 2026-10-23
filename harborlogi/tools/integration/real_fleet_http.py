"""Run Harbor over HTTP against the existing pinned Vault simulation harness.

Only this development process imports the harness and platform. The child Harbor
test process uses the public HTTP adapter and a temporary synthetic credential.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def verify_checkout(root, expected_sha):
    root = Path(root).resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise ValueError('A full pinned commit SHA is required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    actual = git('rev-parse', 'HEAD')
    if actual != expected_sha or Path(git('rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('Checkout differs from the requested source root or revision')
    if git('status', '--porcelain'):
        raise ValueError('Integration source checkout must be clean')
    return actual


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('platform', 'vault'):
        parser.add_argument(f'--{name}-root', type=Path, required=True)
        parser.add_argument(f'--{name}-sha', required=True)
    args = parser.parse_args(argv)
    app_root = Path(__file__).resolve().parents[2]
    app_sha = subprocess.check_output(['git', '-C', str(app_root), 'rev-parse', 'HEAD'], text=True).strip()
    verify_checkout(app_root, app_sha)
    platform_sha = verify_checkout(args.platform_root, args.platform_sha)
    vault_sha = verify_checkout(args.vault_root, args.vault_sha)
    source = args.vault_root / 'tools/integration/real_fleet.py'
    spec = importlib.util.spec_from_file_location('port_test_fleet', source)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    with tempfile.TemporaryDirectory(prefix='port-http-integration-') as temporary:
        fleet = harness.RealFleet(root=args.platform_root, workdir=Path(temporary) / 'fleet')
        try:
            fleet.start()
            ref = next(iter(fleet.transfers.values()))
            with fleet.client() as client:
                response = client.post('/api/fleet/cell-app/compile', json=ref)
                response.raise_for_status()
                artifact = response.json()['process_artifact_digest']
            config = {
                'schema': 'rosy.port-http-test/1', 'simulation': True,
                'origin': fleet.url, 'operator': fleet.tokens['operator'], 'viewer': fleet.tokens['viewer'],
                'approver': fleet.tokens['approver'],
                'platform_sha': platform_sha, 'vault_sha': vault_sha, 'app_sha': app_sha,
                'binding': {**ref, 'cargo_id': 'synthetic-cargo', 'source': 'source', 'destination': 'destination',
                            'workcell_id': harness.WORKCELL, 'instance_id': harness.INSTANCE,
                            'process_artifact_digest': artifact},
                'candidates': [harness.ROBOT], 'steps': [{'to': 'inbound'}],
                'alternate_steps': [{'to': 'outbound'}],
            }
            path = Path(temporary) / 'http-test.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            path.chmod(0o600)
            env = dict(os.environ, ROSY_PORT_INTEGRATION='1', ROSY_PORT_INTEGRATION_CONFIG=str(path))
            env.pop('PYTHONPATH', None)
            print(f'Platform {platform_sha}; Vault harness {vault_sha}; Harbor {app_sha}', flush=True)
            return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests/integration', '-v'],
                                  cwd=app_root, env=env, timeout=180).returncode
        finally:
            fleet.stop()


if __name__ == '__main__':
    raise SystemExit(main())

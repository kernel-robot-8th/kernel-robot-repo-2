"""Run Harbor over HTTP against a pinned platform through Harbor's own simulation harness.

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


def app_checkout(app_root):
    """Harbor lives in a folder of the team repo, so verify the whole containing checkout."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(app_root), *args], text=True).strip()
    return verify_checkout(git('rev-parse', '--show-toplevel'), git('rev-parse', 'HEAD'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform-root', type=Path, required=True)
    parser.add_argument('--platform-sha', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--transport-only', action='store_true', help='Verify neutral TRIP without the Cell compiler')
    mode.add_argument('--arm-only', action='store_true', help='Verify neutral ARM with an independent observer and real UDS')
    args = parser.parse_args(argv)
    app_root = Path(__file__).resolve().parents[2]
    app_sha = app_checkout(app_root)
    platform_sha = verify_checkout(args.platform_root, args.platform_sha)
    sys.path.insert(0, str(app_root))
    # Loaded by path only here: the harness needs the platform's test dependencies.
    source = Path(__file__).resolve().parent / ('arm_fleet_harness.py' if args.arm_only else 'fleet_harness.py')
    spec = importlib.util.spec_from_file_location('harbor_fleet_harness', source)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    # Windows keeps the in-process Fleet SQLite file open after stop; a leftover temp dir must not mask the result.
    with tempfile.TemporaryDirectory(prefix='harbor-http-integration-', ignore_cleanup_errors=True) as temporary:
        fleet = (harness.ArmFleet(root=args.platform_root, workdir=Path(temporary) / 'fleet') if args.arm_only else
                 harness.RealFleet(root=args.platform_root, workdir=Path(temporary) / 'fleet', transport_only=args.transport_only))
        try:
            fleet.start()
            ref, artifact = {}, None
            if not args.transport_only and not args.arm_only:
                ref = next(iter(fleet.transfers.values()))
                with fleet.client() as client:
                    response = client.post('/api/fleet/cell-app/compile', json=ref)
                    response.raise_for_status()
                    artifact = response.json()['process_artifact_digest']
            config = {
                'schema': 'harbor.transport-http-test/1' if args.transport_only else 'harbor.http-test/1', 'simulation': True,
                'origin': fleet.url, 'operator': fleet.tokens['operator'], 'viewer': fleet.tokens['viewer'],
                'approver': fleet.tokens['approver'],
                'platform_sha': platform_sha, 'app_sha': app_sha,
                'binding': {**ref, 'cargo_id': 'synthetic-cargo', 'source': 'source', 'destination': 'destination',
                            'workcell_id': harness.WORKCELL, 'instance_id': harness.INSTANCE,
                            'process_artifact_digest': artifact},
                'candidates': [harness.ROBOT], 'steps': [{'to': 'inbound'}],
                'alternate_steps': [{'to': 'outbound'}],
            }
            if args.arm_only:
                config = {'schema': 'harbor.arm-http-test/1', 'simulation': True, 'origin': fleet.url,
                    'operator': fleet.tokens['operator'], 'viewer': fleet.tokens['viewer'],
                    'platform_sha': platform_sha, 'app_sha': app_sha,
                    'binding': {'cargo_id': 'synthetic-outbound', 'source': 'yard', 'destination': 'stand',
                        'workcell_id': harness.WORKCELL, 'instance_id': harness.INSTANCE, 'object_ref': 'object-1',
                        'source_ref': 'source', 'target_ref': 'target'}}
            path = Path(temporary) / 'http-test.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            path.chmod(0o600)
            env = dict(os.environ, HARBOR_INTEGRATION='1', HARBOR_INTEGRATION_CONFIG=str(path))
            env.pop('PYTHONPATH', None)
            print(f'Platform {platform_sha}; Harbor (with harness) {app_sha}', flush=True)
            pattern = 'arm_http.py' if args.arm_only else 'transport_http.py' if args.transport_only else 'test_real_fleet_http.py'
            return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests/integration', '-p', pattern, '-v'],
                                  cwd=app_root, env=env, timeout=180).returncode
        finally:
            fleet.stop()


if __name__ == '__main__':
    raise SystemExit(main())

import hashlib
import json
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from harbor.workflow import BusinessLedger
from harbor.process import ArmTransferBinding
from harbor.rosy_http import RosyClient


class RecoveryTests(unittest.TestCase):
    def test_original_key_get_only_recovery_keeps_hold(self):
        with TemporaryDirectory() as directory:
            ledger = BusinessLedger(Path(directory) / 'ledger.db', placement_sources=('camera',), retreat_sources=('owner',))
            binding = ArmTransferBinding('C', 'bay', 'out', 'cell', 'instance', 'object', 'source', 'target')
            ledger.prepare('request', 1, (binding,), outbound_id='C', outbound_stand='out', move_arms=(('A',),))
            ledger.hold('request')
            calls = []
            transfer = {k: v for k, v in binding.proposal_request('request:0').items() if k != 'request_key'}
            job = dict(action_kind='ARM_TRANSFER', request_key='request:0', transfer=transfer,
                request_digest=hashlib.sha256(json.dumps(transfer, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                mission_id='mission-1', status='READY')
            def transport(method, path, body):
                calls.append((method, path))
                return {'job': job}
            client = RosyClient(transport=transport)
            ledger.recover_arm('request', client, now=datetime.now(timezone.utc))
            self.assertEqual(ledger.read('request')['state'], 'HOLD')
            self.assertEqual(ledger.read('request')['mission_id'], 'mission-1')
            self.assertEqual(calls, [('GET', '/api/fleet/arm-transfers/jobs?request_key=request%3A0')])
            job['mission_id'] = 'other-mission'
            with self.assertRaises(ValueError):
                ledger.recover_arm('request', client, now=datetime.now(timezone.utc))
            self.assertEqual(ledger.read('request')['mission_id'], 'mission-1')

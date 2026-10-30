"""Fleet physical proof (v1.330) becomes business evidence only when it binds to the verified receipt."""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from harbor.physical_evidence import fleet_move_evidence
from harbor.rosy_http import RosyClient
from harbor.workflow import BusinessLedger
from test_arm_transfer_execution import Fleet, binding
from test_execution import SOURCES
from test_policy import NOW

NS = int(NOW.timestamp()) * 1_000_000_000
ARMS = {'installed': 'A', 'partner': 'B'}


def owner(instance, sequence, **changes):
    return {'workcell_id': 'workcell', 'instance_id': instance, 'joints': {'sample_id': f'{instance}-{sequence}',
        'sequence': sequence, 'observed_at_ns': NS + sequence, 'joint_names': ['joint1', 'joint2'],
        'positions_rad': [0.1, -0.2]}, **changes}


def document(**owner_changes):
    samples = [{'sequence': s, 'object_observed_at_ns': NS + s,
                'owners': [owner('installed', s, **owner_changes), owner('partner', s)]} for s in (3, 4)]
    return json.dumps({'context': {'phase': 'POST_ACTION', 'action_id': 'action-1', 'attempt_id': 'attempt-1'},
                       'samples': samples}, sort_keys=True)


class ProofFleet(Fleet):
    def __init__(self, proof=None, zones=('workcell.shared',)):
        super().__init__()
        self.proof, self.zones = proof or document(), list(zones)
        self.receipt_changes = {'evidence_digest': sha256(self.proof.encode()).hexdigest()}

    def __call__(self, method, path, body):
        if path.endswith('/physical-evidence'):
            receipt = super().__call__('GET', path.replace('physical-evidence', 'physical-receipt'), None)['receipt']
            return {'receipt': receipt, 'evidence_document': self.proof, 'released_zone_ids': self.zones}
        return super().__call__(method, path, body)


class PhysicalEvidenceTests(unittest.TestCase):
    def confirmed(self, tmp, fleet):
        ledger = BusinessLedger(Path(tmp) / 'business.sqlite3', **SOURCES)
        ledger.prepare('outbound', 3, (binding(),), outbound_id='outbound', outbound_stand='stand',
                       move_arms=(('A', 'B'),))
        client = RosyClient(transport=fleet)
        ledger.submit('outbound', client, principal='harbor')
        fleet.status = 'GOAL_CONFIRMED'
        ledger.refresh('outbound', client, now=NOW + timedelta(seconds=1))
        return ledger, client

    def evidence(self, client, **overrides):
        return fleet_move_evidence(client, binding(), 'outbound:0', mission_id='physical-1', revision=3,
            arms=overrides.pop('arms', ARMS), placement_source='installed-camera', retreat_source='fleet-retreat')

    def test_bound_proof_closes_the_business_step(self):
        fleet = ProofFleet()
        with tempfile.TemporaryDirectory() as tmp:
            ledger, client = self.confirmed(tmp, fleet)
            evidence = self.evidence(client)
            self.assertEqual((evidence.placement.cargo_id, evidence.placement.location), ('outbound', 'stand'))
            self.assertLess(abs(evidence.placement.observed_at - NOW), timedelta(milliseconds=1))
            self.assertEqual({a.arm: a.joint_positions for a in evidence.retreat.arms}, {'A': (.1, -.2), 'B': (.1, -.2)})
            self.assertTrue(all(a.claim_released and a.outside_zone and a.pose_revision == 4 for a in evidence.retreat.arms))
            ledger.confirm('outbound', evidence, now=NOW + timedelta(seconds=1))
            self.assertEqual(ledger.read('outbound')['state'], 'AWAITING_FINAL_EVIDENCE')

    def test_unbound_or_incomplete_proof_never_becomes_evidence(self):
        tampered = ProofFleet()
        tampered.proof = document().replace('0.1', '0.3')
        cases = {'digest': tampered, 'zones': ProofFleet(zones=('a', 'b')),
                 'joints': ProofFleet(document(joints=None)),
                 'attempt': ProofFleet(document().replace('attempt-1', 'attempt-0'))}
        for name, fleet in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                _, client = self.confirmed(tmp, fleet)
                with self.assertRaises(ValueError):
                    self.evidence(client)
        with tempfile.TemporaryDirectory() as tmp:
            _, client = self.confirmed(tmp, ProofFleet())
            with self.assertRaises(ValueError):
                self.evidence(client, arms={'installed': 'A'})
            with self.assertRaises(ValueError):
                self.evidence(client, arms={'installed': 'A', 'partner': 'A'})

    def test_unconfirmed_job_reads_no_proof(self):
        fleet = ProofFleet()
        client = RosyClient(transport=fleet)
        fleet('POST', '/api/fleet/arm-transfers/jobs', binding().proposal_request('outbound:0'))
        with self.assertRaises(ValueError):
            self.evidence(client)
        self.assertFalse(any(path.endswith('/physical-evidence') for _, path, _ in fleet.calls))


if __name__ == '__main__':
    unittest.main()

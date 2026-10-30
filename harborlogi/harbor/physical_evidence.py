"""Business evidence for one ARM move from Fleet's stored physical proof (API v1.330, design 31 P2).

Both facts come from one authenticated Fleet proof bound to the confirmed attempt: the
latest object-pose sample (placement) and every participant's measured joints with the
zones that attempt released (retreat). Harbor adds no measurement or kinematics of its
own; the receipt's AT_TARGET_AND_CLEAR is the platform's clearance verdict. Whether these
two sources may close business steps is the installer's choice of registered source ids.
"""
from datetime import datetime, timezone
import hashlib
import json

from harbor.execution import read_arm_execution
from harbor.model import identifier
from harbor.workflow import ArmRetreat, MoveEvidence, PlacementEvidence, RetreatEvidence


def _utc(ns):
    if type(ns) is not int or not 0 <= ns < 2**63:
        raise ValueError('Physical proof requires exact source times')
    return datetime.fromtimestamp(ns / 1e9, timezone.utc)


def fleet_move_evidence(client, binding, request_key, *, mission_id, revision, arms,
                        placement_source, retreat_source):
    """arms maps an installed platform instance_id to its Harbor yard arm (A/B)."""
    execution = read_arm_execution(client, binding, request_key, mission_id=mission_id)
    receipt = execution.receipt
    if not execution.goal_confirmed or receipt is None:
        raise ValueError('Fleet has not confirmed this physical attempt')
    body = client.physical_evidence(mission_id)
    document, zones = body.get('evidence_document'), body.get('released_zone_ids')
    if (not isinstance(document, str) or body.get('receipt') != receipt
            or hashlib.sha256(document.encode()).hexdigest() != receipt['evidence_digest']):
        raise ValueError('Physical evidence does not match the verified receipt')
    # shortcut: one released shared zone per move; several zones need a per-arm zone map from the installation.
    if not isinstance(zones, list) or len(zones) != 1:
        raise ValueError('Expected exactly one released zone for the move')
    identifier(zones[0])
    proof = json.loads(document)
    context, samples = proof['context'], proof['samples']
    if (context['phase'] != 'POST_ACTION' or len(samples) != 2
            or (context['action_id'], context['attempt_id']) != (receipt['action_id'], receipt['attempt_id'])):
        raise ValueError('Physical proof is not the closure of the receipt attempt')
    latest = max(samples, key=lambda sample: sample['sequence'])
    attempt, base = receipt['attempt_id'], receipt['receipt_id']
    placement = PlacementEvidence(base + ':placement', mission_id, attempt, revision, placement_source,
                                  _utc(latest['object_observed_at_ns']), binding.cargo_id, binding.destination)
    facts = []
    for owner in latest['owners']:
        arm, joints = arms.get(owner['instance_id']), owner.get('joints')
        if arm is None or joints is None:
            raise ValueError('Physical proof names an unmapped arm or lacks measured joints')
        facts.append(ArmRetreat(arm, zones[0], True, tuple(float(v) for v in joints['positions_rad']),
                                _utc(joints['observed_at_ns']), joints['sequence'], True))
    if len({fact.arm for fact in facts}) != len(facts):
        raise ValueError('Two installed arms map to one Harbor arm')
    retreat = RetreatEvidence(base + ':retreat', mission_id, attempt, revision, retreat_source,
                              min(fact.pose_observed_at for fact in facts), tuple(facts))
    return MoveEvidence(placement, retreat)

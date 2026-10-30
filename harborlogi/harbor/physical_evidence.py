"""Business evidence for ARM moves from Fleet's measured physical proofs (design 31 P2).

A step uses the stored closure proof of its confirmed attempt (API v1.330). Final completion
uses a recheck of the outbound attempt (API v1.334, D-930): new samples measured after the
request, so moves made since then cannot hide behind the original proof. Each proof yields the
latest object-pose sample (placement) and every participant's measured joints with the
attempt's released zone (retreat). Harbor adds no measurement or kinematics of its own; Fleet's
target-pose and clearance checks are the platform verdict. Whether these sources may close
business steps is the installer's choice of registered source ids.
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


def _confirmed_receipt(client, binding, request_key, mission_id):
    execution = read_arm_execution(client, binding, request_key, mission_id=mission_id)
    if not execution.goal_confirmed or execution.receipt is None:
        raise ValueError('Fleet has not confirmed this physical attempt')
    return execution.receipt


def _evidence(binding, receipt, document, digest, zones, *, phase, base, mission_id, revision, arms,
              placement_source, retreat_source):
    if not isinstance(document, str) or hashlib.sha256(document.encode()).hexdigest() != digest:
        raise ValueError('Physical evidence bytes do not match their digest')
    # shortcut: one released shared zone per move; several zones need a per-arm zone map from the installation.
    if not isinstance(zones, list) or len(zones) != 1:
        raise ValueError('Expected exactly one released zone for the move')
    identifier(zones[0])
    proof = json.loads(document)
    context, samples = proof['context'], proof['samples']
    if (context['phase'] != phase or len(samples) != 2
            or (context['action_id'], context['attempt_id']) != (receipt['action_id'], receipt['attempt_id'])):
        raise ValueError('Physical proof does not belong to the receipt attempt')
    latest, attempt = max(samples, key=lambda sample: sample['sequence']), receipt['attempt_id']
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


def fleet_move_evidence(client, binding, request_key, *, mission_id, revision, arms,
                        placement_source, retreat_source):
    """arms maps an installed platform instance_id to its Harbor yard arm (A/B)."""
    receipt = _confirmed_receipt(client, binding, request_key, mission_id)
    body = client.physical_evidence(mission_id)
    if body.get('receipt') != receipt:
        raise ValueError('Physical evidence does not match the verified receipt')
    return _evidence(binding, receipt, body.get('evidence_document'), receipt['evidence_digest'],
                     body.get('released_zone_ids'), phase='POST_ACTION', base=receipt['receipt_id'],
                     mission_id=mission_id, revision=revision, arms=arms,
                     placement_source=placement_source, retreat_source=retreat_source)


def fleet_recheck_evidence(client, binding, request_key, recheck_key, *, mission_id, revision, arms,
                           placement_source, retreat_source):
    """Request (idempotently) and read a recheck; None while Fleet still waits for new samples."""
    receipt = _confirmed_receipt(client, binding, request_key, mission_id)
    recheck = client.request_recheck(mission_id, recheck_key).get('recheck')
    if (not isinstance(recheck, dict) or recheck.get('mission_id') != mission_id
            or recheck.get('recheck_key') != recheck_key
            or (recheck.get('action_id'), recheck.get('attempt_id')) != (receipt['action_id'], receipt['attempt_id'])):
        raise ValueError('Fleet recheck does not belong to the confirmed outbound attempt')
    if recheck.get('status') == 'WAITING_OBSERVATIONS':
        return None
    if recheck.get('status') != 'CONFIRMED':
        raise ValueError('Unknown Fleet recheck status')
    return _evidence(binding, receipt, recheck.get('evidence_document'), recheck.get('evidence_digest'),
                     recheck.get('zone_ids'), phase='RECHECK', base=f'{receipt["receipt_id"]}:recheck:{recheck_key}',
                     mission_id=mission_id, revision=revision, arms=arms,
                     placement_source=placement_source, retreat_source=retreat_source)

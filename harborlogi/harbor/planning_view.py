"""Deterministic synthetic inputs for the existing planner, never operational inventory."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from harbor.model import Bay, OutboundRequest, PortConstraints, YardSnapshot, identifier
from harbor.outbound import plan_outbound


SCENARIOS = frozenset({'normal', 'no-space', 'stale'})
REFERENCE_TIME = datetime(2026, 10, 21, tzinfo=timezone.utc)


def preview(cargo, scenario):
    identifier(cargo)
    if len(cargo) > 96 or any(ord(c) < 32 for c in cargo) or scenario not in SCENARIOS:
        raise ValueError('Invalid preview input')
    snapshot = YardSnapshot(3, REFERENCE_TIME - timedelta(seconds=6 if scenario == 'stale' else 0), (
        Bay('yard', ('C1', 'C2', 'C3') if scenario == 'no-space' else ('C1', 'C2'), 3),
        Bay('temp', (), 1 if scenario == 'no-space' else 2), Bay('stand', (), 1),
    ))
    constraints = PortConstraints('stand', ('temp',), frozenset({
        ('yard', 'temp'), ('temp', 'yard'), ('yard', 'stand'),
    }), timedelta(seconds=5), 'conveyor', frozenset({
        ('A', 'yard'), ('A', 'temp'), ('A', 'stand'), ('B', 'yard'), ('B', 'stand'),
    }))
    proposal = plan_outbound(snapshot, OutboundRequest(cargo, snapshot.revision), constraints, now=REFERENCE_TIME)
    moves, projected = [], None
    if proposal.status == 'READY':
        outbound_index = next(i for i, move in enumerate(proposal.moves) if move.cargo_id == cargo)
        positions = {bay.bay_id: list(bay.cargo) for bay in snapshot.bays}
        for index, move in enumerate(proposal.moves):
            if positions[move.source].pop() != move.cargo_id:
                raise ValueError('Proposal does not match its original stack')
            positions[move.destination].append(move.cargo_id)
            moves.append({**asdict(move), 'phase': ('REHANDLE' if index < outbound_index else
                                                 'OUTBOUND' if index == outbound_index else 'RESTORE')})
        projected = [dict(bay_id=bay.bay_id, cargo=positions[bay.bay_id], capacity=bay.capacity) for bay in snapshot.bays]
    return dict(mode='demo', scenario=scenario, cargo_id=cargo, source_id='synthetic-top-camera',
                observed_at=snapshot.observed_at.isoformat(), reference_time=REFERENCE_TIME.isoformat(),
                revision=snapshot.revision, max_observation_age_seconds=5,
                candidates=[item for bay in snapshot.bays for item in bay.cargo],
                reach_source='synthetic-installer-table',
                observed_bays=[dict(bay_id=bay.bay_id, cargo=list(bay.cargo), capacity=bay.capacity) for bay in snapshot.bays],
                projected_bays=projected,
                proposal=dict(status=proposal.status, reason=proposal.reason, source_revision=proposal.source_revision,
                              moves=moves, required_completion=list(proposal.required_completion)))

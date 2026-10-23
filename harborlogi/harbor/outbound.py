from dataclasses import dataclass
from datetime import datetime, timedelta

from harbor.model import PortConstraints, OutboundRequest, YardSnapshot, aware_time


@dataclass(frozen=True)
class CargoMove:
    cargo_id: str
    source: str
    destination: str


@dataclass(frozen=True)
class OutboundProposal:
    status: str
    reason: str
    source_revision: int
    moves: tuple[CargoMove, ...] = ()
    required_completion: tuple[str, ...] = (
        "outbound_placed_on_stand", "rehandles_restored", "arms_retreated",
    )


def plan_outbound(snapshot: YardSnapshot, request: OutboundRequest,
                   constraints: PortConstraints, *, now: datetime) -> OutboundProposal:
    """Propose inventory moves without changing observations or granting execution."""
    aware_time(now)
    bays = {s.bay_id: s for s in snapshot.bays}
    referenced = {constraints.outbound_stand, *constraints.temporary_bays}
    referenced.update(s for edge in constraints.allowed_moves for s in edge)
    if not referenced <= bays.keys():
        raise ValueError("Constraints reference an unknown bay")

    def hold(reason):
        return OutboundProposal("HOLD", reason, snapshot.revision)

    if snapshot.revision != request.expected_revision:
        return hold("REVISION_CONFLICT")
    if not timedelta(0) <= now - snapshot.observed_at <= constraints.max_observation_age:
        return hold("OBSERVATION_NOT_CURRENT")
    if snapshot.holdings:
        return hold("PHYSICAL_HOLDING_REQUIRES_RECONCILIATION")
    source = next((s for s in snapshot.bays if request.cargo_id in s.cargo), None)
    if source is None:
        return hold("OUTBOUND_NOT_OBSERVED")
    if source.bay_id == constraints.outbound_stand:
        return hold("OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE")
    output = bays[constraints.outbound_stand]
    if len(output.cargo) >= output.capacity:
        return hold("OUTBOUND_STAND_FULL")
    if (source.bay_id, output.bay_id) not in constraints.allowed_moves:
        return hold("OUTBOUND_MOVE_UNAVAILABLE")

    planned = {s.bay_id: list(s.cargo) for s in snapshot.bays}
    moves = []
    displaced = []
    while planned[source.bay_id][-1] != request.cargo_id:
        temporary = next((s for s in constraints.temporary_bays
                          if s != source.bay_id
                          and len(planned[s]) < bays[s].capacity
                          and (source.bay_id, s) in constraints.allowed_moves
                          and (s, source.bay_id) in constraints.allowed_moves), None)
        if temporary is None:
            return hold("NO_LEGAL_TEMPORARY_BAY")
        cargo = planned[source.bay_id].pop()
        planned[temporary].append(cargo)
        moves.append(CargoMove(cargo, source.bay_id, temporary))
        displaced.append((cargo, temporary))
    moves.append(CargoMove(request.cargo_id, source.bay_id, output.bay_id))
    moves.extend(CargoMove(cargo, temporary, source.bay_id)
                 for cargo, temporary in reversed(displaced))
    return OutboundProposal("READY", "PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE",
                             snapshot.revision, tuple(moves))

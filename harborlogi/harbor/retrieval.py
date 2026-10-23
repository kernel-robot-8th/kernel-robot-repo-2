from dataclasses import dataclass
from datetime import datetime, timedelta

from harbor.model import PortConstraints, RetrievalRequest, YardSnapshot, aware_time


@dataclass(frozen=True)
class CargoMove:
    cargo_id: str
    source: str
    destination: str


@dataclass(frozen=True)
class RetrievalProposal:
    status: str
    reason: str
    source_revision: int
    moves: tuple[CargoMove, ...] = ()
    required_completion: tuple[str, ...] = (
        "target_unloaded_by_supply", "blockers_restored", "arms_clear",
    )


def plan_retrieval(snapshot: YardSnapshot, request: RetrievalRequest,
                   constraints: PortConstraints, *, now: datetime) -> RetrievalProposal:
    """Propose inventory moves without changing observations or granting execution."""
    aware_time(now)
    slots = {s.slot_id: s for s in snapshot.slots}
    referenced = {constraints.supply_slot, *constraints.temporary_slots}
    referenced.update(s for edge in constraints.transfers for s in edge)
    if not referenced <= slots.keys():
        raise ValueError("Constraints reference an unknown slot")

    def hold(reason):
        return RetrievalProposal("HOLD", reason, snapshot.revision)

    if snapshot.revision != request.expected_revision:
        return hold("REVISION_CONFLICT")
    if not timedelta(0) <= now - snapshot.observed_at <= constraints.max_observation_age:
        return hold("OBSERVATION_NOT_CURRENT")
    if snapshot.holdings:
        return hold("PHYSICAL_HOLDING_REQUIRES_RECONCILIATION")
    source = next((s for s in snapshot.slots if request.cargo_id in s.cargo), None)
    if source is None:
        return hold("TARGET_NOT_OBSERVED")
    if source.slot_id == constraints.supply_slot:
        return hold("TARGET_ALREADY_AT_SUPPLY_REQUIRES_EVIDENCE")
    output = slots[constraints.supply_slot]
    if len(output.cargo) >= output.capacity:
        return hold("SUPPLY_FULL")
    if (source.slot_id, output.slot_id) not in constraints.transfers:
        return hold("TARGET_TRANSFER_UNAVAILABLE")

    planned = {s.slot_id: list(s.cargo) for s in snapshot.slots}
    moves = []
    displaced = []
    while planned[source.slot_id][-1] != request.cargo_id:
        temporary = next((s for s in constraints.temporary_slots
                          if s != source.slot_id
                          and len(planned[s]) < slots[s].capacity
                          and (source.slot_id, s) in constraints.transfers
                          and (s, source.slot_id) in constraints.transfers), None)
        if temporary is None:
            return hold("NO_LEGAL_TEMPORARY_SLOT")
        cargo = planned[source.slot_id].pop()
        planned[temporary].append(cargo)
        moves.append(CargoMove(cargo, source.slot_id, temporary))
        displaced.append((cargo, temporary))
    moves.append(CargoMove(request.cargo_id, source.slot_id, output.slot_id))
    moves.extend(CargoMove(cargo, temporary, source.slot_id)
                 for cargo, temporary in reversed(displaced))
    return RetrievalProposal("READY", "PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE",
                             snapshot.revision, tuple(moves))

"""Retrieval order inside one priority tier, found by simulating each order on the observed yard (KAN-116).

Each candidate order is played out with the existing single-retrieval planner: the planned moves are
applied, the retrieved container leaves the yard, and the next container is planned on the result.
Orders are compared by feasibility, total moves, rehandles, then the order they were given in. Small
tiers are searched exhaustively; larger ones take the cheapest next retrieval at each step.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import permutations

from harbor.model import OutboundRequest
from harbor.placement import plan_placement
from harbor.spatial import CargoShape, Placement, SpatialMove, SpatialPolicy, SpatialSnapshot, plan_spatial

EXACT_LIMIT = 4  # 4! = 24 complete simulations per tier


@dataclass(frozen=True)
class ScheduleEntry:
    kind: str  # 'OUTBOUND' or 'INBOUND'
    cargo_id: str
    shape: CargoShape | None = None
    reserved_cells: frozenset[tuple[str, int, int, int]] = frozenset()


@dataclass(frozen=True)
class ScheduleStep:
    cargo_id: str
    kind: str
    status: str
    reason: str
    move_count: int = 0
    rehandle_count: int = 0
    moves: tuple[SpatialMove, ...] = field(default=(), compare=False, repr=False)
    recommendation: Placement | None = None
    search_complete: bool | None = field(default=None, compare=False, repr=False)
    budget_reason: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ScheduleScenario:
    entries: tuple[ScheduleEntry, ...]
    feasible: bool
    total_moves: int
    rehandles: int
    steps: tuple[ScheduleStep, ...]
    final: SpatialSnapshot | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class RetrievalStep:
    cargo_id: str
    status: str
    reason: str
    move_count: int = 0
    rehandle_count: int = 0
    moves: tuple[SpatialMove, ...] = field(default=(), compare=False, repr=False)
    search_complete: bool | None = field(default=None, compare=False, repr=False)
    budget_reason: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class RetrievalScenario:
    order: tuple[str, ...]
    feasible: bool
    total_moves: int
    rehandles: int
    steps: tuple[RetrievalStep, ...]
    exhaustive: bool
    final: SpatialSnapshot | None = field(default=None, compare=False, repr=False)


def _after(snapshot, proposal, target):
    """The yard after one READY retrieval: moved cargo at their last destination, the target gone."""
    final = {p.cargo_id: p for p in snapshot.placements}
    for move in proposal.moves:
        final[move.cargo_id] = move.destination_placement
    final.pop(target, None)
    return replace(snapshot, shapes=tuple(s for s in snapshot.shapes if s.cargo_id != target),
                   placements=tuple(final.values()))


def _retrieve(snapshot, policy, code, *, now, ranks):
    proposal = plan_spatial(snapshot, OutboundRequest(code, snapshot.revision), policy, now=now, schedule=ranks)
    opt = proposal.optimization
    complete = None if opt is None else opt.search_complete
    budget = None if opt is None else opt.budget_reason
    if proposal.status != 'READY':
        return RetrievalStep(code, proposal.status, proposal.reason,
                             search_complete=complete, budget_reason=budget), None
    rehandles = next(i for i, move in enumerate(proposal.moves) if move.cargo_id == code)
    return RetrievalStep(code, 'READY', proposal.reason, len(proposal.moves), rehandles,
                         moves=proposal.moves, search_complete=complete, budget_reason=budget), _after(snapshot, proposal, code)


def _play(snapshot, policy, order, *, now, schedule, group, exhaustive):
    current, steps = snapshot, []
    ranks = dict(schedule)
    ranks.update({code: (group, position) for position, code in enumerate(order)})  # the simulated order is the tier's sequence
    for code in order:
        step, after = _retrieve(current, policy, code, now=now, ranks=ranks)
        steps.append(step)
        if after is None:
            return RetrievalScenario(tuple(order), False, sum(s.move_count for s in steps),
                                     sum(s.rehandle_count for s in steps), tuple(steps), exhaustive)
        current = after
        ranks.pop(code)
    return RetrievalScenario(tuple(order), True, sum(s.move_count for s in steps),
                             sum(s.rehandle_count for s in steps), tuple(steps), exhaustive, current)


def _greedy(snapshot, policy, codes, *, now, schedule, group, first=None):
    """Cheapest next retrieval at each step; ties keep the given order. `first` forces the opening pick."""
    remaining, order = list(codes), []
    current, ranks = snapshot, dict(schedule)
    ranks.update({code: (group, position) for position, code in enumerate(codes)})
    while remaining:
        options = []
        for code in ([first] if first and not order else remaining):
            step, after = _retrieve(current, policy, code, now=now, ranks=ranks)
            options.append((after is None, step.move_count, codes.index(code), code, after))
        blocked, _, _, code, after = min(options, key=lambda option: option[:3])
        order.append(code)
        remaining.remove(code)
        if blocked:
            order.extend(remaining)
            break
        current = after
        ranks.pop(code)
    return _play(snapshot, policy, order, now=now, schedule=schedule, group=group, exhaustive=False)


def _key(codes):
    return lambda s: (not s.feasible, s.total_moves, s.rehandles, [codes.index(code) for code in s.order])


def evaluate_order(snapshot: SpatialSnapshot, policy: SpatialPolicy, order: tuple[str, ...] | list[str], *,
                   now: datetime, schedule: dict | None = None, group: int = 1) -> RetrievalScenario:
    """Simulate retrieving `order` sequentially on `snapshot` without mutating persisted state."""
    codes = tuple(order)
    if len(set(codes)) != len(codes):
        raise ValueError('Retrieval order must not contain duplicate cargo')
    return _play(snapshot, policy, codes, now=now, schedule=dict(schedule or {}), group=group, exhaustive=False)


def _placement_cells(placement: Placement, length_cells: int) -> frozenset[tuple[str, int, int, int]]:
    return frozenset(
        (
            placement.area_id,
            placement.row + (n if placement.orientation == 90 else 0),
            placement.column + (n if placement.orientation == 0 else 0),
            placement.level,
        )
        for n in range(length_cells)
    )


def evaluate_schedule(snapshot: SpatialSnapshot, policy: SpatialPolicy,
                      entries: tuple[ScheduleEntry, ...] | list[ScheduleEntry], *,
                      now: datetime, schedule: dict[str, tuple[int, int | None]] | None = None,
                      reserved_cells: frozenset[tuple[str, int, int, int]] = frozenset()) -> ScheduleScenario:
    """Simulate a mixed inbound/outbound schedule sequentially using the domain planners without mutating state."""
    seq = tuple(entries)
    current = snapshot
    ranks = dict(schedule or {})
    steps: list[ScheduleStep] = []
    base_reserved = frozenset(reserved_cells)
    prior_inbound_cells: dict[str, frozenset[tuple[str, int, int, int]]] = {}
    inbound_lengths: dict[str, int] = {}
    for entry in seq:
        if entry.kind == 'OUTBOUND':
            step, after = _retrieve(current, policy, entry.cargo_id, now=now, ranks=ranks)
            steps.append(ScheduleStep(
                cargo_id=step.cargo_id,
                kind='OUTBOUND',
                status=step.status,
                reason=step.reason,
                move_count=step.move_count,
                rehandle_count=step.rehandle_count,
                moves=step.moves,
                search_complete=step.search_complete,
                budget_reason=step.budget_reason,
            ))
            if after is None:
                return ScheduleScenario(
                    seq, False, sum(s.move_count for s in steps), sum(s.rehandle_count for s in steps), tuple(steps)
                )
            for move in step.moves:
                if move.cargo_id in prior_inbound_cells:
                    prior_inbound_cells[move.cargo_id] = _placement_cells(
                        move.destination_placement, inbound_lengths[move.cargo_id]
                    )
            prior_inbound_cells.pop(entry.cargo_id, None)
            inbound_lengths.pop(entry.cargo_id, None)
            current = after
            ranks.pop(entry.cargo_id, None)
        elif entry.kind == 'INBOUND':
            if entry.shape is None:
                steps.append(ScheduleStep(entry.cargo_id, 'INBOUND', 'HOLD', 'INCOMING_FOOTPRINT_REQUIRED'))
                return ScheduleScenario(
                    seq, False, sum(s.move_count for s in steps), sum(s.rehandle_count for s in steps), tuple(steps)
                )
            step_reserved = base_reserved | entry.reserved_cells
            if prior_inbound_cells:
                step_reserved = step_reserved.union(*prior_inbound_cells.values())
            proposal = plan_placement(current, entry.shape, policy, ranks, now=now, reserved_cells=step_reserved)
            if proposal.status != 'READY' or proposal.recommendation is None:
                steps.append(ScheduleStep(entry.cargo_id, 'INBOUND', proposal.status, proposal.reason))
                return ScheduleScenario(
                    seq, False, sum(s.move_count for s in steps), sum(s.rehandle_count for s in steps), tuple(steps)
                )
            rec = proposal.recommendation
            if rec.area_id not in policy.storage_areas:
                steps.append(ScheduleStep(entry.cargo_id, 'INBOUND', 'HOLD', 'NO_LEGAL_PLACEMENT'))
                return ScheduleScenario(
                    seq, False, sum(s.move_count for s in steps), sum(s.rehandle_count for s in steps), tuple(steps)
                )
            steps.append(ScheduleStep(
                cargo_id=entry.cargo_id,
                kind='INBOUND',
                status='READY',
                reason=proposal.reason,
                move_count=len(proposal.moves),
                rehandle_count=0,
                recommendation=rec,
            ))
            prior_inbound_cells[entry.cargo_id] = _placement_cells(rec, entry.shape.length_cells)
            inbound_lengths[entry.cargo_id] = entry.shape.length_cells
            current = replace(
                current,
                shapes=current.shapes + (entry.shape,),
                placements=current.placements + (rec,),
            )
        else:
            raise ValueError(f'Unsupported schedule entry kind: {entry.kind}')
    return ScheduleScenario(
        seq, True, sum(s.move_count for s in steps), sum(s.rehandle_count for s in steps), tuple(steps), current
    )


def rank_retrieval_orders(snapshot: SpatialSnapshot, policy: SpatialPolicy, codes: tuple[str, ...], *, now: datetime,
                          schedule: dict, group: int, keep: int = 2, exact_limit: int = EXACT_LIMIT) -> list[RetrievalScenario]:
    """Best `keep` retrieval orders of `codes`; `schedule` ranks every other cargo as the planner expects."""
    codes = tuple(codes)
    if len(codes) <= exact_limit:
        ranked = sorted((_play(snapshot, policy, order, now=now, schedule=schedule, group=group, exhaustive=True)
                         for order in permutations(codes)), key=_key(codes))
        return ranked[:keep]
    best = _greedy(snapshot, policy, codes, now=now, schedule=schedule, group=group)
    ranks = dict(schedule)
    ranks.update({code: (group, position) for position, code in enumerate(codes)})
    openings = []
    for code in codes:  # alternatives open with the next cheapest container, then continue greedily
        step, after = _retrieve(snapshot, policy, code, now=now, ranks=ranks)
        openings.append((after is None, step.move_count, codes.index(code), code))
    others = [_greedy(snapshot, policy, codes, now=now, schedule=schedule, group=group, first=code)
              for *_, code in sorted(openings) if code != best.order[0]][:keep - 1]
    return sorted([best, *others], key=_key(codes))[:keep]

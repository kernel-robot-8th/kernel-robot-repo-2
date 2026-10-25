"""Immutable logical footprints and bounded, atomic outbound proposals."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from harbor.layout import YardLayout
from harbor.model import (YARD_ARMS, CargoHolding, OutboundRequest, aware_time,
                          identifier, identifiers, revision)


SPATIAL_SEARCH_LIMIT = 20_000
SPATIAL_CANDIDATE_LIMIT = 20_000


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Expected an integer within the configured bounds')


@dataclass(frozen=True)
class CargoShape:
    cargo_id: str
    length_cells: int
    footprint_revision: int

    def __post_init__(self):
        identifier(self.cargo_id)
        _integer(self.length_cells, 1, 3)
        if type(self.footprint_revision) is not int or self.footprint_revision < 1:
            raise ValueError('Footprint revision must be positive')


@dataclass(frozen=True)
class Placement:
    cargo_id: str
    area_id: str
    row: int
    column: int
    level: int
    orientation: int = 0

    def __post_init__(self):
        identifier(self.cargo_id)
        identifier(self.area_id)
        for n, limit in ((self.row, 12), (self.column, 12), (self.level, 6)):
            _integer(n, 1, limit)
        if type(self.orientation) is not int or self.orientation not in (0, 90):
            raise ValueError('Orientation must be 0 or 90')


@dataclass(frozen=True)
class Area:
    area_id: str
    rows: int
    columns: int
    levels: int

    def __post_init__(self):
        identifier(self.area_id)
        for n, limit in ((self.rows, 12), (self.columns, 12), (self.levels, 6)):
            _integer(n, 1, limit)


@dataclass(frozen=True)
class SpatialSnapshot:
    revision: int
    observed_at: datetime
    layout: YardLayout
    areas: tuple[Area, ...]
    shapes: tuple[CargoShape, ...]
    placements: tuple[Placement, ...]
    holdings: tuple[CargoHolding, ...] = ()

    def __post_init__(self):
        revision(self.revision)
        aware_time(self.observed_at)
        if not isinstance(self.layout, YardLayout):
            raise ValueError('Expected a yard layout')
        for values, kind in ((self.areas, Area), (self.shapes, CargoShape),
                             (self.placements, Placement), (self.holdings, CargoHolding)):
            if not isinstance(values, tuple) or not all(isinstance(v, kind) for v in values):
                raise ValueError('Expected immutable spatial observations')
        identifiers(tuple(a.area_id for a in self.areas))
        identifiers(tuple(s.cargo_id for s in self.shapes))
        identifiers(tuple(p.cargo_id for p in self.placements) + tuple(h.cargo_id for h in self.holdings))
        identifiers(tuple(h.holder_id for h in self.holdings if h.holder_id is not None))
        areas = {a.area_id: a for a in self.areas}
        if len(areas) > 16 or sum(a.rows*a.columns*a.levels for a in self.areas) > 4096:
            raise ValueError('Map workload exceeds the configured bounds')
        if len(self.placements) + len(self.holdings) > 256:
            raise ValueError('Observation exceeds the cargo limit')
        if areas.get('yard') != Area('yard', self.layout.rows, self.layout.columns, self.layout.levels):
            raise ValueError('The yard dimensions must match its layout')
        if (areas.keys() - {'yard'}) & {c.bay_id for c in self.layout.cells}:
            raise ValueError('Auxiliary areas must not alias primary yard cell identifiers')
        placed = {p.cargo_id for p in self.placements}
        shaped = {s.cargo_id for s in self.shapes}
        if not placed <= shaped <= placed | {h.cargo_id for h in self.holdings}:
            raise ValueError('Every placement requires a pinned shape; unrelated shapes are invalid')
        occupied = {}
        for p in self.placements:
            if p.area_id not in areas:
                raise ValueError('Unknown placement area')
            area = areas[p.area_id]
            for cell in self.cells(p):
                _, row, column, level = cell
                if row > area.rows or column > area.columns or level > area.levels:
                    raise ValueError('Footprint extends outside its area')
                if cell in occupied:
                    raise ValueError('Cargo footprints overlap')
                occupied[cell] = p.cargo_id
        if any(level > 1 and (area, row, column, level-1) not in occupied
               for area, row, column, level in occupied):
            raise ValueError('Cargo footprint requires full support')

    def shape(self, cargo_id):
        return next(s for s in self.shapes if s.cargo_id == cargo_id)

    def placement(self, cargo_id):
        return next(p for p in self.placements if p.cargo_id == cargo_id)

    def cells(self, placement):
        return frozenset((placement.area_id, placement.row + (n if placement.orientation == 90 else 0),
                          placement.column + (n if placement.orientation == 0 else 0), placement.level)
                         for n in range(self.shape(placement.cargo_id).length_cells))

    def occupied(self):
        return {cell: p.cargo_id for p in self.placements for cell in self.cells(p)}

    def with_placements(self, placements):
        return replace(self, placements=placements)

    def position_id(self, placement):
        if placement.area_id == 'yard':
            return f'{self.layout.at(placement.row, placement.column).bay_id}-L{placement.level}'
        return f'{placement.area_id}-R{placement.row}C{placement.column}-L{placement.level}'


@dataclass(frozen=True)
class SpatialPolicy:
    outbound_area: str
    temporary_areas: tuple[str, ...]
    allowed_moves: frozenset[tuple[str, str]]
    arm_reach: frozenset[tuple[str, str]]
    max_observation_age: timedelta
    storage_areas: tuple[str, ...] = ('yard',)

    def __post_init__(self):
        identifier(self.outbound_area)
        identifiers(self.temporary_areas)
        identifiers(self.storage_areas)
        if 'yard' not in self.storage_areas or set(self.storage_areas) & {*self.temporary_areas, self.outbound_area, 'conveyor'}:
            raise ValueError('Storage, temporary and outbound roles must be disjoint')
        if self.outbound_area == 'yard' or any(a in ('yard', 'conveyor', self.outbound_area)
                                               for a in self.temporary_areas):
            raise ValueError('Temporary storage must exclude yard, conveyor and outbound areas')
        if not isinstance(self.allowed_moves, frozenset) or not isinstance(self.arm_reach, frozenset):
            raise ValueError('Expected immutable movement and reach pairs')
        for pair in self.allowed_moves | self.arm_reach:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError('Expected pairs')
            for item in pair:
                identifier(item)
        if any(arm not in YARD_ARMS for arm, _ in self.arm_reach):
            raise ValueError('Expected verified yard arms')
        if not isinstance(self.max_observation_age, timedelta) or self.max_observation_age <= timedelta(0):
            raise ValueError('Observation freshness must be positive')


@dataclass(frozen=True)
class SpatialMove:
    cargo_id: str
    source: str
    destination: str
    primary_arm: str
    alternate_arms: tuple[str, ...]
    source_placement: Placement
    destination_placement: Placement
    phase_label: str
    footprint_revision: int


@dataclass(frozen=True)
class SpatialProposal:
    status: str
    reason: str
    source_revision: int
    moves: tuple[SpatialMove, ...] = ()
    required_completion: tuple[str, ...] = (
        'outbound_placed_on_stand', 'rehandles_restored', 'arms_retreated',
    )


def plan_spatial(snapshot: SpatialSnapshot, request: OutboundRequest, policy: SpatialPolicy,
                 *, now: datetime) -> SpatialProposal:
    """Search complete legal plans; exhausted or impossible searches emit no moves."""
    aware_time(now)
    areas = {a.area_id: a for a in snapshot.areas}
    referenced = {policy.outbound_area, *policy.temporary_areas, *policy.storage_areas}
    referenced.update(a for edge in policy.allowed_moves for a in edge)
    if not referenced <= areas.keys():
        raise ValueError('Policy references an unknown area')

    def hold(reason):
        return SpatialProposal('HOLD', reason, snapshot.revision)

    if request.expected_revision != snapshot.revision:
        return hold('REVISION_CONFLICT')
    if not timedelta(0) <= now-snapshot.observed_at <= policy.max_observation_age:
        return hold('OBSERVATION_NOT_CURRENT')
    if snapshot.holdings:
        return hold('PHYSICAL_HOLDING_REQUIRES_RECONCILIATION')
    if request.cargo_id not in {p.cargo_id for p in snapshot.placements}:
        return hold('OUTBOUND_NOT_OBSERVED')
    target = snapshot.placement(request.cargo_id)
    if target.area_id == policy.outbound_area:
        return hold('OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE')
    if target.area_id not in policy.storage_areas:
        return hold('OUTBOUND_NOT_IN_A_BAY')
    if not policy.arm_reach:
        return hold('ARM_REACH_UNVERIFIED')

    # Every directly supported cargo joins the closure, even across another support column.
    occupied = snapshot.occupied()
    dependencies = {target.cargo_id}
    pending = [target.cargo_id]
    while pending:
        cargo = pending.pop()
        for area, row, column, level in snapshot.cells(snapshot.placement(cargo)):
            above = occupied.get((area, row, column, level+1))
            if above is not None and above not in dependencies:
                dependencies.add(above)
                pending.append(above)
    blockers = dependencies - {target.cargo_id}
    originals = {p.cargo_id: p for p in snapshot.placements}
    visited = set()
    count = 0
    candidates = 0

    def reach(p):
        if p.area_id != 'yard':
            return {p.area_id}
        return {snapshot.layout.at(r, c).bay_id for _, r, c, _ in snapshot.cells(p)}

    def destinations(current, cargo, area_ids, restore):
        nonlocal candidates
        origin = current.placement(cargo)
        choices = []
        for area_id in area_ids:
            if (origin.area_id, area_id) not in policy.allowed_moves:
                continue
            area = areas[area_id]
            for level in range(1, area.levels+1):
                for row in range(1, area.rows+1):
                    for column in range(1, area.columns+1):
                        candidates += 1
                        if candidates > SPATIAL_CANDIDATE_LIMIT:
                            raise SearchLimit
                        choices.append(Placement(cargo, area_id, row, column, level, origin.orientation))
        if restore:
            original = originals[cargo]
            choices.sort(key=lambda p: (p != original,
                                       p.area_id != original.area_id,
                                       (p.row, p.column) != (original.row, original.column),
                                       p.level, p.row, p.column))
        return choices

    class SearchLimit(Exception):
        pass

    def search(current, remaining, restoring, exported):
        nonlocal count
        count += 1
        if count > SPATIAL_SEARCH_LIMIT:
            raise SearchLimit
        if exported and not restoring:
            return ()
        key = (current.placements, remaining, restoring, exported)
        if key in visited:
            return None
        visited.add(key)
        occupied = current.occupied()
        if remaining:
            tasks = [(cargo, policy.temporary_areas, '재취급') for cargo in sorted(remaining)]
        elif not exported:
            tasks = [(target.cargo_id, (policy.outbound_area,), '반출')]
        else:
            tasks = [(cargo, policy.storage_areas, '복귀') for cargo in sorted(restoring)]
        for cargo, area_ids, phase in tasks:
            origin = current.placement(cargo)
            if any((a, r, c, l+1) in occupied for a, r, c, l in current.cells(origin)):
                continue
            for destination in destinations(current, cargo, area_ids, exported):
                try:
                    projected = current.with_placements(tuple(destination if p.cargo_id == cargo else p
                                                               for p in current.placements))
                except ValueError:
                    continue
                locations = reach(origin) | reach(destination)
                arms = tuple(a for a in YARD_ARMS if all((a, loc) in policy.arm_reach for loc in locations))
                if not arms:
                    continue
                result = search(projected, remaining-{cargo},
                                restoring-{cargo} if exported else restoring, exported or cargo == target.cargo_id)
                if result is not None:
                    move = SpatialMove(cargo, current.position_id(origin), current.position_id(destination),
                                       arms[0], arms[1:], origin, destination, phase,
                                       snapshot.shape(cargo).footprint_revision)
                    return (move,) + result
        return None

    try:
        moves = search(snapshot, frozenset(blockers), frozenset(blockers), False)
    except SearchLimit:
        return hold('SPATIAL_SEARCH_LIMIT_REACHED')
    if moves is None:
        return hold('NO_LEGAL_SPATIAL_PLAN')
    return SpatialProposal('READY', 'PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE', snapshot.revision, moves)

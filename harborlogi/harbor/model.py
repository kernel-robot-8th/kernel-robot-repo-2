from dataclasses import dataclass
from datetime import datetime, timedelta

YARD_ARMS = ("A", "B")  # Platform-owner arms Harbor plans for; supply arm S is an external step.


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Expected a nonempty identifier without surrounding whitespace")


def identifiers(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise ValueError("Expected immutable identifiers")
    for value in values:
        identifier(value)
    if len(values) != len(set(values)):
        raise ValueError("Duplicate identifier")


def revision(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative revision")


def aware_time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Expected a timezone-aware timestamp")


@dataclass(frozen=True)
class Bay:
    bay_id: str
    cargo: tuple[str, ...]  # Bottom to top; no empty layers.
    capacity: int

    def __post_init__(self):
        identifier(self.bay_id)
        identifiers(self.cargo)
        if type(self.capacity) is not int or self.capacity < 1 or len(self.cargo) > self.capacity:
            raise ValueError("Invalid stack capacity")


@dataclass(frozen=True)
class CargoHolding:
    cargo_id: str
    holder_id: str | None

    def __post_init__(self):
        identifier(self.cargo_id)
        if self.holder_id is not None:
            identifier(self.holder_id)


@dataclass(frozen=True)
class YardSnapshot:
    revision: int
    observed_at: datetime
    bays: tuple[Bay, ...]
    holdings: tuple[CargoHolding, ...] = ()

    def __post_init__(self):
        revision(self.revision)
        aware_time(self.observed_at)
        if (not isinstance(self.bays, tuple) or not self.bays
                or not all(isinstance(s, Bay) for s in self.bays)
                or not isinstance(self.holdings, tuple)
                or not all(isinstance(h, CargoHolding) for h in self.holdings)):
            raise ValueError("Expected immutable bay and holding observations")
        identifiers(tuple(s.bay_id for s in self.bays))
        identifiers(tuple(c for s in self.bays for c in s.cargo)
                    + tuple(h.cargo_id for h in self.holdings))
        identifiers(tuple(h.holder_id for h in self.holdings if h.holder_id is not None))


@dataclass(frozen=True)
class OutboundRequest:
    cargo_id: str
    expected_revision: int

    def __post_init__(self):
        identifier(self.cargo_id)
        revision(self.expected_revision)


@dataclass(frozen=True)
class PortConstraints:
    outbound_stand: str
    temporary_bays: tuple[str, ...]
    allowed_moves: frozenset[tuple[str, str]]
    max_observation_age: timedelta
    conveyor: str
    # (arm, cell location) pairs the installer verified. Empty means unverified, so planning HOLDs.
    # shortcut: installer table, replace with platform compile pre-validation once D-856 P1 lands.
    arm_reach: frozenset[tuple[str, str]] = frozenset()

    def __post_init__(self):
        identifier(self.outbound_stand)
        identifier(self.conveyor)
        identifiers(self.temporary_bays)
        # Rehandles never go on the one-way conveyor or the outbound stand (ADR 0005 decision 8).
        if self.outbound_stand in self.temporary_bays or self.conveyor in self.temporary_bays:
            raise ValueError("The outbound stand and the conveyor cannot be temporary bays")
        if self.conveyor == self.outbound_stand:
            raise ValueError("The conveyor cannot be the outbound stand")
        if not isinstance(self.arm_reach, frozenset) or not all(
                isinstance(e, tuple) and len(e) == 2 and e[0] in YARD_ARMS for e in self.arm_reach):
            raise ValueError("Expected immutable (A|B, location) reach pairs")
        for _, location in self.arm_reach:
            identifier(location)
        if not isinstance(self.allowed_moves, frozenset):
            raise ValueError("Expected immutable directed moves")
        for edge in self.allowed_moves:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise ValueError("Expected source and destination")
            identifiers(edge)
        if not isinstance(self.max_observation_age, timedelta) or self.max_observation_age <= timedelta(0):
            raise ValueError("Observation freshness must be positive")

from dataclasses import dataclass
from datetime import datetime, timedelta


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
class Slot:
    slot_id: str
    cargo: tuple[str, ...]  # Bottom to top; no empty layers.
    capacity: int

    def __post_init__(self):
        identifier(self.slot_id)
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
    slots: tuple[Slot, ...]
    holdings: tuple[CargoHolding, ...] = ()

    def __post_init__(self):
        revision(self.revision)
        aware_time(self.observed_at)
        if (not isinstance(self.slots, tuple) or not self.slots
                or not all(isinstance(s, Slot) for s in self.slots)
                or not isinstance(self.holdings, tuple)
                or not all(isinstance(h, CargoHolding) for h in self.holdings)):
            raise ValueError("Expected immutable slot and holding observations")
        identifiers(tuple(s.slot_id for s in self.slots))
        identifiers(tuple(c for s in self.slots for c in s.cargo)
                    + tuple(h.cargo_id for h in self.holdings))
        identifiers(tuple(h.holder_id for h in self.holdings if h.holder_id is not None))


@dataclass(frozen=True)
class RetrievalRequest:
    cargo_id: str
    expected_revision: int

    def __post_init__(self):
        identifier(self.cargo_id)
        revision(self.expected_revision)


@dataclass(frozen=True)
class PortConstraints:
    supply_slot: str
    temporary_slots: tuple[str, ...]
    transfers: frozenset[tuple[str, str]]
    max_observation_age: timedelta

    def __post_init__(self):
        identifier(self.supply_slot)
        identifiers(self.temporary_slots)
        if self.supply_slot in self.temporary_slots:
            raise ValueError("Supply cannot be a temporary slot")
        if not isinstance(self.transfers, frozenset):
            raise ValueError("Expected immutable directed transfers")
        for edge in self.transfers:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise ValueError("Expected source and destination")
            identifiers(edge)
        if not isinstance(self.max_observation_age, timedelta) or self.max_observation_age <= timedelta(0):
            raise ValueError("Observation freshness must be positive")

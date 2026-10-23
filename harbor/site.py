from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from harbor.model import identifier, identifiers


@dataclass(frozen=True)
class Site:
    arms: Mapping[str, str]
    transport_ids: tuple[str, ...]
    slots: tuple[str, ...]
    waiting_slots: tuple[str, ...]
    transfers: frozenset[tuple[str, str]]
    charging_slots: int
    max_moving_robots: int

    def __post_init__(self):
        if not isinstance(self.arms, Mapping) or set(self.arms) != {"S", "A", "B"}:
            raise ValueError("Expected supply S and yard A/B roles")
        identifiers(tuple(self.arms.values()))
        identifiers(self.transport_ids)
        identifiers(self.slots)
        identifiers(self.waiting_slots)
        if len(self.transport_ids) != 3 or set(self.arms.values()) & set(self.transport_ids):
            raise ValueError("Expected three distinct transport devices, separate from arms")
        if not self.slots or not self.waiting_slots or not set(self.waiting_slots) <= set(self.slots):
            raise ValueError("Expected known waiting positions")
        if type(self.charging_slots) is not int or self.charging_slots != 1:
            raise ValueError("Current site has one conceptual charging slot")
        if type(self.max_moving_robots) is not int or self.max_moving_robots != 1:
            raise ValueError("Current site retains serial movement")
        if not isinstance(self.transfers, frozenset):
            raise ValueError("Expected immutable transfers")
        for edge in self.transfers:
            if not isinstance(edge, tuple) or len(edge) != 2:
                raise ValueError("Expected directed transfer")
            identifiers(edge)
            if not set(edge) <= set(self.slots):
                raise ValueError("Transfer references unknown slot")
        object.__setattr__(self, "arms", MappingProxyType(dict(self.arms)))

    @classmethod
    def from_dict(cls, data: dict) -> "Site":
        expected = {"arms", "transport_ids", "slots", "waiting_slots", "transfers",
                    "charging_slots", "max_moving_robots"}
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("Unknown or missing site configuration fields")
        for name in ("transport_ids", "slots", "waiting_slots", "transfers"):
            if not isinstance(data[name], list):
                raise ValueError("Expected configuration lists")
        if not all(isinstance(edge, list) for edge in data["transfers"]):
            raise ValueError("Expected transfer pairs")
        return cls(data["arms"], tuple(data["transport_ids"]), tuple(data["slots"]),
                   tuple(data["waiting_slots"]), frozenset(tuple(e) for e in data["transfers"]),
                   data["charging_slots"], data["max_moving_robots"])

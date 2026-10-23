from dataclasses import dataclass

from harbor.model import identifier, identifiers


@dataclass(frozen=True)
class TransportState:
    robot_id: str
    status: str

    def __post_init__(self):
        identifier(self.robot_id)
        if self.status not in {"available", "loaded", "charging", "waiting_charge", "maintenance", "unknown"}:
            raise ValueError("Unknown manual transport status")


def transport_candidates(pool: tuple[str, ...], states: tuple[TransportState, ...]) -> tuple[str, ...]:
    """Operator exclusions only; Fleet still owns live admission and allocation."""
    identifiers(pool)
    if not isinstance(states, tuple) or not all(isinstance(s, TransportState) for s in states):
        raise ValueError("Expected immutable manual transport states")
    identifiers(tuple(s.robot_id for s in states))
    if not {s.robot_id for s in states} <= set(pool):
        raise ValueError("Manual state references a robot outside the pool")
    if sum(s.status == "charging" for s in states) > 1:
        raise ValueError("One charging position cannot contain two robots")
    by_robot = {s.robot_id: s.status for s in states}
    return tuple(robot for robot in pool if by_robot.get(robot) == "available")

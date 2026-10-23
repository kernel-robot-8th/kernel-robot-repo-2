"""Run a deterministic inventory-planning example without connecting to devices."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

from harbor.model import PortConstraints, OutboundRequest, Bay, YardSnapshot
from harbor.outbound import plan_outbound


def main():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    snapshot = YardSnapshot(1, now, (
        Bay("yard", ("outbound", "rehandle"), 3),
        Bay("temp", (), 2), Bay("stand", (), 1),
    ))
    constraints = PortConstraints("stand", ("temp",), frozenset({
        ("yard", "temp"), ("temp", "yard"), ("yard", "stand"),
    }), timedelta(seconds=5), "conveyor", frozenset({
        ("A", "yard"), ("A", "temp"), ("A", "stand"), ("B", "yard"), ("B", "stand"),
    }))
    proposal = plan_outbound(snapshot, OutboundRequest("outbound", 1), constraints, now=now)
    print(json.dumps(asdict(proposal), indent=2))


if __name__ == "__main__":
    main()

"""Run a deterministic inventory-planning example without connecting to devices."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

from harbor.model import PortConstraints, RetrievalRequest, Slot, YardSnapshot
from harbor.retrieval import plan_retrieval


def main():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    snapshot = YardSnapshot(1, now, (
        Slot("yard", ("target", "blocker"), 3),
        Slot("temporary", (), 2), Slot("supply", (), 1),
    ))
    constraints = PortConstraints("supply", ("temporary",), frozenset({
        ("yard", "temporary"), ("temporary", "yard"), ("yard", "supply"),
    }), timedelta(seconds=5))
    proposal = plan_retrieval(snapshot, RetrievalRequest("target", 1), constraints, now=now)
    print(json.dumps(asdict(proposal), indent=2))


if __name__ == "__main__":
    main()

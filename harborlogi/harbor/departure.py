"""Transfer-vehicle departure permission from supply-arm S clearance (ADR 0005 decision 6).

공급암 S는 플랫폼 밖 외부 단계다. S의 퇴피(S 그리퍼가 이송 차량의 적재면에서 빠짐)는 업무
완료 조건이 아니라 그 차량의 출발 허용 조건이다. 1차는 사람이 확인하고 이후 카메라가
확인한다. 근거는 단 한 번만 쓰고, 확인 시각이 신선할 때만 출발을 허가한다. 차량의 실제
주행·정지 권한은 Fleet과 CORE가 소유하며 이 장부는 업무 판단만 기록한다.
"""
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import sqlite3

from harbor.model import aware_time, identifier, identifiers


@dataclass(frozen=True)
class SupplyClearance:
    """One confirmation that S's gripper has left a transfer vehicle's load surface.

    Phase 1 the source is a person (operator); camera confirmation is a later phase.
    The confirmation itself is the fact — there is no success claim from S to trust.
    """
    evidence_id: str
    source_id: str
    vehicle_id: str
    observed_at: datetime

    def __post_init__(self):
        for value in (self.evidence_id, self.source_id, self.vehicle_id):
            identifier(value)
        aware_time(self.observed_at)


class DepartureLedger:
    """Records clearance evidence and permits each departure exactly once."""

    def __init__(self, path, *, clearance_sources, freshness=timedelta(seconds=5)):
        identifiers(clearance_sources)
        if (not clearance_sources or not isinstance(freshness, timedelta) or freshness <= timedelta(0)):
            raise ValueError("Configured clearance sources and positive freshness are required")
        self.path, self.freshness = str(path), freshness
        self.clearance_sources = clearance_sources
        with closing(self._connect()) as c, c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS supply_clearance (
                    source_id TEXT NOT NULL, evidence_id TEXT NOT NULL, payload TEXT NOT NULL,
                    vehicle_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                    state TEXT NOT NULL, PRIMARY KEY(source_id,evidence_id));
            """)

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _check_fresh(now, observed_at, freshness):
        if not timedelta(0) <= now - observed_at <= freshness:
            raise ValueError("Clearance observation is not current")

    def record(self, evidence, *, now):
        """Preserve one clearance observation; unknown sources and stale or future times are rejected."""
        aware_time(now)
        if not isinstance(evidence, SupplyClearance):
            raise ValueError("Expected a supply clearance observation")
        if evidence.source_id not in self.clearance_sources:
            raise ValueError("Clearance source is not configured")
        self._check_fresh(now, evidence.observed_at, self.freshness)
        payload = json.dumps(asdict(evidence), sort_keys=True, separators=(",", ":"), allow_nan=False,
                             default=datetime.isoformat)
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM supply_clearance WHERE source_id=? AND evidence_id=?",
                         (evidence.source_id, evidence.evidence_id)).fetchone() is not None:
                raise ValueError("Clearance observation was already consumed")
            c.execute("INSERT INTO supply_clearance VALUES(?,?,?,?,?,'CLEARANCE')",
                      (evidence.source_id, evidence.evidence_id, payload,
                       evidence.vehicle_id, evidence.observed_at.isoformat()))

    def read(self, evidence_id, *, source_id):
        identifier(evidence_id)
        identifier(source_id)
        with closing(self._connect()) as c:
            row = c.execute("SELECT * FROM supply_clearance WHERE source_id=? AND evidence_id=?",
                            (source_id, evidence_id)).fetchone()
        if row is None:
            raise ValueError("Unknown clearance observation")
        return dict(row)

    def authorize(self, vehicle_id, *, now):
        """Permit one departure of this vehicle against its freshest unconsumed clearance."""
        identifier(vehicle_id)
        aware_time(now)
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT source_id, evidence_id, observed_at FROM supply_clearance "
                             "WHERE vehicle_id=? AND state='CLEARANCE'", (vehicle_id,)).fetchall()
        if not rows:
            raise ValueError("No recorded supply clearance for this vehicle")
        # ISO strings are not safely orderable across offsets, so compare parsed times.
        freshest = max(rows, key=lambda r: datetime.fromisoformat(r["observed_at"]))
        self._check_fresh(now, datetime.fromisoformat(freshest["observed_at"]), self.freshness)
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT state FROM supply_clearance WHERE source_id=? AND evidence_id=?",
                            (freshest["source_id"], freshest["evidence_id"])).fetchone()
            if row is None or row["state"] != "CLEARANCE":
                raise ValueError("Clearance was already consumed")
            c.execute("UPDATE supply_clearance SET state='AUTHORIZED' WHERE source_id=? AND evidence_id=?",
                      (freshest["source_id"], freshest["evidence_id"]))

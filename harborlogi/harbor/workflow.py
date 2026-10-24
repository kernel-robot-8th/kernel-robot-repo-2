from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
import sqlite3

from harbor.model import YARD_ARMS, aware_time, identifier, identifiers, revision
from harbor.process import ArmTransferBinding, ProcessBinding


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _payload(row):
    payload = json.loads(row["payload"])
    if "target_id" in payload or "supply_slot" in payload:
        raise ValueError("Ledger row predates the ADR 0005 names; reconcile it and prepare a new request key")
    return payload


def _header(evidence):
    for value in (evidence.evidence_id, evidence.mission_id, evidence.attempt_id, evidence.source_id):
        identifier(value)
    revision(evidence.revision)
    aware_time(evidence.observed_at)


@dataclass(frozen=True)
class PlacementEvidence:
    """Camera observation that a cargo lies at a cell location (내려놓기), separate from action results."""
    evidence_id: str
    mission_id: str
    attempt_id: str
    revision: int
    source_id: str
    observed_at: datetime
    cargo_id: str
    location: str

    def __post_init__(self):
        _header(self)
        identifier(self.cargo_id)
        identifier(self.location)


@dataclass(frozen=True)
class ArmRetreat:
    """One yard arm's retreat facts (퇴피): Fleet zone-claim release and an owner-measured pose."""
    arm: str
    zone_id: str
    claim_released: bool
    joint_positions: tuple[float, ...]
    pose_observed_at: datetime
    pose_revision: int
    outside_zone: bool  # The platform's FK verdict; Harbor does not compute kinematics.

    def __post_init__(self):
        if self.arm not in YARD_ARMS:
            raise ValueError("Retreat evidence covers yard arms A/B only")
        identifier(self.zone_id)
        if type(self.claim_released) is not bool or type(self.outside_zone) is not bool:
            raise ValueError("Physical facts must be explicit booleans")
        if (not isinstance(self.joint_positions, tuple) or not self.joint_positions
                or not all(type(v) in (int, float) and math.isfinite(v) for v in self.joint_positions)):
            raise ValueError("Expected measured finite joint positions")
        aware_time(self.pose_observed_at)
        revision(self.pose_revision)


@dataclass(frozen=True)
class RetreatEvidence:
    """Platform-data retreat record for the arms a step may have used; no camera is involved."""
    evidence_id: str
    mission_id: str
    attempt_id: str
    revision: int
    source_id: str
    observed_at: datetime
    arms: tuple[ArmRetreat, ...]

    def __post_init__(self):
        _header(self)
        if not isinstance(self.arms, tuple) or not all(isinstance(a, ArmRetreat) for a in self.arms):
            raise ValueError("Expected immutable arm retreat facts")
        identifiers(tuple(a.arm for a in self.arms))


@dataclass(frozen=True)
class MoveEvidence:
    """The two independent facts a step needs (ADR 0005 decision 6); each keeps its own source and time."""
    placement: PlacementEvidence
    retreat: RetreatEvidence


class BusinessLedger:
    def __init__(self, path, *, placement_sources, retreat_sources, freshness=timedelta(seconds=5)):
        identifiers(placement_sources)
        identifiers(retreat_sources)
        if (not placement_sources or not retreat_sources or set(placement_sources) & set(retreat_sources)
                or not isinstance(freshness, timedelta) or freshness <= timedelta(0)):
            raise ValueError("Separate configured placement and retreat sources and positive freshness are required")
        self.path, self.freshness = str(path), freshness
        self.placement_sources, self.retreat_sources = placement_sources, retreat_sources
        self._reads = {}
        with closing(self._connect()) as c, c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS business (
                    request_key TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL, step_index INTEGER NOT NULL DEFAULT 0,
                    row_version INTEGER NOT NULL DEFAULT 0,
                    mission_id TEXT, attempt_id TEXT, goal_confirmed INTEGER NOT NULL DEFAULT 0,
                    outbound_mission_id TEXT, outbound_attempt_id TEXT);
                CREATE TABLE IF NOT EXISTS evidence (
                    source_id TEXT NOT NULL, evidence_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(source_id,evidence_id));
            """)
            c.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in c.execute("PRAGMA table_info(business)")}
            if "row_version" not in columns:
                c.execute("ALTER TABLE business ADD COLUMN row_version INTEGER NOT NULL DEFAULT 0")
            for old in ("mission_id", "attempt_id"):  # ADR 0005 names; rows keep their values.
                if "target_" + old in columns:
                    c.execute(f"ALTER TABLE business RENAME COLUMN target_{old} TO outbound_{old}")
            c.execute("UPDATE business SET state='HOLD',row_version=row_version+1 WHERE state='SUBMITTING'")

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _row(c, key):
        row = c.execute("SELECT * FROM business WHERE request_key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Unknown business request")
        return dict(row)

    def read(self, key):
        with closing(self._connect()) as c:
            return self._row(c, key)

    def _check_current(self, c, key, original):
        current = self._row(c, key)
        if (current["row_version"] != original["row_version"] or current["state"] != original["state"]
                or current["step_index"] != original["step_index"] or current["digest"] != original["digest"]):
            raise ValueError("Business stage changed while the Fleet request was pending")

    def prepare(self, key, plan_revision, moves, *, outbound_id, outbound_stand, move_arms):
        identifier(key)
        if len(key) > 128 or any(ord(c) < 32 for c in key):
            raise ValueError("Invalid business request key")
        revision(plan_revision)
        identifier(outbound_id)
        identifier(outbound_stand)
        if (not isinstance(moves, tuple) or not moves
                or not all(isinstance(m, (ProcessBinding, ArmTransferBinding)) for m in moves)
                or len({type(m) for m in moves}) != 1):
            raise ValueError("Expected a nonempty immutable bound plan")
        if sum(m.cargo_id == outbound_id for m in moves) != 1 or not any(
                m.cargo_id == outbound_id and m.destination == outbound_stand for m in moves):
            raise ValueError("Expected exactly one outbound move to the outbound stand")
        # Primary then alternates per step; every arm a step may use must later show retreat.
        if (not isinstance(move_arms, tuple) or len(move_arms) != len(moves)
                or not all(isinstance(a, tuple) and a and set(a) <= set(YARD_ARMS) and len(set(a)) == len(a)
                           for a in move_arms)):
            raise ValueError("Expected nonempty A/B arm roles for every move")
        mode = {"execution_kind": "ARM_TRANSFER"} if isinstance(moves[0], ArmTransferBinding) else {}
        payload = _json({**mode, "revision": plan_revision, "moves": [asdict(m) for m in moves],
                         "outbound_id": outbound_id, "outbound_stand": outbound_stand,
                         "move_arms": [list(a) for a in move_arms]})
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT digest FROM business WHERE request_key=?", (key,)).fetchone()
            if old is not None and old[0] != digest:
                raise ValueError("Business request key conflicts with existing contents")
            c.execute("INSERT OR IGNORE INTO business(request_key,digest,payload,state) VALUES(?,?,?,'PLANNED')",
                      (key, digest, payload))

    def propose(self, key, client):
        row = self.read(key)
        if row["state"] != "PLANNED":
            raise ValueError("Current stage cannot submit a proposal")
        payload = _payload(row)
        neutral = payload.get("execution_kind") == "ARM_TRANSFER"
        binding = (ArmTransferBinding if neutral else ProcessBinding)(**payload["moves"][row["step_index"]])
        if not neutral:
            client.compile(binding)
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            self._check_current(c, key, row)
            c.execute("UPDATE business SET state='SUBMITTING',row_version=row_version+1 WHERE request_key=?", (key,))
        intent_version = row["row_version"] + 1
        request_key = f"{key}:{row['step_index']}"
        try:
            if neutral:
                result = binding.check(client.propose_arm(binding, request_key=request_key)["job"], request_key)
                job = binding.check(client.arm_request(request_key)["job"], request_key)
                mission_id = job["mission_id"]
                if result["mission_id"] != mission_id:
                    raise ValueError("Physical proposal and readback identify different jobs")
            else:
                result = client.propose(binding, request_key=request_key)
                proposal, mission = result["proposal"], result["mission"]
                mission_id = proposal["proposal_id"]
                identifier(mission_id)
                if (proposal["state"] != "RESOLVED" or proposal["request_key"] != request_key
                        or mission["mission_id"] != mission_id
                        or mission.get("process_artifact_digest") != binding.process_artifact_digest):
                    raise ValueError("Proposal readback does not match the submitted stage")
        except BaseException:
            with closing(self._connect()) as c, c:
                c.execute("UPDATE business SET state='HOLD',row_version=row_version+1 "
                          "WHERE request_key=? AND state='SUBMITTING' AND row_version=?", (key, intent_version))
            raise
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            current = self._row(c, key)
            if current["state"] == "SUBMITTING" and current["row_version"] == intent_version:
                state = "WAITING"
            elif (current["state"] == "HOLD" and current["step_index"] == row["step_index"]
                  and current["digest"] == row["digest"] and current["mission_id"] is None):
                state = "HOLD"
            else:
                raise ValueError("Late proposal cannot overwrite a different business stage")
            c.execute("UPDATE business SET state=?,mission_id=?,attempt_id=NULL,goal_confirmed=0,row_version=row_version+1 "
                      "WHERE request_key=?", (state, mission_id, key))
        if state == "HOLD":
            raise ValueError("Late proposal response retained for reconciliation; restart HOLD remains")
        return mission_id

    def refresh(self, key, client, *, now):
        aware_time(now)
        row = self.read(key)
        if row["state"] != "WAITING":
            raise ValueError("No current execution to reconcile")
        payload = _payload(row)
        if payload.get("execution_kind") == "ARM_TRANSFER":
            binding = ArmTransferBinding(**payload["moves"][row["step_index"]])
            job = binding.check(client.arm_job(row["mission_id"])["job"], f"{key}:{row['step_index']}")
            if job["mission_id"] != row["mission_id"]:
                raise ValueError("Physical job identity changed")
            attempt = None
            if job["status"] == "GOAL_CONFIRMED":
                receipt = binding.check_receipt(job, client.physical_receipt(row["mission_id"])["receipt"])
                attempt = receipt["attempt_id"]
        else:
            job = client.job(row["mission_id"])["job"]
            if job["mission_id"] != row["mission_id"] or len(job["steps"]) != 1:
                raise ValueError("Fleet job does not match the bound single-transfer stage")
            attempt = job["steps"][0].get("attempt_id")
        if attempt is not None:
            identifier(attempt)
        confirmed = job["status"] == "GOAL_CONFIRMED" and attempt is not None
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            self._check_current(c, key, row)
            c.execute("UPDATE business SET attempt_id=?,goal_confirmed=?,state=?,row_version=row_version+1 WHERE request_key=?",
                      (attempt, int(confirmed), "HOLD" if job["status"] == "HOLD" else "WAITING", key))
        self._reads[key] = (row["mission_id"], attempt, now, row["row_version"] + 1)

    def _validate(self, row, evidence, now, *, mission_id, attempt_id, cargo_id, location, arms):
        aware_time(now)
        payload = _payload(row)
        if not isinstance(evidence, MoveEvidence):
            raise ValueError("Placement and retreat evidence are both required")
        placement, retreat = evidence.placement, evidence.retreat

        def bound(e, kind, sources):
            if (not isinstance(e, kind) or e.source_id not in sources
                    or e.revision != payload["revision"] or e.mission_id != mission_id
                    or e.attempt_id != attempt_id
                    or not timedelta(0) <= now - e.observed_at <= self.freshness):
                raise ValueError("Physical evidence does not match the current business execution")

        bound(placement, PlacementEvidence, self.placement_sources)
        if placement.cargo_id != cargo_id or placement.location != location:
            raise ValueError("Placement evidence does not show the cargo at its destination")
        bound(retreat, RetreatEvidence, self.retreat_sources)
        by_arm = {a.arm: a for a in retreat.arms}
        for arm in arms:
            fact = by_arm.get(arm)
            if (fact is None or not fact.claim_released or not fact.outside_zone
                    or not timedelta(0) <= now - fact.pose_observed_at <= self.freshness):
                raise ValueError("Retreat evidence does not show every related arm out of the space")

    @staticmethod
    def _save_evidence(c, evidence):
        for part in (evidence.placement, evidence.retreat):
            payload = json.dumps(asdict(part), sort_keys=True, separators=(",", ":"), allow_nan=False,
                                 default=datetime.isoformat)
            old = c.execute("SELECT payload FROM evidence WHERE source_id=? AND evidence_id=?",
                            (part.source_id, part.evidence_id)).fetchone()
            if old is not None:
                raise ValueError("Observation identity was already consumed")
            c.execute("INSERT INTO evidence VALUES(?,?,?)", (part.source_id, part.evidence_id, payload))

    def confirm(self, key, evidence, *, now):
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            row = self._row(c, key)
            payload = _payload(row)
            if row["state"] != "WAITING" or not row["goal_confirmed"]:
                raise ValueError("Fleet goal confirmation is required")
            read = self._reads.get(key)
            if (read is None or read[:2] != (row["mission_id"], row["attempt_id"])
                    or read[3] != row["row_version"]
                    or not timedelta(0) <= now - read[2] <= self.freshness):
                raise ValueError("Fresh Fleet reconciliation is required")
            move = payload["moves"][row["step_index"]]
            self._validate(row, evidence, now, mission_id=row["mission_id"], attempt_id=row["attempt_id"],
                           cargo_id=move["cargo_id"], location=move["destination"],
                           arms=payload["move_arms"][row["step_index"]])
            self._save_evidence(c, evidence)
            if move["cargo_id"] == payload["outbound_id"]:
                c.execute("UPDATE business SET outbound_mission_id=mission_id,outbound_attempt_id=attempt_id "
                          "WHERE request_key=?", (key,))
            index = row["step_index"] + 1
            state = "AWAITING_FINAL_EVIDENCE" if index == len(payload["moves"]) else "PLANNED"
            c.execute("UPDATE business SET state=?,step_index=?,mission_id=NULL,attempt_id=NULL,goal_confirmed=0,row_version=row_version+1 "
                      "WHERE request_key=?", (state, index, key))
        if self._reads.get(key) == read:
            self._reads.pop(key, None)

    def finish(self, key, evidence, *, now):
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            row = self._row(c, key)
            payload = _payload(row)
            if row["state"] != "AWAITING_FINAL_EVIDENCE":
                raise ValueError("Outbound placement and all restored moves are required")
            # Related-arm retreat: every arm used by this outbound request (ADR 0005 decision 6).
            related = tuple(sorted({a for arms in payload["move_arms"] for a in arms}))
            self._validate(row, evidence, now, mission_id=row["outbound_mission_id"],
                           attempt_id=row["outbound_attempt_id"], cargo_id=payload["outbound_id"],
                           location=payload["outbound_stand"], arms=related)
            self._save_evidence(c, evidence)
            c.execute("UPDATE business SET state='COMPLETED',row_version=row_version+1 WHERE request_key=?", (key,))

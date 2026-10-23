from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3

from harbor.model import aware_time, identifier, identifiers, revision
from harbor.process import ProcessBinding


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _payload(row):
    payload = json.loads(row["payload"])
    if "target_id" in payload or "supply_slot" in payload:
        raise ValueError("Ledger row predates the ADR 0005 names; reconcile it and prepare a new request key")
    return payload


@dataclass(frozen=True)
class MoveEvidence:
    evidence_id: str
    mission_id: str
    attempt_id: str
    revision: int
    source_id: str
    observed_at: datetime
    cargo_id: str
    destination: str
    arms_retreated: bool
    placed: bool = False

    def __post_init__(self):
        for value in (self.evidence_id, self.mission_id, self.attempt_id, self.source_id,
                      self.cargo_id, self.destination):
            identifier(value)
        revision(self.revision)
        aware_time(self.observed_at)
        if type(self.arms_retreated) is not bool or type(self.placed) is not bool:
            raise ValueError("Physical facts must be explicit booleans")


class BusinessLedger:
    def __init__(self, path, *, evidence_sources, freshness=timedelta(seconds=5)):
        identifiers(evidence_sources)
        if not evidence_sources or not isinstance(freshness, timedelta) or freshness <= timedelta(0):
            raise ValueError("Configured observation sources and positive freshness are required")
        self.path, self.sources, self.freshness = str(path), evidence_sources, freshness
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

    def prepare(self, key, plan_revision, moves, *, outbound_id, outbound_stand):
        identifier(key)
        if len(key) > 128 or any(ord(c) < 32 for c in key):
            raise ValueError("Invalid business request key")
        revision(plan_revision)
        identifier(outbound_id)
        identifier(outbound_stand)
        if not isinstance(moves, tuple) or not moves or not all(isinstance(m, ProcessBinding) for m in moves):
            raise ValueError("Expected a nonempty immutable bound plan")
        if sum(m.cargo_id == outbound_id for m in moves) != 1 or not any(
                m.cargo_id == outbound_id and m.destination == outbound_stand for m in moves):
            raise ValueError("Expected exactly one outbound move to the outbound stand")
        payload = _json({"revision": plan_revision, "moves": [asdict(m) for m in moves],
                         "outbound_id": outbound_id, "outbound_stand": outbound_stand})
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
        binding = ProcessBinding(**payload["moves"][row["step_index"]])
        client.compile(binding)
        with closing(self._connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            self._check_current(c, key, row)
            c.execute("UPDATE business SET state='SUBMITTING',row_version=row_version+1 WHERE request_key=?", (key,))
        intent_version = row["row_version"] + 1
        request_key = f"{key}:{row['step_index']}"
        try:
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

    def _validate(self, row, evidence, now, *, mission_id, attempt_id, cargo_id, destination):
        aware_time(now)
        payload = _payload(row)
        if (not isinstance(evidence, MoveEvidence) or evidence.source_id not in self.sources
                or evidence.revision != payload["revision"] or evidence.mission_id != mission_id
                or evidence.attempt_id != attempt_id or evidence.cargo_id != cargo_id
                or evidence.destination != destination or not evidence.arms_retreated
                or not timedelta(0) <= now - evidence.observed_at <= self.freshness):
            raise ValueError("Physical evidence does not match the current business execution")

    @staticmethod
    def _save_evidence(c, evidence):
        data = asdict(evidence)
        data["observed_at"] = evidence.observed_at.isoformat()
        payload = _json(data)
        old = c.execute("SELECT payload FROM evidence WHERE source_id=? AND evidence_id=?",
                        (evidence.source_id, evidence.evidence_id)).fetchone()
        if old is not None:
            raise ValueError("Observation identity was already consumed")
        c.execute("INSERT INTO evidence VALUES(?,?,?)", (evidence.source_id, evidence.evidence_id, payload))

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
                           cargo_id=move["cargo_id"], destination=move["destination"])
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
            if row["state"] != "AWAITING_FINAL_EVIDENCE" or not evidence.placed:
                raise ValueError("Outbound placement and all restored moves are required")
            self._validate(row, evidence, now, mission_id=row["outbound_mission_id"],
                           attempt_id=row["outbound_attempt_id"], cargo_id=payload["outbound_id"],
                           destination=payload["outbound_stand"])
            self._save_evidence(c, evidence)
            c.execute("UPDATE business SET state='COMPLETED',row_version=row_version+1 WHERE request_key=?", (key,))

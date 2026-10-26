from dataclasses import dataclass
import hashlib
import json
import re

from harbor.model import YARD_ARMS, identifier
from harbor.outbound import OutboundProposal


@dataclass(frozen=True)
class ProcessBinding:
    cargo_id: str
    source: str
    destination: str
    recipe_id: str
    recipe_digest: str
    cell_id: str
    cell_digest: str
    workcell_id: str
    instance_id: str
    process_artifact_digest: str

    def __post_init__(self):
        for value in (self.cargo_id, self.source, self.destination, self.workcell_id, self.instance_id):
            identifier(value)
            if len(value) > 96 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid process binding identifier")
        for value in (self.recipe_id, self.cell_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
                raise ValueError("Invalid installed document ID")
        for value in (self.recipe_digest, self.cell_digest, self.process_artifact_digest):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Invalid installed document digest")

    def compile_request(self):
        return {"recipe_id": self.recipe_id, "recipe_digest": self.recipe_digest,
                "cell_id": self.cell_id, "cell_digest": self.cell_digest}

    def proposal_request(self, request_key):
        identifier(request_key)
        if len(request_key) > 160 or any(ord(c) < 32 for c in request_key):
            raise ValueError("Invalid process request key")
        return {**self.compile_request(), "request_key": request_key,
                "workcell_id": self.workcell_id, "instance_id": self.instance_id}


@dataclass(frozen=True)
class ArmTransferBinding:
    """Harbor move names map to installed physical references at this boundary."""
    cargo_id: str
    source: str
    destination: str
    workcell_id: str
    instance_id: str
    object_ref: str
    source_ref: str
    target_ref: str

    def __post_init__(self):
        for value in self.__dict__.values():
            identifier(value)
            if len(value) > 192 or value != value.strip() or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
                raise ValueError("Invalid physical transfer binding reference")
        if self.source_ref == self.target_ref:
            raise ValueError("Physical source and target must differ")

    def proposal_request(self, request_key):
        identifier(request_key)
        if len(request_key) > 160 or request_key != request_key.strip() or any(ord(c) < 32 for c in request_key):
            raise ValueError("Invalid physical transfer request key")
        return dict(request_key=request_key, workcell_id=self.workcell_id, instance_id=self.instance_id,
                    object_ref=self.object_ref, source=self.source_ref, target=self.target_ref)

    def check(self, job, request_key):
        request = self.proposal_request(request_key)
        transfer = {k: v for k, v in request.items() if k != "request_key"}
        digest = hashlib.sha256(json.dumps(transfer, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (not isinstance(job, dict) or job.get("action_kind") != "ARM_TRANSFER"
                or job.get("request_key") != request_key or job.get("transfer") != transfer
                or job.get("request_digest") != digest or job.get("status") not in {
                    "PROPOSED", "READY", "RUNNING", "ACTION_SUCCEEDED", "GOAL_CONFIRMED", "HOLD"}):
            raise ValueError("Fleet physical job does not match the bound Harbor move")
        identifier(job.get("mission_id"))
        return job

    def check_receipt(self, job, receipt):
        if not isinstance(receipt, dict):
            raise ValueError("A physical receipt is required")
        expected = dict(mission_id=job["mission_id"], workcell_id=self.workcell_id, instance_id=self.instance_id,
            object_ref=self.object_ref, source=self.source_ref, target=self.target_ref,
            policy_digest=job["resource_policy_digest"], authority_epoch=job["authority_epoch"],
            dispatch_generation=job["dispatch_generation"], physical_status="AT_TARGET_AND_CLEAR")
        if job["status"] != "GOAL_CONFIRMED" or any(receipt.get(k) != v for k, v in expected.items()):
            raise ValueError("Physical receipt does not match the confirmed bound job")
        for key in ("authority_epoch", "dispatch_generation", "validated_at_ns"):
            if type(receipt.get(key)) is not int or not 0 <= receipt[key] < 2**63:
                raise ValueError("Physical receipt requires exact counters")
        # Receipt request_digest identifies the full grant; job request_digest identifies the transfer body (ADR 0006).
        for key in ("request_digest", "policy_digest", "evidence_digest"):
            if not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]):
                raise ValueError("Invalid physical receipt digest")
        for key in ("receipt_id", "action_id", "attempt_id"):
            identifier(receipt.get(key))
        return receipt


def anchor_address(area_id, row, column, level):
    """Canonical placement-anchor address naming convention (user decision 2026-10-27)."""
    return f'{area_id}-r{row}-c{column}-l{level}'


@dataclass(frozen=True)
class SpatialPlacementBinding:
    """An inbound placement bound to installed physical references (design 29).

    The business key is the full placement tuple; the physical target reference
    names the placement anchor address, and the yard arm for retreat evidence
    lives in the binding (user decision 2026-10-27). The ledger-facing move is
    a plain ARM transfer to that anchor address.
    """
    cargo_id: str
    footprint_revision: int
    area_id: str
    row: int
    column: int
    level: int
    orientation: int
    source: str
    yard_arm: str
    workcell_id: str
    instance_id: str
    object_ref: str
    source_ref: str
    target_ref: str

    def __post_init__(self):
        for value in (self.cargo_id, self.area_id, self.source, self.workcell_id,
                      self.instance_id, self.object_ref, self.source_ref, self.target_ref):
            identifier(value)
            if len(value) > 192 or value != value.strip() or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
                raise ValueError('Invalid spatial placement binding reference')
        if self.yard_arm not in YARD_ARMS:
            raise ValueError('Spatial placement binding names one yard arm A/B')
        if self.orientation not in (0, 90):
            raise ValueError('Spatial orientation is 0 or 90 degrees')
        if type(self.footprint_revision) is not int or self.footprint_revision < 1:
            raise ValueError('Footprint revision must be positive')
        for name in ('row', 'column', 'level'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError('Placement grid coordinates are positive integers')
        if self.source_ref == self.target_ref:
            raise ValueError('Physical source and target must differ')
        if self.target_ref != anchor_address(self.area_id, self.row, self.column, self.level):
            raise ValueError('Physical target must name the placement anchor address')

    def anchor(self):
        return anchor_address(self.area_id, self.row, self.column, self.level)

    def matches(self, recommendation, footprint_revision, cargo_id):
        """The binding authorizes exactly this placement tuple (design 29)."""
        return (self.cargo_id == cargo_id and self.footprint_revision == footprint_revision
                and self.area_id == recommendation['area_id'] and self.row == recommendation['row']
                and self.column == recommendation['column'] and self.level == recommendation['level']
                and self.orientation == recommendation.get('orientation', 0))

    def move(self):
        return ArmTransferBinding(self.cargo_id, self.source, self.anchor(), self.workcell_id,
                                  self.instance_id, self.object_ref, self.source_ref, self.target_ref)


def load_bindings(items):
    """Installed binding entries: a footprint key makes a spatial placement binding."""
    bindings = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Installed bindings are objects')
        bindings.append(SpatialPlacementBinding(**item) if 'footprint_revision' in item
                        else ArmTransferBinding(**item))
    return tuple(bindings)


def bind_plan(proposal: OutboundProposal,
              bindings: tuple[ProcessBinding | ArmTransferBinding, ...]) -> tuple[ProcessBinding | ArmTransferBinding, ...]:
    if proposal.status != "READY" or not proposal.moves:
        raise ValueError("Only a ready nonempty inventory proposal can be bound")
    if not isinstance(bindings, tuple) or not all(isinstance(b, (ProcessBinding, ArmTransferBinding)) for b in bindings):
        raise ValueError("Expected immutable installed bindings")
    by_move = {(b.cargo_id, b.source, b.destination): b for b in bindings}
    if len(by_move) != len(bindings):
        raise ValueError("Ambiguous process binding")
    try:
        return tuple(by_move[(m.cargo_id, m.source, m.destination)] for m in proposal.moves)
    except KeyError as exc:
        raise ValueError("A move has no installed process binding") from exc

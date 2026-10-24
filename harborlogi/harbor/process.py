from dataclasses import dataclass
import hashlib
import json
import re

from harbor.model import identifier
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
        for key in ("request_digest", "policy_digest", "evidence_digest"):
            if not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]):
                raise ValueError("Invalid physical receipt digest")
        for key in ("receipt_id", "action_id", "attempt_id"):
            identifier(receipt.get(key))
        return receipt


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

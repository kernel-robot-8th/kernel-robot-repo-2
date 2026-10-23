from dataclasses import dataclass
import re

from harbor.model import identifier
from harbor.retrieval import RetrievalProposal


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


def bind_plan(proposal: RetrievalProposal, bindings: tuple[ProcessBinding, ...]) -> tuple[ProcessBinding, ...]:
    if proposal.status != "READY" or not proposal.moves:
        raise ValueError("Only a ready nonempty inventory proposal can be bound")
    if not isinstance(bindings, tuple) or not all(isinstance(b, ProcessBinding) for b in bindings):
        raise ValueError("Expected immutable installed bindings")
    by_move = {(b.cargo_id, b.source, b.destination): b for b in bindings}
    if len(by_move) != len(bindings):
        raise ValueError("Ambiguous process binding")
    try:
        return tuple(by_move[(m.cargo_id, m.source, m.destination)] for m in proposal.moves)
    except KeyError as exc:
        raise ValueError("A move has no installed process binding") from exc

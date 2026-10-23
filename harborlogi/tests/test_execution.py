from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from harbor.process import ProcessBinding, bind_plan
from harbor.rosy_http import RosyClient
from harbor.workflow import BusinessLedger, MoveEvidence
from test_policy import NOW, yard, constraints
from harbor.model import OutboundRequest
from harbor.outbound import plan_outbound


def bound_moves():
    plan = plan_outbound(yard(), OutboundRequest("outbound", 3), constraints(), now=NOW)
    bindings = tuple(ProcessBinding(m.cargo_id, m.source, m.destination, "recipe", "a" * 64,
                                   "cell", "b" * 64, "workcell", "installed", "c" * 64) for m in plan.moves)
    return bind_plan(plan, bindings)


class FakeFleet:
    def __init__(self):
        self.calls = []
        self.attempt = "attempt-1"
        self.status = "GOAL_CONFIRMED"
        self.fail_post = False
        self.mission = None

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path.endswith("/compile"):
            return {"candidate": {"kind": "cell_job", "recipe_sha256": "a" * 64, "cell_sha256": "b" * 64},
                    "process_artifact_digest": "c" * 64, "summary": {"transfer_count": 1}}
        if path.endswith("/proposals"):
            if self.fail_post:
                raise TimeoutError("response lost")
            self.mission = f"mission-{sum(p.endswith('/proposals') for _, p, _ in self.calls)}"
            return {"proposal": {"proposal_id": self.mission, "state": "RESOLVED", "request_key": body["request_key"]},
                    "mission": {"mission_id": self.mission, "status": "PROPOSED", "process_artifact_digest": "c" * 64}}
        if method == "GET":
            return {"job": {"mission_id": self.mission, "status": self.status,
                            "steps": [{"attempt_id": self.attempt}]}}
        return {"job": {"mission_id": "trip-1"}}


class ProcessTests(unittest.TestCase):
    def test_every_move_is_bound_without_partial_fallback(self):
        moves = bound_moves()
        self.assertEqual([m.cargo_id for m in moves], ["rehandle", "outbound", "rehandle"])
        plan = plan_outbound(yard(), OutboundRequest("outbound", 3), constraints(), now=NOW)
        with self.assertRaises(ValueError):
            bind_plan(plan, (moves[0],))

    def test_invalid_document_digest_is_rejected(self):
        with self.assertRaises(ValueError):
            replace(bound_moves()[0], recipe_digest="changed")

    def test_compile_validates_revision_and_single_step(self):
        for response in [
            {"candidate": {"kind": "cell_job", "recipe_sha256": "d" * 64, "cell_sha256": "b" * 64},
             "process_artifact_digest": "c" * 64, "summary": {"transfer_count": 1}},
            {"candidate": {"kind": "cell_job", "recipe_sha256": "a" * 64, "cell_sha256": "b" * 64},
             "process_artifact_digest": "c" * 64, "summary": {"transfer_count": 2}},
        ]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                RosyClient(transport=lambda *args: response).compile(bound_moves()[0])

    def test_candidate_request_uses_key_and_pool_without_platform_imports(self):
        fleet = FakeFleet()
        RosyClient(transport=fleet).trip(("p2", "p3"), ({"to": "stand"},), request_key="trip-key")
        self.assertEqual(fleet.calls[-1][2], {"candidate_robot_ids": ["p2", "p3"],
                                              "request_key": "trip-key", "steps": [{"to": "stand"}]})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "business.sqlite"
        self.ledger = BusinessLedger(self.path, evidence_sources=("installed-camera",))
        self.fleet = FakeFleet()
        self.client = RosyClient(transport=self.fleet)
        self.prepare()

    def prepare(self, revision=3):
        return self.ledger.prepare("request-1", revision, bound_moves(), outbound_id="outbound", outbound_stand="stand")

    def evidence(self, **changes):
        move = bound_moves()[self.ledger.read("request-1")["step_index"]]
        value = MoveEvidence("evidence-1", self.ledger.read("request-1")["mission_id"], "attempt-1", 3, "installed-camera", NOW,
                             move.cargo_id, move.destination, True)
        return replace(value, **changes)

    def start(self):
        self.ledger.propose("request-1", self.client)
        self.ledger.refresh("request-1", self.client, now=NOW)

    def test_acceptance_and_action_success_do_not_confirm_a_move(self):
        self.ledger.propose("request-1", self.client)
        self.assertEqual(self.ledger.read("request-1")["state"], "WAITING")
        self.fleet.status = "ACTION_SUCCEEDED"
        self.ledger.refresh("request-1", self.client, now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.confirm("request-1", self.evidence(), now=NOW)
        self.assertEqual(self.ledger.read("request-1")["step_index"], 0)

    def test_wrong_attempt_revision_source_cargo_destination_or_time_never_advances(self):
        self.start()
        for changes in ({"attempt_id": "old"}, {"revision": 2}, {"source_id": "planner"},
                        {"cargo_id": "wrong"}, {"destination": "wrong"}, {"arms_retreated": False},
                        {"observed_at": NOW - timedelta(seconds=6)},
                        {"observed_at": NOW + timedelta(seconds=1)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.ledger.confirm("request-1", self.evidence(**changes), now=NOW)
        self.assertEqual(self.ledger.read("request-1")["step_index"], 0)

    def test_one_stage_confirmation_allows_only_the_next_proposal(self):
        self.start()
        self.ledger.confirm("request-1", self.evidence(), now=NOW)
        row = self.ledger.read("request-1")
        self.assertEqual((row["state"], row["step_index"]), ("PLANNED", 1))
        self.assertIsNone(row["mission_id"])
        self.assertEqual(sum(p.endswith("/proposals") for _, p, _ in self.fleet.calls), 1)

    def test_duplicate_request_and_conflicting_contents(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare(revision=4)
        self.ledger.propose("request-1", self.client)
        with self.assertRaises(ValueError):
            self.ledger.propose("request-1", self.client)
        self.assertEqual(sum(p.endswith("/proposals") for _, p, _ in self.fleet.calls), 1)

    def test_uncertain_post_is_durable_hold_without_retry(self):
        self.fleet.fail_post = True
        with self.assertRaises(TimeoutError):
            self.ledger.propose("request-1", self.client)
        restarted = BusinessLedger(self.path, evidence_sources=("installed-camera",))
        self.assertEqual(restarted.read("request-1")["state"], "HOLD")
        with self.assertRaises(ValueError):
            restarted.propose("request-1", self.client)

    def test_restart_requires_fresh_fleet_reconciliation(self):
        self.start()
        restarted = BusinessLedger(self.path, evidence_sources=("installed-camera",))
        with self.assertRaises(ValueError):
            restarted.confirm("request-1", self.evidence(), now=NOW)
        restarted.refresh("request-1", self.client, now=NOW)
        restarted.confirm("request-1", self.evidence(), now=NOW)

    def test_duplicate_evidence_cannot_confirm_a_later_move(self):
        self.start()
        self.ledger.confirm("request-1", self.evidence(), now=NOW)
        self.start()
        with self.assertRaises(ValueError):
            self.ledger.confirm("request-1", self.evidence(), now=NOW)
        self.assertEqual(self.ledger.read("request-1")["step_index"], 1)

    def test_stale_fleet_reconciliation_cannot_advance(self):
        self.start()
        now = NOW + timedelta(seconds=6)
        with self.assertRaises(ValueError):
            self.ledger.confirm("request-1", self.evidence(observed_at=now), now=now)

    def test_late_post_response_does_not_undo_restart_hold(self):
        fleet = self.fleet
        def transport(method, path, body):
            if path.endswith("/proposals"):
                BusinessLedger(self.path, evidence_sources=("installed-camera",))
            return fleet(method, path, body)
        with self.assertRaises(ValueError):
            self.ledger.propose("request-1", RosyClient(transport=transport))
        row = self.ledger.read("request-1")
        self.assertEqual(row["state"], "HOLD")
        self.assertEqual(row["mission_id"], "mission-1")

    def test_compiler_artifact_change_is_not_silently_accepted(self):
        fleet = self.fleet
        def transport(method, path, body):
            result = fleet(method, path, body)
            if path.endswith("/proposals"):
                result["mission"]["process_artifact_digest"] = "d" * 64
            return result
        with self.assertRaises(ValueError):
            self.ledger.propose("request-1", RosyClient(transport=transport))
        self.assertEqual(self.ledger.read("request-1")["state"], "HOLD")

    def test_all_moves_still_require_outbound_placement_before_business_completion(self):
        for index in range(3):
            self.start()
            self.ledger.confirm("request-1", self.evidence(evidence_id=f"e{index}"), now=NOW)
        self.assertEqual(self.ledger.read("request-1")["state"], "AWAITING_FINAL_EVIDENCE")
        placement = MoveEvidence("placement", "mission-2", "attempt-1", 3, "installed-camera", NOW,
                              "outbound", "stand", True, placed=True)
        with self.assertRaises(ValueError):
            self.ledger.finish("request-1", replace(placement, placed=False), now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.finish("request-1", replace(placement, attempt_id="old"), now=NOW)
        self.ledger.finish("request-1", placement, now=NOW)
        self.assertEqual(self.ledger.read("request-1")["state"], "COMPLETED")

    def test_race_late_refresh_cannot_overwrite_next_stage(self):
        self.start()
        evidence = self.evidence()
        def transport(method, path, body):
            result = self.fleet(method, path, body)
            self.ledger.confirm("request-1", evidence, now=NOW)
            return result
        with self.assertRaises(ValueError):
            self.ledger.refresh("request-1", RosyClient(transport=transport), now=NOW)
        row = self.ledger.read("request-1")
        self.assertEqual((row["state"], row["step_index"], row["mission_id"]), ("PLANNED", 1, None))

    def test_race_late_refresh_cannot_overwrite_completed_business(self):
        for index in range(2):
            self.start()
            self.ledger.confirm("request-1", self.evidence(evidence_id=f"e{index}"), now=NOW)
        self.start()
        evidence = self.evidence(evidence_id="e2")
        placement = MoveEvidence("placement", "mission-2", "attempt-1", 3, "installed-camera", NOW,
                              "outbound", "stand", True, placed=True)
        def transport(method, path, body):
            result = self.fleet(method, path, body)
            self.ledger.confirm("request-1", evidence, now=NOW)
            self.ledger.finish("request-1", placement, now=NOW)
            return result
        with self.assertRaises(ValueError):
            self.ledger.refresh("request-1", RosyClient(transport=transport), now=NOW)
        self.assertEqual(self.ledger.read("request-1")["state"], "COMPLETED")

    def test_race_delayed_compile_never_submits_a_previous_stage(self):
        def transport(method, path, body):
            result = self.fleet(method, path, body)
            self.start()
            self.ledger.confirm("request-1", self.evidence(), now=NOW)
            return result
        with self.assertRaises(ValueError):
            self.ledger.propose("request-1", RosyClient(transport=transport))
        row = self.ledger.read("request-1")
        self.assertEqual((row["state"], row["step_index"], row["mission_id"]), ("PLANNED", 1, None))
        self.assertEqual(sum(p.endswith("/proposals") for _, p, _ in self.fleet.calls), 1)

    def test_race_old_refresh_cannot_replace_another_readers_new_attempt(self):
        self.start()
        other = BusinessLedger(self.path, evidence_sources=("installed-camera",))
        def transport(method, path, body):
            result = self.fleet(method, path, body)
            self.fleet.attempt, self.fleet.status = "attempt-2", "RUNNING"
            other.refresh("request-1", self.client, now=NOW)
            return result
        with self.assertRaises(ValueError):
            self.ledger.refresh("request-1", RosyClient(transport=transport), now=NOW)
        self.assertEqual(self.ledger.read("request-1")["attempt_id"], "attempt-2")
        self.assertEqual(self.ledger.read("request-1")["goal_confirmed"], 0)


if __name__ == "__main__":
    unittest.main()

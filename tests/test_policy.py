from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest

from harbor.model import CargoHolding, PortConstraints, RetrievalRequest, Slot, YardSnapshot
from harbor.retrieval import plan_retrieval
from harbor.site import Site
from harbor.availability import TransportState, transport_candidates


NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def yard(stack=("target", "blocker")):
    return YardSnapshot(3, NOW, (
        Slot("yard", stack, 3), Slot("temp", (), 2), Slot("supply", (), 1),
    ))


def constraints():
    return PortConstraints("supply", ("temp",), frozenset({
        ("yard", "temp"), ("temp", "yard"), ("yard", "supply"),
    }), timedelta(seconds=5))


class RetrievalTests(unittest.TestCase):
    def test_preview_reports_proposal_and_required_evidence(self):
        result = subprocess.run([sys.executable, "-m", "harbor"], check=True, capture_output=True, text=True,
                                cwd=Path(__file__).parents[1])
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["status"], "READY")
        self.assertEqual(len(proposal["moves"]), 3)
        self.assertEqual(proposal["required_completion"], [
            "target_unloaded_by_supply", "blockers_restored", "arms_clear",
        ])

    def plan(self, snapshot=None, rules=None, request=None, now=NOW):
        return plan_retrieval(snapshot or yard(), request or RetrievalRequest("target", 3),
                              rules or constraints(), now=now)

    def test_top_target_only_needs_retrieval(self):
        proposal = self.plan(yard(("target",)))
        self.assertEqual(proposal.status, "READY")
        self.assertEqual([(m.cargo_id, m.source, m.destination) for m in proposal.moves],
                         [("target", "yard", "supply")])
        self.assertIn("target_unloaded_by_supply", proposal.required_completion)
        self.assertIn("arms_clear", proposal.required_completion)

    def test_blocker_is_restored_and_observation_is_unchanged(self):
        snapshot = yard()
        proposal = self.plan(snapshot)
        self.assertEqual(proposal.status, "READY")
        self.assertEqual([(m.cargo_id, m.source, m.destination) for m in proposal.moves], [
            ("blocker", "yard", "temp"), ("target", "yard", "supply"),
            ("blocker", "temp", "yard"),
        ])
        self.assertEqual(snapshot.slots[0].cargo, ("target", "blocker"))
        self.assertEqual(proposal.source_revision, 3)
        self.assertEqual(proposal, self.plan(snapshot))

    def test_multiple_blockers_restore_original_order(self):
        proposal = self.plan(yard(("target", "b1", "b2")))
        self.assertEqual([m.cargo_id for m in proposal.moves], ["b2", "b1", "target", "b1", "b2"])

    def test_no_legal_temporary_capacity_is_hold_without_partial_plan(self):
        snapshot = yard(("target", "b1", "b2"))
        snapshot = replace(snapshot, slots=(snapshot.slots[0], Slot("temp", (), 1), snapshot.slots[2]))
        proposal = self.plan(snapshot)
        self.assertEqual(proposal.status, "HOLD")
        self.assertEqual(proposal.reason, "NO_LEGAL_TEMPORARY_SLOT")
        self.assertEqual(proposal.moves, ())

    def test_missing_return_reachability_is_hold(self):
        rules = replace(constraints(), transfers=frozenset({("yard", "temp"), ("yard", "supply")}))
        self.assertEqual(self.plan(rules=rules).status, "HOLD")

    def test_unreachable_target_is_hold(self):
        self.assertEqual(self.plan(rules=replace(constraints(), transfers=frozenset())).reason,
                         "TARGET_TRANSFER_UNAVAILABLE")

    def test_stale_and_future_observations_are_hold(self):
        for delta in (-1, 6):
            with self.subTest(delta=delta):
                self.assertEqual(self.plan(now=NOW + timedelta(seconds=delta)).reason,
                                 "OBSERVATION_NOT_CURRENT")

    def test_unknown_or_known_physical_holding_is_not_a_free_slot(self):
        for holder in (None, "arm-a"):
            snapshot = replace(yard(), holdings=(CargoHolding("airborne", holder),))
            self.assertEqual(self.plan(snapshot).reason, "PHYSICAL_HOLDING_REQUIRES_RECONCILIATION")

    def test_revision_missing_target_and_full_destination_do_not_produce_moves(self):
        cases = [self.plan(request=RetrievalRequest("target", 2)),
                 self.plan(request=RetrievalRequest("missing", 3)),
                 self.plan(replace(yard(), slots=yard().slots[:2] + (Slot("supply", ("other",), 1),)))]
        for proposal in cases:
            self.assertEqual(proposal.status, "HOLD")
            self.assertFalse(proposal.moves)

    def test_target_at_output_is_not_reported_as_completed(self):
        snapshot = replace(yard(()), slots=(Slot("yard", (), 3), Slot("temp", (), 2),
                                           Slot("supply", ("target",), 1)))
        self.assertEqual(self.plan(snapshot).reason, "TARGET_ALREADY_AT_SUPPLY_REQUIRES_EVIDENCE")

    def test_unknown_slot_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(rules=replace(constraints(), temporary_slots=("missing",)))


class ModelTests(unittest.TestCase):
    def test_duplicate_cargo_slots_and_overcapacity_rejected(self):
        for slots in [(Slot("a", ("x",), 1), Slot("b", ("x",), 1)),
                      (Slot("a", (), 1), Slot("a", (), 1))]:
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                YardSnapshot(1, NOW, slots)
        with self.assertRaises(ValueError):
            Slot("a", ("x", "y"), 1)

    def test_observed_and_held_cargo_cannot_overlap(self):
        with self.assertRaises(ValueError):
            replace(yard(), holdings=(CargoHolding("target", "arm-a"),))

    def test_invalid_time_revision_and_identifiers_rejected(self):
        for changes in ({"revision": -1}, {"revision": True}, {"observed_at": NOW.replace(tzinfo=None)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(yard(), **changes)
        with self.assertRaises(ValueError):
            RetrievalRequest("", 1)
        with self.assertRaises(ValueError):
            Slot("a", (), True)
        with self.assertRaises(ValueError):
            replace(constraints(), max_observation_age=timedelta(0))

    def test_mutable_containers_rejected(self):
        with self.assertRaises(ValueError):
            Slot("a", ["x"], 1)
        with self.assertRaises(ValueError):
            replace(constraints(), temporary_slots=["temp"])


class SiteTests(unittest.TestCase):
    def site_data(self):
        return json.loads((Path(__file__).parents[1] / "config/site.example.json").read_text())

    def test_example_and_identity_change_keep_roles_and_pool(self):
        data = self.site_data()
        site = Site.from_dict(data)
        self.assertEqual(site.max_moving_robots, 1)
        self.assertEqual(site.charging_slots, 1)
        self.assertEqual(len(site.transport_ids), 3)
        data["arms"] = {role: "renamed-" + device for role, device in data["arms"].items()}
        data["transport_ids"] = ["new-1", "new-2", "new-3"]
        changed = Site.from_dict(data)
        self.assertEqual(changed.transfers, site.transfers)
        self.assertEqual(set(changed.arms), {"S", "A", "B"})

    def test_bad_site_configuration_rejected(self):
        for field, value in [("charging_slots", 2), ("max_moving_robots", 2),
                             ("transport_ids", ["p1", "p1", "p3"]),
                             ("arms", {"S": "a", "A": "a", "B": "b"}),
                             ("waiting_slots", ["missing"]),
                             ("transfers", [["yard", "missing"]])]:
            data = self.site_data()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                Site.from_dict(data)


class AvailabilityTests(unittest.TestCase):
    def test_manual_charging_loaded_unknown_and_maintenance_excluded(self):
        pool = ("p1", "p2", "p3")
        for status in ("charging", "loaded", "unknown", "maintenance"):
            states = (TransportState("p1", status), TransportState("p2", "available"))
            self.assertEqual(transport_candidates(pool, states), ("p2",))

    def test_charger_arrival_is_not_availability_or_charging_confirmation(self):
        self.assertEqual(transport_candidates(("p1",), (TransportState("p1", "waiting_charge"),)), ())

    def test_duplicate_unknown_robot_and_double_charging_rejected(self):
        for states in [(TransportState("p1", "available"), TransportState("p1", "loaded")),
                       (TransportState("other", "available"),),
                       (TransportState("p1", "charging"), TransportState("p2", "charging"))]:
            with self.subTest(states=states), self.assertRaises(ValueError):
                transport_candidates(("p1", "p2", "p3"), states)


if __name__ == "__main__":
    unittest.main()

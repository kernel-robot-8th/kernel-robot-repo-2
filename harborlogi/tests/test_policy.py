from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest

from harbor.model import CargoHolding, PortConstraints, OutboundRequest, Bay, YardSnapshot
from harbor.outbound import plan_outbound
from harbor.site import Site
from harbor.availability import TransferVehicleState, transfer_candidates


NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def yard(stack=("outbound", "rehandle")):
    return YardSnapshot(3, NOW, (
        Bay("yard", stack, 3), Bay("temp", (), 2), Bay("stand", (), 1),
    ))


def constraints():
    return PortConstraints("stand", ("temp",), frozenset({
        ("yard", "temp"), ("temp", "yard"), ("yard", "stand"),
    }), timedelta(seconds=5))


class OutboundTests(unittest.TestCase):
    def test_preview_reports_proposal_and_required_evidence(self):
        result = subprocess.run([sys.executable, "-m", "harbor"], check=True, capture_output=True, text=True,
                                cwd=Path(__file__).parents[1])
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["status"], "READY")
        self.assertEqual(len(proposal["moves"]), 3)
        self.assertEqual(proposal["required_completion"], [
            "outbound_placed_on_stand", "rehandles_restored", "arms_retreated",
        ])

    def plan(self, snapshot=None, rules=None, request=None, now=NOW):
        return plan_outbound(snapshot or yard(), request or OutboundRequest("outbound", 3),
                              rules or constraints(), now=now)

    def test_top_outbound_cargo_only_needs_its_own_move(self):
        proposal = self.plan(yard(("outbound",)))
        self.assertEqual(proposal.status, "READY")
        self.assertEqual([(m.cargo_id, m.source, m.destination) for m in proposal.moves],
                         [("outbound", "yard", "stand")])
        self.assertIn("outbound_placed_on_stand", proposal.required_completion)
        self.assertIn("arms_retreated", proposal.required_completion)

    def test_rehandle_is_restored_and_observation_is_unchanged(self):
        snapshot = yard()
        proposal = self.plan(snapshot)
        self.assertEqual(proposal.status, "READY")
        self.assertEqual([(m.cargo_id, m.source, m.destination) for m in proposal.moves], [
            ("rehandle", "yard", "temp"), ("outbound", "yard", "stand"),
            ("rehandle", "temp", "yard"),
        ])
        self.assertEqual(snapshot.bays[0].cargo, ("outbound", "rehandle"))
        self.assertEqual(proposal.source_revision, 3)
        self.assertEqual(proposal, self.plan(snapshot))

    def test_multiple_rehandles_restore_original_order(self):
        proposal = self.plan(yard(("outbound", "b1", "b2")))
        self.assertEqual([m.cargo_id for m in proposal.moves], ["b2", "b1", "outbound", "b1", "b2"])

    def test_no_legal_temporary_capacity_is_hold_without_partial_plan(self):
        snapshot = yard(("outbound", "b1", "b2"))
        snapshot = replace(snapshot, bays=(snapshot.bays[0], Bay("temp", (), 1), snapshot.bays[2]))
        proposal = self.plan(snapshot)
        self.assertEqual(proposal.status, "HOLD")
        self.assertEqual(proposal.reason, "NO_LEGAL_TEMPORARY_BAY")
        self.assertEqual(proposal.moves, ())

    def test_missing_return_reachability_is_hold(self):
        rules = replace(constraints(), allowed_moves=frozenset({("yard", "temp"), ("yard", "stand")}))
        self.assertEqual(self.plan(rules=rules).status, "HOLD")

    def test_unreachable_outbound_cargo_is_hold(self):
        self.assertEqual(self.plan(rules=replace(constraints(), allowed_moves=frozenset())).reason,
                         "OUTBOUND_MOVE_UNAVAILABLE")

    def test_stale_and_future_observations_are_hold(self):
        for delta in (-1, 6):
            with self.subTest(delta=delta):
                self.assertEqual(self.plan(now=NOW + timedelta(seconds=delta)).reason,
                                 "OBSERVATION_NOT_CURRENT")

    def test_unknown_or_known_physical_holding_is_not_a_free_bay(self):
        for holder in (None, "arm-a"):
            snapshot = replace(yard(), holdings=(CargoHolding("airborne", holder),))
            self.assertEqual(self.plan(snapshot).reason, "PHYSICAL_HOLDING_REQUIRES_RECONCILIATION")

    def test_revision_missing_outbound_and_full_destination_do_not_produce_moves(self):
        cases = [self.plan(request=OutboundRequest("outbound", 2)),
                 self.plan(request=OutboundRequest("missing", 3)),
                 self.plan(replace(yard(), bays=yard().bays[:2] + (Bay("stand", ("other",), 1),)))]
        for proposal in cases:
            self.assertEqual(proposal.status, "HOLD")
            self.assertFalse(proposal.moves)

    def test_outbound_on_stand_is_not_reported_as_completed(self):
        snapshot = replace(yard(()), bays=(Bay("yard", (), 3), Bay("temp", (), 2),
                                           Bay("stand", ("outbound",), 1)))
        self.assertEqual(self.plan(snapshot).reason, "OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE")

    def test_unknown_bay_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(rules=replace(constraints(), temporary_bays=("missing",)))


class ModelTests(unittest.TestCase):
    def test_duplicate_cargo_bays_and_overcapacity_rejected(self):
        for bays in [(Bay("a", ("x",), 1), Bay("b", ("x",), 1)),
                      (Bay("a", (), 1), Bay("a", (), 1))]:
            with self.subTest(bays=bays), self.assertRaises(ValueError):
                YardSnapshot(1, NOW, bays)
        with self.assertRaises(ValueError):
            Bay("a", ("x", "y"), 1)

    def test_observed_and_held_cargo_cannot_overlap(self):
        with self.assertRaises(ValueError):
            replace(yard(), holdings=(CargoHolding("outbound", "arm-a"),))

    def test_invalid_time_revision_and_identifiers_rejected(self):
        for changes in ({"revision": -1}, {"revision": True}, {"observed_at": NOW.replace(tzinfo=None)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(yard(), **changes)
        with self.assertRaises(ValueError):
            OutboundRequest("", 1)
        with self.assertRaises(ValueError):
            Bay("a", (), True)
        with self.assertRaises(ValueError):
            replace(constraints(), max_observation_age=timedelta(0))

    def test_mutable_containers_rejected(self):
        with self.assertRaises(ValueError):
            Bay("a", ["x"], 1)
        with self.assertRaises(ValueError):
            replace(constraints(), temporary_bays=["temp"])


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
            states = (TransferVehicleState("p1", status), TransferVehicleState("p2", "available"))
            self.assertEqual(transfer_candidates(pool, states), ("p2",))

    def test_charger_arrival_is_not_availability_or_charging_confirmation(self):
        self.assertEqual(transfer_candidates(("p1",), (TransferVehicleState("p1", "waiting_charge"),)), ())

    def test_duplicate_unknown_robot_and_double_charging_rejected(self):
        for states in [(TransferVehicleState("p1", "available"), TransferVehicleState("p1", "loaded")),
                       (TransferVehicleState("other", "available"),),
                       (TransferVehicleState("p1", "charging"), TransferVehicleState("p2", "charging"))]:
            with self.subTest(states=states), self.assertRaises(ValueError):
                transfer_candidates(("p1", "p2", "p3"), states)


if __name__ == "__main__":
    unittest.main()

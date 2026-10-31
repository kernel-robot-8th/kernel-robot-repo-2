"""Design 29: the per-(move, arm) installed correspondence replaces the reach table."""
from datetime import datetime, timedelta, timezone
import unittest

from harbor.model import Bay, OutboundRequest, PortConstraints, YardSnapshot
from harbor.outbound import CargoMove, OutboundProposal, plan_outbound
from harbor.process import ArmTransferBinding, arm_candidates, bind_plan


NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
MOVES = (("rehandle", "yard", "temp"), ("outbound", "yard", "stand"), ("rehandle", "temp", "yard"))


def yard():
    return YardSnapshot(3, NOW, (Bay("yard", ("outbound", "rehandle"), 3), Bay("temp", (), 2), Bay("stand", (), 1)))


def rules():
    return PortConstraints("stand", ("temp",), frozenset({
        ("yard", "temp"), ("temp", "yard"), ("yard", "stand"),
    }), timedelta(seconds=5), "conveyor", frozenset())


def binding(move, arm=''):
    cargo, source, destination = move
    return ArmTransferBinding(cargo, source, destination, "cell",
                              f"instance-{cargo}-{source}-{destination}-{arm or 'legacy'}",
                              f"object-{cargo}", f"{source}-ref", f"{destination}-ref", arm)


class PerArmCorrespondenceTests(unittest.TestCase):
    def plan(self, **kwargs):
        return plan_outbound(yard(), OutboundRequest("outbound", 3), rules(), now=NOW, **kwargs)

    def test_correspondence_assigns_primary_then_alternates_without_any_reach_table(self):
        candidates = {MOVES[0]: ("B", "A"), MOVES[1]: ("A",), MOVES[2]: ("A", "B")}
        proposal = self.plan(arm_candidates=candidates)
        self.assertEqual(proposal.status, "READY")
        self.assertEqual([(m.primary_arm, m.alternate_arms) for m in proposal.moves],
                         [("B", ("A",)), ("A", ()), ("A", ("B",))])

    def test_uncovered_move_holds_and_the_legacy_path_stays_unverified(self):
        held = self.plan(arm_candidates={})
        self.assertEqual((held.status, held.reason), ("HOLD", "NO_ARM_REACHES_MOVE"))
        self.assertEqual(self.plan().reason, "ARM_REACH_UNVERIFIED")

    def test_candidates_come_from_installed_order_and_bind_plan_takes_the_primary(self):
        bindings = (binding(MOVES[0], "A"), binding(MOVES[0], "B"), binding(MOVES[1], "A"), binding(MOVES[2], "A"))
        self.assertEqual(arm_candidates(bindings)[MOVES[0]], ("A", "B"))
        proposal = OutboundProposal("READY", "R", 3, tuple(CargoMove(*move, "A") for move in MOVES))
        primary = bind_plan(proposal, bindings)
        self.assertEqual([b.instance_id for b in primary],
                         ["instance-rehandle-yard-temp-A", "instance-outbound-yard-stand-A",
                          "instance-rehandle-temp-yard-A"])
        with self.assertRaises(ValueError):  # The same arm twice for one move stays ambiguous.
            bind_plan(proposal, (binding(MOVES[0], "A"), binding(MOVES[0], "A")))
        with self.assertRaises(ValueError):  # Two legacy entries for one move stay ambiguous.
            bind_plan(proposal, (binding(MOVES[0]), binding(MOVES[0])))


if __name__ == "__main__":
    unittest.main()

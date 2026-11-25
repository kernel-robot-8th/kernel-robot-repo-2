import unittest
from datetime import datetime, timedelta, timezone

from harbor.layout import YardCell, YardLayout
from harbor.sequencing import ScheduleEntry, evaluate_order, evaluate_schedule, rank_retrieval_orders
from harbor.spatial import Area, CargoShape, Placement, SpatialPolicy, SpatialSnapshot

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)
LAYOUT = YardLayout(3, 3, 3, tuple(YardCell(f'Y{r}{c}', r, c) for r in range(1, 4) for c in range(1, 4)))


def snapshot(placements):
    """1×3 containers only: each one fills a yard row at its level."""
    return SpatialSnapshot(7, NOW, LAYOUT, (Area('yard', 3, 3, 3), Area('temp', 1, 3, 3), Area('stand', 1, 3, 1)),
                           tuple(CargoShape(code, 3, i + 1) for i, (code, _, _) in enumerate(placements)),
                           tuple(Placement(code, 'yard', row, 1, level) for code, row, level in placements))


def policy():
    return SpatialPolicy('stand', ('temp',), frozenset({('yard', 'yard'), ('yard', 'temp'), ('temp', 'yard'), ('yard', 'stand')}),
                         frozenset((arm, place) for arm in ('A', 'B') for place in ('temp', 'stand', *(c.bay_id for c in LAYOUT.cells))),
                         timedelta(minutes=5))


class SequencingTests(unittest.TestCase):
    def test_taking_the_top_container_first_avoids_a_rehandle(self):
        # B rests on A in row 1. Taking B first needs no rehandle; taking A first must move B away and back.
        yard = snapshot([('A', 1, 1), ('B', 1, 2), ('C', 2, 1)])
        ranked = rank_retrieval_orders(yard, policy(), ('A', 'B'), now=NOW, schedule={}, group=2)
        best, other = ranked[0], ranked[1]
        self.assertEqual((best.order, best.feasible, best.rehandles), (('B', 'A'), True, 0))
        self.assertEqual(best.total_moves, 2)
        self.assertEqual((other.order, other.rehandles), (('A', 'B'), 1))
        self.assertGreater(other.total_moves, best.total_moves)
        self.assertEqual([step.cargo_id for step in best.steps], ['B', 'A'])
        self.assertNotIn('A', {p.cargo_id for p in best.final.placements})

    def test_ties_keep_the_given_order(self):
        yard = snapshot([('A', 1, 1), ('B', 2, 1)])
        ranked = rank_retrieval_orders(yard, policy(), ('B', 'A'), now=NOW, schedule={}, group=1)
        self.assertEqual([s.order for s in ranked], [('B', 'A'), ('A', 'B')])
        self.assertEqual({s.total_moves for s in ranked}, {2})

    def test_a_blocked_order_is_ranked_last_and_says_why(self):
        yard = snapshot([('A', 1, 1)])
        ranked = rank_retrieval_orders(yard, policy(), ('A', 'Z'), now=NOW + timedelta(hours=1), schedule={}, group=3)
        self.assertTrue(all(not s.feasible for s in ranked))
        self.assertEqual(ranked[0].steps[-1].status, 'HOLD')
        self.assertTrue(ranked[0].steps[-1].reason)

    def test_large_groups_use_a_bounded_greedy_search(self):
        yard = snapshot([('A', 1, 1), ('B', 1, 2), ('C', 1, 3), ('D', 2, 1), ('E', 3, 1)])
        ranked = rank_retrieval_orders(yard, policy(), ('A', 'B', 'C', 'D', 'E'), now=NOW, schedule={}, group=3, exact_limit=3)
        self.assertLessEqual(len(ranked), 2)
        self.assertEqual(ranked[0].order[:3], ('C', 'B', 'A'))
        self.assertFalse(ranked[0].exhaustive)
        exact = rank_retrieval_orders(yard, policy(), ('A', 'B', 'C'), now=NOW, schedule={}, group=3)
        self.assertTrue(exact[0].exhaustive)

    def test_evaluate_order_wraps_simulation_and_rejects_duplicates(self):
        yard = snapshot([('A', 1, 1), ('B', 1, 2), ('C', 2, 1)])
        good = evaluate_order(yard, policy(), ('B', 'A'), now=NOW)
        rehandled = evaluate_order(yard, policy(), ('A', 'B'), now=NOW)
        self.assertEqual((good.feasible, good.rehandles, good.total_moves), (True, 0, 2))
        self.assertEqual((rehandled.feasible, rehandled.rehandles), (True, 1))
        self.assertGreater(rehandled.total_moves, good.total_moves)
        with self.assertRaises(ValueError):
            evaluate_order(yard, policy(), ('A', 'A'), now=NOW)

    def test_evaluate_schedule_mixed_inbound_and_outbound_enforces_storage_and_lifo(self):
        # Full 3x3x3 yard (9 slots of 1x3): 8 occupied, 1 empty at row 3 level 3.
        yard = snapshot([
            ('A', 1, 1), ('B', 1, 2), ('C', 1, 3),
            ('D', 2, 1), ('E', 2, 2), ('F', 2, 3),
            ('G', 3, 1), ('H', 3, 2),
        ])
        in1 = CargoShape('IN1', 3, 10)
        in2 = CargoShape('IN2', 3, 11)
        # Two inbounds before any outbound: IN1 takes the last storage spot, IN2 fails NO_LEGAL_PLACEMENT (never temp/stand).
        blocked = evaluate_schedule(
            yard, policy(),
            [ScheduleEntry('INBOUND', 'IN1', in1), ScheduleEntry('INBOUND', 'IN2', in2)],
            now=NOW,
            schedule={'C': (1, 1)},
        )
        self.assertFalse(blocked.feasible)
        self.assertEqual(blocked.steps[0].status, 'READY')
        self.assertEqual(blocked.steps[0].recommendation.area_id, 'yard')
        self.assertEqual((blocked.steps[1].status, blocked.steps[1].reason), ('HOLD', 'NO_LEGAL_PLACEMENT'))

        # Outbound C first frees a storage slot so both IN1 and IN2 succeed.
        ok = evaluate_schedule(
            yard, policy(),
            [
                ScheduleEntry('OUTBOUND', 'C'),
                ScheduleEntry('INBOUND', 'IN1', in1),
                ScheduleEntry('INBOUND', 'IN2', in2),
            ],
            now=NOW,
            schedule={'C': (1, 1)},
        )
        self.assertTrue(ok.feasible)
        self.assertEqual((ok.rehandles, ok.total_moves), (0, 1))
        self.assertTrue(all(s.recommendation.area_id == 'yard' for s in ok.steps if s.kind == 'INBOUND'))

        # Outbound with blockers when temp area has only 1 level cannot stage 2 blockers -> HOLD (no stand staging).
        tight_yard = SpatialSnapshot(
            7, NOW, LAYOUT,
            (Area('yard', 3, 3, 3), Area('temp', 1, 3, 1), Area('stand', 1, 3, 1)),
            yard.shapes, yard.placements,
        )
        no_temp = evaluate_schedule(
            tight_yard, policy(),
            [ScheduleEntry('OUTBOUND', 'A')],
            now=NOW,
            schedule={'A': (1, 1)},
        )
        self.assertFalse(no_temp.feasible)
        self.assertEqual(no_temp.steps[0].reason, 'NO_LEGAL_SPATIAL_PLAN')

    def test_evaluate_schedule_respects_reserved_cells_and_never_stacks_on_reservations(self):
        # Empty 3x3x3 yard: row 1 L1 is reserved externally -> IN1 must avoid row 1 and take row 2 L1.
        empty_yard = snapshot([])
        row1_l1 = frozenset({('yard', 1, 1, 1), ('yard', 1, 2, 1), ('yard', 1, 3, 1)})
        in1 = CargoShape('IN1', 3, 20)
        in2 = CargoShape('IN2', 3, 21)

        without_res = evaluate_schedule(empty_yard, policy(), [ScheduleEntry('INBOUND', 'IN1', in1)], now=NOW)
        self.assertEqual((without_res.steps[0].recommendation.row, without_res.steps[0].recommendation.level), (1, 1))

        with_res = evaluate_schedule(
            empty_yard, policy(),
            [ScheduleEntry('INBOUND', 'IN1', in1), ScheduleEntry('INBOUND', 'IN2', in2)],
            now=NOW,
            reserved_cells=row1_l1,
        )
        self.assertTrue(with_res.feasible)
        self.assertEqual((with_res.steps[0].recommendation.row, with_res.steps[0].recommendation.level), (2, 1))
        self.assertEqual((with_res.steps[1].recommendation.row, with_res.steps[1].recommendation.level), (3, 1))

        # Single-row yard (1x3x2) with its only ground slot reserved -> HOLD NO_LEGAL_PLACEMENT (no unobserved support).
        one_row_layout = YardLayout(1, 3, 2, tuple(YardCell(f'Y1{c}', 1, c) for c in range(1, 4)))
        one_row_yard = SpatialSnapshot(
            8, NOW, one_row_layout,
            (Area('yard', 1, 3, 2), Area('temp', 1, 3, 2), Area('stand', 1, 3, 1)),
            (), (),
        )
        one_row_policy = SpatialPolicy(
            'stand', ('temp',),
            frozenset({('yard', 'yard'), ('yard', 'temp'), ('temp', 'yard'), ('yard', 'stand')}),
            frozenset((arm, place) for arm in ('A', 'B') for place in ('temp', 'stand', *(c.bay_id for c in one_row_layout.cells))),
            timedelta(minutes=5),
        )
        blocked = evaluate_schedule(
            one_row_yard, one_row_policy,
            [ScheduleEntry('INBOUND', 'IN1', in1, reserved_cells=row1_l1)],
            now=NOW,
        )
        self.assertFalse(blocked.feasible)
        self.assertEqual((blocked.steps[0].status, blocked.steps[0].reason), ('HOLD', 'NO_LEGAL_PLACEMENT'))


if __name__ == '__main__':
    unittest.main()

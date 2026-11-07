"""Design 33: one outbound line per arm plate; the planner picks the line the moving arm reaches."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from harbor.layout import YardCell, YardLayout
from harbor.model import OutboundRequest
from harbor.spatial import Area, CargoShape, Placement, SpatialPolicy, SpatialSnapshot, plan_spatial

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)
LAYOUT = YardLayout(3, 3, 3, tuple(YardCell(f'Y{r}{c}', r, c) for r in range(1, 4) for c in range(1, 4)))
CELLS = [c.bay_id for c in LAYOUT.cells]


def snapshot(*placements):
    areas = (Area('yard', 3, 3, 3), Area('a_tmp', 1, 3, 3), Area('a_out', 1, 3, 1),
             Area('b_tmp', 1, 3, 3), Area('b_out', 1, 3, 1))
    return SpatialSnapshot(4, NOW, LAYOUT, areas, tuple(CargoShape(p.cargo_id, 1, i + 1) for i, p in enumerate(placements)),
                           tuple(placements))


def policy(a_cells, b_cells):
    reach = {('A', c) for c in a_cells} | {('B', c) for c in b_cells} | {('A', 'a_tmp'), ('A', 'a_out'), ('B', 'b_tmp'), ('B', 'b_out')}
    moves = {('yard', 'yard')} | {('yard', a) for a in ('a_tmp', 'b_tmp', 'a_out', 'b_out')} | {('a_tmp', 'yard'), ('b_tmp', 'yard')}
    return SpatialPolicy('a_out', ('a_tmp', 'b_tmp'), frozenset(moves), frozenset(reach), timedelta(minutes=1),
                         extra_outbound_areas=('b_out',))


class OutboundLineTests(unittest.TestCase):
    def plan(self, a_cells, b_cells):
        result = plan_spatial(snapshot(Placement('t', 'yard', 1, 1, 1)), OutboundRequest('t', 4), policy(a_cells, b_cells), now=NOW)
        self.assertEqual(result.status, 'READY')
        return result.moves[-1]

    def test_outbound_goes_to_the_line_of_the_arm_that_reaches_the_cargo(self):
        move = self.plan(['Y11'], [])
        self.assertEqual((move.destination_placement.area_id, move.primary_arm), ('a_out', 'A'))
        move = self.plan([], ['Y11'])
        self.assertEqual((move.destination_placement.area_id, move.primary_arm), ('b_out', 'B'))

    def test_cargo_already_on_any_outbound_line_is_not_replanned(self):
        source = snapshot(Placement('t', 'b_out', 1, 1, 1))
        result = plan_spatial(source, OutboundRequest('t', 4), policy(CELLS, CELLS), now=NOW)
        self.assertEqual(result.reason, 'OUTBOUND_ALREADY_ON_STAND_REQUIRES_EVIDENCE')

    def test_outbound_lines_must_be_unique_and_disjoint_from_other_roles(self):
        base = policy(CELLS, CELLS)
        for bad in (dict(extra_outbound_areas=('a_out',)), dict(extra_outbound_areas=('a_tmp',)),
                    dict(extra_outbound_areas=('yard',))):
            with self.assertRaises(ValueError):
                replace(base, **bad)
        self.assertEqual(base.outbound_areas, ('a_out', 'b_out'))


if __name__ == '__main__':
    unittest.main()

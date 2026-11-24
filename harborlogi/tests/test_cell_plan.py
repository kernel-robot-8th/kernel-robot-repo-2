"""Unit tests for pure domain dual-mode cell-plan payload translation and response validation."""
import unittest

from harbor.cell_plan import (
    build_harbor_plan_request,
    build_rosy_directive_request,
    observation_site_state,
    validate_cell_plan_response,
)


class CellPlanUnitTests(unittest.TestCase):
    def _observation(self):
        return {
            'schema': 'grid-placement-v1',
            'placements': [
                {'cargo_id': 'C1', 'area_id': 'yard', 'row': 1, 'column': 1, 'level': 1, 'orientation': 0},
                {'cargo_id': 'C2', 'area_id': 'yard', 'row': 1, 'column': 1, 'level': 2, 'orientation': 0},
            ],
        }

    def test_build_harbor_plan_request_translates_spatial_moves_and_inbound_recommendation(self):
        state = observation_site_state(self._observation())
        self.assertEqual(state, {'C1': 'yard-r1-c1-l1', 'C2': 'yard-r1-c1-l2'})

        spatial_outbound = {
            'status': 'READY',
            'model': 'grid-placement-v1',
            'moves': [
                {
                    'cargo_id': 'C2',
                    'primary_arm': 'A',
                    'phase_label': '재취급',
                    'source_placement': {'area_id': 'yard', 'row': 1, 'column': 1, 'level': 2},
                    'destination_placement': {'area_id': 'temp_a', 'row': 1, 'column': 1, 'level': 1},
                },
                {
                    'cargo_id': 'C1',
                    'primary_arm': 'A',
                    'phase_label': '반출',
                    'source_placement': {'area_id': 'yard', 'row': 1, 'column': 1, 'level': 1},
                    'destination_placement': {'area_id': 'stand', 'row': 1, 'column': 1, 'level': 1},
                },
            ],
        }
        req = build_harbor_plan_request(spatial_outbound, observation_payload=self._observation())
        self.assertEqual(req['mode'], 'harbor_plan')
        self.assertIs(req['propose'], True)
        self.assertEqual(req['state'], state)
        self.assertEqual(req['steps'], [
            {'cargo_id': 'C2', 'src_site': 'yard-r1-c1-l2', 'dst_site': 'temp_a-r1-c1-l1', 'arm_id': 'arm_a', 'phase_label': '재취급'},
            {'cargo_id': 'C1', 'src_site': 'yard-r1-c1-l1', 'dst_site': 'stand-r1-c1-l1', 'arm_id': 'arm_a', 'phase_label': '반출'},
        ])

        inbound = {
            'status': 'READY',
            'model': 'grid-placement-v1',
            'moves': [],
            'recommendation': {'cargo_id': 'C3', 'area_id': 'yard', 'row': 1, 'column': 4, 'level': 1},
        }
        req_in = build_harbor_plan_request(inbound, observation_payload=self._observation())
        self.assertEqual(req_in['steps'], [
            {'cargo_id': 'C3', 'src_site': 'belt', 'dst_site': 'yard-r1-c4-l1', 'phase_label': '입고'},
        ])

    def test_build_rosy_directive_request_supports_outbound_rehandle_and_goal(self):
        obs = self._observation()
        out = build_rosy_directive_request(mode='outbound', observation_payload=obs, cargo_id='C1')
        self.assertEqual(out, {
            'mode': 'outbound',
            'state': {'C1': 'yard-r1-c1-l1', 'C2': 'yard-r1-c1-l2'},
            'robot_id': 'rosy_01',
            'propose': True,
            'cargo_id': 'C1',
            'destination': 'stand',
        })
        reh = build_rosy_directive_request(mode='rehandle', observation_payload=obs, cargo_id='C1', avoid=['temp_b-r1-c1-l1'])
        self.assertEqual(reh['mode'], 'rehandle')
        self.assertEqual(reh['avoid'], ['temp_b-r1-c1-l1'])
        goal = build_rosy_directive_request(mode='goal', observation_payload=obs, goal={'yard:r1-s2': ['C1', 'C2']})
        self.assertEqual(goal['goal'], {'yard:r1-s2': ['C1', 'C2']})

        with self.assertRaises(ValueError):
            build_rosy_directive_request(mode='outbound', observation_payload={'schema': 'grid-placement-v1', 'placements': []}, cargo_id='C1')
        with self.assertRaises(ValueError):
            build_rosy_directive_request(mode='unknown', observation_payload=obs, cargo_id='C1')

    def test_validate_cell_plan_response_checks_source_and_dependency_chain(self):
        good = {
            'ok': True,
            'schema': 'rosy.cell-plan.v1',
            'plan': {'plan_id': 'cell-plan-1', 'order_source': 'harbor', 'total_steps': 2, 'status': 'PROPOSED'},
            'tasks': [
                {'task_id': 'cell-plan-1-s1', 'depends_on': None, 'status': 'PROPOSED', 'dst_wire_ref': 'temp_a-r1-c1-l1'},
                {'task_id': 'cell-plan-1-s2', 'depends_on': 'cell-plan-1-s1', 'status': 'PROPOSED', 'dst_wire_ref': 'stand-r1-c1-l1'},
            ],
        }
        self.assertIs(validate_cell_plan_response(good, expected_order_source='harbor', expected_step_count=2), good)
        with self.assertRaises(ValueError):
            validate_cell_plan_response(good, expected_order_source='rosy_r')
        broken_dep = {
            **good,
            'tasks': [
                good['tasks'][0],
                {**good['tasks'][1], 'depends_on': 'wrong-task'},
            ],
        }
        with self.assertRaises(ValueError):
            validate_cell_plan_response(broken_dep, expected_order_source='harbor')


if __name__ == '__main__':
    unittest.main()

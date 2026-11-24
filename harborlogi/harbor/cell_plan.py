"""Domain serialization and fail-closed validation for dual-mode ROSY cell plans (harbor_plan vs rosy_r)."""
from __future__ import annotations

from harbor.model import YARD_ARMS, identifier
from harbor.process import anchor_address

ROSY_ARM_BY_HARBOR = {'A': 'arm_a', 'B': 'arm_b'}
assert set(ROSY_ARM_BY_HARBOR) == set(YARD_ARMS)
AUTONOMOUS_DIRECTIVE_MODES = frozenset({'outbound', 'rehandle', 'goal'})
_LINEAR_TERMINAL_STATES = ('COMPLETED',)


def observation_site_state(observation_payload: dict | None) -> dict[str, str]:
    """Build {cargo_id: wire_ref} from a grid-placement-v1 observation payload."""
    if not isinstance(observation_payload, dict) or observation_payload.get('schema') != 'grid-placement-v1':
        raise ValueError('SPATIAL_OBSERVATION_REQUIRED')
    state: dict[str, str] = {}
    for entry in observation_payload.get('placements') or ():
        if not isinstance(entry, dict):
            raise ValueError('INVALID_SPATIAL_PLACEMENT')
        cargo_id = entry.get('cargo_id')
        identifier(cargo_id)
        if cargo_id in state:
            raise ValueError('DUPLICATE_CARGO_IN_OBSERVATION')
        state[cargo_id] = anchor_address(entry['area_id'], entry['row'], entry['column'], entry['level'])
    return state


def _arm_to_rosy(arm: str | None) -> str | None:
    if arm is None or arm == '':
        return None
    text = str(arm).strip()
    if text in ROSY_ARM_BY_HARBOR:
        return ROSY_ARM_BY_HARBOR[text]
    if text in ('arm_a', 'arm_b'):
        return text
    raise ValueError('INVALID_YARD_ARM')


def build_harbor_plan_request(
    plan_result: dict,
    *,
    observation_payload: dict | None = None,
    robot_id: str = 'rosy_01',
    cargo_id: str | None = None,
) -> dict:
    """Translate a Harbor READY spatial Plan.result into a Mode 1 (harbor_plan) ROSY payload."""
    if not isinstance(plan_result, dict) or plan_result.get('status') != 'READY':
        raise ValueError('PLAN_NOT_READY')
    if plan_result.get('model') != 'grid-placement-v1':
        raise ValueError('SPATIAL_PLAN_REQUIRED')
    identifier(robot_id)

    steps: list[dict] = []
    moves = plan_result.get('moves') or []
    if moves:
        for move in moves:
            if not isinstance(move, dict) or 'source_placement' not in move or 'destination_placement' not in move:
                raise ValueError('SPATIAL_MOVE_COORDINATES_REQUIRED')
            src_p = move['source_placement']
            dst_p = move['destination_placement']
            arm_id = _arm_to_rosy(move.get('primary_arm'))
            step_cargo = move.get('cargo_id')
            identifier(step_cargo)
            steps.append({
                'cargo_id': step_cargo,
                'src_site': anchor_address(src_p['area_id'], src_p['row'], src_p['column'], src_p['level']),
                'dst_site': anchor_address(dst_p['area_id'], dst_p['row'], dst_p['column'], dst_p['level']),
                **({'arm_id': arm_id} if arm_id is not None else {}),
                'phase_label': str(move.get('phase_label') or '이동'),
            })
    elif isinstance(plan_result.get('recommendation'), dict):
        rec = plan_result['recommendation']
        step_cargo = cargo_id or rec.get('cargo_id')
        identifier(step_cargo)
        steps.append({
            'cargo_id': step_cargo,
            'src_site': 'belt',
            'dst_site': anchor_address(rec['area_id'], rec['row'], rec['column'], rec['level']),
            'phase_label': '입고',
        })
    else:
        raise ValueError('PLAN_HAS_NO_EXECUTABLE_MOVES')

    payload: dict = {
        'mode': 'harbor_plan',
        'steps': steps,
        'robot_id': robot_id,
        'propose': True,
    }
    if observation_payload is not None:
        payload['state'] = observation_site_state(observation_payload)
    return payload


def build_rosy_directive_request(
    *,
    mode: str,
    observation_payload: dict | None,
    cargo_id: str | None = None,
    destination: str = 'stand',
    goal: dict[str, list[str]] | None = None,
    avoid: tuple[str, ...] | list[str] = (),
    robot_id: str = 'rosy_01',
) -> dict:
    """Build a Mode 2 high-level directive payload (ROSY plans sequence and temp moves autonomously)."""
    if mode not in AUTONOMOUS_DIRECTIVE_MODES:
        raise ValueError('INVALID_DIRECTIVE_MODE')
    identifier(robot_id)
    state = observation_site_state(observation_payload)
    if not state:
        raise ValueError('NO_OBSERVED_CARGO_STATE')

    payload: dict = {
        'mode': mode,
        'state': state,
        'robot_id': robot_id,
        'propose': True,
    }
    if mode in ('outbound', 'rehandle'):
        identifier(cargo_id)
        payload['cargo_id'] = cargo_id
        if mode == 'outbound' and destination:
            identifier(destination)
            payload['destination'] = destination
    else:
        if not isinstance(goal, dict) or not goal:
            raise ValueError('GOAL_STACKS_REQUIRED')
        payload['goal'] = goal
    if avoid:
        payload['avoid'] = [str(s) for s in avoid]
    return payload


def validate_cell_plan_response(
    response: dict,
    *,
    expected_order_source: str,
    expected_step_count: int | None = None,
) -> dict:
    """Validate rosy.cell-plan.v1 create/read response, preserving strict order_source and dependency chain."""
    if not isinstance(response, dict) or response.get('ok') is not True or response.get('schema') != 'rosy.cell-plan.v1':
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    plan = response.get('plan')
    tasks = response.get('tasks')
    if not isinstance(plan, dict) or not isinstance(tasks, list) or not tasks:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    if not isinstance(plan.get('plan_id'), str) or not plan['plan_id'].strip():
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    if plan.get('order_source') != expected_order_source:
        raise ValueError('CELL_PLAN_ORDER_SOURCE_MISMATCH')
    if type(plan.get('total_steps')) is not int or plan['total_steps'] != len(tasks):
        raise ValueError('CELL_PLAN_STEP_COUNT_MISMATCH')
    if expected_step_count is not None and len(tasks) != expected_step_count:
        raise ValueError('CELL_PLAN_STEP_COUNT_MISMATCH')

    prev_id = None
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        task_id = task.get('task_id')
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        if idx > 0 and task.get('depends_on') != prev_id:
            raise ValueError('CELL_PLAN_DEPENDENCY_BROKEN')
        if not isinstance(task.get('status'), str) or not task['status'].strip():
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        if not task.get('dst_wire_ref') and not task.get('dst_site'):
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        prev_id = task_id
    return response

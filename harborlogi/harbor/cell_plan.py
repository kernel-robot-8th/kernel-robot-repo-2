"""Domain serialization, typed DTOs, and fail-closed validation for dual-mode ROSY cell plans."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from harbor.model import YARD_ARMS, identifier
from harbor.process import anchor_address

CELL_PLAN_SCHEMA = 'rosy.cell-plan.v1'
ROSY_ARM_BY_HARBOR = {'A': 'arm_a', 'B': 'arm_b'}
assert set(ROSY_ARM_BY_HARBOR) == set(YARD_ARMS)
AUTONOMOUS_DIRECTIVE_MODES = frozenset({'outbound', 'rehandle', 'goal'})
CELL_PLAN_ORDER_SOURCES = frozenset({'harbor', 'rosy_r'})
CELL_PLAN_STATUSES = frozenset({
    'PROPOSED',
    'PARTIALLY_ADMITTED',
    'QUEUED',
    'RUNNING',
    'PAUSED_HUMAN',
    'COMPLETED',
    'CANCELLED',
    'ABORTED',
})

CellPlanMode = Literal['harbor_plan', 'outbound', 'rehandle', 'goal']
CellPlanOrderSource = Literal['harbor', 'rosy_r']
CellPlanStatus = Literal[
    'PROPOSED',
    'PARTIALLY_ADMITTED',
    'QUEUED',
    'RUNNING',
    'PAUSED_HUMAN',
    'COMPLETED',
    'CANCELLED',
    'ABORTED',
]


def _clean_text(value: Any, *, max_len: int = 192, label: str = 'value') -> str:
    if not isinstance(value, str):
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    text = value.strip()
    if not text or len(text) > max_len or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    return text


def _req_non_neg_int(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    return value


def _req_pos_int(value: Any, *, code: str = 'INVALID_SPATIAL_PLACEMENT') -> int:
    if type(value) is not int or value < 1:
        raise ValueError(code)
    return value


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
        row = _req_pos_int(entry.get('row'))
        col = _req_pos_int(entry.get('column'))
        level = _req_pos_int(entry.get('level'))
        state[cargo_id] = anchor_address(entry.get('area_id'), row, col, level)
    return state


def _arm_to_rosy(arm: str | None) -> Literal['arm_a', 'arm_b'] | None:
    if arm is None or arm == '':
        return None
    text = str(arm).strip()
    if text in ROSY_ARM_BY_HARBOR:
        return ROSY_ARM_BY_HARBOR[text]  # type: ignore[return-value]
    if text in ('arm_a', 'arm_b'):
        return text  # type: ignore[return-value]
    raise ValueError('INVALID_YARD_ARM')


@dataclass(frozen=True)
class CellPlanStepSpec:
    cargo_id: str
    src_site: str
    dst_site: str
    phase_label: str = '이동'
    arm_id: Literal['arm_a', 'arm_b'] | None = None

    def __post_init__(self):
        identifier(self.cargo_id)
        object.__setattr__(self, 'src_site', _clean_text(self.src_site, max_len=96, label='src_site'))
        object.__setattr__(self, 'dst_site', _clean_text(self.dst_site, max_len=96, label='dst_site'))
        object.__setattr__(self, 'phase_label', _clean_text(self.phase_label, max_len=48, label='phase_label'))
        if self.arm_id is not None and self.arm_id not in ('arm_a', 'arm_b'):
            raise ValueError('INVALID_YARD_ARM')

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            'cargo_id': self.cargo_id,
            'src_site': self.src_site,
            'dst_site': self.dst_site,
            'phase_label': self.phase_label,
        }
        if self.arm_id is not None:
            out['arm_id'] = self.arm_id
        return out


@dataclass(frozen=True)
class HarborPlanRequest:
    steps: tuple[CellPlanStepSpec, ...]
    robot_id: str = 'rosy_01'
    state: tuple[tuple[str, str], ...] | None = None
    propose: Literal[True] = True

    def __post_init__(self):
        if not self.steps:
            raise ValueError('PLAN_HAS_NO_EXECUTABLE_MOVES')
        identifier(self.robot_id)
        if self.propose is not True:
            raise ValueError('INVALID_CELL_PLAN_INPUT')

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'mode': 'harbor_plan',
            'steps': [s.to_wire() for s in self.steps],
            'robot_id': self.robot_id,
            'propose': True,
        }
        if self.state is not None:
            payload['state'] = dict(self.state)
        return payload


@dataclass(frozen=True)
class RosyDirectiveRequest:
    mode: Literal['outbound', 'rehandle', 'goal']
    state: tuple[tuple[str, str], ...]
    robot_id: str = 'rosy_01'
    cargo_id: str | None = None
    destination: str | None = None
    goal: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    avoid: tuple[str, ...] = ()
    propose: Literal[True] = True

    def __post_init__(self):
        if self.mode not in AUTONOMOUS_DIRECTIVE_MODES:
            raise ValueError('INVALID_DIRECTIVE_MODE')
        if not self.state:
            raise ValueError('NO_OBSERVED_CARGO_STATE')
        identifier(self.robot_id)
        if self.propose is not True:
            raise ValueError('INVALID_CELL_PLAN_INPUT')
        if self.mode in ('outbound', 'rehandle'):
            identifier(self.cargo_id)
            if self.mode == 'outbound' and self.destination is not None and self.destination != '':
                identifier(self.destination)
        else:
            if not self.goal:
                raise ValueError('GOAL_STACKS_REQUIRED')
            for stack_key, stack_cargos in self.goal:
                _clean_text(stack_key, max_len=96, label='goal_stack')
                if not stack_cargos:
                    raise ValueError('GOAL_STACKS_REQUIRED')
                for cid in stack_cargos:
                    identifier(cid)
        for site in self.avoid:
            _clean_text(site, max_len=96, label='avoid')

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'mode': self.mode,
            'state': dict(self.state),
            'robot_id': self.robot_id,
            'propose': True,
        }
        if self.mode in ('outbound', 'rehandle'):
            payload['cargo_id'] = self.cargo_id
            if self.mode == 'outbound' and self.destination:
                payload['destination'] = self.destination
        elif self.goal is not None:
            payload['goal'] = {k: list(v) for k, v in self.goal}
        if self.avoid:
            payload['avoid'] = list(self.avoid)
        return payload


@dataclass(frozen=True)
class CellPlanMoveView:
    src_site: str | None
    dst_site: str | None
    src_wire_ref: str | None = None
    dst_wire_ref: str | None = None
    arm_id: str | None = None
    phase_label: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class CellPlanTaskView:
    task_id: str
    status: CellPlanStatus
    depends_on: str | None
    dst_site: str | None
    dst_wire_ref: str | None
    arm_id: str | None = None
    phase_label: str | None = None
    last_refusal: str | None = None
    cancel_reason: str | None = None


@dataclass(frozen=True)
class CellPlanSummary:
    plan_id: str
    order_source: CellPlanOrderSource
    status: CellPlanStatus
    total_steps: int
    completed_steps: int = 0
    request_key: str | None = None
    hold_reason: str | None = None
    cell_phase: str | None = None
    moves: tuple[CellPlanMoveView, ...] = ()


@dataclass(frozen=True)
class CellPlanResponse:
    schema: Literal['rosy.cell-plan.v1']
    plan: CellPlanSummary
    tasks: tuple[CellPlanTaskView, ...]


@dataclass(frozen=True)
class CellPlanDryRunResponse:
    schema: Literal['rosy.cell-plan.v1']
    ok: Literal[True]
    mode: CellPlanMode
    order_source: CellPlanOrderSource
    moves: tuple[CellPlanMoveView, ...]
    final_state: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CellPlanCancelResponse:
    schema: Literal['rosy.cell-plan.v1']
    plan: CellPlanSummary
    tasks: tuple[CellPlanTaskView, ...]
    cancelled_task_ids: tuple[str, ...]


def build_harbor_plan_spec(
    plan_result: dict,
    *,
    observation_payload: dict | None = None,
    robot_id: str = 'rosy_01',
    cargo_id: str | None = None,
) -> HarborPlanRequest:
    """Translate a Harbor READY spatial Plan.result into a typed Mode 1 HarborPlanRequest."""
    if not isinstance(plan_result, dict) or plan_result.get('status') != 'READY':
        raise ValueError('PLAN_NOT_READY')
    if plan_result.get('model') != 'grid-placement-v1':
        raise ValueError('SPATIAL_PLAN_REQUIRED')
    identifier(robot_id)

    steps: list[CellPlanStepSpec] = []
    moves = plan_result.get('moves') or []
    if moves:
        for move in moves:
            if not isinstance(move, dict):
                raise ValueError('SPATIAL_MOVE_COORDINATES_REQUIRED')
            src_p = move.get('source_placement')
            dst_p = move.get('destination_placement')
            if not isinstance(src_p, dict) or not isinstance(dst_p, dict):
                raise ValueError('SPATIAL_MOVE_COORDINATES_REQUIRED')
            src_row = _req_pos_int(src_p.get('row'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
            src_col = _req_pos_int(src_p.get('column'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
            src_lvl = _req_pos_int(src_p.get('level'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
            dst_row = _req_pos_int(dst_p.get('row'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
            dst_col = _req_pos_int(dst_p.get('column'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
            dst_lvl = _req_pos_int(dst_p.get('level'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
            arm_id = _arm_to_rosy(move.get('primary_arm'))
            step_cargo = move.get('cargo_id')
            identifier(step_cargo)
            steps.append(CellPlanStepSpec(
                cargo_id=step_cargo,
                src_site=anchor_address(src_p.get('area_id'), src_row, src_col, src_lvl),
                dst_site=anchor_address(dst_p.get('area_id'), dst_row, dst_col, dst_lvl),
                arm_id=arm_id,
                phase_label=str(move.get('phase_label') or '이동'),
            ))
    elif isinstance(plan_result.get('recommendation'), dict):
        rec = plan_result['recommendation']
        step_cargo = cargo_id or rec.get('cargo_id')
        identifier(step_cargo)
        rec_row = _req_pos_int(rec.get('row'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
        rec_col = _req_pos_int(rec.get('column'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
        rec_lvl = _req_pos_int(rec.get('level'), code='SPATIAL_MOVE_COORDINATES_REQUIRED')
        steps.append(CellPlanStepSpec(
            cargo_id=step_cargo,
            src_site='belt',
            dst_site=anchor_address(rec.get('area_id'), rec_row, rec_col, rec_lvl),
            phase_label='입고',
        ))
    else:
        raise ValueError('PLAN_HAS_NO_EXECUTABLE_MOVES')

    state_pairs = tuple(observation_site_state(observation_payload).items()) if observation_payload is not None else None
    return HarborPlanRequest(steps=tuple(steps), robot_id=robot_id, state=state_pairs)


def build_harbor_plan_request(
    plan_result: dict,
    *,
    observation_payload: dict | None = None,
    robot_id: str = 'rosy_01',
    cargo_id: str | None = None,
) -> dict:
    """Translate a Harbor READY spatial Plan.result into a Mode 1 (harbor_plan) ROSY payload."""
    return build_harbor_plan_spec(
        plan_result,
        observation_payload=observation_payload,
        robot_id=robot_id,
        cargo_id=cargo_id,
    ).to_wire()


def build_rosy_directive_spec(
    *,
    mode: Literal['outbound', 'rehandle', 'goal'],
    observation_payload: dict | None,
    cargo_id: str | None = None,
    destination: str = 'stand',
    goal: dict[str, list[str]] | None = None,
    avoid: tuple[str, ...] | list[str] = (),
    robot_id: str = 'rosy_01',
) -> RosyDirectiveRequest:
    """Build a typed Mode 2 RosyDirectiveRequest."""
    if mode not in AUTONOMOUS_DIRECTIVE_MODES:
        raise ValueError('INVALID_DIRECTIVE_MODE')
    state = tuple(observation_site_state(observation_payload).items())
    goal_pairs: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    if mode == 'goal':
        if not isinstance(goal, dict) or not goal:
            raise ValueError('GOAL_STACKS_REQUIRED')
        parsed_goal: list[tuple[str, tuple[str, ...]]] = []
        for stack_key, cargo_list in goal.items():
            if not isinstance(cargo_list, (list, tuple)) or not cargo_list:
                raise ValueError('GOAL_STACKS_REQUIRED')
            parsed_goal.append((str(stack_key), tuple(cargo_list)))
        goal_pairs = tuple(parsed_goal)
    return RosyDirectiveRequest(
        mode=mode,
        state=state,
        robot_id=robot_id,
        cargo_id=cargo_id,
        destination=destination if mode == 'outbound' else None,
        goal=goal_pairs,
        avoid=tuple(str(s) for s in avoid),
    )


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
    return build_rosy_directive_spec(
        mode=mode,  # type: ignore[arg-type]
        observation_payload=observation_payload,
        cargo_id=cargo_id,
        destination=destination,
        goal=goal,
        avoid=avoid,
        robot_id=robot_id,
    ).to_wire()


def _parse_moves(raw_moves: Any) -> tuple[CellPlanMoveView, ...]:
    if raw_moves is None:
        return ()
    if not isinstance(raw_moves, list):
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    out: list[CellPlanMoveView] = []
    for m in raw_moves:
        if not isinstance(m, dict):
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        out.append(CellPlanMoveView(
            src_site=m.get('src_site') if isinstance(m.get('src_site'), str) else None,
            dst_site=m.get('dst_site') if isinstance(m.get('dst_site'), str) else None,
            src_wire_ref=m.get('src_wire_ref') if isinstance(m.get('src_wire_ref'), str) else None,
            dst_wire_ref=m.get('dst_wire_ref') if isinstance(m.get('dst_wire_ref'), str) else None,
            arm_id=m.get('arm_id') if isinstance(m.get('arm_id'), str) else None,
            phase_label=m.get('phase_label') if isinstance(m.get('phase_label'), str) else None,
            task_id=m.get('task_id') if isinstance(m.get('task_id'), str) else None,
        ))
    return tuple(out)


def _extract_last_refusal(task: dict[str, Any]) -> str | None:
    refusal = task.get('last_refusal')
    if isinstance(refusal, dict) and isinstance(refusal.get('code'), str):
        return refusal['code']
    if isinstance(refusal, str) and refusal.strip():
        return refusal.strip()
    return None


def _parse_summary_and_tasks(
    response: dict[str, Any],
    *,
    expected_order_source: str,
    expected_step_count: int | None = None,
    expected_plan_id: str | None = None,
    expected_request_key: str | None = None,
) -> tuple[CellPlanSummary, tuple[CellPlanTaskView, ...]]:
    plan = response.get('plan')
    tasks = response.get('tasks')
    if not isinstance(plan, dict) or not isinstance(tasks, list) or not tasks:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    plan_id = _clean_text(plan.get('plan_id'), max_len=96, label='plan_id')
    if expected_plan_id is not None and plan_id != expected_plan_id:
        raise ValueError('CELL_PLAN_ID_MISMATCH')
    req_key = plan.get('request_key')
    if req_key is not None and not isinstance(req_key, str):
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    if expected_request_key is not None and req_key is not None and req_key != expected_request_key:
        raise ValueError('CELL_PLAN_REQUEST_KEY_MISMATCH')
    order_source = plan.get('order_source')
    if order_source not in CELL_PLAN_ORDER_SOURCES or order_source != expected_order_source:
        raise ValueError('CELL_PLAN_ORDER_SOURCE_MISMATCH')
    status = plan.get('status')
    if not isinstance(status, str) or status not in CELL_PLAN_STATUSES:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    total_steps = _req_non_neg_int(plan.get('total_steps'))
    if total_steps != len(tasks):
        raise ValueError('CELL_PLAN_STEP_COUNT_MISMATCH')
    if expected_step_count is not None and len(tasks) != expected_step_count:
        raise ValueError('CELL_PLAN_STEP_COUNT_MISMATCH')
    completed_steps = plan.get('completed_steps', 0)
    if _req_non_neg_int(completed_steps) > total_steps:
        raise ValueError('CELL_PLAN_STEP_COUNT_MISMATCH')

    prev_id = None
    parsed_tasks: list[CellPlanTaskView] = []
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        task_id = _clean_text(task.get('task_id'), max_len=128, label='task_id')
        dep = task.get('depends_on')
        if idx == 0 and dep is not None:
            raise ValueError('CELL_PLAN_DEPENDENCY_BROKEN')
        if idx > 0 and dep != prev_id:
            raise ValueError('CELL_PLAN_DEPENDENCY_BROKEN')
        t_status = task.get('status')
        if not isinstance(t_status, str) or t_status not in CELL_PLAN_STATUSES:
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        if not task.get('dst_wire_ref') and not task.get('dst_site'):
            raise ValueError('INVALID_CELL_PLAN_RESPONSE')
        cancel_reason = task.get('cancel_reason') if isinstance(task.get('cancel_reason'), str) else None
        parsed_tasks.append(CellPlanTaskView(
            task_id=task_id,
            status=t_status,  # type: ignore[arg-type]
            depends_on=dep if isinstance(dep, str) else None,
            dst_site=task.get('dst_site') if isinstance(task.get('dst_site'), str) else None,
            dst_wire_ref=task.get('dst_wire_ref') if isinstance(task.get('dst_wire_ref'), str) else None,
            arm_id=task.get('arm_id') if isinstance(task.get('arm_id'), str) else None,
            phase_label=task.get('phase_label') if isinstance(task.get('phase_label'), str) else None,
            last_refusal=_extract_last_refusal(task),
            cancel_reason=cancel_reason,
        ))
        prev_id = task_id

    hold_reason = plan.get('hold_reason') if isinstance(plan.get('hold_reason'), str) else None
    cell_phase = plan.get('cell_phase') if isinstance(plan.get('cell_phase'), str) else None
    summary = CellPlanSummary(
        plan_id=plan_id,
        order_source=order_source,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        total_steps=total_steps,
        completed_steps=completed_steps,
        request_key=req_key,
        hold_reason=hold_reason,
        cell_phase=cell_phase,
        moves=_parse_moves(plan.get('moves')),
    )
    return summary, tuple(parsed_tasks)


def parse_cell_plan_response(
    response: dict[str, Any],
    *,
    expected_order_source: str,
    expected_step_count: int | None = None,
    expected_plan_id: str | None = None,
    expected_request_key: str | None = None,
) -> CellPlanResponse:
    """Strictly parse a rosy.cell-plan.v1 create/read response into immutable typed models."""
    if not isinstance(response, dict) or response.get('ok') is not True or response.get('schema') != CELL_PLAN_SCHEMA:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    summary, tasks = _parse_summary_and_tasks(
        response,
        expected_order_source=expected_order_source,
        expected_step_count=expected_step_count,
        expected_plan_id=expected_plan_id,
        expected_request_key=expected_request_key,
    )
    return CellPlanResponse(schema=CELL_PLAN_SCHEMA, plan=summary, tasks=tasks)


def validate_cell_plan_response(
    response: dict,
    *,
    expected_order_source: str,
    expected_step_count: int | None = None,
    expected_plan_id: str | None = None,
    expected_request_key: str | None = None,
) -> dict:
    """Validate rosy.cell-plan.v1 create/read response while preserving raw dict return compatibility."""
    parse_cell_plan_response(
        response,
        expected_order_source=expected_order_source,
        expected_step_count=expected_step_count,
        expected_plan_id=expected_plan_id,
        expected_request_key=expected_request_key,
    )
    return response


def parse_cell_plan_dry_run(
    response: dict[str, Any],
    *,
    expected_order_source: str | None = None,
) -> CellPlanDryRunResponse:
    """Strictly parse POST /api/fleet/pipeline/cell-plans/dry-run response."""
    if not isinstance(response, dict) or response.get('ok') is not True or response.get('schema') != CELL_PLAN_SCHEMA:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    mode = response.get('mode')
    if mode not in ('harbor_plan', 'outbound', 'rehandle', 'goal'):
        raise ValueError('INVALID_PLAN_MODE')
    order_source = response.get('order_source')
    if order_source not in CELL_PLAN_ORDER_SOURCES or (
        expected_order_source is not None and order_source != expected_order_source
    ):
        raise ValueError('CELL_PLAN_ORDER_SOURCE_MISMATCH')
    moves = _parse_moves(response.get('moves'))
    if not moves:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    final_state_raw = response.get('final_state') or {}
    if not isinstance(final_state_raw, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in final_state_raw.items()
    ):
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    return CellPlanDryRunResponse(
        schema=CELL_PLAN_SCHEMA,
        ok=True,
        mode=mode,  # type: ignore[arg-type]
        order_source=order_source,  # type: ignore[arg-type]
        moves=moves,
        final_state=tuple(sorted(final_state_raw.items())),
    )


def parse_cell_plan_cancel(
    response: dict[str, Any],
    *,
    expected_order_source: str | None = None,
    expected_plan_id: str | None = None,
) -> CellPlanCancelResponse:
    """Strictly parse POST /api/fleet/pipeline/cell-plans/{plan_id}/cancel response."""
    if not isinstance(response, dict) or response.get('ok') is not True or response.get('schema') != CELL_PLAN_SCHEMA:
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    plan_source = (response.get('plan') or {}).get('order_source') if isinstance(response.get('plan'), dict) else None
    source_to_check = expected_order_source if expected_order_source is not None else (plan_source or '')
    summary, tasks = _parse_summary_and_tasks(
        response,
        expected_order_source=source_to_check,
        expected_plan_id=expected_plan_id,
    )
    cancelled_raw = response.get('cancelled_task_ids')
    if not isinstance(cancelled_raw, list) or not all(isinstance(tid, str) and tid.strip() for tid in cancelled_raw):
        raise ValueError('INVALID_CELL_PLAN_RESPONSE')
    return CellPlanCancelResponse(
        schema=CELL_PLAN_SCHEMA,
        plan=summary,
        tasks=tasks,
        cancelled_task_ids=tuple(cancelled_raw),
    )

"""Harbor_web coordinator for dual-mode OMX cell plans (Harbor plan order/coords vs ROSY directive)."""
from __future__ import annotations

import os
import uuid
from urllib.error import HTTPError, URLError

from django.core.exceptions import ValidationError
from django.db import transaction

from harbor.cell_plan import (
    build_harbor_plan_request,
    build_rosy_directive_request,
    validate_cell_plan_response,
)
from harbor_web import services
from harbor_web.command_models import CellPlanDispatch
from harbor_web.fleet_connection import connection_settings, fleet_client
from harbor_web.models import Audit, Observation, Order, Plan

_STATE_LABELS = {
    'PROPOSED': '승인 대기',
    'PARTIALLY_ADMITTED': '일부 승인',
    'QUEUED': '대기 중',
    'RUNNING': '실행 중',
    'PAUSED_HUMAN': '사람 감지로 멈춤',
    'COMPLETED': '완료',
    'CANCELLED': '취소',
    'ABORTED': '중단',
}
_SOURCE_LABELS = {
    'harbor': 'Harbor 계획 순서·좌표',
    'rosy_r': 'ROSY 자율 계획',
}


def _default_client():
    conn = connection_settings()
    token = os.environ.get('HARBOR_FLEET_TOKEN', '')
    if not conn['origin'] or not token:
        raise ValidationError('EXECUTION_CONFIGURATION_REQUIRED')
    if conn['origin'] and not conn['allowed']:
        raise ValidationError('ORIGIN_NOT_ALLOWED')
    client = fleet_client(conn, token)
    if conn.get('principal'):
        client.expected_principal = conn['principal']
    return client


def _submit(client, payload: dict, *, request_key: str, expected_order_source: str, expected_step_count: int | None):
    try:
        raw = client.propose_cell_plan(payload, request_key=request_key)
    except HTTPError as error:
        error.close()
        raise ValidationError('CELL_PLAN_FLEET_REJECTED') from error
    except (URLError, OSError, ValueError) as error:
        raise ValidationError('CELL_PLAN_FLEET_UNREACHABLE') from error
    try:
        return validate_cell_plan_response(
            raw,
            expected_order_source=expected_order_source,
            expected_step_count=expected_step_count,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error


def dispatch_harbor_plan(actor, *, plan_id: int, robot_id: str = 'rosy_01', client=None) -> CellPlanDispatch:
    """Mode 1: send Harbor plan's explicit move sequence and slot coordinates to ROSY (order_source='harbor')."""
    services._role(actor, 'operator')
    plan = Plan.objects.select_related('order__cargo', 'observation').get(pk=plan_id)
    try:
        payload = build_harbor_plan_request(
            plan.result,
            observation_payload=plan.observation.payload,
            robot_id=robot_id,
            cargo_id=plan.order.cargo.code if plan.order.cargo_id else None,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    req_key = f'harbor-cell-plan:{plan.pk}:{uuid.uuid4().hex[:12]}'
    rosy_client = client if client is not None else _default_client()
    response = _submit(
        rosy_client,
        payload,
        request_key=req_key,
        expected_order_source='harbor',
        expected_step_count=len(payload['steps']),
    )
    rosy_plan = response['plan']
    tasks = response['tasks']
    with transaction.atomic():
        row = CellPlanDispatch.objects.create(
            order=plan.order,
            plan=plan,
            observation=plan.observation,
            dispatch_mode='HARBOR_PLAN',
            directive_mode='harbor_plan',
            request_key=req_key,
            rosy_plan_id=rosy_plan['plan_id'],
            order_source=rosy_plan['order_source'],
            state=rosy_plan['status'],
            planned_steps=payload['steps'],
            rosy_task_ids=[t['task_id'] for t in tasks],
            created_by=actor,
        )
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'mode': 'HARBOR_PLAN', 'order_source': row.order_source, 'rosy_plan_id': row.rosy_plan_id, 'steps': len(tasks)},
        )
    return row


def dispatch_rosy_directive(
    actor,
    *,
    order_id: int,
    observation_id: int,
    directive_mode: str = 'outbound',
    destination: str = 'stand',
    goal: dict[str, list[str]] | None = None,
    plan_id: int | None = None,
    robot_id: str = 'rosy_01',
    client=None,
) -> CellPlanDispatch:
    """Mode 2: send a high-level directive and let ROSY compute the move order and coords (order_source='rosy_r')."""
    services._role(actor, 'operator')
    order = Order.objects.select_related('cargo').get(pk=order_id)
    observation = Observation.objects.get(pk=observation_id)
    plan = Plan.objects.filter(pk=plan_id, order=order).first() if plan_id is not None else None
    cargo_code = order.cargo.code if order.cargo_id else None
    try:
        payload = build_rosy_directive_request(
            mode=directive_mode,
            observation_payload=observation.payload,
            cargo_id=cargo_code,
            destination=destination,
            goal=goal,
            robot_id=robot_id,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    req_key = f'harbor-rosy-directive:{order.pk}:{uuid.uuid4().hex[:12]}'
    rosy_client = client if client is not None else _default_client()
    response = _submit(
        rosy_client,
        payload,
        request_key=req_key,
        expected_order_source='rosy_r',
        expected_step_count=None,
    )
    rosy_plan = response['plan']
    tasks = response['tasks']
    steps = [
        {
            'src_wire_ref': m.get('src_wire_ref') or m.get('src_site'),
            'dst_wire_ref': m.get('dst_wire_ref') or m.get('dst_site'),
            'arm_id': m.get('arm_id'),
            'phase_label': m.get('phase_label'),
        }
        for m in rosy_plan.get('moves') or ()
    ]
    with transaction.atomic():
        row = CellPlanDispatch.objects.create(
            order=order,
            plan=plan,
            observation=observation,
            dispatch_mode='ROSY_DIRECTIVE',
            directive_mode=directive_mode,
            request_key=req_key,
            rosy_plan_id=rosy_plan['plan_id'],
            order_source=rosy_plan['order_source'],
            state=rosy_plan['status'],
            planned_steps=steps,
            rosy_task_ids=[t['task_id'] for t in tasks],
            created_by=actor,
        )
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'mode': 'ROSY_DIRECTIVE', 'directive_mode': directive_mode, 'order_source': row.order_source, 'rosy_plan_id': row.rosy_plan_id, 'steps': len(tasks)},
        )
    return row


def sync_cell_plan_dispatch(dispatch_id: int, *, client=None) -> CellPlanDispatch:
    row = CellPlanDispatch.objects.get(pk=dispatch_id)
    rosy_client = client if client is not None else _default_client()
    raw = rosy_client.cell_plan(row.rosy_plan_id)
    validated = validate_cell_plan_response(
        raw,
        expected_order_source=row.order_source,
        expected_step_count=len(row.rosy_task_ids) or None,
    )
    new_state = validated['plan']['status']
    if new_state != row.state:
        row.state = new_state
        row.save(update_fields=['state', 'updated_at'])
    return row


def dispatch_views_for_order(order: Order) -> list[dict]:
    rows = CellPlanDispatch.objects.filter(order=order).order_by('-created_at', '-pk')[:10]
    return [
        {
            'id': row.pk,
            'dispatch_mode': row.dispatch_mode,
            'directive_mode': row.directive_mode,
            'order_source': row.order_source,
            'source_label': _SOURCE_LABELS.get(row.order_source, row.order_source),
            'state': row.state,
            'state_label': _STATE_LABELS.get(row.state, row.state),
            'step_count': len(row.rosy_task_ids),
            'rosy_plan_id': row.rosy_plan_id,
            'planned_steps': row.planned_steps,
            'created_at': row.created_at,
        }
        for row in rows
    ]

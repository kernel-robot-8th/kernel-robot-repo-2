"""Harbor_web coordinator for dual-mode OMX cell plans (Harbor plan order/coords vs ROSY directive)."""
from __future__ import annotations

import json
import logging
import os
import uuid
from urllib.error import HTTPError, URLError

from django.core.exceptions import ObjectDoesNotExist, ValidationError
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

logger = logging.getLogger(__name__)

ACTIVE_CELL_PLAN_STATES = (
    'SUBMITTING',
    'UNCERTAIN',
    'PROPOSED',
    'PARTIALLY_ADMITTED',
    'QUEUED',
    'RUNNING',
    'PAUSED_HUMAN',
)

_STATE_LABELS = {
    'SUBMITTING': '전송 준비 중',
    'UNCERTAIN': '응답 확인 중',
    'REJECTED': '거절됨',
    'NOT_FOUND': '미접수',
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
    client.expected_role = 'service'
    return client


def _http_error_code(error: HTTPError) -> str:
    try:
        raw = error.read()
        data = json.loads(raw.decode('utf-8')) if raw else {}
        detail = data.get('detail') if isinstance(data, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get('code'), str):
            return detail['code']
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        pass
    finally:
        error.close()
    return ''


def _extract_moves(rosy_plan: dict) -> list[dict]:
    return [
        {
            'src_wire_ref': m.get('src_wire_ref') or m.get('src_site'),
            'dst_wire_ref': m.get('dst_wire_ref') or m.get('dst_site'),
            'arm_id': m.get('arm_id'),
            'phase_label': m.get('phase_label'),
        }
        for m in rosy_plan.get('moves') or ()
    ]


def _is_stand_dst(step: dict) -> bool:
    dst = str(step.get('dst_wire_ref') or step.get('dst_site') or '')
    return dst == 'stand' or dst.startswith(('stand-', 'stand_', 'stand:'))


def _targets_stand(row: CellPlanDispatch) -> bool:
    if row.order.kind != 'OUTBOUND':
        return False
    if row.planned_steps:
        return any(_is_stand_dst(step) for step in row.planned_steps)
    return row.directive_mode == 'outbound'


def _create_and_submit(
    actor,
    *,
    order: Order,
    plan: Plan | None,
    observation: Observation,
    dispatch_mode: str,
    directive_mode: str,
    order_source: str,
    request_key: str,
    payload: dict,
    initial_steps: list[dict],
    expected_step_count: int | None,
    rosy_client,
) -> CellPlanDispatch:
    """Rules 6 & 7: persist SUBMITTING intent before POST; mark uncertain responses without re-POSTing."""
    with transaction.atomic():
        row = CellPlanDispatch.objects.create(
            order=order,
            plan=plan,
            observation=observation,
            dispatch_mode=dispatch_mode,
            directive_mode=directive_mode,
            request_key=request_key,
            request_payload=payload,
            rosy_plan_id='',
            order_source=order_source,
            state='SUBMITTING',
            planned_steps=initial_steps,
            rosy_task_ids=[],
            created_by=actor,
        )
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch_prepared',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'mode': dispatch_mode, 'directive_mode': directive_mode, 'request_key': request_key},
        )

    try:
        raw = rosy_client.propose_cell_plan(payload, request_key=request_key)
    except HTTPError as error:
        status_code = error.code
        code = _http_error_code(error)
        if 400 <= status_code < 500 and status_code not in (408, 429):
            reason = 'FLEET_IDENTITY_REJECTED' if status_code in (401, 403) else (code or f'HTTP_{status_code}')
            row.state = 'REJECTED'
            row.reason = reason[:192]
            row.save(update_fields=['state', 'reason', 'updated_at'])
            Audit.objects.create(
                actor=actor,
                action='cell_plan.dispatch_rejected',
                object_type='CellPlanDispatch',
                object_id=str(row.pk),
                detail={'status': status_code, 'reason': row.reason},
            )
            raise ValidationError('CELL_PLAN_FLEET_REJECTED') from error
        row.state = 'UNCERTAIN'
        row.reason = (code or f'UNCERTAIN_HTTP_{status_code}')[:192]
        row.save(update_fields=['state', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch_uncertain',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'status': status_code, 'reason': row.reason},
        )
        return row
    except (URLError, OSError, ValueError) as error:
        row.state = 'UNCERTAIN'
        row.reason = f'UNCERTAIN_{type(error).__name__}'[:192]
        row.save(update_fields=['state', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch_uncertain',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'reason': row.reason},
        )
        return row

    try:
        response = validate_cell_plan_response(
            raw,
            expected_order_source=order_source,
            expected_step_count=expected_step_count,
        )
    except ValueError as error:
        row.state = 'UNCERTAIN'
        row.reason = f'UNCERTAIN_{error}'[:192]
        row.save(update_fields=['state', 'reason', 'updated_at'])
        return row

    rosy_plan = response['plan']
    tasks = response['tasks']
    with transaction.atomic():
        row.rosy_plan_id = rosy_plan['plan_id']
        row.state = rosy_plan['status']
        if not row.planned_steps:
            row.planned_steps = _extract_moves(rosy_plan)
        row.rosy_task_ids = [t['task_id'] for t in tasks]
        row.reason = ''
        row.save(update_fields=['rosy_plan_id', 'state', 'planned_steps', 'rosy_task_ids', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'mode': dispatch_mode, 'order_source': row.order_source, 'rosy_plan_id': row.rosy_plan_id, 'steps': len(tasks)},
        )
    return row


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
    return _create_and_submit(
        actor,
        order=plan.order,
        plan=plan,
        observation=plan.observation,
        dispatch_mode='HARBOR_PLAN',
        directive_mode='harbor_plan',
        order_source='harbor',
        request_key=req_key,
        payload=payload,
        initial_steps=payload['steps'],
        expected_step_count=len(payload['steps']),
        rosy_client=rosy_client,
    )


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
    return _create_and_submit(
        actor,
        order=order,
        plan=plan,
        observation=observation,
        dispatch_mode='ROSY_DIRECTIVE',
        directive_mode=directive_mode,
        order_source='rosy_r',
        request_key=req_key,
        payload=payload,
        initial_steps=[],
        expected_step_count=None,
        rosy_client=rosy_client,
    )


def reconcile_cell_plan_dispatch(dispatch_id: int, *, client=None) -> CellPlanDispatch:
    """Rule 7 & 8: reconcile SUBMITTING/UNCERTAIN requests solely via GET ?request_key=."""
    row = CellPlanDispatch.objects.select_related('order').get(pk=dispatch_id)
    if row.state not in ('SUBMITTING', 'UNCERTAIN'):
        return row
    rosy_client = client if client is not None else _default_client()
    try:
        raw = rosy_client.cell_plan_request(row.request_key)
    except HTTPError as error:
        status_code = error.code
        code = _http_error_code(error)
        if status_code == 404:
            if code == 'UNKNOWN_CELL_PLAN':
                row.state = 'NOT_FOUND'
                row.reason = 'UNKNOWN_CELL_PLAN'
            else:
                row.reason = 'CELL_PLAN_ROUTE_UNAVAILABLE'
            row.save(update_fields=['state', 'reason', 'updated_at'])
        elif status_code in (401, 403):
            row.reason = 'FLEET_IDENTITY_REJECTED'
            row.save(update_fields=['reason', 'updated_at'])
        return row
    except (URLError, OSError, ValueError):
        return row

    expected_steps = len(row.planned_steps) if row.dispatch_mode == 'HARBOR_PLAN' else None
    validated = validate_cell_plan_response(
        raw,
        expected_order_source=row.order_source,
        expected_step_count=expected_steps,
    )
    rosy_plan = validated['plan']
    tasks = validated['tasks']
    with transaction.atomic():
        row.rosy_plan_id = rosy_plan['plan_id']
        row.state = rosy_plan['status']
        if not row.planned_steps:
            row.planned_steps = _extract_moves(rosy_plan)
        row.rosy_task_ids = [t['task_id'] for t in tasks]
        row.reason = ''
        row.save(update_fields=['rosy_plan_id', 'state', 'planned_steps', 'rosy_task_ids', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=row.created_by,
            action='cell_plan.reconciled',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'request_key': row.request_key, 'rosy_plan_id': row.rosy_plan_id, 'state': row.state},
        )
    return row


def sync_cell_plan_dispatch(dispatch_id: int, *, client=None) -> CellPlanDispatch:
    row = CellPlanDispatch.objects.select_related('order').get(pk=dispatch_id)
    if row.state in ('SUBMITTING', 'UNCERTAIN'):
        return reconcile_cell_plan_dispatch(row.pk, client=client)
    if not row.rosy_plan_id:
        return row
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


def tick(registry, *, now=None) -> dict[int, str]:
    """Bounded executor pass: reconcile uncertain dispatches, sync status, and auto-trigger Pinky S_OUT when completed."""
    del now
    client = registry.client
    results: dict[int, str] = {}
    active = list(
        CellPlanDispatch.objects.select_related('order')
        .filter(state__in=ACTIVE_CELL_PLAN_STATES)
        .order_by('pk')[:8]
    )
    for row in active:
        try:
            updated = sync_cell_plan_dispatch(row.pk, client=client)
            results[row.pk] = updated.state
            if updated.state == 'COMPLETED' and _targets_stand(updated):
                from harbor_web import outbound_loop
                outbound_loop.maybe_auto_prepare_from_cell_plan(updated, registry)
        except Exception as exc:
            logger.exception('CellPlanDispatch #%s sync failed; will retry on next tick', row.pk)
            results[row.pk] = f'ERROR: {type(exc).__name__}'
    return results


def dispatch_views_for_order(order: Order) -> list[dict]:
    from harbor_web import outbound_loop

    rows = (
        CellPlanDispatch.objects.select_related('order', 'outbound_dispatch')
        .filter(order=order)
        .order_by('-created_at', '-pk')[:10]
    )
    views = []
    for row in rows:
        try:
            od = row.outbound_dispatch
        except ObjectDoesNotExist:
            od = None
        outbound_view = None
        if od is not None:
            outbound_view = {
                'id': od.pk,
                'version': od.version,
                'state': od.state,
                'status_label': outbound_loop._STATE_LABELS.get(od.state, od.state),
                'pickup_stop_id': od.pickup_stop_id,
                'exit_stop_id': od.exit_stop_id,
                'vehicle_label': od.assigned_vehicle_id or '미배정',
                'reason': od.reason,
                'can_confirm_load': od.state == 'PICKUP_ARRIVED',
            }
        views.append({
            'id': row.pk,
            'dispatch_mode': row.dispatch_mode,
            'directive_mode': row.directive_mode,
            'order_source': row.order_source,
            'source_label': _SOURCE_LABELS.get(row.order_source, row.order_source),
            'state': row.state,
            'state_label': _STATE_LABELS.get(row.state, row.state),
            'reason': row.reason,
            'step_count': len(row.rosy_task_ids),
            'rosy_plan_id': row.rosy_plan_id,
            'planned_steps': row.planned_steps,
            'created_at': row.created_at,
            'can_sync': row.state in ACTIVE_CELL_PLAN_STATES,
            'can_start_outbound': row.state == 'COMPLETED' and _targets_stand(row) and od is None,
            'outbound': outbound_view,
        })
    return views

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
    CellPlanDryRunResponse,
    build_harbor_plan_request,
    build_rosy_directive_request,
    parse_cell_plan_cancel,
    parse_cell_plan_dry_run,
    parse_cell_plan_response,
    validate_cell_plan_response,
)
from harbor_web import services
from harbor_web.command_models import (ACTIVE_CELL_PLAN_DISPATCH_STATES, ACTIVE_COMMAND_STATES, CellPlanDispatch,
                                       Command)
from harbor_web.coordination import serialized
from harbor_web.fleet_connection import connection_settings, fleet_client
from harbor_web.models import Audit, Observation, Order, Plan

logger = logging.getLogger(__name__)

ACTIVE_CELL_PLAN_STATES = ACTIVE_CELL_PLAN_DISPATCH_STATES
CANCELLABLE_CELL_PLAN_STATES = ('PROPOSED', 'PARTIALLY_ADMITTED', 'QUEUED', 'RUNNING', 'PAUSED_HUMAN')


def _default_robot_id() -> str:
    return os.environ.get('HARBOR_CELL_PLAN_ROBOT_ID', '').strip() or 'rosy_01'


def _default_outbound_stand() -> str:
    return os.environ.get('HARBOR_OUTBOUND_STAND', '').strip() or 'stand'


def _record_completed_audit(row: CellPlanDispatch, actor=None) -> None:
    Audit.objects.create(
        actor=actor or row.created_by,
        action='cell_plan.completed',
        object_type='CellPlanDispatch',
        object_id=str(row.pk),
        detail={'order_id': row.order_id, 'rosy_plan_id': row.rosy_plan_id},
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


class FleetOnlyRegistry:
    """The executor registry without the legacy ledger or bindings: cell plans need only the Fleet client."""

    def __init__(self, client=None):
        self.client = client if client is not None else _default_client()


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


def _failure_reason(parsed) -> str:
    if parsed.plan.hold_reason:
        return parsed.plan.hold_reason[:192]
    for task in parsed.tasks:
        if task.cancel_reason:
            return task.cancel_reason[:192]
        if task.last_refusal:
            return task.last_refusal[:192]
    return ''


def _reject_blocked_order(order: Order, *, production: bool, observation: Observation) -> None:
    """P0 mutex: one live execution path per order; restored or unready deployments stay shut."""
    from harbor_web.runtime import execution_block_reason
    blocked = execution_block_reason()
    if blocked:
        raise services.Conflict(blocked)
    if Command.objects.filter(order=order, state__in=ACTIVE_COMMAND_STATES).exists():
        raise services.Conflict('EXISTING_ORDER_EXECUTION_REQUIRES_RECONCILIATION')
    if CellPlanDispatch.objects.filter(order=order, state__in=ACTIVE_CELL_PLAN_DISPATCH_STATES).exists():
        raise services.Conflict('ACTIVE_CELL_PLAN_DISPATCH')
    if production:  # A fake client is an explicit test/simulation bypass.
        from harbor_web.readiness import first_unmet_cell_plan
        unmet = first_unmet_cell_plan(observation)
        if unmet:
            raise services.Conflict(unmet)


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
        parsed = parse_cell_plan_response(
            raw,
            expected_order_source=order_source,
            expected_step_count=expected_step_count,
            expected_request_key=request_key,
        )
    except ValueError as error:
        row.state = 'UNCERTAIN'
        row.reason = f'UNCERTAIN_{error}'[:192]
        row.save(update_fields=['state', 'reason', 'updated_at'])
        return row

    rosy_plan = parsed.plan
    with transaction.atomic():
        row.rosy_plan_id = rosy_plan.plan_id
        row.state = rosy_plan.status
        if not row.planned_steps:
            row.planned_steps = _extract_moves(raw['plan'])
        row.rosy_task_ids = [t.task_id for t in parsed.tasks]
        row.reason = _failure_reason(parsed) if rosy_plan.status in ('PAUSED_HUMAN', 'ABORTED', 'CANCELLED') else ''
        row.save(update_fields=['rosy_plan_id', 'state', 'planned_steps', 'rosy_task_ids', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=actor,
            action='cell_plan.dispatch',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'mode': dispatch_mode, 'order_source': row.order_source, 'rosy_plan_id': row.rosy_plan_id, 'steps': len(parsed.tasks)},
        )
        if row.state == 'COMPLETED':
            _record_completed_audit(row, actor=actor)
    return row


def _harbor_plan_payload(plan, robot_id, *, check_reservations=False):
    result = plan.result
    if result.get('plan_kind') == 'inbound-placement':
        from harbor_web.plan_service import placement_decision

        decision = placement_decision(plan)
        if decision is not None:
            result = {**result, 'recommendation': decision.detail['recommendation']}
        if check_reservations:
            from harbor_web.autoplanning import placement_cells, placement_reservations, unreconciled_cell_dispatches

            if unreconciled_cell_dispatches(plan.observation).filter(order=plan.order).exists():
                raise services.Conflict('EXISTING_ORDER_EXECUTION_REQUIRES_RECONCILIATION')
            reserved, _ = placement_reservations(plan.order, plan.observation)
            if reserved and placement_cells(result['recommendation'], result['incoming_shape']) & reserved:
                raise services.Conflict('INBOUND_DESTINATION_RESERVED')
    try:
        return build_harbor_plan_request(
            result,
            observation_payload=plan.observation.payload,
            robot_id=robot_id or _default_robot_id(),
            cargo_id=plan.order.cargo.code if plan.order.cargo_id else None,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error


def preview_harbor_plan(actor, *, plan_id: int, robot_id: str | None = None, client=None) -> CellPlanDryRunResponse:
    """Read-only simulation preview for Mode 1 Harbor plan without persisting ROSY tasks."""
    services._role(actor, 'operator')
    plan = Plan.objects.select_related('order__cargo', 'observation').get(pk=plan_id)
    payload = _harbor_plan_payload(plan, robot_id)
    rosy_client = client if client is not None else _default_client()
    try:
        raw = rosy_client.preview_cell_plan(payload)
        return parse_cell_plan_dry_run(raw, expected_order_source='harbor')
    except HTTPError as error:
        code = _http_error_code(error)
        raise ValidationError(code or 'CELL_PLAN_DRY_RUN_REJECTED') from error
    except (URLError, OSError, ValueError) as error:
        raise ValidationError(str(error) or 'CELL_PLAN_DRY_RUN_FAILED') from error


def preview_rosy_directive(
    actor,
    *,
    order_id: int,
    observation_id: int,
    directive_mode: str = 'outbound',
    destination: str | None = None,
    goal: dict[str, list[str]] | None = None,
    robot_id: str | None = None,
    client=None,
) -> CellPlanDryRunResponse:
    """Read-only simulation preview for Mode 2 autonomous ROSY directive."""
    services._role(actor, 'operator')
    order = Order.objects.select_related('cargo').get(pk=order_id)
    observation = Observation.objects.get(pk=observation_id)
    try:
        payload = build_rosy_directive_request(
            mode=directive_mode,
            observation_payload=observation.payload,
            cargo_id=order.cargo.code if order.cargo_id else None,
            destination=destination or _default_outbound_stand(),
            goal=goal,
            robot_id=robot_id or _default_robot_id(),
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    rosy_client = client if client is not None else _default_client()
    try:
        raw = rosy_client.preview_cell_plan(payload)
        return parse_cell_plan_dry_run(raw, expected_order_source='rosy_r')
    except HTTPError as error:
        code = _http_error_code(error)
        raise ValidationError(code or 'CELL_PLAN_DRY_RUN_REJECTED') from error
    except (URLError, OSError, ValueError) as error:
        raise ValidationError(str(error) or 'CELL_PLAN_DRY_RUN_FAILED') from error


@serialized
def dispatch_harbor_plan(actor, *, plan_id: int, robot_id: str | None = None, client=None) -> CellPlanDispatch:
    """Mode 1: send Harbor plan's explicit move sequence and slot coordinates to ROSY (order_source='harbor')."""
    services._role(actor, 'operator')
    return _submit_harbor_plan(actor, plan_id=plan_id, robot_id=robot_id, client=client, production=client is None)


def _submit_harbor_plan(actor, *, plan_id: int, robot_id: str | None, client, production: bool) -> CellPlanDispatch:
    plan = Plan.objects.select_related('order__cargo', 'observation').get(pk=plan_id)
    _reject_blocked_order(plan.order, production=production, observation=plan.observation)
    if plan.result.get('plan_kind') == 'inbound-placement':
        from harbor_web.inbound_decision import decision_block

        if Plan.objects.filter(order_id=plan.order_id, pk__gt=plan.pk).exists():
            raise services.Conflict('INBOUND_PLAN_SUPERSEDED')
        blocked = decision_block(plan.order, plan)
        if blocked:
            raise services.Conflict(blocked)
    payload = _harbor_plan_payload(plan, robot_id, check_reservations=True)
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


@serialized
def dispatch_rosy_directive(
    actor,
    *,
    order_id: int,
    observation_id: int,
    directive_mode: str = 'outbound',
    destination: str | None = None,
    goal: dict[str, list[str]] | None = None,
    plan_id: int | None = None,
    robot_id: str | None = None,
    client=None,
) -> CellPlanDispatch:
    """Mode 2: send a high-level directive and let ROSY compute the move order and coords (order_source='rosy_r')."""
    services._role(actor, 'operator')
    order = Order.objects.select_related('cargo').get(pk=order_id)
    observation = Observation.objects.get(pk=observation_id)
    plan = Plan.objects.filter(pk=plan_id, order=order).first() if plan_id is not None else None
    _reject_blocked_order(order, production=client is None, observation=observation)
    cargo_code = order.cargo.code if order.cargo_id else None
    try:
        payload = build_rosy_directive_request(
            mode=directive_mode,
            observation_payload=observation.payload,
            cargo_id=cargo_code,
            destination=destination or _default_outbound_stand(),
            goal=goal,
            robot_id=robot_id or _default_robot_id(),
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
    parsed = parse_cell_plan_response(
        raw,
        expected_order_source=row.order_source,
        expected_step_count=expected_steps,
        expected_request_key=row.request_key,
    )
    rosy_plan = parsed.plan
    with transaction.atomic():
        prev_state = row.state
        row.rosy_plan_id = rosy_plan.plan_id
        row.state = rosy_plan.status
        if not row.planned_steps:
            row.planned_steps = _extract_moves(raw['plan'])
        row.rosy_task_ids = [t.task_id for t in parsed.tasks]
        row.reason = _failure_reason(parsed) if rosy_plan.status in ('PAUSED_HUMAN', 'ABORTED', 'CANCELLED') else ''
        row.save(update_fields=['rosy_plan_id', 'state', 'planned_steps', 'rosy_task_ids', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=row.created_by,
            action='cell_plan.reconciled',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'request_key': row.request_key, 'rosy_plan_id': row.rosy_plan_id, 'state': row.state},
        )
        if prev_state != 'COMPLETED' and row.state == 'COMPLETED':
            _record_completed_audit(row)
    return row


def sync_cell_plan_dispatch(dispatch_id: int, *, client=None) -> CellPlanDispatch:
    row = CellPlanDispatch.objects.select_related('order').get(pk=dispatch_id)
    if row.state in ('SUBMITTING', 'UNCERTAIN'):
        return reconcile_cell_plan_dispatch(row.pk, client=client)
    if not row.rosy_plan_id:
        return row
    rosy_client = client if client is not None else _default_client()
    raw = rosy_client.cell_plan(row.rosy_plan_id)
    parsed = parse_cell_plan_response(
        raw,
        expected_order_source=row.order_source,
        expected_step_count=len(row.rosy_task_ids) or None,
        expected_plan_id=row.rosy_plan_id,
    )
    new_state = parsed.plan.status
    new_reason = '' if new_state == 'COMPLETED' else _failure_reason(parsed)
    update_fields: list[str] = []
    prev_state = row.state
    if new_state != row.state:
        row.state = new_state
        update_fields.append('state')
    if new_reason != row.reason and (new_reason or new_state == 'COMPLETED'):
        row.reason = new_reason
        update_fields.append('reason')
    if update_fields:
        with transaction.atomic():
            row.save(update_fields=[*update_fields, 'updated_at'])
            if prev_state != 'COMPLETED' and row.state == 'COMPLETED':
                _record_completed_audit(row)
    return row


def cancel_cell_plan_dispatch(actor, *, dispatch_id: int, reason: str, client=None) -> CellPlanDispatch:
    """Cancel pending PROPOSED/QUEUED/RUNNING/PAUSED_HUMAN steps via POST /cell-plans/{plan_id}/cancel."""
    services._role(actor, 'operator')
    row = CellPlanDispatch.objects.select_related('order').get(pk=dispatch_id)
    if not row.rosy_plan_id or row.state not in CANCELLABLE_CELL_PLAN_STATES:
        raise services.Conflict('Cell plan dispatch cannot be cancelled in its current state.')
    rosy_client = client if client is not None else _default_client()
    try:
        raw = rosy_client.cancel_cell_plan(row.rosy_plan_id, reason=reason)
        parsed = parse_cell_plan_cancel(
            raw,
            expected_order_source=row.order_source,
            expected_plan_id=row.rosy_plan_id,
        )
    except HTTPError as error:
        code = _http_error_code(error)
        if error.code == 409:
            raise services.Conflict(code or 'CELL_PLAN_NOT_CANCELLABLE') from error
        raise ValidationError(code or 'CELL_PLAN_CANCEL_FAILED') from error
    except (URLError, OSError, ValueError) as error:
        raise ValidationError(str(error) or 'CELL_PLAN_CANCEL_FAILED') from error

    with transaction.atomic():
        row.state = parsed.plan.status
        row.reason = (_failure_reason(parsed) or reason)[:192]
        row.save(update_fields=['state', 'reason', 'updated_at'])
        Audit.objects.create(
            actor=actor,
            action='cell_plan.cancelled',
            object_type='CellPlanDispatch',
            object_id=str(row.pk),
            detail={'rosy_plan_id': row.rosy_plan_id, 'cancelled_task_ids': list(parsed.cancelled_task_ids), 'reason': row.reason},
        )
    return row


AUTO_DISPATCH_USERNAME = 'harbor-automatic'
_auto_hold_reason: dict[int, str] = {}


def _automatic_actor():
    """A system user that never logs in, so automatic proposals stay apart from named people (ADR 0034 point 6)."""
    from django.contrib.auth import get_user_model
    user, created = get_user_model().objects.get_or_create(username=AUTO_DISPATCH_USERNAME, defaults={'is_active': False})
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user


@serialized
def auto_dispatch_next(*, client=None, production: bool = True) -> CellPlanDispatch | None:
    """ADR 0034 automatic operation: propose the next current READY plan to ROSY by site points (cell-plans).

    One cell job at a time; a plan that was already sent is never resent automatically (ADR 0034 point 3).
    ROSY still approves and admits every step. Returns the new dispatch or None.
    """
    from harbor_web.order_queue import completed_order_ids
    if (Command.objects.filter(state__in=ACTIVE_COMMAND_STATES).exists()
            or CellPlanDispatch.objects.filter(state__in=ACTIVE_CELL_PLAN_DISPATCH_STATES).exists()):
        return None
    sent = set(CellPlanDispatch.objects.exclude(plan__isnull=True).values_list('plan_id', flat=True))
    orders = (Order.objects.filter(kind__in=('INBOUND', 'OUTBOUND')).exclude(pk__in=completed_order_ids())
              .select_related('cargo').order_by('priority_group', 'sequence', 'pk'))
    for order in orders:
        plan = Plan.objects.filter(order=order).order_by('-pk').first()
        if (plan is None or plan.pk in sent or plan.result.get('status') != 'READY'
                or plan.order_version != order.version):
            continue
        try:
            row = _submit_harbor_plan(_automatic_actor(), plan_id=plan.pk, robot_id=None, client=client, production=production)
        except (services.Conflict, ValidationError) as error:
            reason = str(getattr(error, 'message', '') or error)
            if _auto_hold_reason.get(plan.pk) != reason:  # record each reason once, not every tick
                _auto_hold_reason[plan.pk] = reason
                Audit.objects.create(actor=None, action='cell_plan.auto_hold', object_type='Plan', object_id=str(plan.pk),
                                     detail=dict(reason=reason, automatic=True))
            return None
        _auto_hold_reason.pop(plan.pk, None)
        Audit.objects.create(actor=None, action='cell_plan.auto_proposed', object_type='CellPlanDispatch',
                             object_id=str(row.pk), detail=dict(plan_id=plan.pk, automatic=True))
        return row
    return None


@serialized
def propose_automatically(plan: Plan, *, client=None) -> CellPlanDispatch:
    """Send one Harbor-made plan (e.g. an authorized rearrangement) as the automatic user, behind every cell-plan gate."""
    return _submit_harbor_plan(_automatic_actor(), plan_id=plan.pk, robot_id=None, client=client, production=client is None)


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
    if not active:
        # The executor only ticks in automatic operation (HARBOR_EXECUTOR_ENABLED=1); a fake test client skips site readiness.
        try:
            proposed = auto_dispatch_next(client=None if production_client(registry) else client,
                                          production=production_client(registry))
        except Exception as exc:
            logger.exception('Automatic cell-plan proposal failed; will retry on next tick')
            results['auto'] = f'ERROR: {type(exc).__name__}'
        else:
            if proposed is not None:
                results[proposed.pk] = proposed.state
    return results


def production_client(registry) -> bool:
    """Real deployments build their own ROSY client; tests hand the executor a fake one."""
    return bool(getattr(registry, 'production', True))


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
                'can_cancel': od.state not in ('EXITED', 'CANCELLED'),
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
            'can_cancel': row.state in CANCELLABLE_CELL_PLAN_STATES and bool(row.rosy_plan_id),
            'can_start_outbound': row.state == 'COMPLETED' and _targets_stand(row) and od is None,
            'outbound': outbound_view,
        })
    return views

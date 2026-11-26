"""Bind Fleet receipts and independently observed placement results to their Harbor requests."""
import logging
import re

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from harbor.process import anchor_address

from harbor_web.autoplanning import replan_enabled
from harbor_web.command_models import ACTIVE_CELL_PLAN_DISPATCH_STATES, ACTIVE_COMMAND_STATES, CellPlanDispatch
from harbor_web.coordination import serialized
from harbor_web.models import Audit, Cargo, IntakeOccurrence, IntakeProjectionCursor, Observation, Order, Plan
from harbor_web.plan_freshness import plan_changes
from harbor_web.plan_service import DECIDE_ACTION, placement_decision
from harbor_web.service_common import automatic_actor
from harbor_web.site_config import covers_spatial_yard
from harbor_web.order_queue import back_of_tier
from harbor_web.port_event_projection import _ProjectionRejected, _record_rejection_once, project_stored_event

logger = logging.getLogger(__name__)
WAITING = ('CARGO_UNKNOWN', 'AMBIGUOUS_ORDER', 'WAITING_FOR_YARD', 'PLANNING_CAPACITY')
DISPATCH_LABELS = {
    'SUBMITTING': '제출 중', 'PROPOSED': '제안 접수 · 승인 대기', 'PARTIALLY_ADMITTED': '일부 단계 승인',
    'QUEUED': '실행 대기', 'RUNNING': '실행 중', 'PAUSED_HUMAN': '작업자 확인 대기',
    'COMPLETED': '작업 완료', 'ABORTED': '작업 중단', 'CANCELLED': '취소', 'REJECTED': '접수 거절',
    'UNCERTAIN': '접수 여부 대조 중', 'NOT_FOUND': '접수 기록 없음',
}


def requested_slot(dispatch):
    if dispatch:
        for step in dispatch.request_payload.get('steps', []):
            if step.get('src_site') == 'belt' and step.get('cargo_id') == dispatch.order.cargo.code:
                return step.get('dst_site')
    return None


def inventory_occurrences():
    dispatches = CellPlanDispatch.objects.select_related('order__cargo').order_by('-pk')
    receipts = IntakeOccurrence.objects.select_related('order__cargo__commercial_profile').prefetch_related(
        Prefetch('order__cell_plan_dispatches', queryset=dispatches[:1], to_attr='sent_placements'),
        Prefetch('order__plan_set', queryset=Plan.objects.order_by('-pk')[:1], to_attr='placement_plans'))
    receipts = list(receipts.order_by('-received_at')[:50])
    unbound_codes = {receipt.object_ref for receipt in receipts if not receipt.order_id}
    unbound_cargo = Cargo.objects.select_related('commercial_profile').in_bulk(unbound_codes, field_name='code')
    plan_ids = [str(order.placement_plans[0].pk) for receipt in receipts if (order := receipt.order) and order.placement_plans]
    facts = {(row.object_id, row.action): row.detail for row in Audit.objects.filter(object_type='Plan',
        object_id__in=plan_ids, action__in=(DECIDE_ACTION, 'cell_plan.auto_hold')).order_by('pk')}
    rows = []
    for receipt in receipts:
        order = receipt.order
        plan = order.placement_plans[0] if order and order.placement_plans else None
        dispatch = order.sent_placements[0] if order and order.sent_placements else None
        sent = requested_slot(dispatch)
        target, reason = None, ''
        if plan:
            decision = facts.get((str(plan.pk), DECIDE_ACTION), {})
            rec = decision.get('recommendation') or plan.result.get('recommendation')
            if rec:
                target = anchor_address(rec['area_id'], rec['row'], rec['column'], rec['level'])
            reason = facts.get((str(plan.pk), 'cell_plan.auto_hold'), {}).get('reason', '')
            if not reason and plan.result.get('status') == 'HOLD':
                reason = plan.result.get('reason', '')
        local_reason = reason if not dispatch or dispatch.plan_id != getattr(plan, 'pk', None) else ''
        local_code = local_reason if isinstance(local_reason, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,95}', local_reason) else ''
        # A worker's diagnostic body can contain device data. Only stable codes appear here.
        code = dispatch.reason if dispatch and re.fullmatch(r'[A-Z][A-Z0-9_]{1,95}', dispatch.reason or '') else ''
        action = ''
        if code in ('SITE_NOT_CALIBRATED', 'STAND_NOT_CALIBRATED', 'UNCALIBRATED_DISPLACED_SITE'):
            action = '현장 보정 필요'
        elif code in ('SITE_ARM_NOT_ALLOWED', 'STAND_REQUIRES_ARM_A', 'INVALID_CELL_SLOT_COORDINATE', 'NO_LEGAL_PLAN'):
            action = '현재 장치 구성에서 실행 불가'
        cargo = order.cargo if order else unbound_cargo.get(receipt.object_ref)
        rows.append(dict(receipt=receipt, cargo=cargo, commercial=cargo.current_commercial if cargo else None,
                         target=target, sent=sent,
                         state=DISPATCH_LABELS.get(dispatch.state, dispatch.state) if dispatch else '미제출',
                         reason=code, action=action, local_reason=local_code))
    return rows


@serialized
@transaction.atomic
def _receive(event):
    if event['observed_at'] > timezone.now():
        return
    receipt, created = IntakeOccurrence.objects.get_or_create(pk=event['intake_occurrence_id'], defaults={
        'object_ref': event['object_ref'], 'receipt_event_id': event['event_id'], 'received_at': event['observed_at']})
    if receipt.receipt_event_id != event['event_id']:
        _record_rejection_once(event, 'OCCURRENCE_RECEIPT_CONFLICT', '회차에 다른 수령 기록이 있습니다.')
        return
    if not created and receipt.status not in WAITING:
        return
    cargo = Cargo.objects.filter(code=receipt.object_ref).first()
    if cargo is None:
        receipt.status = 'CARGO_UNKNOWN'
    elif IntakeOccurrence.objects.filter(object_ref=receipt.object_ref, received_at__gte=receipt.received_at).exclude(pk=receipt.pk).exists():
        receipt.status = 'STALE_RECEIPT'
    else:
        actor = automatic_actor()
        older = IntakeOccurrence.objects.filter(object_ref=receipt.object_ref, received_at__lt=receipt.received_at,
            stored_event_id__isnull=True, order__cell_plan_dispatches__isnull=True, order__commands__isnull=True)
        Order.objects.filter(pk__in=older.values('order_id')).update(auto_replan=False)
        older.update(status='STALE_RECEIPT')
        if receipt.order_id is None:
            unbound = Order.objects.filter(kind='INBOUND', cargo=cargo, intake_occurrence__isnull=True)
            candidates = list(unbound.filter(
                cell_plan_dispatches__isnull=True, commands__isnull=True).order_by('pk')[:2])
            if (len(candidates) > 1 or unbound.filter(cell_plan_dispatches__state__in=ACTIVE_CELL_PLAN_DISPATCH_STATES).exists()
                    or unbound.filter(commands__state__in=ACTIVE_COMMAND_STATES).exists()):
                receipt.status = 'AMBIGUOUS_ORDER'
            else:
                if candidates:
                    receipt.order = candidates[0]
                else:
                    group, seq = back_of_tier(cargo.shipper_tier or 3, 'INBOUND')
                    receipt.order = Order.objects.create(created_by=actor, kind='INBOUND', cargo=cargo,
                        priority_group=group, sequence=seq, tie_policy='FIXED')
                Audit.objects.create(actor=actor, action='order.intake_received', object_type='Order',
                    object_id=str(receipt.order_id), detail={'event_id': event['event_id'], 'occurrence_id': receipt.pk})
        if receipt.order_id:
            order = receipt.order
            saved = Plan.objects.filter(order=order).select_related('observation', 'condition').order_by('-pk').first()
            binding = order.planning_observation
            if binding is None and saved and placement_decision(saved) and not plan_changes(order, saved):
                binding = saved.observation
            observations = Observation.objects.filter(payload__schema='grid-placement-v1', provenance__in=('DECLARED', 'PORT_EVENT'))
            if binding:
                observations = observations.filter(condition_id=binding.condition_id, source_id=binding.source_id)
            observation = observations.select_related('condition').order_by('-observed_at', '-pk').first()
            if observation is None or not covers_spatial_yard(observation):
                receipt.status = 'WAITING_FOR_YARD'
            elif not order.auto_replan and Order.objects.filter(auto_replan=True).count() >= 16:
                receipt.status = 'PLANNING_CAPACITY'
            else:
                if not order.auto_replan or not order.planning_observation_id:
                    order.auto_replan, order.planning_observation = True, observation
                    order.save(update_fields=['auto_replan', 'planning_observation'])
                receipt.status = 'PLANNING'
                receipt.save()
                replan_enabled(actor, 'intake.received')
    receipt.save()


@serialized
@transaction.atomic
def _stored(event):
    receipt = IntakeOccurrence.objects.filter(pk=event['intake_occurrence_id']).first()
    if receipt is None or event['observed_at'] > timezone.now():
        return  # Replay after the receipt/registration arrives.
    if receipt.order_id is None:
        return False if receipt.status == 'STALE_RECEIPT' else None
    retry = receipt.stored_event_id == event['event_id'] and receipt.status == 'YARD_CONFLICT'
    if receipt.stored_event_id == event['event_id'] and not retry:
        if not receipt.result_identity:
            receipt.result_identity = event['identity']
            receipt.save(update_fields=['result_identity'])
        return receipt.status in ('STORED', 'SLOT_MISMATCH')
    dispatch = CellPlanDispatch.objects.filter(order_id=receipt.order_id).select_related('plan', 'observation__condition', 'order__cargo').order_by('-pk').first()
    if (receipt.object_ref != event['object_ref'] or (receipt.stored_event_id and not retry) or dispatch is None
            or dispatch.request_key != event['placement_attempt_id'] or dispatch.request_payload.get('intake_occurrence_id') != receipt.pk
            or dispatch.request_payload.get('placement_attempt_id') != dispatch.request_key
            or not requested_slot(dispatch) or event['observed_at'] < receipt.received_at
            or IntakeOccurrence.objects.filter(object_ref=receipt.object_ref, received_at__gte=receipt.received_at).exclude(pk=receipt.pk).exists()):
        _record_rejection_once(event, 'STALE_OR_UNBOUND_PLACEMENT', '입고 회차와 최신 배치 시도를 대조할 수 없습니다.')
        return False
    try:
        project_stored_event(event, dispatch=dispatch)
    except _ProjectionRejected as exc:
        _record_rejection_once(event, exc.code, exc.message)
        receipt.status = 'YARD_CONFLICT'
    else:
        receipt.status = 'STORED' if requested_slot(dispatch) == event['slot_ref'] else 'SLOT_MISMATCH'
    receipt.result_dispatch = dispatch
    receipt.result_identity = event['identity']
    receipt.stored_event_id, receipt.observed_slot, receipt.observed_at = event['event_id'], event['slot_ref'], event['observed_at']
    receipt.save()
    Order.objects.filter(pk=receipt.order_id).update(auto_replan=False)
    if receipt.status != 'YARD_CONFLICT':
        observed = Observation.objects.filter(source_revision=event['event_id'], provenance='PORT_EVENT').first()
        if observed:
            pending = Order.objects.filter(auto_replan=True, intake_occurrence__status='PLANNING',
                planning_observation__condition=observed.condition, cell_plan_dispatches__isnull=True,
                commands__isnull=True)
            pending.update(planning_observation=observed)
            replan_enabled(automatic_actor(), 'intake.stored', source_id=observed.source_id)
        return True


def _project(event, ledger):
    if event['kind'] == 'container.stored':
        outcome = _stored(event)
        if outcome:
            ledger.complete_bound_receipt(event)
        return outcome is not None
    _receive(event)
    receipt = IntakeOccurrence.objects.filter(pk=event['intake_occurrence_id']).first()
    if receipt is None:
        return False
    if receipt.receipt_event_id != event['event_id']:
        return True
    if (receipt.order_id and receipt.status != 'STALE_RECEIPT' and not IntakeOccurrence.objects.filter(
            object_ref=receipt.object_ref, received_at__gte=receipt.received_at).exclude(pk=receipt.pk).exists()):
        ledger.bind_received_occurrence(event)
    return receipt.status not in WAITING


@serialized
@transaction.atomic
def project_occurrences(ledger):
    """Checkpoint in the projected database so restoring it replays the matching journal."""
    cursor, _ = IntakeProjectionCursor.objects.select_for_update().get_or_create(pk=ledger.stream_id())
    pending = set(cursor.pending)
    last = cursor.sequence
    for seq, event in ledger.occurrence_batch(last, sorted(pending)):
        try:
            with transaction.atomic():
                finished = _project(event, ledger)
        except Exception:
            logger.exception('Intake occurrence event %s will be retried', event['event_id'])
            finished = False
        if finished:
            pending.discard(seq)
        else:
            pending.add(seq)
        last = max(last, seq)
    if last != cursor.sequence or pending != set(cursor.pending):
        cursor.sequence, cursor.pending = last, sorted(pending)
        cursor.save(update_fields=['sequence', 'pending'])

"""Two-leg ROSY Pinky pickup and exit coordinator for completed OUTBOUND arm commands.

Uses two sequential single-step TripIntents (``S_OUT`` pickup -> physical loading
confirmation -> pinned vehicle ``S_EXIT`` departure) with empty ``object_ref`` so
ROSY ``intake_transfer`` never treats outbound trips as supply-to-cell intake.
"""
import logging
import os
import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from harbor.model import identifiers
from harbor.site_places import parse_site_places, resolve_outbound_dispatch_stops, validate_trip_places
from harbor.transport import TransportLedger, TripIntent
from harbor_web.command_models import ACTIVE_OUTBOUND_DISPATCH_STATES, Command, OutboundDispatch
from harbor_web.coordination import serialized
from harbor_web.models import Audit
from harbor_web.services import Conflict, _role

logger = logging.getLogger(__name__)

_ROBOT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$')

_STATE_LABELS = {
    'PICKUP_PLANNED': '차량 호출 준비됨',
    'PICKUP_PROPOSED': '반출대 도킹 승인·이동 중',
    'PICKUP_ARRIVED': '반출대 도킹 완료 · 적재 확인 대기',
    'LOADED': '적재 확인됨 · 출구 이동 준비됨',
    'EXIT_PROPOSED': '출구 이동 승인·주행 중',
    'EXITED': '출구 반출 완료',
    'HOLD': '차량 반출 보류',
}


def pickup_trip_key(dispatch):
    return f'harbor-outbound-pickup:{dispatch.key}'


def exit_trip_key(dispatch):
    return f'harbor-outbound-exit:{dispatch.key}'


def configured_vehicles():
    """Return validated tuple of candidate Pinky IDs from HARBOR_OUTBOUND_VEHICLES."""
    raw = [part.strip() for part in os.environ.get('HARBOR_OUTBOUND_VEHICLES', '').split(',') if part.strip()]
    if not raw or len(raw) > 8:
        raise ValidationError('OUTBOUND_VEHICLES_UNCONFIGURED')
    try:
        identifiers(tuple(raw))
    except ValueError as exc:
        raise ValidationError('OUTBOUND_VEHICLES_INVALID') from exc
    if any(not _ROBOT_RE.fullmatch(robot_id) for robot_id in raw):
        raise ValidationError('OUTBOUND_VEHICLES_INVALID')
    return tuple(raw)


def reserved_vehicles(*, exclude_dispatch_id=None):
    qs = OutboundDispatch.objects.filter(state__in=ACTIVE_OUTBOUND_DISPATCH_STATES).exclude(assigned_vehicle_id='')
    if exclude_dispatch_id is not None:
        qs = qs.exclude(pk=exclude_dispatch_id)
    return set(qs.values_list('assigned_vehicle_id', flat=True))


def available_vehicles(candidates=None):
    pool = tuple(candidates) if candidates is not None else configured_vehicles()
    if not pool or len(pool) > 8:
        raise ValidationError('OUTBOUND_VEHICLES_UNCONFIGURED')
    try:
        identifiers(pool)
    except ValueError as exc:
        raise ValidationError('OUTBOUND_VEHICLES_INVALID') from exc
    if any(not _ROBOT_RE.fullmatch(robot_id) for robot_id in pool):
        raise ValidationError('OUTBOUND_VEHICLES_INVALID')
    reserved = reserved_vehicles()
    free = tuple(robot_id for robot_id in pool if robot_id not in reserved)
    if not free:
        raise ValidationError('OUTBOUND_VEHICLES_BUSY')
    return free


def _transport_for(registry=None):
    if registry is not None:
        transport = getattr(registry, 'transport', None)
        if transport is not None:
            return transport
        return TransportLedger(registry.config['path'])
    from harbor_web.commands import acquire_registry
    return acquire_registry().transport


def _resolve_stops(places, stand_zone_id):
    if places is None:
        from harbor_web.commands import acquire_registry
        raw = acquire_registry().client.site_places()
        places = parse_site_places(raw)
    elif isinstance(places, dict):
        places = parse_site_places(places)
    stops, reason = resolve_outbound_dispatch_stops(places, stand_zone_id=stand_zone_id)
    if stops is None:
        raise ValidationError(reason)
    pickup_stop_id, exit_stop_id = stops
    try:
        validate_trip_places(places, pickup_stop_id)
        validate_trip_places(places, exit_stop_id)
    except ValueError as exc:
        raise ValidationError('OUTBOUND_STOP_UNVERIFIED') from exc
    return pickup_stop_id, exit_stop_id, places.digest


def _leg1_intent(dispatch):
    return TripIntent(
        pickup_trip_key(dispatch),
        tuple(dispatch.candidate_robot_ids),
        dispatch.pickup_stop_id,
        object_ref='',
    )


def _leg2_intent(dispatch):
    return TripIntent(
        exit_trip_key(dispatch),
        (dispatch.assigned_vehicle_id,),
        dispatch.exit_stop_id,
        object_ref='',
    )


@serialized
def prepare_outbound_dispatch(actor, *, command_id, expected_version=None, candidates=None, places=None, registry=None):
    """Create a durable OutboundDispatch and prepare Leg 1 (`S_OUT`) after stand arm completion."""
    _role(actor, 'operator')
    command = Command.objects.select_related('order__cargo').filter(pk=command_id).first()
    if command is None:
        raise ValidationError('COMMAND_NOT_FOUND')
    if expected_version is not None and command.version != expected_version:
        raise Conflict('Command version changed.')
    if command.order.kind != 'OUTBOUND' or command.state != 'COMPLETED':
        raise ValidationError('OUTBOUND_COMMAND_NOT_COMPLETED')
    if OutboundDispatch.objects.filter(command=command).exists():
        raise Conflict('Outbound dispatch already exists for this command.')

    stand_zone_id = (command.binding or {}).get('outbound_stand') or 'stand'
    if OutboundDispatch.objects.filter(stand_zone_id=stand_zone_id, state__in=ACTIVE_OUTBOUND_DISPATCH_STATES).exists():
        raise Conflict('Another outbound dispatch is active at this stand.')

    try:
        if places is None and registry is not None:
            places = registry.client.site_places()
        pickup_stop_id, exit_stop_id, digest = _resolve_stops(places, stand_zone_id)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError('SITE_PLACES_UNAVAILABLE') from exc

    pool = available_vehicles(candidates)
    transport = _transport_for(registry)

    try:
        with transaction.atomic():
            dispatch = OutboundDispatch.objects.create(
                command=command,
                cargo=command.order.cargo,
                actor=actor,
                stand_zone_id=stand_zone_id,
                pickup_stop_id=pickup_stop_id,
                exit_stop_id=exit_stop_id,
                site_places_digest=digest,
                candidate_robot_ids=list(pool),
                state='PICKUP_PLANNED',
            )
            Audit.objects.create(
                actor=actor,
                action='outbound.dispatch_prepared',
                object_type='OutboundDispatch',
                object_id=str(dispatch.pk),
                detail={
                    'command_id': command.pk,
                    'pickup_stop_id': pickup_stop_id,
                    'exit_stop_id': exit_stop_id,
                    'candidates': list(pool),
                },
            )
    except IntegrityError as exc:
        raise Conflict('Outbound dispatch conflicts with active stand allocation.') from exc

    transport.prepare(_leg1_intent(dispatch))
    return dispatch


def maybe_auto_prepare(command, registry):
    """Best-effort opt-in hook when HARBOR_OUTBOUND_AUTO_DISPATCH=1 and command completes."""
    if os.environ.get('HARBOR_OUTBOUND_AUTO_DISPATCH') != '1':
        return None
    if command.order.kind != 'OUTBOUND' or command.state != 'COMPLETED':
        return None
    try:
        return prepare_outbound_dispatch(command.actor, command_id=command.pk, registry=registry)
    except Exception:
        logger.exception('Auto outbound dispatch skipped for completed command %s', command.pk)
        return None


@serialized
def confirm_outbound_loaded(actor, *, dispatch_id, expected_version, now=None, registry=None):
    """Named field/operator confirms cargo transferred from stand onto the docked Pinky."""
    _role(actor, 'field', 'operator')
    now = now or timezone.now()
    dispatch = OutboundDispatch.objects.select_related('command', 'cargo').filter(pk=dispatch_id).first()
    if dispatch is None:
        raise ValidationError('OUTBOUND_DISPATCH_NOT_FOUND')
    if dispatch.version != expected_version or dispatch.state != 'PICKUP_ARRIVED' or not dispatch.assigned_vehicle_id:
        raise Conflict('Outbound dispatch is not awaiting load confirmation.')

    transport = _transport_for(registry)
    with transaction.atomic():
        updated = OutboundDispatch.objects.filter(
            pk=dispatch.pk,
            version=expected_version,
            state='PICKUP_ARRIVED',
        ).update(
            state='LOADED',
            reason='',
            loaded_by=actor,
            loaded_at=now,
            loaded_event_id='',
            version=expected_version + 1,
            updated_at=now,
        )
        if updated != 1:
            raise Conflict('Outbound dispatch state changed concurrently.')
        dispatch.refresh_from_db()
        Audit.objects.create(
            actor=actor,
            action='outbound.loaded_confirmed',
            object_type='OutboundDispatch',
            object_id=str(dispatch.pk),
            detail={'vehicle_id': dispatch.assigned_vehicle_id, 'exit_stop_id': dispatch.exit_stop_id},
        )
    transport.prepare(_leg2_intent(dispatch))
    return dispatch


def _save_progress(dispatch, *, state, reason='', assigned_vehicle_id=None, pickup_mission_id=None, exit_mission_id=None):
    updates = {'state': state, 'reason': reason[:192], 'version': dispatch.version + 1, 'updated_at': timezone.now()}
    if assigned_vehicle_id is not None:
        updates['assigned_vehicle_id'] = assigned_vehicle_id
    if pickup_mission_id is not None:
        updates['pickup_mission_id'] = pickup_mission_id
    if exit_mission_id is not None:
        updates['exit_mission_id'] = exit_mission_id
    updated = OutboundDispatch.objects.filter(pk=dispatch.pk, version=dispatch.version).update(**updates)
    if updated == 1:
        dispatch.refresh_from_db()


def _advance_leg(dispatch, transport, client):
    leg2 = dispatch.loaded_at is not None
    intent = _leg2_intent(dispatch) if leg2 else _leg1_intent(dispatch)
    transport.prepare(intent)
    row = transport.read(intent.request_key)
    if row['state'] == 'PLANNED':
        job = transport.propose(intent.request_key, client)
    elif row['state'] != 'SUBMITTING':
        job = transport.reconcile_proposal(intent.request_key, client)
    else:
        return dispatch.state

    if job is None:
        _save_progress(dispatch, state='LOADED' if leg2 else 'PICKUP_PLANNED', reason='')
        return dispatch.state
    if not job.get('mission_id'):
        _save_progress(dispatch, state='EXIT_PROPOSED' if leg2 else 'PICKUP_PROPOSED', reason='')
        return dispatch.state

    status = job.get('status')
    if leg2:
        if status == 'HOLD':
            next_state = 'HOLD'
            reason = job.get('reason') or 'TRIP_HELD'
        elif status == 'GOAL_CONFIRMED':
            next_state = 'EXITED'
            reason = ''
        else:
            next_state = 'EXIT_PROPOSED'
            reason = ''
        _save_progress(dispatch, state=next_state, reason=reason, exit_mission_id=job['mission_id'])
        return next_state

    if status == 'HOLD':
        next_state = 'HOLD'
        reason = job.get('reason') or 'TRIP_HELD'
    elif status == 'GOAL_CONFIRMED':
        next_state = 'PICKUP_ARRIVED'
        reason = ''
    else:
        next_state = 'PICKUP_PROPOSED'
        reason = ''
    _save_progress(
        dispatch,
        state=next_state,
        reason=reason,
        assigned_vehicle_id=job['robot_id'],
        pickup_mission_id=job['mission_id'],
    )
    return next_state


def tick(registry, *, now=None):
    """Advance active OutboundDispatch records without touching PICKUP_ARRIVED until load confirmation."""
    del now
    transport = _transport_for(registry)
    client = registry.client
    results = {}
    active = list(
        OutboundDispatch.objects.filter(
            state__in=('PICKUP_PLANNED', 'PICKUP_PROPOSED', 'LOADED', 'EXIT_PROPOSED', 'HOLD')
        ).order_by('pk')
    )
    for dispatch in active:
        try:
            results[dispatch.pk] = _advance_leg(dispatch, transport, client)
        except Exception as exc:
            logger.exception('반출대 차량 이송 #%s 처리 실패; 다음 주기에 재시도', dispatch.pk)
            results[dispatch.pk] = f'ERROR: {type(exc).__name__}'
    return results


def awaiting_load_confirmation(command):
    return OutboundDispatch.objects.filter(command=command, state='PICKUP_ARRIVED').exists()


def dispatch_view(command):
    """Read-only context for work_detail; contains no forbidden UI glossary terms."""
    if command.order.kind != 'OUTBOUND' or command.state != 'COMPLETED':
        return dict(show=False)
    dispatch = OutboundDispatch.objects.filter(command=command).first()
    if dispatch is None:
        return dict(show=True, exists=False, status_label='대기 중 · 차량 호출 전', can_start=True, can_confirm_load=False)
    return dict(
        show=True,
        exists=True,
        dispatch=dispatch,
        status_label=_STATE_LABELS.get(dispatch.state, dispatch.state),
        can_start=False,
        can_confirm_load=dispatch.state == 'PICKUP_ARRIVED',
        vehicle_label=dispatch.assigned_vehicle_id or '미배정',
    )

"""Two-leg ROSY Pinky pickup and exit coordinator for completed OUTBOUND arm commands.

Uses two sequential single-step TripIntents (``S_OUT`` pickup -> physical loading
confirmation -> pinned vehicle ``S_EXIT`` departure) with empty ``object_ref`` so
ROSY ``intake_transfer`` never treats outbound trips as supply-to-cell intake.
"""
import logging
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
import re
from urllib.error import HTTPError, URLError

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from harbor.model import identifiers
from harbor.site_places import parse_site_places, resolve_outbound_dispatch_stops, validate_trip_places
from harbor.transport import TransportLedger, TripIntent
from harbor_web.command_models import ACTIVE_OUTBOUND_DISPATCH_STATES, CellPlanDispatch, Command, OutboundDispatch
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
    'CANCELLED': '반출 취소',
    'HOLD': '차량 반출 보류',
}

# Terminal outbound states; the stand zone and the pinned vehicle free up the moment a row leaves the active set.
CLOSED_OUTBOUND_STATES = ('EXITED', 'CANCELLED')


def _normalize_stand(raw):
    """Legacy bindings name the stand as 'out' or a stand-anchored slot; the dispatch only knows 'stand'."""
    return 'stand' if not raw or raw == 'out' or raw.startswith(('stand-', 'stand:', 'stand_')) else raw


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


def _active_inbound_vehicles():
    from harbor_web.models import DepartureIntent
    ledger_path = os.environ.get('HARBOR_LEDGER_PATH', '').strip()
    if not ledger_path or not Path(ledger_path).is_file():
        return set()
    from harbor.port_events import IntakeLedger
    intake = IntakeLedger(ledger_path)
    busy = set()
    for dep in DepartureIntent.objects.select_related('cargo').only('vehicle_id', 'cargo__code'):
        row = intake.read(dep.cargo.code)
        if not row or row.get('state') != 'COMPLETED':
            busy.add(dep.vehicle_id)
    return busy


def _fleet_unavailable_vehicles(fleet_state):
    if not isinstance(fleet_state, dict) or not isinstance(fleet_state.get('robots'), list):
        return set()
    unavailable = set()
    for robot in fleet_state['robots']:
        if not isinstance(robot, dict) or not robot.get('robot_id'):
            continue
        rid = robot['robot_id']
        if robot.get('online') is not True:
            unavailable.add(rid)
            continue
        state = robot.get('state') if isinstance(robot.get('state'), dict) else {}
        safety = state.get('safety') if isinstance(state.get('safety'), dict) else {}
        if safety.get('estop') is True:
            unavailable.add(rid)
            continue
        ph = robot.get('power_health') if isinstance(robot.get('power_health'), dict) else {}
        ph_bat = ph.get('battery') if isinstance(ph.get('battery'), dict) else {}
        if ph_bat.get('evidence') == 'fresh':
            if (ph_bat.get('level') in ('critical', 'deep')
                    or ph_bat.get('charging_state') == 'confirmed'
                    or ph_bat.get('charging_latched') is True):
                unavailable.add(rid)
            continue
        st_bat = state.get('battery_status') if isinstance(state.get('battery_status'), dict) else {}
        if st_bat.get('level') in ('critical', 'deep') or st_bat.get('charging') is True:
            unavailable.add(rid)
    return unavailable


def available_vehicles(candidates=None, *, fleet_state=None):
    pool = tuple(candidates) if candidates is not None else configured_vehicles()
    if not pool or len(pool) > 8:
        raise ValidationError('OUTBOUND_VEHICLES_UNCONFIGURED')
    try:
        identifiers(pool)
    except ValueError as exc:
        raise ValidationError('OUTBOUND_VEHICLES_INVALID') from exc
    if any(not _ROBOT_RE.fullmatch(robot_id) for robot_id in pool):
        raise ValidationError('OUTBOUND_VEHICLES_INVALID')
    reserved = reserved_vehicles() | _active_inbound_vehicles() | _fleet_unavailable_vehicles(fleet_state)
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


def _create_outbound_dispatch(
    actor,
    *,
    cargo,
    stand_zone_id,
    candidates=None,
    places=None,
    registry=None,
    command=None,
    cell_plan_dispatch=None,
    audit_extra=None,
):
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

    from harbor_web.fleet_connection import cached_fleet_state
    live_fleet_state = cached_fleet_state()
    pool = available_vehicles(candidates, fleet_state=live_fleet_state)
    transport = _transport_for(registry)

    try:
        with transaction.atomic():
            dispatch = OutboundDispatch.objects.create(
                command=command,
                cell_plan_dispatch=cell_plan_dispatch,
                cargo=cargo,
                actor=actor,
                stand_zone_id=stand_zone_id,
                pickup_stop_id=pickup_stop_id,
                exit_stop_id=exit_stop_id,
                site_places_digest=digest,
                candidate_robot_ids=list(pool),
                state='PICKUP_PLANNED',
            )
            detail = {
                **(audit_extra or {}),
                'pickup_stop_id': pickup_stop_id,
                'exit_stop_id': exit_stop_id,
                'candidates': list(pool),
            }
            Audit.objects.create(
                actor=actor,
                action='outbound.dispatch_prepared',
                object_type='OutboundDispatch',
                object_id=str(dispatch.pk),
                detail=detail,
            )
    except IntegrityError as exc:
        raise Conflict('Outbound dispatch conflicts with active stand allocation.') from exc

    transport.prepare(_leg1_intent(dispatch))
    return dispatch


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

    stand_zone_id = _normalize_stand((command.binding or {}).get('outbound_stand'))
    return _create_outbound_dispatch(
        actor,
        cargo=command.order.cargo,
        stand_zone_id=stand_zone_id,
        candidates=candidates,
        places=places,
        registry=registry,
        command=command,
        audit_extra={'command_id': command.pk},
    )


@serialized
def prepare_outbound_dispatch_from_cell_plan(
    actor, *, cell_plan_dispatch_id, candidates=None, places=None, registry=None
):
    """Create a durable OutboundDispatch and prepare Leg 1 (`S_OUT`) after a completed outbound CellPlanDispatch."""
    _role(actor, 'operator')
    row = CellPlanDispatch.objects.select_related('order__cargo').filter(pk=cell_plan_dispatch_id).first()
    if row is None:
        raise ValidationError('CELL_PLAN_DISPATCH_NOT_FOUND')
    if row.order.kind != 'OUTBOUND' or row.order.cargo_id is None or row.state != 'COMPLETED':
        raise ValidationError('OUTBOUND_CELL_PLAN_NOT_COMPLETED')
    if OutboundDispatch.objects.filter(cell_plan_dispatch=row).exists():
        raise Conflict('Outbound dispatch already exists for this cell plan dispatch.')
    if row.planned_steps and not any(
        (dst := str(step.get('dst_wire_ref') or step.get('dst_site') or '')) == 'stand'
        or dst.startswith(('stand-', 'stand_', 'stand:'))
        for step in row.planned_steps
    ):
        raise ValidationError('CELL_PLAN_DOES_NOT_TARGET_STAND')
    stand_zone_id = os.environ.get('HARBOR_OUTBOUND_STAND', '').strip() or 'stand'
    return _create_outbound_dispatch(
        actor,
        cargo=row.order.cargo,
        stand_zone_id=stand_zone_id,
        candidates=candidates,
        places=places,
        registry=registry,
        cell_plan_dispatch=row,
        audit_extra={'cell_plan_dispatch_id': row.pk, 'rosy_plan_id': row.rosy_plan_id},
    )


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


def maybe_auto_prepare_from_cell_plan(cell_plan_dispatch, registry):
    """Best-effort opt-in hook when HARBOR_OUTBOUND_AUTO_DISPATCH=1 and outbound cell plan completes."""
    if os.environ.get('HARBOR_OUTBOUND_AUTO_DISPATCH') != '1':
        return None
    if cell_plan_dispatch.order.kind != 'OUTBOUND' or cell_plan_dispatch.state != 'COMPLETED':
        return None
    try:
        return prepare_outbound_dispatch_from_cell_plan(
            cell_plan_dispatch.created_by,
            cell_plan_dispatch_id=cell_plan_dispatch.pk,
            registry=registry,
        )
    except Exception:
        logger.exception('Auto outbound dispatch skipped for completed cell plan %s', cell_plan_dispatch.pk)
        return None


@serialized
def confirm_outbound_loaded(actor, *, dispatch_id, expected_version, now=None, registry=None):
    """Named field/operator confirms cargo transferred from stand onto the docked Pinky."""
    _role(actor, 'field', 'operator')
    now = now or timezone.now()
    dispatch = OutboundDispatch.objects.select_related('command', 'cell_plan_dispatch', 'cargo').filter(pk=dispatch_id).first()
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


def _mark_cancelled(dispatch, *, reason='CANCELLED_BY_OPERATOR', **mission_fields):
    """Leaving the active state set releases the stand zone and the pinned vehicle immediately."""
    _save_progress(dispatch, state='CANCELLED', reason=reason[:192], **mission_fields)
    Audit.objects.create(
        actor=dispatch.actor,
        action='outbound.dispatch_cancelled',
        object_type='OutboundDispatch',
        object_id=str(dispatch.pk),
        detail={'reason': reason[:192]},
    )
    return 'CANCELLED'


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
            if (job.get('reason') or '') == 'CANCELLED_BY_OPERATOR':
                return _mark_cancelled(dispatch, exit_mission_id=job['mission_id'])
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
        if (job.get('reason') or '') == 'CANCELLED_BY_OPERATOR':
            return _mark_cancelled(
                dispatch, assigned_vehicle_id=job['robot_id'], pickup_mission_id=job['mission_id'])
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


DEFAULT_PORT_EVENT_CLOCK_SKEW_S = 30.0


def _port_event_clock_skew():
    """Return allowed clock skew for port event timestamps (HARBOR_PORT_EVENT_CLOCK_SKEW_S, default 30s)."""
    raw = os.environ.get('HARBOR_PORT_EVENT_CLOCK_SKEW_S', '').strip()
    if not raw:
        return timedelta(seconds=DEFAULT_PORT_EVENT_CLOCK_SKEW_S)
    try:
        seconds = float(raw)
    except ValueError:
        return timedelta(seconds=DEFAULT_PORT_EVENT_CLOCK_SKEW_S)
    if not math.isfinite(seconds) or seconds < 0:
        return timedelta(seconds=DEFAULT_PORT_EVENT_CLOCK_SKEW_S)
    return timedelta(seconds=seconds)


def _observed_at(event):
    """Parse one stored port event's `observed_at` into an aware datetime; None when unparseable."""
    text = event.get('observed_at')
    try:
        when = datetime.fromisoformat(text.replace('Z', '+00:00')) if isinstance(text, str) else None
    except ValueError:
        when = None
    return when if when is not None and when.utcoffset() is not None else None


def _apply_outbound_loaded_events(transport, *, now=None):
    """Transition PICKUP_ARRIVED dispatches to LOADED when `container.loaded_for_outbound` matches."""
    ledger_path = os.environ.get('HARBOR_LEDGER_PATH', '').strip()
    if not ledger_path or not Path(ledger_path).is_file():
        return []
    waiting = list(
        OutboundDispatch.objects.select_related('cargo', 'actor')
        .filter(state='PICKUP_ARRIVED')
        .exclude(assigned_vehicle_id='')
        .order_by('pk')
    )
    if not waiting:
        return []
    from harbor.port_events import IntakeLedger
    events = IntakeLedger(ledger_path).outbound_loaded_events()
    if not events:
        return []
    by_pair = {}
    for ev in events:
        by_pair.setdefault((ev.get('object_ref'), ev.get('vehicle_id')), []).append(ev)
    # An event_id proves one physical load: it must never advance a second dispatch, whether a
    # past (closed) dispatch already claimed it or a sibling dispatch matched it in this same tick.
    claimed = set(
        OutboundDispatch.objects.exclude(loaded_event_id='').values_list('loaded_event_id', flat=True)
    )
    skew = _port_event_clock_skew()
    transitioned = []
    stamp = now or timezone.now()
    for dispatch in waiting:
        candidates = by_pair.get((dispatch.cargo.code, dispatch.assigned_vehicle_id), ())
        matched = None
        for ev in candidates:  # ledger rowid order, so the first hit is the earliest event
            ev_id = (ev.get('event_id') or '')[:64]
            if not ev_id or ev_id in claimed:
                continue
            observed = _observed_at(ev)
            if dispatch.created_at is not None and (observed is None or observed < dispatch.created_at - skew):
                continue  # stale: the cargo load predates this (recreated) dispatch beyond allowed clock skew
            matched = ev
            break
        if matched is None:
            continue
        ev_id = matched['event_id'][:64]
        try:
            with transaction.atomic():
                updated = OutboundDispatch.objects.filter(
                    pk=dispatch.pk,
                    version=dispatch.version,
                    state='PICKUP_ARRIVED',
                ).update(
                    state='LOADED',
                    reason='',
                    loaded_by=None,
                    loaded_at=stamp,
                    loaded_event_id=ev_id,
                    version=dispatch.version + 1,
                    updated_at=stamp,
                )
                if updated != 1:
                    continue
                claimed.add(ev_id)
                dispatch.refresh_from_db()
                Audit.objects.create(
                    actor=dispatch.actor,
                    action='outbound.loaded_from_port_event',
                    object_type='OutboundDispatch',
                    object_id=str(dispatch.pk),
                    detail={
                        'vehicle_id': dispatch.assigned_vehicle_id,
                        'event_id': matched['event_id'],
                        'exit_stop_id': dispatch.exit_stop_id,
                    },
                )
        except IntegrityError:
            claimed.add(ev_id)
            continue
        transport.prepare(_leg2_intent(dispatch))
        transitioned.append(dispatch.pk)
    return transitioned


def tick(registry, *, now=None):
    """Advance active OutboundDispatch records without touching PICKUP_ARRIVED until load confirmation."""
    transport = _transport_for(registry)
    client = registry.client
    results = {}
    _apply_outbound_loaded_events(transport, now=now)
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


@serialized
def cancel_outbound_dispatch(actor, *, dispatch_id, reason=''):
    """Operator cancels a non-terminal dispatch; the stand zone and assigned vehicle free immediately."""
    _role(actor, 'operator')
    dispatch = OutboundDispatch.objects.select_related('command', 'cell_plan_dispatch', 'cargo').filter(pk=dispatch_id).first()
    if dispatch is None:
        raise ValidationError('OUTBOUND_DISPATCH_NOT_FOUND')
    if dispatch.state in CLOSED_OUTBOUND_STATES:
        raise Conflict('Outbound dispatch is already closed.')
    saved = (reason or 'OPERATOR_CANCELLED')[:192]
    _save_progress(dispatch, state='CANCELLED', reason=saved)
    Audit.objects.create(
        actor=actor,
        action='outbound.dispatch_cancelled',
        object_type='OutboundDispatch',
        object_id=str(dispatch.pk),
        detail={'reason': saved, 'cancelled_by': 'operator'},
    )
    return dispatch


def awaiting_load_confirmation(command):
    return OutboundDispatch.objects.filter(command=command, state='PICKUP_ARRIVED').exists()


def dispatch_view(command):
    """Read-only context for work_detail; contains no forbidden UI glossary terms."""
    if command.order.kind != 'OUTBOUND' or command.state != 'COMPLETED':
        return dict(show=False)
    dispatch = OutboundDispatch.objects.filter(command=command).first()
    if dispatch is None:
        return dict(show=True, exists=False, status_label='대기 중 · 차량 호출 전',
                    can_start=True, can_confirm_load=False, can_cancel=False)
    return dict(
        show=True,
        exists=True,
        dispatch=dispatch,
        status_label=_STATE_LABELS.get(dispatch.state, dispatch.state),
        can_start=False,
        can_confirm_load=dispatch.state == 'PICKUP_ARRIVED',
        can_cancel=dispatch.state not in CLOSED_OUTBOUND_STATES,
        vehicle_label=dispatch.assigned_vehicle_id or '미배정',
    )

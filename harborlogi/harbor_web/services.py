"""Persist declared inputs and planning results; never issue physical commands."""
import hashlib
import json
from dataclasses import asdict
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from harbor.model import Bay, CargoHolding, YardSnapshot, PortConstraints, OutboundRequest, aware_time
from harbor.outbound import plan_outbound, OutboundProposal
from harbor_web.auth import has_role
from harbor_web.models import (clean_text, Cargo, Condition, Observation, Order, Plan, Audit,
                               FieldEpisode, FieldConfirmation, DepartureIntent, Measurement)


class Conflict(Exception):
    pass


def _role(actor, *roles):
    if not has_role(actor, *roles):
        raise PermissionDenied


def _save(actor, obj, action):
    obj.full_clean()
    obj.save()
    Audit.objects.create(actor=actor, action=action, object_type=type(obj).__name__, object_id=str(obj.pk))
    return obj


def _domain(call):
    try:
        return call()
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ValidationError('Invalid structured input: ' + str(error)) from error


def constraints(payload):
    def build():
        if not isinstance(payload, dict) or set(payload) != {'outbound_stand', 'temporary_bays', 'allowed_moves', 'max_observation_age_seconds', 'conveyor', 'arm_reach'}:
            raise ValueError('Condition requires exactly the documented keys.')
        for name in ('temporary_bays', 'allowed_moves', 'arm_reach'):
            if not isinstance(payload[name], list):
                raise ValueError('Expected lists.')
        for name in ('allowed_moves', 'arm_reach'):
            if any(not isinstance(edge, list) or len(edge) != 2 for edge in payload[name]):
                raise ValueError('Expected pairs.')
            if len({tuple(e) for e in payload[name]}) != len(payload[name]):
                raise ValueError('Duplicate pairs.')
        for text in (payload['outbound_stand'], payload['conveyor'], *payload['temporary_bays'],
                     *(v for edge in payload['allowed_moves'] for v in edge), *(v for edge in payload['arm_reach'] for v in edge)):
            if not isinstance(text, str):
                raise ValueError('Expected text identifiers.')
            clean_text(text)
        age = payload['max_observation_age_seconds']
        if type(age) not in (int, float):
            raise ValueError('Expected numeric freshness.')
        return PortConstraints(payload['outbound_stand'], tuple(payload['temporary_bays']),
                               frozenset(tuple(e) for e in payload['allowed_moves']), timedelta(seconds=age),
                               payload['conveyor'], frozenset(tuple(e) for e in payload['arm_reach']))
    return _domain(build)


def snapshot(observation):
    def build():
        payload = observation.payload
        if not isinstance(payload, dict) or set(payload) != {'bays', 'holdings'}:
            raise ValueError('Observation requires bays and holdings.')
        if not isinstance(payload['bays'], list) or not isinstance(payload['holdings'], list):
            raise ValueError('Expected observation lists.')
        bays, holdings = [], []
        for item in payload['bays']:
            if not isinstance(item, dict) or set(item) != {'bay_id', 'cargo', 'capacity'} or not isinstance(item['cargo'], list):
                raise ValueError('Invalid bay fields.')
            bays.append(Bay(item['bay_id'], tuple(item['cargo']), item['capacity']))
        for item in payload['holdings']:
            if not isinstance(item, dict) or set(item) != {'cargo_id', 'holder_id'}:
                raise ValueError('Invalid holding fields.')
            holdings.append(CargoHolding(**item))
        result = YardSnapshot(observation.pk or 0, observation.observed_at, tuple(bays), tuple(holdings))
        for text in (*(b.bay_id for b in result.bays), *(c for b in result.bays for c in b.cargo),
                     *(h.cargo_id for h in result.holdings), *(h.holder_id for h in result.holdings if h.holder_id is not None)):
            clean_text(text)
        codes = {c for b in result.bays for c in b.cargo} | {h.cargo_id for h in result.holdings}
        if codes != set(Cargo.objects.filter(code__in=codes).values_list('code', flat=True)):
            raise ValueError('Every observed cargo must be registered.')
        policy = constraints(observation.condition.payload)
        referenced = {policy.outbound_stand, *policy.temporary_bays, *(p for edge in policy.allowed_moves for p in edge)} - {policy.conveyor}
        if not referenced <= {b.bay_id for b in result.bays}:
            raise ValueError('Condition references unobserved bays.')
        return result
    return _domain(build)


def _provenance(provenance):
    if provenance == 'SYNTHETIC' and settings.MODE == 'live':
        raise ValidationError('Synthetic records are forbidden in live mode.')


@transaction.atomic
def register_cargo(actor, **validated_fields):
    _role(actor, 'operator')
    return _save(actor, Cargo(created_by=actor, **validated_fields), 'cargo.register')


@transaction.atomic
def register_condition(actor, *, name, calibration_ref, payload):
    _role(actor, 'admin')
    constraints(payload)
    return _save(actor, Condition(created_by=actor, name=name, calibration_ref=calibration_ref, payload=payload), 'condition.register')


@transaction.atomic
def record_observation(actor, *, source_id, source_revision, observed_at, provenance, payload, condition):
    _role(actor, 'admin')
    _provenance(provenance)
    obj = Observation(created_by=actor, source_id=source_id, source_revision=source_revision,
                      observed_at=observed_at, provenance=provenance, payload=payload, condition=condition)
    snapshot(obj)
    existing = Observation.objects.filter(source_id=source_id, source_revision=source_revision).first()
    if existing:
        if any(getattr(existing, key) != getattr(obj, key) for key in ('observed_at', 'provenance', 'payload', 'condition_id')):
            raise Conflict('Source revision already has different content.')
        return existing
    return _save(actor, obj, 'observation.record')


def _queue_policy(order):
    if order.kind != 'OUTBOUND':
        return
    peers = Order.objects.filter(kind='OUTBOUND', priority_group=order.priority_group).exclude(pk=order.pk)
    if peers.exclude(tie_policy=order.tie_policy).exists():
        raise ValidationError('All members of a priority group must use the same policy.')
    if order.tie_policy == 'FIXED' and peers.filter(sequence=order.sequence).exists():
        raise ValidationError('Fixed group sequence must be unique.')


@transaction.atomic
def create_order(actor, *, cargo, kind, priority_group, sequence, tie_policy, note=''):
    _role(actor, 'operator')
    obj = Order(created_by=actor, cargo=cargo, kind=kind, priority_group=priority_group,
                sequence=sequence, tie_policy=tie_policy, note=note)
    _queue_policy(obj)
    return _save(actor, obj, 'order.create')


@transaction.atomic
def update_order(actor, *, order_id, expected_version, priority_group, sequence, tie_policy, note=''):
    _role(actor, 'operator')
    obj = Order.objects.get(pk=order_id)
    if obj.version != expected_version:
        raise Conflict('Order changed; reload before editing.')
    for key, value in dict(priority_group=priority_group, sequence=sequence, tie_policy=tie_policy, note=note).items():
        setattr(obj, key, value)
    obj.version += 1
    obj.full_clean()
    _queue_policy(obj)
    updated = Order.objects.filter(pk=order_id, version=expected_version).update(
        priority_group=obj.priority_group, sequence=obj.sequence, tie_policy=obj.tie_policy,
        note=obj.note, version=obj.version, updated_at=timezone.now())
    if not updated:
        raise Conflict('Order changed; reload before editing.')
    Audit.objects.create(actor=actor, action='order.update', object_type='Order', object_id=str(obj.pk), detail={'version': obj.version})
    obj.refresh_from_db()
    return obj


def queue_digest():
    rows = list(Order.objects.order_by('pk').values('id', 'cargo_id', 'kind', 'priority_group', 'sequence', 'tie_policy', 'note', 'version'))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@transaction.atomic
def make_plan(actor, *, order_id, expected_version, observation_id, now=None):
    _role(actor, 'operator')
    order = Order.objects.get(pk=order_id)
    if order.version != expected_version:
        raise Conflict('Order changed; reload before planning.')
    observation = Observation.objects.select_related('condition').get(pk=observation_id)
    current = timezone.now() if now is None else now
    _domain(lambda: aware_time(current))
    yard = snapshot(observation)
    if order.kind == 'INBOUND':
        proposal = OutboundProposal('HOLD', 'INBOUND_PLANNER_NOT_AVAILABLE', yard.revision)
    elif Order.objects.filter(kind='OUTBOUND', priority_group__lt=order.priority_group).exists() or (
            order.tie_policy == 'FIXED' and Order.objects.filter(kind='OUTBOUND', priority_group=order.priority_group, sequence__lt=order.sequence).exists()):
        proposal = OutboundProposal('HOLD', 'PRIORITY_ORDER_BLOCKED', yard.revision)
    else:
        proposal = _domain(lambda: plan_outbound(yard, OutboundRequest(order.cargo.code, yard.revision), constraints(observation.condition.payload), now=current))
    result = asdict(proposal)
    # Serialize tuples as JSON lists before returning the same representation as persisted reads.
    result = json.loads(json.dumps(result))
    projected = {bay.bay_id: list(bay.cargo) for bay in yard.bays}
    for move in proposal.moves:
        projected[move.source].remove(move.cargo_id)
        projected[move.destination].append(move.cargo_id)
    result['projected_bays'] = ([{'bay_id': b.bay_id, 'cargo': projected[b.bay_id], 'capacity': b.capacity} for b in yard.bays]
                                if proposal.status == 'READY' else None)
    return _save(actor, Plan(created_by=actor, order=order, order_version=order.version,
                            observation=observation, condition=observation.condition,
                            queue_digest=queue_digest(), result=result), 'plan.create')


@transaction.atomic
def assign_episode(actor, *, order_id, vehicle_id, assigned_to):
    _role(actor, 'operator')
    _role(assigned_to, 'field')
    order = Order.objects.get(pk=order_id)
    if order.kind != 'INBOUND':
        raise ValidationError('Field load confirmation applies only to inbound orders.')
    return _save(actor, FieldEpisode(created_by=actor, order=order, vehicle_id=vehicle_id, assigned_to=assigned_to), 'episode.assign')


@transaction.atomic
def reassign_episode(actor, *, episode_id, expected_version, assigned_to):
    _role(actor, 'operator')
    _role(assigned_to, 'field')
    episode = FieldEpisode.objects.get(pk=episode_id)
    if episode.version != expected_version or episode.state == 'REASSIGNED':
        raise Conflict('Episode changed; reload before reassigning.')
    if DepartureIntent.objects.filter(episode=episode).exists():
        raise Conflict('An episode with a departure intent cannot be reassigned.')
    if not FieldEpisode.objects.filter(pk=episode.pk, version=expected_version).exclude(state='REASSIGNED').update(
            state='REASSIGNED', version=expected_version + 1):
        raise Conflict('Episode changed; reload before reassigning.')
    replacement = _save(actor, FieldEpisode(created_by=actor, order=episode.order, vehicle_id=episode.vehicle_id,
                                           assigned_to=assigned_to), 'episode.assign')
    Audit.objects.create(actor=actor, action='episode.reassign', object_type='FieldEpisode', object_id=str(episode.pk),
                         detail={'replacement_episode_id': replacement.pk})
    return replacement


def _fresh(observed_at, now):
    _domain(lambda: aware_time(observed_at))
    _domain(lambda: aware_time(now))
    if not timedelta(0) <= now - observed_at <= timedelta(minutes=5):
        raise ValidationError('Confirmation must be within five minutes and cannot be in the future.')


@transaction.atomic
def confirm_episode(actor, *, episode_id, expected_version, outcome, observed_at, now=None):
    _role(actor, 'field')
    episode = FieldEpisode.objects.get(pk=episode_id)
    if episode.assigned_to_id != actor.pk:
        raise PermissionDenied
    if episode.version != expected_version or episode.state != 'OPEN':
        raise Conflict('Episode changed or was already confirmed.')
    _fresh(observed_at, timezone.now() if now is None else now)
    obj = FieldConfirmation(created_by=actor, actor=actor, episode=episode, outcome=outcome, observed_at=observed_at, version=expected_version)
    obj.full_clean()
    state = {'CLEAR': 'CONFIRMED', 'MISMATCH': 'MISMATCH', 'UNABLE': 'UNABLE'}[outcome]
    if not FieldEpisode.objects.filter(pk=episode.pk, version=expected_version, state='OPEN', assigned_to=actor).update(version=expected_version + 1, state=state):
        raise Conflict('Episode changed or was already confirmed.')
    return _save(actor, obj, 'episode.confirm')


@transaction.atomic
def prepare_departure(actor, *, episode_id, confirmation_id, now=None):
    _role(actor, 'operator')
    episode = FieldEpisode.objects.select_related('order').get(pk=episode_id)
    confirmation = FieldConfirmation.objects.select_related('actor').get(pk=confirmation_id)
    if confirmation.episode_id != episode.pk:
        raise Conflict('Confirmation belongs to another episode.')
    existing = DepartureIntent.objects.filter(episode=episode).first()
    if existing:
        if (existing.confirmation_id != confirmation.pk or existing.vehicle_id != episode.vehicle_id
                or existing.cargo_id != episode.order.cargo_id or existing.actor_id != actor.pk):
            raise Conflict('Departure intent identity differs.')
        return existing
    if (episode.state != 'CONFIRMED' or confirmation.outcome != 'CLEAR'
            or confirmation.actor_id != episode.assigned_to_id or episode.version != confirmation.version + 1):
        raise ValidationError('Current assigned field user must explicitly confirm CLEAR.')
    _role(confirmation.actor, 'field')
    _fresh(confirmation.observed_at, timezone.now() if now is None else now)
    return _save(actor, DepartureIntent(created_by=actor, actor=actor, episode=episode, confirmation=confirmation,
                                        vehicle_id=episode.vehicle_id, cargo=episode.order.cargo), 'departure.prepare')


@transaction.atomic
def record_measurement(actor, **fields):
    _role(actor, 'operator')
    _provenance(fields.get('provenance'))
    return _save(actor, Measurement(created_by=actor, **fields), 'measurement.record')


def compare_measurements(left, right):
    keys = ('scenario', 'conditions_ref', 'initial_layout_ref', 'information_ref', 'provenance')
    reason = 'MATCHED_CONDITIONS'
    if any(getattr(left, key) != getattr(right, key) for key in keys):
        reason = 'CONDITIONS_DIFFER'
    elif not left.baseline_reach_verified or not right.baseline_reach_verified:
        reason = 'BASELINE_REACH_UNVERIFIED'
    elif left.method == right.method:
        reason = 'METHODS_MUST_DIFFER'
    comparable = reason == 'MATCHED_CONDITIONS'
    def row(name):
        a, b = getattr(left, name), getattr(right, name)
        return {'name': name, 'left': a, 'right': b, 'delta': b - a if comparable and a is not None and b is not None else None}
    return {'comparable': comparable, 'reason': reason, 'primary': [row('vehicle_transfers'), row('human_interventions')],
            'secondary': [row('elapsed_seconds')]}

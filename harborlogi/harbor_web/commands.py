"""Durable web intent and a single leased ledger writer. No physical proof producer."""
import atexit
from contextlib import closing
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from harbor.ownership import WriterLease
from harbor.process import ArmTransferBinding, bind_plan
from harbor.outbound import CargoMove, OutboundProposal
from harbor.rosy_http import RosyClient
from harbor.workflow import BusinessLedger
from harbor_web.models import Plan, Command, CommandFact, Audit, Observation
from harbor_web.services import _role, Conflict, queue_digest, constraints
from harbor_web.coordination import serialized

_registry = None
_guard = threading.RLock()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _config():
    values = [os.environ.get('HARBOR_' + name, '') for name in (
        'LEDGER_PATH', 'FLEET_ORIGIN', 'FLEET_TOKEN', 'FLEET_PRINCIPAL', 'BINDINGS_PATH')]
    if not all(values):
        raise ValidationError('EXECUTION_CONFIGURATION_REQUIRED')
    path, origin, token, principal, bindings = values
    RosyClient(origin, token)
    if len(principal) > 192 or not principal.strip() or any(ord(c) < 32 for c in principal):
        raise ValidationError('INVALID_FLEET_PRINCIPAL')
    return dict(path=Path(path).resolve(), origin=origin.rstrip('/'), token=token,
                principal=principal, bindings=bindings)


def _identity(config):
    return hashlib.sha256(str(config['path']).casefold().encode()).hexdigest()


def _bound(plan, config):
    with open(config['bindings'], encoding='utf-8') as source:
        bindings = tuple(ArmTransferBinding(**item) for item in json.load(source))
    proposal = OutboundProposal(plan.result['status'], plan.result['reason'], plan.result['source_revision'],
                               tuple(CargoMove(**move) for move in plan.result['moves']))
    return bind_plan(proposal, bindings)


@serialized
def capability(plan):
    reason, origin = '', None
    try:
        if Command.objects.filter(order_id=plan.order_id).exclude(plan=plan).exists():
            raise ValidationError('EXISTING_ORDER_EXECUTION_REQUIRES_RECONCILIATION')
        from harbor_web.runtime import execution_block_reason
        blocked = execution_block_reason()
        if blocked:
            raise ValidationError(blocked)
        config = _config()
        origin = config['origin']
        if plan.result.get('status') != 'READY':
            raise ValidationError('PLAN_NOT_READY')
        if plan.order.version != plan.order_version or plan.queue_digest != queue_digest():
            raise ValidationError('PLAN_QUEUE_CHANGED')
        if Observation.objects.filter(source_id=plan.observation.source_id, condition=plan.condition, pk__gt=plan.observation_id).exists():
            raise ValidationError('OBSERVATION_REVISION_CHANGED')
        age = timezone.now() - plan.observation.observed_at
        if not timezone.timedelta(0) <= age <= constraints(plan.condition.payload).max_observation_age:
            raise ValidationError('OBSERVATION_NOT_FRESH')
        host = urlsplit(origin).hostname
        try:
            loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not (settings.MODE in {'development', 'test'} and os.environ.get('HARBOR_WEB_ALLOW_SIMULATION') == '1'
                and loopback and plan.observation.provenance == 'SYNTHETIC'):
            raise ValidationError('AUTHENTICATED_OBSERVATION_AND_EVIDENCE_PRODUCER_REQUIRED')
        _bound(plan, config)
    except ValidationError as error:
        safe = {'EXISTING_ORDER_EXECUTION_REQUIRES_RECONCILIATION', 'RESTORE_RECONCILIATION_REQUIRED', 'DEPLOYMENT_PROFILE_REQUIRED',
                'EXECUTION_CONFIGURATION_REQUIRED', 'INVALID_FLEET_PRINCIPAL', 'PLAN_NOT_READY', 'PLAN_QUEUE_CHANGED',
                'OBSERVATION_NOT_FRESH', 'OBSERVATION_REVISION_CHANGED', 'AUTHENTICATED_OBSERVATION_AND_EVIDENCE_PRODUCER_REQUIRED'}
        reason = error.messages[0] if error.messages and error.messages[0] in safe else 'EXECUTION_INPUTS_UNAVAILABLE'
    except (ValueError, KeyError, TypeError, OSError):
        reason = 'EXECUTION_INPUTS_UNAVAILABLE'
    return dict(enabled=not reason, reason=reason, fleet_url=origin,
                requirements=['Fresh authenticated observation', 'Verified installed bindings', 'Independent physical evidence'])


class Registry:
    def __init__(self, config):
        self.config = config
        self.lease = WriterLease(config['path'])
        self.lease.__enter__()
        try:
            # These sources cannot authorize anything: no confirm/finish endpoint exists.
            self.ledger = BusinessLedger(config['path'], placement_sources=('web-unconfigured-placement',),
                                         retreat_sources=('web-unconfigured-retreat',))
            self.client = RosyClient(config['origin'], config['token'])
            self.client.expected_principal = config['principal']
            self.locks = {}
        except BaseException:
            self.lease.__exit__()
            raise

    def lock(self, key):
        with _guard:
            return self.locks.setdefault(str(key), threading.RLock())


def acquire_registry():
    global _registry
    with _guard:
        config = _config()
        if _registry is None:
            _registry = Registry(config)
        if _registry.config != config:
            raise Conflict('Execution identity changed; restart the owner.')
        try:
            _repair_pending_cancellations(_registry)
        except BaseException:
            _registry.lease.__exit__()
            _registry = None
            raise
        return _registry


def registry_close():
    global _registry
    with _guard:
        if _registry:
            _registry.lease.__exit__()
            _registry = None


atexit.register(registry_close)


def _key(command):
    return 'harbor:' + str(command.key)


def _validate(command, config):
    from harbor_web.runtime import ledger_identity_matches
    if (command.order_id != command.plan.order_id or not ledger_identity_matches(command.ledger_identity, _identity(config)) or command.fleet_origin != config['origin']
            or command.fleet_principal != config['principal'] or command.body_digest != _digest(command.binding)):
        raise Conflict('Saved command identity differs from configured execution owner.')


@serialized
def prepare_command(actor, *, plan_id, expected_version):
    _role(actor, 'operator')
    registry = acquire_registry()
    with registry.lock('plan:' + str(plan_id)):
        plan = Plan.objects.select_related('order', 'order__cargo', 'observation', 'condition').get(pk=plan_id)
        existing = Command.objects.filter(order=plan.order).first()
        if existing:
            _validate(existing, registry.config)
            if existing.plan_id != plan.pk:
                raise Conflict('Existing order execution requires reconciliation.')
            if existing.plan_version != expected_version:
                raise Conflict('Plan version changed.')
            return existing
        if plan.order_version != expected_version or plan.order.version != expected_version:
            raise Conflict('Plan version changed.')
        if not capability(plan)['enabled']:
            Audit.objects.create(actor=actor, action='command.blocked', object_type='Plan', object_id=str(plan.pk))
            raise ValidationError('Execution inputs unavailable.')
        bindings = _bound(plan, registry.config)
        body = dict(moves=[b.__dict__ for b in bindings], revision=plan.result['source_revision'],
                    outbound_id=plan.order.cargo.code, outbound_stand=constraints(plan.condition.payload).outbound_stand,
                    move_arms=[[m['primary_arm'], *m.get('alternate_arms', [])] for m in plan.result['moves']])
        with transaction.atomic():
            command = Command.objects.create(plan=plan, order=plan.order, actor=actor, plan_version=expected_version,
                ledger_identity=_identity(registry.config), fleet_origin=registry.config['origin'],
                fleet_principal=registry.config['principal'], binding=body, body_digest=_digest(body))
            Audit.objects.create(actor=actor, action='command.prepare', object_type='Command', object_id=str(command.pk))
    return command


def _prepare_ledger(command, registry):
    body = command.binding
    registry.ledger.prepare(_key(command), body['revision'], tuple(ArmTransferBinding(**m) for m in body['moves']),
        outbound_id=body['outbound_id'], outbound_stand=body['outbound_stand'],
        move_arms=tuple(tuple(a) for a in body['move_arms']))
    row = registry.ledger.read(_key(command))
    payload = json.loads(row['payload'])
    if payload != dict(command.binding, execution_kind='ARM_TRANSFER'):
        raise Conflict('Ledger binding differs from saved command.')


def _repair_pending_cancellations(registry):
    for command in Command.objects.filter(state='CANCEL_PENDING'):
        with registry.lock(command.pk):
            command.refresh_from_db()
            if command.state != 'CANCEL_PENDING':
                continue
            _validate(command, registry.config)
            _prepare_ledger(command, registry)
            if registry.ledger.read(_key(command))['state'] != 'HOLD':
                registry.ledger.hold(_key(command))


def _save(command, actor, state, reason=''):
    command.state, command.reason = state, reason
    command.version += 1
    with transaction.atomic():
        command.save(update_fields=['state', 'reason', 'version', 'mission_id', 'updated_at'])
        Audit.objects.create(actor=actor, action='command.' + state.lower(), object_type='Command', object_id=str(command.pk),
                             detail={'reason': reason, 'version': command.version})
    return command


def _record_fact(command, job, receipt):
    job_fields = ('action_kind', 'request_key', 'transfer', 'request_digest', 'mission_id', 'status',
                  'proposal_principal_id', 'resource_policy_digest', 'authority_epoch', 'dispatch_generation')
    receipt_fields = ('mission_id', 'workcell_id', 'instance_id', 'object_ref', 'source', 'target', 'policy_digest',
        'authority_epoch', 'dispatch_generation', 'physical_status', 'validated_at_ns', 'request_digest',
        'evidence_digest', 'receipt_id', 'action_id', 'attempt_id')
    CommandFact.objects.create(created_by=command.actor, command=command, command_key=command.key, body_digest=command.body_digest,
        fleet_origin=command.fleet_origin, fleet_principal=command.fleet_principal,
        job={k: job[k] for k in job_fields if k in job},
        receipt={k: receipt[k] for k in receipt_fields if k in receipt} if receipt else None, read_at=timezone.now())


@serialized
def _operation(actor, command_id, expected_version, action, reason=''):
    registry = acquire_registry()
    with registry.lock(command_id):
        _role(actor, 'operator')
        command = Command.objects.select_related('plan__order', 'plan__observation', 'plan__condition').get(pk=command_id)
        if command.version != expected_version:
            raise Conflict('Command changed; reload.')
        _validate(command, registry.config)
        key = _key(command)
        if action == 'submit':
            if command.state != 'PREPARED':
                raise Conflict('Explicit reconciliation required; submission cannot be retried.')
            gate = capability(command.plan)
            if not gate['enabled']:
                return _save(command, actor, 'BLOCKED', gate['reason'])
            current_bindings = [binding.__dict__ for binding in _bound(command.plan, registry.config)]
            if current_bindings != command.binding['moves']:
                return _save(command, actor, 'BLOCKED', 'INSTALLED_BINDINGS_CHANGED')
            _prepare_ledger(command, registry)
            _save(command, actor, 'DISPATCHING')
            try:
                command.mission_id = registry.ledger.propose(key, registry.client)
                job = registry.client.arm_request(key + ':0')['job']
                if job.get('proposal_principal_id') != command.fleet_principal:
                    raise ValueError('Principal mismatch')
            except Exception:
                registry.ledger.hold(key)
                return _save(command, actor, 'UNKNOWN', 'RECONCILIATION_REQUIRED')
            return _save(command, actor, 'RECONCILED')
        _prepare_ledger(command, registry)
        if action == 'cancel':
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 192 or any(ord(c) < 32 for c in reason):
                raise ValidationError('A short cancellation reason is required.')
            _save(command, actor, 'CANCEL_PENDING', 'CANCELLATION_REQUESTED')
            registry.ledger.hold(key)
            if command.mission_id:
                try:
                    registry.client.cancel_arm(command.mission_id, reason=reason)
                except Exception:
                    return _save(command, actor, 'CANCEL_PENDING', 'CANCELLATION_OUTCOME_UNKNOWN')
            return command
        try:
            row = registry.ledger.read(key)
            binding = ArmTransferBinding(**command.binding['moves'][row['step_index']])
            request_key = f"{key}:{row['step_index']}"
            job = binding.check(registry.client.arm_request(request_key)['job'], request_key)
            if job.get('proposal_principal_id') != command.fleet_principal:
                raise ValueError('Principal mismatch')
            if row['mission_id'] and job['mission_id'] != row['mission_id']:
                raise ValueError('Mission mismatch')
            receipt = None
            if job['status'] == 'GOAL_CONFIRMED':
                receipt = binding.check_receipt(job, registry.client.physical_receipt(job['mission_id'])['receipt'])
            if row['state'] == 'WAITING':
                registry.ledger.refresh(key, registry.client, now=timezone.now())
            else:
                registry.ledger.recover_arm(key, registry.client, now=timezone.now())
            command.mission_id = job['mission_id']
            _record_fact(command, job, receipt)
            state = 'CANCEL_PENDING' if command.state == 'CANCEL_PENDING' else 'RECONCILED'
            return _save(command, actor, state, 'BUSINESS_HOLD' if row['state'] == 'HOLD' else '')
        except Exception:
            registry.ledger.hold(key)
            return _save(command, actor, 'CANCEL_PENDING' if command.state == 'CANCEL_PENDING' else 'UNKNOWN', 'RECONCILIATION_REQUIRED')


def submit_command(actor, *, command_id, expected_version):
    return _operation(actor, command_id, expected_version, 'submit')


def reconcile_command(actor, *, command_id, expected_version):
    return _operation(actor, command_id, expected_version, 'reconcile')


def cancel_command(actor, *, command_id, expected_version, reason):
    return _operation(actor, command_id, expected_version, 'cancel', reason)


def command_detail(command):
    try:
        config = _config()
        _validate(command, config)
        uri = config['path'].as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT state,step_index,row_version,mission_id,goal_confirmed FROM business WHERE request_key=?', (_key(command),)).fetchone()
        fact = CommandFact.objects.filter(command=command, command_key=command.key, body_digest=command.body_digest,
            fleet_origin=command.fleet_origin, fleet_principal=command.fleet_principal).order_by('-read_at', '-pk').first()
        job, receipt = None, None
        if fact and row:
            binding = ArmTransferBinding(**command.binding['moves'][row['step_index']])
            job = binding.check(fact.job, f"{_key(command)}:{row['step_index']}")
            if job['mission_id'] != row['mission_id'] or job.get('proposal_principal_id') != command.fleet_principal:
                raise ValueError('Stored fact identity differs')
            receipt = binding.check_receipt(job, fact.receipt) if fact.receipt else None
        return dict(ledger=dict(row) if row else None, job=job, receipt=receipt, reason=command.reason)
    except (ValidationError, Conflict, ValueError, sqlite3.Error):
        return dict(ledger=None, job=None, receipt=None, reason='LEDGER_UNAVAILABLE')

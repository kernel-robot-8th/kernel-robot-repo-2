import uuid
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError


# Design 28: one ACTIVE command per order; terminal commands remain as history.
ACTIVE_COMMAND_STATES = ('PREPARED', 'DISPATCHING', 'RECONCILED', 'UNKNOWN', 'CANCEL_PENDING')
ACTIVE_OUTBOUND_DISPATCH_STATES = (
    'PICKUP_PLANNED', 'PICKUP_PROPOSED', 'PICKUP_ARRIVED', 'LOADED', 'EXIT_PROPOSED', 'HOLD'
)

# Named-person evidence sources (ADR 0023): the ledger (commands) and the producer (human_evidence) share these.
HUMAN_PLACEMENT_SOURCE = 'human-attested-placement'
HUMAN_RETREAT_SOURCE = 'human-attested-retreat'
FRESHNESS = timedelta(minutes=5)  # The ledger's human window.


class CommandQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs) - {'state', 'mission_id', 'reason', 'version', 'updated_at'}:
            raise ValidationError('Prepared command identity is immutable.')
        return super().update(**kwargs)

    def delete(self):
        raise ValidationError('Command history cannot be deleted.')


class Command(models.Model):
    objects = CommandQuerySet.as_manager()
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    plan = models.OneToOneField('Plan', on_delete=models.PROTECT)
    order = models.ForeignKey('Order', on_delete=models.PROTECT, related_name='commands')
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='superseded_by', editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    plan_version = models.PositiveIntegerField()
    ledger_identity = models.CharField(max_length=256)
    fleet_origin = models.CharField(max_length=256)
    fleet_principal = models.CharField(max_length=192)
    body_digest = models.CharField(max_length=64)
    binding = models.JSONField()
    state = models.CharField(max_length=16, default='PREPARED', choices=[(s, s) for s in (
        'PREPARED', 'DISPATCHING', 'RECONCILED', 'UNKNOWN', 'BLOCKED', 'CANCEL_PENDING', 'CANCELLED', 'COMPLETED')])
    mission_id = models.CharField(max_length=192, null=True)
    reason = models.CharField(max_length=192, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self._state.adding:
            kwargs['force_insert'] = True
        else:
            old = type(self).objects.get(pk=self.pk)
            immutable = ('key', 'plan_id', 'order_id', 'supersedes_id', 'actor_id', 'plan_version',
                         'ledger_identity', 'fleet_origin', 'fleet_principal', 'body_digest', 'binding')
            if any(getattr(old, name) != getattr(self, name) for name in immutable):
                raise ValidationError('Prepared command identity is immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Command history cannot be deleted.')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(plan_version__gt=0, version__gt=0), name='command_positive_versions'),
            models.UniqueConstraint(fields=('order',), condition=models.Q(state__in=ACTIVE_COMMAND_STATES),
                                    name='one_active_command_per_order'),
        ]


from harbor_web.model_base import Immutable


class CommandFact(Immutable):
    command = models.ForeignKey(Command, on_delete=models.PROTECT)
    command_key = models.UUIDField()
    body_digest = models.CharField(max_length=64)
    fleet_origin = models.CharField(max_length=256)
    fleet_principal = models.CharField(max_length=192)
    job = models.JSONField()
    receipt = models.JSONField(null=True)
    read_at = models.DateTimeField()


class EvidenceSubmission(Immutable):
    """One submitted placement/retreat observation pair, original identity preserved.

    The raw payloads are stored exactly as submitted; the adapter revalidates and binds
    them to the current business execution at consumption time (design 24/27 P2).
    """
    command = models.ForeignKey(Command, on_delete=models.PROTECT, related_name='evidence_submissions')
    placement = models.JSONField()
    retreat = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)



class HumanAttestation(Immutable):
    """A named field person's visual verdict on one business stage (ADR 0023).

    Not a measurement: it holds the ledger stage it was bound to (mission, attempt, step),
    the cargo and location the person saw, the arms they saw waiting outside the zone, and an
    optional note or photo reference. created_by/created_at are the attester and server time.
    """
    command = models.ForeignKey(Command, on_delete=models.PROTECT, related_name='human_attestations')
    phase = models.CharField(max_length=5, choices=[('step', 'step'), ('final', 'final')])
    step_index = models.PositiveIntegerField()
    mission_id = models.CharField(max_length=192)
    attempt_id = models.CharField(max_length=192)
    revision = models.PositiveIntegerField()
    cargo_id = models.CharField(max_length=96)
    location = models.CharField(max_length=96)
    arms = models.JSONField()
    note = models.CharField(max_length=300, blank=True)
    photo_ref = models.CharField(max_length=200, blank=True)


class CellPlanDispatch(models.Model):
    """Durable Harbor record for dual-mode OMX cell-plan requests (HARBOR_PLAN vs ROSY_DIRECTIVE)."""
    objects = models.Manager()
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey('Order', on_delete=models.PROTECT, related_name='cell_plan_dispatches')
    plan = models.ForeignKey('Plan', null=True, blank=True, on_delete=models.PROTECT, related_name='cell_plan_dispatches')
    observation = models.ForeignKey('Observation', on_delete=models.PROTECT)
    dispatch_mode = models.CharField(max_length=16, choices=[('HARBOR_PLAN', 'Harbor Plan'), ('ROSY_DIRECTIVE', 'ROSY Directive')])
    directive_mode = models.CharField(max_length=16)
    request_key = models.CharField(max_length=128, unique=True)
    request_payload = models.JSONField(default=dict)
    rosy_plan_id = models.CharField(max_length=96, blank=True, default='')
    order_source = models.CharField(max_length=16, choices=[('harbor', 'harbor'), ('rosy_r', 'rosy_r')])
    state = models.CharField(max_length=24, default='SUBMITTING')
    planned_steps = models.JSONField(default=list)
    rosy_task_ids = models.JSONField(default=list)
    reason = models.CharField(max_length=192, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self._state.adding:
            kwargs['force_insert'] = True
        else:
            old = type(self).objects.get(pk=self.pk)
            immutable = (
                'key', 'request_key', 'dispatch_mode', 'directive_mode',
                'order_source', 'order_id', 'observation_id',
            )
            if any(getattr(old, name) != getattr(self, name) for name in immutable):
                raise ValidationError('Cell plan dispatch request identity is immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Cell plan dispatch history cannot be deleted.')


class OutboundDispatchQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs) - {
            'state', 'reason', 'assigned_vehicle_id', 'pickup_mission_id',
            'exit_mission_id', 'loaded_at', 'loaded_by', 'loaded_by_id',
            'loaded_event_id', 'version', 'updated_at',
        }:
            raise ValidationError('Outbound dispatch routing identity is immutable.')
        return super().update(**kwargs)

    def delete(self):
        raise ValidationError('Outbound dispatch history cannot be deleted.')


class OutboundDispatch(models.Model):
    """Durable 2-leg ROSY Pinky pickup & exit coordinator for a completed OUTBOUND arm command or cell plan."""
    objects = OutboundDispatchQuerySet.as_manager()
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    command = models.OneToOneField(Command, on_delete=models.PROTECT, related_name='outbound_dispatch', null=True, blank=True)
    cell_plan_dispatch = models.OneToOneField(
        'CellPlanDispatch', on_delete=models.PROTECT, related_name='outbound_dispatch', null=True, blank=True
    )
    cargo = models.ForeignKey('Cargo', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    stand_zone_id = models.CharField(max_length=64)
    pickup_stop_id = models.CharField(max_length=64)
    exit_stop_id = models.CharField(max_length=64)
    site_places_digest = models.CharField(max_length=64)
    candidate_robot_ids = models.JSONField()
    state = models.CharField(
        max_length=20,
        default='PICKUP_PLANNED',
        choices=[(s, s) for s in (
            'PICKUP_PLANNED', 'PICKUP_PROPOSED', 'PICKUP_ARRIVED',
            'LOADED', 'EXIT_PROPOSED', 'EXITED', 'HOLD',
        )],
    )
    reason = models.CharField(max_length=192, blank=True)
    assigned_vehicle_id = models.CharField(max_length=64, blank=True)
    pickup_mission_id = models.CharField(max_length=192, blank=True)
    exit_mission_id = models.CharField(max_length=192, blank=True)
    loaded_at = models.DateTimeField(null=True, blank=True)
    loaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+'
    )
    loaded_event_id = models.CharField(max_length=64, blank=True, default='')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self._state.adding:
            kwargs['force_insert'] = True
        else:
            old = type(self).objects.get(pk=self.pk)
            immutable = (
                'key', 'command_id', 'cell_plan_dispatch_id', 'cargo_id', 'actor_id', 'stand_zone_id',
                'pickup_stop_id', 'exit_stop_id', 'site_places_digest', 'candidate_robot_ids',
            )
            if any(getattr(old, name) != getattr(self, name) for name in immutable):
                raise ValidationError('Outbound dispatch routing identity is immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Outbound dispatch history cannot be deleted.')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gt=0), name='outbound_dispatch_positive_version'),
            models.CheckConstraint(
                condition=(
                    models.Q(command__isnull=False, cell_plan_dispatch__isnull=True)
                    | models.Q(command__isnull=True, cell_plan_dispatch__isnull=False)
                ),
                name='outbound_dispatch_exactly_one_source',
            ),
            models.UniqueConstraint(
                fields=('stand_zone_id',),
                condition=models.Q(state__in=ACTIVE_OUTBOUND_DISPATCH_STATES),
                name='one_active_outbound_dispatch_per_stand',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(state__in=('LOADED', 'EXIT_PROPOSED', 'EXITED'))
                    | (
                        ~models.Q(assigned_vehicle_id='')
                        & (
                            models.Q(loaded_by__isnull=False, loaded_event_id='')
                            | (models.Q(loaded_by__isnull=True) & ~models.Q(loaded_event_id=''))
                        )
                    )
                ),
                name='outbound_dispatch_loaded_requires_vehicle_and_confirmation',
            ),
        ]

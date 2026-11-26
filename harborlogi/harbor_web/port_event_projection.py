"""Project applied ROSY container.stored port events into yard Observations (grid-placement-v1)."""
import copy
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from harbor.port_events import parse_slot_ref
from harbor_web.coordination import serialized
from harbor_web.models import Audit, Cargo, CargoFootprint, Condition, IntakeOccurrence, Observation
from harbor_web.site_config import covers_spatial_yard, map_areas, spatial_policy, spatial_snapshot

logger = logging.getLogger(__name__)

# shortcut: single Fleet source namespace; qualify with connection id if multi-R feeds one Harbor.
PORT_EVENT_SOURCE_ID = 'rosy-port-events'


class _ProjectionRejected(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = str(message)


def _latest_spatial_condition():
    for condition in Condition.objects.order_by('-pk'):
        payload = condition.payload
        if isinstance(payload, dict) and 'yard_layout' in payload and 'spatial_areas' in payload:
            return condition
    return None


def _record_rejection_once(event, code, message):
    event_id = event['event_id']
    if Audit.objects.filter(action='observation.port_event_rejected', object_id=event_id).exists():
        return
    Audit.objects.create(
        actor=None,
        action='observation.port_event_rejected',
        object_type='Intake',
        object_id=event_id,
        detail={
            'reason': code,
            'message': str(message)[:300],
            'object_ref': event.get('object_ref'),
            'slot_ref': event.get('slot_ref'),
            'observation_ref': event.get('observation_ref'),
        },
    )


@serialized
def project_stored_event(event, *, dispatch=None):
    """Apply one container.stored event onto the latest spatial Condition, returning new Observation or None."""
    with transaction.atomic():
        if Observation.objects.filter(source_id=PORT_EVENT_SOURCE_ID, source_revision=event['event_id']).exists():
            return None
        if dispatch is None and IntakeOccurrence.objects.filter(object_ref=event['object_ref']).exists():
            raise _ProjectionRejected('PLACEMENT_CORRELATION_MISSING', '회차가 있는 화물의 적재 증거에는 배치 시도가 필요합니다.')
        try:
            area_id, row, col, level = parse_slot_ref(event.get('slot_ref'))
        except ValueError as exc:
            raise _ProjectionRejected('INVALID_SLOT_REF', exc) from None
        condition = dispatch.observation.condition if dispatch else _latest_spatial_condition()
        if condition is None:
            raise _ProjectionRejected('SPATIAL_CONDITION_MISSING', '등록된 3x6 공간 설치 기준이 없습니다.')
        cargo = Cargo.objects.filter(code=event['object_ref']).first()
        if cargo is None:
            raise _ProjectionRejected('CARGO_NOT_REGISTERED', f"미등록 화물 코드: {event['object_ref']}")
        footprints = CargoFootprint.objects.filter(cargo=cargo)
        if dispatch:
            footprints = footprints.filter(pk=dispatch.plan.result['incoming_shape']['footprint_revision'])
        footprint = footprints.order_by('-pk').first()
        if footprint is None:
            raise _ProjectionRejected('CARGO_FOOTPRINT_MISSING', f"화물 크기(footprint) 미등록: {cargo.code}")
        policy = spatial_policy(condition.payload)
        if not policy.aligned(row, col, 0):
            raise _ProjectionRejected('SLOT_NOT_ALIGNED', f"슬롯 경계에 맞지 않는 위치: {event['slot_ref']}")

        snapshots = Observation.objects.filter(condition=condition)
        if dispatch:
            snapshots = snapshots.filter(provenance__in=('DECLARED', 'PORT_EVENT'))
        base = snapshots.order_by('-observed_at', '-pk').first() if dispatch else snapshots.order_by('-pk').first()
        if (dispatch and base is None) or (base and base.payload.get('schema') == 'grid-placement-v1' and not covers_spatial_yard(base)):
            raise _ProjectionRejected('INCOMPLETE_YARD_OBSERVATION', '한 자리의 관측으로 야드 전체를 확정할 수 없습니다.')
        if base is None:
            placements, holdings = [], []
        elif not isinstance(base.payload, dict) or base.payload.get('schema') != 'grid-placement-v1':
            raise _ProjectionRejected('NON_SPATIAL_OBSERVATION', '기준 야드 현황이 grid-placement-v1 형식이 아닙니다.')
        else:
            placements = copy.deepcopy(base.payload['placements'])
            holdings = copy.deepcopy(base.payload['holdings'])

        holdings = [h for h in holdings if h.get('cargo_id') != cargo.code]
        existing = next((p for p in placements if p.get('cargo_id') == cargo.code), None)
        if dispatch and base and base.observed_at > event['observed_at']:
            if (base.provenance in ('DECLARED', 'PORT_EVENT') and existing
                    and (existing['area_id'], existing['row'], existing['column'], existing['level']) == (area_id, row, col, level)):
                return None  # A later yard already independently shows the same placement.
            raise _ProjectionRejected('NEWER_YARD_OBSERVATION', '현재 관측이 배치 결과보다 늦습니다.')
        if existing is not None:
            same_slot = (
                existing.get('area_id') == area_id and existing.get('row') == row
                and existing.get('column') == col and existing.get('level') == level
            )
            if same_slot and not dispatch:
                return None
            if dispatch:
                placements.remove(existing)
            else:
                raise _ProjectionRejected('CARGO_ALREADY_PLACED', f"화물 {cargo.code}은(는) 이미 다른 위치에 배치되어 있습니다.")

        placements.append({
            'cargo_id': cargo.code,
            'area_id': area_id,
            'row': row,
            'column': col,
            'level': level,
            'orientation': 0,
            'footprint_revision': footprint.pk,
        })
        obs = Observation(
            created_by=None,
            source_id=PORT_EVENT_SOURCE_ID,
            source_revision=event['event_id'],
            observed_at=event['observed_at'],
            provenance='PORT_EVENT',
            condition=condition,
            payload={
                'schema': 'grid-placement-v1',
                'observed_areas': base.payload['observed_areas'] if base else [area.area_id for area in map_areas(condition.payload)],
                'placements': placements,
                'holdings': holdings,
            },
        )
        try:
            spatial_snapshot(obs)
            obs.full_clean()
        except (ValidationError, ValueError) as exc:
            raise _ProjectionRejected('SPATIAL_VALIDATION_FAILED', exc) from None
        obs.save()
        Audit.objects.create(
            actor=None,
            action='observation.port_event',
            object_type='Observation',
            object_id=str(obs.pk),
            detail={
                'event_id': event['event_id'],
                'observation_ref': event['observation_ref'],
                'object_ref': event['object_ref'],
                'slot_ref': event['slot_ref'],
                'identity': event['identity'],
                'base_observation_id': base.pk if base else None,
            },
        )
        return obs


def project_stored_events(ledger):
    """Project all applied container.stored events from the IntakeLedger into Yard Observations."""
    created = []
    for event in ledger.applied_stored_events():
        event_id = event['event_id']
        if Observation.objects.filter(source_id=PORT_EVENT_SOURCE_ID, source_revision=event_id).exists():
            continue
        if Audit.objects.filter(action='observation.port_event_rejected', object_id=event_id).exists():
            continue
        try:
            obs = project_stored_event(event)
            if obs is not None:
                created.append(obs)
        except _ProjectionRejected as exc:
            _record_rejection_once(event, exc.code, exc.message)
        except Exception:
            logger.exception('container.stored 이벤트(%s) 야드 반영 실패; 다음 틱에 재시도', event_id)
    return created

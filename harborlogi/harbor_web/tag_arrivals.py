"""Append-only EPC/barcode tag bindings and sequential physical arrival scans (never FIFO guessed)."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from harbor_web.coordination import serialized
from harbor_web.model_base import clean_text
from harbor_web.models import ArrivalEvent, Audit, Cargo, CargoTag
from harbor_web.service_common import _role

VALID_TAG_KINDS = {'EPC', 'BARCODE', 'QR'}


def _cleaned(raw, label):
    trimmed = (raw or '').strip()
    try:
        clean_text(trimmed)
    except ValidationError as exc:
        raise ValidationError(f'{label}에 올바른 값을 입력하세요.') from exc
    return trimmed


@serialized
@transaction.atomic
def bind_cargo_tag(actor, *, cargo_id, tag_value, tag_kind='EPC'):
    """Attach an immutable EPC/barcode/QR value to a registered container."""
    _role(actor, 'operator')
    kind = (tag_kind or 'EPC').strip().upper()
    if kind not in VALID_TAG_KINDS:
        raise ValidationError('태그 형식은 EPC, BARCODE, QR 중 하나여야 합니다.')
    clean_val = _cleaned(tag_value, '태그 값')
    if len(clean_val) > 128:
        raise ValidationError('태그 값은 128자 이하여야 합니다.')
    cargo = Cargo.objects.get(pk=cargo_id)
    existing = CargoTag.objects.filter(tag_value=clean_val).select_related('cargo').first()
    if existing is not None:
        if existing.cargo_id == cargo.pk:
            return existing
        raise ValidationError(f'태그 {clean_val}은(는) 이미 {existing.cargo.code}에 연결되어 있습니다.')
    tag = CargoTag.objects.create(created_by=actor, cargo=cargo, tag_kind=kind, tag_value=clean_val)
    Audit.objects.create(
        actor=actor,
        action='cargo.tag.bind',
        object_type='CargoTag',
        object_id=str(tag.pk),
        detail={'cargo_id': cargo.pk, 'cargo_code': cargo.code, 'tag_kind': kind, 'tag_value': clean_val},
    )
    return tag


def resolve_scan_target(raw_scan):
    """Resolve scanned text strictly by registered CargoTag or exact Cargo.code; never FIFO."""
    scan = _cleaned(raw_scan, '스캔 값')
    tag = CargoTag.objects.select_related('cargo').filter(tag_value=scan).first()
    if tag is not None:
        return tag.cargo, tag.tag_kind, tag.tag_value
    cargo = Cargo.objects.filter(code=scan).first()
    if cargo is not None:
        return cargo, 'CODE', cargo.code
    return None, 'EPC', scan


@serialized
@transaction.atomic
def record_arrival_scan(actor, *, raw_scan, intake_point='S1', vehicle_id='', observed_at=None, note=''):
    """Append one physical arrival scan record in arrival sequence and match known tags."""
    _role(actor, 'field', 'operator')
    cargo, tag_kind, tag_value = resolve_scan_target(raw_scan)
    point = (intake_point or 'S1').strip() or 'S1'
    if len(point) > 64:
        raise ValidationError('도착 지점 이름은 64자 이하여야 합니다.')
    veh = (vehicle_id or '').strip()
    if len(veh) > 64:
        raise ValidationError('차량 ID는 64자 이하여야 합니다.')
    memo = (note or '').strip()[:192]
    match_status = 'MATCHED' if cargo is not None else 'UNREGISTERED'
    stamp = observed_at or timezone.now()
    event = ArrivalEvent.objects.create(
        created_by=actor,
        cargo=cargo,
        tag_value=tag_value[:128],
        tag_kind=tag_kind,
        match_status=match_status,
        intake_point=point,
        vehicle_id=veh,
        observed_at=stamp,
        note=memo,
    )
    Audit.objects.create(
        actor=actor,
        action='arrival.record',
        object_type='ArrivalEvent',
        object_id=str(event.pk),
        detail={
            'tag_value': event.tag_value,
            'tag_kind': event.tag_kind,
            'match_status': event.match_status,
            'cargo_code': cargo.code if cargo else None,
            'intake_point': point,
            'vehicle_id': veh,
        },
    )
    return event, cargo

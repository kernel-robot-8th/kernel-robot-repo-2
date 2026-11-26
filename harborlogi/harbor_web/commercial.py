"""Operator declarations about a container and the goods on one inbound order."""
from hashlib import sha256

from django.core.exceptions import ValidationError
from django.db import transaction

from harbor_web.coordination import serialized
from harbor_web.models import Audit, Cargo, Order
from harbor_web.service_common import Conflict, _role


PROFILE_FIELDS = ('nominal_length_ft', 'container_type', 'color', 'color_confirmed')


def goods_digest(contents):
    return sha256(contents.encode('utf-8')).hexdigest()


@serialized
@transaction.atomic
def update_commercial(actor, cargo_id, expected_revision, *, nominal_length_ft, container_type, color, color_confirmed):
    _role(actor, 'operator')
    cargo = Cargo.objects.select_related('commercial_profile').get(pk=cargo_id)
    profile = cargo.current_commercial
    if profile.revision != expected_revision:
        raise Conflict('다른 작업자가 제원을 수정했습니다. 새로 불러오세요.')
    before = {name: getattr(profile, name) for name in PROFILE_FIELDS}
    after = dict(nominal_length_ft=nominal_length_ft, container_type=container_type, color=color,
                 color_confirmed=color_confirmed)
    for name, value in after.items():
        setattr(profile, name, value)
    profile.full_clean()
    if before == after:
        return profile
    profile.revision += 1
    profile.save()
    Audit.objects.create(actor=actor, action='cargo.commercial', object_type='Cargo', object_id=str(cargo.pk),
                         detail={'before': before, 'after': after, 'revision': profile.revision})
    return profile


@serialized
@transaction.atomic
def update_contents(actor, order_id, expected_digest, goods_contents):
    _role(actor, 'operator')
    order = Order.objects.get(pk=order_id)
    if order.kind != 'INBOUND':
        raise ValidationError('입고 요청에만 품목을 기록할 수 있습니다.')
    before = order.goods_contents
    if goods_digest(before) != expected_digest:
        raise Conflict('다른 작업자가 품목을 수정했습니다. 새로 불러오세요.')
    order.goods_contents = Order._meta.get_field('goods_contents').clean(goods_contents, order)
    if before != order.goods_contents:
        order.save(update_fields=['goods_contents'])
        Audit.objects.create(actor=actor, action='order.goods', object_type='Order', object_id=str(order.pk),
                             detail={'before': before, 'after': order.goods_contents})
    return order

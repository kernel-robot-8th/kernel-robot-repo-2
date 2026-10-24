"""Start an isolated, loopback-only product preview with private generated access."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'runs/product-preview'
    folder.mkdir(parents=True, exist_ok=True)
    access = folder / 'access.json'
    if not access.exists():
        values = dict(secret=secrets.token_urlsafe(64), username='harbor-preview', password=secrets.token_urlsafe(24))
        descriptor = os.open(access, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(values, stream)
    else:
        values = json.loads(access.read_text(encoding='utf-8'))
    os.environ.update(HARBOR_WEB_MODE='development', HARBOR_WEB_SECRET_KEY=values['secret'], HARBOR_WEB_DATABASE=str(folder / 'product.sqlite3'))
    # The preview never inherits an operational Fleet credential or ledger path.
    for name in ('LEDGER_PATH', 'FLEET_ORIGIN', 'FLEET_TOKEN', 'FLEET_PRINCIPAL', 'BINDINGS_PATH', 'WEB_ALLOW_SIMULATION'):
        os.environ.pop('HARBOR_' + name, None)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'harbor_web.settings')
    os.chdir(root)
    sys.path.insert(0, str(root))
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.utils import timezone
    from harbor_web import services
    from harbor_web.models import Cargo, Condition, FieldEpisode, Order
    from harbor.ownership import WriterLease
    with WriterLease(folder / 'product.sqlite3'):
        call_command('migrate', interactive=False, verbosity=0)
        call_command('bootstrap_roles', verbosity=0)
        user, created = get_user_model().objects.get_or_create(username=values['username'], defaults={'is_staff': True})
        if created:
            user.set_password(values['password'])
            user.save()
            user.groups.add(*Group.objects.filter(name__in=['viewer', 'operator', 'field', 'admin']))
        for code, name in [('C1', '우선 반출 화물'), ('C2', '상단 재취급 화물'), ('C3', '공급 인계 확인 화물')]:
            if not Cargo.objects.filter(code=code).exists():
                services.register_cargo(user, code=code, name=name, weight_kg=2, volume_m3='0.012', destination='반출대 · 합성 시험')
        condition = Condition.objects.filter(name='합성 야드 · 화면 검토용').first()
        if condition is None:
            condition = services.register_condition(user, name='합성 야드 · 화면 검토용', calibration_ref='synthetic-only-not-field-calibration', payload=dict(outbound_stand='반출대', temporary_bays=['임시 칸'], allowed_moves=[['보관 칸', '임시 칸'], ['임시 칸', '보관 칸'], ['보관 칸', '반출대']], max_observation_age_seconds=1800, conveyor='컨베이어', arm_reach=[['A', '보관 칸'], ['A', '임시 칸'], ['A', '반출대']]))
        now = timezone.now()
        observation = services.record_observation(user, source_id='synthetic-preview-camera', source_revision=now.isoformat(), observed_at=now, provenance='SYNTHETIC', condition=condition, payload=dict(bays=[dict(bay_id='보관 칸', cargo=['C1', 'C2'], capacity=3), dict(bay_id='임시 칸', cargo=[], capacity=2), dict(bay_id='반출대', cargo=[], capacity=1)], holdings=[]))
        order = Order.objects.filter(cargo__code='C1', kind='OUTBOUND').first()
        if order is None:
            order = services.create_order(user, cargo=Cargo.objects.get(code='C1'), kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED', note='변경된 반출 지시를 검토하는 합성 사례')
        services.make_plan(user, order_id=order.pk, expected_version=order.version, observation_id=observation.pk)
        inbound = Order.objects.filter(cargo__code='C3', kind='INBOUND').first()
        if inbound is None:
            inbound = services.create_order(user, cargo=Cargo.objects.get(code='C3'), kind='INBOUND', priority_group=9, sequence=1, tie_policy='FIXED', note='현장 확인 화면 검토용 합성 요청')
        if not FieldEpisode.objects.filter(order=inbound, state='OPEN').exists():
            services.assign_episode(user, order_id=inbound.pk, vehicle_id='합성 차량 P1', assigned_to=user)
    print('Product preview: http://127.0.0.1:8060/ · synthetic data; Fleet disconnected.', flush=True)
    print('Private access file: runs/product-preview/access.json (do not publish).', flush=True)
    return subprocess.call([sys.executable, '-m', 'harbor_web', '--port', '8060'], cwd=root, env=os.environ)


if __name__ == '__main__':
    raise SystemExit(main())

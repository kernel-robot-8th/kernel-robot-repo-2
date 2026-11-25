"""Seed a realistic 3x6x3 site-matched sample dataset (C-1..C-20, EPC tags, shippers, voyages, arrivals)."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from harbor.ownership import WriterLease
from harbor_web import services, tag_arrivals
from harbor_web.models import ArrivalEvent, Cargo, CargoFootprint, CargoTag, Condition, Observation, Order
from harbor_web.runtime_models import EvidenceProducerSetting

SEED_CONTAINERS = [
    # Pre-arrived in yard (2 stacked columns creating realistic rehandling vs voyage cutoff trade-offs)
    ('C-1', '부산행 냉동 컨테이너 1', '한진특송', 1, 'V-BUSAN-01', 2, (1, 1, 1), '3034257BF7194E4000000001'),
    ('C-2', '도쿄행 정밀부품 컨테이너 2', '태평양무역', 3, 'V-LA-03', 18, (1, 1, 2), '3034257BF7194E4000000002'),
    ('C-3', '부산행 긴급 선적 3', '대한물산', 2, 'V-BUSAN-01', 2, (1, 4, 1), '3034257BF7194E4000000003'),
    ('C-4', '도쿄행 일반 화물 4', '대한물산', 2, 'V-TOKYO-02', 7, (1, 4, 2), '3034257BF7194E4000000004'),
    # Sequential arrivals awaiting EPC/barcode scan & intake
    ('C-5', '부산행 후속 입하 5', '한진특송', 1, 'V-BUSAN-01', 3, None, '3034257BF7194E4000000005'),
    ('C-6', '도쿄행 후속 입하 6', '대한물산', 2, 'V-TOKYO-02', 8, None, '3034257BF7194E4000000006'),
    ('C-7', 'LA행 일반 입하 7', '태평양무역', 3, 'V-LA-03', 20, None, '3034257BF7194E4000000007'),
    ('C-8', '부산행 추가 입하 8', '한진특송', 1, 'V-BUSAN-01', 3, None, '3034257BF7194E4000000008'),
    # More of the yard (12 of 18 places) so the board, yard and plans show a working shift, not one example.
    ('C-9', 'LA행 일반 화물 9', '태평양무역', 3, 'V-LA-03', 22, (1, 1, 3), '3034257BF7194E4000000009'),
    ('C-10', '도쿄행 일반 화물 10', '대한물산', 2, 'V-TOKYO-02', 9, (2, 1, 1), '3034257BF7194E4000000010'),
    ('C-11', 'LA행 일반 화물 11', '태평양무역', 3, 'V-LA-03', 24, (2, 1, 2), '3034257BF7194E4000000011'),
    ('C-12', '도쿄행 일반 화물 12', '대한물산', 2, 'V-TOKYO-02', 10, (2, 4, 1), '3034257BF7194E4000000012'),
    ('C-13', 'LA행 일반 화물 13', '태평양무역', 3, 'V-LA-03', 26, (3, 1, 1), '3034257BF7194E4000000013'),
    ('C-14', '부산행 일반 화물 14', '한진특송', 2, 'V-BUSAN-01', 4, (3, 1, 2), '3034257BF7194E4000000014'),
    ('C-15', 'LA행 일반 화물 15', '태평양무역', 3, 'V-LA-03', 28, (3, 1, 3), '3034257BF7194E4000000015'),
    ('C-16', '도쿄행 일반 화물 16', '대한물산', 2, 'V-TOKYO-02', 11, (3, 4, 1), '3034257BF7194E4000000016'),
    # More sequential arrivals (inbound examples).
    ('C-17', '도쿄행 후속 입하 17', '대한물산', 2, 'V-TOKYO-02', 12, None, '3034257BF7194E4000000017'),
    ('C-18', '부산행 긴급 입하 18', '한진특송', 1, 'V-BUSAN-01', 5, None, '3034257BF7194E4000000018'),
    ('C-19', 'LA행 후속 입하 19', '태평양무역', 3, 'V-LA-03', 30, None, '3034257BF7194E4000000019'),
    ('C-20', '부산행 후속 입하 20', '한진특송', 2, 'V-BUSAN-01', 6, None, '3034257BF7194E4000000020'),
]


class Command(BaseCommand):
    help = 'Seed realistic 3x6x3 Harbor sample dataset (C-1..C-20, EPC tags, shippers, voyages, sequential arrivals).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--provenance',
            default='DECLARED',
            choices=['DECLARED', 'SYNTHETIC'],
            help='Observation provenance for the seeded initial yard snapshot (default: DECLARED).',
        )

    def handle(self, *args, **options):
        provenance = options['provenance']
        if provenance == 'SYNTHETIC' and settings.MODE == 'live':
            raise CommandError('SYNTHETIC sample observation is forbidden in live mode; use --provenance DECLARED.')

        config_root = Path(__file__).resolve().parents[3] / 'config'
        layout = json.loads((config_root / 'yard-layout-3x6.json').read_text(encoding='utf-8'))
        policy = json.loads((config_root / 'spatial-policy-3x6.json').read_text(encoding='utf-8'))
        policy['yard_layout'] = layout

        call_command('bootstrap_roles', verbosity=0)
        db_path = settings.DATABASES['default'].get('NAME')
        lease_target = (
            Path(db_path) if isinstance(db_path, (str, Path)) and str(db_path) != ':memory:'
            else Path(tempfile.gettempdir()) / 'harbor_seed_site_dataset.lock'
        )
        lease_target.parent.mkdir(parents=True, exist_ok=True)
        with WriterLease(lease_target), transaction.atomic():
            User = get_user_model()
            admin, _ = User.objects.get_or_create(username='seed_admin', defaults={'is_staff': True})
            op, _ = User.objects.get_or_create(username='seed_operator')
            field_user, _ = User.objects.get_or_create(username='seed_field')
            admin.groups.add(*Group.objects.filter(name__in=['admin', 'operator', 'field', 'viewer']))
            op.groups.add(*Group.objects.filter(name__in=['operator', 'viewer']))
            field_user.groups.add(*Group.objects.filter(name__in=['field', 'viewer']))

            if provenance == 'DECLARED':
                from harbor_web.fleet_connection import set_human_confirmation
                setting = EvidenceProducerSetting.objects.first()
                if setting is None or not setting.human_confirmation:
                    set_human_confirmation(admin, enabled=True, expected_version=setting.version if setting else 0)

            cond_name = '3×6×3 현장 표준 배치 기준 (시드)'
            condition = Condition.objects.filter(name=cond_name).order_by('-pk').first()
            if condition is None or condition.payload != policy:
                condition = services.register_condition(
                    admin,
                    name=cond_name,
                    calibration_ref='site-3x6-canonical-seed-v1',
                    payload=policy,
                )

            now = timezone.now()
            placements = []
            for code, name, shipper, s_tier, voyage, hours_until, slot, epc in SEED_CONTAINERS:
                cargo = Cargo.objects.filter(code=code).first()
                if cargo is None:
                    cargo = services.register_cargo(
                        op,
                        code=code,
                        name=name,
                        weight_kg=2,
                        volume_m3='0.012',
                        destination=f'{voyage} 선적',
                        shipper_name=shipper,
                        shipper_tier=s_tier,
                        length_cells=3,
                    )
                if not CargoTag.objects.filter(tag_value=epc).exists():
                    tag_arrivals.bind_cargo_tag(op, cargo_id=cargo.pk, tag_value=epc, tag_kind='EPC')
                footprint = CargoFootprint.objects.filter(cargo=cargo).order_by('-pk').first()
                cutoff = now + timedelta(hours=hours_until)
                if slot is not None:
                    row, column, level = slot
                    placements.append(dict(
                        cargo_id=code, area_id='yard', row=row, column=column, level=level,
                        orientation=0, footprint_revision=footprint.pk,
                    ))
                    if not ArrivalEvent.objects.filter(cargo=cargo).exists():
                        tag_arrivals.record_arrival_scan(
                            field_user, raw_scan=epc, intake_point='S1', vehicle_id='rosy_42',
                            observed_at=now - timedelta(minutes=30), note='시드 초기 입하 완료',
                        )
                    if not Order.objects.filter(cargo=cargo, kind='OUTBOUND').exists():
                        group, seq = services.back_of_tier(s_tier, 'OUTBOUND')
                        services.create_order(
                            op, cargo=cargo, kind='OUTBOUND', priority_group=group, sequence=seq,
                            tie_policy='FIXED', title=f'{voyage} 반출 ({code})',
                            voyage_code=voyage, cutoff_at=cutoff,
                            note=f'{shipper} · {voyage}',
                        )
                else:
                    if not Order.objects.filter(cargo=cargo, kind='INBOUND').exists():
                        group, seq = services.back_of_tier(s_tier, 'INBOUND')
                        services.create_order(
                            op, cargo=cargo, kind='INBOUND', priority_group=group, sequence=seq,
                            tie_policy='FIXED', title=f'{voyage} 순차 입하 ({code})',
                            voyage_code=voyage, cutoff_at=cutoff,
                            note=f'{shipper} · 스캔 대기',
                        )

            seed_payload = dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                placements=placements,
                holdings=[],
            )
            latest_seed_obs = Observation.objects.filter(
                source_id='seed-site-dataset', provenance=provenance, condition=condition,
            ).order_by('-pk').first()
            if latest_seed_obs is None or latest_seed_obs.payload != seed_payload:
                services.record_observation(
                    admin,
                    source_id='seed-site-dataset',
                    source_revision=f'seed-v1-{provenance.lower()}-{now.isoformat()}',
                    observed_at=now,
                    provenance=provenance,
                    condition=condition,
                    payload=seed_payload,
                )
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(SEED_CONTAINERS)} containers (C-1..C-20) with {provenance} yard snapshot.'
        ))

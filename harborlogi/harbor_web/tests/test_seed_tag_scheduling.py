"""Tests for site-matched seed dataset, EPC/barcode tag arrival scans, and shipper/voyage scheduling."""
from datetime import timedelta
from decimal import Decimal
import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from harbor_web import scheduling, services, tag_arrivals
from harbor_web.models import ArrivalEvent, Cargo, CargoTag, Condition, Observation, Order


class SeedTagSchedulingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        call_command('bootstrap_roles', verbosity=0)
        self.op = User.objects.create_user(username='op_sched', password='pw')
        self.field = User.objects.create_user(username='field_sched', password='pw')
        self.op.groups.add(Group.objects.get(name='operator'), Group.objects.get(name='viewer'))
        self.field.groups.add(Group.objects.get(name='field'), Group.objects.get(name='viewer'))

    def test_seed_site_dataset_declared_is_idempotent_and_live_forbids_synthetic(self):
        first_out = io.StringIO()
        call_command('seed_site_dataset', provenance='DECLARED', stdout=first_out)
        self.assertIn('새로 만든 화물 22개, 새 입고 요청 10건', first_out.getvalue())
        self.assertEqual(Cargo.objects.filter(code__startswith='C-').count(), 22)
        self.assertEqual(CargoTag.objects.count(), 22)
        self.assertEqual(ArrivalEvent.objects.filter(match_status='MATCHED').count(), 12)
        self.assertEqual(Observation.objects.filter(provenance='DECLARED').count(), 1)
        self.assertEqual(Order.objects.filter(kind='OUTBOUND').count(), 12)
        self.assertEqual(Order.objects.filter(kind='INBOUND').count(), 10)

        # Second invocation stays idempotent without duplicating cargos, tags, arrivals, or orders.
        second_out = io.StringIO()
        call_command('seed_site_dataset', provenance='DECLARED', stdout=second_out)
        self.assertIn('새로 만든 화물 0개, 새 입고 요청 0건', second_out.getvalue())
        self.assertEqual(Cargo.objects.filter(code__startswith='C-').count(), 22)
        self.assertEqual(CargoTag.objects.count(), 22)
        self.assertEqual(ArrivalEvent.objects.count(), 12)
        self.assertEqual(Order.objects.filter(kind='OUTBOUND').count(), 12)
        self.assertEqual(Order.objects.filter(kind='INBOUND').count(), 10)

        with override_settings(MODE='live'), self.assertRaises(CommandError):
            call_command('seed_site_dataset', provenance='SYNTHETIC', verbosity=0)

    def test_seed_reports_new_cargo_and_inbound_orders_without_modifying_existing_cargo(self):
        # Existing C-1..C-20 (e.g. older seed) stay untouched; C-21 and C-22 add 2 cargos and 2 INBOUND orders.
        for idx in range(1, 21):
            cargo = services.register_cargo(
                self.op, code=f'C-{idx}', name=f'기존 화물 {idx}', weight_kg=Decimal('2.000'),
                volume_m3=Decimal('0.0120'), destination='기존 목적지', color='', length_cells=3,
            )
            kind = 'OUTBOUND' if idx in {1, 2, 3, 4, 9, 10, 11, 12, 13, 14, 15, 16} else 'INBOUND'
            group, seq = services.back_of_tier(3, kind)
            services.create_order(
                self.op, cargo=cargo, kind=kind, priority_group=group, sequence=seq, tie_policy='FIXED',
            )
        out = io.StringIO()
        call_command('seed_site_dataset', provenance='DECLARED', stdout=out)
        self.assertIn('새로 만든 화물 2개, 새 입고 요청 2건', out.getvalue())
        # Existing C-1 was not overwritten; C-21 and C-22 got their seeded colors and INBOUND orders.
        self.assertEqual(Cargo.objects.get(code='C-1').color, '')
        self.assertEqual(Cargo.objects.get(code='C-21').color, 'pink')
        self.assertEqual(Cargo.objects.get(code='C-22').color, 'pink')
        self.assertTrue(Order.objects.filter(cargo__code='C-21', kind='INBOUND').exists())
        self.assertTrue(Order.objects.filter(cargo__code='C-22', kind='INBOUND').exists())

    def test_seed_container_colors_and_confirmation_match_physical_record(self):
        call_command('seed_site_dataset', provenance='DECLARED', verbosity=0)
        by_code = {c.code: (c.color, c.color_confirmed) for c in Cargo.objects.filter(code__startswith='C-')}
        self.assertEqual(len(by_code), 22)
        for idx in range(1, 6):
            self.assertEqual(by_code[f'C-{idx}'], ('pink', True))
        for idx in range(6, 14):
            self.assertEqual(by_code[f'C-{idx}'], ('orange', True))
        for idx in range(14, 18):
            self.assertEqual(by_code[f'C-{idx}'], ('sky', False))
        for idx in range(18, 21):
            self.assertEqual(by_code[f'C-{idx}'], ('yellow', False))
        for idx in range(21, 23):
            self.assertEqual(by_code[f'C-{idx}'], ('pink', False))
        self.assertEqual({color for color, _ in by_code.values()}, {'pink', 'orange', 'sky', 'yellow'})

    def test_epc_barcode_tag_binding_and_sequential_arrival_scan_never_guesses_fifo(self):
        c1 = services.register_cargo(
            self.op, code='C-91', name='Test C-91', weight_kg=Decimal('2.000'),
            volume_m3=Decimal('0.0120'), destination='부산', shipper_name='한진특송',
            shipper_tier=1, length_cells=3, tag_value='EPC-91', tag_kind='EPC',
        )
        c2 = services.register_cargo(
            self.op, code='C-92', name='Test C-92', weight_kg=Decimal('2.000'),
            volume_m3=Decimal('0.0120'), destination='도쿄', length_cells=3,
        )
        self.assertEqual(c1.tags.get().tag_value, 'EPC-91')

        with self.assertRaises(ValidationError):
            tag_arrivals.bind_cargo_tag(self.op, cargo_id=c2.pk, tag_value='EPC-91', tag_kind='EPC')

        ev1, matched1 = tag_arrivals.record_arrival_scan(
            self.field, raw_scan='EPC-91', intake_point='S1', vehicle_id='rosy_42',
        )
        self.assertEqual((ev1.match_status, matched1, ev1.tag_kind), ('MATCHED', c1, 'EPC'))

        # Unregistered tag stays UNREGISTERED rather than guessing C-92 by FIFO order.
        ev_unknown, matched_unknown = tag_arrivals.record_arrival_scan(
            self.field, raw_scan='EPC-UNKNOWN-999', intake_point='S1', vehicle_id='rosy_45',
        )
        self.assertIsNone(matched_unknown)
        self.assertEqual(ev_unknown.match_status, 'UNREGISTERED')

        self.client.force_login(self.op)
        resp_bind = self.client.post(f'/cargo/{c2.pk}/tag/', {'tag_kind': 'BARCODE', 'tag_value': 'BAR-92'})
        self.assertEqual(resp_bind.status_code, 302)
        self.assertEqual(c2.tags.get().tag_value, 'BAR-92')

        self.client.force_login(self.field)
        resp_scan = self.client.post('/field/scan/', {'raw_scan': 'BAR-92', 'intake_point': 'S3', 'vehicle_id': 'rosy_46'})
        self.assertEqual(resp_scan.status_code, 302)
        ev2 = ArrivalEvent.objects.order_by('-pk').first()
        self.assertEqual((ev2.cargo, ev2.match_status), (c2, 'MATCHED'))

    def test_shipper_and_voyage_scheduling_compiles_into_3_tier_queue(self):
        call_command('seed_site_dataset', provenance='DECLARED', verbosity=0)
        # Scramble C-2 (general LA +18h) ahead of C-1 (urgent Busan +2h).
        c2_order = Order.objects.get(cargo__code='C-2', kind='OUTBOUND')
        services.move_outbound_to_front(self.op, c2_order)

        proposal = scheduling.propose_outbound_schedule()
        codes = [row['order'].cargo.code for row in proposal['preview_rows']]
        # Urgent Busan (C-1, C-3, +2h) first, then by voyage cutoff: C-14 +4h, C-4 +7h, ... LA C-15 +28h.
        self.assertEqual(set(codes[:2]), {'C-1', 'C-3'})
        self.assertEqual(codes[2:], ['C-14', 'C-4', 'C-10', 'C-12', 'C-16', 'C-2', 'C-9', 'C-11', 'C-13', 'C-15'])

        self.client.force_login(self.op)
        resp = self.client.post('/outbound/schedule/apply/')
        self.assertEqual(resp.status_code, 302)

        saved_codes = [
            o.cargo.code
            for o in Order.objects.filter(kind='OUTBOUND').select_related('cargo').order_by('priority_group', 'sequence')
        ]
        self.assertEqual(set(saved_codes[:2]), {'C-1', 'C-3'})
        self.assertEqual(saved_codes[2:], ['C-14', 'C-4', 'C-10', 'C-12', 'C-16', 'C-2', 'C-9', 'C-11', 'C-13', 'C-15'])

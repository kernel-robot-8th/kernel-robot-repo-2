"""Tests for Cargo color/confirmation fields, form validation, detail display, scene metadata, and migration rollback."""
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from harbor.cargo_colors import CARGO_COLORS, CARGO_COLOR_CHOICES, CARGO_COLOR_LABELS
from harbor_web import forms, services
from harbor_web.models import Cargo, CargoFootprint
from harbor_web.tests.test_control_board import site_policy
from harbor_web.yard_scene import attach_cargo_metadata, present_stage, scene_for
from harbor_web.yard_view import spatial_yard_view


class CargoColorTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('col-admin')
        self.op = User.objects.create_user('col-op')
        self.admin.groups.add(Group.objects.get_or_create(name='admin')[0], Group.objects.get_or_create(name='viewer')[0])
        self.op.groups.add(Group.objects.get_or_create(name='operator')[0], Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(self.op)

    def test_cargo_form_accepts_valid_colors_and_blank_and_rejects_invalid(self):
        base = dict(
            name='테스트 화물',
            weight_kg='2.000',
            volume_m3='0.0120',
            destination='부산',
            length_cells=3,
        )
        for idx, color in enumerate(('', 'orange', 'pink', 'yellow', 'sky'), start=1):
            form = forms.CargoForm(data={**base, 'code': f'CF-{idx}', 'color': color})
            self.assertTrue(form.is_valid(), f'color={color!r} errors={form.errors}')
        for bad in ('red', 'green', 'blue', '주황'):
            form = forms.CargoForm(data={**base, 'code': f'BAD-{bad}', 'color': bad})
            self.assertFalse(form.is_valid())
            self.assertIn('color', form.errors)

    def test_register_cargo_rejects_unknown_color(self):
        with self.assertRaises(ValidationError):
            services.register_cargo(
                self.op,
                code='BAD-SVC',
                name='Bad',
                weight_kg=Decimal('2.000'),
                volume_m3=Decimal('0.0120'),
                destination='부산',
                color='purple',
                length_cells=3,
            )

    def test_cargo_list_and_history_show_color_unconfirmed_flag_and_metadata(self):
        confirmed = services.register_cargo(
            self.op, code='DET-1', name='확인 화물', weight_kg=Decimal('2.000'), volume_m3=Decimal('0.0120'),
            destination='부산 신항', color='pink', color_confirmed=True,
            shipper_name='한진특송', shipper_tier=1, length_cells=3,
        )
        planned = services.register_cargo(
            self.op, code='DET-2', name='예정 화물', weight_kg=Decimal('2.000'), volume_m3=Decimal('0.0120'),
            destination='도쿄항', color='sky', color_confirmed=False,
            shipper_name='대한물산', shipper_tier=2, length_cells=3,
        )
        services.create_order(
            self.op, cargo=planned, kind='OUTBOUND', priority_group=2, sequence=1, tie_policy='FIXED',
            voyage_code='V-TOKYO-09', cutoff_at=timezone.now(),
        )

        list_html = self.client.get('/cargo/').content.decode()
        self.assertIn('분홍', list_html)
        self.assertIn('하늘', list_html)
        self.assertIn('색 확인 전', list_html)
        self.assertIn('V-TOKYO-09', list_html)

        confirmed_html = self.client.get(f'/cargo/{confirmed.pk}/history/').content.decode()
        self.assertIn('분홍', confirmed_html)
        self.assertNotIn('색 확인 전', confirmed_html)
        self.assertIn('한진특송', confirmed_html)
        self.assertIn('부산 신항', confirmed_html)

        planned_html = self.client.get(f'/cargo/{planned.pk}/history/').content.decode()
        self.assertIn('하늘', planned_html)
        self.assertIn('색 확인 전', planned_html)
        self.assertIn('대한물산', planned_html)
        self.assertIn('V-TOKYO-09', planned_html)

    def test_scene_metadata_uses_open_order_priority_then_shipper_tier_then_neutral(self):
        condition = services.register_condition(
            self.admin, name='3x6', calibration_ref='syn', payload=site_policy(),
        )
        c_order = services.register_cargo(
            self.op, code='M-ORD', name='M-ORD', weight_kg=2, volume_m3='0.012',
            destination='부산', color='orange', color_confirmed=True, shipper_tier=3, length_cells=3,
        )
        services.create_order(
            self.op, cargo=c_order, kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED',
            voyage_code='V-1',
        )
        c_shipper = services.register_cargo(
            self.op, code='M-SHP', name='M-SHP', weight_kg=2, volume_m3='0.012',
            destination='도쿄', color='yellow', color_confirmed=False, shipper_tier=2, length_cells=3,
        )
        c_none = services.register_cargo(
            self.op, code='M-NON', name='M-NON', weight_kg=2, volume_m3='0.012',
            destination='LA', color='', length_cells=3,
        )
        obs = services.record_observation(
            self.admin, source_id='cam', source_revision='1', observed_at=timezone.now(),
            provenance='SYNTHETIC', condition=condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                holdings=[],
                placements=[
                    dict(cargo_id=c_order.code, area_id='yard', row=1, column=1, level=1, orientation=0,
                         footprint_revision=CargoFootprint.objects.get(cargo=c_order).pk),
                    dict(cargo_id=c_shipper.code, area_id='yard', row=2, column=1, level=1, orientation=0,
                         footprint_revision=CargoFootprint.objects.get(cargo=c_shipper).pk),
                    dict(cargo_id=c_none.code, area_id='yard', row=3, column=1, level=1, orientation=0,
                         footprint_revision=CargoFootprint.objects.get(cargo=c_none).pk),
                ],
            ),
        )
        stage = present_stage(dict(spatial_yard_view(services.spatial_snapshot(obs)), index=0, label='현재'), condition.payload)
        scene = attach_cargo_metadata(scene_for([stage]))
        boxes = {b['id']: b for b in scene['frames'][0]['boxes']}
        self.assertEqual(scene['cargo_colors'], list(CARGO_COLORS))
        self.assertEqual(list(Cargo._meta.get_field('color').choices), list(CARGO_COLOR_CHOICES))
        self.assertEqual(list(forms.CargoForm().fields['color'].choices), [('', '---------'), *CARGO_COLOR_CHOICES])
        self.assertEqual(boxes['M-ORD']['color_label'], CARGO_COLOR_LABELS['orange'])
        self.assertEqual((boxes['M-ORD']['color'], boxes['M-ORD']['priority_tier']), ('orange', 'urgent'))
        self.assertEqual((boxes['M-SHP']['color'], boxes['M-SHP']['priority_tier']), ('yellow', 'priority'))
        self.assertEqual((boxes['M-NON']['color'], boxes['M-NON']['priority_tier']), ('', 'neutral'))


class CargoColorMigrationTests(TransactionTestCase):
    def test_migration_0026_is_reversible_and_defaults_existing_rows_safely(self):
        executor = MigrationExecutor(connection)
        old_target = [('harbor_web', '0025_tags_arrivals_shipper_voyage')]
        new_target = [('harbor_web', '0026_cargo_color')]
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            UserModel = old_apps.get_model('auth', 'User')
            OldCargo = old_apps.get_model('harbor_web', 'Cargo')
            actor = UserModel.objects.create(username='mig-actor')
            OldCargo.objects.create(
                created_by=actor,
                code='MIG-1',
                name='Legacy Cargo',
                weight_kg=Decimal('2.000'),
                volume_m3=Decimal('0.0120'),
                destination='부산',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            NewCargo = new_apps.get_model('harbor_web', 'Cargo')
            row = NewCargo.objects.get(code='MIG-1')
            self.assertEqual(row.color, '')
            self.assertIs(row.color_confirmed, False)

            executor = MigrationExecutor(connection)
            executor.migrate(old_target)
            reverted_apps = executor.loader.project_state(old_target).apps
            RevertedCargo = reverted_apps.get_model('harbor_web', 'Cargo')
            self.assertTrue(RevertedCargo.objects.filter(code='MIG-1').exists())
            self.assertFalse(hasattr(RevertedCargo, 'color'))
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)

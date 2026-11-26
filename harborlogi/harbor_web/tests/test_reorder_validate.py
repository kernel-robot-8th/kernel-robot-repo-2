from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone

from harbor.site_recognition import placement_to_site_ref
from harbor_web import services
from harbor_web.models import Audit, Cargo, CargoFootprint, CellPlanDispatch, Command, Observation, Order, Plan
from harbor_web.order_queue import reorder_schedule

ROOT = Path(__file__).resolve().parents[2]


class ReorderValidateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin')
        self.operator = User.objects.create_user('operator')
        self.viewer = User.objects.create_user('viewer')
        self.field = User.objects.create_user('field')
        for user, roles in (
            (self.admin, ('admin', 'viewer')),
            (self.operator, ('operator', 'viewer')),
            (self.viewer, ('viewer',)),
            (self.field, ('field',)),
        ):
            user.groups.add(*[Group.objects.get_or_create(name=name)[0] for name in roles])

        policy = json.loads((ROOT / 'config/spatial-policy.example.json').read_text(encoding='utf-8'))
        policy['yard_layout'] = json.loads((ROOT / 'config/yard-layout.json').read_text(encoding='utf-8'))
        self.condition = services.register_condition(self.admin, name='1×3 rows', calibration_ref='synthetic', payload=policy)
        placements = []
        for code, row, level in (('C1', 1, 1), ('C2', 1, 2), ('C3', 2, 1)):
            services.register_cargo(self.operator, code=code, name=code, weight_kg=2, volume_m3='0.012', destination='반출대', length_cells=3)
            footprint = CargoFootprint.objects.filter(cargo__code=code).latest('pk')
            placements.append(dict(cargo_id=code, area_id='yard', row=row, column=1, level=level, orientation=0, footprint_revision=footprint.pk))
        self.obs = services.record_observation(
            self.admin,
            source_id='row-camera',
            source_revision='1',
            observed_at=timezone.now(),
            provenance='SYNTHETIC',
            condition=self.condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', *(area['area_id'] for area in policy['spatial_areas'])],
                placements=placements,
                holdings=[],
            ),
        )
        self.c1 = services.create_order(self.operator, cargo=Cargo.objects.get(code='C1'), kind='OUTBOUND', priority_group=2, sequence=1, tie_policy='FIXED')
        self.c2 = services.create_order(self.operator, cargo=Cargo.objects.get(code='C2'), kind='OUTBOUND', priority_group=2, sequence=2, tie_policy='FIXED')
        self.client.force_login(self.operator)

    def test_validate_route_permissions_and_read_only(self):
        self.assertEqual(resolve('/outbound/validate/').url_name, 'outbound_validate')
        payload = {'lane': ['FIXED|', f'FIXED|{self.c2.pk}:1,{self.c1.pk}:1', 'FIXED|']}
        before = (Order.objects.count(), Plan.objects.count(), Command.objects.count(), Audit.objects.count())
        with patch('harbor.rosy_http.RosyClient._http', side_effect=AssertionError('validate contacted Fleet')):
            resp = self.client.post('/outbound/validate/', payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual((data['rehandles'], data['moves']), (0, 2))
        self.assertEqual((data['current']['rehandles'], data['current']['moves']), (1, 4))
        self.assertNotIn('endTime', data)
        self.assertEqual(before, (Order.objects.count(), Plan.objects.count(), Command.objects.count(), Audit.objects.count()))
        self.assertEqual((Order.objects.get(pk=self.c1.pk).version, Order.objects.get(pk=self.c2.pk).version), (1, 1))

        self.assertEqual(self.client.get('/outbound/validate/').status_code, 405)
        for user in (self.viewer, self.admin, self.field):
            self.client.force_login(user)
            self.assertEqual(self.client.post('/outbound/validate/', payload).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post('/outbound/validate/', payload).status_code, 302)

    def test_validate_rejects_malformed_stale_and_running_moved_back(self):
        self.assertEqual(self.client.post('/outbound/validate/', {'lane': ['FIXED|x:1', 'FIXED|', 'FIXED|']}).status_code, 400)
        self.assertEqual(self.client.post('/outbound/validate/', {'lane': ['FIXED|', f'FIXED|{self.c2.pk}:9,{self.c1.pk}:1', 'FIXED|']}).status_code, 409)

        plan = services.make_plan(self.operator, order_id=self.c1.pk, expected_version=1, observation_id=self.obs.pk)
        Command.objects.create(order=self.c1, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='test', fleet_origin='test', fleet_principal='test',
            body_digest='a' * 64, binding={}, state='RECONCILED')
        resp = self.client.post('/outbound/validate/', {'lane': ['FIXED|', f'FIXED|{self.c2.pk}:1,{self.c1.pk}:1', 'FIXED|']})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['ok'])
        self.assertIn('실행 중인 반출 작업보다 앞에 둘 수 없습니다.', resp.json()['reasons'])

    def test_work_hub_renders_unified_schedule_for_operator_only_and_keeps_query_count(self):
        import re
        from harbor_web.work_hub import hub_context
        with self.assertNumQueries(6):
            hub_context()
        services.make_plan(self.operator, order_id=self.c1.pk, expected_version=1, observation_id=self.obs.pk)
        with self.assertNumQueries(7):
            hub_context()
        html = self.client.get('/work/').content.decode()
        # Only one unified schedule section ("오늘 시간표"), no duplicate "입고·반출 순서" or old .work-timeline list
        self.assertEqual(html.count('id="work-today"'), 1)
        self.assertNotIn('id="work-outbound"', html)
        self.assertNotIn('입고·반출 순서', html)
        self.assertNotIn('work-timeline', html)
        self.assertNotIn('wk-reorder', html)
        self.assertIsNone(re.search(r'\sstyle=', html))
        for text in ('data-reorder-editor', 'data-validate-url="/outbound/validate/"',
                     'data-reorder-url="/outbound/reorder/"', 'reorder.js',
                     'data-wf-tier="1"', 'data-wf-tier="2"', 'data-wf-tier="3"',
                     '<span class="work-tag">다음</span>'):
            self.assertIn(text, html)
        self.assertNotIn('맨 앞으로', html)
        today_section = html.split('id="work-today"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('<span class="scope">', today_section)

        # Non-operator sees read-only board (no editing attributes/controls/reorder.js)
        for user in (self.viewer, self.admin):
            self.client.force_login(user)
            ro_html = self.client.get('/work/').content.decode()
            self.assertNotIn('data-reorder-editor', ro_html)
            self.assertNotIn('data-validate-url', ro_html)
            self.assertNotIn('data-reorder-url', ro_html)
            self.assertNotIn('data-reorder-form', ro_html)
            self.assertNotIn('wk-out-actions', ro_html)
            self.assertNotIn('reorder.js', ro_html)
            self.assertIn('운영자만 바꿀 수 있음', ro_html)
            self.assertIn('data-schedule-viewer', ro_html)
            self.assertIn('schedule-popover.js', ro_html)
            self.assertIsNone(re.search(r'\sstyle=', ro_html))

    def test_work_hub_shows_done_running_next_and_attention_states_with_popover_metadata(self):
        from datetime import timedelta
        now = timezone.now()
        c3_cargo = Cargo.objects.get(code='C3')
        c3_done = services.create_order(
            self.operator, cargo=c3_cargo, kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED',
        )
        done_plan = services.make_plan(self.operator, order_id=c3_done.pk, expected_version=1, observation_id=self.obs.pk)
        done_cmd = Command.objects.create(
            order=c3_done, plan=done_plan, actor=self.operator, plan_version=1,
            ledger_identity='done-c3', fleet_origin='test', fleet_principal='test',
            body_digest='a' * 64, binding={}, state='COMPLETED',
        )
        Audit.objects.create(actor=self.operator, action='command.completed', object_type='Command', object_id=str(done_cmd.pk))

        # Running work on c1
        c1_plan = services.make_plan(self.operator, order_id=self.c1.pk, expected_version=1, observation_id=self.obs.pk)
        Command.objects.create(
            order=self.c1, plan=c1_plan, actor=self.operator, plan_version=1,
            ledger_identity='run-c1', fleet_origin='test', fleet_principal='test',
            body_digest='b' * 64, binding={}, state='RECONCILED',
        )
        # Metadata on a new next waiting work (Cargo is immutable, so register with shipper_name)
        meta_cargo = services.register_cargo(
            self.operator, code='META1', name='META1', weight_kg=2, volume_m3='0.012',
            destination='출하장', length_cells=3, shipper_name='한빛해운',
        )
        services.create_order(
            self.operator, cargo=meta_cargo, kind='INBOUND', priority_group=1, sequence=2, tie_policy='FIXED',
            voyage_code='V-901', cutoff_at=now + timedelta(hours=2),
        )

        # Blocked inbound (HOLD plan -> 작업 막힘)
        blocked_cargo = services.register_cargo(
            self.operator, code='BLK1', name='BLK1', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        blocked_order = services.create_order(
            self.operator, cargo=blocked_cargo, kind='INBOUND', priority_group=3, sequence=1, tie_policy='FIXED',
        )
        Plan.objects.create(
            created_by=self.operator, order=blocked_order, order_version=1,
            observation=self.obs, condition=self.condition, queue_digest='q', result={'status': 'HOLD'},
        )

        html = self.client.get('/work/').content.decode()
        # Done band appears before priority tiers, dimmed and non-movable (no data-order)
        done_pos = html.index('data-wf-done')
        tier1_pos = html.index('data-wf-tier="1"')
        self.assertLess(done_pos, tier1_pos)
        done_band = html[done_pos:tier1_pos]
        self.assertIn('C3', done_band)
        self.assertIn('data-done="1"', done_band)
        self.assertNotIn('data-order=', done_band)
        self.assertIn('<time class="wf-time"', done_band)

        # Running ("지금"), next ("다음"), and attention ("작업 막힘") labels + popover metadata
        self.assertIn('<span class="work-tag">지금</span>', html)
        self.assertIn('<span class="work-tag">다음</span>', html)
        self.assertIn('작업 막힘', html)
        self.assertIn('data-shipper="한빛해운"', html)
        self.assertIn('data-voyage="V-901"', html)
        self.assertIn('data-href=', html)

    def test_mixed_inbound_and_outbound_validation_and_never_creates_plan(self):
        in_cargo = services.register_cargo(
            self.operator, code='IN1', name='IN1', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        in_order = services.create_order(
            self.operator, cargo=in_cargo, kind='INBOUND', priority_group=1, sequence=1, tie_policy='FIXED',
        )
        before_plans = Plan.objects.count()
        payload = {
            'scope': 'schedule',
            'lane': [
                f'FIXED|{in_order.pk}:1',
                f'FIXED|{self.c2.pk}:1,{self.c1.pk}:1',
                'FIXED|',
            ],
        }
        resp = self.client.post('/outbound/validate/', payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual((data['rehandles'], data['moves']), (0, 2))
        self.assertEqual(data['items'][0]['kind'], 'INBOUND')
        self.assertEqual(data['items'][0]['code'], 'IN1')
        self.assertTrue(data['items'][0]['place'])
        self.assertEqual(Plan.objects.count(), before_plans)

        # Save mixed schedule across tiers via /outbound/reorder/ (moving C2 into tier 1 ahead of IN1)
        save_resp = self.client.post('/outbound/reorder/', {
            'scope': 'schedule',
            'lane': [
                f'FIXED|{self.c2.pk}:1,{in_order.pk}:1',
                f'FIXED|{self.c1.pk}:1',
                'FIXED|',
            ],
        })
        self.assertEqual(save_resp.status_code, 302)
        self.c2.refresh_from_db()
        in_order.refresh_from_db()
        self.c1.refresh_from_db()
        self.assertEqual((self.c2.priority_group, self.c2.sequence), (1, 1))
        self.assertEqual((in_order.priority_group, in_order.sequence), (1, 2))
        self.assertEqual((self.c1.priority_group, self.c1.sequence), (2, 1))

    def test_changing_priority_tier_reallocates_colliding_sequence_bugfix(self):
        # Reproduce the known bug: tier 1 already has sequence=1, and moving a tier-2 order (sequence=1)
        # into tier 1 via PriorityForm (even if the hidden sequence=1 is resent) must reallocate sequence cleanly.
        t1_existing = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='C3'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        self.assertEqual((t1_existing.priority_group, t1_existing.sequence), (1, 1))
        self.assertEqual((self.c1.priority_group, self.c1.sequence), (2, 1))

        # 1) HTML form must not prefill hidden sequence with old tier's sequence
        detail_html = self.client.get(f'/requests/{self.c1.pk}/').content.decode()
        self.assertNotIn('name="sequence" value="1"', detail_html)

        # 2) POST changing priority_group 2 -> 1 WITH the old hidden sequence=1 still succeeds by reallocating
        resp = self.client.post(f'/requests/{self.c1.pk}/', dict(
            action='priority',
            expected_version=self.c1.version,
            priority_group=1,
            sequence=1,
            tie_policy='FIXED',
            note='긴급으로 상향',
        ))
        self.assertEqual(resp.status_code, 302)
        self.c1.refresh_from_db()
        self.assertEqual((self.c1.priority_group, self.c1.sequence, self.c1.version), (1, 2, 2))

        # 3) Direct update_order changing tier with colliding sequence also reallocates cleanly
        moved = services.update_order(
            self.operator, order_id=self.c2.pk, expected_version=self.c2.version,
            priority_group=1, sequence=1, tie_policy='FIXED', note='긴급으로 이동',
        )
        self.assertEqual((moved.priority_group, moved.sequence, moved.version), (1, 3, 2))

    def test_outbound_before_inbound_and_lifo_temp_limit_rejected(self):
        in_cargo = services.register_cargo(
            self.operator, code='IN2', name='IN2', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        in_order = services.create_order(
            self.operator, cargo=in_cargo, kind='INBOUND', priority_group=3, sequence=1, tie_policy='FIXED',
        )
        out_in2 = services.create_order(
            self.operator, cargo=in_cargo, kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED',
        )
        before_plans = Plan.objects.count()
        resp = self.client.post('/outbound/validate/', {
            'scope': 'schedule',
            'lane': [
                f'FIXED|{out_in2.pk}:1',
                f'FIXED|{self.c2.pk}:1,{self.c1.pk}:1',
                f'FIXED|{in_order.pk}:1',
            ],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertTrue(any('입고 전에 반출할 수 없습니다' in r for r in data['reasons']))
        self.assertEqual(Plan.objects.count(), before_plans)

    def test_validate_matches_make_plan_when_auto_inbound_reservations_change_outcome(self):
        # Case 1: Earlier auto_replan inbound reserves row 3 L1; a later-pk inbound ahead of it in the schedule
        # must skip row 3 L1 and match services.make_plan's exact recommendation.
        in_auto_cargo = services.register_cargo(
            self.operator, code='IN-AUTO', name='IN-AUTO', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        in_auto = services.create_order(
            self.operator, cargo=in_auto_cargo, kind='INBOUND', priority_group=3, sequence=1, tie_policy='FIXED',
        )
        now = timezone.now()
        auto_plan = services.make_plan(
            self.operator, order_id=in_auto.pk, expected_version=1, observation_id=self.obs.pk, now=now, automatic=True,
        )
        self.assertEqual(auto_plan.result['status'], 'READY')
        self.assertEqual((auto_plan.result['recommendation']['row'], auto_plan.result['recommendation']['level']), (3, 1))

        in_next_cargo = services.register_cargo(
            self.operator, code='IN-NEXT', name='IN-NEXT', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        in_next = services.create_order(
            self.operator, cargo=in_next_cargo, kind='INBOUND', priority_group=1, sequence=1, tie_policy='FIXED',
        )

        # Put IN-NEXT first (ahead of IN-AUTO) in the schedule: reservation from IN-AUTO redirects IN-NEXT off (3,1,1).
        payload = {
            'scope': 'schedule',
            'lane': [
                f'FIXED|{in_next.pk}:1',
                f'FIXED|{self.c2.pk}:1,{self.c1.pk}:1',
                f'FIXED|{in_auto.pk}:1',
            ],
        }
        with patch('django.utils.timezone.now', return_value=now):
            val_resp = self.client.post('/outbound/validate/', payload)
        self.assertEqual(val_resp.status_code, 200)
        val_data = val_resp.json()
        self.assertTrue(val_data['ok'], val_data)

        actual_next_plan = services.make_plan(
            self.operator, order_id=in_next.pk, expected_version=1, observation_id=self.obs.pk, now=now,
        )
        self.assertEqual(actual_next_plan.result['status'], 'READY')
        self.assertNotEqual(actual_next_plan.result['recommendation'], auto_plan.result['recommendation'])
        rec = actual_next_plan.result['recommendation']
        expected_label = placement_to_site_ref(rec['area_id'], rec['row'], rec['column'], rec['level'])
        self.assertEqual(val_data['items'][0]['code'], 'IN-NEXT')
        self.assertEqual(val_data['items'][0]['place'], expected_label)

        # Case 2: External auto_replan reservation outside the validated schedule fills the only ground slot
        # on a 1x3x2 yard -> both validate and make_plan return HOLD NO_LEGAL_PLACEMENT (never stack on reservation).
        tight_policy = deepcopy(self.condition.payload)
        tight_policy['yard_layout'] = {
            'rows': 1, 'columns': 3, 'levels': 2,
            'cells': [{'bay_id': f'Y1{c}', 'row': 1, 'column': c} for c in (1, 2, 3)],
        }
        tight_policy['arm_reach'] = [
            [arm, place]
            for arm in ('A', 'B')
            for place in ('Y11', 'Y12', 'Y13', 'temp_a', 'temp_b', 'stand')
        ]
        tight_cond = services.register_condition(
            self.admin, name='Tight 1x3x2', calibration_ref='synthetic', payload=tight_policy,
        )
        tight_obs = services.record_observation(
            self.admin,
            source_id='tight-camera',
            source_revision='1',
            observed_at=now,
            provenance='SYNTHETIC',
            condition=tight_cond,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', *(area['area_id'] for area in tight_policy['spatial_areas'])],
                placements=[],
                holdings=[],
            ),
        )
        ext_cargo = services.register_cargo(
            self.operator, code='IN-EXT', name='IN-EXT', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        ext_order = services.create_order(
            self.operator, cargo=ext_cargo, kind='INBOUND', priority_group=1, sequence=2, tie_policy='FIXED',
        )
        ext_plan = services.make_plan(
            self.operator, order_id=ext_order.pk, expected_version=1, observation_id=tight_obs.pk, now=now, automatic=True,
        )
        self.assertEqual(ext_plan.result['status'], 'READY')
        # Close ext_order (so it is outside open_schedule_orders) while tight_obs has not yet observed IN-EXT.
        Command.objects.create(
            order=ext_order, plan=ext_plan, actor=self.operator, plan_version=1,
            ledger_identity='done-ext', fleet_origin='test', fleet_principal='test',
            body_digest='d' * 64, binding={}, state='COMPLETED',
        )
        # Close earlier orders so only a single open inbound IN-BLOCKED remains in open_schedule_orders.
        for order in (self.c1, self.c2, in_auto, in_next):
            p = Plan.objects.filter(order=order).order_by('-pk').first() or Plan.objects.create(
                created_by=self.operator, order=order, order_version=order.version,
                observation=tight_obs, condition=tight_cond, queue_digest='closed', result={'status': 'READY'},
            )
            Command.objects.create(
                order=order, plan=p, actor=self.operator, plan_version=1,
                ledger_identity=f'done-{order.pk}', fleet_origin='test', fleet_principal='test',
                body_digest='e' * 64, binding={}, state='COMPLETED',
            )
        blocked_cargo = services.register_cargo(
            self.operator, code='IN-BLOCKED', name='IN-BLOCKED', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        blocked_order = services.create_order(
            self.operator, cargo=blocked_cargo, kind='INBOUND', priority_group=1, sequence=3, tie_policy='FIXED',
        )
        with patch('django.utils.timezone.now', return_value=now):
            tight_val = self.client.post(f'/outbound/validate/?observation={tight_obs.pk}', {
                'scope': 'schedule',
                'lane': [f'FIXED|{blocked_order.pk}:1', 'FIXED|', 'FIXED|'],
            })
        self.assertEqual(tight_val.status_code, 200)
        tight_data = tight_val.json()
        actual_blocked_plan = services.make_plan(
            self.operator, order_id=blocked_order.pk, expected_version=1, observation_id=tight_obs.pk, now=now,
        )
        self.assertEqual((actual_blocked_plan.result['status'], actual_blocked_plan.result['reason']), ('HOLD', 'NO_LEGAL_PLACEMENT'))
        self.assertFalse(tight_data['ok'])
        self.assertEqual(tight_data['items'][0]['reason_code'], actual_blocked_plan.result['reason'])

    def test_outbound_reorder_redirect_matches_use_schedule(self):
        # 1) Outbound-only reorder without scope redirects to /outbound/ (even when no inbound orders exist).
        out_resp = self.client.post('/outbound/reorder/', {
            'lane': ['FIXED|', f'FIXED|{self.c2.pk}:1,{self.c1.pk}:1', 'FIXED|'],
        })
        self.assertEqual(out_resp.status_code, 302)
        self.assertEqual(out_resp['Location'], '/outbound/')

        # 2) Mixed schedule (inbound included) WITHOUT scope='schedule' saves via reorder_schedule and redirects to /work/.
        in_cargo = services.register_cargo(
            self.operator, code='IN-RED', name='IN-RED', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        in_order = services.create_order(
            self.operator, cargo=in_cargo, kind='INBOUND', priority_group=3, sequence=1, tie_policy='FIXED',
        )
        self.c1.refresh_from_db()
        self.c2.refresh_from_db()
        mixed_resp = self.client.post('/outbound/reorder/', {
            'lane': [
                f'FIXED|{in_order.pk}:1',
                f'FIXED|{self.c2.pk}:{self.c2.version},{self.c1.pk}:{self.c1.version}',
                'FIXED|',
            ],
        })
        self.assertEqual(mixed_resp.status_code, 302)
        self.assertEqual(mixed_resp['Location'], '/work/')
        in_order.refresh_from_db()
        self.assertEqual((in_order.priority_group, in_order.sequence), (1, 1))

    def test_reorder_save_rejects_placing_work_ahead_of_running_command_or_cell_plan(self):
        c3_order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='C3'), kind='OUTBOUND',
            priority_group=2, sequence=3, tie_policy='FIXED',
        )
        in_cargo = services.register_cargo(
            self.operator, code='IN-RUN', name='IN-RUN', weight_kg=2, volume_m3='0.012', destination='야드', length_cells=3,
        )
        in_order = services.create_order(
            self.operator, cargo=in_cargo, kind='INBOUND', priority_group=3, sequence=1, tie_policy='FIXED',
        )

        # Running Command on c1: moving c2 or in_order ahead of c1 must fail in both service and view layers.
        plan = services.make_plan(self.operator, order_id=self.c1.pk, expected_version=1, observation_id=self.obs.pk)
        cmd = Command.objects.create(
            order=self.c1, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='running-cmd', fleet_origin='test', fleet_principal='test',
            body_digest='f' * 64, binding={}, state='RECONCILED',
        )
        with self.assertRaises(ValidationError):
            services.reorder_outbound(self.operator, lanes=[
                dict(fixed=True, orders=[]),
                dict(fixed=True, orders=[(self.c2.pk, 1), (self.c1.pk, 1), (c3_order.pk, 1)]),
                dict(fixed=True, orders=[]),
            ])
        with self.assertRaises(ValidationError):
            reorder_schedule(self.operator, lanes=[
                dict(fixed=True, orders=[(in_order.pk, 1)]),
                dict(fixed=True, orders=[(self.c1.pk, 1), (self.c2.pk, 1), (c3_order.pk, 1)]),
                dict(fixed=True, orders=[]),
            ])
        resp = self.client.post('/outbound/reorder/', {
            'scope': 'schedule',
            'lane': [
                f'FIXED|{in_order.pk}:1',
                f'FIXED|{self.c1.pk}:1,{self.c2.pk}:1,{c3_order.pk}:1',
                'FIXED|',
            ],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual((Order.objects.get(pk=self.c1.pk).version, Order.objects.get(pk=in_order.pk).version), (1, 1))

        # Keeping running c1 first while reordering orders behind it succeeds.
        ok_resp = self.client.post('/outbound/reorder/', {
            'scope': 'schedule',
            'lane': [
                'FIXED|',
                f'FIXED|{self.c1.pk}:1,{c3_order.pk}:1,{self.c2.pk}:1',
                f'FIXED|{in_order.pk}:1',
            ],
        })
        self.assertEqual(ok_resp.status_code, 302)
        self.assertEqual(ok_resp['Location'], '/work/')

        # Mark c1 completed, then attach a running CellPlanDispatch to c3_order and verify save rejects moving ahead of it.
        Command.objects.filter(pk=cmd.pk).update(state='COMPLETED')
        c3_order.refresh_from_db()
        self.c2.refresh_from_db()
        in_order.refresh_from_db()
        CellPlanDispatch.objects.create(
            order=c3_order, observation=self.obs, dispatch_mode='HARBOR_PLAN',
            directive_mode='OUTBOUND', request_key='cp-running-1', order_source='harbor',
            state='RUNNING', created_by=self.operator,
        )
        with self.assertRaises(ValidationError):
            reorder_schedule(self.operator, lanes=[
                dict(fixed=True, orders=[(self.c2.pk, self.c2.version)]),
                dict(fixed=True, orders=[(c3_order.pk, c3_order.version)]),
                dict(fixed=True, orders=[(in_order.pk, in_order.version)]),
            ])
        cp_resp = self.client.post('/outbound/reorder/', {
            'scope': 'schedule',
            'lane': [
                f'FIXED|{self.c2.pk}:{self.c2.version}',
                f'FIXED|{c3_order.pk}:{c3_order.version}',
                f'FIXED|{in_order.pk}:{in_order.version}',
            ],
        })
        self.assertEqual(cp_resp.status_code, 409)


class SeedScheduleValidateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('seed-admin')
        self.operator = User.objects.create_user('seed-operator')
        for user, roles in ((self.admin, ('admin', 'viewer')), (self.operator, ('operator', 'viewer'))):
            user.groups.add(*[Group.objects.get_or_create(name=name)[0] for name in roles])
        call_command('seed_site_dataset', provenance='DECLARED', verbosity=0)
        self.client.force_login(self.operator)

    def test_mixed_schedule_infeasible_when_yard_fills_up_before_outbounds(self):
        # In seed_site_dataset: 3x6x3 yard = 18 places, 12 occupied (C-1..C-4, C-9..C-16), 6 empty.
        # Putting all 10 inbounds (C-5..C-8, C-17..C-22) ahead of all 12 outbounds fills the 6 empty places and blocks on the 7th inbound.
        inbounds = list(Order.objects.filter(kind='INBOUND').select_related('cargo').order_by('priority_group', 'sequence', 'pk'))
        outbounds = list(Order.objects.filter(kind='OUTBOUND').select_related('cargo').order_by('priority_group', 'sequence', 'pk'))
        self.assertEqual((len(inbounds), len(outbounds)), (10, 12))

        before_plans = Plan.objects.count()
        lane1 = ','.join(f'{o.pk}:{o.version}' for o in inbounds)
        lane2 = ','.join(f'{o.pk}:{o.version}' for o in outbounds)
        resp = self.client.post('/outbound/validate/', {
            'scope': 'schedule',
            'lane': [f'FIXED|{lane1}', f'FIXED|{lane2}', 'FIXED|'],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertTrue(any('보관 위치가 없습니다' in r for r in data['reasons']))
        self.assertEqual(Plan.objects.count(), before_plans)

    def test_seed_dataset_validate_matches_actual_make_plan_step_by_step(self):
        """Verify that validate's rehandles, moves, and inbound placements match running make_plan step-by-step."""
        obs = Observation.objects.select_related('condition').order_by('-pk').first()
        # Use a fresh subset of seed orders (close the rest) so we can verify both outbound + inbound step-by-step.
        chosen_codes = [
            ('OUTBOUND', 'C-1'),   # buried under C-2 and C-9 -> requires rehandles
            ('INBOUND', 'C-5'),    # placed into yard considering remaining outbound schedule
            ('OUTBOUND', 'C-4'),   # top of (1,4) -> 0 rehandles
            ('OUTBOUND', 'C-3'),   # now top of (1,4) -> 0 rehandles
            ('INBOUND', 'C-6'),    # placed into yard
        ]
        chosen_orders = [Order.objects.get(kind=kind, cargo__code=code) for kind, code in chosen_codes]
        chosen_ids = {o.pk for o in chosen_orders}

        # Mark all other seed orders completed so open_schedule_orders() is exactly our 5-step mixed schedule.
        for other in Order.objects.exclude(pk__in=chosen_ids):
            dummy_plan = Plan.objects.create(
                created_by=self.operator, order=other, order_version=other.version,
                observation=obs, condition=obs.condition, queue_digest='seed-closed',
                result={'status': 'READY'},
            )
            Command.objects.create(
                order=other, plan=dummy_plan, actor=self.operator, plan_version=1,
                ledger_identity=f'done-{other.pk}', fleet_origin='test', fleet_principal='test',
                body_digest='b' * 64, binding={}, state='COMPLETED',
            )

        lanes = [
            f"FIXED|{','.join(f'{o.pk}:{o.version}' for o in chosen_orders[:3])}",
            f"FIXED|{','.join(f'{o.pk}:{o.version}' for o in chosen_orders[3:])}",
            'FIXED|',
        ]
        now = timezone.now()
        before_plans = Plan.objects.count()
        with patch('django.utils.timezone.now', return_value=now):
            val_resp = self.client.post('/outbound/validate/', {'scope': 'schedule', 'lane': lanes})
        self.assertEqual(val_resp.status_code, 200)
        val_data = val_resp.json()
        self.assertTrue(val_data['ok'], val_data)
        self.assertEqual(Plan.objects.count(), before_plans)  # validate never creates Plan rows

        # Apply the exact same order, then run real services.make_plan step-by-step advancing the yard snapshot.
        apply_resp = self.client.post('/outbound/reorder/', {'scope': 'schedule', 'lane': lanes})
        self.assertEqual(apply_resp.status_code, 302)

        fp_by_code = {
            fp.cargo.code: fp.pk
            for fp in CargoFootprint.objects.select_related('cargo').order_by('pk')
        }
        actual_total_moves = 0
        actual_total_rehandles = 0
        current_obs = obs
        for idx, order in enumerate(chosen_orders):
            order.refresh_from_db()
            plan = services.make_plan(
                self.operator, order_id=order.pk, expected_version=order.version,
                observation_id=current_obs.pk, now=now,
            )
            self.assertEqual(plan.result['status'], 'READY', (order.cargo.code, plan.result))
            moves = plan.result.get('moves', [])
            if order.kind == 'OUTBOUND':
                rehandles = next(i for i, m in enumerate(moves) if m['cargo_id'] == order.cargo.code)
                step_moves = len(moves)
            else:
                rehandles = 0
                step_moves = len(moves)
            self.assertEqual(val_data['items'][idx]['moves'], step_moves)
            self.assertEqual(val_data['items'][idx]['rehandles'], rehandles)
            actual_total_moves += step_moves
            actual_total_rehandles += rehandles

            # Advance yard snapshot identically to execution completion (outbound target leaves yard; inbound stays placed)
            projected = [
                dict(p, footprint_revision=fp_by_code[p['cargo_id']])
                for p in plan.result['projected_placements']
                if not (order.kind == 'OUTBOUND' and p['cargo_id'] == order.cargo.code)
            ]
            next_payload = dict(current_obs.payload, placements=projected)
            current_obs = services.record_observation(
                self.admin,
                source_id=current_obs.source_id,
                source_revision=f'step-{idx + 1}',
                observed_at=now,
                provenance='SYNTHETIC',
                condition=current_obs.condition,
                payload=next_payload,
            )
            Command.objects.create(
                order=order, plan=plan, actor=self.operator, plan_version=1,
                ledger_identity=f'exec-{order.pk}', fleet_origin='test', fleet_principal='test',
                body_digest='c' * 64, binding={}, state='COMPLETED',
            )

        self.assertEqual(val_data['moves'], actual_total_moves)
        self.assertEqual(val_data['rehandles'], actual_total_rehandles)
        self.assertGreater(actual_total_rehandles, 0)  # confirms C-1 rehandles were genuinely exercised

    def test_work_hub_seed_dataset_sequential_destinations_zero_plans_and_bounded_queries(self):
        import re
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from harbor_web.work_hub import SIMULATION_LIMIT, hub_context

        self.assertEqual(Plan.objects.count(), 0)
        with self.assertNumQueries(8):
            hub_ctx = hub_context()
        self.assertEqual(Plan.objects.count(), 0)

        with CaptureQueriesContext(connection) as queries:
            resp = self.client.get('/work/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Plan.objects.count(), 0)
        self.assertLessEqual(len(queries), 25)

        schedule = resp.context['schedule']
        self.assertEqual(len(schedule), 22)  # seed C-1..C-22
        by_code = {row['order'].cargo.code: row for row in schedule}

        # Sequential ROSY wire coordinates for the simulated inbound items
        self.assertEqual(by_code['C-5']['card_place'], 'yard-r2-c4-l2')
        self.assertEqual(by_code['C-8']['card_place'], 'yard-r3-c4-l2')
        self.assertEqual(by_code['C-18']['card_place'], 'yard-r2-c4-l3')
        self.assertEqual(len({by_code['C-5']['card_place'], by_code['C-8']['card_place'], by_code['C-18']['card_place']}), 3)
        self.assertEqual(by_code['C-6']['card_place'], 'yard-r1-c4-l2')
        self.assertEqual(by_code['C-17']['card_place'], 'yard-r1-c4-l2')

        # Outbound cards inside the first 8 carry sequential move/rehandle summaries
        self.assertIn('이동 5회', by_code['C-1']['plan_summary'])
        self.assertIn('재취급 2회', by_code['C-1']['plan_summary'])
        self.assertIn('이동 3회', by_code['C-3']['plan_summary'])
        self.assertIn('재취급 1회', by_code['C-3']['plan_summary'])
        self.assertIn('이동 3회', by_code['C-4']['plan_summary'])
        self.assertIn('재취급 1회', by_code['C-4']['plan_summary'])

        # All 22 items in the queue are simulated with no '계산 전' or '—' placeholders.
        for row in schedule:
            self.assertEqual(row['sim_status'], 'READY')
            self.assertTrue(row['card_place'])
            self.assertNotEqual(row['card_place'], '계산 전')
            self.assertTrue(row['plan_summary'])
            self.assertNotEqual(row['plan_summary'], '계산 전')

        html = resp.content.decode()
        self.assertIn('yard-r2-c4-l2', html)
        self.assertIn('yard-r3-c4-l2', html)
        self.assertIn('yard-r2-c4-l3', html)
        self.assertNotIn('입고 자리', html)
        self.assertNotIn('id="work-inbound"', html)
        self.assertIsNone(re.search(r'\d+행\s+\d+열\s+\d+층', html))
        self.assertNotIn('계산 전', html)
        self.assertNotIn('예상', html)
        self.assertNotIn('<span class="work-state">대기</span>', html)
        body = html.split('<main', 1)[1]
        self.assertNotIn('READY', body)
        self.assertNotIn('HOLD', body)
        self.assertIsNone(re.search(r'\sstyle=', html))
        self.assertEqual(len(hub_ctx['schedule']), 22)  # seed C-1..C-22

    def test_work_hub_shows_short_hold_reason_and_uncomputed_after_hold(self):
        # Put all 8 inbounds ahead of all 12 outbounds so the 6 empty yard places fill up
        # and the 7th inbound hits HOLD (NO_LEGAL_PLACEMENT -> '빈 자리 없음') without creating Plans.
        inbounds = list(Order.objects.filter(kind='INBOUND').select_related('cargo').order_by('priority_group', 'sequence', 'pk'))
        outbounds = list(Order.objects.filter(kind='OUTBOUND').select_related('cargo').order_by('priority_group', 'sequence', 'pk'))
        reorder_schedule(self.operator, lanes=[
            dict(fixed=True, orders=[(o.pk, o.version) for o in inbounds]),
            dict(fixed=True, orders=[(o.pk, o.version) for o in outbounds]),
            dict(fixed=True, orders=[]),
        ])
        self.assertEqual(Plan.objects.count(), 0)
        resp = self.client.get('/work/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Plan.objects.count(), 0)
        schedule = resp.context['schedule']
        for row in schedule[:6]:
            self.assertEqual(row['sim_status'], 'READY')
            self.assertTrue(row['card_place'])
        seventh = schedule[6]
        self.assertEqual(seventh['sim_status'], 'HOLD')
        self.assertEqual(seventh['card_place'], '빈 자리 없음')
        self.assertEqual(seventh['card_place_kind'], 'blocked')
        self.assertIn('보관 위치가 없습니다', seventh['plan_summary'])
        # 8th item after HOLD was not reached -> empty (never '계산 전')
        self.assertEqual(schedule[7]['sim_status'], 'UNCOMPUTED')
        self.assertEqual(schedule[7]['card_place'], '')
        self.assertEqual(schedule[7]['plan_summary'], '')
        html = resp.content.decode()
        self.assertIn('빈 자리 없음', html)
        self.assertIn('wf-place-blocked', html)
        self.assertNotIn('계산 전', html)
        body = html.split('<main', 1)[1]
        self.assertNotIn('HOLD', body)

    def test_work_hub_reflects_auto_replan_reservations_on_get(self):
        # Reserve C-5's default slot (2행 2열 2층) via an earlier automatic inbound plan,
        # then verify GET /work/ reflects the reservation without creating any new Plan rows.
        obs = Observation.objects.select_related('condition').order_by('-pk').first()
        c5 = Order.objects.get(kind='INBOUND', cargo__code='C-5')
        c8 = Order.objects.get(kind='INBOUND', cargo__code='C-8')
        now = timezone.now()
        auto_plan = services.make_plan(
            self.operator, order_id=c5.pk, expected_version=c5.version,
            observation_id=obs.pk, now=now, automatic=True,
        )
        self.assertEqual(auto_plan.result['status'], 'READY')
        # Move C-8 ahead of C-5 so C-8 (higher pk) is simulated before C-5 while C-5's auto_replan reservation exists.
        other_t1 = [o for o in Order.objects.filter(priority_group=1).exclude(pk__in=(c5.pk, c8.pk)).order_by('sequence', 'pk')]
        t2 = list(Order.objects.filter(priority_group=2).order_by('sequence', 'pk'))
        t3 = list(Order.objects.filter(priority_group=3).order_by('sequence', 'pk'))
        t1_new = [c8, c5, *other_t1]
        reorder_schedule(self.operator, lanes=[
            dict(fixed=True, orders=[(o.pk, o.version) for o in t1_new]),
            dict(fixed=True, orders=[(o.pk, o.version) for o in t2]),
            dict(fixed=True, orders=[(o.pk, o.version) for o in t3]),
        ])
        plans_before = Plan.objects.count()
        with patch('django.utils.timezone.now', return_value=now):
            resp = self.client.get('/work/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Plan.objects.count(), plans_before)
        by_code = {row['order'].cargo.code: row for row in resp.context['schedule']}
        rec = auto_plan.result['recommendation']
        reserved_label = placement_to_site_ref(rec['area_id'], rec['row'], rec['column'], rec['level'])
        self.assertEqual(reserved_label, 'yard-r2-c4-l2')
        # C-8 must skip C-5's reserved slot 'yard-r2-c4-l2'
        self.assertNotEqual(by_code['C-8']['card_place'], reserved_label)
        self.assertEqual(by_code['C-8']['card_place'], 'yard-r3-c4-l2')

    def test_schedule_and_yard_upcoming_show_eta_from_moves_and_cutoff_warning(self):
        from datetime import datetime, timedelta, timezone as dt_timezone
        import re
        from harbor_web.site_config import DEFAULT_MOVE_SECONDS_ESTIMATE, MOVE_SECONDS_ESTIMATE_DESCRIPTION
        from harbor_web.work_hub import SIMULATION_LIMIT

        self.assertEqual(DEFAULT_MOVE_SECONDS_ESTIMATE, 60)
        self.assertIn('실측 전 기본값', MOVE_SECONDS_ESTIMATE_DESCRIPTION)

        obs = Observation.objects.select_related('condition').order_by('-pk').first()
        base_now = obs.observed_at + timedelta(minutes=2)
        Order.objects.filter(pk=Order.objects.order_by('priority_group', 'sequence', 'pk').first().pk).update(
            cutoff_at=base_now + timedelta(hours=3),
        )
        # Set a tight cutoff on the 2nd task so cumulative moves push its ETA past cutoff_at.
        ordered = list(Order.objects.filter(kind__in=('OUTBOUND', 'INBOUND')).order_by('priority_group', 'sequence', 'pk'))
        first_order, second_order, third_order = ordered[0], ordered[1], ordered[2]
        Order.objects.filter(pk=second_order.pk).update(cutoff_at=base_now + timedelta(seconds=30))
        Order.objects.filter(pk=third_order.pk).update(cutoff_at=base_now + timedelta(hours=4))

        before_counts = (Order.objects.count(), Plan.objects.count(), Command.objects.count(), Audit.objects.count())
        with timezone.override('Asia/Seoul'), patch('django.utils.timezone.now', return_value=base_now):
            board_resp = self.client.get('/')
            work_resp = self.client.get('/work/')
            yard_resp = self.client.get('/inventory/')

        self.assertEqual((board_resp.status_code, work_resp.status_code, yard_resp.status_code), (200, 200, 200))
        self.assertEqual(
            (Order.objects.count(), Plan.objects.count(), Command.objects.count(), Audit.objects.count()),
            before_counts,
        )

        schedule = work_resp.context['schedule']
        self.assertEqual(len(schedule), 22)
        expected_first_hm = timezone.localtime(base_now).strftime('%H:%M')
        self.assertEqual(schedule[0]['eta_text'], expected_first_hm)
        self.assertFalse(schedule[0]['eta_late'])
        # 2nd task starts after 1st task's moves * 60s -> strictly later than +30s cutoff -> late warning.
        self.assertGreater(schedule[1]['eta_at'], base_now)
        self.assertTrue(schedule[1]['eta_late'])
        self.assertFalse(schedule[2]['eta_late'])
        # Every item across the entire 22-item queue gets an HH:MM ETA (no '—' or '계산 전').
        for row in schedule:
            self.assertRegex(row['eta_text'], r'^\d\d:\d\d$')

        work_html = work_resp.content.decode()
        work_flow_html = work_html.split('id="wk-flow"', 1)[1].split('<details class="panel work-auto">', 1)[0]
        self.assertIn(f'>{expected_first_hm}<', work_flow_html)
        self.assertNotIn('>—<', work_flow_html)
        self.assertNotIn('계산 전', work_flow_html)
        self.assertNotIn('예상', work_flow_html)
        self.assertNotIn('<span class="work-state">대기</span>', work_flow_html)
        self.assertIn('wf-eta-late', work_html)
        self.assertIn('data-eta-late="1"', work_html)
        self.assertIsNone(re.search(r'\sstyle=', work_html))
        popover_js = (ROOT / 'harbor_web/static/harbor_web/schedule-popover.js').read_text(encoding='utf-8')
        self.assertNotIn('예상', popover_js)

        # Yard upcoming list matches ETA formula & cutoff warning class across all 22 items, without '예상' or '—'.
        upcoming = yard_resp.context['upcoming']
        self.assertEqual(upcoming[0]['eta_text'], expected_first_hm)
        self.assertTrue(upcoming[1]['eta_late'])
        for item in upcoming:
            self.assertRegex(item['eta_text'], r'^\d\d:\d\d$')
        self.assertEqual(
            [(item['eta_at'], item['eta_text'], item['eta_late']) for item in upcoming],
            [(row['eta_at'], row['eta_text'], row['eta_late']) for row in schedule],
        )
        yard_html = yard_resp.content.decode()
        yard_upcoming_html = yard_html.split('class="yard-upcoming-list"', 1)[1].split('</ul>', 1)[0]
        self.assertNotIn('예상', yard_upcoming_html)
        self.assertNotIn('계산 전', yard_upcoming_html)
        self.assertNotIn('>—<', yard_upcoming_html)
        self.assertIn('yard-upcoming-eta is-late', yard_html)
        self.assertIsNone(re.search(r'\sstyle=', yard_html))

        # Situation board ('/') matches schedule & yard order, ETA values, and cutoff warning without '예상'.
        board = board_resp.context['board']
        board_jobs = [board['current_job'], *board['next_jobs']]
        self.assertEqual(len(board_jobs), 6)
        self.assertEqual(
            [job['title'] for job in board_jobs],
            [f"{row['order'].cargo.code} {row['kind']}" for row in schedule[:6]],
        )
        self.assertEqual(
            [(job['eta_at'], job['eta_text'], job['eta_late']) for job in board_jobs],
            [(row['eta_at'], row['eta_text'], row['eta_late']) for row in schedule[:6]],
        )
        self.assertEqual(
            [(job['eta_at'], job['eta_text'], job['eta_late']) for job in board_jobs],
            [(item['eta_at'], item['eta_text'], item['eta_late']) for item in upcoming[:6]],
        )
        board_html = board_resp.content.decode()
        board_list_html = board_html.split('class="board-job-list"', 1)[1].split('</ol>', 1)[0]
        self.assertIn(f'>{expected_first_hm}<', board_list_html)
        self.assertIn('board-job-eta is-late', board_list_html)
        self.assertNotIn('예상', board_list_html)
        self.assertIsNone(re.search(r'\sstyle=', board_html))

        # Custom MOVE_SECONDS_ESTIMATE setting scales ETA proportionally.
        with self.settings(MOVE_SECONDS_ESTIMATE=180), \
                timezone.override('Asia/Seoul'), \
                patch('django.utils.timezone.now', return_value=base_now):
            custom_resp = self.client.get('/work/')
        custom_sched = custom_resp.context['schedule']
        delta_60 = (schedule[1]['eta_at'] - base_now).total_seconds()
        delta_180 = (custom_sched[1]['eta_at'] - base_now).total_seconds()
        self.assertEqual(delta_180, delta_60 * 3)

        # Running command start time (created_at) overrides wall-clock now as ETA base on both /work/ and /.
        running_started = base_now - timedelta(minutes=10)
        plan = services.make_plan(
            self.operator, order_id=first_order.pk, expected_version=first_order.version,
            observation_id=obs.pk, now=base_now,
        )
        with patch('django.utils.timezone.now', return_value=running_started):
            Command.objects.create(
                order=first_order, plan=plan, actor=self.operator, plan_version=1,
                ledger_identity='eta-running-1', fleet_origin='test', fleet_principal='test',
                body_digest='a' * 64, binding={}, state='RECONCILED',
            )
        with timezone.override('Asia/Seoul'), patch('django.utils.timezone.now', return_value=base_now):
            running_resp = self.client.get('/work/')
            running_board_resp = self.client.get('/')
        run_sched = running_resp.context['schedule']
        expected_run_hm = timezone.localtime(running_started).strftime('%H:%M')
        self.assertEqual(run_sched[0]['eta_at'], running_started)
        self.assertEqual(run_sched[0]['eta_text'], expected_run_hm)
        run_board = running_board_resp.context['board']
        self.assertEqual(run_board['current_job']['eta_at'], running_started)
        self.assertEqual(run_board['current_job']['eta_text'], expected_run_hm)
        self.assertEqual(
            [(job['eta_at'], job['eta_text'], job['eta_late']) for job in [run_board['current_job'], *run_board['next_jobs']]],
            [(row['eta_at'], row['eta_text'], row['eta_late']) for row in run_sched[:6]],
        )

    def test_move_seconds_estimate_setting_and_env_parsing(self):
        import os
        import subprocess
        import sys
        from django.conf import settings as django_settings
        from django.core.exceptions import ImproperlyConfigured
        from harbor_web.settings import parse_move_seconds_estimate
        from harbor_web.site_config import move_seconds_estimate

        self.assertEqual(django_settings.MOVE_SECONDS_ESTIMATE, 60)
        self.assertIsInstance(django_settings.MOVE_SECONDS_ESTIMATE, int)
        self.assertEqual(move_seconds_estimate(), 60.0)

        for raw, expected in ((None, 60), ('', 60), ('   ', 60), ('1', 1), ('60', 60), ('120', 120)):
            with self.subTest(raw=raw):
                self.assertEqual(parse_move_seconds_estimate(raw), expected)

        for bad in ('0', '-1', '-60', '1.5', 'abc', True):
            with self.subTest(bad=bad), self.assertRaises(ImproperlyConfigured):
                parse_move_seconds_estimate(bad)

        with self.settings(MOVE_SECONDS_ESTIMATE=95):
            self.assertEqual(move_seconds_estimate(), 95.0)
            self.assertEqual(move_seconds_estimate({'move_seconds_estimate': 45}), 45.0)

        base_env = {
            k: v for k, v in os.environ.items()
            if k != 'HARBOR_MOVE_SECONDS_ESTIMATE'
        }
        base_env.update(
            HARBOR_WEB_MODE='test',
            HARBOR_WEB_SECRET_KEY='k' * 64,
            DJANGO_SETTINGS_MODULE='harbor_web.settings',
        )

        # Unset -> 60, valid integer >= 1 -> configured integer used by site_config.
        for env_val, expected in ((None, '60 60.0'), ('90', '90 90.0'), ('1', '1 1.0')):
            env = dict(base_env)
            if env_val is not None:
                env['HARBOR_MOVE_SECONDS_ESTIMATE'] = env_val
            proc = subprocess.run(
                [
                    sys.executable, '-c',
                    'import django; django.setup(); from django.conf import settings as s; '
                    'from harbor_web.site_config import move_seconds_estimate; '
                    'print(s.MOVE_SECONDS_ESTIMATE, move_seconds_estimate())',
                ],
                env=env, capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), expected)

        # Invalid env values (< 1 or non-integer) -> ImproperlyConfigured.
        for bad_env in ('0', '-5', '1.5', 'not-a-number'):
            env = dict(base_env, HARBOR_MOVE_SECONDS_ESTIMATE=bad_env)
            proc = subprocess.run(
                [sys.executable, '-c', 'import harbor_web.settings'],
                env=env, capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertNotEqual(proc.returncode, 0, f'expected failure for {bad_env!r}')
            self.assertIn('ImproperlyConfigured', proc.stderr)
